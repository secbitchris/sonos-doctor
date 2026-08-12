"""CLI: python3 -m sonosdoctor <command>."""
import argparse
import json
import sys

from . import __version__, checks, snapshot as snapmod, store

SEV_ICON = {"crit": "✗ CRIT", "warn": "! WARN", "info": "· info"}


def print_summary(snap, findings):
    devices = snap.get("devices", [])
    print("=" * 76)
    print(f"SONOS DOCTOR v{__version__}   {snap['generated']}   from {snap['host']}")
    print("=" * 76)
    uni = snap.get("unifi") or {}
    print(f"\nPlayers: {len(devices)} (SSDP found {snap.get('discovered_count')})   "
          f"UniFi enrichment: {'yes' if uni.get('available') else 'no (' + str(uni.get('error')) + ')'}")
    print(f"\n  {'IP':<16}{'Room':<24}{'Model':<8}{'1400':<6}{'loss':<7}"
          f"{'jit':<7}{'ch':<4}{'ANI':<4}{'link'}")
    print("  " + "-" * 74)
    for d in devices:
        p = d.get("ping") or {}
        u = d.get("unifi") or {}
        # speaker's own view wins: UniFi calls every Sonos "wired" because
        # mesh MACs are learned on the bridging switch ports
        if d.get("wired_physical"):
            link = "wired"
        elif d.get("connection_type"):
            link = d["connection_type"].split(" ")[0]
        elif u.get("signal"):
            link = f"wifi {u.get('signal')}dBm"
        else:
            link = "?"
        if not d.get("wired_physical") and u.get("switch"):
            link += f" ← {u['switch']} p{u.get('sw_port')}"
        ok = {True: "yes", False: "NO", None: "-"}[d.get("tcp_1400_open")]
        print(f"  {str(d.get('ip')):<16}{str(d.get('room'))[:23]:<24}"
              f"{str(d.get('model_number') or '?'):<8}{ok:<6}"
              f"{str(p.get('loss_pct', '-')) + '%':<7}{str(p.get('jitter_ms', '-')):<7}"
              f"{str(d.get('channel', '-')):<4}{str(d.get('ani', '-')):<4}{link}")

    bridges = snap.get("bridge_points") or {}
    print(f"\nSonosNet bridge points ({len(bridges)}):")
    for where, ips in sorted(bridges.items()):
        print(f"  {where:<44} {len(ips)} device(s)")

    print(f"\nFindings: {len(findings)}"
          + ("  — all clear" if not findings else ""))
    for f in findings:
        print(f"  {SEV_ICON[f['severity']]}  [{f['code']}] {f['subject']}")
        print(f"          {f['message']}")
    print("=" * 76)


def cmd_snapshot(a):
    log = (lambda m: print(f"  … {m}", file=sys.stderr)) if not a.json else (lambda m: None)
    snap = snapmod.take_snapshot(ping_count=a.ping_count,
                                 discover_timeout=a.discover_timeout,
                                 use_unifi=not a.no_unifi,
                                 unifi_host=a.unifi_host,
                                 sweep_cidr=None if a.sweep in (None, "auto") else a.sweep,
                                 force_sweep=a.sweep is not None, log=log)
    conn = store.open_db(a.db)
    previous = store.previous_snapshot(conn, snap["generated"])
    findings = checks.run_checks(snap, previous)
    if a.dry_run:
        sid = None
    else:
        sid = store.save_snapshot(conn, snap, findings)
    if a.json:
        print(json.dumps({**snap, "_findings": findings, "_id": sid}, indent=1))
    else:
        print_summary(snap, findings)
        if sid:
            print(f"saved as snapshot #{sid} in {a.db or store.DEFAULT_DB}")
    return 2 if any(f["severity"] == "crit" for f in findings) else (
        1 if any(f["severity"] == "warn" for f in findings) else 0)


def cmd_report(a):
    conn = store.open_db(a.db)
    snap = store.get_snapshot(conn, a.id)
    if not snap:
        print("no snapshots in the database yet — run: sonos-doctor snapshot",
              file=sys.stderr)
        return 1
    if a.json:
        print(json.dumps(snap, indent=1))
    else:
        print_summary(snap, snap.get("_findings", []))
    return 0


def cmd_history(a):
    conn = store.open_db(a.db)
    for s in store.list_snapshots(conn, a.limit):
        print(f"#{s['id']:<5} {s['ts']}  {s['discovered']:>3} players  "
              f"crit={s['crits']} warn={s['warns']}  ({s['source']})")
    return 0


def cmd_import(a):
    conn = store.open_db(a.db)
    n = 0
    for path in a.files:
        try:
            with open(path) as f:
                snap = json.load(f)
        except Exception as e:
            print(f"  skip {path}: {e}", file=sys.stderr)
            continue
        sid = store.import_legacy_json(conn, snap)
        if sid:
            n += 1
            print(f"  imported {path} → snapshot #{sid}")
        else:
            print(f"  duplicate, skipped: {path}")
    print(f"{n} snapshot(s) imported")
    return 0


def cmd_serve(a):
    from . import web
    web.serve(a.db, a.bind, a.port)
    return 0


def cmd_selftest(a):
    import os
    import unittest
    tests = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "tests")
    if not os.path.isdir(tests):
        print("test suite not present in this build (packaged .pyz?) — "
              "run from the source tree", file=sys.stderr)
        return 1
    suite = unittest.defaultTestLoader.discover(tests)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    return 0 if result.wasSuccessful() else 1


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="sonos-doctor",
        description="Portable Sonos + network health diagnostics.")
    ap.add_argument("--db", default=None,
                    help=f"SQLite path (default {store.DEFAULT_DB})")
    # accept --db after the subcommand too; SUPPRESS keeps the subparser from
    # clobbering a value given before it
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db", default=argparse.SUPPRESS)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("snapshot", parents=[common],
                       help="collect, check, store; exit 0/1/2 = ok/warn/crit")
    s.add_argument("--json", action="store_true")
    s.add_argument("--no-unifi", action="store_true")
    s.add_argument("--unifi-host", default=None)
    s.add_argument("--ping-count", type=int, default=10)
    s.add_argument("--discover-timeout", type=float, default=4.0)
    s.add_argument("--sweep", metavar="CIDR", nargs="?", const="auto",
                   default=None,
                   help="also TCP-sweep this subnet for players (default: "
                        "auto-sweep the local /24 when SSDP finds nothing)")
    s.add_argument("--dry-run", action="store_true", help="don't write to the DB")
    s.set_defaults(fn=cmd_snapshot)

    s = sub.add_parser("report", parents=[common], help="print a stored snapshot (latest by default)")
    s.add_argument("--id", type=int, default=None)
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_report)

    s = sub.add_parser("history", parents=[common], help="list stored snapshots")
    s.add_argument("--limit", type=int, default=40)
    s.set_defaults(fn=cmd_history)

    s = sub.add_parser("import-legacy", parents=[common], help="ingest sonosdiag.py --json files")
    s.add_argument("files", nargs="+")
    s.set_defaults(fn=cmd_import)

    s = sub.add_parser("serve", parents=[common], help="web UI over the stored history")
    s.add_argument("--bind", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8090)
    s.set_defaults(fn=cmd_serve)

    s = sub.add_parser("selftest", parents=[common], help="run the bundled test suite")
    s.set_defaults(fn=cmd_selftest)

    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
