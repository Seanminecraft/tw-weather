#!/usr/bin/env python3
"""tw-weather 空氣品質（v22.2）：環境部即時 AQI → JSON＋測站圖＋鄉鎮平均面量圖（AQI 與各污染物）→ 同一個 GitHub Pages

資料（環境部開放資料平台 data.moenv.gov.tw，需要 secrets.MOENV_API_KEY；金鑰絕不印出、不寫進任何檔案）：
  aqx_p_432 空氣品質指標（AQI）：每小時 1 次（約 84 站）
  aqx_p_07  空氣品質監測站基本資料：每天 1 次（同一天沿用線上 data/air/sites.json 快取）
失敗不影響天氣圖：workflow 這一步 continue-on-error；程式本身出錯時，會把線上上一版的空品檔案原樣帶過來（標記 stale）。

用法：
  python scripts/tw_air.py --out site                       # 在 tw_weather.py 之後執行（同一個 site 資料夾）
  python scripts/tw_air.py --out run --pages-base none      # 本機測試（不讀線上上一版）
"""
import argparse
import concurrent.futures as cf
import gzip
import json
import os
import shutil
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tw_common import TPE, ROOT, HISTORY_HOURS, COUNTY_ORDER, norm_name, now_tpe, parse_t, iso, fkey
from tw_air_common import (AIR_VERSION, AIR_DISCLAIMER, AIR_SOURCE, AIR_SCALES, AIR_VARS, AIR_KEYS, AIR_RULE,
                           AIR_AGG_TEXT, aqi_level)

API = "https://data.moenv.gov.tw/api/v2/%s"
DS_AQI = "aqx_p_432"
DS_SITES = "aqx_p_07"
UA = {"User-Agent": "tw-weather-air/1.0 (+GitHub Actions)", "Accept-Encoding": "gzip"}
DEFAULT_PAGES = os.environ.get("PAGES_BASE", "")

# 會一起帶到下一版的空品檔案（失敗時原樣沿用）
CARRY_FILES = ["air.json", "data/air/latest.json", "data/air/towns.json", "data/air/counties.json", "data/air/sites.json",
               "data/air/history/store.json.gz", "img/history_aq/index.json"]


# ---------------------------------------------------------------- 共用
def mask(s):
    s = str(s)
    for name in ("MOENV_API_KEY", "CWA_API_KEY"):
        k = os.environ.get(name, "").strip()
        if k:
            s = s.replace(k, "***")
            s = s.replace(urllib.parse.quote(k), "***")
    return s


def log(*a):
    print(*[mask(x) for x in a], flush=True)


def _ssl_ctx():
    """仍然完整驗證憑證鏈與主機名稱；只關掉 Python 3.13 起預設的 X509 嚴格模式
    （環境部憑證缺 Subject Key Identifier，嚴格模式會誤判失敗）"""
    import ssl
    ctx = ssl.create_default_context()
    if hasattr(ssl, "VERIFY_X509_STRICT"):
        ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
    return ctx


SSL_CTX = _ssl_ctx()


def http_get(url, timeout=40):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as r:
        data = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            data = gzip.decompress(data)
        return data


