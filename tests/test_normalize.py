import copy
import json

import ifcopenshell
import ifcopenshell.api
import ifcopenshell.util.element
import ifcopenshell.util.unit
import pytest

from conftest import build_minimal_model
from ifc_normalizer import normalize, rules, validate

BASE_RULES = {
    "versao": "teste",
    "definicoes_pset": {
        "PRM-Parametros": {"PRM_CodigoAtividade": "IfcText", "PRM_Altura": "IfcLengthMeasure",
                           "PRM_Quantidade": "IfcInteger"}
    },
    "pendencias": {"classes_genericas": ["IfcBuildingElementProxy"], "exigir_predefined_type": True,
                   "ignorar_classes": ["IfcFeatureElement"]},
    "regras": [
        {
            "id": "R-FIX", "status": "aprovada", "avaliacao_bep": "teste",
            "quando": {"classe": "IfcBuildingElementProxy", "ou": [{"nome": "NUT"}, {"descricao": {"contem": "hex_nut"}}]},
            "entao": {"classe": "IfcMechanicalFastener", "predefined_type": "USERDEFINED", "object_type": "PORCA"},
        },
        {
            "id": "R-PRM", "status": "aprovada", "avaliacao_bep": "teste",
            "quando": {"propriedades": {"Origem.Codigo": {"vazio": False}}},
            "entao": {"propriedades": {"PRM-Parametros": {
                "PRM_CodigoAtividade": {"copiar": "Origem.Codigo"},
                "PRM_Altura": {"copiar": "Origem.Altura", "unidade_origem": "m"},
            }}},
        },
        {
            "id": "R-PROP", "status": "proposta",
            "quando": {"classe": "IfcColumn"},
            "entao": {"predefined_type": "COLUMN"},
        },
    ],
}


def _ruleset(tmp_path, data=None):
    path = tmp_path / "regras.json"
    path.write_text(json.dumps(data or BASE_RULES), encoding="utf-8")
    return rules.load_normalization_rules(path)


@pytest.fixture
def model():
    f = build_minimal_model()
    col = f.by_type("IfcColumn")[0]
    proxy = f.by_type("IfcBuildingElementProxy")[0]
    proxy.Name = "NUT"
    pset = ifcopenshell.api.run("pset.add_pset", f, product=proxy, name="Origem")
    ifcopenshell.api.run("pset.edit_pset", f, pset=pset, properties={"Codigo": "M-75-10", "Altura": 0.25})
    other = ifcopenshell.api.run("root.create_entity", f, ifc_class="IfcBuildingElementProxy", name="FORMA")
    storey = f.by_type("IfcBuildingStorey")[0]
    ifcopenshell.api.run("spatial.assign_container", f, relating_structure=storey, products=[other])
    return f, col.GlobalId, proxy.GlobalId, other.GlobalId


def test_rules_validation_catches_errors():
    bad = copy.deepcopy(BASE_RULES)
    bad["regras"][0]["entao"]["predefined_type"] = "NUT"          # não existe no enum
    bad["regras"][1]["entao"]["classe"] = "IfcInexistente"
    bad["regras"][2]["id"] = "R-FIX"                               # id duplicado
    problems, _ = rules.validate(bad)
    text = "\n".join(problems)
    assert "PredefinedType 'NUT' inválido" in text
    assert "IfcInexistente" in text
    assert "id duplicado" in text


def test_userdefined_requires_object_type():
    bad = copy.deepcopy(BASE_RULES)
    del bad["regras"][0]["entao"]["object_type"]
    problems, _ = rules.validate(bad)
    assert any("USERDEFINED exige object_type" in p for p in problems)


def test_reclassifies_preserving_guid_and_relations(model, tmp_path):
    f, _, proxy_guid, _ = model
    result = normalize.normalize(f, _ruleset(tmp_path), "TESTE.ifc")
    el = f.by_guid(proxy_guid)
    assert el.is_a() == "IfcMechanicalFastener"
    assert el.PredefinedType == "USERDEFINED" and el.ObjectType == "PORCA"
    assert ifcopenshell.util.element.get_container(el).Name == "Terreo"
    assert el.Representation is not None
    assert ifcopenshell.util.element.get_pset(el, "Origem")["Codigo"] == "M-75-10"
    campos = {(c["global_id"], c["campo"], c["regra"]) for c in result.changes}
    assert (proxy_guid, "classe", "R-FIX") in campos
    assert validate.validate_schema(f) == []


