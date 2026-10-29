"""Check an installation without accessing device interfaces or requesting sudo."""
from __future__ import annotations

import hashlib
from importlib.resources import files
import platform
from pathlib import Path
import shlex
import sys

from . import __version__
from . import flasher
from .scsi import SCSIError
from .terminal import Terminal


def _serial():
    import serial
    if not callable(getattr(serial, "Serial", None)):
        raise ValueError("a different 'serial' package is shadowing pyserial")
    return f"pyserial {getattr(serial, 'VERSION', 'installed')}"


def _usb():
    from .usb import _modules
    _modules()
    import usb
    return f"PyUSB {usb.__version__}; libusb loaded"


def _loader():
    profile = flasher.PROFILES["BR25"]
    blob = files("hlyflash").joinpath(profile["resource"]).read_bytes()
    if hashlib.sha256(blob).hexdigest() != profile["sha256"]:
        raise ValueError("bundled loader checksum does not match")
    return "Bundled BR25 loader verified"


def _repair():
    prefix = Path(sys.prefix)
    if (prefix / "pipx_metadata.json").is_file():
        return "Reinstall using pipx: pipx reinstall hlyflash"
    if (prefix / "uv-receipt.toml").is_file():
        return "Reinstall from the project directory: uv tool install --reinstall ."
    command = shlex.join([sys.executable, "-m", "pip", "install", "--upgrade", "."])
    return "Reinstall from the project directory: " + command


def run(terminal=None):
    terminal = terminal or Terminal()
    terminal.line(f"hlyflash {__version__} - installation check")
    failures = 0
    if sys.platform != "darwin" and not sys.platform.startswith("linux"):
        terminal.line("  [FAIL] USB flashing is supported on macOS and Linux")
        failures += 1
    terminal.line(f"  [ OK ] Python {platform.python_version()}")
    repair = _repair()
    native = "macOS: brew install libusb" if sys.platform == "darwin" else "Debian/Ubuntu: sudo apt install libusb-1.0-0"
    checks = (
        ("Serial support", _serial, repair),
        ("USB support", _usb, repair + f" If libusb still cannot load, install your system's libusb 1.0 package ({native})."),
        ("RAM loader", _loader, repair),
    )
    for label, check, hint in checks:
        try:
            terminal.line(f"  [ OK ] {check()}")
        except (ImportError, OSError, ValueError, AttributeError, SCSIError) as error:
            failures += 1
            terminal.line(f"  [FAIL] {label}: {error}")
            terminal.line("         " + hint)
    if failures:
        terminal.line("\nFix the items above, then run hlyflash doctor again.")
        return 1
    if sys.platform.startswith("linux"):
        from .linux import RULE_PATH
        if not RULE_PATH.is_file():
            terminal.line("  [INFO] For device permissions, run hlyflash setup once, then unplug/reconnect.")
    terminal.line("\nReady. Connect your device and run hlyflash update.")
    return 0
