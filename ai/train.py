"""
全データでAIを学習し、data ブランチの models/latest.pkl に保存。
  python -m ai.train --data DIR [--no-line]

ライン補正（ai/line.py）も一緒に学習する。補正は「学習に使っていないレースでのAIの確率」から作る必要があるので、
直近1年を除いたデータでAIをもう一度学習し、直近1年を予測して、その結果で補正を学習する。
"""
import argparse
import pickle
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier

from . import line
from .common import load_races
from .features import CATS, build
from .predict import race_probs

DROP = {"race", "date", "car", "y1", "y2", "y3", "month"}
B = 0.5  # 市場の確率をAIでどれだけ補正するか（検証で最も当てはまりが良かった強さ）
LINE_DAYS = 365


def features_of(df):
    return [c for c in df.columns if c not in DROP]


def fit(df, target, feats):
    m = HistGradientBoostingClassifier(max_iter=400, learning_rate=0.03, max_depth=5, min_samples_leaf=80,
                                       l2_regularization=1.0, categorical_features="from_dtype", random_state=0)
    return m.fit(df[feats], df[target])


def train_line(rs, df, combos, feats):
    last = max(r["date"] for r in rs)
    cut = (date.fromisoformat(last) - timedelta(days=LINE_DAYS)).isoformat()
    tr, te = df[df.date < cut], df[df.date >= cut]
    models = {"models": [fit(tr, t, feats) for t in ("y1", "y2", "y3")], "feats": feats}
    probs = race_probs(models, B, te, combos)
    M = {r["race_id"]: r for r in rs}
    rng = np.random.default_rng(0)
    X, y, off = [], [], []
    for rid, (p, q, odds, _) in probs.items():
        r = M[rid]
        win = next((x["combo"] for x in r["refund"] if x["bet"] == "3連単"), None)
        if not win:
            continue
        k = line.perm_of(r["odds"]["3連単"]["n"])
        w = tuple(int(c) for c in win.split("-"))
        yy = ((k[:, 0] == w[0]) & (k[:, 1] == w[1]) & (k[:, 2] == w[2])).astype(np.int8)
        ok = odds > 0
        x = line.ticket_matrix(r, p, q, odds)[ok]
        a, b_, c = line.sample(x, yy[ok], p[ok], rng)
        X.append(a); y.append(b_); off.append(c)
    model = line.fit(np.vstack(X), np.concatenate(y), np.concatenate(off))
    print("ライン補正 学習", len(probs), "レース", cut, "〜", last)
    return {"model": model, "feats": line.FEATS, "from": cut, "to": last, "pick": line.PICK}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--no-line", action="store_true")
    a = ap.parse_args(argv)
    rs = load_races(a.data)
    df, combos = build(rs)
    feats = features_of(df)
    models = [fit(df, t, feats) for t in ("y1", "y2", "y3")]
    cats = {c: list(df[c].cat.categories) for c in CATS}
    meta = {"models": models, "feats": feats, "categories": cats, "b": B, "n_races": len(rs),
            "last_date": max(r["date"] for r in rs), "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    if not a.no_line:
        meta["line"] = train_line(rs, df, combos, feats)
    out = a.data / "models"
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "latest.pkl", "wb") as f:
        pickle.dump(meta, f)
    print("学習完了", meta["n_races"], "レース", meta["last_date"])


if __name__ == "__main__":
    main()
