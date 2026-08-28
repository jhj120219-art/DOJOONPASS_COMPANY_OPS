# D:\DOJOONPASS_COMPANY_OPS\docs\11_DEPLOYMENT_RUNBOOK.md

## DOJOONPASS Company Ops — Deployment & Operations Runbook

---
10_E2E_OPERATIONS_SPEC.md
## 1. 문서 정의

| 항목 | 내용 |
|---|---|
| 문서명 | Company Ops Deployment & Operations Runbook |
| 프로젝트 | DOJOONPASS Company Ops |
| Owner | COO |
| 기준 명세 | `00_V1_DEVELOPMENT_SPEC.md` ~ `10_E2E_OPERATIONS_SPEC.md` |
| 목적 | Company Ops V1을 실제 Desktop 환경에 설치·검증·운영 전환하기 위한 실행 절차 정의 |
| 중앙 실행 PC | Desktop 4 |
| Event Source | Desktop 1 / 2 / 3 |
| 운영 상태 | V1 Deployment |
| 문서 성격 | 운영 Runbook |
| 신규 기능 정의 | 금지 |

본 문서는 새로운 기능을 정의하지 않는다.

목적은 이미 확정된 Company Ops V1을 실제 환경에 배포하는 것이다.

---

# 2. 가장 중요한 원칙

이 문서 이후에는:

    문서 추가
        ↓
    문서 추가
        ↓
    기능 추가

를 반복하지 않는다.

다음 단계는:

    구현
      ↓
    테스트
      ↓
    실제 Desktop 설치
      ↓
    E2E 검증
      ↓
    운영

이다.

---

# 3. V1 Deployment 목표

최종적으로 다음 구조가 실제로 작동해야 한다.

    Desktop 1 ─┐
               │
    Desktop 2 ─┼─→ Execution Event
               │
    Desktop 3 ─┘
                     ↓
                 Desktop 4
                     ↓
                 Collector
                     ↓
              ┌──────┴──────┐
              ↓             ↓
           Notion        History
           Current        Local
           State          Master
                            ↓
                         Backup
                            ↓
                         GitHub

---

# 4. Deployment 원칙

한 번에 Desktop 4대를 모두 설치하지 않는다.

권장:

    Core
      ↓
    1개 Reporter
      ↓
    실제 E2E
      ↓
    나머지 Reporter

이 순서를 따른다.

---

# 5. Deployment Phase

전체 배포는 다음 순서로 진행한다.

| 단계 | 대상 | 목적 |
|---|---|---|
| D0 | Desktop 4 | 환경 확인 |
| D1 | Desktop 4 | Company Ops Core 설치 |
| D2 | Desktop 4 | Local History 구조 생성 |
| D3 | Desktop 4 | Notion 연결 |
| D4 | Desktop 4 | Scheduler 설치 |
| D5 | Desktop 4 | Backup 연결 |
| D6 | Desktop 3 | 첫 Reporter 설치 |
| D7 | Desktop 3 → 4 | 실제 E2E 검증 |
| D8 | Desktop 1 | Backend Reporter 설치 |
| D9 | Desktop 2 | CMO Reporter 설치 |
| D10 | 전체 | Burn-in |
| D11 | 전체 | V1 Acceptance |

---

# 6. 왜 Desktop 4부터 시작하는가

Desktop 4는 Company Ops의 중앙 실행 PC다.

Desktop 1~3 Reporter부터 설치해도:

    Event 발생
        ↓
    받을 시스템 없음

상태가 된다.

따라서 반드시 Core를 먼저 준비한다.

---

# 7. D0 — Desktop 4 환경 확인

확인 항목:

    Windows 정상

    Python 또는 선택 Runtime 정상

    Git 설치

    GitHub 인증 가능

    Internet 연결

    D: Drive 접근

    Windows Task Scheduler 사용 가능

    Notion 접근 가능

---

# 8. D0에서 하지 않는 것

환경 확인 단계에서:

    새로운 Framework 선택

    Docker 설치

    Cloud Server 구성

    Database 설치

    Kubernetes 설치

등을 하지 않는다.

V1에 필요한 최소 환경만 확인한다.

---

# 9. D1 — Company Ops Core

기본 위치:

    D:\DOJOONPASS_COMPANY_OPS\

최소 구조:

    DOJOONPASS_COMPANY_OPS
    │
    ├─ docs
    │
    ├─ src
    │
    ├─ runtime
    │
    └─ tests

