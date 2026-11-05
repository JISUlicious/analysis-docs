# UTCP vs MCP — 참조 구현 해체 기반 비교 분석

> **조사 대상·방법**:
> ① **UTCP 1차 문서** — [utcp.io](https://www.utcp.io/) 소개·[명세 API 색인](https://www.utcp.io/api)·[UTCP vs MCP](https://www.utcp.io/utcp-vs-mcp)·[Security Considerations](https://www.utcp.io/security)·[registry](https://www.utcp.io/registry).
> ② **참조 구현 직접 해체** — PyPI 휠(`utcp` 1.2.0, `utcp-http` 1.1.15, `utcp-mcp` 1.2.0)을 내려받아
> 압축 해제 후 Pydantic 모델·인터페이스·보안 모듈 소스 열람(설치·실행 없이 읽기만).
> ③ **생태계 수치 실측** — GitHub API로 두 조직의 저장소 스타·마지막 푸시·릴리스 태그, PyPI JSON API로
> 전체 릴리스 이력과 날짜, npm 레지스트리로 JS 패키지 버전.
> ④ MCP 측 수치는 같은 방식으로 수집하고, 프로토콜 사실은 이 저장소의 기존 실측 문서를 재사용.
> **기준일: 2026-11-06** (UTCP 명세 사이트 1.1 / `utcp` 1.2.0 / MCP 2026-07-28 / `mcp` 2.3.0)
> 연관: [기본 개념과 구조](MCP_기본개념_구조.md) · [프리미티브 상세](MCP_프리미티브_상세.md) · [도구 응답 실전 패턴](MCP_도구응답_실전패턴.md) · [다분야 도구관리 가이드](MCP_다분야_도구관리_가이드.md)

---

## 1. UTCP는 무엇인가 — 데이터 모델이 설계를 말한다

Universal Tool Calling Protocol의 발상은 한 줄이다: **도구를 감싸는 서버를 만들지 말고, 어떻게
호출하는지를 적은 문서(manual)를 주면 에이전트가 원래 API를 직접 호출한다.**

`utcp` 1.2.0 휠에서 읽은 모델 전부가 이 발상 그대로다.

```
UtcpManual   = { utcp_version, manual_version="1.0.0", tools[] }
Tool         = { name, description, inputs(JsonSchema), outputs(JsonSchema),
                 tags[], average_response_size?, tool_call_template }
CallTemplate = { name, call_template_type, auth?, allowed_communication_protocols? }
  └ HttpCallTemplate = { url, http_method(GET|POST|PUT|DELETE|PATCH), content_type,
                         headers?, body_field="body", header_fields[]?, auth?, auth_tools? }
Auth         = api_key | basic | oauth2   (3종)
```

제공자는 기존 API에 `/utcp` 같은 정적 엔드포인트 하나를 붙여 이 JSON을 내보낸다. 클라이언트는
그것을 읽어 도구 목록을 얻고, 호출할 때는 **MCP 서버 같은 중간 프로세스 없이 원 API를 직접** 때린다.

프로토콜 플러그인이 구현해야 하는 인터페이스(`utcp/interfaces/communication_protocol.py`)는
**다섯 개뿐**이다. 이것이 UTCP의 표면 전체다.

```python
register_manual(caller, manual_call_template) -> RegisterManualResult
deregister_manual(caller, manual_call_template) -> None
call_tool(caller, tool_name, tool_args, tool_call_template) -> Any
call_tool_streaming(caller, tool_name, tool_args, tool_call_template) -> AsyncGenerator
close() -> None
```

**실무상 가장 중요한 자산은 명세가 아니라 변환기다.** `utcp-http`에는 810줄짜리
`OpenApiConverter`가 들어 있다(`$ref` 해석, security scheme → `Auth` 매핑, 예시 병합,
입력 스키마 추출). **이미 OpenAPI 스펙이 있는 API라면 매뉴얼을 손으로 쓰지 않는다** — UTCP를
검토할 유일한 강한 이유가 여기 있다.

변수 치환은 `${VAR}` 와 `$VAR` 두 문법을 지원하고, 매뉴얼별 네임스페이스 접두사를 붙여 충돌을
막는다. `$ref`는 OpenAPI JSON 참조와 구별하기 위해 치환에서 제외된다(`$refresh_token`처럼
이어지는 식별자는 치환 대상). 값 공급은 `UtcpClientConfig.variables` 또는 `.env` 로더다.

---

## 2. 호출 경로 — 중간자 제거가 무엇을 바꾸는가

```
UTCP   에이전트 ──GET /utcp──▶ 제공자            (등록: 1회, 정적 문서)
       에이전트 ──POST /v1/x──▶ 제공자            (호출: 직접)

MCP    에이전트 ──JSON-RPC──▶ MCP 서버 ──HTTP──▶ 제공자
                             (프로세스/세션)
```

제거되는 것: 서버 프로세스, 그 프로세스의 수명주기·메모리·세션 상태, 한 홉의 지연.
같이 제거되는 것: **그 서버가 담당했던 모든 것** — 자격증명 보관, 최소권한 도구 설계, 응답 축약,
사람 승인 지점, 장시간 작업 관리. §3·§6·§7이 그 목록이다.

---

## 3. 기능 범위 비교 (실측)

| 기능 | UTCP 1.2 | MCP 2026-07-28 |
|---|:---:|:---:|
| 도구 호출 | ✅ | ✅ |
| 스트리밍 | ✅ (SSE, streamable HTTP) | ✅ |
| resources | ❌ — MCP 래핑 시 `register_resources_as_tools`로 **도구로 평탄화** | ✅ |
| prompts | ❌ | ✅ |
| sampling (서버→모델) | ❌ | ✅ |
| elicitation (사용자 입력) | ❌ | ✅ (form / url) |
| 진행률·취소 | ❌ | ✅ |
| 장시간 작업(tasks) | ❌ | ✅ 규격 (Python SDK 미구현) |
| 목록 변경 알림 | ❌ (매뉴얼 재등록) | ✅ |
| 세션 | 없음 (무상태 pull) | 선택 (2026-07-28 무상태 코어) |
| 전송 | HTTP·SSE·streamable·CLI·WebSocket·TCP·UDP·GraphQL·text·**MCP** | HTTP·stdio |
| 인증 | api_key·basic·oauth2 (3종, 클라이언트가 수행) | OAuth 2.1 리소스 서버 패턴 + 호스트 위임 |
| 도구 검색 | ✅ **내장** (`tag_search`) | 호스트 책임 |
| 응답 후처리 | ✅ **내장** (`limit_strings`, `filter_dict`) | 서버/호스트 책임 |

**근거.** `utcp` 1.2.0 코어의 `.py` 42개 전체를 문자열 검색한 결과
`notification` 0건 · `sampling` 0건 · `elicit` 0건 · `prompt` 0건 · `progress` 0건
(`listChanged`/`list_changed`도 0건). `resource` 13건은 전부 텍스트 파일·리소스 제한 문맥이고
매뉴얼에 들어가는 1급 개념은 `Tool` 뿐이다. `utcp-mcp`의 `McpCallTemplate`에
`register_resources_as_tools: bool = False` 필드가 있다는 사실이 "UTCP에 resources 개념이
없어서 도구로 눌러 담는다"는 것을 확정한다.

→ **UTCP는 MCP의 부분집합(도구 호출) + 넓은 전송 계층**이다. 기능 경쟁이 아니라 범위가 다르다.

---

## 4. 성숙도·생태계

| | UTCP | MCP |
|---|---|---|
| 명세 버전 | 사이트 기준 **1.1** (1.0·0.1 병존) / 라이브러리 1.2.0 | **2026-07-28** |
| 주 패키지 | `utcp` 1.2.0 (2026-09-16), 첫 릴리스 2025-07-10, 총 23개 | `mcp` 2.3.0 |
| 명세 저장소 릴리스 태그 | **없음** (쇼케이스 태그 1건뿐) | 날짜 기반 정식 버전 |
| GitHub ★ | python-utcp 663 · 명세 324 · utcp-mcp 213 · go 130 · ts 125 · rust 60 | servers 91,808 · python-sdk 24,770 · 명세 9,621 · registry 7,442 (저장소 45개) |
| 레지스트리 | `utcp.io/registry` = **"All Providers (0)"** | 공식 레지스트리 운영 |
| 호스트 지원 | 없음 (에이전트 프레임워크용 클라이언트 라이브러리) | Claude Code·VS Code·Cursor 등 |

**플러그인 패키지 현황** (PyPI, 최신 업로드일):
`utcp-http` 1.1.15 (2026-10-25) · `utcp-websocket` 1.2.1 (2026-10-25) · `utcp-mcp` 1.2.0 (2026-09-16) ·
`utcp-gql` 1.1.6 (2026-09-16) · `utcp-cli` 1.1.5 (2026-09-05) · `utcp-socket` 1.1.1 (2026-09-05) ·
`utcp-text` 1.1.0 (2025-11-30). **gRPC·WebRTC는 패키지가 없다** — 코어 docstring이 둘을 `(WIP)`로
표시하고 있는데, 홍보 문구의 "프로토콜 다수 지원"에는 이 단서가 빠져 있다.

npm은 `@utcp/http` 1.1.14 (2026-10-25)만 확인됐고 `utcp`·`@utcp/core` 이름으로는 없다.

개발 자체는 살아 있다(python-utcp 최근 커밋 2026-10-25, 루프백·WebSocket 보안 수정). 다만
**저작자 조직 외부의 채택 증거는 이 조사에서 확인되지 않았다.**

---

## 5. UTCP 측 주장 5건 검증

[utcp.io/utcp-vs-mcp](https://www.utcp.io/utcp-vs-mcp)의 비교표를 항목별로 판정한다.

| # | 주장 | 판정 | 근거 |
|---|---|---|---|
| ① | Latency: 직접 호출 vs 이중 홉 | **방향 타당, 수치 불신** | 같은 페이지의 산문(100ms vs 150ms)과 표(50ms vs 75ms)가 **서로 모순**. 방법론 없음. 조직 `benchmarks` 저장소는 ★0에 사실상 공백. MCP의 "이중 홉"은 로컬 stdio 서버에선 네트워크 홉이 아니다 |
| ② | Memory 0MB / CPU 0% | **회계 오류** | UTCP 클라이언트는 에이전트 프로세스 **안에서** 돈다. 비용이 사라진 게 아니라 옮겨간 것. "별도 서버 프로세스 없음"만 사실 |
| ③ | Security: Native vs Reimplemented | **절반만 사실** | §6 참조. 재구현이 서버에서 **클라이언트로 이동**했을 뿐이고, 자격증명 노출 범위는 넓어진다 |
| ④ | Maintenance: Minimal vs High | **절반만 사실** | 제공자 쪽은 맞다. 그러나 API가 바뀌면 매뉴얼도 바뀌고, MCP 서버가 흡수했던 **응답 축약·버전 고정**이 사라진다(§7) |
| ⑤ | Infrastructure: None | **타당** | 엔드포인트 1개. OpenAPI가 있으면 변환기로 끝 |

---

## 6. 보안 모델 — "네이티브"라는 말의 실제 내용

**① 클라이언트가 떠안는 몫이 작지 않다.** `utcp-http` 안에는 `_security.py`가 **별도 모듈로**
존재하고 다음을 직접 구현한다.

- 스킴 제한: `http`/`https`만, 평문 `http`·`ws`는 **리터럴 루프백 주소에만** 허용
- 루프백 판정(`_is_loopback_host`, `0.0.0.0`·`::`·IPv4-mapped 포함)
- 리다이렉트를 **매 홉 재검증**하고 홉 수 상한 적용, 비루프백 → 루프백 전이 차단
- OAuth2 토큰 URL 선검증, 원격 매뉴얼이 루프백 주소를 가리키는 도구 거부
  (`reject_remote_loopback_tool_urls`, `ensure_loopback_was_declared`)

즉 "중간자를 없애면 보안을 재구현할 필요가 없다"가 아니라, **SSRF 방어의 책임이 서버에서
클라이언트 라이브러리로 내려온다.** 그 구현 품질에 전적으로 의존하게 된다는 뜻이기도 하다
(실제로 2026-10-25 커밋들이 이 영역의 수정이다).

**② 자격증명이 에이전트 프로세스로 모인다.** `${VAR}` 치환과 `.env` 로더로 API 키·OAuth 시크릿이
에이전트 쪽 변수 공간에 들어온다. MCP 서버가 키를 들고 **좁은 도구만** 노출하는 구성과 비교하면,
프롬프트 인젝션이 성공했을 때의 폭발 반경이 더 크다. 중간자는 비용이면서 **격리 경계**였다.

**③ 공백: 도구 설명 오염(description poisoning).** UTCP [security 페이지](https://www.utcp.io/security)는
CLI 인젝션·경로 탈출·SSE 플러딩·변수 인젝션은 다루지만, **원격 매뉴얼의 `description`이
모델 컨텍스트로 들어가는 경로의 위협을 다루지 않는다.** 원격 문서를 신뢰해 도구 목록을 만드는
구조에서 이것은 1급 위협이고, 이 저장소의 [작성 표준](../agent-skills/Agent_Skills_작성표준.md) §4.1이
`description`의 꺾쇠를 금지하는 것과 같은 종류의 문제다. 매뉴얼을 가져오는 쪽에서 직접 막아야 한다.

---

## 7. 컨텍스트·운영 비용 — 응답이 모델로 직격한다

중간자가 없으면 **원 API 응답이 그대로 모델 컨텍스트에 들어온다.** 페이지네이션 없는 목록 API
하나가 컨텍스트를 날려버릴 수 있다. UTCP가 이 문제를 안다는 증거가 코어에 내장돼 있다.

| 내장 장치 | 역할 |
|---|---|
| `Tool.average_response_size` | 응답 크기 힌트(바이트) — 모델/클라이언트의 사전 판단용 |
| `limit_strings_post_processor` | 문자열 길이 상한 |
| `filter_dict_post_processor` | 응답 딕셔너리 키 선별 |
| `tag_search` + `Tool.tags` | 도구가 많을 때의 검색 전략 |

MCP 쪽에서는 이 일을 서버가 하고(응답 가공·`resource_link`), 도구 선별은 호스트가 한다
([도구 응답 실전 패턴](MCP_도구응답_실전패턴.md), [다분야 도구관리 가이드](MCP_다분야_도구관리_가이드.md)).
**UTCP를 쓰면 이 책임이 전부 에이전트 쪽 설정으로 온다** — 후처리기를 구성하지 않으면 그대로 맞는다.

운영 쪽 거래는 반대 방향이다. UTCP 매뉴얼은 정적 문서라서 **죽을 프로세스가 없다**.
MCP stdio 서버는 클라이언트마다 프로세스를 띄우고 그 메모리·수명주기를 관리해야 한다
([Python 웹서버 메모리관리 가이드](../memory/Python_웹서버_메모리관리_가이드.md)).

---

## 8. 상호운용 — 한 방향만 열려 있다

- **UTCP → 기존 MCP 서버 호출: 가능.** `utcp-mcp` 플러그인의 `McpCallTemplate`(`config`,
  `auth: OAuth2Auth?`, `register_resources_as_tools`)로 호출한다. 내부적으로 공식 `mcp` SDK의
  `ClientSession`·`stdio_client`와 `mcp_use`에 의존한다 — 즉 **MCP를 대체하는 게 아니라 품는다.**
- **MCP 호스트 → UTCP 매뉴얼 소비: 경로 없음.** Claude Code·VS Code 같은 호스트는 UTCP 매뉴얼을
  읽지 않는다. 공식 브리지는 `go-utcp-mcp-bridge`(★4) 수준이다.

이 비대칭이 **가장 큰 실무 차이**다. UTCP는 사실상 **매뉴얼 포맷 + 클라이언트 라이브러리**이고,
MCP는 **호스트–서버 계약**이다. 도구를 남에게 배포해야 하는 순간 UTCP에는 경로가 없다.

또 UTCP에는 **사용자 승인 개념 자체가 없다.** 부작용 있는 호출을 사람이 확인하는 게이트는 전부
에이전트 프레임워크 몫이다. MCP는 호스트가 승인·elicitation·sampling을 규격으로 제공한다.

---

## 9. 선택 기준

| 상황 | 선택 | 이유 |
|---|---|---|
| 내가 소유하고 OpenAPI가 있는 내부 API를 자체 에이전트에 붙인다 | **UTCP** | 변환기로 즉시. 서버 작성·운영 0 |
| 호출 빈도가 매우 높고 지연·메모리가 실측으로 문제다 | **UTCP** | 홉·프로세스 제거가 실제 이득 |
| CLI·TCP·UDP·GraphQL 표면을 직접 호출해야 한다 | **UTCP** | MCP는 감싸야 한다 |
| 도구를 서드파티 호스트에 배포한다 | **MCP** | UTCP에 배포 경로가 없다 |
| 사람 승인·추가 입력·장시간 작업이 필요하다 | **MCP** | elicitation·tasks·진행률이 UTCP에 없다 |
| 자격증명 격리와 최소권한이 요구사항이다 | **MCP** | 서버가 키를 들고 좁은 도구만 노출 |
| 응답이 크고 가공이 필요하다 | **MCP** (또는 UTCP + 후처리기 필수) | 중간자가 축약 지점 |
| 이미 MCP 서버가 있고 전송만 다양화하고 싶다 | **혼합** | `utcp-mcp`로 기존 서버 유지 |

**결론.** 양자택일 대상이 아니다. MCP는 "호스트에 도구를 배포하는 규격", UTCP는 "에이전트가
기존 API를 싸게 호출하는 매뉴얼 포맷"이다. 자체 에이전트를 만들고 대상 API를 직접 소유한다면
UTCP는 합리적 선택이고, 그 밖의 경우에는 MCP가 유일한 실용 경로다. UTCP 홍보 자료의 지연·메모리·
유지보수 수치는 자체 보고이며 일부는 내부적으로 모순되므로 **의사결정 근거로 쓰지 않는다.**

---

## 10. 미확인 항목

1. **채택 현황** — `utcp.io/registry`는 동적 렌더링이라 목록을 받지 못했고 `"All Providers (0)"`로
   표시됐다. `utcp.io/showcase`는 이번에 열지 않았다. "N개 제품이 지원한다"류 숫자는 이 문서의
   근거에 없다.
2. **명세 원문의 규범 문구** — `utcp.io/api`는 모듈 색인이고 필드 단위 정의는 모듈별 하위 페이지에
   흩어져 있다. 이 문서의 모델 기술은 **참조 구현 소스**가 근거이며, 명세 문서와의 1:1 대조는 미실시.
3. **성능** — 양측 어느 수치도 이 조사에서 재현하지 않았다. 필요하면 동일 API를
   (a) 직접 호출 (b) MCP 서버 경유로 띄워 p50/p99와 RSS를 측정해야 한다.
4. **`code-mode`** — 같은 조직의 [`code-mode`](https://github.com/universal-tool-calling-protocol)가
   ★1,644로 UTCP 본체보다 크다. 모델이 도구 호출 대신 코드를 써서 호출하는 접근으로, 이 주제의
   제3의 축이지만 이번 조사 범위 밖이다.

---

## 11. 출처

**1차 (이 문서의 근거)**
- [utcp.io 소개](https://www.utcp.io/) · [명세 API 색인](https://www.utcp.io/api) · [UTCP vs MCP](https://www.utcp.io/utcp-vs-mcp) · [Security Considerations](https://www.utcp.io/security) · [registry](https://www.utcp.io/registry)
- [GitHub 조직 universal-tool-calling-protocol](https://github.com/universal-tool-calling-protocol) — 저장소 스타·푸시·릴리스는 GitHub API로 수집
- PyPI 휠 직접 해체: `utcp` 1.2.0, `utcp-http` 1.1.15, `utcp-mcp` 1.2.0 (+ PyPI JSON API로 전체 릴리스 이력)
- MCP 측: 이 저장소의 [기본 개념과 구조](MCP_기본개념_구조.md)·[프리미티브 상세](MCP_프리미티브_상세.md)·[SDK 2.0.0 분석](MCP_SDK_2.0_분석.md) (명세 2026-07-28, `mcp` 2.3.0 확인)

**2차 (교차 확인용, 본문 근거로는 사용하지 않음)**
- [Nordic APIs — MCP vs UTCP](https://nordicapis.com/model-context-protocol-mcp-vs-universal-tool-calling-protocol-utcp/) · [HyScaler](https://hyscaler.com/insights/mcp-vs-utcp/) · [MarkTechPost](https://www.marktechpost.com/2025/09/21/understanding-the-universal-tool-calling-protocol-utcp/)

**재현 방법** (휠 해체)

```bash
# pip 인덱스 접근이 막힌 환경에서도 파일만 받아 읽을 수 있다
URL=$(curl -s https://pypi.org/pypi/utcp/json | python3 -c \
  "import json,sys;print(next(f['url'] for f in json.load(sys.stdin)['urls'] if f['packagetype']=='bdist_wheel'))")
curl -sL -o utcp.whl "$URL"
python3 -I -c "import zipfile;z=zipfile.ZipFile('utcp.whl');print(z.read('utcp/data/tool.py').decode())"
```
