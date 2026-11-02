# 氣候指數：ENSO／PDO／MJO／IOD 現況與預報＋各場次歷史統計＋示意圖
# 資料：NOAA CPC（ONI、ENSO 討論、MJO 簡報）、NOAA NCEI（PDO）、澳洲氣象局（MJO RMM）、IRI（ENSO／IOD 機率）、
#       NOAA ERSST v5 與 NCEP/NCAR 再分析（NOAA PSL）；中央氣象署測站觀測。不需要金鑰。
import os, re, io, json, html, argparse, datetime as dt, urllib.request, ssl
TZ = dt.timezone(dt.timedelta(hours=8))
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/120 Safari/537.36"}
URL = dict(oni="https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt",
           pdo="https://www.ncei.noaa.gov/pub/data/cmb/ersst/v5/index/ersst.v5.pdo.dat",
           rmm="https://www.bom.gov.au/clim_data/IDCKGEM000/rmm.74toRealtime.txt",
           cpc="https://www.cpc.ncep.noaa.gov/products/analysis_monitoring/enso_advisory/ensodisc.shtml",
           iri="https://iri.columbia.edu/our-expertise/climate/forecasts/enso/current/",
           mjo="https://www.cpc.ncep.noaa.gov/products/precip/CWlink/MJO/mjoupdate.pdf",
           ersst="https://psl.noaa.gov/thredds/dodsC/Datasets/noaa.ersst.v5/sst.mnmean.nc")
SN = ['DJF', 'JFM', 'FMA', 'MAM', 'AMJ', 'MJJ', 'JJA', 'JAS', 'ASO', 'SON', 'OND', 'NDJ']
STN = {"北部": "466920", "中部": "467490", "南部": "467441"}
MON = {m: i + 1 for i, m in enumerate(['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'])}

def get(u, t=90):
    ctx = ssl.create_default_context(); ctx.verify_flags &= ~getattr(ssl, "VERIFY_X509_STRICT", 0)
    return urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=t, context=ctx).read()
def text(b):
    s = re.sub(r'(?s)<(script|style)[^>]*>.*?</\1>', ' ', b.decode('utf-8', 'ignore'))
    return re.sub(r'\s+', ' ', html.unescape(re.sub('<[^>]+>', ' ', s)))

def p_oni(b):
    o = {}
    for l in b.decode().split('\n')[1:]:
        p = l.split()
        if len(p) == 4: o[(int(p[1]), SN.index(p[0]) + 1)] = float(p[3])   # 中心月
    return o
def p_pdo(b):
    o = {}
    for l in b.decode().split('\n'):
        p = l.split()
        if p and p[0].isdigit() and len(p) == 13:
            for m, v in enumerate(p[1:], 1):
                if float(v) < 99: o[(int(p[0]), m)] = float(v)
    return o
def p_rmm(b):
    o = {}
    for l in b.decode().split('\n')[2:]:
        p = l.split()
        try:
            a = float(p[6])
            if a < 900: o[dt.date(int(p[0]), int(p[1]), int(p[2]))] = (float(p[3]), float(p[4]), int(p[5]), a)
        except Exception: pass
    return o
def p_cpc(t):
    r = {}
    m = re.search(r'ISSUED BY CLIMATE PREDICTION CENTER/NCEP/NWS\s+(\d+ \w+ \d{4})', t, re.I) or re.search(r'NCEP/NWS (\d+ \w+ \d{4})', t)
    if m: d = m.group(1).split(); r['issued'] = dt.date(int(d[2]), MON[d[1]], int(d[0])).isoformat()
    m = re.search(r'ENSO Alert System Status:\s*([A-Za-z\s\-ñÑ]+?)\s+Synopsis', t)
    if m: r['status'] = m.group(1).strip()
    m = re.search(r'Synopsis:\s*(.+?\.)\s', t)
    if m: r['synopsis'] = m.group(1)
    m = re.search(r'next ENSO Diagnostics Discussion is scheduled for (\d+ \w+ \d{4})', t)
    if m: d = m.group(1).split(); r['next'] = dt.date(int(d[2]), MON[d[1]], int(d[0])).isoformat()
    return r
