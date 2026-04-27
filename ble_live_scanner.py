#!/usr/bin/env python3
"""
Live Bluetooth Low Energy Device Scanner with Web UI
Features: continuous scanning, adjustable update rate, pause/resume, stale device cleanup
"""

import asyncio
import json
from datetime import datetime, timedelta
from typing import Dict, Tuple

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
import uvicorn

from bleak import BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

# ── Helper functions ────────────────────────────────────────────────────────
def rssi_quality(rssi: int | None) -> str:
    if rssi is None:
        return "N/A"
    if rssi >= -50:
        return "Excellent"
    if rssi >= -65:
        return "Good"
    if rssi >= -75:
        return "Fair"
    if rssi >= -85:
        return "Weak"
    return "Very Weak"


def rssi_color(rssi: int | None) -> str:
    if rssi is None:
        return "#6b7280"
    if rssi >= -50:
        return "#22c55e"
    if rssi >= -65:
        return "#eab308"
    if rssi >= -75:
        return "#f97316"
    return "#ef4444"


COMPANY_IDS: dict[int, str] = {
    0x004C: "Apple",
    0x0006: "Microsoft",
    0x0075: "Samsung",
    0x00E0: "Google",
    0x0059: "Nordic Semiconductor",
    0x0499: "Ruuvi",
    0x0157: "Garmin",
    0x0087: "Polar",
    0x0171: "Amazon",
    0x02D5: "Espressif",
    0x0131: "Fitbit",
    0x0310: "Tile",
}


def resolve_company(cid: int) -> str:
    return COMPANY_IDS.get(cid, f"Unknown (0x{cid:04X})")


# ── FastAPI App ─────────────────────────────────────────────────────────────
app = FastAPI(title="Live BLE Scanner")

# Global state
scanner_task: asyncio.Task | None = None
is_scanning = False
discovered: Dict[str, Tuple[BLEDevice, AdvertisementData, datetime]] = {}  # addr -> (device, adv, last_seen)
clients: set[WebSocket] = set()
update_interval = 2.0  # seconds

pause_event = asyncio.Event()
pause_event.set()  # start unpaused


async def broadcast_update():
    """Send current device list to all connected clients"""
    if not clients:
        return

    now = datetime.now()
    devices_list = []

    # Clean stale devices (> 45 seconds old)
    stale_threshold = now - timedelta(seconds=45)
    to_remove = [addr for addr, (_, _, ts) in discovered.items() if ts < stale_threshold]
    for addr in to_remove:
        discovered.pop(addr, None)

    for addr, (dev, adv, last_seen) in sorted(
        discovered.items(),
        key=lambda x: (x[1][1].rssi or -999),
        reverse=True,
    ):
        rssi = adv.rssi if adv.rssi is not None else getattr(dev, "rssi", None)
        name = adv.local_name or dev.name or "Unknown"

        manuf = []
        for cid, data in (adv.manufacturer_data or {}).items():
            manuf.append({
                "company": resolve_company(cid),
                "data": data.hex() if data else ""
            })

        devices_list.append({
            "address": addr,
            "name": name,
            "rssi": rssi,
            "rssi_quality": rssi_quality(rssi),
            "rssi_color": rssi_color(rssi),
            "tx_power": adv.tx_power,
            "services": adv.service_uuids or [],
            "manufacturer": manuf,
            "last_seen": last_seen.strftime("%H:%M:%S")
        })

    message = {
        "type": "update",
        "devices": devices_list,
        "device_count": len(devices_list),
        "timestamp": now.isoformat(),
        "scanning": is_scanning
    }

    dead_clients = []
    for ws in list(clients):
        try:
            await ws.send_text(json.dumps(message))
        except Exception:
            dead_clients.append(ws)

    for ws in dead_clients:
        clients.discard(ws)


async def ble_scanner_loop():
    """Background task: continuous BLE scanning"""
    global is_scanning, discovered

    def detection_callback(device: BLEDevice, adv: AdvertisementData):
        rssi = adv.rssi if adv.rssi is not None else getattr(device, "rssi", -999)
        if rssi > -110:  # reasonable filter to reduce noise
            discovered[device.address] = (device, adv, datetime.now())

    print("Starting continuous BLE scanner...")

    try:
        async with BleakScanner(detection_callback=detection_callback) as scanner:
            is_scanning = True
            await broadcast_update()

            while True:
                if not is_scanning:
                    await pause_event.wait()
                    continue

                await asyncio.sleep(update_interval)
                await broadcast_update()

    except asyncio.CancelledError:
        print("BLE scanner task cancelled.")
    except Exception as e:
        print(f"Scanner error: {e}")
    finally:
        is_scanning = False
        await broadcast_update()
        print("BLE scanner stopped.")


