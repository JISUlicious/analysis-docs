# Agent Skills 작성 표준

> 공식 문서 3종 + Anthropic이 실제로 배포한 스킬 40개 실측을 근거로 정리한 작성 표준.
> 조사 기준일: **2026-09-04**

---

## 0. 이 문서의 근거

### 0.1 1차 출처

| # | 출처 | 역할 |
|---|---|---|
| D1 | [Claude Code — Extend Claude with skills](https://code.claude.com/docs/en/skills) | Claude Code의 전체 프론트매터·탐색·수명주기 규격 |
| D2 | [Agent Skills — Skill authoring best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices) | 작성 원칙(자유도, 점진적 공개, 안티패턴, 체크리스트) |
| D3 | [Agent Skills — Overview](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview) | 3단계 로딩 구조, 필드 검증 규칙, 서피스별 제약 |
| D4 | [Using Agent Skills with the API](https://platform.claude.com/docs/en/build-with-claude/skills-guide) | 업로드 한계(30MB), 버전 규칙 |
| D5 | `skill-creator` 스킬 (Apache-2.0, `anthropics/skills`) | 제작 프로세스, 평가 루프, **`scripts/quick_validate.py`의 실행 가능한 검증 규칙** |

> [agentskills.io](https://agentskills.io) 개방 표준 원문은 이 환경의 이그레스 프록시에서 차단되어 직접 확인하지 못했다.
> 표준이 정의하는 필드 집합은 D1의 "Using skill frontmatter outside Claude Code" 표와 D5의
> `ALLOWED_PROPERTIES`로 **간접 확인**했고, 두 출처가 정확히 일치한다.

### 0.2 실측 표본

`/mnt/skills/public/` 8개 + `/mnt/skills/examples/` 32개 = **스킬 40개**의 `SKILL.md`를 파싱한 결과.

| 지표 | 최소 | 중앙값 | 평균 | 최대 |
|---|---:|---:|---:|---:|
| `description` 길이(자) | 139 | **308** | 430 | 1,003 |
| `SKILL.md` 줄 수 | 30 | **80** | 139 | 485 |

- `description`이 1,024자를 넘는 스킬: **0개** (최장 `docx` 1,003자 — 상한의 98%까지 씀)
- `SKILL.md`가 500줄을 넘는 스킬: **0개** (최장 `skill-creator` 485줄)
- 40개 전부 `name`과 `description`을 명시 (둘 다 "선택"인 Claude Code에서도)

→ **문서가 권고하는 500줄·1,024자 선은 실제 배포물에서 예외 없이 지켜지고 있다.** 이 문서는
그 선을 "가이드라인"이 아니라 표준으로 취급한다.

### 0.3 표기

**[규격]** 위반 시 로드/업로드가 실패하는 하드 제약 · **[표준]** 공식 문서가 권고하고 실측이 뒷받침하는 규칙 · **[관행]** 실측에서 관찰된 패턴

---

## 1. 먼저: 세 개의 규격이 겹쳐 있다

Agent Skills를 다룰 때 가장 흔한 사고는 **"Claude Code에서 잘 되던 스킬이 업로드에서 하드 에러로
거부되는 것"**이다. 필드 집합이 서로 다르기 때문이다.

```
┌─ Agent Skills 개방 표준 (agentskills.io) ─────────────┐
│  name  description  license  compatibility            │
│  metadata  allowed-tools          ← 6개가 전부         │
│                                                       │
│  ┌─ Claude Code 확장 ──────────────────────────────┐  │
│  │ when_to_use  argument-hint  arguments  model     │  │
│  │ effort  context  agent  background  hooks        │  │
│  │ paths  shell  disable-model-invocation           │  │
│  │ user-invocable  disallowed-tools                 │  │
│  └──────────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────────┘

배포 경로별 허용 범위
  Claude Code (개인/프로젝트/플러그인) → 위 전부 허용
  claude.ai 업로드 · Skills API · package_skill.py → 바깥쪽 6개만
```

**[규격]** 스펙 밖 필드가 하나라도 있으면 업로드/패키징이 **무시가 아니라 하드 에러**로 실패한다.
D1이 인용하는 실제 에러 메시지:

```
Unexpected key(s) in SKILL.md frontmatter: argument-hint.
Allowed properties are: allowed-tools, compatibility, description, license, metadata, name
```

### 1.1 이식성 등급을 먼저 정하라 **[표준]**

스킬을 쓰기 전에 어느 등급으로 만들지 정하고, 그 등급을 `metadata`나 README에 적어 둔다.

| 등급 | 프론트매터 | 본문 기능 | 도달 범위 |
|---|---|---|---|
| **P0 — 이식형** | 스펙 6필드만 | 순수 마크다운 + 번들 파일 | Claude Code · claude.ai · API · Cowork · 클라우드 세션 전부 |
| **P1 — Claude Code 전용** | 확장 필드 사용 | `` !`cmd` `` 주입, `$ARGUMENTS`, `${CLAUDE_*}` | Claude Code(로컬/플러그인)만 |

**P0를 기본값으로 삼는다.** P1 기능은 그것이 없으면 스킬이 성립하지 않을 때만 쓴다.
D1은 이 점을 명시한다 — *"Claude Code-only body features, such as dynamic context injection,
don't function in claude.ai chat or through the API."* 게다가 claude.ai에서 동기화된 스킬은
로컬 세션에서 `!` 명령이 실행되지 않고 **리터럴 텍스트로 전달**된다. 조용히 깨지는 쪽이다.

---

## 2. 3단계 점진적 공개 — 이 구조가 모든 규칙의 근거다

D3이 정의하는 로딩 단계. **작성 규칙의 거의 전부가 여기서 파생된다.**

| 단계 | 무엇이 | 언제 로드 | 토큰 비용 |
|---|---|---|---|
| **L1 메타데이터** | `name` + `description` | **항상** (세션 시작 시 시스템 프롬프트에) | 스킬당 ~100 토큰 |
| **L2 본문** | `SKILL.md` 마크다운 | 스킬이 트리거될 때 | 5k 토큰 미만 목표 |
| **L3 번들 리소스** | `references/`, `scripts/`, `assets/` | 실제로 읽거나 실행할 때 | **읽기 전엔 0** |

여기서 나오는 세 가지 귀결:

1. **`description`은 항상 켜져 있는 비용이다.** 스킬 100개를 깔면 100개의 description이 매 세션
   시스템 프롬프트에 있다. 그래서 길이 상한이 있고, 그래서 이 필드가 트리거의 전부다.
2. **`SKILL.md` 본문은 트리거된 순간부터 대화 끝까지 남는다** (§8.1). 그래서 500줄 제한이 있다.
3. **번들 리소스는 사실상 무료다.** D3: *"There's no context penalty for bundled content that
   isn't used."* → 큰 참조 문서를 SKILL.md에 우겨넣는 것은 순손실이고, 파일로 빼는 것은 순이익이다.
   **스크립트는 실행하면 코드가 컨텍스트에 안 들어가고 출력만 들어간다.**

> D2의 표현: *"The context window is a public good."* 스킬은 시스템 프롬프트, 대화 기록,
> 다른 스킬의 메타데이터, 그리고 사용자의 실제 요청과 같은 창을 나눠 쓴다.

---

## 3. 디렉터리 표준

### 3.1 표준 레이아웃 **[표준]**

```
skill-name/
├── SKILL.md          # 필수. 정확히 1개, 루트에.
├── LICENSE.txt       # 배포한다면 권장
├── references/       # 필요할 때 컨텍스트로 읽히는 문서
│   ├── domain-a.md
│   └── domain-b.md
├── scripts/          # 실행되는 코드 (컨텍스트에 안 들어감)
│   └── validate.py
├── assets/           # 산출물에 쓰이는 파일 (템플릿·폰트·아이콘)
│   └── template.html
└── evals/            # 평가. 패키징에서 제외됨.
    ├── evals.json
    └── files/
```

디렉터리 이름은 D5의 `skill-creator`가 정의하는 3분류를 그대로 쓴다:

| 디렉터리 | 용도 | 판별 기준 |
|---|---|---|
| `scripts/` | 결정적·반복적 작업의 실행 코드 | **실행**한다. 코드를 읽히지 않는다. |
| `references/` | 필요 시 컨텍스트에 로드되는 문서 | **읽는다.** 토큰을 쓴다. |
| `assets/` | 출력물에 들어가는 파일 | 읽지도 실행하지도 않는다. 복사·삽입한다. |

**[규격]** `SKILL.md`는 스킬 하나에 **정확히 하나**, `<folder>/SKILL.md` 위치에.
D5의 검증기는 중첩된 `SKILL.md`를 거부하며 이유를 명시한다 — Skills API와 claude.ai는 업로드 시
하나만 받고, 중첩 로드는 Claude Code 파일시스템에서만 동작한다. 여러 개가 필요하면 **플러그인**
(`plugin/skills/<name>/SKILL.md`)으로 만든다.

### 3.2 실측 분포 **[관행]**

40개 스킬 중:

| 형태 | 개수 | 예 |
|---|---|---|
| `SKILL.md` 단독 (+LICENSE) | 약 24 | `learn`, `import-memory`, `deep-research` |
| `scripts/` 보유 | 7 | `pdf`, `docx`, `xlsx`, `pptx`, `setup-writing-style` |
| `references/` 또는 `reference/` 보유 | 3 | `mcp-builder`, `deep-research`, `skill-creator` |
| `assets/`·`templates/`·`themes/` 보유 | 5 | `morning`, `theme-factory`, `algorithmic-art` |

**과반이 단일 파일이다.** 번들 디렉터리는 필요할 때만 생긴다 — 처음부터 빈 `scripts/`를
만들지 않는다.

### 3.3 파일 경로 **[규격]**

- **항상 슬래시(`/`)**. `scripts\helper.py`는 유닉스에서 깨진다.
- **내용을 알 수 있는 이름.** `form_validation_rules.md` ○ / `doc2.md` ×
- 도메인이 여럿이면 도메인별로 나눈다: `reference/finance.md`, `reference/sales.md` ○ /
  `docs/file1.md`, `docs/file2.md` ×

---

## 4. 프론트매터 표준

### 4.1 검증 규칙 (하드 제약) **[규격]**

D3의 필드 요구사항과 D5의 `quick_validate.py`가 완전히 일치한다. 아래가 실제로 강제되는 전부다.

| 대상 | 규칙 |
|---|---|
| 파일 | 첫 줄이 `---`이어야 프론트매터로 인식. 아니면 **파일 전체가 본문**이 된다. |
| 프론트매터 | 유효한 YAML **매핑**이어야 함 |
| `name` | `^[a-z0-9-]+$` (소문자·숫자·하이픈만) |
| `name` | **최대 64자** |
| `name` | 하이픈으로 시작/끝 금지, 연속 하이픈(`--`) 금지 |
| `name` | XML 태그 금지 |
| `name` | 예약어 금지: **`anthropic`, `claude`** |
| `description` | 비어 있으면 안 됨 |
| `description` | **최대 1,024자** |
| `description` | **꺾쇠괄호(`<`, `>`) 금지** |
| `compatibility` | 문자열, **최대 500자** |
| 업로드 전체 | **30MB 미만** (압축 해제 기준) |

> `description`의 꺾쇠 금지는 단순한 파싱 문제가 아니다. D1은 동기화된 스킬에 대해
> Claude Code가 *"escapes angle brackets so the text can't imitate Claude Code's internal
> formatting"*이라고 적는다. 즉 **프롬프트 인젝션 방어**다. 직접 쓸 때도 넣지 않는다.

이 규칙들을 그대로 구현한 검증 스크립트를 함께 둔다: [`validate_skill.py`](validate_skill.py)

### 4.2 전체 필드 표

**S** = 개방 표준(어디서나 사용 가능) · **CC** = Claude Code 전용

| 필드 | 범위 | 필수 | 설명 |
|---|:---:|---|---|
| `name` | **S** | 권장 | 표시 이름. **명령어 이름이 아니다** — §4.3 참조 |
| `description` | **S** | **권장(사실상 필수)** | 무엇을 하고 **언제 쓰는지**. 트리거의 전부 |
| `license` | **S** | 아니오 | 라이선스. Claude Code는 받기만 하고 동작하지 않음 |
| `compatibility` | **S** | 아니오 | 실행 환경 요구사항. ≤500자 |
| `metadata` | **S** | 아니오 | 자유 형식 YAML 맵. 자체 툴링용. 다른 필드명 재사용 금지 |
| `allowed-tools` | **S** | 아니오 | 이 스킬을 호출한 **턴 동안만** 승인 없이 쓸 도구 |
| `when_to_use` | CC | 아니오 | 트리거 문구 보강. `description`에 이어붙고 **합산 1,536자에서 잘림** |
| `disable-model-invocation` | CC | 아니오 | `true`면 사람만 호출. 부작용 있는 워크플로용 |
| `user-invocable` | CC | 아니오 | `false`면 Claude만 호출. 배경지식용 |
| `disallowed-tools` | CC | 아니오 | 스킬 활성 중 도구 풀에서 **제거** |
| `argument-hint` | CC | 아니오 | 자동완성 힌트. 예: `[issue-number]` |
| `arguments` | CC | 아니오 | 명명 인자 선언 → `$name` 치환 |
| `model` | CC | 아니오 | 이 스킬 동안 모델 오버라이드 |
| `effort` | CC | 아니오 | `low`/`medium`/`high`/`xhigh`/`max` |
| `context` | CC | 아니오 | `fork` → 격리된 서브에이전트에서 실행 |
| `agent` | CC | 아니오 | `context: fork`일 때 에이전트 타입 |
| `background` | CC | 아니오 | `context: fork`일 때 `false`면 결과를 그 턴에서 대기 |
| `hooks` | CC | 아니오 | 스킬 호출 시 등록되어 **세션 끝까지 유지**되는 훅 |
| `paths` | CC | 아니오 | 글롭. 매칭 파일 작업 중일 때만 자동 로드 |
| `shell` | CC | 아니오 | `bash`(기본) 또는 `powershell` |

### 4.3 이름 규칙 — 가장 자주 오해하는 부분 **[규격]**

**개인/프로젝트 스킬에서 명령어 이름은 `name` 필드가 아니라 디렉터리 이름에서 온다.**

| 위치 | 명령어 이름의 출처 |
|---|---|
| `~/.claude/skills/<dir>/SKILL.md` | **디렉터리 이름** (`name`은 목록의 표시 라벨일 뿐) |
| `.claude/skills/<dir>/SKILL.md` | **디렉터리 이름** |
| 중첩 `.claude/skills/` (이름 충돌 시) | `상대경로:디렉터리명` → `/apps/web:deploy` |
| `.claude/commands/<file>.md` | 확장자 뺀 파일명 |
| 플러그인 `skills/<dir>/SKILL.md` | **`name` 또는 디렉터리명**, 플러그인 접두사 붙음 |
| 플러그인 루트 `SKILL.md` | **`name`** (없으면 플러그인 디렉터리명) |

실제로 이 환경에 설치된 스킬이 이 규칙의 산 증거다:

```
경로: ~/.claude/skills/session-start-hook/SKILL.md
프론트매터: name: startup-hook-skill
스킬 목록에 뜨는 이름: session-start-hook      ← 디렉터리가 이긴다
```

**[표준]** 그래서 **디렉터리 이름과 `name`을 항상 일치시킨다.** 실측 40개 전부 일치한다.
불일치는 문서·대화·이슈에서 스킬을 지칭할 때 두 개의 이름이 돌아다니게 만들 뿐 아무것도 사지 못한다.

**작명 형태** (D2 권고):

| 판정 | 형태 | 예 |
|---|---|---|
| ○ 권장 | 동명사 (verb+ing) | `processing-pdfs`, `analyzing-spreadsheets`, `writing-documentation` |
| ○ 허용 | 명사구 | `pdf-processing`, `spreadsheet-analysis` |
| ○ 허용 | 동사형 | `process-pdfs`, `analyze-spreadsheets` |
| × 금지 | 모호 | `helper`, `utils`, `tools` |
| × 금지 | 지나치게 일반적 | `documents`, `data`, `files` |
| × 금지 | 예약어 포함 | `anthropic-helper`, `claude-tools` |

> 다만 실측 40개는 대부분 **짧은 명사**다(`pdf`, `docx`, `learn`, `pages`, `morning`).
> 동명사는 D2의 권고이고, 실제 배포물의 관행은 "그 스킬의 대상을 가리키는 가장 짧은 말"에 가깝다.
> 컬렉션 안에서 **일관되기만 하면** 어느 쪽이든 된다는 것이 D2의 실제 요지다.

### 4.4 이름 충돌 해소 순서 **[규격]**

Claude Code에서 같은 이름이 겹칠 때:

```
enterprise  >  personal(~/.claude)  >  project(.claude)  >  bundled
                                                          >  claude.ai 동기화
플러그인은 `plugin:skill` 네임스페이스라 충돌하지 않음
같은 이름의 skill과 command가 있으면 → skill이 이김
```

주의할 점 두 가지:

- 프로젝트에 `code-review` 스킬을 두면 번들 `/code-review`를 대체하지만, **번들의 별칭
  `/review`는 절대 내 스킬로 가지 않는다.**
- claude.ai에서 동기화된 스킬은 이름이 다른 무엇과든 겹치면 **그냥 건너뛴다.** 대소문자·공백·
  보이지 않는 문자·전각 문자를 무시하고 비교하므로 `Commit`으로 우회할 수 없다.

---

## 5. `description` 작성 표준 — 여기가 스킬의 성패다

D5의 표현: *"The description field in SKILL.md frontmatter is the primary mechanism that
determines whether Claude invokes a skill."*

### 5.1 필수 요소 세 가지 **[표준]**

```
description = [무엇을 하는가] + [언제 쓰는가 — 구체적 트리거] + [언제 쓰지 않는가]
```

세 번째가 실측에서 가장 두드러진 요소인데 공식 문서에는 강조되어 있지 않다. 40개 중 긴
description(600자 이상) 10개는 **거의 전부 "Do NOT / However" 절을 갖는다.**

`xlsx` (948자, 실제 배포물):

> Use this skill any time a spreadsheet file is the primary input or output. This means any
> task where the user wants to: open, read, edit, or fix an existing .xlsx, .xlsm, .xltx,
> .csv, or .tsv file (e.g., adding columns, computing formulas, formatting, charting,
> cleaning messy data); create a new spreadsheet from scratch (…). Trigger especially when
> the user references a spreadsheet file by name or path — **even casually (like "the xlsx in
> my downloads")** (…). **Do NOT trigger when the primary deliverable is a Word document,
> HTML report, standalone Python script, database pipeline, or Google Sheets API
> integration, even if tabular data is involved.**

구조가 보인다: **동작 나열 → 확장자·경로 등 구체 트리거 → 캐주얼한 표현까지 커버 → 인접
도메인 배제.** 마지막 절이 없으면 이 스킬은 "표 형태 데이터"가 나올 때마다 오발한다.

### 5.2 규칙

| # | 규칙 | 근거 |
|---|---|---|
| 1 | **3인칭으로 쓴다** | D2 경고. description은 시스템 프롬프트에 주입되므로 시점이 섞이면 발견이 망가진다. `"Processes Excel files"` ○ / `"I can help you…"` ×, `"You can use this to…"` × |
| 2 | **무엇 + 언제를 모두 담는다** | 100개 넘는 스킬 중에서 고르는 판단 근거가 이것뿐 |
| 3 | **핵심 유스케이스를 앞에 둔다** | Claude Code는 `description`+`when_to_use`를 **1,536자에서 자른다** |
| 4 | **구체 용어를 넣는다** | 확장자(`.xlsx`), 도구명, 사용자가 실제로 쓸 단어 |
| 5 | **배제 조건을 넣는다** | 인접 스킬과 경쟁하는 지점을 명시 |
| 6 | **약간 "밀어붙인다"** | D5: *"Claude has a tendency to **undertrigger** skills."* 애매하면 트리거되도록 |
| 7 | **꺾쇠(`<`,`>`)를 넣지 않는다** | [규격] |
| 8 | **1,024자를 넘기지 않는다** | [규격] |

규칙 6의 D5 원문 예시:

> 나쁨: `"How to build a simple fast dashboard to display internal Anthropic data."`
> 좋음: `"How to build a simple fast dashboard to display internal Anthropic data.
> **Make sure to use this skill whenever the user mentions dashboards, data visualization,
> internal metrics, or wants to display any kind of company data, even if they don't
> explicitly ask for a 'dashboard.'**"`

### 5.3 길이 기준 **[관행]**

실측 중앙값이 **308자**다. 이걸 기본 목표로 삼는다.

| 길이 | 언제 | 실측 예 |
|---|---|---|
| ~150–300자 | 경쟁 스킬이 없고 트리거가 자명할 때 | `call-to-book`, `financial-calculator`, `import-memory` |
| ~300–600자 | 일반적인 경우 | `deep-research`, `setup-writing-style` |
| ~600–1,000자 | 인접 스킬과 경계를 다퉈서 배제 규칙이 필요할 때 | `docx`(1,003), `pptx`(960), `xlsx`(948), `pages`(982) |

**긴 description은 실패가 아니다.** 다만 길이는 "배제 규칙을 쓰느라" 늘어나야지 "무엇을 하는지
설명하느라" 늘어나면 안 된다.

### 5.4 트리거 정확도를 측정하는 법 **[표준]**

D5는 description 최적화를 별도 스크립트로 돌린다. 방법론만 옮기면:

1. **평가 쿼리 20개**를 만든다 — 트리거되어야 할 것 8–10개, **되지 말아야 할 것 8–10개**.
2. 쿼리는 **진짜 사용자가 칠 법한 문장**이어야 한다. 파일 경로, 회사명, 컬럼명, 오타, 소문자,
   약간의 배경 설명을 넣는다.
   - 나쁨: `"Format this data"`, `"Extract text from PDF"`
   - 좋음: `"ok so my boss just sent me this xlsx file (its in my downloads, called something
     like 'Q4 sales final FINAL v2.xlsx') and she wants me to add a column that shows the
     profit margin as a percentage. The revenue is in column C and costs are in column D i think"`
3. **부정 케이스는 near-miss여야 한다.** PDF 스킬의 부정 테스트로 "피보나치 함수 작성"은
   아무것도 검증하지 못한다. 키워드는 겹치는데 실제로는 다른 게 필요한 쿼리를 만든다.
4. 각 쿼리를 **3회씩** 돌려 트리거율을 낸다(단발은 노이즈).
5. **60% train / 40% held-out test로 나누고, 최종 선택은 test 점수로 한다.** train 점수로
   고르면 오버피팅된다.

**알아둘 메커니즘**: Claude는 **자기가 쉽게 처리할 수 있는 일에는 스킬을 조회하지 않는다.**
"이 PDF 읽어줘" 같은 단발 요청은 description이 완벽해도 트리거되지 않을 수 있다. 그래서
평가 쿼리 자체가 "스킬을 참조할 만큼 복잡"해야 한다.

---

## 6. 본문 작성 표준

### 6.1 분량 **[표준]**

- **`SKILL.md` 본문 500줄 이하.** 실측 40개 전부 준수(최장 485줄, 중앙값 80줄).
- 500줄에 근접하면 **계층을 하나 더 만들고**, 어디로 가야 하는지 포인터를 남긴다.
- 참조 파일이 **100줄을 넘으면 맨 위에 목차**를 둔다. Claude가 `head -100`으로 미리보기 할 때
  전체 범위가 보이게 하기 위해서다.

### 6.2 간결성 — "Claude는 이미 똑똑하다" **[표준]**

D2의 기본 전제. 각 문단에 대해 스스로 묻는다:

- 이 설명이 정말 필요한가?
- Claude가 이미 아는 것 아닌가?
- 이 문단은 토큰 값을 하는가?

D2의 대조 예시 (PDF 텍스트 추출, 약 50토큰 vs 약 150토큰):

````markdown
## 좋음 (~50 토큰)
Use pdfplumber for text extraction:

```python
import pdfplumber
with pdfplumber.open("file.pdf") as pdf:
    text = pdf.pages[0].extract_text()
```
````

```markdown
## 나쁨 (~150 토큰)
PDF (Portable Document Format) files are a common file format that contains
text, images, and other content. To extract text from a PDF, you'll need to
use a library. There are many libraries available for PDF processing, but
pdfplumber is recommended because it's easy to use and handles most cases well.
First, you'll need to install it using pip. Then you can use the code below...
```

### 6.3 자유도를 작업의 취약성에 맞춘다 **[표준]**

D2의 핵심 개념. **로봇이 길을 걷는다고 생각한다.**

| 자유도 | 형태 | 쓸 때 | 비유 |
|---|---|---|---|
| **높음** | 서술형 지침 | 여러 접근이 유효, 맥락에 따라 판단 | **트인 들판** — 방향만 주고 경로는 맡긴다 |
| **중간** | 파라미터 있는 의사코드/스크립트 | 선호 패턴이 있고 변형은 허용 | |
| **낮음** | 파라미터 없는 특정 스크립트 | 취약하고 오류가 잦음, 일관성이 결정적 | **절벽 사이 좁은 다리** — 정확한 명령과 난간 |

낮은 자유도의 예:

````markdown
## Database migration
Run exactly this script:
```bash
python scripts/migrate.py --verify --backup
```
Do not modify the command or add additional flags.
````

### 6.4 강제 어법을 절제한다 **[표준]**

D5가 가장 강하게 말하는 지점이고, 흔한 안티패턴이다:

> Try hard to explain the **why** behind everything you're asking the model to do. (…)
> **If you find yourself writing ALWAYS or NEVER in all caps, or using super rigid
> structures, that's a yellow flag** — if possible, reframe and explain the reasoning so
> that the model understands why the thing you're asking for is important. That's a more
> humane, powerful, and effective approach.

즉 `MUST`/`ALWAYS`를 뿌리는 대신 **왜 중요한지 설명한다.** 다만 출력 형식이 계약인 경우
(API 응답 포맷 등)는 예외로 `ALWAYS use this exact template`을 쓴다 — 그때는 실제로 계약이니까.

### 6.5 워크플로: 체크리스트 패턴 **[표준]**

복잡한 다단계 작업은 **복사해서 체크해 나갈 체크리스트**를 준다.

````markdown
## PDF form filling workflow

Copy this checklist and check off items as you complete them:

```
Task Progress:
- [ ] Step 1: Analyze the form (run analyze_form.py)
- [ ] Step 2: Create field mapping (edit fields.json)
- [ ] Step 3: Validate mapping (run validate_fields.py)
- [ ] Step 4: Fill the form (run fill_form.py)
- [ ] Step 5: Verify output (run verify_output.py)
```

**Step 1: Analyze the form**
Run: `python scripts/analyze_form.py input.pdf`
...
````

코드가 없는 스킬에도 그대로 적용된다(리서치 종합, 문서 리뷰 등).

### 6.6 피드백 루프 **[표준]**

> **검증기 실행 → 오류 수정 → 반복.** D2: *"This pattern greatly improves output quality."*

```markdown
## Document editing process
1. Make your edits to `word/document.xml`
2. **Validate immediately**: `python ooxml/scripts/validate.py unpacked_dir/`
3. If validation fails:
   - Review the error message carefully
   - Fix the issues in the XML
   - Run validation again
4. **Only proceed when validation passes**
5. Rebuild: `python ooxml/scripts/pack.py unpacked_dir/ output.docx`
```

검증기가 스크립트일 필요는 없다. 스타일 가이드 문서를 "검증기"로 삼고 체크리스트로 대조하는
것도 같은 패턴이다.

### 6.7 계획–검증–실행 패턴 **[표준]**

배치 작업·파괴적 변경·고위험 작업에는 **중간 산출물을 파일로 만들고 스크립트로 검증**한다.

```
analyze → [계획 파일 생성] → [계획 검증] → execute → verify
```

이유: 오류를 적용 전에 잡고, 기계적으로 검증 가능하며, 원본을 건드리지 않고 계획만 반복
수정할 수 있고, 에러 메시지가 특정 문제를 가리킨다.

검증 스크립트는 **말이 많아야 한다**: `"Field 'signature_date' not found. Available fields:
customer_name, order_total, signature_date_signed"` 처럼.

### 6.8 참조는 SKILL.md에서 1단계까지만 **[표준]**

```markdown
✗ 나쁨                        ○ 좋음
SKILL.md → advanced.md        SKILL.md → advanced.md
advanced.md → details.md      SKILL.md → reference.md
details.md → 실제 내용         SKILL.md → examples.md
```

이유가 구체적이다 — 참조된 파일에서 다시 참조된 파일을 만나면 Claude가 `head -100` 같은
**부분 읽기로 미리보기**하는 경향이 있고, 그러면 정보가 잘린다.

### 6.9 시간 종속 정보를 넣지 않는다 **[표준]**

```markdown
✗ If you're doing this before August 2025, use the old API.
  After August 2025, use the new API.

○ ## Current method
  Use the v2 API endpoint: `api.example.com/v2/messages`

  ## Old patterns
  <details><summary>Legacy v1 API (deprecated 2025-08)</summary>
  The v1 API used: `api.example.com/v1/messages`. No longer supported.
  </details>
```

### 6.10 용어를 하나로 고정한다 **[표준]**

| ○ 일관 | × 혼용 |
|---|---|
| 항상 "API endpoint" | "API endpoint" / "URL" / "API route" / "path" |
| 항상 "field" | "field" / "box" / "element" / "control" |
| 항상 "extract" | "extract" / "pull" / "get" / "retrieve" |

### 6.11 선택지를 늘어놓지 않는다 **[표준]**

````markdown
✗ "You can use pypdf, or pdfplumber, or PyMuPDF, or pdf2image, or..."

○ "Use pdfplumber for text extraction:
   ```python
   import pdfplumber
   ```
   For scanned PDFs requiring OCR, use pdf2image with pytesseract instead."
````

**기본값 하나 + 명시적 탈출구 하나.**

### 6.12 MCP 도구는 완전 수식 이름으로 **[표준]**

```markdown
Use the BigQuery:bigquery_schema tool to retrieve table schemas.
Use the GitHub:create_issue tool to create issues.
```

서버 접두사가 없으면, 특히 MCP 서버가 여럿일 때 "tool not found"가 난다.

---

## 7. 번들 리소스 표준

### 7.1 스크립트: 미루지 말고 해결한다 **[표준]**

```python
# ○ 오류를 명시적으로 처리
def process_file(path):
    try:
        with open(path) as f:
            return f.read()
    except FileNotFoundError:
        print(f"File {path} not found, creating default")
        with open(path, "w") as f:
            f.write("")
        return ""

# ✗ 실패시키고 Claude에게 떠넘김
def process_file(path):
    return open(path).read()
```

### 7.2 "부두 상수" 금지 **[표준]**

Ousterhout의 법칙. **당신이 올바른 값을 모르면 Claude는 어떻게 알겠는가?**

```python
# ○ 스스로 설명하는 상수
# HTTP requests typically complete within 30 seconds
# Longer timeout accounts for slow connections
REQUEST_TIMEOUT = 30

# ✗ 매직 넘버
TIMEOUT = 47  # 왜 47?
RETRIES = 5   # 왜 5?
```

### 7.3 실행인지 참조인지 명시한다 **[표준]**

- **실행** (대부분): `"Run \`analyze_form.py\` to extract fields"`
- **참조로 읽기** (복잡한 로직을 이해시켜야 할 때): `"See \`analyze_form.py\` for the field
  extraction algorithm"`

실행이 기본이다 — 더 안정적이고, **코드가 컨텍스트에 들어가지 않는다.**

### 7.4 미리 만든 스크립트의 이점

Claude가 즉석에서 짤 수 있는 코드라도 번들하는 편이 낫다: 생성 코드보다 **안정적**, 컨텍스트에
코드를 안 넣어서 **토큰 절약**, 생성 시간 **절약**, 호출마다 **일관성**.

**언제 스크립트로 승격시키나** (D5의 신호): 테스트 실행 전사를 읽었을 때 **여러 케이스에서
독립적으로 비슷한 헬퍼 스크립트를 짜고 있으면**, 그건 번들해야 한다는 강한 신호다.

### 7.5 의존성을 가정하지 않는다 **[표준]**

```markdown
✗ "Use the pdf library to process the file."
○ "Install required package: `pip install pypdf`
   Then use it: ..."
```

서피스별 런타임 제약이 다르다 **[규격]**:

| 서피스 | 네트워크 | 런타임 패키지 설치 |
|---|---|---|
| Claude Code | 전체 | 가능 (단, **전역 설치는 지양** — 사용자 머신을 건드림) |
| claude.ai | 설정에 따라 전체/부분/없음 | 가능(npm·PyPI·GitHub) |
| Claude API | **없음** | **불가** — 사전 설치 패키지만 |

---

## 8. Claude Code 수명주기 — 알고 써야 하는 것들

### 8.1 본문은 남고 권한은 사라진다 **[규격]**

호출된 `SKILL.md`의 렌더링 결과는 **하나의 메시지로 대화에 들어가 이후 턴까지 유지된다.**
그러나 `allowed-tools` 승인은 **다음 사용자 메시지에서 해제**된다.

두 가지 실무적 귀결:

1. **작업 내내 적용되어야 할 지침은 "상시 지침"으로 쓴다.** Claude Code는 이후 턴에 스킬 파일을
   다시 읽지 않으므로, "1회성 단계"처럼 쓰면 나중에 적용이 흐려진다.
2. **같은 내용으로 재호출하면 복사본이 추가되지 않고 "이미 로드됨" 메모만 붙는다.** 인자나
   `` !`cmd` `` 출력이 바뀌어 렌더링 결과가 달라지면 전체가 다시 붙는다.

### 8.2 자동 압축(auto-compaction) 예산 **[규격]**

컨텍스트가 차서 요약되면, 호출된 스킬들이 **요약 뒤에 재부착**된다.

- 스킬당 **앞 5,000 토큰**만 유지
- 재부착 전체 **합산 25,000 토큰** 예산
- 최근 호출한 스킬부터 채우므로, **한 세션에서 많이 호출했다면 오래된 스킬은 통째로 사라진다**

→ 긴 세션에서 스킬이 안 먹는 것 같으면 **재호출**한다.

### 8.3 "스킬이 안 먹는다"의 실제 원인 **[표준]**

D1: 첫 응답 이후 스킬이 영향력을 잃은 것처럼 보이면, 대개 **내용은 아직 거기 있고 모델이 다른
도구/접근을 고르고 있는 것**이다. 대응은 셋 중 하나:

1. `description`과 지침을 강화해 모델이 계속 선호하게 만든다
2. **훅(hooks)** 으로 결정적으로 강제한다
3. 압축 이후라면 재호출한다

### 8.4 `context: fork`의 함정 **[규격]**

```yaml
context: fork      # 격리된 서브에이전트에서 실행. 대화 기록 접근 불가.
agent: Explore     # 없으면 general-purpose
background: false  # 없으면 백그라운드 실행(기본 true)
```

- **지침만 있고 과제가 없는 스킬에 `fork`를 쓰면 안 된다.** "이 API 규약을 써라" 같은 가이드라인은
  서브에이전트에게 과제 없이 전달되어 아무 의미 있는 출력 없이 끝난다.
- 백그라운드 포크는 **더 좁은 도구 집합**으로 돌고, 편집이 **체크포인트 밖에서** 적용되어
  `/rewind`로 되돌릴 수 없다(git으로 되돌려야 한다).

### 8.5 동적 컨텍스트 주입의 실패 모드 **[규격]**

```markdown
## Current changes
!`git diff HEAD`
```

- **실패한 명령은 그 플레이스홀더가 아니라 스킬 호출 전체를 중단시킨다.** Claude는 그 호출의
  스킬 내용을 아예 보지 못한다.
- 기본 `bash`에서 **0이 아닌 종료 코드는 전부 실패**다(검색·비교 계열 명령의 exit 1만 예외).
  → 실패할 수 있는 명령에는 **`|| true`를 붙인다.**
- 주입 명령은 **권한을 묻지 않는다.** 허용이 아닌 판정이 나오면 호출이 중단된다.
  `allowed-tools`로 미리 승인해 둔다.
- `!`는 **줄 시작 또는 공백 직후**에만 인식된다. `` KEY=!`cmd` ``는 리터럴로 남는다.
- 치환은 **원본 파일에 대해 1회**만 돈다. 명령 출력 안의 플레이스홀더는 다시 확장되지 않는다.

---

## 9. 평가 표준 — 트리거되는 것과 잘 되는 것은 다르다

D1의 핵심 문장: *"Seeing a skill trigger tells you Claude found it, not that it did what you
intended."*

### 9.1 평가를 먼저 만든다 **[표준]**

D2의 evaluation-driven development. **문서를 많이 쓰기 전에 평가부터.**

1. **격차 식별** — 스킬 없이 대표 과제를 돌리고 실패·누락을 기록
2. **평가 생성** — 그 격차를 검증하는 시나리오 **최소 3개**
3. **베이스라인 측정** — 스킬 없는 상태의 성능
4. **최소한의 지침 작성** — 격차를 메우고 평가를 통과할 만큼만
5. **반복** — 실행, 베이스라인 대비 비교, 개선

### 9.2 비교는 항상 A/B로 **[표준]**

**새 세션에서**, 같은 프롬프트를 스킬 있는 상태와 없는 상태로 각각 돌린다.
새 세션이 중요한 이유: 스킬을 만들면서 쌓인 컨텍스트가 **작성된 지침의 공백을 가려버린다.**

D5의 실행 규칙: with-skill과 baseline을 **같은 턴에 함께 띄운다.** with-skill을 먼저 돌리고
나중에 baseline을 돌리면 조건이 달라진다.

베이스라인의 정의:
- **새 스킬을 만드는 중** → 스킬 없음
- **기존 스킬을 개선하는 중** → 편집 전 스냅샷 (`cp -r <skill> <workspace>/skill-snapshot/`)

### 9.3 평가 파일 스키마 **[표준]**

`evals/evals.json` (패키징에서 제외되는 경로다):

```json
{
  "skill_name": "example-skill",
  "evals": [
    {
      "id": 1,
      "prompt": "User's example prompt",
      "expected_output": "Description of expected result",
      "files": ["evals/files/sample1.pdf"],
      "expectations": [
        "The output includes X",
        "The skill used script Y"
      ]
    }
  ]
}
```

**어서션(expectations)의 조건**: 객관적으로 검증 가능해야 하고, 이름만 보고도 무엇을 검사하는지
알 수 있어야 한다. **주관적인 스킬(글쓰기 스타일, 디자인 품질)에 어서션을 억지로 붙이지 않는다** —
사람의 판단이 필요한 것에는 정성 평가를 쓴다.

### 9.4 무엇을 볼 것인가 **[표준]**

정량 지표만으로는 안 보이는 것들:

| 관찰 대상 | 의미 |
|---|---|
| 어떤 어서션이 스킬 유무와 무관하게 항상 통과 | **비변별적** — 그 어서션은 아무것도 측정하지 않음 |
| 편차가 큰 eval | 플레이키 — 테스트 자체를 의심 |
| 시간/토큰 오버헤드 | 통과율 개선이 이 비용을 정당화하는가 |
| Claude가 예상 밖 순서로 파일을 읽음 | 구조가 생각만큼 직관적이지 않음 |
| 참조를 따라가지 않음 | 링크가 더 명시적/눈에 띄어야 함 |
| 같은 파일을 반복해서 읽음 | 그 내용은 `SKILL.md` 본문에 있어야 함 |
| 번들 파일을 한 번도 안 읽음 | 불필요하거나 신호가 부족함 |

### 9.5 여러 모델에서 테스트한다 **[표준]**

스킬은 모델에 대한 **덧셈**이므로 효과가 기반 모델에 달려 있다.

- **Haiku** — 지침이 충분한가?
- **Sonnet** — 명확하고 효율적인가?
- **Opus** — 과잉 설명이 없는가?

Opus에 완벽한 스킬이 Haiku에는 부족할 수 있다.

### 9.6 개선의 원칙 **[표준]**

D5가 가장 길게 다루는 부분. 요약하면:

1. **피드백에서 일반화하라.** 3개의 예제로 반복하지만, 스킬은 수백만 번 쓰인다. 그 예제에만
   맞는 "쫀쫀한 오버피팅 수정"이나 "억압적인 MUST"를 넣지 말고, 막히면 **다른 은유나 다른
   작업 패턴**을 시도해 본다.
2. **프롬프트를 가볍게 유지하라.** 제 몫을 못 하는 부분은 제거한다. 최종 출력만 보지 말고
   **전사(transcript)를 읽어라** — 스킬 때문에 모델이 비생산적인 일에 시간을 쓰고 있으면 그
   부분을 없애 본다.
3. **왜인지 설명하라.** (§6.4)
4. **반복 작업을 찾아라.** (§7.4)

---

## 10. 배포 표준

### 10.1 설치 위치 **[규격]**

| 범위 | 경로 | 적용 대상 |
|---|---|---|
| Enterprise | 관리 설정 디렉터리의 `.claude/skills/` | 조직 전체 |
| Personal | `~/.claude/skills/<name>/SKILL.md` | 내 모든 프로젝트 |
| Project | `.claude/skills/<name>/SKILL.md` | 이 프로젝트만 |
| Plugin | `<plugin>/skills/<name>/SKILL.md` | 플러그인이 켜진 곳 |

- `synced`는 **예약어**다(대소문자 무관). claude.ai 동기화 스킬이 들어가는 자리.
- 중첩 `.claude/skills/`는 **그 하위 디렉터리의 파일을 읽거나 편집할 때** 로드된다. 시작 시점엔
  자동완성에 뜨지 않는다.
- Claude Code는 스킬 디렉터리를 **감시**하므로 `SKILL.md` 편집은 재시작 없이 반영된다. 단
  **세션 시작 시 없던 최상위 스킬 디렉터리를 새로 만들면 재시작**해야 한다.

### 10.2 원격/클라우드 세션의 함정 **[규격]**

**Cowork 세션과 클라우드 세션은 로컬 `~/.claude/skills/`를 읽지 않는다.**

- 둘 다 **claude.ai 계정에 활성화된 스킬**을 세션 시작 시 동기화해서 쓴다
- 클라우드 세션은 추가로 **클론된 저장소의 `.claude/skills/`** 를 읽는다
- 개인 스킬만 로컬에 있으면, 루틴(routine)이 그 스킬을 호출할 때 **"스킬 없음"** 이 뜬다

→ **원격에서 쓸 스킬은 저장소의 `.claude/skills/`에 커밋하거나, 플러그인으로 만들어
`.claude/settings.json`에 선언하거나, claude.ai 계정에 올린다.**

### 10.3 패키징 **[규격]**

`.skill` 아카이브를 만들 때 **제외되는 경로** (D5 `package_skill.py`):

| 제외 대상 | 범위 |
|---|---|
| `__pycache__/`, `node_modules/` | 모든 깊이 |
| `*.pyc` | 모든 깊이 |
| `.DS_Store` | 모든 깊이 |
| `evals/` | **스킬 루트에서만** (중첩된 `evals/`는 포함됨) |

업로드 제약:
- **총 30MB 미만** (압축 해제 기준)
- `SKILL.md`가 업로드 루트(또는 단일 상위 폴더 최상단)에 있어야 함
- **새 버전은 전체 스냅샷이다** — 빠뜨린 파일은 이월되지 않는다
- 새 버전의 `SKILL.md` `name`은 기존 스킬 이름과 **일치해야 한다**

### 10.4 서피스 간 동기화는 없다 **[규격]**

D3이 못 박는다: **커스텀 스킬은 서피스 간에 동기화되지 않는다.**
claude.ai 업로드 ≠ API 업로드 ≠ Claude Code 파일시스템. 각각 따로 관리한다.

공유 범위도 다르다:

| 서피스 | 공유 단위 |
|---|---|
| claude.ai | **개인 전용.** 팀원마다 각자 업로드. 관리자 중앙 관리 **불가** |
| Claude API | **워크스페이스 전체** |
| Claude Code | 개인/프로젝트, 또는 플러그인 |

---

## 11. 보안 표준

### 11.1 스킬은 소프트웨어 설치와 같다 **[규격]**

D3의 경고를 그대로 옮긴다:

> Skills give Claude new capabilities through instructions and code, which also means a
> **malicious Skill can direct Claude to invoke tools or execute code in ways that don't
> match the Skill's stated purpose.**

감사 항목:

- `SKILL.md`, 스크립트, 이미지, 그 밖의 모든 번들 파일을 읽는다
- **예상치 못한 네트워크 호출**, 파일 접근 패턴, 명시된 목적과 맞지 않는 동작
- **외부 URL에서 데이터를 가져오는 스킬은 특히 위험하다** — 가져온 내용에 악성 지시가 들어 있을
  수 있고, 신뢰할 만한 스킬도 외부 의존성이 나중에 바뀌면 오염된다

### 11.2 `allowed-tools`는 워크스페이스 신뢰를 통과하지 않는다 **[규격]**

Claude Code는 **신뢰한 적 없는 폴더의 `-p` 실행에서도** 프로젝트 스킬의 `allowed-tools`를
적용한다. 스킬은 스스로에게 넓은 도구 접근을 부여할 수 있다.

→ **저장소에 체크인된 스킬의 `allowed-tools`를 실행 전에 읽어라.**

### 11.3 놀라움 없음의 원칙 **[표준]**

D5: 스킬의 내용은 **설명대로일 때 사용자를 놀라게 하지 않아야 한다.** 악성코드, 익스플로잇,
무단 접근·데이터 유출을 돕는 스킬을 만들지 않는다. (역할극 같은 것은 무관하다.)

### 11.4 관리자용 통제 **[규격]**

| 통제 | 방법 |
|---|---|
| 모든 스킬 차단 | `/permissions` 거부 규칙에 `Skill` |
| 특정 스킬만 허용/거부 | `Skill(commit)`, `Skill(review-pr *)`, `Skill(deploy *)` |
| 개별 스킬 가시성 조정 | `skillOverrides`: `"on"` / `"name-only"` / `"user-invocable-only"` / `"off"` |
| 셸 주입 차단 | `"disableSkillShellExecution": true` (번들·관리 스킬은 영향 없음) |
| 번들 스킬 끄기 | `disableBundledSkills` (`/doctor` 제외) |
| 엔터프라이즈 스캔 | claude.ai/Cowork 업로드 커스텀 스킬에 한해 Skill content scanning |

---

## 12. 최종 체크리스트

작성한 스킬을 공유하기 전에.

### 규격 (위반 시 실패)

- [ ] `SKILL.md`가 정확히 1개, 스킬 루트에
- [ ] 파일 **첫 줄**이 `---`
- [ ] `name`: 소문자·숫자·하이픈만, ≤64자, 하이픈 시작/끝/연속 없음
- [ ] `name`에 `anthropic`/`claude` 없음, XML 태그 없음
- [ ] `description`: 비어 있지 않음, ≤1,024자, **꺾쇠 없음**
- [ ] `compatibility` 쓴다면 ≤500자
- [ ] **P0 목표라면** 프론트매터가 `name`/`description`/`license`/`compatibility`/`metadata`/`allowed-tools` 6개 안에만 있음
- [ ] 총 크기 30MB 미만
- [ ] 디렉터리 이름 == `name`

### description

- [ ] 3인칭
- [ ] 무엇을 하는가 + **언제 쓰는가**를 모두 포함
- [ ] 핵심 유스케이스가 **앞에** (1,536자에서 잘림)
- [ ] 구체적 트리거 용어(확장자·도구명·사용자가 쓸 표현) 포함
- [ ] 인접 스킬과의 **배제 조건** 포함
- [ ] 언더트리거를 감안해 약간 밀어붙임

### 본문

- [ ] 500줄 이하
- [ ] Claude가 이미 아는 내용을 설명하지 않음
- [ ] 자유도가 작업의 취약성에 맞음
- [ ] 대문자 MUST/ALWAYS 남발 대신 **이유**를 설명
- [ ] 시간 종속 정보 없음 (있으면 "Old patterns" 섹션에)
- [ ] 용어 일관
- [ ] 예시가 구체적
- [ ] 참조가 SKILL.md에서 **1단계**
- [ ] 100줄 넘는 참조 파일에 목차
- [ ] 워크플로에 명확한 단계(필요하면 체크리스트)
- [ ] 선택지를 늘어놓지 않고 기본값 + 탈출구

### 코드·스크립트

- [ ] 스크립트가 문제를 **해결**하지 Claude에게 미루지 않음
- [ ] 오류 처리가 명시적이고 도움이 됨
- [ ] 부두 상수 없음 (모든 값에 근거)
- [ ] 필요 패키지를 지침에 명시하고 가용성 확인
- [ ] 경로가 전부 슬래시
- [ ] 실행인지 참조인지 명시
- [ ] 중요한 작업에 검증 단계
- [ ] MCP 도구는 `Server:tool` 완전 수식

### 평가

- [ ] 평가 **최소 3개**
- [ ] **새 세션**에서 A/B 비교
- [ ] Haiku·Sonnet·Opus에서 테스트
- [ ] 실제 사용 시나리오로 테스트
- [ ] (트리거가 문제라면) 20개 쿼리로 트리거 정확도 측정, test 점수로 선택

### 배포

- [ ] 원격/클라우드에서 쓸 것이면 저장소나 플러그인이나 claude.ai에 반영
- [ ] `allowed-tools`가 실제로 필요한 최소 범위
- [ ] 외부 URL 페치가 있다면 그 위험을 문서에 명시
- [ ] `license` 명시(배포하는 경우)

---

## 13. 함께 있는 파일

| 파일 | 내용 |
|---|---|
| [`validate_skill.py`](validate_skill.py) | §4.1 규격을 그대로 구현한 검증기. 이 문서의 실행 가능한 부분 |
| [`templates/SKILL.md`](templates/SKILL.md) | P0(이식형) 뼈대 |
| [`templates/SKILL.claude-code.md`](templates/SKILL.claude-code.md) | P1(Claude Code 확장) 뼈대 |
| [`Agent_Skills_구조도.drawio`](Agent_Skills_구조도.drawio) | 3단계 로딩·수명주기·배포 경로 |
