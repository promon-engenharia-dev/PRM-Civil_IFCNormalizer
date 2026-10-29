"""Aplicação das regras ao IFC (edita, nunca recria geometria).

Fluxo por elemento:
1. Levanta os "fatos" do elemento no estado original (classe, nome, layers, Psets...).
2. Avalia todas as regras ativas, na ordem do arquivo. Para cada campo, a primeira
   regra que casa define o valor; regras seguintes com valor diferente viram conflito.
3. Aplica o plano (classe, PredefinedType, ObjectType, Name, Description, Psets) e
   registra cada alteração com antes/depois e a regra aplicada.
4. O que não foi resolvido por regra vira pendência — nada é decidido por suposição.
"""

from __future__ import annotations

import contextlib
import re
import unicodedata
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import ifcopenshell
import ifcopenshell.api
import ifcopenshell.guid
import ifcopenshell.util.attribute
import ifcopenshell.util.element
import ifcopenshell.util.unit

from . import __version__
from .profile import detect_discipline, detect_origin, normalize_name
from .geometry import GeometrySettings, classify_elements
from .rules import UNITS, RuleSet, precedence, predefined_items, type_class_for, uses_geometry

# Motivos de pendência (texto do relatório)
GENERIC_CLASS = "classe_generica_sem_regra"
NO_PREDEFINED = "sem_predefined_type"
CONFLICT = "conflito_entre_regras"
TYPE_MISMATCH = "tipo_incompativel_com_classe"
MISSING_SOURCE = "valor_de_origem_ausente"
BAD_VALUE = "valor_nao_convertido"
USERDEFINED_NO_OBJECT_TYPE = "userdefined_sem_object_type"

_GUID_NAMESPACE = uuid.UUID("5b0c7a2e-6f1d-4c3e-9a7b-2f1e0d9c8b7a")  # fixo: ifc-normalizer
_PLACEHOLDER = re.compile(r"\{([^{}]+)\}")
_SIMPLE_FIELDS = ("classe", "predefined_type", "object_type", "nome", "descricao")
_IFC_ATTRIBUTE = {"object_type": "ObjectType", "nome": "Name", "descricao": "Description"}


@dataclass
class NormalizationResult:
    changes: list[dict] = field(default_factory=list)
    pending: list[dict] = field(default_factory=list)
    geometry_review: list[dict] = field(default_factory=list)
    rule_usage: dict[str, dict] = field(default_factory=dict)
    context: dict = field(default_factory=dict)


# ---------------------------------------------------------------- fatos

def _fold(text: Any) -> str:
    if text is None:
        return ""
    s = unicodedata.normalize("NFD", str(text))
    return "".join(c for c in s if not unicodedata.combining(c)).casefold().strip()


def _layers(element) -> list[str]:
    rep = getattr(element, "Representation", None)
    names: set[str] = set()
    if not rep:
        return []
    for r in rep.Representations or ():
        for item in [r, *(r.Items or ())]:
            for la in getattr(item, "LayerAssignment", None) or getattr(item, "LayerAssignments", None) or ():
                names.add(la.Name or "")
    return sorted(names)


def element_facts(element, context: dict) -> dict:
    etype = ifcopenshell.util.element.get_type(element)
    props: dict[str, Any] = {}
    for pset, values in ifcopenshell.util.element.get_psets(element).items():
        for name, value in values.items():
            if name != "id":
                props[f"{pset}.{name}"] = value
    return {
        **context,
        "classe": element.is_a(),
        "predefined_type": getattr(element, "PredefinedType", None) or "",
        "nome": element.Name or "",
        "descricao": element.Description or "",
        "object_type": getattr(element, "ObjectType", None) or "",
        "tipo_nome": (etype.Name or "") if etype else "",
        "tipo_classe": etype.is_a() if etype else "",
        "layer": _layers(element),
        "propriedades": props,
    }


# ------------------------------------------------------------ condições