def p_iri(t):
    r = {}
    m = re.search(r'Quick Look Published: (\w+) (\d+), (\d{4})', t)
    if m: r['issued'] = dt.date(int(m.group(3)), MON[m.group(1)], int(m.group(2))).isoformat()
    s = re.search(r'El Ni[ñn]o probabilities[^.]*\.[^.]*\.', t)
    pr = []
    if s:
        x = s.group(0)
        m = re.search(r'remain at (\d+)% from ([A-Z]{3}) (\d{4}) through ([A-Z]{3}) (\d{4})', x)
        if m:
            a, b = SN.index(m.group(2)), SN.index(m.group(4)); y = int(m.group(3)); i = a
            while True:
                pr.append([SN[i], int(m.group(1))])
                if SN[i] == m.group(4): break
                i = (i + 1) % 12
        for v, sn in re.findall(r'(\d+)% in ([A-Z]{3})', x): pr.append([sn, int(v)])
        m = re.search(r'By ([A-Z]{3}) \d{4}, ENSO-neutral becomes the most likely state at (\d+)%, while the El Ni[ñn]o probability drops to (\d+)%', x)
        if m: pr.append([m.group(1), int(m.group(3))]); r['neutral'] = [m.group(1), int(m.group(2))]
    if pr: r['elnino'] = pr
    iod = []
    m = re.search(r'In (\w+) \d{4}, positive IOD conditions become strongly dominant, with probabilities approaching (\d+)%', t)
    if m: iod.append([MON.get(m.group(1), 0), int(m.group(2)), 'pos'])
    m = re.search(r'persist through (\w+) and (\w+), with probabilities of (\d+)% and (\d+)%', t)
    if m: iod += [[MON.get(m.group(1), 0), int(m.group(3)), 'pos'], [MON.get(m.group(2), 0), int(m.group(4)), 'pos']]
    if iod: r['iod'] = iod
    m = re.search(r'latest weekly Ni[ñn]o 3\.4 index, centered on (\w+) (\d+), (\d{4}), climbed to ([+\-]?\d+\.\d)', t)
    if m: r['weekly'] = [dt.date(int(m.group(3)), MON[m.group(1)], int(m.group(2))).isoformat(), float(m.group(4))]
    return r
def p_mjo(b):
    import subprocess, tempfile
    f = tempfile.NamedTemporaryFile(suffix='.pdf', delete=False); f.write(b); f.close()
    try: t = subprocess.run(['pdftotext', '-layout', f.name, '-'], capture_output=True, text=True).stdout
    except Exception: t = ''
    m = re.search(r'Overview\s+(\d+ \w+ \d{4})', t); r = {}
    if m: d = m.group(1).split(); r['issued'] = dt.date(int(d[2]), MON[d[1]], int(d[0])).isoformat()
    else:
        m = re.search(rb'/CreationDate\s*\(D:(\d{4})(\d\d)(\d\d)', b)
        if m: r['issued'] = '%s-%s-%s' % tuple(x.decode() for x in m.groups())
    return r

def enso_cat(v):
    if v is None: return None
    a = abs(v); s = 'EN' if v >= 0.5 else 'LN' if v <= -0.5 else 'NU'
    return [s, 0 if s == 'NU' else (3 if a >= 1.5 else 2 if a >= 1 else 1)]

def venue(C, sid, sd, ed, oni, rmm, cat, mjo_now):
    st = C['st'].get(sid)
    if not st: return None
    t0 = dt.date.fromisoformat(st['start']); doy = sd.timetuple().tm_yday
    th = 1.0 if cat and cat[1] >= 2 else 0.5
    def sel(o):
        if not cat or cat[0] == 'NU': return o is not None and abs(o) < 0.5
        return o is not None and (o >= th if cat[0] == 'EN' else o <= -th)
    g = {'all': [], 'enso': [], 'mjo': []}; yrs = set()
    for i, v in enumerate(st['v']):
        d = t0 + dt.timedelta(i)
        if v is None or v[2] is None or v[3] is None: continue
        dd = abs(d.timetuple().tm_yday - doy); dd = min(dd, 365 - dd)
        if dd > 15: continue
        g['all'].append(v)
        if sel(oni.get((d.year, d.month))): g['enso'].append(v); yrs.add(d.year)
        if mjo_now and d in rmm and rmm[d][3] >= 1 and rmm[d][2] == mjo_now: g['mjo'].append(v)
    def s(a):
        if len(a) < 10: return None
        return dict(wet=round(100 * sum(x[3] >= 1 for x in a) / len(a)), t=round(sum(x[2] for x in a) / len(a), 1), n=len(a))
    return dict(all=s(g['all']), enso=s(g['enso']), enso_years=len(yrs), mjo=s(g['mjo']))

