#!/usr/bin/env python3

"""
  Written by: Will Armijo <will.armijo@gmail.com>
  Script Name: Local_dns_sniffer.py
  Purpose: A live self-refreshing HTML page system resource dashboard
  Usage: Open web browser and nvigate to, http://localhost:8080
  Prerequires: pip install psutil
"""

import argparse
import logging
from datetime import datetime

from scapy.all import DNSQR, DNSRR, sniff

logging.basicConfig(
    format="%(asctime)s  %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

DEFAULT_INTERFACE = "en0"
DEFAULT_FILTER = "udp and port 53"


def decode_name(raw) -> str:
    """Safely decode a DNS wire-format name to a string."""
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace").rstrip(".")
    return str(raw).rstrip(".")


def process_dns(pkt) -> None:
    """Callback invoked by scapy for each captured DNS packet."""
    try:
        if DNSQR in pkt and pkt.dport == 53:
            name = decode_name(pkt[DNSQR].qname)
            qtype = pkt[DNSQR].qtype
            logger.info("[Query]    %-40s  type=%s", name, qtype)

        elif DNSRR in pkt and pkt.sport == 53:
            rr = pkt[DNSRR]
            name = decode_name(rr.rrname)
            try:
                rdata = rr.rdata
                if isinstance(rdata, bytes):
                    rdata = rdata.decode("utf-8", errors="replace")
            except Exception:
                rdata = "<unparseable rdata>"
            logger.info("[Response] %-40s  -> %s", name, rdata)

    except Exception as exc:
        logger.warning("Failed to parse packet: %s", exc)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Capture and log DNS queries and responses."
    )
    parser.add_argument(
        "-i", "--interface",
        default=DEFAULT_INTERFACE,
        help=f"Network interface to sniff on (default: {DEFAULT_INTERFACE})",
    )
    parser.add_argument(
        "-f", "--filter",
        default=DEFAULT_FILTER,
        help=f"BPF filter string (default: '{DEFAULT_FILTER}')",
    )
    parser.add_argument(
        "-c", "--count",
        type=int,
        default=0,
        help="Stop after N packets (default: 0 = unlimited)",
    )
    parser.add_argument(
        "-t", "--timeout",
        type=float,
        default=None,
        help="Stop after N seconds (default: run until interrupted)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    logger.info(
        "Starting DNS sniffer  interface=%s  filter='%s'  count=%s  timeout=%s",
        args.interface,
        args.filter,
        args.count or "unlimited",
        f"{args.timeout}s" if args.timeout else "none",
    )

    try:
        sniff(
            iface=args.interface,
            filter=args.filter,
            store=False,
            prn=process_dns,
            count=args.count,
            timeout=args.timeout,
        )
    except KeyboardInterrupt:
        logger.info("Stopped by user.")
    except PermissionError:
        logger.error("Permission denied — try running with sudo.")
    except Exception as exc:
        logger.error("Unexpected error: %s", exc)


if __name__ == "__main__":
    main()
