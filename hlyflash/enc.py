"""JieLi obfuscation / checksum primitives used by the .ufw firmware container
and the USB download (UBOOT) protocol.

These are interoperability primitives recovered from the vendor's own tool so
that .ufw files can be parsed and verified on Linux/macOS. Nothing here is
secret-key cryptography: `enc` is a CRC16-LFSR keystream XOR ("ENC" scrambler),
and the CRCs are standard CRC-CCITT / Dallas-1Wire variants.
"""
from __future__ import annotations


def crc16(data: bytes, init: int = 0) -> int:
    """CRC-CCITT (poly 0x1021, MSB-first), as used throughout the JL protocol."""
    crc = init & 0xFFFF
    for b in data:
        crc ^= (b << 8) & 0xFFFF
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def crc8(data: bytes) -> int:
    """Dallas/Maxim 1-Wire CRC-8 (poly 0x8C, reflected). Used for CDB byte 15."""
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8C if crc & 1 else crc >> 1
    return crc


def enc(buf: bytearray, key: int, off: int = 0, length: int | None = None) -> int:
    """In-place JieLi "ENC" scrambler: XOR with a CRC16-shift-register keystream.

    key advances as a 16-bit LFSR (same polynomial as crc16). Returns the final
    key so callers can resume a stream. Matches the vendor jl_enc routine.
    """
    if length is None:
        length = len(buf) - off
    key &= 0xFFFF
    for i in range(length):
        buf[off + i] ^= key & 0xFF
        if key & 0x8000:
            key = ((key << 1) ^ 0x1021) & 0xFFFF
        else:
            key = (key << 1) & 0xFFFF
    return key


def enc_bytes(data: bytes, key: int) -> bytes:
    b = bytearray(data)
    enc(b, key)
    return bytes(b)
