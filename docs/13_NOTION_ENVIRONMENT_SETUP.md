# D:\DOJOONPASS_COMPANY_OPS\docs\13_NOTION_ENVIRONMENT_SETUP.md

## DOJOONPASS Company Ops — Notion Environment Setup

---

## 1. 문서 정의

| 항목 | 내용 |
|---|---|
| 문서명 | Company Ops Notion Environment Setup |
| 프로젝트 | DOJOONPASS Company Ops |
| Owner | COO |
| 상위 문서 | `00_V1_DEVELOPMENT_SPEC.md` |
| 실행 기준 | `01_V1_IMPLEMENTATION_PLAN.md` |
| Notion 기준 | `04_NOTION_SYNC_SPEC.md` |
| 목적 | 실제 Notion Workspace를 `src/notion/`이 사용할 수 있는 상태로 준비하는 절차와, 준비 완료 후 수행할 검증 순서·Release Checklist를 정의한다 |
| 실행 위치 | Desktop 4 |
| 적용 버전 | V1 |

본 문서는 새로운 기능이나 정책을 정의하지 않는다. `src/notion/`이 이미 요구하는 환경변수와 `04_NOTION_SYNC_SPEC.md`가 이미 정의한 검증 항목을, 실제 운영 환경 구축 절차로 정리한 것이다.

---

## 2. 환경변수 목록

저장소 전체(`src/`와 루트 entrypoint 4개)에서 `os.environ` / `os.getenv`를 사용하는 곳을 기준으로 추출했다. **정본은 `.env.example`이다** — `tests/test_repository_hygiene.py`가 "코드가 읽는 모든 변수는 `.env.example`에 있어야 하고 그 역도 같다"를 검사하므로, 이 표와 `.env.example`이 어긋나면 `.env.example`이 이긴다.

| 변수 | 구분 | 사용처 | 설명 |
|---|---|---|---|
| `NOTION_API_TOKEN` | **필수(Notion)** | `src/notion/config.py` (`NotionConfig.from_env`) | Notion Integration의 Internal Integration Secret. 없거나 공백뿐이면 `NotionConfigError` — Notion Sync도 Operations Dashboard도 구성되지 않는다(오류가 아니라 건너뜀). |
| `NOTION_PROJECTS_DATABASE_ID` | **필수(Notion)** | `src/notion/config.py` | Notion Sync가 Create/Update할 PROJECTS Database의 ID. |
| `NOTION_OPS_RUNS_DATABASE_ID` | 선택 | `src/notion/config.py` | Operations Dashboard(CEO Decision ④)의 `OPS_RUNS` Database ID. **미설정이면 Dashboard 기록은 매 실행 SKIPPED_NOT_CONFIGURED다.** Notion Sync는 영향받지 않는다. 아래 §3-⑧ 참고. |
| `COMPANY_OPS_HISTORY_START_DATE` | **필수(Runner)** | `run_company_ops.py`, `ops_status.py` | Company History를 언제부터 기록할지. 절대 추측하지 않는다(docs/07 §50). **없으면 `run_company_ops.py`는 첫 줄에서 exit 1이며 아무 단계도 실행되지 않는다.** 형식 `YYYY-MM-DD`. |
| `COMPANY_OPS_PROFILE` | **필수(Agent)** | `src/reporter/profiles.py`, `run_agent.py` | 이 Desktop의 프로필(`DESKTOP_1`~`DESKTOP_4`). role은 따로 설정하지 않는다 — docs/02 §8의 source→role 표에서 나온다. |
| `COMPANY_OPS_AGENT_SYNC_FOLDER` | **필수(Agent)** | `run_agent.py`, `ops_status.py` | 이 Desktop이 Event를 쓰는 OneDrive Sync Folder. `ops_status.py`에서는 전달 정합성 확인에만 쓰이며, 없으면 "확인 불가"로 보고된다. **Agent에만 필수다** — 아래 §2.0.1 참고. |
| `COMPANY_OPS_AGENT_START_DATE` | **필수(Agent)** | `run_agent.py`, `ops_status.py` | 이 Desktop이 최초 실행에서 수집을 시작할 날짜. `COMPANY_OPS_HISTORY_START_DATE`와 분리돼 있다 — Desktop은 Company History보다 늦게 보고를 시작할 수 있다. |

`src/notion/transport.py`의 `NOTION_API_BASE_URL` / `NOTION_API_VERSION`은 환경변수가 아니라 코드 내 상수다.

### 2.0.1 OneDrive 없이 돌아가는 것과 돌아가지 않는 것 (C149)

`COMPANY_OPS_AGENT_SYNC_FOLDER`는 **Event를 다른 Desktop으로 보내는** 쪽에만
필수다. 표의 "필수(Agent)"는 그 뜻이며, Runner 쪽에는 필수가 아니다.

이 변수가 없거나 그 폴더가 죽어 있을 때 실제로 무엇이 되는지:

| | OneDrive 정상 | OneDrive 없음/죽음 |
|---|---|---|
| 이 Desktop의 Event 전달 | 됨 | **안 됨** (Agent가 보고한다) |
| Company History / Daily / Backup | 됨 | 됨 (이미 수집된 Event 기준) |
| Notion Sync / Dashboard | 됨 | 됨 |
| D+1 "어제 무엇이 바뀌었나" | Event + Git | **Git으로 답한다** |

마지막 줄이 C149에서 바뀐 것이다. 그전에는 Event가 유일한 원천이어서
"아무도 일하지 않은 날"과 "일했지만 전달이 안 된 날"이 화면에서 똑같이
조용했다. `src/delivery/git_activity.py`가 로컬 저장소를 직접 읽으므로,
이제 그 두 날은 다르게 보인다.

