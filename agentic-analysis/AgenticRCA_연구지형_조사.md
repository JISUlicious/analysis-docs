# 에이전틱 RCA와 데이터 분석 — 연구·구현 지형 조사

> **조사 목적**: 근본원인분석(RCA)과 데이터 분석을 에이전트로 수행하는 접근이 현재 어디까지 와
> 있는지, 제조 도메인에 적용할 때 무엇을 재사용하고 무엇을 새로 만들어야 하는지 파악한다.
> **조사일**: 2026-09-17 · **방법**: 웹 검색 + 논문/저장소 페이지 직접 확인
> **위치**: RCA는 분석 기법 지형의 한 항목이다. 기법 전반은
> [기법별 적용 지형](AgenticAnalysis_기법별_적용지형.md)을 먼저 보고 여기로 내려올 것.
> **실행 검증**: 제조 센서-불량 원인 탐색을 공개 반도체 데이터로 검증한 결과는
> [`secom-verify/`](secom-verify/README.md) — 원인이 존재할 수 없는 데이터에서 맨몸 에이전트 3/3이 원인을 보고했다.
> 연관: [Agent Skills 작성표준](../agent-skills/Agent_Skills_작성표준.md)

---

## 0. 근거 등급과 한계

이 문서는 **소스 코드를 읽고 실행해 검증한 다른 문서들과 성격이 다르다.** 벤치마크를 직접
돌리거나 저장소를 클론해 확인하지 않았다. 따라서 근거 등급을 아래로 구분한다.

| 표기 | 의미 |
|---|---|
| **[논문]** | 논문 본문/초록에 적힌 수치·주장 |
| **[저장소]** | GitHub 저장소 페이지에 적힌 내용 |
| **[문서]** | 벤더 공식 문서의 아키텍처 기술 |
| **[해석]** | 위를 조합한 나의 판단 |

**미검증 항목 (재현하지 않음)**: 모든 정확도 수치, 스킬 개수, 저장소 규모. 특히 §1-2의
RCA-Agent 정확도는 **출처에 따라 11.34%와 17.9%로 다르게 나온다** — 평가 세팅(서브셋·모델·
채점 방식)이 달라서로 보이지만 확인하지 못했다. 수치를 인용할 때 이 차이를 함께 적을 것.

---

## 1. 결론 먼저

1. **벤치마크 성적이 낮다. RCA는 아직 풀린 문제가 아니다.** OpenRCA에서 전용 RCA 에이전트가
   10~20%대, 범용 에이전트가 36%, 최고 성적 하네스가 59%다 **[논문]**. 프로덕션 기대치와
   상당한 거리가 있고, 이 격차 자체가 설계의 출발점이다.

2. **전용 에이전트를 새로 짜는 방향은 이미 한 번 꺾였다.** OpsHarness(2026)의 핵심 발견은
   **범용 에이전트(Claude Code·Codex)가 처음부터 설계된 전용 RCA 에이전트보다 잘한다**는 것
   (36.1% vs 17.9%) **[논문]**. 병목이 에이전트 아키텍처에서 **외부 지원 계층**으로 옮겼다.
   → 이것이 이 조사에서 가장 중요한 결과다. §7에서 Agent Skills와 연결한다.

3. **범용 에이전트에 없는 것은 추론력이 아니라 시스템 고유 경험이다.** OpsHarness 논문의
   예시: 맨몸 에이전트가 레이턴시 급등을 "절대 증가폭이 가장 큰 네트워크 에러 카운터" 탓으로
   돌리고 진짜 원인(CPU 포화)을 놓쳤다 **[논문]**. 어떤 지표가 부하 상황에서 신뢰할 수 있고
   어떤 게 노이즈인지 — 이 지식은 모델 안에 없고 도메인에 있다.

4. **가장 큰 위험은 오답이 아니라 그럴듯한 오답이다.** 동일 NHANES 데이터로 두 에이전트에
   반대 목표를 주자 한쪽은 유의한 음의 상관(p=0.0006), 다른 쪽은 무관(p=0.855)을 냈고
   **양쪽 다 방법론적으로 방어 가능했다** **[논문]**. RCA는 본질적으로 "원인을 찾아내라"는
   편향된 지시이므로 이 실패 모드에 정면으로 노출된다. §6.

