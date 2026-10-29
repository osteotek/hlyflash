import contextlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from hlyflash import firmware, flasher, updater, privilege
from hlyflash.cli import main
from hlyflash.scsi import SCSIError, USBAccessError
from test_application import image, KEY
from test_flashing import Flash, ProtocolDevice, package


def payload(ver, fill=b"N"):
    return (b"\0" + ver.encode() + b"\0").ljust(9000, fill)


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.port = SimpleNamespace(device="/dev/page-turner", location="1-1", vid=0x4C4A, pid=0x4155)
        self.info = {"model": "Free 2", "firmware": "V0.21", "mac": "AA-BB-CC-DD-EE-FF", "battery": "80"}
        self.release = firmware.Variant("READ22", "https://cdn.hanlinyue.com.cn/READ22.ufw",
                                        "Android / Xteink", "up/down", "", "阅读器固件", "2026-10-21")
        self.dev = Flash(image(payload("V0.21", b"O")))
        self.dev.chip_key_value = KEY
        self.source = package(image(payload("V0.22"), staged=True))

    @contextlib.contextmanager
    def run_context(self, serial=None, interactive=False):
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(contextlib.redirect_stdout(output))
            stack.enter_context(contextlib.redirect_stderr(errors))
            stack.enter_context(patch("hlyflash.updater._ports", return_value=[self.port]))
            query = stack.enter_context(patch("hlyflash.updater.serialinfo.query",
                                             side_effect=serial or [self.info]))
            fetch = stack.enter_context(patch("hlyflash.updater.firmware._fetch", return_value=self.source))
            stack.enter_context(patch("hlyflash.updater.firmware.list_variants", return_value=[self.release]))
            loader = stack.enter_context(patch("hlyflash.updater.flasher.open_loader", return_value=self.dev))
            stack.enter_context(patch("hlyflash.updater.os.geteuid", return_value=0, create=True))
            stack.enter_context(patch("hlyflash.updater._sudo_owner", return_value=None))
            stack.enter_context(patch("hlyflash.updater.sys.stdin.isatty", return_value=interactive))
            yield output, errors, query, fetch, loader

    def command(self, *extra):
        return ["update", "--id", "READ22", "--data-dir", str(self.root), *extra]

    def receipt(self):
        return json.loads(next((self.root / "devices").glob("*.json")).read_text())

    def test_automatic_backup_programming_and_post_boot_identity_verification(self):
        new_info = dict(self.info, firmware="V0.22")
        original = bytes(self.dev.data)
        with self.run_context(serial=[self.info, new_info]) as (output, errors, query, fetch, loader):
            self.assertEqual(main(self.command("--yes")), 0, errors.getvalue())
            loader.assert_called_once_with("usb", None, enter_boot=True, expected_location=(1, (1,)))
            self.assertEqual(query.call_count, 2)
        backup = next((self.root / "backups").rglob("backup.bin"))
        self.assertEqual(backup.read_bytes(), original)
        self.assertEqual(self.dev.data[:4096], original[:4096])
        self.assertEqual(self.dev.data[0x9000:], original[0x9000:])
        self.assertTrue(self.dev.closed)
        self.assertEqual(self.receipt()["status"], "boot_verified")
        self.assertIn("Updated Free 2 to V0.22", output.getvalue())

    def test_exact_selected_release_is_skipped_without_confirmation_or_programming(self):
        self.dev = Flash(image(payload("V0.22")))
        self.dev.chip_key_value = KEY
        self.info["firmware"] = "V0.22"
        with self.run_context() as (output, errors, *_):
            self.assertEqual(main(self.command()), 0, errors.getvalue())
        self.assertIn("Already up to date", output.getvalue())
        self.assertEqual(self.receipt()["status"], "up_to_date")
        self.assertFalse(self.dev.erases)
        self.assertFalse((self.root / "backups").exists())

    def test_equal_version_different_variant_is_not_mistaken_for_latest(self):
        self.dev = Flash(image(payload("V0.22", b"O")))
        self.dev.chip_key_value = KEY
        self.info["firmware"] = "V0.22"
        with self.run_context() as (output, errors, *_):
            self.assertEqual(main(self.command("--check")), 0, errors.getvalue())
        self.assertIn("different application bytes", output.getvalue())
        self.assertEqual(self.receipt()["status"], "update_available")
        self.assertFalse(self.dev.erases)

    def test_numeric_version_downgrade_is_refused_even_with_yes(self):
        self.dev = Flash(image(payload("V0.30", b"O")))
        self.dev.chip_key_value = KEY
        self.info["firmware"] = "V0.30"
        with self.run_context() as (output, errors, *_):
            self.assertEqual(main(self.command("--yes")), 0, errors.getvalue())
        self.assertIn("downgrade refused", output.getvalue())
        self.assertFalse(self.dev.erases)
        self.assertEqual(self.receipt()["status"], "downgrade_refused")
        self.assertLess(updater.version("V0.9"), updater.version("V0.22"))

    def test_check_compares_in_memory_without_creating_backup_or_writing(self):
        original = bytes(self.dev.data)
        with self.run_context() as (_, errors, *_):
            self.assertEqual(main(self.command("--check", "--yes")), 0, errors.getvalue())
        self.assertEqual(self.receipt()["status"], "update_available")
        self.assertFalse((self.root / "backups").exists())
        self.assertEqual(bytes(self.dev.data), original)
        self.assertFalse(self.dev.erases)

    def test_ascii_check_output_is_concise_without_protocol_details(self):
        with self.run_context() as (output, errors, *_):
            self.assertEqual(main(self.command("--check")), 0, errors.getvalue())
        text = output.getvalue()
        self.assertTrue(text.isascii())
        self.assertNotIn("\r", text)
        self.assertIn("Update available", text)
        self.assertNotIn("+---", text)
        self.assertNotIn("| Item", text)
        self.assertEqual(text.count("Check installed firmware"), 1)
        self.assertNotIn("100%", text)
        self.assertNotIn("Application-only conversion validated", text)
        self.assertFalse((self.root / "backups").exists())

    def test_declining_after_preparation_retains_backup_and_does_not_program(self):
        with self.run_context(interactive=True) as (_, errors, *_), patch("builtins.input", return_value="n"):
            self.assertEqual(main(self.command()), 0, errors.getvalue())
        self.assertEqual(self.receipt()["status"], "declined")
        self.assertFalse(self.dev.erases)

    def test_interactive_variant_selection_and_confirmation(self):
        args = ["update", "--data-dir", str(self.root)]
        new_info = dict(self.info, firmware="V0.22")
        with self.run_context(serial=[self.info, new_info], interactive=True) as (_, errors, *_), \
                patch("builtins.input", side_effect=["invalid", "1", "yes"]):
            self.assertEqual(main(args), 0, errors.getvalue())
        self.assertEqual(self.receipt()["firmware_id"], "READ22")
        self.assertTrue(self.dev.erases)

    def test_noninteractive_selection_and_variant_cancellation_never_download(self):
        for interactive, expected in ((False, 1), (True, 0)):
            with self.subTest(interactive=interactive), self.run_context(interactive=interactive) as (
                    _, errors, _, fetch, loader), patch("builtins.input", return_value="q"):
                self.assertEqual(main(["update", "--data-dir", str(self.root)]), expected, errors.getvalue())
                fetch.assert_not_called()
                loader.assert_not_called()

    def test_low_battery_wrong_model_or_unknown_version_prevent_download(self):
        for change, extra in (({"battery": "19"}, []), ({}, ["--model", "free3"]),
                              ({"firmware": "unknown"}, [])):
            with self.subTest(change=change):
                saved = dict(self.info)
                self.info.update(change)
                with self.run_context() as (_, errors, _, fetch, loader):
                    self.assertEqual(main(self.command("--yes", *extra)), 1)
                    fetch.assert_not_called()
                    loader.assert_not_called()
                self.info = saved

    def test_serial_flash_version_disagreement_or_ambiguous_release_never_program(self):
        for corrupt in (False, True):
            with self.subTest(ambiguous=corrupt):
                if corrupt:
                    self.source = package(image(payload("V0.22") + b"\0V0.23\0", staged=True))
                    self.info["firmware"] = "V0.21"
                else:
                    self.info["firmware"] = "V0.20"
                with self.run_context() as (_, errors, *_):
                    self.assertEqual(main(self.command("--yes")), 1)
                self.assertFalse(self.dev.erases)

    def test_corrupt_container_stops_before_usb_or_backup(self):
        self.source = self.source[:-1]
        with self.run_context() as (_, errors, _, _, loader):
            self.assertEqual(main(self.command("--yes")), 1)
            loader.assert_not_called()
        self.assertFalse((self.root / "backups").exists())

    def test_backup_changed_during_confirmation_prevents_first_erase(self):
        def damage_backup(_prompt):
            next((self.root / "backups").rglob("backup.bin")).write_bytes(b"changed")
            return "yes"
        with self.run_context(interactive=True) as (_, errors, *_), patch("builtins.input", side_effect=damage_backup):
            self.assertEqual(main(self.command()), 1)
        self.assertIn("backup changed", errors.getvalue())
        self.assertFalse(self.dev.erases)
        self.assertFalse(self.receipt()["write_started"])
        self.assertIn("Device flash was not modified", errors.getvalue())
        self.assertNotIn(" restore ", errors.getvalue())

    def test_failed_boot_verification_is_not_reported_as_success(self):
        with self.run_context(serial=[self.info, self.info]) as (_, errors, *_):
            self.assertEqual(main(self.command("--yes")), 1)
        self.assertTrue(self.dev.erases)
        self.assertEqual(self.receipt()["status"], "boot_unverified")
        self.assertIn("expected V0.22", errors.getvalue())
        self.assertIn("Full flash readback passed", errors.getvalue())
        self.assertIn(" restore ", errors.getvalue())
        self.assertIn("--backup", errors.getvalue())

    def test_multiple_devices_and_busy_port_are_actionable(self):
        other = SimpleNamespace(device="/dev/another", location="1-2", vid=0x4C4A, pid=0x4155)
        with patch("hlyflash.updater._ports", return_value=[self.port, other]):
            with self.assertRaisesRegex(ValueError, "Multiple devices"):
                updater.detect_device()
        with patch("hlyflash.updater._ports", return_value=[self.port]), \
                patch("hlyflash.updater.serialinfo.query", side_effect=OSError("Resource busy")):
            with self.assertRaisesRegex(ValueError, "Close any app"):
                updater.detect_device()

    def test_usb_port_mismatch_stops_before_inquiry_or_boot_transition(self):
        wire = ProtocolDevice()
        wire.location = (1, (2,))
        with patch("hlyflash.flasher.open_device", return_value=wire):
            with self.assertRaisesRegex(SCSIError, "does not match"):
                flasher.open_loader("usb", enter_boot=True, expected_location=(1, (1,)))
        self.assertTrue(wire.closed)
        self.assertEqual(wire.calls, [])

    def test_menu_translates_known_targets_without_inventing_xteink_support(self):
        self.assertEqual(updater._devices_text("安卓电纸书"), "Android e-readers")
        self.assertEqual(updater._devices_text("手机、平板、阅星曈"), "Phone, Tablet, Xteink")
        translated = updater._devices_text("安卓电纸书、kindle设备（2024年至2026年，需把固件更新至5.20.1版本）")
        self.assertIn("Kindle (2024–2026; system 5.20.1)", translated)
        self.assertEqual(updater._buttons_text("下一页、上一页/休眠"), "Next / Previous / Sleep")

    def test_repeated_checks_do_not_accumulate_backups(self):
        for _ in range(2):
            self.dev.closed = False
            with self.run_context() as (_, errors, *_):
                self.assertEqual(main(self.command("--check")), 0, errors.getvalue())
        paths = list((self.root / "backups").rglob("backup.bin"))
        self.assertEqual(paths, [])
        self.assertEqual(self.receipt()["status"], "update_available")

    def test_remembers_variant_and_choose_reopens_menu(self):
        with self.run_context() as (_, errors, *_):
            self.assertEqual(main(self.command("--check")), 0, errors.getvalue())
        args = ["update", "--data-dir", str(self.root), "--check"]
        with self.run_context() as (output, errors, *_), patch("builtins.input") as prompt:
            self.assertEqual(main(args), 0, errors.getvalue())
            prompt.assert_not_called()
        self.assertNotIn("Choose firmware", output.getvalue())
        with self.run_context(interactive=True) as (output, errors, *_), patch("builtins.input", return_value="q"):
            self.assertEqual(main([*args, "--choose"]), 0, errors.getvalue())
        self.assertIn("Choose firmware", output.getvalue())

    def test_remembered_variant_is_scoped_to_device_and_replacement_is_unambiguous(self):
        updater.remember_variant(self.root, updater.Device(self.port.device, (1, (1,)), self.info, "free2"), self.release)
        device = updater.Device(self.port.device, (1, (1,)), self.info, "free2")
        replacement = firmware.Variant("NEW", self.release.url, self.release.devices,
                                       self.release.direction, "", self.release.title, "2026-10-29")
        self.assertEqual(updater.remembered_variant(self.root, device, [replacement]), replacement)
        other = updater.Device(device.port, device.location, dict(self.info, mac="00-11-22-33-44-55"), "free2")
        self.assertIsNone(updater.remembered_variant(self.root, other, [replacement]))
        from dataclasses import replace
        self.assertIsNone(updater.remembered_variant(self.root, device, [replacement, replace(replacement, fw_id="AMBIGUOUS")]))

    def test_failed_first_erase_reports_write_attempt_and_valid_recovery_command(self):
        with self.run_context() as (_, errors, *_), patch.object(self.dev, "flash_erase_sector", side_effect=SCSIError("erase failed")):
            self.assertEqual(main(self.command("--yes")), 1)
        self.assertTrue(self.receipt()["write_started"])
        self.assertIn("Flash writes started", errors.getvalue())
        self.assertIn(" restore ", errors.getvalue())
        self.assertIn("--force", errors.getvalue())
        backup = next((self.root / "backups").rglob("backup.bin"))
        self.assertEqual(backup.read_bytes(), bytes(self.dev.data))

    def test_changing_flash_between_precheck_and_backup_stops_before_erase(self):
        prepare = flasher.prepare_flash
        def changed(*args, **kwargs):
            self.dev.data[-100] ^= 1
            return prepare(*args, **kwargs)
        with self.run_context() as (_, errors, *_), patch("hlyflash.updater.flasher.prepare_flash", side_effect=changed):
            self.assertEqual(main(self.command("--yes")), 1)
        self.assertIn("changed during preparation", errors.getvalue())
        self.assertFalse(self.dev.erases)

    def test_mac_nonroot_handoff_occurs_after_verified_download_before_usb(self):
        with self.run_context() as (_, errors, _, fetch, loader), \
                patch("hlyflash.updater.sys.platform", "darwin"), \
                patch("hlyflash.updater.os.geteuid", return_value=501), \
                patch("hlyflash.privilege.elevate", return_value=0) as elevate:
            self.assertEqual(main(self.command("--check")), 0, errors.getvalue())
            fetch.assert_called_once()
            loader.assert_not_called()
            elevate.assert_called_once()
            self.assertTrue(elevate.call_args.args[3].is_file())

    def test_late_usb_access_denial_elevates_but_transport_failure_is_not_replayed(self):
        for error, count in ((USBAccessError("denied"), 1), (SCSIError("USB timeout"), 0)):
            with self.run_context() as (_, errors, _, _, loader), \
                    patch("hlyflash.updater.sys.platform", "linux"), \
                    patch("hlyflash.updater.os.geteuid", return_value=501), \
                    patch("hlyflash.privilege.elevate", return_value=0) as elevate:
                loader.side_effect = error
                self.assertEqual(main(self.command("--check")), 0 if count else 1, errors.getvalue())
                self.assertEqual(elevate.call_count, count)
                loader.assert_called_once()

    def test_denied_sudo_cleans_private_job_without_usb_access(self):
        device = updater.Device(self.port.device, (1, (1,)), self.info, "free2")
        cached = self.root / "firmware.ufw"
        cached.write_bytes(self.source)
        args = SimpleNamespace(check=True, yes=False, no_progress=True, node=None, loader=None)
        with patch("hlyflash.privilege.subprocess.run", return_value=SimpleNamespace(returncode=1)), \
                patch("hlyflash.privilege.os.execvpe") as execute:
            with self.assertRaisesRegex(ValueError, "permission was not granted"):
                privilege.elevate(args, device, self.release, cached, self.root, updater.Terminal(io.StringIO()))
            execute.assert_not_called()
        self.assertEqual(list((self.root / ".jobs").iterdir()), [])

    def test_helper_revalidates_identity_and_cached_bytes_before_opening_usb(self):
        from dataclasses import asdict
        device = updater.Device(self.port.device, (1, (1,)), self.info, "free2")
        cached = self.root / "firmware.ufw"
        cached.write_bytes(self.source)
        import hashlib
        job = self.root / "job.json"
        updater._save_json(job, dict(device=asdict(device), variant=asdict(self.release),
                                   download=str(cached), sha256=hashlib.sha256(self.source).hexdigest(),
                                   root=str(self.root), args=dict(check=True, yes=False, no_progress=True, node=None, loader=None)))
        with self.run_context() as (_, errors, _, _, loader), patch("hlyflash.privilege._load_job", return_value=json.loads(job.read_text())):
            cached.write_bytes(b"modified")
            with self.assertRaisesRegex(ValueError, "firmware changed"):
                privilege.resume(job)
            loader.assert_not_called()
        with self.run_context(serial=[dict(self.info, mac="00-11-22-33-44-55")]) as (_, errors, _, _, loader), \
                patch("hlyflash.privilege._load_job", return_value=json.loads(job.read_text())):
            with self.assertRaisesRegex(ValueError, "device changed"):
                privilege.resume(job)
            loader.assert_not_called()

    def test_successful_old_receipt_seeds_choice_for_same_device_only(self):
        folder = self.root / 'backups' / 'old'
        folder.mkdir(parents=True)
        (folder / 'backup.bin.json').write_text(json.dumps(dict(
            model='free2', device_mac=self.info['mac'], status='up_to_date', firmware_id='READ22')))
        device = updater.Device(self.port.device, (1, (1,)), self.info, 'free2')
        self.assertEqual(updater.remembered_variant(self.root, device, [self.release]), self.release)
        (folder / 'backup.bin.json').write_text(json.dumps(dict(
            model='free2', device_mac=self.info['mac'], status='failed', firmware_id='READ22')))
        self.assertIsNone(updater.remembered_variant(self.root, device, [self.release]))

    def test_privilege_handoff_preserves_venv_and_paths_without_shell_interpolation(self):
        device = updater.Device(self.port.device, (1, (1,)), self.info, 'free2')
        cached = self.root / 'firmware with spaces.ufw'
        cached.write_bytes(self.source)
        args = SimpleNamespace(check=True, yes=False, no_progress=True, node=None, loader=None)
        def execute(file, argv, env):
            self.assertEqual(file, 'sudo')
            self.assertEqual(argv[:3], ['sudo', '-n', '--'])
            self.assertEqual(argv[3], updater.sys.executable)
            self.assertEqual(argv[4:7], ['-I', '-c', privilege._BOOTSTRAP])
            self.assertIn(str(Path(privilege.__file__).absolute().parent.parent), json.loads(argv[7]))
            self.assertEqual(argv[8], '--job')
            job = Path(argv[9])
            self.assertEqual(job.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(job.read_text())['download'], str(cached))
            raise SystemExit(0)
        with patch('hlyflash.privilege.subprocess.run', return_value=SimpleNamespace(returncode=0)) as auth, \
                patch('hlyflash.privilege.os.execvpe', side_effect=execute):
            with self.assertRaises(SystemExit):
                privilege.elevate(args, device, self.release, cached, self.root, updater.Terminal(io.StringIO()))
            auth.assert_called_once_with(['sudo', '-v'], check=False)
        self.assertEqual(list((self.root / '.jobs').iterdir()), [])

    def test_privileged_helper_check_from_private_job_skips_network_and_backup(self):
        from dataclasses import asdict
        import hashlib
        import os
        device = updater.Device(self.port.device, (1, (1,)), self.info, 'free2')
        folder = self.root / '.jobs' / 'update-test'
        folder.mkdir(parents=True)
        cached = self.root / 'firmware.ufw'
        cached.write_bytes(self.source)
        job = folder / 'job.json'
        updater._save_json(job, dict(device=asdict(device), variant=asdict(self.release),
                                   download=str(cached), sha256=hashlib.sha256(self.source).hexdigest(),
                                   root=str(self.root), args=dict(check=True, yes=False, no_progress=True, node=None, loader=None)))
        with self.run_context() as (_, errors, query, fetch, loader), patch.dict(os.environ, SUDO_UID=str(os.getuid())):
            self.assertEqual(privilege.main(['--job', str(job)]), 0, errors.getvalue())
            query.assert_called_once()
            fetch.assert_not_called()
            loader.assert_called_once()
        self.assertFalse(job.exists())
        self.assertFalse(folder.exists())
        self.assertFalse((self.root / 'backups').exists())
        self.assertEqual(self.receipt()['status'], 'update_available')

    def test_helper_refuses_world_readable_job_before_serial_or_usb(self):
        job = self.root / 'job.json'
        job.write_text('{}')
        job.chmod(0o644)
        with self.run_context() as (_, errors, query, _, loader):
            self.assertEqual(privilege.main(['--job', str(job)]), 1)
            query.assert_not_called()
            loader.assert_not_called()
        self.assertTrue(job.exists())
        self.assertIn('Device flash was not modified', errors.getvalue())

    def test_privilege_helper_retains_active_user_site_but_not_arbitrary_pythonpath(self):
        with patch('hlyflash.privilege.site.getusersitepackages', return_value='/user/packages'), \
                patch('hlyflash.privilege.sys.path', ['/user/packages', '/unrelated/pythonpath']):
            command = privilege._command(Path('/private/job.json'))
        self.assertEqual(json.loads(command[7])[-1], '/user/packages')
        self.assertNotIn('/unrelated/pythonpath', json.loads(command[7]))

    def test_missing_usb_backend_is_diagnosed_before_sudo_prompt(self):
        with self.run_context() as (_, errors, _, _, loader), \
                patch('hlyflash.updater.sys.platform', 'darwin'), \
                patch('hlyflash.updater.os.geteuid', return_value=501), \
                patch('hlyflash.usb._modules', side_effect=SCSIError('Run hlyflash doctor')), \
                patch('hlyflash.privilege.elevate') as elevate:
            self.assertEqual(main(self.command('--check')), 1)
            elevate.assert_not_called()
            loader.assert_not_called()
        self.assertIn('hlyflash doctor', errors.getvalue())