Git은 회사의 Source of Truth가 아니다 — Project·Blocker·Decision은 여전히
Event가 든다. 자세한 것은 `docs/15_D1_COMPANY_UPDATE_SPEC.md`.

### 2.1 `.env`는 자동으로 읽히지 않는다

이 저장소에는 `.env` 로더가 없다(`python-dotenv` 미사용). `src/notion/config.py`는 `os.environ`만 읽고, 루트 entrypoint 넷도 마찬가지다. 따라서 **`.env`에 값을 채워 넣는 것만으로는 아무 entrypoint도 그 값을 보지 못한다.** 셋 중 하나를 해야 한다:

1. 실행 셸에서 export한다.
2. 실행 스크립트가 `.env`를 직접 로드한다.
3. `scripts/install_agent_task.ps1`을 쓴다 — 이 스크립트는 `COMPANY_OPS_PROFILE` / `COMPANY_OPS_AGENT_SYNC_FOLDER` / `COMPANY_OPS_AGENT_START_DATE` 세 개를 **User 환경변수로 영구 등록한다.** 예약 작업(Task Scheduler)은 대화형 셸의 변수를 상속하지 않으므로 이 경로가 필요하다.

**1번(셸 export)은 예약 실행에는 통하지 않는다.** 위 3번이 그 이유를 Agent 변수에 대해 이미 적고 있는데, 같은 제약이 `NOTION_*`에도 그대로 적용된다 — `install_runner_task.ps1`과 `install_publish_task.ps1`은 Notion 자격증명을 **일부러 받지 않는다**(비밀을 명령줄·프로세스 목록·PowerShell 기록에 남기지 않기 위해서다). 그래서 그 둘은 사람이 직접 User 환경변수로 넣어야 하고, 셸에서 export만 하면 다음이 일어난다:

* 손으로 실행하면 Notion Sync가 동작한다.
* 예약 실행은 **아무것도 상속하지 않아** Notion 단계를 계속 `미설정`으로 건너뛴다.
* 그런데도 실행은 **exit 0**으로 끝난다 — Notion은 History critical path 밖이기 때문이다(README RULE 5). 즉 성공처럼 보이는 실패다.

User 환경변수로 넣는다(PowerShell, 값 부분만 바꾼다):

```powershell
[Environment]::SetEnvironmentVariable('NOTION_API_TOKEN', '<token>', 'User')
[Environment]::SetEnvironmentVariable('NOTION_PROJECTS_DATABASE_ID', '<database id>', 'User')
# 선택 (Operations Dashboard)
[Environment]::SetEnvironmentVariable('NOTION_OPS_RUNS_DATABASE_ID', '<database id>', 'User')
```

넣은 뒤에는 **새 셸을 연다** — 이미 열려 있는 셸은 예전 환경 블록을 그대로 들고 있다. 아래 확인 명령의 `user=True`가 예약 실행이 실제로 보게 될 값이다.

확인 명령(PowerShell, 값은 찍지 않고 설정 여부만):

```powershell
foreach ($n in 'NOTION_API_TOKEN','NOTION_PROJECTS_DATABASE_ID','NOTION_OPS_RUNS_DATABASE_ID',
               'COMPANY_OPS_HISTORY_START_DATE','COMPANY_OPS_PROFILE',
               'COMPANY_OPS_AGENT_SYNC_FOLDER','COMPANY_OPS_AGENT_START_DATE') {
  "{0,-32} process={1} user={2} machine={3}" -f $n,
    [bool][Environment]::GetEnvironmentVariable($n,'Process'),
    [bool][Environment]::GetEnvironmentVariable($n,'User'),
    [bool][Environment]::GetEnvironmentVariable($n,'Machine')
}
```

---

## 3. Notion Workspace 구축 절차

실제 Token 발급과 실제 Workspace 생성은 이 Sprint의 범위가 아니다 — 아래는 사용자(운영자)가 Notion에서 직접 수행할 절차의 기록이다.

### ① Integration 생성

1. https://www.notion.so/my-integrations 접속(Notion 계정 로그인 상태).
2. "New integration" 선택.
3. 이름 예시: `DOJOONPASS Company Ops` (`04_NOTION_SYNC_SPEC.md` §1이 가리키는 시스템임을 알 수 있게).
4. Associated workspace: 도준패스 운영에 사용할 Workspace 선택.
5. Type: Internal Integration.

### ② API Token 발급

1. Integration 생성 후 "Secrets" 탭에서 "Internal Integration Secret" 확인.
2. 이 값이 `NOTION_API_TOKEN`이다.
3. 이 문서·저장소·채팅 등 어디에도 실제 값을 기록하지 않는다(§40 Secret 관리 원칙).

### ③ PROJECTS Database 생성

1. Notion Workspace 내 페이지에서 "+ New" → "Table" (또는 "Database — Full page")로 Database 생성.
2. 이름: `PROJECTS` (`04_NOTION_SYNC_SPEC.md` §4).
3. §8 기본 Property를 동일한 이름/타입으로 생성한다:

| Property | Type |
|---|---|
| Project | Title |
| Project ID | Text |
| Owner | Select |
| Source | Select |
| Status | Status 또는 Select |
| Current Milestone | Text |
| Blocker | Text |
| Last Updated | Date |
| Completed Date | Date |
| Last Event ID | Text |
| Last Event Type | Select |

