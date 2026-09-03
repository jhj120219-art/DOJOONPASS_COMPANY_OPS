# D:\DOJOONPASS_COMPANY_OPS\docs\04_NOTION_SYNC_SPEC.md

## DOJOONPASS Company Ops — Notion Sync Specification

---

## 1. 문서 정의

| 항목 | 내용 |
|---|---|
| 문서명 | Company Ops Notion Sync Specification |
| 프로젝트 | DOJOONPASS Company Ops |
| Owner | COO |
| 상위 문서 | `00_V1_DEVELOPMENT_SPEC.md` |
| 실행 기준 | `01_V1_IMPLEMENTATION_PLAN.md` |
| Event 기준 | `02_EVENT_SCHEMA.md` |
| Collector 기준 | `03_COLLECTOR_SPEC.md` |
| 목적 | Execution Event를 기반으로 Notion의 현재 전사 Execution 상태를 자동 갱신하는 기준 정의 |
| 실행 위치 | Desktop 4 |
| 적용 버전 | V1 |

본 문서는 Company Ops가 Notion을 전사 Execution Current State Layer로 사용하는 방법을 정의한다.

Notion은 Company History의 공식 원본이 아니다.

Notion의 핵심 질문은 다음과 같다.

> 지금 도준패스의 주요 프로젝트가 어떤 상태인가?

---

## 2. Notion의 역할

Company Ops에서 Notion은 다음 역할을 담당한다.

- 현재 진행 중인 핵심 Project 확인
- Owner 확인
- 현재 Status 확인
- Current Milestone 확인
- 현재 Blocker 확인
- 최근 상태변경 시각 확인
- 완료된 Project 확인
- COO가 전사 상태를 빠르게 파악할 수 있는 Current State 제공

Notion은 업무 원본 로그 저장소가 아니다.

Notion은 모든 Execution Event를 누적하는 Event Store도 아니다.

---

## 3. 전체 구조

기본 데이터 흐름:

    Desktop 업무
        ↓
    Reporter
        ↓
    Execution Event
        ↓
    Collector
        ↓
    Validation
        ↓
    ACCEPTED
        ↓
    Notion Sync
        ↓
    Project Current State

History는 별도 흐름이다.

    ACCEPTED EVENT
        ↓
    History Pipeline
        ↓
    Daily History
        ↓
    Local Master

따라서:

    Notion
      =
    현재

    Local History
      =
    역사

로 역할을 분리한다.

---

## 4. V1 Notion 최소 구조

V1에서는 Notion Database를 과도하게 늘리지 않는다.

기본적으로 하나의 핵심 Database를 사용한다.

    PROJECTS

목적:

> 회사의 현재 핵심 Project 상태 관리

필요성이 확인되지 않은 별도 Database는 V1에서 만들지 않는다.

---

## 5. PROJECTS Database

PROJECTS Database는 하나의 Project당 하나의 Row를 가진다.

예:

| Project | Owner | Status | Current Milestone | Blocker |
|---|---|---|---|---|
| Auction Data Sync | CTO Backend | BLOCKED | auction_item Sync | DB synchronization issue |
| Search Frontend | CTO Frontend | IN_PROGRESS | Search API Integration | - |
| Content OS | CMO | IN_PROGRESS | Engine Development | - |
| Company Ops | COO | IN_PROGRESS | Collector | - |

실제 값은 Execution Event를 기준으로 갱신한다.

---

## 6. 핵심 원칙

다음 구조를 사용하지 않는다.

    Event 1
    → Row 생성

    Event 2
    → Row 생성

    Event 3
    → Row 생성

이렇게 하면 Notion이 Event Log가 된다.

V1의 구조는 다음과 같다.

    project_id
        ↓
    Project Row 검색
        ↓
    기존 Row 있음?
      /        \
    YES        NO
     ↓          ↓
    UPDATE     CREATE

즉 동일 프로젝트의 상태변화는 동일 Row에 반영한다.

---

## 7. Primary Key

Notion Project 식별 기준:

    project_id

예:

    SEARCH_FRONTEND

    AUCTION_DATA_SYNC

    CONTENT_OS

    COMPANY_OPS

Project Name이 아니라 `project_id`를 시스템 식별자로 사용한다.

---

