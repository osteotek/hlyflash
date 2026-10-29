"""High-level flash operations for a device already in UBOOT download mode.

SAFETY
------
`dump` is non-destructive and is the recommended first step -- it saves a full
backup of the current flash so the device can be recovered.

`flash` WRITES the chip and can brick the device if interrupted or if the image
is wrong. It is EXPERIMENTAL: the protocol was recovered by reverse-engineering
and the write path has not been validated end-to-end on real hardware by this
tool's author. It mirrors the proven jl-uboot-tool write sequence, but you use
it at your own risk. Always `dump` first, keep the device powered, and prefer
the vendor's Windows updater (in a VM) for routine updates.
"""
from __future__ import annotations

import sys
import time

from .enc import crc16
from .loader import Loader, is_uboot_product
from .scsi import SCSIDev, SCSIError
from . import ufw as ufwmod

CHUNK = 0x1000  # 4 KiB: one flash sector per erase/write step


def open_loader(node: str) -> Loader:
    dev = SCSIDev(node)
    ldr = Loader(dev)
    vendor, product, rev = ldr.inquiry()
    if not is_uboot_product(product):
        dev.close()
        raise SCSIError(
            f"device on {node} reports product {product!r}, not UBOOT. "
            "Put the device into download mode first (see README).")
    return ldr


def dump(node: str, out_path: str, length: int, progress=True) -> None:
    ldr = open_loader(node)
    try:
        with open(out_path, "wb") as f:
            got = 0
            while got < length:
                n = min(CHUNK, length - got)
                f.write(ldr.flash_read(got, n))
                got += n
                if progress:
                    pct = got * 100 // length
                    print(f"\r  dump {got:#09x}/{length:#09x} ({pct:3d}%)",
                          end="", flush=True)
        if progress:
            print()
    finally:
        ldr.dev.close()


def flash_image(node: str, image: bytes, verify=True, progress=True) -> None:
    """Write a raw flash image (e.g. the UFW 'flash.bin') to flash offset 0.

    EXPERIMENTAL -- see module docstring. Writes sector by sector with readback
    CRC verification. Does NOT reorder the boot block; keep power stable.
    """
    ldr = open_loader(node)
    try:
        total = len(image)
        off = 0
        while off < total:
            n = min(CHUNK, total - off)
            chunk = image[off:off + n]
            ldr.flash_erase_sector(off)
            ldr.flash_write(off, chunk)
            if verify:
                back = ldr.flash_read(off, n)
                if crc16(back) != crc16(chunk):
                    raise SCSIError(
                        f"verify failed at {off:#x}: readback CRC mismatch")
            off += n
            if progress:
                pct = off * 100 // total
                print(f"\r  write {off:#09x}/{total:#09x} ({pct:3d}%)",
                      end="", flush=True)
        if progress:
            print()
    finally:
        ldr.dev.close()


def flash_ufw(node: str, ufw_path: str, progress=True) -> None:
    u = ufwmod.load(ufw_path)
    if not (u.header_crc_ok and u.table_crc_ok):
        raise SCSIError("UFW failed its own checksums; refusing to flash.")
    img = u.entry("flash.bin")
    if img is None:
        raise SCSIError("UFW has no flash.bin entry.")
    if not img.crc_ok(u.raw):
        raise SCSIError("flash.bin failed its checksum; refusing to flash.")
    flash_image(node, img.payload(u.raw), progress=progress)
