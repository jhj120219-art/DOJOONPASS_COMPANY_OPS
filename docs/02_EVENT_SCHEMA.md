# D:\DOJOONPASS_COMPANY_OPS\docs\02_EVENT_SCHEMA.md

## DOJOONPASS Company Ops — Execution Event Schema

---

## 1. 문서 정의

| 항목 | 내용 |
|---|---|
| 문서명 | Execution Event Schema |
| 프로젝트 | DOJOONPASS Company Ops |
| Owner | COO |
| 상위 문서 | `00_V1_DEVELOPMENT_SPEC.md` |
| 실행 기준 | `01_V1_IMPLEMENTATION_PLAN.md` |
| 목적 | Desktop 1~4에서 발생하는 업무 상태를 Company Ops가 동일한 규격으로 처리하기 위한 표준 정의 |
| 적용 버전 | V1 |

본 문서는 Company Ops의 Reporter, Collector, Notion Sync, History Pipeline이 공통으로 사용하는 Execution Event 규격을 정의한다.

본 문서는 새로운 Company Ops 기능을 추가하지 않는다.

---

## 2. Execution Event 정의

Execution Event란 각 Desktop에서 발생한 의미 있는 업무 상태 변화를 Company Ops 중앙 시스템으로 전달하기 위한 표준 데이터 단위다.

기본 흐름:

    실제 업무
        ↓
    의미 있는 상태 변화
        ↓
    Reporter
        ↓
    Execution Event
        ↓
    GitHub
        ↓
    Desktop 4 Collector

Execution Event는 모든 작업 로그를 의미하지 않는다.

Company Ops가 전사 Execution 상태를 파악하기 위해 필요한 사건만 Event로 생성한다.

---

## 3. 기본 Schema

모든 Execution Event는 다음 구조를 따른다.

    {
      "schema_version": "1.0",
      "event_id": "UUID",
      "timestamp": "2026-08-01T20:31:00+09:00",
      "source": "DESKTOP_3",
      "role": "CTO_FRONTEND",
      "project_id": "SEARCH_FRONTEND",
      "event_type": "MILESTONE_COMPLETED",
      "status": "COMPLETED",
      "milestone": "Search UI",
      "summary": "Search UI implementation completed",
      "blocker": null,
      "evidence": [
        "TypeScript PASS"
      ],
      "history_candidate": true
    }

---

## 4. 필드 정의

| Field | Type | 필수 | 설명 |
|---|---|---:|---|
| schema_version | string | YES | Event Schema 버전 |
| event_id | string | YES | Event 고유 식별자 |
| timestamp | string | YES | Event 발생 시각 |
| source | string | YES | Event 발생 Desktop |
| role | string | YES | 담당 역할 |
| project_id | string | YES | 프로젝트 식별자 |
| event_type | string | YES | Event 종류 |
| status | string | YES | 해당 시점의 업무 상태 |
| milestone | string/null | 조건부 | 관련 Milestone |
| summary | string | YES | Event 핵심 내용 |
| blocker | string/null | 조건부 | 현재 Blocker |
| evidence | array | 조건부 | 완료/상태를 검증할 Evidence |
| history_candidate | boolean | YES | History 검토 대상 여부 |

---

## 5. schema_version

V1 기본값:

    "schema_version": "1.0"

목적:

- 향후 Event 규격 변경 추적
- 구버전 Event 식별
- Reporter와 Collector 간 호환성 확인

V1에서는 `1.0`만 지원한다.

지원하지 않는 Schema Version의 Event는 정상 처리하지 않고 별도 오류 대상으로 분류한다.

---

## 6. event_id

`event_id`는 모든 Event를 구분하는 고유 식별자다.

기본 원칙:

    하나의 실제 Event
          =
    하나의 event_id

동일한 `event_id`가 다시 전달되면 새로운 Event로 처리하지 않는다.

사용 목적:

- 중복 방지
- 재전송 안전성
- 처리 여부 추적
- 오류 복구

권장 생성 방식:

    UUID