`Status`/`Owner`/`Source`/`Last Event Type`은 Select 옵션이므로, `02_EVENT_SCHEMA.md`의 값 집합(STATUSES, ROLES, SOURCES, EVENT_TYPES)과 `src/notion/properties.py`의 `ROLE_DISPLAY_NAMES`에 맞춰 옵션을 미리 만들어 두면 §66 검증 시 매끄럽다. 다만 Notion Select는 없는 옵션이 들어오면 API가 자동으로 새 옵션을 만들 수 있어(Integration 권한에 따라 다름) 필수 선행 작업은 아니다.

### ④ Integration 권한 부여

1. PROJECTS Database 페이지 우측 상단 "..." → "Connections"(또는 "Add connections") → ①에서 만든 Integration 선택.
2. `04_NOTION_SYNC_SPEC.md` §42 원칙에 따라 필요한 최소 권한만 부여한다: PROJECTS Database에 대한 읽기 + 생성/수정. Workspace 전체 접근 권한은 주지 않는다.

### ⑤ Database ID 확인

1. PROJECTS Database를 브라우저에서 열었을 때의 URL에서 확인한다:
   `https://www.notion.so/{workspace}/{database_id}?v={view_id}`
2. `database_id`는 32자리 영숫자(하이픈 없는 UUID 형태) 문자열이다. 이 값이 `NOTION_PROJECTS_DATABASE_ID`다.

### ⑥ `.env.example` 작성

이번 Sprint에서 저장소 루트의 `.env.example`을 아래와 같이 준비했다(§4 출력 항목 2 참고). Placeholder만 있고 실제 값은 없다.

### ⑦ `.env` 설정

1. 저장소 루트에서 `.env.example`을 `.env`로 복사한다.
2. `.env`에 ②의 Token, ⑤의 Database ID를 채운다.
3. `.env`는 `.gitignore`에 이미 포함되어 있어(`\.env` 패턴) Git에 잡히지 않는다 — 별도 조치 불필요.
4. 이 프로젝트는 `.env` 자동 로딩 코드가 없다 — §2.1 참고. `.env`를 채우는 것과 entrypoint가 값을 보는 것은 다른 일이다.

### ⑧ Operations Dashboard (선택, CEO Decision ④)

Notion Sync와 **독립적이고 완전히 선택적**이다. 설정하지 않으면 `record_run()`이 매 실행 `SKIPPED_NOT_CONFIGURED`를 돌려주고 나머지는 전부 평소대로 동작한다.

현재 상태를 먼저 진단한다 — `init_notion.py`가 마지막에 출력하는 "Operations Dashboard 준비 상태" 절이 그것이며, **읽기 전용이다**(Database를 만들지 않는다).

```
readiness = READY                  이 Page 밑에 만들 수 있다
            NEEDS_PARENT_CHOICE    공유된 Page는 있으나 어느 것을 쓸지는 운영자 결정
            NEEDS_SHARED_PAGE      integration에 공유된 Page가 하나도 없다
```

> **먼저 readiness부터 읽는다. `ALREADY_CREATED`이면 이 절은 건너뛴다.**
> `NOTION_OPS_RUNS_DATABASE_ID`가 설정돼 있으면 `init_notion.py`는 그 Database를
> 실제로 조회한 뒤 답한다(C114):
>
> | readiness | 뜻 | 할 일 |
> |---|---|---|
> | `ALREADY_CREATED` | OPS_RUNS가 있고 변수가 그것을 가리킨다 | **아래 2번을 실행하지 않는다.** 열 누락이 함께 보고되면 ⑧-4(`bootstrap_dashboard_properties()`)만 |
> | `CONFIGURED_BUT_UNREACHABLE` | 변수는 설정됐는데 그 Database가 응답하지 않는다 | id가 맞는지, 공유가 살아 있는지 확인. **새로 만들지 않는다** — 그러면 둘이 된다 |
> | `CONFIGURED_TO_THE_WRONG_DATABASE` | 응답하지만 OPS_RUNS가 아니다 (Title이 `Run ID`도 `Name`도 아니다 — 대개 PROJECTS) | 변수를 올바른 id로 고친다. **⑧-4를 실행하지 않는다** — 아래 경고 참고 |
> | `NEEDS_*` / `READY` | 아직 없다 | 아래 순서대로 |
>
> `CONFIGURED_TO_THE_WRONG_DATABASE` 줄이 필요한 이유는 §3-⑧-4가 이미 적어 둔 것이다: `bootstrap_dashboard_properties()`는
> **자기가 어느 Database에 묶여 있는지 확인할 방법이 없다.** `NOTION_OPS_RUNS_DATABASE_ID`에
> PROJECTS의 id가 들어가면(셸 프로필에서 변수 하나를 바꿔 쓰면 그만이고, `NotionConfig`는
> 두 id를 구별하지 못한다) 그 Database는 멀쩡히 응답하고 22개 열이 전부 "없음"으로 나온다.
> 그 상태에서 ⑧-4를 돌리면 **살아 있는 PROJECTS에 OPS_RUNS의 22개 열이 추가된다.**
> C114의 첫 수정이 정확히 그 문장을 출력했고(실 API로 확인), 그래서 진단이 Title을 보고
> 먼저 거른다.
>
> 이 표가 존재하는 이유는 아래 §4 상태표에 적혀 있다: 그 표가 "미완료"라고 말하는
> 동안 Workspace에는 이미 OPS_RUNS가 있었고, 2번을 따랐다면 **삭제할 수 없는 중복**이
> 생겼을 것이다. 문서는 낡지만 진단은 실행할 때마다 다시 측정한다.

