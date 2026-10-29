"""Estrutura espacial e nomes (rules/estrutura.json)."""

import json

import ifcopenshell
import ifcopenshell.api
import ifcopenshell.util.element
import ifcopenshell.util.placement
import numpy as np
import pytest

from conftest import build_minimal_model
from ifc_normalizer import spatial

CIVIL3D = {"origem": "Civil3D", "disciplina": "IH"}
RULES = {
    "projeto": {"nome": "", "nome_longo": ""},
    "site": {"nome": "", "nome_longo": ""},
    "predios": [{"id": "0601", "arquivos": "*_0601_*", "nome": "", "nome_longo": ""}],
    "infraestrutura": [
        {"id": "EST-IH-001", "status": "aprovada", "quando": {"origem": "Civil3D"}, "acao": "building_para_site"},
        {"id": "EST-IH-002", "status": "aprovada", "quando": {"origem": "Civil3D", "arquivo": {"contem": "PAVIMENTACAO"}},
         "acao": "instalacao", "classe": "IfcRoad", "predefined_type": "NOTDEFINED", "nome": "Pavimentação"},
    ],
}


def civil3d_model() -> ifcopenshell.file:
    """Como o Civil 3D exporta: IfcProject -> IfcBuilding (sem site nem pavimentos) com proxies."""
    f = ifcopenshell.api.run("project.create_file", version="IFC4X3_ADD2")
    project = ifcopenshell.api.run("root.create_entity", f, ifc_class="IfcProject", name="ZTO-IH")
    ifcopenshell.api.run("unit.assign_unit", f)
    building = ifcopenshell.api.run("root.create_entity", f, ifc_class="IfcBuilding", name="ZTO-IH")
    building.ElevationOfRefHeight = 12.5
    ifcopenshell.api.run("aggregate.assign_object", f, relating_object=project, products=[building])
    ifcopenshell.api.run("geometry.edit_object_placement", f, product=building,
                         matrix=np.array([[1, 0, 0, 500.0], [0, 1, 0, 800.0], [0, 0, 1, 0], [0, 0, 0, 1]]))
    for name in ("Pave1", "Base"):
        proxy = ifcopenshell.api.run("root.create_entity", f, ifc_class="IfcBuildingElementProxy", name=name)
        ifcopenshell.api.run("spatial.assign_container", f, relating_structure=building, products=[proxy])
        ifcopenshell.api.run("geometry.edit_object_placement", f, product=proxy,
                             matrix=np.array([[1, 0, 0, 510.0], [0, 1, 0, 820.0], [0, 0, 1, 1], [0, 0, 0, 1]]))
    for i, product in enumerate(f.by_type("IfcRoot")):  # GlobalIds fixos, como num arquivo real
        product.GlobalId = spatial.stable_guid("fixture", str(i))
    return f


def _world(element) -> np.ndarray:
    return ifcopenshell.util.placement.get_local_placement(element.ObjectPlacement)


def test_building_becomes_site_keeping_guid_placement_and_contents():
    f = civil3d_model()
    building = f.by_type("IfcBuilding")[0]
    guid, before = building.GlobalId, {p.GlobalId: _world(p) for p in f.by_type("IfcBuildingElementProxy")}

    changes, pending = spatial.restructure(f, RULES, {**CIVIL3D, "arquivo": "ZTO-13-02-IH-TERRAPLENAGEM.ifc"})

    assert pending == [] and not f.by_type("IfcBuilding")
    site = f.by_guid(guid)
    assert site.is_a("IfcSite") and ifcopenshell.util.element.get_aggregate(site).is_a("IfcProject")
    for proxy in f.by_type("IfcBuildingElementProxy"):
        assert ifcopenshell.util.element.get_container(proxy) == site
        assert np.allclose(_world(proxy), before[proxy.GlobalId])  # coordenadas preservadas
    assert ("classe", "IfcBuilding", "IfcSite") in {(c["campo"], c["antes"], c["depois"]) for c in changes}
    assert any(c["campo"] == "ElevationOfRefHeight" and c["antes"] == 12.5 for c in changes)  # descarte registrado


