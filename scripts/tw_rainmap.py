# -*- coding: utf-8 -*-
"""v22.4 場館降雨地圖（tw-weather 10 分鐘那一輪順便畫）
資料：中央氣象署 O-A0059-001 雷達合成回波、F-B0046-001 未來 1 小時雷達定量降雨（每 10 分鐘）
來源：S3 公開鏡像（不用金鑰）；失敗才用 CWA_API_KEY 打檔案下載 API（金鑰絕不印出）
輸出：<out>/img/rain/<場館代碼>.png、<out>/img/rain/manifest.json（Apps Script 讀這個判斷資料新舊）
任何錯誤都不影響其他步驟（workflow 也設 continue-on-error）
"""
import argparse, datetime as dt, json, math, os, sys, time, urllib.request

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.collections import PathCollection, LineCollection
from matplotlib.colors import ListedColormap, BoundaryNorm

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tw_render as R

# 場館清單（照 code.gs VENUE_META 的名稱與 WGS84 座標；新增場館時兩邊一起加）
# key: (名稱, 緯度, 經度, "縣市|行政區", 舊有人站清單＝只參考，v22.5 雨量圖改用 O-A0002 最近站)
VENUES = {
    "huabo": ("花博爭豔館", 25.06980, 121.52110, "臺北市|中山區", ['466920', 'C0AH70']),
    "underground": ("台北地下街", 25.04930, 121.51210, "臺北市|中正區", ['466920']),
    "worldtrade": ("世貿一館", 25.03430, 121.56260, "臺北市|信義區", ['C0AC70', '466920']),
    "songshan_culture": ("松山文創園區", 25.04390, 121.56040, "臺北市|信義區", ['C0AC70', 'C0AH70']),
    "nangang": ("南港展覽館", 25.05660, 121.61800, "臺北市|南港區", ['CAA040']),
    "hakka_park": ("客家文化主題公園", 25.02040, 121.52530, "臺北市|中正區", ['466920']),
    "ntu": ("台大綜合體育館", 25.02150, 121.53510, "臺北市|大安區", ['A0A010']),
    "shuxin": ("樹心會館", 25.04000, 121.50680, "臺北市|中正區", ['466920']),
    "longshan": ("龍山文創基地", 25.03650, 121.49950, "臺北市|萬華區", ['466920']),
    "jiantan": ("劍潭青年活動中心", 25.07780, 121.52440, "臺北市|士林區", ['C0A770']),
    "idealspace": ("理想間", 25.08380, 121.55070, "臺北市|士林區", ['C0A770']),
    "dianspace": ("典空間", 25.05170, 121.53790, "臺北市|中山區", ['466920', 'C0AH70']),
    "bottle_factory": ("南港瓶蓋工廠", 25.05360, 121.60460, "臺北市|南港區", ['CAA040']),
    "music_center": ("台北流行音樂中心", 25.05170, 121.59770, "臺北市|南港區", ['CAA040']),
    "gengxin": ("耕莘文教院", 25.01900, 121.52890, "臺北市|大安區", ['A0A010']),
    "redspace": ("Red space 多元商務空間", 25.04870, 121.54430, "臺北市|中山區", ['466920']),
    "sanchong": ("三重綜合體育館", 25.06190, 121.49140, "新北市|三重區", ['C0AI30']),
    "fcu": ("逢甲大學", 24.17950, 120.64680, "臺中市|西屯區", ['C0F9T0', '467490']),
    "nchu": ("中興大學", 24.12380, 120.67520, "臺中市|南區", ['467490']),
    "tciec": ("臺中國際展覽館", 24.10650, 120.61220, "臺中市|烏日區", ['C0F9S0']),
    "chaoma": ("臺中市朝馬國民運動中心", 24.16790, 120.63380, "臺中市|西屯區", ['C0F9T0']),
    "tcwtc": ("台中世界貿易中心", 24.17630, 120.61640, "臺中市|西屯區", ['C0F9T0']),
    "culturepark_tc": ("文化部文化資產園區", 24.13320, 120.68050, "臺中市|南區", ['467490']),
    "words_live": ("沃茲新創空間", 24.14550, 120.68770, "臺中市|中區", ['467490']),
    "yuntech": ("國立雲林科技大學", 23.69600, 120.53410, "雲林縣|斗六市", ['C0K400']),
    "ncku": ("國立成功大學", 22.99910, 120.21720, "臺南市|東區", ['467410']),
    "ksu": ("崑山科技大學", 22.99780, 120.25310, "臺南市|永康區", ['467420']),
    "tut": ("台南應用科技大學", 23.03930, 120.23910, "臺南市|永康區", ['467420']),
    "ten_drums": ("十鼓文創園區", 22.93850, 120.22950, "臺南市|仁德區", ['C0X160']),
    "tainan_ecc": ("大臺南會展中心", 22.92140, 120.28520, "臺南市|歸仁區", ['C0X160']),
    "kh_station": ("高雄車站", 22.63940, 120.30260, "高雄市|三民區", ['C0V700']),
    "formosa_blvd": ("美麗島站", 22.63130, 120.30200, "高雄市|新興區", ['C0V490']),
    "kh_arena": ("高雄巨蛋", 22.66850, 120.30230, "高雄市|左營區", ['C0V810']),
    "skm_zuoying": ("新光三越高雄左營店本館", 22.68900, 120.31020, "高雄市|左營區", ['C0V810']),
    "qiaotou": ("橋頭糖廠", 22.75790, 120.31410, "高雄市|橋頭區", ['C0V760']),
    "pier2": ("駁二藝術特區", 22.62060, 120.28130, "高雄市|鹽埕區", ['C0V690']),
    "kh_social": ("高雄社教館", 22.56530, 120.35930, "高雄市|小港區", ['C0V890']),
    "ncku_biz": ("成大商圈會館", 22.99590, 120.21840, "臺南市|東區", ['467410']),
    "mimi_base": ("覓蜜基地", 22.74480, 120.31840, "高雄市|橋頭區", ['C0V760']),
    "nkust_nanzi": ("高科大楠梓校區", 22.72640, 120.31470, "高雄市|楠梓區", ['467440']),
    "ntsus": ("國立臺灣體育運動大學體育館", 24.14920, 120.68790, "臺中市|北區", ['467490']),
    "breeze": ("微風廣場", 25.04580, 121.54500, "臺北市|松山區", ['C0AH70']),
    "taipei_moc": ("臺北文創", 25.04450, 121.56130, "臺北市|松山區", ['C0AH70']),
    "taichung_cityhall": ("臺中市政府惠中樓", 24.16223863373963, 120.64586771363027, "臺中市|西屯區", ['C0F9T0']),
    "taichung_icec": ("臺中國際會展中心", 24.19330567094997, 120.65044788404585, "臺中市|西屯區", ['C0F9T0']),
    "nuli_space": ("女力空間", 25.04222, 121.56542, "臺北市|信義區", ['C0AC70']),
    "kh_exhibition": ("高雄展覽館", 22.60789376549128, 120.29847137572354, "高雄市|前鎮區", ['C0V890']),
}

