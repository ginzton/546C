#!/usr/bin/env python3
"""
Multi-layer pattern generator with live previews and per-layer SVG export.

Layers:
1) fabric_rings
2) fabric_slices (A - C)
3) wooden_board_engraving
4) wooden_board_cut_through
"""

from __future__ import annotations

import math
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
except ImportError as e:
    print("Tkinter is not available in this Python build.", file=sys.stderr)
    print(str(e), file=sys.stderr)
    sys.exit(1)


@dataclass(frozen=True)
class Params:
    n_rings: int = 4
    w_rings: float = 2.0
    w_strips: float = 0.5
    radius_largest_ring: float = 15.0
    radius_smallest_ring: float = 4.0
    n_beat_steps: int = 16
    extra_length_electrode: float = 5.0
    radius_velostate_circle: float = 18.0
    r_circle_cut_through: float = 20.0
    via_radius: float = 0.1
    via_offset_slice: float = 1.0


DEFAULT = Params()
STATE_PATH = Path(__file__).resolve().with_name(".multilayer_pattern_gui_state.json")

FIELD_ROWS: tuple[tuple[str, str, str], ...] = (
    ("n_rings", "n_rings", "int"),
    ("w_rings", "w_rings [cm]", "float"),
    ("w_strips", "w_strips [cm]", "float"),
    ("radius_largest_ring", "radius_largest_ring [cm]", "float"),
    ("radius_smallest_ring", "radius_smallest_ring [cm]", "float"),
    ("n_beat_steps", "n_beat_steps", "int"),
    ("extra_length_electrode", "extra_length_electrode [cm]", "float"),
    ("radius_velostate_circle", "Radius_velostate_circle [cm]", "float"),
    ("r_circle_cut_through", "r_circle_cut_through [cm]", "float"),
    ("via_radius", "via_radius [cm]", "float"),
    ("via_offset_slice", "via_offset_slice [cm]", "float"),
)

LAYER_NAMES = (
    "fabric_rings",
    "fabric_slices",
    "wooden_board_engraving",
    "wooden_board_cut_through",
    "all_layers",
)

CANVAS_SIZE = 520
REDRAW_MS = 100
SIDEBAR_MIN = 340


def _field_labels() -> tuple[tuple[str, str, str], ...]:
    rows: list[tuple[str, str, str]] = []
    for key, label, kind in FIELD_ROWS:
        val = getattr(DEFAULT, key)
        if kind == "int":
            d = str(int(val))
        else:
            d = str(int(val)) if val == int(val) else str(val)
        rows.append((key, f"{label} (default {d})", kind))
    return tuple(rows)


FIELD_SPEC = _field_labels()


def ring_center_radii(p: Params) -> list[float]:
    if p.n_rings < 1:
        raise ValueError("n_rings must be >= 1")
    if p.radius_largest_ring < p.radius_smallest_ring:
        raise ValueError("radius_largest_ring must be >= radius_smallest_ring")
    if p.n_rings == 1:
        return [(p.radius_smallest_ring + p.radius_largest_ring) / 2.0]
    span = p.radius_largest_ring - p.radius_smallest_ring
    return [p.radius_smallest_ring + span * i / (p.n_rings - 1) for i in range(p.n_rings)]


def beat_center_degrees(i: int, p: Params) -> float:
    return (360.0 / p.n_beat_steps) * i


def electrode_outer_radius_cm(p: Params) -> float:
    r = p.radius_largest_ring + p.extra_length_electrode
    if r <= 0:
        raise ValueError("radius_largest_ring + extra_length_electrode must be > 0.")
    return r


def iter_electrode_slices(p: Params) -> list[tuple[float, float, float, float]]:
    """(r_in, r_out, center_deg, span_deg) with full pizza partition."""
    r_out = electrode_outer_radius_cm(p)
    span = 360.0 / p.n_beat_steps
    return [(0.0, r_out, beat_center_degrees(i, p) + span / 2.0, span) for i in range(p.n_beat_steps)]