5. **제조 도메인에는 두 개의 분리된 생태계가 있고 사이가 비어 있다.** 데이터 분석 스킬
   저장소(BI 지표 중심)와 품질 방법론 스킬 저장소(8D/FMEA 템플릿)가 각각 존재하는데,
   **공정 데이터를 실제로 분석하는 제조 RCA 스킬은 찾지 못했다** **[해석]**. §5.

---

## 2. 벤치마크 — 현재 수준의 실측선

### 2-1. OpenRCA (ICLR'25, Microsoft, MIT) **[저장소]**

소프트웨어 장애 RCA 벤치마크. 이 분야에서 가장 많이 인용되는 기준선.

| 항목 | 내용 |
|---|---|
| 규모 | 실제 장애 **335건** |
| 대상 시스템 | Telecom · Bank · Market (cloudbed 서비스군 2종) |
| 텔레메트리 | **KPI 시계열 + 의존성 트레이스 그래프 + 반정형 로그 텍스트** 3종 |
| 태스크 | 자연어 질의 → **발생시각(UTC+8) · 구성요소 · 장애 원인** 예측. 복수 장애면 시간순 나열 |
| 채점 | 정답 레코드와 JSON 필드 대조, CLI로 표준화 |

**베이스라인 RCA-Agent의 설계가 시사적이다**: 텔레메트리를 컨텍스트에 넣지 않고 **Python을
써서 조회·분석**하게 한다. 원문: *"모델이 추론에 집중하게 하고 대용량 텔레메트리에 확장
가능하게"* **[저장소]**. 과도하게 긴 컨텍스트 처리를 피하는 구조 — 제조 공정 데이터처럼
수백만 행 규모에도 그대로 적용되는 원칙이다.

저장소 FAQ가 밝히는 실패 모드 **[저장소]**:
- **네트워크 장애는 KPI만으로 식별 불가** — 트레이스 분석이 필수
- UTC+8 데이터와 로컬 처리 간 **타임존 불일치**로 타임스탬프가 어긋남
- 텔레메트리 **샘플링 주기** 제약을 모델이 다루기 어려움

> 세 번째와 두 번째는 제조 데이터에서도 그대로 재현될 항목이다 (설비 시각 동기화, 센서
> 샘플링 레이트 혼재). **[해석]**

### 2-2. RCAEval (FSE'26 / WWW'25 / ASE'24) **[논문][저장소]**

마이크로서비스 RCA 벤치마크. OpenRCA보다 규모가 크고 baseline이 많다.

| 항목 | 내용 |
|---|---|
| 규모 | 장애 **735건**, 3개 시스템(Online Boutique · Sock Shop · Train Ticket) |
| 결함 유형 | **11종** |
| 데이터셋 | 9개 (RE1/RE2/RE3 3개 스위트) |
| 데이터량 | 케이스당 메트릭 77~327개 · 로그 860만~2,690만 줄 · 트레이스 3,960만~7,670만 건 |
| 베이스라인 | **재현 가능한 15종** (메트릭 기반·트레이스 기반·멀티소스) |
| 지표 | **AC@1, AC@3, Avg@5** |

**AC@k(상위 k 안에 정답 포함)를 쓴다는 점이 실무적으로 중요하다** — RCA는 단일 정답을
맞히는 문제가 아니라 **후보를 좁히는 문제**로 평가된다. 제조 RCA 스킬의 성공 기준을 정할 때
그대로 빌려올 프레이밍이다. **[해석]**

### 2-3. 데이터 분석 쪽 벤치마크