# 淹水警戒門檻（照 code.gs FLOOD_THRESHOLDS；mm）
FLOOD_THRESHOLDS = {
    "臺北市|松山區": {'h1_2': 55, 'h1_1': 65, 'h3_2': 110, 'h3_1': 120, 'h6_2': 150, 'h6_1': 170},
    "臺北市|中山區": {'h1_2': 55, 'h1_1': 65, 'h3_2': 100, 'h3_1': 110, 'h6_2': 145, 'h6_1': 165},
    "臺北市|中正區": {'h1_2': 50, 'h1_1': 60, 'h3_2': 105, 'h3_1': 115, 'h6_2': 140, 'h6_1': 160},
    "臺北市|信義區": {'h1_2': 50, 'h1_1': 60, 'h3_2': 100, 'h3_1': 110, 'h6_2': 145, 'h6_1': 165},
    "臺北市|南港區": {'h1_2': 55, 'h1_1': 65, 'h3_2': 110, 'h3_1': 120, 'h6_2': 150, 'h6_1': 170},
    "臺北市|大安區": {'h1_2': 55, 'h1_1': 65, 'h3_2': 100, 'h3_1': 110, 'h6_2': 140, 'h6_1': 160},
    "臺北市|萬華區": {'h1_2': 50, 'h1_1': 60, 'h3_2': 105, 'h3_1': 115, 'h6_2': 145, 'h6_1': 165},
    "臺北市|士林區": {'h1_2': 50, 'h1_1': 60, 'h3_2': 100, 'h3_1': 110, 'h6_2': 145, 'h6_1': 165},
    "新北市|三重區": {'h1_2': 40, 'h1_1': 50, 'h3_2': 100, 'h3_1': 110, 'h6_2': 140, 'h6_1': 160},
    "臺中市|西屯區": {'h1_2': 50, 'h1_1': 60, 'h3_2': 100, 'h3_1': 110, 'h6_2': 140, 'h6_1': 160},
    "臺中市|南區": {'h1_2': 50, 'h1_1': 60, 'h3_2': 100, 'h3_1': 110, 'h6_2': 135, 'h6_1': 155},
    "臺中市|烏日區": {'h1_2': 40, 'h1_1': 50, 'h3_2': 90, 'h3_1': 110, 'h6_2': 130, 'h6_1': 130},
    "臺中市|東區": {'h1_2': 45, 'h1_1': 55, 'h3_2': 105, 'h3_1': 115, 'h6_2': 140, 'h6_1': 160},
    "臺中市|中區": {'h1_2': 45, 'h1_1': 55, 'h3_2': 100, 'h3_1': 110, 'h6_2': 140, 'h6_1': 160},
    "雲林縣|斗六市": {'h1_2': 50, 'h1_1': 60, 'h3_2': 110, 'h3_1': 120, 'h6_2': 150, 'h6_1': 170},
    "臺南市|東區": {'h1_2': 40, 'h1_1': 50, 'h3_2': 95, 'h3_1': 105, 'h6_2': 130, 'h6_1': 150},
    "臺南市|永康區": {'h1_2': 40, 'h1_1': 50, 'h3_2': 85, 'h3_1': 95, 'h6_2': 120, 'h6_1': 140},
    "臺南市|仁德區": {'h1_2': 40, 'h1_1': 50, 'h3_2': 90, 'h3_1': 100, 'h6_2': 120, 'h6_1': 150},
    "臺南市|歸仁區": {'h1_2': 50, 'h1_1': 60, 'h3_2': 90, 'h3_1': 100, 'h6_2': 130, 'h6_1': 150},
    "高雄市|三民區": {'h1_2': 50, 'h1_1': 60, 'h3_2': 110, 'h3_1': 120, 'h6_2': 150, 'h6_1': 170},
    "高雄市|新興區": {'h1_2': 45, 'h1_1': 55, 'h3_2': 110, 'h3_1': 120, 'h6_2': 145, 'h6_1': 165},
    "高雄市|左營區": {'h1_2': 45, 'h1_1': 55, 'h3_2': 100, 'h3_1': 110, 'h6_2': 140, 'h6_1': 160},
    "高雄市|橋頭區": {'h1_2': 40, 'h1_1': 50, 'h3_2': 90, 'h3_1': 100, 'h6_2': 130, 'h6_1': 150},
    "高雄市|鹽埕區": {'h1_2': 45, 'h1_1': 55, 'h3_2': 105, 'h3_1': 115, 'h6_2': 150, 'h6_1': 170},
    "高雄市|鳳山區": {'h1_2': 40, 'h1_1': 50, 'h3_2': 100, 'h3_1': 110, 'h6_2': 140, 'h6_1': 160},
    "臺中市|北區": {'h1_2': 45, 'h1_1': 55, 'h3_2': 100, 'h3_1': 110, 'h6_2': 140, 'h6_1': 160},
    "高雄市|楠梓區": {'h1_2': 45, 'h1_1': 55, 'h3_2': 100, 'h3_1': 110, 'h6_2': 140, 'h6_1': 160},
    "高雄市|小港區": {'h1_2': 45, 'h1_1': 55, 'h3_2': 105, 'h3_1': 115, 'h6_2': 145, 'h6_1': 165},
    "高雄市|前鎮區": {'h1_2': 45, 'h1_1': 55, 'h3_2': 105, 'h3_1': 115, 'h6_2': 145, 'h6_1': 165},
}

