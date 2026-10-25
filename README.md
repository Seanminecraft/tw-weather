# tw-weather：全台天氣觀測圖（每小時自動更新）

給「動漫同人場次天氣儀表板」的 **全台天氣**、**觀測資料** 分頁用的資料與圖片。
GitHub Actions 每小時從 **中央氣象署開放資料的 S3 公開鏡像** 抓觀測資料（不用金鑰、不吃 API 配額），畫圖後發布到 GitHub Pages。

> 氣象資料僅供參考，預報請依氣象署發布為準，並勿作為重大決策使用。

## 固定網址（每小時覆蓋更新）

網站：`https://seanminecraft.github.io/tw-weather/`

| 內容 | 網址 |
|---|---|
| 最新圖片 | `img/latest/<變數>_town.png`（鄉鎮面量圖）、`img/latest/<變數>_station.png`（測站分布圖） |
| 圖片清單 | `manifest.json` |
| 測站資料 | `data/latest.json` |
| 鄉鎮彙整 | `data/towns.json` |
| 縣市摘要（含警特報、36 小時預報） | `data/counties.json` |
| 警特報（含 CAP 鄉鎮級範圍，v1.1） | `data/warnings.json`：`counties`／`texts`（W-C0033-001 縣市級）＋`caps`（每則 CAP 的類別、燈號、起訖時間、影響鄉鎮代碼）＋`towns`（鄉鎮代碼 → 生效中警特報） |
| 各縣市 48 小時序列 | `data/history/c/<縣市代碼>.json` |
| 歷史影格（WebP） | `img/history/<YYYYMMDDHH>/<變數>_<town|station>.webp`、清單 `img/history/index.json` |

變數：`t` 目前氣溫、`tx` 今日最高、`tn` 今日最低、`r10m` 10 分鐘雨量、`r1h` 1 小時、`r6h` 6 小時、`r24h` 1 日、`r2d` 2 日、`r3d` 3 日、
`p` 海平面氣壓、`ws` 平均風、`wsx` 今日最大平均風、`wd` 風向、`g` 最大陣風（逐時）、`gx` 今日最大陣風。

規則：
- 警特報：每小時從 S3 鏡像讀 `W-C0033-001`（縣市級）與 CAP 檔（`Warning/W-C0033-003～006、-010、W-C0034-001.cap`），解析 CAP 的 `geocode`（鄉鎮代碼），所以陸上強風、大雨等只會畫在真正受影響的鄉鎮；CAP 讀不到時退回縣市級。尚未開始的（`onset` 在未來）標記為 `upcoming`。
- 「今日」從臺灣時間 00:00 起算。
- 鄉鎮面量圖：雨量、平均風、陣風、最高溫取鄉鎮內各站 **最大值**；最低溫取 **最小值**；目前氣溫與氣壓取 **海拔最低的測站**；風向取平均風最強測站的風向。鄉鎮內沒有測站資料就畫灰色斜線（不拿鄰近鄉鎮代替）。
- 海平面氣壓由測站氣壓換算，海拔 500 m 以上測站不列入。
- 48 小時歷史存在 Pages 網站本身（每次部署先下載上一版再加新的一小時），**不會 commit，repo 不會越來越大**。

## 檔案

```
.github/workflows/tw-weather.yml   每小時 :20（主要）與 :50（備援）；已是最新就直接結束
.github/workflows/keepalive.yml    每週重新啟用排程（避免 60 天沒 commit 被 GitHub 停用）
scripts/tw_weather.py              抓資料、彙整、輸出 JSON／圖片
scripts/tw_render.py               畫圖（matplotlib）
scripts/tw_common.py               色階、變數定義
assets/taiwan/                     縣市／鄉鎮邊界
assets/logo.png                    圖片右下角 logo
```

## 金鑰（選用）

Settings → Secrets and variables → Actions → `CWA_API_KEY`。
只有 S3 鏡像連續失敗時才會用它改打氣象署檔案下載 API；不設也能正常運作。程式不會把金鑰印到紀錄裡。

## 本機測試

```bash
pip install -r requirements.txt
TW_FONT_DIR=/path/to/NotoSansTC python scripts/tw_weather.py --out site --pages-base none --force
python -m http.server -d site 8000
```
