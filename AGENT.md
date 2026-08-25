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
> **관리자 권한은 필요 없다.** 여기 있던 "필요할 수 있다 — 빈 Task조차
> 거부됐다"는 측정은 **틀렸다.** 실제 원인은 이 스크립트의 트리거에 `-User`가
> 빠져 있던 것이고(C13에서 수정), 거부되는 것은 machine-wide 트리거뿐이다.
> 같은 비관리자 세션에서 등록 → 실행 → Event 전달 → 수집까지 확인했다.
> 자세한 것은 §2b. 거부되면 스크립트가 판별 절차를 알려준다 —
> **elevation은 마지막 후보다.** 재실행은 안전하다(`-Force`).

등록되는 것: `DOJOONPASS_COMPANY_OPS_AGENT_<DESKTOP_ID>`, **At log on**
트리거, 2분 지연(docs/07 §54), `MultipleInstances=IgnoreNew`(§55).

**PC를 24시간 켜 둘 필요가 없다.** docs/07 §58이 이미 정한 대로 OFF를 허용하고
Catch-up으로 복구한다. 켜질 때마다 1회 실행되어 마지막 성공일 다음 날부터
어제까지를 모두 따라잡는다.

`-DailyAt "11:00"`을 주면 자정을 넘겨 계속 켜져 있는 머신용 일일 트리거가
추가된다. 이것은 안전장치가 아니라 편의 기능이다 — 안전장치는 Catch-up이다.

---

### 2.3 Desktop 4 (Runner)의 환경변수

§2.1의 셋은 **Agent**의 것이다. Desktop 4에서 도는 Runner와 상태 도구는 다른 것을
읽는다:

```
COMPANY_OPS_HISTORY_START_DATE=2026-08-01   # run_company_ops.py (필수)
NOTION_API_TOKEN=                           # 없으면 Notion Sync를 건너뛴다 (실패가 아니다)
NOTION_PROJECTS_DATABASE_ID=
NOTION_OPS_RUNS_DATABASE_ID=                # 없으면 Operations Dashboard만 건너뛴다
```

**정본은 `.env.example`이다** — 각 변수가 무엇을 하고 누가 읽는지 거기에 적혀 있고,
`tests/test_repository_hygiene.py`가 코드와 양방향으로 대조한다. Notion Workspace를
처음 세우는 절차는 `docs/13_NOTION_ENVIRONMENT_SETUP.md`다.

Notion Workspace를 처음 세울 때 한 번 돌리는 것이 **`init_notion.py`**다 —
Runtime 파이프라인의 일부가 아니고(Runner는 import하지 않는다) PROJECTS Database에
**없는 Property만** 만들며 기존 Property는 그대로 둔다. 여러 번 돌려도 안전하다.
절차와 무엇이 만들어지는지는 위 docs/13에 있다.

```powershell
python init_notion.py
```

`.env`는 **자동으로 읽히지 않는다.** 셸에서 export하거나 실행 스크립트가 직접
읽어야 한다(`.env.example` 머리말).

인자를 붙여 실행하면 각 도구가 **자기가 읽는 변수의 이름**을 알려주고 종료한다
(exit 1). 그 목록도 위와 같은 대조를 받는다 — C48 이전에는 일곱 이름 중 셋이
존재하지 않는 변수였다.

---

## 2b. Task Scheduler 등록 (실제 검증됨 — 관리자 권한 불필요)

```powershell
.\scripts\install_agent_task.ps1 `
  -DesktopId DESKTOP_1 `
  -SyncFolder "C:\Users\<you>\OneDrive\CompanyOpsEvents" `
  -StartDate 2026-08-10
