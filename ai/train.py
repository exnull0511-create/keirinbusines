"""
全データでAIを学習し、data ブランチの models/latest.pkl に保存。
  python -m ai.train --data DIR
"""
import argparse
import pickle
from datetime import datetime, timezone
from pathlib import Path

from sklearn.ensemble import HistGradientBoostingClassifier

from .common import load_races
from .features import CATS, build

DROP = {"race", "date", "car", "y1", "y2", "y3", "month"}
B = 0.5  # 市場の確率をAIでどれだけ補正するか（検証で最も当てはまりが良かった強さ）


def features_of(df):
    return [c for c in df.columns if c not in DROP]


def fit(df, target, feats):
    m = HistGradientBoostingClassifier(max_iter=400, learning_rate=0.03, max_depth=5, min_samples_leaf=80,
                                       l2_regularization=1.0, categorical_features="from_dtype", random_state=0)
    return m.fit(df[feats], df[target])


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, required=True)
    a = ap.parse_args(argv)
    rs = load_races(a.data)
    df, _ = build(rs)
    feats = features_of(df)
    models = [fit(df, t, feats) for t in ("y1", "y2", "y3")]
    cats = {c: list(df[c].cat.categories) for c in CATS}
    out = a.data / "models"
    out.mkdir(parents=True, exist_ok=True)
    meta = {"models": models, "feats": feats, "categories": cats, "b": B, "n_races": len(rs),
            "last_date": max(r["date"] for r in rs), "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    with open(out / "latest.pkl", "wb") as f:
        pickle.dump(meta, f)
    print("学習完了", meta["n_races"], "レース", meta["last_date"])


if __name__ == "__main__":
    main()
