#!/usr/bin/env python3
"""1단계 기준선 — 에이전트 없이 채점용 정답을 확정한다.

  1. screen.py의 벡터화 Mann-Whitney가 scipy와 같은 p값을 내는지 교차검증
  2. 실제 라벨 / 순열 라벨 각각에 스크리닝 실행 (순열 1,000회)
  3. results/baseline_{real,permuted}.json 저장
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE / "skill" / "sensor-defect-screening" / "scripts"))
from screen import RankedFeature, screen  # noqa: E402

DATA, OUT = HERE / "data", HERE / "results"
PERMS = 1000


def crosscheck(sensors: pd.DataFrame, labels: pd.DataFrame) -> float:
    y = labels["label"].to_numpy() == 1
    worst = 0.0
    for col in sensors.columns:
        v = sensors[col].to_numpy(dtype=float)
        f = RankedFeature(v)
        if f.constant:
            continue
        mine = f.pvalues(y[None, :])[0]
        if np.isnan(mine):
            continue
        m = ~np.isnan(v)
        ref = stats.mannwhitneyu(v[m][y[m]], v[m][~y[m]], alternative="two-sided",
                                 method="asymptotic", use_continuity=True).pvalue
        worst = max(worst, abs(mine - ref))
    return worst


def main():
    sensors = pd.read_csv(DATA / "sensors.csv")
    OUT.mkdir(exist_ok=True)
    for name in ("real", "permuted"):
        labels = pd.read_csv(DATA / f"labels_{name}.csv")
        if name == "real":
            err = crosscheck(sensors, labels)
            print(f"[crosscheck] max |p_vectorized - p_scipy| = {err:.2e}")
            assert err < 1e-9, "vectorized Mann-Whitney disagrees with scipy"
        r = screen(sensors, labels, perms=PERMS, seed=0, time_bins=10)
        (OUT / f"baseline_{name}.json").write_text(json.dumps(r, ensure_ascii=False, indent=2))
        u, nf, ts = r["univariate"], r["noise_floor"], r["time_structure"]
        print(f"\n== {name} labels == verdict {r['verdict']}")
        print(f"  profile: {r['profile']}")
        print(f"  raw p<0.05: {u['raw_p_lt_0.05']} | BH: {u['bh_fdr_significant']} | Bonferroni: {u['bonferroni_significant']}")
        print(f"  noise floor ({PERMS} shuffles): raw mean {nf['raw_p_lt_0.05_mean']}, p95 {nf['raw_p_lt_0.05_p95']}, "
              f"max {nf['raw_p_lt_0.05_max']} | BH hits: mean {nf['bh_significant_mean']}, "
              f"any-hit in {nf['permutations_with_any_bh_hit']}/{PERMS} | observed-count empirical p {nf['observed_raw_count_empirical_p']}")
        print(f"  time: fails/bin {ts['fails_per_bin']}, chi2 p {ts['chi2_p_fail_rate_homogeneous']:.3g}, "
              f"BH after time adj {ts['bh_significant_time_adjusted']}, lost {ts['bh_significant_lost_after_time_adjustment']}")
        print(f"  validated: {[c['sensor'] for c in r['validated_candidates']]}")
        print(f"  lost to time: {[c['sensor'] for c in r['lost_to_time_adjustment']]}")


if __name__ == "__main__":
    main()
