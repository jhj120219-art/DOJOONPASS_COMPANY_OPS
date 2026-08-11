# Company Ops — Backlog

이 파일은 Spec이 아니다. 승인 없이 진행할 수 없어 **SKIP한 항목**과, Audit
과정에서 발견했지만 이번 범위를 벗어난 항목을 기록한다.

문서 우선순위(README §13)는 변하지 않는다: 여기 적힌 내용이 `README.md`나
`docs/` 명세와 충돌하면 명세가 이긴다. 이 파일은 "아직 결정되지 않은 것"의
목록일 뿐이다.

마지막 갱신: 2026-08-12 (C18 Deep Audit Sweep — 병렬 감사 5건
[Traceability/Release, E2E 연쇄 시나리오, Multi-Desktop 상호작용, Failure
Isolation, Observability/기술부채] 전부 재확인만 하고 종료 — 신규 실행 가능한
결함 없음, 코드 변경 없음. C17까지의 실제 결함 6건 수정은 그대로 유지)

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

### A-15. `event_id`에 개행이 들어간 Event를 거부할 것인가 (C10에서 발견)

C10이 로그 위조(BUG-6)를 **로그 쓰기 지점의 escape**로 막았다. 위조는 이제
불가능하지만, 그런 `event_id`를 가진 Event는 여전히 **정상 수집된다.**

더 깊은 수정은 `events.schema`가 `event_id`를 한 줄로 제한하는 것이다. 그것은
Collector가 무엇을 받아들이는지를 바꾸는 **Event Schema 계약 변경**이다
(docs/02). 지금까지 수집된 Event 중 이 제한에 걸리는 것이 있으면 재수집 시
동작이 달라진다. **SKIP.**

부수 사실 하나가 결정에 필요하다: Windows에서 그런 `event_id`는 적법한
파일명이 아니므로 History Filter가 `OSError`로 **실행 전체를 중단시킨다**
(BUG-5, 미해결·특성화됨). 즉 지금은 "받아들이지만 나중에 터진다"이고,
스키마에서 거부하면 "받는 순간 REJECTED"가 된다. 후자가 낫지만 그 판단은
docs/02 소관이다.

### A-17. `local_master` 이름 변경 (C12에서 발견)

Operational Data Model이 이 저장소를 **Company Repository**로 부르기로 했고
운영자에게 보이는 문자열은 전부 바꿨다(`ops_status.py`). 그러나 다음은 남았다.

| 대상 | 규모 | 왜 남겼나 |
|---|---|---|
| `run_once(local_master_dir=...)` | 공개 API, 호출부 219곳 | **Breaking Contract** |
| `runtime/local_master/` 디렉터리 | 실 데이터 존재 | 이동은 마이그레이션 |
| docs/04~12의 "Local Master" | 13개 spec | **문서가 계약이다**(README §13) |
| 코드 docstring ~40곳 | spec 조항을 인용 중 | 바꾸면 인용과 원문이 어긋난다 |

셋 다 같은 결정 하나에 걸려 있다: **spec 문서의 용어를 바꿀 것인가.** 바꾸면
13개 문서와 공개 API와 디스크 레이아웃을 함께 옮겨야 하고, 안 바꾸면 코드는
spec 용어를, 운영자 화면은 새 용어를 쓰는 현 상태가 유지된다. **SKIP.**

현 상태가 위험하지는 않다 — 두 이름이 같은 것을 가리킨다는 사실이
docs/14 §2에 명시돼 있다.

### E-9b. BUG-47 sync facet — C15 운영 수준 재측정 (여전히 SKIP)

기존 특성화는 `OneDriveTransport.send()` 단위였다. C15는 **실제 Agent로**
끝까지 돌려 운영자에게 무엇이 보이는지 측정했다. 목적지에 0바이트 placeholder
(OneDrive Files On-Demand의 형태)를 미리 심고 Agent 실행:

| 신호 | 값 |
|---|---|
| sync 폴더 파일 | **여전히 0바이트** — 배달 안 됨 |
| Agent `sent/` | event_id **존재** — 배달된 것으로 기록 |
| `last_successful_collection_date` | 배달 안 된 날짜를 **지나 전진** |
| Agent 종료 코드 / 로그 | `0` / `COLLECTED events=1` |
| 어디든 경고 | **없음** |

즉 `sent/`와 sync 폴더가 **서로 다른 말을 하고 그 불일치가 조용하다.**
A-20과 같은 부류("완료로 기록됐지만 실제로는 아님")이며 **발신 측** 버전이다.

**왜 여전히 SKIP:** 고치려면 sync 폴더의 기존 항목을 덮어써야 하는데, 그것은
"OneDrive가 읽는 중일 수 있는 파일을 언제 덮어써도 되는가"라는 race 정책
결정이다(Phase 5.15의 staging buffer가 존재하는 이유).

**완화되어 있는 것:** 배달 내용이 **틀리는** 경우는 C11이 없앴다(staging
overwrite + `event.to_json()` 직접 전달). 남은 것은 "배달 안 됐는데 성공
보고"뿐이고, `ops_status.py`의 Desktop 침묵 추적이 결과적으로 드러낸다 —
단, 원인까지는 말하지 못한다.

**다음에 필요한 조건:** sync 폴더 덮어쓰기 정책 결정. 결정되면 구현은 작다
(`_write_atomic(..., overwrite=)`가 이미 있다).

### A-20. Collector와 Candidate 저장 사이의 유실 창 (BUG-20 잔여분) — C14 신규

**항목:** Collector가 event_id를 seen으로 기록하고 파일을 `processed/`로 옮긴
뒤, 5단계가 History Candidate를 쓰기 **전에** 실행이 끝나면 그 Event는 Company
History에서 **영구히 사라진다.**

**왜 새로운가:** BUG-20은 "3개 동시 Runner에서 Candidate 36% 유실"로 측정되고
Lock 원자성(O_EXCL)으로 **닫혔다.** 그 수정은 유효하다. 그러나 BUG-20이 지목한
세 결함 중 셋째는 동시성이 아니라 **파이프라인 순서의 성질**이다.

C14에서 **동시성 없이** 재현했다 — Runner 1개, lock 경합 0, `HistoryFilter.
evaluate()`만 크래시시킴:

| 확인 | 결과 |
|---|---|
| `processed/fi-crash.json` | 존재 (Event 파일은 살아남음) |
| `history_candidates/keep/` | 비어 있음 (Candidate 미작성) |
| 다음 실행 | `accepted=0` — 재검토되지 않음 |
| Daily History | 없음, 영구적으로 |

즉 **"BUG-20 fixed"는 트리거에 대해서는 참이고 유실 창에 대해서는 거짓이다.**
`README RULE 7`("Event와 History가 영구 손실되어서는 안 된다")에 걸린다.

**완화되어 있는 것:** Run Manifest가 실행을 FAILED로 보고하고 어느 Component가
중단됐는지 이름을 댄다(C12/C13). 운영자는 *무언가* 깨졌다는 것은 안다.
**모르는 것은 어느 Event가 사라졌는지**이고, 재실행으로 복구되지 않는다.

**왜 승인 필요:** 닫으려면 둘 중 하나다.
1. Candidate를 `mark_seen()` 이전에 또는 원자적으로 저장 → **Collector 계약 변경**
2. `processed/` 중 Candidate 없는 Event를 찾는 정합성 패스 → **새 복구 메커니즘**

둘 다 결정이지 정리가 아니다. **SKIP.**

**다음에 필요한 조건:** "Event 소비와 Candidate 저장의 원자성을 어디서
보장할 것인가"에 대한 결정.

**Evidence:** `tests/test_runner_failure_paths.py::ConsumedEventWithoutCandidateTests`
4건이 현 동작을 고정한다.

### A-19. Directory Junction Traversal (BUG-57) — C14 재측정, **여전히 SKIP**

**항목:** `daily/` 아래 directory junction이 Local Master 바깥 내용을 Working
Copy로 복사해 원격에 push한다.

**이유(승인 필요):** 리다이렉트된 `daily/`를 백업할 것인가 말 것인가는
**배포 정책 결정**이다. 거부하면 정당한 저장소 레이아웃(디스크 공간 때문에
`daily/`를 다른 드라이브로 junction)이 깨진다. 이 경계는 이전 Sprint가
명시적으로 그어 뒀다 — 단일 파일 링크는 정당한 용도가 없어 거부했고,
폴더 통째 리다이렉트는 있으므로 남겨 뒀다.

**C14에서 확인된 사실(신규):**

| 측정 | 결과 |
|---|---|
| junction 생성 권한 | **불필요** (`mklink /J`가 일반 사용자로 성공) |
| symlink 생성 권한 | 필요 — 이 세션에서 `WinError 1314`로 실패 |
| `os.walk(followlinks=False)` | **여전히 junction 안으로 내려간다** (`followlinks`는 symlink 전용) |
| `os.path.isjunction()` | **존재한다** (Python 3.12+), junction을 정확히 True로 판별 |

두 가지가 새로 분명해졌다. 첫째, **막을 수 없어서가 아니라 결정하지 않아서
열려 있다** — 탐지 수단은 표준 라이브러리에 있다. "탐지 불가"와 "거부할지
미정"은 다른 진술이고 참인 것은 후자뿐이다. 둘째, **"링크 추적 끄기" 같은
값싼 수정은 없다** — 하강을 멈추려면 디렉터리마다 검사하는 수동 walk가 필요하다.

**다음에 필요한 조건:** "리다이렉트된 History 디렉터리를 백업 대상으로
인정하는가"에 대한 결정. 인정한다면 현 동작이 맞고, 인정하지 않는다면
`_is_link_like()` + 비하강 walk로 구현은 작다.

**C14 중 시도했다가 되돌린 것:** 이 Sprint에서 junction 거부를 실제로
구현했고 유출이 막히는 것까지 확인했다(`BACKUP_FAILED: secret files detected:
daily\\linked`, 원격에 유출 0). 그러나 그 변경은 **정당한 레이아웃을 거부하는
정책 변경**이므로 되돌렸다. 좁은 수정이 가능한지도 확인했다 — junction은
디렉터리 전용이고 hardlink는 일반 파일과 구별 불가(내용이 실제로 그 자리에
있다)이므로, 정책을 건드리지 않고 고칠 수 있는 형태는 **없다.**

### A-18. `run_once()`가 Backup 예외를 흡수해야 하는가 (BUG-4) — C15 영향 범위 실측

결정에 필요한 것은 "무엇을 잃는가"인데 그동안 측정된 적이 없었다. C15에서
Dashboard client를 실제로 붙이고 remote를 깨뜨려 측정했다.

Backup 호출 뒤에 남은 단계는 넷이고, 그중 셋은 잃을 것이 없다:

