# Company Ops — Backlog

이 파일은 Spec이 아니다. 승인 없이 진행할 수 없어 **SKIP한 항목**과, Audit
과정에서 발견했지만 이번 범위를 벗어난 항목을 기록한다.

문서 우선순위(README §13)는 변하지 않는다: 여기 적힌 내용이 `README.md`나
`docs/` 명세와 충돌하면 명세가 이긴다. 이 파일은 "아직 결정되지 않은 것"의
목록일 뿐이다.

마지막 갱신: 2026-08-10 (Partial-Failure Sprint — Dashboard bootstrap 중복 생성 방지, Monthly 예외 기록. src/ 전 모듈 감사 완료)

---

## A. 승인 필요 — 즉시 SKIP한 항목

Spec / Architecture / Policy 결정이 필요해 이번 Sprint에서 손대지 않았다.
각 항목은 "무엇을 바꿔야 하는가"와 "지금은 어떻게 동작하는가"를 함께 적는다.

### A-1. Desktop 3 = `ROLE=OTHER` 요청

요청된 구성은 `DESKTOP3 / ROLE=OTHER`였다. 그러나

- `docs/02_EVENT_SCHEMA.md` §9의 V1 Role은 `CTO_BACKEND / CTO_FRONTEND /
  CMO / COO` 4종이며, "필요성이 실제로 확인되기 전까지 Role을 추가하지
  않는다"고 명시한다.
- `README.md` §3과 `docs/02` §8의 표는 Desktop 3 = CTO Frontend로 고정한다.

따라서 `OTHER` Role 추가는 docs/02 변경이다. **SKIP.**

현재 동작: Desktop 3은 `CTO_FRONTEND`로 동작하며 Agent는 정상 작동한다.
"기타 역할"이 실제로 필요해지면 docs/02 §9의 Role 목록과
`events.schema.ROLES`, `daily/markdown._ROLE_DISPLAY_NAMES`,
`notion/properties`의 Role 매핑을 함께 바꿔야 한다.

### A-2. `NO_ACTIVITY` Event Type

`events.schema.EVENT_TYPES`에 `NO_ACTIVITY`가 없다. 추가는 docs/02 변경.
**SKIP.**

현재 동작으로 요구사항은 이미 충족된다: Signal이 없는 날짜는 Event를 하나도
만들지 않고 수집 완료로 기록되며, Desktop 4의 Daily History는 그 날짜를
docs/06 §25의 Empty Day("No material company history recorded.")로 이미
렌더링한다. Agent 결과 객체에는 `DateOutcome.NO_ACTIVITY`로 드러난다.
즉 NO_ACTIVITY는 **표현되지만 전송되지 않는다** — wire 표현 없이 동작한다.

### A-3. 역할별 Daily Report 세부 분류

요청된 형태는 역할마다 다른 항목이었다:

```
CTO  - 활동 / 개발 완료 / 테스트 / 버그
CMO  - 활동 / 콘텐츠 / 실험 / 이슈
```

`HistoryCandidate.category`는 `DECISION / MILESTONE / ISSUE / LEARNING`
4종(docs/05)뿐이다. "테스트", "버그", "콘텐츠", "실험"을 구분하려면 새로운
분류 정책이 필요하고, 이는 docs/05 변경이다. **SKIP.**

부분 구현: `src/daily/role_summary.py`가 **기존 category 어휘만으로**
역할별 그룹핑(활동 / 완료 / 이슈 + 무활동 역할 명시)을 제공한다. Daily
History Markdown 파일은 전혀 바꾸지 않았다 — docs/06이 그 구조를 고정하고
`tests/test_spec_conformance.py`가 섹션 순서를 검증하기 때문이다.

### A-4. Daily History Markdown에 역할별 섹션 추가

A-3과 별개로, 렌더링 자체를 역할 기준으로 다시 자르는 것은 docs/06 §14-26
템플릿 변경이다. **SKIP.**

### A-5. Notion Dashboard의 Multi-Desktop 속성

Operations Dashboard(`notion/dashboard.py`)는 실행 단위 요약만 기록한다.
Desktop별 / Role별 집계 속성을 추가하려면 docs/04의 Property 표를 바꿔야
한다. **SKIP.**

현재 동작: Desktop 1~4의 Event는 기존 `ExecutionPlanSync` 경로로 Notion에
동일하게 반영된다 — Agent 도입으로 달라진 것이 없다.

### A-6. Backup Long Path 지원

`backup/working_copy.py`는 `scheduler/lock.py`와 달리 `\\?\` 확장 경로
접두어를 쓰지 않는다. Long path가 비활성화된 머신에서 Local Master 경로가
~250자를 넘으면 `WinError 206`으로 Backup이 실패한다.

`git.exe` 자체도 `core.longpaths` 없이는 같은 한계를 갖기 때문에 Python
쪽만 고쳐서는 end-to-end로 동작하지 않는다. 수정 범위(Python 접두어만 /
git 설정 요구 / 배포 경로 길이 제약 문서화)는 정책 결정이다. **SKIP.**

이번 Sprint에서 한 일: 해당 테스트가 long-path가 **켜진** 머신에서 무조건
실패하던 것을 고쳐, 두 환경 모두에서 실제 동작을 검증하도록 바꿨다
(`tests/test_backup_working_copy.py::LongPathBoundaryTests`).

### A-6b. Collector State 쓰기 증폭 (측정 완료)

`PersistentSeenEventStore.mark_seen()`이 호출마다 전체 state 파일을 다시
쓴다. 따라서 한 번의 실행에서 N건을 수집하면 O(N²) 바이트를 쓴다.

측정값(이 머신, Python 3.13):

| 상황 | 결과 |
|---|---|
| 정상 운영: 기존 id 50,000건 + 신규 10건 | **0.23초** — 문제 없음 |
| 정상 운영: 기존 id 10,000건 + 신규 10건 | 0.05초 |
| 대량 Catch-up: 1회 실행에 5,000건 | 8.3초 (선형 대비 2.9배) |
| 대량 Catch-up: 1회 실행에 10,000건 | 28.0초 |

즉 **일상 운영에서는 무해하고, 최초 대량 Import나 초장기 오프라인 복구에서만
드러난다**. 고치려면 (a) 배치 저장 — 내구성 계약 변경, (b) append-only 포맷 —
파일 형식 변경 중 하나가 필요하고, 둘 다 정책 결정이다. Notion Retry Queue가
같은 트레이드오프를 CEO 승인("Batch Save B안")으로 처리한 선례가 있다. **SKIP.**

### A-7. 손상된 HistoryCandidate로부터의 자동 복구

저장된 Candidate 하나의 `timestamp`가 파싱 불가능하면 Scheduler catch-up이
그 날짜에서 영구히 멈춘다(`FAILED`를 정확히 보고하므로 격리는 지켜지지만,
사람이 고칠 때까지 진행되지 않는다). 격리(quarantine) / 건너뛰기 / 정지 중
무엇이 옳은지는 Data Safety 정책 결정이다. **SKIP.**

동일한 구조가 Agent outbox에도 있다: 읽을 수 없는 outbox 파일은 삭제되지
않고 남으며 그 날짜가 진행되지 않는다(`test_agent.py::
test_an_unreadable_outbox_file_is_never_silently_dropped`).

### A-8. 실제 Backup Remote / Notion Workspace 연결

`runtime/backup_working_copy/`의 실제 GitHub Private 원격 설정과 Notion
Workspace 생성은 운영 작업이며 자격 증명이 필요하다. **SKIP.**
(`run_company_ops.py` docstring, `docs/13_NOTION_ENVIRONMENT_SETUP.md`)

### A-9. Multi-Desktop Agent 명세 문서

`docs/`에 `14_MULTI_DESKTOP_AGENT_SPEC.md`를 추가하는 것이 자연스럽지만,
docs/ 는 명세 디렉터리이고 문서 추가 자체가 Spec 변경이다. **SKIP.**

대신 운영 안내는 `AGENT.md`(저장소 루트)에 두었고, 설계 근거는 각 모듈의
docstring이 담고 있다.

### A-10. README / docs 문서 정합성

- README §12의 문서 목록이 `11_DEPLOYMENT_RUNBOOK.md`에서 끝난다 —
  `12_APPLICATION_FLOW_SPEC.md`, `13_NOTION_ENVIRONMENT_SETUP.md` 누락.
- README 포함 13개 문서가 아직 `# D:\DOJOONPASS_COMPANY_OPS\...` 헤더를
  달고 있으나 저장소는 `C:\Users\user\Desktop\...`에 있다.

