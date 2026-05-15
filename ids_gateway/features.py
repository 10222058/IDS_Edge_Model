from __future__ import annotations

from collections import Counter
from typing import Dict, List

import numpy as np

from .capture import PacketRecord


def build_feature_vector(records: List[PacketRecord]) -> Dict[str, float]:
    if not records:
        return {
            "pkt_count": 0.0,
            "tcp_count": 0.0,
            "udp_count": 0.0,
            "icmp_count": 0.0,
            "syn_count": 0.0,
            "ack_count": 0.0,
            "rst_count": 0.0,
            "unique_dst_port_count": 0.0,
            "avg_pkt_len": 0.0,
            "mean_inter_arrival_ms": 0.0,
        }

    protos = Counter(r.proto for r in records)
    flags = "".join(r.tcp_flags for r in records)
    pkt_lens = [r.pkt_len for r in records]
    dst_ports = {r.dst_port for r in records if r.dst_port > 0}

    sorted_ts = sorted(r.ts for r in records)
    iat_ms = []
    if len(sorted_ts) > 1:
        iat_ms = [(t2 - t1) * 1000.0 for t1, t2 in zip(sorted_ts[:-1], sorted_ts[1:])]

    return {
        "pkt_count": float(len(records)),
        "tcp_count": float(protos.get("TCP", 0)),
        "udp_count": float(protos.get("UDP", 0)),
        "icmp_count": float(protos.get("ICMP", 0)),
        "syn_count": float(flags.count("S")),
        "ack_count": float(flags.count("A")),
        "rst_count": float(flags.count("R")),
        "unique_dst_port_count": float(len(dst_ports)),
        "avg_pkt_len": float(np.mean(pkt_lens)),
        "mean_inter_arrival_ms": float(np.mean(iat_ms) if iat_ms else 0.0),
    }
