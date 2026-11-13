"""
Kドリームス レース詳細ページの取得と解析。

1ページ（/{場}/racedetail/{16桁ID}/）に以下がすべて含まれる:
  出走表・予想印・並び予想・選手コメント・短評/前走の走り評・結果(決まり手/S/B/勝敗因)
  ・払戻金・確定オッズ（全賭式）・発売票数・戦評「戦い終わって」
ID: 場コード(2) + 開催初日(8) + 日目(2) + "00" + レース番号(2)
"""
from __future__ import annotations

import re
import time
from itertools import permutations
import unicodedata
from datetime import date, datetime, timedelta

import requests
from bs4 import BeautifulSoup

BASE = "https://keirin.kdreams.jp"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"


def norm(s: str | None) -> str:
    if s is None:
        return ""
    s = unicodedata.normalize("NFKC", s)
    return re.sub(r"\s+", " ", s).strip()


def tight(s: str | None) -> str:
    return norm(s).replace(" ", "")


def num(s: str | None):
    s = norm(s).replace(",", "")
    try:
        return float(s) if "." in s else int(s)
    except ValueError:
        return None


class Client:
    def __init__(self, sleep: float = 2.0, retries: int = 3):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, "Accept-Language": "ja,en;q=0.8"})
        self.sleep, self.retries = sleep, retries
        self.n = 0

    def get(self, url: str) -> str | None:
        for i in range(self.retries):
            try:
                r = self.s.get(url, timeout=30)
                self.n += 1
                time.sleep(self.sleep)
                if r.status_code == 404:
                    return None
                if r.status_code == 200:
                    r.encoding = "utf-8"
                    return r.text
            except requests.RequestException:
                pass
            time.sleep(self.sleep * (i + 2) * 3)
        return None


# ---------- スケジュール・開催 ----------

def meets_in_month(html: str) -> list[str]:
    """月別スケジュールから開催ID（場コード2桁+初日8桁）を抽出。"""
    return sorted(set(re.findall(r"race-news/(\d{10})/", html)))


def meet_info(html: str, meet_id: str) -> dict:
    """race-newsページから場のURL名と、各日目の開催IDを取得。"""
    slugs = re.findall(r"/([a-z]+)/(?:racecard|raceresult|racedetail)/" + meet_id, html)
    days = sorted(set(int(d) for d in re.findall(r"/(?:racecard|raceresult)/" + meet_id + r"(\d{2})00/", html)))
    return {"meet_id": meet_id, "slug": slugs[0] if slugs else None, "days": days}


def race_url(slug: str, meet_id: str, day: int, race_no: int) -> str:
    return f"{BASE}/{slug}/racedetail/{meet_id}{day:02d}00{race_no:02d}/"


# ---------- レース詳細の解析 ----------

def _rider_cell(text: str):
    # 例: "大川 剛 青 森/25/121"
    t = norm(text)
    m = re.match(r"(.+?)\s+([^\s/]+(?:\s[^\s/]+)?)/(\d+)/(\d+)", t)
    if not m:
        return {"name": t}
    return {"name": m.group(1).strip(), "pref": m.group(2).replace(" ", ""), "age": int(m.group(3)), "term": int(m.group(4))}


def _parse_title(soup) -> dict:
    t = norm(soup.title.get_text() if soup.title else "")
    # 例: 函館競輪 レース詳細 | 万博協賛ミリオンナイトカップ 7R Ｓ級一予選 | 2024年06月06日【楽天Kドリームス】
    out = {"title_raw": t}
    m = re.match(r"(\S+?)競輪 レース詳細 \| (.*) (\d+)R (.*?) \| (\d{4})年(\d{2})月(\d{2})日", t)
    if m:
        out.update(venue=m.group(1), meet_name=m.group(2), race_no=int(m.group(3)), race_type=m.group(4),
                   date=f"{m.group(5)}-{m.group(6)}-{m.group(7)}")
    g = soup.select_one("[class*=icon_grade]")
    out["grade"] = norm(g.get_text()) if g else None
    for key, dt_text in [("start_time", "発走予定"), ("close_time", "投票締切")]:
        el = soup.find("dt", string=lambda x, k=dt_text: x and k in x)
        if el and el.find_next_sibling("dd"):
            out[key] = norm(el.find_next_sibling("dd").get_text())
    for key, label in [("advance_rule", "勝ち上がり条件"), ("tipster", "予想担当記者"), ("line_source", "並び提供")]:
        el = soup.find("dt", string=lambda x, k=label: x and k in x)
        if el and el.find_next_sibling("dd"):
            out[key] = norm(el.find_next_sibling("dd").get_text())
    return out


