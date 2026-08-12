"""Findings engine — every check earned from a real incident.

Severities: crit / warn / info. Each finding: {severity, code, subject,
message}. Checks needing history (reboots, disappearances) take the
previous snapshot; checks needing UniFi skip silently without it.
"""

THRESHOLDS = {
    "loss_warn_pct": 0.1,       # any loss is worth a look
    "loss_crit_pct": 20.0,
    "jitter_warn_ms": 30.0,
    "mesh_weak_db": 15,         # signal on a forwarding SonosNet tunnel
    "ani_warn": 9,              # OFDM ANI 0-9; sustained max = interference
    "ani_warn_bridge": 10,      # Boosts/Bridges idle at 8-9 by design (radio
                                # always busy) — 10 disables the check for them
    "noise_floor_warn_dbm": -87,
    "switch_prio_weak": 32768,  # Sonos advertises ~32768 — ties are losable
}


def _f(sev, code, subject, message):
    return {"severity": sev, "code": code, "subject": subject, "message": message}


def run_checks(snap, previous=None, th=None):
    th = {**THRESHOLDS, **(th or {})}
    F = []
    devices = snap.get("devices", [])
    fleet_macs = {(d.get("mac") or "").lower() for d in devices if d.get("mac")}
    fleet_radio = {d.get("radio_mac") for d in devices if d.get("radio_mac")}
    by_mac = {(d.get("mac") or "").lower(): d for d in devices}

    def label(d):
        return f"{d.get('room') or '?'} ({d.get('ip') or d.get('mac') or '?'})"

    # ---- STP root election, from the speakers' own point of view ----
    roots = {}
    for d in devices:
        s = d.get("stp") or {}
        if s.get("root_mac"):
            roots.setdefault((s.get("root_prio"), s["root_mac"]), []).append(d)
    for (prio, rmac), agreeing in roots.items():
        if rmac in fleet_macs or rmac in fleet_radio:
            F.append(_f("crit", "stp-root-is-sonos", rmac,
                        f"A Sonos player ({rmac}, prio {prio}) is the spanning-tree "
                        f"ROOT BRIDGE — {len(agreeing)} player(s) agree. Your LAN "
                        f"topology is being decided by a speaker. Lower a core "
                        f"switch's STP priority below 32768."))
    if len(roots) > 1:
        views = "; ".join(f"{m} (prio {p}, {len(v)} players)"
                          for (p, m), v in roots.items())
        F.append(_f("warn", "stp-root-disagreement", "fleet",
                    f"Players disagree on the STP root: {views}. Topology may be "
                    f"mid-reconvergence, or part of the mesh is partitioned."))

    # ---- controller view of the root election, when available ----
    uni = snap.get("unifi") or {}
    for r in (uni.get("stp") or {}).get("roots", []):
        if not r.get("is_unifi_device"):
            F.append(_f("crit", "stp-root-not-switch", r.get("mac"),
                        f"Switches report root bridge {r.get('mac')} "
                        f"({r.get('identified_as')}) which is not a managed switch "
                        f"({r.get('agreeing_switches')} switches agree)."))
    weak = [n for n, v in (uni.get("stp") or {}).get("priorities", {}).items()
            if v is not None and v >= th["switch_prio_weak"]]
    if weak:
        F.append(_f("warn", "switch-priority-weak", ", ".join(sorted(weak)[:6]),
                    f"{len(weak)} switch(es) at STP priority >= "
                    f"{th['switch_prio_weak']} can lose the root election to a "
                    f"Sonos player (Sonos advertises ~32768)."))

    # ---- per-device reachability & link quality ----
    for d in devices:
        p = d.get("ping") or {}
        if d.get("tcp_1400_open") is False:
            F.append(_f("crit", "port-1400-closed", label(d),
                        "Responds to discovery/ping but port 1400 is closed — "
                        "the Sonos app cannot control this player."))
        if d.get("ssdp_missing"):
            F.append(_f("warn", "ssdp-missing", label(d),
                        "In the household (per /support/review) but did not answer "
                        "SSDP discovery — multicast may be broken on its segment, "
                        "or the player is offline."))
        loss = p.get("loss_pct") or 0
        if loss >= th["loss_crit_pct"]:
            F.append(_f("crit", "packet-loss", label(d), f"{loss}% packet loss."))
        elif loss > th["loss_warn_pct"]:
            F.append(_f("warn", "packet-loss", label(d), f"{loss}% packet loss."))
        if (p.get("jitter_ms") or 0) > th["jitter_warn_ms"]:
            F.append(_f("warn", "high-jitter", label(d),
                        f"Jitter {p['jitter_ms']} ms (avg {p.get('avg_ms')} ms) — "
                        f"grouped-room audio drops start around here."))
        is_bridge = "boost" in (d.get("model") or "").lower() \
            or "bridge" in (d.get("model") or "").lower()
        if (d.get("ani") or 0) >= th["ani_warn_bridge" if is_bridge else "ani_warn"]:
            F.append(_f("warn", "high-ani", label(d),
                        f"OFDM ANI level {d['ani']}/9 — the radio is fighting "
                        f"sustained 2.4 GHz interference on ch {d.get('channel')}."))
        nf = d.get("noise_floor")
        if nf is not None and nf >= th["noise_floor_warn_dbm"]:
            F.append(_f("warn", "noise-floor-high", label(d),
                        f"Noise floor {nf} dBm (healthy is <= -90)."))
        u = d.get("unifi") or {}
        if u and d.get("tcp_1400_open") is False and (u.get("uptime") or 0) > 0:
            F.append(_f("warn", "link-up-but-dead", label(d),
                        "Controller shows the client up "
                        f"(uptime {u.get('uptime')}s) but the player does not "
                        "answer — stale controller state or a one-way path."))
        if u.get("name") and d.get("room"):
            alias = u["name"].lower()
            if "sonos" not in alias and d["room"].lower()[:6] not in alias:
                F.append(_f("info", "alias-oui-mismatch", label(d),
                            f"Controller alias '{u['name']}' does not look like "
                            f"this Sonos ('{d['room']}') — labels lie; verify "
                            f"identity by OUI/causality before trusting names."))

    # ---- weak mesh paths actually in use (forwarding STP tunnels) ----
    edge_by_pair = {}
    for e in snap.get("matrix", []):
        edge_by_pair[(e["src_mac"], e["dst_mac"])] = e
    radio_to_mac = {d.get("radio_mac"): (d.get("mac") or "").lower()
                    for d in devices if d.get("radio_mac")}
    for d in devices:
        for port in (d.get("stp") or {}).get("ports", []):
            if port.get("state") != "forwarding" or not port.get("tunnel_to"):
                continue
            peer = radio_to_mac.get(port["tunnel_to"], port["tunnel_to"])
            e = edge_by_pair.get(((d.get("mac") or "").lower(), peer))
            if e and 0 < e["from_db"] < th["mesh_weak_db"]:
                peer_d = by_mac.get(peer)
                F.append(_f("warn", "weak-mesh-path", label(d),
                            f"Forwarding SonosNet tunnel to "
                            f"{label(peer_d) if peer_d else peer} at only "
                            f"{e['from_db']} dB — audio rides this weak link."))

    # ---- discovery health ----
    if snap.get("discovery_method") == "sweep" and devices:
        F.append(_f("warn", "multicast-broken", "network",
                    f"SSDP multicast found 0 players but a TCP sweep found "
                    f"{snap.get('discovered_count')} — multicast is filtered "
                    f"on this segment. The Sonos app itself will struggle to "
                    f"discover players here (IGMP snooping / mDNS-SSDP "
                    f"filtering is the usual culprit)."))

    # ---- fleet-wide coherence ----
    channels = {d.get("channel") for d in devices
                if d.get("channel") and (d.get("wifi_mode") or "").startswith("SONOSNET")}
    if len(channels) > 1:
        F.append(_f("warn", "channel-mismatch", "fleet",
                    f"SonosNet players report different channels: "
                    f"{sorted(channels)} — mesh may be split mid-migration."))
    bridges = snap.get("bridge_points") or {}
    if len(bridges) > 1:
        F.append(_f("info", "multiple-bridge-points", f"{len(bridges)} locations",
                    f"{len(bridges)} wired Sonos bridge the mesh onto the LAN "
                    f"(redundant paths — healthy ONLY while STP is healthy): "
                    f"{'; '.join(sorted(bridges))}"))

    # ---- history-based checks ----
    if previous:
        prev_by_mac = {(d.get("mac") or "").lower(): d
                       for d in previous.get("devices", [])}
        for mac, pd in prev_by_mac.items():
            d = by_mac.get(mac)
            if d is None:
                F.append(_f("warn", "device-missing", f"{pd.get('room')} ({pd.get('ip')})",
                            "Present in the previous snapshot, gone now."))
                continue
            b0, b1 = pd.get("boot_seq"), d.get("boot_seq")
            if b0 is not None and b1 is not None and b1 > b0:
                F.append(_f("info", "reboot-detected", label(d),
                            f"BootSeq {b0} → {b1} — rebooted "
                            f"{b1 - b0} time(s) since the previous snapshot."))
    return F
