"""JieLi SCSI commands over USB Mass Storage Bulk-Only Transport.

Uses PyUSB and libusb. On macOS capture requires Terminal sudo and
temporarily takes all interfaces of the selected device from their drivers.
Commands are never replayed after an ambiguous transport failure.
"""
from __future__ import annotations

import ctypes.util
import errno
import os
import struct
import sys
import threading
import time

from .scsi import CommandFailed, SCSIError, USBAccessError

VID = 0x4C4A
CBW_SIGNATURE = 0x43425355
CSW_SIGNATURE = 0x53425355
MAX_TRANSFER = 0x10000


def _disconnected(error):
    return (getattr(error, "backend_error_code", None) == -4 or
            getattr(error, "errno", None) == errno.ENODEV)


def _modules():
    try:
        import usb.core
        import usb.util
        import usb.backend.libusb1
    except ImportError as e:
        raise SCSIError("PyUSB is missing. Run hlyflash doctor for installation help.") from e

    def library(_name):
        found = ctypes.util.find_library("usb-1.0")
        if found:
            return found
        if sys.platform == "darwin":
            for path in ("/opt/homebrew/lib/libusb-1.0.dylib",
                         "/usr/local/lib/libusb-1.0.dylib"):
                if os.path.isfile(path):
                    return path
        try:
            import libusb_package
            bundled = libusb_package.get_library_path()
        except ImportError:
            bundled = None
        if bundled:
            return str(bundled)
        return None

    backend = usb.backend.libusb1.get_backend(find_library=library)
    if backend is None:
        raise SCSIError("libusb could not be loaded. Run hlyflash doctor for installation help.")
    return usb.core, usb.util, backend


def _interface(device):
    matches = []
    for intf in device.get_active_configuration():
        if (intf.bInterfaceClass, intf.bInterfaceSubClass,
                intf.bInterfaceProtocol, intf.bAlternateSetting) != (8, 6, 0x50, 0):
            continue
        incoming = [e.bEndpointAddress for e in intf
                    if e.bmAttributes & 3 == 2 and e.bEndpointAddress & 0x80]
        outgoing = [e.bEndpointAddress for e in intf
                    if e.bmAttributes & 3 == 2 and not e.bEndpointAddress & 0x80]
        if len(incoming) == len(outgoing) == 1:
            matches.append((intf.bInterfaceNumber, incoming[0], outgoing[0]))
    if len(matches) != 1:
        raise SCSIError("Expected one SCSI Bulk-Only Transport interface.")
    return matches[0]


def _select(selector, core, backend, location=None):
    wanted = None
    if selector != "usb":
        try:
            prefix, bus, address = selector.split(":")
            if prefix != "usb":
                raise ValueError()
            wanted = (int(bus), int(address))
        except ValueError as e:
            raise SCSIError("Use usb or usb:<bus>:<address> as the device node.") from e
    devices = []
    for dev in core.find(find_all=True, idVendor=VID, backend=backend):
        if location is not None and (dev.bus, tuple(dev.port_numbers or ())) != location:
            continue
        if wanted is not None and (dev.bus, dev.address) != wanted:
            continue
        try:
            _interface(dev)
        except SCSIError:
            continue
        devices.append(dev)
    if len(devices) != 1:
        nodes = ", ".join(f"usb:{d.bus}:{d.address}" for d in devices)
        raise SCSIError(f"Expected one JieLi USB storage device, found {len(devices)}"
                        + (f": {nodes}. Select one explicitly." if nodes else "."))
    return devices[0]