def _match_value(actual: Any, matcher: Any) -> bool:
    if isinstance(actual, list):  # layers: basta um casar; 'vazio' olha a lista
        if isinstance(matcher, dict) and "vazio" in matcher:
            return (len(actual) == 0) == matcher["vazio"]
        return any(_match_value(a, matcher) for a in actual)
    if not isinstance(matcher, dict):
        return _fold(actual) == _fold(matcher)
    op, arg = next(iter(matcher.items()))
    a = _fold(actual)
    if op == "igual":
        return a == _fold(arg)
    if op == "em":
        return a in {_fold(x) for x in arg}
    if op == "contem":
        return _fold(arg) in a
    if op == "comeca_com":
        return a.startswith(_fold(arg))
    if op == "regex":
        return re.search(arg, "" if actual is None else str(actual), re.IGNORECASE) is not None
    if op == "vazio":
        return (a == "") == arg
    raise ValueError(f"comparador desconhecido: {op}")


def _without_geometry(cond: dict) -> dict:
    """Condição sem as partes geométricas (ramo 'ou' só geométrico vira 'qualquer')."""
    out = {k: v for k, v in cond.items() if k not in ("geometria", "ou")}
    if "ou" in cond:
        branches = [_without_geometry(sub) for sub in cond["ou"]]
        if all(branches):  # algum ramo vazio = sempre verdadeiro: dispensa o 'ou'
            out["ou"] = branches
    return out


def matches(cond: dict, facts: dict) -> bool:
    for key, expected in cond.items():
        if key == "ou":
            if not any(matches(sub, facts) for sub in expected):
                return False
        elif key == "propriedades":
            for prop, matcher in expected.items():
                if not _match_value(facts["propriedades"].get(prop), matcher):
                    return False
        elif key in ("origem", "disciplina", "classe"):
            options = expected if isinstance(expected, list) else [expected]
            if _fold(facts.get(key)) not in {_fold(o) for o in options}:
                return False
        elif not _match_value(facts.get(key), expected):
            return False
    return True


# ---------------------------------------------------------------- plano

def _render(template: Any, facts: dict) -> Any:
    if not isinstance(template, str):
        return template

    def repl(m):
        key = m.group(1)
        if key.startswith("prop:"):
            value = facts["propriedades"].get(key[5:])
        else:
            value = facts.get(key)
        return "" if value is None else str(value)

    return _PLACEHOLDER.sub(repl, template)


def build_plan(facts: dict, rules: list[dict]) -> tuple[dict, list[dict], list[str], list[dict]]:
    """Plano {campo: (valor, regra)}; conflitos; ids das regras que casaram; sobreposições.

    `rules` vem ordenado por precedência (atributos antes de geometria). Regra de nível
    inferior que disputa um campo já definido não é conflito: é sobreposta (precedência
    decidida) e fica registrada para revisão.
    """
    plan: dict[str, tuple[Any, dict]] = {}
    conflicts: list[dict] = []
    overridden: list[dict] = []
    matched: list[str] = []
    for rule in rules:
        if not matches(rule["quando"], facts):
            continue
        matched.append(rule["id"])
        action = rule["entao"]
        fields: dict[str, Any] = {k: _render(action[k], facts) for k in _SIMPLE_FIELDS if k in action}
        for pset, props in (action.get("propriedades") or {}).items():
            for prop, spec in props.items():
                fields[f"pset:{pset}.{prop}"] = spec
        for key, value in fields.items():
            if key not in plan:
                plan[key] = (value, rule)
            elif plan[key][0] != value:
                entry = {"campo": key, "regra_vencedora": plan[key][1]["id"], "valor_vencedor": plan[key][0],
                         "regra_ignorada": rule["id"], "valor_ignorado": value}
                if precedence(rule) > precedence(plan[key][1]):
                    overridden.append(entry)
                else:
                    conflicts.append(entry)
    return plan, conflicts, matched, overridden


# ---------------------------------------------------------------- ações

