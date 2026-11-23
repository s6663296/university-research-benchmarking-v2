"""AI 圖表分析的圖片（網頁 AI 總結分頁）：仿研發處過往簡報第 2、4、5、6 頁，每張 16:9。

伺服器端產生 SVG（系統在校內離線使用，不載入外部套件），瀏覽器再轉成 PNG 下載。
- 逐年趨勢：一張放兩個指標，每個指標上方「圖表說明」、中間線圖、下方紅框重點（第 2、4 頁）
- 兩期比較表：各校兩期數值與漲跌幅 ▲▼，本系所整列標黃，下方圖表說明（第 5、6 頁上半）
- 兩期泡泡圖：x = FWCI、y = 論文數、泡泡大小 = 被引次數，兩期各一張，下方紅框重點（第 5、6 頁下半）
"""

import math
from dataclasses import dataclass
from decimal import Decimal
from html import escape

from markupsafe import Markup

from app.ai.charts import PAIRS, PERIOD_COLS, ChartData, PeriodTable, change, fmt_period
from app.reports.emphasis import emphasis, plain
from app.reports.trends import CITATION_BASED, RECENT_NOTE, TrendChart, fmt_trend, trend_label
from app.web.trend_svg import NCU_COLOR, OTHER_COLORS, series_colors

W, H = 1280, 720
FONT = "'Microsoft JhengHei','PingFang TC','Noto Sans TC','Heiti TC',sans-serif"
BROWN, GRAY, DARK, RED = "#7a3e00", "#595959", "#262626", "#e00000"
HEAD_H = 88
FOOT_Y = 708                        # 頁底註記的基線
BOTTOM = 684                        # 內容（紅框）不可超過
AI_NOTE = "※本頁由 AI 依系統數據統整"
BOLD = ' font-weight="bold"'
LABEL_GAP = 15                      # 線尾校名的最小行距

Line = list[tuple[str, bool]]        # 一行：(文字, 是否紅字) 片段


@dataclass
class Slide:
    title: str
    filename: str
    svg: Markup


# ---- 文字 ----

def _cw(ch: str, size: float) -> float:
    """字寬估計：中文、全形字一個字寬，英數約半個字寬。"""
    return size if ord(ch) >= 0x2E80 else size * 0.56


def text_width(text: str, size: float) -> float:
    return sum(_cw(c, size) for c in text)


def _tokens(segments: Line) -> list[tuple[str, bool]]:
    """斷行單位：中文逐字，英數連在一起的整段（數字、英文名不從中間斷開）。"""
    out = []
    for text, red in segments:
        word = ""
        for ch in text:
            if ord(ch) < 0x2E80 and not ch.isspace():
                word += ch
                continue
            if word:
                out.append((word, red))
                word = ""
            out.append((ch, red))
        if word:
            out.append((word, red))
    return out


def wrap(segments: Line, width: float, size: float, indent: float = 0) -> list[Line]:
    """依寬度換行；indent 是第二行起的縮排寬度（編號、□ 後面對齊）。"""
    lines: list[Line] = [[]]
    used = 0.0
    for tok, red in _tokens(segments):
        w = text_width(tok, size)
        limit = width - (indent if len(lines) > 1 else 0)
        if used + w > limit and used > 0 and not tok.isspace():
            lines.append([])
            used = 0.0
        if not lines[-1] and tok.isspace():
            continue
        line = lines[-1]
        if line and line[-1][1] == red:
            line[-1] = (line[-1][0] + tok, red)
        else:
            line.append((tok, red))
        used += w
    for line in lines:                              # 行尾空白不佔寬度
        while line and not line[-1][0].rstrip():
            line.pop()
        if line:
            line[-1] = (line[-1][0].rstrip(), line[-1][1])
    return [ln for ln in lines if ln] or [[("", False)]]


def _tspans(line: Line, color: str, red_bold: bool = True) -> str:
    out = []
    for text, red in line:
        t = escape(text)
        out.append(f'<tspan fill="{RED}"{BOLD if red_bold else ""}>{t}</tspan>' if red
                   else f'<tspan fill="{color}">{t}</tspan>')
    return "".join(out)


