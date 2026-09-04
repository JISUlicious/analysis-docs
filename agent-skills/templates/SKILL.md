---
# P0 — 이식형 뼈대.
# 여기 있는 6개 필드는 Agent Skills 개방 표준의 전부이며, Claude Code · claude.ai
# 업로드 · Skills API · Cowork · 클라우드 세션 어디서나 그대로 로드된다.
# 필드를 더 추가하는 순간 업로드가 "무시"가 아니라 "하드 에러"로 거부된다.
#
# name  [규격] 소문자·숫자·하이픈만 / ≤64자 / 하이픈 시작·끝·연속 금지
#             / 'anthropic'·'claude' 금지 / 디렉터리 이름과 일치시킬 것
name: skill-name

# description  [규격] ≤1024자, 꺾쇠(< >) 금지, 비어 있으면 안 됨
#              [표준] 3인칭 / 무엇 + 언제 + 언제 아닌지 / 핵심 유스케이스를 앞에
#                     (description + when_to_use는 목록에서 1536자에 잘린다)
#              실측 중앙값 308자. 인접 스킬과 경계를 다툴 때만 길어져야 한다.
description: >-
  [무엇을 하는가 — 동작을 구체적으로 나열].
  Use when [언제 — 사용자가 실제로 칠 표현, 확장자, 도구명, 캐주얼한 지칭까지].
  Do NOT use when [인접 도메인 배제 — 이 절이 없으면 오발한다].

# 아래 셋은 선택. 필요 없으면 지운다.
# license: Apache-2.0
# compatibility: "[실행 환경 요구사항, ≤500자]"
# metadata:
#   portability: P0
#   owner: team-name
---

# [Skill Title]

## Overview

[이 스킬이 다루는 범위를 두세 문장으로. Claude가 이미 아는 것은 쓰지 않는다.]

## Quick start

[가장 흔한 경로 하나. 선택지를 늘어놓지 말고 기본값 하나 + 탈출구 하나.]

```language
# 동작하는 최소 예제
```

## Workflow

[다단계라면 복사해서 체크할 수 있는 체크리스트를 준다.]

```
Task Progress:
- [ ] Step 1: ...
- [ ] Step 2: ...
- [ ] Step 3: ...
```

**Step 1: ...**

[각 단계에서 무엇을 하는지. 취약한 단계일수록 자유도를 낮춘다 —
 "정확히 이 명령을 실행한다"까지 내려갈 수 있다.]

## Validation

[중요한 작업에는 검증 루프를 넣는다: 검증기 실행 → 오류 수정 → 반복 →
 통과한 뒤에만 다음 단계로.]

## Additional resources

[참조는 이 파일에서 1단계까지만. 참조 파일이 다시 참조를 걸면
 Claude가 부분 읽기로 미리보기해서 정보가 잘린다.]

[아래 형태로 쓴다. 실제 번들 파일로 교체할 것 —
 링크가 가리키는 파일이 없으면 Claude가 읽으러 갔다가 빈손으로 돌아온다.]

```markdown
- 상세 API: [reference.md](reference.md)
- 사용 예: [examples.md](examples.md)
```

- 실행 스크립트: `scripts/helper.py` — **실행한다** (`python scripts/helper.py <input>`)

<!--
남은 규칙 (Agent_Skills_작성표준.md 참조)
  - 본문 500줄 이하. 넘으면 계층을 하나 더 만들고 포인터를 남긴다.
  - 100줄 넘는 참조 파일에는 맨 위에 목차.
  - 경로는 항상 슬래시. 역슬래시는 유닉스에서 깨진다.
  - 시간 종속 정보 금지. 필요하면 "Old patterns" 섹션에 <details>로.
  - 용어를 하나로 고정한다 (field/box/element를 섞지 않는다).
  - 대문자 MUST/ALWAYS 남발 대신 "왜 중요한지"를 설명한다.
  - MCP 도구는 완전 수식 이름으로: Server:tool_name
  - 의존 패키지를 가정하지 않는다. 설치 명령을 적는다.
-->
