#!/usr/bin/env python3
"""近期場次氣候展望：未來 3 個月內的場次 × 氣象署月／季長期天氣展望 × Copernicus C3S 季節預報 × 測站氣候常態。
用法: python3 scripts/event_outlook.py --out event_climate
- 氣象署 FW14（每週五）、FW15（每月底）PDF：每次執行都重抓（很小），需 pdftotext（poppler-utils）。
- C3S：每月 10 日後才有新初始場；只有新初始場時才下載（需環境變數 CDS_API_KEY，存在 GitHub Secrets，不寫進檔案）。
  回報期（1993–2016）三分類門檻存在 outlook_src/c3s_hindcast.json，已有的不重抓。
- 輸出 event_climate/outlook.json（網頁讀這個）。"""
import os, re, sys, json, math, subprocess, tempfile, argparse, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import event_climate as EC
TZ = dt.timezone(dt.timedelta(hours=8))
FW14 = "https://www.cwa.gov.tw/Data/fcst_pdf/FW14.pdf"
FW15 = "https://www.cwa.gov.tw/Data/fcst_pdf/FW15.pdf"
REGS = ["北部", "中部", "南部", "東部"]
CITY = {"北部": "臺北市 新北市 基隆市 桃園市 新竹市 新竹縣 宜蘭縣", "中部": "臺中市 彰化縣 南投縣 苗栗縣 雲林縣",
        "南部": "嘉義市 嘉義縣 臺南市 高雄市 屏東縣", "東部": "花蓮縣 臺東縣"}
