# Analysis Docs

코드/프레임워크 동작 분석 문서 모음. 주제별로 디렉터리를 나눠 정리한다.

```
google-adk/
├── planreact-planner/   PlanReActPlanner — 프롬프트가 단계별로 어떻게 변하는가
├── agent-tool/          AgentTool — 에이전트를 도구로 감쌌을 때의 동작
└── web-server/          AdkWebServer — 세션 라이프사이클·메모리, 잔존 실행 관측
mcp/                     MCP(Model Context Protocol) — 개념·구조·다이어그램
freebuff/                Freebuff — 무료 AI 코딩 에이전트의 경제 구조 분석
agent-skills/            Agent Skills — 작성 표준·검증기·템플릿
```

---

# Google ADK (Python)

분석 기준 버전: **v1.26.0** (`google/adk-python`, 태그 `427a983b`)

## 1. PlanReActPlanner — 단계별 프롬프트 변화

📁 [`google-adk/planreact-planner/`](google-adk/planreact-planner/)

사용자 요청이 들어온 순간부터 최종 답변까지, **LLM이 보는 프롬프트가 단계별로 어떻게 변하는지** 소스 코드 기준으로 추적한 분석.

**읽는 순서**

| # | 문서 | 내용 |
|---|---|---|
| 0 | [분석보고서](google-adk/planreact-planner/PlanReActPlanner_분석보고서.md) | 전체 개요 · 호출 체인 · 종합 요약 (여기부터) |
| 1 | [단계 1 · 주입 프롬프트](google-adk/planreact-planner/PlanReActPlanner_단계1_주입프롬프트.md) | planner가 system_instruction에 주입하는 실제 프롬프트 원문 |
| 2 | [단계 2 · 응답 파싱](google-adk/planreact-planner/PlanReActPlanner_단계2_응답파싱.md) | LLM 응답의 태그 파싱과 `thought=True` 마킹 (누가 만드는가) |
| 3 | [단계 3 · 스텝 전환 / thought 언마킹](google-adk/planreact-planner/PlanReActPlanner_단계3_스텝전환_thought언마킹.md) | 툴 실행 → contents 재조립 → thought 언마킹 |
| 4 | [단계 4 · 루프 종료 / 최종답변](google-adk/planreact-planner/PlanReActPlanner_단계4_루프종료_최종답변.md) | `/*FINAL_ANSWER*/` split → 루프 종료 → 세션 저장 |
| 5 | [단계 5 · 두 번째 사이클](google-adk/planreact-planner/PlanReActPlanner_단계5_두번째사이클.md) | 다음 사용자 턴에서의 재진행 |
| 6 | [부록 · 단계별 프롬프트/응답 트레이스](google-adk/planreact-planner/PlanReActPlanner_단계별_프롬프트_응답_트레이스.md) | 실제 ADK instruction 포함 프롬프트 전문 + 스텝별 예상 응답 + 1·2턴 태그 내용 변화 |

