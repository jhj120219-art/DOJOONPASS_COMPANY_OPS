# D:\DOJOONPASS_COMPANY_OPS\docs\03_COLLECTOR_SPEC.md

## DOJOONPASS Company Ops — Collector Specification

---

## 1. 문서 정의

| 항목 | 내용 |
|---|---|
| 문서명 | Company Ops Collector Specification |
| 프로젝트 | DOJOONPASS Company Ops |
| Owner | COO |
| 상위 문서 | `00_V1_DEVELOPMENT_SPEC.md` |
| 실행 기준 | `01_V1_IMPLEMENTATION_PLAN.md` |
| Event 기준 | `02_EVENT_SCHEMA.md` |
| 목적 | Execution Event를 안전하게 수집·검증·중복제거하고 후속 시스템으로 전달하는 중앙 Collector 정의 |
| 실행 위치 | Desktop 4 |
| 적용 버전 | V1 |

본 문서는 Company Ops 중앙 Collector의 책임, 처리 흐름, 상태관리, 오류처리 및 검증 기준을 정의한다.

Collector는 Desktop 4에서 실행되는 Company Ops 중앙 처리 계층이다.

Collector는 경영 판단을 수행하는 AI Agent가 아니다.

---

## 2. Collector의 목적

Collector의 핵심 목적은 각 Desktop에서 전달된 Execution Event를 안전하게 받아 Company Ops 내부 처리 대상으로 만드는 것이다.

기본 흐름:

    Reporter
        ↓
    Execution Event
        ↓
    중앙 전달
        ↓
    Collector
        ↓
    Validation
        ↓
    Duplicate Check
        ↓
    정상 Event
        ↓
    ┌──────────────┬──────────────┐
    ↓              ↓
    Notion       History Pipeline

Collector의 핵심 책임은 다음과 같다.

1. Event 수집
2. Schema Validation
3. 중복 Event 차단
4. 처리 상태 기록
5. 오류 Event 격리
6. 정상 Event를 후속 시스템으로 전달
7. 처리 실패 시 Event 원본 보호

---

## 3. Collector의 위치

Collector는 Desktop 4의 Company Ops 프로젝트에서 실행한다.

프로그램 위치:

    D:\DOJOONPASS_COMPANY_OPS\
    └─ collector\

Collector Runtime 데이터:

    D:\DOJOONPASS_COMPANY_OPS\
    └─ runtime\

Company History 공식 원본:

    D:\DOJOONPASS_COO\
    └─ history\

Collector Runtime과 Company History Master를 혼합하지 않는다.

---

## 4. Collector가 하지 않는 일

Collector는 다음 업무를 수행하지 않는다.

- CTO 업무 평가
- CMO 업무 평가
- 프로젝트 우선순위 결정
- Critical Path 자동 확정
- Launch Readiness 자동 확정
- Go / No-Go 결정
- CEO Decision 생성
- Beta Scope 결정
- Pricing 결정
- Target 결정
- Content 전략 결정
- Product 전략 결정
- History 내용을 임의로 창작
- 원본 Event의 사실 수정

Collector는 Execution Event를 처리하는 시스템이다.

경영 의사결정 시스템이 아니다.

---

## 5. Collector Input

Collector의 Input은 `02_EVENT_SCHEMA.md`를 만족하는 Execution Event다.

예:

    {
      "schema_version": "1.0",
      "event_id": "550e8400-e29b-41d4-a716-446655440000",
      "timestamp": "2026-08-01T20:31:00+09:00",
      "source": "DESKTOP_3",
      "role": "CTO_FRONTEND",
      "project_id": "SEARCH_FRONTEND",
      "event_type": "MILESTONE_COMPLETED",
      "status": "IN_PROGRESS",
      "milestone": "Search UI",
      "summary": "Search UI implementation completed",
      "blocker": null,
      "evidence": [
        "TypeScript PASS"
      ],
      "history_candidate": true
    }

Collector는 Event Schema를 자체적으로 새로 정의하지 않는다.

`02_EVENT_SCHEMA.md`를 기준으로 검증한다.

