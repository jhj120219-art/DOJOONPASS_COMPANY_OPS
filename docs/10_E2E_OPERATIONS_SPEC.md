# D:\DOJOONPASS_COMPANY_OPS\docs\10_E2E_OPERATIONS_SPEC.md

## DOJOONPASS Company Ops — End-to-End Validation & Operations Specification

---

## 1. 문서 정의

| 항목 | 내용 |
|---|---|
| 문서명 | Company Ops End-to-End Validation & Operations Specification |
| 프로젝트 | DOJOONPASS Company Ops |
| Owner | COO |
| 상위 문서 | `00_V1_DEVELOPMENT_SPEC.md` |
| 실행 기준 | `01_V1_IMPLEMENTATION_PLAN.md` |
| Event 기준 | `02_EVENT_SCHEMA.md` |
| Collector 기준 | `03_COLLECTOR_SPEC.md` |
| Notion 기준 | `04_NOTION_SYNC_SPEC.md` |
| History 기준 | `05_HISTORY_PIPELINE_SPEC.md` |
| Daily History 기준 | `06_DAILY_HISTORY_SPEC.md` |
| Scheduler 기준 | `07_SCHEDULER_CATCHUP_SPEC.md` |
| Backup 기준 | `08_BACKUP_SPEC.md` |
| Monthly 기준 | `09_MONTHLY_HISTORY_SPEC.md` |
| 목적 | Company Ops V1 전체 Pipeline의 실제 운영 가능성 및 장애 복구 능력 검증 |
| 실행 중심 | Desktop 4 |
| 적용 버전 | V1 |
| Phase | Phase 9 — Final Validation |

본 문서는 Company Ops V1의 마지막 개발/검증 단계다.

목적은 새로운 기능을 추가하는 것이 아니다.

핵심 질문은 하나다.

> 지금까지 만든 Company Ops가 실제 업무 환경에서 사람의 지속적인 개입 없이 회사의 중요한 실행정보와 History를 안전하게 수집·보존할 수 있는가?

---

# 2. Phase 9의 역할

Phase 1~8에서는 각각의 기능을 만든다.

Phase 9에서는 그것들을 하나의 시스템으로 연결해 검증한다.

전체 구조:

    Desktop 1
    CTO Backend
        │
        │
        ▼

    Desktop 2
    CMO / Content OS
        │
        │
        ▼

    Desktop 3
    CTO Frontend
        │
        │
        ▼

    Execution Event
        │
        ▼
    Shared Event Layer
        │
        ▼
    Desktop 4
    Company Ops
        │
        ▼
    Collector
        │
        ├─────────────► Notion
        │               Current State
        │
        ▼
    History Pipeline
        │
        ▼
    Daily History
        │
        ▼
    Monthly History
        │
        ▼
    Local Master
        │
        ▼
    Safe Backup Working Copy
        │
        ▼
    GitHub Private Backup

Phase 9에서는 이 전체 흐름을 실제로 검증한다.

---

# 3. V1 성공의 정의

Company Ops V1의 성공은:

    모든 업무를 AI가 이해한다

가 아니다.

또한:

    회사 전체를 완전히 자동 운영한다

도 아니다.

V1 성공 기준은:

> Desktop 1~3에서 발생한 중요한 업무 Event가 Desktop 4에 도달하고, 현재 상태와 회사 History가 자동으로 정리되며, PC OFF·Network 장애·외부 서비스 장애가 발생해도 원본이 손실되지 않는 것.

이다.

---

# 4. V1 시스템 책임

Company Ops V1이 책임지는 영역:

| 영역 | 책임 |
|---|---|
| Event | 각 프로젝트의 업무 Event 수집 |
| Collector | Event 중앙 취합 |
| Current State | Notion 상태 반영 |
| History | 중요 Event 선별 |
| Daily | 날짜별 History 생성 |
| Monthly | 월간 History 압축 |
| Scheduler | 자동 실행 |
| Catch-up | PC OFF/누락 복구 |
| Local Master | 공식 History 보존 |
| Backup | GitHub Private 복구본 |
| Logging | 실행/실패 추적 |

---

# 5. V1이 책임지지 않는 영역

다음은 V1 책임이 아니다.

- CTO 개발 자동화
- CMO 마케팅 자동화
- CEO 전략 결정
- 자동 Product Priority 결정
- 자동 Beta Scope 결정
- 자동 Pricing 결정
- 자동 Launch Date 결정
- 자동 Go / No-Go 결정
- 모든 회사 업무 기록
- 직원 성과평가
- 재무회계
- 법무
- 계약 관리
- 투자자 CRM
- 완전한 BI 시스템

---

# 6. Source of Truth 구조

Company Ops는 하나의 Source of Truth를 모든 영역에 강제로 적용하지 않는다.

영역별 Source of Truth가 다르다.

| 정보 | Source of Truth |
|---|---|
| 현재 실행 상태 | Notion |
| 공식 개발 명세 | GitHub `/docs` |
| 중요 회사 History | Desktop 4 Local Master |
| History Backup | GitHub Private Backup |
| Raw Event | Event Storage |
| Scheduler State | Desktop 4 Runtime State |

---

# 7. 가장 중요한 데이터 원칙

다음 순서를 지킨다.

    Event
      ↓
    Current State

