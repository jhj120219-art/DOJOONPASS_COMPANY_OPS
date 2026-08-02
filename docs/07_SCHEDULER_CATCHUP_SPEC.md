# D:\DOJOONPASS_COMPANY_OPS\docs\07_SCHEDULER_CATCHUP_SPEC.md

## DOJOONPASS Company Ops — Scheduler & Catch-up Specification

---

## 1. 문서 정의

| 항목 | 내용 |
|---|---|
| 문서명 | Company Ops Scheduler & Catch-up Specification |
| 프로젝트 | DOJOONPASS Company Ops |
| Owner | COO |
| 상위 문서 | `00_V1_DEVELOPMENT_SPEC.md` |
| 실행 기준 | `01_V1_IMPLEMENTATION_PLAN.md` |
| Event 기준 | `02_EVENT_SCHEMA.md` |
| Collector 기준 | `03_COLLECTOR_SPEC.md` |
| Notion 기준 | `04_NOTION_SYNC_SPEC.md` |
| History 기준 | `05_HISTORY_PIPELINE_SPEC.md` |
| Daily History 기준 | `06_DAILY_HISTORY_SPEC.md` |
| 목적 | Daily History 자동 실행 및 Desktop 4 OFF 상황에서 누락된 History를 자동 복구하는 기준 정의 |
| 실행 위치 | Desktop 4 |
| 기본 정기 실행 | 매일 오전 11:00 |
| Timezone | Asia/Seoul |
| 적용 버전 | V1 |

본 문서는 Company Ops의 Scheduler 및 Catch-up 동작을 정의한다.

핵심 목표는 다음과 같다.

> Desktop 4가 매일 오전 11시에 켜져 있지 않아도 Company History가 누락되지 않도록 한다.

---

## 2. 핵심 운영 원칙

Company Ops는 Desktop 4가 항상 켜져 있다고 가정하지 않는다.

실제 운영 환경에서는 다음 상황이 발생할 수 있다.

- 오전 11시에 Desktop 4 OFF
- 며칠 동안 Desktop 4 OFF
- Windows Update
- 재부팅
- Scheduler 실행 실패
- 프로그램 오류
- 네트워크 오류
- GitHub 장애
- Notion 장애

이러한 상황이 발생해도 History 원본이 누락되어서는 안 된다.

---

## 3. Scheduler와 Catch-up의 역할

Scheduler:

> 정해진 시간에 Company Ops 작업을 실행한다.

Catch-up:

> 실행되지 못했던 과거 작업을 찾아 복구한다.

구조:

    Scheduler
       ↓
    정상 실행

또는:

    실행 실패 / PC OFF
          ↓
      다음 실행
          ↓
       Catch-up
          ↓
      누락 복구

---

## 4. 기본 정기 실행

Daily Scheduler:

    매일 오전 11:00

Timezone:

    Asia/Seoul

실행 대상:

    기본적으로 D-1

예:

    2026-08-06
    오전 11:00

        ↓

    2026-08-05
    Daily History 처리

---

## 5. 오전 11시의 의미

오전 11시는 History 날짜 기준이 아니다.

오전 11시는:

> 전날 History를 정기적으로 마감하기 위한 실행 기준 시각

이다.

History 자체의 날짜는 Event Timestamp를 기준으로 한다.

---

## 6. Scheduler 구현 환경

Desktop 4는 Windows 환경을 기준으로 한다.

V1 기본 자동 실행 수단:

    Windows Task Scheduler

별도의 24시간 Scheduler Server를 구축하지 않는다.

---

## 7. Windows Task Scheduler 사용 이유

현재 환경에서는:

- Desktop 4가 중앙 COO PC
- 항상 켜져 있지 않을 수 있음
- 별도 서버 운영 필요 없음
- Windows 기본 기능 사용 가능
- 외부 Scheduler 서비스 추가 필요 없음

따라서 V1에서는 Windows Task Scheduler가 적절하다.

---

## 8. V1에서 추가하지 않는 Scheduler

다음은 V1에서 도입하지 않는다.

- Airflow
- Prefect
- Celery
- Kubernetes CronJob
- AWS Scheduler
- 별도 VPS Scheduler
- 별도 Cloud Server
- 외부 Workflow SaaS

현재 규모에서는 과도하다.

