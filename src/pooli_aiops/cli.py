from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import load_settings
from .contracts import load_contract
from .detector import run_detection
from .synthetic_data import SyntheticDatasetConfig, generate_datasets
from .trainer import run_training


def _parse_datetime(value: str | None) -> datetime | None:
    """Parse CLI datetimes as UTC-aware values."""
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_metric_columns(value: str | None) -> list[str] | None:
    """Split a comma-separated metric column list."""
    if value is None:
        return None
    columns = [column.strip() for column in value.split(",") if column.strip()]
    return columns or None


def _common_parser(parser: argparse.ArgumentParser, require_contract: bool = True) -> None:
    """Attach shared settings/contract options."""
    parser.add_argument(
        "--settings",
        type=Path,
        required=True,
        help="Path to settings YAML",
    )
    parser.add_argument(
        "--contract",
        type=Path,
        required=require_contract,
        help="Path to metric contract YAML",
    )


def build_parser() -> argparse.ArgumentParser:
    """Define the project CLI commands."""
    parser = argparse.ArgumentParser(description="PyOD-based AIOps CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    train_parser = subparsers.add_parser("train", help="Train a model from Prometheus data or a CSV file")
    _common_parser(train_parser, require_contract=False)
    train_parser.add_argument("--start", help="UTC start time in ISO-8601 format")
    train_parser.add_argument("--end", help="UTC end time in ISO-8601 format")
    train_parser.add_argument(
        "--input-csv",
        type=Path,
        help="Path to a CSV file containing raw metric time series",
    )
    train_parser.add_argument(
        "--timestamp-column",
        default="timestamp",
        help="Timestamp column name for CSV training input",
    )
    train_parser.add_argument(
        "--metric-columns",
        help="Comma-separated metric columns to use from the CSV file",
    )
    train_parser.add_argument(
        "--row-filter-column",
        help="Optional CSV column used to filter training rows before feature generation",
    )
    train_parser.add_argument(
        "--row-filter-value",
        default="1",
        help="Filter value matched against --row-filter-column",
    )
    train_parser.add_argument(
        "--application",
        help="Application name to store in metadata when training from CSV without a contract",
    )

    detect_parser = subparsers.add_parser("detect", help="Detect anomalies from latest metrics")
    _common_parser(detect_parser, require_contract=True)
    detect_parser.add_argument("--time", help="UTC detection time in ISO-8601 format")
    detect_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip sending alerts even if anomaly is detected",
    )

    generate_parser = subparsers.add_parser(
        "generate-dataset",
        help="Generate synthetic datasets for classification, anomaly detection, and forecasting",
    )
    generate_parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("artifacts/synthetic_dataset"),
        help="Directory where generated CSV files will be written",
    )
    generate_parser.add_argument(
        "--hours",
        type=int,
        default=24,
        help="Length of the synthetic time series in hours",
    )
    generate_parser.add_argument(
        "--freq-seconds",
        type=int,
        default=1,
        help="Sampling interval in seconds",
    )
    generate_parser.add_argument(
        "--future-seconds",
        type=int,
        default=60,
        help="Forecasting horizon in seconds",
    )
    generate_parser.add_argument(
        "--rolling-window",
        type=int,
        default=30,
        help="Rolling window size used for feature generation",
    )
    generate_parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for reproducible datasets",
    )
    generate_parser.add_argument("--start", help="UTC start time in ISO-8601 format")

    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the selected pipeline and print JSON output."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "generate-dataset":
        result: dict[str, Any] = generate_datasets(
            SyntheticDatasetConfig(
                output_dir=args.output_dir,
                start=_parse_datetime(args.start),
                hours=args.hours,
                freq_seconds=args.freq_seconds,
                future_seconds=args.future_seconds,
                rolling_window=args.rolling_window,
                seed=args.seed,
            )
        )
    elif args.command == "train":
        settings = load_settings(args.settings)
        contract = load_contract(args.contract) if args.contract else None

        if args.input_csv is None and contract is None:
            parser.error("train requires --contract unless --input-csv is provided")

        result = run_training(
            settings=settings,
            contract=contract,
            start=_parse_datetime(args.start),
            end=_parse_datetime(args.end),
            input_csv=args.input_csv,
            timestamp_column=args.timestamp_column,
            metric_columns=_parse_metric_columns(args.metric_columns),
            row_filter_column=args.row_filter_column,
            row_filter_value=args.row_filter_value,
            application=args.application,
        )
    else:
        settings = load_settings(args.settings)
        contract = load_contract(args.contract)
        result = run_detection(
            settings=settings,
            contract=contract,
            dry_run=args.dry_run,
            timestamp=_parse_datetime(args.time),
        )

    print(json.dumps(result, indent=2))
    return 0
