"""Estrutura espacial e nomes de IfcProject / IfcSite / IfcBuilding (regras em rules/estrutura.json).

Roda na normalização, depois das regras de elementos. Edita a estrutura existente:
- infraestrutura (Civil 3D): o IfcBuilding único sem pavimentos vira IfcSite (mesmo GlobalId,
  posicionamento e relações) e, quando a regra pede, os elementos passam para uma instalação
  (ex.: IfcRoad) agregada ao IfcSite;
- nomes: Name/LongName do projeto, do site e do prédio (por padrão de nome de arquivo).
Estrutura que não casa com o esperado não é forçada: vira pendência.
"""

from __future__ import annotations

import copy
import fnmatch
import json
from pathlib import Path

import ifcopenshell

from .normalize import matches, reclassify, stable_guid
from .paths import rules_dir
from .rules import RulesError, predefined_items

DEFAULT_RULES = rules_dir() / "estrutura.json"
NAME_FIELDS = {"nome": "Name", "nome_longo": "LongName"}
ACTIONS = ("building_para_site", "instalacao")
CONDITION_KEYS = {"origem", "disciplina", "arquivo"}
NAMES_RULE = {"id": "EST-NOMES", "status": "aprovada"}
UNRECOGNIZED = "estrutura_espacial_nao_reconhecida"


def load_rules(path: str | Path = DEFAULT_RULES) -> dict:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    problems = validate_rules(data)
    if problems:
        raise RulesError(problems)
    data["_fonte"] = str(path)
    return data


def validate_rules(data: dict) -> list[str]:
    problems = []
    schema = ifcopenshell.ifcopenshell_wrapper.schema_by_name("IFC4X3_ADD2")
    for key in ("projeto", "site"):
        extra = set(data.get(key) or {}) - set(NAME_FIELDS)
        if extra:
            problems.append(f"{key}: campos desconhecidos {sorted(extra)}")
    for i, p in enumerate(data.get("predios") or []):
        if not p.get("id") or not p.get("arquivos"):
            problems.append(f"predios[{i}]: 'id' e 'arquivos' são obrigatórios")
    for r in data.get("infraestrutura") or []:
        rid = r.get("id", "?")
        if r.get("status") not in ("proposta", "aprovada"):
            problems.append(f"{rid}: status deve ser 'proposta' ou 'aprovada'")
        if r.get("acao") not in ACTIONS:
            problems.append(f"{rid}: acao deve ser uma de {list(ACTIONS)}")
        extra = set(r.get("quando") or {}) - CONDITION_KEYS
        if extra:
            problems.append(f"{rid}: condições desconhecidas {sorted(extra)}")
        if r.get("acao") == "instalacao":
            try:
                decl = schema.declaration_by_name(r.get("classe") or "")
            except RuntimeError:
                decl = None
            if decl is None or not _is_subtype(decl, "IfcFacility"):
                problems.append(f"{rid}: classe {r.get('classe')!r} não é IfcFacility (ou subtipo) no IFC 4.3")
            elif r.get("predefined_type") and r["predefined_type"] not in (predefined_items(r["classe"]) or ()):
                problems.append(f"{rid}: PredefinedType {r['predefined_type']!r} inválido para {r['classe']}")
    return problems


def _is_subtype(decl, name: str) -> bool:
    while decl is not None:
        if decl.name() == name:
            return True
        decl = decl.supertype()
    return False


def building_rule(filename: str, rules: dict) -> dict | None:
    name = Path(filename).name.upper()
    return next((p for p in rules.get("predios") or [] if fnmatch.fnmatch(name, p["arquivos"].upper())), None)


def with_overrides(rules: dict, overrides: dict | None) -> dict:
    """Cópia das regras com os nomes ajustados na tela ({projeto, site, predios: {id: {...}}})."""
    out = copy.deepcopy(rules)
    if not overrides:
        return out
    for key in ("projeto", "site"):
        for field, value in (overrides.get(key) or {}).items():
            if field in NAME_FIELDS:
                out.setdefault(key, {})[field] = (value or "").strip()
    by_id = {p["id"]: p for p in out.get("predios") or []}
    for pid, values in (overrides.get("predios") or {}).items():
        if pid in by_id:
            for field, value in values.items():
                if field in NAME_FIELDS:
                    by_id[pid][field] = (value or "").strip()
    return out


def save_names(path: str | Path, rules: dict) -> None:
    """Grava os nomes (projeto, site, prédios) no arquivo de regras, mantendo o restante."""
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    for key in ("projeto", "site"):
        data[key] = {f: (rules.get(key) or {}).get(f, "") for f in NAME_FIELDS}
    names = {p["id"]: p for p in rules.get("predios") or []}
    for p in data.get("predios") or []:
        for f in NAME_FIELDS:
            p[f] = (names.get(p["id"]) or {}).get(f, p.get(f, ""))
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def _label(entity) -> str:
    return f"{entity.is_a()} '{entity.Name or ''}'"


