#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
寒潮監測（tw-weather）→ site/data/coldsurge.json（schema "coldsurge-1.0"）

資料（全部免費；只有低溫特報 W-C0033-004 需要 secrets.CWA_API_KEY，金鑰絕不印出）：
  氣象署 S3 公開鏡像：C-B0024-001（30 天測站逐日統計）、O-A0003-001（10 分鐘綜觀）、W-C0033-001（備援：縣市目前特報）
  氣象署資料擷取 API：W-C0033-004 低溫特報（燈號；需金鑰）
  Open-Meteo（CC BY 4.0）：Forecast／Ensemble／Previous Runs API（ECMWF IFS、GFS/GEFS、ICON/ICON-EPS）
  NOAA AviationWeather METAR（上游 ZMCK／ZBAA／ZSSS）、NOAA CPC AO（觀測＋GEFS 預報）
  scripts/coldsurge_clim.json：ERA5 1991–2020 同期海平面氣壓氣候值（Open-Meteo Historical API 預先計算）

更新節奏：沿用 tw-weather 既有排程（無新 cron）。
  heavy（模式、AO、30 天觀測）：上一版超過 3 小時、或格式版本不同、或 --force 才重抓；
  light（低溫特報、目前觀測、METAR）：每次執行都更新。
  任何一段失敗 → 保留上一版該段並標 cached／stale；拿不到就標 error，絕不用 0 代替缺值。
