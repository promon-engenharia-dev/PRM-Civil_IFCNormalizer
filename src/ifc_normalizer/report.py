"""Relatórios de alterações e pendências (xlsx/json)."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

SUMMARY_FIELDS = [
    "arquivo", "origem", "disciplina", "schema", "schema_alvo_ok", "aplicativo",
    "view_definition", "coordinate_reference", "unidade_comprimento_m", "tamanho_mb",
    "total_entidades", "total_elementos", "proxies", "sem_predefined", "sem_container",
    "sem_layer", "nome_vazio", "com_pset_prm",
]

# Colunas que a equipe (Civil + BIM) preenche para transformar o diagnóstico em de-para.
DEPARA_TARGET_COLUMNS = [
    "classe_destino", "predefined_destino", "object_type_destino", "layer_destino",
    "regra_aplicada", "observacao",
]


def write_profile_json(profile: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{Path(profile['arquivo']).stem}.perfil.json"
    path.write_text(json.dumps(profile, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return path


def _per_file(profiles: list[dict], key: str) -> pd.DataFrame:
    rows = []
    for p in profiles:
        for r in p[key]:
            rows.append({"arquivo": p["arquivo"], "origem": p["origem"], "disciplina": p["disciplina"], **r})
    return pd.DataFrame(rows)


def _write_sheets(sheets: dict[str, pd.DataFrame], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        for name, df in sheets.items():
            df.to_excel(xw, sheet_name=name, index=False)
            ws = xw.sheets[name]
            ws.freeze_panes = "A2"
            ws.auto_filter.ref = ws.dimensions
            for col in ws.columns:
                width = max((len(str(c.value)) for c in col[:200] if c.value is not None), default=8)
                ws.column_dimensions[col[0].column_letter].width = min(max(width + 2, 10), 60)
    return path


# Limite de linhas do Excel, com folga.
MAX_DETAIL_ROWS = 1_000_000

EVENT_LABELS = {
    "classe_convertida": "Classe convertida para equivalente do schema novo",
    "atributo_descartado": "Atributo sem correspondente no schema novo (valor perdido)",
    "atributo_mapeado": "Atributo copiado para outro nome (rules/migracao.yaml)",
    "valor_descartado": "Valor não aceito pelo schema novo (ex.: enumeração)",
    "valor_padrao_gerado": "Atributo obrigatório preenchido com valor padrão",
    "obrigatorio_sem_valor": "Atributo obrigatório ficou sem valor (arquivo inválido)",
    "entidade_substituida": "Entidade obsoleta substituída no pré-processamento",
    "falha": "Entidade não migrada (erro)",
}


def write_migration_xlsx(
    source_name: str, result, out_path: Path, schema_errors: list[dict] | None = None
) -> Path:
    from . import migrate

    events = pd.DataFrame(result.log.events)
    if events.empty:
        events = pd.DataFrame(columns=["etapa", "tipo", "step_id", "classe_origem", "global_id", "nome",
                                       "classe_destino", "atributo", "valor"])
    by_kind = (
        events.groupby("tipo").size().rename("qtd").reset_index()
        if not events.empty else pd.DataFrame(columns=["tipo", "qtd"])
    )
    by_kind["descricao"] = by_kind["tipo"].map(EVENT_LABELS)
    info = pd.DataFrame(
        [
            ("Arquivo de origem", source_name),
            ("Schema de origem", result.source_schema),
            ("Schema de destino", migrate.TARGET_SCHEMA),
            ("Etapas", " -> ".join([result.source_schema, *result.steps]) or "nenhuma (já no alvo)"),
            ("Total de eventos", len(events)),
            ("Verificações com divergência", sum(not c["ok"] for c in result.checks)),
            ("Erros de schema no resultado",
             "não validado" if schema_errors is None else len(schema_errors)),
        ],
        columns=["item", "valor"],
    )
    resumo = pd.concat([info, pd.DataFrame([{}]), by_kind.rename(columns={"tipo": "item", "qtd": "valor"})])

    rooted = events[events["global_id"].notna()]
    others = events[events["global_id"].isna()]
    sheets = {
        "Resumo": resumo,
        "Verificacoes": pd.DataFrame(result.checks),
        "Validacao_Schema": pd.DataFrame(
            schema_errors or [],
            columns=["nivel", "tipo", "step_id", "classe", "global_id", "nome", "atributo", "mensagem"],
        ),
        "Eventos_Agrupados": pd.DataFrame(migrate.summarize(result.log)),
        "Detalhe_Objetos": rooted.head(MAX_DETAIL_ROWS),
        "Detalhe_Outros": others.head(MAX_DETAIL_ROWS),
    }
    return _write_sheets(sheets, out_path)


PENDING_LABELS = {
    "classe_generica_sem_regra": "Classe genérica (proxy) sem regra de classificação",
    "sem_predefined_type": "PredefinedType vazio ou NOTDEFINED",
    "conflito_entre_regras": "Duas regras definem valores diferentes para o mesmo campo",
    "tipo_incompativel_com_classe": "Tipo (IfcTypeObject) não corresponde à nova classe",
    "valor_de_origem_ausente": "Propriedade de origem vazia para cópia",
    "valor_nao_convertido": "Valor não pôde ser convertido para o tipo exigido",
    "userdefined_sem_object_type": "PredefinedType USERDEFINED sem ObjectType",
}

# Uma linha por peça analisada pela heurística geométrica; "conferido" é preenchido na revisão.
GEOMETRY_REVIEW_COLUMNS = [
    "global_id", "nome", "tipo_nome", "classe_original", "orientacao", "angulo_vertical_graus",
    "comprimento_m", "largura_m", "espessura_m", "regra_geometrica", "situacao",
    "classe_final", "predefined_final", "conferido",
]

PENDING_GROUP_KEYS =["motivo", "classe", "predefined_type", "object_type", "nome_normalizado", "tipo_nome", "layers"]
SUGGESTION_COLUMNS = ["sugestao_classe", "sugestao_predefined_type", "sugestao_object_type", "observacao"]


def write_normalization_xlsx(
    source_name: str, result, ruleset, include_proposed: bool, out_path: Path,
    schema_errors: list[dict] | None = None,
) -> Path:
    changes = pd.DataFrame(
        result.changes, columns=["global_id", "classe", "nome", "campo", "antes", "depois", "regra", "status_regra"]
    )
    pending = pd.DataFrame(
        result.pending,
        columns=["global_id", "classe", "nome", "motivo", "detalhe", "regra", "predefined_type", "object_type",
                 "tipo_nome", "layers", "nome_normalizado"],
    )
    if pending.empty:
        grouped = pd.DataFrame(columns=PENDING_GROUP_KEYS + ["qtd", "exemplo_global_id", "exemplo_nome"])
    else:
        grouped = (
            pending.fillna("")
            .groupby(PENDING_GROUP_KEYS)
            .agg(qtd=("global_id", "size"), exemplo_global_id=("global_id", "first"), exemplo_nome=("nome", "first"))
            .reset_index()
            .sort_values(["motivo", "qtd"], ascending=[True, False])
        )
    for col in SUGGESTION_COLUMNS:
        grouped[col] = ""

    ctx = result.context
    info = [
        ("Arquivo", source_name),
        ("Origem detectada", ctx.get("origem")),
        ("Disciplina", ctx.get("disciplina")),
        ("Regras", f"{ruleset.source} (versão {ruleset.version})"),
        ("Regras aplicadas", "aprovadas + propostas (simulação)" if include_proposed else "somente aprovadas"),
        ("Elementos alterados", changes["global_id"].nunique()),
        ("Alterações", len(changes)),
        ("Pendências", len(pending)),
        ("Erros de schema no resultado", "não validado" if schema_errors is None else len(schema_errors)),
    ]
    geo = pd.DataFrame(result.geometry_review, columns=GEOMETRY_REVIEW_COLUMNS)
    if not geo.empty:
        applied = int((geo["situacao"] == "aplicada").sum())
        info.append(("Revisão geométrica (heurística – conferir)",
                     f"{len(geo)} peças analisadas, {applied} com regra geométrica aplicada"))
    by_reason = pending.groupby("motivo").size() if not pending.empty else {}
    info += [(f"  {PENDING_LABELS.get(k, k)}", int(v)) for k, v in dict(by_reason).items()]
    info += [(f"Aviso: {w}", "") for w in ruleset.warnings]

    sheets = {
        "Resumo": pd.DataFrame(info, columns=["item", "valor"]),
        "Alteracoes": changes,
        "Pendencias_Agrupadas": grouped,
        "Pendencias": pending.head(MAX_DETAIL_ROWS),
        "Uso_Regras": pd.DataFrame(list(result.rule_usage.values())),
        **({"Revisao_Geometrica": geo.head(MAX_DETAIL_ROWS)} if not geo.empty else {}),
        "Validacao_Schema": pd.DataFrame(
            schema_errors or [],
            columns=["nivel", "tipo", "step_id", "classe", "global_id", "nome", "atributo", "mensagem"],
        ),
    }
    return _write_sheets(sheets, out_path)


VERIFICATION_DETAIL = ["codigo", "categoria", "regra", "step_id", "global_id", "classe", "familia", "nome"]
SCHEMA_ERROR_COLUMNS = ["nivel", "tipo", "step_id", "classe", "global_id", "nome", "atributo", "mensagem"]


def write_verification_xlsx(source_name: str, result, rules: dict, out_path: Path,
                            schema_errors: list[dict] | None = None, analysis: dict | None = None) -> Path:
    """Relatório de verificação/correção: o que foi achado, corrigido e o que ficou pendente, por regra."""
    from .repair import CATEGORIES

    by_id = {c["id"]: c for c in rules.get("correcoes") or []}
    summary = []
    for cid, n in result.counts.items():
        c = by_id[cid]
        summary.append({"codigo": cid, "categoria": CATEGORIES[c["categoria"]], "regra": c["regra"],
                        "acao": "corrige" if c["acao"] == "automatica" else "aponta", **n,
                        "descricao": c.get("descricao", "")})
    summary = pd.DataFrame(summary, columns=["codigo", "categoria", "regra", "acao", "encontrados", "corrigidos",
                                             "pendentes", "descricao"])
    errors = pd.DataFrame(schema_errors or [], columns=SCHEMA_ERROR_COLUMNS)
    info = [
        ("Arquivo", source_name),
        ("Regras de verificação", f"{rules.get('_fonte', '')} (versão {rules.get('versao', '')})"),
        ("Sintaxe STEP", "arquivo lido sem erro" if result.syntax == "ok" else result.syntax),
        ("Correções aplicadas", len(result.changes)),
        ("Pendências (precisam de decisão)", len(result.pending)),
        ("Erros de schema que restam", "não validado" if schema_errors is None else len(errors)),
    ]
    for cat in summary["categoria"].unique():
        part = summary[summary["categoria"] == cat]
        info.append((f"  {cat}", f"{int(part['corrigidos'].sum())} corrigidos, {int(part['pendentes'].sum())} pendentes"))
    sheets = {
        "Resumo": pd.DataFrame(info, columns=["item", "valor"]),
        "Por_Regra": summary,
        "Correcoes": pd.DataFrame(result.changes, columns=VERIFICATION_DETAIL + ["campo", "antes", "depois"]).head(MAX_DETAIL_ROWS),
        "Pendencias": pd.DataFrame(result.pending, columns=VERIFICATION_DETAIL + ["detalhe", "sugestao"]).head(MAX_DETAIL_ROWS),
        "Erros_Schema_Restantes": errors.head(MAX_DETAIL_ROWS),
        # Análise no estilo do IFC File Analyzer (NIST), sobre o arquivo final.
        **{name: pd.DataFrame(rows) for name, rows in (analysis or {}).items()},
    }
    return _write_sheets(sheets, out_path)


COORD_COLUMNS = {
    "predio": "Prédio", "arquivo": "Modelo", "disciplina": "Disciplina", "origem": "Origem",
    "metodo": "Como o ponto foi achado", "eixos": "Eixos de referência", "x": "X (m)", "y": "Y (m)", "z": "Z (m)",
    "rotacao": "Direção do eixo (°)", "zero_zero": "Eixo no zero-zero do modelo?", "referencia": "Modelo de referência",
    "dx": "ΔX (m)", "dy": "ΔY (m)", "dz": "ΔZ (m)", "distancia": "Distância em planta (m)", "drot": "ΔRotação (°)",
    "situacao": "Situação", "interpretacao": "Interpretação a conferir?", "pilar_mais_proximo_m":
    "Pilar mais próximo da referência (m)", "eixos_disponiveis": "Eixos disponíveis na grade", "observacao": "Observação",
}


def write_coordinates_xlsx(results: list[dict], corrections: list[dict], rules: dict, path: Path) -> Path:
    rows = []
    for r in sorted(results, key=lambda r: (r.get("predio") or "~", r.get("situacao") != "referência", r["arquivo"])):
        row = {label: r.get(key) for key, label in COORD_COLUMNS.items()}
        for key in ("X (m)", "Y (m)", "Z (m)", "ΔX (m)", "ΔY (m)", "ΔZ (m)", "Distância em planta (m)",
                    "Pilar mais próximo da referência (m)"):
            if row[key] is not None:
                row[key] = round(row[key], 3)
        row["Interpretação a conferir?"] = "sim" if r.get("interpretacao") else "não"
        rows.append(row)
    info = pd.DataFrame([
        ("Regras", f"rules/coordenadas.json (versão {rules.get('versao', '')})"),
        ("Tolerância de posição", f"{rules['tolerancia_posicao_m']} m"),
        ("Tolerância de rotação", f"{rules['tolerancia_rotacao_graus']}°"),
        ("Modelos analisados", len(results)),
        ("Divergentes", sum(r.get("situacao") == "divergente" for r in results)),
        ("Sem ponto de referência", sum(r.get("x") is None for r in results)),
        ("Corrigidos nesta execução", len(corrections)),
    ], columns=["item", "valor"])
    sheets = {"Resumo": info, "Coordenadas": pd.DataFrame(rows)}
    if corrections:
        sheets["Correcoes"] = pd.DataFrame(corrections)
    return _write_sheets(sheets, path)


def write_profile_xlsx(profiles: list[dict], path: Path) -> Path:
    summary = pd.DataFrame([{k: p.get(k) for k in SUMMARY_FIELDS} for p in profiles])
    coords = pd.DataFrame(
        [{"arquivo": p["arquivo"], "origem": p["origem"], **p["coordenadas"]} for p in profiles]
    )
    groups = _per_file(profiles, "grupos")
    seed = groups.copy()
    for col in DEPARA_TARGET_COLUMNS:
        seed[col] = ""

    sheets = {
        "Resumo": summary,
        "Coordenadas": coords,
        "Espacial": _per_file(profiles, "espacial"),
        "Classes": _per_file(profiles, "classes"),
        "Layers": _per_file(profiles, "layers"),
        "Psets": _per_file(profiles, "psets"),
        "PRM_Preenchimento": _per_file(profiles, "prm_preenchimento"),
        "DePara_Semente": seed,
    }
    return _write_sheets(sheets, path)
