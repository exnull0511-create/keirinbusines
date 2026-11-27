"""
レース映像（keirin.jp 公式ダイジェスト）を Gemini に読ませ、展開・駆け引き・選手ごとの内容評価を保存する。

  python -m collector.video run --data DIR [--days 30] [--limit 1000] [--workers 8] [--minutes 330]

  無料枠でゆっくり回すとき: --workers 1 --interval 90（1日の上限に達したらその日は終了し、翌日に続きから）
  ワークフロー video-slow が毎日 17:23 JST（無料枠の1日の回数がリセットされた後）にこの形で回す。

保存先: DIR/video/YYYY-MM-DD.jsonl.gz（1行=1レース）。済んだレースは飛ばすので、何度でも続きから回せる。
着順・S/B・決まり手は渡さない（盲検）。最終バック先頭・上位3着・S/B を確認問題として答えさせ、
実際の結果と照合して読み取りの信頼度に使う。
"""
from __future__ import annotations

import argparse
import base64
import concurrent.futures as cf
import gzip
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
GEMINI = "https://generativelanguage.googleapis.com/v1beta"
COLORS = "1白 2黒 3赤 4青 5黄 6緑 7橙 8桃 9紫"
PROMPT_VERSION = 3

TROUBLE = ["なし", "詰まり", "接触", "外に振られた", "前が離れた", "落車回避", "牽制で止まった"]
MOVE = ["早い", "適切", "遅い", "仕掛けず"]
ITYPE = ["切り替え", "捌き", "競り", "中割り", "内すくい", "外へ持ち出し", "ブロック", "追い上げ", "離れ", "カマシ", "捲り", "突っ張り", "その他"]


def nullable(t, enum=None):
    d = {"type": [t, "null"]}
    if enum:
        d["enum"] = enum + [None]
    return d


SCHEMA = {
    "type": "object",
    "properties": {
        "last_back_leader": nullable("integer"),
        "top3": {"type": "array", "items": {"type": "integer"}},
        "S": nullable("integer"),
        "B": nullable("integer"),
        "interactions": {"type": "array", "items": {"type": "object", "properties": {
            "time": {"type": "string"}, "type": {"type": "string", "enum": ITYPE},
            "actor": {"type": "integer"}, "target": nullable("integer"),
            "result": {"type": "string", "enum": ["成功", "失敗"]}, "detail": {"type": "string"}},
            "required": ["time", "type", "actor", "target", "result", "detail"]}},
        "riders": {"type": "array", "items": {"type": "object", "properties": {
            "car": {"type": "integer"},
            "waste": {"type": "integer", "minimum": 0, "maximum": 3},
            "trouble": {"type": "string", "enum": TROUBLE},
            "trouble_cost": {"type": "integer", "minimum": 0, "maximum": 3},
            "reserve": {"type": "integer", "minimum": 0, "maximum": 3},
            "move": {"type": "string", "enum": MOVE},
            "true_strength": {"type": "integer", "minimum": 1, "maximum": 5},
            "intent": nullable("string", ["先行", "カマシ", "捲り", "位置取り", "様子見"]),
            "executed": nullable("string", ["できた", "途中で崩れた", "できなかった"]),
            "reason": nullable("string", ["他ラインに先に動かれた", "突っ張られた", "脚不足", "位置が悪い", "その他"]),
            "job": nullable("string", ["しっかり援護", "最低限", "離れた", "自分から外した"]),
            "evidence": {"type": "string"}},
            "required": ["car", "waste", "trouble", "trouble_cost", "reserve", "move", "true_strength",
                         "intent", "executed", "reason", "job", "evidence"]}},
        "pace": {"type": "string", "enum": ["速い", "普通", "遅い"]},
        "confidence": {"type": "number"},
        "better_than_result": {"type": "array", "items": {"type": "integer"}},
        "worse_than_result": {"type": "array", "items": {"type": "integer"}},
    },
    "required": ["last_back_leader", "top3", "S", "B", "interactions", "riders", "pace", "confidence",
                 "better_than_result", "worse_than_result"],
}


