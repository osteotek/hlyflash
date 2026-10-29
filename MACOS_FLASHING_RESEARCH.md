# Enabling native macOS flashing

Research and hardware probes: 2026-10-29, macOS 27.2 on Apple Silicon,
libusb 1.0.30, Hanlinyue Free 3 firmware V0.09.

## Implementation follow-up

The research below is the initial investigation. Native access is now implemented
in `hlyflash/usb.py`, with ROM/session initialization in `flasher.py`. On the same
Free 3, the installed CLI has subsequently passed:

- SCSI INQUIRY through PyUSB/libusb.
- `FC0A` entry from `UDISK` to `UBOOT1.00`.
- Upload and readback of the pinned 11264-byte BR25 RAM loader at `0x12000`,
  followed by a successful jump into that loader.
- Flash identification `0xcd7013`, 512 KiB capacity, and chip-key retrieval.
- A two-pass full flash backup and a zero-change restore dry run.
- Actual programming/readback of a 256-byte pattern in the empty EXIF sector
  at `0x7f000`, erasing it back to FF, and byte-for-byte verification of all
  512 KiB against the original backup afterward.

The restored flash SHA-256 is
`9ebe18dfcc05ed02518815fcc848209da7727f70779b0c2c5c0737b71f334864`.
The user's installed V0.09 firmware was not upgraded during that test. The programming test and
its temporary-pattern cleanup both completed successfully.

A subsequent serial query confirmed normal operation as Free 3 / V0.09 with
100% battery. All 37 automated tests passed, and an offline wheel build passed
inspection: the USB transport and physical-image validator are included, while
local research artifacts and proprietary loader/firmware files are excluded.

Two BR25 ROM details were established on hardware: the upload CRC covers the
**decoded** loader bytes, while the transmitted bytes remain MengLi-scrambled;
and successful vendor transfers can leave the original CSW residue unchanged.
The latter quirk is explicitly enabled only after recognizing the relevant ROM
profile, and still requires success status and the full actual byte count.
RAM uploads must match readback before execution. An unnecessary unsupported
flash-capability query before ROM upload also caused a rejection; initialization
now follows the successful direct-upload sequence.

`restore` supports verified physical images. Official Free 3 UFW files have an
`app_dir_head2` staging layout and different partition declarations from the
installed firmware. Direct programming remains rejected. Their application
file CRCs validate under this unit's existing chip key; staging/layout conversion
is required rather than simply re-encrypting the payload.

### General firmware upgrade: write, readback and boot passed

