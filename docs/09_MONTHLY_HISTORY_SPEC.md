# D:\DOJOONPASS_COMPANY_OPS\docs\09_MONTHLY_HISTORY_SPEC.md

## DOJOONPASS Company Ops — Monthly History Specification

---

## 1. 문서 정의

| 항목 | 내용 |
|---|---|
| 문서명 | Company Ops Monthly History Specification |
| 프로젝트 | DOJOONPASS Company Ops |
| Owner | COO |
| 상위 문서 | `00_V1_DEVELOPMENT_SPEC.md` |
| 실행 기준 | `01_V1_IMPLEMENTATION_PLAN.md` |
| History 기준 | `05_HISTORY_PIPELINE_SPEC.md` |
| Daily History 기준 | `06_DAILY_HISTORY_SPEC.md` |
| Scheduler 기준 | `07_SCHEDULER_CATCHUP_SPEC.md` |
| Backup 기준 | `08_BACKUP_SPEC.md` |
| 목적 | Daily History를 월 단위 경영 History로 통합·요약하고 장기 Company Intelligence의 기반을 구축하는 기준 정의 |
| 실행 위치 | Desktop 4 |
| 기본 실행 시각 | 매월 1일 오전 11:00 |
| 공식 원본 | Desktop 4 Local Master |
| 적용 버전 | V1 |

본 문서는 Company Ops의 Monthly History 생성 규칙을 정의한다.

핵심 질문은 다음과 같다.

> 지난 한 달 동안 도준패스라는 회사에서 실제로 무엇이 변했는가?

Monthly History는 Daily History를 단순 연결하는 파일이 아니다.

회사의 중요한 변화와 경영적 의미를 월 단위로 압축하는 Summary Layer다.

---

## 2. 현재 History 구조

현재 개발 및 초기 안정화 단계에서는 다음 구조를 사용한다.

    Execution Event
          ↓
    History Pipeline
          ↓
      Daily History
          ↓
    Monthly History

역할:

    Daily History
        =
    세부 공식 History

    Monthly History
        =
    경영 Summary

Monthly History가 생성되더라도 Daily History를 삭제하지 않는다.

---

## 3. 향후 구조

서비스가 안정적인 운영/유지보수 단계로 들어가면 기록 빈도를 다시 검토할 수 있다.

현재:

    Daily
      +
    Monthly

향후:

    Monthly 중심

으로 전환할 가능성이 있다.

단, 자동으로 전환하지 않는다.

운영단계 변경은 COO가 제안하고 회사 운영정책에 영향을 주는 중요 변경이면 CEO 승인 절차를 따른다.

---

## 4. 공식 저장 위치

Monthly History 공식 저장 위치:

    D:\DOJOONPASS_COO\
    └─ history\
        └─ monthly\

예:

    D:\DOJOONPASS_COO\
    └─ history\
        ├─ daily\
        │   ├─ 2026-08-01.md
        │   ├─ 2026-08-02.md
        │   └─ ...
        │
        └─ monthly\
            ├─ 2026-08.md
            ├─ 2026-09.md
            └─ ...

---

## 5. 파일명

기본 형식:

    YYYY-MM.md

예:

    2026-08.md

    2026-09.md

---

## 6. 기본 실행 시점

Monthly History 정기 생성:

    매월 1일
    오전 11:00

예:

    2026-09-01
    오전 11:00

처리 대상:

    2026-08

생성:

    2026-08.md

---

## 7. 왜 매월 1일인가

월말 당일에 Monthly History를 생성하면 월말 오후·저녁 Event가 누락될 수 있다.

따라서:

    8월 전체 종료
        ↓
    9월 1일
        ↓
    8월 Monthly 생성

구조를 사용한다.

---

## 8. 오전 11시의 의미

오전 11시는 Monthly History의 날짜 기준이 아니다.

정기 실행 시각이다.

Monthly에 포함되는 사건은:

    해당 월의 Daily History

를 기준으로 한다.

---

## 9. Monthly 생성 전 가장 중요한 조건

Monthly History 생성 전에 반드시 확인한다.

> 대상 월의 Daily History가 모두 처리되었는가?

예:

8월 Monthly 생성 전:

    2026-08-01.md
        ↓
    ...
        ↓
    2026-08-31.md

Daily 처리 상태를 확인한다.

---

## 10. Daily 완전성 우선

예:

    2026-09-01
    오전 11시

그런데:

    2026-08-30.md
    없음

    2026-08-31.md
    없음

이면 Monthly를 먼저 만들지 않는다.

순서:

    Missing Daily 탐지
          ↓
    Daily Catch-up
          ↓
    Daily Complete
          ↓
    Monthly 생성

---

## 11. Empty Daily도 정상

Daily History 중:

    No material company history recorded.

가 존재할 수 있다.

이는 누락이 아니다.

즉:

    Daily File 존재
    +
    GENERATED_EMPTY

