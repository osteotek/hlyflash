"""Constrained application-only conversion of single-payload BR25 UFW staging.

The application code and configuration payloads remain byte-for-byte unchanged.
The existing boot sector and special-area descriptors are retained. This is an
experimental layout adaptation, not a reproduction of the vendor updater.
"""
from __future__ import annotations

import struct

from . import flashimage
from .enc import crc16, enc_bytes
from .scsi import SCSIError


def _boot(image):
    header = enc_bytes(image[:32], 0xFFFF)
    if len(header) != 32 or crc16(header[2:]) != int.from_bytes(header[:2], "little"):
        raise SCSIError("Invalid source flash header")
    entries = {}
    for address in range(32, min(len(image), 4096), 32):
        entry = flashimage._record(enc_bytes(image[address:address + 32], 0xFFFF))
        if entry["name"] in entries:
            raise SCSIError("Duplicate boot entry")
        entries[entry["name"]] = entry
        if entry["index"]:
            return header, entries
    raise SCSIError("Unterminated boot table")


def _plain(image, base, key):
    return bytearray(b"".join(enc_bytes(image[a:a + 32], key ^ ((a - base) >> 2))
                             for a in range(base, len(image), 32)))


def _app(plain):
    entries = []
    for address in range(0, min(len(plain), 512), 32):
        entry = flashimage._record(plain[address:address + 32])
        entries.append((entry, bytes(plain[address:address + 32])))
        if address and entry["index"]:
            break
    names = [e["name"] for e, _ in entries]
    if len(entries) < 2 or names[0] != "app_area_head" or not entries[-1][0]["index"]:
        raise SCSIError("Invalid application table")
    if len(set(names)) != len(names):
        raise SCSIError("Duplicate application entry")
    head = entries[0][0]
    if not 32 * len(entries) <= head["size"] <= len(plain):
        raise SCSIError("Application area length is invalid")
    if crc16(plain[32:head["size"]]) != head["crc"]:
        raise SCSIError("Whole application area CRC mismatch")
    return entries


def _compatible_config(old, new):
    if old == new:
        return True
    # UPDATE_JUMP selects the vendor updater's entry method / IO retention.
    # Keep the installed value and boot sector; no SPI/clock/reset changes are
    # permitted. Reference: Jieli KDocs "升级相关", DEV_UPDATE_SUPPORT_JUMP.
    marker = b"\x04UPDATE_JUMP\x00"
    if old.count(marker) != 1 or new.count(marker) != 1:
        return False
    offset = old.index(marker) + len(marker)
    if new.index(marker) + len(marker) != offset or offset + 4 > len(old):
        return False
    if any(data[offset:offset + 4] not in (b"\x00" * 4, b"\x01\x00\x00\x00") for data in (old, new)):
        return False
    return old == new[:offset] + old[offset:offset + 4] + new[offset + 4:]