둘 다 명세 문서 수정이므로 **SKIP**. 현재 상태는
`tests/test_repository_hygiene.py::DocumentationGapCharacterizationTests`가
고정해 두었다.

### A-11b. HistoryCandidate에 `source` 추가

Daily Report는 **Role** 기준으로만 집계할 수 있다 —
`HistoryCandidate`(docs/05)는 `role`을 갖고 `source`는 갖지 않기 때문이다.
현재 Profile 표에서 Role과 Desktop은 1:1이라 실질적으로 같지만, 두 Desktop이
같은 Role을 쓰게 되는 순간 "Desktop별 활동"은 답할 수 없게 된다. 후보 스키마
변경이므로 **SKIP.**

### A-11c. Agent Heartbeat

`ops_status.py`는 "Desktop 2가 4일째 조용하다"까지만 말할 수 있고, 그것이
(1) PC가 꺼져 있었기 때문인지 (2) 보고할 일이 없었기 때문인지 (3) Agent가
고장났기 때문인지는 구분하지 못한다. 구분하려면 "살아 있다"를 뜻하는
Event Type이 필요하고 `events.schema.EVENT_TYPES`에는 없다(docs/02 §9의
자제 규칙). **SKIP** — 현재는 사실만 보고하고 해석하지 않는다.

### A-14. Review로 채운 Decision Context가 어디에도 도달하지 않는다