이면 정상 처리된 날짜다.

---

## 12. Monthly Input

Monthly History의 기본 Input:

    Daily History

이다.

Raw Execution Event 전체를 다시 처음부터 처리하지 않는다.

구조:

    Raw Event
       ↓
    History Filter
       ↓
    Daily History
       ↓
    Monthly Consolidation

---

## 13. Monthly가 Raw Event를 직접 사용하지 않는 이유

Raw Event를 다시 사용하면:

- History Filter 중복
- 판단 기준 불일치
- Daily와 Monthly 내용 충돌
- 구현 복잡성 증가

문제가 발생할 수 있다.

따라서 Monthly는 확정된 Daily History를 기본 Source로 사용한다.

---

## 14. Monthly 기본 구조

기본 형식:

    # DOJOONPASS Company History — 2026-08

    ## Executive Summary

    ## Major Decisions

    ## Major Milestones

    ## Major Issues & Resolutions

    ## Key Learnings

    ## Product Evolution

    ## KPI / Customer Signals

    ## Open Risks

    ## Next-Month Carryover

    ## Source Records

    ## Metadata

내용이 없는 Section은 억지로 채우지 않는다.

---

## 15. Executive Summary

해당 월의 가장 중요한 회사 변화만 요약한다.

목표:

> CEO가 이 부분만 읽어도 지난 한 달의 핵심 흐름을 이해할 수 있어야 한다.

예:

    ## Executive Summary

    2026년 8월 도준패스는 Search MVP 개별 기능 개발에서
    통합 및 Beta 준비 단계로 이동했다.

    Search UI 핵심 Milestone이 완료되었으며,
    Auction Data Synchronization의 데이터 신뢰성 문제가
    주요 기술 Blocker로 확인되었다.

    Closed Beta Scope에 대한 CEO Decision이 확정되었고,
    이후 개발 우선순위는 기능 확장보다 데이터 정확성,
    통합 및 배포 안정성에 집중되었다.

---

## 16. Executive Summary 과잉 방지

Executive Summary는 Daily 내용을 모두 다시 쓰지 않는다.

좋지 않은 구조:

    8월 1일에는...
    8월 2일에는...
    8월 3일에는...

좋은 구조:

    이번 달 회사가
    어디에서 어디로 이동했는가?

를 설명한다.

---

## 17. Major Decisions

해당 월에 실제 확정된 중요 Decision만 정리한다.

예:

    ## Major Decisions

    ### Closed Beta Scope

    - Decision: Search-centered Closed Beta로 범위 확정
    - Authority: CEO
    - Date: 2026-08-10
    - Impact: 기술 및 GTM 일정 산정 기준 확정
    - Source: 2026-08-10.md

모든 작은 판단을 포함하지 않는다.

---

## 18. Decision Authority

Monthly History가 Decision Authority를 변경하거나 추정해서는 안 된다.

예:

COO가 제안:

    Beta Scope A 추천

CEO가 승인:

    Beta Scope A 확정

Monthly:

    CEO approved Beta Scope A.

라고 기록한다.

COO 제안을 CEO Decision처럼 기록하지 않는다.

---

## 19. Major Milestones

해당 월 Product 또는 회사 발전을 설명하는 주요 완료사항을 정리한다.

예:

    ## Major Milestones

    ### Search MVP UI

    - Completed: 2026-08-05
    - Owner: CTO Frontend
    - Result: Search MVP 핵심 UI 구현 완료
    - Evidence: TypeScript PASS
    - Source: 2026-08-05.md

---

## 20. Milestone 선별

Daily에 Milestone이 여러 개 존재하더라도 Monthly에는 중요한 것만 남긴다.

질문:

> 이 Milestone이 한 달 뒤 또는 1년 뒤 회사 발전 과정을 설명하는 데 필요한가?

YES:

    Monthly 포함

NO:

    Daily에만 유지

---

## 21. Major Issues & Resolutions

월간 주요 Risk와 해결을 정리한다.

예:

    ## Major Issues & Resolutions

    ### Auction Data Synchronization

    - Identified: 2026-08-05
    - Impact: Auction data reliability
    - Owner: CTO Backend
    - Resolution: 2026-08-12
    - Result: Synchronization validation passed
    - Sources:
      - 2026-08-05.md
      - 2026-08-12.md

---

## 22. Issue Lifecycle

가능하면 Monthly에서는 Issue 발생과 해결을 연결한다.

Daily:

    8월 5일
    Issue 발생

    8월 12일
    Issue 해결

Monthly:

    하나의 Issue Lifecycle

로 표현할 수 있다.

---

## 23. 미해결 Issue

월말까지 해결되지 않은 중요 Issue는:

    OPEN

으로 남긴다.

예:

    Status: OPEN

그리고:

    Open Risks

또는:

    Next-Month Carryover

에도 연결할 수 있다.

