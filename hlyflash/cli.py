"""Command-line entry point for hlyflash."""
from __future__ import annotations

import argparse
import json
import sys

from . import ufw as ufwmod
from . import serialinfo
from . import firmware as fwmod
from . import __version__


def cmd_doctor(args) -> int:
    from .doctor import run
    return run()


def cmd_setup(args) -> int:
    from .linux import setup
    try:
        return setup(args.group, args.print_rules)
    except KeyboardInterrupt:
        print("\nSetup interrupted. Run hlyflash setup again to finish.", file=sys.stderr)
        return 130


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
        if any(e.name in (".", "..") or any(c in e.name for c in "/\\:")
               for e in u.entries):
            raise ValueError("UFW contains an unsafe extraction filename")
        if not ok:
            raise ValueError("Refusing to extract firmware with invalid checksums")
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


def _detect_model(port=None) -> str | None:
    """Try to read the model from a connected device (normal mode)."""
    for port in ([port] if port else serialinfo.find_ports()):
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
        model_name = _detect_model(args.port)
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

    chosen = fwmod.latest_general(variants) if args.general else None
    missing_id = False
    if args.id:
        for v in variants:
            if v.fw_id.lower() == args.id.lower():
                chosen = v
                break
        if not chosen:
            print(f"No variant with id {args.id!r} for {disp}.", file=sys.stderr)
            args.list = True
            missing_id = True

    if chosen is None or args.list:
        print(f"\n{disp}: {len(variants)} firmware variant(s). "
              "Pick one with --id <ID> that matches YOUR e-reader + button layout:\n")
        for v in variants:
            print(f"  ID {v.fw_id}")
            if v.title or v.released:
                print(f"     release : {v.title} {v.released}".rstrip())
            if v.devices:
                print(f"     devices : {v.devices}")
            if v.direction:
                print(f"     buttons : {v.direction}")
            if v.changelog:
                print(f"     changes : {v.changelog}")
            print(f"     url     : {v.url}")
            print()
        if args.list:
            return 2 if missing_id else 0
        if chosen is None:
            print("Re-run with:  hlyflash download --model "
                  f"{key} --id <ID>", file=sys.stderr)
            return 0

    out = args.out or (chosen.fw_id + ".ufw")
    print(f"downloading {chosen.fw_id} ({chosen.title} {chosen.released}) -> {out}")
    try:
        n = fwmod.download(chosen.url, out)
    except Exception as e:
        print(f"download failed: {e}", file=sys.stderr)
        return 1

    # verify what we just downloaded with our own parser
    u = ufwmod.load(out)
    ok = u.header_crc_ok and u.table_crc_ok
    img = u.entry("flash.bin")
    ok = ok and img is not None and bool(img.size)
    ok = ok and all(e.crc_ok(u.raw) for e in u.entries
                    if e.name in {"flash.bin", "ota.bin", "tail.bin", "info.log"})
    print(f"saved {n} bytes  chip={u.chip}  verified={'yes' if ok else 'NO'}")
    if not ok:
        print("WARNING: downloaded file failed checksum verification.",
              file=sys.stderr)
        return 1
    print("\nNext: verify your device's chip matches "
          f"'{u.chip}' before flashing (hardware revisions differ).")
    return 0


def _device_node(args):
    if args.node:
        return args.node
    if sys.platform == "darwin" or sys.platform.startswith("linux"):
        return "usb"
    raise ValueError("Specify --node /dev/sgN or --node usb[:bus:address]")


def cmd_probe(args):
    from .scsi import SCSIError, open_device
    from . import flasher
    try:
        node = _device_node(args)
        if args.enter_boot or args.loader:
            ldr = flasher.open_loader(node, args.loader, args.enter_boot)
            try:
                result = {"family": ldr.family, "product": ldr.product,
                          "flash_id": hex(ldr.flash_id), "flash_size": ldr.flash_size,
                          "io_size": ldr.io_size,
                          "chip_key": (f"{ldr.chip_key_value:04x}"
                                       if ldr.chip_key_value is not None else None)}
            finally:
                ldr.dev.close()
        else:
            with open_device(node) as dev:
                vendor, product, revision = flasher.Loader(dev).inquiry()
                result = {"family": vendor, "product": product, "revision": revision}
        print(json.dumps(result, indent=2))
        return 0
    except SCSIError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


