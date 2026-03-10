from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import load_settings
from .contracts import load_contract
from .detector import run_detection
from .trainer import run_training


def _parse_datetime(value: str | None) -> datetime | None:
    """CLI에서 받은 시간 문자열을 UTC 기준 datetime으로 바꾼다."""
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _common_parser(parser: argparse.ArgumentParser) -> None:
    """train, detect 두 명령이 공통으로 쓰는 인자를 추가한다."""
    parser.add_argument(
        "--settings",
        type=Path,
        required=True,
        help="Path to settings YAML",
    )
    parser.add_argument(
        "--contract",
        type=Path,
        required=True,
        help="Path to metric contract YAML",
    )


def build_parser() -> argparse.ArgumentParser:
    """CLI에서 사용할 train, detect 명령 구성을 정의한다."""
    parser = argparse.ArgumentParser(description="PyOD-based AIOps CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    train_parser = subparsers.add_parser("train", help="Train a model from Prometheus data")
    _common_parser(train_parser)
    train_parser.add_argument("--start", help="UTC start time in ISO-8601 format")
    train_parser.add_argument("--end", help="UTC end time in ISO-8601 format")

    detect_parser = subparsers.add_parser("detect", help="Detect anomalies from latest metrics")
    _common_parser(detect_parser)
    detect_parser.add_argument("--time", help="UTC detection time in ISO-8601 format")
    detect_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip sending alerts even if anomaly is detected",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    """설정을 읽고 선택한 파이프라인을 실행한 뒤 JSON 결과를 출력한다."""
    parser = build_parser()
    args = parser.parse_args(argv)

    # 두 명령은 같은 두 개의 YAML 파일을 사용한다:
    # - settings: 실행 환경 주소와 모델 옵션
    # - contract: 논리 메트릭 이름과 PromQL 매핑
    settings = load_settings(args.settings)
    contract = load_contract(args.contract)

    if args.command == "train":
        # 학습은 더 넓은 과거 구간을 읽고 모델 산출물을 디스크에 저장한다.
        result: dict[str, Any] = run_training(
            settings=settings,
            contract=contract,
            start=_parse_datetime(args.start),
            end=_parse_datetime(args.end),
        )
    else:
        # 탐지는 저장된 모델을 불러와 가장 최근 구간만 점수화한다.
        result = run_detection(
            settings=settings,
            contract=contract,
            dry_run=args.dry_run,
            timestamp=_parse_datetime(args.time),
        )

    print(json.dumps(result, indent=2))
    return 0









