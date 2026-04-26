import asyncio
import json
import threading
import websockets
from scapy.all import sniff, Dot11Beacon, Dot11, Dot11Elt, RadioTap, conf
from http.server import HTTPServer, SimpleHTTPRequestHandler
import os, pathlib

conf.use_pcap = True

CHANNEL_TO_FREQ = {
    **{ch: round(2.407 + 0.005 * ch, 3) for ch in range(1, 14)},
    14: 2.484,
    **{ch: round(5.000 + 0.005 * ch, 3) for ch in range(36, 178, 4)},
    **{ch: round(5.950 + 0.005 * ch, 3) for ch in range(1, 234, 4)},
}

found_bssids = {}
clients      = set()
loop         = None
paused       = False

# ── HTTP server ───────────────────────────────────────────────
class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args): pass

def run_http_server():
    os.chdir(pathlib.Path(__file__).parent)
    server = HTTPServer(('localhost', 9000), QuietHandler)
    print("[http] Dashboard at http://localhost:9000/dashboard.html")
    server.serve_forever()

# ── Packet parsing helpers ────────────────────────────────────
def get_channel(pkt):
    try:
        elt = pkt.getlayer(Dot11Elt)
        while elt:
            if elt.ID == 3:
                return int.from_bytes(elt.info, 'little')
            elt = elt.payload.getlayer(Dot11Elt)
    except Exception:
        pass
    return None

def get_frequency(channel):
    if channel is None:
        return None
    return CHANNEL_TO_FREQ.get(channel)

def get_encryption(pkt):
    cap     = pkt[Dot11Beacon].cap
    has_rsn = False
    has_wpa = False
    elt     = pkt.getlayer(Dot11Elt)
    while elt:
        if elt.ID == 48:
            has_rsn = True
            break
        if elt.ID == 221 and elt.info[:4] == b'\x00\x50\xf2\x01':
            has_wpa = True
        elt = elt.payload.getlayer(Dot11Elt)
    if has_rsn:       return "WPA2"
    elif has_wpa:     return "WPA"
    elif cap.privacy: return "WEP"
    return "Open"

def get_signal(pkt):
    try:
        if pkt.haslayer(RadioTap):
            return int(pkt[RadioTap].dBm_AntSignal)
    except Exception:
        pass
    return None

# ── Broadcast helpers ─────────────────────────────────────────
def broadcast(data):
    if not clients or loop is None:
        return
    asyncio.run_coroutine_threadsafe(_broadcast(json.dumps(data)), loop)

async def _broadcast(msg):
    dead = set()
    for ws in clients:
        try:
            await ws.send(msg)
        except Exception:
            dead.add(ws)
    clients.difference_update(dead)

# ── Packet handler ────────────────────────────────────────────
def packet_handler(pkt):
    if paused:
        return

    if not pkt.haslayer(Dot11Beacon):
        return

    bssid = pkt[Dot11].addr2

    ssid = None
    elt  = pkt.getlayer(Dot11Elt)
    while elt:
        if elt.ID == 0:
            try:
                decoded = elt.info.decode('utf-8', errors='ignore').strip('\x00').strip()
                if decoded:
                    ssid = decoded
            except Exception:
                pass
            break
        elt = elt.payload.getlayer(Dot11Elt)

    if not ssid:
        return

    channel = get_channel(pkt)
    is_new  = bssid not in found_bssids

    found_bssids[bssid] = {
        "ssid":    ssid,
        "bssid":   bssid,
        "channel": channel,
        "freq":    get_frequency(channel),
        "enc":     get_encryption(pkt),
        "signal":  get_signal(pkt),
    }

    broadcast({
        "type":    "new" if is_new else "update",
        "network": found_bssids[bssid],
        "total":   len(found_bssids),
    })

# ── WebSocket handler ─────────────────────────────────────────
async def ws_handler(websocket):
    global paused
    clients.add(websocket)
    print(f"[ws] Client connected ({len(clients)} total)")

    # Send all known networks on connect
    for net in found_bssids.values():
        await websocket.send(json.dumps({
            "type": "new", "network": net, "total": len(found_bssids)
        }))

    # Sync current pause state to newly connected client
    await websocket.send(json.dumps({"type": "state", "paused": paused}))

    try:
        async for raw in websocket:
            try:
                msg = json.loads(raw)
                cmd = msg.get("cmd")
                if cmd == "pause":
                    paused = True
                    print("[ws] Scan paused by client")
                    await _broadcast(json.dumps({"type": "state", "paused": True}))
                elif cmd == "resume":
                    paused = False
                    print("[ws] Scan resumed by client")
                    await _broadcast(json.dumps({"type": "state", "paused": False}))
            except Exception:
                pass
    finally:
        clients.discard(websocket)
        print(f"[ws] Client disconnected ({len(clients)} total)")

# ── WebSocket server ──────────────────────────────────────────
async def run_ws_server():
    global loop
    loop = asyncio.get_running_loop()
    print("[ws]   WebSocket on ws://localhost:8765")
    async with websockets.serve(ws_handler, "localhost", 8765):
        await asyncio.Future()

# ── Scanner thread ────────────────────────────────────────────
def run_scanner():
    print("[scan] Scanning on en0 (requires sudo)...")
    try:
        sniff(iface="en0", prn=packet_handler, monitor=True, store=False)
    except PermissionError:
        print("[scan] Error: run with sudo.")
    except Exception as e:
        print(f"[scan] Error: {e}")

# ── Entry point ───────────────────────────────────────────────
if __name__ == "__main__":
    threading.Thread(target=run_http_server, daemon=True).start()
    threading.Thread(target=run_scanner,     daemon=True).start()
    try:
        asyncio.run(run_ws_server())
    except KeyboardInterrupt:
        print("\n[*] Shutting down.")