```

**관리자 권한은 필요 없다.** 이전 안내는 필요하다고 했는데 **틀렸다** —
실제 원인은 이 스크립트의 트리거에 `-User`가 빠져 있던 것이었고(C13에서 수정),
비관리자 세션에서 등록 → 실행 → Event 전달 → 수집까지 전부 확인했다.

`-WhatIf`를 붙이면 아무것도 바꾸지 않고 무엇이 등록될지만 보여준다.

확인:

```powershell
Get-ScheduledTask     -TaskName DOJOONPASS_COMPANY_OPS_AGENT_*
Get-ScheduledTaskInfo -TaskName DOJOONPASS_COMPANY_OPS_AGENT_<ID>   # LastTaskResult 0 = 정상
Start-ScheduledTask   -TaskName DOJOONPASS_COMPANY_OPS_AGENT_<ID>   # 지금 1회 실행
```

**PC가 계속 켜져 있을 필요는 없다.** 트리거는 로그온 2분 뒤 1회이며
`StartWhenAvailable`이 켜져 있어 놓친 트리거는 다음 기회에 발화한다. 그것마저
놓쳐도 Catch-up이 안전장치다 — 검증에서 밀린 6일치가 한 번의 실행으로
따라잡혔다.

등록이 거부되면 메시지가 판별 절차를 알려준다. **elevation은 마지막 후보다** —
대부분의 경우 원인이 아니다.

`COMPANY_OPS_PROFILE`을 잘못 넣고 등록했다면 그 머신의 Agent state가 다른
Desktop에 묶여 있을 수 있다. 그때 Agent는 실행을 거부하고 무엇이 어긋났는지
알려준다. **state 파일을 직접 지우지 말 것** — 아직 수집되지 않은 날짜가
조용히 건너뛰어질 수 있다.

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

> **같은 날짜·같은 프로젝트에 Signal을 두 개 이상 쓸 때는 `timestamp`를 넣어라.**
>
> 생략하면 그 날짜의 자정이 들어가는데, 그것은 **모든 Signal에 대해 같은 값**
> 이다(catch-up이 결정론적이어야 하므로 의도된 설계다). Notion Sync는 현재
> `Last Updated`보다 **과거이거나 동시**인 Event가 Current State를 되돌리지
> 않게 막으므로(docs/04 §29-30), 같은 프로젝트의 **두 번째 Signal은 Notion에
> 반영되지 않는다.**
>
> **Company History(Daily/Monthly)에는 둘 다 정상적으로 들어간다** — 어긋나는
> 것은 Notion 쪽 Current State의 최신성뿐이다. 1초만 달라도 정상 적용된다.
>
> 자세한 재현과 두 명세의 관계는 `BACKLOG.md` E-23에 있다.

> **`blocker` / `milestone`이 2,000자를 넘으면 Notion 화면에서만 잘린다.**
>
> Notion은 텍스트 Property 하나를 2,000자로 제한하고, 넘으면 그 항목이 아니라
> **행 전체를 거절한다**(HTTP 400). 그래서 PROJECTS View로 보내는 값은 2,000자로
> 맞춰 나가고, 잘린 값은 `…`로 끝나므로 화면에서 바로 알아볼 수 있다.
>
> **원문은 잃지 않는다.** Event 파일(`runtime/events/processed/`)과
> `history_candidate`면 Company History에 그대로 있고, `python ops_status.py`의
> CONTROL TOWER 블록은 그 원문을 보여 준다 — docs/14 §1이 Notion을 "View이며 절대
> Source가 아니다"로 고정하는 것이 바로 이 뜻이다.
>
> 길어질 것 같으면 요약을 `blocker`에, 상세는 `evidence`에 두는 편이 화면에서 읽기
> 좋다.

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

시각 기반 스케줄이 아니라 **날짜 산술**이다. `src/scheduler/scheduler.py`가 Daily
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

로그온 트리거와 수동 실행이 겹치면 `src/scheduler/lock.py`의 lock으로 한쪽이
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
python ops_status.py       # 먼저 이것부터 — 사람이 할 일이 있는지 한 화면에
python dashboard_server.py # 같은 내용을 브라우저에서 (⇒ 6d)
python run_agent.py        # 수동 1회 실행
```

