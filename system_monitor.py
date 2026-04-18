#!/usr/bin/env python3

"""
  Written by: Will Armijo <will.armijo@gmail.com>
  Script Name: system_monitor.py
  Purpose: A live self-refreshing HTML page system resource dashboard
  Usage: Open web browser and nvigate to, http://localhost:8080
  Prerequires: pip install psutil
"""

import json
import time
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime

try:
    import psutil
except ImportError:
    raise SystemExit("Missing dependency: run  pip install psutil  then retry.")


# ──────────────────────────────────────────────
#  Data collector
# ──────────────────────────────────────────────

def get_stats() -> dict:
    cpu_per   = psutil.cpu_percent(interval=0.2, percpu=True)
    cpu_avg   = sum(cpu_per) / len(cpu_per)
    cpu_freq  = psutil.cpu_freq()
    mem       = psutil.virtual_memory()
    swap      = psutil.swap_memory()
    disk      = psutil.disk_usage("/")
    net       = psutil.net_io_counters()
    temps     = {}
    try:
        raw = psutil.sensors_temperatures()
        for key, entries in raw.items():
            for e in entries:
                if e.current and e.current > 0:
                    label = e.label or key
                    temps[label] = round(e.current, 1)
    except AttributeError:
        pass  # Windows / macOS without lm-sensors

    boot_time = psutil.boot_time()
    uptime_s  = int(time.time() - boot_time)
    h, rem    = divmod(uptime_s, 3600)
    m, s      = divmod(rem, 60)

    procs = []
    for p in sorted(psutil.process_iter(["pid","name","cpu_percent","memory_percent"]),
                    key=lambda x: x.info["cpu_percent"] or 0, reverse=True)[:8]:
        procs.append({
            "pid":  p.info["pid"],
            "name": p.info["name"],
            "cpu":  round(p.info["cpu_percent"] or 0, 1),
            "mem":  round(p.info["memory_percent"] or 0, 1),
        })

    return {
        "ts":         datetime.now().strftime("%H:%M:%S"),
        "cpu_avg":    round(cpu_avg, 1),
        "cpu_per":    [round(c, 1) for c in cpu_per],
        "cpu_freq":   round(cpu_freq.current, 0) if cpu_freq else 0,
        "cpu_cores":  len(cpu_per),
        "mem_total":  mem.total,
        "mem_used":   mem.used,
        "mem_pct":    mem.percent,
        "swap_total": swap.total,
        "swap_used":  swap.used,
        "swap_pct":   swap.percent,
        "disk_total": disk.total,
        "disk_used":  disk.used,
        "disk_pct":   disk.percent,
        "net_sent":   net.bytes_sent,
        "net_recv":   net.bytes_recv,
        "temps":      temps,
        "uptime":     f"{h:02d}:{m:02d}:{s:02d}",
        "procs":      procs,
    }


