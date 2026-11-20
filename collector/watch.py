"""
締切前のオッズ監視と、締切直前の推奨判定（ペーパートレード）。開催時間中ずっと動く。
  python -m collector.watch --data DIR --prepare   # 当日のレース一覧を先に取っておく（/tmp に保存）
  python -m collector.watch --data DIR --wait      # 前の回が終わるのを待ち、その保存分を取り込む
  python -m collector.watch --data DIR [--max-minutes 335]

引き継ぎ（監視が途切れないように）
  GitHubの1回の上限（約6時間）があるので、終わる約45分前に次の回を起動する。次の回はレース一覧の取得（数分かかる）を
  済ませてから「引き継ぎ待ち」に入る。今の回はそれを見つけたら、締切4分前以内のレースが無い瞬間に保存して終わる。
  次の回は終了を確認して最新のデータを取り込み、すぐ記録を再開する（途切れるのは1分弱）。
  手動の起動（コードの更新後など）も同じ流れで引き継ぐので、今の回を止める必要はない。
  定期実行（予備）で起動され、すでに監視が動いていれば何もせず終わる。

オッズの記録
  締切12分前〜3分前: 1分おき / 3分前〜締切: 20秒おき / 締切2分後: 1回
  Kドリームスのオッズ更新は約1分ごとで、前回と同じなら保存しないので、記録は3分前以降も約1分おきになる。
  保存先: DIR/odds_ts/YYYY-MM-DD/<race_id>.jsonl.gz（追記。1行 = 1時点。前回と同じなら保存しない）
    {"race_id", "at"(JST), "close", "min_to_close", "votes"(3連単の発売票数), "n", "v"(3連単オッズ、車番の順列順)}

推奨の判定
  起動時に当日の全レースをAIで採点し（DIR/live/日付.json.gz も更新）、目ごとの補正（AIの確率 ÷ 市場の確率）を覚えておく。
  締切3分前からは、取ったオッズで市場の確率を出し直して補正を掛け、推奨の買い方A/Bを判定して
  DIR/paper/YYYY-MM-DD.jsonl に追記する。集計（ai/paper.py）は各レースの締切前の最後の判定を使う。

状態の保存: DIR/health/watch_status.json（今どのレースを見ているか、取得件数など。ダッシュボード用）
時間切れ（GitHubの1回の上限）になったら /tmp/odds_watch_continue を作り、ワークフローが次の回を起動する。
"""
import argparse
import gzip
import json
import os
import pickle
import subprocess
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from .kdreams import Client, SiteDown, parse_race
from .run import Store, races_on, today_jst

JST = timezone(timedelta(hours=9))
WINDOW = 12      # 締切何分前から監視するか
FAST = 3         # 締切何分前から細かくするか（推奨の判定もここから）
STEP_SLOW, STEP_FAST = 60, 20
AFTER = 2        # 締切何分後に最後の1回を取るか
HANDOVER_AT = 45   # 上限の何分前に次の回を起動するか
CALM = 4           # 引き継ぎで終わるとき、締切何分前以内のレースが無いことを確かめるか
CALM_WAIT = 12     # その瞬間を最大何分待つか（来なければそのまま引き継ぐ）
WAIT_MAX = 20      # 次の回が前の回の終了を最大何分待つか（過ぎたら前の回を止める）
WAIT_STEP = "引き継ぎ待ち"   # odds-watch.yml のステップ名（前の回はこれで次の回の準備完了を知る）
RACES_CACHE = Path("/tmp/watch_races.json")
SKIP = Path("/tmp/watch_skip")
ACTIVE = ("queued", "in_progress", "waiting", "pending", "requested")