def reclassify(element, new_class: str):
    """Troca a classe preservando GlobalId, atributos compatíveis e todas as relações."""
    f = element.file
    schema = ifcopenshell.ifcopenshell_wrapper.schema_by_name(f.schema_identifier)
    target = schema.declaration_by_name(new_class)
    info = element.get_info(recursive=False)
    attrs = {}
    for attr in target.all_attributes():
        name = attr.name()
        value = info.get(name)
        if value is None:
            continue
        if ifcopenshell.util.attribute.get_primitive_type(attr) == "enum":
            if value not in ifcopenshell.util.attribute.get_enum_items(attr):
                continue
        attrs[name] = value
    # O GlobalId só é atribuído depois de remover o original: o índice de GlobalId do
    # IfcOpenShell perderia a entrada se as duas instâncias coexistissem com o mesmo valor.
    guid = attrs.pop("GlobalId")
    new_element = f.create_entity(new_class, **attrs)
    ifcopenshell.util.element.replace_element(element, new_element)
    f.remove(element)
    new_element.GlobalId = guid
    return new_element


def _occurrence_pset(element, name: str):
    for rel in getattr(element, "IsDefinedBy", ()) or ():
        if rel.is_a("IfcRelDefinesByProperties"):
            pdef = rel.RelatingPropertyDefinition
            if pdef.is_a("IfcPropertySet") and pdef.Name == name:
                return pdef
    return None


def _typed_value(f, measure: str | None, raw: Any, unit: str | None, length_scale: float):
    """Converte o valor para o tipo IFC definido em definicoes_pset (ValueError se não der)."""
    if measure is None:
        return raw
    if measure in ("IfcText", "IfcLabel", "IfcIdentifier"):
        return f.create_entity(measure, str(raw))
    if measure == "IfcBoolean":
        return f.create_entity(measure, bool(raw))
    number = float(str(raw).replace(",", "."))
    if unit:
        factor = UNITS[unit] / length_scale  # metros-da-origem -> unidade do arquivo
        if measure == "IfcLengthMeasure":
            number *= factor
        elif measure == "IfcAreaMeasure":
            number *= factor ** 2
        elif measure == "IfcVolumeMeasure":
            number *= factor ** 3
    if measure in ("IfcInteger", "IfcCountMeasure"):
        if number != int(number):
            raise ValueError(f"{raw!r} não é inteiro")
        return f.create_entity(measure, int(number))
    return f.create_entity(measure, number)


def stable_guid(*parts: str) -> str:
    """GlobalId IFC determinístico a partir de um texto (UUID5)."""
    return ifcopenshell.guid.compress(uuid.uuid5(_GUID_NAMESPACE, "|".join(parts)).hex)


def _wrapped(v):
    return v.wrappedValue if isinstance(v, ifcopenshell.entity_instance) else v


