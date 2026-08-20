# Company Ops — Backlog

이 파일은 Spec이 아니다. 승인 없이 진행할 수 없어 **SKIP한 항목**과, Audit
과정에서 발견했지만 이번 범위를 벗어난 항목을 기록한다.

문서 우선순위(README §13)는 변하지 않는다: 여기 적힌 내용이 `README.md`나
`docs/` 명세와 충돌하면 명세가 이긴다. 이 파일은 "아직 결정되지 않은 것"의
목록일 뿐이다.

마지막 갱신: 2026-08-20 (C49 — **하나의 Model, 두 소비자.** C48이 화면을 Dashboard Model로
옮겼다면 이번 기준은 **"Notion으로 나가는 것도 같은 모델에서 나온다"**였다.
(1) **Notion 행이 아직 갈라져 있었다** — C48이 count는 하나로 만들었지만 `Desktops Reporting`의
**표현**은 여전히 `app/runner.py`가 rollup에서 조립했다. 화면은 Model, 행은 rollup. 둘을 맞춰
두는 것이 사후 비교 테스트 하나뿐이었다. `src/controltower/projection.py` 신설 —
`ops_runs_fields(model)`가 그 두 열을 만들고 Runner는 부르기만 한다.
(2) **rich_text 상한이 없었다** — `Desktops Reporting`은 `events.SOURCES`와 함께 자라라고
rich_text 하나로 둔 열인데(C47), Notion의 2,000자 한도를 넘으면 행 전체가 400으로 거절된다.
`RICH_TEXT_LIMIT` + **보이는** 절단(`…`).
(3) **Coverage — "무엇에 대해"가 모델에 없었다** — 복원된 머신은 Company History는 전부,
Event는 하나도 갖지 않는다. 그 상태에서 일곱 패널은 전부 0을 보고하고 **그것은 사실이다**.
지금까지 그 단서는 `ops_status.py`가 계산해 화면에만 찍는 한 줄이었고, projection은 다시
파생해야 했다. `Coverage`(`evidence_from`/`to`, `unreadable`, `history_uncovered_from`,
`complete`)를 모델에 넣고 화면이 답을 **모델로 되돌려준다**(`with_history_coverage()`).
(4) **새 게이트가 즉시 자기 값을 했다** — C48 마지막에 넣은 테스트 클래스 인용 게이트가
`projection.py`가 아직 없는 테스트 두 개를 인용한 것을 잡았고, fresh run은 BACKLOG 산문 속
placeholder 하나도 잡았다.
(5) `contracted_columns()`를 만들었다가 **지웠다** — 호출자가 테스트뿐이었다.
(6) ATTENTION 불변식을 property로 고정 — 이 블록이 찍는 ATTENTION 줄은 **RISKS 패널의 행
개수와 정확히 같다**(무작위 40 seed).
(7) **Dashboard 전용 계산이 CRITICAL 단계 앞에 서 있었다** — C48이 5단계 직후에 둔 rollup은
Daily/Monthly/Backup보다 앞이고 감싸는 `except`가 없었다. Dashboard 단계 안으로 옮겼다.
Dashboard 미설정 배포는 이제 아예 계산하지 않는다.
(8) **`days_silent`만으로 stale View를 만들면 가장 걱정스러운 Desktop을 빠뜨린다** — 한 번도
보고한 적 없는 Desktop은 값이 `null`이다. COMPANY 블록은 이미 `None`을 포함하는데, 그 규칙이
문서에 없었다. `docs/13` §3-⑨-1에 조건을 적고 테스트로 고정했다.
(9) **실행된 적 없는 분기 여섯 곳** — `src/notion/`에 분기 커버리지를 걸어 전부 도달 가능한
방어였음을 확인하고 덮었다(빈 `Last Event ID`, update 실패, Title 없는 schema, 빈 report,
제목 없는 Page, 읽을 수 없는 오류 본문). 97% → 99%.
(10) **Notion으로 나가는 요청의 모양을 자격증명 없이 고정했다** — `RealNotionTransport`의 여섯
메서드는 스위트가 한 번도 실행한 적이 없었다. verb/경로/body/헤더/timeout, 그리고 timeout·OSError
변환까지. double의 두 번째 거짓말(타입 미검사)도 `_TypeEnforcingTransport`로 국소 보완.
(8b) **Secret 보고가 세 번째 목적지를 말하지 않았다** — C47이 만든 줄은 Daily History와 backup
원격을 대고 멈춘다. 실측: 훑는 다섯 필드 중 **넷**이 Notion PROJECTS 행에 그대로 들어간다.
제3자이고 파일이 아니어서 교체 체크리스트에서 가장 빠지기 쉬운 사본이다. 보고를 고쳤다
(동작을 고치는 것은 §12의 결정).
(11) **원자적 쓰기 게이트의 명단이 트리보다 뒤처져 있었다** — 손으로 유지된 일곱 개, 실제
열세 개. `agent/state.py`와 `monthly/state.py`는 **어느 게이트에도 없었다.** 명단을 훑기로
바꾸고, cleanup 존재 여부까지 검사한다. 그리고 관용구의 **안쪽** `except OSError`는 열세 곳
어디에서도 실행된 적이 없었다 — cleanup 실패가 원래 실패를 가리지 않는지 고정했다(보고형
writer 둘에서 특히 중요하다).
(11b) 남은 미실행 분기를 **분류해 기록**했다 — 추상/플랫폼/권한/도달불가 넷으로 나뉘며,
다음 Sprint가 같은 조사를 반복하지 않는다.
테스트 2938 → 3051(신규 113건), 실패 0. `src/controltower/` 100%, `src/notion/` 99%,
`src/` 전체 **99%**(직전 98%).

이전 갱신: 2026-08-20 (C48 — **Dashboard Model Sprint.** C47이 "Desktop의 실제 데이터가
Notion까지 도달한다"를 세웠다면 이번 기준은 **"그 데이터가 하나의 모델로 정리돼, 화면과 나가는
payload가 같은 것임을 증명할 수 있다"**였다.
(1) **rollup이 Blocker를 엉뚱한 팀에 붙이고 있었다** — `Risk.team`이 `project.teams[-1]`,
즉 "가장 최근에 아무 Event나 남긴 팀"이었다. 실측: PAY를 CTO_BACKEND가 BLOCKED로 막고
CMO가 DECISION_APPROVED 하나를 남기면 ATTENTION이 **CMO에게** "그 팀이 RESUMED를 보고할
때까지"라고 말한다. Blocker를 연 Event의 `role`로 고쳤다(`open_blocker_team`).
(2) **완료 지표가 완료가 아닌 파일을 증거로 대고 있었다** — `projects_completed`의 evidence가
`open_blocker_evidence or evidence[-1]`이었다. 완료 count에 blocker 파일을 우선하고, 아니면
그 Project가 마지막으로 한 일을 댄다. 실측: 5일 COMPLETED / 9일 DECISION_APPROVED인
Project가 9일 파일을 댔다. `completed_evidence`를 fold에 넣었다.
(3) **`src/controltower/dashboard.py` 신설 — 요청된 5개 패널의 데이터 모델.** rollup과 화면
사이에 아무것도 없었고, 그래서 "Control Tower가 무엇을 보여주는가"는 터미널 출력으로만
존재했다. 7패널(`COMPANY_GOALS`/`METRICS`/`TEAMS`/`PROJECTS`/`SPRINTS`/`DESKTOPS`/`RISKS`),
`PanelStatus.UNSOURCED`가 1급 값이며 `UNSOURCED_LAYERS` 4개를 정확히 한 패널씩 나눠 갖는다
(테스트가 강제). **`ops_status.py`의 CONTROL TOWER 블록이 이 모델로 렌더링한다** — 실 runtime
출력 byte 단위 동일 확인.
(4) **payload가 자기 row key를 안 가렸다** — 처음 설계는 `AUTHORED_KEYS` 허용목록이었고,
테스트가 즉시 깼다: PROJECTS 행의 `key`가 `project_id`라서 secret 모양 Project 이름이 옆
칸은 가려진 채 key로 나갔다. **가릴 것의 목록이 아니라 안 가려도 되는 것의 목록**으로
뒤집었다(`_UNAUTHORED_KEYS` — 전부 `validate_event()`가 고정 집합으로 묶는 값이다).
(5) **CANCELLED가 ACTIVE로 나갔다** — docs/04 §26은 CANCELLED에 property를 주지 않으므로
접을 사실이 없고, 화면은 `status`를 직접 찍어 안 보였지만 `state`를 읽는 projection에는
취소된 Project가 진행 중으로 보였다.
(6) **Runner가 `_roll_desktops()`를 손으로 한 번 더 구현하고 있었다** — docs/02 §8 짝 검사
사본까지. `build_company_rollup(events=...)` seam으로 바꿔 하나의 fold가 화면과 OPS_RUNS 행
둘 다를 만든다.
(7) **PROJECTS 행의 `Owner`는 만든 팀이지 막은 팀이 아니다** (특성화, SKIP §5). 두 팀이 쓰는
Project에서 Notion 행과 Control Tower가 서로 다른 팀을 가리킨다 — 실측. 고치려면 스키마 결정이
필요해 `docs/13` §3-⑨에 "Blocked를 Owner로 group by 하지 말 것"으로 적고 테스트로 고정했다.
(8) 문서: `docs/13` §3-⑨ **Control Tower View 구성** — PROJECTS/OPS_RUNS 위에 얹을 View,
만들 수 없는 View와 그 이유, ⑨-4 경고. 새 Database도 새 Property도 만들지 않는다.
(9) **payload가 작업량에 비례해 자랐다** — docs/14 §3이 Run Manifest에 대해 이미 정리한 규칙을
내 새 코드가 깼다. Event 하나가 네 행(metric/project/team/desktop)에 증거로 실려, 6,000건에서
**2.0 MB / 382 ms**. 상한을 두되 `evidence_count`는 진짜 총계로 남긴다 — 55 KB / 6.6 ms.
(10) **네 entrypoint가 존재하지 않는 환경변수를 설정하라고 안내하고 있었다** — C47이 `--help`
자리에 넣은 refusal 메시지의 일곱 이름 중 **셋이 존재하지 않는다**. `init_notion.py`는
**틀린 둘만** 댔다. C47이 같이 넣은 테스트는 `configured_by` 목록 자신을 근거로 삼고 있어
잡을 수 없었다. 이름을 고치고, 읽는 자리와 대조하는 게이트를 정적·프로세스 두 겹으로 넣었다.
(11) **AGENT.md §2.2가 C13이 틀렸다고 적은 측정을 아직 그대로 싣고 있었다** — 정정(§2b)은
36줄 아래에 있었다. Desktop 4의 환경변수를 다루는 절이 없어 refusal 메시지가 가리키는 문서에
그 변수들이 없다는 문제도 같이 고쳤다(§2.3 신설).
(12) **무작위 체인 property 하네스** — Desktop 1/2/3/4 → rollup → Dashboard Model → payload를
무작위 Event 열로 흘려 8개 불변식을 250 seed(subtest 2,000건) 검증, 문제 0. 저장소에는
40 seed(28초)로 남긴다. 고정하는 것: Desktop 간 혼입 없음, 세 파티션 전부 `events_read`와 일치,
**파일 이름 순서 무관**(이름만 역순으로 바꿔 payload 동일), state가 fold와 일치, 완료마다 완료
Event가 증거, RISKS = 열린 Blocker + mismatch, 어떤 authored 필드로 들어온 secret도 payload에
없음, 나이가 음수인 값 없음.
(13) **Cross-run partition** — `OPS_RUNS` 행의 `Accepted`는 per-run이고 Dashboard Model은
전체 기간이다. 첫 실행에서는 두 값이 같아 단일 실행 테스트로는 두 정의를 구분할 수 없다.
다음 날짜의 두 번째 배치를 실제로 흘려 **행들이 증거를 분할한다**를 고정했다 —
합 = `events_read`, 어느 한 행도 전체가 아님.
(14) **validator가 거절한 값을 그대로 되돌려주고 있었다** — `unreadable`의 `reason`은
`invalid source: '…'`처럼 **거절한 값을 인용한다.** 처음 판단("파일명과 예외 메시지는 authored
텍스트가 아니다")이 §4와 같은 종류로 틀렸다. `processed/`에 손으로 쓴 파일 하나로 credential이
payload에 **두 번** 들어갔다(파일명 + 이유). 둘 다 가리고 `bounded()`도 걸었다.
(15) **src/의 테스트 클래스 인용에 게이트가 없었다** — 이 저장소는 주석을 "그리고 …Tests가
그것을 검사한다"로 끝내는 일이 잦은데(가장 강한 형태의 근거다), 그 이름이 실제로 존재하는지
보는 것은 **BACKLOG.md에 대해서만** 있었다(`BacklogEvidenceLinksResolveTests`). 이번 Sprint에만
src/ 쪽에서 둘이 어긋났다(테스트 이름을 붙인 뒤 바꿨다). 나머지 절반을
`TestClassCitationsResolveTests`가 덮는다 — 둘이 트리를 분할한다.
테스트 2825 → 2938(신규 113건), 실패 0. `src/controltower/` 문/분기 커버리지 100%.

이전 갱신: 2026-08-19 (C47 — **Desktop → Control Tower → Notion Sprint.**
기준은 "코드가 있다"가 아니라 **"Desktop 1/2/4의 실제 데이터가 Notion까지 도달하고 올바르게
표현된다"**였다.
(1) **Desktop 계층이 없었고, 그 부재가 결함을 가리고 있었다.** C46의 rollup은 `source`를 버렸다.
`validate_event()`는 `source`와 `role`을 **각각만** 검사하고 **짝은 검사하지 않는다** — docs/02 §8이
표를 고정하고 `reporter/profiles.PROFILES`가 **생성 시점에만** 적용하며, 읽는 쪽에는 그 검사가
어디에도 없다. 실측(`source=DESKTOP_1`, `role=CMO` 하나): validator 통과, Control Tower는 CMO 팀
것으로, `desktop_activity`는 DESKTOP_1 것으로, Notion 행은 **Owner=CMO / Source=DESKTOP_1**로
모순을 자기 안에 담은 채 아무도 표시하지 않는다. `DesktopRollup` + `PairMismatch`를 넣었고
Desktop은 **`source`로 센다**(`role`을 믿는 것이 혼입 경로 그 자체다). 표는 `PROFILES`에서
import한다(C28).
(2) **그 검사가 저장소 자신의 "건강한 런타임" fixture를 처음으로 잡았다** — `DESKTOP_2/CTO_FRONTEND`,
`DESKTOP_3/CMO`로 §8을 어긴 채 여러 Sprint 통과했고, COMPANY 블록은 그동안 그것을 화면에 그대로
찍고 있었다. fixture를 §8의 짝으로 고쳤다.
(3) **Notion은 기존 계약 안에서 넓혔다** — 새 DB 없이 이미 계약된 `OPS_RUNS`에 `Desktops Reporting`
(rich_text)과 `Role Mismatches`(number) 둘. Desktop당 열이 아니라 rich_text 하나인 이유는
`events.SOURCES`가 자랄 수 있는 스키마 값이기 때문이다. 숫자는 이번 실행의 Event를 이미 전부 읽는
5단계 루프에서 센다(`processed/`를 다시 읽으면 전체 기간을 세게 된다).
(4) **끊기지 않는지 실제로 흘렸다** — Signal → 실 Agent → OneDrive → intake → Collector → Runner →
rollup → `record_run()`까지 stub 없이. `Desktops Reporting` / `Role Mismatches` / `Accepted` /
PROJECTS `Blocker`·`Source`가 **전부 rollup과 일치**. 재실행 시 행·Event 중복 0, Agent 재실행 시
신규 0. E2E 10건.
(5) **Control Tower가 유일하게 회사의 Blocker를 보여준다** — `BLOCKED`는 History Filter가 REVIEW로
보내고 REVIEW는 어떤 Daily에도 렌더링되지 않는다(E-20). 이번 실행에서 Company History에는 KEEP 3건만
있고 열린 Blocker는 Control Tower에만 있다. 두 집합의 차이를 테스트가 정확히 고정한다.
(6) 저장소 게이트가 네 번 발화했다 — 미선언 layer edge, 호출자 없는 `desktop()`, 새 Notion 열의
스키마 대조, 그리고 (2).
(7) **무작위 체인 fuzz가 새 결함을 잡았다 — 한 배치 안에서 Notion 행이 파일 이름 순서에
좌우됐다.** 4b는 Collector의 `sorted(glob)` 순서로 sync하는데 `event_id`는 uuid5라 그 순서가
시간과 무관하고, Late Event 가드는 "새롭지 않은 Event"를 거부한다. 실측: 같은 배치·한 시간 차이의
BLOCKED/DECISION 둘이 파일 이름 순서에 따라 행에 Blocker가 **있기도 없기도** 한다 —
**막힌 프로젝트가 건강해 보이는** 쪽이 uuid 동전 던지기로 결정됐다. E-23(같은 instant)과 다르다:
가드는 옳고 먹인 순서가 임의였다. 4b가 **오래된 것부터** 적용하도록 고쳤고 정렬 키는 rollup의 것을
import한다. 가드 자체는 그대로 — 다음 실행의 진짜 Late Event는 여전히 거부된다.
(8) **복원된 머신에서 Control Tower가 "아무 일도 없었다"고 말했다.** `processed/`는 Backup 범위가
아니므로(docs/08 §26) 복원하면 Company History는 전부 돌아오고 Event는 하나도 안 돌아온다 —
실측: 일이 든 Daily 18일치 옆에서 `Event 0건 / 움직인 Project 0`. 숫자 옆에 한정자를 적게 했다
(경보 아님 — 되돌리는 조치가 없다). 오탐 방지 지점: 비교는 *가장 이른 Daily 파일*이 아니라
**일이 든 가장 이른 Daily**와 한다 — 빈 날에도 파일을 쓰므로 그렇지 않으면 평범한 설치가 전부
"증거를 잃었다"가 된다.
(9) **Secret이 Event 내용으로 들어오면 아무도 막지 않는다.** Agent는 Signal을 그 자리에서
거부하지만, 다른 Desktop에서 온 Event는 `validate_event()`만 거치고 그것은 내용을 읽지 않는다 —
실측: Daily History에 쓰이고 backup 원격까지 push된다. 보고를 붙였다(거부는 결정, §14).
(10) **그 회귀 테스트가 더 큰 유출을 잡았다.** ATTENTION sink가 `redact()`를 생략하는 근거였던
"id는 내용이 아니다"가 틀렸다 — `event_id`는 Desktop이 스스로 정한다. 토큰 이름을 딴 Event가 그
토큰을 화면과 로그 파일에 찍고 있었다. Event가 쓴 식별자를 찍는 **14곳 전부**를 `_authored()`로
보냈다.
(11) **`--dry-run`이 운영 실행이었다.** 네 entrypoint 모두 `sys.argv`를 읽지 않아 모든 인자가
조용히 무시됐다 — 첫 운영 실행 전 `--dry-run`을 친 사람에게 진짜 push와 진짜 Notion 쓰기가
돌아갔다. `src/cli.py` 하나로 거부하고, 그 도구가 읽는 환경변수 이름을 댄다.
(12) **미실행 분기 정리** — `src/controltower/rollup.py`와 `src/cli.py`는 **문장·분기 모두
100%**. 도달할 수 없는 가드 셋을 지웠고(`if rollup.teams:`, `if rollup.desktops:`, 중복 제거
하나), Project 목록의 **무언 절단**과 완료 Project 줄에 회귀를 붙였다.
전체 Regression **2,825 passed / 0 failed** (baseline 2,741), subtest 1,955)

이전 갱신: 2026-08-19 (C46 — **Company Control Tower Sprint.**
요청은 Notion을 전사 Control Tower로 키우는 것이었다. 먼저 **무엇에 원천이 있는지** 조사했고
그 결과가 설계를 정했다.
(1) **요청된 7계층 중 셋만 원천이 있다.** `src/` 전체에서 `goal`·`sprint`·`kpi`는 필드로도
파일로도 명세 절로도 존재하지 않는다(Team은 새로 만들 것이 아니라 `role`이다). 그래서 Notion에
DB를 신설하지 않았다 — docs/14 §1이 Notion을 "**View이며 절대 Source가 아니다**"로 고정하므로
Goal/Sprint를 거기 적고 권위로 삼는 것은 그 문장을 정면으로 깬다. 원천을 어디에 둘지는
명세 결정이고 SKIP했다(조건은 §11).
(2) **`src/controltower/` 신설(파생 전용).** 원천이 있는 세 계층(Team=`role`, Project,
실행 결과)을 `runtime/events/processed/`에서 굴린다. 상태는 마지막 Event가 아니라 **접어서**
구한다 — 월요일 BLOCKED, 수요일 RESUMED는 막힌 것이 아니고 그 사실은 순서를 재생해야 나온다.
blocker/완료 규칙은 docs/04 §20-28을 다시 쓰지 않고 `_type_specific_properties()`의 **답을
읽는다**(테스트가 event_type 8종에서 두 답을 대조한다). 모든 rollup과 metric이 `EvidenceRef`
(event_id + 파일명 + timestamp)를 들고 다녀 "왜 이 숫자인가"가 파일 하나를 여는 것으로 끝난다.
KPI에 target은 붙이지 않았다 — target은 Goal이고 Goal은 원천이 없다.
(3) **`ops_status.py`의 여섯 번째 블록 `CONTROL TOWER`.** 열린 Blocker만 ATTENTION에 올린다
(임계값 없음 — 파이프라인이 스스로 지우지 않는 유일한 상태이고 팀이 보고하면 사라진다).
조용한 Team은 세되 올리지 않는다 — source→role이 1:1이라 COMPANY 블록이 이미 말하는 사실이다.
원천 없는 계층은 **이름으로 댄다**(빈 패널은 "아무 일도 없다"로 읽힌다).
(4) **저장소 게이트가 네 번 발화했고 전부 옳았다** — 미선언 패키지, dead code(파생만 만들고
화면에 안 붙이면 거부된다), 문서화되지 않은 블록, 그리고 dead 목록에서 **떠난** 함수.
(5) **Manifest → Dashboard 전수 대조: 결함 0건.** 정상·삭제 게이트 발동에서 12열이 일치한다.
Backup push 실패는 행도 pending도 만들어지지 않는데 그것은 A-18의 알려진 결과이고, 새로 잰 것은
**pending 파일조차 없다**는 점이다 — Notion에는 그 실행이 존재한 적도 없는 것처럼 보인다.
(6) **E-23은 "최신성"만 잃는 것이 아니다 (신규 측정).** 같은 instant의 두 Event에서 Notion 행은
`IN_PROGRESS`/Blocker 없음, 디스크는 `BLOCKED`/"예산 승인 대기" — **위험 상태의 반대**를 보여준다.
그리고 그 숫자는 C40이 이미 Manifest에 넣어 두었는데 `_print_last_run()`이 FAILED 컴포넌트의
metric만 찍어서 **어떤 뷰도 보여준 적이 없었다.** NOTION 블록에서 꺼냈다(새 파싱 없음).
(7) **같은 계열 전수 조사 — 남은 구멍 0건.** `recorder.ok()`/`failed()`를 AST로 걸어 SUCCESS
에만 실리는 metric을 전부 대조했다. `or None`로 쓰이는 이상 계수는 둘뿐이고 나머지는 전부
다른 뷰에 닿는다.
(8) **내가 넣은 줄 하나가 ATTENTION의 규칙을 깼다(보안).** blocker는 Event 내용이고 sink는
일부러 redact하지 않는다. 그 줄이 자기 자리에서 `redact()`를 부르게 고쳤다 — rollup은 원문을
유지한다(파생이 몰래 입력을 고치면 더 찾기 어렵다).
(9) TODO/FIXME/HACK/XXX 전수: 프로덕션 코드에 **1건**이고 그것은 정규식을 설명하는 산문이다.
(10) **두 파생이 같은 규칙인지 무작위 열 300개로 확인했다** — `Notion 행 == fold(적용된 Event)`.
건너뜀 없는 열 18건, 있는 열 282건 전부 성립. 즉 Notion과 디스크의 어떤 차이도 Late Event 가드의
건너뜀으로 **완전히 설명된다**. 60 seed를 스위트에 넣었다.
(11) **자기 감사에서 하나 나왔다** — 새 rollup이 날짜를 읽을 수 없는 Event를 **조용히**
버리고 있었다(`events_read`가 디렉터리와 달라지는데 이유를 볼 수 없다). `unreadable`로 보낸다.
(12) 확립된 스레딩 패턴을 그대로 따라 했더니 **느려졌다**(27→42 ms). 이 pass는 `Event.from_json()`
으로 GIL 아래 진짜 CPU를 쓰기 때문이고, 되돌린 뒤 숫자를 코드 옆에 적었다. 이번 Sprint에 예측을
실측이 뒤집은 것이 두 번이다.
전체 Regression **2,741 passed / 0 failed** (baseline 2,684), subtest 1,873)

이전 갱신: 2026-08-19 (C45 — **Durability & Fault-Injection Sprint.**
C44까지는 **읽는 경계**를 훑었다. 이번에는 쓰기가 실제로 디스크에 닿는지와,
실행 중간에 결함을 주입했을 때 유실이 복구되거나 **최소한 보고되는지**를 실행으로 물었다.
(1) **원자적 쓰기 14곳 중 fsync를 부르는 곳이 하나도 없었다.** `mkstemp`+`os.replace`는
atomicity를 사고 durability를 사지 못한다 — rename이 먼저 디스크에 닿으면 전원이 끊긴 뒤
파일은 제 이름·제 크기로 거기 있고 내용은 0이다. 이 저장소는 이미 같은 사고의 **staging 파일**
쪽을 모든 reader까지 추적하고 있었고(§`INCOMPLETE_WRITE_PREFIX`), 나머지 반쪽이 더 나쁘다:
0으로 채워진 `2026-08-05.md`는 모든 탐지기와 Backup이 **기록으로 받아들인다.** 14곳 전부에
flush+fsync를 넣었다(Lock 하나는 일부러 제외 — 살아남지 못한 Lock은 stale로 읽히고 그쪽이
회복 방향이다). 실측 0.43 ms → 1.12 ms per write, 스위트 344 s → 348 s. 검사는 **소스 문자열이
아니라 행동**이다 — `os.fsync`/`os.replace`를 둘 다 기록해 rename마다 앞선 flush가 있는지 본다.
(2) **Local Master에서 사라진 Company History를 아무 탐지기도 보지 못했다 (데이터 유실).**
`2026-08-01.md`를 같은 이름의 디렉터리로 바꾸자 구멍 검사·`_kept_but_not_rendered()`·
state 정합성·자기숫자 검사·오타 디렉터리 검사가 **전부 조용**하고 `daily 파일`은 그 디렉터리를
**세고** 있었다. 구멍 검사는 범위를 있는 파일로 잡으므로 사라진 것이 **가장 이른 날짜들**이면
구조적으로 침묵한다. Backup은 실패하지만 파일 이름은 manifest `reason`에만 있고 상태 뷰는
`reason`을 인쇄하지 않아 운영자가 읽는 것은 자격증명 실패와 **같은 줄**이다.
`_history_gone_from_local_master()` 신설 — Working Copy는 한 방향으로만 쓰이고 삭제를 반영하지
않으므로 거기 있는 이름이 Master에 없다는 것은 **있었고 지금 없다**는 뜻이다. 비교는 게이트
자신의 목록을 쓴다(두 번째 의견이 아니라 같은 집합 연산). `daily/monthly 파일` 계수도 `is_file()`을
묻게 했다 — 그 둘이 마지막이었다.
(3) **`STEP_ABORTED`가 어느 단계인지만 말하고 왜인지는 말하지 않았다.** 손상된
`collector_state.json`에서 예외는 파일과 파싱 위치를 이름으로 대는데 그 문장은 콘솔에만 있었다.
`finally`에서 `sys.exc_info()`를 읽어 `reason`에 덧붙인다(redact+one_line, 전파는 그대로).
(4) **`Event Count`만 되돌려 주지 않았다** — 그리고 그 줄이 `_daily_counts_more_than_it_shows()`의
유일한 입력이라 손으로 잘린 Metadata 한 줄이 세 가지 유실의 탐지기를 그 날짜에 대해 영구히 껐다.
(5) **E-17에 두 번째, 더 조용한 진입 경로가 있다** — 6.5단계가 *실패*하는 것이 아니라 *도달하지
못하는* 경우다(6단계에서 state save가 던지면 그대로 밖으로 나간다). manifest에 `late_update`
컴포넌트가 아예 없고 `daily_late_update.log`도 없으며 **이후 두 실행이 exit 0**을 보고한다.
재시도는 E-17의 이유 그대로 SKIP, 모양과 탐지기 덮음만 고정했다.
(6) **Agent state의 `last_run`이 읽히지 않으면 "한 번도 실행된 적 없다"**고 말했다 — 세 줄 위에
그 값을 인쇄하면서. 두 조건을 갈랐다(load에서 거부하지 않는다 — 참고용 필드 때문에 Agent를
멈추는 것은 방향이 반대다).
(7) 미실행 branch 셋을 실제 행동 테스트로 덮었다(Retry Queue 저장 실패의 로그도 실패하는 경우,
Agent 겹침 실행 entrypoint, 잘린 Metadata 조합). `738 -> 746`과 dashboard의
`SKIPPED_NOT_CONFIGURED` elif는 **도달 불가**임을 확인해 기록했다.
(8) 결함 주입 200회 + 손상 주입 240회 + 파이프라인 fuzz 95회에서 위 둘 말고는 조용한 유실 0건이고,
이번에 넣은 탐지기 둘의 오탐도 0건이다.
Windows 예약 장치 이름은 실제로 만들어 확인했다 — 이 OS에서는 평범한 파일이다(결함 아님).
(9) **(2)가 새로 도는 목록이 느려서 재 봤고, 그 목록 자체가 느렸다.** `_relative_files()`가
`rglob("*")`로 `.git/`을 포함해 전부 훑고 항목마다 stat 두 번을 내고 버리고 있었다. `os.scandir`
재귀 + 범위 밖 최상위 디렉터리 pruning으로 **21.3x**(1년) / **11.7x**(10년) — Runner의 Backup
단계도 매 실행 같은 만큼 빨라진다. 반환 집합이 동일함은 주장이 아니라 검사다: 이전 구현을
`_relative_files_by_rglob()`로 남기고 적대적 트리에서 둘을 대조한다.
(10) 그 과정에서 **소스 문자열 검사 하나가 동작이 그대로인데 깨졌다**(junction traversal, A-19).
같은 성질을 실제 junction에 술어를 대고 재는 행동 검사로 바꿨다 — `is_symlink()` False,
`is_dir(follow_symlinks=False)` True, `isjunction()` True. A-19의 결정은 그대로 열려 있다.
(11) **PROJECTS Database에는 OPS_RUNS가 가진 스키마 대조 검사가 하나도 없었다** — 매 Event마다
쓰이는 쪽인데. 현재는 일치하지만 붙들고 있는 것이 없었고, 불일치는 실제 API의 400이며 이 저장소의
in-memory double은 그 두 가지를 **다 받아들인다**고 이미 기록돼 있다. event_type 8종을 걷는 대조
5건(subtest 126)을 넣었다.
(12) **살아 있는 생산자와의 경쟁을 한 번도 재 본 적이 없었다** — 기존 동시성 테스트는 소비자
4개를 이미 있는 파일 위에서 경쟁시킨다. 별도 프로세스의 진짜 sender가 쓰고 있는 폴더를
`stable_after_seconds=0`으로 비웠다: 240건 중 중복 0 / 거부 0 / 찢어짐 0. 테스트가 공허해지지
않도록 "sender가 살아 있는 동안 최소 한 번 옮겼는가"도 함께 단언한다.
(13) **Monthly가 자기 원본(Daily)보다 뒤처져도 아무도 보지 않았다.** 두 검사가 모두 한 문서를
자기 자신과 비교하고 있었고, 파일을 건너는 고리에는 아무것도 없었다. 실측: 7월 통합 뒤 Daily를
손으로 고치면(명세 둘이 허용한다) 이후 실행이 전부 exit 0이고 ATTENTION은 침묵하며 **어떤 실행도
그 달을 다시 만들지 않는다.** `_monthly_lags_its_daily_source()` 신설 — Daily 쪽은 Monthly가
실제로 쓰는 파서로 읽는다. mtime prefilter로 건강한 트리는 10년에 14.1 ms.
전체 Regression **2,684 passed / 0 failed** (baseline 2,616), subtest 1,757 (baseline 1,564).
루트 포함 line coverage 97.16% → **97.04%**, 한쪽만 다닌 branch arc 59 → **60** — 두 수치가
거의 그대로인 것은 이번에 **소스가 159 statement 늘었기 때문**이다(탐지기 둘, 공유 helper 하나,
walk 하나). 닫은 것과 새로 생긴 방어 분기가 상쇄한다: 닫은 것은 runner의 로그-실패 경로,
late_events의 잘린 Metadata 세 조합, run_agent의 겹침 분기이고, 남은 것은 전부 플랫폼 전용
(`scheduler/lock.py`의 POSIX), subprocess로만 측정되는 entrypoint, 또는 도달 불가로 확인된
자리다)

이전 갱신: 2026-08-18 (C44 — **Read Boundary Sprint.**
C43이 Event 경계(docs/02가 `string`이라 적었는데 validator가 강제하지 않음)에서 P0를
찾았으므로, **같은 눈을 나머지 읽기 경계 전부에** 댔다.
(1) **History Candidate 경계에 같은 결함이 있었다 (P0).** docs/11 §71은 COO가
`runtime/history_candidates/` 파일을 손으로 고치는 것을 명시적으로 허용하는데,
`HistoryCandidate.from_dict()`는 파일이 말하는 대로 읽고 타입을 전혀 보지 않는다.
실측 — 손으로 고친 KEEP Candidate 하나가 평범한 것 하나 옆에 있을 때:
`summary=12345` / `project_id=7` / `timestamp=5` / `summary` 키 삭제 **넷 다
daily FAILED, Daily 파일 0개, exit 2**. Scheduler는 keep 인덱스를 배치당 한 번
만들므로 **모든 날짜**가 멈추고, 파일이 `keep/`에 남으므로 **이후 모든 실행이 같은
자리에서 죽는다**(A-7/BUG-38과 같은 반경). 그때 운영자가 받은 것: manifest reason
`sequence item 2: expected str instance, int found` — 파일도, 필드도, Candidate가
관련됐다는 사실조차 말하지 않는다. 그리고 `ops_status.py`는
**`Candidate 정합성 : OK`**라고 했다.
(2) **결정 없이 고칠 수 있는 것만 고쳤다.** `history.result.candidate_errors()` —
`events.validate_event()`와 같은 모양의 순수 함수 — 를 만들고,
`FileHistoryRepository`가 그것으로 거부하며 **파일과 필드를 이름으로 대는**
`HistoryCandidateError(ValueError)`를 던진다. `ops_status._read_keep_candidates()`도
**같은 함수**를 쓴다(두 번째 의견 없음, C28 규칙) — 이제 이미 있던
"읽을 수 없는 KEEP Candidate" ATTENTION 줄이 이 모양에도 발화한다.
**무엇이 실패하는지는 한 줄도 바꾸지 않았다** — 렌더러가 살아남는 세 모양
(`role`·`category`·`evidence` 타입 오류)은 일부러 통과시킨다. 거부하면 살아남을 수
있는 손상을 멈춘 파이프라인으로 바꾸는 것이고, 그게 이 수정이 줄이려는 피해다.
(3) **손상된 Run Manifest 하나가 상태 뷰를 통째로 죽였다 (신규).**
`read_summary()`는 스스로 "세 enum만 검증한다"고 적고, `metrics`는
`c.get("metrics", {})`다. 렌더러는 키와 값을 `one_line()`으로 이미 방어하고 있었는데
**컨테이너가 mapping이라는 것**은 가정했다. 실측: `metrics`가 문자열인 manifest →
`AttributeError: 'str' object has no attribute 'items'`가 `_print_last_run()`
밖으로,
`main()` 밖으로 → **운영자는 상태 대신 traceback을 받는다.** 이 파일 자신의 계약
("증거 일부가 손상돼도 답을 내야 한다")과 docs/10 §46("손상된 State도 보고 대상")을
동시에 깬다. 그리고 그것이 일어나는 때가 **복구 직후**다. 건너뛰지 않고 보고한다 —
나머지 manifest는 그대로 렌더링되고 손상은 자기 줄과 ATTENTION을 얻는다.
(4) **나머지 읽기 경계는 전수로 확인했고 깨끗했다.** scheduler / backup /
monthly / agent state, retry queue, dashboard pending — 전부 객체 모양과 필드 타입을
검사하고 이름 붙은 오류를 던진다(실측). `tuple(json값)`이 문자열을 글자로 펼치는
계열 5곳도 훑었다: Event 쪽은 `validate_event()`가 앞에서 막고, Candidate·
`artifact_refs`는 특성화돼 있으며, `backup/log.py`의 둘은 **호출자가 없다**.
(5) **C27 §8을 실행으로 재검증 — 기록이 정확했다.** `incoming/`의 완성된 staging 파일이 ACCEPTED되어
`processed/`에 staging 이름으로 남는 경우, COMPANY 뷰가 그것을 **정상적으로 센다**(의심했는데 재 보니 필터는
다른 디렉터리 쪽이었다). 잘린 파일이 `rejected/`로 가는 쪽도 이미 정확한 문장 두 줄이 있다. 변경 없음.
전체 Regression **2,616 passed / 0 failed** (baseline 2,597), subtest 1,564, 루트 포함 line coverage 97.60%)

이전 갱신: 2026-08-18 (C43 — **Re-verification Sprint.**
C42의 결론을 문구가 아니라 **실행**으로 다시 확인하고, 그 과정에서 나온 것만 고쳤다.
(1) **BUG-11/27에 기록되지 않은 결과가 있었다 — 그리고 그것이 가장 비싸다.**
개행이 든 `summary`가 `- Event ID: VICTIM` 줄을 위조하면 §38의 중복 가드가
VICTIM을 "이미 이 문서에 있다"고 믿는다. 즉 **그 id로 나중에 도착하는 진짜 Event가
영원히 추가되지 않는다.** 게다가 그 유실을 위해 만들어진 탐지기
`_kept_but_not_rendered()`가 **똑같이 속아 clean을 보고한다**(찾는 id가 파일에 있으니까).
기록에는 "구조 위조"·"Monthly 섹션 유실"까지만 있었다.
(2) **결정 없이 그 침묵을 닫았다 — `_daily_counts_more_than_it_shows()`.**
Daily 파일은 자기 총계(`- Event Count:`)와 Event ID를 **둘 다** 들고 있고,
생성 시점에는 반드시 일치한다. 그 둘을 한 파일 안에서 비교한다(창 없음, 두 번째
의견 없음 — 비교는 §38이 쓰는 `existing_event_ids()` 그대로 재사용). Monthly 쪽
형제는 있었는데 Daily 쪽은 없었다. 세 가지 실제 유실이 처음으로 운영자에게 닿는다:
위조된 id 줄(위 1번), `category=None` 후보가 어느 Section에도 못 들어가 id가 아예
파일에 없는 경우(특성화만 돼 있었다), 손으로 지운 항목 블록. escape는 여전히
BUG-11/27의 결정이고 건드리지 않았다 — **세는 것만** 했다(C31이 이미 그은 선).
(3) **E-23을 측정으로 좁혔다** — 기록은 무엇이 갈라지는지만 말하고 **얼마나 오래**는
말하지 않았다. 실 Agent → 실 Runner → Sync로 끝까지 돌린 결과, 그 프로젝트에 대한
**다음 Event 하나**가 View를 완전히 되돌린다(Last Event ID / Last Updated 모두).
Company History는 처음부터 둘 다 지킨다. 결정은 그대로 열려 있고 시급도가 바뀐다.
(4) **branch coverage 재측정 — 1,300 지점 중 한쪽만 다닌 335건.** 전수로 읽는 대신
위험도 순으로 열었고 **두 개가 진짜 공백**이었다: Backup의 **PENDING 재시도 push가
인증 실패로 끝나는 경우**(§62의 무한 재시도를 막는 두 번째 자리인데 한쪽만 돌고
있었다 — 코드는 옳았고 테스트가 없었다)와 **category 없는 late item 렌더링**.
나머지는 정당하거나 측정 도구의 귀속 문제였다.
(5) **`changed_files`가 상태에 따라 세 가지가 다르다** — 출처(git/sync), 의미(커밋이
싣는 것/sync가 준비한 것), 그리고 **경로 구분자**(`daily/…` 대 `daily\…`)까지.
셋 다 각자의 경로에서는 옳다. 고치지 않고 짝으로 고정했다.
(6) **E-21 / E-24 / E-25 / A-21을 실행으로 재확인 — 기록이 정확했다.** stray 파일은
여전히 원격에 도달하고 탐지는 두 통로로 보고한다, `.gitignore`가 있으면 오탐 없음,
`daily/ID_RSA`는 여전히 게이트를 통과하고 탐지기는 이름을 댄다, 삭제 게이트는
`BACKUP_FAILED`/CRITICAL/PERMANENT + reason + `deleted_files` + **C42가 넣은
Dashboard 열**까지 실제로 도달한다, A-21은 `Consolidated Items: 2`에 EVT-2가
파일 어디에도 없다.
(7) **아무도 넘기지 않는 keyword 인자 2개** — 275개를 AST로 걸어 대조했다. 프로덕션이 안 넘기는 17개 중 열은
`run_once()`의 경로 인자로 **C34 §3의 기록이 정확함을 독립 확인**해 준다. 완전 미실행 2개는 둘 다 판단이 걸린
자리다(`check_coverage(today=)`는 한 달이 가져야 할 날짜 집합을,
`needs_attention(stale_after_days=)`는 Desktop을 언제 침묵으로 부를지 정한다). 지우지 않고 돌렸다. 그
김에 문서/코드 어긋남 하나 — 기본값 2로는 docstring이 막겠다는 **월요일 경보를 막지 못한다**(금→월은 3일). 정책이라
SKIP.
(8) **손으로 돌려 본 것 셋을 영구 테스트로** — 전달 정합성(Event→Daily→Monthly 단계별 계수), **운영 진입점으로
한 디스크 전체 유실 복구**(17파일 바이트 동일, `generated=0 reused=17`), OPS_RUNS 행 열두 열의 디스크
대조. 그리고 Batch Save 가드를 소스 문자열에서 **실제 쓰기 횟수**로 바꿨다.
(9) **docs/02가 `string`이라고 적은 필드를 validator가 강제하지 않았다 (신규, P0)** —
`event_id`·`project_id`·`summary`는 존재 여부만 검사됐다(다른 타입 필드는 전부
덮여 있었다). OneDrive를 건너온 Event 하나에 `summary=12345`가 있으면 Collector가
ACCEPT하고 Candidate가 저장된 뒤 **Daily Close가 죽는다 — Daily 0개, exit 2, 그리고
Candidate가 디스크에 있으므로 이후 모든 실행이 같은 자리에서 죽는다.** 그 실행의
무고한 Event도 함께 사라진다. `event_id`가 int면 **TypeError가 run_once() 밖으로
탈출**한다. 명세가 이미 string이라고 적었으므로 정책 결정이 아니다 — 검사 3줄.
고친 뒤: `rejected/`로 가고, 무고한 Event는 그대로, Daily는 써지고, manifest는
SUCCESS, Dashboard는 Rejected 1/WARN. 보내는 쪽(Signal)도 같은 모양으로 거부한다.
전체 Regression **2,597 passed / 0 failed** (baseline 2,504), subtest 1,542, 루트 포함 line coverage 97.14% -> 97.61%, branch coverage 74.23% -> 74.50%)

이전 갱신: 2026-08-18 (C42 — **Summary Section Sprint.**
`render_daily_markdown()`은 `## Summary`에 요약을 **`- ` 없이 원문 그대로**
반복한다. 그래서 요약이 그 자체로 불릿이면 그 줄은 `- Event ID: …` label과
**바이트 단위로 같다**. C30이 아이템 블록 안쪽의 같은 함정을 닫았는데, 블록 밖의
이 섹션은 `summary_line_indices()`가 `### ` 블록을 걷기 때문에 닿을 수 없었다.
(1) **늦게 온 Event가 영구히 사라졌다** — §38 중복 가드가 요약을 label로 읽어
유령 id를 갖고, 진짜로 늦게 온 그 Event는 도착한 날에도 이후 모든 실행에서도
버려진다(카운터도 로그도 없음). 크래프팅도 손편집도 필요 없고, 이 저장소의 benign
fuzz corpus가 `- leading dash`와 `Event ID: …`를 이미 정상 요약으로 열거하고
있었다 — 없던 것은 조합뿐. `daily/markdown.item_block_bounds()` 신설로 label을
`### ` 블록 안으로 한정했다. **틀려도 안전한 방향**이다(진짜 label을 놓치면
중복 블록 하나, 눈에 보이고 다음 실행에서 멈춘다 / 유령을 더하면 영영 못 오는
Event).
(2) **같은 줄이 E-17 유실 탐지기를 껐다** — `_kept_but_not_rendered()`가
`'- Event ID: EVT-B'` 요약 하나로 EVT-B에 대해 침묵했다(실측 3행 비교).
`_reviewed_but_not_rendered()`도 같은 뿌리. 공유 함수 `_label_lines()`로 닫았고
판정·메시지·심각도는 그대로다.
(3) **Monthly 쪽 오탐은 고치지 않고 특성화했다** — 같은 줄이 `unconsolidated`를
1 올려 아무것도 잃지 않은 날에 `MONTHLY_UNCONSOLIDATED`가 영원히 뜬다. 그 계수는
일찍 끝난 섹션을 잡기 위해 **문서 전체**를 보도록 만들어졌고 자기 계약이 과다
계수를 안전한 방향이라고 명시하므로, 좁히면 보장을 정밀도와 맞바꾼다.
(4) **`BACKUP_SUCCESS`가 파일이 아니라 디렉터리를 세고 있었다 (신규)** — `git status
--porcelain`이 추적되지 않은 디렉터리를 한 줄로 접는데, `backup/runner.py`가 그 add **이전** 목록을
`changed_files`로 실어 Run Manifest와 OPS_BACKUP까지 보냈다. 3일치를 push한 Backup이
`changed_files=1`, 이름은 `daily/`. 첫 Backup·재해 복구·`monthly/` 최초 생성이 전부 이 경우다.
add와 commit 사이에서 같은 명령을 한 번 더 불러 커밋 자신의 내용을 보고한다(새 git 명령 없음, 실측 15~18 ms).
(5) **운영자가 읽는 줄이 Python repr이었고, 문서는 다른 줄을 보여주고 있었다 (신규, 문서 drift)** — 저장소를
복사한 격리 사본에서 `python run_company_ops.py`를 실제로 돌려 보니
`generated=(datetime.date(2026, 8, 1), …)` 606자였다. `AGENT.md` §6a-3은
`generated=(2026-08-05,)`를 보여주며 "둘을 비교하라"고 지시하는데, 그 지시가 가장 중요한 재해 복구 직후가 목록이
가장 긴 때다(60일이면 약 1,800자). 문서를 코드에 붙들어 두는 테스트는 **f-string 소스 문자열**을 검사하고 있어서 둘 다
통과했다(C41 §1과 같은 모양). 개수를 먼저, 날짜는 ISO로, 10일 초과분은 세어서 붙인다.
(6) **Task Scheduler가 실제로 돌리는 명령에 E2E가 없었다** — 모든 테스트가 `run_once()`에 19개 경로를
명시해 부르는데 운영은 3개만 넘기는 스크립트를 돌린다. `main()` 본문은 한 번도 실행된 적이 없었다. 저장소를 복사해
subprocess로 돌리는 E2E 9건 신설(제자리 실행은 `_one_runtime_root_or_refuse()`가 막는 바로 그
위험).
(7) **Desktop 4의 문지기에 행동 테스트가 없었다** — `_resolve_history_start_date()`의 거부 둘과
`_report_backup_failure()`의 fallback 둘. C41 §7의 Desktop 4 짝이다.
(8) **운영자에게 "이 숫자는 계산되지 않았다"고 말하는 줄이 도달 불가였다 (신규, 조용한 실패)** — `if
snapshot.pending_dates and _agent_start_date() is None:`은
모순이다(`read_status()`는 start date가 있을 때만 `pending_dates`를 채운다). 변수가 없는 Desktop은
`미수집 날짜 : 0`만 찍고, 그것은 완전히 따라잡은 Desktop과 같은 줄이다 — Company History를 생산하는 머신에서.
형제인 history 쪽은 처음부터 옳은 모양이었고(`if history_start is None:`) 그것을 가져왔다.
(9) **유실 탐지기 셋의 출력 경로가 한 번도 돌지 않았다** — 전달 정합성 / 검토 미반영 / Monthly 시퀀스 구멍. 셋 다
탐지 함수는 충실히 테스트돼 있고 그 결과를 화면과 ATTENTION으로 바꾸는 자리는 **소스 문자열 검사**가 지키고 있었다. 13건
신설. 나머지 미실행 줄은 전수로 읽고 정당함을 확인했다(결함 0건).
(10) **Dashboard가 재해 복구를 조용한 일요일과 구별하지 못했다 (신규)** — C39의 `generated`/`reused`
쪼갬이 Run Manifest와 stdout에는 닿았는데 **Dashboard에는 닿지 않았다**. 17일이 백업에서 돌아온 실행의 행은
`Generated Days: 0`이고 그것은 한가한 일요일과 같은 값이다. `Reused Days` 한 열 추가(C31 13 → C32
15 → C33 17 → C37 18 → C42 20열, 이미 있는 성장 경로). 판정에는 넣지 않았다. **같은 모양의 두 번째 열도
함께**: `Deleted Files` — `BACKUP_FAILED`는 자격증명 실패와 **Local Master 파일 삭제 감지**가
같이 쓰는 값이고(E-25), C31이 manifest에 넣은 그 구분이 Dashboard에는 없어서 행만 보면 토큰을 고칠 일인지 사라진
History를 찾을 일인지 알 수 없었다. 같이:
`generated_days`만 `getattr(..., 기본값)`이라 이름이 바뀌면 조용히 0을 보고했다 — 직접 접근으로 바꿨고, 그
drift를 잡으라고 있던 `DoublesMatchTheRealResultObjectsTests`가 double 넷 중 둘만 검사하고
있었다(빠진 둘 중 하나가 실제로 drift한 `_FakeScheduler`).
(11) **성공한 단계의 metric은 어떤 View에도 닿지 않는다 (신규, 특성화)** — `_print_last_run()`은
SUCCESS를 건너뛰고 entrypoint는 실패만 찍는다. 대부분은 옳지만(Dashboard·stdout에 이미 있다) 하나는 다른 데
없고 그것이 활동이 아니라 **불일치**를 보고한다: `notion_sync.same_instant_skips`(E-23). 고치지 않고
특성화했다 — 어느 숫자가 줄을 얻는지는 판단이고 E-23의 해결은 열린 Spec 결정이다. 판단이 아닌 것: 이걸 잡았어야 할 sweep이
**dataclass 필드**를 걸었고 metric은 dict 키였다.
(12) **테스트 fixture 두 개가 렌더러가 만들 수 없는 문서였다** — 바로 그래서
아무것도 주장하지 못하고 있었다. 진짜 아이템 블록으로 강화했다.
전체 Regression **2,504 passed / 0 failed** (baseline 2,431), subtest 1,491, 루트 포함 line coverage 96.90% -> 97.56%)

이전 갱신: 2026-08-17 (C41 — **Unrun Branch Sprint.**
C40이 만든 line coverage 수집기를 **결함 탐지기로** 썼다 — 남은 85줄을 훑는 대신
`app/runner.py`의 미실행 4줄을 하나씩 열어 **왜 한 번도 돌지 않았는지**를 물었다.
(1) **테스트가 프로덕션 줄을 복사해 놓고 통과하고 있었다.** `##` 제목이 든
`project_id` 하나가 Monthly 섹션을 통째로 잃게 만드는 유실은
`MONTHLY_UNCONSOLIDATED`로 `daily_late_update.log`에 기록되는데, 그 줄을 실행하는
테스트가 없었다 — 있는 테스트는 `monthly.run_once()`를 부른 뒤 **Runner의 로깅을
테스트 본문에 다시 구현**하고, 짝 테스트는 `inspect.getsource()`에 문자열이 있는지
본다("The half the test above cannot prove by construction"). 조건이 뒤집혀도,
경로가 틀려도, 메시지가 잘려도 통과하는 쌍이었다. 진짜 Runner를 돌려 진짜 로그를
읽는 4건 신설.
(2) **BACKUP_PENDING을 말하는 자리가 둘인데 도는 것은 하나다 (신규).**
`backup/runner.py`가 *반환*하는 entry의 `final_status`는 SUCCESS/NOT_REQUIRED/
FAILED뿐이고 PENDING은 raise 직전 state에만 쓰인다 — 즉 Runner의 반환값 분기는
**도달 불가**(E-16과 같은 모양)이고, 실제로 도는 것은 `except GitOperationError`
쪽이다. **대가가 있다: BUG-39가 붙인 `commit_hash`/`changed_files`가 도달 불가능한
쪽에만 있어, 실측 결과 진짜 pending push의 manifest metric은 `{}`다.** 이 배포가
지금 들어가 있는 바로 그 상태인데 "어느 commit이 밀려 있는가"를 말하지 못한다.
특성화 8건 + 도달 불가의 전제 자체를 소스에서 확인하는 테스트, 그리고 왜 지우지
않는지(BUG-4/A-18이 반대로 결정되면 살아난다) 주석.
(3) **MONTHLY_FAILED 반환 경로가 한 번도 돌지 않았다** — 덮여 있던 것은 PENDING
쪽과 `except Exception` 폴백뿐이었다. 완성된 달에 읽을 수 없는 Daily 하나를 넣는
E2E 5건 신설(§74: 9단계 전부 기록되고 Backup은 성공).
(4) **E-16을 실행 데이터로 독립 재확인** — 기록이 정확했다.
(5) **측정 도구 자신의 사각을 닫았다** — C40의 coverage는 `src/`만 봤고 진입점
넷은 측정 밖이었다. 포함해 재측정: 전체 96.4%인데 **`run_company_ops.py`가 67%**,
즉 Task Scheduler가 실제로 실행하는 파일의 3분의 1이 한 번도 돌지 않았다.
(6) **`_build_notion_clients()`에 행동 테스트가 없었다** — 환경변수만으로
"무엇이 실행될지"를 정하는 세 갈래인데, 있던 것은 `inspect.getsource()` 문자열
검사와 **다른 목적의 subprocess 실행**뿐이라 어떤 객체가 돌아오는지는 아무도
보지 않았다. 가운데 갈래가 이 배포가 매 실행 타는 길이고, 세 번째 갈래는 두
client가 **하나의 transport**를 공유한 채 database id만으로 갈라지는 자리다
(`record_run()`이 "nothing can check that"이라 적은 바로 그것 — 결정되는 자리가
여기이므로 검사할 수 있는 자리도 여기다). 6건 신설.
(7) **`run_agent.py`의 설정 검증 3개와 보고 경로도 미실행** — Desktop 1~3의
문지기이고, 결과가 비대칭이다(거부하면 아무도 안 보는 화면에 말하고, 잘못 설정된
채 돌면 Event가 아예 없다 — 둘 다 며칠 뒤 "PC가 꺼져 있었다"와 구분 안 되는 신호로
도착한다). 6건 신설.
(8) **branch coverage 도구를 만들어 돌렸다 — 결함 0건.** `sys.monitoring`의
BRANCH 이벤트로 1,066 지점 측정, 한쪽만 다닌 279건을 위험도 순으로 읽었다. 전부
정당했다(테스트가 일부러 모든 경로를 명시하는 안전 규칙 때문, 단일 스레드로 도달
불가능한 race 가지, 방어적 early return).
(9) **두 secret 탐침의 fail-safe 방향이 반대인 것은 문서만 있고 테스트가
없었다** — git이 답 못 할 때 filter는 후보를 그대로 돌려주고(fail open) producer는
침묵한다(fail closed). 옳은 설계이고 docstring이 이유까지 적어 뒀는데 두 반환 모두
미실행이었고, 가장 가까운 기존 검사는 **빈 후보 집합**이라 두 방향을 구분하지
못했다. 5건 신설.
전체 Regression **2,431 passed / 0 failed** (baseline 2,397), `runner.py` 미실행
4줄 → 2줄, 루트 포함 line coverage 96.4%)

이전 갱신: 2026-08-17 (C40 — **SKIP Re-audit Sprint.**
SKIP 항목을 **문구가 아니라 코드로** 다시 봤다. 정말 정책 결정이 필요한 부분은
어디까지이고, 그와 무관하게 지금 할 수 있는 것은 무엇인가. 그리고 반대 방향도 —
해결됐다고 기록됐는데 남아 있거나, 남아 있다고 기록됐는데 해결된 것은 없는가.
(1) **E-23 — SKIP된 것은 결정이지 관측이 아니었다.** 재현해 보니 "같은 순간의 두
Event 중 하나가 버려진 것"과 "게이트가 명세대로 늦은 Event를 막은 것"이 결과
객체에서 **완전히 동일했다**(둘 다 `NOTION_SKIPPED_OLD_EVENT`, `error=None`).
하나는 Source와 View가 갈라진 것이고 다른 하나는 정상인데 어떤 하류도 나눌 수
없었다. 동시인 경우에만 이유를 붙이고(기존 통로 그대로) Run Manifest에
`same_instant_skips`로 센다. 비교도 상태값도 심각도도 건드리지 않았다.
(2) **A-20 — BACKLOG가 인용한 테스트가 존재하지 않았다.**
`ReconciliationLockAwarenessTests`(4건)가 orphan 보고의 lock 인지 근거로
적혀 있는데 그 클래스가 없었다. 즉 "목록은 절대 줄이거나 숨기지 않는다"는
약속 — 데이터 유실 보고가 lock 파일 하나로 조용해질 수 있는지를 정하는 문장 —
이 무테스트로 살아 있었다. 인용된 이름 그대로 4건을 썼다(문구 유무, **목록
불변**, 그리고 **stale lock은 면죄부가 아님**).
(3) **E-17 기록이 코드보다 낡아 있었다** — "모든 지표가 정상을 보고하는 채로"는
`_kept_but_not_rendered()`가 이미 닫았다. 재시도 부재는 그대로 두고 서술만 정정.
(4) **증거 링크 전수: 인용된 테스트 클래스 119개 중 1개 끊김.** 고치고
`BacklogEvidenceLinksResolveTests`로 고정 — C38이 문서 포인터·파일 경로에 친
울타리의 세 번째다.
(5) **BUG-42 / E-20 / E-22는 fault injection으로 재현했고 기록이 정확했다** —
읽기 전용 stale lock은 3회 연속 `False`이고 탐지기가 3회 모두 `True`다. 변경 없음.
(6) **큐 저장 실패의 "삼키는 쪽" 가지가 흔적을 남기지 않았다** — 원래 예외를
가리지 않는 판단은 옳지만, 그 대가(이번 실행이 큐에 넣으려던 재시도 대상이
디스크에 없어 다음 실행이 다시 시도하지 않는다)는 어디에도 보고되지 않았다.
로그 한 줄만 추가하고 제어 흐름은 그대로 뒀다.
(7) **Line coverage를 실측했다 — 97.7%** (`coverage.py`가 없어 `sys.settrace`
기반 stdlib 수집기를 만들어 전체 suite를 실제로 돌렸다). 미실행 95줄을 전수로
읽었고 대부분 정당했다(추상 베이스, POSIX 전용 분기, 실제 HTTP). **하나가
아니었다: Company History writer들의 atomic-write 정리 경로가 한 번도 실행되지
않는다.** `AtomicWriteFailureCleanupTests`의 목록은 writer 8개인데 실제 atomic
writer는 14개이고, 빠진 쪽에 `daily/generator.py`(2개)와
`monthly/generator.py`가 있었다 — **백업이 실어 나르는 유일한 파일들**이라
거기 남은 `.tmp-`는 `git add -A`로 원격 history에 영구히 올라간다. 셋 다
테스트를 추가하고 재측정으로 정리 경로가 실행됨을 확인했다.
전체 Regression **2,397 passed / 0 failed** (baseline 2,377), line coverage 97.7% -> **97.9%**)

이전 갱신: 2026-08-17 (C39 — **Restore Sprint.**
`docs/10` §45(Desktop 4 복구) 테스트는 **원격이 무엇을 돌려주는지**까지 보고
멈춰 있었다 — 그 다음에 파이프라인을 실제로 돌려 본 적이 없다. 복구된 Desktop 4가
부팅하는 상태는 `daily/`는 완전하고 `runtime/state/`는 통째로 없는 것,
즉 **완성된 History를 들고 그것을 쓴 기억이 전혀 없는** 상태다. 예약 실행이므로
아무도 보기 전에 저절로 일어난다.
(1) **데이터는 안전하다 (실측)** — 3일치 생성 → 디스크 전체 유실 → clone → 실행:
복구된 파일 4개 한 바이트도 안 바뀌고, 새 날짜 하나만 쓰이고, watermark는 정상
전진하며, **원격에 빈 날이 push되지 않는다.** Scheduler가 쓰기 전에 `is_file()`을
보기 때문이고(§28 crash 대비 장치가 복구 경로도 함께 막고 있었다), 이제 테스트로
고정했다.
(2) **그러나 그 실행은 "5일 생성"이라고 보고했다 (결함)** —
`SchedulerRunResult.generated_dates`는 실제로는 **닫힌 날짜**를 담는데 이름은
generated였고, 루프 주석이 그 합침을 명시까지 하고 있었다. crash 재실행에서는
하루라 눈에 안 띄지만 **복구 직후에는 전부**이며, 하필 운영자가 "복구가 됐나"를
확인하는 그 실행에서 시스템이 가장 큰 활동량을 "생성"이라고 보고한다 — 파이프라인이
만들 수 없는 4일을(Candidate는 백업에 없다, `docs/08` §26). `generated_dates`(쓴 날)
/ `reused_dates`(물려받은 날)로 쪼개고 합집합은 `closed_dates`로 남겼다. watermark
규칙은 한 줄도 안 바뀌었다. C32가 `Notion Synced`에서 `Notion Skipped`를 떼어낸
것과 같은 수술이다.
(3) **합침을 정상으로 고정하던 테스트 3건, 셋 다 자기 주석에서 이미 진실을 알고
있었다** — "08-01 was **adopted** (pre-existing file, not recreated)"라고 써 놓고
둘 다 generated라 단언하는 식. 약화가 아니라 강화로 고쳤다(어느 날을 썼고 어느 날을
물려받았는지까지 단언, `closed_dates`가 옛 합집합을 그대로 지킨다).
(4) **운영자 문서 §6a-3 신설** — 무엇이 돌아오고 무엇이 안 돌아오는지, 실측 표,
그리고 "복구 직후엔 `reused`가 크고 `generated`가 작은 것이 정상이며 그 반대라면
즉시 멈추고 원격을 확인하라". C35의 §6a-2처럼 drift 테스트를 붙였다.
전체 Regression **2,377 passed / 4 skipped / 1,468 subtests / 0 failed**
(baseline 2,366). C39 마무리로 `docs/11` §101 Release Environment Check의
안전성도 고정했다 — item 4가 `python -m src.app.runner`인데 그 모듈에
`__main__`이 없어 오늘은 import일 뿐이다. 누가 `__main__`을 추가하는 순간
release 점검이 **실 runtime에 대한 전체 파이프라인 실행**으로 바뀌므로
그 부재를 테스트로 못박았다(`ReleaseEnvironmentCheckStaysSafeTests`).)

이전 갱신: 2026-08-17 (C38 — **Audit Rotation Sprint.**
C35~C37이 파이프라인을 봤다면 C38은 **감사 도구 자신**을 본다. 그리고 이 저장소가
가장 싫어하는 모양이 테스트 디렉터리 안에 있었다: **초록색으로 통과하는 침묵.**
(1) **테스트 760건이 `unittest.main()` 아래에 숨어 있었다 (P1)** — 그 줄은 쓰여
있는 자리에서 실행된다. 54개 파일 중 **20개**가 그것을 파일 중간에 두고 있었고,
실측: `python tests/test_observability.py` → **Ran 44 tests ... OK**(파일의 11%),
같은 파일이 pytest에서는 411건. 스위트는 pytest로 돌아 아무것도 깨져 있지 않았고,
그래서 오래 살아남았다 — 그러나 파일 하나를 직접 돌리는 것은 개발 중 가장 흔한
동작이고 그때의 `OK`는 **커버리지처럼 읽히는 침묵**이다. 20개 전부 수정 + 고정.
(2) **옮기자마자 두 번째가 드러났다** — 직접 실행이 파일 전체를 돌기 시작하자
15건이 `ModuleNotFoundError: ops_status`로 죽었다. 모든 테스트가 `src/`는
`sys.path`에 넣지만 **루트는 넣지 않고**, pytest만 rootdir을 스스로 넣어 준다.
5개 파일 헤더 수정, 20개 파일 직접 실행 전부 OK.
(3) **주석이 약속한 테스트가 없었다 (E-11 계열, 보안 인접)** — 레이어링 때문에
일부러 두 벌로 두는 규칙 셋 중 둘은 복사본 비교 테스트가 있었고, **없는 하나가
하필 "tests assert the two copies agree"라고 적어 둔 쪽**이었다.
`safe_event_filename()`은 경로 traversal(BUG-15)과 Windows 경로 길이 때문에
생긴 함수이고, 두 복사본이 각각 Agent가 쓰는 이름과 OneDrive가 나르는 이름을
정한다. 행동 기준 비교 테스트를 신설(`../target/X`, NTFS 스트림, 경계값
119/120/121, 250자 등 실제로 문제였던 입력으로) + 규칙 자체의 두 성질과
파일명 충돌까지 함께 단언. `_is_sole_identifier()`(daily↔monthly)도 같이 닫았다.
(4) **문서가 없는 파일을 가리키고 있었다** — backtick 경로 186건 중 2건
(`AGENT.md`의 `scheduler/…`, 실제로는 `src/scheduler/…`). 수정 + 고정.
(5) **"두 번 읽으니 두 배"는 반증됐다** — `ops_status`가 `processed/`를 두 번
완독하는 것은 사실이지만 두 번째는 warm이다(cold 5.09s vs warm 0.43s). 공유 읽기를
실제로 구현해 순서를 번갈아 3회 측정: **3%**. 유실 탐지기에 경계를 가로지르는
매개변수를 넣을 값이 아니라 되돌렸고, 숫자를 docstring에 남겼다 — 다음 사람이
같은 가설을 다시 세우지 않도록. 진짜 비용은 줄지 않는 디렉터리의 첫 훑기이고
그것은 B-6 결정이다.
(6) 결함 0으로 닫은 축: TODO/FIXME/HACK/XXX(실제 표식 0), `__all__` 정합성,
10개 패키지 clean import, 중복 본문 스윕(2건 모두 의도적). SKIP 4건은 전부
symlink 권한(Windows 개발자 모드) — 숨긴 문제가 아니다.
전체 Regression **2,366 passed / 0 failed** (baseline 2,355))

이전 갱신: 2026-08-17 (C37 — **Two Verdicts Sprint.**
한 실행에 대해 **판정을 두 번** 내리는 곳이 있다 — Run Manifest(`last_run.json`,
Task Scheduler와 `ops_status.py`가 읽는다)와 Operations Dashboard의 `Overall` 열
(사람이 Notion에서 보는 것). 둘을 이어 주는 것이 아무것도 없었고 **양쪽 방향
모두로 어긋나 있었다.** docs/14 §4가 한 문장으로 경고해 둔 두 실패를 동시에
하고 있었다("DEGRADED를 SUCCESS로 접으면 실제 고장이 숨고, FAILED로 접으면
늑대 소년이 되어 아무도 안 본다"). 실측:
`collector failed=1` → Dashboard **FAIL** / manifest SUCCESS·exit 0,
`late_update`·`monthly` FAILED → Dashboard **OK** / manifest DEGRADED·exit 3.
(1) **늑대 쪽** — `failed`는 Event **파일** 수이고, `app/runner.py`는 같은 숫자
옆에서 "not a component failure: docs/03 §53 makes per-file isolation the
design"이라 적고 SUCCESS로 기록한다. Dashboard만 Daily Close 유실과 같은 등급으로
올리고 있었다. 이제 WARN — 형제인 `rejected`가 처음부터 갖던 등급이다.
(2) **숨은 고장 쪽은 실수가 아니라 구조였다** — `late_update`와 `monthly`는
아홉 단계 중 **예외를 던지지 않고 FAILED를 기록할 수 있는 딱 두 단계**인데
(나머지는 실패하면 run이 중단돼 행 자체가 안 써진다) 둘 다 열이 없어 판정 함수에
**닿을 수조차 없었다.** `Failed Steps`(Rich Text, 18번째 열)를 추가하고 Runner가
manifest recorder에서 그대로 넘긴다 — 두 번째 판단이 아니라 같은 판단의 전달이다.
**부류를 고쳤지 두 사례를 고친 게 아니다**: 나중에 늘어나는 단계도 자동으로 접힌다.
(3) **관계를 방향까지 못박았다** — 둘은 같은 판정이 아니다(Dashboard는 실행을
degrade시키지 않는 행 단위 사실에도 WARN을 준다). 못박은 것은 `Dashboard OK ⇒
manifest SUCCESS`, `manifest DEGRADED ⇒ OK 아님`, `Dashboard FAIL ⇔ manifest
FAILED`이며, `_SEVERITY`를 복사하지 않고 읽어 모든 단계에 대해 검사한다.
(4) 나머지 판정 쌍(프로세스 exit vs manifest, `ops_status` 3 vs Runner 3, 두
탐지기)은 전수 확인 결과 **결함 없음** — 근거를 적어 뒀다(같은 조사를 다시 하지
않기 위해).
전체 Regression **2,355 passed / 0 failed** (baseline 2,348))

이전 갱신: 2026-08-17 (C36 — **Schema Migration Sprint.**
C32와 C33은 Dashboard가 사실을 말하게 하려고 `OPS_RUNS`에 열을 넷 더했다 —
13열(C31) → 15열(C32) → 17열(C33). **그러면서 이미 Database를 만들어 둔 운영자를
남겨두고 왔다.**
(1) **스키마가 자란 뒤 기존 Database를 고칠 방법이 없었다 (P1)** — 먼저 만든
Database는 자라지 않는다. `record_run()`이 Notion이 들어본 적 없는 Property를
보내므로 **매 실행 400, 영원히**. 실패 자체는 안전하고(행은 큐에 쌓이고 이유도
남는다 — C32 §11이 실측) 없던 것은 **빠져나올 길**이었다: 어느 Property가
거절당했는지 정확히 읽고도 그것을 추가할 명령이 없었다.
`bootstrap_dashboard_properties(client)`를 추가했다 — 없는 것만 만들고 기존
Property는 정의째 그대로 두며(옵션을 설정해 둔 Select도 안전하다), Title이 Notion
기본값 `Name`이면 `Run ID`로 rename한다(두 번째 Title은 만들 수 없으므로 유일한
길). 배선하지 않았다 — `init_notion.py`가 실 Workspace를 건드리지 않는다는 고정은
옳고, 이건 운영자가 직접 부르는 명령이다(docs/13 §3-⑧-4, 실측으로 출력까지 확인).
(2) **두 번째 구현을 하지 않았다** — "어느 Property가 빠졌나"는 `notion.bootstrap`이
PROJECTS에 대해 이미 답하는 질문이고, 같은 질문의 두 구현은 두 답이 갈라지는
방법이다. `diff_properties()` / `_bootstrap_title_property()`를 매개변수화해
재사용했고(기본값은 §8 그대로라 기존 호출자 무변경), 그 덕에 **rename 후 재조회**
함정도 공짜로 넘겼다 — 실측: 재조회 없으면 Title이 쓰던 이름(`Overall`)의 열이
"이미 있음"으로 판정돼 **끝내 만들어지지 않는다**.
(3) **운영자가 읽는 줄이 출구를 가리키게** — `ops_status`의 "Dashboard 기록이 N일째
밀렸다" ATTENTION이 이제 가장 흔한 원인과 고치는 명령의 위치까지 말한다.
(4) **코드가 문서를 가리키는 89개 포인터를 아무도 검사하지 않았다** — 전수 조사
결과 **결함 0건**이라 고칠 것은 없고, 이미 가진 성질에 울타리만 쳤다. E-11과 같은
렌즈를 숫자가 아니라 상호 참조에 겨눈 것이다.
전체 Regression **2,348 passed / 0 failed** (baseline 2,334))

이전 갱신: 2026-08-17 (C35 — **Cross-Run Sprint.**
C34가 한 실행 **안**의 순서를 봤다면 C35는 실행과 실행 **사이**를 본다: run N이
도중에 죽었을 때 run N+1이 그 잔해를 무엇으로 읽는가.
**신규 결함 0건** — 네 Sprint 연속 매번 결함이 나온 뒤 처음이라 그 자체를 결과로
기록한다. 경계는 실제로 튼튼했다: 모든 시나리오가 복구되거나 탐지된다.
(1) **`abort → 재실행` 테스트가 없었다 (커버리지 구멍, 7건 신설)** —
`WholePipelineIdempotencyTests`가 `success → 재실행`을 다섯 테스트로 촘촘히 덮는데,
운영자가 실제로 만나는 쪽(예약 Runner는 지난번에 무슨 일이 있었든 다음 트리거에
그냥 다시 돈다)은 어디에도 없었다. 단계별로 abort시키고 재실행해 **복구되거나
탐지되거나** 둘 중 하나임을 단언한다.
(2) **전수 실측 결과**: `notion_sync`·`daily`·`backup`·stale lock은 전부 복구되고
중복도 없다. **`history_filter`만 복구되지 않는다**(A-20의 창 — Collector가 이미
소비했다). 그 경우 요구 사항은 복구가 아니라 탐지이고, `ops_status`가 Event
이름을 대는 것을 실측으로 확인했다. 파일이 읽히지 않게 된 변형은 "잃었다"가
아니라 "판단할 수 없다"로 따로 보고되며 그 구분도 고정했다.
(3) **모순처럼 보이는 조합은 모순이 아니다** — run N이 FAILED, run N+1이 SUCCESS,
그 사이 Event 하나가 사라진 상태. Run Manifest는 `last_run.json` **한 파일**이라
run N의 기록이 덮인다(이름 그대로의 설계). ATTENTION이 더 오래 사는 신호이고
실제로 남아서 말한다.
(4) 결함 없음으로 **닫은** 것들: Monthly의 두 watermark(반쯤 커밋된 달, crash를
넘어 살아남은 dirty), C34가 의심했던 "step 6의 generated_dates가 dirty로 표시되지
않는다"(`check_coverage`가 달의 모든 날을 요구하므로 이미 통합된 달에 새 날이
생길 수 없다), 중복 event_id 재전달, Agent 재전송.
(5) **orphan 탐지기의 사각을 두 번째로 조사했다** — `processed/`를 정리하면 탐지가
꺼지는 것을 실측했는데, `RetentionErasesTheEvidenceOfALossTests`가 같은 측정과
같은 논증으로 **이미** 특성화하고 B-6에 묶어 두었다. 세 번째로 다시 하지 않도록
렌즈와 결과를 기록.
(6) **"지난 실행이 중단됐다면"이 운영자 문서에 없었다** — `docs/11` §23은 세 줄
짜리 배포 시점 테스트 절차다. C35의 실측을 `AGENT.md` §6a-2 표로 넣고 drift
테스트를 붙였더니 즉시 둘을 잡았다(`dashboard` 행 누락, 인용문이 줄바꿈으로
쪼개져 실제 출력과 불일치).
전체 Regression **2,334 passed / 0 failed** (baseline 2,320))

이전 갱신: 2026-08-17 (C34 — **Execution Order Sprint.**
C33 §3의 뿌리가 버그가 아니라 **순서**였다는 데서 출발했다: step 5와 step 6이 같은
실행이라 사람이 끼어들 창이 없다. C34는 그 질문을 파이프라인 전체에 던진다 —
어떤 단계가 앞 단계의 결과를 잘못 가정하는가, 그리고 **순서 자체를 무엇이 지키고
있는가.**
(1) **중단된 실행이 SUCCESS로 보고됐다 (P0)** — `_Recorder.begin()`은 "어느 단계가
진행 중인지 알아 탈출한 예외를 그 단계에 귀속시키는" 물건인데, 아홉 단계 중
**둘**(`notion_sync`, `daily`)이 그것을 부르지 않았다. 그리고 둘 다 첫 동작이
docs/10 §46이 "손상된 채 발견될 수 있다"고 명시한 state 파일을 읽는 단계다.
`overall_status()`는 *기록된* FAILED만 접으므로, 하나도 기록되지 않으면 중단이
깨끗한 실행과 구분되지 않는다. 실측: 4·6단계 중단 → `STEP_ABORTED NONE`,
manifest **SUCCESS / exit 0**. 대조군인 7단계(Backup, `begin()` 호출함)는
FAILED / 2. 6단계 줄이 최악이다 — **그 단계가 Company History를 쓴다.** 수정 후
DEGRADED/3, FAILED/2. 그리고 "결과를 기록하는 모든 component는 먼저 자신을
알려야 한다"를 불변식으로 고정(C34 §1).
(2) **실행 순서를 지키는 것이 소스 배치뿐이었다** — 순서는 docs/07 §37, docs/09
§50-51, 그리고 run_once 자신의 주석 둘이 정하는 **데이터 의존성**인데(Late Event가
고친 Daily를 Backup이 실어야 하고, Monthly는 확정된 Daily를 읽어야 한다) 아무것도
검사하지 않았다. `PIPELINE_COMPONENTS`는 dict **선언** 순서일 뿐이고, §1이 찾은
대로 실제 실행 순서와 어긋나 있었다. 의존쌍 9개 + 선언순서=실행순서 + Lock 괄호를
불변식으로 신설(C34 §2).
(3) **`RUNTIME_DIR`은 19개 경로 중 3개만 움직이는 손잡이였다** — 나머지 16개는
여섯 모듈이 import 시점에 얼려 둔 각자의 `PROJECT_ROOT`에서 온다. 운영에선 두
루트가 같아 무해하지만, 이 Sprint의 조사 중 **실제로 물렸다**: `RUNTIME_DIR`을
다시 묶어 샌드박스라 믿고 돌린 것이 진짜 파이프라인이었고, Company History는 temp
트리에 쓰이고 **라이브** 포인터가 그 너머로 전진했다(2026-08-10 → 2026-08-16,
CONSISTENT → STATE_INCONSISTENCY, 엿새가 영구 유실). 포인터는 즉시 복구했다.
C31 §10의 호출시점 파생 수정은 여기 쓸 수 없어(16개가 남의 모듈 것) 불완전함을
남기되 **조용하지 않게** 했다 — `main()` 첫 줄에서 두 루트가 다르면 거부한다(C34 §3).
(4) **step 4가 곱게 처리한 파일에 step 5가 죽는다** — 4b는 가드하고, step 5는 같은
파일을 열한 줄 뒤에 가드 없이 읽는다. 그 옆 주석의 측정 블록(`Daily NONE / backup
MISSING`)은 가드가 막았다고 적혀 있는데 **다시 재면 여전히 일어난다.** 중단이
4단계에서 5단계로 옮겨갔을 뿐이다. 둘 다 결함이 아니다(step 5는 BUG-20의 의도된
설계) — 낡은 것은 **주장**이라 좁혔다. 가드가 사는 것은 생존이 아니라 귀속과
증거다. 부수 결과: C33 §1의 `Notion Unreadable` 칸은 4a 경로에서만 0이 아닐 수
있다(C34 §4).
전체 Regression **2,320 passed / 0 failed** (baseline 2,289))

이전 갱신: 2026-08-17 (C33 — **Control Tower Sprint.**
C32는 "Notion에 쓰는 숫자가 디스크 위의 사실과 같은가"를 물었다. C33은 그 다음
질문 — "Dashboard가 기록 화면이 아니라 Control Tower가 되려면 무엇이 더 있어야
하는가" — 이고, 그 길에서 **세 번째 종류의 침묵**이 나왔다: 기계가 아니라
사람이 쓴 내용이 사라지는 경로.
(1) **Dashboard가 볼 수 없던 두 개의 Notion 사실**(C32 §6 해소) — 파싱 불가 Event
파일은 애초에 `SyncResult`가 되지 않고, Retry Queue 깊이는 실행 결과가 아니라
큐의 속성이라 둘 다 `notion_sync_results`에서 유도할 수 없다. 그래서 10건이
읽히지 않은 실행이 **동기화할 것이 없던 실행과 같은 행**이었다.
`Notion Unreadable` / `Notion Queued` 칸 신설, Runner가 계산해 넘긴다. 행의
산수(`synced+skipped+retried == 처리 건수`)를 전 조합으로 고정(C33 §1).
(2) **네 Database의 범위는 이미 정해져 있었다 — A-16 정정** — `docs/14 §1`이
Operational Projection을 `Notion (PROJECTS / OPS_RUNS)`로, **이름까지 적어**
고정한다. 즉 나머지 넷을 쓰는 것은 "앞서 나가는 것"이 아니라 Spec 변경이다.
A-16은 C10부터 이것을 "결정 대기"로 적어 왔는데, 나중에 쓰인 docs/14가 이미
닫았다. 그래서 구현이 아니라 **수정**이 나왔다:
`bootstrap_dashboard_databases()`의 기본값이 다섯 개 전부였고, 설정 문서를 따라간
운영자는 삭제 경로도 없는 빈 Database 넷을 실제 Workspace에 만들게 된다.
기본값을 `CONTRACTED_DATABASES = (OPS_RUNS,)`로(C33 §2).
(3) **사람이 쓴 Decision Context는 Company History에 닿지 않는다 (신규, 실측)** —
`review_cli`가 묻고 `history/review`가 저장하고 `daily/markdown`이 렌더링하는데,
step 5(Candidate 쓰기)와 step 6(Daily 렌더링)이 **같은 실행**이라 사람이 review할
수 있는 시점은 언제나 렌더링 이후다. 그리고 step 6.5는 *새* Event만, step 6은
덮어쓰기 거부. 실측: review 저장 True, 디스크 재확인 True,
`NO_LATE_EVENTS`, `FileExistsError`, **Company History에 반영 False, Daily 파일
무변화**. `_kept_but_not_rendered()`는 event_id가 있으니 깨끗하다고 답한다.
잃는 것이 **사람이 쓴 글**이라 재실행으로 재현되지 않는다. 탐지 추가, 복구 두
안은 전부 결정이라 SKIP(C33 §3).
(4) **`ops_status.py`는 `@dataclass`를 담을 수 없다** — `from __future__ import
annotations` + `dataclasses`의 `KW_ONLY` 해석 + 테스트 헬퍼가 `sys.modules` 등록
없이 `spec_from_file_location`으로 로드하는 것, 셋이 겹쳐 **import 시점에** 죽는다.
실측 293개 실패, 대부분 candidate와 무관. `NamedTuple`로 바꾸고 ban과 **ban의
전제** 둘 다 테스트로 고정(C33 §4).
(5) **영원히 거부되는 Notion 요청은 ATTENTION에 닿을 수 없었다** —
`NotionAPIError.status_code`는 모든 HTTP 실패에서 설정되고 테스트 넷이 단언하는데
**프로덕션에서 읽는 곳이 하나도 없었다.** 그런데 `ops_status`의 ATTENTION은
PERMANENT만 나열하고 Notion 실패는 전부 RETRYABLE이었으므로, 400/401/403/404는
manifest를 통해서는 ATTENTION에 **닿을 수 없었다** — C32 §14의 나이 검사가 사흘 뒤
잡아 주는 것이 유일한 길이었다. docs/14 §5 자신의 PERMANENT 정의를 적용해 분류.
exit code·큐 동작·UNKNOWN 우선순위가 바뀌지 않는 것을 각각 테스트로 고정(C33 §5).
(6) 같은 렌즈를 예외 속성으로 넓혀 `SignalError.filename`의 docstring이 없는
소비자를 주장하고 있는 것을 찾아 정정. 동작 결함은 없다(C33 §6).
(7) 목표가 요구한 TODO/FIXME/HACK/XXX 전수 — 프로덕션 **0건**(유일한 hit은 라벨
모양을 설명하는 산문). 남은 것은 전부 이 파일에 있다(C33 §7).
전체 Regression **2,289 passed / 0 failed** (baseline 2,244))

이전 갱신: 2026-08-17 (C32 — **Notion Dashboard Fidelity Sprint.**
C31이 물은 "쓴 쪽과 읽는 쪽이 어긋나는가"를 시스템 **밖으로** 내보냈다: 이
시스템이 Notion에 쓰는 숫자가, 디스크 위의 실제 상태와 같은 사실을 말하는가.
20개 항목을 `SOURCE → TRANSFORM → NOTION RECORD → DISPLAY → FAILURE`로
추적했고, **어긋난 것은 언제나 DISPLAY 칸이었다** — 값은 정확히 계산돼 있고,
Notion에 도착하지 못하거나 도착해서 다른 뜻으로 읽힌다.
(1) **막힌 유입 경로와 한가한 일요일이 바이트 단위로 같은 행이다 (P0)** —
`run_intake()`가 promote 못한 파일을 다섯 통에 나눠 담는데 Dashboard는 `moved`
하나만 읽었다. 실측(unparseable 10 + `.tmp-` 1 + 이동 실패 1):
`Transport Moved 0 / Accepted 0 / Rejected 0 / Overall OK`. Desktop 1–3이 한 달
안 와도 매 실행 OK. **수정** — `Transport Blocked` 칸 + `count_blocked_intake()`
(스스로 지워지는 두 통은 세지 않는다 — 영구 경보를 만들지 않기 위해).
(2) **`Overall`이 바로 옆 칸과 반대되는 말을 한다** — docstring이 언제나 약속한
"rejected/failed events" 중 `rejected`는 **함수의 매개변수도 아니었고**, Notion
Sync 실패는 어느 분기에도 없었다. 실측: `Rejected 8 → OK`, `Retried 5 → OK`.
WARN 분기의 `"BACKUP_REVIEW"`는 `BackupStatus`가 낸 적 없는 값이라 영원히 죽은
가지였다(docs/08 §34의 이름은 `BACKUP_REVIEW_REQUIRED`). **수정** — 건강한 값의
집합을 닫아 오타로 침묵할 수 없게 하고, 판정 입력이 전부 같은 행의 칸이라는 것을
구조적으로 고정.
(3) **Notion 날짜 선택기 한 번이 Event를 영구히 큐에 가둔다 (BUG-14/BUG-29 수정)**
— 저장된 `Last Updated`가 `{"start": "2026-08-17"}`(시간 없이 고른 값)이면
naive/aware 비교가 `TypeError`, `"yesterday"`면 `ValueError`. `sync()`는
`NotionAPIError`만 잡으므로 둘 다 탈출 → `NOTION_FAILED`(UNKNOWN) → Retry Queue
→ **매 실행 똑같이 실패.** "무엇을 신뢰할지 결정 필요"라며 미뤄져 있었는데,
그 결정은 **같은 함수 한 분기 위**(`is None` → 진행)에 이미 있었다. 같은 답을
주고, 그 사실을 `SyncResult.error`로 로그까지 보낸다. 진행이 자가 치유이기도 하다.
(4) **`init_notion.py`가 원격 문자열 4종을 무방비로 찍는다 (신규 보안)** — C31
§7/§8이 자매 entrypoint에서 고친 그 결함. Page 이름 하나가 운영자가 마지막에 읽는
**결론 줄을 위조**하고, 502를 대신 답하는 proxy가 `Bearer ntn_…`를 그대로 흘린다
(docs/04 §56). sink에 `one_line`/`redact`, `format_report()`는 줄 단위로.
(5) **`Notion Synced`가 쓰지 않은 Event를 썼다고 센다** — `NOTION_SKIPPED_OLD_EVENT`
(§35 "적용하지 않았다")가 성공에 쓸려 들어갔다. 실측: 4건 전부 skip인데
`Notion Synced: 4`, 실제 쓰기 0. **수정** — `Notion Skipped` 칸으로 분할, 세 수의
합이 항상 처리 건수와 같도록.
(6) **Dashboard의 모든 숫자가 rename을 0으로 숨긴다** — `getattr(x, name, 0)`.
`app/runner.py`가 형제 숫자들 옆에 금지해 둔 바로 그 패턴이다. 실측: 50건
accepted가 `Accepted 0 / Overall OK`로, 영원히. **수정** — 직접 접근.
(7) **§62 중복 가드가 rich_text 첫 조각만 읽는다** — Notion은 서식 구간마다 항목을
쪼개고 mention에는 `text` 키가 없다. `EVT-` + `1`로 저장된 같은 id를 못 알아본다.
`dashboard._page_title()`이 이미 옳게 읽고 있던 방식(`plain_text` 이어붙이기)으로.
(8) **실 Workspace 읽기 전용 측정** — Notion Sync는 살아 있다(health PASS, §8
Property 11개 전부 존재). Operations Dashboard는 **한 번도 실행된 적이 없다**:
PROJECTS가 Workspace 루트에 있고 integration에 공유된 Page가 0개라
`NEEDS_SHARED_PAGE`. Page 공유·OPS_RUNS 생성·`NOTION_OPS_RUNS_DATABASE_ID` 설정은
운영자 작업이라 SKIP(C32 §9에 정확한 절차).
(9) **이 머신에서 Runner는 아예 뜨지 않는다** — `COMPANY_OPS_HISTORY_START_DATE`
포함 5개 환경변수가 `.env`·User·Machine 어디에도 없다. 코드 결함이 아니라 배포
상태이고, `.env` 자동 로딩을 넣는 것은 정책이라 SKIP·기록.
(10) 회사 단위 상태(Desktop 1~4 침묵, Agent, Daily/Monthly 구멍, ATTENTION 전체)는
여전히 Notion에 없다. `OPS_RISK` 스키마가 이미 그 모양이지만, ATTENTION을
구조화된 값으로 꺼내는 seam이 없고 Severity/Area 매핑은 정책이라 SKIP(C32 §12).
(11) **읽을 수 없는 전송 기록이 어느 숫자에도 없었다** — `agent/delivery.py`가
`sent/`의 손상된 파일을 `continue`로 버렸다. 바로 그 자리 주석은
`history/reconciliation.py`를 "unreadable 입력을 따로 보고한다"는 선례로 인용하고
있었고, 형제 셋(`reconciliation`, `outbox.DrainSummary`, `CompanyActivitySnapshot`)은
전부 그렇게 한다. **인용하고 따르지 않은 것은 이 하나뿐.** 결과: 손상된 파일 하나가
Event 하나의 전달 검증을 건너뛰게 하고 `전달 정합성 : OK`가 찍힌다. 수정 +
세 번째 판정 `UNKNOWN` 신설(C32 §13).
(12) **Notion 두 큐가 쓰는 `added_at`/`attempt_count`를 아무도 읽지 않았다** —
BUG-13이 "잠깐 죽었다"와 "영원히 거부한다"를 로그의 이유 문자열로 갈랐는데,
같은 질문의 나머지 절반(얼마나 오래, 몇 번)은 어디에도 닿지 않았다. Run
Manifest의 `queued=`는 대체재가 아니다(지난 실행 한정, 실패했을 때만 출력,
`to_event()`가 깨지는 큐 항목은 아예 못 셈). `ops_status.py`에 **NOTION 블록**
신설, 임계값은 `SILENT_AFTER_DAYS` 재사용(C32 §14).
(13) **`run_agent.py`가 한 줄 위에서 막은 위조를 세 줄 뒤에서 허용했다** —
`date_result.errors`는 `one_line()`으로 감싸고, `agent.run_once()`가 **그 문자열을
join해서 만든** `result.error`는 `[FAILED]` 블록에 그대로 찍었다. C31 §7의 "절반짜리
수정"과 같은 모양(C32 §16).
(14) **Run Manifest를 렌더링하는 곳이 둘인데 다섯 필드 전부에 대해 의견이 달랐다** —
`read_summary()`가 검증하는 것은 enum 셋뿐이다. 그중 `reason`은 C31 §7이 **같은
파일 20줄 위에서** redact한 그 원격 응답 본문인데(`reason=queued[0].error`),
`_report_run_summary()`는 디스크를 거쳐 전문 그대로 찍고 있었다. `ops_status`
쪽은 metrics **값**만 감싸고 **키**는 안 감쌌다 — 바로 위 주석이 "today's metric
**list**"라고 적어 둔 그 list가 키다(C32 §17). 그리고 `[FAILED] Backup:`은 git
stderr를 통째로 찍었다 — push 실패 시 git이 되울리는 remote URL에 토큰이 들어
있을 수 있다(C32 §18).
(15) **"쓰고 아무도 읽지 않는 필드"를 전수로 셌다** — dataclass 필드 255개 중
정의 모듈 밖에 독자가 없는 것 **30개**. 대부분 정당하고 셋은 아니었다:
`RunSummary.finished_at`(읽는 곳이 아예 없음 → LAST RUN에 `소요 시간`),
`UnreadableEvent.event_path`(ATTENTION이 파일 이름을 안 댔다),
`PendingDashboardRecord.queued_at`(§14가 형제 큐만 챙겼다). 남은 27개는 목록으로
기록(C32 §20).
(16) **Workspace 검색이 첫 100건만 보고 "이게 전부"라고 답했다** — Notion
`/search`의 `has_more`/`next_cursor`를 무시했다. 잘린 목록은 "Company Ops page가
공유돼 있는가"에 **자신 있게 틀린 '아니오'**를 답한다. 상한 있는 페이징으로
수정하고, 멈춘 사실은 `search_truncated`로 보고한다(C32 §21).
(17) **DR 기록(미수정)**: `runtime/state/`는 백업 범위 밖이라(docs/08 §26) 복원된
머신은 `notion_retry_queue.json`과 `dashboard_pending.json`을 잃는다. 나쁜 것은
손실이 아니라 **조용하다**는 것 — §14가 만든 NOTION 블록은 파일이 없으면 `0`을
보고하고, 그건 큐가 빈 것과 같은 줄이다. §4와 같은 모양, DR 층에서. 범위 변경은
docs/08 결정이라 SKIP(C32 §22).
그리고 §8이 **의도치 않게 DR 경로를 하나 고쳤다** — 오래된 백업에서 되살린 큐의
이미 동기화된 Event를 §62 가드가 이제 알아본다(C32 §23).
전체 Regression **2244 passed / 0 failed**)

이전 갱신: 2026-08-14 (C31 — **쓴 쪽과 읽는 쪽이 어긋난다 Sprint.**
한 모듈이 쓴 것을 다른 모듈이 되읽는 지점을 전수 대조했다 — 여덟 쌍 중 **세 쌍이
어긋나 있었고 셋 다 조용한 유실**이었다((1)~(3)). 그 조사가 끝난 자리에서 같은
질문("이 문자열은 어디서 왔고, 받는 쪽은 그것을 어떻게 읽는가")을 계속 밀어
나머지 스물네 건이 나왔다.
(1) **`Fixed: ` 로 시작하는 평범한 요약 하나가 그 항목을 Monthly에서 통째로
지운다** — `monthly/parser._first_bullet()`이 "라벨처럼 생겼는가"(`^[A-Z][A-Za-z ]+:`)를
물었고, 사람이 쓰는 요약은 늘 그렇게 생겼다. 공격 입력도 손편집도 필요 없다.
Daily는 멀쩡하고 Monthly는 `MONTHLY_GENERATED`를 보고한다. 실 파이프라인 실측:
평범한 항목 **5건 중 4건이 그 달에서 사라진다**(`Consolidated Items: 5` → `1`).
**수정** — 렌더러가 실제로 쓰는 라벨 집합만 건너뛴다.
(2) **`event_id`가 빈 문자열이면 Late Event가 매 실행 다시 추가된다** —
`existing_event_ids()`의 `(\S.*)`가 `- Event ID: ` 를 못 읽어 §38 중복 가드가
무력화된다. 실측 3회 실행 → 같은 항목 3벌, `Late Events Added: 3` 옆에
`Event Count: 1`. **수정**, 그리고 그 때문에 생기던 `ops_status` 오탐도 함께 닫았다
(C30이 넣은 prefix 슬라이싱의 반대편 모서리).
(3) **Monthly 렌더러는 네 Category 밖의 항목을 어느 Section에도 넣지 않고 버리는데
`Consolidated Items`는 그것까지 센다** — Daily 쪽 형제(`test_daily_history.py`)는
기록돼 있었고 Monthly에는 아무도 겨눈 적이 없다. Daily는 요약이라도 남지만 Monthly는
아무것도 남지 않는다. Section 결정은 docs/09 §14이므로 **탐지만** 추가.
(4) **신규 보안 — Secret 게이트가 자기 목록에 있는 이름을 대소문자 때문에 못
알아본다.** 실제 remote로 E2E: `daily/ID_RSA` → BACKUP_SUCCESS, push SUCCESS,
`git show main:daily/ID_RSA`로 키가 읽힌다. `daily/id_rsa` → BACKUP_FAILED, 원격 무변화.
BUG-55와 같은 뿌리의 두 번째 위치. 게이트를 바꾸면 새 BACKUP_FAILED 조건이
생기므로(E-15) **탐지만** 추가.
(5) **Baseline이 1916/0이 아니었다** — 이 머신에서 2건 실패. 둘 다 코드가 아니라
테스트가 환경을 고정하고 있었다. 하나는 실시간 시계에 매인 fixture(어제까지 통과,
오늘 실패), 하나는 PowerShell **UI 언어**에 매인 문자열(`WhatIf` vs `What if` —
같은 머신에서 부모 프로세스 로케일만 달라도 갈린다). 둘 다 수정.
(6) `backup/git_ops`의 인증 실패 분류가 영어 git 메시지에 의존한다 — 이 머신의
Git for Windows에는 번역 카탈로그가 없어 현재 무해함을 **실측**하고,
`GIT_TERMINAL_PROMPT` 선례대로 `LC_ALL=C`로 의존 자체를 제거했다(분류 목록은
그대로 — 목록을 넓히는 것이 BUG-52다).
(7) **BUG-58 재측정을 요청받은 대로 수행했고**(F-3이 "남은 범위 재측정 필요"라고
적어 둔 것) sync 경로에는 남은 범위가 없음을 실측했다. 그 추적 중 **신규 보안
결함**이 나왔다 — 같은 원격 응답 본문을 로그는 redact하는데 `run_company_ops.py`의
stdout은 그대로 찍는다(토큰 전문 + 결과 줄 위조). sink에 `redact`/`one_line`을
걸어 수정.
(8) **그 질문을 자매 entrypoint에 던졌더니 더 나쁜 것이 나왔다** — 개행이 든
`event_id` 하나가 `ops_status.py`의 **ATTENTION 절 안에 통째로 위조된 줄**을
세운다. 실측된 위조 문구는 "모든 검사 통과 — 사람이 지금 할 일은 없다"였다.
AGENT.md §6이 가장 먼저 보라고 하는 뷰다. sink에 `one_line`을 걸어 수정
(BUG-6과 같은 모양, 아무도 겨눈 적 없는 자리).
(9) **날짜 경계 산술을 훑다가 C17이 Agent에만 물었던 질문이 나왔다** — Runner의
state 포인터가 **미래**를 가리키면 Company History가 그 날짜까지 영구히 멈추는데,
Scheduler는 COMPLETED를, 정합성 검사는 CONSISTENT를 보고한다(실측: 4개월 정지,
ATTENTION 0건). 형제를 전수로 훑어 **세 개**를 찾았고 세 번째가 가장 나쁘다 —
`backup_state.last_successful_backup`이 미래면 "이 History는 이 머신에만 있다"는
**안전 검사 자체가 침묵한다.** `agent/status.py`가 자기 state에 대해 **이미 같은
답을 적어 두었으므로** 그것을 형제 셋에 적용했다 — 탐지만, 오탐 불가.
(10) **그 조사가 스스로 함정에 걸렸다** — `RUNTIME_DIR`을 돌려도 `AGENT_DIR`은
import 시점에 얼어 있어 AGENT 블록만 **실기계**를 읽고 있었다. 하마터면 "C17의
선례가 연결돼 있지 않다"고 잘못 기록할 뻔했다(C13 결함 2의 두 번째 자리).
호출 시점 파생으로 고쳤고, 모듈 레벨에서 경로를 얼리는 것을 AST로 금지했다.
(11) **`exists()`가 답하는 질문은 그 코드가 묻는 질문이 아니었다 (P0)** —
`2026-08-12.md`라는 **디렉터리** 하나가 Scheduler에게 "그 날은 이미 썼다"고 답하게
만든다. 실측: `COMPLETED`, `generated=('2026-08-12', …)` — **쓰지도 않은 날을
생성했다고 자기 결과에 적고** 포인터를 그 너머로 옮긴다. 그 날이 빠진 것을 잡으라고
만든 두 검사가 **둘 다** 있다고 동의한다. Monthly도 같다(`MONTHLY_UNCHANGED`).
"산출물이 있는가"(`is_file()`)와 "이름이 쓰였는가"(`exists()`)를 구분해 4곳 수정,
3곳은 그대로가 옳음을 확인·고정.
(12) **Monthly parser를 끝까지 판 결과 네 번째 주입 경로가 나왔다 (P0)** —
BUG-11/27은 `summary`·`evidence`를, C30 §4는 `event_id`를 지목했다. **`project_id`는
이름이 붙은 적이 없고 가장 나쁘다**: `

## Metadata`가 든 project_id 하나가
Category Section을 닫아 **무고한 Event 2건을 포함해 3/3 전부**를 Monthly에서
지우는데 `MONTHLY_GENERATED`가 나온다. escape는 docs/06 결정이라 SKIP하고,
**유실을 세는 것**(`unconsolidated`)은 파싱이 이미 일어나는 자리라 **비용 0**으로
넣어 `daily_late_update.log`까지 연결했다. 그 과정에서 BUG-39 sweep이
`MonthlyResult`를 빠뜨리고 있었다는 것도 찾아 invariant를 붙였다.
(13) **Dead Capability 전수 목록을 처음 만들었고**(A-16·E-20이 각각 하나씩만
기록해 뒀다), 그 과정에서 **진단이 거짓말을 하고 있는 것**을 찾았다 —
`init_notion.py`의 Dashboard 진단이 행복 경로에서 `다음 할 일: None`이라고 말하는데,
OPS_* Database를 만드는 함수는 **어떤 명령도 부르지 않는다**(AST, alias 해석 포함,
호출 지점 0). 운영자는 설정이 끝난 줄 알고 `NOTION_OPS_RUNS_DATABASE_ID`를 비워 두며,
그 상태에서 Dashboard 기록은 매 실행 건너뛰어진다. 자동 생성은 실 Workspace 변경이라
SKIP, **메시지를 사실로 만드는 것**은 즉시 수정. 그리고 자동 분석 세 개가 연달아
틀렸다는 것도 기록했다(패키지 밖 미사용 → 114/180 오탐, call-graph → 64/231 오탐,
alias 미해석 → 15개 중 11개 오탐). **믿을 수 있는 것은 alias를 푼 AST Call 세기뿐이다.**
(14) **Failure Isolation 감사에서 등급 역전이 두 번째 자리에서 나왔다 (P0)** —
Notion Sync(DEGRADED)가 `processed/` 파일 하나를 못 읽으면 **Daily History도 Backup도
실행되지 않고 run이 통째로 중단된다**(실측: Daily NONE, backup state MISSING). 이
단계 자신의 주석이 "Notion 실패가 Runtime을 막지 않는다"고 말하는데도 그랬다.
읽기를 per-event로 감싸 수정 — 수정 후 Daily 13개 기록, Backup 도달,
`notion_sync`는 `unreadable:1`로 FAILED. CRITICAL 쪽(History Filter)의 같은 패턴은
BUG-20이 의도로 고정해 둔 것이라 **건드리지 않았다.**
(15) **Monthly parser를 seed 고정 fuzz로 닫았고, 열거 테스트가 놓친 둘이 나왔다** —
benign 4,000건 중 **641건이 항목을 잃고** 있었다(열거 테스트는 전부 통과). 원인은
(a) 요약이 렌더러의 **라벨 이름**으로 시작할 수 있다는 것(`Lessons Learned: …`는
LEARNING 요약이 실제로 읽히는 방식이다 — 일곱 개 전부 LOST)과 (b) 그런 요약이 자기
항목의 **`event_id`를 가로챈다**는 것(§59가 그 값으로 중복 제거를 한다). 렌더러가
라벨을 **한 번씩 정해진 순서로** 쓴다는 사실로 추측 없이 판정하게 고쳤다.
benign 유실 **641 -> 0**, adversarial은 여전히 유실하지만 **silent 0**.
(16) **같은 뿌리가 인접 경로 넷에 더 있었다** — 뿌리는 파서가 아니라 **포맷**이다:
요약이 raw로 렌더되므로 `Event ID: X`라는 요약은 라벨 줄과 바이트 단위로 같고,
이 파일을 줄 단위로 읽는 모든 코드가 같은 방식으로 속는다. §38 중복 가드는
**Late Event를 영구히 버렸고**(fuzz 1,000건에 98건, Event Count 오류 429건),
§58의 `Generated At`은 **영구 위조**됐으며, ops_status의 Monthly shortfall 검사는
"거짓 경보는 못 한다"는 **자기 docstring을 어겼다**(멀쩡한 달에 `('2026-08', 999, 1)`),
E-17 유실 탐지기는 요약이 지목한 Candidate에 대해 **꺼졌다**.
규칙을 렌더러 옆에 두되 `monthly`는 선언된 leaf라 두 벌로 두고 **행동 동등성
테스트**로 묶었다.
(17) **인용이 가리키는 곳이 실제로 있는지 아무도 확인한 적이 없다** — 이 저장소는
결정을 인용으로 정당화한다(`docs/NN §M` 807건, `BUG-NN` 618건, `README RULE N`
39건). Scheduler 명세를 가리킨 인용 하나는 **줄 번호를 절 번호로 적은 것**이었고
(951은 그 문장의 줄 번호, 진짜는 §44),
`BUG-NN` 15개(65건 인용)는 초기 Audit 번호라 **BACKLOG에 옮겨진 적이 없다**.
65건을 고쳐 쓰는 대신 인용처에서 가져온 설명으로 **색인**을 만들어 포인터가 풀리게
했고, 새 dangling id는 테스트가 막는다.
(18) **`.tmp-` 잔여물을 세 디렉터리 중 둘만 제대로 부르고 있었다** — C27의 규칙이
전수 적용됐는지 아무도 본 적이 없어 `glob`/`iterdir` 22곳을 대조했다. 구멍은
**`incoming/`** 하나, 하필 `write_event_json()`이 실제로 staging하는 곳이다.
staging 파일 하나가 "Collector가 아직 가져가지 않은 Event 1건"이 되고 `is_clear`를
False로 잡았다 — 그런데 `awaiting_collection`은 *promote된 것*을 세고 그 파일은
promote된 적이 없다. 고치는 도중 내가 넣은 회귀(`name_collision` 축소)를 **기존
테스트의 docstring이 정확히 금지하고 있었고** 그것이 잡아냈다.
(19) **(1)에서 내가 만든 blind spot** — 라벨 순서 규칙의 전제("렌더러가 쓸 수 없는
배치니 산문이다")가 틀렸다. **§57이 허용하는 손편집**이 똑같은 배치를 만든다.
`- Event ID:`를 `- Owner:` 위로 옮기면 블록의 id가 사라져 **Late Event가 매 실행
다시 추가된다**(무한 증가). 순서 규칙 이전에는 찾던 id이므로 회귀다. override
하나로 닫았다 — **exclusion이 블록의 유일한 식별자를 없앨 수는 없다.** 같은 뿌리의
reader 넷 전부에 적용되고, 전에 통째로 잃던 블록은 이제 세 필드를 다 되찾는다.
(20) **운영자에게 하는 말이 코드보다 셌다** — ATTENTION 41줄을 코드와 전수 대조했고
셋이 과했다. E-17 경보의 "**어떤 실행도 이것을 넣지 않는다**"는 틀렸다(같은 날짜
Event가 하나라도 더 오면 방치된 Candidate가 **함께 들어간다** — 실측
`added_event_ids=('EVT-S','EVT-N')`). Monthly shortfall 경보는 원인 하나를 단정하고
**틀린 조치**를 지시했다(손편집 원인은 강제 rebuild가 복구한다). 그 검사의
docstring이 주장한 "거짓 양성 없음"도 "as generated"까지만 참이었다. 셋 다
사람이 할 조치를 바꾸는 오류다.
(21) **C31의 `.exists()` 스윕이 전수가 아니었고, 하나는 내가 잘못 분류했다** —
31곳을 전부 재분류했다. 놓친 것은 `agent/outbox.stage()`로, Event 이름의
**디렉터리**가 있으면 아무것도 쓰지 않고 성공을 반환했다(Agent의 내구성 경계를
건너뛴다). 차단은 돼 있었지만 설계가 아니라 운이었고, 운영자에게는 실패한
**날짜** 대신 "읽을 수 없는 파일"이 떴다. 잘못 분류한 것은 C31이 넣은
`test_name_taken_questions_still_use_exists`로, 근거로 적은 위험(디렉터리를
덮어쓴다)이 실측상 일어나지 않는다 — 거부는 한 단계 아래가 한다.
(22) **중복 결함 전부가 위반하는 성질을 아무도 단언하지 않고 있었다** — 전체
파이프라인 3연속 실행은 **완전 멱등**임을 트리 해시로 확인했다(커밋 증가 0,
state 수렴). 그런데 그 테스트에 **이빨이 없었다**: §38 가드를 눈멀게 해도 한 건도
실패하지 않는다 — 새 입력이 없으면 6.5단계가 아예 안 돈다. 연속 실행이 각각 같은
닫힌 날짜의 Event를 수집하는 모양으로 바꾸니 그제야 잡힌다(`- Event ID:` 3줄 대
**7줄**, Event Count 0이 Late Events Added 6과 모순).
(23) **Mutation Testing — 핵심 가드 17개를 부러뜨려 스위트에게 물었다** — §28에서
이빨 없는 테스트를 하나 잡은 뒤 같은 질문을 스위트 전체에 던졌다. **17개 전부
잡혔다**(결함 없음). 다만 방법론 함정 하나를 기록한다: `-x`가 보여주는 첫 실패가
**구조/인벤토리 테스트**일 수 있다 — 함수가 lambda로 바뀐 것을 정체성으로 알아챈
것이지 동작으로 알아챈 게 아니다. `_SEVERITY`와 `overall_status`가 그랬고, 그것을
빼고 다시 돌려야 진짜 행동 테스트가 드러난다.
(24) **Daily 시퀀스에 구멍이 나도 모든 지표가 정상을 보고한다** — Recovery/DR
감사에서 나왔다. 열흘 중 사흘을 지우면 `check_state_consistency()`는 **CONSISTENT**,
ATTENTION은 침묵, Scheduler는 `last_close+1`부터라 **영원히 돌아오지 않는다**.
정합성 검사가 틀린 게 아니라 **가운데를 보는 눈이 없었다**. 판정은 파일만으로
결정 가능하다(빈 날에도 파일이 쓰이므로 Daily 파일명은 끊기지 않는 구간이어야
한다). **탐지만** 추가했고, 사라진 날 중 Backup Working Copy에 아직 있는 것을
이름으로 집어 준다. `os.scandir`로 10년치 62 ms → 5.4 ms(실측 후 결정).
(25) **Monthly 시퀀스에도 같은 구멍** — 04·05를 지워도 아무 ATTENTION도 없다.
탐지 추가. 다만 조치가 Daily보다 낫고 그게 정확하다: Monthly는 Daily에서만
파생되므로 dirty 표시 후 재실행이 **내용까지 복구한다**(실측).
(26) **삭제된 Company History가 Run Manifest에 이름조차 남지 않았다** — 삭제
차단 자체는 훌륭하다(원격 전파 없음, exit 2). 그런데 Manifest는
`BACKUP_FAILED` / `reason: ""` / `changed_files=1`뿐이라 **History가 지워졌다는
사실도, 자격증명 실패와의 차이도** 알 수 없었다. `deleted_files`를 삭제 분기에도
싣고 `reason`에 사실을 적었다. 새 classification 값은 docs/14 §5의 어휘라 SKIP.
(27) **C27이 "결정이 필요하다"며 남긴 거짓 경보 하나는 결정이 필요 없었다** —
중단된 쓰기 잔여물이 `rejected/`에 들어가면 ATTENTION이 "Collector가 거부한
Event 1건"이라고 말한다. Collector가 그것을 소비하지 *않게* 만드는 것은 docs/03
결정이지만, 보고서가 그것을 뭐라고 부르는지는 아니다. 파이프라인 무변경으로
문장을 분리했고, C27의 경계 테스트 docstring에 남아 있던 낡은 주장도 같이 고쳤다.
전체 Regression **2149 passed / 0 failed / 4 skipped**
(baseline 1914 passed / **2 failed** / 4 skipped — 신규 테스트 +229))

이전 갱신: 2026-08-13 (C30 — **낡은 보조 주장 Sprint.** SKIP 판단 자체는 옳은데
그 옆에 적힌 주장이 낡아 있는 경우를 찾는다. E-13: "테스트도 있다"가 **그 분기에는
해당하지 않았다**(lock 미획득 exit code를 아무것도 고정하지 않고 있었다).
E-14: 완화 주장을 필드 단위로 재측정 — 9개 중 6개만 도달하고 `source`는 **전혀**
도달하지 않는다. E-19: C27 §6이 **모르고 완화해 둔 부분**(큐 깊이 가시화)을 기록.
A-15: 낡은 전제(BUG-5)를 재측정하다 **미기록 결함** 발견 — 개행 `event_id`가
Daily Markdown에 **가짜 `- Event ID:` 줄을 위조**한다(BUG-11/27이 `summary`·
`evidence`만 기록했고 `event_id`는 이름이 없었다). 그 조사 중 **C29가 넣은
탐지기의 false negative**(substring 매칭으로 `E-1`이 `E-10`에 가려짐)를 자체
발견·수정했다.
전체 Regression **1916 passed / 0 failed**)

이전 갱신: 2026-08-13 (C29 — **SKIP 사유 전수 재감사.** C28 §6이 확인한 것
("승인 필요"로 적힌 blocker가 이미 결정돼 있었다)을 SKIP 항목 전체에 적용한다.
A-7: 기록된 범위가 실제보다 좁았다 — 손상 후보 **1개가 모든 날짜**를 멈춘다
(실측 9일치 → 0일치). A-19: 정책은 결정이지만 **리다이렉트가 존재한다는 사실**을
말하는 것은 결정이 아니었다 — junction 탐지 추가(Backup 동작 무변경).
A-16: 기록이 맞는지 **AST로 검증**(조사 중 grep으로 한 번 틀렸고, export를 호출로
오인했다). A-10: **두 항목 중 하나는 이미 고쳐져 있었고 BACKLOG만 낡아 있었다** —
E-11의 표류가 양방향임을 보여준다.
전체 Regression **1896 passed / 0 failed**)

이전 갱신: 2026-08-13 (C28 — **경고가 사라졌다 ≠ 위험이 사라졌다.**
E-21 재감사에서 Secret 경고가 **틀린 이유로** 사라지는 것을 실측했다 — 운영자가
메시지대로 파일을 지우면 경고는 없어지지만 원격 history에서는 토큰이 그대로
읽힌다. `git rev-list --all --objects`로 history를 묻는 probe를 추가했고,
건강한 머신 7구성에서 0건임을 확인했다(§28 `.gitignore`가 있으면 발화 불가).
Spec은 건드리지 않았다 — 바뀐 것은 결정 없이 가능한 가시화의 한계선이다.
또한 A-20의 탐지기가 B-6이 지우려는 파일에 의존한다는 것을 실측해 두 결정이
하나임을 기록했고, BUG-55를 "뭔가 잘못됐다"에서 **"이 디렉터리 이름을 바꿔라"**로
만들었다(허용 집합은 게이트에서 import — 두 번째 의견 없음).
E-23은 결정 대기 중이지만 **예방 지침**을 AGENT.md에 넣었고, E-22의 완화 근거를
추측에서 **측정**으로 바꿔 Agent 경로로는 구조적으로 불가능함을 확정했다.
그리고 **"승인 필요"로 적혀 있던 항목 하나가 실제로는 이미 결정돼 있었다** —
`ops_status.py`가 환경변수를 읽고 미설정 시 어떻게 보고하는지는 같은 파일이 이미
두 번 정해 두었고, 그것을 적용하니 탐지 **두 개**(BUG-46 영구 절반, 해결 불가능한
dirty month)가 함께 닫혔다.
그리고 **E-17의 유실을 처음으로 보이게 했다** — 판정이 실행 사이에서 결정 가능하므로
결정이 필요 없었고, 넣자마자 이 머신의 실 runtime에서 실제 사례 1건이 떴다.
그 과정에서 생긴 성능 회귀는 **cold/warm을 분리해 재측정**해 잡고 즉시 고쳤다
(24.3초 → 6.4초, 기존 스레드 관용구 재사용).
F-7/BUG-41은 **가설이 틀린 채로 재측정**해(원격이 복구되면 덮어쓰기가 옳다) 좁혀진
범위를 정확히 적었다 — 이 검사는 `backup_status`를 읽지 않으므로 상태가 무엇으로
덮어써지든 미백업 History는 계속 보인다.
자기 검토에서 **이번 Sprint가 만든 침묵 하나**(읽을 수 없는 KEEP Candidate를 두 검사가
조용히 버리고 아무도 보고하지 않음)를 찾아 같은 Sprint 안에서 닫았다.
전체 Regression **1881 passed / 0 failed**)

이전 갱신: 2026-08-13 (C27 — **끝나지 않은 쓰기와 이미 끝난 일, 두 계열.**
(1) 원자적 쓰기가 crash로 남긴 `.tmp-*` staging 파일을 소비자 6곳이 전부 산출물로
읽었다 — Event로 승격, **잘린 Company History를 원격에 push**, 그리고 그것을
치우면 **영구 BACKUP_FAILED**. 쓰는 쪽이 15곳에서 이미 선언해 온 접두사를 읽는
쪽에 알리는 것으로 닫았다(`is_incomplete_write()`, 사본 4개를 불변식으로 대조).
(2) 재전송된 중복 1건이 `awaiting_intake`를 영구 점유했다 — outbox가 **설계된
복구 경로**로 삼는 바로 그 상황. `already_collected`로 분리. (3) 그 분리가 가릴
뻔한 **`suppressed`**(이름만 같고 Event가 아닌 쌍둥이)를 `event_id` 비교로
갈라냈고, 그 과정에서 **E-22**(대소문자만 다른 id가 Windows에서 한 파일)를
발견해 탐지만 추가하고 SKIP. (4) **Agent Lock은 아무도 감시하지 않았다** —
Runner Lock만 C23이 닫았고, 정작 stale Agent Lock은 매 실행 exit 0으로 조용히
건너뛴다. 같은 두 검사를 그대로 붙였다. (5) `unparseable` 처리를 `incoming/`에는
적용한 적이 없어, 디코딩 불가 파일 1개가 `awaiting_collection`을 영구 점유했다 —
**Collector 자신의 술어**로 분리(intake의 술어를 쓰면 나가는 중인 파일을 박혀
있다고 보고하게 된다). (6) Run Manifest의 `metrics`(모든 단계가 매 실행 기록)를
production 코드가 **한 번도 읽지 않았다** — Notion 큐에 1건이 밀렸든 400건이
밀렸든 운영자 화면이 글자 하나 다르지 않았다. 실패한 단계에 한해 출력.
(7) **docs/10 §48 정합성 검사를 Monthly에 겨눈 적이 없다** — Monthly 파일이
사라져도 포인터가 "통합 완료"로 남아 어떤 실행도 그 달을 다시 만들지 않는다.
Company History 한 달이 모든 지표가 정상인 채로 사라진다. (4)가 남긴 질문을
탐지기 10종 전체에 돌린 결과이며, 그 전수 대조표를 C27 §9에 남겼다.
(8) **C27 자신의 수정이 blind spot을 만들었다** — 이미 커밋된 `.tmp-` 잔여물이
양쪽 집합에서 빠져 흔적이 사라졌다. git-aware probe로 복원. (9) Agent에는 있고
**Runner에는 없던 staleness 검사** — Runner가 멈춰도 마지막 SUCCESS가 영원히
초록색이었다. (10) 테스트 감사: 실시간 시계에 의존하던 fixture 1건 정정 + 전수
조사, 그리고 `_print_last_run()`이 시계를 두 개 읽던 것을 통일. (11) 성능 감사:
**스레드 풀 측정이 캐시 순서 편향으로 부풀려져 있었다** — 결론(풀은 옳다)은
맞지만 근거가 틀렸고, 그대로 믿었으면 풀을 제거해 실제 운영 경우를 4배 느리게
만들 뻔했다. D절과 코드 주석 3곳 정정(**코드 변경 없음**). (12) 문서 감사:
`AGENT.md`가 `ops_status.py`의 절반만 설명하고 있었다 — 정정하고 **다시 벌어지지
않도록 자기 검증 테스트**를 붙였다. (13) `run_agent.py`의 **Exit Code를 아무것도
고정하지 않고 있었다** — Runner에는 BUG-36 이후 계약 테스트가 있는데 같은 Task
Scheduler에서 도는 Agent에는 없었다(같은 비대칭의 세 번째). (14) **docs/14 §7의
Exit Code 표를 구현과 대조한 적이 없다** — 명세가 인용만 되고 검증된 적이 없어
양방향 드리프트가 무증상이었다. (15) C27의 P0 수정을 **실 bare remote + 실
Backup runner로 E2E 검증**(함정 제거 확인 + 진짜 삭제 게이트는 그대로 발동).
(16) **`last_successful_backup`을 production이 한 번도 읽지 않았다** — 그래서
BUG-55(대소문자 때문에 백업이 조용히 일어나지 않음)가 완전히 무증상이었다.
"마지막 성공 백업보다 새로운 History"라는 임계값 없는 조건으로 처음 가시화.
(17) 시각 비교 **전수 조사**(이 Sprint에서만 세 번 문제가 됐으므로) — 11개 지점
중 1건만 결함이었고 나머지는 방어 확인. 그 1건이 **E-23 신규**: docs/04 §29-30의
`<=`("동시"도 skip)와 docs/06 §12의 자정 기본 timestamp가 만나, **같은 날짜의
두 번째 Signal이 Notion에 조용히 도달하지 않는다**(Company History는 무사).
(18) **docs/11 §101 Release 게이트를 가정하지 않고 실행** — 5/5 PASS, 그리고
항목 4가 실제 Runner를 발화시키지 않는다는 성질을 확인. (19) 이 Sprint가 뷰에
더한 스캔 넷의 비용을 **명령 전체로 실측** — 운영 규모 0.045초, 신규분은 10,000건
기준 0.19%. **성능 회귀 없음.**
전체 Regression **1811 passed / 0 failed**)

이전 갱신: 2026-08-12 (C26 — **이 프로젝트가 스스로 만든 거짓 경보 2건 수정.**
(1) C24 Working Copy 탐지가 `.gitignore`를 무시해, docs/08 §28을 따른 올바른
설정에서도 "원격에 올라간다"고 영구히 경고했다 → git에게 직접 묻도록 수정.
(2) C22 검토 대기 카운터가 리뷰를 마쳐도 지워지지 않았다 → 미검토분에만 경고.
새 기준: **"올바른 조치를 취하면 사라지는가?"** 를 ATTENTION 22개 전체에 적용.
전체 Regression **1696 passed / 0 failed**)

이전 갱신: 2026-08-12 (C25 — E-21 전달 경로 전수(삭제/rename/부분 실패/
junction). **신규 결함 없음**, 대신 E-21 성격 확정: 실패는 유출을 막지 못하고
**미룬다**(commit 후 push 실패 → 다음 성공 실행이 전달), 그러나 그 지연이 곧
C24 탐지가 push보다 먼저 경고하는 **창**이다. junction 경유도 탐지가 덮는다.
전체 Regression **1679 passed / 0 failed**)

이전 갱신: 2026-08-12 (C24 — **신규 보안 결함 E-21**: Secret Scan은 Local
Master를 보는데 `git add -A`는 Working Copy를 커밋한다 → Master를 거치지 않은
`.env`/`id_rsa`가 원격에 push되고 BACKUP_SUCCESS. 게이트는 SKIP(E-15와 하나의
결정), 탐지는 추가. BUG-43 가시성 종결. 문서 규범 조항 282개 대조 — docs/02
열거값 전부 일치. 전체 Regression **1667 passed / 0 failed**)

이전 갱신: 2026-08-12 (C23 — F 절 22건 재평가. **제3의 길** 확립("동작을
바꾸는 것"과 "동작을 보이게 하는 것은 다른 결정"): BUG-42/BUG-30의 가시성만
닫고 동작은 그대로. BUG-41+E-14 복합 실측(흔적 0), BUG-46 범위 축소 확정.
전체 Regression **1646 passed / 0 failed**)

이전 갱신: 2026-08-12 (C22 — **BACKLOG 인벤토리 정정**: 테스트가 고정한
미수정 결함 22건이 이 파일에 없었다(F 절 신설). 그중 BUG-40 계열은 결정이
아니라 미구현이어서 4개 지점 수정. E-20(REVIEW Candidate 도달 불가 + 카운터
부재) 신규 기록·카운터 추가. 날짜 경계/Event 보존 감사는 결함 없음.
전체 Regression **1622 passed / 0 failed**)

이전 갱신: 2026-08-12 (C21 — 직렬화 왕복 전수 감사(결함 없음, 가드 신설),
파이프라인 멱등성 실측(결함 없음), naive/aware datetime 비교 2지점(1건 수정 /
1건은 결정 대기라 되돌림 — E-19), 6.5단계 저장소 반복 읽기 제거(7회→1회).
전체 Regression **1603 passed / 0 failed**)

이전 갱신: 2026-08-12 (C20 — 같은 패턴 전수 조사: mixed-offset 정렬 3곳,
`except OSError`가 `UnicodeDecodeError`를 놓치는 5곳(1건은 Backup을 중단시킴),
docs/08 §29 미구현 2종, run_id 이중 파생 1건 수정 + Agent 크래시 지점 E2E 4건.
전체 Regression **1582 passed / 0 failed**)

이전 갱신: 2026-08-12 (C19 — **C17 기록 정정**(아래 C19 §0: C17이 "구현했다"고
적은 6건이 저장소에 존재하지 않았다. 전부 재구현), E-10 구현 완료, Multi-Desktop
장애 격리 E2E 12건 신설, Daily/Monthly 영구 불일치 1건 발견·수정,
`run_agent.py` 오안내 1건 수정. 전체 Regression 1426 → **1519 passed / 0 failed**)

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

**C31 정정 — "제공한다"는 함수에 대해서는 맞고 시스템에 대해서는 아니다.**
`build_role_summary()`를 **production에서 호출하는 곳이 하나도 없다.**
AST로 확인했다(C29 §3의 선례대로 — grep은 예전에 export를 호출로 오인한 적이 있다):
`src/**` 와 루트 스크립트 전체에서 이 이름에 대한 Call 노드는
`role_summary.py` 자신의 생성자 2개(137·140행)뿐이고, 나머지는
`daily/__init__.py`의 import와 `__all__` re-export다. 즉 **운영자는 역할별 요약을
한 번도 본 적이 없다** — A-16 / E-20과 같은 모양(구현·export까지 됐으나 호출자 없음)이다.

연결하는 것은 승인 없이 못 한다 — 역할별 요약을 *어디에* 렌더링할지가 A-3/A-4
(docs/06 변경)다. 승인 없이 한 것은 **기록이 다시 낡지 않게 고정한 것**이다:
호출자가 생기면 테스트가 깨지고 이 항목을 다시 써야 한다
(`test_daily_role_summary.py::NoProductionCallerTests` 3건 — 호출자 부재, 그럼에도
export돼 있다는 것, 그리고 호출하면 **정상 동작한다**는 것까지. "미연결"이지
"고장"이 아니라는 구분이 다음 사람에게 필요하다).

부수 기록: 그래서 `role_summary._candidate_date()`의 무방비 `fromisoformat`
(A-7과 같은 모양)도 production 영향이 없다 — 도달 경로 자체가 없다.

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
멈춘다(`FAILED`를 정확히 보고하므로 격리는 지켜지지만, 사람이 고칠 때까지
진행되지 않는다). 격리(quarantine) / 건너뛰기 / 정지 중 무엇이 옳은지는
Data Safety 정책 결정이다. **SKIP.**

**C29 재측정 — 범위가 기록보다 훨씬 넓다.** 위 문장은 원래 "**그 날짜에서**
영구히 멈춘다"였다. 실측하면 그 날짜만이 아니다.

    손상 없음        COMPLETED, 9일치 생성, Daily 파일 9개
    손상 후보 1개    FAILED,    **0일치 생성, Daily 파일 0개**

`scheduler.run_once()`는 keep 인덱스를 **배치당 1회, 날짜 루프 이전에** 만든다
(성능 최적화이며 그 자체로 주석이 붙어 있다: "배치당 정확히 1회"). 따라서 실패는
어떤 날짜도 시도되기 전에 일어나고, **2026-08-20의 손상 후보 하나가 2026-08-01도
막는다.** `failed_date`는 손상 후보의 날짜가 아니라 **첫 번째 pending 날짜**로
보고된다. Company History 생성이 통째로 멈춘다.

**추가로 확인된 두 사실:**

- `repository.list()`는 **살아남는다** — 후보를 파싱하지 않은 채 돌려준다.
  raise는 timestamp를 읽는 `build_keep_index()`에서 난다. 즉 이것은 BUG-38의
  `list()` 문제가 아니라 **같은 경로로 보고되는 두 번째, 더 넓은 문제**다.
- JSON 자체가 깨진 후보도 같은 지점에서 같은 결과를 낸다(인덱스가 둘 다 읽는다).

**탐지는 C28에서 이미 닫혔다**(§11). `_read_keep_candidates()`가 timestamp 파싱
실패도 "읽을 수 없는 Candidate"로 잡으므로, A-7의 조건이 ATTENTION에 뜬다.
C29에서 그 문구를 실측에 맞게 고쳤다 — "다음 실행의 Scheduler가 실패한다"는
참이지만 **너무 작았다**. 이제 "**모든 날짜의** Daily History 생성이 멈춘다
(실측: 9일치 → 0일치)"라고 말한다.

**Evidence:** `tests/test_scheduler.py::OneCorruptCandidateStopsEveryDateTests`
6건. 인덱스 생성이 루프 안으로 옮겨지면 이 숫자들이 바뀌고 문구를 다시 봐야
하므로, baseline(9일치)과 손상 시(0일치)를 **둘 다** 고정한다.

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

**C29 정정 — 두 항목 중 하나는 이미 고쳐졌고, 이 기록만 낡아 있었다.**

- ~~README §12의 문서 목록이 `11_DEPLOYMENT_RUNBOOK.md`에서 끝난다~~ →
  **해소됨.** 실측(C29): `docs/`의 **15개** 문서가 전부 README에 있고 누락 0건.
  `test_readme_document_list_names_every_spec_that_exists`가 그 사실을
  **하드코딩된 목록이 아니라 `docs/*.md`에 대해 동적으로** 지킨다 — 새 spec이
  추가되면 그것도 목록에 있어야 통과한다. 그 테스트의 docstring이 왜 이것만
  승인 없이 가능했는지도 적어 두었다: **"디스크에 있는 것의 목록을 완성하는
  것은 새 정책을 만들지 않고 우선순위를 바꾸지도 않는다"**(우선순위는 README
  §13이 정하며 손대지 않았다).
- README 포함 **13개** 문서가 아직 `# D:\DOJOONPASS_COMPANY_OPS\...` 헤더를
  달고 있으나 저장소는 `C:\Users\user\Desktop\...`에 있다 — **여전히 유효**
  (C29 실측 13건). 이쪽은 명세 문서 본문 수정이므로 **SKIP.**

즉 A-10은 이제 **한 항목짜리**다. 현재 상태는
`tests/test_repository_hygiene.py::DocumentationGapCharacterizationTests`가
고정해 두었고, 그 클래스는 "고치면 이 테스트가 깨지고 guard로 다시 쓰여야
한다"는 자기 규칙을 **첫 항목에 대해 실제로 이행했다.**

**이 정정이 보여주는 것:** E-11이 예측한 표류가 **양방향으로** 일어난다. C22는
"고치지 않았다는 기록이 BACKLOG에 도달하지 않는다"를 찾았고, 이것은 그 반대다 —
**고쳤다는 사실이 BACKLOG에 도달하지 않았다.** SKIP 목록을 주기적으로 실측에
대조하지 않으면, 이미 없는 문제가 우선순위를 차지한다.

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

### A-22. README §9가 `Dashboard`를 **V1 제외**로 적고 있다 (C48 신규, **문서 충돌**)

README §13이 문서 우선순위를 `README → 00 → 01 → 개별 Spec`으로 고정한다. 그 README의
§9 "V1 제외" 목록 첫 줄이 `Dashboard`다.

그런데 이 저장소에는 **Operations Dashboard가 구현·배선·문서화돼 있다** —
`notion/dashboard.record_run()`이 `OPS_RUNS` 행을 쓰고, `run_company_ops.py`가
`dashboard_client`를 만들며(GAP-1 수정), `docs/13` §3-⑧이 운영자 절차를 담고, C47이
열 둘을 더 넣었다. 근거로 인용되는 것은 **"CEO Decision ④"** 인데, 그 결정문 자체는
이 저장소 안에 없고 코드 주석과 docs/13에만 등장한다.

두 읽기가 가능하고, **고르는 것이 결정이다.**

- (a) README §9의 `Dashboard`는 **BI/제품으로서의 Dashboard**를 뜻하고(같은 목록에
  `Advanced BI`, `KPI Platform`, `Alert Platform`이 나란히 있다), `OPS_RUNS` 한 행은
  그 범주가 아니다 → README는 그대로 두고 용어를 구분해 적어야 한다.
- (b) 문자 그대로 충돌이다 → 최우선 문서가 V1에서 뺀 것을 만들어 둔 셈이고, README §9를
  고쳐야 한다.

**SKIP.** README는 우선순위 1번 문서이고, 이 항목을 어느 쪽으로 읽을지는 CEO Decision ④의
범위를 아는 사람만 정할 수 있다. 코드나 docs/13을 이 판단 없이 되돌리는 것은 반대 방향의
사고다. 결정에 필요한 것: CEO Decision ④의 원문 범위, 그리고 (a)라면 README §9에 쓸
구분 문구.

### A-23. README §4의 시스템 그림에 Desktop 4의 **보고자 역할**이 없다 (C48 신규, **문서**)

§4의 그림은 `Desktop 1 / 2 / 3 ─ Reporter → Event Transport → Desktop 4`다. Desktop 4는
수집자로만 그려져 있다.

실제로는 넷 다 보고자다. docs/02 §8의 표가 `DESKTOP_4 → COO`를 포함하고,
`reporter/profiles.PROFILES`가 그것을 그대로 담으며(그 파일의 주석이 "DESKTOP_4 was
missing here even though docs/02 §8 lists it"이라고 적어 둔 수정이다), Control Tower의
Desktop 계층·`desktop_activity`·이 저장소의 실 runtime 모두 DESKTOP_4의 Event를 갖고
있다. 이번 Sprint의 요청문도 "Desktop 1 / Desktop 2 / Desktop 4"를 기준으로 삼는다.

**SKIP.** §4는 README이고, 그림을 고치는 것은 최우선 문서의 아키텍처 서술을 바꾸는
일이다. 다만 이것은 A-22와 달리 **해석의 여지가 없다** — 표(docs/02 §8)와 그림이 서로
다른 수의 보고자를 말한다. 결정에 필요한 것: 그림에 Desktop 4의 Reporter 가지를 그릴지,
아니면 "Desktop 4는 자기 Event도 만든다"는 한 줄을 덧붙일지.

### A-21. 네 Category 밖의 항목을 Monthly가 어떻게 다루는가 (C31 신규, **데이터 유실**)

**발견한 사실.** `monthly/markdown.render_monthly_markdown()`은
`item.category in by_category`일 때만 항목을 Section에 넣는다. 네 값
(DECISION/MILESTONE/ISSUE/LEARNING) 밖이면 **어느 Section에도 들어가지 않고**,
그런데 같은 함수가 쓰는 `- Consolidated Items:`는 `len(items)`이므로 버린 것까지
센다.

**재현 결과.** 항목 2개, 하나는 `category="Decision"`:

    - Consolidated Items: 2
    Section : Major Decisions, Source Records, Metadata
    EVT-2   : 파일에 없음 (요약도 id도 없음)
    consolidate_month() -> MONTHLY_GENERATED, item_count=2

**현재 제한.** 부패도 공격도 필요 없다. `## Late Events` 항목은 Daily 파일의
`- Category:` 줄로 자기 Category를 밝히고(docs/06 §37, docs/09 §12-13),
`monthly/parser.py`는 그 텍스트를 그대로 읽으며, docs/06 §57 · docs/11 §71은 COO의
Daily 손편집을 명시적으로 허용한다. 손으로 친 `- Category: Decision` 한 줄이 그
Event를 그 달에서 영구히 지운다 — 다시 만들어도 같은 파일이 나온다.

Daily 쪽 형제(`category=None`인 KEEP Candidate)는 이미 특성화돼 있다
(`tests/test_daily_history.py::test_a_category_less_keep_candidate_silently_
loses_its_detail`). 이쪽이 더 나쁘다 — Daily는 `## Summary`에 요약이,
`## Evidence`에 id가 남지만 Monthly에는 그 둘 다 없다.

**필요한 승인/조건.** docs/09 §14의 렌더링 결정. §14는 열한 Section을 나열할 뿐
"목록에 없는 Category"를 다루지 않는다.

**승인 후 구현 방향.** 세 갈래이고 전부 §14를 바꾼다 — (a) 알 수 없는 Category를
가장 가까운 Section에 넣는다, (b) `## Other` 같은 Section을 하나 만든다,
(c) 버리되 `Consolidated Items`를 렌더링된 수로 줄이고 Daily 쪽과 함께 정한다.
Daily 쪽 형제와 **같은 하나의 결정**이므로 함께 정하는 편이 낫다.

**지금 한 것(승인 불필요).** `ops_status._monthly_counts_more_than_it_shows()` —
한 파일 안의 두 숫자만 비교하므로 창도 오탐 경우도 없다.
**Evidence:** `tests/test_observability.py::MonthlyCountsMoreThanItShowsTests` 11건.

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

6. **보존 정책** — `sent/`, `transport/`, `processed/`, `rejected/`,
   `collector_state.json`이 모두 무한히 커진다. C19가 근거 하나를 더했다:
   `ops_status.py`의 backlog 귀속(E-10)이 이제 `rejected/`의 파일을 하나씩
   읽으므로 그 디렉터리 크기가 상태 조회 시간에 직접 들어간다(실측 10,000건
   8.8초 — 다만 그 규모 자체가 이미 ATTENTION 조건이다). 측정상 현재 규모에서는
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


### E-21. Secret Scan이 git이 커밋하는 디렉터리를 보지 않는다 (C24 신규, **보안**)

**측정(실 git, 로컬 remote):** Working Copy에만 `.env`(토큰 형태 문자열),
`notes/id_rsa`, `scratch.log`를 두고 `backup.run_once()` 실행 —
`BACKUP_SUCCESS` / `push_result="SUCCESS"`, 그리고 **세 파일 모두 원격 커밋에
들어갔다.**

    원격 커밋 내용: .env, .gitkeep, daily/2026-08-05.md, notes/id_rsa, scratch.log

**원인은 세 사실의 조합이고, 각각은 따로 보면 정당하다.**

| | 무엇을 보는가 |
|---|---|
| `scan_for_secrets(master_dir)` | **Local Master** |
| `_relative_files()` (sync가 쓰는 것) | scope 필터됨 — `daily/`·`monthly/` 밖은 아예 안 보임 |
| `git_add_all()` → `git add -A` | **Working Copy 전체** |

즉 sync 이외의 경로로 Working Copy에 들어온 파일은 **게이트에도 안 보이고
(디렉터리가 다름) sync에도 안 보이는데(scope 밖) git은 커밋한다.** Working
Copy는 운영자가 `git init`으로 만들고 들어가 작업할 수 있는 실제 저장소이므로
(docs/08 §30), 편집기 스왑 파일·도구 로그·직접 둔 `.env` 전부 이 경로다.

**docs/08은 이것을 막을 조항을 둘 갖고 있고 둘 다 발효돼 있지 않다.**

- **§28**: Backup Repo가 `.gitignore`(`.env`, `.env.*`, `*.tmp`, `*.log`, …)를
  갖도록 요구한다 — Working Copy에는 **없다**. 만드는 것은 운영자 셋업(§30,
  A-8)이다.
- **§29**: "Backup 전에 최소한 알려진 Secret 파일이 **포함**되지 않았는지
  확인한다" — 무엇이 *포함*되는지는 `git add -A`가 정하는데, 게이트는 다른
  디렉터리를 본다.

**E-15와 같은 뿌리, 반대 증상.** E-15는 게이트가 백업되지 않을 파일까지 봐서
**거짓 양성**으로 Backup을 영구 실패시키는 문제였다. 이쪽은 같은 게이트가
백업될 파일을 **안 봐서 거짓 음성**이 되는 문제다. 두 항목은 "게이트를 어느
디렉터리에 겨눌 것인가" 하나의 결정을 가리킨다.

**왜 SKIP인가:** 후보 수정 셋이 전부 보안 게이트나 승인된 명령 집합을
바꾼다.

| 후보 | 무엇이 바뀌는가 |
|---|---|
| Master 대신 Working Copy를 스캔 | 게이트가 지키는 대상이 양방향으로 바뀐다(E-15의 거짓 양성이 사라지고 새로운 차단이 생긴다) |
| `git add -A` → `git add daily monthly` | 커밋 범위를 좁히고, `test_spec_conformance.py::test_git_ops_runs_only_the_approved_command_set`이 고정한 승인 명령 집합을 바꾼다 |
| Working Copy에 `.gitignore` 생성 | 이 코드가 만들지 않은 저장소에 파일을 만든다(§30/A-8은 운영자 셋업으로 규정) |

**완화되어 있는 것:** 파이프라인 경로로는 새지 않는다 — Master에 같은 파일을
두면 게이트가 정확히 잡고 `BACKUP_FAILED`로 막는다(테스트로 확인). 새는 것은
**Working Copy에 직접 들어온 것**뿐이다. 그리고 원격은 docs/08이 Private
Repository로 규정한다(§71 Token 관리).

**탐지는 추가했다(C24), 그리고 C25가 그 값을 재평가했다.** 원래 판단은
"Backup이 먼저 돌면 이미 push된 뒤라 사후 탐지는 약하다"였다. 절반만 맞다.

**C25 실측 — 실패 경로에서는 탐지가 push보다 앞선다.** `backup.run_once()`는
add → commit → push를 한 `try` 안에서 돌리므로 **push 실패는 commit 이후에**
일어난다:

    run 1 (remote 끊김)  GitOperationError  로컬 커밋에 .env 있음 / 원격에 없음
    그 사이             ops_status.py가 ".env" 를 이름까지 대며 경고
    run 2 (remote 복구)  BACKUP_SUCCESS     원격에 .env

즉 실패가 유출을 막지는 못하고 **미루기만 하지만**, 그 미룸이 곧 창(window)
이다. 무인 배포에서 가장 흔한 실패(원격 도달 불가)가 바로 이 창을 만든다.

**C26 정확도 보정:** 이 탐지는 처음에 `.gitignore`를 고려하지 않아, docs/08
§28을 따라 `.gitignore`를 만든 **올바른 설정에서도** 거짓 경보를 영구히 냈다.
이제 `git ls-files -c -o --exclude-standard`로 git에게 직접 물어 실제로 커밋될
파일만 보고한다(추적 중인 파일은 `.gitignore`와 무관하게 커밋되므로 계속
보고된다). git이 답할 수 없으면 과다 보고로 폴백한다.

**Junction 경유도 덮는다.** Working Copy 안의 directory junction은 `git add -A`가
따라가 반대편 내용을 커밋한다(실측: 원격에 `linked/.env`). `scan_for_secrets()`는
`rglob`으로 걷고 rglob은 junction으로 내려가므로(A-19가 Master 쪽 위험으로
기록한 바로 그 동작) 이 경우에도 잡아낸다 — A-19의 위험이 여기서는 유리하게
작용한다.

**여전히 못 막는 것:** 이름이 `_SECRET_EXACT_NAMES`에 없는 secret
(`secrets.yaml` 등, BUG-7의 알려진 한계). 실측: 게이트도 탐지도 통과해 원격에
나가고, 운영자가 Master에서 지우면 삭제 게이트(§43-47)가 `BACKUP_FAILED`를
내며 Working Copy와 원격의 사본은 그대로 남는다.

**다음에 필요한 조건:** "Secret Scan은 무엇을 지키는가 — Local Master인가,
커밋 대상인가"에 대한 결정. 정해지면 E-15와 이 항목이 **함께** 닫힌다.

**Evidence:** `tests/test_untrusted_event_input.py::WorkingCopyStrayFileTests`
7건. 마지막 둘이 후보 수정 두 개의 현재 상태(`.gitignore` 부재, `add -A`)를
고정하므로, 어느 쪽이 바뀌어도 이 항목이 함께 갱신된다.

### E-25. `BACKUP_FAILED` 하나가 서로 다른 두 사건을 가리킨다 (C31 신규, **Run Contract**)

**SKIP 사유: docs/14 §5의 Failure Classification 어휘 변경 = Spec 변경(§6).**

docs/08은 Backup이 FAILED로 끝나는 경로를 둘 적어 두었고 둘은 성격이 다르다:

    §21       인증/권한 실패 — 자격증명을 고쳐야 한다
    §31/§44-47 Local Master 파일 삭제 감지 — Company History가 사라졌다

둘 다 `BACKUP_FAILED` / `PERMANENT` / `CRITICAL`로 보고된다. docs/14 §5의 예시는
`BACKUP_FAILED`를 **인증 실패**에 명시적으로 묶고 있으므로(*"인증 실패는
`BACKUP_FAILED`/`PERMANENT`/`CRITICAL`이다(§21…)"*), 삭제 차단을 같은 값으로
보고하는 것은 이미 예시와 어긋난다. 운영자 관점에서도 조치가 완전히 다르다 —
전자는 토큰, 후자는 **사라진 파일을 찾는 일**이다.

**필요한 결정.** `BACKUP_DELETION_BLOCKED`(가칭) 같은 값을 §5 어휘에 추가할지.
추가하면 `ops_status.py`의 PERMANENT 경보 문장과 Run Manifest를 읽는 모든 것이
새 값을 알아야 하고, 그것은 계약 변경이다.

**C31이 승인 없이 한 것:** 값은 그대로 두고 **`reason`에 사실을 적었다**(§32).
`Failure.reason`은 자기 docstring이 자유 텍스트라고 명시한 필드이고,
`deleted_files` metric도 삭제 분기에 실었다. 이제 Manifest만 읽어도 두 경우가
구별되지만, **분류 자체는 여전히 하나**다.

### E-24. Secret 게이트가 자기 목록의 이름을 대소문자 때문에 못 알아본다 (C31 신규, **보안**)

**측정(실 git, 로컬 bare remote, 실제 `backup.run_once()`).** 같은 내용, 같은
in-scope 디렉터리, 파일 이름의 대소문자만 다르다:

    daily/ID_RSA   BACKUP_SUCCESS   push_result="SUCCESS"
                   원격 tree : daily/2026-08-05.md, daily/ID_RSA
                   git show main:daily/ID_RSA -> 키 본문이 그대로 읽힌다
    daily/id_rsa   BACKUP_FAILED    "secret files detected: daily\id_rsa"
                   원격 tree : (비어 있음)

접미사도 같다 — `server.PEM` · `client.Key` · `bundle.P12` 전부 미탐지.
`.env`/`.ENV`/`.Env`는 파일시스템이 애초에 한 파일로 합쳐 버리는데, 그것이 바로
요점이다: **파일시스템은 이름을 대소문자 구분 없이 다루고 게이트는 그러지 않는다.**
어느 철자로 만들었는지라는 우연이 보호 여부를 정한다.

**원인.** `backup/working_copy._looks_like_secret()`:

    return name in _SECRET_EXACT_NAMES or name.endswith(_SECRET_SUFFIXES)

목록 자체는 옳다(docs/08 §29가 적은 이름들, C20이 두 개를 마저 채웠다). 틀린
것은 비교가 Windows 파일시스템의 이름 동치와 다르다는 것이다.

**BUG-55와 같은 뿌리, 다른 위치.** BUG-55는 `daily/` vs `Daily/` — 무엇이
*백업되는가*. 이쪽은 무엇이 *차단되는가*. 두 곳 다 "대소문자 구분 비교 ×
구분하지 않는 파일시스템"이다.

**왜 SKIP.** 비교를 case-fold하면 게이트에 **새로운 BACKUP_FAILED 조건**이 생긴다.
그것이 정확히 E-15가 기록한 피해다 — 거짓 양성 하나가 Company History를 원격에
못 가게 만들고, Backup은 사람이 개입할 때까지 실패한다. E-15/E-21의 후보 수정이
전부 "게이트를 어디에 무엇으로 겨눌 것인가"라는 하나의 결정을 기다리고 있고,
이 항목은 그 결정의 세 번째 면이다.

**탐지는 추가했다.** `ops_status._secret_names_the_gate_will_not_recognise()`,
Local Master와 Backup Working Copy 양쪽. 이름 목록은 게이트에서 import한다(C28의
규칙: 두 번째 의견을 만들지 않는다). 게이트가 이미 보는 파일은 보고하지 않아
E-21 줄과 겹치지 않는다. 보고는 구조상 늦다 — 예약된 Backup이 이미 push했을 수
있다 — 그러나 늦은 것과 영영 모르는 것의 차이는 자격증명 교체 여부다.

**history 쪽 보고도 함께 열었다.** 이미 push된 경우 파일 이름을 고쳐도 원격
history에는 남으므로 `_secrets_ever_committed()`가 그쪽을 보고하는데, 그 검사도
같은 대소문자 비교를 써서 `ID_RSA`는 **유출이 실제로 일어나는 바로 그 경로에서**
보이지 않았다. 그쪽은 **게이트가 아니라 보고**이므로 — 아무리 넓혀도 Backup을
실패시킬 수 없다 — case-fold를 추가했다. 이 저장소가 secret 신호에 대해 이미
택한 방향이다(`_would_reach_the_commit()`은 git이 답 못 하면 과다 보고로
폴백하고, `oplog.SECRET_PATTERNS`는 의도적으로 과다 매칭한다): 불필요한 교체는
반나절이고 놓친 교체는 살아 있는 자격증명이다.

**여전히 못 막는 것.** BUG-7의 한계는 그대로다 — 목록에 없는 이름
(`secrets.yaml` 등)과 파일 **내용**. 게이트 자체도 그대로다.

**다음에 필요한 조건.** E-15 / E-21과 **같은 하나의 결정**. 정해지면 셋이 함께
닫힌다.

**Evidence:** `tests/test_backup_git_ops.py::StagingResidueThroughTheRealBackupTests::
test_only_the_case_of_a_secret_filename_decides_whether_it_leaks` (+ 대조 1건,
실제 remote), `tests/test_untrusted_event_input.py::SecretNameCaseTests` 7건,
`tests/test_observability.py::…::test_a_case_variant_already_in_history_is_reported`
(+ 과다 매칭 가드 1건). 게이트가 case-insensitive해지면 앞의 둘이 실패하므로
이 항목이 함께 갱신된다.
---

## F. 테스트가 고정했지만 BACKLOG에 없던 미수정 항목 (C22 전수 조사)

이 파일의 목적은 첫 줄에 적혀 있다 — "승인 없이 진행할 수 없어 **SKIP한
항목**"의 기록. 그런데 그 기록이 절반이었다.

C22에서 전수 대조했다: 테스트 docstring이 참조하는 결함 식별자 **60개** 중
**43개가 BACKLOG.md에 한 번도 등장하지 않는다.** 그중 클래스 docstring에
`NOT FIXED`로 명시된 것만 추리면 **22건**이고, 아래가 그 목록이다.

전부 현재 동작이 맞다 — 특성화 테스트는 "오늘 이렇게 동작한다"를 단언하므로
전체 suite가 green이라는 사실 자체가 22건 모두 여전히 미수정임을 증명한다.
(C17이 남긴 교훈의 반대 방향: "고쳤다"는 기록만 검증이 필요한 것이 아니라
"기록되지 않았다"도 검증이 필요하다.)

**이 절은 새로운 결함을 주장하지 않는다.** 이미 측정·고정된 것을 이 파일이
읽을 수 있는 곳으로 옮긴다. 승인이 필요한 판단은 하나도 하지 않았다.

### F-1. 데이터 유실 / 영구 정지

| ID | 무엇인가 | 왜 미수정인가 |
|---|---|---|
| **BUG-25** (P0) | Collector가 Event를 소비한 뒤 Candidate 저장 전에 죽으면 그 Event는 Company History에서 영구 소실 | 결정 필요. **A-20과 같은 항목** — A-20이 이 결함의 BACKLOG 표현이다 |
| **BUG-42** | 읽기 전용 속성이 붙은 stale lock은 `os.unlink()`가 `PermissionError`로 실패 → `try_acquire_lock()`이 영원히 False → **모든 실행이 조용히 건너뛰어진다** | 동작은 여전히 결정(속성을 강제로 벗길 것인가 / 반환 계약을 나눌 것인가). **C23에서 "조용히"만 제거** — `stale_lock_cannot_be_cleared()` 신설, `ops_status.py` ATTENTION. C19의 `is_locked`/`lock_held_since`는 **둘 다 이 경우를 못 봤다**(살아 있는 프로세스를 전제하므로) |
| ~~**BUG-40**~~ | 깊게 중첩된 JSON 하나가 `RecursionError`를 던지고 `_is_parseable_json()`이 `(OSError, ValueError)`만 잡아 **Runner 전체가 영구 정지** | **C22에서 수정** — 결정이 아니었다. `agent/signals.py`가 같은 `json.loads`에 대해 이미 답해 두었고(주석까지), 이 predicate의 목적 자체가 "파싱 못 하는 파일은 건너뛴다"이다. 아래 참조 |
| **BUG-38** | `keep/`의 손상된 파일 하나가 `FileHistoryRepository.list()`에서 `JSONDecodeError`(또는 `RecursionError`)를 던져 그 날짜 전체가 막힘 | 격리/건너뛰기/정지 중 무엇이 옳은지 = Data Safety 정책. **A-7의 자매 항목**(A-7은 timestamp 파싱, 이쪽은 JSON 파싱). C22가 인접 4곳을 고치면서 **이곳만 의도적으로 남겼다** — `list()`는 파일 단위 관용을 문서화한 적이 없는 유일한 지점이다 |
| **BUG-43** | `processed/`에 이미 있는 이름이 `incoming/`에 다시 나타나면 영구히 실패 반복 | 이름 기반 중복 판정을 바꿀지의 결정. BUG-47/BUG-53과 같은 뿌리. **C24에서 가시성만 닫음**(`name_collision` 카운트) — F-10 참조 |
| **BUG-46** | Scheduler 창(window) 밖 날짜의 KEEP Candidate는 저장되지만 **영원히 렌더링되지 않는다** | 창 밖 Candidate를 어떻게 처리할지 결정. **E-20과 같은 부류**(저장되지만 도달 불가) |

### F-2. 조용한 실패 / 성공 오보고

| ID | 무엇인가 | 왜 미수정인가 |
|---|---|---|
| **BUG-41** | `BACKUP_FAILED`가 다음 실행에서 **조용히 덮어써진다** — `BACKUP_PENDING`에 대해서는 CEO 승인 A안으로 이미 고친 바로 그 위험 | 같은 수정을 FAILED에도 적용할지가 결정(FAILED는 사람이 개입해야 지워지는 값일 수 있다). **C23 재측정: 원 기술보다 넓다** — no-change 경로뿐 아니라 변경이 있는 **성공 경로도** FAILED를 덮어쓴다. F-7 참조 |
| **BUG-55** | Backup scope 검사가 대소문자 구분인데 Windows 파일시스템은 구분하지 않음 → `Daily/`로 만들어지면 History가 정상 기록되고 **조용히 백업되지 않는다** | 경로 비교 정규화는 Backup 계약 변경 |
| **BUG-52** | 실제 자격증명 실패 3종이 transient로 분류되어 **영원히 재시도** — docs/08 §62가 금지하는 루프 | 문자열 매칭 목록을 넓히는 것은 오분류 방향을 바꾸는 결정 |
| **BUG-44** | `run_id`가 1초 해상도인데 한 실행은 1초보다 짧다 → 같은 초의 두 실행이 **run_id를 공유** | 해상도를 바꾸면 Manifest/Dashboard/Backup Log의 키가 바뀐다. C19가 Dashboard 중복을 find-before-create로 막았으므로 **피해는 완화**돼 있다 |
| **BUG-45** | `health_check()`는 토큰·DB 도달만 확인하는데 이름은 sync 가능 여부를 예측하는 것처럼 읽힌다 | 무엇을 health로 정의할지의 결정 |

### F-3. 진단 정보 손실

| ID | 무엇인가 | 왜 미수정인가 |
|---|---|---|
| ~~**BUG-58**~~ | Notion이 준 실패 설명을 버리고 HTTP status text만 남김 | **C31에서 재측정 — sync 경로에서는 남은 범위가 없다.** 실 `_request()`에 진짜 400 본문을 물려 끝까지 추적: `NotionAPIError` → `SyncResult.error` → `notion_sync.log`의 `REASON`까지 전부 도달한다. `dashboard`·`bootstrap`·`health_check`도 `str(exc)`를 쓴다. Manifest의 `Failure.reason`이 `queued[0]` 하나인 것은 유실이 아니라 설계다(detail은 `artifact_refs`가 가리키는 로그에 있다). **그 추적 중에 신규 보안 결함 발견** — 같은 문자열이 stdout에는 redact 없이 나갔다(C31 §11, 수정함) |
| **BUG-56** (P3) | Collector 로그가 성공 줄은 `event_id`로, 나머지는 파일명으로 키를 잡아 sanitize된 Event를 로그에서 추적할 수 없음 | 로그 형식 변경 |
| **BUG-51** | `.env` 값을 trim하지 않아 `"  "` 같은 오타가 통과하고 나중에 401로 실패 | trim이 설정 계약 변경 |
| **BUG-31** | `diff_properties()`가 Property **타입**을 보지 않아 이름만 같으면 EXISTS로 판정 | 불일치를 감지한 뒤 무엇을 할지가 결정 |

### F-4. 외부 신뢰 경계

| ID | 무엇인가 | 왜 미수정인가 |
|---|---|---|
| **BUG-30** | Desktop 간 시계 오차로 mtime이 미래면 `_is_stable()`이 그 파일을 **시계가 따라잡을 때까지** 무기한 보류(1년이면 1년) | mtime을 얼마나 신뢰할지의 결정 |
| **BUG-53** | intake의 중복 검사가 존재 기반이라 **이름만 맞으면** 무엇이든 배달을 억제(디렉터리, 0바이트 포함) | BUG-47과 같은 race 정책 결정(E-9) |
| **BUG-32** | Notion 초당 ~3요청 제한에 대해 pacing이 전혀 없음 | pacing 정책은 결정 |
| **BUG-11 / BUG-27** | `summary`와 `evidence`가 Daily Markdown에 escape 없이 렌더링 → Markdown 구조 위조 가능 | 무엇을 escape할지는 docs/06 렌더링 계약 변경. **로그 쪽은 C10이 이미 닫았다**(`oplog`) — 남은 것은 Markdown |
| **BUG-26** | 서로 다른 UTC offset의 Event가 offset-local 날짜로 그룹핑되어 "잘못된 날"에 들어갈 수 있음 | 정규화는 docs/06 §12 변경. **C20이 고친 것은 하루 *안의* 순서**이고 이쪽은 *어느 하루인가* — 다른 결정 |
| **BUG-28** | docs/02 §26이 "명백히 모순되는 조합은 Validation에서 거부한다"고 규정하는데 스키마가 거부하지 않음 | 스키마가 받아들이는 것을 바꾸는 변경(A-15와 같은 벽) |

### F-5. 이미 다른 이름으로 기록돼 있던 것

| ID | 기존 항목 |
|---|---|
| **BUG-37** | **E-14와 동일** (Backup Log 미구현, docs/08 §68-69). E-14를 C20에서 독립적으로 재발견한 것이었다 — 두 항목은 하나다 |
| **BUG-25** | **A-20과 동일** |
| **BUG-29** | **E-19** (C21에서 기록) |

### 이 조사가 드러낸 구조적 문제

E-11이 예측한 것의 반대 방향 사례다. E-11은 "고쳤다는 기록이 저장소보다 오래
산다"였고(C17), 이번 것은 **"고치지 않았다는 기록이 BACKLOG에 도달하지
않는다"**이다. 두 방향 모두 원인이 같다 — 테스트 docstring과 `BACKLOG.md`가
서로를 참조하지 않는다.

값싼 대조 수단이 있다는 것도 확인했다: 테스트에서 `BUG-\d+`를 뽑아 BACKLOG와
차집합을 내면 끝난다(이번에 43개를 그렇게 찾았다). 자동화하려면 형식 결정이
필요하므로 **E-11은 여전히 SKIP**이지만, 그 대조를 Sprint 시작 절차에 넣는
것은 승인이 필요 없다.


---

## C49. 하나의 Model, 두 소비자 — 그리고 "무엇에 대해"

C48은 화면을 Dashboard Model 위로 옮겼다. 이번 질문은 그 다음이었다: **Notion으로 나가는
것도 같은 모델에서 나오는가.** 답은 아니오였고, 그것을 고치는 과정에서 모델이 답할 수 없던
질문이 하나 더 드러났다.

---

### 1. Notion 행이 아직 갈라져 있었다 (신규, 구조)

C48은 Runner의 손수 계산(`events_by_source` 루프 + docs/02 §8 짝 검사 사본)을 없애고 하나의
`build_company_rollup()` fold로 바꿨다. **count는 하나가 됐지만 표현은 아니었다:**

    rollup ─┬─> Dashboard Model ──> 화면
            └─> (Runner가 직접 조립) ──> OPS_RUNS 행

`" ".join(f"{d.source}:{d.event_count}" ...)`가 파이프라인 숫자 여덟 개 사이에 끼어 있었고,
정렬 규칙·조용한 Desktop을 빼는 이유·그 이유가 패널과 반대인 까닭이 전부 그 자리의 주석에만
있었다. 둘을 맞춰 두는 것은 **사후 비교 테스트 하나**였다 — C48이 직접 쓴 것.

**`src/controltower/projection.py` 신설.** `ops_runs_fields(model)`가 두 열을 만들고 Runner는
부른다. 이제:

    rollup ──> Dashboard Model ─┬─> 화면
                                └─> ops_runs_fields() ──> OPS_RUNS 행

"행이 모델과 같다"가 **검사로 얻는 성질이 아니라 구성으로 얻는 성질**이 됐다.

`OPS_RUNS_CONTROL_TOWER_COLUMNS`가 keyword→열 이름 표를 들고, `ContractedColumnsExistTests`가
그것을 `DASHBOARD_DATABASES[OPS_RUNS]`·`record_run()`의 시그니처 양쪽과 대조한다. 열을 하나
더 파생하면서 스키마에 넣지 않으면 **첫 실 운영 실행의 HTTP 400이 아니라 테스트가** 실패한다.

`role_mismatches`를 DESKTOPS 패널에서 세는 것도 명시적 선택이다(RISKS도 같은 Event를 들고
있다). DESKTOPS는 **보낸 기계**로 세고 그것이 이 열이 말하는 분할이다.
`TheTwoPanelsAgreeAboutMismatchesTests`가 두 패널을 같은 수에 묶어, 선택이 조용한 차이가 되지
않게 한다.

---

### 2. rich_text 상한이 없었다 (신규, **Notion 계약**)

`Desktops Reporting`이 Desktop당 열이 아니라 rich_text **하나**인 이유는 C47이 적어 뒀다 —
`events.SOURCES`는 자랄 수 있는 스키마 값이고, 열을 늘리면 매번 Database 마이그레이션이다.
그런데 자라는 쪽에 한도가 없었다: Notion의 rich_text 한 항목은 2,000자이고, 넘으면
**행 전체가 400으로 거절된다**(그 실행의 기록은 `dashboard_pending.json`에 쌓이고 이유가 남는다
— 안전하지만 빠져나올 길이 없다, ⑧-4가 적은 바로 그 모양).

`RICH_TEXT_LIMIT = 2000`, 그리고 절단은 **보인다** — `…`로 끝난다. 조용한 절단은 "네 Desktop이
보고했다"를 짧은 문장이 아니라 **거짓 문장**으로 만든다. 잃는 것도 없다: 같은 수가 DESKTOPS
패널과 Run Manifest에 있다.

---

### 3. Coverage — 모델이 "무엇에 대해"를 답할 수 없었다 (신규, **복구**)

패널은 "무슨 일이 있었나"를 답한다. 답하지 않던 것은 **"무엇에 대해"**다.

`runtime/events/processed/`는 Execution Evidence(docs/14 §2)이고 Backup 범위는
`daily/`·`monthly/`뿐이다(docs/08 §26). 그래서 원격에서 복원한 머신은 Company History를 전부
되찾고 **Event는 하나도** 되찾지 못한다. 그 상태에서 일곱 패널은 전부 0을 보고하며 —
**그것은 사실이다.** 조용한 한 주와 구분되지 않을 뿐이다.

지금까지 그 구분은 `ops_status.py`가 계산해 **화면에만** 찍는 한 줄이었다. Notion projection은
같은 것을 다시 파생해야 했고, 그것이 C48·C49가 계속 닫아 온 갈래다.

`Coverage`를 모델에 넣었다:

    evidence_from / evidence_to    이 숫자들이 덮는 Event 날짜 범위
    unreadable                     디렉터리에 있으나 쓸 수 없었던 파일 수
    history_uncovered_from         일은 기록됐는데 증거가 없는 가장 이른 날
    complete                       위 둘이 모두 비어 있는가

`history_uncovered_from`만 **바깥에서 온다** — Company History는 `local_master/daily/`에 있고
이 모듈은 그 디렉터리를 읽지 않으며 읽기 시작해서도 안 된다(rollup을 받아 재배열할 뿐이다).
`with_history_coverage()`가 그 값을 받아 **새 모델**을 돌려준다(frozen 유지).
`ops_status.py`는 이제 자기가 구한 답을 모델로 되돌려주고 **모델에서 찍는다** — 하나의 파생.

E2E로 실제 파이프라인에서 확인했다: 정상 실행 뒤 `complete=True`, `processed/`를 비운 뒤
(=복원 모양) `history_uncovered_from`이 그 날짜를 말하고 `complete=False`, 화면도 같은 문장,
그리고 **ATTENTION은 비어 있다**(단서지 경보가 아니다).

---

### 4. 새 게이트가 같은 Sprint 안에서 두 번 발화했다 (기록)

C48 마지막에 넣은 `TestClassCitationsResolveTests`가 `projection.py`의 주석이 인용한 테스트
클래스 둘이 아직 없다고 즉시 실패했다(쓸 예정이었고, 이름을 먼저 적었다).

그리고 **fresh 전체 실행**이 하나 더 잡았다 — C48이 BACKLOG 산문에 예시로 적은 가짜
클래스 이름 하나를 기존 `BacklogEvidenceLinksResolveTests`가 인용으로 읽었다. 게이트가
옳았다: `…Tests`로 끝나는 이름은 이 저장소에서 **인용**이며 예시로 쓸 수 있는 단어가
아니다. 산문 쪽을 고쳤다(이 문단도 그래서 이름을 대지 않는다).

두 게이트가 트리를 분할한다 — BACKLOG는 기존 것, `src/`·루트 entrypoint·AGENT.md·README는
새 것.

---

### 5. 만들었다가 지운 것 (기록)

`contracted_columns()` — 스키마에서 두 항목을 읽어 돌려주는 접근자. 호출자가 **테스트뿐**이었고,
그것이 `DeadCapabilityInventoryTests`가 잡는 모양이다. 이 모듈이 다른 곳에 지는 빚은
`OPS_RUNS_CONTROL_TOWER_COLUMNS` 하나이고, 스키마는 `notion/dashboard.py`의 것이며, 둘 다
필요한 독자는 각각에게 한 번씩 물으면 된다.

---

### 6. ATTENTION 불변식을 property로 고정했다 (기록)

이 블록이 찍는 ATTENTION 줄은 **RISKS 패널의 행 개수와 정확히 같다** — 무작위 Event 열
40 seed로 고정했다. 임계값이 생기거나, 조용한 팀·읽을 수 없는 파일이 슬그머니 경보가 되거나,
반대로 열린 Blocker가 조용해지면 이 테스트가 실패한다. 그런 드리프트는 한 줄씩 도착한다.

Coverage 쪽도 property로 덮었다: 증거 범위가 실제 Event 날짜의 최소·최대와 같은가,
`complete`가 정확히 "unreadable 0 + 미덮인 History 없음"인가.

---

### 7. Dashboard 전용 계산이 CRITICAL 단계 **앞**에 서 있었다 (신규, 배치)

§1을 붙이고 나서 그 코드가 **어디에** 있는지를 봤다. C48이 넣은
`build_company_rollup(events=run_events, ...)`은 5단계(History Filter) 직후에 있었다 — 즉
Daily / Monthly / Backup **세 CRITICAL 단계보다 앞**이고, 그 자리를 감싸는 `except`는 없다.

두 호출 모두 이미 파싱된 Event에 대한 순수 변환이라 실제로 던질 일은 없다. 하지만 그것은
**논증으로 얻은 안전**이고, CEO Decision ④이 요구하는 것은 그것이 아니다 — "Dashboard 기록
실패는 Runtime을 절대 중단시키면 안 된다."

Dashboard 단계의 `try` 안으로 옮겼다. 두 가지가 따라온다:

- 던져도 그 실행의 Company History와 Backup은 무사하고, 실패는 `notion_sync.log`의
  `DASHBOARD FAILED (unexpected)`로 남는다 — 주입해서 확인했다.
- **Dashboard 미설정 배포(docs/04가 지원하는 형태)는 아예 계산하지 않는다.** 읽지 않을 값을
  만들지 않고, 그것 때문에 깨질 수도 없다. 이것도 테스트가 고정한다.

실측(한 실행에 Event 5,000건): rollup 24 ms, 모델 0.3 ms, projection 0.009 ms — 옮긴 이유는
비용이 아니라 배치다.

---

### 8. Secret 보고가 **세 번째 목적지**를 말하지 않았다 (신규, **Security**)

C47 §11-12가 만든 ATTENTION 줄은 Event 내용의 secret이 어디로 가는지를 이렇게 적는다 —
"Daily History에 그대로 쓰이고 backup 원격까지 push된다. 해당 자격증명을 **교체**해야 한다 —
파일을 고쳐도 원격 history에는 남는다."

**목적지가 하나 더 있다.** 실 `ExecutionPlanSync`로 측정:

    event_id     -> `Last Event ID`       샌다
    project_id   -> `Project ID` + Title  샌다
    milestone    -> `Current Milestone`   샌다
    blocker      -> `Blocker`             샌다
    summary      -> (Property 없음)       가지 않는다

같은 줄이 훑는 다섯 필드 중 **넷**이 Notion PROJECTS 행에 그대로 들어간다. Notion은 자체 보존
정책을 가진 제3자이고, **파일이 아니어서** 운영자의 교체 체크리스트에서 가장 빠지기 쉬운 사본이다.
"파일을 고쳐도 원격 history에는 남는다"는 두 원격 중 **하나**에 대해서만 참이었다.

**보고를 고쳤지, 동작을 고치지 않았다.** 나가는 길에 가리는 것은 파이프라인이 사람이 쓴 문장을
고쳐 쓰는 것이고 docs/06 §57이 그 얘기다 — C47 §15가 남긴 결정 그대로다. 줄은 이제 조건과 함께
Notion을 이름 댄다("Notion Sync가 설정된 배포에서는"): 이 도구는 `processed/`를 읽을 뿐이고
그 Event를 받은 실행에 Notion이 설정돼 있었는지 알 수 없으며, 토큰 없는 배포는 지원되는
형태다(docs/04).

**결정에 필요한 것** — C47 §15의 (a)/(b)/(c)에 하나 더 붙는다: 거부도 통과도 아닌 네 번째 안,
**Notion 쪽으로 나갈 때만 가리는 것**. Company History의 문장은 손대지 않으므로 §57과 충돌하지
않지만, 그때 Notion 행과 Company History가 서로 다른 문장을 갖게 되고 docs/14 §1이 Notion을
"View"로 고정한 의미를 바꾼다. **SKIP.**

---

### 9. 실행된 적 없는 분기를 전수로 훑었다 — Notion 쪽에서만 여섯 (신규, **테스트**)

`src/notion/`에 분기 커버리지를 걸고 "한 번도 실행되지 않은 방어"를 전부 열었다. 여섯 곳이
나왔고, **전부 도달 가능**했다 — 추상 메서드 본문이나 플랫폼 분기가 아니라 실제 조건이다.

| 어디 | 조건 | 왜 중요한가 |
|---|---|---|
| `properties._extract_rich_text()` ×2 | `Last Event ID`가 없거나 비어 있는 행 | docs/04 §43은 사람이 이 DB에 쓰는 것을 허용한다. 이 함수가 `""`를 답하면 **event_id가 빈 Event**(`EmptyEventIdIsStillAnEventIdTests`가 유효하다고 고정한 것)가 §62 중복 가드에 걸려 적용되지 않은 채 skip된다. §29-30 시각 가드는 이걸 못 잡는다 |
| `sync._update()`의 `except NotionAPIError` | update 실패 | create 실패는 덮여 있었고 **update는 아니었다.** 대부분의 sync가 update다. docs/04 §38이 요구하는 `NOTION_RETRY_REQUIRED`가 실제로 나오는지 아무도 확인하지 않았다 |
| `bootstrap._has_title_property()` + `_bootstrap_title_property()` | Title이 없는 schema | "이론상 발생하지 않음"이라고 적힌 방어. 방어는 **동작할 때만** 방어다 |
| `bootstrap.format_report()` | 빈 결과 | `max()`가 빈 시퀀스에서 `ValueError`를 낸다 — 그 early return이 유일한 방벽이다 |
| `dashboard._page_title()` | 제목 없는 Page | Notion에서 흔하고, 호스트 후보로 **유효하다.** 빈 문자열로 나열되면 렌더링 오류처럼 읽힌다 |
| `transport._error_detail()`의 `except` | 본문을 읽다 끊긴 응답 | docstring의 약속이 "하나의 실패를 둘로 만들지 않는다"인데 아무도 확인하지 않았다 |

전부 테스트로 덮었다. `src/notion/` 97% → **99%**(남은 것은 추상 메서드 본문 6줄뿐).

---

### 10. Notion으로 **실제로 나가는 요청의 모양**을 자격증명 없이 고정했다 (신규, **Release**)

`RealNotionTransport`의 여섯 메서드는 한 줄짜리이고 스위트가 **하나도 실행한 적이 없었다.**
verb 하나, 경로 하나, body 모양 하나 — live Workspace가 404/405로 답하기 전까지 보이지 않는
종류의 코드다. `DashboardSchemaMappingTests`는 *property*를 고정하지만, 그것을 나르는 *요청*은
아무도 고정하지 않았다.

`urllib.request.urlopen`을 가로채 `Request`를 잡아 검사한다 — 네트워크도 자격증명도 필요 없다:
GET `/databases/{id}`, POST `/databases/{id}/query`, POST `/pages`(+`parent.database_id`),
PATCH `/pages/{id}`(스키마가 아니라 값), PATCH `/databases/{id}`(값이 아니라 스키마),
POST `/databases`(+`parent.type=page_id` — Notion은 workspace 루트에 DB를 못 만든다), 그리고
모든 요청의 `Notion-Version`/`Authorization` 헤더와 설정된 timeout.

같은 방식으로 `_request()`의 두 변환도 덮었다 — read timeout(urllib이 `URLError`로 **감싸지
않는다**)과 그 밖의 `OSError`가 `NotionAPIError`가 되는지. 이 변환이 없으면
`ExecutionPlanSync`/`bootstrap`/`dashboard`가 잡지 않는 타입이 새어 나가고, Runner에서 그것은
"재시도하는 단계"가 아니라 "죽는 단계"다.

**그리고 double의 두 번째 거짓말을 고쳤다.** `_SchemaEnforcingTransport`는 없는 열을 거부하지만
**타입은 보지 않았다** — `number` 열에 `rich_text`를 써도 통과한다. `TestDoubleFidelityTests`가
"wrong property type accepted"로 이미 기록해 둔 발산이고, C36이 그 때문에 존재한다.
`_TypeEnforcingTransport`로 실제 `record_run()` 한 번을 **전체 OPS_RUNS 스키마**에 대해 흘려
본다. 이것이 중요한 이유: `record_run()`은 절대 raise하지 않으므로, 값 모양이 틀리면 행은
`dashboard_pending.json`에 쌓여 **매 실행 똑같이 실패하며** 재시도된다.

`TestDoubleFidelityTests`의 특성화는 그대로 둔다(평범한 double은 여전히 관대하다) — 두 완화가
**국소 subclass**라는 것과, 그것이 실제 연결을 대체하지 않는다는 문장을 함께 적었다.

---

### 11. 원자적 쓰기 게이트의 명단이 **트리보다 뒤처져 있었다** (신규, **테스트/내구성**)

`test_every_state_writer_uses_the_same_atomic_idiom`은 이 저장소의 구조 게이트 중 하나다.
자기 docstring이 목적을 이렇게 적는다 — "a future writer that skips tempfile+os.replace would
silently lose the no-torn-file property."

**명단이 손으로 유지되고 있었고, 일곱 개였다.** `tempfile.mkstemp`로 훑으면 **열세 개**다.

그리고 `agent/state.py`와 `monthly/state.py`는 **어느 명단에도 없었다** —
구조 게이트에도, `AtomicWriteFailureCleanupTests`에도,
`CompanyHistoryWritersCleanUpTooTests`에도. 관용구는 갖고 있는데 그것을 아는 게이트가 없었다.
이 테스트가 막으려던 실패가 반대편에서 도착한 것이다: 관용구를 **빠뜨린** 새 writer가 조용히
통과하는 것을 막으려 썼는데, 관용구를 **가진** 새 writer도 똑같이 조용했다.

**명단을 없앴다.** 이제 `src/`를 훑고, 찾은 것마다 `os.replace`와
`except BaseException: os.remove(tmp_path)` 둘 다를 요구한다(후자는 새 검사다 — staging은 하면서
cleanup을 빠뜨린 writer는 이전 게이트가 볼 수 없었다). 훑기가 아무것도 못 잡는 경우를 막는
guard-the-guard도 붙였다.

### 11b. 그리고 관용구의 **안쪽 절반**은 한 번도 실행된 적이 없었다

    except BaseException:
        try:
            os.remove(tmp_path)
        except OSError:      <- 여기
            pass
        raise

바깥은 덮여 있었다(`os.replace`를 깨서 주입). **안쪽은 어디에서도 실행된 적이 없다** — cleanup
자신이 실패한 적이 한 번도 없었다.

이것도 이 프로젝트의 대상 OS에서는 별개 시나리오가 아니다. 목적지를 붙잡고 있는 무언가가
`os.replace`에 WinError 5를 내고, **같은 핸들이** staging 파일에도 WinError 32를 낸다. 둘은
독립적으로 오는 것보다 **함께** 오는 쪽이 흔하다.

걸린 성질은 "호출자가 어느 예외를 보는가"다. `pass`가 사라지면 writer는 "state를 못 썼다"가
아니라 **"임시 파일을 못 지웠다"**를 보고한다 — 문제 대신 문제의 뒷정리에 대한 메시지다. 실패를
분류하는 모든 호출자(`app/runner.py`의 recorder, `SyncResult`, `TransportError`)가 엉뚱한 쪽을
분류하게 된다.

Company History writer 셋에서는 더 나쁘다. 그중 둘은 **던지지 않고 보고한다** —
`update_daily_history()`는 `LateUpdateOutcome.FAILED`의 `error` 문자열을,
`consolidate_month()`는 실패한 `MonthlyResult`를 돌려주고, 그 문자열이 Run Manifest와
`daily_late_update.log`와 `ops_status.py`에 도달한다. Daily Close를 잃은 원인을 찾는 운영자가
"임시 파일을 못 지웠다"를 이유로 받게 된다.

`os.remove`까지 깨는 클래스 둘을 붙여 열세 writer 전부에 대해 고정했다 — 원래 오류가
전파/보고되고, cleanup의 오류가 그것을 가리지 않고, 이름이 붙은 산출물은 여전히 생기지 않는다.

부수 관찰: staging 이름이 `.tmp-f70hq44g.md`처럼 **확장자를 유지**하므로 `glob("*.md")`에 잡힌다.
이 프로젝트의 모든 reader는 `.tmp-` 접두사를 배제하지만(`controltower.read_events()`, Monthly
parser, Collector), 테스트를 쓰면서 그것을 잊으면 잔여물이 "유령 날짜"로 보인다 — 실제로 이
테스트의 첫 판이 그렇게 실패했다.

커버리지: `agent/state.py`·`monthly/state.py`·`daily/generator.py` 100%,
`monthly/generator.py` 99%, `src/` 전체 98%.

---

### 11c. 남은 미실행 분기를 **분류해 두었다** (기록)

`src/` 전체 문·분기 커버리지 **99%**. 남은 55줄/27분기는 다음 넷 중 하나이며, 다음 Sprint가
같은 조사를 다시 하지 않도록 여기에 적는다.

| 종류 | 어디 | 왜 남는가 |
|---|---|---|
| 추상 메서드 본문 | `history/repository.py`, `history/review.py`, `collector/seen_store.py`, `transport/interface.py`, `notion/transport.py`의 6줄 | `raise NotImplementedError` — 구현이 아니라 계약이다 |
| 플랫폼 분기 | `scheduler/lock.py` 67·100-108 | POSIX `os.kill` 경로. 이 머신은 Windows이고 `tasklist` 쪽을 쓴다 |
| 권한 필요 | `backup/working_copy.py` 13줄 | symlink/junction 거부 분기. `SeCreateSymbolicLinkPrivilege`가 없다(B-2와 같은 조건) |
| 도달 불가(문서화됨) | `app/runner.py` 1390·1627 등 | `BACKUP_PENDING`처럼 "도달 불가"가 전제와 함께 테스트로 고정돼 있다 |

이번 Sprint에 **닫은 것**: `agent/agent.py`·`agent/state.py`·`backup/state.py`·
`daily/generator.py`·`monthly/generator.py`·`monthly/state.py`·`monthly/coverage.py`·
`notion/properties.py`·`notion/sync.py`·`notion/dashboard.py`·`notion/bootstrap.py`·
`notion/retry_queue.py`·`notion/dashboard_pending.py`·`reporter/local_output.py`·
`collector/state.py`·`scheduler/state.py`·`history/file_repository.py`·`transport/intake.py`
— 전부 100%.

**남은 셋은 여전히 실제 조건이다**(추상/플랫폼/권한이 아니다):
`agent/delivery.py` 213(빈 record 목록), `agent/outbox.py` 122(이름 경합 흡수),
`agent/status.py` 78-79(naive/aware 정규화), `app/desktop_activity.py` 133-134·414·519,
`backup/git_ops.py` 226(porcelain 빈 줄), `daily/role_summary.py` 98(호출자 없는 모듈, A-3),
`review_cli.py` 178-180·184. 전부 값싸게 덮을 수 있고 이번에 시간이 아니라 우선순위로 남겼다.

---

### 12. 남는 결정 (SKIP, 조건 명시)

C48 §9 그대로이며 바뀐 것은 없다. 덧붙는 것 하나:

**Coverage를 Notion 어디에 둘 것인가.** 값은 이제 payload에 있고 `docs/13` §3-⑨-4가 "패널 옆
텍스트 블록"을 권한다. 그것을 `OPS_RUNS`의 열로 만들지(= per-run 행에 standing fact를 하나 더
얹는 결정, `Notion Queued`의 선례가 있다), 아니면 Workspace에서 사람이 배치할지는 View 구성
결정이고 자격증명이 필요하다(A-8).

---

## C48. Dashboard Model — 화면과 payload가 같은 것임을 증명할 수 있게

C47은 Desktop의 실제 데이터가 Notion 행까지 닿는 것을 흘려서 확인했다. 남은 질문은
다른 것이었다: **그 데이터를 "전사 Control Tower"로 보여줄 때, 화면에 있는 것과
밖으로 나가는 것이 같은 것이라고 무엇이 보장하는가.** 이번 Sprint의 기준은 그
보장을 코드 구조로 만드는 것이었고, 그 과정에서 rollup 자체의 결함 셋이 나왔다.

---

### 1. Blocker가 엉뚱한 팀에 붙어 있었다 (신규, **오귀속**)

`_roll_risks()`가 `Risk.team`을 `project.teams[-1]`로 정했다. `ProjectRollup.teams`는
그 Project를 **건드린 모든 role**을 first-seen 순으로 담는 목록이므로, `[-1]`은
"가장 최근에 처음 등장한 팀"이다. 두 팀이 함께 쓰는 Project에서 다른 팀의 Event
하나가 Blocker의 주인을 옮긴다. 실측:

    E1  PAY  CTO_BACKEND  BLOCKED            blocker="vendor key missing"
    E2  PAY  CMO          DECISION_APPROVED
    -> Risk.team == "CMO"

그리고 `ops_status.py`는 그 이름을 ATTENTION 문장 안에 찍는다 — "…**[CMO]** — vendor
key missing — Blocker는 파이프라인이 스스로 지우지 않는다. **그 팀이** RESUMED /
ISSUE_RESOLVED / COMPLETED를 보고할 때까지 열려 있다". 막고 있지 않은 팀에게 풀라고
말하고 있었다.

**고침:** Blocker를 연 Event의 `role`을 fold가 함께 들고 다닌다
(`ProjectRollup.open_blocker_team`). `role`이지 `source`가 아닌 이유는 이 값이
답하는 질문이 "어느 팀이 RESUMED를 보고해야 하는가"이고 Team 계층의 키가 `role`이기
때문이다. 둘이 어긋난 Event는 이미 `PairMismatch`이고 Desktop 계층이 그것을 따로
말한다.

`TeamRollup.blocked_projects`는 **바꾸지 않았다** — 막힌 Project를 함께 쓰는 팀은
막힌 Project를 갖고 있는 것이 맞다. 두 사실은 다르고 둘 다 참이다.

---

### 2. 완료 지표가 완료가 아닌 파일을 증거로 대고 있었다 (신규, **추적성**)

`projects_completed` metric의 evidence가 이랬다:

    evidence=tuple(p.open_blocker_evidence or p.evidence[-1] for p in completed ...)

**완료 count가 blocker 파일을 우선한다.** 그리고 blocker가 없으면 그 Project가
마지막으로 한 일 아무거나를 댄다. 실측:

    C1  SEARCH  COMPLETED           5일
    C2  SEARCH  DECISION_APPROVED   9일
    -> evidence = C2

    D1  OPSX  COMPLETED  5일
    D2  OPSX  BLOCKED    9일  blocker="reopened for audit"
    -> evidence = D2  (blocker 파일이 완료의 증거로)

이 모듈의 docstring이 스스로 적어 둔 문장이 "왜 열린 Blocker가 3인가는 이름이 적힌
파일 셋을 여는 것으로 끝난다"이다. 이름이 적혔는데 그 파일이 이유가 아니면 없느니만
못하다 — 한 번 열어 보고 상관없다는 것을 알면 그 다음부터 열지 않는다.

**고침:** §25의 Completed Date를 쓴 Event를 fold가 기록한다
(`ProjectRollup.completed_evidence`).

---

### 3. `src/controltower/dashboard.py` — 요청된 패널의 데이터 모델 (신규)

rollup과 `ops_status.py` 사이에 아무 층도 없었다. 렌더러가 `CompanyRollup`을 필드
단위로 헤집어 문장을 조립했으므로 **"Control Tower가 무엇을 보여주는가"는 터미널
출력으로만 존재**했고, 같은 것을 원하는 두 번째 소비자(Notion projection)는 rollup에서
다시 파생할 수밖에 없었다. 하나의 view를 두 번 파생하는 것이 화면과 projection이 같은
날에 대해 다른 말을 하기 시작하는 방식이다.

패널은 요청의 다섯 개 그대로다.

| 요청 | 패널 | 상태 |
|---|---|---|
| ① COMPANY GOALS | `COMPANY_GOALS` | **UNSOURCED** |
| ① KPI | `METRICS` | SOURCED (target 없음 — target은 Goal이다) |
| ② TEAM DASHBOARD | `TEAMS` | SOURCED (`current_sprint`는 항상 null) |
| ③ PROJECT | `PROJECTS` | SOURCED (`sprint`는 항상 null) |
| ③ SPRINT / Backlog | `SPRINTS` | **UNSOURCED** |
| ④ DESKTOP | `DESKTOPS` | SOURCED |
| ⑤ RISK / BLOCKER | `RISKS` | SOURCED (열린 Blocker + role 어긋남) |

**`PanelStatus.UNSOURCED`가 1급 값인 이유.** 빈 패널과 원천 없는 패널은 모델이 둘을
구분하지 못하면 똑같이 그려지고, 뜻은 정반대다 — "아무 일도 없었다" 대 "물어볼 곳이
없다". `UNSOURCED_LAYERS` 4개가 정확히 한 패널씩에 귀속되며, 그 성질을 테스트가
모델에게 물어서 검사한다(어느 패널이 어느 계층을 갖는지 테스트가 알지 않는다).
계층 하나가 원천을 얻으면 그 테스트가 **실패해서** 옮기라고 알린다.

**`ops_status.py`의 CONTROL TOWER 블록이 이 모델로 렌더링한다.** 실 runtime에서
변경 전/후 전체 출력 byte 단위 동일. KPI 숫자만 `rollup.metric()`에서 읽고, 그 둘이
같은 수임을 metric key 전부에 대해 테스트가 대조한다 — "같은 접근자를 부른다"보다
강한 문장이다.

④/⑤의 **운영 쪽 절반은 일부러 여기 없다.** Agent 상태 / Runner 상태 / Last Run /
Backup / Delivery / Recovery / Notion Sync는 전부 이미 `OPS_RUNS` 행에 있고
(`Generated Days`, `Backup Status`, `Notion Queued`, `Failed Steps`,
`Desktops Reporting`, …), 여기에 다시 적는 것은 한 실행에 대한 두 번째 의견이다.
`dashboard.py`의 docstring이 그 대응표를 들고 있다.

---

### 4. payload가 자기 row key를 안 가렸다 (신규, **Security**, 내 코드)

처음 설계는 `AUTHORED_KEYS` — "가릴 필드의 목록"이었다. 테스트가 즉시 깼다:

    project_id = "PROJ-ntn_AAAA…"
    payload  values.project_id = "PROJ-[REDACTED]"      가려짐
             key               = "PROJ-ntn_AAAA…"      **안 가려짐**

`DashboardRow.key`는 PROJECTS 패널에서 `project_id` 그 자체다. 허용목록은 **완전해야만**
동작하고, 나중에 붙는 열마다 잊을 기회가 생긴다.

**뒤집었다.** `_UNAUTHORED_KEYS` — 가리지 **않아도** 되는 것의 짧은 목록이고, 거기
들어간 이름은 전부 `validate_event()`가 고정 집합(`SOURCES`/`ROLES`/`STATUSES`)으로
묶거나 이 모듈이 스스로 고른 단어다. 테스트가 두 방향을 다 잡는다: 목록의 이름이 실제
열인지, 그리고 그 열의 값이 정말 고정 집합 안에 있는지.

같은 검사가 열 이름 충돌 하나도 잡았다 — `source`가 DESKTOPS에서는
`events.SOURCES` 값이고 METRICS에서는 자유 텍스트 출처 문장이었다. `_out()`이 열
**이름만**으로 판단하므로 한 이름이 두 뜻을 가지면 예외목록이 곧 구멍이다. METRICS 쪽을
`derived_from`으로 바꿨다.

`EvidenceRef.path`도 가린다: 수집된 파일 이름이 Event 이름에서 오므로 secret 모양
`event_id`는 secret 모양 파일명이다. 모델 자신은 **verbatim으로 둔다** — 파일을 찾는
데 쓰는 값을 파이프라인이 고쳐 쓰면 증거가 증거가 아니게 된다. 가리는 곳은 기계를
떠나는 지점 하나뿐이다.

---

### 5. CANCELLED가 ACTIVE로 나갔다 (신규, 내 코드)

`_project_state()`가 접힌 사실(blocker / Completed Date)만 봤다. docs/04 §26은
CANCELLED에 property를 **주지 않으므로** 접을 사실이 없고, 취소는 `status`에만 남는다.
화면은 `status`를 직접 찍기 때문에 보이지 않았지만, `state`를 읽는 projection에는
취소된 Project가 진행 중으로 보인다. `PROJECT_STATES`를
`BLOCKED / COMPLETE / CANCELLED / ACTIVE`로 고정하고 테스트를 붙였다.

---

### 4b. validator가 **거절한 값**을 payload로 되돌려주고 있었다 (신규, **Security**, 내 코드)

§4를 고친 뒤에도 `to_payload()`에 한 곳이 남아 있었고, 남긴 이유가 §4와 **똑같은 모양의
추론**이었다 — 주석에 이렇게 적어 두었다: "파일 이름은 디스크에서, 이유는 예외에서 오며,
**둘 다 authored Event 텍스트가 아니다**." 둘 다 틀렸다.

    file    Event 파일은 Event 이름을 따서 지어진다(`safe_event_filename()`).
            secret 모양 `event_id`는 곧 secret 모양 파일명이다.
    reason  `validate_event()`는 **거절한 값을 메시지에 인용한다** —
            `invalid source: '…'`, `timestamp is not valid ISO-8601: '…'`.

실측. `processed/`에 JSON이지만 Event가 아닌 파일 하나(`source`가 credential 모양):

    unreadable: [{'file': 'ntn_AAAA….json',
                  'reason': "invalid source: 'ntn_AAAA…'"}]

같은 credential이 payload에 **두 번** 들어갔다. `ops_status.py`는 개수만 찍으므로 화면에는
없었고, 나가는 바이트에만 있었다.

도달 경로는 손상이 아니다: docs/11이 `incoming/`에 손으로 쓰는 것을 허용하고, 부분 복원은
남은 것을 그대로 남기며, `read_events()`의 docstring 자체가 Collector가 받아들인 staging
residue를 적어 둔다. 그리고 이 필드가 채워지는 상황은 **운영자가 상태를 들여다보는 바로 그
상황**이다.

둘 다 `redact(one_line(...))`, 이유에는 `bounded()`도 걸었다 — `read_events()`가
`except Exception`이라 텍스트가 무엇이든 될 수 있고, 무한히 커질 수 있는 보고서는 디스크를
채운다(`oplog.bounded()`의 논거 그대로).

**교훈이 §4와 같다.** "이 필드는 사용자 입력이 아니다"라는 추론은 이 저장소에서 이미 두 번
틀렸다(C47의 "id는 내용이 아니다", 그리고 이번의 두 번). 그래서 남긴 규칙은 목록이 아니라
방향이다 — payload에서 가리지 **않는** 것만 열거하고, 그 목록에 넣으려면 `validate_event()`가
고정 집합으로 묶는다는 근거가 있어야 한다.

---

### 5b. payload가 작업량에 비례해 자랐다 (신규, **성능**, 내 코드)

docs/14 §3이 Run Manifest에 대해 이미 정리해 둔 규칙이다 — "Manifest는 Event 1건당 줄을
쓰지 않는다 … 작업량에 비례해 커지는 것은 로그이며, 그러면 Manifest일 수 없다." 새
payload가 정확히 그것을 깼다. Event 하나는 **네 행**에 증거로 실린다(자기 metric, 자기
Project, 자기 Team, 자기 Desktop). 실측 6,000건:

    to_payload()   382.4 ms   2.0 MB
    build_dashboard  0.2 ms            (모델 자체는 재배열일 뿐이다)

둘 다 상한 없이 자란다. 이것이 Notion으로 나갈 바로 그 바이트다.

**고침:** `EVIDENCE_IN_PAYLOAD = 5`. 행마다 앞의 다섯 개만 싣고, `evidence_count`는
**진짜 총계**를, `evidence_truncated`가 잘렸는지를 말한다 — 조용한 절단은 "전부 덮었다"로
읽히므로 하지 않는다. 모델 자신은 전부 들고 있다(이 기계 위의 독자에게는 다 있다).

    to_payload()     6.6 ms   55 KB

Blocker나 role 어긋남처럼 **증거가 곧 이유인** 행은 원래 ref 하나짜리라 상한에 닿지 않는다.

---

### 6. Runner가 `_roll_desktops()`를 손으로 한 번 더 구현하고 있었다 (정리)

C47이 `Desktops Reporting` / `Role Mismatches`를 넣으면서 step 5 루프 안에서 직접
셌다 — docs/02 §8 짝 검사의 **두 번째 사본**을 포함해서. 값은 맞았지만 "Dashboard 행이
rollup과 일치한다"가 검사로 얻는 성질이지 구조로 얻는 성질이 아니었다.

`build_company_rollup(events=...)` seam으로 바꿨다(이미 있던 seam이고, 파일을 다시 열지
않는다). 이제 하나의 fold가 화면과 OPS_RUNS 행 둘 다를 만든다. per-run 행이므로 조용한
Desktop은 `DESKTOP_3:0`이 아니라 빠진다 — 상태 뷰는 "활동 없음"과 "세지 않음"을 구분해야
하지만, 이 실행에 아무것도 보내지 않은 Desktop은 이 실행에 보고하지 않은 것이다.

---

### 7. PROJECTS 행의 `Owner`는 **만든 팀**이지 막은 팀이 아니다 (신규, 특성화 — SKIP은 §9)

§1을 고치고 나서 같은 질문을 Notion 쪽에 했다. `build_update_properties()`는
`Owner`/`Source`를 **일부러 빼고**(docstring: docs/04 §9-12는 생성 시점 정보로만 설명하며
매 Update마다 덮어쓸 근거가 spec에 없다), `Blocker`는 보고한 팀이 매번 덮어쓴다. 실측,
실 `ExecutionPlanSync`:

    E1  PAY  CMO / DESKTOP_2          STARTED
    E2  PAY  CTO_BACKEND / DESKTOP_1  BLOCKED "vendor key missing"

    PROJECTS 행   Owner=CMO  Source=DESKTOP_2  Blocker="vendor key missing"
    Control Tower risk.team=CTO_BACKEND

"Blocked를 Owner로 group by"하는 Notion View는 이 Blocker를 CMO에게 보낸다. 둘 중
하나만 맞고, 맞는 쪽은 행이 아니다. 단일 Desktop Project에는 문제가 없다 — 그래서 이제껏
모든 fixture가 일치했다.

고치는 두 길이 **둘 다 명세 결정**이라(§9) 특성화 테스트로 고정하고 `docs/13` §3-⑨에
경고를 적었다.

---

### 8. 문서 — `docs/13` §3-⑨ Control Tower View 구성 (신규)

Database를 만드는 절이 아니다. **이미 있는 PROJECTS / OPS_RUNS 위에 View를 얹는 절**이며
새 Database도 새 Property도 만들지 않는다. 담은 것:

- PROJECTS 위의 View 5종(Board by Status / Timeline by Last Updated / Board by Owner /
  Table by Source / Filter Blocker is not empty)과 각 View가 어느 패널에 대응하는지
- OPS_RUNS 열이 요청의 어느 항목을 답하는지 대응표, 그리고 **행의 연속성을 완전성으로
  읽지 말 것**(Dashboard 단계 앞에서 멈춘 실행은 행을 남기지 않는다)
- 만들 수 **없는** View와 그 이유 — Goal / Sprint / Backlog 칸반은 원천이 없다
- ⑨-4 경고 — Blocked를 `Owner`로 묶지 않는다(§7)

패널 목록 자체는 문서에 옮겨 적지 않고 코드에서 뽑는 명령을 실었다(⑧이 열 목록에 대해
하는 것과 같은 이유: 정본이 둘이면 어긋난다). 그 명령은 실행해서 확인했다.

---

### 10. 네 entrypoint가 **존재하지 않는 환경변수**를 설정하라고 안내하고 있었다 (신규, C47 회귀)

C47 §13이 `--dry-run`이 운영 실행이던 것을 고치면서 `cli.unexpected_arguments()`를 넣었다.
그 메시지의 존재 이유는 자기 docstring에 적혀 있다 — "'이 도구는 인자를 받지 않는다'는
운영자를 갈 곳 없이 만든다. 다음으로 필요한 것은 **실제로 있는 knob의 이름**이다."

실측(네 도구를 실제로 실행):

    init_notion.py       COMPANY_OPS_NOTION_API_TOKEN, COMPANY_OPS_NOTION_PROJECTS_DB
    run_company_ops.py   COMPANY_OPS_HISTORY_START_DATE, COMPANY_OPS_NOTION_API_TOKEN,
                         COMPANY_OPS_NOTION_PROJECTS_DB, COMPANY_OPS_RUNTIME_DIR
    ops_status.py        COMPANY_OPS_RUNTIME_DIR, COMPANY_OPS_HISTORY_START_DATE, …
    run_agent.py         (셋 다 실재)

일곱 이름 중 **셋이 존재하지 않는다.**

- `COMPANY_OPS_NOTION_API_TOKEN` / `COMPANY_OPS_NOTION_PROJECTS_DB` — 진짜 이름은
  `NOTION_API_TOKEN` / `NOTION_PROJECTS_DATABASE_ID`이고, **같은 파일의 module docstring이
  그 둘을 올바로 적고 있다.** 메시지가 자기가 들어 있는 파일과 모순이었다.
- `COMPANY_OPS_RUNTIME_DIR` — knob이 아니다. 두 파일 모두 `RUNTIME_DIR`이 상수이고,
  `run_company_ops.py`에는 그것을 rebind하는 것이 위험해서 넣어 둔 가드가 바로 아래 있다.

`init_notion.py`는 **Notion 설정이 전부인 도구**인데 틀린 둘만 댔다. 메시지를 그대로 따른
운영자는 고치려던 `NotionConfigError`를 다시 만난다. 그리고 메시지는 "AGENT.md를 보세요"로
끝나는데, AGENT.md에는 그 이름이 하나도 없었다(§11).

**C47이 같이 넣은 테스트는 이것을 잡을 수 없었다.** 두 가지가 겹쳤다:

1. `COMPANY_OPS_*`만 훑었다 — Notion 도구가 말하는 이름 대부분이 스캔 밖이었다.
2. `known`이 "소스 어디든 나타나는 `COMPANY_OPS_*` 문자열 전부"였는데, **`configured_by`
   목록 자체가 소스다.** 지어낸 이름이 *검사 대상 목록에 적혀 있다는 이유로* 검사를 통과했다.

게이트를 두 겹으로 다시 세웠다. 정적(`EnvironmentContractTests`) — `configured_by`의 모든
이름이 실제 **읽는 자리**(`os.environ.get` / `source.get` / `*_ENV_VAR`)를 갖고 `.env.example`에
있는가. 프로세스(`AnEntrypointRefusesArgumentsItCannotHonourTests`) — 네 도구를 정말 실행해
찍힌 이름을 같은 집합과 대조한다. 되돌려 보고 발화도 확인했다.

---

### 11. AGENT.md가 **틀렸다고 이미 기록된 측정**을 아직 싣고 있었다 (신규, 문서)

§2.2: "관리자 권한이 필요할 수 있다 … 개발 머신에서 실측: **빈 Task조차 거부됐다**."
§2b(36줄 아래): "**이전 안내는 필요하다고 했는데 틀렸다** — 실제 원인은 `-User` 누락이었고
(C13에서 수정) 비관리자 세션에서 전 과정을 확인했다."

두 문장이 같은 문서에 있고, **틀린 쪽이 먼저다.** §2.2에서 읽기를 멈춘 운영자는 관리자
권한을 찾으러 간다. BACKLOG B-1이 이 사건에서 뽑은 교훈이 그대로 재현된 셈이다 —
"'불가능함이 확인됐다'로 기록된 측정은 근거보다 오래 살아남는다."

§2.2를 정정으로 바꾸고 §2b를 가리키게 했다.

같은 절에서 두 번째 구멍: **AGENT.md에 Desktop 4의 환경변수를 다루는 절이 없었다.**
§2.1은 Agent의 셋만 적고 "`NOTION_API_TOKEN` 등은 Desktop 4의 Runner만 사용한다"로
끝난다. 그런데 `run_company_ops.py` / `init_notion.py` / `ops_status.py`의 refusal 메시지는
전부 "사용법은 AGENT.md를 보세요"로 끝난다 — 이름을 받아 든 운영자가 안내받은 문서에서
그 이름을 찾을 수 없었다. §2.3을 신설해 넷을 적고 정본(`.env.example`)과 Workspace 구축
절차(docs/13)를 가리킨다.

---

### 9. 남는 결정 (SKIP, 조건 명시)

**PROJECTS 행이 Blocker의 주인을 말할 것인가** (§7). 두 길이 있고 둘 다 명세 결정이다.
- (a) `Owner`를 매 Update마다 덮어쓴다 — `build_update_properties()`의 docstring이
  "spec에 근거가 없다"고 적어 둔 바로 그 일이다. 게다가 `Owner`의 뜻이 "만든 팀"에서
  "마지막으로 손댄 팀"으로 바뀌는 것이라, 이미 그 열을 보고 만든 View가 있다면 조용히
  뜻이 달라진다.
- (b) `Blocker Owner` Property를 새로 만든다 — PROJECTS 스키마 변경이고 docs/04가
  고정한다. 기존 행에는 값이 없으므로 backfill 여부도 결정해야 한다.
- 결정에 필요한 것: `Owner`의 정의(생성자인가 최근 담당인가), 그리고 (b)라면 backfill
  정책. 그때까지 팀별 Blocker는 `ops_status.py`의 CONTROL TOWER 블록이 유일하게 옳다.

**`DashboardModel.to_payload()`의 sink** (A-8에 붙는다). payload는 완성돼 있고 화면이
쓰는 바로 그 모델의 직렬화이지만, 그것을 받을 곳은 자격증명이 필요한 Workspace다.
**결정이 남은 것이 아니라 자격증명이 남은 것**이며, 그 구분을 `DeadCapabilityInventoryTests`
항목에 그대로 적었다. 연결할 때 필요한 것은 전송뿐이다 — 모델·payload·계약·View 설계·
테스트·문서는 이번에 끝냈다.

**Notion에 Control Tower 전용 표면을 만들 것인가.** docs/14 §1이 Operational Projection을
`PROJECTS / OPS_RUNS` 둘로 고정한다. 패널 7개를 그 둘 위의 View로 얹는 것은 계약 안이고
(§8), 세 번째 Database나 블록 Page를 만드는 것은 계약을 넓히는 일이라 명세 결정이다.
이번 Sprint는 계약 안에 머물렀다.

**Goal / Team Goal / Sprint / Task의 원천** — C46 §15, C47 §15 그대로. 바뀐 것은 하나:
이제 모델이 그 부재를 데이터로 들고 있고(`UNSOURCED_LAYERS` → 패널), 원천이 생기면
테스트가 실패해서 알린다. 결정 자체는 그대로 남는다.

---

## C47. Desktop → Control Tower → Notion — 끊기지 않는지 실제로 흘려 봤다

C46은 파생 계층과 로컬 뷰를 만들었다. 이번 기준은 "코드가 있다"가 아니라
**"Desktop 1/2/4의 실제 데이터가 Notion까지 도달하고 올바르게 표현된다"**였다.

---

### 1. Desktop 계층이 없었다 — 그리고 그 부재가 결함을 가리고 있었다 (신규, **데이터 혼입**)

C46의 rollup은 `role`(Team)과 `project_id`만 잡고 **`source`를 버렸다.** Team과
Desktop은 `source`→`role`이 1:1인 동안 같은 분할이므로 하나면 충분해 보였다.
아니었다.

**`validate_event()`는 `source`와 `role`을 각각의 허용 집합에 대해서만 검사하고
짝은 검사하지 않는다.** docs/02 §8이 표를 고정하고 `reporter/profiles.PROFILES`가
**생성 시점에만** 그것을 적용한다. 읽는 쪽에는 그 검사가 **어디에도 없다.**

**실측**(`source=DESKTOP_1`, `role=CMO` Event 하나, 실제 reader들):

    validate_event()      오류 없음
    Control Tower         그 작업은 CMO 팀의 것
    desktop_activity      그 작업은 DESKTOP_1의 것
    Notion PROJECTS 행    Owner=CMO / Source=DESKTOP_1

행이 모순을 **자기 안에 담고 있는데** 아무도 표시하지 않는다. 손으로 쓴 Event
(docs/11이 Desktop 4에 허용한다), 복원된 파일, 편집된 파일이 이 모양이 된다.

**고친 것:** rollup에 `DesktopRollup` 계층과 `PairMismatch`를 넣었다. Desktop은
**`source`로 센다** — `role` 필드를 믿는 것이 정확히 한 Desktop의 작업이 조용히
다른 팀 것이 되는 경로다. 표는 `PROFILES`에서 **import**한다(그 모듈의 주석 자신이
"only pairs them the way the spec already does"라고 적는다 — 복사하면 맞춰 둘 규칙이
둘이 된다, C28).

**거부하지 않는다.** 검증 오류로 만들면 그 Event가 `rejected/`로 가서 Company
History에서 아예 사라진다. "기록을 잃는 것"과 "잘못된 주인으로 기록하는 것" 중
무엇이 나쁜지는 **결정**이고(§15), 그때까지 이름을 대는 것이 이 저장소의 일관된
처리다(E-24와 같은 형태).

---

### 2. 그 검사가 **저장소 자신의 "건강한 런타임" fixture**를 처음으로 잡았다 (신규)

`AHealthyRuntimeCanActuallyBeQuietTests`는 "사람이 할 일이 없는 런타임은 ATTENTION이
비어 있고 exit 0"을 지키는 가드다. 그 fixture가 이렇게 되어 있었다:

    ("DESKTOP_2", "CTO_FRONTEND")     docs/02 §8: DESKTOP_2 -> CMO
    ("DESKTOP_3", "CMO")              docs/02 §8: DESKTOP_3 -> CTO_FRONTEND

즉 **"건강하다"고 주장하는 fixture가 §8을 어기고 있었다.** 여러 Sprint 동안 통과했고,
COMPANY 블록은 그동안 `DESKTOP_2 role=CTO_FRONTEND`를 화면에 **그대로 찍고 있었다** —
아무도 그것이 표와 다르다는 것을 볼 근거가 없었기 때문이다.

fixture를 §8의 짝으로 고쳤다. 예외 처리하지 않은 이유: "건강한 모습"을 뜻하는
fixture가 Team 집계와 Desktop 집계를 어긋나게 만드는 바로 그 모양을 품고 있으면 안
된다.

---

### 3. Notion — 기존 계약 안에서 Desktop 차원을 실었다

새 DB를 만들지 않았다(docs/14 §1). **이미 계약된 `OPS_RUNS`에 열 둘**을 더했다 —
C32/C33이 13→17열로 넓힌 것과 같은, 문서화된 연산이다(`bootstrap_dashboard_properties()`,
docs/13 §3-⑧-4).

| 열 | 무엇 | 왜 |
|---|---|---|
| `Desktops Reporting` (rich_text) | `DESKTOP_1:2 DESKTOP_2:1 DESKTOP_4:1` | 나머지 열은 전부 파이프라인 단계 숫자다. **작업이 어디서 왔는지**를 말하는 열은 하나도 없어서, Event 50건을 모은 실행이 Desktop 넷에서 왔는지 하나에서 왔는지 구별되지 않았다 |
| `Role Mismatches` (number) | §1의 개수 | 그 행 자신이 `Owner`/`Source` 모순을 담을 수 있는데 그것을 세는 것이 없었다 |

**Desktop당 열이 아니라 rich_text 하나**: `events.SOURCES`는 자랄 수 있는 스키마
값이고(docs/02 §8), Desktop당 열이면 그때마다 Database 마이그레이션이 된다.

`Overall` 판정에는 넣지 않았다 — 데이터 정합성 사실이지 실패한 단계가 아니고,
"새 입력은 판정에 들어가기 전에 열부터 얻는다"는 그 함수의 규칙 그대로다.

숫자는 **5단계 루프에서 센다** — 이번 실행의 Event를 이미 전부 읽는 유일한 자리다.
`processed/`를 다시 읽으면 이 프로젝트가 지금까지 모은 **전체** Event를 세게 되고,
실행 단위 행을 전체 기간 디렉터리로 만드는 셈이다.

---

### 4. 끊기지 않는지 실제로 흘렸다 (E2E, 신규 10건)

Agent와 Notion 행 사이에 **stub이 없다**:

    Signal 파일 (Desktop 1 / 2 / 4)
      -> agent.run_once()        실 Agent, 실 outbox
      -> OneDriveTransport       공유 폴더에 실제 원자적 쓰기
      -> run_intake()            실제 안정화 창과 dedup
      -> collector.run_once()    실제 중복 검사
      -> app.runner.run_once()   실 파이프라인, 실 git backup
      -> controltower rollup     디스크에서 다시 읽음
      -> record_run() / ExecutionPlanSync

Notion **transport만** in-memory double이다(실 Workspace는 자격증명 필요, A-8).

**실측 결과 — 전 구간 일치:**

    Desktops Reporting  'DESKTOP_1:2 DESKTOP_2:1 DESKTOP_4:1'  == rollup
    Role Mismatches     0                                      == rollup
    Accepted            4                                      == rollup.events_read
    PROJECTS[SEARCH_BACKEND].Blocker '벤더 API 키 발급 대기'    == rollup
    PROJECTS[*].Source  각 Project를 가진 Desktop과 일치

고정한 것: Desktop별 귀속, **다른 Desktop으로 새지 않음**, 재실행 시 행/Event 중복
0, Agent 재실행 시 신규 0(결정론적 `event_id`), Manifest와 Dashboard 일치, 그리고
§1의 혼입 케이스가 **체인 끝까지** 보고되는 것.

**하네스를 만들다 하나 배웠다**(제품 결함 아님): intake의 안정화 창 때문에 방금 쓴
파일은 승격되지 않는다. 실제 OneDrive 배달은 예약 Runner가 볼 때쯤 몇 분 된 파일이다.
테스트는 sleep 대신 mtime을 의도적으로 뒤로 민다.

---

### 5. Control Tower가 **유일하게** 회사의 Blocker를 보여준다 (기록)

E2E가 드러냈다. `BLOCKED` Event는 History Filter가 **REVIEW**로 보내고(docs/05 §24)
REVIEW 후보는 어떤 Daily에도 렌더링되지 않는다(E-20). 즉 이번 실행에서:

    Company History     KEEP 3건만 (Blocker 없음)
    Control Tower       열린 Blocker 1건, 문구·팀·증거 파일까지

**회사의 유일한 열린 리스크가 Company History에는 없다.** E-20이 기록하는 성질의
결과이고, Control Tower가 그것을 처음으로 사람 눈에 닿게 한다. 테스트가 두 집합의
차이를 정확히 고정한다 — "유실"이 아니라 History Filter의 규칙이라는 것까지.

---

### 6. 저장소 게이트가 또 발화했다 (기록)

- `LayeringInvariantTests` — `controltower -> reporter` 미선언. `profiles.py`는 순수
  어휘이고 `reporter`는 `controltower`를 import하지 않으므로 비순환. 선언했다.
- `DeadCapabilityInventoryTests` — `CompanyRollup.desktop()`이 호출자 없음. 뷰가
  순회하므로 지웠다(C46의 `days_silent`와 같은 판단).
- `DashboardSchemaMappingTests` — 새 열 둘이 builder와 스키마 양쪽에 있는지, 타입이
  맞는지 자동으로 검사했다(통과).
- `AHealthyRuntimeCanActuallyBeQuietTests` — §2.

---

### 7. 한 배치 안에서 Notion 행이 **파일 이름 순서**에 좌우됐다 (신규, **데이터 정합성**)

§4의 무작위 체인 fuzz가 12회 중 2회에서 잡았다. `I4 Blocker None != '...'` —
rollup은 막혀 있다고 하는데 Notion 행에는 Blocker가 없다.

**원인.** 4b단계는 `collector_summary.files` 순서로 sync한다. 그 순서는 Collector의
`sorted(glob("*.json"))`, 즉 **파일 이름 순서**다. 그리고
`event_id = uuid5(namespace, "<Desktop>|<날짜>|<Signal 파일명>")`이므로 그 순서는
**일이 언제 있었는지와 아무 관계가 없다.** docs/04 §29-30의 Late Event 가드는 행의
`Last Updated`보다 새롭지 않은 Event를 거부한다.

**실측** — 한 프로젝트의 Event 둘, **같은 배치**, 한 시간 차이:

| 파일 이름 순서 | 결과 |
|---|---|
| 시간 순서와 같음 (`A-BLOCKED`, `B-DECISION`) | `NOTION_CREATED` → `NOTION_UPDATED`, 행에 **Blocker 있음** |
| 시간 순서를 뒤집음 (`Z-BLOCKED`, `A-DECISION`) | `NOTION_CREATED` → **`NOTION_SKIPPED_OLD_EVENT`**, 행에 **Blocker 없음** |

두 Event 다 운영상 "늦은" 것이 아니다 — 같은 실행 안에서 한 시간 차이다. 운영자가
둘 중 어느 결과를 받는지는 **uuid 동전 던지기**였고, 지는 쪽은 **막힌 프로젝트가
건강한 것으로 보이는** 것이다. Control Tower가 틀리면 안 되는 유일한 숫자다.

**E-23과 다르다.** E-23은 timestamp가 **같은** 경우이고 가드의 "동시" 규칙이 명세대로
동작하는 것이다. 이쪽은 timestamp가 다르고 **입력 순서가 임의**였다. 가드는 옳고,
먹인 순서가 틀렸다.

**고친 것:** 4b가 먼저 읽고 **오래된 것부터 적용**한다. 정렬 키는 rollup이 쓰는 것을
**그대로 import**한다(`controltower.event_instant_key` — 두 번째 순서 규칙을 만들면
두 답이 다시 갈라진다).

정렬이 최종 상태를 바꾸는 경우는 **오래된 Event가 새 Event가 건드리지 않는 상태를
들고 있을 때뿐**이고(`RESUMED`가 blocker를 지우고 `DECISION_APPROVED`는 아무것도
건드리지 않는다), 정확히 그 경우에 정렬된 답이 **같은 Event들에 대한 fold의 답**이다
— `TheRowIsExactlyTheFoldOverWhatReachedItTests`가 고정하는 그 불변식이다.

**가드 자체는 손대지 않았다.** *다음* 실행에 도착한 진짜 Late Event는 여전히 거부된다
(테스트로 고정). 읽을 수 없는 파일도 그대로 세어지고 기록된다 — 바뀌는 것은 sync 줄과
로그 줄의 **상대 순서**뿐이다.

**테스트:** `OneBatchIsAppliedOldestFirstTests` 6건 — 양쪽 순서, 성질로서의 진술
(행 == fold), 건너뜀 0건, 진짜 Late Event는 여전히 거부, 정렬 키가 rollup의 것과
같은 것.

---

### 8. 무작위 체인 fuzz — 나머지 불변식은 전부 성립 (기록)

§7을 잡은 하네스가 검사하는 것(브리핑 §6의 목록 그대로):

    I1 어느 Desktop의 project 목록에도 다른 Desktop의 project가 없다
    I2 Desktop별 합 == events_read, Team별 합 == events_read (중복 집계 없음)
    I3 OPS_RUNS의 Desktops Reporting / Role Mismatches / Accepted == rollup
    I4 PROJECTS 행의 Blocker == rollup, Source == 그 Project를 가진 Desktop
    I5 Runner 재실행이 행을 늘리지 않는다
    I6 rollup이 대는 증거 파일이 실제로 있다 (증거 없는 성공 표시 금지)
    I7 Agent 재실행이 Event를 늘리지 않는다

Desktop 4개 × 무작위 Signal(0~4건/Desktop) × event_type 8종으로 **25회**, §7 수정 후
**결함 0건**.

---

### 9. 배치를 정렬해도 **실행을 건너 도착한** Late Event는 남는다 (특성화)

§7을 고치면서 경계를 분명히 해 둘 필요가 생겼다. 정렬은 **한 배치 안의 임의 순서**를
없앤다. 없애지 못하는 것은 **다른 실행에 도착한** Event다.

    실행 1   DECISION_APPROVED 10:00   -> 행이 10:00으로 이동
    실행 2   BLOCKED           09:00   -> NOTION_SKIPPED_OLD_EVENT (정당하다)

    Notion 행   Blocker 없음
    fold        Blocker "arrived late"

가드는 docs/04 §29-30 그대로 옳게 동작한다. 대가는 **행이 "괜찮다"고 말하는 동안
fold는 "막혀 있다"고 말한다**는 것이고, §7과 달리 **고칠 순서가 없다** — 두 Event는
정말로 다른 실행에 도착했다.

특이한 배치가 아니다: 꺼져 있던 Desktop이 어제의 `BLOCKED`를 오늘의
`DECISION_APPROVED`가 이미 동기화된 뒤에 보내면 이 모양이다(docs/07 §58이 꺼진
Desktop을 정상으로 규정한다).

**보상 통제가 이번 Sprint에 생겼다는 점이 기록의 요점이다.** `CONTROL TOWER` 블록은
디스크의 fold를 읽으므로 **로컬에서는** 그 프로젝트가 막혀 있다고 말한다. Notion을
Source로 읽으면 안 되는 이유가 이것이고, docs/14 §1이 이미 그렇게 적어 두었다.

닫으려면 가드가 하는 일을 바꿔야 하고 그것은 E-23의 열린 결정이다. 테스트가
**divergence를 명시적으로** 고정한다(행은 None, fold는 "arrived late").

---

### 10. 복원된 머신에서 Control Tower가 "아무 일도 없었다"고 말한다 (신규, **복구**)

Recovery Audit에서 나왔다. `runtime/events/processed/`는 Execution Evidence이고
(docs/14 §2), Backup 범위는 `daily/`와 `monthly/`뿐이다(docs/08 §26). 즉 머신을
잃고 복원하면 **Company History는 전부 돌아오고 Event는 하나도 돌아오지 않는다.**

**실측**(복원 모양의 트리, 일이 든 Daily 18일치, `processed/` 비어 있음):

    집계 대상       Event 0건 (전체 기간)
    움직인 Project  0
    Team            넷 다 "이 기간 활동 없음"
    ATTENTION       (없음)

화면상 **아무 일도 하지 않은 회사와 구별되지 않는다.** B-6(보존 정책)이 오래된
Event를 지우면 같은 모양이 부분적으로 생긴다.

**고친 것 (한정자, 경보 아님):** Company History가 일을 기록하는 가장 이른 날과 남아
있는 Event의 가장 이른 날을 비교해, 전자가 앞서면 그 사실을 **숫자 옆에 적는다** —
`읽지 못한 파일 N건`과 같은 처리다. 경보로 올리지 않는다: 그 Event를 되돌리는 조치가
없고, 지워지지 않는 경보는 이 파일이 계속 걷어내는 것이다.

**오탐을 막는 지점이 하나 있고 그것이 이 검사의 핵심이다.** 비교 대상은 *가장 이른
Daily 파일*이 아니라 **일이 든 가장 이른 Daily**다. `generate_daily_history()`는 빈
날에도 파일을 쓰므로(docs/09 §72) `history_start_date`가 첫 Event보다 이른 것은
평범한 설치이고, 파일 기준으로 비교하면 **그런 설치가 전부 "증거를 잃었다"고 보고된다.**

**실측 4종:** 복원(전부 없음) → 보고 / 부분 정리(08-10부터) → 보고 / 건강한 트리 →
침묵 / 시작일이 첫 작업보다 이름 → **침묵**. 테스트 5건.

---

### 11. Secret이 Event **내용**으로 들어오면 아무도 막지 않는다 (신규, **Security**)

Security Audit. 이 저장소에서 가장 강한 Secret 방어는 문 **하나에만** 걸려 있다.

| 문 | 검사 | 결과 |
|---|---|---|
| 이 머신에서 쓴 Signal | `find_secret_material()`가 payload 전체를 훑는다 | **거부**. 전송되지 않는다 |
| 다른 Desktop에서 온 Event, 손으로 쓴 파일 | `validate_event()` | 타입과 교차필드만 본다. **내용을 읽지 않는다** |

**실측**(실 Runner, 실 git 원격, `summary`에 secret 형태 문자열 하나):

    validate_event()             []            <- 오류 없음
    Daily History에 쓰였나       예, 문자열 그대로
    원격에 push됐나              예 (`git show origin/main:daily/...`)
    scan_for_secrets()           ()            <- 이름만 본다 (docs/08 §29)
    oplog.redact()가 로그에서    [REDACTED]

즉 **문자열이 지워지는 유일한 곳은 로그이고, 영구히 남는 곳은 Company History와
backup 원격이다.**

**고친 것 (보고, 거부 아님):** `_secret_shaped_event_content()` — `processed/`의
Event 텍스트를 Agent와 **같은 규칙**(`oplog.SECRET_RE`)으로 한 번 훑어 `event_id`,
`source`, 파일명, **어느 필드**인지를 댄다. 일치한 문자열은 절대 반환하지 않는다
(`find_secret_material()`가 스스로 적어 둔 이유 — 유출 보고가 유출의 두 번째 사본이
되면 안 된다).

**성능:** 2,000 Event 기준 읽기 90 ms + 검사 25 ms. Event마다
`find_secret_material()`를 부르면 같은 답에 193 ms다(7개 패턴을 문자열마다 따로,
컴파일 없이 돌린다). 그래서 필드 목록을 명시하고, 그 목록이 낡지 않도록
`Event.to_dict()`와 대조하는 테스트를 붙였다 — 스키마에 문자열 필드가 늘면 검사에
들어가거나 suite가 깨진다.

---

### 12. 그 테스트가 **더 큰 유출을 잡았다** — "id는 내용이 아니다"가 틀렸다 (신규, **Security**)

§11의 회귀 테스트는 "새 보고가 자기가 찾은 문자열을 출력하지 않는다"를 주장했다.
**실패했다.** 두 블록 위 `Candidate 정합성` 줄이 같은 Event의 `event_id`를
**날것으로** 찍고 있었다.

`main()`의 ATTENTION sink는 `one_line()`만 적용하고 `redact()`는 일부러 적용하지
않는다. 거기 적힌 이유는 이랬다 — *"거의 모든 메시지는 파일명·id·개수로 만들어진다,
파일 **내용**이 아니다."* 그 문장은 **id가 기계가 만든 값이라는 가정**에 기대고
있었고, 그 가정이 틀렸다: `event_id`와 `project_id`는 Desktop이 스스로 정하는 평범한
문자열이고 `validate_event()`는 타입만 본다.

**실측:** 자기가 다루던 토큰의 이름을 딴 Event 하나가 그 토큰을 화면에도,
예약 실행이 리다이렉트하는 **로그 파일에도** 찍었다.

**고친 것:** `_authored()` = `redact(one_line(...))` 하나를 만들고, Event가 쓴
식별자를 찍는 **14곳 전부**를 그것으로 보냈다 — orphan 줄(출력·ATTENTION), 전달
누락, Retry Queue, Monthly 미반영, stranded Candidate, Control Tower의 Project /
Risk / PairMismatch, 그리고 `EvidenceRef.describe()`를 인용하는 두 줄.

Sink 자체는 그대로 뒀고 그게 맞다: 경로는 경로여야 하고, **내용을 담은 메시지는
만들어지는 자리에서 지운다** — `_print_control_tower()`의 blocker 줄이 이미 그렇게
하고 있었다. sink의 주석도 무엇이 틀렸는지까지 함께 고쳤다.

회귀 테스트는 **속성**으로 적었다: `now`를 받는 네 블록 전부에 대해, 모든 authored
필드에 같은 문자열을 넣은 Event 하나를 두고 **출력과 ATTENTION 어디에도 그 문자열이
없어야 한다**. 새 줄이 생겨도 아무도 케이스를 추가할 필요가 없다.

---

### 13. `--dry-run`이 **운영 실행**이었다 (신규, **Release**)

Release/Production Readiness Audit. 네 entrypoint 중 **하나도 `sys.argv`를 읽지
않았다** — `argparse`도, `ArgumentParser`도 없다(실측). 설정은 전부 환경변수이고
`scripts/install_agent_task.ps1`이 등록하는 action도 인자가 없다. 일관된 설계이고,
조용한 모서리가 하나 있었다:

    python run_company_ops.py --dry-run

**실제 운영 실행이 돌았다.** 진짜 git push, 진짜 Notion 쓰기, exit 0. 플래그는
거부되지도, 경고되지도, 읽히지도 않았다. 첫 운영 실행 전에 `--dry-run`을 찾는
사람은 정확히 이 도구에 없는 안전장치를 찾고 있는 것이고, 도구는 **안전하지 않은
쪽을 하고 성공을 보고**했다.

`--help`도 같은 모양이었다 — `COMPANY_OPS_HISTORY_START_DATE 환경변수가 없습니다`.
아무도 하지 않은 질문에 대한 참인 문장이다.

**고친 것:** `src/cli.py`(leaf, `oplog`·`runsummary`와 같은 위치) 하나에 규칙을 두고
네 entrypoint가 **import**한다. 거부하면서 **그 도구가 실제로 읽는 환경변수 이름을
댄다** — "인자를 받지 않는다"만으로는 갈 곳이 없기 때문이다. 종료 코드는 새로 만들지
않고 이미 문서화된 `1 설정 오류`를 쓴다(AGENT.md §6).

**설계상 중요한 지점 하나.** `main()`이 `sys.argv`를 직접 읽게 했더니 **기존 테스트
25건이 깨졌다** — in-process로 `main()`을 부르는 테스트에게 `sys.argv`는 pytest의
플래그다. 그래서 `argv`를 기본값 빈 튜플의 **파라미터**로 두고 `sys.argv`는
`__main__` 가드에서만 읽는다. 명령줄이 실제로 존재하는 곳은 거기뿐이다. 그 25건은
버그가 아니라 그 사실의 증명이었다.

회귀 테스트 7건. 전부 **실제 subprocess**로 돌린다 — 사람이 명령을 쳤을 때 OS가
보는 것을 확인하는 방법은 그것뿐이다. 메시지가 대는 환경변수 이름은 소스에서 긁은
`COMPANY_OPS_*` 집합과 대조한다(테스트 안에 목록을 다시 적으면 드리프트한다).

기존 게이트 셋이 발화했고 전부 정당했다: `OneRuntimeRootOrRefuseTests`(main의 첫
문장이 runtime-root 가드여야 한다 — 순서를 되돌렸다), 그리고 `def main()` 문자열로
본문을 잘라 보던 소스 스캔 3곳(같은 anchor를 새 시그니처에 맞췄다).

---

### 14. 미실행 분기 정리 — 새 코드는 분기까지 100%

C20이 남긴 작업의 이번 몫. `coverage --branch`로 실측했다.

| | Stmts | Miss | Branch | BrPart |
|---|---|---|---|---|
| `src/controltower/rollup.py` | 280 | **0** | 74 | **0** |
| `src/cli.py` | 11 | **0** | 2 | **0** |
| `ops_status.py`의 C46/C47분 | — | **0** | — | **0** |

닫은 것과, 그 과정에서 **코드가 틀렸다고 판명된 것**:

- `read_events()`의 OS 오류 둘(`os.scandir` 실패, 항목별 `is_file()` 실패) — 한 번도
  실행된 적이 없었다. `processed/`는 OneDrive 폴더일 수 있는 경로다. 테스트 3건:
  디렉터리를 못 읽으면 **`unreadable`에 이름이 남고**(빈 회사로 보이지 않는다),
  항목 하나가 stat에 실패해도 **나머지는 읽힌다.**
- `_event_day()`의 거부 분기 — Event의 `timestamp` 문자열은 파일에서 읽어 온 값이다.
  `None` / 빈 문자열 / `"not a date"` / `2026-13-45` / 정수 전부 `None`.
- `_company_history_older_than_the_evidence()`의 `except (OSError, ValueError)` —
  **날짜 이름 디렉터리로는 도달하지 않는다**(`_daily_dates()`가 파일만 나열한다).
  도달하는 것은 **UTF-8이 아닌 Daily 파일**이다(`UnicodeDecodeError`가 `ValueError`
  이므로 절이 둘을 함께 적고 있다). 잘리거나 반쯤 복원된 파일이 그 모양이다.
- **`if rollup.teams:` / `if rollup.desktops:` 가드 둘을 지웠다.** 다른 쪽으로 갈 수
  없었다 — 두 fold 모두 docs/02 §8 표를 **전부 seed**하고 조용한 항목도 돌려준다.
  가드가 있으면 "비어 있을 수 있다"고 읽히는데 이 블록의 요점은 그 반대다:
  **"물어봤고 없다"와 "묻지 않았다"는 다른 문장**이고 여기서 참인 것은 앞의 것이다.
- `blocked_by_team`의 중복 제거도 같은 이유로 지웠다 — `projects`는 `project_id`당
  하나이고 `teams`는 만들 때 이미 중복이 제거된다.
- **Project 목록의 무언 절단.** `_CONTROL_TOWER_PROJECT_LINES = 8`을 넘는 9번째가
  있을 때 "외 N건"을 찍는 줄에 테스트가 없었다. 이 저장소가 결함으로 취급하는
  모양이라 회귀 2건을 붙였다(9개 → "외 1건", 정확히 8개 → 아무 말 없음).
- 완료된 Project 줄(`완료 <날짜>`) — 유일하게 아무 테스트도 만든 적 없는 상태였다.

**내 테스트 기대가 틀린 것이 하나 있었고 그게 더 유용했다.** 빈 런타임에서 `Team` /
`Desktop` 헤딩이 **빠질 것**이라 적었는데 실제로는 넷 다 "이 기간 활동 없음"으로
나온다. 그게 옳은 동작이라 테스트를 사실에 맞췄다.

---

### 15. 남는 결정 (SKIP, 조건 명시)

**`source`/`role` 짝을 `validate_event()`가 강제할 것인가.**
- 강제하면: 모순된 Event가 `rejected/`로 가고 **Company History에서 사라진다.**
- 강제하지 않으면: 기록은 남지만 **주인이 틀린 채** Team 집계·Notion `Owner`에 들어간다.
- 어느 쪽이 나쁜지는 정책이다. 지금은 **이름을 대는 쪽**을 택했다(탐지 + ATTENTION +
  Dashboard 열).
- 결정에 필요한 것: docs/02 §8을 validator가 강제할지, 그리고 강제한다면 기존
  `processed/`의 모순 Event를 어떻게 다룰지(재검증? 그대로 둠?).

**Event 내용의 Secret을 `validate_event()`가 거부할 것인가** (§11).
- 거부하면: 그 Event가 `rejected/`로 가고 **그 작업이 Company History에서 사라진다.**
  Agent 문에서는 이것이 옳다 — Signal은 아직 아무 데도 가지 않았고 사람이 고쳐 다시
  내면 된다. 도착한 Event는 다르다: 원본은 다른 머신에 있고, 이 머신이 거부하면 그
  기록은 어디에도 남지 않는다.
- 거부하지 않으면: 자격증명이 Company History와 backup 원격에 **영구히** 들어간다.
  파일을 고쳐도 원격 history에는 남는다.
- 지금은 §1과 같은 처리를 택했다 — **이름을 대고 교체를 요구한다**(ATTENTION).
- 결정에 필요한 것: (a) 거부할지, (b) 거부한다면 이미 `processed/`에 있는 것들을
  어떻게 할지, (c) 거부 대신 **필드를 지우고 통과**시키는 세 번째 안을 쓸지 — 그것은
  Company History의 문장을 파이프라인이 고쳐 쓰는 것이라 docs/06 §57(사람의 편집)과
  충돌한다.

**Company History 파일 자체의 내용 검사** (§11의 남은 구멍). docs/06 §57은 Daily를
사람이 손으로 고치는 것을 허용하고, 거기에 붙여넣은 토큰은 §11의 Event 검사에
걸리지 않는다(Event가 없으므로). `scan_for_secrets()`의 docstring이 이미 실측으로
적어 둔 구멍과 같은 것이다(파일 6종 중 0건 탐지). 실측 비용: Daily 1,000개
(2.7 MB) 전체 내용 검사에 **889 ms** — `ops_status.py` 전체 실행이 오늘 310 ms인
것에 비해 크다. 무엇을 언제 훑을지(전체? 마지막 실행이 쓴 날짜만? 상태를 저장?)가
결정이고, 상태를 저장하면 이 도구가 처음으로 읽기 전용이 아니게 된다.

**Goal / Team Goal / Sprint / Task** — C46 §15 그대로. 원천이 없다.

**팀별 Notion View(Board/Calendar/Timeline)** — Workspace 설정 + 자격증명(A-8).
`OPS_RUNS`에 `Desktops Reporting`이 생겨 ④의 재료 일부는 이제 Notion 쪽에 있다.

**저장소 루트의 0바이트 stray 파일 5개** — `FETCH_HEAD`, `cd`, `claude`, `git`,
`main`. 전부 크기 0이고(내용 확인함) 8월 17일에 만들어졌다. 셸 리다이렉션 사고로
보이지만 **지우지 않았다**: 사용자 파일 삭제는 이 Sprint의 권한 밖이다. `.gitignore`에
없으므로 `git status`에 계속 뜬다. 지울지 무시 목록에 넣을지가 결정이다.

**Fuzz 하네스 재실행** — Desktop 1/2/3/4 → Control Tower → Notion 체인, 무작위
Event 구성으로 **100 trial, 문제 0**(C47 변경 전 40 + 변경 후 60). 고정하는 불변식
7개: Desktop 간 데이터 혼입 없음, Desktop별/Team별 합계 = `events_read`(중복 집계
없음), `OPS_RUNS` payload = rollup, PROJECTS 행의 Blocker/Source = rollup,
재실행 시 행·Event 증가 0, 모든 증거 파일이 실재, Agent 재실행 시 신규 0.

---

## C46. Company Control Tower — 원천이 있는 계층부터, 없는 계층은 없다고 말한다

요청은 Notion을 전사 Control Tower로 키우는 것이었다. 먼저 **무엇이 이미 있고
무엇에 원천이 없는지**를 조사했고, 그 조사 결과가 이 Sprint의 설계를 정했다.

---

### 1. 조사 — 요청된 7계층 중 셋만 원천이 있다 (기록)

| 요청 계층 | 이 시스템의 원천 | 상태 |
|---|---|---|
| Company Goal | 없음 | **원천 없음** |
| Team Goal | 없음 | **원천 없음** |
| Project | `event.project_id`, Notion PROJECTS DB | 있음 |
| Sprint | 없음 (`Sprint`는 주석 속 개발 용어로만 등장) | **원천 없음** |
| Backlog / Task | 없음 (`BACKLOG.md`는 **개발** 백로그다) | **원천 없음** |
| 실행 결과 | Event / Daily / Monthly / Run Manifest | 있음 |
| KPI | 위에서 **파생 가능** | 파생 |
| Risk / Issue | `BLOCKED` + `blocker`(docs/02가 필수로 요구), ISSUE | 있음 |
| Ops Status | Run Manifest, `ops_status.py` | 있음 |

`grep`으로 확인: `src/` 전체에서 `goal`·`sprint`·`kpi`는 **필드로도 파일로도
명세 절로도 존재하지 않는다**. Team은 새로 만들 것이 아니라 `role`이다
(docs/02 §8이 source→role을 1:1로 고정한다).

**그래서 Notion에 DB를 신설하지 않았다.** docs/14 §1이 Operational Data Model을
고정하며 Notion 행을 이렇게 적는다 — `Notion (PROJECTS / OPS_RUNS)` /
"**View이며 절대 Source가 아니다**". Goal·Sprint·Task를 Notion에 적어 넣고
권위로 삼는 것은 그 문장을 정면으로 깬다. 원천은 Company Repository 산출물이거나
Event Schema 필드여야 하고, **둘 다 명세 결정**이다(§7 SKIP).

---

### 2. 그래서 만든 것 — `src/controltower/` (신규, 파생 전용)

원천이 있는 세 계층(Team=`role`, Project=`project_id`, 실행 결과)을
`runtime/events/processed/`(docs/14 §2 Execution Evidence)에서 굴린다. 새 측정도,
새 state도, 새 파일도 없다 — 이미 있는 것을 묶기만 한다.

**상태는 마지막 Event가 아니라 접어서(fold) 구한다.** 월요일 BLOCKED, 수요일
RESUMED인 Project는 막혀 있지 않고, 그 사실은 순서를 재생해야만 나온다 — `RESUMED`
Event는 blocker 텍스트를 들고 있지 않고 **없다는 것 자체가 신호**이기 때문이다.
`ExecutionPlanSync`가 PROJECTS 행에 Event를 하나씩 적용하는 이유와 같다.

**규칙은 한 곳에서만 온다.** 어떤 Event가 blocker를 열고 닫는지는 docs/04 §20-28의
규칙이고 그것은 `notion/properties._type_specific_properties()`에 산다. 여기서
다시 쓰면 맞춰 둬야 할 규칙이 둘이 되므로(C28), **그 함수의 답을 읽는다** —
`Blocker`가 없으면 변화 없음, `rich_text`가 비었으면 해제, 내용이 있으면 설정,
`Completed Date`가 있으면 완료. §20-28이 바뀌면 이쪽이 따라온다.
`OneRuleForBlockerStateTests`가 event_type 8종 전부에 대해 두 답을 대조한다.

**추적 가능성이 기능이 아니라 요점이다.** 모든 rollup과 모든 metric이
`EvidenceRef`(event_id + `processed/` 파일명 + timestamp)를 들고 다닌다.
"열린 Blocker가 왜 3인가"는 이름이 적힌 파일 셋을 여는 것으로 끝난다.

**KPI에 target을 붙이지 않았다.** target은 Goal이고 Goal은 원천이 없다. 화면에
아무도 합의하지 않은 숫자를 띄우는 것이 이 Sprint가 피한 것 전부다.

---

### 3. 보이는 곳 — `ops_status.py`의 여섯 번째 블록

기존 다섯 블록은 전부 *운영*을 본다. 새 `CONTROL TOWER` 블록은 *일*을 본다:
움직인 Project, 팀별 Event/막힌 Project, 완료 Milestone / 승인 Decision / 해결
Issue, 그리고 **열려 있는 Blocker**. Project 줄은 막힌 것이 위, 그다음은 조용한
순서다.

ATTENTION에 올리는 것과 **일부러 올리지 않는 것**:

| | | |
|---|---|---|
| 열린 Blocker | **올린다** | docs/02가 사람이 쓴 `blocker`를 필수로 하고, 파이프라인은 그것을 **스스로 지우지 않는다** — 그 팀의 RESUMED/ISSUE_RESOLVED/COMPLETED만 지운다. 정확히 이 절의 기준("사람이 지금 할 일")이고, 보고하는 순간 사라지므로 지워지지 않는 경보가 아니다. **임계값을 만들지 않았다** — 열려 있으면 열려 있는 것이다 |
| 조용한 Team | 올리지 않는다 | source→role이 1:1이라(docs/02 §8) COMPANY 블록이 이미 말하는 조용한 Desktop과 **같은 사실**이다. 한 사실에 두 줄은 이 프로젝트가 계속 걷어내는 두 번째 의견이다 |
| 읽을 수 없는 파일 | 올리지 않는다 | HISTORY 블록의 `Candidate 정합성` 줄이 이미 이름을 댄다. 여기서는 **아래 숫자가 그만큼 적다**는 사실로만 적는다 |

블록 마지막 줄은 원천이 없는 계층을 **이름으로 댄다**. 빈 패널은 "아무 일도 없다"로
읽히고, `milestone` 같은 것으로 Goal을 지어내면 권위로 읽힌다. "물어볼 곳이 없다"가
유일하게 참인 문장이다.

**비용(실측, warm).** 이 블록은 `processed/`를 한 번 더 읽는다 — 이 명령이 이미
두 번 읽는 디렉터리다(`find_orphaned_events`, `read_company_activity`).

| | 500 Event | 2,000 | 6,000 |
|---|---|---|---|
| CONTROL TOWER 블록 | 31 ms | 106 ms | 318 ms |

그 안의 분해(6,000건): **읽기 292.4 ms / project fold 6.1 ms / 읽기 제외 전체
32.2 ms.** 즉 이 모듈의 비용은 파일 OPEN이고, 같은 디렉터리에 대해
`history/reconciliation.py`가 이미 잰 것과 같은 결론이다.

이 과정에서 `_roll_teams()`가 project fold를 **한 번 더** 돌리고 있던 것을
없앴다. 처음에 "절반이 사라진다"고 적었는데 **틀렸다** — 실측 6 ms다. 중복을
없앤 이유는 느려서가 아니라 파생이 둘이면 안 되기 때문이고, docstring을 실측값으로
정정했다.

---

### 4. 저장소의 게이트가 셋 다 발화했다 (기록)

새 패키지를 넣자마자 이 저장소의 governance가 순서대로 잡았고, 전부 옳았다.

1. `LayeringInvariantTests` — "src/ 아래 패키지에 ALLOWED 항목이 없다".
   `controltower: {"events", "notion"}`로 선언했다(notion은 §20-28 규칙 하나
   때문이고, 그래서 순환이 없다).
2. `DeadCapabilityInventoryTests` — "production caller가 없는 공개 함수".
   즉 **파생 계층만 만들고 화면에 안 붙이면 dead code로 거부된다.** 이것이
   `ops_status.py`에 붙인 이유의 절반이다.
3. `OperatorGuideMatchesTheToolTests` — "도구가 찍는 블록을 AGENT.md가 모른다".
   AGENT.md §6에 블록 설명을 넣고 "다섯 블록"을 "여섯 블록"으로 고쳤다.

---

### 5. Manifest → Dashboard 파생 관계 실측 (결함 0건, 기존 기록 1건 확인)

§7이 우선하라고 한 파생 관계 중 하나를 끝까지 대조했다. 실 Runner + Notion
double로 세 경우:

| 경우 | Manifest | OPS_RUNS 행 |
|---|---|---|
| 정상 | SUCCESS / exit 0 | `Overall OK`, 12열 전부 일치 |
| 삭제 게이트 발동 | FAILED / exit 2, `backup BACKUP_FAILED` | `Overall FAIL`, `Failed Steps backup`, `Deleted Files 1` — **일치** |
| Backup push 실패 | DEGRADED / exit 3, `backup BACKUP_PENDING` | **행 없음, pending 파일도 없음** |

세 번째는 A-18의 알려진 결과다(`GitOperationError`가 `run_once()` 밖으로 나가
9b단계에 도달하지 못한다). 새로 잰 것은 **pending 파일이 만들어지지도 않는다**는
점이다 — "재시도 큐에도 들어가지 않는다"보다 강한 사실이고, 그래서 Notion 쪽에는
그 실행이 **존재한 적도 없는 것처럼** 보인다. 로컬은 덮여 있다(Manifest는 모든 종료
경로에 쓰이고 `ops_status.py`가 "시작되지 못한 단계"로 보고한다). Control Tower
관점에서 이것이 뜻하는 바는 하나다: **OPS_RUNS 행의 연속성을 완전성으로 읽으면 안
된다.** docs/14 §1이 Notion을 View로 못 박은 이유가 정확히 이것이다.

완전한 수정은 부분 행(partial row) 개념이 필요하고 그것은 스키마 결정이다 — 그리고
없는 숫자를 0으로 채우는 것은 `record_run()`의 주석이 명시적으로 금지한다
("a caller that has the numbers and does not pass them is reporting a healthier run
than happened"). SKIP.

---

### 6. E-23은 "최신성"만 잃는 것이 아니다 — 그리고 그 숫자는 이미 디스크에 있었다 (신규)

Control Tower를 만들고 나서야 물을 수 있게 된 질문 하나: **Notion PROJECTS 행과
디스크의 Event가 같은 상태를 말하는가?** 실 `ExecutionPlanSync`와 rollup을 같은
Event 열에 나란히 돌렸다.

    E1  STARTED  IN_PROGRESS   2026-08-01T00:00:00+09:00  -> NOTION_CREATED
    E2  BLOCKED  BLOCKED       2026-08-01T00:00:00+09:00  -> NOTION_SKIPPED_OLD_EVENT

    Notion 행   Status IN_PROGRESS   Blocker 없음   Last Event EVT-A
    디스크      Status BLOCKED       Blocker "예산 승인 대기"

E-23은 이 손실을 "**Notion 쪽 Current State의 최신성**"이라고 적는다. 그것이
전부가 아니다: 행은 한 Event 뒤처진 것이 아니라 **위험 상태의 반대**를 보여줄 수
있다 — 막힌 프로젝트가 건강한 것으로. Notion을 Control Tower로 읽는 사람에게 그
숫자가 틀리면 안 되는 유일한 숫자다. 그리고 이 timestamp는 크래프팅이 아니다:
timestamp 없는 Signal은 그 날짜의 자정을 받고(docs/06 §12), 그것이 그날 모든
Signal에 대해 같은 값이다.

**그리고 그 사실은 이미 세어져 디스크에 있었다.** C40이 `same_instant_skips`를
Run Manifest의 `notion_sync` metric으로 넣었다(규칙을 다시 유도하지 않고
`notion/sync.py`가 붙인 note로 인식한다 — 그래서 두 답이 갈라질 수 없다). 그런데
`_print_last_run()`은 **FAILED 컴포넌트의 metric만** 출력하고 — 블록을 짧게
유지하려는 의도적 규칙이다 — 건너뜀은 실패가 아니다(docs/04 §35 "적용하지
않았다", `recorder.ok()`, exit 0). 그래서 그 숫자는 해당 실행마다 파일에 적히고
**어떤 뷰도 보여준 적이 없다.**

**고친 것 (관측성, 결정 없음):** `_same_instant_skips_from_the_last_run()`이
NOTION 블록에서 그 metric을 꺼낸다. 새 파싱도 새 유도도 없다 — 규칙을 가진 모듈이
센 숫자를 읽기만 한다. 실행 단위이므로 다음 실행에서 사라지고(지워지지 않는 경보가
아니다), 문구가 대는 조치는 AGENT.md §3이 이미 문서화한 완화책이다(그 날짜 Signal에
`timestamp` 명시).

E-23의 결정 자체는 그대로 열려 있다 — 후보 수정 셋이 전부 명세 변경이다.

**테스트:** `SameInstantSkipReachesTheOperatorTests` 7건(0/부재/손상된 metrics
컨테이너/위조된 값 포함) + `SameInstantSkipEndToEndTests` 2건 — 실 Runner로
Signal 둘을 흘려 manifest metric → Notion 행 → CONTROL TOWER rollup → ATTENTION
까지 한 번에 잇고, 1초만 떨어뜨리면 이 경로를 타지 않는 것도 함께 고정한다.

---

### 7. 저장소 게이트가 한 번 더 발화했다 (기록)

`DeadCapabilityInventoryTests`가 이번에는 **줄어든 것**을 잡았다:
`RunSummary.component()`가 §6의 새 호출자를 얻어 dead 목록을 떠났다. 목록과
BACKLOG C31 §17 기록을 함께 정정했다 — 이 저장소는 "죽은 능력이 생기는 것"만이
아니라 "살아난 것을 기록하지 않는 것"도 실패로 다룬다.

---

### 8. 같은 결함 계열을 전수로 훑었다 — SUCCESS 컴포넌트에만 실리는 metric (기록)

§6은 한 건이었다. 같은 모양이 더 있는지 AST로 `recorder.ok()` / `recorder.failed()`
호출을 전부 걸어 대조했다.

**SUCCESS에만 실려 LAST RUN이 절대 찍지 않는 metric:**

| 컴포넌트 | metric | 다른 곳에서 보이는가 |
|---|---|---|
| `transport` | moved / skipped_not_stable / skipped_invalid / skipped_already_present / skipped_incomplete / failed | **보인다** — COMPANY 블록의 backlog 귀속이 그 사유들을 이름으로 댄다 |
| `collector` | accepted / duplicate / rejected / failed | **보인다** — Dashboard 열 4개 + `run_company_ops.py` stdout |
| `daily` / `backup` | status | **보인다** — HISTORY 블록 / Dashboard |
| `dashboard` | drained | **보인다** — NOTION 블록의 밀린 기록 수 |
| `history_filter` | kept_dates | 안 보인다 — 다만 경보 가치가 있는 사실이 아니다(정상 실행의 계수) |
| `notion_sync` | **same_instant_skips** | **어디에서도 안 보였다** → §6에서 고침 |

가르는 성질이 하나 있다: **`or None`로 기록되는 metric은 "정상일 때 아예 없는" 값,
즉 이상 계수다.** 그런 것이 둘뿐이고(`same_instant_skips`, `reused_days`), 후자는
Dashboard 열 · `run_company_ops.py` stdout · Manifest 세 곳에 닿는다. 나머지 SUCCESS
metric은 전부 평범한 계수이며 "블록을 짧게 유지한다"는 `_print_last_run()`의 규칙이
옳게 적용된 자리다. **이 계열에서 남은 구멍은 0건이다.**

---

### 9. 새 줄 하나가 ATTENTION의 규칙을 깼다 — 내가 넣은 것 (신규, **보안**)

`ops_status.main()`의 ATTENTION sink는 `redact()`를 **일부러** 걸지 않고 그 이유를
적어 둔다: *"Every ATTENTION message is built from filenames, ids and counts —
never from a file's contents"*, 그리고 *"If a message ever starts carrying a
response body, it needs `redact()` too"*.

§3에서 넣은 Blocker 줄이 정확히 그것을 깼다. `blocker`는 **다른 Desktop에서 사람이
타이핑해 OneDrive를 건너온 Event 내용**이고, `"waiting for NOTION_API_TOKEN=… to be
rotated"`는 blocker에 충분히 쓸 법한 문장이다. Agent의 Signal 계층은 secret 모양의
Signal 내용을 거부하지만(`agent/signals.py`), Desktop 4 자신의 Reporter와 손으로 쓴
Event는 그 계층을 지나지 않는다.

**고친 것:** 그 한 줄이 **자기 자리에서** `redact()`를 부른다 —
`run_company_ops.py::_print_result()`가 `failure.reason`에 대해 하는 것과 같다.
sink는 그대로 두었다: 경로를 과다 redact하면 운영자가 열어야 할 파일 이름이 망가진다.
sink 쪽 주석도 "예외가 하나 있고 그것은 생산 지점에서 처리한다"로 정정했다.

**rollup 자체는 blocker를 원문 그대로 들고 있다** — redaction은 *뷰*의 일이고,
파생이 몰래 입력을 고치면 그게 더 찾기 어려운 버그다. 테스트 2건이 양쪽을 고정한다.

**실측:** 토큰이 든 blocker → ATTENTION에 `[REDACTED]`, 문장의 나머지("rotated")와
Project 이름은 살아남는다.

---

### 10. Control Tower가 `processed/`를 load-bearing으로 만든다 (B-6에 붙는 조건)

B-6(보존 정책)은 지금까지 `processed/`를 **중복 방지 한 계층**으로만 다뤘다. §2 이후
그 디렉터리는 **Company/Team/Project 상태의 유일한 원천**이기도 하다. 오래된 Event를
지우면 Company History는 그대로지만 Control Tower는 조용히 과거를 잃는다 —
"열려 있는 Blocker"가 그 Event를 지운 순간 사라진다.

B-6 결정에 이 조건을 더한다: **`processed/`를 줄이려면 Project 상태의 원천을 먼저
정해야 한다**(예: 접힌 상태의 스냅샷). 지금은 조치가 필요 없고, 결정할 때 잊으면 안
되는 항목이다.

---

### 11. 두 파생이 같은 규칙인지 무작위 열로 확인했다 (신규, 불변식)

이제 한 Project의 상태를 유도하는 곳이 둘이다: `ExecutionPlanSync`가 View에 Event를
하나씩 적용하고, `controltower`가 디스크에서 접는다. §2의 테스트는 `event_type`
하나씩 대조하는데, 순서·fold 실수는 **열(sequence)**에서 산다.

성질을 하나로 적어 유일한 정당한 분기를 예외가 아니라 성질의 일부로 넣었다:

    Notion 행  ==  fold(가드가 건너뛰지 않은 Event들)

즉 **건너뜀은 전체 fold와의 차이를 설명하고, 그 외의 어떤 것도 설명하지 못한다.**
한쪽에만 적용되는 규칙, 순서 차이, 한쪽이 잊는 필드가 있었다면 건너뜀이 가득한
열에서도 이 등식이 깨진다.

**실측 300 seed:** 건너뜀이 하나도 없는 열 18건, 하나 이상인 열 282건 — **전부
성립**. 스위트에는 60 seed(0.8 s)를 넣었고 seed가 고정이라 실패는 번호로 재현된다.
표본에 두 종류가 다 들어 있는지도 함께 단언한다(한쪽만 보면 성질의 절반만 검사된다).

**테스트:** `TheRowIsExactlyTheFoldOverWhatReachedItTests`.

그리고 신규 설치(`runtime/`이 아예 없는 머신)에서 이 블록이 답하는지도 고정했다 —
docs/11이 운영자에게 가장 먼저 시키는 명령이고, 새 블록이 거기서 죽으면 셋업 중에
뷰 전체가 쓸모없어진다.

---

### 12. 확립된 패턴이 여기서는 **느렸다** (성능, 실측 후 되돌림)

이 블록은 `processed/`를 한 번 더 읽는다 — 이 명령이 이미 두 번 읽는 디렉터리다.
`history/reconciliation.py`·`agent/delivery.py`·`app/desktop_activity.py`가 전부
같은 디렉터리에 대한 pass를 스레드로 돌리고 같은 이유를 적어 둔다("the cost is the
file OPEN"). 그대로 따라 했고, **느려졌다.**

`ops_status.py` 안에서, warm:

| | serial | threaded |
|---|---|---|
| 500 Event | **27.2 ms** | 42.2 ms |
| 2,000 Event | **114.6 ms** | 175.0 ms |

차이는 각 pass가 바이트로 무엇을 하느냐다. 저 셋은 파일을 읽고 **싼 질문 하나**를
하고, 이 pass는 `Event.from_json()`을 돌린다 — JSON 파싱 + `validate_event()`,
즉 GIL 아래의 진짜 CPU다. 풀은 겹치라고 넣은 일을 직렬화하면서 hand-off 비용만
받는다. 되돌렸고, **숫자를 코드 옆에 적어 두었다** — 다음 사람이 같은 패턴을
"당연히 빠르다"고 다시 넣지 않도록.

이번 Sprint에 예측을 실측이 뒤집은 것이 이것으로 **두 번째**다(다른 하나는
`_roll_teams()`의 중복 fold 제거가 "절반"이 아니라 6 ms였던 것). 둘 다 docstring을
실측값으로 고쳤다.

**남는 비용:** 블록은 명령 전체의 약 **23-25%**다(500 Event에서 119 ms 중 27 ms).
`processed/`가 무한히 커지는 것은 B-6이고, §10이 그 결정에 조건을 하나 더했다.

---

### 13. Dead-capability 게이트의 사각 — 이름 충돌 (신규, 테스트 감사)

`DeadCapabilityInventoryTests`는 정의된 이름과 **호출된 이름**을 AST로 모아 차집합을
낸다. 이름 기준이고 **한정(qualified)되지 않는다.**

내 새 코드에서 그것이 발화하지 않는 것을 봤다: `TeamRollup.days_silent()`는 아무도
부르지 않는데, `ops_status.py:1772`가 **다른 클래스**의
`DesktopActivity.days_silent()`를 부르기 때문에 이름이 "호출됨"에 들어가 통과했다.

**고친 것:** 그 메서드를 지웠다 — 팀의 침묵 기간은 COMPANY 블록의 줄이고
(source→role이 1:1), 이 모듈은 팀 침묵을 **일부러** 경보하지 않으므로 설계상 호출자가
생길 수 없는 접근자였다. 지운 자리에 그 이유를 적어 두었다. 같은 검토에서
`ProjectRollup.status`는 반대로 **쓸 곳이 있었다** — "만들어지고 움직인 적 없음
(NOT_STARTED)"과 "진행 중"은 다른 사실이고 Event만 그것을 말한다. Project 줄에 넣었다.

**게이트 자체는 고치지 않았다.** 한정 이름으로 바꾸면 `self.x()` / 별칭 / 상속을
전부 다뤄야 하고, 지금 형태는 "정말 아무도 안 부르는 것"을 잡는 데 충분히 값을 해
왔다(이번 Sprint에도 두 번 발화했다). 사각의 모양을 여기 적어 두는 것이 지금의
정직한 처리다 — 이름이 겹치는 새 메서드는 이 게이트에 잡히지 않는다.

---

### 14. 내 새 코드가 Event 하나를 조용히 떨어뜨리고 있었다 (신규, 자기 감사)

경계 테스트를 쓰다가 나왔다. `build_company_rollup()`은 Event를 기간에 넣기 위해
`_event_date()`로 날짜를 구하고, 읽히지 않으면 `continue` — **아무 말 없이**
버렸다. `events_read`는 디렉터리와 다른 숫자가 되고 그 이유를 볼 방법이 없다.
이 저장소가 계속 닫아 온 바로 그 모양이고, 이번엔 내가 만들었다.

**고친 것:** `unreadable`에 넣는다. 그 필드는 이미 "디렉터리에 있는데 이 rollup이
쓸 수 없었다"는 뜻이고, 뷰가 "아래 숫자는 그만큼 적다"로 렌더링한다.

`read_events()`로는 만들 수 없다 — `Event.from_json()`이 `validate_event()`를
돌리고 그것이 offset 있는 ISO-8601을 요구한다. 닿는 길은 `events=` seam(호출자가
직접 만든 객체)뿐이다. 그래도 **닿는 길이 있으면 조용하면 안 된다**는 것이 이
저장소의 규칙이다.

함께 고정한 것: 비교 불가능한 timestamp가 주변 전부의 순서를 정하지 못한다
(`history/result.HistoryCandidate._sort_key()`와 같은 2단 정렬 키). naive timestamp는
**날짜는 있으므로** 기간에는 들어가고 정렬에서만 뒤로 간다.

---

### 15. 남는 결정 (SKIP, 조건 명시)

**Goal / Team Goal / Sprint / Task의 원천을 어디에 둘 것인가.**
- Notion은 **불가** — docs/14 §1이 View로 고정한다.
- 후보 A: Company Repository 산출물(`local_master/goals/…` 같은 Markdown) —
  docs/06/09과 나란한 새 명세 절이 필요하다.
- 후보 B: Event Schema 필드(`goal_id` / `sprint_id`) — docs/02 변경이고 기존
  Event 전부의 하위호환을 결정해야 한다.
- 어느 쪽이든 **Backup 범위**(docs/08 §26의 `daily/`·`monthly/`)와 **Daily/Monthly
  렌더링**에 영향이 간다.

결정 전까지 `UNSOURCED_LAYERS`가 그 사실을 화면에 적고, `NoInventedLayersTests`가
이 모듈이 몰래 채우지 않는지 지킨다 — Event Schema에 `goal`/`sprint` 필드가 생기면
그 테스트가 **실패해서** 계층을 채우라고 알린다.

**팀별 Notion Dashboard(Board / Calendar / Timeline / Chart View).** 요청의 일부이고
**코드로 만들 수 있는 것이 아니다** — Notion Database의 View 구성은 그 Workspace 안의
설정이며 자격증명이 필요하다(A-8). 게다가 지금 만들 수 있는 View의 재료는 PROJECTS
한 DB뿐이다: Board는 `Status`, Calendar/Timeline은 `Last Updated`/`Completed Date`로
바로 세울 수 있지만, "이번 Sprint" 보드나 Task 칸반은 §15 첫 항목의 원천이 생긴 뒤에야
의미가 있다. **순서가 있다** — 원천 결정 → 그 원천을 Projection으로 내보내는 코드 →
Workspace에서 View 구성. 지금 할 수 있는 마지막 단계만 남겨 두면 앞의 둘이 정해질 때
버려진다.

**Backup 실패 실행이 Dashboard에 남지 않는 것(A-18의 Notion 쪽 면).** §5에서 실측했다.
완전한 수정은 "일부만 채워진 행"이라는 개념이 필요하고 그것은 스키마 결정이며, 없는
숫자를 0으로 채우는 것은 `record_run()`의 주석이 명시적으로 금지한다. 그때까지 **OPS_RUNS
행의 연속성을 완전성으로 읽으면 안 된다** — docs/14 §1이 Notion을 View로 못 박은 이유가
정확히 이것이고, 로컬 Manifest는 모든 종료 경로에 쓰인다.

---

## C45. Durability & Fault-Injection Sprint — 지워진 것을 아무도 못 보는 자리

C44까지의 감사는 **읽는 경계**를 훑었다. 이번에는 (1) 쓰기가 실제로 디스크에
닿는지, (2) 실행 중간에 실패를 주입했을 때 유실이 **복구되거나 최소한
보고되는지**를 실행으로 물었다. 도구는 세 가지 하네스다 — 파이프라인 fuzz,
`os.replace` 결함 주입, 파일 손상 주입.

---

### 1. 원자적 쓰기 14곳 중 **fsync를 부르는 곳이 하나도 없었다** (신규)

`mkstemp` + `os.replace`는 **atomicity**를 산다 — 읽는 쪽은 절대 반쯤 쓰인
파일을 보지 않는다. **durability**는 사지 못한다. flush가 없으면 바이트는 OS
page cache에 있고 `os.replace()`는 NTFS가 저널하는 metadata 연산이므로, 둘은
어느 순서로도 디스크에 닿을 수 있다. 아픈 쪽은 rename이 먼저 가는 경우다 —
전원이 끊긴 뒤 파일은 **제 이름으로, 제 크기로** 거기 있고 내용은 그 블록에
있던 것(보통 0)이다.

이 저장소에게 가상의 사고가 아니다. `reporter/local_output.INCOMPLETE_WRITE_PREFIX`가
이미 *"프로세스가 돌아오지 않은 쓰기 — 전원 차단·SIGKILL·컨테이너 정지"*를
근거로 **staging 파일**을 모든 reader까지 추적한다. 이것은 같은 사고의 나머지
반쪽이고, **더 나쁜 반쪽**이다: 남겨진 `.tmp-…json`은 적어도 눈에 띄게 산출물이
아니지만, 0으로 채워진 `2026-08-05.md`는 모든 reader·탐지기·Backup이 **기록으로
받아들인다.** `_holes_in_the_daily_sequence()`는 *없는* 파일을 찾지 *빈* 파일을
찾지 않고, Backup은 그것을 commit·push한다.

**고친 것:** 14개 writer 전부에서 `os.replace()` 직전에 `handle.flush()` +
`os.fsync(handle.fileno())`. 계약은 한 줄도 바뀌지 않는다(같은 파일, 같은 내용,
같은 예외 경로). 15번째 `mkstemp` 자리인 `scheduler/lock.py`는 **일부러
제외했다** — Lock은 durable한 산출물이 아니고, 내용이 살아남지 못한 Lock은
파싱 불가 → stale → 인수로 읽히는데 그쪽이 회복되는 방향이다.

**실측 비용**(작은 JSON 200회, 로컬 디스크): 0.43 ms → 1.12 ms per write.
한 실행이 쓰는 파일은 수십 개 규모이므로 git이 지배하는 단계에 수십 ms.
전체 스위트 실행시간 344 s → 348 s(노이즈 범위).

**테스트:** `AtomicWritesReachTheDiskBeforeTheRenameTests` —
**소스 문자열 검사가 아니라 행동 검사**다. `os.fsync`와 `os.replace`를 둘 다
기록하고, writer 14개 각각이 **rename 전에** flush했는지, 그리고 두 번 이상
rename하는 writer(late update, OneDrive)의 **모든** rename이 flush 뒤인지를
본다. 한 writer에서 fsync를 지우면 그 subTest만 실패하는 것을 확인했다.
기존 `test_every_state_writer_uses_the_same_atomic_idiom`(소스 문자열, 7개)은
그대로 두었다 — 새 검사가 그것보다 넓고 강하므로 문자열 쪽을 늘리지 않았다.

---

### 2. `Event Count`만 되돌려 주지 않았다 — 그리고 그 줄이 탐지기의 유일한 입력이다 (신규)

`late_events._update_metadata()`의 docstring은 *"없는 필드(손으로 잘린 블록)는
삽입된다"*고 적는다. **셋 중 둘만 참이었다.** `Event Count`를 갱신하는 loop는
찾으면 `break`, 못 찾으면 아무것도 하지 않고, 아래 삽입 블록은 나머지 두 필드만
나열한다.

대가가 실행 하나를 넘어간다. `ops_status._daily_counts_more_than_it_shows()`는
이 숫자를 파일이 들고 있는 id와 대조하는데, **줄이 없거나 파싱되지 않는 파일은
건너뛴다.** 즉 손으로 잘린 Metadata 블록(docs/06 §57·docs/11 §71이 편집을
허용하고, 기계용 bookkeeping 한 줄을 지우는 것이 가장 자연스러운 편집이다)이
**세 가지 실제 유실의 유일한 탐지기를 그 날짜에 대해 영구히 꺼 버린다** —
`category=None` Candidate, 위조된 `- Event ID:` 줄(BUG-11/27), 손으로 지운 항목
블록. 이후 모든 late update가 없는 줄을 갱신하고 있었다.

**고친 것:** 세 필드를 같은 집합으로 다룬다(같은 함수의 "블록이 아예 없을 때"
분기는 이미 셋 다 쓴다). branch coverage로 찾았다 — 여섯 가지 조합 중 두 개만
실행되고 있었고, 실행되지 않던 넷 중 하나가 결함이었다.

**테스트:** `TrimmedMetadataFieldsAreRestoredTests` 7건. 그 중 하나는
탐지기를 직접 호출해 **고치기 전 () / 고친 뒤 다시 비교됨**을 보이고, 다른
하나는 복원된 숫자가 실제 유실(손으로 지운 항목 블록)을 `(날짜, 2, 1)`로
보고하는 것까지 확인한다.

---

### 3. Local Master에서 사라진 Company History를 **아무 탐지기도 보지 못한다** (신규, **데이터 유실**)

파일 손상 주입 하네스가 찾았다. `2026-08-01.md`를 **같은 이름의 디렉터리**로
바꾸고(반쯤 끝난 복사가 남기는 모양이고, C31이 여섯 자리에서 쫓던 바로 그
모양) 08-01..08-04가 닫힌 상태에서 실측:

| 물어본 것 | 답 |
|---|---|
| `_holes_in_the_daily_sequence()` | `()` |
| `_kept_but_not_rendered()` | `()` |
| `check_state_consistency()` | `CONSISTENT` |
| `_daily_counts_more_than_it_shows()` | `()` |
| `_misnamed_scope_directories()` | `()` |
| `daily 파일` | **5** (디렉터리를 세고 있었다) |
| ATTENTION | 2026-08-01을 말하는 줄 없음 |

**왜 구멍 검사가 못 보는가.** 그 함수는 범위를 *있는 파일*로 잡고 전제를 그대로
적어 두었다 — *"그것보다 앞선 것은 이 머신의 History 밖이다"*. 사라진 것이
**가장 이른 날짜들**이면 그 전제가 정확히 거짓이 된다: 첫 파일이 앞으로
밀리고 범위가 같이 밀리므로 구멍이 생기지 않는다. **누락된 prefix는 구조적으로
침묵이다.** 부분 복원이 도중에 멈춘 경우, OneDrive가 위에서부터 동기화한 경우,
"오래된 것"을 손으로 지운 경우가 전부 이 모양이다.

Backup은 실패한다 — 삭제 게이트가 같은 것을 본다 — 그러나 **파일 이름은
manifest의 `reason`에만 있고 `_print_last_run()`은 `reason`을 일부러 출력하지
않는다.** 운영자가 읽는 것은 `backup: BACKUP_FAILED`, 즉 **자격증명 실패와
똑같은 줄**이다. 그 혼동을 없애려고 쓴 것이 Runner의 삭제 게이트 주석 자신이다.

**고친 것 (관측성, 결정 없음):** `_history_gone_from_local_master()` —
Backup Working Copy에는 있고 Local Master에는 없는 Company History. 설정이
필요 없고 틀릴 수 없다: `sync_to_working_copy()`는 한 방향으로만 쓰고 **절대
Working Copy에서 지우지 않으며**(삭제를 감지하면 아무것도 적용하지 않는다 —
docs/08 §31/§44-47), 따라서 Working Copy는 backup 범위에 도달한 모든 파일의
단조 기록이다. 비교는 **게이트 자신의 목록**(`working_copy._relative_files`)을
쓴다 — 두 번째 의견이 아니라, `sync_to_working_copy()`의 `deleted`와 **같은 집합
연산**이다(테스트가 두 답이 같음을 고정한다).

**함께 고친 것:** `daily 파일` / `monthly 파일` 계수가 `is_file()`을 묻지 않는
마지막 reader였다(`_daily_dates()`, `_holes_in_the_monthly_sequence()`,
`_relative_files()`는 전부 묻는다). `_misnamed_scope_directories()`의 docstring이
이미 지나가듯 이름을 대고 있었다. 실측: 4일치 Company History에 대해
`daily 파일 : 5`가 `daily state 정합성 : CONSISTENT` 바로 위에 인쇄되고 있었다 —
같은 블록의 두 숫자가 서로 어긋났고, **유실을 감추는 방향**으로 어긋났다.

**테스트:** `HistoryGoneFromLocalMasterTests` 10건 +
`DailyAndMonthlyCountsExcludeDirectoriesTests` 4건. 조용해야 하는 쪽도 전부
고정했다 — Master가 Backup보다 앞선 평범한 상태, Working Copy가 아직 없는 머신,
Working Copy의 `.tmp-` 잔여물, 범위 밖 파일(`.gitkeep`).
AGENT.md §6의 HISTORY 블록 설명에 이 줄을 추가했다.

---

### 4. `STEP_ABORTED`는 어느 단계인지만 말하고 **왜인지는 말하지 않았다** (신규)

C34가 귀속을 넣은 이유는 귀속되지 않은 중단이 성공처럼 읽혔기 때문이다.
`reason`은 상수 문자열 그대로였다. **실측**, 손상된 `collector_state.json`
(docs/10 §46이 기대하라고 적은 종류다 — 잘린 쓰기, 부분 복원, 손편집):

    collector  FAILED  STEP_ABORTED
    reason     "the run aborted inside this step"

`PersistentSeenEventStore._load()`가 던지는 `CollectorStateError`는 **파일과
파싱 위치를 이름으로 댄다.** 그 문장은 Task Scheduler 콘솔 출력에만 있었다.
이후 모든 실행이 같은 자리에서 죽으므로 Company History는 멈춰 있고, docs/14 §3이
운영자에게 읽으라고 하는 그 artifact는 할 수 있는 말이 없었다.

**고친 것:** `finally`에서 `sys.exc_info()[1]`을 읽어 `reason`에 덧붙인다.
`except`를 새로 달지 않는다 — 전파되는 것은 한 줄도 바뀌지 않는다(테스트가
고정). `redact()`는 중단 예외가 원격 응답 본문을 실어올 수 있기 때문이고
(`oplog.append_line()`이 redact하는 바로 그 이유), `one_line()`은 삭제 게이트가
자기 파일 이름에 이미 걸어 둔 가드와 같은 이유다.

고친 뒤:

    reason  the run aborted inside this step: CollectorStateError:
            collector state file is corrupted: …\collector_state.json
            (Expecting value: line 1 column 28 (char 27))

**테스트:** `AnAbortedStepRecordsWhatAbortedItTests` 7건 — 파일 이름이 닿는가,
상수 접두어가 유지되는가, `ntn_…` 토큰이 manifest에 닿지 않는가, 5,000자가
잘리는가, 개행이 보고서 행을 위조하지 못하는가, **원래 예외가 그대로
탈출하는가**, 정상 실행에는 아무 영향이 없는가.

---

### 5. E-17에 **두 번째 진입 경로**가 있다 — 그리고 그쪽이 더 조용하다 (신규, 특성화)

`os.replace`를 임의 지점에서 실패시키는 결함 주입 60회 중 1회가 잡았고, 그 뒤
결정적으로 재현했다. E-17이 기록하는 원인은 하나다: `update_daily_history()`가
**돌고 실패한다** → manifest에 `late_update` / `LATE_EVENT_MERGE_FAILED` /
PERMANENT → ATTENTION, exit 3. 시끄럽다.

다른 하나: **6.5단계에 아예 도달하지 못한다.** 5단계가 Candidate를 쓰고,
그 사이에 있는 6단계에서 `scheduler/state.save_state()`가 OSError를 던지면
`scheduler.run_once()` 밖으로 그대로 나간다(BUG-3이 이미 적는 모양이다).

**실측**(`2026-08-01.md`가 이미 닫힌 상태에서 그 날짜의 late Event 하나):

| 실행 | manifest | Daily에 EVT-LATE |
|---|---|---|
| 2 (state save 1회 실패) | `daily` FAILED **STEP_ABORTED**, exit 2, `late_update` 컴포넌트 **부재** | 아니오 |
| 3 (깨끗) | 전 단계 SUCCESS, **exit 0** | 아니오 |
| 4 (깨끗) | 전 단계 SUCCESS, **exit 0** | 아니오 |

`daily_late_update.log`는 **존재하지 않는다** — AGENT.md §6a가 "돌긴 돌았는데
뭔가 안 됐다"에 보내는 sink인데, 그것을 쓰는 단계가 시작되지 않았다. 즉 Run
Contract의 신호만 읽으면 "6단계가 실패했고 이후 두 번 성공했다" = **해결된
것으로 읽힌다.** 진실을 말하는 것은 `ops_status._kept_but_not_rendered()` 하나이고
그것은 **다른 명령**이다.

재시도 자체는 E-17의 이유 그대로 SKIP이다(실패 날짜의 영속화 또는
Candidate-대-Daily 정합성 패스 — 값 하나가 아니라 메커니즘). 고정한 것은
이 진입 경로의 모양과, **탐지기가 이 경로도 덮는다**는 사실이다 — 그 덮음은
탐지기가 어떻게 쓰였는지에 따른 우연이었고 아무것도 붙들고 있지 않았다.
같은 날짜에 Event가 하나만 더 오면 둘 다 들어간다는 E-17의 유일한 자동 복구
경로도 이 경로에서 확인했다.

**테스트:** `AbortBeforeTheLateUpdateStrandsACandidateTests` 6건.

---

### 6. `agent state`의 `last_run`이 읽히지 않으면 "한 번도 실행된 적 없다"고 말했다 (신규)

`agent/state.load_state()`는 `last_run`이 **문자열인지**만 보고 멈춘다. 형제인
`last_successful_collection_date`는 추가로 파싱하고 실패하면 거부한다 — 그
파일의 두 날짜 필드 중 하나는 검사되고 하나는 아니다. 복원된·다른 버전이 쓴·
손으로 고친 state 파일이 `2026-08-0`, `yesterday`, `""` 같은 값을 그대로 싣고
로드된다.

`days_since_last_run()`은 그 전부에 대해 None을 돌려주고, "`last_run`이 아예
없다"에 대해서도 None이므로 **한 분기가 둘을 함께 보고**했다. 대가 둘, 둘 다 실측:

    문장이 거짓이다      `ops_status.py`가 세 줄 위에 `last_run` 값을
                         그대로 인쇄한다 — 뷰가 자기 자신과 모순됐다
    stale이 안 검사된다  몇 주째 멈춘 Agent가 "has not run for N day(s)"
                         대신 신규 설치의 줄을 받았다

**고친 것:** 두 조건을 갈랐다. `last_run`을 load에서 거부하지 **않는다** —
그 필드는 참고용이고 무엇을 수집할지는 `last_successful_collection_date`가
정하므로, 미관상 손상 때문에 Agent를 멈추는 것은 방향이 반대다. 값 자체는
메시지에 넣지 않는다 — 파일 *내용*이고, `ops_status.main()`의 ATTENTION 블록이
자기 메시지는 파일명·id·수로 만든다고 적어 두었다.

**테스트:** `UnreadableLastRunTests` 5건(위조 개행이 ATTENTION 줄을 만들지
못하는 것 포함).

---

### 7. 미실행 branch — 세 자리 (신규, 테스트 공백)

- **Retry Queue 저장 실패의 로그도 실패하는 경우** (`app/runner.py`). C40이 넣은
  `RETRY_QUEUE_SAVE_FAILED` 줄은 자기 `except Exception: pass`로 감싸여 있고
  그 줄들은 한 번도 실행되지 않았다. 특이한 배치가 아니다 —
  `runtime/state/`를 못 쓰게 만드는 조건(디스크 참, ACL 회수, 디렉터리 자리에
  파일)은 보통 `notion_sync.log` append도 같이 막는다. 살아남아야 하는 것:
  **원래 예외**. 테스트 2건(전파 중이 아닐 때/일 때).
- **Agent 겹침 실행의 entrypoint 분기** (`run_agent.py`). `run_once()`가
  SKIPPED_ALREADY_RUNNING을 돌려줄 때의 출력 한 줄과 exit 0이 미실행이었다.
  docs/07 §23이 이름을 대는 배치(AtLogOn 트리거 + 수동 실행)다. **실제 Lock**으로
  돌렸다 — 살아 있는 프로세스가 쥔 Lock을 `_is_process_running()`이 확인하므로
  stale로 판정되지 않는다. exit 0이 요점이다(겹침마다 LastTaskResult가 고장으로
  기록되면 안 된다). 테스트 1건.
- **잘린 Metadata의 나머지 조합** — §2의 테스트가 함께 덮는다.

`app/runner.py`의 `738 -> 746` arc는 **도달 불가**임을 확인했다(측정 도구의 정적
CFG가 만든 것이다): 그 조건이 참이 되려면 예외가 전파 중이어야 하고, 그러면
`finally` 이후 제어는 746이 아니라 예외로 간다. 지우지 않고 여기 기록한다.
`app/runner.py:1521-1522`의 `SKIPPED_NOT_CONFIGURED` elif도 **도달 불가**다 —
호출부가 `if dashboard_client is not None:`으로 감싸고 있어 None은 100줄 아래
`else`가 받는다(그쪽은 실행된다). 1326의 `BACKUP_PENDING`처럼 주석으로
도달 불가라고 적혀 있지는 않다. 코드 삭제는 결정이므로 SKIP.

---

### 8. 결함 주입 / 손상 주입 — 결함 0건으로 끝난 부분 (기록)

찾은 것만큼 **찾지 못한 것**도 측정이다.

- **`os.replace` 임의 지점 실패 200회.** 실패 후 운영자의 다음 예정 실행 2회를
  돌리고, 닫힌 날짜의 KEEP Event가 (a) Daily 파일에 있거나 (b) 어떤 탐지기가
  이름/날짜를 대는지 확인했다. **조용한 유실 1건**(§5), 나머지 전부 복구되거나
  보고됐다.
- **파일 손상 주입 140회**(truncate / 0바이트 / 쓰레기 / 잘못된 UTF-8 / 삭제 /
  같은 이름 디렉터리, `runtime/` 전체에서 임의 선택). 신규 1건(§3). 나머지는
  전부 이미 있는 ATTENTION 줄이 정확히 잡았다 — 특히 **읽을 수 없는 KEEP
  Candidate**는 파일 이름과 "모든 날짜가 멈춘다"는 결과까지 말한다(C44).
  `ops_status.py`는 어느 손상에서도 traceback을 내지 않았다.
- **파이프라인 fuzz 95회**(9종 event_type × label 모양 summary × 두 달 경계 ×
  늦은 도착 × 재실행, 실행당 4회 run). Daily 중복 0, 다른 날짜로 새는 것 0,
  Monthly 중복 id 0, Monthly 누락 0, `.tmp-` 잔여물 0, exit code와 manifest
  불일치 0. **이번에 넣은 탐지기 둘(§3, §13)의 오탐도 30회 전부 0** — 건강한
  파이프라인에서 조용한지는 유실을 잡는지만큼 중요하다(지워지지 않는 경보).
- **Windows 예약 장치 이름**(`CON.json`/`NUL.json`/`PRN.json`/`AUX.json`/
  `COM1.json`/`LPT1.json`/`CONOUT$.json`)을 `safe_event_filename()`이 그대로
  통과시키는 것을 확인하고 **실제로 만들어 봤다** — 이 Windows 11(26200)에서는
  전부 평범한 파일로 만들어지고 읽힌다. `history_id`는 `HIST-` 접두어가 있어
  구조적으로도 닿지 않는다. **결함 아님**, 기록만.

---

### 9. `_relative_files()` — 측정하고 나서 21배 빠르게 (성능, 결과 동일)

§3의 새 검사는 `ops_status.py`가 **매 호출**마다 Backup 범위 목록을 두 번 만들게
한다. 그래서 재 봤다 — 그리고 그 목록 자체가 느렸다.

`rglob("*")`는 **전부** 훑는다. Working Copy는 git 저장소이므로 그 안에서 가장 큰
디렉터리는 `.git/`이고, 그 안의 어떤 파일도 범위에 들어갈 수 없다. 그런데도
항목마다 stat 두 번(`is_symlink()` → `is_file()`)을 내고 버렸다.

`os.scandir` 재귀로 바꾸고, `_is_in_scope()`가 받아들일 수 있는 **최상위 이름
안으로만** 내려간다. 반환 집합은 구성상 동일하다 — 건너뛴 경로는 전부 그 술어가
`parts[0]`에서 이미 거부하는 것들이다.

**실측**(warm, Master + Working Copy 둘 다 — 즉 `sync_to_working_copy()` 1회 또는
`ops_status.py` 1회가 하는 일):

| 규모 | rglob | scandir | |
|---|---|---|---|
| 1년 (730 파일, .git 600개) | 55.4 ms | **2.6 ms** | 21.3x |
| 3년 (2,190 파일) | 82.6 ms | **6.3 ms** | 13.2x |
| 10년 (7,300 파일) | 271.1 ms | **23.1 ms** | 11.7x |

`_print_history()` 안에서 §3의 검사는 **블록의 29.6% → 1.9%**(1년),
**29.2% → 3.2%**(10년)가 된다. Runner의 Backup 단계도 매 실행 같은 만큼 빨라진다.

**동일함을 주장이 아니라 검사로.** 이전 구현을 `_relative_files_by_rglob()`로
남겨 두고(프로덕션에서 아무도 부르지 않는다) `RelativeFilesWalkTests`가 둘을
적대적 트리에 함께 돌려 같은 답을 내는지 본다 — 범위 밖 형제, `.git/`, 중첩,
staging 잔여물, 날짜 이름의 디렉터리, **최상위의 `daily`라는 이름의 파일**
(`_is_in_scope("daily")`는 True다 — 이름만 보고 잘라냈다면 최적화 안에 숨은 동작
변경이 됐을 것이다), 한글 이름, 그리고 만들 수 있는 환경에서는 세 위치의 symlink.
`ops_status._daily_dates()`가 이미 들고 있는 `glob+is_file` → `scandir` 교체
(거기서는 16x)와 같은 모양, 같은 이유다.

---

### 10. 소스 문자열 검사 하나를 행동 검사로 바꿨다 (A-19 유지)

§9가 그것을 드러냈다. `BackupJunctionTraversalTests::test_the_scan_follows_links_by_default`는
`_relative_files()`의 **소스**에 `path.is_file()`이 있고 `follow_symlinks=False`가
없음을 단언하고 있었다. 새 walk에서는 둘 다 반대가 됐고 — **junction traversal은
정확히 그대로 미해결이다.** 동작이 그대로인데 움직이는 문자열은 틀린 것을 재고
있었다.

같은 성질을 두 술어를 **실제 junction에 대고 재서** 고정했다:

    entry.is_symlink()                    False   (NTFS reparse point)
    entry.is_dir(follow_symlinks=False)   True    (그래서 내려간다)
    os.path.isjunction(...)               True

즉 링크 거부도, no-follow 플래그도 거부할 것이 없다. A-19의 결정(재지정된
`daily/`를 백업해야 하는가)은 그대로 열려 있고, 이 클래스의 나머지 6건
(end-to-end 결과 포함)은 손대지 않았다.

---

### 11. PROJECTS Database에는 그 검사가 없었다 (신규, 테스트 공백)

`DashboardSchemaMappingTests`는 OPS_RUNS에 대해 세 가지를 고정한다 — 쓰는 쪽이
스키마에 없는 Property를 내보내지 않는가, 스키마의 모든 Property가 채워지는가,
**타입이 일치하는가**. 매 Event마다 쓰이는 **PROJECTS** 쪽에는 같은 검사가
하나도 없었다.

`notion/bootstrap.TARGET_PROPERTIES`(1회 설정이 만드는 것)와
`properties.build_create_properties()` / `build_update_properties()`
(`ExecutionPlanSync`가 매 sync마다 보내는 것)를 붙들고 있는 것이 없었다는 뜻이다.
한쪽에서 이름이 바뀌거나 `_type_specific_properties()`가 한 분기에서만 내보내는
Property가 생기면 **실제 API의 400**이 되는데, 이 저장소 자신의
`TestDoubleFidelityTests`가 `InMemoryNotionTransport`는 "스키마에 없는 Property"와
"잘못된 타입"을 **둘 다 받아들인다**고 기록하고 있다 — 즉 기존 Notion 테스트로는
잡을 수 없는 종류다.

반경도 OPS_RUNS보다 크다. 거기서는 실행당 Dashboard 행 하나를 잃지만, 여기서는
Operational Projection 전체를 잃는다: docs/04 §38이 Event를 버리지 않고 보존하므로
Event들이 `notion_retry_queue.json`에 쌓이고 **재시도해도 스키마 불일치는 바뀌지
않으므로 영원히 같은 자리에서 실패한다.**

현재 상태는 **일치**한다(실측: 양방향 차집합 공집합, 타입 불일치 0). 고친 것은
그것을 붙들어 두는 것이다 — `ProjectsSchemaMappingTests` 5건 / subtest 126건.
샘플 하나가 아니라 **event_type 8종 전부**를 걷는다: payload가 고정이 아니고
(`Blocker`·`Current Milestone`·`Completed Date`가 서로 다른 분기에서 나온다),
한 분기에서만 나오는 Property가 정확히 단일 샘플이 놓칠 것이기 때문이다.
Title 이름이 bootstrap이 rename하는 그 이름인지와, §29-30/§62 가드가 읽는 두
Property가 스키마에 있는지도 함께 고정했다(없으면 가드가 실패하지 않고 **조용히
꺼진다**).

---

### 12. 살아 있는 생산자와의 경쟁 — 한 번도 재 본 적 없는 축 (신규, 테스트 공백)

`TransportIntakeConcurrencySafetyTests`는 **소비자 4개**를 이미 존재하는 파일들
위에서 경쟁시킨다. 반대 축 — **쓰는 쪽이 아직 쓰고 있는 디렉터리를 비우는 것** —
은 없었다. 그리고 그쪽이 원자적 쓰기 규율이 존재하는 이유다.

세 가드가 함께 버텨야 하는데 셋 다 살아 있는 writer에 대고 돌아 본 적이 없었다:
`.tmp-…json` 건너뛰기, `_is_parseable_json()`, `os.replace()` 커밋.

`stable_after_seconds=0`으로 돌린다 — 가장 공격적인 설정이다. 안정화 창이야말로
찢어진 읽기를 지연 뒤에 숨겨 주는 것이므로, 끄고 나면 원자적 쓰기 규율만 남는다.

**실측**(별도 OS 프로세스의 진짜 `OneDriveTransport` 4개 × 60건 = 240건):
240 accepted / **중복 0 / 거부 0 / 실패 0 / 찢어짐 0**, sync·incoming 잔여 0.
스위트에는 2 × 20으로 넣었다(0.55 s, 12회 연속 통과).

테스트가 **공허해지지 않도록** 한 가지를 더 단언한다: 최소 한 번의 drain이
sender가 아직 살아 있는 동안 파일을 옮겼는가. 그 줄이 없으면 이 클래스는
조용히 "아무도 건드리지 않는 폴더 비우기"로 퇴화하고, 그건 이 파일의 나머지가
이미 덮고 있다.

---

### 13. Monthly가 자기 원본보다 뒤처져도 아무도 보지 않았다 (신규, **데이터 정합성**)

세 고리 중 마지막, 그리고 유일하게 **파일을 건너는** 것:

    Daily 파일(원본)  ->  Consolidated Items(실행이 본 것)  ->  렌더링된 항목

`_monthly_counts_more_than_it_shows()`는 두 번째와 세 번째를 **한 파일 안에서**
비교한다. 첫 번째를 무엇과도 비교하는 것이 없었다 — 그런데 docs/09 §12-13이
Monthly를 Daily에서 **전부** 파생된다고 못 박으므로, 그것이 정작 중요한 비교다.

**실측(실 Runner). 손상이 아니라 명세 둘이 허용하는 편집이면 된다.** docs/06 §57과
docs/11 §71 둘 다 COO의 Daily 손편집을 허용한다. 7월을 08-03에 통합한 뒤
`2026-07-30.md`에 항목 하나를 손으로 더했을 때:

| 실행 | exit | Monthly에 있음 |
|---|---|---|
| 08-04 | 0 | 아니오 |
| 08-05 | 0 | 아니오 |

ATTENTION은 그것에 대해 아무 말도 하지 않았다. 그리고 **어떤 실행도 그 달을 다시
만들지 않는다** — `pending_months()`는 마지막 통합 **다음** 달부터 시작하고, 닫힌
달을 다시 여는 것은 Late Event가 바꾼 날짜에 대한 `mark_month_dirty()`뿐이다.
Late Event 경로는 이미 옳고 여기서 건드리지 않았다(테스트가 실 `consolidate_month()`로
그것을 고정한다). 돌아올 길이 없는 것은 **사람의 편집**이다.

**고친 것 (탐지, 결정 없음):** `_monthly_lags_its_daily_source()`. Daily 쪽은
`monthly.parser.read_daily_document()` — Monthly가 실제로 통합에 쓰는 그 파서 — 로
읽으므로 "항목"의 뜻이 양쪽에서 같고, event_id **집합**을 쓰는 것이 §59의
"한 id 한 항목"을 구성상 그대로 만든다. Monthly 쪽은 형제 검사와 같은 방식으로
(`summary_line_indices()` 적용) 자기 `- Event ID:` 줄을 읽는다. 방향은 하나뿐이고
안전한 쪽이다: 요약이 `- Event ID: X` 모양이면 Monthly 쪽 집합만 커지므로 **발견을
숨길 수는 있어도 없는 것을 만들 수는 없다.**

**비용은 재고 넣었다.** Monthly가 자기 원본보다 뒤처지려면 Daily가 그 뒤에 바뀌어야
하므로, 그 달의 Daily가 전부 Monthly보다 오래됐으면 건너뛴다. mtime은 **prefilter이지
판정이 아니다** — 통과한 달은 전부 파일을 읽어서 결정한다(`_content_differs()`가
판정에 mtime을 거부하며 긋는 그 구분이다). 처음 구현은 날짜마다 `stat()`을 냈고
10년에 63.6 ms였다; 디렉터리 한 번 읽기로 바꿔 **14.1 ms**가 됐다.

| | 1년 | 10년 |
|---|---|---|
| 건강한 트리(아무 달도 검사 안 함) | 1.4 ms | 14.1 ms |
| 모든 달 검사(warm) | 24.5 ms | 251.6 ms |

mtime을 전부 새로 쓰는 복원은 한 번의 전수 통과를 치르고 아무것도 보고하지 않는다.
dirty로 표시된 달은 건너뛴다 — 다음 실행이 다시 만들 것을 경보로 띄우는 것이야말로
이 파일이 계속 경계하는 "지워지지 않는 경보"다.

**테스트:** `MonthlyLagsItsDailySourceTests` 12건. 조용해야 하는 쪽을 전부 고정했다 —
일치하는 달, prefilter가 거르는 달, mtime을 전부 새로 쓴 복원, dirty 달, 통합되지 않은
달, 불릿 모양 요약, Daily에 없는 id를 든 Monthly, 그리고 **실 `consolidate_month()`로
돌린 Late Event 경로**. AGENT.md §6의 HISTORY 블록 설명에 추가했다.

---

### 14. 남는 것 (SKIP 사유)

- **E-17의 재시도** — §5가 진입 경로를 하나 더 찾았지만 고치는 방법은 그대로
  둘 중 하나(실패 날짜 영속화 / 정합성 패스)이고 둘 다 메커니즘 추가다.
- **`app/runner.py:1521-1522` 삭제** — 도달 불가 코드의 제거는 결정이다.
- **`scheduler/lock.py`의 POSIX 분기**(67, 100-108) — 이 프로젝트의 대상 OS에서
  실행되지 않는다. `sys.platform`과 `os.kill`을 함께 가짜로 바꾸면 테스트할 수
  있지만, `os.kill(pid, 0)`은 Windows에서 **프로세스를 종료시킨다** — 그 위험을
  테스트 스위트에 들이는 것과 대상 OS에서 절대 돌지 않는 코드를 덮는 이득을
  맞바꾸는 판단이 필요하다.
- **`reason`을 `ops_status.py`가 인쇄할 것인가** — §3이 그 결과(삭제된 파일
  이름이 상태 뷰에 닿지 않는다)를 다른 경로로 우회했다. 인쇄 자체는
  "ATTENTION 메시지는 파일 *내용*으로 만들지 않는다"는 그 파일의 규칙과
  부딪히므로 결정이다.

---

## C44. Read Boundary Sprint — 디스크에서 읽는 모든 것에 같은 질문을

C43은 Event 경계에서 "명세가 `string`이라 적었는데 validator가 강제하지 않는다"를
찾았다. 그 눈을 **디스크에서 무언가를 읽어 객체로 만드는 모든 자리**에 댔다.

---

### 1. History Candidate — 같은 결함, 한 층 안쪽 (신규, **P0**)

docs/11 §71은 COO가 `runtime/history_candidates/`의 파일을 손으로 고치는 것을
**명시적으로 허용한다.** `HistoryCandidate.from_dict()`는 파일이 말하는 대로 읽고
타입을 검사하지 않으며, 없는 키는 맨 `KeyError`가 된다.

**실측 — 손으로 고친 KEEP Candidate 하나 + 평범한 것 하나, 실제 Runner:**

    summary=12345     daily FAILED "sequence item 2: expected str
                      instance, int found" -> Daily 0개, exit 2
    project_id=7      daily FAILED -> Daily 0개, exit 2
    timestamp=5       daily FAILED -> Daily 0개, exit 2
    summary 키 삭제    daily FAILED (KeyError) -> Daily 0개, exit 2

넷 다 **무고한 Candidate도 함께** 렌더링되지 않았다. Scheduler가 keep 인덱스를
**배치당 한 번** 만들기 때문이고(A-7/BUG-38이 기록한 바로 그 반경), 파일은 `keep/`에
남으므로 **다음 실행도 같은 자리에서 죽는다.**

**운영자가 받은 것:** manifest `reason`과 `daily_late_update.log`에
`sequence item 2: expected str instance, int found`. 파일도, 필드도, Candidate가
관련됐다는 사실조차 없다. 그리고 `ops_status.py`는 **`Candidate 정합성 : OK`**라고
했다 — 자기 reader가 `timestamp`와 `event_id`만 보기 때문이다.

**고친 것 (결정 없이 가능한 부분).**

`history.result.candidate_errors()` — `events.validate_event()`와 같은 모양의 순수
함수. `FileHistoryRepository._candidate_from()`이 그것으로 거부하고
`HistoryCandidateError(ValueError)`를 던지며 **파일과 필드를 이름으로 댄다**
(`monthly.parser.read_daily_document`와 같은 패턴, `ValueError` 서브클래스라 기존
`except ValueError` 호출자는 그대로 잡는다). `ops_status._read_keep_candidates()`가
**같은 함수**를 쓴다 — 두 번째 의견을 만들지 않는다는 C28의 규칙이고, 그래야 상태
뷰와 파이프라인이 같은 파일에 대해 다른 답을 하지 않는다.

    고친 뒤 reason : unusable history candidate: …\keep\HIST-EV-BAD.json
                     (summary must be a string)
    고친 뒤 상태 뷰: 읽을 수 없는 Candidate: 1
                     ATTENTION "…이 파일 하나 때문에 **모든 날짜의** Daily
                     History 생성이 멈춘다 … HIST-EV-BAD.json"

**무엇이 실패하는지는 바꾸지 않았다.** 렌더러가 살아남는 세 모양(`role`·`category`·
`evidence` 타입 오류)은 **일부러** 통과시킨다 — 거부하면 살아남을 수 있는 손상을
멈춘 파이프라인으로 바꾸게 되고, 그것이 이 수정이 줄이려는 피해다. `category` 쪽은
C43의 `_daily_counts_more_than_it_shows()`가 이미 보고하고, `evidence` 쪽은 기존
특성화 테스트와 이 BACKLOG에 남는다. 타임스탬프가 **문자열인데 ISO가 아닌** 경우도
일부러 뺐다 — A-7이 이미 기록했고 인덱스 빌드가 이미 `isoformat`을 말하며 거기에
테스트가 있다. 넣으면 `list()`가 예전에 돌려주던 Candidate를 거부하게 되어 계약이
바뀐다.

비용은 잴 것도 없다: Candidate 1,000개에 `list()` 52.2 ms, 이 함수 0.5 ms.

**Evidence:** `tests/test_runner_failure_paths.py::
AnUnusableCandidateNamesItselfTests` 6건(9가지 blocking 모양 / `get()`도 이름을 댄다 /
무고한 이웃은 여전히 함께 잃는다 = 심각도 불변 / 살아남는 모양은 통과 / 술어가
공유되는지 소스 대조), `tests/test_observability.py::
ATypeBrokenCandidateReachesAttentionTests` 4건. BUG-38의 기존 특성화 3건은 예외
클래스와 메시지가 좋아진 만큼 **강화**했다(여전히 raise하고, 이제 파일 이름을 대며,
`__cause__`에 원래 예외가 남는다).

---

### 2. 손상된 Run Manifest 하나가 상태 뷰를 통째로 죽였다 (신규)

`read_summary()`는 스스로 "세 enum만 검증한다"고 적어 두었고, `metrics`는
`c.get("metrics", {})` — JSON이 가진 무엇이든 된다. 렌더러는 그 사실을 알고 이미
한 번 방어했다(키와 값 전부 `one_line()`, C38). **컨테이너가 mapping이라는 것만
가정했다.**

**실측** — `metrics`가 문자열인 manifest:

    AttributeError: 'str' object has no attribute 'items'
    _print_last_run() 밖으로, main() 밖으로
    운영자는 상태 대신 traceback을 받는다

이 파일 자신의 계약("증거 일부가 손상돼도 답을 내야 한다")과 docs/10 §46("손상된
State도 보고 대상이다")을 동시에 깨고, 하필 **복구 직후** — 복원되거나 손으로 고친
manifest가 있을 수 있는 바로 그때 — 에 일어난다.

건너뛰지 않고 **보고한다**: 나머지 manifest는 그대로 렌더링되고(어느 단계가 어떻게
실패했는지가 이 블록의 대부분이다), 손상은 자기 줄과 ATTENTION 항목을 얻는다.
`artifact_refs`가 문자열인 경우는 특성화만 했다 — `read_summary()`가 이미
`tuple()`로 글자 단위로 펼친 뒤라 렌더러 쪽에서는 진짜 tuple과 구별할 수 없고,
비용은 `a, b, c`로 찍히는 것뿐이다.

**Evidence:** `tests/test_observability.py::
ADamagedManifestDoesNotKillTheStatusViewTests` 6건.

---

### 3. 나머지 읽기 경계 — 전수 확인, 결함 0건 (기록)

디스크에서 읽어 객체를 만드는 모든 loader에 잘못된 타입을 먹여 봤다.

| 경계 | 결과 |
|---|---|
| `scheduler/state.py` | 객체 모양·필드 타입·날짜 파싱 전부 검사, 이름 붙은 오류 |
| `backup/state.py` | timestamp·commit·status 전부 검사 |
| `monthly/state.py` | `dirty_months`가 리스트인지, 원소가 문자열인지까지 |
| `agent/state.py` | 세 필드 전부 |
| `notion/retry_queue.py` | 객체 모양과 entry 모양 |
| `notion/dashboard_pending.py` | 같음 |
| `collector/state.py` | `processed_event_ids`가 문자열 리스트인지 |

그리고 **`tuple(json값)`이 문자열을 글자 단위로 펼치는 계열** 5곳을 훑었다:
`events/schema.py`는 `validate_event()`가 앞에서 막고(실측),
`history/result.py`와 `runsummary.py`는 위에서 특성화했으며,
`backup/log.py`의 `from_dict`/`from_json` 둘은 **프로덕션 호출자가 없다**
(docs/08 §68의 Backup Log는 BUG-39 이후 Run Manifest가 대신하고 있다).

---

### 4. C27 §8을 실행으로 재검증 — 기록이 정확했다 (변경 없음)

`is_incomplete_write()`를 6곳에 적용하면서 **Collector만 일부러 제외한** 경계다.
그 판단이 지금도 맞는지 다시 돌렸다 — 특히 "Event는 진짜이고 Company History에
정상 반영된다. 파일명만 staging 이름이다"라는 문장을.

**실측** — `incoming/`에 평범한 Event 하나와 완성된 staging 파일(`close()`와
`os.replace()` 사이의 창) 하나:

    Collector    accepted=2   `.tmp-xyz.json`이 `processed/`로 이동
    COMPANY 뷰   DESKTOP_2 events=1  last=2026-08-05T11:00:00+09:00

즉 **staging 이름으로 들어간 Event도 COMPANY 뷰가 정상적으로 센다.** 처음에는
`app/desktop_activity`의 `_is_incomplete_write` 필터가 `processed/`에도 걸려
그 Event가 뷰에서 영영 안 보일 것이라고 의심했는데, **재 보니 아니었다** — 그
필터는 `transport/`·`incoming/`·`rejected/` 쪽이고 `processed/`는 세는 쪽이다.
기록이 맞고 의심이 틀렸다.

잘린 staging 파일이 `rejected/`로 가는 쪽도 재현했고, 그것을 "거부된 Event가
아니다"라고 정확히 말하는 ATTENTION 두 줄이 이미 있다(C27 §8이 만든 것).

남은 좁은 창 하나는 기록해 둔다: 그 순간에 Collector가 staging 파일을 가져가면
**쓰던 쪽의 `os.replace()`가 `FileNotFoundError`로 실패한다**(실측). Event는
수집됐고 유실이 아니지만 쓴 쪽은 실패로 안다 — Agent 경로에서는 그 날짜가
FAILED가 되고 다음 실행이 같은 `event_id`로 다시 보내 중복으로 걸러진다. 창은
`with` 블록이 닫히고 `os.replace`가 도는 사이(마이크로초)이며, 그 전에는 Windows가
열린 핸들 때문에 이동 자체를 막는다. **경계 자체는 C27 §8이 이미 고정해 두었다**
(`IncompleteWriteInvariantTests::test_the_one_consumer_this_does_not_cover_and_why`).

---

## C43. Re-verification Sprint — 기록이 아니라 실행으로 다시 본다

C42가 닫았다고 적은 것과 열어 둔 것을 **전부 다시 돌려** 확인했다. 여섯 항목 중
넷은 기록이 정확했고, 둘에서 새 결함이 나왔다.

---

### 1. BUG-11/27 — 기록되지 않은, 그리고 가장 비싼 결과 (신규, **데이터 유실**)

기록은 개행이 든 필드의 결과로 **구조 위조**(존재한 적 없는 Event가 History에 한 줄로
선다)와 **Monthly 섹션 유실**(`## ` 제목이 Section을 닫아 무고한 Event까지 사라진다)을
적고 있다. 실행해 보니 셋째가 있고, 그것이 가장 비싸다.

**측정** — 평범한 KEEP Candidate 하나, `summary = "did work\n- Event ID: VICTIM"`:

    existing_event_ids(day)              {'EVT-1', 'VICTIM'}
    select_late_candidates(day, VICTIM)  ()      <- 영원히 추가되지 않는다
    _kept_but_not_rendered(...)          ()      <- 탐지기도 clean이라고 한다
    - Event Count: 1                             <- 그런데 id는 2개다

즉 **그 id로 나중에 도착하는 진짜 Event가 Company History에 영영 못 들어간다.**
C42가 `## Summary` 경로에서 닫은 것과 **같은 §38 메커니즘**인데, 이쪽은 요약이
불릿이라서가 아니라 요약에 **개행**이 있어서 진짜 두 번째 줄이 생기는 경우다 —
그래서 `summary_line_indices()`도 `item_block_bounds()`도 막을 수 없다.

그리고 그 유실을 보라고 만든 `_kept_but_not_rendered()`가 **같은 위조에 함께
속는다** — 찾는 id가 파일에 실제로 들어 있기 때문이다. 두 방어가 하나의 위조로
동시에 무력화된다.

**노림수가 될 수 있는가.** 프로덕션 `event_id`는 uuid5(namespace|source|date|
signal 파일명)이고 namespace는 소스에 있으므로 **예측 가능하다.** 다른 Desktop의
Event id를 계산해 자기 요약에 적으면 그 Event를 막을 수 있다. 코드베이스가 이미
쓰는 표현대로 Event ID spoofing이며, 여기서는 대상이 *미래의* Event다.

**escape는 여전히 SKIP** — docs/06 렌더링 계약(BUG-11/27)이다. 아래 2번이 결정
없이 할 수 있었던 부분이다.

---

### 2. `_daily_counts_more_than_it_shows()` — Monthly에는 있고 Daily에는 없던 검사

Daily 파일은 자기 총계(`- Event Count:`)와 Event ID를 **둘 다** 들고 있다. 렌더러가
전자를 후보 목록에서, 후자를 같은 후보들에서 쓰므로 **생성 시점에는 반드시
일치한다.** 그래서 한 파일 안에서 결정 가능하다 — 창도 없고, 다른 무엇도 참조하지
않으며, 비교는 §38의 가드가 쓰는 `existing_event_ids()`를 **그대로 재사용**한다
(두 번째 의견을 만들지 않는다는 C28의 규칙).

처음으로 운영자에게 닿는 실제 유실 셋:

| 방향 | 무엇 | 이전 상태 |
|---|---|---|
| id가 더 많다 | 위조된 `- Event ID:` 줄 (1번) | 두 탐지기가 함께 속아 **완전 침묵** |
| id가 더 적다 | `category=None` 후보가 어느 Section에도 못 들어간다 | 특성화만 있고 보고 없음 |
| id가 더 적다 | 손으로 지운 항목 블록 (docs/06 §57) | 보고 없음 |

**양방향을 다 보고한다** — Monthly 형제는 한 방향만 본다(거기서 반대 방향은 항목을
*추가한* 손편집이라 유실이 아니다). 여기서 같은 부등호는 1번의 위조이고 둘 중 더
비싼 쪽이므로, 가끔의 손편집 보고를 피하려고 그것을 끄는 것은 방향이 거꾸로다.

**오탐 0 (실측).** 빈 날, 평범한 3건, late append 1·2회, 네 Category 전부, 빈
`event_id`(A-15), evidence 있는 날 — 전부 clean.

**비용 (실측, warm, 하루 5건).** 365일 31 ms / 2,920일(8년) 231 ms, 그중 읽기
150 ms·파싱 50 ms. Daily 파일을 **전부** 읽는 첫 검사다(`_kept_but_not_rendered()`는
후보가 있는 날만 읽는다). 2단 fast path는 Monthly 형제와 **같은 이유로 거부했다** —
싼 줄 세기와 정밀 집합은 양방향으로 어긋나므로(중복 id는 집합을 줄이고 요약 모양
줄은 스캔을 늘린다) fast path가 정밀 pass라면 보고했을 파일에 대해 `Event Count`와
일치해 버린다. 8년 뒤의 50 ms를 사려고 이 검사가 닫으려는 침묵을 다시 여는 셈이다.

**Evidence:** `tests/test_observability.py::DailyCountsMoreThanItShowsTests` 10건
(위조 / 두 탐지기가 정말 속는다는 전제 / category 없음 / 손편집 / 건강한 7가지 모양
전부 clean / 숫자 없는 파일 / 읽을 수 없는 파일 / staging 파일 / 화면과 ATTENTION /
건강하면 침묵).

---

### 3. E-23 — 얼마나 오래 갈라지는가 (측정으로 좁힘)

E-23 항목에 직접 기록했다. 요약: 실 Agent → 실 Runner → Sync로 끝까지 돌린 결과
**그 프로젝트에 대한 다음 Event 하나가 View를 완전히 되돌린다.** docs/14 §1이
Notion을 Current State View로 정의하므로 수렴이 정상이며, View에 영영 없는 것은
"그 순간의 두 번째 Event도 적용됐다"는 중간 상태뿐이다(그 log는 Company History가
지킨다). 결정은 열려 있고, 바뀐 것은 시급도다.
**Evidence:** `tests/test_notion_sync.py::TheSameInstantDivergenceHealsTests` 5건.

---

### 4. Branch coverage 재측정 — 1,300 지점, 한쪽만 다닌 335건, 진짜 공백 2건

`sys.monitoring`의 BRANCH 이벤트로 다시 쟀다(C41은 1,066/279였다 — 코드가 자랐다).
전수로 읽는 대신 **위험도 순**으로 열었고, 두 개가 진짜였다.

**(a) Backup의 PENDING 재시도가 인증 실패로 끝나는 경우.** `run_once()`는 docs/08
§19 대 §21/§62의 분류를 **두 번** 쓴다 — 7단계(변경이 있는 실행)와 6단계(변경이
없고 State가 PENDING이라 push만 재시도하는 실행). 앞쪽은 양쪽 다 테스트가 있고,
뒤쪽은 **transient 쪽만** 돌고 있었다. 둘이 어긋나면 정확히 §62가 금지하는 것이
생긴다 — 성공할 수 없는 push를 매 예약 실행마다 영원히 재시도하면서 State는
"다음 실행이 고친다"고 말한다. **코드는 옳았고 테스트가 없었다.**
**Evidence:** `tests/test_backup_runner.py::PendingRetryClassifiesTheSameWayTests`
5건(전제·인증·transient·두 자리가 같은 규칙을 쓰는지 소스 대조).

**(b) `category=None`인 late item 렌더링.** 결함은 아니고 **비대칭이 기록될 값이
있었다**: Daily Close로 들어가면 요약만 남고 Event ID·Owner가 전부 사라지는데,
늦게 도착하면 `## Late Events`가 네 Category Section이 아니어서 **블록이 통째로
살아남는다.** Monthly는 `- Category:`가 없어 버리지만 **세고**
(`unconsolidated=1` → `MONTHLY_UNCONSOLIDATED`), 다음 실행이 다시 추가하지도 않는다.
**Evidence:** `tests/test_daily_late_events.py::ACategoryLessLateItemTests` 6건.

---

### 5. `changed_files`는 상태에 따라 **셋**이 다르다 (특성화)

C42는 SUCCESS 경로를 git의 staged 목록으로 고쳤다. add/commit 이전에 멈추는 두
경로(삭제 게이트, 대량 수정 가드)는 여전히 `sync_result`를 쓴다. 다시 재 보니 차이가
셋이다 — **출처**(git / sync), **의미**(커밋이 싣는 것 / sync가 준비했는데 못 나간
것), 그리고 **경로 구분자**(`daily/2026-08-01.md` 대 `daily\2026-08-01.md`).

셋 다 각자의 경로에서는 옳다. 커밋이 없는 경로에서 git의 목록은 만들어진 적 없는
staging area를 설명할 뿐이고, sync의 목록은 "무엇이 백업되지 못했나"에 답한다.
고치지 않고 **짝으로 고정**했다(정규화하면 차이를 숨기는 테스트가 된다).
**Evidence:** `tests/test_backup_git_ops.py::
ChangedFilesMeansTheSameThingAsTheStatusTests` 4건.

---

### 6. 실행으로 재확인했고 기록이 정확했던 것 (변경 없음)

| 항목 | 실행 결과 |
|---|---|
| E-21 | Working Copy에만 둔 `.env`·`notes/id_rsa`·`scratch.log` → 셋 다 원격 도달, 탐지는 **현재 WC와 원격 history 두 통로**로 보고 |
| E-21 (오탐) | docs/08 §28의 `.gitignore`가 있으면 `.env`·`*.log`는 커밋되지 않고 **보고도 되지 않는다** |
| 게이트 | 같은 이름이 Local Master에 있으면 `BACKUP_FAILED`, 원격은 비어 있음 |
| E-24 | `daily/ID_RSA`·`server.PEM` → 게이트는 `()`, 탐지기는 **둘 다 이름을 댄다** |
| E-25 | 삭제 게이트 → `BACKUP_FAILED`/CRITICAL/PERMANENT, reason이 파일을 대고, metric `deleted_files=1`, **Dashboard `Deleted Files: 1`**(C42), manifest exit 2 |
| A-21 | `Consolidated Items: 2`인데 EVT-2도 그 요약도 파일 어디에도 없음 |

---

---

### 7. 아무도 넘기지 않는 keyword 인자 2개 (신규, 기술 부채)

`src/`의 keyword-only 인자 **275개**를 AST로 걷고, `src/` + 루트 진입점 + 테스트
전체의 호출부와 대조했다.

    프로덕션 호출부가 한 번도 넘기지 않는 것        17
    프로덕션도 테스트도 넘기지 않는 것 (완전 미실행)  2

17개 중 열 개는 `app/runner.run_once()`의 경로 인자들 — **C34 §3의 기록이
정확했음을 독립적으로 확인**해 준다(운영은 19개 중 3개만 넘긴다). 나머지는
`InMemoryNotionTransport`의 테스트 전용 손잡이들이다.

**완전 미실행 2개는 둘 다 판단이 걸린 함수에 있다.**

| 인자 | 무엇을 정하는가 |
|---|---|
| `monthly.coverage.check_coverage(today=)` | 한 달이 **가져야 할 날짜 집합**을 뒤에서 자른다 — `consolidate_month()`가 "통합해도 되는가"를 판정할 때 디스크와 비교하는 바로 그 집합 |
| `agent.status.needs_attention(stale_after_days=)` | Company History를 **생산하는** Desktop을 언제 침묵으로 부를지 |

지우지 않았다 — 문서화된 capability를 삭제하는 것은 결정이고, 이 저장소는 이미
같은 경우를 하나 기록으로 들고 있다(`remove_pending()`, B-7). 대신 **돌렸다**:
동작함이 확인됐고, 첫 실제 호출자는 놀람 대신 테스트를 물려받는다. `today=`
쪽에는 "현재 달을 막는 것은 이 인자가 아니라 `pending_months()`의 달력
산술(docs/09 §49)"이라는 대조도 함께 넣었다.

**그 과정에서 문서와 코드의 작은 어긋남 하나(SKIP, 정책).**
`stale_after_days`의 docstring은 *"2 rather than 1 because a machine that is
simply off for a weekend is normal … and a status view that cries wolf every
Monday gets ignored"*라고 적는다. 그런데 비교는 `elapsed >= stale_after_days`이고
금요일 실행 → 월요일 확인은 **3일**이므로, **기본값 2로는 월요일 경보를 막지
못한다**(막으려면 4 이상이어야 한다). 값을 바꾸는 것은 정책 결정이라 SKIP하고,
실측한 경계(1일 조용, 2일 보고)를 테스트에 못박았다.
**Evidence:** `tests/test_monthly_history.py::CoverageCanBeTrimmedAtTheBackTests`
6건, `tests/test_agent.py::StalenessThresholdIsAKnobNobodyTurnsTests` 5건
(둘 다 "아무 호출자도 없다"는 전제를 AST로 확인하는 테스트를 포함한다).

---

### 8. 실행으로 고정한 것 — 전달 정합성 / 복구 / Dashboard 교차검증

C43이 손으로 돌려 본 것 중 **성질로 남을 만한 셋**을 영구 테스트로 만들었다.
전부 기존에 없던 종류다.

**(a) Event → Candidate → Daily → Monthly, 단계마다 세기.** 각 단계에는 자기
suite가 있지만 **이음매를 가로지르는 질문**("내가 보낸 Event가 Company History와
Monthly에 있는가")은 아무도 하지 않았다 — 그리고 이 저장소가 찾아낸 유실은 전부
이음매에 있었다. Event 9건(세 Type, 세 날짜) → accepted 9 → KEEP 6 / REVIEW 3 →
Daily에 KEEP 6 전부·`unconsolidated=0` → 8월을 닫고 통합 → Monthly에 **정확히 6**,
`Consolidated Items: 6`, 양방향 차집합 0. 두 상시 탐지기도 조용하다.
**Evidence:** `NothingIsLostBetweenEventAndMonthlyTests` 8건.

**(b) 디스크 전체 유실 → 복구 → 첫 실행을, 운영 진입점으로.** C39는
`run_once()`로 쟀고 그건 복구된 Desktop 4가 실행하는 명령이 아니다. 저장소를
복사한 사본에서 `runtime/`을 통째로 지우고 원격에서 clone해 되돌린 뒤
`python run_company_ops.py`를 돌렸다: 복구된 **17개 파일 전부 바이트 동일**,
사라진 것 없음, 새로 생긴 빈 날 없음, watermark는 마지막 복구일로 전진, 원격 불변,
`generated=0 reused=17`, exit 0. 그 다음 실행도 0.
**Evidence:** `RestoreThroughTheProductionEntrypointTests` 9건.

**(c) OPS_RUNS 행의 모든 숫자를 디스크와 대조.** 다른 Dashboard 테스트는 손으로
만든 result 객체에서 열 하나씩을 본다. docs/14 §1이 Notion을 **View, 절대 Source가
아님**으로 정의하므로 정작 물어야 할 것은 "행이 Source와 맞는가"인데, 그 대조는
손으로만 있었다. 일부러 지저분한 실행 하나(큐에 남는 503, 중복, 파싱 불가, staging
잔여물, 읽을 수 없는 파일)에 대해 열두 열을 **각각 반대편(파일시스템 / 원격 /
manifest / Notion 쪽 페이지)**과 비교한다.
**Evidence:** `EveryDashboardNumberMatchesDiskTests` 15건.

**(d) Batch Save를 소스가 아니라 쓰기 횟수로.** 기존 가드는
`save_retry_queue(`가 소스에 **한 번** 나오는지를 봤다 — 헬퍼 안에 두 번째 저장이
생기거나 루프가 그 하나를 Event마다 부르면 통과한다. 실제 실행의 쓰기 횟수를
센다: Notion이 전부 죽은 상태에서 Event 8건 → **쓰기 1회, 8건 전부 디스크에**,
정상 실행 → **0회**, 다음 실행의 drain → 1회에 0건. 소스 가드는 남겼다(n=1일 때
per-file 헬퍼 사용은 밖에서 안 보인다).
**Evidence:** `TheQueueIsWrittenOncePerRunTests` 4건.

---

### 9. docs/02가 `string`이라고 적은 필드를 validator가 강제하지 않았다 (신규, **P0 — Company History 영구 정지**)

`docs/02` §4의 필드 표는 `event_id` · `project_id` · `summary`를 **`string`**으로
선언한다. `validate_event()`는 그 셋만 **존재 여부만** 검사했다.

다른 타입 필드는 전부 덮여 있었다 — `timestamp`는 `_timestamp_error()`,
`milestone`/`blocker`/`evidence`/`history_candidate`는 직접,
`source`/`role`/`event_type`/`status`는 문자열 frozenset 멤버십이 암묵적으로
(문자열이 아니면 들어 있을 수 없다). **정확히 세 개만 빠져 있었다.**

**신뢰 경계다.** Event는 다른 Desktop에서 쓰인 JSON으로 OneDrive를 건너오므로
타입은 그 파일이 말하는 대로다. Signal 계층도 막지 않는다 —
`agent.signals.parse_signal()`은 필드 **집합**을 검사하지 타입을 보지 않는다
(`history_candidate`와 `timestamp`만 예외).

**실측 — 조작된 Event 1건이 평범한 Event 1건 옆에 도착한 실제 Runner 실행:**

    summary=12345     Collector ACCEPTED -> KEEP Candidate가 keep/에 저장됨
                      -> daily FAILED "sequence item 2: expected str
                         instance, int found"
                      -> **Daily 파일 0개, exit 2**
    project_id=7      ACCEPTED -> notion_sync와 daily가 **둘 다** FAILED
                      ("'int' object has no attribute 'replace'")
                      -> **Daily 파일 0개, exit 2**
    event_id=99       **TypeError가 run_once() 밖으로 탈출** —
                      `collector/state.py`의 sorted()가 int와 str을 비교한다.
                      3단계에서 실행이 죽는다.

**앞의 둘이 더 나쁘다.** Candidate가 이미 `keep/`에 쓰였으므로 **이후 모든 실행이
같은 자리에서 같은 이유로 죽는다** — 사람이 파일을 지우기 전까지 Company History가
전진하지 않는다. 그리고 그 실행의 **무고한 Event들도 함께** 렌더링되지 않는다.
한 Desktop의 Event 하나가 파이프라인 전체를 세운다.

**정책 결정이 아니다.** 명세가 이미 `string`이라고 적었고 validator만 그것을
강제하지 않고 있었다. 검사 3줄을 더했다.

**고친 뒤 (같은 실행, 다섯 가지 조작 전부):**

    rejected/    crafted.json      <- docs/03 §7이 정한 자리
    processed/   EV-OK.json        <- 무고한 Event는 그대로
    Daily        2개 생성
    manifest     SUCCESS / exit 0  <- 잘못된 Event 하나는 실행 실패가 아니다
                                      (docs/03 §53의 per-file 격리)
    Dashboard    Rejected 1, Overall WARN  <- 조용하지 않다

**보내는 쪽도 확인했다.** Signal의 `summary`가 숫자면 이제 그 Desktop에서
거부되고(`signals_rejected/`로 이동, `DateResult.errors`가 이유를 댄다), 같은
날짜의 정상 Signal은 그대로 배달되며, 날짜는 계속 수집 완료로 표시된다 —
쓸 수 없는 Signal 하나가 Desktop을 영원히 세우지 않는다는 기존 규칙 그대로다.

**빈 문자열은 건드리지 않았다.** `""`는 여전히 유효하며, 그것은 A-15의 별개 질문
(열린 채로)이다.

**Evidence:** `tests/test_events.py::DeclaredStringFieldsAreEnforcedTests` 6건
(다섯 타입 × 세 필드 / 표를 docs/02에서 직접 읽어 대조 / 빈 문자열 불변 /
없는 필드는 여전히 "missing" 하나 / 나머지 타입 필드는 이미 덮여 있었다는 sweep),
`tests/test_runner_notion_integration.py::
ANonStringFieldIsRejectedNotCrashedIntoTests` 5건(파이프라인 전체, 다섯 조작),
`tests/test_agent.py::ANonStringSignalFieldIsRefusedOnTheSendingSideTests` 5건.

---

## C42. Summary Section Sprint — 요약을 원문 그대로 반복하는 한 섹션

**한 줄로:** `render_daily_markdown()`은 `## Summary` 섹션에 후보들의 요약을
**`- ` 없이 원문 그대로** 반복한다. 그래서 요약 자체가 불릿이면 그 줄은
`- Event ID: …`라는 **label과 바이트 단위로 같은 줄**이 되는데, 그 줄을 읽는
세 곳 중 두 곳이 그것을 label로 읽고 있었다. 하나는 **실제 Event를 영구히
잃었고**, 하나는 **그 유실을 찾는 탐지기를 꺼 버렸다.**

C30이 같은 함정의 **아이템 블록 안쪽** 문을 닫았다(요약이 `Event ID: EVT-999`인
경우). 블록 안 요약은 `- {summary}`로 렌더링되므로 `- - Event ID: …`가 되어
애초에 매치되지 않는다 — 실제로 새는 곳은 `## Summary`뿐이었고,
`summary_line_indices()`는 `### ` 블록을 걷기 때문에 그 섹션에 닿을 수 없다.

크래프팅도 손편집도 필요 없다. 이 저장소 자신의 benign fuzz corpus가
`- leading dash`와 `Event ID: measured it.`를 **정상적인 요약**으로 이미 열거하고
있고, 없던 것은 그 둘의 조합뿐이었다.

| 읽는 곳 | 질문 | 이전 결과 |
|---|---|---|
| `daily/late_events.existing_event_ids()` | §38 중복 가드 | **늦게 온 Event 영구 유실** |
| `ops_status._kept_but_not_rendered()` | E-17 유실 탐지 | **탐지기가 조용해짐** |
| `ops_status._reviewed_but_not_rendered()` | Decision Context 유실 탐지 | 같은 뿌리 |
| `monthly/parser.py` `unconsolidated` | 몇 줄이 항목이 못 됐나 | 과다 계수(오탐) |

---

### 1. 늦게 온 Event가 영구히 사라진다 (신규, **데이터 유실**)

**측정.** KEEP Candidate 하나, 요약 `- Event ID: L1`, 그 날짜를 Daily Close:

    ## Summary

    - Event ID: L1                    <- 요약, 그대로

    existing_event_ids(day)           {'E1', 'L1'}
    select_late_candidates(day, [L1]) ()        <- 절대 추가되지 않는다
    append_late_events(...)           파일 변화 없음

진짜로 늦게 도착한 L1은 도착한 날에도, 그 날짜를 다시 보는 **모든 이후 실행에서도**
버려진다. §38의 가드는 자기 일을 하고 있다고 믿으므로 카운터도 로그도 없다.
보안 쪽에서 보면 C30이 이름 붙인 것과 같은 **Event ID spoofing**이다 — 한 Event가
다른 Event를 이름으로 억누른다.

**수정.** `daily/markdown.item_block_bounds()` 신설 — label은 `### ` 아이템 블록
안에서만 쓰인다(`_render_item_block()`가 유일한 writer). `existing_event_ids()`는
이제 그 범위 안에서만 label을 찾는다. 규칙이 둘로 분리됐고 둘 다 이름을 얻었다:
**어디에** label이 있을 수 있는가(`item_block_bounds`) / 그 블록 안에서 **어느**
불릿이 요약인가(`summary_line_indices`).

부수 효과로 docstring이 주장만 하던 두 가지가 구조적으로 참이 됐다 —
`## Evidence`의 `- <id>: <text>` 줄(`event_id`가 문자 그대로 `Event ID`이면
label을 철자한다)과 §57이 허용하는 블록 밖 손글씨 메모.

**틀려도 안전한 방향이다.** 진짜 label을 놓치면 대가는 `## Late Events`에
아이템 블록 하나가 중복으로 붙는 것뿐이고 — 그 블록의 label은 스캔되므로 다음
실행에서 멈춘다 — 대가가 눈에 보인다. 유령 id를 더하면 대가는 Company History에
영영 닿지 못하는 Event다.

**Evidence:** `tests/test_daily_late_events.py::OnlyItemBlocksCarryLabelsTests`
6건(+subtests). fuzz corpus에도 `- Event ID: …` 모양을 추가했다 —
`LateSeamFuzzTests`/`RendererParserFuzzTests` 양쪽 모두, 이 조합만 빠져 있었기
때문이다.

---

### 2. 같은 줄이 E-17 탐지기를 껐다 (신규, **관찰성 — 유실 탐지 불능**)

`ops_status._kept_but_not_rendered()`는 E-17의 유실("모든 지표가 정상을 보고하는
채로 Company History에 Event 하나가 비어 있다")을 보이게 만든 함수다. 그 함수의
긴 주석은 C30이 닫은 문을 자세히 적고 있는데, **같은 문이 한 층 위에 하나 더
있었다.**

**측정.** EVT-A를 진짜 렌더러로 세 가지 요약으로 렌더링하고, EVT-B는 그 날짜
파일에 실제로 없다:

    'Shipped it.'         ('EVT-B (2026-08-05)',)
    'Event ID: EVT-B'     ('EVT-B (2026-08-05)',)   <- C30의 수정
    '- Event ID: EVT-B'   ()                        <- 꺼졌다

즉 **한 Candidate가 다른 Candidate를 이름으로 지목해 유실 경보를 끌 수 있었다.**
`_reviewed_but_not_rendered()`(사람이 쓴 Decision Context의 유실)도 같은 코드로
같은 성질을 갖고 있었다.

**수정.** `ops_status._label_lines()` — 두 탐지기가 공유하는 한 함수로, 같은
`item_block_bounds()` 규칙을 쓴다. 판정 로직·메시지·심각도는 한 줄도 바뀌지
않았다.

**Evidence:**
`tests/test_observability.py::ASummaryCanNoLongerSilenceTheLossDetectorTests`
3건(subtest 6). 기존 `KeptButNotRenderedTests`의 fixture는 `### ` 제목이 없는,
렌더러가 만들 수 없는 문서였으므로 **진짜 아이템 블록으로 고쳤다**(약화가 아니라
강화 — 그 fixture로는 실제 Daily 파일에 대해 아무것도 주장하지 못했다).
harness는 `RunnerNotionTestCase`의 선례대로 `_KeptButNotRenderedFixture`
mixin으로 분리해, 두 번째 suite가 첫 번째의 테스트를 다시 돌리지 않게 했다.

---

### 3. Monthly 쪽 오탐은 **고치지 않았다** (특성화)

같은 줄이 `monthly/parser.py`의 `unconsolidated` 계수를 1 올린다. 측정:

    요약 'Event ID: L1'      items 1   unconsolidated 0
    요약 '- Event ID: L1'    items 1   unconsolidated 1

결과는 아무것도 잃지 않은 날에 대해 `MONTHLY_UNCONSOLIDATED …
해당 Daily 파일을 확인해야 한다`가 **그 달을 다시 만들 때마다 영원히** 뜬다.

**그래도 그대로 둔다.** 이 계수는 **문서 전체**를 훑도록 일부러 만들어졌다 —
일찍 끝나 버린 섹션(한 Event의 `project_id`에 `##` 제목이 들어간 경우) 때문에
무고한 Event들이 함께 사라지는 것을 잡는 유일한 장치이고, 자기 계약이 "과다 계수가
안전한 방향"이라고 명시한다. `### ` 블록으로 좁히는 것(1·2번에서 쓴 수정)은
그 보장을 정밀도와 맞바꾸는 일이고, 그쪽 실패는 **유실이 아니라 오탐**이다.

파서 주석에 원인을 적었고 오늘의 숫자를 테스트로 고정했다. 이 테스트가 깨지면
그 트레이드오프가 바뀐 것이므로 이 항목을 갱신해야 한다.
**Evidence:** `tests/test_monthly_history.py::ParserTests::
test_a_bullet_shaped_summary_inflates_the_unconsolidated_count`.

**승인 후 후보 수정(지금은 하지 않음).** (a) `### ` 블록으로 좁히고 일찍 끝난
섹션은 별도 신호로 잡는다, (b) 파서가 이미 요약으로 판정한 문자열과 같은 줄을
prose로 제외한다(주입된 `## Summary` 제목에 흔들리지 않는다), (c) 렌더러가
`## Summary`에서도 요약을 불릿으로 쓴다 — 이건 docs/06의 Daily 파일 구조 변경이라
Spec 결정이다.


---

### 4. `BACKUP_SUCCESS`가 파일이 아니라 **디렉터리**를 세고 있었다 (신규)

**측정.** `git status --porcelain`은 **추적되지 않은 디렉터리**를 한 줄로 접는다.
`daily/`가 아직 한 번도 커밋된 적 없는 Working Copy에 새 Daily 3개:

    add 전   ?? daily/                       -> changed_files ('daily/',)
    add 후   A  daily/2026-08-01.md             changed_files 3건
             A  daily/2026-08-02.md
             A  daily/2026-08-03.md

`backup/runner.py`는 5단계(add **이전**)의 status를 `BackupLogEntry.changed_files`로
실었고, 그것이 Run Manifest의 `changed_files` metric과 OPS_BACKUP의
`Changed Files` 열까지 그대로 간다. 즉 3일치를 push한 Backup이 `changed_files=1`을
보고하고, 이름은 어느 하루도 아닌 `daily/`였다. BUG-39가 이 metric을 붙인 이유가
"이 Backup이 무엇을 실어 날랐는가"인데, **그 답이 가장 중요한 실행에서** 답하지
못했다.

드문 모양이 아니다. 운영자가 `git init`한 Working Copy의 **첫 Backup**(docs/08 §30),
재해 복구가 다시 init하는 경우(docs/10 §45), 그리고 `monthly/`가 처음 쓰이는
실행 — 마지막 것은 정상 운영에서 한 번 일어난다. 디렉터리에 추적 파일이 하나라도
생기면 git이 그 안의 새 파일을 개별로 나열하므로 그 뒤로는 숫자가 맞았고,
그래서 기준선이 없는 실행에서만 틀렸다.

**데이터 유실은 아니다** — 파일은 전부 정확히 commit·push됐다. 틀린 것은 보고이고,
방향은 **축소**다.

같은 필드가 분기마다 다른 뜻이기도 했다: 실패 두 분기는
`sync_result.added + modified`(파일 단위)를 싣고 성공 분기만 git의 접힌 목록을
실었다.

**수정.** add와 commit 사이에서 `git_status()`를 한 번 더 부른다 — 새 git 명령이
아니므로 승인 명령 집합 게이트(`test_spec_conformance.py::
test_git_ops_runs_only_the_approved_command_set`)는 건드리지 않는다. 실측 비용
15.2 / 15.5 / 18.5 ms(10회×3), Backup 단계가 이미 git 프로세스 넷과 push 하나를
띄우는 것에 비하면 무시할 수준이다.

**`sync_result`를 쓰지 않은 이유가 있다.** `git add -A`는 Working Copy **전체**를
stage하므로 sync 이외의 경로로 들어온 것(운영자 파일, 편집기 스왑 파일, E-21의
`.env`)도 커밋에 들어간다 — 그것들은 어떤 sync 결과에도 없다. commit 자신의 내용을
보고하는 것만이 push된 것을 축소하지 않는 유일한 버전이다. 테스트로 고정했다.

**Evidence:** `tests/test_backup_git_ops.py::ChangedFilesIsWhatTheCommitCarriesTests`
6건 — 원격 커밋과의 대조, git이 정말 디렉터리를 접는다는 전제 자체,
추적된 디렉터리에서는 원래도 맞았다는 반대 방향, E-21 stray 파일이 여전히
보고되는지, 그리고 삭제 열. harness는 `_RealBackupFixture`로 분리했다.


---

### 5. 운영자가 읽는 줄이 Python repr이었고, 문서는 다른 줄을 보여주고 있었다 (신규, **문서 drift**)

**측정.** 저장소를 통째로 복사한 격리 사본에서 **`python run_company_ops.py`를
실제로 실행**했다(Task Scheduler가 돌리는 바로 그 명령). 첫 실행, 시작일 17일 전:

    Daily History (Scheduler): COMPLETED, generated=(datetime.date(2026, 8,
    1), datetime.date(2026, 8, 2), … datetime.date(2026, 8, 17))

606자의 Python repr이 **"이번 실행이 무엇을 닫았는가"에 답하는 그 한 줄**에
찍힌다. `f"generated={scheduler_result.generated_dates}"`가 tuple의 repr을
그대로 보간하기 때문이다.

**그런데 `AGENT.md` §6a-3은 운영자에게 다른 줄을 보여주고 있었다:**

    Daily History (Scheduler): COMPLETED, generated=(2026-08-05,) reused=(08-01 … 08-04)

그리고 그 절의 유일한 지시가 **"둘을 비교하라"**이다 — "복구 직후에는 `reused`가
크고 `generated`가 작은 것이 정상이며, 그 반대라면 즉시 멈추고 원격을 확인해야
한다." 그 지시가 가장 중요한 순간이 재해 복구 직후이고, 그때가 두 목록이 가장
긴 순간이다. 복구된 60일은 `datetime.date(...)` 약 1,800자로 찍힌다.

**문서를 코드에 붙들어 두는 테스트가 이미 있었는데 통과했다.**
`RestoreSectionMatchesTheCodeTests`는 자기 docstring에 "그 두 단어가 실행이
실제로 찍는 것일 때에만 그 지시가 실행 가능하다"고 써 놓고,
`assertIn('f"generated={scheduler_result.generated_dates}"', source)` —
**줄이 어떻게 쓰였는가**에 대한 주장 — 을 검사하고 있었다. **무엇이 찍히는가**는
아무도 보지 않았다. C41 §1이 이름 붙인 바로 그 모양이다.

**수정.** `_dates()` — 개수를 먼저, 날짜는 ISO로. 10일을 넘으면 나머지를
**세어서** 붙인다(`외 N일`); 잘랐다는 말 없이 자르면 긴 catch-up이 짧은 것처럼
읽히므로, 이 함수가 없애려는 오독을 새 자리에서 다시 만드는 셈이 된다.

    실행 전   generated=(datetime.date(2026, 8, 1), … )        606자
    실행 후   generated=17 (2026-08-01, …, 외 7일)
    복구 재현 generated=0 reused=17 (2026-08-01, …, 외 7일)

마지막 줄이 §6a-3이 요구하는 판단을 한눈에 준다. `AGENT.md`의 예시는 실제 출력과
글자 그대로 같게 고쳤고, 가드 테스트는 **함수를 실제로 돌려** 출력 줄을 검사하도록
바꿨다(약화가 아니라 강화 — 소스 문자열 검사로는 이 drift를 지나칠 수밖에 없다).

**Evidence:** `tests/test_repository_hygiene.py::RestoreSectionMatchesTheCodeTests`
(기존 1건을 행동 검사로 교체 + 신규 2건).

---

### 6. Task Scheduler가 실제로 돌리는 명령에 E2E가 없었다 (신규, 테스트 공백)

이 저장소의 모든 테스트는 `app.runner.run_once()`에 **19개 경로를 전부 명시해서**
부른다. 그것이 파이프라인을 시험하는 옳은 방법이고, **운영이 하는 일은 아니다** —
운영은 `run_company_ops.py`를 돌리고, 그 스크립트는 3개만 넘기고 나머지 16개는
여섯 모듈이 import 시점에 얼려 둔 `PROJECT_ROOT` 기본값에 맡긴다(C34 §3).
루트 스크립트를 포함한 line coverage 측정에서 **`main()` 본문이 한 번도 실행된 적이
없다**는 것이 나왔다 — 그 둘 사이의 배선은 아무것도 덮고 있지 않았다.

제자리에서는 돌릴 수 없다. `_one_runtime_root_or_refuse()`가 존재하는 이유가 바로
`RUNTIME_DIR` 재바인딩을 막는 것이고, 그것을 한 번 했다가 **진짜 파이프라인**이
temp 트리에 History를 쓰면서 라이브 watermark를 그 너머로 전진시킨 적이 있다.
그래서 **저장소를 복사해서** 그 사본을 subprocess로 돌린다 — 두 루트가 같이
움직이므로 가드는 옳은 이유로 통과하고, temp 밖은 아무것도 건드리지 않는다.

실행해서 확인한 것: exit 0 / Notion 미설정이 `[INFO]`이지 실패가 아닌 것 /
Company History가 그 사본의 runtime 아래에 쓰이는 것 / **state 파일 세 개가 History와
같은 트리에 떨어지는 것**(C34 §3의 주장을 파일시스템에서 확인) / 원격에 push된 것 /
Scheduler 줄이 읽히는 것 / 두 번째 실행이 아무것도 더하지 않고 exit 0인 것 /
manifest가 디스크에 있는 것 / 시작일 미설정이면 **실행 자체가 없고 exit 1**인 것.

**Evidence:** `tests/test_e2e_operations_scenarios.py::ProductionEntrypointE2ETests`
9건.

---

### 7. Desktop 4의 문지기에 행동 테스트가 없었다 (신규, 테스트 공백)

`_resolve_history_start_date()`의 거부 경로 둘과 `_report_backup_failure()`의
fallback 둘이 미실행이었다. C41 §7이 `run_agent.py`(Desktop 1~3의 문지기)에 대해
한 것과 같은 자리이고, **결과의 모양은 반대이며 크기는 작지 않다**: 시작일을
추측하면 Desktop 4는 시끄럽게 실패하지 않는다 — Company History가 어디서 시작하는지를
조용히 정하고, `daily_history_state.json`이 그 뒤로 전진한다.

`_report_backup_failure()` 쪽은 "manifest를 읽을 수 없을 때"다. 주석이 규칙을 적어
뒀지만(분류 못 한 실패는 보수적으로 2) 그 두 줄은 아무도 실행하지 않았고,
읽히는 경우에 manifest가 이기는 것(밀린 push는 DEGRADED/3)도 검사되지 않았다.

**Evidence:** `tests/test_architecture_invariants.py::
RunnerEntrypointConfigurationTests` 5건, `::BackupFailureExitCodeFallbackTests` 3건.


---

### 8. 운영자에게 "이 숫자는 계산되지 않았다"고 말하는 줄이 **도달 불가**였다 (신규, **조용한 실패**)

`ops_status._print_agent()`:

    if snapshot.pending_dates and _agent_start_date() is None:
        print("  (COMPANY_OPS_AGENT_START_DATE 미설정 — 미수집 날짜는 계산되지 않음)")

`read_status()`는 `agent_start_date`가 **주어졌을 때만** `pending_dates`를
계산한다(자기 docstring: *"a first-ever run has no other way to know where
counting starts (docs/07 §50: never guessed)"*), 그리고 이 블록이 넘기는 값이
`_agent_start_date()`다. 즉 `pending_dates`가 비어 있지 않다는 것은 이미 변수가
설정돼 있다는 뜻이고, `and _agent_start_date() is None`은 그것과 모순이다 —
**이 줄은 어떤 상태에서도 찍히지 않는다.**

**대가.** 변수가 설정되지 않은 Desktop은 이렇게 찍는다:

    미수집 날짜         : 0

watermark가 닷새 전인데도 그렇다. 완전히 따라잡은 Desktop과 **바이트 단위로
같은 줄**이고, 그것도 Company History를 *생산하는* 머신에서다. 이 저장소가
반복해서 찾아낸 "측정의 부재가 건강한 측정으로 읽히는" 모양이며, 그것을 막으려고
쓴 코드가 돌 수 없었다.

`_print_history()`의 형제(`COMPANY_OPS_HISTORY_START_DATE`)는 처음부터 옳은
모양이었다 — `if history_start is None:`. 그 모양을 그대로 가져왔다.

**Evidence:** `tests/test_observability.py::PendingDatesAreListedTests` 5건
(도달 불가였던 줄, 설정된 경우 뜨지 않는 것, 그리고 두 형제를 소스에서 대조).

---

### 9. 유실 탐지기 셋의 **출력 경로**가 한 번도 돌지 않았다 (신규, 테스트 공백)

C41이 만든 line coverage를 루트 스크립트까지 포함해 다시 돌렸다(전체 96.90%).
`ops_status.py`의 미실행 42줄을 전수로 읽으니 대부분은 읽기 전용 진단의 OSError
fail-safe(정당)였고, **셋은 아니었다.** 셋 다 같은 모양이다 — 탐지 함수 자체는
충실히 테스트돼 있는데, 그 결과를 **화면 줄과 ATTENTION으로 바꾸는 자리**는
아무도 돌리지 않았고, 대신 **소스 문자열 검사**가 그 자리를 지키고 있었다
(C41 §1이 이름 붙인 바로 그 모양).

| 블록 | 무엇을 잃는가 | 대신 있던 것 |
|---|---|---|
| 전달 정합성 (`_print_agent`) | 전송 완료로 기록됐지만 sync 폴더에 없는 Event | `COMPANY_OPS_AGENT_SYNC_FOLDER`를 설정한 채 `_print_agent()`를 부른 테스트가 없음 |
| 검토 미반영 (`_print_history`) | **사람이 입력한** Decision Context | `assertIn("_reviewed_but_not_rendered(keep_candidates, daily_dir)", source)` |
| Monthly 시퀀스 구멍 | 통합됐는데 파일이 사라진 달 | 함수 단위 테스트만 |

전달 정합성 쪽이 특히 그렇다 — C32 §13이 고쳤다고 적은 결함의 문장이
**다른 파일의 docstring**에 있다: *"`ops_status.py` prints `전달 정합성 : OK` —
the same line it prints when every Event was checked and every one arrived"*.
그 뒤로 무엇이 찍히는지는 아무도 보지 않았다. 이제 세 판정(OK / UNDELIVERED /
UNKNOWN), 두 ATTENTION 줄, 미설정 경우, 그리고 **목록은 5건으로 자르되 개수는
자르지 않는다**는 성질까지 실행해서 확인한다.

**Evidence:** `tests/test_observability.py::DeliveryConsistencyIsRenderedTests` 7건,
`::ReviewedButNotRenderedIsRenderedTests` 3건,
`::MonthlySequenceHoleIsRenderedTests` 3건.

**결함 0건으로 닫은 것(기록).** 나머지 미실행 줄은 전부 읽고 정당함을 확인했다 —
`ops_status.py`의 OSError fail-safe 9곳, `runner.py`의 `nonlocal` 선언(측정
도구의 인공물)·중첩 except의 `pass`·E-16의 도달 불가 분기,
`scheduler/lock.py`의 POSIX 전용 분기와 unlink 실패 `pass`,
`notion/transport.py`의 실제 HTTP, `review_cli.main()`의 대화형 진입점.

**그리고 반대 방향 하나 — `ops_status.main()`의 유일한 exit 0 경로.** 이 파일의
모든 다른 테스트는 결함을 심고 ATTENTION이 그것을 지목하는지 본다. 그래서 정작
그 절 전체가 기대는 성질이 검사되지 않은 채 남아 있었다: **정상 시스템은 빈
ATTENTION을 낼 수 있어야 한다.** 이 저장소 자신의 지침이 이유를 적어 두었다 —
"지워지지 않는 경보는 그 절을 대충 넘기도록 훈련시킨다". 늘 무언가를 말하는 View는
아무도 안 읽게 되고, 그때 그 안의 진짜 경보도 함께 사라진다.

Desktop 넷이 오늘 보고했고, watermark와 Daily가 맞고, 그 Daily가 마지막 백업보다
먼저 쓰였고, 9단계 전부가 SUCCESS인 runtime을 만들어 실제로 돌렸다 —
`ATTENTION — 없음`, exit 0. `main()`의 그 경로와 그 문장은 그때까지 한 번도
실행된 적이 없다.
**Evidence:**
`tests/test_observability.py::AHealthyRuntimeCanActuallyBeQuietTests`
3건.



---

### 10. Dashboard가 재해 복구를 조용한 일요일과 구별하지 못했다 (신규, **Dashboard**)

C39가 `SchedulerRunResult`를 `generated_dates`(이번 실행이 **쓴** 날) /
`reused_dates`(이미 있어서 그대로 둔 날)로 쪼갠 이유는 하나였다 — 복구된
Desktop 4는 자기가 쓰지 않은 날을 닫고, 그것을 "생성"이라고 부르면 운영자에게
**파이프라인이 다시 만들 수 없는 History를 다시 만들었다**고 말하는 셈이다.

그 쪼갬은 Run Manifest와 `run_company_ops.py` stdout에는 닿았다.
**Dashboard에는 닿지 않았다** — CEO Decision ④가 "CLI 확장 금지, Dashboard는
Notion으로"라고 정하며 운영자의 한눈 보기로 만든 바로 그 View다. 실측
(`test_e2e_disaster_scenarios.py`가 수행하는 복구):

    manifest      generated_days=1  reused_days=4
    Dashboard 행  Generated Days: 1   (그 외 아무것도 없음)

17일이 백업에서 돌아온 실행의 행은 `Generated Days: 0`이고, 그것은 완전히
한가한 일요일이 쓰는 값과 같다.

**한 열 추가.** `Reused Days`. 이 Database의 열은 필요할 때마다 자라 왔고
(C31 13 → C32 15 → C33 17 → C37 18 → C42 20), 그 성장을 위해
`bootstrap_dashboard_properties()`와 docs/13 §3-⑧-4가 이미 존재한다.
**`Overall` 판정에는 넣지 않았다** — 날을 재사용하는 것은 파이프라인이 제대로
도는 것이고(docs/07 §28), 판정의 규칙("앞으로 어떤 입력이든 먼저 열을 얻어야
한다")은 WARN의 *원인*에 대한 것이지 모든 열이 입력이라는 뜻이 아니다.

**같이 고친 것 — `getattr(..., 기본값)` 하나.** `record_run()`은 자기 입력을
직접 속성 접근으로 읽는다고 열 줄 위에 적어 두고("a default would only be able
to hide the day one is renamed — reporting 0 skipped files forever instead of
failing"), `generated_days`만 `getattr(scheduler_result, "generated_dates", ())`
였다. C39가 **바로 그 필드의 의미를 바꿨다**는 점에서 이미 아슬아슬했다. 둘 다
직접 접근으로 바꿨다.

**그리고 그 drift를 잡으라고 있던 가드가 절반이었다.**
`DoublesMatchTheRealResultObjectsTests`의 docstring이 말하는 조용한 방향은
*"the real object grows a field, the double does not, and every test here goes
on passing against a shape production never sees"* — 그리고 그 클래스는 double
넷 중 **둘만** 검사하고 있었다. 빠진 둘 중 하나가 실제로 drift한 바로 그
`_FakeScheduler`다(`SchedulerRunResult` 다섯 필드 중 둘만 갖고 있었다).
두 double을 실제 dataclass 필드 전부로 채우고, 빠져 있던 두 검사를 추가했다.

**Evidence:** `tests/test_notion_dashboard.py::ReusedDaysReachesTheDashboardTests`
5건(복구 모양 / 한가한 날과의 구별 / 판정 불변 / 이름이 바뀌면 조용한 0이 아니라
FAILED / 스키마 등재), `::DoublesMatchTheRealResultObjectsTests` 2건 신설.
docs/13의 열 성장 이력도 갱신했다.

**같은 모양, 두 번째 열 — `Deleted Files` (E-25).** `BACKUP_FAILED`는 성격이
전혀 다른 두 사건이 쓴다: docs/08 §21의 자격증명 실패와, §31/§44-47의 삭제
게이트가 **Local Master 파일이 사라져서** add/commit/push를 거부한 것.
운영자의 다음 행동은 하나는 토큰이고 하나는 **없어진 History를 찾는 일**이다.
docs/14 §5의 어휘를 바꾸는 것은 Spec 결정이라 E-25로 열려 있고, C31은 그와
무관한 부분을 했다 — manifest의 자유 텍스트 `reason`에 사실을 적고
`deleted_files` metric을 실었다. **그 둘 다 Dashboard에는 없다.** 행은 양쪽 모두
`Backup Status: BACKUP_FAILED / Overall: FAIL`이었다.

숫자 하나를 옮겼다. 새 classification 없음, `Overall` 불변.
**Evidence:** `tests/test_notion_dashboard.py::
TheTwoBackupFailuresAreTellableApartTests` 5건(삭제 2건 / 인증 0건 / 판정 불변 /
정상 실행도 0을 쓴다 / 실제 게이트가 이 필드를 채운다는 전제).


---

### 11. 성공한 단계의 metric은 **어떤 View에도 닿지 않는다** (신규, 특성화 — 고치지 않음)

`recorder.ok()` 호출은 전부 metric을 기록하는데, **그것을 인쇄하는 곳이 없다.**
`ops_status._print_last_run()`은 SUCCESS 컴포넌트를 통째로 건너뛰고(`continue`),
`run_company_ops._report_run_summary()`는 실패만 인쇄한다. 즉 정상 경로에서
기록된 숫자는 `runtime/runs/last_run.json` 안에만 있다.

**대부분은 그것이 옳고, 테스트가 그렇게 말한다** — `daily`의 카운트, `backup`의
상태와 해시, `collector`의 넷, `transport`의 여섯은 Dashboard 행이나 entrypoint
stdout에 이미 있고, 다시 인쇄하면 LAST RUN 블록이 "정작 중요한 부분을 아무도 안
읽을 만큼" 길어진다(그 블록 자신의 주석).

**하나는 다른 데 없고, 그 하나가 활동이 아니라 *불일치*를 보고한다:**
`notion_sync.same_instant_skips` (E-23, C40이 셀 수 있게 만든 것). Company
History는 지켰고 Notion 행은 지키지 못한 Event의 수 — Source와 View가 갈라진
것 — 이고, docs/04 §29-30 기준으로 skip은 **성공**이므로 SUCCESS 가지에
기록된다. 실측: manifest `same_instant_skips=2`, `ops_status.py` LAST RUN 침묵,
Dashboard는 `Notion Skipped: 2`뿐이라 진짜 Late Event skip과 구별할 수 없다.

**고치지 않았다.** 정상 단계의 어느 숫자가 줄을 얻어야 하는지는 판단이고,
E-23 자신의 해결(동시 skip을 별도 결과로 볼 것인가)이 아직 열린 Spec 결정이다.
판단이 아닌 것 하나는, **이것을 잡았어야 할 sweep이 볼 수 없었다**는 사실이다 —
`WrittenAndNeverReadFieldTests`는 `src/`의 모든 **dataclass 필드**를 걸었고,
metric은 dict 키다.

**Evidence:** `tests/test_observability.py::
MetricsOnASuccessfulStepReachNoViewTests` 4건(성공이면 안 찍힘 / 같은 단계가
실패하면 찍힘 / runner가 정말 성공 가지에 기록한다는 전제 / Dashboard도 두 skip을
가르지 못한다). 이 테스트가 깨지면 성공 metric용 View가 생긴 것이므로 어느 것을
왜 골랐는지 여기에 적어야 한다.

---

## C41. Unrun Branch Sprint — "테스트가 있다"와 "그 줄이 돈다"는 다르다

C40이 만든 line coverage 수집기를 **결함 탐지기로** 썼다. 97.9%의 나머지 85줄을
훑는 것이 아니라, **가장 중요한 모듈의 미실행 분기부터 하나씩 열어** 왜 그 줄이
한 번도 돌지 않았는지 물었다. `app/runner.py`의 미실행 4줄이 이 Sprint 전부다.

| # | 미실행 분기 | 밝혀진 것 |
|---|---|---|
| 1 | `runner.py` MONTHLY_UNCONSOLIDATED 로그 | **테스트가 프로덕션 줄을 복사해 놓고 있었다** |
| 2 | `runner.py` BACKUP_PENDING(반환 경로) | **도달 불가 분기 + BUG-39 metric이 실제로는 안 붙는다** |
| 3 | `runner.py` MONTHLY_FAILED(반환 경로) | 커버리지 구멍 — E2E로 신설 |
| 4 | `runner.py` dashboard SKIPPED | **E-16을 독립적으로 재확인** (기록이 정확했다) |

---

### 1. 테스트가 프로덕션 줄을 다시 써 놓고 통과하고 있었다

`monthly/parser.py`는 한 Event의 `project_id`에 `##` 제목이 들어가면 **섹션
하나를 통째로** 잃는다(무고한 Event까지). 이스케이프는 docs/06의 렌더링 계약이라
유실 자체는 남기고, `MonthlyResult.unconsolidated_days`로 **보이게** 만들었고,
`app/runner.py`가 그것을 `daily_late_update.log`에 쓴다 — AGENT.md §6a가 "돌긴
돌았는데 뭔가 안 됐다"일 때 운영자를 보내는 그 파일이다.

**그 마지막 한 줄을 실행하는 테스트가 없었다.** 그렇다고 테스트가 없는 것도
아니었다 — 있는 쪽이 더 나쁘다:

    test_the_runner_writes_it_to_the_log_operators_are_told_to_read
        -> monthly.run_once()를 부르고, **Runner의 로깅을 테스트 본문에
           다시 구현한 뒤**, 그 결과를 단언한다
    test_the_runner_really_contains_that_call
        -> inspect.getsource(run_once)에 문자열 두 개가 있는지 본다
           (자기 docstring: "The half the test above cannot prove by
           construction")

이 쌍은 Runner의 조건이 **뒤집혀 있어도**, 로그 **경로가 틀려도**, `_bounded()`가
메시지를 잘라 없애도 통과한다. `app.runner.run_once()`를 실제로 돌려 진짜 로그
파일을 읽는 테스트 4건을 신설했고(성공 줄과 공존하는지, 건강한 달에는 안 뜨는지,
run이 실패로 격상되지 않는지 포함), coverage 재측정으로 그 줄이 실행됨을 확인했다.

### 2. BACKUP_PENDING을 말하는 곳이 둘인데, 도는 것은 하나다 (신규)

`app/runner.py`에는 BACKUP_PENDING을 만드는 자리가 **둘** 있다:

    except GitOperationError            분류하고 다시 raise
    if backup_entry.final_status is PENDING   반환값 분기

**두 번째는 도달할 수 없다.** `backup/runner.py`가 *반환*하는 `BackupLogEntry`의
`final_status`는 SUCCESS / NOT_REQUIRED / FAILED 셋뿐이고, `BackupStatus.PENDING`은
오직 `state.backup_status`에 쓰인 뒤 **raise되기 직전에만** 등장한다. E-16의
`recorder.skipped(C_DASHBOARD)`와 같은 모양이다.

**그리고 그 차이에는 대가가 있다.** BUG-39가 붙인 두 metric(`commit_hash`,
`changed_files`)은 **도달 불가능한 쪽에만** 있다. 실제 Runner를 갈라진 remote에
대고 돌려 실측:

    raised            GitOperationError (BUG-4: 흡수하지 않는다)
    manifest backup   FAILED / BACKUP_PENDING / DEGRADED / RETRYABLE
    metrics           {}                    <- 두 metric이 없다
    overall           DEGRADED / exit 3
    기록된 단계        9개 중 8개 (dashboard는 시작도 못 한다)
    backup_state.json BACKUP_PENDING
    Company History   디스크에 있다

즉 **이 배포가 지금 실제로 들어가 있는 상태**(`ops_status`가 마지막 실행에 대해
`! backup: BACKUP_PENDING [DEGRADED/RETRYABLE]`를 찍고 있다)의 manifest는 "어떤
commit이 밀려 있는가"를 말하지 못한다. 코드만 읽으면 말하는 것처럼 보인다.

**한 것:** 실측을 그대로 고정하는 특성화 테스트 8건(분류·exit code·metric 부재·
이후 단계 미시작·History 보존·state와 manifest 일치·건강한 push 대조), 그리고
**도달 불가능성의 전제 자체**를 `backup/runner.py` 소스에서 확인하는 테스트
(반환 경로의 `final_status`가 정확히 그 셋인지). 도달 불가 분기에는 왜 지우지
않는지를 적었다 — BUG-4/A-18이 반대로 결정되면 그날 바로 살아나는 경로다.

**하지 않은 것:** 예외 경로에 metric을 채우는 것. 그 시점에 commit hash는 예외에도
state 파일에도 없다(`last_backup_commit`은 성공한 실행만 갱신한다). 배관을 새로
놓는 일이라 SKIP하고 기록한다.

### 3. MONTHLY_FAILED — 반환 경로가 한 번도 돌지 않았다

Runner는 Monthly의 두 비성공 결과를 다르게 다룬다: PENDING은 docs/09 §10이
의도적으로 거절하는 것이라 로그만, FAILED는 component 실패다. 커버리지는
**PENDING 쪽만 덮여 있었다.** `MONTHLY_CONSOLIDATION_FAILED`를 만드는 유일한
실행 경로는 그 아래의 `except Exception` 폴백이었는데, 그것은 다른 사건이다
(모듈 밖으로 새는 예외 vs 모듈이 정상적으로 실패를 보고하는 것).

완성된 8월에 **읽을 수 없는 Daily 파일 하나**(docs/10 §46이 예상하라고 적은
바로 그 상태)를 넣고 9월에 실행하는 E2E 5건을 신설했다 — component FAILED,
DEGRADED/exit 3, **9단계 전부 기록되고 Backup은 성공**(§74: Monthly 실패가
Runtime을 막지 않는다), `reason`이 비어 있지 않음, 그리고 정상 달 대조.

### 4. E-16을 독립적으로 재확인했다

`recorder.skipped(C_DASHBOARD)`가 미실행으로 나왔다 — BACKLOG E-16이 "도달
불가능"으로 적어 둔 그대로다. **기록이 정확했고**, 이번엔 문구가 아니라 실행
데이터가 그것을 확인했다.

### 5. 측정 도구 자신의 사각 — 루트 스크립트가 한 번도 안 재어졌다

C40의 line coverage는 `src/`만 봤다. **진입점 네 개는 측정 밖에 있었다.**
포함해서 다시 재니:

           statements  missed  covered
    전체         5,055     182    96.4%
    ops_status.py    751      46    93.9%
    run_company_ops.py 107     35    **67.0%**   <- 최악
    run_agent.py      65      10    84.6%
    init_notion.py    49       8    83.7%

`run_company_ops.py`는 **Task Scheduler가 실제로 실행하는 파일**인데 3분의 1이
한 번도 돌지 않았다.

### 6. `_build_notion_clients()` — 무엇이 실행될지 정하는 함수에 행동 테스트가 없었다

미실행 줄은 사실상 이 함수 전체였다. 환경변수만으로 세 결과를 정한다:

    token/PROJECTS 없음              (None, None)      둘 다 안 돈다
    NOTION_OPS_RUNS_DATABASE_ID 없음 (sync, None)      Sync만
    셋 다 설정                        (sync, dashboard) 둘 다

**세 갈래 모두 행동 테스트가 없었다.** 있던 것은 (a) 여기와
`test_spec_conformance.py`의 `inspect.getsource()` 문자열 검사, (b)
`test_run_company_ops_encoding.py` — 이건 실제로 호출하지만 **subprocess**이고
목적은 cp949 콘솔에서 안 죽는지다. 셋 다 **어떤 객체가 돌아오는지는 볼 수 없다.**

퍼센트보다 중요한 이유:

- 가운데 갈래가 **이 배포가 매 실행 타는 길**이다(docs/13: `NOTION_OPS_RUNS_DATABASE_ID`
  미설정). 여기서 None 대신 client가 돌아오면 `record_run()`이 OPS_RUNS 행을
  **그 client가 든 데이터베이스**에 쓴다.
- 세 번째 갈래는 운영자가 docs/13 §3-⑧을 끝내는 날 타는 길이다. 두 client는
  **하나의 transport**를 공유하고 둘을 갈라놓는 것은 database id뿐인데,
  `record_run()`의 docstring이 직접 말한다: "client must be bound to the OPS_RUNS
  database id ... nothing can check that". **결정되는 자리가 여기이므로 검사할 수
  있는 자리도 여기다.**

행동 테스트 6건 신설(세 갈래 + 두 id가 절대 같지 않음 + 빈 값이 "없음"이 아니라
"비어 있음"으로 보고됨 + 토큰이 출력에 안 나옴). 네트워크는 쓰지 않는다 —
`RealNotionTransport`는 토큰을 담기만 하고 요청 시점에야 연결한다.

### 7. `run_agent.py`의 설정 검증 3개와 그 보고 경로도 미실행이었다

Desktop 1~3의 문지기다. `ConfigurationError` raise 세 개(sync 폴더 없음, 시작일
없음, 시작일 형식 오류)와 그것을 stderr로 보고하고 exit 1을 돌려주는 handler가
전부 한 번도 실행되지 않았다.

이쪽 결과는 **비대칭**이라 더 나쁘다: Desktop 4만 사람이 본다. 시작을 거부한
Agent는 아무도 안 보는 화면에 그 말을 하고, 잘못 설정된 채 도는 Agent는 Event를
아예 안 만든다. 어느 쪽이든 COO가 받는 첫 신호는 며칠 뒤 `ops_status`의
"3일 이상 아무것도 오지 않은 Desktop"이고, 그것은 **"그 PC가 꺼져 있었다"와
구분되지 않는다.**

6건 신설. 고정하는 것은 문구가 아니라 docs/07 §50의 두 결정이다 — **없는 값은
추측하지 않고 거부한다**, **형식이 틀린 값은 조용히 재해석하지 않는다**. 빈
문자열이 "없음"과 같이 취급되는 것도 포함한다(`Path("")`는 현재 디렉터리이므로,
그렇지 않으면 Agent가 자기 작업 디렉터리로 "배달"하고 영원히 성공을 보고한다).

### 8. Branch coverage 도구를 만들고 돌렸다 — 결함 0건

`sys.monitoring`(3.12+)의 `BRANCH` 이벤트로 **분기** 커버리지를 stdlib만으로
측정했다. line coverage는 "이 줄이 돌았다"를 말하지만 이것은 **"이 조건이 늘 참이었다"**
를 말한다 — 줄은 덮여 있는데 한쪽 길만 다닌 경우.

    관측된 분기 지점 1,066   한쪽만 다닌 것 279 (26%)

위험도 순으로 표본을 읽었다(`app/runner.py` 40, `agent/outbox.py`,
`agent/delivery.py`, `transport/intake.py`, `scheduler/scheduler.py`). **결함은
없었다.** 대부분 셋 중 하나다:

- `X if X is not None else DEFAULT` 의 기본값 쪽 — 테스트가 **일부러** 모든 경로를
  명시적으로 넘기기 때문이다(`TestIsolationGuardTests`가 강제한다). 한쪽만 다니는
  것이 곧 안전 규칙이 지켜지고 있다는 증거다.
- 단일 스레드로는 도달 불가능한 race 흡수 가지(`outbox.stage()`의 `except
  FileExistsError` 안쪽) — 그 자리의 두 의도는 이미 각각 테스트돼 있다.
- 방어적 early return(`if not root.is_dir()`).

즉 이 축은 **깨끗하다**. 도구와 수치를 기록해 두는 이유는 다음 사람이 같은 도구를
다시 만들지 않게 하기 위해서다.

### 9. 두 secret 탐침의 fail-safe 방향이 서로 반대인 것 — 문서만 있고 테스트가 없었다

git이 답하지 못할 때:

    _would_reach_the_commit(candidates)   FILTER  -> 후보를 그대로 돌려준다
                                                    (fail **open**)
    _secrets_ever_committed(working_copy) PRODUCER -> 아무것도 돌려주지 않는다
                                                    (fail **closed**)

`_secrets_ever_committed()`의 docstring이 이 비대칭과 이유를 이미 적고 있다 —
"That probe filters a set it was handed, so failing open keeps a real exposure
visible. This one *adds* a claim about history; if git cannot answer, asserting
a leak would be inventing one." **옳고, 그리고 테스트가 없었다.** 두 fail-safe
반환 모두 미실행이었고, 가장 가까운 기존 검사는 `_would_reach_the_commit(
Path("/nonexistent"), ())` — **빈 후보 집합**이라 fail open과 fail closed를
구분하지 못한다.

두 방향은 refactor 하나 거리다. filter가 fail closed가 되면 살아 있는 자격증명
경고가 사라지고, producer가 fail open이 되면 git 없는 모든 머신이 유출을 외친다
(그리고 아무도 안 믿는 경고는 아무도 안 읽는 경고다).

git 저장소가 아닌 디렉터리로 5건 신설 — 각 방향, 없는 디렉터리, **둘이 반대임을
한 단언으로**, 그리고 그 디렉터리가 정말 git이 못 읽는 상태인지 확인하는 가드
(그렇지 않으면 위 넷이 평범한 경로를 재면서 통과한다).

### 10. 그래서 "source 검사 테스트"를 전부 금지했는가 — 아니다 (측정 후 결정)

§1과 §6의 두 결함은 모두 `inspect.getsource()` 검사가 **행동 검사의 대체물로**
쓰인 자리에서 나왔다. 그래서 전수를 세어 봤다: **78건.**

대부분은 정당하다. 이 저장소에서 그 검사는 *배치*를 고정하는 데 쓰인다 —
"lock 해제가 `finally` 안에 있는가", "모든 git 호출이 timeout을 갖는가",
"step 5에 여전히 per-event guard가 없는가" — 행동으로는 볼 수 없는 성질들이다.

**그래서 금지 규칙을 만들지 않았다.** 78건 중 대부분을 잡는 검사는 사람이 끄는
법을 배우는 검사이고, 그것은 C38이 문서 포인터 검사에서 이미 내린 판단과 같다.
문제였던 둘은 좁다: (a) 행동 검사가 **가능한데도** source 검사로 대신한 경우,
(b) 테스트가 프로덕션 로직을 **다시 구현**하고 그 구멍을 source 검사로 메운 경우.

대신 남기는 것은 **방법**이다: 이 둘을 찾아낸 것은 규칙이 아니라 coverage였다.
다음에 같은 것을 찾으려면 `sys.settrace` 한 번이면 된다.

전체 Regression: **2,431 passed / 4 skipped / 1,471 subtests / 0 failed**
(Sprint 시작 시 직접 재측정한 baseline 2,397 — C40 보고 숫자와 일치했다).
`app/runner.py`의 미실행 줄: **4 -> 2**, 남은 둘은 도달 불가로 문서화된
분기와 최선-노력 로그 실패 가지다. 루트 스크립트 포함 line coverage **96.4%**,
branch coverage 1,066지점 중 한쪽만 다닌 것 279(전부 정당).

---

## C40. SKIP Re-audit Sprint — "승인 필요"라는 문구를 믿지 않는다

SKIP 항목을 **문구가 아니라 코드로** 다시 봤다. 각 항목마다 물었다: 정말 정책
결정이 필요한 부분은 어디까지이고, 그와 **무관하게** 지금 할 수 있는 것은
무엇인가. 그리고 반대 방향도 — **해결됐다고 기록됐는데 실제로는 남아 있거나,
남아 있다고 기록됐는데 실제로는 해결된 것**은 없는가.

| # | 항목 | 결과 |
|---|---|---|
| 1 | E-23 동일 timestamp | **결함 (관측성) — 수정.** 결정은 그대로 두고 두 사건을 구별 가능하게 |
| 2 | A-20 lock 인지 | **인용된 테스트가 존재하지 않았다 — 4건 신설** |
| 3 | E-17 기록이 낡았다 | 가시성은 이미 닫혀 있었다 — 기록 정정 |
| 4 | BACKLOG 증거 링크 전수 | 119개 중 1개 끊김 → 고치고 **불변식으로 고정** |
| 5 | BUG-42 / E-20 / E-22 | Fault injection으로 재현 — **기록이 정확하다.** 변경 없음 |

---

### 1. E-23 — 결정은 명세의 몫이지만, 구별은 아니다

같은 날짜의 두 번째 Signal이 Notion에 도달하지 않는 것은 docs/04 §29-30(과거
**또는 동시**는 Current State를 되돌리지 않는다)과 docs/06 §12(자기 timestamp가
없는 Signal은 그 날짜의 자정을 받는다)가 각각 옳아서 생기는 일이다. 셋뿐인 수정
후보가 전부 명세 변경이라 SKIP은 옳다.

**그러나 SKIP된 것은 결정이지 관측이 아니었다.** 재현해 보니 두 사건이 결과
객체에서 **완전히 동일했다**:

    same instant #2   NOTION_SKIPPED_OLD_EVENT   error=None
    genuinely older   NOTION_SKIPPED_OLD_EVENT   error=None

하나는 게이트가 명세대로 동작한 것(§63)이고 다른 하나는 **Source와 View가
갈라진 것**인데, 어떤 하류도 둘을 나눌 수 없었다. C32가 `Notion Synced`에서
`Notion Skipped`를 떼어낸 것과 같은 모양이다.

**한 것:** 동시(`==`)인 경우에만 이유를 `SyncResult.error`에 붙인다 — 이미
`comparison_note`가 쓰는 그 통로이고, `_log_notion_sync()`가 그대로 실어 나른다.
Runner는 그 수를 세어 Run Manifest의 `notion_sync` metric `same_instant_skips`로
남긴다(평범한 실행에는 metric 자체가 없다 — `or None`).

**하지 않은 것:** 비교(`<=`)도, 상태값(docs §32-37이 열거한다)도, 심각도도 건드리지
않았다. 실행은 여전히 SUCCESS / exit 0이다 — Company History는 두 Event를 모두
갖고 있고 Notion 행은 명시적으로 View이므로(docs/14 §1), 이것을 실패로 올리는 것은
늑대 소년이다. 그 판단의 근거 자체를 테스트가 지킨다: **Company History가 두 건을
모두 갖고 있다**는 단언이 깨지면 이것은 "View가 늦다"가 아니라 데이터 유실이고
심각도를 다시 봐야 한다.

### 2. A-20 — BACKLOG가 인용한 테스트가 없었다

A-20 항목은 `is_locked()`의 ops_status 쪽 근거로
`tests/test_observability.py::ReconciliationLockAwarenessTests`(4건)를 인용한다.
**그 클래스는 존재하지 않았다.** 짝인 `IsLockedTests`(lock 쪽)는 있었다.

즉 orphan 보고의 "Runner 실행 중 — 완료 후 재확인 권장" 문구와, 그 옆의 약속

> The list is NOT filtered or suppressed: a real loss hidden behind
> "probably just running" is far worse than a false alarm.

이 **아무 테스트도 없이** 살아 있었다. 뒤쪽이 중요한 절반이다 — 데이터 유실
보고가 lock 파일 하나로 조용해질 수 있는지를 결정하는 문장이다.

인용된 이름 그대로 4건을 썼다: lock 없으면 문구 없음, 살아 있는 lock이면 문구
추가, **목록은 한 글자도 짧아지지 않음**(같은 orphan 3건에 대해 lock on/off 출력이
접미사 하나만 다름을 단언), 그리고 **stale lock은 면죄부가 되지 않음**(pid가 죽은
lock이 문구를 붙인다면 crash 한 번이 그 머신의 모든 orphan 보고에 영원히 "아마
실행 중일 뿐"을 달게 된다 — BUG-42의 침묵을 다른 옷으로 입은 것).

### 3. E-17 — 기록이 코드보다 낡아 있었다

E-17의 측정 표는 "파일을 고쳐도 아무 일도 일어나지 않고, **모든 지표가 정상을
보고하는 채로** Company History에 Event 하나가 비어 있다"로 끝난다. 그 문장은
**더 이상 사실이 아니다**: `ops_status._kept_but_not_rendered()`가 정확히 그
상태를 잡아 ATTENTION에 올린다(그 함수의 docstring이 이 문장을 인용하고 있다).
실 runtime에서도 지금 그 줄이 떠 있다.

재시도 메커니즘은 여전히 없다(그쪽이 결정이다). 정정한 것은 **가시성에 대한
서술**뿐이다 — 이미 닫힌 결함을 열려 있는 것처럼 읽히게 두면 다음 사람이 같은
조사를 처음부터 다시 한다(C35가 orphan 탐지기에서 이미 한 번 겪었다).

### 4. 증거 링크 전수 — 119개 중 1개

BACKLOG의 가치는 항목마다 "무엇을 측정했고 그 측정이 어디 사는지"를 대는 데 있다.
없는 클래스를 가리키는 인용은 없느니만 못하다 — **커버리지처럼 읽히고, 옆의 항목은
검증된 것처럼 읽힌다.** E-11이 이 저장소가 이 모양에 붙인 이름이고, C38이 그 두
절반(`docs/NN §M` 포인터, backtick 파일 경로)에 울타리를 쳤다. 이것이 세 번째다.

전수: 인용된 테스트 클래스 **119개, 끊긴 것 1개**(§2). 고치고
`BacklogEvidenceLinksResolveTests`로 고정했다. 메서드 이름은 일부러 검사하지
않는다 — 같은 스윕이 33개를 잡는데 전부 오탐(모듈 이름, 식별자 중간에서 줄바꿈된
인용)이고, 실패가 대부분 소음인 검사는 사람이 끄는 법을 배우는 검사다.

### 5. 재현했고, 기록이 정확했다 (변경 없음)

- **BUG-42** — 읽기 전용 속성이 붙은 stale lock을 격리 runtime에서 실제로 만들어
  3회 연속 실행: `try_acquire_lock -> False` 세 번, 그리고
  `stale_lock_cannot_be_cleared() -> True` 세 번. 기록대로 **영구 skip이고
  탐지된다.** C23의 수정이 살아 있다.
- **E-20** — REVIEW Candidate의 관측성(미검토분 카운트 + ATTENTION, C22/C26)과
  C33의 `_reviewed_but_not_rendered()`가 모두 자리에 있다. 남은 것은 라우팅
  결정뿐.
- **E-22** — 대소문자 event_id는 `IntakeBacklog.suppressed`가 id 비교로 잡고,
  Desktop 1~3의 `derive_event_id()`가 소문자 hex만 만든다는 완화도 유효하다.

### 6. 큐 저장 실패의 "삼키는 쪽" 가지는 아무 흔적도 남기지 않았다

`app/runner.py` 4c단계: Retry Queue 저장이 실패했을 때 **원래 예외가 이미 전파
중이면 다시 raise하지 않는다.** 그 판단은 옳다 — 진짜 실패를 부기 실패로
바꿔치기하면 안 된다. 그런데 그 가지는 **로그 한 줄도 쓰지 않았다.**

대가는 실재한다: 이번 실행이 큐에 넣으려던 재시도 대상이 디스크에 없으므로
**다음 실행이 그것을 다시 시도하지 않는다.** 중단 자체는 `STEP_ABORTED`로
보고되지만 "큐 쓰기도 함께 실패했다"는 사실은 어디에도 없었고, 그 둘은 사람이
할 일이 다르다.

`RETRY_QUEUE_SAVE_FAILED entries=N <reason>`를 `notion_sync.log`에 남긴다.
**제어 흐름은 한 줄도 바뀌지 않았다** — 여전히 raise 조건은 `__context__ is
None`이고, 로그 쓰기 자체가 실패하면 그것마저 삼킨다(그 상황에서 원래 예외를
가리는 것이 훨씬 나쁘다). 두 가지 모두 테스트로 고정했다.

### 7. Line Coverage 실측 — 97.7%, 그리고 그 안에서 찾은 것

`coverage.py`가 설치돼 있지 않고 패키지 설치는 환경 변경이므로, `sys.settrace`
기반 stdlib 전용 수집기를 만들어 **전체 suite를 실제로 돌려** 측정했다:

    statements 4,083   executed 3,988   missed 95   (97.7%)

(docstring과 `global`/`nonlocal`은 제외한다 — 줄 이벤트가 발생하지 않는 문장을
세면 문서를 많이 쓴 모듈일수록 커버리지가 낮아 보인다. 첫 측정은 그 때문에
91.6%로 나왔다.)

미실행 95줄을 **전수로 읽었다.** 대부분은 정당하다: 추상 베이스의
`raise NotImplementedError`(`history/repository.py`, `collector/seen_store.py`),
POSIX 전용 `os.kill(pid, 0)` 분기(`scheduler/lock.py`), 실제 HTTP 경로
(`notion/transport.py`). **정당하지 않은 것이 하나 있었다.**

**Company History를 쓰는 writer들의 atomic-write 정리 경로가 한 번도 실행되지
않는다.**

    src/daily/generator.py     154-158, 301-307
    src/monthly/generator.py   291-295

`AtomicWriteFailureCleanupTests`가 `os.replace` 실패를 주입해 정리를 검증하는데,
그 목록은 **writer 8개**다. `mkstemp`를 스윕하면 실제 atomic writer는 **14개**이고
(2건은 주석), 빠진 쪽에 `daily/generator.py`(2개)·`monthly/generator.py`·
`monthly/state.py`·`agent/state.py`·`runsummary.py`가 있다. 8개는 그 테스트를
쓴 Sprint에서 *바뀐 소스*의 집합이었지 atomic writer의 집합이 아니었다.

**왜 하필 이 셋이 최악인가:** `daily/YYYY-MM-DD.md`와 `monthly/YYYY-MM.md`는 이
파이프라인이 존재하는 이유이고, **백업이 실어 나르는 유일한 파일**이며, Monthly
통합이 다시 읽는 파일이다. 거기 남은 `.tmp-` 파일은 `runtime/state/`의 것과
다르다 — `git add -A`가 스테이징하므로 **원격에 올라가 history에 영구히 남는다.**

셋 다 테스트를 추가했다. 둘(`update_daily_history`, `consolidate_month`)은
raise가 아니라 **결과로 보고**하므로 주입한 오류를 반환값에서 확인한다 — 누수
단언은 동일하고, 검증 대상은 누수 쪽이다. 재측정으로 154·155·158·301·302·305·307이
실행됨을 확인했다(157·304는 "정리 자체가 실패하는" 최선-노력 가지라 남는다).

테스트를 쓰다 두 번 스스로에게 걸렸고 둘 다 주석으로 남겼다:
`consolidate_month`는 달에 구멍이 있으면 **쓰기에 도달하기 전에** 거절하므로
(첫 시도는 "30 day(s) missing"으로 끝나 아무것도 staging하지 않았다) 31일을 모두
만들어야 하고, 누수 탐지기 자체가 참인지 확인하는 가드가 없으면 **staging을
아예 하지 않게 된 writer**가 모든 검사를 통과한다.

전체 Regression: **2,397 passed / 4 skipped / 1,471 subtests / 0 failed**
(Sprint 시작 시 실측 baseline 2,377).

Line coverage 실측: 수정 전 **97.7%**(미실행 95줄) -> 수정 후 **97.9%**(85줄).
`daily/generator.py`와 `monthly/generator.py`가 목록에서 사라졌다 — Company
History writer들의 정리 경로가 이제 실행된다. 남은 85줄은 전수로 읽었고 전부
정당하다: 실제 HTTP 경로(`notion/transport.py` 15), POSIX 전용 `os.kill` 분기
(`scheduler/lock.py` 8), 대화형 CLI의 `main()`(`review_cli.py` 4 — 이것을
테스트가 부르면 개발자의 실 `history_candidates/`를 건드린다), 그리고 "정리
자체가 실패하는" 최선-노력 가지들.

---

## C39. Restore Sprint — 복구 직후의 첫 실행

`docs/10` §45(Desktop 4 복구)를 검증하는 테스트는 **원격이 무엇을 돌려주는지까지**
확인하고 멈춰 있었다. 그 다음에 파이프라인을 실제로 **돌려 본 적은 없다.**
복구된 Desktop 4가 부팅하는 상태는 이렇다:

    daily/                          복구됨, 완전함
    events/, history_candidates/    없음 — 이 머신에서 다시 만들 수 없다
    runtime/state/ 전부             없음 — **watermark가 사라졌다**

즉 첫 실행은 **완성된 Company History를 들고, 그것을 쓴 기억이 전혀 없이**
시작한다. 그리고 예약 실행이므로 **아무도 보기 전에 저절로 일어난다.**

| # | 주제 | 결과 |
|---|---|---|
| 1 | 복구된 History가 첫 실행에 덮이는가 | **아니다 — 실측으로 확인, 이제 고정** |
| 2 | 그 실행이 무엇을 했다고 보고하는가 | **결함 — "5일 생성"(실제로 쓴 파일 1개)** |
| 3 | 운영자 문서에 복구 직후 항목이 없었다 | 신설 + drift 테스트 |

---

### 1. 데이터는 안전하다 (실측)

3일치 History 생성 → 디스크 전체 유실(`local_master`, `backup_working_copy`,
`runtime/` 통째로) → 원격에서 clone → 한 번 실행:

    복구된 파일        2026-08-01 … 08-04    한 바이트도 안 바뀜
    실제로 쓴 파일     2026-08-05            (복구 이후의 새 날짜 하나)
    watermark          2026-08-05 로 정상 전진
    원격               복구된 내용 그대로
    manifest           SUCCESS / exit 0

Scheduler가 날짜마다 `is_file()`을 먼저 보고 있으면 쓰지 않기 때문이다 —
`docs/07` §28(crash 후 재실행)을 위해 넣은 장치가 복구 경로도 함께 막고 있었다.
**우연히 옳았던 것이 아니라 같은 성질이 두 상황을 덮은 것**이고, 이제 그 사실이
테스트로 고정됐다(`TheFirstRunAfterARestoreTests` 4건 — 로컬 파일 불변, **원격에
빈 날이 push되지 않음**, watermark 가시성, manifest 수치).

### 2. 그런데 그 실행은 "5일을 생성했다"고 보고했다

`SchedulerRunResult.generated_dates`는 **닫힌 날짜**를 담고 있었는데 이름은
*generated*였다. 루프의 주석이 그 합침을 명시적으로 적어 두기까지 했다 —
"Either just generated, or the file already existed … either way this date is
now done". 루프에게는 맞는 말이다. 틀린 것은 **그 값을 그대로 읽어 보고하는
모든 하류**다:

    app/runner.py      manifest metric  generated_days=5
    notion/dashboard.py  Dashboard 열   Generated Days: 5
    run_company_ops.py   stdout         generated=(08-01 … 08-05)

crash 후 재실행에서는 하루 정도라 눈에 띄지 않았다. **복구 직후에는 전부다.**
그리고 하필 그 실행이, 운영자가 "복구가 제대로 됐나"를 확인하며 가장 주의 깊게
보는 실행이다 — 그 자리에서 시스템이 **가장 큰 활동량을, 그것도 "생성"이라고**
보고했다. 파이프라인은 그 4일을 생성할 수 없다(History Candidate는 백업에 없다,
`docs/08` §26). 존재할 수 없는 일을 했다고 보고한 것이다.

**수정:** `generated_dates`(이번 실행이 쓴 날) / `reused_dates`(이미 있어서
그대로 둔 날)로 쪼개고, 둘의 합집합은 `closed_dates` 프로퍼티로 남겼다.
watermark 전진 규칙은 **한 줄도 바뀌지 않았다** — 닫힌 날은 여전히 닫힌 날이다.
C32가 `Notion Synced`에서 `Notion Skipped`를 떼어낸 것과 같은 수술이고, 같은
이유다: 일어나지 않은 쓰기를 보고하고 있었다.

**세 개의 테스트가 이 합침을 "정상"으로 고정하고 있었고, 셋 다 자기 주석에서
이미 진실을 알고 있었다:**

| 테스트 | 주석이 이미 하던 말 | 단언하던 것 |
|---|---|---|
| `test_a_daily_file_written_before_a_crash_is_reused_not_rewritten` | 바로 윗줄에서 `daily_snapshot() == before` (아무 파일도 안 바뀜) | `generated_dates == [08-03, 08-04]` |
| `test_full_pipeline_two_days_of_real_work` | "08-01 was **adopted** (pre-existing file, not recreated)" | 둘 다 generated |
| `test_pre_existing_daily_file_is_treated_as_already_done` | "must not error out on an already-generated date" | 둘 다 generated |

셋 다 **약화가 아니라 강화**로 고쳤다 — 이제 어느 날짜를 썼고 어느 날짜를
물려받았는지까지 단언하며, `closed_dates`가 예전 단일 tuple이 담던 합집합을
그대로 지킨다.

### 3. 운영자 문서 §6a-3

`AGENT.md`에 **6a-3. Backup에서 복구한 직후 첫 실행**을 넣었다 — 무엇이
돌아오고 무엇이 돌아오지 않는지, 실측 표, 그리고 읽는 법 한 줄:

> 복구 직후에는 `reused`가 크고 `generated`가 작은 것이 정상이며, 그 반대라면
> — 복구한 날짜를 다시 만들고 있다는 뜻이므로 — 즉시 멈추고 원격을 확인해야
> 한다.

C35의 §6a-2와 같이 **문서는 코드에 대한 주장이므로** drift 테스트를 붙였다
(`RestoreSectionMatchesTheCodeTests`): 문서가 비교하라는 두 단어가 실제로
찍히는 단어인지, 결과 객체에 두 필드가 다 있는지, Scheduler가 여전히 쓰기 전에
`is_file()`을 보는지(이게 무너지면 표 전체가 가장 비싼 방향으로 거짓이 된다),
그리고 표가 말하는 백업 범위가 실제 백업 범위인지.

전체 Regression: **2,374 passed / 4 skipped / 1,468 subtests / 0 failed**
(Sprint 시작 시 실측 baseline 2,366).

---

## C38. Audit Rotation Sprint — 테스트 스위트 자신이 조용히 통과하고 있었다

C35~C37이 파이프라인을 봤다면 C38은 **감사 도구 자신**을 본다. 그리고 이 저장소가
가장 싫어하는 모양이 테스트 디렉터리 안에 있었다: **초록색으로 통과하는 침묵.**

| # | 주제 | 결과 |
|---|---|---|
| 1 | `unittest.main()` 아래에 테스트 **760건**이 숨어 있었다 | **결함 (P1) — 20개 파일 수정** |
| 2 | 그 아래에서 pytest 밖에서는 아예 못 도는 테스트 15건 | **결함 — 5개 파일 수정** |
| 3 | "두 복사본이 일치한다고 테스트가 단언한다"는 주석의 거짓 | **결함 (E-11 계열) — 테스트 신설** |
| 4 | 문서가 가리키는 저장소 경로 2건이 존재하지 않았다 | **결함 — 수정 + 고정** |
| 5 | `ops_status`가 `processed/`를 두 번 읽는다 → 2배 비용? | **가설 반증** — 3%. 구현 후 되돌림 |
| 6 | TODO/FIXME/HACK/XXX, `__all__` 정합성, 패키지 import | **전부 결함 0건** |

---

### 1. 760건이 초록색 아래 숨어 있었다 (P1)

`if __name__ == "__main__": unittest.main()`은 **쓰여 있는 자리에서 실행된다.**
54개 테스트 파일 중 **20개**가 그 줄을 파일 중간에 두고 있었고, 그 아래에
테스트 메서드 **760건**이 정의돼 있었다. 실측:

    python tests/test_observability.py     Ran  44 tests ... OK
    pytest tests/test_observability.py     411 passed

**44건을 돌고 OK를 찍는다.** 파일의 11%다. 최악 5개:

| 파일 | 숨은 건수 | 돌던 건수 |
|---|---|---|
| `test_observability.py` | 351 | 44 |
| `test_architecture_invariants.py` | 71 | 81 |
| `test_runner_failure_paths.py` | 50 | 176 |
| `test_monthly_history.py` | 37 | 79 |
| `test_runner_notion_integration.py` | 36 | 36 |

**아무것도 깨져 있지 않았다** — 스위트는 pytest로 돌고, pytest는 모듈을
import할 뿐 저 줄에 도달하지 않는다. 그래서 오래 살아남았다. 그러나 파일 하나를
직접 돌려 보는 것은 개발 중 가장 흔한 동작이고, 그때 나오는 `OK`는 **커버리지처럼
읽히는 침묵**이다 — 이 저장소가 파이프라인 전체에서 쫓아 온 바로 그 모양이,
그것을 잡으라고 있는 테스트 안에 있었다.

20개 파일 전부 guard를 파일 끝으로 옮겼다. 고정: `NoTestFileHidesTestsBelowItsMainGuardTests`.

### 2. 옮기자마자 두 번째가 드러났다

Guard를 내리니 직접 실행이 파일 전체를 돌기 시작했고, 그러자 **15건이
`ModuleNotFoundError: No module named 'ops_status'`로 죽었다**
(`test_observability.py` 10건, `test_monthly_history.py` 5건).

원인: 모든 테스트 파일이 `src/`를 `sys.path`에 넣지만 **저장소 루트는 넣지
않는다.** `ops_status.py`·`review_cli.py`는 `src/` 옆에 있다. pytest는 rootdir을
스스로 넣어 주므로 이 누락은 pytest 아래에서 영원히 보이지 않는다.

루트 스크립트를 import하는 5개 파일 헤더를 고쳤다. 이후 20개 파일 전부
직접 실행 OK(가장 무거운 셋: `test_runner_failure_paths` 226건/71s,
`test_runner_notion_integration` 72건/46s, `test_untrusted_event_input` 89건).

### 3. 주석이 약속한 테스트가 없었다 (E-11 계열, **보안 인접**)

레이어링 때문에 일부러 두 벌로 두는 규칙이 셋 있다. 둘은 복사본을 비교하는
테스트가 있었고, **없는 하나가 하필 "있다"고 적어 둔 쪽이었다**:

| 규칙 | 복사본 | 실제 |
|---|---|---|
| `INCOMPLETE_WRITE_PREFIX` | 4 | `IncompleteWriteInvariantTests`가 파일마다 리터럴을 뽑아 비교 ✓ |
| `safe_event_filename()` | 2 | docstring: "tests assert the two copies agree" — **그런 테스트 없음** |
| `_is_sole_identifier()` | 2 | docstring: "Mirrors …" — **산문뿐** |

`safe_event_filename()`은 가벼운 함수가 아니다. **경로 traversal 결함
(BUG-15)** 때문에 생겼고 Windows 경로 길이 실패(WinError 123)를 막는 상한을
들고 있으며, 두 복사본은 각각 **Agent가 쓰는 이름**과 **OneDrive가 Desktop 4로
나르는 이름**을 정한다. 갈라지면 같은 Event가 두 이름으로 쓰이고 한쪽 이름으로
하는 모든 조회가 빗나간다.

AST 기준으로 두 본문이 지금은 동일함을 실측했고, **행동으로** 비교하는 테스트를
신설했다(형식이 아니라 답이 같아야 한다는 것이 주장이므로). 입력은 이 저장소에서
실제로 문제가 됐던 모양들이다 — `../target/X`, `..\target\X`, `a:b`(NTFS 스트림),
`E`×250, 경계값 119/120/121, 빈 문자열, 개행, 한글.

같은 클래스가 두 가지를 더 단언한다: 규칙 자체가 여전히 **분리자를 남기지 않고
길이를 묶는지**(동일하지만 틀린 두 복사본은 비교만으로는 통과한다), 그리고
**서로 다른 id가 한 파일명으로 겹치지 않는지**(sanitise와 truncate는 둘 다
다대일이라, 일치보다 이쪽이 먼저 무너진다).

### 4. 문서가 없는 파일을 가리키고 있었다

문서의 backtick 경로 **186건**을 전수 확인: 2건이 존재하지 않았다 —
`AGENT.md`의 `scheduler/scheduler.py`와 `scheduler/lock.py`(실제로는
`src/scheduler/…`, 같은 문서가 다른 곳에서는 `src/reporter/profiles.py`로 쓴다).
고치고 `DocumentPathsResolveTests`로 고정했다. C36의
`DocumentPointersResolveTests`(§ 포인터 89건)와 같은 렌즈, 다른 절반이다.

문서가 부르는 함수 이름 13건도 확인 — 전부 존재한다(유일한 미해결은
`str.splitlines()`, builtin).

### 5. "두 번 읽으니 두 배"는 틀렸다 — 구현하고, 재고, 되돌렸다

`ops_status.py` 한 번 실행에서 `processed/`를 두 번 완독한다는 것은 사실이다
(COMPANY 블록의 `read_company_activity()`, HISTORY 블록의
`find_orphaned_events()`). 블록별 실측이 6.69s / 7.46s로 거의 같아서 2배로
보였다. **아니었다:**

    처음 읽기(cold)            5.09 s   (6,000건)
    같은 것을 다시(warm)        0.43 s
    find_orphaned 자기 읽기     0.40 s
    find_orphaned 넘겨받았을 때 0.02 s

OS 페이지 캐시가 두 번째를 흡수한다. 공유 읽기를 **실제로 구현하고**
순서를 번갈아 3회 측정했다: 10.28s → 9.99s, **3%**. `app` → `history` 경계를
가로지르는 매개변수를, 그것도 **이미 두 번 사각을 막아야 했던 유실 탐지기**에
넣는 대가로는 맞지 않아 되돌렸다. 진짜 비용은 **줄어들지 않는 디렉터리를 처음
한 번 훑는 것**이고 그것은 B-6(보존 정책) 결정이지 최적화가 아니다.

숫자와 결론을 `find_orphaned_events()` docstring에 남겼다 — 다음 사람이 같은
2배 가설을 세우고 같은 3%를 발견하지 않도록. (C27이 warm/cold 순서 때문에 16배
틀린 수치를 낼 뻔했던 것과 같은 함정이고, 이번엔 그 기록 덕에 빨리 알아챘다.)

### 6. 결함 0건으로 닫은 감사 축

- **TODO / FIXME / HACK / XXX**: 전 저장소 4건, 전부 문서 예시·테스트 데이터.
  실제 표식 **0건**.
- **`__all__` 정합성**: 로컬에 정의되지 않은 export **0건**. 10개 패키지 전부
  clean import.
- **중복 본문 스윕**: 다중 문장 함수 본문이 동일한 쌍 **2건**뿐이고 둘 다
  의도적·문서화된 복사본(위 §3에서 테스트를 붙였다).
- **SKIP 4건**: 전부 `symlink creation not permitted in this environment`
  (`test_agent.py` 2, `test_untrusted_event_input.py` 2). Windows에서 symlink
  생성은 개발자 모드나 관리자 권한이 필요하다 — 환경 제약이며 숨긴 문제가
  아니다. 해제 조건: Windows 개발자 모드.

### C35 재확인 (반복하지 않음)

이 Sprint의 지시는 C35 중단 지점부터 이어가는 것이었으나, 현재 상태를 먼저
확인한 결과 **C35는 C36·C37과 함께 이미 닫혀 있었다**(BACKLOG 해당 절, 그리고
`abort → 재실행` 7건·`TheTwoVerdictsAboutOneRunTests` 등 테스트로 고정). 10개
점검 항목을 기존 커버리지와 대조했고 — 부분 산출물 해석, watermark, 유실/재처리,
Multi-Desktop 격리(`DesktopFaultIsolationTests`, `CrashPointRecoveryTests`),
복구 후 뒤틀림 — 새로 열 구멍이 없어 같은 조사를 반복하는 대신 감사 로테이션으로
넘어갔다.

Live runtime은 읽기 전용으로만 확인했다(`ops_status.py` 1회). 과거 Sprint가
남긴 fault-injection 잔재(`fi-*.json`, `MD-*`, `PUSHFAIL-PROBE-*` 등, 2026-08-05
및 08-11 생성)가 실 runtime에 남아 있으나 **삭제하지 않았다** — Event 삭제는
데이터 파괴이고, `ops_status`가 그 상태를 정확히 보고하고 있다(ORPHANED_EVENT,
KEEP 미반영). 이 Sprint에서 실 runtime에 쓴 것은 없다.

전체 Regression: **2,366 passed / 4 skipped / 1,460 subtests / 0 failed**
(Sprint 시작 시 직접 실측한 baseline 2,355).

---

## C37. Two Verdicts Sprint

한 실행에 대해 **판정을 두 번** 내리는 곳이 있다. Run Manifest
(`last_run.json`, Task Scheduler와 `ops_status.py`가 읽는다)와 Operations
Dashboard의 `Overall` 열(사람이 Notion에서 보는 것). 둘을 이어 주는 것이
아무것도 없었고, **양쪽 방향 모두로 어긋나 있었다.**

| # | 주제 | 결과 |
|---|---|---|
| 1 | 파일 하나 실패에 Dashboard가 FAIL을 외쳤다 | **결함 (늑대 소년) — 수정** |
| 2 | DEGRADED 실행이 Dashboard에서 OK로 보였다 | **결함 (숨은 고장) — 수정** |
| 3 | 두 판정이 어긋날 수 있다는 것 자체 | 구조적으로 고정 |

---

### 1~2. docs/14 §4가 이름을 붙여 둔 두 실패를 한꺼번에 하고 있었다

docs/14 §4는 Overall Status를 정의하면서 한 문장으로 경고한다:

> `DEGRADED`를 SUCCESS로 접으면 실제 고장이 숨고, FAILED로 접으면 늑대 소년이
> 되어 아무도 안 본다.

Dashboard 행이 **둘 다** 하고 있었다. 실측:

    collector failed=1     Dashboard FAIL   manifest SUCCESS  / exit 0
    late_update FAILED     Dashboard OK     manifest DEGRADED / exit 3
    monthly     FAILED     Dashboard OK     manifest DEGRADED / exit 3

**늑대 쪽.** `failed`는 Event **파일** 수다. `app/runner.py`는 바로 그 숫자
옆에서 반대를 말한다 — "not a component failure: docs/03 §53 makes per-file
isolation the design, and one malformed Event must not make an ordinary run
look broken" — 그리고 실행을 SUCCESS / exit 0으로 기록한다. Dashboard는 같은
사실을 **Daily Close 유실과 같은 등급**으로 올렸다. 이제 WARN이다 — 형제
격인 `rejected`가 처음부터 갖고 있던 등급이고, "이 행은 사람이 봐야 한다"가
뜻하는 등급이다.

**숨은 고장 쪽.** 이건 실수가 아니라 **구조**였다. `late_update`와 `monthly`는
아홉 단계 중 **예외를 던지지 않고 FAILED를 기록할 수 있는 딱 두 단계**이고
(나머지는 실패하면 run이 중단돼 애초에 행이 써지지 않는다), 둘 다 Dashboard에
열이 없었다. 열이 없으니 판정 함수에 **닿을 수조차 없었다.** 그래서 exit 3인
실행의 행이 `Overall OK`였다.

**추가한 것:** `Failed Steps`(Rich Text, 18번째 열) — 이번 실행이 FAILED로
기록한 단계 이름들. Runner가 manifest recorder에서 그대로 꺼내 넘긴다(두 번째
판단이 아니라 **같은 판단의 전달**). 개수가 아니라 이름인 이유는 WARN이 부르는
다음 질문이 "어느 단계냐"이고, 같은 눈길 안에 들어가기 때문이다.

이것으로 **부류를 고쳤지 두 사례를 고친 게 아니다** — 나중에 추가되는 단계는
누가 전용 숫자 열을 기억해 주지 않아도 자동으로 접힌다.

### 3. 두 판정의 관계를 방향까지 못박았다

둘은 같은 판정이 **아니다.** Dashboard 행은 사람이 훑는 줄이라, 실행을
degrade시키지 않는 행 단위 사실(REJECTED 8건, 안 빠지는 큐)에도 WARN을 준다.
그래서 WARN은 DEGRADED보다 넓다. 못박은 것은 **일방향 관계**다:

    Dashboard OK       => manifest SUCCESS      (더 조용할 수 없다)
    manifest DEGRADED  => WARN이거나 FAIL, 절대 OK 아님
    Dashboard FAIL    <=> manifest FAILED       (CRITICAL 단계가 실패했다)

`app.runner._SEVERITY`를 **복사하지 않고 읽어서** `PIPELINE_COMPONENTS`의 모든
단계에 대해 검사한다. 나중에 단계가 늘면 그날 바로 덮인다. `dashboard` 자신만
제외한다 — 쓰지 못한 행에 자기 실패를 적을 수는 없고, 그 부재는 manifest의
`dashboard` component와 pending 큐가 따로 보고한다.

### 나머지 판정 쌍은 전수로 확인했고, 결함 없음

"같은 실행/상태를 두 번 판정하는 곳"을 전부 훑었다. 기록해 두는 이유는 C35와
같다 — 다음에 같은 조사를 처음부터 다시 하지 않기 위해서다.

- **프로세스 exit code vs Manifest** — 이미 닫혀 있다. `run_company_ops.py`가
  `summary.exit_code`를 그대로 돌려주고, 주석이 그 이유를 적고 있다("Two
  answers to 'how bad was this run' is one too many"). Backup 실패 경로도
  하드코딩 2가 아니라 manifest를 읽는다.
- **`ops_status` exit 3 vs Runner exit 3** — RETRYABLE만 실패한 DEGRADED 실행
  직후 Runner는 3, `ops_status`는 0이다. **결함이 아니라 docs/14 §5의 분리
  그 자체다**: Severity가 exit code를, Retryability가 "지금 사람이 움직여야
  하는가"를 결정한다. `ops_status`는 뒤의 질문에 답하고, 그 사이 다음 실행이
  재시도해 스스로 풀렸을 수 있다. 종합 상태 DEGRADED는 화면에 그대로 찍히므로
  숨지도 않는다. (`overall_status is FAILED and not attention`에 대한 보강은
  이미 들어가 있다.)
- **`scheduler/consistency.py` vs `history/reconciliation.py`** — 같은 대상에
  대한 두 탐지기처럼 보이지만 서로 다른 질문이다(state ↔ 파일 / Candidate ↔
  렌더링된 History). 둘 다 보고되고 서로를 덮지 않는다.

### 부수 정리

- `docs/13`이 "열이 열일곱 개"라고 적고 있었다. C31 13 → C32 15 → C33 17 →
  C37 18로 실제로 자라 왔으므로 **숫자를 지웠다**(C35가 test count에 내린 것과
  같은 판단: 정본 밖에 다시 적은 수치는 기억해야 할 자리가 하나 더 느는 것이고,
  기억되지 않는다). 자란 이력 자체는 날짜가 박힌 기록이라 남긴다.
- C36의 마이그레이션 픽스처(`_C32_C33_ADDITIONS`)를
  `_COLUMNS_ADDED_AFTER_C31`로 넓혔다. 이 목록이 자라는 것이 곧
  `bootstrap_dashboard_properties()`가 일회성 스크립트가 아니어야 하는 이유다 —
  C37이 그 첫 증거다.

**Evidence:** `tests/test_architecture_invariants.py::TheTwoVerdictsAboutOneRunTests`(5건,
subtest 8건 — `dashboard`를 뺀 모든 단계),
`tests/test_runner_notion_integration.py::DegradedStepDoesNotAbortCriticalStepsTests::test_the_dashboard_row_does_not_call_this_run_ok`(실제
파이프라인 끝까지 돌려 행을 읽는다),
`tests/test_notion_dashboard.py::OverallVerdictAgreesWithItsOwnColumnsTests::test_a_collector_file_failure_warns_rather_than_failing`.

마지막 것은 **기존 단언을 바꾼 자리**라 그 자리에 이유를 적어 두었다. 약화가
아니다: 옛 단언은 명세에 없는 규칙(파일 하나 실패 = 실행 실패)을 굳히고
있었고, 같은 클래스의 `test_failure_still_outranks_warning`이 진짜 실패
사례로 같은 요점을 계속 지킨다.

전체 Regression: **2,355 passed / 4 skipped / 1,410 subtests / 0 failed**
(Sprint 시작 시 실측 baseline 2,348).

---

## C36. Schema Migration Sprint

C32와 C33은 Dashboard가 사실을 말하게 하려고 `OPS_RUNS`에 열을 넷 더했다.
**그 넷을 더하면서, 이미 Database를 만들어 둔 운영자를 남겨두고 왔다.**

| # | 주제 | 결과 |
|---|---|---|
| 1 | 스키마가 자란 뒤 기존 Database를 고칠 방법이 없었다 | **결함 (P1) — 수정** |
| 2 | 같은 질문("어느 Property가 빠졌나")을 두 번 구현할 뻔했다 | 기존 모듈을 매개변수화해서 재사용 |
| 3 | 운영자가 실제로 읽는 줄이 출구를 가리키지 않았다 | `ops_status` ATTENTION + docs/13 §3-⑧-4 |
| 4 | 코드가 문서를 가리키는 89개 포인터를 아무도 검사하지 않았다 | 결함 0건 — 그대로 고정 |

---

### 1. 열 넷을 더하면서 남기고 온 사람 (P1)

`bootstrap_dashboard_databases()`는 `DASHBOARD_DATABASES`대로 `OPS_RUNS`를
만든다. 그 스키마는 **자라 왔다** — C31까지 13열, C32에서 15열
(`Transport Blocked`, `Notion Skipped`), C33에서 17열(`Notion Unreadable`,
`Notion Queued`). 넷 다 "Dashboard가 달리 말할 수 없던 것"을 말하려고 더한
열이고, 넷 다 옳다.

문제는 **먼저 만든 Database는 자라지 않는다**는 것이다. C31 시점에 ⑧-2를
실행해 둔 운영자의 Database에는 그 열이 없고, `record_run()`은 Notion이 들어본
적 없는 Property를 보낸다 — **매 실행 400, 영원히.**

실패 자체는 안전하다. C32 §11이 이미 실측해 둔 대로 행은
`dashboard_pending.json`에 쌓이고 `DASHBOARD DRAIN_PENDING … REASON <Notion의
설명>`이 남는다. 데이터는 잃지 않고 이유도 읽을 수 있다. **없던 것은 빠져나올
길이었다** — 어느 Property가 거절당했는지 정확히 읽고도, 그것을 추가할 명령이
없었다.

**추가한 것:** `bootstrap_dashboard_properties(client)`. 없는 Property만
만들고 기존 Property는 정의째 그대로 둔다(옵션을 설정해 둔 Select도 안전하다 —
목표 payload가 빈 `{"select": {}}`라 덮어쓰면 옵션이 사라진다). Title이 Notion
기본값 `Name`이면 `Run ID`로 rename한다. Notion API는 두 번째 Title을 만들 수
없으므로 손으로 만든 Database에서는 rename이 **유일한** 길이고, 이것은
`notion.bootstrap`이 PROJECTS에 대해 V1.1부터 하던 예외를 새로 결정하지 않고
그대로 쓴 것이다.

**어디에도 배선하지 않았다.** `test_the_setup_cli_does_not_create_anything_from_the_diagnosis`가
"`init_notion.py`는 진단만 하고 실 Workspace의 Dashboard를 건드리지 않는다"를
고정하고 있고, 그 고정은 옳다. 운영자가 직접 부르는 명령이며 docs/13 §3-⑧-4에
그대로 붙여 쓸 수 있는 형태로 적었다(실측으로 출력까지 확인). 따라서 C31 §17의
dead-capability 목록에 **이유와 함께** 등록했다 — 이 목록에 이유 없이 이름이
느는 것이 그 테스트가 막는 바로 그 일이다.

### 2. 두 번째 구현을 하지 않았다

`OPS_RUNS`도 결국 "어느 Property가 빠져 있는가"를 묻는다. `notion.bootstrap`이
PROJECTS에 대해 이미 답하고 있고, **같은 질문에 대한 두 개의 구현은 두 답이
갈라지는 방법이다.** 그래서 새로 쓰는 대신 `diff_properties()`와
`_bootstrap_title_property()`에 `targets` / `title_property` 매개변수를
붙였다. 기본값은 §8의 PROJECTS 그대로라 기존 호출자는 한 글자도 바뀌지 않는다.

그 재사용이 공짜로 가져온 것 하나: **rename 후 재조회.** Title이 다른 Target
이름을 쓰고 있었다면(예: Title이 `Overall`) rename이 그 이름을 비우므로,
rename 이전 snapshot으로 diff하면 `Overall`을 "이미 있음"으로 보고 **매 행이
쓰는 Select 열을 만들지 않는다.** `bootstrap_database()`가 PROJECTS에 대해
이미 겪고 고쳐 둔 함정이고, 실측으로 재현했다:

    재조회 없음   Overall 생성? False
    재조회 있음   Overall 생성? True

### 3. 운영자가 읽는 줄이 출구를 가리키게

`ops_status`는 Dashboard 기록이 열흘 넘게 밀리면 ATTENTION에 올리고
"notion_sync.log의 `DASHBOARD DRAIN_PENDING ... REASON`을 확인해야 한다"까지
말했다. 이유는 이미 읽을 수 있었다. 이제 **가장 흔한 원인과 그것을 고치는
명령의 위치**까지 같은 줄에서 말한다.

### 4. 코드가 문서를 가리키는 89개 포인터 (결함 0건)

이 저장소의 주석은 근거를 **참조로** 나른다 — `src/`와 루트 스크립트에
`docs/NN §M` 형태가 89개 있고, 코드 한 줄에서 그 결정까지 가는 주된 통로다.
번호가 밀리거나 사라진 섹션을 가리키는 포인터는 없는 것보다 나쁘다: 읽는 사람을
엉뚱한 문단으로 보내면서 내내 권위처럼 읽힌다. E-11("코드보다 오래 산 주석의
주장")과 같은 렌즈를, 숫자가 아니라 **상호 참조**에 겨눈 것이다.

전수 조사: **89개 전부 해결된다.** 결함 0건이므로 고칠 것은 없고, 이미 갖고
있는 성질에 울타리만 쳤다(`DocumentPointersResolveTests`). 범위 형태
(`§50-51`)는 포인터 둘로 세고, 하위 항목(`§3-⑧-4`의 `-⑧-4`)은 검사하지
않는다 — 문서마다 산문으로 다르게 매기고, 밀리는 것은 섹션 번호 쪽이다.

**Evidence:** `tests/test_notion_dashboard.py::DashboardPropertyBootstrapTests`(9건),
`::DashboardMigrationClosesTheLoopTests`(2건),
`tests/test_repository_hygiene.py::DocumentPointersResolveTests`(2건),
`tests/test_observability.py::...::test_the_stuck_record_line_names_the_command_that_fixes_it`.

`DashboardMigrationClosesTheLoopTests`는 `_SchemaEnforcingTransport`를 쓴다 —
실제 Notion이 하는 일 중 in-memory double이 하지 않던 딱 하나, "정의되지 않은
Property를 담은 page 쓰기를 400으로 거절한다"를 더한 것이다. 그것 없이는 이
파일의 모든 테스트가 **열이 절반 빠진 Database 앞에서도 통과한다** — 즉 C36이
막으려는 상황을, 다른 모든 테스트가 쓰는 double로는 재현할 수 없었다.

전체 Regression: **2,348 passed / 4 skipped / 1,398 subtests / 0 failed**
(Sprint 시작 시 실측 baseline 2,334).

---

## C35. Cross-Run Sprint

C34는 **한 실행 안**의 순서를 검증했다. C35는 실행과 실행 **사이**를 본다:
run N이 도중에 죽었을 때 run N+1이 그 잔해를 무엇으로 읽는가.

Baseline (이번 Sprint 시작 시 실측): 2,320 passed / 4 skipped / 1,381 subtests.
최종: **2,334 passed / 0 failed / 1,395 subtests**.

**결론부터: 신규 결함 0건.** 네 Sprint 연속 매번 결함이 나온 뒤 처음이라, 그
자체를 결과로 기록한다. 대신 **커버리지 구멍 하나**를 닫았다 — 그리고 그 구멍은
작지 않았다.

| # | 주제 | 결과 |
|---|---|---|
| 1 | abort → 재실행 테스트가 없었다 | **커버리지 구멍 (신설 7건)** |
| 2 | 각 단계 abort 후의 복구 가능성 | **전수 실측** — 하나만 복구 불가, 그것은 탐지됨 |
| 3 | Monthly의 두 watermark | 결함 없음 (실측) |
| 4 | stale lock / 중복 Event / Agent 재전송 | 결함 없음 (실측 + 기존 커버리지 확인) |
| 5 | orphan 탐지기의 사각 | **이미 특성화돼 있었다** — 같은 조사를 다시 함 |
| 6 | 운영자 문서에 abort 복구표가 없었다 | **신설 + drift 테스트** |

---

### 1. `success → 재실행`은 덮여 있었고 `abort → 재실행`은 아니었다

`WholePipelineIdempotencyTests`가 **성공 후 재실행**을 촘촘히 덮는다 — "두 번째
동일 실행은 중요한 것을 아무것도 바꾸지 않는다", 다섯 테스트, 전 트리 해시 비교.

**중단 후 재실행은 어디에도 없었다.** 그리고 그쪽이 운영자가 실제로 만나는
경우다 — 예약된 Runner는 지난번에 무슨 일이 있었든 다음 트리거에 그냥 다시 돈다.

두 절반은 단언해야 할 성질이 다르다. 성공 뒤에는 "아무것도 안 바뀐다"이고,
중단 뒤에는 run N이 이미 **일부** 산출물을 써 놓았으므로 둘 중 하나여야 한다:

    복구됨   run N+1이 run N이 시작한 일을 끝낸다. 이미 된 부분을 중복하지 않고.
    탐지됨   복구 불가능하고, 무언가가 그 사실을 말한다. 실패 모드는 유실이
             아니라 **침묵**이다.

단계마다 남기는 반쪽이 다르므로 단계별로 썼다(7건). step 5는 소비됐지만
Candidate 없는 Event를, step 6은 Candidate만, step 7은 백업 안 된 History를
남긴다.

### 2. 단계별 abort → 재실행 전수 실측

| 중단 단계 | 남는 것 | 다음 실행 | 확인 |
|---|---|---|---|
| `notion_sync` | Retry Queue의 Event | 복구 (4a가 큐부터) | ✓ |
| `history_filter` | 소비됐고 Candidate 없음 | **복구 불가** | 탐지됨 |
| `daily` | Candidate만 | 복구 (렌더링) | ✓ 중복 없음 |
| `backup` | History 있고 push 안 됨 | 복구 (같은 commit 재push) | ✓ |
| lock 쥔 채 crash | pid 죽은 lock | 인수 | ✓ |

`history_filter`만 복구되지 않는다 — A-20의 창이다. Collector가 이미 소비했고
(파일이 `processed/`로, id가 seen store로) 어떤 실행도 다시 보지 않는다. 요구
사항은 복구가 아니라 **탐지**이고, 실측으로 확인했다:

    run 1  FAILED/2, STEP_ABORTED history_filter
    run 2  SUCCESS/0, EVT가 Company History에 없음
    ops_status  "! 수집됐지만 History에 들어가지 못한 Event 1건: EVT-H5 —
                 재실행으로 복구되지 않는다(BACKLOG A-20)"

파일이 **읽히지 않게** 된 변형에서는 다른 줄이 뜬다 — "판단할 수 없다". 그
구분(잃었다 vs 판단 불가)이 유지되는 것도 함께 고정했다.

**모순처럼 보이는 조합은 모순이 아니다**: run N이 FAILED였는데 run N+1이
SUCCESS이고 그 사이 Event 하나가 사라졌다. Run Manifest는 `last_run.json`
**한 파일**이라 run N의 기록이 덮인다(이름 그대로의 설계). ATTENTION 쪽이 더
오래 사는 신호이고, 실제로 그것이 남아서 말한다.

### 3~4. 결함 없음으로 확인한 것들 (실측)

- **Monthly의 두 watermark.** Monthly 파일을 쓰고 state 저장 전에 죽으면 →
  다음 실행 `MONTHLY_UNCHANGED`, 포인터 전진, **dirty는 안 지움**(generator가
  명시적으로 처리하는 그 경우). dirty가 crash를 넘어 살아남으면 → 다음 실행
  `MONTHLY_UPDATED`, Late Event가 Monthly에 도달. 둘 다 실측 확인.
- **step 6의 `generated_dates`가 dirty로 표시되지 않는 것**은 결함이 아니다.
  `check_coverage`가 달의 **모든 날**을 요구하므로, 이미 통합된 달에 새 날이
  생길 수 없다. (C34에서 의심하고 C35에서 코드로 닫음.)
- **stale lock**: pid가 죽은 lock은 다음 실행이 인수한다. (처음 실측했을 때
  거부된 것은 harness가 *살아 있는* 자기 pid를 썼기 때문 — 측정 도구의 문제.)
- **같은 event_id 재전달**: intake의 `already_elsewhere`가 막고, 그것을 우회해
  `incoming/`에 직접 넣으면 BUG-43(`name_collision`)이 되며 이미 계수·보고된다.
- **Agent 재전송**: `test_a_crash_between_send_and_filing_produces_no_duplicate_history`
  등이 이미 덮고 있다.

### 5. orphan 탐지기의 사각 — 같은 조사를 두 번 했다 (기록)

`find_orphaned_events()`가 `processed/`를 세계로 삼으므로, 그 디렉터리를 정리하면
탐지가 조용히 꺼진다는 것을 실측했다:

    파일 있음   orphan -> ['EVT-PRUNE']
    파일 지움   orphan -> []          (seen store는 여전히 EVT-PRUNE를 안다)

그리고 이것은 **이미 `RetentionErasesTheEvidenceOfALossTests`가 같은 측정과
같은 논증(왜 seen store로 대체할 수 없는지 — DROP Event가 정상적으로 Candidate가
없다)으로 특성화해 두었고 B-6에 묶여 있었다.**

신규 없음. **같은 조사를 세 번째로 하지 않기 위해** 렌즈와 결과를 여기 남긴다.

### 6. "지난 실행이 중단됐다면" — 운영자 문서에 없었다

`docs/11` §23 Crash 검증은 세 줄이고 배포 시점의 *테스트 절차*다. 어느 단계에서
죽으면 무엇이 남고 다음 실행이 이어받는지 말하는 곳은 없었다.

C35가 그것을 전부 실측했으므로 `AGENT.md`에 **§6a-2**로 표를 넣었다. 그리고
표는 코드에 대한 주장이므로 drift 테스트를 붙였다 —
`PIPELINE_COMPONENTS`의 모든 단계가 표에 있는가, `history_filter`가 여전히
유일한 복구 불가 단계인가, 인용한 두 ATTENTION 문구가 `ops_status.py`가 실제로
찍는 문자열인가, `last_run.json`이 정말 한 파일인가.

그 테스트가 즉시 두 개를 잡았다: `dashboard` 행 누락, 그리고 인용문이 줄바꿈으로
쪼개져 실제 문자열과 다른 것. 둘 다 고쳤다.

### 7. 문서에 얼어붙은 테스트 숫자 (신규, 문서)

문서 감사에서 나왔다. `docs/13`이 **"2244 passed"를 두 곳에** 담고 있었다 — 세
Sprint 전 숫자이고, 그 사이 스위트는 2,300을 넘었다. 둘 다 Release 준비 상태를
주장하는 줄이라 낡은 숫자가 실제로 일을 하고 있었다: 체크리스트를 확인하는
사람이 지금 돌린 결과와 비교하면 어느 쪽이 맞는지 알 방법이 없다.

(그 두 줄은 C32에서 **내가** 썼다.)

**숫자를 갱신하지 않고 없앴다.** C33 §2가 docstring의 property 개수에 이미 적용한
이유 그대로다 — 만들어 내는 곳 밖에 다시 적힌 수치는 기억해야 할 자리가 하나 더
느는 것이고, 기억되지 않는다. 대신 재는 명령을 적었다.

그리고 다음 사람이 같은 일을 하지 못하도록 가드를 붙였다:
**live 문서(README·AGENT·`docs/`)에는 pytest pass 수를 적을 수 없다.**
`BACKLOG.md`는 의도적으로 면제다 — Sprint별 줄은 날짜가 붙은 *역사 기록*이고,
시점에 고정돼 있는 것이 바로 그 값어치다. 이 테스트가 가르는 것은
**스위트가 무엇인가**(고정 금지)와 **어느 날 무엇이었나**(고정 필수)의 차이다.

### 8. 테스트 감사 — 죽은 테스트 0건 (기록)

`tests/` 전체를 AST로 훑어 **단언이 하나도 없는 test 메서드**를 찾았다. 8건이
나왔고 전수 확인한 결과 **전부 정당했다**:

    5건   "예외가 나지 않아야 한다"가 곧 단언인 테스트
    2건   단언이 helper 안에 있다(`with self.assertRaises(...)`) — 탐지기가
          helper 호출을 따라가지 않아 생긴 오탐
    1건   `ExplodingRepository`가 호출되면 `AssertionError`를 던진다 —
          "호출되지 않아야 한다"가 단언

리터럴에 대한 단언(`assertTrue(True)` 부류)은 **0건**.

성능도 함께 쟀다: 가장 느린 테스트가 3.98s이고 상위 15개 합이 343s 중 ~34s다.
병적으로 느린 테스트는 없다 — 나머지는 2,331개에 고르게 퍼진 git subprocess와
temp 디렉터리 비용이다.

---

## C34. Execution Order Sprint

C33은 "Dashboard가 Control Tower가 되려면"을 물었고, 그 끝에서 §3의 뿌리가 버그가
아니라 **순서**라는 것을 알았다 — step 5와 step 6이 같은 실행이라 사람이 끼어들
창이 없다. C34는 그 질문을 파이프라인 전체에 던진다:

> **어떤 단계가 앞 단계의 결과를 잘못 가정하는가. 어떤 단계가 뒤에서만 존재할 수
> 있는 것을 전제하는가. 그리고 순서 자체를 무엇이 지키고 있는가.**

Baseline (이번 Sprint 시작 시 실측): 2,289 passed / 4 skipped / 1,363 subtests.

| # | 주제 | 결과 |
|---|---|---|
| 1 | 아홉 단계 중 둘이 `recorder.begin()`을 부르지 않았다 | **P0 수정** — 중단된 실행이 SUCCESS로 보고됐다 |
| 2 | 실행 순서를 지키는 것이 소스 배치뿐이었다 | **불변식 신설** |
| 3 | `RUNTIME_DIR`은 19개 경로 중 3개만 움직이는 손잡이였다 | **수정** — 실측 중 라이브 state를 깨뜨렸다 |
| 4 | step 4가 곱게 처리한 파일에 step 5가 죽는다 | **주장 정정** — 실측이 낡아 있었다 |

---

### 1. 중단된 실행이 SUCCESS로 보고됐다 (신규, **P0**)

`_Recorder.begin()`의 일은 자기 클래스 docstring에 적혀 있다 — *"knows which
step is currently in flight so that an exception escaping any step can still be
attributed to it"*. `run_once()`의 `finally`가 `recorder.current`를 읽어
`STEP_ABORTED`를 기록한다.

**아홉 단계 중 둘이 그것을 부르지 않았다**: `notion_sync`(4단계)와
`daily`(6단계). 그리고 둘 다 **첫 동작이 state 파일을 읽는** 단계다 —
docs/10 §46이 손상된 채 발견될 수 있다고 명시한 바로 그 파일들
(`notion_retry_queue.json`, `daily_history_state.json`).

결과는 오귀속이 아니라 **거짓 성공**이었다. `overall_status()`는 *기록된* FAILED
component를 접는데, 하나도 기록되지 않으니 중단된 실행과 깨끗한 실행이 구분되지
않는다. 실측:

    4단계에서 중단   STEP_ABORTED NONE     manifest SUCCESS / exit 0
    6단계에서 중단   STEP_ABORTED NONE     manifest SUCCESS / exit 0
    7단계에서 중단   STEP_ABORTED backup   manifest FAILED  / exit 2   <- 대조군

7단계는 `begin()`을 부른다. 그래서 옳게 동작한다.

6단계 줄이 가장 나쁘다. **그 단계가 Company History를 쓴다.** 같은 손상 파일이
이후 모든 실행을 똑같이 중단시키므로 Company History는 영구히 멈추는데, 매
manifest가 SUCCESS라고 말하고, `ops_status.py`의 LAST RUN 블록 — AGENT.md §6이
"먼저 이것부터"라고 지시하는 화면 — 이 `종합 상태 : SUCCESS (exit 0)`을 찍는다.

**수정** — 두 곳에 `begin()`. 실측 후:

    4단계 중단   STEP_ABORTED notion_sync    DEGRADED / 3   (Notion은 임계 경로 밖)
    6단계 중단   STEP_ABORTED daily          FAILED   / 2   (CRITICAL)

그리고 구조적 불변식을 붙였다: **`run_once()`가 결과를 기록하는 모든 component는
그 전에 `begin()`으로 자신을 알려야 한다.** 열 번째 단계가 같은 실수를 반복할 수
없다. `finally`가 `recorder.current`를 읽는다는 연결 자체도 함께 고정했다 —
그것이 끊기면 `begin()`은 장식이 된다.

### 2. 실행 순서를 지키는 것이 소스 배치뿐이었다 (불변식 신설)

순서는 세 문서가 정한다 — docs/07 §37(12단계), docs/09 §50-51(Monthly는 Daily
Catch-up 다음, Backup 앞), 그리고 `run_once()` 자신의 주석 둘:

    6.5  "Backup(7단계)보다 먼저 실행해야 갱신된 Daily 파일이 같은 실행에서
          백업된다"
    6.7  "Monthly는 이미 이 실행에서 확정된 Daily 파일만 읽는다"

전부 **데이터 의존성**이다. Late Event Update가 다시 쓴 Daily 파일을 Backup이
실어 보내야 하고, Monthly는 더 바뀌지 않을 Daily를 읽어야 한다. 단계를 옮기는
것은 취향 문제가 아니라 그 실행의 백업에서 하루가 빠지거나, 곧 바뀔 Daily로
한 달을 통합하는 일이다.

**아무것도 검사하지 않았다.** `PIPELINE_COMPONENTS`는 `_ARTIFACT_REFS`(dict)의
**선언** 순서이고, 기존 테스트는 둘이 같고 길이가 9라는 것만 고정했다. §1이
발견한 대로 두 목록은 실제로 어긋나 있었다 — `notion_sync`와 `daily`가 선언은
돼 있고 announce는 안 됐다.

**신설**: 실행 순서(=`recorder.begin()` 호출 순서, `finally`가 쓰는 그 신호)가
선언 순서와 같다는 것, 그리고 위 의존쌍 아홉 개가 지켜진다는 것. Lock이 첫
단계보다 먼저 잡히고 마지막보다 나중에 풀린다는 바깥 괄호도 함께.

### 3. `RUNTIME_DIR`은 19개 중 3개만 움직이는 손잡이였다 (신규)

`run_company_ops.main()`은 `run_once()`의 **19개 경로 인자 중 3개**만
`RUNTIME_DIR`에서 만든다(`local_master_dir`, `backup_working_copy_dir`,
`runner_lock_path`). 나머지 16개는 기본값이고, 그 기본값은 여기서 오지 않는다 —
`app.runner`, `collector.runtime`, `scheduler.state`, `backup.state`,
`history.file_repository`, `notion.retry_queue` **여섯 모듈이 각자 import 시점에
얼려 둔 `PROJECT_ROOT`**에서 온다.

운영에서는 두 루트가 같은 디렉터리라 아무 문제가 없다. 위험한 것은 다른
경우이고, 그것은 가설이 아니다 — **이 Sprint의 조사 중에 실제로 일어났다.**
`RUNTIME_DIR`을 다시 묶어(이 저장소가 `ops_status.py`를 격리하는 바로 그 방식)
샌드박스라고 믿고 돌린 것이 **진짜 파이프라인**이었고, Company History는 temp
트리에 쓰이고 **라이브** `daily_history_state.json`은 그 너머로 전진했다:

    daily/          여섯 파일, 지금은 존재하지 않는 디렉터리에
    라이브 포인터   2026-08-10 -> 2026-08-16
    정합성          CONSISTENT -> STATE_INCONSISTENCY
    포인터가 이미 지나갔으므로 어떤 실행도 그 엿새를 만들지 않는다

(그 자리에서 포인터를 실제 Daily 파일의 마지막 날짜로 되돌려 CONSISTENT를
복구했다. `runtime/runs/last_run.json`은 되돌리지 않았다 — 그 실행이 정말로
마지막 실행이었으므로, 낡은 manifest를 되살리는 것은 사실을 지어내는 것이다.)

**샌드박스라고 믿고 돌렸는데 운영을 깨뜨리는 것**은 손잡이가 가질 수 있는 최악의
모양이다. C31 §10이 `ops_status.py`에서 같은 함정을 기록하고 호출 시점 파생으로
고쳤는데, 그 수정은 여기서 쓸 수 없다 — 16개 기본값이 남의 모듈 것이고,
여기서 다시 파생하면 이 파일에 레이아웃에 대한 **일곱 번째** 의견이 생긴다
(`ops_status._agent_dir()`가 명시적으로 반대하는 것).

**수정** — 불완전함을 남기되 **조용하지 않게** 한다. `main()` 첫 줄에서 두
루트가 같은지 확인하고, 다르면 두 경로를 모두 보여주며 거부한다(exit 1 —
"실행이 있기 전의 설정 오류"라는 이 파일 자신의 정의에 정확히 맞는다). 운영에서는
항상 통과한다. 실패하는 유일한 길은 `RUNTIME_DIR`을 다시 묶은 것이고, 그때는
실행되면 안 된다.

메시지의 "19개 중 3개"라는 주장도 코드에 대한 주장이므로 테스트가 실제 signature와
대조한다.

### 4. step 4가 곱게 처리한 파일에 step 5가 죽는다 (주장 정정)

4b는 수집된 Event 파일 읽기를 가드하고 `NOTION_UNREADABLE`을 기록한다. **step 5는
같은 파일을 열한 줄 뒤에 아무 가드 없이 읽는다.** 그래서 가드는 실행을 살리지
못하는데, 그 옆 주석은 살린다고 적고 있었다 — 측정 블록까지 달아서:

    run ABORTED: ValueError / Daily files : NONE / backup state: MISSING

**같은 시나리오로 다시 재면 그 블록이 여전히 일어나는 일이다.** 중단이 4단계에서
5단계로 옮겨갔을 뿐이다. 실측(수집 직후 손상된 파일 하나):

    notion_sync    FAILED   unreadable=1   (로그 남음)
    STEP_ABORTED   history_filter
    overall/exit   FAILED / 2
    Daily files    NONE
    backup state   MISSING
    dashboard      도달 못 함

**둘 다 결함이 아니다.** step 5가 죽는 것은 BUG-20의 의도된 설계다 — History는
CRITICAL한 기록이고, 읽을 수 없는 파일이 거기서 조용히 빠지면 안 된다. 그리고
가드가 사는 것도 진짜다. 다만 **생존이 아니라 귀속과 증거**다:

- 실행이 DEGRADED 단계가 아니라 `history_filter`(CRITICAL)에 청구된다 — 위 주석이
  말하는 severity 역전이 실제로 사라진다;
- `NOTION_UNREADABLE <파일명>`이 로그에 남아 **어느 파일인지** 알려준다. step 5의
  맨 traceback은 그것을 말하지 않는다.

주석을 실제로 참인 것으로 좁혔다. 그리고 알아 둘 결과 하나: C33 §1이 만든
`Notion Unreadable` 칸은 **4a(Retry Queue) 경로에서만** 0이 아닐 수 있다. 4b의
읽을 수 없는 파일은 step 9b가 행을 쓰기 다섯 단계 전에 실행을 죽인다.

---

## C33. Control Tower Sprint

C32는 "Notion에 쓰는 숫자가 디스크 위의 사실과 같은가"를 물었다. C33은 그 다음
질문이다: **Dashboard가 기록 화면이 아니라 Control Tower가 되려면 무엇이 더
있어야 하는가.** 그리고 그 과정에서 세 번째 종류의 침묵이 나왔다 — 기계가
아니라 **사람이 쓴 내용**이 사라지는 경로.

Baseline (이번 Sprint 시작 시 실측): 2,244 passed / 4 skipped / 1,285 subtests.

| # | 주제 | 결과 |
|---|---|---|
| 1 | `Notion Unreadable` / `Notion Queued` | **구현** — C32 §6이 남긴 다음 단계 |
| 2 | OPS_* 다섯 개의 범위 | **정정 + 수정** — spec은 이미 정해져 있었다 |
| 3 | 사람이 쓴 Decision Context | **신규, 실측** — Company History에 영원히 닿지 않는다 |
| 4 | `ops_status.py` + `@dataclass` | **함정 발견 + 고정** — 293개 테스트가 죽는다 |
| 5 | `NotionAPIError.status_code` | **어긋남 (수정)** — 분류해 놓고 아무도 안 읽었다 |
| 6 | `SignalError.filename` | **문서 정정** — 없는 소비자를 위해 존재한다고 적혀 있었다 |
| 7 | TODO/FIXME/HACK/XXX 전수 | **결함 없음 (기록)** — 프로덕션 0건 |

---

### 1. Dashboard가 볼 수 없던 두 개의 Notion 사실 (C32 §6 해소)

C32 §6이 다음 단계로 기록해 둔 것이고, **왜 어려운지**까지 적어 두었다: 둘 다
`notion_sync_results`에서 유도할 수 없다.

    파싱 불가 Event 파일   애초에 SyncResult가 되지 않는다 — `app/runner.py`는
                          읽지 못한 파일의 `event_id`를 지어내지 않는다
    Retry Queue 깊이       실행 결과가 아니라 큐의 속성이다

그래서 Event 파일 10개가 읽히지 않은 실행은
`Notion Synced 0 / Skipped 0 / Retried 0` — **동기화할 것이 없던 실행과 같은
행**이었고, 한 달째 800건이 밀린 큐는 빈 큐와 같은 행이었다.

Run Manifest의 `queued=`는 대체재가 아니다(C32 §14에서 세 가지 이유를 이미
기록했다). 특히 세 번째: `to_event()`가 깨지는 큐 항목은 어떤 `queued` 카운트에도
없다.

**구현** — `OPS_RUNS`에 `Notion Unreadable` / `Notion Queued` 두 칸. Runner가
step 4에서 계산해 step 9b로 넘긴다. `record_run()`에서 유도하지 않고 **매개변수로
받는** 이유가 그것이다.

기본값 0을 붙였고, 그것이 C32 §2가 제거한 `getattr(..., 0)`과 다른 이유도
docstring에 적었다: 여기서 0은 "이 단계가 돌지 않았다"이지 "이름이 바뀐 필드를
가린 값"이 아니다. 그래도 값을 가진 호출자가 defaulting하면 실제보다 건강한
실행을 보고하게 되므로, Runner가 명시적으로 넘기는 것을 테스트로 고정했다.

**함정 하나를 피했다(기록)**: 두 값은 step 4의 `if notion_sync is not None:`
안에 있었다. 밖에 바인딩하지 않고 step 9b에 넘겼으면 **Dashboard는 있고 Sync는
없는** 지원되는 구성에서 `NameError`가 났을 것이고, 그것은 그 단계 자신의
`except`가 흡수해 `DASHBOARD FAILED` 한 줄만 남기고 행을 영구히 잃는다. 이
파일의 다른 모든 테스트가 `notion_sync`를 넘기므로 어디서도 안 잡혔을 것이다.
발견한 결함이 아니라 **피한 함정**이고, 테스트 docstring에도 그렇게 적었다.

행의 산수도 고정했다 — `synced + skipped + retried`는 항상 처리 건수와 같고
(`itertools.combinations_with_replacement`로 전 조합 검증), `queued`는 실행을
가로지르는 값이라 **그 합에 들어가지 않는다**.

### 2. 네 Database의 범위는 이미 정해져 있었다 (A-16 정정)

목표가 "문서/스펙상 이미 결정된 범위라면 승인 없이 구현한다"였으므로 먼저
문서를 뒤졌다. 찾은 것은 구현 지시가 아니라 **반대 방향의 결정**이다.

`docs/14_RUN_CONTRACT.md` §1 Operational Data Model:

    | Operational Projection | Notion (PROJECTS / OPS_RUNS) | View이며 절대 Source가 아니다 |

**두 개, 이름까지 적혀 있다.** PROJECTS는 `notion.sync`의 것이고, 이 모듈의
것은 `OPS_RUNS`다. 나머지 넷은 그 모델에 없다. 즉 `OPS_BACKUP` /
`OPS_NOTION_SYNC` / `OPS_RISK` / `OPS_READINESS`를 쓰는 것은 "앞서 나가는 것"이
아니라 **Operational Projection을 docs/14가 정의한 것보다 넓히는 것** — Spec
변경이다.

A-16은 C10부터 이것을 "docs/04 §53의 과잉 방지 **결정 대기**"로 기록해 왔다.
docs/14 §1은 나중에 쓰였고, **그 질문을 이미 닫았다.** 넷은 결정을 기다리는 것이
아니라 계약 밖이다.

`OPS_READINESS`에는 두 번째 근거도 있다 — docs/04 §68이 "자동 Launch Readiness
점수"를 V1에서 만들지 않는다고 명시한다.

**그래서 구현이 아니라 수정이 나왔다.** `bootstrap_dashboard_databases()`의
기본값이 `DASHBOARD_DATABASES` 전부(다섯 개)였다. 설정 문서를 따라간 운영자는
**어떤 코드도 쓰지 않고 어떤 spec도 인정하지 않는 Database 넷**을 실제
Workspace에 만들게 되고, 이 모듈에는 삭제 경로가 없으며(의도된 설계), 유일한
방어선이 docs/13의 산문 경고였다.

기본값을 `CONTRACTED_DATABASES = (OPS_RUNS,)`로 바꿨다. 능력을 없앤 것이 아니다 —
`only=`로 여전히 넷을 만들 수 있다. **되돌릴 수 없는 쪽을 의도적으로 요청해야
하는 쪽에 놓은 것**이다. 네 스키마는 남긴다: 설계 의도의 기록이고, 지우면
기록 없이 잃는다.

### 3. 사람이 쓴 Decision Context는 Company History에 닿지 않는다 (신규, **실측**)

이 Sprint의 가장 나쁜 발견이고, 잃는 것이 **사람이 쓴 글**이라는 점에서 E-17이나
A-20과 다르다. 재실행으로 재현되지 않는 유일한 종류다.

능력은 완전히 구현돼 있다. `review_cli.py`가 네 필드를 묻고,
`history/review.py`가 저장하고, `daily/markdown.py`가 있으면 렌더링한다. 연결이
끊긴 곳은 **타이밍**이다:

    step 5    Candidate를 쓴다
    step 6    그 날짜의 Daily를 렌더링한다      <- 같은 실행, 몇 초 뒤
    ...
    사람이 review한다                          <- 존재하는 유일한 창
    step 6.5  이미 닫힌 날짜에 Late Event를 병합하지만, **이번 실행이 수집한
              날짜만** 대상이고, §38 가드는 파일에 이미 있는 event_id를 건너뛴다
    step 6    이미 있는 Daily 파일을 덮어쓰지 않는다

즉 **KEEP Candidate에 대해 사람이 review할 수 있는 시점은 언제나 렌더링 이후**다.
step 5와 step 6이 같은 실행이기 때문이다.

실측(`DECISION_APPROVED` Event → KEEP → 렌더링 → `submit_review()`로 세 필드):

    review stored (반환 객체)                True
    디스크에서 재확인                        True
    update_daily_history                     NO_LATE_EVENTS
    generate_daily_history                   FileExistsError
    Company History에 Decision Context       False
    Daily 파일이 바뀌기라도 했는가           False

**아무것도 경고하지 않는다.** `_kept_but_not_rendered()`는 Candidate의
`event_id`가 파일에 있는지 묻고 — 있다 — 그래서 깨끗하다고 답한다. reviewer
자신의 반환값은 성공이다.

**수정: 탐지만.** `scheduler/consistency.py`, `history/reconciliation.py`,
`agent/delivery.py`가 전부 적용하는 그 절제이고 이유도 같다 — 모든 복구는
결정이다. `ops_status.py`에 `_reviewed_but_not_rendered()`를 넣고 HISTORY 블록의
`_kept_but_not_rendered()` 바로 옆에 붙였다. 둘은 한 단계 차이의 같은 질문이다:
저쪽은 "Candidate가 파일에 닿았는가", 이쪽은 "Candidate의 **내용**이 닿았는가".

라벨은 `review_cli._REVIEW_FIELDS`를 **import한다**. 세 모듈이 같은 네 필드를
이름 대는데 네 번째 사본을 만드는 것이 곧 drift다. 렌더러가 실제로 그 문자열을
쓰는지도 테스트로 대조한다.

**SKIP한 두 복구와 사유:**

| 복구안 | 왜 결정인가 |
|---|---|
| review 계층이 그 날짜를 다시 렌더링 | `history/review.py` docstring이 "only operates on what a HistoryRepository already has stored"라고 자기 범위를 적어 두었다. Company History 쓰기는 그 밖이다 |
| step 6.5가 이미 병합한 항목도 갱신 | docs/06 §37의 "Late Event" 정의를 *새* Event에서 *바뀐* Event로 바꾼다 |

**필요한 것**: (a) 둘 중 어느 쪽인지에 대한 결정, 또는 (c) review를 Daily Close
*이전에* 넣을 수 있게 파이프라인 순서를 바꾸는 결정(= docs/07 §37 변경).

### 4. `ops_status.py`는 `@dataclass`를 담을 수 없다 (신규 함정, 고정)

§3의 세 번째 소비자가 생기면서 `_read_keep_candidates()`의 3-tuple이 4-tuple이
됐고, 모든 호출부가 한꺼번에 깨졌다. 두 번째로 넓어진 것이라 이름을 붙이기로
했다 — 그리고 `@dataclass`를 붙이자 **테스트 293개가 죽었다.**

평범한 것 셋이 겹쳐 평범하지 않은 것이 된다:

1. `ops_status.py`는 `from __future__ import annotations`로 시작한다 → 모든
   annotation이 문자열이다;
2. `dataclasses`는 `KW_ONLY`를 확인하면서 그 문자열을
   `sys.modules[cls.__module__]`에 대고 해석한다;
3. 이 파일의 모든 테스트 헬퍼는 `importlib.util.spec_from_file_location(...)` +
   `exec_module()`로 로드하고 **`sys.modules`에 먼저 등록하지 않는다** — 그것이
   테스트마다 `RUNTIME_DIR`을 갈아끼우는 방법이기 때문이다.

그 로더에서 (2)의 조회는 `None`을 주고 데코레이터가
`AttributeError: 'NoneType' object has no attribute '__dict__'`로 죽는다.
**import 시점**이라 그 모듈을 로드하는 모든 테스트가 실패한다 — 무엇을 테스트하던
중이든. 실측: `test_observability.py`와 `test_history_review.py`에서 293개,
candidate와 무관한 것들이 대부분.

`NamedTuple`은 그런 해석을 하지 않는다. `StoredCandidate`가 NamedTuple인 이유가
그것이고, 그 이유를 클래스 docstring과 invariant 테스트 양쪽에 적었다 — 다음
사람은 저장소의 다른 모든 곳이 쓰는 `@dataclass`에 손이 갈 것이고, 그때 받는
실패는 데코레이터가 아니라 무관한 293개를 가리킨다.

Ban 하나가 아니라 **ban과 ban의 전제** 둘을 검사한다.
`from __future__ import annotations`가 사라지면 ban도 사라져도 되고, 두 번째
assertion이 그 사실을 말한다.


### 5. 영원히 거부되는 Notion 요청은 ATTENTION에 닿을 수 없었다 (신규)

C32 §20의 렌즈("쓰고 아무도 읽지 않는 값")를 dataclass 필드에서 **예외 속성**으로
넓혔더니 하나가 나왔다.

`NotionAPIError.status_code`는 모든 HTTP 실패에서 설정되고, 테스트 네 개가
단언하고, **프로덕션 코드는 하나도 읽지 않는다.** 그리고 그것은 BUG-13이 말하는
바로 그 신호다 — "Notion이 잠깐 죽었다"와 "Notion이 이 요청을 영원히 거부한다"를
가르는 값. BUG-13의 수정은 **이유 문자열**을 로그에 붙여 사람이 산문을 읽고
구분하게 한 것이었다. 같은 말을 하는 기계 판독 가능한 필드는 이미 거기 있었고
아무도 안 읽었다.

비용은 미관이 아니라 구조였다:

    ops_status.py의 ATTENTION은 PERMANENT만 나열한다 (의도적 — RETRYABLE은
    다음 실행이 처리하고, 스스로 지워지는 경보는 그 절을 대충 넘기게 만든다)
    Notion 실패는 전부 RETRYABLE이었다
    => 영원히 거부되는 요청은 **manifest를 통해서는 ATTENTION에 닿을 수 없었다**

닿는 유일한 길은 C32 §14가 만든 NOTION 블록이 큐 항목의 나이가 사흘을 넘긴 것을
알아채는 것이었다. 즉 **사흘을 기다려야** 알 수 있었다.

**수정** — `SyncResult.status_code`를 얹고(추가만), Runner가 그것으로
retryability를 분류한다. 근거는 docs/14 §5 자신의 정의다: *PERMANENT = "지금
개입해야 한다 (ops_status.py ATTENTION에 뜬다)"*. 잘못된 토큰, 공유되지 않은
Database, Notion이 거부하는 Property 값 — 전부 그것이다.

바뀌지 **않는** 것 셋을 테스트로 고정했다:

- **exit code** — `runsummary.overall_status()`는 **severity**만 접고, Notion
  Sync의 severity는 그대로 DEGRADED다(README RULE 5, docs/14 §5).
- **큐 동작** — docs/04 §38이 Event 삭제를 금지한다. Event는 그대로 큐에 남는다.
  분류가 말하는 것은 "사람이 움직여야 한다"이지 "포기한다"가 아니다.
- **UNKNOWN 우선** — 읽지 못한 파일이나 예기치 못한 예외가 있으면 UNKNOWN이
  이긴다. 읽지도 못한 Event를 "영원히 거부됨"이라고 부르는 것은 BUG-13이 경고하는
  그 월권을 반대 방향으로 하는 것이다.

목록은 짧고 명시적이다 — `(400, 401, 403, 404)`. "아무 4xx"로 하면 408·429·409가
쓸려 들어가는데 셋 다 기다리거나 재시도로 풀린다. 이것도 테스트로 고정했다.

`reason`도 고쳤다: 503과 400이 같은 배치에 있으면 **사람이 행동할 수 있는 쪽**을
고른다. `reason`은 운영자에게 닿는 유일한 문장이다.

비대칭이 이 작업을 할 만하게 만들었다: `backup/git_ops`는 git의 **영어 산문**으로
permanent/transient를 분류하고(`is_authentication_failure()`), C31 §6은 그
분류기의 로케일 의존까지 제거했다. Notion 쪽은 산문보다 나은 신호를 갖고 있으면서
하나도 쓰지 않았다.

### 6. `SignalError.filename`은 없는 소비자를 위해 존재한다고 적혀 있었다 (문서)

같은 렌즈를 예외 속성 전체에 돌린 결과 셋이 나왔고, 둘(`DashboardBootstrapPartialError`의
`cause`/`failed_database`)은 자기 메시지를 만드는 데 쓰이므로 정당하다. 셋째는
docstring이 틀렸다:

> Carries the offending filename so `agent.py` can route it to the rejected/
> directory

`agent.py`는 그렇게 하지 않는다. `load_signals()`가 `(Path, SignalError)` 쌍을
돌려주고 `_collect_one_date()`는 **`Path`로** 라우팅한다 — 그쪽이 옳다. Path는
옮길 수 있고 이름은 옮길 수 없다.

**동작에는 결함이 없다.** 고친 것은 주장이다. 이 종류의 문장이 쓰이지 않는 속성을
load-bearing으로 착각하게 만든다(E-11).

### 7. TODO/FIXME/HACK/XXX 전수 (결함 없음, 기록)

목표가 명시적으로 요구한 sweep. 프로덕션 코드 전체에서 **1건**, 그리고 그것은
할 일이 아니라 `monthly/parser.py`가 `` `TODO: ` ``라는 **영어 라벨 모양**을
설명하는 산문이다(C31 §1이 고친 그 결함). 테스트에서도 1건, 같은 성격.

즉 이 저장소에는 미처리 TODO 주석이 없다. 남은 것은 전부 BACKLOG에 있다 —
이 파일이 실제로 그 역할을 하고 있다는 뜻이라 기록해 둔다.

### 8. 버려지는 반환값 전수 (BUG-39 부류) — 신규 없음, 기록

같은 렌즈의 세 번째 방향: **반환값이 있는 함수를 호출해 놓고 그 값을 버리는 자리.**
AST로 36곳을 찾았고, 대부분은 이름 충돌로 인한 오탐이다(`recorder.failed()`가
`notion/bootstrap.py`의 `BootstrapResult.failed` 프로퍼티로 해석되는 등).

실제로 값을 버리는 자리는 전부 정당했다:

    outbox.stage() -> Path              Path가 필요 없다
    scheduler -> generate_daily_history() Scheduler가 generated_dates를 따로 센다
    drain_pending -> find_or_create_by_title() "raise만 안 하면 된다"가 계약이다
    review_cli -> submit_review()        갱신본이 필요 없다

**하나는 확인할 값이 있었고, 이미 기록돼 있었다.**
`HistoryRepository.save() -> bool`은 `app/runner.py` 5단계에서 버려진다. 읽어 보니
`False`는 DROP(저장 대상 아님)일 때뿐이고 — docs/05 §49대로 — 충돌은 `False`가
아니라 **`FileExistsError`**로 나온다. 5단계에는 per-event 오류 처리가 없으므로
(BUG-20 characterization) 그 예외는 실행 전체를 중단시키고, 파일이 그대로 있으니
**재실행으로도 풀리지 않는다.**

그런데 그것은 **BUG-10**이 이미 기록하고 `tests/test_runner_failure_paths.py`가
고정한 항목이다("재저장 시 FileExistsError가 Runner를 중단시킨다"). 그리고 C31
§7(a)가 그 도달 경로 하나(대소문자 충돌)를 이미 도달 불가로 측정해 뒀다.

**신규 없음.** 같은 조사를 세 번째로 하지 않기 위해, 렌즈와 결과를 기록한다.

---


## C32. Notion Dashboard Fidelity Sprint

C31은 "한 모듈이 쓴 것을 다른 모듈이 되읽는 지점"을 물었다. C32는 그 질문을
**시스템 밖으로** 내보낸다: 이 시스템이 Notion에 쓰는 숫자와 줄이, 디스크 위의
실제 상태와 같은 사실을 말하는가.

추적 대상은 20개 항목 각각에 대해 다섯 칸이다:

    SOURCE OF TRUTH -> TRANSFORM -> NOTION RECORD -> DISPLAY -> FAILURE BEHAVIOR

세 칸(SOURCE·TRANSFORM·FAILURE)은 대체로 견고했다. **어긋난 것은 언제나 네 번째
칸(DISPLAY)이었다** — 값은 정확히 계산되어 있고, Notion에 도착하지 못하거나,
도착해서 다른 뜻으로 읽힌다.

| # | 주제 | 결과 |
|---|---|---|
| 1 | Overall 판정 | **어긋남 (수정)** — 자기 docstring이 약속한 두 원인에 분기가 없었다 |
| 2 | Dashboard 숫자 읽기 | **어긋남 (수정)** — `getattr(x, name, 0)`가 rename을 영구히 0으로 숨긴다 |
| 3 | `init_notion.py` 출력 | **신규 보안 (수정)** — 원격 문자열 4종이 무방비로 stdout에 |
| 4 | Transport 적체 | **어긋남 (수정, P0)** — 막힌 유입 경로와 한가한 일요일이 같은 행 |
| 5 | `Notion Synced` | **어긋남 (수정)** — 쓰지 않은 Event를 썼다고 센다 |
| 6 | `notion_unreadable` | 어긋남 (**미수정** — §6, 다음 단계) |
| 7 | Late Event 가드 | **BUG-29 수정** — Notion 날짜 선택기 한 번이 Event를 영구히 큐에 가둔다 |
| 8 | §62 중복 가드 | **어긋남 (수정)** — rich_text 첫 조각만 읽는다 |
| 9 | 실 Workspace 상태 | **측정 완료** — Dashboard는 만들어진 적이 없다(§9) |
| 10 | 필수 환경변수 | **측정 완료** — 이 머신에서 Runner는 아예 뜨지 않는다(§10) |
| 13 | 읽을 수 없는 전송 기록 | **어긋남 (수정)** — 선례를 인용하고 따르지 않은 유일한 스캐너 |
| 14 | Notion 큐 가시성 | **어긋남 (수정)** — `added_at`/`attempt_count`를 아무도 읽지 않았다 |
| 16 | `run_agent.py` `[FAILED]` | **어긋남 (수정)** — 바로 위 줄이 막은 위조를 세 줄 뒤에서 허용 |
| 17 | Run Manifest 렌더러 둘 | **어긋남 (수정)** — 다섯 필드 전부에 대해 두 렌더러의 의견이 달랐다 |
| 18 | `[FAILED] Backup:` | **신규 보안 (수정)** — git stderr와 remote URL의 토큰을 그대로 |
| 20 | 미독 필드 전수 | **신규 렌즈** — 255개 중 30개 무독자, 그중 3개 수정 |
| 21 | Workspace 검색 | **어긋남 (수정)** — 첫 100건만 보고 "이게 전부"라고 답했다 |
| 22 | Notion 큐 백업 | **기록 (SKIP)** — 복원된 머신은 밀린 Event를 조용히 잃는다 |

Baseline: 2149 passed / 4 skipped / 1233 subtests.

---

### 1. `Overall`이 바로 옆 칸과 반대되는 말을 한다 (신규, P1)

`_overall_status()`의 docstring은 언제나 이렇게 적혀 있었다 — "WARN when nothing
failed outright but something needs a human look (**rejected**/failed events,
Backup not successful)". `rejected`는 **그 함수의 매개변수가 아니었다.** Notion
Sync 실패는 어느 분기에도 없었다. 실제 결과 객체로 실측:

    8건 REJECTED (Collector)              Rejected 8   ->  Overall OK
    5건이 Notion에 닿지 못함 (401)         Retried  5   ->  Overall OK

`Overall`은 Notion View에서 정렬·필터의 기준이 되는 칸이고, 한눈에 읽히는
유일한 칸이다. 거기 적힌 판정이 틀리면 없는 것만 못하다.

세 번째는 같은 나이의 오타다. WARN 분기가 `"BACKUP_REVIEW"`를 물었는데
`backup.result.BackupStatus`는 그런 값을 낸 적이 없다 — docs/08 §34의 선택적
상태 이름은 `BACKUP_REVIEW_REQUIRED`다. **그 상태를 나중에 추가해도 이 분기는
여전히 안 걸린다.**

**수정.** 건강한 값의 집합을 닫는다(`BACKUP_SUCCESS`, `BACKUP_NOT_REQUIRED`).
모르는 상태는 사람에게 간다 — 오타로 침묵할 수 없는 방향이다. 그리고 판정에
들어가는 모든 입력이 **같은 행의 칸이기도 하다**는 것을 구조적으로 고정했다
(`test_every_input_to_the_verdict_is_also_a_column_of_the_same_row`). 원인이 안
보이는 WARN은 사람에게 "찾아보라"고만 하고 볼 것을 주지 않는다.

### 2. Dashboard의 모든 숫자가 rename을 0으로 숨긴다 (신규, P1)

`app/runner.py`는 Run Manifest에 넣는 형제 숫자들 옆에 규칙을 적어 두었다 —
*"Direct attribute access, not `getattr(..., default)`: a default would only be
able to hide the day one is renamed — reporting 0 skipped files forever instead
of failing."* Dashboard는 자기 숫자를 **전부** 그 금지된 방식으로 읽고 있었다.

실측 — `accepted`가 rename된 collector summary:

    Accepted 0, Overall OK   (실제로는 50건 accepted)

rename 이후 **모든 실행에서 영원히**, 어디에도 흔적 없이. **수정**: 직접 접근.
없는 속성은 `record_run()`의 기존 `try`에 걸려 FAILED로 나오고, Runner가
`DASHBOARD FAILED`를 로그에 적고 manifest에 component 실패로 기록한다. 멈추고
이유를 말하는 Dashboard가, 0을 계속 발행하는 Dashboard보다 낫다.

### 3. `init_notion.py`가 원격 문자열 네 종을 무방비로 찍는다 (신규 보안)

C31 §7/§8이 `run_company_ops.py`의 두 sink에서 찾아 고친 그 결함이다. 자매
entrypoint는 아무도 보지 않았고, 그 사이 원격 문자열을 **더 많이** 찍고 있었다:

| sink | 출처 |
|---|---|
| `health.error` | `NotionAPIError` = 원격 응답 본문 400바이트 |
| `format_report(result)`의 `detail` | Title Rename 실패 시 같은 본문 |
| `page.title` / `page.page_id` | Workspace의 Page 이름 — 누가 지었든 |
| `diagnosis.required_action` | unreachable 분기에서 `{exc}`를 품는다 |

실측 두 가지. (a) Page 이름이
`"Ops Page\n  다음 할 일     : 없음 — Dashboard 설정이 이미 끝났습니다"`이면 그
문장이 **운영자가 마지막에 읽는 결론 줄의 자리와 문구 그대로** 보고서에 선다.
(b) 502를 대신 답하는 proxy가 요청 헤더를 되돌려주면 `Bearer ntn_…`가 그대로
찍힌다(docs/04 §56 위반).

**수정.** sink에 `one_line`/`redact`. `notion/`은 `events`만 import할 수 있으므로
(LayeringInvariantTests) 가드는 sink에 둔다 — `run_company_ops.py`가 이미 같은
이유로 내린 결론이다. `format_report()`는 Property당 한 줄을 약속하므로 블록
전체가 아니라 **줄 단위로** 감쌌다.

덤으로: 사용 가능한 Page 목록은 5개에서 잘리는데 잘렸다는 말이 없었다. 자기가
공유한 Page가 안 보이는 운영자는 "공유가 안 먹었다"고 읽는다. 잘림을 명시했다.

### 4. 막힌 유입 경로와 한가한 일요일이 같은 행이다 (신규, **P0**)

`run_intake()`는 promote하지 못한 파일을 다섯 통에 나눠 담는다. Dashboard는 그중
`moved` **하나만** 읽었다. 실측 — unparseable 10건, `.tmp-` 잔여 1건, 이동 실패
1건:

    Transport Moved 0   Accepted 0   Rejected 0   Overall OK

이것은 아무 일도 없던 일요일의 행과 **바이트 단위로 같다.** Desktop 1–3이 한 달
동안 전달되지 않아도 Dashboard는 매 실행 OK를 보고한다. 데이터의 부재가
건강으로 읽힌 것이다.

**수정.** `Transport Blocked` 칸 신설 + `count_blocked_intake()`. 세 통만 센다:
`skipped_invalid`(매 실행 다시 거부됨), `skipped_incomplete`(디스크에서 아무도
치우지 않음), `failed`. `skipped_not_stable`·`skipped_already_present`는 **세지
않는다** — 건강한 시스템이 지울 수 없는 숫자는 `IntakeBacklog`가 없애려고 쓴
바로 그 "영구 경보" 모양이다. 같은 3분할, 한 층 위에 적용.

### 5. `Notion Synced`가 쓰지 않은 Event를 썼다고 센다 (신규)

`synced`는 "실패가 아닌 전부"였고, 거기에 `NOTION_SKIPPED_OLD_EVENT`가
쓸려 들어갔다 — docs/04 §35가 "적용하지 않았다"로 정의한 상태다. 실측: 4건 전부
skip, `Notion Synced: 4`, Notion 쓰기 0회.

**수정.** 분할로 바꾸고 `Notion Skipped` 칸을 신설했다. 모르는 status는 버리지
않고 `retried`로 보낸다 — `synced + skipped + retried`가 항상 처리 건수와 같아서
행의 산수가 닫히고, 알 수 없는 status는 WARN을 띄우는 쪽에 놓이는 게 맞다.

### 6. `notion_unreadable`은 아직 Notion에 닿지 않는다 (**미수정**)

4b 단계가 읽지 못한 Event 파일은 `notion_sync_results`에 들어가지 않는다(id를
지어내지 않기 위한 의도적 결정). 그래서 Run Manifest에는 있고 Dashboard에는
없다. 10건이 읽히지 않은 실행은 `Notion Synced 0 / Skipped 0 / Retried 0` —
"동기화할 것이 없었다"와 같은 행이다.

Notion Retry Queue 깊이도 마찬가지다. 800건이 몇 주째 밀려 있어도 Notion에서는
보이지 않는다. 둘 다 Runner가 이미 계산하지만 `run_once()`의 반환 tuple이 아니라
`recorder`에 있어서, `record_run()`의 계약("Runner가 이미 반환한 객체만 재구성")
안에서는 닿지 않는다. **다음 단계**: `record_run()`이 `RunSummary`(또는
recorder의 component 목록)를 받도록 넓히고 `Notion Unreadable` / `Notion Queued`
두 칸을 추가한다. 칸 추가는 §11의 마이그레이션 위험을 함께 진다.

### 7. Notion 날짜 선택기 한 번이 Event를 영구히 가둔다 (BUG-29, **수정**)

BUG-29는 "무엇을 신뢰할지에 대한 결정"이 필요하다며 characterization만 남아
있었다. 그 결정은 **같은 함수 한 분기 위에 이미 내려져 있었다.**

docs/04 §29-30의 Late Event 가드는 저장된 `Last Updated`를
`datetime.fromisoformat()`으로 읽어 Event의 timestamp와 비교한다. Event 쪽은
안전하다(`events.schema`가 offset을 강제한다). Notion 쪽은 셀 안에 있는 무엇이든
이고, docs/04 §43은 사람이 이 DB를 편집한다고 명시한다. 실측:

    {"start": "2026-08-17"}            TypeError   (naive vs aware)
    {"start": "2026-08-17T09:00:00"}   TypeError
    {"start": "yesterday"}             ValueError

`sync()`는 `NotionAPIError`만 잡는다. 세 개 다 모듈 밖으로 탈출해
`NOTION_FAILED` + retryability UNKNOWN이 되고 Retry Queue에 들어간다 — 저장된
날짜는 재시도로 바뀌지 않으므로 **매 실행 똑같이 실패한다.** 첫 줄은 특별한
값이 아니다. Notion 날짜 선택기에서 시간을 안 고르면 나오는 값이다.

**수정.** `current_last_updated is None`(사람이 날짜를 지운 경우)에 이미 "비교
불가 → 진행"이라는 답이 있었다. 파싱 불가는 같은 인식 상태이고, 같은 답을 주는
것은 정책을 새로 만들지 않는다. 진행이 **자가 치유**이기도 하다 — 이어지는
update가 제대로 된 `Last Updated`를 쓰므로 다음 Event부터 가드가 다시 켜진다.
거부는 셀도, 막힘도 그대로 둔다.

결정하지 **않은** 것: `"2026-08-17"`을 Event의 offset 기준 자정으로 읽는 것.
그건 Late Event 경계를 아무도 고르지 않은 방향으로 최대 하루 옮긴다. 모르는
것은 모른다고 보고한다 — `SyncResult.error`에 적히고 `notion_sync.log`에 닿는다.

그 마지막 연결을 위해 `_log_notion_sync()`의 조건을 "status가 실패인가"에서
"`error`가 채워져 있는가"로 바꿨다. 원래 의도(빈 필드를 매 줄에 더하지 않는다)는
그대로고, **할 말이 있는 성공한 Sync**라는 새 경우를 잃지 않는다.

### 8. §62 중복 가드가 rich_text 첫 조각만 읽는다 (신규)

Notion은 rich_text를 **서식이 같은 구간마다 한 항목**으로 쪼개 저장하고,
mention·equation 항목에는 `text` 키가 아예 없다. `_extract_rich_text()`는
`items[0]["text"]["content"]`를 읽었다. 실측: 같은 id가 `EVT-` + `1` 두 조각으로
저장돼 있으면 `"EVT-"`와 비교해 놓치고, 이미 적용한 Event를 다시 적용한다.

폭발 반경이 작았던 것은 설계가 아니라 운이다 — §29-30의 timestamp 가드가 한 걸음
뒤에서 대개 잡는다. 두 가드가 따로 있는 이유는 서로를 못 덮기 때문이고, §62는
§63이 볼 수 없는 동시각 케이스를 맡는다.

**수정.** `plain_text` 우선 + 전 항목 이어붙이기. `dashboard._page_title()`이
title property에 대해 **이미 그렇게 읽고 있었다** — 같은 Notion 모양, 같은 답,
이제 같은 질문 방식.

### 9. 실 Workspace 상태 (읽기 전용 측정, 2026-08-17)

`init_notion.py`는 PROJECTS에 Property를 **생성**하므로 돌리지 않았다. 읽기만
하는 경로(`health_check()` + `diagnose_dashboard_bootstrap()` +
`get_database_schema()`)로 측정했다:

    CONFIG   projects_database_id 설정됨,  ops_runs_database_id 없음
    HEALTH   ok=True
    PROJECTS §8의 11개 Property 전부 존재 (Title rename도 반영됨)
             + Notion 기본 3개(Date/Notes/Tags) — 무해
    DASHBOARD readiness = NEEDS_SHARED_PAGE
             reference parent type = workspace  (PROJECTS가 Workspace 루트)
             search 가능 = True,  hostable pages = 0

즉 **Notion Sync는 실제로 살아 있고, Operations Dashboard는 한 번도 실행된 적이
없다.** Notion API는 Workspace 루트에 Database를 만들 수 없고, Page 생성은 범위
밖이며, 이 integration에는 Page가 하나도 공유되어 있지 않다.

**막힌 지점(운영자 작업, SKIP):**

1. Notion에서 Page 하나(Company Ops page)를 이 integration에 공유한다
   (Share → Connections). 코드가 할 수 없다 — Page 생성 금지, 어느 Page를 쓸지는
   운영자 결정.
2. 그 Page 밑에 `OPS_RUNS` Database를 만든다. `bootstrap_dashboard_databases()`가
   그 일을 하지만 **어떤 entrypoint도 호출하지 않는다.** 그리고 그것은 의도적으로
   고정돼 있다 — `test_the_setup_cli_does_not_create_anything_from_the_diagnosis`가
   `init_notion.py`에 `bootstrap_dashboard_databases` / `create_database` 문자열이
   없음을 검사한다. 자동 생성은 실 Workspace 변경이라 A-8 SKIP.
3. `NOTION_OPS_RUNS_DATABASE_ID`를 그 id로 설정한다.

셋 중 하나라도 없으면 `record_run()`은 매 실행 SKIPPED_NOT_CONFIGURED다.

### 10. 이 머신에서 Runner는 아예 뜨지 않는다 (신규, 운영)

`.env`에는 `NOTION_API_TOKEN`과 `NOTION_PROJECTS_DATABASE_ID` 둘뿐이다. 나머지
다섯 개는 `.env`에도, User 환경변수에도, Machine 환경변수에도 **없다**(측정):

    COMPANY_OPS_HISTORY_START_DATE   없음  -> run_company_ops.py 즉시 exit 1
    COMPANY_OPS_PROFILE              없음  -> run_agent.py 구성 불가
    COMPANY_OPS_AGENT_SYNC_FOLDER    없음  -> run_agent.py 구성 불가
    COMPANY_OPS_AGENT_START_DATE     없음  -> run_agent.py 구성 불가
    NOTION_OPS_RUNS_DATABASE_ID      없음  -> Dashboard 매 실행 skip

게다가 이 저장소에는 `.env` 자동 로딩이 없다(`.env.example` §9가 명시). 즉
`.env`에 채워 넣어도 entrypoint는 보지 못한다 — 셸에서 export하거나
`scripts/install_agent_task.ps1`이 User 환경에 심어야 한다.

코드 결함이 아니라 배포 상태다. `.env` 로딩을 코드에 넣는 것은 운영 정책
결정이므로 SKIP하고, docs/13에 현재 상태와 필요한 명령을 기록했다.

### 11. 칸을 늘린 것의 마이그레이션 위험 (기록)

§4·§5가 `OPS_RUNS` 스키마에 두 칸을 더했다. 이미 만들어진 `OPS_RUNS`에는 그
Property가 없으므로 Notion이 400으로 거절한다. **이 배포에서는 위험이 없다** —
§9가 그 Database가 존재하지 않음을 측정했고, `bootstrap_dashboard_databases()`는
`DeadCapabilityInventoryTests`가 고정한 대로 호출자가 없다(=이 저장소가 만든
`OPS_RUNS`는 어디에도 없다).

그래도 실패 모드는 안전한 쪽이다: `record_run()`이 FAILED를 돌려주고 pending에
쌓이며, Runner가 `DASHBOARD DRAIN_PENDING ... REASON <Notion의 설명>`을 로그에
적는다. 데이터는 잃지 않고 이유는 남는다.

**다음 단계(미구현)**: `notion/bootstrap.py`의 "없는 Property만 생성"을 OPS_RUNS에
적용하는 `bootstrap_dashboard_properties()`. `init_notion.py`에 붙일 수는 없다
(§9-2의 읽기 전용 고정) — 별도 운영 명령이 필요하고, 그건 entrypoint 추가라
결정 사항이다.

### 12. Dashboard가 보여주지 못하는 것 (SKIP, 범위와 이유)

목표는 "Notion 하나에서 전체 상태"였다. 실행 단위 사실(§1–§6)은 `OPS_RUNS`가
정직하게 나르게 됐다. 나르지 **못하는** 것은 회사 단위 상태다:

| 항목 | 로컬 원본 | Notion |
|---|---|---|
| Desktop 1~4 침묵/적체 | `app/desktop_activity.read_company_activity()` | 없음 |
| Agent 상태 | `agent/status.py` (각 Desktop 로컬) | 없음 |
| Daily/Monthly 구멍 | `ops_status._holes_in_the_*_sequence()` | 없음 |
| History ↔ Backup 정합 | `ops_status._history_newer_than_the_last_backup()` | 없음 |
| ATTENTION 전체 | `ops_status.main()` | 없음 |
| Last successful backup | `backup/state.py` | 없음 |

`OPS_RISK`(Risk/Severity/Area/Status/First Seen/Resolved At)는 이미 스키마가
ATTENTION 모양으로 만들어져 있어 자연스러운 도착지다. 그럼에도 SKIP한 이유:

- `ops_status.py`의 ATTENTION 판정은 print 지향 코드 2,000줄이고 구조화된 API가
  아니다. 데이터로 뽑아내는 것은 큰 리팩터링이고 회귀 위험이 크다.
- `Risk` 문구·`Severity`·`Area` 매핑은 **정책 결정**이다.
- `OPS_READINESS`는 아예 금지다 — docs/04 §68이 "자동 Launch Readiness 점수"를
  V1에서 만들지 않는다고 명시한다.
- `OPS_BACKUP`은 A-16 그대로: `build_ops_backup_properties()`는 있고 호출자는
  없으며, 연결은 docs/04 §53 "Notion 데이터 과잉 방지"에 대한 결정이다.

**아키텍처가 막고 있는 것도 하나 있다** — 정책만이 아니다. Runner가 ATTENTION을
Notion에 기록하려면 `app`이 `ops_status`를 import해야 하는데, `ops_status.py`는
저장소 루트의 entrypoint이고 `app`은 합성 루트다(LayeringInvariantTests:
`"app": None`, 그리고 `src/`의 어떤 패키지도 루트 스크립트를 import하지 않는다).
그러니 선택지는 둘뿐이다: (a) ATTENTION 계산 2,000줄을 `app` 아래 패키지로
내리는 리팩터링, 또는 (b) Runner 뒤에 이어 도는 별도 entrypoint. 둘 다 결정이다.

**append-only run log가 구조적으로 말할 수 없는 것도 있다**: *부재*. Runner가 3주
멈추면 `OPS_RUNS`에는 새 행이 안 생길 뿐이고, Notion View는 없는 행을 강조할 수
없다. `ops_status.py`는 `Runner가 5.6일째 실행되지 않았다`를 말할 수 있는데, 그건
"마지막 실행 시각"과 "지금"을 비교하기 때문이다 — Notion 쪽에서 같은 문장을
만들려면 실행 기록이 아니라 **상태 행 하나를 갱신하는** 모양이 필요하고, 그건
`OPS_RUNS`의 append 계약과 다른 것이다.

**필요한 것**: (a) §9의 Page 공유 + OPS_RUNS 생성, (b) ATTENTION을 구조화된
값으로 내보내는 seam(위 layering 포함), (c) OPS_RISK 매핑에 대한 CEO/COO 승인,
(d) "부재"를 표현할 상태 행에 대한 결정.

### 13. 읽을 수 없는 전송 기록은 어느 숫자에도 없었다 (신규)

`agent/delivery.find_undelivered_events()`의 `_verdict()`는 `sent/`의 파일을
읽지 못하면 `(None, None, None)`을 돌려주고 루프는 그냥 `continue`했다. 필드도,
카운트도, `checked`조차 없다. 바로 그 자리 주석은 이렇게 적혀 있었다:

> `history/reconciliation.py` reports unreadable inputs separately for the
> same reason

`reconciliation.py`는 정말 그렇게 한다 — `UnreadableEvent` dataclass,
`ReconciliationResult.unreadable`, 전용 ATTENTION 줄까지. `outbox.DrainSummary`도
`unreadable`을 갖고, `CompanyActivitySnapshot`도 `unreadable_events`를 갖는다.
**선례를 인용하고 따르지 않은 것은 이 하나뿐이었다.**

결과는 이 저장소가 계속 찾아내는 그 모양이다: `sent/`의 손상된 파일 하나가
Event 하나의 전달 검증을 통째로 건너뛰게 하고, `ops_status.py`는
`전달 정합성 : OK`를 찍는다 — 전부 확인했고 전부 도착했을 때와 같은 줄이다.

**수정** — `UnreadableSentRecord` + `DeliveryResult.unreadable_records`,
`is_clean`에 포함(`ReconciliationResult.is_clean`과 동일), 그리고 세 번째 판정
`UNKNOWN`. "UNDELIVERED"는 이 경우에 틀린 단어다 — 스캔이 갖고 있지 않다고
명시한 전달 판정을 주장하게 된다. 원래의 절제는 그대로다: `undelivered`에도
`checked`에도 넣지 않는다. 읽지 못한 파일에서 `event_id`를 지어내지 않는다.

형제 스캐너 넷이 전부 unreadable 입력을 보고한다는 것을 테스트로 고정했다
(`test_every_sibling_scanner_reports_its_unreadable_inputs`) — 다음 스캐너가
조용히 다음 예외가 되지 않도록.

### 14. Notion 두 큐가 쓰는 두 필드를 아무도 읽지 않았다 (신규)

`RetryQueueEntry`는 upsert마다 `added_at`과 `attempt_count`를 기록하고,
`PendingDashboardRecord`도 같은 쌍을 기록한다. 저장소 전체에서 이 둘의
**소비자**를 찾으면 없다 — 큐 모듈이 쓰고, JSON을 왕복하고, 어떤 로그 줄도
어떤 상태 뷰도 큐 모듈 자신의 테스트 밖 어디도 읽지 않는다. BUG-39의 모양이다.

비용: BUG-13은 `NOTION_RETRY_REQUIRED`가 "Notion이 잠깐 죽었다"와 "Notion이
영원히 거부한다"를 같은 한 단어로 보고한다는 것을 확인하고 **이유 문자열**을
로그에 붙여 그 둘을 가르게 했다. 큐 자신의 두 필드는 같은 질문의 나머지 절반 —
얼마나 오래 막혀 있었는가, 몇 번 시도했는가 — 에 답하고 아무에게도 닿지 않았다.

Run Manifest의 `queued=` 지표는 대체재가 아니다. 세 가지 이유로:

1. **지난 실행의** 숫자다.
2. `notion_sync` component가 SUCCESS가 아닐 때만 출력된다.
3. **`to_event()`가 실패하는 큐 항목을 볼 수 없다.** `app/runner.py` 4a는 그것을
   `notion_unreadable`로 세고 `NOTION_UNREADABLE queued:<id>`를 로그에 적고
   **큐에 그대로 둔다** — `SyncResult`가 되지 않으므로 어떤 `queued` 카운트에도
   들어가지 않는다. 즉 큐에는 어떤 지표도 세지 않는 항목이 남을 수 있다.

**수정** — `ops_status.py`에 `NOTION — Retry Queue` 블록 신설:

    대기 중 Event       : N
    최대 재시도 횟수    : N
    가장 오래된 항목    : <added_at> (N.N일, event <id>)
    Dashboard 밀린 기록 : N

임계값은 **새로 만들지 않고** `SILENT_AFTER_DAYS`를 재사용했다 —
`_print_last_run()`이 이미 내린 같은 선택이고, 그만큼의 날짜를 살아남은 항목은
큐가 견디려고 존재하는 그 장애가 아니다. 손상된 큐 파일 둘 다 보고한다(특히
`dashboard_pending.json`이 조용하다 — `drain_pending()`이 CEO ④ 때문에 그것을
"비었음"으로 흡수하므로 밀린 기록이 영원히 재시도되지 않는데 그 반환값 말고는
아무것도 말하지 않았다).

경로는 `RUNTIME_DIR`에서 **호출 시점에** 파생한다. `notion.retry_queue.DEFAULT_QUEUE_PATH`를
쓰면 C31 §10의 함정에 그대로 걸린다 — `RUNTIME_DIR`을 돌려도 이 블록만
개발자의 실제 큐를 읽는다.

### 15. Overall/Transport 칸 신설의 실측 검증 (이 머신)

새 칸이 실제로 무엇을 말하는지 이 저장소의 현재 `runtime/`에 대해 읽기 전용으로
재현했다(`run_intake()`의 다섯 판정을 그대로 복제, 파일은 움직이지 않음):

    transport/  moved 0  already 0  not_stable 0  invalid 1  incomplete 0
    -> Transport Blocked = 1
    -> 다음 Runner 실행의 OPS_RUNS 행: Transport Moved 0, Transport Blocked 1,
       Overall WARN

C32 이전에는 같은 실행이 `Overall OK`를 냈다. 그리고 그 파일
(`MD-PLACEHOLDER.json`)은 `ops_status.py`가 이미 ATTENTION으로 보고하고 있던
바로 그 파일이다 — **로컬 뷰와 Notion 행이 이제 같은 사실을 말한다**는 것이
이 Sprint의 목표였고, 이 한 파일이 그 목표의 실측 증거다.

### 16. `run_agent.py`가 한 줄 위에서 막은 위조를 세 줄 뒤에서 허용했다 (신규)

`date_result.errors`는 `one_line()`으로 감싸 찍는다 — 주석까지 달려 있다:
*"read back from disk, none constrained to one line."* 그리고 세 줄 뒤,
`[FAILED] {result.error}`는 그대로 찍었다. `agent.run_once()`가 그 값을 어떻게
만드는지 보면 왜 문제인지 분명하다:

    error="; ".join(date_result.errors)        실패한 날짜 경로
    error=_describe_drain_failure(leftover)    outbox 경로

**같은 문자열이다.** 항목별로 escape한 내용을 잠시 뒤 join해서 raw로 찍은 것이다.
그리고 그 자리는 운영자가 "Event가 유실됐는가"를 판단하려고 읽는
`[FAILED]` 블록이다. C31 §7의 "절반짜리 수정"과 같은 모양. **수정.**

### 17. Run Manifest를 렌더링하는 곳이 둘인데 한쪽만 가드가 있었다 (신규 보안)

`run_summary.json`은 `ops_status._print_last_run()`과
`run_company_ops._report_run_summary()` 둘이 읽는다. `read_summary()`가 검증하는
것은 enum 셋(`status`/`severity`/`retryability`)뿐이고, `name`·`classification`·
`reason`·`metrics`·`artifact_refs`는 JSON에 들어 있는 그대로 돌아온다.

두 렌더러는 그 다섯 개 전부에 대해 의견이 달랐다:

| 필드 | ops_status.py | run_company_ops.py |
|---|---|---|
| `component.name` | `one_line()` | **raw** |
| `classification` | `one_line()` | **raw** |
| `reason` | 아예 찍지 않음 | **raw** |
| metrics key | **raw** | 안 찍음 |
| metrics value | `one_line()` | 안 찍음 |
| `artifact_refs` | **raw** | **raw** |

`reason`이 가장 나쁘다. `app/runner.py`는 NOTION_SYNC_INCOMPLETE에
`reason=queued[0].error`를 기록하고, 그것은 C31 §7이 **바로 이 파일 20줄 위에서**
`redact(one_line(...))`를 붙인 그 원격 응답 본문이다. 한 줄에서 redact한 본문을
세 함수 뒤에서 디스크를 거쳐 전문 그대로 찍고 있었다.

metrics 쪽은 더 얄궂다 — 가드 바로 위 주석이 *"the rule that nothing read back
from disk can forge a line should not depend on today's metric **list** staying
the way it is"*라고 적혀 있는데, list는 곧 **키**이고 가드는 **값**에만 걸려 있었다.

**수정** — 두 렌더러 모두. `ops_status`가 `reason`을 찍지 않는 선택은 그대로
두고(자기 주석에 이유가 적혀 있다) 테스트로 고정했다.

### 18. `[FAILED] Backup:`이 다른 프로그램의 stderr를 그대로 찍었다 (신규 보안)

`backup/git_ops._run_git()`은 모든 `GitOperationError`를
`f"git ... failed (exit {code}): {result.stderr.strip()}"`로 만든다 — subprocess의
여러 줄 출력이다. push 실패 시 git은 remote URL을 되울리고,
`https://<token>@github.com/...` 형태의 remote는 그 안에 자격증명을 담고 있다.
`oplog.SECRET_PATTERNS`는 GitHub 토큰 모양을 이미 알고 있는데 여기엔 아무도
적용하지 않았다 — Backup이 실패했을 때 운영자가 읽는 바로 그 메시지다.

**수정.** 분류는 **raw 메시지로** 그대로 유지한다 —
`is_authentication_failure()`는 git 자신의 문구를 찾으므로, redact된 문자열로
분류하면 치환이 판단 근거 문구를 먹어 BACKUP_FAILED/BACKUP_PENDING 결정을
바꿀 수 있다. 순서가 곧 계약이고, 테스트로 고정했다.

### 19. 이 Sprint가 쓴 렌즈 (다음 Sprint를 위해)

C32에서 결함을 낸 질문은 하나였고 다섯 번 먹혔다:

> **어떤 규칙이 한 곳에 적혀 있고 형제 자리에 적용되지 않았는가.**

| 규칙이 적힌 곳 | 적용되지 않은 형제 | §  |
|---|---|---|
| `oplog.append_line()` — 모든 sink에 `one_line`/`redact` | `init_notion.py` 4곳 | §3 |
| 같은 규칙 | `run_agent.py` `[FAILED]` | §16 |
| 같은 규칙 | `run_company_ops._report_run_summary()` 4필드 | §17 |
| 같은 규칙 | `run_company_ops._report_backup_failure()` | §18 |
| 같은 규칙 | `ops_status` metrics **키** (값만 가드) | §17 |
| `app/runner.py` — "getattr 기본값 금지" | `notion/dashboard.record_run()` 전체 | §2 |
| `history/reconciliation` — "unreadable 입력은 따로 보고" | `agent/delivery` | §13 |
| `_print_last_run` — naive/aware 가드 | `notion/sync._update()` | §7 |
| `dashboard._page_title()` — rich_text 전체를 읽는다 | `properties._extract_rich_text()` | §8 |

이 표를 만들면서 두 번째 렌즈가 나왔고 §20이 그것이다: *쓰고 아무도 읽지 않는
필드*를 §14처럼 손이 아니라 **전수로** 세기.

**아직 안 쓴 렌즈**(다음 Sprint 후보): 같은 질문을 *로그 파일*의 sink에 던지기
(`_append_log_line`이 `oplog`를 쓰므로 안전할 것으로 보이나 전수 확인 안 함),
그리고 반대 방향 — *읽는데 아무도 쓰지 않는* 값, 즉 항상 기본값인 채로 소비되는
필드.

### 20. "쓰고 아무도 읽지 않는 필드" 전수 조사 (신규 렌즈)

§14가 손으로 두 개(`RetryQueueEntry.added_at`/`attempt_count`)를 찾았다. 거기서
멈추지 않고 `src/`와 루트 entrypoint의 **모든 `@dataclass` 필드**를 AST로 훑어,
같은 집합의 모든 attribute *load*와 대조했다 — 필드 255개, 모듈 36개.
**정의 모듈 밖에 읽는 곳이 없는 필드가 30개.**

대부분은 정당하다: 정의 모듈이 스스로 로그 줄에 렌더링하는 값, 아직 필요한
호출자가 없는 값, 이미 기록된 dead capability(`RoleActivity.by_category`,
C31 §16). **세 개는 아니었고, 셋 다 같은 모양이었다** — 사람이 행동하려면
필요한 파일 이름 또는 시각을, 기록해 두고 보여주지 않은 것:

| 필드 | 증상 | 조치 |
|---|---|---|
| `RunSummary.finished_at` | 읽는 곳이 **아예 없음**(테스트도) — 디스크에 두 시각이 있는데 아무도 빼지 않았다 | LAST RUN에 `소요 시간` 추가 |
| `UnreadableEvent.event_path` | ATTENTION이 "N건"만 말하고 **어느 파일인지 말하지 않았다** — 이 뷰의 다른 모든 줄은 5개까지 이름을 댄다 | 이름 표기 |
| `PendingDashboardRecord.queued_at` | §14가 Retry Queue의 나이는 보고하고 이 큐는 빼먹었다 — 비대칭을 없애려고 만든 블록 안의 비대칭 | 나이 + ATTENTION |

`소요 시간`이 왜 값어치가 있는가: 이 파이프라인의 비용은 상수가 아니다. Backup은
300초 timeout으로 git을 부르고, `desktop_activity`는 무한히 자라는
`processed/`를 전부 읽고, Notion 단계는 네트워크를 기다린다. 4초 걸리던 실행이
4분 걸리기 시작하는 것이 그 셋 중 하나가 나빠지고 있다는 가장 이른 신호이고,
그 말을 해 줄 두 시각은 줄곧 디스크에 있었다.

**남은 27개는 여기 목록으로만 둔다** — 대부분 정당하고, 아닌 것을 가려내려면
필드마다 "이 값이 없어서 못 하는 일이 있는가"를 물어야 하는데 그건 §20이 연
렌즈의 다음 한 바퀴다. 다음 Sprint 후보:

    UndeliveredEvent.destination        어느 파일을 볼지 말하지 않는다
    OrphanedEvent.event_path            같은 모양 (event_id는 말한다)
    MonthlyResult.coverage/source_dates BUG-39 sweep이 한 번 놓쳤던 자리
    MonthlyItem.source_date             렌더러가 쓰지 않는다
    BackupLogEntry.backup_start/end     로그 줄에는 들어가지만 뷰에는 없다
                                        (= Backup 단계만의 소요 시간)

### 21. Workspace 검색이 첫 페이지만 보고 "이게 전부"라고 답했다 (신규)

Notion `/search`는 요청당 최대 100건을 돌려주고 `has_more`/`next_cursor`로 더
있다는 것을 알린다. `RealNotionTransport.search_pages()`는 `page_size: 100`을
보내고 `results`만 읽었다.

이것이 문제인 이유는 그 목록이 답하는 질문 하나 때문이다.
`diagnose_dashboard_bootstrap()`은 이 목록으로 `NEEDS_PARENT_CHOICE`("공유된
Page가 있으니 하나 고르라")와 `NEEDS_SHARED_PAGE`("Page부터 공유하라")를
가른다. 잘린 목록은 "Company Ops page가 공유돼 있는가"에 **자신 있게 틀린
'아니오'**를 답하고, 이미 공유해 둔 Page를 다시 공유하라고 운영자를 보낸다.

**수정** — cursor를 따라가되 **상한을 둔다**(10요청 = 1,000 Page). 운영자 명령
안에서 원격 API를 도는 `while`은 곧 hang이고, 멈춘 사실은 `search_truncated`로
보고한다(숨기지 않는다). `has_more`인데 cursor가 없는 응답은 같은 요청을 영원히
재전송하지 않도록 그 자리에서 멈춘다.

실 Workspace로 재확인(읽기 전용): 결과 동일 — `NEEDS_SHARED_PAGE`, hostable 0.
이 배포는 Page가 하나도 공유돼 있지 않아 원래부터 잘릴 것이 없었다. 고친 것은
**이 배포에서 안 물리는 결함**이고, 그래서 실측으로 잡히지 않고 코드로만
잡히는 종류다.

### 22. Notion 큐는 백업되지 않는다 (신규, DR 기록 — **미수정, SKIP**)

Backup의 범위는 `local_master/{daily,monthly}`뿐이다(docs/08 §26-27,
`backup/working_copy._ALLOWED_TOP_LEVEL_DIRS`). `runtime/state/`는 들어가지
않는다 — 의도된 설계다. 그 결과 디스크를 잃으면:

| 잃는 것 | 결과 |
|---|---|
| Company History (`daily`/`monthly`) | **잃지 않는다** — git remote에서 복원. 이 설계의 요점 |
| `notion_retry_queue.json` | 밀려 있던 Event가 **영원히 Notion에 반영되지 않는다** |
| `dashboard_pending.json` | 그 실행들의 OPS_RUNS 행이 영원히 생기지 않는다 |

나쁜 부분은 손실 자체가 아니라 **그 손실이 조용하다**는 것이다. §14가 만든
NOTION 블록은 파일을 읽어 "대기 중 Event N"을 말하는데, 복원된 머신에서 그
파일은 아예 없으므로 **0을 보고한다** — 큐가 비어 있을 때와 같은 줄이다.
"데이터의 부재가 건강으로 읽힌다", §4와 같은 모양, DR 층에서.

심각도는 제한적이다: README RULE 5가 Notion을 History의 임계 경로 밖에 두므로
잃는 것은 Notion Current State뿐이고 Company History는 온전하다.

**SKIP 사유**: `runtime/state/`를 Backup 범위에 넣는 것은 docs/08 §26 변경이다.
그리고 그것만으로 되지도 않는다 — state를 백업하면 "History보다 새로운 state"라는
새 복원 실패 모드가 생기고(`scheduler/consistency.py`가 이미 그 반대 방향을
검사한다), 어느 쪽을 신뢰할지는 docs/10 §49의 결정 영역이다.

**필요한 것**: (a) `runtime/state/`를 백업할지에 대한 결정, (b) 백업한다면
"복원된 state가 복원된 History와 어긋날 때" 규칙, (c) 그때까지는 복원 절차
문서에 "Notion Current State는 자동 복구되지 않는다"를 명시.

### 23. 이번 Sprint가 DR 경로를 하나 **개선**했다 (기록)

의도한 것은 아니지만 §8(rich_text 전체 읽기)이 복원 시나리오를 하나 고쳤다.
`runtime/state/`를 오래된 백업에서 되살리면 Retry Queue에 **이미 동기화된**
Event가 들어 있을 수 있다. 4a가 그것을 다시 sync하면 docs/04 §62의 중복 가드가
`Last Event ID`로 알아보고 `NOTION_SKIPPED_OLD_EVENT`를 돌려주며 큐에서
빠진다 — **그 가드가 rich_text 첫 조각만 읽고 있었으므로** 사람이 그 셀을
한 번이라도 편집한 프로젝트에서는 알아보지 못하고 재적용했다. 이제 알아본다.

---

## C31. Writer/Reader Mismatch Sprint

C30은 "SKIP 판단 옆에 적힌 주장이 낡았는가"를 물었다. C31은 **한 모듈이 쓴 것을
다른 모듈이 되읽는 지점**을 전수 대조한다. 이 저장소에는 그런 왕복이 여덟 쌍
있고, 세 쌍이 어긋나 있었다.

| 쓰는 쪽 | 읽는 쪽 | 결과 |
|---|---|---|
| `daily/markdown` | `monthly/parser` | **§1 어긋남 (수정)** |
| `daily/markdown` | `daily/late_events.existing_event_ids` | **§2 어긋남 (수정)** |
| `daily/markdown` | `ops_status._kept_but_not_rendered` | **§2b 어긋남 (수정)** |
| `monthly/markdown` | `monthly/generator._existing_generated_at` | 일치 (`split(":", 1)` 확인) |
| `monthly/markdown` | 자기 자신의 `Consolidated Items` | **§3 어긋남 (탐지)** |
| `late_events._update_metadata` | 자기 자신의 정규식 | 일치 |
| `runsummary.write_summary` | `ops_status._print_last_run` | 일치 (파생 필드는 재계산) |
| `oplog.append_line` | (읽는 코드 없음 — 사람) | 해당 없음 |

### 1. 평범한 요약 한 줄이 그 항목을 Monthly에서 지운다 (신규, **P1, 데이터 유실**)

`monthly/parser._first_bullet()`은 item block 안에서 요약 bullet을 찾는다.
docstring은 "라벨이 붙지 않은 유일한 bullet"이라고 정확히 적어 두었는데, 구현은
라벨 **집합**이 아니라 라벨 **모양**을 물었다.

    if re.match(r"^[A-Z][A-Za-z ]+:[ \t]", text):   # <- 건너뛴다

사람이 쓰는 요약은 늘 그 모양이다. 실측:

    ## Issues
    ### Auth Service
    - Fixed: login token refresh loop.
    - Owner: CTO Backend
    - Event ID: EVT-1

    parse_daily_markdown(...) -> items 1개 (EVT-2만), EVT-1 없음

`- Fixed: …`가 라벨로 걸러지고, 이어지는 `- Owner:` · `- Event ID:`도 걸러지므로
`_first_bullet()`이 `None`을 돌려주고 **항목 전체가 버려진다.** 같은 모양:
`Decision: ` `Resolved: ` `Note: ` `TODO: ` `Launch: ` `Beta: ` — 전부 재현했다.

**공격 입력도 손편집도 필요 없다.** Daily 파일은 완전하고, `consolidate_month()`는
`MONTHLY_GENERATED`를 돌려주고, 두 파일 사이에서 `Event Count: 2`와
`Consolidated Items: 1`이 조용히 어긋난다. Daily 커버리지 검사는 **파일 단위**라
항목 하나가 빠진 것을 볼 수 없다(`monthly/coverage.py`).

**Blast radius를 실 파이프라인으로 쟀다.** Repository → `generate_daily_history()`
→ 한 달치 Daily → 늦게 도착한 Event 1건까지 `update_daily_history()` → `run_once()`.
평범한 요약 5개 중 4개가 라벨 모양(`Adopted: ` `Fixed: ` `Note: ` `Resolved: `)이고
1개는 아니다:

    수정 후   item_count=5   렌더링 5   - Consolidated Items: 5
    수정 전   item_count=1   렌더링 1   - Consolidated Items: 1

**5건 중 4건이 그 달에서 사라진다.** 손편집도 공격 입력도 없고, Daily 파일은
전부 완전하며, `MONTHLY_GENERATED`가 보고된다.

**수정:** 렌더러가 실제로 쓰는 라벨만 건너뛴다(`_ITEM_LABELS` 7종). 손편집으로
라벨이 요약 위로 올라간 경우(docs/06 §57)를 견디는 성질은 그대로다.

테스트 4 + E2E 4건. 그중 하나는 `daily/markdown._render_item_block()` 소스에서
라벨을 추출해 `_ITEM_LABELS`와 대조하므로, 렌더러에 라벨이 하나 늘면 여기서
실패한다 — 이 결함이 다시 생기는 유일한 경로를 막는다.

### 2. 빈 `event_id` 하나가 Daily 파일을 무한히 부풀린다 (신규, **무결성**)

`validate_event()`는 required field를 `is None`으로만 검사하므로 `event_id=""`는
**오늘 유효한 Event**다(그것을 바꾸는 것은 A-15 = docs/02 결정). 렌더러는 그것을
`- Event ID: `로 쓴다 — 줄은 있고 값이 비었다.

`existing_event_ids()`의 `(\S.*)`는 비어 있는 값을 못 읽는다. 그래서 docs/06 §38의
중복 가드가 그 항목에 대해 **작동하지 않는다.** 실측, KEEP Candidate 1개 저장,
이미 닫힌 날짜, 평범한 3회 실행:

    run 1  UPDATED_LATE_EVENT ('',)
    run 2  UPDATED_LATE_EVENT ('',)
    run 3  UPDATED_LATE_EVENT ('',)
    -> `## Late Events` 블록 3벌 (Candidate는 1개)
    -> `- Late Events Added: 3` 바로 옆에 `- Event Count: 1`

`total_events`도 같은 함수로 세므로 두 숫자가 한 파일 안에서 서로를 부정한다.

**수정:** `(.*)`. 게이트를 넓히는 것이 아니라 **§18의 렌더러가 쓴 것을 §38의
가드가 읽게** 하는 것이다. Evidence 줄(`- <id>: <text>`)이 걸리지 않는 성질은
줄 시작 조건이 지키므로 그대로다(테스트로 고정).

**§2가 남긴 비대칭 하나는 일부러 두었다 (신규 기록, SKIP).** `monthly/parser.py`의
`_EVENT_ID_LINE`도 `(\S.*?)`를 쓴다. 그래서 빈 id를 가진 항목은 이제 Daily에는
정확히 한 번 들어가지만 Monthly에는 **여전히 들어가지 않는다.** 실측:

    Daily 파일의 item block : 2   (하나는 `- Event ID: `)
    Monthly로 파싱된 항목   : ['EVT-2']

**같은 `(\S…)`이지만 같은 질문이 아니다.** `late_events` 쪽은 "이 문서에 이미
있는가"(§38 중복 가드)를 묻고, 거기서 빈 값은 명확히 "있다"이다. 파서 쪽은
"이것을 통합해도 되는가"(§59, event_id로 dedup)를 묻는데, 손으로 편집한 Daily의
`- Event ID:` 빈 줄이 **"빈 id"인지 "id를 모른다"인지 구분할 수 없다.** 후자를
`""`로 통합하면 서로 다른 두 항목이 한 항목으로 합쳐진다 — 지금 동작이 막고 있는
것이 그것이다.

즉 이쪽은 §1처럼 "구현이 자기 docstring과 다르다"가 아니라 **무엇이 옳은지가
결정인 경우**다. 빈 `event_id`를 스키마가 받아들일 것인가라는 A-15와 같은 벽이고,
A-15가 정해지면 함께 닫힌다. 재발견 비용을 없애려고 실측만 남긴다.

### 2b. 그 수정이 드러낸 오탐 — C30 수정의 반대편 모서리 (신규)

C30 §5는 `_kept_but_not_rendered()`를 "줄 전체 비교"로 고쳤는데, 방법이
**줄을 분해하는 쪽**이었다: `startswith("- Event ID: ")` 후 접두사를 잘라낸다.
그 접두사는 공백으로 끝난다. 빈 `event_id`가 렌더링한 줄은 `strip()`하면
`- Event ID:`이므로 **접두사로 시작하지 않는다.**

    old: '' in rendered_ids            -> False  (Daily에 있는데 "영구 유실"로 보고)
    old: 'EVT-PAD ' in rendered_ids    -> False  (같은 이유, 반대쪽 끝)

즉 §2를 고쳤다면 그 항목은 이제 Daily에 정확히 한 번 들어가는데 `ops_status`가
"어떤 실행도 이것을 넣지 않는다"고 말하는 상태가 됐을 것이다.

**수정:** 분해하지 않고 **렌더러처럼 조립한다** — id로 줄을 만들어 파일의 줄
집합에서 찾는다. C30이 세운 원칙("렌더러가 답하는 것과 같은 질문을 묻는다")을
더 곧장 구현한 것이고, 잘라낼 접두사가 없으므로 이 모서리가 아예 없다.

### 3. Monthly는 버린 항목까지 세어 놓는다 (신규, **데이터 유실**, 탐지만)

`render_monthly_markdown()`은 `item.category in by_category`일 때만 Section에
넣는다. 네 값(DECISION/MILESTONE/ISSUE/LEARNING) 밖이면 어느 Section에도 들어가지
않는데, `- Consolidated Items:`는 `len(items)`를 쓴다. 실측:

    items 2개 (하나는 category="Decision")
    - Consolidated Items: 2
    Section    : Major Decisions, Source Records, Metadata
    EVT-2      : 없음
    consolidate_month() -> MONTHLY_GENERATED, item_count=2

**부패나 공격 없이 도달한다.** `## Late Events` 항목은 Daily 파일의
`- Category:` 줄로 자기 Category를 밝히고(docs/06 §37), `monthly/parser.py`는 그
줄의 텍스트를 그대로 읽으며, docs/06 §57 · docs/11 §71은 COO의 Daily 손편집을
명시적으로 허용한다. 손으로 친 `- Category: Decision` 하나가 그 Event를 그 달에서
지운다 — 다시 만들어도 같은 결과다.

**Daily 쪽 형제는 이미 기록돼 있었다**(`test_daily_history.py::
test_a_category_less_keep_candidate_silently_loses_its_detail`). Monthly에는 아무도
같은 질문을 겨눈 적이 없고, 이쪽이 더 나쁘다 — Daily는 `## Summary`에 요약이라도
남고 `## Evidence`에 id가 남지만, Monthly에는 그 둘 다 없다.

**왜 SKIP:** 알 수 없는 Category를 어느 Section에 넣을지는 docs/09 §14의 렌더링
결정이다. 결정이 필요 없었던 것은 **파일이 자기가 버린 항목의 개수를 두 줄 아래에
적어 둔다**는 사실이다.

**탐지:** `ops_status._monthly_counts_more_than_it_shows()` — 한 파일 안의 두
숫자만 비교하므로 창(window)도 오탐 경우도 없다. `claimed < rendered`(손편집)는
보고하지 않는다.

**이 탐지기 자신의 한계 — 스스로 물어 찾았고 실측했다.** summary는 escape 없이
렌더링되므로(BUG-11/27, docs/06 렌더링 결정 대기), 개행과 `- Event ID: …`를 담은
summary 하나가 이 검사가 세는 줄을 늘린다. 실측 — 항목 2개, 하나는 Category 때문에
버려지고 하나는 summary가 줄을 위조:

    - Consolidated Items: 2
    `- Event ID: ` 줄        2
    EVT-2 파일에 존재         False
    이 검사                   ()  ← 침묵

**방향이 중요하다.** 위조는 `rendered`를 **올리는** 것만 가능하므로 이 검사를
침묵시킬 수는 있어도 거짓 경보를 내게 할 수는 없다 — 경보로서 안전한 쪽이다.
`### ` 제목을 세도 같은 뿌리에 당한다(문제는 summary가 임의 Markdown을 쓸 수
있다는 것 자체다). 닫는 것은 BUG-11/27의 결정이지 이 함수의 몫이 아니다.
특성화 테스트로 고정했다.

**다음에 필요한 조건:** "네 Category 밖의 항목을 Monthly가 어떻게 다루는가"에
대한 docs/09 §14 결정. 정해지면 Daily 쪽 형제와 **함께** 닫힌다.

### 4. Secret 게이트가 자기 목록의 이름을 못 알아본다 (신규, **보안**, 탐지만)

`_looks_like_secret()`은 이름을 정확히 비교한다. Windows는 그러지 않는다.
같은 내용, 같은 in-scope 디렉터리, 실제 bare remote로 E2E:

    daily/ID_RSA   BACKUP_SUCCESS   push=SUCCESS
                   remote tree: daily/2026-08-05.md, daily/ID_RSA
                   git show main:daily/ID_RSA  -> 키 본문이 그대로 읽힘
    daily/id_rsa   BACKUP_FAILED    "secret files detected: daily\id_rsa"
                   remote tree: (비어 있음)

접미사도 같다(`server.PEM`, `client.Key`, `bundle.P12` 전부 미탐지).
`.env`/`.ENV`/`.Env`는 파일시스템이 한 파일로 합쳐 버리는데 — 그것이 바로 요점이다.
**어느 쪽 철자로 만들었는지라는 우연이 보호 여부를 정한다.**

BUG-55와 같은 뿌리(대소문자 구분 비교 × 구분하지 않는 파일시스템)의 **두 번째
위치**다. BUG-55는 무엇이 *백업되는가*를, 이쪽은 무엇이 *차단되는가*를 정한다.

**왜 SKIP:** 비교를 case-fold하면 게이트에 **새로운 BACKUP_FAILED 조건**이
생긴다. 그것이 정확히 E-15가 기록한 피해(거짓 양성 하나가 Company History를
원격에 못 가게 한다)이고, E-15/E-21의 후보 수정 전부가 결정 대기 중이다.
그래서 BUG-55가 받은 것과 같은 처우 — 보고하고 아무것도 바꾸지 않는다.

**탐지:** `ops_status._secret_names_the_gate_will_not_recognise()`, Local Master와
Working Copy 양쪽. 이름 목록은 게이트에서 import한다(C28이 세운 규칙: 두 번째
의견을 만들지 않는다). 게이트가 이미 보는 파일은 보고하지 않는다 — 한 파일에
두 줄은 그 절을 안 읽게 만드는 방법이다.

**다음에 필요한 조건:** E-15/E-21과 **같은 하나의 결정** — "Secret Scan은 무엇을,
어느 이름 규칙으로 지키는가". 정해지면 셋이 함께 닫힌다.

### 5. Baseline이 기록과 달랐다 — 테스트가 환경을 고정하고 있었다 (신규 2건)

C30은 `1916 passed / 0 failed`로 닫혔다. 이 머신에서 오늘 측정한 baseline은
**1914 passed / 2 failed**다. 둘 다 production 코드가 아니라 테스트의 결함이었고,
둘 다 "어제까지는 통과했다"는 종류다.

**(a) 실시간 시계에 매인 fixture.** `ArrivalVersusWorkDateTests._age_file()`이
`time.time() - days_ago*86400`으로 파일 mtime을 만드는데, 그 클래스의 단언은 전부
고정된 `NOW = 2026-08-10`에 대해 이뤄진다. `days_ago=6`은 실행일이 08-10이면
"NOW보다 6일 전"이지만 08-14면 "NOW보다 **2일 후**"다.

    caught_up_recently(NOW, days=3)  ==  silent >= 3 > arrival
    실행일 2026-08-13  arrival=3  -> 3 > 3 False -> 통과
    실행일 2026-08-14  arrival=2  -> 3 > 2 True  -> 실패

C27 §12가 고친 것과 같은 부류이고, 벽시계가 나흘 움직여야 드러나므로 그때 쓸려
나가지 않았다. **수정:** `NOW.timestamp()` 기준으로 나이를 만든다. 이 클래스의
`_age_file` 사용처 4곳 전부 NOW에 대해 단언하므로 전부 결정론적이 된다.

**(b) PowerShell UI 언어에 매인 문자열.** `test_both_effects_are_announced_as_
would_be_done`이 `assertIn("WhatIf", combined)`를 단언한다. 그것은 PowerShell이
`ShouldProcess` 미리보기를 찍는 접두사인데, **로케일마다 다르다.** 같은 머신,
같은 스크립트, 같은 PowerShell 5.1.26100.8875, 부모 프로세스의 UI culture만
다르게 해서 실측:

    ko-KR   WhatIf: 대상 "user environment"에서 ... 수행합니다.
    en-US   What if: Performing the operation ... on target ...

즉 이 테스트는 **한국어 Windows에서만 통과**하고 영어 머신에서는 반드시 실패한다.
docs/11이 배포 대상으로 삼는 머신이 후자일 수 있다. **수정:** 스크립트 자신이
`ShouldProcess`에 넘기는 문자열(`Set COMPANY_OPS_* variables`, `user environment`,
`Register scheduled task`, task 이름)로 단언한다 — 모든 로케일이 그대로 되뱉는다.
두 로케일에서 각각 실행해 확인했다.

**교훈:** "전체 통과"는 *이 머신, 이 날짜, 이 콘솔 언어에서* 통과했다는 뜻이다.
기록에 그 조건이 적혀 있지 않으면 다음 사람은 코드가 깨진 줄 안다.

### 6. 인증 실패 분류의 언어 의존 — 실측 후 제거 (하드닝)

`_AUTH_FAILURE_MARKERS`는 영어 문구 목록이고, git은 카탈로그가 설치돼 있으면
메시지를 번역한다. 번역되면 어떤 marker도 맞지 않아 진짜 자격증명 실패가
`is_authentication_failure()=False` → BACKUP_PENDING → **docs/08 §62가 금지하는
무한 재시도 루프**가 된다. 분류가 볼 수 없는 경로다.

**추측하지 않고 실측했다.** 이 머신의 Git for Windows 2.55.0.windows.3에는
카탈로그가 **하나도 없다**(`C:\Program Files\Git\mingw64\share\locale` 자체가
없다). `LC_ALL=ko_KR.UTF-8`에서도 영어로 답한다. 즉 현재 무해하다.

그래도 `_git_environment()`에 `LC_ALL=C` / `LANG=C`를 넣었다 — 같은 함수가
`GIT_TERMINAL_PROMPT`에 대해 이미 한 것과 정확히 같은 이유다("marker는 목록에
있는데 그 조건을 만들 수 있는 것이 아무것도 없었다"). **분류 목록은 그대로다** —
목록을 넓히는 것은 BUG-52이고 여전히 결정 대기다. 여기서 바뀐 것은 이미 결정된
목록이 실제로 적용된다는 것뿐이다.

### 7. 같은 부류 전수 조사 — 형제 확인

**(a) 대소문자 × 파일시스템.** §4를 찾고 나서 같은 모양을 전부 훑었다.
`safe_candidate_filename()`이 만드는 `HIST-{event_id}.json`은 대소문자만 다른 두
id에 대해 Windows에서 한 경로가 되고, `FileHistoryRepository.save()`는
`overwrite=False`라 `FileExistsError`를 던진다 — Runner의 History Filter 단계에는
per-event 오류 처리가 없으므로(BUG-20 characterization) 실행 전체가 중단된다.

**도달 불가로 확인했다.** 그 쌍이 History Filter까지 오려면 두 Event가 모두
ACCEPTED로 `processed/`에 있어야 하는데, 파일 이름 기반 중복 억제가 한 층 앞에서
먼저 발화한다(E-22). 그리고 E-22 자체가 Agent 경로로는 구조적으로 불가능함이
C28에서 측정돼 있다(uuid5는 전부 소문자). 새 항목 없음 — 같은 조사를 다시 하지
않기 위해 기록한다.

**(b) 느슨한 멤버십 검사.** C30 §6이 `in text`를 훑었다. 이번에는
`startswith`/`endswith` 13곳을 전수 확인했다. `_looks_like_secret`(§4) 외에는
전부 자기 writer와 짝이 맞거나 우리 코드가 만든 접두사(`.tmp-`)다.

### 8. 성능 — 새 검사의 비용을 측정했다

§3의 탐지기는 Monthly 파일을 전부 읽는다. C27이 정정한 편향(cold/warm)을 그대로
적용해, 매번 새로 쓴 트리에서 cold로 쟀다:

    24개월 x  30항목   serial 124.7 ms   threaded  5.6 ms
    120개월 x 60항목   serial 667.9 ms   threaded 14.4 ms
    이 머신 실제 runtime(0개월)              0.014 ms

`ops_status.py` 전체가 이 머신에서 ~44 ms다. serial이면 2년치 Monthly가 상태
조회 시간을 세 배로 만든다. `_read_keep_candidates()`가 이미 쓰는
`ThreadPoolExecutor` + `_READ_WORKERS` 관용구를 그대로 재사용해 22~46배 줄였고,
결과 순서는 정렬된 파일명 순서 그대로다(`map`이 입력 순서를 보존한다).

§4의 탐지기는 두 트리를 `rglob`로 한 번씩 걷는다. Working Copy 실측 97개 파일 중
**90개가 `.git/`**이라 바로 위 잔여물 검사와 같은 이유·같은 방식으로 제외했다
(5.31 ms → 3.88 ms). Working Copy 쪽 결과는 E-21 줄과 똑같이
`_would_reach_the_commit()`을 통과시킨다 — C26이 측정한 대로, git에게 묻지 않고
보고하면 docs/08 §28대로 `.gitignore`를 둔 **올바른** 설정에서 상시 거짓 경보가 된다.
Local Master에는 물어볼 저장소가 없으므로 그쪽은 목록 그대로다(그 경로의 사실은
"sync가 복사하고 게이트가 안 막는다"이지 "git이 커밋한다"가 아니다).

**그리고 개별 probe가 아니라 명령 전체를 쟀다**(목표가 요구하는 대로). 이 머신의
실제 runtime에서 5회, 네 블록 전부:

    C31 이후   48.2  49.0  53.7  53.7  55.1 ms   (중앙값 53.7)
    C28 기준                                      ~44 ms

**+9.7 ms.** 검사 두 개와 `one_line` 가드 여섯 개를 더한 값이고, 이 머신에는 아직
Monthly 파일이 0개라 §3 탐지기는 0.014 ms만 쓴다 — 증가분의 대부분은 §4의 두
`rglob`이다. 사람이 대화형으로 치는 명령에서 사실상 보이지 않는다.

### 9. 넣지 않기로 한 검사 하나 — 측정 후 판단

§1을 고친 뒤에도 `monthly/parser.py`가 항목을 조용히 버리는 경로가 둘 남는다:
item block에 `- Event ID:` 줄이 없을 때, 그리고 `## Late Events` 항목에
`- Category:` 줄이 없을 때(파서 스스로 "추측해 넣는 것보다 Daily에만 남기는 편이
낫다"고 적어 둔 **의도된** 결정이다). 결정은 결정이지만 그 결정이 만든 유실을
아무도 보고하지 않는 것은 별개다 — E-17에 적용한 것과 같은 논리다.

탐지기는 만들 수 있다: 소비 가능한 `##` Section 안의 `###` 블록 수와
`parse_daily_markdown()`이 돌려주는 항목 수를 비교하면 된다. 한 파일 안에서
끝나므로 창도 타이밍도 없다.

**넣지 않았다. 근거는 두 개의 측정이다.**

    이 머신의 실제 Daily 6개   블록 13 / 파싱 13   (불일치 0)
    730개 Daily x 4항목        cold threaded read 540 ms + parse 21 ms

540 ms는 `ops_status.py` 전체(~44 ms)의 12배다. 그리고 남은 두 경로는 각각
손편집이거나 `include_category=True`가 생기기 **이전** 버전이 쓴 Daily를 복원한
경우인데, 현재 코드의 `append_late_events()`는 항상 그 bullet을 쓴다.

즉 **비용은 확정적이고 발생은 관측되지 않는다.** 상황이 바뀌는 조건을 적어 둔다:
백업에서 복원한 Daily가 섞였거나, `## Late Events`를 손으로 편집한 이력이 생기면
그때 이 검사를 넣는다(그때는 대상을 그 달로 좁힐 수 있다).

Daily 쪽 형제 하나는 이미 덮여 있다는 것도 확인했다 — 렌더러가 Category 때문에
버린 KEEP Candidate는 그 날짜 Daily에 `- Event ID:` 줄이 없으므로
`_kept_but_not_rendered()`가 Candidate 방향에서 잡는다. Monthly에는 그런 반대
방향 검사가 없었고, 그것이 §3이다.

### 10. C27이 "결정이 필요하다"고 남긴 경보 하나 — 결정은 필요 없었다

C27 §8은 `is_incomplete_write()`를 소비자 6곳에 적용하면서 Collector를 **일부러
제외**했고(그 판단은 옳다), 남는 것을 이렇게 적었다:

> 남는 것은 **잘못 이름 붙은 경보 하나**이고, 그것을 고치려면 Collector가
> `incoming/`에서 무엇을 소비하는지를 바꿔야 한다 — 읽는 쪽의 필터가 아니라
> docs/03의 처리 파이프라인이다.

**앞 절은 맞고 뒤 절은 두 가지를 한 문장에 묶었다.** Collector가 staging 파일을
소비하지 *않게* 만드는 것은 확실히 docs/03의 결정이다. 그 결과를 **보고서가
뭐라고 부르는지**는 아니다.

증상: `write_event_json()`의 기본 디렉터리가 `runtime/events/incoming/`이고 거기에
`mkstemp`한다. Desktop 4 reporter가 쓰기 도중 죽으면 Collector가 읽는 바로 그
디렉터리에 `.tmp-….json`이 남고, 잘린 것은 REJECTED로 `rejected/`에 staging
이름 그대로 들어간다. 그러면 ATTENTION이:

    Collector가 거부한 Event 1건 — 사람이 확인해야 한다

**거짓 문장이다.** 거부된 Event는 없고, 이 머신의 쓰기가 중단됐을 뿐이며, 보낸
Desktop은 존재하지 않는다. 운영자를 엉뚱한 기계로 보낸다.

**수정(파이프라인 무변경).** `IntakeBacklog.rejected_incomplete_write`를 나눠
세고, ATTENTION에 다른 문장을 쓴다 — "거부된 Event가 아니다 … 지워도 안전하다".
`name_collision`은 **필터하지 않은 목록**으로 계속 계산한다: 이름이 잡혀 있는지는
그 파일이 staging이든 아니든 같은 사실이고, `run_once()`는 어느 쪽이든 destination을
거부한다(BUG-43). Collector가 무엇을 소비하는지는 한 글자도 바뀌지 않았고,
C27이 세운 경계 테스트가 그대로 그것을 고정한다.

**그리고 C27의 경계 테스트 docstring을 갱신했다** — 그 안에 적힌 "ATTENTION says
… a false statement"가 이제 사실이 아니다. C30이 사냥한 바로 그 모양(판단은
옳은데 옆에 적힌 주장이 낡음)을 이번 Sprint가 스스로 만들지 않도록,
같은 커밋에서 고쳤다.

테스트 6건(`test_observability.py::RejectedStagingResidueTests`). 그중 하나는
`collector/runtime.run_once()`가 여전히 `is_incomplete_write`를 쓰지 않음을
소스에서 확인해, 이 수정이 경계를 건드리지 않았음을 고정한다.

### 11. BUG-58 재측정 — 요청받은 대로 쟀고, 그 과정에서 보안 결함이 나왔다

F-3의 BUG-58에는 *"부분 완화됨 … **남은 범위 재측정 필요**"*라고 적혀 있다.
승인이 필요 없는 측정이므로 실제로 쟀다.

**결과: sync 경로에서는 남은 범위가 없다.** 실제 `RealNotionTransport._request()`에
Notion의 진짜 400 본문을 물려 끝까지 따라갔다:

    1. NotionAPIError      Notion API returned 400: Bad Request | {"code":"validation_error", …}
    2. SyncResult.error    그대로 (str(exc))
    3. notion_sync.log     REASON …  ← `_log_notion_sync()`가 붙인다

`dashboard.py` · `bootstrap.py` · `client.health_check()`도 전부 `str(exc)`를
쓴다. Run Manifest의 `Failure.reason`은 `queued[0].error` 하나뿐이지만 그것은
유실이 아니라 Manifest의 설계다("Detail is referenced, never inlined" — 같은
component의 `artifact_refs`가 로그를 가리킨다). **F-3 항목을 그렇게 갱신했다.**

**그런데 그 추적 중에 신규 결함이 나왔다(보안).** 그 문자열은 **원격 HTTP 응답
본문**이고, `oplog.append_line()`은 정확히 그것 때문에 `redact()`와 `one_line()`을
건다 — 그 docstring이 드는 근거가 *"a 502 page containing `Authorization: Bearer
ntn_…` put the token straight into notion_sync.log"*이다. 그런데 같은 문자열을
`run_company_ops.py::_print_result()`가 **아무것도 걸지 않고 stdout에 찍는다.**

실측 — Notion 대신 응답한 프록시가 502와 함께 요청 헤더를 되돌려주는 경우:

    notion_sync.log   토큰 [REDACTED], 1줄
    이 stdout         Authorization: Bearer ntn_… 전문, 4줄

두 번째 줄의 "4줄"도 장식이 아니다. 본문의 개행이 `  - <event_id> …` 결과 줄을
**추가로 위조**한다 — 운영자가 무슨 일이 있었는지 판단하는 바로 그 보고서에서,
BUG-6과 같은 모양이 아무도 겨눈 적 없는 sink에서 재현된다.

**수정: sink에 건다.** 문자열이 만들어지는 `notion/transport.py`가 아니라
출력 지점이다 — `notion`은 `events`만 import할 수 있고(LayeringInvariantTests)
그 표를 넓히는 것은 **아키텍처 결정**이다. `run_company_ops.py`는 composition
root의 entrypoint라 이미 모든 것 위에 있고, `ops_status.py`가 같은 이유로 이미
`one_line`을 import하고 있다. 진단 내용(`502`, 어느 property가 거부됐는지)은
그대로 남는다.

테스트 1건(`test_architecture_invariants.py::…::
test_a_notion_response_body_reaches_stdout_redacted_and_on_one_line`) — 토큰
부재, `[REDACTED]` 존재, 진단 정보 잔존, 그리고 SyncResult 1건당 결과 줄 정확히
1줄을 모두 단언한다.

### 12. 같은 질문을 자매 entrypoint에 던졌더니 더 나쁜 것이 나왔다 (신규, **보안**)

§11을 고친 뒤 "이 sink에만 있는 문제인가"를 물었다. `ops_status.py`가 더 나빴다.

`event_id`는 다른 Desktop에서 OneDrive를 건너오고 docs/02는 그것을 "present and
non-null"로만 제약한다(A-15). 그래서 개행이 든 id가 그대로 저장되고,
`_kept_but_not_rendered()`·`find_orphaned_events()`·`_candidates_before()`가 그것을
ATTENTION 메시지에 끼워 넣는다. 실측 — KEEP Candidate 1건, id가
`"X\n  ! 모든 검사 통과 — 사람이 지금 할 일은 없다"`로 시작:

    ! KEEP Candidate 1건이 저장돼 있는데 그 날짜의 Daily History에 없다: X
    ! 모든 검사 통과 — 사람이 지금 할 일은 없다 (2026-08-05) — 그 날짜는 …

**둘째 줄은 전부 공격자가 쓴 것이고**, 진짜 발견과 똑같은 `  ! ` 접두사를 달고
ATTENTION 안에 서 있으며, 그 절이 보고하는 내용의 **반대**를 말한다. AGENT.md
§6은 운영자에게 이 뷰를 **가장 먼저** 보라고 한다 — 이 시스템에서 줄을 위조하기에
가장 값어치 있는 자리다.

**BUG-6과 같은 모양이다.** `oplog.one_line()`이 `collector.log`에 대해 이것을
이미 닫았고(C10), 이 파일도 Run Manifest metrics에 대해서는 그 논리를 이미
받아들이고 있었다 — *"디스크에서 되읽은 것이 줄을 위조할 수 없다는 규칙이 오늘의
metric 목록에 의존해서는 안 된다"*. **metrics가 작은 쪽이었다.** 큰 쪽인
ATTENTION 줄들이 정작 신뢰할 수 없는 id를 나른다.

**수정: sink에 건다.** `main()`의 출력 루프 한 곳이므로 지금 있는 40여 개
append 지점과 앞으로 추가될 것 전부가 자동으로 덮인다. 같은 `!` 접두사와 고정
들여쓰기를 쓰는 블록 내부 출력 두 곳(orphaned Event, undelivered Event)과, 파일에서
되읽는 Run Manifest 문자열(`started_at`, `component.name`, `classification`)에도
같은 규칙을 적용했다.

**`redact()`는 일부러 걸지 않았다** — ATTENTION 메시지는 파일명·id·개수로만
만들어지고 파일 **내용**을 나르지 않는다. 예외 메시지를 나르는 둘은 state 파일
파싱 오류이고 그 텍스트는 위치 정보뿐이다("Expecting ',' delimiter: line 3
column 5"). 운영자가 조치해야 할 경로를 과다 redact하면 얻는 것보다 잃는 것이
크다. 언젠가 응답 본문을 나르기 시작하면 그때는 `redact()`도 필요하다 —
`run_company_ops.py`가 이미 그런 경우다(§11).

**세 번째 entrypoint까지 같은 규칙으로 맞췄다.** `run_agent.py`도 날짜별 오류를
`    - {error}`로 찍는데, 그 문자열은 Signal 파일명·파싱 오류·Transport 실패다.
`agent.py`가 만드는 지점에서 이미 `redact()`를 걸어 두었지만 **줄을 끝내지 못하게
하지는 않는다.** 실측 — 개행이 든 오류 하나가
`  2026-01-01: COMPLETED events=99 …` 결과 행을 통째로 위조한다(수집되지 않은
날짜를 수집된 것처럼 보이게 한다). `one_line`을 걸어 닫았다.

이제 세 entrypoint가 모두 같은 규칙을 지킨다:

| sink | 걸린 것 |
|---|---|
| `oplog.append_line()` (모든 로그) | `redact` + `one_line` (기존) |
| `run_company_ops.py::_print_result()` | `redact` + `one_line` (§11 신규) |
| `ops_status.py::main()` + 블록 3곳 | `one_line` (§12 신규 — 내용을 나르지 않으므로 redact 제외, 이유는 코드 주석) |
| `run_agent.py::main()` | `one_line` (§12 신규 — redact는 `agent.py`가 이미 원천에서 건다) |

테스트 5건(`test_observability.py::AttentionLineForgeryTests`) + 1건
(`test_agent.py::…::test_a_per_date_error_cannot_forge_a_result_row`), 각각
수정 전 코드에 대해 **7건 / 1건 실패**를 확인했다. ATTENTION 쪽 하나는 `\n` 외에
`str.splitlines()`가 끊는 나머지 문자들(`\r` `\v` `\f` `\x1c` …)도 함께 덮이는지
확인한다 — `replace()`가 아니라 `one_line()`을 쓰는 이유가 그것이다.

---

### 13. 미래 날짜 State 포인터 — C17이 Agent에만 물었던 질문 (신규 3건, **데이터 정지 / 검사 침묵**)

날짜 경계 산술을 훑다가 나왔다. `scheduler`·`agent.catchup`·`monthly`의 날짜
걸음은 전부 `timedelta`/`calendar` 기반이라 월말·연말·윤년에 결함이 없다(확인함).
결함은 산술이 아니라 **그 산술에 들어가는 포인터**에 있었다.

`_generate_pending_dates()`는 `start = 포인터 + 1일`, `end = 어제`를 계산한다.
포인터가 달력보다 앞서 있으면 `start > end`가 되어 루프가 **0회** 돈다. 뒤로 걷지
않는 것은 옳은 동작이고, 그래서 **스스로 회복하지 않는다.**
`monthly.pending_months()`가 한 단위 위에서 똑같이 동작한다.

**C17은 이 모양을 Agent state에 대해 이미 찾아 보고하고 있다** —
`agent/status.py`가 지금도 이렇게 말한다: *"agent state says it has collected
through X, which is in the future … nothing will be collected until that date
arrives"*. **Runner 자신의 state 파일 두 개에는 아무도 같은 질문을 하지 않았다.**

**실측(실 Scheduler, 실 Repository).** 포인터 `2026-12-25` + 그 Daily 파일 존재,
"지금" 2026-08-14, 2026-08-12를 기다리는 KEEP Candidate 1건:

    scheduler.run_once()   COMPLETED, generated=()
    state consistency      CONSISTENT
    ATTENTION              (없음)
    2026-08-12.md          생성되지 않음

Monthly 쪽도 같다 — 포인터 `2027-06`, "지금" 2026-08: `results=()`, ATTENTION
없음, 화면에는 `마지막 통합한 달: 2027-06`이 성과처럼 찍힌다.

**`check_state_consistency()`는 이것을 볼 수 없다.** 그 검사는 "주장한 Daily 파일이
존재하는가"만 묻는데, 도달 가능한 형태에서는 **파일이 존재한다** — 시계가 앞서
있는 동안 Scheduler가 직접 썼기 때문이다.

**도달 경로.** 시계가 앞섰다가 교정된 경우(CMOS 배터리 방전, NTP 점프, 오래된
시계로 재개된 VM)나 그런 머신에서 복원한 state 파일 — C17이 Agent 쪽에 대해
기록한 것과 같은 두 원인이다.

**탐지만, 그리고 결정이 필요 없다.** 같은 파일이 이미 답을 정해 두었으므로
(C28 §6의 규칙: "승인 필요라고 적혀 있지만 이미 결정된 것") 그 답을 형제 state
파일에 적용한 것뿐이다. 고치는 것은 "Company History가 어느 날짜부터 재개해야
하는가"를 정하는 일이고 그것은 docs/10 §46 금지·§64 운영자 판단이다.

**오탐 불가.** `end`는 항상 어제이고 §49는 진행 중인 달의 통합을 금지하므로,
건강한 실행은 이 포인터를 오늘/이번 달 너머로 절대 보내지 못한다. 경계값
(오늘 · 이번 달)은 영구 정지를 만들지 않으므로 일부러 보고하지 않는다.

**그 부류를 전수로 훑다가 세 번째가 나왔고, 그것이 가장 나쁘다.**
`backup_state.last_successful_backup`이 미래면
`_history_newer_than_the_last_backup()`이 **아무것도** 반환하지 않는다 — "이 파일은
이 머신에만 있다"를 말하려고 만든 **안전 검사가 통째로 침묵한다.** 앞의 둘은
*일*을 멈추고, 이것은 *검사*를 멈춘다. 실측, 한 번도 push된 적 없는 진짜 Daily 1개:

    last_successful_backup 2026-08-01   -> 경보 1건 (정확)
    last_successful_backup 2027-05-01   -> 경보 0건

`backup/state.py`도 실행의 시계로 이 값을 쓰므로 원인이 같고, 복원된
`backup_state.json`은 그것을 머신 사이로 옮긴다. **침묵하게 될 그 줄보다 먼저**
보고하도록 넣었다 — 운영자가 침묵을 신뢰하는 대신 침묵의 이유를 읽게 하려는
것이다("아래 줄이 조용한 것은 안전하다는 뜻이 아니다"). naive/aware 비교 가드는
`_history_newer_than_the_last_backup()`이 이미 쓰는 것과 같다.

**그리고 이 검사는 하마터면 거짓 경보를 달고 나갈 뻔했다 — 기존 테스트 2건이
잡았다.** 처음 구현은 이 타임스탬프를 호출자의 `now`와 비교했다. 그런데
`ops_status.py`는 **Runner가 도는 중에 실행해도 안전하다고 스스로 약속**하고
`main()`은 시계를 맨 위에서 한 번만 읽는다 — 그 뒤 수백 ms 후에 Backup이 끝나면
그 값은 정당하게 `now`보다 뒤다. 건강한 머신에서 두 명령을 같이 돌릴 때마다
ATTENTION에 한 줄이 서는 셈이었다. 코드를 읽어서가 아니라 **"needs no attention"
픽스처 2건이 실패해서** 드러났다(`_healthy_backup_state()`가 일부러 실제 시계를
쓴다 — 같은 상황의 1초짜리 판본이다).

**두 가지를 고쳤다.** (1) 비교 대상을 **실제 시계**로 바꿨다 — 이 값과 그것이
비교되는 파일 mtime은 둘 다 실시간 측정이고, 하나만 고정 시계에 묶는 것이 바로
그 픽스처가 이름 붙인 함정이다(운영에서는 같은 값). (2) **허용 오차 1시간**을
뒀다 — 피해가 거리에 비례하기 때문이다. 1시간 앞선 값은 미백업 검사를 1시간
가리고 스스로 낫지만, 몇 달 앞선 값은 달력이 올 때까지 가린다. 1시간은 어떤
실행 시간보다도 훨씬 길고(git subprocess timeout만 300초) "사실상 영구"보다는
훨씬 짧다. 실측:

    +1초 (Runner가 끝나는 중)  경보 0
    +5분 / +59분               경보 0
    +3시간 / +300일            경보 1

그리고 이 검사의 테스트는 **오늘 기준 상대 날짜를 쓰지 않는다**(`9999-01-01` /
`2000-01-01`) — 이번 Sprint가 §5(a)에서 제거한 시한폭탄을 새로 심지 않기 위해서다.

**포인터 전수 목록(형제 확인 완료).**

| 포인터 | 미래일 때 | 상태 |
|---|---|---|
| `agent_state.last_successful_collection_date` | Agent가 수집을 멈춤 | C17에서 보고 중 |
| `daily_history_state.last_successful_daily_close` | Daily가 멈춤 | **C31 신규** |
| `monthly_history_state.last_successful_monthly_close` | Monthly가 멈춤 | **C31 신규** |
| `backup_state.last_successful_backup` | 미백업 검사가 침묵 | **C31 신규** |
| `agent_state.last_run` | 정보용 — 정지 없음 | 해당 없음 |
| `collector_state` / retry queue | 포인터가 아니라 집합 | 해당 없음 |

테스트 14건(`test_observability.py::FutureDatedStatePointerTests`). 그중 하나는
**실제 Scheduler를 돌려** COMPLETED/CONSISTENT/파일 없음을 함께 단언하고, 하나는
`agent/status.py`의 선례 문구가 아직 거기 있는지 확인한다 — 그 답이 사라지면 이것은
"이미 내려진 결정의 적용"이 아니게 되기 때문이다.

### 14. 그 조사가 스스로 걸려든 함정 — `RUNTIME_DIR`이 절반만 듣는 손잡이였다 (신규)

§13을 확인하려고 Agent 쪽 선례가 **실제로 살아 있는지** 물었다. 임시 트리에
미래 날짜 `agent_state.json`을 두고 `RUNTIME_DIR`을 거기로 돌린 뒤 `_print_agent()`를
불렀더니 이렇게 나왔다:

    AGENT attention items: 1
      ! agent has not run for 3 day(s)

미래 날짜 경고가 없다. **하마터면 "C17의 선례가 ops_status에 연결돼 있지 않다"는
결론을 기록할 뻔했다.** 실제 원인은 다른 것이었다 —

    AGENT_DIR = RUNTIME_DIR / "agent"     # import 시점에 고정

모듈 상수라 import 때 얼어붙는다. 그래서 `RUNTIME_DIR`을 돌려도 AGENT 블록만
**이 저장소의 진짜 `runtime/agent`**를 읽고 있었다. 위 "3 day(s)"는 픽스처가 아니라
**이 머신의 실제 Agent 상태**였다. `AGENT_DIR`까지 함께 돌리자 선례는 정확히
동작했다(그래서 §13의 논거 자체는 유효하다).

**그리고 그 규칙은 이미 이 파일 안에 적혀 있었다.** 바로 위쪽
`_runner_lock_path()`의 docstring: *"Resolved per call, not at import:
`RUNTIME_DIR` is rebound by tests … and a path frozen at import would keep
pointing at the old one."* Runner Lock 경로는 그 규칙을 지키고 `AGENT_DIR`만
지키지 않고 있었다 — C20 §3(명세가 두 이름을 적었는데 목록에 하나만 있었다),
C27 §4(Runner Lock은 감시하고 Agent Lock은 감시하지 않았다)와 **같은 모양**이다.
새 규칙이 아니라 빠뜨린 한 곳에 적용한 것이다.

**C13 결함 2와 같은 것이 두 번째 자리에서 반복됐다.** 그때의 문장이 그대로 맞는다:
*"a test calling it directly picked up the repository's own live manifest — which
said SUCCESS — and got exit 0 for a Backup failure."* 운영에서는 세 상수가 항상
일관되므로 production 결함은 아니다. **테스트·진단이 조용히 실기계를 읽는 함정**이고,
그런 테스트는 엉뚱한 이유로 통과하거나 실패한다.

**수정:** `_agent_dir()`로 호출 시점에 파생한다. 이제 `RUNTIME_DIR`이 이 모듈이
소유한 모든 경로의 **유일한 손잡이**이고, 뷰를 격리하는 일이 절반만 될 수 없다.
기존 테스트에서 죽은 `module.AGENT_DIR = ...` 대입도 함께 지웠다 — 아무도 읽지
않는 대입이 남아 있으면 다음 사람이 그것을 필요한 것으로 읽는다.

테스트 3건(`test_observability.py::RuntimeDirIsTheOnlyKnobTests`). 두 개는 동작
(`RUNTIME_DIR`만 돌려도 AGENT 뷰와 Agent Lock 경로가 따라온다)을, 하나는 **구조**를
고정한다 — 모듈 레벨에서 `RUNTIME_DIR`로부터 경로를 얼리는 새 상수를 AST로 금지한다.
그 가드가 예전 형태를 실제로 잡는지 확인했다(`frozen: ['AGENT_DIR']`).

### 15. `exists()`가 답하는 질문은 그 코드가 묻는 질문이 아니었다 (신규, **P0**)

`exists()`는 **"이 이름이 이미 쓰였는가"**에 답한다. `2026-08-12.md`라는
**디렉터리**는 그 질문에 예라고 답하고, **"이 날의 Company History가 쓰였는가"**에는
아니라고 답한다. 네 곳이 앞의 술어로 뒤의 질문을 묻고 있었다.

**실측(실 Scheduler, 실 Repository).** 2026-08-12를 기다리는 KEEP Candidate 1건 +
같은 이름의 디렉터리:

    scheduler.run_once()              COMPLETED
    result.generated_dates            ('2026-08-12', '2026-08-13')
    check_state_consistency()         CONSISTENT
    (daily/'2026-08-12.md').is_file() False

**실행이 쓰지도 않은 날을 "생성했다"고 자기 결과에 적었고**,
`last_successful_daily_close`를 그 너머로 옮겼다. 그 Candidate는 이제 어떤 실행도
닿지 않는다 — docs/07 §30의 "순서대로 닫고 빈틈을 남기지 않는다"가, **루프가 볼 수
없는 빈틈** 때문에 무너진다. 그리고 그 날이 빠진 것을 잡으라고 만든 두 검사
(`check_state_consistency`, `_kept_but_not_rendered`)가 **둘 다** 있다고 동의한다.

Monthly도 한 단위 위에서 같다 — `2026-08.md` 디렉터리 → `MONTHLY_UNCHANGED`,
그리고 UNCHANGED는 catch-up 포인터를 전진시킨다(`run_once()`). 그 달은 조용히
영영 쓰이지 않는다.

**규칙을 세우고 전수 적용했다.**

| 묻는 질문 | 올바른 술어 | 해당 |
|---|---|---|
| 이 산출물이 있는가 | `is_file()` | scheduler, consistency, monthly×2, late update, **reconciliation(A-20 탐지기), outbox.is_sent** — **6곳 수정** |
| 이 이름이 쓰였는가 | `exists()` | outbox.stage, collector destination, intake 중복 — **그대로가 옳다** |

전수 훑는 중에 두 개가 더 나왔고 둘 다 같은 부류다:

* **`history/reconciliation`** — A-20 탐지기. "이 Event의 Candidate가 쓰였는가"를
  묻는데, Candidate 이름의 디렉터리 하나가 그것을 침묵시킨다. 실측: 진짜 고아
  Event 1건이 아무것도 없을 때는 정확히 보고되고, 그 이름의 디렉터리를 만들자
  **보고가 사라졌다.** §13의 backup 타임스탬프와 같은 "검사를 침묵시키는" 부류다.
* **`agent/outbox.is_sent()`** — "이 Event가 전달됐는가". 디렉터리에 대해 True를
  돌려주면 Agent가 **보낸 적 없는 Event를 다시 보내지 않는다.** `sent/`는 outbox의
  "Event를 잃지 않는다" 보장이 현금화되는 바로 그 자리다.

두 번째 줄이 중요하다: 디렉터리도 이름을 차지하므로 그쪽을 `is_file()`로 좁히면
실행이 디렉터리 위에 쓰려고 **시도**하게 된다. 전수 적용이 아니라 질문별 적용이다.
그 경계를 테스트로 고정했다.

**그리고 이 규칙도 이미 저장소 안에 있었다.** `agent/delivery.py::_problem()`의
첫 줄이 `if not destination.is_file(): return NOT_A_FILE`이다 — 배달 검증은
"거기 뭔가 있는가"(`exists()`)와 "그것이 이 Event인가"(`is_file()` + 내용 확인)를
이미 정확히 나눠 놓았다. §14의 `_runner_lock_path()`와 같은 모양이다: 규칙은
있었고, 여섯 곳이 따르지 않고 있었다.

**전수 확인 — state 로더 8종은 이미 안전하다.** `state.json` 자리에 디렉터리를
두고 전부 호출했다. 여덟 개 모두 **자기가 선언한 오류 타입**으로 떨어진다
(`SchedulerStateError`·`MonthlyStateError`·`BackupStateError`·`AgentStateError`·
`CollectorStateError`·`RetryQueueError`·`DashboardPendingError`·`RunSummaryError`).
미선언 예외가 새어나가 뷰를 죽이는 경우는 없다 — 그쪽은 `exists()` 뒤에서
`read_text()`가 실패하고 그 실패가 이미 보고 대상이므로 손댈 것이 없다.

**메시지도 함께 고쳤다.** 고치기 전에는 실패해도 엉뚱한 것을 가리켰다 —
Late Update는 `[Errno 13] Permission denied`로 **임시 파일 경로**를, Monthly는
`[WinError 5] … '.tmp-xxxx.md'`로 곧 지울 파일을 지목했다. 이제 둘 다
`a non-file is in the way of daily/monthly history: <경로>`라고 말한다.
`generate_daily_history()`에서는 이 분기를 **먼저** 검사한다 — 나중에 두면
일반 메시지("이미 존재한다")가 이겨서 디렉터리를 두고 "그 날은 이미 쓰였다"고
답한다.

수정 후:

    scheduler   FAILED, failed_date=2026-08-12, generated=()
                error: a non-file is in the way of daily history: …
    monthly     MONTHLY_FAILED, 같은 형태의 메시지, staging 잔여물 0건
    consistency STATE_INCONSISTENCY

**건강한 경우는 한 글자도 바뀌지 않는다**(실제 파일은 `is_file()`도 참).

테스트 10건(`test_state_consistency.py::NonFileInTheWayTests`). 그중 하나는
포인터가 전진하지 **않는지**를(영구화의 절반이 그것이었다), 하나는 "이름이
쓰였는가" 쪽 세 곳이 여전히 `exists()`인지를 고정한다.

**부수 발견 — 이 수정이 substring 테스트 하나를 깨뜨렸다.**
`test_consistency_module_does_not_import_scheduler_run_once`가 원문에 대해
`assertNotIn("run_once", source)`를 단언하는데, 내가 **주석에** `scheduler.run_once()`라고
쓰자 실패했다. 모듈은 여전히 아무것도 import·호출하지 않는다. 이 저장소가 계속
찾아온 결함(C30 §5)이 production이 아니라 **테스트 안에** 있던 경우다 — 주석이
깨뜨릴 수 있는 테스트는 사람에게 주석을 쓰지 말라고 가르치고, 반대로
`getattr(scheduler, "run_" + "once")`는 그냥 통과시킨다. C29 §3의 선례대로
**AST로 바꿨다**(import 집합과 호출 이름 집합을 각각 확인). 진짜 import·호출을
여전히 잡는지 확인했다.

### 16. Monthly parser 끝까지 — `project_id`는 아무도 이름 붙인 적 없는 네 번째 주입 경로 (신규, **P0**)

§1을 고친 뒤 "이 seam에 다른 유실이 더 있는가"를 끝까지 물었다.

**먼저 왕복부터 고정했다.** 렌더러가 쓸 수 있는 최대 문서 — 네 Category 전부,
project_id 대소문자 혼합, Decision Context 4필드 전부, evidence, 그리고 Late
항목 하나 — 를 렌더링해서 파서에 넣고 **event_id·category·project·owner·summary가
전부 돌아오는지** 단언한다. 개별 drop 사유마다 테스트를 쓰는 것은 누군가 생각해 낸
사유만 덮지만, 이것은 seam 자체를 덮는다. 5/5 왕복 확인.

**그 다음 적대적 입력으로 갔고, 거기서 나왔다.**

BUG-11/27은 escape 없이 렌더링되는 필드로 `summary`와 `evidence`를 지목한다.
C30 §4가 `event_id`를 추가했다. **`project_id`는 한 번도 이름이 붙은 적이 없고,
넷 중 가장 나쁘다 — 나머지 셋은 자기 항목을 망치지만 이것은 다른 Event를 지운다.**

`_render_item_block()`이 `_display_project_name(project_id)`를 `### ` 제목으로 쓰고,
`validate_event()`는 `project_id`를 "present and non-null"로만 제약한다(실측: 개행이
든 값이 **ACCEPTED**). 즉 다른 Desktop에서 transport를 건너온다.

**Blast radius 실측** — 평범한 Event 3건, 첫 번째만 조작된 `project_id`:

    ordinary                    3/3 생존
    newline only                3/3 생존
    `\n\n- INJECTED`            3/3 생존, EVT-1 summary 탈취
    `\n\n### Second Block`      3/3 생존, EVT-1 summary 탈취
    `\n\n## Metadata`           **0/3 생존**

마지막 줄이 결함이다. `## ` 제목이 Category Section을 닫아 버려서, 그 뒤의 모든
item block — **무고한 Event 2건 포함** — 이 소비 가능한 Section 밖으로 밀려나
Monthly에서 사라진다. `consolidate_month()`는 **MONTHLY_GENERATED**를 반환한다.

**유령 항목은 못 만든다 — 그리고 그건 방어가 아니라 우연이다.**
`_display_project_name()`의 `.title()`이 `Event ID:`를 `Event Id:`로 낮춰서 파서의
라벨 정규식에 걸리지 않는다. 방어로 오해하지 않도록 테스트로 적어 뒀다.

**여전히 SKIP:** 렌더러 escape는 docs/06 계약(BUG-11/27), `project_id` 제약은
docs/02 계약(A-15). 둘 다 결정이다.

**결정이 필요 없었던 것: 유실을 세는 것.** `DailyDocument.unconsolidated` —
문서가 가진 `- Event ID:` 줄 수에서 실제로 항목이 된 수를 뺀다. 렌더러가 항목당
정확히 한 줄을 쓰므로 이해 가능한 문서에서는 0이고, 그 이상은 **Daily에는 있는데
Monthly에는 없을 Company History**다.

    healthy          Event ID 줄 3   items 3   unconsolidated 0
    section closed   Event ID 줄 3   items 0   unconsolidated 3

**비용 0 — 추측이 아니라 실측이다.** `read_daily_document()`가 어차피 그 달의
모든 Daily를 파싱하고, 줄은 이미 split돼 있다. 파일을 한 번도 더 읽지 않는다 —
§9에서 540 ms 때문에 넣지 않기로 했던 검사를, 파싱이 이미 일어나는 자리로 옮겨서
공짜로 얻었다.

    parse x2000 (문서당 30항목)   383.6 ms   그중 새 카운트 10.9 ms (2.8%)
    consolidate_month  5항목/일 x 31일   30.3 ms
    consolidate_month 30항목/일 x 31일   35.4 ms

추가 파일 읽기 0회, 한 달 통합 전체가 여전히 35 ms 미만이다.

**전체 Section 밖으로 밀려난 블록까지 세려면 문서 전체를 봐야 한다** — Section
walk 안에서 세면 잘려 나간 블록은 애초에 보이지 않으므로 0을 보고한다. 그래서
비교 대상은 walk가 아니라 문서 전체다.

**sink까지 연결했다.** `MonthlyResult.unconsolidated_days` →
`app/runner.py`가 `MONTHLY_UNCONSOLIDATED <달> <날짜: N>` 한 줄을
`daily_late_update.log`에 쓴다. 새 artifact도 새 형식도 아니다 — Monthly 실패가
이미 가는 곳이고, AGENT.md §6a가 *"돌긴 돌았는데 뭔가 안 됐다"* 일 때 운영자를
보내는 파일이 바로 거기다.

**그 과정에서 BUG-39 sweep의 구멍도 찾았다.** `test_architecture_invariants.py`는
`RuntimeSummary`·`BackupLogEntry`·`SchedulerRunResult`에 대해 "계산되고 버려지는
필드"를 훑는데 **`MonthlyResult`는 그 sweep에 없었다.** 그래서 이번 결함이 정확히
BUG-39의 모양으로 거기 앉아 있을 수 있었다. 같은 invariant를 Monthly에도 붙였고,
읽히지 않는 5개(`year`·`month`·`coverage`·`source_dates`·`path`)가 왜 그래도 되는지
`BackupLogEntry`와 같은 논거로 고정했다.

테스트 17건 — 왕복 3, 주입 6, sink 4, BUG-39 invariant 1, 그리고 §1의 기존 것들.
탐지기 테스트는 탐지기 없는 코드에 대해 실패함을 확인했다.

**인접 경계를 물었더니 §11이 반만 고쳐져 있었다 (자기 blind spot).**
`project_id`가 어디로 더 흘러가는지 훑다가 `run_company_ops.py`의 같은 줄로
돌아왔다:

    print(f"  - {r.event_id} ({r.project_id}): {r.status.value}{suffix}")

§11에서 `suffix`(= `r.error`)에만 `redact(one_line(...))`를 걸었다. **같은 줄에
있고 같은 transport를 건너오는 나머지 두 필드는 그대로였다.** 실측 —
`event_id = "EVT-1
  - EVT-GHOST (PRJ): SYNCED"`:

    `  - `로 시작하는 출력 줄   2개
    두 번째                     전부 공격자가 쓴 것

한 줄의 눈에 띄는 절반만 막는 것은 절반짜리 수정이다. 두 필드 모두 `one_line`을
걸었고(`redact`는 걸지 않았다 — 이 둘은 운영자가 Event를 찾는 데 필요한 식별자이고
`r.error`와 달리 원격 응답 본문을 나르지 않는다), 두 모양 다 테스트로 고정했다.

### 17. Dead Capability 전수 — 구현·export까지 됐으나 아무도 부르지 않는 것 (완결)

§16에서 `build_role_summary`를 찾고 나서 "이 부류가 몇 개인가"를 전수로 물었다.
A-16(`build_ops_backup_properties`)과 E-20이 각각 하나씩 기록해 두었을 뿐,
목록을 만든 적은 없었다.

**결과 — production 호출자가 0인 public 함수/메서드는 정확히 열둘이다**
(AST Call 노드 + Attribute 읽기, **import alias 해석**, property 제외):

| 함수 | 상태 |
|---|---|
| `notion/dashboard.bootstrap_dashboard_databases` | **C31 §18 신규** — OPS_* DB를 만드는 유일한 함수인데 아무도 부르지 않는다 |
| `notion/dashboard.build_ops_backup_properties` | **A-16 기록됨** — OPS_BACKUP에 아무것도 안 쓴다 |
| `notion/dashboard_pending.remove_pending` | **B-7 기록됨** — 삭제 여부가 열린 결정 |
| `daily/role_summary.build_role_summary` | **C31 §16 신규** — A-3의 "제공한다"가 시스템에 대해서는 틀렸다 |
| `daily/role_summary.for_role` · `of_category` | 같은 모듈, 같은 이유 |
| `reporter.Reporter.report_and_write` · `report_and_send` | 대체됨 — 아래 |
| `notion/retry_queue.enqueue` · `dequeue` | 대체됨 — Runner는 batch API(B안)를 쓴다. 모듈 docstring이 이미 그렇게 적어 두었다 |
| ~~`runsummary.RunSummary.component`~~ | **C46에서 목록을 떠났다** — `ops_status._same_instant_skips_from_the_last_run()`이 manifest에서 이름 붙은 컴포넌트 하나의 metric을 꺼내며, 그것이 이 함수가 쓰인 목적이다 |
| `reporter/local_output.read_event_json` | 읽기 seam |

**Reporter 편의 래퍼 둘은 "빠진 것"이 아니라 "대체된 것"이다.** 핵심
`Reporter.report()`는 Agent가 쓴다. 두 래퍼는 report + 즉시 write/send를 묶은
것인데, Agent는 대신 `report()` → `outbox.stage()` → `outbox.drain()`을 쓴다 —
outbox가 내구성(*"an Event that was created is never lost"*)을 주기 때문이다.
즉 이 둘을 부르는 것은 그 보장을 우회하는 일이므로 **부르지 않는 것이 옳다.**
`report_and_write`의 기본 디렉터리가 `incoming/`이라는 사실은 C27 §8의 경계
논의에 이미 나와 있다. 기술 부채로 기록하되 결함은 아니다.

**방법론 경고 — 자동 분석 두 개를 시도했고 둘 다 못 쓴다.** 다음 사람이 같은
길로 가지 않도록 적어 둔다.

* **"패키지 밖에서 안 쓰이는 export"** → 180개 중 114개가 걸린다. 같은 패키지 안의
  정상적인 사용(`monthly/generator`가 `monthly/parser`를 부르는 것)을 전부 죽은
  것으로 센다.
* **entrypoint에서의 call-graph 도달성** → 231개 중 64개가 "도달 불가"로 나오는데,
  손으로 확인하니 `run_intake`(15곳) · `record_run`(12곳)처럼 **명백히 호출되는
  것들**이 대거 섞여 있다. 이름 기반 그래프는 동명 함수(`run_once`가 여섯 모듈에
  있다)와 간접 호출에서 무너진다.
* **grep** → `bootstrap_dashboard_databases(` 이 2곳이라고 답했다. 둘 다 **주석과
  docstring**이었다. 실제 호출은 0이다.
* **alias를 풀지 않은 AST** → 15개를 죽었다고 했는데 그중 `build_index`는
  `app/runner.py`가 `build_index as build_retry_queue_index`로 import해서 **매 실행
  호출한다**. 11개가 오탐이었다.

**믿을 수 있는 것은 import alias를 푼 AST Call/Attribute 세기뿐이고**, 위 표는
그것으로 얻은 결과다. 열둘 전부 손으로 재확인했다.

**그리고 이 목록 자체를 테스트로 고정했다**
(`test_repository_hygiene.py::DeadCapabilityInventoryTests`) — 항목마다 왜 거기
있는지를 코드에 적어 두었고, 이유 없이 하나가 늘면 실패한다. 그 테스트도 처음에는
alias를 풀지 않아 **스스로 틀렸고**, 전체 Regression에서 실패해서 잡혔다. 네 번째
같은 실수였다.

### 18. 진단이 "할 일 없음"이라고 말하는데, 아무도 하지 않는 단계가 남아 있다 (신규, **운영자 오도**)

§17의 목록에서 `bootstrap_dashboard_databases`를 손으로 확인하다 나왔다.

`init_notion.py`는 Notion 셋업이 유일한 일인 명령이다. 그것이 하는 일은
`bootstrap_database()`(PROJECTS)와 `diagnose_dashboard_bootstrap()`(읽기 전용
진단)뿐이고, **OPS_* Database를 실제로 만드는 `bootstrap_dashboard_databases()`는
부르지 않는다.** AST로 확인했다(import alias까지 풀어서): `src/**`와 루트 스크립트
전체에서 호출 지점 **0**. 어떤 문서에도 이 함수를 실행하라는 안내가 없다.

**그런데 진단의 성공 메시지가 이랬다:**

    다음 할 일 : None — the reference database already lives in a Page;
                bootstrap_dashboard_databases(client) will use it.

두 절 다 사람을 오도한다. `None`은 할 일이 없다는 뜻으로 읽히고, 뒤 절이 가리키는
함수는 **아무 명령도 실행하지 않는다.** 행복 경로의 운영자는 이 줄을 읽고 Dashboard
설정이 끝났다고 결론 내리는데, 실제로는 `NOTION_OPS_RUNS_DATABASE_ID`가 비어 있고
`.env.example`이 명시하듯 그 상태에서는 **`record_run()`이 매 실행 건너뛰어진다.**

이 저장소가 반복해서 없애 온 "지워지지 않는 경보"의 **정반대**이고 더 나쁘다 —
하지 않은 일에 대한 *정상* 신호이며, 아무것도 이것을 반박하지 않는다.

**왜 자동화는 SKIP인가:** `bootstrap_dashboard_databases()`를 `init_notion.py`에서
부르면 **실제 Notion Workspace에 Database 5개를 만든다.** 명시적 SKIP 항목이다.

**승인 없이 한 것: 메시지를 사실로 만들었다.** 세 갈래 모두 (a) 이 저장소의 어떤
명령도 생성 단계를 수행하지 않는다는 것과 (b) 실제로 켜는 스위치가
`NOTION_OPS_RUNS_DATABASE_ID`라는 것을 말한다. BUG-55를 *"뭔가 잘못됐다"*에서
*"이 디렉터리 이름을 바꿔라"*로 만든 것과 같은 작업이다.

**A-16과의 관계:** A-16은 *"Dashboard Database 4종이 영구히 비어 있다"*(5종 중
`OPS_RUNS`에만 쓴다)를 기록한다. 이것은 그 아래층이다 — **5종 전부 만들어지지
않는다**, 만드는 함수를 아무도 부르지 않으므로. 두 항목은 같은 결정(A-8: 실제
Workspace 연결)을 기다린다.

테스트 3건(`test_notion_dashboard.py`) — READY 메시지가 더 이상 "None"이 아님,
세 갈래 전부가 환경변수 이름을 담고 있음, 그리고 **production에 호출 지점이 0**이라는
사실 자체(alias 해석 포함). 마지막 것 때문에, 승인이 나서 배선하는 변경은 같은
커밋에서 이 메시지들을 다시 쓰도록 강제된다.

### 19. DEGRADED 단계가 CRITICAL 단계를 중단시킨다 — 두 번째 자리 (신규, **P0**)

Failure Isolation 감사를 배치 루프 전체에 돌리다 나왔다. 대부분은 per-item
try/except가 있거나(Collector·outbox·reconciliation) 중단이 **의도된**
곳이다(Scheduler §30, History Filter의 BUG-20 특성화). 하나가 아니었다.

Notion Sync 4b단계는 수집된 Event를 `processed/`에서 다시 읽는다:

    event = Event.from_json(processed_file.destination_path.read_text(...))

`_sync_and_record()`의 try는 `notion_sync.sync()`만 감싼다. **이 읽기에는 핸들러가
하나도 없었다.** AST로 확인: 이 줄을 감싸는 `try`는 둘뿐이고 **둘 다
`try/finally`(except 없음)** — 예외는 `run_once()` 밖으로 그대로 나간다.

**실측** — 읽기 실패 1건 주입:

    수정 전   run ABORTED: ValueError
              Daily files : NONE
              backup state: MISSING

    수정 후   run 계속       Daily files : 13개 기록됨
              backup state: written
              notion_sync : FAILED, metrics{processed:1, queued:0, unreadable:1},
                            retryability UNKNOWN
              notion_sync.log: NOTION_UNREADABLE <파일명>

Notion Sync는 `Severity.DEGRADED`이고 History·Backup은 CRITICAL이다. 이것은
`daily/generator.update_daily_history`가 이미 이름 붙인 그 역전이다 —
*"A component docs/14 §5 classifies as DEGRADED was aborting one it classifies
as CRITICAL, which inverts the entire point of having the two severities."*
같은 모양, 다른 단계. 그리고 **그 단계 자신의 주석이 세 줄 위에서** "Notion 실패가
Runtime을 막지 않는다"고 말하고 있었다.

**도달 경로.** Collector가 몇 분 전에 같은 파일을 읽었으므로 아무도 실패를 예상하지
않았다. 그러나 이 배포에서 `runtime/`은 OneDrive 아래에 있고(docs/11), sync
클라이언트나 스캐너가 핸들을 잡고 있으면 Windows에서 정확히 이 예외가 난다.

**수정.** 그 읽기를 `except (OSError, ValueError, EventValidationError)`로 감싸
파일명을 세고 로그에 남기고 **다음 Event로 넘어간다.** 읽지 못한 파일은 Notion에
도달하지 못한 Event이므로 기존 분류 `NOTION_SYNC_INCOMPLETE`로 컴포넌트를
FAILED로 만든다(retryability는 UNKNOWN — 다음 실행이 읽을 수 있을지 이 단계는
모르고, BUG-13이 바로 그것을 꾸며내지 말라는 항목이다).

**SyncResult를 지어내지 않는다.** 읽지 못한 것이 바로 `event_id`인데 가짜 id를
만들면 로그와 Manifest에 없는 Event가 들어간다. 개수와 파일명만 기록한다.

**History Filter 쪽(626행)은 일부러 그대로 두었다.** 그쪽은 CRITICAL 단계이고
중단이 방어 가능한 선택이며, BUG-20이 "이 단계에는 per-event 오류 처리가 없다"를
특성화로 고정해 두었다. 고친 것은 등급이 뒤집힌 쪽 하나뿐이다.

테스트 6건(`test_runner_failure_paths.py::DegradedStepMustNotAbortCriticalStepsTests`) —
Daily 도달, Backup 도달, 컴포넌트 metrics, 로그, 깨끗한 실행의 오탐 없음, 그리고
읽기가 실제로 `except` 안에 있는지를 **AST로** 확인하는 것 하나.

**그리고 네 DEGRADED 단계 전부에 같은 질문을 던졌다 — 둘이 더 걸렸다.**
정적 분석은 또 틀렸다("단계의 줄 범위가 try 안에 있는가"로 물으니 넷 다 노출로
나왔는데, 가드가 범위 *안에* 있기 때문이다). 그래서 **주입으로** 답했다:

| 단계 | 등급 | 예외를 주입하면 |
|---|---|---|
| `notion_sync` | DEGRADED | **중단됨 → 수정** (위) |
| `late_update` | DEGRADED | **중단됨, backup_state MISSING → 수정** |
| `monthly` | DEGRADED | 계속됨 ✓ (이미 가드 있음) |
| `dashboard` | DEGRADED | 계속됨 ✓ (이미 가드 있음) |

`late_update`는 두 겹으로 새고 있었다. **(1)** Runner의 루프가
`update_daily_history()`의 *"Never raises for an I/O or rendering failure"*
docstring을 믿고 자기 가드를 두지 않았다. **(2)** 그 약속 자체가 사실이 아니었다 —
`select_late_candidates()` 호출이 **두 가드 사이의 틈**에 앉아 있었고, 그것은
사람이 편집할 수 있는 문서(docs/06 §57)를 파싱한다. 둘 다 닫았다.

수정 후 실측:

    aborted      : None
    backup_state : written
    late_update  : FAILED  LATE_EVENT_MERGE_FAILED  {updated:0, failed:1}
    overall      : DEGRADED  exit 3

DEGRADED 하나가 실패하고 CRITICAL이 전부 성공했을 때 나와야 하는 값 그대로다.
Monthly의 처우(*"삼키되, 흔적 없이 삼키지는 않는다"*)를 그대로 적용했다.

테스트 5건 추가(`EveryDegradedStepIsContainedTests`) — 네 단계를 하나의 규칙으로
묶어 주입으로 검사하고, `update_daily_history()`의 약속이 이제 실제로 참인지도
따로 확인한다.

**그리고 4b를 고치자 기존 테스트 3건이 깨졌고, 그것이 세 번째 구멍을 찾아 줬다.**
`RetryQueueBatchSaveDurabilityTests`(BUG-24)는 *"Notion 단계 안에서 예외가 나면
큐 델타는 그래도 저장돼야 한다"*를 지키는데, **자극(stimulus)으로**
`Event.from_json`이 raise하게 만들고 있었다. 내가 그 경로를 잡아 버리자 자극이
사라진 것이다 — 지키려던 성질(그 `finally`)은 그대로다.

그래서 "그럼 이 단계에서 아직 무엇이 새어나갈 수 있나"를 물었고,
**4a단계가 같은 구멍을 갖고 있었다**: `queued_entry.to_event()`는
`Event.from_json(self.event_data)`인데, `load_queue()`는 큐 파일의 **모양**만
검사하고 `event_data`가 Event인지는 다시 검증하지 않는다. 손편집·잘린
`notion_retry_queue.json` 하나가 같은 방식으로 run을 중단시킨다. 4b와 같은 처우로
닫았다 — 세고, `notion_sync.log`에 이름을 남기고, 건너뛴다. **읽지 못한 항목을
큐에서 지우지는 않는다**(손상 입력에 대해 이 저장소가 일관되게 지키는 자제).

BUG-24 테스트의 자극은 `retry_queue_upsert`로 옮겼다 — 같은 `try` 안에 있고,
"이 단계에서 뭔가 터졌다"를 대신하기에 정확히 맞는 지점이며, 아무도 그것이
raise하지 않는다고 주장한 적이 없다. 테스트가 지키는 성질은 그대로다.

테스트 3건 추가(`CorruptRetryQueueEntryTests`).

### 20. Test Coverage 감사 — 아무도 실행하지 않는 분기 (신규 2건, 둘 다 특성화)

`coverage`는 설치돼 있지 않고 이번 세션에서 환경을 바꾸지 않는다. **표준 라이브러리
`trace`**로 `daily/`·`monthly/` 모듈을 그 테스트들과 함께 돌려 실행되지 않은
statement를 뽑았다. 둘이 나왔다.

**(a) `_first_bullet()`의 `return None`과 그것을 받는 `if summary is None: continue`.**
§1이 이 분기가 *왜* 발화하는지를 좁혔지만(라벨 모양 요약은 더 이상 걸리지 않는다),
분기 자체는 남아 있고 **어떤 테스트도 도달하지 않았다.** 그리고 그것은 조용한
유실이다. 손편집으로 도달한다(docs/06 §57) — 산문 bullet만 지우고
`- Owner:` · `- Event ID:`는 남긴 item block.

    label bullet만 있는 블록 -> items: []   unconsolidated: 1

두 가지를 함께 단언한다: 항목이 실제로 버려진다는 것(오늘의 동작, 무변경)과 그
유실이 이제 **세어진다**는 것(§16의 카운터가 이 경로도 덮는다). 언젠가 이 drop을
고치면 첫 단언이 뒤집혀 기록이 강제로 갱신된다.

**(b) `_metadata_bounds()`의 "다음 `## ` 제목에서 멈춘다" 루프.**
그 docstring은 *"a file whose Metadata is not last is still handled correctly"*
라고 단언하는데, 그 문장을 구현하는 루프 본문에 **도달한 테스트가 없었다.** 즉
검증되지 않은 코드에 대한 주장이었다 — C30이 사냥한 바로 그 모양.

배치가 이상한 것도 아니다. docs/06 §57 · docs/11 §71이 COO의 손편집을 허용하고,
Metadata 뒤에 메모 절을 붙이는 것이 가장 자연스러운 편집이다. **동작은 옳다 —
추측이 아니라 실측했다**: bounds가 `## COO Note`에서 정확히 멈추고, Late 절이
Metadata **앞에** 삽입되며, 손으로 쓴 뒤쪽 절이 **그대로 살아남고**, Metadata
필드 갱신이 그 절을 넘어가지 않는다. 틀렸다면 사람이 쓴 내용을 잘라먹었을 것이다.

테스트 5건(`test_monthly_history.py` 1 + `test_daily_late_events.py::MetadataNotLastTests` 4).

**(c) 원자적 쓰기의 정리 코드가 14개 모듈 전부에서 한 번도 실행되지 않았다.**
같은 네 줄이 열네 모듈에 있다:

    except BaseException:
        try: os.remove(tmp_path)
        except OSError: pass
        raise

이것이 **C27의 발견 전체를 막는 가드**다 — 중단된 실행이 남긴 `.tmp-*` 파일을
소비자 6곳이 산출물로 읽었고, Event로 승격됐고, 잘린 Company History가 원격으로
push됐다. C27이 고친 것은 *읽는 쪽*(`.tmp-` 건너뛰기)이고, **쓰는 쪽의 절반은
아무도 검사하지 않고 있었다.**

`os.replace`(모든 writer의 마지막 단계이자 temp 파일이 존재한 뒤 실패할 수 있는
유일한 지점)를 실패시켜 writer 9종을 돌렸다. 전부 정리하고 전부 전파한다 — 예외가
하나 있고 그것도 옳다: `write_summary()`는 **일부러 삼킨다**(Manifest는 이미 끝난
run에 대한 보고이고, 보고를 못 했다고 run을 실패시키는 것은 README RULE 9의 역전).
그 하나도 정리는 한다.

테스트가 실제로 판별하는지도 확인했다 — `os.remove`를 무력화하니
`.tmp-pckehc5r.json`이 남았다.

구조 쪽도 함께 고정했다: `mkstemp`를 쓰면서 `except BaseException` 정리가 없는
함수를 AST로 금지한다. 새 writer가 정리 없이 생기면 목록에 없다는 이유로 위
테스트를 통과해 버리기 때문이다.

테스트 2건(`test_repository_hygiene.py::AtomicWriteLeavesNoResidueTests`, subtests 9).

### 21. Monthly parser를 fuzz로 닫았다 — 열거된 케이스가 놓친 것 둘 (신규, **데이터 유실 / 무결성**)

§16에서 "왕복 + 적대적 입력"까지 갔지만 그것도 **열거한 모양들**이다. 마지막으로
**seed 고정 fuzz**를 돌렸다 — 두 모집단, 서로 다른 약속:

    benign       렌더러가 평범한 Event에서 만드는 것 -> **정확히** 왕복해야 한다
    adversarial  개행·제목이 든 summary/project_id(BUG-11/27, §16) -> 유실은
                 예상되지만 **세어지지 않는 것**은 안 된다

**benign 4,000건 중 641건이 항목을 잃고 있었다.** 열거 테스트는 전부 통과하는데.
(silent는 0이었다 — §16의 카운터가 전부 잡았다. 그래서 조용하지는 않았지만
**잃고 있었다.**)

**원인 1 — 요약이 렌더러의 라벨 이름으로 시작할 수 있다.**
§1이 shape 검사(`^[A-Z][A-Za-z ]+:`)를 정확한 라벨 집합으로 좁혀 `Fixed: `를
고쳤는데, **진짜 라벨 이름 일곱 개는 전부 여전히 항목을 잃고 있었다.** 실측:

    Owner: …  Event ID: …  Category: …  Decision Context: …
    Expected Outcome: …  Actual Outcome: …  Lessons Learned: …   전부 LOST

넷은 이 도메인에서 자연스러운 문장 시작이다 — `Lessons Learned: …`는 LEARNING
항목의 요약이 실제로 읽히는 방식이고 `Decision Context: …`는 DECISION이 그렇다.
docs/05가 그 카테고리들에 준 어휘 자체다.

**추측하지 않고 순서로 판정한다.** 렌더러는 라벨을 **한 번씩, 정해진 순서로** 쓴다
(`_ITEM_LABELS`). 따라서 첫 bullet의 라벨이 그 아래 라벨보다 **순서상 뒤**이거나,
아래에 **같은 라벨이 또** 있으면, 그것은 렌더러가 쓸 수 없는 배치다 — 남는 설명은
산문뿐이다. 세 경우 전부 실측:

    - Lessons Learned: …   - Owner: …        6 앞에 0  -> 산문
    - Owner: …             - real summary    손편집     -> 건너뛰고 산문
    - Owner: …             - Event ID: …     0 앞에 1  -> 요약 없음(§20a 유지)

`Owner:`는 순서 0이라 앞설 것이 없다 — **중복** 규칙이 그것을 살린다(블록에
`Owner:` bullet이 둘이면 첫째가 산문이다).

**원인 2 — 그 요약이 자기 항목의 `event_id`를 가로챈다.** `Event ID: measured it.`이
요약이면 그것이 블록의 **첫 `- Event ID:` 줄**이므로, 항목이
`event_id="measured it."`로 통합된다 — 그리고 docs/09 §59는 그 값으로 중복 제거를
한다. 유실이 아니라 **잘못된 신원**이라 더 나쁘다. 라벨 스캔이 `_first_bullet()`이
산문으로 판정한 **그 한 줄만** 건너뛰도록 고쳤다.

**결과:**

    benign       4,000건  유실 641 -> **0**
    adversarial  4,000건  유실 2,235  **silent 0** (BUG-11/27의 열린 결정)

개행이 든 요약이 만드는 **별도 줄**은 여전히 BUG-11/27이다 — 그것은 파서가
구분할 수 없고, 세어질 뿐이다.

테스트 9건(`LabelNamedSummaryTests` 6 + `RendererParserFuzzTests` 3). fuzz는 seed
고정이라 어느 머신 어느 날에 돌려도 같은 문서 8,000개다 — corpus가 움직이는 fuzz는
이번 Sprint가 §5(a)에서 제거한 시한폭탄이다. 그리고 adversarial 모집단이 **실제로
유실을 만드는지**도 단언한다(아니면 위 테스트가 아무것도 검사하지 않고 통과한다).
셋 다 수정 전 파서에 대해 실패함을 확인했다.

### 22. 같은 뿌리가 인접 경로 넷에 더 있었다 (신규, **데이터 유실 / 무결성 / 거짓 경보**)

§21을 닫고 "수정 → 검증 → **인접 경로 재감사**"의 셋째를 했다. §21의 뿌리는
파서 버그가 아니라 **포맷**이다 — 렌더러가 요약을 `- {summary}`로 **그대로**
쓰기 때문에 `Event ID: X`라는 요약은 그 아래 라벨 줄과 **바이트 단위로 같다.**
그러면 이 파일을 줄 단위로 읽는 **모든** 코드가 같은 방식으로 속는다. 세 곳
전부 실측으로 재현됐다.

**(a) `daily/late_events.existing_event_ids()` — §38 중복 가드. Late Event 영구 유실.**
이건 카운터도 로그도 없다. 실측, 평범한 KEEP Candidate 하나:

    요약 "Event ID: EVT-999"
    existing_event_ids(day)              {'EVT-1', 'EVT-999'}
    select_late_candidates(day, EVT-999) ()      <- 추가 안 됨
    append_late_events(...)              파일 그대로
    `- Event Count: 3`                           <- 실제 Event는 둘

늦게 도착한 EVT-999가 그날 버려지고, **그 날짜를 다시 보는 모든 실행에서 또
버려진다.** §38은 자기가 제대로 일하고 있다고 믿는다. Event Count 쪽은 정확히
일치할 필요도 없다 — `Event ID: `로 시작하기만 하면 유령 id가 하나 늘어난다.
보안 쪽에서 보면 **Event ID spoofing**이다: 한 Event가 요약 한 줄로 나중에 올
Event를 지목해 막을 수 있다.

seed 고정 fuzz 1,000건(수정 전): **억제된 Late Event 98건, 틀린 Event Count 429건.**

**(b) `monthly/generator._existing_generated_at()` — §58의 `Generated At` 영구 위조.**
Monthly 항목 요약도 raw로 렌더되고, 항목 섹션은 `## Metadata` **위**에 있다.

    - Generated At: 1999-01-01T00:00:00+09:00   <- 항목의 요약
    - Generated At: 2026-09-01T02:00:00+09:00   <- 진짜 필드
    _existing_generated_at() -> '1999-01-01T00:00:00+09:00'

다음 dirty rebuild(§58의 **평범한** 동작)가 그 값을 그 달의 진짜 `Generated At`로
써 넣고, **되돌아오지 않는다** — 그때부터는 Metadata 블록 자체가 위조본을 들고
있어서 문제의 Event를 지워도 원래 값은 없다. `Generated At`이 "이 달이 처음
닫힌 시각"이라는 §58의 의미가 사라진다. **마지막** Metadata 블록을 읽도록 함께
고쳤다 — 렌더러가 두 경로 모두에서 Metadata를 **마지막에** append하므로 손편집된
Monthly에 위조 블록이 있어도 진짜가 이긴다. escaping 결정(BUG-11/27)은 건드리지
않는다.

**(c) `ops_status._monthly_counts_more_than_it_shows()` — 자기 docstring을 어겼다.**
그 함수는 "**silence는 될 수 있어도 거짓 경보는 못 한다**"고 적어두고 있었는데,
절반이 사실이 아니었다. 실측, 아무것도 잃지 않은 달 하나:

    요약 `Consolidated Items: 999`  ->  ('2026-08', 999, 1)
    요약 `Event ID: EXTRA`          ->  ()   (진짜 shortfall을 가림)

첫째는 멀쩡한 달에 대고 "998건을 적게 기록했다"를 운영자 앞에 세운다. 상시
거짓 ATTENTION 한 줄은 운영자가 그 섹션을 **안 읽게** 만드는 방식이고, 그 손해가
이 검사의 값어치보다 크다. docstring이 근거로 든 newline 경로도 다시 재봤다 —
`monthly/parser.py`가 줄 단위라 **파이프라인으로는 도달할 수 없고**(요약에 개행이
있으면 항목 자체가 통째로 떨어지고 `unconsolidated`로 세어진다) 손편집된 Monthly가
필요하다. 그 문장도 같이 고쳤다.

**(d) `ops_status._kept_but_not_rendered()` — 유실 탐지기 자체가 눈이 멀었다.**
E-17의 모양(KEEP Candidate가 저장돼 있는데 Daily 파일에 없다)을 잡는 **유일한**
검사다. 실측 — EVT-A가 그 요약으로 렌더되고 EVT-B는 정말로 파일에 없을 때:

    요약 `Event ID: EVT-B`   ->  ()
    요약 `Shipped it.`       ->  ('EVT-B (2026-08-05)',)

평범한 요약 한 줄이 자기가 지목한 Candidate에 대해 유실 탐지기를 꺼버렸다.
방향은 silence뿐이지만(요약은 줄을 더할 뿐 지우지 못한다), **부재를 알아채는 것이
일 전부인 검사에서 silence가 곧 피해다.**

**규칙을 한 곳에 뒀다 — 다만 두 벌이다.** `daily/markdown.summary_line_indices()`가
"어느 bullet이 요약인가"를 렌더러 옆에서 답한다(모호함을 만드는 쪽이 규칙을
가진다). `monthly/parser.py`는 **import하지 않는다** — 그 패키지는
`ALLOWED["monthly"] == set()`인 선언된 leaf이고, 그건 Monthly 통합이 Daily
*텍스트* 너머 `history`/`events`로 손을 뻗지 못하게 하는 docs/09 §12-13의 장치다
(§8의 계약 변경은 승인 대상이라 건드리지 않는다). 그래서 한 규칙 두 구현이고,
**행동 동등성 테스트**가 둘을 묶는다 — 라벨 튜플 비교는 이름 변경을 잡고,
블록 배열 8종에 대한 `_first_bullet()` vs `summary_line_indices()` 비교는 규칙이
갈라지는 것을 잡는다. 중복 규칙을 뺀 구현으로 실제 실패함을 확인했다.

**성능.** 측정했다.

    existing_event_ids     12 Event(97줄)    0.016 -> 0.030 ms  (하루치 현실 규모)
                         1000 Event(7013줄)  1.105 -> 2.357 ms
    _kept_but_not_rendered 14일 x 5건         0.84  -> 0.99 ms
                          365일 x 10건       24.09 -> 30.05 ms
    summary_line_indices  24개월 x 30항목   스캔 0.24 ms + 1.14 ms
    (ops_status, CPU만)  120개월 x 60항목   스캔 2.28 ms + 11.38 ms

2년 규모에서 Monthly 검사 전체는 ~5.9 ms로 그대로다. 10년이면 ~17 ms 늘어난다.
2단 fast path(싼 스캔 먼저, 보고 직전 파일만 정밀 재계산)는 **버렸다** — 10년 뒤
규모의 절약을 위해 silence 방향을 다시 열어주는 거래다.

**이 머신의 실제 runtime에서 잰 전체 비용**(추정이 아니라 두 경로를 다 지나는
`_print_company` + `_print_history`, n=20):

    summary_line_indices 있음   min 44.05 ms   median 45.33 ms
    없음(수정 전)               min 43.97 ms   median 45.08 ms

**+0.08 ms — noise 안이다.** `ops_status.py` 전체 명령은 203 ms(n=7)이고 그
대부분은 인터프리터 기동이다.

테스트 24건. 넷 다 수정 전 구현에 대해 실패함을 확인했고(late 6/11, Monthly 3/6,
ops_status shortfall 2/4, stranded 1/4 — 나머지는 "여전히 동작한다" 가드와 범위를
정직하게 적은 것),
그 과정에서 **아무것도 검사하지 않고 통과하던 테스트 하나를 발견해 지웠다**
(shortfall이 없으면 `Event ID:` 위조가 바꾸는 게 없다 — 가리는 방향은 가릴 것이
있을 때만 드러난다).

### 23. 인용이 가리키는 곳이 실제로 있는지 아무도 확인한 적이 없다 (신규, **문서 무결성**)

이 저장소는 결정을 산문으로 정당화하지 않고 **인용**한다. `docs/NN §M` 807건,
`BUG-NN` 618건, `README RULE N` 39건. 인용을 따라간 독자는 근거에 도착해야 한다.
**아무것도 그것을 검사하지 않고 있었다.**

**(a) `docs/NN §M` 807건 중 하나가 존재하지 않는 절을 가리켰다.**
`scheduler.py`가 "수동 실행도 동일한 Lock과 State 규칙을 따른다"의 근거로
docs/07의 951번 절을 들었는데, **951은 그 문장의 줄 번호다.** 절은 §44(Manual
Run)다. 807분의 1은 좋은 비율이고, 동시에 **보는 사람이 없으면 쌓이기만 하는**
종류의 오류다. 테스트로 고정했다 — 절이 존재하는지만 보고, 인용한 주석이 주장하는
내용까지 맞는지는 보지 않는다. 후자는 테스트로 결정할 수 없고, 결정할 수 있는 척하는
검사가 바로 이 프로젝트가 없애려는 낡은 주장이다.

(그 테스트는 **자기 파일과 이 파일을 읽는다.** 처음 실행에서 제일 먼저 잡은 것이
잘못된 인용을 예시로 적어둔 자기 docstring이었고, 두 번째가 이 절이었다. 둘 다
예시가 인용으로 읽히지 않게 다시 썼다 — 검사가 자기 자신에게도 적용된다는
증거이기도 하다.)

**(b) `BUG-NN` 15개가 아무 데도 없다 — 65건이 그것을 가리킨다.**
초기 Audit이 번호를 매긴 발견들인데 BACKLOG에 옮겨진 적이 없다. 정보가 사라진
것은 아니다 — 인용하는 docstring 각각이 결함을 **전부** 적고 있다. 사라진 것은
**id로 찾아갈 방법**이고, 독자는 BUG-18이 열려 있는지 닫혀 있는지 알 수 없다.

65건을 고쳐 쓰지 않았다. id는 역사적 흔적이고, 지우면 그 흔적이 없어진다. 대신
**색인을 만들어 포인터가 다시 풀리게 했다.** 설명은 전부 인용처에서 가져온 것이고
새로 지어낸 것은 없다.

| id | 인용 | 전체 설명이 있는 곳 | 한 줄 |
|----|------|--------------------|-------|
| BUG-1 | 3 | `src/backup/runner.py:168` | push만 실패한 뒤 `git status`가 깨끗하면 NOT_REQUIRED로 끝나 §19의 "다음 Runner에서 재시도"가 영원히 오지 않는다 |
| BUG-2 | 3 | `src/history/file_repository.py:59` | `event_id`가 경로로 쓰여 `../../../PWNED`가 저장소 밖에 썼다 |
| BUG-5 | — | 같은 곳 | 위와 함께: Windows가 금지하는 문자가 든 id가 Runner 전체를 OSError로 중단시켰다 |
| BUG-7 | 3 | `tests/test_runner_failure_paths.py:3464` | Secret Scan은 파일명만 본다 — 현실적인 secret 12개 중 **3개**를 잡았다 |
| BUG-8 | 1 | `tests/test_e2e_disaster_scenarios.py:209` | 거절된 push에는 인증 표시가 없어 PENDING으로 분류되고, §62가 금지한 영구 재시도 루프가 된다 |
| BUG-9 | 11 | `src/collector/runtime.py:200` | 파일 이동이 성공하기 전에 `mark_seen()`이 저장돼, 재시도가 DUPLICATE만 만들고 Candidate가 영구 유실된다 |
| BUG-10 | 2 | `tests/test_runner_failure_paths.py:25` | 재저장 시 FileExistsError가 Runner를 중단시킨다 |
| BUG-12 | 4 | `tests/test_repository_hygiene.py:8` | 문서 공백 — README/docs는 명세라 그 Sprint가 고칠 수 없었다 |
| BUG-14 | 7 | `tests/test_e2e_operations_scenarios.py:19` | Notion이 날짜만 든 "Last Updated"를 주면 Late Event 가드가 깨진다 |
| BUG-15 | 3 | `src/transport/onedrive.py:43` | `event_id`가 경로에 그대로 들어가 `../target/X`가 OneDrive 감시 폴더 **밖**에 썼다 |
| BUG-16 | 2 | `tests/test_repository_hygiene.py:18` | README §12의 문서 목록과 실제 docs/ 불일치, 낡은 절대경로 헤더 |
| BUG-18 | 15 | `tests/test_architecture_invariants.py:591` | Runner Lock이 원자적이지 않아 상호배제가 보장되지 않았다 |
| BUG-19 | 8 | `tests/test_architecture_invariants.py:758` | 경합 중 `os.replace()`가 Windows에서 PermissionError를 던지는데 **어느 writer도 잡지 않는다** (무결성은 지켜지고 가용성이 깨진다) |
| BUG-23 | 1 | `tests/test_untrusted_event_input.py:409` | ~250자 `event_id`가 Windows가 거부하는 경로를 만들어 발신 시점에 조용히 유실된다 |
| BUG-34 | 2 | `tests/test_spec_conformance.py:892` | 렌더러가 모르는 category의 항목이 본문에서 빠지는데 Metadata에는 세어진다 |
| BUG-35 | 1 | `tests/test_architecture_invariants.py:1091` | `InMemoryNotionTransport`가 실제 API보다 관대해서 Notion 테스트가 증명할 수 있는 범위가 제한된다 |

이 표는 **닫혔다는 주장이 아니다.** BUG-19와 BUG-34는 이 Sprint에서 다시
확인했고 열려 있다(전자는 BACKLOG BUG-19가 없어서 아무도 몰랐다). 나머지는
인용처 docstring이 CHARACTERIZATION인지 수정인지 스스로 밝히고 있다.

**(c) `README RULE N` 39건은 전부 존재한다**(RULE 1-12). 문제 없음.

새 dangling id가 조용히 들어오지 못하도록 테스트로 고정했다. 인용 패턴이 매칭을
멈추면(예: 표기가 바뀌면) 그것도 실패한다 — 아무것도 안 세면서 통과하는 검사는
§21에서 이미 한 번 만들어 봤다.

### 24. `.tmp-` 잔여물을 세 디렉터리 중 둘만 제대로 부르고 있었다 (신규, **거짓 경보**)

C27이 세운 규칙은 "atomic write가 staging하는 디렉터리를 읽는 쪽은
`is_incomplete_write()` 이름을 건너뛴다"이다. 그 규칙이 **전수 적용됐는지** 아무도
본 적이 없어서, `glob`/`iterdir` 22곳을 전부 대조했다. (pathlib의 `*`는 dotfile을
매칭하므로 `.tmp-….json`은 `glob("*.json")`에 그대로 걸린다.)

대부분은 걸러지고 있었다. 진짜 구멍은 **`incoming/`** 하나다 — 그리고 하필
`write_event_json()`이 실제로 staging하는 디렉터리다. `.tmp-` 잔여물을 담을 수
있는 디렉터리는 셋인데 둘은 제 이름으로 부르고 있었다:

    transport/   incomplete                  (이미 있음)
    rejected/    rejected_incomplete_write   (C31 §17에서 이미 고침)
    incoming/    **"수집을 기다리는 Event"**   <- 여기

실측, 런타임 전체에 staging 파일 하나뿐일 때:

    awaiting_collection=1   is_clear=False
    -> ATTENTION "Collector가 아직 가져가지 않은 Event 1건"

`awaiting_collection`의 정의는 *intake가 promote했지만 수집되지 않은 것*이고,
staging 파일은 **promote된 적이 없다** — Desktop 4의 reporter가 `incoming/`에
직접 쓴 것이다. 느슨하게 보고된 숫자가 아니라 **그 숫자에 속하지 않는 파일**이다.

두 형제와 다른 점 하나는 정직하게 적었다: 이건 **다음 실행에서 저절로 사라진다**
(Collector가 소비해서 `rejected/`로 옮기고, 거기서는 이미 제 이름으로 불린다).
잘못된 이름이 한 실행 동안만 붙는다 — 다만 그 한 실행이 **crash 직후**, 즉 사람이
이 화면을 보는 바로 그 때다. 파이프라인은 손대지 않았다(§17과 같은 이유로 docs/03
결정이다). C27이 그은 Collector 경계 테스트도 그대로다.

**그 과정에서 내가 만든 회귀를 기존 테스트가 잡았다.** `name_collision`을 필터된
목록으로 계산하도록 바꿨는데, `test_a_staging_name_still_blocks_the_name_in_incoming`의
docstring이 *"Splitting the count must not narrow that check (BUG-43)"*라고 정확히
그것을 금지하고 있었다. `run_once()`는 목적지 이름이 차 있으면 원본이 무엇이든
거부하므로, staging 파일도 Event와 똑같이 영구히 막힌다. 세는 쪽만 좁히고 검사는
`all_incoming_paths`로 되돌렸다 — `rejected/` 쪽이 이미 같은 이유로 그렇게 하고 있었다.

테스트 5건(기존 `RejectedStagingResidueTests`를 상속해서 같은 fixture를 쓴다 —
한 파일에 대한 두 반쪽이 서로 다른 것을 검사하게 갈라지지 않도록). 전부 수정 전
구현에 대해 실패함을 확인했다.

### 25. 내가 §21에서 만든 blind spot — 규칙의 전제가 틀렸다 (신규, **중복 / 유실**)

§21의 라벨 순서 규칙은 이렇게 추론했다: *렌더러는 라벨을 한 번씩 순서대로,
요약 뒤에 쓴다 → 첫 bullet의 라벨이 아래 라벨보다 순서상 뒤면 렌더러가 쓴 것이
아니다 → **남는 설명은 산문뿐이다.*** 마지막 단계가 틀렸다. **docs/06 §57은
손편집을 허용하고, 손편집은 라벨 bullet을 요약 위로 옮길 수 있다** — 똑같은 배치다.

실측, `- Event ID: EVT-H`를 `- Owner:` 위로 옮긴 블록:

    existing_event_ids()        set()       <- 블록의 id가 사라진다
    select_late_candidates()    ['EVT-H']   <- **매 실행 다시 추가**
    Monthly                     통째로 drop

Company History 파일이 무한히 자란다 — §2가 닫은 결함(§38 가드가 렌더러가 쓴
것을 되읽지 못한다)이 다른 문으로 들어온 것이다. **순서 규칙이 생기기 전에는 이
reader가 id를 찾았으므로, 이건 §21이 만든 회귀다.**

**수정은 override 하나이고, 휴리스틱이 아니라 결정 가능하다:
어떤 exclusion도 블록에서 식별자를 전부 없애서는 안 된다.** 산문이라고 부르려는
그 bullet이 블록의 **유일한** `Event ID:`를 들고 있으면 그것은 라벨이다 —
블록 안에 다른 후보가 없기 때문이다. 아래에 두 번째 `Event ID:` bullet이 있으면
첫째는 정말 산문이고(그게 순서 규칙이 쓰인 이유다) 그대로 유지된다.

**덜 틀린 게 아니라 더 낫다.** 전에는 통째로 잃던 블록이 세 필드를 다 되찾는다:

    - Event ID: E1 / - Owner: COO / - the summary
        before  item dropped        after  (E1, COO, "the summary")

**같은 뿌리를 쓰는 reader 넷 전부에 적용된다** — `existing_event_ids()`,
`monthly/parser._first_bullet()`, `ops_status._kept_but_not_rendered()`,
`_monthly_counts_more_than_it_shows()`. 뒤의 둘은 손편집된 블록에서 유일한 id를
빼앗겨 **거짓 경보**를 냈을 경로였고(전자는 멀쩡한 Candidate를 "영구 유실"이라
보고, 후자는 멀쩡한 달을 shortfall로 보고), 실측으로 둘 다 `()`임을 확인했다.
`monthly`는 여전히 선언된 leaf라 두 벌이고, 행동 동등성 테스트의 배열 corpus에
이 override의 양쪽을 추가했다.

**그 김에 카운터의 거짓 경보 하나도 닫았다.** `unconsolidated`는 `- Event ID:`
줄을 items와 대조하는데, `Event ID: measured it.`라는 요약도 그런 줄이다.
파서가 **스스로 산문이라고 판정한** 줄을 유실로 세면, 아무것도 잃지 않은 문서에
대해 손실을 보고한다(실측 1 → 0). 걷어낸 것은 그 줄뿐이다 — walk 밖에 있는 줄은
여전히 걸러지지 않고 세어진다(섹션이 일찍 닫히는 실패가 이 카운터의 존재 이유다).

seed 고정 fuzz 3,000건 재실행: benign 유실 0, Late 억제 0, 비멱등 0,
Event Count 오류 0, Monthly 도달 실패 0. 테스트 7건 중 5건이 override 없이는
실패함을 확인했다(나머지 둘은 "§21의 수정을 되돌리지 않는다" 가드다).

### 26. 운영자에게 하는 말이 코드보다 세다 — ATTENTION 41줄 전수 대조 (신규, **관측성**)

ATTENTION은 AGENT.md §6이 **가장 먼저 읽으라고** 시키는 화면이고, 각 줄은 사실만
말하는 게 아니라 **원인과 조치를 단언**한다. 그 단언들을 코드와 대조한 적이 없어서
41줄을 전부 훑었다. 셋이 코드보다 셌다.

**(a) E-17 경보 — "어떤 실행도 이것을 넣지 않는다"는 틀렸다.** 전제는 맞다(6.5단계의
대상은 그 실행이 수집한 날짜뿐이다). 결론이 한 칸 더 갔다. 같은 날짜의 Event가
**하나라도 더** 수집되면 그 날짜가 `kept_dates`에 들어가고,
`select_late_candidates()`는 저장소의 **그 날짜 전체**를 보므로 방치돼 있던 것이
함께 들어간다. 실측:

    EVT-A 저장 -> Daily Close      2026-08-05.md 생성
    EVT-S 저장 (닫힌 뒤)           탐지기 ('EVT-S (2026-08-05)',)
    EVT-N 저장 (같은 날짜, 나중)   UPDATED_LATE_EVENT
                                   added_event_ids=('EVT-S', 'EVT-N')
                                   탐지기 ()

**자기 힘으로는 못 들어가고, 같은 날짜 동행이 생기면 들어간다.** 지난 날짜에
동행이 오지 않는 것이 보통이라 경보 자체는 옳지만, "어떤 실행도"는 **사람이 할
조치를 바꾼다** — 나중 실행이 고쳐줄 것을 손으로 Company History 파일을 고치게
만든다. 이 유일한 자동 복구 경로를 **아무 테스트도 지나지 않고 있었다**(테스트 4건
추가, 전제인 `for kept_date in sorted(kept_dates)`도 AST로 고정했다 — 6.5의 날짜
출처가 바뀌면 이 발견 전체가 달라지므로 주석을 읽어서 알게 되면 안 된다).

**(b) Monthly shortfall 경보 — 원인 하나를 단정하고 틀린 조치를 지시했다.**
"`- Category:` 줄이 네 값 중 하나인지 확인하라 — **다시 만들어도 같은 결과다**"라고
끝났는데, 원인은 둘이고 조치가 반대다. 실측:

    Category 원인      강제 rebuild 후에도 ('2026-08', 1, 0)   -> 맞다
    손편집 블록 삭제   그냥 재실행은 그대로, 강제 rebuild가 **복구**

docs/06 §57 / docs/11 §71이 허용하는 손편집이 똑같은 불일치를 만드는데, 그쪽은
rebuild가 고친다. 운영자를 한 달치 Daily에서 있지도 않은 잘못된 Category를 찾게
보내고 있었다. 두 원인과 각각의 조치를 적고, **이 검사는 둘 중 어느 쪽인지 말할 수
없다**는 것도 같이 적었다. 테스트 6건.

**(c) 그 검사의 docstring — "false-positive case to caveat 없음"도 과했다.**
`Consolidated Items`와 렌더된 항목 수는 **생성 시점에는** 어긋날 수 없지만, 파일은
그 뒤 디스크에서 산다. 손으로 항목 블록 하나를 지우면 `('2026-08', 3, 2)`가 뜬다.
보고하는 것 자체는 옳다(파일이 자기 총계와 모순된다) — 다만 파이프라인이 낸 유실이
아니고, 숫자를 고칠 때까지 화면에 남는다. "as generated"까지가 그 보증의 전부라고
고쳐 적었다.

나머지 38줄은 대조 결과 코드와 일치했다.

### 27. `.exists()` 전수 재조사 — C31의 스윕이 하나를 놓쳤고, 하나를 잘못 분류했다 (신규)

C31이 여섯 곳을 `.is_file()`로 바꿨다. **그 스윕이 실제로 전수였는지 아무도
확인하지 않았다.** `src/`와 루트의 `.exists()` 31곳을 전부 분류했다.

분류 기준은 그 호출이 **무슨 질문**을 하느냐다:

    "이름이 차 있나"        -> exists()가 맞다. 디렉터리도 이름을 차지한다.
    "이 산출물이 있나"      -> is_file()이어야 한다. 디렉터리는 산출물이 아니다.
    "state 파일이 없으면 기본값" -> 디렉터리면 read_text가 OSError를 내고
                              선언된 오류 타입으로 잡힌다. 시끄럽게 실패하므로 무해.

**놓친 것 하나 — `agent/outbox.stage()`.** 함수 첫 줄이 "Persist `event` in the
outbox"라고 약속하는데, Event 이름을 쓴 **디렉터리**가 있으면 그 경로를 돌려주고
**아무것도 쓰지 않았다.** 실측:

    outbox/EVT-1.json 이 디렉터리일 때
    stage() 반환                     EVT-1.json   <- 성공 보고
    (outbox/EVT-1.json).is_file()    False

outbox 쓰기는 Agent의 **내구성 경계**다 — `_collect_one_date()`가 `OSError`를 잡는
자리에 그렇게 적혀 있다: *"실패하면 Event는 아직 존재하지 않으므로 그 날짜는 수집된
것이 아니고 그렇게 표시해서도 안 된다."* 성공을 보고하는 `stage()`는 그 분기를
통째로 건너뛴다.

**차단돼 있었다** — 그래서 안 보였다. `drain()`이 그 항목을 `unreadable`로 잡고
`is_clear`가 False가 되어 날짜가 전진하지 않는다. 다만 운영자에게 뜬 것은
"outbox에 읽을 수 없는 파일 (Permission denied)"이었지 **어느 날짜가 수집에
실패했는지**가 아니었고, 그 차단은 설계가 아니라 운이었다 — 바로 아래
`except FileExistsError` 분기는 race 케이스를 위해 이미 단단히 해뒀는데 그 위의
fast path만 그대로였다. 둘 다 `is_file()`로 바꾸니 `write_event_json()`이 점유된
이름을 거부하고 `FileExistsError`가 호출자에 도달해 **`DateOutcome.FAILED`**가 된다.

**잘못 분류한 것 하나 — 그것도 내가 C31에서 했다.**
`test_name_taken_questions_still_use_exists`가 `stage()`를 "이름이 차 있나" 쪽으로
분류하고 `existing.exists()`를 **유지하라고 고정**하고 있었다. 그 테스트가 적어둔
근거는 *"is_file()로 좁히면 실행이 디렉터리를 덮어쓰려 들 것"* 인데, 실측하면 그런
일은 없다 — 거부는 한 단계 아래 `write_event_json()`이 하고 거기는 여전히
`exists()`다. `stage()`의 이른 반환은 거부 가드가 아니라 **"이미 됐으니 건너뛴다"**
fast path이고, "이미 됐다"는 진짜 파일이어야 한다. 테스트를 정정하고 두 절반이
계속 구분되도록 양쪽을 다 단언하게 했다.

나머지 29곳은 분류상 맞다. 특히 `collector/runtime.run_once()`의 목적지 가드,
`transport/intake`의 중복 검사, `desktop_activity`의 downstream 이름 검사는
**디렉터리도 이름을 차지한다**가 정확히 의도(BUG-53)이고, `agent/delivery.py`는
`.exists()` 뒤에서 `_problem()`이 `NOT_A_FILE`을 따로 돌려주고 있었다.

테스트 6건(그 중 2건은 수정 전 구현에서 실패함을 확인, 나머지는 "멱등 재stage는
여전히 no-op"과 "이미 있던 blocker는 예전대로 실행 전체를 막는다" 가드).
`review_cli`만 쓰는 `FileHistoryRepository.get()`은 디렉터리에서 OSError로 죽지만
사람이 앞에 있는 대화형 도구라 그대로 뒀다.

### 28. 중복 결함 전부가 위반하는 성질 하나를 아무도 단언하지 않고 있었다 (테스트 공백)

이 프로젝트가 겪은 중복 결함은 전부 같은 성질의 위반이다 — 매 실행 다시 붙는
Late Event, 다시 큐에 들어가는 Notion Event, 다시 통합되는 달, 커밋할 게 없는데
만들어지는 커밋. **각각 따로 발견돼 따로 고쳐졌고, 성질 자체를 단언한 것은
아무것도 없었다.** 그러면 다음에 그것을 깨는 컴포넌트도 같은 방식으로 — 누가
파일이 커지는 걸 눈치채서 — 발견된다.

전체 파이프라인 3연속 실행을 트리 해시로 대조했다(로그와 Run Manifest는 이름으로
제외 — 전자는 커지는 게 일이고 후자는 이번 실행을 기술하므로 같으면 오히려 이상하다).
결과는 **완전 멱등**이었다:

    run1 -> run2   added [] removed [] changed []
    run2 -> run3   added [] removed [] changed []
    commits 2 (fixture의 init + 진짜 backup 1)   unpushed 0
    backup_status  SUCCESS -> NOT_REQUIRED -> NOT_REQUIRED
    seen store     항목 그대로

**그런데 그 테스트에 이빨이 없었다.** §38의 중복 가드를 눈멀게 하고
(`existing_event_ids()` -> `set()`) 돌렸더니 **한 건도 실패하지 않았다.** 이유는
6.5단계의 대상이 *그 실행이 수집한 날짜*뿐이라, 새 입력이 없는 반복 실행에서는
그 단계가 **아예 실행되지 않기** 때문이다. 즉 "반복 실행이 멱등이다"는 중복
경로를 거의 지나가지 않는다.

이빨이 있는 모양은 **연속 실행이 각각 같은 (이미 닫힌) 날짜의 Event를 수집하는**
경우다. 실측, 2026-08-01자 Event를 세 실행이 하나씩 추가:

    수정본   `- Event ID:` 3줄   Late Events Added 2   Event Count 3
    눈먼 것  `- Event ID:` 7줄   Late Events Added 6   Event Count 0

Company History 파일의 무한 증가이고, 그 안의 Metadata 두 숫자가 서로 모순된다.
그 케이스를 추가하고 나서야 눈먼 가드에 대해 실패한다. 클래스 docstring의
과장("모든 중복 결함이 이 성질의 위반")도 실제로 무엇을 지나가는지로 고쳐 적었다.

테스트 6건. 파이프라인 무변경 — 멱등성은 이미 성립하고 있었고, 없던 것은 그것을
지키는 장치다.

### 29. Mutation Testing — 핵심 가드 17개를 부러뜨려 스위트에게 물었다 (검증, 결함 없음)

§28에서 **이빨 없는 테스트**를 하나 잡고 나니 같은 질문이 남는다: *다른 가드들은
실제로 덮여 있나, 아니면 이름만 그런가?* 이 프로젝트의 규칙("테스트가 있다를
믿지 않는다")을 스위트 전체에 적용하는 방법은 하나뿐이다 — **가드를 부러뜨리고
스위트가 알아채는지 본다.**

pytest 플러그인 하나로 가드를 하나씩 무력화하고 전체 스위트를 돌렸다. 17개 전부
**잡혔다.**

    is_incomplete_write      one_line              redact
    _looks_like_secret       safe_candidate_filename  safe_event_filename
    summary_line_indices     _is_sole_identifier   bounded
    try_acquire_lock         is_seen               _is_stable
    existing_event_ids       validate_event        overall_status
    _EXIT_CODES              _SEVERITY

**방법론 함정 하나를 기록해 둔다.** `-x`는 **가장 먼저** 실패한 테스트를 보여주는데,
그게 행동 테스트가 아니라 **구조/인벤토리 테스트일 수 있다.** `_SEVERITY`와
`overall_status`는 둘 다 `DeadCapabilityInventoryTests`가 먼저 잡았다 — 함수 객체가
lambda로 바뀐 것을 **정체성으로** 알아챈 것이지 동작으로 알아챈 게 아니다.
그 테스트를 deselect하고 다시 돌려야 진짜 답이 나온다:

    _SEVERITY (구조 테스트 제외)  -> test_run_contract.py::FailureClassificationTests
                                     ::test_a_daily_close_failure_fails_the_run

즉 CRITICAL 컴포넌트가 전부 DEGRADED가 되면(잃어버린 Daily Close가 exit 2 대신
exit 3을 보고하게 된다) **행동 테스트가 잡는다.** 그리고 그것을 확인하려면
`-x` 없이 돌려야 한다. 함수 객체를 바꾸는 대신 **테이블 값만** 바꾸는 변이가
이 함정을 피한다(`_EXIT_CODES`를 전부 0으로 만든 변이는 곧바로
`ExitCodeContractTests::test_every_overall_status_maps_to_exactly_one_exit_code`가
잡았다).

결함 없음. 파이프라인 무변경. 플러그인은 저장소에 남기지 않았다(루트에 추적되지
않는 파일을 더하지 않는다) — 재현에 필요한 것은 위 목록과 이 방법이 전부다.

### 30. Daily 시퀀스에 구멍이 나도 모든 지표가 정상을 보고한다 (신규, **데이터 유실**, 탐지)

Recovery/DR 감사에서 나왔다. **Local Master를 git에서 이전 시점으로 부분 복원하면
Daily 시퀀스 가운데가 비는데, 그것을 보는 것이 아무것도 없다.** 실측, 열흘이 닫힌
상태에서 08-04~08-06을 지우고:

    check_state_consistency()   CONSISTENT
    ATTENTION                   그 사흘에 대해 아무 말도 없음
    Scheduler 다음 실행          last_close+1부터 시작 — 영원히 돌아오지 않는다

**Company History 사흘이 영구히 사라졌는데 모든 지표가 건강하다.**
`check_state_consistency()`가 틀린 게 아니다 — §47이 그것에게 묻는 것은 *마지막*
닫힌 날에 파일이 있느냐이고 답은 예다. **가운데를 보는 눈이 없었을 뿐이다.**

**판정은 파일만으로 결정 가능하다.** docs/07 §30이 순서대로 닫고 건너뛰지 않으며,
`generate_daily_history()`는 **일이 없는 날에도 파일을 쓴다**(빈 달 파일이 있는
것과 같은 이유, docs/09 §72). 따라서 Daily 파일명은 **끊기지 않는 날짜 구간**이어야
하고, 파일이 있는 두 날짜 **사이**의 빈 날짜는 파일이 있었다가 사라진 것이다.
정책 결정도, 새 설정도 필요 없다.

`COMPANY_OPS_HISTORY_START_DATE`로 구간을 잡지 않았다 — 자주 미설정이고
(`_history_start_date()` 참고) 그러면 검사가 통째로 사라진다. **가장 이른 파일**이
설정 없이도 옳은 하한이다: 그 앞은 이 머신의 History가 아니고, 두 파일 사이의
공백은 그렇지 않다.

**꼬리 결손은 보고하지 않는다.** 중간에 실패한 실행이 남기는 정상적인 재시도
모양이고 다음 실행이 채운다. 가운데만 본다.

**진단이 아니라 조치가 되게 했다.** Backup Working Copy는 바로 옆에 있고 미백업
검사가 이미 그 트리를 훑는다. 사라진 날 중 **거기 아직 남아 있는 것**을 이름으로
집어 준다:

    구멍 3일: 2026-08-04, 2026-08-05, 2026-08-06 — … 그 중 2건은 Backup Working
    Copy에 아직 있다(2026-08-04, 2026-08-05)

**쓰기 전에 이 머신의 실 runtime으로 전제를 확인했다:** `local_master/daily`와
`backup_working_copy/daily` 둘 다 2026-08-05..2026-08-10이 빈틈없다 — 이 검사가
기대는 성질이 fixture가 아니라 실제 트리에서 참이다. 오탐 0.

**성능 — 측정 후 결정했다.** 처음엔 `glob("*.md")` + `is_file()`이었는데 파일당
stat이 한 번 더 든다:

     730개(2년)    glob+is_file 10.90 ms    scandir 0.69 ms   (16배)
    3650개(10년)   glob+is_file 58.75 ms    scandir 3.48 ms   (17배)

10년치가 whole-view 기준선(~44 ms)보다 커지므로 `os.scandir`로 바꿨다(두 형태가
같은 리스트를 돌려주는 것을 먼저 단언했다). 이 머신 실 runtime에서 검사 추가분은
**+0.17 ms — noise 안**이다.

테스트 10건. 디렉터리가 날짜 이름을 쓰고 있으면 **없는 것으로 센다**(C31이 다른
여섯 곳에 적용한 규칙 그대로), `notes.md`와 `.tmp-` 잔여물은 무시, 꼬리 결손·단일
파일·빈 트리는 조용하다. 파이프라인 무변경 — **탐지만** 추가했다(무엇을 복원할지는
docs/10 §64의 운영자 결정이다).

### 31. Monthly 시퀀스에도 같은 구멍이 있다 — 다만 이쪽은 복구된다 (신규, 탐지)

§30의 정확한 형제다. `pending_months()`는 오래된 달부터 건너뛰지 않고 통합하고,
docs/09 §72는 **중요한 일이 없던 달에도 파일을 쓴다**("아무 일 없었다"와 "잊었다"를
구분하려고 존재하는 규칙이다). 따라서 Monthly 파일명도 끊기지 않는 달 구간이어야
하고, 가운데의 빈 달은 파일이 있었다가 사라진 것이다.

실측, 2026-01~2026-08이 통합된 상태에서 04·05를 지우고: **어떤 ATTENTION도
언급하지 않는다.** `pending_months()`는 마지막 통합한 달 **다음**부터 시작하고,
state-대-history 검사는 마지막 달만 묻는다 — §30과 같은 모양이다.

**다만 조치가 Daily보다 낫고, 그게 정확해서 문장에 적었다.** Monthly는 Daily에서만
파생되므로(docs/09 §12-13) 되만들 수 있다. 실측 end-to-end:

    2026-07.md 삭제      그냥 재실행: statuses []  — 그대로 없음
    mark_month_dirty()   MONTHLY_GENERATED — 파일과 **내용까지** 복귀(EVT-1 포함)

Daily는 그런 약속을 할 수 없고, 이 메시지도 그런 척하지 않는다.

달 산술은 문자열이 아니라 진짜 산술로 했다 — 2025-12 → 2026-02는 한 달 빠진
것이고 키를 텍스트로 비교하면 그렇게 나오지 않는다. 테스트 7건(연말 경계, 꼬리
결손, 단일 달, 디렉터리가 달 이름을 쓴 경우, `notes.md`/`.tmp-` 무시, 그리고
위 복구 경로를 실제로 돌리는 것 하나).

### 32. 삭제된 Company History가 Run Manifest에 이름조차 남지 않았다 (신규, **관측성**)

§31을 조사하다 나왔다. Local Master에서 파일이 사라지면
`sync_to_working_copy()`가 add/commit/push를 **통째로 막고** BACKUP_FAILED로
끝낸다 — 설계가 훌륭하다. 삭제는 원격으로 전파되지 않고, Backup은 CRITICAL이라
run이 FAILED가 되고 exit 2가 나간다. 여기까지는 다 맞다.

**그런데 Manifest에 그 사실이 없다.** 실측, Daily 하루치를 지우고 실행:

    classification  BACKUP_FAILED
    reason          ""              <- 비어 있다
    metrics         changed_files=1

`deleted_files`는 **성공 분기에만** 실려 있었고, 삭제 분기는 `push_result`가
None이라 `reason`도 비었다. `last_run.json`만 읽어서는 **Company History가
지워졌다는 사실 자체를 알 수 없고**, 자격증명 실패와도 구별되지 않는다 — 둘 다
`BACKUP_FAILED`/`PERMANENT`/`CRITICAL`이다. docs/14 §3이 Manifest에게 요구하는
것이 정확히 "무슨 일이 있었고 자세한 건 어디 있는지" 한 줄인데, 이 실행에서
일어날 수 있는 가장 무거운 일에 대해 그 한 줄이 비어 있었다.

`deleted_files` metric을 삭제 분기에도 싣고, `reason`에 사실을 적었다:

    reason: "Local Master에서 파일 1건이 사라져 Backup이 add/commit/push를
             중단했다 (docs/08 §31): daily\2026-08-01.md"

**새 classification 값은 만들지 않았다.** 그 어휘는 docs/14 §5의 것이고 §5의
예시가 `BACKUP_FAILED`를 인증 실패에 묶고 있다 — 값을 늘리는 것은 Run Contract
변경이라 §6에 따라 SKIP한다(아래 SKIP 항목 참조). `reason`은 `Failure`의
docstring이 자유 텍스트라고 명시한 필드이므로 계약을 건드리지 않고 사실을 담을 수
있다. Severity/Retryability/Overall/Exit은 그대로임을 테스트로 고정했다.

테스트 5건. 삭제가 원격으로 전파되지 않는다는 §31의 본래 성질(Working Copy에
파일이 남아 있고 unpushed 0)과, 평범한 백업은 `deleted_files`가 0이라는 반대
방향도 같이 고정했다.

### 33. 확인했고 결함이 없던 것 (같은 조사를 다시 하지 않기 위해)

**(a) git push 실패 메시지가 remote URL의 자격증명을 흘리는가 — 아니다.**
운영자가 PAT를 remote URL에 직접 박는 것(`https://ghp_…@github.com/o/r.git`)은
흔한 셋업이고, 그러면 push 실패 메시지가 그 URL을 인용한다. `_report_backup_failure()`는
그것을 stderr로 찍고 스케줄러 로그가 그것을 캡처한다. 실측:

    fatal: unable to access 'https://127.0.0.1:1/o/r.git/': Failed to connect …
    token present: False

**git 자신이 URL에서 자격증명을 지운 뒤 메시지를 만든다.** §11의 Notion 경로와
결정적으로 다른 점이고, 그래서 여기에는 아무것도 걸지 않았다.

**(a0) Multi-Desktop 동시 실행 — 한 폴더에 쓰기와 읽기를 동시에, 결함 없음.**
Desktop 4는 Agent와 Runner를 **같은 머신에서** 돌리고, 단일 머신 구성에서는
Agent의 sync 폴더와 Runner의 transport 폴더가 같은 디렉터리다(B절 3번). 두 스레드로
실제 경합을 만들었다 — 한쪽은 `OneDriveTransport.send()`로 200건을 쓰고, 다른 쪽은
같은 디렉터리에 대해 `run_intake()`를 60회 돌린다:

    errors            없음
    보낸 것            200
    incoming/에 도착   200
    sync/에 남은 것    0   (staging 잔여물 0)
    이름 깨짐          없음
    잘린 파일          없음

`stable_after_seconds=0`으로 **안정화 창을 꺼 놓고** 쟀다 — 경합을 가려 줄 가드를
일부러 제거한 것이다. 그래도 200/200이 온전한 이유는 창이 아니라 **쓰기 쪽의
원자성**(mkstemp + `os.replace`)과 intake가 `.tmp-`를 건너뛰는 규칙이다. 즉 이
보장은 타이밍에 기대고 있지 않다.

**(a2) Recovery / DR — 양방향 실측, 결함 없음.** §15의 수정이 §28의 복구 경로
(*"이전 실행이 파일을 쓰고 state를 저장하기 전에 죽은 경우"*) 위에 정확히 앉아 있어서
그 경로를 실제로 돌려 봤다.

    state 유실 / History 생존
      run 1                COMPLETED, 10일 생성, 포인터 2026-08-10
      state 파일 삭제 후 run 2
                           COMPLETED, 포인터 2026-08-10으로 복원
      파일 byte 동일       True     중복 0건     개수 10 -> 10

    History 유실 / state 생존 (오래된 백업에서 복원)
      최근 3일 삭제
      consistency          STATE_INCONSISTENCY   (보고됨)
      다음 실행            COMPLETED, regenerated=[]  (조용히 되만들지 않음)

두 방향 다 옳다. state는 산출물로부터 완전히 복구되고 덮어쓰기·중복이 없으며,
반대로 History가 없어진 경우는 **보고하되 프로그램이 임의로 다시 만들지 않는다**
(docs/10 §46 금지, §49 "History가 State보다 우선", §64 운영자 판단). 그리고 오래된
백업에서 복원하면 **가장 최근 날짜가 빠지는데 그것이 정확히 watermark 검사가 보는
날**이라, §48의 좁은 범위가 이 DR 경로에서는 제대로 발화한다.

**(b) Windows 예약 장치 이름(`NUL`/`CON`/`COM1`…)이 Event를 삼키는가 — 아니다,
그러나 이유가 sanitiser가 아니다.** `_UNSAFE_FILENAME_CHARS`는 문자 화이트리스트라
`event_id="NUL"`을 손대지 않고 통과시킨다. Win32에서 그 base name은 **장치**이고,
거기에 쓰면 성공하면서 바이트는 사라진다 — 이 저장소가 가장 두려워하는 실패 모양
(docs/11 §50 "History 손실"을 성공으로 보고). 직접 실측:

    NUL        쓰기 -> exists=True, size=0,  디렉터리 목록에 **없음**
    NUL.json   쓰기 -> exists=True, size=10, 있음

**확장자가 막고 있다.** 이 프로젝트가 신뢰할 수 없는 id에서 만드는 모든 파일명은
`.json`으로 끝나고(`safe_event_filename` · `safe_candidate_filename`),
`NUL.json`은 평범한 파일이다. 예약 이름 5종을 실제 `write_event_json()` /
`read_event_json()`으로 왕복시켜 확인했다 — 실제 파일, 실제 내용, 정확한 왕복.

고칠 것은 없고 **고정할 것이 하나** 있었다: 언젠가 확장자 없는 이름을 파생하면
다른 가드가 하나도 없다. 테스트 3건으로 그 성질을 고정했다
(`test_untrusted_event_input.py::ReservedDeviceNameTests`).

**(c) Lock liveness 판정이 로케일에 매여 있는가 — 매여 있지만 오늘은 안전하다.
가정을 처음으로 고정했다.** `_is_process_running()`은 Windows에서
`str(pid) in tasklist_stdout`를 묻는다 — **번역되는** subprocess 출력에 대한
substring 매칭이다. no-match 메시지에 숫자가 하나라도 들어가면 **죽은 pid가
살아 있다고 답하고**, 그러면 stale lock이 영원히 인수되지 않아 모든 실행이 조용히
건너뛰어진다(BUG-42의 결과, 다른 경로).

기존 테스트는 이 probe가 *"pid 숫자를 찾으므로 로케일 독립적"*이라고 적어 두었다.
**맞지만 읽히는 것보다 좁다** — no-match 메시지에 숫자가 없다는 데 의존한다.
이 머신(UI culture ko-KR) 실측:

    정보: 실행 중인 작업 중 지정된 조건에 일치하는 작업이 없습니다.   (숫자 0개)

그 성질을 **주장이 아니라 단언으로** 바꿨다 — suite가 실제로 도는 로케일이 무엇이든
그 출력에 숫자가 없음을 확인한다(ko-KR·en-US 양쪽에서 통과 확인). 언젠가 "0 tasks
found" 같은 문구로 바뀌면 Runner가 멈추는 대신 이 테스트가 먼저 깨진다.
동작 변경 없음(`test_lock_atomicity.py::ProcessProbeFailureTests::
test_the_property_that_makes_it_locale_independent`).

**(d) Agent의 오류 문자열은 원천에서 이미 `redact()`된다** — `agent.py`의 세 지점
(456·484·497행)이 전부 `redact(...)`를 통과시킨다. §12에서 `one_line`만 추가한
이유가 이것이다(§7·§9의 다른 "형제 없음" 결과는 그쪽에 적혀 있다).

**(e) 로케일 의존 단언 전수 조사.** §5(b)를 찾은 뒤 테스트 전체에서 OS/셸 메시지
문자열에 매인 단언을 훑었다. `test_oplog.py`의 `assertIn("denied")`는 테스트 자신이
만든 `PermissionError("denied")`이고, `test_install_agent_task_script.py:411`의
"Access is denied"는 docstring의 실측 기록이지 단언이 아니다. **형제 없음.**

---

## C30. Stale-Claim Sprint

C29가 SKIP 사유를 재감사해 4/6에서 성과를 냈다. C30은 남은 지정 항목
(A-1~A-6b, A-11~A-15, A-17, A-18, E-9b, E-13, E-14, E-19, E-20)에 같은 세
질문을 적용한다. 이번 Sprint의 공통 발견은 **SKIP 판단은 옳은데 그 옆에 적힌
보조 주장이 낡아 있다**는 것이다.

### 1. E-13 — "테스트도 있다"가 이 분기에는 해당하지 않았다

E-13은 docs/14 §7이 Lock 미획득 실행의 Exit Code를 적지 않는다고 기록하고,
"실제 동작은 … **이는 일관되며 테스트도 있다**"고 덧붙인다. SKIP 판단(spec
문서 수정)은 옳다. 보조 주장을 확인하니 **그 분기에는 테스트가 없었다.**

기존 `_print_result()` 테스트는 전부 **완료된 실행의 result 튜플**을 넘긴다.
`None`(= lock 미획득)을 넘기는 테스트는 저장소 전체에 0건이다. 즉 그 문장은
함수의 일반 커버리지에 대한 것이지 이 분기에 대한 것이 아니었다 — C29가 A-10에서
찾은 것과 같은 모양, 한 단계 작은 규모다.

실측: `_print_result(None)` → **exit 0**, `[SKIPPED] …` **stdout**, stderr 빈 문자열.

**왜 "문서 완결성"이라는 표현보다 중요한가:** 이 분기는 **stale lock이 영구화하는
경로**다(BUG-42). Runner는 Task Scheduler가 띄우고 그 자동 신호는 exit code
하나뿐이므로, 영원히 skip하는 실행은 영원히 성공을 보고한다. C27이
`run_agent.py`의 동일 분기에 대해 쓴 논거와 같고, 그쪽도 고정되기 전까지
고정돼 있지 않았다.

테스트 5건(`test_architecture_invariants.py::LockSkippedRunContractTests`).
그중 하나는 **§7이 정말 exit code를 적지 않는지**를 spec 본문에서 확인한다 —
언젠가 적히면 그 테스트가 깨지고 E-13을 닫을 수 있다. 하나는 두 진입점의
skip 분기가 **같은 숫자**를 반환하는지 본다.

### 2. E-14 — 완화 주장을 필드 단위로 재측정

E-14의 SKIP 판단(새 영구 산출물 경로 = docs/14 §2 Taxonomy 변경)은 옳다. 그
옆의 완화 주장은 **필드 단위로 보면 정확하지 않다.**

> §68의 9개 필드 중 타임스탬프 둘을 뺀 전부는 이미 Run Manifest의 `backup`
> component와 `backup_state.json`로 운영자에게 도달한다.

실측:

| 필드 | 실제 |
|---|---|
| `run_id` | Manifest가 자기 것을 갖고, 둘이 같음은 기존 테스트가 고정 |
| `backup_start` / `backup_end` | 주장에서 제외 |
| `final_status` | 기록됨(`status` + `backup_state.json`) |
| `commit_hash` | 기록됨(양쪽 경로 + `backup_state.json`) |
| `changed_files` | **개수만** 기록(목록 아님) |
| `deleted_files` | **개수만**, 그리고 **성공 경로에만** |
| `push_result` | **실패 사유로만** — 성공한 push는 어디에도 기록되지 않음 |
| `source` | **어디에도 기록되지 않음** |

정직한 판본은 "9개 중 6개가 산출물에 도달하고, 그중 둘은 크기로만, 하나는
실패했을 때만, 하나는 전혀"다. `source`는 **어느 Local Master에서 온 백업인가**를
말하는 필드이고, Local Master가 둘 이상인 머신에서 정확히 그것이 필요하다.

**두 번째 축소:** C27 §6이 실패한 component만 metrics를 출력하게 했으므로
(의도적), **성공한 백업은 Manifest 파일에는 도달하지만 운영자 화면에는 닿지
않는다.** 화면에 남는 것은 C27 §17이 노출한 `backup_state.json`뿐이다.

결정은 바뀌지 않는다. 바뀐 것은 **결정할 때 쓰일 비용 추정이 과소평가되지
않는다**는 것이다. 테스트 7건
(`test_architecture_invariants.py::BackupLogFieldsThatReachAnArtifactTests`).

### 3. E-19 — C27이 모르고 완화해 둔 부분

E-19(Notion `Last Updated` 비교 불가 → Retry Queue 무한 증식)의 SKIP 판단은
옳고, 구조 가드까지 있다. 다만 C21이 측정할 때 없던 것이 지금은 있다.

C21의 기록: *"운영자가 보는 사유는 Notion을 한 글자도 언급하지 않는 `TypeError`"*
— 그리고 큐가 매 실행 한 건씩 늘어난다는 사실은 **어디에도 표시되지 않았다.**

C27 §6이 실패한 component의 metrics를 출력하게 만들면서, 그 사실이 화면에
올라왔다. 실측:

    ! notion_sync: NOTION_SYNC_INCOMPLETE [DEGRADED/UNKNOWN]
          processed=3 queued=3

즉 **큐 깊이가 실행마다 커지는 것이 이제 보인다**(queued=1 → 2 → 3). 사유
문자열이 여전히 Notion을 언급하지 않는다는 점은 그대로이지만, "무한히 커지는
중"이라는 사실 자체는 더 이상 숨어 있지 않다.

E-19 항목에 이 완화를 기록했다. **C27 §6은 E-19를 겨냥해 만든 것이 아니었다** —
Manifest의 metrics가 아무에게도 읽히지 않는다는 별개의 결함을 고친 것이고,
그것이 이 항목을 부수적으로 덜 위험하게 만들었다.

### 4. A-15 — 낡은 전제를 재측정하다 미기록 결함이 나왔다 (**신규**)

A-15는 스키마 거부를 지지하는 근거로 "받아들이지만 **나중에 터진다**"를 든다:
Windows에서 개행이 든 `event_id`는 적법한 파일명이 아니므로 History Filter가
`OSError`로 실행 전체를 중단시킨다(BUG-5).

**재측정: 더 이상 터지지 않는다.**

    safe_candidate_filename("HIST-X\n...") -> 'HIST-X_FORGED-LINE-82fa8b62e81e.json'
    repository.save(...)                   -> True, 파일 1개 저장

CEO 승인 B안(저장 경계에서의 sanitise)이 그 전제를 이미 없앴다. A-15의 남은
위험은 "크래시한다"가 아니다.

**그것을 확인하다 미기록 결함이 나왔다: `event_id`가 Daily Markdown 구조를
위조한다.**

`daily/markdown.py`는 `f"- Event ID: {candidate.event_id}"`로 **구조 필드**에
직접 넣는다. 실측 — `event_id = "X\n- Event ID: FORGED-EVENT"`:

    후보 1개 -> 렌더링된 하루에 `- Event ID:` 줄이 **2개**
             -> 존재한 적 없는 Event가 Company History에 한 줄로 선다

**BUG-11/BUG-27은 `summary`와 `evidence`만 기록하고 `event_id`는 이름이 없다.**
그런데 `event_id`가 셋 중 가장 나쁘다 — 나머지 둘은 산문에 끼어들지만 이것은
**구조 필드를 하나 더 만든다.** `oplog.one_line()`이 로그에 대해 같은 위조를
이미 닫았고(BUG-6/C10), 렌더러에는 대응물이 없다.

**여전히 SKIP:** `event_id`를 제약하는 것은 docs/02 Event Schema 계약(A-15),
렌더러를 escape하는 것은 docs/06 렌더링 계약(BUG-11/27)이다. 둘 다 결정이다.
바뀐 것은 **결정이 가정이 아니라 실측 위에서 내려진다**는 것과, `event_id`가
`summary`·`evidence` 옆에 기록됐다는 것이다.

테스트 5건(`test_untrusted_event_input.py::EventIdForgesDailyMarkdownStructureTests`).
그중 하나는 A-15의 낡은 전제(BUG-5)가 닫혔음을, 하나는 로그 쪽은 이미 막혀
있음을 대조로 보인다.

### 5. C29가 넣은 탐지기의 false negative — 같은 Sprint에서 자체 발견·수정

§4를 조사하다 **C29 §7이 만든 `_kept_but_not_rendered()`의 결함**이 드러났다.

그 검사는 `event_id not in text`로 판정했다. `E-1`은 `E-10`의 렌더링 줄에
포함된 문자열이므로, **정말로 유실된 `E-1`이 정상으로 보고됐다.** 실측:

    후보 E-1, E-10 / Daily에는 E-10만 렌더링
    수정 전: stranded = ()            <- E-1 유실이 보이지 않음
    수정 후: stranded = ('E-1 (2026-08-05)',)

**공격 입력이 필요 없다** — 평범한 순번 id면 충분하고, 이 저장소의 실제
id에도 접두 관계가 생길 수 있다(`EVT-1` / `EVT-10`).

**수정:** 렌더러가 쓰는 **줄 전체**와 비교한다. `daily/markdown.py`가
`- Event ID: {event_id}`를 쓰므로, 그 줄들을 집합으로 만들어 정확히 대조한다 —
렌더러가 답하는 것과 같은 질문을 묻는다.

부수 효과: 산문에 우연히 인용된 id도 더 이상 "렌더링됨"으로 세지 않는다
(테스트로 고정).

**이 Sprint가 확인한 것:** §4가 아니었다면 이 false negative는 발견되지 않았을
것이다. **한 항목의 낡은 주장을 확인하러 간 길에서 다른 항목의 결함이 나온다** —
SKIP 재감사의 값은 그 항목에서만 나오지 않는다. 그리고 목표가 매번 묻는
"자기 자신이 만든 blind spot"은 **묻기만 해서는 안 되고 실제로 입력을 넣어
봐야** 나온다.

테스트 3건 추가(`KeptButNotRenderedTests`).

### 6. 같은 부류 전수 조사 — 형제 없음 확인

§5의 결함(정확 일치가 필요한 곳의 느슨한 매칭)이 하나의 부류인지 확인하려고
`src/`와 `ops_status.py`에서 문서 전체를 대상으로 하는 멤버십 검사를 전부 훑었다.

후보는 하나였다 — `monthly/parser.py:110`:

    is_empty_day = EMPTY_DAY_MARKER in text

Empty Day 마커(`"No material company history recorded."`)를 문서 **아무 데서나**
찾으므로, 실제 내용이 그 문장을 인용하기만 해도 하루가 통째로 "빈 날"로
분류될 수 있어 보였다 — Monthly 집계에서 그 하루가 사라진다는 뜻이다.

**실측 결과 이미 방어돼 있다.** 같은 파일 186행:

    is_empty_day=is_empty_day and not items

항목이 하나라도 파싱되면 마커 위치와 무관하게 empty가 아니다. 실측으로 확인:
마커를 Summary 본문에 인용한 실제 하루 → `is_empty_day=False, items=1`.

**부류에 형제는 없다.** 나머지 `splitlines()` 기반 검사들은 이미 줄 단위다.
§5는 고립된 사례였고, 그 사실 자체를 기록해 둔다 — 같은 조사를 다시 하지
않기 위해서다.

---

## C29. SKIP-Reason Re-Audit Sprint

C28이 §6에서 확인한 것 — "승인 필요"라고 적힌 blocker가 실제로는 이미 결정돼
있었다 — 을 **SKIP 항목 전체에 적용**하는 Sprint다. 각 항목에 대해 세 가지를
묻는다: *정말 승인이 필요한가 / 이미 결정된 정책이 있는가 / 결정을 바꾸지 않고
탐지·진단·특성화까지 갈 수 있는가.*

### 1. A-7 — 기록된 범위가 실제보다 좁았다 (재측정, **P1**)

A-7은 손상된 Candidate 하나가 "**그 날짜에서** 영구히 멈춘다"고 적고 있었다.
실측하면 그 날짜만이 아니다.

    손상 없음        COMPLETED, 9일치 생성, Daily 파일 9개
    손상 후보 1개    FAILED,    **0일치 생성, Daily 파일 0개**

`scheduler.run_once()`는 keep 인덱스를 **배치당 1회, 날짜 루프 이전에** 만든다
(성능 최적화이며 그 자체로 주석이 붙어 있다). 따라서 실패는 어떤 날짜도
시도되기 전에 일어나고 `failed_date`는 손상 후보의 날짜가 아니라 **첫 번째
pending 날짜**로 보고된다. **Company History 생성이 통째로 멈춘다.**

**두 사실이 새로 분명해졌다:**

- `repository.list()`는 **살아남는다**(후보를 파싱하지 않은 채 반환). raise는
  timestamp를 읽는 `build_keep_index()`에서 난다 — 즉 BUG-38의 `list()` 문제가
  아니라 **같은 경로로 보고되는 두 번째, 더 넓은 문제**다.
- JSON이 깨진 후보도 같은 지점에서 같은 결과를 낸다.

**탐지는 C28 §11이 이미 닫아 두었다**(우연히 — 그 절은 BUG-38을 겨냥해 썼는데
timestamp 파싱 실패도 같은 함수가 잡는다). C29에서 **문구를 실측에 맞게 고쳤다**:
"다음 실행의 Scheduler가 실패한다"는 참이지만 너무 작았고, 이제 "**모든 날짜의**
Daily History 생성이 멈춘다(실측: 9일치 → 0일치)"라고 말한다.

**여전히 SKIP:** 격리/건너뛰기/정지 중 무엇이 옳은가는 A-7의 Data Safety 결정
그대로다. 격리 자체는 정확하다(실패를 정밀하게 보고하며 삼키지 않는다).

테스트 6건(`test_scheduler.py::OneCorruptCandidateStopsEveryDateTests`).
baseline 9일치와 손상 시 0일치를 **둘 다** 고정한다 — 인덱스가 루프 안으로
옮겨지면 숫자가 바뀌고 문구를 다시 봐야 하기 때문이다.

### 2. A-19 — 정책은 결정이지만, 사실 보고는 아니었다 (탐지 추가)

A-19는 "리다이렉트된 `daily/`를 백업할 것인가"가 배포 정책이라 SKIP돼 있고, 그
판단은 옳다 — 이전 Sprint가 거부를 실제로 구현했다가 정당한 레이아웃을 깨뜨린다는
이유로 되돌렸다.

**그러나 "그 리다이렉트가 존재한다"는 사실을 말하는 것은 결정이 아니다.**
실 sync로 재측정:

| 확인 | 결과 |
|---|---|
| `Path.is_symlink()` | **False** — sync의 symlink 가드가 놓친다 |
| `os.path.isjunction()` | **True** — 표준 라이브러리가 정확히 안다 |
| `sync_to_working_copy()` | `daily/linked/notes.md`, `daily/linked/private.md` **복사됨** |
| `scan_for_secrets(master)` | **()** — 아무것도 걸리지 않음 |

**두 가드가 모두 조용한 이유가 구조적이다.** `_relative_files()`는 symlink를
제외하는데 junction은 symlink가 아니고, secret scan은 **secret 형태 이름**에만
반응한다. 평범한 파일은 조용히 나간다. BACKLOG의 C24 메모("scan_for_secrets가
잡아낸다")는 **그 파일이 secret 이름일 때만** 참이다.

**수정(보고만, 거부 아님):** `_junctions_in_scope()` — 백업 범위 안의 junction과
그 대상 경로를 블록에 한 줄로 적는다. Backup이 복사하는 것은 **하나도 바뀌지
않는다.**

**ATTENTION이 아니라 블록에 둔 이유(C26 규칙):** 의도적으로 리다이렉트한 배포
에서는 어떤 조치로도 사라지지 않는 경보가 된다. 없던 것은 경보가 아니라
**리다이렉트가 존재하고 어디를 가리키는지**라는 사실이다.

`os.path.isjunction()`은 Python 3.12+다. 그보다 낮으면 **추측하지 않고 침묵한다**.

**여전히 SKIP:** 리다이렉트를 인정할 것인가 = 배포 정책. 인정하지 않기로 하면
구현은 작다(`_is_link_like()` + 비하강 walk).

테스트 5건(`test_observability.py::JunctionInBackupScopeTests`). 그중 하나는
실 sync로 전제를 확인하고, 하나는 구버전 Python에서 침묵하는지 본다.

### 3. A-16 — 기억이 아니라 검증으로 (특성화)

A-16은 "Dashboard Database 5개 중 1개만 쓰인다"를 C10 이후 **BACKLOG의 문장으로만**
갖고 있었다. 아무것도 확인하지 않으므로 조용히 참이 아니게 되거나, 조용히 참인
채로 모두가 고쳐졌다고 여길 수 있다 — E-11이 말하는 양방향 표류다.

**조사 중 스스로 한 번 틀렸고, 그것이 방법을 정했다.** `grep`으로 세니
`build_ops_backup_properties`에 호출자가 **2곳** 있는 것처럼 보였다(기록이 낡은
줄 알았다). 실제로는 `notion/__init__.py`의 import와 `__all__` 항목이었다. AST로
`Call` 노드만 세면:

    build_ops_run_properties       1회 호출 (dashboard.record_run)
    build_ops_backup_properties    **0회**
    record_run                     1회 호출 (app/runner.py)

**기록이 맞았다.** 그리고 이것은 기존 `DeadCodeCharacterizationTests`가
**구조적으로 볼 수 없는** 종류다 — 그 테스트는 이름 참조를 세는데, export된
이름은 참조가 있으므로 살아 있어 보인다.

**추가한 것:** `DashboardDatabasesWithNoWriterTests` 4건. 호출 수를 AST로 고정하고,
왜 참조 카운트로는 안 보이는지를 명시하며, **쓰이지 않는 4개 중 3개는 builder조차
없다**는 것까지 고정한다("4개가 비어 있다"가 "4개가 호출 한 줄이면 된다"로 읽히지
않도록).

**여전히 SKIP:** 어느 Database에 무엇을 쓸지는 docs/04 §53("Notion 데이터 과잉
방지") 결정이고 `OPS_BACKUP` 연결은 실행마다 Notion 쓰기를 1회 늘린다. 이 테스트는
아무것도 정하지 않는다 — 누군가 연결하거나 지우는 날을 **눈에 보이는 사건**으로
만들 뿐이다.

### 4. A-10 — 이미 고쳐진 항목이 SKIP 목록을 차지하고 있었다

A-10은 두 가지를 적고 "둘 다 명세 문서 수정이므로 SKIP"이라고 했다. 실측하니
**하나는 이미 고쳐져 있었다.**

| 항목 | C29 실측 |
|---|---|
| README §12가 문서 목록을 다 담지 못한다 | **해소됨** — `docs/` 15개 전부 README에 있음, 누락 0 |
| 13개 문서의 `# D:\...` 헤더가 낡았다 | **유효** — 13건 그대로 |

첫 항목을 고친 것은 이전 어느 Sprint이고,
`test_readme_document_list_names_every_spec_that_exists`가 이미 **guard로 다시
쓰여** 있다 — 하드코딩된 목록이 아니라 `docs/*.md`에 대해 동적으로 검사하므로
새 spec이 추가되면 그것도 README에 있어야 통과한다. 그 테스트의 docstring이 왜
그것만 승인 없이 가능했는지도 적어 두었다: **"디스크에 있는 것의 목록을 완성하는
것은 새 정책을 만들지 않고 우선순위를 바꾸지도 않는다."**

**BACKLOG만 낡아 있었다.** A-10을 한 항목짜리로 정정했다.

**이 Sprint가 확인한 것:** E-11의 표류는 양방향이다. C22는 *"고치지 않았다는
기록이 BACKLOG에 도달하지 않는다"*를 찾았고, A-10은 그 반대다 — **고쳤다는
사실이 BACKLOG에 도달하지 않았다.** 결과는 같은 종류의 손해다: 이미 없는 문제가
우선순위를 차지하고, 실제로 남은 문제(여기서는 낡은 경로 헤더 하나)가 그 뒤에
가린다. **SKIP 목록은 주기적으로 실측에 대조해야 한다** — 이 Sprint 자체가 그
대조다.

---

## C28. Cleared-For-The-Wrong-Reason Sprint

C27은 "이 검사가 겨눌 곳이 여기 하나뿐인가"를 물었다. C28의 첫 질문은 그
다음이다 — **경고가 사라졌다는 것이 위험이 사라졌다는 뜻인가?**

### 1. Secret 경고가 틀린 이유로 사라진다 (E-21, 신규, **보안**)

C24가 "Working Copy에 Secret 형태 파일이 있다"를 ATTENTION에 올렸고 C26이 그것을
git-aware로 만들어 **다음 commit이 무엇을 담을지**에 정확히 답하게 했다. 원격의
**history**는 다른 질문이고, 아무도 묻지 않았다.

**실측**(실 bare remote, E-21 시나리오 그대로):

    1. Notion 토큰을 담은 `.env`가 Working Copy에 들어가 push된다
                                          -> ATTENTION 발생 (정확)
    2. 운영자가 그 메시지가 이끄는 행동을 한다 — 파일 삭제
                                          -> **ATTENTION 소멸**
    3. 원격에서 `git show HEAD:.env`      -> 토큰이 그대로 읽힌다

**경고는 노출이 해소돼서가 아니라 로컬 파일이 없어져서 사라졌다.** "경고가
없어졌다"가 가질 수 있는 최악의 의미이고, 2번은 운영자가 가장 먼저 할 행동이다.
C24의 문구가 "이미 push됐다면 자격증명 교체가 필요하다"고 적어 두긴 했지만, 파일을
지우는 순간 그 문장 자체가 화면에서 사라진다.

**수정(탐지·가시화만, Spec 미변경):** `_secrets_ever_committed()` 신설 —
`git rev-list --all --objects`로 **history에 존재한 적 있는 모든 경로**를 받아
게이트 자신의 술어(`backup.working_copy._looks_like_secret`)로 거른다. 이름 목록을
다시 적지 않았다 — 두 번째 의견은 곧 불일치다.

**두 probe는 서로 다른 말을 하고 독립적이다.**

    _would_reach_the_commit()   나가는 것을 막아라
    _secrets_ever_committed()   이미 나갔다

**건강한 머신에서는 발화할 수 없다** — 그래서 블록이 아니라 ATTENTION에 두었다.
docs/08 §28의 `.gitignore`를 가진 Working Copy는 그런 경로를 커밋한 적이 없으므로
history에도 없다. 7가지 구성으로 실측:

| 구성 | history | will-reach |
|---|---|---|
| secret 없음 | 0 | 0 |
| §28 `.gitignore` + `.env` 존재 | **0** | 0 |
| 같은 상태로 두 번 커밋 | **0** | 0 |
| git 저장소 아님 | 0 | 1(기존 fail-safe) |
| secret 있지만 커밋 안 됨 | 0 | 1 |
| **유출: `.gitignore` 없이 `.env` 커밋** | **1** | 1 |
| **유출: `notes/id_rsa` 커밋** | **1** | 1 |

즉 C26이 제거한 "올바른 머신에서 상주하는 경보" 모양이 **아니다.** 실제 유출
뒤에만 나타난다.

**fail-safe 방향이 sibling과 반대다(의도).** `_would_reach_the_commit()`은 넘겨받은
집합을 *거르므로* 실패 시 열어 두는 것이 실제 노출을 계속 보이게 한다. 이쪽은
history에 대한 **주장을 추가**하므로, git이 답하지 못했다고 유출을 단언하면
없는 것을 만들어 내는 것이 된다. 침묵하면 오늘의 동작으로 돌아갈 뿐이고
present-file 게이트는 영향받지 않는다.

**성능(추측 아님).** 쿼리 3종 비교(3,000커밋 ≈ 8년치): `rev-list --all --objects`
0.19초 < `log --name-only` 0.34초 < pathspec 한정 `log` 1.13초(커밋마다 diff를
강제해 가장 느리다). 명령 전체:

| Working Copy 커밋 | 0 | 100 | 1,000 | 3,000 |
|---|---|---|---|---|
| `ops_status.py main()` | 0.026초 | 0.085초 | 0.229초 | 0.369초 |

**sibling과 달리 깨끗해도 공짜가 아니다**, 그리고 그것은 간과가 아니라 감수다 —
이 함수가 보고하는 조건이 바로 "디스크에 아무것도 없는" 경우이므로 물어보기
전에는 할 말이 없다는 것을 알 수 없다. 8년치에서도 sub-second다.

**알려진 한계:** 로컬 Working Copy의 history를 읽는다. 운영자가 Working Copy를
새로 만들면(docs/08 §30이 허용) 원격에는 남아 있어도 로컬 history에는 없다. 원격에
네트워크로 묻지 않는 read-only 뷰가 볼 수 있는 범위 밖이다.

**여전히 SKIP인 것:** history 재작성, `git add -A` 범위 축소, 게이트를 Working
Copy로 겨누는 것 — 전부 E-15/E-21이 기다리는 결정 그대로다. 이번에 바뀐 것은
**결정 없이 가능한 가시화의 한계선**이다.

테스트 11건(`test_observability.py::SecretAlreadyInHistoryTests`). 그중 하나는
전제를 확인한다 — 커밋에서 토큰 바이트가 실제로 다시 읽히는가.

### 2. A-20의 탐지기는 B-6이 지우려는 파일에 의존한다 (신규 결합, 특성화)

A-20/BUG-25는 결정 대기다. 그러나 **그 탐지기 자체를 감사한 것은 이번이
처음**이고, 두 열린 결정이 사실은 하나라는 것이 나왔다.

`find_orphaned_events()`가 "어느 Event가 사라졌는가"에 답하는 방식은
`processed/`를 훑고 Event로부터 결정을 **재계산**하는 것이다. 즉 **Event 파일이
증거다.** 유실을 영구하게 만드는 seen store는 id만 갖고 있다.

B-6(보존 정책)은 `processed/`·`sent/`·`transport/`·`rejected/`·collector state를
지울지에 대한 열린 결정이다. 실측:

    processed 파일 존재       find_orphaned_events -> ['EVT-LOST']
    seen store                EVT-LOST를 안다
    파일 삭제(보존 정책 적용)  find_orphaned_events -> []
    seen store                **여전히** EVT-LOST를 안다

즉 보존 정책은 **이미 유실된 데이터를 더 잃지는 않지만**, 그것이 유실됐다는
**유일한 기록을 지우면서** 어떤 실행도 되살리지 않도록 보장하는 seen store
항목은 남긴다. **탐지 가능하던 유실이 탐지 불가능한 유실이 된다.**

**왜 탐지기를 seen store로 옮길 수 없는가.** store는 id만 갖는다.
`find_orphaned_events()`가 `HistoryFilter`로 결정을 재계산하는 이유가 바로
아무도 그것을 기록하지 않기 때문이고, **DROP Event는 Candidate가 없는 것이
정상**이다. Event 내용 없이는 "유실"과 "정상 DROP"을 구분할 수 없고 DROP이
흔한 쪽이므로, store 기반 탐지기는 모든 DROP을 orphan으로 보고하게 된다.
`processed/` 의존은 실수가 아니라 **정확성의 근거**다.

**B-6에 더하는 것:** 보존을 어느 쪽으로 정하든, `processed/`는 단순한 중복 방지
보조가 아니다. **A-20의 증거**이며, 지우면 이미 일어난 유실에 대해 시스템이
운영자에게 말할 수 있는 것이 조용히 줄어든다.

테스트 5건(`test_history_reconciliation.py::RetentionErasesTheEvidenceOfALossTests`).
그중 하나는 store 기반 대안이 왜 불가능한지를 DROP Event로 직접 보이고, 하나는
`HistoryFilter.evaluate()`가 정말 순수해서 재계산이 원래 판정과 같은지 확인한다.

### 3. BUG-55 — "뭔가 잘못됐다"에서 "이 디렉터리 이름을 바꿔라"로

C27 §17이 결과를 보이게 했다(원격에 도달하지 않은 Company History). 그러나
**원인은 말하지 못했다** — 운영자가 파일명 안의 대문자(`Daily\2026-08-13.md`)를
알아채고 그 의미를 알아야 했다.

`_misnamed_scope_directories()` 신설. Local Master의 최상위 디렉터리 중 **in-scope
이름과 대소문자만 다른 것**을 찾아 실제 이름과 올바른 이름을 함께 보고한다.

    Local Master의 `Daily/`는 백업 범위 밖이다 — Backup은 `daily/`만 본다
    (docs/08 §26, 대소문자 구분). … `daily/`로 이름을 바꿔야 한다

**허용 집합을 다시 적지 않았다.** `backup.working_copy._ALLOWED_TOP_LEVEL_DIRS`를
import한다 — 백업 범위에 대한 두 번째 의견을 만들지 않기 위해서이고, 세 번째
scope 디렉터리가 생겨도 이 함수를 고칠 필요가 없다.

**false-alarm 프로파일**(8구성 실측):

| 구성 | 진단 |
|---|---|
| `daily` + `monthly` | 없음 |
| **적법한 범위 밖** `decisions/`(§26 조건부) | 없음 |
| 이름만 비슷한 `dailies/` | 없음 |
| scope 이름의 **파일**(디렉터리 아님) | 없음 |
| 빈/없는 Master | 없음 |
| `Daily/` | `Daily/` → `daily/` |
| `MONTHLY/` | `MONTHLY/` → `monthly/` |
| 둘 다 틀림 | 둘 다 |

**두 줄의 역할을 나눴다.** C27의 줄은 *결과*(이 머신에만 있는 History, 파일명
포함)를 말하고 — 이는 BUG-55가 아닌 원인에도 해당한다 — 새 줄이 *원인*을 말한다.
그래서 BUG-55 귀속을 새 줄로 옮겼고, C27 테스트를 그 분담을 단언하도록 갱신했다
(약화가 아니라 두 줄을 함께 확인하므로 더 강해졌다).

**탐지만.** `_is_in_scope()`의 비교를 case-fold하는 것은 BUG-55 자신의 결정이고
(백업이 덮는 파일 집합이 바뀐다), Local Master의 디렉터리 이름을 프로그램이
바꾸는 것은 docs/08 §13/§46이 금지한다. **바뀐 것은 운영자가 무엇을 해야 하는지
아는가이다.**

테스트 10건(`test_observability.py::CaseFoldedScopeDirectoryTests`). 그중 하나는
전제를 확인한다 — 게이트가 정말 대소문자 변형을 거부하는가.

### 4. E-23 — 결정을 기다리는 동안 예방은 가능하다

E-23은 docs/04 §29-30("동시"도 skip)과 docs/06 §12(자정 기본 timestamp)가
각각 옳은데 그 사이에서 유실이 생기는 문제이고, 셋 중 어느 명세를 조정할지가
결정 사항이다. C27이 재현·특성화를 끝냈다.

**결정 없이 가능한 것이 하나 남아 있었다: 애초에 그 경로를 타지 않게 하는 것.**
1초만 달라도 정상 적용된다는 것은 이미 테스트로 고정돼 있다.

`AGENT.md` §3(Signal 작성)에 추가:

> **같은 날짜·같은 프로젝트에 Signal을 두 개 이상 쓸 때는 `timestamp`를 넣어라.**
> … 두 번째 Signal은 Notion에 반영되지 않는다. **Company History에는 둘 다
> 정상적으로 들어간다** — 어긋나는 것은 Notion 쪽 Current State의 최신성뿐이다.

**문구를 테스트로 고정하지는 않았다.** C27 §14가 세운 기준 그대로다 — 텍스트를
고정하는 테스트는 편집마다 깨져서 곧 지워진다. 대신 이 지침이 **참인 근거**가
이미 고정돼 있다: `SameTimestampDifferentEventTests`가 동점 skip과 1초 차이
정상 적용을, `test_the_agent_really_does_give_every_signal_the_same_timestamp`가
자정 기본값을 단언한다. 유도가 바뀌면 그쪽이 깨지고 이 문단을 다시 보게 된다.

### 5. E-22 — "가능성이 낮다"를 "구조적으로 불가능하다"로 (측정)

E-22 항목은 완화 근거를 *추측*으로 적고 있었다("실제 `event_id`는 … 형태로
생성되므로 가능성은 낮다"). 유도 코드를 직접 재서 확정했다.

    derive_event_id() -> uuid5(namespace, "source|date|signal_id")
    실측 charset      -> [0-9a-f-] 뿐, 전부 소문자

**Desktop 1~3의 Signal 경로로는 대소문자 충돌이 만들어질 수 없다.** 입력으로
끼워 넣을 수도 없다 — `signal_id`는 파일 stem이고, E-22가 존재하는 대소문자
무구분 파일시스템에서 이름만 다른 두 파일은 한 파일이다. 구분하는 쪽에서는
두 개의 **완전히 다른** uuid5가 나온다(변형이 아니다). 그리고 `event_id`는
Signal에서 **금지된 필드**다.

남는 표면은 `event_id`를 직접 지정하는 Event(`reporter` API, 외부 도구)뿐이다.
실재하지만 훨씬 좁고, **E-22가 BUG-55보다 아래에 놓이는 근거가 이제 측정이다.**

E-22 항목의 "완화되어 있는 것"을 이 결과로 교체했다. 테스트 5건
(`test_agent.py::EventIdCannotCaseCollideFromTheAgentTests`)이 유도 방식이
uuid5를 벗어나면 깨진다 — 좁힘이 근거 없이 살아남지 않도록.

**이 Sprint가 확인한 것:** BACKLOG의 완화 문구도 측정 대상이다. "가능성이 낮다"는
근거가 아니라 **아직 재보지 않았다는 뜻**일 수 있고, 우선순위는 그런 문장 위에
세워진다.

### 6. 결정을 기다린다고 적혀 있었지만, 그 결정은 이미 내려져 있었다

BACKLOG는 두 탐지를 하나의 결정에 묶어 SKIP해 두었다:

> **탐지를 이번에 넣지 않은 이유:** 시작일 이전인지 판정하려면
> `COMPANY_OPS_HISTORY_START_DATE`가 필요한데 `ops_status.py`는 그 값을 읽지
> 않는다 … **설정이 없을 때 무엇을 보고할지가 또 하나의 판단이라**, 조건만
> 정확히 적고 남긴다.

**그 판단은 같은 파일이 이미 두 번 내려 두었다.** `_agent_start_date()`가
`COMPANY_OPS_AGENT_START_DATE`를, 전달 정합성 검사가
`COMPANY_OPS_AGENT_SYNC_FOLDER`를 읽고, 둘 다 미설정이면 **"미설정"이라고 적고
그 계산만 건너뛴다** — 경보도 추측도 하지 않는다. 화면에 그대로 있다:

    전달 정합성         : 확인 불가 (COMPANY_OPS_AGENT_SYNC_FOLDER 미설정)
    (COMPANY_OPS_AGENT_START_DATE 미설정 — 미수집 날짜는 계산되지 않음)

`_history_start_date()`는 `_agent_start_date()`와 **같은 모양**으로 썼고, 그
동일성을 테스트가 세 경우(미설정·파싱 실패·정상)에서 나란히 단언한다. 그것이
"새 결정이 아니다"라는 논거 자체이기 때문이다.

**닫힌 탐지 ①: BUG-46의 영구 절반.** 시작일보다 이른 KEEP Candidate는 Scheduler가
그 이전으로 가지 않으므로 **어떤 실행에서도** Daily에 들어가지 않는다.
`find_orphaned_events()`는 clean을 보고한다(후보가 존재하므로 정확하다). C22가
좁혀 둔 대로 **미래 날짜는 제외**했다 — 자가 치유되므로 보고하면 스스로 지워지는
경보가 된다.

실측 프로파일: 미설정 → "미설정" 표기·경보 0 / 시작일 이후 후보 → 0 / 미래
날짜 → 0 / **시작일 이전 → 1건(후보명과 날짜를 댄다)** / 파싱 불가 env → 미설정과
동일 / 읽을 수 없는 후보 → 건너뜀(BUG-38으로 뷰를 죽이지 않는다) / staging
파일 → 0.

메시지는 운영자가 실제로 손댈 수 있는 원인을 댄다: **보내는 Desktop의
`COMPANY_OPS_AGENT_START_DATE`가 Desktop 4의 시작일보다 이르면 그 차이가
원인이다.** 손상이 아니라 평범한 다중 Desktop 설정 오류로 도달한다.

**닫힌 탐지 ②: 해결 불가능한 `dirty_months`.** `monthly/generator.py`의 dirty
루프는 시작일 이전 달을 거부하고(docs/09 §85-86) **플래그를 일부러 남긴다** — 그
주석이 이유를 적어 두었다: *"silently forgetting it would hide a state file that
needs a person."* 그런데 Runner는 PENDING을 실패로 세지 않고(평범한 경우에는
옳다) `late_update.log`에 한 줄 쓰고 지나가며, 그 로그는 아무도 읽지 않는다.

**게다가 ATTENTION이 사실과 다른 말을 하고 있었다** — 그 달에 대해서도
"다음 Runner 실행에서 자동 처리된다"고. 이제 자동 처리되는 달과 **어떤 실행도
처리할 수 없는** 달을 나눠서 말한다. 시작일을 모르면 아무 달도 처리 불가로
분류하지 않는다(오늘의 동작 그대로).

**부수 확인:** 잘못된 month key(`"not-a-month"`)는 이 분류기에 **도달하지 않는다** —
`monthly.load_state()`가 먼저 거부하고 state 손상으로 보고된다. 분류기의 방어는
belt-and-braces이며, 그 사실을 테스트가 적어 둔다(도달하지 않는 방어는 아무도
관리하지 않으므로).

**이 Sprint가 확인한 것:** "승인 필요"라고 적힌 항목도 **재평가 대상**이다.
필요한 결정이 이미 다른 이름으로, 같은 파일 안에서 내려져 있을 수 있다. 그것을
찾는 비용은 읽기 한 번이고, 이번에는 그것으로 탐지 두 개가 열렸다.

테스트 16건(`CandidatesBeforeTheHistoryStartTests` 10,
`UnresolvableDirtyMonthTests` 6).

### 7. E-17의 유실을 처음으로 보이게 했다 (탐지, Spec 미변경)

E-17은 실패한 Late Event 병합이 재시도되지 않는 문제이고, 그 자신의 측정이
가장 중요한 문장으로 끝난다:

> 파일을 고쳐도 아무 일도 일어나지 않고, **모든 지표가 정상을 보고하는 채로**
> Company History에 Event 하나가 비어 있다.

C20이 분류를 정정해(RETRYABLE → PERMANENT) **실패한 그 실행**은 보이게 했다.
그 뒤의 상태 — Candidate는 저장돼 있는데 그 날짜 Daily에는 없고, 이후 모든
실행이 SUCCESS인 상태 — 는 여전히 보이지 않았다.

**판정은 실행 사이에서 결정 가능하다. 그래서 결정이 필요 없었다.** 5단계가
Candidate를 쓰고, 6단계가 Scheduler가 닫은 날짜를 렌더링하고, 6.5단계가 이미
닫힌 날짜에 떨어진 것을 병합한다 — 전부 **한 실행 안에서**. 따라서 실행이
끝난 뒤, Daily 파일이 **존재하는데** 자기 `event_id`가 없는 Candidate는 병합되지
않은 것이다.

**C31 정정 — 그 다음 문장이 과했다.** 여기에는 "재시도할 주체가 없다"라고
적혀 있었고 ATTENTION도 "**어떤 실행도 이것을 넣지 않는다**"라고 말하고 있었다.
괄호 안의 전제("6.5의 대상은 그 실행이 수집한 날짜뿐이다")는 맞지만 결론이
한 칸 더 갔다. 그 날짜의 Event가 **하나라도 더** 수집되면 그 날짜가 `kept_dates`에
들어가고, `select_late_candidates()`는 저장소에 있는 **그 날짜의 모든** 후보를
보므로 방치돼 있던 것이 함께 들어간다. 실측:

    EVT-A 저장 -> Daily Close        (2026-08-05.md 생성)
    EVT-S 저장 (닫힌 뒤)             -> 탐지기: ('EVT-S (2026-08-05)',)
    EVT-N 저장 (같은 날짜, 나중)     -> UPDATED_LATE_EVENT
                                        added_event_ids=('EVT-S', 'EVT-N')
                                        탐지기: ()

즉 **자기 힘으로는 못 들어가고, 같은 날짜 동행이 생기면 들어간다.** 지난 날짜에는
그런 동행이 오지 않는 것이 보통이라 경보 자체는 옳지만, "어떤 실행도"는 사람이
할 조치를 바꿀 만큼 틀린 문장이었다 — 자동으로 복구될 수 있는 것을 손으로 고치게
만든다. 두 곳 다 실측 문장으로 바꿨고 회귀 테스트로 고정했다.

Daily 파일이 **없는** 후보는 제외한다 — 아직 렌더링 전(Scheduler 창)이거나
BUG-46의 시작일 이전 경우이고, 후자는 §6이 자기 방식으로 보고한다.

**쓰기 전에 이 머신의 실 runtime으로 전제를 확인했다:** 저장된 14개 후보 중
**13개가 자기 Daily 파일 안에 있었고, 나머지 하나는 정말로 없었다.** 코드를
넣자마자 그것이 이름과 날짜와 함께 화면에 떴다 — E-17의 모양이 아무도 모르는 채
거기 있었다.

    ! KEEP Candidate 1건이 저장돼 있는데 그 날짜의 Daily History에 없다:
      RUNNER-PROD-E2E-002-FAILTEST (2026-08-05) — … 어떤 실행도 이것을 넣지
      않는다. 사람이 확인해야 한다

이제 A-20(수집됐으나 Candidate 없음)과 E-17(Candidate 있으나 Daily에 없음)이
**각각 원인과 함께** 나온다 — "Company History에 Event가 비어 있다"의 두 절반이다.

Runner 실행 중일 수 있는 창은 `find_orphaned_events()`가 이미 문서화한 그것이고,
같은 방식으로 처리한다: 목록을 감추지 않고 "(Runner 실행 중 — 완료 후 재확인
권장)"을 덧붙인다.

**여전히 SKIP:** 실제 재시도 메커니즘(실패 날짜 영속화 또는 정합성 패스)은
E-17이 기다리는 결정 그대로다. 바뀐 것은 **그 결정이 내려지기 전에도 유실을
알 수 있다는 것**이다.

테스트 9건(`test_observability.py::KeptButNotRenderedTests`). 그중 하나는 매칭
대상이 renderer가 실제로 쓰는 형식(`- Event ID: {event_id}`)인지 확인한다.

### 8. 이번 Sprint가 넣은 성능 회귀 — 측정으로 잡고 즉시 고쳤다

§6·§7이 `keep/`을 각각 전수 읽게 되면서 회귀가 생겼다. 목표대로 **전체 명령**을
쟀다.

| 규모(processed/daily/keep) | 수정 전 | 최적화 후 |
|---|---|---|
| 30 / 30 / 14 (현 운영) | 0.205초 | **0.086초** |
| 10,000 / 730 / 5,000 | 40.3초 | **16.7초** |

**원인 귀속에서 하마터면 또 틀릴 뻔했다.** 개별 probe를 `min(2회)`로 재니
`_kept_but_not_rendered`가 0.28초로 나왔다 — **warm 캐시**다. C27이 정정한 바로
그 편향이라, 각 probe에 **새로 쓴 트리를 주고 1회만** 다시 쟀다:

    cold _kept_but_not_rendered (5,000 후보)   24.3초   (warm 0.28초 — 87배)

**최소 최적화 두 가지, 둘 다 이미 있는 것을 재사용한다.**

1. `_read_keep_candidates()` — 두 검사가 같은 파일을 두 번 읽던 것을 **한 번**으로.
2. 그 읽기에 `ThreadPoolExecutor` + `_READ_WORKERS` 적용 —
   `app/desktop_activity.py`·`history/reconciliation.py`·`agent/delivery.py`가
   **같은 이유로** 이미 쓰는 관용구다(항목당 비용의 거의 전부가 파일 open).

    cold 두 검사 합계   24.3초 -> **6.4초** (3.8배)

3.8배는 C27이 순서 편향을 걷어내고 확정한 스레드 풀 계수(cold 4.2배)와 일치한다.
**같은 Sprint에서 정정한 측정 방법이 바로 그 Sprint의 회귀를 잡았다.**

`FileHistoryRepository.list()`는 여전히 쓰지 않는다(BUG-38로 뷰 전체가 죽는다).
읽지 못한 Candidate는 버린다 — 어느 검사도 읽지 못한 파일에 대해 사실을 주장할
수 없다.

### 9. F-7 / BUG-41 재측정 — 가설이 틀렸고, 그래서 결과가 더 정확해졌다

가설: C27이 넣은 미백업 History 검사가 BUG-41(덮어써지는 `BACKUP_FAILED`)의
blast radius를 좁혔을 것이다. **실 remote로 양쪽을 다 재 보니 절반은 틀렸다.**

| 시나리오 | run 2 | state | 원격 | C27 검사 |
|---|---|---|---|---|
| 원격이 복구됨 | BACKUP_SUCCESS | SUCCESS | **파일 도착** | **0건 (정확)** |
| 원격이 계속 죽어 있음 | 다시 예외 | PENDING | 비어 있음 | **1건 (파일명 명시)** |

**첫 줄에서 가설이 틀렸다.** 원격이 복구되면 run 2가 **실제로 전달하므로** 덮어쓰기가
옳고, 검사가 조용한 것도 옳다. C25가 이미 기록한 성질 그대로다 — *"실패는 유출을
막지 못하고 미루기만 한다"*. 여기서는 그 미룸이 **정상 복구**로 끝난다.

**둘째 줄이 실제 성과다.** 문제가 지속되면 History가 이 머신에만 남고, 그것을
검사가 파일명과 함께 보고한다.

**좁혀진 것을 정확히 말하면:** 이 검사는 **`backup_status`를 전혀 읽지 않는다.**
Company History와 `last_successful_backup`만 비교한다. 따라서 `backup_status`가
무엇으로 덮어써지든 — SUCCESS로 덮어써져도 — **미백업 History는 계속 보인다.**
BUG-41을 고친 것이 아니다(상태는 여전히 덮어써진다). **위험하게 만들던 결과**를
없앤 것이다.

테스트 1건 추가(`UnbackedCompanyHistoryTests::test_the_check_never_consults_backup_status`) —
`backup_status`가 SUCCESS라고 주장하는데 아무것도 push된 적 없는 state를 만들어,
검사가 그 주장을 무시하고 사실을 보고하는지 확인한다.

**여전히 SKIP:** FAILED를 사람이 지울 때까지 유지할지는 BUG-41이 기다리는 결정
그대로다. 이번에 바뀐 것은 그 결정이 내려지기 전까지 **무엇이 위험한 채로 남아
있는지**가 정확해졌다는 것이다.

### 10. 우선순위 나머지 — 승인 없이 가능한 것이 남아 있지 않음을 확인

| 항목 | 이번 Sprint에서 한 것 | 남은 것 |
|---|---|---|
| **BUG-42** | 없음 — C23(Runner)·C27(Agent)로 **양쪽 lock 탐지 완비** | 읽기 전용 속성을 벗길지 / 반환 계약을 나눌지 = 결정 |
| **B-6** | A-20 증거 결합을 §2에서 실측·기록 | 보존 정책 자체 = 결정 |
| **E-15/E-21** | §1(history 노출 탐지) | 게이트를 어느 디렉터리에 겨눌지 = 결정 |

세 항목 모두 **탐지·가시화·특성화는 소진**했고, 남은 것은 전부 "무엇이 옳은가"를
정하는 일이다.

### 11. 자기 변경 blind spot 검사 — 이번 Sprint가 만든 침묵 하나

§6·§7의 두 검사는 파싱하지 못한 Candidate를 **버린다.** 그 자체는 옳다 — 읽지
못한 바이트에 대해 사실을 주장할 수는 없다. 그런데 그렇게 버린 파일을 **보고하는
곳이 하나도 없었다.**

**실측**(`keep/`에 잘린 JSON 하나):

    검토 대기 Candidate : 0        <- review/를 센다
    Candidate 정합성    : OK       <- processed/를 훑는다
    ATTENTION           : 이 파일에 대해 아무 말 없음

**무해하지 않다.** `scheduler.run_once()`는 배치 시작 시
`repository.list()`로 keep 인덱스를 만들고, 그 호출은 첫 번째 읽기 불가 Candidate
에서 raise한다(BUG-38). 즉 **다음 실행의 Scheduler 단계가 실패한다.** 지금은 그
전에 파일 이름을 대고 말한다.

읽기는 이미 하고 있었으므로 비용은 0이다 — `_read_keep_candidates()`가 버린
목록을 함께 돌려준다.

**false-alarm 프로파일:** 정상 후보 → 0 / 잘린 후보 → 1(파일명) / 정상+잘림 →
1 / `.tmp-` staging → **0**(끝나지 않은 쓰기는 손상된 Candidate가 아니다, C27) /
빈 디렉터리 → 0.

**§5의 나머지 질문에 대한 답**(전부 이번 Sprint 추가분에 적용):

| 질문 | 결과 |
|---|---|
| 새 탐지기가 다른 경로를 놓치는가 | `_secrets_ever_committed`는 **로컬 history만** 본다(WC 재생성 시 원격의 과거는 못 본다 — docstring에 기록). `_misnamed_scope_directories`는 최상위만 보는데 `_is_in_scope()`도 `parts[0]`만 보므로 일치 |
| KEEP 외 REVIEW는? | REVIEW는 설계상 렌더링되지 않는다(E-20). 대상 아님 |
| 예외를 흡수해 조용해졌는가 | **그렇다 — 이 절이 그것을 닫았다** |
| 제외 조건이 중요한 파일을 숨기는가 | `.git` 제외는 `git ls-files`가 어차피 나열하지 않는 범위, `.tmp-` 제외는 C27이 정의한 "완료되지 않은 쓰기" |
| 정상 상태의 불필요한 경보 | 새 탐지 6종 전부 healthy 구성에서 **0건** 실측 |
| 성능 | §8에서 회귀를 잡고 고쳤다 |

---

## C27. Uncommitted-Write Sprint

C26이 "실행되는 코드도 틀린 말을 할 수 있다"를 확인했다면, C27은 그 바로 옆이다 —
**모든 단계가 정상 종료했는데도 디스크에 남아 있는 것.** 이번 Sprint의 결함
2계열은 전부 "쓰기가 끝나지 않았거나, 이미 끝난 일인데 파일이 그대로 있다"에서
나왔고, 둘 다 이 저장소가 이미 알고 있던 사실의 **읽는 쪽**이 비어 있었다.

### 1. 커밋되지 않은 쓰기(`.tmp-*`)를 모든 소비자가 산출물로 읽는다 (신규, 6곳)

이 프로젝트의 원자적 쓰기 15곳은 전부 같은 관용구를 쓴다.

    fd, tmp = tempfile.mkstemp(dir=<목적지 디렉터리>, prefix=".tmp-")
    ...write...
    os.replace(tmp, final)

`AtomicWriteFailureCleanupTests`(C-이전)가 **예외 경로**의 정리를 증명한다 —
`except BaseException: os.remove(tmp); raise`. 아무도 보지 않은 것은 **처리할
예외가 없는 경로**다: 전원 차단·SIGKILL·컨테이너 정지처럼 프로세스가 돌아오지
않으면 staging 파일이 남고, **이 저장소의 어떤 코드도 그것을 지우지 않는다.**

읽는 쪽이 구분할 수 있었다면 무해했을 것이다. 구분하지 못한다 — 모든 스캐너가
확장자로 디렉터리를 나열하고, `.tmp-….json`은 `glob("*.json")`에 그대로 걸린다.
**측정**(디렉터리마다 버려진 staging 파일 1개):

| 소비자 | 관측된 동작 |
|---|---|
| `transport.run_intake()` | `.tmp-abc.json`을 **Event로 승격**해 `incoming/`으로 옮겼고 Collector가 처리했다 |
| `backup.sync_to_working_copy()` | `added`로 보고 → **잘린 Company History 하루가 커밋·push**됐다. 그리고 그것을 Master에서 지우면 `deleted`로 보고되는데, 이 게이트는 `deleted`가 비어 있지 않으면 **아무것도 적용하지 않으므로** 이후 모든 실행이 실패한다 — **쓰레기를 치우는 행동이 영구 BACKUP_FAILED를 만든다** |
| `FileHistoryRepository.list()` | 잘린 것은 `JSONDecodeError`(BUG-38 → 그 날짜 전체 차단), 완전한 것은 **같은 Candidate를 두 번** 반환 |
| `agent.outbox.pending()` | `drain()`이 `unreadable`로 보고 → `DrainSummary.is_clear`가 **영구 False** → Agent가 수집 날짜를 영원히 진행하지 않는다. Agent 자신이 버린 파일 때문에 |
| `app.desktop_activity._count_transport()` | `awaiting_intake`에 포함 → 지워지지 않는 backlog |
| `ops_status.py` | `daily 파일` 수에 포함, `monthly` 목록에 `.tmp-…`가 **한 달처럼** 표시, `검토 대기 Candidate`에 포함 |

가장 무거운 것은 두 번째 줄이다. 나머지는 잘못된 수치지만, 그쪽은 **운영자가
취할 수 있는 유일하게 온당한 조치(쓰레기 삭제)가 백업을 영구히 망가뜨린다.**

**수정 — 규칙을 새로 만들지 않고, 쓰는 쪽이 이미 선언한 것을 읽는 쪽에 알린다.**

`is_incomplete_write()` 하나. 원본은 쓰는 모듈(`reporter/local_output.py`)에
두고, 그것을 import할 수 없는 leaf 3개(`transport`·`backup`·`history`,
`LayeringInvariantTests`가 고정한 layering)에는 **byte-identical 사본**을 둔다 —
`safe_event_filename()`이 이미 같은 이유로 그렇게 돼 있다. `.tmp-` 접두사는
장식이 아니라 이 저장소가 15곳에서 스스로 선언해 온 **"이 쓰기는 끝나지
않았다"**는 표시이고, 테스트 스위트 자신이 `glob(".tmp-*") == []`를 "잔여물
없음"의 정의로 쓴다. 읽는 쪽에서 그것을 지키는 것은 정책 신설이 아니라 **이미
있는 불변식의 구현**이다.

`git_ops.py`와 마찬가지로 **`scan_for_secrets()`는 건드리지 않았다.** 그쪽은
`.tmp-…pem` 같은 것도 잡아야 하는 fail-safe 방향이다.

**지우지 않는다.** 이 접두사를 가진 파일은 **다른 프로세스가 지금 쓰고 있는
중**일 수도 있고, 그것이 바로 승격하면 안 되는 이유와 같은 이유다. 모든
소비자가 "건너뛰되 남긴다"로 통일했고, `ops_status.py`가 개수를 세어 **"Event가
아니라 중단된 실행의 잔여물이며 지워도 안전하다"**고 말한다 — ATTENTION 중
유일하게 "지우라"고 하는 줄이다.

**`IncompleteWriteInvariantTests`**(4건, subtest 31)가 사본 4개가 서로 일치하는지,
그리고 **쓰는 쪽 15곳의 실제 `mkstemp` 접두사와도** 일치하는지 매 실행 대조한다.
접두사만 바꾸고 넘어가면 모든 소비자가 조용히 다시 열리기 때문이다.

### 2. 재전송된 중복 하나가 ATTENTION을 영구 점유한다 (신규)

`agent/outbox.py`의 docstring은 "Transport 수락"과 "sent/로 이동" 사이의 crash를
**설계된 복구 경로**로 규정하고, 재전송이 무해한 근거로 이 skip을 직접 인용한다.

    transport.run_intake()   already in incoming/processed/rejected
                             -> skipped_already_present

파이프라인에 대해서는 참이다. **뷰에 대해서는 아무도 확인하지 않았다.**
`run_intake()`는 그 파일을 `transport/`에 그대로 두고, `transport/`에서는
**아무것도 삭제되지 않는다.** 측정(원본이 수집된 뒤 재전송 1건):

    run 1..3   moved=0  skipped_already_present=1
               awaiting_intake=1, is_clear=False, 매 실행
               ATTENTION "수집되지 않고 남은 Event: transport=1"

문장이 사실과 다르다(그 Event는 수집됐다). 그리고 어떤 실행도 지우지 못한다.
`unparseable`(0바이트 placeholder), `future_dated`(BUG-30), `name_collision`
(BUG-43)에 이은 **같은 모양의 네 번째**이며, 방아쇠는 셋 중 가장 흔하다 —
성공한 재시도.

**수정:** `already_collected`로 분리하고 `awaiting_intake`에서 제외했다.
`future_dated`가 아니라 `unparseable`의 선례를 따른다 — intake의 판정이
결정론적이고 그 판정을 만드는 downstream 쌍둥이를 아무도 지우지 않으므로, 이
파일은 대기 중인 일이 아니라 **이미 끝난 일**이다. 쌍둥이가 아직 `incoming/`에
있으면 `awaiting_collection`이 그 일을 이미 세므로 in-flight 신호는 잃지 않는다.

### 3. 그 수정이 가릴 뻔한 것 — 이름만 같고 Event가 아닌 쌍둥이

2번을 그대로 두면 **거짓 경보 하나를 없애고 누락 경보 하나를 만드는** 교환이
된다. intake의 판정은 이름 기반이므로(BUG-53) "already present"는 두 가지를
합치고 있다.

| 쌍둥이 | 실제 상태 |
|---|---|
| 같은 `event_id` | 재전송된 중복. 할 일 없음 |
| 디렉터리 / 0바이트 placeholder / **다른 `event_id`** | 전달되지 않은 Event가 **자기가 아닌 파일에 막혀** 있다 — 조용한 유실 |

두 번째 부류는 수정 전에도 (엉뚱한 이유로) ATTENTION에 떠 있었다. 그래서
쌍둥이를 열어 **`event_id` 두 개를 비교**한다. 같으면 중복, 그 외(다름·읽기
불가·파일 아님)는 `suppressed`로 ATTENTION에 남는다.

**이름이 아니라 id를 비교한 것이 잡아낸 것:** `safe_event_filename()`은 id를
바꿔야 할 때마다 digest를 붙여 **두 id가 한 이름을 공유하지 않도록** 보장한다.
그 보장은 **대소문자를 구분하지 않는 파일시스템에서 성립하지 않는다** — 배포
대상인 Windows(docs/11)에서 `EVT-a.json`과 `EVT-A.json`은 한 경로다. 실측:
`EVT-A`가 이미 수집된 상태에서 도착한 `EVT-a`는 `skipped_already_present`로
조용히 사라지고 Company History에 없다. 파이프라인의 다른 어떤 지점도 이것을
볼 수 없다. **탐지만 추가했다** — 파일명 유도 규칙을 바꾸는 것은 CEO 승인 B안
(`safe_event_filename` / `safe_candidate_filename`)을 바꾸는 일이므로
`SuppressedDeliveryTests::test_a_case_only_filename_collision_is_a_suppressed_delivery`
로 경계만 고정하고 **SKIP**한다(아래 E-22).

### 4. Runner Lock은 감시하고 Agent Lock은 감시하지 않았다 (신규)

C23이 BUG-42의 침묵을 닫을 때 대상은 `runtime/locks/company_ops.lock` 하나였다.
`agent/agent.py`는 **같은 `scheduler.lock` 모듈을 그대로 재사용**해
`runtime/agent/locks/agent.lock`을 잠그는데, 그 파일은 아무도 보지 않았다.

**비대칭이 반대 방향이다.** Runner Lock이 막히면 Company History를 *조립하는*
머신이 멈추고, Run Manifest와 history 카운터가 그것을 본다. Agent Lock이 막히면
History를 *생산하는* 머신이 멈추는데, `run_agent.py`는
`SKIPPED_ALREADY_RUNNING`에 대해 **exit 0**을 반환한다(그 모듈 docstring:
"0 COMPLETED, or skipped because another Agent run holds the lock"). Task
Scheduler는 매일 성공을 기록하고, 수집되는 것은 없다.

**측정**(죽은 pid를 기록한 lock 파일 + 읽기 전용 속성 — BUG-42와 같은 모양):

    stale_lock_cannot_be_cleared(agent.lock)   True
    ops_status.py AGENT 섹션                   Lock에 대한 언급 없음
    유일한 흔적                                 "agent has not run for N day(s)"

마지막 줄이 문제다. 그 신호는 **N일이 지나야** 뜨고, 원인이 아니라 증상을
말한다 — 그리고 Agent가 한 번도 성공한 적 없는 머신에서는 "never completed a
run"과 구분되지 않는다.

**수정:** `_print_agent()`에 `_print_last_run()`이 Runner Lock에 대해 이미 하는
두 검사를 그대로 붙였다 — `stale_lock_cannot_be_cleared()`와
`lock_held_since()`. 새 함수도, 새 상태도, 새 결정도 없다. 메시지는 운영자의
실제 문제를 적는다: **실행이 실패한 것이 아니라 매번 성공했다는 것.**

읽기 전용 판독기만 쓴다(`is_locked` / `lock_held_since` /
`stale_lock_cannot_be_cleared`, `try_acquire_lock()`은 절대 아님) — 이 스크립트가
"Agent가 도는 중에 실행해도 안전하다"고 약속하기 때문이다.

**테스트가 먼저 잡은 것:** 처음 쓴 fixture는 lock 파일에 `pid` 필드를 썼는데
실제 계약은 `process_id`다(`try_acquire_lock()`이 쓰는 모양,
`LockFileContractTests`가 고정). 제 이름을 지어낸 fixture는 아무것도 검증하지
않는다 — 두 검사 모두 조용히 통과했을 것이다.

테스트 9건(`test_observability.py::AgentLockIsReportedTests`). 그중 하나는
전제를 확인한다(`try_acquire_lock()`이 정말 False를 돌려주는가), 하나는 C26의
규칙을 확인한다(파일을 지우면 경고가 사라지는가).

### 5. `unparseable` 처리를 `incoming/`에는 적용한 적이 없다 (신규)

`transport/`의 0바이트 placeholder가 `awaiting_intake`를 영구 점유하던 문제는
`unparseable` 분리로 닫혔다. `incoming/`에는 **같은 고장이 그대로 남아 있었다.**

`collector/runtime.run_once()`는 파일마다 `read_text(encoding="utf-8")`을 하고,
그것이 raise하면 FAILED로 기록하고 **파일을 그대로 둔다.** 읽기는 결정론적이고
아무도 그 파일을 다시 쓰지 않으므로 매 실행 같은 일이 반복된다. `name_collision`
(BUG-43)은 영구 FAILED의 **다른 원인**을 세므로 이것을 보지 못한다.

**측정**(디코딩 불가 바이트 1개, Collector 3회 실행):

    run 1..3   collector failed=1 매번, 파일은 incoming/에 그대로
               awaiting_collection=1, is_clear=False 매번
               ATTENTION "수집되지 않고 남은 Event: incoming=1"

**술어는 intake의 것이 아니라 Collector의 것이어야 한다.** 둘은 중요한 지점에서
갈린다 — UTF-8로는 읽히지만 JSON이 아닌 파일은 intake에게는 `unparseable`이지만,
`collector.collect()`는 그것을 **REJECTED로 판정해 첫 실행에 `rejected/`로
옮긴다.** intake의 술어를 빌려 왔다면 **나가는 중인 파일을 박혀 있다고 보고**
했을 것이다 — 이 저장소가 반복해 닫아 온 바로 그 어긋남이다.

그래서 `collector/runtime.py`가 `is_readable_event_file()`을 공개하고,
`run_once()`와 **같은 읽기 헬퍼**(`_read_event_text()`)를 공유한다. 규칙이 한
군데에만 있고 파일당 읽기는 여전히 1회다.

**수정:** `unreadable_incoming`으로 분리하고 `awaiting_collection`에서 뺐다 —
`unparseable`의 선례를 따른다(결정 대기 중인 것이 없고 파일은 그냥 박혀 있다).
`name_collision`처럼 남겨 두지 않은 이유도 같다: BUG-43은 "이미 처리됨"의 두 개념을
화해시키는 **열린 결정**에 걸려 있지만, 디코딩 실패에는 결정할 것이 없다.
`awaiting_collection_sources`도 같은 집합으로 좁혀 `SourceBreakdown.total`이
"세는 수와 항상 같다"는 자기 약속을 지키게 했다.

운영자에게는 `transport`의 `unparseable`과 **한 줄로** 보고한다. 두 단계가 각자
자기 술어로 판정했지만 사람에게는 하나의 사실이기 때문이다 — "읽을 수 없는 파일이
파이프라인에 박혀 있고 어떤 실행도 그것을 움직이지 못한다".

테스트 8건(`test_observability.py::UnreadableIncomingFileTests`). 그중 하나는
실제 Collector를 3회 돌려 **뷰가 부르는 이름과 단계가 하는 일이 같은지**를
확인하고, 하나는 두 술어가 갈리는 지점(valid UTF-8 / invalid JSON)을 고정한다.

### 6. Run Manifest에서 가장 정보가 많은 필드를 아무도 읽지 않았다 (신규)

`recorder.ok()` / `recorder.failed()`는 `**metrics`를 받고, `app/runner.py`의
모든 단계가 그것을 채운다 — `queued`, `processed`, `accepted`, `failed`,
`changed_files`, `generated_days`, `still_pending`, `failed_date`. 전부
`run_summary.json`에 기록된다. 그리고 **테스트 밖에서는 아무도 읽지 않았다**
(`grep -rn "\.metrics\b" src/ tests/` — production 소비자 0건).

**BUG-39와 같은 모양이 한 층 위에서 반복된 것이다.** BUG-39는
`IntakeSummary.failed`/`skipped_*`이 계산되고 버려지던 문제였고, 그 수정은 값을
Manifest로 보냈다. 값은 도착했고, **거기서 멈췄다.**

가장 아픈 사례는 Notion이다. 장애가 나면 매번 이 줄이 찍힌다.

    ! notion_sync: NOTION_SYNC_INCOMPLETE [DEGRADED/RETRYABLE]

Event 1건이 큐에 있든 400건이 있든 **글자 하나 다르지 않다.** 두 상황은 전혀
다르다 — "다음 실행이 따라잡는다" vs "Company History가 몇 주째 Notion과
벌어지고 있다" — 그리고 그것을 구분하는 숫자는 이미 디스크에 있었다. 게다가
docs/14 §5에 따라 RETRYABLE은 ATTENTION에 오르지 않으므로(그 자체는 옳다) 이
줄이 운영자가 볼 수 있는 **유일한 곳**이다.

**수정:** 실패한 component에 한해 metrics를 한 줄 덧붙인다. SUCCESS는 그대로
숨긴다 — 이 블록이 건강한 단계를 감추는 것은 의도이고, 그것을 숫자 벽으로
바꾸면 안 된다.

**`reason`은 계속 찍지 않는다.** `failure` 필드 중 시스템 밖의 텍스트(Notion API
메시지, 예외 문자열, event_id)를 담는 것은 그것 하나뿐이고, 이번 변경의 범위가
아니다. metrics 값은 전부 이 프로젝트 자신의 카운터·상태·날짜다.

그럼에도 `oplog.one_line()`을 통과시킨다. 이것은 **디스크에서 읽어 터미널에
렌더링하는 파일**이고, "디스크에서 읽은 것은 줄을 위조할 수 없다"는 규칙이
오늘의 metrics 목록이 그대로 유지되는지에 의존해서는 안 된다.

테스트 7건(`test_observability.py::FailingComponentMetricsAreShownTests`).
그중 하나는 이 변경의 요점 자체를 단언한다 — `queued=1`인 실행과 `queued=400`인
실행의 출력이 **같으면 안 된다**.

### 7. docs/10 §48 정합성 검사를 Monthly에는 겨눈 적이 없다 (신규, **데이터 유실**)

4번이 남긴 질문 — **"이 검사가 적용되어야 할 곳이 여기 하나뿐인가?"** — 을
C23~C27이 만든 탐지기 전체에 돌렸다. 하나가 더 걸렸고, 이번엔 관측성이 아니라
**유실**이다.

`scheduler/consistency.py`는 §48("State Last Success → Corresponding Local
History 존재?")을 구현하고 `ops_status.py`가 그것을 호출한다 — **Daily 쌍에만.**
`monthly_history_state.json`의 `last_successful_monthly_close`는 정확히 같은
종류의 주장을 하고("이 달은 통합 완료"), 그 주장을 뒷받침하는 산출물은
`monthly/<YYYY-MM>.md`다. **아무도 둘을 대조하지 않았다.** §48은 "Daily만"이라고
쓰여 있지 않다.

**왜 유실인가:** `run_once()`의 catch-up 대상은 `pending_months()`가 주고, 그것은
포인터 **다음부터** 시작한다. 포인터 아래의 달은 **어떤 실행도 다시 보지 않는다.**

**측정**(포인터 `2026-07`, 파일 삭제):

    monthly_run_once()   결과 0건 — 그 달을 아예 쳐다보지 않는다
    ops_status           "monthly 파일: 0"과 "마지막 통합한 달: 2026-07"이
                         두 줄 간격으로 출력, 둘을 잇는 문장 없음
    ATTENTION            비어 있음

Company History에서 한 달이 사라졌는데 모든 지표가 정상이다.

**거짓 경보일 수 없고, 그것을 주장이 아니라 테스트로 확인했다.** 포인터는 정확히
두 결과에서만 전진한다 — `MONTHLY_GENERATED`(방금 파일을 썼다)와
`MONTHLY_UNCHANGED`(파일이 이미 있었다). 그 외에는 루프가 break하고 포인터는
그대로다. 따라서 **포인터가 찍혀 있는데 파일이 없다**는 상태는 모호하지 않다.
C24·C26이 남긴 교훈대로, 깨끗한 경우를 검증하지 않은 탐지기는 예정된 거짓
경보이므로 실제 generator를 돌려 조용한지도 함께 단언한다.

**범위는 §48과 정확히 같게 뒀다** — 포인터 그 달만 보고, 그 아래 달들은 보지
않는다. 포인터가 지나간 뒤 사라진 이전 달은 Daily 검사가 갖는 것과 **동일한
한계**이며, 여기서만 넓히는 것은 명세의 범위가 아니라 내가 만든 범위가 된다.

**부수 효과 — 파일명 유도가 한 군데로 모였다.** `monthly_history_path()` 신설.
generator 안에 이미 두 번 적혀 있었고, 이제 읽는 쪽도 **쓰는 쪽과 같은 곳**을
본다. 두 번째 구현은 곧 두 번째 의견이다.

**테스트가 고친 fixture 2개.** 기존 테스트 두 개가 "포인터만 있고 파일은 없는"
상태를 정상 fixture로 썼다 — 어떤 실행도 만들 수 없는 상태다. 그중 하나
(`test_a_freshly_consolidated_month_needs_no_attention`)는 이름 그대로 "방금
통합했다"를 표현하려던 것이므로, 통합이 만들어 내는 산출물을 fixture에 넣었다.
약화가 아니라 **정직해진 것**이다.

테스트 8건(`test_observability.py::MonthlyStateConsistencyTests`).

### 8. C27 수정이 덮지 않는 소비자 하나 — 경계를 고정했다

`is_incomplete_write()`를 소비자 6곳에 적용하면서 **일부러 제외한 곳이 하나**
있고, 그 이유를 테스트로 남겼다.

`reporter.local_output.write_event_json()`의 기본 디렉터리는
`runtime/events/incoming/`이고 `Reporter.report_and_write()`가 그것을 그대로
넘긴다. 즉 Desktop 4 reporter — `run_once()` 자신의 주석이 인정하는 경로("the
Desktop 4 reporter and the operator both write `incoming/` directly") — 는
**Collector가 읽는 바로 그 디렉터리**에 staging 파일을 남길 수 있다.

**측정:**

| 형태 | 결과 |
|---|---|
| complete | **ACCEPTED.** Event는 진짜이고 Company History에 정상 반영된다. 파일명만 staging 이름이다 |
| truncated | **REJECTED → `rejected/`**, 그리고 ATTENTION "Collector가 거부한 Event 1건" — 거짓 문장이다(거부된 것이 없다. 쓰기가 중단됐을 뿐) |

**둘 다 유실이 아니고, 둘 다 `incoming/`에 남지 않는다** — 한 번의 실행으로
빠져나간다. 그것이 나머지 6곳과 결정적으로 다른 점이다. 남는 것은 **잘못 이름
붙은 경보 하나**이고, 그것을 고치려면 Collector가 `incoming/`에서 무엇을
소비하는지를 바꿔야 한다 — 읽는 쪽의 필터가 아니라 docs/03의 처리 파이프라인이다.

주장이 아니라 **고정**했다: `run_once()`가 언젠가 이것을 건너뛰기 시작하면
테스트가 깨지고 경계가 의도적으로 재검토된다
(`IncompleteWriteInvariantTests::test_the_one_consumer_this_does_not_cover_and_why`).

### 9. 탐지기 전수 대조 결과 (겨냥 누락 없음 확인)

4번의 질문을 나머지 전부에 돌린 결과다. **추가 누락 없음.**

| 탐지기 | 대상 | 두 번째 대상 |
|---|---|---|
| `stale_lock_cannot_be_cleared` / `lock_held_since` | Runner Lock | **Agent Lock — 누락이었다(4번)**. 저장소 전체에 lock 파일은 이 둘뿐 |
| `check_state_consistency` | daily state ↔ `daily/` | **monthly state ↔ `monthly/` — 누락이었다(7번)** |
| `is_incomplete_write` | 6곳 적용 | Collector는 의도적 제외(8번) |
| `is_readable_event_file` | `incoming/` | Collector가 읽는 디렉터리는 이곳뿐 |
| `_is_parseable_json` | `transport/` | intake가 읽는 디렉터리는 이곳뿐 |
| `_would_reach_the_commit` | Working Copy | git 저장소는 이것 하나 |
| `_split_reviewed` | `review/` | `keep/`에는 사람 검토 개념이 없다 |
| `find_orphaned_events` | processed → candidate | candidate → Daily는 **E-17/BUG-46의 열린 결정**(SKIP) |
| `find_undelivered_events` | sent → sync 폴더 | 이 쌍은 하나뿐 |
| `name_collision` | `incoming/` | `transport/` 쪽은 C27 2·3번이 덮었다 |

**검토 후 추가하지 않은 것:** `backup_state.json`의 `last_backup_commit`을 실제
git 커밋과 대조하는 검사. Working Copy를 운영자가 다시 만드는 것은 docs/08 §30이
허용하는 정상 절차이고, 그 경우 기록된 커밋은 단순히 과거의 것이므로 불일치가
아니다 — 거짓 경보가 된다. F-7/BUG-41이 걸려 있는 영역이기도 하다.

### 10. C27 자신의 수정이 만든 blind spot — 이미 커밋된 잔여물 (신규, **보안/무결성**)

1번의 수정(`_is_in_scope()`에서 `.tmp-*` 제외)은 옳았다. staging 파일이 Company
History로 커밋되는 것을 막았고, **쓰레기를 치우면 삭제 게이트가 영구
BACKUP_FAILED를 내는 함정**을 없앴다.

그런데 제외는 양방향으로 작용한다. `_relative_files()`는 Master와 Working Copy
**둘 다**에 적용되므로, C27 이전 코드가 **이미 동기화하고 커밋해 버린** staging
파일은 이제 양쪽 집합에서 모두 빠진다 — `sync_to_working_copy()`가 그것에 대해
**영원히 아무 말도 하지 않는다.**

**측정**(잘린 하루를 담은 `daily/.tmp-abc123.md`가 이미 커밋된 상태에서 C27
코드 실행):

    sync_to_working_copy()   added=() modified=() deleted=()
    scan_for_secrets(wc)     ()            -- secret 형태가 아니다
    ops_status ATTENTION     []            -- 어디에도 없다

잘린 Company History가 백업 원격에 앉아 있고 흔적이 하나도 없다. **C24·C26이
경고하는 바로 그 모양이고, 이번에 눈이 먼 계측은 이 Sprint 자신의 수정이다.**

**원칙 하나를 명시한다: 나쁜 신호를 없애는 변경은 그 자리에 좋은 신호를 빚진다.**

**수정:** C26이 만든 git-aware probe(`_would_reach_the_commit()`)를 그대로 재사용해
Working Copy의 staging 파일 중 **git이 실제로 커밋 대상으로 들고 있는 것**만
보고한다. `.gitignore`가 덮으면 조용해진다(정말로 나가지 않으므로). Secret 보고와
**독립된 줄**이다 — 조치가 정반대이기 때문이다(자격증명 교체 vs 파일 삭제).

**같은 기준을 이 코드에도 한 번 더 적용했다.** 처음 쓴 스캔은 `rglob("*")`로
Working Copy 전체를 걸어 `.git/`까지 포함했다. 정상 경로에서는 `git ls-files`가
어차피 걸러내므로 무해하지만, **fail-safe 경로에서는 아니다** — git이 없거나
timeout이면 `_would_reach_the_commit()`이 후보를 그대로 돌려주므로 git 내부
파일이 "잔여물"로 보고된다. 이 머신 기준 걷는 파일의 **93%(97개 중 90개)**가
`.git/`이기도 하고, 그 비율은 백업 이력이 쌓일수록 커진다. 제외했다.

테스트 12건(`test_observability.py::CommittedStagingResidueTests`). 그중 하나는
전제를 확인한다(Backup 경로가 정말로 아무 말도 하지 않는가), 하나는 secret 보고와
섞이지 않는지, 하나는 git 저장소가 아닐 때 fail-safe로 과다 보고하는지, 둘은
`.git/`이 정상 경로와 fail-safe 경로 **양쪽에서** 조용한지 본다.

### 11. Agent에는 있고 Runner에는 없던 staleness 검사 (신규)

4번과 **정확히 반대 방향의 같은 비대칭**이다.

`AgentStatusSnapshot.needs_attention()`은 "agent has not run for N day(s)"를
처음부터 갖고 있었다. `_print_last_run()`은 `started_at`을 **출력만 하고 어떤
값과도 비교한 적이 없다.** 그래서 Runner가 그냥 멈추면 — 비밀번호 변경 후
비활성화된 Task Scheduler 작업, 잠든 머신, 삭제된 작업 — LAST RUN 블록이 마지막
SUCCESS를 **영원히 초록색으로** 보여준다.

**둘 중 더 위험한 쪽이 비어 있었다.** Runner는 수집된 Event로 Company History를
조립하고 Daily/Monthly를 닫고 Backup을 push하는 머신이다. 그것이 멈추면 그
전부가 멈춘다.

**측정**(이 머신, 실 runtime): 마지막 실행이 2일 전이었고 ATTENTION에는
"agent has not run for 2 day(s)"가 있었으며 **Runner에 대해서는 한 글자도 없었다.**

**수정:** `SILENT_AFTER_DAYS`를 **재사용**한다 — 새 임계값을 만들지 않았다. 그
상수의 기존 주석이 여기 필요한 근거를 이미 적어 두었다("주말에 꺼져 있는 머신은
이 배포에서 정상이다(docs/07 §58), 매주 월요일에 울리는 임계값은 무시당한다").

메시지는 운영자가 실제로 알아야 할 두 번째 절반까지 적는다: Runner가 안 돈 것이
아니라 **Company History와 Backup이 그동안 진행되지 않았다는 것.**

테스트 9건(`test_observability.py::RunnerHasNotRunTests`). 경계는 리터럴이 아니라
상수에 묶었고, 손상된/naive timestamp에서 뷰가 죽지 않는지도 확인한다.

### 12. 테스트 감사 — 실시간 시계에 의존하던 fixture

11번을 넣자 기존 테스트 하나가 깨졌다. `LastRunViewTests._summary()`는
`started_at`을 **고정 날짜**로 못박는데 `_run()`은 `_print_last_run()`을 인자
없이 불러 **실제 오늘**과 비교하고 있었다. 즉 그 클래스의 단언은 **suite를 어느
날 돌리는지에 의존**하고 있었고, 새 검사가 그 잠복 의존성을 실패로 바꿔 드러냈다.

정정: 그 클래스가 이미 선언해 둔 `NOW`를 넘긴다. 약화가 아니라 **테스트가 자기
시계를 쓰게 만든 것**이다.

**전수 조사.** `tests/`에서 `datetime.now()`/`date.today()`를 쓰는 곳 4개 파일을
전부 확인했다.

| 위치 | 판정 |
|---|---|
| `LastRunViewTests` | **잠복 결함 — 정정** (고정 날짜 vs 실시간) |
| `FutureDatedInStatusViewTests` | 정상 — mtime을 `time.time() + N`으로 만든다(상대) |
| `StuckIncomingInStatusViewTests` | 정상 — timestamp 자체가 없다(달력 무관) |
| `LockAtomicity`(subprocess) | 정상 — 실제 동시성 테스트이므로 실시간이 맞다 |
| `test_daily_history.py` | 이미 고쳐져 있고 이유가 주석에 적혀 있다 |

**같은 조사가 production에서도 하나 찾았다.** 11번을 넣으면서
`_print_last_run()`이 **시계를 두 개** 읽게 됐다 — staleness는 `now` 인자,
Runner Lock 보유 시간은 `datetime.now()`. 운영에서는 둘 다 실제 now라 무해하지만,
한 함수가 두 기준으로 두 나이를 보고하는 것은 방금 fixture에서 발견한 바로 그
함정이다. 하나로 통일했다.

### 13. 성능 감사 — 옳은 결론이 틀린 근거를 딛고 있었다 (신규, **코드 변경 없음**)

C27이 `incoming/`에 추가한 읽기(`is_readable_event_file`)의 비용을 재려고
`_attribute()`와 대조했더니 **스레드 풀이 16배 느리다**는 결과가 나왔다. 그대로
믿었다면 풀을 제거했을 것이다.

순서를 통제해 다시 쟀다 — 순서마다 **새 파일 집합**, 양방향.

| 20,000건 | cold | warm |
|---|---|---|
| 직렬 | **8.897초** | 0.942초 |
| 스레드 16 | **2.104초** | 1.119초 |

**나중에 도는 쪽이 항상 이긴다.** 지배 비용은 스레딩도 JSON 파싱도 아니라
**cold cache의 첫 파일 열기**다.

- **cold ↔ cold: 스레드가 4.2배 빠르다.** 운영상 실제 경우가 이쪽이다 —
  `ops_status.py`는 이전 실행이 쓴 파일을 몇 시간 뒤에 읽고 `processed/`는 몇
  달에 걸쳐 쌓인다. **풀은 옳다. 코드는 바뀌지 않는다.**
- warm ↔ warm: 직렬이 1.19배 빠르다(풀 오버헤드). 최적화할 가치가 없는 경우다.

**틀린 것은 D절의 기존 표였다.** 그 표의 직렬 열(1,000→5.2초, 20,000→107.1초)은
cold 직렬을, 그 직렬이 데워 놓은 warm 스레드와 비교한 값이다. 같은 편향
방향으로 여기서 8배가 나온다 — 즉 기록된 "6배"는 부풀려졌고 진짜 값은 4배다.
`_READ_WORKERS` 주석 3곳(`app/desktop_activity.py`·`history/reconciliation.py`·
`agent/delivery.py`)이 같은 수치를 인용하고 있어 전부 정정했다.

**C13의 성능 판본이다.** C13은 *"'불가능함이 확인됐다'로 기록된 측정은 근거보다
오래 살아남는다"*였다. 이번 것은 한 걸음 더 나쁘다 — **결론은 맞았다.** 스레드
풀은 정말로 옳다. 다만 그것을 지탱하던 숫자가 틀렸고, 그 숫자를 검증하지 않은
채 옆에 새 코드를 놓으면 **다음 판단이 틀린다.** 실제로 이번에 그럴 뻔했다.

**교훈:** 결론이 맞다고 근거를 면제하지 않는다. 그리고 파일 I/O 벤치마크는
**순서를 통제하지 않으면 아무것도 측정하지 않는다.**

### 14. 문서 감사 — 운영 가이드가 도구의 절반만 설명하고 있었다 (신규)

`AGENT.md` §6은 `ops_status.py`가 **"두 가지"**를 보여준다며 COMPANY와 AGENT를
설명한다. 실제로는 **네 블록**(COMPANY / HISTORY / LAST RUN / AGENT)을 출력하고,
설명되지 않은 두 블록에 **State↔산출물 정합성 검사, Backup Working Copy 경고,
Run Manifest, Lock 검사 둘**이 전부 들어 있다. 가이드만 읽은 운영자는 진단의
대부분이 존재한다는 사실 자체를 모른다.

드리프트는 사고가 아니라 **예정된 결과**였다 — 이번 Sprint만 해도 HISTORY와
LAST RUN에 줄을 여러 개 추가했고, 가이드와 도구를 잇는 것은 아무것도 없었다.

**수정:** §6을 실제 출력에 맞췄다(네 블록 + ATTENTION의 편성 원칙).

**그리고 다시 벌어지지 않게 했다.** `OperatorGuideMatchesTheToolTests` 3건 —
도구가 출력하는 **모든 블록 제목이 가이드에 등장하는지**, 그 블록 목록이
**테스트가 아니라 프로그램에서** 나오는지, 문서에 적힌 종료 코드가 실제
`main()`이 반환하는 것과 같은지.

의도적으로 좁다. 문구·순서·서술의 완전성은 보지 않는다 — 텍스트를 고정하는
테스트는 편집마다 깨져서 곧 지워진다. 가이드가 **한 번도 들어본 적 없는 블록**이
생겼을 때만 깨지고, 그것이 이번에 실제로 일어난 드리프트다.

**테스트가 즉시 값을 했다.** 처음 쓴 블록 목록에서 `ATTENTION`이 빠져 있었고,
"목록은 프로그램에서 나와야 한다"는 두 번째 테스트가 그것을 잡았다.

E-11(traceability — 저장소가 "고쳤다"는 기록을 검증하지 않는다)의 축소판을 한
쌍에 대해 닫은 것이다. E-11 자체는 형식 결정이 필요해 **여전히 SKIP**이지만,
문서-프로그램 한 쌍을 잇는 데는 승인이 필요 없었다.

### 15. `run_agent.py`의 Exit Code를 아무것도 고정하지 않았다 (신규)

**같은 비대칭의 세 번째 사례다**(Lock 감시, staleness에 이어).

`ExitCodeContractTests`가 `run_company_ops.py`에 존재하는 이유는 BUG-36이
확정했다: Runner는 Windows Task Scheduler가 띄우고, **stdout은 기본적으로
캡처되지 않으며**, 따라서 exit code가 유일한 자동 건강 신호다. 그 문장은
`run_agent.py`에 **한 글자도 다르지 않게** 적용된다 — 같은 Task Scheduler,
같은 미캡처 stdout, `install_agent_task.ps1`이 Desktop 1~3에 등록한다. 그리고
그 Desktop들이 Company History를 **생산하는** 쪽이다.

그런데 아무것도 테스트하지 않았다. 스위트의 모든 단언은 in-process enum인
`AgentStatus`에 대한 것이고, **그 enum에서 OS가 보는 숫자로 가는 사상은 어느
방향으로도 테스트가 없었다.**

**고정한 것**(스크립트 자신의 docstring이 규정하는 사상):

    0   COMPLETED, 또는 다른 Agent 실행이 lock을 쥐어 skip
    1   설정 오류(환경변수/state)
    2   FAILED — 유실 없음. outbox가 일감을 들고 있고 다음 실행이 같은 날짜부터

테스트 7건(`test_architecture_invariants.py::AgentExitCodeContractTests`).
`main()`의 실제 `return`을 AST로 뽑아 **모듈 docstring**과 **AGENT.md** 양쪽과
대조한다 — 문서 둘과 프로그램 하나가 서로 어긋나지 않게. 그리고 `3`이 계속
쓰이지 않는지 단언한다: docs/14가 그 숫자에 `run_company_ops.py`·`ops_status.py`
공통의 뜻("사람이 봐야 한다")을 부여했고, Agent가 그것을 반환하기 시작하면 그
뜻이 조용히 바뀐다.

Lock skip이 **0**이라는 것도 명시적으로 고정했다 — 4번(Agent Lock 보고)이
성립하는 근거가 바로 그것이기 때문이다. stale lock이 Task Scheduler에 보이지
않는 이유가 이 0이다.

### 16. docs/14 §7의 Exit Code 표를 구현과 대조한 적이 없다 (신규)

docs/14는 **명세**다(README §13에서 BACKLOG보다 우선). §7의 표가 exit code 계약
전부이고, `runsummary._EXIT_CODES`가 그 표의 Python 판본이다. **둘을 비교한
것은 아무것도 없었다.**

docs/14는 스위트 곳곳 docstring에서 인용되지만, 파일을 실제로 여는 테스트는
`test_repository_hygiene.py`의 "이 **파일명**이 목록에 있는가" 하나뿐이었다.

즉 숫자가 **양방향 어느 쪽으로든** 드리프트해도 아무것도 깨지지 않는다.
`_EXIT_CODES`를 고치면 명세가 프로그램이 더 이상 지키지 않는 계약을 서술하게
되고, 표를 고치면 명세가 바뀐 적 없는 프로그램에 대해 거짓말을 하게 된다.
Task Scheduler는 그 숫자 하나만 읽으므로 "어느 문서가 맞는가"는 한가한 질문이
아니다.

**사상을 여기에 다시 적지 않고 명세에서 파싱한다.** `{SUCCESS: 0, DEGRADED: 3,
FAILED: 2}`를 하드코딩하면 **세 번째 사본**이 생길 뿐 나머지 둘에 대해서는
아무 증거도 주지 못한다.

테스트 5건(`test_spec_conformance.py::RunContractSpecTableTests`). 첫 번째는
**표가 여전히 파싱되는지**를 본다 — 표 형식이 바뀌어 정규식이 0건을 잡으면 아래
단언들이 전부 공허하게 통과하기 때문이다. 나머지는 모든 `OverallStatus`가 명세에
등장하는지, 각각이 명세가 적은 코드로 사상되는지, `1`(설정 오류)이 어떤 상태에도
사상되지 않는지, `3`에 대해 명세와 두 진입점이 **서로가 아니라 명세를 기준으로**
일치하는지를 본다.

### 17. "이 머신에 있는 것이 정말 이 머신 밖에도 있는가" — 아무도 묻지 않았다 (신규, **데이터 안전**)

`backup_state.json`의 `last_successful_backup`은 Backup 단계가 만들어진 이래
계속 기록돼 왔고 **production 코드가 한 번도 읽지 않았다.** 그리고 스위트가
그것을 이미 알고 있었다 — BUG-55 특성화 테스트의 문장 그대로:

> *"The one artifact that would betray it is `last_successful_backup` never
> advancing, **which nothing surfaces.**"*

**그 대가가 BUG-55다.** `working_copy._is_in_scope()`는 `parts[0]`을
`{"daily","monthly"}`와 **대소문자 구분**해 비교하는데, docs/11의 배포 절차는
사람이 디렉터리를 만들게 한다. 대소문자를 접는 파일시스템에서 `Daily/`는 그
비교를 제외한 **모든 것에게 같은 디렉터리**다.

**실측**(실 bare remote, 3회 연속):

    run 1..3    BACKUP_NOT_REQUIRED, changed=()
    remote      비어 있음
    state       last_successful_backup = None
    ops_status  "daily 파일: 1", ATTENTION 없음

Company History 하루가 **한 대의 머신에만** 있고 모든 지표가 초록이다. 이 뷰는
심지어 그 파일을 세고 있었다 — `glob()`은 대소문자를 접고 scope 검사는 접지
않기 때문이다.

**시계 임계값은 틀린 도구였을 것이다.** 바뀌지 않은 History는 백업할 필요가
없으므로 "마지막 백업이 N일 전"은 조용한 한 주에 정상이고, 곧 지워지지 않는
경보가 된다. 절대 정상일 수 없는 조건은 **마지막 성공 백업보다 새로운 History가
있다**이다 — 아무것도 쓰이지 않는 동안에는 발화할 수 없고, 백업이 성공하는
순간 사라진다.

**실측한 false-alarm 프로파일**(실제 Backup runner):

| 상태 | 경보 |
|---|---|
| 백업 전 (History만 존재) | 1건 — 맞다. 아직 이 머신 밖에 없다 |
| 백업 성공 직후 | **0건** |
| 조용한 주(`BACKUP_NOT_REQUIRED`) ×3 | **0건** ← 임계값 방식이 틀렸을 경우 |
| 새 History, 아직 백업 안 됨 | 1건 — 맞다. 다음 백업이 지운다 |
| 그 다음 백업 성공 후 | **0건** |
| `.tmp-` staging 파일만 새로 생김 | **0건**(C27: 끝나지 않은 쓰기는 History가 아니다) |

**scope 술어를 일부러 재사용하지 않았다.** `_is_in_scope()`를 쓰면 BUG-55를
만드는 바로 그 대소문자 구분을 물려받아, **이 검사가 존재하는 이유인 결함에
눈이 먼다.** 확장자로 Local Master 전체를 훑는다.

탐지만 한다. scope 비교를 case-fold하는 것은 BUG-55 자신의 열린 결정이다(백업이
덮는 파일 집합이 바뀐다). 여기서는 보고하고 **파일명을 댄다** — 운영자가 잘못된
대소문자 디렉터리를 보는 것은 그 문장을 통해서다.

테스트 9건(`test_observability.py::UnbackedCompanyHistoryTests`), 전부 실
bare remote와 실 Backup runner를 쓴다.

**fixture 2개를 다시 정정했다.** 둘 다 Company History를 만들고 **백업 이력을
전혀 두지 않은** 상태를 "정상"으로 서술하고 있었다 — Backup은 같은 파이프라인의
단계이고 실패 시에도 state를 쓰므로, 어떤 실행도 만들 수 없는 상태다.

그 정정 과정에서 **같은 함정을 세 번째로 밟았다.** 헬퍼가 백업 시각을
`timespec="seconds"`로 잘라 같은 초에 쓰인 파일보다 **앞서게** 만들었고, 이
경보가 재현됐다. `backup/state.py`는 `.isoformat()`을 timespec 없이 쓰므로
**production에는 그 창이 없다** — 확인하고 헬퍼를 production과 같게 맞췄다.
시각 두 개를 비교할 때 정밀도와 시계 기준을 맞추는 것은 이 Sprint에서만 세 번
문제가 됐다(§12, §14, 여기).

### 18. 시각 비교 전수 조사 — 결함 1건, 나머지는 방어 확인

이 Sprint에서만 시각 비교가 **세 번** 문제를 냈다(§12 실시간 fixture, §14 한
함수 안의 시계 둘, §17 초 단위 truncation). 세 번은 우연이 아니므로 저장소
전체를 훑었다. 두 가지 위험을 각각 본다: **naive/aware 혼용**(TypeError)과
**정밀도 절단**(순서 뒤집힘).

**쓰는 쪽**(`isoformat(timespec="seconds")` 18곳)과 **비교하는 쪽**을 대조한
결과다.

| 비교 지점 | naive/aware | 정밀도 | 판정 |
|---|---|---|---|
| `ops_status` Runner staleness | 가드 있음 | 3일 임계값 vs 1초 절단 | ✔ |
| `ops_status` Runner Lock 보유 시간 | 가드 있음 | 2시간 임계값 vs 1초 | ✔ |
| `ops_status` Agent Lock 보유 시간 | 가드 있음 | 2시간 임계값 vs 1초 | ✔ |
| `ops_status` 미백업 History(§17) | 가드 있음 | **양쪽 전정밀도** | ✔ |
| `agent/status.days_since_last_run` | 가드 있음 | 일 단위 | ✔ |
| `desktop_activity._before()` | **C21이 수정** — 파싱 실패/혼용 시 문자열 비교로 폴백 | — | ✔ |
| `desktop_activity` future-dated mtime | float ↔ float | — | ✔ |
| `transport.intake._is_stable` | float ↔ float | — | ✔ |
| `monthly._existing_generated_at` | 문자열로 이월만, 비교 없음 | — | ✔ |
| `daily/late_events` `Last Updated At` | 다시 쓰기만, 비교 없음 | — | ✔ |
| **`notion/sync` Late Event Guard** | E-19(기존 SKIP) | **`<=` + 초 단위 기본 timestamp** | **E-23 신규** |

**하나만 걸렸고, 그것이 E-23이다.** 나머지는 전부 임계값이 절단 폭보다 몇
자릿수 크거나(시간·일 단위), 애초에 비교를 하지 않거나, 이미 가드가 있다.

**인접 방어도 확인했다.** `agent/status.py`는 `fromisoformat`을 `ValueError`만
잡는데 `TypeError`도 가능한 함수다 — 그러나 `agent/state.load_state()`가
`last_run`을 str-or-None으로 강제하고 `read_status()`가 `AgentStateError`를
`state_error`로 잡으므로 **도달 불가**다. 방어를 덧붙이지 않았다(C7: 도달하지
않는 코드는 넣지 않는다).

**이 조사가 남기는 것:** 같은 조사를 다시 할 필요가 없다는 근거. 그리고 새
시각 비교를 넣을 때 물어야 할 두 질문이 표로 남았다 — **임계값이 절단 폭보다
충분히 큰가**, **두 값이 같은 시계에서 왔는가.**

### 19. Release / Production Readiness Audit — 게이트를 가정하지 않고 실행했다

docs/11 §101 Release Environment Check의 5개 항목을 **실제로 돌렸다.** 이
게이트는 문서에만 있고 실행된 기록이 없었다.

| 항목 | 결과 |
|---|---|
| `python --version` | PASS (3.13.14) |
| `python -m pytest` (**경로 없이**, 문서 그대로) | PASS — 1811 passed / 0 failed |
| `python -m compileall src` | PASS |
| `python -m src.app.runner` | PASS (exit 0) |
| `python -c "import src.app.runner"` | PASS (exit 0) |

**5/5 PASS.** 두 가지를 확인했다.

**항목 4·5가 왜 통과하는가.** `src/`에는 `__init__.py`가 없고 모듈들은 절대
import(`from events import ...`)를 쓴다. Root에서는 `src/`가 sys.path에 없으므로
실패해야 할 것처럼 보이지만, `src/app/runner.py:51`이
`sys.path.insert(0, .../src)`로 스스로 부트스트랩한다. **우연이 아니라 코드가
그렇게 되어 있다.**

**항목 4가 파이프라인을 실행하지 않는다.** `runner.py`에는 `__main__` 가드가
없으므로 `python -m`은 import만 하고 끝난다. **릴리스 점검이 실제 Runner를
발화시키지 않는다** — 이것이 확인되어야 할 성질이었다(그 반대였다면 릴리스
점검이 Backup과 Notion을 건드린다). 다만 그 결과 항목 4와 5는 사실상 같은 것을
검사한다.

**항목 2를 문서 그대로 실행한 것도 처음이다.** 이 Sprint 내내 `python -m pytest
tests`로 돌렸는데, 문서는 경로 없이 적는다. Root에서 bare로 돌리면
`runtime/`·`v/`까지 수집 범위에 들어갈 수 있으므로 확인이 필요했다 — 동일하게
1811건을 수집하고 통과한다.

### 20. `ops_status.py` 전체 명령 실측 (C27 변경의 자기 검증)

이 Sprint는 이 뷰에 스캔을 넷 추가했다(미백업 History, Working Copy 잔여물,
`incoming/` 판독 가능성, Monthly 정합성). "가장 먼저 실행하는 뷰"이므로 조각이
아니라 **명령 전체**를 쟀다.

| processed | daily | review | transport | `main()` |
|---|---|---|---|---|
| 30 | 30 | 0 | 0 | **0.045초** |
| 500 | 180 | 20 | 10 | 0.559초 |
| 1,000 | 365 | 50 | 50 | 0.918초 |
| 5,000 | 365 | 200 | 200 | 5.426초 |
| 10,000 | 730 | 500 | 500 | 11.426초 |

**현재 운영 규모(수십 건)에서 0.045초.** 큰 수치는 전부 기존의 `processed/`
스캔이 지배하며(D절에 이미 기록된 비용), 이번에 더한 것은 그 안에서 보이지
않는다 — 신규 스캔 중 가장 무거운 `_history_newer_than_the_last_backup()`을
**730일치(2년) Company History**에 대해 따로 재면 **21.4 ms**, 10,000건 전체
11.4초의 **0.19%**다.

**결론: 이번 Sprint는 이 뷰에 성능 회귀를 넣지 않았다.** 추측이 아니라 측정이다.

### 21. 환경 감사 — C13의 수정이 여전히 고정돼 있는가

`scripts/install_agent_task.ps1`의 트리거 스코프는 C13이 찾은 실제 결함이었다
(`-User` 없는 `-AtLogOn`은 어떤 비관리자 머신에서도 등록에 성공한 적이 없었고,
게다가 아무 사용자나 로그온할 때 발화했다).

확인: `test_install_agent_task_script.py`가
`"New-ScheduledTaskTrigger -AtLogOn -User $currentUser"`를 **문자열 그대로**
고정한다. AGENT.md §2b도 같은 사실을 서술한다("관리자 권한은 필요 없다 …
실제 원인은 트리거에 `-User`가 빠진 것"). **문서·스크립트·테스트 셋이 일치한다.**

같은 파일의 `test_it_registers_a_logon_trigger`는 `-AtLogOn`까지만 보므로
`-User`가 빠져도 통과하지만, 위 단언이 그 구멍을 덮는다. 중복 제거는 하지
않았다 — 약한 단언이 강한 단언 옆에 있는 것은 결함이 아니다.

### 22. 이번 Sprint가 확인한 것

C26의 질문은 "이 경고가 뜬 뒤 올바른 조치를 취하면 사라지는가"였다. C27이 더한
질문은 그 앞이다: **"이 뷰의 수치는 그 단계가 실제로 하는 일과 같은 답인가?"**

열두 건 모두 **코드가 이미 정답을 갖고 있었다.** `.tmp-` 접두사는 쓰는 쪽
15곳이 선언해 뒀고, `skipped_already_present`는 intake가 매 실행 반환하고
있었고, `stale_lock_cannot_be_cleared()`는 C23이 만들어 둔 채 Runner Lock에만
겨눠져 있었고, `read_text` 실패는 Collector가 매 실행 FAILED로 기록하고 있었다.
그리고 모든 단계의 `metrics`는 매 실행 Manifest에 기록되고 있었고,
§48 정합성 검사는 `scheduler/consistency.py`에 구현된 채 Daily에만 겨눠져
있었고, `started_at`은 매 실행 Manifest에 적히면서 어떤 값과도 비교된 적이
없었다. 읽는 쪽이 그 답을 **묻지 않았을 뿐이다.**

10번은 그 규칙의 대가를 보여준다: **나쁜 신호를 없애는 변경은 그 자리에
좋은 신호를 빚진다.** C27의 수정이 삭제 게이트 함정을 없애면서 이미 커밋된
잔여물의 마지막 흔적까지 지웠고, 그것을 발견한 것은 이 Sprint가 자기
변경에 C24·C26의 기준을 다시 적용했기 때문이다.

그리고 3번과 5번은 그 교정 자체의 함정을 보여준다.

- **3번**(수치 하나를 조용하게 만들 때): 그 수치가 조용해지면 **안 되는** 경우를
  먼저 분리해야 한다. 그러지 않으면 거짓 경보 하나를 없애고 누락 경보 하나를
  만드는 교환이 된다.
- **5번**(어느 술어로 물을 것인가): "읽을 수 없다"의 답은 단계마다 다르다.
  옆 단계의 술어를 빌려 오면 고치려던 어긋남을 **방향만 바꿔** 재생산한다.

4·9·15번은 다른 종류다 — 결함이 아니라 **적용 범위의 누락**이었고, 셋 다 같은
방향이다: Runner에 세운 규율(Lock 감시 / 계약 테스트)과 Agent에 세운 규율
(staleness)이 서로에게 적용된 적이 없었다. 두 진입점은 **같은 Task Scheduler**
에서 **같은 미캡처 stdout**으로 도는데, 한쪽에서 배운 것이 다른 쪽으로 건너간
적이 없다. 새 계측을 만들 때의 질문이 하나 더 늘었다: **이 검사가 적용되어야
할 곳이 여기 하나뿐인가?**

### 이번 Sprint에서 하지 않은 것

- **`.tmp-*` 잔여물의 자동 삭제.** 살아 있는 쓰기와 구분할 수 없다(A-7과 같은
  종류의 결정). 세고, 이름을 대고, 지워도 안전하다고 말하는 데서 멈춘다.
- **`safe_event_filename()`의 대소문자 충돌 방지.** 파일명 유도 계약 변경.
  E-22로 기록.
- **`scan_for_secrets()`의 `.tmp-` 제외.** fail-safe 방향이 반대다.
- **`name_collision`(BUG-43)을 `awaiting_collection`에서 빼는 것.** 5번과 같은
  모양으로 보이지만 아니다 — 그쪽은 "이미 처리됨"의 두 개념을 화해시키는 **열린
  결정**에 걸려 있고(F-10), 디코딩 실패에는 결정할 것이 없다.
- **Agent Run Manifest.** Runner에는 있고 Agent에는 없다(4번을 조사하며 확인).
  새 산출물이므로 docs/14의 Run Contract 범위 변경이다. 오늘의 대체물은
  `agent_state.last_run` + `agent.log` 두 개다.

---

## C26. False-Alarm Sprint

C25가 다루지 않은 E-21 각도 하나 — **ignored file** — 를 보다가, 이번 Sprint의
결함이 나왔다. 남의 코드가 아니라 **C24에서 내가 넣은 탐지**의 결함이다.

### 1. C24 탐지가 올바르게 설정된 머신에 영구 거짓 경보를 낸다

C24는 "Working Copy에 Secret 형태의 파일이 있다"를 ATTENTION에 올렸고, 근거는
`scan_for_secrets()`였다. 그 술어는 **"이 파일명이 secret 형태인가"**에 답한다 —
Backup 게이트에는 맞는 질문이고 이 보고에는 **틀린 질문**이다. 원격에 실제로
가는 것은 `git add -A`가 stage하는 것이고, git은 `.gitignore`가 시키는 대로
무시한다.

**측정:**

| Working Copy 상태 | git이 원격에 올리는가 | C24 경고 |
|---|---|---|
| `.gitignore` 없음 + `.env` | **예** | 예 (정확) |
| **`.gitignore` 있음(docs/08 §28) + `.env`** | **아니오** | **예 (거짓)** |

docs/08 §28은 Backup Repo가 `.env` / `.env.*` / `*.tmp` / `*.log`를 담은
`.gitignore`를 갖도록 요구한다. 즉 **운영자가 올바른 조치를 취한 바로 그
상태에서** 경고가 매 실행 뜨고, 어떤 행동으로도 지워지지 않는다. 게다가 문구는
"이 파일들은 ... 원격에 올라간다"고 **사실과 다른 말**을 한다.

이것은 이 프로젝트가 반복해서 경고하는 실패 모드다 — `IntakeBacklog` 자신의
docstring: "지워지지 않는 경고는 없는 것보다 나쁘다 ... 영구 항목은 사람들이
그 섹션을 대충 넘기도록 훈련시킨다." **C24의 계측이 그것을 만들었다.**

### 2. 수정 — 규칙을 다시 구현하지 않고 git에게 묻는다

`_would_reach_the_commit()` 신설. `git ls-files -c -o --exclude-standard`는
추적 중인 것 + 추적되지 않았고 무시되지도 않은 것을 준다 — `add -A`가 도달할
집합 그 자체다.

`.gitignore`를 직접 파싱하지 않은 이유는 이 저장소가 반복해 세운 원칙이다:
**보고하는 뷰가 보고 대상 단계와 다른 답을 내면 안 된다**(`_count_transport`가
intake 자신의 parse 술어를 재사용하는 것과 같은 이유). git의 규칙을 두 번째로
구현하는 것이 바로 그 불일치다.

**Fail-safe 방향:** git이 없거나·저장소가 아니거나·timeout이면 후보를 **그대로
반환**한다 — 답할 수 없는 probe는 숨기지 않고 과다 보고한다. 그리고 검사할
것이 있을 때만 실행하므로 정상(깨끗한) 경우는 subprocess 비용이 0이다.

**`git_ops.py`를 건드리지 않았다.** 그 모듈의 명령 집합은
`test_spec_conformance.py::test_git_ops_runs_only_the_approved_command_set`이
닫아 둔 목록이고, 그 테스트 자신이 "새 git 명령 추가는 심의된 행위"라고 적는다.
새 probe는 뷰 계층(`ops_status.py`)에 두었다.

**수정 후 측정:**

| 상태 | 경고 |
|---|---|
| `.gitignore` 있음 + `.env`만 | 없음 |
| `.gitignore` 있음 + `notes/id_rsa` | `id_rsa`만 |
| `.gitignore` 없음 + `.env` | `.env` |
| 이미 추적 중인 `.env` + 나중에 `.gitignore` 추가 | `.env` (git은 추적 중인 파일을 계속 커밋한다) |
| Working Copy가 git 저장소가 아님 | 전부 (fail-safe) |

마지막 두 줄이 중요하다. 추적 중인 파일은 `.gitignore`가 뭐라 하든 커밋되므로
**규칙 파일이 아니라 git을 따라야** 정확하다. 그리고 fail-safe 경로는 C24·C25가
만든 기존 테스트들이 실제로 지나가는 경로다(그 fixture들은 `git init`을 하지
않는다) — 그래서 명시적으로 테스트했다.

테스트 10건(`test_observability.py::GitignoredWorkingCopyFileTests`,
`::GitAwareProbeShapeTests`).

### 3. 같은 기준을 22개 ATTENTION 전체에 적용 — 두 번째 거짓 경보

C26의 기준("올바른 조치를 취하면 사라지는가?")을 `ops_status.py`의 ATTENTION
22개 전체에 돌렸다. 하나가 더 걸렸고, 그것도 이 프로젝트가 스스로 넣은 것이다.

**L349 검토 대기 Candidate (C22).** 운영자가 `review_cli.py`를 돌려
Decision Context를 채우는 것 — 문서가 정한 올바른 조치 — 을 해도 경고가
그대로다. `submit_review()`는 `filter_result`를 건드리지 않으므로 파일이
`review/`를 떠나지 않기 때문이다(E-20에 승격 경로가 없다고 기록돼 있다).
실측으로 확인했다.

두 가지 다른 것이 하나로 보고되고 있었다:

    미검토   사람을 기다리는 일 — 하면 사라진다
    검토됨   E-20의 열린 결정 — 오늘 어떤 조치로도 사라지지 않으므로
             경고가 아니라 블록에 적을 사실이다

**수정:** `_split_reviewed()` — 저장된 Candidate에서 Decision Context 필드가
하나라도 채워졌는지 읽어 나눈다(`submit_review()`가 남기는 흔적 그대로,
별도 추적 상태 없음). ATTENTION은 **미검토분에만** 뜨고, 총계와 내역은 블록에
그대로 남아 docs/05 §50의 신호("REVIEW가 너무 많다")를 유지한다.

`FileHistoryRepository.list()`는 일부러 쓰지 않았다 — 손상된 후보 하나에
raise하므로(BUG-38) 상태 뷰 전체가 죽는다. 읽지 못한 파일은 "미검토"로 센다
(어느 쪽이든 사람이 필요하다).

테스트 7건(`test_history_review.py::ReviewAlertClearsWhenTheWorkIsDoneTests`).

### 4. 이번 Sprint가 확인한 것

C25가 "작성했다 ≠ 동작한다는 방금 쓴 코드에도 적용된다"를 미실행 분기로
확인했다면, C26은 그 다음 단계다 — **실행되는 코드도 틀린 말을 할 수 있다.**
C24의 탐지는 실행됐고 테스트도 통과했지만, 그 테스트들이 전부 `git init`을
하지 않은 fixture를 썼기 때문에 정확성이 검증된 적이 없었다.

새 계측을 추가할 때 물어야 할 질문이 하나 늘었다: **이 경고가 뜬 뒤, 올바른
조치를 취하면 사라지는가?**

### 전체 Regression

**1696 passed, 4 skipped, 976 subtests, 0 failed.**
compileall / `git diff --check` / hygiene / secret scan 통과.

---

## C25. Delivery-Gate Sweep

C24가 찾은 E-21을 중심으로, 아직 보지 않은 경로 — 삭제·rename·부분 실패·
junction — 를 실제 git과 로컬 remote로 끝까지 돌렸다. **신규 결함은 없었다.**
대신 E-21의 성격이 두 방향으로 바뀌었다: 한쪽으로는 더 나쁘고, 다른 쪽으로는
생각보다 낫다.

### 1. 명세대로 동작한 것 (신규 결함 없음)

| 시나리오 | 결과 |
|---|---|
| in-scope secret(`daily/.env`) | 게이트가 정확히 차단(`BACKUP_FAILED`), Working Copy 깨끗, Master에서 지우면 SUCCESS 복귀 |
| Master에서 rename | `BACKUP_FAILED` + `deleted` 보고 — docs/08 §43-47 삭제 게이트가 의도대로 |
| 이미 커밋된 파일을 Master에서 제거 | `BACKUP_FAILED` + `deleted` 보고, Working Copy·원격 사본 유지 — §43-47대로 |

### 2. E-21이 더 나쁜 쪽: 실패가 유출을 막지 못하고 **미룬다**

`backup.run_once()`는 add → commit → push를 한 `try` 안에서 돌린다. 따라서
**push 실패는 commit 이후에** 일어난다.

    run 1 (remote 끊김)   GitOperationError   로컬 커밋에 .env / 원격에는 없음
    run 2 (remote 복구)   BACKUP_SUCCESS      원격에 .env

즉 "실패한 백업"이 이미 stray를 영구히 로컬 히스토리에 넣었고, 다음 성공 실행이
그것을 내보낸다. 실패는 유출을 **지연**시킬 뿐이다.

운영자가 그 사이에 파일을 지워도 tip에서만 사라진다 — 커밋은 히스토리에 남는다
(테스트로 고정).

### 3. E-21이 나은 쪽: 그 지연이 곧 **창(window)**이다

C24는 탐지를 "사후라 약하다"고 적었다. 절반만 맞았다. 실패 경로에서는
`ops_status.py`가 **push보다 먼저** 이름까지 대며 경고한다 — 그리고 무인
배포에서 가장 흔한 실패(원격 도달 불가)가 정확히 그 창을 만든다.

측정: run 1 실패 직후 `ops_status`가
`Backup Working Copy에 Secret 형태의 파일 1건: .env`를 냈고, 그 시점에 원격에는
아직 없었다.

### 4. Junction 경유 — 같은 가족, 탐지는 덮는다

Working Copy 안의 directory junction은 `git add -A`가 따라가 반대편 내용을
커밋한다(실측: 원격에 `linked/.env`, `BACKUP_SUCCESS`). E-21의 또 다른 경로일
뿐 새 결함은 아니다.

**탐지는 이 경로도 잡는다.** `scan_for_secrets()`가 `rglob`으로 걷고 rglob은
junction으로 내려가기 때문이다 — A-19가 Master 쪽 **위험**으로 기록한 바로 그
동작이 여기서는 유리하게 작용한다. 두 항목이 같은 동작에 반대 방향으로 걸려
있다는 사실을 양쪽에서 고정해 두었다.

### 5. 여전히 못 막는 것

이름이 `_SECRET_EXACT_NAMES`에 없는 secret(`secrets.yaml` 등 — BUG-7의 알려진
한계)은 게이트도 탐지도 통과한다. 실측: 원격에 나가고, 운영자가 Master에서
지우면 삭제 게이트가 `BACKUP_FAILED`를 내며 Working Copy와 원격의 사본은 그대로
남는다. 이름 목록을 넓히는 것은 E-15/E-21과 같은 결정이다.

### 6. 자기 감사 — 내가 추가한 방어 분기가 실행된 적이 없었다

C22의 미실행 줄 추적을 **C19~C25가 추가한 코드**에 돌렸다. 두 개가 0회 실행
이었다.

| 지점 | 무엇을 막는가 | 상태 |
|---|---|---|
| `_count_transport`의 `except OSError` (C24) | `run_intake()`가 `transport/`에서 파일을 **옮기는 중에** `ops_status.py`가 목록을 읽는 경쟁 — 이 모듈의 계약이 "Runner가 도는 중에도 안전"이다 | 테스트 추가, 동작 확인 |
| `drain_pending`의 save 실패 사유 (C19) | 재시도가 **이미 일어난 뒤** 큐 파일 기록에 실패하는 경우 | 테스트 추가, 동작 확인 |

두 번째에서 **테스트가 잘못된 경로로 통과하는 것**을 잡았다. 큐 파일을
디렉터리로 바꾸는 방식은 `load_pending()`이 먼저 실패해 재시도 루프에 닿지
못하므로, 겉보기 문자열은 맞지만 **손상 분기**를 지나간다. `save_all`을
교체해 정직하게 도달하도록 고치고, 원래 경로도 별도 테스트로 남겨 둘이
구분되게 했다.

덤으로 C22의 로더 거부 분기 스윕이 `dashboard_pending`을 빠뜨린 것도 발견해
채웠다(같은 파일 형태·같은 계약을 가진 두 큐 중 하나만 덮여 있었다).

교훈은 C17과 같은 방향이다 — **"작성했다"와 "동작한다"는 다른 주장이고,
그것은 내가 방금 쓴 코드에도 적용된다.**

### 7. 이번 Sprint의 판단

정책·명령 집합·게이트 범위는 **하나도 건드리지 않았다.** C24가 추가한 탐지의
실제 커버리지를 측정해 두 가지를 확정했을 뿐이다 — 실패 경로에서는 유효하고,
junction 경유도 덮으며, 이름 목록 밖은 못 덮는다.

### 전체 Regression

**1679 passed, 4 skipped, 972 subtests, 0 failed.**
compileall / `git diff --check` / hygiene / secret scan 통과.

---

## C24. Mis-pointed Gate Sprint

C23이 세운 "제3의 길"(동작 vs 가시성)을 남은 F 항목에 계속 적용하다가,
문서 감사로 넘어간 지점에서 **신규 보안 결함**이 나왔다.

### 1. BUG-43 — 가시성만 닫았다

`processed/`에 이미 있는 이름이 `incoming/`에 다시 나타나면 Collector가 매
실행 실패하고 파일은 계속 `incoming/`에 남는다. 실측 3회: `accepted=0
failed=1`, 매번 동일.

`ops_status.py`는 `incoming=1`을 정확히 보고했고 **이유는 말하지 않았다.**
BUG-43 docstring이 "at least visible"이라 적은 두 근거를 재측정해 둘 다 약함을
확인했다 — `collector_summary.failed`는 Task Scheduler가 캡처하지 않는
stdout으로 가고, Manifest에는 들어가지만 **SUCCESS component의 metric**이라
`_print_last_run()`이 출력하지 않는다. ("exit code는 여전히 0"이라는 문장도
반만 맞다 — BUG-36은 수정됐고, 여기서 exit 0인 것은 docs/03 §53의 파일 단위
격리에 따라 **의도된 것**이다.)

`IntakeBacklog.name_collision` 추가. 조건이 결정적이다 —
`collector/runtime.run_once()`는 목적지 이름이 차 있으면 옮기지 않고, 판정
둘(ACCEPTED/DUPLICATE)이 같은 디렉터리를 목표로 하므로 이름 충돌은 **항상**
영구 FAILED다.

**비용 0.** `processed_paths`는 이미 순회 중인 목록이고 `rejected_paths`는
rejected 카운트에 필요하다. 실측(processed 10,000): 7.57초 — C21이 잰 기존
수치와 같은 차수이고 이름 집합 구성은 측정에 잡히지 않는다.

### 2. 문서 감사 — 명세의 규범 조항 대조

명시적 규범 표현 282개를 뽑아 훑었다. 가장 검증 가능한 형태인
**"거부한다"**(반드시 거부)는 전 문서에 **하나뿐**이고 그것이 BUG-28이다 —
이미 기록·특성화돼 있다.

docs/02가 열거한 값(EVENT_TYPES 8 / ROLES 4 / SOURCES 4 / STATUSES 5,
REQUIRED_FIELDS 10)을 `events/schema.py`와 전수 대조 — **전부 일치.**

프로젝트 `.gitignore`도 hygiene 테스트가 이미 지키고 있다. 그런데 docs/08 §28이
요구하는 **Backup Repo의 `.gitignore`**를 확인하다가 다음 항목이 나왔다.

### 3. E-21 — Secret Scan이 git이 커밋하는 디렉터리를 보지 않는다 (신규, 보안)

**측정(실 git, 로컬 remote):** Working Copy에만 `.env`·`notes/id_rsa`·
`scratch.log`를 두고 `backup.run_once()` 실행 → `BACKUP_SUCCESS`,
`push_result="SUCCESS"`, 그리고 **세 파일 모두 원격 커밋에 들어갔다.**

세 사실의 조합이고 각각은 따로 보면 정당하다:

    scan_for_secrets(master_dir)  ->  Local Master를 본다
    _relative_files()             ->  scope 필터됨(daily/·monthly/ 밖은 안 보임)
    git_add_all() -> git add -A   ->  Working Copy 전체를 커밋한다

sync 이외의 경로로 Working Copy에 들어온 파일은 게이트에도(디렉터리가 다름)
sync에도(scope 밖) 안 보이는데 git은 커밋한다. Working Copy는 운영자가
`git init`으로 만들고 들어가 작업하는 실제 저장소다(docs/08 §30).

**E-15와 같은 뿌리, 반대 증상.** E-15는 게이트가 백업되지 않을 파일까지 봐서
거짓 양성으로 Backup을 영구 실패시키는 문제였다. 이쪽은 같은 게이트가 백업될
파일을 안 봐서 거짓 음성이 되는 문제다. 두 항목은 **"게이트를 어느 디렉터리에
겨눌 것인가"** 하나의 결정을 가리키며 함께 닫힌다.

**게이트는 SKIP.** 후보 수정 셋이 전부 보안 게이트나 승인된 명령 집합을
바꾼다(E-21에 표로 정리).

**탐지는 추가했다.** `ops_status.py`가 `scan_for_secrets()`를 **Working
Copy에도** 적용한다 — 게이트는 그대로이고, 이미 결정된 이름 목록을 아무도
보지 않던 디렉터리에 적용할 뿐이다. 사후 보고라 유출을 막지는 못하지만,
**늦게 아는 것과 영영 모르는 것의 차이는 자격증명 교체 여부**다.

완화 확인: 파이프라인 경로로는 새지 않는다 — Master에 같은 파일을 두면 게이트가
정확히 잡아 `BACKUP_FAILED`로 막는다(테스트로 확인).

### 4. 이번 Sprint가 확인한 것

C23의 제3의 길이 계속 유효하다. 다만 E-21이 그 한계도 보여 준다 — **유출에
대해서는 사후 탐지가 약하다.** F-7(실패한 Backup의 흔적 0)과 함께, 탐지로
우회할 수 없는 항목이 둘이 됐다. 둘 다 결정을 기다린다.

### 전체 Regression

**1667 passed, 4 skipped, 971 subtests, 0 failed.**

---

## C23. Third-Path Sprint

C22가 만든 F 절 22건을 C21/C22의 판별 기준으로 다시 읽는 것이 이번 Sprint의
전부였다. 그리고 그 과정에서 기준이 하나 더 늘었다.

### 0. 이번 Sprint가 확립한 것 — 제3의 길

C21은 "**문서가 답을 갖고 있으면 구현, 없으면 결정**"을 세웠다. C23은 그
이분법이 완전하지 않다는 것을 확인했다. 세 번째가 있다:

    동작을 바꾼다      -> 대개 결정
    동작을 보이게 한다  -> 거의 항상 승인 불필요

BUG-42가 그 사례다. 특성화가 "고치려면 (a) 속성을 강제로 벗기거나 (b) 반환
계약을 나눠야 하는데 **둘 다 결정**"이라고 닫아 두었는데, 같은 docstring의
제목이 "**and nothing anywhere says so**"였다. 그 절반은 결정이 아니었다.

C19의 `is_locked`/`lock_held_since`, C22의 `review/` 카운터, C23의
`stale_lock_cannot_be_cleared`와 `future_dated`가 전부 같은 형태다 — 아무것도
고치지 않고 아무것도 결정하지 않은 채 조용하던 것을 시끄럽게 만든다.

### 1. BUG-42 — C19의 탐지기 둘이 모두 이 경우를 못 봤다

읽기 전용 속성이 붙은 stale lock은 `os.unlink()`가 `PermissionError`로
실패하고, `try_acquire_lock()`이 False를 돌려주며, **False는 하류에서 "다른
Runner 실행 중"을 뜻한다.** Runner는 영구·복구불능 상태를 일상적 경합으로
읽고 매 스케줄마다 조용히 건너뛴다. Lock으로 건너뛴 실행은 Manifest도 쓰지
않는다(docs/14 §7, 의도된 것).

**실측한 새 사실:** C19가 stuck lock을 위해 만든 탐지기 **둘 다 이것을 놓친다.**
`is_locked()`는 False, `lock_held_since()`는 None — 둘 다 *살아 있는*
프로세스를 전제하는데 이 lock의 프로세스는 죽어 있다. 내가 C19에서 만든
안전망에 정확히 이 모양의 구멍이 있었다.

`stale_lock_cannot_be_cleared()` 신설: 파일이 존재하고 + 기록된 프로세스가
죽었고(§27상 stale) + 쓰기 불가. 셋이 모두 참일 때만 참이므로 **구조적으로
영구**다 — 아무도 파일을 다시 쓰지 않고 속성이 모든 unlink를 막는다. 일반
stale lock(다음 실행이 인수한다)은 보고하지 않으므로 노이즈가 없다.
`os.access(W_OK)`가 정확히 unlink 가능 여부와 일치함을 먼저 측정했다.

테스트 12건. BUG-42의 특성화는 **동작 부분을 그대로 두고** 제목의 후반부만
정정했다.

### 2. BUG-41 + E-14 복합 — 실패한 Backup은 흔적을 하나도 남기지 않는다

따로 보면 각각 견딜 만하다. 함께 재보니 아니었다 — F-7에 표를 실었다.
세 내구 위치(`backup_state.json` / `last_run.json` / `logs/backup/`) **전부**에서
FAILED가 사라지고 exit 0이 남는다.

BUG-41의 원 기술보다 넓다는 것도 확인했다: docstring은 *무변경* 경로를
지목하는데, 변경이 있는 **성공 경로도** FAILED를 덮어쓴다.

이 측정이 E-14의 성격을 바꾼다. 지금까지 "Spec 미충족"으로만 적혀 있었는데
실제로는 **BUG-41을 견디는 유일한 내구 기록**이다 — Manifest는 실행당 하나뿐이라
다음 실행이 덮어쓰고 `backup_state`는 현재 상태만 담는다.

탐지로 우회할 수 없는 유일한 사례이기도 하다. 이미 지워진 것을 `ops_status.py`가
볼 방법은 없다. 그래서 **SKIP 유지**하고 숫자만 고정했다(4건).

### 3. BUG-30 — 값은 계산되고 화면에는 도달하지 않았다

미래 mtime 파일 하나로 intake 3회: 매번 `moved=0`, `skipped_not_stable=1`,
화면에는 `transport=1`만 영원히. `skipped_not_stable`은 Manifest에 도달하지만
`_print_last_run()`은 SUCCESS component를 출력하지 않고 transport는 성공한다.

`IntakeBacklog`가 `unparseable`을 위해 이미 써 둔 문장이 그대로 적용된다 —
"지워지지 않는 경고는 없는 것보다 나쁘다". `future_dated` 카운트를 더하고
기존 backlog 문장에 이유를 덧붙였다.

**`awaiting_intake`에서 빼지 않았고 `is_clear`도 건드리지 않았다.** 그 파일이
"in flight"인지가 BUG-30이 남긴 판단이고, `unparseable`을 뺀 근거("영원히
parked됨이 증명된다")가 여기엔 성립하지 않는다.

### 4. BUG-46 — 기술이 실제보다 넓었다

실측으로 좁혔다: **미래 날짜 Candidate는 자가 치유되고**(그 날짜가 어제가 되면
Scheduler가 렌더링한다) **시작일 이전만 영구**다. `find_orphaned_events()`가
clean을 보고하는 것도 정확하다(후보가 존재하므로).

탐지는 넣지 않았다 — 시작일 이전인지 판정하려면 `ops_status.py`가 읽지 않는
환경변수가 필요하고, 미설정 시 무엇을 보고할지가 또 하나의 판단이다.
조건을 F-9에 정확히 적었다.

### 5. 재평가 결과 요약

| 항목 | C23 판정 |
|---|---|
| BUG-42 | 동작은 결정, **가시성은 구현** → 탐지 추가 |
| BUG-30 | 동작은 결정, **가시성은 구현** → 카운트 추가 |
| BUG-41 | 결정 유지 + 영향 범위 확대 측정(F-7) |
| BUG-46 | 결정 유지 + **범위 축소** 확정(F-9) |
| BUG-52 | 결정 유지 — marker를 넓히면 반대 오류가 생기고 선 긋기가 판단 |
| BUG-55 | 결정 유지 — Linux에서는 실제로 다른 디렉터리(크로스플랫폼 변경) |
| BUG-38 | 결정 유지 — C22가 인접 4곳을 고치며 의도적으로 남긴 지점 |

### 전체 Regression

**1646 passed, 4 skipped, 968 subtests, 0 failed.**

---

## C22. Inventory Sprint

C21에 이어서. 이번 Sprint의 성과 대부분은 **새 결함을 찾은 것이 아니라, 이미
찾아 놓고 기록하지 않은 것을 찾은 것**이다.

### 1. 먼저 결함 없음을 확인한 것 (반복이 아니라 새 각도)

**날짜 경계 산술** — 아직 재본 적 없던 축이다. 윤년(2028-02 = 29일), 연말 넘김
(`_previous_month(2026,1) = (2025,12)`), 12개월 밀린 catch-up, 첫 실행이 연말인
경우, 같은 날 재실행. `pending_months()` / `month_dates()` / `pending_dates()`
전부 정확. **결함 없음.**

**Event 보존** — 8개 event_type × 2일 = 16건을 심고 파이프라인 끝까지 추적했다.
유실 0, `processed/`와 `rejected/`에 동시 존재 0, 어디에도 없는 Event 0,
DROP(STARTED/RESUMED)이 History로 새는 것 0. **결함 없음.**

이 과정에서 `COMPLETED`/`BLOCKED`가 KEEP이 아니라는 것을 발견했는데, 확인해
보니 docs/05 §24가 그 셋을 REVIEW 예시로 **직접 지목**하고 `history/filter.py`가
그 조항을 인용하고 있었다 — 코드가 맞다. 그런데 거기서 다음 항목이 나왔다.

### 2. E-20 — REVIEW Candidate는 도달할 곳이 없고, 세는 사람도 없었다

`BLOCKED`/`COMPLETED`/`CANCELLED`는 REVIEW로 저장되는데 `generate_daily_history()`는
KEEP만 읽고 `submit_review()`는 `filter_result`를 바꾸지 못한다. 실 Runner로
확인: COMPLETED Event가 Daily에 없음 → 사람이 리뷰 → **2회 더 실행해도 없음.**

유실은 아니다(후보는 durable하고 `find_orphaned_events()`가 조용한 것도 정확하다).
문제는 **그 더미를 세는 곳이 없었다**는 것이다 — `rejected/`, `signals_rejected/`,
Orphan Event는 전부 카운터가 있는데 `review/`만 없었다.

카운터를 추가했다. 이것은 정책이 아니라 **명세가 요구하는 신호**다 — docs/05 §50이
"REVIEW가 너무 많다 → 자동화 실패 신호"라고 직접 규정하고, 그 신호는 숫자를 보는
사람이 없으면 작동할 수 없다. 임계값은 정하지 않았다(그것이 정책이므로). 승격
경로는 **SKIP** — E-20에 근거와 다음 조건을 적었다.

### 3. F 절 — BACKLOG가 절반이었다

테스트 docstring이 참조하는 결함 식별자 **60개 중 43개가 BACKLOG.md에 한 번도
등장하지 않았다.** 그중 `NOT FIXED`로 명시된 **22건**을 F 절로 옮겼다.

이 파일의 첫 줄은 "승인 없이 진행할 수 없어 **SKIP한 항목**"을 기록한다고
말한다. 실제로는 A/E 절에 20여 개가 있었고, 진짜 인벤토리는 40건이 넘었다.
CEO가 "무엇에 승인이 필요한가"를 이 파일로 판단한다면 절반을 못 본 셈이다.

전부 현재 동작이 맞다는 것은 자동으로 검증된다 — 특성화 테스트는 "오늘 이렇게
동작한다"를 단언하므로 **suite가 green이라는 사실 자체가 22건 모두 여전히
미수정임을 증명한다.**

E-11이 예측한 것의 반대 방향이다. E-11은 "고쳤다는 기록이 저장소보다 오래
산다"(C17)였고, 이번 것은 "**고치지 않았다는 기록이 BACKLOG에 도달하지 않는다**"
이다. 원인은 같다 — 테스트 docstring과 `BACKLOG.md`가 서로를 참조하지 않는다.
대조 자체는 값싸다(`BUG-\d+` 차집합 한 번).

### 4. BUG-40 계열 — 22건 중 하나는 결정이 아니라 미구현이었다

인벤토리를 훑은 값이 여기서 나왔다. `json.loads()`는 깊게 중첩된 입력에
`RecursionError`를 던지고, 그것은 `RuntimeError` 서브클래스라
`except (OSError, ValueError)`가 덮지 못한다. 같은 모양이 6곳, 그중 **5곳이
예외를 밖으로 내보냈다**(실측).

판별 기준은 C21에서 세운 것을 그대로 썼다 — **함수 자신의 문서가 "파싱할 수
없음"의 답을 갖고 있는가.** 4곳은 갖고 있었고(구현), 1곳은 없었다(결정).
`agent/signals.py`는 이미 같은 `json.loads`에 대해 답해 두었고 주석까지 달려
있었다 — 그것이 선례다.

수정: `transport.intake._is_parseable_json`(Runner 2단계 영구 정지),
`app/desktop_activity._read_one`(COMPANY 뷰 사망),
`agent/delivery`의 두 지점(`ops_status.py` 사망),
`collector.Collector.collect`(FAILED로 `incoming/`에 남아 영원히 재시도 →
REJECTED로 정정).

**남긴 것:** `FileHistoryRepository.list()` — 파일 단위 관용을 문서화한 적이
없는 유일한 지점이고, 격리/건너뛰기/정지는 A-7이 기다리는 Data Safety 결정이다.
`test_a_candidate_repository_still_raises`가 그 경계를 **입장으로서** 고정한다.

BUG-40의 특성화는 보증으로 다시 썼다(C19의 `RecordRunRetryDuplicationTests`와
같은 처리).

### 5. Release Audit

진입점 3종을 설정 없이 실행: `run_agent.py` / `init_notion.py` /
`run_company_ops.py` 전부 **exit 1**과 이름이 붙은 메시지로 끝난다 —
docs/14 §4("`1`은 설정 오류 전용")와 일치. traceback 0건.

### 전체 Regression

**1622 passed, 4 skipped, 968 subtests, 0 failed.**

---

## C21. Integrity & Measured-Bottleneck Sprint

C20에 이어서. 이번에는 결함을 **찾는 방법**을 세 가지 새로 썼다 — 직렬화 왕복
전수 검사, 파이프라인 전체 멱등성 측정, 그리고 아직 재본 적 없는 구간의 성능
실측. 앞의 둘은 "결함 없음"으로 끝났고 그 결과를 가드로 고정했다. 셋째가
실제 병목 하나를 짚어냈다.

### 1. 직렬화 왕복 무결성 — 8개 클래스 전수, 결함 없음

`src/`에서 스스로를 저장하는 클래스는 여덟이고, 그 안에 Event · History
Candidate · Backup 기록 · Retry Queue 둘 · Run Manifest가 전부 들어 있다.
dataclass에 필드를 추가하고 `to_dict()`를 잊으면 **조용한 데이터 유실**이다 —
만든 실행에서는 메모리에 살아 있고 파일을 다시 읽는 순간 사라지며 아무 데도
오류가 없다.

여덟 개 전부 확인: 필드 누락 0, 왕복 불일치 0. `RunSummary`가 필드가 아닌
`overall_status`/`exit_code`를 쓰는 것만 예외이고 의도된 것이다(읽는 쪽이
판정을 다시 계산하지 않아도 되도록).

깨끗한 것을 확인한 뒤 **가드를 남겼다**. 이 실패 모드는 누군가 필드를 추가하는
날에만, 조용히 나타나기 때문이다. 가드는 인스턴스를 만들지 않고 소스를 읽으므로
나중에 추가되는 클래스도 자동으로 덮인다. 회귀를 실제로 잡는지 확인했다 —
`RetryQueueEntry`에 필드를 하나 더해 보니 이름을 대며 실패했다.
(`test_architecture_invariants.py::SerialisationFidelityTests`, 5건)

### 2. 파이프라인 전체 멱등성 — 결함 없음

각 단계의 dedup은 개별로 테스트돼 있었지만, **그것들이 합쳐서 만들어 내는
성질** — 같은 입력으로 다시 돌리면 아무것도 늘지 않는다 — 을 확인한 것은
없었다. 수동 재실행과 스케줄러 중복 발화에서 매번 의존하는 성질이다.

전체 runtime 트리를 해시로 떠서 비교한 결과, 두 번째 동일 실행이 다시 쓰는
파일은 정확히 셋이고 전부 정당하다:

    collector.log        append-only 로그
    last_run.json        새 Run Manifest
    backup_state.json    BACKUP_SUCCESS -> BACKUP_NOT_REQUIRED

Company History · git 이력 · Notion 투영 · Dashboard는 그대로다. 5회 연속
재실행해도 누적 없음. (`test_runner_notion_integration.py::
WholePipelineIdempotencyTests`, 5건)

### 3. naive/aware datetime 비교 — 2개 지점, 1개 수정 1개 SKIP

`fromisoformat` 결과 둘을 비교할 때 한쪽이 offset 없는 값이면 `datetime`은
`ValueError`가 아니라 **`TypeError`**를 던진다. "값이 이상하면 `ValueError`"라는
자연스러운 가드가 그대로 통과시킨다. 두 지점이 그랬다.

**(a) `app/desktop_activity._before()` — 수정함.** `processed/`에 offset 없는
Event가 하나 있으면 `ops_status.py`의 COMPANY 뷰 **전체**가 죽는다. 이 모듈의
계약은 "must still produce an answer when part of the evidence is damaged"이고,
`_before()`의 docstring은 이미 "비교할 수 없으면 문자열 순서로 폴백한다"고
**써 두었다**. 즉 `TypeError`를 그 폴백으로 보내는 것은 판단이 아니라 쓰여 있는
계약의 구현이다. `validate_event()`가 offset을 요구하므로 Collector를 통해서는
들어올 수 없지만, `processed/`의 파일은 다시 검증되지 않는다 — 레거시 Event,
손편집, 다른 도구가 만든 복원본이 그 형태다.
(`test_observability.py::NaiveTimestampInProcessedEventsTests`, 5건)

**(b) `notion/sync.py`의 Late Event Guard — SKIP 유지(E-19).** 같은 모양이지만
결론이 반대다. 자세한 근거와 이번에 새로 측정한 운영 수준 증거(Retry Queue가
매 실행 1건씩 무한 증식)는 E-19에 적었다. C21에서 고쳤다가 **되돌렸다** —
비교 불가일 때 무엇을 신뢰할지가 기존 특성화가 명시적으로 남겨 둔 결정이고,
`_before()`와 달리 §29-30에는 폴백 조항이 없다.

두 사례의 차이가 이번 Sprint에서 가장 유용한 구분이었다: **같은 코드 모양이라도
문서가 답을 갖고 있으면 구현이고, 없으면 결정이다.**

구조 가드를 남겼다 — 이 계열을 스캔하되 알려진 한 지점(`sync.py`)만 이름으로
허용한다. 두 번째가 생기면 즉시 실패하고, E-19가 해결되면 목록이 낡았다고
알려 준다. 회귀를 실제로 잡는지 확인했다.
(`test_runner_failure_paths.py::NaiveAwareComparisonGuardTests`, 2건)

### 4. 성능 실측 — 아직 재보지 않았던 구간

| 대상 | 규모 | 결과 |
|---|---|---|
| Daily 생성 (하루 후보 수) | 100 / 1,000 / 5,000 | 0.001 / 0.006 / 0.017s |
| `build_keep_index` | 5,000 | 0.001s |
| Monthly 통합 (31일 × 항목) | 31 / 310 / 1,550 | 0.18 / 0.20 / 0.24s |
| Notion Sync (20 Project) | 100 / 1,000 / 5,000 Event | 0.001 / 0.009 / 0.043s |
| `scan_for_secrets` | 5,000 파일 | 0.149s |
| Backup sync (전부 신규) | 5,000 파일 | 3.10s |
| **Backup sync (변경 없음)** | 5,000 파일 | **29.9s** |
| **`repository.list()`** | 5,000 후보 | **27.3s** |

렌더링·통합·Sync는 전부 무시 가능하다. 비싼 것은 둘 다 **파일 읽기**다.

**Backup sync가 "아무것도 안 바뀐" 경우에 "전부 복사"보다 10배 느린 것은
결함이 아니다.** `_content_differs()`는 `filecmp.cmp(shallow=False)`로 내용을
바이트 비교하고, 그 docstring이 stat/mtime 시그니처(`shallow=True`, 약 9배
빠름)로 바꾸는 것은 "modified"의 의미를 조용히 약화시키는 **Backup 계약
변경**이라고 이미 못박아 두었다. 내용을 읽지 않고 내용이 같음을 알 방법은
없으므로 계약을 지키는 최적화는 존재하지 않는다. 현실 규모(하루 1파일 + 월
1파일 ≈ 연 377개, 5년 ≈ 1,885개)에서 약 11초이고, 하루 1회 실행에는 수용
가능하다. 기록만 한다.

### 5. 실제 병목 — 6.5단계가 Late 날짜마다 저장소를 다시 읽는다 (수정함)

`scheduler.py`는 자기 날짜 루프에 대해 이 문제를 **이미 해결**해 두었다 —
CEO Decision ②(History Repository Cache A안), 소스에 그대로 인용돼 있다.
`update_daily_history()`가 `keep_candidates`를 받는 이유도 정확히 그것이고
모듈 docstring이 그렇게 적는다. **6.5단계만 그것을 넘기지 않는 유일한 호출부
였다.**

측정(후보 3,000건, Late 날짜 7개):

    호출 7회 / 0.97s  ->  호출 1회 / 0.17s

Late 날짜 수와 `keep/` 크기 양쪽에 비례해 벌어지고, `keep/`는 정리되지
않는다(B절 6번).

스냅숏이 안전한 근거는 Scheduler와 같다: 5단계가 모든 Candidate를 쓴 **뒤에**
이 단계가 시작하고, 루프 안에서는 아무도 Candidate를 쓰지 않는다.

실패 시에는 `None`으로 폴백해 **이전 동작과 바이트 단위로 동일**하게 만들었다 —
각 날짜가 자기 호출을 하고 자기 오류를 잡으므로, 저장소가 깨져도 날짜당 FAILED
하나씩이 나오고 단계 밖으로 새지 않는다. 그 폴백도 테스트로 고정했다.

테스트 4건(`test_runner_failure_paths.py::LateUpdateBatchReadTests`) —
호출 횟수 상한, 각 Late Event가 자기 날짜에만 들어가는지, idle 실행이 추가
읽기를 하지 않는지, 실패 폴백. 회귀를 실제로 잡는지 확인했다(`keep_candidates`를
빼자 "7 calls for 5 dates"로 실패).

### 전체 Regression

**1603 passed, 4 skipped, 965 subtests, 0 failed** (C20 종료 시점 1582 →
신규 21건 순증: 추가 28건, 되돌린 BUG-29 수정과 함께 제거 7건).
compileall / `git diff --check` / repository hygiene 통과.

---

## C20. Same-Pattern Sweep

C19에 이어서 진행. 이번 Sprint의 방법은 하나다: **한 결함을 고칠 때마다 같은
모양이 다른 모듈에 있는지 전수 조사한다.** 그렇게 찾은 것이 이번 발견의
대부분이고, 그중 둘은 단일 결함이 아니라 **결함 가족**이었다.

발견 도구도 하나 새로 썼다. `sys.settrace`로 전체 suite가 `src/`의 어느 줄을
**한 번도 실행하지 않는지** 측정하는 stdlib-only 스크립트다(모듈·클래스 수준
문장은 import 시점에 실행돼 discovery 전에 지나가므로, 함수 본문 안의 문장만
센다). 150줄이 나왔고 대부분은 정당한 미도달(Windows에서의 POSIX 분기, 추상
메서드, 실 Notion API가 필요한 경로)이었지만, 그 안에 실제 결함 두 건과
미구현 Spec 한 건이 있었다.

### 1. Mixed-offset Timestamp 정렬 — Company History가 실제 순서와 다르게 기록된다

`app/desktop_activity._before()`는 Event `timestamp`를 문자열로 비교하면 안
된다고 **이미 문서화하고 파싱으로 고쳐** 두었다("String comparison is correct
here only for same-offset timestamps"). 같은 필드를 문자열로 정렬하는 곳이
`daily/`에 셋 남아 있었다:

    daily/generator.py:122      렌더링되는 Daily의 항목 순서
    daily/late_events.py:109    Late Events 섹션의 순서
    daily/role_summary.py:124   역할별 요약의 순서

**재현:**

    2026-08-05T09:00:00+09:00   00:00 UTC   먼저 일어났다
    2026-08-05T01:00:00+00:00   01:00 UTC   나중에 일어났다

둘 다 자기 offset 기준 2026-08-05이므로 같은 Daily 파일에 들어가는데, 문자열
정렬은 나중 것을 먼저 놓는다. Source of Truth 문서가 하루의 사건을 틀린
순서로 적는다.

도달 가능성: 스키마는 offset을 요구하되 KST를 요구하지 않고
(`test_spec_conformance.py::test_the_schema_accepts_a_non_kst_offset`가
`+00:00`/`-05:00`/`+05:30`을 고정), Signal은 자기 `timestamp`를 직접 지정할 수
있다.

**수정:** `HistoryCandidate.chronological_key` 신설 — 세 곳이 **같은 키**를
쓰므로 드리프트할 수 없다. 파싱 불가하거나 offset이 없는 timestamp는 비교할
instant가 없으므로 두 번째 버킷에 넣어 뒤로 보낸다(전순서 보장 — 렌더링 도중
`TypeError`로 하루를 통째로 잃는 것이 최악이다). **그룹핑은 건드리지
않았다**: 후보는 여전히 자기 offset 기준 날짜에 속한다(docs/06 §12, 일이
일어난 곳의 그날). 바뀐 것은 하루 **안의** 순서뿐이다.

비용 측정: 정렬 대상은 항상 **하루치로 먼저 필터링된 뒤** 정렬되므로 실제 n은
수십이다. 그래도 재봤다 — n=10,000에서 문자열 0.91 ms → chronological 12.9 ms.
현실 규모에서 무시 가능. 최적화 불필요.

테스트 9건(`test_daily_role_summary.py::MixedOffsetOrderingTests`).

### 2. `except OSError`가 `UnicodeDecodeError`를 놓친다 — 4개 모듈, 1건은 Backup을 중단시킴

`monthly/generator._existing_generated_at()`의 docstring은 이 버그를 **이미
자기 자신에 대해 기록**해 두었다("it used to catch only `OSError`, so a
previous Monthly that was not valid UTF-8 raised `UnicodeDecodeError` (a
ValueError) out of here and failed the entire rebuild"). 같은 모양을 전수
조사했다 — 디코드/파싱하는 모든 `try` 중 `ValueError`를 잡지 않는 것. **네
곳이 나왔다.**

| 위치 | 스스로 약속한 계약 | 실제 |
|---|---|---|
| `daily/generator.py` `update_daily_history()` | "Never raises for an I/O or rendering failure" | 예외 전파 |
| `collector/runtime.py` `run_once()` | docs/03 §53 "한 파일의 실패가 배치를 막지 않는다"(같은 함수가 인용 중) | 배치 전체 중단 |
| `agent/signals.py` `load_signals()` | 못 쓰는 Signal 하나는 격리, 나머지 날짜는 진행 | Agent 실행 전체 중단 |
| `agent/delivery.py` `_problem()` | `UNREADABLE`이 선언된 4개 판정 중 하나 | `ops_status.py` 크래시 |
| `monthly/parser.py` `read_daily_document()` | "Raises DailyParseError if unreadable" | `UnicodeDecodeError`가 그대로 (호출자가 흡수해 파일명 없는 코덱 메시지만 남음) |

**실 Runner로 측정한 blast radius**(첫 번째 항목, 손상된 Daily + 같은 날짜의
Late Event):

| 신호 | 수정 전 | 수정 후 |
|---|---|---|
| `run_once()` | `UnicodeDecodeError` 전파 | 정상 반환 |
| 기록된 Component | **9개 중 6개** | 9개 |
| monthly / backup / dashboard | **시작조차 못 함** | 정상 실행 |
| Backup commit | **없음** | 있음 |
| Dashboard row | **0** | 1 |
| late_update 분류 | `STEP_ABORTED [DEGRADED/UNKNOWN]` | `LATE_EVENT_MERGE_FAILED [DEGRADED/RETRYABLE]` |

즉 docs/14 §5가 **DEGRADED**로 분류한 단계가 **CRITICAL**로 분류한 단계
(Backup)를 중단시키고 있었다 — 두 Severity를 나눈 이유 자체를 뒤집는다.
(C19가 추가한 "시작되지 못한 단계" 보고가 이 상황을 정확히 드러낸다.)

**수정:** 다섯 곳 모두 `except (OSError, ValueError)`. 손상된 파일은 여전히
지우지도 고치지도 않는다(§41).

테스트 6건(`test_runner_failure_paths.py::UndecodableFileIsolationTests`).
그중 하나는 **구조 가드**다 — AST로 각 디코드 호출의 **가장 안쪽** 감싸는
`try`를 찾아 `ValueError` 계열을 잡는지 검사한다(바깥 `try/finally`를 오탐하지
않도록 innermost로 좁혔다). 가드가 실제로 회귀를 잡는지 확인했다: 수정 하나를
되돌리자 `runtime.py:121 guarded at 120 catching ['OSError']`로 정확히 지목하며
실패했다.

### 3. docs/08 §29가 이름을 적어 둔 Secret 파일 2종이 탐지되지 않았다

§29는 탐지 대상 예시로 `.env` / `credentials.json` / `token.json` 셋을 적는다.
`_SECRET_EXACT_NAMES`에는 첫 번째만 있었다.

**측정:** `credentials.json`을 `daily/`에 두면 — §26이 **백업 대상으로 규정한
디렉터리** — 탐지되지 않고 Working Copy로 동기화된다. 즉 원격에 push된다.
게이트가 막으라고 있는 바로 그 일이다.

**수정:** 두 이름을 목록에 추가. §29를 **구현**하는 것이지 확장이 아니라는
선을 지켰다 — `secrets.json`/`credentials.yml`/`token.txt`는 모듈 docstring이
미탐지로 **측정해 둔** 이름이지 Spec이 요구한 이름이 아니므로 넣지 않았다
(넣는 것은 정책 결정).

기존 특성화 테스트가 `credentials.json`을 "탐지 안 되는 것이 정상"인 이름들과
**한 묶음으로** 단언하고 있어서, Spec 요구사항이 "놓쳐도 되는 것" 목록 안에
숨어 있었다. 테스트를 둘로 쪼갰다: Spec이 적은 이름은 보증으로, 나머지는
특성화 그대로.

### 4. Dashboard 행과 Run Manifest의 run_id가 우연히 일치하고 있었다

`app/runner.py`가 같은 규칙을 두 번 표현했다 —
`resolved_manifest_run_id = run_id or now_iso(now)`와, Dashboard 단계의
`resolved_run_id = run_id or now.isoformat(timespec="seconds")`. `now_iso()`가
바로 그 호출이므로 결과는 같았지만, 한쪽만 바뀌면 OPS_RUNS 행에서 증거로
돌아가는 유일한 경로가 끊긴다.

**수정:** Dashboard가 manifest의 값을 **재사용**한다. 상관관계가 우연이 아니라
구조가 된다. 테스트 4건(`test_runner_notion_integration.py::
DashboardRunIdTraceabilityTests`) — manifest와의 일치, 재시도된 행이 *재시도한*
실행이 아니라 원래 실행의 id를 유지하는지, 서로 다른 두 실행이 두 행을 만드는지,
같은 instant의 재실행이 행을 늘리지 않는지.

기존 `RunIdCorrelationTests`가 소스 **문자열**을 그대로 단언하고 있어 이 개선에
실패했다. 의도(각 run_id가 넘겨받은 `now`에서 파생되며 자체 시계를 읽지
않는다)를 검사하도록 고치고, 아무도 확인하지 않던 end-to-end 상관관계
(BackupLogEntry.run_id == manifest.run_id) 단언 2건을 더했다.

### 5. Agent 프로세스 크래시 지점 — E2E 공백 2곳

`agent/outbox.py`는 쓰기 → 전송 → 파일링 3단계라 창이 둘인데, 그중 하나만
E2E로 덮여 있었다. 나머지 둘의 **downstream 주장**("a duplicate delivery
costs one redundant file copy and produces no duplicate History and no
duplicate Notion write")은 Collector·History Filter·Daily·Notion이 실제로 다
돌아야 확인되는 문장인데 단위 사실로만 확인돼 있었다.

각 크래시를 **그 크래시가 남기는 온디스크 상태로 재구성**해(mock 아님) 실
Desktop 4 파이프라인에 통과시켰다. 4건 전부 통과 — History 항목은 정확히 4개,
중복 0, 유실 0. 네 Desktop이 동시에 재전송하는 복합 케이스 포함.
(`test_agent_multi_desktop_e2e.py::CrashPointRecoveryTests`)

### 6. 실패한 Late Event 병합이 재시도되지 않는다 (E-17) — 이번 Sprint 최대 발견

§5의 크래시 테스트를 Runner 레벨로 올리다가 드러났다. Late 병합 실패는
`RETRYABLE`로 기록되는데 **어떤 후속 실행도 그 날짜를 다시 보지 않는다** —
6.5단계의 대상은 "이번 실행에서 Candidate가 쓰인 날짜"뿐이기 때문이다.

측정: 실행 2에서 실패 → 사람이 파일을 복구 → 실행 3·4는 `late_update`
SUCCESS `updated=0`, **overall SUCCESS / exit 0**, Event는 여전히 없음.
무관한 새 Event가 우연히 같은 날짜에 도착한 실행 5에서야 병합됐다.

`ops_status.py`는 RETRYABLE 실패를 일부러 ATTENTION에 넣지 않는다("다음 실행이
할 일"). 즉 **틀린 분류 하나 때문에** Company History에 구멍이 난 사실이
어디에도 나타나지 않았다.

**C20에서 고친 것:** 분류를 `PERMANENT`로 정정(사실에 맞는 값 선택이지 Spec
변경이 아니다 — docs/14 §5의 두 정의 중 어느 쪽이 참인지의 문제다). 이제
ATTENTION에 뜬다. 진짜 재시도 메커니즘은 새 state 또는 새 정합성 패스가
필요해 **SKIP**(E-17에 조건 기록).

### 7. 검증 분기 커버리지 — 외부 입력 경계 14건

미실행 줄 측정에서 **거부(rejection) 분기 무리**가 0회 실행으로 나왔다.
`validate_event()`(다른 Desktop에서 온 Event), `parse_signal()`(운영자 도구가
쓴 파일), 5개 state 로더(크래시·복원을 견딘 파일) — 전부 경계다. 하나가
뒤집혀 있었어도 1,550개 테스트 중 아무것도 눈치채지 못했을 것이다.

14건 추가(`test_state_consistency.py::NeverExercisedRejectionTests`). **전부
통과** — 즉 결함은 없었고, "그럴 것이다"가 "확인됐다"로 바뀌었다.
`agent/outbox.py`의 "delivered but could not be filed" 분기도 마찬가지로
한 번도 실행된 적이 없어 5건을 더했다(기존 테스트는 `sent/`를 파일로 막아
**전송 자체가 일어나지 않는** 경로만 덮고 있었다 — 이름과 달리).

미실행 줄: **150 → 114**. 남은 것은 대부분 정당한 미도달(Windows에서의 POSIX
분기, 추상 메서드, 실 Notion API 경로, `except OSError: pass` 정리 핸들러).

### 8. `subprocess` 디코딩 — 조용한 `stdout=None` (E-18)

`text=True`만 준 `capture_output=True`는 로케일 코드페이지로 디코딩하고,
실패하면 예외가 **리더 스레드**에 갇혀 `stdout`이 조용히 `None`이 된다.
`backup/git_ops._run_git()`가 그 형태였다(현재 ASCII 출력인 것은 기본값의
사슬 덕분). `encoding="utf-8", errors="replace"`로 실패 모드 자체를 제거했다.
테스트 헬퍼는 실제로 이 버그를 밟고 있었다 — `git show`로 Daily 파일을 읽는
순간 em dash에서 깨졌다.

### 9. 정리

- `notion/transport._rich_text_value()` 제거 — C19가 `_text_value()`로
  일반화하면서 호출자가 사라졌다. 하드코딩 키만 다른 리더 둘은 드리프트하는
  모양이라 남겨 둘 이유가 없다.
- `tests/test_runner_notion_integration.py`의 스캐폴딩을
  `RunnerNotionTestCase`로 분리. 새 suite가 테스트 클래스를 상속하는 바람에
  기존 12건이 **중복 실행**되고 있었다(28건 15초 → 16건 9초).

### 전체 Regression

**1582 passed, 4 skipped, 0 failed** (C19 종료 시점 1519 → 신규 63건,
subtests 808 → 939). compileall / `git diff --check` / repository hygiene 통과.

---

## C19. Restoration & Reconciliation Sprint

C18 직후 이어서 진행. 시작하자마자 **이 파일 자체가 저장소와 어긋나 있다**는
것을 발견해 그 정정이 이번 Sprint의 첫 항목이 됐다.

### 0. C17 섹션이 존재하지 않는 구현을 "완료"로 기록하고 있었다

C19의 첫 단계는 늘 하던 "BACKLOG를 읽고 실제 코드와 대조"였다. 대조 결과
**아래 C17 섹션이 서술하는 코드 변경 6건과 테스트 약 40건이 저장소에 하나도
없었다.**

| C17이 기록한 것 | 저장소 실제 |
|---|---|
| `scheduler/lock.py::is_locked()` | 없음 |
| `scheduler/lock.py::lock_held_since()` | 없음 |
| `ops_status.py::_EXPECTED_COMPONENT_ORDER` | 없음 |
| `NotionClient.find_by_title()` / `find_or_create_by_title()` | 없음 |
| `DrainPendingResult` (`drain_pending()` 실패 사유) | 없음 |
| `agent/status.py::needs_attention()` 미래 날짜 탐지 | 없음 |
| "전체 Regression 1466 passed" | 실측 **1426 passed** (차이 40 = 없는 테스트 수) |

**결정적 증거 두 가지.** 첫째, `tests/test_notion_dashboard.py::
RecordRunRetryDuplicationTests`가 여전히 **특성화** 상태였다 — 그 docstring은
"이 테스트가 실패하기 시작하면 find-before-create가 추가된 것이고, 그때
보증으로 다시 써라"라고 적어 두었는데, C17은 정확히 그 작업을 했다고
기록하면서 테스트는 특성화 그대로였다. 둘째, C17이 "Python 3.9.7 호환성으로
고쳤다"고 적은 `tests/test_agent_fault_injection.py`의 `path.parents[1:]`가
그대로 남아 있었다(현재 이 머신의 `python`은 3.13.14라 실패하지 않는다 —
C17의 환경 서술도 현재와 맞지 않는다).

`git log`로도 확인된다. `bafd243`("C18 deep audit and backlog update")는
`BACKLOG.md` 한 파일 288줄만 바꿨고, 그 앞 `e981272`는 C12/C13/C16 계열
작업이다. C17의 코드 변경이 커밋된 적이 **없다.**

**왜 이것이 이번 Sprint에서 가장 중요한 발견인가:** C18은 그 위에서
"모든 fork가 결함 없음으로 종료"라고 결론냈다. 즉 존재하지 않는 수정을
전제로 한 감사였다. C13이 남긴 교훈("'불가능함이 확인됐다'로 기록된 측정은
근거보다 오래 살아남는다")의 반대 방향 사례다 — **"고쳤다"로 기록된 수정도
근거보다 오래 살아남는다.**

**대응:** 6건 전부를 실제 코드로 재검증하고(전부 현재 코드에 실재하는 결함임을
확인) 다시 구현했다. 아래 C17 섹션은 **기록으로 남기되 그 내용이 저장소
상태가 아니었음을 여기서 못박는다.** 지우지 않는 이유는 각 항목의 *분석*은
정확했고 재구현의 근거가 됐기 때문이다.

재구현한 6건:

1. **OPS_RUNS 중복 행** — `record_run()`/`drain_pending()`이 find-before-create
   없이 무조건 `create_project()`를 호출. `dashboard_pending.py`의 모듈
   docstring이 "한 Runner 실행은 OPS_RUNS 행을 두 개 만들 수 없다"고 이미
   약속하고 있었고 코드가 지키지 않았다. `NotionClient.find_by_title()` /
   `find_or_create_by_title()` 신설(OPS_RUNS의 `Run ID`는 rich_text가 아니라
   **title**이라 기존 `find_project()`를 재사용할 수 없다).
   `InMemoryNotionTransport.query_database()`가 `"Project ID"`+rich_text를
   하드코딩하고 있어 다른 필터에는 조용히 "결과 없음"으로 답하던 것도 함께
   일반화 — 그대로 뒀다면 find-before-create가 **항상 아무것도 못 찾으면서
   테스트는 통과**했을 것이다.
2. **`drain_pending()`의 실패 사유 유실** — `except Exception:`이 예외를
   통째로 버려 영구 실패 레코드가 `attempt_count`만 올리며 원인 없이 재큐잉.
   `DrainPendingResult`(tuple 서브클래스, 기존 2-tuple unpack 보존 —
   `app.runner.RunResult`와 같은 기법) 신설, `app/runner.py`가
   `DRAIN_PENDING ... REASON <이유>`로 기록(bounded+escape+redact).
3. **미래 날짜 State의 무음 영구 정지** — `last_successful_collection_date`가
   미래면 `pending_dates()`가 안전하게 빈 목록을 주고(이 안전한 동작은 건드리지
   않음) `last_run` 최근·outbox 0·pending 0이 되어 **정상보다 더 건강해
   보인다**. `needs_attention()`에 탐지만 추가.
4. **`_print_last_run()`에서 도달 못한 단계가 안 보임** — 이 뷰는
   `summary.components`에 **있는** 것만 순회하므로, Backup 중단으로
   `recorder.begin(C_DASHBOARD)`에 도달조차 못한 실행은 Dashboard 행이 통째로
   사라진 사실이 화면에 전혀 나타나지 않았다(SKIPPED와 구분 불가 — SKIPPED는
   출력되고 "도달 못함"은 침묵). `app/runner.py`에 `PIPELINE_COMPONENTS`
   신설(`_ARTIFACT_REFS`에서 **파생** — 손으로 복사한 목록은 드리프트한다),
   `ops_status.py`가 빠진 이름을 계산해 보고.
5. **A-20 Orphan 오탐** — Collector가 배치 전체를 `processed/`로 옮긴 뒤에야
   History Filter가 Candidate를 하나씩 쓴다(4→5단계). 대량 Catch-up 중
   `ops_status.py`를 돌리면 — 문서가 안전하다고 보장하는 바로 그 사용법 —
   정상 처리 중인 Event가 영구 유실로 보고된다. `is_locked()` 신설(읽기 전용:
   `try_acquire_lock()`과 달리 Lock을 만들지도 가져가지도 않는다), Orphan
   목록은 **줄이거나 숨기지 않고** 문구만 덧붙인다.
6. **Lock PID 재사용** — `_is_process_running()`은 "그 PID를 가진 프로세스가
   있는가"만 확인하고 "Lock을 쓴 그 프로세스인가"는 확인하지 않는다. 정전으로
   죽은 Runner의 PID가 재할당되면 모든 실행이 영구히 조용히 건너뛰어진다.
   판별 로직 자체는 Lock 파일 계약 변경이라 **SKIP 유지**, 대신
   `lock_held_since()` 신설(**기존** `created_at` 필드만 읽는다 — 새 필드
   없음, `LockFileContractTests`의 온디스크 형태 불변)로 2시간 이상 잡힌 Lock을
   ATTENTION에 보고.

### 1. E-10 — `IntakeBacklog` Desktop별 귀속 (완료)

E-10이 선결 조건으로 요구한 확인부터 했다: `rejected/` 파일이 항상 `source`를
파싱 가능한 형태로 담고 있는가 → **아니다.** 실 runtime의 rejected 4건 실측:

| 파일 | `source` |
|---|---|
| `badrole.json` | `'DESKTOP_9'` (스키마에 없는 값) |
| OneDrive 충돌 본 사본 | 없음 |
| `partial.json` / `zerobyte.json` | JSON 파싱 불가 |

그래서 귀속은 **allowlist + 잔여 카운트**로 설계했다. `events.SOURCES`의
실제 멤버만 귀속하고 나머지는 전부 `unattributed`로 센다.

**`DESKTOP_9`을 그 이름으로 보고하지 않는 것이 설계의 핵심이다.** 여기 세어지는
파일은 전부 Event가 되는 데 실패한 **미검증 입력**이고, 그 문자열을 운영자
터미널에 그대로 흘리는 것은 `oplog.append_line()`이 escape로 막는 바로 그
실수다. 개수는 드러나고(참인 진술) 문자열은 드러나지 않으며, 파일 자체는
사람이 열어 볼 수 있게 그대로 있다.

`SourceBreakdown.total`이 항상 기존 카운트와 같다는 불변식을 모든 테스트가
확인한다 — 운영자가 이미 의존하던 숫자의 의미는 바뀌지 않는다.
`unparseable`에는 일부러 breakdown을 붙이지 않았다: 파싱 못 한 파일은 `source`도
못 읽으므로 전부 `unattributed`가 되어 카운트가 말하지 않는 것을 아무것도
말하지 못한다.

적대적 입력 실측(개행/CR/ANSI escape/NUL/U+2028/100KB blob/`../../etc/passwd`/
`ntn_` 시크릿 형태/중첩 객체/숫자/불리언 각 1건을 rejected·transport·incoming
셋 다에 심고 `ops_status.py`의 COMPANY 뷰 전체를 렌더링): **10개 probe 전부
유출 0, 위조 라인 0.**

`transport`/`incoming`/`rejected` 세 더미 모두에 적용했다 — 셋이 같은 ATTENTION
문장에 등장하므로 하나만 귀속하면 절반만 쓸모 있다. 테스트 18건
(`test_observability.py::BacklogSourceAttributionTests`,
`::BacklogAttributionInStatusViewTests`).

### 2. Multi-Desktop 심층 검증 — 장애 격리 E2E 12건 신설

Desktop 1~4는 클라우드 폴더 외에 **아무것도 공유하지 않는다**(Lock 없음, State
없음, 프로세스 없음). 그것이 설계이고, 이번에는 그 설계를 추론이 아니라 실제
장애를 주입해 확인했다. 매 케이스가 Desktop 2를 서로 다른 방식으로 깨뜨린 뒤
Desktop 1/3/4가 **같은 실행에서 같은 날짜의 Company History에 도달하는지**를
단언한다. 각 테스트의 후반부는 깨진 Desktop이 **격리**됐는지(조용히 버려진
것이 아니라) — 작업이 자기 디스크에 남아 있고, State가 전달한 것보다 앞서지
않으며, 나중 실행에서 복구되는지 — 를 단언한다.

`DesktopFaultIsolationTests` (7건): outbox 손상 / 네트워크 실패 / 네트워크
복구 후 Late Event로 도달 / Signal 거부 / State 손상 / 살아 있는 프로세스가
쥔 Lock / 4대 State 독립성.
`MultiDesktopDateEdgeTests` (3건): 미래 날짜 Signal(소비되지 않고 그대로 남음,
다른 Desktop 영향 0) / 90일 오프라인 후 Catch-up(90개 날짜를 순서대로, 빠짐
없이, 나머지 3대의 날짜를 건드리지 않고) / Desktop 간 시계 오차.
`RepeatedDeliveryIsolationTests` (2건): Desktop 4가 파일을 이미 수거한 뒤의
재전송 / 두 Desktop 동시 재전송.

**결과: 12건 전부 통과. Desktop 간 오염은 한 건도 발견되지 않았다.**

검증 중 확인한 사실 둘(버그 아님, 기록): outbox가 손상되면 Agent는 **어떤
날짜도 시도하기 전에** 멈춘다(outbox를 먼저 drain하고, 깨끗하지 않으면 로컬에
남은 작업을 앞질러 전진하지 않는다) — `dates`가 빈 튜플이고 `status`가
FAILED다. State 파일이 손상되면 `run_once()`는 `AgentStateError`를 밖으로
내보내고(BUG-3이 Scheduler에 대해 정한 것과 같은 태도) Lock은 `finally`에서
정상 해제된다.

### 3. 신규 결함 — Daily와 Monthly가 영구히 어긋난다 (발견·재현·수정)

Event → ... → Monthly 연쇄를 코드로 훑다가 발견. **State가 자신이 서술하는
파일보다 뒤처질 때** 두 단계가 각각 타당한 판단을 내려 합쳐지면 영구 불일치가
된다.

`monthly/generator.run_once()`는 Monthly 파일을 쓰고 **그 다음에** state를
저장한다. 그 사이에 죽은 실행은 "포인터가 아직 열려 있다고 말하는 달의 실제
Monthly 파일"을 남긴다. 이 상태에서 그 달의 Late Event가 도착하면:

| 단계 | 판단 | 근거 |
|---|---|---|
| `mark_month_dirty()` | DIRTY 표시 **안 함** | `2026-08 > last_close(None)` → "아직 통합 전이니 catch-up이 새 Daily를 읽을 것" |
| catch-up 루프 | `MONTHLY_UNCHANGED` | 파일이 이미 있음 → 포인터만 전진, **그리고 DIRTY를 지움** |

**재현 결과(실제 코드, mock 없음):**

```
late update: UPDATED_LATE_EVENT added: ('EVT-LATE',)
mark_month_dirty -> False | dirty: []
run B: [('2026-08', 'MONTHLY_UNCHANGED')]
last close: 2026-08 | dirty: []

Daily   contains 'late work': True
Monthly contains 'late work': False        <- 영구
run C: []                                   <- 이후 어떤 실행도 고치지 않는다
```

Daily에는 있고 Monthly에는 없으며, `last_successful_monthly_close`와
`dirty_months`는 **완전히 건강한 닫힌 달**을 보고한다. README RULE 7에 걸린다.

**수정(정책 변경 없음, 기존 조항 적용):**

- `mark_month_dirty(..., monthly_dir=)` — "통합됐는가"의 지표는 State 포인터
  하나가 아니다. Monthly 파일의 존재가 다른 하나이고, 둘이 어긋나면 **파일이
  이긴다**(docs/10 §49 "History가 State보다 우선"을 Daily 수준에서만 쓰던 것을
  여기에도 적용). `app/runner.py`가 `monthly_dir`을 넘긴다.
- catch-up 루프의 `MONTHLY_UNCHANGED`는 포인터는 전진시키되 **DIRTY를 지우지
  않는다.** UNCHANGED는 파일을 다시 만들지 **않았다**는 뜻이므로 그 DIRTY는
  여전히 참이다. (crash 복구를 위한 원래 clear 의도는 `MONTHLY_GENERATED`에
  그대로 남는다.)

수정 후 같은 재현: catch-up이 UNCHANGED로 포인터를 전진시키고, **같은 실행의**
dirty 루프가 UPDATED로 다시 만들어 Late Event가 Monthly에 도달한다. 다음 실행은
아무 일도 하지 않는다(무한 재생성 없음).

같은 함수를 고치며 인접 결함 둘을 더 닫았다:

- dirty 루프가 `MONTHLY_UPDATED`에서만 DIRTY를 지웠다. 파일이 유실된 dirty 달은
  `MONTHLY_GENERATED`로 올바르게 재생성되고도 플래그가 남아 **매 실행 재생성**
  됐고 `dirty_months`가 영구히 비지 않아 `ops_status.py`가 "재생성 대기"를
  계속 보고했다. 둘 다 지금 Daily를 반영한 파일을 남기므로 둘 다 지운다.
- dirty 루프에 docs/09 §85-86("시스템보다 앞선 달을 만들지 않는다") 가드가
  없었다. `pending_months()`에는 있지만 dirty 루프는 달 목록을 state 파일에서
  직접 가져오므로, 손편집·복원된 state가 이름을 대면 그대로 지나갔다 —
  요구 일수가 0이라 coverage도 "complete"라고 답한다. 실제로
  `MonthlyFaultInjectionTests::test_a_dirty_month_never_consolidated_is_not_a_crash`가
  "PENDING으로 보고된다"고 **적어 놓고** 실제로는 GENERATED되고 있었고, 플래그가
  살아남은 것은 위 첫 번째 결함 덕분이라 단언이 둘을 구분하지 못했다. 이제
  PENDING으로 보고하고 파일을 만들지 않으며, 테스트가 셋 다 확인한다.

테스트 8건(`test_monthly_history.py::StateLagsTheMonthlyFileTests`) + 기존
특성화 1건 정정.

### 4. 신규 결함 — 손상된 Agent State에 잘못된 복구 안내 (수정)

Multi-Desktop 검증 중 발견. `AgentStateError`는 두 가지 전혀 다른 사고를
덮는다: (a) 파일은 멀쩡하고 **다른 Desktop**의 것 (b) 파일 자체가 **읽을 수
없음**. `run_agent.py`는 둘 다 한 분기에서 잡아 identity 안내를 출력했다:

> COMPANY_OPS_PROFILE이 잘못 설정됐다 → 원래 Desktop ID로 되돌린다.

정전으로 잘린 state 파일에 대해 이 안내는 **틀렸다.** 변수는 이미 올바르고,
바꿔도 아무것도 해결되지 않으며, 운영자는 파일이 읽히지 않는다는 사실 —
유일하게 어디론가 이어졌을 사실 — 을 끝내 듣지 못한다.

**수정:** `AgentStateMismatchError(AgentStateError)` 신설. **서브클래스**라
기존 `except AgentStateError`는 전부 그대로 잡고 거부 동작 자체는 바뀌지
않는다. `run_agent.py`가 두 분기로 나뉘어 각각의 안내를 낸다. 두 분기 모두
state 파일을 지우거나 고치지 않는 것은 그대로다 —
`last_successful_collection_date`를 추측하면 그 날짜까지가 조용히 건너뛰어진다.

테스트 7건(`test_agent.py::CorruptStateGuidanceTests`).

### 5. Run Contract / Observability 재대조

`docs/14_RUN_CONTRACT.md`와 실제 코드를 조항별로 대조. **불일치 없음.**

| docs/14 | 실제 | 결과 |
|---|---|---|
| §5 Severity 표 (CRITICAL 5 / DEGRADED 4) | `app/runner.py::_SEVERITY` | 9개 전부 일치 |
| §4 Overall→Exit (0/3/2), `1`은 설정 오류 전용 | `runsummary._EXIT_CODES`, `run_company_ops._report_run_summary()` | 일치 |
| §4 `SKIPPED`는 실패가 아니다 | `overall_status()`가 FAILED만 접는다 | 일치 |
| §7 Manifest는 중단된 실행에도 남는다 | `finally` + `recorder.current` 귀속 | 일치 |
| §7 Lock 미획득 실행은 Manifest를 쓰지 않는다 | `try_acquire_lock()` 실패 시 `return None` — recorder 생성 전 | 일치 |
| §2 `runtime/runs/last_run.json` | `DEFAULT_RUN_SUMMARY_PATH` | 일치 |

9개 Component가 정상 경로에서 **전부 무조건** 기록되는 것도 재확인했다
(`notion_sync`/`dashboard`는 미설정 시 `recorder.skipped()`를 명시적으로
호출한다 — 이것을 확인하지 않고 위 §0-4를 구현했다면 Notion 미설정 설치마다
"시작되지 못한 단계"가 매 실행 뜨는 오탐이 됐을 것이다).

### 6. Security 재감사

신규 코드 경로 중심으로 실측. **실제 결함 없음.**

- **Notion 입력값**: `find_by_title()`의 `value`는 `json.dumps()`로 구조화된
  POST 본문에 실리므로 문자열 보간이 없다 — 주입 불가. (`database_id`만 URL
  경로에 보간되나 이는 설정값이고 기존 동작.)
- **Log injection**: 신규 `DRAIN_PENDING ... REASON`은 `_bounded_error()` →
  `oplog.append_line()`(단일 지점 escape+redact)를 지난다.
- **외부 입력 / 파일명 / Path traversal**: E-10 allowlist 설계 + 위 적대적
  probe 10종 실측(유출 0). `mark_month_dirty()`의 새 경로 조립은
  `month_key(day.year, day.month)` — `date` 객체 파생이라 traversal 불가.
- **Secret leakage**: probe에 `ntn_` 형태 시크릿을 `source`로 심어 COMPANY 뷰
  전체를 렌더링 — 출력에 없음. `lock_held_since()`는 `created_at`을
  `datetime.fromisoformat()`으로 파싱한 뒤 파싱된 객체만 출력한다.
- **Git command injection**: `_run_git()`은 list argv + `shell=False`.
  저장소 전체에 `shell=True`/`os.system`/`eval`/`exec` 0건(재확인).
- **Symlink / Junction**: A-19는 여전히 배포 정책 결정 — **SKIP 유지.**

### 7. Performance / Stress (이 머신, Python 3.13.14, Windows 11)

| 대상 | 100 | 1,000 | 5,000 | 10,000 | 20,000 |
|---|---|---|---|---|---|
| Agent Outbox `stage` | 0.06s | 0.63s | 4.26s | 8.63s | — |
| Agent Outbox `drain` | 0.72s | 6.56s | 34.3s | 81.2s | — |
| Retry Queue `save` | 0.005s | 0.020s | 0.153s | 0.331s | 0.515s |
| Retry Queue `load` | 0.009s | 0.019s | 0.063s | 0.092s | 0.156s |
| Retry Queue `build_index` | 0.000s | 0.000s | 0.001s | 0.002s | 0.004s |
| History reconciliation | 0.17s | 0.95s | — | 10.6s | — |
| **backlog 귀속 (C19 신규)** | 0.09s | 0.83s | 4.03s | 8.84s | — |

Desktop catch-up (1 / 7 / 30 / 90 / 365일): 전부 **0.000s** — 순수 날짜
산술이고 파일 접근이 없다.

**전부 선형이다. 알고리즘 병목 없음.** `drain`은 8.1ms/건으로 가장 비싸지만
파일당 open+`os.replace` 비용이고(이 머신의 알려진 ~5.4ms/파일 open 비용과
동일 차수) 스레드풀 적용은 **명시적으로 배제된 설계 결정**이다 —
`drain()`은 쓰기 경로이고 순서와 파일별 실패 격리가 계약이라
`app/desktop_activity.py` docstring이 그 이유를 이미 적어 두었다.
Retry Queue는 Batch Save(CEO 승인 B안) 덕에 20,000건에서도 0.5초 미만.

C19가 새로 추가한 비용은 backlog 귀속뿐이다(이전에는 glob 카운트만). 파일당
0.88ms로 이미 스레드풀을 타고 있고(`processed/` 스캔과 같은 풀·같은 차수),
`rejected/`가 10,000건까지 커져야 8.8초가 된다 — 그 규모 자체가 이미
ATTENTION 조건이다. 보존 정책(B절 6번)에 이 비용을 근거 하나로 추가한다.

### 전체 Regression

**1519 passed, 4 skipped, 0 failed** (baseline 1426 passed → 신규 93건,
subtests 769 → 808). 2회 연속 동일.

한 건은 코드가 아니라 테스트를 고쳤다. `test_daily_history.py::
KeepCandidatesParameterTests::test_output_matches_between_prefetched_and_self_fetched`가
`generated_at`을 고정하지 않고 문서를 두 번 만들어 비교하고 있었다 —
기본값이 1초 해상도의 `datetime.now()`라 두 호출이 초 경계를 넘으면 실패한다.
전체 실행에서 1회 관측됐고 단독 실행에서는 재현되지 않는, 가장 나쁜 모양의
실패다. 시계는 이 테스트의 주제가 아니므로(주제는 "prefetch 경로와 self-fetch
경로가 같은 문서를 만드는가") 그 한 필드를 고정했다. 같은 모양의 테스트가
더 있는지 전체를 스캔했고 이 한 건뿐이었다.

---

## C18. Deep Audit Sweep

> **정정 (C19).** 이 Sprint는 C17의 수정 6건이 저장소에 있다고 전제했으나
> 실제로는 없었다(C19 §0). "결함 없음" 결론은 그 전제 위에서 나온 것이다.

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

> **정정 (C19).** 이 섹션이 "구현했다"고 적은 코드 변경 6건과 테스트 약 40건은
> **저장소에 커밋된 적이 없다.** C19 §0에 대조표와 증거가 있다. 각 항목의
> *분석*은 정확했고 C19가 그 근거로 6건을 전부 재구현했으므로, 이 섹션은 분석
> 기록으로 남긴다 — 다만 "완료"로 읽으면 안 된다.

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

### 스레드 풀 측정 정정 — 캐시 순서 편향 (C27)

**코드는 바뀌지 않는다. 틀린 것은 위 표다.**

위 "COO 상태 조회" 표의 **직렬 열**(1,000→5.2초, 5,000→24.2초, 20,000→107.1초)은
**cold cache에서 잰 직렬**을, 그 직렬 패스가 데워 놓은 **warm cache에서 잰
스레드**와 비교한 값이다. 벤치마크 순서 편향의 교과서적 사례이며, 그래서
"108배"라는 숫자가 나왔다.

**양방향 통제 재측정**(같은 머신, 순서마다 새 파일 집합, `_read_all` vs 직렬
`_read_one`):

| n | 순서 | 스레드 16 | 직렬 | 배율 |
|---|---|---|---|---|
| 1,000 | 직렬 먼저 | 0.061초 | 0.460초 | 스레드 7.5배 |
| 1,000 | 스레드 먼저 | 0.103초 | 0.051초 | 직렬 2.0배 |
| 5,000 | 직렬 먼저 | 0.280초 | 2.260초 | 스레드 8.1배 |
| 5,000 | 스레드 먼저 | 0.497초 | 0.253초 | 직렬 2.0배 |
| 10,000 | 직렬 먼저 | 0.562초 | 4.526초 | 스레드 8.1배 |
| 10,000 | 스레드 먼저 | 1.020초 | 0.485초 | 직렬 2.1배 |
| 20,000 | 직렬 먼저 | 1.119초 | 8.897초 | 스레드 8.0배 |
| 20,000 | 스레드 먼저 | 2.104초 | 0.942초 | 직렬 2.2배 |

**나중에 도는 쪽이 항상 이긴다.** 지배 비용은 스레딩도 JSON 파싱도 아니고
**cold cache의 첫 파일 열기**다 — 이것 자체는 기존 기록이 맞게 짚었다.

**공정 비교(cold ↔ cold, 20,000건):**

    직렬       8.897초
    스레드 16  2.104초     ->  스레드가 4.2배 빠르다

**warm ↔ warm(20,000건):**

    스레드 16  1.119초
    직렬       0.942초     ->  직렬이 1.19배 빠르다(풀 오버헤드)

**결론 세 가지.**

1. **스레드 풀은 옳다.** 운영상 실제 경우는 cold다 — `ops_status.py`는 이전
   실행이 쓴 파일을 몇 시간 뒤에 읽고, `processed/`는 몇 달에 걸쳐 쌓인다.
   OS 캐시에 남아 있을 이유가 없다. 그 경우 **4.2배**다.
2. **기록된 "6배"는 부풀려진 값이었다.** 같은 편향 방향으로 여기서 8배가 나온다.
   진짜 값은 4배이며, 여전히 충분히 크다.
3. **warm에서 풀이 19% 손해라는 사실은 최적화 근거가 아니다.** 그것은 최적화할
   가치가 없는 경우다.

**이 정정이 막은 것.** C27은 `is_readable_event_file()`을 추가하며 그 비용을
`_attribute()`와 비교했고(10,000건 0.36초 vs 7.46초), 그 대조에서 "스레드 풀이
16배 느리다"는 결과가 먼저 나왔다. 순서를 통제하지 않았다면 **정확히 반대 방향의
최적화**(풀 제거)를 했을 것이고, 실제 운영 경우를 **4배 느리게** 만들었을 것이다.

C13이 남긴 교훈의 성능 판본이다 — *"'불가능함이 확인됐다'로 기록된 측정은 근거보다
오래 살아남는다."* 여기서는 "가능함이 확인됐다"가 맞는 결론을 **틀린 근거로**
들고 있었다. 결론이 맞다고 근거를 검증하지 않으면, 그 근거를 딛고 서는 다음
판단이 틀린다.

**워커 수는 그대로 둔다.** 16에서 cold 20,000건 2.1초이고, 기존 기록의
8→16→32→64 평탄화 관찰과 모순되지 않는다.

### `transport/` 누적 — 단계는 공짜, 뷰는 아니다 (C27 정정)

바로 위 표의 **"중복 intake는 사실상 공짜다 … `transport/`가 무한히 쌓인다는
우려는 측정해 보니 성능 문제가 아니다"**는 절반만 맞다. 그 측정은
`run_intake()`(단계)를 잰 것이고, 같은 파일을 **뷰**는 다르게 읽는다.

| `transport/` 누적 중복 | `run_intake()` | `_count_transport()` |
|---|---|---|
| 100 | 0.005초 | 0.217초 |
| 1,000 | 0.037초 | 2.656초 |
| 5,000 | 0.177초 | **22.4초** |

단계는 파일당 stat 3회로 끝나지만(그래서 공짜다), 뷰는 파일을 **연다** — 이
저장소가 반복해 측정한 대로 항목당 비용의 거의 전부가 파일 열기다. 5,000건이면
`ops_status.py`가 20초를 넘게 쓴다.

**이것은 C27이 새로 만든 비용이 아니다.** 수정 전에도 같은 파일을
`_is_parseable_json()`이 열고 `_attribute()`가 한 번 더 열었다 — 실측 20.6초.
수정 후는 쌍둥이의 `event_id`를 읽느라 2회로 같다 — 20.4초. **차이는 측정
한계 안이다.**

바뀐 것은 비용이 아니라 **왜 쌓이는지가 보인다는 것**이다. `transport/`의
누적은 운영 실수가 아니라 설계된 경로의 산물이다: outbox는 "Transport 수락"과
"sent/로 이동" 사이의 crash에서 Event를 **재전송**하고, 재전송된 파일은
`skipped_already_present`로 판정돼 `transport/`에 **영구히** 남는다. 즉 crash
1회 = 파일 1개가 영원히. `already_collected` 카운터가 그 숫자를 처음으로
보여준다.

A절 6번(보존 정책)에 근거를 하나 더한다: `transport/`의 증가율은 "얼마나
많은 Event가 오는가"가 아니라 **"얼마나 자주 전송 중에 죽었는가"**에 비례하고,
그 비용은 단계가 아니라 **운영자가 가장 먼저 실행하는 뷰**가 낸다.

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

### E-10. `IntakeBacklog`의 Desktop별 귀속 부재 (Observability) — **완료 (C19)**

C19에서 구현했다. 선결 조건이었던 "`rejected/` 파일이 항상 `source`를 파싱
가능한 형태로 담고 있는가"의 답은 **아니다**였고(실 runtime 4건 중 4건이
귀속 불가), 그래서 allowlist + 잔여 카운트로 설계했다. 상세는 C19 §1.

아래는 발견 당시의 원문이다.

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

### E-11. Traceability — "고쳤다"는 기록을 저장소가 검증하지 않는다 (C19 신규)

C19 §0이 드러낸 구조적 문제다. `BACKLOG.md`는 Sprint 결과를 **산문으로만**
기록하고, 그 서술이 저장소 상태와 일치하는지 확인하는 장치가 없다. 그래서
C17이 존재하지 않는 수정 6건을 "완료"로 남겼고, C18이 그 위에서 "결함 없음"을
결론냈고, 두 Sprint 뒤에야 코드 대조로 발견됐다.

이번에 실제로 통했던 대조 수단은 셋이다:

| 수단 | 무엇을 잡았나 |
|---|---|
| `grep`으로 함수 이름 존재 확인 | 6건 전부 |
| 특성화 테스트의 자기 예고 문구 | find-before-create (테스트가 "내가 실패하면 고쳐진 것"이라 적어 둠) |
| Regression 건수 비교 (1466 vs 1426) | 누락 규모 40건 |

셋째가 특히 값싸다 — 이전 Sprint가 적어 둔 숫자와 지금 실측이 다르면 무언가
어긋난 것이다.

**왜 승인이 필요한가:** 자동화하려면 BACKLOG에 기계가 읽을 수 있는 형식을
도입해야 한다(Sprint별 "이 심볼이 존재해야 한다" 목록 등). 이 파일의 형식은
지금까지 순수 산문이었고, 형식을 정하는 것은 문서 정책 결정이다. **SKIP.**

**승인 없이 지금 할 수 있는 완화:** 매 Sprint 시작 시 (1) BACKLOG가 주장하는
심볼을 grep으로 확인하고 (2) 직전 기록의 Regression 건수를 실측과 비교한다.
C19가 실제로 그렇게 시작했고 그 두 단계에서 전부 잡혔다.

### E-12. 귀속 불가 파일의 **파일명**은 여전히 보이지 않는다 (C19 신규, 작음)

C19 §1의 귀속은 "출처불명 N건"까지 답한다. 어느 *파일*인지는 여전히
`runtime/events/rejected/`를 열어야 안다. `unreadable_events`는 이미 파일명을
5개까지 출력하므로 같은 처리를 못 할 이유는 없어 보인다.

**하지만 이름의 출처가 다르다.** `processed/`의 파일명은 전부
`safe_event_filename()`을 거친 것이고, `rejected/`에는 그렇지 않은 것이 섞인다
— 실 runtime에 이미 OneDrive 충돌 본 사본이 임의 이름으로 들어와 있다
(`...의 충돌 본ســ.json`). 즉 미검증 문자열을 터미널에 출력하는 문제이고,
E-10에서 `DESKTOP_9`을 출력하지 않기로 한 것과 **같은 판단**을 다시 해야
한다. 어디까지 escape할 것인가(`oplog.one_line()` 재사용? 별도 표시용
sanitizer?)는 출력 계약 결정이다. **SKIP.**

우선순위 낮음: 개수는 이미 보이고, 파일들은 사라지지 않으며, `rejected/`는
정상 운영에서 거의 비어 있다.

### E-13. docs/14 §7이 Lock 미획득 실행의 Exit Code를 명시하지 않는다 (C18 발견, C19 재확인)

§7은 "Lock을 얻지 못한 실행은 Manifest를 쓰지 않는다"까지만 말하고 그 실행이
무엇으로 끝나는지는 적지 않는다. 실제 동작은 `run_once()`가 `None`을 반환하고
`run_company_ops._print_result()`가 `[SKIPPED]`를 출력하며 **0**을 반환하는
것이고, 이는 일관되며 테스트도 있다.

버그가 아니라 문서 완결성 항목이며, `docs/`는 Spec 영역이다. **SKIP.**

### E-14. Backup Log (docs/08 §68-69)가 구현돼 있지 않다 (C20 신규)

§68은 9개 최소 필드를, §69는 `runtime/logs/backup/`을 위치로 정한다.
`BackupLogEntry`는 정확히 그 9개 필드를 갖고 `to_dict`/`to_json`/`from_dict`/
`from_json` 왕복까지 완비돼 있는데 — **넷 다 호출자가 없다.**
`backup.runner.run_once()`가 entry를 만들어 반환하면 `app/runner.py`가 속성
몇 개를 Run Manifest와 Dashboard로 옮길 뿐, 디스크에 닿는 곳이 없다.

전체 suite가 `src/`의 어느 줄을 한 번도 실행하지 않는지 측정하다 발견했다.
완성된 왕복 API 4개가 전부 0회 실행이면 "존재하지 않는 writer를 위해 쓰인
코드"라는 뜻이다.

**왜 SKIP인가:** 쓰려면 `runtime/logs/backup/`이라는 **새 영구 산출물 경로**를
만들어야 한다. docs/14 §2가 Artifact Taxonomy를 고정하면서 "신설한 것은
`runtime/runs/last_run.json` 하나뿐"을 의도된 성질로 명시하고, 그 `logs/`
목록에는 collector / notion_sync / daily_late_update만 있다. 추가하려면
Taxonomy와 `_ARTIFACT_REFS`를 함께 바꿔야 하고 `docs/`는 Spec이다.

**그동안 잃는 것:** §68의 9개 필드 중 타임스탬프 둘을 뺀 전부는 이미
Run Manifest의 `backup` component(`commit_hash`, 실패 사유로서의
`push_result`, `changed_files` 개수)와 `backup_state.json`
(`last_successful_backup` / `last_backup_commit` / `backup_status`)로 운영자에게
도달한다. 없는 것은 그것들의 **실행별 이력**(한 실행 한 줄, 시간에 걸쳐 보존)
이며 그게 §68이 요구하는 것이다.

현 상태는 `test_architecture_invariants.py::BackupLogIsNeverPersistedTests`
5건이 고정한다(GAP-11의 `test_ops_backup_builder_has_no_caller`와 같은 방식).

### E-15. Secret Scan이 백업 대상 밖의 파일로 Backup을 영구 실패시킨다 (C20 신규)

`scan_for_secrets(master_dir)`는 Local Master **전체**를 훑는다. 그런데
동기화 대상은 `_is_in_scope()`가 `daily/`와 `monthly/`로 제한한다(docs/08 §26).

**측정:**

    master/.env                    -> 스캔 적중, Backup FAILED
    master/notes/id_rsa            -> 스캔 적중, Backup FAILED
    sync가 실제로 복사하는 파일    -> daily\2026-08-05.md 뿐

즉 **원격에 절대 도달할 수 없는 파일** 때문에 Backup이 `BACKUP_FAILED`
(PERMANENT / CRITICAL)로 끝나고, 사람이 그 파일을 치울 때까지 Company History가
백업되지 않는다. §29의 문구는 "Backup 전에 최소한 알려진 Secret 파일이
**포함되지 않았는지** 확인한다"이고, 무엇이 "포함"인지는 §26이 정하므로
현재 스캔 범위는 §29가 요구하는 것보다 넓다.

**C24 갱신:** 같은 게이트의 반대 증상이 발견됐다 — **E-21**(게이트가 git이
커밋하는 디렉터리를 보지 않아 거짓 음성). 두 항목은 "게이트를 어느 디렉터리에
겨눌 것인가" 하나의 결정을 가리키며 **함께 닫힌다.**

**왜 SKIP인가:** 범위를 좁히는 것은 **보안 게이트를 좁히는** 일이다. 이전
Sprint가 "적중 시 Backup 전체를 실패시킨다"를 명시적으로 결정한 게이트이고
(Issue #3 결정 이력), 지금 out-of-scope인 디렉터리가 나중에 in-scope가 되는
경우까지 포함해 판단해야 한다. 코드 정리가 아니라 정책 결정이다.

**완화되어 있는 것:** 실패 방향이 안전한 쪽이다(유출이 아니라 중단). 운영자는
`push_result`에 적힌 `secret files detected: <경로>`로 정확히 어느 파일인지
본다. 위험한 것은 "조용히 통과"가 아니라 "요란하게 멈춤"이므로 우선순위는
낮다.

### E-16. 도달 불가능한 `recorder.skipped(C_DASHBOARD)` (C20 신규, 아주 작음)

`app/runner.py`의 Dashboard 단계는 `if dashboard_client is not None:` 안에서
`dashboard_result.outcome is DashboardOutcome.SKIPPED_NOT_CONFIGURED`를 검사해
`recorder.skipped(C_DASHBOARD)`를 부른다. 그러나 `record_run()`이 그 결과를
반환하는 조건은 `client is None` 하나뿐이고, 이 분기에서 client는 절대
None이 아니다. **도달 불가.**

미실행 줄 측정에서 나왔다. 방어적 코드로 해롭지는 않으나, 읽는 사람에게
"Dashboard가 설정됐는데도 SKIPPED가 될 수 있다"는 잘못된 인상을 준다.
제거는 삭제이므로 하지 않았고, `record_run()`의 계약 쪽을 바꾸는 것도
이 항목 하나를 위해서는 과하다. 기록만 남긴다.

### E-17. 실패한 Late Event 병합은 재시도되지 않는다 (C20 신규, **데이터 유실**)

**항목:** `update_daily_history()`가 실패하면 그 Late Event는 어떤 후속 실행도
다시 시도하지 않는다. Candidate는 `keep/`에 남아 있지만 자동으로 도달할
경로가 없다.

**원인:** 6.5단계의 대상 날짜 `kept_dates`는 **이번 실행에서** Candidate가
새로 쓰인 날짜만 담는다(`app/runner.py` 5단계). 병합이 실패한 날짜를 기억하는
곳이 없으므로 다음 실행은 그 날짜를 쳐다볼 이유가 없다.

**측정**(실 Runner, 손상된 Daily + 같은 날짜 Late Event):

| 실행 | late_update | overall / exit | Daily에 Event 있음? |
|---|---|---|---|
| 2 (손상 상태) | `LATE_EVENT_MERGE_FAILED` **RETRYABLE** | DEGRADED / 3 | 아니오 |
| 3 (사람이 파일 복구) | SUCCESS `updated=0` | **SUCCESS / 0** | 아니오 |
| 4 | SUCCESS `updated=0` | **SUCCESS / 0** | 아니오 |
| 5 (**무관한** 새 Event가 같은 날짜에 도착) | SUCCESS `updated=1` | SUCCESS / 0 | 예 |

즉 파일을 고쳐도 아무 일도 일어나지 않고 Company History에 Event 하나가 비어
있다. (**"모든 지표가 정상을 보고하는 채로"는 C40 기준 더 이상 사실이 아니다** —
`ops_status._kept_but_not_rendered()`가 "저장된 KEEP Candidate인데 그 날짜의
Daily에 없다"를 ATTENTION에 올린다. 재시도가 없다는 것은 그대로다.) 우연히 같은 날짜에 다른 Event가
도착해야만 복구된다 — 지나간 날짜에는 사실상 일어나지 않는다.
README RULE 7("Event와 History가 영구 손실되어서는 안 된다")에 걸린다.

**C20에서 고친 것(관측성, 메커니즘 변경 없음):** 분류를 `RETRYABLE` →
`PERMANENT`로 정정했다. docs/14 §5는 RETRYABLE을 "없음. 다음 실행이
재시도한다", PERMANENT를 "지금 개입해야 한다(`ops_status.py` ATTENTION에
뜬다)"로 정의하고, `ops_status.py`는 그 정의를 문자 그대로 지켜 PERMANENT만
나열한다. 재시도가 실제로 일어나지 않으므로 RETRYABLE은 **사실과 다른 값**
이었고, 그 값 하나 때문에 이 실패가 어디에도 나타나지 않았다. 이제 ATTENTION에
뜨고 exit 3이 유지된다.

**왜 진짜 수정은 SKIP인가:** 재시도를 실제로 만들려면 둘 중 하나가 필요하다.

1. 실패한 날짜를 **영속화** → 새 state 파일(또는 기존 state의 스키마 변경)
2. `keep/`의 Candidate와 Daily 파일을 대조하는 **정합성 패스** → 새 복구
   메커니즘(A-20이 걸려 있는 것과 같은 종류의 결정)

둘 다 값 하나를 고치는 일이 아니라 메커니즘 추가다. 게다가 2번은 6.5단계의
범위를 "이번 실행의 Event 보정"에서 "전체 History 대조"로 넓히는 것이고,
`app/runner.py`의 주석이 그 범위를 명시적으로 좁혀 두었다("대상 날짜는
5단계에서 모은 `kept_dates`뿐이다"). **SKIP.**

**다음에 필요한 조건:** "실패한 Late 병합을 어디에 기억할 것인가"에 대한 결정.
정해지면 구현은 작다 — 6.5단계는 이미 실패 날짜 목록(`late_update_failures`)을
손에 들고 있다.

**Evidence:** `tests/test_runner_notion_integration.py::
DegradedStepDoesNotAbortCriticalStepsTests::test_repairing_the_file_alone_does_not_bring_the_late_event_back`
와 `::test_a_new_event_on_the_same_date_pulls_the_stranded_one_in`이 범위를
정확히 고정한다.

### E-18. `subprocess` 출력 디코딩 — 조용한 `stdout=None` (C20에서 방어함)

`text=True`만 준 `subprocess.run(capture_output=True)`은 Windows 로케일
코드페이지로 디코딩한다(이 머신: cp949). 실패하면 예외가 **리더 스레드**에서
발생해 호출자에게 전파되지 않고 `result.stdout`이 조용히 `None`이 된다.
실측:

    git show HEAD:note.md   (내용에 em dash 하나)
    text=True  -> stdout None, returncode 0, stderr ''
    bytes      -> b'Company History \xe2\x80\x94 em dash\n'

`backup/git_ops._run_git()`가 그 형태였다. 현재 출력이 ASCII인 것은 우연이
아니라 **기본값의 사슬** 덕분이다(Daily/Monthly 파일명이 ISO 날짜, git의
`core.quotepath`가 비ASCII 경로를 이스케이프, git 메시지가 영문). 사슬 중
하나만 달라지면 `_parse_porcelain(None)`이 `AttributeError`를 던지고, 그것은
`GitOperationError`가 아니므로 `backup/runner.py`의 분류를 빠져나가 실행을
중단시킨다.

**C20에서 한 것:** `encoding="utf-8", errors="replace"` 추가 — ASCII 출력에
대한 동작은 그대로이고, 디코딩 실패라는 실패 모드 자체가 사라진다. 같은
이유로 `tests/test_runner_notion_integration.py`의 테스트 헬퍼도 고쳤다(그
헬퍼는 실제로 이 버그를 밟고 있었다 — `git show`로 Daily 파일을 읽는 순간
em dash에서 깨졌다).

SKIP 항목이 아니라 기록용이다. 저장소 전체에서 `subprocess`를 쓰는 곳은
`backup/git_ops.py`와 `scheduler/lock.py` 둘뿐이고, 후자는 `tasklist`의
숫자 PID만 읽으므로 해당 없음.

### E-19. BUG-29 — Notion `Last Updated`가 비교 불가일 때 Late Event Guard가 예외를 던진다 (C21 재측정, 여전히 SKIP)

기존 특성화(`tests/test_runner_failure_paths.py::NotionLastUpdatedParsingTests`)가
이미 고정해 둔 결함인데 **BACKLOG에는 항목이 없었다.** C21에서 독립적으로
다시 발견했고, 그 과정에서 특성화가 갖고 있지 않던 **운영 수준 측정**이
나왔으므로 여기 기록한다.

**동작:** docs/04 §29-30의 Late Event Guard는

    datetime.fromisoformat(event.timestamp) <= datetime.fromisoformat(current_last_updated)

인데 `event.timestamp`는 항상 offset을 갖고(docs/02) Notion의 `Last Updated`는
사람이 편집하는 date property다. Notion이 돌려주는 형태는 셋이다.

| 저장된 값 | 결과 |
|---|---|
| `2026-08-05T10:00:00+09:00` | 정상 |
| `2026-08-05T10:00:00` (time zone 미설정) | `TypeError` |
| `2026-08-05` (date only — UI 기본값) | `TypeError` |
| `""` / 파싱 불가 | `ValueError` |

**C21 신규 측정(실 Runner, `Last Updated`를 `"2026-08-01T10:00:00"`으로):**

    run 1: notion_sync=FAILED/UNKNOWN  queue=1  attempts=[1]
    run 2: notion_sync=FAILED/UNKNOWN  queue=2  attempts=[2, 1]
    run 3: notion_sync=FAILED/UNKNOWN  queue=3  attempts=[3, 2, 1]
    reason: "TypeError: can't compare offset-naive and offset-aware datetimes"

즉 Retry Queue가 **매 실행마다 한 건씩 무한 증식**하고, 운영자가 보는 사유는
Notion을 한 글자도 언급하지 않는 `TypeError`다. §62가 금지하는 무한 재시도이며
BUG-13이 다른 곳에서 닫은 불투명 분류다. 사람이 Notion UI에서 날짜를 한 번
고르면 그 Project의 Sync가 영구히 막힌다.

**왜 여전히 SKIP인가:** 특성화가 적어 둔 대로 "무엇을 신뢰할 것인가"의 결정이다.
비교 불가일 때 (a) 그대로 update 진행 (b) not-newer로 간주해 skip
(c) RETRY_REQUIRED로 거부 — 셋 다 판단이고, 셋 다 다른 실패 모드를 남긴다.
C21에서 (a)를 구현했다가 **되돌렸다** — 그 선택 자체가 이 항목이 기다리는
결정이기 때문이다.

**함께 확인된 사실:** 같은 모양(두 `fromisoformat` 결과를 naive/aware 고려 없이
비교)이 `app/desktop_activity._before()`에도 있었고 **그쪽은 고쳤다.** 차이는
문서다 — `_before()`의 docstring이 "비교할 수 없으면 문자열 순서로 폴백한다"를
**이미 약속**하고 있어서, `TypeError`를 그 폴백으로 보내는 것은 결정이 아니라
쓰여 있는 계약의 구현이다. §29-30에는 그런 폴백 조항이 없다.

**다음에 필요한 조건:** "Notion이 비교 불가능한 `Last Updated`를 갖고 있을 때
Current State를 갱신할 것인가"에 대한 결정. 정해지면 구현은 한 함수다.

**완화(C30에서 확인, C27이 의도치 않게 만든 것):** C21이 측정할 때는 큐가 매 실행
한 건씩 늘어난다는 사실이 어디에도 표시되지 않았다. C27 §6이 실패한 component의
metrics를 출력하게 만들면서 그것이 화면에 올라왔다 — 실측:

    ! notion_sync: NOTION_SYNC_INCOMPLETE [DEGRADED/UNKNOWN]
          processed=3 queued=3

큐 깊이가 실행마다 커지는 것이 이제 보인다(queued=1 → 2 → 3). 사유 문자열이
Notion을 언급하지 않는다는 점은 그대로다. C27 §6은 이 항목을 겨냥한 것이
아니었고(Manifest metrics를 아무도 읽지 않는다는 별개 결함), 부수적으로 이
항목을 덜 위험하게 만들었다.

**구조 가드:** `NaiveAwareComparisonGuardTests`가 이 계열을 스캔하며 `sync.py`
하나만 알려진 예외로 허용한다 — 두 번째 지점이 생기면 즉시 실패한다.

### E-20. REVIEW Candidate는 Company History에 도달할 경로가 없다 (C22 신규)

**측정:** `history_candidate: true`인 `BLOCKED` / `COMPLETED` / `CANCELLED`
Event는 `HistoryFilter`가 REVIEW로 판정해 `runtime/history_candidates/review/`에
저장한다(docs/05 §24가 이 셋을 그대로 예시로 든다). 그 다음이 없다.

| 경로 | 결과 |
|---|---|
| `generate_daily_history()` | `decision=KEEP`만 읽는다 → 렌더링 안 됨 |
| `submit_review()` | Decision Context 4개 필드만 쓴다 — `filter_result`는 건드리지 않는다 |
| 그 외 | **없다** |

실 Runner로 끝까지 측정: `COMPLETED` Event가 Daily에 없음 → 사람이 Decision
Context를 채워 리뷰 → **이후 2회 실행 후에도 여전히 없음.** `filter_result`는
계속 `REVIEW`다.

**데이터 유실은 아니다** — 후보 파일은 그대로 있고 사람이 읽을 수 있다.
`find_orphaned_events()`가 조용한 것도 정확하다(후보가 *존재*하므로). 문제는
그 더미를 아무도 세지 않았다는 것이다. 비교 대상은 전부 카운터가 있었다:
`rejected/`(C19에서 Desktop별 귀속까지 추가), `signals_rejected/`,
Orphan Event. **`review/`만 없었다.**

**C22에서 고친 것(관측성):** `ops_status.py`의 HISTORY 블록에 "검토 대기
Candidate" 카운트를 추가하고 ATTENTION에 올린다. **C26 보정:** 처음에는 더미
전체에 경고했는데, 리뷰를 마쳐도 파일이 `review/`를 떠나지 않으므로 올바른
조치로 지워지지 않는 경고였다. 이제 **미검토분에만** 경고하고 총계·내역은
블록에 남는다. 문구는 사실만
말한다 — 이 건들은 아직 Company History에 없고 어떤 실행도 넣지 않는다.

이것은 정책 결정이 아니라 **명세가 요구하는 신호를 읽는 것**이다. docs/05 §50이
직접 그렇게 규정한다:

    REVIEW가 너무 많다
          ↓
    자동화 실패 신호

그리고 "COO가 매일 수십 개의 REVIEW를 수동 처리해야 하는 구조를 만들지
않는다"고 덧붙인다. 둘 다 숫자를 보는 사람이 없으면 작동할 수 없는 규칙이다.
임계값은 정하지 않았다 — 그것이야말로 정책이므로, 카운트는 항상 표시하고
0이 아닐 때만 사실을 말한다.

**왜 승격(promotion)은 SKIP인가:** REVIEW를 KEEP으로 올리는 경로를 만드는 것은
셋 다 결정이다. (1) 누가/무엇이 승격을 판정하는가, (2) 이미 닫힌 Daily 파일에
어떻게 반영하는가(docs/06 §57이 COO 수기 수정을 보호한다 — A-14와 같은 벽),
(3) 그것이 Late Event Update인가 재렌더링인가. 게다가 §50 자체가 승격 경로를
만들기보다 **규칙을 조여 KEEP/DROP으로 확정하라**고 방향을 준다("가능한 경우
명확한 규칙으로 KEEP 또는 DROP한다"). `history/review.py`도 "promoting a REVIEW
candidate to KEEP is not part of this Phase"라고 명시한다. **SKIP.**

**다음에 필요한 조건:** 둘 중 하나. (a) BLOCKED/COMPLETED/CANCELLED의 자동
규칙을 확정한다(docs/05 §25-26 변경) — §50이 권하는 방향. (b) 승격 경로와 그
결과가 닫힌 Daily에 어떻게 반영되는지 정한다(docs/06 §37/§57 관련).

**Evidence:** `tests/test_history_review.py::ReviewCandidatesReachNothingTests`
6건이 경계를 정확히 고정하고,
`::ReviewBacklogInStatusViewTests` 4건이 새 카운터를 고정한다.

**관계:** A-14(리뷰로 채운 Decision Context가 어디에도 도달하지 않는다)와 같은
벽의 다른 면이다. A-14는 *이미 KEEP인* 후보의 보강 내용이 닫힌 Daily에 반영되지
않는 문제이고, E-20은 *REVIEW인* 후보가 애초에 Daily에 들어가지 못하는 문제다.
둘 다 "닫힌 Daily 파일을 어떻게 고칠 것인가"라는 하나의 결정에 걸려 있다.

### E-22. 대소문자만 다른 `event_id`는 Windows에서 한 파일이 된다 (C27 신규, **데이터 유실**)

**항목:** `safe_event_filename()`은 id를 바꿔야 할 때마다 sha256 digest를 붙여
**서로 다른 두 id가 한 파일명을 공유하지 않도록** 보장한다. 그 보장은 대소문자를
구분하지 않는 파일시스템에서 성립하지 않는다 — 배포 대상은 Windows다(docs/11).

    safe_event_filename("EVT-a")  ->  "EVT-a.json"     (안전한 id, 그대로 반환)
    safe_event_filename("EVT-A")  ->  "EVT-A.json"     (안전한 id, 그대로 반환)
    Windows                       ->  같은 경로

**측정:** `processed/EVT-A.json`이 이미 있는 상태에서 `EVT-a`가 도착 —
`run_intake()`가 `skipped_already_present`로 판정하고 파일을 `transport/`에 남긴다.
Collector는 그 Event를 **한 번도 보지 못하고**, `event_id`가 다르므로 seen store의
중복 판정도 걸리지 않으며, Company History에 그 Event는 **없다**. 실패한 단계도,
비정상 exit code도 없다.

**docs/02는 이것을 금지하지 않는다.** `event_id`에 대한 제약은 "present and
non-null"과 유일성뿐이고, `EVT-a`와 `EVT-A`는 **서로 다른 유일한 id 두 개**로
완전히 적법하다. 파일시스템이 그 둘을 접는다.

**왜 SKIP인가:** 후보 수정 셋이 전부 승인된 계약을 바꾼다.

| 후보 | 무엇이 바뀌는가 |
|---|---|
| `safe_event_filename()`이 대문자를 포함한 id에도 digest를 붙인다 | CEO 승인 B안의 핵심 성질 1("이미 안전한 id는 **그대로** 반환한다 → 기존 후보가 절대 rename되지 않는다")을 깬다. 현재 저장된 모든 Event/Candidate 파일명이 바뀐다 |
| 이름을 casefold해서 저장한다 | 같은 성질을 깨고, 게다가 `EVT-a`와 `EVT-A`를 **의도적으로** 한 Event로 합친다 — docs/02가 둘을 다른 Event로 규정하므로 스키마 해석 변경 |
| docs/02가 `event_id`를 case-insensitive-unique로 못박는다 | 스키마 변경(A-15와 같은 벽) |

**C27에서 한 것(탐지만):** `IntakeBacklog.suppressed` — `transport/`의 파일이
downstream 쌍둥이에 막혀 있는데 **그 쌍둥이의 `event_id`가 다를 때** ATTENTION에
올린다. 이름이 아니라 id를 비교하기 때문에 이 경우가 잡힌다. BUG-53(디렉터리)·
BUG-47(0바이트)과 같은 줄에 보고된다 — 셋 다 "전달되지 않은 Event가 자기가 아닌
파일에 막혀 있다"는 하나의 사실이기 때문이다.

**완화되어 있는 것 — C28에서 추측이 아니라 측정으로 확정했다.** 이전 문장은
"가능성은 낮다"였다. 실제로는 **Desktop 1~3의 Signal 경로로는 구조적으로
불가능하다.**

    derive_event_id() -> uuid5(namespace, "source|date|signal_id")
    str(uuid5(...))   -> 소문자 hex, charset [0-9a-f-] 뿐 (실측)

따라서 Agent가 만드는 두 id는 대소문자만 다를 수 **없다.** 입력으로 끼워 넣을
수도 없다: `signal_id`는 Signal **파일의 stem**인데, E-22가 존재하는 바로 그
대소문자 무구분 파일시스템에서는 이름만 다른 두 파일이 **한 파일**이고,
구분하는 파일시스템에서는 두 개의 **완전히 다른** uuid5가 나온다(변형이 아니다).
게다가 `event_id`는 **Signal에서 금지된 필드**다(AGENT.md §3 — 넣으면 Signal
전체가 거부된다).

**남는 표면:** `event_id`를 직접 지정하는 Event — `reporter`의
`create_event(event_id=...)`, 또는 이 저장소 밖의 도구가 transport 폴더에 쓰는
경우. 실재하지만 "모든 Event"보다 훨씬 좁고, **이것이 E-22가 BUG-55보다 아래에
놓이는 이유다.**

**Evidence(추가):** `tests/test_agent.py::EventIdCannotCaseCollideFromTheAgentTests`
5건. 유도 방식이 uuid5를 벗어나면(대소문자 섞인 해시, Signal 본문 사용 등) 이
테스트가 깨지고 좁힘을 다시 증명해야 한다.

**다음에 필요한 조건:** "`event_id`의 유일성은 case-sensitive인가"에 대한 결정.
정해지면 구현은 작다 — 한쪽은 `safe_event_filename()` 두 사본에 조건 하나,
다른 쪽은 docs/02 §26의 Validation 한 줄이다.

**Evidence:** `tests/test_observability.py::SuppressedDeliveryTests::
test_a_case_only_filename_collision_is_a_suppressed_delivery`가 경계를 고정한다
(대소문자를 구분하는 파일시스템에서는 관측할 충돌이 없으므로 전제를 먼저 확인한
뒤 skip한다).

### E-23. 같은 날짜의 두 번째 Signal은 Notion에 도달하지 않는다 (C27 신규, **데이터 분기**)

**두 명세가 각각 옳고, 유실은 그 사이에 있다.**

**docs/04 §29-30 (Late Event 보호):** 프로젝트의 `Last Updated`보다 **과거이거나
동시**인 timestamp의 Event는 Current State를 되돌리지 않는다.
`notion/sync.py::_update()`가 `<=`로 구현하고, "**동시**"는 의도적으로 규칙에
쓰여 있다.

**docs/06 §12 / `agent/agent.py::_default_timestamp()`:** 자기 timestamp가 없는
Signal은 **그 날짜의 자정**을 받는다. 이것도 의도적이다 — 그 함수의 docstring:
*"the one value on that date that is the same for every Signal and for every
re-run"*. catch-up이 "PC가 켜진 날"이 아니라 "일이 있었던 날"에 Event를 넣게
하는 근거다.

**둘을 합치면 "동시"는 드문 동점이 아니라 정상 경로가 된다.** 한 날짜에 대해
timestamp 없이 쓴 모든 Signal이 **같은 timestamp**를 받으므로, 한 프로젝트에
대해서는 **그날의 첫 Event만 Notion에 도달한다.**

**측정**(서로 다른 `event_id` 2개, 같은 프로젝트, 둘 다 `2026-08-10T00:00:00+09:00`):

    EVT-1   NOTION_CREATED
    EVT-2   NOTION_SKIPPED_OLD_EVENT
    Notion에 도달한 update 호출: 0건

**무엇이 갈라지는가:** Company History는 **둘 다** 보존한다(Daily는 timestamp로
묶고 모든 KEEP 후보를 렌더링한다). Notion 프로젝트 행은 **첫 번째만** 반영한다.
그리고 아무것도 보고하지 않는다 — `NOTION_SKIPPED_OLD_EVENT`는
`app/runner.py::_FAILED_SYNC_STATUSES`에 없으므로 component는 `recorder.ok()`,
실행은 SUCCESS, exit 0이고, 유일한 흔적은 아무도 읽지 않는 `notion_sync.log`
한 줄이다.

**왜 SKIP인가:** 후보 수정 셋이 전부 명세 변경이다.

| 후보 | 무엇이 바뀌는가 |
|---|---|
| `<=` → `<` | docs/04 §29-30의 명시적 "동시" 규칙을 뒤집는다. 진짜로 동시에 도착한 Late Event가 Current State를 덮어쓸 수 있게 되며, 그것이 이 게이트의 존재 이유다 |
| 기본 timestamp의 해상도를 높인다 | docs/06 §12의 "모든 Signal과 모든 재실행에 대해 같은 값"을 포기한다 — catch-up의 결정성이 거기서 나온다 |
| `event_id`로 동점 처리 | 두 명세 어디에도 없는 순서를 발명한다 |

**완화되어 있는 것:** Signal에 timestamp를 **명시하면** 이 경로를 전혀 타지
않는다(1초만 달라도 정상 적용된다 — 테스트로 고정). 그리고 이 배포에서는 Notion이
아직 미설정이라 현재는 잠재적이다. **Company History는 영향을 받지 않는다** —
유실되는 것은 Notion 쪽 Current State의 최신성뿐이다.

**C43 — 얼마나 오래 갈라져 있는가 (신규 측정, 항목을 좁힌다).** 이 기록은 무엇이
갈라지는지는 말하고 **얼마나 오래 갈라지는지**는 말하지 않았다. 그것이 열린 결정의
시급도를 정하는 사실이다. 실 Agent → 실 Runner → `ExecutionPlanSync`로 끝까지
돌려 측정했다:

    timestamp 없는 Signal 2개(같은 날짜, 같은 프로젝트)
        Company History   2026-08-05.md에 Event ID **둘 다**
        Notion 행         Last Event ID = 첫 번째
        manifest          notion_sync SUCCESS, same_instant_skips=1

    그 프로젝트에 대한 **평범한 다음 Event 하나**
        Notion 행         Last Event ID = 그 다음 Event
        Last Updated      그 Event의 timestamp

즉 **갈라짐은 영구가 아니라 한정적이다.** docs/14 §1이 Notion을 *Current State*
View로 정의하고, Current State는 그 프로젝트의 다음 Event에서 수렴한다. View에
영영 남지 않는 것은 "그 순간의 두 번째 Event도 적용됐다"는 사실뿐인데, 그것은
Current State projection이 애초에 담기로 한 정보가 아니다 — 그 log는 둘 다 지킨
Company History에 있다.

**E-23이 닫히는 것은 아니다.** 같은 순간의 두 Event 중 어느 것이 Current State인가는
여전히 docs/04 §29-30 대 docs/06 §12의 결정이고, **마지막이 아니라 첫 번째가
이기는 것**도 여전히 임의적이다. 바뀐 것은 시급도이며, 그것은 누군가의 머릿속이
아니라 기록에 있어야 한다.
**Evidence:** `tests/test_notion_sync.py::TheSameInstantDivergenceHealsTests` 5건
(전제 / 다음 Event가 되돌린다 / 며칠 뒤여도 된다 / 다른 프로젝트는 영향 없음 /
그럼에도 중간 상태는 View에 남지 않는다).

**다음에 필요한 조건:** "같은 초의 두 Event 중 어느 것이 Current State인가"에
대한 결정. docs/04와 docs/06 중 어느 쪽을 조정할지가 함께 정해져야 한다.

**Evidence:** `tests/test_notion_sync.py::SameTimestampDifferentEventTests` 6건.
기존 `MockTest6`(같은 `event_id` 재도달, §62)·`MockTest7`(진짜로 더 오래된
timestamp, §63)과 구분되는 **제3의 경우**임을 명시적으로 단언하고, Agent가 정말
모든 Signal에 같은 값을 준다는 전제도 `_default_timestamp()`에서 직접 확인한다.

### F-6. C22에서 BUG-40 계열을 수정하며 그은 선

F 절을 만들면서 22건을 훑은 결과, **하나는 결정이 아니라 미구현**이었다.

`json.loads()`는 깊게 중첩된 입력에 `RecursionError`를 던진다 —
`RuntimeError` 서브클래스라 `except (OSError, ValueError)`가 덮지 못한다.
같은 모양의 `json.loads` 호출부가 6곳 있었고, 실측 결과 5곳이 예외를 밖으로
내보냈다.

| 지점 | 수정 전 | C22 |
|---|---|---|
| `agent/signals.py` | 이미 처리됨(`SignalError`) — **선례** | 그대로 |
| `transport.intake._is_parseable_json` | `RecursionError` 전파 → Runner 2단계 영구 정지 | **수정** |
| `app/desktop_activity._read_one` | 전파 → COMPANY 뷰 전체 사망 | **수정** |
| `agent/delivery._problem` + `_verdict` | 전파 → `ops_status.py` 사망 | **수정** |
| `collector.Collector.collect` | 전파 → 파일이 FAILED로 `incoming/`에 남아 영원히 재시도 | **수정**(REJECTED) |
| `history.FileHistoryRepository.list` | 전파 | **그대로 — BUG-38** |

판별 기준은 C21에서 쓴 것과 같다: **함수 자신의 문서가 "파싱할 수 없음"의
답을 이미 갖고 있는가.**

    _is_parseable_json   "exists precisely so an unparseable file is skipped
                          rather than crashing the run"          -> 구현
    _read_one            "Both failure kinds collapse to None on purpose"  -> 구현
    _problem             UNREADABLE이 선언된 4개 판정 중 하나        -> 구현
    Collector.collect    이미 "invalid JSON" -> REJECTED           -> 구현
    list()               아무 말도 없다                            -> **결정(BUG-38)**

`list()`만 남긴 이유가 그것이다. 격리/건너뛰기/정지 중 무엇이 옳은지는 A-7이
기다리는 Data Safety 결정이고, 인접한 넷을 고쳤다고 해서 그 결정이 내려지지는
않는다. `RecursionErrorIsUnparseableTests::test_a_candidate_repository_still_raises`가
그 경계를 **입장으로서** 고정한다 — 빠뜨린 것이 아니라 남겨 둔 것임을 분명히
하고, 결정이 내려지는 날 실패한다.

BUG-40의 기존 특성화(`IntakeRecursionErrorTests`)는 보증으로 다시 썼다.

### F-7. BUG-41 + E-14 복합 — 실패한 Backup은 흔적을 **하나도** 남기지 않는다 (C23 실측)

두 항목을 따로 보면 각각 견딜 만해 보인다. 함께 측정하니 아니었다.

**측정(실 Runner):** 정상 실행 → `backup_state`를 `BACKUP_FAILED`로 만든 뒤
→ 변경이 있는 다음 실행 1회.

| 남아 있을 법한 곳 | 결과 |
|---|---|
| `runtime/state/backup_state.json` | `BACKUP_SUCCESS` |
| `runtime/runs/last_run.json` | backup **SUCCESS**, overall SUCCESS, **exit 0** |
| `runtime/logs/` | `collector.log`, `notion_sync.log` — backup 로그 없음 |
| `runtime/logs/backup/` | **존재하지 않음**(E-14) |

**흔적 0.** 그리고 exit 0은 Task Scheduler의 Last Run Result가 되므로, 무인
배포에서 유일한 자동 신호가 "정상"을 보고한다.

**BUG-41의 원 기술보다 넓다.** 그 docstring은 *무변경* 경로(FAILED →
NOT_REQUIRED)를 지목하는데, 실측한 실행은 변경이 있어 **성공 경로**를 타고
`BACKUP_SUCCESS`를 그대로 썼다. 두 경로 모두 지운다.

**이 측정이 바꾸는 것:** E-14(Backup Log)의 성격이다. 지금까지 "docs/08 §68-69
Spec 미충족"으로만 기록돼 있었는데, 실제로는 **BUG-41을 견디는 유일한 내구
기록**이다. Manifest는 실행당 하나뿐이라 다음 실행이 덮어쓰고, `backup_state`는
현재 상태만 담는다. 실행별 이력을 갖는 곳은 Backup Log뿐이고 그것이 없다.

**여전히 SKIP:** FAILED 보존은 `backup_status`의 의미 변경(docs/08 §19/§21,
PENDING에 대한 같은 변경은 CEO 승인으로 이루어졌다), Backup Log 신설은
docs/14 §2 Artifact Taxonomy 변경. 둘 다 결정이다. 탐지로 우회할 수도 없다 —
이미 지워진 것을 `ops_status.py`가 볼 방법은 없다.

**Evidence:** `tests/test_runner_notion_integration.py::FailedBackupLeavesNoTraceTests`
4건. 마지막 테스트가 "내구 위치 세 곳의 흔적 수 = 0"을 한 줄로 고정하므로,
둘 중 어느 쪽이든 닫히는 날 실패하고 그때 살아남은 기록을 여기에 단언하면 된다.

### F-8. C23의 F 절 재평가 결과

C22가 만든 22건을 C21/C22의 판별 기준으로 다시 읽었다 — **함수 자신의 문서가
답을 갖고 있는가.**

| 항목 | 재평가 |
|---|---|
| **BUG-40** | 결정이 아니었다 → **C22에서 수정**(4개 지점) |
| **BUG-42** | 동작은 결정, 그러나 "아무 데도 알리지 않는다"는 **제3의 길** → **C23에서 탐지 추가** |
| **BUG-52** | **결정 유지.** 특성화가 이유를 명시한다 — marker를 넓히면 반대 오류(일시적 실패를 영구로 분류해 재시도를 멈춤)가 생기고, 선을 어디에 그을지가 판단이다 |
| **BUG-55** | **결정 유지.** 대소문자 정규화는 Linux에서 `Daily/`와 `daily/`가 실제로 다른 디렉터리이므로 크로스플랫폼 동작 변경 |
| **BUG-41** | **결정 유지**, 단 영향 범위는 F-7로 확대 측정 |
| **BUG-38** | **결정 유지.** C22가 인접 4곳을 고치면서 의도적으로 남긴 유일한 지점 |

패턴이 하나 굳어졌다: **"동작을 바꾸는 것"과 "동작을 보이게 하는 것"은 다른
결정이고, 후자는 거의 항상 승인이 필요 없다.** C19의 `is_locked`/
`lock_held_since`, C22의 `review/` 카운터, C23의 `stale_lock_cannot_be_cleared`가
전부 같은 형태다 — 아무것도 고치지 않고 아무것도 결정하지 않은 채, 조용하던
것을 시끄럽게 만든다.

### F-9. BUG-30 / BUG-46 — C23 재측정으로 좁혀진 경계

**BUG-30(시계 오차로 intake가 멈춤): 가시성만 닫았다.**

실측: 미래 mtime을 가진 파일 하나로 intake를 3회 실행 — 매번 `moved=0`,
`skipped_not_stable=1`, 운영자 화면에는 `transport=1`이 계속 떠 있고 **이유는
어디에도 없었다**. `skipped_not_stable`은 Run Manifest에 도달하지만
`_print_last_run()`은 SUCCESS component를 출력하지 않고 transport는 성공한다.

이것은 `IntakeBacklog` docstring이 `unparseable`을 위해 이미 적어 둔 형태다 —
"An alert that cannot clear is worse than no alert ... a permanent entry
trains people to skim past it." 그래서 `future_dated` 카운트를 추가하고
기존 backlog 문장에 이유를 덧붙였다.

**하지 않은 것:** `awaiting_intake`에서 빼지 않았고 `is_clear`도 바꾸지 않았다.
그런 파일이 "in flight"인지가 BUG-30이 남긴 판단이고, `unparseable`을 제외한
근거는 "영원히 parked됨이 증명된다"였는데 이쪽은 아니다. 없던 것은 숫자가
아니라 숫자가 움직이지 않는 이유였다.

**BUG-46(창 밖 Candidate): 기술이 실제보다 넓었다.**

실측(`history_start_date=2026-08-01`, `now=2026-08-05`):

| Candidate 날짜 | Daily 도달 | 성격 |
|---|---|---|
| 2026-08-03 (창 안) | 예 | 정상 |
| 2026-09-15 (미래) | 아니오 | **지연일 뿐** — 그 날짜가 어제가 되면 Scheduler가 렌더링한다 |
| 2026-07-20 (시작일 이전) | 아니오 | **영구** — Scheduler는 `history_start_date` 이전으로 가지 않는다 |

`find_orphaned_events()`는 clean을 보고한다(후보가 *존재*하므로 정확하다).

즉 BUG-46의 영구 손실 범위는 "창 밖" 전체가 아니라 **시작일 이전** 하나다.
미래 날짜는 자가 치유된다.

**탐지를 이번에 넣지 않은 이유:** 시작일 이전인지 판정하려면
`COMPANY_OPS_HISTORY_START_DATE`가 필요한데 `ops_status.py`는 그 값을 읽지
않는다(읽는 것은 `run_company_ops.py`뿐이고, 미설정일 수 있다). 설정이 없을 때
무엇을 보고할지가 또 하나의 판단이라, 조건만 정확히 적고 남긴다.

**~~다음에 필요한 조건~~ — C28에서 해소됨. 결정은 이미 내려져 있었다.**

이 항목은 "`ops_status.py`가 `COMPANY_OPS_HISTORY_START_DATE`를 읽어도 되는가
(미설정 시 동작 포함)"를 결정 사항으로 기록했다. **그 판단은 같은 파일이 이미
두 번 내려 두었다** — `COMPANY_OPS_AGENT_START_DATE`(`_agent_start_date()`)와
`COMPANY_OPS_AGENT_SYNC_FOLDER`가 모두 환경변수를 읽고, 없으면 **"미설정"이라고
적고 그 계산만 건너뛴다**(경보도 추측도 하지 않는다).

`_history_start_date()`는 `_agent_start_date()`와 같은 모양이다. 이미 있는 답을
적용하는 것은 새 정책이 아니다 — **없었던 것은 그 답이 존재한다는 인식이다.**

구현: `_candidates_before()` — `keep/`의 KEEP 후보 중 시작일보다 이른 것. C22가
좁혀 둔 대로 **미래 날짜는 제외한다**(자가 치유되므로 보고하면 스스로 지워지는
경보가 된다). `FileHistoryRepository.list()`는 쓰지 않는다(BUG-38).

**같은 해소가 두 번째 탐지도 열었다** — 해결 불가능한 `dirty_months`(C28 §6).

**Evidence:** `tests/test_observability.py::CandidatesBeforeTheHistoryStartTests`
10건, `::UnresolvableDirtyMonthTests` 6건. 그중 하나는 두 resolver가 미설정·
파싱 실패·정상 세 경우에서 **동일하게 동작하는지**를 나란히 단언한다 — 그
동일성이 "새 결정이 필요 없었다"는 논거 자체이기 때문이다.

**C27 추가 — 같은 결정이 막고 있는 두 번째 탐지.** 탐지기 전수 대조(C27 §9)에서
`monthly_history_state.json`의 `dirty_months`가 같은 벽에 걸린다는 것이 나왔다.

`monthly/generator.py`의 dirty 루프는 `history_start_date` 이전 달을 만나면
`MONTHLY_PENDING`을 반환하고 **플래그를 일부러 남긴다** — 그 코드의 주석이
이유를 적어 두었다: *"no run can resolve this one, and silently forgetting it
would hide a state file that needs a person."* 그런데 그 사람에게 도달하지
않는다. 추적하면:

    generator   MONTHLY_PENDING + error 문자열 반환
    runner      PENDING은 실패가 아니다(정상 경로에서는 옳다 — Daily 구멍은
                다음 실행이 채운다) -> late_update.log 한 줄
    ops_status  그 로그를 읽지 않는다 -> ATTENTION 없음, exit code 영향 없음

즉 **어떤 실행도 해결할 수 없는 dirty 플래그가 영구히 보이지 않는다.** 도달
경로는 손으로 고쳤거나 복원된 state 파일(DR 시나리오)이다 — `mark_month_dirty()`
자신은 통합되지 않은 달을 dirty로 만들지 않으므로 정상 경로로는 생기지 않는다.

판정에 필요한 값이 정확히 같다(`history_start_date`). 따라서 이 결정 하나가
**두 개의 탐지**를 막고 있다 — BUG-46의 시작일 이전 Candidate, 그리고 해결
불가능한 dirty month. 정해지면 둘 다 같이 닫힌다.

### F-10. BUG-43 — 가시성만 닫았다 (C24)

BUG-30·BUG-42와 같은 처리이고, 이유도 같다.

**측정:** `processed/`에 이미 있는 이름이 `incoming/`에 다시 나타난 상태로
Collector 3회 실행 — 매번 `accepted=0 failed=1`, 파일은 계속 `incoming/`에.
`ops_status.py`는 매번 `incoming=1`을 정확히 보고했고 **왜인지는 말하지
않았다.**

BUG-43의 docstring은 이 조건을 "at least visible"이라 적었는데, 재측정 결과
그 근거 두 가지가 모두 약하다:

- `collector_summary.failed`는 `run_company_ops.py`가 **stdout**에 찍는다 —
  Task Scheduler는 기본적으로 캡처하지 않는다.
- Run Manifest에는 들어가지만 **SUCCESS component의 metric**이고,
  `_print_last_run()`은 SUCCESS component를 출력하지 않는다.
- "exit code는 여전히 0 (BUG-36)"이라는 문장은 이제 반만 맞다. BUG-36은
  수정됐고, 여기서 exit 0인 것은 **의도된 것**이다 — collector component는
  docs/03 §53의 파일 단위 격리에 따라 SUCCESS가 맞다.

**추가한 것:** `IntakeBacklog.name_collision` — `incoming/`의 파일 중 이름이
이미 `processed/`나 `rejected/`에 있는 것의 수. 조건이 결정적이다:
`collector/runtime.run_once()`는 목적지 이름이 차 있으면 옮기지 않고 파일을
그대로 두며, 판정(ACCEPTED/DUPLICATE)은 둘 다 `processed/`를 목표로 하므로
이름 충돌은 **항상** 영구 FAILED다.

**비용 0.** `processed_paths`는 이미 순회 중인 목록이고 `rejected_paths`는
rejected 카운트에 필요하다. 실측: processed 10,000 + incoming/rejected 각 100에서
7.57초로, C21이 잰 기존 수치와 동일한 차수 — 지배 비용은 여전히 `processed/`
읽기이고 이름 집합 구성은 측정에 잡히지 않는다.

**하지 않은 것:** `awaiting_collection`에서 빼지 않았고 `is_clear`도 그대로다.
"이미 처리됨"의 두 개념(seen store vs 파일 이름)을 화해시키는 것 —
`processed/`에서 state를 재구성하거나, 이름 충돌을 실패가 아닌 중복으로
취급하거나 — 이 BUG-43이 기다리는 결정이다.

**Evidence:** `tests/test_observability.py::NameCollisionInIncomingTests`(7건),
`::StuckIncomingInStatusViewTests`(2건). 그중 하나는 카운트가 Collector의
실제 동작을 예측하는지를 3회 실행으로 확인한다 — 설명하려는 단계와 어긋나는
카운터는 없는 것보다 나쁘기 때문이다.
