#!/usr/bin/env python3
"""tw-weather：全台觀測資料 → JSON＋測站圖＋鄉鎮面量圖 → GitHub Pages

資料（全部走氣象署 S3 公開鏡像，不用金鑰、不吃配額）：
  O-A0001-001 自動氣象站（逐時，約 876 站）  O-A0003-001 署屬氣象站（10 分鐘，約 363 站，含陣風）
  O-A0002-001 雨量站（10 分鐘，約 1,300 站）  W-C0033-001/002 縣市警特報  F-C0032-001 縣市 36 小時預報
S3 失敗才用「檔案下載 API」（需 secrets.CWA_API_KEY，1 次呼叫、0 流量；金鑰絕不印出）。

用法：
  python3 scripts/tw_weather.py --check --out site     # 只用內建模組：這個整點已經做過就輸出 skip=true
  python  scripts/tw_weather.py --out site             # 完整：抓資料（或讀 site/_raw）、48h 歷史、畫圖、產生網站
"""
import argparse
import concurrent.futures as cf
import gzip
import json
import os
import shutil
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tw_common import (SCRIPT_VERSION, TPE, ROOT, DISCLAIMER, HISTORY_HOURS, COUNTY_ORDER, VARS, VAR_BY_KEY, SCALES,
                       AGG_TEXT, norm_name, now_tpe, parse_t, iso, fkey, num, sea_level_pressure, compass16, beaufort)

S3 = "https://cwaopendata.s3.ap-northeast-1.amazonaws.com/"
DATASETS = {   # id → S3 路徑
    "O-A0001-001": "Observation/O-A0001-001.json",
    "O-A0003-001": "Observation/O-A0003-001.json",
    "O-A0002-001": "Observation/O-A0002-001.json",
    "W-C0033-001": "Warning/W-C0033-001.json",
    "W-C0033-002": "Warning/W-C0033-002.json",
    "F-C0032-001": "Forecast/F-C0032-001.json",
}
REQUIRED = ("O-A0001-001", "O-A0002-001")
FILEAPI = "https://opendata.cwa.gov.tw/fileapi/v1/opendataapi/%s?downloadType=WEB&format=JSON&Authorization="
UA = {"User-Agent": "tw-weather (GitHub Actions)"}
DEFAULT_PAGES = "https://seanminecraft.github.io/tw-weather/"


def log(*a):
    print(*[mask(x) for x in a], flush=True)


def mask(s):
    k = os.environ.get("CWA_API_KEY", "")
    s = str(s)
    return s.replace(k, "***") if k else s


def http_get(url, timeout=40):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            data = gzip.decompress(data)
        return data


def payload(doc):
    """S3／檔案下載（cwaopendata.dataset）與資料擷取 API（records）兩種格式 → 內容"""
    if isinstance(doc, dict) and "cwaopendata" in doc:
        c = doc["cwaopendata"]
        return c.get("dataset"), c.get("sent") or ""
    if isinstance(doc, dict) and "records" in doc:
        return doc["records"], ""
    return None, ""


def is_stale(did, doc, max_min=100):
    """觀測資料的最新觀測時間比現在舊太多（S3 偶爾會給舊檔）→ True"""
    if not did.startswith("O-"):
        return False
    ts = [parse_t((s.get("ObsTime") or {}).get("DateTime")) for s in stations_of(doc)[:400]]
    ts = [t for t in ts if t]
    if not ts:
        return True
    return (now_tpe() - max(ts)).total_seconds() > max_min * 60


def fetch_one(did):
    """回傳 (doc, 來源)。S3 → 檔案下載 API（有金鑰才試）；S3 給舊檔會重抓一次"""
    errs = []
    stale_doc = None
    for attempt in range(2):
        try:
            doc = json.loads(http_get(S3 + DATASETS[did] + ("?r=%d" % time.time() if attempt else "")).decode("utf-8"))
            if payload(doc)[0] is not None:
                if is_stale(did, doc) and attempt == 0:
                    stale_doc = doc
                    log("⚠️ %s 的 S3 檔案觀測時間偏舊，20 秒後重抓" % did)
                    time.sleep(20)
                    continue
                return doc, "S3"
            errs.append("S3 格式不符")
        except Exception as e:
            errs.append("S3 %s" % e)
            time.sleep(2)
    key = os.environ.get("CWA_API_KEY", "").strip()
    if key:
        try:
            doc = json.loads(http_get(FILEAPI % did + key).decode("utf-8"))
            if payload(doc)[0] is not None:
                return doc, "fileapi"
        except Exception as e:
            errs.append("fileapi %s" % mask(e))
    if stale_doc is not None:
        return stale_doc, "S3(舊)"
    raise RuntimeError("%s 下載失敗：%s" % (did, "；".join(errs)))


def fetch_all(raw_dir=None, only=None):
    """抓全部資料集（平行）；raw_dir 有快取就直接讀"""
    out, src = {}, {}
    ids = list(only or DATASETS.keys())
    todo = []
    for did in ids:
        p = os.path.join(raw_dir, did + ".json") if raw_dir else None
        if p and os.path.exists(p):
            out[did] = json.load(open(p, encoding="utf-8"))
            src[did] = "cache"
        else:
            todo.append(did)
    with cf.ThreadPoolExecutor(6) as ex:
        futs = {ex.submit(fetch_one, d): d for d in todo}
        for f in cf.as_completed(futs):
            d = futs[f]
            try:
                out[d], src[d] = f.result()
                if raw_dir:
                    os.makedirs(raw_dir, exist_ok=True)
                    with open(os.path.join(raw_dir, d + ".json"), "w", encoding="utf-8") as fp:
                        json.dump(out[d], fp, ensure_ascii=False)
            except Exception as e:
                log("⚠️", e)
    for d in REQUIRED:
        if d in ids and d not in out:
            raise SystemExit("❌ 必要資料 %s 取得失敗，本次不更新（網站維持上一版）" % d)
    return out, src


