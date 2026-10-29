"""Fase 1: perfilamento/diagnóstico de IFCs (somente leitura).

Nada aqui altera o arquivo. O resultado é um dicionário serializável que o
`report` transforma em planilha/JSON.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import ifcopenshell
import ifcopenshell.util.element
import ifcopenshell.util.placement
import ifcopenshell.util.unit

TARGET_SCHEMA = "IFC4X3_ADD2"
PRM_PSET = "PRM-Parametros"
LAYER_PATTERN = re.compile(r"^GM_[A-Z0-9_ ]+$")

# Classes que não são "elementos físicos" para fins de classificação.
NON_PHYSICAL = ("IfcFeatureElement", "IfcVirtualElement")

_TRAILING_ID = re.compile(r"(:\d+|\s*\[\d+\]|\s*\(\d+\)|\s+\d+)$")


def detect_origin(header_app: str) -> str:
    app = header_app.lower()
    if "revit" in app:
        return "Revit"
    if "tekla" in app:
        return "Tekla"
    if "civil" in app or "build" in app.strip():
        # O Civil 3D exporta com originating_system vazio/" Build ".
        return "Civil3D"
    return "Desconhecido"


def detect_discipline(filename: str) -> str:
    """Disciplina pelo nome do arquivo (ex.: ZTO-13-04-ST3-..., RVT_..._0313_A_...)."""
    stem = Path(filename).stem.upper()
    m = re.search(r"-(AQ|ST\d|IH|EL|HI|ME|TU)-", stem)
    if m:
        return m.group(1)
    m = re.search(r"_\d{4}_([A-Z])_", stem)
    if m:
        return m.group(1)
    return ""


def has_accents(text: str) -> bool:
    return any(unicodedata.combining(c) for c in unicodedata.normalize("NFD", text))


def normalize_name(name: str | None) -> str:
    """Remove sufixos de identificação (ex.: ':123456') para agrupar nomes."""
    if not name:
        return ""
    prev = None
    out = name.strip()
    while prev != out:
        prev = out
        out = _TRAILING_ID.sub("", out).strip()
    return out


def _header(f: ifcopenshell.file) -> dict:
    h = f.header
    desc = list(h.file_description.description)
    app = h.file_name.originating_system or ""
    pre = h.file_name.preprocessor_version or ""
    return {
        "schema": f.schema_identifier if hasattr(f, "schema_identifier") else f.schema,
        "aplicativo": app.strip(),
        "pre_processador": pre.strip(),
        "data": h.file_name.time_stamp,
        "autor": ", ".join(a for a in h.file_name.author if a),
        "organizacao": ", ".join(o for o in h.file_name.organization if o),
        "view_definition": next((d for d in desc if "ViewDefinition" in d), ""),
        "coordinate_reference": next((d for d in desc if "CoordinateReference" in d), ""),
    }


def _layer_index(f: ifcopenshell.file) -> dict[int, list[str]]:
    """id de representação/item → nomes de layer."""
    index: dict[int, list[str]] = defaultdict(list)
    for la in f.by_type("IfcPresentationLayerAssignment"):
        for item in la.AssignedItems or ():
            index[item.id()].append(la.Name or "")
    return index


def _element_layers(element, layer_index) -> list[str]:
    rep = getattr(element, "Representation", None)
    if not rep:
        return []
    found: set[str] = set()
    for r in rep.Representations or ():
        found.update(layer_index.get(r.id(), ()))
        for item in r.Items or ():
            found.update(layer_index.get(item.id(), ()))
    return sorted(found)


def _spatial_children(obj):
    for rel in getattr(obj, "IsDecomposedBy", ()) or ():
        for child in rel.RelatedObjects:
            yield child


def _spatial_tree(f: ifcopenshell.file, unit_scale: float) -> list[dict]:
    spatial_base = "IfcSpatialElement" if f.schema != "IFC2X3" else "IfcSpatialStructureElement"
    rows: list[dict] = []

    def walk(obj, depth: int):
        contained = 0
        for rel in getattr(obj, "ContainsElements", ()) or ():
            contained += len(rel.RelatedElements)
        z = None
        if getattr(obj, "ObjectPlacement", None):
            m = ifcopenshell.util.placement.get_local_placement(obj.ObjectPlacement)
            z = round(float(m[2][3]) * unit_scale, 3)
        rows.append(
            {
                "nivel": depth,
                "classe": obj.is_a(),
                "nome": obj.Name or "",
                "long_name": getattr(obj, "LongName", None) or "",
                "predefined_type": _predefined(obj),
                "elevacao_m": (
                    round(obj.Elevation * unit_scale, 3)
                    if getattr(obj, "Elevation", None) is not None
                    else None
                ),
                "z_global_m": z,
                "elementos_contidos": contained,
            }
        )
        for child in sorted(_spatial_children(obj), key=lambda o: (o.is_a(), o.Name or "")):
            if child.is_a(spatial_base) or child.is_a("IfcProject"):
                walk(child, depth + 1)

    for project in f.by_type("IfcProject"):
        walk(project, 0)
    return rows


def _predefined(obj) -> str:
    value = getattr(obj, "PredefinedType", None)
    return str(value) if value is not None else ""


def _coordinates(f: ifcopenshell.file, unit_scale: float) -> dict:
    out: dict = {}
    sites = f.by_type("IfcSite")
    if sites:
        site = sites[0]
        out["site_nome"] = site.Name or ""
        out["site_ref_latitude"] = list(site.RefLatitude) if site.RefLatitude else None
        out["site_ref_longitude"] = list(site.RefLongitude) if site.RefLongitude else None
        out["site_ref_elevation_m"] = (
            round(site.RefElevation * unit_scale, 3) if site.RefElevation is not None else None
        )
        if site.ObjectPlacement:
            m = ifcopenshell.util.placement.get_local_placement(site.ObjectPlacement)
            out["site_origem_m"] = [round(float(m[i][3]) * unit_scale, 3) for i in range(3)]
            out["site_rotacao_graus"] = round(math.degrees(math.atan2(m[1][0], m[0][0])), 4)
    for ctx in f.by_type("IfcGeometricRepresentationContext"):
        if ctx.is_a("IfcGeometricRepresentationSubContext"):
            continue
        if ctx.TrueNorth:
            x, y = ctx.TrueNorth.DirectionRatios[:2]
            out["true_north_graus"] = round(math.degrees(math.atan2(-x, y)), 4)
        if ctx.WorldCoordinateSystem:
            loc = ctx.WorldCoordinateSystem.Location.Coordinates
            out["wcs_origem_m"] = [round(c * unit_scale, 3) for c in loc]
        break
    if f.schema != "IFC2X3":
        convs = f.by_type("IfcMapConversion")
        out["map_conversion"] = bool(convs)
        if convs:
            c = convs[0]
            out["map_conversion_eastings"] = c.Eastings
            out["map_conversion_northings"] = c.Northings
            out["map_conversion_altura"] = c.OrthogonalHeight
            crs = c.TargetCRS
            out["crs"] = crs.Name if crs else None
    else:
        out["map_conversion"] = False
    return out


def _psets(f: ifcopenshell.file):
    """Contagem de Psets por classe e preenchimento do PRM-Parametros."""
    per_class: Counter = Counter()
    prm_fill: Counter = Counter()
    prm_elements = 0
    for rel in f.by_type("IfcRelDefinesByProperties"):
        pdef = rel.RelatingPropertyDefinition
        if not pdef or not pdef.is_a("IfcPropertySetDefinition"):
            continue
        name = pdef.Name or ""
        kind = "Qto" if pdef.is_a("IfcElementQuantity") else "Pset"
        for obj in rel.RelatedObjects:
            per_class[(obj.is_a(), kind, name)] += 1
        if name == PRM_PSET and pdef.is_a("IfcPropertySet"):
            prm_elements += len(rel.RelatedObjects)
            for prop in pdef.HasProperties or ():
                val = getattr(prop, "NominalValue", None)
                filled = val is not None and str(val.wrappedValue).strip() != ""
                prm_fill[(prop.Name, filled)] += len(rel.RelatedObjects)
    return per_class, prm_fill, prm_elements


def profile_file(path: str | Path) -> dict:
    path = Path(path)
    f = ifcopenshell.open(str(path))
    unit_scale = ifcopenshell.util.unit.calculate_unit_scale(f)
    header = _header(f)
    origin = detect_origin(header["aplicativo"] + " " + header["pre_processador"])
    layer_index = _layer_index(f)

    elements = [
        e for e in f.by_type("IfcElement") if not any(e.is_a(c) for c in NON_PHYSICAL)
    ]
    classes: Counter = Counter()
    groups: dict[tuple, dict] = {}
    no_container = 0
    no_layer = 0
    empty_name = 0
    layer_count: Counter = Counter()

    for e in elements:
        cls = e.is_a()
        pdt = _predefined(e)
        classes[(cls, pdt)] += 1
        etype = ifcopenshell.util.element.get_type(e)
        type_name = (etype.Name or "") if etype else ""
        type_class = etype.is_a() if etype else ""
        layers = _element_layers(e, layer_index)
        for lay in layers:
            layer_count[lay] += 1
        if not layers:
            no_layer += 1
        if not ifcopenshell.util.element.get_container(e):
            no_container += 1
        if not (e.Name or "").strip():
            empty_name += 1

        key = (
            cls,
            pdt,
            normalize_name(e.ObjectType if hasattr(e, "ObjectType") else ""),
            type_class,
            normalize_name(type_name),
            normalize_name(e.Name),
            " | ".join(layers),
        )
        g = groups.get(key)
        if g is None:
            g = groups[key] = {"qtd": 0, "exemplo_nome": e.Name or "", "exemplo_globalid": e.GlobalId}
        g["qtd"] += 1

    pset_counts, prm_fill, prm_elements = _psets(f)

    layers = []
    for la in sorted(f.by_type("IfcPresentationLayerAssignment"), key=lambda x: x.Name or ""):
        name = la.Name or ""
        layers.append(
            {
                "layer": name,
                "itens_atribuidos": len(la.AssignedItems or ()),
                "elementos": layer_count.get(name, 0),
                "segue_padrao_GM": bool(LAYER_PATTERN.match(name)),
                "tem_acento": has_accents(name),
            }
        )

    result = {
        "arquivo": path.name,
        "tamanho_mb": round(path.stat().st_size / 1e6, 1),
        "origem": origin,
        "disciplina": detect_discipline(path.name),
        **header,
        "schema_alvo_ok": header["schema"] == TARGET_SCHEMA,
        "unidade_comprimento_m": unit_scale,
        "total_entidades": len(list(f)),
        "total_elementos": len(elements),
        "proxies": sum(n for (c, _), n in classes.items() if c == "IfcBuildingElementProxy"),
        "sem_predefined": sum(
            n for (_, p), n in classes.items() if p in ("", "NOTDEFINED", "USERDEFINED")
        ),
        "sem_container": no_container,
        "sem_layer": no_layer,
        "nome_vazio": empty_name,
        "com_pset_prm": prm_elements,
        "coordenadas": _coordinates(f, unit_scale),
        "espacial": _spatial_tree(f, unit_scale),
        "classes": [
            {"classe": c, "predefined_type": p, "qtd": n}
            for (c, p), n in sorted(classes.items())
        ],
        "grupos": [
            {
                "classe": k[0],
                "predefined_type": k[1],
                "object_type": k[2],
                "classe_tipo": k[3],
                "nome_tipo": k[4],
                "nome_normalizado": k[5],
                "layers": k[6],
                **v,
            }
            for k, v in sorted(groups.items())
        ],
        "layers": layers,
        "psets": [
            {"classe": c, "tipo": k, "pset": n, "qtd": q}
            for (c, k, n), q in sorted(pset_counts.items())
        ],
        "prm_preenchimento": [
            {"propriedade": p, "preenchido": filled, "qtd": q}
            for (p, filled), q in sorted(prm_fill.items())
        ],
    }
    return result
