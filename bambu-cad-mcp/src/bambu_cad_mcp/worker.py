"""Isolated CAD worker.

Runs in a separate Python process so that a crash in the OpenCascade kernel,
an infinite loop or a typo in user code never takes the MCP server down.

Usage:
    python -m bambu_cad_mcp.worker <job.json>

job.json:
    {"mode": "build", "code_file": "...", "out_dir": "...", "tolerance": 0.01}
    {"mode": "transform", "step_file": "...", "matrix": [[...]*4]*3, "out_dir": "..."}
    {"mode": "import", "source": "...", "out_dir": "..."}

The worker writes <out_dir>/model.step, model.stl, model.3mf and prints a
single JSON line prefixed with ``@@RESULT@@`` on stdout.
"""

from __future__ import annotations

import json
import math
import shutil
import sys
import traceback
from pathlib import Path

RESULT_TAG = "@@RESULT@@"


def _emit(payload: dict) -> None:
    sys.stdout.write("\n" + RESULT_TAG + json.dumps(payload) + "\n")
    sys.stdout.flush()


def _collect_shapes(result):
    """Normalise whatever the user assigned to ``result`` into [(name, Shape)]."""
    import build123d as bd

    def as_shape(obj):
        # BuildPart / BuildSketch / BuildLine contexts
        for attr in ("part", "sketch", "line"):
            if hasattr(obj, attr) and not isinstance(obj, bd.Shape):
                val = getattr(obj, attr)
                if val is not None:
                    return val
        if isinstance(obj, bd.Shape):
            return obj
        raise TypeError(f"No sé convertir {type(obj).__name__} en un sólido de build123d")

    if isinstance(result, dict):
        return [(str(k), as_shape(v)) for k, v in result.items()]
    if isinstance(result, (list, tuple)):
        return [(f"part_{i + 1}", as_shape(v)) for i, v in enumerate(result)]
    return [("part_1", as_shape(result))]


def _export(named_shapes, out_dir: Path, tolerance: float) -> dict:
    import build123d as bd

    parts = []
    for name, shp in named_shapes:
        solids = shp.solids()
        if not solids:
            raise ValueError(
                f"'{name}' no contiene ningún sólido 3D (¿has devuelto un boceto 2D? usa extrude())."
            )
        parts.append((name, shp))

    compound = bd.Compound([s for _, s in parts]) if len(parts) > 1 else parts[0][1]
    out_dir.mkdir(parents=True, exist_ok=True)
    bd.export_step(compound, str(out_dir / "model.step"))
    bd.export_stl(compound, str(out_dir / "model.stl"), tolerance=tolerance, angular_tolerance=0.1)

    warnings = []
    try:
        mesher = bd.Mesher()
        for name, shp in parts:
            mesher.add_shape(shp, linear_deflection=tolerance, angular_deflection=0.1, part_number=name)
        mesher.write(str(out_dir / "model.3mf"))
    except Exception as exc:
        # lib3mf is strict about manifoldness; fall back to trimesh so the user still gets a 3MF.
        import tempfile

        import trimesh

        warnings.append(f"lib3mf rechazó la malla ({exc}); 3MF generado con trimesh. "
                        "Revisa caras tangentes o solapes de espesor cero.")
        scene = trimesh.Scene()
        with tempfile.TemporaryDirectory() as tmp:
            for name, shp in parts:
                p = Path(tmp) / f"{name}.stl"
                bd.export_stl(shp, str(p), tolerance=tolerance, angular_tolerance=0.1)
                scene.add_geometry(trimesh.load(str(p), force="mesh"), node_name=name, geom_name=name)
        scene.export(str(out_dir / "model.3mf"))

    bb = compound.bounding_box()
    info_parts = []
    for name, shp in parts:
        pbb = shp.bounding_box()
        info_parts.append(
            {
                "name": name,
                "solids": len(shp.solids()),
                "volume_mm3": round(shp.volume, 3),
                "size_mm": [round(pbb.size.X, 3), round(pbb.size.Y, 3), round(pbb.size.Z, 3)],
                "valid": bool(shp.is_valid),
            }
        )
    return {
        "ok": True,
        "volume_mm3": round(sum(p["volume_mm3"] for p in info_parts), 3),
        "bbox_min": [round(bb.min.X, 3), round(bb.min.Y, 3), round(bb.min.Z, 3)],
        "bbox_max": [round(bb.max.X, 3), round(bb.max.Y, 3), round(bb.max.Z, 3)],
        "valid": all(p["valid"] for p in info_parts),
        "parts": info_parts,
        "warnings": warnings,
    }