def strip_polygon_vertices(i: int, p: Params) -> list[tuple[float, float]]:
    theta = math.radians(beat_center_degrees(i, p))
    half_len = electrode_outer_radius_cm(p)
    half_w = p.w_strips / 2.0
    c, s = math.cos(theta), math.sin(theta)
    corners_local = [
        (-half_len, -half_w),
        (half_len, -half_w),
        (half_len, half_w),
        (-half_len, half_w),
    ]
    out: list[tuple[float, float]] = []
    for x, y in corners_local:
        out.append((x * c - y * s, x * s + y * c))
    return out


def hole_angle_deg_for_ring(i: int, p: Params) -> float:
    if p.n_rings <= 1:
        return 360.0 / max(1, p.n_rings)
    start = 360.0 / p.n_rings
    end = start * p.n_rings
    return start + (end - start) * (i / (p.n_rings - 1))


def via_center(i: int, ring_radius: float, p: Params) -> tuple[float, float]:
    ang = math.radians(hole_angle_deg_for_ring(i, p))
    return (ring_radius * math.cos(ang), ring_radius * math.sin(ang))


def electrode_via_centers(p: Params, inward_offset_cm: float) -> list[tuple[float, float]]:
    """
    One via per pizza slice centerline, offset inward from the outer arc.
    """
    r_out = electrode_outer_radius_cm(p)
    r_via = r_out - inward_offset_cm
    if r_via <= 0:
        raise ValueError("Electrode via radius must be > 0; increase electrode radius or reduce offset.")
    centers: list[tuple[float, float]] = []
    for _r0, _r1, cdeg, _sp in iter_electrode_slices(p):
        a = math.radians(cdeg)
        centers.append((r_via * math.cos(a), r_via * math.sin(a)))
    return centers


def annular_sector_polygons_cm(
    r_inner: float, r_outer: float, center_deg: float, span_deg: float, nseg: int = 36
) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    half = span_deg / 2.0
    a0 = math.radians(center_deg - half)
    a1 = math.radians(center_deg + half)
    nseg = max(2, nseg)

    def pt(r: float, a: float) -> tuple[float, float]:
        return (r * math.cos(a), r * math.sin(a))

    outer = [pt(r_outer, a0 + (a1 - a0) * i / nseg) for i in range(nseg + 1)]
    if r_inner <= 0:
        inner: list[tuple[float, float]] = []
    else:
        inner = [pt(r_inner, a1 - (a1 - a0) * i / nseg) for i in range(nseg + 1)]
    return outer, inner


def annular_sector_path(r_inner: float, r_outer: float, center_deg: float, span_deg: float) -> str:
    outer, inner = annular_sector_polygons_cm(r_inner, r_outer, center_deg, span_deg)
    if not inner:
        parts = [f"M 0 0", f"L {outer[0][0]:.6g} {outer[0][1]:.6g}"]
    else:
        parts = [f"M {outer[0][0]:.6g} {outer[0][1]:.6g}"]
    for x, y in outer[1:]:
        parts.append(f"L {x:.6g} {y:.6g}")
    if not inner:
        parts.append("L 0 0")
    else:
        for x, y in inner:
            parts.append(f"L {x:.6g} {y:.6g}")
    parts.append("Z")
    return " ".join(parts)


def closed_polygon_path(vertices: list[tuple[float, float]]) -> str:
    if not vertices:
        return ""
    parts = [f"M {vertices[0][0]:.6g} {vertices[0][1]:.6g}"]
    for x, y in vertices[1:]:
        parts.append(f"L {x:.6g} {y:.6g}")
    parts.append("Z")
    return " ".join(parts)


