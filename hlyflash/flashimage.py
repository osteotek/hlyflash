"""Validate physical BR25/JLFS flash images before any erase operation.

A checksum-valid UFW container need not contain a directly writable image.
In particular, staged app_dir_head2 packages require vendor preprocessing.
"""
from __future__ import annotations

import struct

from .enc import crc16, enc_bytes
from .scsi import SCSIError


def _record(raw):
    if len(raw) != 32 or crc16(raw[2:]) != int.from_bytes(raw[:2], "little"):
        raise SCSIError("Invalid JLFS file-table CRC")
    _, checksum, offset, size, attr, reserved, index, name = struct.unpack("<HHIIBBH16s", raw)
    return {"name": name.split(b"\0", 1)[0].decode("ascii", "replace"),
            "offset": offset, "size": size, "attr": attr, "crc": checksum,
            "index": index}


def is_staged(image):
    """Recognize a validated staging-table marker without needing a device key."""
    for address in range(32, min(len(image), 4096), 32):
        try:
            entry = _record(enc_bytes(image[address:address + 32], 0xFFFF))
        except SCSIError:
            return False
        if entry["name"] == "app_dir_head2":
            return True
        if entry["index"]:
            break
    return False


def validate(image, chip_key, capacity):
    if chip_key is None:
        raise SCSIError("Cannot validate this image without the device chip key")
    if len(image) < 4096 or len(image) > capacity:
        raise SCSIError("Physical flash image length is outside device capacity")
    header = enc_bytes(image[:32], 0xFFFF)
    if crc16(header[2:]) != int.from_bytes(header[:2], "little"):
        raise SCSIError("Physical flash header CRC is invalid")
    if header[12] != 3 or int.from_bytes(header[8:12], "little") != capacity:
        raise SCSIError("Unsupported flash layout or flash-capacity mismatch")
    boot = []
    for address in range(32, min(4096, len(image)), 32):
        entry = _record(enc_bytes(image[address:address + 32], 0xFFFF))
        boot.append(entry)
        if entry["index"]:
            break
    names = [e["name"] for e in boot]
    if "app_dir_head2" in names:
        raise SCSIError("UFW flash.bin uses a staged app_dir_head2 layout. Direct programming "
                        "is unsafe; package conversion is not implemented. No flash was erased.")
    if len(set(names)) != len(names) or names.count("app_dir_head") != 1:
        raise SCSIError("Unsupported or ambiguous JLFS boot file table")
    app_base = next(e["offset"] for e in boot if e["name"] == "app_dir_head")
    if app_base < 32 * (len(boot) + 1) or app_base % 4096 or app_base >= len(image):
        raise SCSIError("Invalid physical application base")
    if "uboot.boot" not in names:
        raise SCSIError("Image has no bootloader")
    occupied = []
    for entry in boot:
        if entry["name"] in ("app_dir_head", "key_mac"):
            continue
        address, size = entry["offset"], entry["size"]
        if not size or address < 32 * (len(boot) + 1) or address + size > app_base:
            raise SCSIError("Boot file overlaps the table or application area")
        if any(address < end and address + size > start for start, end in occupied):
            raise SCSIError("Overlapping boot files")
        occupied.append((address, address + size))
        data = image[address:address + size]
        if entry["name"] == "uboot.boot":
            if len(data) < 16:
                raise SCSIError("Bootloader is truncated")
            data = enc_bytes(data[:16], 0xFFFF) + enc_bytes(data[16:], 0xFFFF)
            if crc16(data[:14]) != int.from_bytes(data[14:16], "little"):
                raise SCSIError("Bootloader BANKCB header CRC mismatch")
            count, length, _, offset, checksum, _ = struct.unpack("<HHIIHH", data[:16])
            if count != 1 or offset + length > len(data) or crc16(data[offset:offset + length]) != checksum:
                raise SCSIError("Unsupported or damaged BANKCB bootloader")
        if crc16(data) != entry["crc"]:
            raise SCSIError(f"Boot file CRC mismatch for {entry['name']!r}")
    plain = bytearray()
    for offset in range(0, len(image) - app_base, 32):
        plain.extend(enc_bytes(image[app_base + offset:app_base + offset + 32],
                               chip_key ^ (offset >> 2)))
    app = []
    for address in range(0, min(len(plain), 512), 32):
        entry = _record(plain[address:address + 32])
        app.append(entry)
        # The first record is the parent directory. Its terminator does not
        # terminate the child file/partition list (Free 2 sets this to one).
        if address and entry["index"]:
            break
    if len(app) < 2 or app[0]["name"] != "app_area_head" or not app[-1]["index"]:
        raise SCSIError("Unsupported or incomplete application file table")
    checked = []
    regions = []
    file_ranges = []
    for entry in app:
        address, size = entry["offset"], entry["size"]
        if entry["attr"] & 0x10:
            if not size or address + size > capacity:
                raise SCSIError(f"Partition {entry['name']!r} exceeds flash capacity")
            regions.append(entry)
        elif entry["attr"] & 0x0F == 2:
            if not size or address < len(app) * 32 or address + size > len(plain):
                raise SCSIError(f"Application file {entry['name']!r} is truncated")
            if any(address < end and address + size > start for start, end in file_ranges):
                raise SCSIError("Overlapping application files")
            file_ranges.append((address, address + size))
            if crc16(plain[address:address + size]) != entry["crc"]:
                raise SCSIError(f"Application CRC mismatch for {entry['name']!r}; "
                                "check the image and device chip key")
            checked.append(entry["name"])
    if "app.bin" not in checked:
        raise SCSIError("Image has no verified app.bin")
    for region in regions:
        if region["name"] == "PRCT":
            continue
        if any(app_base + start < region["offset"] + region["size"] and
               app_base + end > region["offset"] for start, end in file_ranges):
            raise SCSIError("Application file overlaps a data partition")
    return {"app_base": app_base, "files": checked, "regions": regions,
            "product": image[16:32].rstrip(b"\xff\0").decode("ascii", "replace")}
