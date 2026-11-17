import sys, json, subprocess, base64, time, os, re
from pathlib import Path
import requests
OUT = Path(sys.argv[1]); OUT.mkdir(parents=True, exist_ok=True)
KEY = os.environ["GEMINI_API_KEY"]; G = "https://generativelanguage.googleapis.com/v1beta"
log = []
def L(*a):
    s = " ".join(map(str, a)); print(s, flush=True); log.append(s)
ms = requests.get(f"{G}/models?key={KEY}&pageSize=200", timeout=30).json().get("models", [])
names = [m["name"].split("/")[1] for m in ms if "generateContent" in m.get("supportedGenerationMethods", [])]
L("models:", names)
def pick(kind):
    c = [n for n in names if kind in n and "gemini" in n and not re.search(r"tts|image|live|embed|audio|lite|robotics|computer", n)]
    def ver(n):
        m = re.search(r"gemini-(\d+(?:\.\d+)?)", n); return (float(m.group(1)) if m else 0, "preview" not in n, "latest" in n)
    return sorted(c, key=ver)[-1] if c else None
models = [m for m in ["gemini-3.8-flash"] if m in names] or [pick("flash")]
L("use:", models)
s = requests.Session(); s.headers.update({"User-Agent": "Mozilla/5.0", "Referer": "https://keirin.kdreams.jp/"})
V = {v["race_id"]: v["video"] for v in json.load(open("tools/videos.json"))}
res = {}
for p in json.load(open("tools/gem_prompts.json")):
    rid = p["race_id"]; raw = OUT / "raw.mp4"; vid = OUT / f"{rid}.mp4"
    with s.get(V[rid], stream=True, timeout=120) as r:
        with open(raw, "wb") as f:
            for c in r.iter_content(1 << 16): f.write(c)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw), "-vf", "scale=-2:720", "-an", "-c:v", "libx264",
                    "-crf", "26", "-preset", "veryfast", str(vid)], check=True)
    raw.unlink()
    b64 = base64.b64encode(vid.read_bytes()).decode(); vid.unlink()
    for m in models:
        body = {"contents": [{"parts": [{"inline_data": {"mime_type": "video/mp4", "data": b64}}, {"text": p["prompt"]}]}],
                "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2}}
        for i in range(5):
            try:
                r = requests.post(f"{G}/models/{m}:generateContent?key={KEY}", json=body, timeout=1200)
            except Exception as e:
                L(rid, m, "exception", repr(e)[:200]); time.sleep(30); continue
            if r.status_code == 200:
                j = r.json()
                try: txt = "".join(x.get("text", "") for x in j["candidates"][0]["content"]["parts"])
                except Exception: txt = json.dumps(j)[:2000]
                res.setdefault(rid, {})[m] = {"text": txt, "usage": j.get("usageMetadata")}
                L(rid, m, "ok", j.get("usageMetadata", {}).get("totalTokenCount")); break
            L(rid, m, r.status_code, r.text[:300]); time.sleep(30 * (i + 1))
        (OUT / "gemini.json").write_text(json.dumps(res, ensure_ascii=False, indent=1))
        time.sleep(8)
(OUT / "log.txt").write_text("\n".join(log))
