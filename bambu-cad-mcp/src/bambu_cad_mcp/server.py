"""Bambu CAD MCP server — parametric CAD (build123d / OpenCascade) for the Bambu Lab P2S."""

from __future__ import annotations

import datetime as _dt
import functools
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

try:  # MCP Python SDK 2.x
    from mcp.server.mcpserver import Image
    from mcp.server.mcpserver import MCPServer as _Server
    from mcp.server.mcpserver.exceptions import ToolError
except ImportError:  # MCP Python SDK 1.x
    from mcp.server.fastmcp import FastMCP as _Server  # type: ignore[no-redef]
    from mcp.server.fastmcp import Image  # type: ignore[no-redef]
    from mcp.server.fastmcp.exceptions import ToolError  # type: ignore[no-redef]

from . import analysis, render, slicer
from .guide import BUILD123D_GUIDE, WORKFLOW
from .printer import DESIGN_RULES, MATERIALS, P2S, get_material
from .templates import TEMPLATES, render_template

WORKSPACE = Path(os.environ.get("BAMBU_CAD_WORKSPACE", Path.home() / "BambuCAD")).expanduser()
TIMEOUT = int(os.environ.get("BAMBU_CAD_TIMEOUT", "180"))
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{0,63}$")

INSTRUCTIONS = f"""\
Eres un ingeniero de diseño mecánico experto en CAD paramétrico e impresión 3D FDM.
Este servidor modela piezas con build123d (kernel B-rep OpenCascade, el mismo tipo de motor que
usan SolidWorks/Fusion), las analiza para la {P2S.name} ({P2S.build_x:.0f}x{P2S.build_y:.0f}x{P2S.build_z:.0f} mm)
y exporta STEP / STL / 3MF listos para Bambu Studio.

{WORKFLOW}
Antes de escribir tu primer modelo llama a `design_guide` (tema "build123d") para ver la sintaxis.
Diseña siempre pensando en la fabricación: cara plana grande en Z=0, voladizos <= 45°, paredes >= 1.2 mm,
holguras según el tipo de ajuste. Explica al usuario cómo orientar e imprimir la pieza (material,
paredes, relleno, soportes).
"""

mcp = _Server("bambu-cad", instructions=INSTRUCTIONS)


def tool(**kwargs):
    """Register a tool, turning expected errors into readable ToolErrors for the model."""

    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kw):
            try:
                return fn(*args, **kw)
            except ToolError:
                raise
            except (ValueError, FileNotFoundError, RuntimeError, TimeoutError, OSError) as exc:
                raise ToolError(str(exc)) from exc

        mcp.tool(**kwargs)(wrapper)
        return wrapper

    return deco


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _model_dir(name: str, must_exist: bool = True) -> Path:
    if not NAME_RE.match(name):
        raise ValueError("Nombre inválido: usa letras, números, '-' o '_' (máx. 64), sin espacios.")
    d = WORKSPACE / name
    if must_exist and not (d / "model.stl").exists():
        raise FileNotFoundError(f"No existe el modelo '{name}'. Usa list_models para ver los disponibles.")
    return d


def _run_worker(job: dict) -> dict:
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
        json.dump(job, fh)
        job_path = fh.name
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "bambu_cad_mcp.worker", job_path],
            capture_output=True, text=True, timeout=TIMEOUT,
            cwd=job.get("out_dir"),
            env={**os.environ, "PYTHONPATH": os.pathsep.join([str(Path(__file__).parent.parent),
                                                               os.environ.get("PYTHONPATH", "")])},
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"Tiempo agotado ({TIMEOUT}s). Simplifica el modelo o sube BAMBU_CAD_TIMEOUT."}
    finally:
        os.unlink(job_path)
    for line in reversed(proc.stdout.splitlines()):
        if line.startswith("@@RESULT@@"):
            res = json.loads(line[len("@@RESULT@@"):])
            user_out = "\n".join(l for l in proc.stdout.splitlines() if not l.startswith("@@RESULT@@")).strip()
            if user_out:
                res["stdout"] = user_out[-2000:]
            return res
    return {"ok": False, "error": "El proceso CAD terminó sin resultado (¿crash del kernel?).",
            "traceback": (proc.stderr or "")[-3000:]}


