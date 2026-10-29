"""Guided, variant-aware firmware updates with automatic verified backups."""
from __future__ import annotations

from dataclasses import dataclass
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shlex
import secrets
import sys
import tempfile
import time

from . import application, firmware, flasher, serialinfo, ufw
from .scsi import SCSIError, USBAccessError
from .terminal import Terminal


@dataclass
class Device:
    port: str
    location: tuple
    info: dict
    model: str


def version(value):
    match = re.fullmatch(r"[vV]?(\d+(?:\.\d+){1,3})", str(value).strip())
    if not match:
        raise ValueError(f"Cannot compare firmware version {value!r}")
    parts = list(map(int, match.group(1).split(".")))
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def _mac(value):
    result = re.sub(r"[:-]", "", str(value)).upper()
    if not re.fullmatch(r"[0-9A-F]{12}", result):
        raise ValueError("Cannot read a valid device MAC; reconnect the device and retry")
    return result


def _ports():
    try:
        from serial.tools import list_ports
    except ImportError as e:
        raise ValueError("pyserial is missing. Run hlyflash doctor for installation help") from e
    return [p for p in list_ports.comports()
            if (p.vid, p.pid) == (serialinfo.USB_VID, serialinfo.USB_PID)]


def _location(port):
    match = re.fullmatch(r"(\d+)-(\d+(?:\.\d+)*)(?::.*)?", port.location or "")
    if not match:
        raise ValueError("Cannot bind this serial port to its USB device; update requires a known USB location")
    return int(match[1]), tuple(map(int, match[2].split(".")))


def detect_device(port=None, model=None):
    candidates = [p for p in _ports() if port is None or
                  os.path.realpath(p.device) == os.path.realpath(port)]
    if not candidates:
        raise ValueError("No Hanlinyue USB serial device found. Connect it, switch it on, and close the vendor app")
    if len(candidates) != 1:
        raise ValueError("Multiple devices connected. Select one with --port: " +
                         ", ".join(p.device for p in candidates))
    selected = candidates[0]
    location = _location(selected)
    try:
        info = serialinfo.query(selected.device)
    except PermissionError as e:
        raise ValueError(str(e)) from e
    except Exception as e:
        raise ValueError(f"Cannot read {selected.device}: {e}. Close any app or browser tab using the device") from e
    if any(not info.get(field) for field in ("model", "firmware", "mac")):
        raise ValueError("Incomplete device identity. Switch it on and retry in normal mode")
    key = firmware.resolve_model(str(info["model"]))
    if not key:
        raise ValueError(f"Unsupported device model {info['model']!r}")
    if model is not None and firmware.resolve_model(model) != key:
        raise ValueError(f"Requested model {model!r} does not match connected {info['model']!r}")
    version(info["firmware"])
    _mac(info["mac"])
    return Device(selected.device, location, info, key)


_LABELS = {
    "多设备固件": "General", "通用固件": "General", "阅读器固件": "Readers",
    "电纸书阅读器使用固件": "Android readers",
    "手机/平板使用固件": "Phone / tablet", "KOBO/PC固件": "Kobo / PC",
    "KOBO专用固件": "Kobo", "Kindle使用固件": "Kindle",
}


def _devices_text(text):
    """Readable names for known catalog targets; preserve unknown descriptions."""
    targets = []
    for token, label in (("手机", "Phone"), ("平板", "Tablet"), ("kobo", "Kobo"),
                         ("pc", "PC"), ("阅星曈", "Xteink"), ("阅星瞳", "Xteink")):
        if token in text.lower() and label not in targets:
            targets.append(label)
    if "安卓" in text and any(t in text for t in ("阅读器", "电子书", "电纸书")):
        targets.append("Android e-readers")
    elif "电纸书" in text:
        targets.append("E-ink readers")
    if "kindle" in text.lower():
        label = "Kindle"
        if "2024" in text:
            label += " (2024–2026" if "2026" in text else " (2024+"
            requirement = re.search(r"(?<![\d.])5\.\d+\.\d+(?![\d.])", text)
            if requirement:
                label += f"; system {requirement[0]}"
            label += ")"
        targets.append(label)
    return ", ".join(targets) if targets else text


