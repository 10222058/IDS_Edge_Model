from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from .capture import PacketRecord
from .features import build_feature_vector
from .model import IDSModel


@dataclass
class DetectionResult:
    src_ip: str
    attack_probability: float
    is_attack: bool
    features: Dict[str, float]


class Detector:
    def __init__(self, model: IDSModel, threshold: float) -> None:
        self.model = model
        self.threshold = threshold

    def evaluate_host(self, src_ip: str, records: List[PacketRecord]) -> DetectionResult:
        features = build_feature_vector(records)
        score = self.model.predict_attack_probability(features)
        return DetectionResult(
            src_ip=src_ip,
            attack_probability=score,
            is_attack=score >= self.threshold,
            features=features,
        )
