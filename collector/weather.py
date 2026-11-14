"""
各競輪場の1時間ごとの風（向き・速さ・突風）、気温、降水量を Open-Meteo から取得。
Kドリームスの「風速」はレース時点の実測だが向きが無いので、こちらで補う。

  python -m collector.weather --data DIR [--start 2024-01-01]
保存先: DIR/weather/{場名}.csv.gz（time, wind_speed, wind_dir, gust, temp, precip）
"""
from __future__ import annotations

import argparse
import csv
import gzip
import io
import time
from datetime import date, timedelta
from pathlib import Path

import requests

# 競輪場のおおよその位置（気象グリッドは数km単位なので市街地レベルで十分）
VENUES = {
    "函館": (41.79, 140.75), "青森": (40.77, 140.73), "いわき平": (37.05, 140.89), "弥彦": (37.70, 138.83),
    "前橋": (36.40, 139.06), "取手": (35.91, 140.06), "宇都宮": (36.55, 139.88), "大宮": (35.92, 139.63),
    "西武園": (35.77, 139.45), "京王閣": (35.64, 139.56), "立川": (35.70, 139.42), "松戸": (35.79, 139.91),
    "千葉": (35.61, 140.12), "川崎": (35.53, 139.70), "平塚": (35.33, 139.35), "小田原": (35.26, 139.15),
    "伊東": (34.97, 139.10), "静岡": (34.98, 138.39), "名古屋": (35.18, 136.86), "岐阜": (35.42, 136.76),
    "大垣": (35.37, 136.62), "豊橋": (34.77, 137.38), "富山": (36.72, 137.17), "松阪": (34.57, 136.53),
    "四日市": (34.98, 136.62), "福井": (36.07, 136.23), "奈良": (34.67, 135.82), "向日町": (34.94, 135.70),
    "和歌山": (34.22, 135.18), "岸和田": (34.46, 135.37), "玉野": (34.49, 133.95), "広島": (34.36, 132.47),
    "防府": (34.05, 131.56), "高知": (33.57, 133.55), "松山": (33.85, 132.76), "小松島": (33.99, 134.58),
    "小倉": (33.87, 130.88), "久留米": (33.32, 130.52), "武雄": (33.19, 130.02), "佐世保": (33.16, 129.72),
    "別府": (33.30, 131.50), "熊本": (32.82, 130.71),
}
HOURLY = "wind_speed_10m,wind_direction_10m,wind_gusts_10m,temperature_2m,precipitation"


def fetch(lat, lon, start: date, end: date) -> list[list]:
    rows = []
    # 確定値（archive）は数日遅れ。直近は予報APIの過去分で補う
    for url, s, e in [("https://archive-api.open-meteo.com/v1/archive", start, end - timedelta(days=6)),
                      ("https://api.open-meteo.com/v1/forecast", end - timedelta(days=5), end)]:
        if s > e:
            continue
        params = dict(latitude=lat, longitude=lon, start_date=s.isoformat(), end_date=e.isoformat(),
                      hourly=HOURLY, timezone="Asia/Tokyo", wind_speed_unit="ms")
        for i in range(4):
            r = requests.get(url, params=params, timeout=60)
            if r.status_code == 200:
                break
            time.sleep(10 * (i + 1))
        r.raise_for_status()
        h = r.json()["hourly"]
        rows += list(zip(h["time"], h["wind_speed_10m"], h["wind_direction_10m"], h["wind_gusts_10m"],
                         h["temperature_2m"], h["precipitation"]))
        time.sleep(2)
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, type=Path)
    ap.add_argument("--start", default="2024-01-01")
    a = ap.parse_args(argv)
    out = a.data / "weather"
    out.mkdir(parents=True, exist_ok=True)
    end = date.today()
    for name, (lat, lon) in VENUES.items():
        rows = fetch(lat, lon, date.fromisoformat(a.start), end)
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["time", "wind_speed", "wind_dir", "gust", "temp", "precip"])
        seen = set()
        for row in rows:
            if row[0] not in seen:
                seen.add(row[0])
                w.writerow(row)
        with gzip.open(out / f"{name}.csv.gz", "wt", encoding="utf-8") as f:
            f.write(buf.getvalue())
        print(name, len(seen), "hours", flush=True)


if __name__ == "__main__":
    main()