def test_copies_typed_properties(model, tmp_path):
    f, _, proxy_guid, _ = model
    normalize.normalize(f, _ruleset(tmp_path), "TESTE.ifc")
    el = f.by_guid(proxy_guid)
    prm = ifcopenshell.util.element.get_pset(el, "PRM-Parametros")
    assert prm["PRM_CodigoAtividade"] == "M-75-10"
    # Origem em metros convertida para a unidade do arquivo (milímetros no modelo sintético).
    scale = ifcopenshell.util.unit.calculate_unit_scale(f)
    assert scale == pytest.approx(0.001)
    assert prm["PRM_Altura"] == pytest.approx(250.0)
    pset = normalize._occurrence_pset(el, "PRM-Parametros")
    altura = next(p for p in pset.HasProperties if p.Name == "PRM_Altura")
    assert altura.NominalValue.is_a() == "IfcLengthMeasure"


def test_unmatched_elements_become_pending(model, tmp_path):
    f, col_guid, _, other_guid = model
    result = normalize.normalize(f, _ruleset(tmp_path), "TESTE.ifc")
    pend = {(p["global_id"], p["motivo"]) for p in result.pending}
    assert (other_guid, normalize.GENERIC_CLASS) in pend
    assert (other_guid, normalize.NO_PREDEFINED) not in pend  # uma pendência por proxy
    assert (col_guid, normalize.NO_PREDEFINED) in pend
    assert f.by_guid(other_guid).is_a() == "IfcBuildingElementProxy"  # não decide sozinho


def test_proposed_rules_only_with_flag(model, tmp_path):
    f, col_guid, _, _ = model
    rs = _ruleset(tmp_path)
    normalize.normalize(f, rs, "TESTE.ifc")
    assert f.by_guid(col_guid).PredefinedType in (None, "NOTDEFINED")

    g = build_minimal_model()
    result = normalize.normalize(g, rs, "TESTE.ifc", include_proposed=True)
    assert g.by_type("IfcColumn")[0].PredefinedType == "COLUMN"
    assert any(c["status_regra"] == "proposta" for c in result.changes)


def test_conflicting_rules_are_reported(model, tmp_path):
    f, _, proxy_guid, _ = model
    data = copy.deepcopy(BASE_RULES)
    data["regras"].append({
        "id": "R-FIX-2", "status": "aprovada", "avaliacao_bep": "teste",
        "quando": {"nome": "NUT"},
        "entao": {"classe": "IfcMechanicalFastener", "predefined_type": "BOLT"},
    })
    result = normalize.normalize(f, _ruleset(tmp_path, data), "TESTE.ifc")
    assert f.by_guid(proxy_guid).PredefinedType == "USERDEFINED"  # primeira regra vence
    conflicts = [p for p in result.pending if p["motivo"] == normalize.CONFLICT]
    assert conflicts and conflicts[0]["regra"] == "R-FIX-2"


def test_deterministic(model, tmp_path):
    """Mesma entrada + mesmas regras = mesma saída, byte a byte."""
    f, *_ = model
    source = tmp_path / "entrada.ifc"
    f.write(str(source))
    rs = _ruleset(tmp_path)
    outs = []
    for name in ("a.ifc", "b.ifc"):
        g = ifcopenshell.open(str(source))
        normalize.normalize(g, rs, "TESTE.ifc")
        g.write(str(tmp_path / name))
        outs.append((tmp_path / name).read_bytes())
    assert outs[0] == outs[1]


def _typed_beams(names: list[str]):
    """Modelo com um IfcBeamType compartilhado por IfcBeam (como o Tekla exporta porcas)."""
    f = build_minimal_model()
    storey = f.by_type("IfcBuildingStorey")[0]
    beam_type = ifcopenshell.api.run("root.create_entity", f, ifc_class="IfcBeamType", name="1/2''Ø_HEAVY_HEX_NUT")
    pset = ifcopenshell.api.run("pset.add_pset", f, product=beam_type, name="Dados do tipo")
    ifcopenshell.api.run("pset.edit_pset", f, pset=pset, properties={"Diametro": "1/2"})
    elements = []
    for name in names:
        el = ifcopenshell.api.run("root.create_entity", f, ifc_class="IfcBeam", name=name)
        ifcopenshell.api.run("spatial.assign_container", f, relating_structure=storey, products=[el])
        ifcopenshell.api.run("type.assign_type", f, related_objects=[el], relating_type=beam_type)
        elements.append(el.GlobalId)
    return f, beam_type.GlobalId, elements


def _with_types(enabled: bool) -> dict:
    data = copy.deepcopy(BASE_RULES)
    data["tipos"] = {"reclassificar_com_elemento": enabled}
    data["regras"][0]["quando"]["classe"] = ["IfcBuildingElementProxy", "IfcBeam"]
    return data


