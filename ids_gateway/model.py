from __future__ import annotations

from pathlib import Path
from typing import Dict, List

import joblib
import numpy as np


class IDSModel:
    def __init__(self, model_path: str, scaler_path: str | None, features: List[str]) -> None:
        self.model = joblib.load(Path(model_path))
        self.scaler = joblib.load(Path(scaler_path)) if scaler_path else None
        self.features = features

    def _to_array(self, feature_dict: Dict[str, float]) -> np.ndarray:
        row = [feature_dict.get(name, 0.0) for name in self.features]
        arr = np.array([row], dtype=float)
        if self.scaler is not None:
            arr = self.scaler.transform(arr)
        return arr

    def predict_attack_probability(self, feature_dict: Dict[str, float]) -> float:
        arr = self._to_array(feature_dict)
        proba = self.model.predict_proba(arr)
        return float(proba[0][1])
