from __future__ import annotations

import argparse
import importlib
import shutil
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np
import pandas as pd

TARGET_FEATURES = [
    "pkt_count",
    "tcp_count",
    "udp_count",
    "icmp_count",
    "syn_count",
    "ack_count",
    "rst_count",
    "unique_dst_port_count",
    "avg_pkt_len",
    "mean_inter_arrival_ms",
]


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]
    return df


def _pick_series(df: pd.DataFrame, candidates: Sequence[str], default: float = 0.0) -> pd.Series:
    for c in candidates:
        if c in df.columns:
            return pd.to_numeric(df[c], errors="coerce").fillna(default)
    return pd.Series(np.full(len(df), default), index=df.index, dtype=float)


def _pick_text(df: pd.DataFrame, candidates: Sequence[str], default: str = "") -> pd.Series:
    for c in candidates:
        if c in df.columns:
            return df[c].astype(str).fillna(default).str.lower()
    return pd.Series(np.full(len(df), default), index=df.index, dtype=object)


def _protocol_bucket(proto_raw: pd.Series) -> pd.Series:
    proto = proto_raw.astype(str).str.lower().str.strip()

    numeric_map = {
        "6": "tcp",
        "17": "udp",
        "1": "icmp",
    }
    return proto.replace(numeric_map)


def _build_label(df: pd.DataFrame) -> pd.Series:
    if "label" in df.columns:
        label_raw = df["label"]
    elif "attack" in df.columns:
        label_raw = df["attack"]
    elif "category" in df.columns:
        label_raw = df["category"]
    elif "type" in df.columns:
        label_raw = df["type"]
    else:
        raise ValueError(
            "Cannot infer label. Provide one of: label, attack, category, type"
        )

    if pd.api.types.is_numeric_dtype(label_raw):
        return (pd.to_numeric(label_raw, errors="coerce").fillna(0) > 0).astype(int)

    text = label_raw.astype(str).str.lower().str.strip()
    benign_tokens = {"0", "normal", "benign", "false", "non-attack", "nonattack"}
    return (~text.isin(benign_tokens)).astype(int)


def _build_attack_family(df: pd.DataFrame, label: pd.Series) -> pd.Series:
    candidates = ["subcategory", "attack_cat", "attack_type", "category", "type", "label", "attack"]
    text_col = _pick_text(df, candidates, default="")

    families = pd.Series(np.full(len(df), "attack_other", dtype=object), index=df.index)

    benign_tokens = {"0", "normal", "benign", "false", "non-attack", "nonattack"}
    is_benign_text = text_col.isin(benign_tokens)
    families = np.where((label == 0) | is_benign_text, "normal", families)

    ddos_mask = text_col.str.contains("ddos|dos|flood", regex=True, na=False)
    portscan_mask = text_col.str.contains("portscan|port scan|scan", regex=True, na=False)
    recon_mask = text_col.str.contains("recon|probe", regex=True, na=False)

    families = np.where((label == 1) & ddos_mask, "ddos", families)
    families = np.where((label == 1) & (~ddos_mask) & portscan_mask, "portscan", families)
    families = np.where((label == 1) & (~ddos_mask) & (~portscan_mask) & recon_mask, "recon", families)

    return pd.Series(families, index=df.index)


