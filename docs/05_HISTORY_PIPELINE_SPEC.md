# D:\DOJOONPASS_COMPANY_OPS\docs\05_HISTORY_PIPELINE_SPEC.md

## DOJOONPASS Company Ops — History Pipeline Specification

---

## 1. 문서 정의

| 항목 | 내용 |
|---|---|
| 문서명 | Company Ops History Pipeline Specification |
| 프로젝트 | DOJOONPASS Company Ops |
| Owner | COO |
| 상위 문서 | `00_V1_DEVELOPMENT_SPEC.md` |
| 실행 기준 | `01_V1_IMPLEMENTATION_PLAN.md` |
| Event 기준 | `02_EVENT_SCHEMA.md` |
| Collector 기준 | `03_COLLECTOR_SPEC.md` |
| Notion 기준 | `04_NOTION_SYNC_SPEC.md` |
| 목적 | Execution Event 중 장기 보존 가치가 있는 회사 사건을 선별하여 Company History 생성 대상으로 전달하는 기준 정의 |
| 실행 위치 | Desktop 4 |
| 적용 버전 | V1 |

본 문서는 Company Ops의 History Pipeline을 정의한다.

History Pipeline의 목적은 모든 업무를 저장하는 것이 아니다.

핵심 질문은 다음과 같다.

> 이 사건이 향후 회사의 중요한 의사결정, 제품 발전, 운영 학습, 투자, M&A 또는 IPO 실사 과정에서 의미가 있는가?

YES인 사건만 Company History 후보로 남기는 것을 원칙으로 한다.

---

## 2. History Pipeline의 역할

전체 흐름:

    Execution Event
          ↓
      Collector
          ↓
      ACCEPTED
          ↓
    History Candidate?
        /        \
      NO          YES
      ↓            ↓
    종료      History Filter
                    ↓
              Classification
                    ↓
             Importance Check
                    ↓
               Candidate
                    ↓
               Daily Close
                    ↓
              Daily History
                    ↓
               Local Master

History Pipeline은 Event를 바로 Markdown History로 저장하지 않는다.

먼저 장기 보존 가치를 판단하기 위한 Candidate Layer를 거친다.

---

## 3. History와 Event의 차이

Execution Event:

> 업무상 어떤 상태 변화가 발생했는가?

Company History:

> 회사 차원에서 장기적으로 기억해야 할 사건은 무엇인가?

예:

    Search UI 개발 시작

Execution Event:

    STARTED

Company History:

    기본적으로 저장하지 않음

반면:

    Search MVP 핵심 검색 UI 완료

Execution Event:

    MILESTONE_COMPLETED

Company History:

    저장 후보

즉 Event와 History는 동일하지 않다.

----
## Decision Context

History는 결과만 저장하지 않는다.

가능하면 다음 정보를 함께 보존한다.

- 왜 시작했는가
- 왜 변경했는가
- 왜 중단했는가
- 왜 다시 시작했는가
- 어떤 선택지를 검토했는가
- 최종 선택 이유

---

## 4. History 저장 원칙

Company History는 다음 원칙을 따른다.

1. 모든 업무를 기록하지 않는다.
2. 모든 Commit을 기록하지 않는다.
3. 모든 완료 Task를 기록하지 않는다.
4. 모든 Bug를 기록하지 않는다.
5. 모든 AI 대화를 기록하지 않는다.
6. 회사 차원의 의미가 있는 사건만 남긴다.
7. 사실과 판단을 구분한다.
8. Evidence가 있으면 연결한다.
9. 중요한 Decision은 장기 보존한다.
10. 중요한 실패와 방향 변경도 보존한다.
11. 기록량보다 장기적 가치가 중요하다.

추가 원칙

7. 단순히 "무슨 일을 했다"보다 "왜 그 결정을 했는가"를 우선 보존한다.
8. 작업이 중단되었으면 중단 이유를 반드시 남긴다.
9. 다시 시작되었으면 재개 이유를 반드시 남긴다.
10. 중요한 방향 변경은 변경 이유와 함께 보존한다.
11. 동일한 작업이라도 결정의 근거가 달라졌다면 새로운 History로 기록할 수 있다.

