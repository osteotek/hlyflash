import struct
import threading
import types
import unittest

from hlyflash.scsi import CommandFailed, SCSIError
from hlyflash.usb import USBDev, CBW_SIGNATURE, CSW_SIGNATURE


class USBError(Exception):
    def __init__(self, code):
        self.backend_error_code = code


class Wire:
    def __init__(self, replies=()):
        self.replies = list(replies)
        self.writes = []
        self.halts = []

    def write(self, endpoint, data, timeout):
        self.writes.append(bytes(data))
        return len(data)

    def read(self, endpoint, size, timeout):
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def clear_halt(self, endpoint):
        self.halts.append(endpoint)


def status(tag=1, residue=0, code=0, signature=CSW_SIGNATURE):
    return struct.pack("<IIIB", signature, tag, residue, code)


def device(replies):
    dev = object.__new__(USBDev)
    dev.device = Wire(replies)
    dev.ep_in, dev.ep_out = 0x81, 1
    dev.claimed, dev.broken, dev.tag = True, False, 0
    dev.lock = threading.Lock()
    dev.core = types.SimpleNamespace(USBError=USBError)
    return dev


class BulkOnlyTests(unittest.TestCase):
    def test_inquiry_wrapper_and_response(self):
        dev = device([bytes(range(36)), status()])
        cdb = b"\x12\x00\x00\x00\x24\x00"
        self.assertEqual(dev.execute(cdb, None, 36), bytes(range(36)))
        cbw = dev.device.writes[0]
        self.assertEqual(len(cbw), 31)
        self.assertEqual(struct.unpack("<IIIBBB16s", cbw),
                         (CBW_SIGNATURE, 1, 36, 0x80, 0, 6, cdb.ljust(16, b"\0")))

    def test_data_out_and_no_data_commands(self):
        dev = device([status(), status(tag=2)])
        self.assertEqual(dev.execute(b"\xfb\x04", b"abc", 0), b"")
        self.assertEqual(dev.device.writes[1], b"abc")
        dev.execute(b"\0", None, 0)
        self.assertEqual(len(dev.device.writes), 3)  # no empty data packet

    def test_command_rejection_does_not_desynchronize_next_command(self):
        dev = device([USBError(-9), status(residue=16, code=1), b"OK", status(tag=2)])
        with self.assertRaises(CommandFailed):
            dev.execute(b"\xfc\x14", None, 16)
        self.assertEqual(dev.device.halts, [0x81])
        self.assertFalse(dev.broken)
        self.assertEqual(dev.execute(b"\x12", None, 2), b"OK")

    def test_bad_status_and_incomplete_transfers_poison_session(self):
        cases = [status(tag=99), status(signature=0), status(residue=1),
                 status(code=2), b"short"]
        for csw in cases:
            with self.subTest(csw=csw):
                dev = device([b"abc", csw])
                with self.assertRaises(SCSIError):
                    dev.execute(b"\xfd\x05", None, 3)
                self.assertTrue(dev.broken)
                with self.assertRaises(SCSIError):
                    dev.execute(b"\xfb\x01", None, 16)
                self.assertEqual(len(dev.device.writes), 1)

    def test_short_data_and_timeout_are_not_retried(self):
        for reply in (b"x", USBError(-7)):
            dev = device([reply, status()])
            with self.assertRaises(SCSIError):
                dev.execute(b"\xfd\x05", None, 3)
            self.assertTrue(dev.broken)
            self.assertEqual(len(dev.device.writes), 1)

    def test_validation_precedes_any_usb_io(self):
        for cdb, outgoing, incoming in ((b"", None, 0), (b"x" * 17, None, 0),
                                         (b"x", b"x", 1), (b"x", None, -1)):
            dev = device([])
            with self.assertRaises(ValueError):
                dev.execute(cdb, outgoing, incoming)
            self.assertEqual(dev.device.writes, [])

    def test_rejected_upload_reports_sense_without_replaying(self):
        sense = b"\x70\x00\x05" + b"\0" * 9 + b"\x24\0" + b"\0" * 4
        dev = device([USBError(-9), status(residue=3, code=1), sense, status(tag=2)])
        with self.assertRaisesRegex(CommandFailed, "sense=700005"):
            dev.execute(b"\xfb\x06", b"abc", 0)
        commands = [w[15] for w in dev.device.writes if len(w) == 31]
        self.assertEqual(commands, [0xFB, 0x03])

    def test_opt_in_stale_residue_still_requires_exact_byte_count(self):
        dev = device([b"abc", status(residue=3)])
        dev.allow_stale_residue = True
        self.assertEqual(dev.execute(b"\xfd\x07", None, 3), b"abc")
        for data, residue in ((b"a", 3), (b"abc", 1)):
            dev = device([data, status(residue=residue)])
            dev.allow_stale_residue = True
            with self.assertRaises(SCSIError):
                dev.execute(b"\xfd\x07", None, 3)


class LibraryDiscoveryTests(unittest.TestCase):
    def test_packaged_libusb_works_when_no_system_library_is_installed(self):
        from unittest.mock import patch
        from hlyflash.usb import _modules
        bundle = types.SimpleNamespace(get_library_path=lambda: '/package/libusb-1.0.dylib')
        seen = []
        def backend(find_library):
            seen.append(find_library('usb-1.0'))
            return 'backend'
        with patch('hlyflash.usb.ctypes.util.find_library', return_value=None), \
                patch('hlyflash.usb.os.path.isfile', return_value=False), \
                patch.dict('sys.modules', libusb_package=bundle), \
                patch('usb.backend.libusb1.get_backend', side_effect=backend):
            self.assertEqual(_modules()[2], 'backend')
        self.assertEqual(seen, ['/package/libusb-1.0.dylib'])

    def test_existing_system_library_retains_priority(self):
        from unittest.mock import patch
        from hlyflash.usb import _modules
        with patch('hlyflash.usb.ctypes.util.find_library', return_value='/system/libusb.so'), \
                patch('usb.backend.libusb1.get_backend', side_effect=lambda find_library: find_library('usb-1.0')):
            self.assertEqual(_modules()[2], '/system/libusb.so')

    def test_unavailable_library_has_actionable_error(self):
        from unittest.mock import patch
        from hlyflash.usb import _modules
        with patch('usb.backend.libusb1.get_backend', return_value=None):
            with self.assertRaisesRegex(SCSIError, 'hlyflash doctor'):
                _modules()