def _build_features(df: pd.DataFrame) -> pd.DataFrame:
    label = _build_label(df)
    attack_family = _build_attack_family(df, label)

    proto_raw = _pick_text(df, ["proto", "protocol", "l4_proto"])
    proto = _protocol_bucket(proto_raw)

    pkt_count = _pick_series(
        df,
        [
            "pkts",
            "packets",
            "tot_pkts",
            "total_pkts",
            "tot fwd pkts",
            "tot bwd pkts",
            "total fwd packets",
            "total backward packets",
        ],
        default=np.nan,
    )
    if pkt_count.isna().all():
        spkts = _pick_series(df, ["spkts", "src_pkts", "in_pkts"], default=0.0)
        dpkts = _pick_series(df, ["dpkts", "dst_pkts", "out_pkts"], default=0.0)
        pkt_count = spkts + dpkts
    pkt_count = pkt_count.fillna(1.0).clip(lower=1.0)

    bytes_total = _pick_series(
        df,
        [
            "bytes",
            "tot_bytes",
            "total_bytes",
            "totlen fwd pkts",
            "totlen bwd pkts",
            "total length of fwd packets",
            "total length of bwd packets",
        ],
        default=np.nan,
    )
    if bytes_total.isna().all():
        sbytes = _pick_series(df, ["sbytes", "src_bytes", "in_bytes"], default=0.0)
        dbytes = _pick_series(df, ["dbytes", "dst_bytes", "out_bytes"], default=0.0)
        bytes_total = sbytes + dbytes

    avg_pkt_len = _pick_series(
        df,
        ["mean", "pkt_len_mean", "mean_pkt_len", "pkt size avg", "average packet size"],
        default=np.nan,
    )
    avg_pkt_len = avg_pkt_len.fillna(bytes_total / pkt_count).replace([np.inf, -np.inf], np.nan).fillna(0.0)

    duration = _pick_series(df, ["dur", "duration", "flow_duration", "flow duration"], default=np.nan)
    flow_iat_ms = _pick_series(df, ["flow iat mean", "flow_iat_mean", "iat"], default=np.nan)
    mean_iat_ms = (duration / pkt_count) * 1000.0
    mean_iat_ms = mean_iat_ms.fillna(flow_iat_ms)
    mean_iat_ms = mean_iat_ms.replace([np.inf, -np.inf], np.nan).fillna(0.0)

    state_text = _pick_text(df, ["state", "flags", "tcp_flags"])

    dport = _pick_series(df, ["dport", "dst_port", "destination_port"], default=0.0)
    syn_count_raw = _pick_series(df, ["syn flag cnt", "syn_count", "syn flag number"], default=np.nan)
    ack_count_raw = _pick_series(df, ["ack flag cnt", "ack_count", "ack flag number"], default=np.nan)
    rst_count_raw = _pick_series(df, ["rst flag cnt", "rst_count", "rst flag number"], default=np.nan)

    if syn_count_raw.isna().all():
        syn_count = np.where(state_text.str.contains("s", regex=False), pkt_count, 0.0)
    else:
        syn_count = syn_count_raw.fillna(0.0)

    if ack_count_raw.isna().all():
        ack_count = np.where(state_text.str.contains("a", regex=False), pkt_count, 0.0)
    else:
        ack_count = ack_count_raw.fillna(0.0)

    if rst_count_raw.isna().all():
        rst_count = np.where(state_text.str.contains("r", regex=False), pkt_count, 0.0)
    else:
        rst_count = rst_count_raw.fillna(0.0)

    out = pd.DataFrame(index=df.index)
    out["pkt_count"] = pkt_count
    out["tcp_count"] = np.where(proto.eq("tcp"), pkt_count, 0.0)
    out["udp_count"] = np.where(proto.eq("udp"), pkt_count, 0.0)
    out["icmp_count"] = np.where(proto.eq("icmp"), pkt_count, 0.0)
    out["syn_count"] = syn_count
    out["ack_count"] = ack_count
    out["rst_count"] = rst_count
    out["unique_dst_port_count"] = np.where(dport > 0, 1.0, 0.0)
    out["avg_pkt_len"] = avg_pkt_len.clip(lower=0.0)
    out["mean_inter_arrival_ms"] = mean_iat_ms.clip(lower=0.0)

    out["label"] = label
    out["attack_family"] = attack_family
    return out


def _read_one_tabular(path: Path, max_rows_per_file: int) -> pd.DataFrame:
    nrows = max_rows_per_file if max_rows_per_file > 0 else None
    suffix = path.suffix.lower()

    if suffix == ".csv":
        return pd.read_csv(path, nrows=nrows, low_memory=False)

    if suffix == ".parquet":
        try:
            df = pd.read_parquet(path)
        except Exception as exc:
            raise RuntimeError(
                "Failed to read parquet file. Install pyarrow with: pip install pyarrow"
            ) from exc
        if nrows is not None:
            return df.head(nrows)
        return df

    raise ValueError(f"Unsupported file format: {path}")


