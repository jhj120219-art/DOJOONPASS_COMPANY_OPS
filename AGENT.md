# Multi-Desktop Agent — 운영 안내

이 문서는 명세가 아니다. `README.md`와 `docs/`가 정하는 구조를 바꾸지 않으며,
그 구조 위에서 **각 Desktop이 실제로 무엇을 실행하는지**만 설명한다.
충돌 시 우선순위는 README §13을 따른다.

---

## 1. 무엇인가

Desktop 1~4가 **동일한 Agent**를 실행한다. 다른 것은 환경변수뿐이다.

```
Desktop 1 (CTO Backend)  ┐
Desktop 2 (CMO)          ├─ run_agent.py ─ outbox/ ─ OneDrive ─┐
Desktop 3 (CTO Frontend) │                                     │
Desktop 4 (COO)          ┘                                     ▼
                                                    Desktop 4 transport/
                                                               │
                                              run_company_ops.py (기존)
                                                               │
                            Collector → History → Daily → Backup → Notion
```

Transport 이후는 **전부 기존 코드**다. Agent는 새 Company Ops를 만들지 않고
`Reporter / Transport / Collector / History / Scheduler / Backup / Notion`을
그대로 사용한다.

Desktop 4도 예외가 아니다. COO의 업무 역시 같은 Agent로 같은 Transport를
거쳐 자기 자신의 `runtime/events/transport/`에 들어가고, 기존
`run_intake()`가 `incoming/`으로 승격시킨다. 우회 경로는 없다.

---

## 2. 설치

### 2.1 환경변수

```
COMPANY_OPS_PROFILE=DESKTOP_1            # DESKTOP_1 | DESKTOP_2 | DESKTOP_3 | DESKTOP_4
COMPANY_OPS_AGENT_SYNC_FOLDER=C:\Users\me\OneDrive\CompanyOpsEvents
COMPANY_OPS_AGENT_START_DATE=2026-08-10  # YYYY-MM-DD
```

Role은 따로 설정하지 않는다. `src/reporter/profiles.py`가 docs/02 §8의
source→role 표 그대로이므로, Desktop이 스키마에 없는 역할을 주장할 수 없다.

**Agent는 어떤 비밀값도 필요로 하지 않는다.** `NOTION_API_TOKEN` 등은 Desktop
4의 Runner만 사용한다.

### 2.2 Windows 자동 실행

먼저 **무엇이 바뀔지 확인**한다. `-WhatIf`는 아무것도 바꾸지 않는다 —
환경변수도 Task도 건드리지 않고 예정된 작업만 출력한다.

```powershell
cd scripts
powershell -ExecutionPolicy Bypass -File .\install_agent_task.ps1 `
    -DesktopId DESKTOP_1 `
    -SyncFolder "C:\Users\me\OneDrive\CompanyOpsEvents" `
    -StartDate 2026-08-10 -WhatIf
