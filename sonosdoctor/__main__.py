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
            link = d["connection_type"].split(" ")[0].replace("Home", "HT-5GHz")
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


def _parse_ts(ts):
    import datetime
    try:
        return datetime.datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S")
    except Exception:
        return None


def _annotate_phy_rates(snap, previous):
    """PHY-error counters are cumulative; the per-hour delta is the trend."""
    if not previous:
        return
    t0, t1 = _parse_ts(previous.get("generated", "")), _parse_ts(snap["generated"])
    if not t0 or not t1 or t1 <= t0:
        return
    hours = (t1 - t0).total_seconds() / 3600
    prev = {(d.get("mac") or "").lower(): d.get("phy_errors")
            for d in previous.get("devices", [])}
    for d in snap.get("devices", []):
        p0 = prev.get((d.get("mac") or "").lower())
        p1 = d.get("phy_errors")
        if p0 is not None and p1 is not None and p1 >= p0:
            d["phy_err_per_h"] = round((p1 - p0) / hours)


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
    _annotate_phy_rates(snap, previous)
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
    if a.html:
        from . import web
        embedded = json.dumps({
            "snapshot": snap,
            "history": store.all_histories(conn),
            "timeline": store.timeline(conn),
        }).replace("</", "<\\/")      # keep </script> out of the inline JSON
        html = web.PAGE.replace(
            "<script>",
            f"<script>window.EMBEDDED = {embedded};</script>\n<script>", 1)
        print(html)
    elif a.json:
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


def cmd_watch(a):
    """On-site mode: snapshot repeatedly, print finding diffs live."""
    import time
    conn = store.open_db(a.db)
    prev_keys, n = None, 0
    print(f"watching every {a.interval}s (ping x{a.ping_count}) — Ctrl-C to stop",
          file=sys.stderr)
    try:
        while True:
            snap = snapmod.take_snapshot(ping_count=a.ping_count,
                                         use_unifi=not a.no_unifi,
                                         log=lambda m: None)
            previous = store.previous_snapshot(conn, snap["generated"])
            _annotate_phy_rates(snap, previous)
            findings = checks.run_checks(snap, previous)
            sid = store.save_snapshot(conn, snap, findings)
            keys = {(f["severity"], f["code"], f["subject"])
                    for f in findings if f["severity"] != "info"}
            crit = sum(1 for f in findings if f["severity"] == "crit")
            warn = sum(1 for f in findings if f["severity"] == "warn")
            jit = max([(d.get("ping") or {}).get("jitter_ms") or 0
                       for d in snap["devices"]] or [0])
            print(f"{snap['generated']}  #{sid}  {len(snap['devices'])} players"
                  f"  crit={crit} warn={warn}  worst jitter {jit} ms")
            if prev_keys is not None:
                for s_, c, j in sorted(keys - prev_keys):
                    print(f"   + {s_} [{c}] {j}")
                for s_, c, j in sorted(prev_keys - keys):
                    print(f"   − resolved [{c}] {j}")
            prev_keys = keys
            n += 1
            if a.count and n >= a.count:
                break
            time.sleep(a.interval)
    except KeyboardInterrupt:
        print("\nstopped", file=sys.stderr)
    return 0


def cmd_prune(a):
    conn = store.open_db(a.db)
    n = store.prune(conn, a.keep_days)
    print(f"pruned {n} snapshot(s) older than {a.keep_days} days")
    return 0


def _builtin_smoke():
    """Minimal field self-check for packaged builds (no test suite on disk)."""
    from . import checks, collect, review, stp, topology, web  # noqa: F401
    failures = []

    s = stp.parse_showstp(
        "br0\n bridge id\t\t9800.38420b000004\n"
        " designated root\t1000.28704e000001\n"
        " root port\t\t   2\t\t\tpath cost\t\t 248\n"
        "ath0 (2) - tunnel to AA:BB:CC:DD:EE:FF "
        "(remote STP state = forwarding, direct = 1)\n"
        " port id\t\t8002\t\t\tstate\t\t\tforwarding\n")
    if s.get("root_mac") != "28:70:4e:00:00:01" or s.get("root_prio") != 4096:
        failures.append("showstp parser")

    p = review.parse_review(
        "<ZPSupportInfo><ZoneName>X</ZoneName><MACAddress>AA:BB:CC:00:11:22"
        "</MACAddress><SoftwareVersion>57.19-1</SoftwareVersion>"
        "<File name='/proc/ath_rincon/status'>IEEE channel: 6\n"
        "OFDM ANI level: 4\nNode 11:22:33:44:55:66 - FROM 30 : TO 20 : STP 00"
        " : MODEL 1.1: KEY 1\n</File></ZPSupportInfo>")
    if not p or p[0].get("swgen") != "1" or not p[0]["neighbours"]:
        failures.append("review parser / S1 detection")

    b = collect.parse_battery(
        '<ZPSupportInfo><LocalBatteryStatus>'
        '<Data name="Level">17</Data><Data name="PowerSource">BATTERY</Data>'
        '</LocalBatteryStatus></ZPSupportInfo>')
    if not b or b.get("level") != 17:
        failures.append("battery parser")

    snap = {"devices": [{"ip": "1.2.3.4", "mac": "aa:aa:aa:aa:aa:01",
                         "room": "T", "tcp_1400_open": True, "ping": {},
                         "stp": {"root_prio": 32768,
                                 "root_mac": "aa:aa:aa:aa:aa:01",
                                 "ports": []}}],
            "matrix": [], "unifi": {}}
    if "stp-root-is-sonos" not in {f["code"] for f in checks.run_checks(snap)}:
        failures.append("checks engine (root-bridge detector)")

    for f in failures:
        print(f"  FAIL {f}", file=sys.stderr)
    print(f"built-in smoke: {'OK (4/4)' if not failures else 'FAILED'}")
    return 0 if not failures else 1


def cmd_selftest(a):
    import os
    import unittest
    tests = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "tests")
    if not os.path.isdir(tests):
        print("packaged build — running built-in smoke instead of the full "
              "suite", file=sys.stderr)
        return _builtin_smoke()
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
    s.add_argument("--html", action="store_true",
                   help="self-contained HTML report on stdout (leave-behind)")
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

    s = sub.add_parser("watch", parents=[common],
                       help="on-site mode: snapshot every N seconds, "
                            "print finding diffs live")
    s.add_argument("--interval", type=int, default=120)
    s.add_argument("--count", type=int, default=0, help="stop after N (0=forever)")
    s.add_argument("--ping-count", type=int, default=5)
    s.add_argument("--no-unifi", action="store_true")
    s.set_defaults(fn=cmd_watch)

    s = sub.add_parser("prune", parents=[common],
                       help="delete snapshots older than --keep-days")
    s.add_argument("--keep-days", type=int, default=180)
    s.set_defaults(fn=cmd_prune)

    s = sub.add_parser("selftest", parents=[common], help="run the bundled test suite")
    s.set_defaults(fn=cmd_selftest)

    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
