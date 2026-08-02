# D:\DOJOONPASS_COMPANY_OPS\docs\06_DAILY_HISTORY_SPEC.md

## DOJOONPASS Company Ops — Daily History Specification

---

## 1. 문서 정의

| 항목 | 내용 |
|---|---|
| 문서명 | Company Ops Daily History Specification |
| 프로젝트 | DOJOONPASS Company Ops |
| Owner | COO |
| 상위 문서 | `00_V1_DEVELOPMENT_SPEC.md` |
| 실행 기준 | `01_V1_IMPLEMENTATION_PLAN.md` |
| Event 기준 | `02_EVENT_SCHEMA.md` |
| Collector 기준 | `03_COLLECTOR_SPEC.md` |
| Notion 기준 | `04_NOTION_SYNC_SPEC.md` |
| History 기준 | `05_HISTORY_PIPELINE_SPEC.md` |
| 목적 | KEEP 처리된 History Candidate를 날짜별 공식 Company History로 생성·보존하는 기준 정의 |
| 실행 위치 | Desktop 4 |
| 기본 실행 시각 | 매일 오전 11:00 |
| 공식 원본 | Desktop 4 Local Master |
| 적용 버전 | V1 |

본 문서는 Company Ops의 Daily History 생성 규칙을 정의한다.

현재 도준패스는 개발 및 초기 안정화 단계이므로 중요한 변화가 자주 발생할 수 있다.

따라서 현재 단계에서는 Daily History를 Company History의 기본 원본 단위로 사용한다.

---

## 2. Daily History의 목적

Daily History의 목적은 업무일지를 만드는 것이 아니다.

핵심 목적:

> 해당 날짜에 회사 차원에서 장기적으로 보존할 가치가 있는 사건을 기록한다.

전체 흐름:

    Desktop 업무
        ↓
    Execution Event
        ↓
    Collector
        ↓
    History Pipeline
        ↓
    KEEP Candidate
        ↓
    Daily Close
        ↓
    YYYY-MM-DD.md
        ↓
    Desktop 4 Local Master

---

## 3. 현재 단계의 기록 단위

현재:

    개발
      ↓
    통합
      ↓
    Beta 준비
      ↓
    Beta
      ↓
    초기 안정화

단계에서는:

    Daily History

를 기본 원본으로 사용한다.

향후 서비스가 안정적으로 운영·유지보수 단계에 들어가면 Daily 기록 필요성을 다시 검토한다.

그 시점에는 Monthly 중심 운영으로 전환할 수 있다.

전환 시점은 자동으로 결정하지 않는다.

COO가 운영상 필요성을 검토하고, 회사 운영정책에 영향을 주는 변경이면 CEO 승인 절차를 따른다.

---

## 4. 공식 저장 위치

Daily History 공식 원본:

    D:\DOJOONPASS_COO\
    └─ history\
        └─ daily\

예:

    D:\DOJOONPASS_COO\
    └─ history\
        └─ daily\
            ├─ 2026-08-01.md
            ├─ 2026-08-02.md
            ├─ 2026-08-03.md
            └─ ...

이 위치를 Daily History Local Master로 사용한다.

---

## 5. 프로그램과 History 분리

프로그램:

    D:\DOJOONPASS_COMPANY_OPS\

회사 History:

    D:\DOJOONPASS_COO\history\

두 영역을 혼합하지 않는다.

Company Ops 프로그램을 삭제하거나 다시 설치하더라도 공식 Company History가 같이 삭제되어서는 안 된다.

---

## 6. Daily Close 기본 시각

Daily Close 기본 실행 시각:

    매일 오전 11:00

중요:

오전 11시다.

오후 11시가 아니다.

---

## 7. 처리 대상 날짜

오전 11시에 처리하는 기본 대상은:

    D-1

즉 전날이다.

예:

    2026-08-06
    오전 11:00

실행 대상:

    2026-08-05

생성 파일:

    2026-08-05.md

---

## 8. 왜 D-1을 처리하는가

당일 오전 11시에 당일 History를 확정하면 오후와 저녁 업무가 누락될 수 있다.

따라서:

    오늘 업무
       ↓
    오늘 Event 발생
       ↓
    다음날 오전 11시
       ↓
    전날 History 마감

구조를 사용한다.

---

## 9. 시간대

기본 Timezone:

    Asia/Seoul