---

## 5. 기본 History Category

V1 Company History는 네 개의 핵심 Category를 사용한다.

    DECISION

    MILESTONE

    ISSUE

    LEARNING

필요성이 확인되지 않은 Category는 추가하지 않는다.

---

## 6. DECISION

의미:

> 회사의 방향, 범위, 우선순위 또는 중요한 실행방식에 영향을 준 공식 의사결정.

대표 사례:

- Beta Scope 확정
- Target Customer 변경
- Pricing 방향 확정
- Launch 방식 결정
- 주요 Product Scope 변경
- 중요 Architecture 방향 확정
- 핵심 외부 서비스 채택/중단
- 중요한 사업 방향 변경

기본 Event:

    DECISION_APPROVED

중요:

COO 의견이나 CTO/CMO 제안만으로 DECISION History를 만들지 않는다.

실제 승인 또는 확정이 있어야 한다.

---

## 7. MILESTONE

의미:

> 회사 또는 Product 발전 과정에서 의미 있는 단계가 실제 완료됨.

예:

- MVP Search 기능 완성
- Crawler 핵심 Pipeline 완성
- Search API 통합 완료
- Production Deployment 성공
- Closed Beta 시작
- 첫 실제 사용자 유입
- 첫 결제
- 주요 Product Version 출시

기본 Event:

    MILESTONE_COMPLETED

    COMPLETED

단, 모든 `COMPLETED` Event를 MILESTONE으로 저장하지 않는다.

---

## 8. ISSUE

의미:

> 회사 또는 Product 진행에 실제 영향을 준 중요한 문제 또는 위험.

예:

- 핵심 데이터 정확성 문제
- Production 장애
- 중요한 외부 API 장애
- 서비스 Launch를 막는 Blocker
- 데이터 손실 위험
- Security 문제
- 핵심 Dependency 실패

관련 Event:

    BLOCKED

    ISSUE_RESOLVED

ISSUE는 문제 발생과 해결 모두 장기적 가치가 있을 수 있다.

---

## 9. LEARNING

의미:

> 향후 회사 의사결정에 영향을 줄 수 있는 검증된 학습.

예:

- 실제 사용자 VOC에서 반복 확인된 문제
- Beta 사용자의 핵심 행동 패턴
- 예상과 다른 Conversion 결과
- 특정 기능 사용률이 예상보다 현저히 낮음
- 특정 Acquisition Channel의 유효성 확인
- Product 가설이 실제 데이터로 반증됨

중요:

단순 의견이나 추측은 LEARNING이 아니다.

Evidence 또는 반복 관찰이 있어야 한다.

---

## 10. History Candidate 기본 규칙

Event Type별 기본 처리:

| Event Type | 기본 History 처리 |
|---|---|
| STARTED | 제외 |
| BLOCKED | 조건부 Candidate |
| RESUMED | 제외 |
| MILESTONE_COMPLETED | Candidate |
| COMPLETED | 조건부 Candidate |
| CANCELLED | 조건부 Candidate |
| ISSUE_RESOLVED | Candidate |
| DECISION_APPROVED | Candidate |

---

## 11. STARTED

기본:

    history_candidate = false

이유:

프로젝트 시작 자체를 모두 History로 저장하면 기록량이 지나치게 많아진다.

예외:

회사 차원에서 매우 중요한 공식 프로젝트 시작은 향후 필요성이 확인되면 기록할 수 있다.

V1 자동규칙에서는 기본 제외한다.

---

## 12. BLOCKED

기본:

    조건부 Candidate

다음 중 하나 이상이면 History Candidate가 될 수 있다.

- Launch에 영향
- Critical Path에 영향
- 핵심 데이터 신뢰성 영향
- 고객 서비스 영향
- 중요한 일정 지연
- Security 영향
- 재무적 영향
- 반복 발생 가능성이 높은 구조적 문제