def _commit(name: str, staging: Path, meta: dict) -> Path:
    """Move a successful build from the staging dir into the workspace, keeping history."""
    dest = _model_dir(name, must_exist=False)
    dest.mkdir(parents=True, exist_ok=True)
    hist = dest / "history"
    if (dest / "model.py").exists():
        hist.mkdir(exist_ok=True)
        stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.copy(dest / "model.py", hist / f"{stamp}.py")
    for f in ("model.py", "model.step", "model.stl", "model.3mf"):
        if (staging / f).exists():
            shutil.move(str(staging / f), dest / f)
        elif (dest / f).exists() and f != "model.py":
            (dest / f).unlink()
    (dest / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    return dest


def _report_and_preview(name: str, material: str, infill: float, walls: int, preview: bool,
                        header: str = "") -> list:
    d = _model_dir(name)
    mesh = analysis.load_mesh(d / "model.stl")
    rep = analysis.analyze(mesh, material, infill, walls)
    out: list = []
    text = (header + "\n" if header else "") + rep.as_text()
    text += f"\n\nArchivos: {d / 'model.step'}\n          {d / 'model.stl'}\n          {d / 'model.3mf'}"
    out.append(text)
    if preview:
        png = render.render_png(mesh, highlight_overhangs=True,
                                overhang_deg=get_material(material).max_overhang_deg, title=name)
        (d / "preview.png").write_bytes(png)
        out.append(Image(data=png, format="png"))
    return out


def _format_build_error(res: dict) -> str:
    msg = f"❌ Error al generar el modelo: {res.get('error')}"
    if res.get("traceback"):
        msg += f"\n\nTraceback:\n{res['traceback']}"
    if res.get("stdout"):
        msg += f"\n\nSalida del script:\n{res['stdout']}"
    msg += "\n\nCorrige el código y vuelve a llamar a create_model (el modelo anterior, si existía, se conserva)."
    return msg


def _build(name: str, code: str, material: str, infill: float, walls: int, preview: bool,
           tolerance: float, extra_header: str = "") -> list:
    _model_dir(name, must_exist=False)
    get_material(material)
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{name}-", dir=WORKSPACE))
    try:
        (staging / "model.py").write_text(code, encoding="utf-8")
        res = _run_worker({"mode": "build", "code_file": str(staging / "model.py"),
                           "out_dir": str(staging), "tolerance": tolerance})
        if not res.get("ok"):
            return [_format_build_error(res)]
        _commit(name, staging, {"source": "code", "material": material, "build": res})
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    header = f"✅ Modelo '{name}' generado"
    if len(res.get("parts", [])) > 1:
        header += " · piezas: " + ", ".join(
            f"{p['name']} ({p['size_mm'][0]}x{p['size_mm'][1]}x{p['size_mm'][2]} mm)" for p in res["parts"])
    if not res.get("valid", True):
        header += "\n⚠️ El B-rep no es válido según OpenCascade: revisa booleanas/filetes."
    for w in res.get("warnings", []):
        header += f"\n⚠️ {w}"
    if res.get("stdout"):
        header += f"\nSalida del script:\n{res['stdout']}"
    if extra_header:
        header += "\n" + extra_header
    return _report_and_preview(name, material, infill, walls, preview, header)


# ---------------------------------------------------------------------------
# Knowledge
# ---------------------------------------------------------------------------


@tool()
def design_guide(topic: str = "all") -> str:
    """Guía experta de diseño. topic: "build123d" (sintaxis CAD para escribir modelos),
    "fdm" (reglas de diseño, tolerancias, tornillería), "p2s" (la impresora), "materials", "workflow" o "all"."""
    t = topic.lower()
    parts = []
    if t in ("all", "p2s", "printer"):
        parts.append(f"# {P2S.name}\nVolumen: {P2S.build_x:.0f} x {P2S.build_y:.0f} x {P2S.build_z:.0f} mm · "
                     f"boquilla {P2S.nozzle_diameter} mm · hasta {P2S.max_nozzle_temp} °C boquilla / "
                     f"{P2S.max_bed_temp} °C cama · {'cerrada' if P2S.enclosed else 'abierta'}\n{P2S.notes}")
    if t in ("all", "workflow"):
        parts.append(WORKFLOW)
    if t in ("all", "build123d", "cad", "code"):
        parts.append(BUILD123D_GUIDE)
    if t in ("all", "fdm", "rules", "tolerances"):
        parts.append(DESIGN_RULES)
    if t in ("all", "materials", "material"):
        parts.append(materials_guide())
    return "\n\n".join(parts) if parts else "Tema desconocido. Usa: build123d, fdm, p2s, materials, workflow, all."


@tool()
def materials_guide(material: str | None = None) -> str:
    """Temperaturas, placa, cámara y consejos por material para la P2S (PLA, PETG, ABS, ASA, TPU, PC, PETG-CF, PA-CF)."""
    mats = [get_material(material)] if material else list(MATERIALS.values())
    rows = []
    for m in mats:
        flags = []
        if m.needs_hardened_nozzle:
            flags.append("abrasivo (boquilla endurecida)")
        if m.needs_drying:
            flags.append("secar antes")
        rows.append(
            f"## {m.name} (`{m.key}`)\n- Boquilla {m.nozzle_temp} · cama {m.bed_temp} · {m.plate}\n"
            f"- Cámara: {m.chamber} · voladizo máx ~{m.max_overhang_deg}° · densidad {m.density} g/cm³"
            + (f"\n- {', '.join(flags)}" if flags else "") + f"\n- {m.notes}"
        )
    return "\n\n".join(rows)


# ---------------------------------------------------------------------------
# Modelling
# ---------------------------------------------------------------------------


@tool(structured_output=False)
def create_model(name: str, code: str, material: str = "pla", infill_pct: float = 15, walls: int = 2,
                 preview: bool = True, tolerance: float = 0.01) -> list:
    """Crea (o sobrescribe, guardando historial) un modelo CAD a partir de código Python build123d.

    El código se ejecuta con `from build123d import *` e `import math` ya disponibles y debe asignar
    el modelo final a `result` (sólido, lista o dict {nombre: sólido} para varias piezas / multicolor).
    Exporta STEP + STL + 3MF, analiza la imprimibilidad en la P2S y devuelve una vista previa
    (voladizos en rojo). `tolerance` = desviación máxima de la malla en mm (0.01 = muy fino)."""
    return _build(name, code, material, infill_pct, walls, preview, tolerance)


@tool()
def list_templates() -> str:
    """Lista las plantillas paramétricas disponibles y sus parámetros por defecto."""
    lines = []
    for t in TEMPLATES.values():
        params = ", ".join(f"{k}={v}" for k, v in t.defaults.items())
        lines.append(f"### {t.name}\n{t.description}\nParámetros: {params}\nImpresión: {t.print_tips}")
    return "\n\n".join(lines)


@tool(structured_output=False)
def create_from_template(template: str, name: str, params: dict | None = None, material: str = "pla",
                         preview: bool = True) -> list:
    """Genera una pieza desde una plantilla paramétrica (enclosure, l_bracket, spacer, cable_clip,
    phone_stand, knob, tolerance_test). `params` sobrescribe valores por defecto, p.ej. {"width": 100}.
    El código generado queda guardado y se puede editar con get_model_code + create_model."""
    code, t = render_template(template, params)
    return _build(name, code, material, 15, 2, preview, 0.01, extra_header=f"🖨️ Cómo imprimir: {t.print_tips}")


@tool()
def get_model_code(name: str) -> str:
    """Devuelve el código build123d de un modelo para modificarlo e iterar."""
    d = _model_dir(name)
    p = d / "model.py"
    if not p.exists():
        return "Este modelo se importó desde un archivo y no tiene código fuente."
    return p.read_text(encoding="utf-8")


@tool()
def list_models() -> str:
    """Lista los modelos del espacio de trabajo con su tamaño."""
    if not WORKSPACE.exists():
        return f"Espacio de trabajo vacío ({WORKSPACE})."
    rows = []
    for d in sorted(p for p in WORKSPACE.iterdir() if p.is_dir() and not p.name.startswith(".")):
        meta_p = d / "meta.json"
        if not (d / "model.stl").exists():
            continue
        size = "?"
        if meta_p.exists():
            b = json.loads(meta_p.read_text(encoding="utf-8")).get("build", {})
            if b.get("bbox_min"):
                size = " x ".join(f"{hi - lo:.1f}" for lo, hi in zip(b["bbox_min"], b["bbox_max"])) + " mm"
        mtime = _dt.datetime.fromtimestamp((d / "model.stl").stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        rows.append(f"- {d.name}: {size} (modificado {mtime})")
    return f"Espacio de trabajo: {WORKSPACE}\n" + ("\n".join(rows) if rows else "(sin modelos)")


@tool(structured_output=False)
def import_model(path: str, name: str, material: str = "pla") -> list:
    """Importa un STEP/STP (sólido editable), STL, 3MF u OBJ existente para analizarlo, orientarlo o reenviarlo."""
    src = Path(path).expanduser()
    if not src.exists():
        raise FileNotFoundError(f"No existe {src}")
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{name}-", dir=WORKSPACE))
    try:
        res = _run_worker({"mode": "import", "source": str(src), "out_dir": str(staging)})
        if not res.get("ok"):
            return [_format_build_error(res)]
        _model_dir(name, must_exist=False)
        _commit(name, staging, {"source": str(src), "material": material, "build": res})
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return _report_and_preview(name, material, 15, 2, True, f"✅ Importado '{src.name}' como '{name}'")


# ---------------------------------------------------------------------------
# Analysis & preview
# ---------------------------------------------------------------------------


@tool()
def analyze_model(name: str, material: str = "pla", infill_pct: float = 15, walls: int = 2) -> str:
    """Informe de imprimibilidad en la P2S: tamaño, si cabe, malla cerrada, contacto con cama,
    voladizos/soportes, pared mínima, gramos, metros y tiempo estimado."""
    mesh = analysis.load_mesh(_model_dir(name) / "model.stl")
    return analysis.analyze(mesh, material, infill_pct, walls).as_text()


@tool(structured_output=False)
def render_model(name: str, views: list[str] | None = None, highlight_overhangs: bool = True) -> list:
    """Renderiza el modelo. views: iso, iso_back, front, back, left, right, top, bottom
    (por defecto iso/front/right/top). Los voladizos que necesitan soporte salen en rojo."""
    d = _model_dir(name)
    mesh = analysis.load_mesh(d / "model.stl")
    png = render.render_png(mesh, views=tuple(views or ("iso", "front", "right", "top")),
                            highlight_overhangs=highlight_overhangs, title=name)
    return [Image(data=png, format="png")]


@tool(structured_output=False)
def orient_for_print(name: str, new_name: str | None = None, material: str = "pla") -> list:
    """Busca la orientación óptima para imprimir (mínimo soporte, máximo contacto con la cama,
    menor altura) y genera el modelo rotado y apoyado en Z=0 (STEP incluido)."""
    d = _model_dir(name)
    target = new_name or f"{name}_orientado"
    mesh = analysis.load_mesh(d / "model.stl")
    limit = get_material(material).max_overhang_deg
    before_oh = analysis.overhang_area(mesh, limit)
    T, ranking = analysis.best_orientation(mesh, limit)
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{target}-", dir=WORKSPACE))
    try:
        if (d / "model.step").exists():
            res = _run_worker({"mode": "transform", "step_file": str(d / "model.step"),
                               "matrix": T.tolist(), "out_dir": str(staging)})
            if not res.get("ok"):
                return [_format_build_error(res)]
        else:
            mesh.apply_transform(T)
            mesh.export(str(staging / "model.stl"))
            mesh.export(str(staging / "model.3mf"))
            res = {"ok": True, "mesh_only": True}
        _model_dir(target, must_exist=False)
        if (d / "model.py").exists():
            shutil.copy(d / "model.py", staging / "model.py")
        _commit(target, staging, {"source": f"orient_for_print({name})", "material": material,
                                  "transform": T.tolist(), "build": res})
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    best = ranking[0]
    header = (f"✅ '{target}' orientado: voladizos {before_oh:.0f} → {best['overhang_mm2']:.0f} mm², "
              f"contacto con cama {best['contact_mm2']:.0f} mm², altura {best['height_mm']} mm.\n"
              "Alternativas (voladizo mm² / contacto mm² / altura mm): "
              + "; ".join(f"{r['overhang_mm2']}/{r['contact_mm2']}/{r['height_mm']}" for r in ranking[1:4])
              + "\nNota: el código fuente no incluye la rotación; si editas el código vuelve a orientar.")
    return _report_and_preview(target, material, 15, 2, True, header)


# ---------------------------------------------------------------------------
# Export / slicer / printer
# ---------------------------------------------------------------------------


@tool()
def export_model(name: str, format: str = "3mf", destination: str | None = None) -> str:
    """Copia el modelo en formato 3mf (Bambu Studio, multi-pieza), stl, step (para editar en Fusion/
    FreeCAD/Onshape) o py (código) a `destination` (carpeta). Por defecto ~/Downloads o el workspace."""
    fmt = format.lower().lstrip(".")
    ext = {"3mf": "3mf", "stl": "stl", "step": "step", "stp": "step", "py": "py"}.get(fmt)
    if not ext:
        raise ValueError("Formato no soportado: usa 3mf, stl, step o py")
    d = _model_dir(name)
    src = d / f"model.{ext}"
    if not src.exists():
        raise FileNotFoundError(f"El modelo no tiene formato {ext} (¿importado desde malla?).")
    if destination:
        dest_dir = Path(destination).expanduser()
    else:
        dl = Path.home() / "Downloads"
        dest_dir = dl if dl.exists() else d
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{name}.{ext}"
    shutil.copy(src, dest)
    return f"Exportado: {dest}"


@tool()
def open_in_slicer(name: str) -> str:
    """Abre el 3MF del modelo en Bambu Studio u OrcaSlicer (en el ordenador donde corre el MCP)."""
    d = _model_dir(name)
    return slicer.open_in_slicer(d / "model.3mf")


@tool()
def slice_model(name: str, machine_profile: str | None = None, process_profile: str | None = None,
                filament_profiles: list[str] | None = None) -> str:
    """Lamina el modelo con la CLI de Bambu Studio / OrcaSlicer y genera <name>.gcode.3mf.
    Los perfiles son JSON exportados de Bambu Studio (máquina P2S, proceso, filamento) o se toman de
    BAMBU_PROFILE_MACHINE / BAMBU_PROFILE_PROCESS / BAMBU_PROFILE_FILAMENT."""
    d = _model_dir(name)
    out = d / f"{name}.gcode.3mf"
    res = slicer.slice_model(d / "model.3mf", out, machine_profile, process_profile, filament_profiles)
    if res["ok"]:
        msg = f"✅ Laminado: {res['output']}"
        if not res["used_profiles"]:
            msg += ("\n⚠️ Sin perfiles explícitos: el slicer usó los valores por defecto. Para la P2S define "
                    "BAMBU_PROFILE_MACHINE/PROCESS/FILAMENT con tus presets exportados.")
        return msg
    return f"❌ El laminado falló (código {res['returncode']}).\nComando: {res['command']}\nLog:\n{res['log']}"


@tool()
def printer_status() -> str:
    """[LAN, experimental] Estado de la P2S: estado del trabajo, progreso, temperaturas.
    Requiere BAMBU_PRINTER_IP, BAMBU_PRINTER_SERIAL y BAMBU_ACCESS_CODE."""
    from . import bambu_lan

    conn = bambu_lan.PrinterConn.from_env()
    return bambu_lan.summarize_status(bambu_lan.mqtt_request(conn, None))


@tool()
def send_to_printer(name: str, confirm: bool = False, use_ams: bool = True, plate: int = 1,
                    timelapse: bool = False) -> str:
    """[LAN, experimental] Sube <name>.gcode.3mf (de slice_model) a la P2S y empieza a imprimir.
    Pide confirmación explícita al usuario y llama con confirm=true: la impresora empezará a calentar y moverse."""
    from . import bambu_lan

    d = _model_dir(name)
    g = d / f"{name}.gcode.3mf"
    if not g.exists():
        return "No hay archivo laminado. Ejecuta antes slice_model (o lamina en Bambu Studio y envía desde allí)."
    if not confirm:
        return (f"Listo para enviar {g.name} a la impresora. Asegúrate de que la placa está limpia y vacía y el "
                "filamento correcto cargado. Vuelve a llamar con confirm=true para imprimir.")
    conn = bambu_lan.PrinterConn.from_env()
    report = bambu_lan.start_print(conn, g, plate=plate, use_ams=use_ams, timelapse=timelapse)
    return "🖨️ Trabajo enviado.\n" + bambu_lan.summarize_status(report)


@tool()
def printer_control(action: str, confirm: bool = False) -> str:
    """[LAN, experimental] Controla la impresora: pause, resume, stop, light_on, light_off.
    'stop' cancela el trabajo: requiere confirm=true."""
    from . import bambu_lan

    if action not in bambu_lan.CONTROL_COMMANDS:
        return f"Acción desconocida. Opciones: {', '.join(bambu_lan.CONTROL_COMMANDS)}"
    if action == "stop" and not confirm:
        return "Esto cancelará la impresión en curso. Llama de nuevo con confirm=true si el usuario lo confirma."
    conn = bambu_lan.PrinterConn.from_env()
    report = bambu_lan.mqtt_request(conn, bambu_lan.CONTROL_COMMANDS[action])
    return f"Comando '{action}' enviado.\n" + bambu_lan.summarize_status(report)


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------


@mcp.prompt()
def design_part(description: str, material: str = "PLA") -> str:
    """Diseña una pieza para imprimir en la P2S a partir de una descripción."""
    return (
        f"Diseña para imprimir en la Bambu Lab P2S en {material}: {description}\n\n"
        "1) Pregúntame las medidas críticas que falten. 2) Lee design_guide('build123d') y "
        "design_guide('fdm'). 3) Crea el modelo paramétrico con create_model y revisa la vista previa. "
        "4) Corrige voladizos/paredes finas. 5) Orienta con orient_for_print. 6) Dime perfil de impresión "
        "recomendado (altura de capa, paredes, relleno, soportes, placa) y exporta el 3MF."
    )


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