---

## 24. Key Learnings

실제 Evidence 기반의 중요한 학습만 기록한다.

예:

    ## Key Learnings

    ### Search Terminology

    Beta users repeatedly had difficulty understanding auction status terminology.

    Evidence:
    - Beta VOC
    - Multiple user observations

    Implication:
    Search result terminology requires simplification before wider launch.

주의:

Implication은 Evidence에서 합리적으로 직접 도출 가능한 수준까지만 작성한다.

---

## 25. 단순 의견 제외

다음은 Learning으로 기록하지 않는다.

    사용자는 아마 이런 기능을 좋아할 것이다.

    이 기능은 경쟁력이 있을 것 같다.

    앞으로 잘될 것 같다.

Evidence가 없는 추측이다.

---

## 26. Product Evolution

Monthly History에서 중요한 역할을 한다.

목적:

> Product가 이번 달에 어떻게 변했는가?

예:

    ## Product Evolution

    Start of Month:

    Search UI 개별 구현 단계

        ↓

    Mid-Month:

    Search API Integration

        ↓

    End of Month:

    Search-centered Closed Beta 준비

이를 통해 장기적으로 Product Evolution을 추적할 수 있다.

---

## 27. Product Evolution 생성 기준

Product Evolution은:

    Major Decisions
        +
    Major Milestones
        +
    Product Scope Changes

를 기반으로 정리한다.

새로운 사실을 만들어내지 않는다.

---

## 28. Product Evolution과 기능 목록 차이

좋지 않은 예:

    버튼 추가
    Select 추가
    CSS 수정
    API 함수 수정

좋은 예:

    Search Prototype
        ↓
    Search MVP
        ↓
    Search + Detail Integration
        ↓
    Closed Beta

Product의 단계 변화에 집중한다.

---

## 29. KPI / Customer Signals

현재 V1에서는 KPI 시스템 전체를 구현하지 않는다.

하지만 실제 의미 있는 KPI 또는 Customer Signal이 존재하면 Monthly에 포함할 수 있다.

예:

    첫 Beta User

    첫 Signup

    첫 Payment

    중요한 Conversion 변화

    반복 VOC

    Retention Signal

---

## 30. KPI 숫자 생성 금지

자료가 없으면:

    Conversion 15%

    Retention 40%

같은 숫자를 만들어내지 않는다.

데이터가 없으면 Section을 비우거나 생략한다.

---

## 31. KPI Raw Data 저장소 아님

Monthly History는 KPI Database가 아니다.

예:

    Day 1: 4 users
    Day 2: 7 users
    Day 3: 5 users

전체 숫자를 저장하는 것이 목적이 아니다.

의미 있는 변화만 기록한다.

---

## 32. Open Risks

월말 기준 아직 해결되지 않은 중요한 Risk를 기록한다.

예:

    ## Open Risks

    ### Production Deployment

    - Status: OPEN
    - Impact: Closed Beta launch dependency
    - Owner: CTO
    - Source: 2026-08-29.md

---

## 33. Open Risks의 목적

다음 달 시작 시 COO가:

> 지난달에서 넘어온 중요한 위험이 무엇인가?

를 바로 확인할 수 있게 한다.

---

## 34. Next-Month Carryover

다음 달로 넘어가는 중요 실행사항을 기록한다.

예:

    ## Next-Month Carryover

    - Auction Data Sync validation completion
    - Search → Detail E2E validation
    - Production deployment
    - Closed Beta readiness verification

단순 To-do List 전체를 넣지 않는다.

---

## 35. Carryover와 Task Manager 차이

Monthly History는 Task Manager가 아니다.

따라서:

    CSS 수정
    README 정리
    코드 리팩터링

같은 세부 Task를 넣지 않는다.

전사 Execution에 의미 있는 Carryover만 남긴다.

---

## 36. Source Records

Monthly 내용이 어떤 Daily History에서 나왔는지 추적할 수 있어야 한다.

예:

    ## Source Records

    - 2026-08-05.md
    - 2026-08-10.md
    - 2026-08-12.md
    - 2026-08-29.md

모든 Empty Daily를 나열할 필요는 없다.

실제 Monthly 내용에 사용된 Source 중심으로 기록한다.

---

## 37. Metadata

최소:

    History Month

    Generated At

    Source

    Daily Coverage

예:

    ## Metadata

    - History Month: 2026-08
    - Generated At: 2026-09-01T11:00:00+09:00
    - Source: DOJOONPASS Daily Company History
    - Daily Coverage: COMPLETE

---

## 38. Daily Coverage 상태

최소:

    COMPLETE

    INCOMPLETE

를 구분할 수 있다.

정상 Monthly 공식 생성은:

    COMPLETE

를 요구한다.

---

## 39. INCOMPLETE 상태

대상 월 Daily History 일부가 처리되지 않았다면:

    Monthly 공식 확정 금지