S3 = "https://cwaopendata.s3.ap-northeast-1.amazonaws.com/"
SRC = {"radar": ("Observation/O-A0059-001.json", "O-A0059-001"),
       "qpf": ("Forecast/F-B0046-001.json", "F-B0046-001")}
HALF = 0.45          # 地圖上下各 0.45° ≈ ±50 km
NEAR_KM = 5.0        # 「場館附近」最大值的半徑
# 格點是 TWD67；換到 WGS84（場館座標、行政界）約 經度 +0.0081°、緯度 −0.0019°（TM2 ΔE≈828 m、ΔN≈−207 m）
D67_LON, D67_LAT = 0.0081, -0.0019

DBZ_B = list(range(0, 70, 5))
DBZ_C = ["#00ffff", "#00a3ff", "#005bff", "#0000ff", "#00ff00", "#00d300", "#00a500", "#ffff00", "#ffd300",
         "#ffa500", "#ff0000", "#d30000", "#a50000", "#ff00ff"]
QPF_B = [1, 2, 6, 10, 15, 20, 30, 40, 50, 70, 90, 110, 130, 150, 200, 300]
QPF_C = ["#c1c1c1", "#99ffff", "#00ccff", "#0099ff", "#0066ff", "#339900", "#33ff00", "#ffff00", "#ffcc00",
         "#ff9900", "#ff0000", "#cc0000", "#990000", "#990099", "#cc00cc", "#ff00ff"]


