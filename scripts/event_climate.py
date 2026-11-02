#!/usr/bin/env python3
"""場次氣候資料：讀場次表 → 抓氣象署測站逐時觀測（缺資料時用 ERA5 再分析）→ 輸出 JSON。
只用 Python 內建模組。用法: python3 scripts/event_climate.py --out event_climate [--prev URL]"""
import ssl, csv, io, json, math, os, re, sys, time, datetime as dt, urllib.request, urllib.parse, argparse
from concurrent.futures import ThreadPoolExecutor
SHEET = "https://docs.google.com/spreadsheets/d/1ByjfrRdO-tCgC-CXrlD6elnoE9p7QEfSR9-xIPkJh28/export?format=csv"
TZ = dt.timezone(dt.timedelta(hours=8))
STATIONS = [  # 署屬測站 id, 名稱, lat, lon
 ("466920","臺北",25.0377,121.5149),("466880","板橋",24.9976,121.4420),("466940","基隆",25.1333,121.7405),
 ("467050","新屋",25.0067,121.0475),("467571","新竹",24.8279,121.0142),("467490","臺中",24.1457,120.6841),
 ("467480","嘉義",23.4959,120.4329),("467410","臺南",22.9932,120.2047),("467441|467440","高雄",22.5658,120.3157),
 ("467080","宜蘭",24.7640,121.7565),("466990","花蓮",23.9751,121.6133),("467660","臺東",22.7522,121.1546)]
SERIES = ["FF台北","FF高雄","CWT台北","CWT台中","CWT高雄","台北國際動漫節","台北漫畫博覽會","台中國際動漫節","高雄國際動漫節","GJ","IF創集繪","PF"]

CTX = ssl.create_default_context()
CTX.verify_flags &= ~getattr(ssl, "VERIFY_X509_STRICT", 0)  # 氣象署憑證缺 SKI，放寬嚴格檢查（仍驗證憑證）

def get(url, data=None, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, data=data, headers={"User-Agent": "Mozilla/5.0 event-climate"})
            with urllib.request.urlopen(req, timeout=40, context=CTX) as r: return r.read()
        except Exception as e:
            if i == tries - 1: raise
            time.sleep(3 * (i + 1))

def pdate(s):
    s = s.strip().replace("-", "/"); y, m, d = map(int, s.split("/")[:3]); return dt.date(y, m, d)

def classify(r):
    n, city, org = r["活動名稱"], r["縣市"], r.get("主辦單位", "")
    K, C, T = "高雄" in city, "臺中" in city, city in ("臺北市", "新北市", "桃園市")
    if "駁二動漫祭" in n: return "FF高雄"
    if "開拓" in org and K and not re.search(r"Only", n, re.I): return "FF高雄"
    if re.search(r"Only|RF\d|Reality Fantasy|Party", n, re.I) and not re.search(r"CWT|Comic World", n): return None
    if re.search(r"開拓動漫祭|Fancy Frontier|\bFF\d", n): return "FF高雄" if K else ("FF台北" if T else None)
    if re.search(r"Petit Fancy|\bPF\d", n): return "PF"
    if re.search(r"Comic World|CWT", n): return "CWT高雄" if K else "CWT台中" if C else "CWT台北" if T else None
    if "漫畫博覽會" in n: return "台北漫畫博覽會"
    if re.search(r"[台臺]北國際動漫節|TiCA", n): return "台北國際動漫節"
    if re.search(r"[台臺]中國際動漫節", n): return "台中國際動漫節"
    if "高雄國際動漫節" in n: return "高雄國際動漫節"
    if re.search(r"^\W*GJ|GJ\d", n): return "GJ"
    if "創集繪" in n: return "IF創集繪"
    return None

def editions(today):
    rows = csv.DictReader(io.StringIO(get(SHEET).decode("utf-8-sig")))
    out = {}
    for r in rows:
        s = classify(r)
        if not s or not r.get("緯度"): continue
        try: sd = pdate(r["開始日期"]); ed = pdate(r["結束日期"] or r["開始日期"])
        except Exception: continue
        if ed < sd or (ed - sd).days > 15: ed = sd
        if (today - ed).days < 2: continue
        eid = f"{s}_{sd.isoformat()}"
        out[eid] = dict(id=eid, series=s, name=re.sub(r"[^\w\s\-()（）．.·:：&＆!！×☆★【】《》]", "", r["活動名稱"]).strip(),
                        start=sd.isoformat(), end=ed.isoformat(), venue=r["活動地點"].strip(), city=r["縣市"],
                        lat=float(r["緯度"]), lon=float(r["經度"]))
    return list(out.values())

