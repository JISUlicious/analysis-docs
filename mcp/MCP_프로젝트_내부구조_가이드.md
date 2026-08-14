# 다중 MCP 서버 프로젝트의 내부 구조 가이드

> **전제**: [다중 서버 단일 프로젝트 구성](MCP_다중서버_단일프로젝트_구성.md)으로 서빙은 해결됐고,
> 이제 **한 저장소 안에서 서버들을 코드로 어떻게 분리할 것인가**.
> **검증**: 제안 구조를 실제 스캐폴드로 만들어 6개 항목 실측 (PoC: [`mcp_scaffold/`](mcp_scaffold/)).

---

## 1. 설계 원칙 — 디렉터리 경계 = 소유권 경계

이 프로젝트의 특수 조건: **데이터 담당자들이 각자 도구를 만든다.** 그래서 구조의 1차 기준은
기술적 계층이 아니라 **누가 무엇을 소유하는가**다.

```
mcp_platform/          ← 플랫폼팀 소유 (공통·배포)
├── settings.py        env 파싱 (MCP_SERVE_MODE, MCP_SERVERS, ...)
├── registry.py        서버 발견
├── serve.py           A/B안 조립 (path/port)
├── auth/              Authenticator 구현체들
└── db.py              DBHub

servers/               ← 담당자 소유 (한 디렉터리 = 한 서버 = 한 팀)
├── sales/
│   ├── __init__.py    build() 재노출 (규약)
│   ├── server.py      FastMCP 조립 — 도구 등록만
│   ├── tools.py       도구 구현 (순수 함수)
│   └── queries.py     SQL/도메인 로직 (선택)
└── hr/
    └── ...

tests/
├── platform/
└── servers/sales/     ← 소유자별 테스트도 같은 경계
```

**한 이름이 4곳을 관통한다** (single source of truth): 디렉터리명 `sales` = 서버명 =
URL 경로 `/sales/mcp` = `AUTH_PLAN`/`DBHub.access_plan` 키. 이름 매핑 테이블을 따로
두지 않는다.

**CODEOWNERS와 정렬**:
```
/servers/sales/   @sales-data-team
/servers/hr/      @hr-data-team
/mcp_platform/    @platform-team
```

## 2. 쟁점별 검토

### 2-1. 서브디렉터리 vs 단일 모듈

| 방식 | 평가 |
|---|---|
| `servers/sales.py` (단일 모듈) | 도구 2~3개면 충분. 하지만 커지면 쪼개야 하고, 그때 import 경로가 바뀐다 |
| **`servers/sales/` (서브패키지)** | **권장** — 처음부터 확장 여지(tools/queries/schemas 분리), 테스트·CODEOWNERS 경계가 자연스러움, 파일 단위 잠금으로 담당자 간 머지 충돌 최소 |

서브패키지 내부 3분할(`server.py` / `tools.py` / `queries.py`)의 실익:
**`tools.py`가 MCP를 모르게 되어** 도구 로직을 순수 함수로 단위 테스트할 수 있다
(검증 ⑤: `await sales_tools.top_orders(3)` 직접 호출).

### 2-2. 자동 발견 vs 명시적 등록

| 방식 | 문제 |
|---|---|
| 중앙 `REGISTRY = {...}` 딕셔너리 | 서버 추가마다 **중앙 파일 편집** → 담당자 간 머지 충돌 상시, 플랫폼팀 리뷰 병목 |
| 순수 자동 스캔 | 디렉터리만 두면 자동 노출 — **의도치 않은 공개** 위험, 한 서버의 import 오류가 전체 기동 실패 |
| **스캔 + opt-in + 실패 격리** | **권장** — 아래 |

```python
def discover(package="servers", only=None):
    for mi in pkgutil.iter_modules(pkg.__path__):
        if not mi.ispkg or mi.name.startswith("_"):   # _ 접두사 = 비공개
            continue
        if only and mi.name not in only:              # MCP_SERVERS 서브셋
            continue
        try:
            found[mi.name] = importlib.import_module(...).build
        except Exception as e:                        # ★ 실패 격리
            failed[mi.name] = ...; log.error(...)
```

- **규약**: `servers/<name>/__init__.py`가 `build() -> FastMCP`를 노출. 그 외 계약 없음.
- **실패 격리가 핵심**: 담당자 A의 실수가 담당자 B의 서버를 죽이면 안 된다
  (검증 ①: `broken` 서버 import 실패 시에도 나머지 2개 정상 기동).
  단, **failed는 반드시 로그·메트릭으로 노출** — 조용히 사라지면 더 나쁘다.
