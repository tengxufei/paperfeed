"""Inline SVG charts, drawn in plain Python.

No charting library and no CDN on purpose: a digest is a local file you may
open with no internet, and an external <script> tag would leave you with a
blank rectangle. Everything here returns a self-contained <svg> string that
inherits colour from the page via currentColor and a few CSS variables, so
the charts follow light and dark mode without being redrawn.
"""

import math

PALETTE = ["#4c78a8", "#c2703a", "#5b9c62", "#8a6bbf", "#b8873a", "#5b8ca8"]


def _esc(text):
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _open(width, height, label=""):
    return (
        '<svg class="chart" viewBox="0 0 %d %d" width="100%%" height="%d" '
        'role="img" aria-label="%s" xmlns="http://www.w3.org/2000/svg">'
        % (width, height, height, _esc(label))
    )


def bars(pairs, width=560, bar_height=22, gap=6, label=""):
    """Horizontal bars: [(name, value), ...], largest first as given."""
    pairs = [(str(name), float(value)) for name, value in pairs if value]
    if not pairs:
        return ""
    biggest = max(value for _, value in pairs)
    label_width = 150
    track = width - label_width - 54
    height = len(pairs) * (bar_height + gap) + 6

    out = [_open(width, height, label or "bar chart")]
    for index, (name, value) in enumerate(pairs):
        y = index * (bar_height + gap)
        length = max(2, (value / biggest) * track) if biggest else 2
        colour = PALETTE[index % len(PALETTE)]
        out.append(
            '<text x="%d" y="%d" class="c-lab" text-anchor="end">%s</text>'
            % (label_width - 8, y + bar_height * 0.72, _esc(name[:34]))
        )
        out.append(
            '<rect x="%d" y="%d" width="%.1f" height="%d" rx="3" fill="%s" '
            'opacity="0.85"><title>%s: %g</title></rect>'
            % (label_width, y, length, bar_height, colour, _esc(name), value)
        )
        out.append(
            '<text x="%.1f" y="%d" class="c-val">%g</text>'
            % (label_width + length + 7, y + bar_height * 0.72, value)
        )
    out.append("</svg>")
    return "".join(out)


def stacked(rows, width=560, height=46, label=""):
    """One stacked bar: rows = [(name, value, colour_index), ...]."""
    rows = [(n, float(v), c) for n, v, c in rows if v]
    total = sum(value for _, value, _ in rows)
    if not total:
        return ""
    out = [_open(width, height, label or "composition")]
    x = 0.0
    for name, value, colour_index in rows:
        segment = value / total * width
        out.append(
            '<rect x="%.1f" y="0" width="%.1f" height="22" fill="%s" '
            'opacity="0.85"><title>%s: %g</title></rect>'
            % (x, segment, PALETTE[colour_index % len(PALETTE)], _esc(name), value)
        )
        if segment > 46:
            out.append(
                '<text x="%.1f" y="15" class="c-seg">%g</text>'
                % (x + 6, value)
            )
        x += segment

    x = 0.0
    for index, (name, value, colour_index) in enumerate(rows):
        out.append(
            '<rect x="%.1f" y="32" width="9" height="9" rx="2" fill="%s"/>'
            % (x, PALETTE[colour_index % len(PALETTE)])
        )
        out.append(
            '<text x="%.1f" y="40" class="c-lab">%s</text>'
            % (x + 13, _esc("%s (%g)" % (name[:22], value)))
        )
        x += min(200, max(90, len(name) * 6.4 + 46))
    out.append("</svg>")
    return "".join(out)


def histogram(buckets, width=560, height=140, label=""):
    """Vertical bars over a numeric axis: [(bucket_label, count), ...]."""
    buckets = [(str(name), int(value)) for name, value in buckets]
    if not any(value for _, value in buckets):
        return ""
    biggest = max(value for _, value in buckets)
    left, bottom = 30, 24
    plot_width = width - left - 10
    # headroom for the value printed above each bar, or the tallest one
    # has its label clipped by the top edge
    plot_height = height - bottom - 22
    slot = plot_width / len(buckets)

    out = [_open(width, height, label or "distribution")]
    out.append(
        '<line x1="%d" y1="%d" x2="%d" y2="%d" class="c-axis"/>'
        % (left, height - bottom, width - 10, height - bottom)
    )
    for index, (name, value) in enumerate(buckets):
        bar = (value / biggest) * plot_height if biggest else 0
        x = left + index * slot
        out.append(
            '<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="2" '
            'fill="%s" opacity="0.85"><title>%s: %d</title></rect>'
            % (
                x + slot * 0.12,
                height - bottom - bar,
                slot * 0.76,
                bar,
                PALETTE[0],
                _esc(name),
                value,
            )
        )
        out.append(
            '<text x="%.1f" y="%d" class="c-lab" text-anchor="middle">%s</text>'
            % (x + slot / 2, height - bottom + 13, _esc(name))
        )
        if value:
            out.append(
                '<text x="%.1f" y="%.1f" class="c-val" text-anchor="middle">%d</text>'
                % (x + slot / 2, height - bottom - bar - 4, value)
            )
    out.append("</svg>")
    return "".join(out)


