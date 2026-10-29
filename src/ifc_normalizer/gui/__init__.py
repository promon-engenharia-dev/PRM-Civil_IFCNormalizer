"""Interface gráfica (janela nativa com HTML/CSS na identidade Promon)."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from .. import __version__
from ..paths import rules_dir, ui_dir

WINDOW_TITLE = "Normalizador IFC – Promon Engenharia"
OUTPUT_FOLDER = "saida_normalizador"
# Fundo da janela antes de a página carregar (= --pe-bg do tema claro e do escuro).
WINDOW_BACKGROUND = {"claro": "#EDF1F4", "escuro": "#141A20"}


def system_theme() -> str:
    """Tema de aplicativos do Windows ("claro" ou "escuro"). A página segue o mesmo tema via CSS."""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            return "claro" if winreg.QueryValueEx(key, "AppsUseLightTheme")[0] else "escuro"
    except (OSError, ImportError):
        return "claro"


class Api:
    """Métodos chamados pelo JavaScript (window.pywebview.api.*)."""

    def __init__(self) -> None:
        self._window = None
        self._busy = False
        self._lock = threading.Lock()
        self._coord = {"resultados": [], "pasta": None, "regras": None}

    # -- estado -----------------------------------------------------------
    def estado_inicial(self) -> dict:
        return {"versao": __version__, "regras": self.info_regras(), "pasta_regras": str(rules_dir())}

    def info_regras(self) -> dict:
        from .. import rules

        path = rules.DEFAULT_NORMALIZATION_RULES
        try:
            rs = rules.load_normalization_rules(path)
        except FileNotFoundError:
            return {"ok": False, "caminho": str(path), "problemas": [f"Arquivo de regras não encontrado: {path}"]}
        except rules.RulesError as exc:
            return {"ok": False, "caminho": str(path), "problemas": exc.problems}
        except ValueError as exc:  # JSON malformado
            return {"ok": False, "caminho": str(path), "problemas": [f"JSON inválido: {exc}"]}
        return {
            "ok": True,
            "caminho": str(path),
            "versao": rs.version,
            "aprovadas": sum(r["status"] == "aprovada" for r in rs.rules),
            "propostas": sum(r["status"] == "proposta" for r in rs.rules),
            "avisos": rs.warnings,
            "problemas": [],
        }

    def estrutura(self) -> dict:
        """Nomes (projeto, site, prédios) e regras de infraestrutura de rules/estrutura.json."""
        from .. import rules, spatial

        path = spatial.DEFAULT_RULES
        try:
            data = spatial.load_rules(path)
        except FileNotFoundError:
            return {"ok": False, "caminho": str(path), "problemas": [f"Arquivo não encontrado: {path}"]}
        except rules.RulesError as exc:
            return {"ok": False, "caminho": str(path), "problemas": exc.problems}
        except ValueError as exc:
            return {"ok": False, "caminho": str(path), "problemas": [f"JSON inválido: {exc}"]}
        return {"ok": True, "caminho": str(path), "projeto": data.get("projeto") or {},
                "site": data.get("site") or {}, "predios": data.get("predios") or [],
                "infraestrutura": [{k: r.get(k) for k in ("id", "status", "descricao")}
                                   for r in data.get("infraestrutura") or []]}

    def salvar_estrutura(self, nomes: dict) -> dict:
        from .. import spatial

        info = self.estrutura()
        if not info["ok"]:
            return info
        spatial.save_names(spatial.DEFAULT_RULES, spatial.with_overrides(spatial.load_rules(), nomes))
        return self.estrutura()

    # -- seleção de arquivos ------------------------------------------------
    def escolher_arquivos(self) -> list[dict]:
        import webview

        from ..pipeline import peek

        paths = self._window.create_file_dialog(
            webview.FileDialog.OPEN, allow_multiple=True, file_types=("Arquivos IFC (*.ifc)", "Todos (*.*)")
        )
        return [peek(Path(p)) for p in (paths or []) if p.lower().endswith(".ifc")]

    def escolher_pasta_arquivos(self) -> list[dict]:
        import webview

        from ..pipeline import peek

        folder = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        if not folder:
            return []
        folder = folder[0] if isinstance(folder, (list, tuple)) else folder
        return [peek(p) for p in sorted(Path(folder).glob("*.ifc"))]

    def escolher_pasta_saida(self) -> str | None:
        import webview

        folder = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        if not folder:
            return None
        return folder[0] if isinstance(folder, (list, tuple)) else folder

    def pasta_padrao(self, caminho_arquivo: str) -> str:
        return str(Path(caminho_arquivo).parent / OUTPUT_FOLDER)

    # -- abrir no Windows --------------------------------------------------
    def abrir(self, caminho: str) -> bool:
        if caminho and os.path.exists(caminho):
            os.startfile(caminho)  # noqa: S606 – abre com o programa padrão do Windows
            return True
        return False

    def abrir_pasta_de(self, caminho: str) -> bool:
        return self.abrir(str(Path(caminho).parent))

    def abrir_regras(self) -> bool:
        return self.abrir(str(rules_dir() / "normalizacao.json"))

    # -- processamento ------------------------------------------------------
    def processar(self, caminhos: list[str], pasta_saida: str, acoes: list[str] | str,
                  incluir_propostas: bool, validar: bool, estrutura: dict | None = None) -> dict:
        steps = plan_actions(acoes)
        if not steps:
            return {"ok": False, "mensagem": "Escolha pelo menos uma ação."}
        with self._lock:
            if self._busy:
                return {"ok": False, "mensagem": "Já existe um processamento em andamento."}
            self._busy = True
        args = (list(caminhos), Path(pasta_saida), steps, bool(incluir_propostas), bool(validar), estrutura)
        threading.Thread(target=self._run, args=args, daemon=True).start()
        return {"ok": True, "acoes": steps}

    def _emit(self, payload: dict, handler: str = "aoProgresso") -> None:
        if self._window is not None:
            self._window.evaluate_js(f"window.{handler}({json.dumps(payload, ensure_ascii=False, default=str)})")

    def _run(self, caminhos: list[str], out_dir: Path, acoes: list[str], incluir_propostas: bool,
             validar: bool, estrutura: dict | None = None) -> None:
        from .. import migrate, pipeline, repair, rules, spatial

        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            paths = [Path(c) for c in caminhos]
            index = {p.name: i for i, p in enumerate(paths)}
            total = len(paths) * len(acoes)
            step = 0
            outputs: dict[str, Path] = {}  # arquivo -> IFC gerado (normalizado/convertido)
            self._emit({"tipo": "inicio_lote", "total": len(paths), "pasta": str(out_dir), "acoes": acoes})

            def started(acao: str, p: Path) -> None:
                nonlocal step
                step += 1
                self._emit({"tipo": "inicio_arquivo", "acao": acao, "indice": index.get(p.name, 0),
                            "arquivo": p.name, "passo": step - 1, "total_passos": total})

            for acao in acoes:
                self._emit({"tipo": "inicio_etapa", "acao": acao})
                if acao == "diagnostico":
                    result = pipeline.profile_many(paths, out_dir, on_file=lambda p: started("diagnostico", p))
                    self._emit({"tipo": "fim_etapa", "acao": acao, "relatorio": result["relatorio"],
                                "falhas": result["falhas"],
                                "perfis": [{k: p[k] for k in ("arquivo", "origem", "schema", "total_elementos",
                                                              "proxies", "sem_predefined")}
                                           for p in result["perfis"]]})
                elif acao == "coordenadas":
                    # Combinada com normalizar/converter: confere os arquivos gerados (a correção vale para eles).
                    targets = [outputs.get(p.name, p) for p in paths] if outputs else paths
                    self._run_coordinates(targets, out_dir / COORD_FOLDER, lambda p: started("coordenadas", p))
                else:
                    mig_rules = migrate.load_rules()
                    fixes = repair.load_rules() if repair.DEFAULT_RULES.exists() else None
                    ruleset = rules.load_normalization_rules() if acao == "normalizar" else None
                    structure = None
                    if acao == "normalizar" and spatial.DEFAULT_RULES.exists():
                        structure = spatial.with_overrides(spatial.load_rules(), estrutura)
                    for i, path in enumerate(paths):
                        started(acao, path)
                        if acao == "normalizar":
                            summary = pipeline.normalize_one(path, out_dir, ruleset, mig_rules,
                                                             incluir_propostas, validar, structure, fixes)
                        else:
                            summary = pipeline.migrate_one(path, out_dir, mig_rules, validar, fixes)
                        if summary["status"] != "erro":
                            outputs[path.name] = Path(summary["saidas"]["ifc"])
                        self._emit({"tipo": "fim_arquivo", "acao": acao, "indice": i, "resumo": summary})
                    self._emit({"tipo": "fim_etapa", "acao": acao})
            self._emit({"tipo": "fim_lote"})
        except Exception as exc:  # erro geral (ex.: regras inválidas)
            self._emit({"tipo": "erro_lote", "mensagem": str(exc)})
        finally:
            self._busy = False


ACTION_ORDER = ("diagnostico", "normalizar", "migrar", "coordenadas")
COORD_FOLDER = "coordenadas"


def plan_actions(acoes: list[str] | str) -> list[str]:
    """Ordem de execução das ações escolhidas. Normalizar já inclui a conversão para IFC 4.3."""
    chosen = {acoes} if isinstance(acoes, str) else set(acoes or [])
    if "normalizar" in chosen:
        chosen.discard("migrar")
    return [a for a in ACTION_ORDER if a in chosen]


class _CoordinatesMixin:
    """Quadro de coordenadas (estilo aba Measure do Navisworks): verificar e corrigir."""

    def _run_coordinates(self, paths: list[Path], out_dir: Path, on_file=None) -> None:
        from .. import coordinates, report

        rules = coordinates.load_rules()
        out_dir.mkdir(parents=True, exist_ok=True)
        results = coordinates.analyze_paths(paths, rules, on_file=on_file)
        for r in results:
            r["giro_sugerido"] = coordinates.suggested_rotation(r)
        self._coord = {"resultados": results, "pasta": out_dir, "regras": rules}
        xlsx = report.write_coordinates_xlsx(results, [], rules, out_dir / "coordenadas.xlsx")
        self._emit({"tipo": "fim_etapa", "acao": "coordenadas", "coordenadas": results, "relatorio": str(xlsx),
                    "pasta": str(out_dir), "tolerancia": rules["tolerancia_posicao_m"]})

    def corrigir_coordenadas(self, itens: list[dict]) -> dict:
        """itens: [{caminho, alvo: {x, y, z}, giro}] escolhidos e editados no quadro."""
        with self._lock:
            if self._busy:
                return {"ok": False, "mensagem": "Já existe um processamento em andamento."}
            if not self._coord["resultados"]:
                return {"ok": False, "mensagem": "Faça a verificação de coordenadas antes de corrigir."}
            self._busy = True
        threading.Thread(target=self._run_correction, args=(list(itens),), daemon=True).start()
        return {"ok": True}

    def _run_correction(self, itens: list[dict]) -> None:
        from .. import coordinates, report

        try:
            rules, out_dir = self._coord["regras"], self._coord["pasta"]
            by_path = {r["caminho"]: r for r in self._coord["resultados"]}
            done = []
            for item in itens:
                r = by_path[item["caminho"]]
                self._emit({"tipo": "inicio_correcao", "arquivo": r["arquivo"]})
                target = {k: float(item["alvo"][k]) for k in ("x", "y", "z")}
                try:
                    c = coordinates.correct_file(Path(r["caminho"]), out_dir / r["arquivo"], r, target,
                                                 float(item.get("giro") or 0.0), rules)
                except Exception as exc:
                    c = {"arquivo": r["arquivo"], "ok": False, "erro": str(exc)}
                done.append(c)
                self._emit({"tipo": "fim_correcao", "caminho": item["caminho"], "resultado": c})
            xlsx = report.write_coordinates_xlsx(self._coord["resultados"], done, rules, out_dir / "coordenadas.xlsx")
            self._emit({"tipo": "fim_correcoes", "relatorio": str(xlsx), "pasta": str(out_dir)})
        except Exception as exc:
            self._emit({"tipo": "erro_lote", "mensagem": str(exc)})
        finally:
            self._busy = False


class _ViewerMixin:
    """Aba Visualizador 3D: carrega modelos (somente leitura) e consulta propriedades."""

    def _scene(self):
        if getattr(self, "_viewer", None) is None:
            from ..viewer import Scene

            self._viewer = Scene()
            self._viewer_lock = threading.Lock()
        return self._viewer

    def escolher_ifc_visualizar(self) -> list[str]:
        import webview

        paths = self._window.create_file_dialog(
            webview.FileDialog.OPEN, allow_multiple=True, file_types=("Arquivos IFC (*.ifc)", "Todos (*.*)")
        )
        return [p for p in (paths or []) if p.lower().endswith(".ifc")]

    def visualizador_carregar(self, caminho: str, incluir_armaduras: bool = False) -> dict:
        scene = self._scene()
        if not os.path.exists(caminho):
            return {"ok": False, "mensagem": f"Arquivo não encontrado: {caminho}"}
        if not self._viewer_lock.acquire(blocking=False):
            return {"ok": False, "mensagem": "Já existe um modelo sendo carregado."}
        threading.Thread(target=self._load_model, args=(scene, Path(caminho), bool(incluir_armaduras)),
                         daemon=True).start()
        return {"ok": True}

    def _load_model(self, scene, path: Path, include_reinforcement: bool) -> None:
        def emit(payload: dict) -> None:
            self._emit(payload, "aoVisualizador")

        try:
            emit({"tipo": "inicio", "arquivo": path.name})
            meta = scene.load(path, include_reinforcement,
                              on_progress=lambda n, total: emit({"tipo": "progresso", "arquivo": path.name,
                                                                 "feitos": n, "total": total}))
            emit({"tipo": "pronto", "meta": meta})
        except Exception as exc:
            emit({"tipo": "erro", "arquivo": path.name, "mensagem": str(exc)})
        finally:
            self._viewer_lock.release()

    def visualizador_parte(self, modelo: int, parte: int) -> str:
        return self._scene().chunk(int(modelo), int(parte))

    def visualizador_propriedades(self, modelo: int, elemento: int) -> dict:
        try:
            return {"ok": True, **self._scene().properties(int(modelo), int(elemento))}
        except Exception as exc:
            return {"ok": False, "mensagem": str(exc)}

    def visualizador_remover(self, modelo: int) -> bool:
        self._scene().remove(int(modelo))
        return True


class AppApi(_ViewerMixin, _CoordinatesMixin, Api):
    """API exposta à janela: processamento, quadro de coordenadas e visualizador 3D."""


def main() -> int:
    import webview

    api = AppApi()
    window = webview.create_window(
        WINDOW_TITLE, url=str(ui_dir() / "index.html"), js_api=api,
        width=1320, height=860, min_size=(1024, 680), background_color=WINDOW_BACKGROUND[system_theme()],
    )
    api._window = window
    webview.start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