---

## 6. Collector Output

정상 Event는 최소 두 개의 후속 처리 경로를 가진다.

    ACCEPTED EVENT
          │
          ├────────────→ Notion Sync
          │
          └────────────→ History Pipeline

단:

`history_candidate = false`

인 경우 History Pipeline에서 즉시 제외될 수 있다.

Collector는 History 최종 저장 여부를 결정하지 않는다.

---

## 7. 기본 처리 Pipeline

Collector는 다음 순서로 Event를 처리한다.

    1. Event 발견
            ↓
    2. Event 읽기
            ↓
    3. JSON Parsing
            ↓
    4. Schema Validation
            ↓
    5. Business Validation
            ↓
    6. Duplicate Check
            ↓
    7. ACCEPTED 등록
            ↓
    8. 후속 처리
       ├─ Notion
       └─ History Pipeline
            ↓
    9. 처리 결과 기록

중간 단계가 실패하면 이후 단계를 무조건 실행하지 않는다.

---

## 8. Runtime 기본 구조

V1 Runtime은 최소 다음 구조를 사용한다.

    D:\DOJOONPASS_COMPANY_OPS\
    └─ runtime\
        │
        ├─ events\
        │   ├─ incoming\
        │   ├─ processed\
        │   └─ rejected\
        │
        └─ state\

목적:

### incoming

아직 정상 처리되지 않은 Event.

### processed

Collector가 정상적으로 처리한 Event.

### rejected

Validation 실패 등으로 정상 처리할 수 없는 Event.

### state

Collector의 처리 상태 및 중복 방지 정보를 관리한다.

---

## 9. Incoming Event 원칙

새 Event는 먼저 `incoming` 영역에 존재해야 한다.

예:

    runtime\
    └─ events\
        └─ incoming\
            └─ 550e8400-e29b-41d4-a716-446655440000.json

Collector는 Incoming Event를 발견한 후 처리한다.

처리가 끝나기 전에 원본 Event를 삭제하지 않는다.

---

## 10. Processed Event 원칙

정상 처리된 Event는 `processed` 상태로 이동 또는 보존한다.

예:

    runtime\
    └─ events\
        └─ processed\
            └─ 550e8400-e29b-41d4-a716-446655440000.json

Processed Event는 중복 확인 및 문제 추적을 위한 근거로 사용할 수 있다.

Processed Runtime Event는 Company History와 동일한 것이 아니다.

---

## 11. Rejected Event 원칙

다음 Event는 `rejected` 대상으로 처리한다.

- JSON Parsing 실패
- 필수 Field 누락
- 지원하지 않는 Schema Version
- 잘못된 Event Type
- 잘못된 Status
- 잘못된 Timestamp
- Event Type / Status 모순
- BLOCKED인데 blocker 없음
- 기타 Schema Validation 실패

예:

    runtime\
    └─ events\
        └─ rejected\
            └─ TEST-INVALID-001.json

Rejected Event를 자동 삭제하지 않는다.

---

## 12. 처리 상태

Collector의 기본 처리 상태는 다음과 같다.

    RECEIVED
    ACCEPTED
    DUPLICATE
    REJECTED
    PROCESSING
    PROCESSED
    PARTIAL_FAILURE
    FAILED

---

## 13. RECEIVED

의미:

> Collector가 Event 존재를 확인함.

아직 Validation이 완료된 상태는 아니다.

---

## 14. ACCEPTED

의미:

> Event가 Validation과 Duplicate Check를 통과함.

후속 처리 가능한 상태다.

---

## 15. DUPLICATE

의미:

> 동일한 `event_id`가 이미 처리되었음.

후속 처리를 반복하지 않는다.

Notion을 다시 갱신하지 않는다.

History Candidate를 다시 만들지 않는다.

---

## 16. REJECTED

의미:

> Event 자체가 Schema 또는 Validation 기준을 통과하지 못함.

후속 시스템으로 전달하지 않는다.

원인을 Log에 기록한다.

---

## 17. PROCESSING

의미:

> 정상 Event에 대한 후속 처리가 진행 중임.

예:

    Notion Sync
    History Candidate 처리

---

## 18. PROCESSED

의미:

> V1에서 요구되는 후속 처리가 정상 완료됨.

Event 처리 완료 상태다.

---

## 19. PARTIAL_FAILURE

의미:

> Event 자체는 정상이나 후속 시스템 중 일부 처리가 실패함.

예:

    Event Validation
    PASS

    Notion Sync
    PASS

    History Pipeline
    FAIL

이 경우 Event 자체를 REJECTED로 바꾸지 않는다.

Event는 정상이다.

후속 처리만 실패한 것이다.

---

## 20. FAILED

의미:

> Collector 내부 문제 등으로 Event 처리 자체를 완료하지 못함.

원본 Event를 보존해야 한다.

재시도 가능 상태로 남겨야 한다.

---

## 21. Validation 단계

Collector는 두 종류의 Validation을 수행한다.

### Schema Validation

데이터 구조가 올바른가?

예:

- 필수 Field
- Type
- Enum
- Timestamp
- schema_version

### Business Validation

Field 간 관계가 논리적으로 가능한가?

예:

    event_type = BLOCKED
    blocker = null

→ REJECTED

또는:

    event_type = COMPLETED
    status = NOT_STARTED

→ REJECTED

---

## 22. Duplicate Check

중복 판단 기준:

    event_id

기본 처리:

    새로운 event_id
          ↓
       ACCEPTED

    기존 event_id
          ↓
       DUPLICATE

동일 Event가 여러 번 전달되더라도 결과는 한 번만 반영되어야 한다.

---

## 23. Idempotency 원칙

Collector는 동일 Event를 여러 번 받아도 최종 시스템 상태가 달라지지 않아야 한다.

예:

    Event A
      ↓
    최초 처리
      ↓
    Notion Update
    History Candidate

이후 같은 Event A 재전송:

    Event A
      ↓
    DUPLICATE
      ↓
    추가 처리 없음

이를 V1의 기본 Idempotency 원칙으로 한다.

---

## 24. 처리 순서 안전성

Collector는 Event를 무조건 파일 도착 순서대로 사실로 간주하지 않는다.

Event의 실제 발생시간은:

    timestamp

를 기준으로 한다.

단, V1에서는 복잡한 Event Ordering Engine을 만들지 않는다.

현재 상태를 갱신할 때 명백하게 오래된 Event가 최신 상태를 역전시키는 문제가 발생하지 않도록 방어해야 한다.

예:

현재 Notion:

    Last Updated
    20:00

늦게 도착한 Event:

    timestamp
    15:00

이 Event 때문에 현재 상태가 과거 상태로 돌아가서는 안 된다.

---

## 25. Late Event 처리

늦게 도착한 Event도 History상 의미가 있을 수 있으므로 무조건 폐기하지 않는다.

예:

    Event 발생
    8월 1일 15:00

    Collector 도착
    8월 2일 12:00

이 Event는:

- Event 자체는 정상 처리
- Notion Current State 역전 방지
- History는 실제 timestamp 기준 날짜 검토

원칙을 따른다.

---

## 26. Notion 전달 조건

다음 Event는 기본적으로 Notion Sync 대상이다.

    STARTED
    BLOCKED
    RESUMED
    MILESTONE_COMPLETED
    COMPLETED
    CANCELLED
    ISSUE_RESOLVED

`DECISION_APPROVED`의 Notion 반영 방식은 Notion Spec에서 별도로 정의할 수 있다.

Collector는 Notion DB 구조를 직접 결정하지 않는다.

---

## 27. History 전달 조건

기본적으로:

    history_candidate = true

인 Event를 History Pipeline 검토 대상으로 전달한다.

단, Event Type 기본 규칙과 모순되는 값이 있는 경우 Validation 또는 History Pipeline에서 확인한다.

History Candidate가 공식 History를 의미하지 않는다.

---

## 28. Notion 실패 처리

예:

    Event
      ↓
    Validation PASS
      ↓
    Notion API FAIL