- 운영 안전판: 프로덕션은 `MCP_SERVERS`로 **명시 목록**만 기동(스캔은 개발 편의),
  실패가 있으면 기동 실패시키는 `MCP_STRICT_DISCOVERY=1` 옵션 권장.

### 2-3. 의존 방향 — 가장 중요한 규칙

```
servers/*  →  mcp_platform/*     ✅ 허용
servers/A  →  servers/B          ❌ 금지 (서버 간 직접 참조)
mcp_platform/*  →  servers/*     ❌ 금지 (플랫폼이 특정 서버를 알면 안 됨)
```

교차 참조를 허용하면 배포 분리(C안)가 불가능해지고 소유권이 무너진다. 공유가 필요하면
**플랫폼으로 승격**하거나 별도 공용 패키지로 뺀다.

**CI로 강제** (검증 ④ — AST 스캔 20줄, 고의 위반 주입 시 정확히 검출):

```python
for py in pathlib.Path("servers").rglob("*.py"):
    owner = py.relative_to("servers").parts[0]
    for m in imported_modules(ast.parse(py.read_text())):
        if m.startswith("servers.") and m.split(".")[1] != owner:
            fail(f"{py}: cross-server import {m}")
```

### 2-4. 도구 네이밍

서버가 URL로 분리되므로 **도구 이름에 접두사를 붙이지 않는다**(`sales_top_orders` ❌).
클라이언트는 서버 단위로 등록하므로 이름 공간이 이미 분리돼 있고, 접두사는 모델이 보는
토큰만 늘린다. 단, 한 클라이언트가 여러 서버를 동시에 붙일 때 **같은 이름의 도구가 둘**이면
혼동될 수 있으니, 도메인 색채가 있는 이름(`top_orders`, `headcount`)을 권장.

### 2-5. 공통 코드의 위치

| 성격 | 위치 |
|---|---|
| 커넥션·인증·설정·직렬화 헬퍼 | `mcp_platform/` |
| 두 서버 이상이 쓰는 도메인 로직 | 별도 패키지(`common/`)로 — servers 간 직접 import 대신 |
| 한 서버 전용 | 해당 `servers/<name>/` 안 |

## 3. 검증 결과 (스캐폴드 실측)

```
① 전체 발견 + 실패 격리 : 성공 ['hr','sales'] / 실패 {'broken': ModuleNotFoundError}
                          → broken 하나가 죽어도 나머지 정상 기동 = True
② 서브셋 선택           : only={'sales'} → ['sales']
③ build() 규약          : sales 서버 도구 ['top_orders'] 정상 노출
④ 의존 방향 검사        : 위반 없음 ✅ / 고의 위반 주입 시
                          → ['servers/hr/leak.py: imports servers.sales'] 검출
⑤ 도구 단위 테스트      : MCP 없이 top_orders(3) 직접 호출 성공
⑥ 경계 일치            : DBHub allowlist가 디렉터리 경계와 일치 (hr→sales 차단)
```

⑥이 이 구조의 요점이다 — **디렉터리·URL·인증·DB 접근 권한이 같은 이름 하나로 정렬**되므로,
"sales 팀 코드는 sales 데이터만" 이 구조적으로 보장된다.

## 4. 담당자 온보딩 (새 서버 추가 절차)

1. `servers/<name>/` 생성 — `__init__.py`(build 재노출), `server.py`, `tools.py`
2. `server.py`에 `build() -> FastMCP` 구현, 도구 등록
3. DB 접근이 필요하면 플랫폼팀에 **타깃·allowlist 등록 요청** (코드 아님, 설정)
4. `tests/servers/<name>/` 에 도구 단위 테스트 (MCP 없이 순수 함수 호출)
5. CODEOWNERS에 팀 추가
6. 배포: `MCP_SERVERS`에 이름 추가 → URL `https://host/<name>/mcp` 자동 생성

**플랫폼팀 코드 수정은 0줄** — 이것이 구조가 잘 잡혔는지의 리트머스 시험이다.

## 5. 안티패턴

| 안티패턴 | 왜 나쁜가 |
|---|---|
| `tools/` 아래에 전 서버 도구를 평면 배치 | 소유권 경계 소실, 머지 충돌, 배포 분리 불가 |
| 서버 간 직접 import (`from servers.sales import ...`) | C안 배포 분리 붕괴, 순환 의존 |
| 플랫폼이 서버 이름을 하드코딩 | 서버 추가마다 플랫폼 수정 → 병목 |
| 도구 함수 안에서 `create_async_engine` | 풀 난립 (DB 설계 문서 §3) |
| 중앙 REGISTRY 딕셔너리 수동 관리 | 머지 충돌 상시 |
| 디렉터리명 ≠ 서버명 ≠ URL 경로 | 매핑 테이블 필요 → 실수 지점 증가 |
