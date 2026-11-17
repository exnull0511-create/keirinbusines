import sys, re, json
from pathlib import Path
import requests
OUT = Path(sys.argv[1]); OUT.mkdir(parents=True, exist_ok=True)
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
s = requests.Session(); s.headers.update({"User-Agent": UA, "Referer": "https://keirin.kdreams.jp/", "X-Requested-With": "XMLHttpRequest"})
res = []
for p in json.load(open("tools/pick.json")):
    h = s.get(p["url"], timeout=30).text
    m = re.search(r"loadDigestVideoFromApi\(([^)]*)\)", h)
    args = m.group(1) if m else None
    url = None
    if m:
        a = [x.strip().strip("'\"") for x in args.split(",")]
        r = s.post("https://keirin.kdreams.jp/api/json/kaisai/get_keirinjp_digest_url",
                   data={"kyogijoCd": a[0], "gradeCd": a[1], "kaisaiDate": a[2], "raceNo": a[3]}, timeout=20)
        try: url = r.json().get("data")
        except Exception: url = r.text[:200]
    ok = None
    if isinstance(url, str) and url.startswith("http"):
        hd = s.head(url, timeout=20); ok = (hd.status_code, hd.headers.get("Content-Length"))
    res.append({**p, "args": args, "video": url, "head": ok})
(OUT / "videos.json").write_text(json.dumps(res, ensure_ascii=False, indent=1))