`NEEDS_SHARED_PAGE`가 나오는 가장 흔한 이유는, PROJECTS Database가 **Workspace 루트**에 있고(=부모가 Page가 아니고) integration에는 그 Database만 공유돼 있는 경우다. Notion API는 Workspace 루트에 Database를 만들 수 없고, 이 저장소는 Page를 만들지 않는다(운영자 지시: "새로운 Page 생성 금지"). 따라서:

1. **Page 공유** — Notion에서 Company Ops용 Page 하나를 열고 Share → Connections에서 이 integration을 추가한다. 어느 Page를 쓸지는 운영자 결정이라 코드가 고르지 않는다.
2. **OPS_RUNS 생성** — `src/notion/dashboard.py`의 `bootstrap_dashboard_databases(client, parent_page_id=..., only=["OPS_RUNS"])`가 그 일을 한다. **어떤 entrypoint도 이 함수를 호출하지 않는다** — 실 Workspace에 Database를 만드는 일을 설정 명령이 조용히 하지 않게 하려는 의도적 고정이며, `tests/test_notion_dashboard.py::test_the_setup_cli_does_not_create_anything_from_the_diagnosis`가 검사한다. 운영자가 직접 호출하거나, Notion UI에서 `OPS_RUNS` Database를 손으로 만든다.

   직접 호출할 때 쓸 명령(저장소 루트에서, ⑧-1의 `page_id`를 넣어서). **이 명령은 실제 Notion Workspace에 Database를 만든다 — 한 번만 실행하고, 출력된 id를 반드시 기록한다.** 두 번 실행하면 같은 이름의 Database가 하나 더 생기고, 어느 쪽이 어느 쪽인지 코드가 구분하지 못한다:

   ```powershell
   python -c @'
   import os, sys
   sys.path.insert(0, "src")
   from notion import NotionClient, NotionConfig, RealNotionTransport, bootstrap_dashboard_databases
   c = NotionConfig.from_env()
   client = NotionClient(transport=RealNotionTransport(api_token=c.api_token),
                         database_id=c.projects_database_id)
   result = bootstrap_dashboard_databases(client,
                                          parent_page_id="<⑧-1에서 고른 page_id>")
   print("NOTION_OPS_RUNS_DATABASE_ID=" + result.database_id("OPS_RUNS"))
   '@
   ```

   중간에 실패하면 `DashboardBootstrapPartialError`가 **그때까지 만들어진 id를 메시지에 담아** 올라온다. 그 id들은 Workspace에 실제로 존재하므로 기록해 두고, 재시도는 `only=`에 **남은 이름만** 넘긴다.
3. **`NOTION_OPS_RUNS_DATABASE_ID` 설정** — 위에서 얻은 id. §2.1대로 entrypoint가 실제로 보게 해야 한다.

`OPS_RUNS`의 Property 정의는 `src/notion/dashboard.py`의 `DASHBOARD_DATABASES["OPS_RUNS"]`가 정본이다. 손으로 만들 때는 이름과 타입을 그대로 맞춘다 — 열이 많아 옮겨 적기보다 **출력해서 보고 만드는 편이 안전하다.** 이 문서에 목록을 복사하지 않는 이유도 같다(정본이 둘이 되면 어긋난다). 개수도 적지 않는다: C31 13열 → C32 15열 → C33 17열 → C37 18열 → C42 20열 → C47 22열로 실제로 자라 왔고, 문서에 박아 둔 숫자는 그때마다 조용히 틀렸다 — **이 줄 자신도 C47 뒤로 한 Sprint 동안 틀려 있었다**(C48에서 발견). 이제 `OpsRunsColumnHistoryIsCurrentTests`가 이 줄의 마지막 숫자를 스키마와 대조한다:

```powershell
python -c "import sys; sys.path.insert(0,'src'); from notion.dashboard import DASHBOARD_DATABASES, OPS_RUNS; [print(f'{n:<20} {next(iter(t))}') for n, t in DASHBOARD_DATABASES[OPS_RUNS].items()]"
```

Notion UI에서의 타입 대응: `title`=Title, `date`=Date, `number`=Number, `select`=Select, `rich_text`=Text — 하나라도 없으면 Notion이 400으로 거절하고, 그 실행의 기록은 `runtime/state/dashboard_pending.json`에 쌓이며 `notion_sync.log`에 `DASHBOARD DRAIN_PENDING … REASON <Notion의 설명>`이 남는다(데이터는 잃지 않는다).

#### ⑧-4 이미 만든 Database에 열이 모자랄 때

위 400은 손으로 만들다 빠뜨렸을 때만 나오는 게 아니다. **`OPS_RUNS` 스키마는 자라 왔다** — C31까지 13열, C32에서 15열(`Transport Blocked`, `Notion Skipped`), C33에서 17열(`Notion Unreadable`, `Notion Queued`), C37에서 18열(`Failed Steps`), C42에서 20열(`Reused Days`, `Deleted Files`), C47에서 22열(`Desktops Reporting`, `Role Mismatches`). ⑧-2를 C31 시점에 실행해 둔 Database는 지금 코드가 쓰는 열을 갖고 있지 않고, 그 상태에서는 **모든 실행이 400으로 거절된다.** 실패는 안전하지만(위 문단대로 큐에 쌓이고 이유가 남는다) 빠져나올 길이 없었다.

`bootstrap_dashboard_properties(client)`가 그 길이다. **없는 Property만 추가하고 기존 Property는 정의째 그대로 둔다**(옵션을 설정해 둔 Select도 안전하다). Title이 Notion 기본값 `Name`이면 `Run ID`로 rename한다 — Notion API는 두 번째 Title을 만들 수 없어서 rename이 유일한 방법이고, `notion.bootstrap`이 PROJECTS에 대해 이미 하던 예외를 그대로 쓴다.

