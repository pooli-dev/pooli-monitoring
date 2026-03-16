# pooli-monitoring TODO

## 목적

- 1단계에서는 `CPU`, `RAM`, `iowait`, `disk` 같은 단일 운영 메트릭을 baseline 기반으로 감시한다.
- 2단계에서는 `WAS`, `DB`, `Redis`를 역할별 멀티메트릭 이상탐지로 확장한다.
- 3단계에서는 이상 신호를 연결해서 RCA 후보를 제시한다.

## 현재 판단

- 현재 레포는 `settings + contract + artifact_dir` 기준으로 한 번에 한 프로파일을 돌리기 좋은 구조다.
- 현재 활성 contract는 Redis stream 계열에 가깝고, example contract는 WAS 계열 초안이다.
- 운영 초기에 트래픽이 적다면 `ECOD` 학습보다 단일 메트릭 baseline 감시를 먼저 구축하는 것이 낫다.
- `CPU/RAM` 같은 단일 메트릭은 `baseline + robust z-score + 절대 임계값 + 지속 조건`이 더 적합하다.
- `WAS/DB/Redis`처럼 여러 메트릭이 함께 움직이는 역할 단위는 `ECOD`가 더 적합하다.

## 목표 구조

1. Prometheus로 공통 인프라 메트릭 수집
2. 단일 메트릭 baseline 감시 구축
3. 역할별 `WAS`, `DB`, `Redis` contract 분리
4. 역할별 `ECOD` 학습 및 탐지
5. Alertmanager 알림 정리
6. RCA 연결 레이어 추가

## 1단계: 단일 메트릭 baseline 감시

### 대상 메트릭

- `cpu_usage_ratio`
- `memory_available_ratio`
- `cpu_iowait_ratio`
- `disk_util_ratio`
- `disk_read_latency_ms`
- `disk_write_latency_ms`
- `restart_count`
- `oom_kill_count`

### 탐지 방식

- 시간대 baseline 사용
- 최근 `7~14일` 동일 시간대 기준선 계산
- 평균/표준편차 대신 `median + MAD` 사용
- `robust z-score` 계산
- 고정 임계값 병행
- `3/5` 같은 지속 조건 사용

### PromQL 후보

- `cpu_usage_ratio`
  - `1 - avg by(instance) (rate(node_cpu_seconds_total{mode="idle"}[5m]))`
- `memory_available_ratio`
  - `node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes`
- `cpu_iowait_ratio`
  - `avg by(instance) (rate(node_cpu_seconds_total{mode="iowait"}[5m]))`
- `disk_util_ratio`
  - `rate(node_disk_io_time_seconds_total[5m])`
- `disk_read_latency_ms`
  - `1000 * rate(node_disk_read_time_seconds_total[5m]) / clamp_min(rate(node_disk_reads_completed_total[5m]), 0.001)`
- `disk_write_latency_ms`
  - `1000 * rate(node_disk_write_time_seconds_total[5m]) / clamp_min(rate(node_disk_writes_completed_total[5m]), 0.001)`

### 초기 알림 기준

- `cpu_usage_ratio`
  - warn: `z >= 3.5` and `value >= 0.85`
  - critical: `z >= 5` or `value >= 0.95`
- `memory_available_ratio`
  - warn: `z <= -3.5` and `value <= 0.15`
  - critical: `z <= -5` or `value <= 0.08`
- `cpu_iowait_ratio`
  - warn: `z >= 3.5` and `value >= 0.10`
  - critical: `z >= 5` or `value >= 0.20`
- `disk_util_ratio`
  - warn: `z >= 3.5` and `value >= 0.80`
  - critical: `value >= 0.95`
- `disk_read_latency_ms`, `disk_write_latency_ms`
  - warn: `z >= 3.5` and `value >= 20`
  - critical: `z >= 5` or `value >= 50`
- `restart_count`, `oom_kill_count`
  - 최근 `10분` 내 `1회 이상`이면 즉시 경고

### 구현 TODO

- Prometheus에 node-exporter 기반 공통 인프라 메트릭 확인
- baseline 계산용 데이터 저장 방식 결정
- `robust z-score` 계산 로직 설계
- alert 포맷 정의
- `instance`, `role`, `metric`, `value`, `baseline`, `zscore`, `duration` 포함

## 2단계: 역할별 ECOD 이상탐지

### 공통 원칙

