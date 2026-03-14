from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from .alerting import AlertmanagerClient
from .config import AppSettings
from .contracts import ContractFile
from .features import engineer_features
from .modeling import align_feature_frame, load_artifacts, top_deviations
from .prometheus_client import PrometheusClient, build_range_frame


def run_detection(
    settings: AppSettings,
    contract: ContractFile,
    dry_run: bool = False,
    timestamp: datetime | None = None,
) -> dict[str, Any]:
    """가장 최근 Prometheus 구간으로 이상 탐지를 수행한다."""
    now = timestamp or datetime.now(timezone.utc)
    model, scaler, metadata = load_artifacts(settings.artifact_dir)

    rolling_window = int(metadata.get("rolling_window", settings.model.rolling_window))
    metadata_step_seconds = int(metadata.get("step_seconds", settings.prometheus.step_seconds))
    effective_step_seconds = max(metadata_step_seconds, settings.prometheus.step_seconds)

    required_points = max(
        settings.detection.lookback_points,
        rolling_window + 3,
    )
    lookback_seconds = required_points * effective_step_seconds
    start_time = now - timedelta(seconds=lookback_seconds)

    client = PrometheusClient(settings.prometheus)
    raw_frame = build_range_frame(
        client=client,
        metrics=contract.metrics,
        start=start_time,
        end=now,
        step_seconds=effective_step_seconds,
    )

    feature_frame = engineer_features(raw_frame, rolling_window)
    latest = feature_frame.tail(1)
    if latest.empty:
        raise ValueError(
            "탐지에 사용할 feature가 없습니다 "
            f"(raw_rows={len(raw_frame)}, feature_rows={len(feature_frame)}, "
            f"step_seconds={effective_step_seconds}, rolling_window={rolling_window}, "
            f"raw_columns={list(raw_frame.columns)})"
        )

    aligned = align_feature_frame(latest, metadata["feature_columns"])
    scaled = scaler.transform(aligned)
    score = float(model.decision_function(scaled)[0])
    threshold = float(metadata["threshold"])
    is_anomaly = score >= threshold + settings.detection.alert_score_margin

    top_features = top_deviations(aligned.iloc[0], metadata.get("feature_medians", {}))

    result = {
        "status": "detected",
        "application": contract.application,
        "timestamp": now.isoformat(),
        "score": score,
        "threshold": threshold,
        "is_anomaly": is_anomaly,
        "top_features": top_features,
    }

    if is_anomaly and not dry_run:
        alert_client = AlertmanagerClient(settings.alertmanager)
        alert_client.send_anomaly(
            labels={
                "service": contract.application,
                "severity": "warning",
                "model": str(metadata.get("model_name", settings.model.name)),
            },
            annotations={
                "summary": f"PyOD anomaly detected for {contract.application}",
                "description": f"score={score:.6f}, threshold={threshold:.6f}",
                "top_features": ", ".join(item["feature"] for item in top_features),
            },
        )

    return result
