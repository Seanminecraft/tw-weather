"""tw-weather v22.3：10 分鐘雨量（r10m）每 10 分鐘重畫

只抓 S3 的 O-A0002-001（雨量，約 1.7 MB），不呼叫氣象署 API、不用金鑰。
  --check  （只用內建模組）S3 的 10 分鐘觀測時間 ≠ 線上 r10.json → 需要更新
  （預設）  畫 r10m 測站圖／鄉鎮圖（img/latest/r10m_*.png，覆蓋每小時版）＋寫：
           r10.json、data/r10/latest.json、data/r10/store.json.gz（48 小時 × 每 10 分鐘）、
           img/history_r10/<YYYYMMDDHHMM>/r10m_{station,town}.webp、img/history_r10/index.json
其他圖、每小時歷史、空品完全不動。任何失敗 → 沿用上一版 r10 檔案（標記 stale），不影響其他資料。
"""
import argparse
import collections
import concurrent.futures as cf
import datetime as dt
import gzip
import json
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tw_common import ROOT, now_tpe, parse_t, iso  # noqa: E402
import tw_weather as W  # noqa: E402

DID = "O-A0002-001"
R10_HOURS = 48                  # 地圖資料（store）保留幾小時
R10_IMG_HOURS = 48              # 歷史圖保留幾小時（每格 2 張 webp，約 70 KB → 48 小時約 20 MB）
STEP = 10                       # 分鐘
NAMES = ("r10m_station.webp", "r10m_town.webp")
DEFAULT_PAGES = getattr(W, "DEFAULT_PAGES", "")
log = W.log


def k10(t):
    return t.strftime("%Y%m%d%H%M")


def obs10(doc):
    """最多測站的 ObsTime（10 分鐘整）；沒有就 None"""
    c = collections.Counter()
    for s in W.stations_of(doc):
        t = parse_t((s.get("ObsTime") or {}).get("DateTime"))
        if t:
            c[t.replace(minute=t.minute - t.minute % STEP, second=0, microsecond=0)] += 1
    return c.most_common(1)[0][0] if c else None


def fetch_rain(raw_dir=None, max_age_s=600):
    """O-A0002：raw_dir 有 10 分鐘內的快取（--check 剛抓的）就直接用"""
    p = os.path.join(raw_dir, DID + ".json") if raw_dir else None
    if p and os.path.exists(p) and time.time() - os.path.getmtime(p) < max_age_s:
        return json.load(open(p, encoding="utf-8")), "cache"
    doc, src = W.fetch_one(DID)
    if p:
        os.makedirs(raw_dir, exist_ok=True)
        with open(p, "w", encoding="utf-8") as fp:
            json.dump(doc, fp, ensure_ascii=False)
    return doc, src


def online_r10(pages_base):
    if not pages_base:
        return None
    try:
        return json.loads(W.http_get(pages_base + "r10.json?ts=%d" % time.time(), timeout=20).decode("utf-8"))
    except Exception:
        return None


def needs_update(pages_base, raw_dir=None):
    """（tw_weather --check 會呼叫）回傳 (要不要更新, S3 時間, 線上時間)"""
    doc, _ = fetch_rain(raw_dir, max_age_s=0)
    T = obs10(doc)
    man = online_r10(pages_base)
    on = parse_t(man.get("obsTime")) if man else None
    need = T is not None and (on is None or T > on)
    return need, (iso(T) if T else None), (iso(on) if on else None)


def read_prev(rel, sources, binary=False):
    """sources：[('dir', 路徑) | ('url', base)]；依序找，拿不到回 None"""
    raw = None
    for kind, base in sources:
        try:
            if kind == "dir":
                p = os.path.join(base, rel)
                if os.path.exists(p):
                    raw = open(p, "rb").read()
            elif base:
                raw = W.http_get(base + rel + "?ts=%d" % time.time(), timeout=30)
        except Exception:
            raw = None
        if raw is not None:
            break
    if raw is None or binary:
        return raw
    try:
        if rel.endswith(".gz"):
            try:
                raw = gzip.decompress(raw)
            except OSError:
                pass
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return None


def update_store(store, stations, T):
    """store = {v, step, ids:[…], meta:{id:[n,c,town,tc,la,lo]}, frames:[{t,k,v:[…對齊 ids…]}]}"""
    old = store if (store and store.get("v") == 1) else {"ids": [], "meta": {}, "frames": []}
    cut = T - dt.timedelta(hours=R10_HOURS) + dt.timedelta(minutes=1)
    frames = []
    for f in old.get("frames", []):
        t = parse_t(f.get("t"))
        if t and cut <= t and k10(t) != k10(T):
            frames.append({"t": f["t"], "k": f["k"], "d": dict(zip(old["ids"], f["v"]))})
    frames.append({"t": iso(T), "k": k10(T), "d": {sid: r.get("r10m") for sid, r in stations.items()}})
    frames.sort(key=lambda f: f["k"])
    meta = dict(old.get("meta") or {})
    for sid, r in stations.items():
        meta[sid] = [r.get("n"), r.get("c"), r.get("town"), r.get("tc"), r.get("la"), r.get("lo")]
    seen = set()
    for f in frames:
        seen.update(sid for sid, v in f["d"].items() if v is not None)
    seen.update(stations.keys())
    ids = sorted(i for i in seen if i in meta)
    return {"v": 1, "step": STEP, "key": "r10m", "ids": ids, "meta": {i: meta[i] for i in ids},
            "frames": [{"t": f["t"], "k": f["k"], "v": [f["d"].get(i) for i in ids]} for f in frames]}


