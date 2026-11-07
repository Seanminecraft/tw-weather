#!/usr/bin/env python3
"""Comiket / 東京ビッグサイト (江東区) data for 場次天氣儀表板.
Sources (documented, keyless):
  - JMA 防災情報XML PULL feeds  https://www.data.jma.go.jp/developer/xml/feed/{extra_l,regular_l}.xml
    warnings: 気象警報・注意報（Ｒ０６） VPWW55-61 (東京都 130000), item 江東区 1310800
    forecast: 府県天気予報（Ｒ１） VPFD51_130000 (東京地方 130010 / 東京 44132)
  - NOAA AviationWeather Data API METAR RJTT (substitute observation, NOT JMA AMeDAS)
Writes <out>/data/comiket.json. Stdlib only. Never fabricates: missing -> null + reason.
Usage: python jma_comiket.py --out site [--prev-dir prev] [--fixtures DIR] [--now ISO]
"""
import argparse, json, math, os, re, sys, urllib.request, datetime as dt
import xml.etree.ElementTree as ET

VERSION = "comiket-1.0"
FEED_EXTRA = "https://www.data.jma.go.jp/developer/xml/feed/extra_l.xml"
FEED_REGULAR = "https://www.data.jma.go.jp/developer/xml/feed/regular_l.xml"
METAR_URL = "https://aviationweather.gov/api/data/metar?ids=RJTT&format=json&hours=2"
PREF, CITY, CITY_NAME = "130000", "1310800", "江東区"
FC_AREA, FC_POINT = "130010", "44132"
VENUE = {"id": "comiket", "name": "東京國際展示場（東京ビッグサイト）", "lat": 35.6320533, "lon": 139.7978048,
         "coordSource": "OpenStreetMap relation 7743566 (centroid)", "pref": "東京都", "city": CITY_NAME, "cityCode": CITY}
# R06 files: dataTypeCode -> hazard group (zh-TW)
R06 = {"VPWW55": "大雨", "VPWW56": "土砂災害", "VPWW57": "高潮", "VPWW58": "暴風（雪）・強風・風雪",
       "VPWW59": "波浪", "VPWW60": "大雪", "VPWW61": "その他注意報（雷・濃霧・乾燥・低温等）"}
# Official code table: xml.kishou.go.jp jmaxml_20261104_code.xlsx 警報等情報要素コード管理表 (names verbatim; XML <Name> wins)
ZH = {"02": "暴風雪警報", "03": "大雨警報（警戒等級3）", "05": "暴風警報", "06": "大雪警報", "07": "波浪警報",
      "08": "高潮警報（警戒等級3）", "09": "土砂災害警報（警戒等級3）", "10": "大雨注意報（警戒等級2）", "12": "大雪注意報",
      "13": "風雪注意報", "14": "雷注意報", "15": "強風注意報", "16": "波浪注意報（大浪）", "17": "融雪注意報",
      "19": "高潮注意報（警戒等級2）", "20": "濃霧注意報", "21": "乾燥注意報", "22": "雪崩注意報", "23": "低溫注意報（日本基準，非臺灣冷傷害）",
      "24": "霜注意報", "25": "結冰注意報", "26": "積雪附著注意報", "29": "土砂災害注意報（警戒等級2）",
      "32": "暴風雪特別警報", "33": "大雨特別警報（警戒等級5）", "35": "暴風特別警報", "36": "大雪特別警報", "37": "波浪特別警報",
      "38": "高潮特別警報（警戒等級5）", "39": "土砂災害特別警報（警戒等級5）", "43": "大雨危險警報（警戒等級4）",
      "48": "高潮危險警報（警戒等級4）", "49": "土砂災害危險警報（警戒等級4）"}
def tier(code):
    c = int(code)
    if c >= 32 and c <= 39: return "special"      # 特別警報 / レベル５
    if c in (43, 48, 49): return "danger"         # 危険警報 / レベル４
    if c < 10: return "warning"                   # 警報
    return "advisory"                             # 注意報