그리고 별도로:

    Event
      ↓
    History Filter
      ↓
    Company History

즉:

> 모든 Current State가 Company History가 되는 것은 아니다.

---

# 8. E2E 검증 범위

Phase 9에서는 최소 다음을 검증한다.

    Reporter
    Event
    Collector
    Notion
    History Filter
    Daily History
    Scheduler
    Catch-up
    Monthly History
    Local Master
    Backup
    Recovery

---

# 9. 검증 환경

실제 운영환경을 기준으로 한다.

현재 구조:

    Desktop 1
    CTO Backend / Crawling / Search

    Desktop 2
    CMO / Content OS

    Desktop 3
    CTO Frontend

    Desktop 4
    COO / Company Ops

각 Desktop에서 발생하는 실제 Event 흐름을 검증한다.

---

# 10. Mock-only 검증 금지

Unit Test 또는 Mock Test만 PASS했다고 V1 완료로 판단하지 않는다.

최종적으로 실제 Desktop 환경에서:

    Event 발생
        ↓
    Desktop 4 수집
        ↓
    History 생성

까지 최소 한 번 검증해야 한다.

---

# 11. E2E Scenario 1 — 정상 Event

Desktop 3에서 예:

    Search UI 완료

Event 생성:

    milestone.completed

기대:

    Event
      ↓
    Collector
      ↓
    Notion Update
      ↓
    History KEEP
      ↓
    Daily History

---

# 12. 정상 Event 검증 항목

확인:

    Event ID 생성

    Timestamp 정상

    Project 정상

    Owner 정상

    Event Type 정상

    Evidence 존재

    Collector 수집

    중복 없음

    Notion 반영

    History 반영

    Local 저장

---

# 13. E2E Scenario 2 — 중요하지 않은 Event

예:

    CSS spacing 수정

    README typo 수정

    변수명 변경

기대:

    Event 수집 가능

하지만:

    History Filter
        ↓
    DROP

Company History에는 들어가지 않는다.

---

# 14. E2E Scenario 3 — Blocker

예:

    auction → auction_item
    synchronization failure

Event:

    blocker.detected

기대:

    Collector
        ↓
    Notion
        ↓
    Current Blocker

그리고 중요도 기준 충족 시:

    Daily History
        ↓
    Major Issue

---

# 15. E2E Scenario 4 — Blocker Resolution

기존 Blocker:

    OPEN

해결 Event:

    blocker.resolved

기대:

    Notion
        ↓
    Resolved

History:

    Resolution 기록

Monthly:

    Issue Lifecycle 연결 가능

---

# 16. E2E Scenario 5 — CEO Decision

예:

    Closed Beta Scope 확정

Event:

    decision.confirmed

Authority:

    CEO

기대:

    Current State 반영

    Daily History 반영

    Monthly Major Decision 반영

---

# 17. CEO Decision 권한 검증

다음 Event:

    COO recommends Beta Scope A.

이것을:

    CEO approved Beta Scope A.

로 변환하면 FAIL이다.

Recommendation과 Decision을 구분해야 한다.

---

# 18. E2E Scenario 6 — 중복 Event

동일 Event가 두 번 전달됨.

기대:

    Event ID 확인
        ↓
    첫 Event 처리
        ↓
    두 번째 Event
        ↓
    Duplicate

History 중복 생성 없음.

---

# 19. E2E Scenario 7 — 잘못된 Event

예:

    event_type 없음

또는:

    event_id 없음

기대:

    Validation FAIL

    REJECTED

정상 Event Pipeline을 오염시키지 않는다.

---

# 20. E2E Scenario 8 — Desktop 4 OFF

Desktop 1~3에서 Event 발생.

Desktop 4:

    OFF

기대:

    Event 원본 유지

Desktop 4 ON:

    Collector
        ↓
    Pending Event 수집
        ↓
    정상 처리

---

# 21. 핵심 검증

Desktop 4가 꺼져 있었다는 이유로 Event가 사라지면:

    V1 FAIL

이다.

Company Ops는 항상 켜져 있는 서버를 전제로 하지 않는다.

---

# 22. E2E Scenario 9 — 여러 날 Desktop 4 OFF

예:

    금요일
    Desktop 4 OFF

    토요일
    OFF

    일요일
    OFF

    월요일
    ON

기대:

    Pending Event 수집
        ↓
    Missing Daily 계산
        ↓
    오래된 날짜부터 Catch-up
        ↓
    Daily History 복구

---

# 23. E2E Scenario 10 — Scheduler 시간 PC OFF

오전 11시:

    Desktop 4 OFF

오후:

    Desktop 4 ON

기대:

    Startup/Login Runner
        ↓
    Catch-up
        ↓
    전날 History 생성

---

# 24. E2E Scenario 11 — Scheduler 실행 중 강제 종료

Runner 실행 중:

    PC Shutdown

기대:

    완료되지 않은 날짜
    Success State 없음

다음 실행:

    마지막 성공 State 이후부터 재개

---

# 25. E2E Scenario 12 — Stale Lock

실행 중 강제 종료.

Lock 남음.

다음 실행:

    Lock 확인
        ↓
    실제 Process 없음
        ↓
    Stale Lock 제거
        ↓
    정상 재개

---

# 26. E2E Scenario 13 — Notion 장애

Notion API:

    FAIL