def fetch(kind):
    path, did = SRC[kind]
    urls = [S3 + path]
    key = os.environ.get("CWA_API_KEY", "").strip()
    if key:
        urls.append("https://opendata.cwa.gov.tw/fileapi/v1/opendataapi/%s?Authorization=%s&format=JSON" % (did, key))
    last = None
    for i, u in enumerate(urls):
        for k in range(2):
            try:
                with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "tw-weather"}), timeout=60) as r:
                    return json.loads(r.read().decode("utf-8"))
            except Exception as e:
                last = "%s（%s）" % (type(e).__name__, "S3" if i == 0 else "API")   # 不印網址（可能含金鑰）
                time.sleep(3)
    raise RuntimeError("%s 下載失敗：%s" % (did, last))


def grid(d):
    ds = d["cwaopendata"]["dataset"]
    p = ds["datasetInfo"]["parameterSet"]
    nx, ny = int(p["GridDimensionX"]), int(p["GridDimensionY"])
    v = np.array(ds["contents"]["content"].split(","), dtype=np.float32).reshape(ny, nx)
    lon0 = float(p["StartPointLongitude"]) + D67_LON
    lat0 = float(p["StartPointLatitude"]) + D67_LAT
    return {"v": v, "lon0": lon0, "lat0": lat0, "res": float(p["GridResolution"]), "t": p["DateTime"]}


def near_max(G, lat, lng, km=NEAR_KM):
    v, res = G["v"], G["res"]
    j0, i0 = round((lat - G["lat0"]) / res), round((lng - G["lon0"]) / res)
    nj = int(km / 111.0 / res) + 1
    ni = int(km / (111.0 * math.cos(math.radians(lat))) / res) + 1
    best = None
    for j in range(j0 - nj, j0 + nj + 1):
        for i in range(i0 - ni, i0 + ni + 1):
            if 0 <= j < v.shape[0] and 0 <= i < v.shape[1]:
                dy = (j - j0) * res * 111.0
                dx = (i - i0) * res * 111.0 * math.cos(math.radians(lat))
                if dx * dx + dy * dy <= km * km and v[j, i] >= 0:
                    best = float(v[j, i]) if best is None else max(best, float(v[j, i]))
    return None if best is None else round(best, 1)


def build_fig(towns, lines, G1, G2):
    fig, axs = plt.subplots(1, 2, figsize=(12, 7.4), dpi=100)
    for ax, G, b, c, ttl, unit in ((axs[0], G1, DBZ_B, DBZ_C, "雷達合成回波", "dBZ"),
                                   (axs[1], G2, QPF_B, QPF_C, "未來 1 小時定量降雨預報", "mm")):
        ax.set_facecolor(R.SEA)
        ax.add_collection(PathCollection([t["path"] for t in towns.values() if t["path"] is not None],
                                         facecolor=R.LAND, edgecolor="none", zorder=1))
        v = np.where(G["v"] < b[0], np.nan, G["v"])
        ny, nx = v.shape
        r = G["res"]
        ext = [G["lon0"] - r / 2, G["lon0"] + r * (nx - .5), G["lat0"] - r / 2, G["lat0"] + r * (ny - .5)]
        cm = ListedColormap(c)
        nm = BoundaryNorm(b + [b[-1] + (b[-1] - b[-2])], cm.N)
        im = ax.imshow(v, origin="lower", extent=ext, cmap=cm, norm=nm, alpha=.85, zorder=2, interpolation="nearest")
        for kind, lw, col in ((2, .3, "#b0b0b0"), (1, .9, "#555555"), (0, .9, "#333333")):
            ax.add_collection(LineCollection([np.asarray(a)[:, :2] for a in lines.get(kind, [])], lw=lw, colors=col, zorder=3))
        ax.set_xticks([]); ax.set_yticks([]); ax.set_aspect(R.ASPECT)
        tt = dt.datetime.fromisoformat(G["t"]).strftime("%m/%d %H:%M")
        ax.set_title("%s\n%s（台北時間）" % (ttl, tt), fontsize=15, fontweight="bold")
        cb = plt.colorbar(im, ax=ax, orientation="horizontal", fraction=.05, pad=.03, ticks=b)
        cb.ax.tick_params(labelsize=8); cb.set_label(unit, fontsize=10)
    marks = [ax.plot([], [], marker="X", ms=18, mfc="#e01010", mec="white", mew=2, ls="none", zorder=5)[0] for ax in axs]
    sup = fig.suptitle("", fontsize=17, fontweight="bold", y=.99)
    fig.text(.5, .01, "資料：中央氣象署 O-A0059-001 雷達合成回波、F-B0046-001 雷達定量降雨預報（每 10 分鐘）｜×＝場館｜同人場次天氣儀表板",
             ha="center", fontsize=9, color="#555555")
    fig.tight_layout(rect=[0, .03, 1, .96])
    return fig, axs, marks, sup