즉:

    UTC+09:00

을 기준으로 한다.

Event Timestamp에도 Timezone 정보를 유지한다.

---

## 10. Daily Close Input

Daily Close는 원본 Execution Event 전체를 다시 판단하지 않는다.

기본 Input:

    History Pipeline
          ↓
    filter_result = KEEP

인 Candidate다.

즉:

    Event
      ↓
    History Filter
      ↓
    KEEP
      ↓
    Daily Close

구조를 따른다.

---

## 11. 날짜 선택 기준

Candidate가 어느 Daily History에 들어갈지는:

    Event Timestamp

를 기준으로 한다.

예:

    Event Timestamp
    2026-08-05 23:40

Collector 처리:

    2026-08-06 10:00

Daily History:

    2026-08-05.md

---

## 12. Collector 실행일 기준 금지

다음처럼 처리하지 않는다.

    Collector 실행일
          ↓
    History 날짜 결정

이 방식은 늦게 들어온 Event의 실제 회사 History 날짜를 왜곡한다.

History는 실제 사건 발생일을 기준으로 한다.

---

## 13. 기본 Daily Close Flow

    Scheduler
       ↓
    오전 11:00
       ↓
    Daily Close 실행
       ↓
    마지막 처리 상태 확인
       ↓
    누락 날짜 확인
       ↓
    처리 대상 날짜 결정
       ↓
    KEEP Candidate 조회
       ↓
    Daily History 생성
       ↓
    Local Master 저장
       ↓
    State Update
       ↓
    Backup Queue

---

## 14. Daily History 기본 형식

기본 파일:

    YYYY-MM-DD.md

예:

    2026-08-05.md

파일 내부 기본 구조:

    # DOJOONPASS Company History — 2026-08-05

    ## Summary

    ## Decisions

    ## Milestones

    ## Issues

    ## Learnings

    ## Evidence

    ## Metadata

단, 내용이 없는 Section을 억지로 채우지 않는다.

---

## 15. Daily History 예시

    # DOJOONPASS Company History — 2026-08-05

    ## Summary

    Search MVP의 핵심 Search UI Milestone이 완료되었다.

    Auction Data Sync에서 데이터 신뢰성에 영향을 주는 동기화 문제가 확인되었다.

    ## Milestones

    ### Search Frontend

    - Search UI implementation completed.
    - Owner: CTO Frontend
    - Event ID: EVENT-001

    ## Issues

    ### Auction Data Sync

    - Existing auction_item synchronization issue affected data reliability.
    - Owner: CTO Backend
    - Event ID: EVENT-002

    ## Evidence

    - EVENT-001: TypeScript PASS
    - EVENT-002: Synchronization validation failed

    ## Metadata

    - History Date: 2026-08-05
    - Generated At: 2026-08-06T11:00:00+09:00
    - Source: DOJOONPASS Company Ops

---

## 16. Summary 원칙

Summary는 해당 날짜의 중요한 회사 변화만 짧게 정리한다.

Summary가 다음처럼 업무일지가 되어서는 안 된다.

    오늘 프론트를 수정했다.
    테스트를 여러 번 했다.
    README도 수정했다.
    코드도 정리했다.

좋은 Summary:

    Search MVP 핵심 UI Milestone이 완료되었다.

    Auction Data Sync의 데이터 신뢰성 Blocker가 확인되었다.

---

## 17. Category Section

History Category:

    DECISION
        ↓
    Decisions

    MILESTONE
        ↓
    Milestones

    ISSUE
        ↓
    Issues

    LEARNING
        ↓
    Learnings

로 Daily History에 배치한다.

---

## 18. Decisions

해당 날짜에 실제 승인된 중요 Decision만 기록한다.

예:

    ## Decisions

    ### Closed Beta Scope

    - CEO approved the Closed Beta scope.
    - Related Project: DOJOONPASS_PRODUCT
    - Event ID: DECISION-001

COO 추천 단계는 Decision History에 넣지 않는다.

---

## 19. Milestones

회사 또는 Product 발전 과정에서 의미 있는 완료사항을 기록한다.

예:

    ## Milestones

    ### Search MVP

    - Search UI implementation completed.
    - Owner: CTO Frontend
    - Event ID: EVENT-001

---

## 20. Issues

중요한 Blocker, Risk, 장애 및 해결을 기록한다.

