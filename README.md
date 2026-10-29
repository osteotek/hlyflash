# hlyflash

Command-line tools for **Hanlinyue** Bluetooth page turners (Free 3 / E1 Pro /
Free 2 / etc.), which are built on **JieLi AC632N** chips. The vendor only ships
a Windows firmware updater; the macOS app deliberately disables firmware
updates. This project reconstructs the pieces needed to work with these devices
from macOS and Linux.

It was produced by reverse-engineering the vendor's own tools **for
interoperability / educational purposes**, on hardware the user owns.

## What works where

| Feature | macOS | Linux | How |
|---|---|---|---|
| Read model / firmware / MAC / battery | ✅ | ✅ | CDC serial JSON (read-only) |
| Parse / verify / extract `.ufw` firmware | ✅ | ✅ | pure Python |
| Back up device flash (`dump`) | ❌ | ✅ | SCSI `SG_IO` passthrough |
| Write firmware (`flash`) | ❌ | ⚠️ experimental | SCSI `SG_IO` passthrough |

**Why no flashing on macOS:** the firmware-update protocol runs over USB
Mass-Storage vendor SCSI commands. Linux exposes those via the `SG_IO` ioctl;
macOS claims mass-storage devices with its own driver and offers no equivalent
userspace passthrough. (The open-source `jl-uboot-tool` has the same limitation
— Linux/Windows only.) On macOS, run the vendor Windows updater in a VM with USB
passthrough, or use a Linux box.

## Install

```sh
pip install -e .          # or: pipx install .
# 'info' needs pyserial; 'ufw' needs nothing extra.
```

## Usage

```sh
# Safe, works now on macOS and Linux (device in normal mode):
hlyflash info
#   Device on /dev/cu.usbmodem1103:
#     model    : Free 3
#     firmware : V0.09
#     mac      : DD-14-33-87-28-A0
#     battery  : 100%

# Inspect / verify / extract a firmware package:
hlyflash ufw AB7ELJ.ufw
hlyflash ufw AB7ELJ.ufw -x ./extracted/

# Linux only — back up flash BEFORE doing anything destructive:
sudo hlyflash dump --node /dev/sg1 --out backup.bin

# Linux only, EXPERIMENTAL — write firmware (brick risk, read warnings):
sudo hlyflash flash AB7ELJ.ufw --node /dev/sg1 --force
```

Get official firmware from the vendor download page
(`https://hanlinyue.com.cn/` → downloads) and match it to your model.

## Entering download (UBOOT) mode

`dump`/`flash` require the chip to be in **UBOOT download mode**, where it
enumerates as a USB Mass-Storage device whose SCSI INQUIRY product string starts
with `UBOOT`. A JieLi chip enters this mode when:

- its flash is blank or the firmware image is invalid (auto-recovery), or
- it is forced in via the vendor "USB updater" dongle / key signal, or
- (model dependent) a button combination at power-on.

Find the resulting SCSI node with `lsscsi -g` or `dmesg` after it appears.
The app-triggered transition from normal mode was not reverse-engineered here;
if you only have normal (serial) mode, use the Windows updater in a VM.

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

`flash.bin` is the chip flash image (verified by CRC and written to flash offset
0). Other entries (`ota.bin`, `tail.bin`, config blobs) are auxiliary.

## Protocol notes

The download protocol is JieLi's standard Mass-Storage command set: 16-byte SCSI
CDBs carrying a 2-byte big-endian opcode plus args (0xFF-padded), with a 16-byte
response echoing the opcode. Families: `0xFB` write/erase, `0xFC` control/query,
`0xFD` read. This matches the documented
[`jl-uboot-tool`](https://github.com/kagaimiq/jl-uboot-tool) `LoaderV2`.

## Safety

Firmware writing can permanently brick the device. The `flash` path here is
**experimental and not validated end-to-end on hardware** — it follows the known
protocol but you use it at your own risk. Always `dump` a backup first, keep the
device powered and connected throughout, and prefer the vendor Windows updater
for routine updates.

## Credits

Protocol cross-checked against the open-source
[`jl-uboot-tool`](https://github.com/kagaimiq/jl-uboot-tool) and
[`jielie`](https://github.com/kagaimiq/jielie) projects by kagaimiq.