## 8. PROJECTS 기본 Property

V1에서는 최소 다음 Property를 사용한다.

| Property | Type | 목적 |
|---|---|---|
| Project | Title | 사람이 읽는 Project 이름 |
| Project ID | Text | 시스템 식별자 |
| Owner | Select | 현재 담당 역할 |
| Source | Select | 주요 실행 Desktop |
| Status | Status 또는 Select | 현재 Project 상태 |
| Current Milestone | Text | 현재 또는 최근 주요 Milestone |
| Blocker | Text | 현재 Blocker |
| Last Updated | Date | 마지막 상태변경 |
| Completed Date | Date | 완료일 |
| Last Event ID | Text | 마지막 반영 Event |
| Last Event Type | Select | 마지막 Event 종류 |

이보다 많은 Property는 실제 필요성이 확인될 때 추가한다.

---

## 9. Project

사람이 읽을 수 있는 프로젝트명이다.

예:

    Search Frontend

    Auction Data Sync

    Content OS

    Company Ops

Project 이름은 표시용이다.

시스템 식별에는 `Project ID`를 사용한다.

---

## 10. Project ID

Execution Event의:

    project_id

를 저장한다.

예:

    SEARCH_FRONTEND

Project ID는 동일 프로젝트에 대해 변경하지 않는 것을 원칙으로 한다.

---

## 11. Owner

Execution Event의 `role`을 사람이 읽기 쉬운 형태로 매핑한다.

예:

    CTO_BACKEND
        ↓
    CTO Backend

    CTO_FRONTEND
        ↓
    CTO Frontend

    CMO
        ↓
    CMO

    COO
        ↓
    COO

---

## 12. Source

Execution Event의 `source`를 저장한다.

예:

    DESKTOP_1
    DESKTOP_2
    DESKTOP_3
    DESKTOP_4

목적은 프로젝트의 주요 실행 환경을 확인하는 것이다.

---

## 13. Status

V1 기본 Status:

    NOT_STARTED
    IN_PROGRESS
    BLOCKED
    COMPLETED
    CANCELLED

Execution Event의 `status`를 기준으로 갱신한다.

임의의 진행률 %는 사용하지 않는다.

예:

    72%

    85%

같은 추정값은 만들지 않는다.

COO 운영 원칙과 동일하게 Milestone과 실제 상태를 기준으로 관리한다.

---

## 14. Current Milestone

Event의 `milestone` 정보를 반영한다.

예:

    Search UI

    Search API Integration

    auction_item Synchronization

    Collector

Milestone이 없는 Event가 들어왔다고 기존 유효 Milestone을 무조건 null로 덮어쓰지 않는다.

---

## 15. Blocker

현재 실제 Blocker를 표시한다.

BLOCKED Event:

    blocker 값 반영

RESUMED 또는 Blocker 해결:

    Blocker 제거

예:

    BLOCKED
        ↓
    "auction_item synchronization mismatch"

이후:

    RESUMED
        ↓
    Blocker 제거

---

## 16. Last Updated

현재 Project 상태에 마지막으로 정상 반영된 Event의 `timestamp`를 저장한다.

중요:

Collector 실행시간이 아니라 Event 발생시간을 기준으로 한다.

예:

    Event Timestamp
    2026-08-01 18:30

    Collector
    2026-08-02 11:00

Notion Last Updated:

    2026-08-01 18:30

---

## 17. Completed Date

다음 조건에서 설정한다.

    event_type = COMPLETED
        AND
    status = COMPLETED

Completed Date는 Event Timestamp를 기준으로 한다.

프로젝트가 다시 열리는 특수 상황은 V1에서 자동 처리 규칙을 복잡하게 만들지 않는다.

실제 사례가 발생하면 별도 검토한다.

---

## 18. Last Event ID

마지막으로 Notion Current State에 반영된:

    event_id

를 저장한다.

목적:

- 중복 확인 보조
- 상태 추적
- 문제 발생 시 Event 역추적
- Debugging

---

## 19. Last Event Type

마지막으로 Current State를 변경한 Event Type을 저장한다.

예:

    STARTED
    BLOCKED
    RESUMED
    MILESTONE_COMPLETED
    COMPLETED
    CANCELLED
    ISSUE_RESOLVED

---