# ------------------------------------------------------------------ 雨量站逐時圖（大雨特報／淹水燈號附圖）
# v22.5：測站＝儀表板「累積雨量」工作表同一站（code.gs syncRainfallToSheet）：
#   O-A0002-001 自動雨量站全部測站，取 WGS84 座標（沒有就用第一組），與 VENUE_META 座標算大圓距離（R=6371 km），最近的那一站
#   （不再用上面 VENUES 第 5 欄的有人站清單；那欄只留著參考）
# 逐時資料：tw-weather 自己的 48h 歷史 site/data/history/store.json.gz（每小時影格，r1h/r24h 來自 O-A0002；不另打 API）
# 最新數值：本輪下載的 O-A0002-001（與儀表板同一份 10 分鐘資料）
TPE = dt.timezone(dt.timedelta(hours=8))
# 儀表板「☔ 氣象署降雨特報／雨量燈號」RAIN_YELLOW／RAIN_HEAVY／RAIN_TORRENTIAL／RAIN_EXTREME 門檻（mm）：(名稱, 1h, 3h, 24h)
CWA_RAIN = [("大雨", 40, None, 80), ("豪雨", None, 100, 200), ("大豪雨", None, 200, 350), ("超大豪雨", None, None, 500)]
LVL_COL = {"大雨": "#f1c40f", "豪雨": "#e67e22", "大豪雨": "#e74c3c", "超大豪雨": "#8e44ad"}


SRC["rain"] = ("Observation/O-A0002-001.json", "O-A0002-001")


def _haversine_km(lat1, lon1, lat2, lon2):
    """與 code.gs haversineKm_ 相同公式"""
    r = math.pi / 180
    dla, dlo = (lat2 - lat1) * r, (lon2 - lon1) * r
    a = math.sin(dla / 2) ** 2 + math.cos(lat1 * r) * math.cos(lat2 * r) * math.sin(dlo / 2) ** 2
    return 2 * 6371 * math.asin(min(1.0, math.sqrt(a)))


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f <= -90 else f


def rain_stations(doc):
    """O-A0002-001 → [{id,name,lat,lng,t,h1,h3,h24,now}]；座標規則同 code.gs rainStationLatLng_"""
    ds = (doc.get("cwaopendata") or {}).get("dataset") or (doc.get("records") or {})
    out = []
    for s in ds.get("Station") or []:
        g = s.get("GeoInfo") or {}
        co = g.get("Coordinates") or []
        if isinstance(co, dict):
            co = [co]
        lat = lng = None
        for c in co:
            la = _num(c.get("StationLatitude", c.get("Latitude")))
            lo = _num(c.get("StationLongitude", c.get("Longitude")))
            if la is None or lo is None:
                continue
            if "WGS84" in str(c.get("CoordinateName") or "") or lat is None:
                lat, lng = la, lo
        if lat is None:
            continue
        re_ = s.get("RainfallElement") or {}
        pv = lambda k: _num((re_.get(k) or {}).get("Precipitation"))
        out.append({"id": str(s.get("StationId") or ""), "name": s.get("StationName") or "", "lat": lat, "lng": lng,
                    "t": (s.get("ObsTime") or {}).get("DateTime") or "",
                    "h1": pv("Past1hr"), "h3": pv("Past3hr"), "h24": pv("Past24hr"), "now": pv("Now")})
    return out