def _text(x: float, y: float, text: str, size: float, color: str = DARK, anchor: str = "start",
          bold: bool = False) -> str:
    weight = BOLD if bold else ""
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size:g}" fill="{color}" text-anchor="{anchor}"{weight}>'
            f'{escape(text)}</text>')


def fit(text: str, width: float, size: float, minimum: float = 12) -> float:
    while size > minimum and text_width(text, size) > width:
        size -= 1
    return size


# ---- 版面 ----

def _frame(title: str, body: list[str], foot: str = "") -> Markup:
    """頁首金色標題列＋內容＋頁底 AI 註記（同過往簡報的頁首）。"""
    size = fit(title, W - 80 - 170, 38, 22)
    note = AI_NOTE + (f"　{foot}" if foot else "")
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" class="ai-chart-svg" viewBox="0 0 {W} {H}" width="{W}" '
        f'height="{H}" font-family="{FONT}" role="img" aria-label="{escape(title)}">',
        '<defs><linearGradient id="head" x1="0" x2="1" y1="0" y2="0">'
        '<stop offset="0" stop-color="#f3c14d"/><stop offset="1" stop-color="#fdebb3"/></linearGradient></defs>',
        f'<rect width="{W}" height="{H}" fill="#ffffff"/>',
        f'<rect width="{W}" height="{HEAD_H}" fill="url(#head)"/>',
        _text(40, HEAD_H / 2 + size * 0.36, title, size, BROWN, bold=True),
        f'<path d="M{W - 170},0 H{W} V34 H{W - 158} Z" fill="#c55a11"/>',
        _text(W - 82, 24, "AI 圖表分析", 17, "#ffffff", "middle", True),
        *body,
        _text(30, FOOT_Y, note, fit(note, W - 60, 13, 9), GRAY),
        "</svg>",
    ]
    return Markup("".join(parts))


def _notes(x: float, y: float, width: float, notes: list[str], size: float, line_h: float) -> tuple[list[str], float]:
    """「圖表說明：」＋編號條列；回傳 (SVG 片段, 結束的 y)。"""
    parts = [_text(x, y + size, "圖表說明：", size, DARK, bold=True)]
    y += line_h
    for i, note in enumerate(notes, 1):
        num = f"{i}. "
        indent = text_width(num, size)
        for k, line in enumerate(wrap(emphasis(note), width, size, indent)):
            lx = x if k == 0 else x + indent
            prefix = f'<tspan fill="{DARK}">{num}</tspan>' if k == 0 else ""
            parts.append(f'<text x="{lx:.1f}" y="{y + size:.1f}" font-size="{size:g}">{prefix}{_tspans(line, DARK)}</text>')
            y += line_h
    return parts, y


def _notes_height(width: float, notes: list[str], size: float, line_h: float) -> float:
    lines = 1 + sum(len(wrap(emphasis(n), width, size, text_width(f"{i}. ", size))) for i, n in enumerate(notes, 1))
    return lines * line_h


def _fit_notes(width: float, notes: list[str], max_h: float, size: float = 18, minimum: float = 13) -> float:
    while size > minimum and _notes_height(width, notes, size, size * 1.4) > max_h:
        size -= 1
    return size


def _callout(x: float, bottom: float, width: float, texts: list[str], size: float = 20) -> tuple[list[str], float]:
    """紅框重點（過往簡報的紅圈）：圓角紅框，框內紅色粗體字；回傳 (SVG 片段, 框的上緣)。"""
    texts = [t for t in texts if t]
    if not texts:
        return [], bottom
    inner = width - 64
    while True:
        lines = [ln for t in texts for ln in wrap([(plain(t), True)], inner, size)]
        if len(lines) <= 4 or size <= 14:
            break
        size -= 1
    line_h = size * 1.35
    height = len(lines) * line_h + 30
    top = bottom - height
    parts = [f'<rect x="{x + 3:.1f}" y="{top:.1f}" width="{width - 6:.1f}" height="{height:.1f}" '
             f'rx="{min(height / 2, 44):.1f}" fill="#fff5f5" stroke="{RED}" stroke-width="5"/>']
    y = top + 15 + size
    for line in lines:
        parts.append(_text(x + width / 2, y - size * 0.12, "".join(t for t, _ in line), size, RED, "middle", True))
        y += line_h
    return parts, top


# ---- 線圖 ----

