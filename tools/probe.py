"""
サイト構造の調査（2回目・対象を絞った版）。GitHub Actions上で実行。
"""
import json
import re
import sys
import time
from pathlib import Path

import requests

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "samples")
OUT.mkdir(parents=True, exist_ok=True)
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
K = "https://keirin.kdreams.jp"
s = requests.Session()
s.headers.update({"User-Agent": UA, "Accept-Language": "ja,en;q=0.8"})
index = []


def get(url, tag):
    try:
        r = s.get(url, timeout=25)
        r.encoding = r.apparent_encoding if r.encoding in (None, "ISO-8859-1") else r.encoding
        fn = f"{len(index):03d}_{tag}.html"
        (OUT / fn).write_text(r.text, encoding="utf-8")
        index.append({"tag": tag, "url": url, "status": r.status_code, "file": fn, "bytes": len(r.text)})
        time.sleep(1.5)
        return r.text if r.status_code == 200 else ""
    except Exception as e:  # noqa: BLE001
        index.append({"tag": tag, "url": url, "error": str(e)})
        return ""


# 1) 過去スケジュールがどこまで遡れるか
for ym in ["2026/10", "2025/06", "2024/06", "2023/06", "2021/06"]:
    html = get(f"{K}/schedule/search/{ym}/", "schedule_" + ym.replace("/", ""))
    if ym == "2024/06":
        sched_2024 = html

# 2) 今日・昨日のレース詳細（出走表／結果／オッズ）
top = get(f"{K}/raceresult/", "raceresult_list")
ids = re.findall(r"/([a-z]+)/racedetail/(\d{16})/", top)
if ids:
    venue, rid = ids[0]
    base = f"{K}/{venue}/racedetail/{rid}/"
    get(base, "racedetail")
    get(base + "?pageType=result", "racedetail_result")
    get(base + "?pageType=odds&kakeshikiType=3rentan", "racedetail_odds3t")
    get(base + "?pageType=odds&kakeshikiType=2shatan", "racedetail_odds2t")
    get(base + "?pageType=yoso", "racedetail_yoso")
    get(base + "?pageType=comment", "racedetail_comment")
    get(base + "?pageType=movie", "racedetail_movie")
    kaisai = rid[:14]
    get(f"{K}/{venue}/raceresult/{kaisai}/", "venue_raceresult")
    get(f"{K}/{venue}/racecard/{kaisai}/", "venue_racecard")

# 3) 記事・番組ページ
sched_now = get(f"{K}/schedule/", "schedule_now")
for pat, tag in [(r'href="([^"]*/kaisai/race-news/[^"]+)"', "race_news"),
                 (r'href="([^"]*/kaisai/race-program/[^"]+)"', "race_program")]:
    for u in list(dict.fromkeys(re.findall(pat, sched_now)))[:2]:
        get(u if u.startswith("http") else K + u, tag)

# 4) 過去（2024年6月）のレースが見られるか
for u in list(dict.fromkeys(re.findall(r'href="([^"]+)"', sched_2024 or "")))[:400]:
    if re.search(r"/(racecard|raceresult|race-program|race-news|racedetail)/", u):
        get(u if u.startswith("http") else K + u, "past_2024_link")
        break
m = re.search(r"/(\d{2})(2024\d{4})", sched_2024 or "")
if m:
    pass
# 2024年6月のレース詳細IDを推定して直接アクセス（例: 立川 28）
get(f"{K}/tachikawa/racedetail/2820240601010001/?pageType=result", "past_guess_result")

# 5) 他サイトの接続確認
for u, tag in [("https://www.winticket.jp/keirin", "winticket"),
               ("https://keirin.jp/pc/top", "keirinjp"),
               ("https://keirin.netkeiba.com/", "netkeirin"),
               ("https://www.chariloto.com/keirin", "chariloto"),
               ("https://www.oddspark.com/keirin/", "oddspark")]:
    get(u, "ext_" + tag)

(OUT / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(index, ensure_ascii=False, indent=1))