def _load_input(input_path: Path, max_files: int, max_rows_per_file: int) -> pd.DataFrame:
    if input_path.is_file():
        return _read_one_tabular(input_path, max_rows_per_file=max_rows_per_file)

    if input_path.is_dir():
        files = sorted([p for p in input_path.rglob("*") if p.is_file() and p.suffix.lower() in {".csv", ".parquet"}])
        if not files:
            raise FileNotFoundError(f"No CSV/Parquet files found in: {input_path}")

        if max_files > 0:
            files = files[:max_files]

        frames = [_read_one_tabular(p, max_rows_per_file=max_rows_per_file) for p in files]
        return pd.concat(frames, ignore_index=True)

    raise FileNotFoundError(f"Input path not found: {input_path}")


def _download_kaggle_tabular_files(dataset: str, target_dir: Path, max_files: int) -> Path:
    kagglehub = importlib.import_module("kagglehub")
    downloaded_path = Path(kagglehub.dataset_download(dataset))

    tabular_files = sorted(
        [p for p in downloaded_path.rglob("*") if p.is_file() and p.suffix.lower() in {".csv", ".parquet"}]
    )
    if not tabular_files:
        raise FileNotFoundError(f"No CSV/Parquet files found in downloaded dataset: {downloaded_path}")

    if max_files > 0:
        tabular_files = tabular_files[:max_files]

    target_dir.mkdir(parents=True, exist_ok=True)

    for src in tabular_files:
        dst = target_dir / src.name
        if dst.exists():
            stem = src.stem
            suffix = src.suffix
            i = 1
            while True:
                candidate = target_dir / f"{stem}_{i}{suffix}"
                if not candidate.exists():
                    dst = candidate
                    break
                i += 1
        shutil.copy2(src, dst)

    print(f"Downloaded dataset cache: {downloaded_path}")
    print(f"Copied {len(tabular_files)} tabular file(s) to: {target_dir}")
    return target_dir


def _print_summary(df: pd.DataFrame) -> None:
    total = len(df)
    attack = int(df["label"].sum())
    normal = total - attack
    ratio = (attack / total) if total else 0.0

    print(f"Rows: {total}")
    print(f"Normal: {normal}")
    print(f"Attack: {attack}")
    print(f"Attack ratio: {ratio:.3f}")
    print("Features:", ", ".join(TARGET_FEATURES))


def _rebalance_binary_labels(
    df: pd.DataFrame,
    target_attack_ratio: float,
    random_state: int,
) -> pd.DataFrame:
    if not (0.0 < target_attack_ratio < 1.0):
        raise ValueError("--target-attack-ratio must be in (0, 1)")

    attack = df[df["label"] == 1]
    normal = df[df["label"] == 0]

    n_attack = len(attack)
    n_normal = len(normal)
    if n_attack == 0 or n_normal == 0:
        raise ValueError(
            "Cannot rebalance because one class is empty "
            f"(normal={n_normal}, attack={n_attack}). "
            "Increase --max-files/--max-rows-per-file, reduce --sample-frac, "
            "or disable --ddos-only for this run."
        )

    current_ratio = n_attack / (n_attack + n_normal)
    if abs(current_ratio - target_attack_ratio) < 1e-6:
        return df

    if current_ratio > target_attack_ratio:
        # Downsample attack class to hit target ratio.
        new_attack = int((target_attack_ratio / (1.0 - target_attack_ratio)) * n_normal)
        new_attack = max(1, min(new_attack, n_attack))
        attack = attack.sample(n=new_attack, random_state=random_state)
    else:
        # Downsample normal class to hit target ratio.
        new_normal = int(((1.0 - target_attack_ratio) / target_attack_ratio) * n_attack)
        new_normal = max(1, min(new_normal, n_normal))
        normal = normal.sample(n=new_normal, random_state=random_state)

    out = pd.concat([attack, normal], ignore_index=True)
    return out.sample(frac=1.0, random_state=random_state).reset_index(drop=True)