class _Lazy:
    rd = None


def renderer(logo):
    if _Lazy.rd is None:
        import tw_render
        import matplotlib.pyplot as plt

        class R10Renderer(tw_render.Renderer):
            """只改兩行字：觀測時間（10 分鐘）＋「每 10 分鐘更新」；其他版面與每小時版完全相同"""
            def _time_label(self, v, times, hours_today):
                t = parse_t(times.get("rain"))
                return "觀測時間 %s（雨量站・10 分鐘）" % (t.strftime("%Y-%m-%d %H:%M") if t else "—")

            def render(self, *a, **k):
                orig = plt.figure

                def fig(*fa, **fk):
                    f = orig(*fa, **fk)
                    t0 = f.text

                    def text(x, y, s, *ta, **tk):
                        if s == "臺灣時間（UTC+8）・每小時更新":
                            s = "臺灣時間（UTC+8）・每 10 分鐘更新"
                        return t0(x, y, s, *ta, **tk)
                    f.text = text
                    return f
                plt.figure = fig
                try:
                    return super().render(*a, **k)
                finally:
                    plt.figure = orig
        _Lazy.rd = R10Renderer(logo=logo)
    return _Lazy.rd


def build(a):
    t0 = time.time()
    out = a.out
    raw_dir = os.path.join(out, "_raw")
    sources = [("dir", out)] + ([("dir", a.prev_dir)] if a.prev_dir else []) + [("url", a.pages_base)]
    doc, src = fetch_rain(raw_dir)
    T = obs10(doc)
    if T is None:
        raise RuntimeError("O-A0002 沒有觀測時間")
    stations = W.merge_stations({DID: doc})
    n_val = sum(1 for r in stations.values() if r.get("r10m") is not None)
    log("🌧️ 10 分鐘雨量 %s（來源 %s），雨量站 %d（有值 %d）" % (iso(T), src, len(stations), n_val))

    store = update_store(read_prev("data/r10/store.json.gz", sources), stations, T)
    towns = W.town_table()
    tagg = W.aggregate_towns(stations, towns)
    times = {"frame": iso(T), "rain": iso(T), "generated": iso(now_tpe())}
    srcs_label = "中央氣象署"   # v22.4：圖上只寫資料來源（實際抓取方式見 log）
    v = next(x for x in W.VARS if x["key"] == "r10m")
    rd = renderer(a.logo)
    fk = k10(T)
    hist_root = os.path.join(out, "img", "history_r10")
    os.makedirs(os.path.join(out, "img", "latest"), exist_ok=True)
    for style in ("station", "town"):
        png = os.path.join(out, "img", "latest", "r10m_%s.png" % style)
        rd.render(v, style, stations, tagg, times, png, source=srcs_label, hours_today=0)
        rd.to_webp(png, os.path.join(hist_root, fk, "r10m_%s.webp" % style))
    log("🖼️ 已畫 r10m 兩張（%.1f 秒）" % (time.time() - t0))

    # 歷史圖：保留 R10_IMG_HOURS，缺的從上一版（本機 → artifact → Pages）補
    cut = T - dt.timedelta(hours=R10_IMG_HOURS) + dt.timedelta(minutes=1)
    keep = [f["k"] for f in store["frames"] if parse_t(f["t"]) >= cut]
    prev_idx = read_prev("img/history_r10/index.json", sources[1:]) or {"frames": []}
    have_prev = {f["k"] for f in prev_idx.get("frames", [])}

    def get(job):
        k, nm = job
        dst = os.path.join(hist_root, k, nm)
        if os.path.exists(dst):
            return 0
        raw = read_prev("img/history_r10/%s/%s" % (k, nm), sources[1:], binary=True)
        if raw is None:
            return 0
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        open(dst, "wb").write(raw)
        return 1
    jobs = [(k, nm) for k in keep if k != fk and k in have_prev for nm in NAMES]
    with cf.ThreadPoolExecutor(16) as ex:
        copied = sum(ex.map(get, jobs))
    if os.path.isdir(hist_root):
        for d in os.listdir(hist_root):
            p = os.path.join(hist_root, d)
            if os.path.isdir(p) and d not in keep:
                shutil.rmtree(p, ignore_errors=True)
    frames = []
    for f in store["frames"]:
        if f["k"] in keep and all(os.path.exists(os.path.join(hist_root, f["k"], nm)) for nm in NAMES):
            frames.append({"k": f["k"], "t": f["t"]})
    W.dump(os.path.join(hist_root, "index.json"), {"step": STEP, "hours": R10_IMG_HOURS, "frames": frames,
                                                   "pattern": "img/history_r10/{k}/r10m_{style}.webp"})

    # 資料
    rows = [[sid, r.get("r10m"), r.get("la"), r.get("lo"), r.get("n"), r.get("c"), r.get("town"), r.get("tc")]
            for sid, r in sorted(stations.items())]
    tw = {code: [a2.get("r10m"), a2.get("r10m_by")] for code, a2 in tagg.items() if a2.get("r10m") is not None}
    W.dump(os.path.join(out, "data", "r10", "latest.json"),
           {"obsTime": iso(T), "cols": ["id", "v", "la", "lo", "n", "c", "town", "tc"], "rows": rows,
            "towns": tw, "townCols": ["v", "by"]})
    sz = W.dump(os.path.join(out, "data", "r10", "store.json.gz"), store, gz=True)
    gz = os.path.getsize(os.path.join(out, "data", "r10", "store.json.gz"))
    man = {"version": "v22.3", "key": "r10m", "obsTime": iso(T), "generated": iso(now_tpe()), "step": STEP,
           "stale": False, "source": srcs_label, "stations": len(stations), "withValue": n_val,
           "frames": len(frames), "storeFrames": len(store["frames"]), "historyHours": R10_IMG_HOURS,
           "img": {"station": "img/latest/r10m_station.png", "town": "img/latest/r10m_town.png"},
           "rule": "鄉鎮取最大值（只用本鄉鎮的雨量站）"}
    W.dump(os.path.join(out, "r10.json"), man)
    hist_bytes = sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fs in os.walk(hist_root) for f in fs)
    log("💾 r10 歷史：圖 %d 格（補 %d 張，共 %.1f MB）、資料 %d 格（store %.0f KB → gzip %.0f KB）｜%.1f 秒" % (
        len(frames), copied, hist_bytes / 1e6, len(store["frames"]), sz / 1e3, gz / 1e3, time.time() - t0))
    return man


