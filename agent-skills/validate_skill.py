#!/usr/bin/env python3
"""
Agent Skills 검증기 — Agent_Skills_작성표준.md §4.1의 규격을 그대로 구현한다.

이 문서 세트의 "실행 가능한 부분"이다. 표준 문서가 [규격]으로 표시한 항목은 ERROR,
[표준]으로 표시한 항목은 WARN으로 낸다.

규칙의 출처 (전부 공개 문서):
  - https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview
      name/description 검증 규칙, 예약어
  - https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices
      500줄 권고, 참조 깊이, 경로 규칙
  - https://code.claude.com/docs/en/skills
      스펙 필드 vs Claude Code 확장 필드, 1536자 절단
  - https://platform.claude.com/docs/en/build-with-claude/skills-guide
      30MB 업로드 한계

사용법:
    python3 validate_skill.py <skill_dir> [<skill_dir> ...]
    python3 validate_skill.py --portable <skill_dir>    # P0(이식형) 등급으로 검사
    python3 validate_skill.py --quiet <skill_dir>       # ERROR만 출력

종료 코드: 0 = ERROR 없음, 1 = ERROR 있음
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - 환경에 따라 없을 수 있다
    yaml = None


# ---------------------------------------------------------------------------
# 규격 상수 — 표준 문서 §4.1과 1:1로 대응한다
# ---------------------------------------------------------------------------

#: 개방 표준(agentskills.io)이 허용하는 전체 필드. claude.ai 업로드, Skills API,
#: package_skill.py가 받는 유일한 집합이며, 이 밖의 키는 무시가 아니라 하드 에러다.
SPEC_FIELDS = {
    "name",
    "description",
    "license",
    "compatibility",
    "metadata",
    "allowed-tools",
}

#: Claude Code가 추가로 받는 필드. 로컬/플러그인 스킬에서만 동작한다.
CLAUDE_CODE_FIELDS = {
    "when_to_use",
    "argument-hint",
    "arguments",
    "disable-model-invocation",
    "user-invocable",
    "disallowed-tools",
    "model",
    "effort",
    "context",
    "agent",
    "background",
    "hooks",
    "paths",
    "shell",
}

NAME_PATTERN = re.compile(r"^[a-z0-9-]+$")
NAME_MAX = 64
DESCRIPTION_MAX = 1024
COMPATIBILITY_MAX = 500

#: description + when_to_use를 합쳐 스킬 목록에 실릴 때 잘리는 지점.
LISTING_TRUNCATE_AT = 1536

#: name에 들어갈 수 없는 예약어.
RESERVED_WORDS = ("anthropic", "claude")

#: 업로드 상한(압축 해제 기준).
MAX_TOTAL_BYTES = 30 * 1024 * 1024

#: SKILL.md 본문 권고 상한. 실측 40개 중 초과 사례 0건.
BODY_MAX_LINES = 500

#: 참조 파일에 목차를 요구하는 기준 줄 수.
TOC_REQUIRED_LINES = 100

#: 패키징에서 빠지는 경로. 어느 깊이에서나 제외된다.
EXCLUDED_DIR_PARTS = {"__pycache__", "node_modules"}
#: 스킬 루트에서만 제외되는 디렉터리.
ROOT_EXCLUDED_DIR_PARTS = {"evals"}
EXCLUDED_FILES = {".DS_Store"}
EXCLUDED_SUFFIXES = {".pyc"}

XML_TAG = re.compile(r"<[^>]*>")


# ---------------------------------------------------------------------------
# 결과 수집
# ---------------------------------------------------------------------------


class Report:
    """한 스킬에 대한 검사 결과."""

    def __init__(self, skill_dir: Path) -> None:
        self.skill_dir = skill_dir
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.notes: list[str] = []

    def error(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    def note(self, msg: str) -> None:
        self.notes.append(msg)

    @property
    def ok(self) -> bool:
        return not self.errors

    def render(self, quiet: bool = False) -> str:
        lines = [f"── {self.skill_dir}"]
        for m in self.errors:
            lines.append(f"   ERROR  {m}")
        if not quiet:
            for m in self.warnings:
                lines.append(f"   WARN   {m}")
            for m in self.notes:
                lines.append(f"   note   {m}")
        if self.ok and not self.warnings:
            lines.append("   OK")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 패키징 제외 규칙
# ---------------------------------------------------------------------------


def is_packaged(rel_path: Path) -> bool:
    """rel_path(스킬 루트 기준 상대경로)가 .skill 아카이브에 담기는지."""
    parts = rel_path.parts
    dir_parts = parts[:-1]
    if any(part in EXCLUDED_DIR_PARTS for part in dir_parts):
        return False
    if dir_parts and dir_parts[0] in ROOT_EXCLUDED_DIR_PARTS:
        return False
    if rel_path.name in EXCLUDED_FILES:
        return False
    return rel_path.suffix not in EXCLUDED_SUFFIXES


# ---------------------------------------------------------------------------
# 프론트매터
# ---------------------------------------------------------------------------


def split_frontmatter(text: str) -> tuple[str | None, str]:
    """(프론트매터 원문, 본문)을 돌려준다. 프론트매터가 없으면 (None, 전체).

    여는 `---`가 파일의 첫 줄일 때만 프론트매터로 친다. 아니면 파일 전체가 본문이며,
    이것은 스킬이 조용히 망가지는 가장 흔한 방식이다.
    """
    if not text.startswith("---"):
        return None, text
    m = re.match(r"^---\n(.*?)\n---\n?", text, re.DOTALL)
    if not m:
        return None, text
    return m.group(1), text[m.end():]


def check_frontmatter(fm: dict, rep: Report, portable: bool) -> None:
    keys = set(fm.keys())

    unknown = keys - SPEC_FIELDS - CLAUDE_CODE_FIELDS
    if unknown:
        rep.error(
            f"알 수 없는 프론트매터 키: {', '.join(sorted(unknown))}"
        )

    cc_only = keys & CLAUDE_CODE_FIELDS
    if cc_only:
        msg = (
            "Claude Code 전용 필드: "
            + ", ".join(sorted(cc_only))
            + " — claude.ai 업로드/Skills API/package_skill.py에서 하드 에러"
        )
        (rep.error if portable else rep.note)(msg)

    # --- name -------------------------------------------------------------
    if "name" not in fm:
        rep.warn("name 없음 — Claude Code는 디렉터리명으로 대체하지만 업로드 시 필수")
    else:
        name = fm["name"]
        if not isinstance(name, str):
            rep.error(f"name이 문자열이 아님: {type(name).__name__}")
        else:
            name = name.strip()
            if not NAME_PATTERN.match(name):
                rep.error(f"name '{name}': 소문자·숫자·하이픈만 허용")
            if name.startswith("-") or name.endswith("-") or "--" in name:
                rep.error(f"name '{name}': 하이픈으로 시작/끝나거나 연속 하이픈 포함")
            if len(name) > NAME_MAX:
                rep.error(f"name이 {len(name)}자 — 최대 {NAME_MAX}자")
            for word in RESERVED_WORDS:
                if word in name.lower():
                    rep.error(f"name '{name}': 예약어 '{word}' 포함")
            if XML_TAG.search(name):
                rep.error(f"name '{name}': XML 태그 포함")
            if name != rep.skill_dir.name:
                rep.warn(
                    f"name '{name}' != 디렉터리 '{rep.skill_dir.name}' — "
                    "개인/프로젝트 스킬의 명령어 이름은 디렉터리에서 온다"
                )

    # --- description ------------------------------------------------------
    if "description" not in fm:
        rep.error("description 없음 — 트리거의 유일한 근거")
    else:
        desc = fm["description"]
        if not isinstance(desc, str):
            rep.error(f"description이 문자열이 아님: {type(desc).__name__}")
        else:
            desc = desc.strip()
            if not desc:
                rep.error("description이 비어 있음")
            if "<" in desc or ">" in desc:
                rep.error("description에 꺾쇠(< >) 포함 — 내부 포맷 위장 방지를 위해 금지")
            if len(desc) > DESCRIPTION_MAX:
                rep.error(f"description이 {len(desc)}자 — 최대 {DESCRIPTION_MAX}자")
            _check_description_quality(desc, fm, rep)

    # --- compatibility ----------------------------------------------------
    compat = fm.get("compatibility")
    if compat is not None:
        if not isinstance(compat, str):
            rep.error(f"compatibility가 문자열이 아님: {type(compat).__name__}")
        elif len(compat) > COMPATIBILITY_MAX:
            rep.error(
                f"compatibility가 {len(compat)}자 — 최대 {COMPATIBILITY_MAX}자"
            )

    # --- metadata ---------------------------------------------------------
    meta = fm.get("metadata")
    if meta is not None:
        if not isinstance(meta, dict):
            rep.error("metadata는 YAML 매핑이어야 함 (아니면 값이 버려진다)")
        else:
            clash = set(meta.keys()) & (SPEC_FIELDS | CLAUDE_CODE_FIELDS)
            if clash:
                rep.warn(
                    "metadata 키가 프론트매터 필드명과 겹침: "
                    + ", ".join(sorted(clash))
                )

    # --- context: fork ----------------------------------------------------
    if fm.get("context") == "fork" and "agent" not in fm:
        rep.note("context: fork인데 agent 미지정 — general-purpose로 실행된다")


def _check_description_quality(desc: str, fm: dict, rep: Report) -> None:
    """[표준] 수준의 description 점검. 전부 WARN/note."""
    lowered = desc.lower()

    # 1인칭/2인칭은 시스템 프롬프트에 주입될 때 발견을 망가뜨린다.
    for bad in ("i can help", "i will ", "you can use this", "we will "):
        if bad in lowered:
            rep.warn(f"description이 3인칭이 아님 ('{bad.strip()}')")
            break

    # "언제 쓰는가"가 없으면 트리거 근거가 절반뿐이다.
    if not any(k in lowered for k in ("use when", "use this", "trigger", "when the user", "when you")):
        rep.warn("description에 '언제 쓰는가'가 안 보임 — 무엇+언제를 모두 담을 것")

    when_to_use = fm.get("when_to_use")
    combined = len(desc) + (len(when_to_use) if isinstance(when_to_use, str) else 0)
    if combined > LISTING_TRUNCATE_AT:
        rep.warn(
            f"description+when_to_use가 {combined}자 — 스킬 목록에서 "
            f"{LISTING_TRUNCATE_AT}자에서 잘린다. 핵심 유스케이스를 앞으로"
        )


# ---------------------------------------------------------------------------
# 본문 / 번들
# ---------------------------------------------------------------------------


def check_body(body: str, rep: Report) -> None:
    lines = body.splitlines()
    if len(lines) > BODY_MAX_LINES:
        rep.warn(
            f"SKILL.md 본문 {len(lines)}줄 — 권고 상한 {BODY_MAX_LINES}줄. "
            "계층을 하나 더 만들고 포인터를 남길 것"
        )

    # 윈도우 경로는 유닉스에서 깨진다.
    for i, line in enumerate(lines, 1):
        if re.search(r"\b(?:scripts|references|reference|assets)\\[\w.]", line):
            rep.warn(f"{i}행: 역슬래시 경로 — 항상 슬래시를 쓸 것")
            break


def check_bundle(skill_dir: Path, body: str, rep: Report) -> None:
    packaged: list[Path] = []
    total = 0
    extra_skill_md: list[str] = []

    for path in sorted(skill_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(skill_dir)
        if not is_packaged(rel):
            continue
        packaged.append(rel)
        try:
            total += path.stat().st_size
        except OSError:
            pass
        if path.name == "SKILL.md" and rel != Path("SKILL.md"):
            extra_skill_md.append(str(rel))

    if extra_skill_md:
        rep.error(
            "SKILL.md가 여러 개: " + ", ".join(extra_skill_md) + " — "
            "스킬 하나에 정확히 하나여야 한다. 별개 스킬이면 플러그인으로, "
            "보조 문서면 references/<topic>.md로"
        )

    if total >= MAX_TOTAL_BYTES:
        rep.error(f"총 {total / 1024 / 1024:.1f}MB — 업로드 상한 30MB 미만")

    # 참조 파일이 SKILL.md에서 언급되는지, 100줄 넘으면 목차가 있는지.
    # 마크다운 링크만 보면 "read FORMS.md and follow its instructions" 같은 산문
    # 참조를 놓치므로, 본문 어디든 파일명이 나오면 언급된 것으로 친다.
    for rel in packaged:
        if rel.suffix != ".md" or rel == Path("SKILL.md"):
            continue
        as_posix = rel.as_posix()
        referenced = as_posix in body or rel.name in body
        if not referenced:
            rep.warn(f"{as_posix}: SKILL.md에서 링크되지 않음 — 읽히지 않을 가능성")
        try:
            n = len((skill_dir / rel).read_text(encoding="utf-8", errors="replace").splitlines())
        except OSError:
            continue
        if n > TOC_REQUIRED_LINES:
            head = (skill_dir / rel).read_text(encoding="utf-8", errors="replace")[:2000].lower()
            if "## contents" not in head and "## 목차" not in head and "table of contents" not in head:
                rep.warn(
                    f"{as_posix}: {n}줄인데 목차 없음 — "
                    f"{TOC_REQUIRED_LINES}줄 초과 참조 파일엔 맨 위에 목차를 둘 것"
                )

    rep.note(f"패키징 대상 {len(packaged)}개 파일, {total / 1024:.0f}KB")


# ---------------------------------------------------------------------------
# 엔트리포인트
# ---------------------------------------------------------------------------


def validate(skill_dir: Path, portable: bool = False) -> Report:
    rep = Report(skill_dir)

    if not skill_dir.is_dir():
        rep.error("디렉터리가 아님")
        return rep

    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        rep.error("SKILL.md 없음")
        return rep

    text = skill_md.read_text(encoding="utf-8", errors="replace")
    raw_fm, body = split_frontmatter(text)

    if raw_fm is None:
        rep.error(
            "프론트매터를 찾지 못함 — 여는 `---`가 파일의 첫 줄이어야 한다. "
            "아니면 `---`를 포함한 파일 전체가 본문으로 취급된다"
        )
        check_body(body, rep)
        check_bundle(skill_dir, body, rep)
        return rep

    if yaml is None:
        rep.error("PyYAML이 없어 프론트매터를 파싱할 수 없음 (pip install pyyaml)")
        return rep

    try:
        fm = yaml.safe_load(raw_fm)
    except yaml.YAMLError as exc:
        rep.error(f"프론트매터 YAML 파싱 실패: {exc}")
        return rep

    if not isinstance(fm, dict):
        rep.error("프론트매터가 YAML 매핑이 아님")
        return rep

    check_frontmatter(fm, rep, portable)
    check_body(body, rep)
    check_bundle(skill_dir, body, rep)
    return rep


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Agent Skills 검증기 (Agent_Skills_작성표준.md §4.1)"
    )
    parser.add_argument("skill_dirs", nargs="+", type=Path)
    parser.add_argument(
        "--portable",
        action="store_true",
        help="P0(이식형) 등급으로 검사 — Claude Code 전용 필드를 ERROR로 올린다",
    )
    parser.add_argument("--quiet", action="store_true", help="ERROR만 출력")
    args = parser.parse_args(argv)

    failed = 0
    for skill_dir in args.skill_dirs:
        rep = validate(skill_dir, portable=args.portable)
        print(rep.render(quiet=args.quiet))
        if not rep.ok:
            failed += 1

    if len(args.skill_dirs) > 1:
        print(f"\n{len(args.skill_dirs)}개 중 {failed}개 실패")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
