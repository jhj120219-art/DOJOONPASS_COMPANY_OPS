# D:\DOJOONPASS_COMPANY_OPS\docs\12_APPLICATION_FLOW_SPEC.md

## DOJOONPASS Company Ops — Application Flow Specification

---

## 1. 문서 정의

| 항목 | 내용 |
|---|---|
| 문서명 | Application Flow Specification |
| 프로젝트 | DOJOONPASS Company Ops |
| Owner | COO |
| 상위 문서 | `00_V1_DEVELOPMENT_SPEC.md` |
| 실행 기준 | `01_V1_IMPLEMENTATION_PLAN.md` |
| 참조 문서 | `02_EVENT_SCHEMA.md`, `03_COLLECTOR_SPEC.md`, `04_NOTION_SYNC_SPEC.md`, `05_HISTORY_PIPELINE_SPEC.md`, `06_DAILY_HISTORY_SPEC.md`, `07_SCHEDULER_CATCHUP_SPEC.md`, `08_BACKUP_SPEC.md`, `09_MONTHLY_HISTORY_SPEC.md` |
| 목적 | 개별 Component Spec에 흩어져 있는 전체 System Flow, Runtime Sequence, Failure/Retry/Recovery 규칙을 하나의 그림으로 통합 |
| 적용 버전 | V1 |

본 문서는 새로운 기능이나 새로운 Component를 정의하지 않는다.

이미 각 Component Spec에 정의된 규칙을 하나의 End-to-End 흐름으로 재구성한 것이다.

내용이 충돌하면 개별 Component Spec이 우선하고, 본 문서는 수정 대상이 된다.

---

## 2. 목적과 범위

지금까지 Event Schema, Collector, History Pipeline, Daily/Monthly History, Scheduler, Backup은 각각 별도 문서로 정의되어 있었다.

각 문서는 자신의 책임 범위는 명확히 정의하지만, "전체가 실제로 어떻게 이어지는가"는 문서를 넘나들며 조합해야 알 수 있었다.

본 문서의 목적:

1. 전체 System Flow를 하나의 다이어그램으로 표현한다.
2. 각 Component의 경계를 다시 한 번 명시적으로 확인한다.
3. 실패가 발생했을 때 어디서 멈추고, 어디서 재시도하는지 통합 규칙으로 정리한다.
4. Queue/Event/Daily Close의 상태 전이를 하나의 표로 정리한다.
5. Runtime 구현 시 "이것만은 절대 하면 안 되는 것"을 한 곳에 모은다.

본 문서는 설계 문서다. Runtime 구현, Transport 기술 선택, Collector Runtime 구현은 포함하지 않는다.

---

## 3. 현재 구현 상태

본 문서 작성 시점 기준:

| Component | 상태 |
|---|---|
| Event Schema (`src/events`) | 구현 완료 (Phase 1) |
| Reporter (`src/reporter`) | 구현 완료 (Phase 2) |
| Transport Interface (`src/transport`) | Interface만 구현 완료 (Phase 2.5). GitHub/USB/SharedFolder 등 실제 구현 없음 |
| Collector Interface (`src/collector`) | Interface + Validation/Duplicate 판정 로직 구현 완료 (Phase 3). Runtime(파일 스캔, 상태 영속화, 로깅)은 없음 |
| Notion Sync | 미구현 (Spec만 존재, `04_NOTION_SYNC_SPEC.md`) |
| History Pipeline | 미구현 (Spec만 존재, `05_HISTORY_PIPELINE_SPEC.md`) |
| Daily History | 미구현 (Spec만 존재, `06_DAILY_HISTORY_SPEC.md`) |
| Monthly History | 미구현 (Spec만 존재, `09_MONTHLY_HISTORY_SPEC.md`) |
| Scheduler / Catch-up | 미구현 (Spec만 존재, `07_SCHEDULER_CATCHUP_SPEC.md`) |
| Backup | 미구현 (Spec만 존재, `08_BACKUP_SPEC.md`) |

본 문서의 흐름도는 위 표의 "미구현" 부분을 포함한 **목표 아키텍처 전체**를 다룬다.

