# 14. Run Contract & Artifact Taxonomy

이 문서는 **Runner 1회 실행의 계약**과 **산출물 분류**를 정의한다.
구현은 `src/runsummary.py`(어휘와 계산)와 `src/app/runner.py`(매핑)에 있다.

---

## 1. Operational Data Model

용어를 먼저 고정한다. 같은 파일을 두 사람이 다른 이름으로 부르면 어느 것이
진실인지에 대한 합의가 없다는 뜻이다.

| 분류 | 무엇인가 | 진실의 지위 |
|---|---|---|
| **Company Repository** | Daily / Monthly History Markdown | **Policy / Knowledge Source of Truth** |
| **Execution Evidence** | 로그와 Event 파일 — 실행이 무엇을 했는지의 증거 | 실행의 증거 |
| **Operational State** | state/·locks/ — 재개 지점 | 증거가 아님(재생성 가능) |
| **Operational Projection** | Notion (PROJECTS / OPS_RUNS) | **View이며 절대 Source가 아니다** |
| **Run Manifest** | 실행 1회의 요약 + Evidence 참조 | Evidence를 가리키는 색인 |

Notion이 Projection이라는 것이 왜 중요한가: Notion이 죽어도 Company History는
계속 기록된다(README RULE 5·9). Projection의 실패는 Source의 실패가 아니며,
아래 Severity 분류가 그 사실을 코드로 옮긴 것이다.

---

## 2. Artifact Taxonomy (실측)

**먼저 실측하고 분류했다. 새 폴더를 만들지 않았다** — 아래 경로는 Run Manifest가
생기기 전부터 파이프라인이 이미 쓰고 있던 것들이다. 신설한 것은
`runtime/runs/last_run.json` 하나뿐이며, 그것이 나머지를 가리킨다.

    runtime/
    ├─ local_master/daily|monthly/   Company Repository
    ├─ backup_working_copy/          Company Repository (원격 사본)
    ├─ logs/                         Execution Evidence
    │    collector.log
    │    notion_sync.log             (DASHBOARD 줄 포함)
    │    daily_late_update.log       (SCHEDULER_FAILED / MONTHLY_* 포함)
    ├─ agent/logs/agent.log          Execution Evidence
    ├─ events/                       Execution Evidence
    │    transport|incoming|processed|rejected/
    ├─ history_candidates/keep|review/   Execution Evidence
    ├─ state/                        Operational State
    ├─ locks/                        Operational State
    └─ runs/last_run.json            Run Manifest   <- 이번에 신설

> `local_master`라는 **디렉터리·인자 이름**은 docs/04~12와
> `run_once(local_master_dir=...)` 공개 API가 고정하고 있어 그대로 둔다.
> 운영자에게 보이는 어휘는 Company Repository다(`ops_status.py`).
> 이름 변경은 Breaking Contract이며 BACKLOG A-17 참조.

---

## 3. Run Summary는 로그가 아니다

    Run Summary   무엇이 일어났는가 — 한 화면
    artifact_ref  자세한 내용은 어디에 있는가
    Evidence      실제 내용

Manifest는 **Event 1건당 줄을 쓰지 않는다.** Event 500건을 처리한 실행도
`{"accepted": 500}` 한 줄이고, 자세한 것은 `logs/collector.log`를 가리킨다.
작업량에 비례해 커지는 것은 로그이며, 그러면 Manifest일 수 없다.

---

## 4. Component Status / Overall Status

**Component Status** — 단계 1개의 결과

| 값 | 뜻 |
|---|---|
| `SUCCESS` | 정상 |
| `FAILED` | 실패 (반드시 분류된 `Failure`를 동반한다) |
| `SKIPPED` | 미설정 — **실패가 아니다** |

`SKIPPED`가 실패가 아닌 것이 핵심이다. Notion 미설정은 지원되는 배포 형태이고
(docs/04), 그것을 실패로 보고하면 Notion 도입 전의 모든 설치가 영원히 고장난
것으로 보인다.

**Overall Status** — 실행 전체

