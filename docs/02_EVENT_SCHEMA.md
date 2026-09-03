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
    AT_RISK
    BLOCKED
    RESUMED
    MILESTONE_COMPLETED
    COMPLETED
    CANCELLED
    ISSUE_RAISED
    ASSIGNED
    ISSUE_RESOLVED
    DECISION_REQUIRED
    DECISION_APPROVED
    DECISION_REJECTED
    EXECUTED

이외의 Event Type은 V1에서 정상 Event로 처리하지 않는다.

### 11.1 Lifecycle 대칭성 (C149)

이 목록의 앞 여덟 개는 전부 **과거형**이었다. 끝난 일, 해결된 일,
승인된 일만 적을 수 있었고, 그 앞에 오는 **열린 상태**는 적을 자리가
없었다. 회사가 실제로 관리하는 것은 결과가 아니라 열린 상태다.

세 Lifecycle이 절반만 있었다.

    Issue      ISSUE_RESOLVED   -> 시작이 없어서 Issue Aging이
                                   구현되지 않은 것이 아니라
                                   원리적으로 계산 불가능했다.
    Decision   DECISION_APPROVED-> §19가 "CEO Decision Required를
                                   DECISION_APPROVED로 적지 말라"고
                                   명시하면서 적을 곳을 주지 않았다.
                                   거절도 같다.
    Project    BLOCKED          -> 이미 멈춘 것만 있었다. 아직
                                   움직이지만 멈출 것 같은 상태는
                                   "정상" 또는 "정지" 둘 중 하나로만
                                   보고할 수 있었다.

추가된 넷은 정확히 그 셋을 닫는다. 새 필드는 하나도 늘리지 않았고,
넷 다 이미 있는 Event Type을 본떴다.

    ISSUE_RAISED       §18과 대칭. 고유 Property 없음(§28형).
    ASSIGNED           §18의 **가운데**. 고유 Property 없음(§28형).
    DECISION_REQUIRED  §19와 대칭. 고유 Property 없음(§28형).
    DECISION_REJECTED  §19와 대칭. 고유 Property 없음(§28형).
    EXECUTED           §19의 **뒤쪽** 끝. 고유 Property 없음(§28형).
    AT_RISK            자기 status를 고정하는 상태 Event(§25·§26형).

Decision Lifecycle은 두 구간이며, 승인은 그 **가운데**다.

    DECISION_REQUIRED ──(결정 대기)── DECISION_APPROVED ──(실행 대기)── EXECUTED
                       └─────────────  DECISION_REJECTED  (여기서 끝)

`DECISION_APPROVED`가 Lifecycle을 닫는다고 보면, **승인됐지만 아무도 하지
않은 결정**이 모든 목록에서 사라진다 — 그것이 문제가 되기 시작하는 바로 그
시점에. `EXECUTED`가 그 뒤쪽 끝이다.

`DECISION_REJECTED`는 실행 대기를 열지 않는다. 거절은 질문을 닫고 할 일을
남기지 않으므로, 여기에 넣으면 모든 "아니오"가 영원히 미완료 작업으로
보고된다.

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

`CEO Decision Required`는 이제 적을 자리가 있다 — `DECISION_REQUIRED`(§19.2)다.

---

## 18.1 ASSIGNED

의미:

> 열려 있는 Issue 또는 승인된 Decision을 어느 Team이 맡았다.

예:

    {
      "event_type": "ASSIGNED",
      "status": "IN_PROGRESS",
      "summary": "checkout drop-off — frontend가 가져감"
    }

Notion:

    반영 (공통 필드만)

History:

    기본 제외 (docs/05 §26 — 진행이지 성과가 아니다)

**누가 맡았는지는 이 Event의 `role`이다.** docs/02 §8이 source→role을
고정하므로 그 값은 실제로 그 일을 받은 팀이며, 다른 팀이 대신 주장하면
PairMismatch로 잡힌다. 별도 담당자 필드를 만들지 않는다.

