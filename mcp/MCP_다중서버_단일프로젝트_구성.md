# 단일 파이썬 프로젝트로 다중 MCP 서버 제공하기 — 구성 방식 검토

> **요구사항**: 하나의 파이썬 프로젝트에서 **분리된 MCP 서버 여러 개**를 제공하고,
> 사용자는 **필요한 URL만** 자기 에이전트에 등록해 사용.
> **검증**: 두 방식 모두 mcp SDK 1.29(FastMCP, `stateless_http=True`) + uvicorn으로
> 실제 구동·클라이언트 접속·도구 격리·호출까지 확인. PoC: [`multi_mcp_poc.py`](multi_mcp_poc.py)(A안),
> [`multi_mcp_ports_poc.py`](multi_mcp_ports_poc.py)(B안),
> [`multi_mcp_layered_poc.py`](multi_mcp_layered_poc.py)(§5 레이어 분리 + 환경변수 제어 — **최종 권장 형태**),
> [`multi_mcp_auth_poc.py`](multi_mcp_auth_poc.py)(§7 서버별 Bearer 인증).

---

## 0. 원리

Streamable HTTP에서 **"MCP 서버 1개 = 엔드포인트 URL 1개"**다. FastMCP 인스턴스마다 독립
ASGI 앱(`streamable_http_app()`)이 나오므로, 이 앱들을 어떻게 배치하느냐가 곧 구성 방식이다.
어느 방식이든 각 URL은 **완전히 독립된 서버**(capability 협상·도구 목록 별개)로 보인다.

## 1. A안 (권장): 단일 포트 + path 분리 — ✅ 검증됨

```
http://host:8765/calc/mcp  →  도구: ['add']
http://host:8765/text/mcp  →  도구: ['upper']
격리: calc 서버에 'upper' 없음 = True / calc.add(2,40) = 42
```

```python
calc = FastMCP("calc-server", stateless_http=True)
text = FastMCP("text-server", stateless_http=True)
REGISTRY = {"calc": calc, "text": text}

@contextlib.asynccontextmanager
async def lifespan(app):
    async with contextlib.AsyncExitStack() as stack:
        for srv in REGISTRY.values():
            await stack.enter_async_context(srv.session_manager.run())   # ★ 필수
        yield

app = Starlette(
    routes=[Mount(f"/{name}", app=srv.streamable_http_app())
            for name, srv in REGISTRY.items()],
    lifespan=lifespan)
# uvicorn.run(app, port=8765)  → 최종 URL = /{name}/mcp
```

**⚠ 함정**: Starlette는 **mount된 하위 앱의 lifespan을 실행하지 않는다** → 각 서버의
`session_manager.run()`을 루트 lifespan에서 직접 시작해야 한다(빠뜨린 path만 500).
최종 URL은 `마운트경로 + /mcp`(FastMCP 기본 `streamable_http_path`).

## 2. B안: 동일 host + 포트 분리 (단일 프로세스) — ✅ 검증됨

한 프로세스에서 `uvicorn.Server` 여러 개를 asyncio 태스크로 띄우는 방식. **가능하다**:

```
http://127.0.0.1:8766/mcp  →  도구: ['add']
http://127.0.0.1:8767/mcp  →  도구: ['upper']
text.upper('port-based') = PORT-BASED
```

```python
PORTS = {8766: calc, 8767: text}
for port, srv in PORTS.items():
    cfg = uvicorn.Config(srv.streamable_http_app(), port=port, log_level="error")
    asyncio.create_task(uvicorn.Server(cfg).serve())
```

**A안과의 기술적 비대칭 (검증에서 확인)**: 직접 서빙에서는 앱 **자체 lifespan이
session manager를 자동 구동** → A안의 수동 `AsyncExitStack` 배선이 필요 없다.
구현 난이도는 B안이 오히려 낮다.

**그럼에도 운영 관점에서는 A안 우위**:

| 관점 | A안 (path) | B안 (port) |
|---|---|---|
| 노출 | 포트 1개 — TLS 인증서·방화벽·LB 리스너 1개 | 서버 수만큼 포트 개방·관리 |
| k8s | Service/Ingress 1개, path 라우팅 | Service 포트 N개 (또는 Service N개), NetworkPolicy 복잡 |
| 서버 추가 | 코드만 (REGISTRY에 등록) | 코드 + 인프라(포트 개방) 변경 |
| URL 형태 | `https://mcp.example.com/calc/mcp` | `https://mcp.example.com:8766/mcp` (URL에 포트 노출) |
| 프록시 뒤 배치 | 자연스러움 | 포트 포워딩 규칙 N개 |
| 적합 상황 | 일반적 서비스 제공 | 사내망 직결·간단한 데모, 프록시 없는 환경, path rewrite가 불가능한 특수 인프라 |

결론: **B안은 기술적으로 문제없이 동작하며 구현도 단순하지만, 포트가 늘수록 인프라 관리
비용이 선형 증가**한다. 프록시/ingress를 둘 수 있는 환경이면 A안, 그게 오히려 부담인
소규모·사내 환경이면 B안도 실용적.