---

## 9. Scheduler 기본 작업

정기 실행 시 기본 흐름:

    Windows Task Scheduler
            ↓
    Company Ops Runner
            ↓
    Lock 확인
            ↓
    Collector 실행
            ↓
    Pending Event 처리
            ↓
    Catch-up 확인
            ↓
    Daily Close
            ↓
    Backup Pending 확인
            ↓
    State 저장
            ↓
    종료

---

## 10. Collector 선행 실행

Daily Close 전에 가능한 Pending Event를 먼저 수집해야 한다.

이유:

    Daily Close 먼저 실행
          ↓
    아직 Collector가 처리하지 않은
    전날 Event 존재
          ↓
    History 누락

을 방지하기 위해서다.

따라서 기본 순서:

    Collector
        ↓
    History Pipeline
        ↓
    Daily Close

를 따른다.

---

## 11. 실행 상태 확인

Scheduler 실행 시 최소 다음 State를 확인한다.

    last_successful_daily_close

    pending_retry

    backup_pending

필요하면:

    last_successful_monthly_close

를 향후 Monthly 단계에서 추가한다.

---

## 12. Daily State 예시

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

## 13. Catch-up 판단 기준

현재 날짜:

    CURRENT_DATE

마지막 성공 날짜:

    LAST_SUCCESSFUL_DATE

처리 가능한 최대 날짜:

    CURRENT_DATE - 1 DAY

따라서:

    LAST_SUCCESSFUL_DATE + 1
             ↓
    CURRENT_DATE - 1

사이의 날짜를 확인한다.

---

## 14. 정상 상황

현재:

    2026-08-06

마지막 성공:

    2026-08-04

오전 11시 실행:

처리 대상:

    2026-08-05

처리 성공:

    last_successful_daily_close
    =
    2026-08-05

---

## 15. PC OFF — 하루

상황:

    8월 6일 오전 11시
    Desktop 4 OFF

마지막 성공:

    2026-08-04

Desktop 4 다음 실행:

    2026-08-07

Catch-up 대상:

    2026-08-05
    2026-08-06

순서:

    8월 5일
       ↓
    성공
       ↓
    8월 6일
       ↓
    성공

---

## 16. PC OFF — 여러 날

마지막 성공:

    2026-08-05

다음 실행:

    2026-08-10

처리 대상:

    2026-08-06
    2026-08-07
    2026-08-08
    2026-08-09

오래된 날짜부터 순서대로 처리한다.

---

## 17. Catch-up 순서

항상:

    Oldest Missing Date
            ↓
          ...
            ↓
    Newest Missing Date

순서로 처리한다.

예:

    2026-08-06
        ↓
    2026-08-07
        ↓
    2026-08-08
        ↓
    2026-08-09

---

## 18. 당일 처리 금지

현재 날짜는 Daily History로 마감하지 않는다.

예:

현재:

    2026-08-10

최대 처리 가능:

    2026-08-09

금지:

    2026-08-10.md
    당일 오전 생성

당일 업무가 아직 진행 중일 수 있기 때문이다.

---

## 19. PC 시작 Catch-up

오전 11시 Scheduler만 사용하면:

    오전 11시 PC OFF
          ↓
    오후 PC ON
          ↓
    다음날까지 기다림

상황이 발생할 수 있다.

따라서 V1에서는 PC 시작 또는 사용자 로그인 시:

    Company Ops Catch-up Check

를 실행할 수 있도록 한다.

---

## 20. Startup Catch-up 목적

Startup Catch-up은 다음을 확인한다.

    누락 Daily Close 존재?

    Pending Event 존재?

    Retry 대상 존재?

    Backup Pending 존재?

존재하면 필요한 작업을 실행한다.

---

## 21. 오전 11시 이전 PC 시작

예:

현재:

    2026-08-10
    오전 09:00

마지막 성공:

    2026-08-07

처리 가능한 종료 날짜:

    2026-08-09

따라서:

    2026-08-08
    2026-08-09

Catch-up 가능하다.

정기 Scheduler 시각인 오전 11시까지 기다릴 필요는 없다.

---

## 22. 오전 11시 이후 PC 시작

예:

현재:

    2026-08-10
    오후 15:00

마지막 성공:

    2026-08-08

처리 대상:

    2026-08-09

