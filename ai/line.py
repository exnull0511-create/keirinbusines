"""
ライン補正: AIの3連単確率に、ラインの形とラインそのものの強さで上乗せ・割り引きをかける。

買い目ごとの特徴:
  形      … 1-2着・2-3着・1-3着が同じラインか（3車同ライン/1-2同/2-3同/1-3同/全部別）
  役割    … 1・2・3着の役割（先頭/番手/3番手以降/単騎）
  オッズ帯
  ライン  … 1着と2着の選手それぞれの「自分のラインの強さ」
             合計得点・人数・得点順位・他ラインとの差・先頭の得点・先頭の逃げ＋捲り回数・番手の得点
学習: AIの確率（学習に使っていないレースでの予測）を初期値に置き、LightGBMで補正分だけを学習する。
2025-07〜2026-11の時系列検証で、50〜300倍・期待値1.2以上の回収率 121%（4,601点・的中50）。
"""
from itertools import permutations

import numpy as np

OB = np.array([10, 30, 50, 100, 300, 1000])
LINE_COLS = ["l_len", "l_sum", "l_rank", "l_nlines", "h_rel", "h_nm", "s2_rel", "l_gap"]
FEATS = ["logp", "logpq", "pat", "r1", "r2", "r3", "ob"] + [f"{c}_{k}" for k in (1, 2) for c in LINE_COLS]
CAT_IDX = [2, 3, 4, 5]
# 並行して記録する買い方（名前: 条件）。最初のものが「推奨」（picks）
PICKS = {
    "A_50-300倍_期待値1.2": {"min_odds": 50, "max_odds": 300, "min_ev": 1.2},   # 検証 121%（4,601点）
    "B_10-300倍_期待値1.1": {"min_odds": 10, "max_odds": 300, "min_ev": 1.1},   # 検証 97〜110%（約1.6〜1.9万点）
}
PICK = next(iter(PICKS.values()))
# 締切直前のオッズの動きを使う買い方（ペーパートレードで並行記録。2026-11-24 追加）
#   締切3分前の時点と比べたオッズの変化 r = 今のオッズ ÷ 3分前のオッズ
#   C / D … A / B のうち、直前にオッズが上がった目（r > 1.05、見放された目）を外す
#   E     … AIを使わず、直前に下がり始めた目（10〜300倍、r が 0.70〜0.95）を買う
#   根拠: 自前の監視4日分で、3分前→30秒前に上がった目は 実際/確定の見込み 0.64・回収率34%、
#         0.5〜3割下がった目は 1.25〜2.8・回収率94〜288%（的中36回。まだ偶然の幅が大きい）
LATE = {
    "C_Aから見放された目を除く": {"base": "A_50-300倍_期待値1.2", "max_r": 1.05},
    "D_Bから見放された目を除く": {"base": "B_10-300倍_期待値1.1", "max_r": 1.05},
    "E_直前に下がり始めた目": {"min_odds": 10, "max_odds": 300, "min_r": 0.70, "max_r": 0.95},
}
_PERM = {}


def perm_of(n):
    if n not in _PERM:
        _PERM[n] = np.array(list(permutations(range(1, n + 1), 3)), np.int16)
    return _PERM[n]


def car_line_table(r, n):
    """車番 -> (ライン番号, 役割, ラインの強さ8項目)。並び不明の車は単騎扱い。"""
    E = {e["car"]: e for e in r["entries"]}
    sc = {c: (e.get("score") or 0) for c, e in E.items()}
    mean_sc = np.mean(list(sc.values())) if sc else 0
    lines = [l for l in r["line"]["lines"] if all(c in E for c in l)]
    lsum = [sum(sc[c] for c in l) for l in lines]
    order = np.argsort(-np.array(lsum)) if lines else []
    lrank = {li: k + 1 for k, li in enumerate(order)}
    line = np.arange(100, 100 + n + 1)
    role = np.full(n + 1, 3, np.int8)
    feat = np.full((n + 1, len(LINE_COLS)), np.nan, np.float32)
    for li, l in enumerate(lines):
        h = l[0]; s2 = l[1] if len(l) >= 2 else None
        others = [x for k, x in enumerate(lsum) if k != li]
        v = [len(l), lsum[li], lrank[li], len(lines), sc[h] - mean_sc,
             (E[h].get("nige") or 0) + (E[h].get("makuri") or 0),
             (sc[s2] - mean_sc) if s2 else np.nan, lsum[li] - max(others, default=lsum[li])]
        for i, c in enumerate(l):
            if c > n:
                continue
            line[c] = li
            role[c] = 3 if len(l) == 1 else min(i, 2)
            feat[c] = v
    return line, role, feat


