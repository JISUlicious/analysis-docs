#!/usr/bin/env python3
"""2단계 — Codex CLI로 조건 A / A′ / B′ 각 3회 실행.

워크스페이스는 저장소 밖(WORK_ROOT)에 만든다. 저장소 안에 두면 피험 에이전트가 상위 디렉터리를
탐색하다 results/baseline_*.json(정답)이나 이 README를 읽을 수 있다.

사용법:
    python run_codex.py --smoke                 # 환경 점검 1회 (pandas 가용성·스킬 발견 경로)
    python run_codex.py                         # 본 실행 9회, 동시 3
    python run_codex.py --only A1,B'2           # 일부만 재실행
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
SKILL = HERE / "skill" / "sensor-defect-screening"
RUNS = HERE / "runs"
VENV_BIN = HERE / ".venv" / "bin"
WORK_ROOT = Path(os.environ.get(
    "SECOM_WORK_ROOT",
    "/private/tmp/claude-501/-Users-jisu-data-workspace-vibe-analysis-docs/"
    "1732be2a-c19b-469c-9436-a23ff837a1a2/scratchpad/secom-runs"))

MODEL = "gpt-5.6-terra"
EFFORT = "high"
TIMEOUT_SEC = 45 * 60
REPEATS = 3
CONDITIONS = {  # name: (labels file, skill?)
    "A": ("labels_real.csv", False),
    "A'": ("labels_permuted.csv", False),
    "B'": ("labels_permuted.csv", True),
}
# 피험 산출물 중 저장소로 복사할 것 (입력 CSV는 제외)
KEEP_SUFFIXES = {".md", ".json", ".py", ".txt", ".csv"}
INPUTS = {"sensors.csv", "labels.csv"}


def agent_zdotdir() -> Path:
    """피험 셸 전용 zsh 설정 디렉터리.

    스모크 테스트에서 확인한 함정: Codex는 명령을 `/bin/zsh -lc`(로그인 셸)로 실행하고,
    macOS /etc/zprofile의 path_helper가 PATH를 재구성하면서 기존 PATH 항목을 맨 뒤로 보낸다.
    그래서 PATH에 venv를 앞세워도 /usr/bin/python3(패키지 없음)가 이긴다. 로그인 셸이 읽는
    .zprofile을 ZDOTDIR로 대체해 venv를 다시 앞에 둔다. 사용자 개인 zsh 설정에서도 격리된다.
    """
    z = WORK_ROOT / "zdotdir"
    z.mkdir(parents=True, exist_ok=True)
    (z / ".zprofile").write_text(f'export PATH="{VENV_BIN}:$PATH"\n')
    return z


def codex_cmd(ws: Path, prompt: str, last_msg: Path) -> list[str]:
    return [
        "codex", "exec",
        "--ignore-user-config", "--ignore-rules",
        "--skip-git-repo-check", "--ephemeral",
        "--disable", "shell_snapshot",
        "-c", f'shell_environment_policy.set={{ZDOTDIR="{agent_zdotdir()}"}}',
        "-s", "workspace-write",
        "-m", MODEL, "-c", f'model_reasoning_effort="{EFFORT}"',
        "-c", 'web_search="disabled"', "-c", "tools.web_search=false",
        "-C", str(ws), "--json", "-o", str(last_msg),
        prompt,
    ]


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def write_manifest():
    RUNS.mkdir(exist_ok=True)
    frozen = [HERE / "prompts" / "task.md", HERE / "prompts" / "rubric.md",
              SKILL / "SKILL.md", SKILL / "scripts" / "screen.py", SKILL / "scripts" / "check_claims.py",
              DATA / "sensors.csv", DATA / "labels_real.csv", DATA / "labels_permuted.csv"]
    ver = subprocess.run(["codex", "--version"], capture_output=True, text=True).stdout.strip()
    manifest = {
        "frozen_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "codex_version": ver, "model": MODEL, "reasoning_effort": EFFORT,
        "sha256": {str(p.relative_to(HERE)): sha256(p) for p in frozen},
    }
    (RUNS / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    return manifest


def prepare_ws(name: str, labels_file: str, with_skill: bool) -> Path:
    ws = WORK_ROOT / name.replace("'", "p") / "ws"
    if ws.parent.exists():
        shutil.rmtree(ws.parent)
    ws.mkdir(parents=True)
    shutil.copy(DATA / "sensors.csv", ws / "sensors.csv")
    shutil.copy(DATA / labels_file, ws / "labels.csv")
    if with_skill:
        shutil.copytree(SKILL, ws / ".agents" / "skills" / SKILL.name)
    return ws


def run_one(name: str, labels_file: str, with_skill: bool, prompt: str) -> dict:
    ws = prepare_ws(name, labels_file, with_skill)
    out = RUNS / name.replace("'", "p")
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    env = dict(os.environ, PATH=f"{VENV_BIN}:{os.environ['PATH']}")
    t0 = time.time()
    with open(out / "events.jsonl", "w") as ev, open(out / "stderr.txt", "w") as er:
        try:
            rc = subprocess.run(codex_cmd(ws, prompt, out / "last_message.md"),
                                stdin=subprocess.DEVNULL, stdout=ev, stderr=er, env=env, timeout=TIMEOUT_SEC).returncode
        except subprocess.TimeoutExpired:
            rc = "timeout"
    elapsed = round(time.time() - t0)
    kept = []
    for p in sorted(ws.rglob("*")):
        rel = p.relative_to(ws)
        if p.is_file() and p.name not in INPUTS and p.suffix in KEEP_SUFFIXES and ".agents" not in rel.parts:
            dest = out / "workspace" / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            if p.stat().st_size <= 2_000_000:
                shutil.copy(p, dest)
                kept.append(str(rel))
    meta = {"run": name, "labels": labels_file, "skill": with_skill, "exit": rc,
            "elapsed_sec": elapsed, "workspace_files": kept}
    (out / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))
    print(f"[done] {name}: exit={rc} {elapsed}s files={kept}", flush=True)
    return meta


def smoke():
    ws = WORK_ROOT / "smoke" / "ws"
    if ws.parent.exists():
        shutil.rmtree(ws.parent)
    for d, n in ((".agents/skills", "probe-alpha"), (".codex/skills", "probe-beta")):
        sk = ws / d / n
        sk.mkdir(parents=True)
        (sk / "SKILL.md").write_text(
            f"---\nname: {n}\ndescription: Probe skill {n} used only to test skill discovery paths. "
            f"Use when asked to list available skills.\n---\n\n# {n}\n\nReply with the word {n}.\n")
    prompt = ("Run exactly this command and show its output: "
              "python3 -c \"import pandas, scipy, statsmodels, sklearn; print('libs-ok', pandas.__version__)\"\n"
              "Then list the names of every skill available to you in this session, names only, one per line. "
              "Do nothing else.")
    out = WORK_ROOT / "smoke"
    env = dict(os.environ, PATH=f"{VENV_BIN}:{os.environ['PATH']}")
    with open(out / "events.jsonl", "w") as ev, open(out / "stderr.txt", "w") as er:
        rc = subprocess.run(codex_cmd(ws, prompt, out / "last_message.md"),
                            stdin=subprocess.DEVNULL, stdout=ev, stderr=er, env=env, timeout=600).returncode
    print(f"smoke exit={rc}\n--- last message ---")
    print((out / "last_message.md").read_text() if (out / "last_message.md").exists() else "(none)")
    print(f"--- events: {out / 'events.jsonl'}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--only", default="")
    ap.add_argument("--parallel", type=int, default=3)
    a = ap.parse_args()
    if a.smoke:
        return smoke()

    prompt = (HERE / "prompts" / "task.md").read_text()
    m = write_manifest()
    print(f"manifest frozen at {m['frozen_at_utc']} ({m['codex_version']}, {MODEL}/{EFFORT})")
    jobs = [(f"{c}{i}", lab, sk) for i in range(1, REPEATS + 1) for c, (lab, sk) in CONDITIONS.items()]
    if a.only:
        wanted = set(a.only.split(","))
        jobs = [j for j in jobs if j[0] in wanted]
    with ThreadPoolExecutor(a.parallel) as pool:
        metas = list(pool.map(lambda j: run_one(*j, prompt), jobs))
    (RUNS / "summary_meta.json").write_text(json.dumps(metas, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
