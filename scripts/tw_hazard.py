#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
強風／豪大雨／淹水監測（tw-weather）→ site/data/hazard.json（schema "hazard-1.0"）
v1.0（2026-11-21）：Page.html v23.0 的「💨 強風」「🌧️ 豪大雨」子分頁與寒潮「場次低溫」圖卡共用

資料（全部免金鑰；只用 Python 標準函式庫）：
  氣象署 S3 公開鏡像：O-A0003-001（綜觀站，10 分鐘）、O-A0001-001（自動站，逐時）→ 風速、陣風、氣溫、今日最低
                      O-A0002-001（自動雨量站，10 分鐘）→ 10 分鐘／1／3／6／12／24 小時／2 日／3 日累積雨量
                      W-C0033-001（各縣市目前天氣特報：陸上強風、大雨、豪雨…）
  國家災害防救科技中心 NCDR 民生示警公開資料平台 CAP（AlertType=8：經濟部水利署 淹水警戒，鄉鎮＋雨量站）
    ※ NCDR 限制每 3 秒 1 次；本程式每次最多抓 1 份清單＋6 份 CAP，間隔 3.2 秒，CAP 結果存在上一版 hazard.json 重複使用
  經濟部水利署 水利資料開放平台：IoW 淹水深度感測器最新資料（依鄉鎮彙整，補充用）
  經濟部水利署 防災資訊服務網 NewstFloodWarm.kml（淹水警示 KML，只做交叉檢查）

規則：
  每個區塊各自 try；失敗時沿用上一版該區塊並標 state="cached"，沒有上一版就標 "error"。
  缺值一律 null（頁面顯示「—」），絕不補 0。站上的 -99／-999 等異常值視為缺值。