이 경우:

    Event 삭제 금지
    Event REJECTED 처리 금지
    Local History 삭제 금지

처리 상태:

    PARTIAL_FAILURE

또는 후속 처리 상태를 별도로 기록한다.

Notion Sync는 재시도할 수 있어야 한다.

---

## 29. History Pipeline 실패 처리

예:

    Event
      ↓
    Validation PASS
      ↓
    Notion PASS
      ↓
    History Pipeline FAIL

처리:

    Event 원본 유지
    Notion 상태 유지
    History 재처리 가능 상태 유지

이미 성공한 Notion 작업을 실패했다고 되돌리지 않는다.

---

## 30. GitHub 장애 처리

GitHub 또는 중앙 전달 계층 접근 실패가 발생하면 Event가 사라져서는 안 된다.

원칙:

    전달 실패
        ↓
    Local Event 유지
        ↓
    다음 실행 재시도

Collector가 GitHub 장애 때문에 Local Company History를 삭제하거나 수정해서는 안 된다.

---

## 31. Atomic Processing 원칙

가능한 범위에서 Event 파일은 처리 중간 상태와 완료 상태를 구분한다.

다음과 같은 상황을 방지한다.

    Event 읽는 중
        ↓
    프로그램 종료
        ↓
    Event는 삭제됨
        ↓
    복구 불가

V1에서는 복잡한 Transaction 시스템을 만들 필요는 없지만:

> 성공이 확인되기 전에 원본을 제거하지 않는다.

원칙은 반드시 지킨다.

---

## 32. Retry 원칙

재시도 가능한 오류:

- Notion API 일시 실패
- GitHub 일시 실패
- 파일 잠금
- 네트워크 오류
- 일시적인 외부 서비스 오류

재시도해서는 안 되는 오류:

- 잘못된 Schema
- 필수 Field 누락
- 지원하지 않는 Event Type
- 잘못된 Status 조합

후자는 `REJECTED`로 처리한다.

---

## 33. Retry 횟수

V1에서는 지나치게 복잡한 Retry Engine을 만들지 않는다.

최소한:

    실패 기록
       ↓
    Event 보존
       ↓
    다음 실행 시 재처리 가능

구조를 보장한다.

정교한 Exponential Backoff 시스템은 실제 필요성이 확인되기 전에는 구현하지 않는다.

---

## 34. Logging

Collector는 최소 다음을 기록한다.

- 실행 시작
- Event 발견
- Event ID
- Validation 결과
- Duplicate 여부
- 처리 결과
- Notion 전달 결과
- History 전달 결과
- Error
- Retry
- 실행 종료

예:

    2026-08-01 11:00:00
    COLLECTOR START

    EVENT
    TEST-001

    VALIDATION
    PASS

    DUPLICATE
    NO

    NOTION
    PASS

    HISTORY
    PASS

    RESULT
    PROCESSED

---

## 35. Log 금지사항

Log에 다음을 불필요하게 저장하지 않는다.

- API Secret
- Access Token
- Password
- 전체 환경변수
- 인증정보
- 불필요한 AI 대화 전체

Secret은 Log에 출력하지 않는다.

---

## 36. State 관리

Collector는 최소 다음 정보를 추적할 수 있어야 한다.

    processed event_id

    last collector run

    pending retry

필요한 경우:

    last daily close

    last monthly close

는 Scheduler/History 영역에서 관리한다.

Collector State와 Company History를 혼합하지 않는다.

---

## 37. State 저장 위치

V1 예시:

    runtime\
    └─ state\
        └─ collector_state.json

예:

    {
      "last_run": "2026-08-01T11:00:00+09:00",
      "processed_event_ids": [
        "TEST-001",
        "TEST-002"
      ]
    }

단, 실제 구현에서 Event 수가 늘어날 경우 구조 변경이 필요할 수 있다.

V1에서는 과도한 DB 도입을 하지 않는다.

---

## 38. Database 도입 원칙

V1 Collector만을 위해 별도 PostgreSQL, Supabase 또는 별도 중앙 DB를 즉시 도입하지 않는다.