즉시 Catch-up한다.

---

## 23. 정상 정기 실행과 Startup 중복

예:

    10:59
    PC 로그인

Startup Catch-up 실행

    11:00
    Daily Scheduler 실행

두 프로세스가 동시에 같은 날짜를 처리할 수 있다.

따라서 중복 실행 방지가 필요하다.

---

## 24. Process Lock

V1에서는 간단한 Process Lock을 사용한다.

예:

    runtime\
    └─ locks\
        └─ company_ops.lock

실행 시작:

    Lock 존재?
      /       \
    YES       NO
     ↓         ↓
    종료      Lock 생성
                ↓
             실행
                ↓
             완료
                ↓
             Lock 제거

---

## 25. Lock의 목적

다음 상황을 방지한다.

    Scheduler A
        ↓
    Daily Close

동시에:

    Startup B
        ↓
    같은 Daily Close

결과:

    중복 파일 수정
    State 충돌
    Backup 충돌

Lock으로 하나의 Runner만 실행되게 한다.

---

## 26. Stale Lock 문제

프로그램 실행 중 PC가 강제 종료되면 Lock 파일이 남을 수 있다.

따라서:

    Lock 존재
        ↓
    무조건 영구 중단

구조를 사용하지 않는다.

Lock에는 최소한 다음 정보를 기록할 수 있다.

    process_id

    created_at

예:

    {
      "process_id": 12345,
      "created_at": "2026-08-10T10:59:00+09:00"
    }

---

## 27. Stale Lock 판단

Lock이 존재할 경우:

    해당 Process가 실제 실행 중인가?

를 확인한다.

실행 중이면:

    새 실행 종료

실행 중이 아니면:

    Stale Lock
        ↓
    제거
        ↓
    새 실행 진행

단순 시간 경과만으로 실행 중인 정상 Process를 강제 종료하지 않는다.

---

## 28. 실행 중 PC 종료

예:

    Catch-up 4일 처리 중

    8월 6일 PASS
    8월 7일 PASS
    8월 8일 처리 중

        ↓

    PC OFF

다음 실행 시:

    last_successful_daily_close

를 기준으로 다시 시작한다.

8월 6일, 7일을 다시 성공 처리할 필요가 없다.

8월 8일부터 재개한다.

---

## 29. 날짜별 State Commit

Catch-up 여러 날짜를 처리할 때 모든 날짜가 끝난 뒤 한 번만 State를 갱신하지 않는다.

예:

    8월 6일 성공
        ↓
    State = 8월 6일

    8월 7일 성공
        ↓
    State = 8월 7일

    8월 8일 실패
        ↓
    State = 8월 7일 유지

이 방식으로 중간 실패 복구를 단순하게 만든다.

---

## 30. 중간 날짜 실패

예:

    8월 6일 PASS

    8월 7일 FAIL

    8월 8일 미처리

기본 정책:

    8월 7일에서 중단

이유:

History 날짜 연속성을 유지하기 위해서다.

State:

    last_successful_daily_close
    =
    2026-08-06

다음 실행:

    8월 7일부터 다시 시작

---

## 31. Empty Day 성공

KEEP Candidate가 없는 날:

    GENERATED_EMPTY

도 정상 성공으로 본다.

따라서 State를 다음 날짜로 진행시킨다.

---

## 32. Daily Close 실패

다음 상황은 해당 날짜 성공으로 기록하지 않는다.

- Candidate 조회 실패
- Markdown 생성 실패
- Local Master 저장 실패
- 파일 Validation 실패

State를 진행시키지 않는다.

---

## 33. Backup 실패와 Daily State

Local History 생성:

    PASS

Backup:

    FAIL

이면 Daily Close 자체는 성공으로 처리할 수 있다.

State:

    Daily Success

Backup State:

    BACKUP_PENDING

으로 분리한다.

---

## 34. Notion 실패와 Daily History

Notion API 실패가 반드시 Daily History 실패를 의미하지 않는다.

구조:

    Event
      ↓
    Collector
      ↓
    ┌──────────────┐
    ↓              ↓
    Notion       History

Notion:

    FAIL

History:

    PASS

가능하다.

따라서 Notion 장애 때문에 Local Company History 생성을 막지 않는다.

---

## 35. Network 없는 상황

