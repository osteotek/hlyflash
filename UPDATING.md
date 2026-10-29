# Guided updates

Run `hlyflash update` with the device on and connected over a USB data cable.
Close the vendor app or browser device-control tab so it releases the serial
port. On Linux, first run `hlyflash setup` once and reconnect the device; see
[Linux setup](LINUX.md). No firmware filename, loader path, backup filename or boot-mode flag is
needed for a supported BR25 device.

## Flow

1. Identify one Hanlinyue USB serial device and read model, firmware, MAC and battery.
   With several connected, use `--port /dev/cu.usbmodem...`.
2. Fetch the model's live official catalog. On the first run, choose a variant
   by number or ID. Subsequent runs remember that device's selection; use
   `--choose` to change it. Device targets, buttons and dates appear in the menu.
3. Download and validate the selected package and payload checksums. On macOS,
   request administrator permission through sudo only when USB access is needed.
   Python never receives your password. After authentication the USB helper
   rechecks the physical USB location, model, MAC, battery and downloaded hash.
4. Bind USB access to the selected serial device's physical bus/port. Check chip,
   flash capacity and key; start the bundled verified RAM loader as needed.
5. Read the installed flash into memory and validate the exact proposed
   application-only target. Skip an exact match and refuse a lower version.
   Equal version numbers with different bytes are treated as variant changes.
   Already-current and check-only runs create no backup directory.
6. For an actual update, save a new full backup, compare it with a second read,
   and confirm it still matches the earlier snapshot. Save the exact target.
   Bootloader and data partitions are retained. Ask before the first erase.
7. Recheck the saved backup after confirmation, program changed sectors, verify
   all flash bytes, restore USB drivers and check the running model/firmware/MAC.
   Successful readback without a serial boot check is reported as unverified.

A Braille-dot spinner or progress bar occupies one transient terminal line.
Successful stages clear themselves; the final result stays visible. ASCII-only
terminals use a `|/-\` spinner. Redirected output has one plain line per stage,
without animation or repeated completion lines. `--no-progress` also disables
animation. Detailed loader output goes to `device.log` for prepared updates;
errors before preparation save a small diagnostic log under `logs/`.

## Options

- `--choose`: reopen the variant menu, overriding the remembered choice.
- `--id ID`: select an exact currently listed variant and remember it.
- `--general`: follow the newest explicitly labelled general/multi-device release.
- `--check` / `--dry-run`: download and compare in memory; no backup or flash writes.
- `--yes`: skip only the final write confirmation; all guards remain active.
- `--port PORT`: select a normal-mode device; USB follows its physical port.
- `--model NAME`: assert the expected model; reject a mismatch.
- `--data-dir DIR`: override the firmware, preferences and backup root.
- `--no-progress`: disable animated spinners and bars.
- `--node usb:B:A`: constrain USB selection further, still bound to the serial port.
- `--loader FILE`: override the bundled loader, subject to its hash check.

Non-interactive runs need a remembered choice, `--id`, or `--general`; `--yes`
never guesses a variant. The original Free 2 has specialized releases and no
general release. A remembered ID no longer in the catalog follows a replacement
only when its title, device targets and buttons match exactly and there is one
newest dated release. Otherwise selection is required again. Successful receipts
from older updater versions can seed the remembered ID for the same model/MAC.

Writing requires a readable battery level of at least 20%. Check-only mode does
not enforce that writing threshold. Invalid CRCs, unknown keys, wrong devices,
unsupported layouts, changed boot/configuration payloads, overlapping regions,
unstable backups and short USB transfers stop before programming. The serial
version must agree with the installed application. Compatibility limits remain
unchanged; `update` never falls back to raw programming. Automatic privilege
retry applies only to access denial before device commands, never to ambiguous
USB transport failures. Manual `flash` and `restore` still require sudo on macOS.

## Saved files and recovery

Default root:

- macOS: `~/Library/Application Support/hlyflash/`
- Linux: `$XDG_DATA_HOME/hlyflash/`, or `~/.local/share/hlyflash/`

Sudo uses the invoking user's home and ownership. New state/backup directories
are private. Downloads are cached by firmware ID plus content hash; every run
still fetches the live release. Modified cached files are rejected.
`preferences.json` stores choices keyed by model/MAC. `devices/` stores lightweight
last-check/update results. Check-only and already-current runs save no flash dumps.

Every prepared write gets its own backup directory containing:

- `backup.bin`: full, double-verified original flash; never overwritten.
- `backup.bin.target.bin`: exact proposed physical target.
- `backup.bin.json`: hashes, variant, versions, changed sectors and update result.
- `device.log`: protocol and loader diagnostics.

Failures say whether writes started or full readback passed. When the saved
backup is still valid, the error includes its path and a copyable `restore`
command with a fresh backup destination. Connect only the original device before
running recovery. A missing, modified or incomplete backup is never recommended
for restoration. If only the restart check fails, first close the vendor app,
reconnect and run `hlyflash info` as the error suggests.

## Validation

Guarded updates are exercised against a NOR-flash simulator, including checks
without backup creation, remembered choices, cancellation, stale identity after
sudo, modified downloads/backups, failed erase, mismatched ports, variant changes,
downgrade refusal and failed boot verification. Terminal tests cover Braille and
ASCII fallback, redirected output and transient progress cleanup.

Earlier hardware validation on the Free 2 confirmed F0AFLJ/V0.22 was already
installed, with zero changed sectors. Actual Free 2 and Free 3 writes were
verified through `flash` using the same backend.

On 2026-10-29, an interactive Free 3 check also passed through the new automatic
sudo handoff. The saved result confirms installed/selected V0.25, variant 5A0FLJ,
zero changed sectors, `write_started: false` and `status: up_to_date`. No new
backup directory was created. The selected variant was remembered, the result
file retained the invoking user's ownership, and the private privilege job was
cleaned up. This validates the new check path on hardware; writing through the
new handoff remains covered by simulation and the existing backend write tests.