이 Event는 아무것도 열지 않고 아무것도 닫지 않는다. Issue Aging의 기준은
여전히 `ISSUE_RAISED`의 시각이다 — 배정이 시계를 되돌리면 "얼마나 오래
열려 있었나"가 조용히 "배정된 지 얼마나 됐나"로 바뀐다.

없을 때 무엇이 안 되는가:

> 아무도 맡지 않은 Issue와 누군가 붙어 있는 Issue가 목록에서 똑같이
> 보인다. 그 둘은 반대 상황이고 다음 행동이 다르며, aging 목록은 바로
> 그것을 구분하려고 읽는다.

주의: `ASSIGNED`는 PROJECTS Row의 `Owner`를 바꾸지 않는다. `Owner`는 §9-12에
따라 최초 생성 시점 정보이며, 담당 이동은 Control Tower가 나른다.

---

## 19.0 EXECUTED

의미:

> 승인된 Decision이 실제로 실행되었다.

예:

    {
      "event_type": "EXECUTED",
      "status": "IN_PROGRESS",
      "summary": "closed beta scope cut to 3 features; sprint replanned"
    }

Notion:

    반영

History:

    자동 Candidate

`DECISION_APPROVED`(§19)의 뒤쪽 끝이며, Execution Aging의 시작 시각은
승인 시각이다.

**승인은 일이 아니다.** 이 Event가 없으면 "정해 놓고 하지 않은 것"을 셀
수 없고, 그것은 회사가 멈추는 가장 흔한 방식 중 하나다.

이 Event 자체는 Project의 상태를 바꾸지 않는다. 실행이 Project 상태를
바꿨다면 그것을 말하는 Event(§15 MILESTONE_COMPLETED, §16 COMPLETED 등)를
따로 보고한다.

---

## 19.1 AT_RISK

의미:

> 프로젝트가 아직 진행 중이지만, 알려진 사유로 멈출 가능성이 크다.

예:

    {
      "event_type": "AT_RISK",
      "status": "AT_RISK",
      "summary": "vendor contract renewal unsigned; blocks launch in 2 weeks"
    }

Notion:

    반영 (Status = AT_RISK)

History:

    조건부 Candidate (REVIEW)

`BLOCKED`와의 차이:

    BLOCKED   이미 멈췄다. 재개하려면 blocker가 해소되어야 한다.
    AT_RISK   아직 움직인다. 지금 개입하면 멈추지 않을 수 있다.

`AT_RISK`는 `status = AT_RISK`를 요구한다. 상태를 정하는 Event가
동시에 다른 상태를 주장하면 아무도 행동할 수 없기 때문이다.
`COMPLETED`(§25)·`CANCELLED`(§26)와 같은 규칙이다.

위험의 내용은 `summary`에 적는다. `blocker`를 쓰지 않는다 —
`blocker`는 §22가 "무엇이 멈췄는가"에 배정한 필드이고, 아직 멈추지
않은 것에 그 필드를 쓰면 두 Event Type이 같은 사실을 주장하게 된다.

위험이 해소되면 `RESUMED` 또는 다음 정상 Event가 `status`를 되돌린다.
위험이 현실이 되면 `BLOCKED`를 보고한다.

---

## 19.2 DECISION_REQUIRED

의미:

> CEO 권한에 해당하는 Decision이 필요하며, 아직 확정되지 않았다.

예:

    {
      "event_type": "DECISION_REQUIRED",
      "status": "IN_PROGRESS",
      "summary": "Closed Beta scope: 3 features or 5? blocks sprint planning"
    }

Notion:

    반영

History:

    자동 Candidate

이 Event는 Decision Aging의 **시작 시각**이다. 이것이 없으면
"결정이 며칠째 밀려 있는가"는 계산할 수 없다.

