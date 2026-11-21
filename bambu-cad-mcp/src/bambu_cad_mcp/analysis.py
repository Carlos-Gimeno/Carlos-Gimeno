"""Printability analysis for the Bambu Lab P2S, based on the exported mesh."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import trimesh

from .printer import BUILD_MARGIN_MM, P2S, get_material

FILAMENT_DIAMETER = 1.75


@dataclass
class Report:
    data: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    tips: list[str] = field(default_factory=list)

    def as_text(self) -> str:
        d = self.data
        lines = [
            f"📐 Tamaño: {d['size_mm'][0]} x {d['size_mm'][1]} x {d['size_mm'][2]} mm",
            f"🧊 Volumen: {d['volume_cm3']} cm³ · Superficie: {d['area_cm2']} cm²",
            f"🖨️ Cabe en la P2S (256³ mm): {'Sí' if d['fits'] else 'NO'}"
            + (f" (girando {d['fits_rotated_deg']}° en Z)" if d.get("fits_rotated_deg") else ""),
            f"🧱 Malla cerrada (watertight): {'Sí' if d['watertight'] else 'NO'} · cuerpos: {d['bodies']}",
            f"⬇️ Contacto con la cama: {d['bed_contact_mm2']} mm²",
            f"⚠️ Voladizos > {d['overhang_limit_deg']}°: {d['overhang_area_mm2']} mm² ({d['overhang_pct']} % de la superficie)"
            f" → soportes {'RECOMENDADOS' if d['needs_support'] else 'no necesarios'}",
        ]
        if d.get("min_wall_mm") is not None:
            lines.append(f"📏 Espesor de pared mínimo estimado: {d['min_wall_mm']} mm")
        est = d.get("estimate")
        if est:
            lines.append(
                f"🧵 Estimación ({est['material']}, {est['infill_pct']} % relleno, {est['walls']} paredes): "
                f"~{est['grams']} g · ~{est['meters']} m de filamento · ~{est['time_h']} h"
            )
        if self.warnings:
            lines.append("\nAvisos:")
            lines += [f"  - {w}" for w in self.warnings]
        if self.tips:
            lines.append("\nConsejos:")
            lines += [f"  - {t}" for t in self.tips]
        return "\n".join(lines)


def load_mesh(path) -> trimesh.Trimesh:
    mesh = trimesh.load(str(path), force="mesh")
    if not isinstance(mesh, trimesh.Trimesh) or mesh.is_empty:
        raise ValueError("La malla está vacía o no se pudo leer")
    return mesh


def overhang_area(mesh: trimesh.Trimesh, limit_deg: float = 45.0, bed_tol: float = 0.3) -> float:
    """Area of downward-facing triangles steeper than ``limit_deg`` from vertical,
    excluding triangles that sit on the build plate."""
    normals = mesh.face_normals
    # A face needs support when its normal points down more than cos(90-limit).
    threshold = -math.sin(math.radians(90.0 - limit_deg))
    down = normals[:, 2] < threshold
    zmin = mesh.bounds[0][2]
    face_z = mesh.triangles_center[:, 2]
    on_bed = face_z < zmin + bed_tol
    mask = down & ~on_bed
    return float(mesh.area_faces[mask].sum())


def bed_contact_area(mesh: trimesh.Trimesh, bed_tol: float = 0.3) -> float:
    normals = mesh.face_normals
    zmin = mesh.bounds[0][2]
    mask = (normals[:, 2] < -0.99) & (mesh.triangles_center[:, 2] < zmin + bed_tol)
    return float(mesh.area_faces[mask].sum())


def min_wall_thickness(mesh: trimesh.Trimesh, samples: int = 1500) -> float | None:
    """Estimate the thinnest wall by casting rays inward from sampled surface points."""
    try:
        points, face_idx = trimesh.sample.sample_surface_even(mesh, samples, seed=7)
        if len(points) == 0:
            return None
        normals = mesh.face_normals[face_idx]
        origins = points - normals * 1e-3
        locs, ray_idx, _ = mesh.ray.intersects_location(origins, -normals, multiple_hits=False)
        if len(locs) == 0:
            return None
        dist = np.linalg.norm(locs - origins[ray_idx], axis=1)
        dist = dist[dist > 0.02]
        if len(dist) == 0:
            return None
        # 2nd percentile is robust against stray rays grazing edges.
        return round(float(np.percentile(dist, 2)), 2)
    except Exception:
        return None


def fits_in_printer(size, margin: float = BUILD_MARGIN_MM):
    """Return (fits, rotation_deg) — tries rotating around Z if the XY footprint is too large."""
    bx, by, bz = P2S.build_x - margin, P2S.build_y - margin, P2S.build_z - margin
    sx, sy, sz = size
    if sz > bz:
        return False, None
    if sx <= bx and sy <= by:
        return True, None
    # Diagonal placement for long thin parts.
    for deg in range(5, 90, 5):
        a = math.radians(deg)
        w = sx * math.cos(a) + sy * math.sin(a)
        h = sx * math.sin(a) + sy * math.cos(a)
        if w <= bx and h <= by:
            return True, deg
    return False, None


def estimate_print(mesh: trimesh.Trimesh, material: str = "pla", infill_pct: float = 15,
                   walls: int = 2, layer_h: float = 0.2) -> dict:
    """Very rough filament/time estimate (the slicer is the source of truth)."""
    mat = get_material(material)
    volume = float(abs(mesh.volume)) if mesh.is_watertight else float(mesh.convex_hull.volume)
    area = float(mesh.area)
    line_w = 0.42
    shell_t = max(walls * line_w, 4 * layer_h)  # walls vs top/bottom ~4-5 layers
    shell_vol = min(volume, area * shell_t * 0.5 + area * 0.5 * 5 * layer_h)
    core_vol = max(volume - shell_vol, 0.0)
    plastic_mm3 = shell_vol + core_vol * infill_pct / 100.0
    grams = plastic_mm3 / 1000.0 * mat.density
    meters = plastic_mm3 / (math.pi * (FILAMENT_DIAMETER / 2) ** 2) / 1000.0
    # P2S averages ~10-14 mm³/s on standard 0.2 mm profiles once accel/travel is accounted for.
    flow = 6.0 if mat.key == "tpu" else 11.0
    seconds = plastic_mm3 / flow + mesh.extents[2] / layer_h * 4.0
    return {
        "material": mat.name,
        "infill_pct": infill_pct,
        "walls": walls,
        "grams": round(grams, 1),
        "meters": round(meters, 2),
        "time_h": round(seconds / 3600.0, 2),
    }


def analyze(mesh: trimesh.Trimesh, material: str = "pla", infill_pct: float = 15,
            walls: int = 2, check_walls: bool = True) -> Report:
    rep = Report()
    mat = get_material(material)
    size = [round(float(v), 2) for v in mesh.extents]
    fits, rot = fits_in_printer(size)
    limit = mat.max_overhang_deg
    total_area = float(mesh.area)
    oh = overhang_area(mesh, limit)
    contact = bed_contact_area(mesh)
    bodies = len(mesh.split(only_watertight=False)) if len(mesh.faces) < 400_000 else 1
    min_wall = min_wall_thickness(mesh) if check_walls else None

    rep.data = {
        "size_mm": size,
        "volume_cm3": round(float(abs(mesh.volume)) / 1000.0, 2) if mesh.is_watertight else None,
        "area_cm2": round(total_area / 100.0, 2),
        "fits": fits,
        "fits_rotated_deg": rot,
        "watertight": bool(mesh.is_watertight),
        "bodies": bodies,
        "bed_contact_mm2": round(contact, 1),
        "overhang_limit_deg": limit,
        "overhang_area_mm2": round(oh, 1),
        "overhang_pct": round(100.0 * oh / total_area, 1) if total_area else 0.0,
        "needs_support": oh > 25.0 and oh / max(total_area, 1e-9) > 0.01,
        "min_wall_mm": min_wall,
        "triangles": int(len(mesh.faces)),
        "estimate": estimate_print(mesh, material, infill_pct, walls),
    }

    # --- warnings -----------------------------------------------------------
    if not fits:
        rep.warnings.append(
            f"No cabe en la P2S ({P2S.build_x:.0f}x{P2S.build_y:.0f}x{P2S.build_z:.0f} mm). "
            "Divide la pieza en partes con uniones (espigas, cola de milano) o escala el modelo."
        )
    elif rot:
        rep.warnings.append(f"Solo cabe colocada en diagonal ({rot}° en Z). Gírala en el slicer.")
    if not mesh.is_watertight:
        rep.warnings.append("La malla no es cerrada: el slicer puede fallar. Repárala en Bambu Studio (clic derecho → Reparar).")
    if mesh.bounds[0][2] > 0.05 or mesh.bounds[0][2] < -0.05:
        rep.tips.append("La pieza no está apoyada en Z=0; el slicer la bajará a la cama automáticamente.")
    if contact < 1.0:
        rep.warnings.append("Casi no hay superficie plana apoyada en la cama: usa `orient_for_print` o añade una base plana.")
    else:
        min_xy = max(min(size[0], size[1]), 1e-6)
        if size[2] / min_xy > 4 or contact < 0.05 * size[0] * size[1]:
            rep.warnings.append("Pieza alta con poca base: riesgo de que se suelte o vibre. Activa brim (5 mm) o ensancha la base.")
    if rep.data["needs_support"]:
        rep.tips.append(
            "Hay voladizos: prueba `orient_for_print`, rediseña con chaflanes a 45° o activa soportes de árbol (tree) en Bambu Studio."
        )
    if min_wall is not None and min_wall < 0.8:
        rep.warnings.append(f"Hay zonas de ~{min_wall} mm: por debajo de 0.8 mm (2 líneas) pueden no imprimirse.")
    if mat.needs_hardened_nozzle:
        rep.tips.append(f"{mat.name} es abrasivo: la P2S trae boquilla de acero endurecido de serie, OK.")
    if mat.needs_drying:
        rep.tips.append(f"Seca el {mat.name} antes de imprimir (AMS HT o secador 8 h a 70-80 °C).")
    if mat.key in ("abs", "asa", "pc", "pa-cf") and max(size[0], size[1]) > 150:
        rep.tips.append("Pieza grande en material técnico: pre-calienta la cámara cerrada y usa brim para evitar warping.")
    if rep.data["triangles"] > 1_500_000:
        rep.tips.append("Malla muy densa; exporta con tolerancia mayor (0.02-0.05 mm) para un 3MF más ligero.")
    return rep


# ---------------------------------------------------------------------------
# Orientation optimisation
# ---------------------------------------------------------------------------


def best_orientation(mesh: trimesh.Trimesh, limit_deg: float = 45.0, max_candidates: int = 24):
    """Search stable resting poses and pick the one that minimises supports
    while maximising bed contact and keeping the part low.

    Returns (transform 4x4, scores list).
    """
    candidates = []
    try:
        transforms, probs = trimesh.poses.compute_stable_poses(mesh, n_samples=1, threshold=0.0)
        candidates = list(transforms[:max_candidates])
    except Exception:
        candidates = []
    # Always include the six axis-aligned orientations.
    for axis, ang in [((1, 0, 0), 0), ((1, 0, 0), 90), ((1, 0, 0), 180), ((1, 0, 0), -90),
                      ((0, 1, 0), 90), ((0, 1, 0), -90)]:
        candidates.append(trimesh.transformations.rotation_matrix(math.radians(ang), axis))

    scored = []
    diag = float(np.linalg.norm(mesh.extents))
    for T in candidates:
        T = np.asarray(T, dtype=float).copy()
        T[:3, 3] = 0.0  # keep only the rotation; we re-centre below
        m = mesh.copy()
        m.apply_transform(T)
        # Drop to the bed and centre on the XY origin.
        offset = [-m.bounds[:, 0].mean(), -m.bounds[:, 1].mean(), -m.bounds[0][2]]
        m.apply_translation(offset)
        final = trimesh.transformations.translation_matrix(offset) @ T
        oh = overhang_area(m, limit_deg)
        contact = bed_contact_area(m)
        height = m.extents[2]
        fits, _ = fits_in_printer(m.extents)
        score = oh - 0.5 * contact + 0.02 * height * diag + (0 if fits else 1e9)
        scored.append({
            "transform": final,
            "overhang_mm2": round(oh, 1),
            "contact_mm2": round(contact, 1),
            "height_mm": round(float(height), 1),
            "fits": fits,
            "score": score,
        })
    scored.sort(key=lambda s: s["score"])
    # Deduplicate similar results
    unique = []
    for s in scored:
        if not any(abs(s["overhang_mm2"] - u["overhang_mm2"]) < 0.5 and abs(s["height_mm"] - u["height_mm"]) < 0.1
                   for u in unique):
            unique.append(s)
    return unique[0]["transform"], unique[:5]
