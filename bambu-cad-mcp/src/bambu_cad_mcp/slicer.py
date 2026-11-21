"""Bambu Studio / OrcaSlicer command-line integration."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path

CANDIDATE_NAMES = ["bambu-studio", "BambuStudio", "bambustudio", "orca-slicer", "OrcaSlicer", "orcaslicer"]
CANDIDATE_PATHS = [
    "/Applications/BambuStudio.app/Contents/MacOS/BambuStudio",
    "/Applications/OrcaSlicer.app/Contents/MacOS/OrcaSlicer",
    r"C:\Program Files\Bambu Studio\bambu-studio.exe",
    r"C:\Program Files\OrcaSlicer\orca-slicer.exe",
    "/opt/bambu-studio/bin/bambu-studio",
    "/usr/bin/bambu-studio",
]


def find_slicer() -> str | None:
    env = os.environ.get("BAMBU_SLICER_PATH")
    if env and Path(env).exists():
        return env
    for n in CANDIDATE_NAMES:
        p = shutil.which(n)
        if p:
            return p
    for p in CANDIDATE_PATHS:
        if Path(p).exists():
            return p
    return None


def open_in_slicer(model_path: Path) -> str:
    slicer = find_slicer()
    system = platform.system()
    if slicer is None:
        if system == "Darwin":
            subprocess.Popen(["open", str(model_path)])
            return "Abierto con la aplicación por defecto de macOS para .3mf."
        if system == "Windows":
            os.startfile(str(model_path))  # type: ignore[attr-defined]
            return "Abierto con la aplicación por defecto de Windows para .3mf."
        raise FileNotFoundError(
            "No encuentro Bambu Studio ni OrcaSlicer. Instálalo o define BAMBU_SLICER_PATH."
        )
    if system == "Darwin" and ".app/" in slicer:
        app = slicer.split(".app/")[0] + ".app"
        subprocess.Popen(["open", "-a", app, str(model_path)])
    else:
        subprocess.Popen([slicer, str(model_path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return f"Abierto en {Path(slicer).name}."


def slice_model(model_3mf: Path, out_file: Path, machine: str | None = None, process: str | None = None,
                filaments: list[str] | None = None, timeout: int = 900) -> dict:
    """Slice a 3MF with the Bambu Studio / OrcaSlicer CLI into a printable ``.gcode.3mf``.

    Profiles are JSON files exported from Bambu Studio (File → Export → Export preset bundle, or the
    user presets folder). They can also come from the env vars BAMBU_PROFILE_MACHINE,
    BAMBU_PROFILE_PROCESS and BAMBU_PROFILE_FILAMENT (several filaments separated by ';').
    """
    slicer = find_slicer()
    if slicer is None:
        raise FileNotFoundError("No encuentro Bambu Studio ni OrcaSlicer. Instálalo o define BAMBU_SLICER_PATH.")
    machine = machine or os.environ.get("BAMBU_PROFILE_MACHINE")
    process = process or os.environ.get("BAMBU_PROFILE_PROCESS")
    if not filaments and os.environ.get("BAMBU_PROFILE_FILAMENT"):
        filaments = os.environ["BAMBU_PROFILE_FILAMENT"].split(";")

    cmd = [slicer, "--arrange", "1", "--orient", "0"]
    settings = [s for s in (machine, process) if s]
    if settings:
        cmd += ["--load-settings", ";".join(settings)]
    if filaments:
        cmd += ["--load-filaments", ";".join(filaments)]
    cmd += ["--slice", "0", "--export-3mf", str(out_file.name), "--outputdir", str(out_file.parent),
            str(model_3mf)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    ok = proc.returncode == 0 and out_file.exists()
    return {
        "ok": ok,
        "command": " ".join(f'"{c}"' if " " in c else c for c in cmd),
        "returncode": proc.returncode,
        "output": out_file.as_posix() if ok else None,
        "log": (proc.stdout + "\n" + proc.stderr)[-3000:],
        "used_profiles": bool(settings),
    }