class Normalizer:
    def __init__(self, f: ifcopenshell.file, ruleset: RuleSet, include_proposed: bool, context: dict):
        self.f = f
        self.ruleset = ruleset
        self.rules = ruleset.active(include_proposed)
        self.context = context
        self.length_scale = ifcopenshell.util.unit.calculate_unit_scale(f)
        self.result = NormalizationResult(context=context)
        for r in ruleset.rules:
            self.result.rule_usage[r["id"]] = {
                "regra": r["id"], "status": r["status"], "descricao": r.get("descricao", ""),
                "ativa": r in self.rules, "elementos_casados": 0, "alteracoes": 0,
            }
        self.ignore = tuple(ruleset.pending.get("ignorar_classes", []))
        self._type_candidates: dict[str, list[dict]] = {}
        self._geo_rule_ids = {r["id"] for r in self.rules if uses_geometry(r["quando"])}

    # registro --------------------------------------------------------
    def _row(self, element, **extra) -> dict:
        return {"global_id": element.GlobalId, "classe": element.is_a(), "nome": element.Name, **extra}

    def change(self, element, campo: str, antes: Any, depois: Any, rule: dict) -> None:
        self.result.changes.append(
            self._row(element, campo=campo, antes=_wrapped(antes), depois=_wrapped(depois),
                      regra=rule["id"], status_regra=rule["status"])
        )
        self.result.rule_usage[rule["id"]]["alteracoes"] += 1

    def pend(self, element, motivo: str, detalhe: str = "", regra: str = "", facts: dict | None = None) -> None:
        facts = facts or {}
        self.result.pending.append(
            self._row(
                element, motivo=motivo, detalhe=detalhe, regra=regra,
                predefined_type=getattr(element, "PredefinedType", None) or "",
                object_type=getattr(element, "ObjectType", None) or "",
                tipo_nome=facts.get("tipo_nome", ""), layers=" | ".join(facts.get("layer", [])),
                nome_normalizado=normalize_name(element.Name),
            )
        )

    # execução --------------------------------------------------------
    def run(self) -> NormalizationResult:
        elements = sorted(
            (e for e in self.f.by_type("IfcElement") if not any(e.is_a(c) for c in self.ignore)),
            key=lambda e: e.GlobalId,
        )
        # Fatos do estado original de todos os elementos antes de qualquer alteração.
        all_facts = [element_facts(e, self.context) for e in elements]
        geo = self.compute_geometry(elements, all_facts)
        for element, facts in zip(elements, all_facts):
            facts["geometria"] = geo.get(element.GlobalId, {}).get("orientacao", "")
            plan, conflicts, matched, overridden = build_plan(facts, self.rules)
            for rid in matched:
                self.result.rule_usage[rid]["elementos_casados"] += 1
            for c in conflicts:
                self.pend(element, CONFLICT, f"{c['campo']}: {c['regra_vencedora']}={c['valor_vencedor']!r} "
                          f"x {c['regra_ignorada']}={c['valor_ignorado']!r}", c["regra_ignorada"], facts)
            guid = element.GlobalId
            element = self.apply(element, plan, facts)
            self.check_pending(element, plan, facts)
            if guid in geo:
                self.review_geometry(element, facts, geo[guid], matched, overridden)
        self.fix_types()
        return self.result

    def compute_geometry(self, elements: list, all_facts: list[dict]) -> dict[str, dict]:
        """Classificação geométrica só dos candidatos das regras geométricas ativas.

        Candidato = elemento que satisfaz todas as outras condições de alguma regra
        geométrica (disciplina, classe, nome...). Triangular só o necessário evita
        processar malhas enormes que nenhuma regra usaria (ex.: drenagem do Civil 3D).
        """
        conditions = [_without_geometry(r["quando"]) for r in self.rules if r["id"] in self._geo_rule_ids]
        if not conditions:
            return {}
        candidates = [e for e, facts in zip(elements, all_facts) if any(matches(c, facts) for c in conditions)]
        cfg = GeometrySettings.from_rules(self.ruleset.geometry)
        return classify_elements(self.f, candidates, cfg)

    def review_geometry(self, element, facts: dict, geo: dict, matched: list[str], overridden: list[dict]) -> None:
        geo_ids = [rid for rid in matched if rid in self._geo_rule_ids]
        losers = {o["regra_ignorada"]: o["regra_vencedora"] for o in overridden}
        if not geo_ids:
            situacao = "nenhuma regra geométrica casou"
        elif all(rid in losers for rid in geo_ids):
            situacao = "sobreposta por " + ", ".join(sorted({losers[rid] for rid in geo_ids}))
        else:
            situacao = "aplicada"
        self.result.geometry_review.append({
            "global_id": element.GlobalId, "nome": element.Name, "tipo_nome": facts.get("tipo_nome", ""),
            "classe_original": facts["classe"], "orientacao": geo.get("orientacao"),
            "angulo_vertical_graus": geo.get("angulo_vertical_graus"),
            "comprimento_m": geo.get("comprimento_m"), "largura_m": geo.get("largura_m"),
            "espessura_m": geo.get("espessura_m"),
            "regra_geometrica": ", ".join(geo_ids), "situacao": situacao,
            "classe_final": element.is_a(), "predefined_final": getattr(element, "PredefinedType", None) or "",
            "conferido": "",
        })

    def fix_types(self) -> None:
        """Ajusta o tipo às ocorrências reclassificadas.

        Todas as ocorrências pedem a mesma classe de tipo: o tipo é reclassificado. Tipo compartilhado
        com ocorrências de outras classes (ex.: IfcBeamType do Tekla usado por vigas e porcas): é
        dividido em um tipo por classe, com o mesmo nome e os mesmos Psets (decisão de 2026-10-28).
        """
        from .repair import align_type

        enabled = self.ruleset.types.get("reclassificar_com_elemento", False)
        for type_guid in sorted(self._type_candidates):
            entries = self._type_candidates[type_guid]
            etype = self.f.by_guid(type_guid)
            target = entries[0]["classe"]
            reason = None
            if not enabled:
                reason = "reclassificação de tipos desligada (tipos.reclassificar_com_elemento)"
            elif type_class_for(target) is None:
                reason = f"{target} não tem classe de tipo no IFC 4.3"
            if reason:
                for e in entries:
                    self.pend(self.f.by_guid(e["global_id"]), TYPE_MISMATCH,
                              f"tipo {etype.is_a()} '{etype.Name}': {reason}", e["regra"]["id"], e["fatos"])
                continue
            rule = entries[0]["regra"]
            for entity, campo, antes, depois in align_type(self.f, etype):
                self.change(entity, campo, antes, depois, rule)

    def apply(self, element, plan: dict, facts: dict):
        if "classe" in plan and plan["classe"][0] != element.is_a():
            new_class, rule = plan["classe"]
            old_class = element.is_a()
            element = reclassify(element, new_class)
            self.change(element, "classe", old_class, new_class, rule)
            etype = ifcopenshell.util.element.get_type(element)
            expected = type_class_for(new_class)
            if etype and not (expected and etype.is_a(expected)):
                # Decidido em fix_types(), quando todas as ocorrências do tipo já foram vistas.
                self._type_candidates.setdefault(etype.GlobalId, []).append(
                    {"global_id": element.GlobalId, "classe": new_class, "regra": rule, "fatos": facts}
                )

        if "predefined_type" in plan:
            value, rule = plan["predefined_type"]
            items = predefined_items(element.is_a())
            if items is None or value not in items:
                self.pend(element, BAD_VALUE, f"PredefinedType {value!r} inválido para {element.is_a()}",
                          rule["id"], facts)
            elif element.PredefinedType != value:
                self.change(element, "PredefinedType", element.PredefinedType, value, rule)
                element.PredefinedType = value

        for key, attr in _IFC_ATTRIBUTE.items():
            if key in plan and hasattr(element, attr):
                value, rule = plan[key]
                if getattr(element, attr) != value:
                    self.change(element, attr, getattr(element, attr), value, rule)
                    setattr(element, attr, value)

        psets: dict[str, dict[str, tuple[Any, dict]]] = {}
        for key, (spec, rule) in plan.items():
            if key.startswith("pset:"):
                pset, prop = key[5:].split(".", 1)
                psets.setdefault(pset, {})[prop] = (spec, rule)
        for pset_name, props in sorted(psets.items()):
            self.write_pset(element, pset_name, props, facts)
        return element

    def write_pset(self, element, pset_name: str, props: dict, facts: dict) -> None:
        definitions = self.ruleset.pset_definitions.get(pset_name, {})
        pset = _occurrence_pset(element, pset_name)
        current = ifcopenshell.util.element.get_pset(element, pset_name) if pset else {}
        values: dict[str, Any] = {}
        for prop, (spec, rule) in sorted(props.items()):
            if isinstance(spec, dict):
                raw = facts["propriedades"].get(spec["copiar"])
                unit = spec.get("unidade_origem")
                if raw is None or str(raw).strip() == "":
                    self.pend(element, MISSING_SOURCE, f"{pset_name}.{prop} <- {spec['copiar']}", rule["id"], facts)
                    continue
            else:
                raw, unit = _render(spec, facts), None
            try:
                value = _typed_value(self.f, definitions.get(prop), raw, unit, self.length_scale)
            except (ValueError, TypeError) as exc:
                self.pend(element, BAD_VALUE, f"{pset_name}.{prop} = {raw!r}: {exc}", rule["id"], facts)
                continue
            before = current.get(prop) if current else None
            if before == _wrapped(value):
                continue
            values[prop] = value
            self.change(element, f"{pset_name}.{prop}", before, value, rule)
        if not values:
            return
        if pset is None:
            pset = ifcopenshell.api.run("pset.add_pset", self.f, product=element, name=pset_name)
            # GlobalId derivado (não aleatório) para manter a saída determinística.
            pset.GlobalId = stable_guid(element.GlobalId, "pset", pset_name)
            for rel in pset.DefinesOccurrence or ():
                rel.GlobalId = stable_guid(element.GlobalId, "rel", pset_name)
        ifcopenshell.api.run("pset.edit_pset", self.f, pset=pset, properties=values)

    def check_pending(self, element, plan: dict, facts: dict) -> None:
        generic = self.ruleset.pending.get("classes_genericas", [])
        if element.is_a() in generic and "classe" not in plan:
            # A classificação resolve também o PredefinedType: uma pendência só.
            self.pend(element, GENERIC_CLASS, "", "", facts)
            return
        if self.ruleset.pending.get("exigir_predefined_type", True):
            items = predefined_items(element.is_a())
            value = getattr(element, "PredefinedType", None)
            etype = ifcopenshell.util.element.get_type(element)
            type_value = getattr(etype, "PredefinedType", None) if etype is not None else None
            if value is None and type_value not in (None, "NOTDEFINED"):
                return  # OJT001: o tipo define o PredefinedType; a ocorrência fica vazia
            if items is not None and value in (None, "NOTDEFINED") and not (
                "predefined_type" in plan and plan["predefined_type"][0] == "NOTDEFINED"
            ):
                self.pend(element, NO_PREDEFINED, f"{element.is_a()} com PredefinedType {value or 'vazio'}", "", facts)
            if value == "USERDEFINED" and not getattr(element, "ObjectType", None):
                self.pend(element, USERDEFINED_NO_OBJECT_TYPE, "", "", facts)


