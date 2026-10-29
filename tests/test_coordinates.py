import math

import ifcopenshell
import ifcopenshell.api
import ifcopenshell.util.placement
import numpy as np
import pytest

from conftest import build_minimal_model
from ifc_normalizer import coordinates, validate

RULES = {
    "versao": "teste",
    "tolerancia_posicao_m": 0.01,
    "tolerancia_rotacao_graus": 0.01,
    "eixos_padrao": ["A", "1"],
    "disciplinas_referencia": ["AQ"],
    "predios": [{"id": "P1", "arquivos": "P1-*"}],
    "pilares": {"classe": ["IfcColumn"], "nome": {"contem": "PILAR"}},
}


def _matrix(x, y, z, deg):
    a = math.radians(deg)
    m = np.eye(4)
    m[:2, :2] = [[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]]
    m[:3, 3] = [x, y, z]
    return m


def _model(path, site_xyz_m, site_deg, with_grid=True):
    """Prédio com eixo A (vertical) e eixo 1 (horizontal) cruzando na origem do IfcSite.

    Unidades do modelo sintético em mm; um pilar 'PILAR P1' no cruzamento A x 1 e outro a 6 m.
    """
    f = build_minimal_model()
    site = f.by_type("IfcSite")[0]
    x, y, z = (v * 1000 for v in site_xyz_m)
    ifcopenshell.api.run("geometry.edit_object_placement", f, product=site, matrix=_matrix(x, y, z, site_deg), is_si=False)
    storey = f.by_type("IfcBuildingStorey")[0]
    ifcopenshell.api.run("geometry.edit_object_placement", f, product=storey, matrix=_matrix(x, y, z, site_deg), is_si=False)
    columns = []
    for name, dx in (("PILAR P1", 0.0), ("PILAR P2", 6000.0)):
        c = ifcopenshell.api.run("root.create_entity", f, ifc_class="IfcColumn", name=name)
        ifcopenshell.api.run("spatial.assign_container", f, relating_structure=storey, products=[c])
        local = _matrix(x, y, z, site_deg) @ _matrix(dx, 0, 0, 0)
        ifcopenshell.api.run("geometry.edit_object_placement", f, product=c, matrix=local, is_si=False)
        columns.append(c.GlobalId)
    if with_grid:
        def axis(tag, p0, p1):
            line = f.create_entity("IfcPolyline", [f.create_entity("IfcCartesianPoint", p0),
                                                   f.create_entity("IfcCartesianPoint", p1)])
            return f.create_entity("IfcGridAxis", AxisTag=tag, AxisCurve=line, SameSense=True)
        grid = ifcopenshell.api.run("root.create_entity", f, ifc_class="IfcGrid", name="Grade")
        grid.UAxes = [axis("1", (-2000.0, 0.0), (30000.0, 0.0)), axis("2", (-2000.0, 6000.0), (30000.0, 6000.0))]
        grid.VAxes = [axis("A", (0.0, -2000.0), (0.0, 20000.0)), axis("B", (6000.0, -2000.0), (6000.0, 20000.0))]
        ifcopenshell.api.run("geometry.edit_object_placement", f, product=grid,
                             matrix=_matrix(x, y, z, site_deg), is_si=False)
        ifcopenshell.api.run("spatial.assign_container", f, relating_structure=storey, products=[grid])
    f.write(str(path))
    return columns


@pytest.fixture
def building(tmp_path):
    ref = tmp_path / "P1-AQ-ARQ.ifc"
    shifted = tmp_path / "P1-ST4-EST.ifc"
    tekla = tmp_path / "P1-ST3-MET.ifc"
    _model(ref, (1000.0, 2000.0, 700.0), 30.0)
    cols = _model(shifted, (1012.5, 1995.0, 700.0), 32.0)  # 12,5 m / -5 m e 2° de diferença
    _model(tekla, (1000.0, 2000.0, 700.0), 30.0, with_grid=False)  # sem grade, alinhado
    return ref, shifted, tekla, cols


