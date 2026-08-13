# MCP Python SDK 2.0.0 분석 — 1.x 대비 변경점

> **검증 방법**: SDK **1.29.0**(프로토콜 2025-11-25)과 **2.0.0**(프로토콜 2026-07-28)을 나란히
> 설치해 타입 심볼·모듈 구조·클래스 시그니처를 **직접 diff**한 실측 + 공식 릴리스 노트 교차 확인.
> 연관 문서: [기본 개념과 구조](MCP_기본개념_구조.md) — 2026-07-28 스펙 변경 자체는 이쪽 참고.

---

## 0. 한눈 요약

| 영역 | 변경 |
|---|---|
| 프로토콜 | **2026-07-28 (stateless 코어)** 정렬 — 단, 구모델 타입·`initialize` 전부 잔존(하위호환) |
| 서버 | **`mcp.server.fastmcp` 모듈 제거** → `mcp.server.mcpserver.MCPServer` (데코레이터 API는 유지, import 경로는 breaking). `stateless_http` 옵션 소멸 — **stateless가 유일 모드** |
| 클라이언트 | 신규 고수준 **`Client`** 클래스 (연결+세션+MRTR 자동 처리 통합). `ClientSession`은 유지·확장 |
| 전송 | **websocket 제거**(양쪽), SSE 재개(resumability) 제거, Streamable HTTP 현대화 |
| 패키징 | 타입이 별도 패키지 **`mcp-types`** 로 분리, **httpx → `httpx2`**, OpenTelemetry 기본 포함, pydantic-settings 제거 |
| 1.x 운명 | **maintenance mode** — 보안 수정만 (릴리스 노트 명시) |

## 1. 버전·패키징 (실측)

| | 1.29.0 | 2.0.0 |
|---|---|---|
| 대상 프로토콜 | `2025-11-25` | `2026-07-28` |
| requires-python | >=3.10 | >=3.10 (동일) |
| 주요 의존성 | httpx(+httpx-sse), pydantic-settings, sse-starlette 1.x | **`mcp-types==2.0.0`**(신규 분리), **`httpx2>=2.5`**, `opentelemetry-api`, sse-starlette 3.x, starlette 명시. httpx-sse·pydantic-settings 제거 |

- `mcp.types`는 이제 `mcp_types` 패키지의 **영구 별칭**. 리비전별 와이어 타입은
  `mcp_types._v*`로 **private** (릴리스 노트) — 앱 코드는 `mcp.types`만 보면 됨.
- `MCP_*` 환경변수 설정 방식 제거 (pydantic-settings와 함께).

## 2. 타입 계층 변경 (심볼 diff 실측)

**추가 31종** — 전부 2026-07-28 스펙 기능의 구현체:

| 그룹 | 심볼 |
|---|---|
| stateless `_meta` 키 | `PROTOCOL_VERSION_META_KEY` · `CLIENT_CAPABILITIES_META_KEY` · `CLIENT_INFO_META_KEY` · `SERVER_INFO_META_KEY` · `LOG_LEVEL_META_KEY` |
| 서버 발견 | `DiscoverRequest` / `DiscoverResult` |
| MRTR | `InputRequiredResult` · `InputRequest(s)` · `InputResponse(s)` · `InputResponseRequestParams` · `ResultType` · `CORE_RESULT_TYPES` |
| 구독 재편 | `SubscriptionsListenRequest/Result` · `SubscriptionsAcknowledgedNotification` · `SubscriptionFilter` |
| 캐시 | `CacheableResult` (ttlMs/cacheScope) |
| 신규 오류 | `UNSUPPORTED_PROTOCOL_VERSION` · `MISSING_REQUIRED_CLIENT_CAPABILITY` · `HEADER_MISMATCH` (+ErrorData류) |

**제거 17종** — 핵심은 **tasks의 코어 퇴출**: `TASK_REQUIRED/OPTIONAL/FORBIDDEN`,
`TASK_STATUS_*`, `TaskExecutionMode` 등 태스크 상수·모드 일괄 제거 (공식 확장
`io.modelcontextprotocol/tasks`로 이동). 나머지는 내부 타입 별칭 정리.

