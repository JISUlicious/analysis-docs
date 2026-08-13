# MCP 도구 응답 실전 패턴 — 인기 서버 소스 조사 + wire 실측

> **조사 대상·방법**:
> ① **공식 레퍼런스 서버 모노레포** `modelcontextprotocol/servers`(생태계 최다 스타 저장소,
> 7개 서버: filesystem/everything/memory/sequentialthinking=TS SDK, fetch/git/time=Python SDK)
> 소스 직접 열람. ② Python SDK(FastMCP 1.29)의 반환값 변환 규칙 소스 확인.
> ③ FastMCP 서버를 인메모리 세션으로 띄워 **반환 타입별 실제 wire 응답 캡처**.
> (microsoft/playwright-mcp는 클론 결과 소스가 본체 저장소로 이관되어 배포 번들만 남아 제외)
> 연관: [프리미티브 상세](MCP_프리미티브_상세.md) §1(CallToolResult 구조)

---

## 1. 공개 서버들의 출력 정의 방식 — 두 세대가 공존

### 1-1. 1세대 패턴: "전부 텍스트" (Python 공식 서버들이 대표)

| 서버 | 반환 방식 (소스 확인) |
|---|---|
| **fetch** | `[TextContent(text=f"Contents of {url}:\n{content}")]` — 웹페이지를 텍스트로 |
| **git** | `[TextContent(text=diff_텍스트)]` — diff/로그를 텍스트로 |
| **time** | `[TextContent(text=json.dumps(result.model_dump(), indent=2))]` — **구조화 데이터를 JSON 문자열로 직렬화해 text 블록에** |
| **memory**(구 스타일 잔재) | `content: [{type:"text", text: JSON.stringify(result, null, 2)}]` |

→ **"dict를 만들고 → JSON 문자열로 → text 블록"**이 오랫동안 사실상 표준.
outputSchema 없음, 모델이 텍스트로 파싱.

### 1-2. 2세대 패턴: 이중 표현 (content + structuredContent) + outputSchema

최신 공식 서버들은 전환 완료:

- **memory** — 전 도구에 zod `outputSchema` 선언 (`{entities: z.array(EntitySchema)}` 등)
- **filesystem** — `read_file` 등이 `{ content: [text], structuredContent: {...} }` 반환
- **everything**의 `get-structured-content`가 교과서 예제 — 소스 주석까지 명시적:

```ts
const backwardCompatibleContentBlock = { type: "text", text: JSON.stringify(weather) };
return {
  content: [backwardCompatibleContentBlock],   // 하위호환용 직렬화 텍스트
  structuredContent: weather,                  // outputSchema를 따르는 구조화 본체
};
```

스펙의 "outputSchema 사용 시 content에도 직렬화 JSON을 SHOULD" 권고가 그대로 구현된 형태.

### 1-3. 케이스별 반환 구조 (everything 서버 = 공식 전시장)

| 케이스 | 소스 확인 구조 |
|---|---|
| **텍스트** | `{type:"text", text}` — 기본형 |
| **이미지** | `get-tiny-image`: 안내 text 블록 + `{type:"image", data:<base64>, mimeType}` **혼합 배열** |
| **파일(바이너리)** | `gzip-file-as-resource`: `{type:"resource", resource:{uri, mimeType, blob}}` (embedded) **또는** resource_link — **도구 인자(outputType)로 선택지 제공** |
| **URL/링크** | `get-resource-links`: 인트로 text + `{type:"resource_link", uri, name, description, mimeType}` 여러 개 |
| **embedded 참조** | `get-resource-reference`: `{type:"resource", resource}` + "URI로 접근 가능" 안내 text |
| **미디어 파일 읽기** | filesystem `read_media_file`: 이미지는 `{type:"image"}`, 그 외 바이너리는 `{type:"resource"}` 분기 |

## 2. Python SDK(FastMCP)의 자동 변환 규칙 — 서버 코드에서 output "정의"의 실체

FastMCP 기반 서버는 outputSchema를 손으로 안 씁니다 — **함수 반환 타입 어노테이션이 곧 정의**입니다
(`func_metadata.py` 소스 + 아래 실측으로 확인):

