"""
収集の健康チェック（30分おき）。異常は GitHub の Issue で知らせ、直ったら閉じる。オッズ監視が止まっていれば再起動する。
  python tools/health.py --data DIR
結果: DIR/health/status.json（最新）と DIR/health/log.jsonl（履歴）

確認すること
  1. 開催時間中（8:40〜23:20）にオッズ監視が動いているか → 止まっていれば起動し直す
  2. オッズ監視の最新の記録が古すぎないか（開催時間中に60分以上新しい記録がない）
  3. 前日のオッズ記録の取りこぼし（締切前の記録があるレースの割合が90%未満）
  4. 毎朝の収集で前日のレース結果が保存されたか（9:30以降に確認）
  5. 毎朝の推奨の集計（paper/summary.json）が前日分まで更新されているか
  6. 直近のワークフローの失敗（収集・オッズ監視・学習）
"""
import argparse
import glob
import gzip
import json
import os
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))
REPO = os.environ.get("GITHUB_REPOSITORY", "exnull0511-create/keirinbusines")
TOKEN = os.environ.get("GITHUB_TOKEN")
TITLE = "【自動監視】データ収集の異常"
WATCH_START = "2026-11-20"   # オッズ監視を1日通して動かし始めた日（取りこぼしの確認はこの日から）


def api(path, method="GET", body=None):
    req = urllib.request.Request(f"https://api.github.com/repos/{REPO}{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {TOKEN}", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        t = r.read()
        return json.loads(t) if t else {}


def runs(wf, n=10):
    return api(f"/actions/workflows/{wf}/runs?per_page={n}").get("workflow_runs", [])


def jst(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(JST)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--no-act", action="store_true", help="Issue作成・再起動をしない（試験用）")
    a = ap.parse_args()
    now = datetime.now(JST)
    hm = now.strftime("%H:%M")
    today, yday = now.date().isoformat(), (now.date() - timedelta(days=1)).isoformat()
    problems, notes, actions = [], [], []

    # 1. オッズ監視が動いているか
    ow = runs("odds-watch.yml")
    active = [r for r in ow if r["status"] in ("in_progress", "queued", "waiting", "pending")]
    if "08:40" <= hm <= "23:20":
        if not active:
            problems.append("開催時間中なのにオッズ監視が動いていない（自動で再起動した）")
            if not a.no_act:
                api("/actions/workflows/odds-watch.yml/dispatches", "POST", {"ref": "main"})
                actions.append("オッズ監視を再起動")
        else:
            notes.append(f"オッズ監視: 実行中（{jst(active[0]['created_at']).strftime('%m/%d %H:%M')}開始）")

    # 2. 最新の記録の新しさ
    latest = None
    for f in glob.glob(str(a.data / "odds_ts" / today / "*.jsonl.gz")):
        try:
            *_, last = gzip.open(f, "rt", encoding="utf-8")
            t = datetime.fromisoformat(json.loads(last)["at"])
            latest = max(latest or t, t)
        except Exception:
            pass
    if latest:
        age = (now - latest).total_seconds() / 60
        notes.append(f"最新のオッズ記録: {latest.strftime('%H:%M')}（{age:.0f}分前）")
        if "09:40" <= hm <= "22:50" and age > 60:
            problems.append(f"オッズの記録が{age:.0f}分間止まっている（最後 {latest.strftime('%H:%M')}）")
    elif "09:40" <= hm <= "22:50":
        problems.append("今日のオッズ記録がまだ1件もない")

    # 3. 前日の取りこぼし（前日のレース結果が保存されてから確認）
    rf = a.data / "races" / yday[:4] / f"{yday}.jsonl.gz"
    if rf.exists():
        races = [json.loads(l) for l in gzip.open(rf, "rt", encoding="utf-8")]
        races = [r for r in races if r.get("close_time")]
        have = {Path(f).name.split(".")[0] for f in glob.glob(str(a.data / "odds_ts" / yday / "*.jsonl.gz"))}
        near = 0
        for r in races:
            p = a.data / "odds_ts" / yday / f"{r['race_id']}.jsonl.gz"
            if p.exists():
                ms = [json.loads(l)["min_to_close"] for l in gzip.open(p, "rt", encoding="utf-8")]
                near += any(0 < m <= 3 for m in ms)
        cov = len(have & {r["race_id"] for r in races}) / max(len(races), 1)
        notes.append(f"前日 {yday}: {len(races)}レース中 記録あり{cov*100:.0f}%・締切3分以内の記録あり{near}レース")
        if cov < 0.9 and yday >= WATCH_START:
            missing = sorted((r["close_time"], r["venue"], r["race_no"]) for r in races if r["race_id"] not in have)
            problems.append(f"前日のオッズ記録の取りこぼし: {len(missing)}/{len(races)}レース（例 " +
                            "、".join(f"{c} {v}{n}R" for c, v, n in missing[:6]) + "）")
    elif hm >= "09:30":
        problems.append(f"毎朝の収集で前日（{yday}）のレース結果が保存されていない")

    # 5. 推奨の集計
    sp = a.data / "paper" / "summary.json"
    if sp.exists() and hm >= "09:30":
        upd = json.load(open(sp)).get("updated_from")
        notes.append(f"推奨の集計: {upd} 分まで")

    # 6. 直近のワークフローの失敗
    for wf, name in [("collect.yml", "毎朝の収集"), ("odds-watch.yml", "オッズ監視"), ("train-ai.yml", "AIの学習"), ("live-score.yml", "採点")]:
        rs = runs(wf, 5) if wf != "odds-watch.yml" else ow
        done = [r for r in rs if r["status"] == "completed" and r["conclusion"] != "cancelled"]
        if done and done[0]["conclusion"] != "success":
            msg = f"{name}の直近の実行が失敗（{jst(done[0]['created_at']).strftime('%m/%d %H:%M')}） {done[0]['html_url']}"
            (problems if wf in ("collect.yml", "odds-watch.yml", "train-ai.yml") else notes).append(msg)

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
    if a.no_act or not TOKEN:
        return
    # Issue で知らせる（同じ Issue にコメントを足していく）。直ったら閉じる
    issues = api(f"/issues?state=open&per_page=50")
    mine = [i for i in issues if i.get("title") == TITLE]
    if problems:
        body = "\n".join(f"- {p}" for p in problems) + "\n\n" + "\n".join(f"- {n}" for n in notes) + f"\n\n確認時刻: {now.strftime('%m/%d %H:%M')}\n\n@{REPO.split('/')[0]}"
        if mine:
            if problems != prev.get("problems"):   # 内容が変わったときだけ書き足す
                api(f"/issues/{mine[0]['number']}/comments", "POST", {"body": body})
        else:
            api("/issues", "POST", {"title": TITLE, "body": body})
    elif mine:
        api(f"/issues/{mine[0]['number']}/comments", "POST", {"body": f"直りました（{now.strftime('%m/%d %H:%M')}）\n\n" + "\n".join(f"- {n}" for n in notes)})
        api(f"/issues/{mine[0]['number']}", "PATCH", {"state": "closed"})


if __name__ == "__main__":
    main()