`client`는 **`OPS_RUNS` Database id에 묶인 것**이어야 한다(PROJECTS가 아니다). 코드가 확인할 방법이 없으니 아래 명령의 `database_id`를 반드시 확인하고 실행한다:

```powershell
python -c @'
import os, sys
sys.path.insert(0, "src")
from notion import NotionClient, NotionConfig, RealNotionTransport, bootstrap_dashboard_properties, format_report
c = NotionConfig.from_env()
client = NotionClient(transport=RealNotionTransport(api_token=c.api_token),
                      database_id=os.environ["NOTION_OPS_RUNS_DATABASE_ID"])
print(format_report(bootstrap_dashboard_properties(client)))
'@
```

두 번 실행해도 안전하다(두 번째는 전부 `EXISTS`/`SKIPPED`). 실행 후 `dashboard_pending.json`에 쌓여 있던 행은 **다음 Runner 실행이 자동으로 밀어 넣는다** — 큐를 손으로 건드릴 필요는 없다.

`bootstrap_dashboard_databases()`는 다섯 개(`OPS_RUNS`/`OPS_BACKUP`/`OPS_NOTION_SYNC`/`OPS_RISK`/`OPS_READINESS`)의 스키마를 갖고 있지만, **인자 없이 부르면 `OPS_RUNS` 하나만 만든다.** `docs/14_RUN_CONTRACT.md` §1이 Operational Projection을 "Notion (PROJECTS / OPS_RUNS)"로 고정하고 있고, 나머지 넷은 그 모델에 없으며 어떤 코드도 쓰지 않기 때문이다(BACKLOG A-16 / C33 §2).

넷을 굳이 만들려면 `only=[...]`로 **명시적으로** 요청해야 한다. 만들면 영구히 비어 있고, 이 모듈에는 삭제 경로가 없다(의도된 설계) — Notion UI에서 직접 지워야 한다.

---

### ⑨ Control Tower View 구성 (선택)

Database를 만드는 절차가 아니다. **이미 있는 두 Database(PROJECTS / OPS_RUNS) 위에
View를 얹는 절차**이며, 새 Database도 새 Property도 만들지 않는다 —
`docs/14_RUN_CONTRACT.md` §1이 Operational Projection을 그 둘로 고정하고,
Goal·Sprint·Task는 이 시스템에 원천이 없다(§⑨-3).

무엇을 보여줄지는 코드가 이미 정해 두었다. `src/controltower/dashboard.py`의
`build_dashboard()`가 만드는 **Dashboard Model**이 정본이고, 같은 모델을
`ops_status.py`의 CONTROL TOWER 블록이 화면에 그린다. 아래 View는 그 모델의 패널을
Notion 쪽 재료로 옮긴 것이며, 화면과 어긋나면 어긋난 쪽이 틀린 것이다.

패널 목록과 각 패널이 무엇에서 파생되는지는 코드에서 직접 뽑는다(여기에 옮겨 적으면
정본이 둘이 된다 — ⑧과 같은 이유):

```powershell
python -c @'
import sys
sys.path.insert(0, "src")
import businessdate
from controltower import build_company_rollup, build_dashboard
now = businessdate.now()   # KST — docs/06 §9. `datetime.now().astimezone()`
                           # reads the *machine's* zone and is what C135 removed.
model = build_dashboard(build_company_rollup(now=now), now=now)
for panel in model.panels:
    print(f"{panel.key:<15} {panel.status.value:<10} {panel.title}")
    print(f"                {panel.source or panel.note}")
'@
```

#### ⑨-1 PROJECTS Database 위의 View

| Dashboard Model 패널 | Notion View | 만드는 법 |
|---|---|---|
| `PROJECTS` (③) | Board | Group by **Status** |
| `PROJECTS` (③) | Timeline / Calendar | Date = **Last Updated**, 완료는 **Completed Date** |
| `TEAMS` (②) | Board | Group by **Owner** — 단, ⑨-5의 경고를 먼저 읽는다 |
| `DESKTOPS` (④) | Table | Group by **Source** — 같은 경고가 적용된다 |
| `RISKS` (⑤, 열린 Blocker) | Table | Filter **Blocker is not empty** |

> **stale View를 `days_silent`만으로 만들지 않는다.** `days_silent`는 그 Desktop의 가장 최근
> Event로부터의 날 수이고, **한 번도 보고한 적 없는 Desktop에는 값이 없다**(null). 그래서
> `days_silent >= 3` 필터는 가장 걱정스러운 경우를 **빠뜨린다.** 조건은
> `has_activity = false` **또는** `days_silent >= N`이다 — `ops_status.py`의 COMPANY 블록이
> 쓰는 규칙과 같다(`silent_for()`는 `None`을 포함한다). 임계값 자체(N)는 Control Tower가
> 정하지 않는다: 이 계층은 숫자만 싣고, 경보는 COMPANY 블록이 `SILENT_AFTER_DAYS`로 낸다.

`Blocker`는 사람이 쓴 텍스트이고 파이프라인은 그것을 **스스로 지우지 않는다**
(그 팀의 RESUMED / ISSUE_RESOLVED / COMPLETED만 지운다). 따라서
"Blocker is not empty" View는 Control Tower의 열린 Blocker 목록과 같은 집합이다.

#### ⑨-2 OPS_RUNS Database 위의 View