예:

    ## Issues

    ### Auction Data Synchronization

    - Synchronization mismatch affected data reliability.
    - Status: Identified
    - Owner: CTO Backend
    - Event ID: EVENT-002

Issue 해결:

    - Synchronization issue resolved.
    - Status: Resolved
    - Event ID: EVENT-003

---

## 21. Learnings

실제 Evidence가 있는 회사 학습을 기록한다.

예:

    ## Learnings

    ### Beta Search UX

    - Multiple beta users had difficulty understanding auction status terminology.
    - Evidence: Beta VOC
    - Event ID: LEARNING-001

추측은 기록하지 않는다.

---

## 22. Evidence

해당 날짜 History에 포함된 중요한 Evidence를 정리한다.

예:

    ## Evidence

    - EVENT-001 — TypeScript PASS
    - EVENT-002 — Synchronization validation failed
    - DECISION-001 — CEO Approval

Evidence가 없는 경우 존재하지 않는 Evidence를 생성하지 않는다.

---

## 23. Metadata

최소 Metadata:

    History Date

    Generated At

    Source

필요하면:

    Event Count

정도는 추가할 수 있다.

가짜 Importance Score 같은 값은 추가하지 않는다.

---

## 24. History가 없는 날

중요한 History가 없는 날은 정상적인 상태다.

예:

    KEEP Candidate
    0건

이 경우 억지로 History 내용을 만들지 않는다.

---

## 25. Empty Day 처리

V1 기본 정책:

중요 History가 없는 날짜에도 처리 여부 확인을 위해 최소 Daily 파일을 생성할 수 있다.

예:

    # DOJOONPASS Company History — 2026-08-05

    No material company history recorded.

    ## Metadata

    - History Date: 2026-08-05
    - Generated At: 2026-08-06T11:00:00+09:00
    - Material Events: 0

이 방식의 장점:

> 해당 날짜가 누락된 것인지 실제 중요한 사건이 없었던 것인지 구분 가능

따라서 V1에서는 Empty Daily File 생성을 기본으로 한다.

---

## 26. Empty File 과잉 문제

Empty Daily File이 장기간 지나치게 많이 쌓이는 문제는 현재 단계에서는 큰 문제가 아니다.

현재는 개발 및 초기 안정화 단계이므로:

    날짜 연속성

이 더 중요하다.

운영 안정화 후 Daily 기록 정책 자체를 재검토한다.

---

## 27. Daily State

Daily Close는 최소 다음 상태를 관리해야 한다.

    last_successful_daily_close

예:

    {
      "last_successful_daily_close": "2026-08-05"
    }

저장 위치 예:

    D:\DOJOONPASS_COMPANY_OPS\
    runtime\
    state\
    daily_history_state.json

---

## 28. State의 목적

State는 다음 질문에 답한다.

> 마지막으로 정상 마감된 날짜가 언제인가?

이를 통해 PC가 꺼져 있었더라도 누락 날짜를 계산할 수 있다.

---

## 29. PC가 켜져 있는 정상 상황

예:

    8월 5일
    업무 발생

        ↓

    8월 6일
    오전 11시

        ↓

    Daily Close

        ↓

    2026-08-05.md

        ↓

    State
    last_successful_daily_close = 2026-08-05

---

## 30. PC OFF 상황

예:

    8월 6일 오전 11시
    Desktop 4 OFF

Daily Close 실행 불가.

다음 실행 가능한 시점에:

    State 확인
        ↓
    누락 날짜 탐지
        ↓
    Catch-up

한다.

---

## 31. Catch-up 기본 원칙

사용자가 컴퓨터를 켜지 않았다는 이유로 History 날짜가 누락되어서는 안 된다.

예:

마지막 성공:

    2026-08-05

현재 날짜:

    2026-08-09

현재 시각:

    오전 11시 이후

처리 필요:

    2026-08-06
    2026-08-07
    2026-08-08

각 날짜를 순서대로 처리한다.

---

## 32. 오전 11시 이전 Catch-up

예:

현재:

    2026-08-09
    오전 09:00

마지막 성공:

    2026-08-05

완전히 마감 가능한 날짜:

    2026-08-06
    2026-08-07

그리고 8월 8일도 이미 종료된 날짜이므로 Catch-up 대상으로 처리할 수 있다.