기대:

    Event Local 처리 유지

    History Pipeline 유지

    Daily Local History 생성 가능

Notion:

    PENDING

---

# 27. Notion이 Single Point of Failure이면 안 된다

다음 구조는 FAIL이다.

    Notion FAIL
        ↓
    Company History 생성 불가

Company History는 외부 SaaS 하나에 의존해서는 안 된다.

---

# 28. E2E Scenario 14 — Internet OFF

Desktop 4:

    Internet OFF

Local Event:

    존재

기대:

    Collector Local 처리

    History 생성

    Daily Local 저장

외부:

    Notion Pending
    GitHub Backup Pending

---

# 29. E2E Scenario 15 — GitHub Push 실패

Local History:

    PASS

GitHub:

    FAIL

기대:

    Local History 유지

    Backup Pending

다음 실행:

    Retry

---

# 30. E2E Scenario 16 — Remote Divergence

GitHub Backup Repository에 예상하지 못한 Remote Commit 존재.

Push:

    rejected

기대:

    자동 Pull 금지

    자동 Merge 금지

    Force Push 금지

    Local Master 변경 금지

    Backup Review/Failed

---

# 31. E2E Scenario 17 — Local History 삭제

실수로 Local History 파일 삭제.

Backup Runner:

    Deleted File 감지

기대:

    Remote 삭제 자동 전파 금지

    Backup 중단

---

# 32. E2E Scenario 18 — Backup Working Copy 손상

Working Copy:

    삭제 또는 손상

기대:

    Local Master 영향 없음

Working Copy는 다시 구성 가능해야 한다.

---

# 33. E2E Scenario 19 — Late Event

예:

현재:

    2026-09-03

Event 실제 발생일:

    2026-08-20

중요 Event가 늦게 도착.

기대:

    2026-08-20 Daily
        ↓
    안전 Update

    2026-08
        ↓
    MONTHLY_DIRTY

    Monthly Update

---

# 34. E2E Scenario 20 — Monthly Catch-up

9월 1일:

    Desktop 4 OFF

9월 3일:

    ON

기대:

    8월 Daily Coverage 확인
        ↓
    Missing Daily 처리
        ↓
    8월 Monthly 생성

---

# 35. E2E Scenario 21 — 여러 달 누락

마지막 Monthly:

    2026-08

현재:

    2026-12

기대:

    09
      ↓
    10
      ↓
    11

순차 생성.

현재 월 12월은 생성하지 않는다.

---

# 36. E2E Scenario 22 — AI 실패

History Summary AI 기능:

    FAIL

기대:

    Rule-based Fallback

Company History 자체는 계속 생성된다.

---

# 37. AI가 Single Point of Failure이면 안 된다

AI API:

    unavailable

이라고 해서:

    Daily History FAIL

    Monthly History FAIL

이 되어서는 안 된다.

---

# 38. E2E Scenario 23 — Empty Day

중요 Event 없음.

기대:

    GENERATED_EMPTY

State:

    정상 진행

다음날 Catch-up에서 다시 처리하지 않는다.

---

# 39. E2E Scenario 24 — Empty Month

한 달 동안 Material History 없음.

기대:

    Monthly 생성 가능

내용:

    No material company-level changes were recorded.

누락된 월과 구분한다.

---

# 40. E2E Scenario 25 — Reporter 미설치 Desktop

예:

Desktop 2 Reporter 아직 설치 안 됨.

기대:

    Desktop 1/3/4 Pipeline 정상 동작

하나의 Reporter 미설치가 전체 Company Ops를 막아서는 안 된다.

---

# 41. Partial Deployment 허용

V1 설치는 모든 Desktop을 동시에 완료할 필요가 없다.

예:

    Desktop 3
    먼저 Reporter

        ↓

    검증

        ↓

    Desktop 1

        ↓

    Desktop 2

순차 설치 가능.

---

# 42. Reporter Rollout 권장 순서

현재 실제 상황을 기준으로 권장 순서:

    1. Desktop 4
       Company Ops Core

    2. Desktop 3
       CTO Frontend Reporter

    3. Desktop 1
       CTO Backend Reporter

    4. Desktop 2
       CMO / Content OS Reporter

이 순서는 물리적 접근성과 현재 업무환경에 따라 조정할 수 있다.

---

# 43. Desktop별 역할

| Desktop | 역할 | Company Ops 역할 |
|---|---|---|
| Desktop 1 | CTO Backend / Crawling / Search | Backend Event Source |
| Desktop 2 | CMO / Content OS | CMO Event Source |
| Desktop 3 | CTO Frontend | Frontend Event Source |
| Desktop 4 | COO | Collector / History / Scheduler / Backup |

---

# 44. Desktop 4 장애

Desktop 4 자체가 고장날 수 있다.

이 경우:

    Local Master 접근 불가

하지만:

    GitHub Backup

이 존재해야 한다.

Reporter Event도 Desktop 4 OFF 기간 동안 손실되지 않는 구조여야 한다.

---

# 45. Desktop 4 복구

새 PC 또는 복구된 Desktop:

    Company Ops 설치
        ↓
    GitHub Backup
        ↓
    Recovery Directory
        ↓
    History 검증
        ↓
    Local Master 복구
        ↓
    Runtime 재구성

자동 Restore는 하지 않는다.