# ──────────────────────────────────────────────
#  HTML template (served once, JS polls /data)
# ──────────────────────────────────────────────

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SYS//MONITOR</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=Exo+2:wght@300;600;800&display=swap" rel="stylesheet">
<style>
  :root {
    --bg:      #05080f;
    --panel:   #0b1120;
    --border:  #1a2a45;
    --accent:  #00e5ff;
    --accent2: #ff4d6d;
    --accent3: #b8ff57;
    --dim:     #3a5070;
    --text:    #cde4f5;
    --mono:    'Share Tech Mono', monospace;
    --sans:    'Exo 2', sans-serif;
  }
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  html, body { height: 100%; background: var(--bg); color: var(--text); font-family: var(--mono); overflow-x: hidden; }

  /* scanline overlay */
  body::after {
    content: '';
    position: fixed; inset: 0; pointer-events: none; z-index: 999;
    background: repeating-linear-gradient(0deg, transparent, transparent 2px, rgba(0,0,0,.08) 2px, rgba(0,0,0,.08) 4px);
  }

  header {
    display: flex; align-items: center; justify-content: space-between;
    padding: 18px 28px;
    border-bottom: 1px solid var(--border);
    background: linear-gradient(90deg, #07101e 60%, #0a1628);
  }
  header h1 {
    font-family: var(--sans); font-weight: 800; font-size: 1.5rem;
    letter-spacing: 4px; text-transform: uppercase;
    color: var(--accent);
    text-shadow: 0 0 18px var(--accent);
  }
  #clock { font-size: .85rem; color: var(--dim); letter-spacing: 2px; }
  #uptime-val { color: var(--accent3); }

  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
    gap: 16px;
    padding: 20px 24px;
  }

  .card {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 18px 20px;
    position: relative;
    overflow: hidden;
    transition: border-color .3s;
  }
  .card:hover { border-color: var(--accent); }
  .card::before {
    content: '';
    position: absolute; top: 0; left: 0; right: 0; height: 2px;
    background: linear-gradient(90deg, transparent, var(--accent), transparent);
    opacity: .6;
  }
  .card-title {
    font-family: var(--sans); font-weight: 600; font-size: .7rem;
    letter-spacing: 3px; text-transform: uppercase;
    color: var(--dim); margin-bottom: 14px;
  }
  .big-num {
    font-size: 2.8rem; font-weight: 800; font-family: var(--sans);
    line-height: 1; color: var(--accent);
    text-shadow: 0 0 24px rgba(0,229,255,.4);
  }
  .big-num span { font-size: 1rem; color: var(--dim); }

  /* gauge bar */
  .bar-wrap {
    background: #0f1e32; border-radius: 3px; height: 8px;
    margin: 10px 0 4px; overflow: hidden;
  }
  .bar-fill {
    height: 100%; border-radius: 3px;
    background: linear-gradient(90deg, var(--accent), var(--accent2));
    transition: width .8s ease;
    box-shadow: 0 0 8px rgba(0,229,255,.5);
  }
  .bar-fill.green { background: linear-gradient(90deg, var(--accent3), #00c49a); }
  .bar-fill.red   { background: linear-gradient(90deg, var(--accent2), #ff8c00); }

  .sub-row { display: flex; justify-content: space-between; font-size: .75rem; color: var(--dim); margin-top: 4px; }
  .sub-row .val { color: var(--text); }

  /* per-core grid */
  #cores-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(46px, 1fr));
    gap: 6px; margin-top: 10px;
  }
  .core-cell {
    background: #0f1e32; border-radius: 4px; padding: 6px 4px;
    text-align: center; font-size: .65rem; color: var(--dim);
  }
  .core-cell .cpct { font-size: .9rem; font-weight: 700; color: var(--accent3); }

  /* process table */
  table { width: 100%; border-collapse: collapse; font-size: .73rem; margin-top: 8px; }
  thead th { color: var(--dim); font-weight: 400; letter-spacing: 1px; padding: 4px 6px; text-align: left; border-bottom: 1px solid var(--border); }
  tbody tr:hover { background: rgba(0,229,255,.04); }
  tbody td { padding: 5px 6px; border-bottom: 1px solid rgba(26,42,69,.5); }
  .pid  { color: var(--dim); }
  .pname{ max-width: 130px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--text); }
  .pcpu { color: var(--accent2); text-align: right; }
  .pmem { color: var(--accent3); text-align: right; }

  /* temps */
  #temps-list { margin-top: 8px; display: flex; flex-wrap: wrap; gap: 10px; }
  .temp-chip {
    background: #0f1e32; border: 1px solid var(--border); border-radius: 4px;
    padding: 6px 12px; font-size: .75rem;
  }
  .temp-chip .tv { font-size: 1.1rem; color: var(--accent2); font-family: var(--sans); font-weight: 600; }

  /* network */
  .net-row { display: flex; gap: 16px; margin-top: 10px; }
  .net-box {
    flex: 1; background: #0f1e32; border-radius: 4px;
    padding: 10px 14px; text-align: center;
  }
  .net-box .nl { font-size: .65rem; color: var(--dim); letter-spacing: 1px; margin-bottom: 4px; }
  .net-box .nv { font-size: 1.1rem; color: var(--accent); }

  .wide { grid-column: 1 / -1; }

  #status-dot {
    display: inline-block; width: 8px; height: 8px; border-radius: 50%;
    background: var(--accent3); box-shadow: 0 0 8px var(--accent3);
    animation: pulse 1.4s infinite;
  }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.3} }
  #status-label { font-size: .7rem; color: var(--dim); margin-left: 6px; letter-spacing: 1px; }
