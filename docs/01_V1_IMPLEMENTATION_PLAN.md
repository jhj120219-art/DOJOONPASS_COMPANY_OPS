# IMPLEMENTATION OVERRIDE — V1 EXECUTION PRIORITY

> 본 섹션은 Company Ops V1 실제 구현 순서의 최우선 기준이다.
> 하위 내용과 구현 순서가 충돌할 경우 본 기준을 우선한다.

## V1 구현 우선순위

### P0 — Core

1. Project Initialization
2. Event Schema
3. Reporter / Event Generation
4. Event Transport
5. Collector
6. Event Persistence / Deduplication
7. History Filter
8. Daily History
9. Local Master
10. Scheduler / Catch-up
11. Backup / Recovery
12. Real Environment E2E

### P1 — Supporting

13. Notion Sync
14. Monthly History

---

## First E2E Gate

전체 기능을 한 번에 연결하지 않는다.

첫 번째 실제 E2E 목표는 다음으로 제한한다.

    Desktop 3
        ↓
    Reporter / Event
        ↓
    Event Transport
        ↓
    Desktop 4
        ↓
    Collector
        ↓
    History Filter
        ↓
    Daily History
        ↓
    Local Master

위 경로가 PASS하기 전에는 Desktop 1·2 Reporter Rollout을 진행하지 않는다.

---

## Second E2E Gate

First E2E PASS 후:

    First E2E
        +
    Backup

을 검증한다.

---

## Third E2E Gate

Backup PASS 후:

    Core Pipeline
        +
    Notion Current State

를 검증한다.

Notion 장애는 Local History Pipeline을 차단해서는 안 된다.

---

## Reporter Rollout

    Desktop 3
        ↓
    Desktop 1
        ↓
    Desktop 2

순으로 진행한다.

각 Desktop은 이전 단계가 실제 E2E PASS한 뒤 연결한다.

---

## Event Transport Requirement

Event Transport 방식은 구현 초기에 확정한다.

필수 조건:

- Source Desktop OFF/ON과 관계없이 Event 보존
- Desktop 4 OFF 상태에서도 Event 손실 금지
- Network Failure 시 Local Queue 유지
- Retry 가능
- Event ID 기반 Deduplication
- 별도 복잡한 Server Infrastructure 금지

Transport 기술은 위 조건을 만족하는 가장 단순한 방식을 선택한다.

---

## Development Stop Rule

다음 Core가 실제 환경에서 PASS하면 V1 Core 개발을 종료한다.

    Reporter
    Event Transport
    Collector
    History Filter
    Daily
    Local Master
    Scheduler
    Catch-up
    Backup
    Recovery

그 이후 실제 운영에서 필요성이 확인되지 않은 신규 기능은 V1에 추가하지 않는다.

# END OF IMPLEMENTATION OVERRIDE