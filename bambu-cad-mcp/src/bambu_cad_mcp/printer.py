"""Bambu Lab P2S printer profile, materials and FDM design rules."""

from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Bambu Lab P2S
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PrinterProfile:
    name: str
    build_x: float
    build_y: float
    build_z: float
    nozzle_diameter: float
    max_nozzle_temp: int
    max_bed_temp: int
    enclosed: bool
    notes: str


P2S = PrinterProfile(
    name="Bambu Lab P2S",
    build_x=256.0,
    build_y=256.0,
    build_z=256.0,
    nozzle_diameter=0.4,
    max_nozzle_temp=300,
    max_bed_temp=110,
    enclosed=True,
    notes=(
        "CoreXY cerrada, boquilla de acero endurecido 0.4 mm (opcionales 0.2/0.6/0.8), "
        "compatible con AMS 2 Pro / AMS HT para multicolor. Volumen 256 x 256 x 256 mm. "
        "Al ser cerrada imprime bien ABS/ASA/PC/PA; para PLA abre la tapa o la puerta si hace calor."
    ),
)

# Margen de seguridad respecto al volumen real (purga, clips de la placa...).
BUILD_MARGIN_MM = 2.0


# ---------------------------------------------------------------------------
# Materials
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Material:
    key: str
    name: str
    density: float  # g/cm3
    nozzle_temp: str
    bed_temp: str
    plate: str
    chamber: str
    max_overhang_deg: int
    notes: str
    needs_hardened_nozzle: bool = False
    needs_drying: bool = False


MATERIALS: dict[str, Material] = {
    m.key: m
    for m in [
        Material(
            "pla", "PLA / PLA Basic / PLA Matte", 1.24, "210-230 °C", "55-65 °C",
            "Textured PEI o Cool/Engineering plate", "Puerta abierta / tapa entreabierta",
            50, "El más fácil. Rígido, poco resistente al calor (~55 °C). Ideal para prototipos y decoración.",
        ),
        Material(
            "petg", "PETG / PETG HF", 1.27, "240-260 °C", "70-80 °C",
            "Textured PEI (en liso usa pegamento como separador)", "Cerrada, sin calentar",
            45, "Más tenaz que el PLA, aguanta ~70 °C, buena resistencia química. Propenso a hilos.",
        ),
        Material(
            "abs", "ABS", 1.04, "250-270 °C", "90-100 °C",
            "Textured PEI / Engineering plate + pegamento", "Cerrada, pre-calentar 10 min",
            45, "Resistente al calor (~95 °C) y lijable. Necesita la cámara cerrada (la P2S lo es).",
        ),
        Material(
            "asa", "ASA", 1.07, "250-270 °C", "90-100 °C",
            "Textured PEI / Engineering plate", "Cerrada",
            45, "Como el ABS pero resistente a UV: perfecto para piezas de exterior.",
        ),
        Material(
            "tpu", "TPU 95A / 85A", 1.21, "220-240 °C", "30-40 °C",
            "Textured PEI", "Puerta abierta",
            40, "Flexible. Imprime lento, sin AMS (usa el portabobinas externo). Evita retracciones largas.",
        ),
        Material(
            "pc", "PC (policarbonato)", 1.20, "260-290 °C", "100-110 °C",
            "Engineering plate + pegamento", "Cerrada, caliente",
            40, "Muy resistente (~115 °C). Tiende a deformarse (warping): usa bordes (brim).", needs_drying=True,
        ),
        Material(
            "petg-cf", "PETG-CF", 1.25, "250-270 °C", "70-80 °C",
            "Textured PEI", "Cerrada",
            45, "PETG con fibra de carbono: más rígido y acabado mate.", needs_hardened_nozzle=True,
        ),
        Material(
            "pa-cf", "PA-CF / PAHT-CF (nylon + carbono)", 1.10, "270-300 °C", "80-100 °C",
            "Engineering plate + pegamento", "Cerrada, caliente",
            40, "Piezas mecánicas de alta exigencia. SECAR SIEMPRE antes de imprimir.",
            needs_hardened_nozzle=True, needs_drying=True,
        ),
    ]
}