`ops_status.py`는 아무것도 쓰지 않고 lock도 잡지 않는다. Runner나 Agent가
도는 중에 실행해도 안전하다. **여섯 블록**을 보여준다.

**COMPANY** — Desktop 4가 수집한 Event 기준으로 각 Desktop의 마지막 소식,
침묵 일수, 아직 수집되지 않은 backlog. backlog 줄에는 왜 그 숫자가 줄지 않는지도
같이 나온다(`unparseable`, `future_dated`, `name_collision`, `incomplete`,
`already_collected`, `unreadable_incoming`) — 숫자만 있고 이유가 없으면 지워지지
않는 경보가 되기 때문이다. `incoming_incomplete_write`와
`rejected_incomplete_write`는 그것들과 다른 것을 센다: **Event가 아니라 중단된
쓰기의 잔여물**(`.tmp-…json`)이고, 보낸 Desktop을 확인할 필요가 없으며 지워도
안전하다. 같은 파일이 `incoming/`에 있으면 앞의 것, 다음 실행에서 Collector가
`rejected/`로 옮기면 뒤의 것으로 세어진다.

**HISTORY** — Company Repository의 daily/monthly 파일 수, 사람 검토를 기다리는
Candidate, **daily/monthly State와 실제 파일의 정합성**, 수집됐지만 History에
들어가지 못한 Event, **사람이 `review_cli.py`로 입력했는데 Company History에
반영되지 않은 Decision Context**(`검토 미반영` — 그 날짜의 Daily는 이미
렌더링됐고 Late Event 병합은 *새* Event만 대상이라 어떤 실행도 이 내용을 넣지
않는다. 내용은 `runtime/history_candidates/keep/`에 남아 있으니 유실은 아니지만
Company History에는 없다. `BACKLOG.md` C33 §3), **Monthly가 스스로 센 것보다 적게 기록한 달**
(`Consolidated Items`와 실제 항목 수가 다르면 그 Event는 그 달에서 사라진 것이다),
그리고 Backup Working Copy에 있으면 안 되는 파일 — 게이트가 이름을 아는 것과
**대소문자 때문에 못 알아보는 것**(`ID_RSA` 대 `id_rsa`)을 따로 보여준다.

> **`Event 내용에 Secret 형태의 문자열` 경보가 뜨면** 그 자격증명을 **교체**해야
> 한다. 이 머신의 Agent는 Signal에 그런 문자열이 있으면 그 자리에서 거부하지만, 다른
> Desktop에서 온 Event는 `validate_event()`만 거치고 그것은 내용을 읽지 않는다 —
> 실측으로 Daily History에 그대로 쓰이고 backup 원격까지 push된다. 파일을 고쳐도
> 원격 history에서는 사라지지 않는다. 화면에 찍히는 id는 전부 `[REDACTED]` 처리되므로
> **어느 Event인지는 파일명으로 찾는다.**

여기에 **Daily에는 있는데 그 달 Monthly에는 없는 Event**(`Monthly 원본 미반영`)도
나온다. Monthly는 Daily에서 **전부** 파생되므로(`docs/09` §12-13) 그 달이 통합된 뒤에
Daily가 바뀌면 — `docs/06` §57과 `docs/11` §71이 허용하는 손편집이 그렇다 — **어떤
실행도 그 달을 다시 만들지 않는다.** 닫힌 달을 다시 여는 것은 Late Event가 바꾼
날짜에 대한 자동 dirty 표시뿐이고, 그건 이미 정상 동작한다. 복구는 정확하다: 그 달을
dirty로 표시하고 한 번 실행하면 Monthly가 자기 원본과 다시 같아진다.

