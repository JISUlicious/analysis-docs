#!/usr/bin/env python3
"""검토 스크립트 — 결론 파일의 원인 센서가 스크리닝 결과로 뒷받침되는지 확인한다.

생성(분석·서술)과 검증을 분리하기 위한 reviewer 단계다. 원인 목록의 각 센서가
screen.json의 validated_candidates에 있어야 통과한다.

사용법:
    python check_claims.py --claims result.json --screen screen.json [--key cause_sensors]

종료 코드: 0 = 통과, 1 = 뒷받침되지 않는 주장 있음, 2 = 입력 오류
"""
import argparse
import json
import sys


def claimed_sensors(doc: dict, key: str) -> list[str]:
    items = doc.get(key)
    if items is None:
        raise KeyError(f"'{key}' not found in claims file. Available keys: {sorted(doc)}")
    out = []
    for it in items:
        out.append(it["sensor"] if isinstance(it, dict) else str(it))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--claims", required=True)
    ap.add_argument("--screen", required=True)
    ap.add_argument("--key", default="cause_sensors")
    a = ap.parse_args()

    try:
        claims = claimed_sensors(json.load(open(a.claims)), a.key)
        screen = json.load(open(a.screen))
    except (OSError, ValueError, KeyError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    validated = {c["sensor"] for c in screen["validated_candidates"]}
    lost = {c["sensor"] for c in screen["lost_to_time_adjustment"]}
    verdict = screen["verdict"]

    problems = []
    for s in claims:
        if s in validated:
            continue
        if s in lost:
            problems.append(f"{s}: BH 보정은 통과했지만 시간 구간 보정에서 사라짐 — 시기 효과일 수 있음")
        else:
            problems.append(f"{s}: BH-FDR 보정을 통과하지 못함 — 다중비교 노이즈와 구별되지 않음")

    print(f"verdict: {verdict} | validated candidates: {sorted(validated) or '없음'}")
    if problems:
        print(f"FAIL — 스크리닝이 뒷받침하지 않는 원인 주장 {len(problems)}건:")
        for p in problems:
            print(f"  - {p}")
        print("원인 목록에서 제외하고 필요하면 본문에 '참고'로만 언급한 뒤 다시 실행하라.")
        return 1
    print(f"PASS — 원인 목록 {len(claims)}개 모두 스크리닝으로 뒷받침됨")
    return 0


if __name__ == "__main__":
    sys.exit(main())
