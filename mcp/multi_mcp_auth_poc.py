"""인증 추상화 PoC: Authenticator 인터페이스 + 단일 AuthMiddleware.
   구현체: StaticBearer / ApiKey / Jwt(HS256) / Chain(any-of) / AllowAll.
   배선: 서버별로 다른 인증 — calc=Chain(Bearer,ApiKey), text=JWT.
"""
import asyncio, contextlib, time
from dataclasses import dataclass, field
from typing import Protocol
import jwt as pyjwt   # mcp 의존성에 포함
import httpx, uvicorn
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
from mcp.server.fastmcp import FastMCP
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

# ═══ 인증 추상화 계층 ════════════════════════════════════════════════════════
@dataclass
class AuthResult:
    ok: bool
    principal: str | None = None
    claims: dict = field(default_factory=dict)
    error: str = "unauthorized"
    challenge: str | None = None          # WWW-Authenticate 값

class Authenticator(Protocol):            # ← 확장 지점: 이것만 구현하면 됨
    async def authenticate(self, headers: dict[str, str]) -> AuthResult: ...

class AuthMiddleware:
    """적용 지점은 하나 — 어떤 Authenticator든 주입 가능."""
    def __init__(self, app, authenticator: Authenticator):
        self.app, self.auth = app, authenticator
    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {k.decode().lower(): v.decode()
                   for k, v in (scope.get("headers") or [])}
        result = await self.auth.authenticate(headers)
        if not result.ok:
            hdrs = {"WWW-Authenticate": result.challenge} if result.challenge else {}
            resp = JSONResponse({"error": result.error}, status_code=401, headers=hdrs)
            return await resp(scope, receive, send)
        scope.setdefault("state", {})["principal"] = result.principal   # 하류 전파
        return await self.app(scope, receive, send)

# ═══ 구현체들 ════════════════════════════════════════════════════════════════
class StaticBearer:
    def __init__(self, token, principal="bearer-user"):
        self.token, self.principal = token, principal
    async def authenticate(self, h):
        if h.get("authorization") == f"Bearer {self.token}":
            return AuthResult(True, self.principal)
        return AuthResult(False, error="invalid bearer token", challenge="Bearer")

class ApiKey:
    def __init__(self, keys: dict[str, str], header="x-api-key"):
        self.keys, self.header = keys, header
    async def authenticate(self, h):
        who = self.keys.get(h.get(self.header, ""))
        return AuthResult(True, who) if who else \
               AuthResult(False, error="invalid api key")

class Jwt:
    def __init__(self, secret, issuer, audience, alg="HS256"):
        self.secret, self.iss, self.aud, self.alg = secret, issuer, audience, alg
    async def authenticate(self, h):
        tok = h.get("authorization", "").removeprefix("Bearer ").strip()
        try:
            claims = pyjwt.decode(tok, self.secret, algorithms=[self.alg],
                                  issuer=self.iss, audience=self.aud)
            return AuthResult(True, claims.get("sub"), claims)
        except Exception as e:
            return AuthResult(False, error=f"jwt: {type(e).__name__}",
                              challenge='Bearer error="invalid_token"')

class Chain:
    """any-of: 순서대로 시도, 하나라도 통과하면 성공. challenge는 취합."""
    def __init__(self, *authenticators): self.chain = authenticators
    async def authenticate(self, h):
        fails = []
        for a in self.chain:
            r = await a.authenticate(h)
            if r.ok: return r
            fails.append(r)
        challenge = ", ".join(f.challenge for f in fails if f.challenge) or None
        return AuthResult(False, error="; ".join(f.error for f in fails),
                          challenge=challenge)

class AllowAll:                            # 개발용
    async def authenticate(self, h): return AuthResult(True, "anonymous")

# ═══ 서버 정의 + 배선 (서버별로 다른 인증기 주입) ════════════════════════════
def build(name, fn):
    srv = FastMCP(name, stateless_http=True)
    srv.tool()(fn)
    return srv

def add(a: int, b: int) -> int: return a + b
def upper(s: str) -> str: return s.upper()

JWT_SECRET, ISS, AUD = "s3cret", "https://auth.example.com", "mcp-text"
AUTH_PLAN = {   # 배포 레이어 설정 (실전에선 env/Secret에서 조립)
    "calc": Chain(StaticBearer("tok-calc"), ApiKey({"key-123": "apikey-user"})),
    "text": Jwt(JWT_SECRET, ISS, AUD),
}
instances = {"calc": build("calc-server", add), "text": build("text-server", upper)}

async def whoami(request):                 # principal 전파 확인용
    return JSONResponse({"principal": request.scope["state"]["principal"]})

@contextlib.asynccontextmanager
async def lifespan(app):
    async with contextlib.AsyncExitStack() as st:
        for s in instances.values():
            await st.enter_async_context(s.session_manager.run())
        yield

app = Starlette(routes=[
    Route("/healthz", lambda r: JSONResponse({"ok": True})),
    Mount("/whoami-test", app=AuthMiddleware(
        Starlette(routes=[Route("/", whoami)]), StaticBearer("tok-calc"))),
    *[Mount(f"/{n}", app=AuthMiddleware(s.streamable_http_app(), AUTH_PLAN[n]))
      for n, s in instances.items()],
], lifespan=lifespan)

# ═══ 검증 ════════════════════════════════════════════════════════════════════
async def mcp_tools(url, headers):
    async with streamablehttp_client(url, headers=headers) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            return [t.name for t in (await s.list_tools()).tools]

async def main():
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8765,
                                           log_level="error"))
    task = asyncio.create_task(server.serve())
    await asyncio.sleep(0.8)
    B = "http://127.0.0.1:8765"

    async with httpx.AsyncClient() as hc:
        r = await hc.post(f"{B}/calc/mcp", json={})
        print(f"① calc 무자격        → {r.status_code}, WWW-Authenticate={r.headers.get('www-authenticate')}")
        r = await hc.post(f"{B}/text/mcp", json={},
                          headers={"Authorization": "Bearer tok-calc"})
        print(f"② text에 calc 토큰   → {r.status_code} ({r.json()['error']})")
        expired = pyjwt.encode({"sub": "u1", "iss": ISS, "aud": AUD,
                                "exp": int(time.time()) - 100}, JWT_SECRET)
        r = await hc.post(f"{B}/text/mcp", json={},
                          headers={"Authorization": f"Bearer {expired}"})
        print(f"③ text 만료 JWT      → {r.status_code} ({r.json()['error']})")
        r = await hc.get(f"{B}/whoami-test/",
                         headers={"Authorization": "Bearer tok-calc"})
        print(f"⑦ principal 전파     → {r.json()}")

    print(f"④ calc Bearer        → 도구 {await mcp_tools(f'{B}/calc/mcp', {'Authorization': 'Bearer tok-calc'})}")
    print(f"⑤ calc ApiKey(체인)  → 도구 {await mcp_tools(f'{B}/calc/mcp', {'X-API-Key': 'key-123'})}")
    good = pyjwt.encode({"sub": "u1", "iss": ISS, "aud": AUD,
                         "exp": int(time.time()) + 300}, JWT_SECRET)
    print(f"⑥ text 정상 JWT      → 도구 {await mcp_tools(f'{B}/text/mcp', {'Authorization': f'Bearer {good}'})}")

    server.should_exit = True
    await task

asyncio.run(main())
