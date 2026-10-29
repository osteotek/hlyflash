# Project research and USB hardware validation

Tested 2026-10-29 against repository baseline `fd8d362`, with the fixes described
below applied. Host: macOS 27.2, Apple Silicon; project virtual environment:
Python 3.11.17, pyserial 3.5.

Later implementation and write-test results are recorded in
`MACOS_FLASHING_RESEARCH.md` under “Implementation follow-up.” The historical
findings below describe the initial baseline. USB transport, full backup/raw
restore, SCSI residual checking, UFW bounds checks and safe extraction have
since been implemented. Native physical program/erase/readback has passed on
the Free 3. A later constrained application-only update of official general
firmware 5A0FLJ from V0.09 to V0.25 passed full readback and live boot/serial
verification with the same MAC. Bluetooth pairing and button behavior remain
untested. Direct programming of staged UFW data is still rejected.

The connected device successfully answers all four supported serial queries.
It reports **Free 3**, firmware **V0.09**, battery **100%**, and a MAC address.
Five consecutive CLI runs passed after fixing response framing. At that initial
stage, flash backup and firmware writing had not yet been validated; the later
physical write-test results are linked above. After that test, a fresh serial
query again returned Free 3 / V0.09 / 100% battery.

## How the project works

| Layer | Implementation | Behavior |
|---|---|---|
| CLI | `hlyflash/cli.py` | Routes `info`, `download`, `ufw`, `dump`, and `flash`; rejects the last two on macOS before opening a device. |
| Normal device access | `hlyflash/serialinfo.py` | Opens USB CDC serial at 460800 baud; sends JSON `get` operations for model, firmware, MAC, and battery. Waits through startup drops and retries missing fields. |
| Firmware discovery | `hlyflash/firmware.py` | Maps model names to vendor pages, scrapes firmware cards, and downloads `.ufw` files from the vendor CDN. |
| Package parsing | `hlyflash/ufw.py`, `hlyflash/enc.py` | Decodes the 64-byte header and individual 80-byte table records with the JieLi ENC scrambler. Checks CRC16 values and exposes payloads, including `flash.bin`. The table CRC covers the encoded table. |
| Download-mode commands | `hlyflash/loader.py` | Encodes vendor SCSI commands: 0xFB write/erase, 0xFC control/query, 0xFD read. This is separate from serial JSON. |
| Linux transport | `hlyflash/scsi.py` | Uses `SG_IO` via `fcntl.ioctl` and a ctypes SCSI request structure. |
| Backup/write operations | `hlyflash/flasher.py` | Requires an INQUIRY product beginning with `UBOOT`; reads flash in 4 KiB chunks, or erases/writes sectors and compares readback CRC16 values. |

