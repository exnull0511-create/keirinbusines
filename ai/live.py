"""
当日の全レースを取得し、最新オッズでAIの着順確率と3連単の期待値を計算して保存。
  python -m ai.live --data DIR [--date YYYY-MM-DD]
保存先: DIR/live/YYYY-MM-DD.json.gz （1レース=1要素。3連単は車番の順列順の配列）
"""
import argparse
import gzip
import json
import pickle
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from collector.kdreams import Client
from collector.run import Store, races_on, today_jst
from .common import load_races
from . import line
from .features import build
from .predict import race_probs


def score_races(meta, history, todays):
    df, combos = build(history + todays, categories=meta["categories"])
    ids = {r["race_id"] for r in todays}
    df = df[df.race.isin(ids)]
    if df.empty:
        return {}
    M = {r["race_id"]: r for r in todays}
    lm = meta.get("line")
    out = {}
    for rid, (p, q, odds, gg) in race_probs(meta, meta["b"], df, combos).items():
        p0 = p
        if lm:
            p = line.apply(lm["model"], M[rid], p0, q, odds)
        s = {"p": [round(float(x), 6) for x in p], "q": [round(float(x), 6) for x in q],
             "riders": {int(c): {"p1": round(float(a), 4), "p3": round(float(b), 4), "m1": round(float(m1), 4), "m3": round(float(m3), 4)}
                        for c, a, b, m1, m3 in zip(gg.car, gg.p1, gg.p3, gg.m_win, gg.m_top3)}}
        if lm:
            s["p_base"] = [round(float(x), 6) for x in p0]
            s["picks_by"] = {k: line.picks(M[rid], p, odds, rule) for k, rule in line.PICKS.items()}
            s["picks"] = next(iter(s["picks_by"].values()))
        out[rid] = s
    return out


def write_live(data, d, meta, todays):
    """当日のレースをAIで採点し、DIR/live/日付.json.gz に保存。戻り値: race_id -> 採点結果"""
    todays = [r for r in todays if r.get("entries")]
    with_odds = [r for r in todays if r.get("odds", {}).get("3連単")]  # 前日などオッズ未発売のレースは採点しない
    history = [r for r in load_races(data) if r["date"] < d.isoformat()]
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
    out = data / "live"
    out.mkdir(parents=True, exist_ok=True)
    with gzip.open(out / f"{d.isoformat()}.json.gz", "wt", encoding="utf-8") as f:
        json.dump({"date": d.isoformat(), "updated_at": now, "model_last_date": meta["last_date"], "races": rows}, f, ensure_ascii=False)
    print(d, "races:", len(rows), "scored:", len(scores))
    return scores


def main(argv=None):
    """手動用（画面の更新）。推奨の記録（ペーパートレード）は collector/watch.py が締切直前のオッズで行う。"""
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--date")
    a = ap.parse_args(argv)
    d = date.fromisoformat(a.date) if a.date else today_jst()
    meta = pickle.load(open(a.data / "models" / "latest.pkl", "rb"))
    st, cli = Store(a.data), Client(sleep=1.2)
    todays, _ = races_on(cli, st, d)
    write_live(a.data, d, meta, todays)


if __name__ == "__main__":
    main()
