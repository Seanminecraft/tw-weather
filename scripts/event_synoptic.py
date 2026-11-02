#!/usr/bin/env python3
"""場次氣候：每個場次日的綜觀天氣圖（ERA5 再分析，台灣時間 14 時）＋依天氣型態規則產生的簡短說明。
需要：xarray zarr gcsfs matplotlib cartopy pillow。用法: python3 scripts/event_synoptic.py --out event_climate"""
import os, sys, json, math, argparse, io, datetime as dt
from concurrent.futures import ThreadPoolExecutor
import warnings; warnings.filterwarnings("ignore")
import numpy as np, xarray as xr
import matplotlib; matplotlib.use("Agg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg
import cartopy.crs as ccrs, cartopy.feature as cf
from PIL import Image

STORE = "gs://gcp-public-data-arco-era5/ar/full_37-1h-0p25deg-chunk-1.zarr-v3"
LAT, LON = slice(45, 10), slice(100, 150)
DS = None

def ds():
    global DS
    if DS is None:
        DS = xr.open_zarr(STORE, chunks=None, storage_options={"token": "anon"}, consolidated=True)
    return DS

def fields(date):
    t = np.datetime64(date + "T06:00")  # 台灣時間 14 時
    d = ds()
    msl = d["mean_sea_level_pressure"].sel(time=t, latitude=LAT, longitude=LON).load() / 100
    u = d["u_component_of_wind"].sel(time=t, level=850, latitude=LAT, longitude=LON).load()
    v = d["v_component_of_wind"].sel(time=t, level=850, latitude=LAT, longitude=LON).load()
    z = d["geopotential"].sel(time=t, level=500, latitude=LAT, longitude=LON).load() / 9.80665
    tp = sum(d["total_precipitation"].sel(time=t - np.timedelta64(h, "h"), latitude=LAT, longitude=LON).load() for h in range(3)) * 1000
    return msl, u, v, z, tp

def at(f, lat, lon): return float(f.sel(latitude=lat, longitude=lon, method="nearest"))

def km(a, b, c, d):
    p = math.radians; x = math.sin(p(c - a) / 2) ** 2 + math.cos(p(a)) * math.cos(p(c)) * math.sin(p(d - b) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(x))

DIR8 = [("北", "N", "北"), ("東北", "NE", "北東"), ("東", "E", "東"), ("東南", "SE", "南東"), ("南", "S", "南"), ("西南", "SW", "南西"), ("西", "W", "西"), ("西北", "NW", "北西")]
def bearing(a, b, c, d):
    y = math.sin(math.radians(d - b)) * math.cos(math.radians(c))
    x = math.cos(math.radians(a)) * math.sin(math.radians(c)) - math.sin(math.radians(a)) * math.cos(math.radians(c)) * math.cos(math.radians(d - b))
    return (math.degrees(math.atan2(y, x)) + 360) % 360

def explain(e, day, f):
    """依 ERA5 型態＋測站觀測，用固定規則組合說明（不臆測）。"""
    msl, u, v, z, tp = f
    lat, lon, m = e["lat"], e["lon"], int(day["date"][5:7])
    ua, va = at(u, lat, lon), at(v, lat, lon)
    wdir = (math.degrees(math.atan2(-ua, -va)) + 360) % 360; wspd = math.hypot(ua, va)
    z5 = at(z, lat, lon); p0 = at(msl, lat, lon)
    tags = []
    # 熱帶低壓／颱風：5–11 月，北緯 32 度以南、東經 110 度以東的最低氣壓 ≤ 996 hPa
    sub = msl.sel(latitude=slice(32, 10), longitude=slice(110, 150))
    i = np.unravel_index(int(sub.argmin()), sub.shape)
    plat, plon, pmin = float(sub.latitude[i[0]]), float(sub.longitude[i[1]]), float(sub.min())
    if 5 <= m <= 11 and pmin <= 996:
        dkm = km(lat, lon, plat, plon); b = DIR8[int((bearing(lat, lon, plat, plon) + 22.5) // 45) % 8]
        tags.append(("typhoon_near" if dkm <= 800 else "typhoon_far", dkm, b, pmin))
    hi_cn = float(msl.sel(latitude=slice(45, 25), longitude=slice(100, 125)).max())
    grad = at(msl, 30, 117) - p0
    if (m >= 10 or m <= 4) and (grad >= 6 or 0 <= wdir <= 100 and wspd >= 5) and not any(t[0] == "typhoon_near" for t in tags):
        tags.append(("cold_surge" if hi_cn >= 1035 and (day.get("tmin") or 99) <= 14 else "ne_monsoon",))
    rain = (day.get("rain_open") or 0) >= 0.5
    if 5 <= m <= 10 and z5 >= 5880 and not any(t[0] == "typhoon_near" for t in tags):
        tags.append(("pac_high_storm",) if rain else ("pac_high",))
    if rain and not tags:
        if m in (5, 6): tags.append(("meiyu",))
        elif 5 <= m <= 8 and 180 <= wdir <= 270: tags.append(("sw_flow",))
        elif 2 <= m <= 6 or m >= 10: tags.append(("front",))
    if not tags:
        tags.append(("ne_return",) if (m >= 11 or m <= 4) and 60 <= wdir <= 150 else ("stable",))
    Z, E, J = [], [], []
    for t in tags:
        k = t[0]
        if k in ("typhoon_near", "typhoon_far"):
            d, b, p = t[1], t[2], t[3]
            if k == "typhoon_near":
                Z.append(f"會場{b[0]}方約 {d:.0f} 公里處有颱風或熱帶性低氣壓（中心氣壓約 {p:.0f} hPa），受其本體或外圍環流影響。")
                E.append(f"A typhoon or tropical depression (central pressure about {p:.0f} hPa) was about {d:.0f} km to the {b[1]} of the venue, bringing its circulation to the area.")
                J.append(f"会場の{b[2]}約{d:.0f}kmに台風または熱帯低気圧（中心気圧約{p:.0f}hPa）があり、その影響を受けました。")
            else:
                Z.append(f"遠方（會場{b[0]}方約 {d:.0f} 公里）有颱風或熱帶性低氣壓，對會場影響有限。")
                E.append(f"A typhoon or tropical depression was far away (about {d:.0f} km to the {b[1]}), with limited effect on the venue.")
                J.append(f"遠方（会場の{b[2]}約{d:.0f}km）に台風または熱帯低気圧がありましたが、会場への影響は限定的でした。")
        elif k == "cold_surge":
            Z.append("大陸冷氣團南下，吹偏北風，氣溫偏低。"); E.append("A continental cold air mass moved south with northerly winds and cold temperatures."); J.append("大陸からの寒気団が南下し、北寄りの風で気温が低めでした。")
        elif k == "ne_monsoon":
            Z.append("受東北季風影響，吹東北風。"); E.append("The northeast monsoon was affecting Taiwan, with northeasterly winds."); J.append("北東季節風の影響で、北東の風が吹きました。")
        elif k == "pac_high":
            Z.append("太平洋高壓籠罩，天氣晴熱穩定。"); E.append("The Pacific high dominated, giving hot and stable weather."); J.append("太平洋高気圧に覆われ、晴れて暑く安定した天気でした。")
        elif k == "pac_high_storm":
            Z.append("太平洋高壓籠罩，白天炎熱，午後有局部對流降雨。"); E.append("Under the Pacific high it was hot, with local afternoon showers or thunderstorms."); J.append("太平洋高気圧に覆われて暑く、午後は局地的なにわか雨がありました。")
        elif k == "meiyu":
            Z.append("梅雨季鋒面影響，有降雨。"); E.append("A plum-rain (Meiyu) front brought rain."); J.append("梅雨前線の影響で雨が降りました。")
        elif k == "sw_flow":
            Z.append("西南氣流影響，水氣多、有降雨。"); E.append("Moist southwesterly flow brought rain."); J.append("南西気流の影響で湿った空気が流れ込み、雨が降りました。")
        elif k == "front":
            Z.append("鋒面或低壓帶通過，有降雨。"); E.append("A front or trough passed, bringing rain."); J.append("前線または気圧の谷が通過し、雨が降りました。")
        elif k == "ne_return":
            Z.append("高壓出海，轉吹東南風，天氣回暖。"); E.append("High pressure moved out to sea and winds turned easterly to southeasterly, warming things up."); J.append("高気圧が東へ抜け、南東の風に変わって気温が上がりました。")
        else:
            Z.append("沒有明顯天氣系統，天氣型態平穩。"); E.append("No significant weather system; the pattern was settled."); J.append("目立った気象システムはなく、穏やかな天気でした。")
    # 對參加者的意義（依測站觀測）
    hi, tmin, ro, dtr = day.get("hi_max"), day.get("tmin"), day.get("rain_open"), day.get("dtr")
    if ro is not None and ro >= 10: Z.append(f"開場時段雨量約 {ro:.0f} 毫米，排隊與移動需要雨具。"); E.append(f"About {ro:.0f} mm fell during opening hours; rain gear was needed for queues."); J.append(f"開場時間の雨量は約{ro:.0f}mmで、待機列や移動に雨具が必要でした。")
    elif ro is not None and ro >= 0.5: Z.append("開場時段有降雨，建議帶傘。"); E.append("There was some rain during opening hours; an umbrella was useful."); J.append("開場時間に雨があり、傘があると安心でした。")
    if hi is not None and hi >= 35: Z.append(f"綜合熱指數最高約 {hi:.0f}，室外排隊有中暑風險。"); E.append(f"Heat index peaked near {hi:.0f}, a heat-illness risk for outdoor queues."); J.append(f"暑さ指数は最高約{hi:.0f}で、屋外の待機列では熱中症の危険がありました。")
    if tmin is not None and tmin <= 12: Z.append(f"最低氣溫約 {tmin:.0f}°C，清晨排隊偏冷。"); E.append(f"The low was about {tmin:.0f}°C, so early-morning queues were cold."); J.append(f"最低気温は約{tmin:.0f}°Cで、早朝の待機列は寒く感じられました。")
    if dtr is not None and dtr >= 10: Z.append(f"日夜溫差約 {dtr:.0f}°C，適合洋蔥式穿搭。"); E.append(f"The day–night range was about {dtr:.0f}°C; layers were handy."); J.append(f"寒暖差は約{dtr:.0f}°Cで、重ね着が便利でした。")
    return dict(zh="".join(Z), en=" ".join(E), ja="".join(J), tags=[t[0] for t in tags])

def render(e, date, f, path):
    msl, u, v, z, tp = f
    fig = Figure(figsize=(7.2, 5.4), dpi=110); FigureCanvasAgg(fig)
    ax = fig.add_subplot(1, 1, 1, projection=ccrs.PlateCarree()); ax.set_extent([100, 150, 10, 45], ccrs.PlateCarree())
    lv = [0.5, 1, 2, 5, 10, 20, 40, 80]
    ax.add_feature(cf.LAND, facecolor="#f2efe9"); ax.add_feature(cf.OCEAN, facecolor="#e8f1f8")
    cs = ax.contourf(tp.longitude, tp.latitude, tp, levels=lv, cmap="YlGnBu", extend="max", alpha=.85, transform=ccrs.PlateCarree())
    ax.coastlines("50m", linewidth=.6, color="#555")
    c = ax.contour(msl.longitude, msl.latitude, msl, levels=np.arange(940, 1064, 4), colors="k", linewidths=.8, transform=ccrs.PlateCarree())
    ax.clabel(c, fmt="%d", fontsize=7)
    s = 8
    ax.barbs(u.longitude[::s], u.latitude[::s], u[::s, ::s] * 1.944, v[::s, ::s] * 1.944, length=4.5, linewidth=.5, color="#7c2d12", transform=ccrs.PlateCarree())
    ax.plot(e["lon"], e["lat"], marker="*", color="#e11d48", markersize=14, markeredgecolor="w", transform=ccrs.PlateCarree())
    gl = ax.gridlines(draw_labels=True, linewidth=.3, color="#999", alpha=.5); gl.top_labels = gl.right_labels = False
    gl.xlabel_style = gl.ylabel_style = {"size": 7}
    cb = fig.colorbar(cs, ax=ax, orientation="horizontal", pad=.06, fraction=.04, aspect=40); cb.ax.tick_params(labelsize=7)
    cb.set_label("3-h precip (mm)", fontsize=7)
    ax.set_title(f"{date} 14:00 (UTC+8)  MSLP (hPa) / 850 hPa wind (kt)\nERA5 reanalysis", fontsize=8.5)
    buf = io.BytesIO(); fig.savefig(buf, format="png", bbox_inches="tight")
    Image.open(buf).convert("RGB").save(path, "WEBP", quality=62, method=6)

def job(args):
    e, day, out = args
    name = f"{e['key']}_{day['date']}.webp"; path = os.path.join(out, "synoptic", name)
    print("start", e["key"], day["date"], flush=True)
    try:
        f = fields(day["date"])
        if not os.path.exists(path): render(e, day["date"], f, path)
        return e["key"], day["date"], dict(explain(e, day, f), img="synoptic/" + name)
    except Exception as ex:
        print("fail", e["key"], day["date"], ex, file=sys.stderr); return e["key"], day["date"], None

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--out", default="event_climate"); ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--limit", type=int, default=0); a = ap.parse_args()
    os.makedirs(os.path.join(a.out, "synoptic"), exist_ok=True)
    sp = os.path.join(a.out, "synoptic.json")
    syn = json.load(open(sp, encoding="utf-8")) if os.path.exists(sp) else {}
    eds = json.load(open(os.path.join(a.out, "editions.json"), encoding="utf-8"))["editions"]
    todo = [(e, d, a.out) for e in eds for d in e["days"] if d["date"] not in syn.get(e["key"], {})
            or not os.path.exists(os.path.join(a.out, syn[e["key"]][d["date"]]["img"]))]
    if a.limit: todo = todo[:a.limit]
    print("synoptic to do", len(todo)); ds()
    with ThreadPoolExecutor(a.workers) as ex:
        for n, (k, date, r) in enumerate(ex.map(job, todo)):
            if r: syn.setdefault(k, {})[date] = r
            if n % 10 == 9:  # 中途存檔
                json.dump(syn, open(sp, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":")); print("progress", n + 1, flush=True)
    keys = {e["key"] for e in eds}; syn = {k: v for k, v in syn.items() if k in keys}
    json.dump(syn, open(sp, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print("synoptic done", sum(len(v) for v in syn.values()))

if __name__ == "__main__": main()
