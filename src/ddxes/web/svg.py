"""Server-side SVG for the web interface: proof trees and the consultation curve.

The drawings carry class names only; colours and fonts come from the page's stylesheet, so the figures match
the rest of the interface and need no JavaScript.

A proof tree reads from left to right: the patient's answers (highlighted) feed the rules that used them, the
rules feed derived facts and, at the right edge, the conclusion. Every rule box shows its identifier, the
certainty factor it contributed and the short citation of the source it encodes.
"""
from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass, field
from html import escape

FACT_KINDS = {"evidence", "answer", "scale", "age", "sex", "not"}

LINE = 15            # line height of node text, px (12 px type)
PAD_X, PAD_Y = 10, 7
GAP_Y = 10           # between sibling subtrees
GAP_X = 42           # between columns
MARGIN = 6
WIDTH = {"root": 172, "rule": 156, "fact": 186, "derived": 182}
CHARS = {"root": 22, "rule": 22, "fact": 27, "derived": 26}


@dataclass
class _Box:
    node: dict
    depth: int
    cls: str
    lines: list
    w: float
    h: float
    children: list = field(default_factory=list)
    x: float = 0.0
    y: float = 0.0
    span: float = 0.0


def _cls(node: dict, depth: int) -> str:
    if depth == 0:
        return "root"
    kind = node.get("kind")
    if kind == "rule":
        return "rule"
    return "fact" if kind in FACT_KINDS else "derived"


def _wrap(text: str, width: int, max_lines: int = 4) -> list:
    parts = textwrap.wrap(text, width) or [""]
    if len(parts) > max_lines:
        parts = parts[:max_lines]
        parts[-1] = parts[-1][: width - 1].rstrip(" ,;:") + "…"
    return parts


def _cf_inline(node: dict) -> bool:
    """The CF fits on the identifier's line unless the identifier is long."""
    return node.get("cf") is not None and len(node["rule"]["id"]) <= 10


def _lines(node: dict, cls: str) -> list:
    if cls == "rule":
        head = [node["rule"]["id"]]
        if node.get("cf") is not None and not _cf_inline(node):
            head.append(f"CF {node['cf']:+.2f}")
        return head + _wrap(node["rule"]["short"], CHARS["rule"], 2)
    return _wrap(re.sub(r"(\w)\(", r"\1 (", node.get("label", "")), CHARS[cls])     # 'calf(R)' -> 'calf (R)'


def _build(node: dict, depth: int = 0) -> _Box:
    cls = _cls(node, depth)
    lines = _lines(node, cls)
    box = _Box(node, depth, cls, lines, WIDTH[cls], len(lines) * LINE + 2 * PAD_Y)
    box.children = [_build(c, depth + 1) for c in node.get("children", [])]
    return box


def _measure(b: _Box) -> None:
    for c in b.children:
        _measure(c)
    stack = sum(c.span for c in b.children) + GAP_Y * max(0, len(b.children) - 1)
    b.span = max(b.h, stack)


def _place(b: _Box, top: float, right_edge: dict) -> None:
    b.x = right_edge[b.depth] - b.w          # nodes are right-aligned in their column
    if b.children:
        stack = sum(c.span for c in b.children) + GAP_Y * (len(b.children) - 1)
        y = top + (b.span - stack) / 2
        for c in b.children:
            _place(c, y, right_edge)
            y += c.span + GAP_Y
        first, last = b.children[0], b.children[-1]
        b.y = (first.y + first.h / 2 + last.y + last.h / 2) / 2 - b.h / 2
    else:
        b.y = top + (b.span - b.h) / 2


def _walk(b: _Box):
    yield b
    for c in b.children:
        yield from _walk(c)


def _title(b: _Box) -> str:
    n = b.node
    if b.cls == "rule":
        r = n["rule"]
        cf = f", CF {n['cf']:+.2f}" if n.get("cf") is not None else ""
        return f"{r['id']}: {r['name']}{cf}. Source: {r['citation']}"
    return n.get("label", "")


def _node_svg(b: _Box) -> str:
    x, y = b.x, b.y
    out = [f'<g class="pn pn--{b.cls}{" pn--not" if b.node.get("kind") == "not" else ""}">',
           f"<title>{escape(_title(b))}</title>",
           f'<rect x="{x:.1f}" y="{y:.1f}" width="{b.w:.1f}" height="{b.h:.1f}" rx="{2 if b.cls == "fact" else 5}"/>']
    ty = y + PAD_Y + 11.5
    cf_line = 1 if b.cls == "rule" and b.node.get("cf") is not None and not _cf_inline(b.node) else -1
    for i, line in enumerate(b.lines):
        css = "pn__text"
        if b.cls == "rule":
            css = "pn__id" if i == 0 else ("pn__cf" if i == cf_line else "pn__src")
        out.append(f'<text class="{css}" x="{x + PAD_X:.1f}" y="{ty + i * LINE:.1f}">{escape(line)}</text>')
    if b.cls == "rule" and _cf_inline(b.node):
        out.append(f'<text class="pn__cf" x="{x + b.w - PAD_X:.1f}" y="{ty:.1f}" text-anchor="end">'
                   f'CF {b.node["cf"]:+.2f}</text>')
    out.append("</g>")
    return "".join(out)