</style>
</head>
<body>
<header>
  <h1>⬡ SYS//MONITOR</h1>
  <div style="display:flex;align-items:center;gap:20px">
    <div><span id="status-dot"></span><span id="status-label">LIVE</span></div>
    <div id="clock">UP <span id="uptime-val">--:--:--</span> &nbsp;|&nbsp; <span id="ts-val">--:--:--</span></div>
  </div>
</header>

<div class="grid">

  <!-- CPU Avg -->
  <div class="card">
    <div class="card-title">CPU Usage</div>
    <div class="big-num" id="cpu-avg">0<span>%</span></div>
    <div class="bar-wrap"><div class="bar-fill" id="cpu-bar" style="width:0%"></div></div>
    <div class="sub-row">
      <span>Frequency</span><span class="val" id="cpu-freq">—</span>
    </div>
    <div class="sub-row">
      <span>Cores</span><span class="val" id="cpu-cores">—</span>
    </div>
  </div>

  <!-- Memory -->
  <div class="card">
    <div class="card-title">Memory (RAM)</div>
    <div class="big-num" id="mem-pct">0<span>%</span></div>
    <div class="bar-wrap"><div class="bar-fill green" id="mem-bar" style="width:0%"></div></div>
    <div class="sub-row"><span>Used</span><span class="val" id="mem-used">—</span></div>
    <div class="sub-row"><span>Total</span><span class="val" id="mem-total">—</span></div>
  </div>

  <!-- Swap -->
  <div class="card">
    <div class="card-title">Swap</div>
    <div class="big-num" id="swap-pct">0<span>%</span></div>
    <div class="bar-wrap"><div class="bar-fill red" id="swap-bar" style="width:0%"></div></div>
    <div class="sub-row"><span>Used</span><span class="val" id="swap-used">—</span></div>
    <div class="sub-row"><span>Total</span><span class="val" id="swap-total">—</span></div>
  </div>

  <!-- Disk -->
  <div class="card">
    <div class="card-title">Disk ( / )</div>
    <div class="big-num" id="disk-pct">0<span>%</span></div>
    <div class="bar-wrap"><div class="bar-fill red" id="disk-bar" style="width:0%"></div></div>
    <div class="sub-row"><span>Used</span><span class="val" id="disk-used">—</span></div>
    <div class="sub-row"><span>Total</span><span class="val" id="disk-total">—</span></div>
  </div>

  <!-- Network -->
  <div class="card">
    <div class="card-title">Network I/O (cumulative)</div>
    <div class="net-row">
      <div class="net-box"><div class="nl">▲ SENT</div><div class="nv" id="net-sent">—</div></div>
      <div class="net-box"><div class="nl">▼ RECV</div><div class="nv" id="net-recv">—</div></div>
    </div>
  </div>

  <!-- Temps -->
  <div class="card" id="temps-card">
    <div class="card-title">Temperatures</div>
    <div id="temps-list"><span style="color:var(--dim);font-size:.75rem">No sensors detected</span></div>
  </div>

  <!-- Per-core -->
  <div class="card wide">
    <div class="card-title">Per-Core CPU Usage</div>
    <div id="cores-grid"></div>
  </div>

  <!-- Processes -->
  <div class="card wide">
    <div class="card-title">Top Processes (by CPU)</div>
    <table>
      <thead><tr><th>PID</th><th>Name</th><th style="text-align:right">CPU%</th><th style="text-align:right">MEM%</th></tr></thead>
      <tbody id="proc-tbody"></tbody>
    </table>
  </div>

