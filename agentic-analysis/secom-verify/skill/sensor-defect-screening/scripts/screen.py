#!/usr/bin/env python3
"""센서-불량 연관 스크리닝 — 다중비교·노이즈 바닥·시간 교란을 한 번에 점검한다.

수백 개 센서를 양품/불량 라벨과 하나씩 대조하면, 연관이 전혀 없어도 α=0.05에서
센서의 약 5%가 "유의"하게 나온다. 이 스크립트는 그 착시를 걸러내기 위해 네 가지를 계산한다.

  1. 프로파일   : 결측 비율, 상수 컬럼, 검정 가능한 센서 수
  2. 단변량 검정 : Mann-Whitney U (분포 가정 없음) + BH-FDR / Bonferroni 보정
  3. 노이즈 바닥 : 라벨을 무작위로 섞었을 때 "유의" 센서가 몇 개 나오는지 (순열)
  4. 시간 교란   : 불량이 특정 시기에 몰렸는지, 시간 구간 층화 검정(van Elteren) 후에도
                  연관이 남는지. 몰려 있다면 시간에 따라 표류하는 센서는 전부 불량과
                  상관이 나온다.

사용법:
    python screen.py --sensors sensors.csv --labels labels.csv --out screen.json

labels.csv 는 label(-1=양품, 1=불량), timestamp 두 열. 행 순서는 sensors.csv 와 같아야 한다.
"""
from __future__ import annotations

import argparse
import json
import sys

import numpy as np
import pandas as pd
from scipy import stats
from statsmodels.stats.multitest import multipletests

ALPHA = 0.05
# 불량/양품 각각 최소 이 개수의 비결측 관측이 있어야 검정한다. 5 미만이면 정규근사가 무의미하다.
MIN_PER_GROUP = 5
# 최빈값이 아닌 관측이 이보다 적으면 "사실상 상수"로 보고 검정하지 않는다.
# SECOM 실측: 1,561행 중 1~2행만 값이 다른 센서가 있고, 그 몇 행이 우연히 불량 행에 걸리면
# 점근 근사 p값이 극단적으로 작아진다. 라벨을 섞은 영가설 1,000회에서 BH 허위 유의의 78%가
# 이런 센서 7개에서 나왔다 (README §3-2). 정규근사의 적용 조건 위반이다.
MIN_MINORITY = 10