| 값 | 조건 | Exit Code |
|---|---|---|
| `SUCCESS` | 실패한 Component 없음 | `0` |
| `DEGRADED` | 실패했지만 전부 비critical | `3` |
| `FAILED` | CRITICAL Component 1개 이상 실패 | `2` |

`DEGRADED`가 없으면 모든 실행은 "정상 아니면 고장"뿐인데, 이 파이프라인의
설계 자체가 **대부분의 실패는 둘 다 아니라는 것**이다. DEGRADED를 SUCCESS로
접으면 실제 고장이 숨고, FAILED로 접으면 늑대 소년이 되어 아무도 안 본다.

`1`은 **설정 오류** 전용이다(실행이 시작조차 못 한 경우).
`3`은 `ops_status.py`의 기존 "사람이 확인해야 함"과 같은 뜻이다 — 두 진입점이
같은 숫자로 같은 말을 한다.

---

## 5. Failure Classification

    Failure -> Classification -> Severity / Retryability -> Overall Status -> Exit Code

**Severity** — 이 실행의 목적을 위협하는가. 목적은 Company History다.

| Component | Severity | 근거 |
|---|---|---|
| transport / collector / history_filter / daily / backup | `CRITICAL` | History를 기록하거나 보호한다 |
| notion_sync / dashboard / monthly / late_update | `DEGRADED` | History를 다른 곳에 투영하거나 보정한다 |

**Retryability** — 같은 일을 다시 해서 될 일인가. Severity와 분리한 이유는
서로 다른 질문에 답하기 때문이다. Severity는 Exit Code를, Retryability는
**운영자가 지금 움직여야 하는지**를 결정한다. 둘을 합치면 BUG-13이 된다 —
영구 400과 일시적 503이 같은 `NOTION_RETRY_REQUIRED`로 보고되어 큐가 영원히
재전송했다.

| 값 | 운영자의 행동 |
|---|---|
| `RETRYABLE` | 없음. 다음 실행이 재시도한다 |
| `PERMANENT` | 지금 개입해야 한다 (`ops_status.py` ATTENTION에 뜬다) |
| `UNKNOWN` | 확인 필요 |

예: Backup push 실패는 `BACKUP_PENDING`/`RETRYABLE`/`DEGRADED`(docs/08 §19,
일상적)이지만, 인증 실패는 `BACKUP_FAILED`/`PERMANENT`/`CRITICAL`이다(§21,
§62가 무한 재시도를 금지한다).

---

## 6. Event Contract는 바뀌지 않는다

Run Contract는 Event Contract를 **건드리지 않는다.** 두 반쪽이 따로 있다.

- `event_id`에 개행이 있어도 **스키마는 계속 받아들인다**(docs/02).
  좁히면 이미 수집된 Event가 거부된다 — BACKLOG A-15.
- 로그에 쓸 때 **canonical escaping을 적용한다**(`oplog.one_line()`).
  `str.splitlines()`가 끊는 문자 전부가 대상이다.

로그 서식 문제를 "받아들이는 것을 바꿔서" 푸는 것은 비용을 이미 보낸 Desktop
쪽으로 미루는 것이다. `tests/test_run_contract.py::EventContractPreservationTests`가
두 반쪽을 함께 고정한다.

---

## 7. Manifest는 중단된 실행에도 남는다

`run_once()`는 `finally`에서 Manifest를 쓴다. 5단계에서 죽은 실행도
"앞의 네 단계는 무엇을 했는가"에 답할 수 있어야 하기 때문이다 — 이전에는
`run_company_ops.py`가 보고 코드에 도달하지도 못해 **아무것도 출력하지 않았다.**

예외를 던진 단계는 추측하지 않고 `recorder.current`로 귀속시킨다.
예외는 그대로 전파된다 — `run_once()`가 흡수해야 하는지는 BUG-4이며 여기서
결정하지 않는다.

Lock을 얻지 못한 실행은 Manifest를 **쓰지 않는다.** 한 일이 없으므로 보고할
것이 없고, 실제로 일한 직전 실행의 기록을 빈 것으로 덮어쓰면 안 된다.