여기에 **Backup Working Copy에는 있고 Local Master에는 없는 Company History**
(`Master에서 사라짐`)가 함께 나온다. Working Copy는 한 방향으로만 쓰이고 삭제를
반영하지 않으므로, 거기 있는 이름이 Master에 없다는 것은 **그 파일이 있었고 지금
없다**는 뜻이다. `Daily 시퀀스 구멍`은 파일이 남아 있는 범위 안쪽만 보므로
사라진 것이 **가장 이른 날짜들**이면 아무것도 보고하지 않고, 날짜 이름의
**디렉터리**가 대신 서 있는 경우도 같다. 이 상태에서는 Backup이 add/commit/push를
전부 중단하므로(`docs/08` §31) 복구하기 전까지 **이후 History도 원격에 가지
않는다**.

**CONTROL TOWER** — 나머지 블록이 전부 *운영*을 본다면 이것은 *일*을 본다.
움직인 Project, 팀별 Event 수와 막힌 Project 수, 완료된 Milestone / 승인된
Decision / 해결된 Issue, 그리고 **열려 있는 Blocker**. Project 줄은 막힌 것을
맨 위에 올리고 그 다음은 조용한 순서다.

숫자는 전부 `runtime/events/processed/`의 Event에서 나오며 **추적 가능하다** —
Blocker 경보는 막힌 Project·팀·사람이 쓴 blocker 문구·그것을 말한 Event 파일
이름을 함께 댄다. 열린 Blocker는 파이프라인이 스스로 지우지 않는다: 그 팀이
`RESUMED` / `ISSUE_RESOLVED` / `COMPLETED`를 보고할 때 사라진다. 그래서
임계값 없이 **열려 있으면 곧 ATTENTION**이다.

`Desktop` 줄은 Team 아래 계층이다. **둘 다 있는 이유**는 `source`→`role`이 1:1인
동안에는 같은 분할이기 때문이 아니라, **어긋나는 순간 하나만으로는 그것이 보이지
않기** 때문이다. `validate_event()`는 `source`와 `role`을 **각각의 허용 집합에
대해서만** 검사하고 **짝은 검사하지 않으므로**, 손으로 쓴 Event나 복원된 파일이
"DESKTOP_1에서 왔는데 CMO 일을 했다"고 말해도 전부 통과한다. 그 경우 Team 집계와
Desktop 집계가 서로 다른 곳으로 가고 Notion PROJECTS 행은 `Owner`와 `Source`가
서로 다른 Desktop을 가리킨다 — 그 Event를 **ATTENTION이 이름으로 댄다**
(`role 어긋남`). 거부하지는 않는다: 거부하면 그 Event가 `rejected/`로 가서 Company
History에서 아예 사라지고, "기록을 잃는 것"과 "잘못된 주인으로 기록하는 것" 중
무엇이 나쁜지는 결정 사항이다(`BACKLOG.md`).

조용한 팀과 조용한 Desktop은 여기서 세지만 경보로 올리지 않는다 — `source`가
COMPANY 블록의 키이기도 해서, 그것은 그 블록이 이미 말하는 사실이다.

> **`증거 범위 밖` 줄이 보이면** 이 블록의 숫자는 회사 전체가 아니라 **남아 있는
> Event가 덮는 기간**만 말한다. `runtime/events/processed/`는 Backup 범위가 아니므로
> (`docs/08` §26) **복원한 머신은 Company History를 전부 되찾고 Event는 하나도 되찾지
> 못한다.** 그 상태에서 이 블록은 "아무 일도 없었다"처럼 보이는데 사실이 아니고, 그
> 줄이 그것을 말한다. 경보로 올리지 않는 이유는 그 Event를 되돌리는 조치가 없기
> 때문이다 — Company History는 그대로 있다.

> **Goal / Team Goal / Sprint / Task 계층은 비어 있지 않고 아예 없다.** Event
> Schema에도 Company Repository에도 그 계층의 원천이 없어서, 블록 마지막 줄이
> 그렇게 말한다. Notion에 적어 넣는 것은 답이 아니다 — `docs/14` §1이 Notion을
> "**View이며 절대 Source가 아니다**"로 고정한다. 어디에 두어야 하는지는
> `BACKLOG.md`에 있는 결정 사항이다.