def transplant(staged, original, key):
    """Return a full physical image; reject any unsupported layout or migration."""
    capacity = len(original)
    installed = flashimage.validate(original, key, capacity)
    if installed["product"] != "AC636N" or installed["app_base"] != 4096:
        raise SCSIError("Application-only conversion currently requires BR25 / AC636N at 0x1000")
    old_header, old_boot = _boot(original)
    new_header, new_boot = _boot(staged)
    required = {"uboot.boot", "isd_config.ini", "app_dir_head", "key_mac"}
    if set(old_boot) != required or set(new_boot) != required | {"app_dir_head2"}:
        raise SCSIError("Unsupported staging boot table")
    if old_header[2:15] != new_header[2:15] or original[16:32] != staged[16:32]:
        raise SCSIError("Source flash hardware configuration differs")
    app = new_boot["app_dir_head"]
    second = new_boot["app_dir_head2"]
    slot = second["size"]
    if (app["offset"] != 0x1020 or app["size"] != 4096 or len(staged) % 4096 or
            slot % 4096 or not len(staged) <= slot <= capacity // 2 or
            second["offset"] != slot + 32):
        raise SCSIError("Unsupported staging offsets or additional application bank")
    for name in ("uboot.boot", "isd_config.ini"):
        old, new = old_boot[name], new_boot[name]
        old_data = original[old["offset"]:old["offset"] + old["size"]]
        new_data = staged[new["offset"]:new["offset"] + new["size"]]
        if (new["offset"] < 32 * (len(new_boot) + 1) or
                new["offset"] + new["size"] > 4096 or
                any(new[k] != old[k] for k in ("size", "attr")) or
                (name == "uboot.boot" and (new["crc"] != old["crc"] or new_data != old_data)) or
                (name == "isd_config.ini" and (crc16(new_data) != new["crc"] or
                                                not _compatible_config(old_data, new_data)))):
            raise SCSIError(f"Application-only update requires identical {name} "
                            "(only UPDATE_JUMP may differ; the installed value is retained)")
    old_plain = _plain(original, 4096, key)
    new_plain = _plain(staged, app["offset"], key)
    old_entries, new_entries = _app(old_plain), _app(new_plain)
    old_names = [e["name"] for e, _ in old_entries]
    supported = ["app_area_head", "app.bin", "cfg_tool.bin", "VM", "PRCT", "BTIF"]
    if old_names not in (supported, supported + ["EXIF"]):
        raise SCSIError("Unsupported installed application/partition layout")
    if [e["name"] for e, _ in new_entries] != [
            "app_area_head", "app.bin", "cfg_tool.bin", "VM", "PRCT", "BTIF"]:
        raise SCSIError("Unsupported package application/partition layout")
    if old_entries[0][0]["offset"] != new_entries[0][0]["offset"]:
        raise SCSIError("Application entry point changed")
    old_cfg, new_cfg = old_entries[2][0], new_entries[2][0]
    if (old_cfg["size"] != new_cfg["size"] or
            old_plain[old_cfg["offset"]:old_cfg["offset"] + old_cfg["size"]] !=
            new_plain[new_cfg["offset"]:new_cfg["offset"] + new_cfg["size"]]):
        raise SCSIError("Application-only update requires identical cfg_tool.bin")
    # Validate the source files in an otherwise conventional physical envelope.
    envelope = bytearray(original[:4096] + b"\xff" * (capacity - 4096))
    if 4096 + len(new_plain) > capacity:
        raise SCSIError("Staged application exceeds capacity")
    envelope[4096:4096 + len(new_plain)] = staged[app["offset"]:]
    flashimage.validate(envelope, key, capacity)
    app_size = new_entries[0][0]["size"]
    length = (app_size + 31) // 32 * 32
    if length > len(new_plain) or 32 * len(old_entries) > new_entries[1][0]["offset"]:
        raise SCSIError("No room for retained partition descriptors")
    for region in installed["regions"]:
        if region["name"] != "PRCT" and 4096 + length > region["offset"]:
            raise SCSIError("New application would overlap preserved data")
    if app_size > old_entries[0][0]["size"]:
        raise SCSIError("Application-only conversion does not support growing the application")
    if any(e["offset"] + e["size"] > app_size for e, _ in new_entries[1:3]):
        raise SCSIError("Application files exceed the declared application area")
    # Preserve every original special-area descriptor, including the terminator.
    for i, (_, raw) in enumerate(old_entries[3:], 3):
        new_plain[i * 32:(i + 1) * 32] = raw
    # Retain the installed parent-directory terminator independently of its children.
    new_plain[14:16] = old_plain[14:16]
    struct.pack_into("<H", new_plain, 2, crc16(new_plain[32:app_size]))
    struct.pack_into("<H", new_plain, 0, crc16(new_plain[2:32]))
    result = bytearray(original)
    for offset in range(0, length, 32):
        result[4096 + offset:4096 + offset + 32] = enc_bytes(
            new_plain[offset:offset + 32], key ^ (offset >> 2))
    validated = flashimage.validate(result, key, capacity)
    _app(_plain(result, 4096, key))
    if validated["regions"] != installed["regions"]:
        raise SCSIError("Converted image changed the partition map")
    if result[:4096] != original[:4096] or result[4096 + length:] != original[4096 + length:]:
        raise SCSIError("Converted image changed bytes outside the application")
    return bytes(result)
