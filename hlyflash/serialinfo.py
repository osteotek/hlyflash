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


def query(port: str, fields=FIELDS, timeout: float = 1.2) -> dict:
    import serial  # pyserial; imported lazily so parsing works without it

    s = serial.Serial(port, BAUD, timeout=0.4)
    try:
        time.sleep(0.3)
        s.reset_input_buffer()
        got: dict[str, str] = {}
        for t in fields:
            s.write(json.dumps({"o": "get", "t": t}).encode())
            s.flush()
            deadline = time.time() + timeout
            buf = b""
            while time.time() < deadline:
                chunk = s.read(256)
                if not chunk:
                    continue
                buf += chunk
                for line in buf.split(b"\n"):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line)
                    except Exception:
                        continue
                    if msg.get("t") == t and msg.get("v") is not None:
                        got[t] = msg["v"]
                if t in got:
                    break
            time.sleep(0.1)
        return got
    finally:
        s.close()