def _by_name(results):
    return {r["arquivo"]: r for r in results}


def test_detects_reference_and_divergence(building):
    ref, shifted, tekla, _ = building
    res = _by_name(coordinates.analyze_paths([ref, shifted, tekla], RULES))

    a = res[ref.name]
    assert a["metodo"] == "grade" and a["situacao"] == "referência" and a["zero_zero"] == "sim"
    assert (a["x"], a["y"]) == pytest.approx((1000.0, 2000.0))

    b = res[shifted.name]
    assert b["situacao"] == "divergente"
    assert (b["dx"], b["dy"]) == pytest.approx((12.5, -5.0))
    assert b["drot"] == pytest.approx(2.0)
    assert coordinates.suggested_rotation(b) == pytest.approx(-2.0)

    t = res[tekla.name]
    assert t["metodo"] == "pilar de canto" and t["interpretacao"]
    assert t["situacao"] == "coincide com a referência"
    assert t["pilar_mais_proximo_m"] == pytest.approx(0.0, abs=1e-6)


def test_missing_axes_are_reported_not_guessed(building, tmp_path):
    ref, *_ = building
    rules = {**RULES, "predios": [{"id": "P1", "arquivos": "P1-*", "eixos": ["F", "7"]}]}
    r = coordinates.analyze_paths([ref], rules)[0]
    assert r["x"] is None and r["situacao"] == "ponto de referência não localizado"
    assert "A, B, 1, 2" in r["eixos_disponiveis"] or set(r["eixos_disponiveis"].split(", ")) == {"1", "2", "A", "B"}


def test_correction_aligns_model_and_preserves_relative_geometry(building, tmp_path):
    ref, shifted, _, cols = building
    res = _by_name(coordinates.analyze_paths([ref, shifted], RULES))
    b = res[shifted.name]

    before = ifcopenshell.open(str(shifted))
    scale = 0.001
    p_before = [ifcopenshell.util.placement.get_local_placement(before.by_guid(g).ObjectPlacement)[:3, 3] * scale
                for g in cols]

    out = tmp_path / "corrigido" / shifted.name
    c = coordinates.correct_file(shifted, out, b, b["alvo"], coordinates.suggested_rotation(b), RULES)
    assert c["ok"] and c["rotacao_conferida"]
    assert c["residuo_m"] == pytest.approx(0.0, abs=1e-6)

    after = coordinates.analyze_paths([ref, out], RULES)
    fixed = _by_name(after)[out.name]
    assert fixed["situacao"] == "coincide com a referência"

    g = ifcopenshell.open(str(out))
    p_after = [ifcopenshell.util.placement.get_local_placement(g.by_guid(gid).ObjectPlacement)[:3, 3] * scale
               for gid in cols]  # GlobalId preservado
    # movimento rígido: distância entre os pilares não muda
    assert np.linalg.norm(p_after[1] - p_after[0]) == pytest.approx(np.linalg.norm(p_before[1] - p_before[0]))
    assert p_after[0][:2] == pytest.approx([1000.0, 2000.0], abs=1e-6)
    assert validate.validate_schema(g) == []
    assert any("Coordenadas" in d for d in g.header.file_description.description)


def test_source_file_untouched(building, tmp_path):
    ref, shifted, _, _ = building
    before = shifted.read_bytes()
    res = _by_name(coordinates.analyze_paths([ref, shifted], RULES))
    coordinates.correct_divergent(list(res.values()), tmp_path / "saida", RULES)
    assert shifted.read_bytes() == before
    assert (tmp_path / "saida" / shifted.name).exists()


def test_project_rules_file_is_valid():
    rules = coordinates.load_rules()
    assert rules["predios"] and rules["eixos_padrao"] == ["A", "1"]
    assert coordinates.building_for("ZTO-13-04-AQ-OFICINA.ifc", rules)["eixos"] == ["F", "7"]
    assert coordinates.building_for("RVT_B0310-F3_FEL3_0610_A_VESTIARIO.ifc", rules)["id"] == "0610"