def layout_extent_cm(p: Params) -> float:
    centers = ring_center_radii(p)
    ring_env = max(centers) + p.w_rings / 2.0
    elec_r = electrode_outer_radius_cm(p)
    strip_r = math.hypot(elec_r, p.w_strips / 2.0)
    hole_r = max(centers) + p.via_radius
    outer = max(ring_env, elec_r, strip_r, p.radius_velostate_circle, p.r_circle_cut_through, hole_r)
    pad = max(1.0, p.w_rings, p.via_radius) + 0.5
    return outer + pad


def _xml_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _svg_header(p: Params, title: str) -> tuple[list[str], float]:
    extent = layout_extent_cm(p)
    vb = -extent
    size = 2 * extent
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vb} {vb} {size} {size}" width="{size:.6g}cm" height="{size:.6g}cm">',
        f"<title>{_xml_escape(title)}</title>",
    ]
    return parts, extent


def build_svg_fabric_rings(p: Params) -> str:
    centers = ring_center_radii(p)
    parts, _ = _svg_header(p, "Layer 1 - fabric_rings")
    parts.append('<g id="fabric_rings">')
    for rc in centers:
        r_in = rc - p.w_rings / 2.0
        r_out = rc + p.w_rings / 2.0
        if r_in <= 0:
            raise ValueError("Ring inner outline radius must be > 0. Reduce w_rings or increase smallest radius.")
        parts.append(
            f'<circle cx="0" cy="0" r="{r_in:.6g}" fill="none" stroke="#1f4e79" stroke-width="0.05"/>'
        )
        parts.append(
            f'<circle cx="0" cy="0" r="{r_out:.6g}" fill="none" stroke="#1f4e79" stroke-width="0.05"/>'
        )
    parts.append("</g></svg>")
    return "\n".join(parts)


def build_svg_fabric_slices(p: Params) -> str:
    parts, _ = _svg_header(p, "Layer 2 - fabric_slices")
    parts.extend(
        [
            "<defs>",
            '<style type="text/css"><![CDATA[',
            ".slice-outline { fill: none; stroke: #b45309; stroke-width: 0.05; }",
            ".strip-outline { fill: none; stroke: #1d4ed8; stroke-width: 0.04; }",
            "]]></style>",
            "</defs>",
            '<g id="fabric_slices_outline">',
        ]
    )
    for r0, r1, cdeg, sp in iter_electrode_slices(p):
        parts.append(f'<path class="slice-outline" d="{annular_sector_path(r0, r1, cdeg, sp)}"/>')
    parts.append("</g>")
    parts.append('<g id="fabric_strips_outline">')
    for i in range(p.n_beat_steps):
        parts.append(f'<path class="strip-outline" d="{closed_polygon_path(strip_polygon_vertices(i, p))}"/>')
    parts.append("</g></svg>")
    return "\n".join(parts)


def build_svg_wood_engraving(p: Params) -> str:
    centers = ring_center_radii(p)
    parts, _ = _svg_header(p, "Layer 3 - wooden_board_engraving")
    parts.extend(
        [
            "<defs>",
            '<style type="text/css"><![CDATA[',
            ".ring { fill: none; stroke: #5c4033; stroke-width: 0.08; }",
            ".slice-outline { fill: none; stroke: #7c2d12; stroke-width: 0.05; }",
            ".strip-outline { fill: none; stroke: #1d4ed8; stroke-width: 0.04; }",
            ".velo { fill: none; stroke: #111827; stroke-width: 0.07; }",
            "]]></style>",
            "</defs>",
            '<g id="engraving_rings">',
        ]
    )
    for rc in centers:
        r_in = rc - p.w_rings / 2.0
        r_out = rc + p.w_rings / 2.0
        if r_in <= 0:
            raise ValueError("Ring inner outline radius must be > 0. Reduce w_rings or increase smallest radius.")
        parts.append(
            f'<circle class="ring" cx="0" cy="0" r="{r_in:.6g}" stroke-width="0.05"/>'
        )
        parts.append(
            f'<circle class="ring" cx="0" cy="0" r="{r_out:.6g}" stroke-width="0.05"/>'
        )
    parts.append("</g>")
    parts.append('<g id="engraving_slice_outlines">')
    for r0, r1, cdeg, sp in iter_electrode_slices(p):
        parts.append(f'<path class="slice-outline" d="{annular_sector_path(r0, r1, cdeg, sp)}"/>')
    parts.append("</g>")
    parts.append('<g id="engraving_strip_outlines">')
    for i in range(p.n_beat_steps):
        parts.append(f'<path class="strip-outline" d="{closed_polygon_path(strip_polygon_vertices(i, p))}"/>')
    parts.append("</g>")
    parts.append(
        f'<g id="velostate_circle"><circle class="velo" cx="0" cy="0" r="{p.radius_velostate_circle:.6g}"/></g>'
    )
    parts.append("</svg>")
    return "\n".join(parts)