| 벤치마크 | 규모 | 특징 |
|---|---|---|
| **InfiAgent-DABench** (ICML'24) | CSV 124개에서 뽑은 질문 **603개**(DAEval), **LLM 34종** 평가 | 개방형 질문을 **format-prompting으로 폐쇄형 변환**해 자동 채점. 전용 DAAgent가 GPT-3.5 대비 +3.9% |
| **DA-Code** | 태스크 **500개** | 데이터 랭글링→EDA→ML까지 파이프라인 전 단계 |
| **DataSciBench** | — | 데이터 사이언스 에이전트 벤치마크 |

**개방형 분석 질문을 자동 채점하는 문제**가 이 분야의 공통 난점이고, InfiAgent의 해법은
"폐쇄형으로 변환"이다 **[논문]**. 제조 RCA 스킬을 평가할 때도 같은 문제에 부딪힌다 —
"원인이 무엇인가"는 개방형이지만 "원인 설비 ID는 무엇인가"는 폐쇄형이다. **[해석]**

---

## 3. 에이전트 아키텍처 계보

### 3-1. ReAct 계열 — 정확도는 안 오르지만 환각이 준다

Microsoft의 [Exploring LLM-based Agents for RCA](https://arxiv.org/html/2403.04123v1) **[논문]**:

- 데이터: 사내 인시던트 **10.7만건**(2020-01~2021-09). 평가 100건 / 테스트 500건 샘플,
  나머지 10.2만건은 검색 코퍼스
- 비교: ReAct vs RAG · CoT · IR-CoT

| 지표 | 결과 |
|---|---|
| 정확도 | ReAct **35%** vs 베이스라인 39% — **더 낮다** |
| 어휘·의미 유사도 | 거의 차이 없음 |
| **환각률** | **ReAct 4% · CoT 12% · 검색기반 40%** |
| ReAct 오답의 성격 | **66%가 "증거 불충분"** |
| 제약 | 20 이터레이션 한계 초과하는 복잡 인시던트에서 실패 |

**이 표가 말하는 것**: ReAct는 더 잘 맞히지 않는다. 대신 **틀릴 때 지어내지 않고 모른다고
한다.** RCA에서는 이쪽이 훨씬 중요한 속성이다 — 틀린 근본원인은 잘못된 시정조치로 직결되고,
제조에서는 그 비용이 라인 정지나 불량 재발이다. **[해석]**

부수 발견: 과거 인시던트의 **토론 코멘트를 넣어도 성능이 거의 안 올랐다**(가설과 반대)
**[논문]**.

**결론**: 저자들은 파인튜닝을 하지 않았고, 제로샷 ReAct가 강한 베이스라인과 경쟁 가능하다고
봤다. 다만 실전 배포에는 **전용 도구와 human-in-the-loop가 필요**하다고 명시 **[논문]**.

### 3-2. 코드 실행형 — 컨텍스트 대신 Python

OpenRCA의 RCA-Agent(§2-1), Alibaba의 **RCAgent**(CIKM'24)가 여기 속한다.
RCAgent는 **15스텝 내 99.38% pass rate, invalid rate 7.93%**를 보고한다 **[논문]**.
*"privacy-aware industrial usage"*를 내세운 점이 특징 — 사내 데이터를 외부 모델에 통째로
넘기지 않는 구조가 산업 적용의 전제라는 인식이다.

> pass rate는 "정답률"이 아니라 "유효한 결과를 산출한 비율"에 가깝다. 정확도와 혼동하지 말 것.
> **[해석]**

### 3-3. 멀티에이전트 분할

로그·메트릭·트레이스·검증을 각각 전담 에이전트에 나누고 슈퍼바이저가 조율하는 구성이
보고된다 **[문서]**. 제조로 옮기면 센서 시계열 / 검사 이미지 / 이력(lot·설비·레시피) /
검증으로 나뉠 자연스러운 경계가 있다. **[해석]**

다만 멀티에이전트는 자체 실패 모드를 만든다 — 14종 실패 모드 taxonomy가 **System Design
Issues · Inter-Agent Misalignment · Task Verification Failures** 세 범주로 정리되어 있고,
**Premature Action(근거 없이 행동) · Over-Helpfulness(빠진 항목을 지어서 채움) ·
Context Pollution · Fragile Execution** 네 원형이 반복 관찰된다 **[논문]**.

### 3-4. 하네스 + 자기진화 — 가장 중요한 갈래

**[OpsHarness (2026)](https://arxiv.org/html/2608.25661)** **[논문]**. 범용 에이전트를
**밖에서 감싸서** RCA 전문가로 만든다. 에이전트를 새로 만들지 않는다.

```
Data Plane
├─ 계층화된 운영 지식 𝒦 (4층)
│   ① 일반 RCA 프로세스 지식
│   ② 시스템 프로파일
│   ③ 마이닝된 진단 워크플로
│   └ ④ 세부 조작·규칙
└─ idea-card 도구 라이브러리 𝒯
    = 자연어로 쓴 알고리즘 명세. 에이전트가 살아있는 데이터 스키마에 맞춰 구현한다

Control Plane
└─ setup → diagnose → evolve → verify  (evolve↔verify가 자기진화 루프)
```

**자기진화 3단계**:
1. **Trajectory mining** — 사용자 피드백으로 진단을 정답/오답 라벨링
2. **Evidence mining** — 성공 궤적에서 반복 패턴, 실패에서 대조적 교정 추출
3. **Staged dual-gate verification** — 제안이 ① 원본 케이스에서 **엄격한 개선**,
   ② held-out 테스트베드에서 **비퇴행**을 둘 다 통과해야 채택

**dual-gate가 없으면 약한 모델에서 정확도가 붕괴했다** — 가짜 패턴이 쌓여서 **[논문]**.
경험 축적형 시스템의 핵심 안전장치.

**성적** (OpenRCA + RCAEval 합산, 4개 모델 평균) **[논문]**:

| 구성 | top-1 정확도 |
|---|---|
| 전용 RCA-Agent | 17.9% |
| 맨몸 범용 에이전트 | 36.1% |
| **OpsHarness** | **59.0%** (범용 대비 +63.4%, 전용 대비 4.02×) |

논문의 결론 문장이 이 조사 전체의 요지다: 범용 에이전트가 성숙하면서 **병목이 에이전트
아키텍처에서 외부 지원 계층으로 이동**했고, 하네스는 범용 기능을 재구현할 게 아니라
*"범용 능력 바깥에 있는 진단 전용 인프라"*를 공급해야 한다 **[논문]**.

### 3-5. 뉴로심볼릭 / 인과 — 상관과 인과를 가르려는 갈래

| 시스템 | 접근 |
|---|---|
| **CausalTrace** ([2510.12033](https://arxiv.org/pdf/2510.12033)) | **스마트 제조 전용** 뉴로심볼릭 인과 분석 에이전트. 인과 발견 엔진 + 신경망(센서·운영 데이터) + 심볼릭 추론 + 에이전트 프레임워크. 도구: 데이터 조회 · 인과 추론 · **반사실 시뮬레이션** · 지식 표현 · 시정조치 생성 **[논문]** |
| **Causal-LLM** | 제약 기반 인과 발견으로 인과 그래프를 만들고, LLM은 **해석 가능한 설명 생성**을 맡는 하이브리드 **[논문]** |
| 인과발견 기반 RCA ([2411.06990](https://arxiv.org/html/2411.06990v2)) | 사전 정의 인과 그래프 없이 관계 추정. 외생 노이즈를 랜덤화한 **반사실 예측**을 실제 이상 예측과 비교 **[논문]** |
| 시간의존 반사실 RCA ([2407.11056](https://arxiv.org/pdf/2407.11056)) | 다변량 시계열에서 **발단 고장 시점(point of incipient failure)** 탐지 **[논문]** |

**역할 분담이 일관되게 나타난다**: 인과 구조는 통계적 방법이 찾고, LLM은 **맥락화·설명·
조율**을 맡는다. LLM에게 인과 판단 자체를 맡기지 않는다. **[해석]** — 이것은 §6의 검증
격차 문제에 대한 직접적인 방어책이기도 하다.

---

## 4. 제조 도메인 실제 구현 — 참조 아키텍처

가장 구체적으로 공개된 제조 RCA 에이전트 청사진은 MongoDB의
[Agentic Yield Analytics](https://www.mongodb.com/docs/atlas/architecture/current/solutions-library/agentic-yield-analytics/)
**[문서]**. 반도체 팹 수율 이탈(excursion) 대응이 대상이다.

### 4-1. 탐지와 분석을 분리한다

```
센서 텔레메트리 → sensor_events 컬렉션
      → Change Streams 감시 → 임계 위반 탐지 → Alert 생성 (밀리초)
      → ★여기서부터 Root Cause Agent 기동 (ReAct)
```

**탐지는 스트림 기반의 반응형, 에이전트는 알럿 트리거형**이다. 에이전트가 상시 감시하지
않는다 — 비용·지연·안정성 모두에서 합리적인 분리다. **[해석]**

### 4-2. 에이전트는 1개, 도구는 4개

| 도구 | 하는 일 |
|---|---|
| `query_alerts` | 설비·심각도·시간창으로 알럿 조회 |
| `query_wafer_info` | 웨이퍼 결함 상세(수율%, 결함 패턴, 심각도) + **멀티모달 벡터 검색으로 유사 과거 결함**(기본 상위 3건) |
| `query_time_series_data` | 시간창 주변 센서 텔레메트리. **집계 통계(min/max/avg)로 반환해 토큰 절감** |
| `vector_search_knowledge_base` | 과거 RCA 리포트·기술문서 시맨틱 검색 |

**`query_time_series_data`가 원시 데이터가 아니라 집계를 반환한다**는 점이 설계 포인트다.
§2-1의 "텔레메트리를 컨텍스트에 넣지 않는다" 원칙과 같은 계열. **[해석]**

### 4-3. 멀티모달 — 결함 이미지와 텍스트를 한 벡터로

웨이퍼 결함마다 관측 사실로 텍스트를 만들고(`Wafer ID / Defect pattern / Equipment / Yield`),
잉크맵 이미지를 S3에서 가져와 **voyage-multimodal-3로 1024차원 단일 임베딩** 생성 **[문서]**.

> 효과: 엔지니어가 **원래 어떻게 기술됐든 상관없이** 유사 결함을 찾을 수 있다. 시각 패턴
> 매칭과 시맨틱 텍스트 매칭이 한 번의 검색으로 합쳐진다 **[문서]**.

### 4-4. 메모리와 감사 추적

- 단기: LangGraph `MongoDBSaver`로 추론 스텝마다 `checkpoints`에 상태 저장
- 장기: 벡터 검색으로 과거 결함·지식베이스 조회
- 효과: **중단된 조사 재개 · 추론 체인 감사 · 사용자 상호작용 간 컨텍스트 유지** **[문서]**

**감사 추적이 기능 목록에 들어가 있다는 점**이 제조 도메인의 특징이다. 품질 조사는 결론만이
아니라 **어떻게 그 결론에 도달했는지가 기록으로 남아야** 한다 (8D 리포트가 그런 문서다).
**[해석]**

### 4-5. 산업 맥락 수치 **[문서]**

- 팹 계획외 정지: **시간당 최대 $1M**
- 목표: 조사 시간을 **시간 단위 → 초 단위**로

> 이 수치가 설계에 주는 함의는 속도보다 **오답 비용**이다. 시간당 $1M짜리 라인에서 잘못된
> 근본원인은 잘못된 설비를 세우게 만든다. §6의 검증 규율이 선택이 아닌 이유. **[해석]**

---

## 5. 스킬 생태계 — 두 개의 섬과 그 사이의 빈칸

### 5-1. 데이터 분석 스킬 저장소

| 저장소 | 내용 |
|---|---|
| [nimrodfisher/data-analytics-skills](https://github.com/nimrodfisher/data-analytics-skills) | **31개 스킬 / 6 카테고리**(데이터품질·문서화·분석조사·스토리텔링·이해관계자소통·워크플로). **`root-cause-investigation` 스킬 보유 — 단, "예상치 못한 지표 변화의 구조적 진단"** = BI 지표용 **[저장소]** |
| [anthropics/skills](https://github.com/anthropics/skills) | 공식 스킬 저장소 |
| [alirezarezvani/claude-skills](https://github.com/alirezarezvani/claude-skills) | 380+ 스킬 표방, 다분야 |
| [BehiSecc/awesome-claude-skills](https://github.com/BehiSecc/awesome-claude-skills) | 큐레이션 인덱스 |

**제조·품질 스킬은 없다** **[저장소]**.

### 5-2. 제조 품질 방법론 스킬 저장소

| 저장소 | 내용 |
|---|---|
| [ddunnock/claude-plugins](https://github.com/ddunnock/claude-plugins) | MIT · 521 커밋 · 13 stars. **RCA 스킬 8개 체인**: `rcca-master`(8D 오케스트레이터) → `problem-definition`(5W2H, IS/IS-NOT) → `five-whys-analysis` · `fishbone-diagram`(6M/8P/4S) · `pareto-analysis` · `kepner-tregoe-analysis` · `fault-tree-analysis` · `fmea-analysis`(AIAG-VDA DFMEA/PFMEA). 표준 `SKILL.md` + `scripts/` + `references/` + `assets/` **[저장소]** |
| [jherrodthomas/automotive-skills-suite](https://github.com/jherrodthomas/automotive-skills-suite) | MIT · **152개 = 76 builder/reviewer 쌍**. ISO 26262 · ISO/SAE 21434 · AIAG-VDA(APQP/PPAP/FMEA) · ASPICE + 8D · 5-Why · fishbone · **MSA Gauge R&R** · **SPC(Cpk)** **[저장소]** |
| [awesome-skills/5-whys-skill](https://github.com/awesome-skills/5-whys-skill) | MIT · 약 1,170 단어 · 5단계. TPS 유래지만 **예시가 API 레이턴시·배포 이슈 = 범용** **[저장소]** |
| [TuringWorks/civstack](https://skills.lc/TuringWorks/civstack/turingworks-civstack-skills-09-manufacturing-and-industrial-production-roles-root-cause-analysis-agent-skill-md) | RCA **역할 정의**(책임 경계 중심). *"안전·비용·정당성이 걸리면 결정하지 말고 권고한다"* **[저장소]** |

### 5-3. automotive-skills-suite에서 빌려올 두 가지 **[저장소]**

이 저장소를 쓰지 않더라도 설계 아이디어 두 개는 가져올 가치가 있다.

1. **builder + reviewer 쌍** — 산출물을 만드는 스킬마다 **확인 체크리스트를 돌리고
   정량 임계값을 자동 검증**하는 리뷰어 스킬이 짝으로 있다.
2. **xlsx를 스킬 간 안정 계약으로** — *"모든 하류 스킬이 상류 스킬의 xlsx 출력을 소비한다."*
   HARA → FSC → TSC → FMEDA → Safety Case가 이 계약으로 연결되고, 상류 변경이 자동 전파된다.

> 1번은 §6의 검증 격차에 대한 **스킬 레벨 대응**이다. 리뷰어를 별도 스킬로 분리하면
> "만든 사람이 검사하지 않는다"가 구조로 강제된다. **[해석]**

### 5-4. 빈칸

두 섬 사이가 비어 있다 **[해석]**:

| | 데이터 분석 스킬 | 품질 방법론 스킬 | 필요한 것 |
|---|---|---|---|
| 도메인 모델 | 없음 | 있음 | 있음 |
| 실제 데이터 처리 | 있음 | **없음** | 있음 |
| 산출물 | 분석 리포트 | 8D/FMEA 문서 | **둘 다** |

품질 방법론 스킬들은 **퍼실리테이션 템플릿**이다 — 사람을 피시본이나 8D로 안내하고 문서를
만든다. 공정 데이터를 건드리지 않는다: 실제 시계열에서 SPC 위반을 잡거나, lot·설비·챔버
공통성 분석을 하거나, 파라미터-불량 상관을 보거나, 웨이퍼맵 패턴을 분류하는 스킬은 없다.
웨이퍼맵·공정 파라미터 상관 스킬을 별도로 검색했으나 **CNN 논문만 나오고 스킬은 없었다.**

---

## 6. 가장 큰 위험 — 검증 격차

[Sound Agentic Science Requires Adversarial Experiments](https://arxiv.org/html/2604.22080v1)
**[논문]**가 이 문제를 가장 선명하게 제시한다.

### 6-1. 시연

동일한 NHANES 데이터를 두 에이전트에 주고 **반대 목표**를 부여했다:

| | 결과 |
|---|---|
| Agent A | 비타민 D ↔ 우울증 **유의한 음의 상관, p=0.0006** |
| Agent B | **연관 없음, p=0.855** |

**양쪽 모두 방어 가능한 방법론적 선택을 사용했다** **[논문]**.

### 6-2. 왜 RCA에 특히 위험한가 **[해석]**

소프트웨어 개발에서는 코드가 명세에 대해 상시 테스트된다. 데이터 분석 에이전트에는 그런
반증 장치가 없다 — 논문의 표현으로 **"verification gap"**. 에이전트는 *"데이터가 지지하는
것처럼 보이는 가설을 빠르게 섞을 수" 있지만 경쟁 가설을 배제할 실험을 돌릴 수 없다* **[논문]**.

그리고 **RCA 지시는 구조적으로 편향되어 있다.** "이 불량의 근본원인을 찾아라"는 Agent A에게
준 지시와 형태가 같다. 원인을 찾으라고 하면 찾아온다. 공정 데이터는 변수가 수백~수천 개라
상관은 항상 존재한다.

§4-5의 수치와 합치면: 시간당 $1M 라인에서 **그럴듯한 오답이 진짜 위험**이다.

### 6-3. 대응 — falsification-first **[논문]**

논문이 제안하는 표준:
- 모든 결과에 **결론을 깨려고 시도한 흔적(adversarial trial)**을 함께 싣는다
- 실행 가능한 분석 패키지를 함께 제출
- 리뷰어가 **에이전트를 반증 시도에 사용**

이미 조사에서 만난 다른 대응들과 계열이 같다:

| 출처 | 대응 |
|---|---|
| Microsoft ReAct 연구 | 오답의 66%를 **"증거 불충분"으로 내게** 만든 도구 기반 접지 (§3-1) |
| OpsHarness | **dual-gate 검증** — 개선 + 비퇴행 둘 다 통과해야 지식 채택 (§3-4) |
| CausalTrace / Causal-LLM | 인과 판단을 **LLM 밖의 통계적 방법**에 맡김 (§3-5) |
| automotive-skills-suite | **reviewer 스킬을 builder와 분리** (§5-3) |
| VerifyMAS | 직접 예측 대신 **가설을 세우고 전체 궤적에 대해 검증** **[논문]** |

**공통 구조: 생성과 검증을 다른 주체에 둔다.** **[해석]**

---

## 7. 설계 함의 — 제조 RCA를 만든다면

§1-2(범용 에이전트가 전용보다 낫다)와 §3-4(병목은 외부 지원 계층)를 합치면 방향이 나온다.

### 7-1. 만들 것은 에이전트가 아니라 하네스다 **[해석]**

OpsHarness의 4층 지식 구조를 제조로 옮기면:

| OpsHarness 층 | 제조 대응 | Agent Skills 배치 |
|---|---|---|
| ① 일반 RCA 프로세스 지식 | 8D·5Why·피시본·FTA 절차 | `SKILL.md` 본문 (L2) |
| ② 시스템 프로파일 | 라인·설비·공정 흐름, 계측 정의, 어떤 지표가 언제 신뢰 가능한가 | `references/` (L3) |
| ③ 마이닝된 진단 워크플로 | 과거 불량 유형별 조사 경로 | `references/` (L3) |
| ④ 세부 조작·규칙 | SPC 룰, 공통성 분석 쿼리, 타임존·샘플링 보정 | `scripts/` (L3, **출력만 토큰**) |

[Agent Skills 3단계 로딩](../agent-skills/Agent_Skills_작성표준.md#2-3단계-점진적-공개--이-구조가-모든-규칙의-근거다)과
정확히 맞물린다. ②③은 커지므로 `references/`로, ④는 결정적이므로 `scripts/`로 — 표준 §7-3의
"실행이 기본" 원칙 그대로. **[해석]**

OpsHarness의 **idea-card**(자연어 알고리즘 명세를 에이전트가 살아있는 스키마에 맞춰 구현)는
스킬 본문에 절차를 쓰고 실제 쿼리는 에이전트가 짜게 하는 방식과 같다 — 스키마가 사이트마다
다른 제조 환경에 특히 맞는다. **[해석]**

### 7-2. 구조 제안

```
탐지(비-에이전트, 스트림/배치)  →  알럿  →  [에이전트 조사]  →  [검증 스킬]  →  8D 산출물
   §4-1 분리 원칙                                 ↑ 생성과 검증 분리 §6-3
```

- **데이터는 컨텍스트에 넣지 않는다** — 스크립트로 조회하고 집계만 반환 (§2-1, §4-2)
- **인과 판단을 LLM에 맡기지 않는다** — 통계/인과 발견은 `scripts/`, LLM은 맥락화·설명 (§3-5)
- **평가는 AC@k로** — 단일 정답이 아니라 후보 축소로 (§2-2)
- **감사 추적을 산출물에 포함** — 품질 조사의 요건 (§4-4)
- **책임 경계를 명시** — civstack의 "안전·비용이 걸리면 권고까지" (§5-2)

### 7-3. 기대치를 낮게 잡을 것 **[해석]**

소프트웨어 RCA에서 최고 하네스가 59%다. 제조는 도메인 지식 의존도가 더 높고 공개
벤치마크도 없다. **"근본원인을 자동으로 찾는다"가 아니라 "조사 시간을 줄이고 후보를 좁히며
증거를 정리한다"**가 현실적인 성공 정의다.

---

## 8. 미확정 — 추가 조사가 필요한 것

| # | 항목 | 확인 방법 |
|---|---|---|
| 1 | RCA-Agent 정확도 11.34% vs 17.9% 불일치 (§0) | 두 논문의 평가 세팅 대조 |
| 2 | 스킬 저장소들의 실제 품질 — 스킬 개수는 저장소 페이지 주장 | 클론 후 [`validate_skill.py`](../agent-skills/validate_skill.py) 실행, P0/P1 등급 판정 |
| 3 | CausalTrace 코드·데이터 공개 여부 | 저자 저장소 확인 (논문 PDF에서 확인 실패) |
| 4 | 제조 도메인 공개 RCA 벤치마크 존재 여부 | 미발견 — 없다면 자체 평가셋 설계 필요 (§2-3의 폐쇄형 변환 기법 적용) |
| 5 | OpsHarness 코드 공개 여부 | 저장소 확인 |

---

## 9. 출처

**벤치마크**
- [microsoft/OpenRCA](https://github.com/microsoft/OpenRCA) (ICLR'25, MIT)
- [phamquiluan/RCAEval](https://github.com/phamquiluan/RCAEval) · [논문](https://arxiv.org/abs/2412.17015) (FSE'26/WWW'25/ASE'24)
- [InfiAgent-DABench](https://arxiv.org/abs/2401.05507) (ICML'24)
- [DataSciBench](https://arxiv.org/html/2502.13897v1)

**에이전트 아키텍처**
- [Exploring LLM-based Agents for Root Cause Analysis](https://arxiv.org/html/2403.04123v1) (Microsoft)
- [RCAgent: Cloud Root Cause Analysis by Autonomous Agents](https://arxiv.org/pdf/2310.16340) (CIKM'24)
- [From General Agents to RCA Experts: A Self-Evolving Harness (OpsHarness)](https://arxiv.org/html/2608.25661)
- [DatawiseAgent: A Notebook-Centric LLM Agent Framework](https://arxiv.org/pdf/2503.07044)
- [EvoDS: Self-Evolving Autonomous Data Science Agent](https://arxiv.org/pdf/2606.03841)
- [awesome-LLM-AIOps](https://github.com/Jun-jie-Huang/awesome-LLM-AIOps) — 분야 인덱스

**인과 / 제조**
- [CausalTrace: A Neurosymbolic Causal Analysis Agent for Smart Manufacturing](https://arxiv.org/pdf/2510.12033)
- [Causal-discovery-based root-cause analysis](https://arxiv.org/html/2411.06990v2)
- [Industrial-Grade Time-Dependent Counterfactual RCA](https://arxiv.org/pdf/2407.11056)
- [Intelligent Assistants for Semiconductor Failure Analysis with LLM-Based Planning Agents](https://arxiv.org/pdf/2506.15567)
- [Wafer Defect Root Cause Analysis with Partial Trajectory Regression](https://arxiv.org/pdf/2507.20357)
- [Cross-Process Defect Attribution using Potential Loss Analysis](https://arxiv.org/pdf/2508.00895)
- [ML methods for RCA towards zero-defect manufacturing (리뷰)](https://www.frontiersin.org/journals/manufacturing-technology/articles/10.3389/fmtec.2022.972712/full)
- [MongoDB Agentic Yield Analytics 참조 아키텍처](https://www.mongodb.com/docs/atlas/architecture/current/solutions-library/agentic-yield-analytics/)

**검증 / 실패 모드**
- [Sound Agentic Science Requires Adversarial Experiments](https://arxiv.org/html/2604.22080v1)
- [VerifyMAS: Hypothesis Verification for Failure Attribution](https://arxiv.org/abs/2605.17467)
- [How Do LLMs Fail In Agentic Scenarios?](https://arxiv.org/pdf/2512.07497)

**스킬 저장소**
- [nimrodfisher/data-analytics-skills](https://github.com/nimrodfisher/data-analytics-skills)
- [ddunnock/claude-plugins](https://github.com/ddunnock/claude-plugins)
- [jherrodthomas/automotive-skills-suite](https://github.com/jherrodthomas/automotive-skills-suite)
- [awesome-skills/5-whys-skill](https://github.com/awesome-skills/5-whys-skill)
- [anthropics/skills](https://github.com/anthropics/skills) · [awesome-claude-skills](https://github.com/BehiSecc/awesome-claude-skills)
