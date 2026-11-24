"""
収集の健康チェック（30分おき）とダッシュボード用データの作成。
  python tools/health.py --data DIR [--no-act]
異常は GitHub の Issue で知らせ（内容が変わったときだけ追記）、直ったら閉じる。直せるものは自動で直す。
結果:
  DIR/health/status.json     最新の結果（problems / notes / actions）
  DIR/health/log.jsonl       履歴
  DIR/health/dashboard.json  ダッシュボード（docs/index.html）用。8人の係の状態・稼働率・全体の進捗

確認と自動対応
  1. 開催時間中（8:40〜23:20）にオッズ監視が動いているか → 止まっていれば起動
  2. オッズ監視が固まっていないか（状態ファイルが45分以上更新なし）→ 止めて起動し直す（夜の待機中 state=standby は除く）
  3. オッズの記録が60分以上止まっていないか
  4. 前日のオッズ記録の取りこぼし（締切3分以内の記録があるレースが9割未満）
  5. 毎朝の収集で前日のレース結果が保存されたか → 9:00を過ぎて無く、収集が動いていなければ起動
  6. AIの学習が8日以上成功していない
  7. 収集・オッズ監視・学習・天気の直近の失敗
"""
import argparse
import glob
import gzip
import json
import os
import traceback
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))
REPO = os.environ.get("GITHUB_REPOSITORY", "exnull0511-create/keirinbusines")
TOKEN = os.environ.get("GITHUB_TOKEN")
TITLE = "【自動監視】データ収集の異常"
WATCH_START = "2026-11-20"   # オッズ監視を1日通して動かし始めた日（取りこぼしの確認はこの日から）
ACTIVE = ("in_progress", "queued", "waiting", "pending", "requested")
RULE_A, RULE_B = "A_50-300倍_期待値1.2", "B_10-300倍_期待値1.1"


