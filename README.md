# pooli-monitoring

`pooli-monitoring` 은 Prometheus 메트릭을 기반으로 학습, 이상 탐지, 알림 전송, 간단한 RCA 추정을 수행하는 PyOD 기반 AIOps runner 입니다. 저장소 안에는 baseline 기반 인프라 감지 규칙과 Prometheus, Grafana, Loki, Alertmanager 샘플 스택도 함께 포함되어 있습니다.

## 프로젝트 소개

이 저장소는 `pooli-be` 같은 서비스를 대상으로 다음 흐름을 구성하는 데 초점을 둡니다.

- Prometheus 메트릭 수집
- 계약 파일(`metrics_contract.yaml`) 기반 학습 데이터 정의
- PyOD 모델 학습 및 아티팩트 저장
- 최신 메트릭 구간 이상 탐지
- Alertmanager 알림 전송
- 감지 이벤트 저장 및 RCA 후보 추정

예제 설정은 `pooli-be`를 기준으로 되어 있지만, 계약 파일과 설정 파일을 바꾸면 다른 서비스에도 같은 흐름을 적용할 수 있습니다.

## 핵심 기능과 구성

### 주요 CLI 명령

- `train`: Prometheus 히스토리 또는 CSV 파일로 모델을 학습합니다.
- `detect`: 최신 메트릭 구간을 읽어 이상 여부를 판정합니다.
- `baseline-detect`: 단일 인프라 메트릭에 baseline 기반 이상 감지를 적용합니다.
- `rca`: 저장된 이벤트를 시간 구간별로 묶어 유력 원인 계층을 추정합니다.
- `generate-dataset`: 분류, 이상 탐지, 예측 실험용 synthetic dataset을 생성합니다.

### 저장소 구조

```text
pooli-monitoring/
  config/
    baseline_rules.yaml
    metrics_contract.example.yaml
    settings.example.yaml
  deploy/
    monitoring/
    systemd/
  artifacts/
  src/pooli_aiops/
  pyproject.toml
  README.md
```

### 아티팩트와 데이터 흐름

- 학습 결과는 기본적으로 `artifacts/` 아래에 저장됩니다.
- 모델 학습 후 `model.joblib`, `scaler.joblib`, `metadata.json` 이 생성됩니다.
- RCA 이벤트 저장소는 설정에 따라 `artifacts/rca/rca_events.db` 같은 경로를 사용합니다.
- synthetic dataset 생성 시 기본 출력 경로는 `artifacts/synthetic_dataset/` 입니다.

## 빠른 시작

### 1. 환경 준비

이 프로젝트는 Python `>=3.11` 을 요구합니다.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

Windows 환경이라면 활성화 명령만 다음과 같이 바꾸면 됩니다.

```powershell
.venv\Scripts\Activate.ps1
```

### 2. 설정 파일 준비

예제 설정을 복사해서 실제 실행 파일을 만듭니다.

```bash
cp config/settings.example.yaml config/settings.yaml
cp config/metrics_contract.example.yaml config/metrics_contract.yaml
```

Windows 환경이라면 `cp` 대신 `copy` 를 사용하면 됩니다.

수정이 필요한 대표 항목은 다음과 같습니다.

- `config/settings.yaml`
  Prometheus 주소, Alertmanager 주소, 아티팩트 저장 위치, 모델 파라미터
- `config/metrics_contract.yaml`
  서비스 이름, 사용할 메트릭 키, PromQL, 라벨 규칙

### 3. 첫 학습 실행

```bash
python -m pooli_aiops train \
  --settings config/settings.yaml \
  --contract config/metrics_contract.yaml
```

학습이 끝나면 `artifacts/` 아래에 모델, 스케일러, 메타데이터가 저장됩니다.

### 4. 첫 탐지 실행

```bash
python -m pooli_aiops detect \
  --settings config/settings.yaml \
  --contract config/metrics_contract.yaml \
  --dry-run
```

`--dry-run` 을 제거하면 이상 탐지 시 Alertmanager로 알림을 전송합니다.

## 주요 명령

### `train`

Prometheus 기반 학습:

```bash
python -m pooli_aiops train \
  --settings config/settings.yaml \
  --contract config/metrics_contract.yaml
```

CSV 기반 학습:

```bash
python -m pooli_aiops train \
  --settings config/settings.yaml \
  --input-csv artifacts/synthetic_dataset/synthetic_raw_timeseries.csv \
  --timestamp-column timestamp \
  --metric-columns traffic_stream_length,traffic_stream_pending_message,traffic_stream_requests_tps \
  --application traffic-generator
```

필수/중요 옵션:

- `--settings`: 설정 YAML 경로
- `--contract`: Prometheus 학습용 계약 파일
- `--input-csv`: CSV 입력 파일
- `--start`, `--end`: UTC ISO-8601 학습 기간
- `--row-filter-column`, `--row-filter-value`: CSV 행 필터링

### `detect`

```bash
python -m pooli_aiops detect \
  --settings config/settings.yaml \
  --contract config/metrics_contract.yaml \
  --dry-run
```

필수/중요 옵션:

- `--settings`: 설정 YAML 경로
- `--contract`: 메트릭 계약 파일
- `--time`: 탐지 시각(UTC ISO-8601)
- `--dry-run`: 알림 전송 없이 판정만 수행

### `baseline-detect`

```bash
python -m pooli_aiops baseline-detect \
  --settings config/settings.yaml \
  --rules config/baseline_rules.yaml \
  --dry-run
```