**LAST RUN** — 마지막 Runner 실행의 Run Manifest: 실행 시각, 종합 상태,
실패한 단계와 **그 단계의 수치**(예: `queued=47`), 시작조차 못 한 단계,
Runner Lock 상태. 마지막 실행이 너무 오래됐으면 그것도 여기서 걸린다.

**NOTION** — Notion에 아직 닿지 못한 것: Retry Queue에 남은 Event 수, 최대
재시도 횟수, **가장 오래된 항목이 며칠째 남아 있는지**, 그리고 Operations
Dashboard의 밀린 기록 수. 마지막 두 가지가 이 블록의 이유다 —
`NOTION_RETRY_REQUIRED`는 "Notion이 잠깐 죽었다"와 "Notion이 이 요청을 영원히
거부한다"를 같은 한 단어로 보고하고(`BACKLOG.md` BUG-13), 그 둘을 가르는 것은
**얼마나 오래 막혀 있었는가**다. 사흘을 넘긴 항목은 ATTENTION에 뜬다 —
일시적 장애라면 이미 빠져나갔을 시간이다. 그때는 `notion_sync.log`의
`REASON`을 본다. 큐 파일 자체가 손상된 경우도 여기서 보고한다(특히
`dashboard_pending.json`은 손상돼도 Runtime을 멈추지 않으므로 — CEO 결정 ④ —
이 줄이 아니면 밀린 기록이 영원히 재시도되지 않는 것을 알 방법이 없다).

**AGENT** — 이 머신 Agent의 마지막 실행, 마지막 수집 날짜, 미수집 날짜,
outbox/sent 개수, 거부된 Signal 수, 전달 정합성, Agent Lock 상태. 전달 정합성은
세 값이다: `OK`, `UNDELIVERED`(sync 폴더에 도착하지 않은 Event가 있다),
`UNKNOWN`(`sent/`에 읽을 수 없는 기록이 있어 그 Event의 도착 여부를 판단할 수
없다).

마지막에 **ATTENTION** 절이 나온다. 비어 있으면 지금 사람이 할 일은 없다.

> ATTENTION에 뜨는 것은 **사람이 지금 할 일이 있는 것**뿐이다. 다음 실행이
> 알아서 처리하는 것(RETRYABLE), 그리고 오늘 어떤 조치로도 지울 수 없는 것은
> 일부러 뺀다 — 지워지지 않는 경보는 그 절을 대충 넘기도록 훈련시킨다.

종료 코드: `0` 정상, `1` 설정 오류, `3` 확인이 필요한 항목 있음.

**이 저장소의 도구는 명령줄 인자를 받지 않는다.** 설정은 전부 환경변수다.
`--dry-run`이나 `--help` 같은 것을 붙이면 `1 설정 오류`로 거부하면서 그 도구가
읽는 변수 이름을 알려준다. C47 이전에는 인자를 **조용히 무시**했고, 그래서
`python run_company_ops.py --dry-run`이 진짜 push와 진짜 Notion 쓰기를 하는
운영 실행이었다.

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

### 6a. Desktop 4 로그에서 실패를 찾는 법

Runner는 실패해도 대부분 **멈추지 않는다** — Notion이 죽어도, Monthly가
깨져도, Dashboard가 안 되어도 Company History는 계속 기록된다(README RULE 5,
RULE 9). 그래서 "돌긴 돌았는데 뭔가 안 됐다"를 알아내려면 로그를 봐야 한다.
Runner가 Task Scheduler 뒤에서 돌면 stdout은 아무도 보지 않으므로, 실패는
전부 아래 두 파일에 남는다.

```powershell
Select-String FAILED runtime\logs\daily_late_update.log
Select-String "DASHBOARD|REASON" runtime\logs\notion_sync.log
```

