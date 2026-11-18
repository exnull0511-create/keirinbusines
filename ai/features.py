"""
AI初版の特徴量。1行=1選手×1レース。
- 市場: 3連単オッズから出した 1着/2着内/3着内 の確率
- 出走表: 得点(レース内相対)、S/B、決まり手回数、級班、脚質、記者印、コメント分類
- 展開: ラインでの役割、ライン長、自力ライン数、番手と先頭の得点差
- 短期: 直近3走の主導権・着順、前日の上がり（役割別に補正）、その日の同場の逃げ決着率
"""
import collections
from datetime import date
import numpy as np
import pandas as pd
from itertools import permutations
from .comment_cls import classify
from .common import heads_of, cls

ROLE = {0: "先頭", 1: "番手", 2: "3番手"}


_PERM = {}


def market_probs(r):
    t = r["odds"]["3連単"]; n = t["n"]
    perm = _PERM.setdefault(n, list(permutations(range(1, n + 1), 3)))
    q = np.array([1 / v if v else 0.0 for v in t["v"]]); q = q / q.sum()
    w, p2, p3 = collections.Counter(), collections.Counter(), collections.Counter()
    for k, x in zip(perm, q):
        w[k[0]] += x; p2[k[0]] += x; p2[k[1]] += x
        for c in k: p3[c] += x
    return perm, q, w, p2, p3


CATS = ["weather", "cls", "grade", "klass", "style", "mark", "cm1", "cm2", "role", "tac", "head_tac", "head_cm"]


