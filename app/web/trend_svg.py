"""結果頁「逐年趨勢」分頁的線圖：伺服器端產生 SVG（系統在校內離線使用，不載入外部圖表套件）。"""

from decimal import Decimal
from html import escape

from markupsafe import Markup

from app.reports.trends import TrendChart, fmt_trend

W, H = 560, 280
PAD_L, PAD_R, PAD_T, PAD_B = 48, 28, 14, 30
NCU_COLOR = "#c00000"
OTHER_COLORS = ("#2e75b6", "#70ad47", "#7f7f7f", "#7030a0", "#00b0f0", "#bf9000", "#1f3864", "#a5a5a5", "#548235",
                "#c55a11")


def _nice_max(v: float) -> float:
    """y 軸上限取 1、2、2.5、5 × 10^n，刻度才是整齊的數字。"""
    if v <= 0:
        return 1.0
    mag = 10 ** len(str(int(v))) / 10 if v >= 1 else 0.1
    while mag * 10 < v:
        mag *= 10
    for step in (1, 2, 2.5, 5, 10):
        if step * mag >= v:
            return step * mag
    return 10 * mag


def series_colors(chart: TrendChart) -> list[str]:
    others = iter(OTHER_COLORS * 3)
    return [NCU_COLOR if s.is_ncu else next(others) for s in chart.series]


def trend_svg(chart: TrendChart) -> Markup:
    """一個單位一條折線；本系所紅色加粗；沒有值的年份斷開。滑鼠移到點上顯示數值。"""
    ind = chart.indicator
    values = [float(v) for s in chart.series for v in s.values.values() if v is not None]
    top = _nice_max(max(values, default=0))
    years = chart.years
    plot_w, plot_h = W - PAD_L - PAD_R, H - PAD_T - PAD_B

    def x(i: int) -> float:
        return PAD_L + (plot_w * i / (len(years) - 1) if len(years) > 1 else plot_w / 2)

    def y(v: float) -> float:
        return PAD_T + plot_h * (1 - v / top)

    parts = [f'<svg class="trend-chart" viewBox="0 0 {W} {H}" role="img" '
             f'aria-label="{escape(ind.label)}逐年趨勢">']
    for k in range(5):
        v = top * k / 4
        label = f"{v:.2f}" if ind.aggregate == "mean" else f"{v:g}"
        parts.append(f'<line x1="{PAD_L}" x2="{W - PAD_R}" y1="{y(v):.1f}" y2="{y(v):.1f}" class="grid"/>'
                     f'<text x="{PAD_L - 6}" y="{y(v) + 4:.1f}" text-anchor="end">{label}</text>')
    for i, yr in enumerate(years):
        parts.append(f'<text x="{x(i):.1f}" y="{H - 8}" text-anchor="middle">{yr}</text>')
    # 本系所最後畫，蓋在其他線上面
    order = sorted(zip(chart.series, series_colors(chart)), key=lambda p: p[0].is_ncu)
    for s, color in order:
        width = 3.5 if s.is_ncu else 1.75
        segments, current = [], []
        for i, yr in enumerate(years):
            v = s.values[yr]
            if v is None:
                if current:
                    segments.append(current)
                current = []
            else:
                current.append((i, v))
        if current:
            segments.append(current)
        for seg in segments:
            if len(seg) > 1:
                pts = " ".join(f"{x(i):.1f},{y(float(v)):.1f}" for i, v in seg)
                parts.append(f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="{width}"/>')
            for i, v in seg:
                tip = f"{s.label} {years[i]}：{fmt_trend(Decimal(v), ind)}"
                parts.append(f'<circle cx="{x(i):.1f}" cy="{y(float(v)):.1f}" r="{4.5 if s.is_ncu else 3.5}" '
                             f'fill="{color}"><title>{escape(tip)}</title></circle>')
    parts.append("</svg>")
    return Markup("".join(parts))
