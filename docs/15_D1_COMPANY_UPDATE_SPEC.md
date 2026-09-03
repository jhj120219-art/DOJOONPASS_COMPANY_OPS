# 15. D+1 Company Update — 역할별 KPI와 Git 기준 개발 변경

작성: C149

이 문서는 새 파이프라인을 정의하지 않는다. 이미 있는 읽기 경로
(`src/controltower/rollup.py` → `src/controltower/dashboard.py` →
`src/controltower/notion_page.py`)에 **무엇이 더해졌고 왜 그것이 별도의
원천이어야 하는지**를 적는다.

---

## 1. 해결한 문제

이 시스템의 모든 관점은 Execution Event에서 나왔다. Event는 사람이
"이런 일이 있었다"고 **보고하기로 결정했을 때만** 존재한다.

그래서 다음 두 날이 구별되지 않았다.

    아무도 일하지 않은 날
    일했지만 Event가 전달되지 않은 날

두 번째는 실제로 일어난다 — OneDrive가 가득 찼거나, 로그인이 풀렸거나,
그 Desktop이 꺼져 있었거나. 그리고 그 실패는 **신호가 없다**: Dashboard의
모든 표가 "조용한 날"을 그린다. 사람이 가장 알아야 할 날에 화면이 가장
평온해 보인다.

Git은 그 사실을 이미 들고 있다. 로그인할 계정도, 동기화할 폴더도 없이
기계 위에 있다.

---

## 2. 두 원천, 두 종류의 질문

    Event   판단  — "이 Milestone은 끝났다", "이건 막혔다",
                    "이 Decision이 필요하다"
    Git     사실  — "이 파일들이 이 시각에 이 사람에 의해 바뀌었다"

둘은 서로를 대체하지 않는다.

**Git을 회사의 Source of Truth로 만들지 않는다.** Git은 Project가
무엇인지, 어느 팀이 그것을 맡는지, 무엇이 막혔는지, 어떤 Decision이
기다리는지 알지 못한다. Commit에서 그중 어느 것도 유도되지 않는다.
그 전부는 `_OPEN_ITEM_LIFECYCLES`와 `ProjectRollup`이 든다.

**Event를 Git으로 대체하지 않는다.** Commit은 배포가 아니고, 완료도
아니고, 위험도 아니다. Commit 수로 Deployment Frequency를 만들면 DORA처럼
보이지만 production과 아무 관계가 없는 수가 되고, 그 수는 실제로 의사결정에
쓰인다 — `src/controltower/kpi.py`가 DORA 7종을 전부 `DATA REQUIRED`로 두는
이유다.

---

## 3. OneDrive의 위치

`src/transport/onedrive.py`는 **Desktop 사이에서 Event를 나르는** 방법이며
그대로 유지된다. `run_agent.py`가 만드는 유일한 production Transport다.

바뀐 것은 그것이 **D+1 보고의 필수 경로가 아니게 되었다**는 것이다.

    Transport가 살아 있음   Event + Git — 보고한 것과 실제로 바뀐 것
    Transport가 죽어 있음   Git만 — "그날 무슨 일이 있었는지" 여전히 답한다

`src/delivery/git_activity.py`는 네트워크도 계정도 쓰지 않는다. 로컬
저장소에서 `git log`와 `git rev-parse`만 실행하며, 둘 다 읽기다
(`tests/test_spec_conformance.py`의 `APPROVED_COMMANDS_ELSEWHERE`가 이
저장소 밖 모든 git 호출의 검토 게이트다).

읽지 못하면 **0을 보고하지 않는다**. `GitActivity.available = False`와 git
자신의 이유를 든다. "commit이 없었다"와 "git을 읽지 못했다"는 다른 문장이며,
그 구별이 이 모듈 전체의 존재 이유다.

### 3.1 이 기능이 실제로 값을 내는 한 줄

Event 0건인데 같은 기간 Git에 commit이 있으면, 두 화면(브라우저 판정과
Notion ① 배너)이 그것을 **말한다**:

> Event는 0건인데 같은 기간 Git에는 commit이 N건 있다 — 일이 없었던 것이
> 아니라 보고가 도착하지 않았을 가능성이 크다

실측(2026-09-02 하루 창): `events_read: 0`, `CODE_CHANGES` commit 1건·파일
21개. 그전에는 두 화면 다 "셀 Event가 없다"까지만 말했다 — 참이지만, 답이
바로 옆에 있다는 것은 말하지 않았다.