def build_svg_wood_cutthrough(p: Params) -> str:
    centers = ring_center_radii(p)
    electrode_centers = electrode_via_centers(p, inward_offset_cm=p.via_offset_slice)
    parts, _ = _svg_header(p, "Layer 4 - wooden_board_cut_through")
    parts.extend(
        [
            "<defs>",
            '<style type="text/css"><![CDATA[',
            ".cut { fill: none; stroke: #111111; stroke-width: 0.06; }",
            ".via-ring { fill: none; stroke: #111111; stroke-width: 0.04; }",
            ".via-slice { fill: none; stroke: #111111; stroke-width: 0.04; }",
            "]]></style>",
            "</defs>",
            '<g id="cut_through_circle">',
            f'<circle class="cut" cx="0" cy="0" r="{p.r_circle_cut_through:.6g}"/>',
            "</g>",
            '<g id="via_holes_rings">',
        ]
    )
    for i, rc in enumerate(centers):
        hx, hy = via_center(i, rc, p)
        parts.append(f'<circle class="via-ring" cx="{hx:.6g}" cy="{hy:.6g}" r="{p.via_radius:.6g}"/>')
    parts.append("</g>")
    parts.append('<g id="via_holes_slices">')
    for hx, hy in electrode_centers:
        parts.append(f'<circle class="via-slice" cx="{hx:.6g}" cy="{hy:.6g}" r="{p.via_radius:.6g}"/>')
    parts.append("</g></svg>")
    return "\n".join(parts)


def _parse_value(raw: str, kind: str) -> int | float:
    raw = raw.strip()
    if raw == "":
        raise ValueError("empty")
    if kind == "int":
        return int(raw, 10)
    return float(raw)


def _read_params(vars_map: dict[str, tk.StringVar]) -> Params:
    kwargs: dict = {}
    for key, _label, kind in FIELD_SPEC:
        kwargs[key] = _parse_value(vars_map[key].get(), kind)
    return Params(**kwargs)


def _load_last_params() -> Params:
    """
    Load persisted params from disk; fall back to defaults on any error.
    """
    try:
        if not STATE_PATH.exists():
            return DEFAULT
        raw = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return DEFAULT
        kwargs = asdict(DEFAULT)
        for key, _label, kind in FIELD_SPEC:
            if key not in raw:
                continue
            val = raw[key]
            if kind == "int":
                kwargs[key] = int(val)
            else:
                kwargs[key] = float(val)
        return Params(**kwargs)
    except (OSError, ValueError, TypeError):
        return DEFAULT


def _save_params(p: Params) -> None:
    """
    Persist params to disk; ignore I/O failures to keep UX smooth.
    """
    try:
        STATE_PATH.write_text(json.dumps(asdict(p), indent=2), encoding="utf-8")
    except OSError:
        pass


