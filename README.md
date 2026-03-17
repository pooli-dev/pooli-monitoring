# pooli-monitoring

`pooli-be` 모니터링을 위한 `PyOD` 기반 AIOps 스캐폴드입니다.

## 목적

이 저장소는 `pooli-be` 애플리케이션 레포와 분리되어 있습니다.

- `trainer`는 Prometheus 메트릭에서 정상 패턴을 학습합니다.
- `detector`는 최신 메트릭 구간을 읽고, feature를 만든 뒤, 이상 점수를 계산하고, 필요하면 Alertmanager로 알림을 보냅니다.
- `pooli-be`는 나중에 메트릭만 노출하면 되고, 모델 코드는 이 저장소에 유지합니다.

## 구조

```text
pooli-monitoring/
  config/
    metrics_contract.example.yaml
    settings.example.yaml
  artifacts/
    .gitkeep
  src/pooli_aiops/
    __init__.py
    __main__.py
    alerting.py
    cli.py
    config.py
    contracts.py
    detector.py
    features.py
    modeling.py
    prometheus_client.py
    trainer.py
  pyproject.toml
  README.md
```

## 빠른 시작

1. 가상환경을 생성합니다.
2. `pip install -e .` 로 의존성을 설치합니다.
3. `config/settings.example.yaml` 을 `config/settings.yaml` 로 복사한 뒤 Prometheus와 Alertmanager 주소를 수정합니다.
4. `config/metrics_contract.example.yaml` 을 `config/metrics_contract.yaml` 로 복사한 뒤 필요하면 PromQL을 수정합니다.
5. 모델을 학습합니다.
6. detector를 실행합니다.

예시 명령:

```bash
cd /opt/pooli-monitoring
source .venv/bin/activate
python -m pooli_aiops train --settings config/settings.yaml --contract config/metrics_contract.yaml
python -m pooli_aiops detect --settings config/settings.yaml --contract config/metrics_contract.yaml --dry-run
```

## API Dashboard

### Variables

| 항목 | 의미 | 어떻게 쓰는지 |
| --- | --- | --- |
| `instance` | 어떤 `pooli-be` 인스턴스의 메트릭을 볼지 고르는 필터 | 특정 서버만 보고 싶으면 하나 선택, 전체 추세를 보려면 `All` |
| `uri` | 어떤 API URI를 볼지 고르는 필터 | 특정 API만 보고 싶을 때 사용, 전체 비교면 `All` |

### Panels

| 패널명 | 의미 | 해석 포인트 | 주의사항 |
| --- | --- | --- | --- |
| `Total RPS` | 전체 API 요청 처리량 | 현재 API 트래픽 크기를 빠르게 확인 | 정상/비정상은 서비스 평시 기준과 비교해야 함 |
| `Error Rate (%)` | 전체 요청 중 5xx 비율 | 장애가 실제 사용자 오류로 이어지는지 확인 | 현재 5xx 기준이라 4xx는 포함되지 않음 |
| `Overall Latency P95` | 전체 API 응답시간의 P95 | 느린 요청이 얼마나 늘었는지 확인 | 평균보다 P95가 실무에서 더 중요 |
| `Active Endpoints` | 최근 요청이 들어온 URI 개수 | 얼마나 다양한 API가 호출 중인지 확인 | 호출량이 아닌 종류 수임 |
| `RPS by Endpoint` | URI별, Method별 요청량 추이 | 어떤 API가 트래픽을 많이 먹는지 확인 | 특정 시점 급증 API 찾기에 유용 |
| `Latency P95 by Endpoint` | URI별, Method별 응답시간 P95 | 느려진 API를 빠르게 식별 | 일부 저트래픽 API는 변동성이 클 수 있음 |
| `Error Rate (%) by Endpoint` | URI별, Method별 5xx 비율 | 어떤 API가 실제 오류를 내는지 확인 | 트래픽이 아주 적은 API는 비율이 크게 튈 수 있음 |
| `Status Code Distribution` | 상태코드별 요청량 분포 | 200/400/500대 비중 변화 확인 | 문제 판단 시 절대값과 비율을 같이 봐야 함 |
| `Top 10 Slowest Endpoints (P95)` | 가장 느린 API 상위 10개 | 병목 API를 우선순위로 볼 때 사용 | 현재 `UNKNOWN`, `/actuator*`, `/favicon.ico`는 제외 |
| `Top 10 Most Requested Endpoints` | 가장 많이 호출되는 API 상위 10개 | 트래픽 집중 API 파악에 유용 | 현재 `UNKNOWN`, `/actuator*`, `/favicon.ico`는 제외 |