def cmd_dump(args) -> int:
    from . import flasher
    try:
        result = flasher.dump(_device_node(args), args.out, args.length,
                              loader_path=args.loader, enter_boot=args.enter_boot)
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"saved backup -> {args.out}")
    print(json.dumps(result, indent=2))
    return 0


def cmd_flash(args) -> int:
    from . import flasher
    u = ufwmod.load(args.file)
    print(f"UFW chip: {u.chip}  size: {u.size:#x}  "
          f"valid: {u.header_crc_ok and u.table_crc_ok}")
    if not args.force and not args.dry_run:
        print("\nThis WRITES your device's flash and can brick it if wrong "
              "or interrupted.\nStaged packages automatically use the experimental "
              "application-only conversion.\nA new verified full backup is saved to --backup before writing.\n"
              "Re-run with --force to proceed at your own risk.", file=sys.stderr)
        return 2
    try:
        sectors = flasher.flash_ufw(_device_node(args), args.file, args.backup,
                                    loader_path=args.loader, enter_boot=args.enter_boot,
                                    application_only=args.application_only, dry_run=args.dry_run)
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if args.dry_run:
        print(f"Dry run: {sectors} sectors would change; no flash writes performed.")
    else:
        print(f"flash complete: {sectors} changed sectors; full readback verified.")
    return 0


def cmd_restore(args):
    from . import flasher
    if not args.force and not args.dry_run:
        print("Restore writes a full physical dump, including settings and identity. "
              "Use your own device backup. Re-run with --force to write.", file=sys.stderr)
        return 2
    try:
        sectors = flasher.restore(_device_node(args), args.file, args.backup,
                                  loader_path=args.loader, enter_boot=args.enter_boot,
                                  dry_run=args.dry_run)
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if args.dry_run:
        print(f"Dry run: {sectors} sectors would change; no flash writes performed.")
    else:
        print(f"restore complete: {sectors} changed sectors; full readback verified.")
    return 0


