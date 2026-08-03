# 00_COMPANY_OPS_V1_SPEC.md

# DOJOONPASS Company Ops V1 Development Specification

---

## 1. 문서 정의

| 항목 | 내용 |
|---|---|
| 문서명 | Company Ops V1 Development Specification |
| 프로젝트 | DOJOONPASS Company Ops |
| Owner | COO |
| 목적 | 전사 Execution 상태 자동수집 및 Company History 자동보존 |
| 대상 | Desktop 1~4 |
| 현재 개발 위치 | Desktop 4 |
| 버전 | V1 |
| 원칙 | 최소 기능으로 구축하고 도준패스 본 서비스 개발을 방해하지 않는다 |

본 스펙은 `DOJOONPASS_OS/00_foundation/04_repository_contract.md`의 Repository Contract 범위 내에서 유효하며, 회사 전체 원칙과 충돌 시 Contract를 우선한다.

이 문서는 DOJOONPASS Company Ops V1의 개발 기준을 정의한다.

Company Ops는 도준패스 Product 및 Content OS와 분리된 별도의 내부 운영 프로젝트로 관리한다.

---

# 2. 프로젝트 목적

Company Ops V1의 목적은 다음 상태를 만드는 것이다.

> 각 Desktop에서 평소처럼 업무를 수행하면 중요한 업무 상태 변화가 자동으로 수집되고, 현재 회사 상태는 Notion에 반영되며, 완료된 중요한 사건은 Desktop 4의 Company History에 자동 보존된다.

사용자가 CTO/CMO의 작업 결과를 직접 복사하여 COO에게 전달하거나 별도로 History를 작성하는 구조를 제거한다.

최종적으로 다음 흐름을 자동화한다.

Desktop 업무
→ 업무 상태 변화
→ Execution Event
→ 중앙 수집
→ Notion 현재 상태 반영
→ History Candidate 선별
→ Daily History
→ Monthly History
→ Backup

---

# 3. V1 핵심 원칙

Company Ops V1은 다음 원칙을 따른다.

1. 현재 상태와 회사 History를 분리한다.
2. Notion은 현재 Execution 상태를 관리한다.
3. Desktop 4 Local은 Company History의 Master다.
4. GitHub는 코드/공식문서 관리와 Company Ops 데이터 전달 및 History Backup에 사용한다.
5. Company History는 모든 업무를 기록하지 않는다.
6. 회사에 의미 있는 Decision, Milestone, Issue, Learning만 장기 보존한다.
7. 각 Desktop의 AI 대화 전체를 저장하지 않는다.
8. 일반 Commit이나 파일 변경을 Company History로 기록하지 않는다.
9. 자동화 실패가 History 원본을 손상시켜서는 안 된다.
10. COO 판단 영역과 CEO 결정 영역을 AI가 자동 확정하지 않는다.
11. Company Ops 개발 때문에 도준패스 본 서비스 Launch가 지연되어서는 안 된다.
12. V1 완료 후 실제 사용 결과 없이 기능을 계속 추가하지 않는다.

---

# 4. 전체 Architecture

```text
Desktop 1 — CTO Backend ── Reporter ──┐
                                      │
Desktop 2 — CMO ────────── Reporter ──┤
                                      │
Desktop 3 — CTO Front ───── Reporter ─┤
                                      │
Desktop 4 — COO ──────────────────────┘
                                      │
                                      ↓
                              Execution Event
                                      │
                                      ↓
                         GitHub Company Ops
                                      │
                                      ↓
                          Desktop 4 Collector
                                      │
                  ┌───────────────────┴───────────────────┐
                  │                                       │
                  ↓                                       ↓
               Notion                              History Filter
          Current Execution                              │
                                                          ↓
                                                   History Candidate
                                                          │
                                                          ↓
                                                    Daily History
                                                          │
                                                          ↓
                                             Desktop 4 Local Master
                                                          │
                                         ┌────────────────┴──────────────┐
                                         ↓                               ↓
                                  Monthly History                  GitHub Backup