`DECISION_APPROVED` 또는 `DECISION_REJECTED`가 같은 `project_id`로
도착하면 그 Decision은 닫힌 것으로 본다.

Company Ops가 Decision을 만들거나 재촉하지 않는다. 이미 존재하는
"결정이 필요한 상태"를 기록할 뿐이다.

---

## 19.3 DECISION_REJECTED

의미:

> CEO 권한에 해당하는 Decision이 실제로 거절되었다.

예:

    {
      "event_type": "DECISION_REJECTED",
      "status": "IN_PROGRESS",
      "summary": "Q4 paid ads budget increase rejected"
    }

Notion:

    반영

History:

    자동 Candidate

`DECISION_APPROVED`와 완전히 대칭이다. 거절을 기록하지 않으면
실제로는 끝난 Decision이 영원히 Pending으로 남는다 — 거절을 승인으로
적는 것은 거짓이고, 적지 않는 것도 거짓이다.

---

## 19.4 ISSUE_RAISED

의미:

> 프로젝트 또는 서비스에 중요한 영향을 주는 Issue가 제기되었다.

예:

    {
      "event_type": "ISSUE_RAISED",
      "status": "IN_PROGRESS",
      "summary": "auction_item sync drifts under concurrent writes"
    }

Notion:

    반영

History:

    자동 Candidate

`ISSUE_RESOLVED`(§18)의 시작이며, Issue Aging의 시작 시각이다.

Issue가 제기되었다는 것이 곧 프로젝트가 멈췄다는 뜻은 아니다.
멈췄다면 그것을 말하는 Event는 §22의 `BLOCKED`다. 따라서
`ISSUE_RAISED`는 `Blocker` Property를 쓰지 않는다.

사소한 Bug 보고마다 사용하지 않는다 — §18과 같은 기준이다.

---

## 20. status

V1 기본 Status:

    NOT_STARTED
    IN_PROGRESS
    AT_RISK
    BLOCKED
    COMPLETED
    CANCELLED

`AT_RISK`(C149)는 IN_PROGRESS와 BLOCKED 사이에 있다 — 아직 움직이지만
멈출 가능성이 크다. Event Type이자 Status인 이유는 `BLOCKED`와 같다:
프로젝트는 그 위험을 언급하지 않는 이후 Event들을 가로질러 계속
위험한 상태이고, 그렇게 살아남는 필드는 `status`다.

Notion 쪽 마이그레이션은 필요 없다. `src/notion/bootstrap.py`가 `Status`를
고정 option 없는 `{"select": {}}`로 선언하므로 첫 쓰기에서 option이
생성된다.

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
| AT_RISK | 조건부 |
| BLOCKED | 조건부 |
| RESUMED | false |
| MILESTONE_COMPLETED | true |
| COMPLETED | 조건부 |
| CANCELLED | 조건부 |
| ISSUE_RAISED | true |
| ASSIGNED | false |
| ISSUE_RESOLVED | true |
| DECISION_REQUIRED | true |
| DECISION_APPROVED | true |
| DECISION_REJECTED | true |
| EXECUTED | true |

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
| AT_RISK | AT_RISK (강제) |
| BLOCKED | BLOCKED |
| RESUMED | IN_PROGRESS |
| MILESTONE_COMPLETED | IN_PROGRESS 또는 COMPLETED |
| COMPLETED | COMPLETED |
| CANCELLED | CANCELLED |
| ISSUE_RAISED | IN_PROGRESS 또는 AT_RISK 또는 BLOCKED |
| ASSIGNED | 현재 프로젝트 상태 유지 |
| ISSUE_RESOLVED | IN_PROGRESS 또는 COMPLETED |
| DECISION_REQUIRED | 현재 프로젝트 상태 유지 |
| DECISION_APPROVED | 현재 프로젝트 상태 유지 |
| DECISION_REJECTED | 현재 프로젝트 상태 유지 |
| EXECUTED | 현재 프로젝트 상태 유지 |

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