예:

    "event_id": "550e8400-e29b-41d4-a716-446655440000"

테스트 Event는 별도 식별 가능한 ID를 사용할 수 있다.

예:

    "event_id": "TEST-001"

Production Event에서 수동 순번 ID를 사용하지 않는다.

---

## 7. timestamp

Event가 실제 발생한 시간을 기록한다.

형식:

    ISO-8601

한국 시간 예:

    2026-08-01T20:31:00+09:00

단순 날짜만 저장하지 않는다.

잘못된 예:

    2026-08-01

    8월 1일

    오후 8시

Collector가 실행된 시간이 아니라 실제 Event 발생 시간을 기준으로 한다.

---

## 8. source

Event가 발생한 물리적 업무환경을 나타낸다.

V1 허용값:

    DESKTOP_1
    DESKTOP_2
    DESKTOP_3
    DESKTOP_4

현재 역할:

| source | 현재 역할 |
|---|---|
| DESKTOP_1 | CTO Backend / Crawler / Search |
| DESKTOP_2 | CMO / Content OS |
| DESKTOP_3 | CTO Frontend |
| DESKTOP_4 | COO / Company Ops |

`source`와 `role`은 동일한 개념이 아니다.

source:

> 어느 컴퓨터에서 발생했는가?

role:

> 어떤 조직 역할의 업무인가?

를 의미한다.

---

## 9. role

Event의 업무 Owner 역할을 나타낸다.

V1 기본 Role:

    CTO_BACKEND
    CTO_FRONTEND
    CMO
    COO

필요성이 실제로 확인되기 전까지 Role을 추가하지 않는다.

CEO Decision이 기록되는 경우에도 자동화 프로그램이 CEO 역할을 대신한다는 의미가 아니다.

CEO의 실제 확정 Decision을 기록할 필요가 있을 경우 별도 승인된 Event로 처리한다.

---

## 10. project_id

Event가 어느 프로젝트에 속하는지 식별한다.

예:

    SEARCH_FRONTEND

    AUCTION_CRAWLER

    SEARCH_API

    CONTENT_OS

    COMPANY_OPS

`project_id`는 사람이 읽는 프로젝트 제목과 다를 수 있다.

원칙:

- 영문 대문자
- 공백 사용 금지
- 단어 구분은 `_`
- 동일 프로젝트는 항상 동일 ID 사용
- 프로젝트 이름이 조금 바뀌었다고 ID를 새로 만들지 않는다

예:

    SEARCH_FRONTEND

Notion Sync는 `project_id`를 기준으로 동일 프로젝트를 식별한다.

---

## 11. event_type

Event의 성격을 정의한다.

V1 허용값:

    STARTED
    BLOCKED
    RESUMED
    MILESTONE_COMPLETED
    COMPLETED
    CANCELLED
    ISSUE_RESOLVED
    DECISION_APPROVED

이외의 Event Type은 V1에서 정상 Event로 처리하지 않는다.

---

## 12. STARTED

의미:

> 주요 프로젝트 또는 Milestone 작업이 실제 시작됨.

예:

    {
      "event_type": "STARTED",
      "status": "IN_PROGRESS"
    }

Notion:

    반영

History:

    기본 제외

단순 파일 작업 시작이나 작은 Task 시작마다 생성하지 않는다.

---

## 13. BLOCKED

의미:

> 현재 업무 진행을 막는 실제 Blocker가 발생함.

예:

    {
      "event_type": "BLOCKED",
      "status": "BLOCKED",
      "blocker": "auction → auction_item synchronization failure"
    }

Notion:

    반영

History:

    조건부 Candidate

모든 Bug를 BLOCKED로 처리하지 않는다.

실제로 Milestone 또는 프로젝트 진행을 중단시키는 문제여야 한다.

---

## 14. RESUMED

의미:

> 기존 Blocker가 해소되어 업무 진행이 다시 가능해짐.

예:

    {
      "event_type": "RESUMED",
      "status": "IN_PROGRESS",
      "blocker": null
    }

Notion:

    반영

