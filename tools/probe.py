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


# 過去レース詳細に、コメント・戦評・確定オッズが残っているか
for code_date, tag in [("1120240606", "2024"), (None, "2021"), (None, "2023")]:
    if code_date is None:
        ym = {"2021": "2021/06", "2023": "2023/06"}[tag]
        sched = get(f"{K}/schedule/search/{ym}/", "sched" + tag)
        ids = re.findall(r"race-news/(\d{10})/", sched)
        if not ids:
            continue
        code_date = ids[len(ids) // 2]
    news = get(f"{K}/kaisai/race-news/{code_date}/{code_date}0100/", "news" + tag)
    m = re.search(r"/([a-z]+)/racedetail/(" + code_date + r"\d{4})01/", news) or re.search(r"/([a-z]+)/racecard/(" + code_date + r"\d{2})00/", news)
    if m:
        slug = m.group(1)
        get(f"{K}/{slug}/racedetail/{code_date}010007/", "past_detail_" + tag)
        get(f"{K}/{slug}/raceresult/{code_date}0100/", "past_venue_result_" + tag)

(OUT / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(index, ensure_ascii=False, indent=1))