## 20. Event별 기본 처리

| Event Type | Notion 처리 |
|---|---|
| STARTED | Project 생성 또는 IN_PROGRESS 갱신 |
| BLOCKED | Status BLOCKED + Blocker 반영 |
| RESUMED | Status IN_PROGRESS + Blocker 제거 |
| MILESTONE_COMPLETED | Milestone/Last Updated 갱신 |
| COMPLETED | Status COMPLETED + Completed Date |
| CANCELLED | Status CANCELLED |
| ISSUE_RESOLVED | 관련 상태 갱신 |
| DECISION_APPROVED | Project Current State에 필요한 경우만 반영 |

---

## 21. STARTED 처리

입력:

    event_type = STARTED
    status = IN_PROGRESS

Project가 없으면:

    CREATE

Project가 있으면:

    UPDATE

기본 결과:

    Status
    IN_PROGRESS

    Last Updated
    Event Timestamp

    Last Event ID
    Event ID

---

## 22. BLOCKED 처리

입력:

    event_type = BLOCKED

결과:

    Status
    BLOCKED

    Blocker
    Event.blocker

    Last Updated
    Event.timestamp

COO는 이를 통해 현재 전사 Blocker를 확인할 수 있다.

---

## 23. RESUMED 처리

입력:

    event_type = RESUMED

결과:

    Status
    IN_PROGRESS

    Blocker
    EMPTY

    Last Updated
    Event.timestamp

---

## 24. MILESTONE_COMPLETED 처리

입력:

    event_type = MILESTONE_COMPLETED

결과:

    Current Milestone
    Event.milestone

    Last Updated
    Event.timestamp

Project 전체 Status는 Event의 status를 따른다.

Milestone 완료가 Project 완료를 의미하지 않는다.

---

## 25. COMPLETED 처리

입력:

    event_type = COMPLETED

결과:

    Status
    COMPLETED

    Completed Date
    Event.timestamp

    Blocker
    EMPTY

    Last Updated
    Event.timestamp

---

## 26. CANCELLED 처리

입력:

    event_type = CANCELLED

결과:

    Status
    CANCELLED

    Last Updated
    Event.timestamp

취소 이유가 Summary에 존재하면 Event 원본에서 확인할 수 있다.

V1에서는 Cancellation Reason Property를 별도로 만들지 않는다.

---

## 27. ISSUE_RESOLVED 처리

ISSUE_RESOLVED는 상황에 따라 Current State를 갱신한다.

예:

기존:

    Status
    BLOCKED

해결 후:

    ISSUE_RESOLVED
    status = IN_PROGRESS

결과:

    Status
    IN_PROGRESS

    Blocker
    EMPTY

단, 모든 ISSUE_RESOLVED가 Project 상태를 변경한다고 가정하지 않는다.

Event의 실제 status를 따른다.

---

## 28. DECISION_APPROVED 처리

`DECISION_APPROVED`는 기본적으로 Company History 가치가 높은 Event다.

그러나 모든 Decision을 PROJECTS Database Property로 만들지 않는다.

Decision이 특정 Project의 Current State를 변경하는 경우에만 관련 상태를 반영할 수 있다.

중요 Decision의 장기 기록은 History Pipeline이 담당한다.

---

## 28.1 AT_RISK / ISSUE_RAISED / DECISION_REQUIRED / DECISION_REJECTED 처리 (C149)

넷 다 공통 필드(Status / Last Updated / Last Event ID / Last Event Type)
외에 **추가 Property가 없다**. §28의 DECISION_APPROVED와 같은 자리다.

`AT_RISK`는 §26의 CANCELLED와 정확히 같다 — 자기 `status`를 고정하는
것이 전부이고, 그 값은 공통 필드의 `Status`가 이미 쓴다. 위험의 내용은
`summary`에 있고, PROJECTS Database의 Property가 되지 않는다.

`ISSUE_RAISED`는 **`Blocker`를 쓰지 않는다**. 제기된 Issue가 곧
프로젝트를 멈춘 것은 아니다. 멈췄다면 그것을 말하는 Event는 §22의
`BLOCKED`이며, 여기서 `Blocker`를 쓰면 두 Event Type이 같은 사실을
주장하게 된다 — §27이 ISSUE_RESOLVED에 대해 피한 바로 그 겹침이다.

