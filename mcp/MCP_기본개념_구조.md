# MCP(Model Context Protocol) 기본 개념과 구조

> **검증 앵커**: MCP 공식 스펙 changelog(리비전 **2026-07-28**), mcp Python SDK **2.0.0**
> (`LATEST_PROTOCOL_VERSION=2026-07-28`, `DiscoverRequest`/`InputRequiredResult` 타입 실재 확인)
> 및 SDK 1.29.0(`2025-11-25`), google-adk 1.26.0의 `mcp>=1.23.0,<2.0.0` 의존성과
> `tools/mcp_tool/` 소스.
> **주의**: 2026-07-28 리비전은 프로토콜 출시 이래 최대 개정으로, **코어를 stateless로
> 전환**했다. 이 문서는 현행(2026-07-28) 모델을 기준으로 쓰되, 구모델(≤2025-11-25)을
> 병기한다 — 현장의 SDK/호스트 다수(ADK 포함)는 아직 구모델이다.
> 다이어그램: [개요도](MCP_개요도.drawio) · [구조도](MCP_구조도.drawio)

---

## 1. MCP란

**LLM 애플리케이션과 외부 시스템(데이터·도구) 사이의 연결을 표준화하는 오픈 프로토콜.**
2024년 11월 Anthropic이 공개했고, 스펙과 다국어 SDK가 오픈소스로 관리된다.
흔히 "AI의 USB-C"로 비유된다.

**해결하는 문제 — N×M 통합 폭발**:

```
표준화 전:  앱 N개 × 연동 대상 M개 = N×M 개의 커스텀 통합
표준화 후:  앱은 MCP 클라이언트 1번, 대상은 MCP 서버 1번 = N+M
```

## 2. 아키텍처 — 3개 역할 (리비전 무관하게 동일)

| 역할 | 정의 | 예 |
|---|---|---|
| **Host** | LLM을 품고 전체를 조율하는 애플리케이션. 보안·동의·권한의 주체 | Claude Desktop, IDE, **ADK 에이전트 앱** |
| **Client** | Host 안에서 **서버와 1:1 연결**을 담당하는 프로토콜 커넥터 | Host가 서버 수만큼 생성 |
| **Server** | 도구·데이터·프롬프트를 표준 형식으로 노출하는 프로세스/서비스 | filesystem, github, DB, 사내 API 래퍼 |

## 3. 프로토콜 코어 — 현행 stateless 모델 (2026-07-28)

JSON-RPC 2.0 메시지 교환은 유지하되, **프로토콜 수준 세션과 초기화 핸드셰이크가 제거**됐다.

| 항목 | 현행 (2026-07-28) | 구모델 (≤2025-11-25) |
|---|---|---|
| 세션 | **없음** — `Mcp-Session-Id` 헤더 제거. 요청 간 상태가 필요하면 서버가 발급한 핸들을 **일반 tool 인자로** 전달 (SEP-2567) | `initialize`로 수립되는 stateful 세션 |
| 초기화 | **핸드셰이크 없음** — 모든 요청이 `_meta`에 프로토콜 버전·클라이언트 capability·identity를 자체 포함 (`io.modelcontextprotocol/protocolVersion`·`clientCapabilities`·`clientInfo`). 버전 불일치 시 `UnsupportedProtocolVersionError` | `initialize` → capability 협상 → `notifications/initialized` |
| 서버 발견 | **`server/discover`** RPC (서버 필수 구현): 지원 버전·capability·identity 광고. 클라이언트가 사전 버전 선택이나 **stdio 하위호환 탐침**으로 사용 | initialize 응답이 그 역할 |
| 서버발 알림 | **`subscriptions/listen`** — 단일 장수명 POST 응답 스트림에 **opt-in 구독**(toolsListChanged 등). 요청 스코프 알림(progress, message)은 해당 요청의 응답 스트림으로 | HTTP GET 스트림 + `resources/subscribe` |
| 서버→클라이언트 요청 | **MRTR 패턴**(§4-2) — 서버가 `resultType:"input_required"` + `inputRequests`로 응답하면, 클라이언트가 `inputResponses`를 실어 **원 요청을 재시도** | 세션 위에서 서버가 역방향 요청 전송 (`elicitation/create` 등) |
| 결과 형식 | 모든 결과에 **`resultType` 필수** (`"complete"` / `"input_required"`). 구서버가 생략하면 complete로 간주 | 없음 |

