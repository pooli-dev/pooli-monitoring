from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd
import requests

from .config import PrometheusSettings
from .contracts import MetricContract


class PrometheusClient:
    def __init__(self, settings: PrometheusSettings) -> None:
        self.base_url = settings.base_url.rstrip("/")
        self.timeout_seconds = settings.timeout_seconds

    def _request(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        """Prometheus를 호출하고 응답 중 data 부분만 반환한다."""
        response = requests.get(
            f"{self.base_url}{endpoint}",
            params=params,
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("status") != "success":
            raise ValueError(f"Prometheus query failed: {payload}")
        return payload["data"]

    def query_range(
        self,
        promql: str,
        start: datetime,
        end: datetime,
        step_seconds: int,
    ) -> list[dict[str, Any]]:
        """단일 시점이 아니라 시계열을 얻기 위해 range query를 실행한다."""
        data = self._request(
            "/api/v1/query_range",
            {
                "query": promql,
                "start": start.timestamp(),
                "end": end.timestamp(),
                "step": f"{step_seconds}s",
            },
        )
        if data.get("resultType") != "matrix":
            raise ValueError(f"Expected matrix result, got {data.get('resultType')}")
        return data.get("result", [])


def _metric_column_name(metric: MetricContract, labels: dict[str, str]) -> str:
    """메트릭 라벨로부터 읽기 쉬운 DataFrame 컬럼 이름을 만든다."""
    visible = {key: value for key, value in labels.items() if key != "__name__"}
    if metric.labels:
        # 모델 입력에 의도적으로 쓰려는 라벨만 남긴다.
        visible = {key: visible[key] for key in metric.labels if key in visible}
    if not visible:
        return metric.key
    suffix = ",".join(f"{key}={visible[key]}" for key in sorted(visible))
    return f"{metric.key}[{suffix}]"


def _matrix_to_frame(metric: MetricContract, result: list[dict[str, Any]]) -> pd.DataFrame:
    """Prometheus의 matrix JSON 결과를 pandas DataFrame으로 바꾼다."""
    series_list: list[pd.Series] = []
    for item in result:
        column_name = _metric_column_name(metric, item.get("metric", {}))
        timestamps = [float(entry[0]) for entry in item.get("values", [])]
        values = [entry[1] for entry in item.get("values", [])]
        index = pd.to_datetime(timestamps, unit="s", utc=True)
        series = pd.Series(pd.to_numeric(values, errors="coerce"), index=index, name=column_name)
        series_list.append(series)

    if not series_list:
        return pd.DataFrame()

    return pd.concat(series_list, axis=1)


def build_range_frame(
    client: PrometheusClient,
    metrics: list[MetricContract],
    start: datetime,
    end: datetime,
    step_seconds: int,
) -> pd.DataFrame:
    """계약에 있는 모든 메트릭을 조회해 하나의 시계열 표로 합친다."""
    frames: list[pd.DataFrame] = []
    for metric in metrics:
        result = client.query_range(metric.promql, start, end, step_seconds)
        frame = _matrix_to_frame(metric, result)
        if not frame.empty:
            frames.append(frame)

    if not frames:
        raise ValueError("No metric data returned from Prometheus")

    dataset = pd.concat(frames, axis=1).sort_index()
    return dataset.loc[:, ~dataset.columns.duplicated()].sort_index()