</div>

<script>
const fmt = (bytes) => {
  if (bytes >= 1e9) return (bytes/1e9).toFixed(1)+' GB';
  if (bytes >= 1e6) return (bytes/1e6).toFixed(1)+' MB';
  return (bytes/1e3).toFixed(0)+' KB';
};

async function refresh() {
  try {
    const r = await fetch('/data');
    const d = await r.json();

    document.getElementById('ts-val').textContent     = d.ts;
    document.getElementById('uptime-val').textContent = d.uptime;

    // CPU
    document.getElementById('cpu-avg').innerHTML  = d.cpu_avg+'<span>%</span>';
    document.getElementById('cpu-bar').style.width = d.cpu_avg+'%';
    document.getElementById('cpu-freq').textContent = d.cpu_freq+' MHz';
    document.getElementById('cpu-cores').textContent = d.cpu_cores;

    // Memory
    document.getElementById('mem-pct').innerHTML  = d.mem_pct+'<span>%</span>';
    document.getElementById('mem-bar').style.width = d.mem_pct+'%';
    document.getElementById('mem-used').textContent  = fmt(d.mem_used);
    document.getElementById('mem-total').textContent = fmt(d.mem_total);

    // Swap
    document.getElementById('swap-pct').innerHTML  = d.swap_pct+'<span>%</span>';
    document.getElementById('swap-bar').style.width = d.swap_pct+'%';
    document.getElementById('swap-used').textContent  = fmt(d.swap_used);
    document.getElementById('swap-total').textContent = fmt(d.swap_total);

    // Disk
    document.getElementById('disk-pct').innerHTML  = d.disk_pct+'<span>%</span>';
    document.getElementById('disk-bar').style.width = d.disk_pct+'%';
    document.getElementById('disk-used').textContent  = fmt(d.disk_used);
    document.getElementById('disk-total').textContent = fmt(d.disk_total);

    // Network
    document.getElementById('net-sent').textContent = fmt(d.net_sent);
    document.getElementById('net-recv').textContent = fmt(d.net_recv);

    // Temps
    const tl = document.getElementById('temps-list');
    const keys = Object.keys(d.temps);
    if (keys.length) {
      tl.innerHTML = keys.map(k =>
        `<div class="temp-chip"><div>${k}</div><div class="tv">${d.temps[k]}°C</div></div>`
      ).join('');
    }

    // Per-core
    const cg = document.getElementById('cores-grid');
    cg.innerHTML = d.cpu_per.map((c,i) =>
      `<div class="core-cell"><div class="cpct">${c}%</div><div>C${i}</div></div>`
    ).join('');

    // Processes
    const tb = document.getElementById('proc-tbody');
    tb.innerHTML = d.procs.map(p =>
      `<tr><td class="pid">${p.pid}</td><td class="pname">${p.name}</td>
       <td class="pcpu">${p.cpu}</td><td class="pmem">${p.mem}</td></tr>`
    ).join('');

  } catch(e) { console.warn('fetch error', e); }
}

refresh();
setInterval(refresh, 1500);
</script>
</body>
</html>
"""


# ──────────────────────────────────────────────
#  HTTP handler
# ──────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_): pass  # silence access log

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            body = HTML.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif self.path == "/data":
            body = json.dumps(get_stats()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        else:
            self.send_response(404)
            self.end_headers()


# ──────────────────────────────────────────────
#  Entry point
# ──────────────────────────────────────────────

PORT = 8080

if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"  ┌─────────────────────────────────────────┐")
    print(f"  │  SYS//MONITOR running                   │")
    print(f"  │  Open → http://localhost:{PORT}            │")
    print(f"  │  Press Ctrl-C to stop                   │")
    print(f"  └─────────────────────────────────────────┘")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped.")
        server.server_close()
