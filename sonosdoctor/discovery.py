"""Discovery: SSDP first, TCP sweep of :1400 as the fallback.

On networks where multicast is filtered (common on other people's setups —
and itself the #1 cause of "the Sonos app can't find my speakers"), SSDP
returns nothing while the players are perfectly reachable. The sweep finds
them anyway, and the SSDP-vs-sweep disagreement becomes a finding.
"""
import ipaddress
import re
import socket
import time
from concurrent.futures import ThreadPoolExecutor

SSDP_ADDR, SSDP_PORT = "239.255.255.250", 1900
SONOS_PORT = 1400
ST_ZONEPLAYER = "urn:schemas-upnp-org:device:ZonePlayer:1"


def ssdp_discover(timeout=4.0, bursts=2):
    """M-SEARCH for Sonos ZonePlayers. Returns {ip: {response headers}}."""
    msg = (
        "M-SEARCH * HTTP/1.1\r\n"
        f"HOST: {SSDP_ADDR}:{SSDP_PORT}\r\n"
        'MAN: "ssdp:discover"\r\n'
        "MX: 3\r\n"
        f"ST: {ST_ZONEPLAYER}\r\n\r\n"
    ).encode()
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    s.settimeout(timeout)
    found = {}
    try:
        for _ in range(bursts):                 # UDP is lossy, send twice
            s.sendto(msg, (SSDP_ADDR, SSDP_PORT))
        end = time.time() + timeout
        while time.time() < end:
            try:
                data, addr = s.recvfrom(2048)
            except socket.timeout:
                break
            hdrs = {}
            for line in data.decode("utf-8", "ignore").split("\r\n")[1:]:
                if ":" in line:
                    k, v = line.split(":", 1)
                    hdrs[k.strip().upper()] = v.strip()
            found.setdefault(addr[0], hdrs)
    finally:
        s.close()
    return found


def local_subnet():
    """Best-effort /24 of the default-route interface."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 53))            # no packets actually sent (UDP)
        ip = s.getsockname()[0]
    except Exception:
        return None
    finally:
        s.close()
    return str(ipaddress.ip_network(f"{ip}/24", strict=False))


def _is_sonos(ip, timeout=2.0):
    """Port 1400 open AND the device description says Sonos."""
    try:
        with socket.create_connection((ip, 1400), timeout=timeout):
            pass
    except Exception:
        return False
    from .collect import http_get
    xml = http_get(ip, "/xml/device_description.xml", timeout=3.0)
    return bool(xml and re.search(r"Sonos|RINCON", xml))


def tcp_sweep(cidr=None, workers=64):
    """Scan a subnet for Sonos players by TCP. Returns {ip: {}}."""
    cidr = cidr or local_subnet()
    if not cidr:
        return {}
    hosts = [str(h) for h in ipaddress.ip_network(cidr, strict=False).hosts()]
    if len(hosts) > 4096:                       # refuse to sweep huge ranges
        return {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        hits = ex.map(lambda ip: ip if _is_sonos(ip) else None, hosts)
    return {ip: {} for ip in hits if ip}
