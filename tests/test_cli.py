import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hlyflash.cli import main
from test_flashing import package, physical_image


class WriteGateTests(unittest.TestCase):
    def test_restore_without_force_never_opens_device(self):
        with patch("hlyflash.flasher.open_device") as device, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["restore", "original.bin", "--node", "usb",
                                   "--backup", "before.bin"]), 2)
            device.assert_not_called()

    def test_flash_without_force_never_opens_device(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "image.ufw"
            path.write_bytes(package(physical_image(b"Q" * 5000)))
            with patch("hlyflash.flasher.open_device") as device, \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(["flash", str(path), "--node", "usb",
                                       "--backup", "before.bin"]), 2)
                device.assert_not_called()
