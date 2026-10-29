"""Verified backup and experimental programming through a JieLi flash loader.

Every write requires a freshly saved, twice-read full backup. RAM loaders are
checked against known profiles; the tested BR25 loader is bundled.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from importlib.resources import files
import json
import os
from pathlib import Path
import sys
import time

from .loader import Loader, is_uboot_product
from .enc import crc_decode
from .scsi import CommandFailed, SCSIError, open_device
from . import ufw as ufwmod
from . import flashimage

SECTOR = 4096
PROFILES = {
    "BR25": {
        "chip": "AC636N", "address": 0x12000,
        "resource": "data/br25loader.bin",
        "rom_crc_plain": True, "rom_stale_residue": True,
        "sha256": "0bc69023d6bf4c4d5d5bc048e06249ee39c484acd11196ffc73f88940787d77d",
    },
    "BD19": {
        "chip": "AC632N", "address": 0x2000,
        "sha256": "faa1b519a3ff1b082f11ccafac8c73d31150161ac972ef9f1934002bcbb8df4f",
    },
}


def _bootstrap(ldr, path, profile):
    if path is not None:
        image = Path(path).read_bytes()
    elif profile.get("resource"):
        try:
            image = files("hlyflash").joinpath(profile["resource"]).read_bytes()
        except OSError as e:
            raise SCSIError("Bundled RAM loader is missing; reinstall hlyflash or supply --loader") from e
    else:
        raise SCSIError("The ROM needs a RAM flash loader. Supply --loader with "
                        "the known chip-specific binary (see README).")
    if hashlib.sha256(image).hexdigest() != profile["sha256"]:
        raise SCSIError("RAM loader hash does not match this chip family; refusing to upload.")
    # Reference blobs are already MengLi-scrambled in 512-byte blocks.
    print(f"Uploading verified RAM loader at {profile['address']:#x} ({len(image)} bytes)",
          file=sys.stderr, flush=True)
    for offset in range(0, len(image), 512):
        block = image[offset:offset + 512]
        address = profile["address"] + offset
        try:
            crc_data = crc_decode(block) if profile.get("rom_crc_plain") else block
            ldr.mem_write(address, block, crc_data=crc_data)
            if ldr.mem_read(address, len(block)) != block:
                raise SCSIError("RAM readback does not match uploaded bytes")
        except SCSIError as e:
            raise SCSIError(f"RAM upload at {address:#x}, {len(block)} bytes: {e}") from e
    ldr.mem_jump(profile["address"], 1)  # SPI NOR
    time.sleep(0.2)
    print("Verified RAM loader started", file=sys.stderr, flush=True)


def _enter_boot(ldr, vendor):
    try:
        return ldr.online_device()
    except SCSIError as initial:
        # Follow USB re-enumeration only on the same physical port.
        from .usb import USBDev
        if isinstance(ldr.dev, USBDev) and ldr.dev.location[1]:
            location = ldr.dev.location
            captured = ldr.dev.detached
            try:
                ldr.dev.close(restore=False)
            except SCSIError:
                pass
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                time.sleep(0.2)
                try:
                    ldr.dev = USBDev("usb", location=location)
                except SCSIError:
                    continue
                ldr.dev.detached = ldr.dev.detached or captured
                new_vendor, product, _ = ldr.inquiry()
                if new_vendor != vendor:
                    raise SCSIError("Device identity changed during boot transition")
                if is_uboot_product(product):
                    return None
                ldr.dev.close(restore=False)
        raise SCSIError("Device did not enter download mode after FC0A. "
                        "Reconnect it in UBOOT mode and retry.") from initial


def flash_capacity(jedec_id):
    density = jedec_id & 0xFF
    if not 0x10000 <= jedec_id <= 0xFFFFFF or not 16 <= density <= 25:
        raise SCSIError(f"Unsupported flash JEDEC ID {jedec_id:#x}; capacity is unknown")
    return 1 << density


def open_loader(node, loader_path=None, enter_boot=False, expected_location=None):
    dev = open_device(node, location=expected_location) if expected_location is not None else open_device(node)
    ldr = Loader(dev)
    try:
        if expected_location is not None and getattr(ldr.dev, "location", None) != expected_location:
            raise SCSIError("USB device does not match the selected serial device; refusing boot transition")
        vendor, product, revision = ldr.inquiry()
        print(f"Device: {vendor} {product} {revision}", file=sys.stderr, flush=True)
        if vendor not in PROFILES:
            raise SCSIError(f"Unsupported JieLi chip family {vendor!r}")
        profile = PROFILES[vendor]
        online = None
        if not is_uboot_product(product):
            if product not in ("UDISK", "DEVICE") or not enter_boot:
                raise SCSIError(f"Device reports {product!r}. Use --enter-boot to request "
                                "download mode, or reconnect in UBOOT mode.")
            online = _enter_boot(ldr, vendor)
            new_vendor, product, revision = ldr.inquiry()
            print(f"After FC0A: {new_vendor} {product} {revision}", file=sys.stderr, flush=True)
            if new_vendor != vendor:
                raise SCSIError("Device identity changed during boot transition")
        if online is None:
            if product == "UBOOT1.00" and profile.get("rom_stale_residue"):
                if hasattr(ldr.dev, "allow_stale_residue"):
                    ldr.dev.allow_stale_residue = True
            if product == "UBOOT1.00":
                # ROM identity already establishes that a flash loader is
                # required. Do not send unsupported flash-capability queries
                # before the verified upload sequence.
                _bootstrap(ldr, loader_path, profile)
            online = ldr.online_device()
        if online["type"] not in (3, 0x16):
            raise SCSIError(f"Expected SPI NOR flash, got device type {online['type']:#x}")
        ldr.family = vendor
        ldr.product = product
        ldr.flash_id = online["id"]
        ldr.flash_size = flash_capacity(ldr.flash_id)
        try:
            ldr.chip_key_value = ldr.chip_key()  # also unlocks relevant ROM loaders
        except CommandFailed:
            ldr.chip_key_value = None  # some app-mode handlers don't implement FC09
        try:
            io_size = ldr.usb_buffer_size()
        except CommandFailed:
            io_size = 256
        if not 1 <= io_size <= 0x10000:
            raise SCSIError(f"Invalid loader transfer buffer size: {io_size}")
        ldr.io_size = min(io_size, SECTOR)
        return ldr
    except BaseException:
        try:
            ldr.dev.close()
        except SCSIError:
            pass
        raise


def _read(ldr, address, length):
    return b"".join(ldr.flash_read(off, min(ldr.io_size, address + length - off))
                    for off in range(address, address + length, ldr.io_size))


def _progress(verb, done, total, enabled):
    if callable(enabled):
        enabled(verb, done, total)
    elif enabled:
        print(f"\r  {verb} {done:#09x}/{total:#09x} ({done * 100 // total:3d}%)",
              end="", flush=True)


def _backup(ldr, out_path, length, progress):
    path = Path(out_path)
    if path.exists() or path.is_symlink():
        raise SCSIError(f"Backup already exists; choose a new path: {path}")
    partial = path.with_name(path.name + ".partial")
    digest = hashlib.sha256()
    with partial.open("xb") as output:
        for offset in range(0, length, SECTOR):
            block = _read(ldr, offset, min(SECTOR, length - offset))
            output.write(block)
            digest.update(block)
            _progress("read", offset + len(block), length, progress)
        output.flush()
        os.fsync(output.fileno())
    with partial.open("rb") as saved:
        for offset in range(0, length, SECTOR):
            count = min(SECTOR, length - offset)
            if _read(ldr, offset, count) != saved.read(count):
                raise SCSIError(f"Backup second-read mismatch at {offset:#x}; "
                                f"unverified data retained at {partial}")
            _progress("verify backup", offset + count, length, progress)
    # Atomically publish without overwriting an existing path.
    os.link(partial, path)
    partial.unlink()
    if progress:
        print()
    return digest.hexdigest()


def dump(node, out_path, length=None, progress=True, loader_path=None, enter_boot=False):
    ldr = open_loader(node, loader_path, enter_boot)
    try:
        length = ldr.flash_size if length is None else length
        if not 0 < length <= ldr.flash_size:
            raise SCSIError(f"Backup length must be within detected capacity {ldr.flash_size:#x}")
        digest = _backup(ldr, out_path, length, progress)
        return {"family": ldr.family, "flash_id": hex(ldr.flash_id), "bytes": length,
                "full": length == ldr.flash_size, "sha256": digest}
    finally:
        ldr.dev.close()


def _program(ldr, image, backup, progress, dry_run=False, before_write=None):
    if not image or len(image) > len(backup) or len(backup) != ldr.flash_size:
        raise SCSIError("Programming requires a nonempty image within a complete flash backup")
    expected = bytearray(backup)
    expected[:len(image)] = image
    sectors = [offset for offset in range(0, len(image), SECTOR)
               if expected[offset:offset + SECTOR] != backup[offset:offset + SECTOR]]
    if dry_run:
        return len(sectors)
    # Write high addresses first and the boot sector last; preserve partial tails.
    for count, offset in enumerate(reversed(sectors), 1):
        block = bytes(expected[offset:offset + SECTOR])
        if count == 1 and before_write is not None:
            before_write()
        ldr.flash_erase_sector(offset)
        for pos in range(0, len(block), ldr.io_size):
            ldr.flash_write(offset + pos, block[pos:pos + ldr.io_size])
        if _read(ldr, offset, len(block)) != block:
            raise SCSIError(f"Byte-for-byte readback failed at {offset:#x}")
        _progress("write", count * SECTOR, len(sectors) * SECTOR, progress)
    for offset in range(0, len(expected), SECTOR):
        count = min(SECTOR, len(expected) - offset)
        if _read(ldr, offset, count) != expected[offset:offset + count]:
            raise SCSIError(f"Final full-flash verification failed at {offset:#x}")
        _progress("verify flash", offset + count, len(expected), progress)
    if progress:
        print()
    return len(sectors)


def validate_package(u, application_only=None):
    """Validate the downloadable container before opening any USB device."""
    if not (u.header_crc_ok and u.table_crc_ok):
        raise SCSIError("UFW failed its checksums; refusing to flash")
    img = u.entry("flash.bin")
    if img is None or not img.size or not img.crc_ok(u.raw):
        raise SCSIError("UFW has no nonempty, checksum-valid flash.bin")
    staged = flashimage.is_staged(img.payload(u.raw))
    if application_only is None:
        application_only = staged
    if staged and not application_only:
        raise SCSIError("Staged app_dir_head2 packages need conversion; direct programming "
                        "is unsafe and unsupported. No device was opened.")
    if any(not e.crc_ok(u.raw) for e in u.entries
           if e.name in {"flash.bin", "ota.bin", "tail.bin", "info.log"}):
        raise SCSIError("UFW payload checksum mismatch")
    if application_only and (u.chip != "AC636N" or not staged):
        raise SCSIError("Application-only conversion requires a staged BR25 / AC636N package")
    return img, application_only


@dataclass
class FlashPlan:
    backup_path: Path
    backup: bytes
    target: bytes
    manifest: dict

    @property
    def changed_sectors(self):
        expected = self.target + self.backup[len(self.target):]
        return sum(expected[a:a + SECTOR] != self.backup[a:a + SECTOR]
                   for a in range(0, len(expected), SECTOR))

    def program(self, ldr, progress=True, before_write=None):
        # The confirmation prompt may have taken time. Ensure recovery data is
        # still present on disk immediately before the first erase.
        saved = self.backup_path.read_bytes()
        if hashlib.sha256(saved).hexdigest() != self.manifest["backup_sha256"]:
            raise SCSIError("Verified backup changed before programming; refusing to write")
        return _program(ldr, self.target, self.backup, progress, before_write=before_write)


def read_current(ldr, progress=True):
    """Read into memory for comparison; recovery backups still require two reads."""
    blocks = []
    for offset in range(0, ldr.flash_size, SECTOR):
        blocks.append(_read(ldr, offset, min(SECTOR, ldr.flash_size - offset)))
        _progress("check", offset + len(blocks[-1]), ldr.flash_size, progress)
    return b"".join(blocks)


def build_target(ldr, u, current, application_only=True):
    """Apply the same compatibility guards for checks and writes."""
    img, application_only = validate_package(u, application_only)
    if u.chip != PROFILES[ldr.family]["chip"]:
        raise SCSIError(f"Firmware chip {u.chip!r} does not match {ldr.family}")
    if img.size > ldr.flash_size or len(current) != ldr.flash_size:
        raise SCSIError("Firmware or current image exceeds detected flash capacity")
    target = img.payload(u.raw)
    if application_only:
        from .application import transplant
        target = transplant(target, current, ldr.chip_key_value)
    else:
        flashimage.validate(target, ldr.chip_key_value, ldr.flash_size)
    return target


def prepare_flash(ldr, u, backup_path, progress=True, application_only=None, dry_run=False):
    """Save a verified backup and exact target; do not erase or program flash."""
    img, application_only = validate_package(u, application_only)
    if u.chip != PROFILES[ldr.family]["chip"]:
        raise SCSIError(f"Firmware chip {u.chip!r} does not match {ldr.family}")
    if img.size > ldr.flash_size:
        raise SCSIError("Firmware image exceeds detected flash capacity")
    if not application_only:
        flashimage.validate(img.payload(u.raw), ldr.chip_key_value, ldr.flash_size)
    digest = _backup(ldr, backup_path, ldr.flash_size, progress)
    backup = Path(backup_path).read_bytes()
    if len(backup) != ldr.flash_size or hashlib.sha256(backup).hexdigest() != digest:
        raise SCSIError("Backup changed after verification; refusing to write")
    target = build_target(ldr, u, backup, application_only)
    if application_only:
        with Path(str(backup_path) + ".target.bin").open("xb") as output:
            output.write(target)
            output.flush()
            os.fsync(output.fileno())
        print("Application-only conversion validated: boot sector and data partitions retained",
              file=sys.stderr, flush=True)
    manifest = {"family": ldr.family, "flash_id": hex(ldr.flash_id),
                "bytes": len(backup), "backup_sha256": digest,
                "firmware_chip": u.chip, "firmware_sha256": hashlib.sha256(u.raw).hexdigest(),
                "application_only": application_only, "dry_run": dry_run,
                "target_sha256": hashlib.sha256(target).hexdigest()}
    with Path(str(backup_path) + ".json").open("x") as output:
        json.dump(manifest, output, indent=2)
        output.flush()
        os.fsync(output.fileno())
    return FlashPlan(Path(backup_path), backup, target, manifest)


def flash_ufw(node, ufw_path, backup_path, progress=True, loader_path=None, enter_boot=False,
              application_only=None, dry_run=False):
    u = ufwmod.load(ufw_path)
    validate_package(u, application_only)
    ldr = open_loader(node, loader_path, enter_boot)
    try:
        plan = prepare_flash(ldr, u, backup_path, progress, application_only, dry_run)
        return plan.changed_sectors if dry_run else plan.program(ldr, progress)
    finally:
        ldr.dev.close()


def restore(node, image_path, backup_path, progress=True, loader_path=None, enter_boot=False,
            dry_run=False):
    """Restore a complete, physical flash dump; never treat UFW staging data as raw."""
    image = Path(image_path).read_bytes()
    ldr = open_loader(node, loader_path, enter_boot)
    try:
        if len(image) != ldr.flash_size:
            raise SCSIError("Restore requires a full physical dump matching flash capacity")
        info = flashimage.validate(image, ldr.chip_key_value, ldr.flash_size)
        if info["product"] != PROFILES[ldr.family]["chip"]:
            raise SCSIError("Restore image product does not match the device chip family")
        digest = _backup(ldr, backup_path, ldr.flash_size, progress)
        backup = Path(backup_path).read_bytes()
        if hashlib.sha256(backup).hexdigest() != digest:
            raise SCSIError("Backup changed after verification")
        with Path(str(backup_path) + ".json").open("x") as output:
            json.dump({"family": ldr.family, "flash_id": hex(ldr.flash_id),
                       "bytes": len(backup), "backup_sha256": digest,
                       "dry_run": dry_run,
                       "restore_sha256": hashlib.sha256(image).hexdigest()}, output, indent=2)
            output.flush()
            os.fsync(output.fileno())
        return _program(ldr, image, backup, progress, dry_run=dry_run)
    finally:
        ldr.dev.close()
