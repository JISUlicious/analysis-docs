# MCP 서버 프리미티브 상세 — Tools · Resources · Prompts

> **검증 앵커**: mcp Python SDK **2.0.0** (`LATEST_PROTOCOL_VERSION=2026-07-28`)의 타입 정의를
> 직접 introspect한 필드 구조 + 공식 스펙 changelog. 리비전 표기가 없는 내용은 리비전 무관.
> 연관 문서: [기본 개념과 구조](MCP_기본개념_구조.md) · [기능 구성도](MCP_프리미티브_기능구성.drawio)

---

## 0. 세 프리미티브를 가르는 축 — "누가 사용을 결정하는가"

| 프리미티브 | 제어 주체 | 한 줄 정의 | 대표 상황 |
|---|---|---|---|
| **Tool** | **모델** (model-controlled) | LLM이 호출을 결정하는 **실행 가능한 동작** | "파일을 검색해줘" → 모델이 search 도구 호출 |
| **Resource** | **애플리케이션** (app-controlled) | 호스트 앱이 컨텍스트로 주입할 **데이터** | 사용자가 채팅에 파일을 첨부 → 앱이 resource read |
| **Prompt** | **사용자** (user-controlled) | 사용자가 명시적으로 고르는 **템플릿** | 슬래시 커맨드 `/review-code` 선택 |

같은 정보라도 노출 방식이 달라진다: DB 스키마를 "조회 도구"로 주면 모델이 알아서 부르고,
"리소스"로 주면 앱/사용자가 골라 컨텍스트에 넣고, "프롬프트 인자"로 주면 사용자가 템플릿과
함께 명시적으로 소환한다. **서버 설계란 이 세 노출 방식의 배분 결정**이다.

각 프리미티브는 서버 capability(`tools`/`resources`/`prompts`)로 광고되며, 목록 조회는
공통적으로 **커서 기반 페이지네이션**(`nextCursor`)을 쓴다. 2026-07-28부터 모든 list 계열
결과에 **`ttlMs`(신선도 힌트)·`cacheScope`(`public`/`private`)** 필드가 필수다
(SDK `ListToolsResult` 필드로 실측 확인: `ttl_ms`, `cache_scope`, `next_cursor`).

---

## 1. Tools — 모델이 부르는 실행 동작

### 1-1. 선언 구조 (`Tool` — SDK 실측 필드)

| 필드 | 설명 |
|---|---|
| `name` | 프로그램적 식별자 (호출 시 사용) |
| `title` | 사람에게 보여줄 표시명 (없으면 name 표시) |
| `description` | **모델이 "언제 이 도구를 쓸지" 판단하는 근거** — 사실상 프롬프트의 일부 |
| `inputSchema` | 입력 정의 — JSON Schema (2026-07-28부터 2020-12 전체 키워드 허용) |
| `outputSchema` | 구조화 출력 스키마 (선택, 2025-06-18~) — 있으면 결과의 `structuredContent`가 이를 따라야 함 |
| `annotations` | 행동 힌트 (1-3) |
| `icons` | UI 표시용 아이콘 |
| `execution` | 실행 방식 메타데이터 — `taskSupport` (tasks 확장 연동, 장기 실행 도구 표시) |

### 1-2. 메서드와 결과

- **`tools/list`** → `Tool[]` + 페이지네이션 + 캐시 필드. 2026-07-28부터 **결정적 순서** 권장
  — 목록이 LLM 프롬프트에 들어가므로 순서가 고정돼야 **프롬프트 캐시**가 적중한다.
- **`tools/call`** `(name, arguments)` → `CallToolResult`:

| 필드 | 설명 |
|---|---|
| `content` | **콘텐츠 블록 배열** — 텍스트/이미지/오디오/리소스 링크/embedded resource (§4) |
| `structuredContent` | `outputSchema`를 따르는 구조화 결과 (JSON) |
| `isError` | **도구 실행 실패 플래그** — 중요: JSON-RPC 프로토콜 오류와 구분된다. 실행 실패(`isError: true`)는 **정상 응답에 실려 모델에게 보여지는 오류**로, 모델이 읽고 재시도/우회를 판단한다. (ADK/skill 사례에서 봤듯 "오류가 예외가 아니라 정상 응답"인 것이 재시도 루프의 토양이 되기도 한다) |
| `resultType` | `"complete"` / `"input_required"` (MRTR 중간 결과, 2026-07-28~) |

### 1-3. ToolAnnotations — 신뢰할 수 없는 "힌트"

| 힌트 | 의미 |
|---|---|
| `readOnlyHint` | 환경을 변경하지 않음 |
| `destructiveHint` | 파괴적 변경 가능 (삭제 등) |
| `idempotentHint` | 같은 인자 반복 호출이 추가 효과 없음 |
| `openWorldHint` | 외부 세계와 상호작용 (웹 등) |

어디까지나 **서버의 자기 신고**다. 스펙은 신뢰할 수 없는 서버의 annotations를 보안 결정에
쓰지 말 것을 요구한다 — 호스트 UI의 승인 강도 조절(예: destructive면 확인 대화상자) 정도에
활용한다.