이 전환의 목적: **요청이 자기완결적**이 되어 세션 어피니티가 불필요 → 평범한 라운드로빈
로드밸런서 뒤에서 아무 서버 인스턴스나 아무 요청을 처리 가능 (수평 확장·서버리스 친화).

## 4. 프리미티브

### 4-1. 서버 프리미티브 (서버 → 호스트) — 현행에서도 3종 유지

| 프리미티브 | 제어 주체 | 용도 | 주요 메서드 |
|---|---|---|---|
| **Tools** | **모델**이 호출 결정 | 실행 가능한 동작. JSON Schema(2020-12 전체 키워드 허용)로 입출력 정의 | `tools/list`, `tools/call` |
| **Resources** | **애플리케이션**이 선택 | 컨텍스트 데이터. URI 식별 | `resources/list`, `resources/read` (구독은 `subscriptions/listen`으로 통합) |
| **Prompts** | **사용자**가 선택 | 재사용 프롬프트 템플릿 | `prompts/list`, `prompts/get` |

- 2026-07-28부터 list 계열·`resources/read` 결과에 **`ttlMs`/`cacheScope` 필수**
  (`CacheableResult`) — 클라이언트/중간 캐시 활용. `tools/list`는 **결정적 순서** 권장
  (LLM 프롬프트 캐시 적중률 목적).

### 4-2. 클라이언트 프리미티브 — 대거 정리됨 (중요 변화)

| 구모델의 기능 | 현행 (2026-07-28) 상태 |
|---|---|
| **Elicitation** (사용자 입력 요청) | **유지하되 MRTR로 전달 방식 변경** — 서버발 `elicitation/create` 요청 대신, 원 요청의 응답에 `input_required`로 실려 오고 클라이언트가 재시도로 응답 |
| **Sampling** (호스트 LLM 빌려 쓰기) | **Deprecated** — 신규 구현 비권장. 권장 대안: 서버가 LLM 프로바이더 API 직접 사용 |
| **Roots** (작업 범위 통지) | **Deprecated** — 대안: 디렉터리/파일을 tool 인자·리소스 URI·서버 설정으로 전달 |
| Logging (`logging/setLevel`, `notifications/message`) | **Deprecated/제거** — 로그 레벨은 요청별 `_meta`의 `logLevel`로; 대안은 stderr(OTel) |
| `ping` | 제거 |

구모델(≤2025-11-25)의 "서버가 호스트 능력을 빌리는 역방향 3종(Sampling/Roots/Elicitation)"
구도는, 현행에서 **"MRTR을 통한 Elicitation 하나"로 수렴**하는 방향이다.
(Deprecated 기능은 최소 12개월의 제거 유예 기간 동안은 동작한다 — §6 거버넌스.)

## 5. 전송 계층

| 전송 | 대상 | 현행 (2026-07-28) 상태 |
|---|---|---|
| **stdio** | 로컬 서버 | 유지. 서브프로세스 stdin/stdout으로 JSON-RPC (`server/discover`가 하위호환 탐침) |
| **Streamable HTTP** | 원격 서버 | 유지하되 대폭 단순화: 세션 헤더 제거, GET 스트림 → `subscriptions/listen`으로 대체, **SSE 재개/재전송(Last-Event-ID) 제거** — 스트림 끊기면 새 요청으로 재발행. POST에 `Mcp-Method`/`Mcp-Name` 표준 헤더 필수 (**본문 파싱 없이 라우팅/정책 적용 가능** = routable headers) |
| HTTP+SSE (구식) | 원격 서버 | 2025-03-26부터 비권장 → 정식 **Deprecated** 등재 |

## 6. 스펙 리비전 이력과 거버넌스

