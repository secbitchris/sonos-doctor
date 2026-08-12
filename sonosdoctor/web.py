"""Web UI — the recreated network matrix + fleet health over stored history.

Stdlib http.server. Read-only over the SQLite history; never probes the
network itself (snapshots come from the CLI/cron).
"""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import store

PAGE = """<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sonos Doctor</title>
<style>
:root {
  color-scheme: light;
  --surface-1:#fcfcfb; --page:#f9f9f7;
  --ink:#0b0b0b; --ink-2:#52514e; --muted:#898781;
  --grid:#e1e0d9; --baseline:#c3c2b7; --ring:rgba(11,11,11,.10);
  --seq-100:#cde2fb; --seq-200:#9ec5f4; --seq-300:#6da7ec; --seq-400:#3987e5;
  --seq-500:#256abf; --seq-600:#184f95; --seq-700:#0d366b;
  --series-1:#2a78d6;
  --st-good:#0ca30c; --st-warn:#fab219; --st-serious:#ec835a; --st-crit:#d03b3b;
}
@media (prefers-color-scheme: dark) {
  :root {
    color-scheme: dark;
    --surface-1:#1a1a19; --page:#0d0d0d;
    --ink:#ffffff; --ink-2:#c3c2b7; --muted:#898781;
    --grid:#2c2c2a; --baseline:#383835; --ring:rgba(255,255,255,.10);
    --series-1:#3987e5;
  }
}
* { box-sizing:border-box; }
body { margin:0; background:var(--page); color:var(--ink);
  font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif; }
main { max-width:1200px; margin:0 auto; padding:20px; }
h1 { font-size:18px; margin:0 0 2px; }
h2 { font-size:14px; margin:26px 0 8px; color:var(--ink-2);
  text-transform:uppercase; letter-spacing:.04em; }
.sub { color:var(--muted); font-size:12.5px; }
.card { background:var(--surface-1); border:1px solid var(--ring);
  border-radius:10px; padding:14px 16px; margin-top:8px; overflow-x:auto; }
.tiles { display:flex; gap:10px; flex-wrap:wrap; margin-top:14px; }
.tile { background:var(--surface-1); border:1px solid var(--ring);
  border-radius:10px; padding:10px 16px; min-width:120px; }
.tile b { display:block; font-size:22px; font-weight:600; }
.tile span { color:var(--muted); font-size:12px; }
table { border-collapse:collapse; font-size:13px; width:100%; }
th { text-align:left; color:var(--muted); font-weight:500; padding:4px 8px;
  border-bottom:1px solid var(--baseline); white-space:nowrap; }
td { padding:4px 8px; border-bottom:1px solid var(--grid); white-space:nowrap; }
td.num, th.num { text-align:right; font-variant-numeric:tabular-nums; }
.matrix td { width:26px; height:22px; padding:0; text-align:center;
  border:2px solid var(--surface-1); border-radius:4px; font-size:10.5px;
  cursor:default; }
.matrix .rowhead { text-align:right; padding-right:8px; color:var(--ink-2);
  font-size:12px; border:none; max-width:170px; overflow:hidden;
  text-overflow:ellipsis; }
.matrix .colhead { writing-mode:vertical-rl; transform:rotate(180deg);
  font-size:10.5px; color:var(--muted); padding:2px 0; height:auto;
  max-height:150px; border:none; white-space:nowrap; }
.matrix td.inuse { outline:2px solid var(--ink); outline-offset:-2px; }
.finding { display:flex; gap:10px; padding:7px 0;
  border-bottom:1px solid var(--grid); align-items:baseline; }
.finding:last-child { border-bottom:none; }
.badge { font-size:11px; font-weight:600; padding:1px 8px; border-radius:9px;
  white-space:nowrap; }
.b-crit { background:var(--st-crit); color:#fff; }
.b-warn { background:var(--st-warn); color:#0b0b0b; }
.b-info { background:var(--grid); color:var(--ink-2); }
.finding .msg { color:var(--ink-2); }
.finding .subj { font-weight:600; }
.legend { display:flex; align-items:center; gap:4px; margin:10px 0 2px;
  font-size:12px; color:var(--muted); }
.legend i { width:22px; height:12px; border-radius:3px; display:inline-block; }
svg.spark { display:block; }
#tip { position:fixed; pointer-events:none; background:var(--surface-1);
  border:1px solid var(--ring); border-radius:8px; padding:7px 10px;
  font-size:12px; box-shadow:0 4px 14px rgba(0,0,0,.18); display:none;
  z-index:10; max-width:290px; white-space:normal; }
select { font:inherit; background:var(--surface-1); color:var(--ink);
  border:1px solid var(--baseline); border-radius:7px; padding:3px 8px; }
#tabs { display:flex; gap:6px; margin-top:14px;
  border-bottom:1px solid var(--baseline); }
#tabs .tab { padding:7px 14px; cursor:pointer; color:var(--muted);
  border:1px solid transparent; border-bottom:none;
  border-radius:8px 8px 0 0; font-size:13.5px; user-select:none; }
#tabs .tab.on { color:var(--ink); background:var(--surface-1);
  border-color:var(--ring); font-weight:600; }
.guide h3 { font-size:13.5px; margin:20px 0 6px; }
.guide p, .guide li { color:var(--ink-2); font-size:13.5px; }
.guide code { background:var(--grid); padding:0 5px; border-radius:4px;
  font-size:12.5px; }
.guide table td { white-space:normal; vertical-align:top; }
.guide .sev { font-weight:600; white-space:nowrap; }
</style></head>
<body><main>
<h1>Sonos Doctor</h1>
<div class="sub" id="meta">loading…</div>
<div style="margin-top:10px"><label class="sub">snapshot
<select id="snapsel"></select></label></div>
<nav id="tabs"><span class="tab on" data-tab="dash">Dashboard</span><span
 class="tab" data-tab="guide">How to read this</span></nav>
<div id="tab-dash">
<div class="tiles" id="tiles"></div>
<h2>Findings</h2><div class="card" id="findings"></div>
<h2>Network matrix <span class="sub" style="text-transform:none">— row hears
column at N dB; outlined cells are forwarding SonosNet tunnels (paths audio
actually rides)</span></h2>
<div class="card"><div class="legend" id="mlegend"></div>
<div id="matrix"></div></div>
<h2>Recent changes <span class="sub" style="text-transform:none">— when
warnings appeared (+) and cleared (−) across snapshots</span></h2>
<div class="card" id="timeline"></div>
<h2>Mesh tree <span class="sub" style="text-transform:none">— who rides whom:
each speaker's actual STP uplink, with the signal it hears its parent at</span></h2>
<div class="card" id="tree"></div>
<h2>Fleet</h2><div class="card" id="fleet"></div>
</div>
<div id="tab-guide" class="guide" hidden>
<h2>Triage order</h2><div class="card">
<p>Read top to bottom: <b>tiles</b> (anything critical?) → <b>findings</b>
(what the doctor thinks) → <b>recent changes</b> (when it started) →
<span class="mesh-only"><b>matrix / mesh tree</b> (which link or parent is to
blame) → </span><b>fleet</b> (the sick speaker's vitals and history). One
snapshot is a photo; the trend is the diagnosis — flip the snapshot selector
or read the timeline before concluding anything.</p></div>

<h2>Sections</h2><div class="card">
<h3>Snapshot selector</h3>
<p>Every collection run is stored. Pick an older one and the whole page
re-renders as the network looked then — use it to answer "was it like this
before?". <code>△n</code> = warnings in that snapshot.</p>
<h3>Tiles</h3>
<p><b>critical</b> = broken right now. <b>worst jitter</b> &gt; 30 ms is
where grouped-room audio starts dropping. <b>bridge points</b> = wired
Sonos bridging the mesh onto the LAN: a drop by one means a bridge went
offline; an increase means someone wired a speaker somewhere new.</p>
<h3>Findings</h3>
<p><span class="sev" style="color:var(--st-crit)">critical</span> — act now
(player unreachable, a Sonos won the STP root election).
<span class="sev" style="color:var(--st-warn)">warning</span> — degraded but
functioning (loss, jitter, interference, weak paths).
<span class="sev">info</span> — context, not a problem by itself. Every code
is listed in the reference below.</p>
<div class="mesh-only"><h3>Network matrix</h3>
<p><b>Row hears column at N dB</b> — direction matters; compare cell (A,B)
with (B,A): more than ~15 dB apart points at noise or obstruction at the
quieter end. Darker = stronger. Blank = can't hear each other (normal for
distant rooms; home-theater satellites only talk 5 GHz to their soundbar, so
their rows are nearly empty). <b>Outlined cells are the links audio actually
rides</b> (forwarding STP tunnels on both ends) — check those are dark, and
check a problem room has non-blank alternatives in its row (a row with one
usable cell has no fallback). Bands: ≥45 excellent · 30–44 good ·
20–29 marginal · &lt;20 audio at risk.</p>
<h3>Mesh tree</h3>
<p>Each speaker under the wired bridge its audio actually flows through,
with the dB it hears that parent at (orange &lt; 20 dB = weak link in use).
Indentation = extra hops; only home-theater satellites should sit at depth 2.
♪ = that group is playing now. Use it for blast radius: everything indented
under a bridge is affected if that bridge misbehaves.</p></div>
<h3>Recent changes</h3>
<p>Warnings appearing (<b style="color:var(--st-warn)">+</b>) and clearing
(<b style="color:var(--st-good)">−</b>) between snapshots. A symptom cluster
on one speaker across a short window (jitter, then SSDP-missing, then
recovery) is one problem, not three. Warnings that flap on/off sit at a
threshold edge — watch, don't chase.</p>
<h3>Fleet table</h3>
<p><b>1400</b> — Sonos control port; closed = the app cannot reach it.
<b>loss / avg / jitter</b> — ping vitals; any loss is worth a look, jitter
&gt; 30 ms breaks grouped audio, LAN avg should be &lt; 10 ms.
<b>ANI</b> (0–9) — how hard the radio fights interference; sustained 8–9 =
hostile RF <i>near that speaker</i> (Boosts/Bridges idle at 9 by design).
<b>noise</b> — floor in dBm; −95 or lower is healthy, −87 or higher warns.
<b>PHY err/h</b> — corrupted-frame rate; read it relatively (one speaker at
3× the fleet lives in a bad RF spot). Dashes appear when snapshots are taken
too close together (the counter resets on read).
<b>link</b> — the speaker's own connection truth<span class="unifi-only">,
plus which switch port its traffic enters the LAN on (<code>←</code>)</span>.
♪ = its group is playing. <b>jitter history</b> — hover for numbers.</p></div>

<h2>Findings reference</h2><div class="card"><table>
<tr><th>code</th><th>sev</th><th>meaning → what to do</th></tr>
<tr><td><code>stp-root-is-sonos</code></td><td class="sev" style="color:var(--st-crit)">crit</td>
<td>Players agree a Sonos is the LAN's spanning-tree ROOT — your network
topology is decided by a speaker; whole-LAN slowdowns follow. Set a core
switch's STP priority below 32768 (e.g. 4096).</td></tr>
<tr class="unifi-only"><td><code>stp-root-not-switch</code></td><td class="sev" style="color:var(--st-crit)">crit</td>
<td>Switches report a root bridge that isn't a managed switch. Same fix as
above; identify the device by MAC first.</td></tr>
<tr><td><code>port-1400-closed</code></td><td class="sev" style="color:var(--st-crit)">crit</td>
<td>Speaker answers ping but not the Sonos app port. Power-cycle it; if it
persists, factory reset is next.</td></tr>
<tr><td><code>packet-loss</code></td><td class="sev" style="color:var(--st-warn)">warn/crit</td>
<td>Dropped pings. Confirm on the trend (one bad sample flaps); then look at
its uplink signal<span class="mesh-only"> in the tree</span> and local RF.</td></tr>
<tr><td><code>high-jitter</code></td><td class="sev" style="color:var(--st-warn)">warn</td>
<td>Delay variance &gt; 30 ms — grouped rooms drift/drop. Same
investigation as loss; jitter usually points at airtime contention.</td></tr>
<tr><td><code>stp-root-disagreement</code></td><td class="sev" style="color:var(--st-warn)">warn</td>
<td>Players name different root bridges — mid-reconvergence snapshot or a
partitioned mesh. Re-snapshot; persistent disagreement = investigate.</td></tr>
<tr><td><code>ssdp-missing</code></td><td class="sev" style="color:var(--st-warn)">warn</td>
<td>In the household but silent to discovery multicast — the app will show
it as missing. If chronic on one speaker: its link. If fleet-wide: see
<code>multicast-broken</code>.</td></tr>
<tr><td><code>multicast-broken</code></td><td class="sev" style="color:var(--st-warn)">warn</td>
<td>SSDP found nothing but a TCP sweep found players — the network filters
multicast (IGMP snooping without querier, AP isolation, "WiFi
optimization"). This alone explains "the app can't find my speakers."</td></tr>
<tr><td><code>high-ani</code></td><td class="sev" style="color:var(--st-warn)">warn</td>
<td>Radio at max interference mitigation. Persistent on the same speakers =
find the 2.4 GHz source near them (cameras, baby monitors, microwaves,
neighbour WiFi).</td></tr>
<tr><td><code>noise-floor-high</code></td><td class="sev" style="color:var(--st-warn)">warn</td>
<td>RF noise floor ≥ −87 dBm — something is transmitting close by.
Physically relocate the speaker or the noise source.</td></tr>
<tr class="mesh-only"><td><code>weak-mesh-path</code></td><td class="sev" style="color:var(--st-warn)">warn</td>
<td>Audio is riding a tunnel weaker than 15 dB. Move the speaker or its
parent closer, or add/wire a bridge between them.</td></tr>
<tr class="mesh-only"><td><code>asymmetric-path</code></td><td class="sev" style="color:var(--st-warn)">warn</td>
<td>An in-use link ≥ 15 dB louder one way than the other — noise, antenna
placement, or obstruction at the quieter end. Investigate the end that
hears poorly.</td></tr>
<tr class="mesh-only"><td><code>channel-mismatch</code></td><td class="sev" style="color:var(--st-warn)">warn</td>
<td>SonosNet players on different channels — a split mesh (usually
mid-migration). Set one SonosNet channel and let it settle.</td></tr>
<tr class="unifi-only"><td><code>switch-priority-weak</code></td><td class="sev" style="color:var(--st-warn)">warn</td>
<td>Switches at STP priority ≥ 32768 can lose the root election to a Sonos
(which advertises ~32768). Lower core switch priorities.</td></tr>
<tr class="unifi-only"><td><code>link-up-but-dead</code></td><td class="sev" style="color:var(--st-warn)">warn</td>
<td>Controller says the client is up; the player doesn't answer. Stale
controller state or one-way path — trust the direct probe.</td></tr>
<tr><td><code>behind-wifi-extender</code></td><td class="sev" style="color:var(--st-warn)">warn</td>
<td>Player sits behind a WiFi repeater — the classic cause of drops and
ghost players (extenders mangle multicast). Move it to the main AP's
coverage or wire it.</td></tr>
<tr><td><code>multiple-households</code></td><td class="sev" style="color:var(--st-warn)">warn</td>
<td>Two+ Sonos systems share this LAN — they cannot see or group with each
other. Usually an S1/S2 split or a half-migrated system: decide on one
household and re-add strays to it.</td></tr>
<tr><td><code>mixed-generations</code></td><td class="sev" style="color:var(--st-warn)">warn</td>
<td>S1 and S2 players on one LAN. They can't group unless everything runs
S1. Options: all-S1, split systems deliberately, or replace S1 units.</td></tr>
<tr><td><code>low-battery</code></td><td class="sev" style="color:var(--st-warn)">warn</td>
<td>Portable (Roam/Move) ≤ 20% and not charging.</td></tr>
<tr><td><code>device-missing</code></td><td class="sev" style="color:var(--st-warn)">warn</td>
<td>Present in the previous snapshot, gone now. Unplugged, or lost its
network — check power first.</td></tr>
<tr class="mesh-only"><td><code>multiple-bridge-points</code></td><td class="sev">info</td>
<td>Several wired Sonos bridge the mesh — redundancy, healthy ONLY while
STP is healthy. Know where they are (listed in the finding).</td></tr>
<tr class="mesh-only"><td><code>mesh-reparented</code></td><td class="sev">info</td>
<td>A speaker's uplink moved. Occasional = normal self-healing; frequent
on one speaker = marginal RF path — check its signal options in the
matrix.</td></tr>
<tr class="mesh-only"><td><code>unknown-mesh-neighbor</code></td><td class="sev">info</td>
<td>A radio outside this household heard at strength — a neighbour's Sonos
on the same channel, or a forgotten device. It competes for airtime; change
SonosNet channel if it's loud.</td></tr>
<tr class="unifi-only"><td><code>controller-path-mismatch</code></td><td class="sev">info</td>
<td>Controller learned the speaker's MAC on a different port than its mesh
tree implies — stale switch FDB entry, or an undocumented cable path. If it
persists across snapshots, trace the port.</td></tr>
<tr class="unifi-only"><td><code>alias-oui-mismatch</code></td><td class="sev">info</td>
<td>Controller's name for this device doesn't look like the Sonos it
actually is. Labels lie — verify identity by MAC/OUI, then fix the
alias.</td></tr>
<tr><td><code>reboot-detected</code></td><td class="sev">info</td>
<td>BootSeq incremented — the speaker restarted since the previous
snapshot. Clusters of reboots = power or overheating.</td></tr>
</table></div>

<h2>Healthy baselines</h2><div class="card"><p>
Signal on an in-use link: <b>≥ 30 dB</b> · loss: <b>0%</b> · jitter:
<b>&lt; 10 ms</b> (worry at 30) · LAN avg latency: <b>&lt; 10 ms</b> ·
ANI: <b>≤ 7</b> (Boosts 8–9 normal) · noise floor: <b>≤ −95 dBm</b><span
class="mesh-only"> · one wired bridge minimum, satellites the only depth-2
nodes</span>.</p></div>

<h2>Command line</h2><div class="card"><p>
<code>snapshot</code> collect + check + store (exit 0/1/2 = clean/warn/crit)
· <code>snapshot --no-unifi</code> pure network probe ·
<code>snapshot --sweep</code> force a TCP sweep when multicast is broken ·
<code>watch --interval 60</code> live mode while moving speakers — prints
finding diffs each pass · <code>report --html &gt; report.html</code>
this page as one self-contained file (leave-behind) ·
<code>history</code> / <code>report --id N</code> stored snapshots ·
<code>prune --keep-days N</code> retention · <code>selftest</code> run the
bundled test suite.</p></div>
</div>
<div id="tip"></div>
<script>
const SEQ = ['--seq-100','--seq-200','--seq-300','--seq-400','--seq-500',
             '--seq-600','--seq-700'].map(v =>
             getComputedStyle(document.documentElement).getPropertyValue(v).trim());
const css = v => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const tip = document.getElementById('tip');
function showTip(ev, html) {
  tip.innerHTML = html; tip.style.display = 'block';
  const x = Math.min(ev.clientX + 14, innerWidth - 300);
  tip.style.left = x + 'px'; tip.style.top = (ev.clientY + 14) + 'px';
}
function hideTip() { tip.style.display = 'none'; }
function seqColor(db) {           // 0..~60 dB → sequential ramp
  if (db == null) return null;
  const i = Math.min(SEQ.length - 1, Math.floor(db / 9));
  return SEQ[i];
}
function nm(d) { return d.zone || d.room; }
function esc(s) { return String(s ?? '').replace(/[&<>"]/g,
  c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }

async function load(id) {
  const snap = await (await fetch('/api/snapshot' + (id ? '?id=' + id : ''))).json();
  if (!snap || snap.error) {
    document.getElementById('meta').textContent =
      'no snapshots yet — run: sonos-doctor snapshot';
    return;
  }
  render(snap);
  const hist = await (await fetch('/api/history')).json();
  sparks(snap, hist);
  renderTimeline(await (await fetch('/api/timeline')).json());
}

function renderTimeline(tl) {
  document.getElementById('timeline').innerHTML = tl.length ? tl.map(ev => {
    const item = (f, sign) =>
      `<span style="margin-right:14px">${sign == '+'
        ? `<b style="color:${f.severity == 'crit' ? 'var(--st-crit)' : 'var(--st-warn)'}">+</b>`
        : '<b style="color:var(--st-good)">−</b>'} ` +
      `[${esc(f.code)}] ${esc(f.subject)}</span>`;
    return `<div class="finding"><span class="sub" style="white-space:nowrap">` +
      `${esc(ev.ts.slice(5, 16).replace('T', ' '))}</span><span>` +
      ev.added.map(f => item(f, '+')).join('') +
      ev.resolved.map(f => item(f, '-')).join('') + '</span></div>';
  }).join('') : '<div class="sub">no warning changes recorded yet</div>';
}

document.querySelectorAll('#tabs .tab').forEach(t => t.onclick = () => {
  document.querySelectorAll('#tabs .tab').forEach(x =>
    x.classList.toggle('on', x === t));
  document.getElementById('tab-dash').hidden = t.dataset.tab != 'dash';
  document.getElementById('tab-guide').hidden = t.dataset.tab != 'guide';
});

function render(s) {
  const devs = s.devices || [], f = s._findings || [];
  // the guide only documents what this network actually has
  const uniOk = !!(s.unifi && s.unifi.available);
  const meshOk = (s.matrix || []).length > 0;
  document.querySelectorAll('.unifi-only').forEach(e => e.hidden = !uniOk);
  document.querySelectorAll('.mesh-only').forEach(e => e.hidden = !meshOk);
  document.getElementById('meta').textContent =
    `snapshot #${s._id} · ${s.generated} · collected on ${s.host}` +
    ` · UniFi ${s.unifi && s.unifi.available ? 'enriched' : 'not available'}`;
  const crit = f.filter(x => x.severity == 'crit').length,
        warn = f.filter(x => x.severity == 'warn').length;
  const loss = devs.filter(d => (d.ping || {}).loss_pct > 0).length;
  const jit = Math.max(0, ...devs.map(d => (d.ping || {}).jitter_ms || 0));
  document.getElementById('tiles').innerHTML = [
    [devs.length, 'players'],
    [crit, 'critical', crit ? 'var(--st-crit)' : null],
    [warn, 'warnings', warn ? 'var(--st-warn)' : null],
    [loss, 'with packet loss', loss ? 'var(--st-serious)' : null],
    [jit.toFixed(1) + ' ms', 'worst jitter'],
    [Object.keys(s.bridge_points || {}).length, 'bridge points'],
  ].map(([v, l, c]) =>
    `<div class="tile"><b${c ? ` style="color:${c}"` : ''}>${v}</b>` +
    `<span>${l}</span></div>`).join('');

  document.getElementById('findings').innerHTML = f.length ? f.map(x =>
    `<div class="finding"><span class="badge b-${x.severity}">` +
    `${{crit:'✗ critical', warn:'△ warning', info:'ⓘ info'}[x.severity]}</span>` +
    `<span><span class="subj">[${esc(x.code)}] ${esc(x.subject)}</span> ` +
    `<span class="msg">${esc(x.message)}</span></span></div>`).join('')
    : '<div class="sub">all clear</div>';

  // ---- matrix ----
  const byMac = {}; devs.forEach(d => { if (d.mac) byMac[d.mac.toLowerCase()] = d; });
  const order = devs.filter(d => d.mac)
    .sort((a, b) => (nm(a) || '').localeCompare(nm(b) || ''));
  const edges = {};
  (s.matrix || []).forEach(e => { edges[e.src_mac + '|' + e.dst_mac] = e; });
  // a tunnel is a real tree edge only when BOTH ends forward — the
  // root-side end of every idle tunnel reports 'forwarding' (designated
  // port), so one-sided state over-marks massively
  const inuse = new Set();
  devs.forEach(d => ((d.stp || {}).ports || []).forEach(p => {
    if (p.state == 'forwarding' && p.remote_state == 'forwarding' && p.tunnel_to) {
      const peer = devs.find(x => x.radio_mac == p.tunnel_to);
      if (peer && d.mac) inuse.add(d.mac.toLowerCase() + '|' + peer.mac.toLowerCase());
    }
  }));
  let h = '<table class="matrix"><tr><td class="rowhead"></td>' +
    order.map(d => `<td class="colhead">${esc(nm(d))}</td>`).join('') + '</tr>';
  for (const r of order) {
    h += `<tr><td class="rowhead">${esc(nm(r))}</td>`;
    for (const c of order) {
      if (r === c) { h += '<td style="background:var(--grid)"></td>'; continue; }
      const e = edges[r.mac.toLowerCase() + '|' + c.mac.toLowerCase()];
      const bg = e ? seqColor(e.from_db) : null;
      const used = inuse.has(r.mac.toLowerCase() + '|' + c.mac.toLowerCase());
      const light = e && e.from_db < 27;   // seq steps 100-300 need dark ink
      h += `<td class="${used ? 'inuse' : ''}"` +
        ` style="background:${bg || 'transparent'};color:${light ? '#0b0b0b' : '#ffffff'}"` +
        (e ? ` data-t="<b>${esc(nm(r))}</b> hears <b>${esc(nm(c))}</b> at ` +
             `<b>${e.from_db} dB</b> (reverse ${e.to_db} dB)` +
             `${used ? ' — forwarding STP tunnel (in use)' : ''}"` : '') +
        `>${e && e.from_db ? e.from_db : ''}</td>`;
    }
    h += '</tr>';
  }
  document.getElementById('matrix').innerHTML = h + '</table>';
  document.getElementById('mlegend').innerHTML = 'signal ' +
    SEQ.map(c => `<i style="background:${c}"></i>`).join('') +
    ' stronger&nbsp;&nbsp;·&nbsp;&nbsp;<i style="outline:2px solid var(--ink);' +
    'outline-offset:-2px;background:transparent"></i> in-use tunnel';
  document.querySelectorAll('.matrix td[data-t]').forEach(td => {
    td.onmousemove = ev => showTip(ev, td.dataset.t);
    td.onmouseleave = hideTip;
  });

  // ---- mesh tree ----
  const tree = s.mesh_tree || {};
  const kids = {};
  Object.entries(tree).forEach(([mac, n]) => {
    kids[n.parent || 'LAN'] = kids[n.parent || 'LAN'] || [];
    kids[n.parent || 'LAN'].push(mac);
  });
  const edgeDb = (child, parent) => {
    const e = edges[child + '|' + parent];
    return e ? e.from_db : null;
  };
  function branch(mac, depth) {
    const d = byMac[mac];
    const n = tree[mac] || {};
    const db = n.parent ? edgeDb(mac, n.parent) : null;
    const u = (d || {}).unifi || {};
    const where = !n.parent && u.switch ? ` — ${esc(u.switch)} p${u.sw_port}` : '';
    const sig = db != null
      ? ` <span style="color:${db < 20 ? 'var(--st-serious)' : 'var(--muted)'}">` +
        `${db} dB</span>` : '';
    let h = `<div style="padding:2px 0 2px ${depth * 26}px">` +
      `${depth ? '<span style="color:var(--baseline)">└</span> ' : ''}` +
      `<b>${esc(d ? nm(d) : mac)}</b>` +
      `${!n.parent ? ' <span class="sub">(wired bridge' + where + ')</span>' : sig}` +
      `${d && d.playing ? ' <span title="group is playing">♪</span>' : ''}</div>`;
    (kids[mac] || []).sort((a, b) => (edgeDb(b, mac) || 0) - (edgeDb(a, mac) || 0))
      .forEach(c => { h += branch(c, depth + 1); });
    return h;
  }
  let treeHtml = (kids['LAN'] || []).map(m => branch(m, 0)).join('');
  const orphanParents = Object.keys(kids).filter(p =>
    p != 'LAN' && !(p in tree));
  orphanParents.forEach(p => {
    treeHtml += `<div style="padding:6px 0 2px"><b>${esc(p)}</b> ` +
      `<span class="sub">(uplink outside the fleet — unresolved)</span></div>` +
      kids[p].map(c => branch(c, 1)).join('');
  });
  document.getElementById('tree').innerHTML =
    treeHtml || '<div class="sub">no tree data in this snapshot</div>';

  // ---- fleet table ----
  const rows = devs.map(d => {
    const p = d.ping || {}, u = d.unifi || {};
    // speaker's own view wins: UniFi marks every Sonos "wired" (mesh MACs
    // are learned on the bridge ports); for wireless, show the bridge port
    let link = d.wired_physical ? 'wired'
      : (d.connection_type ? d.connection_type.split(' ')[0].replace('Home', 'HT-5GHz')
         : (u.signal ? `wifi ${u.signal} dBm` : '—'));
    if (!d.wired_physical && u.switch) link += ` ← ${u.switch} p${u.sw_port}`;
    return `<tr><td>${esc(nm(d))}</td><td>${esc(d.ip)}</td>` +
      `<td>${esc(d.model_number || '')}</td>` +
      `<td>${d.tcp_1400_open === false ? '<b style="color:var(--st-crit)">closed</b>' : 'open'}</td>` +
      `<td class="num">${p.loss_pct ?? '—'}</td>` +
      `<td class="num">${p.avg_ms ?? '—'}</td>` +
      `<td class="num">${p.jitter_ms ?? '—'}</td>` +
      `<td class="num">${d.ani ?? '—'}</td>` +
      `<td class="num">${d.noise_floor ?? '—'}</td>` +
      `<td class="num">${d.phy_err_per_h != null ? d.phy_err_per_h.toLocaleString() : '—'}</td>` +
      `<td>${esc(link)}${d.playing ? ' ♪' : ''}</td>` +
      `<td><svg class="spark" data-mac="${esc((d.mac || '').toLowerCase())}"` +
      ` width="110" height="26"></svg></td></tr>`;
  }).join('');
  document.getElementById('fleet').innerHTML =
    '<table><tr><th>Room</th><th>IP</th><th>Model</th><th>1400</th>' +
    '<th class="num">loss %</th><th class="num">avg ms</th>' +
    '<th class="num">jitter</th><th class="num">ANI</th>' +
    '<th class="num">noise</th><th class="num">PHY err/h</th>' +
    '<th>link</th><th>jitter history</th></tr>' +
    rows + '</table>';
}

function sparks(snap, hist) {
  document.querySelectorAll('svg.spark').forEach(svg => {
    const rows = hist[svg.dataset.mac] || [];
    const vals = rows.map(r => r.jitter_ms).filter(v => v != null);
    if (vals.length < 2) return;
    const w = 110, h = 26, max = Math.max(...vals, 1);
    const pts = vals.map((v, i) =>
      `${(i / (vals.length - 1) * (w - 4) + 2).toFixed(1)},` +
      `${(h - 3 - v / max * (h - 6)).toFixed(1)}`).join(' ');
    svg.innerHTML =
      `<polyline points="${pts}" fill="none" stroke="${css('--series-1')}"` +
      ` stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`;
    svg.style.cursor = 'default';
    svg.onmousemove = ev => showTip(ev,
      `jitter over ${vals.length} snapshots — now ${vals[vals.length-1]} ms, ` +
      `max ${Math.max(...vals)} ms`);
    svg.onmouseleave = hideTip;
  });
}

async function boot() {
  if (window.EMBEDDED) {          // static exported report — no server
    document.querySelector('label.sub').style.display = 'none';
    render(EMBEDDED.snapshot);
    sparks(EMBEDDED.snapshot, EMBEDDED.history || {});
    renderTimeline(EMBEDDED.timeline || []);
    return;
  }
  const snaps = await (await fetch('/api/snapshots')).json();
  const sel = document.getElementById('snapsel');
  sel.innerHTML = snaps.map(s =>
    `<option value="${s.id}">#${s.id} · ${esc(s.ts)} · ${s.discovered} players` +
    `${s.crits ? ' · ✗' + s.crits : ''}${s.warns ? ' · △' + s.warns : ''}</option>`
  ).join('');
  sel.onchange = () => load(sel.value);
  load(snaps.length ? snaps[0].id : null);
}
boot();
</script></main></body></html>
"""


class Handler(BaseHTTPRequestHandler):
    db_path = None

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlparse(self.path)
        q = parse_qs(url.query)
        conn = store.open_db(self.db_path)
        try:
            if url.path == "/":
                body = PAGE.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif url.path == "/api/snapshots":
                self._json(store.list_snapshots(conn))
            elif url.path == "/api/snapshot":
                try:
                    sid = int(q["id"][0]) if q.get("id") else None
                except ValueError:
                    self._json({"error": "bad id"}, 400)
                    return
                snap = store.get_snapshot(conn, sid)
                self._json(snap if snap else {"error": "empty"})
            elif url.path == "/api/timeline":
                self._json(store.timeline(conn))
            elif url.path == "/api/history":
                self._json(store.all_histories(conn))
            else:
                self._json({"error": "not found"}, 404)
        finally:
            conn.close()

    def log_message(self, fmt, *args):
        pass


def serve(db_path, bind="127.0.0.1", port=8090):
    Handler.db_path = db_path
    srv = ThreadingHTTPServer((bind, port), Handler)
    print(f"sonos-doctor web UI on http://{bind}:{port}  (db: {db_path or store.DEFAULT_DB})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