Git도 비어 있으면 이 문장은 나오지 않는다. 어긋남이 없는데 어긋남을 만들어
내는 것은 침묵보다 나쁘다.

---

## 4. Event Vocabulary — 이번에 닫은 비대칭

docs/02 §11.1이 전체를 담는다. 요약:

| 추가 | 대칭짝 | 없어서 불가능했던 것 |
|---|---|---|
| `ISSUE_RAISED` | `ISSUE_RESOLVED` | Issue Aging — 시작 시각이 없었다 |
| `DECISION_REQUIRED` | `DECISION_APPROVED` | Decision Aging, "무엇을 기다리는가" |
| `DECISION_REJECTED` | `DECISION_APPROVED` | 거절을 기록할 자리 |
| `AT_RISK` | `BLOCKED` | 아직 멈추지 않았지만 멈출 것 같은 상태 |
| `ASSIGNED` | (Issue 중간) | 아무도 안 맡은 Issue와 누가 붙은 Issue의 구분 |
| `EXECUTED` | `DECISION_APPROVED` | 승인해 놓고 하지 않은 것 |

Lifecycle 세 개가 이제 양끝을 모두 갖는다.

    Issue     ISSUE_RAISED → ASSIGNED → ISSUE_RESOLVED
    Decision  DECISION_REQUIRED → DECISION_APPROVED → EXECUTED
                                └→ DECISION_REJECTED (여기서 끝)
    Project   NOT_STARTED → IN_PROGRESS → AT_RISK / BLOCKED → COMPLETED

`ASSIGNED`는 아무것도 열지 않고 닫지도 않는다 — 나이는 여전히 제기 시각부터
잰다. `EXECUTED`가 없을 때 Decision Lifecycle은 **승인에서 끝났고**, 그래서
"정해 놓고 안 한 것"이 문제가 되기 시작하는 바로 그 순간 목록에서 사라졌다.

이 넷은 **구현되지 않은 KPI를 구현한 것이 아니다.** 그전에는 원리적으로
계산할 수 없었다 — 끝난 시각만으로는 얼마나 걸렸는지 알 수 없다.

---

## 5. KPI — 계산 가능한 것과 아닌 것

`src/controltower/kpi.py`.

    CEO   12개 — 전부 DATA REQUIRED
    CTO   10개 — DORA 7종 DATA REQUIRED, git 기반 3종 계산 가능
    COO   13개 — 10개 계산 가능, 3개 DATA REQUIRED

COO의 셋이 늘어난 것은 KPI를 더 쓴 것이 아니라 **Event 어휘가 그것을 셀 수
있게 됐기 때문**이다: `Unassigned Open Items`(ASSIGNED), `Unexecuted
Decisions`·`Execution Aging`(EXECUTED).

**이것은 결함 목록이 아니라 판정이다: 이 시스템은 실행을 재고 사업을
재지 않는다.** 매출도, 고객도, 계약도 Event Schema 13개 필드와 git commit
어디에도 없다. 어떻게 배열해도 나오지 않는다.

계산할 수 없는 KPI는 값 대신 `DATA REQUIRED`를 싣고, `requires`에 **무엇이
있어야 답할 수 있는지** 적는다. 그럴듯한 숫자를 만들지 않는다 — 추적할 수
없는 숫자가 소문이라면, 추적 가능해 보이는 조작된 숫자는 확인되고 실행되는
소문이다.

계산된 KPI는 전부 `rollup.Metric`의 **같은 수**이며 같은 증거 파일을 든다.
여기서 다시 세지 않는다.

### 5.1 연결 (Metric → Goal → Initiative → Project → Issue → Action)

정직한 답: **어떤 KPI도 Goal에 닿지 않는다.** Goal은 원천이 없다
(`rollup.UNSOURCED_LAYERS`). 계산된 KPI가 실제로 닿는 곳은
`Metric → Project → Issue`이며, `EvidenceRef`가 그 경로다 — 모든 수가
자기 Event 파일을 열어 보이고, 그 파일마다 `project_id`가 있다.

`Kpi.chain`이 KPI별로 이것을 적는다. 어느 것에도 "Goal에 연결됨"이라고
쓰지 않는다.

---

## 6. Dashboard 배치