---

# 46. State 파일 손상

예:

    daily_history_state.json

손상.

프로그램이 임의로 모든 History를 삭제하거나 다시 생성하면 안 된다.

처리:

    State Error
        ↓
    Existing Local History 확인
        ↓
    Safe Recovery 필요

---

# 47. State와 History 충돌

예:

State:

    last_successful_daily_close
    =
    2026-08-10

하지만:

    2026-08-10.md
    없음

이 경우 State를 맹신하면 안 된다.

이는:

    STATE_INCONSISTENCY

다.

---

# 48. State Consistency Check

Runner 시작 시 최소 확인 가능:

    State Last Success
        ↓
    Corresponding Local History 존재?

없으면:

    Warning/Error

자동으로 더 진행하기 전에 안전한 복구 판단을 한다.

---

# 49. History가 State보다 우선

Company History에서 공식 원본은:

    Local History File

이다.

Runtime State는 자동화를 위한 보조 데이터다.

따라서 State와 Local History가 충돌하면:

> Runtime State보다 공식 History 보존을 우선한다.

---

# 50. Raw Event 보존

Collector가 Event를 처리했다고 즉시 원본을 완전히 삭제하면 복구가 어려울 수 있다.

V1 Event Lifecycle은 기존 Event 명세의 보존 규칙을 따른다.

최소한 처리 성공 여부를 추적할 수 있어야 한다.

---

# 51. Data Loss Test

Phase 9의 핵심은 기능보다 데이터 손실 검증이다.

다음 질문에 답해야 한다.

    PC OFF → 데이터 남는가?

    Network OFF → 데이터 남는가?

    Notion FAIL → History 남는가?

    GitHub FAIL → Local 남는가?

    Runner Crash → 재개되는가?

    Duplicate → 중복 안 되는가?

    Late Event → 과거 History 반영 가능한가?

---

# 52. Critical Data

V1에서 가장 보호해야 하는 데이터:

1. 중요 Execution Event
2. 공식 Daily History
3. 공식 Monthly History
4. 중요 Decision
5. 주요 Milestone
6. 주요 Risk / Resolution
7. Customer Learning

---

# 53. Non-Critical Data

다음은 손실돼도 회사 History 자체가 훼손되지 않을 수 있다.

    Temporary File

    Cache

    Debug Log

    Working Copy

    Lock File

    재생성 가능한 Runtime Artifact

---

# 54. Recovery Priority

장애 시 우선순위:

    1. Local Master 보호

    2. Raw Event 보호

    3. History 복구

    4. Runtime State 복구

    5. Notion Sync 복구

    6. GitHub Backup 재개

UI나 편의기능보다 데이터 보호를 우선한다.

---

# 55. Notion과 Local 충돌

Notion 상태와 Local History가 다를 수 있다.

예:

Notion:

    Blocker OPEN

History:

    Blocker RESOLVED

이 경우 자동으로 History를 Notion에 맞춰 변경하지 않는다.

두 시스템 역할이 다르기 때문이다.

---

# 56. Current State Sync 오류

Current State 오류는:

    Notion Sync Issue

로 처리한다.

Company History를 자동 변경하는 근거가 되지 않는다.

---

# 57. Manual Correction

자동화가 잘못 기록한 경우 COO가 수정할 수 있어야 한다.

수정 대상:

    Local History

수정 후:

    Backup

가능.

단, 원본 Event 자체를 조용히 위조하지 않는다.

---

# 58. Manual Correction 기록

중요 History 수정이면 최소:

    Last Updated At

또는 수정 추적정보를 남긴다.

V1에서 복잡한 Audit Database까지 만들지는 않는다.

Git Version History도 보조 증거가 된다.

---

# 59. 운영 중 확인해야 할 최소 상태

COO가 확인할 최소 Health 정보:

| 항목 | 의미 |
|---|---|
| Last Runner | 마지막 실행 |
| Last Daily Close | 마지막 Daily 성공 |
| Last Monthly Close | 마지막 Monthly 성공 |
| Pending Events | 미처리 Event |
| Notion Pending | Notion 재시도 |
| Backup Status | Backup 상태 |
| Last Backup | 마지막 성공 Backup |
| Error | 현재 주요 오류 |

---

# 60. 별도 Dashboard 필요 여부

V1에서는 별도 Dashboard를 만들지 않는다.

초기에는:

    State File
    +
    Log
    +
    필요 시 간단한 CLI Summary

정도로 충분하다.

---

# 61. CLI Health Summary

필요하면 Runner 실행 후 다음 정도를 표시할 수 있다.

    COMPANY OPS STATUS

    Last Daily:
    2026-08-10

    Last Monthly:
    2026-07

    Pending Events:
    0

    Notion Pending:
    0

    Backup:
    SUCCESS

    Last Backup:
    2026-08-11 11:05

    Errors:
    NONE

---

# 62. 별도 UI 개발 금지

V1 종료 전에:

    React Dashboard

    Admin Page

    Mobile App

    Monitoring Web

을 만들지 않는다.

운영 필요성이 실제 확인된 이후 판단한다.

---

# 63. Daily 운영 개입 목표

정상 상태에서 COO가 매일 해야 하는 행동:

    없음

을 목표로 한다.

즉:

    PC ON
      ↓
    Runner
      ↓
    자동 처리