즉 PC 시작 Catch-up에서는 종료된 전날까지 처리할 수 있다.

오전 11시는 정기 실행 기준이며, 이미 종료된 날짜의 복구를 불필요하게 오전 11시까지 기다릴 필요는 없다.

---

## 33. 당일 처리 금지

현재 날짜는 Daily Close하지 않는다.

예:

현재:

    2026-08-09

처리 가능한 최대 날짜:

    2026-08-08

당일:

    2026-08-09

은 아직 업무가 끝나지 않았으므로 처리하지 않는다.

---

## 34. Catch-up 순서

누락이 여러 날짜일 경우 오래된 날짜부터 처리한다.

예:

    8월 5일
        ↓
    8월 6일
        ↓
    8월 7일
        ↓
    8월 8일

중간 날짜가 실패하면 해당 실패를 기록한다.

성공하지 않은 날짜를 성공했다고 State에 기록하지 않는다.

---

## 35. Catch-up과 Empty Day

누락 날짜에 KEEP Candidate가 없어도 Empty Daily File을 생성한다.

이를 통해:

    처리 누락

과:

    중요한 History 없음

을 구분한다.

---

## 36. Late Event 문제

예:

    8월 5일 Event 발생

    8월 6일 Daily Close 완료

    8월 7일에
    8월 5일 Event가 늦게 도착

이 경우 기존:

    2026-08-05.md

가 이미 존재한다.

Late Event를 무시하면 중요한 History가 누락될 수 있다.

반대로 파일을 조용히 덮어쓰면 History 무결성이 떨어진다.

---

## 37. Late Event 처리 원칙

V1에서는 Late KEEP Event가 확인되면 기존 Daily History를 재생성할 수 있다.

단, 반드시:

    기존 파일 존재 확인
        ↓
    기존 내용 보호
        ↓
    동일 Event 중복 확인
        ↓
    Late Event 추가
        ↓
    Updated Metadata 기록

원칙을 따른다.

---

## 38. Late Event 중복 방지

기존 Daily History에 동일:

    event_id

가 이미 존재하면 다시 추가하지 않는다.

---

## 39. Late Update Metadata

Late Event로 Daily History가 갱신된 경우 Metadata에 표시한다.

예:

    - History Date: 2026-08-05
    - Generated At: 2026-08-06T11:00:00+09:00
    - Last Updated At: 2026-08-07T12:15:00+09:00
    - Late Events Added: 1

---

## 40. 조용한 덮어쓰기 금지

기존 History 파일을 아무 기록 없이 새 내용으로 교체하지 않는다.

최소한:

    Last Updated At

을 남긴다.

History가 언제 수정됐는지 추적 가능해야 한다.

---

## 41. History 생성 실패

예:

    KEEP Candidate 조회
        PASS

    Markdown 생성
        FAIL

처리:

    State 성공 처리 금지

    Candidate 삭제 금지

    기존 History 삭제 금지

    오류 기록

    다음 실행 재시도

---

## 42. Local 저장 실패

예:

    Markdown 생성
        PASS

    Local 저장
        FAIL

처리:

    Daily Close FAIL

`last_successful_daily_close`를 갱신하지 않는다.

---

## 43. Backup 실패

예:

    Local History 저장
        PASS

    GitHub Backup
        FAIL

이 경우:

    Daily History 자체는 성공

으로 볼 수 있다.

왜냐하면 공식 Master는 Local이기 때문이다.

다만:

    BACKUP_PENDING

상태로 남겨 재시도해야 한다.

---

## 44. Local Master 우선순위

공식 History 기준:

    Local Master
        ↓
    Backup

GitHub Backup이 Local보다 우선하지 않는다.

Backup Repository의 상태가 Local History를 자동 덮어쓰게 하지 않는다.

---

## 45. 파일 생성 안전성

가능하면 다음 방식으로 저장한다.

    Temporary File 생성
            ↓
    Write 완료
            ↓
    Validation
            ↓
    Final File Replace

목적:

프로그램이 파일 작성 도중 종료되어 깨진 History 파일이 공식 원본으로 남는 것을 방지한다.

---

## 46. 파일 Encoding

기본:

    UTF-8

한글과 영문을 안정적으로 처리해야 한다.

---

## 47. Daily History 정렬

같은 날짜에 여러 History가 있는 경우 기본적으로:

    timestamp ASC