새 Notion Database를 만들지 않는다. 기존 것을 재사용한다.

    ROLE_KPI       Notion Database 없음 — CT_METRICS에 이미 있는 같은 수다.
                   같은 수가 Notion 안 두 곳에 살면 어긋나는 날 어느 쪽이
                   맞는지 말해 줄 것이 없다. 사람이 읽는 페이지의 ③ 안에
                   역할별 toggle 셋으로 들어간다.
    CODE_CHANGES   Notion Database 없음 — commit 1건당 행 하나는 ACTIVITY와
                   같은 무한 성장이고, 원본은 저장소 자체다. 요약이 ⑤ 안에
                   문장으로, commit 목록이 toggle로 들어간다.

패널 제목은 `개발 변경 (Git)`이며 **`D+1`이 아니다.** 이 패널은 호출자가
패널에 요청한 창을 따르고, 창을 주지 않으면 증거 전체 범위가 된다. 실제로
띄워 보니 제목이 `D+1 개발 변경`인 채로 `2026-08-05 ~ 2026-08-10 · commit
6건`이 나왔다 — 24일 폭에 24일 지난 창이다. **D+1은 이 패널의 용법이지
정의가 아니다**: 하루짜리 창을 주면 그것이 D+1 보고다. 창은 언제나 `note`에
있다.
    RISKS          기존 패널에 `AT_RISK` / `OPEN_ISSUE` / `PENDING_DECISION`
                   세 종류가 추가된다. 새 표가 아니다.

`notion_projection.UNPROJECTED_PANELS`가 두 결정을 이유와 함께 든다.

---

## 7. 사람이 10초에 읽는 것

Notion 페이지 기준.

**창의 뜻 (C152).** D+1은 하루짜리 창이고, 창은 두 가지에 서로 다르게
적용된다.

    활동   `since`~`until` — 어제 무엇이 바뀌었나 (Event, 완료, commit)
    상태   `until` 시점 — 지금 무엇이 열려 있나 (Blocker, 위험, Issue, Decision)

상태에 `since`를 적용하면 "어제 아무것도 새로 막히지 않았다"가 "막힌 것이
없다"로 보고된다. 그 둘은 반대다.

    ①  지금 상태          한 줄 판정 + 열린 Blocker 수
    ②  ATTENTION          지금 사람이 봐야 할 것, 심각도 순
    ③  핵심 숫자          5개 callout + 역할별 KPI toggle 셋
    ④  Project            막힌 것 먼저, 그다음 오래 조용한 순
    ⑤  최근 변화          보고된 Event + **Git 기준 실제 변경**
    ⑥  상세               필요할 때만 펼친다

③과 ⑤가 이번에 넓어진 자리다. 새 번호를 만들지 않은 이유: ③은 이미 KPI
절이고 ⑤는 이미 "최근 변화"다. 네 번째 제목을 더하면 같은 질문이 페이지
두 곳에서 답해진다.

---

## 8. 아직 답하지 못하는 것 (승인 또는 원천이 필요)

1. **실제 Notion workspace 반영** — 이 저장소에 자격증명이 없다
   (BACKLOG A-8). 코드는 준비되어 있고 `publish_control_tower.py`가
   실행 지점이다.
2. **CEO KPI 12종 전부** — 재무/고객 원천이 필요하다. 어디에 두어야
   하는지는 승인이 필요한 결정이다. docs/14 §1이 Notion을 View로
   고정하므로 Notion에 입력하는 것으로는 원천이 되지 않는다.
3. **DORA 7종** — 배포 사건을 기록하는 원천이 필요하다. 가장 작은 길은
   Event Type 하나(`DEPLOYED`)를 더하는 것이고, 그것은 docs/02 변경이다.
4. **Process Cycle Time / Critical Project On-time Rate** — 기간에
   의존하지 않는 Project 개체와 기한이 필요하다. 지금은 Project가
   `project_id`를 공유하는 Event들의 집합일 뿐이다.
5. ~~**기간 경계** — 열린 Issue/Decision은 읽은 창 안에서 열린 것만 보인다.~~
   **해소됨 (C152).** 이것은 한계가 아니라 **범주 오류**였다 — 열린 상태에는
   "얼마나 거슬러"가 없다. 상태 fold는 `until`까지의 모든 Event를 받고
   `since`를 무시하며, 활동 fold만 창 양끝을 적용받는다. D+1 화면이
   "열려 있는 것이 없다"고 말하던 것이 그 결과였다.