def parse_args(args: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert BoT-IoT/TON_IoT CSV data to IDS training feature schema"
    )
    parser.add_argument(
        "--input",
        default="data/raw",
        help="Path to raw CSV/Parquet file or folder containing tabular files",
    )
    parser.add_argument(
        "--output",
        default="data/processed/ids_features.csv",
        help="Path to output processed CSV",
    )
    parser.add_argument(
        "--sample-frac",
        type=float,
        default=1.0,
        help="Optional random sampling fraction in (0,1], useful for very large datasets",
    )
    parser.add_argument(
        "--random-state",
        type=int,
        default=42,
        help="Random seed for sampling",
    )
    parser.add_argument(
        "--max-files",
        type=int,
        default=0,
        help="Optional max number of CSV files to read from input folder, 0 means all",
    )
    parser.add_argument(
        "--max-rows-per-file",
        type=int,
        default=0,
        help="Optional max rows to read per CSV file, 0 means all rows",
    )
    parser.add_argument(
        "--target-attack-ratio",
        type=float,
        default=-1.0,
        help="Optional downsampling target for attack ratio in output, set value in (0,1) to enable",
    )
    parser.add_argument(
        "--ddos-only",
        action="store_true",
        help="Keep only normal and ddos-like attack families for core DDoS detector training",
    )
    parser.add_argument(
        "--download-cicddos2019",
        action="store_true",
        help="Download CIC-DDoS2019 from Kaggle via kagglehub before preprocessing",
    )
    parser.add_argument(
        "--kaggle-dataset",
        default="dhoogla/cicddos2019",
        help="Kaggle dataset handle used when --download-cicddos2019 is enabled",
    )
    parser.add_argument(
        "--download-to",
        default="data/raw/cicddos2019",
        help="Directory where downloaded CSV/Parquet files are copied",
    )
    return parser.parse_args(args)


def _is_ddos_family(value: str) -> bool:
    v = str(value).lower().strip()
    keywords = [
        "ddos",
        "drdos",
        "dos",
        "flood",
        "udp-lag",
        "syn",
        "ntp",
        "dns",
        "ldap",
        "mssql",
        "netbios",
        "snmp",
        "ssdp",
        "tftp",
        "webddos",
    ]
    return any(k in v for k in keywords)


def main() -> None:
    args = parse_args()
    input_path = Path(args.input)
    output_path = Path(args.output)

    if args.max_files < 0:
        raise ValueError("--max-files must be >= 0")
    if args.max_rows_per_file < 0:
        raise ValueError("--max-rows-per-file must be >= 0")

    if args.download_cicddos2019:
        input_path = _download_kaggle_tabular_files(
            dataset=args.kaggle_dataset,
            target_dir=Path(args.download_to),
            max_files=args.max_files,
        )

    raw = _normalize_columns(
        _load_input(
            input_path,
            max_files=args.max_files,
            max_rows_per_file=args.max_rows_per_file,
        )
    )

    if not (0 < args.sample_frac <= 1.0):
        raise ValueError("--sample-frac must be in (0, 1]")

    if args.sample_frac < 1.0:
        raw = raw.sample(frac=args.sample_frac, random_state=args.random_state)

    processed = _build_features(raw)

    if args.ddos_only:
        fam = processed["attack_family"].astype(str)
        keep = fam.eq("normal") | fam.apply(_is_ddos_family)
        processed = processed.loc[keep].copy()
        processed["attack_family"] = np.where(
            processed["attack_family"].astype(str).eq("normal"),
            "normal",
            "ddos",
        )
        processed["label"] = (processed["attack_family"] == "ddos").astype(int)

        class_counts = processed["label"].value_counts().to_dict()
        if len(class_counts) < 2:
            raise ValueError(
                "--ddos-only produced a single-class dataset. "
                f"Class counts: {class_counts}. "
                "Use more input files/rows or a larger sample fraction so both normal and ddos exist."
            )

    if args.target_attack_ratio > 0:
        processed = _rebalance_binary_labels(
            processed,
            target_attack_ratio=args.target_attack_ratio,
            random_state=args.random_state,
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    processed.to_csv(output_path, index=False)

    print(f"Saved processed dataset to: {output_path}")
    _print_summary(processed)


if __name__ == "__main__":
    main()
