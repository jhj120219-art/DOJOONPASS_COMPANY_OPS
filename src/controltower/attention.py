"""How an ATTENTION line is ranked, for every surface that shows one.

`ops_status.main()` returns `list[str]`. Nothing upstream carries a
severity — not the Event, not the Run Manifest, not the Dashboard Model — so
any ranking is a **reading**, and this module is the one place that reading
is written down.

Three surfaces render the same list, and before C129 each rendered it flat:

    ops_status.py            the terminal ATTENTION block
    dashboard_server.py      the browser page
    controltower/notion_page the bullets on the page the workspace reads

The browser page was fixed first and the classifier lived there. That put it
in an **entrypoint**, which `controltower/notion_page.py` sits below and
cannot import — so the company-facing surface would have kept the flat list
while the local one improved. A rule two renderers need belongs under both.

**P1 is "work is not reaching Company History, or the pipeline is stopped".**
Every phrase in the table is one `ops_status.py` already writes for exactly
that condition; none was invented here. A line that matches nothing is `?`,
never quietly filed as minor — the same posture `PanelStatus.UNSOURCED`
takes for "no source" versus "zero".
"""

from __future__ import annotations

#: `(phrase, severity, why)`. Order matters only in that the first match
#: wins, and the P1 phrases are listed first for that reason.
RULES: tuple[tuple[str, str, str], ...] = (
    ("복구되지 않는다", "P1", "재실행으로 복구되지 않음"),
    ("History에 들어가지 못한", "P1", "Company History에 도달하지 못함"),
    ("Daily History에 없다", "P1", "Company History에 도달하지 못함"),
    ("실행되지 않았다", "P1", "파이프라인이 돌지 않음"),
    ("수집되지 않으며", "P1", "다음 실행에서도 수집되지 않음"),
    ("거부한 Event", "P1", "Event가 거부됨"),
    ("제거할 수 없다", "P1", "모든 실행이 조용히 건너뛰어짐"),
    # C133. One `event_id`, two files with different contents: the Control
    # Tower counts one and does **not** count the other, and which one it
    # counts is decided by filename order. That is a file whose work is
    # absent from every rollup on the screen -- "work is not reaching
    # Company History" by the letter of this module's own P1 definition.
    ("같은 event_id를 두고 내용이 다른", "P1", "같은 id의 파일 둘 — 한쪽이 세어지지 않음"),
    # C138. The SCHEDULE block reads Windows Task Scheduler — the only
    # evidence in this report that is not a file this system wrote. Its
    # lines came out `?` on every surface, measured by loading the rendered
    # Dashboard: "이 화면이 분류하지 못한 줄", directly under an older alarm
    # whose own remedy is "예약 작업(Windows 작업 스케줄러)이 꺼져 있는지
    # 확인한다" — the manual check this block now performs.
    #
    # P1 by this module's own definition ("the pipeline is stopped"). A task
    # that is absent, disabled, failing every trigger, or pointed at another
    # checkout is a pipeline that will not run again on its own, and
    # `실행되지 않았다` — already P1 — is the *symptom* of exactly these.
    ("예약 실행이 등록돼 있지 않다", "P1", "예약된 실행이 없음 — 자동으로 아무것도 돌지 않는다"),
    ("예약 실행이 사용 안 함 상태다", "P1", "Task가 꺼져 있음 — 트리거가 와도 시작되지 않는다"),
    ("마지막 예약 실행이 실패로 끝났다", "P1", "예약 실행이 실패로 끝남"),
    ("이 저장소가 아닌 곳을 실행한다", "P1", "다른 경로를 실행 중 — 이 저장소는 돌지 않는다"),
    # The registered Agent task is for a different Desktop, so
    # `ensure_desktop()` refuses it on every run: this Desktop's Events
    # never leave the machine. "Work is not reaching Company History" by
    # the letter of the definition above.
    ("Desktop ID와 다르다", "P1", "등록된 Task가 다른 Desktop의 것 — 매번 거부된다"),
    # C143. The **damaged-evidence family** — seven lines, every one of them
    # `?`. Measured by running `dashboard_server.py` against a runtime with
    # corrupted state files: the clean tree renders zero unclassified
    # badges, the damaged one rendered six, and they were the only six.
    #
    # That is the worst possible place for this gap. These are the lines an
    # operator sees *when the evidence itself is broken* — the moment they
    # most need "how bad is this" and "what do I do" — and they arrived with
    # no severity and no remedy, sorted in beside the genuine faults.
    #
    # The two below are P1 by measurement, not by reading. Each one stops a
    # pipeline outright:
    #
    #     scheduler.run_once()  -> SchedulerStateError, no Daily History is
    #                              generated for **any** date until a human
    #                              fixes the file
    #     agent.run_once()      -> AgentStateError, this Desktop's work never
    #                              leaves the machine (the same consequence
    #                              `Desktop ID와 다르다` above is P1 for)
    ("Daily State를 읽을 수 없다", "P1", "Scheduler가 멈춤 — 어떤 날짜도 생성되지 않는다"),
    ("Agent state를 읽을 수 없다", "P1", "Agent가 멈춤 — 이 Desktop의 일이 나가지 않는다"),
    # The zero-case of `실행되지 않았다`, which is already P1 four lines up.
    # `agent/status.py` raises them from one `if/elif/elif` on the same
    # question, so they cannot honestly carry different severities.
    ("한 번도 실행을 완료한 적이 없다", "P1", "파이프라인이 돌지 않음 — 한 번도 완료된 적이 없다"),
    # C144. The **Backup/durability family**, measured the same way: a
    # fixture with a real working copy, history missing from Local Master,
    # and a failed backup state, rendered through `ops_status.py`. Four
    # backup alarms came out `?`; these two are the ones that fit this
    # module's existing P1 definition without stretching it.
    #
    # docs/08 exists so Company History survives this machine. A tree where
    # backup has *never* succeeded has no off-machine copy of anything, and
    # the line itself says the green signal is wrong ("Backup이 SUCCESS/
    # NOT_REQUIRED를 보고하고 있어도 이 파일들은 이 머신에만 있다") — the
    # C137 shape. That is the backup pipeline not running, not a warning.
    ("원격 백업에 도달하지 않은", "P1", "이 머신에만 있는 Company History — 백업이 도달하지 않았다"),
    # Company History that reached the backup and is **now gone from this
    # machine**. "Work is not reaching Company History" by the letter — it
    # got there and left. `_history_gone_from_local_master()` exists because
    # every other hole check structurally cannot see a missing prefix.
    ("Local Master에는 없는", "P1", "Company History가 이 머신에서 사라졌다"),
    # C133. An open Blocker is the most actionable line this list carries
    # and it was **unclassified** -- measured on a probe tree, it rendered
    # with a `?` badge and "이 화면이 분류하지 못한 줄".
    #
    # P2 rather than P1, and the distinction is worth stating: P1 here
    # means the *pipeline* is broken, and a blocked Project is a pipeline
    # working perfectly on work that a person has stopped. Promoting it
    # would redefine P1 for the ten lines that already use it. The
    # Dashboard gives open Blockers their own never-folded section
    # instead, which is where that emphasis belongs.
    ("막혀 있는 Project", "P2", "업무가 멈춰 있음 — 사람이 풀어야 한다"),
    ("Desktop과 role이 어긋난", "P2", "Desktop↔role 불일치 — 집계가 갈라짐"),
    # The reassuring twin of `아무것도 오지 않은`: the Desktop was off and
    # has just sent its backlog. Left unclassified it sorted to the top
    # beside the genuine faults.
    ("밀린 분을 보낸 것으로", "P2", "밀린 보고가 도착함 — Agent는 살아 있다"),
    ("아무것도 오지 않은", "P2", "침묵 — 원인은 아직 알 수 없음"),
    ("전달되지 않았다", "P2", "설정이 전달되지 않음"),
    # C133, found by reading the **published Notion page** rather than the
    # code: this rendered as `[?] (분류 불가)` on the surface the company
    # reads, directly under a sentence that ends "run_company_ops.py를 한 번
    # 실행해 실제로 도달하는지 확인해야 한다". The line knew its own remedy
    # and the classifier did not.
    #
    # P2: nothing is broken. What is true is that the two zero counts above
    # it are not evidence of health, which is a thing to verify rather than
    # a thing to repair.
    ("Notion 단계를 시도한 실행이", "P2", "Notion 도달 여부가 아직 확인되지 않음"),
    ("시작조차 되지 못한", "P2", "앞 단계가 중단시킴"),
    # C138, the other half. None of these four is a stopped pipeline.
    #
    # Windows terminating an overrunning run is docs/07 §55 working: the run
    # was cut off part-way and the next trigger resumes it. Discarding
    # console output costs the *diagnosis*, not the run. And the last two
    # are "this could not be determined", which must never be filed as a
    # fault — the same posture `?` takes, but with a stated reason so the
    # line does not sort with the genuine faults.
    ("예약 실행을 Windows가 중단시켰다", "P2", "시간 제한 초과로 중단됨 — 다음 트리거가 이어받는다"),
    ("예약 실행이 콘솔 출력을 버린다", "P2", "실패해도 이유가 남지 않음 — 관측만 약해진 상태"),
    ("둘 이상 등록돼 있다", "P2", "Task가 둘 — 이 머신 것이 아닌 쪽은 매번 거부된다"),
    ("예약 실행을 확인할 수 없다", "P2", "예약 여부를 알 수 없음"),
    ("판단할 근거를 읽지 못했다", "P2", "이 머신이 Runner인지 판단할 수 없음"),
    # C133. The review queue was **unclassified** until now — measured:
    # `사람 검토를 기다리는 History Candidate 3건` matched no phrase and came
    # out `?`, which sorts with P1 and renders as "이 화면이 분류하지 못한
    # 줄". It is neither. It is the one condition on this list that is not a
    # fault at all: `docs/05 §24` says BLOCKED/COMPLETED/CANCELLED are not
    # decided by rule, so those Candidates are **work waiting for a person**,
    # and P2 ("사람 확인 필요") is exactly that tier. Listed before the
    # broader `사람이 확인해야 한다` because that phrase is appended to
    # several fault lines too and would otherwise claim this one first.
    # C143, the other half of the damaged-evidence family. None of these
    # stops anything — which is exactly why filing them beside the two P1s
    # above would be wrong.
    #
    # The Run Manifest is the *account* of a run, not the run:
    # `_exit_code_from_manifest()` already falls back to 2 without it, so
    # what is lost is the explanation, not the pipeline.
    ("Run Manifest를 읽을 수 없다", "P2", "마지막 실행의 기록을 읽을 수 없음 — 실행 자체는 멈추지 않는다"),
    # One file whose attribution cannot be judged. Per-file isolation is the
    # design (docs/03 §53), so the rest of the run is unaffected — the two
    # spellings come from two different producers on the same subject.
    ("읽을 수 없는 processed Event", "P2", "이 Event의 귀속을 판단할 수 없음"),
    ("processed에 읽을 수 없는 Event", "P2", "이 Event의 History 반영 여부를 판단할 수 없음"),
    # `agent/status.py` states this one's severity itself: `last_run` is
    # informational, and it deliberately does **not** validate it on load
    # because "rejecting it there would turn a cosmetic corruption into a
    # stopped Agent, which is the wrong direction". The Agent keeps running;
    # only the staleness check is blind.
    ("last_run이 timestamp가 아니다", "P2", "마지막 실행 시각을 읽을 수 없어 지연 검사만 못 한다"),
    # C144. Residue an interrupted run left in the Backup Working Copy.
    # Nothing is lost and nothing is stopped — `git add -A` would carry it
    # to the remote, so it is something to clear, which is what P2 is.
    ("완료되지 않은 쓰기 잔여물", "P2", "중단된 실행의 잔여물 — Company History가 아니다"),
    ("검토를 기다리", "P2", "사람 검토 대기"),
    ("사람이 확인해야 한다", "P2", "사람 확인 필요"),
)