def nice_max(v: float) -> float:
    """y 軸上限：比 trend_svg 細的級距（1、1.2、1.5、2、2.5、3、4、5、6、8 × 10^n），圖才不會只用到一半高度；
    分成 4 格後刻度仍是整齊的數字。"""
    if v <= 0:
        return 1.0
    mag = 10 ** math.floor(math.log10(v))
    for step in (1, 1.2, 1.6, 2, 2.4, 3, 4, 5, 6, 8, 10):
        if step * mag >= v - 1e-9:
            return step * mag
    return 10 * mag


def _line_chart(chart: TrendChart, x: float, y: float, w: float, h: float) -> list[str]:
    """一校一條線（本系所紅色加粗），線尾標校名（同過往簡報，不另放圖例）。"""
    ind = chart.indicator
    series = [(s, c) for s, c in zip(chart.series, series_colors(chart)) if any(v is not None for v in s.values.values())]
    values = [float(v) for s, _ in series for v in s.values.values() if v is not None]
    vmax = max(values, default=0)
    if ind.aggregate != "mean" and vmax <= 40:     # 篇數少時刻度要是整數（4 格，每格整數篇）
        top = float(max(4, math.ceil(vmax / 4) * 4))
    else:
        top = nice_max(vmax)
    years = chart.years
    label_w = max((text_width(s.label, 13) for s, _ in series), default=40) + 12
    pl, pr, pt, pb = 50, min(label_w, 150), 8, 26
    pw, ph = w - pl - pr, h - pt - pb

    def px(i: int) -> float:
        return x + pl + (pw * i / (len(years) - 1) if len(years) > 1 else pw / 2)

    def py(v: float) -> float:
        return y + pt + ph * (1 - v / top)

    parts = []
    for k in range(5):
        v = top * k / 4
        label = f"{v:.2f}" if ind.aggregate == "mean" else f"{v:g}"
        parts.append(f'<line x1="{x + pl:.1f}" x2="{x + pl + pw:.1f}" y1="{py(v):.1f}" y2="{py(v):.1f}" '
                     f'stroke="#d9d9d9" stroke-width="1"/>')
        parts.append(_text(x + pl - 6, py(v) + 4, label, 12, GRAY, "end"))
    for i, yr in enumerate(years):
        parts.append(_text(px(i), y + h - 6, str(yr), 12, GRAY, "middle"))
    ends = []
    for s, color in sorted(series, key=lambda p: p[0].is_ncu):   # 本系所最後畫，蓋在上面
        width = 3.5 if s.is_ncu else 2
        pts = [(i, float(s.values[yr])) for i, yr in enumerate(years) if s.values[yr] is not None]
        seg: list[tuple[int, float]] = []
        for i, yr in enumerate(years):
            if s.values[yr] is None:
                if len(seg) > 1:
                    parts.append(_poly(seg, px, py, color, width))
                seg = []
            else:
                seg.append((i, float(s.values[yr])))
        if len(seg) > 1:
            parts.append(_poly(seg, px, py, color, width))
        for i, v in pts:
            tip = f"{s.label} {years[i]}：{fmt_trend(Decimal(str(v)), ind)}"
            parts.append(f'<circle cx="{px(i):.1f}" cy="{py(v):.1f}" r="{4.5 if s.is_ncu else 3.5}" fill="{color}">'
                         f'<title>{escape(tip)}</title></circle>')
        if pts:
            ends.append([py(pts[-1][1]), s, color, px(pts[-1][0])])
    # 線尾校名：依高度排序後往下推開，避免重疊
    ends.sort(key=lambda e: e[0])
    for k in range(1, len(ends)):
        ends[k][0] = max(ends[k][0], ends[k - 1][0] + LABEL_GAP)
    if ends:                                    # 超出圖底時由下往上推回，只動擠在一起的那幾個
        ends[-1][0] = min(ends[-1][0], y + pt + ph)
        for k in range(len(ends) - 2, -1, -1):
            ends[k][0] = min(ends[k][0], ends[k + 1][0] - LABEL_GAP)
    for ly, s, color, lx in ends:
        parts.append(f'<text x="{x + pl + pw + 6:.1f}" y="{ly + 4:.1f}" font-size="13" fill="{color}"'
                     f'{BOLD if s.is_ncu else ""}>{escape(s.label)}</text>')
    return parts


