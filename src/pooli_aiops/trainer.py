from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from .config import AppSettings
from .contracts import ContractFile
from .features import engineer_features
from .modeling import fit_model, save_artifacts
from .prometheus_client import PrometheusClient, build_range_frame

CSV_EXCLUDED_COLUMNS = {
    "scenario_id",
    "is_anomaly",
    "anomaly_train_eligible",
}
CSV_EXCLUDED_PREFIXES = ("future_",)


def _coerce_filter_value(series: pd.Series, raw_value: str) -> Any:
    """Convert a filter literal to the dtype used by the target column."""
    if pd.api.types.is_bool_dtype(series):
        normalized = raw_value.strip().lower()
        if normalized in {"1", "true", "yes", "y"}:
            return True
        if normalized in {"0", "false", "no", "n"}:
            return False
        raise ValueError(f"Unsupported boolean filter value: {raw_value}")

    if pd.api.types.is_integer_dtype(series):
        return int(raw_value)

    if pd.api.types.is_float_dtype(series):
        return float(raw_value)

    return raw_value


def _load_csv_training_frame(
    input_csv: str | Path,
    timestamp_column: str,
    start: datetime | None,
    end: datetime | None,
    row_filter_column: str | None,
    row_filter_value: str,
) -> pd.DataFrame:
    """Load and optionally filter a CSV-backed training dataset."""
    csv_path = Path(input_csv)
    frame = pd.read_csv(csv_path)

    if timestamp_column not in frame.columns:
        raise ValueError(f"CSV is missing timestamp column: {timestamp_column}")

    timestamps = pd.to_datetime(frame[timestamp_column], utc=True, errors="coerce")
    if timestamps.isna().any():
        raise ValueError(f"CSV contains invalid timestamps in column: {timestamp_column}")

    frame = frame.drop(columns=[timestamp_column])
    frame.index = pd.DatetimeIndex(timestamps, name=timestamp_column)
    frame = frame.sort_index()

    if row_filter_column:
        if row_filter_column not in frame.columns:
            raise ValueError(f"CSV is missing filter column: {row_filter_column}")
        expected_value = _coerce_filter_value(frame[row_filter_column], row_filter_value)
        frame = frame.loc[frame[row_filter_column] == expected_value]

    if start is not None:
        frame = frame.loc[frame.index >= start]
    if end is not None:
        frame = frame.loc[frame.index <= end]

    if frame.empty:
        raise ValueError("No CSV rows available for training after filtering")

    return frame


def _resolve_metric_columns(
    raw_frame: pd.DataFrame,
    contract: ContractFile | None,
    metric_columns: list[str] | None,
) -> list[str]:
    """Choose which CSV columns should be treated as raw metric inputs."""
    if metric_columns:
        missing = [column for column in metric_columns if column not in raw_frame.columns]
        if missing:
            raise ValueError(f"Missing requested metric columns in CSV: {missing}")
        return metric_columns

    if contract is not None:
        required_missing = [metric.key for metric in contract.metrics if metric.required and metric.key not in raw_frame.columns]
        if required_missing:
            raise ValueError(f"CSV is missing required contract metrics: {required_missing}")

        contract_columns = [metric.key for metric in contract.metrics if metric.key in raw_frame.columns]
        if contract_columns:
            return contract_columns

    numeric_columns = raw_frame.select_dtypes(include="number").columns.tolist()
    inferred = [
        column
        for column in numeric_columns
        if column not in CSV_EXCLUDED_COLUMNS and not column.startswith(CSV_EXCLUDED_PREFIXES)
    ]
    if not inferred:
        raise ValueError("Could not infer metric columns from the CSV input")
    return inferred


def _infer_step_seconds(index: pd.DatetimeIndex) -> int | None:
    """Estimate sampling resolution from a datetime index."""
    if len(index) < 2:
        return None

    deltas = index.to_series().diff().dropna().dt.total_seconds()
    deltas = deltas[deltas > 0]
    if deltas.empty:
        return None
    return max(1, int(round(float(deltas.median()))))


