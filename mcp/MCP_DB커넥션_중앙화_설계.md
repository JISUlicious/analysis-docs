# MCP 도구의 DB 커넥션 중앙화 — 비판적 검토와 구현 패턴

> **전제**: [다중 서버 단일 프로젝트](MCP_다중서버_단일프로젝트_구성.md)의 레이어 구조 위에서,
> 분리된 서버마다 데이터 담당자가 DB 조회 도구를 만들되 커넥션 관리는 중앙화하려는 방향.
> **검증**: 제안 패턴(DBHub)을 SQLAlchemy async + FastMCP로 구현해 5개 항목 실측
> (PoC: [`db_hub_poc.py`](db_hub_poc.py)).

---

## 1. 비판적 검토 — "중앙화가 합당한가"

### 1-1. 합당한 근거 (강함)

1. **풀 난립 방지**: 단일 프로세스에 서버 N개(A안)일 때 담당자마다 엔진을 만들면
   같은 DB에 N개의 풀 — 커넥션 수는 DB의 공유 희소 자원이라 통제 불능이 된다.
   중앙화하면 **프로세스당 타깃당 1엔진**으로 통합된다.
2. **정책·가드레일의 단일 지점**: timeout, row limit, read-only 강제, pool_pre_ping,
   자격증명 회전 — 담당자 개개인이 잊기 쉬운 것을 플랫폼이 강제.
3. **라이프사이클 정확성**: async 엔진은 이벤트 루프 친화적으로 생성·정리돼야 한다.
   배포 레이어 lifespan(이미 있는 AsyncExitStack)에 통합하는 것이 구조적으로 맞다.
4. **관측 단일화**: 풀 사용률·슬로우 쿼리·감사 로그를 한곳에서.

### 1-2. 반론과 한계 (설계에 반영해야 성립)

1. **"중앙화"의 실체를 정확히**: C안(배포 분리) 순간 풀은 어차피 **프로세스별**이다.
   중앙화되는 것은 **코드·정책·설정**이지 전역 단일 런타임 풀이 아니다.
   총 커넥션 = Σ(배포별 replicas × (pool_size+max_overflow)) — **예산 공식으로 관리**해야
   하며, 이는 인프라 전달 사항이다.
2. **보안 경계 약화 위험 (최대 리스크)**: 중앙층이 슈퍼 계정 하나로 모든 DB에 붙으면,
   한 도구의 버그/프롬프트 인젝션이 **타 도메인 데이터 유출**로 직결된다.
   → 반드시 **타깃(도메인)별 자격증명** + **서버별 접근 allowlist**를 중앙층에 내장.
3. **noisy neighbor**: 풀 공유의 대가 — 한 서버의 폭주 쿼리가 같은 타깃을 쓰는 다른
   서버를 굶긴다. → 타깃별 풀 상한, 필요 시 (서버,타깃)별 엔진 분리 옵션,
   상습 과부하 서버는 C안으로 배포 격리.
4. **플랫폼 병목**: 새 타깃 추가마다 중앙팀 코드 수정이면 담당자들이 막힌다.
   → 타깃 등록을 **선언형 설정**(env/Secret)으로 — 중앙 코드는 제네릭하게.
5. **이질 백엔드의 최소공배수화**: Postgres/BigQuery/Oracle을 하나의 추상화로 덮으면
   백엔드 고유 기능을 잃는다. → 중앙화 단위는 "커넥션·정책"까지만, 쿼리 표현은
   담당자 자유(백엔드별 어댑터 허용).

### 1-3. stateless MCP와의 결합 제약 (중요)

- 도구 호출 = **커넥션 단기 대여**. 호출 간 트랜잭션/커서/커넥션 유지 금지 —
  2026-07-28 stateless 모델에서 왕복 간 상태는 `requestState`로 클라이언트에 맡기는데,
  **커넥션은 거기 담을 수 없다.** MRTR(사용자 확인) 왕복 사이에 tx를 잡고 있는 설계는
  성립 불가. 확인이 필요한 쓰기는 "확인 → 새 호출에서 원자적 실행"으로.