def _parse_entries(soup) -> list[dict]:
    tables = soup.find_all("table", class_="racecard_table")
    if not tables:
        return []
    entries = {}
    # 1つ目: 出走表（直近4ヶ月の成績）
    cols = ["score", "S", "B", "nige", "makuri", "sashi", "mark_cnt", "n1", "n2", "n3", "nout", "win_rate", "quinella_rate", "trio_rate"]
    for tr in tables[0].find_all("tr"):
        num_td = tr.find("td", class_="num")
        if not num_td:
            continue
        tds = tr.find_all("td")
        car = num(num_td.get_text())
        idx = tds.index(num_td)
        rider_td = tr.find("td", class_="rider")
        e = {"car": car, "bracket": num(tr.find("td", class_="bracket").get_text()) if tr.find("td", class_="bracket") else None,
             "mark": norm(tr.find("td", class_="tip").get_text()) if tr.find("td", class_="tip") else "",
             "kiai": norm(tr.find("td", class_="kiai").get_text()) if tr.find("td", class_="kiai") else "",
             "evaluation": num(tr.find("td", class_="evaluation").get_text()) if tr.find("td", class_="evaluation") else None}
        e.update(_rider_cell(rider_td.get_text(" ")) if rider_td else {})
        rest = tds[tds.index(rider_td) + 1:] if rider_td else tds[idx + 2:]
        if len(rest) >= 3:
            e["class"], e["style"], e["gear"] = norm(rest[0].get_text()), norm(rest[1].get_text()), num(rest[2].get_text())
            for k, td in zip(cols, rest[3:]):
                e[k] = num(td.get_text())
        entries[car] = e
    # 2つ目: 選手コメント＋短評（旧）／前走成績・走り評（新）
    if len(tables) > 1:
        for tr in tables[1].find_all("tr"):
            num_td, ctd = tr.find("td", class_="num"), tr.find("td", class_="comment")
            if not num_td or not ctd:
                continue
            e = entries.setdefault(num(num_td.get_text()), {"car": num(num_td.get_text())})
            e["comment"] = norm(ctd.get_text())
            after = [norm(td.get_text()) for td in ctd.find_next_siblings("td")]
            after = [re.sub(r"\s*詳細$", "", a) for a in after if a != "詳細"]
            if len(after) == 1:
                e["short_eval"] = after[0]
            elif len(after) >= 4:
                e["prev_day"], e["prev_race"], e["prev_rank"], e["prev_review"] = after[:4]
            elif after:
                e["comment_extra"] = after
    return [entries[k] for k in sorted(entries, key=lambda x: (x is None, x))]


def _parse_line(soup) -> dict:
    """並び予想。<div class="line_position"> 内の icon_p（space=ライン区切り）と予想戦法ラベル。"""
    lines, cur, tactics = [], [], {}
    box = soup.find("div", class_="line_position")
    if box:
        for sp in box.find_all("span", class_="icon_p"):
            if "space" in (sp.get("class") or []):
                if cur:
                    lines.append(cur)
                    cur = []
                continue
            car = tac = None
            for ch in sp.find_all("span"):
                m = re.match(r"^p(\d{3})$", " ".join(ch.get("class") or []))
                if not m:
                    continue
                if car is None and 0 < int(m.group(1)) < 10:
                    car = int(m.group(1))
                elif car is not None:
                    tac = norm(ch.get_text())
            if car:
                cur.append(car)
                tactics[car] = tac
        if cur:
            lines.append(cur)
    tip = soup.find("table", class_="tipster_table")
    last_bs = []
    if tip:
        for tr in tip.find_all("tr"):
            cells = [norm(c.get_text(" ")) for c in tr.find_all(["td", "th"])]
            if cells and "最終BS" in cells[0].replace(" ", ""):
                last_bs = cells[1:]
    return {"lines": lines, "tactics": {str(k): v for k, v in tactics.items()}, "last_bs": last_bs}


