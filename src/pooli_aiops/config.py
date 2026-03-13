from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(slots=True)
class PrometheusSettings:
    base_url: str
    timeout_seconds: int = 15
    step_seconds: int = 300


@dataclass(slots=True)
class AlertmanagerSettings:
    enabled: bool = False
    base_url: str = "http://localhost:9093"
    route: str = "/api/v2/alerts"
    default_labels: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class ArtifactSettings:
    dir: str = "artifacts"


@dataclass(slots=True)
class ModelSettings:
    name: str = "ecod"
    contamination: float = 0.01
    rolling_window: int = 3
    history_hours: int = 24
    min_training_rows: int = 50


@dataclass(slots=True)
class DetectionSettings:
    lookback_points: int = 8
    alert_score_margin: float = 0.0


@dataclass(slots=True)
class AppSettings:
    # settings.yaml의 각 섹션을 한곳에 모아 다루는 최상위 설정 객체다.
    prometheus: PrometheusSettings
    alertmanager: AlertmanagerSettings = field(default_factory=AlertmanagerSettings)
    artifacts: ArtifactSettings = field(default_factory=ArtifactSettings)
    model: ModelSettings = field(default_factory=ModelSettings)
    detection: DetectionSettings = field(default_factory=DetectionSettings)

    @property
    def artifact_dir(self) -> Path:
        return Path(self.artifacts.dir)


def _read_yaml(path: Path) -> dict[str, Any]:
    """YAML 파일을 읽고 비어 있으면 빈 dict를 반환한다."""
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def load_settings(path: str | Path) -> AppSettings:
    """YAML 원본 데이터를 코드에서 쓰기 쉬운 설정 객체로 변환한다."""
    config_path = Path(path)
    raw = _read_yaml(config_path)

    # settings.yaml의 각 섹션은 작은 dataclass 객체로 바뀐다.
    # 이렇게 하면 나머지 코드에서 raw dict보다 읽기 쉽게 접근할 수 있다.
    prometheus = PrometheusSettings(**raw["prometheus"])
    alertmanager = AlertmanagerSettings(**raw.get("alertmanager", {}))
    artifacts = ArtifactSettings(**raw.get("artifacts", {}))
    model = ModelSettings(**raw.get("model", {}))
    detection = DetectionSettings(**raw.get("detection", {}))

    return AppSettings(
        prometheus=prometheus,
        alertmanager=alertmanager,
        artifacts=artifacts,
        model=model,
        detection=detection,
    )