def _validate(p: Params) -> str | None:
    if p.n_rings < 1:
        return "n_rings must be at least 1."
    if p.n_beat_steps < 1:
        return "n_beat_steps must be at least 1."
    if p.w_rings <= 0:
        return "w_rings must be positive."
    if p.w_strips <= 0:
        return "w_strips must be positive."
    if p.radius_largest_ring < p.radius_smallest_ring:
        return "radius_largest_ring must be >= radius_smallest_ring."
    if p.extra_length_electrode < 0:
        return "extra_length_electrode must be >= 0."
    if p.radius_velostate_circle <= 0:
        return "Radius_velostate_circle must be positive."
    if p.r_circle_cut_through <= 0:
        return "r_circle_cut_through must be positive."
    if p.via_radius <= 0:
        return "via_radius must be positive."
    if p.via_offset_slice <= 0:
        return "via_offset_slice must be positive."
    if p.radius_largest_ring + p.extra_length_electrode <= 0:
        return "radius_largest_ring + extra_length_electrode must be > 0."
    if p.radius_largest_ring + p.extra_length_electrode <= p.via_offset_slice:
        return "Need radius_largest_ring + extra_length_electrode > via_offset_slice."
    if p.radius_smallest_ring - p.w_rings / 2.0 <= 0:
        return "Need radius_smallest_ring > w_rings / 2.0 to form ring inner outlines."
    return None


def _cm_to_canvas(x: float, y: float, cx: float, cy: float, s: float) -> tuple[float, float]:
    return (cx + x * s, cy + y * s)


def _draw_fabric_rings(canvas: tk.Canvas, p: Params, cx: float, cy: float, s: float) -> None:
    centers = ring_center_radii(p)
    width_px = max(1, int(p.w_rings * s))
    for rc in centers:
        r_px = rc * s
        canvas.create_oval(cx - r_px, cy - r_px, cx + r_px, cy + r_px, outline="#1f4e79", width=width_px)


def _draw_fabric_slices(canvas: tk.Canvas, p: Params, cx: float, cy: float, s: float) -> None:
    for r0, r1, cdeg, span in iter_electrode_slices(p):
        outer, _ = annular_sector_polygons_cm(r0, r1, cdeg, span)
        poly: list[float] = []
        px0, py0 = _cm_to_canvas(0.0, 0.0, cx, cy, s)
        poly.extend([px0, py0])
        for x, y in outer:
            px, py = _cm_to_canvas(x, y, cx, cy, s)
            poly.extend([px, py])
        canvas.create_polygon(poly, fill="#fdba74", outline="#c2410c", width=1)
    for i in range(p.n_beat_steps):
        verts = strip_polygon_vertices(i, p)
        poly: list[float] = []
        for x, y in verts:
            px, py = _cm_to_canvas(x, y, cx, cy, s)
            poly.extend([px, py])
        canvas.create_polygon(poly, fill="#ffffff", outline="#ffffff")


def _draw_wood_engraving(canvas: tk.Canvas, p: Params, cx: float, cy: float, s: float) -> None:
    _draw_fabric_rings(canvas, p, cx, cy, s)
    for r0, r1, cdeg, span in iter_electrode_slices(p):
        outer, _ = annular_sector_polygons_cm(r0, r1, cdeg, span)
        poly: list[float] = []
        px0, py0 = _cm_to_canvas(0.0, 0.0, cx, cy, s)
        poly.extend([px0, py0])
        for x, y in outer:
            px, py = _cm_to_canvas(x, y, cx, cy, s)
            poly.extend([px, py])
        canvas.create_polygon(poly, fill="", outline="#7c2d12", width=1)
    for i in range(p.n_beat_steps):
        verts = strip_polygon_vertices(i, p)
        poly: list[float] = []
        for x, y in verts:
            px, py = _cm_to_canvas(x, y, cx, cy, s)
            poly.extend([px, py])
        canvas.create_polygon(poly, fill="", outline="#1d4ed8", width=1)
    r = p.radius_velostate_circle * s
    canvas.create_oval(cx - r, cy - r, cx + r, cy + r, outline="#111827", width=2, dash=(6, 4))


