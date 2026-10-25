"""tw-weather 空氣品質（環境部）共用：變數、色階、鄉鎮彙整規則（只用 Python 內建模組，--check 也能用）"""

AIR_VERSION = "tw-air 1.0"
AIR_DISCLAIMER = "空氣品質資料僅供參考，請依環境部發布為準，並勿作為重大決策使用"
AIR_SOURCE = "環境部 空氣品質指標 AQX_P_432（每小時）"
AIR_SOURCE_SHORT = "環境部"

# 環境部 AQI 官方顏色（良好、普通、對敏感族群不健康、對所有族群不健康、非常不健康、危害）
AQI_COLORS = ["#00e800", "#ffff00", "#ff7e00", "#ff0000", "#8f3f97", "#7e0023"]
AQI_LABELS = ["良好", "普通", "敏感族群\n不健康", "所有族群\n不健康", "非常不健康", "危害"]
# 沒有官方分級的污染物（NOx、NO）：只表示濃度高低（藍→紫，避免和 AQI 健康等級顏色混淆）
SEQ_COLORS = ["#eef2ff", "#c7d2fe", "#a5b4fc", "#818cf8", "#6366f1", "#4f46e5", "#4338ca", "#6d28d9", "#7e22ce", "#86198f"]

# 色階：bounds＝每一級的下限（≥ 下限就是這一級）；分級依環境部 114 年（2025）起的 AQI 污染物濃度級距
AIR_SCALES = {
    "aq_aqi":  {"unit": "", "bounds": [0, 51, 101, 151, 201, 301], "colors": AQI_COLORS, "labels": AQI_LABELS},
    "aq_pm25": {"unit": "μg/m³", "bounds": [0, 12.5, 30.5, 50.5, 125.5, 225.5], "colors": AQI_COLORS, "labels": AQI_LABELS},
    "aq_pm10": {"unit": "μg/m³", "bounds": [0, 31, 76, 191, 355, 425], "colors": AQI_COLORS, "labels": AQI_LABELS},
    "aq_o3_8": {"unit": "ppb", "bounds": [0, 55, 71, 86, 106, 201], "colors": AQI_COLORS, "labels": AQI_LABELS},
    # O3 小時值：官方只有 101 ppb 以上的分級；100 ppb 以下沿用 8 小時級距當參考（71–100 用淺橘色表示「接近」）
    "aq_o3_1": {"unit": "ppb", "bounds": [0, 55, 71, 101, 135, 205, 405],
                "colors": ["#00e800", "#ffff00", "#ffc04d", "#ff7e00", "#ff0000", "#8f3f97", "#7e0023"],
                "labels": ["良好\n(參考)", "普通\n(參考)", "偏高\n(參考)", "敏感族群\n不健康", "所有族群\n不健康", "非常不健康", "危害"]},
    "aq_co":   {"unit": "ppm", "bounds": [0, 4.5, 9.5, 12.5, 15.5, 30.5], "colors": AQI_COLORS, "labels": AQI_LABELS},
    "aq_so2":  {"unit": "ppb", "bounds": [0, 9, 66, 161, 305, 605], "colors": AQI_COLORS, "labels": AQI_LABELS},
    "aq_no2":  {"unit": "ppb", "bounds": [0, 22, 101, 361, 650, 1250], "colors": AQI_COLORS, "labels": AQI_LABELS},
    "aq_nox":  {"unit": "ppb", "bounds": [0, 5, 10, 20, 30, 50, 75, 100, 150, 200], "colors": SEQ_COLORS},
}