def _parse_result(soup) -> list[dict]:
    t = soup.find("table", class_="result_table")
    if not t:
        return []
    out = []
    for tr in t.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 8:
            continue
        v = [norm(td.get_text(" ")) for td in tds]
        out.append({"mark": v[0], "rank": v[1], "car": num(v[2]), "name": v[3], "margin": v[4],
                    "last_lap": num(v[5]), "kimarite": v[6], "sb": v[7], "reason": v[8] if len(v) > 8 else ""})
    return out


def _parse_refund(soup) -> list[dict]:
    t = soup.find("table", class_="refund_table")
    if not t:
        return []
    out = []
    for tr in t.find_all("tr"):
        label = ""
        for c in tr.find_all(["th", "td"]):
            txt = norm(c.get_text(" "))
            if "円" not in txt:
                label = (label + " " + txt).strip() if c.name == "td" else txt.replace(" ", "")
                if c.name == "th":
                    group = txt.replace(" ", "")
                continue
            for combo, yen, pop in re.findall(r"([\d=\-]+)\s*([\d,]+)円\s*\((\d+)\)", txt):
                kind = ("3連" if combo.count("=") + combo.count("-") == 2 else
                        "ワイド" if "ワイド" in label else ("枠" if "枠" in label else "車"))
                bet = kind + ("" if kind == "ワイド" else ("単" if "-" in combo else "複"))
                out.append({"bet": bet, "combo": combo, "payout": int(yen.replace(",", "")), "pop": int(pop)})
            label = ""
    # 2行目（単）は列見出しが無いので、1行目のグループ順に合わせて枠/車を補正
    firsts = [r for r in out if r["bet"].endswith("複") and r["bet"] != "3連複"]
    groups = [r["bet"][0] for r in firsts]
    singles = [r for r in out if r["bet"] in ("車単", "枠単")]
    for r, g in zip(singles, [g for g in groups]):
        r["bet"] = ("枠単" if g == "枠" else "車単")
    for r in out:
        r["bet"] = {"枠単": "2枠単", "枠複": "2枠複", "車単": "2車単", "車複": "2車複"}.get(r["bet"], r["bet"])
    return out


