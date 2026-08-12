"""Fetch and parse /status/showstp — each speaker's own STP view.

This is the controller-free root-bridge check: every Sonos runs STP on its
internal bridge (br0) and reports the designated root it agrees on. If that
root's MAC belongs to a Sonos player, the mesh has won the root election —
the exact incident class that silently degrades whole LANs.

Bridge IDs print as `PPPP.MMMMMMMMMMMM` (hex priority . MAC).
"""
import re
from .collect import http_get

_BRIDGE_ID = r"([0-9a-f]{4})\.([0-9a-f]{12})"
_PORT_HDR = re.compile(
    r"^(\w+) \((\d+)\)"
    r"(?: - tunnel to ([0-9A-Fa-f:]{17}) \(remote STP state = (\w+), direct = (\d+)\))?",
    re.M)


def _fmt_mac(hexmac):
    return ":".join(hexmac[i:i + 2] for i in range(0, 12, 2))


def fetch_showstp(ip, timeout=8.0):
    return http_get(ip, "/status/showstp", timeout)


def parse_showstp(txt):
    """→ {bridge_prio, bridge_mac, root_prio, root_mac, root_path_cost, ports}."""
    out = {}
    m = re.search(rf"bridge id\s+{_BRIDGE_ID}", txt)
    if m:
        out["bridge_prio"] = int(m.group(1), 16)
        out["bridge_mac"] = _fmt_mac(m.group(2))
    m = re.search(rf"designated root\s+{_BRIDGE_ID}", txt)
    if m:
        out["root_prio"] = int(m.group(1), 16)
        out["root_mac"] = _fmt_mac(m.group(2))
    m = re.search(r"root port\s+(\d+)\s+path cost\s+(\d+)", txt)
    if m:
        out["root_port"], out["root_path_cost"] = int(m.group(1)), int(m.group(2))

    ports = []
    heads = list(_PORT_HDR.finditer(txt))
    for i, h in enumerate(heads):
        body = txt[h.end(): heads[i + 1].start() if i + 1 < len(heads) else len(txt)]
        sm = re.search(r"\bstate\s+(\w+)", body)
        pc = re.search(r"\bpath cost\s+(\d+)", body)
        ports.append({
            "iface": h.group(1), "index": int(h.group(2)),
            "tunnel_to": h.group(3).lower() if h.group(3) else None,
            "remote_state": h.group(4), "direct": int(h.group(5)) if h.group(5) else None,
            "state": sm.group(1) if sm else None,
            "path_cost": int(pc.group(1)) if pc else None,
        })
    out["ports"] = ports
    return out