def _buttons_text(text):
    for original, translated in (("下一页", "Next"), ("上一页", "Previous"),
                                 ("锁屏", "Lock screen"), ("休眠", "Sleep"),
                                 ("上", "Up"), ("下", "Down"), ("左", "Left"), ("右", "Right")):
        text = text.replace(original, translated)
    return re.sub(r"\s*[、，/]\s*", " / ", text)


def choose_variant(variants, fw_id=None, general=False, terminal=None):
    if not variants:
        raise ValueError("The official catalog contains no firmware releases")
    if fw_id:
        matches = [v for v in variants if v.fw_id.lower() == fw_id.lower()]
        if len(matches) != 1:
            raise ValueError(f"Firmware {fw_id!r} is not uniquely listed for this model; run download --list")
        return matches[0]
    if general:
        return firmware.latest_general(variants)
    terminal = terminal or Terminal()
    terminal.variants([(item, _LABELS.get(item.title, item.title) or item.fw_id,
                        _devices_text(item.devices), _buttons_text(item.direction)) for item in variants])
    if not sys.stdin.isatty():
        raise ValueError("Select a variant with --id ID or --general when running non-interactively")
    while True:
        choice = input("\nChoose a variant number or ID (q to cancel): ").strip()
        if choice.lower() in ("q", "quit"):
            return None
        if choice.isdecimal() and 1 <= int(choice) <= len(variants):
            return variants[int(choice) - 1]
        matches = [v for v in variants if v.fw_id.lower() == choice.lower()]
        if len(matches) == 1:
            return matches[0]
        terminal.line("  [!] Enter one of the listed numbers or firmware IDs.")


def _sudo_owner():
    if hasattr(os, "geteuid") and os.geteuid() == 0 and os.environ.get("SUDO_UID"):
        import pwd
        uid = int(os.environ["SUDO_UID"])
        return uid, int(os.environ.get("SUDO_GID", pwd.getpwuid(uid).pw_gid))
    return None


def data_directory():
    owner = _sudo_owner()
    if owner:
        import pwd
        home = Path(pwd.getpwuid(owner[0]).pw_dir)
    else:
        home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "hlyflash"
    configured = os.environ.get("XDG_DATA_HOME", "") if not owner else ""
    base = Path(configured) if configured and Path(configured).is_absolute() else home / ".local" / "share"
    return base / "hlyflash"


def _own(path):
    owner = _sudo_owner()
    if owner:
        os.chown(path, *owner, follow_symlinks=False)


def _directory(path):
    if not path.exists():
        path.mkdir(parents=True, mode=0o700)
        _own(path)
    return path


def _component(value):
    return re.sub(r"[^A-Za-z0-9_-]", "_", value)[:64] or "firmware"


def download_release(chosen, root, terminal):
    # Read the live URL each time; cache filenames include the verified content hash.
    with terminal.task(f"Download and verify {chosen.fw_id}"):
        raw = firmware._fetch(chosen.url)
        u = ufw.parse(raw)
        flasher.validate_package(u, application_only=True)
        cache = _directory(root / "firmware")
        digest = hashlib.sha256(raw).hexdigest()
        path = cache / f"{_component(chosen.fw_id)}-{digest[:16]}.ufw"
        try:
            with path.open("xb") as output:
                output.write(raw)
                output.flush()
                os.fsync(output.fileno())
            _own(path)
        except FileExistsError:
            if path.read_bytes() != raw:
                raise ValueError(f"Cached firmware was modified; refusing to use {path}")
    return u, path


def application_version(image, key):
    """Read the unique version literal from a checksum-validated physical app."""
    plain = application._plain(image, 4096, key)
    entries = application._app(plain)
    entry = next(e for e, _ in entries if e["name"] == "app.bin")
    payload = plain[entry["offset"]:entry["offset"] + entry["size"]]
    matches = set(re.findall(rb"(?:^|\x00)(V\d+(?:\.\d+){1,3})(?=\x00)", payload))
    if len(matches) != 1:
        raise ValueError("Cannot identify a unique firmware version in app.bin; refusing an unchecked update")
    return matches.pop().decode("ascii")


