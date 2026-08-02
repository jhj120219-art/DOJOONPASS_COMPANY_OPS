# D:\DOJOONPASS_COMPANY_OPS\README.md

# DOJOONPASS COMPANY OPS

## 1. 프로젝트 목적

DOJOONPASS COMPANY OPS는 도준패스의 내부 운영 및 Company Intelligence 자동화 시스템이다.

목적은 CTO·CMO 및 회사 전체에서 발생하는 중요한 실행 정보와
의사결정의 맥락(Decision Context)을
자동으로 수집하여:

* 현재 프로젝트 상태
* Milestone
* Blocker
* Dependency
* 중요 Decision
* Product Evolution
* 주요 Risk
* Customer Learning
* Company History
* Decision Context (왜 시작했고, 왜 변경했고, 왜 중단했고, 왜 다시 시작했는지)

를 COO가 지속적으로 파악할 수 있도록 하는 것이다.

이 시스템은 회사의 전략을 결정하거나 CEO의 의사결정을 대신하는 시스템이 아니다.

---

# 2. 운영 책임

Owner:

```
COO
```

COO 책임:

```
Execution Integration
Launch Readiness
Dependency / Critical Path
QA
KPI / VOC
Company Intelligence
```

CEO 권한:

```
Strategy
Beta Scope
Target
Pricing
Major Product Priority
Launch
Final Go / No-Go
```

Company Ops는 CEO Decision을 자동으로 생성하거나 확정하지 않는다.

---

# 3. Desktop 구조

```
Desktop 1
CTO Backend / Crawling / Search

Desktop 2
CMO / Content OS

Desktop 3
CTO Frontend

Desktop 4
COO / Company Ops
```

Desktop 4가 Company Ops의 중앙 실행 환경이다.

---

# 4. 전체 시스템 구조

```
Desktop 1 ─ Reporter ─┐
                      │
Desktop 2 ─ Reporter ─┼── Event Transport
                      │
Desktop 3 ─ Reporter ─┘
                            │
                            ▼
                       Desktop 4
                            │
                            ▼
                        Collector
                       /         \
                      /           \
                     ▼             ▼
                Notion         History Filter
             Current State          │
                                    ▼
                                  Daily
                                    │
                                    ▼
                              Local Master
                                    │
                                    ▼
                            GitHub Private
                             Backup Copy
                                    │
                                    ▼
                                 Monthly
```

---

# 5. Source of Truth

| 정보             | Source of Truth           |
| -------------- | ------------------------- |
| 현재 실행 상태       | Notion                    |
| 공식 개발 명세       | Project `/docs`           |
| 중요 회사 History  | Desktop 4 Local Master    |
| History Backup | GitHub Private Repository |
| Raw Event      | Event Storage             |
| Runtime State  | Desktop 4 Runtime         |

---

# 6. 핵심 원칙

## RULE 1

Notion은 Current State 관리용이다.

Notion은 Company History의 공식 원본이 아니다.

## RULE 2

Desktop 4 Local Master가 Company History의 Primary Source다.

## RULE 3

GitHub Private Repository는 Local Master의 Off-device Versioned Backup Copy다.

GitHub가 Local Master를 자동으로 덮어쓰지 않는다.

## RULE 4

Reporter는 모든 PC 행동을 기록하지 않는다.

회사 상태를 변경할 의미 있는 Execution Event만 생성한다.

## RULE 5

Notion은 History Critical Path가 아니다.

핵심 흐름은:

```
Reporter
    ↓
Event
    ↓
Collector
    ↓
History Filter
    ↓
Daily
    ↓
Local Master
```

이다.

Notion 장애가 발생해도 Local History 생성은 계속 가능해야 한다.

## RULE 6

AI는 Single Point of Failure가 아니다.

AI 기능 실패 시 Rule-based 방식으로 핵심 History 처리를 계속한다.

## RULE 7

PC가 꺼져 있어도 Event와 History가 영구 손실되어서는 안 된다.

Catch-up을 지원한다.

## RULE 8

자동화는 CEO Decision을 대신하지 않는다.

## RULE 9

Data Safety가 Convenience보다 우선한다.

## RULE 10

V1 완료 후 실제 운영 Evidence 없이 V2 개발을 시작하지 않는다.

## RULE 11

Company Ops는 단순히 "무엇을 했는가"를 기록하는 시스템이 아니다.

회사의 중요한 의사결정이

- 왜 시작되었는가
- 왜 그렇게 결정되었는가
- 왜 변경되었는가
- 왜 중단되었는가
- 왜 다시 시작되었는가

를 장기적으로 복원할 수 있도록 Decision Context를 함께 보존한다.

단순 작업 기록보다 Decision Context를 더 중요한 자산으로 간주한다.

## RULE 12

History는 과거를 저장하기 위한 기록이 아니라

미래의 더 나은 의사결정을 위한 회사의 학습 데이터이다.

History는 가능하면 다음 질문에 답할 수 있어야 한다.

- 무엇을 했는가
- 왜 시작했는가
- 왜 그렇게 결정했는가
- 기대했던 결과는 무엇인가
- 실제 결과는 무엇인가
- 무엇을 배웠는가
- 다음에는 무엇을 할 것인가

AI는 단순 검색이 아니라

Decision Context와 결과를 함께 분석하여