| 반환 어노테이션 | 자동 outputSchema | 실제 응답 |
|---|---|---|
| `str` | `{result: string}` — **"result" 키로 래핑** | text 블록 + `structuredContent: {"result": ...}` |
| `dict` (제네릭) | **생성 안 됨** | text 블록에 JSON 직렬화만 (structuredContent 없음!) |
| pydantic `BaseModel` | 모델 스키마 그대로 | **이중 표현** — text(JSON) + structuredContent |
| `list[str]` | `{result: array}` 래핑 | **아이템마다 별도 text 블록** + structuredContent |
| `Image` 헬퍼 | 없음 | `{type:"image", data, mimeType}` 단일 블록 |
| `ResourceLink` 반환 | ResourceLink 스키마 | resource_link 블록 + structuredContent(전체 필드 덤프) |
| `CallToolResult` 직접 반환 | (선언한 대로) | 완전 수동 제어 |

## 3. 실측 wire 응답 (FastMCP 1.29, 인메모리 캡처 — 요약)

```jsonc
// str 반환                                  // pydantic 모델 반환
{"content":[{"type":"text",                  {"content":[{"type":"text",
  "text":"결과 텍스트: hello"}],               "text":"{\"temperature\": 21.5, ...}"}],
 "structuredContent":{"result":"..."},        "structuredContent":{"temperature":21.5,
 "isError":false}                              "conditions":"맑음"}, "isError":false}

// 제네릭 dict 반환 — 주의!                   // list[str] 반환 — 블록 분할
{"content":[{"type":"text",                  {"content":[{"type":"text","text":"ab"},
  "text":"{\n \"answer\": \"hi\", ...}"}],               {"type":"text","text":"AB"}],
 "isError":false}   // structuredContent 없음  "structuredContent":{"result":["ab","AB"]}}

// Image 반환 (파일/바이너리)                 // ResourceLink 반환 (URL)
{"content":[{"type":"image",                 {"content":[{"type":"resource_link",
  "data":"iVBORy...","mimeType":"image/png"}],  "uri":"file:///docs/guide.md",
 "isError":false}                               "name":"guide.md","mimeType":"text/markdown"}], ...}
```

## 4. 종합 — 실전에서 관찰되는 규칙

1. **텍스트가 여전히 왕**: 가장 인기 있는 서버들(fetch/git/memory 등)의 주력 응답은
   text 블록이며, 구조화 데이터도 "JSON을 문자열로 넣은 text"가 광범위. 모델(LLM)의 1차
   소비 경로가 텍스트이기 때문.
2. **구조화 출력은 이중 표현이 표준**: structuredContent를 쓰는 곳은 예외 없이 같은 내용을
   text로도 병행 (하위호환 SHOULD 준수). 공식 서버들이 2세대 패턴으로 이행 중.
3. **파일은 두 가지 길**: 작으면 embedded(base64 blob 인라인), 크면 resource_link(참조)
   — everything 서버는 아예 이 선택을 도구 인자로 노출. 토큰/전송 예산 결정이 서버
   설계에 들어와 있음.
4. **URL은 resource_link 블록**이 정석 (평문 text에 URL 적는 것보다 메타데이터
   (name/mimeType/description)가 실려 클라이언트가 후속 read 가능).
5. **FastMCP 함정 2개** (실측): ① 제네릭 `dict` 어노테이션은 outputSchema도
   structuredContent도 만들지 않는다 — 구조화가 목적이면 pydantic 모델/TypedDict로.
   ② 비객체 반환(str/list)은 `"result"` 키로 래핑된 스키마가 된다 — 클라이언트가 이 래핑을
   알아야 함.
6. **오류는 `isError:true` + text** (fetch 서버: `TextContent(text=str(e))`) — 예외가 아니라
   응답. (ADK 재시도 루프 분석과 이어지는 지점)

## 5. ADK 소비 관점 재확인

ADK(1.26.0)의 `McpTool`은 이 응답 **봉투를 통째로 `model_dump`** 해서 함수응답으로 모델에
전달한다. 위 실측 JSON이 곧 ADK 에이전트의 LLM이 보는 내용이다. 함의:
- 2세대(이중 표현) 서버라면 모델이 같은 데이터를 text와 structuredContent로 **두 번** 보게
  됨 — 토큰 낭비 소지. 필요시 `after_tool_callback`에서 한쪽을 제거하는 최적화 여지.
- resource_link를 받은 경우 후속 read는 ADK가 자동으로 하지 않음 — 앱 코드
  (`toolset.read_resource`) 몫.
