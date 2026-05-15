from __future__ import annotations

from pathlib import Path

import joblib
import pandas as pd
import yaml
from lightgbm import LGBMClassifier
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report, roc_auc_score
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler


def load_yaml(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def main(config_path: str = "configs/ids_config.yaml") -> None:
    cfg = load_yaml(config_path)
    train_cfg = cfg["training"]
    model_cfg = cfg["model"]

    dataset_path = Path(train_cfg["dataset_path"])
    df = pd.read_csv(dataset_path)

    label_col = train_cfg["label_column"]
    feature_cols = model_cfg["expected_features"]

    x = df[feature_cols]
    y = df[label_col]

    x_train, x_test, y_train, y_test = train_test_split(
        x,
        y,
        test_size=train_cfg["test_size"],
        random_state=train_cfg["random_state"],
        stratify=y,
    )

    scaler = StandardScaler()
    x_train_scaled = scaler.fit_transform(x_train)
    x_test_scaled = scaler.transform(x_test)

    if model_cfg["type"] == "lightgbm":
        model = LGBMClassifier(
            n_estimators=250,
            max_depth=10,
            learning_rate=0.05,
            random_state=train_cfg["random_state"],
        )
    else:
        model = RandomForestClassifier(
            n_estimators=300,
            max_depth=16,
            min_samples_split=8,
            class_weight="balanced",
            random_state=train_cfg["random_state"],
            n_jobs=-1,
        )

    model.fit(x_train_scaled, y_train)

    y_pred = model.predict(x_test_scaled)
    y_prob = model.predict_proba(x_test_scaled)[:, 1]

    print(classification_report(y_test, y_pred))
    print("ROC-AUC:", roc_auc_score(y_test, y_prob))

    model_path = Path(model_cfg["path"])
    scaler_path = Path(model_cfg["scaler_path"])
    model_path.parent.mkdir(parents=True, exist_ok=True)
    scaler_path.parent.mkdir(parents=True, exist_ok=True)

    joblib.dump(model, model_path)
    joblib.dump(scaler, scaler_path)

    print(f"Saved model to: {model_path}")
    print(f"Saved scaler to: {scaler_path}")


if __name__ == "__main__":
    main()
