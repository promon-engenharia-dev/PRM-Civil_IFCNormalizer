from ifc_normalizer import profile


def test_normalize_name_strips_ids():
    assert profile.normalize_name("Pilar:P1:123456") == "Pilar:P1"
    assert profile.normalize_name("Pipe - (123)") == "Pipe -"
    assert profile.normalize_name(None) == ""


def test_detect_discipline():
    assert profile.detect_discipline("ZTO-13-04-ST3-OFICINA.ifc") == "ST3"
    assert profile.detect_discipline("RVT_B0310-F3_FEL3_0313_A_APOIO.ifc") == "A"


def test_profile_minimal(minimal_ifc):
    p = profile.profile_file(minimal_ifc)
    assert p["schema"] == "IFC4X3_ADD2"
    assert p["schema_alvo_ok"]
    assert p["disciplina"] == "ST4"
    assert p["total_elementos"] == 2
    assert p["proxies"] == 1
    assert p["sem_container"] == 0
    assert p["sem_layer"] == 1
    assert p["com_pset_prm"] == 1
    assert [s["classe"] for s in p["espacial"]] == ["IfcProject", "IfcSite", "IfcBuilding", "IfcBuildingStorey"]
    layer = p["layers"][0]
    assert layer["layer"] == "GM_ESTRUTURA" and layer["segue_padrao_GM"] and layer["elementos"] == 1
    fill = {(r["propriedade"], r["preenchido"]) for r in p["prm_preenchimento"]}
    assert ("PRM_EAP", True) in fill and ("PRM_CodigoMaterial", False) in fill


def test_profile_is_read_only(minimal_ifc):
    before = minimal_ifc.read_bytes()
    profile.profile_file(minimal_ifc)
    assert minimal_ifc.read_bytes() == before
