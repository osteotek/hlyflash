import contextlib
import hashlib
import io
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from hlyflash import application, flasher, flashimage
from hlyflash.cli import main
from hlyflash.enc import crc16, enc_bytes
from hlyflash.scsi import SCSIError
from test_flashing import Flash, package, physical_image


KEY = 0x284C


def record(name, offset, size, attr, last=0, checksum=0xFFFF):
    raw = bytearray(struct.pack("<HHIIBBH16s", 0, checksum, offset, size,
                                attr, 0xFF, last, name.encode().ljust(16, b"\0")))
    struct.pack_into("<H", raw, 0, crc16(raw[2:]))
    return bytes(raw)


def image(payload, staged=False, cfg=b"CONFIG", hardware=b"HWCFG", vm=0x9000,
          exif=True, parent_last=0, slot=None):
    """Independent small JLFS fixture with occupied user-data sectors."""
    region_names = [("VM", 0xD000, 0x2000, 0x12), ("PRCT", 0, 0xD000, 0x92),
                    ("BTIF", 0xF000, 0x1000, 0x92)] if staged else [
                        ("VM", vm, 0xE000 - vm, 0x12), ("PRCT", 0, vm, 0x92),
                        ("BTIF", 0xE000, 0x1000, 0x92), ("EXIF", 0xF000, 0x1000, 0x92)]
    if not staged and not exif:
        region_names = [("VM", vm, 0xF000 - vm, 0x12), ("PRCT", 0, vm, 0x92),
                        ("BTIF", 0xF000, 0x1000, 0x92)]
    app_size = 288 + len(payload) + len(cfg)
    tail = record("app.bin", 288, len(payload), 0x82, checksum=crc16(payload))
    tail += record("cfg_tool.bin", 288 + len(payload), len(cfg), 0x82, checksum=crc16(cfg))
    for i, (name, start, length, attr) in enumerate(region_names):
        tail += record(name, start, length, attr, last=int(i == len(region_names) - 1))
    tail = tail.ljust(256, b"\xff") + payload + cfg
    plain = record("app_area_head", 0x1E00120, app_size, 0x83, last=parent_last, checksum=crc16(tail)) + tail
    plain = plain.ljust((len(plain) + 31) // 32 * 32, b"\xff")
    base = 0x1020 if staged else 0x1000
    length = (base + len(plain) + 4095) // 4096 * 4096 if staged else 65536
    out = bytearray(b"\xff" * length)
    out[:32] = physical_image(b"X")[:32]
    bank = bytearray(struct.pack("<HHIIHH", 1, 4, 0x12000, 16, crc16(b"BOOT"), 0))
    struct.pack_into("<H", bank, 14, crc16(bank[:14]))
    boot = enc_bytes(bank, 0xFFFF) + enc_bytes(b"BOOT", 0xFFFF)
    boot_offset = 192 if staged else 160
    out[boot_offset:boot_offset + len(boot)] = boot
    out[boot_offset + len(boot):boot_offset + len(boot) + len(hardware)] = hardware
    entries = [record("uboot.boot", boot_offset, len(boot), 0, checksum=crc16(bytes(bank) + b"BOOT")),
               record("isd_config.ini", boot_offset + len(boot), len(hardware), 2, checksum=crc16(hardware)),
               record("app_dir_head", base, 4096 if staged else 0xFFFFFFFF, 0x81)]
    if staged:
        reserved = len(out) if slot is None else slot
        entries.append(record("app_dir_head2", reserved + 32, reserved, 0x81))
    entries.append(record("key_mac", 0xFFFFFFFF, 0xFFFFFFFF, 0xFF, 0xFFFF))
    for i, entry in enumerate(entries, 1):
        out[i * 32:(i + 1) * 32] = enc_bytes(entry, 0xFFFF)
    for a in range(0, len(plain), 32):
        out[base + a:base + a + 32] = enc_bytes(plain[a:a + 32], KEY ^ (a >> 2))
    if not staged:
        out[vm:0xE000] = bytes(range(256)) * ((0xE000 - vm) // 256)
        out[0xE000:] = b"B" * 4096 + b"E" * 4096
        if not exif:
            out[0xE000:0xF000] = b"V" * 4096
            out[0xF000:] = b"B" * 4096
    return bytes(out)


class ApplicationTests(unittest.TestCase):
    def setUp(self):
        self.old = image(b"OLD" * 4000)
        self.staged = image(b"NEW" * 3000, staged=True)

    def test_code_payload_and_boot_settings_and_every_data_byte_are_preserved(self):
        target = application.transplant(self.staged, self.old, KEY)
        self.assertEqual(target[:4096], self.old[:4096])
        self.assertEqual(target[0x9000:], self.old[0x9000:])
        plain = application._plain(target, 4096, KEY)
        self.assertEqual(plain[288:288 + 9000], b"NEW" * 3000)
        self.assertEqual(plain[288 + 9000:288 + 9006], b"CONFIG")
        self.assertEqual(flashimage.validate(target, KEY, 65536)["regions"],
                         flashimage.validate(self.old, KEY, 65536)["regions"])
        self.assertEqual([r["name"] for r, _ in application._app(plain)][-1], "EXIF")

    def test_free2_layout_parent_directory_reserved_slot_and_no_exif(self):
        old_cfg = b"HARDWARE\x07SPI\x002_3_0_0\x04UPDATE_JUMP\x00" + b"\x00" * 4 + b"TAIL"
        new_cfg = old_cfg.replace(b"\x00" * 4 + b"TAIL", b"\x01\x00\x00\x00TAIL")
        old = image(b"OLD" * 4000, hardware=old_cfg, exif=False, parent_last=1)
        source = image(b"NEW" * 3000, True, hardware=new_cfg, parent_last=1, slot=0x7000)
        target = application.transplant(source, old, KEY)
        layout = flashimage.validate(target, KEY, 65536)
        self.assertEqual(layout["files"], ["app.bin", "cfg_tool.bin"])
        self.assertEqual([r["name"] for r in layout["regions"]], ["VM", "PRCT", "BTIF"])
        self.assertEqual(target[:4096], old[:4096])
        self.assertEqual(target[0x9000:], old[0x9000:])
        plain = application._plain(target, 4096, KEY)
        self.assertEqual(application._app(plain)[0][0]["index"], 1)
        self.assertEqual(plain[288:288 + 9000], b"NEW" * 3000)
        dev = Flash(old)
        flasher._program(dev, target, old, False)
        self.assertEqual(bytes(dev.data), target)
        self.assertNotIn(0, dev.erases)

    def test_update_entry_exception_does_not_allow_hardware_changes(self):
        old_cfg = b"SPI\x002_3_0_0\x04UPDATE_JUMP\x00" + b"\x00" * 4
        old = image(b"OLD" * 4000, hardware=old_cfg)
        for new_cfg in (old_cfg.replace(b"2_3", b"4_3"), old_cfg[:-4] + b"\x02\x00\x00\x00",
                        old_cfg.replace(b"UPDATE_JUMP", b"OTHER_FIELD")):
            with self.subTest(config=new_cfg), self.assertRaisesRegex(SCSIError, "isd_config.ini"):
                application.transplant(image(b"NEW" * 3000, True, hardware=new_cfg), old, KEY)

    def test_reserved_bank_cannot_exceed_flash_or_overlap_existing_payload(self):
        for slot in (0x2000, 0x9000, 0x7100):
            with self.subTest(slot=slot), self.assertRaisesRegex(SCSIError, "staging offsets"):
                application.transplant(image(b"NEW" * 3000, True, slot=slot), self.old, KEY)

    def test_parent_terminator_does_not_hide_a_corrupt_child(self):
        old = bytearray(image(b"OLD" * 4000, parent_last=1))
        old[4096 + 32] ^= 1
        with self.assertRaisesRegex(SCSIError, "file-table CRC"):
            flashimage.validate(old, KEY, len(old))

    def test_rejects_changed_hardware_config_cfg_key_growth_and_corruption(self):
        bad = bytearray(self.staged)
        bad[0x1020 + 1000] ^= 1
        for source, key in ((image(b"NEW" * 3000, True, hardware=b"OTHER"), KEY),
                            (image(b"NEW" * 3000, True, cfg=b"CHANGE"), KEY),
                            (image(b"NEW" * 4500, True), KEY),
                            (self.staged, KEY ^ 1), (bytes(bad), KEY)):
            with self.subTest(digest=hashlib.sha256(source).hexdigest(), key=key), \
                    self.assertRaises(SCSIError):
                application.transplant(source, self.old, key)

    def test_second_application_bank_cannot_be_silently_dropped(self):
        source = bytearray(self.staged)
        source[128:160] = enc_bytes(record("app_dir_head2", 0x2020, len(source), 0x81), 0xFFFF)
        with self.assertRaisesRegex(SCSIError, "additional application bank"):
            application.transplant(source, self.old, KEY)

    def test_preserved_data_cannot_overlap_new_application(self):
        # The installed firmware fits, but a larger replacement must never spill into VM.
        old = image(b"OLD" * 1000, vm=0x3000)
        with self.assertRaisesRegex(SCSIError, "overlap preserved data"):
            application.transplant(self.staged, old, KEY)

    def test_full_cli_backend_dry_run_and_nor_programming(self):
        for dry_run in (True, False):
            dev = Flash(self.old)
            dev.chip_key_value = KEY
            with self.subTest(dry_run=dry_run), tempfile.TemporaryDirectory() as folder, \
                    patch("hlyflash.flasher.open_loader", return_value=dev):
                root = Path(folder)
                (root / "update.ufw").write_bytes(package(self.staged))
                backup = root / "before.bin"
                count = flasher.flash_ufw("usb", root / "update.ufw", backup, progress=False,
                                          dry_run=dry_run)
                self.assertGreater(count, 0)
                self.assertEqual(backup.read_bytes(), self.old)
                target = Path(str(backup) + ".target.bin").read_bytes()
                self.assertEqual(bytes(dev.data), self.old if dry_run else target)
                self.assertEqual(dev.data[0x9000:], self.old[0x9000:])
                self.assertNotIn(0, dev.erases)
                if dry_run:
                    self.assertEqual(dev.erases, [])
                else:
                    self.assertEqual(dev.erases[-1], 4096)

    def test_incompatible_application_never_erases(self):
        dev = Flash(self.old)
        dev.chip_key_value = KEY
        with tempfile.TemporaryDirectory() as folder, patch("hlyflash.flasher.open_loader", return_value=dev):
            root = Path(folder)
            (root / "bad.ufw").write_bytes(package(image(b"NEW" * 3000, True, hardware=b"OTHER")))
            with self.assertRaisesRegex(SCSIError, "identical isd_config.ini"):
                flasher.flash_ufw("usb", root / "bad.ufw", root / "backup.bin", False)
            self.assertEqual(dev.erases, [])
            self.assertEqual(bytes(dev.data), self.old)

    def test_default_rejects_unsupported_staging_without_direct_write_fallback(self):
        source = bytearray(self.staged)
        source[128:160] = enc_bytes(record("app_dir_head2", 0x2020, len(source), 0x81), 0xFFFF)
        dev = Flash(self.old)
        dev.chip_key_value = KEY
        with tempfile.TemporaryDirectory() as folder, patch("hlyflash.flasher.open_loader", return_value=dev):
            root = Path(folder)
            (root / "bad.ufw").write_bytes(package(source))
            with self.assertRaisesRegex(SCSIError, "additional application bank"):
                flasher.flash_ufw("usb", root / "bad.ufw", root / "backup.bin", False)
            self.assertEqual(dev.erases, [])
            self.assertEqual(bytes(dev.data), self.old)

    def test_explicit_direct_mode_cannot_program_staging(self):
        with tempfile.TemporaryDirectory() as folder, patch("hlyflash.flasher.open_loader") as device:
            root = Path(folder)
            (root / "update.ufw").write_bytes(package(self.staged))
            with self.assertRaisesRegex(SCSIError, "direct programming"):
                flasher.flash_ufw("usb", root / "update.ufw", root / "backup.bin", False,
                                  application_only=False)
            device.assert_not_called()

    def test_cli_automatically_converts_staging_and_still_requires_force(self):
        with tempfile.TemporaryDirectory() as folder, \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            root = Path(folder)
            source = root / "update.ufw"
            source.write_bytes(package(self.staged))
            args = ["flash", str(source), "--node", "usb", "--backup", str(root / "backup.bin")]
            dev = Flash(self.old)
            dev.chip_key_value = KEY
            with patch("hlyflash.flasher.open_loader", return_value=dev) as device:
                self.assertEqual(main(args), 2)
                device.assert_not_called()
                self.assertEqual(main(args + ["--force"]), 0)
            target = Path(str(root / "backup.bin") + ".target.bin").read_bytes()
            self.assertEqual(bytes(dev.data), target)
            self.assertEqual(dev.data[:4096], self.old[:4096])
            self.assertEqual(dev.data[0x9000:], self.old[0x9000:])