- 역할별로 contract, settings, artifact를 분리한다.
- 한 이미지에서 프로파일만 바꿔 순차 실행한다.
- 초기에는 `WAS`, `DB`, `Redis` 3개 역할만 구성한다.
- `Traffic`은 우선 모델이 아니라 컨텍스트 신호로 둔다.

### 권장 프로파일 구조

- `config/was.settings.yaml`
- `config/was.contract.yaml`
- `artifacts/was`
- `config/db.settings.yaml`
- `config/db.contract.yaml`
- `artifacts/db`
- `config/redis.settings.yaml`
- `config/redis.contract.yaml`
- `artifacts/redis`

### WAS ECOD 후보 메트릭

- `request_rate`
- `error_rate`
- `latency_p95_ms`
- `active_db_connections`
- `jvm_heap_used_ratio`
- `jvm_gc_pause_p95_ms`
- 이후 확장
  - `endpoint_request_rate`
  - `endpoint_latency_p95`
  - `endpoint_db_time_p95`

### DB ECOD 후보 메트릭

- `db_cpu_usage_ratio`
- `db_active_connections`
- `db_query_latency_p95_ms`
- `db_slow_query_rate`
- `db_rows_examined_rate`
- `db_lock_wait_rate`
- 이후 확장
  - `db_top_query_fingerprint_rate`
  - `db_top_query_latency_p95`

### Redis ECOD 후보 메트릭

- `stream_length`
- `pending_count`
- `enqueue_latency`
- `oldest_pending_idle_seconds`
- `dlq_rate`
- `hydrate_rate`
- `refill_rate`
- 이후 확장
  - `command_latency_p95`
  - `blocked_clients`
  - `used_memory_ratio`
  - `evicted_keys_rate`

### 구현 TODO

- `WAS` contract 초안 작성
- `DB` contract 초안 작성
- `Redis` contract 개선
- raw total 메트릭을 가능한 경우 `rate` 또는 `increase`로 변경
- 역할별 artifact 경로 분리
- 역할별 학습 주기와 탐지 주기 정의

## 3단계: RCA 연결

### 목표

- 단순히 이상이 있다는 사실만 알리는 것이 아니라 원인 후보를 좁힌다.

### 1차 RCA 규칙

- `WAS -> DB -> Redis` 순서로 시간 상관 분석
- 같은 시간대에 튄 단일 메트릭 baseline 경보와 ECOD 경보를 연결
- `top_features`와 공통 시간 범위를 같이 저장

### 예시

- `DB CPU` 급등
- 같은 시각 `db_query_latency_p95` 급등
- 같은 시각 `WAS endpoint_db_time_p95` 상승
- 결론 후보
  - 특정 API 또는 무거운 SELECT로 인한 DB 병목

### 구현 TODO

- 이상 이벤트 공통 포맷 정의
- 시간 상관 규칙 정의
- 원인 후보 점수화 기준 정의
- Alertmanager description에 RCA 후보 추가

## 운영 배치

- 상시 컨테이너
  - `Prometheus`
  - `Grafana`
  - `Alertmanager`
  - 필요 시 `Loki`
- 배치 실행
  - `aiops-runner` 이미지 1개
  - `WAS`, `DB`, `Redis` 프로파일 순차 실행
- 학습
  - 하루 1회 또는 주기적 재학습
- 탐지
  - `1~5분` 간격 실행

## 인프라 판단

- `t3.medium`은 PoC 용도로는 가능할 수 있다.
- `Prometheus + Grafana + Alertmanager + Loki + AIOps` 전체를 운영으로 한 서버에 올리기에는 빠듯할 수 있다.
- 초기에는 `Prometheus + Grafana + Alertmanager + AIOps runner` 중심으로 시작하고, `Loki`는 후순위로 둔다.
- 운영에서는 모니터링 스택과 서비스 서버를 분리하는 것이 좋다.

## 우선순위

1. 단일 메트릭 baseline 감시 구축
2. `DB` ECOD 구성
3. `WAS` ECOD 구성
4. `Redis` ECOD 구성
5. RCA 연결

## 보류 사항

- 실제 DB exporter 종류 확인
- 실제 Redis exporter 종류 확인
- `pooli-traffic-generator`를 운영 컨텍스트로 쓸지 여부 결정
- Loki를 초기 배치에 포함할지 결정
- 학습용 정상 데이터 확보 시점 결정
