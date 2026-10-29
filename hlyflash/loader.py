"""JieLi USB download (UBOOT / LoaderV2) protocol over SCSI.

This is the public, documented JieLi mass-storage command set (the same one
implemented by the open-source jl-uboot-tool, https://github.com/kagaimiq/
jl-uboot-tool, and matching the vendor jl_firmware_upgrade library). A chip in
download mode presents as USB Mass Storage; vendor commands ride in 16-byte
SCSI CDBs: 2-byte big-endian opcode, then args, padded with 0xFF. A 16-byte
response echoes the opcode in its first two bytes.

Opcode families: 0xFB = write/erase, 0xFC = control/query, 0xFD = read.
"""
from __future__ import annotations

from .enc import crc16
from .scsi import SCSIDev, SCSIError


def _cdb(opcode: int, args: bytes = b"") -> bytes:
    cdb = opcode.to_bytes(2, "big") + args
    if len(cdb) < 16:
        cdb += b"\xff" * (16 - len(cdb))
    return cdb


class Loader:
    """LoaderV2 / UBOOT1.00 command wrapper around an open SCSIDev."""

    # --- command opcodes (LoaderV2) ---
    ERASE_BLOCK = 0xFB00     # 64 KiB
    ERASE_SECTOR = 0xFB01    # 4 KiB
    ERASE_CHIP = 0xFB02
    READ_STATUS = 0xFC03
    WRITE_FLASH = 0xFB04
    READ_FLASH = 0xFD05
    WRITE_MEM = 0xFB06
    READ_MEM = 0xFD07
    JUMP = 0xFB08
    READ_KEY = 0xFC09
    ONLINE_DEVICE = 0xFC0A
    READ_ID = 0xFC0B
    RUN_APP = 0xFC0C

    def __init__(self, dev: SCSIDev):
        self.dev = dev

    # --- transport helpers ---
    def _resp(self, opcode: int, args: bytes = b"", check: bool = True) -> bytes:
        r = self.dev.execute(_cdb(opcode, args), None, 16)
        if check and int.from_bytes(r[:2], "big") != opcode:
            raise SCSIError(f"response opcode {r[:2].hex()} != {opcode:#06x}")
        return r[2:]

    def _datain(self, opcode: int, args: bytes, length: int) -> bytes:
        return self.dev.execute(_cdb(opcode, args), None, length)

    def _dataout(self, opcode: int, args: bytes, data: bytes) -> None:
        self.dev.execute(_cdb(opcode, args), data, 0)

    # --- SCSI standard inquiry (to read UBOOT/UDISK/DEVICE product string) ---
    def inquiry(self) -> tuple[str, str, str]:
        data = self.dev.execute(
            bytes([0x12, 0, 0]) + (36).to_bytes(2, "big") + bytes([0]), None, 36)
        return (data[8:16].decode("ascii", "replace").strip(),
                data[16:32].decode("ascii", "replace").strip(),
                data[32:36].decode("ascii", "replace").strip())

    # --- non-destructive queries ---
    def online_device(self) -> dict:
        r = self._resp(self.ONLINE_DEVICE)
        return {"type": r[0], "id": int.from_bytes(r[2:6], "little")}

    def read_id(self) -> int:
        r = self._resp(self.READ_ID)
        return int.from_bytes(r[0:3], "big")

    def read_status(self) -> int:
        return self._resp(self.READ_STATUS)[0]

    def flash_read(self, addr: int, length: int) -> bytes:
        return self._datain(
            self.READ_FLASH,
            addr.to_bytes(4, "big") + length.to_bytes(2, "big"), length)

    # --- destructive: upload stub / write flash (EXPERIMENTAL, see flasher) ---
    def mem_write(self, addr: int, data: bytes) -> None:
        self._dataout(
            self.WRITE_MEM,
            addr.to_bytes(4, "big") + len(data).to_bytes(2, "big")
            + b"\x00" + crc16(data).to_bytes(2, "little"), data)

    def mem_jump(self, addr: int, arg: int = 0) -> None:
        self._resp(self.JUMP, addr.to_bytes(4, "big") + arg.to_bytes(2, "big"))

    def flash_erase_sector(self, addr: int) -> None:
        self._resp(self.ERASE_SECTOR, addr.to_bytes(4, "big"))

    def flash_write(self, addr: int, data: bytes) -> None:
        self._dataout(
            self.WRITE_FLASH,
            addr.to_bytes(4, "big") + len(data).to_bytes(2, "big")
            + b"\x00" + crc16(data).to_bytes(2, "little"), data)

    def run_app(self, arg: int = 0) -> None:
        self._resp(self.RUN_APP, arg.to_bytes(4, "big"), check=False)


def is_uboot_product(product: str) -> bool:
    return product.upper().startswith("UBOOT")