def ticket_matrix(r, p, q, odds):
    """1レース分の全順列の特徴行列（行は車番の順列順）。"""
    n = r["odds"]["3連単"]["n"]
    k = perm_of(n)
    a, b, c = k[:, 0], k[:, 1], k[:, 2]
    line, role, feat = car_line_table(r, n)
    s12 = line[a] == line[b]; s23 = line[b] == line[c]; s13 = line[a] == line[c]
    pat = np.select([s12 & s23, s12, s23, s13], [0, 1, 2, 3], 4)
    pp = np.clip(p, 1e-7, 1); qq = np.clip(q, 1e-7, 1)
    base = np.column_stack([np.log(pp), np.log(pp / qq), pat, role[a], role[b], role[c],
                            np.searchsorted(OB, odds)]).astype(np.float32)
    return np.hstack([base, feat[a], feat[b]])


def apply(model, r, p, q, odds):
    """AIの確率 p にライン補正をかけ、レース内で合計1に戻す。"""
    p = np.asarray(p, float)
    g = model.predict(ticket_matrix(r, p, q, np.asarray(odds, float)), raw_score=True)
    x = p * np.exp(np.clip(g, -2, 2))
    return x / x.sum()


def sample(x, y, p, rng, rate=0.05):
    """学習用に1レース分を間引く（当たりは全部、外れは rate の割合）。補正の初期値も返す。"""
    keep = (y == 1) | (rng.random(len(y)) < rate)
    pk = np.clip(p[keep], 1e-7, 1 - 1e-7)
    return x[keep], y[keep], np.log(pk / (1 - pk)) + np.log(1 / rate)


def fit(X, y, off, seed=0):
    import lightgbm as lgb
    ds = lgb.Dataset(X, y, init_score=off, categorical_feature=CAT_IDX)
    return lgb.train(dict(objective="binary", learning_rate=0.03, num_leaves=15, min_data_in_leaf=2000, lambda_l2=10,
                          feature_fraction=0.8, bagging_fraction=0.8, bagging_freq=1, verbose=-1, seed=seed), ds, num_boost_round=300)


def late_picks(r, by, odds, ref):
    """締切直前の動きを使う買い方 C/D/E。by: A/B の判定結果、ref: 締切3分前ごろのオッズ（車番の順列順）"""
    o = np.asarray(odds, float)
    ref = np.asarray(ref, float)
    if len(ref) != len(o):
        return {}
    ratio = np.where((ref > 0) & (o > 0), o / np.where(ref > 0, ref, 1), 0)
    k = perm_of(r["odds"]["3連単"]["n"])
    ix = {"-".join(map(str, c)): i for i, c in enumerate(k)}
    out = {}
    for name, rule in LATE.items():
        if "base" in rule:
            out[name] = [dict(x, r=round(float(ratio[ix[x["combo"]]]), 3)) for x in by.get(rule["base"], [])
                         if 0 < ratio[ix[x["combo"]]] <= rule["max_r"]]
        else:
            sel = np.where((o >= rule["min_odds"]) & (o < rule["max_odds"]) & (ratio >= rule["min_r"]) & (ratio <= rule["max_r"]))[0]
            out[name] = [{"combo": "-".join(map(str, k[i])), "odds": round(float(o[i]), 1), "r": round(float(ratio[i]), 3)}
                         for i in sorted(sel, key=lambda i: ratio[i])]
    return out


def picks(r, p, odds, rule=PICK):
    """推奨の買い目（期待値 = 補正後の確率 × オッズ）。"""
    n = r["odds"]["3連単"]["n"]
    k = perm_of(n)
    out = []
    for i, (pi, o) in enumerate(zip(p, odds)):
        if o and rule["min_odds"] <= o < rule["max_odds"] and pi * o >= rule["min_ev"]:
            out.append({"combo": "-".join(map(str, k[i])), "odds": round(float(o), 1), "p": round(float(pi), 6), "ev": round(float(pi * o), 3)})
    return sorted(out, key=lambda x: -x["ev"])