def _draw_wood_cutthrough(canvas: tk.Canvas, p: Params, cx: float, cy: float, s: float) -> None:
    r_cut = p.r_circle_cut_through * s
    canvas.create_oval(cx - r_cut, cy - r_cut, cx + r_cut, cy + r_cut, outline="#111111", width=2)
    centers = ring_center_radii(p)
    electrode_centers = electrode_via_centers(p, inward_offset_cm=p.via_offset_slice)
    rv = max(1.0, p.via_radius * s)
    for i, rc in enumerate(centers):
        hx, hy = via_center(i, rc, p)
        px, py = _cm_to_canvas(hx, hy, cx, cy, s)
        canvas.create_oval(px - rv, py - rv, px + rv, py + rv, fill="#111111", outline="#111111")
    for hx, hy in electrode_centers:
        px, py = _cm_to_canvas(hx, hy, cx, cy, s)
        canvas.create_oval(px - rv, py - rv, px + rv, py + rv, fill="#111111", outline="#111111")


def _draw_one(canvas: tk.Canvas, p: Params, layer: str) -> None:
    canvas.delete("all")
    extent = layout_extent_cm(p)
    scale = (CANVAS_SIZE / 2) * 0.92 / extent
    cx = cy = CANVAS_SIZE / 2
    canvas.create_rectangle(0, 0, CANVAS_SIZE, CANVAS_SIZE, fill="#ffffff", outline="#d4d4d4")
    canvas.create_line(0, cy, CANVAS_SIZE, cy, fill="#efefef")
    canvas.create_line(cx, 0, cx, CANVAS_SIZE, fill="#efefef")

    if layer == "fabric_rings":
        _draw_fabric_rings(canvas, p, cx, cy, scale)
    elif layer == "fabric_slices":
        _draw_fabric_slices(canvas, p, cx, cy, scale)
    elif layer == "wooden_board_engraving":
        _draw_wood_engraving(canvas, p, cx, cy, scale)
    elif layer == "wooden_board_cut_through":
        _draw_wood_cutthrough(canvas, p, cx, cy, scale)
    elif layer == "all_layers":
        _draw_fabric_slices(canvas, p, cx, cy, scale)
        _draw_fabric_rings(canvas, p, cx, cy, scale)
        _draw_wood_engraving(canvas, p, cx, cy, scale)
        _draw_wood_cutthrough(canvas, p, cx, cy, scale)


