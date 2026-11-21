# Ejemplo de código para create_model: soporte de pared para un mando a distancia.
# Copia este contenido en el parámetro `code` (build123d ya está importado).
ANCHO, FONDO, ALTO, ESPESOR = 50, 30, 40, 3
TORNILLO = 4.5  # paso para tornillo de 4 mm

with BuildPart() as soporte:
    # Placa trasera (va contra la pared)
    Box(ANCHO, ESPESOR, ALTO, align=(Align.CENTER, Align.MIN, Align.MIN))
    # Bandeja
    with BuildSketch():
        with Locations((0, ESPESOR + FONDO / 2)):
            RectangleRounded(ANCHO, FONDO, 4)
    extrude(amount=ESPESOR)
    # Labio frontal
    with BuildSketch(Plane.XZ.offset(-FONDO)):
        with Locations((0, 6)):
            Rectangle(ANCHO, 12)
    extrude(amount=ESPESOR)
    # Agujeros pasantes para atornillar a la pared
    with BuildSketch(Plane.XZ):
        with Locations((-15, 30), (15, 30)):
            Circle(TORNILLO / 2)
    extrude(amount=-ESPESOR, mode=Mode.SUBTRACT)

result = soporte.part
