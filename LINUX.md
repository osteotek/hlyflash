# Linux

The normal Linux flow uses libusb directly. No `/dev/sgN` discovery, Python
activation or loader download is needed. Python 3.9+ is required.

From this project's directory:

```sh
uv tool install .
hlyflash setup
```

Unplug/reconnect the page turner, switch it on, and close any vendor app or
browser tab using it. Then run:

```sh
hlyflash doctor
hlyflash info
hlyflash update --check
hlyflash update
```

If the command is not found, run `uv tool update-shell` and open a new terminal.
Installation with pipx or a Python virtual environment also works; see
[INSTALLING.md](INSTALLING.md). Debian/Ubuntu users missing uv can use
`sudo apt install pipx`, followed by `pipx install .` and `pipx ensurepath`.

## One-time permissions

`hlyflash setup` installs `/etc/udev/rules.d/70-hlyflash.rules`, requesting sudo
only for that system change. Review the exact rules without changing anything:

```sh
hlyflash setup --print-rules
```

The rules match USB VID:PID `4c4a:4155`. They give the active local desktop user
access to the device's USB and serial nodes through `uaccess`, and tell
ModemManager to leave the device's serial port alone. They do not grant access
to unrelated serial adapters or make devices writable by everyone. The file
sorts before `73-seat-late.rules`, where systemd applies session ACLs.

Setup reloads udev rules. It does not trigger unrelated devices; unplug/reconnect
the page turner afterward. Existing custom rules at that filename or symlinks
are preserved and reported for review. Repeating setup replaces only a file
marked as managed by hlyflash. To remove these permissions, remove that one rule
as an administrator, reload udev rules, and reconnect the device.

This uses the standard Linux [udev permission mechanism](https://www.freedesktop.org/software/systemd/man/latest/udev.html)
and systemd's [session ACL application](https://github.com/systemd/systemd/blob/main/rules.d/73-seat-late.rules.in).
The ModemManager properties follow its [device filtering documentation](https://www.freedesktop.org/software/ModemManager/doc/latest/ModemManager/ModemManager-Common-udev-tags.html).

## SSH and headless systems

Session ACLs apply to active local sessions. For SSH or a machine without
logind, use an existing device-access group instead. For example, on Debian/Ubuntu:

```sh
sudo usermod -aG dialout "$USER"
hlyflash setup --group dialout
```

Log out and back in after adding group membership, then reconnect the device.
The selected group gets both USB and serial access. Other distributions may use
`uucp` or `plugdev`; setup validates the group exists and does not create groups
or change account membership. Use the group appropriate to your distribution.

If USB capture still needs administrator permission, `update` requests sudo
before any USB commands and revalidates the same device after authentication.
Serial access must already work to identify the device and select its firmware.
A serial permission error points to `hlyflash setup` rather than suggesting the
device is off or busy. Keep the installed command accessible to the account
running the CLI; do not install Python packages with sudo.

## Device selection and mounted storage

CDC serial devices normally appear as `/dev/ttyACM0`. Detection matches USB
identity, rather than querying every serial adapter. Select among multiple page
turners using a device node or a stable serial symlink:

```sh
hlyflash update --port /dev/ttyACM0
hlyflash update --port /dev/serial/by-id/YOUR-DEVICE
```

Serial interface suffixes such as `1-2.3:1.2` are mapped to the USB bus/port.
The updater follows that physical port through boot transitions and verifies
model/MAC again after programming. It does not choose a replacement by a newly
assigned `/dev/ttyACM` number.

Before claiming USB mass storage, hlyflash refuses to detach a device with a
mounted filesystem visible in the process's mount namespace. Unmount the page
turner's volumes before retrying. It detaches only the selected storage
interface, restores its kernel driver on completion, and tolerates a disappeared
device during normal USB re-enumeration. It does not replay failed transfers.

For manual commands, USB is also the default on Linux:

```sh
hlyflash probe
hlyflash dump --enter-boot --out original.bin
```

These manual commands do not request sudo automatically; use the permissions
setup or invoke the installed command with sudo if needed. The low-level
`--node /dev/sgN` backend remains available for advanced use; it is not used by
the guided updater or automatically selected.

## Libraries and distro services

Default installation includes packaged libusb for supported Linux wheels and
prefers an existing system libusb. If the library cannot load, `hlyflash doctor`
prints a Linux-specific repair hint. Common native-library packages are
`libusb-1.0-0` on Debian/Ubuntu and `libusb1` on Fedora. Setup requires `udevadm`
and a running udev service. Containers typically lack a udev daemon and session
ACLs; they are useful for software tests, not automatic host USB access.

The same firmware guards apply on Linux: exact hardware/layout checks,
application-only conversion, downgrade refusal, a fresh double-verified backup
before writes, full-flash readback and normal-mode firmware/MAC verification.
Backups and preferences live under `$XDG_DATA_HOME/hlyflash`, normally
`~/.local/share/hlyflash`.

## Validation

On 2026-10-30, the built wheel was installed in Debian Bookworm ARM64 containers
and tested as UID 1000 with Python 3.9.25 and 3.11.18. All 126 tests passed in
both environments, including a real pyserial/termios exchange over a Linux
pseudo-terminal. The checks also verified packaged libusb loading, XDG data
paths, installed update/setup helper imports and the no-device error path.
Container mounts of the wheel and test suite were read-only.

The suite covers serial permission errors, stable symlinks, composite USB
locations, managed udev-rule installation, group selection, mounted-volume
refusal, USB-driver cleanup, access-denial escalation and full flash simulation.
The macOS regression run passed with the Linux PTY test skipped; the connected
Free 3 still reported V0.25 through the normal serial command.

A GitHub Actions matrix is configured for Linux and macOS with Python 3.9 and
3.13. This local validation did not execute that remote workflow.

Actual Free 2 and Free 3 flash writes have been verified on macOS. A physical
Linux USB flash test, including live udev/session permissions and kernel-driver
behavior, is still required. Passing a container suite does not establish
Linux hardware behavior.
