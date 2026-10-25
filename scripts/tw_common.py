"""tw-weather 共用：時間、數值、色階、縣市名正規化（只用 Python 內建模組，--check 快速檢查也能用）"""
import datetime as dt
import os

SCRIPT_VERSION = "tw-weather 1.0"
TPE = dt.timezone(dt.timedelta(hours=8))
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DISCLAIMER = "氣象資料僅供參考，預報請依氣象署發布為準，並勿作為重大決策使用"
HISTORY_HOURS = 48

COUNTY_ORDER = ["基隆市", "臺北市", "新北市", "桃園市", "新竹市", "新竹縣", "苗栗縣", "臺中市", "彰化縣", "南投縣", "雲林縣",
                "嘉義市", "嘉義縣", "臺南市", "高雄市", "屏東縣", "宜蘭縣", "花蓮縣", "臺東縣", "澎湖縣", "金門縣", "連江縣"]


def norm_name(s):
    """臺/台 統一成「臺」、去空白（縣市、鄉鎮、場館比對共用）"""
    return str(s or "").replace("台", "臺").replace(" ", "").replace("\u3000", "").strip()


def now_tpe():
    return dt.datetime.now(TPE)


def parse_t(s):
    """ISO 時間（含 +08:00）→ aware datetime；失敗回 None"""
    if not s or str(s).startswith("-99"):
        return None
    try:
        t = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=TPE)
        return t.astimezone(TPE)
    except Exception:
        return None


def iso(t):
    return t.astimezone(TPE).strftime("%Y-%m-%dT%H:%M+08:00") if t else None


def fkey(t):
    """逐時影格代碼：YYYYMMDDHH（臺灣時間）"""
    return t.astimezone(TPE).strftime("%Y%m%d%H")


def num(v, lo=-90.0):
    """氣象署缺值（-99、-999、-998、空字串、'X'）→ None"""
    try:
        x = float(v)
    except Exception:
        return None
    if x != x or x <= lo:
        return None
    return x


# ------------------------------------------------------------------ 色階（PNG 與網頁互動地圖共用，輸出到 scales.json）
RAIN_COLORS = ["#9bffff", "#00cfff", "#0198ff", "#0165ff", "#309901", "#32ff00", "#f8ff00", "#ffcb00",
               "#ff9a00", "#fa0300", "#cc0003", "#a00000", "#98009a", "#c304cc", "#f805f3", "#fecbff"]
TEMP_COLORS = ["#1a237e", "#283593", "#1e50a2", "#1565c0", "#1e88e5", "#29b6f6", "#4dd0e1", "#26a69a",
               "#43a047", "#7cb342", "#c0ca33", "#fdd835", "#ffb300", "#fb8c00", "#f4511e", "#e53935",
               "#c62828", "#ad1457", "#8e24aa", "#6a1b9a"]
WIND_COLORS = ["#e0f2f1", "#b2dfdb", "#80cbc4", "#4db6ac", "#a5d6a7", "#fff176", "#ffd54f", "#ffb74d",
               "#ff8a65", "#f4511e", "#d32f2f", "#ad1457", "#7b1fa2", "#4a148c"]
PRES_COLORS = ["#4a148c", "#6a1b9a", "#8e24aa", "#3949ab", "#1e88e5", "#29b6f6", "#4dd0e1", "#81c784",
               "#c5e1a5", "#fff59d", "#ffcc80", "#ffab91", "#ef9a9a", "#e57373", "#c62828"]
BEAUFORT = [0.3, 1.6, 3.4, 5.5, 8.0, 10.8, 13.9, 17.2, 20.8, 24.5, 28.5, 32.7, 37.0]

SCALES = {
    "temp": {"unit": "°C", "bounds": [0, 4, 8, 10, 12, 14, 16, 18, 20, 22, 24, 26, 28, 30, 32, 34, 35, 36, 37, 38],
             "colors": TEMP_COLORS, "below": "#0d1b4c", "zero": None},
    "rain10": {"unit": "mm", "bounds": [0.5, 1, 1.5, 2, 3, 4, 5, 6, 8, 10, 12, 15, 20, 25, 30, 40], "colors": RAIN_COLORS},
    "rain1": {"unit": "mm", "bounds": [1, 2, 5, 10, 15, 20, 25, 30, 40, 50, 60, 70, 80, 90, 100, 120], "colors": RAIN_COLORS},
    "rain6": {"unit": "mm", "bounds": [1, 2, 6, 10, 15, 20, 30, 40, 50, 70, 90, 110, 130, 150, 200, 300], "colors": RAIN_COLORS},
    "rain24": {"unit": "mm", "bounds": [1, 2, 6, 10, 15, 20, 30, 40, 50, 70, 90, 110, 130, 150, 200, 300], "colors": RAIN_COLORS},
    "rain72": {"unit": "mm", "bounds": [1, 5, 10, 20, 30, 50, 80, 100, 130, 150, 200, 300, 400, 500, 600, 800], "colors": RAIN_COLORS},
    "pres": {"unit": "hPa", "bounds": [970, 985, 995, 1000, 1004, 1006, 1008, 1010, 1012, 1014, 1016, 1018, 1020, 1024, 1028],
             "colors": PRES_COLORS},
    "wind": {"unit": "m/s", "bounds": BEAUFORT, "colors": WIND_COLORS[:len(BEAUFORT)], "levels": list(range(1, 14))},
}

