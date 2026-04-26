from scapy.all import sniff, Dot11Beacon, Dot11, Dot11Elt, RadioTap, conf

conf.use_pcap = True

found_bssids = set()

CHANNEL_TO_FREQ = {
    # 2.4 GHz (channels 1–14)
    **{ch: round(2.407 + 0.005 * ch, 3) for ch in range(1, 14)},
    14: 2.484,
    # 5 GHz (channels 36–177)
    **{ch: round(5.000 + 0.005 * ch, 3) for ch in range(36, 178, 4)},
    # 6 GHz (channels 1–233, Wi-Fi 6E)
    **{ch: round(5.950 + 0.005 * ch, 3) for ch in range(1, 234, 4)},
}

def get_channel(pkt):
    """Extract channel from DS Parameter Set element (ID 3)."""
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
    """Map channel number to GHz frequency string."""
    if channel is None:
        return "?"
    freq = CHANNEL_TO_FREQ.get(channel)
    return f"{freq:.3f}" if freq else "?"

def get_encryption(pkt):
    """Detect encryption: WPA2 (RSN IE), WPA (vendor IE), WEP, or Open."""
    cap = pkt[Dot11Beacon].cap
    has_rsn = False
    has_wpa = False
    elt = pkt.getlayer(Dot11Elt)
    while elt:
        if elt.ID == 48:
            has_rsn = True
            break
        if elt.ID == 221 and elt.info[:4] == b'\x00\x50\xf2\x01':
            has_wpa = True
        elt = elt.payload.getlayer(Dot11Elt)
    if has_rsn:
        return "WPA2"
    elif has_wpa:
        return "WPA"
    elif cap.privacy:
        return "WEP"
    return "Open"

def get_signal(pkt):
    """Extract RSSI (dBm) from RadioTap header if present."""
    try:
        if pkt.haslayer(RadioTap):
            return f"{pkt[RadioTap].dBm_AntSignal} dBm"
    except Exception:
        pass
    return "?"

def packet_handler(pkt):
    if not pkt.haslayer(Dot11Beacon):
        return

    bssid = pkt[Dot11].addr2
    if bssid in found_bssids:
        return
    found_bssids.add(bssid)

    # Parse SSID from element ID 0
    ssid = None
    elt = pkt.getlayer(Dot11Elt)
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
        return  # Skip hidden networks

    channel = get_channel(pkt)
    freq    = get_frequency(channel)
    enc     = get_encryption(pkt)
    signal  = get_signal(pkt)
    ch_str  = str(channel) if channel is not None else "?"

    print(f"  {ssid!r:<32}  {bssid}  {ch_str:>3}  {freq:>9}  {enc:<8}  {signal}")

COL_WIDTHS = f"  {'SSID':<32}  {'BSSID':<17}  {'CH':>3}  {'FREQ(GHz)':>9}  {'ENC':<8}  SIGNAL"
DIVIDER    = "  " + "-" * (len(COL_WIDTHS) - 2)

print("Starting scan on en0 (requires sudo)...\n")
print(DIVIDER)
print(COL_WIDTHS)
print(DIVIDER)

try:
    sniff(iface="en0", prn=packet_handler, monitor=True, timeout=30)
except PermissionError:
    print("Error: Run with sudo.")
except Exception as e:
    print(f"Error: {e}")

print(DIVIDER)
print(f"\nScan complete. Found {len(found_bssids)} unique network(s).")
