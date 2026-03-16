from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from .alerting import AlertmanagerClient
from .config import AppSettings
from .prometheus_client import PrometheusClient, build_range_frame


@dataclass(slots=True)
class BaselineDefaults:
    history_hours: int = 24
    step_seconds: int = 60
    evaluation_points: int = 5
    required_breaches: int = 3
    min_baseline_points: int = 60
    mad_epsilon: float = 1e-6


@dataclass(slots=True)
class BaselineMetricRule:
    key: str
    promql: str
    description: str = ""
    unit: str = ""
    direction: str = "high"
    labels: list[str] = field(default_factory=list)
    warn_z: float = 3.5
    critical_z: float = 5.0
    warn_value: float | None = None
    critical_value: float | None = None


@dataclass(slots=True)
class BaselineRuleFile:
    version: int
    application: str
    defaults: BaselineDefaults = field(default_factory=BaselineDefaults)
    metrics: list[BaselineMetricRule] = field(default_factory=list)


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def load_baseline_rules(path: str | Path) -> BaselineRuleFile:
    rules_path = Path(path)
    raw = _read_yaml(rules_path)

    defaults = BaselineDefaults(**raw.get("defaults", {}))
    metrics = [BaselineMetricRule(**entry) for entry in raw.get("metrics", [])]

    if defaults.history_hours <= 0:
        raise ValueError("history_hours must be positive")
    if defaults.step_seconds <= 0:
        raise ValueError("step_seconds must be positive")
    if defaults.evaluation_points <= 0:
        raise ValueError("evaluation_points must be positive")
    if defaults.required_breaches <= 0:
        raise ValueError("required_breaches must be positive")
    if defaults.required_breaches > defaults.evaluation_points:
        raise ValueError("required_breaches cannot exceed evaluation_points")

    for metric in metrics:
        if metric.direction not in {"high", "low"}:
            raise ValueError(f"Unsupported direction for {metric.key}: {metric.direction}")

    return BaselineRuleFile(
        version=raw.get("version", 1),
        application=raw["application"],
        defaults=defaults,
        metrics=metrics,
    )


def _series_labels(series_name: str) -> dict[str, str]:
    if "[" not in series_name or not series_name.endswith("]"):
        return {}

    label_block = series_name[series_name.index("[") + 1 : -1]
    labels: dict[str, str] = {}
    for part in label_block.split(","):
        key, _, value = part.partition("=")
        if key and value:
            labels[key] = value
    return labels


def _robust_z_scores(series: pd.Series, median: float, mad: float, epsilon: float) -> pd.Series:
    scale = max(float(mad), epsilon)
    return (0.6744897501960817 * (series - median)) / scale


def _matches_threshold(
    direction: str,
    z_score: float,
    value: float,
    z_threshold: float,
    value_threshold: float | None,
) -> bool:
    if direction == "high":
        z_match = z_score >= z_threshold
        value_match = value_threshold is None or value >= value_threshold
        return z_match and value_match

    z_match = z_score <= -z_threshold
    value_match = value_threshold is None or value <= value_threshold
    return z_match and value_match


def _format_detected_at(timestamp: datetime) -> str:
    return timestamp.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def _format_value(value: float, unit: str) -> str:
    if unit == "ms":
        return f"{value:.2f}ms"
    return f"{value:.3f}"


