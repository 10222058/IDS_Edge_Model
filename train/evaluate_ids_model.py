from __future__ import annotations

import argparse
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import yaml
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    classification_report,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import train_test_split


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Detailed evaluation for IDS model with threshold sweep"
    )
    parser.add_argument(
        "--config",
        default="configs/ids_config.yaml",
        help="Path to IDS config file",
    )
    parser.add_argument(
        "--dataset",
        default="",
        help="Optional dataset path (override config training.dataset_path)",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.5,
        help="Decision threshold for attack classification",
    )
    parser.add_argument(
        "--test-size",
        type=float,
        default=-1.0,
        help="Optional test split size (override config), set in (0,1)",
    )
    parser.add_argument(
        "--random-state",
        type=int,
        default=42,
        help="Random seed for split",
    )
    parser.add_argument(
        "--report-dir",
        default="reports",
        help="Directory for evaluation artifacts",
    )
    return parser.parse_args()


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def metrics_at_threshold(y_true: np.ndarray, y_prob: np.ndarray, threshold: float) -> dict:
    y_pred = (y_prob >= threshold).astype(int)
    tn, fp, fn, tp = confusion_matrix(y_true, y_pred, labels=[0, 1]).ravel()

    return {
        "threshold": float(threshold),
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "precision_attack": float(precision_score(y_true, y_pred, pos_label=1, zero_division=0)),
        "recall_attack": float(recall_score(y_true, y_pred, pos_label=1, zero_division=0)),
        "f1_attack": float(f1_score(y_true, y_pred, pos_label=1, zero_division=0)),
        "false_positive_rate": float(fp / (fp + tn) if (fp + tn) else 0.0),
        "false_negative_rate": float(fn / (fn + tp) if (fn + tp) else 0.0),
        "tp": int(tp),
        "tn": int(tn),
        "fp": int(fp),
        "fn": int(fn),
    }

 
def build_synthetic_scenarios(feature_cols: list[str]) -> pd.DataFrame:
    # Synthetic feature profiles emulate normal IoT traffic and common attacks.
    profiles = {
        "normal_mqtt_like": {
            "pkt_count": 60,
            "tcp_count": 55,
            "udp_count": 4,
            "icmp_count": 1,
            "syn_count": 3,
            "ack_count": 50,
            "rst_count": 0,
            "unique_dst_port_count": 2,
            "avg_pkt_len": 180,
            "mean_inter_arrival_ms": 120,
        },
        "portscan_fast": {
            "pkt_count": 250,
            "tcp_count": 250,
            "udp_count": 0,
            "icmp_count": 0,
            "syn_count": 240,
            "ack_count": 5,
            "rst_count": 15,
            "unique_dst_port_count": 180,
            "avg_pkt_len": 70,
            "mean_inter_arrival_ms": 4,
        },
        "ddos_syn_flood": {
            "pkt_count": 1200,
            "tcp_count": 1200,
            "udp_count": 0,
            "icmp_count": 0,
            "syn_count": 1180,
            "ack_count": 8,
            "rst_count": 2,
            "unique_dst_port_count": 6,
            "avg_pkt_len": 66,
            "mean_inter_arrival_ms": 0.6,
        },
        "ddos_udp_flood": {
            "pkt_count": 1400,
            "tcp_count": 0,
            "udp_count": 1400,
            "icmp_count": 0,
            "syn_count": 0,
            "ack_count": 0,
            "rst_count": 0,
            "unique_dst_port_count": 10,
            "avg_pkt_len": 84,
            "mean_inter_arrival_ms": 0.5,
        },
    }

    rows = []
    for name, values in profiles.items():
        row = {c: float(values.get(c, 0.0)) for c in feature_cols}
        row["scenario"] = name
        rows.append(row)
    return pd.DataFrame(rows)


