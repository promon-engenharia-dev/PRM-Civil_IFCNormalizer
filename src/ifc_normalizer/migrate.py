"""Migração de schema para IFC4X3_ADD2 com registro de tudo o que mudou.

Etapas: IFC2X3 -> IFC4 -> IFC4X3_ADD2 (o IfcOpenShell só converte entre versões
vizinhas). A geometria e os GlobalId são copiados; cada classe convertida,
atributo descartado, valor padrão gerado ou entidade que falhou vira um evento
no relatório.
"""

from __future__ import annotations

import contextlib
import io
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import ifcopenshell
import ifcopenshell.util.schema
import yaml

from . import __version__
from .paths import rules_dir

TARGET_SCHEMA = "IFC4X3_ADD2"
DEFAULT_RULES = rules_dir() / "migracao.yaml"

_STEPS = {
    "IFC2X3": ["IFC4", TARGET_SCHEMA],
    "IFC4": [TARGET_SCHEMA],
    "IFC4X3": [],
}

# Tipos de evento (texto do relatório)
CLASS_CHANGED = "classe_convertida"
ATTR_DROPPED = "atributo_descartado"
ATTR_RENAMED = "atributo_mapeado"
VALUE_DROPPED = "valor_descartado"
DEFAULT_SET = "valor_padrao_gerado"
REQUIRED_EMPTY = "obrigatorio_sem_valor"
PREPROCESS_REMOVED = "entidade_substituida"
FAILED = "falha"


@dataclass
class MigrationLog:
    events: list[dict] = field(default_factory=list)

    def add(self, step: str, kind: str, element, **extra: Any) -> None:
        self.events.append(
            {
                "etapa": step,
                "tipo": kind,
                "step_id": element.id(),
                "classe_origem": element.is_a(),
                "global_id": getattr(element, "GlobalId", None) if element.is_a("IfcRoot") else None,
                "nome": getattr(element, "Name", None) if element.is_a("IfcRoot") else None,
                **extra,
            }
        )


@dataclass
class MigrationResult:
    file: ifcopenshell.file
    source_schema: str
    steps: list[str]
    log: MigrationLog
    checks: list[dict]


def load_rules(path: Path = DEFAULT_RULES) -> dict:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _short(value: Any) -> str:
    if isinstance(value, ifcopenshell.entity_instance):
        return f"#{value.id()}={value.is_a()}"
    if isinstance(value, (list, tuple)):
        text = ", ".join(_short(v) for v in value[:5])
        return f"({text}{', …' if len(value) > 5 else ''})"
    return str(value)[:200]


