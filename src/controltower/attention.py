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
    # C146. The sweep this table never had. Every rule above was added by
    # reading *one* block at a time — SCHEDULE (C138), damaged evidence
    # (C143), Backup (C144) — and each of those found its lines by rendering
    # that block and looking for `?` badges. Nobody had asked the whole
    # question: **of every place that appends to the ATTENTION list, how many
    # produce a line this table can read?**
    #
    # Measured by walking the AST of `ops_status.py` and `agent/status.py`
    # for every `…attention.append(…)` / `reasons.append(…)` and classifying
    # each site's literal skeleton: **90 sites, 43 unclassified.** Reproduced
    # live for two of them (a corrupt `backup_state.json` and a corrupt
    # `monthly_history_state.json`) — both printed under ATTENTION, both
    # `severity() == "?"`, both `next_action() is None`, on all three
    # surfaces.
    #
    # `EveryAttentionSiteIsClassifiedTests` now asks that whole question on
    # every run, so the table cannot fall behind the emitters again.
    #
    # The severities below are this module's own P1 definition applied
    # literally — "work is not reaching Company History, or the pipeline is
    # stopped" — and nothing else. Where a line says in its own text that
    # no future run repairs it, it is P1; where the next run handles it, P2.

    # --- Nothing repairs this by itself. The line says so in its own words.
    #     Two spellings of the same sentence, both already in `ops_status.py`
    #     ("재실행으로 해결되지 않는다" for a stalled intake, "재시도로
    #     해결되지 않는다" for a PERMANENT component failure), and both the
    #     same statement `복구되지 않는다` above already carries.
    ("재실행으로 해결되지 않는다", "P1", "재실행으로 낫지 않음 — 사람이 손대야 한다"),
    ("재시도로 해결되지 않는다", "P1", "마지막 실행의 단계가 영구 실패로 끝남"),
    ("마지막 실행이 FAILED로 끝났다", "P1", "마지막 실행이 실패로 끝남"),

    # --- Events that will never reach Company History.
    ("같은 이름의 다른 파일에 막혀 승격되지 않는다", "P1", "이름이 막혀 승격되지 않는 Event"),
    ("보다 이른 KEEP Candidate", "P1", "시작일 이전 — 어떤 실행에서도 History에 들어가지 않는다"),
    # One unreadable file stops the *batch*: `scheduler.py` builds the keep
    # index once per batch, so this is not one lost date but all of them.
    ("읽을 수 없는 KEEP Candidate", "P1", "keep 인덱스가 깨져 모든 날짜의 Daily 생성이 멈춤"),
    ("Company History에 반영되지 않았다", "P1", "사람이 쓴 내용이 History에 들어가지 않음"),
    ("어느 날짜로도 수집되지 않는 Signal", "P1", "Agent가 읽지 않는 자리의 Signal — 나가지 않는다"),
    ("수집이 끝난 날짜에 미전달 Signal", "P1", "이미 지나간 날짜 — 어떤 실행도 다시 읽지 않는다"),

    # --- The History that is already written and no longer agrees with itself.
    #     `_holes_in_the_daily_sequence()` and its Monthly twin exist because
    #     the consistency check looks only at the last date; each of these
    #     lines states, in its own text, that no run rebuilds what is gone.
    ("Daily State와 실제 History가 어긋난다", "P1", "State가 닫혔다는 날의 History 파일이 없다"),
    ("Daily History 시퀀스에 구멍", "P1", "닫힌 날의 Daily가 사라졌다 — 다시 만들어지지 않는다"),
    ("Monthly History 시퀀스에 구멍", "P1", "통합된 달의 Monthly가 사라졌다"),
    ("Daily History의 자기 숫자가 어긋난 날", "P1", "그날 파일이 자기가 센 Event를 들고 있지 않다"),
    ("Monthly에는 없는 Event", "P1", "Daily에 있는 일이 그 달 Monthly에서 빠졌다"),
    ("스스로 센 항목보다 적게 기록한 달", "P1", "그 달 Event가 Monthly에서 통째로 빠졌다"),
    # The overflow line for the same conflict the per-item rule above calls
    # P1 (`같은 event_id를 두고 내용이 다른`). `_risk_totals`'s else-branch
    # renders the kind name verbatim, and EVENT_ID_CONFLICT is the only kind
    # that reaches it — OPEN_BLOCKER and ROLE_MISMATCH have their own
    # sentences and are matched by their own phrases, above and below.
    ("EVENT_ID_CONFLICT", "P1", "같은 id의 파일 둘 — 세어지지 않는 쪽이 더 있다"),

    # --- A pointer in the future: the pipeline runs, reports COMPLETED, and
    #     produces nothing until the calendar catches up.
    ("미래 날짜를 마지막 Daily Close로 기록하고 있다", "P1", "State가 미래를 가리켜 Daily가 멈춤"),
    ("미래의 달을 통합 완료로 기록하고 있다", "P1", "State가 미래를 가리켜 Monthly가 멈춤"),
    ("그 날짜는 미래다", "P1", "Agent의 수집 날짜가 미래 — 아무것도 수집되지 않는다"),

    # --- State this system reads on every run and cannot read now. Each
    #     stops its own pipeline for good, exactly as the two entries above
    #     (`Daily State를 읽을 수 없다`, `Agent state를 읽을 수 없다`) do.
    #     Measured: `app/runner.py` catches `GitOperationError` at the Backup
    #     step and nothing else, so a `BackupStateError` aborts the run there
    #     and the steps after it never start; the Monthly step is wrapped
    #     (§74) so it is the Monthly pipeline that stops, permanently.
    ("backup state 파일이 손상됨", "P1", "Backup 단계가 매 실행 중단됨"),
    ("monthly state 파일이 손상됨", "P1", "Monthly가 멈춤 — 어떤 달도 통합되지 않는다"),

    # --- A Lock nothing releases: every following run is skipped. The same
    #     consequence `제거할 수 없다` above is P1 for. One phrase, both
    #     Locks — `ops_status.py` writes the two lines from one template.
    ("시간째 잡혀 있다", "P1", "Lock이 안 풀림 — 이후 실행이 전부 건너뛰어진다"),

    # --- Company History that Backup structurally never looks at, while
    #     Backup keeps reporting SUCCESS. The `원천 백업에 도달하지 않은`
    #     family, one directory up.
    ("백업 범위 밖이다", "P1", "Backup이 보지 않는 디렉터리 — 한 번도 백업되지 않는다"),
    ("거부된 Signal", "P1", "Signal이 거부됨 — 이 Desktop의 일이 나가지 않는다"),
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
    # C146, the other half of the sweep. None of these stops anything, and
    # that is why they are listed after every P1 phrase above rather than
    # among them: the next run handles them, or there is nothing to handle.
    ("수집되지 않고 남은 Event", "P2", "아직 수집되지 않은 Event가 남아 있음"),
    # Not an `attention.append()` at all — a list returned by
    # `_same_instant_skips_from_the_last_run()` and `extend`ed in. It was
    # missed by the first pass of C146's sweep for exactly that reason, which
    # is why the guard now follows `extend` to the function it calls.
    #
    # P2 on the line's own evidence: "어긋난 것은 Notion 쪽 행뿐이다 — 이
    # 건너뜀은 디스크의 무엇도 바꾸지 않는다". Company History is untouched.
    ("Notion 프로젝트 행에 반영되지 않았다", "P2", "Notion 행만 뒤처짐 — 디스크는 그대로다"),
    # Not an Event at all — a staging file a dying writer left behind. Both
    # spellings (`incoming/`, `rejected/`) say "지워도 안전하다" themselves.
    ("중단된 쓰기 잔여물", "P2", "중단된 쓰기가 남긴 파일 — Event가 아니다"),
    ("만들어졌지만 전달되지 않은 Event", "P2", "outbox에 남은 Event — 다음 실행이 다시 보낸다"),
    ("아직 수집되지 않은 날짜", "P2", "다음 Agent 실행이 수집할 날짜"),
    ("백업 범위 밖 디렉터리를 확인 못 함", "P2", "확인 못 함 — '없음'이 아니다"),
    # The same "확인 못 함" tier, for the detector that answers BACKLOG
    # E-9b's measured silent loss. Not P1: nothing is known to be broken —
    # what is true is that one check is switched off, which is a thing to
    # turn on rather than a thing to repair.
    ("전달 정합성을 확인할 수 없다", "P2", "전달 검사가 꺼져 있음 — 도착 여부를 알 수 없다"),
    ("제목으로 합쳐진다", "P2", "id 철자 차이로 Project 수가 갈라짐 — Event는 유실되지 않는다"),
    ("Late Event로 다시 만들어야 할 달", "P2", "다음 실행이 자동으로 처리한다"),
    ("state에는 통합 기록이 없다", "P2", "monthly 파일과 state가 어긋남 — 확인 필요"),
    ("Monthly가 아직 없다", "P2", "그 달 Daily가 아직 완전하지 않다"),
    ("일째 남아 있는 Event가 있다", "P2", "Notion Retry Queue가 오래 비워지지 않음"),
    ("일째 Notion에 반영되지 못하고 있다", "P2", "Dashboard 기록이 Notion에 닿지 않음"),
    ("단계 metrics가 손상됐다", "P2", "그 단계의 숫자만 읽을 수 없음 — 실행은 멈추지 않는다"),
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
    # C146. One remedy per phrase added by the sweep. Each names an
    # entrypoint that exists in this repository, or names no command at all
    # — the rule this table has followed since it was written.
    "재실행으로 해결되지 않는다": (
        "다시 돌려도 같은 결과다 — 줄이 지목한 파일을 직접 열어 치우거나 고친다."
    ),
    "재시도로 해결되지 않는다": (
        "`runtime/runs/last_run.json` 에서 그 단계의 evidence 경로를 읽고 그 파일을 "
        "먼저 고친다. 고치기 전에 다시 실행하면 같은 자리에서 같은 실패가 난다."
    ),
    "마지막 실행이 FAILED로 끝났다": (
        "`runtime/runs/last_run.json` 을 열어 어느 단계가 실패했는지 읽는다. "
        "고칠 것이 없다면 `python run_company_ops.py` 를 한 번 더 돌린다."
    ),
    "같은 이름의 다른 파일에 막혀 승격되지 않는다": (
        "`runtime/events/processed/` · `rejected/` 에서 그 이름을 쓰고 있는 것을 "
        "열어 본다 — 그 Event가 아니면 치운다. 치우기 전까지 이 Event는 "
        "매 실행 같은 자리에 남는다."
    ),
    "보다 이른 KEEP Candidate": (
        "보내는 Desktop의 `COMPANY_OPS_AGENT_START_DATE` 와 이 머신의 "
        "`COMPANY_OPS_HISTORY_START_DATE` 중 어느 쪽이 맞는지 정한다. "
        "이미 도착한 Candidate는 `runtime/history_candidates/keep/` 에 남아 있으므로 "
        "사람이 그 내용을 History에 반영할지 판단한다."
    ),
    "읽을 수 없는 KEEP Candidate": (
        "이 파일 하나가 **모든 날짜의** Daily 생성을 막는다. "
        "`runtime/history_candidates/keep/` 에서 그 파일을 열어 고치거나 옮긴 뒤 "
        "`python run_company_ops.py` 를 돌린다."
    ),
    "Company History에 반영되지 않았다": (
        "내용은 `runtime/history_candidates/keep/` 에만 있고 그곳은 Backup 대상이 "
        "아니다. 그 날짜의 Daily 파일에 사람이 직접 옮겨 적는다 "
        "(docs/06 §57 / docs/11 §71이 손편집을 허용한다)."
    ),
    "어느 날짜로도 수집되지 않는 Signal": (
        "그 파일을 `runtime/agent/signals/<YYYY-MM-DD>/` 아래로 옮긴다 — 아직 수집되지 않은 "
        "날짜여야 한다. 지나간 날짜로 옮기면 Agent가 다시 읽지 않는다."
    ),
    "수집이 끝난 날짜에 미전달 Signal": (
        "그 파일을 아직 수집되지 않은 날짜 디렉터리로 옮긴 뒤 "
        "`python run_agent.py` 를 돌린다. **state 파일은 되돌리지 않는다.**"
    ),
    "Daily State와 실제 History가 어긋난다": (
        "`runtime/local_master/daily/` 에 실제로 있는 파일이 정본이다(docs/10 §49). "
        "그것을 보고 State를 사람이 되살린다 — History를 지우거나 다시 만들지 "
        "않는다(docs/10 §46)."
    ),
    "Daily History 시퀀스에 구멍": (
        "`runtime/backup_working_copy/daily/` 에 그 날짜들이 남아 있는지 먼저 본다. "
        "있으면 `runtime/local_master/daily/` 로 사람이 되돌린다 — 어떤 실행도 이 "
        "날들을 다시 만들지 않는다."
    ),
    "Monthly History 시퀀스에 구멍": (
        "Monthly는 Daily에서만 파생되므로 복구된다. 그 달 Daily가 남아 있는지 "
        "확인한 뒤 `runtime/state/monthly_history_state.json` 에서 그 달을 dirty로 "
        "표시하고 `python run_company_ops.py` 를 돌린다."
    ),
    "Daily History의 자기 숫자가 어긋난 날": (
        "그 날짜의 `runtime/local_master/daily/<날짜>.md` 를 열어 `- Event Count:` "
        "와 실제 `- Event ID:` 줄 수를 비교한다. 적으면 해당 Candidate의 "
        "`category` 가 네 값 밖인지 보고, 많으면 어떤 Event의 본문에 개행이 "
        "들어가 줄을 위조한 것이다."
    ),
    "Monthly에는 없는 Event": (
        "그 달을 `runtime/state/monthly_history_state.json` 에서 dirty로 표시하고 "
        "`python run_company_ops.py` 를 돌리면 Monthly가 자기 원본과 다시 같아진다."
    ),
    "스스로 센 항목보다 적게 기록한 달": (
        "먼저 그 달 Daily의 `- Category:` 가 DECISION/MILESTONE/ISSUE/LEARNING "
        "중 하나인지 본다. 그렇다면 Monthly가 손편집된 것이므로 그 달을 dirty로 "
        "표시하고 다시 돌린다. 아니라면 다시 만들어도 같은 결과다."
    ),
    "EVENT_ID_CONFLICT": (
        "`runtime/events/processed/` 에서 그 id를 쓰는 파일들을 열어 보고, "
        "그 Event가 아닌 쪽을 치운다 — 화면에 보이는 것 말고도 더 있다."
    ),
    "미래 날짜를 마지막 Daily Close로 기록하고 있다": (
        "`runtime/local_master/daily/` 에 실제로 있는 마지막 날짜가 정본이다"
        "(docs/10 §49). 그 값으로 State를 사람이 되살린다 — 그때까지 어떤 Daily도 "
        "생성되지 않는다."
    ),
    "미래의 달을 통합 완료로 기록하고 있다": (
        "`runtime/local_master/monthly/` 에 실제로 있는 마지막 달이 정본이다. "
        "그 값으로 `runtime/state/monthly_history_state.json` 을 사람이 되살린다."
    ),
    "그 날짜는 미래다": (
        "`runtime/agent/sent/` 에 실제로 무엇까지 전달됐는지가 남아 있다. "
        "그것을 보고 `agent_state.json` 의 수집 날짜를 사람이 되살린다 — "
        "**앞당기기만 하고 지우지 않는다.**"
    ),
    "backup state 파일이 손상됨": (
        "이 파일을 읽지 못하면 Backup 단계에서 실행이 중단되고 그 뒤 단계는 "
        "시작되지 않는다. `runtime/state/backup_state.json` 을 열어 고친다 — "
        "마지막 성공 시각을 모르면 백업은 다시 돌면 되므로 그 값은 비워도 된다."
    ),
    "monthly state 파일이 손상됨": (
        "`runtime/state/monthly_history_state.json` 을 열어 고친다. "
        "`runtime/local_master/monthly/` 에 실제로 있는 마지막 달이 정본이다 — "
        "고치기 전까지 어떤 달도 통합되지 않는다."
    ),
    "시간째 잡혀 있다": (
        "그 Lock 파일이 적어 둔 PID의 프로세스가 정말 이 시스템의 것인지 "
        "확인한다. 아니라면 `runtime/locks/` 의 그 파일을 지운다 — 지우기 전까지 "
        "모든 실행이 조용히 건너뛰어진다."
    ),
    "백업 범위 밖이다": (
        "그 디렉터리 이름을 줄이 말하는 소문자 이름으로 바꾼다. "
        "바꾸기 전까지 그 안의 Company History는 백업되지 않으면서 Backup은 "
        "계속 SUCCESS를 보고한다."
    ),
    "거부된 Signal": (
        "`runtime/agent/signals_rejected/` 의 파일을 열어 사유를 읽고, 고쳐서 "
        "`runtime/agent/signals/<아직 수집되지 않은 날짜>/` 로 되돌리거나 필요 없으면 지운다."
    ),
    "Notion 프로젝트 행에 반영되지 않았다": (
        "디스크의 Company History는 그대로다. 그 프로젝트에 Event가 하나 더 "
        "도착하면 Notion 행이 따라잡는다 — 반복된다면 그 날짜 Signal에 "
        "`timestamp`를 명시한다(AGENT.md §3)."
    ),
    "수집되지 않고 남은 Event": (
        "`python run_company_ops.py` 를 한 번 돌린다. 줄에 이유가 붙어 있다면 "
        "그 이유가 먼저 해결돼야 한다."
    ),
    "중단된 쓰기 잔여물": (
        "Event가 아니므로 지워도 안전하다. 보낸 Desktop을 확인할 필요는 없다."
    ),
    "만들어졌지만 전달되지 않은 Event": (
        "`python run_agent.py` 를 한 번 돌린다. 숫자가 줄지 않으면 그 실행이 "
        "찍은 전송 오류가 원인이다."
    ),
    "아직 수집되지 않은 날짜": (
        "`python run_agent.py` 를 한 번 돌린다 — 그 실행이 이 날짜들을 수집한다."
    ),
    "전달 정합성을 확인할 수 없다": (
        "이 머신의 OneDrive Sync Folder 경로를 `COMPANY_OPS_AGENT_SYNC_FOLDER` "
        "로 넘긴 뒤 이 화면을 다시 본다 (`.env.example` 참고). 그때까지 "
        "`runtime/agent/sent/` 의 기록은 '보냈다고 적혀 있다'는 뜻일 뿐이다."
    ),
    "백업 범위 밖 디렉터리를 확인 못 함": (
        "`runtime/local_master/` 아래 권한을 확인한다. 읽을 수 있게 된 뒤 이 "
        "화면을 다시 본다 — 지금은 '없음'이 아니라 '모름'이다."
    ),
    "제목으로 합쳐진다": (
        "Event는 유실되지 않는다. 앞으로 보낼 `project_id` 의 철자를 한 가지로 "
        "통일한다 — 이미 도착한 Event의 집계는 갈라진 채로 남는다."
    ),
    "Late Event로 다시 만들어야 할 달": (
        "지금 할 일은 없다 — 다음 `run_company_ops.py` 실행이 그 달을 다시 만든다."
    ),
    "state에는 통합 기록이 없다": (
        "`runtime/local_master/monthly/` 에 있는 파일과 "
        "`runtime/state/monthly_history_state.json` 을 나란히 본다. "
        "파일 쪽이 정본이다(docs/10 §49)."
    ),
    "Monthly가 아직 없다": (
        "지금 할 일은 없다. 그 달 Daily가 전부 닫히면 다음 실행이 통합한다."
    ),
    "일째 남아 있는 Event가 있다": (
        "`runtime/logs/notion_sync.log` 의 REASON을 읽는다. Notion이 영구히 "
        "거부하는 요청이라면 다시 돌려도 빠져나가지 않는다 (docs/13)."
    ),
    "일째 Notion에 반영되지 못하고 있다": (
        "`runtime/logs/notion_sync.log` 의 `DASHBOARD DRAIN_PENDING ... REASON` "
        "을 읽는다. 열이 모자란 Database가 흔한 원인이며 docs/13 §3-⑧-4가 "
        "고치는 명령이다."
    ),
    "단계 metrics가 손상됐다": (
        "실행은 멈추지 않는다 — 잃은 것은 그 단계의 숫자뿐이다. "
        "다음 `run_company_ops.py` 실행이 새 Manifest를 쓴다."
    ),
    "사람이 확인해야 한다": (
        "줄 전문을 읽고 사람이 판단한다 — 자동으로 처리되지 않는다."
    ),
}