# ── WebSocket ───────────────────────────────────────────────────────────────
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    clients.add(websocket)

    try:
        await broadcast_update()  # initial state
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            if msg.get("action") == "set_interval":
                global update_interval
                update_interval = max(0.5, float(msg.get("value", 2.0)))
                await broadcast_update()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        clients.discard(websocket)


# ── Control endpoints ───────────────────────────────────────────────────────
@app.post("/scan/start")
async def start_scan():
    global scanner_task, is_scanning
    if scanner_task is None or scanner_task.done():
        pause_event.set()
        scanner_task = asyncio.create_task(ble_scanner_loop())
    else:
        pause_event.set()
    is_scanning = True
    await broadcast_update()
    return {"status": "started"}


@app.post("/scan/pause")
async def pause_scan():
    global is_scanning
    is_scanning = False
    pause_event.clear()
    await broadcast_update()
    return {"status": "paused"}


@app.post("/scan/stop")
async def stop_scan():
    global scanner_task, is_scanning, discovered
    is_scanning = False
    pause_event.clear()

    if scanner_task and not scanner_task.done():
        scanner_task.cancel()
        try:
            await scanner_task
        except asyncio.CancelledError:
            pass

    discovered.clear()
    await broadcast_update()
    return {"status": "stopped"}


# ── HTML Frontend ───────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def get_ui():
    html = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Live BLE Scanner</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        body { font-family: system-ui, sans-serif; }
        .device-row { transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1); }
        .signal-bar { height: 6px; background: linear-gradient(to right, #22c55e, #eab308, #ef4444); }
    </style>
</head>
<body class="bg-zinc-950 text-zinc-100">
    <div class="max-w-7xl mx-auto p-6">
        <div class="flex justify-between items-center mb-8">
            <div>
                <h1 class="text-4xl font-bold text-emerald-400">Live BLE Scanner</h1>
                <p class="text-zinc-400">Real-time Bluetooth Low Energy device discovery</p>
                <p class="text-zinc-500 text-sm">by Will Armijo • https://github.com/j3st3r</p>
            </div>
            <div class="flex gap-4 items-center">
                <div class="flex items-center gap-3">
                    <label class="text-sm text-zinc-400">Update every</label>
                    <input id="interval" type="number" value="2" min="0.5" step="0.5"
                           class="bg-zinc-900 border border-zinc-700 rounded px-3 py-1 w-20 text-center focus:outline-none focus:border-emerald-500">
                    <span class="text-sm text-zinc-400">seconds</span>
                </div>
                <button onclick="toggleScan()" id="toggleBtn"
                        class="px-6 py-2 bg-emerald-600 hover:bg-emerald-500 rounded-lg font-medium transition">
                    Start Scanning
                </button>
                <button onclick="stopScan()"
                        class="px-6 py-2 bg-red-600 hover:bg-red-500 rounded-lg font-medium transition">
                    Stop & Clear
                </button>
            </div>
        </div>

        <div id="status" class="mb-6 text-sm flex items-center gap-3">
            <div class="w-3 h-3 rounded-full bg-red-500" id="led"></div>
            <span id="statusText" class="font-medium">Not scanning</span>
            <span id="deviceCount" class="ml-auto text-zinc-500 text-xs"></span>
        </div>

        <div class="bg-zinc-900 rounded-2xl overflow-hidden border border-zinc-800 shadow-xl">
            <table class="w-full">
                <thead>
                    <tr class="border-b border-zinc-800 bg-zinc-950">
                        <th class="text-left p-4 font-medium">Device Name</th>
                        <th class="text-left p-4 font-medium">Address</th>
                        <th class="text-left p-4 font-medium">RSSI</th>
                        <th class="text-left p-4 font-medium">TX Power</th>
                        <th class="text-left p-4 font-medium">Manufacturer</th>
                        <th class="text-left p-4 font-medium">Last Seen</th>
                    </tr>
                </thead>
                <tbody id="deviceTable" class="divide-y divide-zinc-800"></tbody>
            </table>
        </div>

        <p class="text-center text-zinc-500 text-xs mt-8">
            Ensure Bluetooth is enabled. Run with: <code class="bg-zinc-900 px-2 py-1 rounded">uvicorn ble_live_scanner:app --reload</code><br>
            <span class="text-amber-400">Note:</span> On some platforms (esp. Windows/Linux) you may need admin privileges or Bluetooth permissions.
        </p>
    </div>

    <script>
        let ws;
        let isScanning = false;

        function connectWebSocket() {
            ws = new WebSocket(`ws://${location.host}/ws`);
            ws.onmessage = function(event) {
                const data = JSON.parse(event.data);
                if (data.type === "update") {
                    renderDevices(data.devices);
                    updateStatus(data.scanning, data.device_count || 0);
                }
            };
            ws.onclose = function() {
                setTimeout(connectWebSocket, 1500);
            };
        }

        function renderDevices(devices) {
            const tbody = document.getElementById("deviceTable");
            tbody.innerHTML = "";

            if (devices.length === 0) {
                tbody.innerHTML = `<tr><td colspan="6" class="p-12 text-center text-zinc-500">No devices detected yet...</td></tr>`;
                return;
            }

            devices.forEach(dev => {
                const row = document.createElement("tr");
                row.className = "device-row hover:bg-zinc-800/70";

                let manufHTML = dev.manufacturer.length
                    ? dev.manufacturer.map(m => `<span class="text-emerald-400">${m.company}</span>`).join(", ")
                    : '<span class="text-zinc-500">—</span>';

                row.innerHTML = `
                    <td class="p-4 font-medium">${dev.name}</td>
                    <td class="p-4 font-mono text-zinc-400">${dev.address}</td>
                    <td class="p-4">
                        <div class="flex items-center gap-3">
                            <span style="color: ${dev.rssi_color}" class="font-mono">${dev.rssi !== null ? dev.rssi + ' dBm' : '—'}</span>
                            <span class="text-xs px-2 py-0.5 rounded-full bg-zinc-800">${dev.rssi_quality}</span>
                        </div>
                    </td>
                    <td class="p-4 text-zinc-400">${dev.tx_power !== null ? dev.tx_power + ' dBm' : '—'}</td>
                    <td class="p-4">${manufHTML}</td>
                    <td class="p-4 text-zinc-500 text-sm">${dev.last_seen}</td>
                `;
                tbody.appendChild(row);
            });
        }

        function updateStatus(scanning, count) {
            isScanning = scanning;
            const led = document.getElementById("led");
            const text = document.getElementById("statusText");
            const btn = document.getElementById("toggleBtn");
            const countEl = document.getElementById("deviceCount");

            countEl.textContent = `${count} device${count === 1 ? '' : 's'}`;

            if (scanning) {
                led.className = "w-3 h-3 rounded-full bg-emerald-500 animate-pulse";
                text.textContent = "Scanning live...";
                btn.textContent = "Pause";
                btn.classList.remove("bg-emerald-600");
                btn.classList.add("bg-amber-600");
            } else {
                led.className = "w-3 h-3 rounded-full bg-amber-500";
                text.textContent = "Paused";
                btn.textContent = "Resume";
                btn.classList.remove("bg-amber-600");
                btn.classList.add("bg-emerald-600");
            }
        }

        async function toggleScan() {
            if (!isScanning) {
                await fetch("/scan/start", {method: "POST"});
            } else {
                await fetch("/scan/pause", {method: "POST"});
            }
        }

        async function stopScan() {
            await fetch("/scan/stop", {method: "POST"});
        }

        // Update interval
        document.getElementById("interval").addEventListener("change", (e) => {
            const val = parseFloat(e.target.value);
            if (ws && ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({action: "set_interval", value: val}));
            }
        });

        // Init
        connectWebSocket();
    </script>
</body>
</html>
    """
    return HTMLResponse(html)


if __name__ == "__main__":
    print("🚀 Starting Live Bluetooth Scanner")
    print("Open → http://127.0.0.1:8000")
    print("Make sure Bluetooth is enabled on your machine!")
    uvicorn.run(
        "ble_live_scanner:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )
