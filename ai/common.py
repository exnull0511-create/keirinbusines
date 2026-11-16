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


def fix_entries(r):
    """過去データの「名字1文字の選手」で、名前と府県の切れ目がずれているものを直す。"""
    from collector.kdreams import PREFS, split_name_pref
    for e in r.get("entries", []):
        if e.get("pref") and e["pref"] not in PREFS:
            e["name"], e["pref"] = split_name_pref(f"{e.get('name', '')} {e['pref']}")
    return r


def load_races(data: Path, finished_only=True):
    rs = []
    for f in sorted((Path(data) / "races").glob("*/*.jsonl.gz")):
        for line in gzip.open(f, "rt", encoding="utf-8"):
            r = json.loads(line)
            if not r.get("odds", {}).get("3連単") or not r.get("entries"):
                continue
            if finished_only and not (r["result"] and any(p["bet"] == "3連単" for p in r["refund"])):
                continue
            rs.append(fix_entries(r))
    return rs
