import contextlib
import io
import unittest
from unittest.mock import patch

from hlyflash import __version__, doctor
from hlyflash.cli import main
from hlyflash.scsi import SCSIError
from hlyflash.terminal import Terminal


class DoctorTests(unittest.TestCase):
    def test_success_needs_no_device_sudo_or_flash_access(self):
        output = io.StringIO()
        with patch('hlyflash.doctor.sys.platform', 'darwin'), \
                patch('hlyflash.doctor._serial', return_value='pyserial installed'), \
                patch('hlyflash.doctor._usb', return_value='libusb loaded'), \
                patch('hlyflash.updater._ports') as ports, \
                patch('hlyflash.usb.USBDev') as capture, \
                patch('hlyflash.flasher.open_loader') as loader, \
                patch('subprocess.run') as sudo:
            self.assertEqual(doctor.run(Terminal(output)), 0)
            ports.assert_not_called()
            capture.assert_not_called()
            loader.assert_not_called()
            sudo.assert_not_called()
        self.assertIn('Bundled BR25 loader verified', output.getvalue())
        self.assertIn('Ready.', output.getvalue())

    def test_reports_all_failures_with_repair_instructions(self):
        output = io.StringIO()
        with patch('hlyflash.doctor._serial', side_effect=ImportError('No module named serial')), \
                patch('hlyflash.doctor._usb', side_effect=SCSIError('libusb could not be loaded')), \
                patch('hlyflash.doctor._loader', side_effect=ValueError('loader checksum does not match')), \
                patch('hlyflash.doctor._repair', return_value='uv tool install --reinstall .'):
            self.assertEqual(doctor.run(Terminal(output)), 1)
        text = output.getvalue()
        self.assertIn('Serial support', text)
        self.assertIn('USB support', text)
        self.assertIn('RAM loader', text)
        self.assertIn('uv tool install --reinstall .', text)
        self.assertNotIn('Ready.', text)

    def test_corrupt_loader_is_not_reported_as_ready(self):
        with patch('hlyflash.doctor.files') as resource:
            resource.return_value.joinpath.return_value.read_bytes.return_value = b'corrupt'
            with self.assertRaisesRegex(ValueError, 'checksum'):
                doctor._loader()

    def test_different_serial_package_is_explained(self):
        import types
        with patch.dict('sys.modules', serial=types.SimpleNamespace()):
            with self.assertRaisesRegex(ValueError, 'shadowing pyserial'):
                doctor._serial()

    def test_version_and_empty_command_work_without_a_device(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            with self.assertRaises(SystemExit) as exit_code:
                main(['--version'])
            self.assertEqual(exit_code.exception.code, 0)
            self.assertEqual(main([]), 0)
        self.assertIn('hlyflash ' + __version__, output.getvalue())
        self.assertIn('doctor', output.getvalue())

    def test_unsupported_os_does_not_claim_flash_support(self):
        output = io.StringIO()
        with patch('hlyflash.doctor.sys.platform', 'win32'), \
                patch('hlyflash.doctor._serial', return_value='serial installed'), \
                patch('hlyflash.doctor._usb', return_value='libusb loaded'):
            self.assertEqual(doctor.run(Terminal(output)), 1)
        self.assertNotIn('Ready.', output.getvalue())

    def test_repairs_use_the_original_tool_manager(self):
        from pathlib import Path
        import tempfile
        with tempfile.TemporaryDirectory() as directory, patch('hlyflash.doctor.sys.prefix', directory):
            (Path(directory) / 'uv-receipt.toml').touch()
            self.assertIn('uv tool install --reinstall .', doctor._repair())
            (Path(directory) / 'uv-receipt.toml').unlink()
            (Path(directory) / 'pipx_metadata.json').touch()
            self.assertIn('pipx reinstall hlyflash', doctor._repair())
