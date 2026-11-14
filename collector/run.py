"""
競輪データ収集 CLI

  python -m collector.run collect  --data DIR [--date YYYY-MM-DD]
  python -m collector.run backfill --data DIR --start 2024-01-01 [--end YYYY-MM-DD] [--max-minutes 30]

保存先:
  DIR/races/YYYY/YYYY-MM-DD.jsonl.gz   1行=1レース（出走表・並び・コメント・結果・払戻・確定オッズ・戦評）
  DIR/state/done.txt                    取得が完了した日付
  DIR/state/meets/YYYYMM.json           月ごとの開催情報キャッシュ
"""
from __future__ import annotations

import argparse
import gzip
import json
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .kdreams import BASE, Client, meet_info, meets_in_month, parse_race, race_url

JST = timezone(timedelta(hours=9))


def today_jst() -> date:
    return datetime.now(JST).date()


SCHEMA = "2"


class Store:
    def __init__(self, root: Path):
        self.root = root
        (root / "state" / "meets").mkdir(parents=True, exist_ok=True)
        sp = root / "state" / "schema.txt"
        if not sp.exists() or sp.read_text().strip() != SCHEMA:
            # データ形式が変わったら取り直す（開催情報キャッシュは残す）
            import shutil
            shutil.rmtree(root / "races", ignore_errors=True)
            (root / "state" / "done.txt").unlink(missing_ok=True)
            sp.write_text(SCHEMA + "\n")
        self.done_path = root / "state" / "done.txt"
        self.done = set(self.done_path.read_text().split()) if self.done_path.exists() else set()

    def mark_done(self, d: date):
        self.done.add(d.isoformat())
        self.done_path.write_text("\n".join(sorted(self.done)) + "\n")

    def race_file(self, d: date) -> Path:
        p = self.root / "races" / f"{d.year}" / f"{d.isoformat()}.jsonl.gz"
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def write_day(self, d: date, races: list[dict]):
        with gzip.open(self.race_file(d), "wt", encoding="utf-8") as f:
            for r in races:
                f.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")

    def meets_cache(self, y: int, m: int) -> dict:
        p = self.root / "state" / "meets" / f"{y}{m:02d}.json"
        return json.loads(p.read_text()) if p.exists() else {}

    def save_meets(self, y: int, m: int, data: dict):
        p = self.root / "state" / "meets" / f"{y}{m:02d}.json"
        p.write_text(json.dumps(data, ensure_ascii=False, indent=0))


def meets_for_month(cli: Client, st: Store, y: int, m: int, refresh: bool) -> dict:
    cache = st.meets_cache(y, m)
    if cache and not refresh:
        return cache
    html = cli.get(f"{BASE}/schedule/search/{y}/{m:02d}/") or ""
    for mid in meets_in_month(html):
        if mid in cache and cache[mid].get("slug") and cache[mid].get("days"):
            continue
        news = cli.get(f"{BASE}/kaisai/race-news/{mid}/{mid}0100/") or ""
        cache[mid] = meet_info(news, mid)
    st.save_meets(y, m, cache)
    return cache


def races_on(cli: Client, st: Store, d: date) -> tuple[list[dict], bool]:
    """日付dの全レースを取得。戻り値: (レース一覧, 全レースに結果があるか)"""
    recent = (today_jst() - d).days <= 10
    meets = {}
    for back in (0, 8):
        x = d - timedelta(days=back)
        meets.update(meets_for_month(cli, st, x.year, x.month, refresh=recent))
    targets = []
    for mid, info in meets.items():
        start = datetime.strptime(mid[2:], "%Y%m%d").date()
        k = (d - start).days + 1
        if info.get("slug") and k in (info.get("days") or []):
            targets.append((mid, info["slug"], k))
    races, complete = [], True
    for mid, slug, k in sorted(targets):
        for rn in range(1, 13):
            url = race_url(slug, mid, k, rn)
            html = cli.get(url)
            r = parse_race(html, url) if html else None
            if not r:
                break
            if r.get("date") and r["date"] != d.isoformat():
                print(f"  ! 日付不一致 {url} -> {r['date']}（順延の可能性）", file=sys.stderr)
            if not r["result"]:
                complete = False
            races.append(r)
        mine = [r for r in races if r["race_id"].startswith(mid)]
        bad = [r["race_no"] for r in mine if r.get("odds_check") is False]
        print(f"  {d} {slug} {k}日目: {len(mine)}R" + (f"  ! オッズと払戻の不一致: {bad}R" if bad else ""))
    return races, complete and bool(targets)


def do_date(cli: Client, st: Store, d: date) -> int:
    races, complete = races_on(cli, st, d)
    if races:
        st.write_day(d, races)
    if complete or (races == [] and (today_jst() - d).days > 3):
        st.mark_done(d)
    return len(races)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["collect", "backfill", "remaining"])
    ap.add_argument("--data", required=True, type=Path)
    ap.add_argument("--date")
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end")
    ap.add_argument("--max-minutes", type=float, default=30)
    ap.add_argument("--sleep", type=float, default=2.0)
    a = ap.parse_args(argv)
    st, cli = Store(a.data), Client(sleep=a.sleep)

    if a.cmd == "collect":
        days = [date.fromisoformat(a.date)] if a.date else [today_jst() - timedelta(days=2), today_jst() - timedelta(days=1)]
        for d in days:
            if a.date or d.isoformat() not in st.done:
                print(d, "races:", do_date(cli, st, d))
        return

    if a.cmd == "remaining":
        end = date.fromisoformat(a.end) if a.end else today_jst() - timedelta(days=1)
        d, n = date.fromisoformat(a.start), 0
        while d <= end:
            n += d.isoformat() not in st.done
            d += timedelta(days=1)
        print(n)
        return

    t0 = time.time()
    end = date.fromisoformat(a.end) if a.end else today_jst() - timedelta(days=1)
    start = date.fromisoformat(a.start)
    d = end
    while d >= start:  # 新しい日付から順に遡る
        if d.isoformat() not in st.done:
            n = do_date(cli, st, d)
            print(d, "races:", n, f"requests={cli.n}", f"elapsed={(time.time()-t0)/60:.1f}m", flush=True)
            if (time.time() - t0) / 60 > a.max_minutes:
                break
        d -= timedelta(days=1)


if __name__ == "__main__":
    main()
