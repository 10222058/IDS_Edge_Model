from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .features import build_feature_vector, empty_feature_vector
from .model import IDSModel
from .records import PacketRecord
from .rules import DeviceLogContext, RuleEngine, RuleViolation


@dataclass
class DetectionResult:
    device_id: str
    src_ip: str
    attack_probability: float
    is_attack: bool
    features: Dict[str, float]
    predicted_class: str = ""
    family_probability: float = 0.0
    raw_probabilities: list[float] | None = None
    rule_passed: bool = True
    rule_violation: Optional[RuleViolation] = None
    skipped_reason: str = ""
    semantic_risk: str = "LOW"
    risk_flags: list[str] = field(default_factory=list)
    ml_trustworthy: bool = True


class Detector:
    def __init__(
        self,
        model: IDSModel,
        threshold: float,
        rule_engine: Optional[RuleEngine] = None,
        min_packets_per_window: int = 3,
        ml_enabled: bool = True,
    ) -> None:
        self.model = model
        self.threshold = threshold
        self.rule_engine = rule_engine or RuleEngine()
        self.min_packets_per_window = max(int(min_packets_per_window), 1)
        self.ml_enabled = ml_enabled

    def evaluate_device(
        self,
        device_id: str,
        src_ip: str,
        records: List[PacketRecord],
        context: Optional[DeviceLogContext] = None,
    ) -> DetectionResult:
        pkt_count = len(records)
        rule_passed, violation = self.rule_engine.evaluate(
            device_id=device_id,
            src_ip=src_ip,
            records=records,
            context=context,
        )
        if not rule_passed:
            return DetectionResult(
                device_id=device_id,
                src_ip=src_ip,
                attack_probability=0.0,
                is_attack=True,
                features=empty_feature_vector(),
                rule_passed=False,
                rule_violation=violation,
            )

        features = build_feature_vector(records)

        if not self.ml_enabled:
            return DetectionResult(
                device_id=device_id,
                src_ip=src_ip,
                attack_probability=0.0,
                is_attack=False,
                features=features,
                rule_passed=True,
                skipped_reason="machine_learning_disabled",
            )

        if pkt_count < self.min_packets_per_window:
            return DetectionResult(
                device_id=device_id,
                src_ip=src_ip,
                attack_probability=0.0,
                is_attack=False,
                features=features,
                rule_passed=True,
                skipped_reason=f"insufficient_packets<{self.min_packets_per_window}",
            )

        score = self.model.predict_attack_probability(features)

        return DetectionResult(
            device_id=device_id,
            src_ip=src_ip,
            attack_probability=score,
            is_attack=(score >= self.threshold),
            features=features,
            rule_passed=True,
        )

    def evaluate_batch(
        self,
        batches: Dict[str, List[PacketRecord]],
        source_ips: Optional[Dict[str, str]] = None,
        contexts: Optional[Dict[str, DeviceLogContext]] = None,
    ) -> List[DetectionResult]:
        results: List[DetectionResult] = []
        source_ips = source_ips or {}
        contexts = contexts or {}
        for device_id, records in batches.items():
            src_ip = source_ips.get(device_id, "")
            if not src_ip and records:
                src_ip = str(getattr(records[0], "observed_src_ip", "") or records[0].src_ip)
            results.append(
                self.evaluate_device(
                    device_id,
                    src_ip,
                    records,
                    context=contexts.get(device_id),
                )
            )
        return results