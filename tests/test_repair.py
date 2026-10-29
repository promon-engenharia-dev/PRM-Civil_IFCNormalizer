"""Verificação e correção (rules/verificacao.json) com fixtures sintéticas."""

import ifcopenshell
import ifcopenshell.api
import ifcopenshell.guid
import ifcopenshell.util.element
import pytest

from conftest import build_minimal_model
from ifc_normalizer import repair, validate


@pytest.fixture(scope="module")
def rules():
    return repair.load_rules()


def _run(f, rules, only=None):
    data = {**rules, "correcoes": [c for c in rules["correcoes"] if only is None or c["id"] in only]}
    return repair.repair(f, data)


def _express_errors(f, rule_name):
    return [e for e in validate.validate_schema(f, express_rules=True) if rule_name in e["atributo"]]


def _storey(f):
    return f.by_type("IfcBuildingStorey")[0]


def _element(f, cls, name="X", **attrs):
    el = ifcopenshell.api.run("root.create_entity", f, ifc_class=cls, name=name)
    for k, v in attrs.items():
        setattr(el, k, v)
    ifcopenshell.api.run("spatial.assign_container", f, relating_structure=_storey(f), products=[el])
    return el


def test_rules_file_is_valid(rules):
    assert {c["id"] for c in rules["correcoes"]} <= set(repair.FIXES)


def test_generic_type_product_becomes_specific_type(rules):
    f = build_minimal_model()
    stair = _element(f, "IfcStair", "Escada", PredefinedType="STRAIGHT_RUN_STAIR")
    generic = f.create_entity("IfcTypeProduct", GlobalId=repair.stable_guid("t"), Name="Escada montada")
    f.create_entity("IfcRelDefinesByType", GlobalId=repair.stable_guid("r"), RelatedObjects=[stair], RelatingType=generic)
    assert _express_errors(f, "CorrectTypeAssigned")

    res = _run(f, rules, {"SCH-TIPO"})
    t = ifcopenshell.util.element.get_type(stair)
    assert t.is_a() == "IfcStairType" and t.GlobalId == repair.stable_guid("t") and t.Name == "Escada montada"
    assert t.PredefinedType == "STRAIGHT_RUN_STAIR"
    assert res.counts["SCH-TIPO"]["corrigidos"] == 1
    assert not _express_errors(f, "CorrectTypeAssigned")


def test_grid_axis_flattened_only_when_z_is_zero(rules):
    f = build_minimal_model()

    def axis(tag, z):
        pts = [f.create_entity("IfcCartesianPoint", (0.0, 0.0, z)), f.create_entity("IfcCartesianPoint", (10.0, 0.0, z))]
        return f.create_entity("IfcGridAxis", AxisTag=tag, AxisCurve=f.create_entity("IfcPolyline", pts), SameSense=True)

    a, b = axis("A", 0.0), axis("B", 3.0)
    f.create_entity("IfcGrid", GlobalId=repair.stable_guid("g"), UAxes=[a, b], VAxes=[axis("1", 0.0)])
    res = _run(f, rules, {"SCH-EIXO2D"})
    assert a.AxisCurve.Dim == 2 and [p.Coordinates for p in a.AxisCurve.Points] == [(0.0, 0.0), (10.0, 0.0)]
    assert b.AxisCurve.Dim == 3  # fora do plano: não muda
    assert res.counts["SCH-EIXO2D"] == {"encontrados": 3, "corrigidos": 2, "pendentes": 1}


def test_representation_type_label_matches_items(rules):
    f = build_minimal_model()
    col = f.by_type("IfcColumn")[0]
    rep = col.Representation.Representations[0]
    solid = rep.Items[0]
    half = f.create_entity("IfcHalfSpaceSolid", f.create_entity("IfcPlane", f.create_entity(
        "IfcAxis2Placement3D", f.create_entity("IfcCartesianPoint", (0.0, 0.0, 1.0)))), False)
    rep.Items = [f.create_entity("IfcBooleanClippingResult", "DIFFERENCE", solid, half)]
    assert rep.RepresentationType == "SweptSolid" and _express_errors(f, "CorrectItemsForType")

    _run(f, rules, {"SCH-REPTIPO"})
    assert rep.RepresentationType == "Clipping"
    assert not _express_errors(f, "CorrectItemsForType")


