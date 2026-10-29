"""Concise terminal output with Braille spinners and progress bars."""
from __future__ import annotations

from contextlib import contextmanager
import shutil
import sys
import textwrap
import threading
import time


def _ascii_punctuation(value):
    return str(value).translate(str.maketrans({"—": "-", "–": "-", "…": "...", "→": "->"}))


class Terminal:
    def __init__(self, stream=None, no_progress=False):
        self.stream = stream if stream is not None else sys.stdout
        self.animate = bool(self.stream.isatty()) and not no_progress
        self.width = max(60, min(100, shutil.get_terminal_size((100, 24)).columns))
        self.spinner_frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        try:
            self.spinner_frames.encode(getattr(self.stream, "encoding", None) or "utf-8")
        except (LookupError, UnicodeEncodeError):
            self.spinner_frames = "|/-\\"

    def line(self, text=""):
        print(_ascii_punctuation(text), file=self.stream, flush=True)

    def banner(self):
        self.line("hlyflash update\n")

    def result(self, title, lines, mark=" OK "):
        self.line(f"\n  [{mark}] {title}")
        for line in lines:
            for text in textwrap.wrap(_ascii_punctuation(line), self.width - 4,
                                      break_long_words=False, break_on_hyphens=False):
                self.line("    " + text)

    def info(self, name, value):
        # Keep file paths intact so they can be copied directly.
        self.line(f"  {name}: {value}")

    def variants(self, variants):
        self.line("\nChoose firmware:")
        for i, (item, label, devices, buttons) in enumerate(variants, 1):
            self.line(f"  {i}. {label} - {item.fw_id}" + (f" ({item.released})" if item.released else ""))
            detail = devices + (f" | Buttons: {buttons}" if buttons else "")
            for line in textwrap.wrap(_ascii_punctuation(detail), self.width - 5,
                                      break_long_words=False, break_on_hyphens=False):
                self.line("     " + line)

    def plan(self, installed, selected, fw_id, sectors):
        self.line(f"\n  {installed} -> {selected} ({fw_id})")
        self.line(f"  {sectors} sectors to change. Bootloader and settings preserved.")

    @contextmanager
    def task(self, label):
        activity = Activity(self, label)
        activity.start()
        try:
            yield activity
        except BaseException:
            activity.finish(False)
            raise
        else:
            activity.finish(True)


class Activity:
    def __init__(self, terminal, label):
        self.terminal = terminal
        self.label = _ascii_punctuation(label)
        self.started = time.monotonic()
        self.event = threading.Event()
        self.lock = threading.Lock()
        self.thread = None
        self.frame = 0
        self.last_width = 0
        self.phase = None
        self.done = self.total = 0

    def _render(self):
        if self.phase is not None:
            percent = min(100, self.done * 100 // max(1, self.total))
            filled = percent * 24 // 100
            text = f"  [{('#' * filled).ljust(24, '-')}] {percent:3d}% {self.phase}"
        else:
            frames = self.terminal.spinner_frames
            text = f"  [{frames[self.frame % len(frames)]}] {self.label}"
        self.frame += 1
        self.terminal.stream.write("\r" + text + " " * max(0, self.last_width - len(text)))
        self.terminal.stream.flush()
        self.last_width = len(text)

    def start(self):
        if not self.terminal.animate:
            self.terminal.line(f"  [....] {self.label}")
            return
        with self.lock:
            self._render()
        def animate():
            while not self.event.wait(0.1):
                with self.lock:
                    self._render()
        self.thread = threading.Thread(target=animate, daemon=True)
        self.thread.start()

    def progress(self, verb, done, total):
        labels = {"check": "Check installed application", "read": "Read backup", "verify backup": "Verify backup",
                  "write": "Write application", "verify flash": "Verify full flash"}
        phase = labels.get(verb, verb)
        with self.lock:
            self.phase, self.done, self.total = phase, done, total
            if self.terminal.animate:
                self._render()

    def finish(self, success):
        self.event.set()
        if self.thread is not None:
            self.thread.join()
        with self.lock:
            if self.terminal.animate:
                self.terminal.stream.write("\r" + " " * self.last_width + "\r")
                self.terminal.stream.flush()
            if not success:
                self.terminal.line(f"  [FAIL] {self.label}")
