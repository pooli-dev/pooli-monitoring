# pooli-monitoring

`pooli-monitoring` 은 Prometheus 메트릭을 수집해 학습하고, 최신 구간을 이상 탐지한 뒤, 필요하면 Alertmanager로 알림을 보내고 RCA 후보까지 묶어보는 PyOD 기반 AIOps runner 입니다.

예제 설정은 `pooli-be` 기준이지만, 메트릭 계약 파일과 설정 파일을 바꾸면 다른 서비스에도 같은 흐름을 적용할 수 있습니다.

## 핵심 흐름

이 저장소는 아래 순서로 동작합니다.

1. Prometheus 에서 메트릭을 읽습니다.
2. `metrics_contract.yaml` 로 어떤 메트릭을 어떤 PromQL 로 볼지 정의합니다.
3. `train` 으로 정상 구간을 학습해 모델 아티팩트를 생성합니다.
4. `detect` 또는 `baseline-detect` 로 최신 메트릭 구간을 평가합니다.
5. 이상이 감지되면 Alertmanager 전송과 RCA 이벤트 저장을 수행합니다.
6. `rca` 가 저장된 이벤트를 시간창 기준으로 묶어 유력 원인 계층을 추정합니다.

실제 CLI 진입점은 다음 5개입니다.

- `train`
- `detect`
- `baseline-detect`
- `rca`
- `generate-dataset`

실행 방식은 두 가지를 모두 지원합니다.

- `pooli-aiops ...`
- `python -m pooli_aiops ...`

## 프로젝트 구조

README 관점에서는 이 저장소를 네 축으로 보면 가장 이해가 쉽습니다.

### `src/pooli_aiops`

실행 로직과 CLI 본체가 들어 있는 패키지입니다.

- `cli.py`, `__main__.py`
  진입점과 서브커맨드 정의
- `config.py`, `contracts.py`
  설정 파일과 메트릭 계약 파일 로딩
- `prometheus_client.py`, `alerting.py`
  Prometheus 조회와 Alertmanager 전송
- `features.py`, `modeling.py`
  feature engineering, 모델 학습, 아티팩트 저장
- `trainer.py`
  Prometheus 또는 CSV 기반 학습
- `detector.py`
  최신 메트릭 구간 이상 탐지
- `baseline.py`
  단일 인프라 메트릭 baseline 감지
- `rca_engine.py`
  이벤트 저장과 RCA 추정
- `synthetic_data.py`
  synthetic dataset 생성

### `config`

실행 설정과 감지 계약을 담는 디렉터리입니다.

- `settings*.yaml`
  Prometheus, Alertmanager, model, detection, artifacts 설정
- `metrics_contract*.yaml`
  서비스별 메트릭 키와 PromQL 계약
- `baseline_rules.yaml`
  CPU, memory, disk, MySQL 같은 단일 메트릭 baseline 감지 규칙

### `deploy`

운영 배포와 모니터링 스택 예제가 들어 있습니다.

- `deploy/monitoring`
  Prometheus, Alertmanager, Loki, Promtail, Grafana 예제 구성
- `deploy/systemd`
  주기 실행용 service 와 timer 유닛

### `artifacts`

실행 결과가 쌓이는 위치입니다.

- 학습 후 `model.joblib`, `scaler.joblib`, `metadata.json`
- RCA 이벤트 DB
- synthetic dataset CSV 와 metadata 출력

## 빠른 시작

### 1. 환경 준비

이 프로젝트는 Python `>=3.11` 을 요구합니다.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

Windows PowerShell 환경이라면 활성화만 다음처럼 바꾸면 됩니다.

```powershell
.venv\Scripts\Activate.ps1
```

### 2. 설정 파일 준비

예제 설정을 복사해 실제 실행 파일을 만듭니다.

```bash
cp config/settings.example.yaml config/settings.yaml
cp config/metrics_contract.example.yaml config/metrics_contract.yaml
```