사소한 개발 Bug는 제외한다.

---

## 13. RESUMED

기본:

    제외

단순히 작업이 다시 시작됐다는 사실은 장기 보존 가치가 낮다.

중요 Issue가 해결된 경우:

    ISSUE_RESOLVED

를 사용한다.

---

## 14. MILESTONE_COMPLETED

기본:

    Candidate

단, 다음처럼 지나치게 작은 Milestone은 제외할 수 있다.

    Button 수정

    CSS 수정

    파일 구조 변경

    README 작성

좋은 History Milestone:

    Search MVP UI Completed

    Auction Data Synchronization Stabilized

    Production Deployment Completed

---

## 15. COMPLETED

기본:

    조건부 Candidate

다음 질문을 적용한다.

> 이 완료가 Product 또는 회사의 발전 단계를 설명하는 데 필요한가?

YES:

    Candidate

NO:

    제외

예:

    Search Frontend MVP Completed

→ Candidate

    README 수정 Completed

→ 제외

---

## 16. CANCELLED

기본:

    조건부 Candidate

다음 경우 보존 가치가 높다.

- 중요 프로젝트 중단
- 전략 변경에 따른 기능 폐기
- 외부 Dependency 문제로 사업방향 변경
- 비용/효율성 판단에 따른 중요 시스템 중단

단순 Task 취소는 제외한다.

---

## 17. ISSUE_RESOLVED

기본:

    Candidate

단, 사소한 Bug 해결은 제외한다.

중요 Issue의 해결은 향후 다음 질문에 답할 수 있어야 한다.

> 당시 회사의 핵심 위험은 무엇이었고 어떻게 해결했는가?

---

## 18. DECISION_APPROVED

기본:

    Candidate

중요 Decision은 Company Intelligence의 핵심 자산이다.

예:

    Beta Scope approved

    Pricing model approved

    Product positioning changed

    Launch strategy approved

단, 실제 승인 Evidence가 있어야 한다.

---

## 19. History Importance Check

Candidate는 다음 질문으로 중요도를 확인한다.

    Q1. 회사 방향에 영향을 주었는가?

    Q2. Product Evolution을 설명하는가?

    Q3. Launch 또는 Critical Path에 영향을 주었는가?

    Q4. 중요한 기술/운영 Risk인가?

    Q5. 실제 Customer Learning인가?

    Q6. 중요한 KPI 변화와 관련되는가?

    Q7. 향후 같은 의사결정을 할 때 참고할 가치가 있는가?

    Q8. 투자/M&A/IPO 실사에서 설명 가치가 있는가?

하나 이상 명확하게 YES라면 History Candidate로 유지할 수 있다.

---

## 20. 점수 시스템 금지

V1에서는 다음과 같은 임의 점수 시스템을 만들지 않는다.

    Importance Score = 78

    History Score = 8.3

    Strategic Value = 92%

현재 회사 규모에서는 숫자 점수가 오히려 가짜 정밀도를 만든다.

기본 판단:

    KEEP

    DROP

    REVIEW

세 상태면 충분하다.

---

## 21. History Filter 결과

History Pipeline 결과는 다음 세 가지다.

    KEEP

    DROP

    REVIEW

---

## 22. KEEP

의미:

> 장기 보존 가치가 명확함.

Daily History 생성 대상으로 전달한다.

---

## 23. DROP

의미:

> Execution Event로는 유효하지만 Company History 장기 보존 가치는 낮음.

Event 원본은 Runtime 정책에 따라 존재할 수 있지만 Daily Company History에는 넣지 않는다.

---

## 24. REVIEW

의미:

> 자동규칙만으로 장기 보존 여부를 확정하기 어려움.

예:

- 중요도가 애매한 CANCELLED
- 장기 영향이 불확실한 BLOCKED
- 의미가 애매한 COMPLETED

REVIEW가 지나치게 많이 발생하도록 설계하지 않는다.

---

## 25. 자동 KEEP

