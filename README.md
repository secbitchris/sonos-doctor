# sonos-doctor

Portable Sonos + network health diagnostics — bring it to any network, get a
verdict. Python 3, **stdlib only**, read-only against the network.

Born from a real incident: a Sonos Boost silently won a LAN's spanning-tree
root election and degraded the whole network. Off-the-shelf Sonos tools don't
check for that. This one does — from the speakers' own point of view, no
controller required.

## What it collects

| Source | What it yields |
|---|---|
| SSDP discovery | every ZonePlayer on the segment |
| `/xml/device_description.xml` | room, model, serial, ethernet MAC |
| `/support/review` (ONE fetch, any speaker) | whole-household dump: SonosNet channel, noise floor, OFDM ANI, PHY errors, and the neighbour signal table behind Sonos's classic **network matrix** |
| `/status/showstp` | each speaker's own STP bridge view — root bridge identity, per-port states, forwarding vs blocking SonosNet tunnels |
| `/status/ifconfig` | radio MAC (the matrix keys on radio MACs, identity XML on ethernet MACs — this closes the gap) |
| ZoneGroupTopology SOAP | groups, bonded pairs/satellites, BootSeq (reboot counter) |
| ICMP | loss / latency / jitter per speaker |
| UniFi controller *(optional)* | RF stats, switch/port per speaker, switch STP priorities. Degrades gracefully to nothing. |

## Checks (each earned from a real incident)

- **STP root watch** — CRIT if a Sonos player is the root bridge (speaker-side
  `showstp`, works with zero infrastructure access; controller-side too when
  available). WARN if switches sit at priority ≥ 32768 (losable election) or
  players disagree on the root.
- **Weak mesh path in use** — matrix signal < 15 dB on a *forwarding* STP
  tunnel (audio actually rides it).
- **Reachability** — port 1400 closed; in-household but SSDP-silent
  (multicast broken); controller says up but player dead (stale state).
- **Link quality** — packet loss, jitter > 30 ms, OFDM ANI ≥ 8, noise floor
  ≥ −87 dBm, SonosNet channel mismatch.
- **Labels lie** — controller alias doesn't match the Sonos identity
  (verify by OUI/causality, never by name).
- **History** — device disappeared vs previous snapshot; reboots detected via
  BootSeq deltas.

## Taking it to another network

Build the single-file artifact and copy it to any laptop with Python ≥ 3.8:

```sh
./build.sh                       # → dist/sonos-doctor.pyz (~25 KB)
python3 sonos-doctor.pyz snapshot --no-unifi
```

Requirements on site: be on the same VLAN/segment as the speakers, nothing
else. If SSDP multicast is filtered (a classic "the app can't find my
speakers" network), the snapshot automatically falls back to a TCP sweep of
the local /24 — and reports the broken multicast as a finding, because that
IS a diagnosis. Force or widen the sweep with `--sweep [CIDR]`.

Run `python3 -m sonosdoctor selftest` from the source tree to verify a
checkout (35 tests pinned against real captured fleet data — including
synthetic recreations of the incidents each check exists for).

Packaged builds run a built-in smoke (`selftest` → 4 checks: parsers,
S1 detection, battery format, root-bridge detector) since the full suite
stays in the source tree.

Device-generation notes: S1 firmware (≤ 57.x) predates the `SWGen` tag, so
generation is derived from the firmware major version. Era-class speakers
(Era 100/300, Move 2) expose the `:1400/status` endpoints but have no
SonosNet radio and don't populate the matrix — they get discovery,
identity, ping, topology and checks; mesh sections auto-hide. Battery
parsing matches the format SoCo verifies against real Move/Roam hardware
(`<Data name="Level">…`).

Note: `tests/fixtures/` contains real (scrubbed) captures from the home
fleet — keep the repo private, or re-scrub before publishing.

## Use

```sh
python3 -m sonosdoctor snapshot            # collect + check + store; exit 0/1/2 = ok/warn/crit
python3 -m sonosdoctor snapshot --no-unifi # pure network probe, no controller
python3 -m sonosdoctor snapshot --sweep    # force a TCP sweep (multicast-broken sites)
python3 -m sonosdoctor watch --interval 60 # on-site mode: live finding diffs each pass
python3 -m sonosdoctor report              # re-print latest stored snapshot
python3 -m sonosdoctor report --html > report.html  # self-contained leave-behind page
python3 -m sonosdoctor history             # list stored snapshots
python3 -m sonosdoctor import-legacy sonos-*.json   # ingest old sonosdiag.py output
python3 -m sonosdoctor serve --port 8090   # web UI: matrix, tree, findings, trends, guide
python3 -m sonosdoctor prune --keep-days 180        # retention
python3 -m sonosdoctor selftest            # run the bundled test suite (source tree only)
```

History lives in SQLite (`~/.sonos-doctor/history.db`, override with `--db`).
The `snapshot` exit code makes cron alerting trivial.

Field-visit extras: multiple households on one LAN are each collected
(S1/S2 splits detected), WiFi-extender-fed players are flagged, Roam/Move
battery is read, and foreign radios competing for airtime are identified by
OUI. The web UI ships a "How to read this" tab — triage order, findings
reference with actions, healthy baselines — which auto-hides UniFi- and
mesh-specific content when the viewed snapshot has neither. The same guide
is embedded in `report --html` exports.

UniFi enrichment reads `UNIFI_API_KEY` from the environment (or a path in
`UNIFI_KEYFILE`), `UNIFI_HOST` defaults to `192.168.1.1`. No credentials are
ever written by this tool.

## Web UI

`serve` renders the recreated network matrix (row hears column at N dB,
sequential colour ramp, forwarding tunnels outlined), the findings list,
per-player fleet health, and jitter sparklines over stored history. Read-only
over the DB — it never probes the network itself.

## Layout

```
sonosdoctor/
  discovery.py   SSDP
  collect.py     per-speaker probes (1400, identity, ping, radio MAC)
  review.py      /support/review parser → fleet + matrix edges
  stp.py         /status/showstp parser → root bridge + tunnel states
  topology.py    ZoneGroupTopology SOAP → groups/bonded/BootSeq
  unifi.py       optional controller enrichment
  snapshot.py    orchestrates one full collection
  checks.py      findings engine (thresholds in one dict)
  store.py       SQLite history + legacy import
  web.py         stdlib web UI
```