Desktop 4는 인터넷 연결 없이도 Local History 생성이 가능한 범위에서는 계속 처리해야 한다.

가능:

    이미 Local에 존재하는 Event
        ↓
    History Pipeline
        ↓
    Daily History
        ↓
    Local Master

불가능한 외부 작업:

    GitHub Sync
    Notion Sync

이 경우 Pending으로 남긴다.

---

## 36. Pending Retry

외부 서비스 장애 등으로 재처리가 필요한 작업은 Pending 상태로 관리한다.

예:

    NOTION_PENDING

    BACKUP_PENDING

Event 자체가 잘못된 경우의 REJECTED와 혼동하지 않는다.

---

## 37. Scheduler 실행 순서

V1 기본 Runner 순서:

    1. Lock 획득

    2. State Load

    3. Collector 실행

    4. Pending Event 처리

    5. Notion Retry

    6. History Pipeline

    7. Missing Daily Date 계산

    8. Daily Catch-up

    9. Backup Pending 처리

    10. State Save 확인

    11. Log 기록

    12. Lock 해제

---

## 38. 실패 격리

한 외부 시스템 실패가 전체 시스템을 불필요하게 멈추게 하지 않는다.

예:

    Notion FAIL

하지만:

    History Local 생성 가능

이면 계속 진행한다.

반면:

    Local History Write FAIL

은 공식 원본 생성 실패이므로 해당 날짜 Daily Close를 성공 처리하면 안 된다.

---

## 39. Scheduler Log

각 Runner 실행마다 최소 다음을 기록한다.

    Run ID

    Start Time

    Trigger

    Collector Result

    Catch-up Dates

    Daily Result

    Notion Result

    Backup Result

    End Time

    Final Status

---

## 40. Trigger Type

Runner가 어떤 방식으로 실행됐는지 기록한다.

허용값 예:

    SCHEDULED

    STARTUP

    MANUAL

---

## 41. Run ID

각 Runner 실행을 구분하기 위한 고유 ID를 생성할 수 있다.

예:

    RUN-20260810-110000

또는 UUID.

목적:

문제 발생 시 해당 실행 전체를 추적하기 위함이다.

---

## 42. Scheduler Log 위치

예:

    D:\DOJOONPASS_COMPANY_OPS\
    runtime\
    logs\
    scheduler\

예:

    2026-08-10.log

V1에서는 지나치게 복잡한 Logging Infrastructure를 만들지 않는다.

---

## 43. Secret Logging 금지

Log에 다음을 기록하지 않는다.

    GitHub Token

    Notion Token

    Password

    API Secret

    Authorization Header

    .env 전체 내용

---

## 44. Manual Run

자동화 문제 발생 시 COO가 수동 실행할 수 있어야 한다.

예:

    Company Ops Runner
        ↓
    Manual

수동 실행도 동일한 Lock과 State 규칙을 따른다.

---

## 45. Manual Run이 별도 로직이면 안 되는 이유

자동 실행:

    Logic A

수동 실행:

    Logic B

처럼 두 개의 다른 처리 로직을 만들지 않는다.

둘 다 동일 Runner를 호출하고 Trigger만 다르게 기록한다.

---

## 46. 특정 날짜 재처리

필요한 경우:

    REPROCESS_DATE

기능을 사용할 수 있다.

예:

    2026-08-05

단:

기존 History가 존재하면 무조건 삭제 후 새로 생성하지 않는다.

`06_DAILY_HISTORY_SPEC.md`의 기존 History 보호 원칙을 따른다.

---

## 47. Catch-up 최대 기간

V1에서 임의로:

    최대 3일

    최대 7일

같은 제한을 두지 않는다.

State 이후 누락된 날짜가 존재하면 처리한다.

단, 실제 운영에서 매우 긴 누락기간으로 성능 문제가 발생하면 이후 정책을 검토한다.

---

## 48. 최초 실행 문제

Company Ops를 처음 설치했을 때는:

    last_successful_daily_close

가 존재하지 않을 수 있다.

이때 회사 창립일부터 모든 날짜를 생성하면 안 된다.

최초 기준일이 필요하다.

---

## 49. Initial History Date

V1 최초 설정 시:

    history_start_date

를 설정한다.