TIER_LEVEL = {"advisory": "yellow", "warning": "orange", "danger": "red", "special": "purple"}
ACTIVE = ("発表", "継続")
TPE = dt.timezone(dt.timedelta(hours=8))

def L(e): return e.tag.split("}")[-1]
def tpe(s):
    if not s: return None
    d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    return d.astimezone(TPE).isoformat(timespec="minutes")

class Src:
    def __init__(s, fixtures=None): s.fx = fixtures
    def get(s, url):
        if s.fx:
            p = os.path.join(s.fx, url.rstrip("/").split("/")[-1].split("?")[0])
            alias = {"extra_l.xml": "xml_extra.xml", "regular_l.xml": "xml_regular.xml", "metar": "metar.json"}
            p2 = os.path.join(s.fx, alias.get(os.path.basename(p), os.path.basename(p)))
            with open(p2, "rb") as f: return f.read()
        req = urllib.request.Request(url, headers={"User-Agent": "tw-doujin-event-weather (github actions)"})
        with urllib.request.urlopen(req, timeout=25) as r: return r.read()

def feed_latest(raw, pattern):
    """newest entry link per dataTypeCode matching pattern, using <updated>."""
    ns = {"a": "http://www.w3.org/2005/Atom"}; best = {}
    for e in ET.fromstring(raw).findall("a:entry", ns):
        href = e.find("a:link", ns).get("href"); m = re.search(pattern, href)
        if not m: continue
        k = m.group(1)
        up = dt.datetime.fromisoformat(e.find("a:updated", ns).text.replace("Z", "+00:00"))
        stamp = re.search(r"/(\d{14})_", href)  # tie-break by issue serial in file name
        key = (up, stamp.group(1) if stamp else "")
        if k not in best or key > best[k][0]: best[k] = (key, href)
    return best

def parse_warning(raw):
    r = ET.fromstring(raw); rep = None; kinds = []; found = False; info = None
    for e in r.iter():
        if L(e) == "ReportDateTime" and rep is None: rep = e.text
        if L(e) == "InfoType" and info is None: info = e.text
    if info == "取消": return rep, None, []
    for w in r.iter():
        if L(w) != "Warning" or w.get("type") != "気象警報・注意報（市町村等）": continue
        for it in w:
            if L(it) != "Item": continue
            area = [c for c in it if L(c) == "Area"]
            if not area or [c.text for c in area[0] if L(c) == "Code"] != [CITY]: continue
            found = True
            for k in it:
                if L(k) != "Kind": continue
                g = {L(c): c.text for c in k if L(c) in ("Name", "Code", "Status")}
                kinds.append({"code": g.get("Code"), "name": g.get("Name"), "status": g.get("Status")})
    return rep, found, kinds

