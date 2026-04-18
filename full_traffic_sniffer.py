#!/usr/bin/env python3

"""
  Written by: Will Armijo <will.armijo@gmail.com>
  Script Name: full_traffic_sniffer.py 
  Purpose: displays all traffic between client and server services.
  Usage: python3 full_traffic_sniffer.py
  Prerequires: pip install psutil
"""

from scapy.all import sniff, IP, TCP, UDP, ICMP

def process_packet(packet):
    if IP in packet:
        ip_layer = packet[IP]
        src = ip_layer.src
        dst = ip_layer.dst
        proto = ip_layer.proto
        print(f"[IP] {src} → {dst} | Protocol: {proto}")
        if TCP in packet:
            tcp = packet[TCP]
            print(f" └── [TCP] Port: {tcp.sport} → {tcp.dport}")
        elif UDP in packet:
            udp = packet[UDP]
            print(f" └── [UDP] Port: {udp.sport} → {udp.dport}")
        elif ICMP in packet:
            print(f" └── [ICMP] Type: {packet[ICMP].type}")

# Start sniffing on default interface
sniff(prn=process_packet, count=100, store=False)
