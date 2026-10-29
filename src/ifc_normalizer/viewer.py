"""Visualizador 3D: tesselação dos IFCs para a interface e consulta de propriedades.

Somente leitura. A malha é gerada pelo ifcopenshell (coordenadas do mundo, em metros) e enviada à
interface como um bloco binário; a interface desenha com WebGL. Vários modelos podem ser
carregados juntos (federado) e compartilham a mesma origem de desenho, para que a sobreposição
mostre a coerência de coordenadas entre eles.
"""

from __future__ import annotations

import base64
import multiprocessing
from dataclasses import dataclass, field
from pathlib import Path

import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.util.element
import numpy as np

# Malha mais leve que a da análise geométrica: suficiente para visualizar.
MESH_SETTINGS = {"mesher-linear-deflection": 0.005, "mesher-angular-deflection": 1.2}
# CGAL onde dá (Breps facetadas do Civil 3D ficam ~6x mais rápidas), OpenCascade no restante.
GEOMETRY_LIBRARY = "hybrid-cgal-simple-opencascade"
REINFORCEMENT = ("IfcReinforcingBar", "IfcReinforcingMesh", "IfcTendon")
NOT_DRAWN = ("IfcOpeningElement",)
CHUNK = 4 * 1024 * 1024  # caracteres base64 por parte enviada à interface
DEFAULT_COLOR = (0.75, 0.77, 0.79, 1.0)


@dataclass
class LoadedModel:
    id: int
    path: Path
    file: ifcopenshell.file
    elements: list[int] = field(default_factory=list)  # índice local -> id STEP
    payload: str = ""