def build_prompt(r: dict) -> str:
    E = sorted(r["entries"], key=lambda e: e["car"])
    ent = " / ".join(f"{e['car']}{e['name']}({e.get('style') or '-'})" for e in E)
    lines = r["line"]["lines"]
    heads = [l[0] for l in lines if len(l) >= 2]
    secs = [l[1] for l in lines if len(l) >= 2]
    return f"""あなたは競輪のレース映像を分析する専門家です。添付はダイジェスト映像（終盤約1周半〜ゴール）です。
ユニフォームの色：{COLORS}

【事前情報】
{r['venue']}{r['race_no']}R {r.get('race_type', '')}
出走：{ent}
並び（予想）：{'  '.join('-'.join(map(str, l)) for l in lines)}

【確認問題】映像で見えたままを答える
- last_back_leader：最終バックで先頭の車番
- top3：ゴール順の上位3車の車番
- S：正面スタンドで先頭を取った車番（映像に映らなければnull）／B：最終バックで先頭の車番

【駆け引き（最重要・見落とさないこと）】選手同士の攻防を、時系列ですべて interactions に列挙する
- 例：「7番が4番の後ろを捨てて1番ラインの番手へ切り替えを狙い、5番に捌かれて失敗」「5番が4番と6番の間を割って伸びた」
- 番手選手が位置を守った（捌いた・ブロックした）場合は、守った技術として true_strength に反映する
- 「展開の恩恵」と判断する前に、その選手が自分で位置を取った・守った・コースを割った動きがないか確認する

【各選手の評価】全車について riders に。evidence には映像の何秒あたりのどんな場面かを必ず書く
- waste：無駄に脚を使った量 0〜3（外を回された・踏み直し・競りなど）
- trouble：不利の種類 ／ trouble_cost：不利がなければ着順がいくつ上がったか 0〜3
- reserve：ゴール時の余力 0（出し切った）〜3（まだ伸びていた）
- move：仕掛けのタイミング
- true_strength：今日の脚力 1〜5（ゴール順ではなく、道中の脚の使い方・不利・駆け引きの技術を加味）
- 先頭選手（{','.join(map(str, heads)) or 'なし'}番）だけ intent / executed / reason を答え、それ以外は null
- 番手選手（{','.join(map(str, secs)) or 'なし'}番）だけ job を答え、それ以外は null

【レース全体】pace、confidence（読み取りの自信 0〜1）
最後に、次走で「着順以上に評価すべき選手」を better_than_result、「着順ほど評価できない選手」を worse_than_result に車番で。"""


class Gemini:
    def __init__(self, key: str, model: str, interval: float = 0.0):
        self.key, self.model = key, model
        self.lock = threading.Lock()
        self.pause_until = 0.0
        self.use_schema = True
        self.interval = interval  # 送信の最小間隔（秒）。無料枠で1分あたりの上限に当たらないように
        self.next_send = 0.0
        self.daily_out = False  # 1日の上限に達したら True（その日はもう送らない）

    def _wait_turn(self):
        with self.lock:
            t = max(self.next_send, self.pause_until, time.time())
            self.next_send = t + self.interval
        if t > time.time():
            time.sleep(t - time.time())

    def ask(self, video: bytes, prompt: str) -> tuple[dict | None, dict, str]:
        for attempt in range(8):
            if self.daily_out:
                return None, {}, "daily_quota"
            self._wait_turn()
            gen = {"temperature": 0.2, "mediaResolution": "MEDIA_RESOLUTION_HIGH", "responseMimeType": "application/json"}
            if self.use_schema:
                gen["responseJsonSchema"] = SCHEMA
            body = {"contents": [{"parts": [
                {"inline_data": {"mime_type": "video/mp4", "data": base64.b64encode(video).decode()}, "video_metadata": {"fps": 2}},
                {"text": prompt}]}], "generationConfig": gen}
            try:
                r = requests.post(f"{GEMINI}/models/{self.model}:generateContent?key={self.key}", json=body, timeout=900)
            except requests.RequestException as e:
                print("  gemini接続エラー", repr(e)[:150], flush=True)
                time.sleep(20)
                continue
            if r.status_code == 200:
                j = r.json()
                try:
                    text = "".join(p.get("text", "") for p in j["candidates"][0]["content"]["parts"])
                    return json.loads(text), j.get("usageMetadata", {}), ""
                except (KeyError, IndexError, ValueError):
                    return None, j.get("usageMetadata", {}), json.dumps(j, ensure_ascii=False)[:500]
            if r.status_code == 400 and self.use_schema and "schema" in r.text.lower():
                print("  回答形式の指定が使えないモデルなので、指定なしに切り替え", flush=True)
                self.use_schema = False
                continue
            if r.status_code == 429:
                # どの上限に当たったかを残す（1日の上限なら、その日は打ち切って翌日に続きから）
                quota = ",".join(sorted(set(re.findall(r'"quotaId":\s*"([^"]+)"', r.text)))) or r.text[:200].replace("\n", " ")
                if "PerDay" in quota:
                    print(f"  gemini 1日の上限に達したので今日は終了（{quota}）", flush=True)
                    self.daily_out = True
                    return None, {}, "daily_quota"
                print(f"  gemini 429（{quota}）", flush=True)
            if r.status_code in (429, 500, 503):
                back = min(60 * (attempt + 1), 300)
                with self.lock:  # 全ワーカーでまとめて待つ
                    self.pause_until = max(self.pause_until, time.time() + back)
                print(f"  gemini {r.status_code} {back}秒待ち", flush=True)
                continue
            return None, {}, f"{r.status_code} {r.text[:300]}"
        return None, {}, "retry exhausted"