V1에서 다음은 기본적으로 KEEP 후보로 처리할 수 있다.

    DECISION_APPROVED

    주요 MILESTONE_COMPLETED

    주요 ISSUE_RESOLVED

단, Event 자체가 명백하게 사소한 내용이면 Filter에서 제외할 수 있다.

---

## 26. 자동 DROP

다음은 기본 DROP 대상이다.

    STARTED

    RESUMED

그리고 다음과 같은 내용:

- 단순 코드 정리
- 작은 UI 수정
- 파일 이동
- 오탈자 수정
- 일반 테스트 실행
- 일반 Commit
- 일반 Debugging
- 작은 Bug 수정

---

## 27. History Candidate 데이터

History Candidate는 최소 다음 정보를 가진다.

    history_id

    event_id

    timestamp

    category

    project_id

    role

    summary

    decision_context

    evidence

    expected_outcome

    actual_outcome

    lessons_learned

    decision

    filter_result
    

---

## 28. history_id

History Candidate 자체의 식별자다.

Execution Event의 `event_id`와 구분한다.

Event 하나가 하나의 History Candidate가 되는 V1에서는 Event ID와 연결관계를 유지해야 한다.

---

## 29. event_id

원본 Execution Event를 추적하기 위한 ID.

History 기록에서 문제가 발생하면 원본 Event까지 역추적할 수 있어야 한다.

---

## 30. timestamp

History 사건이 실제 발생한 시간.

Collector 처리시간이 아니다.

원본 Event Timestamp를 따른다.

Daily History 날짜 결정에도 이 값을 사용한다.

---

## 31. category

허용값:

    DECISION

    MILESTONE

    ISSUE

    LEARNING

---

## 32. project_id

관련 Project 식별자.

예:

    AUCTION_DATA_SYNC

    SEARCH_FRONTEND

    CONTENT_OS

    COMPANY_OPS

---

## 33. role

관련 Owner 역할.

예:

    CTO_BACKEND

    CTO_FRONTEND

    CMO

    COO

---

## 34. summary

장기 기록에 적합한 핵심 사건 요약.

summary는 사실(Fact)만 기록한다.

판단은 decision_context에서 기록한다.

Event Summary를 그대로 사용할 수 있는 경우 사용한다.

단, 자동 시스템이 원본 사실에 없는 내용을 추가해서는 안 된다.

---

## 35. evidence

가능한 경우 원본 Evidence를 유지한다.

예:

    TypeScript PASS

    E2E PASS

    Deployment Success

    CEO Approval

    Customer Interview

    KPI Data

향후 Company Intelligence의 신뢰도를 높이는 근거다.

Evidence는 단순히 Event를 증명하기 위한 자료가 아니다.

Decision Context를 이해할 수 있는 근거도 Evidence가 될 수 있다.

예:

    Decision Document

    Architecture Review

    CEO Feedback

    Customer Research

    VOC Summary

자동 시스템은 존재하지 않는 Evidence를 생성해서는 안 된다.

---

## 36. decision

History Filter 결과:

    KEEP

    DROP

    REVIEW

`DECISION` Category와 혼동하지 않는다.

필요하면 구현에서는 `filter_result`만 사용하고 별도 `decision` Field는 생략할 수 있다.

---

## 37. History Candidate 예시

    {
      "history_id": "HIST-001",
      "event_id": "TEST-MILESTONE-001",
      "timestamp": "2026-08-01T20:00:00+09:00",
      "category": "MILESTONE",
      "project_id": "SEARCH_FRONTEND",
      "role": "CTO_FRONTEND",
      "summary": "Search UI implementation completed",
      "evidence": [
        "TypeScript PASS"
      ],
      "filter_result": "KEEP"
    }

---

## 38. Decision History 예시