**잔존 확인 (하위호환)**: `InitializeRequest/Result`, `InitializedNotification`, `PingRequest`,
`SetLevelRequest`, `Subscribe/UnsubscribeRequest`, `CreateMessageRequest`(sampling),
`ListRootsRequest`, `ElicitRequest` — **구모델 심볼 전부 유지**.

## 3. 클라이언트 변경

### 3-1. 신규 고수준 `Client` (권장 진입점)

v1의 "transport 열고 → ClientSession 만들고 → `initialize()`" 3단 수동 조립을 대체:

```python
Client(server, ...)   # server = URL | stdio 서브프로세스 | 커스텀 transport | 인메모리 서버 객체
```

주목할 생성자 파라미터 (실측):
- `input_required_max_rounds` — **MRTR 왕복을 클라이언트가 자동 루프** (input_required →
  콜백 → 재시도를 SDK가 대행, 상한 지정)
- `cache` / `CacheConfig` — `ttlMs`/`cacheScope` 기반 **응답 캐시 내장**
- `prior_discover` / `mode` — `server/discover` 결과 재사용·동작 모드 제어
- `listen()` 메서드 — `subscriptions/listen` 스트림 구독
- 유예 기간용 콜백들(sampling/roots/logging) 잔존

### 3-2. `ClientSession` 메서드 diff (실측)

| 추가 | 제거 |
|---|---|
| `discover` / `send_discover` (server/discover) | `get_server_capabilities` (세션 상태 전제 → discover로 대체) |
| `dispatch_input_request` (MRTR 수동 처리) | `add_response_router` |
| `validate_tool_result` (outputSchema 검증) | |
| `adopt` | |

`initialize()`는 **잔존** — 구모델 서버 접속용.

### 3-3. 신규 클라이언트 모듈 (실측)

`_probe`(stdio 구서버 하위호환 탐침), `_input_required`(MRTR), `caching`, `subscriptions`,
`extension`, `client`(고수준 Client), `_transport`, `_memory`(인메모리 연결 — 테스트용).
**제거**: `experimental`, `websocket`.

## 4. 서버 변경 (가장 큰 breaking)

### 4-1. FastMCP → MCPServer

- `from mcp.server.fastmcp import FastMCP` → **`ModuleNotFoundError`** (실측).
  신규: `from mcp.server.mcpserver import MCPServer`.
- 릴리스 노트: "**데코레이터 API는 변경 없음**; 저수준 Server는 공유 dispatcher 엔진
  중심으로 재구축" — `@server.tool()`/`resource`/`prompt` 스타일은 유지, import만 교체.
- 실행 메서드 유지: `run` / `run_stdio_async` / `run_streamable_http_async` / `run_sse_async`,
  ASGI 팩토리 `streamable_http_app` / `sse_app`.

### 4-2. 생성자 파라미터 diff (실측) — 스펙 전환이 그대로 반영됨

| 제거 (1.29 FastMCP) | 이유 |
|---|---|
| **`stateless_http`** | **stateless가 유일 모드가 되어 옵션 자체가 소멸** |
| `event_store`, `retry_interval` | SSE 재개/재전송(Last-Event-ID) 스펙 제거와 정합 |
| `host`, `port`, `mount_path`, `sse_path`, `message_path`, `streamable_http_path` | 생성자에서 배포 관심사 분리 (실행 메서드/ASGI 앱으로) |
| `json_response`, `max_request_body_size`, `transport_security` | 전송 계층 재편 (4 MiB 초과 바디는 HTTP 413 거부로 고정 — 릴리스 노트) |

| 추가 (2.0 MCPServer) | 용도 |
|---|---|
| **`request_state_security`** (+`AESGCMRequestStateCodec`) | **MRTR `requestState`를 암호화**해 클라이언트에 맡기는 코덱 — stateless 서버의 왕복 간 상태 위탁을 안전하게 |
| `cache_hints` | list/read 결과의 `ttlMs`/`cacheScope` 정책 |
| `subscriptions` | `subscriptions/listen` 구성 |
| `middleware`, `extensions`, `resource_security` | 미들웨어 체인·확장 프레임워크·리소스 접근 정책 |
| `title`, `description`, `version` | `server/discover` identity 광고 |