def warnings(src, prev, now=None):
    now_tpe = (now or dt.datetime.now(dt.timezone.utc)).astimezone(TPE).isoformat(timespec="minutes")
    out = {"area": {"code": CITY, "name": CITY_NAME, "pref": PREF}, "groups": {}, "active": []}
    try: best = feed_latest(src.get(FEED_EXTRA), r"_(VPWW5[5-9]|VPWW6[01])_" + PREF + r"\.xml$")
    except Exception as ex: best = None; out["error"] = "feed_unreachable: %s" % type(ex).__name__
    pg = (prev or {}).get("warnings", {}).get("groups", {})
    pgen = (prev or {}).get("generatedAt")
    def carry(t, why):
        p = pg.get(t)
        if p and p.get("state") in ("ok", "carried"):
            g = dict(p); g["state"] = "carried"; g["reason"] = why
            g["lastVerifiedAt"] = p.get("lastVerifiedAt") if p.get("state") == "carried" else pgen
            return g
        return None
    for t, label in R06.items():
        g = {"label": label, "state": "unknown", "reportTime": None, "sourceUrl": None}
        try:
            if best and t in best:
                url = best[t][1]; rep, found, kinds = parse_warning(src.get(url))
                if found is None:
                    g.update(state="cancelled_report", sourceUrl=url, reportTime=tpe(rep), reason="最新電文為取消報，狀態未知")
                else:
                    g.update(sourceUrl=url, reportTime=tpe(rep), kinds=kinds, lastVerifiedAt=now_tpe,
                             state="ok" if found else "area_not_listed")
                    if not found: g["reason"] = "最新電文未列出江東区，狀態未知（不等於無警報）"
            else:
                why = "feed 無法取得" if best is None else "此類別近期無電文（feed 期間內未發布）"
                g = carry(t, why + "；沿用上次確認內容，未重新驗證") or dict(g, reason=why + "，狀態未知")
        except Exception as ex:
            g = carry(t, "電文讀取失敗；沿用上次確認內容，未重新驗證") or dict(g, state="error", reason=type(ex).__name__)
        out["groups"][t] = g
        for k in g.get("kinds", []):
            if k.get("status") in ACTIVE and k.get("code"):
                ti = tier(k["code"])
                out["active"].append({"code": k["code"], "nameJa": k["name"], "nameZh": ZH.get(k["code"], k["name"]),
                    "status": k["status"], "tier": ti, "level": TIER_LEVEL[ti], "group": t,
                    "issuedAt": g["reportTime"], "sourceUrl": g["sourceUrl"],
                    "carried": g["state"] == "carried", "lastVerifiedAt": g.get("lastVerifiedAt")})
    out["complete"] = all(g["state"] == "ok" for g in out["groups"].values())
    out["explicitNone"] = out["complete"] and not out["active"]  # only case where 「無發布中警報・注意報」 may be shown
    return out

def forecast(src, now=None):
    best = feed_latest(src.get(FEED_REGULAR), r"_(VPFD51)_" + PREF + r"\.xml$")
    if "VPFD51" not in best: return {"state": "unavailable", "reason": "feed 中無府県天気予報"}
    url = best["VPFD51"][1]; r = ET.fromstring(src.get(url))
    res = {"state": "ok", "sourceUrl": url, "region": {"code": FC_AREA, "name": "東京地方"},
           "point": {"code": FC_POINT, "name": "東京（大手町）", "note": "氣溫為東京點預報，非場館點"},
           "periods": [], "pop6h": [], "minMax": [], "threeHourly": [],
           "unavailable": ["逐時預報", "濕度預報", "體感溫度", "風速（m/s）—僅有氣象廳風速階級"]}
    for e in r.iter():
        if L(e) == "ReportDateTime": res["issuedAt"] = tpe(e.text); break
    for ts in r.iter():
        if L(ts) != "TimeSeriesInfo": continue
        tdef = {}
        for t in ts.iter():
            if L(t) == "TimeDefine":
                d = {L(c): c.text for c in t}; tdef[t.get("timeId")] = d
        for it in ts:
            if L(it) != "Item": continue
            codes = [c.text for c in it.iter() if L(c) == "Code" and c.text in (FC_AREA, FC_POINT)]
            if not codes: continue
            vals = {}
            for c in it.iter():
                if c.get("refID") and c.get("type"): vals.setdefault(c.get("type"), {})[c.get("refID")] = c.text
            ids = sorted(tdef, key=int)
            row = lambda i: {"start": tpe(tdef[i].get("DateTime")), "label": tdef[i].get("Name")}
            if "天気" in vals and "天気予報用テロップ番号" in vals:
                res["periods"] = [dict(row(i), weather=vals["天気"].get(i), weatherCode=vals["天気予報用テロップ番号"].get(i)) for i in ids]
            elif "６時間降水確率" in vals:
                res["pop6h"] = [dict(row(i), label=None, popPct=int(vals["６時間降水確率"][i]) if vals["６時間降水確率"].get(i) else None) for i in ids]
            elif "朝の最低気温" in vals or "日中の最高気温" in vals:
                for i in ids:
                    for k in ("朝の最低気温", "日中の最高気温"):
                        if vals.get(k, {}).get(i) is not None: res["minMax"].append(dict(row(i), kind=k, tempC=int(vals[k][i])))
            elif "天気" in vals and "風向" in vals:
                for i in ids: res["threeHourly"].append(dict(row(i), weather=vals["天気"].get(i), windDir=vals["風向"].get(i),
                                    windSpeedLevel=vals.get("風速階級", {}).get(i)))
            elif "気温" in vals:
                tmap = {tpe(tdef[i]["DateTime"]): int(vals["気温"][i]) for i in ids if vals["気温"].get(i)}
                res["_temps"] = tmap
    tm = res.pop("_temps", {})
    if res.get("issuedAt") and now:
        age = (now - dt.datetime.fromisoformat(res["issuedAt"])).total_seconds() / 3600
        res["ageHours"] = round(age, 1)
        if age > 9: res["state"] = "stale"; res["reason"] = "預報發布已超過 9 小時（氣象廳每日 5/11/17 時更新），可能非最新"
    elif not res.get("issuedAt"): res["state"] = "error"; res["reason"] = "無發布時間"
    for r3 in res["threeHourly"]: r3["tempC_point"] = tm.get(r3["start"])
    return res