`DECISION_REQUIRED` / `DECISION_REJECTED` / `EXECUTED`는 §28과 대칭이므로
같은 이유로 추가 Property가 없다. `EXECUTED`는 승인된 Decision이 실제로
실행됐다는 사실이며, 그 자체로 Project 상태를 바꾸지 않는다 — 바꿨다면
그것을 말하는 Event(§24 MILESTONE_COMPLETED, §25 COMPLETED)를 따로 보고한다.

---

## 29. Late Event 보호

Notion은 Current State이므로 과거 Event가 최신 상태를 역전시키면 안 된다.

예:

현재:

    Last Updated
    2026-08-03 18:00

새로 도착한 Event:

    timestamp
    2026-08-02 10:00

이 경우:

    Event 자체
    ACCEPTED

    History
    검토 가능

하지만:

    Notion Current State
    과거로 되돌리지 않음

을 기본 원칙으로 한다.

---

## 30. Update 조건

Notion Current State Update 전에 비교한다.

    Incoming Event Timestamp
              ↓
    Current Last Updated와 비교
              ↓
    더 최신인가?
       /           \
     YES           NO
      ↓             ↓
    UPDATE      Current State
                변경하지 않음

단, History Pipeline은 별도 처리한다.

---

## 31. 동일 Timestamp

동일 Timestamp Event가 존재하는 특수 상황에서는 Event 간 임의 우선순위를 복잡하게 만들지 않는다.

가능하면 Reporter가 실제 Event 발생시간을 충분한 정밀도로 기록한다.

충돌 사례가 실제 발생하면 별도 규칙을 검토한다.

---

## 32. Notion Sync 결과 상태

최소 다음 상태를 구분한다.

    NOTION_UPDATED

    NOTION_CREATED

    NOTION_SKIPPED_OLD_EVENT

    NOTION_RETRY_REQUIRED

    NOTION_FAILED

---

## 33. NOTION_CREATED

해당 `project_id`가 존재하지 않아 새로운 Project Row를 생성한 상태.

---

## 34. NOTION_UPDATED

기존 Project Row를 정상 갱신한 상태.

---

## 35. NOTION_SKIPPED_OLD_EVENT

정상 Event이지만 Current State보다 오래된 Event이므로 Notion 상태 갱신을 생략한 상태.

Event 자체는 실패가 아니다.

History Pipeline 처리는 계속 가능하다.

---

## 36. NOTION_RETRY_REQUIRED

Notion API 일시 오류 등으로 재시도가 필요한 상태.

Event를 삭제하지 않는다.

---

## 37. NOTION_FAILED

설정 오류 등으로 현재 자동 처리가 불가능한 상태.

원인을 Log에 남긴다.

Event 원본은 유지한다.

---

## 38. Notion API 실패

예:

    Event
      ↓
    Collector PASS
      ↓
    Notion API FAIL

이 경우:

    Event 삭제 금지

    Event REJECTED 금지

    History 처리 가능

    Notion Retry 필요

Notion 장애 때문에 Company History Pipeline 전체가 중단될 필요는 없다.

---

## 39. Notion과 History 장애 분리

다음 구조를 지향한다.

    Event
      ↓
    Collector
      ↓
    ┌──────────────┐
    ↓              ↓
    Notion       History

예:

    Notion
    FAIL

    History
    PASS

가능해야 한다.

반대도 가능하다.

두 후속 시스템의 장애를 서로 묶지 않는다.

---

## 40. Secret 관리

Notion API Token은 코드에 직접 입력하지 않는다.

금지:

    notion_token = "secret_xxxxxxxxx"

환경변수 또는 별도 Secret 설정을 사용한다.

예:

    NOTION_API_TOKEN

    NOTION_PROJECTS_DATABASE_ID

실제 Secret 파일은 Git에 Commit하지 않는다.

---

## 41. .env 원칙

필요한 경우:

    .env

또는:

    .env.local

을 사용할 수 있다.

`.gitignore`에 반드시 포함한다.

예:

    .env
    .env.local

공유용 예시는:

    .env.example

로 관리할 수 있다.

실제 Token 값은 포함하지 않는다.

---

## 42. Notion Permission

Company Ops Integration에는 필요한 최소 권한만 부여한다.