실제 구현 언어/구조에 따라 최소 범위에서 조정 가능하다.

---

# 10. docs

이미 작성한 명세를 보관한다.

    docs\
    ├─ 00_V1_DEVELOPMENT_SPEC.md
    ├─ 01_V1_IMPLEMENTATION_PLAN.md
    ├─ 02_EVENT_SCHEMA.md
    ├─ 03_COLLECTOR_SPEC.md
    ├─ 04_NOTION_SYNC_SPEC.md
    ├─ 05_HISTORY_PIPELINE_SPEC.md
    ├─ 06_DAILY_HISTORY_SPEC.md
    ├─ 07_SCHEDULER_CATCHUP_SPEC.md
    ├─ 08_BACKUP_SPEC.md
    ├─ 09_MONTHLY_HISTORY_SPEC.md
    ├─ 10_E2E_OPERATIONS_SPEC.md
    └─ 11_DEPLOYMENT_RUNBOOK.md

---

# 11. Runtime Directory

예:

    runtime\
    ├─ state\
    ├─ logs\
    ├─ locks\
    └─ backup_worktree\

Runtime 데이터는 Company History 공식 원본이 아니다.

재생성 가능한 운영 데이터다.

---

# 12. D2 — Local Master 생성

공식 History:

    D:\DOJOONPASS_COO\

기본:

    DOJOONPASS_COO
    │
    ├─ decisions
    │
    └─ history
        ├─ daily
        └─ monthly

---

# 13. Local Master 확인

반드시 확인:

    Directory 존재

    Write 가능

    UTF-8 파일 생성 가능

    Company Ops에서 접근 가능

---

# 14. Local Master 보호

금지:

    Git Repository 직접 운영

    자동 git pull

    자동 reset

    자동 restore

    Working Copy와 동일 Directory 사용

Local Master는 Git 작업영역과 분리한다.

---

# 15. history_start_date

첫 운영 시작 시 설정한다.

예:

    history_start_date
    =
    실제 Company Ops 운영 시작일

자동으로 회사 창립일을 추정하지 않는다.

---

# 16. 기존 회사 History

Company Ops 이전의 과거 History 전체를 지금 Migration하지 않는다.

현재 목표:

> 오늘 이후 History를 놓치지 않는 것.

과거자료 정리는 별도 필요성이 생기면 수행한다.

---

# 17. D3 — Notion 연결

Notion은:

    Current Execution State

용이다.

확인:

    Integration 연결

    Database 접근

    최소 Property Mapping

    Test Event Sync

---

# 18. Notion 연결 실패

Notion이 연결되지 않아도:

    Local History Core

개발 및 테스트는 진행 가능하다.

Notion 때문에 전체 Deployment를 중단하지 않는다.

---

# 19. D4 — Scheduler 설치

Desktop 4:

    Windows Task Scheduler

Task:

    DOJOONPASS_COMPANY_OPS_DAILY

기본 Trigger:

    매일 오전 11:00

Action:

    Company Ops Runner

**손으로 만들지 말고 설치 스크립트를 쓴다.** 먼저 `-WhatIf`로 무엇이 바뀔지
확인한다 — 아무것도 바꾸지 않고 예정된 작업만 출력한다.

```powershell
cd scripts
powershell -ExecutionPolicy Bypass -File .\install_runner_task.ps1 `
    -HistoryStartDate 2026-08-10 -WhatIf
