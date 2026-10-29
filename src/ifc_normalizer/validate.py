"""Validação: schema IFC4X3_ADD2, Psets Promon e IDS do BEP."""

from __future__ import annotations

import ifcopenshell
import ifcopenshell.validate


def validate_schema(f: ifcopenshell.file, express_rules: bool = False) -> list[dict]:
    """Erros de schema (tipos, obrigatórios, cardinalidade) do arquivo."""
    logger = ifcopenshell.validate.json_logger()
    ifcopenshell.validate.validate(f, logger, express_rules=express_rules)
    rows = []
    for s in logger.statements:
        inst = s.get("instance")
        is_entity = isinstance(inst, ifcopenshell.entity_instance)
        rows.append(
            {
                "nivel": s.get("level"),
                "tipo": s.get("type"),
                "step_id": inst.id() if is_entity else None,
                "classe": inst.is_a() if is_entity else None,
                "global_id": getattr(inst, "GlobalId", None) if is_entity and inst.is_a("IfcRoot") else None,
                "nome": getattr(inst, "Name", None) if is_entity and inst.is_a("IfcRoot") else None,
                "atributo": str(s.get("attribute") or ""),
                "mensagem": str(s.get("message", ""))[:1000],
            }
        )
    return rows
