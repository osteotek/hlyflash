import unittest
from unittest.mock import patch

from hlyflash.scsi import SCSIDev, SCSIError, CommandFailed


class SCSICompletionTests(unittest.TestCase):
    def test_residual_and_driver_errors_are_not_silently_accepted(self):
        for field, value in (("resid", 4), ("driver_status", 8), ("host_status", 7)):
            with self.subTest(field=field):
                dev = object.__new__(SCSIDev)
                dev.fd = -1
                def ioctl(fd, operation, header):
                    setattr(header, field, value)
                with patch("hlyflash.scsi.fcntl.ioctl", side_effect=ioctl):
                    with self.assertRaises(SCSIError):
                        dev.execute(b"\x12\0\0\0\x10\0", None, 16)

    def test_check_condition_is_distinct_from_transport_failure(self):
        dev = object.__new__(SCSIDev)
        dev.fd = -1
        def ioctl(fd, operation, header):
            header.status = 2
        with patch("hlyflash.scsi.fcntl.ioctl", side_effect=ioctl):
            with self.assertRaises(CommandFailed):
                dev.execute(b"\xfc\x14", None, 16)