def nearest_rain_station(stations, lat, lng):
    """同 code.gs：逐站比較，距離「嚴格小於」才換（同距離取先出現的）"""
    best, bd = None, 1e9
    for st in stations:
        d = _haversine_km(lat, lng, st["lat"], st["lng"])
        if d < bd:
            best, bd = st, d
    return best, (round(bd, 3) if best else None)


def load_store(out, pages_base):
    import gzip
    fp = os.path.join(out, "data", "history", "store.json.gz")
    raw = open(fp, "rb").read() if os.path.exists(fp) else None
    if raw is None and pages_base:
        with urllib.request.urlopen(pages_base + "data/history/store.json.gz?ts=%d" % time.time(), timeout=60) as r:
            raw = r.read()
    try:
        return json.loads(gzip.decompress(raw).decode("utf-8"))
    except OSError:
        return json.loads(raw.decode("utf-8"))


def station_series(st, sid):
    ki = {k: i for i, k in enumerate(st["keys"])}
    ts, r1, r24 = [], [], []
    for f in st["frames"]:
        row = f["d"].get(sid)
        ts.append(dt.datetime.fromisoformat(f["t"]).astimezone(TPE))
        r1.append(row[ki["r1h"]] if row else None)
        r24.append(row[ki["r24h"]] if row else None)
    return ts, r1, r24


def gauge_stats(ts, r1, r24):
    """3 小時累積（連續 3 筆都有才算）、日累積（00 時那筆＝前一天 23–24 時，算進前一天）"""
    n = len(ts)
    r3, rday = [None] * n, [None] * n
    acc, day = 0.0, None
    for i in range(n):
        d = (ts[i] - dt.timedelta(minutes=1)).date()
        if d != day:
            day, acc = d, 0.0
        if r1[i] is not None and r1[i] >= 0:
            acc += r1[i]
        rday[i] = round(acc, 1)
        w = [x for x in r1[max(0, i - 2):i + 1] if x is not None and x >= 0]
        r3[i] = round(sum(w), 1) if len(w) == min(3, i + 1) else None
    return r3, rday


def level_of(h1, h3, h24):
    lv = None
    for name, a, b, c in CWA_RAIN:
        if (a and h1 is not None and h1 >= a) or (b and h3 is not None and h3 >= b) or (c and h24 is not None and h24 >= c):
            lv = name
    return lv


