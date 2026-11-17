import sys, json, subprocess
from pathlib import Path
import requests
OUT = Path(sys.argv[1]); OUT.mkdir(parents=True, exist_ok=True)
s = requests.Session(); s.headers.update({"User-Agent": "Mozilla/5.0", "Referer": "https://keirin.kdreams.jp/"})
for v in json.load(open("tools/videos.json")):
    raw = OUT / "raw.mp4"
    with s.get(v["video"], stream=True, timeout=120) as r:
        with open(raw, "wb") as f:
            for c in r.iter_content(1 << 16): f.write(c)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw), "-vf", "scale=-2:480,fps=15", "-an",
                    "-c:v", "libx264", "-crf", "28", "-preset", "veryfast", "-movflags", "+faststart", str(OUT / f"{v['race_id']}.mp4")], check=True)
    raw.unlink()