즉 실제 발생 순서대로 정렬한다.

Category Section 안에서도 시간순 정렬을 기본으로 한다.

---

## 48. 같은 사건의 발생과 해결

같은 날짜에:

    ISSUE 발생
        ↓
    ISSUE 해결

이 모두 존재할 수 있다.

둘 다 중요하다면 모두 기록한다.

예:

    10:00
    Data Sync Blocker identified

    18:00
    Data Sync Blocker resolved

이를 하나만 남기지 않는다.

---

## 49. Daily History는 원본 Summary Layer

Daily History는 Raw Event Store가 아니다.

그러나 회사 History 관점에서는 현재 개발단계의 기본 공식 기록 단위다.

구조:

    Raw Event
       ↓
    History Filter
       ↓
    Daily History
       ↓
    Monthly Summary

따라서 Monthly History가 생성되더라도 Daily History를 삭제하지 않는다.

---

## 50. Daily → Monthly

향후:

    2026-08-01.md
    2026-08-02.md
    2026-08-03.md
    ...
    2026-08-31.md

를 기반으로:

    2026-08.md

Monthly History를 생성한다.

Monthly는 Daily의 대체물이 아니다.

Summary Layer다.

---

## 51. 월별 원본에 대한 현재 원칙

현재 개발·초기 안정화 단계:

    Daily
      =
    기본 세부 History 원본

    Monthly
      =
    Summary

향후 안정화 이후 운영/유지보수 단계:

    Monthly 중심 기록

으로 전환하는 것을 검토한다.

현재부터 월별 기록만 사용하지 않는다.

---

## 52. Daily History와 Notion

Notion:

    Current State

Daily History:

    Historical Record

예:

Notion:

    Search Frontend
    COMPLETED

Daily History:

    언제 완료됐는가?
    어떤 Milestone이었는가?
    어떤 Evidence가 있었는가?

역할이 다르다.

---

## 53. Daily History와 GitHub

Daily History의 공식 Master는 GitHub가 아니다.

구조:

    Local History
        ↓
    Backup Process
        ↓
    GitHub Private

GitHub는 복구 가능한 Backup Layer다.

---

## 54. Daily History와 Git Pull

History Backup Repository에서 무분별한 자동 `git pull`을 실행하여 Local Master를 덮어쓰지 않는다.

특히 자동화에서:

    git pull
        ↓
    Conflict
        ↓
    Local History 변경

같은 구조를 피한다.

Backup은 Local → Remote 방향을 기본으로 한다.

---

## 55. History 삭제

Daily History 자동 삭제 기능은 V1에서 만들지 않는다.

파일이 오래됐다는 이유로 삭제하지 않는다.

---

## 56. History 수정

프로그램이 기존 History를 수정할 수 있는 대표적 V1 상황:

    Late KEEP Event

그 외 자동 수정 기능은 최소화한다.

---

## 57. Manual Edit

COO가 공식 History를 직접 수정해야 하는 경우가 있을 수 있다.

프로그램은 Local 파일이 이미 존재한다는 이유만으로 무조건 새로 덮어써서는 안 된다.

Late Event Update 구현 시 기존 내용을 읽고 보존하는 방식이 필요하다.

---

## 58. AI 사용

Daily History Summary 작성에 AI를 사용할 수 있다.

하지만 V1의 필수 Dependency로 만들지 않는다.

AI 사용 가능:

    KEEP Candidate
        ↓
    Summary 정리

AI 실패:

    Candidate Summary 기반
        ↓
    Rule-based Markdown 생성

Fallback이 가능해야 한다.

---

## 59. AI 금지사항

AI가 다음을 생성해서는 안 된다.

- 존재하지 않는 Decision
- 존재하지 않는 Milestone
- 존재하지 않는 Issue
- 존재하지 않는 Learning
- 존재하지 않는 Evidence
- 추정 KPI
- CEO 승인 추정
- 원본에 없는 원인
- 원본에 없는 결과

---

## 60. Daily History 품질 기준

좋은 Daily History는 다음 질문에 빠르게 답할 수 있어야 한다.

    오늘 회사에서
    실제로 중요한 일이 무엇이었는가?

    무엇이 결정됐는가?

    무엇이 완료됐는가?

    어떤 중요한 문제가 있었는가?

    무엇을 배웠는가?

