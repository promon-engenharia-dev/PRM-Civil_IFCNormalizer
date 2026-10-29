import base64

import numpy as np

from ifc_normalizer import viewer
from ifc_normalizer.gui import plan_actions


def _arrays(scene, meta):
    payload = "".join(scene.chunk(meta["id"], i) for i in range(meta["partes"]))
    blob = base64.b64decode(payload)
    assert len(blob) == meta["bytes"]
    kinds = (np.float32, np.int8, np.uint8, np.uint32, np.uint32)
    return [np.frombuffer(blob, dtype=t, count=n, offset=o) for (o, n), t in zip(meta["layout"], kinds)]


def test_tessellation_payload_is_consistent(minimal_ifc):
    scene = viewer.Scene()
    meta = scene.load(minimal_ifc)
    assert [e["classe"] for e in meta["elementos"]] == ["IfcColumn", "IfcBuildingElementProxy"]
    pos, nor, cor, ids, idx = _arrays(scene, meta)
    assert len(pos) == 3 * meta["vertices"] and len(cor) == 4 * meta["vertices"] and len(ids) == meta["vertices"]
    assert len(idx) == 3 * meta["triangulos"] and idx.max() < meta["vertices"]
    assert set(ids.tolist()) == {0, 1}
    assert meta["caixa"] is not None
    # mesma origem de desenho para o segundo modelo (sobreposição fiel)
    again = scene.load(minimal_ifc)
    assert again["origem"] == meta["origem"]


def test_properties_include_pset_layer_and_location(minimal_ifc):
    scene = viewer.Scene()
    meta = scene.load(minimal_ifc)
    p = scene.properties(meta["id"], 0)
    attrs = dict(p["atributos"])
    assert attrs["Classe IFC"] == "IfcColumn" and attrs["Name"] == "Pilar:P1:123456"
    groups = {g["nome"]: dict(g["itens"]) for g in p["grupos"]}
    assert groups["PRM-Parametros"]["PRM_EAP"] == "1.2.3"
    assert groups["Localização"]["Layer"] == "GM_ESTRUTURA"
    assert "IfcBuildingStorey · Terreo" in groups["Localização"]["Estrutura espacial"]


def test_scene_remove_resets_origin(minimal_ifc):
    scene = viewer.Scene()
    meta = scene.load(minimal_ifc)
    scene.remove(meta["id"])
    assert scene.origin is None and not scene.models


def test_combined_run_checks_coordinates_on_generated_file(minimal_ifc, tmp_path):
    from ifc_normalizer.gui import AppApi

    events = []
    api = AppApi()
    api._emit = lambda payload, handler="aoProgresso": events.append(payload)
    out = tmp_path / "saida"
    api._busy = True
    api._run([str(minimal_ifc)], out, plan_actions(["coordenadas", "normalizar"]), False, False)

    kinds = [(e["tipo"], e.get("acao")) for e in events]
    assert kinds[0] == ("inicio_lote", None) and kinds[-1] == ("fim_lote", None)
    assert [a for t, a in kinds if t == "inicio_etapa"] == ["normalizar", "coordenadas"]
    coord = next(e for e in events if e["tipo"] == "fim_etapa" and e["acao"] == "coordenadas")
    assert coord["coordenadas"][0]["caminho"] == str(out / minimal_ifc.name)  # o arquivo normalizado
    assert (out / "coordenadas" / "coordenadas.xlsx").exists()
    assert not api._busy


def test_plan_actions_order_and_exclusion():
    assert plan_actions(["coordenadas", "normalizar", "diagnostico"]) == ["diagnostico", "normalizar", "coordenadas"]
    assert plan_actions(["normalizar", "migrar"]) == ["normalizar"]  # normalizar já converte
    assert plan_actions("migrar") == ["migrar"]
    assert plan_actions([]) == []
