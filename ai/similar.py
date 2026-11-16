"""
予想アシスト: 指定レースのライン先頭ごとに、同じ条件で走った過去レース（映像リンク付き）を探す。

  python -m ai.similar --data DIR 富山 9 [--date YYYY-MM-DD]

条件（ライン先頭から見て）:
  バンク周長 / 開催日（初日・2日目・中日・最終日）/ ラインの長さ / 何分戦（2車以上のライン数）
  / 番手が同県の先輩か / 番手が地元か / 自分が地元か
"""
from __future__ import annotations

import argparse
import collections
import gzip
import json
import pickle
from pathlib import Path

from .common import cls, fix_entries, load_races

# 競輪場 -> (都道府県, バンク周長m)
VENUE = {
    "函館": ("北海道", 400), "青森": ("青森", 400), "いわき平": ("福島", 400), "弥彦": ("新潟", 400), "前橋": ("群馬", 335),
    "取手": ("茨城", 400), "宇都宮": ("栃木", 500), "大宮": ("埼玉", 500), "西武園": ("埼玉", 400), "京王閣": ("東京", 400),
    "立川": ("東京", 400), "松戸": ("千葉", 333), "千葉": ("千葉", 250), "川崎": ("神奈川", 400), "平塚": ("神奈川", 400),
    "小田原": ("神奈川", 333), "伊東": ("静岡", 333), "静岡": ("静岡", 400), "名古屋": ("愛知", 400), "岐阜": ("岐阜", 400),
    "大垣": ("岐阜", 400), "豊橋": ("愛知", 400), "富山": ("富山", 333), "松阪": ("三重", 400), "四日市": ("三重", 400),
    "福井": ("福井", 400), "奈良": ("奈良", 333), "向日町": ("京都", 400), "和歌山": ("和歌山", 400), "岸和田": ("大阪", 400),
    "玉野": ("岡山", 400), "広島": ("広島", 400), "防府": ("山口", 333), "高松": ("香川", 400), "小松島": ("徳島", 400),
    "高知": ("高知", 500), "松山": ("愛媛", 400), "小倉": ("福岡", 400), "久留米": ("福岡", 400), "武雄": ("佐賀", 400),
    "佐世保": ("長崎", 400), "別府": ("大分", 400), "熊本": ("熊本", 400),
}
CONDS = ["bank", "day", "linelen", "bunsen", "sec_senpai", "sec_local", "head_local"]
LABEL = {"bank": "バンク", "day": "開催日", "linelen": "ライン", "bunsen": "分戦", "sec_senpai": "番手が同県の先輩",
         "sec_local": "番手が地元", "head_local": "自分が地元"}


def day_label(day: int, maxday: int) -> str:
    if day == 1:
        return "初日"
    if day == maxday:
        return "最終日"
    return "2日目" if day == 2 else "中日"


def head_rows(r: dict, maxday: int) -> list[dict]:
    """1レースから、2車以上のラインの先頭ごとに条件を作る。"""
    pref, bank = VENUE.get(r["venue"], (None, None))
    E = {e["car"]: e for e in r["entries"]}
    lines = r["line"]["lines"]
    bunsen = sum(1 for l in lines if len(l) >= 2)
    day = int(r["race_id"][10:12])
    res = {x["car"]: x for x in r.get("result", [])}
    winner = next((c for c, x in res.items() if x["rank"] == "1"), None)
    out = []
    for l in lines:
        if len(l) < 2 or l[0] not in E or l[1] not in E:
            continue
        h, s = E[l[0]], E[l[1]]
        row = {
            "race_id": r["race_id"], "date": r["date"], "venue": r["venue"], "race_no": r["race_no"],
            "race_type": r.get("race_type"), "cls": cls(r.get("race_type")), "url": r.get("url"),
            "name": h.get("name"), "car": l[0], "line": l, "lines": lines, "tactic": r["line"]["tactics"].get(str(l[0])),
            "comment": h.get("comment"),
            "bank": bank, "day": day_label(day, maxday), "linelen": min(len(l), 3), "bunsen": bunsen,
            "sec_senpai": bool(h.get("pref") and s.get("pref") == h.get("pref") and (s.get("term") or 999) < (h.get("term") or 0)),
            "sec_local": bool(pref and s.get("pref") == pref),
            "head_local": bool(pref and h.get("pref") == pref),
        }
        if res:
            hr = res.get(l[0], {})
            row.update(S="S" in (hr.get("sb") or ""), B="B" in (hr.get("sb") or ""), rank=hr.get("rank"),
                       kimarite=hr.get("kimarite") or "", line_win=winner in l,
                       winner=winner, winner_kimarite=res.get(winner, {}).get("kimarite", "") if winner else "",
                       order="-".join(str(x["car"]) for x in sorted([x for x in res.values() if x["rank"].isdigit()], key=lambda x: int(x["rank"]))[:3]),
                       review=(r.get("review") or {}).get("summary"))
        out.append(row)
    return out