기본적으로 Daily Catch-up을 먼저 시도한다.

Catch-up 실패:

    MONTHLY_PENDING

으로 남긴다.

---

## 40. Monthly 상태

최소 다음 상태를 사용한다.

    MONTHLY_PENDING

    MONTHLY_GENERATED

    MONTHLY_UPDATED

    MONTHLY_FAILED

---

## 41. MONTHLY_PENDING

전월 Monthly를 아직 생성할 수 없는 상태.

예:

- Daily 누락
- Daily Catch-up 실패
- Local History 읽기 실패

---

## 42. MONTHLY_GENERATED

정상적으로 처음 생성된 상태.

---

## 43. MONTHLY_UPDATED

Late Event 등으로 Daily History가 수정된 후 Monthly도 안전하게 갱신된 상태.

---

## 44. MONTHLY_FAILED

Monthly 생성 또는 Local 저장에 실패한 상태.

기존 Monthly 파일이 있다면 삭제하지 않는다.

---

## 45. Monthly State

예:

    {
      "last_successful_monthly_close": "2026-08"
    }

저장 위치 예:

    D:\DOJOONPASS_COMPANY_OPS\
    runtime\
    state\
    monthly_history_state.json

---

## 46. PC OFF 상황

예:

    9월 1일 오전 11시
    Desktop 4 OFF

Monthly 생성 불가.

다음 Desktop 4 실행 시:

    Monthly Catch-up

을 수행한다.

---

## 47. Monthly Catch-up

현재:

    2026-09-03

마지막 Monthly:

    2026-07

미생성:

    2026-08

처리:

    8월 Daily 완전성 확인
        ↓
    필요 시 Daily Catch-up
        ↓
    8월 Monthly 생성

---

## 48. 여러 달 PC OFF

예:

마지막 Monthly:

    2026-08

현재:

    2026-12

미생성:

    2026-09
    2026-10
    2026-11

오래된 월부터 처리한다.

    9월
      ↓
    10월
      ↓
    11월

---

## 49. 현재 월 생성 금지

현재:

    2026-09-15

Monthly Catch-up 대상에:

    2026-09

를 포함하지 않는다.

현재 월은 아직 종료되지 않았다.

최대 처리 대상:

    2026-08

---

## 50. Scheduler 기본 순서

매월 1일 Runner:

    Lock
      ↓
    Collector
      ↓
    History Pipeline
      ↓
    Daily Catch-up
      ↓
    Previous Month Daily Coverage Check
      ↓
    Monthly Consolidation
      ↓
    Local Monthly Save
      ↓
    Backup
      ↓
    State Update

---

## 51. Daily와 Monthly 실행 충돌 방지

매월 1일 오전 11시는:

    Daily Scheduler

와:

    Monthly Scheduler

가 동시에 실행될 가능성이 있다.

별도 프로세스 두 개가 경쟁하지 않도록 한다.

권장:

    동일 Company Ops Runner
        ↓
    Daily 작업 먼저
        ↓
    Monthly 작업

---

## 52. 1일 오전 11시 처리 예

예:

    2026-09-01
    11:00

먼저:

    2026-08-31
    Daily Close

그 다음:

    2026-08
    Monthly History

순서가 중요하다.

---

## 53. Monthly 생성 전 31일 확인

8월 Monthly 생성 전에 8월 31일 Daily까지 처리되어야 한다.

즉:

    8월 31일 Daily
        ↓
    PASS
        ↓
    8월 Monthly

---

## 54. Late Event 문제

예:

    8월 Monthly
    9월 1일 생성 완료

그런데:

    9월 3일

8월 20일에 실제 발생했던 중요한 Late Event가 들어옴.

Daily:

    2026-08-20.md
    업데이트

Monthly도 영향을 받을 수 있다.

---

## 55. Monthly Late Update

Late Event가 Monthly에 포함될 정도로 중요한 KEEP History라면:

    Daily Update
        ↓
    Monthly Dirty 표시
        ↓
    Monthly Rebuild/Update

가 필요하다.

---

## 56. Monthly Dirty

특정 과거 Daily History가 수정되면 해당 월을:

    MONTHLY_DIRTY

상태로 표시할 수 있다.

예:

    {
      "month": "2026-08",
      "status": "MONTHLY_DIRTY"
    }

---

## 57. MONTHLY_DIRTY 처리

다음 Runner에서:

    MONTHLY_DIRTY
        ↓
    해당 월 Daily 재확인
        ↓
    Monthly Update
        ↓
    MONTHLY_UPDATED

---

## 58. 기존 Monthly 보호

Monthly Update 시 기존:

    2026-08.md

를 아무 기록 없이 조용히 덮어쓰지 않는다.

Metadata에 최소:

    Last Updated At

을 남긴다.

예:

    - Generated At: 2026-09-01T11:00:00+09:00
    - Last Updated At: 2026-09-03T15:20:00+09:00