{
  "history_id": "HIST-002",
  "event_id": "DECISION-001",
  "timestamp": "2026-08-10T10:00:00+09:00",
  "category": "DECISION",
  "project_id": "DOJOONPASS_PRODUCT",
  "role": "COO",
  "summary": "CEO approved Closed Beta scope",

  "decision_context": "Beta 범위를 먼저 제한하여 실제 사용자 검증을 우선하기로 결정",

  "expected_outcome": "초기 운영 리스크 감소",

  "actual_outcome": null,

  "lessons_learned": null,

  "evidence": [
      "CEO Approval"
  ],

  "filter_result": "KEEP"
}

중요:

`role = COO`라고 해서 COO가 Decision을 승인했다는 뜻은 아니다.

실제 Decision Authority는 회사 공식 권한체계를 따른다.

---

## 39. Issue History 예시

    {
      "history_id": "HIST-003",
      "event_id": "ISSUE-001",
      "timestamp": "2026-08-12T14:00:00+09:00",
      "category": "ISSUE",
      "project_id": "AUCTION_DATA_SYNC",
      "role": "CTO_BACKEND",
      "summary": "Auction item synchronization issue affected data reliability",
      "evidence": [
        "Synchronization validation failed"
      ],
      "filter_result": "KEEP"
      "decision_context":"데이터 신뢰성이 서비스 신뢰도보다 우선이라고 판단","expected_outcome":"동기화 정확도 확보",
      "actual_outcome":null,
      "lessons_learned":null,
    }

---

## 40. Learning History 예시

향후 Beta 운영 시:

    {
      "history_id": "HIST-004",
      "event_id": "LEARNING-001",
      "timestamp": "2026-09-10T16:00:00+09:00",
      "category": "LEARNING",
      "project_id": "DOJOONPASS_BETA",
      "role": "COO",
      "summary": "Multiple beta users had difficulty understanding auction status terminology",
      "evidence": [
        "Beta VOC"
      ],
      "filter_result": "KEEP"
      "decision_context":"데이터 신뢰성이 서비스 신뢰도보다 우선이라고 판단","expected_outcome":"동기화 정확도 확보",
      "actual_outcome":null,
      "lessons_learned":null,
    }

LEARNING은 추측이 아니라 실제 Evidence 기반이어야 한다.

---

## 41. Product Evolution

V1에서 `PRODUCT_EVOLUTION`이라는 별도 Category를 만들지 않는다.

Product Evolution은 여러 MILESTONE과 DECISION의 연속으로 표현한다.

예:

    Search Prototype
        ↓
    Search MVP
        ↓
    Search + Detail
        ↓
    Closed Beta
        ↓
    Public Launch

필요하면 Monthly Summary에서 Product Evolution 형태로 재구성한다.

---

## 42. KPI History

현재 V1에서는 KPI 자동수집을 구현하지 않는다.

향후 KPI가 존재할 경우 다음과 같은 변화는 History 가치가 있을 수 있다.

- 첫 가입
- 첫 유료고객
- Conversion의 의미 있는 변화
- Retention 변화
- CAC 변화
- Revenue Milestone

단순 일일 숫자 변화는 Company History가 아니다.

---

## 43. VOC History

모든 VOC를 Company History에 넣지 않는다.

History 가치가 높은 VOC:

- 반복되는 핵심 Pain Point
- Product 방향을 변경시킨 VOC
- 중요한 기능 요청 패턴
- 예상과 다른 사용자 행동
- 중요한 구매/이탈 이유

개별 단순 문의는 제외한다.

---

## 44. 실패 기록

Company History는 성공만 기록하는 시스템이 아니다.

중요한 실패도 남긴다.

예:

    중요한 기능 가설 실패

    핵심 Integration 실패

    Launch 지연 원인

    중요 프로젝트 중단

    주요 Acquisition 실험 실패

목적은 책임 추궁이 아니다.

향후 동일한 실수를 반복하지 않기 위한 Company Intelligence다.

---

## 45. 사실과 해석 분리

History에는 확인된 사실을 우선 기록한다.

좋은 예:

    Search API integration E2E test passed.

좋지 않은 예:

    Search 기능은 앞으로 엄청난 경쟁력이 될 것이다.

후자는 전략적 의견이지 History Fact가 아니다.

---

## 46. Evidence 원칙