**판정: 조건부 합당.** 중앙화할 것 = 엔진 팩토리·풀·정책·자격증명·allowlist·관측.
중앙화하지 말 것 = 쿼리 로직·도메인 스키마 지식(담당자 소유), 전역 단일 풀이라는 환상.

## 2. 제안 패턴 — DBHub (검증 완료)

기존 구조와 대칭: 서버는 `REGISTRY`, 인증은 `AUTH_PLAN`, 데이터 접근은 **`DBHub(targets, access_plan)`**.

```python
class DBHub:
    def __init__(self, targets, access_plan=None, default_row_limit=500):
        self._engines = {}                      # 프로세스 내 타깃당 1엔진 (lazy)

    def engine(self, target):                   # 루프 안 최초 사용 시 생성 (루프 친화)
        if target not in self._engines:
            self._engines[target] = create_async_engine(self._targets[target],
                                                        pool_pre_ping=True)
        return self._engines[target]

    @asynccontextmanager
    async def connect(self, target, *, server):          # 저수준: 단기 대여
        self._authorize(server, target)                  # allowlist 검사
        async with self.engine(target).connect() as conn:
            yield conn

    async def fetch_all(self, target, sql, params=None, *, server,
                        row_limit=None) -> dict:         # 고수준(권장): 가드레일 내장
        ...  # row_limit+1 조회로 truncated 감지 → {"rows", "row_count", "truncated"}

    async def shutdown(self):                            # 배포 레이어 lifespan에서 호출
        for e in self._engines.values(): await e.dispose()
```

담당자 도구는 좁은 API만 사용:

```python
@sales_srv.tool()
async def top_orders(n: int = 5) -> dict:
    return await hub.fetch_all("sales", "SELECT ... ORDER BY amount DESC",
                               server="sales-mcp", row_limit=n)
```

### 검증 결과 (PoC 실측)

```
① 조회 동작: top_orders(2) → 2행 + truncated=True (전체 5행 중)
② 엔진 캐시(풀 통합): 호출·서버 간 동일 엔진 = True
②' allowlist: sales-mcp가 hr 타깃 접근 → TARGET_FORBIDDEN (오류-as-결과로 모델에 전달)
③ row_limit 가드레일: 초과 감지(truncated) 정상
④ 단기 대여: 호출 종료 후 pool checked_out=0 (반납 완료)
⑤ hub.shutdown() → 전체 엔진 일괄 dispose
```

### 배선 (배포 레이어)

```python
hub = DBHub(targets=load_targets_from_env(),        # 타깃 DSN: Secret 주입
            access_plan={"sales-mcp": {"sales"}, "hr-mcp": {"hr"}})

@contextlib.asynccontextmanager
async def lifespan(app):
    async with contextlib.AsyncExitStack() as st:
        for s in instances.values():
            await st.enter_async_context(s.session_manager.run())
        yield
    await hub.shutdown()                             # 종료 시 정리
```

## 3. 성문화 — 도구 작성자(담당자) 계약

1. **엔진/커넥션 직접 생성 금지** — 모든 DB 접근은 `hub` 경유.
2. 대여는 **도구 호출 스코프 안**에서만. 전역/모듈 변수에 conn/tx 저장 금지.
3. 기본은 `fetch_all`(가드레일 내장). 저수준 `connect`는 사유가 있을 때만.
4. **호출 간 트랜잭션 금지** (stateless — §1-3). 확인 필요한 쓰기는 확인 후
   새 호출에서 원자 실행.
5. 대량 결과는 row_limit 안에서 자르고, 파일로 내릴 거면
   [도구응답 실전패턴 §6](MCP_도구응답_실전패턴.md)의 링크 전달 패턴으로.
6. 새 타깃/자격증명은 코드가 아니라 **설정 등록**으로 요청.

## 4. 인프라 전달 사항 (추가분)

- **커넥션 예산**: Σ(배포별 replicas × (pool_size+max_overflow)) ≤ DB max_connections − 운영 여유분.
  배포 분리(C안)·HPA 증설 시 이 식으로 pool 상한 재조정 필요.
- 타깃별 자격증명은 Secret으로 주입 (도메인별 최소 권한 DB role, 조회 도구엔 read-only role).