class Restructurer:
    def __init__(self, f: ifcopenshell.file, rules: dict, context: dict, include_proposed: bool):
        self.f = f
        self.rules = rules
        self.context = context
        self.include_proposed = include_proposed
        self.changes: list[dict] = []
        self.pending: list[dict] = []

    def change(self, entity, campo: str, antes, depois, rule: dict) -> None:
        self.changes.append({"global_id": entity.GlobalId, "classe": entity.is_a(), "nome": entity.Name,
                             "campo": campo, "antes": antes, "depois": depois,
                             "regra": rule["id"], "status_regra": rule["status"]})

    def pend(self, entity, detalhe: str, rule: dict) -> None:
        self.pending.append({"global_id": entity.GlobalId if entity else "", "classe": entity.is_a() if entity else "",
                             "nome": entity.Name if entity else "", "motivo": UNRECOGNIZED, "detalhe": detalhe,
                             "regra": rule["id"], "predefined_type": "", "object_type": "", "tipo_nome": "",
                             "layers": "", "nome_normalizado": ""})

    def run(self) -> tuple[list[dict], list[dict]]:
        allowed = ("aprovada", "proposta") if self.include_proposed else ("aprovada",)
        for rule in self.rules.get("infraestrutura") or []:
            if rule["status"] in allowed and matches(rule["quando"], self.context):
                getattr(self, rule["acao"])(rule)
        self.names()
        return self.changes, self.pending

    # ações ------------------------------------------------------------
    def building_para_site(self, rule: dict) -> None:
        sites, buildings = self.f.by_type("IfcSite"), self.f.by_type("IfcBuilding")
        if sites and not buildings:
            return  # já está no IfcSite
        if sites or len(buildings) != 1 or self.f.by_type("IfcBuildingStorey"):
            self.pend(buildings[0] if buildings else None,
                      f"esperado um IfcBuilding sem IfcSite nem pavimentos; arquivo tem {len(sites)} IfcSite, "
                      f"{len(buildings)} IfcBuilding, {len(self.f.by_type('IfcBuildingStorey'))} pavimentos", rule)
            return
        building = buildings[0]
        dropped = {k: v for k, v in building.get_info(recursive=False).items()
                   if k in ("ElevationOfRefHeight", "ElevationOfTerrain", "BuildingAddress") and v is not None}
        site = reclassify(building, "IfcSite")
        self.change(site, "classe", "IfcBuilding", "IfcSite", rule)
        for attr, value in sorted(dropped.items()):
            self.change(site, attr, value if not isinstance(value, ifcopenshell.entity_instance) else value.is_a(),
                        None, rule)

    def instalacao(self, rule: dict) -> None:
        sites = self.f.by_type("IfcSite")
        if len(sites) != 1:
            self.pend(None, f"{rule['classe']}: esperado um IfcSite, arquivo tem {len(sites)}", rule)
            return
        site = sites[0]
        for rel in site.IsDecomposedBy or ():
            if any(o.is_a(rule["classe"]) and o.Name == rule.get("nome") for o in rel.RelatedObjects):
                return  # já existe (arquivo já normalizado)
        facility = self.f.create_entity(
            rule["classe"], GlobalId=stable_guid(site.GlobalId, "instalacao", rule["id"]), Name=rule.get("nome"),
            ObjectPlacement=self.f.create_entity(
                "IfcLocalPlacement", PlacementRelTo=site.ObjectPlacement,
                RelativePlacement=self.f.create_entity(
                    "IfcAxis2Placement3D", Location=self.f.create_entity("IfcCartesianPoint", (0.0, 0.0, 0.0)))),
            CompositionType="ELEMENT", PredefinedType=rule.get("predefined_type"),
        )
        self.f.create_entity("IfcRelAggregates", GlobalId=stable_guid(site.GlobalId, "agregacao", rule["id"]),
                             RelatingObject=site, RelatedObjects=[facility])
        self.change(facility, "criado", None, f"agregado a {_label(site)}", rule)
        for rel in list(site.ContainsElements or ()):
            rel.RelatingStructure = facility
            for element in sorted(rel.RelatedElements, key=lambda e: e.GlobalId):
                self.change(element, "Estrutura espacial", _label(site), _label(facility), rule)

    def names(self) -> None:
        targets = [(self.f.by_type("IfcProject"), self.rules.get("projeto") or {}, "projeto"),
                   (self.f.by_type("IfcSite"), self.rules.get("site") or {}, "site")]
        predio = building_rule(self.context.get("arquivo", ""), self.rules)
        if predio:
            targets.append((self.f.by_type("IfcBuilding"), predio, f"prédio {predio['id']}"))
        for entities, values, label in targets:
            wanted = {attr: (values.get(field) or "").strip() for field, attr in NAME_FIELDS.items()}
            wanted = {k: v for k, v in wanted.items() if v}
            if not wanted or not entities:
                continue
            if len(entities) > 1:
                self.pend(entities[0], f"nomes de {label}: {len(entities)} entidades {entities[0].is_a()} no arquivo",
                          NAMES_RULE)
                continue
            entity = entities[0]
            for attr, value in wanted.items():
                if getattr(entity, attr) != value:
                    self.change(entity, attr, getattr(entity, attr), value, NAMES_RULE)
                    setattr(entity, attr, value)


def restructure(f: ifcopenshell.file, rules: dict, context: dict, include_proposed: bool = False
                ) -> tuple[list[dict], list[dict]]:
    """Aplica as regras de estrutura. Devolve (alterações, pendências) no formato do relatório."""
    return Restructurer(f, rules, context, include_proposed).run()