| 요청 항목 | 열 |
|---|---|
| Desktop / Agent / Runner (④) | `Desktops Reporting`, `Role Mismatches`, `Run At` |
| Delivery | `Transport Moved`, `Transport Blocked` |
| Daily / History | `Generated Days`, `Reused Days`, `Accepted`, `Duplicate`, `Rejected` |
| Backup / Recovery | `Backup Status`, `Deleted Files`, `Reused Days` |
| Notion Sync | `Notion Synced` / `Skipped` / `Retried` / `Unreadable` / `Queued` |
| 실패 (⑤) | `Failed Steps`, `Overall` |

이 중 **Control Tower가 파생하는 두 열**(`Desktops Reporting`, `Role Mismatches`)은
`src/controltower/projection.py`의 `OPS_RUNS_CONTROL_TOWER_COLUMNS`가 정본이다. 나머지 열은
파이프라인 각 단계가 스스로 센 값이고, 이 둘만 Dashboard Model에서 나온다 — 그래서 화면의
DESKTOPS 패널과 이 행은 **같은 fold의 같은 배열**이며 서로 다를 수 없다.

한 행이 실행 1회다. **행의 연속성을 완전성으로 읽으면 안 된다** — Dashboard 단계
앞에서 멈춘 실행은 행을 남기지 않는다(`runtime/runs/last_run.json`은 모든 종료
경로에 쓰인다). docs/14 §1이 Notion을 View로 못 박은 이유가 이것이다.

#### ⑨-3 만들 수 없는 View, 그리고 왜인지

| 요청 | 상태 |
|---|---|
| 전사 목표 / 목표별 진행률 / KPI target | **원천 없음** (`COMPANY_GOAL`, `TEAM_GOAL`) |
| 현재 Sprint / Sprint Board / Backlog 칸반 | **원천 없음** (`SPRINT`, `TASK`) |

Event Schema에도 Company Repository에도 이 계층이 없다. Notion에 적어 넣고 권위로
삼는 것은 docs/14 §1을 정면으로 깨는 일이므로, 원천을 Company Repository 산출물로
둘지 Event Schema 필드로 둘지가 먼저 정해져야 한다(BACKLOG). 그때까지 이 패널들은
**빈 View가 아니라 "원천 없음"으로** 남긴다 — 빈 View는 "아무 일도 없었다"로 읽히고,
그것은 사실이 아니다.

KPI 자체는 `METRICS` 패널에 있고 target만 없다. target은 Goal이기 때문이다.

#### ⑨-4 반드시 함께 보여야 하는 것 — Coverage

Control Tower의 모든 숫자에는 **"무엇에 대해"**가 붙는다. `processed/`는 Execution
Evidence이고 Backup 범위는 `daily/`·`monthly/`뿐이므로(docs/08 §26), 원격에서 복원한 머신은
**Company History는 전부, Event는 하나도** 갖지 않는다. 그 상태에서 일곱 패널은 전부 0을
보고하며 그것은 **사실이다** — 다만 조용한 한 주와 구분되지 않는다.

`DashboardModel.coverage`가 그 구분이다.

| 필드 | 뜻 |
|---|---|
| `evidence_from` / `evidence_to` | 이 숫자들이 덮는 Event 날짜 범위 |
| `unreadable` | 디렉터리에 있으나 쓸 수 없었던 파일 수 |
| `history_uncovered_from` | Company History가 일을 기록하는데 증거가 없는 가장 이른 날 |
| `complete` | 위 둘이 모두 비어 있는가 |

**경보가 아니라 단서다** — 그 Event를 되돌리는 조치는 없고, 지울 수 없는 경보는 이 프로젝트가
계속 없애 온 것이다. Notion View를 만들 때는 이 값을 **패널 옆 텍스트 블록**으로 두고, 숫자가
0인 화면을 그것 없이 보여주지 않는다. `ops_status.py`의 CONTROL TOWER 블록이 같은 값을
`증거 범위 밖 :` 줄로 찍는다.

#### ⑨-5 경고 — Blocked를 `Owner`로 묶지 않는다

PROJECTS 행의 `Owner`/`Source`는 **그 Project를 처음 만든 Event**의 값이고, 이후
Update가 덮어쓰지 않는다(`build_update_properties()`의 docstring이 근거를 적어 둔다).
`Blocker`는 반대로 보고한 팀이 매번 덮어쓴다. 그래서 두 팀이 함께 쓰는 Project에서는
행 하나가 서로 다른 두 팀을 가리킬 수 있다. 실측:

```
E1  PAY  CMO / DESKTOP_2          STARTED
E2  PAY  CTO_BACKEND / DESKTOP_1  BLOCKED "vendor key missing"

PROJECTS 행   Owner=CMO  Source=DESKTOP_2  Blocker="vendor key missing"
Control Tower risk.team=CTO_BACKEND
```

"Blocked를 Owner로 group by"한 View는 이 Blocker를 CMO에게 보낸다. 막고 있는 팀은
CTO Backend다. **팀별 Blocker는 `ops_status.py`의 CONTROL TOWER 블록(과 같은 모델의 RISKS 패널)에서 본다** —
거기서는 Blocker를 선언한 Event의 `role`로 귀속한다(C48). 행에 `Blocker Owner`를
더하는 것은 PROJECTS 스키마 변경이고, `Owner`를 매 Update마다 덮어쓰는 것은 위
docstring이 근거 없다고 적은 일이라, 둘 다 승인이 필요하다(BACKLOG).

단일 Desktop Project에는 이 문제가 없다 — `Owner`가 곧 유일한 팀이다.

---

## 4. Workspace 준비 완료 후 검증 순서