The local vendor-app serial manager independently confirms VID/PID
`4c4a:4155`, the baud rate, and JSON requests without newline terminators.
The open-source [LoaderV2 protocol reference](https://github.com/kagaimiq/jl-uboot-tool/blob/main/docs/usb-loader-v2.md)
matches the command families and argument layouts used here. Protocol agreement
does not establish that this project's full write sequence works on this device.

## Hardware findings and fixes

USB enumeration showed a JieLi Technology composite device, VID/PID
`4c4a:4155`, with CDC port `/dev/cu.usbmodem1103`.

Before the fix, `hlyflash info --port /dev/cu.usbmodem1103` opened the port but
returned exit code 1 and “no response.” A raw capture showed complete responses
such as `{"o":"getresult","t":"firmware","v":"V0.09"}` with **no newline**.
The old parser only processed newline-terminated messages. It also discarded
unfinished data whenever it started the next field query.

`serialinfo.py` now decodes JSON incrementally, preserves partial responses
across reads and queries, handles concatenated objects and optional newlines,
and accepts only `getresult` objects. Deadlines use a monotonic clock. The probe
also observed startup drops: the first two requests received no reply, while
later requests did. The existing warm-up/retry strategy remains useful.

The documented installation initially failed with setuptools' “Multiple
top-level packages ... ['assets', 'hlyflash']” error. `pyproject.toml` now explicitly
packages `hlyflash`; README installation examples include the `serial` extra.
The corrected editable installation succeeded.

## Executed checks

| Check | Result |
|---|---|
| Raw read-only serial probe | Complete replies received without newline separators; spaced and compact requests both produced replies after startup. |
| Five fresh `info` invocations | 5/5 passed; three explicit-port runs and two auto-discovery runs. All returned model, firmware, MAC, and battery; each took about 4.54–4.59 seconds. |
| Device-based `download --list` | Passed; detected `Free 3` over USB and loaded the official firmware catalog. |
| Explicit-model firmware listing | Passed; found `5A0FLJ` and `18B2LJ` on the live vendor page. |
| Fresh download of `18B2LJ` | Passed; 499456 bytes, container target `AC636N`, header/table/flash checks passed. |
| Five real firmware samples | `12A0LJ`, `5A0FLJ`, `A0B7LJ`, `AB7ELJ`, and freshly downloaded `18B2LJ` passed header, table, and known-payload CRC checks. Samples span `AC632N` and `AC636N`. |
| Corruption probes on sample copies | Header, table, and flash-payload corruption detected for each of the five packages: 15/15 checks. |
| Extraction of fresh download | Seven nonempty entries extracted; each matched its original payload byte for byte. |
| CRC and ENC checks | CRC16 `123456789` = `0x31c3`; CRC8 = `0xa1`; ENC round trip recovered the input. |
| macOS `dump` and `flash` guards | Both returned exit code 3 before device access; no backup file created. No `--force` used. |
| New serial regression suite | Six tests passed: delimiter-free replies, fragments/combined replies, split UTF-8, invalid-line recovery, response filtering/port closure, and empty field lists. |

Re-run the regression suite after installing `.[serial]`:

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/hlyflash info --port /dev/cu.usbmodem1103
```

The fresh firmware package has SHA-256
`8600962818de000e0eb4acdef553b5247491f6ec6e8f8f6fe447a922649719c4`.
Auxiliary entry types are not all covered by the CLI's known-payload checks;
“VALID” does not establish every auxiliary payload's integrity or device compatibility.

## Remaining issues found during research

These are not fixed by this change. Reproductions used temporary local files
or mocked transport calls; none wrote to the hardware.

1. **UFW extraction permits path traversal.** `cmd_ufw` joins the entry name
   directly to the extraction directory. A copied firmware with a table entry
   named `../escaped.bin` and recomputed table/header CRCs wrote outside the
   selected directory and returned exit code 0. Normalize and constrain every
   output path before extraction.
2. **Firmware structure checks are incomplete.** Appending five bytes left all
   existing checksum checks passing despite a declared/actual size mismatch.
   Truncating the table to an 80-byte file produced an uncaught `IndexError`.
   Validate table length, declared size, and all payload ranges before use.
3. **SCSI short transfers and driver errors can be hidden.** A mocked `SG_IO`
   request with `resid=4` still returned the full requested 16 bytes; a nonzero
   `driver_status` alone also returned without error. Check transport status
   and transfer lengths before treating a dump or readback as complete.
4. **Model matching can choose the wrong hardware generation.**
   `resolve_model('Free 2 (2026)')` and
   `resolve_model('Free 2 2026 edition')` both returned `free2`, rather than
   `free2-2026`. The generic contains match wins too early. Prefer specific
   generation aliases before generic model aliases.
5. **`download --port` is ignored.** With `/dev/explicit` passed and discovery
   mocked to `/dev/auto`, the command queried `/dev/auto`. Pass the explicit
   port into `_detect_model`.
6. **The flash path does not identify the actual chip or flash capacity.**
   It checks the package and UBOOT prefix, then writes `flash.bin` at address
   zero. It does not enforce a chip match, a verified full backup, or capacity
   bounds. The normal-mode serial fields cannot establish the connected unit's
   chip family. A downloaded `AC636N` package is not proof of compatibility.
7. **UBOOT enumeration alone is insufficient.** The reference distinguishes
   ROM `UBOOT1.00`, which requires uploading a chip-specific RAM loader for
   flash operations, from more capable loaders. `open_loader` currently accepts
   either based on the product prefix and performs no loader bootstrap or
   capability negotiation. Default backup length is a fixed 256 KiB, not a
   detected flash size. These need resolution before claiming complete backup
   or write support on arbitrary hardware revisions.

## Scope and evidence

This run validated normal-mode USB communication, firmware discovery/download,
container checks, and extraction. It did not transition the device to UBOOT,
erase or write device flash, produce a flash backup, identify the silicon
revision, or test Bluetooth page-turn behavior. The macOS limitation is in the
project's implemented transport; this report does not claim that every possible
macOS USB/SCSI approach is impossible.

Local raw logs, device identifiers, downloaded firmware, extracted payloads,
and repeatable probe scripts remain under the existing gitignored directory
`reverse-engineering/hardware-tests/`. Key files are `probe_serial.json`,
`validate_hardware.json`, and `firmware-validation.json`. Proprietary artifacts
were not added to the tracked changes.

External references consulted:

- [Official Free 3 firmware page](https://hanlinyue.com.cn/1762607565939)
- [JieLi UBOOT tool](https://github.com/kagaimiq/jl-uboot-tool)
- [LoaderV2 protocol](https://github.com/kagaimiq/jl-uboot-tool/blob/main/docs/usb-loader-v2.md)
