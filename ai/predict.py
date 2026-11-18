"""選手ごとの着順確率 → 3連単の全順列の確率（ai/live.py と ai/train.py で共通）"""
import numpy as np


def race_probs(models, b, df, combos):
    """df: features.build の行（対象レースだけ）。戻り値: race_id -> (p, q, odds, 選手ごとの表)"""
    df = df.copy()
    X = df[models["feats"]]
    for k, m in enumerate(models["models"], 1):
        df[f"p{k}"] = m.predict_proba(X)[:, 1]
    g = df.groupby("race")
    df["p1"] = df.p1 / g.p1.transform("sum")
    df["p2"] = df.p2 / g.p2.transform("sum") * 2
    df["p3"] = df.p3 / g.p3.transform("sum") * 3
    df["r1"] = (df.p1 / df.m_win.clip(1e-4)).clip(0.2, 5)
    df["r2"] = ((df.p2 - df.p1).clip(1e-4) / (df.m_top2 - df.m_win).clip(1e-4)).clip(0.2, 5)
    df["r3"] = ((df.p3 - df.p2).clip(1e-4) / (df.m_top3 - df.m_top2).clip(1e-4)).clip(0.2, 5)
    out = {}
    for rid, gg in df.groupby("race"):
        perm, q, odds = combos[rid]
        R = {c: (a, bb, d) for c, a, bb, d in zip(gg.car, gg.r1, gg.r2, gg.r3)}
        f = np.array([R.get(k[0], (1, 1, 1))[0] * R.get(k[1], (1, 1, 1))[1] * R.get(k[2], (1, 1, 1))[2] for k in perm])
        p = q * np.power(f, b)
        out[rid] = (p / p.sum(), q, odds, gg)
    return out
