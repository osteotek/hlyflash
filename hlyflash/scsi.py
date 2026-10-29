"""Minimal SCSI generic (SG_IO) passthrough for Linux.

JieLi chips in USB download (UBOOT) mode present as a USB Mass Storage device
and carry a vendor command set over custom SCSI CDBs. On Linux we can issue
those CDBs directly to the /dev/sgN or /dev/sdX node via the SG_IO ioctl.

macOS/Windows are not supported here: macOS claims mass-storage devices with
its own driver and offers no comparable userspace passthrough, and Windows uses
a different ioctl (SCSI_PASS_THROUGH_DIRECT). See README for the platform matrix.
"""
from __future__ import annotations

import ctypes
import fcntl
import os
import sys

SG_IO = 0x2285
SG_DXFER_NONE = -1
SG_DXFER_TO_DEV = -2
SG_DXFER_FROM_DEV = -3


class _sg_io_hdr(ctypes.Structure):
    _fields_ = [
        ("interface_id", ctypes.c_int),
        ("dxfer_direction", ctypes.c_int),
        ("cmd_len", ctypes.c_ubyte),
        ("mx_sb_len", ctypes.c_ubyte),
        ("iovec_count", ctypes.c_ushort),
        ("dxfer_len", ctypes.c_uint),
        ("dxferp", ctypes.c_void_p),
        ("cmdp", ctypes.c_void_p),
        ("sbp", ctypes.c_void_p),
        ("timeout", ctypes.c_uint),
        ("flags", ctypes.c_uint),
        ("pack_id", ctypes.c_int),
        ("usr_ptr", ctypes.c_void_p),
        ("status", ctypes.c_ubyte),
        ("masked_status", ctypes.c_ubyte),
        ("msg_status", ctypes.c_ubyte),
        ("sb_len_wr", ctypes.c_ubyte),
        ("host_status", ctypes.c_ushort),
        ("driver_status", ctypes.c_ushort),
        ("resid", ctypes.c_int),
        ("duration", ctypes.c_uint),
        ("info", ctypes.c_uint),
    ]


class SCSIError(Exception):
    pass


class SCSIDev:
    """Open a SCSI generic node and run raw CDBs. Linux only."""

    def __init__(self, path: str):
        if not sys.platform.startswith("linux"):
            raise SCSIError(
                "SCSI passthrough is Linux-only in this tool; "
                "see README for macOS/Windows options.")
        self.fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    def execute(self, cdb: bytes, data_out: bytes | None,
                data_in_len: int, timeout_ms: int = 5000) -> bytes:
        """Run one CDB. Provide data_out for TO_DEV, data_in_len for FROM_DEV."""
        sense = ctypes.create_string_buffer(32)
        cmd = ctypes.create_string_buffer(bytes(cdb), len(cdb))

        if data_out is not None:
            direction = SG_DXFER_TO_DEV
            dbuf = ctypes.create_string_buffer(bytes(data_out), len(data_out))
            dlen = len(data_out)
        elif data_in_len > 0:
            direction = SG_DXFER_FROM_DEV
            dbuf = ctypes.create_string_buffer(data_in_len)
            dlen = data_in_len
        else:
            direction = SG_DXFER_NONE
            dbuf = None
            dlen = 0

        hdr = _sg_io_hdr()
        hdr.interface_id = ord('S')
        hdr.dxfer_direction = direction
        hdr.cmd_len = len(cdb)
        hdr.mx_sb_len = 32
        hdr.dxfer_len = dlen
        hdr.dxferp = ctypes.cast(dbuf, ctypes.c_void_p) if dbuf else None
        hdr.cmdp = ctypes.cast(cmd, ctypes.c_void_p)
        hdr.sbp = ctypes.cast(sense, ctypes.c_void_p)
        hdr.timeout = timeout_ms

        fcntl.ioctl(self.fd, SG_IO, hdr)

        if hdr.status != 0 or hdr.host_status != 0:
            raise SCSIError(
                f"SCSI status={hdr.status:#x} host={hdr.host_status:#x} "
                f"driver={hdr.driver_status:#x} sense={sense.raw[:hdr.sb_len_wr].hex()}")
        if direction == SG_DXFER_FROM_DEV:
            return dbuf.raw[:dlen]
        return b""