`.env`가 ⑦까지 채워진 뒤, 다음 순서로 검증한다(`04_NOTION_SYNC_SPEC.md` §66 Phase 3 완료 기준을 실행 순서로 나열한 것 — 새 기준을 추가하지 않는다).

```
1. Health Check
   NotionClient.health_check() — Authentication + Database 접근 성공 확인 (§66-1, §66-2)
        ↓
2. Database Access
   PROJECTS Database에서 실제 project_id로 조회(find_project) — 빈 결과도 정상 (§66-4)
        ↓
3. ExecutionPlanSync(Create)
   존재하지 않는 project_id로 STARTED Event sync -> NOTION_CREATED,
   Notion Workspace에서 새 Row 육안 확인 (§66-3, §57)
        ↓
4. ExecutionPlanSync(Update)
   같은 project_id로 BLOCKED/RESUMED/MILESTONE_COMPLETED/COMPLETED Event
   순서대로 sync -> NOTION_UPDATED, Notion Workspace에서 각 필드 반영 확인
   (§66-6~9, §58~61)
        ↓
5. Failure Recovery
   Token을 일부러 잘못된 값으로 바꾸거나 Database ID를 잘못 지정해
   NotionAPIError를 실제로 발생시키고, NOTION_RETRY_REQUIRED로 귀결되며
   Runner의 History/Daily/Backup 단계가 계속 진행되는지 확인 (§66-12, 구현
   범위는 이미 Mock으로 검증됨 — 실제 API 오류로 동일 동작 재확인)
        ↓
6. Acceptance Criteria
   §66 13개 항목 전체를 실제 Workspace 기준으로 체크리스트 형태로 재확인
   (§13 Secret이 Repository에 없음 포함)
```

이 순서는 앞선 Sprint(Notion E2E Validation, Phase 3)가 시도했던 항목과 동일하며, `NOTION_API_TOKEN`/`NOTION_PROJECTS_DATABASE_ID`가 설정되는 즉시 이어서 수행할 수 있다.

---

## 5. Release Checklist

상태는 **2026-08-17 (C32) 읽기 전용 실측** 기준이다. 측정에 쓴 경로는 `client.health_check()` + `diagnose_dashboard_bootstrap()` + `client.get_database_schema()` 세 개뿐이며, 셋 다 읽기만 한다 — `init_notion.py`는 PROJECTS에 Property를 **생성**하므로 이 측정에는 쓰지 않았다.

| 구분 | 항목 | 상태 |
|---|---|---|
| **Mock** | Mock Test 1~8 (`tests/test_notion_sync.py`, §57~64) | ✅ PASS |
| **Mock** | NotionClient Health Check / Project Lookup (`tests/test_notion_client.py`) | ✅ PASS |
| **Runtime** | Runner ↔ ExecutionPlanSync 연결, ACCEPTED만 Sync (`tests/test_runner_notion_integration.py`) | ✅ PASS |
| **Runtime** | Notion 실패 시 History/Daily/Backup 계속 진행 (Mock 기준) | ✅ PASS |
| **Runtime** | 전체 회귀 테스트 | ✅ PASS — 숫자는 여기 적지 않는다(측정: `python -m pytest tests -q`). Sprint별 실측치는 `BACKLOG.md`에 날짜와 함께 기록된다 |
| **Workspace** | Integration 생성 (①) | ✅ 완료 (health check PASS) |
| **Workspace** | PROJECTS Database 생성 + Property 구성 (③) | ✅ 완료 (§8의 11개 Property 전부 존재. Title도 `Project`로 반영됨. Notion 기본 `Date`/`Notes`/`Tags` 3개가 남아 있으나 무해) |
| **Workspace** | Integration 권한 부여 (④) | ✅ 완료 (PROJECTS 읽기/쓰기 확인) |
| **Environment** | `NOTION_API_TOKEN` 설정 | ✅ 완료 (`.env`) |
| **Environment** | `NOTION_PROJECTS_DATABASE_ID` 설정 | ✅ 완료 (`.env`) |
| **Environment** | Secret이 Git에 커밋되지 않음 확인(`.gitignore`) | ✅ `tests/test_repository_hygiene.py::SecretExposureGuardTests`가 전 tracked 파일을 검사 |
| **Environment** | `COMPANY_OPS_HISTORY_START_DATE` 설정 | ❌ **미설정** — `.env`·User·Machine 어디에도 없다. 이 상태에서 `run_company_ops.py`는 첫 줄에서 exit 1이며 **Runner가 아예 뜨지 않는다** |
| **Environment** | `COMPANY_OPS_PROFILE` / `_AGENT_SYNC_FOLDER` / `_AGENT_START_DATE` | ❌ **미설정** — `run_agent.py` 구성 불가. `scripts/install_agent_task.ps1`이 User 환경에 심는 세 값이다 |
| **Dashboard** | integration에 Page 공유 (⑧-1) | ✅ 완료 (C114 실측 2026-08-26: hostable pages = **168**, `readiness = NEEDS_PARENT_CHOICE`. PROJECTS 자신의 부모는 여전히 `workspace`이며, 그것과 "공유된 Page가 있는가"는 다른 질문이다) |
| **Dashboard** | `OPS_RUNS` Database 생성 (⑧-2) | ✅ 완료 (C114 실측: 존재하고, **22개 열이 `DASHBOARD_DATABASES[OPS_RUNS]`와 이름·타입 모두 일치**하며, 누락 0) |
| **Dashboard** | `NOTION_OPS_RUNS_DATABASE_ID` 설정 (⑧-3) | ✅ 값은 `.env`에 있다 — 다만 **`.env`에 있다는 것과 entrypoint가 본다는 것은 다른 일이다(§2.1)**. 그 상태에서만 Dashboard 기록이 SKIPPED다 |
| **Acceptance** | §66 Phase 3 완료 기준 13개 항목, 실제 Workspace 기준 | ⬜ 미착수 |
| **Rollback** | Notion Workspace/Integration을 잘못 만들었을 때 원복 방법 | 아래 §6 참고 |

