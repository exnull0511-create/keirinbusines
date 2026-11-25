"""
ペーパートレードの集計。paper/YYYY-MM-DD.jsonl（ai.live が締切前に記録した推奨買い目）を、確定した結果と照らす。
各レースは締切前の最後の記録を使う（実際に買うならその時点の目とオッズ）。1点100円で計算。
  python -m ai.paper --data DIR
保存先: DIR/paper/summary.json

オッズのずれ（rules.<買い方>.drift）
  オッズ監視が締切直前に判定した推奨（src=watch）について、判定時のオッズと確定オッズを比べる。
  ratio = 確定 ÷ 判定時、ev_final = 判定時の期待値 × ratio（確定オッズで買えたときの期待値）。
  total（累計）と days（日ごと）に、点数・ratio の平均と中央値・下がった割合・確定でも基準の期待値以上だった割合。
  買い方 E（AIを使わない）は期待値を持たないので、ev_final は 0 になる（ratio だけ見る）。
"""
import argparse
import collections
import gzip
import itertools
import json
import re
import statistics
from pathlib import Path


def drift_stats(xs, thr):
    """xs: [(ratio, ev_final)]"""
    if not xs:
        return {"bets": 0}
    r = [x[0] for x in xs]
    return {"bets": len(xs), "ratio_mean": round(statistics.mean(r), 3), "ratio_median": round(statistics.median(r), 3),
            "down": round(sum(v < 1 for v in r) / len(r), 3),
            "ev_final_mean": round(statistics.mean(x[1] for x in xs), 3),
            "keep": round(sum(x[1] >= thr for x in xs) / len(xs), 3), "threshold": thr}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, required=True)
    a = ap.parse_args(argv)
    last = {}
    for f in sorted((a.data / "paper").glob("*.jsonl")):
        for l in open(f, encoding="utf-8"):
            x = json.loads(l)
            last[x["race_id"]] = x
    res, fin = {}, {}
    days = sorted({f.stem for f in (a.data / "paper").glob("*.jsonl")})
    for d in days:
        p = a.data / "races" / d[:4] / f"{d}.jsonl.gz"
        if not p.exists():
            continue
        for l in gzip.open(p, "rt", encoding="utf-8"):
            r = json.loads(l)
            if r["race_id"] in last and r["result"]:
                win = next((x for x in r["refund"] if x["bet"] == "3連単"), None)
                res[r["race_id"]] = (d, win, r["venue"], r["race_no"])
                t = (r.get("odds") or {}).get("3連単")
                if t and t.get("v"):
                    fin[r["race_id"]] = t
    roi = lambda t: round(t[2] / (t[0] * 100) * 100, 1) if t[0] else None
    rules = {}
    for x in last.values():
        by = x.get("picks_by") or {"A_50-300倍_期待値1.2": x.get("picks", [])}   # 初日の記録は推奨だけ
        for k, v in by.items():
            rules.setdefault(k, []).append((x["race_id"], v, x.get("src")))
    out = {"updated_from": days[-1] if days else None, "rules": {}}
    for name, items in rules.items():
        month = collections.defaultdict(lambda: [0, 0, 0])
        hits, total, races = [], [0, 0, 0], 0
        m_ = re.search(r"期待値([0-9.]+)", name)
        thr = float(m_.group(1)) if m_ else {"C": 1.2, "D": 1.1}.get(name[:1], 1.0)   # C/D は元の A/B の基準
        drift = collections.defaultdict(list)
        for rid, pks, src in items:
            if rid not in res or not pks:
                continue
            if src == "watch" and rid in fin:
                t = fin[rid]
                ix = {f"{a}-{b}-{c}": i for i, (a, b, c) in enumerate(itertools.permutations(range(1, t["n"] + 1), 3))}
                for pk in pks:
                    i = ix.get(pk["combo"])
                    f = t["v"][i] if i is not None and i < len(t["v"]) else None
                    if f and pk.get("odds"):
                        ratio = f / pk["odds"]
                        drift[res[rid][0]].append((ratio, (pk.get("ev") or 0) * ratio))   # E は期待値なし
            races += 1
            d, win, venue, no = res[rid]
            for pk in pks:
                ret = win["payout"] if win and win["combo"] == pk["combo"] else 0
                for t in (month[d[:7]], total):
                    t[0] += 1; t[1] += int(ret > 0); t[2] += ret
                if ret:
                    hits.append({"date": d, "venue": venue, "race_no": no, "combo": pk["combo"], "odds_at_log": pk["odds"], "payout": ret})
        out["rules"][name] = {"total": {"races": races, "bets": total[0], "hits": total[1], "payout": total[2], "roi": roi(total)},
                              "months": {m: {"bets": t[0], "hits": t[1], "payout": t[2], "roi": roi(t)} for m, t in sorted(month.items())},
                              "drift": {"total": drift_stats([x for v in drift.values() for x in v], thr),
                                        "days": {d: drift_stats(v, thr) for d, v in sorted(drift.items())}},
                              "hits": sorted(hits, key=lambda h: h["date"], reverse=True)[:50]}
        print("ペーパートレード", name, out["rules"][name]["total"], "オッズのずれ", out["rules"][name]["drift"]["total"])
    (a.data / "paper" / "summary.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

if __name__ == "__main__":
    main()