## 3. C안: 같은 프로젝트 → 배포 분리 (A안의 확장)

특정 서버만 트래픽 폭주·장애 격리·독립 스케일링이 필요해지면:

```python
enabled = os.environ.get("SERVERS", "").split(",") or REGISTRY.keys()
routes = [Mount(f"/{n}", app=REGISTRY[n].streamable_http_app()) for n in enabled]
```

**동일 이미지**로 Deployment를 나눠(`SERVERS=calc` / `SERVERS=text`) ingress가 path별로
다른 Deployment로 라우팅. A안과 코드 호환이라 무중단 전환 가능.

## 4. D안 (기각): 서버 1개에 전체 도구 + 클라이언트 필터

기술적으로 가능하나 요구사항("필요한 것만 등록") 미충족 — 사용자가 전체 도구 목록을 받고
걸러내야 하며, 불필요한 도구가 LLM 프롬프트를 오염시킨다.

## 5. 최종 권장 아키텍처 — 정의/배포 레이어 분리 + 환경변수 제어 (✅ 검증됨)

서버 인스턴스를 **만드는 레이어**와 **배포하는 레이어**를 분리하면, A/B안 선택과 서브셋
선택(C안)이 전부 배포 시점의 환경변수 문제가 된다. 코드 수정 없이 배포 형태를 바꾼다.

### 5-1. 레이어 1: 서버 정의 (배포 지식 전무)

```python
# servers/calc.py — 전송·포트·경로를 전혀 모름
def build_calc() -> FastMCP:
    srv = FastMCP("calc-server", stateless_http=True)
    @srv.tool()
    def add(a: int, b: int) -> int: ...
    return srv

# registry.py
REGISTRY = {"calc": build_calc, "text": build_text}   # 이름 → 팩토리(lazy)
```

- **팩토리(lazy) 패턴** 권장: import 시점이 아니라 배포 시점에 인스턴스 생성 —
  서브셋 배포에서 안 쓰는 서버의 초기화 비용/부작용 회피.

### 5-2. 레이어 2: 배포 (환경변수가 유일한 제어면)

| 환경변수 | 값 | 의미 |
|---|---|---|
| `MCP_SERVE_MODE` | `path`(기본) / `port` | **A안/B안 선택** |
| `MCP_SERVERS` | `calc,text` (기본: 전체) | 마운트할 서버 서브셋 (C안 겸용) |
| `MCP_HOST` | 기본 `127.0.0.1` | bind host |
| `MCP_HTTP_PORT` | path 모드 전용, 기본 8765 | 단일 포트 |
| `MCP_PORT_MAP` | port 모드: `calc=8766,text=8767` | 명시 포트 매핑 |
| `MCP_PORT_BASE` | port 모드, 매핑 없을 때 기본 8766~ | 순차 자동 할당 |

배포 레이어는 config를 읽어 (mode에 따라) A안 조립(루트 Starlette + Mount +
AsyncExitStack lifespan) 또는 B안 조립(포트별 `uvicorn.Server` 태스크)을 수행한다.
전체 구현은 `multi_mcp_layered_poc.py` (약 60줄).

### 5-3. 검증 결과 — 코드 동일, 환경변수만 변경

```
(기본)                 [path] :8765/calc/mcp → ['add']   :8765/text/mcp → ['upper']
MCP_SERVE_MODE=port    [port] :8766/mcp → ['add']        :8767/mcp → ['upper']
MCP_SERVERS=calc       [path] :8765/calc/mcp → ['add']   (text 미기동)
```

### 5-4. 배포 시나리오 매핑

| 시나리오 | 환경변수 |
|---|---|
| 통합 서비스 (일반) | (기본값) — 단일 포트 path 분리 |
| 사내망 직결·프록시 없음 | `MCP_SERVE_MODE=port MCP_PORT_MAP=...` |
| 특정 서버 독립 스케일 (k8s Deployment 분리) | Deployment마다 `MCP_SERVERS=<서브셋>` — 동일 이미지 |
| 로컬 개발 (한 서버만) | `MCP_SERVERS=calc` |

이 구조의 요점: **서버를 추가할 때 건드리는 곳은 정의 레이어(모듈 1개 + REGISTRY 1줄)뿐**이고,
어디에 어떤 형태로 노출할지는 전적으로 배포 환경의 결정으로 남는다.

## 6. 공통 설계 권고

1. **`stateless_http=True`**: 세션 어피니티 불필요 → 복제본 수평 확장. 2026-07-28 스펙
   방향과 정합(SDK 2.0에선 유일 모드 — `streamable_http_app()` 패턴은 2.0 `MCPServer`에도
   존재하므로 마이그레이션 경로도 동일).
2. **인증은 mount(또는 포트) 단위**: 각 앱을 인증 미들웨어로 감싸 서버별 토큰/스코프 적용.
   스펙상 목록이 "연결별로" 달라지면 안 되지만 **자격증명별 차이는 허용**(요청 단위 입력).
