"""
締切前のオッズ監視。開催時間中ずっと動き、締切が近いレースの3連単オッズを細かく記録する。
  python -m collector.watch --data DIR [--max-minutes 340]

  締切12分前〜3分前: 1分おき / 3分前〜締切: 20秒おき / 締切後: 1回（確定に近いオッズ）
保存先: DIR/odds_ts/YYYY-MM-DD/<race_id>.jsonl.gz（追記。1行 = 1時点。レースごとに分けて、保存のたびに履歴が膨らまないようにする）
  {"race_id", "at"(JST), "close", "min_to_close", "votes"(3連単の発売票数), "n", "v"(3連単オッズ、車番の順列順)}
  前回とオッズ・票数が同じ時点は保存しない。
時間切れ（GitHubの1回の上限）になったら /tmp/odds_watch_continue を作り、ワークフローが次の回を起動する。
"""
import argparse
import gzip
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .kdreams import Client, SiteDown, parse_race
from .run import Store, races_on, today_jst

JST = timezone(timedelta(hours=9))
WINDOW = 12      # 締切何分前から監視するか
FAST = 3         # 締切何分前から細かくするか
STEP_SLOW, STEP_FAST = 60, 20
AFTER = 2        # 締切何分後に最後の1回を取るか
END_HM = "23:59"


def now_jst():
    return datetime.now(JST)


def close_dt(d, hm):
    try:
        h, m = map(int, hm.split(":"))
    except (AttributeError, ValueError):
        return None
    return datetime(d.year, d.month, d.day, h, m, tzinfo=JST)


def push(msg):
    subprocess.run("cd data && (git checkout -- state 2>/dev/null; git clean -fdq state 2>/dev/null); cd .. && "
                   f"bash tools/data_branch.sh push '{msg}'", shell=True)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--max-minutes", type=float, default=340)
    ap.add_argument("--push-every", type=float, default=20, help="何分ごとに data ブランチへ保存するか")
    ap.add_argument("--no-push", action="store_true")
    a = ap.parse_args(argv)
    t0 = time.time()
    d = today_jst()
    if now_jst().strftime("%H:%M") >= "23:30":
        print("本日の監視は終了時刻を過ぎています"); return
    st, cli = Store(a.data), Client(sleep=1.0)
    races, _ = races_on(cli, st, d)
    todo = {}
    for r in races:
        c = close_dt(d, r.get("close_time"))
        if c and not r["result"] and c > now_jst() - timedelta(minutes=AFTER):
            todo[r["race_id"]] = {"url": r["url"], "close": c, "next": c - timedelta(minutes=WINDOW), "last": None, "done": False}
    print(d, "監視対象", len(todo), "レース", flush=True)
    folder = a.data / "odds_ts" / d.isoformat()
    folder.mkdir(parents=True, exist_ok=True)
    last_push, n_saved = time.time(), 0
    stat, last_stat, warned = {"ok": 0, "same": 0, "no_html": 0, "no_odds": 0}, time.time(), set()
    while True:
        if all(x["done"] for x in todo.values()):
            break
        if (time.time() - t0) / 60 > a.max_minutes:
            Path("/tmp/odds_watch_continue").write_text("1")
            print("時間切れ。次の回に引き継ぎます", flush=True)
            break
        now = now_jst()
        due = sorted([(rid, x) for rid, x in todo.items() if not x["done"] and x["next"] <= now], key=lambda t: t[1]["close"])
        for rid, x in due:
            try:
                html = cli.get(x["url"])
            except SiteDown as e:
                print("サイト応答なし", e, file=sys.stderr, flush=True)
                time.sleep(60)
                break
            at = now_jst()
            r = parse_race(html, x["url"]) if html else None
            t = (r or {}).get("odds", {}).get("3連単")
            mins = (x["close"] - at).total_seconds() / 60
            if not html:
                stat["no_html"] += 1
                print(f"  ! 取得失敗 {rid} あと{mins:.1f}分 状態{getattr(cli, 'last_status', '?')}", flush=True)
            elif not t:
                stat["no_odds"] += 1
                if rid not in warned:
                    warned.add(rid)
                    title = (r or {}).get("title_raw") or html[:200].replace("\n", " ")
                    print(f"  ! オッズなし {rid} あと{mins:.1f}分 長さ{len(html)} {title[:120]}", flush=True)
            if t:
                key = (tuple(t["v"]), r["odds"].get("votes", {}).get("3連単"))
                if key != x["last"]:
                    with gzip.open(folder / f"{rid}.jsonl.gz", "at", encoding="utf-8") as f:
                        f.write(json.dumps({"race_id": rid, "at": at.isoformat(timespec="seconds"), "close": x["close"].strftime("%H:%M"),
                                            "min_to_close": round(mins, 2), "votes": key[1], "n": t["n"], "v": t["v"]}, ensure_ascii=False) + "\n")
                    x["last"] = key
                    n_saved += 1
                    stat["ok"] += 1
                else:
                    stat["same"] += 1
            if mins < -AFTER + 0.01 or (r and r.get("result")):
                x["done"] = True
            elif mins <= 0:
                x["next"] = x["close"] + timedelta(minutes=AFTER)
            else:
                step = STEP_FAST if mins <= FAST else STEP_SLOW
                x["next"] = at + timedelta(seconds=step)
        if time.time() - last_stat >= 600:
            left = sum(not x["done"] for x in todo.values())
            print(now_jst().strftime("%H:%M"), "取得", stat, "残り", left, "レース", "失敗連続", cli.fail_streak, flush=True)
            last_stat = time.time()
        if not a.no_push and (time.time() - last_push) / 60 >= a.push_every:
            push("odds watch")
            last_push = time.time()
        nxt = min([x["next"] for x in todo.values() if not x["done"]], default=now_jst())
        time.sleep(min(max((nxt - now_jst()).total_seconds(), 2), 60))
    print("保存", n_saved, "時点", flush=True)


if __name__ == "__main__":
    main()
