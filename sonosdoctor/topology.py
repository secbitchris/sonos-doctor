"""ZoneGroupTopology — groups, bonded pairs/satellites, per-member flags.

GetZoneGroupState returns (XML-escaped) ZoneGroupState XML. Member
attributes worth keeping: BootSeq (increments every reboot — a delta
between snapshots is a reboot detector), WirelessMode, ConnectionType,
ChannelFreq, EthLink, Invisible (bonded satellites).
"""
import html
import re
import urllib.request
import xml.etree.ElementTree as ET

SOAP_BODY = (
    '<?xml version="1.0"?>'
    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
    's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
    '<u:GetZoneGroupState xmlns:u="urn:schemas-upnp-org:service:ZoneGroupTopology:1">'
    "</u:GetZoneGroupState></s:Body></s:Envelope>")

MEMBER_ATTRS = ("UUID", "ZoneName", "Location", "BootSeq", "WirelessMode",
                "ConnectionType", "ChannelFreq", "EthLink", "WifiEnabled",
                "Invisible", "SoftwareVersion")


def fetch_zone_groups(ip, timeout=8.0):
    req = urllib.request.Request(
        f"http://{ip}:1400/ZoneGroupTopology/Control",
        data=SOAP_BODY.encode(),
        headers={
            "SOAPACTION":
                '"urn:schemas-upnp-org:service:ZoneGroupTopology:1#GetZoneGroupState"',
            "Content-Type": 'text/xml; charset="utf-8"',
        })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as f:
            return f.read().decode("utf-8", "ignore")
    except Exception:
        return None


def _member_dict(el, role):
    d = {k.lower(): el.get(k) for k in MEMBER_ATTRS if el.get(k) is not None}
    for k in ("BootSeq", "WirelessMode", "ConnectionType", "ChannelFreq",
              "EthLink", "WifiEnabled", "Invisible"):
        lk = k.lower()
        if lk in d:
            try:
                d[lk] = int(d[lk])
            except ValueError:
                pass
    m = re.search(r"http://([\d.]+):", d.get("location", ""))
    if m:
        d["ip"] = m.group(1)
    d["role"] = role
    return d


TRANSPORT_BODY = (
    '<?xml version="1.0"?>'
    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
    's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
    '<u:GetTransportInfo xmlns:u="urn:schemas-upnp-org:service:AVTransport:1">'
    "<InstanceID>0</InstanceID></u:GetTransportInfo></s:Body></s:Envelope>")


def fetch_transport(ip, timeout=5.0):
    """Playback state of a group coordinator: PLAYING / PAUSED / STOPPED."""
    req = urllib.request.Request(
        f"http://{ip}:1400/MediaRenderer/AVTransport/Control",
        data=TRANSPORT_BODY.encode(),
        headers={
            "SOAPACTION":
                '"urn:schemas-upnp-org:service:AVTransport:1#GetTransportInfo"',
            "Content-Type": 'text/xml; charset="utf-8"',
        })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as f:
            m = re.search(r"<CurrentTransportState>(\w+)</CurrentTransportState>",
                          f.read().decode("utf-8", "ignore"))
            return m.group(1) if m else None
    except Exception:
        return None


def parse_zone_groups(soap_xml):
    """→ list of {id, coordinator, members:[...]}; satellites flagged by role."""
    m = re.search(r"<ZoneGroupState>(.*?)</ZoneGroupState>", soap_xml, re.S)
    if not m:
        return []
    inner = html.unescape(m.group(1))
    if not inner.strip().startswith("<"):
        return []
    root = ET.fromstring(f"<root>{inner}</root>")
    groups = []
    for zg in root.iter("ZoneGroup"):
        g = {"id": zg.get("ID"), "coordinator": zg.get("Coordinator"),
             "members": []}
        for mem in zg.iter("ZoneGroupMember"):
            g["members"].append(_member_dict(mem, "member"))
            for sat in mem.iter("Satellite"):
                g["members"].append(_member_dict(sat, "satellite"))
        groups.append(g)
    return groups
