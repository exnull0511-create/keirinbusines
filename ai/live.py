"""
当日の全レースを取得し、最新オッズでAIの着順確率と3連単の期待値を計算して保存。
  python -m ai.live --data DIR [--date YYYY-MM-DD]
保存先: DIR/live/YYYY-MM-DD.json.gz （1レース=1要素。3連単は車番の順列順の配列）
"""
import argparse
import gzip
import json
import pickle
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np

from collector.kdreams import Client
from collector.run import Store, races_on, today_jst
from .common import load_races
from .features import build


def score_races(meta, history, todays):
    df, combos = build(history + todays, categories=meta["categories"])
    ids = {r["race_id"] for r in todays}
    df = df[df.race.isin(ids)].copy()
    if df.empty:
        return {}
    X = df[meta["feats"]]
    for k, m in enumerate(meta["models"], 1):
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
        R = {c: (a, b, d) for c, a, b, d in zip(gg.car, gg.r1, gg.r2, gg.r3)}
        f = np.array([R.get(k[0], (1, 1, 1))[0] * R.get(k[1], (1, 1, 1))[1] * R.get(k[2], (1, 1, 1))[2] for k in perm])
        p = q * np.power(f, meta["b"])
        p = p / p.sum()
        out[rid] = {"p": [round(float(x), 6) for x in p], "q": [round(float(x), 6) for x in q],
                    "riders": {int(c): {"p1": round(float(a), 4), "p3": round(float(b), 4), "m1": round(float(m1), 4), "m3": round(float(m3), 4)}
                               for c, a, b, m1, m3 in zip(gg.car, gg.p1, gg.p3, gg.m_win, gg.m_top3)}}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--date")
    a = ap.parse_args(argv)
    d = date.fromisoformat(a.date) if a.date else today_jst()
    meta = pickle.load(open(a.data / "models" / "latest.pkl", "rb"))
    st, cli = Store(a.data), Client(sleep=1.2)
    todays, _ = races_on(cli, st, d)
    todays = [r for r in todays if r.get("entries")]
    with_odds = [r for r in todays if r.get("odds", {}).get("3連単")]  # 前日などオッズ未発売のレースは採点しない
    history = [r for r in load_races(a.data) if r["date"] < d.isoformat()]
    history += [r for r in with_odds if r["result"]]
    scores = score_races(meta, history, with_odds) if with_odds else {}
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = []
    for r in todays:
        s = scores.get(r["race_id"])
        rows.append({"race_id": r["race_id"], "venue": r["venue"], "race_no": r["race_no"], "race_type": r["race_type"],
                     "grade": r.get("grade"), "start_time": r.get("start_time"), "close_time": r.get("close_time"),
                     "finished": bool(r["result"]), "fetched_at": now,
                     "entries": [{k: e.get(k) for k in ("car", "name", "mark", "comment", "score", "class", "style", "pref", "term", "age", "S", "B", "nige", "makuri")} for e in r["entries"]],
                     "url": r["url"],
                     "lines": r["line"]["lines"], "tactics": r["line"]["tactics"],
                     "n": (r["odds"].get("3連単") or {}).get("n", len(r["entries"])), "odds": (r["odds"].get("3連単") or {}).get("v", []), "ai": s,
                     "result": [{k: x[k] for k in ("rank", "car", "kimarite", "sb")} for x in r["result"]],
                     "refund": [p for p in r["refund"] if p["bet"] == "3連単"]})
    out = a.data / "live"
    out.mkdir(parents=True, exist_ok=True)
    with gzip.open(out / f"{d.isoformat()}.json.gz", "wt", encoding="utf-8") as f:
        json.dump({"date": d.isoformat(), "updated_at": now, "model_last_date": meta["last_date"], "races": rows}, f, ensure_ascii=False)
    print(d, "races:", len(rows), "scored:", len(scores))


if __name__ == "__main__":
    main()