# ---------------------------------------------------------------------------
# 순위 기반 Mann-Whitney — 순열을 수백 번 돌려야 하므로 scipy 루프 대신 벡터화한다.
# scipy.stats.mannwhitneyu(method="asymptotic", use_continuity=True)와 같은 값을 낸다
# (baseline.py가 실제 라벨에서 교차검증한다).
# ---------------------------------------------------------------------------
class RankedFeature:
    def __init__(self, values: np.ndarray):
        self.mask = ~np.isnan(values)
        v = values[self.mask]
        self.n = v.size
        self.ranks = stats.rankdata(v)
        _, counts = np.unique(v, return_counts=True)
        self.tie_term = float(np.sum(counts.astype(float) ** 3 - counts))
        self.constant = counts.size <= 1
        self.minority = int(self.n - counts.max()) if counts.size else 0
        self.near_constant = not self.constant and self.minority < MIN_MINORITY

    def pvalues(self, labels: np.ndarray) -> np.ndarray:
        """labels: (P, N) bool — 행마다 하나의 라벨 벡터. 반환: (P,) 양측 p값."""
        ym = labels[:, self.mask]
        n1 = ym.sum(axis=1).astype(float)
        n0 = self.n - n1
        r1 = ym.astype(float) @ self.ranks
        u1 = r1 - n1 * (n1 + 1) / 2
        mu = n1 * n0 / 2
        n = float(self.n)
        var = n1 * n0 / 12 * ((n + 1) - self.tie_term / (n * (n - 1)))
        with np.errstate(divide="ignore", invalid="ignore"):
            z = (np.abs(u1 - mu) - 0.5) / np.sqrt(var)
        p = np.clip(2 * stats.norm.sf(np.maximum(z, 0)), 0, 1)
        invalid = (n1 < MIN_PER_GROUP) | (n0 < MIN_PER_GROUP) | (var <= 0)
        p[invalid] = np.nan
        return p

    def auc(self, label: np.ndarray) -> float:
        """불량 쪽 값이 클 확률. 0.5 = 무관."""
        ym = label[self.mask]
        n1 = ym.sum()
        n0 = self.n - n1
        return float((self.ranks[ym].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def van_elteren_p(values: np.ndarray, label: np.ndarray, strata: np.ndarray) -> float:
    """시간 구간(strata) 안에서만 비교하는 층화 Wilcoxon. 시기 차이로 생긴 연관을 제거한다."""
    t_sum, v_sum = 0.0, 0.0
    for s in np.unique(strata):
        sel = (strata == s) & ~np.isnan(values)
        v, y = values[sel], label[sel]
        n, n1 = v.size, int(y.sum())
        n0 = n - n1
        if n1 == 0 or n0 == 0 or n < 2:
            continue
        r = stats.rankdata(v)
        _, c = np.unique(v, return_counts=True)
        tie = float(np.sum(c.astype(float) ** 3 - c))
        var = n1 * n0 / 12 * ((n + 1) - tie / (n * (n - 1)))
        if var <= 0:
            continue
        w = 1.0 / (n + 1)
        t_sum += w * (r[y].sum() - n1 * (n + 1) / 2)
        v_sum += w * w * var
    if v_sum <= 0:
        return float("nan")
    return float(2 * stats.norm.sf(abs(t_sum) / np.sqrt(v_sum)))


def bh(pvals: np.ndarray) -> np.ndarray:
    """NaN을 제외하고 BH-FDR 기각 여부를 반환한다."""
    out = np.zeros(pvals.shape, dtype=bool)
    ok = ~np.isnan(pvals)
    if ok.any():
        out[ok] = multipletests(pvals[ok], alpha=ALPHA, method="fdr_bh")[0]
    return out


def screen(sensors: pd.DataFrame, labels: pd.DataFrame, perms: int, seed: int, time_bins: int) -> dict:
    names = list(sensors.columns)
    x = sensors.to_numpy(dtype=float)
    y = labels["label"].to_numpy() == 1
    ts = pd.to_datetime(labels["timestamp"])
    N = len(y)

    # 1. 프로파일 --------------------------------------------------------------
    feats = [RankedFeature(x[:, j]) for j in range(x.shape[1])]
    nan_ratio = np.isnan(x).mean(axis=0)
    constant = [names[j] for j, f in enumerate(feats) if f.constant]
    near_constant = [names[j] for j, f in enumerate(feats) if f.near_constant]
    skip = np.array([f.constant or f.near_constant for f in feats])
    real_p = np.array([np.nan if skip[j] else f.pvalues(y[None, :])[0] for j, f in enumerate(feats)])
    testable = ~np.isnan(real_p)

    # 2. 단변량 + 보정 ---------------------------------------------------------
    bh_real = bh(real_p)
    bonf_real = np.zeros_like(bh_real)
    bonf_real[testable] = real_p[testable] < ALPHA / testable.sum()
    raw_sig = int(np.nansum(real_p < ALPHA))

    # 3. 노이즈 바닥 (라벨 순열) ------------------------------------------------
    rng = np.random.default_rng(seed)
    perm_labels = np.stack([rng.permutation(y) for _ in range(perms)])
    perm_p = np.full((perms, len(feats)), np.nan)
    for j, f in enumerate(feats):
        if testable[j]:
            perm_p[:, j] = f.pvalues(perm_labels)
    perm_raw = np.nansum(perm_p < ALPHA, axis=1)
    perm_bh = np.array([bh(perm_p[i]).sum() for i in range(perms)])
    # Westfall-Young minP: 섞은 라벨마다의 최소 p값 분포 5백분위 = 상관·동점을 반영한 FWER 5% 문턱
    minp_threshold = float(np.percentile(np.nanmin(perm_p, axis=1), 5))

    # 4. 시간 교란 -------------------------------------------------------------
    order = np.argsort(ts.to_numpy(), kind="stable")
    strata = np.empty(N, dtype=int)
    strata[order] = np.minimum(np.arange(N) * time_bins // N, time_bins - 1)
    table = np.array([[np.sum((strata == b) & y), np.sum((strata == b) & ~y)] for b in range(time_bins)])
    chi2, chi_p, _, _ = stats.chi2_contingency(table)
    time_rank = np.empty(N)
    time_rank[order] = np.arange(N)

    ve_p = np.full(len(feats), np.nan)
    for j in np.flatnonzero(testable):
        ve_p[j] = van_elteren_p(x[:, j], y, strata)
    bh_time = bh(ve_p)

    validated = bh_real & bh_time
    lost_to_time = bh_real & ~bh_time

    def row(j: int) -> dict:
        m = ~np.isnan(x[:, j])
        rho = stats.spearmanr(x[m, j], time_rank[m]).statistic
        return {
            "sensor": names[j],
            "p_raw": float(real_p[j]),
            "p_time_adjusted": float(ve_p[j]),
            "bh_significant": bool(bh_real[j]),
            "bh_significant_time_adjusted": bool(bh_time[j]),
            "auc_fail_higher": round(feats[j].auc(y), 3),
            "spearman_with_time": round(float(rho), 3),
            "nan_ratio": round(float(nan_ratio[j]), 3),
        }

    ranked = np.argsort(np.where(testable, real_p, np.inf))
    p95_raw = float(np.percentile(perm_raw, 95))
    if raw_sig <= p95_raw and not bh_real.any():
        verdict = "NO_EVIDENCE"
    elif not validated.any():
        verdict = "NO_VALIDATED_CANDIDATES"
    else:
        verdict = "CANDIDATES"

    return {
        "verdict": verdict,
        "profile": {
            "rows": N,
            "sensors": len(names),
            "fails": int(y.sum()),
            "fail_rate": round(float(y.mean()), 4),
            "constant_sensors": len(constant),
            "near_constant_sensors_excluded": len(near_constant),
            "sensors_over_50pct_nan": int(np.sum(nan_ratio > 0.5)),
            "testable_sensors": int(testable.sum()),
        },
        "univariate": {
            "test": "Mann-Whitney U, two-sided, asymptotic with continuity",
            "raw_p_lt_0.05": raw_sig,
            "bh_fdr_significant": int(bh_real.sum()),
            "bonferroni_significant": int(bonf_real.sum()),
        },
        "noise_floor": {
            "permutations": perms,
            "seed": seed,
            "raw_p_lt_0.05_mean": round(float(perm_raw.mean()), 1),
            "raw_p_lt_0.05_p95": p95_raw,
            "raw_p_lt_0.05_max": int(perm_raw.max()),
            "bh_significant_mean": round(float(perm_bh.mean()), 3),
            "bh_significant_max": int(perm_bh.max()),
            "permutations_with_any_bh_hit": int(np.sum(perm_bh > 0)),
            "observed_raw_count_empirical_p": round(float((1 + np.sum(perm_raw >= raw_sig)) / (perms + 1)), 4),
            "fwer_minP_threshold": minp_threshold,
            "sensors_below_minP_threshold": int(np.nansum(real_p < minp_threshold)),
        },
        "time_structure": {
            "bins": time_bins,
            "fails_per_bin": table[:, 0].tolist(),
            "chi2_p_fail_rate_homogeneous": float(chi_p),
            "bh_significant_time_adjusted": int(bh_time.sum()),
            "bh_significant_lost_after_time_adjustment": int(lost_to_time.sum()),
        },
        "validated_candidates": [row(j) for j in ranked if validated[j]],
        "lost_to_time_adjustment": [row(j) for j in ranked if lost_to_time[j]],
        "top10_by_raw_p": [row(j) for j in ranked[:10]],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sensors", required=True)
    ap.add_argument("--labels", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--perms", type=int, default=200)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--time-bins", type=int, default=10)
    a = ap.parse_args()

    sensors = pd.read_csv(a.sensors)
    labels = pd.read_csv(a.labels)
    if len(sensors) != len(labels):
        print(f"ERROR: sensors {len(sensors)} rows vs labels {len(labels)} rows", file=sys.stderr)
        return 2
    if not set(labels["label"].unique()) <= {-1, 1}:
        print(f"ERROR: label values must be -1/1, got {sorted(labels['label'].unique())}", file=sys.stderr)
        return 2

    result = screen(sensors, labels, a.perms, a.seed, a.time_bins)
    with open(a.out, "w") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    u, nf, ts = result["univariate"], result["noise_floor"], result["time_structure"]
    print(f"verdict: {result['verdict']}")
    print(f"raw p<0.05: {u['raw_p_lt_0.05']} sensors  |  noise floor (label shuffles): "
          f"mean {nf['raw_p_lt_0.05_mean']}, 95th pct {nf['raw_p_lt_0.05_p95']}")
    print(f"BH-FDR significant: {u['bh_fdr_significant']}  |  after time adjustment: "
          f"{ts['bh_significant_time_adjusted']}  |  validated: {len(result['validated_candidates'])}")
    print(f"fail rate homogeneous over time? chi2 p={ts['chi2_p_fail_rate_homogeneous']:.3g}")
    print(f"written: {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