```

출력이 예상과 맞으면 `-WhatIf`만 빼고 다시 실행한다.

> **`-ExecutionPolicy Bypass`가 필요하다.** Windows 기본 실행 정책은 서명되지
> 않은 로컬 스크립트를 거부하며, 없이 실행하면 `UnauthorizedAccess`로 끝난다.
> 위 형태는 **그 PowerShell 프로세스에만** 적용되고 시스템 정책은 바꾸지 않는다.
> `Set-ExecutionPolicy`로 머신 전체를 바꿀 이유는 없다.
>
> **관리자 권한이 필요할 수 있다.** 머신에 따라 `Register-ScheduledTask`가
> 비관리자 세션을 거부한다(개발 머신에서 실측: 빈 Task조차 거부됐다).
> 거부되면 스크립트가 원인과 조치를 알려주니 그대로 따르면 된다 — 그 경우
> PowerShell을 관리자로 실행해 다시 돌린다. 재실행은 안전하다(`-Force`).

등록되는 것: `DOJOONPASS_COMPANY_OPS_AGENT_<DESKTOP_ID>`, **At log on**
트리거, 2분 지연(docs/07 §54), `MultipleInstances=IgnoreNew`(§55).

**PC를 24시간 켜 둘 필요가 없다.** docs/07 §58이 이미 정한 대로 OFF를 허용하고
Catch-up으로 복구한다. 켜질 때마다 1회 실행되어 마지막 성공일 다음 날부터
어제까지를 모두 따라잡는다.

`-DailyAt "11:00"`을 주면 자정을 넘겨 계속 켜져 있는 머신용 일일 트리거가
추가된다. 이것은 안전장치가 아니라 편의 기능이다 — 안전장치는 Catch-up이다.

---

## 3. Signal 작성

Agent는 무엇이 "의미 있는 작업"인지 **추론하지 않는다**(README RULE 4).
각 역할이 명시적으로 Signal 파일을 놓는다.

```
runtime/agent/signals/2026-08-10/search-api-done.json
```

```json
{
  "project_id": "SEARCH_BACKEND",
  "event_type": "MILESTONE_COMPLETED",
  "status": "IN_PROGRESS",
  "summary": "검색 API p95 320ms -> 180ms",
  "milestone": "Search Latency",
  "evidence": ["pytest PASS", "부하 테스트 리포트"],
  "history_candidate": true
}
```

| 필드 | 필수 | 비고 |
|---|---|---|
| `project_id` | ✔ | |
| `event_type` | ✔ | `events.schema.EVENT_TYPES` |
| `status` | ✔ | `events.schema.STATUSES` |
| `summary` | ✔ | |
| `milestone` / `blocker` | | `BLOCKED`는 `blocker` 필수(docs/02) |
| `evidence` | | 문자열 배열 |
| `history_candidate` | | 기본 `false` |
| `timestamp` | | 생략 시 그 날짜의 자정(로컬) |

**쓸 수 없는 필드**: `source`, `role`, `event_id`, `schema_version`.
신원은 Profile에서 오고 `event_id`는 Agent가 결정론적으로 만든다. 이 필드를
넣으면 Signal 전체가 거부된다 — 조용히 무시되지 않는다.

`timestamp`를 직접 쓸 경우 **반드시 그 날짜여야 한다**. Daily History는
Event 자신의 timestamp로 날짜를 나누므로(docs/06 §12), 어긋나면 수집된 날짜와
기록되는 날짜가 달라진다. 그래서 거부한다.

### 거부되는 Signal

거부된 Signal은 삭제되지 않고 `runtime/agent/signals_rejected/<날짜>/`로
옮겨진다. 나머지 Signal은 정상 처리되며 그 날짜는 완료된다.

거부 사유: JSON 오류 / 미지 필드 / 신원 필드 / 날짜 불일치 / Event Schema
위반(예: blocker 없는 BLOCKED) / **secret 형태의 내용** / symlink.

---

## 4. 동작 규칙

### Catch-up

```
마지막 성공 = 2026-08-07,  오늘 = 2026-08-11
  →  08-08, 08-09, 08-10   (오래된 날짜부터, 오늘은 제외)
```

시각 기반 스케줄이 아니라 **날짜 산술**이다. `scheduler/scheduler.py`가 Daily
History에 쓰는 규칙과 정확히 같다(docs/07 §18, §21).

### 마지막 성공 지점

```
08-08 성공, 08-09 실패
  →  last_successful_collection_date = 08-08
  →  다음 실행은 08-09부터