`daily_late_update.log` — Daily / Monthly 계열

| 줄 | 뜻 | 할 일 |
|---|---|---|
| `LATE_UPDATE UPDATED_LATE_EVENT <날짜>` | 늦게 온 Event를 그날 History에 추가함 | 없음(정상) |
| `LATE_UPDATE FAILED <날짜>` | 그 날짜 History 갱신 실패 | 원인 확인. Candidate는 남아 있음 |
| `LATE_UPDATE SCHEDULER_FAILED date=<날짜>` | **그 날짜에서 Daily Close가 멈춤** | 그 날짜와 이후 Daily가 아직 없음. 원인 해결 후 재실행하면 이어서 생성 |
| `LATE_UPDATE MONTHLY_FAILED <달>` | 월간 통합 실패 | 다음 실행이 재시도 |

`notion_sync.log` — Notion 계열

| 줄 | 뜻 | 할 일 |
|---|---|---|
| `NOTION_RESULT NOTION_CREATED/UPDATED` | 정상 | 없음 |
| `NOTION_RESULT NOTION_RETRY_REQUIRED ... REASON <이유>` | 실패, 큐에 남음 | **REASON을 볼 것** — 아래 참고 |
| `NOTION_RESULT NOTION_FAILED ... REASON <이유>` | 예기치 못한 실패 | REASON 확인 |
| `DASHBOARD DRAIN_PENDING drained=N still_pending=M` | 밀린 Dashboard 기록 처리 결과 | `still_pending`이 계속 늘면 확인 |
| `DASHBOARD FAILED ...` | Dashboard 기록 실패 | Company History에는 영향 없음 |

`REASON`이 중요한 이유: `NOTION_RETRY_REQUIRED`는 **저절로 나을 실패와 영원히
안 나을 실패를 같은 단어로 보고한다.** `503`이면 기다리면 되고, `400`이면
기다려도 소용없다 — 매 실행 재전송만 반복한다. 그 둘을 가르는 문장이 REASON이다.

Company History 자체가 위험한 경우는 이 표에 없다. Notion·Dashboard·Monthly는
전부 History가 이미 디스크에 저장된 **뒤에** 일어나는 단계다.

---

### 6a-2. 지난 실행이 **중단**됐다면

`ops_status.py`의 LAST RUN에 `STEP_ABORTED`가 있거나 `시작되지 못한 단계`가
보이면, 그 실행은 도중에 죽은 것이다. **대부분은 다음 실행이 알아서 이어받는다.**
어느 단계에서 죽었느냐에 따라 남는 것이 다르고, 아래는 전부 실측한 결과다.

| 중단된 단계 | 남는 것 | 다음 실행이 |
|---|---|---|
| `transport` / `collector` | 아직 `incoming/`·`transport/`에 있는 파일 | **이어받는다** — 파일이 그대로 있으므로 |
| `notion_sync` | Retry Queue에 남은 Event | **이어받는다** — 4a가 큐부터 처리한다 |
| `history_filter` | **소비됐지만 Candidate가 없는 Event** | **이어받지 못한다** ↓ |
| `daily` | Candidate는 있고 Daily 파일이 없음 | **이어받는다** — Candidate가 durable하다 |
| `late_update` / `monthly` | (실행을 중단시키지 않는 단계다 — 실패해도 다음 단계로 간다) | 해당 없음 |
| `backup` | Company History는 있고 push가 안 됨 | **이어받는다** — 다음 실행이 같은 commit을 다시 push한다 |
| `dashboard` | (중단시키지 않는다) Notion에 못 쓴 기록이 `dashboard_pending.json`에 | **이어받는다** — 다음 실행이 큐부터 비운다 |
| Lock을 쥔 채 crash | pid가 죽은 lock 파일 | **이어받는다** — 다음 실행이 인수한다 |

