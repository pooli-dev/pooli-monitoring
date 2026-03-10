from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import requests

from .config import AlertmanagerSettings


class AlertmanagerClient:
    def __init__(self, settings: AlertmanagerSettings) -> None:
        self.settings = settings

    def send_anomaly(self, labels: dict[str, str], annotations: dict[str, str]) -> None:
        """알림 전송이 활성화된 경우 Alertmanager로 이상 징후를 1건 전송한다."""
        if not self.settings.enabled:
            return

        # Alertmanager는 알림을 1건만 보내도 리스트 형태의 payload를 기대한다.
        payload: list[dict[str, Any]] = [
            {
                "labels": {
                    **self.settings.default_labels,
                    "alertname": "PooliAiopsAnomaly",
                    **labels,
                },
                "annotations": annotations,
                "startsAt": datetime.now(timezone.utc).isoformat(),
            }
        ]

        response = requests.post(
            f"{self.settings.base_url.rstrip('/')}{self.settings.route}",
            json=payload,
            timeout=10,
        )
        response.raise_for_status()


