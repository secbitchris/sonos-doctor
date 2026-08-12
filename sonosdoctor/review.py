"""Fetch and parse /support/review — the fleet-wide diagnostics dump.

One GET against ANY speaker returns a <ZPNetworkInfo> XML document with one
<ZPSupportInfo> section per player in the household. Each section carries
the player's identity plus its /proc/ath_rincon/status: SonosNet channel,
noise floor, OFDM ANI level, PHY errors, and the neighbour list —
`Node <radio MAC> - FROM x : TO y : STP f` — which is exactly the data
behind Sonos's classic colour-coded network matrix.
"""
import html
import re
from .collect import http_get

ZP_TAGS = (("ZoneName", "zone"), ("LocalUID", "uid"), ("SerialNumber", "serial"),
           ("SoftwareVersion", "sw"), ("HardwareVersion", "hw"),
           ("IPAddress", "ip"), ("MACAddress", "mac"), ("SeriesID", "series"),
           ("WifiModeString", "wifi_mode"), ("SWGen", "swgen"),
           ("HouseholdControlID", "household"),
           ("ConnectionTypeString", "connection_type"))

NODE_RE = re.compile(
    r"Node ([0-9A-Fa-f:]{17}) - FROM (\d+) : TO (\d+) : STP (\d+)"
    r"(?: : MODEL ([\d.]+))?(?:: KEY (\d+))?")


def fetch_review(ip, timeout=25.0):
    return http_get(ip, "/support/review", timeout)


def parse_review(xml):
    """→ list of per-player dicts (identity + radio stats + neighbour edges)."""
    players = []
    for sect in re.findall(r"<ZPSupportInfo>(.*?)</ZPSupportInfo>", xml, re.S):
        p = {}
        for tag, key in ZP_TAGS:
            m = re.search(rf"<{tag}>(.*?)</{tag}>", sect)
            if m:
                p[key] = html.unescape(m.group(1))
        if p.get("mac"):
            p["mac"] = p["mac"].lower()

        m = re.search(r"<File name='/proc/ath_rincon/status'>(.*?)</File>",
                      sect, re.S)
        radio = m.group(1) if m else ""
        m = re.search(r"IEEE channel:\s*(\d+)", radio)
        if m:
            p["channel"] = int(m.group(1))
        m = re.search(r"OFDM ANI level:\s*(\d+)", radio)
        if m:
            p["ani"] = int(m.group(1))
        m = re.search(r"PHY errors since last reading/reset:\s*(\d+)", radio)
        if m:
            p["phy_errors"] = int(m.group(1))
        floors = [int(x) for x in re.findall(r"Noise Floor:\s*(-?\d+) dBm", radio)
                  if int(x) != 0]                # 0 dBm = radio chain not reporting
        if floors:
            p["noise_floor"] = max(floors)       # worst (highest) chain

        p["neighbours"] = [
            {"radio_mac": mac.lower(), "from_db": int(f), "to_db": int(t),
             "stp": int(s), "model": mo, "key": int(k) if k else None}
            for mac, f, t, s, mo, k in NODE_RE.findall(radio)]
        players.append(p)
    return players


def build_matrix(players, radio_to_mac):
    """Neighbour lists → directed edge list keyed by ethernet MAC.

    radio_to_mac maps radio MAC → ethernet MAC (from per-speaker ifconfig);
    edges to radio MACs we cannot resolve keep the raw radio MAC so nothing
    is silently dropped (an unknown strong neighbour is itself a finding).
    """
    edges = []
    for p in players:
        src = p.get("mac")
        if not src:
            continue
        for n in p["neighbours"]:
            dst = radio_to_mac.get(n["radio_mac"])
            edges.append({
                "src_mac": src,
                "dst_mac": dst or n["radio_mac"],
                "dst_resolved": dst is not None,
                "from_db": n["from_db"], "to_db": n["to_db"],
                "stp": n["stp"],
            })
    return edges
