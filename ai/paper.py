"""
ペーパートレードの集計。paper/YYYY-MM-DD.jsonl（ai.live が締切前に記録した推奨買い目）を、確定した結果と照らす。
各レースは締切前の最後の記録を使う（実際に買うならその時点の目とオッズ）。1点100円で計算。
  python -m ai.paper --data DIR
保存先: DIR/paper/summary.json
"""
import argparse
import collections
import gzip
import json
from pathlib import Path


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, required=True)
    a = ap.parse_args(argv)
    last = {}
    for f in sorted((a.data / "paper").glob("*.jsonl")):
        for l in open(f, encoding="utf-8"):
            x = json.loads(l)
            last[x["race_id"]] = x
    res = {}
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
    month = collections.defaultdict(lambda: [0, 0, 0])
    hits, total = [], [0, 0, 0]
    for rid, x in last.items():
        if rid not in res or not x["picks"]:
            continue
        d, win, venue, no = res[rid]
        for pk in x["picks"]:
            ret = win["payout"] if win and win["combo"] == pk["combo"] else 0
            for t in (month[d[:7]], total):
                t[0] += 1; t[1] += int(ret > 0); t[2] += ret
            if ret:
                hits.append({"date": d, "venue": venue, "race_no": no, "combo": pk["combo"], "odds_at_log": pk["odds"], "payout": ret})
    roi = lambda t: round(t[2] / (t[0] * 100) * 100, 1) if t[0] else None
    out = {"total": {"bets": total[0], "hits": total[1], "payout": total[2], "roi": roi(total)},
           "months": {m: {"bets": t[0], "hits": t[1], "payout": t[2], "roi": roi(t)} for m, t in sorted(month.items())},
           "hits": sorted(hits, key=lambda h: h["date"], reverse=True)[:50]}
    (a.data / "paper" / "summary.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("ペーパートレード", out["total"])


if __name__ == "__main__":
    main()