History:

    기본 제외

중요한 Blocker 해결 자체를 History에 남겨야 한다면 `ISSUE_RESOLVED`를 사용한다.

---

## 15. MILESTONE_COMPLETED

의미:

> 사전에 정의되었거나 프로젝트상 의미 있는 Milestone이 완료됨.

예:

    {
      "event_type": "MILESTONE_COMPLETED",
      "status": "IN_PROGRESS",
      "milestone": "Search UI",
      "summary": "Search UI implementation completed"
    }

프로젝트 전체가 완료된 것은 아니므로 상황에 따라 `status`는 `IN_PROGRESS`일 수 있다.

Notion:

    반영

History:

    자동 Candidate

---

## 16. COMPLETED

의미:

> 해당 프로젝트 또는 관리 대상 주요 업무가 완료됨.

예:

    {
      "event_type": "COMPLETED",
      "status": "COMPLETED"
    }

Notion:

    반영

History:

    조건부 Candidate

`COMPLETED`라는 이유만으로 Company History에 자동 확정하지 않는다.

---

## 17. CANCELLED

의미:

> 진행 중이던 주요 프로젝트 또는 업무가 공식적으로 중단됨.

예:

    {
      "event_type": "CANCELLED",
      "status": "CANCELLED"
    }

Notion:

    반영

History:

    조건부 Candidate

단순 Task 삭제는 해당하지 않는다.

중요 프로젝트의 중단이나 방향 변경에 사용한다.

---

## 18. ISSUE_RESOLVED

의미:

> 프로젝트 또는 서비스에 중요한 영향을 주던 Issue가 해결됨.

예:

    {
      "event_type": "ISSUE_RESOLVED",
      "status": "IN_PROGRESS",
      "summary": "auction_item synchronization issue resolved"
    }

Notion:

    반영

History:

    자동 Candidate

사소한 Bug 수정마다 사용하지 않는다.

---

## 19. DECISION_APPROVED

의미:

> CEO 권한에 해당하는 중요 Decision이 실제로 확정됨.

예:

    {
      "event_type": "DECISION_APPROVED",
      "status": "IN_PROGRESS",
      "summary": "Closed Beta scope approved by CEO"
    }

History:

    자동 Candidate

중요:

Company Ops가 Decision을 생성하거나 승인하는 것이 아니다.

실제로 CEO가 확정한 Decision을 기록하기 위한 Event다.

다음과 같은 상태는 `DECISION_APPROVED`로 기록하지 않는다.

    COO 추천

    CTO 의견

    CMO 의견

    검토 중

    CEO Decision Required

CEO의 실제 확정이 존재해야 한다.

---

## 20. status

V1 기본 Status:

    NOT_STARTED
    IN_PROGRESS
    BLOCKED
    COMPLETED
    CANCELLED

Status는 Event Type과 동일한 개념이 아니다.

예:

    event_type = MILESTONE_COMPLETED
    status = IN_PROGRESS

가능하다.

이는:

> Milestone 하나는 완료됐지만 프로젝트 전체는 아직 진행 중

이라는 의미다.

---

## 21. milestone

관련 Milestone을 기록한다.

예:

    "milestone": "Search UI"

Milestone과 관계없는 Event는:

    "milestone": null

을 사용할 수 있다.

Milestone에는 지나치게 세부적인 작업명을 사용하지 않는다.

좋은 예:

    Search UI

    Search API Integration

    Production Deployment

좋지 않은 예:

    button margin 수정

    파일명 변경

    console.log 삭제

---

## 22. summary

Event의 핵심 내용을 짧고 사실 중심으로 기록한다.

좋은 예:

    "Search UI implementation completed"

    "Search API blocked by auction_item synchronization issue"

좋지 않은 예:

    "오늘 정말 많은 작업을 진행했고 여러 가지를 수정했다"

Summary는 감상이나 업무일지가 아니다.

---

## 23. blocker

현재 진행을 막는 문제가 있을 경우 기록한다.

Blocker가 없으면:

    null

예:

    "blocker": null

