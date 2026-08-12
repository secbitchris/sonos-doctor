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

    def test_low_battery_semantics(self):
        # unplugged + low → warn
        d = base_device(model="Sonos Roam",
                        battery={"level": 15, "power_source": "BATTERY"})
        self.assertIn("low-battery", codes(checks.run_checks(snap([d])), "warn"))
        # charging (ring or USB) → quiet, even at low level
        for src in ("SONOS_CHARGING_RING", "USB_POWER"):
            d["battery"] = {"level": 15, "power_source": src}
            self.assertNotIn("low-battery", codes(checks.run_checks(snap([d]))))

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


class TestMeshTree(unittest.TestCase):
    def two_speakers(self):
        a = base_device()
        b = base_device(mac="cc:cc:cc:cc:cc:01", radio_mac="cc:cc:cc:cc:cc:02",
                        ip="192.168.1.53", room="Far Room")
        return a, b

    def test_asymmetric_path(self):
        a, b = self.two_speakers()
        s = snap([a, b], mesh_tree={
            "aa:aa:aa:aa:aa:01": {"parent": "cc:cc:cc:cc:cc:01",
                                  "via": "sonosnet", "depth": 1},
            "cc:cc:cc:cc:cc:01": {"parent": None, "via": "lan", "depth": 0}},
            matrix=[
                {"src_mac": "aa:aa:aa:aa:aa:01", "dst_mac": "cc:cc:cc:cc:cc:01",
                 "dst_resolved": True, "from_db": 45, "to_db": 20, "stp": 0},
                {"src_mac": "cc:cc:cc:cc:cc:01", "dst_mac": "aa:aa:aa:aa:aa:01",
                 "dst_resolved": True, "from_db": 20, "to_db": 45, "stp": 0}])
        f = checks.run_checks(s)
        self.assertIn("asymmetric-path", codes(f, "warn"))
        # balanced edge → quiet
        for e in s["matrix"]:
            e["from_db"] = 40
        self.assertNotIn("asymmetric-path", codes(checks.run_checks(s)))

    def test_unknown_mesh_neighbor(self):
        a, _ = self.two_speakers()
        s = snap([a], matrix=[
            {"src_mac": "aa:aa:aa:aa:aa:01", "dst_mac": "de:ad:be:ef:00:01",
             "dst_resolved": False, "from_db": 33, "to_db": 0, "stp": 0}])
        f = checks.run_checks(s)
        self.assertIn("unknown-mesh-neighbor", codes(f, "info"))
        s["matrix"][0]["from_db"] = 10          # too quiet to matter
        self.assertNotIn("unknown-mesh-neighbor",
                         codes(checks.run_checks(s)))

    def test_controller_path_mismatch(self):
        bridge = base_device(mac="cc:cc:cc:cc:cc:01", ip="192.168.1.53",
                             room="Bridge", wired_physical=True,
                             unifi={"switch": "SW-A", "sw_port": 9})
        spk = base_device(unifi={"switch": "SW-B", "sw_port": 10})
        tree = {"aa:aa:aa:aa:aa:01": {"parent": "cc:cc:cc:cc:cc:01",
                                      "via": "sonosnet", "depth": 1},
                "cc:cc:cc:cc:cc:01": {"parent": None, "via": "lan", "depth": 0}}
        f = checks.run_checks(snap([spk, bridge], mesh_tree=tree))
        self.assertIn("controller-path-mismatch", codes(f, "info"))
        spk["unifi"] = {"switch": "SW-A", "sw_port": 9}   # agrees → quiet
        self.assertNotIn("controller-path-mismatch",
                         codes(checks.run_checks(snap([spk, bridge],
                                                      mesh_tree=tree))))
        # attribution to a gateway or an uplink port is path noise → quiet
        spk["unifi"] = {"switch": "UDM", "sw_port": 10}
        s = snap([spk, bridge], mesh_tree=tree,
                 unifi={"available": True, "gateways": ["UDM"],
                        "uplink_ports": [["SW-B", 1]]})
        self.assertNotIn("controller-path-mismatch",
                         codes(checks.run_checks(s)))
        spk["unifi"] = {"switch": "SW-B", "sw_port": 1}
        self.assertNotIn("controller-path-mismatch",
                         codes(checks.run_checks(s)))

    def test_reparent_detected(self):
        a, b = self.two_speakers()
        c = base_device(mac="dd:dd:dd:dd:dd:01", radio_mac="dd:dd:dd:dd:dd:02",
                        ip="192.168.1.54", room="Third Room")
        prev = snap([a, b, c], mesh_tree={
            "aa:aa:aa:aa:aa:01": {"parent": "cc:cc:cc:cc:cc:01",
                                  "via": "sonosnet", "depth": 1}})
        cur = snap([a, b, c], mesh_tree={
            "aa:aa:aa:aa:aa:01": {"parent": "dd:dd:dd:dd:dd:01",
                                  "via": "sonosnet", "depth": 1}})
        f = checks.run_checks(cur, previous=prev)
        self.assertIn("mesh-reparented", codes(f, "info"))
        self.assertNotIn("mesh-reparented",
                         codes(checks.run_checks(cur, previous=cur)))


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