같은 실수를 반복하지 않도록 지원한다.
---

# 7. Event Transport

Desktop 1·2·3에서 생성된 Event는 Desktop 4 Collector까지 전달되어야 한다.

구조:

```
Reporter
    ↓
Local Event
    ↓
Event Transport
    ↓
Desktop 4 Incoming
    ↓
Collector
```

Event Transport의 실제 구현 방식은 구현 단계에서 가장 단순하고 안전한 방법 하나를 선택한다.

선택 기준:

1. Desktop 1·2·3이 동시에 켜져 있을 필요가 없어야 한다.
2. Desktop 4가 꺼져 있어도 Event가 손실되지 않아야 한다.
3. Network 장애 시 Event가 Local에 남아야 한다.
4. 재전송이 가능해야 한다.
5. Duplicate Event는 Event ID로 제거할 수 있어야 한다.
6. 별도의 복잡한 Server Infrastructure를 만들지 않는다.

V1에서 Event Transport를 위한 별도 대규모 시스템을 구축하지 않는다.

실제 Transport 방식은 Phase 1 구현 시 환경 검증 후 확정한다.

---

# 8. V1 Critical Path

```
Project Initialization
    ↓
Event Schema
    ↓
Reporter / Event Generation
    ↓
Event Transport
    ↓
Collector
    ↓
History Filter
    ↓
Daily History
    ↓
Scheduler / Catch-up
    ↓
Local Master
    ↓
Backup
    ↓
Real E2E Validation
```

---

# 9. V1 Priority

## P0 — Core

```
Project Initialization
Event Schema
Reporter
Event Transport
Collector
Event Persistence
Duplicate Protection
History Filter
Daily History
Local Master
Scheduler
Catch-up
Backup
Recovery
Real E2E
```

## P1 — Supporting

```
Notion Sync
Monthly History
```

## V1 제외

```
Dashboard
Mobile App
Advanced BI
Quarterly Automation
KPI Platform
Alert Platform
AI Executive Agent
RAG Company Intelligence Search
```

---

# 10. 구현 원칙

처음부터 전체 시스템을 한 번에 연결하지 않는다.

첫 번째 목표:

```
Desktop 3
    ↓
Event
    ↓
Desktop 4
    ↓
Collector
    ↓
Daily
    ↓
Local Master
```

이 흐름을 먼저 PASS시킨다.

그 다음:

```
+ Backup
```

그 다음:

```
+ Notion
```

그 다음:

```
Desktop 1 Reporter
```

그 다음:

```
Desktop 2 Reporter
```

순서로 확장한다.

---

# 11. Phase 체계

Company Ops 개발 Phase의 Master는:

```
01_V1_IMPLEMENTATION_PLAN.md
```

이다.

Phase 0~11은 Development Execution Phase를 의미한다.

Deployment 문서의:

```
D0~D11
```

은 실제 Desktop 설치 및 운영 전환 Step을 의미한다.

둘을 혼용하지 않는다.

---

# 12. 문서 구조

```
docs\
├─ 00_COMPANY_OPS_V1_SPEC.md
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
```

---

# 13. 문서 우선순위

문서 간 내용이 충돌할 경우:

```
README
    ↓
00_COMPANY_OPS_V1_SPEC
    ↓
01_V1_IMPLEMENTATION_PLAN
    ↓
개별 Component Spec
```

순서로 확인한다.

단, 실제 구현 과정에서 발견된 기술적 제약은 기록 후 최소 범위에서 명세를 수정할 수 있다.

---

# 14. V1 Stop Rule

다음이 실제 환경에서 안정적으로 작동하면 V1 개발을 종료한다.

```
Event 자동 생성
    ↓
Event 안전 보존
    ↓
Desktop 4 수집
    ↓
중요 History 선별
    ↓
Daily 자동 생성
    ↓
Local Master 저장
    ↓
Catch-up
    ↓
Backup
    ↓
장애 후 Recovery
```

Notion과 Monthly는 운영 편의 및 Company Intelligence를 강화하는 Supporting Layer다.

---

# 15. 최종 목표

Company Ops를 만드는 목적은 Company Ops 자체를 거대한 Product로 만드는 것이 아니다.

목적은:

> 여러 Desktop에서 진행되는 도준패스의 개발·마케팅·운영 업무에서 중요한 변화가 자동으로 수집되고, COO가 회사의 현재 실행 상태를 파악하며, 중요한 회사 History가 장기적으로 사라지지 않도록 만드는 것이다.


Company Ops가 기억해야 하는 것은

무엇을 했는가가 아니라

- 왜 시작했는가
- 왜 그렇게 결정했는가
- 왜 변경했는가
- 왜 중단했는가
- 왜 다시 시작했는가
- 실제 결과는 무엇이었는가
- 무엇을 배웠는가
- 다음에는 무엇을 할 것인가

이다.

회사의 가장 중요한 자산은

코드가 아니라

시간이 지나도 복원 가능한

Decision Context이다.

Company Ops는 업무를 기록하는 시스템이 아니라

회사의 의사결정을 기억하는 Company Memory System이다.

이 목적이 달성되면 Company Ops 개발을 멈추고 도준패스 Product의 Beta, Customer Validation, Launch에 집중한다.

---

# END OF DOCUMENT
