"""Parser regression tests against real captured fleet data (2026-08-12)."""
import os
import unittest

from sonosdoctor import review, stp, topology

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def load(name):
    with open(os.path.join(FIX, name)) as f:
        return f.read()


class TestReview(unittest.TestCase):
    def setUp(self):
        self.players = review.parse_review(load("review.xml"))

    def test_full_household_parsed(self):
        self.assertEqual(len(self.players), 30)

    def test_identity_fields(self):
        for p in self.players:
            self.assertIn("mac", p, p.get("zone"))
            self.assertRegex(p["mac"], r"^[0-9a-f:]{17}$")
            self.assertIn("zone", p)
            self.assertIn("uid", p)

    def test_entities_decoded(self):
        zones = {p["zone"] for p in self.players}
        self.assertTrue(any("'" in z for z in zones),
                        "fixture must exercise an apostrophe in a zone name")
        self.assertFalse(any("&apos;" in z or "&amp;" in z for z in zones),
                         "XML entities must be decoded in zone names")

    def test_radio_stats(self):
        with_ani = [p for p in self.players if "ani" in p]
        self.assertGreaterEqual(len(with_ani), 15)
        for p in with_ani:
            self.assertTrue(0 <= p["ani"] <= 9)
        channels = {p.get("channel") for p in self.players if p.get("channel")}
        self.assertEqual(channels, {6})
        floors = [p["noise_floor"] for p in self.players if "noise_floor" in p]
        self.assertTrue(all(-120 < nf < -60 for nf in floors), floors)

    def test_neighbour_edges(self):
        total = sum(len(p["neighbours"]) for p in self.players)
        self.assertGreater(total, 500)
        for p in self.players:
            for n in p["neighbours"]:
                self.assertRegex(n["radio_mac"], r"^[0-9a-f:]{17}$")
                self.assertTrue(0 <= n["from_db"] < 100)
                self.assertTrue(0 <= n["to_db"] < 100)

    def test_build_matrix_resolution(self):
        # map every radio MAC that appears as a neighbour to a fake eth MAC,
        # except one — the unresolved edge must survive with the raw radio MAC
        radios = sorted({n["radio_mac"] for p in self.players
                         for n in p["neighbours"]})
        held_out = radios[0]
        r2m = {r: f"eth-{r}" for r in radios if r != held_out}
        edges = review.build_matrix(self.players, r2m)
        self.assertEqual(len(edges),
                         sum(len(p["neighbours"]) for p in self.players))
        unresolved = [e for e in edges if not e["dst_resolved"]]
        self.assertTrue(all(e["dst_mac"] == held_out for e in unresolved))
        self.assertTrue(all(e["dst_mac"].startswith("eth-")
                            for e in edges if e["dst_resolved"]))


class TestShowSTP(unittest.TestCase):
    def setUp(self):
        self.s = stp.parse_showstp(load("showstp.txt"))

    def test_bridge_and_root(self):
        self.assertEqual(self.s["bridge_mac"], "38:42:0b:00:00:04")
        self.assertEqual(self.s["bridge_prio"], 0x9800)
        self.assertEqual(self.s["root_mac"], "28:70:4e:00:00:01")   # USW Agg
        self.assertEqual(self.s["root_prio"], 0x1000)               # 4096
        self.assertEqual(self.s["root_port"], 2)

    def test_ports(self):
        ports = self.s["ports"]
        self.assertGreater(len(ports), 3)
        states = {p["state"] for p in ports}
        self.assertIn("forwarding", states)
        self.assertIn("blocking", states)
        eth = [p for p in ports if p["iface"] == "eth0"]
        self.assertEqual(eth[0]["state"], "disabled")
        tunnels = [p for p in ports if p["tunnel_to"]]
        self.assertGreater(len(tunnels), 2)
        fwd = [p for p in tunnels if p["state"] == "forwarding"]
        self.assertEqual(len(fwd), 7, "matches raw capture: uplink + 6 children")
        # the port carrying traffic toward the root must be forwarding
        self.assertIn(self.s["root_port"], [p["index"] for p in fwd])


class TestZoneGroups(unittest.TestCase):
    def setUp(self):
        self.groups = topology.parse_zone_groups(load("zonegroups.xml"))

    def test_groups_parsed(self):
        self.assertGreater(len(self.groups), 5)
        members = [m for g in self.groups for m in g["members"]]
        self.assertGreaterEqual(len(members), 25)
        for m in members:
            self.assertTrue(m.get("uuid", "").startswith("RINCON_"))
        with_ip = [m for m in members if m.get("ip")]
        self.assertGreater(len(with_ip), 20)
        with_boot = [m for m in members if isinstance(m.get("bootseq"), int)]
        self.assertGreater(len(with_boot), 20)

    def test_coordinator_is_member(self):
        for g in self.groups:
            uuids = {m["uuid"] for m in g["members"]}
            self.assertIn(g["coordinator"], uuids)


if __name__ == "__main__":
    unittest.main()
