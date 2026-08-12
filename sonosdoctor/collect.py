"""Per-speaker probes: port 1400, device description, ping, radio MAC."""
import html
import re
import socket
import statistics
import subprocess
import sys
import time
import urllib.request

SONOS_PORT = 1400


def http_get(ip, path, timeout=8.0):
    """GET http://ip:1400/<path> → text, or None on failure."""
    try:
        req = urllib.request.Request(f"http://{ip}:{SONOS_PORT}{path}")
        with urllib.request.urlopen(req, timeout=timeout) as f:
            return f.read().decode("utf-8", "ignore")
    except Exception:
        return None


def tcp_check(ip, port=SONOS_PORT, timeout=2.0):
    t0 = time.time()
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True, round((time.time() - t0) * 1000, 1)
    except Exception:
        return False, None


def device_info(ip, timeout=3.0):
    """Parse /xml/device_description.xml — model, room, serial, MAC."""
    out = {}
    xml = http_get(ip, "/xml/device_description.xml", timeout)
    if xml is None:
        return {"error": "device_description unreachable"}
    for tag, key in (("roomName", "room"), ("modelName", "model"),
                     ("modelNumber", "model_number"), ("displayName", "display"),
                     ("serialNum", "serial"), ("MACAddress", "mac"),
                     ("softwareVersion", "sw"), ("hardwareVersion", "hw")):
        m = re.search(rf"<{tag}>(.*?)</{tag}>", xml)
        if m:
            out[key] = html.unescape(m.group(1))
    return out


def radio_mac(ip, timeout=5.0):
    """Wireless (ath0/wlan0) MAC from /status/ifconfig.

    The mesh matrix identifies neighbours by their RADIO MAC, which differs
    from the ethernet MAC in device_description.xml — this closes that gap.
    """
    txt = http_get(ip, "/status/ifconfig", timeout)
    if not txt:
        return None
    m = re.search(r"(?:ath0|wlan0)\S*\s+Link encap.*?HWaddr\s+([0-9A-Fa-f:]{17})",
                  txt, re.S)
    return m.group(1).lower() if m else None


def ping_stats(ip, count=10):
    """Latency / jitter / loss. Jitter = stdev of RTTs.

    Tries a 0.25 s interval; if the OS restricts sub-second intervals to
    root (macOS does), falls back to the default 1 s interval.
    """
    for args in (["-i", "0.25"], []):
        try:
            r = subprocess.run(
                ["ping", "-n", "-c", str(count)] + args + [ip],
                capture_output=True, text=True, timeout=count * 1.5 + 15)
        except Exception as e:
            return {"error": str(e)[:80]}
        rtts = [float(x) for x in re.findall(r"time=([\d.]+)", r.stdout)]
        if not rtts and args and ("privilege" in r.stderr.lower()
                                  or "permission" in r.stderr.lower()
                                  or "interval" in r.stderr.lower()):
            continue                             # retry at default interval
        lost = count - len(rtts)
        return {
            "sent": count, "received": len(rtts),
            "loss_pct": round(100.0 * lost / count, 1),
            "min_ms": round(min(rtts), 2) if rtts else None,
            "avg_ms": round(statistics.fmean(rtts), 2) if rtts else None,
            "max_ms": round(max(rtts), 2) if rtts else None,
            "jitter_ms": round(statistics.stdev(rtts), 2) if len(rtts) > 1 else 0.0,
        }
    return {"error": "ping failed"}


def probe_speaker(ip, ping_count=10):
    """Full per-speaker probe. Returns a device dict."""
    ok, ms = tcp_check(ip)
    d = {"ip": ip, "tcp_1400_open": ok, "tcp_connect_ms": ms}
    if ok:
        d.update(device_info(ip))
        rm = radio_mac(ip)
        if rm:
            d["radio_mac"] = rm
    d["ping"] = ping_stats(ip, ping_count)
    return d
