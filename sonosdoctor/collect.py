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


def radio_macs(ip, timeout=5.0):
    """ALL wireless MACs from /status/ifconfig (ath*/wlan* interfaces).

    The mesh matrix identifies neighbours by RADIO MACs, which differ from
    the ethernet MAC in device_description.xml — and home-theater satellites
    use a second (5 GHz) radio with its own MAC, so one is not enough.
    """
    txt = http_get(ip, "/status/ifconfig", timeout)
    if not txt:
        return []
    return [m.lower() for m in re.findall(
        r"(?:ath|wlan)\d\S*\s+Link encap.*?HWaddr\s+([0-9A-Fa-f:]{17})",
        txt, re.S)]


def battery_status(ip, timeout=4.0):
    """Roam/Move battery from /status/batterystatus (absent on mains models)."""
    txt = http_get(ip, "/status/batterystatus", timeout)
    if not txt:
        return None
    out = {}
    for tag, key in (("Level", "level"), ("Health", "health"),
                     ("PowerSource", "power_source"), ("Temperature", "temp")):
        m = re.search(rf"<{tag}>([^<]+)</{tag}>", txt, re.I)
        if m:
            out[key] = m.group(1)
    if "level" in out:
        try:
            out["level"] = int(re.sub(r"\D", "", out["level"]))
        except ValueError:
            pass
    return out or None


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
        rms = radio_macs(ip)
        if rms:
            d["radio_mac"] = rms[0]
            d["radio_macs"] = rms
        if any(w in (d.get("model") or "").lower() for w in ("roam", "move")):
            b = battery_status(ip)
            if b:
                d["battery"] = b
    d["ping"] = ping_stats(ip, ping_count)
    return d