def _poly(seg, px, py, color: str, width: float) -> str:
    pts = " ".join(f"{px(i):.1f},{py(v):.1f}" for i, v in seg)
    return f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="{width}"/>'


# ---- 各頁 ----

def trend_slide(dept: str, period: str, charts: list[TrendChart], notes: dict[str, dict]) -> Slide:
    """逐年趨勢：左右各一個指標（同過往簡報第 4 頁）。"""
    names = "與".join(c.indicator.label for c in charts)
    title = f"{period} {dept}逐年趨勢：{'、'.join(c.indicator.label for c in charts)}"
    body = []
    half = (W - 60 - 40) / 2
    for chart, x in zip(charts, (30, 30 + half + 40)):
        note = notes.get(chart.indicator_id, {})
        body.append(_text(x + half / 2, 128, f"{period} {trend_label(chart.indicator)}", fit(
            f"{period} {trend_label(chart.indicator)}", half, 22, 15), DARK, "middle", True))
        callout, box_top = _callout(x, BOTTOM, half, [note.get("highlight") or ""])
        body += callout
        y = 146
        if note.get("notes"):
            max_h = (box_top - y) * 0.42
            size = _fit_notes(half, note["notes"], max_h)
            parts, y = _notes(x, y, half, note["notes"], size, size * 1.4)
            body += parts
        chart_bottom = box_top - 10 if box_top < BOTTOM else BOTTOM
        body += _line_chart(chart, x, y + 6, half, chart_bottom - y - 6)
    recent = any(c.indicator_id in CITATION_BASED for c in charts)
    foot = f"註：{RECENT_NOTE}" if recent else ""
    return Slide(title, f"{dept}_{names}逐年趨勢.png", _frame(title, body, foot))


def _arrow(a: Decimal | None, b: Decimal | None) -> tuple[str, str]:
    """漲跌幅欄：(文字, 顏色)；上升紅色 ▲，下降黑色 ▼（同過往簡報）。"""
    pct = change(a, b)
    if pct is None:
        return "—", GRAY
    if pct > 0:
        return f"▲ {pct:.2f}%", RED
    if pct < 0:
        return f"▼ {pct:.2f}%", DARK
    return "0.00%", DARK