def carry_prev(a, reason):
    """失敗：上一版 r10 檔案沿用（r10-only 模式本來就已經在 out 裡），manifest 標 stale"""
    sources = ([("dir", a.prev_dir)] if a.prev_dir else []) + [("url", a.pages_base)]
    man = read_prev("r10.json", [("dir", a.out)]) or read_prev("r10.json", sources)
    if not man:
        log("ℹ️ 沒有上一版 r10，網站維持每小時的 r10m 圖")
        return
    rels = ["data/r10/latest.json", "data/r10/store.json.gz", "img/history_r10/index.json",
            "img/latest/r10m_station.png", "img/latest/r10m_town.png"]
    idx = read_prev("img/history_r10/index.json", [("dir", a.out)] + sources) or {"frames": []}
    rels += ["img/history_r10/%s/%s" % (f["k"], nm) for f in idx.get("frames", []) for nm in NAMES]

    def get(rel):
        dst = os.path.join(a.out, rel)
        if os.path.exists(dst) and not rel.startswith("img/latest/"):
            return
        raw = read_prev(rel, sources, binary=True)
        if raw is not None:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            open(dst, "wb").write(raw)
    with cf.ThreadPoolExecutor(16) as ex:
        list(ex.map(get, rels))
    man["stale"] = True
    man["staleReason"] = str(reason)[:200]
    W.dump(os.path.join(a.out, "r10.json"), man)
    log("⚠️ r10 沿用上一版（%s）" % reason)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="site")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--pages-base", default=os.environ.get("PAGES_BASE", DEFAULT_PAGES))
    ap.add_argument("--prev-dir", default=None, help="上一版網站（Actions artifact 解開的資料夾）；沒有就讀線上 Pages")
    ap.add_argument("--logo", default=os.path.join(ROOT, "assets", "logo.png"))
    ap.add_argument("--keep-raw", action="store_true")
    a = ap.parse_args()
    if a.pages_base and not a.pages_base.endswith("/"):
        a.pages_base += "/"
    if a.pages_base in ("none", "none/"):
        a.pages_base = ""
    if a.prev_dir and not os.path.isdir(a.prev_dir):
        a.prev_dir = None
    if a.check:
        need, s3t, on = needs_update(a.pages_base, os.path.join(a.out, "_raw"))
        log("🔎 S3 10 分鐘雨量 %s｜線上 %s → %s" % (s3t, on, "需要更新" if need else "略過"))
        W.gh_output("r10", "true" if need else "false")
        return
    try:
        build(a)
    except Exception as e:
        log("❌ r10 更新失敗：%s" % W.mask(e))
        try:
            carry_prev(a, W.mask(e))
        except Exception as e2:
            log("⚠️ r10 沿用上一版也失敗：%s" % W.mask(e2))
    finally:
        if not a.keep_raw:
            shutil.rmtree(os.path.join(a.out, "_raw"), ignore_errors=True)


if __name__ == "__main__":
    main()
