# MCP 도구 응답 실전 패턴 — 인기 서버 소스 조사 + wire 실측

> **조사 대상·방법**:
> ① **공식 레퍼런스 서버 모노레포** `modelcontextprotocol/servers`(생태계 최다 스타 저장소,
> 7개 서버: filesystem/everything/memory/sequentialthinking=TS SDK, fetch/git/time=Python SDK)
> 소스 직접 열람. ② **인기 서드파티 4종** 소스 열람 — `github/github-mcp-server`(Go, GitHub 공식),
> `upstash/context7`(TS), `sooperset/mcp-atlassian`(Python FastMCP), `firecrawl/firecrawl-mcp-server`(TS).
> ③ Python SDK(FastMCP 1.29)의 반환값 변환 규칙 소스 확인.
> ④ FastMCP 서버를 인메모리 세션으로 띄워 **반환 타입별 실제 wire 응답 캡처**.
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

## 1-4. 인기 서드파티 서버 4종의 실제 선택 (소스 확인)

| 서버 | SDK | 응답 패턴 |
|---|---|---|
| **github-mcp-server** (GitHub 공식, Go) | 공식 Go SDK v1.7 | **1세대 주력** — `json.Marshal(구조체)` → text 블록(159곳). 특징 둘: ① **오류-as-결과가 압도적**(`NewToolResultError` 822곳 + `ErrorFromErr` 183곳 — isError 패턴의 대규모 실사용) ② **`MinimalCommit` 등 트림 전용 구조체**로 응답을 축소해 토큰 절약. structuredContent는 단 1곳, resource/resource_link 합계 4곳(희소) |
| **context7** (Upstash, TS) | 공식 TS SDK | **text 전용** — 문서 검색 결과를 통짜 텍스트로. 특이점: 인증 유도에 **elicitation** 사용(`maybeElicitAuthSignIn`) |
| **mcp-atlassian** (Python) | FastMCP (`mcp>=1.27,<2.0`) | `return json.dumps(response_data, indent=2)` — **str 반환으로 JSON-문자열** (FastMCP 규칙상 `{result: string}` 래핑으로 전달됨). FastMCP 위에서도 1세대 스타일을 쓰는 실사용례 |
| **firecrawl-mcp-server** (TS) | 공식 TS SDK | **2세대 채택** — `content:[text]` + `structuredContent: payload` 이중 표현 |

→ 서드파티까지 보면: **1세대(JSON→text)가 여전히 다수파**이고(GitHub 공식조차), 2세대 이행은
서버별로 진행 중(firecrawl, 공식 filesystem/memory). 그리고 GitHub 서버의 두 관행 —
**대량의 오류-as-결과**와 **트림 구조체** — 는 규모 있는 프로덕션 서버의 실전 요령이다.

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

1. **텍스트가 여전히 왕**: 공식(fetch/git/memory)·서드파티(github-mcp-server, context7,
   mcp-atlassian) 공히 주력 응답은 text 블록이며, 구조화 데이터도 "JSON을 문자열로 넣은
   text"가 광범위. 모델(LLM)의 1차 소비 경로가 텍스트이기 때문.
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

## 6. 파일 전달 방식 심층 — 표준 선택지 vs 실전 사용 (소스 실측)

### 6-1. 표준(스펙/SDK)이 제공하는 선택지

| # | 메커니즘 | 형태 | 성격 |
|---|---|---|---|
| 1 | `EmbeddedResource` | `{type:"resource", resource:{uri, mimeType, text\|blob(base64)}}` | **인라인** — 내용이 응답에 실림 |
| 2 | `ResourceLink` | `{type:"resource_link", uri, name, size...}` | **참조** — 클라이언트가 `resources/read`로 후속 조회 (`https://` 스킴이면 웹에서 직접 fetch — 2026-07-28 명시) |
| 3 | `ImageContent`/`AudioContent` | `{type:"image", data(base64), mimeType}` | 미디어 전용 인라인 |
| 4 | Resources 프리미티브 | `resources/read` → Text/BlobResourceContents | 도구 응답 밖의 정식 파일 창구 |
| 5 | (비표준) URL/base64를 text·JSON에 | 평문 필드 | 스펙 밖이지만 실전에서 흔함 |

### 6-2. 서버별 실제 선택 (소스 확인)

