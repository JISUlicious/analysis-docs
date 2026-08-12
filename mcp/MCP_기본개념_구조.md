# MCP(Model Context Protocol) 기본 개념과 구조

> **검증 앵커**: mcp Python SDK **1.29.0** (`LATEST_PROTOCOL_VERSION = 2025-11-25` 상수 확인),
> google-adk 1.26.0의 의존성 `mcp>=1.23.0,<2.0.0` 및 `tools/mcp_tool/` 소스.
> 다이어그램: [개요도](MCP_개요도.drawio) · [구조도](MCP_구조도.drawio)

---

## 1. MCP란

**LLM 애플리케이션과 외부 시스템(데이터·도구) 사이의 연결을 표준화하는 오픈 프로토콜.**
2024년 11월 Anthropic이 공개했고, 스펙과 다국어 SDK(Python/TypeScript 등)가 오픈소스로
관리된다. 흔히 "AI의 USB-C"로 비유된다 — 애플리케이션마다 제각각이던 통합 커넥터를
하나의 규격으로 통일한다.

**해결하는 문제 — N×M 통합 폭발**:

```
표준화 전:  앱 N개 × 연동 대상 M개 = N×M 개의 커스텀 통합
표준화 후:  앱은 MCP 클라이언트 1번, 대상은 MCP 서버 1번 = N+M
```

앱(호스트)은 어떤 MCP 서버든 붙일 수 있고, 서버는 어떤 MCP 호스트에서든 재사용된다.

## 2. 아키텍처 — 3개 역할

| 역할 | 정의 | 예 |
|---|---|---|
| **Host** | LLM을 품고 전체를 조율하는 애플리케이션. 보안·동의·권한의 주체 | Claude Desktop, IDE, **ADK 에이전트 앱** |
| **Client** | Host 안에 존재하며 **서버와 1:1 연결**을 유지하는 프로토콜 커넥터 | Host가 서버 수만큼 생성 |
| **Server** | 도구·데이터·프롬프트를 표준 형식으로 노출하는 경량 프로세스/서비스 | filesystem, github, DB, 사내 API 래퍼 |

핵심 규칙: **클라이언트:서버 = 1:1**. 서버 3개를 쓰는 호스트는 클라이언트 3개를 만든다.
서버는 로컬(호스트가 서브프로세스로 실행)일 수도, 원격(HTTP 엔드포인트)일 수도 있다.

## 3. 프로토콜 기반

- **JSON-RPC 2.0** 메시지(요청/응답/알림)를 주고받는 **stateful 세션**.
- **라이프사이클**: `initialize` 요청(프로토콜 버전 + 자기 capability 제시) → 서버 응답
  (버전 합의 + 서버 capability) → `notifications/initialized` → 이후 정상 운용.
  **capability 협상**이 끝난 기능만 세션에서 사용 가능하다.
- **알림(notification)**: 응답이 없는 단방향 메시지. 목록 변경
  (`notifications/tools/list_changed` 등), 진행률(progress), 로그 등에 사용.

## 4. 프리미티브 — 서버가 주는 것 3가지, 클라이언트가 주는 것 3가지

### 4-1. 서버 → 호스트 (서버 프리미티브)

| 프리미티브 | 제어 주체 | 용도 | 주요 메서드 |
|---|---|---|---|
| **Tools** | **모델**이 호출 결정 | 실행 가능한 동작(함수). JSON Schema로 입력 정의 | `tools/list`, `tools/call` |
| **Resources** | **애플리케이션**이 선택 | 컨텍스트로 쓸 데이터(파일, DB 레코드…). URI로 식별 | `resources/list`, `resources/read`, `resources/subscribe` |
| **Prompts** | **사용자**가 선택 | 재사용 프롬프트 템플릿(슬래시 커맨드류) | `prompts/list`, `prompts/get` |

"누가 사용을 결정하는가"(model/app/user-controlled)가 세 프리미티브를 가르는 축이다.

### 4-2. 호스트 → 서버 (클라이언트 프리미티브)

서버가 거꾸로 호스트의 능력을 빌려 쓰는 방향. SDK `ClientSession` 생성자 파라미터로 실재
확인: `sampling_callback`, `elicitation_callback`, `list_roots_callback`, `logging_callback`.

| 프리미티브 | 용도 |
|---|---|
| **Sampling** | 서버가 호스트의 **LLM 호출을 요청** (서버는 API 키 없이 지능을 빌림) |
| **Roots** | 호스트가 서버에게 **작업 범위(디렉터리 등)를 알려줌** |
| **Elicitation** | 서버가 실행 중 **사용자에게 추가 입력/확인을 요청** |

## 5. 전송 계층 (transports)