# 變數：key（網站與圖片檔名用）、來源欄位、名稱、色階、小數位、說明
AIR_VARS = [
    {"key": "aq_aqi",      "src": "aqi",       "name": "空氣品質指標 AQI",       "scale": "aq_aqi",  "dec": 0,
     "note": ["AQI＝各污染物副指標的最大值", "（環境部即時 AQI）"]},
    {"key": "aq_pm25",     "src": "pm2.5",     "name": "PM2.5（小時濃度）",       "scale": "aq_pm25", "dec": 1,
     "note": ["色階＝PM2.5 的 AQI 分級", "官方分級用移動平均（另一張圖）"]},
    {"key": "aq_pm25_avg", "src": "pm2.5_avg", "name": "PM2.5 移動平均",          "scale": "aq_pm25", "dec": 1,
     "note": ["0.5×前 12 小時平均＋0.5×前 4 小時平均", "（即時 AQI 用這個值）"]},
    {"key": "aq_pm10",     "src": "pm10",      "name": "PM10（小時濃度）",        "scale": "aq_pm10", "dec": 0,
     "note": ["色階＝PM10 的 AQI 分級", "官方分級用移動平均（另一張圖）"]},
    {"key": "aq_pm10_avg", "src": "pm10_avg",  "name": "PM10 移動平均",           "scale": "aq_pm10", "dec": 0,
     "note": ["0.5×前 12 小時平均＋0.5×前 4 小時平均", "（即時 AQI 用這個值）"]},
    {"key": "aq_o3",       "src": "o3",        "name": "臭氧 O3（小時濃度）",     "scale": "aq_o3_1", "dec": 0,
     "note": ["101 ppb 以上用 1 小時分級", "100 ppb 以下用 8 小時級距當參考"]},
    {"key": "aq_o3_8hr",   "src": "o3_8hr",    "name": "臭氧 O3 8 小時平均",      "scale": "aq_o3_8", "dec": 0,
     "note": ["最近連續 8 小時移動平均", "色階＝O3 8 小時 AQI 分級"]},
    {"key": "aq_co",       "src": "co",        "name": "一氧化碳 CO（小時濃度）", "scale": "aq_co",   "dec": 2,
     "note": ["色階＝CO 8 小時 AQI 分級（參考）"]},
    {"key": "aq_co_8hr",   "src": "co_8hr",    "name": "一氧化碳 CO 8 小時平均",  "scale": "aq_co",   "dec": 1,
     "note": ["最近連續 8 小時移動平均", "色階＝CO 8 小時 AQI 分級"]},
    {"key": "aq_so2",      "src": "so2",       "name": "二氧化硫 SO2",            "scale": "aq_so2",  "dec": 1,
     "note": ["小時濃度；色階＝SO2 AQI 分級", "（AQI 200 以上官方改用 24 小時值）"]},
    {"key": "aq_no2",      "src": "no2",       "name": "二氧化氮 NO2",            "scale": "aq_no2",  "dec": 0,
     "note": ["小時濃度；色階＝NO2 AQI 分級"]},
    {"key": "aq_nox",      "src": "nox",       "name": "氮氧化物 NOx",            "scale": "aq_nox",  "dec": 1,
     "note": ["沒有 AQI 分級：", "顏色只表示濃度高低"]},
    {"key": "aq_no",       "src": "no",        "name": "一氧化氮 NO",             "scale": "aq_nox",  "dec": 1,
     "note": ["沒有 AQI 分級：", "顏色只表示濃度高低"]},
]
for _v in AIR_VARS:
    _v.update({"group": "空氣品質", "agg": "mean", "rank": "max",
               "fmt": "%.0f" if _v["dec"] == 0 else ("%.1f" if _v["dec"] == 1 else "%.2f")})
AIR_KEYS = [v["key"] for v in AIR_VARS]
AIR_RULE = ("鄉鎮內各空品測站的算術平均（AQI 四捨五入到整數，其他依原始小數位）；"
            "鄉鎮內沒有空品測站＝灰色斜線（不以鄰近測站代替）")
AIR_AGG_TEXT = {"mean": "鄉鎮內各空品測站取平均"}


def aqi_level(v):
    """AQI → (等級名稱, 顏色)"""
    if v is None:
        return ("", None)
    names = ["良好", "普通", "對敏感族群不健康", "對所有族群不健康", "非常不健康", "危害"]
    b = AIR_SCALES["aq_aqi"]["bounds"]
    i = 0
    while i + 1 < len(b) and v >= b[i + 1]:
        i += 1
    return (names[i], AQI_COLORS[i])