def table_slide(dept: str, t: PeriodTable, notes: list[str]) -> Slide:
    title = f"兩期比較：{dept}與對標學校之間的變化"
    x0, width = 30, W - 60
    school_w = min(max(max((text_width(r.school, 16) for r in t.rows), default=60) + 24, 120), 220)
    n_a, n_b = len(PERIOD_COLS), len(PERIOD_COLS) * 2
    col_w = (width - school_w) / (n_a + n_b)
    head_y, sub_h = 100, 34
    room = 430 if notes else BOTTOM - head_y - 2 * sub_h
    row_h = max(min(38, room / max(len(t.rows), 1)), 26)
    size = 17 if row_h >= 32 else 15
    body = []
    # 表頭：兩期各一組
    groups = [(x0 + school_w, n_a * col_w, t.base_period, "#d9d9d9"), (x0 + school_w + n_a * col_w, n_b * col_w,
                                                                       t.period, "#bdd7ee")]
    body.append(f'<rect x="{x0}" y="{head_y}" width="{school_w:.1f}" height="{2 * sub_h}" fill="#f2f2f2"/>')
    body.append(_text(x0 + school_w / 2, head_y + sub_h + 6, "學校", 17, DARK, "middle", True))
    for gx, gw, label, color in groups:
        body.append(f'<rect x="{gx:.1f}" y="{head_y}" width="{gw:.1f}" height="{sub_h}" fill="{color}"/>')
        body.append(_text(gx + gw / 2, head_y + 23, label, 17, DARK, "middle", True))
    cols = [(name, col, False) for col, name in PERIOD_COLS] + \
        [c for col, name in PERIOD_COLS for c in ((name, col, False), ("漲跌幅", col, True))]
    for k, (name, _, _) in enumerate(cols):
        cx = x0 + school_w + k * col_w
        body.append(f'<rect x="{cx:.1f}" y="{head_y + sub_h}" width="{col_w:.1f}" height="{sub_h}" fill="#f2f2f2"/>')
        body.append(_text(cx + col_w / 2, head_y + sub_h + 23, name, fit(name, col_w - 6, 15, 10), DARK, "middle",
                          True))
    y = head_y + 2 * sub_h
    for i, r in enumerate(t.rows):
        fill = "#ffc000" if r.is_own else ("#ffffff" if i % 2 == 0 else "#f7f7f7")
        body.append(f'<rect x="{x0}" y="{y:.1f}" width="{width}" height="{row_h:.1f}" fill="{fill}"/>')
        base = y + row_h / 2 + size * 0.36
        body.append(_text(x0 + 10, base, r.school, fit(r.school, school_w - 14, size, 11), DARK, bold=r.is_own))
        for k, (name, col, is_pct) in enumerate(cols):
            cx = x0 + school_w + (k + 1) * col_w - 8
            if k < n_a:
                text, color = fmt_period(col, r.a.get(col)), DARK
            elif is_pct:
                text, color = _arrow(r.a.get(col), r.b.get(col))
            else:
                text = fmt_period(col, r.b.get(col))
                pct = change(r.a.get(col), r.b.get(col))
                color = RED if pct is not None and pct > 0 else DARK
            body.append(_text(cx, base, text, fit(text, col_w - 12, size, 10), color, "end", r.is_own))
        y += row_h
    body.append(f'<rect x="{x0}" y="{head_y}" width="{width}" height="{y - head_y:.1f}" fill="none" '
                f'stroke="#ed7d31" stroke-width="1.5"/>')
    split = x0 + school_w + n_a * col_w
    body.append(f'<line x1="{split:.1f}" x2="{split:.1f}" y1="{head_y}" y2="{y:.1f}" stroke="#ed7d31" '
                f'stroke-width="1.5"/>')
    if notes:
        size = _fit_notes(width, notes, BOTTOM - y - 18)
        parts, _ = _notes(x0, y + 18, width, notes, size, size * 1.4)
        body += parts
    foot = "論文數、Top 1%、被引次數為期間總數（不除以教師數）；被引次數排除自我引用"
    if t.differences:
        foot += f"；兩期比較基準不同：{'、'.join(t.differences)}"
    return Slide(title, f"{dept}_兩期比較表.png", _frame(title, body, foot))


def x_ticks(xr: tuple[float, float], step: float) -> list[float]:
    """FWCI 軸刻度：從下限開始每隔 step 的倍數一格，最多 6 格。"""
    tick = step
    while (xr[1] - xr[0]) / tick > 6 + 1e-9:
        tick *= 2
    n = int((xr[1] - xr[0]) / tick + 1e-9)
    return [round(xr[0] + tick * k, 4) for k in range(n + 1)]