def _receipt(plan, **fields):
    plan.manifest.update(fields)
    path = Path(str(plan.backup_path) + ".json")
    fd, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w") as output:
            json.dump(plan.manifest, output, indent=2, ensure_ascii=False)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        _own(path)
    finally:
        temporary.unlink(missing_ok=True)


def verify_boot(device, expected_version, timeout=20):
    deadline = time.monotonic() + timeout
    last_error = "no serial response"
    while time.monotonic() < deadline:
        for port in _ports():
            try:
                location = _location(port)
            except ValueError:
                continue
            if location != device.location:
                continue
            try:
                info = serialinfo.query(port.device, fields=("model", "firmware", "mac"),
                                        timeout=0.6, rounds=1)
                if not all(info.get(f) for f in ("model", "firmware", "mac")):
                    continue
                if (firmware.resolve_model(str(info["model"])) != device.model or
                        _mac(info["mac"]) != _mac(device.info["mac"])):
                    raise SCSIError("Device identity changed after programming")
                if version(info["firmware"]) != version(expected_version):
                    raise SCSIError(f"Device booted {info['firmware']}, expected {expected_version}")
                return info
            except (OSError, ValueError) as e:
                last_error = str(e)
        time.sleep(0.4)
    raise SCSIError("Flash readback passed, but boot could not be verified: " + last_error +
                    ". Close the vendor app and run hlyflash info")


def _save_json(path, value):
    _directory(path.parent)
    fd, name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as output:
            json.dump(value, output, indent=2, ensure_ascii=False)
            output.flush()
            os.fsync(output.fileno())
        os.replace(name, path)
        _own(path)
    finally:
        Path(name).unlink(missing_ok=True)


def _device_key(device):
    return device.model + "-" + _mac(device.info["mac"])


