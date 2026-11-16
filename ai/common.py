"""AI共通: データ読み込み・クラス判定・自力ライン判定"""
import gzip
import json
from pathlib import Path


def cls(t: str) -> str:
    return next((k for k in ["ガールズ", "チャレンジ", "A級", "S級"] if k in (t or "")), "他")


def heads_of(r):
    E = {e["car"]: e for e in r["entries"]}
    out = []
    for l in r["line"]["lines"]:
        h = E.get(l[0], {})
        c = h.get("comment") or ""
        tac = r["line"]["tactics"].get(str(l[0]))
        if "自力" in c or tac in ("先行", "押え先"):
            out.append(l)
    return E, out


def load_races(data: Path, finished_only=True):
    rs = []
    for f in sorted((Path(data) / "races").glob("*/*.jsonl.gz")):
        for line in gzip.open(f, "rt", encoding="utf-8"):
            r = json.loads(line)
            if not r.get("odds", {}).get("3連単") or not r.get("entries"):
                continue
            if finished_only and not (r["result"] and any(p["bet"] == "3連単" for p in r["refund"])):
                continue
            rs.append(r)
    return rs