가능한 경우 History에 Evidence를 연결한다.

Evidence 예:

    Commit Hash

    Test Result

    Deployment Record

    CEO Approval

    KPI Report

    VOC Source

    Decision Document

Evidence가 없다는 이유만으로 모든 History를 폐기하지는 않는다.

다만 자동 시스템이 Evidence를 만들어내서는 안 된다.

---

## 47. History 원본 위치

공식 Company History Master:

    D:\DOJOONPASS_COO\
    └─ history\

프로그램 코드:

    D:\DOJOONPASS_COMPANY_OPS\

두 영역을 분리한다.

---

## 48. Candidate Runtime

History Candidate는 최종 Daily Close 전에 Runtime 영역에서 관리할 수 있다.

예:

    D:\DOJOONPASS_COMPANY_OPS\
    └─ runtime\
        └─ history_candidates\

예상 구조:

    history_candidates\
    ├─ pending\
    ├─ keep\
    ├─ review\
    └─ drop\

단, 실제 구현에서 더 단순한 구조가 가능하면 단순 구조를 우선한다.

---

## 49. DROP 데이터

DROP된 Candidate를 영구적으로 모두 저장할 필요는 없다.

원본 Execution Event가 존재하므로 Company History에 포함하지 않는 것이 핵심이다.

Debugging 목적의 단기 보존은 가능하다.

V1에서 복잡한 Retention Policy는 만들지 않는다.

---

## 50. REVIEW 처리

REVIEW는 자동화가 애매한 사건을 위한 예외 상태다.

원칙:

    REVIEW가 너무 많다
          ↓
    자동화 실패 신호

따라서 가능한 경우 명확한 규칙으로 KEEP 또는 DROP한다.

COO가 매일 수십 개의 REVIEW를 수동 처리해야 하는 구조를 만들지 않는다.

---

## 51. Daily Close와의 관계

History Pipeline은 Daily History 파일을 직접 생성하지 않는다.

구조:

    History Pipeline
          ↓
    KEEP Candidates
          ↓
      Daily Close
          ↓
    YYYY-MM-DD.md

Daily Close의 세부 규칙은 별도 Daily History 문서에서 정의한다.

---

## 52. 날짜 기준

History는 Event의 실제 Timestamp 기준 날짜에 속한다.

예:

    Event 발생
    2026-08-05 22:30

    Collector 처리
    2026-08-06 11:00

History 날짜:

    2026-08-05

Collector 처리 날짜:

    사용하지 않음

---

## 53. Late Event

늦게 도착한 Event도 실제 발생 날짜를 기준으로 History에 포함되어야 한다.

예:

    Event 발생
    8월 5일

    Event 수집
    8월 7일

History 대상 날짜:

    8월 5일

이미 Daily Close된 날짜에 Late Event가 도착한 경우 처리방식은 Daily Close/Catch-up 구현에서 별도로 관리한다.

기존 확정 History를 조용히 덮어쓰지 않는다.

---

## 54. History 중복 방지

동일 `event_id` 기반 Candidate가 두 번 생성되어서는 안 된다.

기본 관계:

    event_id
       ↓
    Candidate
       ↓
    한 번만 생성

Collector의 Duplicate Protection과 함께 이중 방어할 수 있다.

---

## 55. History 삭제 원칙

공식 Local History로 확정된 기록을 자동 삭제하지 않는다.

잘못된 기록이 확인될 경우 조용히 삭제하거나 덮어쓰기보다는 정정 흔적을 남길 수 있는 구조를 향후 검토한다.

V1에서는 자동 History 삭제 기능을 만들지 않는다.

---

## 56. AI 사용 원칙

History Pipeline에 AI를 사용할 수 있는 영역:

- Summary 정리
- Category 보조
- 중복 표현 정리
- Monthly Summary 생성 보조

하지만 AI가 다음을 해서는 안 된다.

- 존재하지 않는 사건 생성
- 존재하지 않는 Evidence 생성
- CEO Decision 추정
- 중요도를 임의 숫자로 생성
- 사실을 전략적 해석으로 바꿈
- 누락된 정보를 추측으로 채움