---

## 59. Monthly 중복 방지

동일 Daily Event가 여러 번 Monthly에 반복되어서는 안 된다.

Monthly Consolidator는 Source Daily와 Event ID를 기준으로 중복을 방지할 수 있다.

---

## 60. Issue 중복 압축

예:

Daily:

    8월 5일
    Issue 발견

    8월 6일
    Issue 계속

    8월 7일
    Issue 계속

    8월 12일
    Issue 해결

Monthly:

    4개의 별도 Issue로 기록하지 않는다.

가능하면 하나의 Issue Lifecycle로 압축한다.

---

## 61. Milestone 중복 압축

비슷한 작은 Milestone 여러 개가 하나의 큰 Product Milestone을 구성한다면 Monthly에서 통합할 수 있다.

단, 원본 Daily 사실을 왜곡해서는 안 된다.

---

## 62. AI 사용

Monthly Summary는 Daily보다 AI 활용 가치가 높을 수 있다.

AI 사용 가능:

- Executive Summary 초안
- 유사 Issue 묶기
- Product Evolution 정리
- 중복 표현 제거
- 중요 사건 압축

그러나 AI는 보조 도구다.

---

## 63. AI Fallback

AI 사용 불가:

    Daily History
        ↓
    Rule-based Category Collection
        ↓
    Template Monthly

형태로 Monthly를 생성할 수 있어야 한다.

AI 장애 때문에 Monthly 원본이 생성되지 않는 구조를 만들지 않는다.

---

## 64. AI 금지사항

AI는 다음을 하지 않는다.

- 존재하지 않는 Decision 생성
- CEO 의도 추측
- KPI 생성
- 존재하지 않는 VOC 생성
- Issue 원인 추측
- Product 성공 여부 임의 평가
- 회사 가치평가
- 미래 전망을 History Fact로 기록
- 중요 사건 삭제

---

## 65. 자동 경영판단 금지

Monthly Generator는:

    회사 상태 🟢

    Launch Go

    전략 변경 필요

같은 경영판단을 자동 확정하지 않는다.

Monthly History는 Evidence Layer다.

COO 보고서는 이 Evidence를 기반으로 별도로 판단한다.

---

## 66. COO와 Monthly History

COO는 Monthly History를 이용해 다음을 확인할 수 있다.

    지난달 주요 Decision

    주요 Milestone

    주요 Blocker

    해결된 Risk

    남은 Risk

    Product 변화

    실제 Learning

이를 다음 달 Execution 관리와 CEO 보고의 근거로 활용한다.

---

## 67. CEO Decision과 Monthly

Monthly History가 새로운 CEO Decision Required 항목을 자동 확정하지 않는다.

다만 기존 History에서:

    unresolved strategic decision

이 명확하다면 향후 COO Report 생성 시 근거자료로 사용할 수 있다.

---

## 68. Company Intelligence와 Monthly

Monthly History는 Company Intelligence의 핵심 중간 Layer다.

구조:

    Daily History
          ↓
    Monthly History
          ↓
    Long-term Company Intelligence

장기적으로 다음 질문에 답하는 기반이 된다.

    회사가 어떻게 발전했는가?

    어떤 중요한 결정을 했는가?

    어떤 위험을 겪었는가?

    무엇을 배웠는가?

    Product가 어떻게 변했는가?

---

## 69. 투자/M&A/IPO 관점

Monthly History는 향후 다음 자료를 정리할 때 Source가 될 수 있다.

- Product Evolution
- 주요 경영 Decision
- 주요 기술 Milestone
- 핵심 Risk와 해결
- Customer Learning
- KPI 변화
- 사업전략 변화

그러나 Monthly 자체를 투자보고서나 실사보고서로 만들지는 않는다.

---

## 70. Monthly 파일 크기

Monthly가 지나치게 길어지면 Daily 복사본이 된 것이다.

목표는:

    압축

이다.

정확한 글자 수 제한을 강제하지는 않는다.

대신:

> Daily 전체를 읽는 것보다 Monthly 하나를 읽는 것이 훨씬 빨라야 한다.

를 기준으로 한다.

---

## 71. History가 거의 없는 월

중요 History가 거의 없는 달도 정상이다.

예:

    # DOJOONPASS Company History — 2026-10

    ## Executive Summary

    No material company-level changes were recorded during this month.

억지로 내용을 생성하지 않는다.

---

## 72. History가 없는 월

대상 월 모든 Daily가 Empty이고 중요 History가 없다면 Monthly 파일은 생성할 수 있다.

이유:

    해당 월 누락

과:

    중요한 변화 없음

을 구분하기 위해서다.

---

## 73. Monthly Empty 상태

필요하면:

    MONTHLY_GENERATED_EMPTY

상태를 사용할 수 있다.

구현 단순화를 위해 `MONTHLY_GENERATED` + Material Events 0으로 처리해도 된다.