STN = {"北部": ("466920", "臺北"), "中部": ("467490", "臺中"), "南部": ("467441", "高雄")}
BOX = {"北部": ([25], [121, 122]), "中部": ([24], [120, 121]), "南部": ([22, 23], [120]), "東部": ([23, 24], [121])}
SYSTEMS = [("ecmwf", "51"), ("meteo_france", "9"), ("dwd", "22")]   # C3S 改版時改這裡
AREA = [26, 119, 21, 123]
DAYS_IN = lambda y, m: (dt.date(y + m // 12, m % 12 + 1, 1) - dt.date(y, m, 1)).days

def region(city):
    c = city.replace("台", "臺")
    for r, s in CITY.items():
        if c in s.split(): return r
    return None

def pdf_text(url):
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "a.pdf"); open(p, "wb").write(EC.get(url))
        return subprocess.run(["pdftotext", "-layout", p, "-"], capture_output=True, text=True, check=True).stdout

def roc(s):
    m = re.search(r"民國\s*(\d+)\s*年\s*(\d+)\s*月\s*(\d+)\s*日", s); return dt.date(int(m[1]) + 1911, int(m[2]), int(m[3])) if m else None

TRI = r"(\d+)\s*：\s*(\d+)\s*：\s*(\d+)"; RNG = r"([\d.]+)\s*～\s*([\d.]+)"

def parse_fw14(t):
    iss = roc(t.split("發布日期")[1][:20]); nxt = roc(t.split("下次預定發布日期")[1][:20]) if "下次預定發布日期" in t else None
    per = []
    for a, b, c, d in re.findall(r"\((\d+)月(\d+)日~(\d+)月(\d+)日\)", t)[:3]:
        y0 = iss.year + (1 if int(a) < iss.month - 6 else 0); y1 = y0 + (1 if int(c) < int(a) else 0)
        per.append([dt.date(y0, int(a), int(b)).isoformat(), dt.date(y1, int(c), int(d)).isoformat()])
    out = dict(issued=iss.isoformat(), next=nxt and nxt.isoformat(), periods=per, tp={}, rp={}, tn={}, rn={})
    for ln in t.splitlines():
        s = ln.strip()
        r = s[:2]
        if r not in REGS: continue
        tr = re.findall(TRI, s); rg = re.findall(RNG, s)
        if len(tr) == 6: out["tp"][r] = [list(map(int, x)) for x in tr[:3]]; out["rp"][r] = [list(map(int, x)) for x in tr[3:]]
        elif len(rg) == 6: out["tn"][r] = [list(map(float, x)) for x in rg[:3]]; out["rn"][r] = [list(map(float, x)) for x in rg[3:]]
    assert len(per) == 3 and len(out["tp"]) == 4, "FW14 格式改變"
    return out

def parse_fw15(t):
    iss = roc(t.split("發布日期")[1][:20]); nxt = roc(t.split("下次預定發布日期")[1][:20]) if "下次預定發布日期" in t else None
    m = re.search(r"有效期間：自民國(\d+)年(\d+)月", t); y, mo = int(m[1]) + 1911, int(m[2])
    months = []
    for i in range(3): months.append("%04d-%02d" % (y + (mo - 1 + i) // 12, (mo - 1 + i) % 12 + 1))
    out = dict(issued=iss.isoformat(), next=nxt and nxt.isoformat(), months=months, tp={}, rp={}, tn={}, rn={})
    sec = None
    for ln in t.splitlines():
        s = ln.strip()
        if "氣溫機率預報" in s: sec = "tp"
        elif "雨量機率預報" in s: sec = "rp"
        elif "月平均氣溫之氣候正常值範圍" in s: sec = "tn"
        elif "月累積雨量之氣候正常值範圍" in s: sec = "rn"
        if not sec or s[:2] not in REGS: continue
        if sec in ("tp", "rp"):
            tr = re.findall(TRI, s)
            if len(tr) == 3: out[sec][s[:2]] = [list(map(int, x)) for x in tr]
        else:
            rg = re.findall(RNG, s)
            if len(rg) == 3: out[sec][s[:2]] = [list(map(float, x)) for x in rg]
    assert len(out["tp"]) == 4 and len(out["rp"]) == 4, "FW15 格式改變"
    return out

# ---------------- C3S ----------------
def c3s_init(today):
    d = today if today.day >= 10 else today.replace(day=1) - dt.timedelta(days=1)
    return "%04d-%02d" % (d.year, d.month)

def regmean(a, la, lo, r):
    import numpy as np
    ys, xs = BOX[r]
    yi = [int(np.abs(la - y).argmin()) for y in ys]; xi = [int(np.abs(lo - x).argmin()) for x in xs]
    return a[..., yi, :][..., xi].mean(axis=(-1, -2))

def c3s_client():
    import cdsapi
    return cdsapi.Client(url=os.environ.get("CDSAPI_URL", "https://cds.climate.copernicus.eu/api"), key=os.environ["CDS_API_KEY"], quiet=True, progress=False)

def c3s_update(src, today):
    """有新初始場且有金鑰才下載；每個模式下載完就存檔（中途逾時，下次接著做）。"""
    if not os.environ.get("CDS_API_KEY"): print("C3S: 沒有 CDS_API_KEY，略過下載"); return
    import numpy as np, netCDF4 as N
    init = c3s_init(today); y, m = map(int, init.split("-"))
    fp = os.path.join(src, "c3s_fc.json"); hp = os.path.join(src, "c3s_hindcast.json")
    FC = json.load(open(fp, encoding="utf-8")) if os.path.exists(fp) else {}
    HC = json.load(open(hp, encoding="utf-8")) if os.path.exists(hp) else {}
    if FC.get("init") != init:
        old = FC; FC = dict(init=init, sys={})
    cl = c3s_client()
    for ctr, sy in SYSTEMS:
        key = f"{ctr}_{sy}"
        try:
            if key not in FC["sys"]:
                with tempfile.TemporaryDirectory() as d:
                    p = os.path.join(d, "f.nc")
                    cl.retrieve("seasonal-postprocessed-single-levels", {"originating_centre": ctr, "system": sy,
                        "variable": ["2m_temperature_anomaly", "total_precipitation_anomalous_rate_of_accumulation"],
                        "product_type": ["monthly_mean"], "year": [str(y)], "month": ["%02d" % m],
                        "leadtime_month": [str(i) for i in range(1, 7)], "area": AREA, "data_format": "netcdf"}, p)
                    f = N.Dataset(p); la = np.array(f["latitude"][:]); lo = np.array(f["longitude"][:]); L = [int(x) for x in f["forecastMonth"][:]]
                    t = np.array(f["t2a"][:, 0]); pr = np.array(f["tpara"][:, 0]) * 86400 * 1000   # mm/day
                    ent = dict(la=la.round(2).tolist(), lo=lo.round(2).tolist(), lead={})
                    for i, ld in enumerate(L):
                        ent["lead"][str(ld)] = dict(
                            map=dict(t=np.nanmean(t[:, i], 0).round(2).tolist(), p=np.nanmean(pr[:, i], 0).round(2).tolist()),
                            reg={r: dict(t=[round(float(v), 2) for v in regmean(t[:, i], la, lo, r) if not math.isnan(v)],
                                         p=[round(float(v), 3) for v in regmean(pr[:, i], la, lo, r) if not math.isnan(v)]) for r in BOX})
                    FC["sys"][key] = ent
                    json.dump(FC, open(fp, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
                    print("C3S forecast", key, init)
            hk = f"{key}_{m:02d}"
            if hk not in HC:
                with tempfile.TemporaryDirectory() as d:
                    p = os.path.join(d, "h.nc")
                    cl.retrieve("seasonal-monthly-single-levels", {"originating_centre": ctr, "system": sy,
                        "variable": ["2m_temperature", "total_precipitation"], "product_type": ["monthly_mean"],
                        "year": [str(v) for v in range(1993, 2017)], "month": ["%02d" % m],
                        "leadtime_month": [str(i) for i in range(1, 7)], "area": AREA, "data_format": "netcdf"}, p)
                    HC[hk] = hindcast_terciles(p)
                    json.dump(HC, open(hp, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
                    print("C3S hindcast", hk)
        except Exception as e:
            print("C3S 失敗", key, type(e).__name__, str(e)[:200])

def hindcast_terciles(path):
    import numpy as np, netCDF4 as N
    h = N.Dataset(path); la = np.array(h["latitude"][:]); lo = np.array(h["longitude"][:]); L = [int(x) for x in h["forecastMonth"][:]]
    t = np.array(h["t2m"][:]); p = np.array(h["tprate"][:]) * 86400 * 1000
    out = {}
    for i, ld in enumerate(L):
        o = {}
        for r in BOX:
            e = {}
            for v, a in (("t", t), ("p", p)):
                x = regmean(a[:, :, i], la, lo, r); x = x - np.nanmean(x); x = x[~np.isnan(x)]
                e[v] = [round(float(q), 3) for q in np.percentile(x, [100 / 3, 200 / 3])]
            e["pm"] = round(float(np.nanmean(regmean(p[:, :, i], la, lo, r))), 3)
            o[r] = e
        out[str(ld)] = o
    return out

def c3s_event(src, reg, days):
    fp = os.path.join(src, "c3s_fc.json"); hp = os.path.join(src, "c3s_hindcast.json")
    if not (os.path.exists(fp) and os.path.exists(hp)) or reg not in BOX: return None
    FC = json.load(open(fp, encoding="utf-8")); HC = json.load(open(hp, encoding="utf-8"))
    y0, m0 = map(int, FC["init"].split("-"))
    ms = {}
    for d in days: k = (d.year, d.month); ms[k] = ms.get(k, 0) + 1
    tot = sum(ms.values()); res = dict(init=FC["init"], months=[], w=[], tp=[0, 0, 0], pp=[0, 0, 0], t=0, t10=0, t90=0, pct=0, sys=[])
    first = None
    for (y, m), n in sorted(ms.items()):
        ld = str((y - y0) * 12 + m - m0 + 1); w = n / tot
        P = {"t": [], "p": []}; mean = {"t": [], "p": []}; pct = []; allt = []; per = {}
        for key, ent in FC["sys"].items():
            hk = HC.get(f"{key}_{m0:02d}", {}).get(ld, {}).get(reg); le = ent["lead"].get(ld)
            if not hk or not le: continue
            for v in "tp":
                x = le["reg"][reg][v]; lo_, hi_ = hk[v]
                P[v].append([sum(1 for a in x if a < lo_) / len(x), sum(1 for a in x if lo_ <= a <= hi_) / len(x), sum(1 for a in x if a > hi_) / len(x)])
                mean[v].append(sum(x) / len(x))
            pct.append(100 * mean["p"][-1] / hk["pm"] if hk["pm"] else 0); allt += le["reg"][reg]["t"]
            per[key] = le
        if not P["t"]: return None
        res["months"].append(m); res["w"].append(round(w, 2))
        for v, k in (("t", "tp"), ("p", "pp")):
            avg = [sum(c[i] for c in P[v]) / len(P[v]) for i in range(3)]
            res[k] = [res[k][i] + w * 100 * avg[i] for i in range(3)]
        allt.sort(); q = lambda f: allt[min(len(allt) - 1, int(f * (len(allt) - 1)))]
        res["t"] += w * sum(mean["t"]) / len(mean["t"]); res["t10"] += w * q(.1); res["t90"] += w * q(.9); res["pct"] += w * sum(pct) / len(pct)
        if first is None:
            nd = DAYS_IN(y, m); keys = sorted(per)
            first = dict(m=m, map=dict(t=[[round(sum(per[k]["map"]["t"][i][j] for k in keys) / len(keys), 2) for j in range(len(per[keys[0]]["map"]["t"][0]))] for i in range(len(per[keys[0]]["map"]["t"]))],
                                       p=[[round(sum(per[k]["map"]["p"][i][j] for k in keys) / len(keys) * nd, 1) for j in range(len(per[keys[0]]["map"]["p"][0]))] for i in range(len(per[keys[0]]["map"]["p"]))]),
                         box=dict(t={k: per[k]["reg"][reg]["t"] for k in keys}, p={k: [round(v * nd, 1) for v in per[k]["reg"][reg]["p"]] for k in keys}),
                         la=FC["sys"][keys[0]]["la"], lo=FC["sys"][keys[0]]["lo"])
            res["sys"] = keys
    for k in ("tp", "pp"):
        a = [round(x) for x in res[k]]; a[1] = 100 - a[0] - a[2]; res[k] = a
    for k in ("t", "t10", "t90"): res[k] = round(res[k], 1)
    res["pct"] = round(res["pct"]); res.update(first)
    return res

# ---------------- CWA ----------------
def cwa_event(cwa, reg, s):
    f14, f15 = cwa.get("fw14"), cwa.get("fw15"); si = s.isoformat()
    if f14 and reg in f14["tp"]:
        P = f14["periods"]
        for i, name in ((0, "第1週"), (1, "第2週"), (2, "第1~4週")):
            if P[i][0] <= si <= P[i][1]:
                o = dict(kind="fw14", label=name, period=P[i], tp=f14["tp"][reg][i], rp=f14["rp"][reg][i], tn=f14["tn"].get(reg, [None] * 3)[i], rn=f14["rn"].get(reg, [None] * 3)[i], issued=f14["issued"])
                if i < 2: o["w4"] = dict(tp=f14["tp"][reg][2], rp=f14["rp"][reg][2])
                return o
    if f15 and reg in f15["tp"]:
        ym = si[:7]
        if ym in f15["months"]:
            i = f15["months"].index(ym)
            return dict(kind="fw15", label="%d月" % s.month, tp=f15["tp"][reg][i], rp=f15["rp"][reg][i], tn=f15["tn"].get(reg, [None] * 3)[i], rn=f15["rn"].get(reg, [None] * 3)[i], issued=f15["issued"])
    return None

# ---------------- 常態 ----------------
def normals(C, sid, s, n):
    c = C["st"].get(sid)
    if not c: return None
    s0 = dt.date.fromisoformat(c["start"]); V = c["v"]; TX = []; TN = []; RD = []
    for y in range(1991, 2021):
        try: b = s.replace(year=y)
        except ValueError: b = s.replace(year=y, day=28)
        for o in range(-7, 8):
            i = (b - s0).days + o; R = V[i:i + n]
            if len(R) < n or any(r[0] is None or r[1] is None for r in R): continue
            TX.append(sum(r[0] for r in R) / n); TN.append(sum(r[1] for r in R) / n); RD.append(any((r[3] or 0) >= 1 for r in R))
    if not TX: return None
    q = lambda a, p: sorted(a)[int(p * (len(a) - 1))]
    return dict(tx=[round(q(TX, .1), 1), round(sum(TX) / len(TX), 1), round(q(TX, .9), 1)],
                tn=[round(q(TN, .1), 1), round(sum(TN) / len(TN), 1), round(q(TN, .9), 1)], rain=round(100 * sum(RD) / len(RD)))

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--out", default="event_climate"); ap.add_argument("--today"); a = ap.parse_args()
    today = dt.date.fromisoformat(a.today) if a.today else dt.datetime.now(TZ).date()
    src = os.path.join(a.out, "outlook_src"); os.makedirs(src, exist_ok=True)
    cp = os.path.join(src, "cwa.json"); cwa = json.load(open(cp, encoding="utf-8")) if os.path.exists(cp) else {}
    for k, u, fn in (("fw14", FW14, parse_fw14), ("fw15", FW15, parse_fw15)):
        try: cwa[k] = fn(pdf_text(u))
        except Exception as e: print("氣象署", k, "讀取失敗，沿用上次：", type(e).__name__, str(e)[:150])
    json.dump(cwa, open(cp, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    c3s_update(src, today)
    C = json.load(open(os.path.join(a.out, "climo_1991_2020.json"), encoding="utf-8"))
    E = json.load(open(os.path.join(a.out, "editions.json"), encoding="utf-8"))["editions"]
    import csv, io
    rows = csv.DictReader(io.StringIO(EC.get(EC.SHEET).decode("utf-8-sig")))
    ups = {}
    for r in rows:
        s = EC.classify(r)
        if not s: continue
        try: sd = EC.pdate(r["開始日期"]); ed = EC.pdate(r["結束日期"] or r["開始日期"])
        except Exception: continue
        if ed < sd or (ed - sd).days > 15: ed = sd
        if ed < today or (sd - today).days > 92: continue
        ups[f"{s}_{sd.isoformat()}"] = (s, sd, ed, r)
    ev = []
    for eid, (s, sd, ed, r) in sorted(ups.items(), key=lambda x: x[1][1]):
        reg = region(r["縣市"]); n = (ed - sd).days + 1; days = [sd + dt.timedelta(i) for i in range(n)]
        st = STN.get(reg)
        mset = {d.month for d in days}
        past = [x for x in E if x["series"] == s and int(x["start"][5:7]) in mset and x.get("days")][-4:]
        pa = []
        for x in past:
            D = x["days"]; tx = [d["tmax"] for d in D if d.get("tmax") is not None]; tn = [d["tmin"] for d in D if d.get("tmin") is not None]
            pa.append(dict(y=x["start"][:4], tx=round(sum(tx) / len(tx), 1) if tx else None, tn=round(sum(tn) / len(tn), 1) if tn else None,
                           rain=round(sum(d.get("rain") or 0 for d in D), 1) if any(d.get("rain") is not None for d in D) else None))
        e = dict(id=eid, series=s, name=EC.re.sub(r"\s+", " ", r["活動名稱"]).strip(), start=sd.isoformat(), end=ed.isoformat(), venue=r["活動地點"].strip(),
                 city=r["縣市"], reg=reg, lat=float(r["緯度"]) if r.get("緯度") else None, lon=float(r["經度"]) if r.get("經度") else None,
                 stn=st and st[1], norm=st and normals(C, st[0], sd, n), cwa=reg and cwa_event(cwa, reg, sd), c3s=reg and c3s_event(src, reg, days), past=pa)
        ev.append(e)
    fc = json.load(open(os.path.join(src, "c3s_fc.json"), encoding="utf-8")) if os.path.exists(os.path.join(src, "c3s_fc.json")) else {}
    out = dict(fw14=cwa.get("fw14") and {k: cwa["fw14"][k] for k in ("issued", "next", "periods")},
               fw15=cwa.get("fw15") and {k: cwa["fw15"][k] for k in ("issued", "next", "months")},
               c3s=dict(init=fc.get("init"), sys=sorted(fc.get("sys", {}))), events=ev)
    json.dump(out, open(os.path.join(a.out, "outlook.json"), "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print("outlook events", len(ev), [x["id"] for x in ev])

if __name__ == "__main__": main()
