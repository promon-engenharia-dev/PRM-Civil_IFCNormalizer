"""Análise do arquivo no estilo do IFC File Analyzer (NIST): famílias, atributos preenchidos e relações.

O conhecimento vem de ref/nist-ifa/conhecimento.json (extraído do IFA 3.10): famílias de entidades, versão do
IFC em que cada entidade existe e relações inversas. Também verifica a sintaxe STEP no texto do arquivo
(referências a entidades inexistentes, ids repetidos, arquivo vazio), como o IFA reporta.
"""

from __future__ import annotations

import collections
import json
import re
from functools import lru_cache
from pathlib import Path

import ifcopenshell
import ifcopenshell.util.element

from .paths import app_dir

KNOWLEDGE = app_dir() / "ref" / "nist-ifa" / "conhecimento.json"
_DEF = re.compile(rb"^\s*#(\d+)\s*=", re.M)
_REF = re.compile(rb"#(\d+)")
_STRING = re.compile(rb"'(?:[^']|'')*'")
FILL_ATTRIBUTES = ("Name", "Description", "ObjectType", "Tag", "LongName")


@lru_cache(maxsize=1)
def knowledge() -> dict:
    if not KNOWLEDGE.exists():
        return {"familias": {}, "versoes": {}, "relacoes_inversas": []}
    with open(KNOWLEDGE, encoding="utf-8") as fh:
        return json.load(fh)


@lru_cache(maxsize=1)
def _family_index() -> dict[str, str]:
    index = {}
    for fam in knowledge()["familias"].values():
        for ent in fam["entidades"]:
            index.setdefault(ent.lower(), fam["nome"])
    return index


def family(ifc_class: str | None) -> str:
    """Família do IFA para a classe (ex.: IfcBeam → Elementos construtivos)."""
    return _family_index().get((ifc_class or "").lower(), "") if ifc_class else ""


# ---------------------------------------------------------------- sintaxe

def check_syntax(path: Path) -> list[dict]:
    """Achados de sintaxe STEP no texto: arquivo vazio, #id repetido, referência a #id inexistente."""
    data = Path(path).read_bytes()
    findings = []
    for marker in (b"ISO-10303-21;", b"HEADER;", b"DATA;", b"END-ISO-10303-21;"):
        if marker not in data[:4096] and marker not in data[-4096:] and marker not in data:
            findings.append({"codigo": "SIN-ESTRUTURA", "detalhe": f"marcador {marker.decode()} ausente"})
    body = data[data.find(b"DATA;"):] if b"DATA;" in data else data
    body = _STRING.sub(b"''", body)  # '#' dentro de textos não é referência
    defined = collections.Counter(int(m) for m in _DEF.findall(body))
    if not defined:
        findings.append({"codigo": "SIN-VAZIO", "detalhe": "arquivo sem entidades"})
        return findings
    for eid, n in sorted(defined.items()):
        if n > 1:
            findings.append({"codigo": "SIN-ID", "detalhe": f"#{eid} definido {n} vezes"})
    referenced = {int(m) for m in _REF.findall(body)}
    for eid in sorted(referenced - set(defined)):
        findings.append({"codigo": "SIN-REF", "detalhe": f"#{eid} é referenciado mas não existe no arquivo"})
    return findings


def placement_cycles(f: ifcopenshell.file) -> list:
    """IfcLocalPlacement cujo encadeamento (PlacementRelTo) volta para si mesmo."""
    cyclic = []
    for lp in f.by_type("IfcLocalPlacement"):
        seen, node = set(), lp
        while node is not None and node.is_a("IfcLocalPlacement"):
            if node.id() in seen:
                cyclic.append(lp)
                break
            seen.add(node.id())
            node = node.PlacementRelTo
    return cyclic


# ---------------------------------------------------------------- relatórios

def family_counts(f: ifcopenshell.file) -> list[dict]:
    """Quantidade de entidades por família do IFA (como as abas do relatório do IFA)."""
    counts = collections.Counter()
    classes = collections.defaultdict(collections.Counter)
    for e in f:
        fam = family(e.is_a()) or "Outras"
        counts[fam] += 1
        classes[fam][e.is_a()] += 1
    return [{"familia": fam, "entidades": n, "classes": len(classes[fam]),
             "principais": ", ".join(f"{c} ({k})" for c, k in classes[fam].most_common(5))}
            for fam, n in counts.most_common()]


