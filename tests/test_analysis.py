"""Análise no estilo do IFC File Analyzer (NIST): sintaxe STEP, famílias, preenchimento e relações."""

import ifcopenshell
import ifcopenshell.api

from conftest import build_minimal_model
from ifc_normalizer import analysis, repair

STEP = """ISO-10303-21;
HEADER;
FILE_DESCRIPTION(('ViewDefinition [x]'),'2;1');
FILE_NAME('a.ifc','2026-01-01T00:00:00',(''),(''),'','','');
FILE_SCHEMA(('IFC4'));
ENDSEC;
DATA;
{data}
ENDSEC;
END-ISO-10303-21;
"""


def _write(tmp_path, data):
    p = tmp_path / "t.ifc"
    p.write_text(STEP.format(data=data), encoding="utf-8")
    return p


def test_knowledge_from_ifa_is_available():
    k = analysis.knowledge()
    assert "PR_BEAM" in k["familias"] and "IfcBeam" in k["versoes"]["IFC4X3"]
    assert any(r["inverso"] == "ContainedInStructure" for r in k["relacoes_inversas"])
    assert analysis.family("IfcBeam") == "Elementos construtivos"
    assert analysis.family("IFCPIPESEGMENT") == "Instalações (HVAC, hidráulica, automação)"
    assert analysis.family("IfcNaoExiste") == ""


def test_syntax_finds_missing_and_duplicate_ids(tmp_path):
    ok = _write(tmp_path, "#1=IFCCARTESIANPOINT((0.,0.,0.));\n#2=IFCDIRECTION((0.,0.,1.));")
    assert analysis.check_syntax(ok) == []
    bad = _write(tmp_path, "#1=IFCCARTESIANPOINT((0.,0.,0.));\n#1=IFCDIRECTION((0.,0.,1.));\n"
                           "#3=IFCAXIS2PLACEMENT3D(#1,#9,$);\n#4=IFCLABEL('texto com #77 dentro');")
    codes = {(f["codigo"], f["detalhe"]) for f in analysis.check_syntax(bad)}
    assert ("SIN-ID", "#1 definido 2 vezes") in codes
    assert ("SIN-REF", "#9 é referenciado mas não existe no arquivo") in codes
    assert not any("#77" in d for _, d in codes)  # '#' dentro de texto não é referência
    empty = _write(tmp_path, "")
    assert [f["codigo"] for f in analysis.check_syntax(empty)] == ["SIN-VAZIO"]


def test_syntax_findings_go_to_verification_report(tmp_path):
    f = build_minimal_model()
    src = tmp_path / "origem.ifc"
    f.write(str(src))
    text = src.read_text(encoding="utf-8").replace("ENDSEC;\nEND-ISO", "#999999=IFCCARTESIANPOINT(#888888);\nENDSEC;\nEND-ISO")
    src.write_text(text, encoding="utf-8")
    res = repair.repair(f, repair.load_rules(), src)
    refs = [p for p in res.pending if p["codigo"] == "SIN-REF"]
    assert refs and "#888888" in refs[0]["detalhe"] and refs[0]["categoria"] == "Sintaxe STEP"
    assert res.syntax.endswith("problema(s)")


def test_placement_cycle():
    f = ifcopenshell.file(schema="IFC4X3_ADD2")
    origin = f.create_entity("IfcAxis2Placement3D", f.create_entity("IfcCartesianPoint", (0.0, 0.0, 0.0)))
    a = f.create_entity("IfcLocalPlacement", None, origin)
    b = f.create_entity("IfcLocalPlacement", a, origin)
    assert analysis.placement_cycles(f) == []
    a.PlacementRelTo = b
    assert {lp.id() for lp in analysis.placement_cycles(f)} == {a.id(), b.id()}


def test_fill_and_relation_reports():
    f = build_minimal_model()
    col = f.by_type("IfcColumn")[0]
    col.Description = "Pilar P1"
    fill = {r["classe"]: r for r in analysis.attribute_fill(f)}
    assert fill["IfcColumn"]["% Description"] == 100.0 and fill["IfcColumn"]["familia"] == "Elementos construtivos"
    rel = {r["classe"]: r for r in analysis.relation_coverage(f)}
    assert rel["IfcColumn"]["% na estrutura espacial"] == 100.0
    assert rel["IfcColumn"]["% com Pset"] == 100.0 and rel["IfcColumn"]["% com layer"] == 100.0
    assert rel["IfcBuildingElementProxy"]["% com layer"] == 0.0
    fams = {r["familia"] for r in analysis.family_counts(f)}
    assert "Geometria" in fams and "Elementos construtivos" in fams