| 서버 | 파일 전달 방식 |
|---|---|
| **github-mcp-server** | **표준 풀스택 — 모범 사례.** `repo://{owner}/{repo}/contents{/path*}` URI 템플릿으로 Resources 프리미티브 노출 + `get_file_contents`는 크기 분기: **0B→빈 embedded / <1MB 텍스트→embedded(text) / <1MB 바이너리→embedded(base64 blob) / ≥1MB→`ResourceLink`**(자기 리소스 URI+size) — 그리고 안내 텍스트에 `download_url`도 병기(클라가 link 후속조회를 못 할 경우 대비) |
| **filesystem** (공식) | `read_media_file`: 이미지→`ImageContent`, 그 외 바이너리→`EmbeddedResource(blob)` 분기. 텍스트 파일은 text |
| **everything** (공식) | `gzip-file-as-resource`: embedded(blob) ↔ resourceLink **선택을 도구 인자로 노출** |
| **mcp-atlassian** | `download_attachments`: **≤50MB만 인라인**(초과분은 실패 처리+"Jira에서 직접 받으라" 안내). 이미지→`EmbeddedResource(blob)`, **비이미지→`TextContent`에 base64 페이로드** — 의도적 표준 이탈: "많은 MCP 클라이언트가 이미지 MIME이 아닌 embedded blob을 drop한다"(이슈 #1419)는 **클라이언트 호환성 우회** |
| **firecrawl** | 스크린샷 등을 MCP 미디어/리소스 블록 없이 **payload JSON 내 URL 필드**로 (Firecrawl API가 호스팅한 URL 전달) |
| context7 / fetch / git | 파일 전달 개념 없음 — 전부 text |

### 6-3. 관찰되는 실전 규칙

1. **크기 임계값 분기가 표준 관행**: 인라인(embedded/blob) ↔ 참조(link)의 경계를 서버가
   정한다 — github 1MB, atlassian 50MB(초과는 아예 거부). base64는 원본 대비 +33%라는
   점까지 계산에 넣어야 한다.
2. **이미지는 특별 대우**: 이미지면 `ImageContent`(또는 image MIME embedded)로 —
   클라이언트 UI가 렌더링해주는 유일한 경로이기 때문. 같은 바이너리라도 이미지/비이미지의
   전달 타입을 나누는 서버가 둘(filesystem, atlassian).
3. **클라이언트 지원 격차가 설계를 왜곡한다** (실전 최대 함정): 비이미지 embedded blob을
   drop하는 클라이언트들 때문에 atlassian은 base64를 TextContent에 싣는 우회를 택했다.
   표준만 믿지 말고 대상 클라이언트의 콘텐츠 블록 처리를 확인할 것.
4. **ResourceLink는 벨트+멜빵으로**: 후속 `resources/read`를 클라이언트가 자동으로 해주지
   않는 경우가 많아(ADK 포함), github은 link 블록과 함께 안내 텍스트에 download URL을
   병기한다.
5. **비표준 URL-in-JSON도 여전히 현역**: 외부 저장소(호스팅 URL)가 있으면 그 URL을
   payload 필드로 주는 게 가장 단순하고(firecrawl), 2026-07-28부터는 `https://` 스킴
   ResourceLink로 표준화할 수 있는 길이 생겼다(클라 직접 fetch 허용 명시).

### 6-4. 설계 가이드 (요약)

- 텍스트 파일 → embedded(text) 또는 그냥 text 블록
- 이미지 → `ImageContent` (렌더링 경로)
- 소형 바이너리 → embedded(blob), **크기 상한 명시**
- 대형 파일 → `ResourceLink`(+size) + 안내 텍스트에 대체 URL 병기, 서버에 Resources
  프리미티브(read)도 함께 노출 — github 패턴 그대로가 정답에 가깝다
- 대상 클라이언트가 비이미지 blob을 소화하는지 사전 확인 — 못 하면 atlassian식 우회 고려

### 6-5. "링크-온리(무 base64)" 정책 검토 — 컨텍스트가 타이트한 환경

**질문**: 파일을 무조건 URL/resource_link로만 내리고 encoded string(base64)은 아예 쓰지
않는 정책이 괜찮은가?

**판정: 해당 환경(모델 컨텍스트 제한 + ADK류 호스트 + 비전 없는 모델)에서는 강력히 합당하다.**

**근거 — base64의 토큰 비용 (산술)**:

| 원본 크기 | base64 크기(+33%) | 대략 토큰 (≈3~4자/토큰) |
|---|---|---|
| 10 KB | ~13.7 KB | ~4천 |
| 100 KB | ~137 KB | **~4만** |
| 1 MB | ~1.37 MB | **~40만** |

- ADK는 응답 봉투를 통째로 모델에 전달하므로(§5) blob은 **그대로 토큰이 된다** —
  100KB 파일 하나가 대화 컨텍스트를 잠식.
- base64는 모델이 읽어도 의미가 없는 문자열이다. 비전 모델의 이미지 렌더링(클라이언트가
  ImageContent를 실제 이미지 입력으로 변환)이 유일한 정당화인데, **비전 없는 스택이면
  그 예외조차 사라진다.**
- 부수 이득: atlassian이 겪은 "클라이언트가 비이미지 blob을 drop"(#1419) 문제 자체가
  소멸 — 우회 코드 불필요.

**단서 4가지 (정책이 성립하려면 함께 정해야 함)**:

1. **"전달"과 "분석"의 분리** — 링크는 모델에게 내용을 주지 않는다(ADK는 자동 read 안 함).
   모델이 파일 **내용으로 추론해야 하는** 요구는 파일 전달이 아니라 **추출 텍스트를
   반환하는 별도 도구**(파싱 + 길이 상한 + 필요시 페이지네이션)로 충족한다.
   → 이 이원화를 채택하면 base64가 필요한 경우는 실제로 남지 않는다.
2. **URL 수명·인증**: presigned URL 만료 시간, fetch 주체(호스트 앱? 최종 사용자 브라우저?),
   내부망 도달성을 명시. 만료가 대화 수명보다 짧으면 모델이 죽은 링크를 인용하게 된다.
3. **클라이언트 호환 폴백**: resource_link 미지원 클라이언트 대비, 안내 텍스트에
   평문 URL 병기(github 패턴). 링크 메타데이터에 `size`·`mimeType`을 실어 모델이
   "받을지 말지"를 판단할 근거 제공.
4. **구현 최소 경로**: resource_link의 URI는 자체 `resources/read` 구현 대신
   **오브젝트 스토리지 presigned `https://` URL**이 가장 저렴하다 — 2026-07-28 스펙이
   `https://` 스킴은 클라이언트 직접 fetch를 공식 허용.

**정책 문안 예시** (프로젝트 규칙으로 성문화용):

> 도구 응답에 파일 바이트/base64 인라인 금지. 파일은 presigned https URL을 가진
> `resource_link`(+`size`,`mimeType`) + 안내 텍스트의 평문 URL 병기로 전달한다.
> 모델이 내용을 읽어야 하는 경우는 파일이 아니라 추출 텍스트 도구(상한 N자)로 제공한다.