def _evaluate_series(
    metric: BaselineMetricRule,
    series: pd.Series,
    defaults: BaselineDefaults,
) -> dict[str, Any]:
    cleaned = pd.to_numeric(series, errors="coerce")
    cleaned = cleaned.replace([np.inf, -np.inf], np.nan).dropna()

    required_points = defaults.min_baseline_points + defaults.evaluation_points
    if len(cleaned) < required_points:
        return {
            "status": "skipped",
            "reason": (
                "Not enough points for baseline detection "
                f"(required={required_points}, actual={len(cleaned)})"
            ),
        }

    baseline = cleaned.iloc[:-defaults.evaluation_points]
    evaluation = cleaned.iloc[-defaults.evaluation_points :]

    baseline_median = float(baseline.median())
    baseline_mad = float((baseline - baseline_median).abs().median())
    z_scores = _robust_z_scores(evaluation, baseline_median, baseline_mad, defaults.mad_epsilon)

    warn_hits = sum(
        _matches_threshold(metric.direction, float(z_score), float(value), metric.warn_z, metric.warn_value)
        for value, z_score in zip(evaluation.tolist(), z_scores.tolist())
    )
    critical_hits = sum(
        _matches_threshold(
            metric.direction,
            float(z_score),
            float(value),
            metric.critical_z,
            metric.critical_value,
        )
        for value, z_score in zip(evaluation.tolist(), z_scores.tolist())
    )

    latest_value = float(evaluation.iloc[-1])
    latest_z = float(z_scores.iloc[-1])
    severity = "ok"
    if critical_hits >= defaults.required_breaches:
        severity = "critical"
    elif warn_hits >= defaults.required_breaches:
        severity = "warning"

    return {
        "status": "evaluated",
        "severity": severity,
        "current_value": latest_value,
        "baseline_median": baseline_median,
        "baseline_mad": baseline_mad,
        "latest_zscore": latest_z,
        "warn_hits": warn_hits,
        "critical_hits": critical_hits,
        "observed_points": defaults.evaluation_points,
        "window_start": evaluation.index.min().isoformat(),
        "window_end": evaluation.index.max().isoformat(),
    }


def _send_alert(
    alert_client: AlertmanagerClient,
    application: str,
    metric: BaselineMetricRule,
    series_name: str,
    finding: dict[str, Any],
    detected_at: datetime,
) -> None:
    labels = _series_labels(series_name)
    current_value = float(finding["current_value"])
    baseline_median = float(finding["baseline_median"])
    latest_z = float(finding["latest_zscore"])
    severity = str(finding["severity"])

    alert_client.send_anomaly(
        labels={
            "service": application,
            "severity": severity,
            "detector": "baseline",
            "metric": metric.key,
            **labels,
        },
        annotations={
            "summary": f"{application} baseline 이상 탐지: {metric.key}",
            "description": (
                f"{series_name} 현재값 {_format_value(current_value, metric.unit)}, "
                f"기준값 {_format_value(baseline_median, metric.unit)}, "
                f"robust_z {latest_z:.2f}, "
                f"탐지 시각: {_format_detected_at(detected_at)}"
            ),
        },
    )


def run_baseline_detection(
    settings: AppSettings,
    rules: BaselineRuleFile,
    dry_run: bool = False,
    timestamp: datetime | None = None,
) -> dict[str, Any]:
    now = timestamp or datetime.now(timezone.utc)
    defaults = rules.defaults
    start_time = now - timedelta(
        hours=defaults.history_hours,
        seconds=defaults.evaluation_points * defaults.step_seconds,
    )

    client = PrometheusClient(settings.prometheus)
    alert_client = AlertmanagerClient(settings.alertmanager)

    findings: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []
    evaluated_series = 0

    for metric in rules.metrics:
        try:
            frame = build_range_frame(
                client=client,
                metrics=[metric],
                start=start_time,
                end=now,
                step_seconds=defaults.step_seconds,
            )
        except Exception as exc:
            errors.append({"metric": metric.key, "error": str(exc)})
            continue

        for column in frame.columns:
            evaluated_series += 1
            finding = _evaluate_series(metric, frame[column], defaults)
            if finding["status"] == "skipped":
                skipped.append(
                    {
                        "metric": metric.key,
                        "series": column,
                        "reason": str(finding["reason"]),
                    }
                )
                continue

            if finding["severity"] == "ok":
                continue

            result = {
                "metric": metric.key,
                "series": column,
                "description": metric.description,
                "unit": metric.unit,
                **finding,
            }
            findings.append(result)

            if not dry_run:
                _send_alert(alert_client, rules.application, metric, column, finding, now)

    return {
        "status": "baseline_detected",
        "application": rules.application,
        "timestamp": now.isoformat(),
        "evaluated_series": evaluated_series,
        "finding_count": len(findings),
        "findings": findings,
        "skipped": skipped,
        "errors": errors,
    }
