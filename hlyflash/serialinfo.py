"""Safe, read-only access to a Hanlinyue page turner in normal (app) mode.

In normal mode the device is a USB CDC serial port speaking line-delimited
JSON at 460800 baud: you send {"o":"get","t":<field>} and it answers
{"o":"getresult","t":<field>,"v":<value>}. This module only ever issues
`get` queries -- it never writes settings or firmware.
"""
from __future__ import annotations

import glob
import json
import sys
import time

BAUD = 460800
# JieLi VID/PID the vendor app matches on (see SerialPortManager).
USB_VID = 0x4C4A
USB_PID = 0x4155
FIELDS = ("model", "firmware", "mac", "battery")


def find_ports() -> list[str]:
    """Best-effort list of candidate serial device paths for this platform."""
    pats = []
    if sys.platform == "darwin":
        pats = ["/dev/cu.usbmodem*"]
    elif sys.platform.startswith("linux"):
        pats = ["/dev/ttyACM*", "/dev/ttyUSB*"]
    else:
        pats = ["COM*"]
    out: list[str] = []
    for p in pats:
        out.extend(sorted(glob.glob(p)))
    return out


def query(port: str, fields=FIELDS, timeout: float = 1.2, rounds: int = 3) -> dict:
    """Query the device for `fields` and return {field: value}.

    The device drops commands sent in the brief warm-up window right after the
    port opens, so we settle, send a throwaway query, then poll each field with
    a generous per-field deadline and retry any that are still missing. Replies
    are matched by their own `t` field, not send order.
    """
    import serial  # pyserial; imported lazily so parsing works without it

    want = set(fields)
    got: dict[str, str] = {}

    s = serial.Serial(port, BAUD, timeout=0.3)
    try:
        time.sleep(0.5)
        s.reset_input_buffer()

        def ask(t: str) -> None:
            """Send one get and read until its reply arrives or `timeout`."""
            s.write(json.dumps({"o": "get", "t": t}).encode())
            s.flush()
            deadline = time.time() + timeout
            buf = b""
            while time.time() < deadline:
                chunk = s.read(256)
                if not chunk:
                    continue
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line)
                    except Exception:
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