def sparkline(series, width=560, height=110, label=""):
    """A line over time: series = [(x_label, value), ...] oldest first."""
    series = [(str(name), float(value)) for name, value in series]
    if len(series) < 2:
        return ""
    values = [value for _, value in series]
    biggest = max(values) or 1
    left, bottom = 30, 22
    plot_width = width - left - 12
    plot_height = height - bottom - 12
    step = plot_width / (len(series) - 1)

    points = [
        (left + index * step, height - bottom - (value / biggest) * plot_height)
        for index, (_, value) in enumerate(series)
    ]

    out = [_open(width, height, label or "over time")]
    out.append(
        '<line x1="%d" y1="%d" x2="%d" y2="%d" class="c-axis"/>'
        % (left, height - bottom, width - 12, height - bottom)
    )
    area = " ".join("%.1f,%.1f" % point for point in points)
    out.append(
        '<polygon points="%d,%d %s %.1f,%d" fill="%s" opacity="0.13"/>'
        % (left, height - bottom, area, points[-1][0], height - bottom, PALETTE[0])
    )
    out.append(
        '<polyline points="%s" fill="none" stroke="%s" stroke-width="2" '
        'stroke-linejoin="round"/>' % (area, PALETTE[0])
    )
    for index, (name, value) in enumerate(series):
        x, y = points[index]
        out.append(
            '<circle cx="%.1f" cy="%.1f" r="3" fill="%s"><title>%s: %g</title></circle>'
            % (x, y, PALETTE[0], _esc(name), value)
        )
    out.append(
        '<text x="%d" y="%d" class="c-lab">%s</text>'
        % (left, height - 6, _esc(series[0][0]))
    )
    out.append(
        '<text x="%d" y="%d" class="c-lab" text-anchor="end">%s</text>'
        % (width - 12, height - 6, _esc(series[-1][0]))
    )
    out.append(
        '<text x="%d" y="14" class="c-val">%g</text>' % (left - 26, biggest)
    )
    out.append("</svg>")
    return "".join(out)


def donut(pairs, width=190, height=190, label=""):
    """A ring: [(name, value), ...]."""
    pairs = [(str(name), float(value)) for name, value in pairs if value]
    total = sum(value for _, value in pairs)
    if not total:
        return ""
    cx, cy, radius, thickness = width / 2, height / 2, min(width, height) / 2 - 8, 26
    out = [_open(width, height, label or "breakdown")]
    angle = -math.pi / 2
    for index, (name, value) in enumerate(pairs):
        sweep = value / total * 2 * math.pi
        end = angle + sweep
        large = 1 if sweep > math.pi else 0
        x1, y1 = cx + radius * math.cos(angle), cy + radius * math.sin(angle)
        x2, y2 = cx + radius * math.cos(end), cy + radius * math.sin(end)
        out.append(
            '<path d="M %.1f %.1f A %.1f %.1f 0 %d 1 %.1f %.1f" fill="none" '
            'stroke="%s" stroke-width="%d" opacity="0.88">'
            "<title>%s: %g</title></path>"
            % (x1, y1, radius, radius, large, x2, y2,
               PALETTE[index % len(PALETTE)], thickness, _esc(name), value)
        )
        angle = end
    out.append(
        '<text x="%.1f" y="%.1f" class="c-big" text-anchor="middle">%d</text>'
        % (cx, cy + 6, total)
    )
    out.append("</svg>")
    return "".join(out)


