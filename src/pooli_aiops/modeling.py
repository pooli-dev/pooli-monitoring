from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from pyod.models.ecod import ECOD
from pyod.models.hbos import HBOS
from pyod.models.iforest import IForest
from sklearn.preprocessing import RobustScaler


def build_model(name: str, contamination: float):
    """지원하는 가벼운 PyOD 모델 중 하나를 생성한다."""
    normalized = name.lower()
    if normalized == "ecod":
        return ECOD(contamination=contamination)
    if normalized == "hbos":
        return HBOS(contamination=contamination)
    if normalized == "iforest":
        return IForest(contamination=contamination, random_state=42)
    raise ValueError(f"Unsupported model: {name}")


def fit_model(
    feature_frame: pd.DataFrame,
    model_name: str,
    contamination: float,
) -> tuple[Any, RobustScaler, dict[str, Any]]:
    """학습 데이터를 스케일링하고 모델을 학습한 뒤 탐지에 필요한 메타데이터를 남긴다."""
    scaler = RobustScaler()
    feature_columns = list(feature_frame.columns)
    scaled = scaler.fit_transform(feature_frame[feature_columns])

    model = build_model(model_name, contamination)
    model.fit(scaled)

    # threshold는 이 점수 이상일 때 이상 징후로 보겠다는 학습 결과다.
    threshold = float(getattr(model, "threshold_", np.quantile(model.decision_scores_, 1 - contamination)))
    medians = {
        column: float(value)
        for column, value in feature_frame.median(numeric_only=True).to_dict().items()
    }

    metadata = {
        "model_name": model_name,
        "contamination": contamination,
        "threshold": threshold,
        "feature_columns": feature_columns,
        "feature_medians": medians,
    }
    return model, scaler, metadata


def save_artifacts(
    artifact_dir: str | Path,
    model: Any,
    scaler: RobustScaler,
    metadata: dict[str, Any],
) -> None:
    """학습된 모델 묶음을 나중에 재사용할 수 있게 디스크에 저장한다."""
    target_dir = Path(artifact_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    joblib.dump(model, target_dir / "model.joblib")
    joblib.dump(scaler, target_dir / "scaler.joblib")
    (target_dir / "metadata.json").write_text(
        json.dumps(metadata, indent=2),
        encoding="utf-8",
    )


def load_artifacts(artifact_dir: str | Path) -> tuple[Any, RobustScaler, dict[str, Any]]:
    """학습 때 저장한 모델 묶음을 다시 불러온다."""
    target_dir = Path(artifact_dir)
    model = joblib.load(target_dir / "model.joblib")
    scaler = joblib.load(target_dir / "scaler.joblib")
    metadata = json.loads((target_dir / "metadata.json").read_text(encoding="utf-8"))
    return model, scaler, metadata


def align_feature_frame(feature_frame: pd.DataFrame, expected_columns: list[str]) -> pd.DataFrame:
    """탐지용 feature의 컬럼 순서를 학습 때와 정확히 맞춘다."""
    missing = [column for column in expected_columns if column not in feature_frame.columns]
    if missing:
        raise ValueError(f"Missing expected feature columns: {missing}")
    return feature_frame.reindex(columns=expected_columns)


def top_deviations(
    latest_row: pd.Series,
    feature_medians: dict[str, float],
    limit: int = 5,
) -> list[dict[str, float | str]]:
    """학습 시 중앙값에서 가장 멀리 벗어난 feature를 보여준다."""
    deviations: list[dict[str, float | str]] = []
    for column, value in latest_row.items():
        if column not in feature_medians:
            continue
        median = feature_medians[column]
        deviations.append(
            {
                "feature": column,
                "value": float(value),
                "median": float(median),
                "distance": abs(float(value) - float(median)),
            }
        )

    return sorted(deviations, key=lambda item: item["distance"], reverse=True)[:limit]







