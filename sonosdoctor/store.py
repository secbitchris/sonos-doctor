"""SQLite history. Full snapshots as JSON blobs + flat tables for trends."""
import json
import os
import sqlite3

DEFAULT_DB = os.path.expanduser("~/.sonos-doctor/history.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshot(
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    host TEXT,
    discovered INTEGER,
    unifi_available INTEGER,
    source TEXT DEFAULT 'sonos-doctor',
    raw TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS device(
    snapshot_id INTEGER REFERENCES snapshot(id) ON DELETE CASCADE,
    mac TEXT, radio_mac TEXT, ip TEXT, room TEXT, model_number TEXT, sw TEXT,
    channel INTEGER, ani INTEGER, phy_errors INTEGER, noise_floor INTEGER,
    wired INTEGER, tcp_1400_open INTEGER, loss_pct REAL, avg_ms REAL,
    jitter_ms REAL, boot_seq INTEGER, signal INTEGER, satisfaction INTEGER,
    uptime INTEGER, switch TEXT, sw_port INTEGER, ap TEXT);
CREATE INDEX IF NOT EXISTS idx_device_mac ON device(mac, snapshot_id);
CREATE TABLE IF NOT EXISTS edge(
    snapshot_id INTEGER REFERENCES snapshot(id) ON DELETE CASCADE,
    src_mac TEXT, dst_mac TEXT, from_db INTEGER, to_db INTEGER,
    stp INTEGER, dst_resolved INTEGER);
CREATE TABLE IF NOT EXISTS finding(
    snapshot_id INTEGER REFERENCES snapshot(id) ON DELETE CASCADE,
    severity TEXT, code TEXT, subject TEXT, message TEXT);
"""


def open_db(path=None):
    path = path or DEFAULT_DB
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")   # cron writer + web reader coexist
    conn.executescript(SCHEMA)
    return conn


def _device_row(sid, d):
    p = d.get("ping") or {}
    u = d.get("unifi") or {}
    wired = u.get("wired")
    if wired is None:
        ct = (d.get("connection_type") or "").lower()
        wired = d.get("eth_link") == 1 or ct.startswith("wired") or None
    return (sid, (d.get("mac") or "").lower() or None, d.get("radio_mac"),
            d.get("ip"), d.get("room"), d.get("model_number"), d.get("sw"),
            d.get("channel"), d.get("ani"), d.get("phy_errors"),
            d.get("noise_floor"),
            None if wired is None else int(bool(wired)),
            None if d.get("tcp_1400_open") is None else int(d["tcp_1400_open"]),
            p.get("loss_pct"), p.get("avg_ms"), p.get("jitter_ms"),
            d.get("boot_seq"), u.get("signal"), u.get("satisfaction"),
            u.get("uptime"), u.get("switch"), u.get("sw_port"), u.get("ap"))


def save_snapshot(conn, snap, findings=(), source="sonos-doctor"):
    cur = conn.execute(
        "INSERT INTO snapshot(ts, host, discovered, unifi_available, source, raw)"
        " VALUES(?,?,?,?,?,?)",
        (snap.get("generated"), snap.get("host"), snap.get("discovered_count"),
         int(bool((snap.get("unifi") or {}).get("available"))), source,
         json.dumps(snap)))
    sid = cur.lastrowid
    conn.executemany(
        "INSERT INTO device VALUES(" + ",".join("?" * 23) + ")",
        [_device_row(sid, d) for d in snap.get("devices", [])])
    conn.executemany(
        "INSERT INTO edge VALUES(?,?,?,?,?,?,?)",
        [(sid, e["src_mac"], e["dst_mac"], e["from_db"], e["to_db"],
          e.get("stp"), int(bool(e.get("dst_resolved"))))
         for e in snap.get("matrix", [])])
    conn.executemany(
        "INSERT INTO finding VALUES(?,?,?,?,?)",
        [(sid, f["severity"], f["code"], f["subject"], f["message"])
         for f in findings])
    conn.commit()
    return sid


def list_snapshots(conn, limit=100):
    return [dict(r) for r in conn.execute(
        "SELECT id, ts, host, discovered, unifi_available, source,"
        " (SELECT COUNT(*) FROM finding f WHERE f.snapshot_id = s.id"
        "   AND severity='crit') AS crits,"
        " (SELECT COUNT(*) FROM finding f WHERE f.snapshot_id = s.id"
        "   AND severity='warn') AS warns"
        " FROM snapshot s ORDER BY ts DESC LIMIT ?", (limit,))]


def get_snapshot(conn, sid=None):
    """Full snapshot dict (raw JSON) + its findings. Latest if sid is None."""
    q = "SELECT * FROM snapshot"
    row = conn.execute(
        q + (" WHERE id=?" if sid else " ORDER BY ts DESC LIMIT 1"),
        (sid,) if sid else ()).fetchone()
    if not row:
        return None
    snap = json.loads(row["raw"])
    snap["_id"], snap["_source"] = row["id"], row["source"]
    snap["_findings"] = [dict(r) for r in conn.execute(
        "SELECT severity, code, subject, message FROM finding"
        " WHERE snapshot_id=? ORDER BY CASE severity WHEN 'crit' THEN 0"
        " WHEN 'warn' THEN 1 ELSE 2 END", (row["id"],))]
    return snap


def previous_snapshot(conn, before_ts):
    row = conn.execute(
        "SELECT id, raw FROM snapshot WHERE ts < ? ORDER BY ts DESC LIMIT 1",
        (before_ts,)).fetchone()
    if not row:
        return None
    snap = json.loads(row["raw"])
    snap["_findings"] = [dict(r) for r in conn.execute(
        "SELECT severity, code, subject, message FROM finding"
        " WHERE snapshot_id=?", (row["id"],))]
    return snap


def device_history(conn, mac, limit=500):
    return [dict(r) for r in conn.execute(
        "SELECT s.ts, d.loss_pct, d.avg_ms, d.jitter_ms, d.ani,"
        " d.noise_floor, d.phy_errors, d.boot_seq, d.signal"
        " FROM device d JOIN snapshot s ON s.id = d.snapshot_id"
        " WHERE d.mac = ? ORDER BY s.ts DESC LIMIT ?", (mac.lower(), limit))][::-1]


def prune(conn, keep_days=180):
    """Delete snapshots (and their rows, via cascade) older than keep_days."""
    import datetime
    cutoff = (datetime.datetime.now() -
              datetime.timedelta(days=keep_days)).strftime("%Y-%m-%dT%H:%M:%S")
    conn.execute("PRAGMA foreign_keys=ON")
    cur = conn.execute("DELETE FROM snapshot WHERE ts < ?", (cutoff,))
    conn.commit()
    conn.execute("VACUUM")
    return cur.rowcount


def import_legacy_json(conn, snap):
    """Ingest a sonosdiag.py --json snapshot (the legacy cron format)."""
    dup = conn.execute("SELECT 1 FROM snapshot WHERE ts=? AND source='sonosdiag'",
                       (snap.get("generated"),)).fetchone()
    if dup:
        return None
    return save_snapshot(conn, snap, findings=(), source="sonosdiag")