def gh_api(path, method="GET", body=None):
    """GitHub API（このリポジトリ）。Actions の外では使わない。"""
    import urllib.request
    req = urllib.request.Request(f"https://api.github.com/repos/{os.environ['GITHUB_REPOSITORY']}{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
                                          "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        t = r.read()
        return json.loads(t) if t else {}


def in_actions():
    return bool(os.environ.get("GITHUB_RUN_ID") and os.environ.get("GITHUB_TOKEN") and os.environ.get("GITHUB_REPOSITORY"))


def other_runs():
    """自分以外の動いている odds-watch の回（古い順）。"""
    me = int(os.environ["GITHUB_RUN_ID"])
    rs = gh_api("/actions/workflows/odds-watch.yml/runs?per_page=20").get("workflow_runs", [])
    return sorted([r for r in rs if r["id"] != me and r["status"] in ACTIVE], key=lambda r: r["id"])


def successor_ready():
    """自分より新しい回が「引き継ぎ待ち」に入っているか。"""
    me = int(os.environ["GITHUB_RUN_ID"])
    for r in other_runs():
        # 予備の定期実行は、監視が動いていれば何もせず終わるので、引き継ぎ先にしない
        if r["id"] < me or r["status"] != "in_progress" or r.get("event") == "schedule":
            continue
        for j in gh_api(f"/actions/runs/{r['id']}/jobs").get("jobs", []):
            if any(s["name"] == WAIT_STEP and s["status"] == "in_progress" for s in j.get("steps", [])):
                return r["id"]
    return None


def dispatch_next():
    gh_api("/actions/workflows/odds-watch.yml/dispatches", "POST", {"ref": os.environ.get("GITHUB_REF_NAME") or "main"})


def prepare(data):
    """準備: 予備の定期実行なら必要か判断し、当日のレース一覧を取って /tmp に置く。"""
    SKIP.unlink(missing_ok=True)
    if in_actions() and os.environ.get("GITHUB_EVENT_NAME") == "schedule":
        try:
            older = [r for r in other_runs() if r["id"] < int(os.environ["GITHUB_RUN_ID"])]
        except Exception as e:
            print("  ! 他の回の確認に失敗", e, flush=True)
            older = []
        if older:
            SKIP.write_text("1")
            print("監視はすでに動いているので、この回は何もしない", [r["id"] for r in older], flush=True)
            return
    d = today_jst()
    if now_jst().strftime("%H:%M") >= "23:30":
        return
    st, cli = Store(data), Client(sleep=1.0)
    for _ in range(3):
        try:
            races, _ = races_on(cli, st, d)
            RACES_CACHE.write_text(json.dumps({"date": d.isoformat(), "races": races}, ensure_ascii=False), encoding="utf-8")
            print(d, "レース一覧", len(races), flush=True)
            return
        except Exception as e:
            print("  ! レース一覧の取得に失敗", e, flush=True)
            time.sleep(120)


def wait_older(data):
    """引き継ぎ待ち: 前の回が保存して終わるのを待ち、その保存分を data に取り込む。"""
    if SKIP.exists() or not in_actions():
        return
    me, t0, cancelled = int(os.environ["GITHUB_RUN_ID"]), time.time(), False
    while True:
        try:
            older = [r for r in other_runs() if r["id"] < me]
        except Exception as e:
            print("  ! 他の回の確認に失敗", e, flush=True)
            older = []
        if not older:
            break
        waited = (time.time() - t0) / 60
        if waited > WAIT_MAX and not cancelled:
            print(f"前の回が{WAIT_MAX}分たっても終わらないので止める", [r["id"] for r in older], flush=True)
            for r in older:
                try:
                    gh_api(f"/actions/runs/{r['id']}/cancel", "POST")
                except Exception as e:
                    print("  ! 停止に失敗", r["id"], e, flush=True)
            cancelled = True
        if waited > WAIT_MAX + 5:
            print("前の回の終了を確認できないまま始める", flush=True)
            break
        time.sleep(10)
    print(f"前の回の終了を確認（{(time.time() - t0) / 60:.1f}分待った）", flush=True)
    # 前の回が保存した最新の data を取り込む（この回はまだ何も保存していないので、そのまま置き換えてよい）
    subprocess.run(f"cd '{data}' && git fetch -q --depth 1 origin data && git reset -q --hard FETCH_HEAD && git clean -fdq state", shell=True)


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


def score_today(data, d, races):
    """AIで採点し、レースごとの補正（AIの確率 ÷ 市場の確率）を返す。失敗しても監視は続ける。"""
    try:
        from ai.live import write_live
        from ai import line
        meta = pickle.load(open(data / "models" / "latest.pkl", "rb"))
        scores = write_live(data, d, meta, races)
        fac = {}
        for rid, s in scores.items():
            p, q = np.array(s["p"]), np.array(s["q"])
            fac[rid] = np.where(q > 0, p / np.where(q > 0, q, 1), 0)
        return fac, line
    except Exception:
        traceback.print_exc()
        print("  ! AIの採点に失敗（オッズの記録だけ続ける）", flush=True)
        return {}, None


def judge(line, r, fac, v):
    """その時点のオッズで推奨A/Bを判定。"""
    o = np.array([x or 0 for x in v], float)
    if len(o) != len(fac):
        return None
    q = np.where(o > 0, 1 / np.where(o > 0, o, 1), 0)
    if q.sum() <= 0:
        return None
    p = q / q.sum() * fac
    p = p / p.sum() if p.sum() > 0 else p
    return {k: line.picks(r, p, o, rule) for k, rule in line.PICKS.items()}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--max-minutes", type=float, default=335)
    ap.add_argument("--push-every", type=float, default=10, help="何分ごとに data ブランチへ保存するか")
    ap.add_argument("--no-push", action="store_true")
    ap.add_argument("--prepare", action="store_true", help="レース一覧を先に取るだけ")
    ap.add_argument("--wait", action="store_true", help="前の回の終了を待つだけ")
    a = ap.parse_args(argv)
    if a.prepare:
        return prepare(a.data)
    if a.wait:
        return wait_older(a.data)
    if SKIP.exists():
        print("この回は何もしない（監視はすでに動いている）")
        return
    t0 = time.time()
    d = today_jst()
    hs = a.data / "health"
    hs.mkdir(parents=True, exist_ok=True)

    def status(**kw):
        try:
            (hs / "watch_status.json").write_text(json.dumps({"updated_at": now_jst().isoformat(timespec="seconds"), "date": d.isoformat(),
                                                              "started_at": datetime.fromtimestamp(t0, JST).isoformat(timespec="seconds"), **kw},
                                                             ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    if now_jst().strftime("%H:%M") >= "23:30":
        print("本日の監視は終了時刻を過ぎています")
        status(state="done", note="本日の監視は終了")
        return
    st, cli = Store(a.data), Client(sleep=1.0)
    status(state="starting", note="当日のレースを取得中")
    races = None
    try:
        c = json.loads(RACES_CACHE.read_text(encoding="utf-8"))
        if c.get("date") == d.isoformat():
            races = c["races"]
            print("準備で取ったレース一覧を使う", len(races), flush=True)
    except Exception:
        pass
    for _ in range(0 if races is not None else 3):
        try:
            races, _ = races_on(cli, st, d)
            break
        except Exception as e:
            print("  ! レース一覧の取得に失敗", e, flush=True)
            time.sleep(120)
    races = races or []
    R = {r["race_id"]: r for r in races}
    todo = {}
    for r in races:
        c = close_dt(d, r.get("close_time"))
        if c and not r["result"] and c > now_jst() - timedelta(minutes=AFTER):
            todo[r["race_id"]] = {"url": r["url"], "close": c, "next": c - timedelta(minutes=WINDOW), "last": None, "done": False,
                                  "label": f"{r['venue']}{r['race_no']}R"}
    print(d, "監視対象", len(todo), "レース", flush=True)
    # AIの採点は数分かかるので、裏で行い、その間もオッズの記録は続ける
    import threading
    ai = {"fac": {}, "line": None, "ready": False}

    def _score():
        f, l = score_today(a.data, d, [r for r in races if r["race_id"] in todo])
        ai.update(fac=f, line=l, ready=True)
        print("採点", len(f), "レース", flush=True)

    if todo:
        threading.Thread(target=_score, daemon=True).start()
    folder = a.data / "odds_ts" / d.isoformat()
    folder.mkdir(parents=True, exist_ok=True)
    paper = a.data / "paper" / f"{d.isoformat()}.jsonl"
    paper.parent.mkdir(parents=True, exist_ok=True)
    last_push, last_stat = 0, time.time()   # 最初の1周のあとすぐ保存（状態を早く見せる）
    stat = {"ok": 0, "same": 0, "no_html": 0, "no_odds": 0, "errors": 0, "judged": 0, "picks": 0}
    warned = set()
    ho = {"dispatched": False, "ready": None, "since": None, "checked": 0}
    handed = False
    while True:
        if all(x["done"] for x in todo.values()):
            break
        el = (time.time() - t0) / 60
        if in_actions():
            # 上限が近づいたら次の回を起動（次の回は準備を済ませてから「引き継ぎ待ち」に入る）
            if el > a.max_minutes - HANDOVER_AT and not ho["dispatched"]:
                try:
                    dispatch_next()
                    ho["dispatched"] = True
                    print(now_jst().strftime("%H:%M"), "次の回を起動（準備ができたら引き継ぐ）", flush=True)
                except Exception as e:
                    print("  ! 次の回の起動に失敗", e, flush=True)
            # 次の回（手動の起動も含む）の準備ができたか、1分ごとに確認
            if not ho["ready"] and time.time() - ho["checked"] >= 60:
                ho["checked"] = time.time()
                try:
                    ho["ready"] = successor_ready()
                    if ho["ready"]:
                        ho["since"] = time.time()
                        print(now_jst().strftime("%H:%M"), "次の回の準備ができた", ho["ready"], flush=True)
                except Exception as e:
                    print("  ! 次の回の確認に失敗", e, flush=True)
            if ho["ready"]:
                soon = [x for x in todo.values() if not x["done"] and 0 < (x["close"] - now_jst()).total_seconds() / 60 <= CALM]
                if not soon or (time.time() - ho["since"]) / 60 > CALM_WAIT:
                    print(now_jst().strftime("%H:%M"), "次の回に引き継いで終わる", "（締切直前のレースあり）" if soon else "", flush=True)
                    handed = True
                    break
        if el > a.max_minutes:
            if not ho["ready"]:
                Path("/tmp/odds_watch_continue").write_text("1")   # 次の回をワークフローで起動
            print("時間切れ。次の回に引き継ぎます", flush=True)
            break
        now = now_jst()
        due = sorted([(rid, x) for rid, x in todo.items() if not x["done"] and x["next"] <= now], key=lambda t: t[1]["close"])
        for rid, x in due:
            try:
                html = cli.get(x["url"])
            except SiteDown as e:
                print("  ! サイト応答なし", e, flush=True)
                cli.fail_streak = 0
                time.sleep(60)
                break
            at = now_jst()
            mins = (x["close"] - at).total_seconds() / 60
            try:
                r = parse_race(html, x["url"]) if html else None
                t = (r or {}).get("odds", {}).get("3連単")
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
                        stat["ok"] += 1
                        # 締切3分前〜締切: 推奨の判定（ペーパートレード）
                        if 0 < mins <= FAST and rid in ai["fac"] and ai["line"]:
                            pk = judge(ai["line"], R[rid], ai["fac"][rid], t["v"])
                            if pk is not None:
                                with open(paper, "a", encoding="utf-8") as f:
                                    f.write(json.dumps({"race_id": rid, "at": at.isoformat(timespec="seconds"), "src": "watch",
                                                        "min_to_close": round(mins, 2), "close_time": x["close"].strftime("%H:%M"),
                                                        "picks": next(iter(pk.values())), "picks_by": pk}, ensure_ascii=False) + "\n")
                                stat["judged"] += 1
                                stat["picks"] += sum(len(v) for v in pk.values())
                    else:
                        stat["same"] += 1
                if mins < -AFTER + 0.01 or (r and r.get("result")):
                    x["done"] = True
                elif mins <= 0:
                    x["next"] = x["close"] + timedelta(minutes=AFTER)
                else:
                    x["next"] = at + timedelta(seconds=STEP_FAST if mins <= FAST else STEP_SLOW)
            except Exception:
                stat["errors"] += 1
                traceback.print_exc()
                x["next"] = at + timedelta(seconds=STEP_SLOW)
                if mins < -AFTER:
                    x["done"] = True
        upcoming = sorted([x for x in todo.values() if not x["done"]], key=lambda x: x["close"])
        watching = [x for x in upcoming if x["close"] - timedelta(minutes=WINDOW) <= now_jst()]
        status(state="watching", targets=len(todo), left=len(upcoming), stat=stat, ai_ready=ai["ready"],
               watching=[{"race": x["label"], "close": x["close"].strftime("%H:%M")} for x in watching[:6]],
               next=[{"race": x["label"], "close": x["close"].strftime("%H:%M")} for x in upcoming[:3]])
        if time.time() - last_stat >= 600:
            print(now_jst().strftime("%H:%M"), "取得", stat, "残り", len(upcoming), "レース", "失敗連続", cli.fail_streak, flush=True)
            last_stat = time.time()
        if not a.no_push and (time.time() - last_push) / 60 >= a.push_every:
            push("odds watch")
            last_push = time.time()
        nxt = min([x["next"] for x in todo.values() if not x["done"]], default=now_jst())
        time.sleep(min(max((nxt - now_jst()).total_seconds(), 2), 60))
    status(state="done" if all(x["done"] for x in todo.values()) and not handed else "handover", targets=len(todo),
           left=sum(not x["done"] for x in todo.values()), stat=stat)
    print("保存", stat, flush=True)


if __name__ == "__main__":
    main()