def schematic(src, out_png, sst_now, months):
    import numpy as np, matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
    from matplotlib.patches import Ellipse, FancyArrowPatch
    from matplotlib import font_manager as fm
    fp = os.path.join(src, 'label-font.otf'); fm.fontManager.addfont(fp)
    plt.rcParams['font.family'] = [fm.FontProperties(fname=fp).get_name()]; plt.rcParams['axes.unicode_minus'] = False
    C = json.load(open(os.path.join(src, 'composite.json'))); coast = json.load(open(os.path.join(src, 'coast_wp.json')))
    A = lambda x: np.where(np.array(x) == -999, np.nan, np.array(x, float))
    sl, so, al, ao = map(np.array, (C['sst_lat'], C['sst_lon'], C['atm_lat'], C['atm_lon']))
    fig, axs = plt.subplots(3, 1, figsize=(9, 17), dpi=80)
    def base(a):
        for c in coast: c = np.array(c); a.plot(c[:, 0], c[:, 1], 'k', lw=.6)
        a.set_xlim(100, 180); a.set_ylim(0, 40); a.set_facecolor('#f8fafc')
        a.set_xticks(range(100, 181, 10), [f'{x}°E' for x in range(100, 181, 10)], fontsize=7); a.set_yticks(range(0, 41, 10), [f'{y}°N' for y in range(0, 41, 10)], fontsize=7)
        a.plot(121, 23.7, '*', ms=16, color='#facc15', mec='k', zorder=9)
        for x, y in ((121.52, 25.04), (120.68, 24.15), (120.28, 22.62)): a.plot(x, y, 'o', ms=3, color='k', zorder=10)
    def ext(f, la, lo, box, fn=np.nanargmax):
        LO, LA = np.meshgrid(lo, la); m = (LO >= box[0]) & (LO <= box[1]) & (LA >= box[2]) & (LA <= box[3]); z = np.where(m, f, np.nan); i = fn(z)
        return LO.flat[i], LA.flat[i], z.flat[i]
    def ell(a, x, y, w, h, col, t, dy=0):
        a.add_patch(Ellipse((x, y), w, h, fc=col, alpha=.28, ec=col, lw=2, zorder=5))
        a.text(x, y + dy, t, ha='center', va='center', fontsize=9, zorder=11, bbox=dict(fc='white', ec=col, alpha=.85, boxstyle='round,pad=.3'))
    lv = np.arange(-1.6, 1.61, .2)
    for a, nm in zip(axs[:2], ('SON', 'DJF')):
        d = C[nm]; base(a); s, p, u, v = A(d['sst']), A(d['slp']), A(d['u850']), A(d['v850'])
        cf = a.contourf(so, sl, s, levels=lv, cmap='RdBu_r', extend='both', alpha=.55, zorder=1)
        cs = a.contour(ao, al, p, levels=[-2, -1.5, -1, -.5, .5, 1, 1.5, 2], colors='#7c3aed', linewidths=.8, zorder=2); a.clabel(cs, fontsize=6, fmt='%.1f')
        a.quiver(ao[::2], al[::2], u[::2, ::2], v[::2, ::2], scale=25, width=.003, color='#334155', zorder=3)
        cl = lambda k: A(d[k + '_cl'])
        vws = np.hypot(cl('u200') + A(d['u200']) - cl('u850') - u, cl('v200') + A(d['v200']) - cl('v850') - v) - np.hypot(cl('u200') - cl('u850'), cl('v200') - cl('v850'))
        hx, hy, hv = ext(p, al, ao, (110, 175, 5, 35)); cx, cy, cv = ext(s, sl, so, (115, 165, 0, 30), np.nanargmin); wx, wy, wv = ext(s, sl, so, (150, 180, -2, 10)); vx, vy, vv = ext(vws, al, ao, (120, 175, 5, 25))
        ell(a, hx, hy, 22, 11, '#a855f7', f'反氣旋／副高偏強 +{hv:.1f} hPa\nanticyclonic anomaly')
        ell(a, cx, cy, 14, 9, '#3b82f6', f'海溫偏冷 {cv:.1f}°C\ncold SST')
        ell(a, wx, max(wy, 4), 16, 7, '#ef4444', f'赤道海溫偏暖 +{wv:.1f}°C\nwarm SST (El Niño)')
        if vv > 1.5: a.text(vx, vy, f'垂直風切增強 +{vv:.0f} m/s\nstronger VWS', fontsize=8, ha='center', color='#c2410c', zorder=11, bbox=dict(fc='white', ec='#c2410c', alpha=.8))
        if nm == 'DJF':
            a.add_patch(FancyArrowPatch((hx - 8, hy + 3), (119.5, 22.8), connectionstyle='arc3,rad=-.3', arrowstyle='-|>', mutation_scale=22, lw=2.5, color='#16a34a', zorder=8))
            a.text(108, 30, '異常西南風\n東北季風減弱、水氣↑\nSW anomaly', fontsize=8.5, color='#166534', ha='center', bbox=dict(fc='white', ec='#16a34a', alpha=.85), zorder=11)
        y = d['yrs']; a.set_title(f"{nm}  {'9–11 月 Sep–Nov' if nm == 'SON' else '12–2 月 Dec–Feb'}\n聖嬰合成 El Niño composite n={len(y)}（{y[0]}–{y[-1]}），相對 1991–2020", fontsize=11, loc='left')
        a.text(178, 38, nm, ha='right', va='top', fontsize=13, weight='bold', bbox=dict(fc='#fde047', ec='k'), zorder=12)
    a = axs[2]; base(a); now = sst_now
    a.contourf(so, sl, now, levels=lv, cmap='RdBu_r', extend='both', alpha=.55)
    cx, cy, cv = ext(now, sl, so, (115, 165, 0, 30), np.nanargmin); wx, wy, wv = ext(now, sl, so, (150, 180, -2, 10)); kx, ky, kv = ext(now, sl, so, (115, 170, 15, 40))
    ell(a, wx, max(wy, 4), 16, 7, '#ef4444', f'赤道海溫偏暖 +{wv:.1f}°C\nwarm SST')
    ell(a, cx, cy, 14, 8, '#3b82f6', f'海溫偏冷 {cv:.1f}°C\ncold SST')
    if kv >= 1: a.text(kx, min(ky, 36), f'海溫偏暖 +{kv:.1f}°C\nwarm SST', ha='center', fontsize=9, bbox=dict(fc='white', ec='#ef4444', alpha=.85), zorder=11)
    a.set_title(f'{months[0]}～{months[-1]} 海溫距平 SST anomaly（ERSST v5）', fontsize=11, loc='left')
    a.text(178, 38, months[-1][:4], ha='right', va='top', fontsize=13, weight='bold', bbox=dict(fc='#fde047', ec='k'))
    cb = fig.colorbar(cf, ax=axs, orientation='horizontal', fraction=.015, pad=.03)
    cb.set_label('海溫距平 SST anomaly (°C)；紫線 SLP anomaly (hPa)；箭頭 850 hPa wind anomaly')
    fig.text(.5, .005, 'NOAA ERSST v5、NCEP/NCAR Reanalysis（NOAA PSL）；El Niño：NOAA CPC ONI ≥ 1.0', ha='center', fontsize=8, color='#555')
    plt.savefig(out_png, bbox_inches='tight'); plt.close(fig)
    return dict(warm=round(float(wv), 1), cold=round(float(cv), 1), far=round(float(kv), 1), far_at=[round(float(kx)), round(float(ky))])