def build_index(data: Path) -> list[dict]:
    cache = data / "models" / "similar_index.pkl"
    files = sorted((data / "races").glob("*/*.jsonl.gz"))
    stamp = (len(files), files[-1].stat().st_mtime if files else 0)
    if cache.exists():
        c = pickle.load(open(cache, "rb"))
        if c.get("stamp") == stamp:
            return c["rows"]
    rs = load_races(data)
    maxday = collections.defaultdict(int)
    for r in rs:
        maxday[r["race_id"][:10]] = max(maxday[r["race_id"][:10]], int(r["race_id"][10:12]))
    rows = []
    for r in rs:
        rows += head_rows(r, maxday[r["race_id"][:10]])
    cache.parent.mkdir(parents=True, exist_ok=True)
    pickle.dump({"stamp": stamp, "rows": rows}, open(cache, "wb"))
    return rows


def describe(x: dict) -> str:
    act = "主導権(B)" if x.get("B") else ("S取り" if x.get("S") else "")
    km = f"{x['rank']}着" + (f"({x['kimarite']})" if x.get("kimarite") else "")
    return f"{act} {km}".strip()


def summarize(rows: list[dict]) -> dict:
    n = len(rows)
    if not n:
        return {"n": 0}
    return {"n": n, "B率": round(sum(x["B"] for x in rows) / n * 100), "1着率": round(sum(x["rank"] == "1" for x in rows) / n * 100),
            "逃げ勝ち": sum(x["rank"] == "1" and x["kimarite"] == "逃" for x in rows),
            "捲り勝ち": sum(x["rank"] == "1" and x["kimarite"] == "捲" for x in rows),
            "ラインから1着率": round(sum(x["line_win"] for x in rows) / n * 100)}


def search(index: list[dict], target: dict, k: int = 5) -> dict:
    same = lambda x, keys: all(x[c] == target[c] for c in keys)
    core = ["linelen", "bunsen", "sec_senpai", "sec_local", "head_local"]
    mine = [x for x in index if x["name"] == target["name"] and x["race_id"] < target["race_id"]]
    mine_all = sorted([x for x in mine if same(x, CONDS)], key=lambda x: x["date"], reverse=True)
    mine_core = sorted([x for x in mine if same(x, core) and x not in mine_all], key=lambda x: x["date"], reverse=True)
    others = [x for x in index if x["name"] != target["name"] and same(x, CONDS) and x["cls"] == target["cls"]]
    # 同じ競輪場 → 新しい順
    others = sorted(others, key=lambda x: (x["venue"] == target["venue"], x["date"]), reverse=True)
    return {"target": {c: target[c] for c in ["name", "car", "line", "tactic", "comment"] + CONDS},
            "本人・全条件一致": {"stats": summarize(mine_all), "races": mine_all[:k]},
            "本人・ライン条件一致": {"stats": summarize(mine_core), "races": mine_core[:k]},
            "本人・先頭で走った全レース": {"stats": summarize(sorted(mine, key=lambda x: x["date"], reverse=True))},
            "他の選手・全条件一致": {"stats": summarize(others), "races": others[:k]}}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--date")
    ap.add_argument("venue")
    ap.add_argument("race_no", type=int)
    a = ap.parse_args(argv)
    files = sorted((a.data / "live").glob("*.json.gz"))
    f = (a.data / "live" / f"{a.date}.json.gz") if a.date else files[-1]
    live = json.load(gzip.open(f, "rt", encoding="utf-8"))
    r = next((x for x in live["races"] if x["venue"].startswith(a.venue) and x["race_no"] == a.race_no), None)
    if not r:
        print(json.dumps({"error": "レースが見つかりません"}, ensure_ascii=False))
        return
    meets = json.loads((a.data / "state" / "meets" / f"{live['date'][:4]}{live['date'][5:7]}.json").read_text()) \
        if (a.data / "state" / "meets" / f"{live['date'][:4]}{live['date'][5:7]}.json").exists() else {}
    mid = r["race_id"][:10]
    days = (meets.get(mid) or {}).get("days") or [int(r["race_id"][10:12])]
    race = {"race_id": r["race_id"], "date": live["date"], "venue": r["venue"], "race_no": r["race_no"], "race_type": r["race_type"],
            "url": r.get("url"), "entries": fix_entries({"entries": r["entries"]})["entries"], "line": {"lines": r["lines"], "tactics": r["tactics"]}, "result": []}
    targets = head_rows(race, max(days))
    index = build_index(a.data)
    out = {"race": {k: r.get(k) for k in ("venue", "race_no", "race_type", "start_time", "close_time", "lines")},
           "heads": [search(index, t) for t in targets]}
    print(json.dumps(out, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
