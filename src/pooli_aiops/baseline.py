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
from .rca_engine import EventStore


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
    if unit == "ratio":
        return f"{value * 100:.1f}%"
    return f"{value:.3f}"


def _metric_display_name(metric_key: str) -> str:
    metric_names = {
        "cpu_usage_ratio": "CPU 사용률",
        "load1_per_cpu": "1분 Load / CPU 코어",
        "memory_available_ratio": "가용 메모리 비율",
        "cpu_iowait_ratio": "CPU I/O 대기 비율",
        "disk_util_ratio": "디스크 사용률",
        "disk_read_latency_ms": "디스크 읽기 지연",
        "disk_write_latency_ms": "디스크 쓰기 지연",
    }
    return metric_names.get(metric_key, metric_key)


def _format_breach_points(points: list[dict[str, Any]], unit: str) -> str:
    if not points:
        return "- 없음"

    formatted: list[str] = []
    for point in points:
        timestamp = datetime.fromisoformat(str(point["timestamp"])).astimezone().strftime("%H:%M:%S")
        value = _format_value(float(point["value"]), unit)
        zscore = float(point["zscore"])
        formatted.append(f"- {timestamp} 값 {value}, z {zscore:.2f}")
    return "\n".join(formatted)


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

    warn_breach_points = [
        {
            "timestamp": index.isoformat(),
            "value": float(value),
            "zscore": float(z_score),
        }
        for index, value, z_score in zip(evaluation.index, evaluation.tolist(), z_scores.tolist())
        if _matches_threshold(metric.direction, float(z_score), float(value), metric.warn_z, metric.warn_value)
    ]
    critical_breach_points = [
        {
            "timestamp": index.isoformat(),
            "value": float(value),
            "zscore": float(z_score),
        }
        for index, value, z_score in zip(evaluation.index, evaluation.tolist(), z_scores.tolist())
        if _matches_threshold(
            metric.direction,
            float(z_score),
            float(value),
            metric.critical_z,
            metric.critical_value,
        )
    ]

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
        "warn_breach_points": warn_breach_points,
        "critical_breach_points": critical_breach_points,
        "observed_points": defaults.evaluation_points,
        "required_breaches": defaults.required_breaches,
        "window_start": evaluation.index.min().isoformat(),
        "window_end": evaluation.index.max().isoformat(),
    }


def _build_alert_context(
    application: str,
    metric: BaselineMetricRule,
    series_name: str,
    finding: dict[str, Any],
    detected_at: datetime,
) -> tuple[dict[str, str], str, str]:
    labels = _series_labels(series_name)
    instance = labels.get("instance", series_name)
    current_value = float(finding["current_value"])
    baseline_median = float(finding["baseline_median"])
    latest_z = float(finding["latest_zscore"])
    observed_points = int(finding["observed_points"])
    warn_hits = int(finding["warn_hits"])
    required_breaches = int(finding["required_breaches"])
    severity = str(finding["severity"])
    metric_name = _metric_display_name(metric.key)
    warn_breach_points = _format_breach_points(list(finding["warn_breach_points"]), metric.unit)
    warn_value_text = (
        _format_value(metric.warn_value, metric.unit)
        if metric.warn_value is not None
        else "값 기준 없음"
    )
    critical_value_text = (
        _format_value(metric.critical_value, metric.unit)
        if metric.critical_value is not None
        else "값 기준 없음"
    )

    summary = f"[AIOps][{severity}] {instance} {metric_name} 이상 탐지"
    description = (
        f"서비스: {application}\n"
        f"인스턴스: {instance}\n"
        f"탐지 방식: 기준선 감시\n"
        f"메트릭: {metric_name}\n"
        f"심각도: {severity}\n"
        f"상태: firing\n\n"
        f"현재값: {_format_value(current_value, metric.unit)}\n"
        f"기준값: {_format_value(baseline_median, metric.unit)}\n"
        f"이상 점수(z): {latest_z:.2f}\n"
        f"최근 {observed_points}개 포인트 중 {warn_hits}회 경고 조건 충족\n"
        f"경고 충족 시각:\n{warn_breach_points}\n"
        f"경고 판정 기준: 최근 {observed_points}개 포인트 중 {required_breaches}개 이상에서 z >= {metric.warn_z:.1f} 이고 현재값 >= {warn_value_text}\n"
        f"치명 판정 기준: 최근 {observed_points}개 포인트 중 {required_breaches}개 이상에서 z >= {metric.critical_z:.1f} 이고 현재값 >= {critical_value_text}\n"
        f"탐지 시각: {_format_detected_at(detected_at)}"
    )
    return labels, summary, description


def _send_alert(
    alert_client: AlertmanagerClient,
    application: str,
    metric: BaselineMetricRule,
    series_name: str,
    finding: dict[str, Any],
    detected_at: datetime,
) -> None:
    labels, summary, description = _build_alert_context(
        application=application,
        metric=metric,
        series_name=series_name,
        finding=finding,
        detected_at=detected_at,
    )
    alert_client.send_anomaly(
        labels={
            "service": application,
            "severity": str(finding["severity"]),
            "detector": "baseline",
            "metric": metric.key,
            **labels,
        },
        annotations={
            "summary": summary,
            "description": description,
        },
    )


def _store_alert(
    store: EventStore,
    application: str,
    metric: BaselineMetricRule,
    series_name: str,
    finding: dict[str, Any],
    detected_at: datetime,
) -> None:
    labels, _, description = _build_alert_context(
        application=application,
        metric=metric,
        series_name=series_name,
        finding=finding,
        detected_at=detected_at,
    )
    store.add_event(
        timestamp=detected_at,
        application=application,
        instance=labels.get("instance", series_name),
        source="baseline",
        severity=str(finding["severity"]),
        metric=metric.key,
        score=float(finding["latest_zscore"]),
        description=description,
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
    store = EventStore(settings.rca_db_path)

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

            findings.append(
                {
                    "metric": metric.key,
                    "series": column,
                    "description": metric.description,
                    "unit": metric.unit,
                    **finding,
                }
            )

            if not dry_run:
                _store_alert(store, rules.application, metric, column, finding, now)
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