def stations_of(doc):
    ds, _ = payload(doc)
    st = (ds or {}).get("Station") or []
    return st if isinstance(st, list) else [st]


def latlon(s):
    co = ((s.get("GeoInfo") or {}).get("Coordinates")) or []
    if isinstance(co, dict):
        co = [co]
    pick = None
    for c in co:
        if c.get("CoordinateName") == "WGS84":
            pick = c
    pick = pick or (co[0] if co else {})
    return num(pick.get("StationLatitude")), num(pick.get("StationLongitude"))


# ------------------------------------------------------------------ 合併測站
def obs_hour(docs):
    """本次影格時間＝O-A0001 最常見的觀測時間（整點）"""
    from collections import Counter
    c = Counter()
    for s in stations_of(docs.get("O-A0001-001")):
        t = parse_t((s.get("ObsTime") or {}).get("DateTime"))
        if t:
            c[t.replace(minute=0, second=0, microsecond=0)] += 1
    if not c:
        return now_tpe().replace(minute=0, second=0, microsecond=0)
    return c.most_common(1)[0][0]


def merge_stations(docs):
    """O-A0001（全部自動站）＋ O-A0003（署屬站較新、含陣風）＋ O-A0002（雨量）→ {站號: 記錄}"""
    S = {}

    def base(s, src):
        g = s.get("GeoInfo") or {}
        la, lo = latlon(s)
        return {"id": s.get("StationId"), "n": s.get("StationName"), "c": norm_name(g.get("CountyName")),
                "town": norm_name(g.get("TownName")), "tc": str(g.get("TownCode") or ""), "la": la, "lo": lo,
                "al": num(g.get("StationAltitude"), lo=-500), "src": src}

    for did, src in (("O-A0001-001", "auto"), ("O-A0003-001", "cwa")):
        for s in stations_of(docs.get(did)):
            sid = s.get("StationId")
            if not sid:
                continue
            w = s.get("WeatherElement") or {}
            rec = S.get(sid) or base(s, src)
            rec["src"] = src if src == "cwa" else rec.get("src", src)
            ot = parse_t((s.get("ObsTime") or {}).get("DateTime"))
            if rec.get("_ot") and ot and ot < rec["_ot"]:
                continue                                  # 已有較新的
            rec["_ot"] = ot
            rec["ot"] = iso(ot)
            de = w.get("DailyExtreme") or {}
            hi = ((de.get("DailyHigh") or {}).get("TemperatureInfo")) or {}
            lo_ = ((de.get("DailyLow") or {}).get("TemperatureInfo")) or {}
            gi = w.get("GustInfo") or {}
            mx = w.get("Max10MinAverage") or {}
            vals = {
                "t": num(w.get("AirTemperature")),
                "rh": num(w.get("RelativeHumidity"), lo=-1),
                "ps": num(w.get("AirPressure"), lo=100),
                "ws": num(w.get("WindSpeed"), lo=-0.01),
                "wd": num(w.get("WindDirection"), lo=-0.01),
                "tx": num(hi.get("AirTemperature")), "txt": iso(parse_t((hi.get("Occurred_at") or {}).get("DateTime"))),
                "tn": num(lo_.get("AirTemperature")), "tnt": iso(parse_t((lo_.get("Occurred_at") or {}).get("DateTime"))),
                "g": num(gi.get("PeakGustSpeed"), lo=-0.01),
                "gd": num((gi.get("Occurred_at") or {}).get("WindDirection"), lo=-0.01),
                "gt": iso(parse_t((gi.get("Occurred_at") or {}).get("DateTime"))),
                "wm": num(mx.get("WindSpeed"), lo=-0.01),
                "wmt": iso(parse_t((mx.get("Occurred_at") or {}).get("DateTime"))),
                "wx": (w.get("Weather") if w.get("Weather") not in (None, "-99", "") else None),
            }
            if vals["wd"] == 0 and (vals["ws"] or 0) == 0:
                vals["wd"] = None                         # 靜風
            for k, v in vals.items():
                if v is not None or k not in rec:
                    rec[k] = v
            S[sid] = rec
    for s in stations_of(docs.get("O-A0002-001")):
        sid = s.get("StationId")
        if not sid:
            continue
        r = s.get("RainfallElement") or {}
        rec = S.get(sid)
        if not rec:
            rec = base(s, "rain")
            S[sid] = rec

        def rv(k):
            return num((r.get(k) or {}).get("Precipitation"), lo=-0.01)
        rec.update({"r10m": rv("Past10Min"), "r1h": rv("Past1hr"), "r3h": rv("Past3hr"), "r6h": rv("Past6hr"),
                    "r12h": rv("Past12hr"), "r24h": rv("Past24hr"), "r2d": rv("Past2days"), "r3d": rv("Past3days"),
                    "rnow": rv("Now"), "rt": iso(parse_t((s.get("ObsTime") or {}).get("DateTime")))})
        if s.get("Maintainer"):
            rec["mt"] = s.get("Maintainer")
    out = {}
    for sid, rec in S.items():
        if rec.get("la") is None or rec.get("lo") is None:
            continue
        rec.pop("_ot", None)
        rec["p"] = sea_level_pressure(rec.get("ps"), rec.get("al"), rec.get("t"))
        if rec["p"] is not None:
            rec["p"] = round(rec["p"], 1)
        out[sid] = rec
    return out


