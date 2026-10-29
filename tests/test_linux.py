import contextlib
import errno
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from hlyflash import linux, serialinfo, updater
from hlyflash.cli import _device_node, main
from hlyflash.scsi import SCSIError, USBAccessError
from hlyflash.usb import USBDev
from test_usb import USBError


class LinuxSetupTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.rule = self.root / 'rules.d' / linux.RULE_PATH.name

    def test_preview_does_not_install_or_request_sudo(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch('subprocess.run') as run:
            self.assertEqual(main(['setup', '--print-rules']), 0)
            run.assert_not_called()
        self.assertIn('TAG+="uaccess"', output.getvalue())
        self.assertIn('idVendor}=="4c4a"', output.getvalue())
        self.assertIn('idProduct}=="4155"', output.getvalue())
        self.assertNotIn('0666', output.getvalue())

    def test_install_is_private_to_this_device_and_can_be_repeated(self):
        with patch('hlyflash.linux.sys.platform', 'linux'), patch('hlyflash.linux.os.geteuid', return_value=0), \
                patch('hlyflash.linux.RULE_PATH', self.rule), patch('hlyflash.linux.shutil.which', return_value='/sbin/udevadm'), \
                patch('subprocess.run', return_value=SimpleNamespace(returncode=0)) as reload, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(linux.install(), 0)
            self.assertEqual(linux.install(), 0)
        self.assertEqual(self.rule.stat().st_mode & 0o777, 0o644)
        self.assertEqual(self.rule.read_text(), linux.rules())
        reload.assert_called_with(['/sbin/udevadm', 'control', '--reload-rules'], capture_output=True, text=True)
        self.assertEqual(reload.call_count, 2)

    def test_custom_rules_and_symlinks_are_never_overwritten(self):
        self.rule.parent.mkdir()
        self.rule.write_text('# custom rules\n')
        with patch('hlyflash.linux.sys.platform', 'linux'), patch('hlyflash.linux.os.geteuid', return_value=0), \
                patch('hlyflash.linux.RULE_PATH', self.rule), patch('hlyflash.linux.shutil.which', return_value='/sbin/udevadm'):
            with self.assertRaisesRegex(ValueError, 'custom rules'):
                linux.install()
            self.assertEqual(self.rule.read_text(), '# custom rules\n')
            self.rule.unlink()
            self.rule.symlink_to(self.root / 'other')
            with self.assertRaisesRegex(ValueError, 'symlink'):
                linux.install()
        self.assertFalse((self.root / 'other').exists())

    def test_invalid_or_missing_groups_stop_before_writing(self):
        for name in ('bad"group', 'bad group', '../group'):
            with self.assertRaisesRegex(ValueError, 'group name'):
                linux.rules(name)
        with patch('hlyflash.linux.sys.platform', 'linux'), patch('hlyflash.linux.os.geteuid', return_value=0), \
                patch('hlyflash.linux.RULE_PATH', self.rule), patch('hlyflash.linux.grp.getgrnam', side_effect=KeyError):
            with self.assertRaisesRegex(ValueError, 'does not exist'):
                linux.install('missing')
        self.assertFalse(self.rule.exists())
        self.assertEqual(linux.rules('dialout').count('GROUP="dialout"'), 2)

    def test_unprivileged_setup_uses_installed_interpreter_without_shell(self):
        with patch('hlyflash.linux.sys.platform', 'linux'), patch('hlyflash.linux.os.geteuid', return_value=1000), \
                patch('hlyflash.linux.shutil.which', side_effect=lambda name: '/usr/bin/' + name), \
                patch('subprocess.run', return_value=SimpleNamespace(returncode=0)) as run, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(linux.setup('dialout'), 0)
        command = run.call_args.args[0]
        self.assertEqual(command[:2], ['sudo', '--'])
        self.assertEqual(command[2], updater.sys.executable)
        self.assertEqual(command[3:5], ['-I', '-c'])
        self.assertEqual(command[-3:], ['--install', '--group', 'dialout'])
        self.assertNotIn('shell', run.call_args.kwargs)

    def test_missing_udev_stops_before_sudo_or_rule_creation(self):
        with patch('hlyflash.linux.sys.platform', 'linux'), patch('hlyflash.linux.os.geteuid', return_value=1000), \
                patch('hlyflash.linux.shutil.which', return_value=None), patch('subprocess.run') as run:
            with self.assertRaisesRegex(ValueError, 'udevadm not found'):
                linux.setup()
            run.assert_not_called()

    def test_reload_failure_keeps_saved_rules_and_explains_retry(self):
        with patch('hlyflash.linux.sys.platform', 'linux'), patch('hlyflash.linux.os.geteuid', return_value=0), \
                patch('hlyflash.linux.RULE_PATH', self.rule), patch('hlyflash.linux.shutil.which', return_value='/sbin/udevadm'), \
                patch('subprocess.run', return_value=SimpleNamespace(returncode=1, stderr='no daemon')):
            with self.assertRaisesRegex(ValueError, 'Rules saved.*udev could not reload'):
                linux.install()
        self.assertTrue(self.rule.exists())


class LinuxDeviceTests(unittest.TestCase):
    def test_usb_default_and_linux_composite_location(self):
        with patch('hlyflash.cli.sys.platform', 'linux'):
            self.assertEqual(_device_node(SimpleNamespace(node=None)), 'usb')
            self.assertEqual(_device_node(SimpleNamespace(node='/dev/sg3')), '/dev/sg3')
        self.assertEqual(updater._location(SimpleNamespace(location='2-1.3:1.2')), (2, (1, 3)))

    def test_scan_ignores_unrelated_serial_adapters(self):
        ports = [SimpleNamespace(device='/dev/ttyACM0', vid=0x4C4A, pid=0x4155),
                 SimpleNamespace(device='/dev/ttyUSB0', vid=0x0403, pid=0x6001)]
        with patch('serial.tools.list_ports.comports', return_value=ports):
            self.assertEqual(serialinfo.find_ports(), ['/dev/ttyACM0'])

    def test_serial_denial_has_linux_permissions_remedy(self):
        with patch('hlyflash.serialinfo.sys.platform', 'linux'), \
                patch('serial.Serial', side_effect=OSError(errno.EACCES, 'Permission denied')):
            with self.assertRaisesRegex(PermissionError, 'hlyflash setup'):
                serialinfo.query('/dev/ttyACM0')

    def test_by_id_alias_keeps_physical_binding(self):
        with tempfile.TemporaryDirectory() as directory:
            canonical = Path(directory) / 'ttyACM0'
            canonical.touch()
            alias = Path(directory) / 'by-id'
            alias.symlink_to(canonical)
            port = SimpleNamespace(device=str(canonical), location='2-1.3:1.2')
            info = dict(model='Free 2', firmware='V0.22', mac='AA-BB-CC-DD-EE-FF')
            with patch('hlyflash.updater._ports', return_value=[port]), \
                    patch('hlyflash.updater.serialinfo.query', return_value=info) as query:
                device = updater.detect_device(str(alias))
            self.assertEqual(device.location, (2, (1, 3)))
            query.assert_called_once_with(str(canonical))

    def test_storage_mounts_are_scoped_to_exact_usb_port(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sysfs = root / 'sys'
            usb = sysfs / 'devices' / '1-2.3'
            other = sysfs / 'devices' / '1-2.30'
            for device, name, number in ((usb, 'sda1', '8:1'), (other, 'sdb1', '8:17')):
                block = device / 'block' / name
                block.mkdir(parents=True)
                (block / 'dev').write_text(number)
                link = sysfs / 'class' / 'block' / name
                link.parent.mkdir(parents=True, exist_ok=True)
                link.symlink_to(block)
            bus = sysfs / 'bus' / 'usb' / 'devices'
            bus.mkdir(parents=True)
            (bus / '1-2.3').symlink_to(usb)
            mounts = root / 'mountinfo'
            mounts.write_text('42 21 8:1 / /media/Page\\040Turner rw - vfat /dev/sda1 rw\n'
                              '43 21 8:17 / /unrelated rw - vfat /dev/sdb1 rw\n')
            self.assertEqual(linux.mounted_volumes((1, (2, 3)), sysfs, mounts), ['/media/Page Turner'])
            self.assertEqual(linux.mounted_volumes((1, (9,)), sysfs, mounts), [])


class LinuxCaptureTests(unittest.TestCase):
    def context(self, mounts=()):
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        self.device = Mock(bus=1, address=7, port_numbers=(2, 3))
        self.device.is_kernel_driver_active.return_value = True
        self.util = Mock()
        core = SimpleNamespace(USBError=USBError)
        stack.enter_context(patch('hlyflash.usb.sys.platform', 'linux'))
        stack.enter_context(patch('hlyflash.usb._modules', return_value=(core, self.util, None)))
        stack.enter_context(patch('hlyflash.usb._select', return_value=self.device))
        stack.enter_context(patch('hlyflash.usb._interface', return_value=(0, 0x81, 1)))
        stack.enter_context(patch('hlyflash.linux.mounted_volumes', return_value=list(mounts)))

    def test_detaches_and_restores_only_storage_driver(self):
        self.context()
        usb = USBDev()
        self.device.detach_kernel_driver.assert_called_once_with(0)
        self.util.claim_interface.assert_called_once_with(self.device, 0)
        usb.close()
        self.util.release_interface.assert_called_once_with(self.device, 0)
        self.device.attach_kernel_driver.assert_called_once_with(0)
        self.util.dispose_resources.assert_called_once_with(self.device)

    def test_mounted_storage_stops_before_detaching_or_claiming(self):
        self.context(['/media/PageTurner'])
        with self.assertRaisesRegex(SCSIError, 'Unmount'):
            USBDev()
        self.device.detach_kernel_driver.assert_not_called()
        self.util.claim_interface.assert_not_called()

    def test_failed_claim_restores_driver_before_safe_access_error(self):
        self.context()
        self.util.claim_interface.side_effect = USBError(-3)
        with self.assertRaises(USBAccessError):
            USBDev()
        self.device.attach_kernel_driver.assert_called_once_with(0)

    def test_disappearance_after_reenumeration_is_not_a_driver_restore_failure(self):
        self.context()
        usb = USBDev()
        self.util.release_interface.side_effect = USBError(-4)
        self.device.attach_kernel_driver.side_effect = USBError(-4)
        usb.close()
        self.assertIsNone(usb.device)
        self.util.dispose_resources.assert_called_once()

    def test_other_driver_restore_failures_are_reported(self):
        self.context()
        usb = USBDev()
        self.device.attach_kernel_driver.side_effect = USBError(-6)
        with self.assertRaisesRegex(SCSIError, 'driver restoration failed'):
            usb.close()


class LinuxStateTests(unittest.TestCase):
    def test_xdg_data_root_and_invalid_relative_value(self):
        import os
        with patch('hlyflash.updater.sys.platform', 'linux'), \
                patch('hlyflash.updater._sudo_owner', return_value=None), \
                patch('hlyflash.updater.Path.home', return_value=Path('/home/reader')):
            for value, expected in (('/var/reader', '/var/reader/hlyflash'),
                                    ('', '/home/reader/.local/share/hlyflash'),
                                    ('relative', '/home/reader/.local/share/hlyflash')):
                with self.subTest(value=value), patch.dict(os.environ, XDG_DATA_HOME=value):
                    self.assertEqual(updater.data_directory(), Path(expected))

    def test_interrupted_setup_reports_retry_without_traceback(self):
        output = io.StringIO()
        with patch('hlyflash.linux.setup', side_effect=KeyboardInterrupt), contextlib.redirect_stderr(output):
            self.assertEqual(main(['setup']), 130)
        self.assertIn('Run hlyflash setup again', output.getvalue())
        self.assertNotIn('Traceback', output.getvalue())
