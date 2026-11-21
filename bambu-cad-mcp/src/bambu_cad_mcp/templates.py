"""Parametric part templates.

Each template generates *build123d source code* (not a solid) so the result
goes through the same pipeline as hand-written models and the user/LLM can
read, tweak and re-run the code with ``create_model``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass
class Template:
    name: str
    description: str
    defaults: dict
    builder: Callable[[dict], str]
    print_tips: str


def _header(p: dict) -> str:
    lines = ["# Parámetros (mm / grados)"]
    for k, v in p.items():
        lines.append(f"{k.upper()} = {v!r}")
    return "\n".join(lines) + "\n\n"


# ---------------------------------------------------------------------------


def _enclosure(p: dict) -> str:
    return _header(p) + '''\
# --- Caja ---
with BuildPart() as caja:
    Box(WIDTH, DEPTH, HEIGHT, align=(Align.CENTER, Align.CENTER, Align.MIN))
    fillet(caja.edges().filter_by(Axis.Z), CORNER_RADIUS)
    # Vaciado dejando la cara superior abierta
    offset(amount=-WALL, openings=caja.faces().sort_by(Axis.Z)[-1])
    # Chaflán inferior contra el pie de elefante
    chamfer(caja.edges().group_by(Axis.Z)[0], 0.5)
    if SCREW_POSTS:
        px, py = WIDTH / 2 - WALL - 3.4, DEPTH / 2 - WALL - 3.4  # solapan 0.6 mm con la pared
        with BuildSketch(Plane.XY.offset(WALL)):
            with Locations((px, py), (-px, py), (px, -py), (-px, -py)):
                Circle(4)
                Circle(INSERT_HOLE / 2, mode=Mode.SUBTRACT)
        extrude(amount=HEIGHT - WALL)

# --- Tapa (se imprime boca arriba, con el labio hacia arriba) ---
in_w = WIDTH - 2 * WALL - 2 * FIT_CLEARANCE
in_d = DEPTH - 2 * WALL - 2 * FIT_CLEARANCE
in_r = max(CORNER_RADIUS - WALL - FIT_CLEARANCE, 0.5)
with BuildPart() as tapa:
    with BuildSketch():
        RectangleRounded(WIDTH, DEPTH, CORNER_RADIUS)
    extrude(amount=LID_THICKNESS)
    chamfer(tapa.edges().group_by(Axis.Z)[0], 0.4)
    with BuildSketch(Plane.XY.offset(LID_THICKNESS)):
        RectangleRounded(in_w, in_d, in_r)
        RectangleRounded(in_w - 2 * LIP_WALL, in_d - 2 * LIP_WALL, max(in_r - LIP_WALL, 0.3), mode=Mode.SUBTRACT)
        if SCREW_POSTS:
            # Rebajes en el labio para que no choque con las columnas
            px, py = WIDTH / 2 - WALL - 3.4, DEPTH / 2 - WALL - 3.4  # solapan 0.6 mm con la pared
            with Locations((px, py), (-px, py), (px, -py), (-px, -py)):
                Circle(4 + FIT_CLEARANCE + 0.2, mode=Mode.SUBTRACT)
    extrude(amount=LIP_HEIGHT)
    if SCREW_POSTS:
        px, py = WIDTH / 2 - WALL - 3.4, DEPTH / 2 - WALL - 3.4  # solapan 0.6 mm con la pared
        with BuildSketch():
            with Locations((px, py), (-px, py), (px, -py), (-px, -py)):
                Circle(SCREW_CLEARANCE / 2)
        extrude(amount=LID_THICKNESS, mode=Mode.SUBTRACT)

result = {"caja": caja.part, "tapa": Pos(WIDTH + 10, 0, 0) * tapa.part}
'''


def _l_bracket(p: dict) -> str:
    return _header(p) + '''\
with BuildPart() as escuadra:
    with BuildSketch(Plane.XZ):
        with BuildLine():
            Polyline((0, 0), (LEG_A, 0), (LEG_A, THICKNESS), (THICKNESS, THICKNESS),
                     (THICKNESS, LEG_B), (0, LEG_B), close=True)
        make_face()
    extrude(amount=WIDTH / 2, both=True)
    # Filete en la esquina interior (concentra menos tensiones)
    inner = escuadra.edges().filter_by(Axis.Y).sort_by_distance((THICKNESS, 0, THICKNESS))[0]
    fillet(inner, INNER_FILLET)
    # Nervio central
    if GUSSET:
        g = min(LEG_A, LEG_B) * 0.6
        with BuildSketch(Plane.XZ):
            with BuildLine():
                Polyline((THICKNESS - 0.1, THICKNESS - 0.1), (g, THICKNESS - 0.1), (THICKNESS - 0.1, g), close=True)
            make_face()
        extrude(amount=GUSSET_THICKNESS / 2, both=True)
    # Agujeros en el ala horizontal
    ys = [-WIDTH / 4, WIDTH / 4] if GUSSET else [0]
    hx = THICKNESS + (LEG_A - THICKNESS) * 0.6
    with BuildSketch(Plane.XY.offset(THICKNESS)):
        with Locations(*[(hx, y) for y in ys]):
            Circle(HOLE_DIAMETER / 2)
    extrude(amount=-THICKNESS, mode=Mode.SUBTRACT)
    # Agujeros en el ala vertical
    hz = THICKNESS + (LEG_B - THICKNESS) * 0.6
    with BuildSketch(Plane.YZ):
        with Locations(*[(y, hz) for y in ys]):
            Circle(HOLE_DIAMETER / 2)
    extrude(amount=THICKNESS, mode=Mode.SUBTRACT)

result = escuadra.part
'''


def _spacer(p: dict) -> str:
    return _header(p) + '''\
with BuildPart() as separador:
    with BuildSketch():
        if HEX:
            RegularPolygon(OUTER_DIAMETER / 2, 6)
        else:
            Circle(OUTER_DIAMETER / 2)
        Circle(INNER_DIAMETER / 2, mode=Mode.SUBTRACT)
    extrude(amount=HEIGHT)
    chamfer(separador.edges().group_by(Axis.Z)[0], 0.4)

result = separador.part
'''


def _cable_clip(p: dict) -> str:
    return _header(p) + '''\
r_in = CABLE_DIAMETER / 2 + 0.2
r_out = r_in + WALL
with BuildPart() as clip:
    with BuildSketch(Plane.XZ):
        with Locations((0, r_out)):
            Circle(r_out)
            Circle(r_in, mode=Mode.SUBTRACT)
            # Abertura superior para meter el cable a presión
            with Locations((0, r_out)):
                Rectangle(CABLE_DIAMETER * OPENING_RATIO, WALL * 3, mode=Mode.SUBTRACT)
        with Locations((0, WALL / 2)):
            Rectangle(2 * (r_out + TAB_LENGTH), WALL)
        with Locations((0, r_out)):
            Circle(r_in, mode=Mode.SUBTRACT)
    extrude(amount=CLIP_WIDTH / 2, both=True)
    with BuildSketch(Plane.XY.offset(WALL)):
        with Locations((r_out + TAB_LENGTH / 2, 0), (-(r_out + TAB_LENGTH / 2), 0)):
            Circle(SCREW_HOLE / 2)
    extrude(amount=-WALL, mode=Mode.SUBTRACT)

result = clip.part
'''


def _phone_stand(p: dict) -> str:
    return _header(p) + '''\
a = math.radians(ANGLE)
ledge = 14.0
lr = REST_LENGTH
px, pz = ledge + 0.6 * lr * math.cos(a), 0.6 * lr * math.sin(a)
lb = ledge + lr * math.cos(a) + 20
sx, sz = lb - THICKNESS / 2, THICKNESS / 2
strut_len = math.hypot(px - sx, pz - sz)
strut_ang = math.degrees(math.atan2(pz - sz, px - sx))

with BuildPart() as soporte:
    with BuildSketch(Plane.XZ):
        # Base
        Rectangle(lb, THICKNESS, align=(Align.MIN, Align.MIN))
        # Labio delantero
        Rectangle(THICKNESS, LIP_HEIGHT + THICKNESS, align=(Align.MIN, Align.MIN))
        # Respaldo inclinado
        with Locations(Location((ledge, 0), ANGLE)):
            Rectangle(lr, THICKNESS, align=(Align.MIN, Align.MAX))
        # Puntal trasero
        with Locations(Location((sx, sz), strut_ang)):
            Rectangle(strut_len, THICKNESS, align=(Align.MIN, Align.CENTER))
        # Recorta lo que quede por debajo de la cama
        Rectangle(1000, 1000, align=(Align.CENTER, Align.MIN), mode=Mode.INTERSECT)
    extrude(amount=WIDTH / 2, both=True)
    if CABLE_SLOT:
        with BuildSketch(Plane.YZ.offset(ledge / 2)):
            SlotCenterToCenter(1, 12, rotation=90)
        extrude(amount=ledge, both=True, mode=Mode.SUBTRACT)

# Tumbado de lado (perfil sobre la cama): se imprime sin soportes y las capas siguen el perfil
tumbado = Rot(90, 0, 0) * soporte.part
result = Pos(0, 0, -tumbado.bounding_box().min.Z) * tumbado
'''


def _knob(p: dict) -> str:
    return _header(p) + '''\
with BuildPart() as pomo:
    Cylinder(DIAMETER / 2, HEIGHT, align=(Align.CENTER, Align.CENTER, Align.MIN))
    fillet(pomo.edges().group_by(Axis.Z)[-1], min(2.0, HEIGHT / 4))
    # Moleteado: estrías cilíndricas alrededor
    with PolarLocations(DIAMETER / 2 + GRIP_DEPTH * 0.3, GRIP_COUNT):
        Cylinder(GRIP_DEPTH, HEIGHT, align=(Align.CENTER, Align.CENTER, Align.MIN), mode=Mode.SUBTRACT)
    # Eje en D (agujero ciego desde abajo)
    with BuildSketch():
        Circle(SHAFT_DIAMETER / 2 + SHAFT_CLEARANCE)
        with Locations((SHAFT_DIAMETER / 2 + SHAFT_CLEARANCE, 0)):
            Rectangle((SHAFT_DIAMETER - SHAFT_FLAT) * 2, SHAFT_DIAMETER * 2, mode=Mode.SUBTRACT)
    extrude(amount=SHAFT_DEPTH, mode=Mode.SUBTRACT)
    # Marca indicadora en la parte superior
    with BuildSketch(Plane.XY.offset(HEIGHT)):
        with Locations((0, DIAMETER / 4)):
            Rectangle(1.6, DIAMETER / 3)
    extrude(amount=-0.8, mode=Mode.SUBTRACT)

result = pomo.part
'''


def _tolerance_test(p: dict) -> str:
    return _header(p) + '''\
clear = [round(START + i * STEP, 3) for i in range(COUNT)]
pitch = PEG_DIAMETER + max(clear) + 6
with BuildPart() as placa:
    Box(pitch * COUNT, pitch + 8, PLATE_THICKNESS, align=(Align.MIN, Align.CENTER, Align.MIN))
    with BuildSketch(Plane.XY.offset(PLATE_THICKNESS)):
        for i, c in enumerate(clear):
            with Locations((pitch * (i + 0.5), 0)):
                Circle(PEG_DIAMETER / 2 + c)
    extrude(amount=-PLATE_THICKNESS, mode=Mode.SUBTRACT)
    # Etiquetas grabadas con la holgura (en centésimas de mm)
    with BuildSketch(Plane.XY.offset(PLATE_THICKNESS)):
        for i, c in enumerate(clear):
            with Locations((pitch * (i + 0.5), -(pitch / 2 + 1))):
                Text(f"{int(round(c * 100))}", 4, align=(Align.CENTER, Align.CENTER))
    extrude(amount=-0.6, mode=Mode.SUBTRACT)

with BuildPart() as clavija:
    Cylinder(PEG_DIAMETER / 2, PLATE_THICKNESS + 8, align=(Align.CENTER, Align.CENTER, Align.MIN))
    chamfer(clavija.edges().group_by(Axis.Z)[-1], 0.6)
    with BuildSketch():
        Circle(PEG_DIAMETER / 2 + 3)
    extrude(amount=2)

result = {"placa": placa.part, "clavija": Pos(-PEG_DIAMETER - 8, 0, 0) * clavija.part}
'''


TEMPLATES: dict[str, Template] = {
    t.name: t
    for t in [
        Template(
            "enclosure",
            "Caja de electrónica con tapa encajada (labio), columnas opcionales para insertos térmicos M3.",
            dict(width=80.0, depth=60.0, height=35.0, wall=2.0, corner_radius=5.0,
                 lid_thickness=2.0, lip_height=4.0, lip_wall=1.6, fit_clearance=0.2,
                 screw_posts=True, insert_hole=4.1, screw_clearance=3.4),
            _enclosure,
            "Caja boca arriba, tapa con el labio hacia arriba. PLA/PETG, 3 paredes, 15 % relleno, sin soportes.",
        ),
        Template(
            "l_bracket",
            "Escuadra en L con nervio de refuerzo y agujeros pasantes.",
            dict(width=30.0, leg_a=40.0, leg_b=40.0, thickness=5.0, inner_fillet=4.0,
                 hole_diameter=4.5, gusset=True, gusset_thickness=4.0),
            _l_bracket,
            "Para máxima resistencia imprímela de lado (perfil en L sobre la cama): las capas no quedan en la esquina. "
            "PETG/ASA, 4-5 paredes, 40 % gyroid.",
        ),
        Template(
            "spacer",
            "Separador / standoff redondo o hexagonal.",
            dict(outer_diameter=8.0, inner_diameter=3.4, height=10.0, hex=False),
            _spacer,
            "De pie. Usa 100 % relleno o 4 paredes para que sea macizo.",
        ),
        Template(
            "cable_clip",
            "Clip para cable a presión con dos orejetas para tornillo.",
            dict(cable_diameter=6.0, wall=2.0, clip_width=10.0, tab_length=8.0,
                 screw_hole=3.4, opening_ratio=0.75),
            _cable_clip,
            "Base plana sobre la cama. PETG o TPU para que el clip flexe sin romperse.",
        ),
        Template(
            "phone_stand",
            "Soporte de móvil/tablet con respaldo inclinado y ranura para el cable.",
            dict(angle=65.0, width=70.0, thickness=4.0, rest_length=90.0, lip_height=8.0, cable_slot=True),
            _phone_stand,
            "Ya sale tumbado de lado (perfil sobre la cama): sin soportes. PLA/PETG, 3 paredes, 15 % relleno.",
        ),
        Template(
            "knob",
            "Pomo moleteado para eje en D (potenciómetros, encoders).",
            dict(diameter=25.0, height=16.0, grip_count=18, grip_depth=1.2, shaft_diameter=6.0,
                 shaft_flat=4.5, shaft_clearance=0.1, shaft_depth=12.0),
            _knob,
            "Boca abajo no hace falta: imprímelo con la cara del eje sobre la cama. Ajusta shaft_clearance tras una prueba.",
        ),
        Template(
            "tolerance_test",
            "Pieza de calibración de holguras: placa con agujeros de holgura creciente + clavija.",
            dict(peg_diameter=8.0, start=0.05, step=0.05, count=8, plate_thickness=4.0),
            _tolerance_test,
            "Imprímela con el mismo filamento y perfil que la pieza final; la etiqueta indica la holgura en centésimas.",
        ),
    ]
}


def render_template(name: str, overrides: dict | None = None) -> tuple[str, Template]:
    if name not in TEMPLATES:
        raise ValueError(f"Plantilla desconocida '{name}'. Opciones: {', '.join(TEMPLATES)}")
    t = TEMPLATES[name]
    params = dict(t.defaults)
    for k, v in (overrides or {}).items():
        key = k.lower()
        if key not in params:
            raise ValueError(f"Parámetro '{k}' no existe en '{name}'. Válidos: {', '.join(params)}")
        default = params[key]
        if isinstance(default, bool):
            v = v if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes", "si", "sí")
        elif isinstance(default, int):
            v = int(v)
        elif isinstance(default, float):
            v = float(v)
        params[key] = v
    code = f'"""Plantilla: {t.name} — {t.description}"""\n' + t.builder(params)
    return code, t
