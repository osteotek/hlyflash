"""Late privilege handoff for the USB stage of a guided update.

The ordinary user selects and downloads the firmware. sudo inherits the terminal
only after those steps; passwords are handled by sudo, never by Python.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import stat
import site
import subprocess
import sys
import tempfile
from types import SimpleNamespace

from . import firmware, flasher, ufw, updater
from .scsi import SCSIError
from .terminal import Terminal


_BOOTSTRAP = ("import json,sys; sys.path[:0] = json.loads(sys.argv.pop(1)); "
              "from hlyflash.privilege import main; raise SystemExit(main())")


def _python_command(bootstrap, arguments):
    # -I ignores the working directory and PYTHONPATH. Explicitly retain this
    # installation and active user-site dependencies (pip --user), as well as
    # the interpreter's own virtualenv site-packages. Do not resolve the Python
    # symlink: that would escape a pipx/venv installation.
    paths = [str(Path(__file__).absolute().parent.parent)]
    user_site = site.getusersitepackages()
    for path in ([user_site] if isinstance(user_site, str) else user_site):
        if path in sys.path and path not in paths:
            paths.append(path)
    return [sys.executable, "-I", "-c", bootstrap, json.dumps(paths), *arguments]


def _command(job):
    return ["sudo", "-n", "--", *_python_command(_BOOTSTRAP, ["--job", str(job)])]


def elevate(args, device, chosen, download, root, terminal):
    jobs = updater._directory(root / ".jobs")
    folder = Path(tempfile.mkdtemp(prefix="update-", dir=jobs))
    job = folder / "job.json"
    handed_off = False
    try:
        updater._save_json(job, {
            "device": asdict(device), "variant": asdict(chosen),
            "download": str(download), "sha256": hashlib.sha256(download.read_bytes()).hexdigest(),
            "root": str(root),
            "args": {k: getattr(args, k) for k in ("check", "yes", "no_progress", "node", "loader")},
        })
        terminal.line("  USB access needs administrator permission; sudo may ask for your password.")
        authenticated = subprocess.run(["sudo", "-v"], check=False)
        if authenticated.returncode:
            raise ValueError("Administrator permission was not granted. Run hlyflash update to retry")
        command = _command(job)
        # Replace this process so Ctrl-C and final confirmation use the same TTY.
        os.execvpe("sudo", command, os.environ.copy())
        handed_off = True  # exec does not return; useful only for mocked test runners.
        return 0
    finally:
        if not handed_off:
            job.unlink(missing_ok=True)
            folder.rmdir()


def _load_job(path):
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd) as source:
        metadata = os.fstat(source.fileno())
        owner = int(os.environ.get("SUDO_UID", os.geteuid()))
        if (not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o600 or
                metadata.st_uid != owner or metadata.st_nlink != 1 or metadata.st_size > 65536):
            raise ValueError("Invalid private update job; run hlyflash update again")
        return json.load(source)


def resume(path):
    if os.geteuid() != 0:
        raise ValueError("The USB helper must be started by hlyflash update")
    job = _load_job(path)
    expected = job["device"]
    args = SimpleNamespace(**job["args"])
    # A device could be swapped while sudo was asking for a password.
    device = updater.detect_device(expected["port"], expected["model"])
    location = (expected["location"][0], tuple(expected["location"][1]))
    if (device.location != location or
            updater._mac(device.info["mac"]) != updater._mac(expected["info"]["mac"])):
        raise ValueError("Connected device changed while requesting permission; run update again")
    updater._battery(device, args.check)
    download = Path(job["download"])
    raw = download.read_bytes()
    if hashlib.sha256(raw).hexdigest() != job["sha256"]:
        raise ValueError("Downloaded firmware changed before USB access; run update again")
    package = ufw.parse(raw)
    flasher.validate_package(package, application_only=True)
    chosen = firmware.Variant(**job["variant"])
    return updater._usb_session(args, device, chosen, package, download,
                                Path(job["root"]), Terminal(no_progress=args.no_progress))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", required=True, type=Path)
    path = parser.parse_args(argv).job
    valid_job = False
    try:
        # Do not delete arbitrary paths passed to an invalid helper invocation.
        _load_job(path)
        valid_job = True
        return resume(path)
    except updater.UpdateFailure as error:
        return updater.report_failure(error)
    except (OSError, ValueError, KeyError, TypeError, SCSIError) as error:
        return updater.report_failure(updater.UpdateFailure(error))
    except (KeyboardInterrupt, EOFError):
        return updater.report_failure(updater.UpdateFailure("Interrupted", interrupted=True))
    finally:
        if valid_job:
            path.unlink(missing_ok=True)
            try:
                path.parent.rmdir()
            except OSError:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