DIR16 = ["北", "北北東", "東北", "東北東", "東", "東南東", "東南", "南南東", "南", "南南西", "西南", "西南西", "西", "西北西", "西北", "北北西"]
def rh_magnus(t, td):
    es = lambda x: 6.112 * math.exp(17.62 * x / (243.12 + x)); return round(100 * es(td) / es(t))
def observation(src, now):
    raw = json.loads(src.get(METAR_URL) or b"[]")
    if not isinstance(raw, list): raw = []
    raw = [m for m in raw if isinstance(m, dict) and m.get("obsTime") and m.get("lat") is not None]
    raw = sorted(raw, key=lambda m: m.get("obsTime", 0), reverse=True)
    if not raw: return {"state": "unavailable", "reason": "METAR 無資料"}
    m = raw[0]; obs = dt.datetime.fromtimestamp(m["obsTime"], dt.timezone.utc)
    age = (now - obs).total_seconds() / 60
    t, td, wd, ws = m.get("temp"), m.get("dewp"), m.get("wdir"), m.get("wspd")
    o = {"state": "ok" if age <= 90 else "stale", "ageMinutes": round(age),
         "substitute": True, "provider": "NOAA AviationWeather Data API", "sourceUrl": METAR_URL,
         "station": {"icao": "RJTT", "name": "羽田機場", "lat": m.get("lat"), "lon": m.get("lon"),
                     "distanceKm": round(gc(VENUE["lat"], VENUE["lon"], m["lat"], m["lon"]), 1)},
         "label": "替代觀測：羽田機場 METAR，非日本氣象廳 AMeDAS，非場館現場觀測",
         "observedAt": obs.astimezone(TPE).isoformat(timespec="minutes"), "raw": m.get("rawOb"),
         "tempC": t, "dewPointC": td, "rhPct": rh_magnus(t, td) if t is not None and td is not None else None,
         "rhMethod": "推算（Magnus，由氣溫/露點）", "windDirDeg": wd if isinstance(wd, (int, float)) else None,
         "windDirVariable": wd == "VRB",
         "windMs": round(ws * 0.514444, 1) if ws is not None else None,
         "gustMs": round(m["wgst"] * 0.514444, 1) if m.get("wgst") else None,
         "qnhHpa": m.get("altim"), "pressureNote": "海平面修正氣壓(QNH)，非測站氣壓",
         "precipMm": None, "precipNote": "METAR 不提供雨量（未提供，不是 0）",
         "feelsLikeC": None, "wxString": m.get("wxString")}
    if o["windDirDeg"] is not None: o["windDirZh"] = DIR16[int((o["windDirDeg"] % 360) / 22.5 + 0.5) % 16]
    return o