Windows 에서는 `cp` 대신 `copy` 를 사용하면 됩니다.

먼저 확인할 항목:

- `config/settings.yaml`
  Prometheus 주소, Alertmanager 주소, 아티팩트 위치, 모델 파라미터
- `config/metrics_contract.yaml`
  대상 서비스 이름, 메트릭 키, PromQL, 라벨 규칙

### 3. 첫 학습

```bash
python -m pooli_aiops train \
  --settings config/settings.yaml \
  --contract config/metrics_contract.yaml
```

성공하면 `artifacts/` 아래에 모델 아티팩트가 생성됩니다.

### 4. 첫 탐지

```bash
python -m pooli_aiops detect \
  --settings config/settings.yaml \
  --contract config/metrics_contract.yaml \
  --dry-run
```

`--dry-run` 을 제거하면 이상 탐지 시 Alertmanager 전송까지 수행합니다.

## 명령별 사용법

### `train`

Prometheus 히스토리로 학습:

```bash
python -m pooli_aiops train \
  --settings config/settings.yaml \
  --contract config/metrics_contract.yaml
```

CSV 로 학습:

```bash
python -m pooli_aiops train \
  --settings config/settings.yaml \
  --input-csv artifacts/synthetic_dataset/synthetic_raw_timeseries.csv \
  --timestamp-column timestamp \
  --metric-columns traffic_stream_length,traffic_stream_pending_message,traffic_stream_requests_tps \
  --application traffic-generator
```

주요 옵션:

- `--settings`: 설정 YAML 경로
- `--contract`: Prometheus 학습 시 사용할 계약 파일
- `--input-csv`: CSV 입력 파일
- `--start`, `--end`: UTC ISO-8601 기준 학습 구간
- `--row-filter-column`, `--row-filter-value`: CSV 행 필터링

### `detect`

```bash
python -m pooli_aiops detect \
  --settings config/settings.yaml \
  --contract config/metrics_contract.yaml \
  --dry-run
```

주요 옵션:

- `--settings`: 설정 YAML 경로
- `--contract`: 메트릭 계약 파일
- `--time`: 탐지 시각
- `--dry-run`: 알림 전송 없이 판정만 수행

### `baseline-detect`

```bash
python -m pooli_aiops baseline-detect \
  --settings config/settings.yaml \
  --rules config/baseline_rules.yaml \
  --dry-run
```

이 명령은 서비스 트래픽 모델보다 `node-exporter`, `mysql` 같은 인프라 메트릭 baseline 감지에 맞춰져 있습니다.

주요 옵션:

- `--settings`: 설정 YAML 경로
- `--rules`: baseline 규칙 파일
- `--time`: 탐지 시각
- `--dry-run`: 알림 전송 없이 판정만 수행

### `rca`

```bash
python -m pooli_aiops rca \
  --settings config/settings.yaml \
  --dry-run \
  --time-window-minutes 5
```

`detect` 와 `baseline-detect` 가 저장한 이벤트를 묶어서 OS, DB, Redis, WAS, Client 계층 중 어디가 유력한지 추정합니다.

주요 옵션:

- `--settings`: 설정 YAML 경로
- `--dry-run`: RCA 알림 전송 없이 분석만 수행
- `--time-window-minutes`: 이벤트 묶음 시간 창

### `generate-dataset`

```bash
python -m pooli_aiops generate-dataset \
  --output-dir artifacts/synthetic_dataset \
  --hours 24 \
  --freq-seconds 1 \
  --future-seconds 60 \
  --rolling-window 30
```

출력물:

- `synthetic_raw_timeseries.csv`
- `synthetic_feature_dataset.csv`
- `synthetic_anomaly_train.csv`
- `synthetic_anomaly_eval.csv`
- `synthetic_events.csv`
- `metadata.json`

주요 옵션:

