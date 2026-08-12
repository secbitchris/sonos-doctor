"""Assemble one full snapshot: discovery → probes → review → topology → UniFi."""
import socket
import time
from concurrent.futures import ThreadPoolExecutor

from . import collect, discovery, review, stp, topology, unifi


def take_snapshot(ping_count=10, discover_timeout=4.0, use_unifi=True,
                  unifi_host=None, log=lambda msg: None):
    snap = {"generated": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "host": socket.gethostname()}

    log("SSDP discovery…")
    disc = discovery.ssdp_discover(discover_timeout)
    ips = sorted(disc)
    snap["discovered_count"] = len(ips)
    if not ips:
        snap["devices"] = []
        snap["matrix"] = []
        return snap

    log(f"{len(ips)} players — probing (ping/identity/radio/stp)…")
    with ThreadPoolExecutor(max_workers=12) as ex:
        devices = list(ex.map(
            lambda ip: collect.probe_speaker(ip, ping_count), ips))
        stp_raw = list(ex.map(stp.fetch_showstp, ips))
    for d, raw in zip(devices, stp_raw):
        if raw:
            d["stp"] = stp.parse_showstp(raw)

    alive = [d["ip"] for d in devices if d["tcp_1400_open"]]

    log("fleet review (/support/review) — one fetch covers the household…")
    fleet = []
    for ip in alive[:3]:                       # retry against up to 3 players
        xml = review.fetch_review(ip)
        if xml:
            fleet = review.parse_review(xml)
            break
    by_mac = {d["mac"].lower(): d for d in devices if d.get("mac")}
    for p in fleet:
        d = by_mac.get((p.get("mac") or "").lower())
        if d is None:                          # in household but not discovered
            d = {"ip": p.get("ip"), "mac": p.get("mac"), "room": p.get("zone"),
                 "tcp_1400_open": None, "ssdp_missing": True, "ping": {}}
            devices.append(d)
        for k in ("uid", "series", "channel", "ani", "phy_errors",
                  "noise_floor", "wifi_mode", "connection_type"):
            if k in p:
                d[k] = p[k]
        if not d.get("room") and p.get("zone"):
            d["room"] = p["zone"]

    radio_to_mac = {d["radio_mac"]: d["mac"].lower()
                    for d in devices if d.get("radio_mac") and d.get("mac")}
    snap["matrix"] = review.build_matrix(fleet, radio_to_mac)

    log("zone-group topology…")
    groups = []
    for ip in alive[:3]:
        soap = topology.fetch_zone_groups(ip)
        if soap:
            groups = topology.parse_zone_groups(soap)
            break
    snap["groups"] = groups
    by_uid = {d.get("uid"): d for d in devices if d.get("uid")}
    for g in groups:
        for mem in g["members"]:
            d = by_uid.get(mem.get("uuid"))
            if d is None:
                for cand in devices:
                    if cand.get("ip") and cand["ip"] == mem.get("ip"):
                        d = cand
                        break
            if d is not None:
                d["boot_seq"] = mem.get("bootseq")
                d["group"] = g["id"]
                d["is_coordinator"] = (mem.get("uuid") == g["coordinator"])
                d["role"] = mem.get("role")
                if mem.get("ethlink") is not None:
                    d["eth_link"] = mem["ethlink"]

    uni = {"available": False, "error": "disabled"}
    if use_unifi:
        key = unifi.api_key()
        if key:
            log("UniFi enrichment…")
            uni = unifi.controller_context(key, unifi_host)
        else:
            uni = {"available": False, "error": "no API key"}
    bymac = uni.get("by_mac", {})
    for d in devices:
        mac = (d.get("mac") or "").lower().replace("-", ":")
        if mac in bymac:
            d["unifi"] = bymac[mac]
    snap["unifi"] = {k: v for k, v in uni.items() if k != "by_mac"}

    bridges = {}
    for d in devices:
        u = d.get("unifi") or {}
        wired_by_unifi = u.get("wired")
        wired_by_sonos = d.get("eth_link") == 1 or (
            d.get("connection_type", "").lower().startswith("wired"))
        if (wired_by_unifi or (not u and wired_by_sonos)):
            where = (f"{u['switch']} port {u.get('sw_port')}"
                     if u.get("switch") else f"wired ({d.get('room')})")
            bridges.setdefault(where, []).append(d.get("ip"))
    snap["bridge_points"] = bridges

    snap["devices"] = sorted(devices, key=lambda d: d.get("ip") or "")
    return snap