class MultiLayerApp(ttk.Frame):
    def __init__(self, master: tk.Tk) -> None:
        super().__init__(master, padding=(10, 10, 10, 6))
        self.master = master
        self.pack(fill=tk.BOTH, expand=True)
        self._after_id: str | None = None
        self._vars: dict[str, tk.StringVar] = {}
        self._status_var = tk.StringVar(value="")
        self._initial_params = _load_last_params()

        self.grid_columnconfigure(0, minsize=SIDEBAR_MIN)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        sidebar = ttk.Frame(self)
        sidebar.grid(row=0, column=0, sticky=tk.NSEW, padx=(0, 10))
        ttk.Label(sidebar, text="Parameters", font=("TkDefaultFont", 11, "bold")).pack(anchor=tk.W, pady=(0, 6))
        form = ttk.Frame(sidebar)
        form.pack(fill=tk.X)

        for row, (key, label, _kind) in enumerate(FIELD_SPEC):
            v = tk.StringVar(value=str(getattr(self._initial_params, key)))
            self._vars[key] = v
            v.trace_add("write", self._schedule_redraw)
            ttk.Label(form, text=label).grid(row=row, column=0, sticky=tk.W, padx=(0, 6), pady=3)
            ttk.Entry(form, textvariable=v, width=14).grid(row=row, column=1, sticky=tk.EW, pady=3)
        form.columnconfigure(1, weight=1)

        ttk.Label(
            sidebar,
            textvariable=self._status_var,
            wraplength=SIDEBAR_MIN - 12,
            foreground="#525252",
            font=("TkDefaultFont", 9),
        ).pack(anchor=tk.W, pady=(12, 0))

        right = ttk.Frame(self)
        right.grid(row=0, column=1, sticky=tk.NSEW)
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(0, weight=1)

        notebook = ttk.Notebook(right)
        notebook.grid(row=0, column=0, sticky=tk.NSEW)
        self._canvases: dict[str, tk.Canvas] = {}
        for name in LAYER_NAMES:
            f = ttk.Frame(notebook, padding=6)
            f.grid_columnconfigure(0, weight=1)
            f.grid_rowconfigure(0, weight=1)
            c = tk.Canvas(
                f,
                width=CANVAS_SIZE,
                height=CANVAS_SIZE,
                bg="#ffffff",
                highlightthickness=1,
                highlightbackground="#d4d4d4",
            )
            c.grid(row=0, column=0, sticky=tk.NSEW)
            self._canvases[name] = c
            notebook.add(f, text=name)

        bottom = ttk.Frame(self, padding=(0, 10, 0, 0))
        bottom.grid(row=1, column=0, columnspan=2, sticky=tk.EW)
        ttk.Button(bottom, text="Save SVG", command=self._on_save).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(bottom, text="Cancel", command=self._on_cancel).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(bottom, text="Quit", command=self._quit).pack(side=tk.LEFT)

        self._do_redraw()

    def _quit(self) -> None:
        self.master.destroy()

    def _schedule_redraw(self, *_args: object) -> None:
        p = self._try_params()
        if p is not None:
            _save_params(p)
        if self._after_id is not None:
            self.after_cancel(self._after_id)
        self._after_id = self.after(REDRAW_MS, self._do_redraw)

    def _try_params(self) -> Params | None:
        try:
            p = _read_params(self._vars)
        except ValueError:
            return None
        if _validate(p) is not None:
            return None
        try:
            layout_extent_cm(p)
            ring_center_radii(p)
            iter_electrode_slices(p)
            strip_polygon_vertices(0, p)
        except ValueError:
            return None
        return p

    def _do_redraw(self) -> None:
        self._after_id = None
        p = self._try_params()
        if p is None:
            for c in self._canvases.values():
                c.delete("all")
                c.create_text(
                    CANVAS_SIZE // 2,
                    CANVAS_SIZE // 2,
                    text="Enter valid parameters.\nPreviews update automatically.",
                    fill="#737373",
                    justify=tk.CENTER,
                )
            self._status_var.set("Invalid or incomplete input.")
            return

        for name in LAYER_NAMES:
            _draw_one(self._canvases[name], p, name)
        self._status_var.set(
            "Live preview ready. Save writes 4 files: fabric_rings, fabric_slices, "
            "wooden_board_engraving, wooden_board_cut_through."
        )

    def _on_cancel(self) -> None:
        for key, _label, _kind in FIELD_SPEC:
            self._vars[key].set(str(getattr(DEFAULT, key)))
        self._do_redraw()

    def _on_save(self) -> None:
        p = self._try_params()
        if p is None:
            messagebox.showerror("Cannot save", "Fix inputs until previews are valid.")
            return

        out_dir = filedialog.askdirectory(title="Choose folder for layer SVG files")
        if not out_dir:
            return
        folder = Path(out_dir)
        files = {
            "fabric_rings.svg": build_svg_fabric_rings(p),
            "fabric_slices.svg": build_svg_fabric_slices(p),
            "wooden_board_engraving.svg": build_svg_wood_engraving(p),
            "wooden_board_cut_through.svg": build_svg_wood_cutthrough(p),
        }
        try:
            for name, svg in files.items():
                (folder / name).write_text(svg, encoding="utf-8")
        except OSError as e:
            messagebox.showerror("Save failed", str(e))
            return
        messagebox.showinfo("Saved", f"Wrote {len(files)} SVG files to:\n{folder.resolve()}")


def main() -> None:
    root = tk.Tk()
    root.title("Multi-layer Pattern Generator")
    root.minsize(1120, 700)
    MultiLayerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()