def render_gauge(path, title, sid, sname, ts, r1, r24, flood=None, dkm=None):
    import matplotlib.dates as mdates
    r3, rday = gauge_stats(ts, r1, r24)
    fig, ax = plt.subplots(figsize=(12, 6.2), dpi=100)
    ax2 = ax.twinx()
    x = mdates.date2num(ts)
    v1 = [x_ if (x_ is not None and x_ >= 0) else 0 for x_ in r1]
    cols = []
    for i in range(len(ts)):
        lv = level_of(r1[i], r3[i], r24[i])
        cols.append(LVL_COL[lv] if lv else "#3b82f6")
    ax.bar(x, v1, width=0.8 / 24, color=cols, edgecolor="none", zorder=3, label="逐時雨量（左軸）")
    for i, val in enumerate(v1):
        if val >= 10:
            ax.text(x[i], val, "%g" % val, ha="center", va="bottom", fontsize=8, zorder=4)
    # 1 小時門檻：大雨 40；淹水警戒（儀表板 FLOOD_THRESHOLDS）
    ymax1 = max([v for v in v1] + [10]) * 1.25
    hl = [(40, "大雨 1h 40", LVL_COL["大雨"])]
    if flood:
        hl += [(flood["h1_2"], "淹水二級 1h %d" % flood["h1_2"], "#0ea5e9"), (flood["h1_1"], "淹水一級 1h %d" % flood["h1_1"], "#1d4ed8")]
    for y, lab, c in hl:
        if y <= ymax1 * 1.6:
            ax.axhline(y, color=c, lw=1.2, ls=":", zorder=2)
            ax.text(x[len(x) * 3 // 5], y, lab, color=c, fontsize=8, va="bottom", zorder=5)
    ax.set_ylim(0, max(ymax1, 52))
    ax.set_ylabel("逐時雨量 mm", fontsize=11)
    d_day = [v for v in rday]
    d24 = [v if (v is not None and v >= 0) else np.nan for v in r24]
    ax2.plot(x, d_day, color="#0f766e", lw=2.2, zorder=5, label="日累積（00 時起，右軸）")
    ax2.plot(x, d24, color="#7c3aed", lw=1.6, ls="--", zorder=5, label="24 小時累積（右軸）")
    ax2.plot(x, [v if v is not None else np.nan for v in r3], color="#c2410c", lw=1.2, ls=":", zorder=5, label="3 小時累積（右軸）")
    dmax = max([v for v in d_day if v is not None] + [v for v in d24 if v == v] + [v for v in r3 if v is not None] + [20])
    nxt = next((c for c in (80, 100, 200, 350, 500) if c > dmax), 500)
    ymax2 = max(dmax * 1.15, nxt * 1.12)
    for name, a, b, c in CWA_RAIN:
        if c <= ymax2:
            ax2.axhline(c, color=LVL_COL[name], lw=1.3, ls="-.", zorder=2)
            ax2.text(x[-1], c, "%s 24h %d " % (name, c), color=LVL_COL[name], fontsize=9, ha="right", va="bottom",
                     fontweight="bold", zorder=6)
    for name, c3 in (("豪雨", 100), ("大豪雨", 200)):
        if c3 <= ymax2:
            ax2.axhline(c3, color=LVL_COL[name], lw=1.0, ls=(0, (1, 3)), zorder=2)
            ax2.text(x[len(x) // 2], c3, "%s 3h %d" % (name, c3), color=LVL_COL[name], fontsize=8, ha="center", va="bottom", zorder=6)
    ax2.set_ylim(0, ymax2)
    ax2.set_ylabel("累積雨量 mm", fontsize=11)
    # 標出第一次達到各級的時間點
    seen = set()
    for i in range(len(ts)):
        lv = level_of(r1[i], r3[i], r24[i])
        if lv and lv not in seen:
            seen.add(lv)
            ax.annotate("達%s\n%s" % (lv, ts[i].strftime("%m/%d %H時")), (x[i], v1[i]), xytext=(-40, 30),
                         textcoords="offset points", ha="center", fontsize=9, color=LVL_COL[lv], fontweight="bold",
                         arrowprops=dict(arrowstyle="->", color=LVL_COL[lv]), zorder=7)
    ax.xaxis.set_major_locator(mdates.HourLocator(byhour=[0, 6, 12, 18], tz=TPE))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d\n%H時", tz=TPE))
    ax.grid(axis="y", color="#e5e7eb", zorder=0)
    ax.set_xlim(x[0] - 0.03, x[-1] + 0.03)
    h1, l1 = ax.get_legend_handles_labels(); h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, loc="upper left", fontsize=9, framealpha=.9)
    last = len(ts) - 1
    ax.set_title("%s｜%s（%s%s）逐時雨量\n整點 %s：1h %s、3h %s、24h %s、今日 %s mm" % (
        title, sname, sid, ("，距場館 %.1f km" % dkm) if dkm is not None else "", ts[last].strftime("%m/%d %H:%M"), fmt(r1[last]), fmt(r3[last]), fmt(r24[last]), fmt(rday[last])),
        fontsize=14, fontweight="bold")
    fig.text(.5, .01, "資料：中央氣象署 自動雨量站 O-A0002-001（場館最近站＝儀表板「累積雨量」同站）逐時｜長條顏色＝達該級（大雨黃、豪雨橘、大豪雨紅、超大豪雨紫）｜同人場次天氣儀表板",
             ha="center", fontsize=8.5, color="#555555")
    fig.tight_layout(rect=[0, .03, 1, 1])
    fig.savefig(path); plt.close(fig)
    return {"r1h": r1[last], "r3h": r3[last], "r24h": r24[last], "rday": rday[last],
            "level": level_of(r1[last], r3[last], r24[last]), "t": ts[last].isoformat(timespec="minutes")}


def fmt(v):
    return "—" if v is None or v < 0 else ("%g" % v)


def render_gauges(out, man, keys, pages_base, demo=None, rain_file=None):
    st = load_store(out, pages_base)
    if not st.get("frames"):
        raise RuntimeError("沒有 48h 歷史")
    doc = json.load(open(rain_file, encoding="utf-8")) if rain_file else fetch("rain")
    stations = rain_stations(doc)
    if len(stations) < 100:
        raise RuntimeError("O-A0002-001 測站太少（%d），不畫雨量圖以免選錯站" % len(stations))
    odir = os.path.join(out, "img", "rain")
    done = {}
    jobs = []
    for k in keys:
        name, lat, lng, cd = VENUES[k][:4]
        rs, dkm = nearest_rain_station(stations, lat, lng)
        if rs:
            jobs.append((k, name, rs, dkm, FLOOD_THRESHOLDS.get(cd)))
    if demo:
        rs = next((x for x in stations if x["id"] == demo), None)
        if rs:
            jobs.append(("demo_" + demo, "示意", rs, None, None))
    for k, name, rs, dkm, flood in jobs:
        sid = rs["id"]
        try:
            if sid not in st.get("meta", {}):
                raise RuntimeError("48h 歷史沒有 %s（新站？下一輪就有）" % sid)
            ts, r1, r24 = station_series(st, sid)
            info = render_gauge(os.path.join(odir, k + "_gauge.png"), name, sid, rs["name"], ts, r1, r24, flood, dkm)
            info.update({"png": "img/rain/%s_gauge.png" % k, "sid": sid, "sname": rs["name"], "dist_km": dkm,
                         "src": "O-A0002-001"})
            # 最新 10 分鐘數值＝儀表板「累積雨量」同一份資料（逐時長條仍是整點影格）
            if rs["t"]:
                info.update({"t": rs["t"][:16] + rs["t"][19:] if len(rs["t"]) >= 25 else rs["t"],
                             "r1h": rs["h1"], "r3h": rs["h3"], "r24h": rs["h24"], "rday": rs["now"],
                             "level": level_of(rs["h1"], rs["h3"], rs["h24"])})
            man["venues"].setdefault(k, {"name": name})["gauge"] = info
            done[k] = 1
        except Exception as e:
            man["venues"].setdefault(k, {"name": name})["gauge_station"] = {"sid": sid, "sname": rs["name"], "dist_km": dkm}
            print("⚠️ 雨量圖 %s 失敗：%s" % (k, e))
    man["gauge_time"] = st["frames"][-1]["t"]
    man["gauge_station_rule"] = "O-A0002-001 最近站（同儀表板「累積雨量」）"
    print("雨量站逐時圖 %d 張（%d 站可選）" % (len(done), len(stations)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="site")
    ap.add_argument("--radar-file"); ap.add_argument("--qpf-file"); ap.add_argument("--rain-file")   # 本機測試用
    ap.add_argument("--only", help="只畫這些場館（逗號分隔，測試用）")
    ap.add_argument("--gauge-demo", help="另外畫一張指定雨量站的逐時圖（測試用）")
    a = ap.parse_args()
    t0 = time.time()
    G1 = grid(json.load(open(a.radar_file, encoding="utf-8")) if a.radar_file else fetch("radar"))
    G2 = grid(json.load(open(a.qpf_file, encoding="utf-8")) if a.qpf_file else fetch("qpf"))
    print("雷達 %s、QPF %s" % (G1["t"], G2["t"]))
    R.setup_fonts()
    towns, lines = R.load_geo()
    fig, axs, marks, sup = build_fig(towns, lines, G1, G2)
    odir = os.path.join(a.out, "img", "rain")
    os.makedirs(odir, exist_ok=True)
    keys = [k for k in VENUES if not a.only or k in a.only.split(",")]
    man = {"radar_time": G1["t"], "qpf_time": G2["t"],
           "generated": dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(timespec="seconds"),
           "near_km": NEAR_KM, "half_deg": HALF, "venues": {}}
    for k in keys:
        name, lat, lng = VENUES[k][:3]
        try:
            for ax, m in zip(axs, marks):
                ax.set_xlim(lng - HALF * R.ASPECT, lng + HALF * R.ASPECT); ax.set_ylim(lat - HALF, lat + HALF)
                m.set_data([lng], [lat])
            sup.set_text("× %s（%.4f, %.4f）" % (name, lat, lng))
            fig.savefig(os.path.join(odir, k + ".png"))
            man["venues"][k] = {"name": name, "png": "img/rain/%s.png" % k,
                                "dbz_max": near_max(G1, lat, lng), "qpf_max": near_max(G2, lat, lng)}
        except Exception as e:
            print("⚠️ %s 失敗：%s" % (k, e))
    try:
        render_gauges(a.out, man, keys, os.environ.get("PAGES_BASE", "").strip(), a.gauge_demo, a.rain_file)
    except Exception as e:
        print("⚠️ 雨量站逐時圖略過：%s" % e)
    with open(os.path.join(odir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False, indent=1)
    print("場館降雨地圖 %d 張，%.1f 秒" % (len(man["venues"]), time.time() - t0))


if __name__ == "__main__":
    main()