def file_context(f: ifcopenshell.file, filename: str) -> dict:
    fn = f.header.file_name
    app = f"{fn.originating_system or ''} {fn.preprocessor_version or ''}"
    return {"arquivo": Path(filename).name, "origem": detect_origin(app), "disciplina": detect_discipline(filename)}


@contextlib.contextmanager
def _without_owner_history():
    """Impede a API do IfcOpenShell de criar IfcOwnerHistory com a hora atual.

    Por padrão a API cria um registro de autoria com time.time() para cada entidade nova
    (Pset, relação) quando o arquivo tem usuário e aplicativo cadastrados — o que torna a
    saída diferente a cada execução. No IFC 4.3 o OwnerHistory é opcional; a rastreabilidade
    fica no relatório de alterações.
    """
    import ifcopenshell.api.owner.settings as owner

    saved = owner.get_user, owner.get_application
    owner.get_user = owner.get_application = lambda ifc: None
    try:
        yield
    finally:
        owner.get_user, owner.get_application = saved


def normalize(f: ifcopenshell.file, ruleset: RuleSet, filename: str, include_proposed: bool = False
              ) -> NormalizationResult:
    if f.schema_identifier != "IFC4X3_ADD2":
        raise ValueError(f"Normalização exige IFC4X3_ADD2 (arquivo em {f.schema_identifier}); migre antes.")
    with _without_owner_history():
        result = Normalizer(f, ruleset, include_proposed, file_context(f, filename)).run()
    desc = list(f.header.file_description.description)
    mode = "aprovadas+propostas" if include_proposed else "aprovadas"
    desc.append(f"ifc-normalizer {__version__} [Normalize: regras {ruleset.version} ({mode})]")
    f.header.file_description.description = tuple(desc)
    return result
