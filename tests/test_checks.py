"""Synthetic-incident tests: prove each check fires when its incident recurs.

The 2026-08-11 root-bridge incident cannot be recreated on a live network,
so it is pinned here instead.
"""
import unittest

from sonosdoctor import checks


def base_device(**kw):
    d = {"ip": "192.168.1.50", "mac": "aa:aa:aa:aa:aa:01",
         "radio_mac": "aa:aa:aa:aa:aa:02", "room": "Test Room",
         "tcp_1400_open": True,
         "ping": {"loss_pct": 0.0, "avg_ms": 2.0, "jitter_ms": 1.0},
         "channel": 6, "ani": 3, "wifi_mode": "SONOSNET_MODE",
         "stp": {"root_prio": 0x1000, "root_mac": "28:70:4e:00:00:01",
                 "ports": []}}
    d.update(kw)
    return d


def snap(devices, **kw):
    s = {"generated": "2026-08-12T12:00:00-0400", "host": "test",
         "discovered_count": len(devices), "devices": devices,
         "matrix": [], "unifi": {"available": False}, "bridge_points": {}}
    s.update(kw)
    return s


def codes(findings, severity=None):
    return {f["code"] for f in findings
            if severity is None or f["severity"] == severity}


class TestSTPRoot(unittest.TestCase):
    def test_clean_fleet_no_findings(self):
        f = checks.run_checks(snap([base_device()]))
        self.assertEqual(f, [])

    def test_sonos_as_root_is_critical(self):
        # THE incident: players agree the root bridge is one of their own
        boost = base_device(mac="bb:bb:bb:bb:bb:01", radio_mac="bb:bb:bb:bb:bb:02",
                            room="Boost Loft", ip="192.168.1.51")
        victim = base_device(
            stp={"root_prio": 0x8000, "root_mac": "bb:bb:bb:bb:bb:01",
                 "ports": []})
        boost["stp"] = {"root_prio": 0x8000, "root_mac": "bb:bb:bb:bb:bb:01",
                        "ports": []}
        f = checks.run_checks(snap([victim, boost]))
        self.assertIn("stp-root-is-sonos", codes(f, "crit"))

    def test_root_via_radio_mac_also_caught(self):
        d = base_device(stp={"root_prio": 0x8000,
                             "root_mac": "aa:aa:aa:aa:aa:02", "ports": []})
        f = checks.run_checks(snap([d]))
        self.assertIn("stp-root-is-sonos", codes(f, "crit"))

    def test_root_disagreement(self):
        a = base_device()
        b = base_device(mac="aa:aa:aa:aa:aa:11", ip="192.168.1.52",
                        stp={"root_prio": 0x1000,
                             "root_mac": "ff:ee:dd:cc:bb:aa", "ports": []})
        f = checks.run_checks(snap([a, b]))
        self.assertIn("stp-root-disagreement", codes(f, "warn"))

    def test_weak_switch_priority_from_unifi(self):
        s = snap([base_device()],
                 unifi={"available": True,
                        "stp": {"roots": [], "priorities":
                                {"Good Switch": 4096, "Lazy Switch": 32768}}})
        f = checks.run_checks(s)
        self.assertIn("switch-priority-weak", codes(f, "warn"))


class TestLinkQuality(unittest.TestCase):
    def test_loss_and_jitter(self):
        d = base_device(ping={"loss_pct": 30.0, "avg_ms": 80, "jitter_ms": 45})
        f = checks.run_checks(snap([d]))
        self.assertIn("packet-loss", codes(f, "crit"))
        self.assertIn("high-jitter", codes(f, "warn"))

    def test_port_closed_is_critical(self):
        f = checks.run_checks(snap([base_device(tcp_1400_open=False)]))
        self.assertIn("port-1400-closed", codes(f, "crit"))

    def test_ssdp_missing(self):
        f = checks.run_checks(snap([base_device(ssdp_missing=True,
                                                tcp_1400_open=None)]))
        self.assertIn("ssdp-missing", codes(f, "warn"))

    def test_ani_thresholds_model_aware(self):
        speaker = base_device(ani=9)
        f = checks.run_checks(snap([speaker]))
        self.assertIn("high-ani", codes(f, "warn"))
        boost = base_device(ani=9, model="Sonos Boost")
        self.assertNotIn("high-ani", codes(checks.run_checks(snap([boost]))))

    def test_channel_mismatch(self):
        a = base_device()
        b = base_device(mac="aa:aa:aa:aa:aa:11", ip="192.168.1.52", channel=11)
        f = checks.run_checks(snap([a, b]))
        self.assertIn("channel-mismatch", codes(f, "warn"))

    def test_weak_path_in_use(self):
        peer = base_device(mac="cc:cc:cc:cc:cc:01", radio_mac="cc:cc:cc:cc:cc:02",
                           ip="192.168.1.53", room="Far Room")
        d = base_device(stp={"root_prio": 0x1000,
                             "root_mac": "28:70:4e:00:00:01",
                             "ports": [{"iface": "ath0", "index": 2,
                                        "tunnel_to": "cc:cc:cc:cc:cc:02",
                                        "state": "forwarding",
                                        "remote_state": "forwarding",
                                        "direct": 1, "path_cost": 200}]})
        s = snap([d, peer], matrix=[
            {"src_mac": "aa:aa:aa:aa:aa:01", "dst_mac": "cc:cc:cc:cc:cc:01",
             "dst_resolved": True, "from_db": 9, "to_db": 12, "stp": 0}])
        f = checks.run_checks(s)
        self.assertIn("weak-mesh-path", codes(f, "warn"))
        # same edge NOT forwarding → no finding
        s["devices"][0]["stp"]["ports"][0]["state"] = "blocking"
        self.assertNotIn("weak-mesh-path", codes(checks.run_checks(s)))


class TestHistory(unittest.TestCase):
    def test_reboot_and_disappearance(self):
        prev = snap([base_device(boot_seq=10),
                     base_device(mac="aa:aa:aa:aa:aa:11", ip="192.168.1.52",
                                 room="Gone Room", boot_seq=5)])
        cur = snap([base_device(boot_seq=12)])
        f = checks.run_checks(cur, previous=prev)
        self.assertIn("reboot-detected", codes(f, "info"))
        self.assertIn("device-missing", codes(f, "warn"))

    def test_stable_history_quiet(self):
        prev = snap([base_device(boot_seq=10)])
        cur = snap([base_device(boot_seq=10)])
        self.assertEqual(checks.run_checks(cur, previous=prev), [])


if __name__ == "__main__":
    unittest.main()