---

## 4. 전체 시스템 흐름

```text
Reporter                       (구현 완료)
   │  Event 생성 + Schema Validation
   ▼
Transport                      (Interface만 구현 완료 — 구체 기술 미정)
   │  Desktop 간 전달, 재시도, 로컬 큐 보존
   ▼
Incoming Queue                 (runtime/events/incoming/)
   │  아직 Collector가 처리하지 않은 Event
   ▼
Collector                      (Interface + 판정 로직 구현 완료 — Runtime 미구현)
   │  JSON Parsing → Schema Validation → Business Validation → Duplicate Check
   ▼
Accepted Event
   │
   ├──────────────────────────┐
   ▼                          ▼
Notion Sync                History Filter        (둘 다 미구현)
(Current State 반영)          │  KEEP / DROP / REVIEW
                              ▼
                         History Candidate
                              │
                              ▼
                          Daily Close            (미구현, 매일 11:00)
                              │  Event Timestamp 기준 날짜로 귀속
                              ▼
                    Desktop 4 Local Master        (D:\DOJOONPASS_COO\history\daily\)
                              │
                              ▼
                        Backup Queue
                              │
                              ▼
                          Backup (GitHub)          (미구현)
                              │
                              ▼
                      Monthly History              (미구현, 매월 1일 11:00,
                                                     확정된 Daily만 입력으로 사용)
```

Notion Sync와 History Filter는 서로 독립적인 두 갈래다. 한쪽이 실패해도 다른 쪽은 영향받지 않는다 (`03_COLLECTOR_SPEC.md` §28-29).

---

## 5. Component Boundary

### 5.1 Reporter 책임

- 명시적인 업무 Signal을 받아 `create_event()`로 Event를 생성한다.
- Event Schema Validation을 통과하지 못하면 Event를 만들지 않는다.
- 자동 감시, 추론, Critical Path/Launch/Decision 판단을 하지 않는다.
- Transport, Collector, Notion, History를 직접 호출하지 않는다.

### 5.2 Transport 책임

- 이미 생성된 Event를 Source Desktop에서 Desktop 4까지 안전하게 옮긴다.
- Desktop 4 OFF, Source PC OFF, Network 단절 상황에서도 Event를 유실하지 않는다.
- 재시도 가능해야 하며, `event_id` 기반 중복 전송을 허용한다 (중복 판단은 Collector가 한다).
- Event의 내용을 검증하거나 수정하지 않는다.
- Collector의 존재나 처리 로직을 알지 못한다.

### 5.3 Collector 책임

- `runtime/events/incoming/`에서 발견된 Event 하나를 읽어 Schema/Business Validation과 Duplicate Check를 수행한다.
- 결과를 ACCEPTED / DUPLICATE / REJECTED 중 하나로 판정한다.
- Notion이나 History Pipeline을 직접 호출하지 않는다. Transport, Reporter의 존재를 알지 못한다.
- Event 원본의 사실을 임의로 수정하지 않는다.

### 5.4 History 책임 (History Filter → Daily → Monthly)

- History Filter: `history_candidate = true`인 ACCEPTED Event 중 장기 보존 가치를 판단해 KEEP/DROP/REVIEW를 결정한다.
- Daily Close: KEEP Candidate를 Event Timestamp 기준 날짜에 귀속시켜 `YYYY-MM-DD.md`를 생성한다.
- Monthly: 확정된 Daily History만 입력으로 사용한다. Raw Event를 직접 읽지 않는다 (`09_MONTHLY_HISTORY_SPEC.md` §13).
- History 단계는 Collector나 Transport를 호출하지 않는다. Local Master에만 쓴다.

### 5.5 Backup 책임

