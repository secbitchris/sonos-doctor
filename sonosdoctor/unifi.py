"""Optional UniFi controller enrichment. Degrades gracefully to nothing.

Reads UNIFI_API_KEY from the environment (or a key file path in
UNIFI_KEYFILE). Never stores credentials itself. Read-only endpoints only.
"""
import json
import os
import ssl
import urllib.request

UNIFI_HOST = os.environ.get("UNIFI_HOST", "192.168.1.1")

SONOS_OUIS = {"00:0e:58", "5c:aa:fd", "94:9f:3e", "78:28:ca", "b8:e9:37",
              "34:7e:5c", "48:a6:b8", "f0:f6:c1", "38:42:0b", "54:2a:1b",
              "c4:38:75", "68:54:fd", "e8:5d:86", "8c:4c:ad"}


def api_key():
    k = os.environ.get("UNIFI_API_KEY")
    if k:
        return k.strip()
    kf = os.environ.get("UNIFI_KEYFILE")
    if kf:
        try:
            with open(kf) as f:
                return f.read().strip()
        except Exception:
            return None
    return None


def _get(path, key, host=None, timeout=25):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(
        f"https://{host or UNIFI_HOST}/proxy/network/api/s/default/{path}",
        headers={"X-API-KEY": key, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, context=ctx, timeout=timeout) as f:
        return json.load(f)["data"]


def is_sonos_oui(mac):
    return bool(mac) and mac.lower()[:8] in SONOS_OUIS


def controller_context(key, host=None):
    """Client RF/switch data by MAC + the STP root-bridge view from switches."""
    ctx = {"available": False}
    try:
        sta = _get("stat/sta", key, host)
        dev = _get("stat/device", key, host)
    except Exception as e:
        ctx["error"] = str(e)[:120]
        return ctx
    ctx["available"] = True
    names = {d.get("mac"): d.get("name") for d in dev}
    ctx["by_mac"] = {}
    for c in sta:
        m = (c.get("mac") or "").lower()
        ctx["by_mac"][m] = {
            "ip": c.get("ip"), "name": c.get("hostname") or c.get("name"),
            "wired": bool(c.get("is_wired")),
            "ap": names.get(c.get("ap_mac")), "switch": names.get(c.get("sw_mac")),
            "sw_port": c.get("sw_port"), "essid": c.get("essid"),
            "signal": c.get("signal"), "rx_rate": c.get("rx_rate"),
            "tx_rate": c.get("tx_rate"), "tx_retries": c.get("tx_retries"),
            "satisfaction": c.get("satisfaction"), "uptime": c.get("uptime"),
        }
    roots = {}
    for d in dev:
        if d.get("type") == "usw" and d.get("root_switch"):
            roots.setdefault(d["root_switch"], []).append(d.get("name"))
    stp = {"roots": [], "priorities": {
        d.get("name"): d.get("stp_priority")
        for d in dev if d.get("type") == "usw" and d.get("stp_priority") is not None}}
    for rmac, switches in roots.items():
        owner = names.get(rmac)
        client = ctx["by_mac"].get(rmac.lower())
        stp["roots"].append({
            "mac": rmac, "is_unifi_device": owner is not None,
            "identified_as": owner or (client or {}).get("name") or "UNKNOWN DEVICE",
            "ip": (client or {}).get("ip"),
            "agreeing_switches": len(switches),
        })
    ctx["stp"] = stp
    return ctx