정상이라면 수동으로 Event를 취합하거나 History를 작성하지 않는다.

---

# 64. COO가 개입해야 하는 상황

다음은 수동 개입 가능:

    REJECTED 중요 Event

    Backup Remote Conflict

    State Inconsistency

    History Correction

    반복적 Authentication Failure

    실제 데이터 손상 의심

---

# 65. 자동화와 판단의 경계

자동화:

    수집
    분류
    저장
    Sync
    Summary 초안
    Backup
    Catch-up

사람:

    전략
    최종 중요도 판단
    CEO Decision
    Go / No-Go
    정책 변경
    위험한 Restore
    Conflict Resolution

---

# 66. COO 권한

COO는 다음을 운영할 수 있다.

    Company Ops 실행

    History 품질관리

    Notion Execution 관리

    Blocker 추적

    Dependency 관리

    Launch Readiness 취합

    Company Intelligence 기록

하지만:

    Beta Scope 최종 확정

    Pricing 최종 확정

    Launch Date 최종 확정

    Product 전략 변경

등 CEO 권한을 독단적으로 행사하지 않는다.

---

# 67. E2E Test Level

검증을 세 단계로 나눈다.

    LEVEL 1
    Component Validation

    LEVEL 2
    Integrated Pipeline

    LEVEL 3
    Real Environment Validation

V1 완료에는 LEVEL 3까지 필요하다.

---

# 68. LEVEL 1 — Component Validation

각 기능 독립 검증:

    Reporter

    Collector

    Notion Sync

    History Filter

    Daily

    Scheduler

    Backup

    Monthly

각 Phase 완료보고를 기준으로 한다.

---

# 69. LEVEL 2 — Integrated Pipeline

예:

    Mock Event
        ↓
    Collector
        ↓
    Notion
        ↓
    Daily
        ↓
    Backup

전체 자동 흐름 검증.

---

# 70. LEVEL 3 — Real Environment Validation

실제 Desktop에서 실제 업무 Event를 사용한다.

예:

Desktop 3:

    실제 Frontend Milestone 완료

        ↓

Reporter

        ↓

Desktop 4

        ↓

Company Ops

        ↓

Daily History

        ↓

Backup

이 흐름을 검증한다.

---

# 71. Real Environment 최소 검증

최소 다음 두 종류를 권장한다.

    Milestone Event

    Blocker 또는 Decision Event

이를 통해 단순 Event뿐 아니라 회사 History 가치가 높은 Event도 검증한다.

---

# 72. Burn-in 기간

E2E Test 한 번 성공했다고 바로 완전히 안정적이라고 판단하지 않는다.

실제 업무환경에서 일정 기간 돌려본다.

V1에서는 고정적으로 몇 주를 강제하지 않는다.

최소:

    여러 Scheduler Cycle

    PC OFF → ON

    실제 Event 발생

    Backup Cycle

을 경험할 정도로 검증한다.

---

# 73. Burn-in 동안 기능 추가 금지

Burn-in 목적은:

    안정성 검증

이다.

이 기간에 새로운 편의기능을 계속 추가하면 검증 기준이 계속 변한다.

따라서 P0 Bug 외 기능 확장을 억제한다.

---

# 74. Bug Severity

최소:

    P0

    P1

    P2

로 구분할 수 있다.

---

# 75. P0

V1 종료를 막는 문제.

예:

    History 데이터 손실

    Event 영구 손실

    Local Master 손상

    잘못된 자동 삭제

    중복 History 대량 생성

    Backup이 Master를 덮어씀

    Catch-up 불가

---

# 76. P1

운영에는 영향을 주지만 데이터 원본은 안전한 문제.

예:

    Notion Sync 반복 실패

    Backup Pending 반복

    일부 Event 분류 오류

    Scheduler 수동 실행 필요

---

# 77. P2

편의성 문제.

예:

    Log 가독성

    Commit Message 표현

    CLI 출력 개선

    폴더명 개선

P2 때문에 V1 완료를 지연시키지 않는다.

---

# 78. V1 Go 조건

Company Ops V1 운영 시작 최소조건:

    Reporter Event 생성 가능

    Collector 정상

    Daily History 정상

    Scheduler 정상

    Catch-up 정상

    Local Master 보호

    Backup 정상

    주요 Failure Recovery 검증

Monthly는 해당 월이 종료되어야 실제 정규 Cycle 검증이 가능하므로 Mock + 이후 실제 월말 확인을 병행할 수 있다.

---

# 79. V1 No-Go 조건

다음 중 하나라도 존재하면 운영 자동화 기준으로 No-Go:

    Event 손실 가능성 확인

    Local Master 자동 손상 가능

    Catch-up 실패

    Backup이 Master를 변경

    Delete가 Remote에 위험하게 전파

    중복 Event가 History를 지속 오염

    Crash 후 복구 불가

---

# 80. Notion 장애는 V1 No-Go인가?

반드시 그렇지는 않다.

조건:

    Local History 정상
    Event 안전
    Notion Retry 가능

이면:

    조건부 운영 가능

Notion은 공식 History 원본이 아니다.

---

# 81. GitHub 장애는 V1 No-Go인가?

일시적 GitHub 장애 자체는 No-Go가 아니다.

조건:

    Local Master 정상
    Backup Pending 추적
    이후 재시도 가능

