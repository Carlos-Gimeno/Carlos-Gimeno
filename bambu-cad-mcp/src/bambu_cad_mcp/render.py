"""Headless preview renderer: a small numpy z-buffer rasterizer.

No GPU, OpenGL or display needed, correct occlusion (unlike matplotlib's
painter's algorithm) and CAD-style dark outlines on sharp edges.
"""

from __future__ import annotations

import io
import math

import numpy as np
import trimesh
from PIL import Image as PILImage
from PIL import ImageDraw, ImageFont

VIEWS = {
    "iso": (30, -55),
    "iso_back": (30, 125),
    "front": (0, -90),
    "back": (0, 90),
    "right": (0, 0),
    "left": (0, 180),
    "top": (90, -90),
    "bottom": (-90, -90),
}

BASE_COLOR = np.array([46, 160, 92], dtype=float)  # Bambu-ish green
OVERHANG_COLOR = np.array([225, 70, 55], dtype=float)
EDGE_COLOR = np.array([25, 40, 30], dtype=np.uint8)
BG_TOP = np.array([250, 251, 252], dtype=float)
BG_BOTTOM = np.array([222, 227, 232], dtype=float)


def _camera(elev: float, azim: float):
    e, a = math.radians(elev), math.radians(azim)
    v = np.array([math.cos(e) * math.cos(a), math.cos(e) * math.sin(a), math.sin(e)])  # towards camera
    r = np.array([-math.sin(a), math.cos(a), 0.0])
    u = np.cross(v, r)
    return r, u, v


def _face_colors(mesh: trimesh.Trimesh, highlight: bool, limit_deg: float) -> np.ndarray:
    colors = np.tile(BASE_COLOR, (len(mesh.faces), 1))
    if highlight:
        n = mesh.face_normals
        thr = -math.sin(math.radians(90 - limit_deg))
        mask = (n[:, 2] < thr) & (mesh.triangles_center[:, 2] > mesh.bounds[0][2] + 0.3)
        colors[mask] = OVERHANG_COLOR
    return colors


def _render_view(mesh, colors, sharp_edges, elev, azim, size, center, radius):
    W = H = size
    r, u, v = _camera(elev, azim)
    verts = mesh.vertices - center
    scale = (size * 0.44) / radius
    px = W / 2 + (verts @ r) * scale
    py = H / 2 - (verts @ u) * scale
    pz = verts @ v  # bigger = closer

    # Shading (light from upper-left of the camera).
    light = 0.45 * r + 0.65 * u + 1.0 * v
    light /= np.linalg.norm(light)
    n = mesh.face_normals
    facing = n @ v
    diffuse = np.clip(np.abs(n @ light), 0, 1)
    intensity = 0.38 + 0.62 * diffuse
    intensity = np.where(facing < 0, intensity * 0.6, intensity)  # backfaces (open meshes)
    shaded = np.clip(colors * intensity[:, None], 0, 255)

    # Background gradient.
    grad = np.linspace(0, 1, H)[:, None, None]
    img = (BG_TOP * (1 - grad) + BG_BOTTOM * grad).repeat(W, axis=1)
    zbuf = np.full((H, W), -np.inf)

    F = mesh.faces
    tx, ty, tz = px[F], py[F], pz[F]
    xmin = np.floor(tx.min(1)).astype(int)
    xmax = np.ceil(tx.max(1)).astype(int)
    ymin = np.floor(ty.min(1)).astype(int)
    ymax = np.ceil(ty.max(1)).astype(int)
    span = (xmax - xmin) * (ymax - ymin)

    # --- big triangles: proper barycentric rasterisation -----------------------
    big = np.nonzero(span > 4)[0]
    for i in big:
        x0, x1 = max(xmin[i], 0), min(xmax[i], W - 1)
        y0, y1 = max(ymin[i], 0), min(ymax[i], H - 1)
        if x0 > x1 or y0 > y1:
            continue
        xs, ys = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
        (ax, bx, cx), (ay, by, cy) = tx[i], ty[i]
        den = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
        if abs(den) < 1e-12:
            continue
        w0 = ((by - cy) * (xs - cx) + (cx - bx) * (ys - cy)) / den
        w1 = ((cy - ay) * (xs - cx) + (ax - cx) * (ys - cy)) / den
        w2 = 1 - w0 - w1
        inside = (w0 >= -1e-6) & (w1 >= -1e-6) & (w2 >= -1e-6)
        if not inside.any():
            continue
        z = w0 * tz[i, 0] + w1 * tz[i, 1] + w2 * tz[i, 2]
        sub = zbuf[y0:y1 + 1, x0:x1 + 1]
        win = inside & (z > sub)
        sub[win] = z[win]
        img[y0:y1 + 1, x0:x1 + 1][win] = shaded[i]

    # --- tiny triangles: depth-sorted splats (vectorised) ------------------------
    small = np.nonzero(span <= 4)[0]
    if len(small):
        sx = np.concatenate([tx[small].mean(1), tx[small, 0], tx[small, 1], tx[small, 2]])
        sy = np.concatenate([ty[small].mean(1), ty[small, 0], ty[small, 1], ty[small, 2]])
        sz = np.concatenate([tz[small].mean(1), tz[small, 0], tz[small, 1], tz[small, 2]])
        sc = np.concatenate([shaded[small]] * 4)
        # 2x2 splats so neighbouring micro-triangles leave no gaps.
        sx = np.concatenate([sx, sx + 0.7, sx, sx + 0.7])
        sy = np.concatenate([sy, sy, sy + 0.7, sy + 0.7])
        sz = np.concatenate([sz] * 4)
        sc = np.concatenate([sc] * 4)
        ix, iy = sx.astype(int), sy.astype(int)
        ok = (ix >= 0) & (ix < W) & (iy >= 0) & (iy < H)
        ix, iy, sz, sc = ix[ok], iy[ok], sz[ok], sc[ok]
        ok = sz >= zbuf[iy, ix] - 1e-6
        ix, iy, sz, sc = ix[ok], iy[ok], sz[ok], sc[ok]
        order = np.argsort(sz)  # nearest written last wins
        ix, iy, sz, sc = ix[order], iy[order], sz[order], sc[order]
        img[iy, ix] = sc
        zbuf[iy, ix] = np.maximum(zbuf[iy, ix], sz)

    out = img.astype(np.uint8)

    # --- sharp edges with depth test -------------------------------------------
    if sharp_edges is not None and len(sharp_edges):
        e0, e1 = sharp_edges[:, 0], sharp_edges[:, 1]
        length = np.hypot(px[e1] - px[e0], py[e1] - py[e0])
        steps = np.clip(np.ceil(length * 1.5).astype(int), 2, 4000)
        idx = np.repeat(np.arange(len(e0)), steps)
        t = np.concatenate([np.linspace(0, 1, s) for s in steps])
        lx = px[e0][idx] * (1 - t) + px[e1][idx] * t
        ly = py[e0][idx] * (1 - t) + py[e1][idx] * t
        lz = pz[e0][idx] * (1 - t) + pz[e1][idx] * t
        ix, iy = lx.astype(int), ly.astype(int)
        ok = (ix >= 0) & (ix < W) & (iy >= 0) & (iy < H)
        ix, iy, lz = ix[ok], iy[ok], lz[ok]
        tol = radius * 0.012
        # Compare against the neighbourhood max depth so edges on silhouettes survive.
        visible = lz >= zbuf[iy, ix] - tol
        out[iy[visible], ix[visible]] = EDGE_COLOR
    return out


