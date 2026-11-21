"""Cheat-sheet that teaches the LLM how to write build123d models for this server."""

BUILD123D_GUIDE = '''\
# Cómo escribir modelos (build123d, kernel OpenCascade B-rep)

El código se ejecuta con `from build123d import *` y `import math` ya hechos.
Unidades: milímetros y grados. Z es "arriba" (la cama de la impresora es el plano XY, Z=0).
**Asigna el resultado final a `result`**:
  - un sólido: `result = pieza.part`
  - varias piezas en la misma placa / multicolor AMS: `result = {"cuerpo": a.part, "tapa": Pos(60,0,0) * b.part}`

## Modo builder (recomendado)
```python
with BuildPart() as pieza:
    Box(60, 40, 10, align=(Align.CENTER, Align.CENTER, Align.MIN))   # apoyada en Z=0
    fillet(pieza.edges().filter_by(Axis.Z), radius=4)                  # aristas verticales
    with Locations((20, 0, 10)):                                       # sobre la cara superior
        Hole(radius=1.7)                                               # M3 pasante
    with BuildSketch(pieza.faces().sort_by(Axis.Z)[-1]):              # boceto en la cara de arriba
        SlotCenterToCenter(20, 6)
    extrude(amount=-4, mode=Mode.SUBTRACT)                              # bolsillo de 4 mm
    chamfer(pieza.edges().group_by(Axis.Z)[0], 0.5)                     # chaflán inferior
result = pieza.part
```

## Primitivas 3D
Box(l, w, h) · Cylinder(radius, height) · Sphere(radius) · Cone(bottom_r, top_r, h)
Torus(major_r, minor_r) · Wedge(...) · Hole(radius, depth=None) · CounterBoreHole(r, cb_r, cb_depth)
CounterSinkHole(r, cs_r) · `align=(Align.CENTER, Align.CENTER, Align.MIN)` para apoyar en la cama.

## Bocetos 2D (dentro de BuildSketch) → extrude / revolve / loft / sweep
Rectangle(w, h) · RectangleRounded(w, h, r) · Circle(r) · Ellipse(rx, ry) · RegularPolygon(r, n)
Polygon(*pts) · SlotCenterToCenter(dist, h) · SlotOverall(w, h) · Trapezoid(w, h, angle)
Text("Hola", font_size=8, font="Arial") · offset(amount=1) · fillet(sketch.vertices(), r)
Líneas: `with BuildLine(): Polyline(p1, p2, ..., close=True)` → `make_face()`;
también Line, ThreePointArc, TangentArc, Spline, JernArc, CenterArc.

Operaciones: extrude(amount=h, both=False, taper=0) · revolve(axis=Axis.Z, revolution_arc=360)
loft() (entre varios bocetos en planos distintos) · sweep(path=...) · mirror(about=Plane.YZ)
offset(amount=-2, openings=cara)  ← vaciar (shell) dejando la cara abierta
split(bisect_by=Plane.XY) · section() · project()

## Modos
`mode=Mode.ADD` (por defecto) · `Mode.SUBTRACT` (cortar) · `Mode.INTERSECT` · `Mode.PRIVATE` (no se añade)

## Posicionar
`with Locations((x, y, z), ...)` · `with GridLocations(dx, dy, nx, ny)` · `with PolarLocations(r, n)`
`with HexLocations(...)` · Planos: Plane.XY, Plane.XZ, Plane.YZ, `Plane.XY.offset(z)`, `Plane(cara)`
Fuera de builders: `Pos(x, y, z) * forma`, `Rot(0, 0, 45) * forma`, `forma.rotate(Axis.X, 90)`,
`forma.moved(Location(...))`, booleanas con `a + b`, `a - b`, `a & b`.

## Seleccionar caras/aristas
`pieza.faces().sort_by(Axis.Z)[-1]` (cara superior) · `.edges().filter_by(Axis.Z)` (aristas verticales)
`.edges().group_by(Axis.Z)[-1]` (aristas de arriba) · `.filter_by(GeomType.CIRCLE)`
`.sort_by_distance((x, y, z))[0]` · `pieza.edges(Select.LAST)` (aristas de la última operación)
Operadores cortos: `pieza.faces() > Axis.Z` (= sort_by), `pieza.edges() | Axis.Z` (= filter_by)

## Roscas y piezas comunes
- Para tornillos mejor insertos térmicos o tuercas cautivas (hexágono `RegularPolygon(5.7/2/cos(30°), 6)`).
- Agujero horizontal > 6 mm: forma de lágrima (círculo + triángulo a 45° en la parte superior).
- Ensamblajes "print-in-place": deja >= 0.35 mm de holgura entre piezas móviles.

## Errores frecuentes
- "Failed creating a fillet": radio demasiado grande, o filete después de booleanas complejas.
  Haz los filetes antes de restar detalles, usa radios más pequeños o `chamfer`.
- Sólidos que se tocan solo en una arista o son tangentes → malla no-manifold. Solápalos >= 0.1 mm.
- Un BuildSketch sin extrude no es un sólido: `result` debe tener volumen.
- Texto: requiere fuentes del sistema; si falla, usa font="Arial" o elimina el texto.
'''

WORKFLOW = '''\
# Flujo de trabajo recomendado
1. Aclara medidas reales (calibre), función, material y si hay piezas que encajen.
2. `design_guide` si necesitas reglas (tolerancias, tornillería, voladizos).
3. ¿Hay una plantilla? → `create_from_template`. Si no → `create_model` con código build123d paramétrico
   (parámetros en MAYÚSCULAS al principio del script).
4. Revisa la imagen de vista previa y el informe: tamaño, voladizos (rojo), pared mínima, si cabe en la P2S.
5. Itera: `get_model_code` → modifica → `create_model` con el mismo nombre (se guarda historial).
6. `orient_for_print` para minimizar soportes y maximizar contacto con la cama.
7. `export_model` (3MF para Bambu Studio, STEP para CAD) / `open_in_slicer` / `slice_model` / `send_to_printer`.
'''