def run_build(job: dict) -> dict:
    import build123d as bd

    code = Path(job["code_file"]).read_text(encoding="utf-8")
    namespace: dict = {"__name__": "__cad_model__", "math": math}
    exec("from build123d import *", namespace)  # noqa: S102 - local CAD sandbox
    exec(compile(code, "model.py", "exec"), namespace)  # noqa: S102

    if "result" not in namespace:
        # Friendly fallback: the last BuildPart / Part defined in the script.
        candidates = [
            v for k, v in namespace.items()
            if not k.startswith("_") and isinstance(v, (bd.BuildPart, bd.Part, bd.Solid, bd.Compound))
        ]
        if not candidates:
            raise NameError(
                "El código debe asignar el modelo final a una variable llamada `result` "
                "(p.ej. `result = part.part`). Puede ser un sólido, una lista o un dict {nombre: sólido} "
                "para piezas multicolor."
            )
        namespace["result"] = candidates[-1]

    shapes = _collect_shapes(namespace["result"])
    return _export(shapes, Path(job["out_dir"]), float(job.get("tolerance", 0.01)))


def _apply_matrix(shape, matrix):
    import build123d as bd
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.gp import gp_Trsf

    trsf = gp_Trsf()
    trsf.SetValues(*[float(v) for row in matrix[:3] for v in row[:4]])
    return bd.Compound(BRepBuilderAPI_Transform(shape.wrapped, trsf, True).Shape())


def run_transform(job: dict) -> dict:
    import build123d as bd

    shape = bd.import_step(job["step_file"])
    solids = shape.solids()
    moved = [(f"part_{i + 1}", _apply_matrix(s, job["matrix"])) for i, s in enumerate(solids)]
    return _export(moved, Path(job["out_dir"]), float(job.get("tolerance", 0.01)))


def run_import(job: dict) -> dict:
    import build123d as bd

    src = Path(job["source"]).expanduser()
    out_dir = Path(job["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = src.suffix.lower()
    if suffix in (".step", ".stp"):
        shape = bd.import_step(str(src))
        solids = shape.solids()
        return _export([(f"part_{i + 1}", s) for i, s in enumerate(solids)], out_dir,
                       float(job.get("tolerance", 0.01)))
    if suffix in (".stl", ".3mf", ".obj"):
        import trimesh

        mesh = trimesh.load(str(src), force="mesh")
        mesh.export(str(out_dir / "model.stl"))
        if suffix == ".3mf":
            shutil.copy(src, out_dir / "model.3mf")
        else:
            mesh.export(str(out_dir / "model.3mf"))
        b = mesh.bounds
        return {
            "ok": True,
            "mesh_only": True,
            "volume_mm3": round(float(mesh.volume), 3) if mesh.is_watertight else None,
            "bbox_min": [round(float(v), 3) for v in b[0]],
            "bbox_max": [round(float(v), 3) for v in b[1]],
            "valid": bool(mesh.is_watertight),
            "parts": [],
        }
    raise ValueError(f"Formato no soportado: {suffix} (usa STEP, STL, 3MF u OBJ)")


def main() -> None:
    job = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    try:
        mode = job["mode"]
        if mode == "build":
            res = run_build(job)
        elif mode == "transform":
            res = run_transform(job)
        elif mode == "import":
            res = run_import(job)
        else:
            raise ValueError(f"modo desconocido {mode}")
        _emit(res)
    except Exception as exc:  # report every failure back to the server
        tb = traceback.format_exc()
        # Keep only the frames that belong to the user's script when possible.
        user_tb = "\n".join(line for line in tb.splitlines() if "model.py" in line or not line.startswith("  File"))
        _emit({"ok": False, "error": f"{type(exc).__name__}: {exc}", "traceback": user_tb[-4000:]})


if __name__ == "__main__":
    main()
