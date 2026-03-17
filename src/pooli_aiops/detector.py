from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from .alerting import AlertmanagerClient
from .config import AppSettings
from .contracts import ContractFile
from .features import engineer_features
from .modeling import align_feature_frame, load_artifacts, top_deviations
from .prometheus_client import PrometheusClient, build_range_frame
from .rca_engine import EventStore


def _format_detected_at(timestamp: datetime) -> str:
    """알림 메시지에 넣을 탐지 시각 문자열을 만든다."""
    local_time = timestamp.astimezone()
    return local_time.strftime("%Y-%m-%d %H:%M:%S %Z")


def _format_top_features(top_features: list[dict[str, float | str]], limit: int = 3) -> str:
    """상위 이상 feature를 읽기 쉬운 한 줄 문자열로 만든다."""
    formatted: list[str] = []
    for item in top_features[:limit]:
        formatted.append(
            f"{item['feature']}(현재 {float(item['value']):.2f}, 기준 {float(item['median']):.2f})"
        )
    return ", ".join(formatted) if formatted else "주요 지표 정보 없음"


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
        store = EventStore(settings.artifacts.rca_db_path)
        detected_at = _format_detected_at(now)
        top_feature_text = _format_top_features(top_features)
        
        description = (
            f"탐지 점수 {score:.2f}가 임계값 {threshold:.2f}를 초과했습니다. "
            f"탐지 시각: {detected_at}. "
            f"주요 지표: {top_feature_text}"
        )
        
        store.add_event(
            timestamp=now,
            application=contract.application,
            instance="all",  # ML Model acts on aggregate features unless specified
            source="ecod",
            severity="warning",
            metric="aggregate_score",
            score=score,
            description=description,
        )

    return result
