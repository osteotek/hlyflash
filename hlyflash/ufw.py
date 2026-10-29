"""Parser/verifier for JieLi ".ufw" firmware containers (as shipped by Hanlinyue).

Layout (all little-endian), recovered from the vendor updater and verified
against a real AB7ELJ.ufw:

  Header, 0x40 bytes, ENC-scrambled with key 0xFFFF:
    0x00 u16  CRC16 of header[2:0x40]
    0x02 u16  CRC16 of the whole entry table
    0x04 u32  total file size
    0x08 u16  entry count
    0x0A u16  (flags; observed small)
    0x0C u16  (reserved)
    0x10      chip name, NUL-padded ASCII (e.g. "AC632N")

  Entry table: <count> records of 0x50 bytes, ENC-scrambled with key 0xFFFF:
    0x00 u8   type
    0x04 u16  CRC16 of this entry's payload
    0x08 u32  payload file offset
    0x0C u32  payload size
    0x40      name, NUL-padded ASCII (e.g. "flash.bin")

The payloads are stored in the clear in the file; the main one, "flash.bin",
is the chip flash image. (The flash image itself has an inner per-0x20-block
ENC layer, handled only by the flasher, not needed to validate the container.)
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

from .enc import crc16, enc_bytes

HEADER_LEN = 0x40
ENTRY_LEN = 0x50
UFW_KEY = 0xFFFF


@dataclass
class UfwEntry:
    index: int
    type: int
    crc: int
    offset: int
    size: int
    name: str

    def payload(self, raw: bytes) -> bytes:
        return raw[self.offset:self.offset + self.size]

    def crc_ok(self, raw: bytes) -> bool:
        return crc16(self.payload(raw)) == self.crc


@dataclass
class Ufw:
    raw: bytes
    size: int
    chip: str
    entries: list[UfwEntry]
    header_crc_ok: bool
    table_crc_ok: bool

    def entry(self, name: str) -> UfwEntry | None:
        for e in self.entries:
            if e.name == name:
                return e
        return None


def _cstr(b: bytes) -> str:
    return b.split(b"\x00", 1)[0].decode("ascii", "replace")


def parse(raw: bytes) -> Ufw:
    if len(raw) < HEADER_LEN:
        raise ValueError("file too short to be a .ufw")

    hdr = enc_bytes(raw[:HEADER_LEN], UFW_KEY)
    hdr_crc, table_crc, total, count = struct.unpack_from("<HHIH", hdr, 0)
    chip = _cstr(hdr[0x10:0x40])
    header_crc_ok = crc16(hdr[2:]) == hdr_crc

    table_raw = raw[HEADER_LEN:HEADER_LEN + count * ENTRY_LEN]
    table_crc_ok = crc16(table_raw) == table_crc

    entries: list[UfwEntry] = []
    for i in range(count):
        rec = enc_bytes(table_raw[i * ENTRY_LEN:(i + 1) * ENTRY_LEN], UFW_KEY)
        etype = rec[0]
        (ecrc,) = struct.unpack_from("<H", rec, 4)
        eoff, esize = struct.unpack_from("<II", rec, 8)
        name = _cstr(rec[0x40:0x50])
        entries.append(UfwEntry(i, etype, ecrc, eoff, esize, name))

    return Ufw(
        raw=raw,
        size=total,
        chip=chip,
        entries=entries,
        header_crc_ok=header_crc_ok,
        table_crc_ok=table_crc_ok,
    )


def load(path: str) -> Ufw:
    with open(path, "rb") as f:
        return parse(f.read())
