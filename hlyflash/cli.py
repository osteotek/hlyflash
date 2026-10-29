"""Command-line entry point for hlyflash."""
from __future__ import annotations

import argparse
import sys

from . import ufw as ufwmod
from . import serialinfo


def cmd_info(args) -> int:
    ports = [args.port] if args.port else serialinfo.find_ports()
    if not ports:
        print("No serial port found. Connect the device (normal mode) and retry.",
              file=sys.stderr)
        return 2
    for port in ports:
        try:
            info = serialinfo.query(port)
        except Exception as e:
            print(f"{port}: error: {e}", file=sys.stderr)
            continue
        if not info:
            print(f"{port}: no response (is this the page turner?)", file=sys.stderr)
            continue
        print(f"Device on {port}:")
        for k in ("model", "firmware", "mac", "battery"):
            if k in info:
                suffix = "%" if k == "battery" else ""
                print(f"  {k:<9}: {info[k]}{suffix}")
        return 0
    return 1


def cmd_ufw(args) -> int:
    u = ufwmod.load(args.file)
    ok = u.header_crc_ok and u.table_crc_ok
    print(f"file   : {args.file}")
    print(f"chip   : {u.chip}")
    print(f"size   : {u.size:#x} ({u.size} bytes)")
    print(f"header CRC : {'ok' if u.header_crc_ok else 'BAD'}")
    print(f"table  CRC : {'ok' if u.table_crc_ok else 'BAD'}")
    print(f"entries: {len(u.entries)}")
    print(f"  {'#':>2} {'type':>5} {'offset':>9} {'size':>9}  crc   name")
    # Entries whose payload CRC we know how to verify; others are informational.
    KNOWN = {"flash.bin", "ota.bin", "tail.bin", "info.log"}
    for e in u.entries:
        if e.name in KNOWN:
            cok = "ok" if e.crc_ok(u.raw) else "BAD"
            ok = ok and e.crc_ok(u.raw)
        else:
            cok = "-"
        print(f"  {e.index:>2} {e.type:>#5x} {e.offset:>#9x} {e.size:>#9x}  {cok:>3}   {e.name}")

    if args.extract:
        import os
        os.makedirs(args.extract, exist_ok=True)
        for e in u.entries:
            if e.size == 0:
                continue
            path = os.path.join(args.extract, e.name)
            with open(path, "wb") as f:
                f.write(e.payload(u.raw))
            print(f"  extracted {e.name} -> {path}")

    print("RESULT :", "VALID" if ok else "CHECKSUM MISMATCH")
    return 0 if ok else 1


def _require_linux() -> None:
    if not sys.platform.startswith("linux"):
        print("This command needs raw SCSI access and is Linux-only.\n"
              "On macOS/Windows use the vendor Windows updater in a VM "
              "(see README).", file=sys.stderr)
        raise SystemExit(3)


def cmd_dump(args) -> int:
    _require_linux()
    from . import flasher
    try:
        flasher.dump(args.node, args.out, args.length)
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"saved backup -> {args.out}")
    return 0


def cmd_flash(args) -> int:
    _require_linux()
    from . import flasher
    u = ufwmod.load(args.file)
    print(f"UFW chip: {u.chip}  size: {u.size:#x}  "
          f"valid: {u.header_crc_ok and u.table_crc_ok}")
    if not args.force:
        print("\nThis WRITES your device's flash and can brick it if wrong "
              "or interrupted.\nThis write path is EXPERIMENTAL and unverified "
              "on hardware.\nBack up first:  hlyflash dump --node <sg> --out backup.bin\n"
              "Re-run with --force to proceed at your own risk.", file=sys.stderr)
        return 2
    try:
        flasher.flash_ufw(args.node, args.file)
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print("flash complete.")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="hlyflash",
        description="Tools for Hanlinyue / JieLi AC632N Bluetooth page turners.")
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("info", help="read model/firmware/MAC/battery over serial (safe)")
    pi.add_argument("-p", "--port", help="serial port (default: auto-detect)")
    pi.set_defaults(func=cmd_info)

    pu = sub.add_parser("ufw", help="parse and verify a .ufw firmware file")
    pu.add_argument("file", help="path to .ufw")
    pu.add_argument("-x", "--extract", metavar="DIR",
                    help="extract payloads into DIR")
    pu.set_defaults(func=cmd_ufw)

    pd = sub.add_parser("dump", help="[linux] back up device flash (non-destructive)")
    pd.add_argument("--node", required=True, help="SCSI node, e.g. /dev/sg1 or /dev/sdb")
    pd.add_argument("--out", required=True, help="output backup file")
    pd.add_argument("--length", type=lambda x: int(x, 0), default=0x40000,
                    help="bytes to read (default 0x40000)")
    pd.set_defaults(func=cmd_dump)

    pf = sub.add_parser("flash", help="[linux] write a .ufw to the device (EXPERIMENTAL)")
    pf.add_argument("file", help="path to .ufw")
    pf.add_argument("--node", required=True, help="SCSI node of the UBOOT device")
    pf.add_argument("--force", action="store_true", help="really write (brick risk)")
    pf.set_defaults(func=cmd_flash)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
