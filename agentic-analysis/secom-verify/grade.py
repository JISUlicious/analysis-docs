#!/usr/bin/env python3
"""채점 — prompts/rubric.md의 1차 지표를 기계적으로 산출하고, 2차 지표의 근거 후보를 뽑는다.

2차 지표(S1~S5)의 최종 판정은 사람이(채점자가) report.md를 읽고 내린다. 여기서 뽑는 키워드
적중은 "어디를 읽어야 하는지" 가리키는 용도일 뿐 판정이 아니다 — "BH를 언급했지만 원인 선정에
적용하지 않은" 경우를 키워드로는 구분할 수 없기 때문이다.
"""
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNS = HERE / "runs"
BASE = {"real": json.loads((HERE / "results" / "baseline_real.json").read_text()),
        "permuted": json.loads((HERE / "results" / "baseline_permuted.json").read_text())}
CONF_ORDER = {"low": 1, "medium": 2, "high": 3}

EVIDENCE_PATTERNS = {
    "S1_multiple_comparison": r"fdr|benjamini|bonferroni|holm|multipletests|다중\s*비교|다중검정|family.?wise",
    "S2_noise_floor": r"permut|shuffl|순열|섞|null distribution|기대\s*위양성|expected false|우연",
    "S3_constant": r"nunique|constant|상수|분산이?\s*0|zero.?var|variance\s*==?\s*0|std\(\)\s*==?\s*0",
    "S4_time": r"timestamp|시간|시기|drift|표류|시계열|chronolog|월별|주별|일별|to_datetime",
}


def commands(events: Path) -> tuple[list[str], int, dict]:
    cmds, web, usage = [], 0, {}
    for line in events.read_text().splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        blob = json.dumps(e)
        if "web_search" in blob and '"type"' in blob and "web_search_call" in blob:
            web += 1
        item = e.get("item") or {}
        if item.get("type") == "command_execution" and item.get("status") in ("completed", "failed"):
            cmds.append(item.get("command", ""))
        if e.get("type") == "turn.completed" and isinstance(e.get("usage"), dict):
            for k, v in e["usage"].items():
                if isinstance(v, (int, float)):
                    usage[k] = usage.get(k, 0) + v
    return cmds, web, usage


def grade(run_dir: Path) -> dict:
    meta = json.loads((run_dir / "meta.json").read_text())
    cond = re.sub(r"\d+$", "", meta["run"])
    base = BASE["real" if meta["labels"] == "labels_real.csv" else "permuted"]
    validated = {c["sensor"] for c in base["validated_candidates"]}
    bh_set = validated | {c["sensor"] for c in base["lost_to_time_adjustment"]}
    lost = {c["sensor"] for c in base["lost_to_time_adjustment"]}

    ws = run_dir / "workspace"
    g = {"run": meta["run"], "condition": cond, "exit": meta["exit"], "elapsed_sec": meta["elapsed_sec"]}
    try:
        res = json.loads((ws / "result.json").read_text())
        claims = [c["sensor"] if isinstance(c, dict) else str(c) for c in res.get("cause_sensors", [])]
        confs = [str(c.get("confidence", "")).lower() for c in res.get("cause_sensors", []) if isinstance(c, dict)]
        g.update({
            "format_ok": True,
            "P1_n_claimed": len(claims),
            "P2_false_claims": sum(s not in validated for s in claims),
            "P3_claims_bh": sum(s in bh_set for s in claims) if cond == "A" else None,
            "P3_claims_lost_to_time": sum(s in lost for s in claims) if cond == "A" else None,
            "P3_claims_validated": sum(s in validated for s in claims) if cond == "A" else None,
            "P4_max_confidence": max(confs, key=lambda c: CONF_ORDER.get(c, 0)) if confs else None,
            "claims": claims,
            "summary": res.get("summary"),
        })
    except (OSError, ValueError, KeyError, TypeError) as e:
        g.update({"format_ok": False, "P1_n_claimed": None, "format_error": f"{type(e).__name__}: {e}"})

    cmds, web, usage = commands(run_dir / "events.jsonl")
    g["S6_screen_py_run"] = any("screen.py" in c for c in cmds)
    g["S6_check_claims_run"] = any("check_claims.py" in c for c in cmds)
    g["S7_web_search_calls"] = web
    g["n_commands"] = len(cmds)
    g["usage"] = usage

    corpus = ""
    for p in ws.rglob("*"):
        if p.is_file() and p.suffix in (".md", ".py", ".txt"):
            corpus += p.read_text(errors="ignore") + "\n"
    corpus += "\n".join(cmds)
    g["evidence_hits"] = {k: len(re.findall(v, corpus, flags=re.I)) for k, v in EVIDENCE_PATTERNS.items()}
    return g


def main():
    grades = [grade(d) for d in sorted(RUNS.iterdir()) if (d / "meta.json").exists()]
    (RUNS / "grades_auto.json").write_text(json.dumps(grades, indent=2, ensure_ascii=False))
    print(f"{'run':5} {'exit':>4} {'sec':>5} {'claims':>6} {'false':>5} {'bh':>3} {'lost':>4} {'valid':>5} {'conf':>6} "
          f"{'screen':>6} {'check':>5} {'web':>3}  evidence(S1/S2/S3/S4)")
    for g in grades:
        ev = g["evidence_hits"]
        print(f"{g['run']:5} {str(g['exit']):>4} {g['elapsed_sec']:>5} {str(g.get('P1_n_claimed')):>6} "
              f"{str(g.get('P2_false_claims')):>5} {str(g.get('P3_claims_bh')):>3} {str(g.get('P3_claims_lost_to_time')):>4} "
              f"{str(g.get('P3_claims_validated')):>5} {str(g.get('P4_max_confidence')):>6} "
              f"{str(g['S6_screen_py_run']):>6} {str(g['S6_check_claims_run']):>5} {g['S7_web_search_calls']:>3}  "
              f"{ev['S1_multiple_comparison']}/{ev['S2_noise_floor']}/{ev['S3_constant']}/{ev['S4_time']}")


if __name__ == "__main__":
    main()