---

## 74. Local 저장 실패

Monthly Markdown 생성:

    PASS

Local 저장:

    FAIL

결과:

    MONTHLY_FAILED

State:

    갱신하지 않음

다음 Runner에서 재시도한다.

---

## 75. Backup 실패

Monthly Local 저장:

    PASS

GitHub Backup:

    FAIL

결과:

    Monthly
    SUCCESS

    Backup
    BACKUP_PENDING

Local Master가 공식 원본이다.

---

## 76. Monthly Backup

Backup 대상에:

    history\
    └─ monthly\

를 포함한다.

구조:

    Monthly Local
        ↓
    Safe Working Copy
        ↓
    Git Commit
        ↓
    Private Remote

`08_BACKUP_SPEC.md` 규칙을 그대로 적용한다.

---

## 77. Monthly 삭제

자동 삭제하지 않는다.

Daily와 마찬가지로 과거 Monthly History를 오래됐다는 이유로 삭제하지 않는다.

---

## 78. Monthly 수정

자동 수정이 허용되는 대표적 상황:

    Late Event
        ↓
    Daily Update
        ↓
    MONTHLY_DIRTY
        ↓
    Monthly Update

그 외 자동 수정은 최소화한다.

---

## 79. Monthly 생성 안전성

Daily와 동일하게 가능하면:

    Temporary File
        ↓
    Write
        ↓
    Validation
        ↓
    Final Replace

방식을 사용한다.

작성 중 프로그램 종료로 기존 Monthly가 깨지는 것을 방지한다.

---

## 80. Encoding

기본:

    UTF-8

한글/영문 혼용을 정상 처리한다.

---

## 81. Monthly Validation

생성 후 최소 확인:

    올바른 대상 월인가?

    Daily Coverage COMPLETE인가?

    파일이 생성됐는가?

    파일이 비정상적으로 Empty인가?

    Metadata가 존재하는가?

    Source Records가 추적 가능한가?

---

## 82. Monthly와 Notion

Notion:

    지금 상태

Monthly:

    지난달 변화

역할이 다르다.

Notion 내용을 Monthly 원본으로 직접 Dump하지 않는다.

---

## 83. Monthly와 COO Report

향후 COO Report는:

    Notion Current State
        +
    Monthly History
        +
    CTO Report
        +
    CMO Report

등을 통합할 수 있다.

하지만 Monthly History 자체는 COO 판단 보고서가 아니다.

---

## 84. Monthly와 CEO 보고

Monthly History를 그대로 CEO 보고서로 보내는 구조를 강제하지 않는다.

CEO 보고는 필요에 따라:

    Executive Summary
    +
    Current State
    +
    Decision Required

형태로 별도 생성한다.

---

## 85. 최초 Monthly 기준

`history_start_date`가 월 중간이라면 그 달 Monthly는 해당 시작일부터만 생성한다.

예:

    history_start_date
    =
    2026-08-15

8월 Monthly:

    8월 15일
      ~
    8월 31일

Coverage Metadata에 이를 표시한다.

---

## 86. 자동화 이전 과거 월

Company Ops 시작 이전의 과거 월을 자동 생성하려 하지 않는다.

필요하면 별도 Historical Migration 프로젝트로 처리한다.

---

## 87. Monthly Catch-up State

예:

    {
      "history_start_date": "2026-08-01",
      "last_successful_monthly_close": "2026-08"
    }

현재 월 기준으로 누락된 과거 월을 계산한다.

---

## 88. Scheduler Trigger

별도의 Windows Monthly Task를 만들 수도 있지만 V1에서는 가능하면 기존 Company Ops Runner를 재사용한다.

매일 Runner 실행 시:

    오늘이 월초인가?
        ↓
    미생성 Monthly 존재하는가?
        ↓
    YES
        ↓
    Monthly Catch-up

이 방식이면 Scheduler Task 수를 불필요하게 늘리지 않을 수 있다.

---

## 89. 권장 V1 방식

권장:

    하나의 Company Ops Runner

Trigger:

    Daily 11:00

    Startup/Login

Runner 내부:

    Daily Catch-up
        ↓
    Monthly Catch-up
        ↓
    Backup

즉 Monthly용 별도 프로그램을 운영하지 않는다.

---

## 90. Monthly Catch-up은 날짜가 아니라 State 기반

단순히:

    오늘이 1일인가?

만 확인하면 안 된다.

예:

9월 1일 PC OFF.

9월 3일 PC ON.

오늘은 1일이 아니지만:

    8월 Monthly
    미생성

이므로 생성해야 한다.

따라서:

    State
      +
    현재 종료된 월

을 비교한다.

---

## 91. Mock Test — Normal Month

현재:

    2026-09-01
    11:00

8월 Daily:

    COMPLETE

기대:

    2026-08.md

생성.

State:

    last_successful_monthly_close
    =
    2026-08

