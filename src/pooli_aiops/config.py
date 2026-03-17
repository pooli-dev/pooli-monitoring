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
    rca_db_path: str | None = None


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
    prometheus: PrometheusSettings
    alertmanager: AlertmanagerSettings = field(default_factory=AlertmanagerSettings)
    artifacts: ArtifactSettings = field(default_factory=ArtifactSettings)
    model: ModelSettings = field(default_factory=ModelSettings)
    detection: DetectionSettings = field(default_factory=DetectionSettings)

    @property
    def artifact_dir(self) -> Path:
        return Path(self.artifacts.dir)

    @property
    def rca_db_path(self) -> Path:
        if self.artifacts.rca_db_path:
            return Path(self.artifacts.rca_db_path)
        return self.artifact_dir / "rca_events.db"


def _read_yaml(path: Path) -> dict[str, Any]:
    """Read a YAML file and return an empty dict when it is blank."""
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def load_settings(path: str | Path) -> AppSettings:
    """Convert raw YAML settings into typed application settings."""
    config_path = Path(path)
    raw = _read_yaml(config_path)

    return AppSettings(
        prometheus=PrometheusSettings(**raw["prometheus"]),
        alertmanager=AlertmanagerSettings(**raw.get("alertmanager", {})),
        artifacts=ArtifactSettings(**raw.get("artifacts", {})),
        model=ModelSettings(**raw.get("model", {})),
        detection=DetectionSettings(**raw.get("detection", {})),
    )