class RecordingMigrator(ifcopenshell.util.schema.Migrator):
    """Migrator do IfcOpenShell que registra cada perda/alteração em vez de imprimir."""

    def __init__(self, step: str, log: MigrationLog, rules: dict) -> None:
        super().__init__()
        self.step = step
        self.log = log
        self.class_overrides: dict[str, str] = rules.get("ifc4_para_ifc4x3", {}) or {}
        self.extra_defaults: dict[str, Any] = rules.get("valores_padrao", {}) or {}
        self.attribute_renames: dict[str, dict[str, str]] = rules.get("atributos_renomeados", {}) or {}

    # -- classes ---------------------------------------------------------
    def migrate_class(self, element, new_file):
        cls = element.is_a()
        target = self.class_overrides.get(cls) if new_file.schema == "IFC4X3" else None
        if target:
            new_element = new_file.create_entity(target)
        else:
            new_element = super().migrate_class(element, new_file)
        if new_element.is_a() != cls:
            self.log.add(self.step, CLASS_CHANGED, element, classe_destino=new_element.is_a())
        return new_element

    # -- atributos -------------------------------------------------------
    def _consumed_source_names(self, old_schema: str, new_schema: str, new_class: str) -> set[str]:
        key = {("IFC2X3", "IFC4"): ("IFC4", "IFC2X3"), ("IFC4", "IFC4X3"): ("IFC4X3", "IFC4")}.get(
            (old_schema, new_schema)
        )
        if not key:
            return set()
        return set(self.attributes_mapping[key].get(new_class, {}).values())

    def migrate_attributes(self, element, new_file, new_element, new_element_schema):
        new_element = super().migrate_attributes(element, new_file, new_element, new_element_schema)
        if element.id() == 0:
            return new_element
        new_names = {a.name() for a in new_element_schema.all_attributes()}
        consumed = self._consumed_source_names(element.file.schema, new_file.schema, new_element.is_a())
        consumed |= set(self.attribute_renames.get(element.is_a(), {}))
        old_decl = ifcopenshell.util.schema.get_declaration(element)
        for i, attr in enumerate(old_decl.all_attributes()):
            if old_decl.derived()[i]:
                continue
            name = attr.name()
            if name in new_names or name in consumed:
                continue
            value = element[i]
            if value is None:
                continue
            self.log.add(
                self.step, ATTR_DROPPED, element,
                classe_destino=new_element.is_a(), atributo=name, valor=_short(value),
            )
        return new_element

    def migrate_attribute(self, attribute, element, new_file, new_element, new_element_schema):
        name = attribute.name()
        renamed_from = {v: k for k, v in self.attribute_renames.get(element.is_a(), {}).items()}.get(name)
        if renamed_from and getattr(element, renamed_from, None) is not None:
            value = getattr(element, renamed_from)
            if ifcopenshell.util.schema._enum_value_outside_target(attribute, value):
                self.log.add(
                    self.step, VALUE_DROPPED, element,
                    classe_destino=new_element.is_a(), atributo=renamed_from, valor=_short(value),
                )
            else:
                setattr(new_element, name, value)
                self.log.add(
                    self.step, ATTR_RENAMED, element,
                    classe_destino=new_element.is_a(), atributo=f"{renamed_from} -> {name}", valor=_short(value),
                )
                return
        super().migrate_attribute(attribute, element, new_file, new_element, new_element_schema)
        new_value = getattr(new_element, name, None)
        if new_value is not None:
            return
        old_value = getattr(element, name, None) if hasattr(element, name) else None
        if old_value is not None:
            # Ex.: valor de enumeração que não existe no schema de destino.
            self.log.add(
                self.step, VALUE_DROPPED, element,
                classe_destino=new_element.is_a(), atributo=name, valor=_short(old_value),
            )
        if not attribute.optional():
            if name in self.extra_defaults:
                setattr(new_element, name, self.extra_defaults[name])
                self.log.add(
                    self.step, DEFAULT_SET, element,
                    classe_destino=new_element.is_a(), atributo=name, valor=str(self.extra_defaults[name]),
                )
            else:
                self.log.add(
                    self.step, REQUIRED_EMPTY, element, classe_destino=new_element.is_a(), atributo=name,
                )

    def generate_default_value(self, attribute, new_file):
        value = super().generate_default_value(attribute, new_file)
        if value is not None and self._current is not None:
            self.log.add(
                self.step, DEFAULT_SET, self._current, atributo=attribute.name(), valor=_short(value),
            )
        return value

    _current = None

    def migrate(self, element, new_file):
        previous, self._current = self._current, element
        try:
            return super().migrate(element, new_file)
        finally:
            self._current = previous


def _preprocess_events(old_file: ifcopenshell.file, new_schema: str, step: str, log: MigrationLog) -> None:
    """Registra as entidades que o preprocess do IfcOpenShell substitui/remove."""
    if old_file.schema == "IFC2X3" and new_schema == "IFC4":
        for e in old_file.by_type("IfcCalendarDate"):
            log.add(step, PREPROCESS_REMOVED, e, classe_destino="IfcDate (texto)", valor=_short(list(e)))
    if old_file.schema == "IFC4" and new_schema == "IFC4X3":
        for e in old_file.by_type("IfcPresentationStyleAssignment"):
            log.add(step, PREPROCESS_REMOVED, e, classe_destino="IfcStyledItem.Styles", valor=_short(e.Styles))