def graph(nodes, edges, width=620, height=430, label=""):
    """A small force-directed graph.

    nodes: [(name, weight, tone)] where tone is 'new' | 'rising' | ''
    edges: [(name_a, name_b, weight)]

    The layout is a plain spring embedder run for a fixed number of steps
    from a deterministic starting circle, so the same data always draws the
    same picture - a graph that reshuffles on every run is impossible to
    read week to week.
    """
    if not nodes:
        return ""
    names = [name for name, _, _ in nodes]
    index_of = {name: i for i, name in enumerate(names)}
    count = len(names)
    weights = {name: weight for name, weight, _ in nodes}
    tones = {name: tone for name, _, tone in nodes}
    heaviest = max(weights.values()) or 1

    # deterministic start: evenly spaced on a circle
    positions = []
    for i in range(count):
        angle = 2 * math.pi * i / count
        positions.append([
            width / 2 + (width / 3.2) * math.cos(angle),
            height / 2 + (height / 3.2) * math.sin(angle),
        ])

    links = [
        (index_of[a], index_of[b], w)
        for a, b, w in edges
        if a in index_of and b in index_of
    ]

    for step in range(220):
        cooling = 1.0 - step / 240.0
        forces = [[0.0, 0.0] for _ in range(count)]
        for i in range(count):
            for j in range(i + 1, count):
                dx = positions[i][0] - positions[j][0]
                dy = positions[i][1] - positions[j][1]
                distance = math.hypot(dx, dy) or 0.01
                push = 5200.0 / (distance * distance)
                forces[i][0] += dx / distance * push
                forces[i][1] += dy / distance * push
                forces[j][0] -= dx / distance * push
                forces[j][1] -= dy / distance * push
        for i, j, weight in links:
            dx = positions[j][0] - positions[i][0]
            dy = positions[j][1] - positions[i][1]
            distance = math.hypot(dx, dy) or 0.01
            pull = (distance - 90) * 0.012 * min(3.0, weight)
            forces[i][0] += dx / distance * pull
            forces[i][1] += dy / distance * pull
            forces[j][0] -= dx / distance * pull
            forces[j][1] -= dy / distance * pull
        for i in range(count):
            # gentle pull to centre keeps islands from drifting off-canvas
            forces[i][0] += (width / 2 - positions[i][0]) * 0.008
            forces[i][1] += (height / 2 - positions[i][1]) * 0.008
            positions[i][0] += max(-14, min(14, forces[i][0])) * cooling
            positions[i][1] += max(-14, min(14, forces[i][1])) * cooling
            positions[i][0] = max(58, min(width - 58, positions[i][0]))
            positions[i][1] = max(24, min(height - 18, positions[i][1]))

    out = [_open(width, height, label or "subject graph")]
    heaviest_link = max([w for _, _, w in links] or [1])
    for i, j, weight in links:
        out.append(
            '<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" class="c-edge" '
            'stroke-width="%.2f"/>'
            % (
                positions[i][0], positions[i][1],
                positions[j][0], positions[j][1],
                0.6 + 2.2 * (weight / heaviest_link),
            )
        )
    for i, name in enumerate(names):
        weight = weights[name]
        radius = 6 + 16 * math.sqrt(weight / heaviest)
        tone = tones[name]
        fill = {"new": "#5b9c62", "rising": "#c2703a"}.get(tone, PALETTE[0])
        out.append(
            '<circle cx="%.1f" cy="%.1f" r="%.1f" fill="%s" opacity="0.82">'
            "<title>%s - on %d papers%s</title></circle>"
            % (
                positions[i][0], positions[i][1], radius, fill,
                _esc(name), int(weight),
                ", %s" % tone if tone else "",
            )
        )
        out.append(
            '<text x="%.1f" y="%.1f" class="c-node" text-anchor="middle">%s</text>'
            % (
                positions[i][0],
                positions[i][1] + radius + 11,
                _esc(name if len(name) <= 26 else name[:25] + "\u2026"),
            )
        )
    out.append("</svg>")
    return "".join(out)


CHART_CSS = """
.chart { display:block; max-width:100%; height:auto; margin:6px 0 2px; }
.c-lab { font-size:11px; fill:#6a727c; }
.c-val { font-size:11px; fill:#3d444c; font-weight:600; }
.c-seg { font-size:11px; fill:#fff; font-weight:600; }
.c-big { font-size:26px; fill:#3d444c; font-weight:700; }
.c-node { font-size:10.5px; fill:#4a525b;
          paint-order:stroke; stroke:#f6f7f9; stroke-width:3px;
          stroke-linejoin:round; }
.c-axis { stroke:#cfd5dc; stroke-width:1; }
.c-edge { stroke:#b9c2cd; opacity:.55; }
@media (prefers-color-scheme: dark) {
  .c-lab { fill:#98a0a8; } .c-val { fill:#cfd5dc; } .c-big { fill:#cfd5dc; }
  .c-node { fill:#aab2bb; stroke:#16181c; } .c-axis { stroke:#3c424a; } .c-edge { stroke:#464d56; }
}
"""