- `--output-dir`: 출력 디렉터리
- `--hours`: 시계열 길이
- `--freq-seconds`: 샘플링 주기
- `--future-seconds`: 미래 라벨 시점
- `--rolling-window`: feature engineering window
- `--seed`: 난수 시드

## 설정 파일 역할

### `config/settings*.yaml`

애플리케이션 전체 동작을 제어합니다.

- `prometheus`: base URL, timeout, step
- `alertmanager`: 알림 전송 여부와 대상 주소
- `artifacts`: 모델과 RCA DB 저장 위치
- `model`: 모델 이름, contamination, rolling window, history hours
- `detection`: lookback 길이와 알림 점수 마진

대표 파일:

- `config/settings.example.yaml`
- `config/settings.yaml`
- `config/settings.traffic_db.yaml`

### `config/metrics_contract*.yaml`

Prometheus 에서 어떤 메트릭을 어떤 PromQL 로 읽을지 정의합니다.

- `application`: 대상 서비스 이름
- `metrics[*].key`: 코드 내부 식별자
- `metrics[*].promql`: 실제 Prometheus 쿼리
- `metrics[*].required`: 필수 메트릭 여부
- `labels`: 필수, 권장, 금지 라벨 규칙

대표 파일:

- `config/metrics_contract.example.yaml`
- `config/metrics_contract.yaml`
- `config/metrics_contract.traffic_db.yaml`
- `config/metrics_contract.traffic_db_query.yaml`
- `config/metrics_contract.was_db.yaml`

### `config/baseline_rules.yaml`

인프라용 baseline 감지 규칙을 정의합니다.

- 감시 대상 메트릭 목록
- 방향성(`high`, `low`)
- warn/critical 기준값
- baseline 계산 파라미터
- 인스턴스 기준 라벨

## 배포 개요

배포 자산은 핵심 코드보다 뒤쪽 보조 섹션으로 보면 됩니다.

### `deploy/systemd`

주기 실행용 유닛이 들어 있습니다.

- `pooli-traffic-detect.service` / `.timer`
- `pooli-baseline-detect.service` / `.timer`
- `pooli-rca.service` / `.timer`

운영 환경에서는 `/opt/pooli-monitoring` 기준 경로에 맞춰 배포해서 사용하는 형태입니다.

### `deploy/monitoring`

예제 모니터링 스택이 들어 있습니다.

- Prometheus
- Alertmanager
- Loki
- Promtail
- Grafana

즉, 이 저장소는 AIOps runner 코드만 있는 것이 아니라, 대시보드와 알림 구성을 함께 실험할 수 있는 샘플 스택도 제공합니다.

## 대시보드 요약

Grafana 대시보드는 `deploy/monitoring/grafana/dashboards/` 아래에 포함되어 있습니다.

- `pooli/api-endpoint-dashboard.json`: API 요청량, 오류율, 지연 시간을 보는 대시보드
- `pooli/db-query-dashboard.json`: DB 쿼리 병목과 지연을 보는 대시보드
- `pooli/pooli-infrastructure-overview-dashboard.json`: 인프라 상태를 상위 수준에서 보는 대시보드

README 에서는 사용 목적만 요약하고, 패널별 상세 해석은 실제 Grafana 화면과 JSON 구성을 기준으로 보는 편이 맞습니다.

## 한계와 전제

- 기본 데이터 소스는 Prometheus 입니다.
- 기본 모델은 `ECOD`, `HBOS`, `IForest` 같은 가벼운 PyOD 계열입니다.
- 충분한 학습 히스토리가 없으면 `train` 은 실패할 수 있습니다.
- `detect` 와 `baseline-detect` 의 결과는 RCA 이벤트 저장소에 누적됩니다.
- `rca` 결과는 확정 진단이 아니라 상관관계 기반 추정입니다.
- 운영 초기에는 트래픽 모델보다 `baseline-detect` 를 먼저 안정화하는 편이 더 현실적일 수 있습니다.