---

## 57. AI 없는 Fallback

Company History 핵심 Pipeline은 AI 서비스가 실패하더라도 최소 기능이 작동할 수 있어야 한다.

예:

    Event Summary
       ↓
    Rule-based Category
       ↓
    KEEP
       ↓
    Daily History

AI가 없다는 이유로 History 원본이 생성되지 않는 구조는 피한다.

V1에서는 Rule-based 처리를 우선한다.

---

## 58. History Filter 우선순위

V1 우선순위:

    Rule-based
        ↓
    필요한 경우 AI 보조
        ↓
    COO Review는 예외

처음부터 모든 Event를 LLM에 보내지 않는다.

---

## 59. 개인정보 및 Secret

History에 다음 정보를 자동 저장하지 않는다.

- Password
- API Token
- Secret Key
- 인증정보
- 불필요한 개인정보
- 환경변수 전체
- 민감한 Raw Log

Evidence에 Secret이 포함되지 않도록 주의한다.

---

## 60. Mock Test — STARTED

입력:

    event_type = STARTED

기대:

    DROP

Daily History:

    포함하지 않음

---

## 61. Mock Test — Major Milestone

입력:

    event_type = MILESTONE_COMPLETED

    milestone = Search MVP

기대:

    category = MILESTONE

    filter_result = KEEP

---

## 62. Mock Test — Minor Completed

입력:

    event_type = COMPLETED

    summary = README typo correction completed

기대:

    DROP

---

## 63. Mock Test — Critical Blocker

입력:

    event_type = BLOCKED

    summary = Auction synchronization issue affects data reliability

기대:

    category = ISSUE

    KEEP 또는 REVIEW

V1 테스트에서는 명확한 Critical Blocker Sample을 KEEP으로 정의할 수 있다.

---

## 64. Mock Test — Issue Resolved

입력:

    event_type = ISSUE_RESOLVED

    summary = Auction synchronization issue resolved

기대:

    category = ISSUE

    filter_result = KEEP

---

## 65. Mock Test — CEO Decision

입력:

    event_type = DECISION_APPROVED

    summary = CEO approved Closed Beta scope

기대:

    category = DECISION

    filter_result = KEEP

---

## 66. Mock Test — Resume

입력:

    event_type = RESUMED

기대:

    DROP

단, 중요한 Issue 해결은 별도의 ISSUE_RESOLVED Event로 기록한다.

---

## 67. Test Matrix

| Test | Category | 기대 결과 |
|---|---|---|
| STARTED | - | DROP |
| RESUMED | - | DROP |
| Major Milestone | MILESTONE | KEEP |
| Minor Completed | - | DROP |
| Critical Blocker | ISSUE | KEEP |
| Minor Blocker | - | DROP |
| Major Issue Resolved | ISSUE | KEEP |
| CEO Decision Approved | DECISION | KEEP |
| Major Cancellation | ISSUE 또는 DECISION 관련 | KEEP/REVIEW |
| Minor Cancellation | - | DROP |
| Evidence-based Customer Learning | LEARNING | KEEP |
| 단순 의견 | - | DROP |

---

## 68. Phase 4 완료 기준

다음이 검증되면 Phase 4를 PASS할 수 있다.

1. ACCEPTED Event를 History Pipeline이 읽는다.
2. `history_candidate`를 확인한다.
3. Category 분류가 가능하다.
4. KEEP / DROP / REVIEW가 구분된다.
5. STARTED가 기본 제외된다.
6. RESUMED가 기본 제외된다.
7. 주요 Milestone이 KEEP된다.
8. 사소한 Completed가 DROP된다.
9. Critical Blocker가 ISSUE로 분류된다.
10. 주요 Issue Resolution이 KEEP된다.
11. CEO 승인 Decision이 DECISION으로 분류된다.
12. 중복 Candidate가 생성되지 않는다.
13. Event Timestamp가 유지된다.
14. Evidence가 유지된다.
15. AI 없이 기본 Pipeline이 작동한다.