---

## 92. Mock Test — Missing Daily

8월 30일 Daily 없음.

기대:

    Daily Catch-up 먼저

성공:

    Monthly 생성

실패:

    MONTHLY_PENDING

---

## 93. Mock Test — Empty Daily

8월 중 여러 Empty Daily 존재.

기대:

    누락으로 판단하지 않음

Monthly 정상 생성.

---

## 94. Mock Test — PC OFF on 1st

9월 1일 PC OFF.

9월 3일 PC ON.

기대:

    8월 Monthly 미생성 감지

    8월 Daily Coverage 확인

    8월 Monthly 생성

---

## 95. Mock Test — Multiple Missing Months

Last Monthly:

    2026-08

Current:

    2026-12

기대:

    2026-09
    2026-10
    2026-11

순서대로 생성.

---

## 96. Mock Test — Current Month

현재:

    2026-09-15

기대:

    2026-09 Monthly 생성 안 함

---

## 97. Mock Test — Major Decision

Daily:

    CEO approved Closed Beta Scope.

기대 Monthly:

    Major Decisions 포함

Authority:

    CEO

유지.

---

## 98. Mock Test — Issue Lifecycle

Daily:

    08-05 Issue identified

    08-12 Issue resolved

기대 Monthly:

    하나의 Major Issue Lifecycle로 정리 가능

Source 두 개 유지.

---

## 99. Mock Test — Open Issue

8월 말까지 해결되지 않은 중요 Issue.

기대:

    Major Issues

    또는

    Open Risks

에 OPEN 상태로 유지.

---

## 100. Mock Test — Product Evolution

8월:

    Search UI
        ↓
    Search API Integration
        ↓
    Closed Beta Ready

기대:

    Product Evolution에 단계 변화 기록.

---

## 101. Mock Test — No KPI

KPI 자료 없음.

기대:

    숫자 생성 안 함.

KPI Section 생략 또는 No verified KPI signal.

---

## 102. Mock Test — Late Event

8월 Monthly 생성 후:

    8월 중요 Late Event

도착.

기대:

    Daily Update

    MONTHLY_DIRTY

    Monthly Update

    Last Updated At 기록

---

## 103. Mock Test — AI Failure

AI Summary 기능 실패.

기대:

    Rule-based Monthly 생성

    Monthly 전체 실패 아님.

---

## 104. Mock Test — Backup Failure

Monthly Local:

    PASS

Backup:

    FAIL

기대:

    Monthly 성공

    BACKUP_PENDING

---

## 105. Test Matrix

| Test | 기대 결과 |
|---|---|
| 정상 월말 | 전월 Monthly 생성 |
| Daily 누락 | Catch-up 우선 |
| Empty Daily | 정상 Coverage |
| PC OFF 월초 | 다음 실행 Monthly Catch-up |
| 여러 달 누락 | 오래된 월부터 복구 |
| 현재 월 | 생성 금지 |
| 중요 Decision | Major Decisions |
| 중요 Milestone | Major Milestones |
| Issue 발생+해결 | Lifecycle 통합 |
| Open Issue | Open Risks |
| Learning | Evidence 기반 포함 |
| KPI 없음 | 숫자 생성 금지 |
| Product Evolution | 단계 변화 요약 |
| Late Event | MONTHLY_DIRTY |
| AI 실패 | Rule-based Fallback |
| Local Write 실패 | State 미갱신 |
| Backup 실패 | Local 유지 |

---

## 106. Phase 8 완료 기준

다음이 실제 검증되면 Phase 8을 PASS할 수 있다.

1. 종료된 전월을 정확히 계산한다.
2. 대상 월 Daily Coverage를 확인한다.
3. Missing Daily가 있으면 Daily Catch-up을 우선한다.
4. Empty Daily를 누락으로 판단하지 않는다.
5. Monthly Markdown을 생성한다.
6. Major Decision을 추출한다.
7. Major Milestone을 추출한다.
8. Major Issue와 Resolution을 연결할 수 있다.
9. Key Learning을 유지한다.
10. Product Evolution을 정리한다.
11. Open Risk를 유지한다.
12. Next-Month Carryover를 정리한다.
13. KPI가 없으면 생성하지 않는다.
14. Source Daily를 추적할 수 있다.
15. Local Master에 안전하게 저장한다.
16. Monthly State를 갱신한다.
17. PC OFF 이후 Catch-up이 가능하다.
18. 여러 달 누락을 순차 복구할 수 있다.
19. 현재 월을 생성하지 않는다.
20. Late Event 발생 시 MONTHLY_DIRTY 처리가 가능하다.
21. AI 없이 기본 Monthly를 생성할 수 있다.
22. Backup 실패가 Local Monthly를 손상시키지 않는다.

---

## 107. 구현 위치

예상 코드:

    D:\DOJOONPASS_COMPANY_OPS\
    history\
    monthly\

