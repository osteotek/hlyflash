"""Command-line entry point for hlyflash."""
from __future__ import annotations

import argparse
import sys

from . import ufw as ufwmod
from . import serialinfo
from . import firmware as fwmod


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


def _detect_model() -> str | None:
    """Try to read the model from a connected device (normal mode)."""
    for port in serialinfo.find_ports():
        try:
            info = serialinfo.query(port, fields=("model",))
        except Exception:
            continue
        if info.get("model"):
            return info["model"]
    return None


def cmd_download(args) -> int:
    model_name = args.model
    if not model_name:
        model_name = _detect_model()
        if model_name:
            print(f"detected device model: {model_name}")
    if not model_name:
        print("No --model given and no device detected. "
              f"Known models: {', '.join(fwmod.MODELS)}", file=sys.stderr)
        return 2
    key = fwmod.resolve_model(model_name)
    if not key:
        print(f"Unknown model {model_name!r}. "
              f"Known: {', '.join(fwmod.MODELS)}", file=sys.stderr)
        return 2

    disp = fwmod.MODELS[key][0]
    try:
        variants = fwmod.list_variants(key)
    except Exception as e:
        print(f"Could not fetch firmware list: {e}", file=sys.stderr)
        return 1
    if not variants:
        print(f"No firmware variants found for {disp}.", file=sys.stderr)
        return 1

    chosen = None
    if args.id:
        for v in variants:
            if v.fw_id.lower() == args.id.lower():
                chosen = v
                break
        if not chosen:
            print(f"No variant with id {args.id!r} for {disp}.", file=sys.stderr)
            args.list = True

    if chosen is None or args.list:
        print(f"\n{disp}: {len(variants)} firmware variant(s). "
              "Pick one with --id <ID> that matches YOUR e-reader + button layout:\n")
        for v in variants:
            print(f"  ID {v.fw_id}")
            if v.devices:
                print(f"     devices : {v.devices}")
            if v.direction:
                print(f"     buttons : {v.direction}")
            if v.changelog:
                print(f"     changes : {v.changelog}")
            print(f"     url     : {v.url}")
            print()
        if chosen is None:
            print("Re-run with:  hlyflash download --model "
                  f"{key} --id <ID>", file=sys.stderr)
            return 0

    out = args.out or (chosen.fw_id + ".ufw")
    print(f"downloading {chosen.fw_id} -> {out}")
    try:
        n = fwmod.download(chosen.url, out)
    except Exception as e:
        print(f"download failed: {e}", file=sys.stderr)
        return 1

    # verify what we just downloaded with our own parser
    u = ufwmod.load(out)
    ok = u.header_crc_ok and u.table_crc_ok
    img = u.entry("flash.bin")
    if img is not None:
        ok = ok and img.crc_ok(u.raw)
    print(f"saved {n} bytes  chip={u.chip}  verified={'yes' if ok else 'NO'}")
    if not ok:
        print("WARNING: downloaded file failed checksum verification.",
              file=sys.stderr)
        return 1
    print("\nNext: verify your device's chip matches "
          f"'{u.chip}' before flashing (hardware revisions differ).")
    return 0


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
        description="Tools for Hanlinyue / JieLi AC632N/AC636N Bluetooth page turners.")
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("info", help="read model/firmware/MAC/battery over serial (safe)")
    pi.add_argument("-p", "--port", help="serial port (default: auto-detect)")
    pi.set_defaults(func=cmd_info)

    pu = sub.add_parser("ufw", help="parse and verify a .ufw firmware file")
    pu.add_argument("file", help="path to .ufw")
    pu.add_argument("-x", "--extract", metavar="DIR",
                    help="extract payloads into DIR")
    pu.set_defaults(func=cmd_ufw)

    pdl = sub.add_parser("download", help="list / download official firmware for a model")
    pdl.add_argument("-m", "--model", help="model name (default: auto-detect from device)")
    pdl.add_argument("--id", help="firmware ID to download (from --list)")
    pdl.add_argument("--list", action="store_true", help="just list variants")
    pdl.add_argument("-o", "--out", help="output path (default: <ID>.ufw)")
    pdl.add_argument("-p", "--port", help="serial port for model auto-detect")
    pdl.set_defaults(func=cmd_download)

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