def test_duplicate_properties(rules):
    f = build_minimal_model()
    col = f.by_type("IfcColumn")[0]
    pset = ifcopenshell.api.run("pset.add_pset", f, product=col, name="PROMON-PRJ")
    empty = f.create_entity("IfcPropertySingleValue", "PRM-AVANCO", None, f.create_entity("IfcLabel", ""), None)
    none = f.create_entity("IfcPropertySingleValue", "PRM-AVANCO", None, None, None)
    a = f.create_entity("IfcPropertySingleValue", "FASE", None, f.create_entity("IfcLabel", "1"), None)
    b = f.create_entity("IfcPropertySingleValue", "FASE", None, f.create_entity("IfcLabel", "2"), None)
    pset.HasProperties = [empty, none, a, b]

    res = _run(f, rules, {"SCH-PSETDUP"})
    names = [p.Name for p in pset.HasProperties]
    assert names.count("PRM-AVANCO") == 1 and names.count("FASE") == 2  # valores diferentes: não escolhe
    assert res.counts["SCH-PSETDUP"] == {"encontrados": 2, "corrigidos": 1, "pendentes": 1}


def test_non_positive_ratio_and_styled_layer(rules):
    f = build_minimal_model()
    col = f.by_type("IfcColumn")[0]
    pset = ifcopenshell.api.run("pset.add_pset", f, product=col, name="Dados")
    prop = f.create_entity("IfcPropertySingleValue", "Taxa", None, f.create_entity("IfcPositiveRatioMeasure", 0.0), None)
    pset.HasProperties = [prop]
    rep = col.Representation.Representations[0]
    styled = f.create_entity("IfcPresentationLayerWithStyle", Name="C-TOPO", AssignedItems=[rep, rep.Items[0]],
                             LayerOn=True, LayerFrozen=False, LayerBlocked=False, LayerStyles=[])
    assert _express_errors(f, "ApplicableOnlyToItems")

    _run(f, rules, {"SCH-RAZAO", "SCH-LAYER"})
    assert prop.NominalValue.is_a("IfcRatioMeasure") and prop.NominalValue.wrappedValue == 0.0
    assert list(styled.AssignedItems) == [rep.Items[0]]
    plain = [la for la in f.by_type("IfcPresentationLayerAssignment", False) if la.Name == "C-TOPO"]
    assert plain and rep in plain[0].AssignedItems
    assert not _express_errors(f, "ApplicableOnlyToItems") and not _express_errors(f, "IfcPositiveRatioMeasure")


def test_userdefined_without_text(rules):
    f = build_minimal_model()
    wt = ifcopenshell.api.run("root.create_entity", f, ifc_class="IfcWallType", name="Parede", predefined_type="USERDEFINED")
    w1 = _element(f, "IfcWall", "P1", PredefinedType="USERDEFINED", ObjectType="DIVISORIA")
    ifcopenshell.api.run("type.assign_type", f, related_objects=[w1], relating_type=wt)
    wt2 = ifcopenshell.api.run("root.create_entity", f, ifc_class="IfcWallType", name="Sem texto", predefined_type="USERDEFINED")
    _run(f, rules, {"SCH-USERDEF"})
    assert wt.ElementType == "DIVISORIA"
    assert wt2.PredefinedType == "NOTDEFINED"