def proof_tree(node: dict, label: str = "Proof tree") -> str:
    """SVG of a proof tree from `Explainer` (kinds: pathology/concept/score/triage at the root)."""
    root = _build(node)
    boxes = list(_walk(root))
    depth = max(b.depth for b in boxes)
    col_w = {d: max(b.w for b in boxes if b.depth == d) for d in range(depth + 1)}
    right_edge, x = {}, MARGIN               # the deepest column (patient answers) is on the left
    for d in range(depth, -1, -1):
        x += col_w[d]
        right_edge[d] = x
        x += GAP_X
    width = x - GAP_X + MARGIN
    _measure(root)
    _place(root, MARGIN, right_edge)
    height = root.span + 2 * MARGIN
    edges = []
    for b in boxes:
        for c in b.children:
            x1, y1 = c.x + c.w, c.y + c.h / 2
            x2, y2 = b.x - 3, b.y + b.h / 2
            mid = (x1 + x2) / 2
            edges.append(f'<path class="pe" marker-end="url(#pe-arrow)" '
                         f'd="M{x1:.1f},{y1:.1f} C{mid:.1f},{y1:.1f} {mid:.1f},{y2:.1f} {x2:.1f},{y2:.1f}"/>')
    nodes = "".join(_node_svg(b) for b in boxes)
    # Shrink to fit the column, but never below 75 % of the natural size: wider trees scroll instead.
    return (f'<svg class="proof" viewBox="0 0 {width:.0f} {height:.0f}" width="{width:.0f}" height="{height:.0f}" '
            f'style="width: min({width:.0f}px, max(100%, {0.75 * width:.0f}px))" role="img" aria-label="{escape(label)}">'
            '<defs><marker id="pe-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" '
            'orient="auto"><path d="M0,0 L8,4 L0,8 z" class="pe__head"/></marker></defs>'
            f'<g class="pe-layer">{"".join(edges)}</g>{nodes}</svg>')


def line_chart(points: list, *, x_label: str, y_label: str, y_fmt=lambda v: f"{v:.0%}", x_fmt=lambda v: f"{v:g}",
               width: int = 560, height: int = 260) -> str:
    """Single-series line chart with direct point labels. points: [(x, y, label, tooltip)]."""
    left, right, top, bottom = 52, 24, 16, 42
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    x0, x1 = 0.0, max(xs) * 1.15
    y0 = min(0.9, (min(ys) // 0.02) * 0.02)
    y1 = 1.0
    sx = lambda v: left + (v - x0) / (x1 - x0) * (width - left - right)  # noqa: E731
    sy = lambda v: top + (y1 - v) / (y1 - y0) * (height - top - bottom)  # noqa: E731
    out = [f'<svg class="chart" viewBox="0 0 {width} {height}" width="{width}" height="{height}" role="img" '
           f'aria-label="{escape(y_label)} by {escape(x_label)}">']
    steps = 4
    for i in range(steps + 1):
        v = y0 + (y1 - y0) * i / steps
        out.append(f'<line class="chart__grid" x1="{left}" x2="{width - right}" y1="{sy(v):.1f}" y2="{sy(v):.1f}"/>')
        out.append(f'<text class="chart__tick" x="{left - 8}" y="{sy(v) + 4:.1f}" text-anchor="end">{escape(y_fmt(v))}</text>')
    xt = 0
    while xt <= x1:
        out.append(f'<text class="chart__tick" x="{sx(xt):.1f}" y="{height - bottom + 18}" text-anchor="middle">{escape(x_fmt(xt))}</text>')
        xt += 1
    out.append(f'<line class="chart__axis" x1="{left}" x2="{width - right}" y1="{height - bottom}" y2="{height - bottom}"/>')
    out.append(f'<text class="chart__label" x="{(left + width - right) / 2:.0f}" y="{height - 6}" text-anchor="middle">{escape(x_label)}</text>')
    path = " ".join(f"{'M' if i == 0 else 'L'}{sx(px):.1f},{sy(py):.1f}" for i, (px, py, *_rest) in enumerate(points))
    out.append(f'<path class="chart__line" d="{path}"/>')
    for i, (px, py, label, tip) in enumerate(points):
        out.append(f'<g class="chart__pt"><title>{escape(tip)}</title>'
                   f'<circle cx="{sx(px):.1f}" cy="{sy(py):.1f}" r="4.5"/>'
                   f'<text class="chart__val" x="{sx(px):.1f}" y="{sy(py) - (12 if i % 2 == 0 else -20):.1f}" '
                   f'text-anchor="middle">{escape(label)}</text></g>')
    out.append("</svg>")
    return "".join(out)