def _sharp_edges(mesh: trimesh.Trimesh, angle_deg: float = 30.0) -> np.ndarray:
    try:
        angles = mesh.face_adjacency_angles
        sharp = mesh.face_adjacency_edges[angles > math.radians(angle_deg)]
        # Boundary edges of open meshes too.
        return sharp
    except Exception:
        return np.empty((0, 2), dtype=int)


def _font(size: int):
    for name in ("DejaVuSans.ttf", "Arial.ttf", "arial.ttf", "Helvetica.ttc"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return ImageFont.load_default()


def render_png(mesh: trimesh.Trimesh, views=("iso", "front", "right", "top"),
               highlight_overhangs: bool = True, overhang_deg: float = 45.0,
               show_bed: bool = True, title: str | None = None, size_px: int = 520) -> bytes:
    views = [v for v in views if v in VIEWS] or ["iso"]
    colors = _face_colors(mesh, highlight_overhangs, overhang_deg)
    sharp = _sharp_edges(mesh)
    lo, hi = mesh.bounds
    center = (lo + hi) / 2
    radius = float(np.linalg.norm(hi - lo)) / 2 or 1.0

    cols = 2 if len(views) > 1 else 1
    rows = math.ceil(len(views) / cols)
    header = 34
    label_h = 22
    canvas = PILImage.new("RGB", (cols * size_px, header + rows * (size_px + label_h)), (255, 255, 255))
    draw = ImageDraw.Draw(canvas)
    f_title, f_label = _font(16), _font(13)

    for k, view in enumerate(views):
        elev, azim = VIEWS[view]
        arr = _render_view(mesh, colors, sharp, elev, azim, size_px, center, radius)
        cx, cy = (k % cols) * size_px, header + (k // cols) * (size_px + label_h)
        canvas.paste(PILImage.fromarray(arr), (cx, cy + label_h))
        draw.text((cx + 8, cy + 3), view, fill=(60, 60, 60), font=f_label)
        # small axis triad
        r, u, _ = _camera(elev, azim)
        ox, oy = cx + 30, cy + label_h + size_px - 30
        for vec, col, lab in (((1, 0, 0), (200, 50, 50), "X"), ((0, 1, 0), (50, 150, 50), "Y"),
                              ((0, 0, 1), (50, 80, 200), "Z")):
            dx, dy = np.dot(vec, r) * 20, -np.dot(vec, u) * 20
            if abs(dx) + abs(dy) < 2:
                continue
            draw.line((ox, oy, ox + dx, oy + dy), fill=col, width=2)
            draw.text((ox + dx * 1.25 - 3, oy + dy * 1.25 - 6), lab, fill=col, font=f_label)

    sx, sy, sz = mesh.extents
    text = (title + "  ·  " if title else "") + f"{sx:.1f} x {sy:.1f} x {sz:.1f} mm"
    if highlight_overhangs:
        text += f"   (rojo = voladizo > {overhang_deg:.0f}° que necesita soporte)"
    draw.text((10, 8), text, fill=(30, 30, 30), font=f_title)
    for c in range(1, cols):
        draw.line((c * size_px, header, c * size_px, canvas.height), fill=(210, 210, 210))

    buf = io.BytesIO()
    canvas.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