즉 **Notion Sync도 Operations Dashboard도 실제로 연결돼 있고, Runner 자체는 이 머신에서 환경변수 때문에 실행되지 않는다.** OPS_RUNS에는 `record_run()`이 쓴 행 **6개**가 들어 있으며 전부 `ENGPROBE-*` — 즉 **Engineering Probe이고 실제 업무 실행이 아니다.**

> **이 세 줄은 C114 이전까지 셋 다 ❌였고, 셋 다 틀려 있었다.** 그것이 무해한 오차가
> 아닌 이유: ⑧-2의 지시는 `bootstrap_dashboard_databases()`를 부르라는 것이고, 그
> 함수는 **조건 없이 생성하며 이 모듈에는 삭제 경로가 없다**(설계상 그렇다). 이미
> 존재하는 Workspace에 대고 이 문서를 따르면 `OPS_RUNS`가 **둘**이 되고, 어느 쪽을
> `NOTION_OPS_RUNS_DATABASE_ID`가 가리키는지 말할 수 있는 것이 아무것도 없다.
> 그래서 C114는 이 표만 고치지 않았다 — **문서가 다시 낡아도 사람이 그 실수를 하지
> 않도록** `diagnose_dashboard_bootstrap()`이 OPS_RUNS를 실제로 들여다보고
> `ALREADY_CREATED`를 답하게 했다(§3-⑧ 참고). 이 표는 측정한 날의 사실이고,
> 그 진단은 실행하는 날의 사실이다.

---

## 6. Rollback

Notion 측:

- 잘못 만든 Integration은 https://www.notion.so/my-integrations 에서 삭제하거나 Secret을 재발급(Regenerate)할 수 있다 — 재발급 시 기존 `.env`의 값은 즉시 무효가 된다.
- PROJECTS Database는 Notion에서 페이지 삭제(Trash로 이동) 후 필요 시 복원 가능(Notion 자체 Trash 보관 기간 내).
- Integration 권한은 Database "Connections"에서 언제든 해제할 수 있으며, Company Ops 쪽 코드 변경이 필요 없다(§42, 최소 권한 원칙과 대칭).

Company Ops 측:

- `.env`를 비우거나 삭제하면 `NotionConfig.from_env()`가 즉시 `NotionConfigError`를 발생시켜, Runner에 `notion_sync`를 구성하지 않은 것과 동일하게 Notion Sync 단계가 스킵된다(`src/app/runner.py` — `notion_sync=None`이면 해당 단계 자체를 건너뛴다). 코드 롤백이 아니라 환경변수 제거만으로 Notion 연동을 즉시 끌 수 있다.
- 이 문서와 `.env.example` 자체를 되돌리는 것은 일반적인 `git checkout`/`git revert` 대상이며, 별도 절차가 필요 없다.

---

## 7. Summary

Notion **Sync**에 필요한 환경변수는 `NOTION_API_TOKEN`, `NOTION_PROJECTS_DATABASE_ID` 두 개뿐이고, 이 배포에서는 둘 다 설정돼 있으며 실제로 동작한다(§5). 이 문서는 그 두 값을 발급·확인하는 절차(①~⑦), Operations Dashboard를 켜는 절차(⑧), 값이 채워진 뒤 수행할 검증 순서, Release 판단에 필요한 Checklist를 정리한다.

남은 것은 **하나**이고 운영 작업이다:

1. **Runner 환경변수** — `COMPANY_OPS_HISTORY_START_DATE`(+ Agent용 3개). 없으면 Runner도 Agent도 실행되지 않는다. `.env`에 적는 것만으로는 부족하다(§2.1).

~~2. **Dashboard용 Page 공유** (⑧-1)~~ · ~~3. **`OPS_RUNS` 생성 + id 설정** (⑧-2, ⑧-3)~~ —
**C114에서 실측으로 반증됐다.** Page는 공유돼 있고(hostable 168), `OPS_RUNS`는 존재하며
22개 열이 스키마와 정확히 일치하고, `NOTION_OPS_RUNS_DATABASE_ID`는 `.env`에 있다.
그 Database에는 `record_run()`이 쓴 행 6개가 들어 있다 — 전부 `ENGPROBE-*`, 즉
**Engineering Probe이며 실제 업무 실행이 아니다.**

이 세 줄은 실제로 완료된 뒤에도 "미완료"로 남아 있었다. BACKLOG의 여러 항목이 이
줄을 근거로 SKIP돼 있었고, C76이 남긴 교훈("환경 의존으로 기록된 항목은 근거보다
오래 살아남는다")의 세 번째 사례다. **다음에 이 문서를 읽는 사람이 같은 함정에
빠지지 않도록, 판정을 문서가 아니라 `init_notion.py`의 readiness가 하게 했다(§3-⑧).**

코드 쪽은 완성돼 있고 전체 회귀는 통과한다. 지금 Notion에 반영되는 것은 PROJECTS의
Current State와 OPS_RUNS의 실행 기록 둘 다이며, 후자가 실제 업무 실행으로 채워지려면
위 1번이 끝나 이 머신에서 Runner가 실제로 떠야 한다.