예:

    {
      "history_start_date": "2026-08-01",
      "last_successful_daily_close": null
    }

최초 Catch-up은 이 날짜부터 시작한다.

---

## 50. 최초 기준일 자동 추측 금지

다음 값을 프로그램이 임의로 추측하지 않는다.

    회사 설립일

    프로젝트 최초 Commit 날짜

    첫 Chat 날짜

`history_start_date`는 Company Ops 실제 운영 시작 기준으로 설정한다.

---

## 51. 현재 과거 History와의 관계

Company Ops 자동화 이전의 회사 History를 자동으로 모두 복원하려 하지 않는다.

기존 중요한 History가 필요하면 별도 Migration 또는 수동 정리를 검토한다.

V1의 목적은:

> 자동화 시작 이후 중요한 History가 안정적으로 누적되도록 만드는 것

이다.

---

## 52. Windows Task Scheduler — Daily

V1에서 생성할 기본 Task:

    DOJOONPASS_COMPANY_OPS_DAILY

Trigger:

    Daily
    11:00 AM

Action:

    Company Ops Runner 실행

---

## 53. Windows Task Scheduler — Startup

추가 Task:

    DOJOONPASS_COMPANY_OPS_STARTUP

Trigger:

    At log on

또는 실제 구현환경에서 안정적인 Windows Startup Trigger.

Action:

    동일 Company Ops Runner 실행

Trigger Type:

    STARTUP

---

## 54. Startup Delay

Windows 로그인 직후 Git, Network 등이 아직 준비되지 않았을 수 있다.

필요하면 짧은 Startup Delay를 사용할 수 있다.

예:

    로그인 후 1~5분

단, 실제 테스트 없이 불필요하게 긴 Delay를 넣지 않는다.

---

## 55. Task 중복 방지

Windows Task Scheduler 자체 설정에서도 가능하면:

> 이미 실행 중이면 새 Instance를 시작하지 않음

정책을 사용한다.

하지만 애플리케이션 내부 Lock도 유지한다.

즉:

    Windows Level Protection

        +

    Application Lock

두 단계로 방어한다.

---

## 56. Task 실행 실패

Windows Task Scheduler가 실행에 실패하면 다음 Startup 또는 Scheduled Run에서 State 기반 Catch-up이 작동해야 한다.

즉 Scheduler 자체가 Single Point of Failure가 되어서는 안 된다.

---

## 57. PC 절전 상태

오전 11시에 PC가 절전 상태일 수 있다.

Windows Task Scheduler 설정에서 실제 환경에 적합한 경우:

    Wake the computer to run this task

옵션을 검토할 수 있다.

그러나 V1의 핵심 안전장치는 Wake 기능이 아니다.

PC가 실행되지 못했더라도 다음 실행에서 Catch-up하는 것이 핵심이다.

---

## 58. 강제 PC Wake 필수 아님

Company Ops 때문에 Desktop 4를 항상 켜두거나 강제로 깨울 필요는 없다.

현재 1인 기업 운영환경에서는:

    OFF 허용
      +
    Catch-up

구조가 더 적합하다.

---

## 59. Monthly Scheduler 연결

향후 Monthly Summary:

    매월 1일 오전 11:00

실행을 추가한다.

하지만 Daily Catch-up이 먼저 완료되어야 한다.

기본 순서:

    Daily Catch-up
          ↓
    전월 Daily 완전성 확인
          ↓
    Monthly Summary

Monthly 세부 규칙은 별도 문서에서 정의한다.

---

## 60. Monthly 생성 전 조건

전월 Monthly Summary를 생성하기 전에:

    전월 Daily History
    모두 처리됐는가?

를 확인해야 한다.

누락된 Daily가 있으면 먼저 Catch-up한다.

---

## 61. Scheduler가 판단하지 않는 것

Scheduler는 다음을 판단하지 않는다.

    History 중요도

    CEO Decision

    Critical Path

    Launch Readiness

    Go / No-Go

    Project Priority

Scheduler는 실행시점과 누락복구만 담당한다.

---

## 62. Health Check

V1에서 별도 Monitoring Dashboard는 만들지 않는다.

대신 최소한 다음 상태를 확인할 수 있어야 한다.

    Last Successful Run

    Last Successful Daily Close

    Pending Retry Count

    Backup Pending