def test_pavement_goes_into_road_deterministically_and_only_once():
    results = []
    for _ in range(2):
        f = civil3d_model()
        context = {**CIVIL3D, "arquivo": "ZTO-13-02-IH-PAVIMENTACAO.ifc"}
        before = {p.GlobalId: _world(p) for p in f.by_type("IfcBuildingElementProxy")}
        changes, _ = spatial.restructure(f, RULES, context)
        road = f.by_type("IfcRoad")[0]
        assert road.Name == "Pavimentação" and road.PredefinedType == "NOTDEFINED"
        assert ifcopenshell.util.element.get_aggregate(road).is_a("IfcSite")
        for proxy in f.by_type("IfcBuildingElementProxy"):
            assert ifcopenshell.util.element.get_container(proxy) == road
            assert np.allclose(_world(proxy), before[proxy.GlobalId])
        assert sum(c["campo"] == "Estrutura espacial" for c in changes) == 2
        again, _ = spatial.restructure(f, RULES, context)  # arquivo já normalizado: nada muda
        assert again == [] and len(f.by_type("IfcRoad")) == 1
        results.append(road.GlobalId)
    assert results[0] == results[1]


def test_unexpected_structure_is_reported_not_forced():
    f = build_minimal_model()  # já tem site, prédio e pavimento
    building = f.by_type("IfcBuilding")[0]
    rules = {**RULES, "infraestrutura": [{**RULES["infraestrutura"][0]}]}
    f.by_type("IfcSite")[0].Name = "Site"
    f.remove(f.by_type("IfcSite")[0])  # sem site, mas com pavimento: não é o padrão do Civil 3D
    changes, pending = spatial.restructure(f, rules, {**CIVIL3D, "arquivo": "x.ifc"})
    assert changes == [] and building.is_a("IfcBuilding")
    assert pending and pending[0]["motivo"] == spatial.UNRECOGNIZED


def test_rules_only_apply_to_their_origin():
    f = civil3d_model()
    changes, pending = spatial.restructure(f, RULES, {"origem": "Revit", "disciplina": "AQ", "arquivo": "a.ifc"})
    assert changes == pending == [] and f.by_type("IfcBuilding")


def test_names_from_rules_with_screen_overrides():
    f = build_minimal_model()
    rules = spatial.with_overrides(RULES, {
        "projeto": {"nome": "B0310", "nome_longo": "Projeto B0310"},
        "site": {"nome": "ZTO"},
        "predios": {"0601": {"nome": "0601", "nome_longo": "Administrativo"}, "9999": {"nome": "ignorado"}},
    })
    assert RULES["projeto"]["nome"] == ""  # cópia: as regras originais não mudam
    changes, pending = spatial.restructure(f, rules, {"origem": "Revit", "arquivo": "RVT_B0310_0601_A_ADM.ifc"})
    assert pending == []
    assert f.by_type("IfcProject")[0].Name == "B0310" and f.by_type("IfcProject")[0].LongName == "Projeto B0310"
    assert f.by_type("IfcSite")[0].Name == "ZTO" and f.by_type("IfcSite")[0].LongName is None  # vazio = manter
    building = f.by_type("IfcBuilding")[0]
    assert (building.Name, building.LongName) == ("0601", "Administrativo")
    assert {c["regra"] for c in changes} == {"EST-NOMES"}
    assert len(changes) == 5


def test_building_name_needs_matching_file_pattern():
    f = build_minimal_model()
    rules = spatial.with_overrides(RULES, {"predios": {"0601": {"nome": "0601"}}})
    spatial.restructure(f, rules, {"origem": "Revit", "arquivo": "RVT_B0310_0313_A_APOIO.ifc"})
    assert f.by_type("IfcBuilding")[0].Name == "Edificio"


def test_rules_file_and_validation(tmp_path):
    assert spatial.load_rules()["infraestrutura"]  # rules/estrutura.json válido
    bad = {**RULES, "infraestrutura": [{**RULES["infraestrutura"][1], "classe": "IfcWall"},
                                       {**RULES["infraestrutura"][0], "acao": "inventada"}]}
    problems = spatial.validate_rules(bad)
    assert any("IfcWall" in p for p in problems) and any("acao" in p for p in problems)


def test_save_names_keeps_the_rest(tmp_path):
    path = tmp_path / "estrutura.json"
    path.write_text(json.dumps(RULES), encoding="utf-8")
    spatial.save_names(path, spatial.with_overrides(RULES, {"site": {"nome": "ZTO"}, "predios": {"0601": {"nome": "0601"}}}))
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["site"]["nome"] == "ZTO" and saved["predios"][0]["nome"] == "0601"
    assert saved["infraestrutura"] == RULES["infraestrutura"]


@pytest.mark.parametrize("incluir", [False, True])
def test_proposed_structure_rules_only_in_simulation(incluir):
    f = civil3d_model()
    rules = {**RULES, "infraestrutura": [{**RULES["infraestrutura"][0], "status": "proposta"}]}
    spatial.restructure(f, rules, {**CIVIL3D, "arquivo": "t.ifc"}, include_proposed=incluir)
    assert bool(f.by_type("IfcSite")) is incluir
