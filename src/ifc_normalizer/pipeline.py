"""Fluxo por arquivo, compartilhado pela linha de comando e pela interface.

Cada função processa um arquivo, grava as saídas e devolve um resumo serializável.
Erros viram resumo com status "erro" — um arquivo ruim não interrompe o lote.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from . import analysis, migrate, profile, repair, report, spatial, validate
from .normalize import normalize
from .rules import RuleSet

_SCHEMA_RE = re.compile(rb"FILE_SCHEMA\s*\(\s*\(\s*'([^']+)'")
_FILE_NAME_RE = re.compile(rb"FILE_NAME\s*\((.*?)\);", re.S)


def expand_inputs(patterns: list[str]) -> list[Path]:
    files: set[Path] = set()
    for pattern in patterns:
        p = Path(pattern)
        if p.is_dir():
            files.update(p.glob("*.ifc"))
        elif any(ch in pattern for ch in "*?["):
            files.update(Path().glob(pattern))
        else:
            files.add(p)
    return sorted(files)


def peek(path: Path) -> dict:
    """Lê só o cabeçalho (rápido, mesmo em arquivos grandes)."""
    with open(path, "rb") as fh:
        head = fh.read(16384)
    schema = _SCHEMA_RE.search(head)
    file_name = _FILE_NAME_RE.search(head)
    app = file_name.group(1).decode("latin-1", "replace") if file_name else ""
    return {
        "arquivo": path.name,
        "caminho": str(path),
        "tamanho_mb": round(path.stat().st_size / 1e6, 1),
        "schema": schema.group(1).decode() if schema else "?",
        "origem": profile.detect_origin(app),
        "disciplina": profile.detect_discipline(path.name),
    }


def _syntax_hint(path: Path, exc: Exception) -> str:
    """Mensagem de erro de leitura com os problemas de sintaxe STEP encontrados no texto."""
    try:
        found = analysis.check_syntax(path)
    except OSError:
        return str(exc)
    if not found:
        return str(exc)
    shown = "; ".join(f["detalhe"] for f in found[:5])
    return f"{exc} | sintaxe STEP: {len(found)} problema(s): {shown}"


def _base(path: Path) -> dict:
    return {"arquivo": path.name, "caminho": str(path), "status": "ok", "mensagem": "", "saidas": {}}


def _repair(f, path: Path, out_dir: Path, repair_rules: dict | None, run_validation: bool):
    """Correções de schema/normativas + validação final (schema e WHERE rules). Devolve (resultado, erros, xlsx)."""
    fixes = repair.repair(f, repair_rules, path) if repair_rules is not None else None
    errors = validate.validate_schema(f, express_rules=fixes is not None) if run_validation else None
    xlsx = None
    if fixes is not None:
        sheets = {"Familias": analysis.family_counts(f), "Preenchimento_Atributos": analysis.attribute_fill(f),
                  "Relacoes_por_Classe": analysis.relation_coverage(f),
                  "Cobertura_bSI": repair.bsi_coverage(repair_rules)}
        xlsx = report.write_verification_xlsx(path.name, fixes, repair_rules, out_dir / f"{path.stem}.verificacao.xlsx",
                                              errors, sheets)
    return fixes, errors, xlsx


def _fix_summary(fixes, xlsx) -> dict:
    if fixes is None:
        return {}
    return {"correcoes": len(fixes.changes), "pendencias_verificacao": len(fixes.pending),
            "verificacao": str(xlsx)}


def migrate_one(path: Path, out_dir: Path, rules: dict, run_validation: bool = True,
                repair_rules: dict | None = None) -> dict:
    summary = _base(path)
    t0 = time.perf_counter()
    try:
        result = migrate.migrate_file(path, rules)
        fixes, errors, fix_xlsx = _repair(result.file, path, out_dir, repair_rules, run_validation)
        target = out_dir / path.name
        result.file.write(str(target))
        xlsx = report.write_migration_xlsx(path.name, result, out_dir / f"{path.stem}.migracao.xlsx", errors)
    except Exception as exc:  # registrado no resumo
        return {**summary, "status": "erro", "mensagem": _syntax_hint(path, exc),
                "tempo_s": round(time.perf_counter() - t0, 1)}
    kinds: dict[str, int] = {}
    for e in result.log.events:
        kinds[e["tipo"]] = kinds.get(e["tipo"], 0) + 1
    return {
        **summary,
        "schema_origem": result.source_schema,
        "etapas": " → ".join([result.source_schema, *result.steps]),
        "eventos": kinds,
        "verificacoes_divergentes": [c["verificacao"] for c in result.checks if not c["ok"]],
        "erros_schema": None if errors is None else len(errors),
        **_fix_summary(fixes, fix_xlsx),
        "saidas": {"ifc": str(target), "relatorio": str(xlsx), **({"verificacao": str(fix_xlsx)} if fix_xlsx else {})},
        "tempo_s": round(time.perf_counter() - t0, 1),
    }


def normalize_one(
    path: Path, out_dir: Path, ruleset: RuleSet, migration_rules: dict,
    include_proposed: bool = False, run_validation: bool = True, structure_rules: dict | None = None,
    repair_rules: dict | None = None,
) -> dict:
    """Migra (se preciso), aplica as regras de elementos e, com `structure_rules`, a estrutura espacial."""
    summary = _base(path)
    t0 = time.perf_counter()
    try:
        mig = migrate.migrate_file(path, migration_rules)
        outputs = {}
        if mig.steps:
            outputs["migracao"] = str(
                report.write_migration_xlsx(path.name, mig, out_dir / f"{path.stem}.migracao.xlsx")
            )
        result = normalize(mig.file, ruleset, path.name, include_proposed)
        if structure_rules is not None:
            changes, pending = spatial.restructure(mig.file, structure_rules, result.context, include_proposed)
            result.changes.extend(changes)
            result.pending.extend(pending)
        fixes, errors, fix_xlsx = _repair(mig.file, path, out_dir, repair_rules, run_validation)
        target = out_dir / path.name
        mig.file.write(str(target))
        xlsx = report.write_normalization_xlsx(
            path.name, result, ruleset, include_proposed, out_dir / f"{path.stem}.normalizacao.xlsx", errors
        )
    except Exception as exc:
        return {**summary, "status": "erro", "mensagem": _syntax_hint(path, exc),
                "tempo_s": round(time.perf_counter() - t0, 1)}
    reasons: dict[str, int] = {}
    for p in result.pending:
        reasons[p["motivo"]] = reasons.get(p["motivo"], 0) + 1
    return {
        **summary,
        "origem": result.context.get("origem"),
        "schema_origem": mig.source_schema,
        "etapas": " → ".join([mig.source_schema, *mig.steps]),
        "alteracoes": len(result.changes),
        "elementos_alterados": len({c["global_id"] for c in result.changes}),
        "pendencias": len(result.pending),
        "pendencias_por_motivo": reasons,
        "erros_schema": None if errors is None else len(errors),
        **_fix_summary(fixes, fix_xlsx),
        "saidas": {**outputs, "ifc": str(target), "relatorio": str(xlsx),
                   **({"verificacao": str(fix_xlsx)} if fix_xlsx else {})},
        "tempo_s": round(time.perf_counter() - t0, 1),
    }


def profile_many(paths: list[Path], out_dir: Path, on_file=None) -> dict:
    """Diagnóstico de vários arquivos numa planilha só."""
    profiles, failures = [], []
    for path in paths:
        if on_file:
            on_file(path)
        try:
            p = profile.profile_file(path)
        except Exception as exc:
            failures.append({"arquivo": path.name, "mensagem": str(exc)})
            continue
        report.write_profile_json(p, out_dir / "json")
        profiles.append(p)
    xlsx = report.write_profile_xlsx(profiles, out_dir / "diagnostico.xlsx") if profiles else None
    return {"perfis": profiles, "falhas": failures, "relatorio": str(xlsx) if xlsx else None}
