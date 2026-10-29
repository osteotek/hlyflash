"""Safe, read-only access to a Hanlinyue page turner in normal (app) mode.

In normal mode the device is a USB CDC serial port speaking a stream of
JSON at 460800 baud: you send {"o":"get","t":<field>} and it answers
{"o":"getresult","t":<field>,"v":<value>}. This module only ever issues
`get` queries -- it never writes settings or firmware.
"""
from __future__ import annotations

import codecs
import errno
import json
import sys
import time

BAUD = 460800
# JieLi VID/PID the vendor app matches on (see SerialPortManager).
USB_VID = 0x4C4A
USB_PID = 0x4155
FIELDS = ("model", "firmware", "mac", "battery")


class _JSONStream:
    """Decode complete JSON values, with or without newline separators."""

    def __init__(self):
        self.buffer = ""
        self.text_decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.json_decoder = json.JSONDecoder()

    def feed(self, chunk: bytes):
        self.buffer += self.text_decoder.decode(chunk)
        while self.buffer:
            self.buffer = self.buffer.lstrip()
            if not self.buffer:
                break
            try:
                msg, end = self.json_decoder.raw_decode(self.buffer)
            except json.JSONDecodeError:
                if "\n" in self.buffer:
                    _, self.buffer = self.buffer.split("\n", 1)
                    continue
                break  # incomplete value; retain it for the next read
            self.buffer = self.buffer[end:]
            yield msg


def find_ports() -> list[str]:
    """Find page turners without probing unrelated serial devices."""
    from serial.tools import list_ports
    return sorted({port.device for port in list_ports.comports()
                   if (port.vid, port.pid) == (USB_VID, USB_PID)})


def query(port: str, fields=FIELDS, timeout: float = 1.2, rounds: int = 3) -> dict:
    """Query the device for `fields` and return {field: value}.

    The device drops commands sent in the brief warm-up window right after the
    port opens, so we settle, send a throwaway query, then poll each field with
    a generous per-field deadline and retry any that are still missing. Replies
    are matched by their own `t` field, not send order.
    """
    import serial  # pyserial; imported lazily so parsing works without it

    fields = tuple(fields)
    if not fields:
        return {}
    want = set(fields)
    got: dict[str, str] = {}
    stream = _JSONStream()

    try:
        s = serial.Serial(port, BAUD, timeout=0.3)
    except OSError as error:
        if sys.platform.startswith("linux") and error.errno in (errno.EACCES, errno.EPERM):
            raise PermissionError(f"Cannot open {port}: permission denied. Run hlyflash setup, "
                                  "unplug/reconnect the device, then retry. For SSH use setup --group GROUP") from error
        raise
    try:
        time.sleep(0.5)
        s.reset_input_buffer()

        def ask(t: str) -> None:
            """Send one get and read until its reply arrives or `timeout`."""
            s.write(json.dumps({"o": "get", "t": t}).encode())
            s.flush()
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                chunk = s.read(256)
                if not chunk:
                    continue
                for msg in stream.feed(chunk):
                    if not isinstance(msg, dict) or msg.get("o") != "getresult":
                        continue
                    mt = msg.get("t")
                    if mt in want and msg.get("v") is not None:
                        got[mt] = msg["v"]
                if t in got:
                    return
            time.sleep(0.15)  # pace commands; the device drops back-to-back ones

        ask(fields[-1])  # throwaway: flush the device's warm-up drop window
        for _ in range(rounds):
            for t in fields:
                if t not in got:
                    ask(t)
            if want <= got.keys():
                break
        return got
    finally:
        s.close()
