"""
捲り決着候補レースの抽出。
条件: 先行意欲の強い先頭（過去に先頭で走ったときの主導権率55%以上、かつ自力コメント/先行・押え先）が2人以上いるレース。
各先頭について、本人の過去（全体・同じライン条件）での主導権率・捲り勝ち率・捲りで2着以内の率と、競走得点の推移を出す。

  python -m ai.makuri_pick --data DIR [--date YYYY-MM-DD]
"""
import argparse
import collections
import gzip
import json
from pathlib import Path

from .common import fix_entries, load_races
from .similar import VENUE, build_index, head_rows


def rider_scores(data: Path):
    """選手名 -> [(日付, 競走得点)]（開催ごとに1件）"""
    out = collections.defaultdict(dict)
    for r in load_races(data):
        mid = r["race_id"][:10]
        for e in r["entries"]:
            if e.get("score"):
                out[e["name"]].setdefault(mid, (r["date"], e["score"]))
    return {k: sorted(v.values()) for k, v in out.items()}


def stats(rows):
    n = len(rows)
    if not n:
        return {"n": 0}
    mk_win = sum(1 for x in rows if x.get("rank") == "1" and x.get("kimarite") == "捲")
    mk_top2 = sum(1 for x in rows if x.get("rank") in ("1", "2") and x.get("kimarite") == "捲")
    return {"n": n, "主導権率": round(sum(x["B"] for x in rows) / n * 100), "1着率": round(sum(x["rank"] == "1" for x in rows) / n * 100),
            "捲り勝ち率": round(mk_win / n * 100), "捲り2着内率": round(mk_top2 / n * 100)}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--date")
    ap.add_argument("--min-strong", type=int, default=2)
    ap.add_argument("--th", type=float, default=0.55)
    a = ap.parse_args(argv)
    f = (a.data / "live" / f"{a.date}.json.gz") if a.date else sorted((a.data / "live").glob("*.json.gz"))[-1]
    live = json.load(gzip.open(f, "rt", encoding="utf-8"))
    index = build_index(a.data)
    by = collections.defaultdict(list)
    for x in index:
        by[x["name"]].append(x)
    scores = rider_scores(a.data)
    meets = {}
    for p in (a.data / "state" / "meets").glob("*.json"):
        meets.update(json.loads(p.read_text()))
    out = []
    for r in live["races"]:
        if "ガールズ" in (r.get("race_type") or ""):
            continue
        ents = fix_entries({"entries": r["entries"]})["entries"]
        E = {e["car"]: e for e in ents}
        days = (meets.get(r["race_id"][:10]) or {}).get("days") or [int(r["race_id"][10:12])]
        race = {"race_id": r["race_id"], "date": live["date"], "venue": r["venue"], "race_no": r["race_no"], "race_type": r["race_type"],
                "url": r.get("url"), "entries": ents, "line": {"lines": r["lines"], "tactics": r["tactics"]}, "result": []}
        heads = head_rows(race, max(days))
        info = []
        for t in heads:
            past = [x for x in by.get(t["name"], []) if x["date"] < live["date"]]
            hb = (sum(x["B"] for x in past) + 1) / (len(past) + 3)
            jiriki = "自力" in (t["comment"] or "") or t["tactic"] in ("先行", "押え先")
            core = ["linelen", "bunsen", "sec_senpai", "sec_local", "head_local"]
            same = [x for x in past if all(x[c] == t[c] for c in core)]
            sc = scores.get(t["name"], [])[-6:]
            e = E[t["car"]]
            info.append({"car": t["car"], "name": t["name"], "line": t["line"], "tactic": t["tactic"], "comment": t["comment"],
                         "strong": jiriki and hb >= a.th, "主導権率_推定": round(hb * 100),
                         "全体": stats(past), "同ライン条件": stats(same),
                         "4か月": {k: e.get(k) for k in ("S", "B", "nige", "makuri")}, "得点": e.get("score"),
                         "得点推移": [(d[5:], s) for d, s in sc]})
        strong = sum(1 for x in info if x["strong"])
        if strong >= a.min_strong:
            out.append({"venue": r["venue"], "race_no": r["race_no"], "race_type": r["race_type"], "start_time": r.get("start_time"),
                        "close_time": r.get("close_time"), "lines": r["lines"], "strong": strong, "groups": len(r["lines"]),
                        "bank": VENUE.get(r["venue"], (None, None))[1], "url": r.get("url"), "heads": info})
    out.sort(key=lambda x: (x["close_time"] or "99", x["venue"]))
    print(json.dumps({"date": live["date"], "n_races": len(live["races"]), "picked": out}, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