def dist(a, b, c, d):
    R = 6371; p = math.radians
    x = math.sin(p(c - a) / 2) ** 2 + math.cos(p(a)) * math.cos(p(c)) * math.sin(p(d - b) / 2) ** 2
    return 2 * R * math.asin(math.sqrt(x))

def vap(T, RH): return RH / 100 * 6.105 * math.exp(17.27 * T / (237.7 + T))
def apparent(T, RH, V):  # 氣象署體感溫度
    return 1.04 * T + 0.2 * vap(T, RH) - 0.65 * V - 0.9
def heat_index(T, RH, V):  # 綜合熱指數（近似氣象署公式）
    return 1.07 * T + 0.2 * vap(T, RH) - 0.65 * V - 2.7

def codis(stn, day):
    d = day.isoformat()
    body = urllib.parse.urlencode(dict(date=d + "T00:00:00.000+08:00", type="report_date", stn_ID=stn, stn_type="cwb",
                                       start=d + "T00:00:00", end=d + "T23:59:59")).encode()
    j = json.loads(get("https://codis.cwa.gov.tw/api/station", body))
    if not j.get("data"): return None
    hrs = []
    def v(x, k="Instantaneous"):
        if not isinstance(x, dict): return None
        y = x.get(k)
        if y is None: y = next((x[q] for q in x if not q.endswith("f") and isinstance(x[q], (int, float))), None)
        return None if y is None or y < -90 else y
    for h in j["data"][0]["dts"]:
        hrs.append(dict(h=int(h["DataTime"][11:13]) % 24, T=v(h.get("AirTemperature")), RH=v(h.get("RelativeHumidity")),
                        V=v(h.get("WindSpeed"), "Mean"), D=v(h.get("WindDirection"), "Prevailing") or v(h.get("WindDirection"), "Mean"),
                        P=v(h.get("Precipitation"), "Accumulation"), S=v(h.get("SunshineDuration"), "Total"),
                        C=v(h.get("TotalCloudAmount"), "Instantaneous")))
    hrs = [x for x in hrs if x["T"] is not None]
    return hrs if len(hrs) >= 12 else None

def era5(lat, lon, d0, d1):
    q = urllib.parse.urlencode(dict(latitude=lat, longitude=lon, start_date=d0, end_date=d1, timezone="Asia/Taipei",
        hourly="temperature_2m,relative_humidity_2m,precipitation,wind_speed_10m,wind_direction_10m,cloud_cover", wind_speed_unit="ms"))
    j = json.loads(get("https://archive-api.open-meteo.com/v1/archive?" + q))["hourly"]
    days = {}
    for i, t in enumerate(j["time"]):
        if j["temperature_2m"][i] is None: continue
        days.setdefault(t[:10], []).append(dict(h=int(t[11:13]), T=j["temperature_2m"][i], RH=j["relative_humidity_2m"][i],
            V=j["wind_speed_10m"][i], D=j["wind_direction_10m"][i], P=j["precipitation"][i], S=None,
            C=None if j["cloud_cover"][i] is None else j["cloud_cover"][i] / 10))
    return days

