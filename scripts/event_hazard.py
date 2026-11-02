#!/usr/bin/env python3
"""場次颱風標記＋警示燈號回推（只用 Python 內建模組；讀既有 event_climate/*.json，不重抓觀測）。
颱風警報時間取自中央氣象署颱風資料庫。用法: python3 scripts/event_hazard.py --out event_climate"""
import json, os, re, ssl, sys, time, argparse, html, datetime as dt, urllib.request
TDB = "https://rdc28.cwa.gov.tw/TDB/public/"
CTX = ssl.create_default_context(); CTX.verify_flags &= ~getattr(ssl, "VERIFY_X509_STRICT", 0)

def get(url, data=None, ajax=False):
    h = {"User-Agent": "Mozilla/5.0 event-climate"}
    if ajax: h["X-Requested-With"] = "XMLHttpRequest"
    for i in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=h), timeout=40, context=CTX) as r: return r.read()
        except Exception:
            if i == 2: raise
            time.sleep(3 * (i + 1))

def P(s):
    try: return dt.datetime.strptime(s.strip()[:16], "%Y-%m-%d %H:%M")
    except Exception: return None

def typhoons(out, eds):
    """警報颱風清單＋各颱風陸上警報時間（快取在 typhoons.json）"""
    cf = os.path.join(out, "typhoons.json")
    cache = json.load(open(cf, encoding="utf-8")) if os.path.exists(cf) else {}
    try:
        wl = json.loads(get(TDB + "warning_typhoon_list/get_warning_typhoon", b"year=all", True).decode("utf-8-sig"))
    except Exception as ex:
        print("typhoon list fail", ex, file=sys.stderr); wl = []
    spans = [(dt.datetime.fromisoformat(e["start"]), dt.datetime.fromisoformat(e["end"]) + dt.timedelta(days=1)) for e in eds]
    for t in wl:
        tid = str(t["id"]); s, e = P(t.get("sea_start_datetime") or ""), P(t.get("sea_end_datetime") or "")
        if not s or not e or not any(a < e and s < b for a, b in spans): continue
        c = cache.get(tid)
        if c and c.get("done"): continue
        c = dict(id=tid, zh=(t.get("cht_name") or "").strip(), en=(t.get("eng_name") or "").strip().title(), sea=[s.isoformat(" ", "minutes"), e.isoformat(" ", "minutes")], land=None)
        try:
            pg = html.unescape(re.sub(r"<[^>]+>", " ", get(TDB + "typhoon_detail?typhoon_id=" + tid).decode("utf-8", "ignore")))
            a = re.search(r"發布時間\s+海上\s*([\d\- :]+?)\s+(?:陸上\s*([\d\- :]+?)\s+)?解除時間\s+(?:陸上\s*([\d\- :]+?)\s+)?海上\s*([\d\- :]+)", pg)
            if a and a.group(2) and a.group(3): c["land"] = [P(a.group(2)).isoformat(" ", "minutes"), P(a.group(3)).isoformat(" ", "minutes")]
            c["done"] = bool(a)
        except Exception as ex: print("detail fail", tid, ex, file=sys.stderr)
        cache[tid] = c; time.sleep(0.5)
    json.dump(cache, open(cf, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    return list(cache.values())

def rng(x): return (dt.datetime.fromisoformat(x[0]), dt.datetime.fromisoformat(x[1])) if x else None

def es(T): return 6.105 * 2.718281828 ** (17.27 * T / (237.7 + T))

def hindcast(ed, tys):
    """逐時回推場次天氣警示燈號（依觀測值）。每天 24 格字串，如 'R2W1'（類別＋等級 1黃 2橙 3紅 4紫）。"""
    hrs = []
    for d in ed["days"]:
        h = d.get("hr") or {}
        for i in range(24):
            hrs.append(dict(t=dt.datetime.fromisoformat(d["date"]) + dt.timedelta(hours=i),
                            **{k: (h.get(k) or [None] * 24)[i] for k in ("T", "AT", "P", "RH", "V")}))
    tmaxs = [d.get("tmax") for d in ed["days"]]
    all36 = len(tmaxs) >= 3 and all(x is not None and x >= 36 for x in tmaxs)
    all38 = len(tmaxs) >= 3 and all(x is not None and x >= 38 for x in tmaxs)
    out = []
    for i, x in enumerate(hrs):
        L = {}
        def lit(c, l): L[c] = max(L.get(c, 0), l)
        win = lambda n: [y["P"] for y in hrs[max(0, i - n + 1): i + 1] if y["P"] is not None]
        v1, v3, v24 = (x["P"] or 0), sum(win(3)), sum(win(24))
        if v24 >= 500: lit("R", 4)
        elif v24 >= 350 or v3 >= 200: lit("R", 3)
        elif v24 >= 200 or v3 >= 100: lit("R", 2)
        elif v24 >= 80 or v1 >= 40: lit("R", 1)
        T, AT, V = x["T"], x["AT"], x["V"]
        if T is not None:
            if all38: lit("H", 3)
            elif T >= 38: lit("H", 2)
            elif all36: lit("H", 2)
            elif T >= 36: lit("H", 1)
            if T >= 38 or (AT is not None and AT >= 40): lit("X", 4)
            elif x["RH"] is not None and 1.07 * T + 0.2 * (x["RH"] / 100 * es(T)) - 0.65 * (V or 0) - 2.7 >= 40: lit("X", 3)
            past = [y["T"] for y in hrs[max(0, i - 23): i + 1] if y["T"] is not None]
            if len(past) >= 4 and all(t < 6 for t in past): lit("C", 3)
            elif T <= 6: lit("C", 2)
            elif T <= 10 and len(past) >= 4 and all(t < 12 for t in past): lit("C", 2)
            elif T <= 10: lit("C", 1)
        if V is not None:
            if V >= 32.7: lit("W", 3)
            elif V >= 20.8: lit("W", 2)
            elif V >= 13.9: lit("W", 1)
        for t in tys:
            land, sea = rng(t.get("land")), rng(t.get("sea"))
            if land and land[0] <= x["t"] < land[1]: lit("T", 4)
            elif sea and sea[0] <= x["t"] < sea[1]: lit("T", 3)
        out.append("".join(f"{k}{v}" for k, v in sorted(L.items()) if v))
    return [out[j * 24:(j + 1) * 24] for j in range(len(ed["days"]))]

def ty_tag(ed, tys):
    a = dt.datetime.fromisoformat(ed["start"]); b = dt.datetime.fromisoformat(ed["end"]) + dt.timedelta(days=1)
    rain = max([d.get("rain") or 0 for d in ed["days"]] or [0])
    p1 = max([v for d in ed["days"] for v in ((d.get("hr") or {}).get("P") or []) if v is not None] or [0])
    vmax = max([v for d in ed["days"] for v in ((d.get("hr") or {}).get("V") or []) if v is not None] or [0])
    hit = []
    for t in tys:
        sea, land = rng(t.get("sea")), rng(t.get("land"))
        if not (sea and sea[0] < b and a < sea[1]): continue
        lo = bool(land and land[0] < b and a < land[1])
        # 顯著影響：陸上警報期間且會場有明顯風雨；或海上警報期間會場觀測到大雨／強風
        strong = rain >= 80 or p1 >= 40 or vmax >= 13.9
        mild = rain >= 30 or p1 >= 15 or vmax >= 10.8
        hit.append(dict(id=t["id"], zh=t["zh"], en=t["en"], sea=t["sea"], land=t.get("land") if lo else None, sig=bool((lo and mild) or strong)))
    return hit

TXT = {"sea": ("氣象署對{n}颱風發布海上颱風警報的期間與場次重疊。", "The event overlapped a CWA sea warning for Typhoon {e}.", "台風{e}（{n}）の海上台風警報の期間と重なりました。"),
       "land": ("氣象署對{n}颱風發布海上陸上颱風警報的期間與場次重疊。", "The event overlapped a CWA sea-and-land warning for Typhoon {e}.", "台風{e}（{n}）の海上・陸上台風警報の期間と重なりました。"),
       "sig": ("會場觀測到明顯風雨，屬顯著影響。", "Notable wind or rain was observed at the venue (significant impact).", "会場で目立った風雨が観測され、顕著な影響がありました。"),
       "none": ("附近海域有低壓系統，但氣象署未發布颱風警報。", "There was a low-pressure system offshore, but CWA issued no typhoon warning.", "近海に低気圧がありましたが、気象署の台風警報は出ていませんでした。")}
OLD = (re.compile(r"^(會場.{1,3}方約 \d+ 公里處有颱風.*?。|遠方（會場.*?颱風或熱帶性低氣壓.*?。)"),
       re.compile(r"^A typhoon or tropical depression[^.]*\.\s*"),
       re.compile(r"^(会場の.{1,3}約\d+kmに台風.*?。|遠方（会場の.*?台風または熱帯低気圧.*?。)"))
NEW = (re.compile(r"^((氣象署對.*?颱風.*?重疊。(會場觀測到明顯風雨，屬顯著影響。)?)+|附近海域有低壓系統，但氣象署未發布颱風警報。)"),
       re.compile(r"^((The event overlapped a CWA [^.]*\.\s*(Notable wind[^.]*\.\s*)?)+|There was a low-pressure system offshore[^.]*\.\s*)"),
       re.compile(r"^((台風[^。]*?の海上[^。]*?重なりました。(会場で目立った風雨.*?。)?)+|近海に低気圧がありましたが.*?。)"))

def fix_synoptic(out, tagmap):
    f = os.path.join(out, "synoptic.json")
    if not os.path.exists(f): return
    S = json.load(open(f, encoding="utf-8"))
    for k, days in S.items():
        tg = tagmap.get(k, [])
        for date, x in days.items():
            had = any(t.startswith("typhoon") or t == "ty_none" for t in x["tags"])
            for i, lang in enumerate(("zh", "en", "ja")):
                x[lang] = NEW[i].sub("", OLD[i].sub("", x[lang]))
            x["tags"] = [t for t in x["tags"] if not t.startswith("typhoon") and not t.startswith("ty_")]
            d0 = dt.datetime.fromisoformat(date); d1 = d0 + dt.timedelta(days=1)
            on = [t for t in tg if rng(t["sea"])[0] < d1 and d0 < rng(t["sea"])[1]]
            pre = ["", "", ""]; tags = []
            for t in on:
                lo = bool(t["land"] and rng(t["land"])[0] < d1 and d0 < rng(t["land"])[1])
                z, e, j = TXT["land" if lo else "sea"]
                pre = [pre[0] + z.format(n=t["zh"]), pre[1] + e.format(e=t["en"]) + " ", pre[2] + j.format(e=t["en"], n=t["zh"])]
                if t["sig"]: pre = [pre[0] + TXT["sig"][0], pre[1] + TXT["sig"][1] + " ", pre[2] + TXT["sig"][2]]
                tags.append("ty_sig" if t["sig"] else ("ty_land" if lo else "ty_sea"))
            if not on and had:
                pre = [TXT["none"][0], TXT["none"][1] + " ", TXT["none"][2]]; tags = ["ty_none"]
            x["tags"] = tags + x["tags"]
            x["zh"], x["en"], x["ja"] = pre[0] + x["zh"], pre[1] + x["en"], pre[2] + x["ja"]
    json.dump(S, open(f, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))

def alert_max(al):
    m = {}
    for row in al:
        for s in row:
            for c, l in re.findall(r"([A-Z])(\d)", s): m[c] = max(m.get(c, 0), int(l))
    return m

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--out", default="event_climate"); a = ap.parse_args()
    ef = os.path.join(a.out, "editions.json"); E = json.load(open(ef, encoding="utf-8"))
    sers = {}
    for i in range(len(E["meta"]["series"])):
        f = os.path.join(a.out, f"series_{i:02d}.json")
        if os.path.exists(f): sers[f] = json.load(open(f, encoding="utf-8"))
    full = {x["id"]: x for s in sers.values() for x in s["editions"]}
    tys = typhoons(a.out, E["editions"])
    tagmap = {}
    for x in full.values():
        tg = ty_tag(x, tys); x["ty"] = tg; tagmap[x["key"]] = tg
        ids = {g["id"] for g in tg}
        al = hindcast(x, [t for t in tys if t["id"] in ids])
        for d, row in zip(x["days"], al): d["al"] = row
        x["al_max"] = alert_max(al)
    dis = {}
    try: dis = json.load(open(os.path.join(a.out, "disruptions.json"), encoding="utf-8"))["items"]
    except Exception as ex: print("no disruptions", ex)
    for x in full.values():
        if x["id"] in dis: x["dis"] = dis[x["id"]]
        else: x.pop("dis", None)
    nsig = 0
    for e in E["editions"]:
        x = full.get(e["id"])
        if not x: continue
        e["ty"], e["al_max"] = x["ty"], x["al_max"]
        if x.get("dis"): e["dis"] = x["dis"]
        else: e.pop("dis", None)
        nsig += any(t["sig"] for t in x["ty"])
    E["meta"]["hazard_note"] = "颱風警報時間：中央氣象署颱風資料庫；燈號為依測站逐時觀測回推之參考值"
    json.dump(E, open(ef, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    for f, s in sers.items(): json.dump(s, open(f, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    fix_synoptic(a.out, tagmap)
    print("typhoon editions", sum(1 for e in E["editions"] if e.get("ty")), "significant", nsig)

if __name__ == "__main__": main()