def build(rs, categories=None):
    rs = sorted(rs, key=lambda r: (r["date"], r["venue"], r["race_no"]))
    last = collections.defaultdict(list)        # 選手名 -> 直近の(主導権, 着順)
    prevday = {}                                # (開催, 選手名) -> (日目, 役割別上がり偏差, 着順)
    vday = collections.defaultdict(list)        # (場, 日付) -> 逃げ決着か
    meets = collections.defaultdict(list)       # 選手名 -> [(日付, 開催ID, 落車/失格があったか)]
    pair = collections.defaultdict(lambda: [0, 0])  # (先頭名, 番手名) -> [連係回数, 番手が先頭に先着]
    rows, combos = [], {}
    for r in rs:
        E = {e["car"]: e for e in r["entries"]}
        if not E:
            continue
        perm, q, W, P2, P3 = market_probs(r)
        combos[r["race_id"]] = (perm, q.astype(np.float32), np.array([v or 0 for v in r["odds"]["3連単"]["v"]], dtype=np.float32))
        _, heads = heads_of(r)
        heads_set = {l[0] for l in heads}
        res = {x["car"]: x for x in r["result"]}
        rk = {c: (int(x["rank"]) if x["rank"].isdigit() else 9) for c, x in res.items()}
        sb = {c: x["sb"] or "" for c, x in res.items()}
        bcar = next((c for c, v in sb.items() if "B" in v), None)
        mid, day = r["race_id"][:10], int(r["race_id"][10:12])
        scores = np.array([E[c].get("score") or 0 for c in E])
        role_of, line_of = {}, {}
        for l in r["line"]["lines"]:
            for i, c in enumerate(l):
                role_of[c] = "単騎" if len(l) == 1 else ROLE.get(i, "4番手以降")
                line_of[c] = l
        vk = (r["venue"], r["date"])
        dnow = date.fromisoformat(r["date"])
        vd = vday[vk]
        for c, e in E.items():
            l = line_of.get(c, [c])
            head = E.get(l[0], {})
            L3 = last[e["name"]][-3:]
            pv = prevday.get((mid, e["name"]))
            pvd = pv if pv and pv[0] == day - 1 else None
            cm = classify(e.get("comment"))
            M = meets[e["name"]]
            prevm = [x for x in M if x[1] != mid]
            lastm = prevm[-1] if prevm else None
            gap = (dnow - lastm[0]).days if lastm else np.nan
            n30 = len({x[1] for x in prevm if (dnow - x[0]).days <= 30})
            rak_last = int(any(x[2] for x in prevm if lastm and x[1] == lastm[1]))
            rak_here = int(any(x[2] for x in M if x[1] == mid))
            pr = pair.get((head.get("name"), e["name"])) if role_of.get(c) == "番手" else None
            rows.append(dict(
                race=r["race_id"], date=r["date"], car=c, n=len(E), cls=cls(r["race_type"]), grade=r.get("grade") or "",
                wind=r.get("wind_speed", np.nan), weather=r.get("weather") or "",
                m_win=W.get(c, 0), m_top2=P2.get(c, 0), m_top3=P3.get(c, 0),
                score_rel=(e.get("score") or 0) - scores.mean(), score_rank=int((scores > (e.get("score") or 0)).sum()) + 1,
                S=e.get("S") or 0, B=e.get("B") or 0, nige=e.get("nige") or 0, makuri=e.get("makuri") or 0,
                sashi=e.get("sashi") or 0, mark_cnt=e.get("mark_cnt") or 0,
                win_rate=e.get("win_rate") or 0, trio_rate=e.get("trio_rate") or 0,
                klass=e.get("class") or "", style=e.get("style") or "", gear=e.get("gear") or 0, age=e.get("age") or 0,
                mark=e.get("mark") or "", cm1=cm[0], cm2=cm[1],
                role=role_of.get(c, "単騎"), linelen=len(l), nlines=len(r["line"]["lines"]), nheads=len(heads),
                tac=r["line"]["tactics"].get(str(c)) or "", head_tac=r["line"]["tactics"].get(str(l[0])) or "",
                head_cm=classify(head.get("comment"))[1], head_B=head.get("B") or 0,
                head_is_jiriki=int(l[0] in heads_set),
                diff_head=(e.get("score") or 0) - (head.get("score") or 0),
                rec_n=len(L3), rec_B=np.mean([x[0] for x in L3]) if L3 else np.nan,
                rec_rank=np.mean([x[1] for x in L3]) if L3 else np.nan,
                prev_lapdev=pvd[1] if pvd else np.nan, prev_rank=pvd[2] if pvd else np.nan,
                vday_n=len(vd), vday_nige=np.mean(vd) if vd else np.nan,
                gap_days=gap, n30=n30, rak_last=rak_last, rak_here=rak_here,
                pair_n=pr[0] if pr else np.nan, pair_beat=(pr[1] / pr[0]) if pr and pr[0] else np.nan,
                y1=int(rk.get(c, 9) == 1), y2=int(rk.get(c, 9) <= 2), y3=int(rk.get(c, 9) <= 3),
            ))
        # ---- このレースの結果で履歴を更新（以降のレースの特徴量になる）。未確定のレースは更新しない
        if not r["result"]:
            continue
        laps = {c: x["last_lap"] for c, x in res.items() if x["last_lap"]}
        by_role = collections.defaultdict(list)
        for c, v in laps.items():
            by_role[role_of.get(c, "単騎")].append(v)
        for c, e in E.items():
            last[e["name"]].append((int(bcar == c and c in heads_set), rk.get(c, 9)))
            if c in laps:
                ref = np.median(by_role[role_of.get(c, "単騎")]) if len(by_role[role_of.get(c, "単騎")]) >= 2 else np.median(list(laps.values()))
                prevday[(mid, e["name"])] = (day, laps[c] - ref, rk.get(c, 9))
        for c, e in E.items():
            meets[e["name"]].append((dnow, mid, res.get(c, {}).get("rank") in ("落", "失", "故")))
        for l in r["line"]["lines"]:
            if len(l) >= 2 and l[0] in E and l[1] in E:
                pp = pair[(E[l[0]]["name"], E[l[1]]["name"])]
                pp[0] += 1
                pp[1] += int(rk.get(l[1], 9) < rk.get(l[0], 9))
        win_k = next((x["kimarite"] for x in r["result"] if x["rank"] == "1"), "")
        vd.append(int(win_k == "逃"))
    df = pd.DataFrame(rows)
    for col in CATS:
        if categories and col in categories:
            df[col] = pd.Categorical(df[col].astype(str), categories=categories[col])
        else:
            df[col] = df[col].astype(str).astype("category")
    for c in ["m_win", "m_top2", "m_top3"]:
        df["log_" + c] = np.log(df[c].clip(1e-4))
    return df, combos
