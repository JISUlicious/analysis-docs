# AdkWebServer 세션 라이프사이클 · 메모리 분석 (ADK Python v1.26.0)

> **발단 관측**: `_get_or_create_session`이 동일 세션의 새 요청마다 세션 객체를 새로 만들고
> 기존 것을 재사용하지 않으며, 응답 후에도 세션이 남아 있는 것처럼 보임 → 누수 여부 조사.
> **방법**: v1.26.0 소스 분석 + weakref/gc 실측 (mock LLM, DatabaseSessionService=sqlite 포함).

---

## 1. 구조 — "재사용 없음"은 의도된 설계다

```
요청 → Runner._get_or_create_session → session_service.get_session()
        InMemory : 마스터(self.sessions dict)를 copy.deepcopy → 요청 스코프 스냅샷
        Database : DB rows(SELECT) → pydantic Session 새로 조립   (deepcopy 단계 없음)
      → invocation 동안 스냅샷 사용
      → append_event: InMemory는 스냅샷+마스터 이중 기록 / Database는 DB INSERT + 스냅샷 append
응답 후 → 스냅샷은 회수 대상. 마스터(InMemory dict 또는 DB rows)만 잔류
```

- 매 요청 새 객체를 만드는 것은 **동시 요청 간 격리를 위한 스냅샷 설계**다.
- `DatabaseSessionService`에는 **메모리 마스터가 없다** — DB가 마스터. 인스턴스 상태는
  SQLAlchemy 엔진/풀과 ref-count 관리되는 `_session_locks`(0이면 pop)뿐.

## 2. 실측 결과 (요청 스코프 객체의 운명)

| 측정 | 결과 |
|---|---|
| InMemory, 동일 세션 5회 요청 후 스냅샷 5개 weakref | **전부 회수 (0 잔존)** |
| Database(sqlite), 동일 5회 + Event 25개 weakref + 전체 gc 스캔 | **Session/Event/락 전부 0 잔존** |
| **실행이 살아있는 동안** (버려진 run 방치) | **Session 1 + InvocationContext 2 잔존** (runner 레벨 + agent 레벨 컨텍스트) |
| 실행 취소 **직후** | 1/2 일시 잔존 → **루프 수 턴 후 0/0** (정리 지연, 누수 아님) |

**결론**: 프레임워크 레벨 세션 객체 누수는 없다. "잔존 인스턴스"는 **잔존 실행의 그림자**이며,
실행이 끝나면 사라진다. 판독 시 취소 직후 단일 스냅샷은 오탐 — 짧은 간격 2회 측정으로
추세를 본다.

## 3. 실제 메모리 성장 벡터 (v1.26.0)

| # | 벡터 | InMemory | Database |
|---|---|---|---|
| A | **마스터 무한 성장** — 세션 수×이벤트 수×크기, evict 없음 (`delete_session`은 존재하나 자동 호출 없음) | RAM에서 무한 → 장수 서버의 느린 랫칫 | RAM에선 소멸, 디스크로 이동 (위생상 TTL 삭제 배치 권장) |
| B | **버려진 `/run_sse` 실행** — 끊김 감지 없음. 세션 해제 시점은 "응답 전송 완료"가 아니라 "**invocation 종료**" | 있음 | **동일하게 있음** (서비스 무관). upstream `6a533573` "Abort runs on client drops to avoid leaks"(1.26.0 미포함)가 이를 수정 |
| C | **요청당 전체 히스토리 로드** — O(히스토리) CPU/RAM 스파이크 (누수 아님) | 전체 deepcopy | SQL 조회+역직렬화. 단 DB 서비스는 `GetSessionConfig(num_recent_events/after_timestamp)`를 **SQL LIMIT 수준 지원** — 서브클래스로 기본 상한 강제 가능 (1.26.0 Runner는 config 미전달; `eff724ac`가 후속 추가) |