Blocker가 있으면:

    "blocker": "auction_item synchronization mismatch"

를 사용한다.

BLOCKED Event에서는 blocker 값이 필수다.

---

## 24. evidence

Event가 실제로 발생했다는 근거다.

형식:

    [
      "TypeScript PASS",
      "Search API integration test PASS"
    ]

가능한 Evidence 예:

- TypeScript PASS
- Unit Test PASS
- Integration Test PASS
- E2E PASS
- Deployment Success
- 실제 생성 파일
- 실제 API 응답
- Commit Hash
- Pull Request
- 실행 로그
- 공식 승인 기록

Evidence가 없는 상태를 프로그램이 임의로 PASS로 만들지 않는다.

---

## 25. history_candidate

History 검토 대상 여부를 나타낸다.

값:

    true

또는:

    false

기본 규칙:

| Event Type | 기본값 |
|---|---:|
| STARTED | false |
| BLOCKED | 조건부 |
| RESUMED | false |
| MILESTONE_COMPLETED | true |
| COMPLETED | 조건부 |
| CANCELLED | 조건부 |
| ISSUE_RESOLVED | true |
| DECISION_APPROVED | true |

`history_candidate = true`는 공식 History 확정을 의미하지 않는다.

의미:

> Daily History 생성 과정에서 장기 보존 가치 검토 대상

이다.

---

## 26. Event Type과 Status 관계

기본 관계:

| Event Type | 일반적인 Status |
|---|---|
| STARTED | IN_PROGRESS |
| BLOCKED | BLOCKED |
| RESUMED | IN_PROGRESS |
| MILESTONE_COMPLETED | IN_PROGRESS 또는 COMPLETED |
| COMPLETED | COMPLETED |
| CANCELLED | CANCELLED |
| ISSUE_RESOLVED | IN_PROGRESS 또는 COMPLETED |
| DECISION_APPROVED | 현재 프로젝트 상태 유지 |

명백하게 모순되는 조합은 Validation에서 거부한다.

예:

    event_type = COMPLETED
    status = NOT_STARTED

→ REJECT

---

## 27. 정상 Event 예시 — STARTED

    {
      "schema_version": "1.0",
      "event_id": "TEST-START-001",
      "timestamp": "2026-08-01T09:00:00+09:00",
      "source": "DESKTOP_3",
      "role": "CTO_FRONTEND",
      "project_id": "SEARCH_FRONTEND",
      "event_type": "STARTED",
      "status": "IN_PROGRESS",
      "milestone": "Search UI",
      "summary": "Search UI implementation started",
      "blocker": null,
      "evidence": [],
      "history_candidate": false
    }

---

## 28. 정상 Event 예시 — BLOCKED

    {
      "schema_version": "1.0",
      "event_id": "TEST-BLOCK-001",
      "timestamp": "2026-08-01T13:00:00+09:00",
      "source": "DESKTOP_1",
      "role": "CTO_BACKEND",
      "project_id": "AUCTION_DATA_SYNC",
      "event_type": "BLOCKED",
      "status": "BLOCKED",
      "milestone": "auction_item synchronization",
      "summary": "Auction item update synchronization is blocked",
      "blocker": "Existing auction_item values are not consistently updated",
      "evidence": [
        "Synchronization test failed"
      ],
      "history_candidate": true
    }

---