기본적으로 필요한 것은:

- PROJECTS Database 읽기
- PROJECTS Database 생성/수정

불필요하게 전체 Workspace 접근권한을 주지 않는다.

---

## 43. 수동 입력과 자동 입력

PROJECTS Database는 자동화를 기본으로 한다.

그러나 COO가 모든 Property를 자동화할 필요는 없다.

V1에서는 다음을 자동화한다.

    Project
    Project ID
    Owner
    Source
    Status
    Current Milestone
    Blocker
    Last Updated
    Completed Date
    Last Event ID
    Last Event Type

---

## 44. 자동화하지 않는 COO 판단

다음은 Event만으로 자동 결정하지 않는다.

    Critical Path

    Launch Readiness

    COO Recommendation

    Go / No-Go Opinion

    CEO Decision Required

이 정보가 향후 Notion에 필요하다면 COO 관리영역으로 별도 설계할 수 있다.

V1 Event Sync가 자동으로 값을 생성하지 않는다.

---

## 45. CEO 권한 보호

다음은 Notion Sync가 자동 확정하지 않는다.

    Beta Scope

    Target

    Pricing

    Launch Date

    Final Go / No-Go

    Major Product Priority

    Company Strategy

Execution Event는 경영 판단의 근거가 될 수 있지만 결정 그 자체가 아니다.

---

## 46. View 구성

V1에서는 새로운 Dashboard를 개발하지 않고 Notion View를 활용한다.

최소 다음 View를 사용할 수 있다.

    ALL PROJECTS

    ACTIVE

    BLOCKED

    COMPLETED

복잡한 View는 실제 필요성이 확인된 후 추가한다.

---

## 47. ALL PROJECTS

전체 핵심 Project 확인용.

표시 권장:

    Project
    Owner
    Status
    Current Milestone
    Blocker
    Last Updated

---

## 48. ACTIVE

Filter:

    Status = IN_PROGRESS

목적:

현재 실제 진행 중인 Project 확인.

---

## 49. BLOCKED

Filter:

    Status = BLOCKED

목적:

COO가 현재 전사 Blocker를 빠르게 확인.

COO Execution 관리에서 가장 중요한 View 중 하나다.

---

## 50. COMPLETED

Filter:

    Status = COMPLETED

목적:

최근 완료 Project 확인.

단, 장기 회사 History는 이 View가 아니라 Local History에서 관리한다.

---

## 51. 완료 Project 처리

완료 Project를 즉시 삭제하지 않는다.

Status:

    COMPLETED

로 유지한다.

향후 데이터가 너무 많아질 경우 Archive 정책을 별도로 검토한다.

V1에서는 자동 Archive 기능을 만들지 않는다.

---

## 52. Project 생성 정책

Reporter가 새로운 `project_id`를 보내면 무조건 모든 것을 새로운 회사 핵심 Project로 등록하는 구조는 피해야 한다.

Reporter 단계에서 Event 생성 기준을 지킨다.

Project는 전사 Execution 관리 가치가 있는 단위여야 한다.

예:

좋음:

    SEARCH_FRONTEND

    AUCTION_DATA_SYNC

    CONTENT_OS

    COMPANY_OPS

지나치게 세부적:

    BUTTON_MARGIN_FIX

    README_TYPO

    CONSOLE_LOG_DELETE

---

## 53. Notion 데이터 과잉 방지

Notion은 다음 정보를 저장하지 않는다.

- 모든 Commit
- 모든 파일 수정
- 모든 AI 대화
- 모든 테스트 실행
- 모든 작은 Task
- 모든 Debugging Log
- 모든 Execution Event 원문

Notion은 Current State Layer다.

---

## 54. Event 원문 위치

Event 원문은 Company Ops Runtime에서 추적한다.

예:

    runtime\
    └─ events\
        └─ processed\

Notion에는 Current State에 필요한 정보만 반영한다.

---

## 55. Sync Logging

Notion Sync는 최소 다음을 Log에 기록한다.

    event_id

    project_id

    sync timestamp

    result

예:

    EVENT
    TEST-001

    PROJECT
    SEARCH_FRONTEND

    NOTION RESULT
    UPDATED

---

## 56. 민감정보 Logging 금지