#: Whether a line is about **the company** or about **Company Ops itself**.
#:
#: The severity axis answers "how broken is the pipeline", and it is right
#: about that — `막혀 있는 Project` is deliberately P2 because, as `RULES`
#: says above, "a blocked Project is a pipeline working perfectly on work
#: that a person has stopped". For an operator reading `ops_status.py` that
#: is the correct ordering.
#:
#: It is the wrong ordering for the one surface a CEO opens. Measured, on a
#: tree carrying two real blockers and a stopped pipeline at the same time,
#: the Notion page's ② section — the one its own headline says to read from
#: the top — came out:
#:
#:     🔴 즉시 조치 (6건)   Collector가 거부한 Event / backup state 파일이
#:                          손상됨 / Runner가 16일째 / 예약 실행이 등록돼
#:                          있지 않다 / Agent가 한 번도 …      ← 6/6 도구
#:     🟡 확인 필요 (6건)   … 3번째와 4번째에 막힌 Project 둘, 그중 하나가
#:                          **CEO 본인의 승인 대기**
#:
#: So the page walked the reader through six tool-maintenance items before
#: the thing that needed them. Nothing was wrong with any single line; the
#: page had one axis and was asking it to do two jobs.
#:
#: **The test is the remedy, not the subject.** A line is `COMPANY` when the
#: person acting on it needs to know nothing about Company Ops — the team
#: must unblock a project, a person must decide what enters Company History.
#: A line is `SYSTEM` when the remedy names a script, a state file, a queue
#: or a scheduled task, however important the data behind it is.
#:
#: Keyed on `RULES`'s own phrases, for the reason `ACTIONS` states one
#: paragraph down: one match, three answers, so a line cannot carry one
#: rule's severity and another's audience.
#:
#: Every phrase must appear here — there is no default. A new rule that
#: forgets this fails `AttentionSaysHowBadAndWhereFromTests` rather than quietly
#: becoming `SYSTEM`, which is how the two company lines would get buried
#: again.
COMPANY = "COMPANY"
SYSTEM = "SYSTEM"

