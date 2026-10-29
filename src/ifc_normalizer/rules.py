"""Leitura e validação das regras em rules/ (normalização em JSON)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import ifcopenshell
import ifcopenshell.util.attribute
import ifcopenshell.util.schema

from .paths import rules_dir

TARGET_SCHEMA = "IFC4X3_ADD2"
DEFAULT_NORMALIZATION_RULES = rules_dir() / "normalizacao.json"

STATUSES = ("proposta", "aprovada")
TEXT_FIELDS = ("arquivo", "nome", "descricao", "object_type", "tipo_nome", "layer", "predefined_type")
LIST_FIELDS = ("origem", "disciplina", "classe")
CONDITION_KEYS = set(TEXT_FIELDS) | set(LIST_FIELDS) | {"propriedades", "ou", "geometria"}
GEOMETRY_KEYS = {"tolerancia_angular_graus", "razao_alongamento_min", "razao_espessura_max"}
MATCHER_OPS = {"igual", "em", "regex", "comeca_com", "contem", "vazio"}
ACTION_KEYS = {"classe", "predefined_type", "object_type", "nome", "descricao", "propriedades"}
UNITS = {"m": 1.0, "cm": 0.01, "mm": 0.001}


class RulesError(ValueError):
    """Arquivo de regras inválido (a lista de problemas está em .problems)."""

    def __init__(self, problems: list[str]):
        super().__init__("Regras inválidas:\n  - " + "\n  - ".join(problems))
        self.problems = problems


@dataclass
class RuleSet:
    version: str
    rules: list[dict]
    pset_definitions: dict[str, dict[str, str]]
    pending: dict
    types: dict = field(default_factory=dict)
    geometry: dict = field(default_factory=dict)
    source: str = ""
    warnings: list[str] = field(default_factory=list)

    def active(self, include_proposed: bool) -> list[dict]:
        """Regras ativas, em ordem de precedência: atributos antes de geometria (estável)."""
        allowed = STATUSES if include_proposed else ("aprovada",)
        rules = [r for r in self.rules if r["status"] in allowed]
        return sorted(rules, key=precedence)


def uses_geometry(cond: dict) -> bool:
    return "geometria" in cond or any(uses_geometry(sub) for sub in cond.get("ou", []))


def precedence(rule: dict) -> int:
    """0 = nome/atributos; 1 = heurística geométrica (precedência decidida no CLAUDE.md)."""
    return 1 if uses_geometry(rule.get("quando") or {}) else 0


def _schema():
    return ifcopenshell.ifcopenshell_wrapper.schema_by_name(TARGET_SCHEMA)


def predefined_items(ifc_class: str) -> tuple[str, ...] | None:
    """Valores válidos de PredefinedType para a classe (None se a classe não tem o atributo)."""
    decl = _schema().declaration_by_name(ifc_class)
    for attr in decl.all_attributes():
        if attr.name() == "PredefinedType":
            return tuple(ifcopenshell.util.attribute.get_enum_items(attr))
    return None


def type_class_for(ifc_class: str) -> str | None:
    """Classe de tipo correspondente (IfcBeam -> IfcBeamType), se existir no IFC 4.3."""
    name = f"{ifc_class}Type"
    try:
        decl = _schema().declaration_by_name(name)
    except RuntimeError:
        return None
    if not ifcopenshell.util.schema.is_a(decl, "IfcTypeObject") or decl.is_abstract():
        return None
    return name


def _class_problems(ifc_class: str, where: str, must_be_element: bool) -> list[str]:
    try:
        decl = _schema().declaration_by_name(ifc_class)
    except RuntimeError:
        return [f"{where}: classe '{ifc_class}' não existe no {TARGET_SCHEMA}"]
    if must_be_element and not ifcopenshell.util.schema.is_a(decl, "IfcElement"):
        return [f"{where}: '{ifc_class}' não é subtipo de IfcElement"]
    if must_be_element and decl.is_abstract():
        return [f"{where}: '{ifc_class}' é abstrata"]
    return []


def _matcher_problems(value, where: str) -> list[str]:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return []
    if not isinstance(value, dict) or len(value) != 1 or next(iter(value)) not in MATCHER_OPS:
        return [f"{where}: comparador inválido {value!r} (use texto ou um de {sorted(MATCHER_OPS)})"]
    op, arg = next(iter(value.items()))
    if op == "em" and not isinstance(arg, list):
        return [f"{where}: 'em' espera uma lista"]
    if op == "vazio" and not isinstance(arg, bool):
        return [f"{where}: 'vazio' espera true/false"]
    if op == "regex":
        try:
            re.compile(arg)
        except re.error as exc:
            return [f"{where}: regex inválida ({exc})"]
    return []


def _condition_problems(cond: dict, where: str) -> list[str]:
    problems = []
    if not isinstance(cond, dict):
        return [f"{where}: 'quando' deve ser um objeto"]
    for key, value in cond.items():
        if key not in CONDITION_KEYS:
            problems.append(f"{where}: condição desconhecida '{key}'")
        elif key == "ou":
            if not isinstance(value, list) or not value:
                problems.append(f"{where}: 'ou' espera uma lista de condições")
            else:
                for i, sub in enumerate(value):
                    problems += _condition_problems(sub, f"{where}.ou[{i}]")
        elif key == "propriedades":
            if not isinstance(value, dict):
                problems.append(f"{where}: 'propriedades' espera {{'Pset.Propriedade': comparador}}")
                continue
            for prop, matcher in value.items():
                if "." not in prop:
                    problems.append(f"{where}: propriedade '{prop}' deve ser 'Pset.Propriedade'")
                problems += _matcher_problems(matcher, f"{where}.propriedades[{prop}]")
        elif key == "classe":
            for c in value if isinstance(value, list) else [value]:
                problems += _class_problems(c, f"{where}.classe", must_be_element=False)
        elif key == "geometria":
            from .geometry import ORIENTATIONS

            options = value if isinstance(value, str) else (value.get("em") if isinstance(value, dict) else None)
            options = [options] if isinstance(options, str) else options
            if not isinstance(options, list) or not options:
                problems.append(f"{where}: 'geometria' espera texto ou {{'em': [...]}}")
            else:
                bad = [o for o in options if o not in ORIENTATIONS]
                if bad:
                    problems.append(f"{where}: orientação {bad} inválida (use {', '.join(ORIENTATIONS)})")
        elif key in LIST_FIELDS:
            if not isinstance(value, (str, list)):
                problems.append(f"{where}: '{key}' espera texto ou lista")
        else:
            problems += _matcher_problems(value, f"{where}.{key}")
    return problems


def _action_problems(rule: dict, pset_defs: dict, where: str) -> list[str]:
    action = rule.get("entao")
    if not isinstance(action, dict) or not action:
        return [f"{where}: 'entao' vazio"]
    problems = [f"{where}: ação desconhecida '{k}'" for k in action if k not in ACTION_KEYS]

    target = action.get("classe")
    if target:
        problems += _class_problems(target, f"{where}.entao.classe", must_be_element=True)

    pdt = action.get("predefined_type")
    if pdt:
        # Valida contra a classe de destino; sem troca de classe, contra as classes da condição.
        candidates = [target] if target else (
            rule["quando"].get("classe") if isinstance(rule["quando"].get("classe"), list)
            else [rule["quando"].get("classe")] if rule["quando"].get("classe") else []
        )
        if not candidates:
            problems.append(f"{where}: predefined_type sem classe definida (em 'entao' ou 'quando')")
        for c in candidates:
            if _class_problems(c, "", must_be_element=False):
                continue
            items = predefined_items(c)
            if items is None:
                problems.append(f"{where}: {c} não tem PredefinedType")
            elif pdt not in items:
                problems.append(f"{where}: PredefinedType '{pdt}' inválido para {c} (válidos: {', '.join(items)})")
        if pdt == "USERDEFINED" and not action.get("object_type"):
            problems.append(f"{where}: PredefinedType USERDEFINED exige object_type")

    for pset, props in (action.get("propriedades") or {}).items():
        if not isinstance(props, dict):
            problems.append(f"{where}: propriedades de '{pset}' devem ser um objeto")
            continue
        for prop, spec in props.items():
            if isinstance(spec, dict):
                if "copiar" not in spec or "." not in spec["copiar"]:
                    problems.append(f"{where}: {pset}.{prop}: 'copiar' deve ser 'Pset.Propriedade'")
                unit = spec.get("unidade_origem")
                if unit and unit not in UNITS:
                    problems.append(f"{where}: {pset}.{prop}: unidade_origem '{unit}' (use {', '.join(UNITS)})")
            if pset in pset_defs and prop not in pset_defs[pset]:
                problems.append(f"{where}: '{prop}' não está definida em definicoes_pset.{pset}")
    return problems


def validate(data: dict) -> tuple[list[str], list[str]]:
    """Retorna (problemas, avisos). Problemas impedem o uso das regras."""
    problems: list[str] = []
    warnings: list[str] = []
    if not isinstance(data.get("regras"), list):
        return ["'regras' deve ser uma lista"], warnings

    pset_defs = data.get("definicoes_pset") or {}
    for pset, props in pset_defs.items():
        for prop, measure in props.items():
            try:
                _schema().declaration_by_name(measure)
            except RuntimeError:
                problems.append(f"definicoes_pset.{pset}.{prop}: tipo '{measure}' não existe")

    for key, value in (data.get("tipos") or {}).items():
        if key != "reclassificar_com_elemento":
            problems.append(f"tipos: opção desconhecida '{key}'")
        elif not isinstance(value, bool):
            problems.append("tipos.reclassificar_com_elemento espera true/false")

    for key, value in (data.get("geometria") or {}).items():
        if key not in GEOMETRY_KEYS:
            problems.append(f"geometria: parâmetro desconhecido '{key}' (use {', '.join(sorted(GEOMETRY_KEYS))})")
        elif not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
            problems.append(f"geometria.{key} deve ser um número positivo")

    for c in (data.get("pendencias") or {}).get("classes_genericas", []):
        problems += _class_problems(c, "pendencias.classes_genericas", must_be_element=True)

    seen: set[str] = set()
    for i, rule in enumerate(data["regras"]):
        rid = rule.get("id") or f"regras[{i}]"
        where = f"regra {rid}"
        if not rule.get("id"):
            problems.append(f"{where}: sem 'id'")
        elif rid in seen:
            problems.append(f"{where}: id duplicado")
        seen.add(rid)
        if rule.get("status") not in STATUSES:
            problems.append(f"{where}: status deve ser {' ou '.join(STATUSES)}")
        if "tema" in rule and not isinstance(rule["tema"], str):
            problems.append(f"{where}: 'tema' deve ser texto")
        if "perguntas" in rule and not (
            isinstance(rule["perguntas"], list) and all(isinstance(p, str) for p in rule["perguntas"])
        ):
            problems.append(f"{where}: 'perguntas' deve ser uma lista de textos")
        if rule.get("status") == "proposta" and not rule.get("perguntas"):
            warnings.append(f"{where}: proposta sem 'perguntas' para a validação")
        if not rule.get("quando"):
            problems.append(f"{where}: sem 'quando' (regra sem condição se aplicaria a tudo)")
            continue
        problems += _condition_problems(rule["quando"], f"{where}.quando")
        problems += _action_problems(rule, pset_defs, where)
        if rule.get("status") == "aprovada" and not rule.get("avaliacao_bep"):
            warnings.append(f"{where}: aprovada sem registro em 'avaliacao_bep'")
        if rule.get("status") == "aprovada" and uses_geometry(rule["quando"]):
            warnings.append(f"{where}: regra geométrica aprovada – confirme a validação na aba Revisao_Geometrica")
    return problems, warnings


def load_normalization_rules(path: str | Path = DEFAULT_NORMALIZATION_RULES) -> RuleSet:
    path = Path(path)
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    problems, warnings = validate(data)
    if problems:
        raise RulesError(problems)
    return RuleSet(
        version=str(data.get("versao", "")),
        rules=data["regras"],
        pset_definitions=data.get("definicoes_pset") or {},
        pending=data.get("pendencias") or {},
        types=data.get("tipos") or {},
        geometry=data.get("geometria") or {},
        source=str(path),
        warnings=warnings,
    )