def _load_training_frame(
    settings: AppSettings,
    contract: ContractFile | None,
    start: datetime | None,
    end: datetime | None,
    input_csv: str | Path | None,
    timestamp_column: str,
    metric_columns: list[str] | None,
    row_filter_column: str | None,
    row_filter_value: str,
    application: str | None,
) -> tuple[pd.DataFrame, datetime, datetime, int, str, str, str | None]:
    """Load raw training metrics from Prometheus or a CSV file."""
    if input_csv is not None:
        source_frame = _load_csv_training_frame(
            input_csv=input_csv,
            timestamp_column=timestamp_column,
            start=start,
            end=end,
            row_filter_column=row_filter_column,
            row_filter_value=row_filter_value,
        )
        selected_columns = _resolve_metric_columns(source_frame, contract, metric_columns)
        raw_frame = source_frame[selected_columns]
        start_time = raw_frame.index.min().to_pydatetime()
        end_time = raw_frame.index.max().to_pydatetime()
        step_seconds = _infer_step_seconds(raw_frame.index) or settings.prometheus.step_seconds
        application_name = application or (contract.application if contract is not None else Path(input_csv).stem)
        return raw_frame, start_time, end_time, step_seconds, application_name, "csv", str(Path(input_csv))

    if contract is None:
        raise ValueError("contract is required when training from Prometheus")

    end_time = end or datetime.now(timezone.utc)
    start_time = start or end_time - timedelta(hours=settings.model.history_hours)

    client = PrometheusClient(settings.prometheus)
    raw_frame = build_range_frame(
        client=client,
        metrics=contract.metrics,
        start=start_time,
        end=end_time,
        step_seconds=settings.prometheus.step_seconds,
    )
    return raw_frame, start_time, end_time, settings.prometheus.step_seconds, contract.application, "prometheus", None


def run_training(
    settings: AppSettings,
    contract: ContractFile | None,
    start: datetime | None = None,
    end: datetime | None = None,
    input_csv: str | Path | None = None,
    timestamp_column: str = "timestamp",
    metric_columns: list[str] | None = None,
    row_filter_column: str | None = None,
    row_filter_value: str = "1",
    application: str | None = None,
) -> dict[str, Any]:
    """Train an anomaly model from Prometheus history or a CSV file."""
    raw_frame, start_time, end_time, step_seconds, application_name, data_source, csv_path = _load_training_frame(
        settings=settings,
        contract=contract,
        start=start,
        end=end,
        input_csv=input_csv,
        timestamp_column=timestamp_column,
        metric_columns=metric_columns,
        row_filter_column=row_filter_column,
        row_filter_value=row_filter_value,
        application=application,
    )

    feature_frame = engineer_features(raw_frame, settings.model.rolling_window)

    if len(feature_frame) < settings.model.min_training_rows:
        raise ValueError(
            "Not enough training rows after feature engineering. "
            f"Expected at least {settings.model.min_training_rows}, got {len(feature_frame)}"
        )

    model, scaler, metadata = fit_model(
        feature_frame=feature_frame,
        model_name=settings.model.name,
        contamination=settings.model.contamination,
    )
    metadata.update(
        {
            "application": application_name,
            "trained_at": datetime.now(timezone.utc).isoformat(),
            "start": start_time.isoformat(),
            "end": end_time.isoformat(),
            "raw_columns": list(raw_frame.columns),
            "rolling_window": settings.model.rolling_window,
            "step_seconds": step_seconds,
            "training_rows": int(len(feature_frame)),
            "data_source": data_source,
            "input_csv": csv_path,
        }
    )

    save_artifacts(settings.artifact_dir, model, scaler, metadata)

    return {
        "status": "trained",
        "application": application_name,
        "artifact_dir": str(settings.artifact_dir),
        "training_rows": len(feature_frame),
        "feature_columns": len(feature_frame.columns),
        "start": start_time.isoformat(),
        "end": end_time.isoformat(),
        "model": settings.model.name,
        "data_source": data_source,
        "input_csv": csv_path,
    }
