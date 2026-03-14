from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .features import engineer_features

METRIC_COLUMNS = [
    "traffic_stream_length",
    "traffic_stream_pending_message",
    "traffic_stream_requests_tps",
    "traffic_stream_enqueue_latency",
    "traffic_dlq_total",
    "traffic_stream_oldest_pending_idle_seconds",
    "traffic_hydrate_total",
    "traffic_refill_total",
]

EVENT_TYPES = [
    "backlog",
    "latency_spike",
    "dlq_burst",
    "consumer_slowdown",
]


@dataclass(slots=True)
class SyntheticDatasetConfig:
    output_dir: Path
    start: datetime | None = None
    hours: int = 24
    freq_seconds: int = 1
    seed: int = 42
    future_seconds: int = 60
    rolling_window: int = 30


def _validate_config(config: SyntheticDatasetConfig) -> None:
    if config.hours <= 0:
        raise ValueError("hours must be positive")
    if config.freq_seconds <= 0:
        raise ValueError("freq_seconds must be positive")
    if config.future_seconds <= 0:
        raise ValueError("future_seconds must be positive")
    if config.rolling_window < 2:
        raise ValueError("rolling_window must be at least 2")


def _build_index(config: SyntheticDatasetConfig) -> pd.DatetimeIndex:
    periods = int((config.hours * 3600) / config.freq_seconds)
    if periods <= config.rolling_window + 5:
        raise ValueError("dataset is too short for the configured rolling window")

    start = config.start or datetime(2026, 3, 13, tzinfo=timezone.utc)
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    else:
        start = start.astimezone(timezone.utc)

    return pd.date_range(
        start=start,
        periods=periods,
        freq=f"{config.freq_seconds}s",
        name="timestamp",
    )