현재 규모에서는 파일 기반 상태관리로 시작할 수 있다.

DB가 실제로 필요한 조건이 확인되면 이후 검토한다.

예:

- Event 수 급증
- 동시 처리 증가
- 다수 사용자
- 복잡한 Query 필요
- 파일 기반 상태관리 한계

현재는 해당 문제를 선제적으로 해결하지 않는다.

---

## 39. Collector 실행 방식

Collector는 최소 다음 상황에서 실행 가능해야 한다.

### 수동 테스트 실행

개발 중:

    Collector 직접 실행

### 정기 실행

Scheduler 기반.

### 프로그램 시작

Catch-up 또는 Pending Event 확인.

V1에서 24시간 상시 실행 Server를 필수로 하지 않는다.

---

## 40. Collector와 Scheduler 분리

Collector:

> Event를 처리한다.

Scheduler:

> 언제 처리할지 실행한다.

둘의 책임을 분리한다.

Scheduler 장애가 Collector 데이터 로직을 변경해서는 안 된다.

---

## 41. Collector와 Reporter 분리

Reporter:

> Event를 만든다.

Collector:

> Event를 받는다.

Reporter가 Notion을 직접 수정하지 않는다.

Reporter가 Company History를 직접 생성하지 않는다.

구조:

    Reporter
       ↓
    Event
       ↓
    Collector
       ↓
    Notion / History

이 구조를 유지한다.

---

## 42. Collector와 History Generator 분리

Collector가 Markdown Company History를 직접 작성하지 않는다.

Collector:

    Event 처리

History Pipeline:

    History Candidate 선별

Daily Generator:

    Daily History 작성

Monthly Generator:

    Monthly Summary 작성

책임을 분리한다.

---

## 43. Mock Event Test

실제 Reporter 연결 전에 다음 Event를 Collector에 투입한다.

### 정상

    TEST-START-001
    TEST-BLOCK-001
    TEST-RESUME-001
    TEST-MILESTONE-001
    TEST-COMPLETE-001

### 오류

    TEST-INVALID-TYPE-001
    TEST-MISSING-FIELD-001
    TEST-INVALID-STATUS-001
    TEST-INVALID-TIMESTAMP-001

### 중복

    TEST-DUPLICATE-001

동일 Event를 두 번 전달한다.

---

## 44. Collector Test Matrix

| Test | 기대 결과 |
|---|---|
| 정상 STARTED | ACCEPTED |
| 정상 BLOCKED | ACCEPTED |
| 정상 RESUMED | ACCEPTED |
| 정상 MILESTONE | ACCEPTED |
| 정상 COMPLETED | ACCEPTED |
| 동일 event_id 두 번 | 두 번째 DUPLICATE |
| 필수 Field 누락 | REJECTED |
| Invalid Event Type | REJECTED |
| Invalid Status | REJECTED |
| Invalid Timestamp | REJECTED |
| BLOCKED + blocker null | REJECTED |
| 후속 Notion 실패 | Event 보존 |
| 후속 History 실패 | Event 보존 |
| Collector 중간 종료 | Event 손실 없음 |

---

## 45. Mock E2E

Collector 단독 테스트 이후 다음 흐름을 검증한다.

    Mock Event
        ↓
    incoming
        ↓
    Collector
        ↓
    Validation
        ↓
    Duplicate Check
        ↓
    ACCEPTED
        ↓
    processed

이 단계에서는 실제 Reporter가 필요하지 않다.

---

## 46. 후속 E2E

Notion과 History 구현 후:

    Mock Event
        ↓
    Collector
        ↓
    ┌───────────────┐
    ↓               ↓
    Notion       History
      ↓               ↓
    Current        Candidate
    State             ↓
                  Daily

까지 검증한다.

---

## 47. 실제 Reporter 연결 이후

최종적으로:

    Desktop 3
        ↓
    Reporter
        ↓
    GitHub
        ↓
    Desktop 4
        ↓
    Collector
        ↓
    Notion / History

를 검증한다.

