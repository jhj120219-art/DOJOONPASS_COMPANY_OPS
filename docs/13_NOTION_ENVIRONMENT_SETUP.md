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

소스코드(`src/`) 전체에서 `os.environ` / `os.getenv`를 사용하는 곳을 기준으로 추출했다.

| 변수 | 구분 | 사용처 | 설명 |
|---|---|---|---|
| `NOTION_API_TOKEN` | **필수** | `src/notion/config.py` (`NotionConfig.from_env`) | Notion Integration의 Internal Integration Secret. 없으면 `NotionConfigError` 발생, Notion Sync 자체를 구성할 수 없다. |
| `NOTION_PROJECTS_DATABASE_ID` | **필수** | `src/notion/config.py` (`NotionConfig.from_env`) | Notion Sync가 Create/Update할 PROJECTS Database의 ID. 없으면 위와 동일하게 `NotionConfigError`. |
| `COMPANY_OPS_PROFILE` | 선택 | `src/reporter/profiles.py` (`resolve_profile`) | Reporter가 어떤 Desktop 프로필(`DESKTOP_1`/`DESKTOP_2`/`DESKTOP_3`)로 동작할지 결정. `Reporter(profile=...)`를 코드에서 명시적으로 넘기면 이 변수는 필요 없다 — 이번 Sprint(Notion Environment Setup)의 범위 밖이며 참고용으로만 기재한다. |

Notion 연동에 필요한 것은 `NOTION_API_TOKEN`, `NOTION_PROJECTS_DATABASE_ID` 두 개뿐이다. 이 외에 코드가 요구하는 Notion 관련 환경변수는 없다(`src/notion/transport.py`의 `NOTION_API_BASE_URL`, `NOTION_API_VERSION`은 환경변수가 아니라 코드 내 상수).

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
4. 이 프로젝트는 `.env` 자동 로딩 코드가 없다(`python-dotenv` 등 미사용, `src/notion/config.py`는 `os.environ`만 읽는다) — 실행 전 셸에서 값을 export하거나, 실행 스크립트에서 `.env`를 직접 로드해야 한다. 이 로딩 방식 자체를 만드는 것은 이번 Sprint 범위 밖이다(코드 작성 금지).

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

| 구분 | 항목 | 상태 |
|---|---|---|
| **Mock** | Mock Test 1~8 (`tests/test_notion_sync.py`, §57~64) | ✅ PASS |
| **Mock** | NotionClient Health Check / Project Lookup (`tests/test_notion_client.py`) | ✅ PASS |
| **Runtime** | Runner ↔ ExecutionPlanSync 연결, ACCEPTED만 Sync (`tests/test_runner_notion_integration.py`) | ✅ PASS |
| **Runtime** | Notion 실패 시 History/Daily/Backup 계속 진행 (Mock 기준) | ✅ PASS |
| **Runtime** | 전체 회귀 테스트 (267 passed) | ✅ PASS |
| **Workspace** | Integration 생성 (①) | ⬜ 미완료 |
| **Workspace** | PROJECTS Database 생성 + Property 구성 (③) | ⬜ 미완료 |
| **Workspace** | Integration 권한 부여 (④) | ⬜ 미완료 |
| **Environment** | `NOTION_API_TOKEN` 설정 | ⬜ 미완료 |
| **Environment** | `NOTION_PROJECTS_DATABASE_ID` 설정 | ⬜ 미완료 |
| **Environment** | Secret이 Git에 커밋되지 않음 확인(`.gitignore`) | ✅ 기존 확인됨(단, `.env.example`이 `.env.*`에 함께 걸리는 기존 이슈는 별도 Backlog) |
| **Acceptance** | §66 Phase 3 완료 기준 13개 항목, 실제 Workspace 기준 | ⬜ 미착수(Environment 선행 필요) |
| **Rollback** | Notion Workspace/Integration을 잘못 만들었을 때 원복 방법 | 아래 §6 참고 |

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

Notion 연동에 필요한 환경변수는 `NOTION_API_TOKEN`, `NOTION_PROJECTS_DATABASE_ID` 두 개뿐이다. 이 문서는 그 두 값을 실제 Notion Workspace에서 발급·확인하는 절차(①~⑦)와, 값이 채워진 뒤 수행할 검증 순서, Release 판단에 필요한 Checklist를 정리한다. 코드는 이미 완성되어 있으며(Mock 267 passed), 남은 것은 순수하게 운영 환경 구축이다.