이면 운영 가능하다.

다만 장기간 Backup이 전혀 없는 상태는 운영 Risk로 관리한다.

---

# 82. AI 장애는 V1 No-Go인가?

아니다.

Rule-based Fallback이 작동하면 된다.

AI는 V1 핵심 원본 저장 기능의 필수 Dependency가 아니다.

---

# 83. Company Ops Critical Path

V1 구축 Critical Path:

    Event Schema
        ↓
    Reporter
        ↓
    Collector
        ↓
    History Pipeline
        ↓
    Daily History
        ↓
    Scheduler / Catch-up
        ↓
    Backup
        ↓
    E2E Validation

Notion과 Monthly는 중요하지만 원본 History 보존 Critical Path와 구분한다.

---

# 84. 운영 Critical Path

실제 운영 시:

    Event 생성
        ↓
    Event 보존
        ↓
    Collector
        ↓
    History Filter
        ↓
    Local History

여기까지가 핵심이다.

Backup은 안전성을 강화한다.

Notion은 Execution 가시성을 제공한다.

---

# 85. E2E Acceptance Matrix

| 영역 | 필수 | 실패 시 |
|---|---:|---|
| Event 생성 | YES | NO-GO |
| Event 보존 | YES | NO-GO |
| Collector | YES | NO-GO |
| Duplicate 방지 | YES | NO-GO |
| History Filter | YES | 조건에 따라 NO-GO |
| Daily History | YES | NO-GO |
| Local Master | YES | NO-GO |
| Catch-up | YES | NO-GO |
| Scheduler | YES | NO-GO |
| Backup | YES | 조건부 NO-GO |
| Notion | YES | 조건부 운영 가능 |
| Monthly | YES | 실제 월말 후 추가 검증 가능 |
| AI Summary | NO | Fallback |
| Dashboard | NO | V1 제외 |

---

# 86. Full E2E Test Matrix

| Test | 기대 결과 |
|---|---|
| 정상 Milestone Event | 전체 Pipeline PASS |
| 중요하지 않은 Event | History DROP |
| Blocker | Current + History |
| Blocker Resolution | Resolution 연결 |
| CEO Decision | Authority 유지 |
| COO Recommendation | CEO Decision으로 변환 금지 |
| Duplicate Event | 중복 없음 |
| Invalid Event | REJECTED |
| Desktop 4 OFF | Event 보존 |
| Multi-day OFF | Catch-up |
| 11시 PC OFF | Startup Catch-up |
| Runner Crash | State 기반 재개 |
| Stale Lock | 자동 복구 |
| Notion FAIL | Local History 유지 |
| Internet OFF | Local 처리 |
| GitHub FAIL | Backup Pending |
| Remote Divergence | 자동 Pull 없음 |
| Local Delete | Remote 삭제 방지 |
| Working Copy 손상 | Master 안전 |
| Late Event | Daily + Monthly Update |
| Monthly 누락 | Catch-up |
| 여러 달 누락 | 순차 복구 |
| AI FAIL | Rule Fallback |
| Empty Day | 정상 Close |
| Empty Month | 정상 Monthly |
| State 불일치 | 감지 |
| Single File Recovery | 복구 가능 |

---

# 87. 실제 테스트 Evidence

각 핵심 Test는 가능하면 Evidence를 남긴다.

예:

    Test ID

    실행일

    Scenario

    Input Event

    Expected

    Actual

    PASS / FAIL

    관련 Log

    관련 History File

복잡한 QA 시스템은 만들지 않는다.

---

# 88. Test 기록 위치

예:

    D:\DOJOONPASS_COMPANY_OPS\
    tests\
    evidence\

또는 기존 프로젝트 테스트 구조를 사용한다.

새 폴더를 불필요하게 만들지 않는다.

---

# 89. Production이라는 표현

Company Ops는 고객용 Product가 아니라 내부 운영 시스템이다.

따라서 여기서 Production은:

> 실제 회사 업무에 Company Ops를 사용하기 시작한 상태

를 의미한다.

---

# 90. 운영 전환

검증 완료 후:

    TEST MODE
        ↓
    V1 ACCEPTED
        ↓
    LIVE INTERNAL OPERATION

으로 전환한다.

---

# 91. Live 이후 원칙

Live 이후에는:

    Company Ops 개발

보다:

    Company Ops 사용

이 우선이다.

내부 도구를 계속 만드는 것이 회사의 본업이 되어서는 안 된다.

---

# 92. 기능 추가 Gate

V1 이후 새로운 기능은 다음 질문을 통과해야 한다.

    실제 반복 문제가 발생했는가?

    현재 방식으로 해결하기 어려운가?

    추가 자동화가 실제 시간을 줄이는가?

    회사 본업보다 우선할 가치가 있는가?

아니면 추가하지 않는다.

---

# 93. 예: Dashboard

단순히:

    Dashboard가 있으면 멋있다.

는 개발 이유가 아니다.

하지만:

    매일 State 파일 5개를 열어봐야 해서
    반복적으로 시간이 낭비된다.

가 확인되면:

    Dashboard 검토

가능.

---

# 94. 예: Alert

현재는 별도 Alert 시스템 없음.

향후:

    Backup 실패를 여러 번 놓침

    Event Pending을 발견하지 못함

