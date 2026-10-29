"""Interface de linha de comando."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _files(args) -> list[Path]:
    from .pipeline import expand_inputs

    files = expand_inputs(args.inputs)
    if not files:
        print("Nenhum arquivo IFC encontrado.", file=sys.stderr)
    return files


def _load_ruleset(path: str | None):
    from . import rules

    try:
        return rules.load_normalization_rules(path) if path else rules.load_normalization_rules()
    except rules.RulesError as exc:
        print(str(exc), file=sys.stderr)
        return None


def _load_structure(path: str | None):
    """Regras de estrutura espacial; sem o arquivo padrão, a etapa é pulada (com aviso)."""
    from . import rules, spatial

    target = Path(path) if path else spatial.DEFAULT_RULES
    if not target.exists():
        print(f"Aviso: {target} não encontrado; estrutura espacial e nomes não serão ajustados.")
        return None
    try:
        return spatial.load_rules(target)
    except rules.RulesError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(3)


def _load_repair(args: argparse.Namespace):
    """Regras de verificação/correção (rules/verificacao.json), salvo --sem-correcao."""
    from . import repair, rules

    if getattr(args, "sem_correcao", False):
        return None
    target = Path(args.verificacao) if getattr(args, "verificacao", None) else repair.DEFAULT_RULES
    if not target.exists():
        print(f"Aviso: {target} não encontrado; correções de schema/normativas não serão aplicadas.")
        return None
    try:
        return repair.load_rules(target)
    except rules.RulesError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(3)


def _cmd_profile(args: argparse.Namespace) -> int:
    from . import pipeline

    files = _files(args)
    if not files:
        return 1
    result = pipeline.profile_many(files, Path(args.out), on_file=lambda p: print(f"Perfilando {p.name} ...", flush=True))
    for p in result["perfis"]:
        print(f"  {p['arquivo']}: {p['origem']} | {p['schema']} | {p['total_elementos']} elementos")
    for f in result["falhas"]:
        print(f"  ERRO {f['arquivo']}: {f['mensagem']}", file=sys.stderr)
    if result["relatorio"]:
        print(f"\nDiagnóstico consolidado: {result['relatorio']}")
    return 2 if result["falhas"] else 0


def _print_summary(s: dict) -> None:
    if s["status"] == "erro":
        print(f"  ERRO: {s['mensagem']}", file=sys.stderr)
        return
    if "alteracoes" in s:
        print(f"  {s['origem']} | {s['etapas']} | {s['alteracoes']} alterações "
              f"| pendências {s['pendencias_por_motivo'] or 0} | {s['tempo_s']}s")
    else:
        print(f"  {s['etapas']} | eventos {s['eventos']} | {s['tempo_s']}s")
    if s.get("verificacoes_divergentes"):
        print(f"  ATENÇÃO – verificações divergentes: {', '.join(s['verificacoes_divergentes'])}")
    if "correcoes" in s:
        print(f"  verificação: {s['correcoes']} correção(ões), {s['pendencias_verificacao']} pendência(s)")
    if s.get("erros_schema"):
        print(f"  ATENÇÃO – {s['erros_schema']} erro(s) de schema no resultado (ver Validacao_Schema)")
    print(f"  -> {', '.join(Path(p).name for p in s['saidas'].values())}")


def _cmd_migrate(args: argparse.Namespace) -> int:
    from . import migrate, pipeline

    files = _files(args)
    if not files:
        return 1
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    rules = migrate.load_rules(Path(args.rules)) if args.rules else migrate.load_rules()
    fixes = _load_repair(args)
    status = 0
    for path in files:
        print(f"Migrando {path.name} ...", flush=True)
        s = pipeline.migrate_one(path, out_dir, rules, not args.sem_validacao, fixes)
        _print_summary(s)
        status = status or (2 if s["status"] == "erro" else 0)
    return status


def _cmd_normalize(args: argparse.Namespace) -> int:
    from . import migrate, pipeline

    ruleset = _load_ruleset(args.regras)
    if ruleset is None:
        return 3
    for w in ruleset.warnings:
        print(f"Aviso: {w}")
    active = ruleset.active(args.incluir_propostas)
    mode = "aprovadas + propostas (simulação)" if args.incluir_propostas else "somente aprovadas"
    print(f"Regras {ruleset.version}: {len(active)} de {len(ruleset.rules)} ativas ({mode})")

    files = _files(args)
    if not files:
        return 1
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    mig_rules = migrate.load_rules()
    structure = _load_structure(args.estrutura)
    fixes = _load_repair(args)
    status = 0
    for path in files:
        print(f"Normalizando {path.name} ...", flush=True)
        s = pipeline.normalize_one(path, out_dir, ruleset, mig_rules, args.incluir_propostas, not args.sem_validacao,
                                   structure, fixes)
        _print_summary(s)
        status = status or (2 if s["status"] == "erro" else 0)
    return status


def _cmd_check_rules(args: argparse.Namespace) -> int:
    ruleset = _load_ruleset(args.regras)
    if ruleset is None:
        return 3
    counts: dict[str, int] = {}
    for r in ruleset.rules:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    print(f"Regras válidas: {ruleset.source} (versão {ruleset.version}) – {counts}")
    for w in ruleset.warnings:
        print(f"Aviso: {w}")
    return 0


def _cmd_validation_pack(args: argparse.Namespace) -> int:
    from . import validation_pack

    ruleset = _load_ruleset(args.regras)
    if ruleset is None:
        return 3
    reports = sorted(Path(args.relatorios).glob("*.normalizacao.xlsx"))
    if not reports:
        print(f"Nenhum relatório *.normalizacao.xlsx em {args.relatorios}. Rode antes: "
              "normalize ... --incluir-propostas", file=sys.stderr)
        return 1
    out = validation_pack.build(ruleset, reports, Path(args.out))
    print(f"Pacote de validação ({len(reports)} relatórios): {out}")
    return 0


def _cmd_coordinates(args: argparse.Namespace) -> int:
    from . import coordinates, report

    try:
        rules = coordinates.load_rules(args.regras) if args.regras else coordinates.load_rules()
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 3
    files = _files(args)
    if not files:
        return 1
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    results = coordinates.analyze_paths(files, rules, on_file=lambda p: print(f"Lendo {p.name} ...", flush=True))
    for r in sorted(results, key=lambda r: (r["predio"] or "~", r["arquivo"])):
        pos = "–" if r["x"] is None else f"X={r['x']:.3f} Y={r['y']:.3f} Z={r['z']:.3f}"
        dist = "" if r.get("distancia") is None else f" | Δ={r['distancia']:.3f} m"
        print(f"  [{r['predio'] or '-'}] {r['arquivo']}: {r['metodo']} | {pos}{dist} | {r['situacao']}")
    corrections = coordinates.correct_divergent(results, out_dir, rules) if args.corrigir else []
    for c in corrections:
        if not c["ok"]:
            status = "CONFERIR"
        elif c["rotacao_conferida"]:
            status = "OK"
        else:
            status = "posição OK, rotação NÃO conferida (modelo sem grade)"
        print(f"  Corrigido {c['arquivo']}: deslocamento ({c['deslocamento_x']:.3f}, {c['deslocamento_y']:.3f}) m, "
              f"giro {c['giro_graus']:.3f}° | resíduo {c['residuo_m']} m [{status}]")
    xlsx = report.write_coordinates_xlsx(results, corrections, rules, out_dir / "coordenadas.xlsx")
    print(f"Relatório: {xlsx}")
    return 0


def _cmd_gui(args: argparse.Namespace) -> int:
    from .gui import main as gui_main

    return gui_main()


def main(argv: list[str] | None = None) -> int:
    # Console do Windows (cp1252) não tem "→" e afins: sem isso a impressão derruba o comando.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(
        prog="ifc-normalizer",
        description="Normaliza IFCs para o padrão Promon / BEP.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_prof = sub.add_parser("profile", help="Fase 1: diagnóstico (somente leitura)")
    p_prof.add_argument("inputs", nargs="+", help="Arquivos .ifc, pastas ou padrões glob")
    p_prof.add_argument("-o", "--out", default="out/perfil", help="Pasta de saída")
    p_prof.set_defaults(func=_cmd_profile)

    p_mig = sub.add_parser("migrate", help="Migra IFC2X3/IFC4 para IFC4X3_ADD2 com relatório")
    p_mig.add_argument("inputs", nargs="+", help="Arquivos .ifc, pastas ou padrões glob")
    p_mig.add_argument("-o", "--out", default="out/migrado", help="Pasta de saída")
    p_mig.add_argument("--rules", help="YAML de regras de migração (padrão: rules/migracao.yaml)")
    p_mig.add_argument("--sem-validacao", action="store_true", help="Não validar o schema do resultado")
    p_mig.add_argument("--verificacao", help="JSON de verificação/correção (padrão: rules/verificacao.json)")
    p_mig.add_argument("--sem-correcao", action="store_true", help="Não aplicar as correções de schema/normativas")
    p_mig.set_defaults(func=_cmd_migrate)

    p_norm = sub.add_parser("normalize", help="Migra (se preciso) e aplica as regras de normalização")
    p_norm.add_argument("inputs", nargs="+", help="Arquivos .ifc, pastas ou padrões glob")
    p_norm.add_argument("-o", "--out", default="out/normalizado", help="Pasta de saída")
    p_norm.add_argument("--regras", help="JSON de regras (padrão: rules/normalizacao.json)")
    p_norm.add_argument("--incluir-propostas", action="store_true",
                        help="Aplica também regras com status 'proposta' (simulação para avaliação)")
    p_norm.add_argument("--sem-validacao", action="store_true", help="Não validar o schema do resultado")
    p_norm.add_argument("--estrutura", help="JSON de estrutura espacial e nomes (padrão: rules/estrutura.json)")
    p_norm.add_argument("--verificacao", help="JSON de verificação/correção (padrão: rules/verificacao.json)")
    p_norm.add_argument("--sem-correcao", action="store_true", help="Não aplicar as correções de schema/normativas")
    p_norm.set_defaults(func=_cmd_normalize)

    p_chk = sub.add_parser("check-rules", help="Valida o JSON de regras sem processar IFCs")
    p_chk.add_argument("--regras", help="JSON de regras (padrão: rules/normalizacao.json)")
    p_chk.set_defaults(func=_cmd_check_rules)

    p_pack = sub.add_parser("pacote-validacao", help="Planilha para validar as regras com o BEP")
    p_pack.add_argument("relatorios", help="Pasta com os *.normalizacao.xlsx de uma simulação")
    p_pack.add_argument("-o", "--out", default="out/validacao/pacote-validacao-regras.xlsx", help="Arquivo de saída")
    p_pack.add_argument("--regras", help="JSON de regras (padrão: rules/normalizacao.json)")
    p_pack.set_defaults(func=_cmd_validation_pack)

    p_coord = sub.add_parser("coordenadas", help="Verifica (e corrige) o posicionamento dos modelos de cada prédio")
    p_coord.add_argument("inputs", nargs="+", help="Arquivos .ifc, pastas ou padrões glob")
    p_coord.add_argument("-o", "--out", default="out/coordenadas", help="Pasta de saída")
    p_coord.add_argument("--regras", help="JSON de coordenadas (padrão: rules/coordenadas.json)")
    p_coord.add_argument("--corrigir", action="store_true",
                         help="Grava cópias corrigidas dos modelos divergentes (posição e rotação da referência)")
    p_coord.set_defaults(func=_cmd_coordinates)

    p_gui = sub.add_parser("gui", help="Abre a interface gráfica")
    p_gui.set_defaults(func=_cmd_gui)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