이 정도면 충분하다.

---

## 63. 별도 알림 시스템

V1에서는 Slack, KakaoTalk, Email 등 별도 알림시스템을 필수로 만들지 않는다.

먼저 자동 실행과 복구를 안정화한다.

실제 운영에서 반복적으로 실패를 놓치는 문제가 확인되면 알림 기능을 검토한다.

---

## 64. 실패 은폐 금지

자동화가 실패했을 때:

    성공처럼 State 기록

    오류 파일 삭제

    Event 삭제

    History 생성됐다고 표시

해서는 안 된다.

실패는 실패 상태로 남겨야 한다.

---

## 65. Catch-up 성공 기준

Catch-up 성공:

    누락 날짜 발견
        ↓
    오래된 날짜부터 처리
        ↓
    Local History 생성 확인
        ↓
    날짜별 State Update
        ↓
    마지막 누락 날짜 완료

---

## 66. Mock Test — Scheduled Normal

현재:

    2026-08-06
    11:00

Last Close:

    2026-08-04

기대:

    2026-08-05 생성

    State = 2026-08-05

---

## 67. Mock Test — Startup Before 11

현재:

    2026-08-10
    09:00

Last Close:

    2026-08-07

기대:

    2026-08-08
    2026-08-09

Catch-up.

---

## 68. Mock Test — Startup After 11

현재:

    2026-08-10
    15:00

Last Close:

    2026-08-08

기대:

    2026-08-09

Catch-up.

---

## 69. Mock Test — Multi-Day Offline

Last Close:

    2026-08-05

Current:

    2026-08-10

기대:

    06
    07
    08
    09

순차 처리.

---

## 70. Mock Test — Empty Days

누락 날짜 중 KEEP Candidate가 없는 날짜 존재.

기대:

    GENERATED_EMPTY

처리 후 State 정상 진행.

---

## 71. Mock Test — Mid-Catch-up Failure

처리:

    06 PASS
    07 PASS
    08 FAIL

기대:

    State = 07

다음 실행:

    08부터 시작

---

## 72. Mock Test — PC Shutdown During Run

Daily History 저장 전에 프로그램 강제 종료.

기대:

    해당 날짜 성공 State 없음

다음 실행:

    해당 날짜 재처리

---

## 73. Mock Test — Duplicate Runner

Startup Runner 실행 중 Scheduler 실행.

기대:

    첫 Runner
    계속 실행

    두 번째 Runner
    Lock 감지 후 종료

History 중복 없음.

---

## 74. Mock Test — Stale Lock

강제 종료 후 Lock 파일 잔존.

다음 실행:

    실제 Process 없음 확인

기대:

    Stale Lock 제거

    정상 실행

---

## 75. Mock Test — Notion Offline

Notion:

    FAIL

Local History:

    가능

기대:

    Notion Pending

    Daily History 계속 처리

---

## 76. Mock Test — Internet Offline

Internet:

    OFF

Local Candidate:

    존재

기대:

    Local History 생성

    외부 Sync Pending

---

## 77. Mock Test — Backup Failure

Local History:

    PASS

GitHub:

    FAIL

기대:

    Daily Close 성공

    BACKUP_PENDING

---

## 78. Test Matrix

| Test | 기대 결과 |
|---|---|
| 매일 11시 정상 실행 | D-1 처리 |
| PC OFF 하루 | 다음 실행 Catch-up |
| PC OFF 여러 날 | 전 날짜 순차 Catch-up |
| 오전 11시 전 Startup | 전날까지 Catch-up |
| 오후 Startup | 전날까지 Catch-up |
| Empty Day | 정상 성공 |
| 중간 날짜 실패 | 해당 날짜에서 중단 |
| 실행 중 PC 종료 | 다음 실행 재개 |
| 중복 Runner | 하나만 실행 |
| Stale Lock | 자동 복구 |
| Notion 장애 | History 계속 |
| Internet 장애 | Local 처리 계속 |
| Backup 장애 | BACKUP_PENDING |
| 당일 History | 생성하지 않음 |
| 최초 실행 | history_start_date 기준 |

---

## 79. Phase 6 완료 기준

다음이 실제로 검증되면 Phase 6을 PASS할 수 있다.