| 리비전 | 주요 내용 |
|---|---|
| 2024-11-05 | 최초 공개 (3 프리미티브, stdio/HTTP+SSE) |
| 2025-03-26 | Streamable HTTP 도입, OAuth 2.1 인증 프레임, tool annotations |
| 2025-06-18 | Elicitation 추가, structured tool output, 인증 강화(RFC 8707 resource indicators) |
| 2025-11-25 | tasks(실험) 등 — SDK 1.29.0의 대상 리비전 |
| **2026-07-28** | **최대 개정**: stateless 코어(세션·initialize 제거, `_meta` 자기기술), `server/discover`, MRTR, `subscriptions/listen`, 캐시 필드 필수화, routable headers, **Roots/Sampling/Logging deprecated**, tasks를 공식 확장(`io.modelcontextprotocol/tasks`, 폴링형)으로 이동, 확장(extensions) 프레임워크, 인증 강화(RFC 9207 iss 검증, DCR → Client ID Metadata Documents 방향) |

거버넌스: **기능 라이프사이클 정책**(Active → Deprecated → Removed, **최소 12개월 유예**)과
deprecated 기능 레지스트리, PR 기반 SEP 프로세스가 이번 리비전에서 공식화됐다.

## 7. SDK·ADK 현황 (실측)

| 항목 | 확인 내용 |
|---|---|
| mcp Python SDK **2.0.0** | `LATEST_PROTOCOL_VERSION=2026-07-28`. 신 타입 실재: `DiscoverRequest`/`DiscoverResult`/`InputRequiredResult`. `ClientSession`에 `extensions`·`log_level`·`notification_bindings` 등 추가 (구 콜백들은 유예 기간용으로 잔존) |
| mcp Python SDK 1.29.0 | `2025-11-25` 대상 (구모델 최종선) |
| **google-adk** (1.26.0 기준) | 의존성 **`mcp>=1.23.0,<2.0.0`** — **SDK 1.x = 구모델(stateful)에 고정.** `McpToolset → MCPSessionManager(Stdio/SSE/StreamableHTTP 연결 3종, 세션 캐시·재연결) → McpTool(inputSchema→FunctionDeclaration, run_async→call_tool)` 구조는 구모델의 세션 수립을 전제로 한다 |

**ADK 사용자 관점 함의**:

1. ADK가 SDK 2.x로 올라가기 전까지, ADK 호스트는 **구모델 클라이언트**다. 신형(2026-07-28
   전용) 서버와의 직접 호환은 서버 측의 하위호환 제공 여부에 달려 있다 — 유예 기간 중에는
   대부분 양쪽을 지원하지만, **stateless 전용으로 새로 구현된 서버**와는 협상이 실패할 수 있다.
2. 반대로 ADK로 붙일 MCP **서버를 고를 때**: 신 스펙 기준으로 세션이 사라졌으므로, 서버가
   "세션에 상태를 쌓는" 설계라면 신 스펙에서는 tool 인자 핸들 방식으로 재설계됐는지 확인.
3. 이 세션에서 다룬 ADK 리소스 정리 이슈(MCP stdio 서브프로세스와 `runner.close()`)는
   전송이 stdio인 한 리비전과 무관하게 유효하다.

## 8. 한 장 요약

- MCP = LLM 앱↔외부 시스템 통합의 공용 규격 (JSON-RPC 2.0).
- 구조 = **Host ⊃ Client(서버당 1:1) ↔ Server** + 교체 가능한 전송(stdio / Streamable HTTP).
- **2026-07-28부터 코어가 stateless**: 세션·initialize 제거, 요청이 `_meta`로 자기기술,
  `server/discover`로 발견, 서버발 상호작용은 **MRTR**(input_required→재시도)와
  **subscriptions/listen**(opt-in 알림 스트림)으로 재편.
- 서버 프리미티브 **Tools/Resources/Prompts**는 유지(+캐시 필드), 클라이언트 프리미티브는
  **Elicitation(MRTR)만 실질 존속** — Sampling/Roots/Logging은 deprecated.
- ADK는 현재 **SDK 1.x 핀(<2.0)** = 구모델 호스트 — 신형 전용 서버와의 호환에 유의.
