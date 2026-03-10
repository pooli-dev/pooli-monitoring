from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from .config import AppSettings
from .contracts import ContractFile
from .features import engineer_features
from .modeling import fit_model, save_artifacts
from .prometheus_client import PrometheusClient, build_range_frame


def run_training(
    settings: AppSettings,
    contract: ContractFile,
    start: datetime | None = None,
    end: datetime | None = None,
) -> dict[str, Any]:
    """과거 Prometheus 메트릭으로 이상 탐지 모델을 학습한다."""
    end_time = end or datetime.now(timezone.utc)
    start_time = start or end_time - timedelta(hours=settings.model.history_hours)

    client = PrometheusClient(settings.prometheus)

    # 1) Prometheus에서 원본 시계열 데이터를 읽는다.
    raw_frame = build_range_frame(
        client=client,
        metrics=contract.metrics,
        start=start_time,
        end=end_time,
        step_seconds=settings.prometheus.step_seconds,
    )

    # 2) 시계열 데이터를 PyOD가 학습할 수 있는 행 단위 feature로 바꾼다.
    feature_frame = engineer_features(raw_frame, settings.model.rolling_window)

    if len(feature_frame) < settings.model.min_training_rows:
        raise ValueError(
            "Not enough training rows after feature engineering. "
            f"Expected at least {settings.model.min_training_rows}, got {len(feature_frame)}"
        )

    # 3) 모델을 학습하고 사용한 feature 구성을 그대로 기억한다.
    model, scaler, metadata = fit_model(
        feature_frame=feature_frame,
        model_name=settings.model.name,
        contamination=settings.model.contamination,
    )
    metadata.update(
        {
            "application": contract.application,
            "trained_at": datetime.now(timezone.utc).isoformat(),
            "start": start_time.isoformat(),
            "end": end_time.isoformat(),
            "raw_columns": list(raw_frame.columns),
            "rolling_window": settings.model.rolling_window,
            "step_seconds": settings.prometheus.step_seconds,
            "training_rows": int(len(feature_frame)),
        }
    )

    # 4) detector가 나중에 재사용할 수 있게 모델 묶음을 저장한다.
    save_artifacts(settings.artifact_dir, model, scaler, metadata)

    return {
        "status": "trained",
        "application": contract.application,
        "artifact_dir": str(settings.artifact_dir),
        "training_rows": len(feature_frame),
        "feature_columns": len(feature_frame.columns),
        "start": start_time.isoformat(),
        "end": end_time.isoformat(),
        "model": settings.model.name,
    }





