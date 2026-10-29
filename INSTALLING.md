# Installing hlyflash

## Recommended: uv

Install the current GitHub version without cloning the repository:

```sh
uv tool install https://github.com/osteotek/hlyflash/archive/refs/heads/main.zip
hlyflash doctor
```

For a downloaded or cloned copy, run `uv tool install .` from its directory
instead. The GitHub archive includes all CLI source, the BR25 loader and its
license, Linux permission rules, documentation and tests. Firmware is downloaded
from the official catalog when requested; no local research files are required.

If uv is not installed, follow its [official installation guide](https://docs.astral.sh/uv/getting-started/installation/).
The [uv tool installer](https://docs.astral.sh/uv/guides/tools/) creates an isolated
Python environment and exposes `hlyflash` as a normal command. No activation is
needed. The firmware updater can then be started from any directory:

```sh
hlyflash update
```

Installation includes pyserial, PyUSB, a packaged libusb shared library and the
verified BR25 RAM loader. Old `.[serial,usb]` install commands still work, but
extras are no longer needed. macOS and Linux are supported; Python 3.9 or newer
is required. Do not run the installer with sudo. The guided updater asks for
administrator permission only when USB access requires it.

The [libusb package](https://github.com/pyocd/libusb-package) provides wheels for
supported macOS and Linux architectures. On those systems Homebrew or a system
libusb package is not required. hlyflash prefers an existing system libusb and
falls back to the packaged library. Availability on uncommon platforms depends
on the dependency's wheels; `doctor` checks that a library actually loads.

## Linux permissions

After installation, run `hlyflash setup` once. It requests sudo to install
narrowly scoped USB/serial udev rules. Unplug/reconnect the device afterward,
then run `hlyflash update`. No `--node` is needed. For SSH/headless access,
use `hlyflash setup --group GROUP` with an existing device-access group.
See [LINUX.md](LINUX.md) for distro notes and the full setup flow.

## Command not found

For a uv installation, run:

```sh
uv tool update-shell
```

Then open a new terminal and run `hlyflash --version`. This updates the shell's
PATH; installing again is not needed. For pipx, use `pipx ensurepath` instead.

## Check setup

```sh
hlyflash --version
hlyflash doctor
```

`doctor` checks the serial module, USB library loading and bundled loader hash.
It opens no device, changes no firmware and never requests sudo. It returns zero
when ready, or a nonzero status with repair instructions when a check fails.
It does not prove device permissions or firmware compatibility; those checks
happen when connecting to a device.

## Upgrade or remove

To install the newest GitHub source:

```sh
uv tool install --reinstall https://github.com/osteotek/hlyflash/archive/refs/heads/main.zip
hlyflash doctor
```

For a local checkout, obtain the newer source and run `uv tool install --reinstall .`
from its directory instead.

This updates the CLI. `hlyflash update` updates device firmware separately.
To remove the uv-installed command:

```sh
uv tool uninstall hlyflash
```

Saved firmware, preferences and recovery backups remain in the data directory
listed in [UPDATING.md](UPDATING.md).

## Alternative: pipx

Install from GitHub or use `pipx install .` for a local checkout:

```sh
pipx install https://github.com/osteotek/hlyflash/archive/refs/heads/main.zip
pipx ensurepath
```

Open a new terminal, then run `hlyflash doctor`. To replace a pipx installation
with updated source, run `pipx reinstall hlyflash`. A local-source installation
requires its original source path to remain available. Use `pipx uninstall hlyflash` to remove the command. Use the
same tool manager for reinstalling; do not overwrite another manager's executable.

## Alternative: Python virtual environment

With Python 3.9 or newer, from the project directory:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/hlyflash doctor
.venv/bin/hlyflash update
```

On Debian/Ubuntu, if creating the environment reports that ensurepip is missing,
install the distribution's `python3-venv` package and retry. A virtual environment
also avoids externally-managed Python errors from system or Homebrew Python.
`python -m hlyflash` works inside an installed environment if you prefer it to
the console command.

For development, use `uv sync --locked`, then `uv run hlyflash doctor` and
`uv run python -m unittest discover -s tests -v`. Editable pip installs also work:
`.venv/bin/python -m pip install -e .`.

## Missing dependencies or libusb

Run `hlyflash doctor` using the installed command, rather than a different Python
environment. Reinstall with the same tool manager to pick up all dependencies.
If the packaged library cannot load on your platform, install a native libusb
1.0 package. On macOS the fallback is `brew install libusb`; on Debian/Ubuntu it
is the `libusb-1.0-0` package. Then run `hlyflash doctor` again.

If setup passes but no device is found, connect the device with a USB data cable,
switch it on and close the vendor app or browser device-control tab. No device
is required to install the CLI or run `doctor`.