중요한 것이 없었다면:

    없음

이라고 기록하는 것이 맞다.

---

## 61. 보안

History에 다음을 기록하지 않는다.

    Password

    API Token

    Secret Key

    전체 .env

    인증정보

    불필요한 개인정보

    민감 Raw Log

Evidence를 기록할 때도 Secret을 제거해야 한다.

---

## 62. Scheduler와 Daily History 분리

Daily History Generator:

> 무엇을 생성할지 담당

Scheduler:

> 언제 실행할지 담당

따라서 Daily History Generator는 수동으로도 실행할 수 있어야 한다.

예:

    특정 날짜 Daily History 생성

    특정 날짜 재처리

    누락 날짜 Catch-up

Scheduler가 없어도 핵심 생성 로직을 테스트할 수 있어야 한다.

---

## 63. 기본 실행 모드

최소 다음 실행 모드를 지원할 수 있다.

    DAILY

    CATCH_UP

    REPROCESS_DATE

### DAILY

전날 처리.

### CATCH_UP

누락된 날짜 처리.

### REPROCESS_DATE

특정 날짜를 명시적으로 재검증.

REPROCESS_DATE는 자동으로 남발하지 않는다.

---

## 64. REPROCESS_DATE 보호

특정 날짜 재처리 시 기존 History가 있다면:

    Existing File Detection

이 필수다.

기존 내용을 무조건 삭제 후 재생성하지 않는다.

---

## 65. Daily History 상태

날짜별 처리 결과를 최소 다음처럼 구분할 수 있다.

    GENERATED

    GENERATED_EMPTY

    UPDATED_LATE_EVENT

    FAILED

    BACKUP_PENDING

---

## 66. GENERATED

KEEP Candidate가 존재하며 정상 Daily History가 생성됨.

---

## 67. GENERATED_EMPTY

KEEP Candidate가 없지만 날짜 연속성 확인을 위한 Empty History가 정상 생성됨.

---

## 68. UPDATED_LATE_EVENT

기존 Daily History에 Late Event가 안전하게 추가됨.

---

## 69. FAILED

Daily History 생성 또는 Local 저장에 실패.

성공 State를 갱신하지 않는다.

---

## 70. BACKUP_PENDING

Local Daily History는 정상 저장되었지만 Backup이 아직 완료되지 않음.

Local History 자체는 유효하다.

---

## 71. Mock Test — Normal Day

조건:

    8월 5일
    KEEP Candidate 2건

실행:

    8월 6일 오전 11시

기대:

    2026-08-05.md 생성

    Candidate 2건 포함

    State
    2026-08-05

---

## 72. Mock Test — Empty Day

조건:

    KEEP Candidate 0건

기대:

    2026-08-05.md 생성

    No material company history recorded.

    GENERATED_EMPTY

---

## 73. Mock Test — PC OFF 1일

조건:

    8월 6일 Desktop 4 OFF

    8월 7일 Desktop 4 ON

기대:

    8월 5일 누락 여부 확인

    8월 6일도 종료된 날짜라면 처리 대상 확인

    누락된 날짜 순서대로 Catch-up

---

## 74. Mock Test — PC OFF 여러 날

마지막 성공:

    2026-08-05

다음 실행:

    2026-08-10

기대:

    2026-08-06
    2026-08-07
    2026-08-08
    2026-08-09

누락 날짜를 순서대로 처리.

---

## 75. Mock Test — Late Event

기존:

    2026-08-05.md

Late KEEP Event:

    timestamp = 2026-08-05

도착:

    2026-08-07

기대:

    기존 History 보호

    Event ID 중복 확인

    Late Event 추가

    Last Updated At 기록

---

## 76. Mock Test — Duplicate Late Event

Late Event가 이미 Daily History에 존재.

기대:

    추가하지 않음

    중복 History 없음

---

## 77. Mock Test — Write Failure

Local 저장 실패를 재현.

기대:

    FAILED

    State 갱신 안 함

    Candidate 유지

    다음 실행 재시도 가능

---

## 78. Mock Test — Backup Failure

Local 저장:

    PASS

Backup:

    FAIL

기대:

    Daily History 유지

    BACKUP_PENDING

    Local File 삭제 없음

---

## 79. Test Matrix

