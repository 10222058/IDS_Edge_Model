from __future__ import annotations

import time

from .blocker import IptablesBlocker
from .capture import PacketCapture
from .config import load_config
from .detector import Detector
from .model import IDSModel


def run(config_path: str = "configs/ids_config.yaml") -> None:
    cfg = load_config(config_path)

    gateway_cfg = cfg["gateway"]
    model_cfg = cfg["model"]

    capture = PacketCapture(interface=gateway_cfg["capture_interface"])
    model = IDSModel(
        model_path=model_cfg["path"],
        scaler_path=model_cfg.get("scaler_path"),
        features=model_cfg["expected_features"],
    )
    detector = Detector(model=model, threshold=gateway_cfg["suspicious_threshold"])
    blocker = IptablesBlocker(block_seconds=gateway_cfg["default_block_seconds"])

    print("[IDS] Gateway IDS loop started")
    while True:
        packets_by_source = capture.sniff_once(timeout=gateway_cfg["inference_interval_seconds"])
        blocker.unblock_expired()

        for src_ip, records in packets_by_source.items():
            result = detector.evaluate_host(src_ip=src_ip, records=records)
            print(
                f"[IDS] src={result.src_ip} score={result.attack_probability:.3f} "
                f"attack={result.is_attack}"
            )
            if result.is_attack:
                blocker.block_ip(result.src_ip)
                print(f"[IDS] blocked {result.src_ip}")

        time.sleep(0.1)


if __name__ == "__main__":
    run()
