import hashlib
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from hlyflash import flasher, ufw, flashimage
from hlyflash.enc import crc16, crc_decode, enc_bytes
from hlyflash.scsi import CommandFailed, SCSIError


def package(image, chip="AC636N", name="flash.bin"):
    record = bytearray(80)
    struct.pack_into("<H", record, 4, crc16(image))
    struct.pack_into("<II", record, 8, 144, len(image))
    record[64:80] = name.encode().ljust(16, b"\0")
    table = enc_bytes(record, 0xFFFF)
    header = bytearray(64)
    struct.pack_into("<HIH", header, 2, crc16(table), 144 + len(image), 1)
    header[16:16 + len(chip)] = chip.encode()
    struct.pack_into("<H", header, 0, crc16(header[2:]))
    return enc_bytes(header, 0xFFFF) + table + image


def physical_image(payload, key=0xFFFF, full=False):
    """Independent, minimal JLFS fixture using the documented on-flash layout."""
    def entry(name, address, size, attr, index, checksum=0xFFFF):
        raw = bytearray(struct.pack("<HHIIBBH16s", 0, checksum, address, size,
                                    attr, 0xFF, index, name.encode().ljust(16, b"\0")))
        struct.pack_into("<H", raw, 0, crc16(raw[2:]))
        return bytes(raw)
    header = bytearray(32)
    struct.pack_into("<I", header, 8, 65536)
    header[12] = 3
    raw_header = bytearray(enc_bytes(header, 0xFFFF))
    raw_header[4:8] = b"0.13"
    raw_header[16:32] = b"AC636N".ljust(16, b"\xff")
    header = bytearray(enc_bytes(raw_header, 0xFFFF))
    struct.pack_into("<H", header, 0, crc16(header[2:]))
    app = (entry("app_area_head", 0x1E00040, len(payload), 0x83, 0, crc16(payload)) +
           entry("app.bin", 64, len(payload), 0x82, 1, crc16(payload)) + payload)
    app = app.ljust((len(app) + 31) // 32 * 32, b"\xff")
    length = 65536 if full else (4096 + len(app) + 4095) // 4096 * 4096
    image = bytearray(b"\xff" * length)
    image[:32] = enc_bytes(header, 0xFFFF)
    bank = bytearray(struct.pack("<HHIIHH", 1, 4, 0x12000, 16, crc16(b"BOOT"), 0))
    struct.pack_into("<H", bank, 14, crc16(bank[:14]))
    boot_data = bytes(bank) + b"BOOT"
    image[128:148] = enc_bytes(bank, 0xFFFF) + enc_bytes(b"BOOT", 0xFFFF)
    boot = [entry("uboot.boot", 128, len(boot_data), 0, 0, crc16(boot_data)),
            entry("app_dir_head", 4096, 0xFFFFFFFF, 0x81, 0),
            entry("key_mac", 0xFFFFFFFF, 0xFFFFFFFF, 0xFF, 0xFFFF)]
    for i, raw in enumerate(boot):
        image[32 + i * 32:64 + i * 32] = enc_bytes(raw, 0xFFFF)
    for offset in range(0, len(app), 32):
        image[4096 + offset:4096 + offset + 32] = enc_bytes(app[offset:offset + 32], key ^ (offset >> 2))
    return bytes(image)


class Flash:
    def __init__(self, contents):
        self.data = bytearray(contents)
        self.family = "BR25"
        self.flash_id = 0xEF4010
        self.flash_size = len(contents)
        self.chip_key_value = 0xFFFF
        self.io_size = 256
        self.erases = []
        self.writes = []
        self.dev = self
        self.closed = False
        self.ram = {}

    def flash_read(self, address, length):
        return bytes(self.data[address:address + length])

    def flash_erase_sector(self, address):
        self.erases.append(address)
        self.data[address:address + 4096] = b"\xff" * 4096

    def flash_write(self, address, data):
        self.writes.append(address)
        assert len(data) <= self.io_size
        for i, byte in enumerate(data):
            assert self.data[address + i] & byte == byte  # actual NOR programming rule
            self.data[address + i] &= byte

    def close(self):
        self.closed = True


class FlashTests(unittest.TestCase):
    def test_program_boot_last_and_preserve_partial_sector_and_tail(self):
        backup = bytes(range(256)) * 256
        dev = Flash(backup)
        image = b"\x35" * 4500
        changed = flasher._program(dev, image, backup, False)
        self.assertEqual(changed, 2)
        self.assertEqual(dev.erases, [4096, 0])
        self.assertEqual(bytes(dev.data), image + backup[len(image):])

    def test_unchanged_image_never_erases(self):
        backup = bytes(range(256)) * 256
        dev = Flash(backup)
        self.assertEqual(flasher._program(dev, backup[:4096], backup, False), 0)
        self.assertEqual(dev.erases, [])

    def test_dry_run_counts_changes_without_writes(self):
        backup = b"X" * 65536
        dev = Flash(backup)
        self.assertEqual(flasher._program(dev, b"Y" * 5000, backup, False, dry_run=True), 2)
        self.assertEqual(dev.erases, [])
        self.assertEqual(dev.writes, [])
        self.assertEqual(bytes(dev.data), backup)

    def test_backup_verifies_twice_and_never_overwrites(self):
        dev = Flash(b"X" * 65536)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "backup.bin"
            digest = flasher._backup(dev, path, len(dev.data), False)
            self.assertEqual(digest, hashlib.sha256(dev.data).hexdigest())
            self.assertEqual(path.read_bytes(), dev.data)
            with self.assertRaises(SCSIError):
                flasher._backup(dev, path, len(dev.data), False)

    def test_unstable_backup_is_not_published(self):
        dev = Flash(b"X" * 65536)
        calls = 0
        original = dev.flash_read
        def unstable(address, length):
            nonlocal calls
            calls += 1
            return original(address, length) if calls <= 256 else b"Y" * length
        dev.flash_read = unstable
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "backup.bin"
            with self.assertRaisesRegex(SCSIError, "second-read mismatch"):
                flasher._backup(dev, path, len(dev.data), False)
            self.assertFalse(path.exists())
            self.assertTrue(Path(str(path) + ".partial").exists())

    def test_full_ufw_flow_and_fail_before_erase(self):
        for chip, key, succeeds in (("AC636N", 0xFFFF, True),
                                     ("AC632N", 0xFFFF, False),
                                     ("AC636N", 0x1234, False)):
            with self.subTest(chip=chip, key=key), tempfile.TemporaryDirectory() as folder:
                p = Path(folder)
                image = physical_image(b"Q" * 5000)
                (p / "image.ufw").write_bytes(package(image, chip))
                dev = Flash(b"X" * 65536)
                dev.chip_key_value = key
                with patch("hlyflash.flasher.open_loader", return_value=dev):
                    if succeeds:
                        flasher.flash_ufw("fake", p / "image.ufw", p / "backup.bin", progress=False)
                        self.assertTrue((p / "backup.bin.json").is_file())
                        self.assertEqual(dev.data[:len(image)], image)
                    else:
                        with self.assertRaises(SCSIError):
                            flasher.flash_ufw("fake", p / "image.ufw", p / "backup.bin", progress=False)
                        self.assertFalse(dev.erases)
                self.assertTrue(dev.closed)

    def test_full_keyed_backup_restore(self):
        original = physical_image(b"R" * 5000, 0x284C, full=True)
        target = physical_image(b"Q" * 5000, 0x284C, full=True)
        dev = Flash(original)
        dev.chip_key_value = 0x284C
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder)
            (p / "original.bin").write_bytes(target)
            with patch("hlyflash.flasher.open_loader", return_value=dev):
                flasher.restore("fake", p / "original.bin", p / "before.bin", progress=False)
            self.assertEqual(bytes(dev.data), target)
            self.assertEqual((p / "before.bin").read_bytes(), original)

    def test_keyed_physical_image_validation_and_wrong_key_rejection(self):
        image = physical_image(b"Q" * 5000, 0x284C, full=True)
        self.assertEqual(flashimage.validate(image, 0x284C, 65536)["files"], ["app.bin"])
        with self.assertRaises(SCSIError):
            flashimage.validate(image, 0xFFFF, 65536)

    def test_staged_image_and_corrupt_bootloader_are_rejected(self):
        image = bytearray(physical_image(b"Q" * 5000))
        record = bytearray(enc_bytes(image[96:128], 0xFFFF))
        record[16:32] = b"app_dir_head2".ljust(16, b"\0")
        struct.pack_into("<H", record, 0, crc16(record[2:]))
        image[96:128] = enc_bytes(record, 0xFFFF)
        with self.assertRaisesRegex(SCSIError, "staged"):
            flashimage.validate(bytes(image), 0xFFFF, 65536)
        image = bytearray(physical_image(b"Q" * 5000))
        image[130] ^= 1
        with self.assertRaisesRegex(SCSIError, "BANKCB"):
            flashimage.validate(bytes(image), 0xFFFF, 65536)

    def test_capacity_rejects_unknown_density(self):
        self.assertEqual(flasher.flash_capacity(0xEF4013), 512 * 1024)
        for value in (0, 0xFFFFFF, 0xEF4001, 0xEF401A):
            with self.assertRaises(SCSIError):
                flasher.flash_capacity(value)

    def test_bad_loader_is_never_uploaded(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "loader.bin"
            path.write_bytes(b"wrong loader")
            with self.assertRaises(SCSIError):
                flasher._bootstrap(None, path, flasher.PROFILES["BR25"])


class ContainerTests(unittest.TestCase):
    def test_valid_package(self):
        u = ufw.parse(package(b"abc"))
        self.assertTrue(u.header_crc_ok and u.table_crc_ok)
        self.assertTrue(u.entry("flash.bin").crc_ok(u.raw))

    def test_size_and_entry_bounds(self):
        raw = package(b"abc")
        for data in (raw + b"extra", raw[:80], raw[:-1]):
            with self.assertRaises(ValueError):
                ufw.parse(data)
        record = bytearray(enc_bytes(raw[64:144], 0xFFFF))
        struct.pack_into("<I", record, 8, 0xFFFFFF)
        with self.assertRaises(ValueError):
            ufw.parse(raw[:64] + enc_bytes(record, 0xFFFF) + raw[144:])

    def test_crc_decode_round_trip(self):
        data = bytes(range(256))
        self.assertEqual(crc_decode(crc_decode(data)), data)


class ProtocolDevice:
    """Model the external CDB protocol, including a ROM with no flash loader."""
    def __init__(self, product="UBOOT1.00", vendor="BR25"):
        self.product, self.vendor = product, vendor
        self.running = product != "UBOOT1.00"
        self.calls = []
        self.closed = False
        self.ram = {}

    def execute(self, cdb, data_out, length):
        self.calls.append((cdb, data_out, length))
        if cdb[0] == 0x12:
            return (b"\0" * 8 + self.vendor.encode().ljust(8, b" ") +
                    self.product.encode().ljust(16, b" ") + b"1.00")
        op = int.from_bytes(cdb[:2], "big")
        reply = bytearray(14)
        if op == 0xFB06:
            self.ram[int.from_bytes(cdb[2:6], "big")] = data_out
            return b""
        if op == 0xFD07:
            return self.ram[int.from_bytes(cdb[2:6], "big")]
        if op == 0xFB08:
            self.running = True
        elif not self.running:
            raise CommandFailed("No flash commands in ROM")
        elif op == 0xFC0A:
            reply[0] = 3
            reply[2:6] = (0xEF4013).to_bytes(4, "little")
        elif op == 0xFC09:
            reply[4:6] = crc_decode(b"\xff\xff")[::-1]
        elif op == 0xFC14:
            reply[:4] = (256).to_bytes(4, "big")
        else:
            raise AssertionError(f"Unexpected opcode {op:#x}")
        return cdb[:2] + reply

    def close(self):
        self.closed = True


class SessionTests(unittest.TestCase):
    def test_rom_bootstrap_then_capability_detection(self):
        wire = ProtocolDevice()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "loader.bin"
            image = b"loader" * 200
            path.write_bytes(image)
            profile = dict(flasher.PROFILES["BR25"], sha256=hashlib.sha256(image).hexdigest())
            with patch.dict(flasher.PROFILES, {"BR25": profile}), \
                    patch("hlyflash.flasher.open_device", return_value=wire), patch("time.sleep"):
                ldr = flasher.open_loader("fake", path)
            uploads = [(cdb, data) for cdb, data, _ in wire.calls if cdb[:2] == b"\xfb\x06"]
            self.assertEqual(b"".join(data for _, data in uploads), image)
            self.assertEqual(int.from_bytes(uploads[0][0][2:6], "big"), 0x12000)
            self.assertEqual(int.from_bytes(uploads[0][0][9:11], "little"),
                             crc16(crc_decode(image[:512])) )
            self.assertEqual(ldr.flash_size, 512 * 1024)
            self.assertEqual(ldr.io_size, 256)
            self.assertEqual(ldr.chip_key_value, 0xFFFF)
            self.assertEqual(wire.calls[1][0][:2], b"\xfb\x06")

    def test_rom_without_loader_cannot_flash_and_closes(self):
        wire = ProtocolDevice(vendor="BD19")
        with patch("hlyflash.flasher.open_device", return_value=wire):
            with self.assertRaisesRegex(SCSIError, "ROM needs"):
                flasher.open_loader("fake")
        self.assertTrue(wire.closed)
        self.assertFalse(any(cdb[0] == 0xFB for cdb, _, _ in wire.calls))

    def test_br25_uses_exact_bundled_loader_without_path(self):
        wire = ProtocolDevice()
        with patch("hlyflash.flasher.open_device", return_value=wire), patch("time.sleep"):
            ldr = flasher.open_loader("fake")
        image = b"".join(data for cdb, data, _ in wire.calls if cdb[:2] == b"\xfb\x06")
        self.assertEqual(len(image), 11264)
        self.assertEqual(hashlib.sha256(image).hexdigest(), flasher.PROFILES["BR25"]["sha256"])
        self.assertEqual(wire.ram[0x12000], image[:512])
        self.assertEqual(ldr.flash_size, 524288)
        self.assertFalse(any(cdb[:2] in (b"\xfb\x01", b"\xfb\x04") for cdb, _, _ in wire.calls))

    def test_missing_or_corrupt_bundled_loader_never_uploads(self):
        for missing in (False, True):
            wire = ProtocolDevice()
            with self.subTest(missing=missing), \
                    patch("hlyflash.flasher.open_device", return_value=wire), \
                    patch("hlyflash.flasher.files") as resource:
                read = resource.return_value.joinpath.return_value.read_bytes
                if missing:
                    read.side_effect = FileNotFoundError("missing")
                else:
                    read.return_value = b"corrupt loader"
                with self.assertRaisesRegex(SCSIError, "missing|hash"):
                    flasher.open_loader("fake")
            self.assertTrue(wire.closed)
            self.assertEqual(len(wire.calls), 1)

    def test_normal_mode_requires_explicit_transition(self):
        wire = ProtocolDevice("UDISK")
        with patch("hlyflash.flasher.open_device", return_value=wire):
            with self.assertRaisesRegex(SCSIError, "enter-boot"):
                flasher.open_loader("fake")
        self.assertEqual(len(wire.calls), 1)  # INQUIRY only

    def test_firmware_that_handles_loader_protocol_is_queried(self):
        wire = ProtocolDevice("UDISK")
        with patch("hlyflash.flasher.open_device", return_value=wire):
            ldr = flasher.open_loader("fake", enter_boot=True)
        self.assertEqual(ldr.product, "UDISK")
        self.assertEqual(ldr.flash_id, 0xEF4013)
        self.assertFalse(any(cdb[0] == 0xFB for cdb, _, _ in wire.calls))

    def test_unknown_family_rejected_before_vendor_commands(self):
        wire = ProtocolDevice(vendor="OTHER")
        with patch("hlyflash.flasher.open_device", return_value=wire):
            with self.assertRaises(SCSIError):
                flasher.open_loader("fake")
        self.assertEqual(len(wire.calls), 1)
        self.assertTrue(wire.closed)