다음은 Log에 출력하지 않는다.

    NOTION_API_TOKEN

    Authorization Header

    Secret

    Password

---

## 57. Mock Test 1 — Project Create

입력:

    event_type = STARTED

    project_id = SEARCH_FRONTEND

Notion에 Project가 없음.

기대 결과:

    New Row Created

    Project ID
    SEARCH_FRONTEND

    Status
    IN_PROGRESS

결과:

    NOTION_CREATED

---

## 58. Mock Test 2 — Blocker

기존:

    SEARCH_FRONTEND
    IN_PROGRESS

입력:

    event_type = BLOCKED

기대:

    Status
    BLOCKED

    Blocker
    Event.blocker

결과:

    NOTION_UPDATED

---

## 59. Mock Test 3 — Resume

기존:

    Status
    BLOCKED

입력:

    RESUMED

기대:

    Status
    IN_PROGRESS

    Blocker
    EMPTY

---

## 60. Mock Test 4 — Milestone

입력:

    MILESTONE_COMPLETED

    milestone
    Search UI

기대:

    Current Milestone
    Search UI

    Last Updated
    Event timestamp

---

## 61. Mock Test 5 — Complete

입력:

    COMPLETED

기대:

    Status
    COMPLETED

    Completed Date
    Event timestamp

    Blocker
    EMPTY

---

## 62. Mock Test 6 — Duplicate

동일 Event를 Collector가 다시 전달하려는 상황.

Collector에서:

    DUPLICATE

처리되어 Notion Sync 자체가 반복 실행되지 않는 것이 기본이다.

방어적으로 동일 Last Event ID를 확인할 수도 있다.

---

## 63. Mock Test 7 — Late Event

현재 Notion:

    Last Updated
    2026-08-05 18:00

입력 Event:

    timestamp
    2026-08-04 10:00

기대:

    Current State 변경 없음

결과:

    NOTION_SKIPPED_OLD_EVENT

Event 자체는 History Pipeline으로 전달 가능.

---

## 64. Mock Test 8 — API Failure

Notion API를 사용할 수 없는 상황을 재현한다.

기대:

    Event 원본 유지

    Collector Event 삭제 없음

    Retry 가능

    History Pipeline 독립 처리 가능

---

## 65. Test Matrix

| Test | 기대 결과 |
|---|---|
| 신규 STARTED | CREATE |
| 기존 Project STARTED | UPDATE |
| BLOCKED | Status + Blocker 갱신 |
| RESUMED | IN_PROGRESS + Blocker 제거 |
| MILESTONE_COMPLETED | Milestone 갱신 |
| COMPLETED | Completed 처리 |
| CANCELLED | Cancelled 처리 |
| Old Event | Current State 유지 |
| Duplicate | 중복 Update 없음 |
| API Failure | Event 보존 |
| Invalid Event | Collector에서 차단 |

---

## 66. Phase 3 완료 기준

다음이 실제로 검증되면 Phase 3을 PASS할 수 있다.

1. Notion API 연결 성공
2. PROJECTS Database 접근 성공
3. 신규 Project 자동 생성
4. 기존 Project 자동 검색
5. 기존 Project 자동 Update
6. BLOCKED 반영
7. RESUMED 반영
8. Milestone 반영
9. COMPLETED 반영
10. Late Event 보호
11. Duplicate Update 방지
12. API 실패 시 Event 보존
13. Secret이 Repository에 저장되지 않음

---

## 67. 구현 위치

예상 코드 위치:

    D:\DOJOONPASS_COMPANY_OPS\
    └─ notion\

구체적인 파일구조는 구현 시 최소한으로 결정한다.

파일을 미리 지나치게 세분화하지 않는다.

---

## 68. V1에서 만들지 않는 것

Notion V1에서는 다음을 만들지 않는다.

- 별도 Web Dashboard
- 복잡한 Executive Dashboard
- 자동 Critical Path 계산
- 자동 Launch Readiness 점수
- 자동 Go / No-Go
- 자동 CEO Decision
- KPI Dashboard (단, §68.1 참조)
- VOC Dashboard
- M&A Dashboard
- IPO Dashboard
- 직원 평가
- 근태 관리
- Task Management 전체 대체
- Event Log 전체 저장
- AI 경영판단 Agent