The live [official Free 3 catalog](https://hanlinyue.com.cn/1762607565939) lists
`5A0FLJ`, published 2026-10-22, as multi-device/general firmware. The newer
`18B2LJ` card is the reader-specific variant. `download --general` now selects
the general variant from the live card title and publication date, refusing
ambiguous choices. The downloaded general package contains a `V0.25` string.
Its SHA-256 is
`5a20a8906d066ab1cbd6a4acc93ae2ed8d952ee7d1cd51ee3c77b66eb034e8a7`.

`flash --application-only` is a constrained, experimental adaptation, not a
reproduction of the vendor updater. It requires the same BR25 bootloader,
hardware configuration, application entry point and cfg_tool.bin payload. It
retains the original boot sector and VM/PRCT/BTIF/EXIF descriptors and contents,
copies the unchanged application/configuration payloads, and recalculates the
application-area and table-header CRCs after adapting the table. Changed
boot/configuration payloads, additional banks, overlapping data and growing
applications are rejected. `--dry-run` saves a two-pass backup, exact proposed
target and manifest without erasing or programming.

Against the original Free 3 backup, the proposed physical target is 524288 bytes,
SHA-256 `d3f15a9eab22be5081ad5e7c359604b2912a7c65680a81269ea388b61644f27f`.
It changes 59 application sectors (`0x1000` through `0x3b000`). The boot sector
and every byte from `0x44000` onward remain identical. The exact target passed
the NOR programming simulation. All 47 automated tests and the updated wheel
build passed. The user subsequently ran the hardware dry run with sudo: ROM
entry, verified RAM loader startup, two-pass full backup and application-only
conversion all passed, reporting exactly 59 changed sectors and no flash writes.
The saved backup, firmware and target hashes were independently checked against
the values above, and boot/data preservation was confirmed from those artifacts.
The user then ran the actual `--application-only --force` write with a fresh
backup path. It reported 59 changed sectors and successful full 512 KiB readback.
The saved write backup and target hashes match the values above; the manifest
records a real write, and boot/data preservation was independently confirmed
from the artifacts. A subsequent live serial query returned **Free 3 / V0.25 /
100% battery**, with the same Bluetooth MAC as V0.09. This confirms the new
firmware boots and answers normal serial queries. Bluetooth pairing and physical
button/page-turn behavior have not yet been tested.
See README for the command interface and limits.

### Bundled BR25 loader

The CLI now ships the exact hardware-tested 11264-byte `br25loader.bin`, selected
automatically after BR25 identification. The SHA-256 remains pinned and RAM
readback is still required before execution. An explicit `--loader` override is
subject to the same hash check; the untested BD19 loader remains external.
The source is `kagaimiq/jl-uboot-tool` at
`adb3f18889e88ac512ce0a3c4d8cc3d3cb30696a`. That repository distributes the blob
alongside its root MIT license, with no separate loader-directory notice found.
The package includes the upstream license and a provenance notice. Only this
selected loader is added; device backups, vendor firmware and decompiled code
remain excluded. All 49 tests passed, including default-loader upload, rejection
of missing/corrupt bundled bytes and the external-loader override. Both source
and wheel archives include the loader/notices; a simulated ROM bootstrap loaded
the resource directly from the wheel in an isolated Python process.

### Automatic staging conversion

`flash` now detects staged UFW data and automatically selects the same validated
application-only conversion used for the successful V0.25 upgrade. The explicit
`--application-only` flag remains compatible and requires that conversion path.
Unsupported staging is rejected without any direct-write fallback. Non-staged
physical images retain their validated physical-image path. The backup,
`--force`, dry-run, hash and partition-preservation requirements are unchanged.
All 52 tests passed against the rebuilt wheel, including the CLI update without
`--application-only`, unsupported-staging rejection, and bundled-loader startup.

**Native USB transport is confirmed working on this hardware.** The user ran
the administrator capture probe and successfully completed SCSI INQUIRY over
USB Bulk-Only Transport (BOT). It returned `BR25 / UDISK / 1.00`, then released
the interface and restored macOS drivers. This establishes a path for carrying
the existing JieLi commands; it does not validate flash programming.

The previous README statement that native flashing is impossible on macOS was
too broad. The implemented `SG_IO` transport is Linux-only, but privileged USB
capture provides another route.

## Hardware evidence

The normal-mode USB device is VID/PID `4c4a:4155` and exposes these interfaces:

| Interface | Class/subclass/protocol | Endpoints | Function |
|---|---|---|---|
| 0 | `08/06/50` | bulk OUT `01`, bulk IN `81`, maximum packet 64 bytes | SCSI transparent command set, BOT |
| 1 | `03/00/01` | interrupt IN `82` | HID |
| 2 | `02/02/01` | interrupt IN `83` | CDC control |
| 3 | `0a/00/00` | bulk OUT `04`, bulk IN `84` | CDC data |

IORegistry identifies `IOUSBMassStorageInterfaceNub` as interface 0's exclusive
owner. Its logical unit has peripheral type 0 and identifies as `BR25 UDISK`.

Three probes establish the boundary:

1. Apple's native SCSITask plugin returned `0xe00002c7` (unsupported) on both
   `IOSCSILogicalUnitNub` and `IOSCSIPeripheralDeviceType00`. No SCSI commands
   were sent by this discovery probe.
2. As an ordinary user outside the agent sandbox, libusb could enumerate the
   descriptors and open the device, but claiming interface 0 returned
   `LIBUSB_ERROR_ACCESS` while the storage driver owned it. No BOT command was
   sent in that run.
3. **As root in the user's Terminal, USB capture and BOT succeeded.** The
   command wrapper transferred 31 bytes, INQUIRY returned 36 bytes, and its
   status wrapper transferred 13 bytes. Signature, tag, residue and command
   status checks passed. Driver release/restoration also returned success.

After restoration, a fresh `hlyflash info --port /dev/cu.usbmodem1103` also
succeeded, returning Free 3, V0.09, a MAC address and battery 99%.

The final probe output, supplied by the user, included:

```text
capture: LIBUSB_SUCCESS / LIBUSB_TRANSFER_COMPLETED
claim: LIBUSB_SUCCESS / LIBUSB_TRANSFER_COMPLETED
INQUIRY CBW: LIBUSB_SUCCESS / LIBUSB_TRANSFER_COMPLETED, transferred=31
INQUIRY data: LIBUSB_SUCCESS / LIBUSB_TRANSFER_COMPLETED, transferred=36
INQUIRY CSW: LIBUSB_SUCCESS / LIBUSB_TRANSFER_COMPLETED, transferred=13
INQUIRY: peripheral_type=00 vendor=BR25     product=UDISK            revision=1.00
release: LIBUSB_SUCCESS / LIBUSB_TRANSFER_COMPLETED
restore drivers: LIBUSB_SUCCESS / LIBUSB_TRANSFER_COMPLETED
```

`BR25` is useful family evidence. The reference chip database maps that family
to AC636N, AC696N, and AC608N; it does not uniquely establish the silicon model
or flash capacity. The earlier local reverse-engineering note assuming this
Free 3 was AC632N should not be used as a flashing precondition.

## Recommended implementation

Keep the existing JieLi command layer and add a USB BOT implementation of
`execute(cdb, data_out, data_in_len, timeout_ms)`. Select that transport on
macOS and retain `SG_IO` on Linux. Normal `info` access remains serial.

For a first CLI implementation, an optional PyUSB/libusb backend run from the
user's Terminal with `sudo` is a small integration step. A small native helper
is the better distribution boundary: parse and validate firmware as the normal
user, and give the helper only bounded device-transfer requests. It must check
the selected device identity, CDB length, direction, transfer size and timeout.
Do not expose arbitrary file execution through that helper.

The transport must:

- Discover the chosen device by USB identity/topology, then find its BOT
  interface and endpoints from descriptors. Re-discover after mode changes;
  a USB bus/address is not a persistent identifier. Download-mode PID and
  descriptors still need to be observed on this unit.
- Close the serial connection before capture. On macOS the capture operation
  affects the whole composite device, so CDC and HID can disappear too.
- Capture with libusb, allow time for interfaces to reappear, and claim the
  storage interface. Administrator rights are sufficient for the tested probe;
  it required no custom kernel extension or SIP change.
- Send the existing CDB inside a 31-byte BOT command wrapper, perform the
  requested data phase, and consume the 13-byte status wrapper. Check tag,
  signature, residue, command status and actual byte counts; serialize commands.
- Handle stalls and protocol errors deliberately. Avoid automatic replay of
  erase/program commands after an ambiguous timeout. USB reset/re-enumeration
  can change download-mode state, so recovery needs to be explicit.
- Release interfaces and define driver-restoration behavior at session end.
  The INQUIRY probe restores drivers. A flash session must not reset the device
  midway through loader installation or programming.

These packet sizes and phases come from the
[USB-IF BOT specification](https://www.usb.org/sites/default/files/usbmassbulk_10.pdf).
No filesystem implementation is required: the intended operations send vendor
SCSI commands rather than read or write mounted files.

## Existing macOS implementation to study

[M-Flasher](https://github.com/jvitkauskas/m-flasher) implements this route for
other JieLi hardware. Its authors report macOS backup/write/restore tests on an
M3 Mac with an SMK-37 Pro. This is supporting evidence for the transport design,
not Hanlinyue compatibility. See its
[development notes](https://github.com/jvitkauskas/m-flasher/blob/main/docs/development.md).

The inspected checkout is commit `0c5d50fe40a1c913c3408b9dafb33fbb88ad5d69`:

- [`src/usb.rs`](https://github.com/jvitkauskas/m-flasher/blob/0c5d50fe40a1c913c3408b9dafb33fbb88ad5d69/src/usb.rs)
  implements libusb capture, delayed interface claiming, BOT transfers and
  error recovery. Its hardcoded PID and WL82-specific assumptions do not apply
  directly to this page turner.
- [`src/macos.rs`](https://github.com/jvitkauskas/m-flasher/blob/0c5d50fe40a1c913c3408b9dafb33fbb88ad5d69/src/macos.rs)
  keeps a normal-user frontend connected to a root helper. Its comments discuss
  macOS removable-volume privacy prompts and process ancestry; its helper uses
  deprecated `AuthorizationExecuteWithPrivileges`. Prefer Terminal `sudo` for
  the first implementation and investigate supported helper deployment before
  shipping a GUI.

M-Flasher's application source is MIT licensed; bundled loader files have
separate notices. Its WL82 flash loader must not be uploaded to a BR25 device.

Apple documents [device capture](https://developer.apple.com/documentation/iousbhost/iousbhostobjectinitoptions/devicecapture)
with root privileges or an authorized `com.apple.vm.device-access` entitlement.
The [libusb FAQ](https://github.com/libusb/libusb/wiki/FAQ#how-can-i-run-libusb-applications-under-macos-if-there-is-already-a-kernel-extension-installed-for-the-device-and-claim-exclusive-access)
documents macOS capture support since 1.0.25. The installed SDK headers and
[libusb 1.0.30 backend](https://github.com/libusb/libusb/blob/v1.0.30/libusb/os/darwin_usb.c)
confirm the privileged capture path. The successful local root probe is the
strongest evidence that it applies to this device and host.

## Protocol work still required

Transport availability removes one blocker. Enabling the current `flash`
function on macOS without further work would still be premature.

1. **Establish download-mode entry.** The local vendor DLL's
   `find_and_enter_uboot` calls `cmd_online_device` (`FC0A`) and polls device
   identification again. The upstream `JL_MSCDevice.open` also tries an online
   query on non-UBOOT devices. This makes `FC0A` a concrete candidate for the
   normal-mode handshake. It was not sent during these probes; it could change
   mode, and its actual behavior/arguments need a controlled experiment or
   capture of the Windows updater. An `UDISK` INQUIRY result alone is not proof
   of loader capabilities.
2. **Identify and bootstrap the appropriate loader.** The
   [reference loader database](https://github.com/kagaimiq/jl-uboot-tool/blob/main/data/usb-loaders.yaml)
   specifies BR25 RAM address `0x12000`, MengLi transport scrambling, and
   512-byte upload blocks. BD19 uses a different address (`0x2000`). These are
   reference settings, not a validated prescription for this unit. ROM UBOOT
   needs a RAM loader before flash commands are available; the current project
   only checks the UBOOT product prefix and does not install one.
3. **Validate flash geometry and data handling.** Query loader capabilities,
   flash ID/capacity and transfer limits; do not assume the fixed 256 KiB backup
   length or 4 KiB transfer size is correct. Establish how vendor firmware and
   device-specific settings/keys are written and preserved. The local vendor
   updater has additional authentication, configuration and write stages that
   the minimal project does not reproduce.
4. **Back up and compare before programming.** Read the full flash twice and
   compare bytes/hashes, check package structure and chip compatibility, then
   validate programming and byte-for-byte readback under a separate write
   operation. The existing structural-validation and short-transfer issues in
   `HARDWARE_TEST_REPORT.md` also need repair.

Suggested delivery order: read-only `usb-probe` → mode/loader investigation →
verified full `dump` → firmware compatibility and preservation checks → write
support. Keep the CLI's write guard until those stages are established.

## Reproducing the safe probe

For the distributed CLI, use `hlyflash probe` (or `sudo hlyflash probe` when USB
permissions require it). It performs the read-only INQUIRY check without the
historical C probes described below. Those local research sources are not
required to install or use hlyflash.


The local probe source and binary are under the existing gitignored directory
`reverse-engineering/hardware-tests/`. The C program only contains the standard
SCSI INQUIRY command; it contains no vendor query, mode-switch, loader-upload,
erase, or firmware-write command. Its capture option temporarily detaches
drivers for this one VID/PID and restores them on exit.

Build against the already installed Homebrew libusb:

```sh
clang -Wall -Wextra -Werror -I/opt/homebrew/include/libusb-1.0 \
  reverse-engineering/hardware-tests/macos_usb_probe.c \
  -L/opt/homebrew/lib -lusb-1.0 \
  -o reverse-engineering/hardware-tests/macos_usb_probe
```

Run from the user's Terminal:

```sh
sudo reverse-engineering/hardware-tests/macos_usb_probe --capture
```

The compiled `macos_scsi_probe` separately tests native SCSITask plugin
availability without sending device commands. Neither research probe is yet
integrated into the installed `hlyflash` CLI.
