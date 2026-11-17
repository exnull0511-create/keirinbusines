import sys, re
from pathlib import Path
import requests
OUT = Path(sys.argv[1]); OUT.mkdir(parents=True, exist_ok=True)
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
s = requests.Session(); s.headers.update({"User-Agent": UA})
out = []
for u in ["https://keirin.kdreams.jp/schedule/search/2026/11/", "https://keirin.kdreams.jp/schedule/", "https://keirin.kdreams.jp/"]:
    h = s.get(u, timeout=30).text
    out.append(f"=== {u} len={len(h)}")
    for m in re.finditer(r"75202611|matsuyama|松山", h):
        out.append(h[max(0, m.start()-300): m.start()+200].replace("\n", " "))
        if len(out) > 40: break
(OUT / "log.txt").write_text("\n\n".join(out))