# 變數定義：key → 標題、色階、鄉鎮彙整規則、排名方向
#   agg: max / min / rep（代表站＝鄉鎮內海拔最低、且有值的測站）/ dir（風速最大測站的風向）
VARS = [
    {"key": "t",    "group": "氣溫", "name": "目前氣溫",        "scale": "temp",   "agg": "rep", "rank": "max", "fmt": "%.1f"},
    {"key": "tx",   "group": "氣溫", "name": "今日最高氣溫",    "scale": "temp",   "agg": "max", "rank": "max", "fmt": "%.1f"},
    {"key": "tn",   "group": "氣溫", "name": "今日最低氣溫",    "scale": "temp",   "agg": "min", "rank": "min", "fmt": "%.1f"},
    {"key": "r10m", "group": "雨量", "name": "10 分鐘雨量",      "scale": "rain10", "agg": "max", "rank": "max", "fmt": "%.1f"},
    {"key": "r1h",  "group": "雨量", "name": "1 小時雨量",       "scale": "rain1",  "agg": "max", "rank": "max", "fmt": "%.1f"},
    {"key": "r6h",  "group": "雨量", "name": "6 小時累積雨量",   "scale": "rain6",  "agg": "max", "rank": "max", "fmt": "%.1f"},
    {"key": "r24h", "group": "雨量", "name": "24 小時累積雨量（1 日）", "scale": "rain24", "agg": "max", "rank": "max", "fmt": "%.1f"},
    {"key": "r2d",  "group": "雨量", "name": "2 日累積雨量",     "scale": "rain72", "agg": "max", "rank": "max", "fmt": "%.1f"},
    {"key": "r3d",  "group": "雨量", "name": "3 日累積雨量",     "scale": "rain72", "agg": "max", "rank": "max", "fmt": "%.1f"},
    {"key": "p",    "group": "氣壓", "name": "海平面氣壓（換算）", "scale": "pres",  "agg": "rep", "rank": "min", "fmt": "%.1f"},
    {"key": "ws",   "group": "平均風", "name": "平均風（逐時）",  "scale": "wind",   "agg": "max", "rank": "max", "fmt": "%.1f"},
    {"key": "wsx",  "group": "平均風", "name": "今日最大平均風",  "scale": "wind",   "agg": "max", "rank": "max", "fmt": "%.1f"},
    {"key": "wd",   "group": "風向", "name": "風向",            "scale": "wind",   "agg": "dir", "rank": "max", "fmt": "%.0f"},
    {"key": "g",    "group": "陣風", "name": "最大陣風（逐時）",  "scale": "wind",   "agg": "max", "rank": "max", "fmt": "%.1f"},
    {"key": "gx",   "group": "陣風", "name": "今日最大陣風",    "scale": "wind",   "agg": "max", "rank": "max", "fmt": "%.1f"},
]
VAR_BY_KEY = {v["key"]: v for v in VARS}
AGG_TEXT = {
    "max": "鄉鎮內各測站取最大值",
    "min": "鄉鎮內各測站取最小值",
    "rep": "鄉鎮內海拔最低的測站（代表平地）",
    "dir": "鄉鎮內平均風最強測站的風向",
}


def color_for(scale_key, v):
    """數值 → 色碼（低於第一級回 None＝不上色）"""
    if v is None:
        return None
    sc = SCALES[scale_key]
    b, c = sc["bounds"], sc["colors"]
    if v < b[0]:
        return sc.get("below")
    i = 0
    while i + 1 < len(b) and v >= b[i + 1]:
        i += 1
    return c[min(i, len(c) - 1)]


def beaufort(ws):
    if ws is None:
        return None
    lv = 0
    for i, th in enumerate(BEAUFORT):
        if ws >= th:
            lv = i + 1
    return lv


def compass16(deg):
    if deg is None:
        return ""
    names = ["北", "北北東", "東北", "東北東", "東", "東南東", "東南", "南南東", "南", "南南西", "西南", "西南西", "西", "西北西", "西北", "北北西"]
    return names[int((deg % 360) / 22.5 + 0.5) % 16]


def sea_level_pressure(ps, alt, t):
    """測站氣壓 → 海平面氣壓（標準大氣近似，海拔 500 m 以上不換算，回 None）"""
    if ps is None or alt is None or alt > 500:
        return None
    tc = t if t is not None else 25.0
    return ps * (1 - 0.0065 * alt / (tc + 0.0065 * alt + 273.15)) ** -5.257