def test_deprecated_entities_and_attributes(rules):
    f = build_minimal_model()
    wall = _element(f, "IfcWallStandardCase", "Parede")
    guid = wall.GlobalId
    storey = _storey(f)
    storey.Elevation = 3.5
    building = f.by_type("IfcBuilding")[0]
    address = f.create_entity("IfcPostalAddress", Town="Araraquara")
    building.BuildingAddress = address
    faces = f.create_entity("IfcConnectedFaceSet", [f.create_entity("IfcFace", [f.create_entity(
        "IfcFaceOuterBound", f.create_entity("IfcPolyLoop", [f.create_entity("IfcCartesianPoint", c) for c in
                                                             ((0., 0., 0.), (1., 0., 0.), (1., 1., 0.))]), True)])])
    fbsm = f.create_entity("IfcFaceBasedSurfaceModel", [faces])
    ctx = f.by_type("IfcGeometricRepresentationSubContext")[0]
    f.by_type("IfcColumn")[0].Representation.Representations = list(
        f.by_type("IfcColumn")[0].Representation.Representations) + [
        f.create_entity("IfcShapeRepresentation", ctx, "Surface", "SurfaceModel", [fbsm])]
    mat = [f.create_entity("IfcMaterial", "Aço"), f.create_entity("IfcMaterial", "Concreto")]
    mlist = f.create_entity("IfcMaterialList", mat)
    f.create_entity("IfcRelAssociatesMaterial", GlobalId=repair.stable_guid("m"), RelatedObjects=[wall], RelatingMaterial=mlist)

    res = _run(f, rules, {"IFC102-ENT", "IFC102-GEO", "IFC102-ATR"})
    assert f.by_guid(guid).is_a() == "IfcWall"
    assert storey.Elevation is None and building.BuildingAddress is None and not f.by_type("IfcPostalAddress")
    assert not f.by_type("IfcFaceBasedSurfaceModel") and f.by_type("IfcShellBasedSurfaceModel")
    assert f.by_type("IfcShellBasedSurfaceModel")[0].SbsmBoundary[0].CfsFaces == faces.CfsFaces  # mesmas faces
    cs = f.by_type("IfcMaterialConstituentSet")[0]
    assert [c.Material.Name for c in cs.MaterialConstituents] == ["Aço", "Concreto"]
    elevation = [c for c in res.changes if c["campo"] == "Elevation"]
    assert elevation and elevation[0]["antes"] == 3.5  # valor anterior registrado
    assert any("Araraquara" in str(c["antes"]) for c in res.changes if c["campo"] == "BuildingAddress")


def test_object_predefined_type_follows_type(rules):
    f = build_minimal_model()
    st = ifcopenshell.api.run("root.create_entity", f, ifc_class="IfcSlabType", name="Laje", predefined_type="FLOOR")
    same, other = _element(f, "IfcSlab", "L1"), _element(f, "IfcSlab", "L2")
    ifcopenshell.api.run("type.assign_type", f, related_objects=[same, other], relating_type=st)
    same.PredefinedType, other.PredefinedType = "FLOOR", "ROOF"  # como o exportador grava
    res = _run(f, rules, {"OJT001"})
    assert same.PredefinedType is None and other.PredefinedType == "ROOF"
    assert res.counts["OJT001"] == {"encontrados": 2, "corrigidos": 1, "pendentes": 1}


def test_reports_without_changing(rules):
    f = build_minimal_model()
    seg = _element(f, "IfcFlowSegment", "Tubo")
    pset = ifcopenshell.api.run("pset.add_pset", f, product=seg, name="Pset_FlowSegmentPipeSegment")
    ifcopenshell.api.run("pset.edit_pset", f, pset=pset, properties={"Reference": "DN50"})
    ctx = f.by_type("IfcGeometricRepresentationContext")[0]
    crs = f.create_entity("IfcProjectedCRS", Name="EPSG:0", Description="SIRGAS datum, UTM Zone 22S")
    f.create_entity("IfcMapConversion", SourceCRS=ctx, TargetCRS=crs, Eastings=0.0, Northings=0.0, OrthogonalHeight=0.0)
    proxy = f.by_type("IfcBuildingElementProxy")[0]
    proxy.Name = None

    res = _run(f, rules, {"IFC102-FLOW", "GRF004", "PSE001", "SCH-NOME"})
    assert seg.is_a() == "IfcFlowSegment" and crs.Name == "EPSG:0"  # só aponta
    by_code = {p["codigo"]: p for p in res.pending}
    assert "IfcPipeSegment" in by_code["IFC102-FLOW"]["sugestao"]
    assert "UTM Zone 22S" in by_code["GRF004"]["sugestao"]
    assert "Pset_FlowSegmentPipeSegment" in by_code["PSE001"]["detalhe"]
    assert "SCH-NOME" in by_code
    assert not res.changes