| Test | 기대 결과 |
|---|---|
| 정상 Daily Close | D-1 History 생성 |
| KEEP 0건 | Empty History 생성 |
| 여러 Candidate | Category별 기록 |
| PC OFF 1일 | Catch-up |
| PC OFF 여러 날 | 누락 날짜 순차 Catch-up |
| Late Event | 기존 History 보호 + 추가 |
| Duplicate Late Event | 중복 없음 |
| Local Write Fail | State 미갱신 |
| Backup Fail | Local 유지 |
| AI Fail | Rule-based 생성 |
| Secret 포함 Evidence | Secret 저장 방지 |
| 당일 처리 | 실행하지 않음 |

---

## 80. Phase 5 완료 기준

다음이 실제 검증되면 Phase 5 Daily Close를 PASS할 수 있다.

1. D-1 날짜 계산이 정확하다.
2. KEEP Candidate를 날짜별 조회한다.
3. Daily Markdown을 생성한다.
4. Category별 Section이 생성된다.
5. Evidence가 연결된다.
6. Local Master에 저장된다.
7. Empty Day가 구분된다.
8. Event Timestamp 기준으로 날짜를 정한다.
9. 동일 Event가 중복 기록되지 않는다.
10. Local 저장 실패 시 State가 갱신되지 않는다.
11. AI 없이 Daily History를 생성할 수 있다.
12. UTF-8 한글 저장이 정상이다.

---

## 81. Phase 6과의 관계

Catch-up의 기본 원칙은 본 문서에서 정의한다.

실제 자동 실행 및 PC OFF 복구 메커니즘은:

    Phase 6
    Catch-up + Scheduler

에서 완성한다.

즉:

    Phase 5
    Daily History 생성 로직

        ↓

    Phase 6
    자동 실행 + 누락 복구

순서다.

---

## 82. V1에서 만들지 않는 것

Daily History V1에서는 다음을 만들지 않는다.

- 실시간 History 작성
- 시간별 History
- 모든 Task 기록
- 모든 Commit 기록
- 모든 Chat 기록
- 복잡한 AI Narrative
- History Dashboard
- History Web UI
- Vector DB
- RAG
- Knowledge Graph
- 자동 투자보고서
- 자동 IPO 문서
- 자동 M&A 실사자료
- 자동 분기보고서
- 자동 연간보고서

---

## 83. Stop Rule

다음이 안정적으로 작동하면 Daily History 기능 개발을 종료한다.

    KEEP Candidate
        ↓
    날짜 분류
        ↓
    Daily Markdown
        ↓
    Local Master

그리고:

    History 없음
        ↓
    Empty Day 확인

까지 작동하면 충분하다.

복잡한 기능을 추가하지 않고 Scheduler/Catch-up 단계로 넘어간다.

---

## 84. 완료 보고 형식

Phase 5 완료 시:

    [Phase]
    Phase 5 — Daily History

    [상태]
    PASS / BLOCKED / FAIL

    [구현 내용]

    [변경 파일]

    [Daily Close Test]
    PASS / FAIL

    [D-1 Calculation]
    PASS / FAIL

    [History Classification]
    PASS / FAIL

    [Empty Day]
    PASS / FAIL

    [Local Master Write]
    PASS / FAIL

    [Duplicate Protection]
    PASS / FAIL

    [Late Event Basic Test]
    PASS / FAIL

    [Write Failure Protection]
    PASS / FAIL

    [AI-independent Fallback]
    PASS / FAIL

    [Evidence]

    [발견된 문제]

    [다음 작업]

    [CEO/COO Decision Required]
    NONE 또는 해당 사항

---

## 85. 다음 명세

다음 단계:

    Phase 6
    Catch-up + Scheduler

다음 문서:

    D:\DOJOONPASS_COMPANY_OPS\
    docs\
    07_SCHEDULER_CATCHUP_SPEC.md

다음 문서에서는 다음을 정의한다.

    매일 오전 11시 자동 실행

    Windows 시작 시 누락 검사

    Desktop 4가 꺼져 있었을 때 복구

    여러 날짜 누락 처리

    Scheduler 중복 실행 방지

    실행 중 PC 종료 대응

    Daily Catch-up

    향후 Monthly Catch-up 연결

중요 원칙:

> Desktop 4가 매일 오전 11시에 반드시 켜져 있어야만 History가 생성되는 구조로 만들지 않는다.

---

# END OF DOCUMENT