def api(path, method="GET", body=None):
    req = urllib.request.Request(f"https://api.github.com/repos/{REPO}{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {TOKEN}", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        t = r.read()
        return json.loads(t) if t else {}


def runs(wf, n=20):
    try:
        return api(f"/actions/workflows/{wf}/runs?per_page={n}").get("workflow_runs", [])
    except Exception as e:
        print("API失敗", wf, e)
        return []


def jst(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(JST)


def read_jsonl_gz(p):
    try:
        return [json.loads(l) for l in gzip.open(p, "rt", encoding="utf-8")]
    except Exception:
        return []


def day_races(data, d):
    """その日のレース（結果が保存済みなら races/、まだなら当日の live/）。戻り値: (レース, 結果保存済みか)"""
    rf = data / "races" / d[:4] / f"{d}.jsonl.gz"
    if rf.exists():
        return [r for r in read_jsonl_gz(rf) if r.get("close_time")], True
    lf = data / "live" / f"{d}.json.gz"
    if lf.exists():
        try:
            return [r for r in json.load(gzip.open(lf, "rt", encoding="utf-8"))["races"] if r.get("close_time")], False
        except Exception:
            pass
    return [], False


def watch_coverage(data, d, races, upto=None):
    """締切を過ぎたレースのうち、オッズの記録・締切3分以内の記録・推奨の判定があるレースの数"""
    closed = [r for r in races if upto is None or r["close_time"] <= upto]
    rec = near = 0
    for r in closed:
        xs = read_jsonl_gz(data / "odds_ts" / d / f"{r['race_id']}.jsonl.gz")
        rec += bool(xs)
        near += any(0 < x["min_to_close"] <= 3 for x in xs)
    judged = set()
    pf = data / "paper" / f"{d}.jsonl"
    if pf.exists():
        for l in open(pf, encoding="utf-8"):
            try:
                x = json.loads(l)
                if x.get("src") == "watch":
                    judged.add(x["race_id"])
            except Exception:
                pass
    judged &= {r["race_id"] for r in closed}
    return {"races": len(closed), "recorded": rec, "near": near, "judged": len(judged)}


def success_rate(rs, n=14):
    done = [r for r in rs if r["status"] == "completed" and r["conclusion"] not in ("cancelled", "skipped")][:n]
    return (sum(r["conclusion"] == "success" for r in done) / len(done)) if done else None, done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--no-act", action="store_true", help="Issue作成・再起動をしない（試験用）")
    a = ap.parse_args()
    now = datetime.now(JST)
    hm = now.strftime("%H:%M")
    today, yday = now.date().isoformat(), (now.date() - timedelta(days=1)).isoformat()
    problems, notes, actions = [], [], []

    def act(desc, fn):
        if a.no_act or not TOKEN:
            actions.append(desc + "（試験のため実行せず）")
            return
        try:
            fn()
            actions.append(desc)
        except Exception as e:
            actions.append(f"{desc} に失敗: {e}")

    W = {wf: runs(wf) for wf in ("odds-watch.yml", "collect.yml", "train-ai.yml", "weather.yml", "health.yml", "video.yml")}
    ws = {}
    try:
        ws = json.load(open(a.data / "health" / "watch_status.json"))
    except Exception:
        pass

    # 1, 2. オッズ監視
    active = [r for r in W["odds-watch.yml"] if r["status"] in ACTIVE]
    if "08:40" <= hm <= "23:20":
        if not active:
            problems.append("開催時間中なのにオッズ監視が動いていなかった（自動で起動した）")
            act("オッズ監視を起動", lambda: api("/actions/workflows/odds-watch.yml/dispatches", "POST", {"ref": "main"}))
        else:
            run = active[0]
            started = jst(run.get("run_started_at") or run["created_at"])
            age = (now - datetime.fromisoformat(ws["updated_at"])).total_seconds() / 60 if ws.get("updated_at") else None
            if run["status"] == "in_progress" and (now - started).total_seconds() > 50 * 60 and (age is None or age > 45) \
                    and ws.get("state") != "standby":   # 夜の待機中（その日のレースが終わった後）は状態を更新しない
                problems.append(f"オッズ監視が固まっている（状態の更新が{'なし' if age is None else f'{age:.0f}分前'}）。止めて起動し直した")
                act("オッズ監視を再起動", lambda: (api(f"/actions/runs/{run['id']}/cancel", "POST"),
                                              api("/actions/workflows/odds-watch.yml/dispatches", "POST", {"ref": "main"})))

    # 3. 最新の記録の新しさ
    latest = None
    for f in glob.glob(str(a.data / "odds_ts" / today / "*.jsonl.gz")):
        xs = read_jsonl_gz(f)
        if xs:
            t = datetime.fromisoformat(xs[-1]["at"])
            latest = max(latest or t, t)
    if latest:
        age = (now - latest).total_seconds() / 60
        notes.append(f"最新のオッズ記録: {latest.strftime('%H:%M')}（{age:.0f}分前）")
        if "09:40" <= hm <= "22:50" and age > 60:
            problems.append(f"オッズの記録が{age:.0f}分間止まっている（最後 {latest.strftime('%H:%M')}）")
    elif "09:40" <= hm <= "22:50":
        problems.append("今日のオッズ記録がまだ1件もない")

    # 4, 5. 前日の取りこぼし・毎朝の収集
    yr, ydone = day_races(a.data, yday)
    if ydone:
        c = watch_coverage(a.data, yday, yr)
        notes.append(f"前日 {yday}: {c['races']}レース中 締切3分以内の記録{c['near']}・推奨の判定{c['judged']}")
        if yday >= WATCH_START and c["near"] < 0.9 * c["races"]:
            have = {Path(f).name.split(".")[0] for f in glob.glob(str(a.data / "odds_ts" / yday / "*.jsonl.gz"))}
            miss = sorted((r["close_time"], r["venue"], r["race_no"]) for r in yr if r["race_id"] not in have)
            problems.append(f"前日のオッズ記録の取りこぼし: 締切3分以内の記録があるのは{c['near']}/{c['races']}レース" +
                            (f"（記録なしの例 {'、'.join(f'{t} {v}{n}R' for t, v, n in miss[:6])}）" if miss else ""))
    elif hm >= "09:00":
        col_active = [r for r in W["collect.yml"] if r["status"] in ACTIVE]
        problems.append(f"毎朝の収集で前日（{yday}）のレース結果が保存されていない" + ("（収集は実行中）" if col_active else "（自動で起動した）"))
        if not col_active:
            act("毎朝の収集を起動", lambda: api("/actions/workflows/collect.yml/dispatches", "POST", {"ref": "main"}))

    # 6. AIの学習
    tr_ok = [r for r in W["train-ai.yml"] if r["conclusion"] == "success"]
    if not tr_ok or (now - jst(tr_ok[0]["created_at"])).days >= 8:
        problems.append("AIの学習が8日以上成功していない")

    # 7. 直近の失敗
    for wf, name, serious in [("collect.yml", "毎朝の収集", True), ("odds-watch.yml", "オッズ監視", True),
                              ("train-ai.yml", "AIの学習", True), ("weather.yml", "天気", False)]:
        _, done = success_rate(W[wf], 1)
        if done and done[0]["conclusion"] != "success":
            msg = f"{name}の直近の実行が失敗（{jst(done[0]['created_at']).strftime('%m/%d %H:%M')}） {done[0]['html_url']}"
            (problems if serious else notes).append(msg)

    prev = {}
    try:
        prev = json.load(open(a.data / "health" / "status.json"))
    except Exception:
        pass
    status = {"checked_at": now.isoformat(timespec="seconds"), "ok": not problems, "problems": problems, "notes": notes, "actions": actions}
    out = a.data / "health"
    out.mkdir(parents=True, exist_ok=True)
    (out / "status.json").write_text(json.dumps(status, ensure_ascii=False, indent=1), encoding="utf-8")
    with open(out / "log.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(status, ensure_ascii=False) + "\n")
    print(json.dumps(status, ensure_ascii=False, indent=1))

    try:
        dash = dashboard(a.data, now, W, ws, status)
        (out / "dashboard.json").write_text(json.dumps(dash, ensure_ascii=False), encoding="utf-8")
    except Exception:
        traceback.print_exc()

    if a.no_act or not TOKEN:
        return
    try:
        issues = api("/issues?state=open&per_page=50")
        mine = [i for i in issues if i.get("title") == TITLE]
        if problems:
            body = "\n".join(f"- {p}" for p in problems) + "\n\n" + "\n".join(f"- {n}" for n in notes) + \
                   ("\n\n自動で行ったこと:\n" + "\n".join(f"- {x}" for x in actions) if actions else "") + \
                   f"\n\n確認時刻: {now.strftime('%m/%d %H:%M')}\n\n@{REPO.split('/')[0]}"
            if mine:
                if problems != prev.get("problems"):
                    api(f"/issues/{mine[0]['number']}/comments", "POST", {"body": body})
            else:
                api("/issues", "POST", {"title": TITLE, "body": body})
        elif mine:
            api(f"/issues/{mine[0]['number']}/comments", "POST", {"body": f"直りました（{now.strftime('%m/%d %H:%M')}）\n\n" + "\n".join(f"- {n}" for n in notes)})
            api(f"/issues/{mine[0]['number']}", "PATCH", {"state": "closed"})
    except Exception as e:
        print("Issue の更新に失敗", e)


# ---------------- ダッシュボード ----------------

def dashboard(data, now, W, ws, status):
    today = now.date().isoformat()
    hm = now.strftime("%H:%M")
    days = [(now.date() - timedelta(days=k)).isoformat() for k in range(1, 8)]

    # オッズ監視・直前判定: 過去7日＋今日の記録率
    cov_days = []
    for d in days:
        if d < WATCH_START:
            continue
        rs, done = day_races(data, d)
        if done and rs:
            cov_days.append({"date": d, **watch_coverage(data, d, rs)})
    tr, _ = day_races(data, today)
    tc = watch_coverage(data, today, tr, upto=hm) if tr else {"races": 0, "recorded": 0, "near": 0, "judged": 0}
    allc = cov_days + ([{"date": today, **tc}] if tc["races"] else [])
    tot = {k: sum(c[k] for c in allc) for k in ("races", "recorded", "near", "judged")}
    watch_rate = tot["near"] / tot["races"] if tot["races"] else None
    judge_rate = tot["judged"] / tot["near"] if tot["near"] else None

    # ペーパートレード
    paper = {}
    try:
        paper = json.load(open(data / "paper" / "summary.json"))
    except Exception:
        pass
    rules = paper.get("rules") or {}

    def rule_line(k):
        t = (rules.get(k) or {}).get("total") or {}
        return {"bets": t.get("bets", 0), "hits": t.get("hits", 0), "roi": t.get("roi")}

    today_picks = []
    n_e = 0
    pf = data / "paper" / f"{today}.jsonl"
    if pf.exists():
        last = {}
        for l in open(pf, encoding="utf-8"):
            try:
                x = json.loads(l)
                if x.get("src") == "watch":
                    last[x["race_id"]] = x
            except Exception:
                pass
        names = {r["race_id"]: f"{r['venue']}{r['race_no']}R" for r in tr}
        # 当日一覧に無いレースは、レースIDの先頭2桁（場コード）とレース番号から名前を作る
        code = {}
        for f in sorted(glob.glob(str(data / "races" / "*" / "*.jsonl.gz")))[-30:]:
            for r in read_jsonl_gz(f):
                code.setdefault(r["race_id"][:2], r["venue"])

        def name_of(rid, x):
            return x.get("label") or names.get(rid) or (f"{code[rid[:2]]}{int(rid[-2:])}R" if rid[:2] in code else rid)
        for rid, x in last.items():
            for k, v in (x.get("picks_by") or {}).items():
                for p in v:
                    if k.startswith("E"):   # Eは点数が多いので一覧には出さず数だけ数える
                        n_e += 1
                        continue
                    today_picks.append({"rule": k[:1], "race": name_of(rid, x), "combo": p["combo"], "odds": p["odds"], "ev": p["ev"], "close": x.get("close_time")})
    today_picks.sort(key=lambda p: (p["close"] or "", p["rule"]))

    # データの量
    n_races = 0
    for f in glob.glob(str(data / "races" / "*" / "*.jsonl.gz")):
        try:
            with gzip.open(f, "rb") as g:
                n_races += sum(1 for _ in g)
        except Exception:
            pass
    ts_days = sorted(Path(p).name for p in glob.glob(str(data / "odds_ts" / "*")) if Path(p).is_dir())
    ts_races = len(glob.glob(str(data / "odds_ts" / "*" / "*.jsonl.gz")))
    n_video = 0
    for f in glob.glob(str(data / "video" / "*.jsonl.gz")):
        n_video += sum(1 for x in read_jsonl_gz(f) if x.get("result"))

    def last_run(rs):
        r = rs[0] if rs else None
        return None if not r else {"at": jst(r["created_at"]).isoformat(timespec="minutes"), "status": r["status"], "conclusion": r["conclusion"], "url": r["html_url"]}

    def rate(wf, n=14):
        return success_rate(W.get(wf, []), n)[0]

    def active(wf):
        return any(r["status"] in ACTIVE for r in W.get(wf, []))

    hc = [r for r in W.get("health.yml", []) if (now - jst(r["created_at"])).total_seconds() < 86400]
    health_runs = len(hc) + 1
    health_rate = min(1.0, health_runs / 48)

    wstate = ws.get("state")
    w_age = (now - datetime.fromisoformat(ws["updated_at"])).total_seconds() / 60 if ws.get("updated_at") else None
    if active("odds-watch.yml") and wstate == "watching" and (w_age if w_age is not None else 99) < 30:
        if ws.get("watching"):
            w_doing = "見ている: " + "、".join(f"{x['race']}（締切{x['close']}）" for x in ws["watching"][:3])
            w_mode = "busy"
        else:
            nx = (ws.get("next") or [{}])[0]
            w_doing = f"次の締切を待っている: {nx.get('race', '-')}（{nx.get('close', '-')}）"
            w_mode = "wait"
    elif active("odds-watch.yml") and wstate == "standby":
        w_doing, w_mode = ws.get("note") or "夜の待機中（7:50に当日の準備を始める）", "off"
    elif active("odds-watch.yml"):
        w_doing, w_mode = {"starting": "当日のレース一覧を取得中", "scoring": "AIで当日のレースを採点中"}.get(wstate, "出勤中（準備しています）"), "busy"
    elif "08:40" <= hm <= "23:20":
        w_doing, w_mode = "止まっている（見張り係が起こしに行く）", "down"
    else:
        w_doing, w_mode = "本日の監視は終了。明日7:50に出勤", "off"

    cnt = {c: sum(p["rule"] == c for p in today_picks) for c in "ABCD"}
    j_doing = (f"今日 A{cnt['A']}・B{cnt['B']}・C{cnt['C']}・D{cnt['D']}・E{n_e}点" if today_picks or n_e else
               ("締切3分前から判定しています（今日はまだ推奨なし）" if w_mode in ("busy", "wait") else "待機中"))
    j_mode = "busy" if w_mode == "busy" else ("wait" if w_mode == "wait" else w_mode)

    col = last_run(W.get("collect.yml"))
    col_doing = ("前日分を収集中" if active("collect.yml") else
                 (f"前日分の収集を完了（{col['at'][11:16]}）" if col and col["conclusion"] == "success" and col["at"][:10] == today else "明朝7:10に前日分を収集"))
    trn = [r for r in W.get("train-ai.yml", []) if r["conclusion"] == "success"]
    trn_doing = "AIを学習中" if active("train-ai.yml") else (f"最後の学習 {jst(trn[0]['created_at']).strftime('%m/%d')}・次は月曜5:17" if trn else "学習待ち")
    wea = last_run(W.get("weather.yml"))

    agents = [
        {"id": "watch", "name": "オッズ監視係", "role": "締切12分前から3連単オッズを記録", "mode": w_mode, "doing": w_doing,
         "rate": watch_rate, "rate_label": "締切3分以内の記録率（7日）",
         "stats": {"今日の記録": f"{tc['near']}/{tc['races']}レース", "今回の取得": (ws.get("stat") or {}).get("ok", 0)}},
        {"id": "judge", "name": "直前判定係", "role": "締切直前のオッズで推奨A/Bを判定", "mode": j_mode, "doing": j_doing,
         "rate": judge_rate, "rate_label": "判定できた率（7日）",
         "stats": {f"{k[0]} 累計": rule_line(k) for k in sorted(rules)} or {"A 累計": rule_line(RULE_A), "B 累計": rule_line(RULE_B)}},
        {"id": "collect", "name": "収集係", "role": "毎朝、前日の全レースの結果と確定オッズを保存", "mode": "busy" if active("collect.yml") else "wait",
         "doing": col_doing, "rate": rate("collect.yml"), "rate_label": "成功率（直近14回）", "stats": {"保存済み": f"{n_races:,}レース"}, "last": col},
        {"id": "train", "name": "学習係", "role": "毎週、確率AIとライン補正を学習し直す", "mode": "busy" if active("train-ai.yml") else "wait",
         "doing": trn_doing, "rate": rate("train-ai.yml", 8), "rate_label": "成功率（直近8回）", "last": last_run(W.get("train-ai.yml"))},
        {"id": "weather", "name": "天気係", "role": "3時間ごとに各場の風・雨・気温を取得", "mode": "busy" if active("weather.yml") else "wait",
         "doing": "天気を取得中" if active("weather.yml") else f"最後の取得 {wea['at'][5:16].replace('T', ' ') if wea else '-'}",
         "rate": rate("weather.yml", 20), "rate_label": "成功率（直近20回）", "last": wea},
        {"id": "video", "name": "映像分析係", "role": "Geminiでレース映像を数値化", "mode": "busy" if active("video.yml") else "off",
         "doing": "映像を分析中" if active("video.yml") else "お休み中（Google Cloudの支払い設定待ち）", "rate": None, "rate_label": "-",
         "stats": {"分析済み": f"{n_video}レース"}, "last": last_run(W.get("video.yml"))},
        {"id": "health", "name": "見張り係", "role": "30分おきに全員の様子を確認し、止まっていたら起こす", "mode": "alert" if status["problems"] else "wait",
         "doing": ("異常あり: " + status["problems"][0]) if status["problems"] else f"全員異常なし（{now.strftime('%H:%M')}確認）",
         "rate": health_rate, "rate_label": "直近24時間の確認回数 / 48", "stats": {"確認(24h)": health_runs, "自動対応": len(status["actions"])}},
        {"id": "report", "name": "報告係（Claude）", "role": "毎朝9:43に様子をまとめてスマホに通知", "mode": "busy" if "09:40" <= hm <= "09:55" else "wait",
         "doing": "朝の報告をまとめ中" if "09:40" <= hm <= "09:55" else "明朝9:43に報告", "rate": None, "rate_label": "-"},
    ]
    rates = [x["rate"] for x in agents if x["rate"] is not None]
    a_tot = rule_line(RULE_A)
    return {
        "updated_at": now.isoformat(timespec="minutes"),
        "ok": status["ok"], "problems": status["problems"], "actions": status["actions"],
        "overall_rate": sum(rates) / len(rates) if rates else None,
        "agents": agents,
        "today": {"date": today, "races": len(tr), "closed": tc["races"], "near": tc["near"], "judged": tc["judged"], "picks": today_picks[:60], "n_e": n_e},
        "progress": [
            {"label": "レースのデータベース", "value": n_races, "unit": "レース", "note": "2024年1月から。毎朝追加"},
            {"label": "締切前オッズの記録", "value": len(ts_days), "unit": "日", "goal": 60, "note": f"{ts_races:,}レース分。60日分たまったら確率への組み込みを検証"},
            {"label": "推奨Aのペーパートレード", "value": a_tot["bets"], "unit": "点", "goal": 1000,
             "note": f"的中{a_tot['hits']}・回収率{a_tot['roi'] if a_tot['roi'] is not None else '-'}%。1,000点で本番を判断"},
            {"label": "映像分析", "value": n_video, "unit": "レース", "goal": 300, "note": "300レースで効果を検証（支払い設定待ち）"},
        ],
        "coverage_days": cov_days,
    }


if __name__ == "__main__":
    main()
