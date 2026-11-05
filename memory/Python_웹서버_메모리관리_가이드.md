# Python 웹서버 메모리 관리 — 기본 · MCP 서버(2.0+) · Google ADK(1.x / 2.x)

> **출처 세 갈래**: ① [claude.ai 공유 대화](https://claude.ai/share/18d81179-d154-4700-bfd3-141d2a3772ec)
> (2026-09-30, 웹 검색 기반 Q&A 2건 — `del+gc.collect+malloc_trim` 평가, K8s 위 MCP FastAPI 서버)
> ② 이 저장소의 기존 실측 문서 ③ **2026-10-01 설치 패키지 소스 대조** — mcp 1.27.0 / 2.0.0 / 2.2.0,
> google-adk 1.26.0 / 1.39.1 / 2.10.0, uvicorn 0.54.0.
> 기준 시점 최신판: **mcp 2.2.0**(1.x 최신 1.30.0), **google-adk 2.10.0**(1.x 최신 1.39.1).

---

## 0. 근거 등급

| 표기 | 의미 |
|---|---|
| **[소스]** | 이번에 설치한 패키지 소스에서 직접 확인. 파일·줄 번호 표기 |
| **[저장소]** | 이 저장소의 실측 문서 ([세션 메모리 분석](../google-adk/web-server/AdkWebServer_세션_라이프사이클_메모리분석.md), [관측 명세](../google-adk/web-server/AdkWebServer_잔존실행_관측_구현명세.md), [OOM 분석](../google-adk/agent-tool/AgentTool_SkillToolset_OOM_원인분석.md), MCP 문서들) |
| **[외부]** | 공유 대화가 인용한 공식 문서·이슈·엔지니어링 사례. **재검증하지 않음** |

공유 대화의 MCP 부분은 **1.x 기준**이었다(FastMCP, `stateless_http`, python-sdk #756). 2.x 부분은
소스로 다시 확인했고, 그 과정에서 공유 대화와 이 저장소 문서 모두에 보정할 점이 나왔다(§4).

---

## 1. 파이썬 웹서버 메모리 관리 — 기본

### 1-1. 먼저 가를 것: 누수인가, 보유인가

RSS에는 성격이 다른 다섯 가지가 섞여 있다 **[외부]**: 아직 GC되지 않은 객체 · pymalloc이 libc에
반환하지 않은 메모리 · libc가 OS에 반환하지 않은 메모리 · 단편화 · 진짜 누수.
**RSS가 안 내려간다는 사실만으로는 누수가 아니다.** CPython allocator와 glibc malloc은 해제한 힙을
OS에 잘 돌려주지 않아 고수위를 유지한다 **[저장소]**.

판별은 RSS가 아니라 **객체 수·파이썬 힙**으로 한다. 저장소 관측 명세(C5)의 판정표 **[저장소]**:

| 시계열 패턴 | 결론 → 다음 행동 |
|---|---|
| RSS↑ · 객체/타입 수 평탄 | allocator 고수위 또는 네이티브 계층 → plateau 수렴 관찰, 수렴하면 정상 → §1-3 |
| RSS↑ · 진행 중 요청(실행) 수 증가 | **잔존 실행 누적** → 끊김 처리 (§1-5, §3) |
| RSS↑ · 특정 타입 카운트 단조 증가 | 파이썬 레벨 누수 → `gc.get_referrers` 역추적, tracemalloc으로 할당 지점 |

도구: `tracemalloc`(파이썬 힙 증감, 할당 파일:라인) · `gc.get_objects()` 타입 census(diff로 증가 타입 특정).
둘 다 비싸다 — tracemalloc은 **메모리 ~2배**, `gc.get_objects()` 전수 순회는 수십 ms. **상시 켜지 말고
조사 기간에만** env 플래그로 켠다 **[저장소]**.

### 1-2. `del + gc.collect() + malloc_trim(0)` — 응급 패턴이지 해결책이 아니다

| 요소 | 하는 일 | 한계 |
|---|---|---|
| `del` + `gc.collect()` | 참조를 끊고 순환 참조를 회수 | 순환 참조는 보조적 원인인 경우가 대부분 **[외부]** |
| `malloc_trim(0)` | glibc가 쥔 free 페이지를 OS에 반환 | **glibc 전용.** 살아있는 객체 하나가 arena·페이지를 붙잡으면 반환 안 됨. glibc는 스레드별 arena를 만든다 **[외부]** |

한 사례에서 `MALLOC_ARENA_MAX=2` + 주기적 `malloc_trim(0)`으로도 안 풀려 jemalloc으로 교체했다 **[외부]**.
`malloc_trim`은 **대용량 작업 직후의 보조 수단**으로만 둔다.

### 1-3. 대안 — 효과 대비 비용 순

| 순위 | 방법 | 비고 |
|---|---|---|
| 1 | `MALLOC_ARENA_MAX=2` | 코드 수정 0. arena 폭증·단편화 억제. 멀티스레드 성능 저하 가능 **[외부]** |
| 2 | **jemalloc** (`LD_PRELOAD`) | auto-decay로 반환 → `malloc_trim` 불필요 **[외부]** |
| 3 | **워커 재활용** | 요청 N개 후 프로세스 재시작. 원인을 고치지 않지만 운영에서 가장 확실 |
| 4 | 대용량 처리 **프로세스 격리** | 큰 버퍼 작업을 별도 프로세스로. 종료 시 확실히 반환 |

워커 재활용 옵션 **[소스: uvicorn 0.54.0 `--help`]**:

| 옵션 | 효과 |
|---|---|
| `--limit-max-requests N` | N개 처리 후 프로세스 종료 |
| `--limit-max-requests-jitter J` | 재시작 시점을 흩어 워커 동시 재시작 방지 |
| `--limit-concurrency N` | 동시 연결·태스크 상한, 초과 시 **503** — 동시 요청 수 × 요청당 메모리로 **피크를 제한** |
| `--timeout-graceful-shutdown S` | 재시작 시 진행 중 요청 대기 한도 |

Gunicorn이면 `max_requests` + `max_requests_jitter` **[외부]**.

### 1-4. 컨테이너 · K8s

- **지표는 `container_memory_working_set_bytes`.** `usage_bytes`는 회수 가능한 page cache를 포함해 오해를
  부른다. cgroup 한도에 닿으면 커널이 page cache를 먼저 회수하고, 그래도 부족하면 OOM killer **[외부]**.
- **증가 모양으로 원인을 가른다**: 수 시간~수 일 완만한 증가 = 누수·allocator 보유 / 기동 직후·부하 시
  급등 = 용량 산정 문제 **[외부]**.
- 한도 출발점: request = p95, limit = p99 이상 또는 request의 1.5~2배 **[외부]**. 측정 후 재조정.
- **베이스 이미지**: `python:*-slim`(Debian/glibc) + `MALLOC_ARENA_MAX=2`, 부족하면 jemalloc.
  **Alpine(musl)은 `malloc_trim`으로 단편화 힙을 반환할 수 없어 불리** **[외부]**.

```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends libjemalloc2 \
    && rm -rf /var/lib/apt/lists/*
# 경로는 아키텍처별: amd64 = x86_64-linux-gnu, arm64(Graviton·Apple 빌드) = aarch64-linux-gnu
ENV LD_PRELOAD=/usr/lib/x86_64-linux-gnu/libjemalloc.so.2
```

- **프로세스 구성**: Pod 메모리 = 워커 수 × 워커당 메모리. **Pod당 워커 1개 + replica 수평 확장**이
  측정·제한에 유리하다 **[외부]**. 재활용 시 `replicas ≥ 2` + PDB로 무중단.
- **stdio로 띄운 자식 프로세스도 같은 Pod 메모리에 합산된다** (MCP stdio 서버 등).

### 1-5. 요청 스코프 누수의 주범 — 버려진 실행

프레임워크 레벨 객체 누수보다 흔한 것이 **클라이언트는 떠났는데 서버 작업은 계속 도는 경우**다.
실행이 살아 있는 동안 그 요청의 객체(세션 스냅샷·컨텍스트·이력)는 전부 붙잡혀 있다 **[저장소]**.

- 스트리밍 응답: yield 루프마다 끊김 확인 후 종료, 제너레이터는 `aclosing`으로 감싸 정리 보장
- 비스트리밍: **끊김 감시 태스크**가 `await request.receive()`로 `http.disconnect`를 기다렸다가 작업
  태스크를 취소 — CPU 0%로 대기한다 (ADK 2.x가 채택한 방식, §3-3)
- **실행당 wall-time 상한**을 따로 둔다. 끊김 처리가 새도 상한이 막는다
- 판독 주의: 취소 **직후**의 단일 스냅샷은 정리 지연으로 객체가 남아 보인다. 짧은 간격 2회 측정으로
  추세를 본다 **[저장소]**

### 1-6. 권장 순서 (1장 요약)

```
1. 관측: working set + tracemalloc/타입 census로 "누수 vs 보유" 판별
2. 버려진 실행 차단: 끊김 감시 + wall-time 상한 + aclosing
3. MALLOC_ARENA_MAX=2 → 부족하면 jemalloc
4. 안전망: 워커 재활용(+jitter), --limit-concurrency로 피크 제한
5. 한도 재조정: 측정값 기반 request/limit
6. malloc_trim: 특정 대용량 작업 직후의 보조 수단으로만
```

---

## 2. MCP 서버 특화 (Python SDK 2.0 이상)

### 2-1. 출발점 — 2.x에도 상태 유지 세션 경로가 남아 있다

2026-07-28 프로토콜은 스테이트리스지만, SDK 2.x는 **2025년형 클라이언트를 같은 서버에서 계속 받는다**.
그 레거시 경로는 `stateless_http=False`(기본)일 때 **세션을 서버 메모리에 보관**한다
(`StreamableHTTPSessionManager._server_instances: dict[str, StreamableHTTPServerTransport]`,
`server/streamable_http_manager.py:118`) **[소스]**.

| | mcp 1.27.0 (FastMCP) | mcp 2.0.0 (MCPServer) | **mcp 2.2.0** (MCPServer) |
|---|---|---|---|
| `stateless_http` 위치 | 생성자 설정 | **전송 메서드**로 이동 (`streamable_http_app` · `run_streamable_http_async`) | 동일 |
| `stateless_http` 기본값 | False | **False** | **False** |
| 유휴 세션 만료 기본값 | **None — 무기한** (FastMCP로는 설정 불가) | **None — 무기한** (매니저 직접 구성 시에만) | **1800초** — 전송 메서드 인자 |
| 세션 수 상한 | 없음 | 없음 | **10,000** (`max_sessions`, None이면 해제) |
| 요청 바디 상한 | — | 4 MiB | 4 MiB — 초과 시 **413** (`transport_security.py:15`) |

**2.2.0 전까지는 레거시 클라이언트 세션이 기본값으로 무한 누적될 수 있었다.**

### 2-2. 요령

**① 가능하면 `stateless_http=True`.** 레거시 클라이언트에 대해서도 요청마다 새 전송을 만들고 세션을
추적하지 않는다(`_handle_stateless_request`) **[소스]**. 다중 replica를 sticky LB 없이 쓰려면 어차피 필요하다.
대가: 레거시 경로에서 **서버→클라이언트 요청**(elicitation 등)이 불가 — 세션 ID가 없어 응답이 돌아올 곳이
없으므로 `can_send_request=False`, 요청 시 `NoBackChannelError` (알림은 가능) **[소스]**.

**② 상태 유지를 써야 하면 2.2.0 이상 + 상한을 명시.**

```python
app = srv.streamable_http_app(
    session_idle_timeout=600,     # 기본 1800. 이벤트 재전송 retry_interval보다 충분히 길게
    max_sessions=2_000,           # 기본 10,000 — 세션당 메모리 × 이 값이 곧 상한
)
```

2.0.x에서는 `MCPServer` 전송 메서드가 이 인자를 받지 않으므로 매니저를 직접 구성해야 한다.
`max_sessions × 세션당 메모리`가 Pod limit 안에 들어오는지 계산해서 값을 정한다.

**③ 요청 바디 4 MiB 상한을 올리지 말 것.** 이 값이 입력 메모리의 상한이다. 올리면 워커당 동시 요청 수 ×
바디 크기만큼 피크가 커진다.

**④ 상태를 서버 메모리에 두지 않는다.** 2026-07-28 모델에서 요청 간 상태는 ⓐ 도구 인자로 오가는 **핸들**
ⓑ **`request_state`** (MRTR 왕복 상태를 `AESGCMRequestStateCodec`으로 암호화해 클라이언트에 위탁)
ⓒ **외부 저장소**(Redis/DB) 중 하나다 **[저장소: SDK 2.0 문서]**. 장기 작업도 마찬가지 — 호출 하나를 오래
붙잡으면 그 요청 스코프 객체를 그동안 보유한다. **submit → job_id 즉시 반환, 조회 도구로 폴링**하고
잡 상태는 외부 저장소에 둔다.

**⑤ 도구 응답에 파일 바이트를 싣지 않는다.** base64는 원본의 +33%이고 응답 전체가 메모리에 올라간다.
대용량은 `resource_link` + presigned URL **[저장소: 도구 응답 문서 §6]**.

**⑥ DB 커넥션은 프로세스당 타깃당 엔진 1개.** 호출마다 엔진을 만들고 버리면 풀을 재사용 못 하고
(로컬 SQLite 실측 2.7배 느림) DB `max_connections`를 두드린다. 커넥션은 **호출 스코프에서만 대여** —
호출 간 커넥션·트랜잭션 보유 금지 **[저장소: DB 커넥션 문서]**.

**⑦ 정리는 lifespan에서.** 엔진 dispose, 세션 매니저 종료. Starlette에 mount한 하위 앱의 lifespan은
**실행되지 않으므로** 루트 lifespan에서 직접 시작·정리해야 한다 **[저장소: 다중 서버 문서]**.

**⑧ 도구 수·서버 수.** 서버 N개를 한 프로세스에 mount하면 인스턴스·풀·세션 매니저가 N벌이다.
트래픽이 몰리는 서버는 동일 이미지로 배포를 분리(`MCP_SERVERS=<서브셋>`)해 메모리를 격리한다 **[저장소]**.

### 2-3. 1.x에서 올라올 때 확인할 것

| 1.x 시절 문제 | 2.x 상황 |
|---|---|
| 스테이트리스 모드 누수 (python-sdk **#756** "Stateless Mode memory leak") | **2025-07-21 종료** (GitHub API로 상태 확인). 공유 대화의 "수정 여부 미확인"은 해소 |
| 상태 유지 세션 무기한 누적 | **2.2.0부터 기본 상한** (30분 / 10,000) |
| FastMCP에서 유휴 만료 설정 불가 | 2.2.0 `MCPServer` 전송 메서드 인자로 설정 가능 |

---

## 3. Google ADK 특화

### 3-1. 버전별 판도 [소스]

| 항목 | 1.26.0 | **1.39.1** (1.x 최신) | **2.10.0** (최신) |
|---|:---:|:---:|:---:|
| MCP SDK 의존성 | `<2.0` 필수 | `<2` 필수 | **`<3` 선택 extra** (`google-adk[mcp]`) |
| 클라이언트 끊김 시 실행 중단 (upstream `6a533573`) | ❌ | ❌ | ✅ `cli/api_server.py` |
| 요청당 히스토리 로드 상한 `RunConfig.get_session_config` | ❌ | ✅ | ✅ |
| InMemory 세션 "가벼운 복사" | ❌ deepcopy만 | ⚙️ **WIP 플래그, 기본 꺼짐** | ⚙️ 동일 |
| InMemory 세션 만료·축출 | ❌ | ❌ | ❌ |
| MCP 세션 풀 유휴 만료 | ❌ | ❌ | ✅ **900초** |
| AgentTool 자식이 부모 `RunConfig` 상속 | ❌ | ❌ | ✅ |
| 스킬 리소스 오류 재시도 차단 (#5652) | ❌ | ❌ | ✅ |
| 스킬 로드 후 같은 턴에서 계속 (#6225 취지) | ❌ | ❌ | ✅ 문구 확인 ("in the SAME turn") |
| AgentTool 자식 오류를 결과로 노출 (`e737f229`) | ❌ | ❌ | ✅ `error_message` 폴백 |
| `max_llm_calls` 기본값 | 500 | 500 | 500 |

**1.x 라인은 메모리 관련 수정을 거의 받지 못했다.** 1.x에 들어간 것은 히스토리 상한 설정(**1.27.0**, 릴리스 노트
"pass GetSessionConfig through Runner")과 기본 꺼진 가벼운 복사(**1.29.0**, "Option to use shallow-copy for session in
InMemorySessionService") 둘뿐이다. 나머지는 모두 2.x에만 있다. (표는 1.26.0·1.39.1·2.10.0 세 버전의 존재 여부 비교)

### 3-2. 2.0 미만 (1.x) — 프레임워크가 막아주지 않으니 앱에서 막는다

**① 운영에서 `InMemorySessionService`를 쓰지 않는다.** 마스터 dict가 세션 수 × 이벤트 수만큼 무한
성장하고 축출이 없다(`delete_session`은 있으나 자동 호출 없음). 요청마다 전체 세션을 `deepcopy`한다
**[저장소]**. → `DatabaseSessionService`로 전환하면 성장이 RAM에서 디스크로 옮겨간다. DB 쪽은
`last_update_time` 기준 TTL 삭제 배치를 둔다.
`InMemoryArtifactService`·`InMemoryMemoryService`도 같은 무한 누적이다 — 세션만 DB로 옮기고 이 둘을
남겨 두는 것이 흔한 잔여 원인 **[저장소]**.

**② 1.27.0 이상이면 히스토리 로드 상한을 건다.**

```python
run_config = RunConfig(get_session_config=GetSessionConfig(num_recent_events=50))
```

1.26.0에는 이 경로가 없다 — `DatabaseSessionService`를 서브클래스해 `num_recent_events` 기본값을 강제한다
(DB 서비스는 SQL LIMIT 수준으로 지원) **[저장소]**. 주의: `EventsCompactionConfig`는 **LLM 컨텍스트만
줄이고 저장된 원본 이벤트는 지우지 않는다** — 저장소 성장에는 무효 **[저장소]**.

**③ InMemory를 쓸 수밖에 없으면 (1.29.0+) 가벼운 복사를 검토.**
`ADK_ENABLE_IN_MEMORY_SESSION_SERVICE_LIGHT_COPY=1` 또는 `override_feature_enabled(...)`
(`features/_feature_registry.py`) **[소스]**. 이벤트·state 컨테이너만 얕게 복사해 전체 deepcopy를 피한다.
**WIP 단계라 기본 꺼짐** — 복사본의 이벤트 객체를 제자리 수정하는 코드가 있으면 원본이 바뀐다. 켜기 전에 확인.

**④ 버려진 실행을 직접 막는다.** 1.x의 `/run_sse`는 `Aclosing(runner.run_async(...))` 구조뿐이고
명시적 끊김 감시가 없다(1.26.0 `adk_web_server.py:1605`, 1.39.1 `:2154`) **[소스]**. `/run`(비스트리밍)은
끊겨도 끝까지 돈다. 자체 조립 서버라면 §1-5의 끊김 감시 태스크 + wall-time 상한을 붙인다 — 2.x의
`api_server.py`가 같은 방식이니 그대로 옮겨 올 수 있다.

**⑤ AgentTool 자식의 LLM 호출 상한은 부모 설정으로 막히지 않는다.** 1.x `AgentTool`은
`runner.run_async(user_id=…, session_id=…, new_message=…)`만 넘기고 **`run_config`를 전달하지 않는다**
**[소스: 1.39.1 `tools/agent_tool.py:265`]**. 부모에 `RunConfig(max_llm_calls=50)`을 걸어도 자식은 기본값
**500**으로 돈다. 게다가 호출마다 새 Runner라 카운터가 리셋된다 **[저장소]**.

> 저장소 [OOM 분석](../google-adk/agent-tool/AgentTool_SkillToolset_OOM_원인분석.md) §6의 완화책 ③
> "부모 안전판 `max_llm_calls` 축소"는 **부모만** 제한한다. 자식 폭주는 ①·②(자식 콜백 가드,
> `SafeAgentTool`)로 막아야 하고, `SafeAgentTool`에서 `run_config`를 넘기도록 오버라이드하는 것도 방법이다.

`SkillToolset` 오류 재시도 루프는 1.x 전 버전 무방비(1.39.1까지 확인) — 가드 콜백이 필수 **[저장소+소스]**.

**⑥ MCP 도구셋의 세션 풀이 헤더 조합만큼 불어난다.** `MCPSessionManager`의 세션 키는 **병합된 헤더의
해시**다 (`_generate_session_key` → `json.dumps(merged_headers, sort_keys=True)`) **[소스]**.
`header_provider`로 **사용자별 토큰**을 헤더에 넣으면 사용자 수만큼 MCP 세션(과 그 연결·버퍼)이 생기고,
**1.x에는 만료가 없다.** → 헤더를 사용자별로 바꾸지 않는 구성, 또는 `McpToolset.close()`·`runner.close()`의
주기적 호출. stdio MCP 서버는 자식 프로세스라 Pod 메모리에 합산된다(§1-4).

**⑦ 관측을 붙이되 관측 도구가 누수원이 되지 않게.** RunTrackerPlugin(실행 시작·종료 추적) + 객체 census
**[저장소: 관측 명세]**. 두 가지 함정:
- plugin 레지스트리에 `invocation_context`·`session`·`event` 같은 **대형 객체를 담지 않는다** (과거 logging
  plugin 사고). 스칼라와 task 참조만
- plugin의 `close()`는 **no-op**으로 — AgentTool 자식 Runner가 공유 plugin의 `close()`를 조기 호출한다
- 정지 지점 덤프는 `Task.get_stack()`(최외곽 프레임 1개뿐)이 아니라 **코루틴 객체 gc 열거**로

### 3-3. 2.0 이상 (2.x) — 내장된 것과 여전히 남은 것

**내장된 것** **[소스: 2.10.0]**

| 개선 | 내용 |
|---|---|
| 끊김 시 실행 중단 | `cli/api_server.py:1961·1983` — `/run`은 `request.receive()`로 `http.disconnect`를 기다리는 감시 태스크가 작업 태스크를 취소하고 499 반환. SSE는 Starlette 태스크 그룹 취소 + `Aclosing` |
| MCP 세션 풀 유휴 만료 | `_SESSION_IDLE_TTL_SECONDS = 900.0`. 호출 중인 세션은 스윕에서 제외, 4×TTL 넘게 잡혀 있으면 **로그 경고만** (닫지 않음) |
| AgentTool 부모 설정 상속 | 자식이 부모 `RunConfig`를 물려받음 → 부모의 `max_llm_calls`가 자식에도 적용. **단 카운트는 여전히 invocation 단위** — 자식 상한이 부모와 합산되지는 않는다 (소스 주석 명시) |
| 스킬 루프 방어 | 리소스 오류 재시도 차단, "같은 턴에서 계속" 지침, **스킬 내용이 턴 종료 시 해제**("released at the end of the current turn") — 컨텍스트 누적도 줄어든다 |
| AgentTool 오류 노출 | 자식 오류를 빈 문자열 대신 `error_message`로 반환 → 부모의 빈-결과 재호출 루프 차단 |

**여전히 남은 것 — 1.x 요령 그대로 적용**

- InMemory 세션·아티팩트·메모리 서비스의 **무한 누적, 축출 없음** → §3-2 ①
- 요청당 **전체 세션 deepcopy가 기본** (가벼운 복사는 여전히 WIP·기본 꺼짐) → §3-2 ③
- `max_llm_calls` 기본 500, **AgentTool 호출마다 카운터 새로** → 부모 재호출 × 500이 가능. 부모 instruction에
  "빈 결과·오류면 재호출 금지" + 부모 쪽 상한도 함께
- `get_session_config`를 **설정하지 않으면** 여전히 전체 히스토리 로드 → §3-2 ②
- 끊김 처리는 ADK 제공 서버(`api_server.py`) 기준이다. **자체 FastAPI로 Runner를 조립했다면** 같은 감시
  태스크를 직접 붙여야 한다

**2.x에서 MCP SDK 2.x를 쓸 때**: ADK는 MCP **클라이언트**다. 2장의 서버 쪽 요령은 ADK가 아니라 ADK가
붙는 MCP 서버에 적용된다. ADK 쪽에서 볼 것은 §3-2 ⑥의 세션 풀(2.x는 900초 만료 내장)과 도구 응답 크기다 —
ADK는 MCP 응답 봉투를 `model_dump`해 통째로 function_response로 넘기므로 큰 응답은 그대로 메모리와 토큰이 된다
**[저장소 + 소스: 2.10.0 `mcp_tool.py:147`]**.

### 3-4. ADK 결정 순서

```
1. 세션 서비스: InMemory → Database (아티팩트·메모리 서비스도 함께 점검)
2. 히스토리 상한: RunConfig.get_session_config (1.27.0+) / 서브클래스 (1.26.x)
3. 버려진 실행: 2.x 제공 서버면 내장, 1.x·자체 조립이면 끊김 감시 + wall-time 상한
4. AgentTool: 1.x는 자식 가드 콜백 필수 (부모 max_llm_calls는 자식에 안 닿음)
5. MCP 도구셋: 사용자별 헤더로 세션 풀이 불어나는지 확인 (1.x는 만료 없음)
6. 관측: RunTracker + census, plugin은 대형 객체 비보관·close no-op
7. 그 위에 1장의 allocator·워커 재활용
```

---

## 4. 보정 사항

| 대상 | 기존 서술 | 확인 결과 |
|---|---|---|
| 공유 대화 | python-sdk #756 "현재 수정 여부 확인 못함" | **2025-07-21 종료** |
| 공유 대화 | "FastMCP의 `session_idle_timeout` … None이면 만료 안 됨. 값을 명시하세요" | 공식 SDK 1.27.0의 `FastMCP`는 생성자에 `stateless_http`만 노출 — **FastMCP로는 유휴 만료를 설정할 수 없다** (독립 패키지 `fastmcp`를 가리켰을 가능성). 2.2.0 `MCPServer`에서는 전송 메서드 인자로 설정, 기본 1800초 |
| 공유 대화 | "uvicorn 옵션은 확인 못 함", jitter는 Gunicorn만 언급 | uvicorn 0.54.0에 `--limit-max-requests-jitter`도 있음 |
| 저장소 [SDK 2.0 분석](../mcp/MCP_SDK_2.0_분석.md) §0 | "`stateless_http` 옵션 소멸 — stateless가 **유일 모드**" | 생성자에서 빠졌을 뿐 **전송 메서드로 이동, 기본값 False**. 2026-07-28 프로토콜 요청은 스테이트리스지만 **2025년형 클라이언트는 기본적으로 서버 메모리에 세션이 생긴다** |
| 저장소 [OOM 분석](../google-adk/agent-tool/AgentTool_SkillToolset_OOM_원인분석.md) §6 ③ | 부모 `max_llm_calls` 축소를 안전판으로 제시 | 1.x에서는 **자식에 전달되지 않는다** (§3-2 ⑤) |

## 5. 확인하지 않은 것

| 항목 | 확인 방법 |
|---|---|
| mcp 2.1.x의 유휴 만료·세션 상한 기본값 (2.0.0에는 없고 2.2.0에는 있음) | 2.1.1 설치 후 동일 확인 |
| upstream `6a533573`이 처음 포함된 2.x 버전 (2.10.0 포함만 확인) | 2.0 ~ 2.9 태그 대조 |
| 가벼운 복사 플래그의 실측 효과와 부작용 | 세션 크기별 벤치 + 제자리 수정 코드 탐색 |
| `MALLOC_ARENA_MAX` · jemalloc 효과 | 워크로드별 working set 실측 (공유 대화도 동료 심사 자료 없음을 명시) |
| MCP 레거시 세션 1개당 메모리 | `max_sessions` 산정에 필요. 부하 도구로 세션 N개 생성 후 census |

## 6. 출처

**공유 대화가 인용한 자료** [외부]
- Dask, Worker Memory Management — https://distributed.dask.org/en/stable/worker-memory.html
- NVIDIA FLARE, Memory Management — https://nvflare.readthedocs.io/en/main/programming_guide/memory_management.html
- CPython tracker, glibc arena/trim threshold 분석 — https://bugs.python.org/msg316359
- DEV, Python 메모리 단편화와 jemalloc 사례 — https://dev.to/sondar4/your-memory-leak-might-be-standard-python-behavior-484o
- imgproxy, MALLOC_ARENA_MAX와 jemalloc — https://docs.imgproxy.net/memory_usage_tweaks

**이번 확인** [소스]
- mcp 2.2.0 `server/streamable_http_manager.py`, `server/transport_security.py`, `server/mcpserver` · mcp 2.0.0 · mcp 1.27.0
- google-adk 1.26.0 / 1.39.1 / 2.10.0 — `cli/adk_web_server.py`, `cli/api_server.py`, `sessions/in_memory_session_service.py`,
  `runners.py`, `agents/run_config.py`, `tools/agent_tool.py`, `tools/skill_toolset.py`, `tools/mcp_tool/mcp_session_manager.py`,
  `features/_feature_registry.py`
- upstream `6a533573` — https://github.com/google/adk-python/commit/6a533573
- python-sdk #756 — https://github.com/modelcontextprotocol/python-sdk/issues/756
