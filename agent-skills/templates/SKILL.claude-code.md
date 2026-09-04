---
# P1 — Claude Code 전용 뼈대.
#
# 이 파일의 필드와 본문 기능(`!`명령 주입, $ARGUMENTS, ${CLAUDE_*})은
# Claude Code에서만 동작한다. claude.ai 업로드 · Skills API · package_skill.py는
# 스펙 밖 필드를 만나면 하드 에러로 거부한다.
#
#   Unexpected key(s) in SKILL.md frontmatter: argument-hint.
#   Allowed properties are: allowed-tools, compatibility, description,
#                           license, metadata, name
#
# 그러니 P1은 "이게 없으면 스킬이 성립하지 않을 때"만 고른다.
# 필요한 줄만 남기고 나머지는 지운다.

name: skill-name
description: >-
  [무엇을 하는가]. Use when [언제]. Do NOT use when [배제 조건].

# ── 호출 주체 ──────────────────────────────────────────────────────────
# 기본은 사람도 Claude도 호출 가능.
# disable-model-invocation: true   # 사람만. 부작용 있는 워크플로(/deploy, /commit)
#                                  # description이 컨텍스트에 실리지 않아 토큰도 아낀다
# user-invocable: false            # Claude만. 명령으로 의미 없는 배경지식용

# ── 권한 ───────────────────────────────────────────────────────────────
# allowed-tools는 이 스킬을 호출한 "그 턴 동안만" 유효하다.
# 다음 사용자 메시지에서 해제된다 (본문은 남지만 권한은 사라진다).
# ${CLAUDE_SKILL_DIR}는 본문과 이 필드 양쪽에서 치환되므로, 같은 문자열을 써서
# 번들 스크립트를 권한 프롬프트 없이 실행시킬 수 있다.
# allowed-tools: Bash(${CLAUDE_SKILL_DIR}/scripts/run.sh *) Read Grep
# disallowed-tools: AskUserQuestion   # 무인 루프에서 사람에게 묻지 않게

# ── 실행 컨텍스트 ──────────────────────────────────────────────────────
# context: fork      # 격리된 서브에이전트에서 실행. 대화 기록 접근 불가.
#                    # 주의: 과제 없이 "가이드라인"만 있는 스킬에 쓰면
#                    #       서브에이전트가 아무 의미 있는 출력 없이 끝난다.
# agent: Explore     # 없으면 general-purpose
# background: false  # 없으면 백그라운드. 백그라운드 포크는 도구 집합이 좁고
#                    # 편집이 체크포인트 밖이라 /rewind로 못 되돌린다.
# model: inherit
# effort: high

# ── 인자 ───────────────────────────────────────────────────────────────
# argument-hint: "[issue-number]"
# arguments: [issue, branch]     # → 본문에서 $issue, $branch

# ── 활성 범위 ──────────────────────────────────────────────────────────
# paths: ["packages/api/**", "*.proto"]   # 매칭 파일 작업 중일 때만 자동 로드
# when_to_use: "추가 트리거 문구. description에 이어붙고 합산 1536자에서 잘림"
# shell: bash
---

# [Skill Title]

## Live context

<!--
  `!`명령 주입: 스킬 내용이 Claude에게 전달되기 전에 실행되고 출력으로 치환된다.

  실패 모드를 알고 쓴다.
    - 실패한 명령은 그 자리만이 아니라 스킬 호출 전체를 중단시킨다.
      Claude는 그 호출의 스킬 내용을 아예 보지 못한다.
    - bash에서 0이 아닌 종료 코드는 전부 실패다(검색·비교 명령의 exit 1만 예외).
      실패할 수 있는 명령에는 반드시 `|| true`.
    - 주입 명령은 권한을 묻지 않는다. 허용 판정이 아니면 호출이 중단되므로
      allowed-tools로 미리 승인해 둔다.
    - `!`는 줄 시작 또는 공백 직후에만 인식된다. KEY=!`cmd`는 리터럴로 남는다.
    - 치환은 원본 파일에 대해 1회만 돈다. 출력 안의 플레이스홀더는 확장되지 않는다.
-->

- 변경 사항: !`git diff HEAD --stat || true`
- 브랜치: !`git branch --show-current`

```!
node --version || true
```

## Task

[$ARGUMENTS / $0 / $1 / $issue 로 인자를 받는다.
 플레이스홀더가 하나도 없으면 Claude Code가 본문 끝에 `ARGUMENTS: <입력>`을 붙인다.]

$ARGUMENTS 에 대해:

1. ...
2. ...

## Bundled scripts

번들 스크립트는 설치 위치와 무관하게 `${CLAUDE_SKILL_DIR}`로 참조한다.
프로젝트 루트 기준 파일은 `${CLAUDE_PROJECT_DIR}`, 플러그인 리소스는
`${CLAUDE_PLUGIN_ROOT}` / `${CLAUDE_PLUGIN_DATA}`.

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/run.py <input>
```

<!--
수명주기에서 알아야 할 것 (Agent_Skills_작성표준.md §8)

  - 렌더링된 본문은 하나의 메시지로 대화에 들어가 이후 턴까지 남는다.
    Claude Code는 이후 턴에 파일을 다시 읽지 않는다.
    → 작업 내내 적용될 지침은 "1회성 단계"가 아니라 "상시 지침"으로 쓴다.
  - 같은 내용으로 재호출하면 복사본 대신 "이미 로드됨" 메모만 붙는다.
    인자나 주입 출력이 바뀌면 전체가 다시 붙는다.
  - 자동 압축 시 스킬당 앞 5,000토큰만, 전체 25,000토큰 예산 안에서 재부착된다.
    최근 호출 순으로 채우므로 오래된 스킬은 통째로 사라질 수 있다 → 재호출한다.
  - 스킬이 안 먹는 것처럼 보이면 대개 내용은 남아 있고 모델이 다른 접근을
    고르는 중이다. description·지침을 강화하거나, 훅으로 결정적으로 강제한다.
-->