def _preferences(root):
    try:
        value = json.loads((root / "preferences.json").read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def remembered_variant(root, device, variants):
    remembered = _preferences(root).get(_device_key(device))
    if remembered is None:
        # Import a prior successful update/check without guessing from a version.
        paths = sorted((root / "backups").glob("*/backup.bin.json"), reverse=True)
        for path in paths:
            try:
                receipt = json.loads(path.read_text())
                if (receipt.get("model") == device.model and
                        _mac(receipt.get("device_mac", "")) == _mac(device.info["mac"]) and
                        receipt.get("status") in ("up_to_date", "boot_verified")):
                    remembered = {"fw_id": receipt["firmware_id"]}
                    break
            except (OSError, ValueError, KeyError, TypeError):
                continue
    if not isinstance(remembered, dict):
        return None
    if remembered.get("general"):
        return firmware.latest_general(variants)
    exact = [v for v in variants if v.fw_id == remembered.get("fw_id")]
    if len(exact) == 1:
        return exact[0]
    # An ID may be replaced. Follow only an identical catalog description.
    signature = ("title", "devices", "direction")
    matches = [v for v in variants if all(remembered.get(k) == getattr(v, k) for k in signature)]
    if not matches or not all(v.released for v in matches):
        return None
    newest = max(v.released for v in matches)
    matches = [v for v in matches if v.released == newest]
    return matches[0] if len(matches) == 1 else None


def remember_variant(root, device, chosen, general=False):
    preferences = _preferences(root)
    preferences[_device_key(device)] = {k: getattr(chosen, k) for k in
                                      ("fw_id", "title", "devices", "direction", "released")}
    preferences[_device_key(device)]["general"] = general
    _save_json(root / "preferences.json", preferences)


def _battery(device, check):
    if check:
        return
    try:
        battery = int(str(device.info["battery"]).rstrip("%"))
    except (KeyError, ValueError):
        raise ValueError("Battery level is unknown; reconnect the device before updating")
    if not 20 <= battery <= 100:
        raise ValueError("Charge the device to at least 20% before updating")


class UpdateFailure(Exception):
    def __init__(self, cause, *, started=False, verified=False, backup=None, log=None, interrupted=False):
        super().__init__(str(cause) or "Interrupted")
        self.started, self.verified = started, verified
        self.backup, self.log, self.interrupted = backup, log, interrupted


def recovery_command(backup):
    entry = Path(sys.executable).parent / "hlyflash"
    command = [str(entry)] if entry.is_file() else [sys.executable, "-m", "hlyflash.cli"]
    if sys.platform != "win32":
        command.insert(0, "sudo")
    fresh = Path(backup).parent / ("before-recovery-" + secrets.token_hex(4) + ".bin")
    command += ["restore", str(backup), "--node", "usb", "--enter-boot", "--backup", str(fresh), "--force"]
    return shlex.join(command)


def report_failure(error):
    print(f"[FAIL] Update stopped: {error}", file=sys.stderr)
    if error.verified:
        print("Full flash readback passed; restart verification failed. Close the vendor app, "
              "reconnect the device and run hlyflash info.", file=sys.stderr)
    elif error.started:
        print("Flash writes started; the update may be incomplete. Keep the backup for recovery.", file=sys.stderr)
    else:
        print("Device flash was not modified.", file=sys.stderr)
    if error.backup:
        print(f"Backup: {error.backup}", file=sys.stderr)
        print("To restore this device's backup (connect only this device):\n" +
              recovery_command(error.backup), file=sys.stderr)
    if error.log:
        print(f"Details: {error.log}", file=sys.stderr)
    return 130 if error.interrupted else 1


def _usb_session(args, device, chosen, u, download, root, terminal):
    ldr = plan = run_dir = backup_path = None
    snapshot = None
    started = verified = False
    log = io.StringIO()
    fields = {}
    def status(value, **extra):
        fields.update(status=value, **extra)
        _save_json(root / "devices" / (_device_key(device) + ".json"), fields)
        if plan is not None:
            _receipt(plan, **fields)
    def before_write():
        nonlocal started
        status("programming", write_started=True)
        started = True
    try:
        try:
            with terminal.task("Check installed firmware") as activity:
                with redirect_stdout(log), redirect_stderr(log):
                    ldr = flasher.open_loader(args.node or "usb", args.loader, enter_boot=True,
                                              expected_location=device.location)
                    snapshot = flasher.read_current(ldr, activity.progress)
                    target = flasher.build_target(ldr, u, snapshot, application_only=True)
                installed = application_version(snapshot, ldr.chip_key_value)
                selected = application_version(target, ldr.chip_key_value)
                if version(installed) != version(device.info["firmware"]):
                    raise ValueError("Serial firmware version disagrees with the flash image; refusing update")
                changed = flasher.FlashPlan(Path(), snapshot, target, {}).changed_sectors
            fields = dict(model=device.model, device_mac=device.info["mac"],
                          installed_version=installed, selected_version=selected,
                          firmware_id=chosen.fw_id, firmware_url=chosen.url,
                          firmware_file=str(download), changed_sectors=changed, write_started=False)
            if not changed:
                status("up_to_date")
                terminal.result(f"Already up to date: {selected} ({chosen.fw_id})", [], mark="SKIP")
                return 0
            if version(installed) > version(selected):
                status("downgrade_refused")
                terminal.result("Update skipped", [f"Installed {installed} is newer than {selected}; downgrade refused."], mark="SKIP")
                return 0
            terminal.plan(installed, selected, chosen.fw_id, changed)
            if version(installed) == version(selected):
                terminal.line("  Same version number; different application bytes for this variant.")
            if args.check:
                status("update_available")
                terminal.result("Update available", ["Run again without --check to install."], mark="INFO")
                return 0
            backups = _directory(root / "backups")
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            run_dir = Path(tempfile.mkdtemp(prefix=f"{stamp}-{_device_key(device)}-", dir=backups))
            _own(run_dir)
            backup_path = run_dir / "backup.bin"
            with terminal.task("Save and verify recovery backup") as activity:
                with redirect_stdout(log), redirect_stderr(log):
                    plan = flasher.prepare_flash(ldr, u, backup_path, progress=activity.progress,
                                                 application_only=True)
                for path in run_dir.iterdir():
                    _own(path)
                if plan.backup != snapshot or plan.target != target:
                    raise ValueError("Device flash changed during preparation; refusing to write. Run update again")
                status("prepared")
            terminal.info("Backup", backup_path)
            if not args.yes:
                if not sys.stdin.isatty():
                    raise ValueError("Writing requires an interactive confirmation or --yes")
                if input(f"Install {chosen.fw_id} ({selected})? [y/N] ").strip().lower() not in ("y", "yes"):
                    status("declined")
                    terminal.line("  Cancelled. Verified backup retained; no flash writes.")
                    return 0
            with terminal.task("Install and verify firmware") as activity:
                with redirect_stdout(log), redirect_stderr(log):
                    plan.program(ldr, progress=activity.progress, before_write=before_write)
            verified = True
            status("readback_verified")
        finally:
            if ldr is not None:
                ldr.dev.close()
        with terminal.task("Verify device restart"):
            info = verify_boot(device, selected)
        status("boot_verified", boot_firmware=info["firmware"], mac_unchanged=True)
        terminal.result(f"Updated {device.info['model']} to {info['firmware']} ({chosen.fw_id})",
                        ["Device restarted and verified.", f"Backup: {backup_path}"])
        return 0
    except USBAccessError:
        # Only raised during USB opening, before any device command. Safe to elevate.
        raise
    except BaseException as error:
        if fields:
            try:
                status("boot_unverified" if verified else "failed", write_started=started)
            except OSError:
                pass
        backup = None
        if backup_path is not None and snapshot is not None:
            try:
                if backup_path.read_bytes() == snapshot:
                    backup = backup_path
            except OSError:
                pass
        log_path = None
        try:
            folder = run_dir or _directory(root / "logs")
            fd, name = tempfile.mkstemp(prefix="update-", suffix=".log", dir=folder)
            with os.fdopen(fd, "w") as output:
                output.write(log.getvalue() + "\n" + repr(error) + "\n")
            log_path = Path(name)
            _own(log_path)
        except OSError:
            pass
        raise UpdateFailure(error, started=started, verified=verified, backup=backup, log=log_path,
                            interrupted=isinstance(error, (KeyboardInterrupt, EOFError))) from error
    finally:
        if run_dir is not None:
            path = run_dir / "device.log"
            try:
                path.write_text(log.getvalue())
                for artifact in run_dir.iterdir():
                    _own(artifact)
            except OSError:
                pass


def run(args):
    terminal = Terminal(no_progress=args.no_progress)
    with terminal.task("Detect connected device"):
        device = detect_device(args.port, args.model)
    terminal.info("Device", f"{device.info['model']} - {device.info['firmware']}")
    root = _directory(Path(args.data_dir).expanduser().absolute() if args.data_dir else data_directory())
    with terminal.task("Check official releases"):
        variants = firmware.list_variants(device.model)
    chosen = None
    remembered_general = False
    if not args.id and not args.general and not args.choose:
        chosen = remembered_variant(root, device, variants)
        preference = _preferences(root).get(_device_key(device), {})
        remembered_general = isinstance(preference, dict) and preference.get("general", False)
    chosen = chosen or choose_variant(variants, args.id, args.general, terminal)
    if chosen is None:
        terminal.line("  Cancelled. No firmware downloaded or written.")
        return 0
    terminal.info("Firmware", f"{_LABELS.get(chosen.title, chosen.title) or chosen.fw_id} ({chosen.fw_id})")
    remember_variant(root, device, chosen, general=args.general or remembered_general)
    _battery(device, args.check)
    u, download = download_release(chosen, root, terminal)
    if sys.platform == "darwin" and os.geteuid() != 0:
        # Diagnose a broken installation before requesting a password.
        from .usb import _modules
        _modules()
        from .privilege import elevate
        return elevate(args, device, chosen, download, root, terminal)
    try:
        return _usb_session(args, device, chosen, u, download, root, terminal)
    except USBAccessError:
        if os.geteuid() == 0:
            raise
        from .privilege import elevate
        return elevate(args, device, chosen, download, root, terminal)