class Scene:
    """Modelos carregados. Os índices de elemento são globais (todos os modelos)."""

    def __init__(self) -> None:
        self.models: dict[int, LoadedModel] = {}
        self.origin: np.ndarray | None = None  # origem de desenho comum (m)
        self._next = 1

    def clear(self) -> None:
        self.models.clear()
        self.origin = None

    def remove(self, model_id: int) -> None:
        self.models.pop(model_id, None)
        if not self.models:
            self.origin = None

    def load(self, path: Path, include_reinforcement: bool = False, on_progress=None) -> dict:
        f = ifcopenshell.open(str(path))
        model = LoadedModel(self._next, Path(path), f)
        self._next += 1
        meta, blob = tessellate(model, self, include_reinforcement, on_progress)
        model.payload = base64.b64encode(blob).decode("ascii")
        self.models[model.id] = model
        meta["bytes"] = len(blob)
        meta["partes"] = max(1, -(-len(model.payload) // CHUNK))
        return meta

    def chunk(self, model_id: int, index: int) -> str:
        payload = self.models[model_id].payload
        part = payload[index * CHUNK:(index + 1) * CHUNK]
        if (index + 1) * CHUNK >= len(payload):
            self.models[model_id].payload = ""  # já entregue: libera memória
        return part

    def properties(self, model_id: int, index: int) -> dict:
        model = self.models[model_id]
        return element_properties(model.file.by_id(model.elements[index]))


def _color(material) -> tuple[float, float, float, float]:
    if material is None:
        return DEFAULT_COLOR
    try:
        r, g, b = material.diffuse.components
        alpha = 1.0 - float(material.transparency or 0.0)
    except Exception:  # material sem cor definida
        return DEFAULT_COLOR
    return (r, g, b, max(0.15, min(1.0, alpha)))


def tessellate(model: LoadedModel, scene: Scene, include_reinforcement: bool = False, on_progress=None):
    """Gera a malha do modelo. Devolve (meta, bloco binário).

    Bloco: posições float32 (x,y,z relativas à origem comum), normais int8 (x,y,z),
    cores uint8 (r,g,b,a), elemento uint32 por vértice, índices uint32.
    """
    f = model.file
    settings = ifcopenshell.geom.settings()
    settings.set("use-world-coords", True)
    settings.set("weld-vertices", False)  # normais por face (arestas vivas)
    for k, v in MESH_SETTINGS.items():
        settings.set(k, v)

    skip = set(NOT_DRAWN) | (set() if include_reinforcement else set(REINFORCEMENT))
    products = [p for p in f.by_type("IfcProduct")
                if p.Representation is not None and not any(p.is_a(c) for c in skip)]
    skipped = 0 if include_reinforcement else sum(len(_by_type(f, c)) for c in REINFORCEMENT)
    total = len(products)

    pos, nor, col, ids, idx = [], [], [], [], []
    elements: list[dict] = []
    vcount = done = 0
    if products:
        iterator = ifcopenshell.geom.iterator(settings, f, multiprocessing.cpu_count(), include=products,
                                              geometry_library=GEOMETRY_LIBRARY)
        if iterator.initialize():
            while True:
                shape = iterator.get()
                g = shape.geometry
                verts = np.frombuffer(g.verts_buffer, dtype=np.float64).reshape(-1, 3)
                faces = np.frombuffer(g.faces_buffer, dtype=np.int32).reshape(-1, 3)
                if len(verts) and len(faces):
                    if scene.origin is None:
                        scene.origin = verts.mean(axis=0).round(0)
                    normals = np.frombuffer(g.normals_buffer, dtype=np.float64).reshape(-1, 3)
                    if len(normals) != len(verts):
                        normals = np.zeros_like(verts)
                    colors = np.empty((len(verts), 4), dtype=np.float32)
                    colors[:] = _color(g.materials[0] if len(g.materials) else None)
                    mids = np.frombuffer(g.material_ids_buffer, dtype=np.int32)
                    if len(g.materials) > 1 and len(mids) == len(faces):
                        palette = np.array([_color(m) for m in g.materials], dtype=np.float32)
                        colors[faces.ravel()] = palette[np.repeat(np.clip(mids, 0, len(palette) - 1), 3)]
                    element = f.by_id(shape.id)
                    local = len(elements)
                    model.elements.append(shape.id)
                    elements.append({"classe": element.is_a(), "nome": element.Name or "",
                                     "guid": element.GlobalId, "alpha": float(colors[:, 3].min())})
                    pos.append((verts - scene.origin).astype(np.float32))
                    nor.append(np.clip(normals * 127, -127, 127).astype(np.int8))
                    col.append((colors * 255).round().astype(np.uint8))
                    ids.append(np.full(len(verts), local, dtype=np.uint32))
                    idx.append((faces + vcount).astype(np.uint32))
                    vcount += len(verts)
                done += 1
                if on_progress and done % 200 == 0:
                    on_progress(done, total)
                if not iterator.next():
                    break

    def cat(parts, dtype, width):
        return np.concatenate(parts) if parts else np.zeros((0, width), dtype=dtype)

    arrays = [cat(pos, np.float32, 3), cat(nor, np.int8, 3), cat(col, np.uint8, 4),
              cat(ids, np.uint32, 1), cat(idx, np.uint32, 3)]
    blocks, layout, offset = [], [], 0
    for a in arrays:
        raw = a.tobytes()
        raw += b"\0" * (-len(raw) % 4)  # alinhamento para os TypedArrays
        layout.append([offset, a.size])
        blocks.append(raw)
        offset += len(raw)
    pts = arrays[0]
    meta = {
        "id": model.id,
        "arquivo": model.path.name,
        "caminho": str(model.path),
        "schema": f.schema_identifier,
        "elementos": elements,
        "sem_geometria": total - len(elements),
        "armaduras_omitidas": skipped,
        "vertices": int(vcount),
        "triangulos": int(arrays[4].shape[0]),
        "layout": layout,
        "origem": [float(v) for v in (scene.origin if scene.origin is not None else (0, 0, 0))],
        "caixa": [pts.min(axis=0).tolist(), pts.max(axis=0).tolist()] if len(pts) else None,
    }
    return meta, b"".join(blocks)


def _by_type(f, cls):
    try:
        return f.by_type(cls)
    except RuntimeError:  # classe inexistente no schema do arquivo
        return []


def _text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:.6g}"
    if isinstance(v, (tuple, list)):
        return ", ".join(_text(x) for x in v)
    if isinstance(v, ifcopenshell.entity_instance):
        return getattr(v, "Name", None) or v.is_a()
    return str(v)


def element_properties(element) -> dict:
    """Atributos, tipo, localização espacial, layers, materiais, Psets e quantidades de um elemento."""
    attrs = [["Classe IFC", element.is_a()]]
    for name in ("GlobalId", "Name", "Description", "ObjectType", "PredefinedType", "Tag", "LongName"):
        if hasattr(element, name):
            attrs.append([name, _text(getattr(element, name))])

    etype = ifcopenshell.util.element.get_type(element)
    container = ifcopenshell.util.element.get_container(element)
    spatial = []
    node = container or ifcopenshell.util.element.get_aggregate(element)
    while node is not None:
        spatial.append(f"{node.is_a()} · {node.Name or '(sem nome)'}")
        node = ifcopenshell.util.element.get_aggregate(node)
    layers = []
    rep = getattr(element, "Representation", None)
    for r in (rep.Representations if rep else []):
        for layer in getattr(r, "LayerAssignments", None) or []:
            if layer.Name not in layers:
                layers.append(layer.Name)
    materials = []
    mat = ifcopenshell.util.element.get_material(element, should_skip_usage=True)
    if mat is not None:
        if mat.is_a("IfcMaterial"):
            materials = [mat.Name]
        else:
            materials = [m.Name for m in ifcopenshell.util.element.get_materials(element)] or [_text(mat)]

    groups = []
    if etype is not None:
        groups.append({"nome": "Tipo", "itens": [["Classe", etype.is_a()], ["Nome", _text(etype.Name)],
                                                  ["PredefinedType", _text(getattr(etype, "PredefinedType", None))]]})
    groups.append({"nome": "Localização", "itens": [["Estrutura espacial", " ← ".join(spatial) or "–"],
                                                    ["Layer", ", ".join(layers) or "–"],
                                                    ["Material", ", ".join(materials) or "–"]]})
    for kind, psets in (("Pset", ifcopenshell.util.element.get_psets(element, psets_only=True)),
                        ("Quantidades", ifcopenshell.util.element.get_psets(element, qtos_only=True))):
        for pname in sorted(psets):
            items = [[k, _text(v)] for k, v in sorted(psets[pname].items()) if k != "id"]
            groups.append({"nome": pname, "tipo": kind, "itens": items})
    return {"titulo": element.Name or element.is_a(), "atributos": attrs, "grupos": groups}