def attribute_fill(f: ifcopenshell.file) -> list[dict]:
    """Por classe de objeto: quantos têm Name, Description, ObjectType, Tag, LongName e PredefinedType efetivo."""
    rows = []
    groups = collections.defaultdict(list)
    for o in f.by_type("IfcObject"):
        groups[o.is_a()].append(o)
    for cls, objs in sorted(groups.items()):
        row = {"classe": cls, "familia": family(cls), "qtd": len(objs)}
        for attr in FILL_ATTRIBUTES:
            if hasattr(objs[0], attr):
                row[f"% {attr}"] = _pct(sum(1 for o in objs if getattr(o, attr, None)), len(objs))
        if hasattr(objs[0], "PredefinedType"):
            row["% PredefinedType (ocorrência ou tipo)"] = _pct(sum(1 for o in objs if _effective_pdt(o)), len(objs))
        rows.append(row)
    return rows


def relation_coverage(f: ifcopenshell.file) -> list[dict]:
    """Por classe de elemento/estrutura espacial: cobertura das relações que o IFA expande."""
    rows = []
    groups = collections.defaultdict(list)
    for e in f.by_type("IfcProduct"):
        if e.is_a("IfcElement") or e.is_a("IfcSpatialElement"):
            groups[e.is_a()].append(e)
    for cls, items in sorted(groups.items()):
        n = len(items)
        rows.append({
            "classe": cls, "familia": family(cls), "qtd": n,
            "% na estrutura espacial": _pct(sum(1 for e in items if _placed(e)), n),
            "% com tipo": _pct(sum(1 for e in items if ifcopenshell.util.element.get_type(e) is not None), n),
            "% com Pset": _pct(sum(1 for e in items if _has_definition(e, "IfcPropertySet")), n),
            "% com quantidades": _pct(sum(1 for e in items if _has_definition(e, "IfcElementQuantity")), n),
            "% com material": _pct(sum(1 for e in items if ifcopenshell.util.element.get_material(e) is not None), n),
            "% com layer": _pct(sum(1 for e in items if _has_layer(e)), n),
            "% com geometria": _pct(sum(1 for e in items if e.Representation is not None), n),
            "aberturas": sum(len(getattr(e, "HasOpenings", ()) or ()) for e in items),
            "preenchimentos": sum(len(getattr(e, "FillsVoids", ()) or ()) for e in items),
        })
    return rows


def _pct(part: int, total: int) -> float:
    return round(100.0 * part / total, 1) if total else 0.0


def _effective_pdt(o) -> bool:
    value = getattr(o, "PredefinedType", None)
    if value and value != "NOTDEFINED":
        return True
    etype = ifcopenshell.util.element.get_type(o)
    return bool(etype is not None and getattr(etype, "PredefinedType", None) not in (None, "NOTDEFINED"))


def _placed(e) -> bool:
    if e.is_a("IfcSpatialElement"):
        return bool(getattr(e, "Decomposes", None))
    return bool(getattr(e, "ContainedInStructure", None) or getattr(e, "Decomposes", None)
                or getattr(e, "Nests", None) or getattr(e, "VoidsElements", None) or getattr(e, "FillsVoids", None))


def _has_definition(e, kind: str) -> bool:
    for rel in getattr(e, "IsDefinedBy", ()) or ():
        if rel.is_a("IfcRelDefinesByProperties") and rel.RelatingPropertyDefinition.is_a(kind):
            return True
    etype = ifcopenshell.util.element.get_type(e)
    return bool(etype is not None and any(p.is_a(kind) for p in (getattr(etype, "HasPropertySets", None) or ())))


def _has_layer(e) -> bool:
    rep = getattr(e, "Representation", None)
    for r in (rep.Representations if rep else ()):
        if getattr(r, "LayerAssignments", None) or any(getattr(i, "LayerAssignment", None) for i in r.Items):
            return True
    return False