def test_repair_is_deterministic(rules, tmp_path):
    f = build_minimal_model()
    _element(f, "IfcWallStandardCase", "Parede")
    _storey(f).Elevation = 3.0
    source = tmp_path / "origem.ifc"
    f.write(str(source))
    outs = []
    for i in range(2):
        g = ifcopenshell.open(str(source))
        _run(g, rules)
        path = tmp_path / f"{i}.ifc"
        g.write(str(path))
        outs.append([line for line in path.read_text().splitlines() if not line.startswith("FILE_NAME")])
    assert outs[0] == outs[1]


def _bsi_examples(rule_id, scenario_start):
    rule = next(r for r in repair.bsi_rules() if r["id"] == rule_id)
    return [row for s in rule["cenarios"] if s["nome"].startswith(scenario_start) for row in s["exemplos"]]


def test_ifc102_lists_match_buildingsmart(rules):
    """verificacao.json cobre todas as tabelas da IFC102 (IFC 4.3) do validador bSI (ref/bsi-validacao)."""
    by_id = {c["id"]: c for c in rules["correcoes"]}
    entities = {r["Entity"] for r in _bsi_examples("IFC102", "Check for deprecated entities - IFC4.3")}
    handled = (set(by_id["IFC102-ENT"]["equivalentes"]) | set(by_id["IFC102-OBS"]["entidades"])
               | {"IfcFaceBasedSurfaceModel", "IfcMaterialList"})  # IFC102-GEO
    assert entities and entities == handled
    attrs = {(r["Entity"], r["Attribute"]) for r in _bsi_examples("IFC102", "Check for deprecated attributes - IFC4.3")}
    assert attrs == {(c, a) for c, names in by_id["IFC102-ATR"]["atributos"].items() for a in names}
    enums = {(r["Entity"], v.strip(" '")) for r in _bsi_examples("IFC102", "Check for deprecated enumerated values - IFC4.3")
             for v in r["Value"].split(" or ")}
    assert enums == {(c, v) for c, vals in by_id["IFC102-ENUM"]["valores"].items() for v in vals}
    flow = {r["Entity"] for r in _bsi_examples("IFC102", "Check for deprecated explicitly instantiated entities - IFC4.3")}
    assert flow == set(by_id["IFC102-FLOW"]["classes"])


def test_deprecated_entities_and_values_are_reported(rules):
    f = build_minimal_model()
    system = f.create_entity("IfcBuildingSystem", ifcopenshell.guid.new(), Name="Sistema")
    rep = f.by_type("IfcShapeRepresentation")[0]
    rep.RepresentationType = "PointCloud"
    ifcopenshell.api.run("pset.add_pset", f, product=f.by_type("IfcColumn")[0], name="Pset_Draughting")
    res = _run(f, rules, {"IFC102-OBS", "IFC102-VAL"})
    assert system.is_a() == "IfcBuildingSystem" and not res.changes  # só aponta
    found = {(p["codigo"], p["classe"]): p for p in res.pending}
    assert found[("IFC102-OBS", "IfcBuildingSystem")]["sugestao"] == "IfcBuiltSystem"
    assert ("IFC102-VAL", "IfcShapeRepresentation") in found and ("IFC102-VAL", "IfcPropertySet") in found


def test_bsi_coverage_table(rules):
    rows = {r["regra_bsi"]: r for r in repair.bsi_coverage(rules)}
    assert len(rows) == 104
    assert rows["IFC102"]["acao"] == "automatica + pendencia" and "IFC102-OBS" in rows["IFC102"]["verificacao"]
    assert rows["ALB000"]["acao"] == "não verificada"