#: What each group is called where a person reads it.
DOMAIN_LABELS = {
    COMPANY: "회사 — 사람이 풀어야 하는 일",
    SYSTEM: "시스템 — Company Ops 자체 상태",
}

DOMAINS: dict[str, str] = {
    # --- the company's own work -----------------------------------------
    # A team has stopped on something a person outside this repository has
    # to resolve. `ACTIONS` for this phrase names no command at all.
    "막혀 있는 Project": COMPANY,
    # docs/05 §24: BLOCKED / COMPLETED / CANCELLED are not decided by rule.
    # These Candidates are work waiting for a person's judgement about what
    # belongs in Company History — `kind()` already calls it DECIDE.
    "검토를 기다리": COMPANY,
}
#: Everything else is about the tooling. Listed rather than defaulted so the
#: roster below is a statement someone made, and `RULES` cannot grow past it
#: in silence.
DOMAINS.update(
    {
        phrase: SYSTEM
        for phrase in (
            "복구되지 않는다",
            "History에 들어가지 못한",
            "Daily History에 없다",
            "실행되지 않았다",
            "수집되지 않으며",
            "거부한 Event",
            "제거할 수 없다",
            "같은 event_id를 두고 내용이 다른",
            "예약 실행이 등록돼 있지 않다",
            "예약 실행이 사용 안 함 상태다",
            "마지막 예약 실행이 실패로 끝났다",
            "이 저장소가 아닌 곳을 실행한다",
            "Desktop ID와 다르다",
            "Daily State를 읽을 수 없다",
            "Agent state를 읽을 수 없다",
            "한 번도 실행을 완료한 적이 없다",
            "원격 백업에 도달하지 않은",
            "Local Master에는 없는",
            "재실행으로 해결되지 않는다",
            "재시도로 해결되지 않는다",
            "마지막 실행이 FAILED로 끝났다",
            "같은 이름의 다른 파일에 막혀 승격되지 않는다",
            "보다 이른 KEEP Candidate",
            "읽을 수 없는 KEEP Candidate",
            "Company History에 반영되지 않았다",
            "어느 날짜로도 수집되지 않는 Signal",
            "수집이 끝난 날짜에 미전달 Signal",
            "Daily State와 실제 History가 어긋난다",
            "Daily History 시퀀스에 구멍",
            "Monthly History 시퀀스에 구멍",
            "Daily History의 자기 숫자가 어긋난 날",
            "Monthly에는 없는 Event",
            "스스로 센 항목보다 적게 기록한 달",
            "EVENT_ID_CONFLICT",
            "미래 날짜를 마지막 Daily Close로 기록하고 있다",
            "미래의 달을 통합 완료로 기록하고 있다",
            "그 날짜는 미래다",
            "backup state 파일이 손상됨",
            "monthly state 파일이 손상됨",
            "시간째 잡혀 있다",
            "백업 범위 밖이다",
            "거부된 Signal",
            "Desktop과 role이 어긋난",
            "밀린 분을 보낸 것으로",
            "아무것도 오지 않은",
            "전달되지 않았다",
            "Notion 단계를 시도한 실행이",
            "시작조차 되지 못한",
            "예약 실행을 Windows가 중단시켰다",
            "예약 실행이 콘솔 출력을 버린다",
            "둘 이상 등록돼 있다",
            "예약 실행을 확인할 수 없다",
            "판단할 근거를 읽지 못했다",
            "Run Manifest를 읽을 수 없다",
            "읽을 수 없는 processed Event",
            "processed에 읽을 수 없는 Event",
            "last_run이 timestamp가 아니다",
            "완료되지 않은 쓰기 잔여물",
            "수집되지 않고 남은 Event",
            "Notion 프로젝트 행에 반영되지 않았다",
            "중단된 쓰기 잔여물",
            "만들어졌지만 전달되지 않은 Event",
            "아직 수집되지 않은 날짜",
            "백업 범위 밖 디렉터리를 확인 못 함",
            "전달 정합성을 확인할 수 없다",
            "제목으로 합쳐진다",
            "Late Event로 다시 만들어야 할 달",
            "state에는 통합 기록이 없다",
            "Monthly가 아직 없다",
            "일째 남아 있는 Event가 있다",
            "일째 Notion에 반영되지 못하고 있다",
            "단계 metrics가 손상됐다",
            "사람이 확인해야 한다",
        )
    }
)


def domain(line: str) -> str:
    """`COMPANY` or `SYSTEM` — who this line is for.

    `SYSTEM` for a line no rule matched: an unclassified line is one this
    module has no reading of, and promoting it to the company's list would
    put an unread sentence in front of the CEO. It still sorts with P1
    inside that list (`RANK`), so it cannot hide.
    """
    for marker, _level, _why in RULES:
        if marker in line:
            return DOMAINS.get(marker, SYSTEM)
    return SYSTEM


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