def dump(path, obj, gz=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    s = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    if gz:
        with gzip.open(path, "wb", compresslevel=9) as fp:
            fp.write(s.encode("utf-8"))
    else:
        with open(path, "w", encoding="utf-8") as fp:
            fp.write(s)
    return len(s)


def read_prev(rel, pages_base, local_prev, binary=False):
    """上一版的檔案：本機（測試）→ 線上 Pages；拿不到回 None"""
    raw = None
    if local_prev and os.path.exists(os.path.join(local_prev, rel)):
        raw = open(os.path.join(local_prev, rel), "rb").read()
    elif pages_base:
        try:
            raw = http_get(pages_base + rel + "?ts=%d" % time.time(), timeout=40)
        except Exception:
            raw = None
    if raw is None or binary:
        return raw
    try:
        if rel.endswith(".gz"):
            try:
                raw = gzip.decompress(raw)
            except OSError:
                pass
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return None


def moenv(dataset, key, limit=1000):
    """環境部 API v2：回傳 list。網址含金鑰 → 任何錯誤訊息都先遮罩；最多試 3 次"""
    url = API % dataset + "?" + urllib.parse.urlencode({"api_key": key, "limit": limit, "format": "JSON"})
    errs = []
    for i in range(3):
        try:
            doc = json.loads(http_get(url, timeout=60).decode("utf-8"))
            recs = doc.get("records") if isinstance(doc, dict) else doc
            if isinstance(recs, list) and recs:
                return recs
            errs.append("格式不符或沒有資料")
        except Exception as e:
            errs.append(mask(e))
        if i < 2:
            time.sleep(3 + 4 * i)
    raise RuntimeError("%s 下載失敗：%s" % (dataset, "；".join(errs)))


def fnum(x):
    """環境部數值：空字串、-、ND、x、負值 → None"""
    if x is None:
        return None
    s = str(x).strip()
    if s in ("", "-", "--", "ND", "x", "NA", "N/A"):
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    return v if v >= 0 else None


# ---------------------------------------------------------------- 測站 → 鄉鎮
def town_geo():
    d = json.load(open(os.path.join(ROOT, "assets", "taiwan", "taiwan_towns.json"), encoding="utf-8"))
    return d


def town_index(geo):
    idx = {}
    for t in geo["towns"]:
        idx[(norm_name(t["c"]), norm_name(t["t"]))] = t
    return idx


def town_polys(geo):
    """座標落在哪個鄉鎮（站點清單沒有的新測站才用）"""
    try:
        from shapely.geometry import Polygon
        from shapely.ops import unary_union
        from shapely.validation import make_valid
    except Exception:
        return None
    sc = geo["scale"]
    arcs = [[(x * sc, y * sc) for x, y in a] for a in geo["arcs"]]

    def ring(ix):
        pts = []
        for i in ix:
            a = arcs[i] if i >= 0 else arcs[~i][::-1]
            pts.extend(a if not pts else a[1:])
        return pts
    out = []
    for t in geo["towns"]:
        try:
            polys = []
            for poly in t["p"]:
                rings = [r for r in (ring(x) for x in poly) if len(r) >= 4]
                if rings:
                    pg = Polygon(rings[0], rings[1:])
                    polys.append(pg if pg.is_valid else make_valid(pg))
            if polys:
                out.append((t, unary_union(polys)))
        except Exception:
            continue
    return out


def locate(lo, la, polys):
    if polys is None or lo is None:
        return None
    from shapely.geometry import Point
    p = Point(lo, la)
    best, bd = None, 9e9
    for t, g in polys:
        if g.contains(p):
            return t
        d = g.distance(p)
        if d < bd:
            best, bd = t, d
    return best if bd < 0.05 else None


def load_sites(key, pages_base, local_prev, today):
    """測站基本資料：同一天沿用上一版快取（不重抓）；不同天才呼叫 aqx_p_07；失敗就用舊快取"""
    prev = read_prev("data/air/sites.json", pages_base, local_prev)
    if prev and prev.get("date") == today and prev.get("sites"):
        return prev, "cache"
    try:
        recs = moenv(DS_SITES, key)
        sites = []
        for s in recs:
            sites.append({"id": str(s.get("siteid") or ""), "n": s.get("sitename"), "en": s.get("siteengname"),
                          "area": s.get("areaname"), "c": s.get("county"), "town": s.get("township"),
                          "addr": s.get("siteaddress"), "type": s.get("sitetype"),
                          "lo": fnum(s.get("twd97lon")), "la": fnum(s.get("twd97lat"))})
        return {"date": today, "updated": iso(now_tpe()), "source": DS_SITES, "sites": sites}, "api"
    except Exception as e:
        log("⚠️ 測站基本資料下載失敗：%s" % mask(e))
        if prev and prev.get("sites"):
            return prev, "cache(舊)"
        return {"date": None, "sites": []}, "none"


def county_area_map(sites):
    """縣市 → 空品區（多數決）"""
    cnt = {}
    for s in sites:
        if not s.get("c") or not s.get("area") or s["area"] == "其他":
            continue
        c = norm_name(s["c"])
        cnt.setdefault(c, {}).setdefault(s["area"], 0)
        cnt[c][s["area"]] += 1
    out = {c: max(d.items(), key=lambda x: x[1])[0] for c, d in cnt.items()}
    for c in ("金門縣", "連江縣", "澎湖縣"):
        out.setdefault(c, "其他")
    return out


def build_stations(recs, sites, geo):
    idx = town_index(geo)
    by_id = {s["id"]: s for s in sites}
    polys = None
    stations = {}
    pubs = []
    for r in recs:
        sid = str(r.get("siteid") or "").strip()
        if not sid:
            continue
        s = by_id.get(sid) or {}
        lo, la = fnum(r.get("longitude")) or s.get("lo"), fnum(r.get("latitude")) or s.get("la")
        t = None
        if s.get("c") and s.get("town"):
            t = idx.get((norm_name(s["c"]), norm_name(s["town"])))
        if t is None:
            if polys is None:
                polys = town_polys(geo) or []
            t = locate(lo, la, polys)
        pt = parse_t((r.get("publishtime") or "").replace("/", "-"))
        if pt:
            pubs.append(pt)
        o = {"id": sid, "n": r.get("sitename"), "en": s.get("en"), "c": (t or {}).get("c") or r.get("county"),
             "town": (t or {}).get("t"), "tc": (t or {}).get("code"), "area": s.get("area"), "type": s.get("type"),
             "la": la, "lo": lo, "status": r.get("status") or "", "pol": r.get("pollutant") or "",
             "pt": iso(pt) if pt else None, "ws": fnum(r.get("wind_speed")), "wd": fnum(r.get("wind_direc"))}
        for v in AIR_VARS:
            x = fnum(r.get(v["src"]))
            o[v["key"]] = None if x is None else (int(round(x)) if v["dec"] == 0 and v["key"] == "aq_aqi" else round(x, 3))
        if o["aq_aqi"] is not None:
            o["lv"] = aqi_level(o["aq_aqi"])[0]
        stations[sid] = o
    T = max(pubs) if pubs else None
    if T:
        T = T.replace(minute=0, second=0, microsecond=0)
    return stations, T


def aggregate_towns(stations, geo):
    """鄉鎮值＝鄉鎮內各空品測站的算術平均（AQI 取整數）；沒有測站的鄉鎮不列入（前端/圖都顯示灰色）"""
    by_town = {}
    for r in stations.values():
        if r.get("tc"):
            by_town.setdefault(r["tc"], []).append(r)
    tinfo = {t["code"]: t for t in geo["towns"]}
    out = {}
    for code, rs in by_town.items():
        t = tinfo.get(code) or {}
        a = {"code": code, "c": t.get("c"), "t": t.get("t"), "n": len(rs), "st": sorted(r["id"] for r in rs)}
        for v in AIR_VARS:
            k = v["key"]
            vals = [(r[k], r["n"]) for r in rs if r.get(k) is not None]
            if not vals:
                a[k] = None
                continue
            m = sum(x for x, _ in vals) / len(vals)
            a[k] = int(round(m)) if k == "aq_aqi" else round(m, max(1, v["dec"]))
            a[k + "_n"] = len(vals)
            a[k + "_names"] = [n for _, n in vals]
        if a.get("aq_aqi") is not None:
            a["lv"] = aqi_level(a["aq_aqi"])[0]
        out[code] = a
    return out


def county_summary(stations, area_map):
    out = {}
    for r in stations.values():
        c = norm_name(r.get("c") or "")
        if not c:
            continue
        d = out.setdefault(c, {"c": c, "n": 0, "area": area_map.get(c), "st": []})
        d["n"] += 1
        d["st"].append(r["id"])
    for c, d in out.items():
        rs = [stations[i] for i in d["st"]]
        for k in ("aq_aqi", "aq_pm25", "aq_pm25_avg", "aq_o3_8hr"):
            vals = [(r[k], r) for r in rs if r.get(k) is not None]
            if vals:
                mx = max(vals, key=lambda x: x[0])
                d[k + "_max"] = {"v": mx[0], "id": mx[1]["id"], "n": mx[1]["n"], "pol": mx[1].get("pol") if k == "aq_aqi" else None}
                m = sum(x for x, _ in vals) / len(vals)
                d[k + "_mean"] = int(round(m)) if k == "aq_aqi" else round(m, 1)
        if d.get("aq_aqi_max"):
            d["lv"] = aqi_level(d["aq_aqi_max"]["v"])[0]
    order = {c: i for i, c in enumerate(COUNTY_ORDER)}
    return [out[c] for c in sorted(out, key=lambda c: order.get(c, 99))]


# ---------------------------------------------------------------- 48h 歷史
def load_store(pages_base, local_prev):
    st = read_prev("data/air/history/store.json.gz", pages_base, local_prev)
    if not st or st.get("keys") != AIR_KEYS:
        return {"v": 1, "keys": AIR_KEYS, "frames": [], "meta": {}}
    return st


def update_store(store, stations, T):
    d = {}
    for sid, r in stations.items():
        row = [r.get(k) for k in AIR_KEYS]
        if any(x is not None for x in row):
            d[sid] = row
    fr = {"t": iso(T), "k": fkey(T), "d": d}
    frames = [f for f in store.get("frames", []) if f.get("k") != fr["k"]] + [fr]
    cutoff = T.timestamp() - HISTORY_HOURS * 3600
    frames = sorted([f for f in frames if (parse_t(f["t"]) or T).timestamp() > cutoff - 1], key=lambda f: f["k"])
    meta = store.get("meta") or {}
    for sid, r in stations.items():   # 與天氣 store 同格式：[名稱, 縣市, 鄉鎮, 鄉鎮代碼, 緯度, 經度, 高度, 來源]
        meta[sid] = [r.get("n"), r.get("c"), r.get("town"), r.get("tc"), r.get("la"), r.get("lo"), None, "moenv"]
    alive = set()
    for f in frames:
        alive.update(f["d"].keys())
    store.update({"v": 1, "keys": AIR_KEYS, "frames": frames, "meta": {k: v for k, v in meta.items() if k in alive},
                  "updated": iso(now_tpe())})
    return store


def copy_prev_frames(keep, out, pages_base, local_prev, names):
    idx = read_prev("img/history_aq/index.json", pages_base, local_prev) or {"frames": []}
    jobs = []
    for fr in idx.get("frames", []):
        if fr.get("k") not in keep:
            continue
        for nm in names:
            rel = "img/history_aq/%s/%s" % (fr["k"], nm)
            if not os.path.exists(os.path.join(out, rel)):
                jobs.append(rel)

    def get(rel):
        dst = os.path.join(out, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        try:
            if local_prev and os.path.exists(os.path.join(local_prev, rel)):
                shutil.copyfile(os.path.join(local_prev, rel), dst)
            else:
                open(dst, "wb").write(http_get(pages_base + rel, timeout=30))
            return True
        except Exception:
            return False
    ok = 0
    if jobs and (pages_base or local_prev):
        with cf.ThreadPoolExecutor(16) as ex:
            ok = sum(1 for r in ex.map(get, jobs) if r)
    return ok, len(jobs)


# ---------------------------------------------------------------- 失敗時：沿用上一版
def carry_prev(out, pages_base, local_prev, reason):
    man = read_prev("air.json", pages_base, local_prev)
    if not man:
        log("ℹ️ 線上也沒有上一版空品資料，這次網站不含空品（天氣不受影響）")
        return False
    n = 0
    rels = list(CARRY_FILES) + [v["img"][s].split("?")[0].replace(man.get("base") or "", "") for v in man.get("vars", [])
                                 for s in ("station", "town")]
    idx = read_prev("img/history_aq/index.json", pages_base, local_prev) or {"frames": []}
    names = ["%s_%s.webp" % (k, s) for k in AIR_KEYS for s in ("station", "town")]
    for fr in idx.get("frames", []):
        rels += ["img/history_aq/%s/%s" % (fr["k"], nm) for nm in names]

    def get(rel):
        dst = os.path.join(out, rel)
        if rel == "air.json" or os.path.exists(dst):
            return False
        raw = read_prev(rel, pages_base, local_prev, binary=True)
        if raw is None:
            return False
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        open(dst, "wb").write(raw)
        return True
    with cf.ThreadPoolExecutor(16) as ex:
        n = sum(1 for r in ex.map(get, rels) if r)
    man["stale"] = True
    man["staleReason"] = mask(reason)[:200]
    man["staleSince"] = man.get("staleSince") or iso(now_tpe())
    dump(os.path.join(out, "air.json"), man)
    log("♻️ 沿用上一版空品資料（%d 個檔案），監測時間 %s" % (n, man.get("obsTime")))
    return True


# ---------------------------------------------------------------- index.html 附加區塊
def append_index(out, man):
    p = os.path.join(out, "index.html")
    if not os.path.exists(p):
        return
    html = open(p, encoding="utf-8").read()
    if "<!--tw-air-->" in html:
        return
    rows = "".join('<div class="v"><h3>%s</h3><a href="%s"><img loading="lazy" src="%s" alt="%s 測站"></a>'
                   '<a href="%s"><img loading="lazy" src="%s" alt="%s 鄉鎮"></a></div>' % (
                       v["name"], v["img"]["station"], v["img"]["station"], v["name"], v["img"]["town"], v["img"]["town"], v["name"])
                   for v in man["vars"])
    sec = ('<!--tw-air--><h1>🌫️ 全台空氣品質</h1><p>監測時間：%s（臺灣時間）｜每小時更新｜資料來源：環境部｜鄉鎮規則：%s</p>'
           '<div class="g">%s</div><p>%s。</p>') % (
        man["obsTime"][:16].replace("T", " "), AIR_RULE, rows, AIR_DISCLAIMER)
    html = html.replace("</body>", sec + "</body>")
    open(p, "w", encoding="utf-8").write(html)


# ---------------------------------------------------------------- 主流程
def build(a):
    t0 = time.time()
    out = a.out
    key = os.environ.get("MOENV_API_KEY", "").strip()
    if not key:
        raise RuntimeError("沒有設定 MOENV_API_KEY（GitHub → Settings → Secrets and variables → Actions）")
    os.makedirs(out, exist_ok=True)
    pages, prev = a.pages_base, a.prev_dir
    gen = now_tpe()
    sites_doc, sites_src = load_sites(key, pages, prev, gen.strftime("%Y-%m-%d"))
    recs = moenv(DS_AQI, key)
    geo = town_geo()
    stations, T = build_stations(recs, sites_doc.get("sites") or [], geo)
    if not T or not stations:
        raise RuntimeError("aqx_p_432 沒有可用的監測時間")
    if (gen - T).total_seconds() > 6 * 3600:
        raise RuntimeError("aqx_p_432 資料太舊（%s）" % iso(T))
    no_town = [r["n"] for r in stations.values() if not r.get("tc")]
    log("📥 aqx_p_432：%d 站，監測時間 %s｜站點清單 %s（%d 站）%s" % (
        len(stations), iso(T), sites_src, len(sites_doc.get("sites") or []), ("｜對不到鄉鎮：" + "、".join(no_town)) if no_town else ""))
    tagg = aggregate_towns(stations, geo)
    area_map = county_area_map(sites_doc.get("sites") or [])
    counties = county_summary(stations, area_map)
    store = update_store(load_store(pages, prev), stations, T)
    log("🕒 空品 48h 歷史 %d 個影格" % len(store["frames"]))
    times = {"frame": iso(T), "generated": iso(gen)}

    from tw_air_render import AirRenderer
    rd = AirRenderer(logo=a.logo)
    fk = fkey(T)
    hist = os.path.join(out, "img", "history_aq", fk)
    os.makedirs(os.path.join(out, "img", "latest"), exist_ok=True)
    os.makedirs(hist, exist_ok=True)
    names, nbytes = [], 0
    for v in AIR_VARS:
        for style in ("station", "town"):
            png = os.path.join(out, "img", "latest", "%s_%s.png" % (v["key"], style))
            rd.render(v, style, stations, tagg, times, png, source=AIR_SOURCE)
            nm = "%s_%s.webp" % (v["key"], style)
            rd.to_webp(png, os.path.join(hist, nm))
            names.append(nm)
            nbytes += os.path.getsize(png)
    log("🖼️ 空品 %d 張 PNG（%.1f MB）＋ WebP，耗時 %.0f 秒" % (len(names), nbytes / 1e6, time.time() - t0))
    keep = {f["k"] for f in store["frames"]}
    got, want = copy_prev_frames(keep - {fk}, out, pages, prev, names)
    log("🗂️ 空品歷史圖：沿用 %d/%d 張" % (got, want))
    frames_idx = []
    for f in store["frames"]:
        d = os.path.join(out, "img", "history_aq", f["k"])
        if os.path.isdir(d) and os.listdir(d):
            frames_idx.append({"k": f["k"], "t": f["t"], "n": len(os.listdir(d))})
    dump(os.path.join(out, "img", "history_aq", "index.json"),
         {"updated": iso(gen), "frames": frames_idx, "pattern": "img/history_aq/{k}/{var}_{style}.webp"})

    st_out = []
    for r in sorted(stations.values(), key=lambda r: r["id"]):
        st_out.append({k: v for k, v in r.items() if v is not None and v != ""})
    sz = {}
    sz["latest"] = dump(os.path.join(out, "data", "air", "latest.json"), {"times": times, "count": len(st_out), "stations": st_out})
    sz["towns"] = dump(os.path.join(out, "data", "air", "towns.json"), {"times": times, "rule": AIR_RULE, "towns": list(tagg.values())})
    sz["counties"] = dump(os.path.join(out, "data", "air", "counties.json"), {"times": times, "areas": area_map, "counties": counties})
    sz["sites"] = dump(os.path.join(out, "data", "air", "sites.json"), sites_doc)
    sz["store"] = dump(os.path.join(out, "data", "air", "history", "store.json.gz"), store, gz=True)
    base = pages or "./"
    man = {"version": AIR_VERSION, "obsTime": iso(T), "times": times, "generated": iso(gen), "base": base,
           "source": AIR_SOURCE, "sources": {DS_AQI: "api", DS_SITES: sites_src}, "disclaimer": AIR_DISCLAIMER,
           "rules": AIR_AGG_TEXT, "rule": AIR_RULE, "noStation": "鄉鎮內沒有空品測站 → 灰色斜線（不以鄰近測站代替）",
           "scales": AIR_SCALES, "historyFrames": len(frames_idx), "stationCount": len(stations), "townCount": len(tagg),
           "areas": area_map,
           "vars": [{"key": v["key"], "group": v["group"], "name": v["name"], "scale": v["scale"], "agg": v["agg"],
                     "dec": v["dec"], "note": v["note"],
                     "img": {"station": base + "img/latest/%s_station.png" % v["key"], "town": base + "img/latest/%s_town.png" % v["key"]}}
                    for v in AIR_VARS]}
    dump(os.path.join(out, "air.json"), man)
    append_index(out, man)
    log("📦 空品 JSON（未壓縮）：" + "、".join("%s %.0f KB" % (k, v / 1024) for k, v in sz.items()))
    log("✅ 空品完成，總耗時 %.0f 秒" % (time.time() - t0))
    return man


def needs_update(pages_base, now=None):
    """給 tw_weather.py --check 用（只用內建模組、不呼叫環境部）：線上 air.json 不是這個整點（且已過 :15）→ True"""
    now = now or now_tpe()
    if not pages_base or now.minute < 15:
        return False
    try:
        man = json.loads(http_get(pages_base + "air.json?ts=%d" % time.time(), timeout=20).decode("utf-8"))
    except Exception:
        return True
    t = parse_t(man.get("obsTime"))
    cur = now.replace(minute=0, second=0, microsecond=0)
    return (t is None) or (t < cur) or bool(man.get("stale"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="site")
    ap.add_argument("--pages-base", default=os.environ.get("PAGES_BASE", DEFAULT_PAGES))
    ap.add_argument("--prev-dir", default=None, help="（測試用）上一版網站資料夾，取代線上 Pages")
    ap.add_argument("--logo", default=os.path.join(ROOT, "assets", "logo.png"))
    ap.add_argument("--no-carry", action="store_true", help="失敗時不要沿用上一版")
    a = ap.parse_args()
    if a.pages_base and not a.pages_base.endswith("/"):
        a.pages_base += "/"
    if a.pages_base in ("none", "none/"):
        a.pages_base = ""
    if not os.environ.get("MOENV_API_KEY", "").strip():
        log("ℹ️ 沒有設定 MOENV_API_KEY → 略過空氣品質（天氣不受影響）。設定方式見 README")
        return
    try:
        build(a)
    except Exception as e:
        log("❌ 空品失敗（天氣圖照常發布）：%s" % mask(e))
        if not a.no_carry:
            try:
                carry_prev(a.out, a.pages_base, a.prev_dir, str(e))
            except Exception as e2:
                log("⚠️ 沿用上一版也失敗：%s" % mask(e2))
        sys.exit(1)


if __name__ == "__main__":
    main()
