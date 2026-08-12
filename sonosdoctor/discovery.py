"""SSDP discovery of Sonos ZonePlayers."""
import socket
import time

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
