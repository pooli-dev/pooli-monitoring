from __future__ import annotations

import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .alerting import AlertmanagerClient
from .config import AppSettings


@dataclass(slots=True)
class AnomalyEvent:
    id: int
    timestamp: datetime
    application: str
    instance: str
    source: str
    severity: str
    metric: str
    score: float
    description: str
    processed: bool = False


class EventStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                '''
                CREATE TABLE IF NOT EXISTS anomaly_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    application TEXT NOT NULL,
                    instance TEXT,
                    source TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    metric TEXT,
                    score REAL,
                    description TEXT,
                    processed BOOLEAN NOT NULL DEFAULT 0
                )
                '''
            )
            conn.commit()

    def add_event(
        self,
        timestamp: datetime,
        application: str,
        instance: str,
        source: str,
        severity: str,
        metric: str,
        score: float,
        description: str,
    ) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                '''
                INSERT INTO anomaly_events
                (timestamp, application, instance, source, severity, metric, score, description)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    timestamp.isoformat(),
                    application,
                    instance,
                    source,
                    severity,
                    metric,
                    score,
                    description,
                ),
            )
            conn.commit()

    def get_unprocessed_events(self) -> list[AnomalyEvent]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                'SELECT * FROM anomaly_events WHERE processed = 0 ORDER BY timestamp ASC'
            )

            events: list[AnomalyEvent] = []
            for row in cursor.fetchall():
                events.append(
                    AnomalyEvent(
                        id=row['id'],
                        timestamp=datetime.fromisoformat(row['timestamp']),
                        application=row['application'],
                        instance=row['instance'],
                        source=row['source'],
                        severity=row['severity'],
                        metric=row['metric'],
                        score=row['score'],
                        description=row['description'],
                        processed=bool(row['processed']),
                    )
                )
            return events

    def mark_processed(self, event_ids: list[int]) -> None:
        if not event_ids:
            return
        with sqlite3.connect(self.db_path) as conn:
            placeholders = ','.join('?' * len(event_ids))
            conn.execute(
                f'''UPDATE anomaly_events SET processed = 1 WHERE id IN ({placeholders})''',
                event_ids,
            )
            conn.commit()


class RcaEngine:
    def __init__(self, settings: AppSettings):
        self.settings = settings
        self.store = EventStore(settings.rca_db_path)
        self.alert_client = AlertmanagerClient(settings.alertmanager)
        self.topology_bonus = {
            'OS': 2.0,
            'DB': 1.5,
            'Redis': 1.0,
            'WAS': 0.5,
            'Client': 0.0,
        }

    def _determine_component_type(self, application: str, metric: str) -> str:
        metric_lower = (metric or '').lower()
        application_lower = application.lower()

        if any(keyword in metric_lower for keyword in ('cpu', 'disk', 'memory', 'load', 'iowait', 'filesystem')):
            return 'OS'
        if application_lower in {'db', 'mysql', 'mariadb'} or any(
            keyword in metric_lower
            for keyword in ('db_', 'mybatis', 'query', 'mysql', 'innodb', 'sql', 'connection')
        ):
            return 'DB'
        if application_lower == 'redis' or any(
            keyword in metric_lower
            for keyword in ('redis', 'stream', 'pending', 'enqueue', 'queue', 'consumer', 'lag')
        ):
            return 'Redis'
        if application_lower == 'client' or 'traffic' in metric_lower:
            return 'Client'
        return 'WAS'

    @staticmethod
    def _severity_weight(severity: str) -> float:
        return {
            'critical': 30.0,
            'warning': 10.0,
            'info': 2.0,
        }.get(severity.lower(), 0.0)

    def _event_rca_score(
        self,
        event: AnomalyEvent,
        component_type: str,
        component_counts: Counter[str],
        application_counts: Counter[str],
    ) -> float:
        score_magnitude = min(abs(float(event.score)), 100.0)
        component_support = max(component_counts[component_type] - 1, 0)
        application_support = max(application_counts[event.application] - 1, 0)
        return (
            score_magnitude * 10.0
            + self._severity_weight(event.severity)
            + component_support * 8.0
            + application_support * 4.0
            + self.topology_bonus.get(component_type, 0.0)
        )

    @staticmethod
    def _group_events(
        events: list[AnomalyEvent],
        time_window: timedelta,
    ) -> list[list[AnomalyEvent]]:
        if not events:
            return []

        groups: list[list[AnomalyEvent]] = [[events[0]]]
        for event in events[1:]:
            previous_event = groups[-1][-1]
            if event.timestamp - previous_event.timestamp <= time_window:
                groups[-1].append(event)
            else:
                groups.append([event])
        return groups

    @staticmethod
    def _format_timestamp(value: datetime) -> str:
        return value.astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')

    def _build_description(
        self,
        group: list[AnomalyEvent],
        scored_events: list[tuple[float, AnomalyEvent, str]],
        root_event: AnomalyEvent,
        root_component: str,
        component_counts: Counter[str],
    ) -> str:
        impacted_apps = sorted({event.application for event in group})
        window_start = self._format_timestamp(group[0].timestamp)
        window_end = self._format_timestamp(group[-1].timestamp)
        root_score = abs(float(root_event.score))

        lines = [
            f'추정 RCA 결과: {root_component} 계층 이상이 가장 유력합니다.',
            '',
            f'분석 구간: {window_start} ~ {window_end}',
            f'영향 범위: {", ".join(impacted_apps)}',
            f'유력 후보: {root_event.application} ({root_event.instance})',
            f'메트릭: {root_event.metric}',
            f'탐지 소스: {root_event.source}',
            f'이상 점수 크기: {root_score:.2f}',
            '',
            '판단 근거:',
            f'- 동일 시간 구간에 연관 이벤트 {len(group)}건이 함께 감지되었습니다.',
            f'- {root_component} 계층 이벤트가 {component_counts[root_component]}건으로 가장 많이 관측되었습니다.',
            f'- 대표 이벤트 설명: {root_event.description}',
            '',
            '같이 관측된 이벤트:',
        ]

        for index, (rca_score, event, component_type) in enumerate(scored_events, start=1):
            lines.append(
                f'{index}. [{component_type}] {event.application}/{event.instance} - {event.metric} '
                f'(source={event.source}, severity={event.severity}, score={abs(float(event.score)):.2f}, rca_score={rca_score:.1f})'
            )

        lines.extend(
            [
                '',
                '이 결과는 추정 RCA이며, DB slow query / lock / EXPLAIN 같은 추가 증거가 있으면 확정 RCA로 승격할 수 있습니다.',
            ]
        )
        return '\n'.join(lines)

    def run_rca(self, dry_run: bool = False, time_window_minutes: int = 5) -> dict[str, Any]:
        if time_window_minutes <= 0:
            raise ValueError('time_window_minutes must be positive')

        events = self.store.get_unprocessed_events()
        if not events:
            return {'status': 'no_events', 'processed_count': 0}

        event_groups = self._group_events(events, timedelta(minutes=time_window_minutes))
        processed_ids: list[int] = []
        alerts_sent = 0
        skipped_singletons = 0

        for group in event_groups:
            group_ids = [event.id for event in group]
            processed_ids.extend(group_ids)

            if len(group) <= 1:
                skipped_singletons += 1
                continue

            component_types = {
                event.id: self._determine_component_type(event.application, event.metric)
                for event in group
            }
            component_counts: Counter[str] = Counter(component_types.values())
            application_counts: Counter[str] = Counter(event.application for event in group)

            scored_events: list[tuple[float, AnomalyEvent, str]] = []
            for event in group:
                component_type = component_types[event.id]
                rca_score = self._event_rca_score(
                    event=event,
                    component_type=component_type,
                    component_counts=component_counts,
                    application_counts=application_counts,
                )
                scored_events.append((rca_score, event, component_type))

            scored_events.sort(key=lambda item: item[0], reverse=True)
            _, root_event, root_component = scored_events[0]
            impacted_apps = sorted({event.application for event in group})
            severity = 'critical' if any(event.severity == 'critical' for event in group) else 'warning'
            description = self._build_description(
                group=group,
                scored_events=scored_events,
                root_event=root_event,
                root_component=root_component,
                component_counts=component_counts,
            )

            if not dry_run:
                self.alert_client.send_anomaly(
                    labels={
                        'service': 'rca-engine',
                        'severity': severity,
                        'detector': 'rca',
                        'rca_state': 'suspected',
                        'root_cause_app': root_event.application,
                        'root_cause_component': root_component,
                        'impacted_apps': ','.join(impacted_apps),
                    },
                    annotations={
                        'summary': f'[AIOps][suspected] 연관 이상 징후 감지 (유력 후보: {root_event.application}/{root_component})',
                        'description': description,
                    },
                )
            alerts_sent += 1

        if not dry_run:
            self.store.mark_processed(processed_ids)

        return {
            'status': 'success',
            'processed_count': len(processed_ids),
            'alerts_evaluated_groups': len(event_groups),
            'alerts_sent': alerts_sent,
            'skipped_singletons': skipped_singletons,
        }
