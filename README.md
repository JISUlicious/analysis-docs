# Analysis Docs

코드/프레임워크 동작 분석 문서 모음. 주제별로 디렉터리를 나눠 정리한다.

```
google-adk/
├── planreact-planner/   PlanReActPlanner — 프롬프트가 단계별로 어떻게 변하는가
├── agent-tool/          AgentTool — 에이전트를 도구로 감쌌을 때의 동작
└── web-server/          AdkWebServer — 세션 라이프사이클·메모리, 잔존 실행 관측
mcp/                     MCP(Model Context Protocol) — 개념·구조·다이어그램
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