"""
import argparse, json, math, os, sys, time, urllib.request, urllib.parse
from datetime import datetime, timedelta, timezone

SCHEMA = "coldsurge-1.0"
TPE = timezone(timedelta(hours=8))
UA = {"User-Agent": "tw-weather coldsurge (GitHub Actions)"}
S3 = "https://cwaopendata.s3.ap-northeast-1.amazonaws.com/"
OM_FC = "https://api.open-meteo.com/v1/forecast"
OM_ENS = "https://ensemble-api.open-meteo.com/v1/ensemble"
OM_PREV = "https://previous-runs-api.open-meteo.com/v1/forecast"
CWA_API = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/"
AWC = "https://aviationweather.gov/api/data/metar"
CPC = "https://ftp.cpc.ncep.noaa.gov/cwlinks/"
DEFAULT_PAGES = "https://seanminecraft.github.io/tw-weather/"
HEAVY_MAX_AGE_H = 3.0

TAIPEI = {"id": "466920", "name": "臺北", "lat": 25.0377, "lon": 121.5149}
# CWA 冷空氣分級（臺北站日最低溫）：>14 未達冷氣團；>12–≤14 大陸冷氣團；>10–≤12 強烈大陸冷氣團；≤10 寒流
LEVELS = [
    {"key": "cold_wave", "label": "寒流", "max": 10.0, "emoji": "🥶"},
    {"key": "strong", "label": "強烈大陸冷氣團", "max": 12.0, "emoji": "🟣"},
    {"key": "continental", "label": "大陸冷氣團", "max": 14.0, "emoji": "🔵"},
    {"key": "none", "label": "未達冷氣團", "max": None, "emoji": "🟢"},
]
DET_MODELS = [("ecmwf", "ecmwf_ifs025", "ECMWF IFS"), ("gfs", "gfs_seamless", "GFS"), ("icon", "icon_seamless", "ICON")]
ENS_MODELS = [("ecmwf", "ecmwf_ifs025", "ecmwf_ifs025_ensemble", "ECMWF ENS"),
              ("gefs", "ncep_gefs025", "ncep_gefs025", "GEFS"),
              ("icon", "icon_seamless", "icon_seamless_eps", "ICON-EPS")]
ENS_FAMILY = {"ecmwf": "ecmwf", "gefs": "gfs", "icon": "icon"}   # 偏差取自同機構決定性模式

# ERA5 氣候值與即時指數共用的網格（scripts/coldsurge_clim.json 同一組點）
SH_PTS = [(a, o) for a in (40, 50, 60) for o in (80, 90, 100, 110, 120)]          # Wu & Wang 2002 區域 40–60N, 80–120E
SIB_PTS = SH_PTS + [(40, 70), (50, 70), (60, 70)]                                   # Wang & Chen 2014 西伯利亞 40–60N, 70–120E
NP_PTS = [(40, 145), (40, 165), (40, -175)]                                         # 北太平洋 30–50N, 140E–170W（粗網格）
MC_PTS = [(-5, 115), (-5, 135), (-5, 155)]                                          # 海洋大陸 20S–10N, 110–160E（粗網格）
CLIM_PTS = SIB_PTS + NP_PTS + MC_PTS
CENTRE_PTS = [(a, o) for a in (35, 40, 45, 50, 55, 60) for o in range(75, 126, 5)]  # 高壓中心（5°）
# 乾冷／濕冷回測（2026-11-09 離線計算，程式：tests/calibrate_drywet.py）：ERA5 再分析（理想預報，非實際預報技術）
# 2010/11–2019/20 冬季、松山站日最低溫 ≤14°C 的 354 天；實況「濕冷」＝有雨且日溫差 ≤5°C（178 天）、「乾冷」＝無雨且日溫差 ≥7°C（63 天），其餘 113 天不列入。
# ERA5 無 700 hPa 濕度可用 → 回測只用 5 項（上游 2 m 濕度／10 m 風速代替 1000 hPa，臺北總雲量代替低雲）。
DRYWET_CALIB = {"period": "2010/11–2019/20 冬季", "days": 354, "wetDays": 178, "dryDays": 63,
                "wetDetected": 0.933, "dryDetected": 0.794, "crossErrors": 4, "noDtrWet": 0.904, "noDtrDry": 0.714,
                "summary": "ERA5 回測（2010–2020 冬季 354 個冷天）：明顯濕冷日 93% 判為濕冷／偏濕，明顯乾冷日 79% 判為乾冷／偏乾，乾濕顛倒 4/241；為再分析理想條件，實際預報準確度會較低"}

UP_BOX = [(25.5, 123.0), (25.5, 124.5), (27.0, 123.0), (27.0, 124.5)]               # Wang et al. 2024 上游方框 25–27.5N, 122.5–125E
MID_PTS = [(23.0, 116.0), (24.5, 118.5), (24.0, 121.0)]                             # 華南–臺灣 700 hPa 水氣
NE_PTS = [("基隆", 25.1333, 121.7405), ("宜蘭", 24.7640, 121.7565)]
UPSTREAM = [("ZMCK", "烏蘭巴托"), ("ZBAA", "北京"), ("ZSSS", "上海虹橋")]

WARN_LEVEL = {"低溫黃色燈號": "yellow", "低溫橙色燈號1": "orange", "低溫橙色燈號2": "orange", "低溫橙色燈號": "orange", "低溫紅色燈號": "red"}
WARN_RANK = {"yellow": 1, "orange": 2, "red": 3}
COUNTIES = ["基隆市", "臺北市", "新北市", "桃園市", "新竹市", "新竹縣", "苗栗縣", "臺中市", "彰化縣", "南投縣", "雲林縣",
            "嘉義市", "嘉義縣", "臺南市", "高雄市", "屏東縣", "宜蘭縣", "花蓮縣", "臺東縣", "澎湖縣", "金門縣", "連江縣"]

ATTR_OM = "模式資料：Open-Meteo（CC BY 4.0）；各模式版權屬原機構"


def log(*a):
    print("[coldsurge]", *a, file=sys.stderr, flush=True)


def now_tpe():
    return datetime.now(TPE)


def iso(dt):
    return dt.astimezone(TPE).isoformat(timespec="minutes") if dt else None


def parse_t(s):
    if not s:
        return None
    try:
        s = str(s).replace("Z", "+00:00")
        d = datetime.fromisoformat(s)
        return d if d.tzinfo else d.replace(tzinfo=TPE)
    except Exception:
        return None


def num(v, lo=-90.0):
    """字串/數字 → float；None、空白、'X'、'T'、-99/-990/-999 等哨兵值、布林、NaN → None（絕不變 0）"""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        f = float(v)
    else:
        s = str(v).strip()
        if not s or s.upper() in ("X", "T", "NULL", "NAN", "-", "--"):
            return None
        try:
            f = float(s)
        except ValueError:
            return None
    if math.isnan(f) or math.isinf(f) or f <= lo:
        return None
    return f


def r1(v, nd=1):
    return None if v is None else round(v, nd)


def mean(vs):
    vs = [v for v in vs if v is not None]
    return sum(vs) / len(vs) if vs else None


def quantile(vs, q):
    vs = sorted(v for v in vs if v is not None)
    if not vs:
        return None
    if len(vs) == 1:
        return vs[0]
    p = (len(vs) - 1) * q
    i = int(math.floor(p))
    j = min(i + 1, len(vs) - 1)
    return vs[i] + (vs[j] - vs[i]) * (p - i)


def level_of(t):
    if t is None:
        return None
    for L in LEVELS:
        if L["max"] is not None and t <= L["max"]:
            return L["key"]
    return "none"


# ---------------------------------------------------------------- HTTP
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
                time.sleep(3 + 4 * i)
    raise last


def get_json(url, **kw):
    return json.loads(http_get(url, **kw).decode("utf-8"))


def mask(e):
    s = str(e)
    key = os.environ.get("CWA_API_KEY") or ""
    return s.replace(key, "***") if key else s


def om_url(base, lats, lons, **params):
    q = {"latitude": ",".join(str(a) for a in lats), "longitude": ",".join(str(o) for o in lons),
         "timezone": "Asia/Taipei", "wind_speed_unit": "ms"}
    q.update(params)
    return base + "?" + urllib.parse.urlencode(q, safe=",")


def as_list(d):
    return d if isinstance(d, list) else [d]


# ---------------------------------------------------------------- 時間序列工具
def day_window_min(times, vals, date):
    """CWA 日最低溫視窗：當日 01:00 至次日 00:00（24 筆逐時都要有值）"""
    d0 = datetime.strptime(date, "%Y-%m-%d")
    keys = [(d0 + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M") for h in range(1, 25)]
    idx = {t: i for i, t in enumerate(times)}
    out = []
    for k in keys:
        i = idx.get(k)
        if i is None or vals[i] is None:
            return None
        out.append(vals[i])
    return min(out)


def day_window_stat(times, vals, date, fn="mean", need=20):
    d0 = datetime.strptime(date, "%Y-%m-%d")
    keys = [(d0 + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M") for h in range(1, 25)]
    idx = {t: i for i, t in enumerate(times)}
    xs = [vals[idx[k]] for k in keys if k in idx and vals[idx[k]] is not None]
    if len(xs) < need:
        return None
    if fn == "mean":
        return sum(xs) / len(xs)
    if fn == "sum":
        return sum(xs) if len(xs) == 24 else None
    if fn == "max":
        return max(xs)
    if fn == "min":
        return min(xs)
    return None


def dates_in(times):
    ds = sorted(set(t[:10] for t in times))
    return ds


def daily_mean_calendar(times, vals, need=20):
    """自然日（00–23 時）平均：用於海平面氣壓指數"""
    acc = {}
    for t, v in zip(times, vals):
        if v is None:
            continue
        acc.setdefault(t[:10], []).append(v)
    return {d: sum(x) / len(x) for d, x in acc.items() if len(x) >= need}


# ---------------------------------------------------------------- 1) 冷空氣等級
def ens_members(hourly, var="temperature_2m"):
    """Open-Meteo 系集鍵名 → {模式後綴: [成員序列…]}"""
    out = {}
    for k, v in hourly.items():
        if not k.startswith(var + "_") or k == "time":
            continue
        rest = k[len(var) + 1:]
        if rest.startswith("member"):
            p = rest.find("_")
            if p < 0:
                continue
            model = rest[p + 1:]
        else:
            model = rest
        out.setdefault(model, []).append(v)
    return out


def probs(vals):
    n = len(vals)
    if n == 0:
        return None
    return {"le14": round(100 * sum(1 for x in vals if x <= 14.0) / n),
            "le12": round(100 * sum(1 for x in vals if x <= 12.0) / n),
            "le10": round(100 * sum(1 for x in vals if x <= 10.0) / n)}


def summarize(vals):
    vals = [v for v in vals if v is not None]
    if len(vals) < 2:
        return None
    return {"n": len(vals), "p10": r1(quantile(vals, 0.1)), "p50": r1(quantile(vals, 0.5)),
            "p90": r1(quantile(vals, 0.9)), "prob": probs(vals)}


def compute_bias(prev_hourly, obs_daily, min_n=7):
    """prev_hourly: {'time':[], fam:[…前一日預報逐時…]}；obs_daily: {date: tmin}"""
    out = {}
    times = prev_hourly.get("time") or []
    for fam, vals in prev_hourly.items():
        if fam == "time":
            continue
        diffs = []
        for d, ob in sorted(obs_daily.items()):
            if ob is None:
                continue
            fc = day_window_min(times, vals, d)
            if fc is not None:
                diffs.append(fc - ob)
        b = mean(diffs)
        out[fam] = {"bias": r1(b, 2), "n": len(diffs), "mae": r1(mean([abs(x) for x in diffs]), 2),
                    "applied": bool(b is not None and len(diffs) >= min_n)}
    return out


def build_level(det, ens, bias, obs_daily, today, ndays=15):
    """det: {fam: (times, vals)}; ens: {fam: (times, [member vals…])}"""
    days = []
    d0 = datetime.strptime(today, "%Y-%m-%d")
    for lead in range(0, ndays + 1):
        date = (d0 + timedelta(days=lead)).strftime("%Y-%m-%d")
        row = {"date": date, "lead": lead, "ref": lead > 7, "det": {}, "ens": {}}
        for fam, (times, vals) in det.items():
            raw = day_window_min(times, vals, date)
            b = bias.get(fam) or {}
            adj = (raw - b["bias"]) if (raw is not None and b.get("applied")) else raw
            row["det"][fam] = {"raw": r1(raw), "adj": r1(adj)} if raw is not None else None
        pooled = []
        for fam, (times, members) in ens.items():
            b = bias.get(ENS_FAMILY[fam]) or {}
            off = b["bias"] if b.get("applied") else 0.0
            mins = []
            for m in members:
                t = day_window_min(times, m, date)
                if t is not None:
                    mins.append(t - off)
            s = summarize(mins)
            row["ens"][fam] = s
            pooled.extend(mins)
        row["pooled"] = summarize(pooled)
        p50 = row["pooled"]["p50"] if row["pooled"] else None
        if p50 is None:
            dets = [v["adj"] for v in row["det"].values() if v]
            p50 = r1(quantile(dets, 0.5)) if len(dets) >= 2 else None
            row["basis"] = "det-median" if p50 is not None else None
        else:
            row["basis"] = "ensemble"
        row["level"] = level_of(p50)
        row["tmin"] = p50
        row["obs"] = obs_daily.get(date)
        if row["pooled"] is None and all(v is None for v in row["det"].values()):
            continue
        days.append(row)
    peak = None
    for r in days[1:]:
        if r["tmin"] is not None and (peak is None or r["tmin"] < peak["tmin"]):
            peak = {"date": r["date"], "tmin": r["tmin"], "level": r["level"], "ref": r["ref"]}
    return days, peak


# ---------------------------------------------------------------- 2) 低溫特報
def info_list(doc):
    rec = (doc or {}).get("records") or {}
    for k in ("info",):
        if isinstance(rec.get(k), list):
            return rec[k]
        if isinstance(rec.get(k), dict):
            return [rec[k]]
    ds = rec.get("dataset") or {}
    if isinstance(ds, dict) and ds.get("info"):
        return as_list(ds["info"])
    if rec.get("record"):
        return as_list(rec["record"])
    return []


def param_of(info, name):
    ps = info.get("parameter") or []
    for p in as_list(ps):
        if isinstance(p, dict) and (p.get("valueName") == name or p.get("parameterName") == name):
            return p.get("value") or p.get("parameterValue")
    return None


def county_of(s):
    s = str(s or "").replace("台", "臺")
    for c in COUNTIES:
        if s.startswith(c) or c in s:
            return c
    return None


def parse_cold_warning(doc, now):
    """W-C0033-004 → {counties:[{name,level,towns}]}；格式不明 → raise ValueError"""
    if not isinstance(doc, dict) or str(doc.get("success", "true")).lower() not in ("true", "1"):
        raise ValueError("回應格式不符")
    infos = info_list(doc)
    if not isinstance((doc.get("records") or None), dict):
        raise ValueError("缺少 records")
    agg = {}
    issued = None
    for info in infos:
        if not isinstance(info, dict):
            continue
        lvl_txt = param_of(info, "severity_level") or ""
        lvl = WARN_LEVEL.get(str(lvl_txt).strip())
        if not lvl:
            col = str(param_of(info, "alert_color") or "")
            lvl = {"黃色": "yellow", "橙色": "orange", "紅色": "red"}.get(col)
        exp = parse_t(info.get("expires"))
        if exp and exp < now:
            continue
        eff = parse_t(info.get("effective") or info.get("onset"))
        if eff and (issued is None or eff > issued):
            issued = eff
        if not lvl:
            continue
        for a in as_list(info.get("area") or []):
            if not isinstance(a, dict):
                continue
            name = a.get("areaDesc") or a.get("AreaDesc") or a.get("locationName") or ""
            c = county_of(name)
            if not c:
                continue
            e = agg.setdefault(c, {"name": c, "level": lvl, "towns": []})
            if WARN_RANK[lvl] > WARN_RANK[e["level"]]:
                e["level"] = lvl
            town = str(name).replace("台", "臺").replace(c, "")
            if town and town not in e["towns"]:
                e["towns"].append(town)
    rows = sorted(agg.values(), key=lambda e: (-WARN_RANK[e["level"]], COUNTIES.index(e["name"])))
    return {"counties": rows, "issued": iso(issued)}


def parse_w1_cold(doc):
    """備援：W-C0033-001（S3）縣市目前特報中 phenomena 含「低溫」的縣市（不含燈號顏色）"""
    locs = (((doc or {}).get("cwaopendata") or {}).get("dataset") or {}).get("location")
    if locs is None:
        raise ValueError("W-C0033-001 格式不符")
    out = []
    for L in as_list(locs):
        hz = ((L.get("hazardConditions") or {}).get("hazards")) or []
        for h in as_list(hz):
            ph = ((h or {}).get("info") or {}).get("phenomena") or ""
            if "低溫" in ph:
                c = county_of(L.get("locationName"))
                if c and c not in out:
                    out.append(c)
    return out


def model_advisory(day_rows_hourly):
    """臺北點模式推估（非官方）：黃 Tmin≤10；橙 Tmin≤6 或（≤10 且該日最高≤12）；紅 24 小時最高≤6"""
    out = []
    for r in day_rows_hourly:
        tmin, tmax = r.get("tmin"), r.get("tmax")
        if tmin is None:
            out.append({"date": r["date"], "level": None})
            continue
        lv = "none"
        if tmax is not None and tmax <= 6:
            lv = "red"
        elif tmin <= 6 or (tmin <= 10 and tmax is not None and tmax <= 12):
            lv = "orange"
        elif tmin <= 10:
            lv = "yellow"
        out.append({"date": r["date"], "level": lv, "tmin": r1(tmin), "tmax": r1(tmax), "ref": r.get("ref", False)})
    return out


# ---------------------------------------------------------------- 3) 4) 西伯利亞高壓／冬季季風
def region_daily(resp_list, pts, sel):
    """多點回應 → 選定點的自然日平均區域平均 {date: value}（缺任一點就不算）"""
    per = []
    pos = {(round(p[0], 2), round(p[1], 2)): i for i, p in enumerate(pts)}
    for p in sel:
        i = pos[(round(p[0], 2), round(p[1], 2))]
        h = resp_list[i].get("hourly") or {}
        per.append(daily_mean_calendar(h.get("time") or [], h.get("pressure_msl") or []))
    dates = set(per[0].keys())
    for d in per[1:]:
        dates &= set(d.keys())
    return {d: sum(x[d] for x in per) / len(per) for d in sorted(dates)}


def clim_lookup(clim, region, date):
    md = date[5:]
    v = ((clim or {}).get("regions") or {}).get(region, {}).get(md)
    return (v[0], v[1]) if v else (None, None)


def build_sh(resp, centre, clim, model_label):
    sh = region_daily(resp, CLIM_PTS, SH_PTS)
    t, idx, anom, z, cmax, clm = [], [], [], [], [], []
    cent_daily = []
    if centre:
        for p in centre:
            h = p.get("hourly") or {}
            acc = {}
            for tt, v in zip(h.get("time") or [], h.get("pressure_msl") or []):
                if v is not None:
                    acc.setdefault(tt[:10], []).append(v)
            cent_daily.append({d: max(x) for d, x in acc.items() if len(x) >= 20})
    for d in sorted(sh):
        m, s = clim_lookup(clim, "sh", d)
        t.append(d)
        idx.append(r1(sh[d]))
        clm.append(r1(m))
        anom.append(r1(sh[d] - m) if m is not None else None)
        z.append(r1((sh[d] - m) / s, 2) if (m is not None and s) else None)
        cm = [c[d] for c in cent_daily if d in c]
        cmax.append(r1(max(cm)) if len(cm) == len(cent_daily) and cm else None)
    return {"label": model_label, "t": t, "index": idx, "clim": clm, "anom": anom, "z": z, "centreMax": cmax}


def build_eawm(resp, clim, model_label):
    regs = {"sib": region_daily(resp, CLIM_PTS, SIB_PTS), "np": region_daily(resp, CLIM_PTS, NP_PTS),
            "mc": region_daily(resp, CLIM_PTS, MC_PTS)}
    dates = sorted(set(regs["sib"]) & set(regs["np"]) & set(regs["mc"]))
    t, val = [], []
    for d in dates:
        zs = {}
        for k in regs:
            m, s = clim_lookup(clim, k, d)
            zs[k] = (regs[k][d] - m) / s if (m is not None and s) else None
        t.append(d)
        val.append(r1((2 * zs["sib"] - zs["np"] - zs["mc"]) / 2, 2) if None not in zs.values() else None)
    return {"label": model_label, "t": t, "index": val}


# ---------------------------------------------------------------- 5) 寒潮爆發
def metar_changes(rows, now):
    """AWC METAR JSON（同站多筆）→ 最新值與 24/48 小時同時刻差（±90 分鐘內最接近者）"""
    pts = []
    for r in rows:
        tt = parse_t(r.get("reportTime") or r.get("obsTime"))
        if isinstance(r.get("obsTime"), (int, float)):
            tt = datetime.fromtimestamp(r["obsTime"], timezone.utc)
        T = num(r.get("temp"), lo=-80)
        P = num(r.get("altim"), lo=800)
        if tt is not None:
            pts.append((tt, T, P))
    pts.sort(key=lambda x: x[0])
    if not pts:
        return None
    last = None
    for p in reversed(pts):
        if p[1] is not None:
            last = p
            break
    if last is None:
        return None

    def near(hours):
        target = last[0] - timedelta(hours=hours)
        best = None
        for p in pts:
            dt = abs((p[0] - target).total_seconds())
            if dt <= 5400 and p[1] is not None and (best is None or dt < best[0]):
                best = (dt, p)
        return best[1] if best else None

    out = {"time": iso(last[0]), "temp": last[1], "altim": r1(last[2]),
           "ageH": r1((now - last[0]).total_seconds() / 3600)}
    for h in (24, 48):
        p = near(h)
        out["dT%d" % h] = r1(last[1] - p[1]) if p else None
        out["dP%d" % h] = r1(last[2] - p[2]) if (p and last[2] is not None and p[2] is not None) else None
    # GB/T 21987-2017 寒潮：24h 降 ≥8℃、48h ≥10℃（此處為同時刻氣溫差，非日最低溫；僅作參考訊號）
    sig = None
    if (out["dT24"] is not None and out["dT24"] <= -8) or (out["dT48"] is not None and out["dT48"] <= -10):
        sig = "cold_wave_drop"
    out["signal"] = sig
    return out


# ---------------------------------------------------------------- 6) 乾冷／濕冷
def drywet_score(c):
    """c: rhUp, wsUp, rh700, pNE, lowCloud, dtr；回傳 (score, n_components, parts)"""
    parts = {}

    def add(k, v):
        parts[k] = v

    if c.get("rhUp") is not None:
        x = c["rhUp"]
        add("rhUp", 2 if x >= 80 else 1 if x >= 70 else -2 if x <= 60 else 0)
    if c.get("wsUp") is not None:
        x = c["wsUp"]
        add("wsUp", 1 if x >= 8 else -1 if x <= 4 else 0)
    if c.get("rh700") is not None:
        x = c["rh700"]
        add("rh700", 1 if x >= 70 else -1 if x <= 40 else 0)
    if c.get("pNE") is not None:
        x = c["pNE"]
        add("pNE", 2 if x >= 5 else 1 if x >= 1 else -1 if x < 0.2 else 0)
    if c.get("lowCloud") is not None:
        x = c["lowCloud"]
        add("lowCloud", 1 if x >= 70 else -1 if x <= 30 else 0)
    if c.get("dtr") is not None:
        x = c["dtr"]
        add("dtr", 1 if x <= 4 else -1 if x >= 8 else 0)
    if len(parts) < 4:
        return None, len(parts), parts
    return sum(parts.values()), len(parts), parts


def drywet_class(score):
    if score is None:
        return None
    if score >= 4:
        return "wet"
    if score >= 2:
        return "wetish"
    if score <= -4:
        return "dry"
    if score <= -2:
        return "dryish"
    return "mixed"


def build_drywet(pts_resp, taipei_hourly, dates):
    """pts_resp：UP_BOX(4)+MID_PTS(3)+NE_PTS(2) 多點回應；taipei_hourly：同模式臺北逐時（溫度、低雲）"""
    nU, nM = len(UP_BOX), len(MID_PTS)
    rows = []
    tt = taipei_hourly.get("time") or []
    for d in dates:
        rh = mean([day_window_stat(p["hourly"]["time"], p["hourly"].get("relative_humidity_1000hPa") or [], d)
                   for p in pts_resp[:nU]])
        ws = mean([day_window_stat(p["hourly"]["time"], p["hourly"].get("wind_speed_1000hPa") or [], d)
                   for p in pts_resp[:nU]])
        r7 = mean([day_window_stat(p["hourly"]["time"], p["hourly"].get("relative_humidity_700hPa") or [], d)
                   for p in pts_resp[nU:nU + nM]])
        pn = [day_window_stat(p["hourly"]["time"], p["hourly"].get("precipitation") or [], d, "sum")
              for p in pts_resp[nU + nM:]]
        pne = max(pn) if pn and all(x is not None for x in pn) else None
        lc = day_window_stat(tt, taipei_hourly.get("cloud_cover_low") or [], d)
        tx = day_window_stat(tt, taipei_hourly.get("temperature_2m") or [], d, "max", need=24)
        tn = day_window_stat(tt, taipei_hourly.get("temperature_2m") or [], d, "min", need=24)
        comp = {"rhUp": r1(rh, 0), "wsUp": r1(ws), "rh700": r1(r7, 0), "pNE": r1(pne), "lowCloud": r1(lc, 0),
                "dtr": r1(tx - tn) if (tx is not None and tn is not None) else None}
        sc, n, parts = drywet_score(comp)
        rows.append({"date": d, "score": sc, "class": drywet_class(sc), "n": n, "comp": comp, "parts": parts})
    return rows


# ---------------------------------------------------------------- 7) AO
def parse_ao_obs(text, keep=90):
    rows = []
    for line in text.splitlines()[1:]:
        p = line.strip().split(",")
        if len(p) < 4:
            continue
        try:
            d = "%04d-%02d-%02d" % (int(p[0]), int(p[1]), int(p[2]))
        except ValueError:
            continue
        rows.append((d, r1(num(p[3], lo=-99), 2)))
    rows = rows[-keep:]
    return {"t": [r[0] for r in rows], "v": [r[1] for r in rows]}


def parse_ao_fcst(text):
    hdr = None
    by_init = {}
    for line in text.splitlines():
        p = [x.strip() for x in line.split(",")]
        if hdr is None:
            hdr = p
            continue
        if len(p) != len(hdr):
            continue
        r = dict(zip(hdr, p))
        v = num(r.get("ao_index"), lo=-99)
        if v is None:
            continue
        by_init.setdefault(r.get("time"), {}).setdefault(r.get("valid_time"), []).append(v)
    if not by_init:
        return None
    init = max(by_init)
    vt = sorted(by_init[init])
    out = {"init": init, "t": [], "p10": [], "p50": [], "p90": [], "n": 0}
    for d in vt:
        xs = by_init[init][d]
        out["n"] = max(out["n"], len(xs))
        out["t"].append(d)
        out["p10"].append(r1(quantile(xs, .1), 2))
        out["p50"].append(r1(quantile(xs, .5), 2))
        out["p90"].append(r1(quantile(xs, .9), 2))
    return out


# ---------------------------------------------------------------- 觀測（氣象署 S3）
def cb24_station(doc, sid):
    locs = (((((doc or {}).get("cwaopendata") or {}).get("resources") or {}).get("resource") or {}).get("data") or {}) \
        .get("surfaceObs", {}).get("location") or []
    for L in as_list(locs):
        if (L.get("station") or {}).get("StationID") == sid:
            return L
    return None


def cb24_daily_tmin(doc, sid):
    L = cb24_station(doc, sid)
    if not L:
        raise ValueError("C-B0024-001 找不到 %s" % sid)
    daily = (((L.get("stationObsStatistics") or {}).get("AirTemperature") or {}).get("daily")) or []
    out = {}
    for r in as_list(daily):
        d = r.get("Date")
        if d:
            out[d] = {"tmin": num(r.get("Minimum")), "tmax": num(r.get("Maximum"))}
    return out


def oa3_station(doc, sid):
    st = (((doc or {}).get("cwaopendata") or {}).get("dataset") or {}).get("Station") or []
    for s in as_list(st):
        if s.get("StationId") == sid:
            return s
    return None


def oa3_now(doc):
    out = {}
    for sid, name in (("466920", "臺北"), ("466950", "彭佳嶼"), ("466940", "基隆"), ("467080", "宜蘭")):
        s = oa3_station(doc, sid)
        if not s:
            out[sid] = None
            continue
        w = s.get("WeatherElement") or {}
        de = w.get("DailyExtreme") or {}
        lo = ((de.get("DailyLow") or {}).get("TemperatureInfo") or {})
        out[sid] = {"name": name, "time": (s.get("ObsTime") or {}).get("DateTime"),
                    "temp": num(w.get("AirTemperature")), "rh": num(w.get("RelativeHumidity"), lo=-1),
                    "ws": num(w.get("WindSpeed"), lo=-1), "wd": num(w.get("WindDirection"), lo=-1),
                    "precipToday": num((w.get("Now") or {}).get("Precipitation"), lo=-1),
                    "lowToday": num(lo.get("AirTemperature")), "weather": w.get("Weather") or None}
    return out


# ---------------------------------------------------------------- 組裝
def load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def age_h(ts, now):
    t = parse_t(ts)
    return None if t is None else (now - t).total_seconds() / 3600


def keep_prev(prev, key, reason, now):
    b = (prev or {}).get(key)
    if isinstance(b, dict) and b.get("state") in ("ok", "partial", "cached"):
        b = dict(b)
        b["state"] = "cached"
        b["cacheReason"] = reason
        b["ageHours"] = r1(age_h(b.get("updatedAt"), now))
        return b
    return {"state": "error", "reason": reason}


def fetch_heavy(now, clim, F):
    """F：fetch 函式表（測試可替換）。回傳 {block: dict}；個別失敗會 raise 到 block 層由呼叫端處理"""
    out = {}
    today = now.strftime("%Y-%m-%d")
    errors = {}

    # 觀測：臺北 30 天日統計（S3）
    obs_daily = {}
    try:
        cb = F["s3"]("Climate/C-B0024-001.json")
        tp = cb24_daily_tmin(cb, TAIPEI["id"])
        obs_daily = {d: v["tmin"] for d, v in tp.items() if v["tmin"] is not None}
        out["obsTaipei"] = {d: v for d, v in sorted(tp.items())}
    except Exception as e:
        errors["obs"] = str(e)

    # 決定性：臺北
    det, det_hourly = {}, {}
    for fam, mid, lab in DET_MODELS:
        try:
            d = F["om"](om_url(OM_FC, [TAIPEI["lat"]], [TAIPEI["lon"]], models=mid, past_days=7, forecast_days=16,
                               hourly="temperature_2m,temperature_850hPa,cloud_cover_low"))
            h = d["hourly"]
            det[fam] = (h["time"], h["temperature_2m"])
            det_hourly[fam] = h
        except Exception as e:
            errors["det_" + fam] = str(e)
    # 系集
    ens = {}
    try:
        d = F["om"](om_url(OM_ENS, [TAIPEI["lat"]], [TAIPEI["lon"]], models=",".join(m[1] for m in ENS_MODELS),
                           forecast_days=16, hourly="temperature_2m"))
        mem = ens_members(d["hourly"])
        for fam, mid, suf, lab in ENS_MODELS:
            if mem.get(suf):
                ens[fam] = (d["hourly"]["time"], mem[suf])
    except Exception as e:
        errors["ens"] = str(e)
    # 偏差（近 30 日：前一日預報 vs 臺北站）
    bias = {}
    try:
        d = F["om"](om_url(OM_PREV, [TAIPEI["lat"]], [TAIPEI["lon"]], models=",".join(m[1] for m in DET_MODELS),
                           past_days=31, forecast_days=1, hourly="temperature_2m_previous_day1"))
        h = d["hourly"]
        ph = {"time": h["time"]}
        for fam, mid, lab in DET_MODELS:
            k = "temperature_2m_previous_day1_" + mid
            if k in h:
                ph[fam] = h[k]
        past = {k: v for k, v in obs_daily.items() if k < today}
        bias = compute_bias(ph, past) if past else {}
    except Exception as e:
        errors["bias"] = str(e)

    if det or ens:
        days, peak = build_level(det, ens, bias, obs_daily, today)
        obs_rows = [{"date": d, "tmin": r1(v), "level": level_of(v)} for d, v in sorted(obs_daily.items())][-10:]
        out["level"] = {"state": "ok" if (len(ens) == len(ENS_MODELS) and len(det) == len(DET_MODELS)) else "partial", "updatedAt": iso(now),
                        "station": TAIPEI, "days": days, "peak": peak, "obs": obs_rows,
                        "bias": {"method": "近 30 日 Open-Meteo 前一日預報（Previous Runs）臺北點日最低溫 − 氣象署臺北站實測；系集套用同機構決定性模式偏差；n≥7 才套用",
                                 "models": bias},
                        "models": {"det": [m[2] for m in DET_MODELS if m[0] in det],
                                   "ens": [{"id": m[0], "label": m[3], "members": len(ens[m[0]][1])} for m in ENS_MODELS if m[0] in ens]},
                        "errors": {k: v for k, v in errors.items() if k.startswith(("det", "ens", "bias", "obs"))} or None}
        # 模式推估低溫燈號（臺北點）
        mrows = []
        for r in days:
            tx = []
            for fam, (times, vals) in det.items():
                x = day_window_stat(times, vals, r["date"], "max", need=24)
                if x is not None:
                    b = bias.get(fam) or {}
                    tx.append(x - (b["bias"] if b.get("applied") else 0))
            mrows.append({"date": r["date"], "tmin": r["tmin"], "tmax": quantile(tx, .5) if len(tx) >= 2 else None, "ref": r["ref"]})
        out["modelAdvisory"] = model_advisory(mrows)
        # 850 hPa（臺北點）
        t850 = {}
        for fam, h in det_hourly.items():
            tt, vv = h["time"], h.get("temperature_850hPa") or []
            sel = [(t, r1(v)) for t, v in zip(tt, vv) if t[11:13] in ("02", "08", "14", "20")]
            t850[fam] = {"t": [s[0] for s in sel], "v": [s[1] for s in sel]}
        out["t850"] = t850
        # 臺灣 48 小時日最低溫變化：觀測（氣象署臺北站）＋預報（系集中位數；前 2 日用實測或預報）
        drops = []
        od = sorted(obs_daily.items())
        for i in range(2, len(od)):
            d_i = datetime.strptime(od[i][0], "%Y-%m-%d")
            if (d_i - datetime.strptime(od[i - 2][0], "%Y-%m-%d")).days == 2:
                drops.append({"date": od[i][0], "kind": "obs", "d48": r1(od[i][1] - od[i - 2][1])})
        fcmap = {r["date"]: r["tmin"] for r in days}
        for r in days:
            d, v = r["date"], r["tmin"]
            if d < today or v is None:
                continue
            p2 = (datetime.strptime(d, "%Y-%m-%d") - timedelta(days=2)).strftime("%Y-%m-%d")
            base = obs_daily.get(p2) if p2 < today else fcmap.get(p2)
            if base is not None:
                drops.append({"date": d, "kind": "fcst", "d48": r1(v - base), "ref": r["ref"]})
        out["drop48"] = drops[-20:]
    else:
        out["level"] = None

    # 海平面氣壓網格：西伯利亞高壓與冬季季風
    sh, ea = {}, {}
    for fam, mid, lab in DET_MODELS[:2]:
        try:
            lats, lons = [p[0] for p in CLIM_PTS], [p[1] for p in CLIM_PTS]
            resp = as_list(F["om"](om_url(OM_FC, lats, lons, models=mid, past_days=5, forecast_days=16, hourly="pressure_msl")))
            cen = None
            try:
                cen = as_list(F["om"](om_url(OM_FC, [p[0] for p in CENTRE_PTS], [p[1] for p in CENTRE_PTS], models=mid,
                                             past_days=5, forecast_days=16, hourly="pressure_msl")))
            except Exception as e:
                errors["centre_" + fam] = str(e)
            sh[fam] = build_sh(resp, cen, clim, lab)
            ea[fam] = build_eawm(resp, clim, lab)
        except Exception as e:
            errors["slp_" + fam] = str(e)
    if sh:
        out["siberianHigh"] = {"state": "ok" if len(sh) == 2 else "partial", "updatedAt": iso(now), "models": sh,
                               "region": "40–60°N, 80–120°E（10° 網格 15 點）", "centreGrid": "35–60°N, 75–125°E（5° 網格日最大值）",
                               "clim": {"period": (clim or {}).get("period"), "source": (clim or {}).get("source"),
                                        "window": (clim or {}).get("window")} if clim else None}
    if ea:
        out["eawm"] = {"state": "ok" if len(ea) == 2 else "partial", "updatedAt": iso(now), "models": ea,
                       "name": "Wang & Chen (2014) 冬季季風指數（日尺度、粗網格改寫）",
                       "formula": "(2·Z西伯利亞 − Z北太平洋 − Z海洋大陸)/2；Z＝區域平均海平面氣壓相對 ERA5 1991–2020 同期的標準化距平"}

    # 乾冷／濕冷
    dw = {}
    pts = UP_BOX + MID_PTS + [(p[1], p[2]) for p in NE_PTS]
    for fam, mid, lab in DET_MODELS[:2]:
        if fam not in det_hourly:
            continue
        try:
            resp = as_list(F["om"](om_url(OM_FC, [p[0] for p in pts], [p[1] for p in pts], models=mid, forecast_days=16,
                                          hourly="relative_humidity_1000hPa,wind_speed_1000hPa,relative_humidity_700hPa,precipitation")))
            dates = [r["date"] for r in out["level"]["days"]] if out.get("level") else []
            dw[fam] = {"label": lab, "days": build_drywet(resp, det_hourly[fam], dates)}
        except Exception as e:
            errors["dw_" + fam] = str(e)
    if dw:
        out["dryWet"] = {"state": "ok" if len(dw) == 2 else "partial", "updatedAt": iso(now), "models": dw, "calibration": DRYWET_CALIB}

    # AO
    try:
        obs = parse_ao_obs(F["text"](CPC + "norm.daily.ao.cdas.z1000.19500101_current.csv"))
        fc = None
        try:
            fc = parse_ao_fcst(F["text"](CPC + "norm.daily.ao.gefs.z1000.120days.csv"))
        except Exception as e:
            errors["ao_fcst"] = str(e)
        out["ao"] = {"state": "ok" if fc else "partial", "updatedAt": iso(now), "obs": obs, "fcst": fc}
    except Exception as e:
        errors["ao"] = str(e)
    out["_errors"] = errors
    return out


def fetch_light(now, F):
    out, errors = {}, {}
    # 低溫特報（官方）
    key = os.environ.get("CWA_API_KEY") or ""
    off = {"state": "unavailable", "source": None, "counties": [], "issued": None}
    if key:
        try:
            doc = F["cwa"]("W-C0033-004", key)
            p = parse_cold_warning(doc, now)
            off = {"state": "ok", "source": "W-C0033-004", "counties": p["counties"], "issued": p["issued"]}
        except Exception as e:
            errors["warn004"] = mask(e)
    else:
        errors["warn004"] = "未設定 CWA_API_KEY"
    if off["state"] != "ok":
        try:
            w1 = F["s3"]("Warning/W-C0033-001.json")
            cs = parse_w1_cold(w1)
            off = {"state": "partial", "source": "W-C0033-001",
                   "counties": [{"name": c, "level": None, "towns": []} for c in cs], "issued": None,
                   "reason": "燈號資料暫無法取得，僅列出目前有低溫特報的縣市"}
        except Exception as e:
            errors["warn001"] = str(e)
    off["updatedAt"] = iso(now)
    out["warning"] = off
    # 目前觀測
    try:
        out["now"] = {"state": "ok", "updatedAt": iso(now), "stations": oa3_now(F["s3"]("Observation/O-A0003-001.json"))}
    except Exception as e:
        errors["now"] = str(e)
    # 上游 METAR
    try:
        ids = ",".join([u[0] for u in UPSTREAM])
        rows = F["json"](AWC + "?" + urllib.parse.urlencode({"ids": ids, "format": "json", "hours": 54}, safe=","))
        ups = []
        for icao, name in UPSTREAM:
            ch = metar_changes([r for r in rows if r.get("icaoId") == icao], now)
            ups.append({"icao": icao, "name": name, "state": "ok" if ch else "error", "data": ch})
        out["upstream"] = {"state": "ok" if all(u["state"] == "ok" for u in ups) else "partial", "updatedAt": iso(now), "stations": ups}
    except Exception as e:
        errors["metar"] = str(e)
    out["_errors"] = errors
    return out


def default_fetchers():
    key_cache = {}

    def s3(path):
        if path not in key_cache:
            key_cache[path] = json.loads(http_get(S3 + path, timeout=90).decode("utf-8"))
        return key_cache[path]

    def cwa(did, key):
        url = CWA_API + did + "?" + urllib.parse.urlencode({"Authorization": key, "format": "JSON"})
        return json.loads(http_get(url, timeout=40).decode("utf-8"))

    def om(url):
        d = json.loads(http_get(url, timeout=90).decode("utf-8"))
        if isinstance(d, dict) and d.get("error"):
            raise RuntimeError("Open-Meteo：" + str(d.get("reason")))
        return d

    return {"s3": s3, "cwa": cwa, "om": om, "json": lambda u: get_json(u, timeout=40),
            "text": lambda u: http_get(u, timeout=60).decode("utf-8")}


def assemble(prev, heavy, light, now, heavy_ran):
    doc = {"schema": SCHEMA, "generatedAt": iso(now), "timezone": "Asia/Taipei (+08:00)",
           "levels": LEVELS, "attribution": ATTR_OM,
           "footer": ["資料來源：中央氣象署開放資料；Open-Meteo（CC BY 4.0，ECMWF／NOAA GFS·GEFS／DWD ICON）；ERA5（Copernicus）；NOAA AviationWeather；NOAA CPC",
                      "資料僅供參考，實際以中央氣象署最新發布為準。"]}
    blocks = ["level", "modelAdvisory", "t850", "drop48", "obsTaipei", "siberianHigh", "eawm", "dryWet", "ao"]
    if heavy_ran:
        errs = heavy.get("_errors") or {}
        for b in blocks:
            v = heavy.get(b)
            if v is None:
                v = keep_prev(prev, b, "本次抓取失敗：" + "; ".join("%s=%s" % kv for kv in errs.items())[:300], now) \
                    if b in ("level", "siberianHigh", "eawm", "dryWet", "ao") else (prev or {}).get(b)
            doc[b] = v
        doc["heavyAt"] = iso(now) if heavy.get("level") else (prev or {}).get("heavyAt")
        doc["heavyErrors"] = errs or None
    else:
        for b in blocks:
            doc[b] = (prev or {}).get(b)
        doc["heavyAt"] = (prev or {}).get("heavyAt")
        doc["heavyErrors"] = (prev or {}).get("heavyErrors")
    for b in ("warning", "now", "upstream"):
        v = light.get(b)
        if v is None:
            v = keep_prev(prev, b, "本次抓取失敗", now) if b != "warning" else {"state": "unavailable", "counties": [], "updatedAt": iso(now)}
        doc[b] = v
    doc["lightErrors"] = light.get("_errors") or None
    doc["warning"]["model"] = doc.get("modelAdvisory")
    return doc


def need_heavy(prev, now, force):
    if force or not prev or prev.get("schema") != SCHEMA:
        return True
    a = age_h(prev.get("heavyAt"), now)
    return a is None or a >= HEAVY_MAX_AGE_H


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="site")
    ap.add_argument("--prev-dir", default=None)
    ap.add_argument("--clim", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "coldsurge_clim.json"))
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    now = now_tpe()
    outp = os.path.join(a.out, "data", "coldsurge.json")
    prev = None
    for cand in ([os.path.join(a.prev_dir, "data", "coldsurge.json")] if a.prev_dir else []) + [outp]:
        prev = load_json(cand)
        if prev:
            break
    if not prev:
        try:
            prev = get_json((os.environ.get("PAGES_BASE") or DEFAULT_PAGES).rstrip("/") + "/data/coldsurge.json", timeout=20, retries=0)
        except Exception:
            prev = None
    clim = load_json(a.clim)
    if not clim:
        log("⚠️ 找不到 ERA5 氣候值檔（%s）→ 距平與季風指數留白" % a.clim)
    F = default_fetchers()
    heavy_ran = need_heavy(prev, now, a.force)
    heavy = fetch_heavy(now, clim, F) if heavy_ran else {}
    light = fetch_light(now, F)
    doc = assemble(prev, heavy, light, now, heavy_ran)
    os.makedirs(os.path.dirname(outp), exist_ok=True)
    tmp = outp + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, outp)
    log("✅ %s heavy=%s errors=%s/%s" % (outp, heavy_ran, list((heavy.get("_errors") or {}).keys()), list((light.get("_errors") or {}).keys())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