### 1-4. 운영 특성

- 목록 변경: 서버가 `tools/list_changed` 알림 (현행에선 `subscriptions/listen` 스트림에 opt-in).
- 실행 중 추가 입력 필요: 현행에선 `resultType:"input_required"`(MRTR)로 사용자 입력을 요구.
- 안전 원칙: 도구 호출은 **호스트가 사용자 동의를 얻어 실행**하는 것이 스펙의 전제
  (사람이 개입하는 human-in-the-loop 권장).

---

## 2. Resources — 앱이 주입하는 컨텍스트 데이터

### 2-1. 선언 구조 (`Resource` — SDK 실측 필드)

| 필드 | 설명 |
|---|---|
| `uri` | 고유 식별자 — `file:///...`, `postgres://...`, 커스텀 스킴 자유 |
| `name` / `title` / `description` | 식별자·표시명·설명 |
| `mimeType` | 콘텐츠 타입 |
| `size` | 바이트 크기 (호스트가 컨텍스트 예산 판단에 사용) |
| `annotations` | `audience`(user/assistant 대상 구분) · `priority`(0~1 중요도) · `lastModified` |

### 2-2. 정적 목록 + 동적 템플릿

- **`resources/list`** — 서버가 열거한 **구체 리소스** 목록.
- **`resources/templates/list`** — **URI 템플릿**(RFC 6570, 예: `file:///{path}`,
  `db://{table}/{id}`)으로 무한한 리소스 공간을 표현. 클라이언트가 인자를 채워 임의 리소스를
  지칭할 수 있다 (SDK `ResourceTemplate.uri_template` 실측).

### 2-3. 읽기와 구독

- **`resources/read`** `(uri)` → contents 배열:
  - `TextResourceContents` — `text` 필드 (텍스트)
  - `BlobResourceContents` — `blob` 필드 (base64 바이너리)
- 변경 통지: 구모델의 `resources/subscribe`(개별 리소스)·`list_changed`는 현행에서
  `subscriptions/listen` 스트림의 opt-in 타입(`resourceSubscriptions`, `resourcesListChanged`)으로 통합.

### 2-4. "app-controlled"의 실제 의미

리소스는 **모델이 마음대로 읽는 것이 아니라**, 호스트 앱이 정책적으로 컨텍스트에 넣는
데이터다. 전형 패턴:

- UI에서 사용자가 리소스를 골라 채팅에 첨부 (파일 피커)
- 앱이 관련 리소스를 자동 선별해 주입 (RAG류)
- 도구 결과가 `ResourceLink`(§4)로 리소스를 가리키면, 앱이 필요할 때 read

모델 주도 읽기가 필요하면 서버가 "read_resource류 **도구**"를 별도로 제공하는 것이 관례다 —
그 순간 제어권이 모델로 넘어간다는 것을 의식하고 설계해야 한다.

---

## 3. Prompts — 사용자가 소환하는 템플릿

### 3-1. 선언 구조 (`Prompt` — SDK 실측 필드)

| 필드 | 설명 |
|---|---|
| `name` / `title` / `description` | 식별자·표시명·설명 |
| `arguments` | `PromptArgument[]` — `name`·`description`·`required`. **자동완성(completion) 지원 대상** |

### 3-2. 사용 흐름

1. **`prompts/list`** — 호스트 UI가 템플릿 메뉴(슬래시 커맨드, 버튼 등)로 노출
2. 사용자가 선택 + 인자 입력 (예: `/review-code language=python`)
3. **`prompts/get`** `(name, arguments)` → `GetPromptResult`:
   - `messages: PromptMessage[]` — `role`(user/assistant) + `content`(콘텐츠 블록)
   - **완성된 대화 메시지 시퀀스**를 돌려준다 — 단일 문자열이 아니라 멀티턴/멀티모달 구성 가능

### 3-3. 활용 패턴

- 정형 워크플로 (코드리뷰, 회의록 요약, 장애 분석 절차)
- few-shot 예시가 포함된 템플릿 (user/assistant 교대 메시지로 구성)
- embedded resource를 포함해 "데이터가 미리 끼워진" 템플릿
- 핵심 성격: **명시성** — 모델이 임의로 트리거하지 않고, 사용자의 의도적 선택으로만 발동

---

## 4. 공통 — 콘텐츠 블록 (`ContentBlock`)

`tools/call` 결과와 `PromptMessage.content`가 공유하는 타입 (SDK 실측 union):

| 타입 | 내용 |
|---|---|
| `TextContent` | 텍스트 |
| `ImageContent` / `AudioContent` | base64 데이터 + mimeType |
| `ResourceLink` | 리소스 **참조**(URI+메타만) — 대용량을 인라인하지 않고 가리킴. 2025-06-18~ |
| `EmbeddedResource` | 리소스 **내용물을 인라인** 포함 |

`ResourceLink` vs `EmbeddedResource` 선택 = "지금 컨텍스트에 넣을 것인가(embedded),
필요할 때 읽게 할 것인가(link)"의 결정 — 토큰 예산 관리 수단이다.