- Desktop 4 Local Master(`D:\DOJOONPASS_COO\history\`)의 변경분을 GitHub Private Repository로 복사한다.
- Local → Remote 단방향. Remote가 Local을 덮어쓰지 않는다.
- Daily/Monthly History 생성 성공 여부와 독립적으로 재시도 가능해야 한다.
- Backup 실패가 Local History의 성공 여부를 바꾸지 않는다.

---

## 6. Runtime Sequence — 정상 흐름 (Event 생성부터 History 저장까지)

```text
 1. Desktop에서 의미 있는 업무 상태 변화 발생
 2. Reporter.report()  → Event 생성, Schema Validation PASS
 3. (선택) Reporter.send(event) → Transport.send(event)
 4. Transport가 Event를 Local Queue에 보존 + Desktop 4로 전달 시도
 5. Desktop 4 runtime/events/incoming/<event_id>.json 도착
 6. (Scheduler 또는 수동 실행) Collector Runtime이 incoming을 스캔
 7. Collector.collect(raw) 호출
      → JSON Parsing
      → Schema Validation
      → Business Validation
      → Duplicate Check (SeenEventStore)
 8. 결과 분기
      REJECTED  → runtime/events/rejected/ 로 보존, 이후 단계 중단
      DUPLICATE → runtime/events/processed/ 참조, 이후 단계 반복 실행 안 함
      ACCEPTED  → runtime/events/processed/ 로 이동, 다음 단계 진행
 9. ACCEPTED Event 후속 처리 (서로 독립적)
      9a. Notion Sync    → Current State 갱신
      9b. History Filter → KEEP / DROP / REVIEW 판정
10. KEEP Candidate는 runtime/history_candidates/ 에 보관
11. (Scheduler, 매일 11:00) Daily Close 실행
      → 전날(D-1) 대상 KEEP Candidate 조회
      → YYYY-MM-DD.md 생성
      → Desktop 4 Local Master에 저장
      → daily_history_state.json 갱신 (last_successful_daily_close)
12. Backup Queue 등록
13. Backup Runner 실행 → GitHub Push 시도
14. (매월 1일 11:00) Monthly Close → 확정된 Daily 묶어서 Monthly 생성
```

이 순서는 이상적인 단일 Event 기준이며, 실제로는 여러 Event가 동시에 `incoming/`에 쌓여 있을 수 있다. Collector는 파일 단위로 순차 처리한다 (`03_COLLECTOR_SPEC.md` §24: 파일 도착 순서를 사실 순서로 간주하지 않고 `timestamp` 기준으로 판단).

---

## 7. Runtime Sequence — Scheduler Runner와의 관계

`07_SCHEDULER_CATCHUP_SPEC.md` §9 기준, 정기 실행 시 Runner 내부 순서는 다음과 같다.

```text
Windows Task Scheduler
      ↓
Company Ops Runner
      ↓
Process Lock 확인
      ↓
Collector 실행        ← incoming 전체 소진
      ↓
Pending Event 처리
      ↓
Catch-up 확인          ← 누락된 Daily 날짜 계산
      ↓
Daily Close            ← Collector가 먼저 끝나야 전날 Event 누락을 막을 수 있음
      ↓
Backup Pending 확인
      ↓
State 저장
      ↓
종료
```

Collector가 Daily Close보다 먼저 실행되어야 하는 이유: Daily Close가 먼저 실행되면 아직 Collector가 처리하지 않은 전날 Event가 History에서 누락될 수 있다 (`07_SCHEDULER_CATCHUP_SPEC.md` §10).

Scheduler는 "언제 실행할지"만 결정한다. Collector/History/Backup의 판단 로직을 Scheduler가 대신하지 않는다 (`03_COLLECTOR_SPEC.md` §40).

---

## 8. Queue Lifecycle

### 8.1 파일 단위 (디렉터리 이동)

```text
runtime/events/incoming/<event_id>.json
        │
        │  Collector.collect()
        ▼
   ┌────┴────┐
   ▼         ▼
REJECTED   ACCEPTED / DUPLICATE
   │             │
   ▼             ▼
runtime/events/  runtime/events/
  rejected/        processed/
   │                    │
   │ (보존, 자동삭제 없음)  │ (Notion / History Filter가 여기서 읽음)
   ▼                    ▼
  종료               History Filter → KEEP → history_candidates/ → Daily Close
```

- `incoming` → `rejected` 또는 `processed`로의 이동은 **처리가 완전히 끝난 뒤**에만 일어난다 (`03_COLLECTOR_SPEC.md` §31 Atomic Processing 원칙). 처리 중 프로그램이 죽으면 원본은 `incoming`에 그대로 남아 다음 실행에서 재처리된다.
- `rejected`는 자동 삭제하지 않는다.
- `processed`는 Company History 원본이 아니다 (`03_COLLECTOR_SPEC.md` §10). Company History 원본은 Desktop 4 Local Master(`D:\DOJOONPASS_COO\history\`)이며 완전히 별도 위치다.
- **archive**: 사용자가 요청한 Lifecycle 용어 중 `archive`는 현재 Runtime 구조(`incoming/processed/rejected`)에 대응하는 디렉터리가 없다. `processed`가 무한정 누적되는 문제는 V1 규모에서는 우선순위가 낮다 (`05_HISTORY_PIPELINE_SPEC.md` §49 "복잡한 Retention Policy는 만들지 않는다"와 동일한 원칙 적용). 장기적으로 필요해지면 "Daily Close에 반영되고 Backup까지 성공한 processed Event"를 별도 archive 영역으로 옮기는 정책을 검토할 수 있으나, 이는 V1 범위가 아니다. → [OPEN QUESTIONS] 참조.

### 8.2 History Candidate 단위

`05_HISTORY_PIPELINE_SPEC.md` §48이 제시하는 구조:

```text
runtime/history_candidates/
├─ pending/
├─ keep/
├─ review/
└─ drop/
```

KEEP만 Daily Close의 입력이 된다. DROP은 영구 보존 의무가 없다. REVIEW는 예외적으로만 발생해야 하며, REVIEW가 과다하면 자동화 실패 신호로 간주한다 (`05_HISTORY_PIPELINE_SPEC.md` §50).

---

## 9. Collector Processing State

`03_COLLECTOR_SPEC.md` §12-20 기준, Collector 계층의 상태는 두 층으로 나뉜다.

**1층 — Collector Interface가 이미 반환하는 상태** (Phase 3에서 구현 완료):

```text
RECEIVED → (Validation) → ACCEPTED | REJECTED
                              │
                    (Duplicate Check)
                              │
                       ACCEPTED | DUPLICATE
```

**2층 — Collector 이후 후속 처리 상태** (아직 미구현, Collector Runtime의 책임):

```text
ACCEPTED
   ↓
PROCESSING        ← Notion Sync, History Filter 진행 중
   ↓
PROCESSED         ← 둘 다 성공
   또는
PARTIAL_FAILURE   ← Event 자체는 정상, 일부 후속 처리만 실패 (Event를 REJECTED로 바꾸지 않음)
```

내부 문제(예: SeenEventStore 접근 실패)로 판정 자체를 완료하지 못하면 `FAILED`다. 이때 원본 Event는 `incoming`에 남아 재시도 가능한 상태를 유지한다.

---

## 10. Event 상태 변화 (Work Narrative)

이것은 Collector 처리 상태와 다른 개념이다. `event_type`은 하나의 업무/프로젝트가 시간이 지나며 어떤 사건들을 거쳤는지 나타내는 서술이며, 코드가 강제하는 단일 State Machine이 아니다 — 각 Event는 독립적인 불변 레코드다 (`02_EVENT_SCHEMA.md` §34 Event 수정 금지 원칙).

일반적인 진행 예:

```text
STARTED
   ↓
BLOCKED  ──────────────→ RESUMED
   │                         │
   │ (중요 Blocker라면)        │
   ▼                         ▼
ISSUE_RESOLVED           MILESTONE_COMPLETED
                              │
                              ▼
                          COMPLETED
```

- `CANCELLED`는 위 흐름 어느 시점에서든 발생할 수 있다.
- `DECISION_APPROVED`는 이 흐름과 독립적으로, CEO의 실제 확정이 있을 때만 발생한다.
- Notion은 `project_id` 기준으로 최신 Event가 기존 Row를 갱신하는 방식이며, Event 개수만큼 Row가 늘어나지 않는다 (`02_EVENT_SCHEMA.md` §35).
- 오래된 `timestamp`를 가진 Event가 나중에 도착해도 Notion의 현재 상태를 과거로 역전시키지 않는다 (`03_COLLECTOR_SPEC.md` §24).

---

## 11. Failure Flow

### 11.1 Transport 실패

```text
Event 생성 성공
      ↓
Transport 전송 시도
      ↓
   실패 (Network OFF / Desktop 4 OFF / 인증 실패 등)
      ↓
Source Local Queue에 Event 보존
      ↓
Rollback 범위: 없음 (Event 자체는 이미 완성된 상태이므로 되돌릴 것이 없음)
      ↓
Retry 위치: Source Desktop의 다음 Transport 실행 시점
```

Transport가 실패했다고 해서 Reporter가 만든 Event를 지우거나 재생성하지 않는다. Event의 진실은 Source Local Queue에만 있다.

### 11.2 Collector 실패

```text
Event가 incoming에 도착
      ↓
Collector.collect() 호출
      ↓
   ┌─────────────┬──────────────┐
   ▼             ▼              ▼
Validation     내부 오류      (성공)
 실패            (SeenEventStore 등)
   ↓             ↓              ↓
REJECTED      CollectorError   ACCEPTED/DUPLICATE
   ↓             ↓              ↓
rejected/     incoming에 유지   processed/ 로 이동
보존, 재시도    (재시도 대상)
안 함
```

- Rollback 범위: REJECTED는 애초에 아무 후속 시스템도 건드리지 않았으므로 Rollback할 것이 없다.
- CollectorError(내부 오류)는 원본을 `incoming`에서 제거하지 않는다 — 자동으로 다음 실행에서 재시도된다.
- REJECTED는 재시도하지 않는다 (`03_COLLECTOR_SPEC.md` §32 — 잘못된 Schema/Type/Status는 재시도 대상이 아니다).

### 11.3 History 실패

`06_DAILY_HISTORY_SPEC.md` §41-42 기준:

```text
KEEP Candidate 조회 PASS
      ↓
Markdown 생성 실패
      ↓
Rollback 범위: 없음 — 기존 History 삭제 금지, Candidate 삭제 금지
Retry 위치: 다음 Daily Close 실행
State: last_successful_daily_close 갱신 안 함
```

```text
Markdown 생성 PASS
      ↓
Local 저장(파일 쓰기) 실패
      ↓
Daily Close 전체 = FAIL
Rollback 범위: 없음 (아직 아무것도 확정 저장되지 않음)
Retry 위치: 다음 Daily Close 실행
```

Notion 실패는 History Pipeline을 막지 않는다. History Pipeline 실패는 이미 성공한 Notion 반영을 되돌리지 않는다 (`03_COLLECTOR_SPEC.md` §28-29). 두 갈래는 서로의 실패를 Rollback하지 않는다.

### 11.4 Backup 실패

`08_BACKUP_SPEC.md` §16, §60-63 기준:

```text
Local Master 저장 PASS  (Daily/Monthly History는 이미 SUCCESS로 확정됨)
      ↓
GitHub Push 실패
      ↓
Rollback 범위: 없음 — Local Master는 이미 확정된 공식 원본이므로 되돌리지 않음
Backup 상태 = BACKUP_PENDING (일시적 실패: Network/GitHub 장애)
           또는 BACKUP_FAILED (인증 실패 — 무한 Retry 금지)
Retry 위치: 다음 Backup Runner 실행
```

Backup 실패는 Daily/Monthly History의 성공 여부에 영향을 주지 않는다. 공식 원본은 Local이기 때문이다 (`08_BACKUP_SPEC.md` §16).

---

## 12. Retry Policy

| 실패 종류 | Retry 여부 | 위치 |
|---|---|---|
| Transport 네트워크/Desktop OFF | O | Source의 다음 Transport 실행 |
| Collector — Schema/Business Validation 실패 (REJECTED) | X | 재시도하지 않음, 사람이 원인 확인 |
| Collector — 지원하지 않는 Event Type/Status 조합 | X | 재시도하지 않음 |
| Collector — SeenEventStore 등 내부 의존성 실패 | O | 다음 Collector 실행 (원본이 `incoming`에 남아 있음) |
| History — Markdown 생성 실패 | O | 다음 Daily Close 실행 |
| History — Local 파일 저장 실패 | O | 다음 Daily Close 실행 |
| Notion API 일시 실패 | O | 다음 Runner 실행 (`03_COLLECTOR_SPEC.md` §28) |
| Backup — Network/GitHub 일시 장애 | O | 다음 Backup Runner 실행 |
| Backup — 인증 실패 | X (무한 Retry 금지) | 사람이 Token/인증 갱신 후 수동 재개 |
| Duplicate Event (`event_id` 재도착) | 해당 없음 | 재시도 개념이 아니라 즉시 DUPLICATE 처리, 후속 처리 반복 안 함 |

원칙 (`03_COLLECTOR_SPEC.md` §32-33, §53 요약):

> 명확히 잘못된 데이터(Schema/Business 위반)는 재시도해도 결과가 달라지지 않으므로 재시도하지 않는다.
> 일시적 인프라 문제(Network, 파일 잠금, 외부 서비스 장애)는 재시도하면 성공할 수 있으므로 재시도한다.
> V1에서는 정교한 Exponential Backoff, 무제한 Retry Loop를 만들지 않는다 — "다음 정기 실행에서 다시 시도"가 기본 Retry 단위다.

---

## 13. Recovery

### 13.1 Desktop 4 종료 (Collector/History/Backup을 실행하는 중앙 PC가 꺼져 있던 경우)

```text
Desktop 4 재시작
      ↓
daily_history_state.json 등 State 파일 확인
      ↓
last_successful_daily_close 대비 누락 날짜 계산
      ↓
Startup Catch-up 실행 (오전 11시를 기다리지 않고, 이미 종료된 날짜까지 즉시 처리 가능)
      ↓
당일(오늘)은 처리하지 않음 — 아직 업무 중인 날짜이기 때문
```

Desktop 4가 며칠간 꺼져 있었더라도 그동안 Source Desktop들이 만든 Event는 각자의 Local Queue/Transport 경로에 안전하게 남아 있으므로 유실되지 않는다 (`06_DAILY_HISTORY_SPEC.md` §30-34, `07_SCHEDULER_CATCHUP_SPEC.md` §15-19).

### 13.2 Transport 실패 (전달 자체가 안 되는 경우)

```text
Source Desktop은 계속 정상 업무 수행 가능
      ↓
Event는 계속 Local Queue에 쌓임 (유실 없음)
      ↓
Transport 연결이 복구되면 쌓인 Event를 순서에 관계없이 모두 전달 시도
      ↓
Desktop 4 Collector가 event_id 기준으로 개별 판정 (Duplicate 포함)
```

Transport 자체의 Recovery는 "무엇을 다시 보낼지"를 Source Local Queue가 그대로 기억하고 있으므로 별도의 복구 절차가 필요 없다 — 밀린 것을 순서 상관없이 다시 시도하면 된다.

### 13.3 Collector 중단 (처리 도중 프로세스가 죽은 경우)

```text
Event A: incoming → 처리 완료 → processed/  (안전)
Event B: incoming → 처리 도중 → 프로세스 종료
      ↓
Event B는 incoming에 원본 그대로 존재 (§11 Atomic Processing 원칙)
      ↓
다음 Collector 실행 시 Event B를 처음부터 다시 판정
      ↓
Event B가 이미 한 번 ACCEPTED로 판정된 뒤 죽었더라도, SeenEventStore에 기록되지 않았다면
  다시 정상적으로 ACCEPTED 판정됨 (원자성: 판정과 seen 기록이 실질적으로 하나의 단위로 취급되어야 함)
```

Process Lock(`07_SCHEDULER_CATCHUP_SPEC.md` §24-27)이 동시에 두 Collector Runner가 같은 `incoming`을 처리하는 것을 막는다. Lock 소유 프로세스가 실제로 죽었는지 확인 후(Stale Lock), 살아있지 않다면 Lock을 해제하고 새 실행을 진행한다 — 단순히 시간이 지났다는 이유만으로 정상 실행 중인 프로세스를 강제 종료하지 않는다.

### 13.4 프로그램 재시작 (Desktop 4가 아니라 Company Ops 프로세스 자체의 재시작)

```text
재시작
      ↓
Lock 파일 확인 → Stale Lock이면 제거
      ↓
Collector 먼저 실행 (밀린 incoming 전량 소진)
      ↓
Catch-up 판단 (Daily Close 누락 날짜 계산)
      ↓
날짜별로 순서대로 재처리, 중간 날짜 실패 시 그 이후 날짜로 넘어가지 않고 실패를 기록
      ↓
Backup Pending 확인 후 재시도
```

핵심 원칙: 어떤 단계에서 중단되었든, **이미 성공한 부분을 다시 실행하지 않는다** (State가 마지막 성공 지점을 기억). **아직 성공하지 않은 부분만 재시도한다.**

---

## 14. Component Dependency

```text
Reporter
   │  (선택적으로 의존)
   ▼
Transport ◄──────────────┐  (Interface만 존재, 구체 구현은 아직 아무도 의존하지 않음)
   │                      │
   ▼                      │
Collector                 │  Transport는 Collector Interface를 호출하는
   │  (내부적으로 events    │  방향으로 설계된다 (COO Architecture Decision).
   │   패키지만 사용)        │  Collector는 Transport를 알지 못한다.
   ▼                      │
 ┌─┴──────────┐            │
 ▼            ▼            │
Notion     History Filter  │
Sync           │           │
               ▼           │
          Daily Close      │
               │           │
               ▼           │
        Local Master       │
               │           │
               ▼           │
            Backup ─────────┘ (아님 — Backup은 Transport와 무관, 별도 GitHub Repo 원칙)
               │
               ▼
        Monthly History (Daily 확정본만 참조)

Scheduler
   │  (Collector/History/Daily/Backup의 "언제"만 결정, "어떻게"는 각 Component가 소유)
   ▼
Collector → History Filter → Daily Close → Backup  (실행 순서를 지시할 뿐 로직을 대신하지 않음)
```

의존성 방향 요약:

- Reporter → `events` (Phase 1 스키마), 선택적으로 → Transport Interface
- Transport (구현체) → Collector Interface (COO Architecture Decision: Transport가 Collector Contract에 맞춰 구현되어야 함)
- Collector → `events`만 의존. Transport/Reporter/Notion/History를 모른다.
- History Filter/Daily/Monthly → Collector가 만든 ACCEPTED Event(및 그 안의 `history_candidate`)를 소비. Collector를 호출하지 않는다 (호출 방향이 반대: Collector가 History를 부르는 게 아니라, 후속 단계가 Collector의 산출물을 읽는다).
- Backup → Local Master(파일)만 읽는다. Daily/Monthly/Collector 로직을 모른다.
- Scheduler → 각 Component의 실행 시점만 결정. 각 Component의 내부 로직에 관여하지 않는다.

---

## 15. Runtime에서는 절대 하면 안 되는 것

| 금지 사항 | 이유 |
|---|---|
| Collector가 History Markdown을 직접 저장 | Collector와 History Generator는 분리된 책임이다 (`03_COLLECTOR_SPEC.md` §42) |
| Collector가 Notion을 직접 갱신 | Collector는 판정만 한다. 전달은 후속 orchestration의 역할 |
| Reporter가 Backup을 수행 | Reporter는 Event 생성 계층일 뿐, Company History나 GitHub를 알지 못한다 (`03_COLLECTOR_SPEC.md` §41) |
| Reporter가 Notion/Collector/History를 직접 호출 | Reporter → Event → Transport 까지만. 구조 유지 필수 |
| Transport가 Event Validation을 수행 | Validation은 Collector(및 그 기반인 Event Schema)의 책임. Transport가 임의로 검증하면 이중 기준이 생긴다 |
| Transport가 Event 내용을 수정 | 원본 사실을 보존해야 한다 (`02_EVENT_SCHEMA.md` §34) |
| History Filter가 Collector를 재호출 | 방향이 거꾸로다. History는 Collector의 산출물을 읽기만 한다 |
| Daily Close가 Collector 실행일 기준으로 날짜를 정함 | 반드시 Event의 실제 `timestamp` 기준 (`06_DAILY_HISTORY_SPEC.md` §12) |
| GitHub 장애를 이유로 Local History를 삭제/수정 | Local이 원본이다. Remote 장애가 원본을 훼손해서는 안 된다 (`03_COLLECTOR_SPEC.md` §30) |
| Backup Working Copy → Local Master로 역방향 복사 | 허용된 방향은 Local → Working Copy 뿐 (`08_BACKUP_SPEC.md` §13) |
| REJECTED/실패 Event를 자동 삭제하거나 "성공"으로 위장 | 데이터 안전이 최우선 원칙 (`03_COLLECTOR_SPEC.md` §53) |
| 여러 Runner(Scheduler + 수동 실행 등)가 Lock 없이 동시에 같은 incoming을 처리 | 중복 처리, State 충돌, Backup 충돌 위험 (`07_SCHEDULER_CATCHUP_SPEC.md` §25) |
| 실패를 숨기고 알림 없이 조용히 넘어감 | 실패 은폐 금지 (`07_SCHEDULER_CATCHUP_SPEC.md` §64) |
| Duplicate/Retry 처리를 위해 별도 Message Broker(Kafka/Redis 등) 도입 | V1 규모에서 불필요한 Infrastructure (`03_COLLECTOR_SPEC.md` §49) |

---

## 16. V1에서 의도적으로 결정하지 않은 것

다음은 본 문서가 흐름을 설계하면서도 일부러 확정하지 않은 부분이다. Runtime 구현 전에 별도 결정이 필요하다.

1. ~~Transport 구체 기술 (GitHub / OneDrive / USB / SharedFolder) — 이미 별도 분석 및 COO 결정 대기 중.~~
   **결정되었고 구현되었다.** `src/transport/onedrive.py`(`OneDriveTransport`)가
   유일한 production Transport이며 `run_agent.py`가 이것을 만든다. USB /
   SharedFolder / GitHub Transport는 작성된 적이 없다.
   `src/transport/interface.py`의 모듈 docstring이 같은 정정을 이미 담고
   있고(C122), 그 파일의 테스트가 두 사실을 트리에 대고 검사한다. 이 줄은
   그 정정이 도달하지 못한 마지막 자리였다 — "아직 정하지 않았다"고 적힌
   목록에 이미 정해지고 배포된 항목이 남아 있으면, 그 목록 전체를 읽을 수
   없게 된다.

   **다만 Transport는 D+1 운영보고의 필수 경로가 아니다 (C149).**
   `src/delivery/git_activity.py`가 개발 활동을 로컬 git에서 직접 읽으므로,
   OneDrive가 가득 찼거나 로그아웃됐거나 그 기계가 꺼져 있던 날에도 "어제
   무엇이 바뀌었는가"는 답이 있다. 그전에는 그런 날이 "조용한 날"과 구별되지
   않았다 — 신호가 없는 실패였다. 자세한 것은 `docs/15_D1_COMPANY_UPDATE_SPEC.md`.
2. `runtime/events/processed/`의 장기 누적을 완화할 archive 정책 — V1에서는 불필요, 향후 검토.
3. Collector Runtime의 `SeenEventStore` 실제 구현(파일 기반 `collector_state.json` 등) — Interface만 존재.
4. History Filter의 KEEP/DROP/REVIEW 자동 판정 로직의 구체 규칙 엔진 (AI 사용 여부 포함, `05_HISTORY_PIPELINE_SPEC.md` §56-57).
5. Scheduler를 Windows Task Scheduler에 실제로 등록하는 작업 — 명시적으로 이번 Phase 범위 밖.

---

## 17. 다음 문서와의 관계

본 문서는 `01_V1_IMPLEMENTATION_PLAN.md`의 구현 순서를 대체하지 않는다.

Runtime 구현이 시작되면, 각 Component는 여전히 자신의 개별 Spec(`03`~`09`)을 세부 기준으로 따른다.

본 문서는 그 문서들 사이의 "이음매"만 담당한다.

---

# END OF DOCUMENT