| 전송 | 대상 | 특징 |
|---|---|---|
| **stdio** | 로컬 서버 | 호스트가 서버를 서브프로세스로 띄우고 stdin/stdout으로 JSON-RPC 교환. 가장 단순·보편 |
| **Streamable HTTP** | 원격 서버 | 단일 HTTP 엔드포인트(POST + 선택적 SSE 스트림). 2025-03-26 리비전에서 기존 HTTP+SSE 방식을 대체 |
| HTTP+SSE (legacy) | 원격 서버 | 구 방식 — deprecated, 호환용으로만 잔존 |

전송은 교체 가능한 계층이고, 위의 데이터 계층(JSON-RPC·프리미티브)은 전송과 무관하게 동일하다.

## 6. 스펙 리비전 이력

| 리비전 | 주요 내용 |
|---|---|
| 2024-11-05 | 최초 공개 스펙 (3 프리미티브, stdio/HTTP+SSE) |
| 2025-03-26 | **Streamable HTTP** 도입, **OAuth 2.1 기반 인증** 프레임, tool annotations |
| 2025-06-18 | **Elicitation** 추가, structured tool output(outputSchema), resource links, 인증 강화(리소스 서버 분류, RFC 8707 resource indicators) |
| **2025-11-25** | 현행 최신 (SDK 1.29.0 상수로 확인). 장기 실행 작업(**tasks**) 등 비동기 운용 확장 — SDK에 `experimental_task_handlers` 파라미터로 실재 |

인증 요지: 원격(HTTP) 서버는 OAuth 2.1 리소스 서버로 취급되며, 호스트/클라이언트가
토큰을 획득해 요청에 싣는다. stdio 서버는 프로세스 경계라 별도 스킴(환경변수 등)을 쓴다.

## 7. ADK에서의 MCP (google-adk 1.26.0, 소스 기준)

ADK는 **호스트/클라이언트 쪽**이다. `tools/mcp_tool/` 모듈이 MCP 서버를 ADK 도구로 편입한다:

```
McpToolset (BaseToolset)
 ├─ MCPSessionManager ── 연결 파라미터 3종:
 │    StdioConnectionParams / SseConnectionParams / StreamableHTTPConnectionParams
 │    (mcp SDK의 stdio_client / sse_client / streamablehttp_client 사용,
 │     세션 캐시·재연결 관리)
 ├─ get_tools() ──→ MCP tools/list → 각 tool을 McpTool로 변환
 │                   (conversion_utils: MCP 스키마 ↔ Gemini FunctionDeclaration)
 ├─ read_resource() / list_resources() ──→ MCP resources/*
 └─ close() ──→ 세션 정리 (Runner.close()가 toolset들을 일괄 close)

McpTool (BaseAuthenticatedTool)
 ├─ _get_declaration(): MCP inputSchema → FunctionDeclaration
 └─ run_async(): ADK 인증(헤더 주입) 후 session.call_tool(...) → 결과를 도구 응답으로
```

ADK 관점 정리:

- **Tools 중심 + Resources 보조**: `McpToolset`은 tools를 1급으로(에이전트 도구로 자동 편입),
  resources는 명시적 API(`read_resource`/`list_resources`)로 노출한다. prompts는 1.26.0
  toolset API에 노출되지 않는다.
- **에이전트 flow와의 관계**: MCP 도구도 결국 `BaseTool`이므로, 이 세션에서 분석한 도구
  실행 경로(함수콜 → `handle_function_calls_async` → `run_async`)와 라이프사이클
  (toolset 수집·close, 세션 관리)을 그대로 따른다.
- **tool_filter / 인증**: 서버가 주는 도구 중 일부만 선별 가능하며,
  `get_auth_config()`로 toolset 수준 인증(헤더)을 flow의 auth 단계와 연동한다.
- 참고(후속 버전): elicitation 클라이언트 콜백 지원은 1.26.0 이후 커밋(`48246195`
  "Support elicitation_callback in McpToolset")으로 추가 — 1.26.0에는 없음.

## 8. 한 장 요약

- MCP = **LLM 앱 ↔ 외부 시스템 통합의 공용 규격** (JSON-RPC 2.0 기반 stateful 프로토콜).
- 구조 = **Host ⊃ Client(서버당 1개) ↔ Server** + 교체 가능한 전송(stdio/Streamable HTTP).
- 내용 = 서버 프리미티브 **Tools/Resources/Prompts** (model/app/user-controlled) ↔
  클라이언트 프리미티브 **Sampling/Roots/Elicitation**.
- ADK는 `McpToolset`으로 MCP 서버의 tools를 자기 도구 체계에 흡수하는 **호스트 구현체**다.