또는 기존 History Module 내부:

    history\
    ├─ daily
    └─ monthly

구체적인 구조는 구현 단계에서 최소화한다.

State:

    runtime\
    state\
    monthly_history_state.json

---

## 108. V1에서 만들지 않는 것

Monthly History V1에서는 다음을 만들지 않는다.

- Quarterly Report
- Annual Report
- Investor Report
- M&A Report
- IPO Report
- Board Report
- 자동 회사 가치평가
- 자동 전략 결정
- 자동 Go / No-Go
- 자동 Critical Path 계산
- KPI Database
- BI Dashboard
- Knowledge Graph
- Vector DB
- RAG
- Company AI Brain
- 복잡한 Narrative Generator
- 모든 Daily 내용 복제

---

## 109. 기록량 원칙

Monthly History의 성공 기준은 길이가 아니다.

예:

    Daily History
    31개

        ↓

    Monthly History
    핵심 사건 5개

여도 정상이다.

중요한 것은:

> 한 달 동안 회사가 실제로 어떻게 변했는지를 보존하는 것

이다.

---

## 110. COO 관점의 핵심

COO가 Monthly를 열었을 때 최소 다음 질문에 답할 수 있어야 한다.

    지난달 가장 중요한 결정은 무엇이었는가?

    무엇을 실제로 완료했는가?

    무엇이 가장 크게 막았는가?

    무엇이 해결됐는가?

    아직 무엇이 남아 있는가?

    Product는 어떻게 변했는가?

    고객에게서 무엇을 배웠는가?

이 질문에 답할 수 있으면 Monthly History의 목적은 달성한 것이다.

---

## 111. CEO 권한 보호

Monthly History Generator가 다음을 자동 결정하지 않는다.

    회사 전략

    Beta Scope

    Target

    Pricing

    Launch Date

    Final Go / No-Go

    주요 Product Priority

Monthly는 Decision을 기록한다.

Decision을 대신하지 않는다.

---

## 112. Stop Rule

다음 구조가 안정적으로 작동하면 Monthly 기능 개발을 종료한다.

    Daily History
          ↓
    Coverage Check
          ↓
    Monthly Consolidation
          ↓
    Local Monthly
          ↓
    Backup

그리고:

    PC OFF
      ↓
    Monthly Catch-up

    Late Event
      ↓
    Monthly Update

까지 작동하면 충분하다.

더 복잡한 Company Intelligence 기능으로 확장하지 않는다.

---

## 113. 완료 보고 형식

Phase 8 완료 시:

    [Phase]
    Phase 8 — Monthly History

    [상태]
    PASS / BLOCKED / FAIL

    [구현 내용]

    [변경 파일]

    [Previous Month Calculation]
    PASS / FAIL

    [Daily Coverage Check]
    PASS / FAIL

    [Missing Daily Catch-up]
    PASS / FAIL

    [Monthly Generation]
    PASS / FAIL

    [Decision Consolidation]
    PASS / FAIL

    [Milestone Consolidation]
    PASS / FAIL

    [Issue Lifecycle]
    PASS / FAIL

    [Learning Consolidation]
    PASS / FAIL

    [Product Evolution]
    PASS / FAIL

    [Open Risk]
    PASS / FAIL

    [Monthly Catch-up]
    PASS / FAIL

    [Late Event Update]
    PASS / FAIL

    [AI-independent Fallback]
    PASS / FAIL

    [Local Master Write]
    PASS / FAIL

    [Backup Integration]
    PASS / FAIL

    [Evidence]

    [발견된 문제]

    [다음 작업]

    [CEO/COO Decision Required]
    NONE 또는 해당 사항

---

## 114. 다음 명세

다음 단계:

    Phase 9
    End-to-End Validation & Operations

다음 문서:

    D:\DOJOONPASS_COMPANY_OPS\
    docs\
    10_E2E_OPERATIONS_SPEC.md

다음 문서에서는 지금까지 만든 전체 Pipeline을 하나로 묶어서 검증한다.

    Desktop Reporter
          ↓
    Execution Event
          ↓
    Collector
          ↓
    Notion Current State
          ↓
    History Filter
          ↓
    Daily History
          ↓
    Monthly History
          ↓
    Local Master
          ↓
    GitHub Backup

그리고 실제 장애 상황까지 검증한다.

    Desktop OFF

    Desktop 간 Event 지연

    중복 Event

    잘못된 Event

    Notion 장애

    Internet 장애

    GitHub Push 실패

    PC 강제 종료

    Late Event

    Scheduler 누락

    Backup 충돌

Phase 9의 목적은 새로운 기능 개발이 아니다.

> 지금까지 만든 Company Ops V1이 실제 운영환경에서 데이터 손실 없이 끝까지 돌아가는지 검증하고 운영 가능한 상태로 닫는 것

이다.

---

# END OF DOCUMENT