README RULE 11/12는 Decision Context를 회사의 가장 중요한 자산이라고 못박는다
("회사의 가장 중요한 자산은 코드가 아니라 시간이 지나도 복원 가능한 Decision
Context이다"). Event Schema에 그 필드가 없으므로 `RepositoryHistoryReviewer`가
유일한 입력 경로다.

**측정 결과**(`test_history_review.py::ReviewedContextReachesNothingTests`):

| 도달지 | 결과 |
|---|---|
| Candidate에 저장 | 된다 |
| Daily History 파일에 반영 | **안 된다** — 파일이 이미 있고, `update_daily_history()`는 event_id가 이미 있으므로 정확히 NO_LATE_EVENTS로 답한다 |
| git Backup에 포함 | **안 된다** — docs/08 §26-28은 `daily/`와 `monthly/`만 동기화하고, Candidate는 Local Master 밖 `runtime/`에 있다 |

즉 가장 중요하다고 선언한 자산이 **Company History도 아니고 백업도 되지 않는
단 한 곳**(`runtime/history_candidates/keep/*.json`, Desktop 4 로컬)에만 남는다.
디스크가 죽으면 사라지고 아무도 모른다.

빠져나갈 길이 모두 결정을 요구해서 **SKIP**했다:

- Daily 재렌더링 → COO 수기 수정을 지운다(docs/06 §57이 명시적으로 보호)
- 항목만 제자리 수정 → 사람이 편집하는 문서에 대한 텍스트 수술
- Late Event Update 확장 → docs/06 §37은 늦게 온 *Event*를 다루지, 이미 있는
  항목의 *보강*을 다루지 않는다
- Candidate를 백업 대상에 추가 → docs/08 §26-28 변경

현재 동작은 특성화 테스트로 고정했다. 참고로 **리뷰 전에** Daily를 생성하면
Decision Context는 정상 렌더링된다 — 렌더러가 아니라 이미 쓰인 파일로의
전파가 빠진 것이다.

### A-12. Monthly History의 서술형 Section

`src/monthly/`는 docs/09를 구현하지만 §14의 11개 Section 중 4개를 **생략**한다.
§14("내용이 없는 Section은 억지로 채우지 않는다")와 §30/§64/§65(없는 것을 만들지
않는다)에 따른 것이고, 생략 자체는 테스트로 고정돼 있다
(`test_monthly_history.py::OmittedSectionTests`).

| Section | 왜 규칙만으로 만들 수 없나 | 필요한 것 |
|---|---|---|
| Executive Summary | §15-16이 "이번 달 회사가 어디에서 어디로 이동했는가"를 요구 — 판단이다. §108은 "복잡한 Narrative Generator"를 V1 제외로 명시 | §62의 선택적 AI, 또는 COO 수기 작성 |
| Product Evolution | §27이 제품 수준 서술을 요구 | 위와 동일 |
| KPI / Customer Signals | §30이 숫자 생성을 금지하고 §31이 KPI 저장소가 아님을 명시. V1에 KPI Source가 없다 | KPI Source 결정 |
| Open Risks / Next-Month Carryover | §32-34가 "월말까지 미해결"을 요구 → Issue와 Resolution 짝짓기(§22)가 필요. `HistoryCandidate`는 `category`만 갖고 `event_type`이 없어 BLOCKED와 ISSUE_RESOLVED가 둘 다 ISSUE로만 보인다 | docs/05 Candidate 스키마에 `event_type` 추가 (A-11b와 동일한 변경) |

빈 달(§71)의 Executive Summary는 예외다 — 문장이 §71에 그대로 적혀 있어 기계적이며,
그 경우에만 렌더링한다. **SKIP.**

### A-13. Issue / Milestone 중복 압축

docs/09 §60-61은 같은 Issue의 여러 날 기록을 하나의 Lifecycle로, 작은 Milestone
여러 개를 큰 것 하나로 "가능하면" 압축하라고 한다. 둘 다 A-12의 마지막 행과 같은
이유로 규칙만으로는 불가능하다(발생/해결 구분 불가, 그리고 "비슷한 Milestone"의
판단 기준이 없다). 현재는 각 항목을 그대로 나열한다 — 누락은 없고 압축만 없다.
**SKIP.**

### A-11. Signal 작성 자동화

Agent는 Signal을 **읽어서** 전달할 뿐, 무엇이 "의미 있는 작업"인지 추론하지
않는다(README RULE 4, `reporter/reporter.py` 계약). 각 역할의 작업 도구가
Signal을 어떤 시점에 어떤 기준으로 생성할지는 역할별 워크플로 정책이다.
**SKIP.**

---

## B. 승인 없이 가능한 다음 작업

순수 코드로 진행할 수 있는 항목은 이번 Sprint에서 사실상 소진했다.
남은 것은 환경 의존이거나 정책 대기다.

### 환경 의존 (코드 검증은 최대한 끝냈다)

1. **Windows Task Scheduler 실제 등록 1회** — 이 환경에서는 **불가능함이
   확인됐다**(C5). 등록을 실제로 시도했고 `Register-ScheduledTask`가
   `액세스가 거부되었습니다`로 거부한다. 최소 재현(`cmd.exe /c exit`만 하는
   빈 Task, `-User`/`-Principal` 변형 포함)으로 스크립트 인자 문제가 아니라
   권한 문제임을 확인했다 — 비관리자 세션이다.

   스크립트 쪽에서 할 수 있는 것은 다 했다: `-WhatIf` 실동작 검증, 등록 실패
   시 원인·조치·머신 상태를 알려주는 메시지, 등록 직후 `Get-ScheduledTask`
   실재 확인. 남은 것은 **관리자 권한 세션에서 1회 등록 후 로그아웃/로그인**
   해 트리거가 실제로 도는지 보는 것뿐이다.
2. **Symlink 실환경 검증** — 거부 분기 자체는 이번 Sprint에 테스트로
   덮었다(`SymlinkRefusalBranchTests` 5건 — 실제 디렉터리·실제 파일·실제
   `load_signals()`를 쓰고 `Path.is_symlink`의 답만 바꾼다). 실제 symlink를
   만드는 E2E 2건은 여전히 `SeCreateSymbolicLinkPrivilege`가 없어 skip된다.
   Developer Mode 머신에서 1회.
3. **실제 2대 이상 머신의 OneDrive 왕복** — 단일 머신 공유 폴더로 대체 중.
   동기화 지연·부분 생성·0바이트·충돌 파일·순서 역전은 모두 재현해 검증했다.
   남은 미검증은 실제 OneDrive 클라이언트 고유 동작(파일 잠금, 대용량 지연,
   계정 재연결)뿐이다.
4. **실제 GitHub Remote** — Backup 메커니즘 자체는 검증됐다(C5):
   `runtime/backup_working_copy`가 로컬 bare 원격에 실제로 push하고 있고
   원격에 Daily History가 들어 있다. 남은 것은 `origin`을 실제 GitHub
   Private URL로 바꾸는 것 하나이며 자격증명이 필요하다.
5. **실제 Notion Workspace** — 자격증명 필요. `init_notion.py`가 준비돼
   있고 미설정 시 Sync/Dashboard를 건너뛰는 경로는 검증돼 있다.

### 남은 승인 없는 후보

이번 Sprint에서 "테스트가 한 번도 실행하지 않은 코드"를 훑어 결함 6건을
찾았다. 같은 눈으로 아직 보지 않은 곳:

**`src/` 전 모듈 감사 완료.** 남은 미검증은 코드가 아니라 환경이다 —
`bootstrap_dashboard_databases()`의 성공 경로만 실제 Notion Workspace를
요구하며, 실패 경로(부분 실패, id 누락, 부모 Page 없음, 재시도 복구)는
C9에서 전부 검증했다.

(감사 완료 이력: `notion/bootstrap.py`·`history/review.py`·`review_cli.py`·
`diagnose_dashboard_bootstrap()` → C7, `_parse_porcelain()` rename 경계 →
C8(도달 불가 확인 후 특성화), `bootstrap_dashboard_databases()` → C9.)

### 정책 대기 (승인 필요, A절 참조)

6. **보존 정책** — `sent/`, `transport/`, `processed/`,
   `collector_state.json`이 모두 무한히 커진다. 측정상 현재 규모에서는
   넷 다 문제가 아니고(D절), 넷 모두 "지우면 중복 방지가 한 계층 약해진다"는
   같은 트레이드오프를 갖는다. 한 번에 하나의 정책으로 정하는 편이 낫다.
   `processed/` 스캔 비용은 이번 Sprint에 코드로 해결했으므로(6x) 더 이상
   이 결정을 압박하지 않는다.
7. **`notion/dashboard_pending.remove_pending()` 정리** — 삭제 금지 원칙에
   걸린다. 이번 Sprint에 동작은 특성화 테스트로 고정했으므로(C5), 남은 것은
   "존재해야 하는가"라는 삭제 결정뿐이다.
8. **`.env` 로딩 부재** — `.env.example`이 "자동 로드하지 않는다"를 명시한
   확정 결정이라 되돌리려면 승인이 필요하다. Task Scheduler 등록 스크립트가
   사용자 환경변수로 영구 저장해 실질적으로 우회하고 있다. 이번 Sprint에
   문서와 코드가 어긋나지 않도록 양방향 계약 테스트를 붙였으므로(C5),
   현 구조가 유지되는 한 드리프트는 더 이상 발생하지 않는다.

---

## C9. Partial-Failure Sprint

`src/`에서 마지막까지 남아 있던 미감사 코드를 처리했다. **이제 전 모듈이
실패 경로까지 검증되어 있다.**

- **Dashboard bootstrap이 부분 실패 시 운영자 Workspace에 중복 Database를
  만들게 되어 있었다.** `bootstrap_dashboard_databases()`는 OPS_* Database
  5개를 루프로 만드는데, 3번째에서 실패하면 예외가 그대로 올라가면서
  **이미 만들어진 1·2번의 id가 버려진다.**

  재현 결과: 429 하나로 `OPS_RUNS`·`OPS_BACKUP`이 Notion에 실재하는데
  운영자가 그 id를 알 방법이 없다. 이 함수의 docstring 자신이 "호출자는 이미
  가진 Database를 다시 만들지 않을 책임이 있다(그 id는 설정에 들어간다)"고
  하는데 id가 없으면 불가능하다. 재시도하면 **두 번째 OPS_RUNS, 두 번째
  OPS_BACKUP이 조용히 생기고**, 이 함수는 삭제 권한이 없어 정리도 못 한다.

  `DashboardBootstrapPartialError`가 이미 만들어진 `{이름: id}` 맵, 실패한
  Database 이름, 원인을 함께 들고 나오도록 했다. 되돌리거나 재시도하지는
  않는다 — 그것은 호출자의 결정이고, 이 함수가 할 수 있는 일은 생성뿐이다.
  메시지가 `only=`로 남은 것만 재시도하라고 알려주고, 그 복구 절차를
  테스트로 실제 수행해 확인했다(Database 8개가 아니라 5개로 끝난다).

  id 없는 응답도 같은 이유로 조용히 넘기지 않는다 — `None`을 기록하면
  `database_id()`가 나중에 "만들어지지 않았다"고 답해 같은 중복 함정에
  다른 경로로 빠진다.

- **Monthly 단계의 예기치 못한 예외가 흔적 없이 사라졌다.**
  `except Exception: pass`. `monthly_run_once()`가 스스로 돌려주는
  PENDING/FAILED는 기록되지만 그 바깥으로 새는 예외는 아무 데도 남지 않아
  Monthly가 조용히 없어졌다. docs/09 §44/§74는 "기록하되 Runtime을 막지
  않는다"이므로 둘은 양립한다 — 삼키되 기록하도록 고쳤다.

  Runner 격리 자체도 이제 검증돼 있다: Monthly가 터져도 Backup은 돌고,
  History·Daily는 남고, 실행은 완료된다.

---

## C8. Operator-Facing Failure Sprint

- **Backup 실패가 운영자에게 Python traceback으로 나왔다.** docs/08 §19는
  push 실패를 "일상적이고 복구 가능한 상황"(BACKUP_PENDING, 다음 실행 재시도)
  으로 규정하는데, 실제로 재현해 보니 화면에는 스택 트레이스만 나온다.
  시스템이 망가진 것처럼 읽히지만 사실은 Backup이 마지막 단계라 그 앞의
  모든 것은 이미 디스크에 안전하다.

  **Runner의 계약은 건드리지 않았다.** `app/runner.py`가 예외를 그대로
  올려보내는 것은 알려진 특성화(BUG-4)이고, 반환 tuple에 "Backup 실패"를
  담을 자리가 없어 그것을 만드는 것은 계약 결정이다. 대신 `run_company_ops.py`가
  이미 받고 있던 예외를 어떻게 *보여줄지*만 고쳤다:

      무엇이 안전한지        Backup 앞 단계는 전부 저장됨, 유실 없음
      왜 실패했는지          git의 원본 메시지 그대로
      다음에 무슨 일이       일시적 → 다음 실행이 자동 재시도 / 인증 →
                             사람이 자격증명 갱신 (§19 vs §21·§62)
      어디서 확인하는지      `ops_status.py`

  분류는 backup이 state에 기록할 때 쓰는 `is_authentication_failure()`를
  그대로 재사용한다 — 화면과 `backup_state.json`이 다른 말을 하면 안 된다.

  **종료 코드 정책은 바뀌지 않았다.** 이 조건은 전에도 nonzero였다(처리되지
  않은 예외 → 1). 이제 정의된 2와 읽을 수 있는 메시지가 될 뿐이다. BUG-36이
  다루는 신호들(`final_status`, `scheduler_result.status`,
  `collector_summary.failed`)은 여전히 출력만 되고 종료 코드에 반영되지
  않는다 — 그쪽은 여전히 승인 대기다.

- **git porcelain rename 경계 — 도달 불가임을 확인하고 특성화했다.**
  `R  old -> new`를 파싱하면 `"old -> new"` 문자열 하나가 경로로 들어간다.
  다만 `sync_to_working_copy()`는 Working Copy에서 파일을 **삭제하지
  않으므로**(docs/08 §31/44-47 — 삭제를 *보고*하고 backup runner가 멈춘다)
  Working Copy는 추가·수정만 겪고 git이 rename을 감지할 조건 자체가 생기지
  않는다. 추측이 아니라 코드로 확인했다.

  rename을 어떻게 표현할지는 보고 형식 결정이고 도달할 수도 없으므로
  고치지 않았다. 대신 **도달 가능해지는 순간 드러나도록** 특성화 테스트를
  달았다 — Working Copy가 파일을 잃을 수 있게 만드는 변경이 생기면 여기서
  깨진다.

**세 Sprint 연속으로 같은 종류의 결함이 나왔다**: 실행되지 않는 코드(C7),
실패 경로(C6), 그리고 실패했을 때 사람이 보는 것(C8). 정상 경로는 오래 전에
견고해졌고, 남은 결함은 전부 "무언가 잘못됐을 때" 쪽에 있었다.

---

## C7. Uncalled-Code Audit Sprint

지난 Sprint에서 드러난 패턴을 끝까지 밀어붙였다: **테스트는 있는데 실제로는
아무도 부르지 않는 코드**. 그런 함수를 세 개 더 찾았고, 그중 둘은 결함까지
안고 있었다.

- **Review CLI가 저장 실패 한 번에 사람이 타이핑한 내용을 버렸다.**
  `submit_review()`가 예외를 던지면 CLI 전체가 죽는다. 재현해 보니 후보 3건
  중 1번에서 실패했을 때 **방금 쓴 Decision Context가 사라지고 2·3번은
  검토 기회조차 없었다.**

  이 프로젝트가 도처에서 쓰는 per-item 격리(collector runtime, outbox drain,
  monthly generator)를 여기에도 적용했다. 그리고 여기서 특히 중요한 이유는
  입력이 **사람 손에서 나왔다는 것**이다 — 실패 시 입력값을 화면에 되돌려
  주어 스크롤백에서 복구할 수 있게 했고, 마지막 요약에 실패 목록을 다시
  적었다(30개 뒤로 밀려 사라진 실패 메시지 하나는 "저장됨 2건"이라는 성공처럼
  읽힌다).

  반환값을 bool에서 `ReviewOutcome`(SAVED/SKIPPED/FAILED)으로 바꿨다 —
  "건너뜀"과 "실패"가 숫자 하나에서 구분되지 않으면 안 된다.

- **`diagnose_dashboard_bootstrap()`이 자기 계약을 어겼다.** docstring은
  "unusable workspace에 대해 절대 raise하지 않는다 — unusable 자체가
  답이다"라고 하고 `search_pages()` 호출은 그걸 지키는데, 바로 위의
  `get_database_parent()`는 무방비였다. 네트워크 장애·만료된 토큰·삭제된
  참조 Database에서 **진단 도구 자체가 터진다** — Notion이 이상할 때 바로
  그걸 알아보려고 부르는 도구가.

  같은 모양으로 보고하도록 고쳤다(`reference_parent_type="unreachable"` +
  무엇을 확인해야 하는지).

- **그 진단 함수도 호출자가 0이었다.** export돼 있고 테스트도 6건 있는데
  production에서 아무도 부르지 않았다. `init_notion.py`(Notion 설정 CLI)의
  마지막 단계로 붙였다 — 운영자가 "OPS_* Database를 만들 수 있나, 없으면
  뭘 해야 하나"를 묻는 바로 그 시점이다. 읽기 전용이고 종료 코드에 영향을
  주지 않는다(Dashboard는 선택 계층이고, 부모 Page 선택은 운영자 결정이다).

**이번 Sprint까지 찾은 "호출자 없는 코드" 3건**:
`remove_pending`(특성화만, 연결하면 오히려 나빠짐 — C5),
`check_state_consistency`(→ `ops_status.py`, C5),
`diagnose_dashboard_bootstrap`(→ `init_notion.py`, 이번).
공통점은 전부 **테스트가 초록이었다는 것**이다. 테스트는 함수가 동작하는지는
말해 주지만 누가 부르는지는 말해 주지 않는다.

---

## C6. Network / Subprocess Audit Sprint

이번 Sprint의 공통 주제: **한 번도 실행되지 않은 코드**. 네트워크 계층은
전부 In-Memory 더블로만 테스트돼 있었고, git은 subprocess 경계가 무방비였고,
정합성 검사기는 호출자가 없었다.

- **`RealNotionTransport`에 테스트가 하나도 없었다.** Notion 테스트 전체가
  `InMemoryNotionTransport`로 돌기 때문에 urllib의 오류 경로는 구조상 한 번도
  실행되지 않았다. 127.0.0.1의 로컬 소켓에 붙여 보니 결함 4건이 나왔다.

  | 상황 | 이전 | 지금 |
  |---|---|---|
  | 읽기 타임아웃 | `TimeoutError` 누출 | `NotionAPIError` |
  | JSON 아닌 200 응답 | `JSONDecodeError` 누출 | `NotionAPIError` |
  | 디코딩 불가 본문 | `UnicodeDecodeError` 누출 | `NotionAPIError` |
  | HTTP 4xx | 사유 폐기 | Notion 설명 포함 |

  앞의 셋이 중요한 이유는 모든 호출자가 `NotionAPIError`를 기준으로 분기하기
  때문이다 — `ExecutionPlanSync.sync()`가 NOTION_RETRY_REQUIRED로 바꾸고,
  bootstrap과 dashboard도 그것을 잡는다. 예상 못 한 타입은 그 분류를 통째로
  건너뛴다. 그리고 **타임아웃은 실제로 가장 흔한 장애인데 하필 그것이
  누출되고 있었다.**

  네 번째는 진단 문제다. 영구 4xx는 재시도 큐가 매 실행마다 다시 보내는데
  (별도 기록된 미해결 항목), "Bad Request"만으로는 어느 속성이 잘못됐는지
  영영 알 수 없다. 이미 이 결함을 기록한 특성화 테스트가 있었고, 이제 가드로
  바뀌었다.

- **git subprocess가 무한히 멈출 수 있었다.** `_run_git()`에 timeout이 없고
  자격증명 프롬프트도 막지 않았다. Runner는 Backup 단계 내내 시스템 전역
  Lock을 쥐고 있으므로, 멈춘 git 하나가 **이후 모든 Runner 실행을 막는다** —
  docs/08 §62가 금지하는 무한 재시도 루프보다 나쁘다(재시도는 최소한 Event
  수집은 계속한다).

  흥미로운 단서가 이미 코드 안에 있었다: `_AUTH_FAILURE_MARKERS`에
  `"terminal prompts disabled"`가 들어 있는데, 이는 `GIT_TERMINAL_PROMPT=0`일
  때만 git이 내는 메시지다. **아무도 그 변수를 설정하지 않았으므로 그 마커는
  영원히 매치될 수 없었다.** 코드가 스스로 기대하던 조건을 만들지 않고 있었다.

  timeout(300초) + `GIT_TERMINAL_PROMPT=0` + `GCM_INTERACTIVE=never`(Windows
  자격증명 관리자의 GUI 대화상자는 터미널 변수를 보지 않는다)를 추가했다.
  타임아웃은 `GitOperationError`로 변환되어 BACKUP_PENDING(다음 실행 재시도)로
  분류된다 — 인증 실패로 오분류되지 않음을 테스트로 고정했다.
  명령줄이 아니라 환경변수로 넣은 것은 승인된 git 명령 집합
  (`test_spec_conformance.py`)을 건드리지 않기 위해서다.

- **정합성 검사기에 호출자가 없었다.** `scheduler/consistency.py`는 docs/10
  §47이 명시한 손상(State는 Daily Close를 주장하는데 파일이 없음)을 감지하고
  테스트도 완비돼 있는데 **production 호출자가 0이었다.** 실행되지 않는
  감지기는 아무것도 감지하지 못한다.

  Runner가 아니라 `ops_status.py`에 붙였다. 그 모듈이 Scheduler 제어 흐름에
  들어가기를 거부하는 이유가 있고(§49 History 우선, §64 COO 판단), 읽기 전용
  상태 화면은 **결정하지 않고 보고만 하는** 유일한 자리다.

- **공백 설정값이 통과되고 있었다.** `""`는 이미 거부하면서 `"   "`는
  통과시켰다 — 눈에 보이지 않는 문자로 쓴 같은 실수다. 손으로 쓴 `.env`의
  `=` 뒤 공백 하나가 401 → NOTION_RETRY_REQUIRED → 모든 Event가 영원히
  큐에 쌓이는 결과로 이어졌다. 가장 나쁜 곳에 떨어지는 오타였다.

  **경계를 명확히 그었다**: 비어 있으면 없는 것으로 취급한다. 문자가 들어
  있는 값은 여전히 바이트 그대로 통과시킨다 — `"  ntn_x  "`를 다듬거나
  따옴표를 벗기는 것은 운영자가 설정한 것을 넘겨짚는 판단이라 하지 않았다.
  두 쪽 모두 테스트로 고정했다.

  덧붙여 `run_company_ops.py`가 "변수 없음"이라는 고정 문구 대신 실제 사유를
  출력하도록 했다. 공백 값일 때 운영자는 자기 `.env`에서 그 변수를 빤히
  보면서 "없음"이라는 말을 듣고 있었다.

---

## C5. E-1 실환경 검증 Sprint

- **Task 등록을 실제로 시도했고, 이 환경에서는 불가능함을 확인했다.**
  등록 → 검증 → 해제 → 환경 복원을 한 번에 하는 스크립트로 실행했다.
  `Register-ScheduledTask`가 `액세스가 거부되었습니다`로 실패한다.

  최소 재현으로 원인을 좁혔다: `cmd.exe /c exit`만 실행하는 빈 Task도,
  `-User`를 명시해도, `-Principal`을 Interactive로 줘도 **똑같이 거부된다.**
  즉 스크립트가 넘기는 인자의 문제가 아니라 이 세션이 Task를 쓸 권한이
  없는 것이다(비관리자). 스크립트 쪽에 고칠 것은 없다.

  정리는 정상 동작했다 — 남은 Task 0개, 환경변수 3개 모두 원래대로 미설정.
  머신은 실행 전과 동일하다.

- **등록 실패 시 운영자가 아무것도 알 수 없었다.** `Register-ScheduledTask`는
  현지화된 세 단어("액세스가 거부되었습니다")만 던지고 끝난다. 이 단계는
  배포의 핵심인데 그 상태로는 무엇을 해야 할지 알 수 없다. 원인·시도할
  것·현재 머신 상태(무엇이 설정됐고 무엇이 안 됐는지)를 담은 메시지로
  감쌌다.

- **등록 성공을 확인 없이 보고하고 있었다.** `Register-ScheduledTask`가
  예외 없이 돌아왔지만 Task가 없는 경우, 운영자는 Agent가 예약됐다고 믿고
  떠난다. 등록 직후 `Get-ScheduledTask`로 실재를 확인하고, 없으면 실패로
  보고하도록 했다.

- **Backup 체인은 실제로 동작하고 있다(확인).** `runtime/backup_working_copy`가
  로컬 bare 원격에 실제로 push하고 있고, 원격에 Daily History가 들어 있다
  (`backup: company history through 2026-08-10`까지 5개 커밋).
  즉 Backup 메커니즘 자체는 검증됐고, 남은 미검증은 `origin`을 실제 GitHub
  URL로 바꾸는 것뿐이다.

- **`.env.example`과 코드가 어긋날 수 있었다.** 아무것도 `.env`를 자동
  로드하지 않으므로 이 파일은 설정이 아니라 **문서**이고, 어긋난 문서는
  없느니만 못하다 — 코드가 요구하는 변수가 빠져 있으면 로그온 시 실패하고
  그 종료 코드는 아무도 보지 않는 Task로 간다. 양방향 계약 테스트를 붙였다
  (읽는 변수는 전부 문서화돼야 하고, 문서화된 변수는 전부 읽혀야 한다).
  안내문이 `run_company_ops.py / init_notion.py`만 언급하던 것도 4개
  entrypoint 전부로 고쳤다.

- **`remove_pending()`이 호출자도 테스트도 없는 공개 함수였다.** 그 조합이
  덫이다 — 쓸 수 있어 보이는데 동작이 보증되지 않는다. 삭제하지도, 억지로
  연결하지도 않고(현재 `drain_pending()`이 한 번만 저장하는 쪽이 더 낫다)
  동작만 특성화 테스트로 고정했다. 손상된 파일을 만나면 조용히 덮어쓰지 않고
  예외를 던진다는 점까지 포함해서.

---

## C4. Installer / Observability Sprint에서 발견하고 고친 것

- **Installer가 어떤 머신에서도 Task를 등록할 수 없었다.** 지연 시간을
  `$settings.CimInstanceProperties.Item('RandomDelay').Value = ...`로 넣고
  있었는데, `New-ScheduledTaskSettingsSet`에는 그런 속성이 없다. 조회 결과가
  `$null`이라 대입에서 `PropertyNotFound`로 죽었고, 그 지점은
  `Register-ScheduledTask`보다 **앞**이다. 즉 자동 실행이 한 번도 동작한 적이
  없다. 지연은 트리거 속성(`$logonTrigger.Delay`)으로 옮겼다.

  **정적 테스트 22건이 전부 통과하고 있었다.** 텍스트에 "RandomDelay"가
  들어 있는지만 봤기 때문이다. 스크립트를 실제로 실행해 본 적이 없다는 것이
  진짜 원인이었다.

- **`-WhatIf`가 사용자 환경변수 3개를 실제로 바꾸고 있었다.**
  `SetEnvironmentVariable` 3줄이 스크립트의 유일한 `ShouldProcess` 검사보다
  위에서 무조건 실행됐다. 잘못된 `-DesktopId`로 "미리보기"만 해도 그 머신의
  Agent 신원이 영구히 바뀌면서, 화면에는 아무것도 하지 않았다고 나온다.
  세 줄을 `ShouldProcess`로 감쌌다.

  이 결함 때문에 `-WhatIf` 실행을 테스트로 만들 수도 없었다 — 테스트 자체가
  개발자 환경을 오염시켰을 것이다. 고치고 나서야 실동작 테스트 7건을 붙일 수
  있었고, 그 테스트가 위의 `PropertyNotFound`를 잡았다.

- **실행 정책 때문에 첫 설치가 실패한다.** Windows 기본값은 서명되지 않은
  로컬 스크립트를 거부하므로 `.\install_agent_task.ps1`은 `UnauthorizedAccess`로
  끝난다. `AGENT.md`에 프로세스 한정 `-ExecutionPolicy Bypass` 형태를 적었다
  (시스템 정책은 바꾸지 않는다).

- **COO 상태 조회가 20,000건에서 107초였다.** 항목당 5.4ms이고 거의 전부가
  JSON 파싱이 아니라 **파일 열기** 비용이다. 순수 I/O이므로 스레드 풀(16)로
  돌려 5,000건 24초 → 3.3초, 20,000건 107초 → 18초가 됐다. 결과는 정렬된
  파일명 순서로 다시 접기 때문에 바이트 단위로 동일하며, 그것을 테스트로
  고정했다(직렬 읽기와 스냅샷이 완전히 같은지 비교).

  같은 처리를 `outbox.drain()`/`run_intake()`에는 하지 않았다. 그쪽은 순서와
  파일별 실패 격리가 계약의 일부인 쓰기 경로다.

- **Monthly 재생성이 "고치려던 그 파일" 때문에 실패했다.**
  `_existing_generated_at()`이 `OSError`만 잡아서, 기존 Monthly가 UTF-8이
  아니면 `UnicodeDecodeError`가 밖으로 나가 재생성 전체가 FAILED가 됐다.
  어차피 교체할 파일의 메타데이터 한 줄 때문에 그 달을 영원히 복구할 수 없게
  되는 구조였다. Fault Injection으로 발견.

- **Desktop이 조용한 이유를 일부 구분할 수 있게 됐다.** Event는 *작업한 날*을,
  파일은 *도착한 때*를 말한다. 일주일 꺼져 있다가 밀린 분을 보낸 Desktop은
  작업일만 보면 죽은 Desktop과 똑같아 보이지만 도착 시각을 보면 다르다.
  둘을 나란히 보고하도록 했다 — 새 Event Type도, 스키마 변경도, heartbeat도
  없이.

  도착 시각은 OneDrive를 건너온 파일 mtime이라 **측정이 아니라 정황**이므로,
  경보를 좁히기만 하고 **끄지는 않는다**. 죽은 Desktop에 대한 잘못된 안심은
  그것이 대체하는 오경보보다 나쁘다.

---

## C3. Monthly History Sprint에서 발견하고 고친 것

- **Monthly와 Notion의 관계는 이미 올바랐다(확인만 함).** docs/09 §82-84는
  전부 *금지* 조항이다 — Notion은 "지금 상태", Monthly는 "지난달 변화"이고
  "Notion 내용을 Monthly 원본으로 직접 Dump하지 않는다"(§82), Monthly는 COO
  판단 보고서가 아니며(§83) CEO 보고서로 자동 전송되지도 않는다(§84).
  현재 구현이 정확히 그 상태여서 고칠 것은 없었지만, 나중에 누군가 "친절하게"
  Monthly를 Notion에 동기화하는 것이 바로 §82가 막는 일이므로 테스트로
  고정했다(`test_monthly_history.py::MonthlyIsNotNotionTests`).

- **`ops_status.py`에 HISTORY 뷰를 추가했다.** Daily 파일 수, Monthly 파일
  목록, 마지막 통합한 달, 재생성 대기 중인 달을 보여준다. 닫힌 달인데 아직
  통합되지 않았으면 ATTENTION으로 올린다 — 그 달 Daily가 아직 완전하지
  않다는 뜻이기 때문이다. 새 state 없이 기존 파일만 읽는다.

- **Monthly History가 아예 없었다.** docs/09는 2,240줄로 저장 위치·파일명·
  구조·State·Catch-up·Late Event·Backup·Validation·14개 Mock Test까지
  전부 확정해 두었는데 `src/`에 생성기가 없었다. `backup/working_copy.py`는
  이미 `monthly/`를 동기화 대상에 포함하고 있어, 만들어지기만 하면 백업까지
  자동으로 이어지는 상태였다.

  구현은 docs/09 §63의 규칙 기반 경로다: Daily History → 카테고리 수집 →
  Template Monthly. AI는 쓰지 않으며(§63이 AI 없이도 동작할 것을 요구),
  규칙으로 도출할 수 없는 4개 Section은 채우지 않고 생략했다(A-12).

- **Late Event가 Monthly를 조용히 어긋나게 만들 수 있었다.** 직전 Sprint에서
  Daily의 Late Event 반영을 고쳤는데, 그 Daily로 이미 만들어진 Monthly는
  갱신되지 않는다. docs/09 §54-57이 정한 DIRTY → Rebuild → UPDATED 경로를
  구현해 같은 실행 안에서 재생성되도록 했다. Monthly가 자기 Daily와 다른
  내용을 담고 있는 창이 존재하지 않는다.

- **Late Event 항목이 카테고리를 잃고 있었다.** Daily의 `## Late Events`
  절은 모든 카테고리를 한데 담는데, 정규 4개 절과 달리 제목이 카테고리를
  알려주지 않는다. Monthly는 Daily 파일만 읽으므로(§12-13) 늦게 도착한
  DECISION을 Major Decisions에 넣을 방법이 없었다. Late 항목에만
  `- Category:` 불릿을 추가했다 — docs/06이 고정한 4개 절의 템플릿은
  건드리지 않았다.

- **Monthly State가 실제 `runtime/`으로 샐 수 있었다.** 직전 Sprint에서
  고친 것과 같은 부류의 잠복 결함이다. 아직 터지지 않은 이유는 테스트에
  완결된 달이 없었기 때문일 뿐이다. `monthly_state_path`를 전 테스트에
  넘기고, 재발 방지 가드에 그 인자를 추가했다.

- **PowerShell Installer가 저장소에서 유일하게 검증되지 않은 실행 파일이었다.**
  실제 등록은 되돌리기 어려워 하지 않았지만, 실제 PowerShell 파서로 구문을
  확인하고 환경변수 이름이 `run_agent.py`가 읽는 것과 정확히 일치하는지를
  테스트로 묶었다. 이 이름이 어긋나면 매 로그온마다 Task가 시작되고 설정
  오류로 종료 코드 1을 내지만 **아무도 보지 않는다** — 조용한 실패였다.

## C2. Hardening Sprint에서 발견하고 고친 것

- **BUG-17 (P0) — Late Event가 Company History에 영원히 도달하지 못했다.**
  이미 Daily Close가 끝난 날짜의 Event는 Collector ACCEPTED, History Filter
  KEEP, Notion Sync 성공으로 처리됐지만, Scheduler는 `.md`가 있는 날짜를
  건너뛰고 `generate_daily_history()`는 덮어쓰기를 거부했다. 모든 지표가
  성공을 보고하는 채로 History만 비어 있었다(README RULE 7 위반).

  Multi-Desktop 구성이 이 결함을 예외에서 일상으로 바꿨다 — Desktop 하나가
  Daily Close를 걸쳐 꺼져 있는 것이 정상 상황이기 때문이다.

  docs/06 §36-40이 이미 처리 방식을 확정해 두었으므로(존재 확인 → 기존 내용
  보호 → event_id 중복 확인 → 추가 → Metadata 기록) 새 정책을 만들지 않고
  그대로 구현했다. `docs/08 §65`의 "backup: history late update" 커밋
  템플릿도 이 경우를 위해 이미 존재했고, 이제 도달 가능해졌다.

  기존 파일은 **재렌더링하지 않고 append**한다 — docs/06 §57이 COO의 수기
  수정을 보존하라고 요구하기 때문이다. 재렌더링이 코드는 더 짧지만 그 수정을
  조용히 지운다.

- **Fault Injection이 내가 방금 넣은 코드의 결함 2건을 잡았다.**
  (1) `outbox.stage()`가 `FileExistsError`를 무조건 멱등 케이스로 취급해,
  `outbox/` 자리에 파일이 있어 `mkdir`이 실패한 경우까지 "저장 성공"으로
  보고했다 — Event가 디스크에 없는데 날짜가 수집 완료로 전진했다.
  (2) `outbox.drain()`이 `sent/`를 만들 수 없으면 예외를 밖으로 던졌다.
  계약("실패는 보고하고 던지지 않는다") 위반이며, Agent 실행 전체가 죽었다.

- **테스트가 개발자의 실제 `runtime/`에 쓰고 있었다.** `run_once()`에
  `late_update_log_path`를 추가하면서, 그 인자를 넘기지 않는 8개 테스트
  모듈이 저장소의 진짜 `runtime/logs/daily_late_update.log`에 기록하기
  시작했다. 존재하지 않는 Event에 대한 LATE_UPDATE 기록이, 운영자가 "실제로
  어떤 Event가 늦게 왔나"를 보려고 여는 바로 그 파일에 쌓였다. 아무 테스트도
  실패하지 않았고 앞으로도 실패하지 않았을 것이다.

  전 모듈에 인자를 넘기도록 고치고, 재발을 막는 정적 가드를 추가했다
  (`test_repository_hygiene.py::TestIsolationGuardTests`) — Runner를
  구동하는 테스트 모듈은 무조건 기록되는 로그 경로를 반드시 명시해야 한다.

- **Junction은 symlink 가드를 통과한다(특성 확인).** `Path.is_symlink()`가
  Windows junction에 대해 False를 반환하므로 Signal 디렉터리를 junction으로
  바꿔치기할 수 있다. 다만 그렇게 읽힌 Signal도 동일하게 검증·secret
  스캔되고 신원은 Profile에서 오므로 우회 경로가 되지는 않는다. symlink와
  달리 junction 생성에는 특별 권한이 필요 없어 확인해 둘 가치가 있었다.

---

## C1. Multi-Desktop Agent Sprint에서 발견하고 고친 것

기록용. 이미 완료되었으므로 작업 항목이 아니다.

- **`scheduler/lock.py::_long_path()` 실제 결함.** 이미 `\\?\` 접두어가
  붙은 경로를 받으면 `\\?\UNC\?\C:\...`라는 존재할 수 없는 경로를 만들어
  냈다. Windows API가 전부 거부하고 `try_acquire_lock()`은 이를 "다른
  Runner가 실행 중"으로 보고하므로, 그런 lock 경로로 설정된 Runner는
  **모든 실행을 조용히 건너뛴다**. 근거였던 "`Path.resolve()`가 접두어를
  제거한다"는 주석은 Python 3.13에서 거짓이다.
- **테스트 4건이 환경/버전 변화로 무력화되어 있었다.** Z-suffix 타임스탬프
  2건(py3.11+에서 파싱 가능), long-path 1건(머신 설정 의존), lock 1건(위
  결함을 감추고 있었음).
- **GAP-10 (COO Desktop Profile 부재)** — docs/02가 허용하는
  `DESKTOP_4 / COO` 조합에 Profile이 없어 COO 자신의 업무는 Event가 될 수
  없었다. Agent가 이를 운영상 필수로 만들었으므로 채웠다.
- **Secret 가드가 커밋 전에는 아무것도 막지 못했다.**
  `test_no_secret_material_in_any_tracked_file`이 `git ls-files`(= 이미
  커밋된 파일)만 스캔했다. 즉 유출을 *막는* 것이 아니라 이미 일어난 유출을
  *보고*하고 있었다. 이번 Sprint의 새 테스트 파일들이 의도적으로
  secret 형태의 fixture를 담고 있었는데, 커밋 직전까지 이 가드는 저장소가
  깨끗하다고 보고했다. `--others --exclude-standard`를 추가해 "`git add -A`
  후 저장소에 들어갈 모든 파일"로 넓혔고, 실제로 막는지 probe 파일로
  확인했다. fixture 쪽은 리터럴을 런타임 결합으로 바꿔 가드에 예외를 두지
  않았다.
- **Agent 견고성 2건** — 깊게 중첩된 JSON Signal이 `json.loads`의
  `RecursionError`(→ `ValueError`가 아님)로 Agent 실행 전체를 죽일 수
  있었다. secret 스캔의 재귀 순회도 같은 한계를 가졌다. 각각 명시적
  거부와 명시적 스택 순회로 바꿨다.

---

## D. 측정값 (이 머신, Python 3.13, Windows 11)

### COO 상태 조회 (`processed/` 스캔) — 이번 Sprint

| Event 수 | 직렬(이전) | 스레드 16(현재) |
|---|---|---|
| 1,000 | 5.2초 | 0.9초 |
| 5,000 | 24.2초 | 3.3초 |
| 20,000 | 107.1초 | ~17.8초 |

항목당 비용의 거의 전부가 파일 **열기**이며 JSON 파싱이 아니다(따뜻한 캐시
격리 실험에서는 28.5초 → 0.26초로 108배까지 나왔다). 워커 수는 8 → 4.7초,
16 → 3.3초, 32/64 → 3.3초로 16에서 평탄해진다.



성능 주장을 추측이 아니라 숫자로 남긴다. 테스트에는 wall-clock 임계값을 넣지
않았다 — 공유 머신에서 불안정하고, 느슨하면 아무것도 못 잡고 빡빡하면 무관한
이유로 실패한다.

| 대상 | 100 | 1,000 | 5,000 | 10,000 |
|---|---|---|---|---|
| outbox stage | 0.06s | 0.54s | 2.7s | 5.6s |
| outbox drain (전송+파일 이동) | 0.63s | 5.7s | 26.9s | 54.0s |
| outbox 재stage (멱등) | 0.05s | 0.57s | 2.8s | 5.6s |
| transport intake (신규) | 0.61s | 5.7s | 27.2s | 55.4s |
| transport intake (전량 중복) | 0.003s | 0.03s | 0.13s | **0.26s** |
| collector state (1회 실행 수집) | 0.06s | 0.78s | 8.6s | **28.0s** |

읽는 법:

- stage / drain / intake는 **선형**이다(항목당 약 0.5ms / 5.4ms / 5.5ms).
  알고리즘 결함은 없고, drain·intake의 항목당 비용은 파일 읽기 + 이름 변경
  1회씩이 지배한다.
- **중복 intake는 사실상 공짜다**(항목당 26µs, stat 3회). `transport/`가
  무한히 쌓인다는 우려는 측정해 보니 성능 문제가 아니다.
- **collector state만 초선형**이다. 상세와 실제 영향 범위는 A-6b 참조 —
  일상 운영(하루 수십 건)에서는 문제가 되지 않는다.

`tests/test_agent_outbox_stress.py`의 정확성 단언은 N=300(기본), 1,000,
5,000, 10,000 전부에서 동일하게 통과했다.

### Monthly Consolidation (이번 Sprint 추가 측정)

| 대상 | 결과 |
|---|---|
| Daily Coverage 확인 (31일) | 0.001초 |
| 1개월 통합 (하루 1건) | 0.166초 (하루당 5.4ms) |
| 1개월 통합 (하루 20건) | 0.210초 (하루당 6.8ms) |
| 12개월 Catch-up (하루 5건) | 2.04초 (월당 0.17초) |

**선형이고 저렴하다.** 비용은 Daily 파일 하나당 열기+파싱 1회가 지배하며,
항목 수에는 거의 반응하지 않는다(하루 1건과 20건이 사실상 같다). 병목 없음.

---

---

## E. 다음 Sprint 제안

우선순위는 Reliability → Observability → Recovery → Security → Performance →
Testability → Release Readiness 순으로 두었다. 기능 추가는 마지막이다.

### E-1. 실환경 1회 검증 (Release Readiness) — 승인/환경 필요

코드로 더 밀어낼 수 있는 것은 사실상 끝났다. 남은 위험은 전부 "실제 환경에서
한 번도 돌려보지 않았다"는 한 가지 종류다. 순서대로 각 1회:

1. **관리자 권한 PowerShell에서** Desktop 하나에
   `install_agent_task.ps1` 등록 → 로그아웃/로그인 → Task가 실제로 도는지,
   `runtime/agent/logs/agent.log`에 기록이 남는지.

   스크립트는 `-WhatIf`로 실제 실행해 검증했고(C4에서 실동작 결함 2건을
   그렇게 찾아 고쳤다), 실제 등록도 시도해 **이 세션에는 권한이 없다는 것을
   확인**했다(C5). 남은 미검증은 "권한이 있는 세션에서 등록하면 Windows가
   로그온 시 실제로 띄우는가" 한 가지다.
2. 실제 OneDrive 폴더를 공유한 2대 사이 왕복 1회
3. 실제 GitHub Private 원격에 Backup Push 1회
4. Notion Workspace 연결 후 Sync 1회
5. Developer Mode가 켜진 머신에서 symlink 테스트 2건 실행

각각 되돌리기 어렵거나 자격증명이 필요해 이번 Sprint에서 하지 않았다.
1~4는 순서를 지켜야 한다 — 뒤엣것이 앞엣것을 전제한다.

### E-2. 보존 정책 결정 (Reliability) — 승인 필요

지금 무한히 커지는 것: `sent/`, `transport/`, `processed/`,
`collector_state.json`의 `processed_event_ids`.

측정해 보면 **현재 규모에서는 넷 다 문제가 아니다**(D절). 그러나 넷 모두
"지우면 중복 방지가 한 계층 약해진다"는 같은 트레이드오프를 갖고 있어
개별적으로 결정할 문제가 아니다. 한 번에 하나의 보존 정책으로 정하는 편이
낫다. 결정되면 구현은 작다.

### E-3. Candidate에 `event_type` 추가 (Observability) — 승인 필요

하나의 스키마 변경이 세 가지를 동시에 푼다:

- Monthly의 Open Risks / Next-Month Carryover (A-12)
- Issue Lifecycle 압축 (A-13)
- 역할별 Daily Report의 세부 분류 가능성 (A-3의 일부)

지금은 BLOCKED와 ISSUE_RESOLVED가 둘 다 `category=ISSUE`로만 보여서
"아직 안 끝난 이슈"를 기계적으로 알 수 없다. docs/05 변경이다.

### E-4. Agent Heartbeat (Observability) — 승인 필요

`ops_status.py`는 "Desktop 2가 4일째 조용하다"까지만 말한다. 꺼져 있어서인지,
보고할 일이 없어서인지, 고장인지는 구분하지 못한다. 이 구분이 실제 운영에서
가장 아쉬운 한 가지다. docs/02에 Event Type 추가가 필요하다(A-11c).

### E-5. 손상 데이터 자동 복구 (Recovery) — 승인 필요

현재는 손상된 Candidate 하나가 그 날짜를, 읽을 수 없는 outbox 파일 하나가
그 Desktop을, 읽을 수 없는 Daily 하나가 그 달을 각각 멈춘다. **유실은 없고
정지만 있다** — 의도된 안전 방향이지만, 사람이 알아차리지 못하면 정지가
길어진다. 격리(quarantine) / 건너뛰기 / 정지 중 무엇이 옳은지는 Data Safety
정책 결정이다(A-7).

`ops_status.py`가 이 세 가지를 모두 ATTENTION으로 올리므로, 정책이 정해지기
전까지는 "빨리 알아차리기"로 완화되어 있다.

### E-6. 승인 없이 가능한 것

B절 참조. 남은 것은 전부 환경 제약이거나 정책 대기 상태다.

이번 Sprint의 교훈 하나를 남긴다: **"정적 검증했다"와 "동작한다"는 다르다.**
Installer는 정적 테스트 22건을 통과하면서도 어떤 머신에서도 Task를 등록할 수
없는 상태였다. 실행해 볼 방법이 있는 코드는 — `-WhatIf`처럼 안전한 모드가
있다면 — 반드시 실행해 보는 쪽으로 판단해야 한다. 남은 환경 의존 항목들도
같은 눈으로 다시 볼 가치가 있다.