관련 후속 개선(모두 v1.26.0 미포함): `6a533573`(클라이언트 끊김 시 실행 중단),
`16a1a185`(InMemory shallow-copy 옵션), `eff724ac`(GetSessionConfig를 Runner로 관통).

## 4. `delete_session` 계약 (커스텀 DB 서비스 구현 기준)

ADK 자체 구현(`DatabaseSessionService` + `schemas/v1.py`)이 정의하는 기준:

1. **세션 row + 소속 이벤트 전부 삭제.** ADK는 Core `delete()` 문 + 스키마의
   `ForeignKeyConstraint(ondelete="CASCADE")`로 이벤트를 지운다 (ORM cascade가 아니라
   **DB 레벨 cascade**가 실제 동작; SQLite는 `PRAGMA foreign_keys=ON` 리스너로 보장).
   커스텀 구현은 cascade 보장 또는 명시적 이벤트 DELETE 중 하나 필수 — 아니면 이벤트
   rows가 고아로 남는 DB 쪽 누수.
2. **app/user state는 보존** (`StorageAppState`/`StorageUserState` — 세션보다 넓은 스코프).
3. 멱등 (없는 세션 삭제 = no-op), 단일 트랜잭션.
4. 세션 키 기반 부수 캐시/락이 있다면 함께 정리.

## 5. Cleanup 권고 (우선순위)

1. **DatabaseSessionService 전환** — 벡터 A를 메모리에서 제거하는 정공법.
2. **TTL/용량 eviction** — SessionService 서브클래스(lazy evict) + lifespan 주기 스윕,
   또는 기존 `DELETE /apps/{app}/users/{uid}/sessions/{sid}` 엔드포인트를 프론트
   라이프사이클에 연결. DB에선 `last_update_time` 조건 일괄 DELETE 배치.
3. **SSE 끊김 가드** (벡터 B) — yield 루프마다 `await request.is_disconnected()` 확인 후
   `return` (Aclosing이 실행 체인 정리) + 실행당 wall-time 상한.
4. **히스토리 바운딩** (벡터 C) — DB 서비스의 `num_recent_events` 기본값 강제 서브클래스.
   주의: `EventsCompactionConfig`는 LLM 컨텍스트만 줄이고 **저장된 원본 이벤트는 지우지
   않는다** — 저장소 성장 억제에는 무효.

## 6. "응답 후에도 조금 남아 보이는 것"의 정체

- **RSS는 내려가지 않는다** — CPython allocator/시스템 malloc은 해제된 힙을 OS에 잘
  반환하지 않음(고수위 유지). 객체가 전부 죽어도 외부 계측엔 점유로 보인다.
- 정당한 상주분: SQLAlchemy 엔진/풀, compiled statement LRU 캐시(상한 ~500), aiosqlite
  워커 스레드, ORM/pydantic 1회성 메타데이터.
- 진짜로 남는 경우는 §2의 "실행이 살아있는 동안"뿐 → 잔존 실행 탐지는
  [관측 구현명세](AdkWebServer_잔존실행_관측_구현명세.md)의 RunTracker/객체 프로브/census로.

## 7. 점진적 메모리 증가의 귀속 절차

급성 건(무한 루프성 폭주)과 달리 점진 증가는 단일 원인 단정이 불가하므로 계측으로
귀속한다 — 절차와 판정 표는 관측 명세의 **C5(주기 메모리 census)** 참조. 요지:

1. (전제) 관측 오염원 제거 후 **베이스라인 재측정** — 재현 안 되면 종료.
2. 재현 시: RSS + 객체 카운트 + RunTracker 메트릭 + 타입 분포 diff 주기 수집.
3. 판정: 객체 평탄+RSS↑=allocator/네이티브 · active↑=잔존 실행 · 특정 타입↑=파이썬 누수
   (referrer/tracemalloc으로 지점 특정). InMemory Artifact/Memory 서비스 사용 여부도 점검.