def _parse_odds(soup) -> dict:
    out = {}
    # 3連単: 1着ごとのマトリクス（行見出し=3着, 列見出し=2着）※払戻金との照合で確認済み
    box = soup.find(id="JS_ODDSCONTENTS_3rentan")
    if box:
        o = {}
        for t in box.select("div.odds_table_wrapper table.odds_table"):
            rows = t.find_all("tr")
            if not rows:
                continue
            first = num(rows[0].get_text(" ").split()[0]) if rows[0].get_text(strip=True) else None
            header = None
            for tr in rows[1:]:
                ths = tr.find_all("th")
                if header is None and ths and not tr.find("td", class_=lambda c: c not in (None,) and "rider" in c):
                    hs = [num(th.get_text()) for th in ths]
                    if sum(isinstance(h, int) for h in hs) >= 2:
                        header = [h for h in hs if isinstance(h, int)]
                        continue
                if header and ths and tr.find("td"):
                    third = num(ths[0].get_text())
                    vals = [td for td in tr.find_all("td")]
                    for second, td in zip(header, vals):
                        val = num(td.get_text())
                        if isinstance(val, (int, float)) and first and third and second not in (first, third):
                            o[f"{first}-{second}-{third}"] = float(val)
        if o:
            n = max(int(k.split("-")[0]) for k in o)
            n = max(n, max(int(x) for k in o for x in k.split("-")))
            # 容量節約: 車番の順列（1-2-3, 1-2-4, ...）順の配列で保存。欠場などは null
            out["3連単"] = {"n": n, "order": "permutations(1..n,3)",
                           "v": [o.get(f"{a}-{b}-{c}") for a, b, c in permutations(range(1, n + 1), 3)]}
    # その他の賭式: 人気順リスト（"5-2 9.7" / ワイドは "2=5 1.5 - 1.8"）
    names = {"2shatan": "2車単", "3renhuku": "3連複", "2shahuku": "2車複", "2wakutan": "2枠単",
             "2wakuhuku": "2枠複", "wide": "ワイド", "3rentan": "3連単_pop"}
    for key, name in names.items():
        box = soup.find(id=f"JS_ODDSCONTENTS_{key}")
        if not box:
            continue
        o = {}
        for td in box.select("div.oddspop_table_wrapper td"):
            m = re.match(r"([\d=\-]+)\s+([\d.]+)(?:\s*-\s*([\d.]+))?", norm(td.get_text(" ")))
            if m:
                o[m.group(1)] = [float(m.group(2)), float(m.group(3))] if m.group(3) else float(m.group(2))
        if o and not (name == "3連単_pop" and "3連単" in out):
            out[name] = o
    # 発売票数（賭式ごと）
    votes = {}
    for key, name in [("3rentan", "3連単")] + [(k, v) for k, v in names.items() if k != "3rentan"]:
        box = soup.find(id=f"JS_ODDSSTATUS_{key}")
        m = re.search(r"発売票数\s*([\d,]+)", norm(box.get_text(" "))) if box else None
        if m:
            votes[name] = int(m.group(1).replace(",", ""))
    out["votes"] = votes
    return out


def _parse_review(soup) -> dict:
    h = soup.find(lambda t: t.name in ("h3", "h4") and "戦い終わって" in t.get_text())
    if not h:
        return {}
    box = h.find_next_sibling("div") or h.parent
    paras = [norm(p.get_text(" ")) for p in box.find_all("p")] or [norm(box.get_text(" "))]
    paras = [p for p in paras if p]
    return {"summary": paras[0] if paras else "", "comments": paras[1:]}


def odds_check(odds: dict, refund: list[dict]):
    """3連単の確定オッズと払戻金が一致するか（True/False、照合不能はNone）。"""
    t = odds.get("3連単")
    pays = [p for p in refund if p["bet"] == "3連単"]
    if not t or not pays:
        return None
    d = dict(zip(permutations(range(1, t["n"] + 1), 3), t["v"]))
    v = d.get(tuple(int(x) for x in pays[0]["combo"].split("-")))
    return v is not None and abs(v - pays[0]["payout"] / 100) < 0.15


def parse_race(html: str, url: str) -> dict | None:
    soup = BeautifulSoup(html, "lxml")
    for t in soup(["script", "style", "noscript"]):
        t.decompose()
    meta = _parse_title(soup)
    if "race_no" not in meta:
        return None
    m = re.search(r"/([a-z]+)/racedetail/(\d{16})/", url)
    odds, refund = _parse_odds(soup), _parse_refund(soup)
    return {
        "race_id": m.group(2) if m else None, "slug": m.group(1) if m else None, "url": url,
        **meta,
        "entries": _parse_entries(soup),
        "line": _parse_line(soup),
        "result": _parse_result(soup),
        "refund": refund,
        "odds": odds,
        "odds_check": odds_check(odds, refund),
        "review": _parse_review(soup),
        "schema": 1,
        "fetched_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }


def month_range(d0: date, d1: date):
    y, m = d0.year, d0.month
    while (y, m) <= (d1.year, d1.month):
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def meet_dates(meet_id: str, days: list[int]) -> dict[int, date]:
    start = datetime.strptime(meet_id[2:], "%Y%m%d").date()
    return {d: start + timedelta(days=d - 1) for d in days}
