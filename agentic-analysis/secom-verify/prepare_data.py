#!/usr/bin/env python3
"""SECOM 원본 → 피험 에이전트용 익명화 CSV + 순열 라벨.

설계 결정 (README §2):
  - 열 순서를 고정 시드로 섞고 sensor_001.. 로 재명명한다. SECOM은 문헌에서 "feature 59"
    처럼 원본 인덱스로 자주 언급되므로, 원본 순서를 유지하면 에이전트가 데이터 대신
    기억으로 답할 수 있다. 매핑은 data/ 에만 두고 워크스페이스에 넣지 않는다.
  - 파일명·.names 설명서를 워크스페이스에 넣지 않는다 (데이터셋 식별 단서 최소화).
  - 타임스탬프를 dd/mm/yyyy → ISO 로 바꾼다. 날짜 파싱 모호성은 이 실험의 검증 대상이 아니다.
  - 순열 라벨: 라벨 열만 고정 시드로 섞고 타임스탬프 순서는 유지. 불량 104건 그대로.
    어떤 센서도 이 라벨과 실제 연관이 있을 수 없다 = 원인이 존재하지 않는 데이터.
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).parent
RAW = HERE / "data"
COLUMN_SEED = 20260917
LABEL_SEED = 7


def main():
    x = pd.read_csv(RAW / "secom.data", sep=" ", header=None)
    lab = pd.read_csv(RAW / "secom_labels.data", sep=" ", header=None, names=["label", "timestamp"])
    lab["timestamp"] = pd.to_datetime(lab["timestamp"], format="%d/%m/%Y %H:%M:%S").dt.strftime("%Y-%m-%d %H:%M:%S")
    # .names는 "591 attributes"라고 하지만 파일은 모든 행이 590열이다 (라벨 포함 집계로 보임)
    assert x.shape == (1567, 590), x.shape
    assert (lab["label"] == 1).sum() == 104

    order = np.random.default_rng(COLUMN_SEED).permutation(x.shape[1])
    anon = x.iloc[:, order].copy()
    anon.columns = [f"sensor_{i + 1:03d}" for i in range(x.shape[1])]
    mapping = {f"sensor_{i + 1:03d}": int(orig) for i, orig in enumerate(order)}

    perm = lab.copy()
    perm["label"] = np.random.default_rng(LABEL_SEED).permutation(lab["label"].to_numpy())
    assert (perm["label"] == 1).sum() == 104

    anon.to_csv(RAW / "sensors.csv", index=False)
    lab.to_csv(RAW / "labels_real.csv", index=False)
    perm.to_csv(RAW / "labels_permuted.csv", index=False)
    (RAW / "column_mapping.json").write_text(json.dumps(mapping, indent=1))
    agree = float((perm["label"] == lab["label"]).mean())
    print(f"sensors.csv {anon.shape}, labels_real / labels_permuted (fails=104 each)")
    print(f"permuted vs real label agreement: {agree:.3f} (chance for 1:14 imbalance ≈ {1 - 2 * 104 / 1567 * (1 - 104 / 1567):.3f})")


if __name__ == "__main__":
    main()