| 단계 | 실제 내용 | 예외 시 |
|---|---|---|
| 8. State | 주석뿐 — 각 단계가 이미 자체 저장 | 손실 없음 |
| 9. Log | 주석뿐 — 이미 기록됨 | 손실 없음 |
| **9b. Operations Dashboard** | 유일한 실제 작업 | **유실** |
| 10. Lock Release | `finally` | 정상 실행 |

실측 결과:

```
GitOperationError propagated
dashboard rows written : 0
dashboard pending file : ABSENT   <- 재시도 큐에도 안 들어감
manifest written       : DEGRADED exit 3, 8 components
history candidate saved: True
```

**핵심 발견:** Dashboard 실패를 견디려고 만든 pending 재시도 메커니즘이
**아예 도달되지 않는다.** 그래서 그 실행의 Dashboard row는 다음 실행에서
복구되지 않고 **영구히 비어 있다.** Manifest에도 `dashboard` component가
없다(9개가 아니라 8개) — 건너뛰었다는 사실조차 기록되지 않는다.

**손실 규모:** Operations Projection의 row 1개. **데이터는 안전하다** —
History Candidate는 저장되고 Manifest도 기록된다(RULE 5: Notion/Dashboard는
History critical path 밖).

**여전히 SKIP:** 흡수는 반환 계약 변경이고 특성화 테스트 4건이 현 동작을
고정하고 있다. 다만 이제 결정에 필요한 숫자가 있다 — 잃는 것은 데이터가
아니라 투영 1행이며, 그것도 재시도되지 않는다.

### A-18b. `run_once()`가 Backup 예외를 흡수해야 하는가 (BUG-4, C12 재확인)

C12가 Manifest를 `finally`에 쓰면서 **증거 측면은 해소됐다** — 중단된 실행도
이제 분류된 Component 기록을 남긴다. 남은 것은 제어 흐름이다:
`GitOperationError`가 `run_once()` 밖으로 전파되어 이후 단계(Dashboard)가
실행되지 않는다.

흡수하면 반환 계약이 "Backup 실패"를 표현해야 하는데, Manifest가 이미 그것을
표현하므로 **이전보다 결정하기 쉬워졌다.** 다만 여전히 계약 변경이고
특성화 테스트 4건이 현 동작을 고정하고 있다. **SKIP.**

### A-16. Dashboard Database 4종이 영구히 비어 있다 (GAP-11, C10 재확인)

`bootstrap_dashboard_databases()`는 Database 5개를 만드는데 `record_run()`은
`OPS_RUNS` 하나에만 쓴다. `build_ops_backup_properties()`는 구현·export까지
돼 있으나 **호출자가 없고**, `OPS_NOTION_SYNC` / `OPS_RISK` / `OPS_READINESS`는
builder조차 없다.

C10이 여기를 건드리지 않은 이유: 어느 Database에 무엇을 쓸지는 docs/04 §53
("Notion 데이터 과잉 방지")이 걸린 **정책 결정**이지 정리 작업이 아니다.
`OPS_BACKUP`을 연결하는 것은 실행마다 Notion 쓰기를 1회 더 늘리는 일이다.
**SKIP.** (스키마가 나중 Sprint를 위한 의도적 준비라면 그렇다고 적어 두는
것만으로도 충분하다 — 지금은 아무 문서도 그 말을 하지 않는다.)

---

## B. 승인 없이 가능한 다음 작업

순수 코드로 진행할 수 있는 항목은 이번 Sprint에서 사실상 소진했다.
남은 것은 환경 의존이거나 정책 대기다.

### 환경 의존 (코드 검증은 최대한 끝냈다)

1. **Windows Task Scheduler 실제 등록 — 완료 (C13).** 이전 기록은 **틀렸다.**

   C5는 "이 환경에서는 등록이 불가능하다(비관리자 세션)"고 기록했고, 그 근거로
   "빈 Task조차 `-User`/`-Principal` 변형을 포함해 똑같이 거부된다"를 들었다.
   같은 머신에서 다시 측정한 결과:

   | 시도 | 결과 |
   |---|---|
   | `cmd.exe /c exit` + Once 트리거 | **등록됨** |
   | + SettingsSet / `-Force` / `-Description` | **등록됨** |
   | `Daily -At 09:00` | **등록됨** |
   | `AtLogOn -User <me>` | **등록됨** |
   | `AtLogOn` (`-User` 없음) | Access is denied |
   | `AtStartup` | Access is denied |

   거부되는 것은 **machine-wide 트리거뿐**이다. Installer는 `-User` 없는
   `-AtLogOn`을 쓰고 있었으므로 **어떤 비관리자 머신에서도 등록에 성공한 적이
   없다.** 인자 하나가 빠진 것이었고, 권한 문제가 아니었다.

   고친 뒤 실제로 등록·실행·검증했다(비관리자 세션):
   Task 등록 → `Start-ScheduledTask` → LastTaskResult=0 → Agent 실행 →
   6개 밀린 날짜 Catch-up → Event 전달 → Runner 수집 → Late Event 병합 →
   Backup commit → Run Manifest SUCCESS.

   `-User` 스코프는 등록 문제만 푸는 것이 아니다. 스코프 없는 `-AtLogOn`은
   **아무 사용자나 로그온할 때** 발화하는데, Agent는 자기 identity와 sync
   폴더를 **user** 환경변수에서 읽는다 — 다른 계정 로그온에 발화하면 설정이
   전혀 없는 상태로 도는 셈이었다.

   **교훈:** "불가능함이 확인됐다"로 기록된 측정은 근거보다 오래 살아남는다.
   이제 트리거 스코프와 정정된 메시지를 테스트가 매 실행 재검증한다.

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

## C18. Deep Audit Sweep

C17 직후 이어서 진행한 Sprint. C17에서 이미 고친 6개 영역은 반복하지 않고,
그 주변에서 아직 보지 않은 각도를 병렬 조사 fork 5개로 훑었다. **모든 fork가
"신규 실행 가능한 결함 없음"으로 종료** — 이번 Sprint에서 코드 변경은 없다.

### 조사 범위와 결과

1. **Traceability / Release Readiness** — Event 3가지 경로(ACCEPTED/
   DUPLICATE/REJECTED)의 로그-파일 상관관계, `docs/14`의 Severity 표와
   `app/runner.py`의 실제 매핑 대조, 4개 진입점의 Exit Code 문서 일치성,
   `requirements.txt` 부재(의도된 설계 — 표준 라이브러리만 사용) 확인.
   **결함 없음.** 사소한 문서 완결성 메모 하나(docs/14 §7이 Lock 미획득 시의
   정확한 exit code를 명시하지 않음 — 동작 자체는 일관되고 테스트도 있어 버그
   아님, docs/ 수정은 Spec 영역이라 보고만 함).
2. **E2E 연쇄 시나리오** — Late Event가 Monthly 재생성 중 Backup까지 실패하는
   복합 상황, Retry Queue 항목이 property mapping 변경을 건너뛰는지, 같은
   실행 안에서 Daily Close 전/후 Event가 섞여 도착하는 경우, Backup
   local-commit 성공/push 실패 후 즉시 재실행 — **4개 시나리오 전부 이미
   올바르게 처리됨**을 코드로 확인(각각 구조적 이유가 있음: Monthly State는
   Backup 이전에 로컬 저장 완료, Retry Queue는 Event 원본을 저장해 매번
   Property를 재생성, Collector가 배치 전체를 처리한 뒤에야 History Filter가
   시작되므로 같은 실행 내 순서 역전 불가능, push 재시도는 이미 CEO 승인
   A안으로 구현·테스트됨).
3. **Multi-Desktop 상호작용/동시 장애** — 여러 Desktop 동시 이상 상태(마스킹
   없음 확인), Desktop 4 자신의 Agent vs Runner 구분(섹션이 명확히 분리돼
   있어 혼동 불가), Desktop 간 Clock Skew(미래 시각 Event는 그 날짜가 실제로
   올 때까지 대기할 뿐 손실·손상 없음 — 기존 "오늘/미래 처리 안 함" 불변식이
   부작용 없이 흡수), 다중 날짜 Catch-up 중 하루의 Signal 거부(나머지 날짜를
   막지 않음, 실제 전달 실패만 FAILED를 유발). **결함 없음.** 저우선순위
   관측성 개선 후보 하나: `IntakeBacklog.rejected`/`awaiting_intake`
   (`app/desktop_activity.py`)가 Desktop별로 귀속되지 않은 전역 합계 —
   여러 Desktop이 동시에 거부된 Signal을 만들면 파일을 직접 열어야 어느
   Desktop인지 안다. 버그는 아니고(파일 자체는 검사 가능), 신규 결함도
   아니라 개선 여지일 뿐이라 이번 Sprint 범위에서 구현하지 않음 — 다음
   Sprint 후보로 기록.
4. **Failure Isolation (Backup/Dashboard 외)** — Collector/History
   Filter/Scheduler/Late Update 4단계 각각에 대해 예기치 못한 예외가
   이후 단계를 막는지 확인. Scheduler/Daily·Late Update·Monthly·Dashboard는
   전부 자체 try/except로 격리돼 있음을 확인. Collector 앞단(mkdir 등)은
   비격리이나 실제 손실 없이 다음 실행이 재시도하는 구조(A-18 수정으로
   LAST RUN에 이미 가시화됨). History Filter는 A-20 그 자체(기존 SKIP,
   신규 아님). **결함 없음.**

   후속 확인(fork가 시간 내 못 끝낸 것 직접 확인): `scheduler_run_once()`
   내부 `load_state()`가 State 파일 손상 시 `SchedulerStateError`를 던지고
   이것이 `app/runner.py`를 통해 전체 실행을 중단시키는지 재확인 — **이미
   BUG-3 (CEO 승인 A안)으로 명시적으로 결정된 사항**이었다
   (`tests/test_runner_failure_paths.py::CorruptStateFilePathTests`,
   "A안의 의도된 범위... Runner가 흡수하도록 하는 것은 병목 #3 B안이며
   승인되지 않았다"). 새 발견이 아니라 이미 결정된 정책의 재확인.
5. **Observability / 기술부채 / Architecture** (직접 수행) —
   `compileall`, `git diff --check`, TODO/FIXME/HACK/XXX 전체 grep(0건),
   bare `except:`/`except Exception: pass` 전체 grep(0건, 유일한
   `except Exception:` 1건은 `notion/transport.py`의 의도된 best-effort
   에러 상세 파싱으로 정상), C17이 추가한 import 정합성 확인. **신규
   기술부채 없음.**

### 전체 Regression

코드 변경 없음. C17 이후 상태 그대로 재검증: **1466 passed, 0 failed**
(2회 재실행, 동일 결과).

---

## C17. Production-Readiness Sweep

승인 없이 진행하는 장시간 Sprint. Baseline 재검증 → A-20/E-9/A-18/A-19 순으로
직접 감사 → Dashboard/Notion·Multi-Desktop은 읽기 전용 조사 fork 4개를 병렬로
투입해 새 결함을 찾고, 발견된 것은 모두 직접 구현·테스트했다.

### Baseline: Python 3.9.7 환경 드리프트로 인한 Regression

이전 Sprint 기록("이 머신, Python 3.13")과 달리 현재 기본 `python`은 Anaconda의
3.9.7이다. `tests/test_agent_fault_injection.py::SecretLeakTests`가
`Path.parents[1:]` 슬라이싱(3.10+ 전용)을 써서 `TypeError`로 실패하고 있었다.
`list(path.parents)[1:]`로 교체 — 검사 의도는 그대로, Python 버전 호환성만
수정. 저장소 전체에서 `.parents[슬라이스]` 패턴은 이 한 곳뿐임을 확인했다
(`grep -rn "\.parents\["`, 나머지는 전부 정수 인덱스라 3.9에서도 안전).

### A-20 — 동시 실행 중 오탐(false positive) 신규 발견 및 수정

`find_orphaned_events()`는 순수 함수이고 Lock을 전혀 모른다. 그런데
`app/runner.py`의 실제 흐름은 Collector가 배치 전체를 `processed/`로 옮긴
**뒤에야** History Filter 루프가 시작해 Candidate를 하나씩 쓴다(4→5단계).
즉 대량 Catch-up 도중 `ops_status.py`를 동시에 실행하면 — 문서가 명시적으로
안전하다고 보장하는 바로 그 사용법 — 아직 Candidate 차례가 오지 않은,
정상적으로 처리 중인 Event가 A-20 Orphan으로 잘못 보고될 수 있다. 재현하지
않고 코드 경로 분석만으로 확인(재현에는 실제 대량 배치 타이밍이 필요해
결정적 재현이 어려움 — 대신 로직 자체가 이 취약점을 구조적으로 갖고 있음을
`app/runner.py` 597-625행과 `collector/runtime.py`의 배치 완료 시점 비교로 확정).

**수정(탐지/관측성 강화, 수정이 아닌 추가):** `scheduler/lock.py`에
읽기 전용 `is_locked()` 신설 — `try_acquire_lock()`과 달리 Lock을 만들거나
가져가지 않고 "지금 살아있는 프로세스가 쥐고 있는가"만 답한다.
`ops_status.py::_print_history()`가 Orphan을 보고할 때 Lock이 잡혀 있으면
"Runner 실행 중 — 완료 후 재확인 권장" 문구를 **덧붙인다** — Orphan 목록 자체는
줄이거나 숨기지 않는다(진짜 유실을 가리면 안 되므로). 테스트:
`tests/test_lock_atomicity.py::IsLockedTests`(5건),
`tests/test_observability.py::ReconciliationLockAwarenessTests`(4건).

### A-18 — `_print_last_run()` 테스트 커버리지 0건 + 도달 못한 단계가 안 보임

fork 조사로 확인: `ops_status.py::_print_last_run()`는 저장소 전체에서
직접 테스트하는 곳이 하나도 없었다(`_print_company`/`_print_agent`/
`_print_history`는 각각 전용 테스트 클래스가 있음). 또한 이 함수는
`summary.components`에 **있는** 것만 순회하므로, Backup 실패로 실행이
중단돼 `recorder.begin(C_DASHBOARD)`조차 호출되지 못한 경우 Dashboard
행이 통째로 사라진 사실이 LAST RUN 화면에 전혀 나타나지 않았다(SKIPPED와
구분 불가 — SKIPPED는 명시적으로 출력되지만 "도달 못함"은 침묵).

**수정:** `_EXPECTED_COMPONENT_ORDER`(9개 Component 고정 순서, `app/runner.py`의
실제 `recorder.begin()` 호출부와 대조해 전부 무조건 도달함을 확인)를
`ops_status.py`에 추가. `summary.components`에 없는 이름을 계산해
"! 시작되지 못한 단계: dashboard" 및 ATTENTION 문구로 보고. Exit
Code·Severity·Retryability 등 Run Contract(docs/14)의 의미는 전혀 바꾸지
않음 — 순수 표시 추가. 테스트: `tests/test_observability.py::LastRunViewTests`
(7건, `_print_last_run()`의 첫 테스트 커버리지).

### Dashboard/Notion — OPS_RUNS 중복 행 + Pending 실패 사유 누락 (둘 다 실제 결함, 수정함)

fork 조사로 두 가지 실결함 확인, 둘 다 정책 결정 없이 수정 가능:

1. **`record_run()`과 `drain_pending()`이 find-before-create 없이
   `create_project()`를 무조건 호출.** `notion/dashboard_pending.py`의
   모듈 docstring은 "한 Runner 실행은 OPS_RUNS 행을 두 개 만들 수 없다"고
   **이미 약속**하고 있었는데 코드가 지키지 않았다 — `notion.sync.
   ExecutionPlanSync`는 동일 이유로 이미 `find_project()`를 먼저 부른다.
   재현: `create_page`가 Notion 쪽에는 실제로 쓰였지만 응답이 유실돼
   예외로 보이는 경우(`_FalseNegativeCreateTransport`), 재시도가 두 번째
   행을 만든다 — 기존 특성화 테스트(`RecordRunRetryDuplicationTests`)가
   스스로 "이 테스트가 실패하기 시작하면 find-before-create가 추가된
   것"이라고 적어 두고 있었다.

   수정: `NotionClient.find_by_title()` / `find_or_create_by_title()` 신설
   (title Property 기준 조회 — OPS_RUNS의 `Run ID`는 rich_text가 아니라
   title이라 기존 `find_project()`를 재사용할 수 없음). `record_run()`과
   `drain_pending()` 둘 다 이걸 통해서만 행을 만들도록 교체.
   `InMemoryNotionTransport.query_database()`를 "Project ID rich_text"
   하드코딩에서 property명+filter타입 범용으로 일반화.
   `RecordRunRetryDuplicationTests`를 특성화에서 보증으로 재작성(fork의
   docstring 예고대로).

2. **`drain_pending()`의 실패 사유가 어디에도 안 남음.** `except
   Exception:`이 예외 텍스트를 통째로 버려서, 영구히 실패하는 오염된
   레코드(예: Notion이 거부하는 Select 값)가 `attempt_count`만 늘며
   원인 없이 영원히 재큐잉됐다 — BUG-13/C10이 Notion Sync에 이미 고친
   것과 같은 진단 공백이 Dashboard Pending Queue에는 남아 있었다.

   수정: `DrainPendingResult`(tuple 서브클래스, 기존
   `recorded, still_pending = drain_pending(...)` 2-tuple unpack을 그대로
   보존하면서 `.last_reason`만 추가 — `runsummary.RunResult`가 5-tuple에
   `.summary`를 더한 것과 같은 기법, C12) 신설. `app/runner.py`가
   `DRAIN_PENDING ... REASON <이유>`로 로그(bounded, 기존 REASON 로깅과
   동일 패턴).

   테스트: `tests/test_notion_client.py::NotionClientTitleLookupTests`(5건),
   `tests/test_notion_dashboard.py`의 신규/수정 테스트(4건),
   `tests/test_architecture_invariants.py::test_only_ops_runs_has_a_writer`
   갱신(호출부가 `create_project`→`find_or_create_by_title`로 바뀐 사실 반영).
   기존 `test_runner_failure_paths.py::DrainPendingPartialSuccessTests`의
   Fake Client 2종이 `create_project`만 구현하고 있어 함께 갱신(BUG-50
   특성화 자체는 변경 없음 — 여전히 BaseException은 새지 않음이 아니라
   샌다는 특성화가 유지됨, 재확인).

### Multi-Desktop — 미래 날짜 State가 영구 무음 정지를 만들고 탐지되지 않음

fork 조사로 확인: `agent.run_once()`는 `last_successful_collection_date`를
`now` 이후로 절대 쓰지 않지만(항상 `now` 상한), 시계 오차나 잘못된 백업
복원으로 미래 날짜가 State 파일에 들어갈 수 있다. `catchup.pending_dates()`는
이미 안전하게 처리한다(start > end → 빈 목록, 날짜를 건너뛰지 않음 —
`test_a_future_state_date_never_walks_backwards`). 그러나
`agent/status.py::needs_attention()`은 이 경우를 전혀 확인하지 않아서,
그런 State를 가진 Desktop은 `last_run`도 최근, outbox도 0, pending_dates도
0으로 **완벽하게 건강해 보인다** — 실제로는 시계가 그 미래 날짜에 도달할
때까지(몇 년이 걸릴 수도 있음) 다시는 수집하지 않는데도.

**수정(탐지 전용, `scheduler/consistency.py`·`history/reconciliation.py`와
같은 절제):** `needs_attention()`에
`last_successful_collection_date > now.date()` 확인 추가, ATTENTION 사유로
보고. `pending_dates()`의 안전한 방향(날짜를 건너뛰지 않음)은 전혀 건드리지
않음 — 여전히 아무것도 재처리·수정하지 않는다. 테스트:
`tests/test_observability.py`에 2건 추가(미래 날짜 신규 탐지 +
오늘 날짜는 오탐 아님 확인).

### 확인했으나 신규 결함 없음

- **A-19 보안 재감사** (Path Traversal, Hardlink, Log Injection, Secret
  노출, malicious event_id, Git Command Injection): fork로 `src/` 전체
  재확인, 기존 Sanitizer(`safe_event_filename`/`safe_candidate_filename`)와
  `oplog.append_line()` 단일 지점 escape+redact가 모든 경로를 덮고 있음을
  재확인. Junction(A-19 본항목)은 여전히 배포 정책 결정이라 **SKIP 유지**.
- **E-9/E-9b**: `agent/delivery.py`의 4종 탐지(EMPTY/NOT_A_FILE/UNREADABLE/
  DIFFERENT_EVENT)와 A-20류 동시성 오탐 가능성을 직접 코드 분석 — `send()`가
  sync 폴더에 원자적으로 쓴 **뒤에만** `sent/`로 옮기므로(A-20과 달리 배치
  전체 완료를 기다리지 않음) 동시 실행 시 오탐 창이 없음을 확인. 신규 결함 없음.
- **A-18 Backup/Dashboard 격리**: `finally`의 Lock 해제, Manifest 기록 순서
  재확인 — 기존 특성화(Dashboard row 유실, 재시도 큐 미도달)가 정확함을 재확인,
  이중 실패(Backup+Dashboard 동시) 경로도 도달 불가능함을 확인(Backup이 먼저
  중단시키므로 Dashboard 자체가 시작되지 않음 — 두 개의 독립 결함이 아니라
  하나의 순서 사실).
- **TODO/FIXME/HACK/XXX**: `src/`, 루트 스크립트, `scripts/` 전체 grep — 0건.
- **Recovery/DR 나머지 항목**: Working Copy 전체 파괴는 이미 완전히
  테스트됨(`WorkingCopyDestroyedTests`, Local Master 무손상 확인). Monthly
  History는 `monthly_history_state.json` 손실 시에도
  `generate_or_update_monthly()`가 상태가 아니라 **파일 존재 여부**로
  덮어쓰기를 막으므로 안전(`monthly/generator.py`) — 유일한 부작용은
  `dirty_months`도 함께 사라져 재생성이 다음 Late Event까지 지연되는 것뿐,
  데이터 손상 아님. State+대응 데이터 동시 손실은 `scheduler/consistency.py`의
  "state 없음 = 확인할 것 없음" 처리가 신규 설치와 같은 경로로 흡수함.

### Performance 재측정 (이 머신, Python 3.9.7)

C16이 Python 3.13에서 측정한 것과 같은 스레드풀(16) 구현을 그대로 재측정.
정확성 회귀 없음, 성능도 이전 기록과 동등하거나 더 빠름(디스크/캐시 차이로
추정 — 알고리즘은 변경하지 않았으므로 코드 개선이 아니라 환경 차이):

| n | reconcile (C16, py3.13) | reconcile (지금, py3.9.7) | delivery (C16) | delivery (지금) |
|---|---|---|---|---|
| 1,000 | 0.86s | 0.22s | 0.29s | 0.21s |
| 5,000 | 3.95s | 1.09s | 3.66s | 1.04s |
| 20,000 | 18.26s | 4.79s | 18.98s | 4.48s |

`test_agent_outbox_stress.py`를 `COMPANY_OPS_STRESS_N=5000`으로 재실행 —
11개 테스트 전체 통과, 정확성 단언 전부 유지(중복 0, 유실 0). 안전한 추가
최적화가 필요한 병목은 발견되지 않았다.

### Recovery/DR — Lock PID 재사용으로 인한 영구 정지 가능성 (탐지 신설)

두 번째 읽기 전용 조사 fork(Recovery/DR/E2E)로 신규 발견. `_is_process_running()`
(`scheduler/lock.py`)은 "지금 이 PID를 가진 프로세스가 있는가"만 확인하고
"그것이 Lock을 쓴 바로 그 프로세스인가"는 확인하지 않는다 — Lock 파일
payload에는 `process_id`/`created_at`만 있고 그 이상의 식별 정보가 없다.

**재현 시나리오:** 진짜 정전 등으로 Runner가 Lock을 쥔 채 죽으면 죽은
프로세스의 PID가 Lock 파일에 영구히 남는다. 재부팅 후 Windows가 그 숫자를
무관한 다른 프로세스에 재할당하면 `_is_process_running(dead_pid)`가 True를
반환해 `try_acquire_lock()`이 "다른 Runner가 실행 중"으로 오판 — docs/07
§27("경과 시간만으로 판단 안 함")을 지키는 한 사람이 Lock 파일을 수동
삭제할 때까지 **영구히** 모든 실행이 조용히 건너뛰어진다.

**왜 Lock 판별 로직 자체는 고치지 않았는가:** `LockFileContractTests`가
Lock 파일의 온디스크 형태를 정확히 `process_id`/`created_at` 두 필드로
고정해 둔 계약이고("다른 코드와 운영자가 의존하는 형태"), 식별 정확도를
높이려면 이 형태를 넓혀야 한다 — 계약 변경. 또한 `_is_process_running()`의
불확실 시 판정 방향(§27 관련, `ProcessProbeFailureTests`의 BUG-54: probe
실패 시 "죽었다고 가정"하는 관대한 방향)은 이미 "함께 결정해야 한다"고
문서화된 별개의 정책 트레이드오프다. 이 둘을 건드리지 않고는 진짜 수정이
불가능하므로 판별 로직 자체는 **SKIP**.

**대신 구현한 것(탐지/관측성, 계약 변경 없음):** `scheduler/lock.py`에
읽기 전용 `lock_held_since()` 신설 — Lock 파일의 **기존** `created_at`
필드만 읽어(새 필드 추가 없음) 살아있는 프로세스가 잡고 있는 Lock의 획득
시각을 반환한다. `ops_status.py::_print_last_run()`이 이를 이용해 Lock이
2시간(`LOCK_STUCK_AFTER_HOURS` — D절 측정상 20,000건 배치도 수 초, git
subprocess timeout도 300초이므로 충분히 넉넉한 값) 이상 잡혀 있으면
ATTENTION에 보고한다. Staleness를 판정하거나 Lock을 가져가지 않는다 —
진짜 장시간 실행이든 PID 재사용 오탐이든 "확인이 필요하다"는 사실만 알린다.
테스트: `tests/test_lock_atomicity.py::LockHeldSinceTests`(5건),
`tests/test_observability.py::LastRunLockStuckTests`(5건).

### 전체 Regression

수정 5건 + 신규/갱신 테스트 37건 반영 후 전체 스위트: **1466 passed, 0
failed** (수정 전 baseline 1430 passed / 1 failed — Python 버전 드리프트).

---

## C16. Delivery Integrity Sprint

### E-9b — `sent/`가 무엇을 의미하는지 코드로 확정

브리프의 질문은 *"'sent'가 실제 전달 성공을 의미하는가"*였다. 코드가 답한다:

```python
transport.send(event)      # 예외 안 나면
os.replace(path, sent_dir) # sent/로 이동
```

**`sent/`는 "`send()`가 raise하지 않았다"만 뜻한다.** 그리고
`OneDriveTransport.send()`는 목적지가 **어떤 형태로든** 이미 있으면 쓰지
않고 반환한다(`_write_atomic`의 `exists()` short-circuit). OneDrive
클라이언트는 그런 형태를 스스로 만든다 — Files On-Demand placeholder는
0바이트, 중단된 전송은 잘리고, 충돌 처리는 잔재를 남긴다.

### 탐지 가능성 — 결정적 발견은 "부재"의 취급

수정(sync 덮어쓰기)은 race 정책이라 SKIP. 그런데 **탐지는 가능한가**가
관건이었고, 답은 목적지의 형태에 달려 있었다. 6종을 실측:

| 목적지 상태 | `send()` | 판정 |
|---|---|---|
| 0바이트 | 통과 | **UNDELIVERED** (EMPTY) |
| 디렉터리 | 통과 | **UNDELIVERED** (NOT_A_FILE) |
| 무관한 내용 | 통과 | **UNDELIVERED** (UNREADABLE) |
| 다른 Event | 통과 | **UNDELIVERED** (DIFFERENT_EVENT) |
| 올바른 내용 | 통과 | clean |
| **부재** | 통과 | **clean — 보고하지 않음** |

**부재를 보고하지 않는 것이 설계의 핵심이다.** Desktop 4가 파일을 수거하면
목적지는 정당하게 사라지며 그것이 정상 상태다. 부재를 실패로 세면 건강한
배포마다 영구 경보가 뜨고, 정상 상황에 울리는 검사는 하루 만에 꺼진다.

`src/agent/delivery.py` 신설(탐지 전용) + `ops_status.py` 노출. 실 Agent로
E-9b를 재현해 끝까지 확인했다:

```
전달 정합성 : UNDELIVERED (확인 4건, 이미 수거됨 2건)
            ! d9b014f0-... [EMPTY]
ATTENTION: 전송 완료로 기록됐지만 sync 폴더에 도착하지 않은 Event 1건
```

### 탐지 정확도 — false positive 0 / false negative 0

실 runtime의 `sent/` 4건을 ground truth와 대조:

```
checked=4 absent=2 undelivered=1 clean=1
ARITHMETIC EXACT: True
```

2건은 실제로 수거됨(보고 안 함 — 정답), 1건은 실제로 0바이트(보고함 — 정답),
1건은 실제로 정상 배달(보고 안 함 — 정답).

### 신규 탐지기가 status 뷰를 망가뜨릴 뻔했다

`ops_status.py`는 "먼저 이것부터"라고 문서화된 뷰인데, C15/C16이 거기에
전체 디렉터리 스캔 **2개**를 추가했다. 측정:

| n | reconcile | delivery |
|---|---|---|
| 1,000 | 6.05s | 6.05s |
| 20,000 | **116.35s** | **117.67s** |

항목당 5.3ms이고 거의 전부 **파일 열기** 비용이다 — D절이 COO 상태 조회에
대해 이미 측정한 것과 같은 수치. 그때 채택한 해법(스레드 풀 16)을 같은
모양의 코드에 적용했다:

| n | reconcile | delivery |
|---|---|---|
| 1,000 | 0.86s | 0.29s |
| 5,000 | 3.95s | 3.66s |
| 10,000 | 8.57s | 9.16s |
| 20,000 | **18.26s** | **18.98s** |

**약 6배**, 그리고 20,000건 18초는 D절의 기존 기록과 일치한다. 실 runtime의
`ops_status` 전체 실행은 **0.23초**.

읽기 전용 경로에만 적용했다 — `outbox.drain()`/`run_intake()`는 순서와
파일별 실패 격리가 계약이라 직렬로 남긴 것과 같은 구분이다. 최적화가
정확성 검사 위에 얹히는 것이므로 **직렬 구현과 결과가 완전히 같은지**
비교하는 테스트를 양쪽에 붙였다(D절의 기존 선례와 동일한 가드).

---

## C15. Blast-Radius Sprint

승인이 필요한 항목들이 남았는데 결정에 필요한 **숫자가 없었다.** 이번
Sprint는 그 숫자를 만들었다 — 고치지 않고, 무엇을 잃는지 측정했다.

### A-20 — 실 runtime에서 유실 1건 탐지, 그리고 가시화

가설이 아니라 **이 저장소의 실제 runtime**에서 확인했다:

```
processed=17  keep=14  review=0
ORPHAN FI-CRASH-1 [KEEP] -> expected HIST-FI-CRASH-1.json
```

C14가 주입한 Event 하나가 그대로 남아 있었다 — `processed/`에 있고,
decision은 KEEP이고, Candidate는 없고, 재실행해도 복구되지 않는다.

**중요한 발견: 잔재가 사후 탐지 가능하다.** A-20의 *수정*은 계약 결정이지만
*탐지*는 아니다. `scheduler/consistency.py`가 정확히 같은 자세의 선례다 —
탐지만 하고 절대 고치지 않으며, 그 절제의 이유가 "무엇을 할지는 운영자
결정"이라고 그 모듈 docstring에 적혀 있다.

`src/history/reconciliation.py` 신설(탐지 전용) + `ops_status.py` 노출:

```
Candidate 정합성    : ORPHANED_EVENT (Event 17건 확인)
                      ! FI-CRASH-1 [KEEP]
ATTENTION: 수집됐지만 History에 들어가지 못한 Event 1건: FI-CRASH-1
           — 재실행으로 복구되지 않는다(BACKLOG A-20)
```

A-20이 "모르는 것"으로 기록한 **어느 Event가 사라졌는지**가 이제 답해진다.
유실 창 자체는 그대로 열려 있다(SKIP). 재처리·재수집·seen store 수정·파일
이동을 하지 않는다는 것을 테스트가 소스 수준에서 고정한다.

DROP은 원래 Candidate가 없으므로 보고하지 않는다 — 그러지 않으면 정상
상황이 고장으로 보이고, 그것이 이 검사를 무시하게 만드는 가장 빠른 길이다.

### E-9b / A-18 — 영향 범위 실측

둘 다 정책 결정이라 손대지 않았고, **잃는 것의 크기**만 쟀다. 상세는 각
항목 참조. 요약:

- **E-9b**: 실 Agent로 재현 — sync 파일 0바이트 그대로, `sent/`는 배달됨으로
  기록, state는 전진, 종료 코드 0, **경고 없음.** A-20과 같은 부류의 발신 측.
- **A-18**: Backup 예외로 실제 잃는 것은 **Dashboard row 1개뿐**이고,
  **재시도 큐에도 안 들어간다.** 데이터는 안전(Candidate 저장됨, Manifest
  기록됨). Manifest에 `dashboard` component가 아예 없어 건너뛴 사실조차 남지
  않는다(9개가 아니라 8개).

### 보존 정책 — 정확성은 안전, 비용은 성능

20,000 id에서 중복 판정 **정확**(false hit/miss 0), 비용은 5.7 ms/op.
무한 증가는 **정확성 문제가 아니라 성능 문제**이며, 하루 수십 건 규모에서
결정을 압박하는 요인은 없다.

### A-16 / A-17

재측정만: Dashboard DB 5종 중 write되는 것은 여전히 `OPS_RUNS` 하나,
`local_master`는 코드 3파일 12곳 + spec 12개 문서. 둘 다 변화 없음, SKIP 유지.

---

## C14. Re-verification Sprint

이전 Sprint의 결론을 **다시 측정**하는 것이 이번 주제였다. 세 종류가 나왔다:
참인 것, 거짓인 것, 그리고 참이지만 범위가 좁았던 것.

### 재측정 결과

| 이전 기록 | 재측정 |
|---|---|
| "Symlink 생성 불가(권한)" | **참** — `WinError 1314`로 실제 실패 |
| "Task 등록 불가" (C13에서 이미 반증) | 거짓이었음 |
| "BUG-20 유실 = 동시성 문제, 수정됨" | **범위가 좁았다** — 동시성 없이 재현됨 |

### 신규 A-20 — 동시성 없는 데이터 유실 창

BUG-20은 "3 Runner 동시 실행 시 Candidate 36% 유실"로 측정되고 Lock
원자성으로 닫혔다. 그 수정은 유효하다. 그러나 BUG-20이 지목한 셋째 결함은
**동시성이 아니라 파이프라인 순서의 성질**이다.

Runner 1개, lock 경합 0으로 재현: Collector가 event_id를 seen 처리하고
`processed/`로 옮긴 뒤 5단계 이전에 실행이 끝나면

```
processed/fi-crash.json      존재  (Event 파일은 살아남음)
history_candidates/keep/     비어 있음
다음 실행                     accepted=0  — 재검토 안 함
Daily History                없음, 영구적으로
```

**"BUG-20 fixed"는 트리거에 대해 참이고 유실 창에 대해 거짓이다.**
Run Manifest가 FAILED와 중단 Component는 알려주지만 **어느 Event가 사라졌는지는
말하지 않는다.** 닫으려면 Collector 계약 변경 또는 새 정합성 패스가 필요하므로
**SKIP**, characterization 4건으로 고정.

### 신규 A-19 — junction은 무권한으로 가능

BUG-57(junction traversal)은 "배포 정책 결정"이라 미수정으로 남아 있었다.
그 판단은 유지하되, 새 사실 두 가지를 측정해 붙였다:

- `os.walk(followlinks=False)`도 junction 안으로 **내려간다**(`followlinks`는
  symlink 전용) → "링크 추적 끄기" 같은 값싼 수정은 없다
- `os.path.isjunction()`이 **존재한다**(3.12+) → **탐지 불가라서가 아니라
  결정하지 않아서 열려 있다**

이 Sprint에서 실제로 거부를 구현해 유출이 막히는 것까지 확인했으나
(`BACKUP_FAILED`, 원격 유출 0), **정당한 저장소 레이아웃을 거부하는 정책
변경**이므로 되돌렸다. 좁은 수정이 가능한지도 확인했다 — junction은 디렉터리
전용이고 hardlink는 일반 파일과 구별 불가이므로 **없다.**

### 수정 — 지워지지 않는 거짓 ATTENTION

`transport.run_intake()`는 파싱 불가 파일을 **그 자리에 영원히 둔다** —
승격도 이동도 삭제도 하지 않고 매 실행 다시 판정한다. 그런데 backlog 뷰는
`transport/`의 모든 `*.json`을 세고 있었다.

0바이트 파일 1개(OneDrive Files On-Demand placeholder의 형태)로 측정:

```
run 1..4   transport metrics {'skipped_invalid': 1}   매 실행
ATTENTION  "수집되지 않고 남은 Event: transport=1"     영구히
```

그 문장은 "Event가 수집을 기다린다"는 뜻인데 거짓이다 — 판정이 끝났고
영원히 수집되지 않는다. **어떤 실행으로도 지워지지 않는 알림은 알림이
없는 것보다 나쁘다.** ATTENTION은 진짜 문제가 뜨는 곳이고, 상시 항목 하나가
그 절 전체를 흘려보게 만든다.

`unparseable`을 분리해 세고, 맞는 문장으로 보고한다. 판정은 `run_intake()`
자신의 파싱 테스트를 재사용한다 — 두 번째 의견을 만들면 뷰와 그 뷰가 보고하는
단계가 서로 어긋날 수 있고, 그것이 이번 Sprint가 찾으라고 지시받은 모순의
부류다.

### 장애주입 (신규 5종)

Monthly 실패(`MONTHLY_CONSOLIDATION_FAILED [DEGRADED/RETRYABLE]`, exit 3,
**History는 정상 기록**), Manifest 쓰기 실패(삼켜짐, 실행 정상 완료),
Manifest 실패 + Backup 실패 동시(보수적 fallback exit 2), State 불일치
(`STATE_INCONSISTENCY` + 파일 경로까지 ATTENTION에 표시, exit 3),
중간 Crash(4 component 기록 + `STEP_ABORTED [CRITICAL/UNKNOWN]`,
lock 정상 해제, exit 2).

첫 Crash 주입은 **vacuous**했다 — ACCEPTED Event가 없어 `evaluate()`가
호출되지 않았다. 주입이 실제로 도달했는지 확인하지 않으면 장애주입도
통과하는 것처럼 보인다.

---

## C13. Production Verification Sprint

코드가 아니라 **머신**을 검증했다. 결과적으로 이번 Sprint의 가장 큰 수확은
**기록된 사실 하나가 틀렸다는 것**이었다.

### 결함 1 — Installer는 어떤 비관리자 머신에서도 등록에 성공한 적이 없다

B-1은 "이 환경에서는 Task 등록이 불가능하다(비관리자 세션)"로 닫혀 있었고,
근거는 "빈 Task조차 `-User`/`-Principal` 변형 포함 똑같이 거부된다"였다.
**같은 머신에서 다시 측정하니 그 근거가 거짓이었다.**

| 시도 | 결과 |
|---|---|
| `cmd.exe /c exit` + Once / SettingsSet / `-Force` / `-Description` | **전부 등록됨** |
| `Daily -At 09:00` | **등록됨** |
| `AtLogOn -User <me>` | **등록됨** |
| `AtLogOn` (`-User` 없음) | Access is denied |
| `AtStartup` | Access is denied |

거부되는 것은 **machine-wide 트리거뿐**이다. Installer는 `-User` 없는
`-AtLogOn`을 썼다 — 인자 하나. 권한 문제가 아니었다.

`-User`는 등록 문제만 푸는 것이 아니다. 스코프 없는 `-AtLogOn`은 **아무
사용자나 로그온할 때** 발화하는데, Agent는 identity와 sync 폴더를 **user**
환경변수에서 읽는다. 다른 계정 로그온에 발화하면 설정이 하나도 없는 상태로
도는 것이었다 — **잠재 결함이 두 개였다.**

Installer의 진단 메시지도 틀렸다. "elevation이 1순위"라고 안내했으므로,
그 말을 따른 운영자는 **필요 없는 관리자 권한을 찾아다니며 이 파일의 버그를
고치려 했을 것이다.** 메시지를 정정하고, 순서를 바꾸고, 판별용 probe 명령을
넣었다.

**교훈: "불가능함이 확인됐다"로 기록된 측정은 근거보다 오래 산다.**
이제 트리거 스코프와 정정된 메시지를 테스트가 매 실행 재검증한다.

### 결함 2 — Manifest가 시스템의 나머지보다 부정확했다 (C12 자체 결함)

실제로 깨진 remote에 대고 돌려 보니, Manifest는 `STEP_ABORTED
[CRITICAL/UNKNOWN]`(generic fallback)을 기록하는데 `backup_state.json`은
BACKUP_PENDING, 운영자 메시지는 "다음 실행이 자동 재시도"라고 말하고 있었다.
**셋 중 Manifest가 가장 부정확했고, 그것이 `ops_status.py`가 읽는 것이다.**

ATTENTION은 `PERMANENT`만 올리므로, 이 경로로 들어온 **진짜 영구 인증 실패는
UNKNOWN으로 분류되어 영원히 아무에게도 안 보였을 것이다.**
`is_authentication_failure()`(docs/08 §21 규칙, `run_company_ops.py`가 이미
쓰는 것)를 재사용해 분류하고 그대로 re-raise한다.

### 결함 3 — 종료 코드가 Manifest와 불일치했다

위를 고치자 드러났다: Manifest는 DEGRADED/exit 3인데 프로세스는 2를 반환했다.
`_report_backup_failure()`가 2를 하드코딩하고 있었기 때문이다. **"이 실행이
얼마나 나빴는가"에 대한 답이 두 개였고, Task Scheduler는 그중 하나만 본다.**
Manifest에서 읽도록 바꿨다.

그 과정에서 파생 결함 하나 더: 그 함수가 **호출자가 지정하지 않은** 전역
경로를 읽고 있었다. 테스트에서 직접 호출하니 저장소의 **실제** Manifest
(SUCCESS)를 읽어 Backup 실패에 exit 0을 돌려줬다. 경로를 인자로 바꿨다.

### 결함 4 — Desktop identity 불일치가 traceback으로 나왔다

`COMPANY_OPS_PROFILE`을 바꾼 뒤 Agent를 돌리면 `AgentStateError`가 raw
traceback으로 나온다. Installer 자신의 문서가 "잘못된 -DesktopId로 preview하면
identity가 바뀐다"고 경고하는 바로 그 시나리오다. 예상된 운영 상황인데 화면은
시스템이 깨진 것처럼 보인다(C8이 Backup에 대해 고친 것과 같은 부류).
**state 파일은 절대 자동 수정하지 않는다** — 그렇게 하면 `ensure_desktop()`이
막으려는 바로 그 데이터 유실을 친절하게 수행하는 셈이다.

### 결함 5 — 캡처된 로그에서 stderr가 stdout을 추월했다

Python은 터미널이 아닐 때 stdout을 블록 버퍼링하고 stderr은 하지 않는다.
Task Scheduler가 실제로 쓰는 `>log 2>&1` 형태에서 **실패 메시지가 그것을
설명하는 문맥 줄보다 위에 찍힌다.** 3회 목격 후 격리 재현했고, 세 진입점
전부 `line_buffering=True`를 적용했다.

### 실환경 검증 결과

| 단계 | 결과 |
|---|---|
| Task Scheduler 등록 (비관리자) | **성공** — IgnoreNew, StartWhenAvailable, delay PT2M, user 스코프 |
| `Start-ScheduledTask` 실행 | **LastTaskResult=0** |
| Agent 실행 | DESKTOP_4/COO, 밀린 6개 날짜 Catch-up |
| Event 전달 | sync 폴더에 결정적 event_id로 도착 |
| 작업일/도착일 분리 | Signal 날짜(08-08) 유지, 실행일(08-11) 아님 |
| Transport 안정성 가드 | 방금 쓴 파일은 `skipped_not_stable` |
| Late Event 병합 | 이미 닫힌 08-08에 `## Late Events`로 추가, 원본 무변경 |
| Backup | commit + push, 무변경 3회 실행 시 **중복 commit 0** |
| Run Manifest | 9 component, SUCCESS/exit 0 |
| ops_status LAST RUN | 표시 확인 |
| **DR 복구** | bare remote clone → 6개 Daily 파일 **byte-for-byte 동일** |

### 장애주입 11종 — 전부 통과

중복(transport층 `skipped_already_present`, collector층 `duplicate`),
0바이트, 부분 파일, 잘못된 role/source, OneDrive 충돌 사본, push 실패(일시),
push 실패(영구=Secret Scan), transport 실패, Desktop identity 불일치,
동시 Runner 4개, 순서 역전.

- **데이터 유실 0, 중복 History 0, 크래시 0**
- 거부된 파일은 전부 `rejected/`에 사유와 함께 보존
- 동시 4개 중 1개만 작업, 3개 SKIPPED(exit 0), lock 정상 해제
- Outbox: 전송 실패 시 Event 보존 + `last_successful_collection_date`
  **전진 안 함** → 복구 후 재전송 → outbox 비워짐
- 순서 역전: 늦게 배달된 오래된 날짜 Event도 **자기 작업일** 파일로 감

---

## C12. Run Contract Sprint

C10이 "삼킨 실패가 로그에 남지 않는다"를, C11이 "이 경로에 있는 것은 우리
것이다"를 다뤘다. 이번 것은 그 둘이 계속 부딪히던 벽이다: **실행 결과를 담을
그릇이 없었다.**

BUG-39(23개 필드 중 14개가 계산된 뒤 버려짐)와 BUG-36(무엇을 출력했든 항상
exit 0)은 따로 발견된 두 결함이 아니라 **같은 빈자리의 두 증상**이었다.
Run Manifest 하나가 둘을 동시에 없앤다.

### 먼저 실측했다

새 폴더를 만들기 전에 기존 산출물을 전수 조사했다. 신설한 것은
`runtime/runs/last_run.json` **하나뿐**이고, 나머지는 전부 파이프라인이 이미
쓰고 있던 경로를 `artifact_ref`로 가리킨다.

| 분류 | 실측된 경로 |
|---|---|
| Company Repository | `local_master/daily|monthly/`, `backup_working_copy/` |
| Execution Evidence | `logs/*.log`, `agent/logs/`, `events/*/`, `history_candidates/*/` |
| Operational State | `state/*.json`, `locks/` |
| Operational Projection | Notion PROJECTS / OPS_RUNS |
| **Run Manifest** | `runs/last_run.json` ← 신설 |

### Component / Overall Status

`SKIPPED`가 실패가 아니라는 것이 설계의 핵심이다. Notion 미설정은 지원되는
배포 형태이므로(docs/04), 실패로 보고하면 **Notion 도입 전의 모든 설치가
영원히 고장난 것으로 보인다.**

`DEGRADED`는 없던 값이다. 그것이 없으면 모든 실행이 "정상 아니면 고장"인데,
이 파이프라인의 설계 자체가 대부분의 실패는 둘 다 아니라는 것이다(RULE 5·9).
SUCCESS로 접으면 실제 고장이 숨고, FAILED로 접으면 아무도 안 보게 된다.

### Failure -> Classification -> Severity/Retryability -> Overall -> Exit

Severity와 Retryability를 **분리**했다. 서로 다른 질문에 답하기 때문이다 —
Severity는 Exit Code를, Retryability는 "운영자가 지금 움직여야 하는가"를
정한다. 둘을 합치면 BUG-13이 된다.

| Exit | 뜻 |
|---|---|
| `0` | SUCCESS |
| `2` | FAILED — CRITICAL Component 실패 |
| `3` | DEGRADED — 사람이 확인해야 함 (`ops_status.py`와 같은 뜻) |
| `1` | 설정 오류 전용, Run Contract가 쓰지 않는다 |

이전 docstring이 열어 두었던 질문 — "`collector_summary.failed > 0`에 non-zero를
주면 평범한 malformed Event가 시스템 장애로 보인다" — 은 중간값과 Severity가
함께 답한다. malformed Event는 SUCCESS Component의 **metric**이므로 Exit Code를
전혀 바꾸지 않는다.

### 반환 계약을 깨지 않았다

`run_once()`의 반환값을 5-tuple에서 늘리면 **219개 호출부**가 깨진다(실측).
`RunResult(tuple)`로 만들어 5-way unpacking과 index 접근을 그대로 두고
`.summary`만 더했다.

### 중단된 실행에도 Manifest가 남는다

`finally`에서 쓴다. 5단계에서 죽은 실행은 이전에 `run_company_ops.py`가 보고
코드에 **도달하지도 못해 아무것도 출력하지 않았다.** 예외를 던진 단계는
추측하지 않고 `recorder.current`로 귀속시키며, 예외는 그대로 전파된다 —
`run_once()`가 흡수해야 하는지는 BUG-4이고 여기서 결정하지 않았다.

Lock 미획득 실행은 Manifest를 쓰지 않는다. 한 일이 없고, 실제로 일한 직전
실행의 기록을 빈 것으로 덮어쓰면 안 되기 때문이다.

### Event Contract는 그대로다

두 반쪽을 함께 고정했다(`EventContractPreservationTests`): 스키마는 개행
`event_id`를 **계속 받아들이고**, 로그는 **계속 escape한다.** 로그 서식 문제를
"받아들이는 것을 바꿔서" 푸는 것은 비용을 이미 보낸 Desktop 쪽으로 미루는 것이다.

### 가드가 스스로 일했다

이번 Sprint에 **내가 지난 Sprint에 만든 가드 3개가 내 변경을 잡았다.**

| 가드 | 잡은 것 |
|---|---|
| `LayeringInvariantTests` | 새 `runsummary` 패키지에 계층 선언 없음 |
| `RunnerDrivingTestGuard` | 8개 테스트 모듈이 저장소의 **진짜** `runtime/runs/`에 기록 |
| README 문서 목록 가드 | `docs/14` 미등록 |

세 번째는 C11에서 "기록"을 "가드"로 바꿔 둔 것이 바로 이번에 값을 했다.
두 번째는 실제로 오염이 발생한 뒤 잡았다 — 정리하고 8개 모듈 전부 리다이렉트했다.

### 신설/변경

- `src/runsummary.py` — 어휘와 계산. `oplog`처럼 project import 0개인 leaf.
- `src/app/runner.py` — 9개 Component 기록, Manifest 작성.
- `ops_status.py` — `LAST RUN` 절 신설. 마지막 실행이 무엇을 했는지 볼 곳이
  이전에는 없었다. `PERMANENT` 실패만 ATTENTION에 올린다 — `RETRYABLE`을
  올리면 스스로 사라지는 상시 항목이 되어 그 절을 무시하게 만든다.
- `docs/14_RUN_CONTRACT.md` — 계약 문서.

---

## C11. Trust-Boundary Sprint

C10의 후속. 주제는 **"이 경로에 이미 있는 것은 우리 것이다"라는 가정**이다.
세 곳에서 그 가정이 틀렸고, 세 곳 모두 조용히 틀렸다.

- **collector.log도 위조 가능했다 — 재현 후 수정.** C10은 `app/runner.py`만
  고쳤고, 같은 결함이 남았는지는 "확인 자체가 다음 작업"으로 남겨 뒀었다.
  확인했더니 남아 있었고, **더 나빴다.**

  ```
  2026-08-11T13:40:02+09:00 ACCEPTED X
  2026-01-01T00:00:00+09:00 ACCEPTED EVT-TOTALLY-FINE     <- 위조된 줄
  ```

  Runner 쪽 위조가 "동기화 결과"를 꾸며냈다면 이쪽은 **수집 결과 자체를**
  꾸며낸다 — 존재한 적 없는 Event가 ACCEPTED로 기록된다. 원인도 같다:
  `_log(log_path, f"ACCEPTED {result.event.event_id}")`가 raw `event_id`를
  그대로 넣는다.

  옆줄인 `REJECTED`는 이미 안전했지만 **우연히** 그랬다 —
  `events/schema.py`가 `{value!r}`로 포맷하고 `repr()`이 개행을 escape한다.
  앞으로 추가될 모든 필드가 그렇게 포맷되기를 기대하는 것을 그만두는 것이
  이번 변경이다.

- **로그 writer 3벌 → `src/oplog.py` 1벌.** `collector/runtime.py`,
  `agent/agent.py`, `app/runner.py`가 같은 6줄을 각자 갖고 있었고 escape는
  그중 **하나에만** 있었다. 최상위 모듈인 이유는 계층 때문이다: `collector`와
  `agent`가 쓰고 `app`이 그 둘에 의존하므로, 어느 패키지 안에 두든 cycle이
  생기거나 `collector`가 `app`을 import해야 한다. `oplog`는 project import가
  0개인 leaf이며, 그 사실을 subprocess로 검증한다(`test_oplog.py`).
  `review_cli.py`가 최상위 모듈의 기존 선례다.

- **BUG-47의 최악 facet 수정 — 잘못된 데이터가 Desktop 4로 갔다.**
  `send()`는 Event를 `outgoing/`에 stage한 뒤 **그 파일을 다시 읽어** sync
  폴더로 복사했다. `outgoing/`에 이전 실패의 잔재가 있으면 stage 쓰기가
  short-circuit되어 **잔재가 Event 대신 배달**됐고, `send()`는 None을 반환하며
  예외도 없으므로 Agent는 성공으로 기록하고 날짜를 넘겼다.

  이전 Sprint가 미수정으로 둔 이유는 "재전송이 OneDrive가 읽는 중인 파일을
  덮어쓰는 race"였다. 그 논리는 **sync 폴더에만** 적용된다. `outgoing/`은 이
  코드가 만들고, OneDrive가 건드리지 않고, **아무것도 스캔하지 않는다**(그들
  자신의 테스트가 단언한다). 즉 race가 존재하지 않는 디렉터리에 race 논리를
  적용하고 있었다.

  수정: staging은 overwrite하고, sync에는 `event.to_json()`을 직접 쓴다.
  **sync 폴더 skip은 건드리지 않았다** — 나머지 facet(디렉터리·0바이트
  placeholder·무관한 내용이 목적지에 있는 경우)은 그대로 특성화돼 있고,
  그 skip을 좁히는 것은 race에 대한 결정이지 정리가 아니다.

- **C10이 만든 §56 위반을 발견하고 막았다 — 실제 유출 재현.**
  C10이 추가한 ` REASON <이유>`는 **원격 응답 본문**이다. Notion 자신의 JSON은
  토큰을 담을 수 없지만(토큰은 *요청* 헤더로 간다), Notion 대신 응답하는
  프록시/캡티브 포털은 요청 헤더를 되돌려줄 수 있다 — `notion/transport.py`가
  다른 맥락에서 이미 상정하는 상황이다.

  기존 §56 테스트는 **성공** 경로만 검사했고(닫힌 값만 들어가는 줄), 새 필드는
  **실패** 경로에만 쓰인다. 즉 보장이 필요해진 바로 그 지점이 미검증이었다.
  실패 경로 테스트를 쓰자마자 유출이 재현됐다:

  ```
  ... NOTION_RESULT NOTION_RETRY_REQUIRED REASON Notion API returned 502:
  Bad Gateway | <html>...Authorization: Bearer ntn_***</html>
  ```

  범위 한정: `runtime/logs/`는 gitignore되고 Backup은 `local_master`만 다루므로
  **GitHub까지 가지는 않는다.** 로컬 평문 파일에 자격증명이 남는 문제다.

  수정: `append_line()`이 escape 다음에 redact한다 — escape와 같은 논리로
  쓰기 지점 1곳에 둔다("나중에 추가될 필드가 잊을 수 없도록"). 비밀 패턴은
  `agent/signals.py`에서 `oplog.py`로 내렸다. 소비자가 둘이 됐기 때문이고
  (Signal *내용* 거부 / 로그 *출력* 제거), `agent`는 이미 `oplog`를 import하므로
  edge가 늘지 않는다 — 반대 방향이면 cycle이 된다. `agent.signals`가 원래
  이름으로 재노출하므로 Signal 검증 계약은 한 글자도 바뀌지 않았다.

  부수 확인: 저장소 hygiene 가드가 **내 새 테스트 파일을 잡았다** — fixture가
  진짜 자격증명처럼 생겼기 때문이다. 가드가 옳다(스캐너에게도, diff를 읽는
  사람에게도 구별되지 않는다). 기존 관례대로 문자열 연결로 바꿨다.

- **Vacuous-pass 16건 수정.** `for py_file in <pkg>_src.glob("*.py"):` 형태의
  경계 테스트가 8개 파일에 복제돼 있는데, **glob이 비면 규칙을 0개 파일에
  적용하고 통과한다.** 패키지 이름만 바꿔 실증했다: "files actually checked: 0,
  verdict: PASS". `assertNotIn`으로 텍스트를 검사하는 3건도 같다 — 빈 문자열은
  모든 `assertNotIn`을 만족시킨다. 전부 비어있지 않음을 먼저 단언하도록 고쳤고,
  가드가 실제로 실패를 일으키는지 패키지를 가짜 이름으로 바꿔 확인했다.

- **`LayeringInvariantTests` 신설** — 위 경계 테스트들이 구조적으로 줄 수 없는
  두 가지 때문이다.

  | 성질 | 패키지별 테스트 | 신규 |
  |---|---|---|
  | 완전성 | 파일을 만든 사람이 복사했을 때만 | `SRC.iterdir()`에서 유도 |
  | 비순환성 | 불가능(그래프의 성질) | 경로까지 보고 |

  허용 목록을 "금지"가 아니라 **"허용"** 표로 적었다. 금지 목록은 아무도
  금지할 생각을 못 한 것을 조용히 허용하며, 그것이 새 패키지가 빠져나가는
  방식이다. 실제로 `oplog.py`가 추가됐을 때 아무것도 실패하지 않았다.
  표가 실제보다 **넓지도** 않은지도 검사한다 — 쓰이지 않는 허용은 이유 없이
  준 권한이고 누군가 그 import를 추가하는 날 조용히 승인한다.

- **`DependencyGuardTests.LOCAL_PACKAGES` 하드코딩 제거.** 서드파티 import를
  잡는 가드가 first-party 목록을 손으로 관리하고 있었고, `oplog.py` 추가 시
  정확히 그 방식으로 실패했다. 디스크에서 유도한다 — `src/` 아래 있는 것은
  정의상 first-party이므로 오래된 목록은 가드를 엄격하게 만들 수 없고 틀리게만
  만든다.

- 감사 결과 **결함 없음**으로 확인된 것: `src/` 전체에 dead parameter 0건
  (유일한 후보 `SeenEventStore.is_seen(event_id)`는 abstractmethod —
  탐지기의 false positive), assertion 없는 테스트 6건은 전부 "raise하지
  않는 것"이 단언인 정당한 형태.

---

## C10. Runner Diagnostics Sprint

주제 하나로 묶인다: **실패를 삼키는 것과 실패를 감추는 것은 다르다.** 이
시스템은 의도적으로 대부분의 실패를 삼킨다(README RULE 5·9 — Notion이 죽어도
Company History는 계속 기록된다). 그런데 삼킨 실패가 **어디에도 남지 않는**
곳이 여러 군데 남아 있었다. Runner는 Task Scheduler 뒤에서 도는 것이
정상이므로 stdout은 아무도 보지 않고, 반환값은 프로세스 종료와 함께 사라진다.
로그에 없으면 존재하지 않은 것과 같다.

- **Daily Close 실패 지점이 계산되고 버려졌다 (BUG-39의 가장 날카로운 조각).**
  `SchedulerRunResult`는 어느 날짜에서 멈췄는지(`failed_date`)와 왜
  멈췄는지(`error`)를 항상 채워서 돌려주는데 **읽는 곳이 하나도 없었다.**
  운영자가 보는 것은 `FAILED, generated=[]` 한 줄뿐 — 그 다음에 할 수 있는
  일이 없는 메시지다.

  Scheduler는 첫 실패 날짜에서 멈추므로(docs §30, 순서에 구멍을 내지 않기
  위해) 그 날짜와 이후 날짜는 아직 Daily 파일이 없다. 즉 버려진 두 필드가
  **다음 실행이 어디서부터 이어야 하는지를 아는 유일한 근거**였다.

  `app/runner.py`가 `daily_late_update.log`에 `SCHEDULER_FAILED date=...`로
  기록하고, `run_company_ops.py`가 stderr에 출력한다. 새 artifact도 새 형식도
  만들지 않았다 — BUG-39가 말하는 "통합 run summary sink"는 형식·위치 결정이
  필요한 별건이고 **여전히 열려 있다.**

- **Dashboard(= 이 시스템의 metrics sink) 실패가 통째로 사라졌다.**
  `except Exception: pass`. 게다가 Dashboard는 실패해도 조용한 것이
  자기은폐적이다 — "기록이 멈춘 것을 알아차릴 곳"이 바로 멈춘 그것이기
  때문이다. 세 경로가 아무 sink에도 닿지 않았다.

  | 경로 | 이전 | 지금 |
  |---|---|---|
  | `record_run()` → FAILED | 조용히 재큐잉만 | `DASHBOARD FAILED run_id=...` |
  | 예기치 못한 예외 | `pass` | `DASHBOARD FAILED (unexpected) ...` |
  | pending backlog 누적 | `drain_pending()` 반환값 폐기 | `DASHBOARD DRAIN_PENDING drained=N still_pending=M` |

  정상 실행은 한 줄도 쓰지 않는다 — 매 실행 잡음을 더하면 위 세 줄이 눈에
  띄지 않게 된다.

- **Notion Sync 실패 이유가 로그에 없었다.** `NOTION_RETRY_REQUIRED`는
  **저절로 나을 503과 영원히 안 나을 400을 같은 한 단어로 보고한다**(BUG-13).
  그 둘을 가르는 문장은 `SyncResult.error`에만 있었고, 그 값은 stdout과
  반환 tuple에는 닿지만 로그에는 닿지 않았다. 즉 스케줄러 뒤에서 도는
  **실제 운영 방식일 때만 사라졌다.** Retry Queue가 같은 Event를 매 실행
  재전송하는 것을 보면서도 이유를 알 수 없던 것이 이 누락이다.
  실패한 Sync에만 ` REASON <이유>`를 덧붙인다(docs/04 §55는 "최소"를 정한다).

- **로그 주입 (BUG-6) — 특성화만 되어 있던 것을 고쳤다.** `event_id`에 개행을
  넣으면 진짜와 구별할 수 없는 **두 번째 로그 줄이 만들어졌다.** Event는
  OneDrive를 건너 다른 Desktop이 쓰는 파일이고 `Event.from_json()`은
  `event_id`를 한 줄로 제한하지 않으므로 실제로 신뢰할 수 없는 입력이다.
  로그는 운영자가 "시스템이 정상인가"를 판단하는 기록이므로 위조된 줄은
  장식이 아니라 **시스템이 한 일에 대한 거짓 진술**이다.

  쓰기 지점 한 곳(`_one_line()`)에서 escape한다. 개행만이 아니라
  `str.splitlines()`가 끊는 문자 전부다 — `\v \f \x1c-\x1e \x85  
   ` 7종을 빼면 같은 위조를 하는 다른 방법이 그대로 남는다. 역슬래시는
  일부러 이중화하지 않았다(Windows 경로가 오류 문자열에 상시 등장한다).
  Event를 아예 거부하는 쪽이 더 깊은 수정이지만 그것은 Event Schema 계약
  변경이라 **범위 밖**이다.

- **로그에 쓰는 오류 문자열에 상한이 없었다.** `except Exception` 경로의
  `str(exc)`는 어떤 층도 자르지 않는다. 이제 규칙이 하나다: *이 모듈이 로그에
  쓰는 문자열 중 무한한 것은 없다*(`_MAX_LOG_ERROR = 600`).
  `notion/transport.py`의 400자 본문 상한보다 **크게** 잡았다 — 같거나 작으면
  다른 층이 이미 자른 문자열을 다시 잘라 어느 쪽이 잘랐는지 알 수 없게 된다.

- **테스트 double이 실제 API보다 관대했다.** `InMemoryNotionTransport`가
  `database_id`를 완전히 무시해 모든 page를 한 통에 담았다. 그런데 운영은
  transport **하나**를 PROJECTS와 OPS_RUNS 두 Client가 공유한다
  (`run_company_ops.py`) — 실제 Notion은 id로 격리하지만 이 double은 하지
  않았다. 즉 **운영 배선을 그대로 흉내 낸 테스트**(가장 자연스럽게 쓸 법한
  테스트)가 OPS_RUNS 행을 PROJECTS 조회 결과로 받고도 **조용히 통과**한다.
  "모든 것이 발견되는" 방향으로 틀린 double은 누락된 쓰기를 감추는 쪽이라
  위험하다. 지금은 격리하며, 오늘 그 함정을 밟는 테스트는 없었다(확인함) —
  그래서 지금 고칠 가치가 있었다.

- 잔가지: `monthly/generator.py`의 미사용 import 1건(`MonthlyState` —
  패키지는 `.state`에서 따로 재노출한다), `_FAILED_SYNC_STATUSES` 중복 튜플,
  로그 기록 함수 3벌 → `_append_log_line()` 1벌.

- 드리프트 가드 2건 추가: 역할 표시명 표 2벌(`daily/markdown.py`,
  `notion/properties.py`)이 `events.ROLES`를 덮는지. 둘은 서로 다른 Spec에
  답하므로 **합치지 않았다** — docs/04가 Notion Owner 라벨을 바꿔도 docs/06의
  Markdown 제목은 그대로여야 한다. 합치면 두 문서가 요구한 적 없는 결합을
  만든다. 위험한 것은 두 표가 서로 달라지는 것이 아니라 **둘 다 `ROLES`에서
  뒤처지는 것**이고(`.get(role, role)` fallback이라 조용히 원문이 렌더된다),
  A-1(다섯 번째 Role 요청)이 열려 있으므로 가설이 아니다.

- 문서: README §12 문서 목록이 실재하는 spec 2개를 빠뜨리고 있었다(BUG-12의
  일부). 목록은 규칙이 아니라 디스크 상태 서술이므로 채웠고, 테스트를
  "빠져 있다"는 기록에서 `docs/*.md` 전수 대조 **가드**로 바꿨다.
  AGENT.md에 §6a "Desktop 4 로그에서 실패를 찾는 법"을 추가했다 — 위에서
  추가한 줄들이 각각 무슨 뜻이고 무엇을 해야 하는지의 표.

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

### 보존 정책 안전성 (C15 측정)

`processed/`·`transport/`·`sent/`·`collector_state.json`이 무한히 커진다는
것은 기록돼 있었으나(A절 6번), **커지면 무엇이 깨지는가**는 측정된 적이
없었다. 정책 결정에 필요한 것은 그것이다.

현재 규모(이 저장소의 검증 runtime):

| 저장소 | 파일 | 크기 |
|---|---|---|
| `processed/` | 17 | 6.8 KiB |
| `rejected/` | 4 | 0.3 KiB |
| `agent/sent/` | 3 | 1.3 KiB |
| `collector_state.json` | id 16개 | 0.5 KiB |

seen-store를 20,000 id까지 키워 **정확성**을 확인했다:

```
20000 mark_seen : 114.35s  (5717 us/op)
lookups         : 정확 — hit 전부 맞고 miss도 맞음
state size      : 371 KiB
```

**결론: 무한 증가는 정확성 문제가 아니라 성능 문제다.** 20,000건에서도
중복 판정은 정확했고 false hit/miss가 없었다. 비용은 쓰기 시간이며 A-6b의
초선형 쓰기 증폭이 재확인된다. 하루 수십 건 규모에서 20,000건에 닿으려면
수십 년이 걸리므로 **보존 정책 결정을 압박하는 요인은 없다.**

### 로그 쓰기 비용 (C10 측정, C11 갱신)

Body 168자(실제 `NOTION_RESULT ... REASON ...` 줄) 기준.

| 대상 | C10 | C11 |
|---|---|---|
| `one_line()` (위조 방지 escape) | 25.7µs | 23.2µs |
| `redact()` (§56 비밀 제거) | — | **74.7µs** |
| `append_line()` 전체 (+ open/write/close) | 252.1µs | **305.3µs** |

**C11이 로그 쓰기를 21% 느리게 만들었다.** 숨기지 않고 적는다. redaction은
7개 대안을 가진 대소문자 무시 정규식이라 escape보다 3배 비싸고, 로그 쓰기의
24.5%를 차지한다.

받아들인 이유는 절대값이다. 코드가 최악으로 명시하는 "Notion 장애로 800건이
큐에 밀린 실행" 기준 로그 쓰기 총합이 202ms → 244ms다. Runner 1회 실행은 초
단위이고, 비용은 여전히 파일 I/O가 지배한다. 42ms를 아끼려고 §56 위반 가능성을
남기는 거래는 성립하지 않는다.

최적화(사전 필터 등)를 넣지 않았다 — 측정이 문제를 보여주지 않았고, 이 저장소는
측정 없는 최적화를 하지 않는다. 이 표는 나중에 로그량이 크게 늘면 다시 볼 지점이
어디인지 기록해 두는 용도다.

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

### E-7. Run Summary Artifact — **완료 (C12)**

승인된 설계로 구현했다. BUG-39의 12개 잔여 필드 중 8개가 Manifest에 들어갔고,
나머지 4개(`run_id`/`backup_start`/`backup_end`/`source`)는 Manifest가 run
수준에서 이미 갖고 있어 **일부러** 복사하지 않았다 — 내려 적으면 두 값이
어긋나는 날 Manifest가 자기 자신과 불일치한다.

종료 코드 결정(BUG-36)도 함께 해소됐다. 남은 관련 항목은 A-18(BUG-4)뿐이다.

### E-8. 승인 없이 가능한 후속 (작음)

C11에서 처리 완료: `_one_line()`을 collector·agent에 적용(위조 재현 후 수정,
`src/oplog.py`로 통합).

남은 것:

- `humanize_project_id()` / `_display_project_name()`이 같은 한 줄
  (`project_id.replace("_", " ").title()`)의 두 벌이다. 역할 표시명 표와 같은
  이유로 합치지 않았고(서로 다른 Spec에 답한다), 표와 달리 드리프트해도 조용히
  틀리지 않는다(표시용 문자열뿐). 우선순위 낮음.
- 패키지별 경계 테스트 8쌍이 `LayeringInvariantTests`와 상당 부분 겹친다.
  C11은 **지우지 않았다** — vacuous-pass만 고쳤다. 지우는 것은 커버리지
  근거를 줄이는 변경이라 별도 판단이 필요하고, 파일별 지역성에도 값이 있다.

### E-9. BUG-47 sync 폴더 facet (Reliability) — 승인 필요

C11이 `outgoing/` 쪽(race 없음)을 고쳤다. sync 폴더 쪽은 남아 있다.

| 목적지 상태 | 현재 동작 |
|---|---|
| 디렉터리 | 배달된 것으로 간주, 성공 보고 |
| 0바이트 placeholder (OneDrive Files On-Demand) | 위와 같음 |
| 무관한 내용 | 위와 같음, 내용 보존 |

**왜 승인이 필요한가:** skip을 좁히려면 "OneDrive가 읽는 중일 수 있는 파일을
언제 덮어써도 되는가"를 정해야 한다(Phase 5.15의 staging buffer가 존재하는
이유). `is_file()`+크기 확인, 내용 비교, 또는 conflict-copy 규약 중 무엇을
택할지가 결정이고, 셋 다 실환경 OneDrive 동작에 대한 가정을 포함한다 —
B절이 "실제 OneDrive 클라이언트 고유 동작"을 미검증으로 남겨 둔 바로 그
부분이다.