#: Sort order. `?` sits with P1 rather than at the bottom: an unclassified
#: line must not be able to hide, and the badge says it is unclassified so
#: nobody reads it as a verdict.
RANK = {"P1": 0, "?": 1, "P2": 2}

UNCLASSIFIED = "?"


#: What a person does about a line, keyed on the phrase that classified it.
#:
#: A dashboard that says only *what is wrong* leaves the reader to work out
#: *what to do*, and the portfolio-reporting rule this project was measured
#: against is explicit about it: every red or amber entry needs one line
#: saying what happens next. Before this, none of the eleven did.
#:
#: **Keyed on `RULES`'s own phrases, deliberately.** A second table matched
#: independently would drift from the severity table it sits beside, and
#: then one line could carry a P1 badge with a P2's remedy. One match, two
#: answers: `severity()` and `next_action()` walk the same tuple.
#:
#: Each entry names an entrypoint that exists in this repository, or names
#: no command at all. A dashboard that tells an operator to run something
#: that is not there is worse than one that stays quiet — so where the
#: remedy is a judgement rather than a command, the text says to read and
#: judge, and stops.
ACTIONS: dict[str, str] = {
    "복구되지 않는다": (
        "재실행으로는 낫지 않는다 — 줄이 지목한 파일·날짜를 직접 열어 확인한다."
    ),
    "History에 들어가지 못한": (
        "그 날짜의 Event가 더 수집되면 함께 들어간다. 지난 날짜라면 "
        "`runtime/history_candidates/keep/` 의 해당 건을 사람이 처리한다."
    ),
    "Daily History에 없다": (
        "그 날짜의 Event가 더 수집되면 함께 들어간다. 지난 날짜라면 "
        "`runtime/history_candidates/keep/` 의 해당 건을 사람이 처리한다."
    ),
    "실행되지 않았다": (
        "`python run_company_ops.py` 로 한 번 돌린다. 계속 반복되면 "
        "예약 작업(Windows 작업 스케줄러)이 꺼져 있는지 확인한다."
    ),
    "수집되지 않으며": (
        "줄이 지목한 파일을 열어 형식을 고치거나, 필요 없으면 지운다."
    ),
    "거부한 Event": (
        "거부된 파일의 사유를 읽고 스키마에 맞게 고친 뒤 다시 넣는다 "
        "(docs/02)."
    ),
    "제거할 수 없다": (
        "그 Lock을 잡고 있는 프로세스가 정말 도는지 확인한 뒤, 죽었으면 "
        "Lock 파일을 지운다."
    ),
    "아무것도 오지 않은": (
        "그 Desktop에서 Agent가 도는지 확인한다 (`python run_agent.py`). "
        "꺼져 있었을 뿐일 수도 있다 — 이 줄만으로는 고장인지 알 수 없다."
    ),
    "Notion 단계를 시도한 실행이": (
        "`python run_company_ops.py` 를 한 번 돌려 Notion에 실제로 닿는지 "
        "확인한다 — 토큰이나 Database 공유 설정이 틀렸다면 그때 드러난다."
    ),
    "전달되지 않았다": (
        "`.env` 의 값을 이 프로세스의 환경변수로 넘긴 뒤 다시 실행한다."
    ),
    "시작조차 되지 못한": (
        "앞 단계의 실패를 먼저 고친다 — 이 단계는 그 결과일 뿐이다."
    ),
    # C143. docs/10 §46 forbids the obvious remedy: "프로그램이 임의로 모든
    # History를 삭제하거나 다시 생성하면 안 된다", and §49 says "History가
    # State보다 우선". So the action names no command that rewrites state —
    # it points at the History already on disk, which is the authority.
    "Daily State를 읽을 수 없다": (
        "Scheduler는 이 파일을 읽지 못하면 어떤 날짜도 생성하지 않는다. "
        "`runtime/local_master/daily/` 에 이미 있는 파일이 무엇까지 닫혔는지의 "
        "정본이다(docs/10 §49) — 그것을 보고 State를 사람이 되살린다. "
        "History를 지우거나 다시 만들지 않는다(docs/10 §46)."
    ),
    "Agent state를 읽을 수 없다": (
        "이 Desktop의 Agent는 이 파일을 읽지 못하면 실행되지 않는다. "
        "`runtime/agent/sent/` 에 무엇이 전달됐는지가 남아 있으므로 "
        "그것을 보고 `desktop_id` 와 마지막 수집 날짜를 사람이 되살린다."
    ),
    "한 번도 실행을 완료한 적이 없다": (
        "`python run_agent.py` 를 한 번 돌린다. 설치 직후라면 정상이고, "
        "그 실행이 끝나면 이 줄은 사라진다 — 사라지지 않으면 그 실행이 "
        "찍은 오류가 원인이다."
    ),
    "Run Manifest를 읽을 수 없다": (
        "실행 자체는 멈추지 않는다 — 잃은 것은 마지막 실행의 설명이다. "
        "`python run_company_ops.py` 를 한 번 돌리면 새 Manifest가 쓰이고, "
        "그때까지 `runtime/logs/` 의 로그가 그 실행의 유일한 기록이다."
    ),
    "읽을 수 없는 processed Event": (
        "줄이 지목한 파일을 `runtime/events/processed/` 에서 열어 본다 — "
        "다른 Event는 영향받지 않는다(docs/03 §53)."
    ),
    "processed에 읽을 수 없는 Event": (
        "줄이 지목한 파일을 `runtime/events/processed/` 에서 열어 본다 — "
        "그 Event가 History에 들어갔는지는 그 파일을 읽어야만 알 수 있다."
    ),
    "last_run이 timestamp가 아니다": (
        "Agent는 계속 돈다 — 못 하는 것은 지연 여부 검사뿐이다. "
        "다음 실행이 `last_run` 을 다시 쓰므로, 한 번 돌려 이 줄이 "
        "사라지는지 확인한다."
    ),
    # C144. docs/08 §30 makes the Working Copy operator setup, so the remedy
    # names the one command that fixes the usual cause and stops.
    "원격 백업에 도달하지 않은": (
        "`python run_company_ops.py` 를 한 번 돌리고 Backup 결과를 본다. "
        "실패한다면 그 메시지가 원인이다 — `runtime/backup_working_copy/` 가 "
        "clone으로 만들어졌고 `origin` 이 설정돼 있는지 먼저 확인한다"
        "(docs/11 §26)."
    ),
    "Local Master에는 없는": (
        "그 파일들은 백업 원격에 아직 있다. "
        "`runtime/backup_working_copy/daily/` 에서 `runtime/local_master/daily/` "
        "로 사람이 되돌린다 — 이 시스템은 지워진 Company History를 스스로 "
        "복원하지 않는다(docs/10 §46)."
    ),
    "완료되지 않은 쓰기 잔여물": (
        "Company History가 아니므로 `runtime/backup_working_copy/` 에서 "
        "그 파일을 지운다 — 지우지 않으면 다음 Backup이 원격으로 가져간다."
    ),
    "같은 event_id를 두고 내용이 다른": (
        "두 파일을 열어 보고, 그 Event가 아닌 쪽을 "
        "`runtime/events/processed/` 에서 치운다."
    ),
    "막혀 있는 Project": (
        "Blocker 문장이 지목한 것을 사람이 푸는 수밖에 없다 — "
        "파이프라인은 이것을 스스로 지우지 않는다. 그 Team이 RESUMED / "
        "ISSUE_RESOLVED / COMPLETED를 보고해야 닫힌다."
    ),
    "Desktop과 role이 어긋난": (
        "건별로 보기 전에 그 Desktop의 role 설정을 먼저 확인한다 — "
        "한 대가 잘못 설정되면 그 Desktop의 모든 Event가 여기 들어온다."
    ),
    "밀린 분을 보낸 것으로": (
        "지금 할 일은 없다. Agent는 살아 있고 밀렸던 보고가 막 도착했다는 "
        "뜻이다 — 다음 실행에서 이 줄이 사라지는지만 확인한다."
    ),
    "검토를 기다리": (
        "`python src/review_cli.py` 로 내용을 읽고 KEEP/IGNORE를 정한다 "
        "(AGENT.md §5b). 고장이 아니라 사람이 할 일이다."
    ),
    "예약 실행이 등록돼 있지 않다": (
        "줄이 이름을 댄 `scripts/install_*_task.ps1` 을 그 머신에서 실행한다 "
        "(`-WhatIf` 로 먼저 미리 볼 수 있다). 등록 전까지 이 머신은 아무것도 "
        "자동으로 실행하지 않는다."
    ),
    "예약 실행이 사용 안 함 상태다": (
        "작업 스케줄러에서 그 Task를 다시 사용함으로 바꾼다 — 암호 변경 뒤 "
        "Windows가 꺼 두는 것이 가장 흔한 원인이다."
    ),
    "마지막 예약 실행이 실패로 끝났다": (
        "줄이 이름을 댄 `runtime/logs/scheduled_*.log` 의 끝을 읽는다 — "
        "설정이 빠져 끝난 실행은 그 파일에만 이유를 남긴다."
    ),
    "이 저장소가 아닌 곳을 실행한다": (
        "그 경로가 아직 있는지 확인한다. 이 저장소가 맞다면 여기서 "
        "`scripts/install_*_task.ps1` 을 다시 실행해 Task를 다시 가리키게 한다."
    ),
    "Desktop ID와 다르다": (
        "`COMPANY_OPS_PROFILE` 과 등록된 Task 중 어느 쪽이 맞는지 정한 뒤 "
        "틀린 쪽을 고친다. **state 파일은 직접 지우지 않는다** — 아직 수집되지 "
        "않은 날짜가 조용히 건너뛰어진다."
    ),
    "예약 실행을 Windows가 중단시켰다": (
        "다음 실행이 정상으로 끝나는지 본다. 반복되면 그 Task의 실행 시간 "
        "제한을 늘린다 — 첫 Catch-up은 길어질 수 있다."
    ),
    "예약 실행이 콘솔 출력을 버린다": (
        "`scripts/install_*_task.ps1` 을 다시 실행한다 (`-Force`, 멱등). "
        "Windows는 저장소가 바뀌었다고 Action을 갱신하지 않는다."
    ),
    "둘 이상 등록돼 있다": (
        "쓰지 않는 Task를 `Unregister-ScheduledTask` 로 지운다 — 어느 쪽이 "
        "이 머신 것인지는 `runtime/agent/state/agent_state.json` 의 "
        "`desktop_id` 가 말해 준다."
    ),
    "예약 실행을 확인할 수 없다": (
        "그 머신에서 `COMPANY_OPS_PROFILE` 을 확인한다 — 값이 없으면 이 화면은 "
        "Task 이름을 만들 수 없어 등록 여부를 말하지 못한다."
    ),
    "판단할 근거를 읽지 못했다": (
        "`runtime/` 아래 권한을 확인한다 — 이 화면은 그것을 읽지 못하면 "
        "이 머신이 Desktop 4인지 아닌지를 말할 수 없다."
    ),
    "사람이 확인해야 한다": (
        "줄 전문을 읽고 사람이 판단한다 — 자동으로 처리되지 않는다."
    ),
}

