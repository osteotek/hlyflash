"""Linux device permissions and mounted-storage checks."""
from __future__ import annotations

import argparse
import grp
from importlib.resources import files
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

RULE_PATH = Path('/etc/udev/rules.d/70-hlyflash.rules')
MARKER = '# Managed by hlyflash setup.'
_BOOTSTRAP = ('import json,sys; sys.path[:0] = json.loads(sys.argv.pop(1)); '
              'from hlyflash.linux import main; raise SystemExit(main())')


def rules(group=None):
    text = files('hlyflash.data').joinpath(RULE_PATH.name).read_text()
    if group is not None:
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]*\$?', group):
            raise ValueError('Invalid group name')
        text = text.replace('MODE="0660"', f'MODE="0660", GROUP="{group}"')
    return text


def install(group=None):
    if not sys.platform.startswith('linux'):
        raise ValueError('udev permission setup is only needed on Linux')
    if os.geteuid() != 0:
        raise ValueError('Linux setup needs administrator permission; run hlyflash setup')
    text = rules(group)
    if group:
        try:
            grp.getgrnam(group)
        except KeyError:
            raise ValueError(f'Group {group!r} does not exist; select an existing device-access group') from None
    udevadm = shutil.which('udevadm')
    if not udevadm:
        raise ValueError('udevadm not found. Install your distribution\'s udev package first')
    path = RULE_PATH
    if path.is_symlink():
        raise ValueError(f'Refusing to replace a symlink: {path}')
    if path.exists() and not path.read_text().startswith(MARKER):
        raise ValueError(f'{path} contains custom rules. Review it before replacing it with hlyflash setup')
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as output:
            output.write(text)
            output.flush()
            os.fchmod(output.fileno(), 0o644)
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    result = subprocess.run([udevadm, 'control', '--reload-rules'], capture_output=True, text=True)
    if result.returncode:
        raise ValueError(f'Rules saved to {path}, but udev could not reload them: '
                         f'{result.stderr.strip()}. Start udev and run hlyflash setup again')
    print('USB and serial permissions installed. Unplug/reconnect your page turner.')
    if group:
        print(f'Group access: {group}. Your account must belong to this group; new memberships require a new login.')
    return 0


def setup(group=None, print_rules=False):
    if print_rules:
        print(rules(group), end='')
        return 0
    if not sys.platform.startswith('linux'):
        raise ValueError('This setup command is for Linux. On macOS, run hlyflash doctor')
    if os.geteuid() == 0:
        return install(group)
    rules(group)  # Validate before asking for administrator permission.
    if not shutil.which('udevadm'):
        raise ValueError('udevadm not found. Install your distribution\'s udev package first')
    if not shutil.which('sudo'):
        raise ValueError('sudo not found. Have an administrator run hlyflash setup from this installation')
    from .privilege import _python_command
    arguments = ['--install'] + (['--group', group] if group else [])
    print('Installing Linux device permissions; sudo may ask for your password.', flush=True)
    return subprocess.run(['sudo', '--', *_python_command(_BOOTSTRAP, arguments)]).returncode


def mounted_volumes(location, sysfs=Path('/sys'), mountinfo=Path('/proc/self/mountinfo')):
    """Find mounts belonging to this exact USB device before detaching storage."""
    name = str(location[0]) + '-' + '.'.join(map(str, location[1]))
    device = sysfs / 'bus' / 'usb' / 'devices' / name
    if not device.exists():
        return []
    device = device.resolve()
    numbers = set()
    for block in (sysfs / 'class' / 'block').iterdir():
        if block.resolve().is_relative_to(device):
            numbers.add((block / 'dev').read_text().strip())
    mounts = []
    for line in mountinfo.read_text().splitlines():
        columns = line.split()
        if len(columns) >= 5 and columns[2] in numbers:
            mounts.append(re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), columns[4]))
    return mounts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install', action='store_true', required=True)
    parser.add_argument('--group')
    args = parser.parse_args(argv)
    try:
        return install(args.group)
    except (OSError, ValueError) as error:
        print(f'[FAIL] Linux setup: {error}', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('\nSetup interrupted. Run hlyflash setup again to finish.', file=sys.stderr)
        return 130


if __name__ == '__main__':
    raise SystemExit(main())