이 반복되면:

    Alert 기능

검토 가능.

---

# 95. 예: AI Agent

AI Agent가 Company Ops 전체를 자동 판단하도록 만드는 것은 V1 이후에도 기본 목표가 아니다.

AI는:

    Summary

    Classification 보조

    Information Extraction

등 명확한 영역에서 사용한다.

경영 권한은 사람에게 남긴다.

---

# 96. V1 종료 후 정상 운영

정상적인 하루:

    Desktop 1~3
    업무 수행
        ↓
    Reporter Event
        ↓
    Desktop 4
    Runner
        ↓
    Current State
        +
    Daily History
        +
    Backup

COO가 별도로 업무 완료내역을 일일이 복붙하지 않는 상태가 목표다.

---

# 97. 정상적인 월초

    1일
    Desktop 4 Runner
        ↓
    전월 마지막 Daily Catch-up
        ↓
    Daily Coverage COMPLETE
        ↓
    Monthly History
        ↓
    Backup

COO는 필요하면 Monthly Summary만 검토한다.

---

# 98. COO 정기 확인

V1 이후 COO가 확인할 것은 시스템 자체보다 회사 운영이다.

Company Ops는 다음 정보를 제공하는 도구다.

    Project State

    Milestone

    Blocker

    Dependency

    Decision

    History

COO는 이를 기반으로 Execution을 관리한다.

---

# 99. CEO 보고와 연결

Company Ops V1 완료 이후 다음과 같은 COO 보고를 지원할 수 있다.

    전사 현황

    CTO 상태

    CMO 상태

    COO 상태

    Blocker

    Critical Path

    Launch Readiness

    CEO Decision Required

하지만 자동으로 CEO의 결정을 대신하지 않는다.

---

# 100. Company Intelligence 장기 구조

V1:

    Raw Event
        ↓
    Daily
        ↓
    Monthly

향후 필요 시:

    Quarterly

    Annual

    Product Evolution Timeline

    Decision Register

    KPI History

    Customer Learning

등으로 확장할 수 있다.

현재는 만들지 않는다.

---

# 101. M&A / IPO 관점

장기적으로 의미 있는 기록:

    주요 Decision

    주요 Milestone

    Product Evolution

    핵심 KPI 변화

    중요한 Customer Learning

    전략 변경

    핵심 Risk / Resolution

    중요 계약/IP/재무 증빙

Company Ops는 이 중 실행 및 Product History 기반을 담당한다.

모든 회사 자료를 Company Ops에 넣으려 하지 않는다.

---

# 102. Final V1 Acceptance Criteria

다음 항목이 모두 충족되면 Company Ops V1을 완료로 판단할 수 있다.

### Core

    [ ] Reporter 작동

    [ ] Event Schema Validation

    [ ] Collector 작동

    [ ] Duplicate Protection

    [ ] Event Persistence

### Current State

    [ ] Notion Sync

    [ ] Notion Failure Isolation

### History

    [ ] History Filter

    [ ] Daily History

    [ ] Empty Day

    [ ] Late Event

    [ ] Local Master

### Automation

    [ ] 11:00 Scheduler

    [ ] Startup Catch-up

    [ ] Multi-day Catch-up

    [ ] Process Lock

    [ ] Crash Recovery

### Backup

    [ ] Working Copy Separation

    [ ] Automatic Backup

    [ ] Delete Protection

    [ ] No Automatic Pull

    [ ] No Force Push

    [ ] Recovery Test

### Monthly

    [ ] Monthly Generation

    [ ] Monthly Catch-up

    [ ] Daily Coverage Check

    [ ] Monthly Late Update

### Real Environment

    [ ] 실제 Desktop Event 테스트

    [ ] Desktop OFF 테스트

    [ ] Network 장애 테스트

    [ ] External Service 장애 테스트

    [ ] Data Loss Test

---

# 103. V1 PASS 기준

V1 PASS:

    모든 Core P0 항목 PASS

그리고:

    알려진 데이터 손실 위험 없음

그리고:

    실제 Desktop 환경 E2E PASS

P1/P2가 일부 남아 있어도 V1을 종료할 수 있다.

---

# 104. V1 BLOCKED 기준

다음 중 하나라도 있으면:

    Event 손실

    History 손실

    Master 손상

    Catch-up 불가

    Crash Recovery 불가

    Backup 위험

    중복 오염

V1은 BLOCKED다.

---

# 105. V1 완료 후 하지 말아야 할 것

PASS 직후:

    V2 Dashboard 개발

    AI Agent 개발

    KPI 플랫폼 개발

    BI 개발

    Quarterly 자동화

를 바로 시작하지 않는다.

먼저 실제 운영한다.

---

# 106. V1 완료 후 관찰

실제 사용하면서 다음을 본다.

    무엇이 실제로 자주 실패하는가?

    무엇을 COO가 계속 수동으로 하는가?

    어떤 데이터가 실제로 유용한가?

    어떤 기록은 쓸모없는가?

    Notion이 실제로 필요한가?

    Monthly Summary가 실제로 유용한가?

이 Evidence를 기반으로 V2 여부를 결정한다.

---

# 107. V2는 자동 시작하지 않는다

V1 완료:

    ≠

V2 개발 시작

V2는 실제 운영 Evidence가 있어야 한다.