**하나만 자동 복구되지 않는다.** `history_filter`에서 죽으면 Collector가 이미
그 Event를 소비했고(파일이 `processed/`로 옮겨졌고 id가 seen store에 저장됐다)
어떤 실행도 다시 보지 않는다 — `BACKLOG.md` A-20. 그래서 그 경우에만 **사람이
필요하고**, `ops_status.py`가 이름을 대 준다:

    ! 수집됐지만 History에 들어가지 못한 Event 1건: EVT-XXX — 재실행으로
      복구되지 않는다(BACKLOG A-20). 사람이 확인해야 한다

파일 자체가 읽히지 않게 된 경우에는 다른 줄이 뜬다 — 그때는 "잃었다"가 아니라
"판단할 수 없다"이고, 그 구분은 일부러 유지한다:

    ! processed에 읽을 수 없는 Event 1건: EVT-XXX.json
      — History 반영 여부를 판단할 수 없다

> **다음 실행이 성공하면 중단 기록은 사라진다.** Run Manifest는
> `runtime/runs/last_run.json` **한 파일**이고 이름 그대로 마지막 실행만
> 담는다. 그래서 `종합 상태: SUCCESS`인데 위 ATTENTION 줄이 떠 있는 조합이
> 나올 수 있다 — 모순이 아니라, **지난 실행에서 잃은 것이 아직 그대로**라는
> 뜻이다. ATTENTION 쪽이 더 오래 사는 신호다.

---

### 6a-3. Backup에서 **복구한 직후** 첫 실행

`docs/10` §45대로 Desktop 4를 새로 세우고 GitHub Backup에서 Company History를
되돌린 상태다. 되돌아오는 것과 되돌아오지 않는 것이 정해져 있다(`docs/08` §26):

| | Backup에 있나 | 복구 후 |
|---|---|---|
| `daily/`, `monthly/` | ✅ | 그대로 돌아온다 (byte 단위로 동일) |
| Raw Event, History Candidate | ❌ | 없다 — 이 Desktop에서 다시 만들 수 없다 |
| `runtime/state/` 전부 | ❌ | 없다 — **watermark가 사라졌다** |

즉 첫 실행은 **완성된 Company History를 손에 들고, 그것을 쓴 기억이 전혀 없는**
상태에서 시작한다. 위험은 명백하다 — 그 날짜들을 "아직 안 썼다"고 판단하고
(이제는 없는) Candidate로 다시 만들면, 실제 History가 빈 날로 덮이고 그것이
**유일한 사본인 원격으로 push된다.**

**그렇게 되지 않는다. 실측이다.** 3일치 History를 만들고 → 디스크 전체를 잃고
→ 원격에서 clone하고 → 한 번 실행했다:

    복구된 파일        2026-08-01 … 08-04   그대로, 한 바이트도 안 바뀜
    실제로 쓴 파일     2026-08-05           (복구 이후의 새 날짜 하나)
    watermark          2026-08-05 로 정상 전진
    원격               복구된 내용 그대로 유지
    manifest           SUCCESS / exit 0

Scheduler가 날짜마다 **파일이 이미 있는지 먼저 보고**(`is_file()`), 있으면 쓰지
않고 "닫힘"으로만 표시하기 때문이다(`docs/07` §28과 같은 장치).

**읽을 때 주의할 것 하나.** 그 실행의 보고는 이렇게 나뉜다:

    Daily History (Scheduler): COMPLETED, generated=1 (2026-08-05) reused=4 (2026-08-01, 2026-08-02, 2026-08-03, 2026-08-04)

각 항목은 `개수 (날짜들)` 꼴이다. 날짜가 10일을 넘으면 뒤에 `외 N일`이 붙는다 —
잘렸다는 사실을 숨기지 않기 위해서다. 비교해야 하는 것은 **앞의 숫자**다.