def summarize(hrs, day, src):
    T = [x["T"] for x in hrs]; open_ = [x for x in hrs if 10 <= x["h"] <= 17] or hrs
    def avg(k, L=open_):
        a = [x[k] for x in L if x[k] is not None]; return round(sum(a) / len(a), 1) if a else None
    rain = round(sum(x["P"] or 0 for x in hrs), 1); rain_open = round(sum(x["P"] or 0 for x in open_), 1)
    at = [apparent(x["T"], x["RH"], x["V"] or 0) for x in open_ if x["RH"] is not None]
    hi = [heat_index(x["T"], x["RH"], x["V"] or 0) for x in open_ if x["RH"] is not None]
    # 盛行風向（向量平均）
    u = [-(x["V"] or 0) * math.sin(math.radians(x["D"])) for x in open_ if x["D"] is not None]
    w = [-(x["V"] or 0) * math.cos(math.radians(x["D"])) for x in open_ if x["D"] is not None]
    wd = round((math.degrees(math.atan2(-sum(u), -sum(w))) + 360) % 360) if u and (sum(u) or sum(w)) else None
    cl = avg("C"); sun = sum(x["S"] or 0 for x in open_) if any(x["S"] is not None for x in open_) else None
    if rain_open >= 10: wx = "rain_heavy"
    elif rain_open >= 0.5: wx = "rain"
    elif rain > 0.5: wx = "shower_offhours"
    elif cl is not None: wx = "sunny" if cl <= 3 else "cloudy" if cl <= 7 else "overcast"
    elif sun is not None: wx = "sunny" if sun >= 5 else "cloudy" if sun >= 2 else "overcast"
    else: wx = "dry"
    tmax, tmin = max(T), min(T)
    m = day.month
    return dict(date=day.isoformat(), src=src, tmax=round(tmax, 1), tmin=round(tmin, 1), tavg=avg("T"),
                at=round(sum(at) / len(at), 1) if at else None, at_max=round(max(at), 1) if at else None,
                rh=avg("RH"), ws=avg("V"), wd=wd, rain=rain, rain_open=rain_open, wx=wx,
                hi_max=round(max(hi), 1) if hi and 4 <= m <= 10 else None,
                dtr=round(tmax - tmin, 1),
                cold_tmin=round(tmin, 1) if m in (11, 12, 1, 2, 3) else None)

def fetch_edition(e):
    lat, lon = e["lat"], e["lon"]
    st = sorted(STATIONS, key=lambda s: dist(lat, lon, s[2], s[3]))[0]
    sd, ed = dt.date.fromisoformat(e["start"]), dt.date.fromisoformat(e["end"])
    days, need = [], []
    d = sd
    while d <= ed:
        h = None
        for sid in st[0].split("|"):  # 測站搬遷：新站號優先，舊站號備援
            try: h = codis(sid, d)
            except Exception as ex: print("codis fail", sid, d, ex, file=sys.stderr)
            if h: break
        if h: days.append(summarize(h, d, "cwa"))
        else: need.append(d)
        d += dt.timedelta(days=1); time.sleep(0.3)
    if need:
        try:
            ee = era5(lat, lon, need[0].isoformat(), need[-1].isoformat())
            for d in need:
                if d.isoformat() in ee: days.append(summarize(ee[d.isoformat()], d, "era5"))
        except Exception as ex: print("era5 fail", e["id"], ex, file=sys.stderr)
    if not days: return None
    days.sort(key=lambda x: x["date"])
    r = dict(e); r.update(station=dict(id=st[0].split("|")[0], name=st[1], lat=st[2], lon=st[3], km=round(dist(lat, lon, st[2], st[3]), 1)), days=days)
    return r

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--out", default="event_climate"); ap.add_argument("--prev", default="")
    ap.add_argument("--limit", type=int, default=0); a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True); path = os.path.join(a.out, "editions.json")
    old = {}
    src = a.prev.rstrip("/") + "/editions.json" if a.prev else ""
    try:
        if src: old = {x["id"]: x for x in json.loads(get(src))["editions"]}
    except Exception as ex: print("no previous data online:", ex)
    if not old and os.path.exists(path): old = {x["id"]: x for x in json.load(open(path, encoding="utf-8"))["editions"]}
    today = dt.datetime.now(TZ).date()
    eds = editions(today)
    todo = [e for e in eds if e["id"] not in old]
    if a.limit: todo = todo[:a.limit]
    print(f"editions {len(eds)}, stored {len(old)}, to fetch {len(todo)}")
    with ThreadPoolExecutor(3) as ex:
        for r in ex.map(fetch_edition, todo):
            if r: old[r["id"]] = r
    keep = {e["id"] for e in eds}
    res = sorted([x for k, x in old.items() if k in keep], key=lambda x: (x["series"], x["start"]))
    meta = dict(updated=dt.datetime.now(TZ).isoformat(timespec="minutes"), series=SERIES,
                stations=[dict(id=s[0].split("|")[0], name=s[1], lat=s[2], lon=s[3]) for s in STATIONS],
                attribution="中央氣象署測站觀測（CODiS）；部分早期資料為 ERA5 再分析（Copernicus/ECMWF，經 Open-Meteo）")
    json.dump(dict(meta=meta, editions=res), open(path, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    for s in SERIES:
        json.dump(dict(meta=meta, editions=[x for x in res if x["series"] == s]),
                  open(os.path.join(a.out, f"series_{SERIES.index(s):02d}.json"), "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print("written", len(res))

if __name__ == "__main__": main()