def _migrate_step(old_file: ifcopenshell.file, new_schema: str, log: MigrationLog, rules: dict) -> ifcopenshell.file:
    step = f"{old_file.schema_identifier}->{new_schema}"
    new_file = ifcopenshell.file(schema=new_schema)
    migrator = RecordingMigrator(step, log, rules)
    _preprocess_events(old_file, new_file.schema, step, log)
    with contextlib.redirect_stdout(io.StringIO()):
        migrator.preprocess(old_file, new_file)
        for element in old_file:
            try:
                migrator.migrate(element, new_file)
            except Exception as exc:  # registrada; a migração segue
                log.add(step, FAILED, element, valor=str(exc)[:300])
    return new_file


def _checks(source: ifcopenshell.file, result: ifcopenshell.file) -> list[dict]:
    def guids(f):
        return {e.GlobalId for e in f.by_type("IfcRoot")}

    def with_geometry(f):
        return sum(1 for p in f.by_type("IfcProduct") if p.Representation)

    def placements(f):
        return len(f.by_type("IfcLocalPlacement"))

    src_g, res_g = guids(source), guids(result)
    rows = [
        ("GlobalId preservados", len(src_g), len(src_g & res_g)),
        ("GlobalId novos", 0, len(res_g - src_g)),
        ("Produtos com geometria", with_geometry(source), with_geometry(result)),
        ("IfcLocalPlacement", placements(source), placements(result)),
        ("Elementos (IfcElement)", len(source.by_type("IfcElement")), len(result.by_type("IfcElement"))),
        ("Psets (IfcPropertySet)", len(source.by_type("IfcPropertySet")), len(result.by_type("IfcPropertySet"))),
        ("Layers", len(source.by_type("IfcPresentationLayerAssignment")),
         len(result.by_type("IfcPresentationLayerAssignment"))),
    ]
    return [{"verificacao": n, "origem": a, "resultado": b, "ok": a == b} for n, a, b in rows]


def _stamp_header(result: ifcopenshell.file, source_schema: str) -> None:
    desc = list(result.header.file_description.description)
    desc.append(f"ifc-normalizer {__version__} [Migrate: {source_schema} -> {TARGET_SCHEMA}]")
    result.header.file_description.description = tuple(desc)


def migrate_file(path: str | Path, rules: dict | None = None) -> MigrationResult:
    rules = rules if rules is not None else load_rules()
    source = ifcopenshell.open(str(path))
    source_schema = source.schema_identifier
    steps = _STEPS.get(source.schema)
    if steps is None:
        raise ValueError(f"Schema de origem não suportado: {source_schema}")
    if source.schema == "IFC4X3" and source_schema != TARGET_SCHEMA:
        raise ValueError(f"IFC4X3 em versão diferente do alvo: {source_schema}")

    log = MigrationLog()
    # O preprocess do IfcOpenShell altera o arquivo de entrada; trabalhamos sobre
    # uma cópia para poder comparar origem x resultado.
    current = ifcopenshell.open(str(path)) if steps else source
    for schema in steps:
        current = _migrate_step(current, schema, log, rules)
    if steps:
        _stamp_header(current, source_schema)
    return MigrationResult(current, source_schema, steps, log, _checks(source, current))


def summarize(log: MigrationLog) -> list[dict]:
    counter = Counter(
        (e["etapa"], e["tipo"], e["classe_origem"], e.get("classe_destino") or "", e.get("atributo") or "")
        for e in log.events
    )
    return [
        {"etapa": k[0], "tipo": k[1], "classe_origem": k[2], "classe_destino": k[3], "atributo": k[4], "qtd": n}
        for k, n in sorted(counter.items())
    ]