def gc(a, b, c, d):
    p = math.radians; return 6371 * 2 * math.asin(math.sqrt(math.sin(p(c - a) / 2) ** 2 + math.cos(p(a)) * math.cos(p(c)) * math.sin(p(d - b) / 2) ** 2))

LAYERS = {
  "osm": {"available": True, "url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png", "attribution": "© OpenStreetMap contributors"},
  "satellite": {"available": False, "reason": "尚未建置：可行來源為 NOAA Himawari-9 開放資料（AWS），需新增日本範圍裁切；不可沿用臺灣範圍"},
  "radar": {"available": False, "reason": "日本氣象廳雷達無公開文件化的免費自動取用方式（官方配信經氣象業務支援中心，付費）"},
  "qpf1h": {"available": False, "reason": "日本氣象廳降水短時間預報同上，無免費文件化來源"}}

def build(src, now, prev=None):
    out = {"schema": VERSION, "generatedAt": now.astimezone(TPE).isoformat(timespec="minutes"), "timezone": "Asia/Taipei (UTC+8)",
           "venue": VENUE, "layers": LAYERS,
           "event": {"name": "コミックマーケット109", "start": "2026-12-29", "end": "2026-12-31",
                     "source": "https://www.comiket.co.jp/", "note": "以主辦單位公告為準"},
           "disclaimer": "資料僅供參考，實際以日本氣象廳最新發布為準。",
           "attribution": ["出典：気象庁（防災情報XML）", "觀測替代：NOAA AviationWeather（RJTT METAR）", "© OpenStreetMap contributors"]}
    for k, fn in (("warnings", lambda: warnings(src, prev, now)), ("forecast", lambda: forecast(src, now)), ("observation", lambda: observation(src, now))):
        try: out[k] = fn()
        except Exception as ex: out[k] = (prev or {}).get(k) or {}; out[k] = dict(out[k], state="error", reason=type(ex).__name__)
    start = dt.datetime.fromisoformat(out["event"]["start"] + "T00:00:00+09:00")
    end = dt.datetime.fromisoformat(out["event"]["end"] + "T23:59:59+09:00")
    days = (start - now).total_seconds() / 86400
    out["event"]["daysUntilStart"] = round(days, 1)
    last = max([dt.datetime.fromisoformat(p["start"]) for p in out["forecast"].get("periods", []) if p.get("start")] or [now])
    if now > end: note = "場次已結束；以下為現況與近期預報"
    elif last + dt.timedelta(days=1) < start: note = "現況與近期預報；場次期間尚未進入可預報範圍"
    else: note = "預報期間已涵蓋部分場次日期"
    out["forecast"]["note"] = note
    return out

def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument("--out", required=True); ap.add_argument("--prev-dir")
    ap.add_argument("--fixtures"); ap.add_argument("--now"); a = ap.parse_args(argv)
    now = dt.datetime.fromisoformat(a.now) if a.now else dt.datetime.now(dt.timezone.utc)
    prev = None
    if a.prev_dir:
        try:
            with open(os.path.join(a.prev_dir, "data", "comiket.json"), encoding="utf-8") as f: prev = json.load(f)
        except Exception: prev = None
    d = build(Src(a.fixtures), now, prev)
    os.makedirs(os.path.join(a.out, "data"), exist_ok=True)
    dst = os.path.join(a.out, "data", "comiket.json")
    with open(dst + ".tmp", "w", encoding="utf-8") as f: json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(dst + ".tmp", dst)  # atomic: never publish a half-written file
    print("comiket.json: warnings active=%d complete=%s obs=%s fc=%s" % (len(d["warnings"].get("active", [])),
          d["warnings"].get("complete"), d["observation"].get("state"), d["forecast"].get("state")))
if __name__ == "__main__": main()