def main() -> None:
    args = parse_args()

    cfg = load_config(args.config)
    train_cfg = cfg["training"]
    model_cfg = cfg["model"]

    dataset_path = Path(args.dataset) if args.dataset else Path(train_cfg["dataset_path"])
    feature_cols = model_cfg["expected_features"]
    label_col = train_cfg["label_column"]

    if not dataset_path.exists():
        raise FileNotFoundError(f"Dataset not found: {dataset_path}")

    df = pd.read_csv(dataset_path, low_memory=False)
    y = df[label_col].astype(int)

    test_size = train_cfg["test_size"] if args.test_size <= 0 else args.test_size
    if not (0 < test_size < 1):
        raise ValueError("test_size must be in (0,1)")

    train_idx, test_idx = train_test_split(
        df.index,
        test_size=test_size,
        random_state=args.random_state,
        stratify=y,
    )

    df_test = df.loc[test_idx].copy()
    x_test = df_test[feature_cols]
    y_test = df_test[label_col].astype(int)

    model = joblib.load(Path(model_cfg["path"]))
    scaler_path = Path(model_cfg["scaler_path"]) if model_cfg.get("scaler_path") else None
    scaler = joblib.load(scaler_path) if scaler_path and scaler_path.exists() else None

    x_test_arr = scaler.transform(x_test) if scaler is not None else x_test.to_numpy(dtype=float)
    y_prob = model.predict_proba(x_test_arr)[:, 1]

    # Core aggregate metrics independent from threshold.
    summary = {
        "rows_total": int(len(df)),
        "rows_test": int(len(x_test)),
        "attack_ratio_total": float(y.mean()),
        "roc_auc": float(roc_auc_score(y_test, y_prob)),
        "pr_auc": float(average_precision_score(y_test, y_prob)),
    }

    # Main threshold metrics and classification report.
    main_threshold = metrics_at_threshold(y_test.to_numpy(), y_prob, args.threshold)
    y_pred_main = (y_prob >= args.threshold).astype(int)
    class_report = classification_report(y_test, y_pred_main, digits=4)

    # Sweep threshold for operating point selection.
    sweep = []
    for t in np.round(np.arange(0.05, 1.0, 0.05), 2):
        sweep.append(metrics_at_threshold(y_test.to_numpy(), y_prob, float(t)))
    sweep_df = pd.DataFrame(sweep)

    scenario_df = pd.DataFrame()
    if "attack_family" in df_test.columns:
        y_pred_main_series = pd.Series(y_pred_main, index=df_test.index)
        y_prob_series = pd.Series(y_prob, index=df_test.index)

        rows = []
        for family in sorted(df_test["attack_family"].astype(str).unique()):
            mask = df_test["attack_family"].astype(str) == family
            if int(mask.sum()) == 0:
                continue

            fam_true = y_test[mask]
            fam_pred = y_pred_main_series[mask]
            fam_prob = y_prob_series[mask]

            rows.append(
                {
                    "attack_family": family,
                    "rows": int(mask.sum()),
                    "true_attack_ratio": float(fam_true.mean()),
                    "predicted_attack_rate": float(fam_pred.mean()),
                    "mean_score": float(fam_prob.mean()),
                    "detect_rate_as_attack": float(fam_pred.mean()) if family != "normal" else float(1.0 - fam_pred.mean()),
                }
            )

        scenario_df = pd.DataFrame(rows).sort_values(by="rows", ascending=False)

    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)

    summary_path = report_dir / "evaluation_summary.json"
    sweep_path = report_dir / "threshold_sweep.csv"
    scenario_path = report_dir / "scenario_breakdown.csv"
    synthetic_path = report_dir / "synthetic_scenarios.csv"

    with summary_path.open("w", encoding="utf-8") as fh:
        json.dump({"summary": summary, "threshold_metrics": main_threshold}, fh, indent=2)

    sweep_df.to_csv(sweep_path, index=False)
    if not scenario_df.empty:
        scenario_df.to_csv(scenario_path, index=False)

    synthetic_df = build_synthetic_scenarios(feature_cols)
    synthetic_x = synthetic_df[feature_cols]
    synthetic_arr = scaler.transform(synthetic_x) if scaler is not None else synthetic_x.to_numpy(dtype=float)
    synthetic_prob = model.predict_proba(synthetic_arr)[:, 1]
    synthetic_df["attack_score"] = synthetic_prob
    synthetic_df["predicted_attack"] = (synthetic_prob >= args.threshold).astype(int)
    synthetic_df.to_csv(synthetic_path, index=False)

    print("=== IDS Evaluation Summary ===")
    print(json.dumps(summary, indent=2))
    print("\n=== Metrics at Threshold ===")
    print(json.dumps(main_threshold, indent=2))
    print("\n=== Classification Report ===")
    print(class_report)

    print(f"Saved summary JSON: {summary_path}")
    print(f"Saved threshold sweep CSV: {sweep_path}")
    print(f"Saved synthetic scenario CSV: {synthetic_path}")
    if not scenario_df.empty:
        print(f"Saved scenario breakdown CSV: {scenario_path}")

        ddos_rows = scenario_df[scenario_df["attack_family"] == "ddos"]
        if not ddos_rows.empty:
            print("\n=== DDoS Scenario Check ===")
            print(ddos_rows.to_string(index=False))

    print("\n=== Synthetic Attack Scenarios ===")
    print(synthetic_df[["scenario", "attack_score", "predicted_attack"]].to_string(index=False))


if __name__ == "__main__":
    main()