`generated`는 **이번 실행이 쓴 날**, `reused`는 **이미 있어서 그대로 둔 날**이다.
복구 직후에는 `reused`가 크고 `generated`가 작은 것이 정상이며, 그 반대라면
— 복구한 날짜를 다시 만들고 있다는 뜻이므로 — **즉시 멈추고 원격을 확인해야
한다.** (C39 전에는 둘이 한 숫자였고, 복구 직후 실행이 "5일 생성"이라고 보고했다.
없는 Candidate로 만들었을 리 없는 5일이다.)

---

### 6d. 브라우저에서 보기 (Control Tower Dashboard)

```powershell
python dashboard_server.py
```

→ **http://127.0.0.1:8765/** 를 연다. 종료는 Ctrl+C.

`ops_status.py`와 **같은 사실을 같은 순서로** 보여준다. 새로 계산하는 것은
없다 — ATTENTION 목록은 `ops_status.py`가 만드는 그 목록이고(비어 있으면
exit 0에 해당), 운영 블록 다섯은 그 화면의 출력을 그대로 싣는다. Control Tower만
패널·KPI로 다시 그리고, 터미널 텍스트도 접힌 채로 같이 둔다 — 둘이 같은 말을
하는지 사람이 직접 대조하라고.

`ops_status.py`와 똑같이 **아무것도 쓰지 않고 lock도 잡지 않는다.** GET만
답하고 나머지는 405다. Runner나 Agent가 도는 중에 열어도 안전하다.

읽는 법:

| 보이는 것 | 뜻 |
|---|---|
| 우상단 빨간 배지 | ATTENTION이 있다 (= `ops_status.py` exit 3) |
| `증거가 하나도 없다` | 0은 "일이 없었다"가 아니라 **"셀 Event가 없다"** |
| `UNSOURCED` 점선 패널 | 비어 있는 것이 아니라 **물어볼 곳이 없다** |
| `해당 없음` (실선 패널) | 원천은 있고, 이 기간에 하나도 없었다 |
| `불완전` / `확인 못 함` | 화면의 숫자가 전부가 아니다. 사유가 배너에 적혀 있다 |
| 각 행의 `증거 N건` | 펼치면 그 숫자가 나온 Event ID와 파일 이름 |
| `3시간 12분 전 기준` (노랑) | **이 화면은 스스로 갱신하지 않는다.** 새로고침은 사람이 |
| `63ms에 생성` | 페이지를 만드는 데 걸린 시간. Event 수에 비례해 늘어난다 |

`127.0.0.1`에만 바인딩하며 바꿀 수 없고, `Host` 헤더가 loopback 이름이 아니면 **403**이다 (DNS rebinding — 바인딩은 다른 머신의 패킷만 막고, 이 머신의 브라우저는 못 막는다).
이 화면에는 다른 Desktop에서 사람이
입력한 `blocker` 문장과 `project_id`가 실린다. 다른 머신에서 보려면 인증·TLS가
필요한 **배포 결정**이고, 그래서 `--host` 플래그가 없다.

기간을 좁히려면 화면 위쪽 `기간` 칸에 날짜를 넣거나 URL로 직접 준다.

```
http://127.0.0.1:8765/?since=2026-08-01&until=2026-08-07
```

Event의 **작업일** 기준으로 자른다(파일이 도착한 날이 아니라 — docs/06 §12).
잘못된 날짜·거꾸로 된 기간·모르는 조건은 **거절(400)**한다. 조용히 전체 기간을
보여주면 운영자는 한 주를 본다고 믿으면서 전체를 보게 된다.
**이 기간은 위쪽 KPI·패널에만 적용된다** — 아래 `운영 상태` 블록은
`ops_status.py`의 출력이고 기간 개념이 없다(언제나 현재 상태).

포트가 이미 쓰이고 있으면 `COMPANY_OPS_DASHBOARD_PORT`로 바꾼다. 명령줄 인자는
받지 않는다(다른 도구와 같다).

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
