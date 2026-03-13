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
    """저장된 모델로 최신 메트릭 구간의 이상 점수를 계산한다."""
    now = timestamp or datetime.now(timezone.utc)
    model, scaler, metadata = load_artifacts(settings.artifact_dir)

    # 학습 때 쓴 rolling feature를 다시 만들려면 과거 데이터가 충분히 필요하다.
    required_points = max(
        settings.detection.lookback_points,
        int(metadata.get("rolling_window", settings.model.rolling_window)) + 3,
    )
    lookback_seconds = required_points * int(metadata.get("step_seconds", settings.prometheus.step_seconds))
    start_time = now - timedelta(seconds=lookback_seconds)

    client = PrometheusClient(settings.prometheus)

    # 1) 가장 최근의 원본 메트릭을 가져온다.
    raw_frame = build_range_frame(
        client=client,
        metrics=contract.metrics,
        start=start_time,
        end=now,
        step_seconds=int(metadata.get("step_seconds", settings.prometheus.step_seconds)),
    )

    # 2) trainer가 사용했던 것과 같은 형태의 feature를 다시 만든다.
    feature_frame = engineer_features(raw_frame, int(metadata.get("rolling_window", settings.model.rolling_window)))
    latest = feature_frame.tail(1)
    if latest.empty:
        raise ValueError("No features available for detection")

    # 3) scaler와 모델에 넣기 전에 컬럼 순서를 학습 때와 맞춘다.
    aligned = align_feature_frame(latest, metadata["feature_columns"])
    scaled = scaler.transform(aligned)
    score = float(model.decision_function(scaled)[0])
    threshold = float(metadata["threshold"])
    is_anomaly = score >= threshold + settings.detection.alert_score_margin

    # 사람이 보기 쉽게 어떤 feature가 가장 많이 변했는지 함께 계산한다.
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
        # 점수가 학습된 기준값을 넘었을 때만 Alertmanager로 알린다.
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







