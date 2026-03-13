from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(slots=True)
class MetricContract:
    # metrics_contract.yaml에 있는 논리 메트릭 1건의 정의다.
    key: str
    promql: str
    description: str = ""
    required: bool = True
    unit: str = ""
    aggregation: str = "service"
    window: str = "5m"
    labels: list[str] = field(default_factory=list)


@dataclass(slots=True)
class LabelsContract:
    required: list[str] = field(default_factory=list)
    recommended: list[str] = field(default_factory=list)
    forbidden: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ContractFile:
    version: int
    application: str
    metrics: list[MetricContract]
    labels: LabelsContract = field(default_factory=LabelsContract)


def _read_yaml(path: Path) -> dict[str, Any]:
    """메트릭 계약 YAML 파일을 읽는다."""
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def load_contract(path: str | Path) -> ContractFile:
    """trainer와 detector가 메트릭 정의를 순회할 수 있게 계약 파일을 파싱한다."""
    contract_path = Path(path)
    raw = _read_yaml(contract_path)

    metrics = [MetricContract(**entry) for entry in raw.get("metrics", [])]
    labels = LabelsContract(**raw.get("labels", {}))

    return ContractFile(
        version=raw.get("version", 1),
        application=raw["application"],
        metrics=metrics,
        labels=labels,
    )



