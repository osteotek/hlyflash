# Free 2 support and hardware validation

Tested on 2026-10-29, macOS 27.2 / Apple Silicon. The connected device was
upgraded from **Free 2 / V0.15 to V0.22**, with its original MAC unchanged after
testing. USB identity is **BR25 / UDISK / 1.00**; the RAM loader identifies flash
`0xcd7013`, capacity **524288 bytes**. The device uses the same bundled,
hash-verified BR25 RAM loader as the tested Free 3.

## Hardware checks

- Normal-mode model/firmware/MAC/battery queries passed before and after testing.
- Device-detected `download --list --port /dev/cu.usbmodem1103` selected the
  original Free 2 catalog and listed all seven variants with normalized dates.
- USB INQUIRY, FC0A ROM entry, verified loader upload/readback and startup passed.
- A full 512 KiB backup passed two independent reads. SHA-256:
  `53af20f2d3d7a342779495f637e3be115c07a946deaa049fa9fc4a5c9560a427`.
- `flash F0AFLJ.ufw --dry-run` passed on the real device, reporting **46 sectors**
  would change and no flash writes. Its second full backup matches the original
  hash above. The proposed target has SHA-256
  `cb4dbe381a916d0db2f527e70fe40fa36bf15d0000b9146651d5c31f4da908a3`.
- The entire boot sector and every byte from `0x41000` through `0x7ffff` are
  identical between the verified backup and proposed target. The existing VM
  starts at `0x41000`; BTIF starts at `0x7f000`. This unit has no EXIF entry.

The user then ran the actual F0AFLJ write with a fresh backup path. Programming
of **46 sectors** and full 512 KiB readback verification passed. The saved write
backup and target hashes match the values above; the manifest records an actual
write, and the target independently validates with the boot sector and data
region preserved. The user's subsequent normal-mode serial query reported
**Free 2 / V0.22 / 100% battery**, with the same MAC as V0.15. This verifies
programming, readback and boot/serial operation on this BR25 Free 2. Bluetooth
pairing and physical button/page-turn behavior under V0.22 remain untested.

## Implementation fixes

1. Model aliases such as `Free02`, `Free2_2026`, `Free22026` and parenthesized
   generation names select the correct catalog. Unknown future generations and
   unrelated names are not silently matched to the original Free 2.
2. Catalog parsing handles short/dotted publication dates, nested title tooltips,
   `按键方式` button labels and `基础说明` release notes. Dates normalize to ISO.
3. JLFS validation traverses the application directory's children even when the
   parent record has its own nonzero terminator. The Free 2 backup uses this
   convention; it previously stopped before finding app.bin.
4. Application-only conversion accepts the known six-entry layout without EXIF,
   retains the installed parent terminator and partition descriptors, and
   bounds the absent second slot independently of the trimmed package size.
   Populated/overlapping second-bank layouts remain unsupported.
5. The installed bootloader must match. The sole allowed boot-configuration
   difference is the 0/1 `UPDATE_JUMP` updater-entry option; its installed value
   and entire boot sector remain unchanged. All other configuration bytes,
   including SPI/reset/clock fields, must match. The source configuration CRC
   and cfg_tool.bin payload are checked. Jieli documents this setting's role in
   [updater entry and IO-state retention](https://doc.zh-jieli.com/KDocs/master/%E9%9F%B3%E9%A2%91%E6%8A%80%E6%9C%AF%E5%BA%94%E7%94%A8%E6%96%87%E6%A1%A3%E5%85%A5%E5%8F%A3%E6%B1%87%E6%80%BB/%E5%8D%87%E7%BA%A7%E7%9B%B8%E5%85%B3/).

## Firmware and automated coverage

The live [original Free 2 catalog](https://hanlinyue.com.cn/1755595121764) lists
seven packages: A0B7LJ, FBF4LJ, F0AFLJ, F2-478DLJ, F2-462CLJ, 69FDLJ and 31DDLJ.
All seven fresh downloads passed container and understood-payload checksums,
conversion against the actual backup, and NOR erase/program/readback simulation.
The [2026 catalog](https://hanlinyue.com.cn/1766830643878) lists 12A0LJ and B130LJ;
both parse correctly and are rejected against this original unit's hardware
configuration. No Free 2 (2026) hardware was connected.

Source and wheel builds passed; all 58 automated tests also passed against code
and resources imported directly from the built wheel. Regression against the original Free 3 backup
produces the exact previously hardware-verified V0.25 target:
`d3f15a9eab22be5081ad5e7c359604b2912a7c65680a81269ea388b61644f27f`.

Older BD19/AC632N Free 2 hardware remains untested. Its loader is external, and
physical-image validation/conversion currently targets BR25/JLFS version 3.
Firmware catalog support is not a claim of validated flashing on every revision.

Private backups, downloaded packages and machine-readable evidence stay under
the gitignored `reverse-engineering/hardware-tests/` directory. Web Serial in
the vendor app must be disconnected (or its tab closed) before serial queries;
the browser can reconnect automatically after USB driver restoration.