```

출력이 예상과 맞으면 `-WhatIf`만 빼고 다시 실행한다. 재실행은 안전하다(`-Force`).

스크립트가 대신 해 주는 것: logon trigger의 `-User` 스코프(없으면 비관리자
머신에서 등록 자체가 거부되고, 다른 계정 로그온에도 발화한다 — Agent
Installer가 C13에서 치른 대가다), `MultipleInstances=IgnoreNew`(docs/07 §55),
`StartWhenAvailable`(§20의 목적), logon trigger 지연(docs/07 §54),
그리고 등록됐다고 보고했지만 실제로 없는 경우의 검출.

**Notion 자격증명은 이 스크립트가 다루지 않는다.** `NOTION_API_TOKEN`은 비밀이고
매개변수로 받으면 명령줄·프로세스 목록·PowerShell 기록에 남는다. 운영자가 직접
설정하며, 없으면 Notion Sync만 건너뛰고 수집·Company History·Backup은 정상
동작한다(`python ops_status.py`가 부재를 보고한다).

---

# 20. Startup Catch-up

추가 Trigger:

    Windows Login / Startup

목적:

    오전 11시 PC OFF

상황 복구.

§19의 설치 스크립트가 이 트리거를 함께 등록한다 — 별도 작업이 아니다.

---

# 21. Scheduler 검증

최소 테스트:

    Manual Run

    Scheduled Run

    Startup Run

    Duplicate Run

    Catch-up

---

# 22. Process Lock 검증

두 Runner를 동시에 실행한다.

기대:

    Runner A
    실행

    Runner B
    Lock 감지
    종료

---

# 23. Crash 검증

Runner 실행 중 강제 종료 테스트.

다음 실행:

    Stale Lock 처리

    Last Successful State 확인

    미완료 작업 재개

---

# 24. D5 — Backup 연결

Backup 구조:

    Local Master
        ↓
    Backup Working Copy
        ↓
    GitHub Private Repository

---

# 25. Backup Repository

별도 Private Repository 사용.

목적:

    Company History Backup

Product Repository와 섞지 않는다.

---

# 26. 최초 Backup 테스트

Working Copy는 **clone으로 만든다**:

    git clone <Private Repository URL> runtime/backup_working_copy

`git push`는 Working Copy에 이미 설정된 upstream tracking branch에만
의존한다(docs/08 §30, `src/backup/git_ops.py`). `clone`은 그것을 자동으로 잡고,
`git init` + `git remote add`는 잡지 않는다 — 후자로 만들면 첫 Backup이
`fatal: The current branch ... has no upstream branch`로 실패한다.

그 실패는 자격증명 오류가 아니므로 BACKUP_PENDING(일시)으로 분류되어
**매 실행 재시도되지만 사람이 upstream을 설정할 때까지 성공하지 않는다**
(BACKLOG F-2b / BUG-52). 그래서 여기서 clone을 지정한다.

Local Test History 생성:

    TEST_HISTORY.md

Backup 실행.

확인:

    Working Copy

    Commit

    Push

    GitHub 확인

---

# 27. Delete Protection Test

Test History 삭제.

Backup 실행.

기대:

    Deleted File 감지

    Push STOP

Remote File 유지.

---

# 28. Remote Divergence Test

Test Branch/환경에서 Remote 변경을 발생시킨다.

Push 시도.

기대:

    Push Rejected

    Pull 없음

    Force Push 없음

    Local Master 영향 없음

---

# 29. Recovery Test

GitHub Test History를:

    D:\DOJOONPASS_RECOVERY\

로 복구한다.

확인:

    Remote → Recovery

가능.

Remote → Master 직접 Restore는 하지 않는다.

---

# 30. D6 — 첫 Reporter

첫 Reporter 대상:

    Desktop 3

이유:

    현재 접근 가능
    +
    CTO Frontend 실제 업무 진행 중

첫 Reporter는 실제 업무에서 검증하기 좋은 Source다.

---

# 31. Desktop 3 Reporter 설치

Reporter 역할:

    업무 자체 수행

이 아니라:

    중요한 Execution Event 생성

이다.

---

# 32. Reporter 최소 테스트

Test Event:

    reporter.test

Desktop 3:

    Event 생성

기대:

    Event Layer
        ↓
    Desktop 4 Collector

---

# 33. Test Event와 Company History

`reporter.test`는 Company History에 KEEP하지 않는다.

테스트 데이터가 공식 회사 History를 오염시키면 안 된다.

---

# 34. D7 — 첫 실제 E2E

Test Event 성공 후 실제 업무 Event 하나를 사용한다.

예:

    Frontend Milestone Completed

구조:

    Desktop 3
        ↓
    Reporter
        ↓
    Event
        ↓
    Desktop 4
        ↓
    Collector
        ↓
    Notion
        ↓
    History
        ↓
    Daily
        ↓
    Backup

---

# 35. 첫 E2E Acceptance

다음 모두 확인:

    Event 생성

    Event 보존

    Collector 수집

    Duplicate 없음

    Notion 반영

    History Filter 정상

    Daily History 생성

    Local Master 저장

    Backup 성공

---

# 36. 첫 E2E 실패 시

Desktop 1/2 Reporter 설치로 넘어가지 않는다.

먼저:

    Desktop 3 → Desktop 4

경로를 안정화한다.

---

# 37. 왜 하나부터 검증하는가

4대를 동시에 연결하면 장애 발생 시:

    Reporter?

    Network?

    Collector?

    Event Schema?

    Scheduler?

원인 추적이 어려워진다.

따라서 한 경로를 먼저 완성한다.

---

# 38. D8 — Desktop 1 Reporter

Desktop 3 E2E PASS 후:

    Desktop 1
    CTO Backend / Crawling / Search

Reporter 설치.

---

# 39. Desktop 1 주요 Event

대표:

    Backend Milestone

    Crawler Milestone

    Search API Milestone

    Blocker

    Blocker Resolution

    Deployment

    Critical Bug

모든 Git Commit을 Event로 만들지 않는다.

---

# 40. Desktop 1 검증

최소:

    Milestone Event

    Blocker Event

두 종류를 실제 또는 안전한 테스트 Event로 검증한다.

---

# 41. D9 — Desktop 2 Reporter

마지막:

    Desktop 2
    CMO / Content OS

Reporter 설치.

---

# 42. Desktop 2 주요 Event

대표:

    Content OS Milestone

    Market Validation

    Campaign Milestone

    Important Customer Learning

    GTM Blocker

    Major Channel Decision

---

# 43. CMO Event 주의

다음과 같은 일상 콘텐츠 생성물을 전부 Company History로 보내지 않는다.

    Shorts 1개 생성

    Blog 1개 작성

    Thumbnail 수정

    문장 수정

History 폭증을 막는다.

---

# 44. Reporter 공통 원칙

Reporter는:

    모든 행동

을 기록하는 프로그램이 아니다.

목표:

> 회사 상태를 바꾸는 Event를 구조화해 전달하는 것.

---

# 45. Desktop별 Reporter 완료

최종:

    Desktop 1
    Reporter PASS

    Desktop 2
    Reporter PASS

    Desktop 3
    Reporter PASS

Desktop 4:

    Collector PASS

---

# 46. D10 — Burn-in

전체 연결 후 실제 업무에서 일정 기간 사용한다.

목적:

    기능 개발

이 아니라:

    안정성 확인

이다.

---

# 47. Burn-in 중 확인

확인:

    Event 누락

    Duplicate

    불필요 Event 과다

    History 과다

    History 누락

    Scheduler 누락

    Backup 실패

    Notion Sync 문제

---

# 48. Burn-in 중 금지

다음 기능을 갑자기 추가하지 않는다.

    Dashboard

    AI Agent

    KPI Platform

    Slack Alert

    Kakao Alert

    Mobile App

    새로운 DB

---

# 49. Burn-in Bug 처리

P0:

    즉시 수정

P1:

    운영 영향 기준 수정

P2:

    기록 후 V1 이후 판단

---

# 50. P0 예

    Event 손실

    History 손실

    Local Master 손상

    Catch-up 실패

    Backup이 Master 변경

    삭제 자동 전파

---

# 51. P1 예

    Notion Sync 실패

    Backup Retry 실패

    일부 History 분류 오류

    Scheduler 편의 문제

---

# 52. P2 예

    Log 보기 불편

    CLI 표현

    Commit Message

    Folder Naming

---

# 53. Daily 운영

V1 Live 이후 정상 하루:

    업무
      ↓
    Reporter
      ↓
    Event
      ↓
    Company Ops

COO:

    별도 Daily 작성
    필요 없음

---

# 54. 오전 11시

Desktop 4 ON:

    Scheduler
        ↓
    Pending Event
        ↓
    Daily Catch-up
        ↓
    Monthly Catch-up
        ↓
    Backup

---

# 55. Desktop 4 OFF

오전 11시 OFF:

    문제 없음

다음 ON:

    Startup Catch-up

---

# 56. COO Daily 업무

정상 상태:

    수동 작업 없음

오류가 있을 때만 확인한다.

---

# 57. COO가 확인할 최소 Health

    Last Runner

    Last Daily

    Pending Event

    Backup Status

    Error

매일 Notion과 로그를 전부 검토할 필요는 없다.

---

# 58. Monthly 운영

월초:

    Daily Coverage
        ↓
    Monthly
        ↓
    Backup

COO는 필요하면 Monthly Summary를 검토한다.

---

# 59. Monthly를 사용하는 목적

Monthly History는:

    CEO 보고

    COO Review

    Product Evolution

    Company Intelligence

의 Evidence로 활용한다.

---

# 60. 장애 — Notion

Notion FAIL:

    Local History 계속

처리:

    NOTION_PENDING

COO가 즉시 회사 업무를 중단할 필요 없음.

---

# 61. 장애 — GitHub

GitHub FAIL:

    Local Master 유지

처리:

    BACKUP_PENDING

장기간 지속될 경우 확인.

---

# 62. 장애 — Internet

Internet OFF:

    Local Processing 계속

외부 Sync:

    Pending

---

# 63. 장애 — Desktop 4

Desktop 4 OFF:

    Reporter Event 보존

다음 ON:

    Catch-up

---

# 64. 장애 — Desktop 4 고장

Local 접근 불가:

    GitHub Backup 확인

새 환경:

    Recovery Directory

로 복구.

---

# 65. 장애 — Event Source PC

Desktop 1/2/3 중 하나 OFF:

다른 Reporter는 정상 동작한다.

하나의 Source PC 때문에 전체 Company Ops가 중단되지 않는다.

---

# 66. 장애 — State

State 오류:

    Local History 보호

자동 삭제 금지.

State와 History 충돌:

    History 우선.

---

# 67. 장애 — Backup Conflict

Remote Divergence:

    자동 해결 금지

COO Review 필요.

---

# 68. Manual Intervention 조건

COO가 개입하는 대표 상황:

    BACKUP_REVIEW_REQUIRED

    STATE_INCONSISTENCY

    중요 REJECTED Event

    반복 Notion Auth Failure

    Local Data Damage 의심

---

# 69. 위험한 수동 조작

다음은 자동으로 실행하지 않는다.

    Restore

    Force Push

    History Delete

    State 강제 초기화

    Remote Merge

필요 시 사람이 확인한다.

---

# 70. Emergency Rule

무언가 이상하면 우선:

> Local Master를 건드리지 않는다.

그리고:

    Backup 중단

    Runner 중단

    상태 확인

순으로 대응한다.

---

# 71. History 수정

History 오류 발견:

    Local Master 수정

가능.

이후:

    Backup

Git Version History로 변경 추적 가능.

---

# 72. History 삭제

History 삭제는 일반 수정과 다르게 취급한다.

자동 Backup:

    STOP

필요하면 COO 확인 후 처리한다.

---

# 73. Reporter 제거

특정 Reporter가 문제를 일으켜도:

    해당 Reporter 중지

가능.

Company Ops Core 전체를 제거하지 않는다.

---

# 74. Notion 제거 가능성

향후 실제 운영 결과 Notion이 불필요하다고 판단되면 제거를 검토할 수 있다.

하지만 이는 V1 Deployment 중 결정하지 않는다.

실제 Evidence 후 판단한다.

---

# 75. GitHub Backup 제거 금지

Local-only 구조로 되돌리는 것은 권장하지 않는다.

Local 단일 Copy는 Hardware Failure에 취약하다.

---

# 76. V1 Acceptance Review

Burn-in 이후 다음을 검토한다.

    Core 정상?

    데이터 손실 없음?

    Catch-up 정상?

    Backup 정상?

    운영 부담 감소?

    History 품질 적절?

---

# 77. Acceptance Checklist

    [ ] Desktop 4 Core 설치

    [ ] Local Master 생성

    [ ] Notion 연결

    [ ] Scheduler 연결

    [ ] Startup Catch-up

    [ ] Backup 연결

    [ ] Desktop 3 Reporter

    [ ] Desktop 3 E2E

    [ ] Desktop 1 Reporter

    [ ] Desktop 1 E2E

    [ ] Desktop 2 Reporter

    [ ] Desktop 2 E2E

    [ ] Desktop OFF Test

    [ ] Network OFF Test

    [ ] Notion Failure Test

    [ ] GitHub Failure Test

    [ ] Delete Protection Test

    [ ] Recovery Test

    [ ] Burn-in

    [ ] Release Environment Check (§101)

---

# 78. V1 Acceptance 조건

다음이 충족되면:

    COMPANY OPS V1
    ACCEPT

가능.

조건:

    P0 = 0

    실제 Event E2E PASS

    Data Loss Risk 없음

    Catch-up PASS

    Backup PASS

    Recovery PASS

---

# 79. P1이 남은 경우

P1이 있다고 반드시 V1을 막지는 않는다.

판단:

    데이터 안전성 영향?

    핵심 자동화 영향?

영향 없으면:

    V1 ACCEPT
    +
    P1 Backlog

가능.

---

# 80. P2

V1 Acceptance를 막지 않는다.

Backlog 또는 폐기.

---

# 81. Deployment Rollback

Company Ops가 실제 업무를 방해하면:

    Reporter 중지

    Scheduler Disable

가능.

Local History는 삭제하지 않는다.

---

# 82. Rollback 시 금지

    Local Master 삭제

    Backup Repository 삭제

    기존 History 초기화

하지 않는다.

---

# 83. V1 Live 선언

Acceptance 후:

    Status:
    LIVE INTERNAL OPERATION

기록.

---

# 84. Live Date

실제 V1 Acceptance가 완료된 날짜를 기록한다.

사전에 임의 날짜를 넣지 않는다.

예:

    V1 Live Date:
    TBD

Acceptance 시 실제 날짜 입력.

---

# 85. Company Intelligence 기록

V1 Live 자체는 중요한 Company Milestone이다.

따라서 History에 남긴다.

예:

    Milestone:
    Company Ops V1 entered live internal operation.

---

# 86. CEO Decision 여부

Company Ops 내부 배포 세부사항은 일반적으로 COO 운영권한이다.

그러나 다음 변경은 CEO Decision이 필요할 수 있다.

    회사 공식 History 정책 변경

    중요한 데이터 외부 서비스 이전

    회사 전체 운영 프로세스 대폭 변경

    비용이 큰 Infrastructure 도입

---

# 87. V1 Deployment 자체

현재 확정된 Company Ops V1을 명세대로 구현·검증하는 것은 COO Execution 영역이다.

새로운 전략결정으로 확대하지 않는다.

---

# 88. Deployment 완료 보고

    [Project]
    DOJOONPASS Company Ops

    [Version]
    V1

    [Deployment Status]
    PASS / BLOCKED / FAIL

    [Desktop 4 Core]
    PASS / FAIL

    [Local Master]
    PASS / FAIL

    [Notion]
    PASS / FAIL

    [Scheduler]
    PASS / FAIL

    [Backup]
    PASS / FAIL

    [Desktop 3 Reporter]
    PASS / FAIL

    [Desktop 1 Reporter]
    PASS / FAIL

    [Desktop 2 Reporter]
    PASS / FAIL

    [E2E]
    PASS / FAIL

    [Burn-in]
    PASS / FAIL

    [P0]
    0 / N

    [P1]

    [P2]

    [Data Loss Risk]
    NONE / FOUND

    [V1 Acceptance]
    ACCEPT / BLOCKED

    [CEO Decision Required]
    NONE 또는 해당 사항

---

# 89. 구현 시작 순서

문서 작성이 완료되면 실제 구현은 다음 순서로 시작한다.

    STEP 1
    Desktop 4
    Company Ops Project 초기화

        ↓

    STEP 2
    Event Schema

        ↓

    STEP 3
    Collector

        ↓

    STEP 4
    History Pipeline

        ↓

    STEP 5
    Daily History

        ↓

    STEP 6
    Scheduler / Catch-up

        ↓

    STEP 7
    Backup

        ↓

    STEP 8
    Desktop 3 Reporter

        ↓

    STEP 9
    First E2E

        ↓

    STEP 10
    Desktop 1 / 2 Rollout

---

# 90. 구현 전 금지

구현 시작 전에 추가로 다음 문서를 만들 필요 없다.

    Dashboard Spec

    Alert Spec

    AI Agent Spec

    Quarterly Spec

    KPI Spec

    BI Spec

현재 V1 Scope 밖이다.

---

# 91. 구현 시 명세 변경

개발 중 명세와 실제 환경이 충돌할 수 있다.

이 경우:

    문제 발견
        ↓
    이유 확인
        ↓
    최소 수정

한다.

개발 편의를 이유로 핵심 안전원칙을 제거하지 않는다.

---

# 92. 변경 가능한 것

구현상 조정 가능:

    함수명

    파일명

    내부 Class 구조

    Library

    세부 Directory

    Logging 방식

단, 기능 의미는 유지한다.

---

# 93. 임의 변경 금지

개발자가 임의로 변경하면 안 되는 핵심:

    Local Master 원본 원칙

    Local → Backup 단방향

    자동 Pull 금지

    Delete Protection

    Catch-up

    Event Deduplication

    CEO Decision Authority

    History 중요도 Filter

---

# 94. 개발 중 새로운 요구사항

새로운 아이디어가 나오면:

    V1에 즉시 추가

하지 않는다.

먼저:

    V2_BACKLOG

로 보낸다.

---

# 95. V2 Backlog

예:

    Dashboard

    Alerts

    Quarterly Summary

    Advanced KPI

    Company Intelligence Search

    RAG

    AI Executive Assistant

현재 구현하지 않는다.

---

# 96. 개발 Stop Rule

V1 Acceptance Criteria를 충족하면:

    개발 종료

한다.

더 좋은 구조가 생각났다는 이유만으로 계속 리팩터링하지 않는다.

---

# 97. COO 운영 Stop Rule

COO가 Company Ops 자체를 관리하는 데 너무 많은 시간을 쓰기 시작하면 시스템 목적에 실패한 것이다.

목표:

    Company Ops 관리 시간
        ↓
    최소

    회사 Execution 가시성
        ↑
    최대

---

# 98. 최종 Deployment 구조

    [Desktop 1]
    CTO Backend
          │
          │ Reporter
          ▼
       Event Layer
          ▲
          │ Reporter
    [Desktop 2]
    CMO / Content OS
          │
          │
          ▲
          │ Reporter
    [Desktop 3]
    CTO Frontend
          │
          ▼
    ─────────────────────
          │
          ▼
    [Desktop 4]
    COMPANY OPS CORE
          │
          ├── Collector
          │
          ├── Notion Sync
          │
          ├── History
          │
          ├── Daily
          │
          ├── Monthly
          │
          ├── Scheduler
          │
          └── Backup
          │
          ├──────────────► NOTION
          │                 Current
          │
          ├──────────────► LOCAL MASTER
          │                 History
          │
          └──────────────► GITHUB
                            Backup

---

# 99. 최종 운영 목표

Company Ops 구축 전:

    CTO 업무
       ↓
    사용자가 확인
       ↓
    CMO 업무
       ↓
    사용자가 확인
       ↓
    완료내역 취합
       ↓
    COO에게 전달
       ↓
    History 수동 정리

Company Ops 구축 후:

    CTO / CMO 업무
          ↓
       Reporter
          ↓
        Event
          ↓
     Company Ops
       ↙      ↘
    Current   History
      ↓          ↓
    Notion     Local
                 ↓
               Backup

사용자가 매번 각 업무 완료내역을 수동으로 COO에게 전달하지 않는 상태를 만든다.

---

# 100. 최종 원칙

Company Ops V1의 목적은 내부 관리 시스템 자체를 거대한 Product로 만드는 것이 아니다.

목적은:

> 여러 Desktop에서 동시에 진행되는 도준패스의 개발·마케팅·운영 업무를 자동으로 연결하여 COO가 현재 회사 상태를 파악할 수 있게 하고, 회사의 중요한 Decision·Milestone·Risk·Learning이 사라지지 않도록 보존하는 것이다.

이 목적을 달성하면 Company Ops V1 개발은 종료한다.

다음 우선순위는 다시:

    DOJOONPASS
        ↓
    Product Integration
        ↓
    Beta
        ↓
    Customer Validation
        ↓
    Launch

이다.

---

# 101. Release Environment Check

Repository는 src-layout 구조다 (`src/app/runner.py`).

실행 위치: Repository Root (`git rev-parse --show-toplevel` 결과 디렉터리)

    1. python --version

    2. python -m pytest

    3. python -m compileall src

    4. python -m src.app.runner

    5. python -c "import src.app.runner"

판정 기준:

    5개 항목 모두 PASS → PASS

    Pytest 또는 Compile 실패 → A. 코드 문제

    Python/Import(4, 5) 실패 → B. 환경 문제

금지:

    import app.runner 를 검사 항목으로 사용하지 않는다.

    (근거: `app` 최상위 패키지는 Repository 어디에도 존재하지 않는다.
    실제 모듈은 `src/app/runner.py`이며, Root에서 유효한 경로는
    `src.app.runner` 뿐이다. docs·기존 코드 어디에도
    `import app.runner`를 검사 대상으로 삼을 근거가 없었다.)

---

# END OF DOCUMENT