"""
import argparse, json, os, re, sys, time, urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

SCHEMA = "hazard-1.0"
VERSION = "1.0"
TPE = timezone(timedelta(hours=8))
UA = {"User-Agent": "Mozilla/5.0 (tw-weather hazard; GitHub Actions)"}
S3 = "https://cwaopendata.s3.ap-northeast-1.amazonaws.com/"
NCDR_FEED = "https://alerts.ncdr.nat.gov.tw/JSONAtomFeed.ashx?AlertType=8"
NCDR_GAP = 3.2
NCDR_MAX_CAP = 6
WRA_KML = "https://fhy.wra.gov.tw/pub_web_2011/kml/KmlShare/NewstFloodWarm.kml"
WRA_IOW = "https://opendata.wra.gov.tw/api/v2/1b991bbb-ad85-4e7a-b931-06ce8749d3ed?size=1000&page=%d"
DEFAULT_PAGES = "https://seanminecraft.github.io/tw-weather/"
FLOOD_DEFAULT_H = 3.0          # CAP 沒抓到時：水利署淹水警戒有效期間（實際 CAP 皆為發布後 3 小時）
FLOOD_RECENT_H = 24.0
SENSOR_MAX_AGE_H = 3.0         # 感測器超過 3 小時沒回報 → 不計
SENSOR_WET_CM = 10.0           # 本站統計「有積水」的門檻（公分）
SENSOR_BAD = 500.0             # ≥5 m 視為異常值（例如 999.9）

# 氣象署雨量分級（2020 起）：大雨 24h≥80 或 1h≥40；豪雨 24h≥200 或 3h≥100；大豪雨 24h≥350 或 3h≥200；超大豪雨 24h≥500
RAIN_LEVELS = [
    {"key": "extreme_torrential", "label": "超大豪雨", "r24": 500},
    {"key": "torrential", "label": "大豪雨", "r24": 350, "r3": 200},
    {"key": "extremely_heavy", "label": "豪雨", "r24": 200, "r3": 100},
    {"key": "heavy", "label": "大雨", "r24": 80, "r1": 40},
]
# 蒲福風級上限（m/s，氣象署）
BEAUFORT = [0.2, 1.5, 3.3, 5.4, 7.9, 10.7, 13.8, 17.1, 20.7, 24.4, 28.4, 32.6, 36.9, 41.4, 46.1, 50.9, 56.0]
HAZ_KEEP = ("強風", "大雨", "豪雨", "大豪雨", "超大豪雨", "颱風", "低溫", "濃霧")


def now_tpe():
    return datetime.now(TPE)


def iso(dt):
    return dt.astimezone(TPE).strftime("%Y-%m-%dT%H:%M+08:00") if dt else None


def parse_t(s):
    if not s or not isinstance(s, str) or s.startswith("-9"):
        return None
    try:
        d = datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=TPE)
    except Exception:
        return None


def log(*a):
    print("[hazard]", *a, flush=True)


def http_get(url, timeout=60, retries=2, headers=None):
    last = None
    for i in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=dict(UA, **(headers or {})))
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:
            last = e
            if i < retries:
                time.sleep(3 + 3 * i)
    raise last


def load_json(p):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def num(v, lo=-80.0, hi=200.0, nd=1):
    """數值；-99／-999／'X'／超出合理範圍 → None（不補 0）"""
    try:
        x = float(v)
    except Exception:
        return None
    if x != x or x <= -90 or x < lo or x > hi:
        return None
    return round(x, nd)


def norm_county(s):
    return (s or "").replace("台", "臺")


def stations_of(doc):
    def find(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k == "Station" and isinstance(v, list):
                    return v
                r = find(v)
                if r is not None:
                    return r
        return None
    return find(doc) or []


def wgs84(s):
    cs = ((s.get("GeoInfo") or {}).get("Coordinates")) or []
    pick = None
    for c in cs:
        if c.get("CoordinateName") == "WGS84":
            pick = c
            break
    pick = pick or (cs[0] if cs else None)
    if not pick:
        return None, None
    return num(pick.get("StationLatitude"), 10, 30, 4), num(pick.get("StationLongitude"), 110, 130, 4)


def beaufort(ms):
    if ms is None:
        return None
    for i, hi in enumerate(BEAUFORT):
        if ms <= hi:
            return i
    return 17


def hhmm(dt):
    return dt.astimezone(TPE).strftime("%Y-%m-%dT%H:%M") if dt else None


# ---------------------------------------------------------------- ① 測站（風、氣溫）
SRC_SHORT = {"O-A0003-001": "10m", "O-A0001-001": "1h"}
OBS_F = ["id", "name", "county", "town", "lat", "lon", "alt", "src", "t", "temp", "low", "lowAt", "ws", "wd", "bf", "gust", "gd", "gbf", "gustAt", "w10"]


def parse_obs(docs):
    """docs = [(來源代碼, json)]；先出現的來源優先（O-A0003 10 分鐘）"""
    out, seen, latest = [], set(), None
    for src, d in docs:
        for s in stations_of(d):
            sid = s.get("StationId")
            if not sid or sid in seen:
                continue
            seen.add(sid)
            g = s.get("GeoInfo") or {}
            w = s.get("WeatherElement") or {}
            lat, lon = wgs84(s)
            t = parse_t((s.get("ObsTime") or {}).get("DateTime"))
            if t and (latest is None or t > latest):
                latest = t
            ws = num(w.get("WindSpeed"), 0, 90)
            wd = num(w.get("WindDirection"), 0, 360)
            gi = w.get("GustInfo") or {}
            gust = num(gi.get("PeakGustSpeed"), 0, 120)
            go = gi.get("Occurred_at") or {}
            gd = num(go.get("WindDirection"), 0, 360)
            gat = parse_t(go.get("DateTime"))
            m10 = (w.get("Max10MinAverage") or {})
            w10 = num(m10.get("WindSpeed"), 0, 90)
            de = (w.get("DailyExtreme") or {}).get("DailyLow") or {}
            ti = de.get("TemperatureInfo") or {}
            low = num(ti.get("AirTemperature"), -30, 45)
            lat_ = parse_t((ti.get("Occurred_at") or {}).get("DateTime"))
            out.append([sid, s.get("StationName"), norm_county(g.get("CountyName")), g.get("TownName"), lat, lon,
                        num(g.get("StationAltitude"), -50, 4000), SRC_SHORT.get(src, src), hhmm(t), num(w.get("AirTemperature"), -30, 45),
                        low, hhmm(lat_), ws, wd, beaufort(ws), gust, gd, beaufort(gust), hhmm(gat), w10])
    return out, latest


# ---------------------------------------------------------------- ② 雨量站
RAIN_F = ["id", "name", "county", "town", "tc", "lat", "lon", "alt", "t", "r10", "r1", "r3", "r6", "r12", "r24", "r48"]
RAIN_KEYS = ["Past10Min", "Past1hr", "Past3hr", "Past6hr", "Past12hr", "Past24hr", "Past2days"]


def parse_rain(doc):
    out, latest = [], None
    for s in stations_of(doc):
        sid = s.get("StationId")
        if not sid:
            continue
        g = s.get("GeoInfo") or {}
        re_ = s.get("RainfallElement") or {}
        lat, lon = wgs84(s)
        t = parse_t((s.get("ObsTime") or {}).get("DateTime"))
        if t and (latest is None or t > latest):
            latest = t
        vals = [num((re_.get(k) or {}).get("Precipitation"), 0, 3000) for k in RAIN_KEYS]
        out.append([sid, s.get("StationName"), norm_county(g.get("CountyName")), g.get("TownName"), g.get("TownCode"), lat, lon,
                    num(g.get("StationAltitude"), -50, 4000), hhmm(t)] + vals)
    return out, latest


def rain_level(r1, r3, r24):
    for lv in RAIN_LEVELS:
        if (r24 is not None and r24 >= lv["r24"]) or (r3 is not None and lv.get("r3") and r3 >= lv["r3"]) or \
           (r1 is not None and lv.get("r1") and r1 >= lv["r1"]):
            return lv["key"]
    return None


# ---------------------------------------------------------------- ③ 縣市天氣特報
def parse_warn(doc):
    d = (doc or {}).get("cwaopendata") or {}
    locs = ((d.get("dataset") or {}).get("location")) or []
    counties = []
    for L in locs:
        hc = L.get("hazardConditions") or {}
        hz = hc.get("hazards") if isinstance(hc, dict) else None
        if not hz:
            continue
        hz = hz if isinstance(hz, list) else [hz]
        items = []
        for h in hz:
            info = h.get("info") or {}
            ph = info.get("phenomena") or ""
            vt = h.get("validTime") or {}
            items.append({"p": ph, "s": info.get("significance") or "", "start": iso(parse_t(vt.get("startTime"))),
                          "end": iso(parse_t(vt.get("endTime")))})
        if items:
            counties.append({"name": norm_county(L.get("locationName")), "items": items})
    return {"sent": iso(parse_t(d.get("sent"))), "counties": counties}


# ---------------------------------------------------------------- ④ 淹水警戒（NCDR CAP／水利署）
CN = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "十二": 12, "二十四": 24}
RX_ON = re.compile(r"水利署訊\s*(\S{3})(\S+?)淹水([一二])級警戒")
RX_RULE = re.compile(r"[（(]\s*(.+?)站\s*([一二三四五六七八九十]+|\d+)\s*小時雨量超過\s*(\d+(?:\.\d+)?)\s*mm\s*[)）]")
RX_VIL = re.compile(r"[（(]如[:：]\s*([^)）]+)[)）]")
RX_OFF = re.compile(r"水利署訊\s*(.+?)站已無警戒\s*[（(]\s*(\S{3})(\S+?)\s*[)）]")


def parse_flood_entry(e):
    """NCDR Atom JSON 的一筆 → dict；看不懂回 None"""
    txt = ((e.get("summary") or {}).get("#text") or "") if isinstance(e.get("summary"), dict) else str(e.get("summary") or "")
    txt = re.sub(r"\s+", " ", txt).strip()
    cid = e.get("id") or ""
    up = parse_t(e.get("updated"))
    link = e.get("link") or {}
    href = link.get("@href") if isinstance(link, dict) else None
    m = RX_ON.search(txt)
    if m:
        county, town, lv = norm_county(m.group(1)), m.group(2), CN.get(m.group(3))
        r = RX_RULE.search(txt)
        v = RX_VIL.search(txt)
        vil = None
        if v:
            vv = v.group(1)
            vil = vv.split("-", 1)[1] if "-" in vv else vv
            vil = [x.strip() for x in re.split(r"[,，、]", vil) if x.strip()]
        st = r.group(1).strip() if r else None
        hrs = None
        if r:
            g2 = r.group(2)
            hrs = int(g2) if g2.isdigit() else CN.get(g2)
        return {"id": cid, "kind": "on", "level": lv, "county": county, "town": town, "station": st, "hours": hrs,
                "mm": float(r.group(3)) if r else None, "villages": vil, "issued": up, "cap": href, "desc": txt[:400]}
    m = RX_OFF.search(txt)
    if m:
        return {"id": cid, "kind": "off", "station": m.group(1).strip(), "county": norm_county(m.group(2)), "town": m.group(3),
                "issued": up, "cap": href, "desc": txt[:300]}
    return None


def cap_expires(xml_bytes):
    try:
        root = ET.fromstring(xml_bytes)
    except Exception:
        return None, None
    ns = {"c": "urn:oasis:names:tc:emergency:cap:1.2"}
    ex = root.find(".//c:info/c:expires", ns)
    mt = root.find("c:msgType", ns)
    if ex is None:   # 舊版 1.1 命名空間或沒有命名空間
        for el in root.iter():
            if el.tag.endswith("expires"):
                ex = el
            if el.tag.endswith("msgType"):
                mt = el
    return parse_t(ex.text if ex is not None else None), (mt.text.strip() if mt is not None and mt.text else None)


def build_flood(feed, now, F, prev_flood, errors):
    entries = feed.get("entry") or []
    entries = entries if isinstance(entries, list) else [entries]
    cache = {}
    for x in ((prev_flood or {}).get("capCache") or {}).items():
        cache[x[0]] = x[1]
    parsed, unknown = [], 0
    for e in entries:
        p = parse_flood_entry(e)
        if p is None:
            unknown += 1
            continue
        if p["issued"] is None:
            continue
        parsed.append(p)
    parsed.sort(key=lambda p: p["issued"])
    # 每個雨量站（＋鄉鎮）取最後一則
    last = {}
    for p in parsed:
        key = (p.get("station") or "?") + "|" + p["county"] + p["town"]
        last[key] = p
    active_c = [p for p in last.values() if p["kind"] == "on" and now - p["issued"] < timedelta(hours=12)]
    # 抓 CAP 的到期時間（快取；每次最多 NCDR_MAX_CAP 份）
    fetched = 0
    for p in sorted(active_c, key=lambda p: p["issued"], reverse=True):
        if p["id"] in cache or not p.get("cap") or fetched >= NCDR_MAX_CAP:
            continue
        try:
            time.sleep(NCDR_GAP)
            ex, mt = cap_expires(F["ncdr_cap"](p["cap"]))
            cache[p["id"]] = {"expires": iso(ex), "msgType": mt}
            fetched += 1
        except Exception as e:
            errors.append("CAP %s：%s" % (p["id"], str(e)[:80]))
    active, recent = [], []
    for p in last.values():
        if p["kind"] != "on":
            continue
        c = cache.get(p["id"]) or {}
        ex = parse_t(c.get("expires"))
        assumed = ex is None
        if ex is None:
            ex = p["issued"] + timedelta(hours=FLOOD_DEFAULT_H)
        rec = {"id": p["id"], "level": p["level"], "county": p["county"], "town": p["town"], "station": p["station"],
               "hours": p["hours"], "mm": p["mm"], "villages": p["villages"], "issued": iso(p["issued"]), "expires": iso(ex),
               "expiresAssumed": assumed, "desc": p["desc"]}
        if c.get("msgType") == "Cancel":
            continue
        if ex > now:
            active.append(rec)
    for p in reversed(parsed):
        if now - p["issued"] > timedelta(hours=FLOOD_RECENT_H):
            continue
        recent.append({"id": p["id"], "kind": p["kind"], "level": p.get("level"), "county": p["county"], "town": p["town"],
                       "station": p.get("station"), "issued": iso(p["issued"])})
    towns = {}
    for a in active:
        k = a["county"] + a["town"]
        t = towns.setdefault(k, {"county": a["county"], "town": a["town"], "level": a["level"], "stations": [], "expires": a["expires"]})
        t["level"] = min(t["level"], a["level"])     # 一級（較嚴重）優先
        if a["station"] and a["station"] not in t["stations"]:
            t["stations"].append(a["station"])
        if a["expires"] > t["expires"]:
            t["expires"] = a["expires"]
    keep = {p["id"] for p in parsed if now - p["issued"] < timedelta(hours=FLOOD_RECENT_H)}
    return {"feedUpdated": iso(parse_t(feed.get("updated"))), "entries": len(entries), "unparsed": unknown,
            "active": sorted(active, key=lambda a: (a["level"], a["county"], a["town"])), "towns": list(towns.values()),
            "recent": recent[:60], "capCache": {k: v for k, v in cache.items() if k in keep}}


def parse_kml(b):
    """水利署淹水警示 KML：只數 Placemark（格式未公開，僅交叉檢查用）"""
    try:
        root = ET.fromstring(b)
    except Exception:
        return {"state": "error", "note": "KML 無法解析"}
    names = []
    for el in root.iter():
        if el.tag.split("}")[-1] == "Placemark":
            for c in el:
                if c.tag.split("}")[-1] == "name" and c.text:
                    names.append(c.text.strip())
    title = None
    for el in root.iter():
        if el.tag.split("}")[-1] == "name" and el.text:
            title = el.text.strip()
            break
    return {"state": "ok", "placemarks": len(names), "names": names[:40], "title": title,
            "none": bool(title and "無淹水警示" in title and not names)}


def build_sensors(pages, now):
    towns, n, used, stale, bad = {}, 0, 0, 0, 0
    for rows in pages:
        for r in rows or []:
            n += 1
            ac = str(r.get("areacode") or "")
            v = num(r.get("latestvalue"), 0, 100000)
            t = parse_t(r.get("timestamp"))
            if v is None or v >= SENSOR_BAD:
                bad += 1
                continue
            if t is None or now - t > timedelta(hours=SENSOR_MAX_AGE_H):
                stale += 1
                continue
            if len(ac) < 7:
                continue
            used += 1
            x = towns.setdefault(ac, [0, 0, None, None])
            x[0] += 1
            if v >= SENSOR_WET_CM:
                x[1] += 1
            if x[2] is None or v > x[2]:
                x[2] = v
            tt = hhmm(t)
            if x[3] is None or tt > x[3]:
                x[3] = tt
    return {"n": n, "used": used, "stale": stale, "bad": bad, "wetCm": SENSOR_WET_CM, "maxAgeH": SENSOR_MAX_AGE_H,
            "f": ["n", "wet", "maxCm", "t"], "towns": towns}


# ---------------------------------------------------------------- 組合
def default_fetchers():
    last_ncdr = [0.0]

    def s3(path):
        return json.loads(http_get(S3 + path, timeout=90).decode("utf-8"))

    def ncdr(url):
        gap = NCDR_GAP - (time.time() - last_ncdr[0])
        if gap > 0:
            time.sleep(gap)
        try:
            return http_get(url, timeout=40, retries=1)
        finally:
            last_ncdr[0] = time.time()

    def iow():
        pages = []
        for p in range(1, 9):
            rows = json.loads(http_get(WRA_IOW % p, timeout=40, retries=1).decode("utf-8"))
            pages.append(rows)
            if not isinstance(rows, list) or len(rows) < 1000:
                break
        return pages

    return {"s3": s3, "ncdr_feed": lambda: json.loads(ncdr(NCDR_FEED).decode("utf-8")), "ncdr_cap": ncdr,
            "kml": lambda: http_get(WRA_KML, timeout=30, retries=1), "iow": iow}


def block(name, prev, fn, now, errors):
    t0 = time.time()
    try:
        b = fn()
        b["state"] = b.get("state") or "ok"
        b["updatedAt"] = iso(now)
        b["sec"] = round(time.time() - t0, 1)
        return b
    except Exception as e:
        errors.append("%s：%s" % (name, str(e)[:160]))
        log("⚠️", name, e)
        p = (prev or {}).get(name)
        if p and p.get("state") in ("ok", "cached", "partial"):
            p = dict(p)
            p["state"] = "cached"
            p["error"] = str(e)[:120]
            return p
        return {"state": "error", "updatedAt": iso(now), "error": str(e)[:120]}


def build(now, F, prev):
    errors = []
    doc = {"schema": SCHEMA, "version": VERSION, "generatedAt": iso(now), "timezone": "Asia/Taipei (+08:00)"}

    def obs():
        docs = []
        miss = []
        for code in ("O-A0003-001", "O-A0001-001"):
            try:
                docs.append((code, F["s3"]("Observation/%s.json" % code)))
            except Exception as e:
                miss.append(code)
                errors.append("%s：%s" % (code, str(e)[:100]))
        if not docs:
            raise RuntimeError("兩個觀測檔都抓不到")
        rows, latest = parse_obs(docs)
        return {"state": "partial" if miss else "ok", "sources": [d[0] for d in docs], "missing": miss, "latest": iso(latest),
                "f": OBS_F, "s": rows}

    def rain():
        rows, latest = parse_rain(F["s3"]("Observation/O-A0002-001.json"))
        if not rows:
            raise RuntimeError("雨量站清單是空的")
        return {"source": "O-A0002-001", "latest": iso(latest), "f": RAIN_F, "s": rows}

    def warn():
        w = parse_warn(F["s3"]("Warning/W-C0033-001.json"))
        w["source"] = "W-C0033-001"
        return w

    def flood():
        sub = []
        f = build_flood(F["ncdr_feed"](), now, F, (prev or {}).get("flood"), sub)
        f["source"] = "NCDR 民生示警 CAP（經濟部水利署 淹水警戒）"
        if sub:
            f["notes"] = sub[:6]
        try:
            f["kml"] = parse_kml(F["kml"]())
        except Exception as e:
            f["kml"] = {"state": "error", "note": str(e)[:100]}
        return f

    def sensors():
        s = build_sensors(F["iow"](), now)
        s["source"] = "經濟部水利署 IoW 淹水深度感測器"
        return s

    doc["obs"] = block("obs", prev, obs, now, errors)
    doc["rain"] = block("rain", prev, rain, now, errors)
    doc["warn"] = block("warn", prev, warn, now, errors)
    doc["flood"] = block("flood", prev, flood, now, errors)
    doc["sensors"] = block("sensors", prev, sensors, now, errors)
    doc["thresholds"] = {"rain": RAIN_LEVELS, "beaufort": BEAUFORT,
                         "wind": {"avgBf": 6, "gustBf": 8, "note": "氣象署陸上強風特報標準：平均風 6 級以上或陣風 8 級以上"},
                         "sensorWetCm": SENSOR_WET_CM}
    doc["errors"] = errors or None
    doc["footer"] = ["資料來源：中央氣象署開放資料；經濟部水利署（NCDR 民生示警 CAP、IoW 感測器）",
                     "本站監測僅供參考，不是官方特報；以中央氣象署、水利署最新發布為準。"]
    return doc


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="site")
    ap.add_argument("--prev-dir", default=None)
    a = ap.parse_args(argv)
    now = now_tpe()
    outp = os.path.join(a.out, "data", "hazard.json")
    prev = None
    for cand in ([os.path.join(a.prev_dir, "data", "hazard.json")] if a.prev_dir else []) + [outp]:
        prev = load_json(cand)
        if prev:
            break
    if not prev:
        try:
            prev = json.loads(http_get((os.environ.get("PAGES_BASE") or DEFAULT_PAGES).rstrip("/") + "/data/hazard.json", timeout=20, retries=0))
        except Exception:
            prev = None
    doc = build(now, default_fetchers(), prev)
    os.makedirs(os.path.dirname(outp), exist_ok=True)
    tmp = outp + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, outp)
    fl = doc.get("flood") or {}
    log("✅ %s（%.1f KB）obs=%s rain=%s warn=%s flood=%s（警戒中 %s）sensors=%s errors=%s" % (
        outp, os.path.getsize(outp) / 1024, doc["obs"]["state"], doc["rain"]["state"], doc["warn"]["state"], fl.get("state"),
        len(fl.get("active") or []), doc["sensors"]["state"], len(doc["errors"] or [])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