```

한 날짜라도 전송에 실패하면 그 자리에서 멈춘다. 뒤 날짜를 먼저 보내
순서에 구멍을 내지 않는다.

### 유실 방지

Event는 전송 **전에** `outbox/`에 원자적으로 기록되고, 전송이 성공한 **후에**
`sent/`로 옮겨진다. 어느 순간에 죽어도 Event는 셋 중 정확히 하나의 상태다:
미생성 / outbox(재시도됨) / sent(완료).

### 중복 방지

`event_id = uuid5(고정 네임스페이스, "<Desktop>|<날짜>|<Signal 파일명>")`

같은 Signal은 언제 몇 번 실행해도 같은 Event가 된다. 그래서 crash 후 재전송이
`OneDriveTransport` → `run_intake()` → `Collector` 세 계층 모두에서 중복으로
인식된다. 무작위 `event_id`였다면 같은 성과가 History에 두 번 남았을 것이다.

### 동시 실행

로그온 트리거와 수동 실행이 겹치면 `scheduler/lock.py`의 lock으로 한쪽이
`SKIPPED_ALREADY_RUNNING`이 된다. Agent lock은 Desktop 4 Runner lock과
**다른 파일**이다 — 둘은 서로 다른 구간을 보호하며 Desktop 4에서 동시에
실행되어도 정상이다.

---

## 5. 디렉터리

모두 `runtime/` 아래이며 git-ignored다.

```
runtime/agent/
├─ signals/<날짜>/*.json          작성하는 곳
├─ signals_rejected/<날짜>/*.json 거부된 Signal (보존)
├─ outbox/*.json                  전송 대기 (비어 있는 것이 정상)
├─ sent/*.json                    전송 완료
├─ outgoing/*.json                Transport 스테이징
├─ state/agent_state.json         last_successful_collection_date
├─ locks/agent.lock
└─ logs/agent.log
```

`state/agent_state.json`은 `desktop_id`를 함께 기록한다. 다른 Desktop의 상태
파일이 섞여 들어오면 실행을 거부한다 — 남의 수집 완료일을 물려받아 그 사이
날짜를 통째로 건너뛰는 사고를 막기 위해서다.

---

## 6. 확인 방법

```powershell
python ops_status.py     # 먼저 이것부터 — 사람이 할 일이 있는지 한 화면에
python run_agent.py      # 수동 1회 실행
```

`ops_status.py`는 아무것도 쓰지 않고 lock도 잡지 않는다. Runner나 Agent가
도는 중에 실행해도 안전하다. 두 가지를 보여준다.

**COMPANY** — Desktop 4가 수집한 Event 기준으로 각 Desktop의 마지막 소식,
침묵 일수, 아직 수집되지 않은 backlog.

**AGENT** — 이 머신 Agent의 마지막 실행, 마지막 수집 날짜, 미수집 날짜,
outbox/sent 개수, 거부된 Signal 수.

마지막에 **ATTENTION** 절이 나온다. 비어 있으면 지금 사람이 할 일은 없다.

종료 코드: `0` 정상, `1` 설정 오류, `3` 확인이 필요한 항목 있음.

COMPANY 줄은 두 가지를 따로 보여준다.

```
DESKTOP_1   events=12   role=CTO_BACKEND   작업일 5일 전 도착 1일 전
```

**작업일**은 Event 자신이 말하는 "그 일이 있었던 날"이고, **도착**은 그 파일이
Desktop 4에 나타난 때다. 위 예시는 "5일치 밀린 작업을 어제 한꺼번에 보냈다"는
뜻이다 — Desktop은 살아 있다. 둘 다 5일 전이면 그 Desktop에서 아무것도 오지
않고 있다는 뜻이다.

> 그래도 (1) PC가 꺼져 있어서인지 (2) 보고할 일이 없어서인지는 **여전히
> 구분하지 못한다**. 완전히 구분하려면 heartbeat Event Type이 필요한데
> 스키마에 없다(`BACKLOG.md` A-11c).
>
> 도착 시각은 OneDrive를 건너온 파일 시각이라 **정황이지 측정이 아니다.**
> 그래서 경보를 좁히는 데만 쓰고 **끄지는 않는다** — 조용한 Desktop은 도착이
> 최근이어도 계속 ATTENTION에 남는다.

원시 파일을 직접 볼 때:

```powershell
Get-Content runtime\agent\state\agent_state.json      # 마지막 성공일
Get-ChildItem runtime\agent\outbox                    # 비어 있어야 정상
Get-ChildItem runtime\agent\signals_rejected -Recurse # 사람이 봐야 할 것
Get-Content runtime\agent\logs\agent.log -Tail 20
Get-Content runtime\logs\daily_late_update.log        # Desktop 4: 늦게 도착한 Event
```

`run_agent.py` 종료 코드: `0` 정상/skip, `1` 설정 오류, `2` 전송 실패(유실
아님 — outbox에 남아 있고 다음 실행에서 같은 날짜부터 재시도).

---

## 6b. 늦게 도착한 Event

Desktop이 며칠 꺼져 있다가 켜지면, 이미 Daily Close가 끝난 날짜의 Event를
보내게 된다. 이것은 예외가 아니라 Multi-Desktop 구성의 일상이다.

Desktop 4는 그 Event를 해당 날짜의 Daily History에 **추가**한다
(docs/06 §36-40). 기존 내용은 한 줄도 바뀌지 않고, 새 항목은
`## Late Events` 절에 붙으며, Metadata에 흔적이 남는다.

```
- Generated At: 2026-08-09T11:00:00+09:00      (처음 닫힌 시각 — 바뀌지 않음)
- Last Updated At: 2026-08-12T10:00:00+09:00
- Late Events Added: 1
- Event Count: 3
```

같은 `event_id`는 두 번 추가되지 않는다. COO가 손으로 고친 내용은 보존된다 —
그래서 파일을 다시 렌더링하지 않고 덧붙인다(docs/06 §57).

**Notion은 이 경우 갱신되지 않는다.** 의도된 것이다: Notion은 Current
State(README RULE 1)이므로 3일 전 Event가 오늘의 상태를 덮어써서는 안 되고,
History는 영구 기록(RULE 2)이므로 늦게 와도 그날에 들어가야 한다. 두 목적지가
서로 다르게 동작하는 것이 설계다.

---

## 6c. 월간 정리 (Monthly History)

달이 끝나면 Desktop 4가 그 달의 Daily를 모아 한 파일로 만든다.

```
runtime/local_master/monthly/2026-08.md
```

별도 실행이 아니다. `run_company_ops.py`가 Daily Catch-up 다음, Backup 앞에서
자동으로 처리한다(docs/09 §50-51). 백업까지 같은 실행에서 끝난다.

**전제: 그 달의 Daily가 모두 있어야 한다**(§10). 하루라도 비어 있으면 Monthly를
만들지 않고 넘어간다 — Daily Catch-up이 먼저 그 구멍을 메우고, 그 다음 실행에서
통합된다. 짧은 Monthly를 만드느니 안 만든다.

**현재 달은 절대 만들지 않는다**(§49). 8월 25일에 실행해도 8월은 대상이 아니다.

**PC가 몇 달 꺼져 있어도 따라잡는다**(§47-48, §90). "오늘이 1일인가"가 아니라
"마지막으로 통합한 달이 언제인가"를 기준으로 하므로, 9월 3일에 켜도 8월은
정상적으로 만들어진다. 여러 달이 밀려 있으면 오래된 달부터 순서대로.

**늦게 온 Event는 그 달을 다시 만든다**(§54-57). 8월 Monthly가 이미 있는데
8월 20일자 Event가 9월에 도착하면, Daily가 갱신되고 → 그 달이 DIRTY로 표시되고
→ 같은 실행에서 Monthly가 재생성된다. Metadata에 흔적이 남는다.

```
- Generated At: 2026-09-01T11:00:00+09:00      (처음 통합한 시각)
- Last Updated At: 2026-09-03T15:20:00+09:00
```

**비어 있는 달도 파일을 만든다**(§72). "그 달에 중요한 일이 없었다"와
"그 달을 통합하는 걸 잊었다"를 구분하기 위해서다.

### 지금 들어가는 것 / 안 들어가는 것

들어감: Major Decisions / Major Milestones / Major Issues & Resolutions /
Key Learnings / Source Records / Metadata.

안 들어감: Executive Summary, Product Evolution, KPI, Open Risks,
Next-Month Carryover. 규칙만으로는 만들 수 없는 항목이고, docs/09 §30·§64·§65는
없는 것을 지어내는 것을 금지한다. 억지로 채우지 않는다(§14). 자세한 이유와
채우려면 무엇이 필요한지는 `BACKLOG.md` A-12에 있다.

상태 파일: `runtime/state/monthly_history_state.json`

---

## 7. 아직 안 되는 것

`BACKLOG.md` A그룹 참조. 요약하면:

- Desktop 3의 `ROLE=OTHER` — docs/02 §9 변경 필요
- `NO_ACTIVITY` Event Type — docs/02 변경 필요 (기능상 이미 불필요)
- 역할별 세부 분류(테스트/버그/콘텐츠/실험) — docs/05 category 변경 필요
- Monthly의 서술형 Section(Executive Summary 등) — AI 또는 수기 작성 필요
- Open Risks / Carryover — Candidate에 `event_type` 추가 필요
- Agent Heartbeat — "조용함"의 이유를 구분하려면 새 Event Type 필요
- Signal 자동 생성 — 역할별 워크플로 정책
