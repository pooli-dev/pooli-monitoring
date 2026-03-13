from __future__ import annotations

import numpy as np
import pandas as pd


def clean_raw_frame(raw_frame: pd.DataFrame) -> pd.DataFrame:
    """원본 메트릭 데이터를 모델이 쓰기 쉬운 형태로 정리한다."""
    if raw_frame.empty:
        return raw_frame

    cleaned = raw_frame.copy().sort_index()
    cleaned = cleaned.apply(pd.to_numeric, errors="coerce")
    cleaned = cleaned.replace([np.inf, -np.inf], np.nan)

    # 모니터링 데이터는 중간에 비는 경우가 많아서 먼저 가까운 값으로 채운다.
    cleaned = cleaned.ffill().bfill()
    cleaned = cleaned.dropna(axis=1, how="all")
    return cleaned


def engineer_features(raw_frame: pd.DataFrame, rolling_window: int) -> pd.DataFrame:
    """PyOD는 숫자 행 단위 입력을 받기 때문에 표 형태 feature를 만든다."""
    cleaned = clean_raw_frame(raw_frame)
    if cleaned.empty:
        return cleaned

    features = cleaned.copy()

    for column in cleaned.columns:
        series = cleaned[column]

        # 원본 값뿐 아니라 최근 변화량도 함께 feature로 넣는다.
        features[f"{column}__delta1"] = series.diff()
        pct_change = series.pct_change(fill_method=None)
        features[f"{column}__pct_change1"] = pct_change.replace([np.inf, -np.inf], np.nan)

        # rolling 평균과 표준편차는 복잡한 시계열 모델 없이도 짧은 문맥을 제공한다.
        # 그래서 최근 흐름을 같이 보게 해준다.
        features[f"{column}__roll_mean"] = series.rolling(
            window=rolling_window,
            min_periods=rolling_window,
        ).mean()
        features[f"{column}__roll_std"] = series.rolling(
            window=rolling_window,
            min_periods=rolling_window,
        ).std(ddof=0)

    features = features.replace([np.inf, -np.inf], np.nan).dropna()
    return features