def cmd_update(args):
    from . import updater
    from .scsi import SCSIError
    try:
        return updater.run(args)
    except updater.UpdateFailure as e:
        return updater.report_failure(e)
    except (ValueError, OSError, SCSIError) as e:
        return updater.report_failure(updater.UpdateFailure(e))
    except (EOFError, KeyboardInterrupt):
        return updater.report_failure(updater.UpdateFailure("Interrupted", interrupted=True))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="hlyflash",
        description="Tools for Hanlinyue / JieLi AC632N/AC636N Bluetooth page turners.")
    p.add_argument("--version", action="version", version=f"hlyflash {__version__}")
    sub = p.add_subparsers(dest="cmd")

    doctor = sub.add_parser("doctor", help="check installation without opening a device or requesting sudo")
    doctor.set_defaults(func=cmd_doctor)

    setup = sub.add_parser("setup", help="configure Linux USB and serial permissions once")
    setup.add_argument("--print-rules", action="store_true", help="show the udev rules without installing them")
    setup.add_argument("--group", help="also grant access to an existing group (for SSH / headless use)")
    setup.set_defaults(func=cmd_setup)

    pi = sub.add_parser("info", help="read model/firmware/MAC/battery over serial (safe)")
    pi.add_argument("-p", "--port", help="serial port (default: auto-detect)")
    pi.set_defaults(func=cmd_info)

    def device_args(parser):
        parser.add_argument("--node", help="usb[:bus:address] (default: usb); Linux also accepts /dev/sgN")
        parser.add_argument("--enter-boot", action="store_true",
                            help="request download mode from normal firmware via FC0A")
        parser.add_argument("--loader", help="RAM loader override (BR25 uses the bundled, hash-verified loader)")

    pp = sub.add_parser("probe", help="inspect SCSI identity or initialize a flash session")
    device_args(pp)
    pp.set_defaults(func=cmd_probe)

    pu = sub.add_parser("ufw", help="parse and verify a .ufw firmware file")
    pu.add_argument("file", help="path to .ufw")
    pu.add_argument("-x", "--extract", metavar="DIR",
                    help="extract payloads into DIR")
    pu.set_defaults(func=cmd_ufw)

    pdl = sub.add_parser("download", help="list / download official firmware for a model")
    pdl.add_argument("-m", "--model", help="model name (default: auto-detect from device)")
    selection = pdl.add_mutually_exclusive_group()
    selection.add_argument("--id", help="firmware ID to download (from --list)")
    selection.add_argument("--general", action="store_true",
                           help="download the latest general / multi-device release from the live catalog")
    pdl.add_argument("--list", action="store_true", help="just list variants")
    pdl.add_argument("-o", "--out", help="output path (default: <ID>.ufw)")
    pdl.add_argument("-p", "--port", help="serial port for model auto-detect")
    pdl.set_defaults(func=cmd_download)

    pup = sub.add_parser("update", help="select, download, back up and update the connected device")
    pup.add_argument("-p", "--port", help="USB serial port (default: detect one Hanlinyue device)")
    pup.add_argument("-m", "--model", help="optional expected model; must match the connected device")
    choice = pup.add_mutually_exclusive_group()
    choice.add_argument("--id", help="firmware variant ID (default: remember this device's choice)")
    choice.add_argument("--general", action="store_true", help="select the latest general / multi-device release")
    choice.add_argument("--choose", action="store_true", help="choose a different firmware variant")
    pup.add_argument("--check", "--dry-run", dest="check", action="store_true",
                     help="download and check the installed application without programming flash")
    pup.add_argument("-y", "--yes", action="store_true", help="skip the final write confirmation; all checks still apply")
    pup.add_argument("--data-dir", help="directory for downloaded firmware and automatic backups")
    pup.add_argument("--node", help="USB selector (default: selected serial device's physical USB port)")
    pup.add_argument("--loader", help="RAM loader override (BR25 loader is bundled)")
    pup.add_argument("--no-progress", action="store_true", help="disable animated loaders (automatic for redirected output)")
    pup.set_defaults(func=cmd_update)

    pd = sub.add_parser("dump", help="back up flash and verify against a second full read")
    device_args(pd)
    pd.add_argument("--out", required=True, help="output backup file")
    pd.add_argument("--length", type=lambda x: int(x, 0),
                    help="bytes to read (default: entire detected flash)")
    pd.set_defaults(func=cmd_dump)

    pf = sub.add_parser("flash", help="back up, write and verify a .ufw (EXPERIMENTAL)")
    device_args(pf)
    pf.add_argument("file", help="path to .ufw")
    pf.add_argument("--backup", required=True, help="new path for the verified full backup")
    pf.add_argument("--force", action="store_true", help="really write (brick risk)")
    pf.add_argument("--dry-run", action="store_true", help="save backup and validate the target without writing")
    pf.add_argument("--application-only", action="store_true", default=None,
                    help="require application-only conversion (default: automatically selected for staged packages)")
    pf.set_defaults(func=cmd_flash)

    pr = sub.add_parser("restore", help="restore a full physical device backup (EXPERIMENTAL)")
    device_args(pr)
    pr.add_argument("file", help="full physical flash dump from this device")
    pr.add_argument("--backup", required=True, help="new path to save current flash before restoration")
    pr.add_argument("--force", action="store_true", help="really write the physical image")
    pr.add_argument("--dry-run", action="store_true", help="verify backup and report changes without writing")
    pr.set_defaults(func=cmd_restore)

    args = p.parse_args(argv)
    if not hasattr(args, "func"):
        p.print_help()
        return 0
    try:
        return args.func(args)
    except (ValueError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
