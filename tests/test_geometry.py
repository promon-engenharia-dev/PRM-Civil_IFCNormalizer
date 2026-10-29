import copy
import json
import math

import ifcopenshell.api
import numpy as np
import pytest

from conftest import build_minimal_model
from ifc_normalizer import normalize, rules
from ifc_normalizer.geometry import GeometrySettings, classify_points

CFG = GeometrySettings()


def box(dx, dy, dz, rot_y_deg=0.0):
    """Vértices de uma caixa centrada na origem, girada em torno de Y."""
    pts = np.array([[x, y, z] for x in (0, dx) for y in (0, dy) for z in (0, dz)], dtype=float)
    pts -= pts.mean(axis=0)
    a = math.radians(rot_y_deg)
    rot = np.array([[math.cos(a), 0, math.sin(a)], [0, 1, 0], [-math.sin(a), 0, math.cos(a)]])
    return pts @ rot.T


@pytest.mark.parametrize("pts, esperado", [
    (box(0.3, 0.3, 4.0), "vertical"),
    (box(6.0, 0.2, 0.4), "horizontal"),
    (box(0.2, 0.2, 5.0, rot_y_deg=45), "inclinado"),
    (box(0.2, 0.2, 5.0, rot_y_deg=5), "vertical"),        # dentro da tolerância de 10°
    (box(4.0, 3.0, 0.2), "plano_horizontal"),
    (box(4.0, 0.2, 3.0), "plano_vertical"),
    (box(1.0, 1.0, 1.0), "compacto"),
])
def test_classify_points(pts, esperado):
    assert classify_points(pts, CFG)["orientacao"] == esperado


def test_dimensions_reported():
    r = classify_points(box(6.0, 0.2, 0.4), CFG)
    assert r["comprimento_m"] == pytest.approx(6.0)
    assert r["angulo_vertical_graus"] == pytest.approx(90.0)


RULES = {
    "versao": "teste",
    "geometria": {"tolerancia_angular_graus": 10},
    "pendencias": {"classes_genericas": ["IfcBuildingElementProxy"], "exigir_predefined_type": True},
    "regras": [
        {"id": "R-GEO-V", "status": "proposta",
         "quando": {"classe": ["IfcBeam", "IfcColumn"], "geometria": "vertical"},
         "entao": {"classe": "IfcColumn", "predefined_type": "COLUMN"}},
        {"id": "R-NOME", "status": "proposta",
         "quando": {"nome": "NUT"},
         "entao": {"classe": "IfcMechanicalFastener", "predefined_type": "USERDEFINED", "object_type": "PORCA"}},
    ],
}


def _ruleset(tmp_path, data=RULES):
    p = tmp_path / "r.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return rules.load_normalization_rules(p)


def _model_with_beams(names_and_sizes):
    f = build_minimal_model()
    storey = f.by_type("IfcBuildingStorey")[0]
    body = next(c for c in f.by_type("IfcGeometricRepresentationSubContext") if c.ContextIdentifier == "Body")
    guids = []
    for name, (length, thickness, height) in names_and_sizes:
        el = ifcopenshell.api.run("root.create_entity", f, ifc_class="IfcBeam", name=name)
        ifcopenshell.api.run("spatial.assign_container", f, relating_structure=storey, products=[el])
        rep = ifcopenshell.api.run("geometry.add_wall_representation", f, context=body,
                                   length=length, height=height, thickness=thickness)
        ifcopenshell.api.run("geometry.assign_representation", f, product=el, representation=rep)
        guids.append(el.GlobalId)
    return f, guids


def test_geometric_rule_reclassifies_and_is_reviewed(tmp_path):
    # unidades do modelo sintético em mm
    f, (vert, horiz) = _model_with_beams([("VIGA", (300, 300, 4000)), ("VIGA", (6000, 200, 400))])
    result = normalize.normalize(f, _ruleset(tmp_path), "ZTO-00-ST3-TESTE.ifc", include_proposed=True)

    assert f.by_guid(vert).is_a() == "IfcColumn" and f.by_guid(vert).PredefinedType == "COLUMN"
    assert f.by_guid(horiz).is_a() == "IfcBeam"
    review = {r["global_id"]: r for r in result.geometry_review}
    assert review[vert]["orientacao"] == "vertical" and review[vert]["situacao"] == "aplicada"
    assert review[horiz]["orientacao"] == "horizontal"
    assert review[horiz]["situacao"] == "nenhuma regra geométrica casou"


def test_geometric_rules_need_proposed_flag(tmp_path):
    f, (vert,) = _model_with_beams([("VIGA", (300, 300, 4000))])
    result = normalize.normalize(f, _ruleset(tmp_path), "TESTE.ifc")
    assert f.by_guid(vert).is_a() == "IfcBeam"
    assert result.geometry_review == []  # sem regra geométrica ativa, nem calcula


def test_attribute_rule_takes_precedence_without_conflict(tmp_path):
    # Regra geométrica escrita ANTES no arquivo: a precedência é por nível, não por ordem.
    f, (nut,) = _model_with_beams([("NUT", (100, 100, 1000))])
    result = normalize.normalize(f, _ruleset(tmp_path), "TESTE.ifc", include_proposed=True)
    assert f.by_guid(nut).is_a() == "IfcMechanicalFastener"
    assert not [p for p in result.pending if p["motivo"] == normalize.CONFLICT]
    # O modelo também tem um pilar; a linha é localizada pelo GlobalId (a ordem segue o GlobalId).
    row = next(r for r in result.geometry_review if r["global_id"] == nut)
    assert row["situacao"] == "sobreposta por R-NOME"


def test_rules_validation_geometry():
    bad = copy.deepcopy(RULES)
    bad["regras"][0]["quando"]["geometria"] = "diagonal"
    bad["geometria"]["tolerancia"] = 5
    problems, warnings = rules.validate(bad)
    text = "\n".join(problems)
    assert "orientação ['diagonal'] inválida" in text
    assert "parâmetro desconhecido 'tolerancia'" in text

    approved = copy.deepcopy(RULES)
    approved["regras"][0]["status"] = "aprovada"
    _, warnings = rules.validate(approved)
    assert any("regra geométrica aprovada" in w for w in warnings)


def test_geometry_only_for_candidates_of_other_conditions(tmp_path):
    """Regra geométrica do ST4 não triangula peças de outra disciplina (ex.: drenagem IH)."""
    data = copy.deepcopy(RULES)
    data["regras"][0]["quando"]["disciplina"] = "ST4"
    rs = _ruleset(tmp_path, data)
    f, _ = _model_with_beams([("VIGA", (300, 300, 4000))])
    result = normalize.normalize(f, rs, "ZTO-13-02-IH-DRENAGEM.ifc", include_proposed=True)
    assert result.geometry_review == []

    g, (vert,) = _model_with_beams([("VIGA", (300, 300, 4000))])
    result = normalize.normalize(g, rs, "ZTO-13-04-ST4-TESTE.ifc", include_proposed=True)
    assert vert in {r["global_id"] for r in result.geometry_review}


def test_without_geometry_strips_nested_conditions():
    cond = {"classe": "IfcBeam", "geometria": "vertical", "ou": [{"geometria": "horizontal"}, {"nome": "X"}]}
    assert normalize._without_geometry(cond) == {"classe": "IfcBeam"}
    cond = {"ou": [{"nome": "A", "geometria": "vertical"}, {"nome": "B"}]}
    assert normalize._without_geometry(cond) == {"ou": [{"nome": "A"}, {"nome": "B"}]}
