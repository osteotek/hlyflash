import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hlyflash import firmware
from hlyflash.cli import main
from test_flashing import package, physical_image


def card(fw_id, title, released):
    return f'''<div class="driver-item" data-firmware="{fw_id}">
      <div class="driver-name"><span>{title}</span>
        <span class="release-time">发布时间：{released}</span></div>
      <div class="label">固件编号：</div><div>{fw_id}</div>
      <div class="label">适用设备：</div><div>PC、手机</div>
      <a href="https://cdn.hanlinyue.com.cn/{fw_id}.ufw">下载</a></div>'''


class FirmwareSelectionTests(unittest.TestCase):
    def test_latest_general_ignores_newer_reader_release_and_id_order(self):
        releases = firmware.parse_variants(
            card("ZOLD", "多设备固件", "2026-10-01") +
            card("ANEW", "多设备固件", "2026-10-22") +
            card("READER", "阅读器固件", "2026-10-23"))
        self.assertEqual(firmware.latest_general(releases).fw_id, "ANEW")
        self.assertEqual(releases[1].devices, "PC、手机")

    def test_ambiguous_or_unlabelled_general_is_never_guessed(self):
        for page in (card("A", "阅读器固件", "2026-10-23"),
                     card("A", "多设备固件", "") + card("B", "多设备固件", "2026-10-22"),
                     card("A", "多设备固件", "2026-10-22") +
                     card("B", "多设备固件", "2026-10-22")):
            with self.subTest(page=page), self.assertRaises(ValueError):
                firmware.latest_general(firmware.parse_variants(page))

    def test_specific_model_generation_wins_contains_match(self):
        self.assertEqual(firmware.resolve_model("Free 2 (2026)"), "free2-2026")

    def test_free2_serial_names_and_generations_choose_the_right_catalog(self):
        for name in ("Free2", "Free 2", "Free02", "F2"):
            with self.subTest(name=name):
                self.assertEqual(firmware.resolve_model(name), "free2")
        for name in ("Free2_2026", "Free22026", "Free 2 (2026)", "Free2（2026）", "Free2 2026 edition"):
            with self.subTest(name=name):
                self.assertEqual(firmware.resolve_model(name), "free2-2026")
        for name in ("Free 2 (2027)", "notfree2", "Free 20"):
            with self.subTest(name=name):
                self.assertIsNone(firmware.resolve_model(name))

    def test_free2_catalog_dates_tooltips_and_alternate_fields(self):
        for raw_date, expected in (("2026-9-5", "2026-09-05"), ("2026.7.08", "2026-07-08")):
            page = card("FREE2", "电纸书阅读器使用固件<span class=\"tooltip\">按此键：上、下</span>", raw_date)
            page = page.replace('<a href=', '<div class="label">按键方式：</div><div>下一页，上一页</div>'
                                '<div class="label">基础说明：</div><div>R：阅读器<br>M：手机</div><a href=')
            v = firmware.parse_variants(page)[0]
            self.assertEqual(v.title, "电纸书阅读器使用固件")
            self.assertEqual(v.released, expected)
            self.assertEqual(v.direction, "下一页，上一页")
            self.assertIn("R：阅读器", v.changelog)

    def test_download_general_and_list_only_and_explicit_port(self):
        release = firmware.parse_variants(card("NEW", "多设备固件", "2026-10-22"))
        raw = package(physical_image(b"Q" * 5000))
        def download(url, dest):
            Path(dest).write_bytes(raw)
            return len(raw)
        with tempfile.TemporaryDirectory() as folder, \
                patch("hlyflash.firmware.list_variants", return_value=release), \
                patch("hlyflash.firmware.download", side_effect=download) as transfer, \
                patch("hlyflash.serialinfo.find_ports") as discover, \
                patch("hlyflash.serialinfo.query", return_value={"model": "Free 3"}) as query, \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            out = Path(folder) / "download.ufw"
            self.assertEqual(main(["download", "--port", "explicit", "--general",
                                   "--out", str(out)]), 0)
            discover.assert_not_called()
            query.assert_called_once_with("explicit", fields=("model",))
            self.assertEqual(out.read_bytes(), raw)
            transfer.reset_mock()
            self.assertEqual(main(["download", "--model", "free3", "--general", "--list"]), 0)
            transfer.assert_not_called()
            self.assertEqual(main(["download", "--model", "free3", "--id", "MISSING"]), 2)
            transfer.assert_not_called()