# ------------------------------------------------------------------ 48 小時歷史（存在 GitHub Pages：data/history/store.json.gz）
HKEYS = ["t", "tx", "tn", "r10m", "r1h", "r24h", "p", "ws", "wd", "g", "wm", "wmt", "gt", "rh"]


def frame_of(stations, t):
    d = {}
    for sid, r in stations.items():
        row = []
        for k in HKEYS:
            v = r.get(k)
            if k in ("wmt", "gt"):
                tt = parse_t(v)
                v = int(tt.timestamp() // 60) if tt else None    # 發生時間：epoch 分鐘
            elif isinstance(v, float):
                v = round(v, 1)
            row.append(v)
        if any(x is not None for x in row):
            d[sid] = row
    return {"t": iso(t), "k": fkey(t), "d": d}


def load_store(pages_base, local_dir=None):
    """上一版 48h 歷史：本機（測試）→ 線上 Pages；拿不到就從空的開始"""
    raw = None
    if local_dir and os.path.exists(os.path.join(local_dir, "data", "history", "store.json.gz")):
        raw = open(os.path.join(local_dir, "data", "history", "store.json.gz"), "rb").read()
    elif pages_base:
        try:
            raw = http_get(pages_base + "data/history/store.json.gz?ts=%d" % time.time(), timeout=60)
        except Exception as e:
            log("ℹ️ 線上沒有歷史檔（第一次執行或尚未部署）：", e)
    if not raw:
        return {"v": 1, "keys": HKEYS, "frames": [], "meta": {}}
    try:
        try:
            st = json.loads(gzip.decompress(raw).decode("utf-8"))
        except OSError:
            st = json.loads(raw.decode("utf-8"))
        if st.get("keys") != HKEYS:
            log("⚠️ 歷史欄位版本不同，重新開始")
            return {"v": 1, "keys": HKEYS, "frames": [], "meta": {}}
        return st
    except Exception as e:
        log("⚠️ 歷史檔損毀，重新開始：", e)
        return {"v": 1, "keys": HKEYS, "frames": [], "meta": {}}


def update_store(store, stations, t):
    fr = frame_of(stations, t)
    frames = [f for f in store.get("frames", []) if f.get("k") != fr["k"]]
    frames.append(fr)
    cutoff = t.timestamp() - HISTORY_HOURS * 3600
    frames = [f for f in frames if (parse_t(f["t"]) or t).timestamp() > cutoff - 1]
    frames.sort(key=lambda f: f["k"])
    meta = store.get("meta") or {}
    for sid, r in stations.items():
        meta[sid] = [r.get("n"), r.get("c"), r.get("town"), r.get("tc"), r.get("la"), r.get("lo"), r.get("al"), r.get("src")]
    alive = set()
    for f in frames:
        alive.update(f["d"].keys())
    meta = {k: v for k, v in meta.items() if k in alive}
    store.update({"v": 1, "keys": HKEYS, "frames": frames, "meta": meta, "updated": iso(now_tpe())})
    return store


def daily_max(store, stations, t):
    """今日（臺灣時間 00:00 起）最大平均風 wsx、最大陣風 gx：從 48h 歷史每小時影格取最大。
    署屬站另有「過去一小時最大 10 分鐘平均風」（wm）與陣風發生時間，只採計發生時間在今天的值。"""
    day0 = t.astimezone(TPE).replace(hour=0, minute=0, second=0, microsecond=0)
    d0 = int(day0.timestamp() // 60)
    ki = {k: i for i, k in enumerate(HKEYS)}
    acc_w, acc_wt, acc_g, acc_gt = {}, {}, {}, {}
    for f in store["frames"]:
        ft = parse_t(f["t"])
        if not ft or ft < day0:
            continue
        fm = int(ft.timestamp() // 60)
        for sid, row in f["d"].items():
            cands = []
            if row[ki["ws"]] is not None:
                cands.append((row[ki["ws"]], fm))
            if row[ki["wm"]] is not None and (row[ki["wmt"]] or fm) >= d0:
                cands.append((row[ki["wm"]], row[ki["wmt"]] or fm))
            for v, tm in cands:
                if v > acc_w.get(sid, -1):
                    acc_w[sid], acc_wt[sid] = v, tm
            gv, gtm = row[ki["g"]], row[ki["gt"]] or fm
            if gv is not None and gtm >= d0 and gv > acc_g.get(sid, -1):
                acc_g[sid], acc_gt[sid] = gv, gtm

    def tstr(m):
        import datetime as _dt
        return iso(_dt.datetime.fromtimestamp(m * 60, TPE))
    for sid, r in stations.items():
        r["wsx"] = acc_w.get(sid)
        r["wsxt"] = tstr(acc_wt[sid]) if sid in acc_wt else None
        r["gx"] = acc_g.get(sid)
        r["gxt"] = tstr(acc_gt[sid]) if sid in acc_gt else None
    hours = len([f for f in store["frames"] if (parse_t(f["t"]) or t) >= day0])
    return hours


# ------------------------------------------------------------------ 鄉鎮／縣市彙整
def town_table():
    d = json.load(open(os.path.join(ROOT, "assets", "taiwan", "taiwan_towns.json"), encoding="utf-8"))
    return [{"code": t["code"], "c": t["c"], "t": t["t"]} for t in d["towns"]]


def aggregate_towns(stations, towns):
    by = {}
    name_ix = {(norm_name(t["c"]), norm_name(t["t"])): t["code"] for t in towns}
    codes = {t["code"] for t in towns}
    for sid, r in stations.items():
        tc = r.get("tc")
        if tc not in codes:
            tc = name_ix.get((norm_name(r.get("c")), norm_name(r.get("town"))))
            if not tc:
                continue
            r["tc"] = tc
        by.setdefault(tc, []).append(r)
    out = {}
    for t in towns:
        lst = by.get(t["code"], [])
        rec = {"code": t["code"], "c": t["c"], "town": t["t"], "n": len(lst), "st": sorted(r["id"] for r in lst)}
        for v in VARS:
            k, agg = v["key"], v["agg"]
            src_k = "ws" if agg == "dir" else k
            have = [r for r in lst if r.get(src_k) is not None and (agg != "dir" or r.get("wd") is not None)]
            if not have:
                rec[k] = None
                continue
            if agg == "max" or agg == "dir":
                best = max(have, key=lambda r: r[src_k])
            elif agg == "min":
                best = min(have, key=lambda r: r[src_k])
            else:   # rep：海拔最低
                best = min(have, key=lambda r: (r.get("al") if r.get("al") is not None else 9999))
            if agg == "dir":
                rec["wd"] = best.get("wd")
                rec["wd_ws"] = best.get("ws")
            else:
                rec[k] = best.get(k)
            rec[k + "_by"] = best["id"]
        out[t["code"]] = rec
    return out


def parse_warnings(doc1, doc2):
    """W-C0033-001 → 各縣市目前有效的警特報清單；W-C0033-002 → 特報原文"""
    out = {c: [] for c in COUNTY_ORDER}
    sent = ""
    if doc1:
        ds, sent = payload(doc1)
        locs = (ds or {}).get("location") or []
        for loc in locs if isinstance(locs, list) else [locs]:
            name = norm_name(loc.get("locationName"))
            hz = ((loc.get("hazardConditions") or {}) or {}).get("hazards") if loc.get("hazardConditions") else None
            items = hz if isinstance(hz, list) else ([hz] if hz else [])
            for h in items:
                info = h.get("info") or {}
                vt = h.get("validTime") or {}
                areas = []
                sub = h.get("hazard")
                for s in (sub if isinstance(sub, list) else ([sub] if sub else [])):
                    si = s.get("info") or {}
                    al = ((si.get("affectedAreas") or {}).get("location")) or []
                    al = al if isinstance(al, list) else [al]
                    areas.append({"ph": si.get("phenomena"), "areas": [a.get("locationName") for a in al if a]})
                out.setdefault(name, []).append({"ph": info.get("phenomena"), "sig": info.get("significance"),
                                                 "start": vt.get("startTime"), "end": vt.get("endTime"), "sub": areas})
    texts = []
    if doc2:
        ds2, _ = payload(doc2)
        for it in (ds2 if isinstance(ds2, list) else ([ds2] if ds2 else [])):
            di = it.get("datasetInfo") or {}
            ct = (((it.get("contents") or {}).get("content")) or {})
            if isinstance(ct, list):
                ct = ct[0] if ct else {}
            texts.append({"title": di.get("datasetDescription"), "issue": di.get("issueTime"),
                          "start": (di.get("validTime") or {}).get("startTime"), "end": (di.get("validTime") or {}).get("endTime"),
                          "text": ct.get("contentText")})
    return {"sent": sent, "counties": out, "texts": texts}


# ---------- v22.1：鄉鎮級警特報（CAP 檔，S3 公開鏡像，不用金鑰）----------
CAP_FILES = ["W-C0033-003", "W-C0033-004", "W-C0033-005", "W-C0033-006", "W-C0033-010", "W-C0034-001"]


def hz_key(s):
    s = str(s or "")
    for k, pat in (("ty", "颱風"), ("rain", "雨"), ("wind", "風"), ("cold", "低溫"), ("heat", "高溫"), ("fog", "霧")):
        if pat in s:
            return k
    if "寒" in s:
        return "cold"
    if "熱" in s:
        return "heat"
    return "other"


def fetch_caps(raw_dir=None):
    """回傳 {檔名: XML 文字}；失敗的檔案略過"""
    out = {}

    def get(f):
        p = os.path.join(raw_dir, "cap", f + ".cap") if raw_dir else None
        if p and os.path.exists(p):
            return f, open(p, encoding="utf-8").read()
        try:
            t = http_get(S3 + "Warning/" + f + ".cap", timeout=25).decode("utf-8")
            if p:
                os.makedirs(os.path.dirname(p), exist_ok=True)
                open(p, "w", encoding="utf-8").write(t)
            return f, t
        except Exception as e:
            log("⚠️ CAP %s 讀取失敗：%s" % (f, mask(e)))
            return f, None

    with cf.ThreadPoolExecutor(6) as ex:
        for f, t in ex.map(get, CAP_FILES):
            if t:
                out[f] = t
    return out


def split_area(desc):
    s = norm_name(desc)
    for c in COUNTY_ORDER:
        if s.startswith(c):
            return c, s[len(c):]
    return "", ""


def parse_caps(caps, now=None):
    """CAP 1.2 → [{hk, ph, lvl, prio, start, end, upcoming, areas:[{c,t}]}]（略過 Cancel／解除／已過期）"""
    import xml.etree.ElementTree as ET
    now = now or now_tpe()
    out = []

    def tag(e):
        return e.tag.split("}")[-1]

    def kid(e, name):
        for x in e.iter():
            if tag(x) == name:
                return (x.text or "").strip()
        return ""

    for fname, txt in sorted((caps or {}).items()):
        try:
            root = ET.fromstring(txt.encode("utf-8"))
        except Exception as e:
            log("⚠️ CAP %s 解析失敗：%s" % (fname, e))
            continue
        msg = ""
        for x in root:
            if tag(x) == "msgType":
                msg = (x.text or "").strip()
        if msg == "Cancel":
            continue
        for info in [x for x in root if tag(x) == "info"]:
            head, ev = kid(info, "headline"), kid(info, "event")
            par = {}
            for p in [x for x in info if tag(x) == "parameter"]:
                par[kid(p, "valueName")] = kid(p, "value")
            start = kid(info, "onset") or kid(info, "effective")
            end = kid(info, "expires")
            if "解除" in head:
                continue
            te = parse_t(end) if end else None
            if te and te < now:
                continue
            ts = parse_t(start) if start else None
            title = par.get("alert_title") or head or ev
            col = par.get("alert_color") or ""
            hk = hz_key(title + ev)
            prio = ("ty" if hk == "ty" else "purple" if "紫" in col else "red" if "紅" in col else "orange" if "橙" in col
                    else "yellow" if "黃" in col else ("red" if "豪雨" in title else "orange" if "大雨" in title else "yellow"))
            areas = []
            for a in [x for x in info if tag(x) == "area"]:
                c, t = split_area(kid(a, "areaDesc"))
                if c:
                    areas.append({"c": c, "t": t})
            if areas:
                out.append({"file": fname, "hk": hk, "ph": title, "lvl": par.get("severity_level", ""), "prio": prio,
                            "start": start, "end": end, "upcoming": bool(ts and ts > now), "areas": areas})
    return out


def merge_caps(counties, caps):
    """把 CAP 的鄉鎮範圍併進 W-C0033-001 的縣市清單（同一種災害＝同一筆，補 towns；001 沒有的就新增 capOnly）"""
    out = {}
    for c in COUNTY_ORDER:
        out[c] = []
        for w in (counties.get(c) or []):
            ts = parse_t(w.get("start")) if w.get("start") else None
            out[c].append(dict(w, hk=hz_key(w.get("ph")), upcoming=bool(ts and ts > now_tpe())))
    for cap in caps:
        by = {}
        for a in cap["areas"]:
            o = by.setdefault(a["c"], {"all": False, "towns": []})
            if not a["t"]:
                o["all"] = True
            elif a["t"] not in o["towns"]:
                o["towns"].append(a["t"])
        for c, o in by.items():
            lst = out.setdefault(c, [])
            towns = None if o["all"] else o["towns"]
            ex = next((w for w in lst if w.get("hk") == cap["hk"] and not w.get("_cap")), None)
            if ex is not None:
                if towns:
                    ex["towns"] = towns
                ex["prio"] = cap["prio"]
                ex["lvl"] = cap["lvl"]
                ex["_cap"] = True
                ex["end"] = ex.get("end") or cap["end"]
            else:
                lst.append({"ph": cap["ph"], "sig": "", "start": cap["start"], "end": cap["end"], "sub": [], "towns": towns,
                            "prio": cap["prio"], "lvl": cap["lvl"], "hk": cap["hk"], "_cap": True, "capOnly": True,
                            "upcoming": cap["upcoming"]})
    return out


def town_warn_index(counties, towns_geo):
    """鄉鎮代碼 → [{ph, prio, upcoming}]（縣市級＝全縣市鄉鎮）"""
    by_name, by_c = {}, {}
    for t in towns_geo:
        by_name[(norm_name(t["c"]), norm_name(t["t"]))] = t["code"]
        by_c.setdefault(norm_name(t["c"]), []).append(t["code"])
    ix = {}
    for c, lst in counties.items():
        for w in lst:
            tw = w.get("towns") or []
            codes = [by_name.get((norm_name(c), norm_name(x))) for x in tw]
            codes = [x for x in codes if x] or by_c.get(norm_name(c), [])
            for k in codes:
                ix.setdefault(k, []).append({"ph": w.get("ph"), "prio": w.get("prio"), "upcoming": bool(w.get("upcoming")),
                                             "end": w.get("end")})
    return ix


def parse_forecast36(doc):
    out = {}
    if not doc:
        return out, ""
    ds, _ = payload(doc)
    issue = ((ds or {}).get("datasetInfo") or {}).get("issueTime", "")
    for loc in (ds or {}).get("location") or []:
        name = norm_name(loc.get("locationName"))
        el = {e.get("elementName"): e.get("time") or [] for e in loc.get("weatherElement") or []}
        per = []
        for i, w in enumerate(el.get("Wx", [])):
            def g(k, f="parameterName"):
                arr = el.get(k) or []
                return ((arr[i].get("parameter") or {}).get(f)) if i < len(arr) else None
            per.append({"start": w.get("startTime"), "end": w.get("endTime"), "wx": g("Wx"), "wxv": g("Wx", "parameterValue"),
                        "pop": g("PoP"), "minT": g("MinT"), "maxT": g("MaxT"), "ci": g("CI")})
        out[name] = per
    return out, issue


def county_summary(stations, towns_agg, warn, fc36):
    out = {}
    for c in COUNTY_ORDER:
        sts = [r for r in stations.values() if norm_name(r.get("c")) == c]
        tws = [t for t in towns_agg.values() if t["c"] == c]

        def mx(k, fn=max):
            vals = [(r[k], r) for r in sts if r.get(k) is not None]
            if not vals:
                return None
            v, r = fn(vals, key=lambda x: x[0])
            return {"v": v, "id": r["id"], "n": r["n"], "town": r.get("town")}
        code = (tws[0]["code"][:5] if tws else "")
        out[c] = {"c": c, "code": code, "nst": len(sts), "ntw": len(tws),
                  "tx": mx("tx"), "tn": mx("tn", min), "t": mx("t"), "r1h": mx("r1h"), "r24h": mx("r24h"),
                  "r3d": mx("r3d"), "gx": mx("gx"), "g": mx("g"), "ws": mx("ws"), "wsx": mx("wsx"),
                  "warn": warn["counties"].get(c, []), "fc36": fc36.get(c, [])}
    return out


# ------------------------------------------------------------------ 輸出
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


STATION_KEYS = ["id", "n", "c", "town", "tc", "la", "lo", "al", "src", "mt", "ot", "rt", "wx", "t", "rh", "tx", "txt", "tn", "tnt",
                "r10m", "r1h", "r3h", "r6h", "r12h", "r24h", "r2d", "r3d", "rnow", "ps", "p", "ws", "wd", "g", "gd", "gt",
                "wm", "wmt", "wsx", "wsxt", "gx", "gxt"]


def station_out(r):
    o = {}
    for k in STATION_KEYS:
        v = r.get(k)
        if v is None:
            continue
        o[k] = round(v, 2) if isinstance(v, float) else v
    return o


def county_history_files(store, towns_agg, out_dir):
    """前端用：每縣市一檔（測站 48h 序列＋鄉鎮彙整序列），點選才載入"""
    ki = {k: i for i, k in enumerate(HKEYS)}
    frames = store["frames"]
    times = [f["t"] for f in frames]
    meta = store["meta"]
    series_keys = ["t", "r1h", "r24h", "p", "ws", "g", "rh"]
    by_c = {}
    for sid, m in meta.items():
        c = norm_name(m[1])
        by_c.setdefault(c, []).append(sid)
    town_of = {}
    for code, t in towns_agg.items():
        for sid in t["st"]:
            town_of[sid] = code
    total = 0
    for c, sids in by_c.items():
        code = next((t["code"][:5] for t in towns_agg.values() if t["c"] == c), None)
        if not code:
            continue
        st = {}
        for sid in sids:
            s = {}
            for k in series_keys:
                arr = [(f["d"].get(sid) or [None] * len(HKEYS))[ki[k]] for f in frames]
                if any(x is not None for x in arr):
                    s[k] = arr
            if s:
                st[sid] = {"n": meta[sid][0], "town": meta[sid][2], "tc": town_of.get(sid, meta[sid][3]), "s": s}
        tw = {}
        for tcode, t in towns_agg.items():
            if t["c"] != c:
                continue
            s = {}
            for k, fn in (("t", "rep"), ("r1h", max), ("r24h", max), ("ws", max), ("g", max)):
                arr = []
                for i in range(len(frames)):
                    vals = []
                    for sid in t["st"]:
                        if sid in st and k in st[sid]["s"] and st[sid]["s"][k][i] is not None:
                            vals.append((st[sid]["s"][k][i], meta[sid][6] if meta[sid][6] is not None else 9999))
                    if not vals:
                        arr.append(None)
                    elif fn == "rep":
                        arr.append(min(vals, key=lambda x: x[1])[0])
                    else:
                        arr.append(max(v for v, _ in vals))
                if any(x is not None for x in arr):
                    s[k] = arr
            tw[tcode] = s
        total += dump(os.path.join(out_dir, code + ".json"), {"c": c, "code": code, "times": times, "st": st, "tw": tw})
    return total


def write_index_html(out, man):
    rows = []
    for v in man["vars"]:
        rows.append('<div class="v"><h3>%s</h3><a href="%s"><img loading="lazy" src="%s" alt="%s 測站"></a>'
                    '<a href="%s"><img loading="lazy" src="%s" alt="%s 鄉鎮"></a></div>' % (
                        v["name"], v["img"]["station"], v["img"]["station"], v["name"], v["img"]["town"], v["img"]["town"], v["name"]))
    html = """<!doctype html><html lang="zh-TW"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>全台天氣觀測圖</title><style>body{font-family:system-ui,'Noto Sans TC',sans-serif;margin:16px;background:#0f172a;color:#e2e8f0}
a{color:#7dd3fc}.g{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:14px}.v{background:#1e293b;border-radius:10px;padding:8px}
.v img{width:49%%;border-radius:6px}h3{margin:4px 0 8px;font-size:15px}</style></head><body>
<h1>🗺️ 全台天氣觀測圖</h1><p>觀測時間：%s（臺灣時間）｜每小時更新｜資料來源：中央氣象署</p>
<div class="g">%s</div><p>%s。</p></body></html>""" % (man["obsTime"][:16].replace("T", " "), "".join(rows), DISCLAIMER)
    open(os.path.join(out, "index.html"), "w", encoding="utf-8").write(html)


def gh_output(k, v):
    p = os.environ.get("GITHUB_OUTPUT")
    if p:
        with open(p, "a", encoding="utf-8") as fp:
            fp.write("%s=%s\n" % (k, v))
    log("::%s=%s" % (k, v))


def prev_history_frames(pages_base, local_prev, keep_keys):
    """上一版逐時歷史圖（WebP）：本機（測試）或線上 Pages 下載回來，一起部署"""
    idx = None
    if local_prev and os.path.exists(os.path.join(local_prev, "img", "history", "index.json")):
        idx = json.load(open(os.path.join(local_prev, "img", "history", "index.json"), encoding="utf-8"))
    elif pages_base:
        try:
            idx = json.loads(http_get(pages_base + "img/history/index.json?ts=%d" % time.time()).decode("utf-8"))
        except Exception as e:
            log("ℹ️ 線上沒有歷史圖索引：", e)
    return idx or {"frames": []}


def copy_prev_frames(idx, keep_keys, out, pages_base, local_prev, names):
    jobs = []
    for fr in idx.get("frames", []):
        k = fr.get("k")
        if k not in keep_keys:
            continue
        for nm in names:
            rel = "img/history/%s/%s" % (k, nm)
            dst = os.path.join(out, rel)
            if os.path.exists(dst):
                continue
            jobs.append((rel, dst))

    def get(job):
        rel, dst = job
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        try:
            if local_prev and os.path.exists(os.path.join(local_prev, rel)):
                shutil.copyfile(os.path.join(local_prev, rel), dst)
            else:
                data = http_get(pages_base + rel, timeout=30)
                open(dst, "wb").write(data)
            return True
        except Exception:
            return False
    ok = 0
    with cf.ThreadPoolExecutor(16) as ex:
        for r in ex.map(get, jobs):
            ok += 1 if r else 0
    return ok, len(jobs)


def build(a):
    t0 = time.time()
    out = a.out
    os.makedirs(out, exist_ok=True)
    raw_dir = os.path.join(out, "_raw")
    docs, src = fetch_all(raw_dir)
    log("📥 來源：", ", ".join("%s=%s" % (k, src[k]) for k in sorted(src)))
    T = obs_hour(docs)
    stations = merge_stations(docs)
    log("🧭 影格 %s，測站 %d" % (iso(T), len(stations)))
    pages = a.pages_base
    store = load_store(pages, a.prev_dir)
    store = update_store(store, stations, T)
    hours_today = daily_max(store, stations, T)
    log("🕒 48h 歷史 %d 個影格（今天 %d 個）" % (len(store["frames"]), hours_today))
    towns = town_table()
    tagg = aggregate_towns(stations, towns)
    warn = parse_warnings(docs.get("W-C0033-001"), docs.get("W-C0033-002"))
    try:   # v22.1：鄉鎮級（CAP）
        caps = parse_caps(fetch_caps(raw_dir))
        warn["c001"] = warn["counties"]
        warn["caps"] = caps
        warn["counties"] = merge_caps(warn["counties"], caps)
        warn["towns"] = town_warn_index(warn["counties"], towns)
        log("⚠️ CAP 鄉鎮級警特報 %d 則，影響 %d 個鄉鎮" % (len(caps), len(warn["towns"])))
    except Exception as e:
        log("⚠️ CAP 處理失敗（只用縣市級）：%s" % mask(e))
    fc36, fc_issue = parse_forecast36(docs.get("F-C0032-001"))
    counties = county_summary(stations, tagg, warn, fc36)
    gen = now_tpe()
    rain_t = max([r["rt"] for r in stations.values() if r.get("rt")] or [None]) if any(r.get("rt") for r in stations.values()) else None
    cwa_t = max([r["ot"] for r in stations.values() if r.get("src") == "cwa" and r.get("ot")] or [""]) or None
    times = {"frame": iso(T), "auto": iso(T), "cwa": cwa_t, "rain": rain_t, "generated": iso(gen)}

    # ---- 圖
    from tw_render import Renderer
    rd = Renderer(logo=a.logo)
    fk = fkey(T)
    names = []
    hist_dir = os.path.join(out, "img", "history", fk)
    os.makedirs(os.path.join(out, "img", "latest"), exist_ok=True)
    os.makedirs(hist_dir, exist_ok=True)
    srcs_label = "中央氣象署"   # v22.4：圖上只寫資料來源，不寫資料集代碼／抓取方式（實際來源記在 manifest.json 的 sources）
    img_bytes = 0
    for v in VARS:
        for style in ("station", "town"):
            png = os.path.join(out, "img", "latest", "%s_%s.png" % (v["key"], style))
            rd.render(v, style, stations, tagg, times, png, source=srcs_label, hours_today=hours_today)
            webp = "%s_%s.webp" % (v["key"], style)
            rd.to_webp(png, os.path.join(hist_dir, webp))
            names.append(webp)
            img_bytes += os.path.getsize(png)
    log("🖼️ %d 張 PNG（%.1f MB）＋ WebP，耗時 %.0f 秒" % (len(names), img_bytes / 1e6, time.time() - t0))

    # ---- 逐時歷史圖：沿用線上（或本機上一版）48h 內的影格
    keep = {f["k"] for f in store["frames"]}
    idx = prev_history_frames(pages, a.prev_dir, keep)
    got, want = copy_prev_frames(idx, keep - {fk}, out, pages, a.prev_dir, names)
    log("🗂️ 歷史圖：沿用 %d/%d 張" % (got, want))
    frames_idx = []
    for f in store["frames"]:
        d = os.path.join(out, "img", "history", f["k"])
        if os.path.isdir(d):
            have = sorted(os.listdir(d))
            if have:
                frames_idx.append({"k": f["k"], "t": f["t"], "n": len(have)})
    dump(os.path.join(out, "img", "history", "index.json"), {"updated": iso(gen), "frames": frames_idx,
                                                              "pattern": "img/history/{k}/{var}_{style}.webp"})

    # ---- JSON
    st_out = [station_out(r) for r in sorted(stations.values(), key=lambda r: r["id"])]
    sz = {}
    sz["latest"] = dump(os.path.join(out, "data", "latest.json"), {"times": times, "count": len(st_out), "stations": st_out})
    sz["towns"] = dump(os.path.join(out, "data", "towns.json"), {"times": times, "rules": AGG_TEXT,
                                                                  "towns": list(tagg.values())})
    sz["counties"] = dump(os.path.join(out, "data", "counties.json"), {"times": times, "fc36Issue": fc_issue,
                                                                        "warnSent": warn["sent"], "counties": counties})
    sz["warnings"] = dump(os.path.join(out, "data", "warnings.json"), warn)
    sz["store"] = dump(os.path.join(out, "data", "history", "store.json.gz"), store, gz=True)
    sz["countyHist"] = county_history_files(store, tagg, os.path.join(out, "data", "history", "c"))
    os.makedirs(os.path.join(out, "geo"), exist_ok=True)
    shutil.copyfile(os.path.join(ROOT, "assets", "taiwan", "taiwan_towns.json"), os.path.join(out, "geo", "taiwan_towns.json"))
    base = pages or "./"
    man = {"version": SCRIPT_VERSION, "obsTime": iso(T), "times": times, "generated": iso(gen), "sources": src,
           "disclaimer": DISCLAIMER, "rules": AGG_TEXT, "noStation": "鄉鎮內沒有該項目的測站資料 → 灰色斜線（不以鄰近測站代替）",
           "scales": SCALES, "hoursToday": hours_today, "historyFrames": len(frames_idx),
           "vars": [{"key": v["key"], "group": v["group"], "name": v["name"], "scale": v["scale"], "agg": v["agg"],
                     "img": {"station": base + "img/latest/%s_station.png" % v["key"], "town": base + "img/latest/%s_town.png" % v["key"]}}
                    for v in VARS]}
    dump(os.path.join(out, "manifest.json"), man)
    dump(os.path.join(out, "scales.json"), SCALES)
    write_index_html(out, man)
    open(os.path.join(out, ".nojekyll"), "w").close()
    if not a.keep_raw:
        shutil.rmtree(raw_dir, ignore_errors=True)
    log("📦 JSON 大小（未壓縮）：" + "、".join("%s %.0f KB" % (k, v / 1024) for k, v in sz.items()))
    log("✅ 完成，總耗時 %.0f 秒" % (time.time() - t0))
    return man


def check(a):
    """快速檢查（只用內建模組）：線上 manifest 的影格＝目前 S3 的整點 → skip"""
    raw_dir = os.path.join(a.out, "_raw")
    docs, src = fetch_all(raw_dir, only=("O-A0001-001",))
    T = obs_hour(docs)
    online = None
    if a.pages_base:
        try:
            online = json.loads(http_get(a.pages_base + "manifest.json?ts=%d" % time.time()).decode("utf-8")).get("obsTime")
        except Exception as e:
            log("ℹ️ 讀不到線上 manifest：", e)
    skip = (online == iso(T)) and not a.force
    if skip and os.environ.get("MOENV_API_KEY", "").strip():   # v22.2：天氣已是最新、但空品還不是這個整點 → 照樣跑（不呼叫環境部）
        try:
            from tw_air import needs_update
            if needs_update(a.pages_base):
                skip = False
                log("🌫️ 線上空品還不是這個整點 → 需要更新")
        except Exception as e:
            log("ℹ️ 空品檢查略過：", e)
    log("🔎 S3 觀測整點 %s｜線上 %s → %s" % (iso(T), online, "略過（已是最新）" if skip else "需要更新"))
    mode = "skip" if skip else "full"
    if skip and online:   # v22.3：每小時資料已是最新，但 10 分鐘雨量有新的 10 分鐘 → 只更新 r10m（mode=r10）
        try:
            from tw_r10 import needs_update as r10_needs
            need, s3t, on = r10_needs(a.pages_base, raw_dir)
            log("🌧️ S3 10 分鐘雨量 %s｜線上 %s → %s" % (s3t, on, "只更新 10 分鐘雨量" if need else "略過"))
            if need:
                skip, mode = False, "r10"
        except Exception as e:
            log("ℹ️ 10 分鐘雨量檢查略過：", mask(e))
    gh_output("mode", mode)
    gh_output("skip", "true" if skip else "false")
    gh_output("frame", fkey(T))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="site")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--pages-base", default=os.environ.get("PAGES_BASE", DEFAULT_PAGES))
    ap.add_argument("--prev-dir", default=None, help="（測試用）上一版網站資料夾，取代線上 Pages")
    ap.add_argument("--logo", default=os.path.join(ROOT, "assets", "logo.png"))
    ap.add_argument("--keep-raw", action="store_true")
    a = ap.parse_args()
    if a.pages_base and not a.pages_base.endswith("/"):
        a.pages_base += "/"
    if a.pages_base in ("none", "none/"):
        a.pages_base = ""
    if a.check:
        check(a)
    else:
        build(a)


if __name__ == "__main__":
    main()