1. 매일 오전 11시 자동 실행된다.
2. Startup Catch-up이 작동한다.
3. PC OFF 하루 누락을 복구한다.
4. PC OFF 여러 날 누락을 복구한다.
5. 오래된 날짜부터 처리한다.
6. 당일 History를 마감하지 않는다.
7. Empty Day를 정상 처리한다.
8. 날짜별 State가 안전하게 갱신된다.
9. 중간 실패 시 성공 날짜까지만 State가 진행된다.
10. Process Lock이 작동한다.
11. Stale Lock을 복구한다.
12. 프로그램 중단 후 재실행이 가능하다.
13. Notion 장애가 Local History를 막지 않는다.
14. Backup 장애가 Local History를 삭제하지 않는다.
15. 최초 실행 기준일을 명확히 관리한다.
16. 자동 실행과 수동 실행이 동일 Runner를 사용한다.

---

## 80. 구현 위치

예상 코드:

    D:\DOJOONPASS_COMPANY_OPS\
    scheduler\

또는 프로젝트가 작다면:

    runner\

구체적인 Directory를 불필요하게 늘리지 않는다.

State:

    runtime\
    state\

Lock:

    runtime\
    locks\

Logs:

    runtime\
    logs\

---

## 81. V1에서 만들지 않는 것

Scheduler V1에서는 다음을 만들지 않는다.

- 별도 Scheduler Server
- Cloud Cron
- Kubernetes
- Airflow
- Prefect
- Celery
- Redis
- 실시간 Monitoring Dashboard
- 복잡한 Alert System
- SMS Alert
- Kakao Alert
- Slack Alert
- 자동 장애 복구 AI Agent
- 원격 Desktop 제어
- 24시간 PC 강제 실행

---

## 82. 운영 철학

현재 Company Ops는 회사 본업을 지원하는 내부 도구다.

따라서 목표는:

    24시간 완벽한 시스템

이 아니라:

    PC가 켜졌을 때
    빠진 업무를 알아서 복구하는 시스템

이다.

이 차이를 유지한다.

---

## 83. Stop Rule

다음 구조가 안정적으로 작동하면 Scheduler 개발을 종료한다.

    오전 11시
        ↓
    자동 실행

        +

    PC OFF
        ↓
    다음 실행
        ↓
    Catch-up

        +

    중간 실패
        ↓
    State 기반 재개

        +

    중복 실행
        ↓
    Lock 방지

이후 기능을 추가하지 않고 Backup Phase로 넘어간다.

---

## 84. 완료 보고 형식

Phase 6 완료 시:

    [Phase]
    Phase 6 — Catch-up + Scheduler

    [상태]
    PASS / BLOCKED / FAIL

    [구현 내용]

    [변경 파일]

    [11:00 Scheduled Run]
    PASS / FAIL

    [Startup Catch-up]
    PASS / FAIL

    [1-Day Offline Recovery]
    PASS / FAIL

    [Multi-Day Offline Recovery]
    PASS / FAIL

    [Empty Day]
    PASS / FAIL

    [Mid-run Failure Recovery]
    PASS / FAIL

    [Process Lock]
    PASS / FAIL

    [Stale Lock Recovery]
    PASS / FAIL

    [Notion Failure Isolation]
    PASS / FAIL

    [Internet Offline Test]
    PASS / FAIL

    [State Integrity]
    PASS / FAIL

    [Evidence]

    [발견된 문제]

    [다음 작업]

    [CEO/COO Decision Required]
    NONE 또는 해당 사항

---

## 85. 다음 명세

다음 단계:

    Phase 7
    Backup

다음 문서:

    D:\DOJOONPASS_COMPANY_OPS\
    docs\
    08_BACKUP_SPEC.md

다음 문서에서는 다음을 정의한다.

    Local Master를 어떻게 Backup할 것인가?

    GitHub Private Repository를 어떻게 사용할 것인가?

    Local → Remote 단방향 원칙

    git pull로 Local Master를 덮어쓰는 위험 방지

    자동 Commit / Push

    Push 실패 시 재시도

    Conflict 방지

    Backup 상태 검증

    Repository 손상 시 Local 보호

    Local 디스크 손실 시 복구

핵심 원칙은:

> Local은 원본이고 GitHub는 복구용 Backup이다.

이다.

---

# END OF DOCUMENT