---

## 69. 구현 위치

예상 코드 위치:

    D:\DOJOONPASS_COMPANY_OPS\
    └─ history\

Runtime Candidate:

    D:\DOJOONPASS_COMPANY_OPS\
    └─ runtime\
        └─ history_candidates\

구체적인 파일구조는 구현 단계에서 최소한으로 결정한다.

---

## 70. V1에서 만들지 않는 것

History Pipeline V1에서는 다음을 만들지 않는다.

- RAG
- Vector Database
- Company Knowledge Graph
- 자동 M&A Report
- 자동 IPO Report
- 전사 AI Memory
- 모든 AI Conversation Archive
- 모든 Git Commit Archive
- 모든 Task Archive
- 모든 VOC Archive
- 모든 KPI Archive
- 복잡한 Importance Scoring
- 복잡한 ML Classification
- 별도 History Dashboard
- 별도 History Web UI

---

## 71. Company Intelligence와의 관계

Company History는 향후 Company Intelligence의 원재료다.

현재:

    Execution
        ↓
    Important Events
        ↓
    Company History

향후 필요 시:

    Company History
        +
    Decisions
        +
    KPI
        +
    VOC
        +
    Product Evolution
        ↓
    Company Intelligence

그러나 V1에서는 Company Intelligence 전체를 구현하지 않는다.

우선 신뢰할 수 있는 History 원본을 축적한다.

---

## 72. COO 운영 관점

History Pipeline이 성공했는지는 기록량으로 판단하지 않는다.

좋은 상태:

    하루 Event 50개
          ↓
    History 2개

일 수도 있다.

반대로 중요한 일이 없었던 날은:

    History 0개

여도 정상이다.

목적은 많이 기록하는 것이 아니다.

중요한 것을 놓치지 않는 것이다.

---

## 73. Stop Rule

다음이 작동하면 History Filter 기능을 더 복잡하게 만들지 않는다.

    중요한 Decision
          ↓
        KEEP

    중요한 Milestone
          ↓
        KEEP

    중요한 Issue
          ↓
        KEEP

    중요한 Learning
          ↓
        KEEP

    사소한 업무
          ↓
        DROP

이 기본 분리가 안정적으로 작동하면 다음 Phase로 넘어간다.

---

## 74. 완료 보고 형식

Phase 4 종료 시 다음 형식으로 보고한다.

    [Phase]
    Phase 4 — History Pipeline

    [상태]
    PASS / BLOCKED / FAIL

    [구현 내용]

    [변경 파일]

    [실제 테스트]

    [STARTED Filter]
    PASS / FAIL

    [Milestone Classification]
    PASS / FAIL

    [Issue Classification]
    PASS / FAIL

    [Decision Classification]
    PASS / FAIL

    [Minor Event Drop]
    PASS / FAIL

    [Duplicate Protection]
    PASS / FAIL

    [Timestamp Preservation]
    PASS / FAIL

    [Evidence Preservation]
    PASS / FAIL

    [AI-independent Fallback]
    PASS / FAIL

    [Evidence]

    [발견된 문제]

    [다음 작업]

    [CEO/COO Decision Required]
    NONE 또는 해당 사항

---

## 75. 다음 명세

History Pipeline 다음 단계:

    Daily Close

다음 문서:

    D:\DOJOONPASS_COMPANY_OPS\
    docs\
    06_DAILY_HISTORY_SPEC.md

다음 문서에서는 다음을 정의한다.

    전날 History를 언제 마감하는가?

    오전 11시 실행을 어떻게 적용하는가?

    KEEP Candidate를 어떻게 하나의 Daily History로 만드는가?

    History가 없는 날은 어떻게 처리하는가?

    PC가 꺼져 있었을 때 어떻게 Catch-up하는가?

    Late Event가 들어왔을 때 기존 History를 어떻게 보호하는가?

    Local Master를 어떻게 저장하는가?

---

# END OF DOCUMENT