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
| `COMPANY_OPS_AGENT_SYNC_FOLDER` | **필수(Agent)** | `run_agent.py`, `ops_status.py` | 이 Desktop이 Event를 쓰는 OneDrive Sync Folder. `ops_status.py`에서는 전달 정합성 확인에만 쓰이며, 없으면 "확인 불가"로 보고된다. |
| `COMPANY_OPS_AGENT_START_DATE` | **필수(Agent)** | `run_agent.py`, `ops_status.py` | 이 Desktop이 최초 실행에서 수집을 시작할 날짜. `COMPANY_OPS_HISTORY_START_DATE`와 분리돼 있다 — Desktop은 Company History보다 늦게 보고를 시작할 수 있다. |

`src/notion/transport.py`의 `NOTION_API_BASE_URL` / `NOTION_API_VERSION`은 환경변수가 아니라 코드 내 상수다.

### 2.1 `.env`는 자동으로 읽히지 않는다

이 저장소에는 `.env` 로더가 없다(`python-dotenv` 미사용). `src/notion/config.py`는 `os.environ`만 읽고, 루트 entrypoint 넷도 마찬가지다. 따라서 **`.env`에 값을 채워 넣는 것만으로는 아무 entrypoint도 그 값을 보지 못한다.** 셋 중 하나를 해야 한다:

1. 실행 셸에서 export한다.
2. 실행 스크립트가 `.env`를 직접 로드한다.
3. `scripts/install_agent_task.ps1`을 쓴다 — 이 스크립트는 `COMPANY_OPS_PROFILE` / `COMPANY_OPS_AGENT_SYNC_FOLDER` / `COMPANY_OPS_AGENT_START_DATE` 세 개를 **User 환경변수로 영구 등록한다.** 예약 작업(Task Scheduler)은 대화형 셸의 변수를 상속하지 않으므로 이 경로가 필요하다.

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

`OPS_RUNS`의 Property 정의는 `src/notion/dashboard.py`의 `DASHBOARD_DATABASES["OPS_RUNS"]`가 정본이다. 손으로 만들 때는 이름과 타입을 그대로 맞춘다 — 열이 많아 옮겨 적기보다 **출력해서 보고 만드는 편이 안전하다.** 이 문서에 목록을 복사하지 않는 이유도 같다(정본이 둘이 되면 어긋난다). 개수도 적지 않는다: C31 13열 → C32 15열 → C33 17열 → C37 18열 → C42 20열로 실제로 자라 왔고, 문서에 박아 둔 숫자는 그때마다 조용히 틀렸다:

```powershell
python -c "import sys; sys.path.insert(0,'src'); from notion.dashboard import DASHBOARD_DATABASES, OPS_RUNS; [print(f'{n:<20} {next(iter(t))}') for n, t in DASHBOARD_DATABASES[OPS_RUNS].items()]"
```

Notion UI에서의 타입 대응: `title`=Title, `date`=Date, `number`=Number, `select`=Select, `rich_text`=Text — 하나라도 없으면 Notion이 400으로 거절하고, 그 실행의 기록은 `runtime/state/dashboard_pending.json`에 쌓이며 `notion_sync.log`에 `DASHBOARD DRAIN_PENDING … REASON <Notion의 설명>`이 남는다(데이터는 잃지 않는다).

#### ⑧-4 이미 만든 Database에 열이 모자랄 때

위 400은 손으로 만들다 빠뜨렸을 때만 나오는 게 아니다. **`OPS_RUNS` 스키마는 자라 왔다** — C31까지 13열, C32에서 15열(`Transport Blocked`, `Notion Skipped`), C33에서 17열(`Notion Unreadable`, `Notion Queued`), C37에서 18열(`Failed Steps`), C42에서 20열(`Reused Days`, `Deleted Files`). ⑧-2를 C31 시점에 실행해 둔 Database는 지금 코드가 쓰는 열을 갖고 있지 않고, 그 상태에서는 **모든 실행이 400으로 거절된다.** 실패는 안전하지만(위 문단대로 큐에 쌓이고 이유가 남는다) 빠져나올 길이 없었다.

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
| **Dashboard** | integration에 Page 공유 (⑧-1) | ❌ **미완료** — `readiness = NEEDS_SHARED_PAGE`, hostable pages = 0, PROJECTS의 부모는 `workspace` |
| **Dashboard** | `OPS_RUNS` Database 생성 (⑧-2) | ❌ 미완료 (선행: ⑧-1) |
| **Dashboard** | `NOTION_OPS_RUNS_DATABASE_ID` 설정 (⑧-3) | ❌ 미설정 — Dashboard 기록은 매 실행 SKIPPED |
| **Acceptance** | §66 Phase 3 완료 기준 13개 항목, 실제 Workspace 기준 | ⬜ 미착수 |
| **Rollback** | Notion Workspace/Integration을 잘못 만들었을 때 원복 방법 | 아래 §6 참고 |

즉 **Notion Sync는 실제로 연결돼 있고, Operations Dashboard는 한 번도 실행된 적이 없으며, Runner 자체가 이 머신에서는 환경변수 때문에 실행되지 않는다.** 앞의 둘은 Notion UI에서의 운영자 작업(⑧-1)이 선행 조건이고, 셋째는 §2.1의 세 방법 중 하나다.

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

남은 것은 셋이고 전부 운영 작업이다:

1. **Runner 환경변수** — `COMPANY_OPS_HISTORY_START_DATE`(+ Agent용 3개). 없으면 Runner도 Agent도 실행되지 않는다. `.env`에 적는 것만으로는 부족하다(§2.1).
2. **Dashboard용 Page 공유** — Notion UI에서만 할 수 있고, 어느 Page를 쓸지는 운영자 결정이다(⑧-1).
3. **`OPS_RUNS` 생성 + id 설정** — ⑧-2, ⑧-3.

코드 쪽은 완성돼 있고 전체 회귀는 통과한다. 지금 상태에서 Notion에 반영되는 것은 PROJECTS의 Current State뿐이고, 실행 단위 운영 기록(Operations Dashboard)은 위 2·3이 끝나기 전까지 매 실행 건너뛰어진다.
