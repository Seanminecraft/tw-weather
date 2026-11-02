#!/usr/bin/env python3
"""場次氣候診斷：把每屆觀測（editions.json 內的逐日值）與 1991–2020 同測站、同日期前後 7 天的氣候值比較。
只用標準函式庫；氣候值檔 climo_1991_2020.json 為固定資料，不需每天重抓。"""
import json, os, argparse, datetime as dt, statistics as st
K = ["tmax", "tmin", "tavg", "rain", "ws", "rh"]
ALIAS = {"467440": "467441", "467441": "467441"}
HK = {"tavg": "T", "ws": "V", "rh": "RH"}
def dval(d, k):
    if k in HK:  # 全日（24 小時）平均，與氣候值定義一致
        a = [x for x in ((d.get("hr") or {}).get(HK[k]) or []) if x is not None]
        return round(sum(a) / len(a), 2) if len(a) >= 20 else None
    return d.get(k)
def obs(e):
    out = {}
    for i, k in enumerate(K):
        a = [dval(d, k) for d in e["days"]]
        if not a or any(x is None for x in a): continue
        out[k] = round(sum(a), 1) if k == "rain" else round(sum(a) / len(a), 2)
    return out
def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--out", default="event_climate"); a = ap.parse_args()
    C = json.load(open(os.path.join(a.out, "climo_1991_2020.json"), encoding="utf-8"))
    P = os.path.join(a.out, "editions.json"); E = json.load(open(P, encoding="utf-8"))
    cache = {}; full = {}
    for fn in sorted(os.listdir(a.out)):
        if fn.startswith("series_") and fn.endswith(".json"):
            for x in json.load(open(os.path.join(a.out, fn), encoding="utf-8")).get("editions", []): full[x["id"]] = x
    for e in E["editions"]:
        sid = ALIAS.get(e["station"]["id"], e["station"]["id"]); c = C["st"].get(sid)
        e.pop("dg", None)
        if not c or not e.get("days"): continue
        s0 = dt.date.fromisoformat(c["start"]); V = c["v"]
        s = dt.date.fromisoformat(e["start"]); n = len(e["days"])
        ck = (sid, s.strftime("%m-%d"), n)
        if ck not in cache:
            S = {k: [] for k in K}
            for y in range(1991, 2021):
                try: b = s.replace(year=y)
                except ValueError: b = s.replace(year=y, day=28)
                for o in range(-7, 8):
                    i0 = (b - s0).days + o
                    if i0 < 0 or i0 + n > len(V): continue
                    rows = V[i0:i0 + n]
                    for j, k in enumerate(K):
                        x = [r[j] for r in rows]
                        if any(v is None for v in x): continue
                        S[k].append(sum(x) if k == "rain" else sum(x) / n)
            cache[ck] = S
        S = cache[ck]; o = obs(full.get(e["id"], e)); dg = {}
        for k, v in o.items():
            smp = S[k]
            if len(smp) < 200: continue
            nm = st.mean(smp); lo = sum(1 for x in smp if x < v - 1e-9); eq = sum(1 for x in smp if abs(x - v) <= 1e-9)
            dg[k] = [v, round(nm, 1), round(v - nm, 1), round(100 * (lo + eq / 2) / len(smp))]
        if dg: e["dg"] = dg; e["dg_n"] = len(S["tmax"])
    E["meta"]["climo"] = {"period": "1991-2020", "window": 7}
    json.dump(E, open(P, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print("diag", sum(1 for e in E["editions"] if "dg" in e), "/", len(E["editions"]))
if __name__ == "__main__": main()