### 4-3. 신규 서버 모듈 (실측)

`mcpserver`, `runner`(요청 파이프라인), `connection`, `context`, `request_state`,
`subscriptions`, `caching`, `extension`, `apps`(MCP Apps 확장), `_streamable_http_modern`,
`_otel`(OTel 계측). **제거**: `fastmcp`, `experimental`, `websocket`.

`Elicit`/`Sample`/`ListRoots`/`Resolve`, `Accepted/Declined/CancelledElicitation` 등
상호작용 선언 심볼이 `mcpserver` 네임스페이스로 정리됨.

## 5. 하위호환 전략 (중요)

릴리스 노트 원문: "**v2는 2026-07-28 개정판을 지원하면서도 모든 2025-era 클라이언트를
동일 MCPServer에서, Streamable HTTP 및 stdio를 통해 제공**".

| 방향 | 지원 방식 |
|---|---|
| 2.0 서버 ← 구(1.x/2025-era) 클라이언트 | **듀얼 프로토콜 서빙** — 같은 MCPServer가 구모델 initialize 핸드셰이크도 수용 |
| 2.0 클라이언트 → 구 서버 | `_probe`(stdio 탐침) + `ClientSession.initialize()` 잔존 + 구모델 타입 전부 유지 |
| 1.x SDK | maintenance mode (보안 수정만) — 신기능 없음 |

## 6. ADK 관점 영향 (google-adk 1.26.0 기준)

1. **당장은 무영향**: ADK는 `mcp>=1.23,<2.0` 핀 — SDK 1.x에 고정되어 2.0 breaking과 무관.
2. **ADK(구 클라이언트) ↔ SDK 2.0 기반 신형 서버**: 서버가 MCPServer(듀얼 서빙)라면 **호환
   OK**. 위험은 SDK를 안 쓰거나 stateless 전용으로 직접 구현한 서버뿐.
3. **ADK가 언젠가 2.x로 올라갈 때의 마이그레이션 부담**: ADK는 클라이언트 역할만 하므로
   서버측 breaking(FastMCP)과 무관. `ClientSession`이 유지되므로
   `MCPSessionManager`의 골격은 살릴 수 있으나 — websocket 미사용(무관),
   `get_server_capabilities` 제거 대응, httpx→httpx2 의존성 충돌 검토가 필요 지점.
   고수준 `Client`(MRTR 자동 루프·캐시 내장)로 갈아타는 것이 장기적으론 자연스러움.
4. **MCP 서버를 직접 만들 때**: 지금 새로 시작한다면 SDK 2.0 `MCPServer` 권장 —
   1.x FastMCP로 짜면 곧 유지보수 모드 라인에 얹는 셈.

## 7. 마이그레이션 체크리스트

**서버 작성자 (1.x FastMCP → 2.0 MCPServer)**
- [ ] import 교체: `mcp.server.fastmcp.FastMCP` → `mcp.server.mcpserver.MCPServer`
- [ ] 생성자에서 host/port/path·`stateless_http`·`event_store` 제거 → 실행 메서드/ASGI 앱으로
- [ ] 세션에 상태를 쌓던 설계 제거 → 도구 인자 핸들 또는 `request_state_security` 코덱
- [ ] `cache_hints`로 ttlMs/cacheScope 정책 지정, 4 MiB 바디 제한 확인
- [ ] `MCP_*` 환경변수 의존 제거

**클라이언트 작성자**
- [ ] 수동 3단 조립 → 고수준 `Client` 검토 (MRTR `input_required_max_rounds` 필수 인지)
- [ ] `get_server_capabilities` 사용처 → `discover` 결과로 대체
- [ ] websocket 전송 사용 시 Streamable HTTP로 이관
- [ ] httpx 직접 공유하던 코드는 httpx2 전환 영향 검토