---

# 108. Company Ops 개발 Stop Rule

다음이 충족되면 개발을 멈춘다.

    Event 자동 수집

        ↓

    Current State 자동 반영

        ↓

    중요 History 자동 선별

        ↓

    Daily 자동 생성

        ↓

    Monthly 자동 생성

        ↓

    Local Master

        ↓

    Backup

        ↓

    PC OFF Catch-up

        ↓

    장애 후 복구

이 구조가 실제 환경에서 안정적으로 작동하면:

> Company Ops는 개발 프로젝트에서 운영 도구로 전환한다.

---

# 109. Phase 9 완료 보고 형식

    [Phase]
    Phase 9 — End-to-End Validation & Operations

    [상태]
    PASS / BLOCKED / FAIL

    [검증 환경]

    [검증 Desktop]

    [Component Validation]
    PASS / FAIL

    [Integrated Pipeline]
    PASS / FAIL

    [Real Environment]
    PASS / FAIL

    [Reporter]
    PASS / FAIL

    [Collector]
    PASS / FAIL

    [Notion Sync]
    PASS / FAIL

    [History Pipeline]
    PASS / FAIL

    [Daily History]
    PASS / FAIL

    [Scheduler]
    PASS / FAIL

    [Catch-up]
    PASS / FAIL

    [Monthly History]
    PASS / FAIL

    [Backup]
    PASS / FAIL

    [Recovery]
    PASS / FAIL

    [Desktop OFF]
    PASS / FAIL

    [Network Failure]
    PASS / FAIL

    [Notion Failure]
    PASS / FAIL

    [GitHub Failure]
    PASS / FAIL

    [Crash Recovery]
    PASS / FAIL

    [Duplicate Protection]
    PASS / FAIL

    [Late Event]
    PASS / FAIL

    [Data Loss Risk]
    NONE / FOUND

    [P0 Bugs]

    [P1 Bugs]

    [P2 Bugs]

    [Evidence]

    [남은 문제]

    [COO 판단]
    V1 ACCEPT / V1 BLOCKED

    [CEO Decision Required]
    NONE 또는 해당 사항

---

# 110. V1 최종 상태

Phase 9 PASS 이후 문서 상태:

    DOJOONPASS COMPANY OPS
    V1
    =
    LIVE INTERNAL OPERATION

이 시점부터 Company Ops의 기본 목적은 개발이 아니라 실제 사용이다.

---

# 111. 최종 아키텍처

    ┌───────────────────────────────┐
    │         DESKTOP 1             │
    │ CTO Backend / Crawler/Search  │
    └───────────────┬───────────────┘
                    │
                    │
    ┌───────────────▼───────────────┐
    │         DESKTOP 2             │
    │       CMO / Content OS        │
    └───────────────┬───────────────┘
                    │
                    │
    ┌───────────────▼───────────────┐
    │         DESKTOP 3             │
    │         CTO Frontend          │
    └───────────────┬───────────────┘
                    │
                    ▼
              EXECUTION EVENTS
                    │
                    ▼
            SHARED EVENT LAYER
                    │
                    ▼
    ┌───────────────────────────────┐
    │         DESKTOP 4             │
    │              COO              │
    │                               │
    │          COMPANY OPS          │
    │                               │
    │ Collector                     │
    │ Notion Sync                   │
    │ History Pipeline              │
    │ Daily History                 │
    │ Monthly History               │
    │ Scheduler / Catch-up          │
    │ Backup                        │
    └───────────────┬───────────────┘
                    │
          ┌─────────┼─────────┐
          ▼         ▼         ▼
       NOTION     LOCAL     GITHUB
       CURRENT    MASTER    BACKUP
       STATE      HISTORY
          │         │
          ▼         ▼
      EXECUTION   COMPANY
      MANAGEMENT  INTELLIGENCE
                    BASE

---

# 112. 최종 운영 원칙

### RULE 1

    NOTION
    =
    CURRENT STATE

### RULE 2

    LOCAL
    =
    COMPANY HISTORY MASTER

### RULE 3

    GITHUB
    =
    BACKUP

### RULE 4

    REPORTER
    =
    EVENT SOURCE

### RULE 5

    COMPANY OPS
    =
    EXECUTION INTELLIGENCE PIPELINE

### RULE 6

    AUTOMATION
    DOES NOT MAKE CEO DECISIONS

### RULE 7

    DATA SAFETY
    BEFORE CONVENIENCE

### RULE 8

    V1 COMPLETION
    BEFORE V2 EXPANSION

---

# 113. 최종 원칙

Company Ops를 만드는 목적은:

    회사를 관리하는 프로그램을 만드는 것

그 자체가 아니다.

목적은:

> CTO·CMO 및 회사 전체에서 실제로 일어난 중요한 변화가 자동으로 모이고, COO가 회사의 현재 상태를 파악하며, CEO가 필요한 결정을 내릴 수 있도록 하고, 동시에 회사의 중요한 History가 장기적으로 사라지지 않게 만드는 것.

이다.

Company Ops가 이 목적을 달성하면 더 만들지 않는다.

회사의 본업인:

    DOJOONPASS Product
        ↓
    Beta
        ↓
    Customer Validation
        ↓
    Launch
        ↓
    PMF

에 다시 집중한다.

---

# END OF DOCUMENT