#: The line asks for a **judgement**, not a repair.
#:
#: Narrow on purpose. `사람이 확인해야 한다` is *appended* to several broken
#: -state lines in `ops_status.py` (a rejected Event, a damaged state file),
#: so it does not separate the two — measured. The review queue does: those
#: Candidates are not a fault, they are work waiting for a person, and
#: `docs/05 §24` is why they are not decided automatically.
DECIDE_MARKERS: tuple[str, ...] = ("검토를 기다리",)

#: What this screen calls each group.
KIND_LABELS = {
    "FIX": "조치 — 사람이 손대야 한다",
    "DECIDE": "판단 — 사람이 정해야 한다",
}


def next_action(line: str) -> str | None:
    """One line saying what a person does about `line`, or `None`.

    `None` rather than a guess: a line `severity()` could not classify is
    one this module has no reading of, and inventing a remedy for it would
    be the same failure `UNCLASSIFIED` exists to prevent one field over.
    """
    for marker, _level, _why in RULES:
        if marker in line:
            return ACTIONS.get(marker)
    return None


def kind(line: str) -> str:
    """`FIX` or `DECIDE` — whether the line asks for a repair or a decision."""
    return "DECIDE" if any(m in line for m in DECIDE_MARKERS) else "FIX"


def severity(line: str) -> tuple[str, str | None]:
    """`(P1 | P2 | ?, the phrase it matched)` for one ATTENTION line."""
    for marker, level, why in RULES:
        if marker in line:
            return level, why
    return UNCLASSIFIED, None


def rank(line: str) -> int:
    """Sort key: P1 and unclassified first, P2 last."""
    return RANK[severity(line)[0]]


def tally(lines) -> dict[str, int]:
    """`{severity: count}` over `lines`, for a one-line summary."""
    counts: dict[str, int] = {}
    for line in lines:
        level = severity(line)[0]
        counts[level] = counts.get(level, 0) + 1
    return counts
