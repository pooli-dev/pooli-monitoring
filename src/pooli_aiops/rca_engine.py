from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .alerting import AlertmanagerClient
from .config import AppSettings


@dataclass
class AnomalyEvent:
    id: int
    timestamp: datetime
    application: str
    instance: str
    source: str  # e.g., "baseline" or "ecod"
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
            conn.execute('''
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
            ''')
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
            conn.execute('''
                INSERT INTO anomaly_events 
                (timestamp, application, instance, source, severity, metric, score, description)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                timestamp.isoformat(), application, instance,
                source, severity, metric, score, description
            ))
            conn.commit()

    def get_unprocessed_events(self) -> list[AnomalyEvent]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute('SELECT * FROM anomaly_events WHERE processed = 0 ORDER BY timestamp ASC')
            
            events = []
            for row in cursor.fetchall():
                events.append(AnomalyEvent(
                    id=row['id'],
                    timestamp=datetime.fromisoformat(row['timestamp']),
                    application=row['application'],
                    instance=row['instance'],
                    source=row['source'],
                    severity=row['severity'],
                    metric=row['metric'],
                    score=row['score'],
                    description=row['description'],
                    processed=bool(row['processed'])
                ))
            return events

    def mark_processed(self, event_ids: list[int]) -> None:
        if not event_ids:
            return
        with sqlite3.connect(self.db_path) as conn:
            placeholders = ','.join('?' * len(event_ids))
            conn.execute(f'''
                UPDATE anomaly_events SET processed = 1 WHERE id IN ({placeholders})
            ''', event_ids)
            conn.commit()


class RcaEngine:
    def __init__(self, settings: AppSettings):
        self.settings = settings
        self.store = EventStore(settings.artifacts.rca_db_path)
        self.alert_client = AlertmanagerClient(settings.alertmanager)

        # Simple topology rules: lower number means closer to the root infrastructure
        self.topology_ranks = {
            "OS": 1,        # CPU, Memory, Disk
            "DB": 2,        # Database
            "Redis": 3,     # Cache / Queue
            "WAS": 4,       # Application Server
            "Client": 5     # Traffic
        }
    
    def _determine_component_type(self, application: str, metric: str) -> str:
        # Heuristic mapping
        if "cpu" in metric.lower() or "disk" in metric.lower() or "memory" in metric.lower():
            return "OS"
        if application.lower() == "db" or "db_" in metric.lower():
            return "DB"
        if application.lower() == "redis" or "stream" in metric.lower():
            return "Redis"
        return "WAS"

    def run_rca(self, dry_run: bool = False, time_window_minutes: int = 5) -> dict[str, Any]:
        """Group unprocessed events, determine root cause, and send aggregated alert."""
        events = self.store.get_unprocessed_events()
        if not events:
            return {"status": "no_events", "processed_count": 0}

        # Group by time window using a simple greedy approach
        event_groups: list[list[AnomalyEvent]] = []
        
        current_group = []
        group_start = None

        for event in events:
            if group_start is None:
                group_start = event.timestamp
                current_group.append(event)
            else:
                if event.timestamp - group_start <= timedelta(minutes=time_window_minutes):
                    current_group.append(event)
                else:
                    event_groups.append(current_group)
                    current_group = [event]
                    group_start = event.timestamp
        if current_group:
            event_groups.append(current_group)

        
        processed_ids = []
        alerts_sent = 0

        for group in event_groups:
            processed_ids.extend([e.id for e in group])
            if len(group) == 0:
                continue
                
            # If only one event, just send it directly (skip RCA)
            if len(group) == 1:
                e = group[0]
                if not dry_run:
                    self.alert_client.send_anomaly(
                        labels={
                            "service": e.application,
                            "severity": e.severity,
                            "detector": e.source,
                            "metric": e.metric,
                            "instance": e.instance
                        },
                        annotations={
                            "summary": f"[{e.severity}] {e.application} 이상 탐지 ({e.metric})",
                            "description": e.description
                        }
                    )
                alerts_sent += 1
                continue

            # Perform RCA scoring for the group
            scored_events = []
            for e in group:
                comp_type = self._determine_component_type(e.application, e.metric)
                rank = self.topology_ranks.get(comp_type, 100)
                
                # Rule: lower rank (closer to OS/DB) gets higher root cause score.
                # Adding the raw anomaly score as a tie-breaker.
                # Score = (100 - rank) * 1000 + anomaly_score
                rca_score = (100 - rank) * 1000 + e.score
                scored_events.append((rca_score, e, comp_type))
            
            # Sort by RCA score descending
            scored_events.sort(key=lambda x: x[0], reverse=True)
            root_cause_tuple = scored_events[0]
            root_event = root_cause_tuple[1]
            root_comp_type = root_cause_tuple[2]

            # Format the RCA Alert
            impacted_apps = set([e.application for e in group])
            
            description = (
                f"🚨 RCA 분석 결과: {root_comp_type} 병목/장애 의심\n\n"
                f"📌 가장 유력한 Root Cause: {root_event.application} ({root_event.instance})\n"
                f"- 핵심 지표: {root_event.metric}\n"
                f"- 상세 내용: {root_event.description}\n\n"
                f"🔍 동시간대 연관 경보 ({len(group)}건):\n"
            )
            
            for i, (_, e, c_type) in enumerate(scored_events):
                description += f"  {i+1}. [{c_type}] {e.application} - {e.metric} (탐지: {e.source})\n"

            if not dry_run:
                self.alert_client.send_anomaly(
                    labels={
                        "service": "rca-engine",
                        "severity": "critical" if any(e.severity == "critical" for e in group) else "warning",
                        "detector": "rca",
                        "root_cause_app": root_event.application,
                        "impacted_apps": ",".join(impacted_apps)
                    },
                    annotations={
                        "summary": f"[RCA] 서버 연쇄 이상 탐지 (의심: {root_comp_type} 병목)",
                        "description": description
                    }
                )
            alerts_sent += 1

        if not dry_run:
            self.store.mark_processed(processed_ids)

        return {
            "status": "success",
            "processed_count": len(processed_ids),
            "alerts_evaluated_groups": len(event_groups),
            "alerts_sent": alerts_sent
        }