def test_exclusive_type_is_reclassified_with_elements(tmp_path):
    f, type_guid, els = _typed_beams(["NUT", "NUT"])
    result = normalize.normalize(f, _ruleset(tmp_path, _with_types(True)), "TESTE.ifc")

    t = f.by_guid(type_guid)
    assert t.is_a() == "IfcMechanicalFastenerType"
    assert t.Name == "1/2''Ø_HEAVY_HEX_NUT"
    assert t.PredefinedType == "USERDEFINED" and t.ElementType == "PORCA"
    assert ifcopenshell.util.element.get_pset(t, "Dados do tipo")["Diametro"] == "1/2"  # Pset do tipo preservado
    for g in els:
        assert ifcopenshell.util.element.get_type(f.by_guid(g)) == t
    assert not [p for p in result.pending if p["motivo"] == normalize.TYPE_MISMATCH]
    campos = {(c["global_id"], c["campo"]) for c in result.changes}
    assert (type_guid, "classe") in campos and (type_guid, "ElementType") in campos
    assert validate.validate_schema(f) == []


def test_shared_type_is_split_per_class(tmp_path):
    f, type_guid, els = _typed_beams(["NUT", "FORMA"])  # FORMA não casa regra nenhuma: continua IfcBeam
    result = normalize.normalize(f, _ruleset(tmp_path, _with_types(True)), "TESTE.ifc")

    original = f.by_guid(type_guid)
    assert original.is_a() == "IfcBeamType" and ifcopenshell.util.element.get_type(f.by_guid(els[1])) == original
    nut_type = ifcopenshell.util.element.get_type(f.by_guid(els[0]))
    assert nut_type.is_a() == "IfcMechanicalFastenerType" and nut_type.Name == original.Name
    assert nut_type.PredefinedType == "USERDEFINED" and nut_type.ElementType == "PORCA"
    assert ifcopenshell.util.element.get_pset(nut_type, "Dados do tipo")["Diametro"] == "1/2"  # Pset compartilhado
    assert not [p for p in result.pending if p["motivo"] == normalize.TYPE_MISMATCH]
    assert any(c["campo"] == "tipo dividido" for c in result.changes)
    errors = validate.validate_schema(f, express_rules=True)
    assert not [e for e in errors if "CorrectTypeAssigned" in e["atributo"]]
    assert nut_type.GlobalId == normalize.stable_guid(type_guid, "tipo", "IfcMechanicalFastenerType")


def test_type_reclassification_can_be_disabled(tmp_path):
    f, type_guid, _ = _typed_beams(["NUT"])
    result = normalize.normalize(f, _ruleset(tmp_path, _with_types(False)), "TESTE.ifc")
    assert f.by_guid(type_guid).is_a() == "IfcBeamType"
    assert any("desligada" in p["detalhe"] for p in result.pending if p["motivo"] == normalize.TYPE_MISMATCH)


def test_rules_validation_types_option():
    bad = _with_types(True)
    bad["tipos"]["outra"] = 1
    problems, _ = rules.validate(bad)
    assert any("opção desconhecida 'outra'" in p for p in problems)


def test_requires_ifc4x3(tmp_path):
    f = build_minimal_model("IFC4")
    with pytest.raises(ValueError, match="IFC4X3_ADD2"):
        normalize.normalize(f, _ruleset(tmp_path), "TESTE.ifc")


def test_no_timestamped_owner_history(tmp_path):
    """Arquivo com usuário/aplicativo (como os reais): a normalização não cria IfcOwnerHistory
    com a hora atual, senão a saída mudaria a cada execução (princípio do determinismo)."""
    from ifc_normalizer import migrate

    src = build_minimal_model("IFC2X3")
    src.by_type("IfcBuildingElementProxy")[0].Name = "NUT"
    path = tmp_path / "origem_2x3.ifc"
    src.write(str(path))
    f = migrate.migrate_file(path).file
    assert f.by_type("IfcPersonAndOrganization") and f.by_type("IfcApplication")
    before = len(f.by_type("IfcOwnerHistory"))

    result = normalize.normalize(f, _ruleset(tmp_path), "TESTE.ifc")
    assert any(c["campo"].startswith("PRM-Parametros") or c["campo"] == "classe" for c in result.changes)
    assert len(f.by_type("IfcOwnerHistory")) == before

    import ifcopenshell.api.owner.settings as owner
    assert owner.get_user(f) is not None  # configuração global restaurada depois da normalização
