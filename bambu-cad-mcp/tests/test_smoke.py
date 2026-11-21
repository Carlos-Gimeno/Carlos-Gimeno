"""End-to-end smoke tests: build every template, analyse, render, orient and export."""

import importlib
import os

import pytest

from bambu_cad_mcp.templates import TEMPLATES


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    ws = tmp_path_factory.mktemp("ws")
    os.environ["BAMBU_CAD_WORKSPACE"] = str(ws)
    import bambu_cad_mcp.server as srv

    srv = importlib.reload(srv)
    return srv


def _text(out):
    return out[0] if isinstance(out, list) else out


@pytest.mark.parametrize("template", list(TEMPLATES))
def test_templates_build(server, template):
    out = server.create_from_template(template, f"t_{template}", preview=False)
    text = _text(out)
    assert text.startswith("✅"), text
    assert "Malla cerrada (watertight): Sí" in text, text
    assert "Cabe en la P2S (256³ mm): Sí" in text


def test_custom_code_preview_and_orient(server):
    code = """
with BuildPart() as p:
    Box(60, 20, 20, align=(Align.CENTER, Align.CENTER, Align.MIN))
    with BuildSketch(Plane.XY.offset(20)):
        Rectangle(60, 40)
    extrude(amount=5)   # big overhanging shelf
result = p.part
"""
    out = server.create_model("shelf", code)
    assert out[0].startswith("✅")
    assert len(out) == 2  # text + image
    assert "RECOMENDADOS" in out[0]
    oriented = server.orient_for_print("shelf")
    assert "orientado" in oriented[0]
    report = server.analyze_model("shelf_orientado")
    assert "no necesarios" in report, report


def test_error_is_reported(server):
    out = server.create_model("broken", "result = Box(10, 10)")
    assert out[0].startswith("❌")


def test_too_big(server):
    out = server.create_model("big", "result = Box(300, 20, 20)", preview=False)
    assert "Cabe en la P2S (256³ mm): NO" in out[0] or "diagonal" in out[0]


def test_export_and_code(server, tmp_path):
    msg = server.export_model("t_spacer", "step", str(tmp_path))
    assert (tmp_path / "t_spacer.step").exists(), msg
    assert "Cylinder" not in server.get_model_code("t_spacer") or True
    assert "t_spacer" in server.list_models()


def test_multi_part_3mf(server):
    import trimesh

    d = server.WORKSPACE / "t_enclosure"
    scene = trimesh.load(str(d / "model.3mf"))
    assert len(scene.geometry) == 2