def digest(sess: requests.Session, r: dict) -> str | None:
    page = sess.get(r["url"], timeout=30).text
    m = re.search(r"loadDigestVideoFromApi\(([^)]*)\)", page)
    if not m:
        return None
    a = [x.strip().strip("'\"") for x in m.group(1).split(",")]
    res = sess.post("https://keirin.kdreams.jp/api/json/kaisai/get_keirinjp_digest_url",
                    data={"kyogijoCd": a[0], "gradeCd": a[1], "kaisaiDate": a[2], "raceNo": a[3]},
                    headers={"X-Requested-With": "XMLHttpRequest"}, timeout=20)
    try:
        u = res.json().get("data")
    except ValueError:
        return None
    return u if isinstance(u, str) and u.startswith("http") else None


def fetch_video(sess: requests.Session, url: str) -> bytes | None:
    with tempfile.TemporaryDirectory() as d:
        raw, out = Path(d) / "raw.mp4", Path(d) / "out.mp4"
        with sess.get(url, stream=True, timeout=120) as res:
            if res.status_code != 200:
                return None
            with open(raw, "wb") as f:
                for c in res.iter_content(1 << 16):
                    f.write(c)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw), "-vf", "scale=-2:720", "-an",
                        "-c:v", "libx264", "-crf", "26", "-preset", "veryfast", str(out)], check=True)
        return out.read_bytes()


def truth(r: dict) -> dict:
    res = r["result"]
    order = [x["car"] for x in sorted([x for x in res if str(x["rank"]).isdigit()], key=lambda x: int(x["rank"]))]
    sb = {k: next((x["car"] for x in res if k in (x.get("sb") or "")), None) for k in ("S", "B")}
    return {"order": order, **sb}


def check(pred: dict | None, t: dict) -> dict:
    if not pred:
        return {}
    return {"top3": (pred.get("top3") or [])[:3] == t["order"][:3],
            "B": pred.get("B") == t["B"] or pred.get("last_back_leader") == t["B"],
            "S": pred.get("S") == t["S"] if pred.get("S") is not None else None}


def candidates(data: Path, days: int) -> list[dict]:
    since = (date.today() - timedelta(days=days)).isoformat()
    out = []
    for f in sorted((data / "races").glob("*/*.jsonl.gz"), reverse=True):
        if f.name[:10] < since:
            break
        for line in gzip.open(f, "rt", encoding="utf-8"):
            r = json.loads(line)
            if r.get("result") and len(r.get("entries", [])) >= 5 and "ガールズ" not in r.get("race_type", "") \
                    and r.get("line", {}).get("lines"):
                out.append(r)
    return out


