"""
当日の途中経過（ペーパートレード）。締切済みで推奨があったレースだけ、Kドリームスから結果を取りに行って照合する。
翌朝の正式な集計（ai/paper.py）を待たずに「今日どうだったか」を見るためのもの。
  python -m ai.paper_today --data DIR [--date YYYY-MM-DD]
保存先: DIR/paper/today.json
  {"date", "updated_at", "rules": {買い方: {"races", "bets", "hits", "payout", "roi"}}, "hits": [当たった目], "pending": 結果待ちのレース数}
各レースは締切前の最後の判定を使う。1点100円。
"""
import argparse
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from collector.kdreams import Client, parse_race

JST = timezone(timedelta(hours=9))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--date")
    a = ap.parse_args(argv)
    now = datetime.now(JST)
    d = a.date or now.date().isoformat()
    last = {}
    for l in open(a.data / "paper" / f"{d}.jsonl", encoding="utf-8"):
        try:
            x = json.loads(l)
        except Exception:
            continue
        if x.get("src") == "watch":
            last[x["race_id"]] = x
    # レースのURLは当日の一覧（live/）から
    urls = {}
    try:
        import gzip
        for r in json.load(gzip.open(a.data / "live" / f"{d}.json.gz", "rt", encoding="utf-8"))["races"]:
            urls[r["race_id"]] = (r["url"], f"{r['venue']}{r['race_no']}R")
    except Exception as e:
        print("  ! 当日の一覧が読めない", e, flush=True)
    cli = Client(sleep=1.5)
    rules, hits, pending = {}, [], 0
    for rid, x in sorted(last.items(), key=lambda t: t[1].get("close_time") or ""):
        if not any(x.get("picks_by", {}).values()):
            continue
        ct = x.get("close_time") or "99:99"
        if d == now.date().isoformat() and ct > (now - timedelta(minutes=10)).strftime("%H:%M"):
            pending += 1
            continue
        if rid not in urls:
            print("  ! URL不明", rid, flush=True)
            continue
        url, label = urls[rid]
        html = cli.get(url)
        r = parse_race(html, url) if html else None
        win = next((p for p in (r or {}).get("refund", []) if p["bet"] == "3連単"), None)
        if not win:
            pending += 1
            print("  結果まだ", label, flush=True)
            continue
        for k, v in x["picks_by"].items():
            t = rules.setdefault(k, {"races": 0, "bets": 0, "hits": 0, "payout": 0})
            if v:
                t["races"] += 1
            for p in v:
                t["bets"] += 1
                if p["combo"] == win["combo"]:
                    t["hits"] += 1
                    t["payout"] += win["payout"]
                    hits.append({"rule": k[:1], "race": x.get("label") or label, "combo": p["combo"], "odds_at_judge": p["odds"], "payout": win["payout"]})
    for t in rules.values():
        t["roi"] = round(t["payout"] / (t["bets"] * 100) * 100, 1) if t["bets"] else None
    out = {"date": d, "updated_at": now.isoformat(timespec="minutes"), "rules": dict(sorted(rules.items())), "hits": hits, "pending": pending}
    (a.data / "paper" / "today.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    for k, t in out["rules"].items():
        print(k, t, flush=True)
    print("当たり", hits, "結果待ち", pending, flush=True)


if __name__ == "__main__":
    main()