---

## 5. 한눈 비교표

| | Tools | Resources | Prompts |
|---|---|---|---|
| 제어 주체 | 모델 | 앱(호스트) | 사용자 |
| 식별 | `name` | `uri` (+템플릿) | `name` |
| 발견 | `tools/list` | `resources/list`, `resources/templates/list` | `prompts/list` |
| 사용 | `tools/call` | `resources/read` | `prompts/get` |
| 반환물 | 콘텐츠 블록 + `structuredContent` + `isError` | text/blob contents | 완성된 메시지 시퀀스 |
| 변경 통지 | list_changed | list_changed + 개별 구독 | list_changed |
| 스키마 | inputSchema/outputSchema (JSON Schema) | mimeType | arguments 선언 |
| 전형 사례 | 검색·생성·수정 API | 파일·문서·스키마·로그 | 슬래시 커맨드·정형 워크플로 |

(셋 모두: 커서 페이지네이션 · `ttlMs`/`cacheScope` 캐시 필드(2026-07-28~) · list_changed는
현행에서 `subscriptions/listen` opt-in)

---

## 6. 공식 문서에 실린 사용 사례 (2026-07-28 스펙 페이지 확인)

### 6-1. Resources — 스펙이 직접 든 예시

- **데이터 종류** (서두 정의): "files, **database schemas**, or application-specific information"
- **UI 노출 3방식** (User Interaction Model 절, 원문):
  1. "Expose resources through UI elements for explicit selection, **in a tree or list view**"
  2. "Allow the user to **search through and filter** available resources"
  3. "Implement **automatic context inclusion**, based on heuristics **or the AI model's
     selection**" — app-controlled의 '앱'이 모델 휴리스틱을 쓸 수도 있음을 공식 인정
- **프로토콜 예제 소재**: `file:///project/src/main.rs`(소스 파일), 디렉터리 read 시
  **여러 파일 내용 일괄 반환**, 템플릿 `file:///{path}` "Project Files",
  `README.md`+annotations → "Prioritize which resources to include in context" 등 활용처 명시
- **표준 URI 스킴**: `https://`(클라이언트가 직접 fetch 가능할 때만), `file://`(파일시스템처럼
  행동하되 "실제 물리 파일시스템일 필요는 없다"), `git://`(버전 관리), 커스텀 스킴 자유
- `resources/read`도 **MRTR**(`InputRequiredResult`)로 응답 가능 — 읽기 전 추가 입력이
  필요한 시나리오까지 공식 플로우

### 6-2. Prompts — 스펙이 직접 든 예시

- **UI 노출** (원문): "Typically, prompts would be triggered through **user-initiated
  commands**... For example, as **slash commands**" (스크린샷 포함)
- user-controlled의 정확한 의미 (원문): "This refers to **who decides when the prompt is
  used, not who authors its content**" — 사용 시점 결정권이지 내용 작성 주체가 아님
- **대표 예제 `code_review`**: 인자 `code`(required) → `prompts/get` →
  "Please review this Python code:..." 메시지 반환 — 페이지 전체가 이 예제로 구성
- **멀티모달·리소스 결합**: Image/Audio 콘텐츠("multi-modal interactions"),
  `resource_link`("without embedding the resource contents directly"),
  embedded resource("documentation, **code samples**, or other reference materials")
- `prompts/get`도 **MRTR** 응답 가능

### 6-3. 실무 확장 사례 (스펙 명시는 아님 — 위 원칙들의 조합)

- **few-shot 내장 템플릿** — user/assistant 교대 메시지 구조가 지원 (스펙에 명시 사례 없음)
- **조직 표준 워크플로의 중앙 배포** — user-controlled + 서버 중앙 관리 성질의 응용
- **IDE 현재 파일 자동 주입**, **장애 분석 조합 시나리오**(Prompt로 절차 소환 + Resource로
  로그/런북 주입 + Tool로 메트릭 조회) — 공식 3원칙의 실무 조합

## 7. ADK 관점 (google-adk 1.26.0, 소스 확인 사실)

| 프리미티브 | ADK 지원 |
|---|---|
| Tools | **1급 지원** — `McpToolset.get_tools()`가 `tools/list` 결과를 `McpTool(BaseTool)`로 변환해 에이전트 도구 체계에 자동 편입 (`inputSchema`→`FunctionDeclaration`) |
| Resources | **명시 API** — `read_resource()`/`list_resources()` 제공. 자동 컨텍스트 주입은 없음(호스트 앱 몫) — 콜백/instruction에서 직접 활용해야 함 |
| Prompts | **미노출** — toolset API에 prompts 관련 메서드 없음 |

설계 함의: ADK 에이전트에서 MCP를 쓸 때 실질 단위는 **도구**다. 리소스를 컨텍스트로 쓰려면
`before_model_callback` 등에서 `read_resource`를 호출해 instruction/contents에 넣는 앱 레벨
코드가 필요하고, 프롬프트 템플릿은 MCP 경유로는 쓸 수 없다.
