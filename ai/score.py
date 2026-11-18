"""
買い目（フォーメーション）の採点。AIの確率はライン補正後（ai/line.py）。
  python -m ai.score --data DIR 立川 11 "3-12-1234" ["4=2-1356" ...]

書き方:
  3-12-1234   1着3、2着1か2、3着1〜4（同じ車番の重複は除く）
  4=2-1356    1・2着が4と2（順不同）、3着1,3,5,6
  1=2=3       3連単ボックス
  *（または 全）で全車
"""
import argparse
import gzip
import json
from itertools import permutations, product
from pathlib import Path


def expand(spec: str, n: int):
    allc = list(range(1, n + 1))
    grp = lambda s: allc if s in ("*", "全", "総流し") else [int(ch) for ch in s if ch.isdigit()]
    out = set()
    if spec.count("=") == 2 and "-" not in spec:
        cars = sorted({c for part in spec.split("=") for c in grp(part)})
        return sorted(permutations(cars, 3))
    parts = spec.split("-")
    if len(parts) == 2 and "=" in parts[0]:
        a, b = parts[0].split("=")
        for x, y in product(grp(a), grp(b)):
            for p, s in ((x, y), (y, x)):
                for t in grp(parts[1]):
                    if len({p, s, t}) == 3:
                        out.add((p, s, t))
        return sorted(out)
    if len(parts) == 2 and "=" in parts[1]:   # 例: 4-2=1356 → 1着4、2・3着は {2} と {1,3,5,6} の順不同
        b, c = parts[1].split("=")
        for x in grp(parts[0]):
            for y, z in product(grp(b), grp(c)):
                for s, t in ((y, z), (z, y)):
                    if len({x, s, t}) == 3:
                        out.add((x, s, t))
        return sorted(out)
    if len(parts) != 3:
        raise ValueError(f"書き方が読めません: {spec}")
    for x, y, z in product(grp(parts[0]), grp(parts[1]), grp(parts[2])):
        if len({x, y, z}) == 3:
            out.add((x, y, z))
    return sorted(out)


def find_race(live: dict, venue: str, race_no: int):
    for r in live["races"]:
        if r["venue"].startswith(venue) and r["race_no"] == race_no:
            return r
    return None


def score(r: dict, specs: list[str]):
    n = r["n"]
    perm = list(permutations(range(1, n + 1), 3))
    idx = {k: i for i, k in enumerate(perm)}
    combos = []
    for s in specs:
        combos += expand(s, n)
    combos = sorted(set(combos), key=lambda k: idx[k])
    rows = []
    for k in combos:
        i = idx[k]
        o = r["odds"][i] or 0
        p = r["ai"]["p"][i] if r.get("ai") else None
        q = r["ai"]["q"][i] if r.get("ai") else None
        rows.append({"combo": "-".join(map(str, k)), "odds": o, "ai_p": p, "mkt_p": q, "ev": (p * o if p and o else None)})
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--date")
    ap.add_argument("venue")
    ap.add_argument("race_no", type=int)
    ap.add_argument("specs", nargs="+")
    a = ap.parse_args(argv)
    files = sorted((a.data / "live").glob("*.json.gz"))
    f = (a.data / "live" / f"{a.date}.json.gz") if a.date else files[-1]
    live = json.load(gzip.open(f, "rt", encoding="utf-8"))
    r = find_race(live, a.venue, a.race_no)
    if not r:
        print("レースが見つかりません", [f"{x['venue']}{x['race_no']}R" for x in live["races"]][:40])
        return
    rows = score(r, a.specs)
    print(json.dumps({"race": {k: r[k] for k in ("venue", "race_no", "race_type", "start_time", "close_time", "fetched_at", "finished")},
                      "rows": rows}, ensure_ascii=False))


if __name__ == "__main__":
    main()