이 명령은 `node-exporter`, `mysql` 같은 인프라 메트릭에 적합한 단일 메트릭 baseline 감지를 수행합니다.

필수/중요 옵션:

- `--settings`: 설정 YAML 경로
- `--rules`: baseline 규칙 파일
- `--time`: 탐지 시각(UTC ISO-8601)
- `--dry-run`: 알림 전송 없이 판정만 수행

### `rca`

```bash
python -m pooli_aiops rca \
  --settings config/settings.yaml \
  --dry-run \
  --time-window-minutes 5
```

`detect` 와 `baseline-detect` 가 저장한 이벤트를 묶어서, OS, DB, Redis, WAS, Client 계층 중 어디가 유력한지 추정합니다.

필수/중요 옵션:

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

생성 결과에는 raw 시계열, feature dataset, anomaly train/eval CSV, 이벤트 목록, 메타데이터가 포함됩니다.

필수/중요 옵션:

- `--output-dir`: 출력 디렉터리
- `--hours`: 시계열 길이
- `--freq-seconds`: 샘플링 주기
- `--future-seconds`: 미래 라벨 시점
- `--rolling-window`: 윈도우 크기
- `--seed`: 난수 시드

## 설정 파일

### `config/settings*.yaml`

애플리케이션 전체 동작을 제어합니다.

- `prometheus`: base URL, timeout, query step
- `alertmanager`: 알림 전송 여부와 대상 주소
- `artifacts`: 모델과 RCA DB 저장 위치
- `model`: 모델 이름, contamination, 학습 기간, rolling window
- `detection`: lookback 길이, 임계값 마진

대표 파일:

- `config/settings.example.yaml`: 기본 예제
- `config/settings.yaml`: 실제 실행용 설정
- `config/settings.traffic_db.yaml`: 특정 운영 시나리오용 변형 설정

### `config/metrics_contract*.yaml`

Prometheus에서 어떤 메트릭을 어떤 PromQL로 읽을지 정의합니다.

- `application`: 대상 서비스 이름
- `metrics[*].key`: 코드 내부 메트릭 식별자
- `metrics[*].promql`: 실제 Prometheus 쿼리
- `metrics[*].required`: 필수 메트릭 여부
- `labels`: 필수, 권장, 금지 라벨 규칙

대표 파일:

- `config/metrics_contract.example.yaml`: `pooli-be` 예제 계약
- `config/metrics_contract.yaml`: 실제 실행용 계약
- `config/metrics_contract.traffic_db.yaml`
- `config/metrics_contract.traffic_db_query.yaml`
- `config/metrics_contract.was_db.yaml`

### `config/baseline_rules.yaml`

단일 메트릭 baseline 감지 규칙을 정의합니다.

- 감시 대상 메트릭 목록
- 방향성(`high`, `low`)
- 경고/치명 임계값
- baseline 계산 파라미터
- 인스턴스 라벨 기준 집계 방식

## 배포 개요

### systemd 타이머

`deploy/systemd/` 에 주기 실행용 unit 파일이 포함되어 있습니다.

- `pooli-traffic-detect.service` / `.timer`: 트래픽 이상 탐지 주기 실행
- `pooli-baseline-detect.service` / `.timer`: baseline 이상 탐지 주기 실행
- `pooli-rca.service` / `.timer`: RCA 분석 주기 실행

운영 환경에서는 이 unit 파일을 `/opt/pooli-monitoring` 기준 경로에 맞춰 배포해서 사용할 수 있습니다.

### 모니터링 스택

`deploy/monitoring/docker-compose.yml` 은 다음 구성 요소를 한 번에 띄우는 예제입니다.

- Prometheus
- Alertmanager
- Loki
- Promtail
- Grafana

즉, 이 저장소는 AIOps runner만 있는 것이 아니라 대시보드와 알림 구성을 같이 실험할 수 있는 샘플 모니터링 스택도 제공합니다.

## 대시보드 요약

Grafana 대시보드는 `deploy/monitoring/grafana/dashboards/` 아래에 포함되어 있습니다.

- `pooli/api-endpoint-dashboard.json`
  API 요청량, 오류율, 지연 시간 중심으로 서비스 상태를 봅니다.
- `pooli/db-query-dashboard.json`
  DB 쿼리 지연, 호출량, 병목 지점을 확인합니다.
- `pooli/pooli-infrastructure-overview-dashboard.json`
  인프라 상태를 상위 레벨에서 빠르게 훑습니다.

README에는 사용 목적만 요약하고, 패널별 상세 해석은 대시보드 JSON과 Grafana 화면을 기준으로 확인하는 방식이 적합합니다.

## 한계와 전제

- 기본 데이터 소스는 Prometheus 입니다.
- 모델은 `ECOD`, `HBOS`, `IForest` 같은 가벼운 PyOD 계열을 전제로 합니다.
- 충분한 학습 히스토리가 없으면 `train` 이 실패할 수 있습니다.
- 탐지 결과는 RCA DB에 저장된 이벤트를 바탕으로 후속 RCA 분석에 사용됩니다.
- RCA 결과는 확정 진단이 아니라 시간 상관관계와 메트릭 성격을 이용한 추정 결과입니다.
- 운영 초기에는 트래픽 기반 이상 탐지보다 `baseline-detect` 중심으로 먼저 안정화하는 편이 더 적합할 수 있습니다.