---

## 68.1 KPI에 대한 단서 (C149)

§68의 "KPI Dashboard"는 **Notion 안에 KPI를 선언하고 목표를 입력하는
Database를 만들지 않는다**는 뜻이다. 그 금지는 유지된다 — docs/14 §1이
Notion을 "View이며 절대 Source가 아니다"로 고정하기 때문이다.

Event Evidence에서 **파생된** 수를 Notion 페이지에 문장으로 싣는 것은
여기 해당하지 않는다. `src/controltower/rollup.py`가 이미 그렇게 하고
있고(KPI 층은 선언되지 않고 계산된다), C149의 `src/controltower/kpi.py`도
같다: 계산할 수 없는 KPI는 숫자 대신 `DATA REQUIRED`를 싣는다.

금지선은 "Notion에 KPI가 보이는가"가 아니라 "Notion이 KPI의 Source인가"다.

---

## 69. Notion의 한계

Notion은 Company Ops 전체 시스템이 아니다.

구조:

    Notion
      =
    Current Execution View

따라서 Notion이 장애가 나더라도:

    Event
    History
    Local Master

가 손실되어서는 안 된다.

Notion을 Single Source of Truth로 사용하지 않는다.

---

## 70. Source of Truth 구분

Company Ops에서는 데이터 종류에 따라 원본을 구분한다.

### Execution Event

    Company Ops Event 원본

### Current Execution

    Notion

### Company History

    Desktop 4 Local Master

### 개발 명세

    GitHub Repository / docs

하나의 도구에 모든 데이터를 넣지 않는다.

---

## 71. COO 운영 관점

COO가 Notion을 열었을 때 가장 먼저 확인해야 할 것은 다음이다.

    무엇이 진행 중인가?

    무엇이 막혔는가?

    누가 담당하는가?

    현재 Milestone은 무엇인가?

    마지막 변화는 언제인가?

이 질문에 답할 수 있으면 V1 Notion 목적은 달성한 것이다.

---

## 72. 문서 변경 원칙

새로운 Notion Property가 필요해 보일 경우 바로 추가하지 않는다.

먼저 확인한다.

    COO가 현재 Execution을
    관리하는 데 반드시 필요한가?
            ↓
        YES / NO

NO라면 추가하지 않는다.

Notion Database가 다시 복잡한 회사 관리 시스템으로 커지는 것을 방지한다.

---

## 73. Phase 관계

현재 구현 순서:

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

본 문서는 Phase 3의 구현 기준이다.

---

## 74. 완료 보고 형식

Phase 3 종료 시 다음 형식으로 보고한다.

    [Phase]
    Phase 3 — Notion Sync

    [상태]
    PASS / BLOCKED / FAIL

    [구현 내용]

    [Notion Database]

    [변경 파일]

    [실제 테스트]

    [Project Create]
    PASS / FAIL

    [Project Update]
    PASS / FAIL

    [BLOCKED]
    PASS / FAIL

    [RESUMED]
    PASS / FAIL

    [MILESTONE]
    PASS / FAIL

    [COMPLETED]
    PASS / FAIL

    [Late Event Protection]
    PASS / FAIL

    [Duplicate Protection]
    PASS / FAIL

    [API Failure Protection]
    PASS / FAIL

    [Evidence]

    [발견된 문제]

    [다음 작업]

    [CEO/COO Decision Required]
    NONE 또는 해당 사항

---

## 75. 다음 명세

Notion Sync 이후 다음 처리 계층은:

    History Pipeline

이다.

다음 문서:


    D:\DOJOONPASS_COMPANY_OPS\
    docs\
    05_HISTORY_PIPELINE_SPEC.md

History Pipeline에서는 다음을 정의한다.

    어떤 Event를 회사 History로 볼 것인가?

    어떤 Event를 버릴 것인가?

    Daily History Candidate를 어떻게 만들 것인가?

    Decision / Milestone / Issue / Learning을 어떻게 구분할 것인가?

단, History Pipeline은 모든 업무를 기록하는 시스템이 되어서는 안 된다.

장기적으로 경영 판단, 투자, M&A/IPO 실사 등에 의미가 있을 수 있는 회사 사건을 선별하는 것이 목적이다.

---

# END OF DOCUMENT