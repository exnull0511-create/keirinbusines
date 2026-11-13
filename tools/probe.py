"""
サイト構造の調査用クローラー（GitHub Actions上で実行）。
各サイトのページを「URLの形」ごとに少数ずつ保存し、パーサー作成の材料にする。
"""
import json
import re
import sys
import time
from collections import defaultdict
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "samples")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
SLEEP = 1.5
PER_PATTERN = 2

SITES = {
    "kdreams": {
        "host": "keirin.kdreams.jp",
        "seeds": ["https://keirin.kdreams.jp/"],
        "max_pages": 70,
    },
    "oddspark": {
        "host": "www.oddspark.com",
        "seeds": [
            "https://www.oddspark.com/keirin/",
            "https://www.oddspark.com/keirin/yosou/toride/2025/0602.html",
            "https://www.oddspark.com/keirin/auth-yosou/toride/2026/0627.html",
            "https://www.oddspark.com/keirin/blog/graderace/cat316/index_5.html",
        ],
        "max_pages": 45,
        "path_prefix": "/keirin",
    },
}


def shape(url: str) -> str:
    """URLを形に変換（数字→#）して、同じ種類のページを重複取得しない。"""
    p = urlparse(url)
    q = re.sub(r"=[^&]*", "=", p.query)
    return re.sub(r"\d+", "#", p.path) + ("?" + q if q else "")


def extra_variants(url: str):
    """Kドリームスのレース詳細は、クエリで結果・オッズ画面に切り替わる。"""
    m = re.search(r"/racedetail/(\d+)/?$", urlparse(url).path)
    if m:
        base = url.split("?")[0].rstrip("/") + "/"
        return [base + "?pageType=result", base + "?pageType=odds&kakeshikiType=3rentan",
                base + "odds/3rentan/"]
    return []


def crawl(name, cfg, session):
    d = OUT / name
    d.mkdir(parents=True, exist_ok=True)
    queue = list(cfg["seeds"])
    seen, per_shape, index = set(), defaultdict(int), []
    while queue and len(index) < cfg["max_pages"]:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        sh = shape(url)
        if per_shape[sh] >= PER_PATTERN and url not in cfg["seeds"]:
            continue
        try:
            r = session.get(url, timeout=20)
        except Exception as e:  # noqa: BLE001
            index.append({"url": url, "error": str(e)})
            continue
        time.sleep(SLEEP)
        per_shape[sh] += 1
        r.encoding = r.apparent_encoding if r.encoding in (None, "ISO-8859-1") else r.encoding
        fn = f"{len(index):03d}.html"
        (d / fn).write_text(r.text, encoding="utf-8")
        index.append({"url": url, "status": r.status_code, "file": fn, "shape": sh, "bytes": len(r.text)})
        if r.status_code != 200 or "html" not in r.headers.get("content-type", ""):
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        new = []
        for a in soup.find_all("a", href=True):
            u = urljoin(url, a["href"]).split("#")[0]
            pu = urlparse(u)
            if pu.netloc != cfg["host"]:
                continue
            if cfg.get("path_prefix") and not pu.path.startswith(cfg["path_prefix"]):
                continue
            if u not in seen and per_shape[shape(u)] < PER_PATTERN:
                new.append(u)
        for u in list(new):
            new.extend(extra_variants(u))
        # 未知の形のページを優先
        new.sort(key=lambda u: per_shape[shape(u)])
        queue = new + queue if len(index) < 5 else queue + new
    (d / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    print(name, "pages:", len(index))


def main():
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "ja,en;q=0.8"})
    for name, cfg in SITES.items():
        crawl(name, cfg, s)
    for host in ("https://keirin.kdreams.jp/robots.txt", "https://www.oddspark.com/robots.txt"):
        try:
            (OUT / (urlparse(host).netloc + "_robots.txt")).write_text(s.get(host, timeout=20).text)
        except Exception as e:  # noqa: BLE001
            print("robots error", host, e)


if __name__ == "__main__":
    main()