def _base_metrics(periods: int, freq_seconds: int, rng: np.random.Generator) -> dict[str, np.ndarray]:
    seconds = np.arange(periods) * freq_seconds
    daily_wave = np.sin((2 * np.pi * seconds / 86_400) - (np.pi / 2))
    micro_wave = np.sin(2 * np.pi * seconds / 1_800)
    load = np.clip(
        1.0 + 0.28 * daily_wave + 0.06 * micro_wave + rng.normal(0, 0.02, size=periods),
        0.6,
        1.5,
    )

    requests_tps = 22 + (12 * load) + rng.normal(0, 2.0, size=periods)
    stream_length = 32 + (16 * load) + rng.normal(0, 4.0, size=periods)
    pending_message = 8 + (5 * load) + rng.normal(0, 1.8, size=periods)
    enqueue_latency = 4 + (1.4 * load) + rng.normal(0, 0.6, size=periods)
    oldest_pending_idle = 1.3 + (0.7 * load) + rng.normal(0, 0.25, size=periods)

    dlq_rate = rng.poisson(0.02 + (0.01 * load), size=periods).astype(float)
    hydrate_rate = rng.poisson(1.0 + (0.5 * load), size=periods).astype(float)
    refill_rate = rng.poisson(0.8 + (0.4 * load), size=periods).astype(float)

    for _ in range(max(3, periods // 14_400)):
        center = int(rng.integers(120, periods - 120))
        half_width = int(rng.integers(30, 90))
        left = max(0, center - half_width)
        right = min(periods, center + half_width)
        window = right - left
        envelope = np.sin(np.linspace(0, np.pi, window)) ** 2
        requests_tps[left:right] += envelope * rng.uniform(4, 9)
        enqueue_latency[left:right] += envelope * rng.uniform(0.6, 2.0)

    return {
        "traffic_stream_length": stream_length,
        "traffic_stream_pending_message": pending_message,
        "traffic_stream_requests_tps": requests_tps,
        "traffic_stream_enqueue_latency": enqueue_latency,
        "traffic_stream_oldest_pending_idle_seconds": oldest_pending_idle,
        "dlq_rate": dlq_rate,
        "hydrate_rate": hydrate_rate,
        "refill_rate": refill_rate,
    }


def _schedule_events(periods: int, freq_seconds: int, rng: np.random.Generator) -> list[dict[str, int | str]]:
    events: list[dict[str, int | str]] = []
    min_buffer = max(1, int(15 * 60 / freq_seconds))
    cursor = max(1, int(20 * 60 / freq_seconds))
    rotation = EVENT_TYPES.copy()
    rng.shuffle(rotation)

    while cursor < periods - min_buffer:
        gap = int(rng.integers(max(1, int(25 * 60 / freq_seconds)), max(2, int(60 * 60 / freq_seconds))))
        start = cursor + gap
        if start >= periods - min_buffer:
            break

        duration = int(rng.integers(max(1, int(8 * 60 / freq_seconds)), max(2, int(18 * 60 / freq_seconds))))
        end = min(start + duration, periods - min_buffer)
        event_type = rotation[len(events) % len(rotation)]

        events.append(
            {
                "scenario_id": len(events) + 1,
                "event_type": event_type,
                "start_idx": start,
                "end_idx": end,
            }
        )
        cursor = end

    return events


def _apply_event(
    metrics: dict[str, np.ndarray],
    state_labels: np.ndarray,
    scenario_ids: np.ndarray,
    event: dict[str, int | str],
    rng: np.random.Generator,
) -> None:
    start = int(event["start_idx"])
    end = int(event["end_idx"])
    event_type = str(event["event_type"])
    scenario_id = int(event["scenario_id"])

    window = end - start
    envelope = np.sin(np.linspace(0, np.pi, window)) ** 2
    noise = rng.normal(0, 1.0, size=window)
    event_slice = slice(start, end)

    state_labels[event_slice] = event_type
    scenario_ids[event_slice] = scenario_id

    if event_type == "backlog":
        metrics["traffic_stream_length"][event_slice] += 95 * envelope + (4 * noise)
        metrics["traffic_stream_pending_message"][event_slice] += 72 * envelope + (3 * noise)
        metrics["traffic_stream_requests_tps"][event_slice] += 28 * envelope + (2 * noise)
        metrics["traffic_stream_enqueue_latency"][event_slice] += 60 * envelope + (5 * noise)
        metrics["traffic_stream_oldest_pending_idle_seconds"][event_slice] += 12 * envelope + (0.5 * noise)
        metrics["dlq_rate"][event_slice] += rng.poisson(0.5 + (1.5 * envelope), size=window)
        metrics["hydrate_rate"][event_slice] += rng.poisson(2.0 + (2.5 * envelope), size=window)
        metrics["refill_rate"][event_slice] += rng.poisson(1.0 + (2.0 * envelope), size=window)
        return

    if event_type == "latency_spike":
        metrics["traffic_stream_length"][event_slice] += 18 * envelope + (2 * noise)
        metrics["traffic_stream_pending_message"][event_slice] += 16 * envelope + (2 * noise)
        metrics["traffic_stream_requests_tps"][event_slice] -= 4 * envelope
        metrics["traffic_stream_enqueue_latency"][event_slice] += 120 * envelope + (8 * noise)
        metrics["traffic_stream_oldest_pending_idle_seconds"][event_slice] += 4 * envelope + (0.4 * noise)
        metrics["dlq_rate"][event_slice] += rng.poisson(1.0 + (3.0 * envelope), size=window)
        metrics["hydrate_rate"][event_slice] += rng.poisson(1.0 + (1.5 * envelope), size=window)
        metrics["refill_rate"][event_slice] += rng.poisson(0.5 + (1.0 * envelope), size=window)
        return

    if event_type == "dlq_burst":
        metrics["traffic_stream_length"][event_slice] += 22 * envelope + (3 * noise)
        metrics["traffic_stream_pending_message"][event_slice] += 18 * envelope + (2 * noise)
        metrics["traffic_stream_requests_tps"][event_slice] += 10 * envelope + noise
        metrics["traffic_stream_enqueue_latency"][event_slice] += 20 * envelope + (3 * noise)
        metrics["traffic_stream_oldest_pending_idle_seconds"][event_slice] += 5 * envelope + (0.3 * noise)
        metrics["dlq_rate"][event_slice] += rng.poisson(3.0 + (8.0 * envelope), size=window)
        metrics["hydrate_rate"][event_slice] += rng.poisson(2.0 + (4.0 * envelope), size=window)
        metrics["refill_rate"][event_slice] += rng.poisson(1.5 + (3.0 * envelope), size=window)
        return

    if event_type == "consumer_slowdown":
        metrics["traffic_stream_length"][event_slice] += 110 * envelope + (4 * noise)
        metrics["traffic_stream_pending_message"][event_slice] += 85 * envelope + (3 * noise)
        metrics["traffic_stream_requests_tps"][event_slice] += 8 * envelope + noise
        metrics["traffic_stream_enqueue_latency"][event_slice] += 42 * envelope + (4 * noise)
        metrics["traffic_stream_oldest_pending_idle_seconds"][event_slice] += 18 * envelope + (0.6 * noise)
        metrics["dlq_rate"][event_slice] += rng.poisson(0.5 + (2.0 * envelope), size=window)
        metrics["hydrate_rate"][event_slice] -= 0.4 * envelope
        metrics["refill_rate"][event_slice] += rng.poisson(1.0 + (2.0 * envelope), size=window)


def _finalize_frame(
    metrics: dict[str, np.ndarray],
    index: pd.DatetimeIndex,
    state_labels: np.ndarray,
    scenario_ids: np.ndarray,
) -> pd.DataFrame:
    stream_length = np.rint(np.clip(metrics["traffic_stream_length"], 0, None)).astype(int)
    pending_message = np.rint(np.clip(metrics["traffic_stream_pending_message"], 0, None)).astype(int)
    requests_tps = np.round(np.clip(metrics["traffic_stream_requests_tps"], 0, None), 3)
    enqueue_latency = np.round(np.clip(metrics["traffic_stream_enqueue_latency"], 0, None), 3)
    oldest_pending_idle = np.round(np.clip(metrics["traffic_stream_oldest_pending_idle_seconds"], 0, None), 3)

    dlq_total = np.cumsum(np.rint(np.clip(metrics["dlq_rate"], 0, None)).astype(int))
    hydrate_total = np.cumsum(np.rint(np.clip(metrics["hydrate_rate"], 0, None)).astype(int))
    refill_total = np.cumsum(np.rint(np.clip(metrics["refill_rate"], 0, None)).astype(int))

    raw_frame = pd.DataFrame(
        index=index,
        data={
            "traffic_stream_length": stream_length,
            "traffic_stream_pending_message": pending_message,
            "traffic_stream_requests_tps": requests_tps,
            "traffic_stream_enqueue_latency": enqueue_latency,
            "traffic_dlq_total": dlq_total,
            "traffic_stream_oldest_pending_idle_seconds": oldest_pending_idle,
            "traffic_hydrate_total": hydrate_total,
            "traffic_refill_total": refill_total,
            "scenario_id": scenario_ids.astype(int),
            "state_label": state_labels,
        },
    )
    raw_frame["is_anomaly"] = (raw_frame["state_label"] != "normal").astype(int)
    return raw_frame


def _add_targets(raw_frame: pd.DataFrame, future_steps: int, future_label: int) -> pd.DataFrame:
    labeled = raw_frame.copy()
    split = np.full(len(labeled), "train", dtype=object)
    validation_start = int(len(labeled) * 0.70)
    test_start = int(len(labeled) * 0.85)
    split[validation_start:test_start] = "validation"
    split[test_start:] = "test"
    labeled["split"] = split

    labeled[f"future_is_anomaly_t_plus_{future_label}"] = labeled["is_anomaly"].shift(-future_steps)
    labeled[f"future_state_label_t_plus_{future_label}"] = labeled["state_label"].shift(-future_steps)
    labeled[f"future_traffic_stream_length_t_plus_{future_label}"] = labeled["traffic_stream_length"].shift(
        -future_steps
    )
    labeled["anomaly_train_eligible"] = ((labeled["split"] == "train") & (labeled["is_anomaly"] == 0)).astype(int)
    return labeled


def _feature_dataset(raw_frame: pd.DataFrame, config: SyntheticDatasetConfig) -> pd.DataFrame:
    feature_frame = engineer_features(raw_frame[METRIC_COLUMNS], config.rolling_window)
    labeled = raw_frame.loc[feature_frame.index]
    dataset = feature_frame.join(
        labeled[
            [
                "scenario_id",
                "state_label",
                "is_anomaly",
                "split",
                "anomaly_train_eligible",
                f"future_is_anomaly_t_plus_{config.future_seconds}",
                f"future_state_label_t_plus_{config.future_seconds}",
                f"future_traffic_stream_length_t_plus_{config.future_seconds}",
            ]
        ]
    )
    return dataset.dropna()


def _event_frame(events: list[dict[str, int | str]], index: pd.DatetimeIndex, freq_seconds: int) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for event in events:
        start_idx = int(event["start_idx"])
        end_idx = int(event["end_idx"])
        rows.append(
            {
                "scenario_id": int(event["scenario_id"]),
                "event_type": str(event["event_type"]),
                "start_idx": start_idx,
                "end_idx": end_idx,
                "start_time": index[start_idx].isoformat(),
                "end_time": index[end_idx - 1].isoformat(),
                "duration_seconds": (end_idx - start_idx) * freq_seconds,
            }
        )
    return pd.DataFrame(rows)


def generate_datasets(config: SyntheticDatasetConfig) -> dict[str, Any]:
    _validate_config(config)

    index = _build_index(config)
    rng = np.random.default_rng(config.seed)
    metrics = _base_metrics(len(index), config.freq_seconds, rng)
    state_labels = np.full(len(index), "normal", dtype=object)
    scenario_ids = np.zeros(len(index), dtype=int)
    events = _schedule_events(len(index), config.freq_seconds, rng)

    for event in events:
        _apply_event(metrics, state_labels, scenario_ids, event, rng)

    raw_frame = _finalize_frame(metrics, index, state_labels, scenario_ids)
    future_steps = max(1, int(config.future_seconds / config.freq_seconds))
    raw_frame = _add_targets(raw_frame, future_steps=future_steps, future_label=config.future_seconds)
    feature_dataset = _feature_dataset(raw_frame, config)
    event_frame = _event_frame(events, index, config.freq_seconds)

    output_dir = config.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    raw_path = output_dir / "synthetic_raw_timeseries.csv"
    feature_path = output_dir / "synthetic_feature_dataset.csv"
    anomaly_train_path = output_dir / "synthetic_anomaly_train.csv"
    anomaly_eval_path = output_dir / "synthetic_anomaly_eval.csv"
    events_path = output_dir / "synthetic_events.csv"
    metadata_path = output_dir / "metadata.json"

    raw_frame.to_csv(raw_path)
    feature_dataset.to_csv(feature_path)
    feature_dataset.loc[feature_dataset["anomaly_train_eligible"] == 1].to_csv(anomaly_train_path)
    feature_dataset.loc[feature_dataset["split"] != "train"].to_csv(anomaly_eval_path)
    event_frame.to_csv(events_path, index=False)

    metadata = {
        "seed": config.seed,
        "start": index[0].isoformat(),
        "end": index[-1].isoformat(),
        "hours": config.hours,
        "freq_seconds": config.freq_seconds,
        "future_seconds": config.future_seconds,
        "rolling_window": config.rolling_window,
        "raw_rows": int(len(raw_frame)),
        "feature_rows": int(len(feature_dataset)),
        "event_count": int(len(event_frame)),
        "label_distribution": {
            key: int(value) for key, value in raw_frame["state_label"].value_counts().sort_index().to_dict().items()
        },
        "split_distribution": {
            key: int(value) for key, value in raw_frame["split"].value_counts().sort_index().to_dict().items()
        },
    }
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    return {
        "status": "generated",
        "output_dir": str(output_dir),
        "raw_path": str(raw_path),
        "feature_path": str(feature_path),
        "anomaly_train_path": str(anomaly_train_path),
        "anomaly_eval_path": str(anomaly_eval_path),
        "events_path": str(events_path),
        "metadata_path": str(metadata_path),
        "raw_rows": len(raw_frame),
        "feature_rows": len(feature_dataset),
        "event_count": len(event_frame),
    }