class USBDev:
    def __init__(self, selector="usb", location=None):
        self.core, self.util, backend = _modules()
        self.device = None
        self.claimed = self.detached = self.broken = False
        self.tag = 0
        self.allow_stale_residue = False
        self.lock = threading.Lock()
        if sys.platform == "darwin" and os.geteuid() != 0:
            raise USBAccessError("macOS USB capture requires administrator access. "
                            "Run this command with sudo from Terminal.")
        try:
            self.device = _select(selector, self.core, backend, location)
            self.node = f"usb:{self.device.bus}:{self.device.address}"
            self.location = (self.device.bus, tuple(self.device.port_numbers or ()))
            self.iface, self.ep_in, self.ep_out = _interface(self.device)
            if sys.platform.startswith("linux"):
                from .linux import mounted_volumes
                mounts = mounted_volumes(self.location)
                if mounts:
                    raise SCSIError("The selected USB device has mounted volumes: " +
                                    ", ".join(mounts) + ". Unmount them before updating")
                if self.device.is_kernel_driver_active(self.iface):
                    self.device.detach_kernel_driver(self.iface)
                    self.detached = True
            try:
                self.util.claim_interface(self.device, self.iface)
            except self.core.USBError:
                if sys.platform != "darwin":
                    raise
                # Capture affects the whole composite device on macOS.
                self.device.detach_kernel_driver(self.iface)
                self.detached = True
                for attempt in range(20):
                    time.sleep(0.1)
                    try:
                        self.util.claim_interface(self.device, self.iface)
                        break
                    except self.core.USBError:
                        if attempt == 19:
                            raise
            self.claimed = True
        except BaseException as e:
            try:
                self.close()
            except SCSIError:
                pass
            if isinstance(e, SCSIError):
                raise
            if not isinstance(e, Exception):
                raise
            if (getattr(e, "errno", None) in (1, 13) or
                    getattr(e, "backend_error_code", None) == -3):
                hint = ". Run hlyflash setup and reconnect the device" if sys.platform.startswith("linux") else ""
                raise USBAccessError(f"USB access denied: {e}{hint}") from e
            raise SCSIError(f"Could not capture the USB device: {e}") from e

    def close(self, restore=True):
        if self.device is None:
            return
        errors = []
        if self.claimed:
            try:
                self.util.release_interface(self.device, self.iface)
            except self.core.USBError as e:
                if not _disconnected(e):
                    errors.append(e)
            self.claimed = False
        if self.detached and restore:
            try:
                self.device.attach_kernel_driver(self.iface)
            except self.core.USBError as e:
                if not _disconnected(e):
                    errors.append(e)
            self.detached = False
        self.util.dispose_resources(self.device)
        self.device = None
        if errors:
            raise SCSIError(f"USB driver restoration failed; unplug/reconnect the device: {errors[0]}")

    def __enter__(self):
        return self

    def __exit__(self, kind, value, traceback):
        try:
            self.close()
        except SCSIError:
            if kind is None:
                raise

    def execute(self, cdb, data_out, data_in_len, timeout_ms=5000):
        if not 1 <= len(cdb) <= 16:
            raise ValueError("SCSI CDB must contain 1..16 bytes")
        if data_out is not None and data_in_len:
            raise ValueError("A command cannot transfer in both directions")
        length = len(data_out) if data_out is not None else data_in_len
        if not 0 <= length <= MAX_TRANSFER or timeout_ms <= 0:
            raise ValueError("Invalid USB transfer length or timeout")
        with self.lock:
            if not self.claimed or self.broken:
                raise SCSIError("USB transport closed or failed; reconnect before issuing commands.")
            self.tag = (self.tag + 1) & 0xFFFFFFFF
            cbw = struct.pack("<IIIBBB16s", CBW_SIGNATURE, self.tag, length,
                              0x80 if data_in_len else 0, 0, len(cdb), bytes(cdb))
            try:
                return self._transfer(cbw, data_out, data_in_len, timeout_ms)
            except CommandFailed as e:
                # A valid failed-command status leaves BOT synchronized.
                detail = ""
                if data_out is not None:
                    # Diagnose a rejected upload/write without replaying it.
                    self.tag = (self.tag + 1) & 0xFFFFFFFF
                    sense_cdb = b"\x03\0\0\0\x12\0"
                    sense_cbw = struct.pack("<IIIBBB16s", CBW_SIGNATURE, self.tag, 18,
                                            0x80, 0, 6, sense_cdb)
                    try:
                        sense = self._transfer(sense_cbw, None, 18, timeout_ms)
                        detail = f"; sense={sense.hex()}"
                    except CommandFailed:
                        detail = "; REQUEST SENSE unsupported"
                    except Exception:
                        self.broken = True
                        detail = "; REQUEST SENSE transport failed"
                raise CommandFailed(f"SCSI {bytes(cdb[:2]).hex()}: {e}{detail}") from e
            except Exception as e:
                self.broken = True
                if isinstance(e, SCSIError):
                    raise
                raise SCSIError(f"USB transfer failed; command was not retried: {e}") from e

    def _read(self, endpoint, length, timeout):
        return bytes(self.device.read(endpoint, length, timeout=timeout))

    def _transfer(self, cbw, data_out, data_in_len, timeout):
        if self.device.write(self.ep_out, cbw, timeout=timeout) != 31:
            raise SCSIError("Short USB command wrapper write")
        received = b""
        moved = 0
        stalled = False
        try:
            if data_in_len:
                received = self._read(self.ep_in, data_in_len, timeout)
                moved = len(received)
            elif data_out:
                moved = self.device.write(self.ep_out, data_out, timeout=timeout)
        except self.core.USBError as e:
            if e.backend_error_code != -9:  # LIBUSB_ERROR_PIPE
                raise
            self.device.clear_halt(self.ep_in if data_in_len else self.ep_out)
            stalled = True
        try:
            csw = self._read(self.ep_in, 13, timeout)
        except self.core.USBError as e:
            if e.backend_error_code != -9:
                raise
            self.device.clear_halt(self.ep_in)
            csw = self._read(self.ep_in, 13, timeout)
        if len(csw) != 13:
            raise SCSIError("Short USB command status wrapper")
        signature, tag, residue, status = struct.unpack("<IIIB", csw)
        requested = data_in_len if data_out is None else len(data_out)
        if signature != CSW_SIGNATURE or tag != self.tag or residue > requested:
            raise SCSIError("Invalid USB command status signature, tag or residue")
        if status == 1:
            raise CommandFailed(f"Device rejected SCSI command (BOT status 1, "
                                f"transferred={moved}/{requested}, residue={residue})")
        if status != 0:
            raise SCSIError(f"USB Bulk-Only Transport phase error (status {status})")
        # Some JieLi ROMs leave dCSWDataResidue unchanged for vendor commands.
        # This opt-in quirk still requires successful status and the full actual
        # byte count. ROM uploads additionally require RAM readback before jump.
        stale = (getattr(self, "allow_stale_residue", False) and
                 residue == requested and moved == requested)
        if stalled or (residue and not stale) or moved != requested:
            raise SCSIError(f"Incomplete USB transfer: {moved}/{requested} bytes, residue={residue}")
        return received