def get_material(key: str) -> Material:
    k = key.strip().lower().replace(" ", "-").replace("_", "-")
    if k not in MATERIALS:
        raise ValueError(f"Material desconocido '{key}'. Opciones: {', '.join(MATERIALS)}")
    return MATERIALS[k]


# ---------------------------------------------------------------------------
# Design-for-FDM rules (0.4 mm nozzle)
# ---------------------------------------------------------------------------

DESIGN_RULES = """\
# Reglas de diseño para FDM en la Bambu Lab P2S (boquilla 0.4 mm)

## Paredes y detalles
- Pared mínima: 0.8 mm (2 líneas). Recomendado para piezas funcionales: 1.6-2.4 mm (4-6 líneas).
  Diseña espesores múltiplos de ~0.42 mm para que el slicer no deje huecos.
- Detalle mínimo en XY: ~0.4 mm. En Z depende de la altura de capa (0.2 mm estándar, 0.08 mm fino).
- Texto en relieve/grabado: altura de letra >= 5 mm, profundidad/relieve >= 0.6 mm, trazos >= 0.8 mm.

## Voladizos y puentes
- Voladizo sin soporte: hasta 45° respecto a la vertical (PLA aguanta ~50°).
- Puentes: hasta ~20-30 mm sin soporte con buena refrigeración.
- Agujeros horizontales (eje paralelo a la cama) > 6 mm: usa forma de lágrima (teardrop) o techo a 45°.
- Prefiere chaflanes de 45° a filetes en caras que miran hacia abajo.

## Cara en la cama
- Pon la cara plana más grande sobre la cama. Evita filetes en el borde inferior: usa chaflán de 0.4-0.6 mm
  (compensa el "pie de elefante").
- Piezas altas y estrechas (alto > 4 x base): añade brim o ensancha la base.

## Tolerancias (calibradas típicas para Bambu, ajusta con una pieza de prueba)
- Ajuste a presión (press fit): 0.05-0.10 mm de holgura por lado.
- Ajuste deslizante: 0.15-0.25 mm por lado.
- Ajuste holgado / piezas que giran: 0.3-0.4 mm por lado.
- Tapas encajadas: 0.2 mm por lado.
- Piezas impresas ensambladas en el mismo trabajo (print-in-place): >= 0.3-0.4 mm de separación.
- Los agujeros verticales salen ~0.1-0.2 mm más pequeños: sobredimensiona.

## Tornillería
- M2: paso 2.4 mm, roscar directo 1.7 mm.
- M3: paso 3.4 mm, roscar directo 2.6-2.8 mm, inserto térmico M3 (Ruthex/CNC Kitchen) 4.0-4.2 mm x 5.7 mm de profundidad.
- M4: paso 4.5 mm, roscar directo 3.5 mm, inserto térmico 5.6 mm.
- M5: paso 5.5 mm, roscar directo 4.4 mm.
- Cabeza M3 alojamiento: Ø6 mm x 3.2 mm; tuerca M3 hexagonal: 5.7 mm entre caras + 0.3 mm, 2.6 mm de alto.
- Mejor insertos térmicos que roscas impresas para tornillos < M8.

## Resistencia
- Las piezas son más débiles entre capas (eje Z). Orienta para que la carga vaya en el plano XY.
- Más paredes (perímetros) aportan más resistencia que más relleno.
- Relleno: 10-15 % decorativo, 20-30 % funcional, 40 %+ o gyroid para piezas sometidas a esfuerzo.

## Multicolor (AMS)
- Separa los colores en sólidos distintos (cada uno se exporta como objeto del 3MF y se asigna a un filamento).
"""
