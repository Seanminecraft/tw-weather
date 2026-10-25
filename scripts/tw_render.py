"""tw-weather 繪圖：測站分布圖＋鄉鎮市區面量圖（matplotlib，經緯度等距投影＋緯度校正）"""
import math
import os

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.collections import PathCollection, LineCollection
from matplotlib.colors import ListedColormap, BoundaryNorm, to_rgba
from matplotlib.path import Path
from matplotlib.patches import Patch
import matplotlib.patheffects as pe

from tw_common import ROOT, SCALES, DISCLAIMER, AGG_TEXT, color_for, compass16, beaufort, parse_t

MAIN = (119.28, 122.18, 21.86, 25.68)        # 本島＋澎湖＋綠島蘭嶼＋彭佳嶼
MATSU = (119.86, 120.56, 25.90, 26.43)
KINMEN = (118.18, 118.52, 24.36, 24.54)
ASPECT = 1 / math.cos(math.radians(23.8))
LAND = "#f3f1ea"
SEA = "#e3eef7"
NODATA = "#e5e7eb"
SECTOR_COLORS = ["#1e88e5", "#00acc1", "#43a047", "#c0ca33", "#fb8c00", "#e53935", "#8e24aa", "#3949ab"]
SECTOR_NAMES = ["北", "東北", "東", "東南", "南", "西南", "西", "西北"]


def setup_fonts():
    from matplotlib import font_manager, rcParams
    cands = []
    for d in (os.environ.get("TW_FONT_DIR", ""), os.path.join(ROOT, "fonts"), os.path.expanduser("~/.fonts")):
        if d and os.path.isdir(d):
            cands += [os.path.join(d, f) for f in sorted(os.listdir(d)) if f.lower().endswith((".otf", ".ttf", ".ttc"))]
    fam = None
    for f in cands:
        try:
            font_manager.fontManager.addfont(f)
            nm = font_manager.FontProperties(fname=f).get_name()
            if fam is None or "TC" in nm:
                fam = nm
        except Exception:
            pass
    names = ([fam] if fam else []) + ["Noto Sans CJK TC", "Noto Sans TC", "Noto Sans CJK JP", "DejaVu Sans"]
    rcParams["font.family"] = "sans-serif"
    rcParams["font.sans-serif"] = names
    rcParams["axes.unicode_minus"] = False
    rcParams["hatch.linewidth"] = 0.6
    return names[0]


def _ring_path(coords, ccw=True):
    pts = np.asarray(coords)[:, :2]
    # 外環逆時針、內環順時針 → nonzero 填色時洞會挖空
    area = 0.5 * np.sum(pts[:-1, 0] * pts[1:, 1] - pts[1:, 0] * pts[:-1, 1])
    if (area > 0) != ccw:
        pts = pts[::-1]
    codes = np.full(len(pts), Path.LINETO, dtype=np.uint8)
    codes[0] = Path.MOVETO
    codes[-1] = Path.CLOSEPOLY
    return pts, codes


def geom_path(g):
    verts, codes = [], []
    polys = [g] if g.geom_type == "Polygon" else list(getattr(g, "geoms", []))
    for p in polys:
        if p.geom_type != "Polygon" or p.is_empty:
            continue
        v, c = _ring_path(p.exterior.coords, True)
        verts.append(v); codes.append(c)
        for h in p.interiors:
            v, c = _ring_path(h.coords, False)
            verts.append(v); codes.append(c)
    if not verts:
        return None
    return Path(np.concatenate(verts), np.concatenate(codes))


def load_geo():
    import json
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    from shapely.validation import make_valid
    d = json.load(open(os.path.join(ROOT, "assets", "taiwan", "taiwan_towns.json"), encoding="utf-8"))
    sc = d["scale"]
    arcs = [[(x * sc, y * sc) for x, y in a] for a in d["arcs"]]

    def ring(idx):
        pts = []
        for i in idx:
            a = arcs[i] if i >= 0 else arcs[~i][::-1]
            pts.extend(a if not pts else a[1:])
        return pts
    towns = {}
    for t in d["towns"]:
        polys = []
        for poly in t["p"]:
            rings = [r for r in (ring(x) for x in poly) if len(r) >= 4]
            if rings:
                pg = Polygon(rings[0], rings[1:])
                if not pg.is_valid:
                    pg = make_valid(pg)
                polys.append(pg)
        g = unary_union(polys) if len(polys) > 1 else polys[0]
        if g.geom_type == "GeometryCollection":
            g = unary_union([x for x in g.geoms if x.geom_type in ("Polygon", "MultiPolygon")])
        rp = g.representative_point()
        towns[t["code"]] = {"c": t["c"], "t": t["t"], "path": geom_path(g), "rp": (rp.x, rp.y), "area": g.area}
    lines = {k: [arcs[i] for i, kk in enumerate(d["kind"]) if kk == k and len(arcs[i]) >= 2] for k in (0, 1, 2)}
    return towns, lines


