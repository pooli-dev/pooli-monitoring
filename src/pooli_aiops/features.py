from __future__ import annotations

import numpy as np
import pandas as pd


def clean_raw_frame(raw_frame: pd.DataFrame) -> pd.DataFrame:
    """feature 생성 전에 원본 메트릭 프레임을 정리한다."""
    if raw_frame.empty:
        return raw_frame

    cleaned = raw_frame.copy().sort_index()
    cleaned = cleaned.apply(pd.to_numeric, errors="coerce")
    cleaned = cleaned.replace([np.inf, -np.inf], np.nan)

    # Prometheus range 결과는 중간중간 값이 비는 경우가 있어서 rolling 계산 전에 채운다.
    cleaned = cleaned.ffill().bfill()
    cleaned = cleaned.dropna(axis=1, how="all")
    return cleaned


def engineer_features(raw_frame: pd.DataFrame, rolling_window: int) -> pd.DataFrame:
    """학습 때와 동일한 구조의 feature 세트를 만든다."""
    cleaned = clean_raw_frame(raw_frame)
    if cleaned.empty:
        return cleaned

    features = cleaned.copy()
    rolling_columns: list[str] = []

    for column in cleaned.columns:
        series = cleaned[column]

        # 0으로 고정된 시계열은 pct_change 계산에서 NaN이 많이 생긴다.
        # 이런 값은 중립적인 0으로 처리해서 한 개의 정적인 metric 때문에
        # 전체 탐지 row가 사라지지 않게 한다.
        features[f"{column}__delta1"] = series.diff().fillna(0.0)
        features[f"{column}__pct_change1"] = (
            series.pct_change(fill_method=None)
            .replace([np.inf, -np.inf], np.nan)
            .fillna(0.0)
        )

        roll_mean_column = f"{column}__roll_mean"
        roll_std_column = f"{column}__roll_std"
        features[roll_mean_column] = series.rolling(
            window=rolling_window,
            min_periods=rolling_window,
        ).mean()
        features[roll_std_column] = series.rolling(
            window=rolling_window,
            min_periods=rolling_window,
        ).std(ddof=0)
        rolling_columns.extend([roll_mean_column, roll_std_column])

    features = features.replace([np.inf, -np.inf], np.nan)

    # rolling warm-up 구간만 제거하고, 나머지 희소한 feature 값은 0으로 채운다.
    if rolling_columns:
        features = features.dropna(subset=rolling_columns)

    return features.fillna(0.0)