Desktop 3 검증 성공 후 Desktop 1과 Desktop 2에 적용한다.

---

## 48. Collector 성공 기준

Collector V1은 다음 조건을 만족하면 성공으로 판단한다.

1. 정상 Event를 읽을 수 있다.
2. Schema Validation이 작동한다.
3. Business Validation이 작동한다.
4. 중복 Event를 차단한다.
5. Rejected Event를 보존한다.
6. 정상 Event를 Processed 상태로 관리한다.
7. Notion 후속 처리가 가능하다.
8. History 후속 처리가 가능하다.
9. 후속 처리 실패 시 Event가 손실되지 않는다.
10. 프로그램 중단 시 Event가 손실되지 않는다.
11. 처리 결과 Log가 남는다.
12. 동일 Event 재전송이 최종 상태를 중복 변경하지 않는다.

---

## 49. V1에서 만들지 않는 것

Collector V1에서는 다음을 만들지 않는다.

- Kafka
- RabbitMQ
- Redis Queue
- 별도 Message Broker
- Kubernetes
- 별도 중앙 Server
- 실시간 Streaming
- 복잡한 Event Sourcing Platform
- 대규모 Observability
- 복잡한 Retry Infrastructure
- 별도 Admin Dashboard
- 별도 Collector Web UI

현재 규모에서 필요한 최소 구조로 구현한다.

---

## 50. 구현 우선순위

Collector 구현 순서:

    1. Runtime Directory
          ↓
    2. Event Reader
          ↓
    3. JSON Parser
          ↓
    4. Schema Validator
          ↓
    5. Business Validator
          ↓
    6. Duplicate Checker
          ↓
    7. State Manager
          ↓
    8. Processed / Rejected 처리
          ↓
    9. Logging
          ↓
    10. Mock Test
          ↓
    11. 후속 Notion/History 연결

---

## 51. Phase 관계

`01_V1_IMPLEMENTATION_PLAN.md` 기준:

    Phase 1
    Execution Event Core
          ↓
    Phase 2
    Mock Event + Collector
          ↓
    Phase 3
    Notion Sync
          ↓
    Phase 4
    History Pipeline

본 문서는 주로 Phase 2 구현 기준으로 사용한다.

---

## 52. 변경 원칙

Collector 개발 중 새로운 기능이 필요해 보여도 즉시 추가하지 않는다.

다음 질문을 먼저 확인한다.

    Event 안전 처리에 필수인가?
            ↓
       YES       NO
        ↓         ↓
      검토      V1 제외

특히 운영 규모를 예상하여 복잡한 Infrastructure를 선제적으로 만들지 않는다.

---

## 53. 데이터 안전 최우선 원칙

Collector의 가장 중요한 기술 원칙:

> 처리 실패는 허용할 수 있지만 Event 손실은 허용하지 않는다.

따라서:

    실패
      ↓
    보존
      ↓
    원인 기록
      ↓
    재처리

구조를 기본으로 한다.

자동화 실패를 숨기기 위해 Event를 삭제하거나 성공 처리하지 않는다.

---

## 54. 완료 보고 형식

Collector 구현 완료 시 다음 형식으로 보고한다.

    [Phase]
    Phase 2 — Mock Event + Collector

    [상태]
    PASS / BLOCKED / FAIL

    [구현 내용]

    [변경 파일]

    [테스트]

    [Evidence]

    [Rejected Test 결과]

    [Duplicate Test 결과]

    [Event Loss Test 결과]

    [발견된 문제]

    [다음 작업]

    [CEO/COO Decision Required]
    NONE 또는 해당 사항

---

## 55. 본 문서 이후

Collector Spec 이후 후속 시스템 명세 순서는 다음과 같다.

    02_EVENT_SCHEMA.md
            ↓
    03_COLLECTOR_SPEC.md
            ↓
    Notion Sync
            ↓
    History Pipeline

단, 문서 작성 자체가 개발보다 우선되어서는 안 된다.

필요한 최소 명세가 확보되면 실제 구현과 검증을 우선한다.

---

# END OF DOCUMENT