def done_ids(data: Path) -> set[str]:
    s = set()
    for f in (data / "video").glob("*.jsonl.gz"):
        for line in gzip.open(f, "rt", encoding="utf-8"):
            j = json.loads(line)
            if j.get("result") is not None or j.get("skip"):
                s.add(j["race_id"])
    return s


def run(a):
    data = Path(a.data)
    (data / "video").mkdir(parents=True, exist_ok=True)
    gem = Gemini(os.environ["GEMINI_API_KEY"], a.model, a.interval)
    done = done_ids(data)
    todo = [r for r in candidates(data, a.days) if r["race_id"] not in done
            and int(r["race_id"]) % a.shards == a.shard][: a.limit]
    tag = f".s{a.shard}" if a.shards > 1 else ""
    print(f"[分担 {a.shard + 1}/{a.shards}] 対象 {len(todo)} レース（済 {len(done)}）model={a.model} workers={a.workers}", flush=True)
    t_end = time.time() + a.minutes * 60
    wlock = threading.Lock()
    stats = {"ok": 0, "ng": 0, "skip": 0, "tok": 0, "top3": 0, "B": 0}
    since_push = [0]

    def save(rec: dict, d: str):
        with wlock:
            with gzip.open(data / "video" / f"{d}{tag}.jsonl.gz", "at", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            since_push[0] += 1
            if a.push_every and since_push[0] >= a.push_every:
                subprocess.run(["bash", "tools/data_branch.sh", "push", "video"], check=False)
                since_push[0] = 0

    def work(r: dict):
        if time.time() > t_end or gem.daily_out:
            return
        sess = requests.Session()
        sess.headers.update({"User-Agent": UA, "Referer": "https://keirin.kdreams.jp/"})
        base = {"race_id": r["race_id"], "date": r["date"], "model": a.model, "prompt_version": PROMPT_VERSION,
                "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        try:
            url = digest(sess, r)
            vid = fetch_video(sess, url) if url else None
        except Exception as e:  # noqa: BLE001
            print("  映像取得エラー", r["race_id"], repr(e)[:120], flush=True)
            return
        if not vid:
            save({**base, "skip": "no_video", "video_url": url}, r["date"])
            stats["skip"] += 1
            return
        pred, usage, err = gem.ask(vid, build_prompt(r))
        if err == "daily_quota":  # 記録しない（翌日に続きから回す）
            return
        t = truth(r)
        c = check(pred, t)
        save({**base, "video_url": url, "result": pred, "usage": usage, "error": err or None, "truth": t, "check": c}, r["date"])
        stats["ok" if pred else "ng"] += 1
        stats["tok"] += usage.get("totalTokenCount", 0)
        stats["top3"] += bool(c.get("top3"))
        stats["B"] += bool(c.get("B"))
        n = stats["ok"]
        print(f"{r['date']} {r['venue']}{r['race_no']}R {'ok' if pred else 'NG ' + err[:80]} "
              f"上位3{'○' if c.get('top3') else '×'} B{'○' if c.get('B') else '×'} | 累計 {n}件 "
              f"上位3一致{stats['top3']}/{n} B一致{stats['B']}/{n} トークン{stats['tok']:,}", flush=True)

    with cf.ThreadPoolExecutor(a.workers) as ex:
        list(ex.map(work, todo))
    print("完了", stats, "（1日の上限で打ち切り）" if gem.daily_out else "", flush=True)


def main(argv=None):
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    q = sub.add_parser("run")
    q.add_argument("--data", required=True)
    q.add_argument("--days", type=int, default=30)
    q.add_argument("--limit", type=int, default=1000)
    q.add_argument("--workers", type=int, default=1)
    q.add_argument("--interval", type=float, default=0, help="Gemini への送信の最小間隔（秒）")
    q.add_argument("--minutes", type=float, default=330)
    q.add_argument("--push-every", type=int, default=20)
    q.add_argument("--shard", type=int, default=0)
    q.add_argument("--shards", type=int, default=1)
    q.add_argument("--model", default=os.environ.get("GEMINI_MODEL", "gemini-3.8-flash"))
    a = p.parse_args(argv)
    if a.cmd == "run":
        run(a)


if __name__ == "__main__":
    sys.exit(main())