def sst_recent(src):
    import numpy as np, netCDF4 as nc
    C = json.load(open(os.path.join(src, 'composite.json')))
    d = nc.Dataset(URL['ersst']); la = d['lat'][:]; lo = d['lon'][:]
    iy = [i for i, x in enumerate(la) if min(C['sst_lat']) <= x <= max(C['sst_lat'])]; ix = [i for i, x in enumerate(lo) if min(C['sst_lon']) <= x <= max(C['sst_lon'])]
    tm = nc.num2date(d['time'][-3:], d['time'].units, only_use_cftime_datetimes=False)
    a = np.ma.filled(d['sst'][-3:, iy[0]:iy[-1] + 1, ix[0]:ix[-1] + 1].astype(float), np.nan)
    if (la[iy[0]] > la[iy[-1]]) != (C['sst_lat'][0] > C['sst_lat'][-1]): a = a[:, ::-1]
    cl = np.array([np.where(np.array(C['sst_clim'][t.month - 1]) == -999, np.nan, C['sst_clim'][t.month - 1]) for t in tm])
    return (a - cl).mean(0), [f'{t.year}-{t.month:02d}' for t in tm]

def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--out', default='event_climate'); ap.add_argument('--force-schematic', action='store_true'); a = ap.parse_args()
    src = os.path.join(a.out, 'indices_src'); os.makedirs(src, exist_ok=True)
    cp = os.path.join(src, 'live.json'); L = json.load(open(cp, encoding='utf-8')) if os.path.exists(cp) else {}
    raw = {}
    for k in ('oni', 'pdo', 'rmm'):
        try: raw[k] = get(URL[k])
        except Exception as e: print(k, '下載失敗', type(e).__name__)
    for k, fn in (('cpc', p_cpc), ('iri', p_iri)):
        try:
            r = fn(text(get(URL[k])))
            if r: L[k] = {**L.get(k, {}), **r}
        except Exception as e: print(k, '讀取失敗，沿用上次', type(e).__name__)
    try:
        r = p_mjo(get(URL['mjo']))
        if r: L['cpc_mjo'] = r
    except Exception as e: print('MJO 簡報讀取失敗', type(e).__name__)
    oni = p_oni(raw['oni']) if 'oni' in raw else {}
    pdo = p_pdo(raw['pdo']) if 'pdo' in raw else {}
    rmm = p_rmm(raw['rmm']) if 'rmm' in raw else {}
    if oni:
        ks = sorted(oni)[-24:]; L['oni'] = [[f'{SN[m - 1]} {y}', oni[(y, m)]] for y, m in ks]
    if pdo:
        ks = sorted(pdo)[-12:]; L['pdo'] = [[f'{y}-{m:02d}', pdo[(y, m)]] for y, m in ks]
    if rmm:
        ks = sorted(rmm)[-40:]; L['rmm'] = [[d.isoformat(), round(rmm[d][0], 2), round(rmm[d][1], 2), rmm[d][2], round(rmm[d][3], 2)] for d in ks]
    # 示意圖：ERSST 有新月份才重畫
    try:
        now, months = sst_recent(src)
        if a.force_schematic or L.get('schem', {}).get('months') != months:
            os.makedirs(os.path.join(a.out, 'indices'), exist_ok=True)
            info = schematic(src, os.path.join(a.out, 'indices', 'schematic.png'), now, months)
            L['schem'] = dict(months=months, **info); print('示意圖已更新', months)
    except Exception as e: print('示意圖略過', type(e).__name__, str(e)[:150])
    json.dump(L, open(cp, 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    # 場次：讀近期展望的場次清單
    O = json.load(open(os.path.join(a.out, 'outlook.json'), encoding='utf-8'))
    C = json.load(open(os.path.join(a.out, 'climo_1991_2020.json'), encoding='utf-8'))
    last = L['oni'][-1][1] if L.get('oni') else None; cat = enso_cat(last)
    mj = L['rmm'][-1] if L.get('rmm') else None; mph = mj[3] if mj and mj[4] >= 1 else None
    today = dt.datetime.now(TZ).date(); ev = []
    for e in O.get('events', []):
        sd, ed = dt.date.fromisoformat(e['start']), dt.date.fromisoformat(e['end'])
        sid = STN.get(e.get('reg'))
        v = venue(C, sid, sd, ed, oni, rmm, cat, mph if (sd - today).days <= 21 else None) if sid and oni else None
        ev.append(dict(id=e['id'], name=e['name'], start=e['start'], end=e['end'], reg=e.get('reg'), stn=e.get('stn'), month=sd.month, v=v))
    S = json.load(open(os.path.join(src, 'stats.json'), encoding='utf-8'))
    out = dict(enso=dict(oni=L.get('oni'), cat=cat, cpc=L.get('cpc'), iri=L.get('iri')), pdo=L.get('pdo'), mjo=dict(rmm=L.get('rmm'), cpc=L.get('cpc_mjo')),
               schem=L.get('schem'), stats=S, events=ev, ref_period='1991–2020')
    json.dump(out, open(os.path.join(a.out, 'indices.json'), 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    print('indices events', len(ev), 'ENSO', cat, 'MJO', mj and mj[3:])

if __name__ == '__main__': main()