class Renderer:
    W, H, DPI = 10.8, 13.5, 100

    def __init__(self, logo=None):
        self.font = setup_fonts()
        self.towns, self.lines = load_geo()
        self.codes = list(self.towns.keys())
        self.logo = None
        if logo and os.path.exists(logo):
            from PIL import Image
            self.logo = np.asarray(Image.open(logo).convert("RGBA"))

    # -------------------------------------------------------------- 底圖
    def _base(self, ax, ext, town_lw=0.25, land=True):
        ax.set_facecolor(SEA)
        ax.set_xlim(ext[0], ext[1]); ax.set_ylim(ext[2], ext[3])
        ax.set_aspect(ASPECT)
        ax.set_xticks([]); ax.set_yticks([])
        for s in ax.spines.values():
            s.set_edgecolor("#94a3b8"); s.set_linewidth(0.8)
        if land:
            ax.add_collection(PathCollection([t["path"] for t in self.towns.values() if t["path"] is not None],
                                             facecolor=LAND, edgecolor="none", zorder=1))

    def _lines(self, ax, town_lw=0.25):
        ax.add_collection(LineCollection(self.lines[2], colors="#9aa5b1", linewidths=town_lw, zorder=4))
        ax.add_collection(LineCollection(self.lines[1], colors="#475569", linewidths=0.8, zorder=5))
        ax.add_collection(LineCollection(self.lines[0], colors="#334155", linewidths=0.7, zorder=5))

    def _cmap(self, sk):
        sc = SCALES[sk]
        b = sc["bounds"]
        cols = sc["colors"][:len(b)]
        cmap = ListedColormap(cols)
        if sc.get("below"):
            cmap.set_under(sc["below"])
        cmap.set_over(cols[-1])
        norm = BoundaryNorm(b + [b[-1] + (b[-1] - b[-2])], cmap.N)
        return cmap, norm

    # -------------------------------------------------------------- 主程式
    def render(self, v, style, stations, tagg, times, png, source="", hours_today=0):
        fig = plt.figure(figsize=(self.W, self.H), dpi=self.DPI)
        fig.patch.set_facecolor("white")
        ax = fig.add_axes([0.012, 0.135, 0.70, 0.785])
        axm = fig.add_axes([0.725, 0.765, 0.262, 0.140])
        axk = fig.add_axes([0.725, 0.645, 0.262, 0.100])
        for a, ext in ((ax, MAIN), (axm, MATSU), (axk, KINMEN)):
            self._base(a, ext)
        k, sk = v["key"], v["scale"]
        ranks = []
        if style == "town":
            ranks = self._town_layer([ax, axm, axk], v, tagg, stations)
        for a in (ax, axm, axk):
            self._lines(a, 0.35 if style == "town" else 0.22)
        if style == "station":
            ranks = self._station_layer([ax, axm, axk], v, stations)
        axm.set_title("連江縣（馬祖）", fontsize=10, pad=3, loc="left", color="#334155")
        axk.set_title("金門縣", fontsize=10, pad=3, loc="left", color="#334155")

        # ---- 標題
        sty = "測站分布圖" if style == "station" else "鄉鎮市區面量圖"
        fig.text(0.015, 0.965, "全台觀測｜%s" % v["name"], fontsize=25, weight="bold", color="#0f172a", va="center")
        rule = "" if style == "station" else "（%s）" % AGG_TEXT.get(v["agg"], "")
        fig.text(0.015, 0.932, sty + rule, fontsize=13, color="#334155", va="center")
        fig.text(0.985, 0.965, self._time_label(v, times, hours_today), fontsize=12.5, color="#0f172a", ha="right", va="center")
        fig.text(0.985, 0.937, "臺灣時間（UTC+8）・每小時更新", fontsize=10, color="#64748b", ha="right", va="center")

        # ---- 右欄：排行
        self._rank_box(fig, v, style, ranks, stations, tagg)

        # ---- 色階
        self._legend(fig, v, style)

        # ---- 頁尾：來源、免責（左）＋ logo（右下）
        gen = (times.get("generated") or "")[:16].replace("T", " ")
        fig.text(0.015, 0.043, "資料來源：%s｜繪圖 %s" % (source, gen), fontsize=9.5, color="#475569", va="center")
        fig.text(0.015, 0.020, DISCLAIMER + "。", fontsize=11, color="#b91c1c", weight="bold", va="center")
        if self.logo is not None:
            lax = fig.add_axes([0.905, 0.006, 0.082, 0.082 * self.W / self.H * 1.0])
            lax.imshow(self.logo); lax.axis("off")
        os.makedirs(os.path.dirname(png), exist_ok=True)
        fig.savefig(png, dpi=self.DPI, facecolor="white")
        plt.close(fig)
        try:
            from PIL import Image
            im = Image.open(png).convert("RGB")
            im = im.quantize(colors=192, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
            im.save(png, optimize=True)
        except Exception:
            pass

    def _time_label(self, v, times, hours_today):
        def hm(s, full=False):
            t = parse_t(s)
            if not t:
                return "—"
            return t.strftime("%Y-%m-%d %H:%M") if full else t.strftime("%H:%M")
        k = v["key"]
        if k.startswith("r"):
            return "觀測時間 %s（雨量站）" % hm(times.get("rain"), True)
        if k in ("wsx", "gx"):
            return "今日 00:00～%s（逐時 %d 筆）" % (hm(times.get("frame")), hours_today)
        if k in ("tx", "tn"):
            return "今日 00:00 起至 %s" % hm(times.get("cwa") or times.get("frame"), True)
        return "觀測時間 %s（署屬站 %s）" % (hm(times.get("frame"), True), hm(times.get("cwa")))

    def _vals(self, v, stations):
        k = v["key"]
        src_k = "ws" if k == "wd" else k
        out = []
        for r in stations.values():
            x = r.get(src_k)
            if x is None or r.get("la") is None:
                continue
            if k == "wd" and r.get("wd") is None and (x or 0) >= 0.3:
                continue
            out.append((x, r))
        return out

    def _station_layer(self, axes, v, stations):
        k, sk = v["key"], v["scale"]
        vals = self._vals(v, stations)
        if not vals:
            for a in axes[:1]:
                a.text(0.5, 0.5, "目前沒有資料", transform=a.transAxes, ha="center", fontsize=20, color="#64748b")
            return []
        rev = v["rank"] == "min"
        vals.sort(key=lambda x: (-x[0] if rev else x[0]))      # 極端值最後畫（在最上層）
        cmap, norm = self._cmap(sk)
        lon = np.array([r["lo"] for _, r in vals]); lat = np.array([r["la"] for _, r in vals])
        x = np.array([val for val, _ in vals])
        if k == "wd":
            ws = x
            wd = np.array([r.get("wd") if r.get("wd") is not None else 0 for _, r in vals])
            calm = ws < 0.3
            rad = np.radians(wd)
            u, w = -np.sin(rad), -np.cos(rad)
            cols = [color_for("wind", s) or "#cbd5e1" for s in ws]
            for a in axes:
                a.quiver(lon[~calm], lat[~calm], u[~calm], w[~calm], color=[c for c, m in zip(cols, calm) if not m],
                         angles="uv", scale=48, width=0.0032, headwidth=4, headlength=4.5, headaxislength=4,
                         edgecolor="#1f2937", linewidth=0.25, zorder=7)
                a.scatter(lon[calm], lat[calm], s=10, facecolor="white", edgecolor="#64748b", linewidth=0.6, zorder=6)
        else:
            colors = []
            sizes = []
            for val in x:
                c = color_for(sk, val)
                colors.append(c or "#ffffff")
                sizes.append(30 if c else 9)
            ec = ["#1f2937" if c != "#ffffff" else "#94a3b8" for c in colors]
            for a in axes:
                a.scatter(lon, lat, s=sizes, c=colors, edgecolors=ec, linewidths=0.35, zorder=7)
        # 排行（極端前 5 名）
        top = sorted(vals, key=lambda t: (t[0] if rev else -t[0]))[:5]
        if k.startswith("r") and top and top[0][0] <= 0:
            top = []
        for i, (val, r) in enumerate(top):
            for a in axes:
                a.scatter([r["lo"]], [r["la"]], s=150, facecolor="none", edgecolor="#111827", linewidth=1.4, zorder=9)
                a.text(r["lo"] + 0.035, r["la"] + 0.03, str(i + 1), fontsize=11, weight="bold", color="#111827", zorder=10, clip_on=True,
                       path_effects=[pe.withStroke(linewidth=2.5, foreground="white")])
        return [(val, r) for val, r in top]

    def _town_layer(self, axes, v, tagg, stations):
        k, sk = v["key"], v["scale"]
        paths, cols, nod = [], [], []
        vals = []
        for code, t in self.towns.items():
            if t["path"] is None:
                continue
            a = tagg.get(code) or {}
            val = a.get(k)
            if val is None:
                nod.append(t["path"]); continue
            if k == "wd":
                sec = int(((val % 360) + 22.5) // 45) % 8
                c = SECTOR_COLORS[sec] if (a.get("wd_ws") or 0) >= 0.3 else "#f8fafc"
            else:
                c = color_for(sk, val) or "#ffffff"
            paths.append(t["path"]); cols.append(c)
            vals.append((val, a, t))
        for ax in axes:
            ax.add_collection(PathCollection(paths, facecolor=cols, edgecolor="none", zorder=2))
            ax.add_collection(PathCollection(nod, facecolor=NODATA, edgecolor="#a3a3a3", hatch="////", linewidth=0.0, zorder=2))
        if k == "wd":
            pts = [(t["rp"], a) for val, a, t in vals if (a.get("wd_ws") or 0) >= 0.3]
            if pts:
                lon = np.array([p[0][0] for p in pts]); lat = np.array([p[0][1] for p in pts])
                rad = np.radians([p[1]["wd"] for p in pts])
                for ax in axes:
                    ax.quiver(lon, lat, -np.sin(rad), -np.cos(rad), color="#111827", angles="uv", scale=60, width=0.0026,
                              headwidth=4, headlength=4.5, headaxislength=4, zorder=8)
            rank_vals = [(a.get("wd_ws"), a, t) for val, a, t in vals if a.get("wd_ws") is not None]
        else:
            rank_vals = vals
        rev = v["rank"] == "min"
        top = sorted(rank_vals, key=lambda x: (x[0] if rev else -x[0]))[:5]
        if k.startswith("r") and top and top[0][0] <= 0:
            top = []
        for i, (val, a, t) in enumerate(top):
            for ax in axes:
                ax.text(t["rp"][0], t["rp"][1], str(i + 1), fontsize=11, weight="bold", color="#111827", ha="center",
                        va="center", zorder=10, clip_on=True, path_effects=[pe.withStroke(linewidth=2.5, foreground="white")])
        return top

    def _rank_box(self, fig, v, style, ranks, stations, tagg):
        k = v["key"]
        unit = SCALES[v["scale"]]["unit"]
        title = {"max": "最高前 5 名", "min": "最低前 5 名"}[v["rank"]]
        if k == "wd":
            title = "風速最大前 5 名"
        if k.startswith("r") or k in ("ws", "wsx", "g", "gx"):
            title = "最大前 5 名"
        y = 0.605
        fig.text(0.728, y, "🏆 " + title if False else title, fontsize=14, weight="bold", color="#0f172a")
        y -= 0.03
        if not ranks:
            fig.text(0.728, y, "目前無明顯數值" if k.startswith("r") else "無資料", fontsize=12, color="#64748b")
            y -= 0.03
        for i, item in enumerate(ranks):
            if style == "station":
                val, r = item
                name = r.get("n") or r.get("id")
                place = "%s%s" % (r.get("c") or "", r.get("town") or "")
            else:
                val, a, t = item
                name = "%s%s" % (t["c"], t["t"])
                by = stations.get(a.get(("ws" if k == "wd" else k) + "_by")) if k != "wd" else stations.get(a.get("wd_by"))
                place = ("%s站" % by.get("n")) if by else ""
            if k == "wd":
                ws_ = val
                rr = item[1] if style == "station" else item[1]
                d = rr.get("wd")
                vtxt = "%s %.1f m/s" % (compass16(d) + "風" if d is not None else "", ws_)
            elif v["scale"] == "wind":
                vtxt = "%.1f %s（%d 級）" % (val, unit, beaufort(val) or 0)
            else:
                vtxt = (v["fmt"] % val) + " " + unit
            fig.text(0.728, y, "%d. %s" % (i + 1, vtxt), fontsize=12.5, weight="bold", color="#b91c1c" if i == 0 else "#0f172a")
            fig.text(0.748, y - 0.018, "%s %s" % (name, place)[:22], fontsize=10, color="#334155")
            y -= 0.044
        # 統計
        y -= 0.008
        if style == "station":
            n = len(self._vals(v, stations))
            fig.text(0.728, y, "有資料測站：%d 站" % n, fontsize=10.5, color="#475569"); y -= 0.022
        else:
            n = sum(1 for a in tagg.values() if a.get(k) is not None)
            fig.text(0.728, y, "有資料鄉鎮：%d／368" % n, fontsize=10.5, color="#475569"); y -= 0.022
            fig.text(0.728, y, "斜線灰色＝鄉鎮內無測站資料", fontsize=10, color="#475569"); y -= 0.022
        notes = {
            "p": ["由測站氣壓依海拔換算海平面氣壓", "海拔 500 m 以上測站不列入"],
            "wsx": ["今日 00:00 起逐時觀測的最大值", "（署屬站含每小時最大 10 分鐘平均風）"],
            "gx": ["今日 00:00 起逐時陣風的最大值", "（只計發生時間在今天的陣風）"],
            "wd": ["箭頭＝風吹去的方向", "顏色＝風速級數" if style == "station" else "顏色＝風從哪個方位吹來"],
            "t": ["鄉鎮取海拔最低測站（代表平地）"] if style == "town" else [],
            "tx": ["氣象署測站今日最高氣溫"], "tn": ["氣象署測站今日最低氣溫"],
            "r10m": ["過去 10 分鐘雨量"], "r1h": ["過去 1 小時雨量"],
        }.get(k, [])
        for s in notes:
            fig.text(0.728, y, s, fontsize=10, color="#475569"); y -= 0.02

    def _legend(self, fig, v, style):
        k, sk = v["key"], v["scale"]
        if k == "wd" and style == "town":
            hs = [Patch(facecolor=c, edgecolor="#475569", label=n + "風") for c, n in zip(SECTOR_COLORS, SECTOR_NAMES)]
            hs.append(Patch(facecolor="#f8fafc", edgecolor="#475569", label="靜風"))
            hs.append(Patch(facecolor=NODATA, edgecolor="#a3a3a3", hatch="////", label="無資料"))
            fig.legend(handles=hs, loc="lower center", bbox_to_anchor=(0.5, 0.062), ncol=10, fontsize=11, frameon=False,
                       handlelength=1.4, columnspacing=0.9)
            return
        sc = SCALES[sk]
        cax = fig.add_axes([0.05, 0.088, 0.80, 0.020])
        cmap, norm = self._cmap(sk)
        b = sc["bounds"]
        ext = "min" if sc.get("below") else "neither"
        cb = matplotlib.colorbar.ColorbarBase(cax, cmap=cmap, norm=norm, boundaries=([b[0] - 4] if ext == "min" else []) + b + [b[-1] + (b[-1] - b[-2])],
                                              extend="neither", spacing="uniform", orientation="horizontal",
                                              ticks=b)
        if sk == "wind":
            cb.set_ticklabels(["%g" % x for x in b])
            for i, x in enumerate(b):
                pass
            cax.text(1.01, 0.5, "m/s", transform=cax.transAxes, va="center", fontsize=11)
            ax2 = cax.twiny()
            ax2.set_xlim(cax.get_xlim())
            ax2.set_xticks([(i + 0.5) / len(b) * (cax.get_xlim()[1] - cax.get_xlim()[0]) + cax.get_xlim()[0] for i in range(len(b))])
            ax2.set_xticklabels(["%d級" % (i + 1) if i < len(b) - 1 else "13+" for i in range(len(b))], fontsize=9)
            ax2.tick_params(length=0, pad=1)
        else:
            cb.set_ticklabels(["%g" % x for x in b])
            cax.text(1.01, 0.5, sc["unit"], transform=cax.transAxes, va="center", fontsize=11)
        cb.ax.tick_params(labelsize=9.5, length=2)
        cb.outline.set_linewidth(0.5)
        if style == "town":
            fig.patches.append(matplotlib.patches.Rectangle((0.905, 0.088), 0.022, 0.020, transform=fig.transFigure,
                                                             facecolor=NODATA, edgecolor="#a3a3a3", hatch="////", lw=0.5))
            fig.text(0.93, 0.098, "無資料", fontsize=10, va="center")

    @staticmethod
    def to_webp(png, dst, width=720, quality=70):
        from PIL import Image
        im = Image.open(png).convert("RGB")
        h = int(im.height * width / im.width)
        im = im.resize((width, h), Image.LANCZOS)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        im.save(dst, "WEBP", quality=quality, method=6)
