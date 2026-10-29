import io
import unittest

from hlyflash.terminal import Terminal
from hlyflash import flasher
from test_flashing import Flash


class TTY(io.StringIO):
    def isatty(self):
        return True


class TerminalTests(unittest.TestCase):
    def test_redirected_output_is_ascii_without_animation_or_carriage_returns(self):
        stream = io.StringIO()
        terminal = Terminal(stream)
        terminal.banner()
        with terminal.task("Save backup") as activity:
            activity.progress("read", 1, 2)
            activity.progress("read", 2, 2)
            activity.progress("verify backup", 2, 2)
        terminal.plan("V0.15", "V0.22", "F0AFLJ", 46)
        text = stream.getvalue()
        self.assertTrue(text.isascii())
        self.assertNotIn("\r", text)
        self.assertEqual(text.count("Save backup"), 1)
        self.assertNotIn("100%", text)
        self.assertNotIn("[ OK ]", text)

    def test_interactive_loader_and_bar_clean_up_on_error(self):
        stream = TTY()
        terminal = Terminal(stream)
        with self.assertRaisesRegex(ValueError, "failed"):
            with terminal.task("Check device") as activity:
                activity.progress("read", 1, 2)
                raise ValueError("failed")
        self.assertIn("[⠋] Check device", stream.getvalue())
        self.assertIn("50% Read backup", stream.getvalue())
        self.assertIn("[FAIL] Check device", stream.getvalue())
        self.assertIn("⠋", stream.getvalue())

    def test_ascii_only_terminal_gets_a_supported_spinner(self):
        class AsciiTTY(TTY):
            @property
            def encoding(self):
                return "ascii"
        stream = AsciiTTY()
        with Terminal(stream).task("Connect"):
            pass
        self.assertIn("[|] Connect", stream.getvalue())
        self.assertTrue(stream.getvalue().isascii())

    def test_no_progress_disables_animation_on_a_tty(self):
        stream = TTY()
        with Terminal(stream, no_progress=True).task("Download"):
            pass
        self.assertNotIn("\r", stream.getvalue())
        self.assertIn("[....] Download", stream.getvalue())

    def test_write_progress_counts_changed_sectors_in_programming_order(self):
        dev = Flash(b"X" * 65536)
        events = []
        flasher._program(dev, b"Y" * 4500, bytes(dev.data),
                         lambda verb, done, total: events.append((verb, done, total)))
        writes = [(done, total) for verb, done, total in events if verb == "write"]
        self.assertEqual(writes, [(4096, 8192), (8192, 8192)])

    def test_tty_progress_stays_on_one_transient_line(self):
        stream = TTY()
        with Terminal(stream).task("Prepare") as activity:
            activity.progress("read", 1, 2)
            activity.progress("verify backup", 2, 2)
        self.assertNotIn("\n", stream.getvalue())
        self.assertTrue(stream.getvalue().endswith("\r"))
