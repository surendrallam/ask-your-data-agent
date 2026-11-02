"""Dependency-free SVG charts for query results.

Single-series only: horizontal bars for categories, a line (or columns, for
few points) for time. Colors come from CSS custom properties so the host page
controls light and dark mode; text uses text tokens, never the series color.
Each mark carries a <title> so hovering shows its exact value.
"""

from __future__ import annotations

import re
from typing import Any
from xml.sax.saxutils import escape

_TIME = re.compile(r"^(\d{4})([-/]\d{1,2})?([-/]\d{1,2})?( .*)?$|^\d{4}[- ]?Q[1-4]$|^Q[1-4]\b", re.I)
_TIME_COL = re.compile(r"(year|month|quarter|date|day|week|period)", re.I)
MAX_BARS = 25
FONT = "system-ui, -apple-system, Segoe UI, sans-serif"


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(",", ""))
    except ValueError:
        return None


def _fmt(v: float) -> str:
    if abs(v) >= 1000:
        return f"{v:,.0f}"
    if float(v).is_integer():
        return f"{int(v)}"
    return f"{v:,.2f}"


def pick_spec(columns: list[str], rows: list[list[Any]], hint: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Choose chart type and x/y columns. Honors a valid hint from the model."""
    if not rows or len(columns) < 2 or len(rows) < 2:
        return None
    hint = hint or {}
    if hint.get("type") == "none":
        return None
    numeric = [i for i in range(len(columns)) if all(_num(r[i]) is not None for r in rows)]
    if not numeric:
        return None
    x = columns.index(hint["x"]) if hint.get("x") in columns else None
    y = columns.index(hint["y"]) if hint.get("y") in columns and columns.index(hint["y"]) in numeric else None
    if x is None:
        non_num = [i for i in range(len(columns)) if i not in numeric]
        x = non_num[0] if non_num else 0
    if y is None:
        cands = [i for i in numeric if i != x]
        if not cands:
            return None
        y = cands[-1]
    if x == y:
        return None
    labels = [str(r[x]) for r in rows]
    is_time = bool(_TIME_COL.search(columns[x])) or all(_TIME.match(s.strip()) for s in labels)
    kind = hint.get("type") if hint.get("type") in ("bar", "line") else ("line" if is_time and len(rows) >= 4 else "bar")
    if kind == "line" and not is_time:
        kind = "bar"
    if kind == "bar" and len(rows) > MAX_BARS:
        return None
    return {"type": kind, "x": columns[x], "y": columns[y], "time": is_time}


def render_svg(columns: list[str], rows: list[list[Any]], hint: dict[str, Any] | None = None, title: str = "") -> str | None:
    spec = pick_spec(columns, rows, hint)
    if spec is None:
        return None
    xi, yi = columns.index(spec["x"]), columns.index(spec["y"])
    labels = [str(r[xi]) for r in rows]
    values = [_num(r[yi]) or 0.0 for r in rows]
    if spec["type"] == "line":
        return _line(labels, values, spec, title)
    if spec["time"]:
        return _columns(labels, values, spec, title)
    return _hbars(labels, values, spec, title)


def _frame(w: int, h: int, title: str, body: str, desc: str) -> str:
    t = escape(title)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="100%" '
        f'role="img" aria-label="{escape(desc, {chr(34): "&quot;"})}" font-family="{FONT}" font-size="12">'
        f"<title>{t}</title>"
        f'<text x="0" y="16" font-size="14" font-weight="600" fill="var(--text-primary, #0b0b0b)">{t}</text>'
        f"{body}</svg>"
    )


def _hbars(labels: list[str], values: list[float], spec: dict[str, Any], title: str) -> str:
    n = len(labels)
    band, bar = 28, 18
    label_w = min(220, max(60, max(len(s) for s in labels) * 7))
    w, top = 640, 32
    plot_w = w - label_w - 70
    h = top + n * band + 8
    vmax = max(max(values), 0) or 1.0
    parts = [f'<line x1="{label_w}" y1="{top}" x2="{label_w}" y2="{top + n * band}" stroke="var(--grid, #d9d8d4)"/>']
    for i, (lab, v) in enumerate(zip(labels, values)):
        y = top + i * band + (band - bar) / 2
        bw = max(1.0, plot_w * max(v, 0) / vmax)
        short = lab if len(lab) <= 32 else lab[:31] + "…"
        tip = escape(f"{lab}: {_fmt(v)}")
        # Square at the baseline, 4px rounded at the data end.
        r = min(4.0, bw / 2, bar / 2)
        path = (
            f"M{label_w},{y} H{label_w + bw - r} Q{label_w + bw},{y} {label_w + bw},{y + r} "
            f"V{y + bar - r} Q{label_w + bw},{y + bar} {label_w + bw - r},{y + bar} H{label_w} Z"
        )
        parts.append(
            f'<g><title>{tip}</title><path d="{path}" fill="var(--series-1, #2a78d6)"/>'
            f'<rect x="0" y="{top + i * band}" width="{w}" height="{band}" fill="transparent"/></g>'
            f'<text x="{label_w - 8}" y="{y + bar / 2 + 4}" text-anchor="end" fill="var(--text-secondary, #52514e)">{escape(short)}</text>'
        )
        if n <= 12:
            parts.append(
                f'<text x="{label_w + bw + 6}" y="{y + bar / 2 + 4}" fill="var(--text-primary, #0b0b0b)">{_fmt(v)}</text>'
            )
    desc = f"Bar chart of {spec['y']} by {spec['x']}, {n} bars"
    return _frame(w, h, title or f"{spec['y']} by {spec['x']}", "".join(parts), desc)


def _axis_y(vmin: float, vmax: float, x0: float, x1: float, y_of, ticks: int = 4) -> list[str]:
    out = []
    for k in range(ticks + 1):
        v = vmin + (vmax - vmin) * k / ticks
        y = y_of(v)
        out.append(f'<line x1="{x0}" y1="{y:.1f}" x2="{x1}" y2="{y:.1f}" stroke="var(--grid, #d9d8d4)" stroke-width="{1 if k else 1.5}"/>')
        out.append(f'<text x="{x0 - 6}" y="{y + 4:.1f}" text-anchor="end" fill="var(--text-muted, #6f6e6a)">{_fmt(v)}</text>')
    return out


def _columns(labels: list[str], values: list[float], spec: dict[str, Any], title: str) -> str:
    n = len(labels)
    w, h, left, top, bottom = 640, 300, 56, 32, 40
    plot_w, plot_h = w - left - 12, h - top - bottom
    vmax = max(max(values), 0) or 1.0
    y_of = lambda v: top + plot_h - plot_h * max(v, 0) / vmax  # noqa: E731
    band = plot_w / n
    bar = min(24.0, band - 2)
    parts = _axis_y(0, vmax, left, w - 12, y_of)
    step = max(1, n // 12)
    for i, (lab, v) in enumerate(zip(labels, values)):
        x = left + i * band + (band - bar) / 2
        y = y_of(v)
        r = min(4.0, bar / 2, (top + plot_h - y) / 2)
        base = top + plot_h
        path = (
            f"M{x:.1f},{base} V{y + r:.1f} Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f} H{x + bar - r:.1f} "
            f"Q{x + bar:.1f},{y:.1f} {x + bar:.1f},{y + r:.1f} V{base} Z"
        )
        parts.append(f'<g><title>{escape(f"{lab}: {_fmt(v)}")}</title><path d="{path}" fill="var(--series-1, #2a78d6)"/>'
                     f'<rect x="{left + i * band:.1f}" y="{top}" width="{band:.1f}" height="{plot_h}" fill="transparent"/></g>')
        if i % step == 0:
            parts.append(f'<text x="{x + bar / 2:.1f}" y="{base + 16}" text-anchor="middle" fill="var(--text-secondary, #52514e)">{escape(lab[:10])}</text>')
    desc = f"Column chart of {spec['y']} by {spec['x']}, {n} columns"
    return _frame(w, h, title or f"{spec['y']} by {spec['x']}", "".join(parts), desc)


def _line(labels: list[str], values: list[float], spec: dict[str, Any], title: str) -> str:
    n = len(labels)
    w, h, left, top, bottom = 640, 300, 56, 32, 40
    plot_w, plot_h = w - left - 16, h - top - bottom
    lo, hi = min(min(values), 0), max(values)
    hi = hi if hi > lo else lo + 1
    y_of = lambda v: top + plot_h - plot_h * (v - lo) / (hi - lo)  # noqa: E731
    x_of = lambda i: left + (plot_w * i / (n - 1) if n > 1 else plot_w / 2)  # noqa: E731
    parts = _axis_y(lo, hi, left, w - 16, y_of)
    pts = " ".join(f"{x_of(i):.1f},{y_of(v):.1f}" for i, v in enumerate(values))
    parts.append(f'<polyline points="{pts}" fill="none" stroke="var(--series-1, #2a78d6)" stroke-width="2" stroke-linejoin="round"/>')
    step = max(1, n // 10)
    for i, (lab, v) in enumerate(zip(labels, values)):
        x, y = x_of(i), y_of(v)
        parts.append(
            f'<g><title>{escape(f"{lab}: {_fmt(v)}")}</title>'
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="var(--series-1, #2a78d6)" stroke="var(--surface-1, #fcfcfb)" stroke-width="2"/>'
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="12" fill="transparent"/></g>'
        )
        if i % step == 0 or i == n - 1:
            parts.append(f'<text x="{x:.1f}" y="{top + plot_h + 16}" text-anchor="middle" fill="var(--text-secondary, #52514e)">{escape(lab[:10])}</text>')
    # Label the last point only (selective direct label).
    parts.append(f'<text x="{x_of(n - 1) - 6:.1f}" y="{y_of(values[-1]) - 10:.1f}" text-anchor="end" fill="var(--text-primary, #0b0b0b)">{_fmt(values[-1])}</text>')
    desc = f"Line chart of {spec['y']} over {spec['x']}, {n} points"
    return _frame(w, h, title or f"{spec['y']} over {spec['x']}", "".join(parts), desc)
