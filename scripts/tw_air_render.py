"""tw-weather 空氣品質繪圖：沿用 tw_render 的底圖、鄉鎮面量、版面（logo、時間、來源、免責），只換標題、排行、色階標籤"""
import os

import numpy as np
import matplotlib
import matplotlib.pyplot as plt
import matplotlib.patheffects as pe

import tw_render
from tw_render import Renderer, MAIN, MATSU, KINMEN, NODATA
from tw_common import SCALES, AGG_TEXT, color_for, parse_t
from tw_air_common import AIR_SCALES, AIR_AGG_TEXT, AIR_DISCLAIMER

SCALES.update(AIR_SCALES)          # 同一個 dict（tw_render 也用它）；只在空品程式的行程裡加，不影響天氣圖
AGG_TEXT.update(AIR_AGG_TEXT)


class AirRenderer(Renderer):
    def render(self, v, style, stations, tagg, times, png, source="", hours_today=0):
        fig = plt.figure(figsize=(self.W, self.H), dpi=self.DPI)
        fig.patch.set_facecolor("white")
        ax = fig.add_axes([0.012, 0.135, 0.70, 0.785])
        axm = fig.add_axes([0.725, 0.765, 0.262, 0.140])
        axk = fig.add_axes([0.725, 0.645, 0.262, 0.100])
        for a, ext in ((ax, MAIN), (axm, MATSU), (axk, KINMEN)):
            self._base(a, ext)
        ranks = []
        if style == "town":
            ranks = self._town_layer([ax, axm, axk], v, tagg, stations)
        for a in (ax, axm, axk):
            self._lines(a, 0.35 if style == "town" else 0.22)
        if style == "station":
            ranks = self._station_layer([ax, axm, axk], v, stations)
        axm.set_title("連江縣（馬祖）", fontsize=10, pad=3, loc="left", color="#334155")
        axk.set_title("金門縣", fontsize=10, pad=3, loc="left", color="#334155")
        sty = "測站分布圖" if style == "station" else "鄉鎮市區面量圖"
        fig.text(0.015, 0.965, "全台空氣品質｜%s" % v["name"], fontsize=24, weight="bold", color="#0f172a", va="center")
        rule = "（環境部空品測站）" if style == "station" else "（%s）" % AGG_TEXT.get(v["agg"], "")
        fig.text(0.015, 0.932, sty + rule, fontsize=13, color="#334155", va="center")
        t = parse_t(times.get("frame"))
        fig.text(0.985, 0.965, "監測時間 %s" % (t.strftime("%Y-%m-%d %H:%M") if t else "—"), fontsize=12.5, color="#0f172a", ha="right", va="center")
        fig.text(0.985, 0.937, "臺灣時間（UTC+8）・環境部每小時發布", fontsize=10, color="#64748b", ha="right", va="center")
        self._rank_box(fig, v, style, ranks, stations, tagg)
        self._legend(fig, v, style)
        gen = (times.get("generated") or "")[:16].replace("T", " ")
        fig.text(0.015, 0.043, "資料來源：%s｜繪圖 %s" % (source, gen), fontsize=9.5, color="#475569", va="center")
        fig.text(0.015, 0.020, AIR_DISCLAIMER + "。", fontsize=11, color="#b91c1c", weight="bold", va="center")
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

    def _station_layer(self, axes, v, stations):
        """空品測站少（約 80 站）：圓點畫大一點，沒資料（儀器維護）畫空心灰圈"""
        k, sk = v["key"], v["scale"]
        vals = [(r.get(k), r) for r in stations.values() if r.get("la") is not None]
        have = [(x, r) for x, r in vals if x is not None]
        miss = [r for x, r in vals if x is None]
        if miss:
            for a in axes:
                a.scatter([r["lo"] for r in miss], [r["la"] for r in miss], s=46, facecolor="white", edgecolor="#9ca3af",
                          linewidths=1.0, zorder=6)
        if not have:
            axes[0].text(0.5, 0.5, "目前沒有資料", transform=axes[0].transAxes, ha="center", fontsize=20, color="#64748b")
            return []
        have.sort(key=lambda x: x[0])
        lon = np.array([r["lo"] for _, r in have]); lat = np.array([r["la"] for _, r in have])
        cols = [color_for(sk, x) or "#ffffff" for x, _ in have]
        for a in axes:
            a.scatter(lon, lat, s=78, c=cols, edgecolors="#1f2937", linewidths=0.6, zorder=7)
        top = sorted(have, key=lambda t: -t[0])[:5]
        for i, (val, r) in enumerate(top):
            for a in axes:
                a.scatter([r["lo"]], [r["la"]], s=230, facecolor="none", edgecolor="#111827", linewidth=1.4, zorder=9)
                a.text(r["lo"] + 0.04, r["la"] + 0.035, str(i + 1), fontsize=11, weight="bold", color="#111827", zorder=10, clip_on=True,
                       path_effects=[pe.withStroke(linewidth=2.5, foreground="white")])
        return top

    def _rank_box(self, fig, v, style, ranks, stations, tagg):
        k = v["key"]
        unit = SCALES[v["scale"]]["unit"]
        y = 0.605
        fig.text(0.728, y, "最高前 5 名", fontsize=14, weight="bold", color="#0f172a")
        y -= 0.03
        if not ranks:
            fig.text(0.728, y, "無資料", fontsize=12, color="#64748b"); y -= 0.03
        for i, item in enumerate(ranks):
            if style == "station":
                val, r = item
                name = r.get("n") or r.get("id")
                place = "%s%s" % (r.get("c") or "", r.get("town") or "")
            else:
                val, a, t = item
                name = "%s%s" % (t["c"], t["t"])
                place = "%d 站平均" % a.get(k + "_n", 0) if a.get(k + "_n", 0) > 1 else ("%s站" % (a.get(k + "_names") or [""])[0])
            vtxt = (v["fmt"] % val) + (" " + unit if unit else "")
            fig.text(0.728, y, "%d. %s" % (i + 1, vtxt), fontsize=12.5, weight="bold", color="#b91c1c" if i == 0 else "#0f172a")
            fig.text(0.748, y - 0.018, ("%s %s" % (name, place))[:22], fontsize=10, color="#334155")
            y -= 0.044
        y -= 0.008
        if style == "station":
            n = sum(1 for r in stations.values() if r.get(k) is not None)
            fig.text(0.728, y, "有資料測站：%d／%d 站" % (n, len(stations)), fontsize=10.5, color="#475569"); y -= 0.022
            fig.text(0.728, y, "空心灰圈＝該測項暫無資料", fontsize=10, color="#475569"); y -= 0.022
        else:
            n = sum(1 for a in tagg.values() if a.get(k) is not None)
            fig.text(0.728, y, "有資料鄉鎮：%d／368" % n, fontsize=10.5, color="#475569"); y -= 0.022
            fig.text(0.728, y, "鄉鎮值＝鄉鎮內空品測站平均", fontsize=10, color="#475569"); y -= 0.022
            fig.text(0.728, y, "斜線灰色＝鄉鎮內沒有空品測站", fontsize=10, color="#475569"); y -= 0.022
        for s in v.get("note", []):
            fig.text(0.728, y, s, fontsize=10, color="#475569"); y -= 0.02

    def _legend(self, fig, v, style):
        sc = SCALES[v["scale"]]
        b = sc["bounds"]
        cax = fig.add_axes([0.05, 0.088, 0.80, 0.020])
        cmap, norm = self._cmap(v["scale"])
        cb = matplotlib.colorbar.ColorbarBase(cax, cmap=cmap, norm=norm, boundaries=b + [b[-1] + (b[-1] - b[-2])],
                                              extend="neither", spacing="uniform", orientation="horizontal", ticks=b)
        cb.set_ticklabels(["%g" % x for x in b])
        cb.ax.tick_params(labelsize=9.5, length=2)
        cb.outline.set_linewidth(0.5)
        if sc.get("unit"):
            cax.text(1.01, 0.5, sc["unit"], transform=cax.transAxes, va="center", fontsize=11)
        labels = sc.get("labels")
        if labels:
            n = len(b)
            for i, lab in enumerate(labels[:n]):
                cax.text((i + 0.5) / n, 1.25, lab, transform=cax.transAxes, ha="center", va="bottom", fontsize=8.5,
                         color="#0f172a", linespacing=0.95)
        if style == "town":
            fig.patches.append(matplotlib.patches.Rectangle((0.905, 0.088), 0.022, 0.020, transform=fig.transFigure,
                                                             facecolor=NODATA, edgecolor="#a3a3a3", hatch="////", lw=0.5))
            fig.text(0.93, 0.098, "無測站", fontsize=10, va="center")
