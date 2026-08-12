"""SQLite store round-trip, legacy import dedupe, history queries."""
import json
import os
import tempfile
import unittest

from sonosdoctor import store

SNAP = {
    "generated": "2026-08-12T12:00:00-0400", "host": "test",
    "discovered_count": 2,
    "unifi": {"available": False},
    "devices": [
        {"ip": "192.168.1.50", "mac": "AA:AA:AA:AA:AA:01",
         "radio_mac": "aa:aa:aa:aa:aa:02", "room": "A", "model_number": "S12",
         "tcp_1400_open": True, "channel": 6, "ani": 4, "boot_seq": 7,
         "ping": {"loss_pct": 0.0, "avg_ms": 2.5, "jitter_ms": 1.1},
         "unifi": {"wired": True, "switch": "SW1", "sw_port": 3}},
        {"ip": "192.168.1.51", "mac": "aa:aa:aa:aa:aa:11", "room": "B",
         "tcp_1400_open": False, "ping": {}},
    ],
    "matrix": [{"src_mac": "aa:aa:aa:aa:aa:01", "dst_mac": "aa:aa:aa:aa:aa:11",
                "dst_resolved": True, "from_db": 30, "to_db": 28, "stp": 0}],
    "bridge_points": {"SW1 port 3": ["192.168.1.50"]},
}
FINDINGS = [{"severity": "crit", "code": "port-1400-closed", "subject": "B",
             "message": "closed"}]


class TestStore(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.dir.name, "t.db")

    def tearDown(self):
        self.dir.cleanup()

    def test_roundtrip(self):
        conn = store.open_db(self.db)
        sid = store.save_snapshot(conn, SNAP, FINDINGS)
        got = store.get_snapshot(conn)
        self.assertEqual(got["_id"], sid)
        self.assertEqual(len(got["devices"]), 2)
        self.assertEqual(got["_findings"][0]["code"], "port-1400-closed")
        rows = store.list_snapshots(conn)
        self.assertEqual(rows[0]["crits"], 1)
        # device table normalised (mac lowercased)
        mac = conn.execute("SELECT mac FROM device ORDER BY mac").fetchall()
        self.assertEqual(mac[0]["mac"], "aa:aa:aa:aa:aa:01")

    def test_previous_snapshot(self):
        conn = store.open_db(self.db)
        old = dict(SNAP, generated="2026-08-12T06:00:00-0400")
        store.save_snapshot(conn, old)
        store.save_snapshot(conn, SNAP)
        prev = store.previous_snapshot(conn, SNAP["generated"])
        self.assertEqual(prev["generated"], old["generated"])

    def test_history(self):
        conn = store.open_db(self.db)
        for i, ts in enumerate(["2026-08-12T06:00:00-0400",
                                "2026-08-12T12:00:00-0400"]):
            s = json.loads(json.dumps(SNAP))
            s["generated"] = ts
            s["devices"][0]["ping"]["jitter_ms"] = 1.0 + i
            store.save_snapshot(conn, s)
        h = store.device_history(conn, "AA:AA:AA:AA:AA:01")
        self.assertEqual([r["jitter_ms"] for r in h], [1.0, 2.0])

    def test_prune(self):
        conn = store.open_db(self.db)
        old = dict(SNAP, generated="2020-01-01T00:00:00-0400")
        store.save_snapshot(conn, old)
        store.save_snapshot(conn, SNAP)
        self.assertEqual(store.prune(conn, keep_days=180), 1)
        left = store.list_snapshots(conn)
        self.assertEqual(len(left), 1)
        self.assertEqual(left[0]["ts"], SNAP["generated"])
        # cascade removed the orphaned child rows
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) c FROM device d LEFT JOIN snapshot s"
            " ON s.id=d.snapshot_id WHERE s.id IS NULL").fetchone()["c"], 0)

    def test_legacy_import_dedupes(self):
        conn = store.open_db(self.db)
        self.assertIsNotNone(store.import_legacy_json(conn, SNAP))
        self.assertIsNone(store.import_legacy_json(conn, SNAP))
        self.assertEqual(len(store.list_snapshots(conn)), 1)


if __name__ == "__main__":
    unittest.main()