def _bubbles(t: PeriodTable, which: str, x: float, y: float, w: float, h: float, label: str,
             xr: tuple[float, float], step: float, top: float, cmax: float) -> list[str]:
    pl, pr, pt, pb = 64, 16, 10, 44
    pw, ph = w - pl - pr, h - pt - pb

    def px(v: float) -> float:
        return x + pl + pw * (v - xr[0]) / (xr[1] - xr[0])

    def py(v: float) -> float:
        return y + pt + ph * (1 - v / top)

    parts = [f'<rect x="{x + pl:.1f}" y="{y + pt:.1f}" width="{pw:.1f}" height="{ph:.1f}" fill="none" '
             f'stroke="#bfbfbf"/>']
    for k in range(5):
        v = top * k / 4
        parts.append(f'<line x1="{x + pl:.1f}" x2="{x + pl + pw:.1f}" y1="{py(v):.1f}" y2="{py(v):.1f}" '
                     f'stroke="#e7e6e6"/>')
        parts.append(_text(x + pl - 6, py(v) + 4, f"{v:,.0f}", 12, GRAY, "end"))
    for v in x_ticks(xr, step):
        parts.append(_text(px(v), y + pt + ph + 18, f"{v:.2f}", 12, GRAY, "middle"))
    parts.append(_text(x + pl + pw / 2, y + h - 4, "FWCI", 13, GRAY, "middle"))
    parts.append(f'<text x="{x + 14:.1f}" y="{y + pt + ph / 2:.1f}" font-size="13" fill="{GRAY}" text-anchor="middle" '
                 f'transform="rotate(-90 {x + 14:.1f} {y + pt + ph / 2:.1f})">論文數</text>')
    parts.append(_text(x + pl + 14, y + pt + 28, f"{label}年", 20, DARK, bold=True))
    others = iter(OTHER_COLORS * 3)
    rows = []
    for r in t.rows:
        color = NCU_COLOR if r.is_own else next(others)
        v = getattr(r, which)
        if v.get("fwci") is None or v.get("total") is None:
            continue
        c = float(v.get("citations") or 0)
        rows.append((r, color, float(v["fwci"]), float(v["total"]), 7 + 30 * math.sqrt(c / cmax) if cmax else 7))
    for r, color, fx, ty, rad in sorted(rows, key=lambda e: -e[4]):     # 大的先畫，小泡泡不被蓋住
        parts.append(f'<circle cx="{px(fx):.1f}" cy="{py(ty):.1f}" r="{rad:.1f}" fill="{color}" fill-opacity="0.55" '
                     f'stroke="{color}" stroke-width="{2.5 if r.is_own else 1.2}">'
                     f'<title>{escape(r.school)}：FWCI {fx:.2f}，論文數 {ty:,.0f}</title></circle>')
    for r, color, fx, ty, rad in rows:
        anchor_right = px(fx) + rad + 4 + text_width(r.school, 13) < x + pl + pw
        lx = px(fx) + rad + 4 if anchor_right else px(fx) - rad - 4
        parts.append(f'<text x="{lx:.1f}" y="{py(ty) + 4:.1f}" font-size="13" fill="{NCU_COLOR if r.is_own else DARK}" '
                     f'text-anchor="{"start" if anchor_right else "end"}"{BOLD if r.is_own else ""}>'
                     f'{escape(r.school)}</text>')
    return parts


def bubble_slide(dept: str, t: PeriodTable, highlights: list[str]) -> Slide:
    title = f"兩期比較：{dept}與對標學校的論文數與 FWCI 分布"
    pts = [v for r in t.rows for v in (r.a, r.b)]
    fwci = [float(v["fwci"]) for v in pts if v.get("fwci") is not None]
    total = [float(v["total"]) for v in pts if v.get("total") is not None]
    cites = [float(v.get("citations") or 0) for v in pts]
    lo, hi = (min(fwci), max(fwci)) if fwci else (0.0, 1.0)
    pad = max((hi - lo) * 0.1, 0.05)
    step = 0.05 if hi - lo < 0.6 else 0.1 if hi - lo < 1.5 else 0.25     # 刻度落在整齊的數字上
    xr = (max(math.floor((lo - pad) / step) * step, 0.0), math.ceil((hi + pad) / step) * step)
    top = nice_max(max(total, default=0) * 1.08)
    body, box_top = _callout(30, BOTTOM, W - 60, highlights)
    half = (W - 60 - 30) / 2
    chart_h = (box_top - 12 if box_top < BOTTOM else BOTTOM) - 100
    for which, label, x in (("a", t.base_period, 30), ("b", t.period, 30 + half + 30)):
        body += _bubbles(t, which, x, 100, half, chart_h, label, xr, step, top, max(cites, default=0))
    foot = "泡泡大小為被引次數（排除自我引用）；本系所為紅色"
    return Slide(title, f"{dept}_兩期泡泡圖.png", _frame(title, body, foot))


def slides(data: ChartData, notes: dict) -> list[Slide]:
    """依 AI 圖表分析的內容產生各頁圖片：逐年趨勢兩兩一張；有前期時再加兩期比較表與泡泡圖。"""
    by_ind = {c.indicator_id: c for c in data.charts}
    chart_notes = {c["indicator"]: c for c in notes.get("charts") or []}
    out = []
    for pair in PAIRS:
        charts = [by_ind[i] for i in pair if i in by_ind]
        if charts:
            out.append(trend_slide(data.dept, data.period, charts, chart_notes))
    period = notes.get("period") or {}
    if data.table and data.table.rows:
        out.append(table_slide(data.dept, data.table, period.get("notes") or []))
        out.append(bubble_slide(data.dept, data.table, period.get("highlights") or []))
    return out