완화되어 있는 점: 배달 내용이 **틀리는** 경우는 C11이 없앴다. 남은 것은
"배달되지 않았는데 성공 보고"이며, `ops_status.py`의 도착 추적이 침묵을
드러낸다(단, 원인까지는 말하지 못한다 — A-11c).

### E-10. `IntakeBacklog`의 Desktop별 귀속 부재 (Observability) — 승인 없이 가능, 작음

C18의 Multi-Desktop 상호작용 감사에서 발견. `app/desktop_activity.py`의
`IntakeBacklog.rejected`/`awaiting_intake`는 전체 Desktop 합산 카운트이고
어느 Desktop이 원인인지 귀속하지 않는다. 여러 Desktop이 동시에 Signal을
거부당하는 상황에서 운영자는 "총 N건 거부"까지만 보고, 어느 Desktop인지는
`runtime/events/rejected/` 또는 각 Desktop의 `signals_rejected/`를 직접
열어야 안다.

버그가 아니다 — 파일 자체는 검사 가능하고 데이터 유실도 없다. 정책 결정도
필요 없다(순수 집계 방식 변경). 구현하지 않은 이유는 순수 우선순위: 이번
Sprint의 병렬 감사 5건이 전부 "결함 없음"으로 끝난 뒤 남은 유일한 개선
후보이고, `rejected/` 파일들이 항상 `source`를 파싱 가능한 형태로 담고
있는지(완전히 손상된 JSON은 못 열 수도 있음) 확인이 먼저 필요하다.
다음 Sprint에서 가장 먼저 볼 만한 작은 항목.