---

## DB Dashboard

### Variables

| 항목 | 의미 | 어떻게 쓰는지 |
| --- | --- | --- |
| `mapper` | 어떤 MyBatis Mapper를 볼지 고르는 필터 | 특정 Mapper만 보고 싶으면 선택, 전체면 `All` |
| `operation` | Mapper 내부 어떤 쿼리 작업을 볼지 고르는 필터 | 특정 메서드 단위 분석 시 사용 |

### Panels

| 패널명 | 의미 | 해석 포인트 | 주의사항 |
| --- | --- | --- | --- |
| `Total Query RPS` | 전체 DB 쿼리 실행량 | 현재 DB 부하 크기를 빠르게 확인 | API 요청 수와 1:1 대응은 아님 |
| `Overall Query P95` | 전체 쿼리 지연시간 P95 | 전반적인 DB 응답 저하 여부 확인 | 느린 소수 쿼리 영향 파악에 유리 |
| `Overall Query P99` | 전체 쿼리 지연시간 P99 | 극단적으로 느린 쿼리 존재 여부 확인 | P95보다 더 민감해서 튐이 클 수 있음 |
| `Active Mappers` | 최근 실행된 Mapper 수 | 어떤 DB 기능 영역이 활성화됐는지 확인 | 호출량이 아닌 종류 수임 |
| `Query Latency P95 by Mapper` | Mapper별 쿼리 지연시간 P95 | 어느 Mapper 계층이 느린지 확인 | 상세 원인은 operation 패널에서 추가 확인 |
| `Query Latency P95 by Operation` | Mapper+Operation별 쿼리 지연시간 P95 | 어떤 메서드가 느린지 직접 식별 | 실제 병목 SQL 후보를 좁히는 데 유용 |
| `Query RPS by Mapper` | Mapper별 쿼리 실행량 | 어떤 기능 영역이 DB를 많이 쓰는지 확인 | 고트래픽 Mapper와 고지연 Mapper를 같이 봐야 함 |
| `Query RPS by Operation` | Mapper+Operation별 쿼리 실행량 | 어떤 쿼리가 가장 자주 호출되는지 확인 | 빈도 높고 느린 쿼리가 우선 개선 대상 |
| `Top 10 Slowest Queries (P95)` | 가장 느린 쿼리 상위 10개 | 성능 최적화 우선순위 선정용 | 현재 선택한 `mapper`, `operation` 필터 영향을 받음 |
| `Top 10 Most Called Queries` | 가장 많이 호출된 쿼리 상위 10개 | 부하 집중 지점 파악용 | 호출량만 높고 문제 없을 수도 있으니 P95와 함께 봐야 함 |

---

## 같이 보는 방법

| 상황 | 먼저 볼 항목 | 다음에 볼 항목 |
| --- | --- | --- |
| API가 느려짐 | `Overall Latency P95` | `Latency P95 by Endpoint` -> `Top 10 Slowest Endpoints (P95)` |
| API 오류 증가 | `Error Rate (%)` | `Error Rate (%) by Endpoint` -> `Status Code Distribution` |
| DB 병목 의심 | `Overall Query P95`, `Overall Query P99` | `Query Latency P95 by Operation` -> `Top 10 Slowest Queries (P95)` |
| 트래픽 급증 | `Total RPS`, `Total Query RPS` | `Top 10 Most Requested Endpoints`, `Top 10 Most Called Queries` |
| 특정 기능 장애 분석 | `uri` 또는 `mapper` 필터 적용 | API 패널과 DB 패널을 같은 시간대에서 같이 비교 |


## 현재 전제

- Prometheus를 메트릭의 기준 데이터 소스로 사용합니다.
- contract 파일이 논리 메트릭 이름과 실제 PromQL 매핑을 담당합니다.
- PyOD는 시계열 예측 전용 라이브러리가 아니기 때문에 window 기반 feature engineering을 사용합니다.
- 처음에는 `ECOD`, `HBOS`, `IForest` 같은 가벼운 모델부터 시작하는 것을 권장합니다.

## pooli-be 연동의 다음 단계

나중에 `pooli-be`가 contract를 만족하는 메트릭을 노출해야 합니다.
모니터링 코드는 `request_rate`, `error_rate`, `p95_latency_ms` 같은 논리 키를 계속 사용합니다.
실제 raw metric 이름이 바뀌더라도 contract 파일만 수정하면 되도록 설계했습니다.