3. **서버 간 도구 이름 충돌 무관** — URL별 독립 네임스페이스. 공통 비즈니스 로직은 프로젝트
   내부 모듈로 공유하고 노출만 서버별로 나누는 구조가 이 방식의 장점.
4. **사용자 등록 방법**:
   - ADK: `McpToolset(connection_params=StreamableHTTPConnectionParams(url="https://host/calc/mcp"))`
   - Claude Code: `claude mcp add --transport http calc https://host/calc/mcp`
5. 헬스체크는 루트 앱에 별도 route(`/healthz`)로 — MCP 엔드포인트와 분리.

## 7. 인증 미들웨어 — 서버(mount)별 Bearer 토큰 (✅ 검증됨)

mount 단위로 순수 ASGI 래퍼를 끼우면 서버별 독립 토큰이 된다. 검증 결과:

```
① 무토큰            → HTTP 401
② calc에 text 토큰  → HTTP 401   (서버별 토큰 격리)
③ 정상 토큰(calc)   → 도구 ['add'], add(1,2)=3   (정상 MCP 세션)
④ /healthz 무인증   → HTTP 200   (프로브 경로는 인증 밖)
```

```python
class BearerAuth:                       # 순수 ASGI 래퍼 — 프레임워크 무관
    def __init__(self, app, token: str):
        self.app, self.expected = app, f"Bearer {token}"
    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            hdrs = dict(scope.get("headers") or [])
            if hdrs.get(b"authorization", b"").decode() != self.expected:
                resp = JSONResponse({"error": "unauthorized"}, status_code=401)
                return await resp(scope, receive, send)
        return await self.app(scope, receive, send)

# 배포 레이어에서: 토큰은 환경변수(MCP_AUTH_TOKENS="calc=...,text=...") ← k8s Secret
Mount(f"/{n}", app=BearerAuth(srv.streamable_http_app(), TOKENS[n]))
```

- B안(포트 분리)에서도 동일 — `uvicorn.Config(BearerAuth(app, tok), port=...)`.
- `/healthz`는 mount 밖 루트 route라 자연히 인증 제외 (k8s 프로브용).
- **클라이언트 쪽 등록**:
  - ADK: `StreamableHTTPConnectionParams(url=..., headers={"Authorization": "Bearer <tok>"})`
  - Claude Code: `claude mcp add --transport http calc <url> --header "Authorization: Bearer <tok>"`
- 스펙 정합 참고: 정적 Bearer는 사내/내부용 실용 해법이다. 외부 공개 서비스라면 스펙의
  **OAuth 2.1 리소스 서버** 모델(FastMCP `auth`/`token_verifier` 파라미터)로 올리는 것이
  정식 경로 — 미들웨어 자리는 동일하므로 교체 비용은 국소적.
- 토큰별로 노출 도구를 달리하고 싶으면: 스펙상 목록의 "연결별" 가변은 금지지만
  **자격증명별 가변은 허용** — 다만 그 수준이 필요해지면 서버를 더 쪼개는 편이 단순하다.

## 8. 인프라(k8s) 담당자 전달 사항 — 앱이 요구하는 계약만 성문화

배포 방법은 담당자 재량. 단, **이 앱의 동작 특성상 반드시 지켜져야 하는 사항**:

1. **바인딩**: 컨테이너에서는 `MCP_HOST=0.0.0.0` 필수 (기본값 127.0.0.1 — 미설정 시 외부 접속 불가).
   리슨 포트는 `MCP_HTTP_PORT`(기본 8765) 하나.
2. **경로 라우팅**: 엔드포인트는 `/{서버명}/mcp` 형태의 path Prefix.
   **경로 rewrite 금지** — `/calc/mcp`를 `/mcp`로 벗겨서 전달하면 mount가 매칭되지 않아 404.
   경로를 그대로 보존해 전달해야 한다.
3. **스트리밍 응답**: MCP Streamable HTTP는 POST 응답이 **SSE 스트림**일 수 있다.
   프록시/LB에서 응답 버퍼링 비활성화, idle timeout을 도구 최장 실행 시간 이상으로.
4. **무상태**: 세션 어피니티(sticky session) 불필요·설정 금지. 복제수/HPA 자유.
5. **헬스체크**: `GET /healthz` — 무인증. (그 외 모든 경로는 Bearer 인증으로 401)
6. **시크릿**: `MCP_AUTH_TOKENS` env로 `이름=토큰,이름=토큰` 형식 주입 (Secret 권장).
7. **배포 형태 제어는 전부 env** (§5-2 표): 기본값 = path 모드·전체 서버.
   서버별 배포 분리 시 `MCP_SERVERS=<서브셋>`으로 동일 이미지 재사용 —
   이때 **사용자에게 노출되는 URL은 변하지 않아야 한다** (path 라우팅만 백엔드 교체).
8. **종료**: SIGTERM 시 uvicorn graceful shutdown — 진행 중 스트림 고려해
   종료 유예(terminationGracePeriod)를 짧게 잡지 말 것.