**다이어그램** (draw.io — [app.diagrams.net](https://app.diagrams.net) 또는 VS Code Draw.io Integration 확장으로 열기)

| 파일 | 내용 |
|---|---|
| [전체 흐름도](google-adk/planreact-planner/PlanReActPlanner_flow.drawio) | 요청 → 전처리/주입 → LLM → 후처리/파싱 → 툴 loop → 최종답변 → 다음 사이클 |
| [태그 내용 변화표](google-adk/planreact-planner/PlanReActPlanner_태그내용변화.drawio) | 1·2턴에 걸친 각 태그(PLANNING/ACTION/REASONING/REPLANNING/FINAL_ANSWER)의 append-only 누적 |

### 핵심 요약

- PlanReActPlanner는 **모델 내장 thinking 없이** 태그(`/*PLANNING*/`, `/*REASONING*/`, `/*ACTION*/`, `/*REPLANNING*/`, `/*FINAL_ANSWER*/`) 기반으로 동작.
- **system 지침은 매 스텝 동일하게 재부착(정적)**, 실제로 변하는 것은 **contents(대화 히스토리) 누적**.
- 계획/추론 텍스트는 **사용자에겐 thought로 접히지만, 다음 스텝에서 모델에겐 평문으로 재노출**(마킹 → 언마킹 2단 구조).
- 프레임워크는 태그 블록 내용을 **편집하지 않음**(append-only, 동결).
- system_instruction 조립 순서: `agent 지침 → identity → planning → sub-agent(전송대상) 목록 → skills`.

## 2. AgentTool — 에이전트를 도구로 감쌌을 때

📁 [`google-adk/agent-tool/`](google-adk/agent-tool/)

| 문서 | 내용 |
|---|---|
| [AgentTool 동작 분석](google-adk/agent-tool/AgentTool_동작분석.md) | `AgentTool(LlmAgent)` 의 동작을 일반 agent 흐름 및 sub-agent 전송과 **대비**해 정리 |
| [AgentTool + SkillToolset 폭주 루프→OOM 원인 분석](google-adk/agent-tool/AgentTool_SkillToolset_OOM_원인분석.md) | 무한 재시도 루프 + AgentTool의 은폐/상한 리셋/빈-결과 재호출 구조(코드 사실·실측), upstream 수정 버전 매트릭스, 검증된 완화책 |
| [폭주 루프 재현 번들](google-adk/agent-tool/repro/) | 재현 스크립트(`repro_oom.py`, mock LLM·워치독 내장) + 셋업/시나리오/실측 결과/검증된 완화책 |

### 핵심 요약

- AgentTool은 에이전트를 부모 LLM의 **함수(tool)** 로 노출하고, 호출되면 **격리된 Runner/세션에서 자식을 완주**시킨 뒤 **마지막 응답 텍스트만 function_response로 반환**한다 (블랙박스 위임).
- **sub-agent 전송과의 차이**: 전송은 *대화 주체 교체*(컨텍스트 공유·제어권 이전), AgentTool은 *위임 후 결과 회수*(세션 격리·부모가 제어권 유지).
- 상태는 **양방향** — 시작 시 부모 state 복사 입력, 실행 중 자식 `state_delta`를 부모로 역전파.
- **tool 이름 = `agent.name`**, 설명 = `agent.description` (별도 rename 옵션 없음).
- 리소스: 아티팩트·자격증명은 공유, **메모리·세션은 격리**, 플러그인은 `include_plugins`로 선택.

## 3. AdkWebServer — 세션 라이프사이클과 메모리 관측

📁 [`google-adk/web-server/`](google-adk/web-server/)

| 문서 | 내용 |
|---|---|
| [세션 라이프사이클 · 메모리 분석](google-adk/web-server/AdkWebServer_세션_라이프사이클_메모리분석.md) | 마스터/스냅샷 구조(재사용 없음은 의도), 요청 스코프 객체 회수 실측, InMemory↔Database 성장 벡터 비교, `delete_session` 계약, cleanup 권고 |
| [잔존 실행 관측 구현명세](google-adk/web-server/AdkWebServer_잔존실행_관측_구현명세.md) | RunTrackerPlugin·객체 프로브·디버그 API·주기 메모리 census(C1~C5) 명세 — **1.26.0 실검증 완료(9/9)**, 구현 함정 2건 반영 |
| [참조 구현 + 검증 테스트](google-adk/web-server/test_runtracker.py) | 명세의 참조 구현과 T1~T6 검증 스크립트 (mock LLM, 네트워크 불필요) |

### 핵심 요약

- 세션 스냅샷을 매 요청 새로 만드는 것은 **격리를 위한 의도된 설계**이며, 정상 경로에서 요청 스코프 객체는 **전부 회수됨** (weakref/gc 실측).
- "잔존 인스턴스"는 **잔존 실행(버려진 `/run_sse` 등)의 그림자** — 실행 중 invocation당 `Session 1 + InvocationContext 2`, 종료 시 수 턴 내 회수.
- 실제 성장 벡터: **(A)** InMemory 마스터 무한 성장(evict 없음) **(B)** SSE 끊김 미처리(1.26.0, upstream `6a533573`이 후속 수정) **(C)** 요청당 전체 히스토리 로드.
- 관측 체계: after_run이 finally가 아닌 성질을 이용한 RunTracker 판정 + 코루틴 객체 열거 기반 정지 지점 덤프(`Task.get_stack()`·FrameType 열거는 함정) + 주기 census로 점진 증가 귀속.

---

# MCP (Model Context Protocol)

📁 [`mcp/`](mcp/)

LLM 앱↔외부 시스템 통합 표준 프로토콜의 개념·구조 정리. 공식 스펙 changelog(**2026-07-28
stateless 개정**), mcp Python SDK 2.0.0(`2026-07-28`)·1.29.0(`2025-11-25`) 실측,
google-adk 1.26.0 `tools/mcp_tool/` 소스로 검증.

| 문서 | 내용 |
|---|---|
| [기본 개념과 구조](mcp/MCP_기본개념_구조.md) | N×M 문제, Host/Client/Server, **현행 stateless 코어(2026-07-28) vs 구모델(≤2025-11-25)**, 프리미티브(서버 3종 유지 / Sampling·Roots·Logging deprecated·MRTR), 전송, 리비전 이력, SDK·ADK 호환 현황 |
| [프리미티브 상세](mcp/MCP_프리미티브_상세.md) | Tools/Resources/Prompts 심층 — SDK 2.0.0 실측 필드 구조, 메서드·결과 형식, ToolAnnotations, URI 템플릿, 콘텐츠 블록, 비교표, ADK 지원 현황 |
| [SDK 2.0.0 분석](mcp/MCP_SDK_2.0_분석.md) | 1.29.0↔2.0.0 API 표면 실측 diff — FastMCP→MCPServer(stateless 유일 모드), 신규 고수준 Client(MRTR 자동 루프·캐시), 타입 31+/17-, 듀얼 프로토콜 하위호환, ADK 영향과 마이그레이션 체크리스트 |
| [도구 응답 실전 패턴](mcp/MCP_도구응답_실전패턴.md) | 공식 레퍼런스 서버 7종 소스 조사 + FastMCP 반환 변환 규칙 + 타입별(텍스트/dict/파일/URL) wire 응답 실측 — 1·2세대 패턴, FastMCP 함정, ADK 소비 관점 |
| [다중 서버 단일 프로젝트 구성](mcp/MCP_다중서버_단일프로젝트_구성.md) | 한 파이썬 프로젝트로 URL별 분리된 MCP 서버 N개 제공 — path 분리(A안)·포트 분리(B안) 모두 PoC 검증, 배포 분리 확장(C안), lifespan 함정, 운영 비교표 (+PoC 스크립트 2종) |
| [다분야 도구관리 가이드](mcp/MCP_다분야_도구관리_가이드.md) | 한 서버에 여러 분야 도구가 쌓일 때 — 도구당 선언 비용 실측(100개=1.3만 토큰), 서버 분리 우선, tool_filter·동적 노출·라우터 패턴·네이밍·description 다이어트 비교 |
| [프로젝트 내부구조 가이드](mcp/MCP_프로젝트_내부구조_가이드.md) | 한 저장소에서 서버들을 코드로 분리하는 법 — 디렉터리=소유권 경계, 스캔+opt-in+실패격리 발견, 의존 방향 CI 강제, 네이밍·안티패턴 (+검증 스캐폴드) |
| [DB 커넥션 중앙화 설계](mcp/MCP_DB커넥션_중앙화_설계.md) | 다중 MCP 서버의 DB 도구에서 커넥션 중앙화의 타당성 비판 검토 + DBHub 패턴(타깃별 엔진 캐시·allowlist·가드레일·lifespan) PoC 검증 + 담당자 계약·커넥션 예산 |
| [프리미티브 기능 구성도 (draw.io)](mcp/MCP_프리미티브_기능구성.drawio) | 2페이지 — (간단) 제어 주체→프리미티브→서버 구성 / (상세) 메서드·공식 사용 사례·공통 요소·조합 시나리오 |
| [개요도 (draw.io)](mcp/MCP_개요도.drawio) | Host ⊃ Client(1:1) ↔ Server 생태계, 전송별 연결, 역방향 프리미티브 |
| [구조도 (draw.io)](mcp/MCP_구조도.drawio) | 프로토콜 계층 스택 · 프리미티브 상세 · 세션 라이프사이클 시퀀스 · ADK 매핑 |

### 핵심 요약

- MCP = LLM 앱↔외부 시스템 통합의 공용 규격 (JSON-RPC 2.0, N×M→N+M).
- 구조 = **Host ⊃ Client(서버당 1:1) ↔ Server** + 교체 가능한 전송(stdio / Streamable HTTP).
- **2026-07-28부터 코어 stateless**: 세션·initialize 핸드셰이크 제거, 요청이 `_meta`로 자기기술, `server/discover`로 발견, 서버발 상호작용은 **MRTR**(input_required→재시도)·**subscriptions/listen**으로 재편.
- 서버 프리미티브 **Tools/Resources/Prompts** 유지(+ttlMs/cacheScope 캐시 필드), 클라이언트측은 **Elicitation(MRTR)만 실질 존속** — Sampling/Roots/Logging deprecated(12개월+ 유예).
- ADK는 호스트 구현체(`McpToolset→MCPSessionManager→McpTool`)이나 **`mcp<2.0` 핀 = 구모델 클라이언트** — stateless 전용 서버와의 호환 유의.

---

# Freebuff — 무료 제공 구조

📁 [`freebuff/`](freebuff/)

[`CodebuffAI/freebuff`](https://github.com/CodebuffAI/freebuff)가 **어떻게 무료로 제공되는지**를
공개 스냅샷 코드 기준으로 복원한 분석. 분석 기준 커밋 **`90e6615`** (2026-08-26).

| 문서 | 내용 |
|---|---|
| [무료 제공 구조 분석](freebuff/Freebuff_무료제공_구조_분석.md) | 원가 억제 · 배급 · 수익화 · 방어 4축 + Trust/Level 획득 루프. 실측 원가표, 프로바이더 캐스케이드 단가, 세션·신뢰레벨 매트릭스, 광고 지면·참여 마켓플레이스 가격, 재판매 방어 계층, 단위 경제 추정 |
| [무료 구조도 (draw.io)](freebuff/Freebuff_무료구조.drawio) | 수입↔원가↔배급↔방어 자금 흐름 한 장 + 획득 루프 + 사용자가 실제로 지불하는 것 |

### 핵심 요약

- README는 "텍스트 광고가 모델을 지원한다"고만 말하지만, **광고 단독으로는 이 구조가 지탱되지 않는다.**
  성립 조건은 `원가 억제 × 배급 + 수익화 + 방어`.
- **원가 억제가 가장 무거운 축**: 18개 프로바이더 레인의 최저가 캐스케이드(세션 단위 고정 —
  토큰의 ~96.5%가 cache read라 레인을 옮기면 콜드 프리필 재지불), DeepSeek 피크 2배 요금 창
  전체 차단(세션이 1시간이라 1시간 리드타임), 서브에이전트를 없앤 base3 단일 루프 하네스,
  양자화 빌드. 실측 세션(1h) 원가 — Luna $0.758 / V4 Pro $0.605 / **V4 Flash $0.156**.
- **배급 4층이 곱해진다**: 1시간 세션(모델 바인딩) × 지역 티어(IP 국가) × 계정 신뢰 레벨 ×
  지출 실링(최솟값이 이김). 약한 신호는 **밴이 아니라 캡** — 659계정 오탐 밴을 수작업 복구한
  기록이 근거로 남아 있다.
- **Trust/Level 루프가 네 축을 묶는다**: 참여 1건 = 광고주에게 $0.50 청구 = 사용자에게 Trust 50
  = 프롬프트 50개. 프롬프트마다 Trust −1이라 **레벨 유지에 지속 참여가 필요**하고, 그 결과
  무료 용량 확대의 재원을 그 용량을 요구하는 행위 자체가 조달한다.
- **`gravity_index` 툴 = 에이전트에 내장된 제휴 전환 채널**. 시스템 프롬프트가 "서비스를 기억만으로
  추천하지 말라"고 지시하고, 추적 setup URL을 강제 보존하며, **env var 저장이 "활성화"로 과금**된다
  (클릭 후 30일 귀속).
- **최대 위협은 헤비 유저가 아니라 재판매**. 시스템 프롬프트 0바이트 프리픽스 검사, 툴 시그니처
  (프롬프트와 달리 위조하면 진짜 클라이언트가 되어 버림), 차단 대신 **원가 0 모델로 다운그레이드**,
  Cloudflare가 엣지에서 찍어 제거 불가능한 `CF-Worker` 헤더.

---

# Agent Skills — 작성 표준

📁 [`agent-skills/`](agent-skills/)

Anthropic **Agent Skills**의 작성 표준. 공식 문서 4종과 Anthropic이 실제로 배포한 스킬
**40개 실측**을 근거로 정리했다. 조사 기준일 **2026-09-04**.

| 문서 | 내용 |
|---|---|
| [작성 표준](agent-skills/Agent_Skills_작성표준.md) | 세 규격의 중첩·이식성 등급, 3단계 로딩, 디렉터리·프론트매터 규격, description 작성법, 본문/번들/평가/배포/보안 표준, 최종 체크리스트 |
| [검증기](agent-skills/validate_skill.py) | 표준 §4.1의 하드 제약을 그대로 구현. 실측 40개 전부 ERROR 0으로 통과 확인 |
| [P0 템플릿](agent-skills/templates/SKILL.md) | 이식형 뼈대 — 스펙 6필드만, 어느 서피스에서나 로드됨 |
| [P1 템플릿](agent-skills/templates/SKILL.claude-code.md) | Claude Code 확장 뼈대 — 주입·인자·fork의 실패 모드 주석 포함 |
| [구조도 (draw.io)](agent-skills/Agent_Skills_구조도.drawio) | 3단계 로딩 · 규격 · 이름 규칙 · 수명주기 · 배포 경로 한 장 |

### 핵심 요약

- **규격이 세 겹이다.** 개방 표준 6필드 ⊂ Claude Code 확장 20필드, 그리고 claude.ai 업로드·
  Skills API·`package_skill.py`는 **6필드 밖을 만나면 무시가 아니라 하드 에러로 거부**한다.
  → 작성 전에 **이식성 등급(P0/P1)** 을 정하고, P0를 기본값으로 삼는다.
- **작성 규칙 대부분이 3단계 로딩에서 파생된다.** L1 메타데이터는 항상 켜져 있는 비용(→1,024자
  상한), L2 본문은 트리거 후 대화 끝까지 잔존(→500줄 제한), L3 번들은 읽기 전까지 **비용 0**
  (→큰 참조는 파일로 빼는 게 순이익, 스크립트는 출력만 토큰을 씀).
- **명령어 이름은 `name`이 아니라 위치에서 온다.** 개인/프로젝트 스킬은 **디렉터리 이름**이
  이기고 `name`은 표시 라벨일 뿐 — 이 환경에 설치된 `session-start-hook`(name은
  `startup-hook-skill`)이 산 증거다. 그래서 둘을 항상 일치시킨다.
- **`description`이 스킬의 성패다.** `[무엇] + [언제] + [언제 아닌지]`. 세 번째 절이 실측에서
  가장 두드러지는데 공식 문서엔 강조되어 있지 않다 — 600자 넘는 10개는 거의 전부
  "Do NOT / However" 절을 갖는다. 실측 길이 중앙값 **308자**, 최장 1,003자(상한의 98%).
- **실측이 권고선을 뒷받침한다.** 40개 중 `SKILL.md` 500줄 초과 **0건**(최장 485), `description`
  1,024자 초과 **0건**. 권고가 아니라 사실상의 표준으로 지켜지고 있다.
- **강제 어법은 안티패턴이다.** 대문자 `MUST`/`ALWAYS`가 눈에 띄면 경고 신호 — 재구성해서
  **왜 중요한지**를 설명하는 쪽이 더 효과적이라고 공식 스킬 제작 가이드가 명시한다.
- **트리거되는 것과 잘 되는 것은 다르다.** 평가는 항상 **새 세션**에서 A/B로. 스킬을 만들며
  쌓인 컨텍스트가 지침의 공백을 가린다. 트리거 정확도는 20개 쿼리(절반은 near-miss 부정
  케이스)를 3회씩 돌려 60/40 분할의 **test 점수**로 고른다.
