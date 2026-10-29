# hlyflash

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)
[![Platform: macOS | Linux](https://img.shields.io/badge/platform-macOS%20%7C%20Linux-lightgrey.svg)](#what-works-where)
[![Models: Free 2 · Free 3 · Free 2 2026 · E1 Pro](https://img.shields.io/badge/models-Free%202%20%C2%B7%20Free%203%20%C2%B7%20Free%202%202026%20%C2%B7%20E1%20Pro-green.svg)](#)

Command-line tools for **Hanlinyue** Bluetooth page turners (Free 2 / Free 3 /
Free 2 2026 / E1 Pro), which are built on **JieLi AC632N / AC636N** chips. The
vendor only ships a Windows firmware updater; the macOS app deliberately
disables firmware updates. This project reconstructs the pieces needed to work
with these devices from macOS and Linux.

It was produced by reverse-engineering the vendor's own tools **for
interoperability / educational purposes**, on hardware the user owns.

## Install

Install directly from GitHub on macOS or Linux:

```sh
uv tool install https://github.com/osteotek/hlyflash/archive/refs/heads/main.zip
```

[Install uv](https://docs.astral.sh/uv/getting-started/installation/) if needed.
If `hlyflash` is not found afterward, run `uv tool update-shell` and open a new
terminal.

On Linux, run `hlyflash setup` once, then unplug/reconnect the device. On macOS,
no permission setup is needed. Connect the page turner, switch it on and close
the vendor app or browser device-control tab. Then run:

```sh
hlyflash doctor
hlyflash update
```

The CLI runs in its own environment. Serial support, PyUSB, packaged libusb and
the verified BR25 RAM loader are included; no dependency extras or separate
loader download are needed. Supported macOS/Linux wheels avoid a separate
libusb installation. Python 3.9+ is required. The updater checks compatibility,
creates a verified backup before writes and requests administrator access only
when required.

Flashing has been tested on BR25 / AC636N **Free 2** and **Free 3** devices.
Firmware catalogs for Free 2 (2026) and E1 Pro are available, but flashing those
models and other chip revisions has not been verified on hardware.

Already have a downloaded or cloned copy? Run `uv tool install .` from its
directory. `pipx install .` also works. See [installation and troubleshooting](INSTALLING.md)
for PATH fixes, upgrades, removal and development, and [Linux setup](LINUX.md)
for headless permissions and device selection.

![hlyflash demo: list commands, download and verify firmware, inspect a .ufw](assets/demo.gif)

## What works where

| Feature | macOS | Linux | How |
|---|---|---|---|
| Read model / firmware / MAC / battery | ✅ | ✅ | CDC serial JSON (read-only) |
| List / download official firmware | ✅ | ✅ | scrapes vendor site (stdlib) |
| Parse / verify / extract `.ufw` firmware | ✅ | ✅ | pure Python |
| Guided download, latest-release check and automatic backup (`update`) | ✅ | supported | variant selection + guarded application update |
| Inspect SCSI identity / start RAM loader (`probe`) | ✅ | ✅ | libusb BOT / Linux `SG_IO` |
| Back up full flash, with two-pass verification (`dump`) | ✅ tested on Free 2 and Free 3 | supported | libusb BOT / Linux `SG_IO` |
| Write a compatible physical image (`restore`, `flash`) | ⚠️ experimental | ⚠️ experimental | verified backup + erase/program/readback |

Linux installation and the full simulation suite have passed on Debian ARM64
with Python 3.9 and 3.11. Physical Linux USB flashing still needs hardware
validation. See [Linux setup and test results](LINUX.md).

Supported models (and their firmware pages): **Free 2**, **Free 3**,
**Free 2 (2026)**, **E1 Pro**.

On macOS the CLI uses PyUSB/libusb to capture the selected USB device and sends
vendor SCSI commands through Bulk-Only Transport. The guided `update` command requests administrator access when needed; manual
USB commands require `sudo`. Capture briefly disconnects the serial and HID interfaces.
Linux uses the same USB backend by default and also accepts `/dev/sgN` explicitly. `usb` selects exactly one JieLi
storage device; use `usb:<bus>:<address>` when several are connected.

**Package limitation:** current official Free 2 / Free 3 `.ufw` files contain a staged
`app_dir_head2` layout, not a directly writable physical image. Direct flashing
is rejected. `flash` automatically adapts a constrained staged BR25 package to
the installed partition map using the experimental application-only path.
It requires an identical bootloader and `cfg_tool.bin`, compatible hardware
configuration, and retains the existing boot sector
and data partitions. This is not a reproduction of the vendor updater. A Free 3
upgrade from V0.09 to general firmware V0.25 passed full readback and boot/serial
verification; Bluetooth/button behavior remains to be checked. `restore` accepts a validated physical
backup from your own device.
Writing also checks the application against the device's chip key; it never
burns or changes that key. See [macOS research and hardware results](MACOS_FLASHING_RESEARCH.md).
The original BR25 Free 2 has also passed a verified full backup, F0AFLJ firmware
programming/full readback, and V0.15 → V0.22 boot/serial verification with the same MAC.
See [Free 2 support and validation](FREE2_SUPPORT.md).

## Usage

### Guided update

Connect the device, switch it on and close any vendor app / browser device-control
tab. The guided updater remembers the firmware variant for each device, checks
the installed application, and creates a double-verified full backup before updating:

```sh
hlyflash update
```

It compares the installed application with the selected release and skips an
exact match. Different variants can share a version number, so an equal `V0.xx`
string alone never triggers an "already up to date" result. Downgrades are refused.
Already-current and `--check` runs do not create backups. When an update is
needed, it saves a fresh verified backup and asks before programming. If USB
access requires administrator permission, it requests your password through
`sudo` after variant selection and download. Successful updates verify the entire flash and then check
the running firmware and unchanged device MAC over serial.

```sh
# Check the remembered variant without creating a backup or writing flash:
hlyflash update --check

# Choose another variant:
hlyflash update --choose

# Check a particular variant:
hlyflash update --id F0AFLJ --check

# Select the latest explicitly labelled general release:
hlyflash update --general

# Explicit variant for unattended use; compatibility checks remain enabled:
hlyflash update --id F0AFLJ --yes
```

On macOS, firmware and backups live under
`~/Library/Application Support/hlyflash/` for the invoking user, including when
using sudo. Linux uses `$XDG_DATA_HOME/hlyflash` or `~/.local/share/hlyflash`.
`--data-dir DIR` overrides this location. Updates that need writing get a new
backup directory; the exact target, hashes and result are retained alongside
`backup.bin`. Device preferences and check results are stored separately.

The interface provides a short numbered variant list, compact update details, animated Braille-dot
spinners (`⠋⠙⠹⠸...`) and `[####----]` progress bars on one transient line. ASCII-only terminals fall
back to `|/-\`. Redirected output is plain and animation-free;
`--no-progress` also disables animation in a terminal. Detailed device messages
are saved to `device.log` in the backup folder. Failures state whether writes
started and show the valid backup and a copyable restore command when available. See [guided update details](UPDATING.md).

### Individual commands

```sh
# Safe, works now on macOS and Linux (device in normal mode):
hlyflash info
#   Device on /dev/cu.usbmodem1103:
#     model    : Free 3
#     firmware : V0.09
#     mac      : DD-14-33-87-28-A0
#     battery  : 100%

# List the official firmware variants for your model (auto-detects a
# connected device, or pass --model free2 / free3 / free2-2026 / e1pro):
hlyflash download --list
hlyflash download --model free3 --general
hlyflash download --model free2 --list

# Download a specific variant (verified against its own checksums on save):
hlyflash download --model free2 --id FBF4LJ -o free2.ufw

# Inspect / verify / extract a firmware package:
hlyflash ufw free2.ufw
hlyflash ufw free2.ufw -x ./extracted/

# Inspect USB SCSI identity (INQUIRY only):
sudo hlyflash probe --node usb

# Linux SCSI example, when a flash-capable loader is already running:
sudo hlyflash dump --node /dev/sg1 --out backup.bin

# A physical UFW image (staged vendor images are rejected):
sudo hlyflash flash physical.ufw --node /dev/sg1 --backup before-flash.bin --force
```

### macOS: loader, backup, and restore

The tested Free 3 identifies as BR25 / AC636N. The ROM needs a RAM flash loader
before it can read or program flash. The CLI bundles the tested 11264-byte
`br25loader.bin` from `jl-uboot-tool` with its upstream license and
[provenance notice](hlyflash/data/NOTICE.md). It selects the loader automatically
for BR25, verifies its SHA-256, uploads it to RAM, and checks readback before
starting it. This does not replace the bootloader stored in flash. BR25's hash is
`0bc69023d6bf4c4d5d5bc048e06249ee39c484acd11196ffc73f88940787d77d`.
An optional `--loader /path/to/br25loader.bin` overrides the bundled resource,
subject to the same hash check. The BD19 loader must still be supplied through
`--loader`; its profile is present but has not been tested on hardware;
physical-image validation currently supports BR25/JLFS version 3.

```sh
# Request download mode, verify/start the RAM loader, inspect capacity and key:
sudo hlyflash probe --node usb --enter-boot

# Read the entire detected flash twice, compare every byte, then publish backup:
sudo hlyflash dump --node usb --enter-boot --out original.bin

# Validate a restore and save current flash, without erasing/programming:
sudo hlyflash restore original.bin --node usb --enter-boot \
  --backup before-dry-run.bin --dry-run

# Explicitly restore your own physical dump; save a fresh full backup first:
sudo hlyflash restore original.bin --node usb --enter-boot \
  --backup before-restore.bin --force
```

Backup paths must be new. Incomplete or inconsistent reads remain in a
`.partial` file and are never reported as verified backups. Each write saves a
backup manifest, skips unchanged sectors, preserves partial-sector tails, writes
the boot sector last, and verifies the entire physical flash afterward. `restore`
includes the dump's settings and identity, so use a backup from the same unit.

Each model publishes **several firmware variants** for different e-readers
(generic / Kindle / Kobo) and button layouts — `download --list` shows the
applicable-devices and button-direction notes so you can pick the right one.
`download --general` selects the newest release explicitly labelled general /
multi-device, using its publication date. It refuses an ambiguous selection;
`--id` remains available for an exact variant. `--list` never downloads a file.

### Experimental Free 3 application update

```sh
hlyflash download --model free3 --general -o general.ufw

# Save a new double-verified backup and the exact proposed physical image:
sudo hlyflash flash general.ufw --node usb --enter-boot \
  --backup before-general-dry-run.bin --dry-run

# After inspecting the dry run, use another new backup path for the actual write:
sudo hlyflash flash general.ufw --node usb --enter-boot \
  --backup before-general-write.bin --force
```

Staged packages automatically select this path; `--application-only` remains
available to explicitly require it. Validated non-staged physical packages use
the physical-image path. A rejected conversion never falls back to direct
programming of staging data. This path retains VM, BTIF and EXIF (when present)
contents and descriptors, refuses growing applications or changed bootloader /
cfg_tool payloads, and checks the complete
application-area CRC before and after adapting its table. The proposed image is
saved as `<backup>.target.bin`, with hashes in `<backup>.json`. Boot and user-data
sectors must remain unchanged. A successful full readback still needs a reboot
and functional verification; firmware compatibility is experimental.
Hardware configuration must match except for the documented `UPDATE_JUMP`
updater-entry option (0/1); the installed value and entire boot sector are kept.
SPI, clock, reset and other configuration changes are rejected.

### Original Free 2

The original Free 2 and Free 2 (2026) use separate firmware catalogs. Serial names
such as `Free2`, `Free02`, `Free2_2026`, and `Free2 2026` select the appropriate
catalog. The original Free 2 publishes specialized phone, reader, Kobo and
Kindle variants, so choose an explicit ID rather than `--general`:

```sh
hlyflash info
hlyflash download --model free2 --list
hlyflash download --model free2 --id F0AFLJ -o free2-reader.ufw
sudo hlyflash dump --node usb --enter-boot --out free2-original.bin
sudo hlyflash flash free2-reader.ufw --node usb --enter-boot \
  --backup free2-before-dry-run.bin --dry-run
```

`F0AFLJ` is a reader variant, not a universal selection. On the tested BR25 Free 2,
the dry run and actual write verified 46 changed sectors while retaining boot
and user data; the device then booted V0.22 and answered serial queries.
Free 2 (2026) firmware is rejected against this unit's hardware configuration.
Free 2 (2026) has catalog/package coverage but has not been tested on hardware.
Close the vendor app or browser device-control tab before serial commands;
Web Serial can automatically reconnect after a USB boot transition.

### ⚠️ Hardware revisions: AC632N vs AC636N

The same model name covers two chip families. Older units are **AC632N**; the
current Free 2 / Free 3 / Free 2 2026 firmware is **AC636N**. The `.ufw` header
names its target chip (shown by `download` and `ufw`). **Flashing an image built
for the wrong chip bricks the device** — make sure the firmware's chip matches
your hardware before using `flash`.

## Entering download (UBOOT) mode

`dump`/`flash`/`restore` need a flash-capable RAM loader. In **UBOOT download mode** the chip
enumerates as a USB Mass-Storage device whose SCSI INQUIRY product string starts
with `UBOOT`. A JieLi chip enters this mode when:

- its flash is blank or the firmware image is invalid (auto-recovery), or
- it is forced in via the vendor "USB updater" dongle / key signal, or
- (model dependent) a button combination at power-on.

On the tested Free 3, `--enter-boot` sends `FC0A`, which changes normal `UDISK`
mode to ROM `UBOOT1.00`; the CLI then uploads and verifies the chip-specific
RAM loader before starting it. The USB backend follows re-enumeration on the
same physical port. On Linux, find the SCSI node with `lsscsi -g` or `dmesg`.

## The `.ufw` firmware container

All multi-byte fields little-endian. The header and entry table are scrambled
with JieLi's "ENC" keystream (a CRC16-LFSR XOR) keyed with `0xFFFF`; payloads are
stored in the clear.

```
Header (0x40 bytes, ENC key 0xFFFF)
  0x00 u16  CRC16 of header[2:0x40]
  0x02 u16  CRC16 of the entry table
  0x04 u32  total file size
  0x08 u16  entry count
  0x10      chip name, e.g. "AC632N"

Entry[] (0x50 bytes each, ENC key 0xFFFF)
  0x00 u8   type
  0x04 u16  CRC16 of payload
  0x08 u32  payload offset in file
  0x0C u32  payload size
  0x40      name, e.g. "flash.bin"
```

`flash.bin` is firmware image data. Some packages use a vendor staging layout
that needs conversion before it represents physical flash bytes. A valid
container checksum alone does not establish that it can be written at offset 0.
Other entries (`ota.bin`, `tail.bin`, config blobs) are auxiliary.

## Protocol notes

The download protocol is JieLi's standard Mass-Storage command set: 16-byte SCSI
CDBs carrying a 2-byte big-endian opcode plus args (0xFF-padded), with a 16-byte
response echoing the opcode. Families: `0xFB` write/erase, `0xFC` control/query,
`0xFD` read. This matches the documented
[`jl-uboot-tool`](https://github.com/kagaimiq/jl-uboot-tool) `LoaderV2`.

## Safety

Firmware writing is experimental. On a Free 3, macOS capture, ROM entry, RAM
upload/readback, a full 512 KiB two-pass backup, physical programming of a test
pattern into an empty EXIF sector, sector erase, and complete flash restoration
have passed on real hardware. The constrained application-only Free 3 upgrade
from V0.09 to V0.25 also passed full-flash readback and boot/serial verification.
The BR25 Free 2 upgrade from V0.15 to V0.22 passed the same checks. Both retained
their Bluetooth MAC afterward. Bluetooth pairing and button operation
have not been tested; other firmware packages and hardware remain experimental.
The writer refuses unsupported staging layouts, unknown keys, invalid CRCs,
capacity mismatches, and incomplete transfers. Keep power and USB stable during
any write; use the vendor updater for staged packages that this tool rejects.

Run the protocol and NOR-flash simulation tests with:

```sh
python -m unittest discover -s tests -v
```

## Credits

Protocol cross-checked against the open-source
[`jl-uboot-tool`](https://github.com/kagaimiq/jl-uboot-tool) and
[`jielie`](https://github.com/kagaimiq/jielie) projects by kagaimiq.