## 29. 정상 Event 예시 — MILESTONE_COMPLETED

    {
      "schema_version": "1.0",
      "event_id": "TEST-MILESTONE-001",
      "timestamp": "2026-08-01T20:00:00+09:00",
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

---

## 30. 정상 Event 예시 — COMPLETED

    {
      "schema_version": "1.0",
      "event_id": "TEST-COMPLETE-001",
      "timestamp": "2026-08-02T18:00:00+09:00",
      "source": "DESKTOP_3",
      "role": "CTO_FRONTEND",
      "project_id": "SEARCH_FRONTEND",
      "event_type": "COMPLETED",
      "status": "COMPLETED",
      "milestone": "Search Frontend Integration",
      "summary": "Search frontend implementation completed",
      "blocker": null,
      "evidence": [
        "TypeScript PASS",
        "Integration Test PASS"
      ],
      "history_candidate": true
    }

---

## 31. Validation Rules

Collector는 최소 다음을 검증한다.

### 필수 필드

다음 필드가 없으면 REJECT:

    schema_version
    event_id
    timestamp
    source
    role
    project_id
    event_type
    status
    summary
    history_candidate

### Enum 검증

다음은 정의된 값만 허용한다.

    source
    role
    event_type
    status

### Timestamp

ISO-8601 형식이어야 한다.

### BLOCKED

다음 조건 필요:

    event_type = BLOCKED
            ↓
    blocker != null

### COMPLETED

다음 조건 필요:

    event_type = COMPLETED
            ↓
    status = COMPLETED

### CANCELLED

다음 조건 필요:

    event_type = CANCELLED
            ↓
    status = CANCELLED

---

## 32. Validation 결과

Event 처리 결과는 최소 다음 세 상태로 구분한다.

    ACCEPTED

    DUPLICATE

    REJECTED

### ACCEPTED

Schema와 Validation을 통과한 새로운 Event.

### DUPLICATE

이미 처리된 `event_id`.

재처리하지 않는다.

### REJECTED

Schema 또는 Validation을 통과하지 못한 Event.

삭제하지 않는다.

오류 확인이 가능하도록 보존한다.

---

## 33. 중복 처리

중복 판단 기본 Key:

    event_id

예:

    TEST-001
       ↓
    최초 입력
       ↓
    ACCEPTED

동일 Event 재입력:

    TEST-001
       ↓
    DUPLICATE
       ↓
    처리하지 않음

동일 Event가 Notion이나 History에 두 번 반영되어서는 안 된다.

---

## 34. Event 수정 금지 원칙

이미 발생한 Event의 원본 내용을 자동으로 수정하지 않는다.

잘못된 Event가 생성된 경우 기존 Event를 조용히 덮어쓰기하는 구조를 사용하지 않는다.

V1에서는 오류 Event를 REJECTED 상태로 보존하고 원인을 확인할 수 있도록 한다.

향후 정정 Event가 실제로 필요하다고 확인되면 별도 버전에서 검토한다.

---

## 35. Event와 Notion 관계

Execution Event는 Event Log다.

Notion은 Current State다.

따라서:

    Event 1 STARTED
          ↓
    Event 2 BLOCKED
          ↓
    Event 3 RESUMED
          ↓
    Event 4 COMPLETED

이 발생하더라도 Notion에 Project Row 4개를 만들지 않는다.

`project_id`를 기준으로 기존 Row를 갱신한다.

최종:

    Project
    SEARCH_FRONTEND

    Status
    COMPLETED

---

## 36. Event와 History 관계

모든 Event가 History가 되는 것은 아니다.

    Execution Event
          ↓
    history_candidate?
       /        \
    false       true
      ↓           ↓
    종료      History Filter
                  ↓
             중요도 검토
                  ↓
             Daily History

Execution Event는 업무 상태 전달 데이터다.

Company History는 장기 보존할 회사 기록이다.

둘을 동일하게 취급하지 않는다.

---

## 37. 생성하지 않는 Event

다음은 기본적으로 Execution Event를 생성하지 않는다.

- 일반 Git Commit
- 파일 생성
- 파일명 변경
- CSS 조정
- 단순 코드 정리
- console.log 제거
- 일반 검색
- 단순 조사
- AI와의 일반 대화
- 작은 Bug 수정
- 테스트 한 번 실행
- 문서 오탈자 수정
- 단순 질문/답변
- 업무 중간 저장

목적은 Event 수집량을 늘리는 것이 아니다.

회사의 Execution 상태를 파악하는 데 필요한 신호만 수집한다.

---

## 38. Event 생성 기준

다음 질문 중 하나 이상이 YES라면 Event 생성 후보가 될 수 있다.

    프로젝트 상태가 바뀌었는가?

    주요 Milestone이 완료됐는가?

    실제 Blocker가 발생했는가?

    Blocker가 해결됐는가?

    주요 프로젝트가 완료됐는가?

    주요 프로젝트가 중단됐는가?

    중요한 Issue가 해결됐는가?

    CEO의 중요 Decision이 확정됐는가?

모두 NO라면 Event를 생성하지 않는 것을 기본으로 한다.

---

## 39. Reporter 책임

Reporter는 다음까지만 책임진다.

    업무 상태 감지
        ↓
    Event 생성
        ↓
    Schema Validation
        ↓
    Event 전달

Reporter가 다음을 판단하지 않는다.

- 전사 Critical Path
- Launch Readiness
- Go / No-Go
- 전략
- Beta Scope
- Pricing
- Target
- 최종 우선순위

Reporter는 사실 전달 계층이다.

---

## 40. Collector 책임

Collector는 다음을 책임진다.

    Event 수집
        ↓
    Validation
        ↓
    중복 확인
        ↓
    처리 상태 기록
        ↓
    Notion / History Pipeline 전달

Collector가 Event의 사실을 임의로 변경하지 않는다.

---

## 41. COO/CEO 권한 보호

Execution Event 자동화가 경영 의사결정 자동화로 확대되어서는 안 된다.

특히 다음 항목은 Event 데이터만으로 자동 확정하지 않는다.

    Critical Path

    Launch Readiness

    COO Recommendation

    Go / No-Go

    Beta Scope

    Pricing

    Target

    Launch Date

Company Ops는 판단을 위한 데이터를 제공한다.

최종 권한 구조는 기존 회사 공식 문서를 따른다.

---

## 42. Mock Test Set

Phase 2에서는 최소 다음 Mock Event를 준비한다.

    TEST-START-001
    TEST-BLOCK-001
    TEST-RESUME-001
    TEST-MILESTONE-001
    TEST-COMPLETE-001

추가 Validation Test:

    TEST-DUPLICATE-001
    TEST-INVALID-TYPE-001
    TEST-MISSING-FIELD-001
    TEST-INVALID-STATUS-001
    TEST-INVALID-TIMESTAMP-001

---

## 43. Phase 1 완료 기준

본 Schema를 코드로 구현한 후 최소 다음 결과가 확인되어야 한다.

| Test | 기대 결과 |
|---|---|
| 정상 STARTED | ACCEPTED |
| 정상 BLOCKED | ACCEPTED |
| 정상 RESUMED | ACCEPTED |
| 정상 MILESTONE_COMPLETED | ACCEPTED |
| 정상 COMPLETED | ACCEPTED |
| 중복 event_id | DUPLICATE |
| 필수 Field 누락 | REJECTED |
| 잘못된 Event Type | REJECTED |
| 잘못된 Status | REJECTED |
| 잘못된 Timestamp | REJECTED |
| BLOCKED인데 blocker 없음 | REJECTED |
| COMPLETED인데 status 불일치 | REJECTED |

모든 핵심 Validation Test가 통과해야 Phase 1을 PASS로 판단한다.

---

## 44. 변경 원칙

V1 개발 중 Event Field나 Event Type을 임의로 계속 추가하지 않는다.

새로운 값이 필요해 보이면 먼저 다음을 확인한다.

    기존 Schema로 표현 가능한가?
            ↓
           YES
            ↓
         추가 금지

            ↓ NO

    V1 운영에 반드시 필요한가?
            ↓
        YES / NO

V1에 반드시 필요한 경우에만 Schema 변경을 검토한다.

---

## 45. 본 문서 이후 실행

본 문서는 새로운 개발 Phase를 추가하지 않는다.

`01_V1_IMPLEMENTATION_PLAN.md`의 다음 순서를 그대로 따른다.

현재 실행:

    Phase 0
    Project Initialization

완료 후:

    Phase 1
    Execution Event Core

Phase 1 구현 시 본 문서를 Event Schema의 기준으로 사용한다.

---

# END OF DOCUMENT