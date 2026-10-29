"""Verificação e correção de coordenadas entre modelos do mesmo prédio.

Convenção Promon: o zero-zero do modelo fica no cruzamento de eixos de referência do
prédio (padrão A x 1, configurável em rules/coordenadas.json). Para cada modelo:
1. localiza o ponto de referência: cruzamento dos eixos na grade (IfcGrid) ou, sem grade
   (Tekla exportado com GridExport:Off), o pilar do canto inferior esquerdo – interpretação
   a conferir, medida no sistema de eixos da arquitetura do mesmo prédio;
2. confere se o ponto está no zero-zero do modelo (origem do IfcSite);
3. compara posição e rotação com o modelo de referência do prédio (arquitetura).

A correção desloca/gira o modelo inteiro alterando só os posicionamentos absolutos
(IfcLocalPlacement sem PlacementRelTo): a geometria de cada peça não muda.
"""

from __future__ import annotations

import fnmatch
import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import ifcopenshell
import ifcopenshell.util.placement
import ifcopenshell.util.unit
import numpy as np

from . import __version__
from .normalize import element_facts, file_context, matches
from .paths import rules_dir
from .profile import detect_discipline

DEFAULT_RULES = rules_dir() / "coordenadas.json"


def load_rules(path: str | Path = DEFAULT_RULES) -> dict:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    problems = []
    for key in ("tolerancia_posicao_m", "tolerancia_rotacao_graus"):
        if not isinstance(data.get(key), (int, float)) or data[key] <= 0:
            problems.append(f"{key} deve ser um número positivo")
    for p in data.get("predios", []):
        if not p.get("id") or not p.get("arquivos"):
            problems.append(f"prédio sem 'id' ou 'arquivos': {p}")
        if "eixos" in p and (not isinstance(p["eixos"], list) or len(p["eixos"]) != 2):
            problems.append(f"prédio {p.get('id')}: 'eixos' deve ter dois nomes (ex.: [\"A\", \"1\"])")
    if problems:
        raise ValueError("Regras de coordenadas inválidas:\n  - " + "\n  - ".join(problems))
    return data


def building_for(filename: str, rules: dict) -> dict | None:
    name = Path(filename).name.upper()
    for p in rules.get("predios", []):
        if fnmatch.fnmatch(name, p["arquivos"].upper()):
            return p
    return None


# ------------------------------------------------------------ extração

@dataclass
class ModelData:
    arquivo: str
    caminho: str
    predio: dict | None
    disciplina: str
    origem: str
    site: np.ndarray                                    # 4x4, metros
    grids: list[tuple[float, np.ndarray, dict]] = field(default_factory=list)  # (z, matriz m, tag -> (p0, p1) m)
    columns: list[tuple[str, np.ndarray]] = field(default_factory=list)      # (nome, ponto mundo m)


def _matrix_m(placement, scale: float) -> np.ndarray:
    m = np.array(ifcopenshell.util.placement.get_local_placement(placement), dtype=float)
    m[:3, 3] *= scale
    return m


def extract(f: ifcopenshell.file, filename: str, path: str, rules: dict) -> ModelData:
    scale = ifcopenshell.util.unit.calculate_unit_scale(f)
    sites = [s for s in f.by_type("IfcSite") if s.ObjectPlacement]
    site = _matrix_m(sites[0].ObjectPlacement, scale) if sites else np.eye(4)
    ctx = file_context(f, filename)
    data = ModelData(Path(filename).name, str(path), building_for(filename, rules),
                     detect_discipline(filename), ctx["origem"], site)

    for g in f.by_type("IfcGrid"):
        if not g.ObjectPlacement:
            continue
        m = _matrix_m(g.ObjectPlacement, scale)
        axes = {}
        for attr in ("UAxes", "VAxes", "WAxes"):
            for ax in getattr(g, attr, None) or ():
                curve = ax.AxisCurve
                if ax.AxisTag and curve and curve.is_a("IfcPolyline") and len(curve.Points) >= 2:
                    p0 = np.array(curve.Points[0].Coordinates[:2], dtype=float) * scale
                    p1 = np.array(curve.Points[-1].Coordinates[:2], dtype=float) * scale
                    axes[ax.AxisTag.strip().upper()] = (p0, p1)
        if axes:
            data.grids.append((float(m[2, 3]), m, axes))
    data.grids.sort(key=lambda g: g[0])

    column_rule = rules.get("pilares") or {"classe": ["IfcColumn"]}
    for c in f.by_type("IfcColumn"):
        if c.ObjectPlacement and matches(column_rule, element_facts(c, ctx)):
            data.columns.append((c.Name or "", _matrix_m(c.ObjectPlacement, scale)[:3, 3]))
    return data


# ------------------------------------------------------------ localização

def _intersection(a, b) -> np.ndarray | None:
    (p, q), (r, s) = a, b
    d1, d2 = q - p, s - r
    den = d1[0] * d2[1] - d1[1] * d2[0]
    if abs(den) < 1e-12:
        return None
    t = ((r - p)[0] * d2[1] - (r - p)[1] * d2[0]) / den
    return p + t * d1


def _angle(m: np.ndarray) -> float:
    return round(math.degrees(math.atan2(m[1, 0], m[0, 0])), 4)


def _norm_angle(a: float) -> float:
    """Diferença entre direções de eixo (retas sem sentido): faixa de -90° a 90°."""
    return (a + 90.0) % 180.0 - 90.0


def _axis_direction(m: np.ndarray, axis: tuple) -> float:
    """Direção do eixo no espaço, em graus de 0 a 180 (independe do sistema local da grade)."""
    p0, p1 = axis
    d = m[:3, :3] @ np.array([p1[0] - p0[0], p1[1] - p0[1], 0.0])
    return round(math.degrees(math.atan2(d[1], d[0])) % 180.0, 4) % 180.0


def axes_for(model: ModelData, rules: dict) -> list[str]:
    axes = (model.predio or {}).get("eixos") or rules.get("eixos_padrao", ["A", "1"])
    return [a.strip().upper() for a in axes]


def locate(model: ModelData, rules: dict, frame_rotation: float | None = None) -> dict:
    """Ponto de referência do modelo em coordenadas do arquivo (metros)."""
    axes = axes_for(model, rules)
    result = {"arquivo": model.arquivo, "caminho": model.caminho,
              "predio": (model.predio or {}).get("id"), "disciplina": model.disciplina,
              "origem": model.origem, "eixos": f"{axes[0]} x {axes[1]}", "metodo": "nenhum",
              "x": None, "y": None, "z": None, "rotacao": None, "interpretacao": False,
              "zero_dx": None, "zero_dy": None, "eixos_disponiveis": "", "observacao": ""}

    if model.grids:
        available = sorted({t for _, _, ax in model.grids for t in ax})
        result["eixos_disponiveis"] = ", ".join(available)
        for z, m, ax in model.grids:  # grade mais baixa que tenha os dois eixos
            if axes[0] in ax and axes[1] in ax:
                pt = _intersection(ax[axes[0]], ax[axes[1]])
                if pt is None:
                    continue
                w = m @ np.array([pt[0], pt[1], 0.0, 1.0])
                result.update(metodo="grade", x=float(w[0]), y=float(w[1]), z=float(w[2]),
                              rotacao=_axis_direction(m, ax[axes[0]]), rotacao_grade=_angle(m))
                break
        else:
            result["observacao"] = (f"Eixos {axes[0]} e {axes[1]} não encontrados na grade. "
                                    "Configure os eixos do prédio em rules/coordenadas.json.")
    elif model.columns:
        theta = math.radians(frame_rotation if frame_rotation is not None else _angle(model.site))
        rot = np.array([[math.cos(-theta), -math.sin(-theta)], [math.sin(-theta), math.cos(-theta)]])
        pts = np.array([p[:2] for _, p in model.columns])
        local = pts @ rot.T
        i = int(np.argmin(local[:, 0] + local[:, 1]))
        name, p = model.columns[i]
        result.update(metodo="pilar de canto", x=float(p[0]), y=float(p[1]), z=float(p[2]), interpretacao=True,
                      rotacao_quadro=math.degrees(theta),
                      observacao=(f"Modelo sem grade: usado o pilar do canto inferior esquerdo (“{name}”), "
                                  f"medido nos eixos {'da arquitetura' if frame_rotation is not None else 'do próprio modelo'}. "
                                  "Interpretação a conferir; recomendado exportar com grade."))
    else:
        result["observacao"] = "Sem grade e sem pilares selecionáveis: ponto de referência não localizado."

    if result["x"] is not None:
        local = np.linalg.inv(model.site) @ np.array([result["x"], result["y"], result["z"], 1.0])
        result["zero_dx"], result["zero_dy"] = float(local[0]), float(local[1])
    return result


def compare(results: list[dict], rules: dict) -> list[dict]:
    """Deltas de cada modelo em relação ao modelo de referência do prédio."""
    tol_pos, tol_rot = rules["tolerancia_posicao_m"], rules["tolerancia_rotacao_graus"]
    order = [d.upper() for d in rules.get("disciplinas_referencia", ["AQ", "A"])]
    by_building: dict[str, list[dict]] = {}
    for r in results:
        by_building.setdefault(r["predio"] or "", []).append(r)

    for building, items in by_building.items():
        located = [r for r in items if r["x"] is not None]
        ref = None
        for d in order:
            ref = next((r for r in located if r["disciplina"].upper() == d and r["metodo"] == "grade"), None)
            if ref:
                break
        for r in items:
            r["referencia"] = ref["arquivo"] if ref else ""
            r.update(dx=None, dy=None, dz=None, distancia=None, drot=None)
            zero_ok = r["zero_dx"] is not None and math.hypot(r["zero_dx"], r["zero_dy"]) <= tol_pos
            r["zero_zero"] = "sim" if zero_ok else ("não" if r["zero_dx"] is not None else "–")
            if not building:
                r["situacao"] = "prédio não configurado"
            elif r["x"] is None:
                r["situacao"] = "ponto de referência não localizado"
            elif ref is None:
                r["situacao"] = "sem modelo de referência (arquitetura) no prédio"
            elif r is ref:
                r["situacao"] = "referência"
                r.update(dx=0.0, dy=0.0, dz=0.0, distancia=0.0, drot=0.0 if r["rotacao"] is not None else None)
            else:
                r["dx"], r["dy"], r["dz"] = r["x"] - ref["x"], r["y"] - ref["y"], r["z"] - ref["z"]
                r["distancia"] = math.hypot(r["dx"], r["dy"])
                if r["rotacao"] is not None and ref["rotacao"] is not None:
                    r["drot"] = _norm_angle(r["rotacao"] - ref["rotacao"])
                pos_ok = r["distancia"] <= tol_pos
                rot_ok = r["drot"] is None or abs(r["drot"]) <= tol_rot
                r["situacao"] = "coincide com a referência" if pos_ok and rot_ok else "divergente"
            # alvo sugerido para a correção: posição e rotação da referência, cota do próprio modelo
            if ref and r["x"] is not None and r is not ref:
                r["alvo"] = {"x": ref["x"], "y": ref["y"], "z": r["z"],
                             "rotacao": ref["rotacao"] if r["rotacao"] is not None else None}
            else:
                r["alvo"] = None
    return results


def analyze_paths(paths: list[Path], rules: dict, on_file=None) -> list[dict]:
    """Abre cada arquivo uma vez; localiza primeiro as grades (definem a rotação do prédio)."""
    models = []
    for p in paths:
        if on_file:
            on_file(p)
        models.append(extract(ifcopenshell.open(str(p)), p.name, str(p), rules))

    results: dict[str, dict] = {}
    building_rotation: dict[str, tuple[int, float]] = {}  # prédio -> (prioridade, rotação)
    order = [d.upper() for d in rules.get("disciplinas_referencia", ["AQ", "A"])]
    for m in models:
        if m.grids:
            r = locate(m, rules)
            results[m.caminho] = r
            pid = r["predio"]
            if pid and r["metodo"] == "grade" and m.disciplina.upper() in order:
                prev = building_rotation.get(pid)
                if prev is None or order.index(m.disciplina.upper()) < prev[0]:
                    # sistema X/Y da grade da arquitetura: define o "canto inferior esquerdo"
                    building_rotation[pid] = (order.index(m.disciplina.upper()), r["rotacao_grade"])
    for m in models:
        if m.caminho not in results:
            rot = building_rotation.get((m.predio or {}).get("id"))
            results[m.caminho] = locate(m, rules, rot[1] if rot else None)
    compared = compare([results[m.caminho] for m in models], rules)

    # Ajuda a conferir o pilar de canto: distância do pilar mais próximo ao ponto de referência
    # da arquitetura. Perto de zero = modelo alinhado (a escolha do canto é que estava errada).
    by_file = {r["arquivo"]: r for r in compared}
    for m in models:
        r = by_file[m.arquivo]
        r["pilar_mais_proximo_m"] = None
        ref = by_file.get(r.get("referencia") or "")
        if r["metodo"] == "pilar de canto" and ref and ref["x"] is not None and m.columns:
            pts = np.array([p[:2] for _, p in m.columns])
            r["pilar_mais_proximo_m"] = float(np.min(np.hypot(pts[:, 0] - ref["x"], pts[:, 1] - ref["y"])))
    return compared


# --------------------------------------------------------------- correção

def correction_matrix(current: dict, target: dict, rotate_deg: float) -> np.ndarray:
    """Leva o ponto atual ao alvo, girando 'rotate_deg' graus em torno do ponto atual (eixo Z)."""
    a = math.radians(rotate_deg)
    rz = np.array([[math.cos(a), -math.sin(a), 0, 0], [math.sin(a), math.cos(a), 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]])
    to_origin = np.eye(4)
    to_origin[:3, 3] = [-current["x"], -current["y"], -current["z"]]
    to_target = np.eye(4)
    to_target[:3, 3] = [target["x"], target["y"], target["z"]]
    return to_target @ rz @ to_origin


def _set_placement(f: ifcopenshell.file, lp, m_units: np.ndarray) -> None:
    def direction(v):
        v = v / np.linalg.norm(v)
        return f.create_entity("IfcDirection", [float(round(c, 12)) for c in v])

    lp.RelativePlacement = f.create_entity(
        "IfcAxis2Placement3D",
        Location=f.create_entity("IfcCartesianPoint", [float(round(c, 9)) for c in m_units[:3, 3]]),
        Axis=direction(m_units[:3, 2]),
        RefDirection=direction(m_units[:3, 0]),
    )


def apply_correction(f: ifcopenshell.file, current: dict, target: dict, rotate_deg: float) -> dict:
    """Aplica a correção a todos os posicionamentos absolutos. Retorna o resumo da alteração."""
    scale = ifcopenshell.util.unit.calculate_unit_scale(f)
    c = correction_matrix(current, target, rotate_deg)
    changed = 0
    for lp in sorted(f.by_type("IfcLocalPlacement"), key=lambda e: e.id()):
        if lp.PlacementRelTo is not None:
            continue
        m = _matrix_m(lp, scale)
        new = c @ m
        new[:3, 3] /= scale
        _set_placement(f, lp, new)
        changed += 1
    desc = list(f.header.file_description.description)
    desc.append(
        f"ifc-normalizer {__version__} [Coordenadas: ({current['x']:.3f}, {current['y']:.3f}, {current['z']:.3f}) -> "
        f"({target['x']:.3f}, {target['y']:.3f}, {target['z']:.3f}), giro {rotate_deg:.4f} graus]"
    )
    f.header.file_description.description = tuple(desc)
    return {"posicionamentos_alterados": changed, "deslocamento_x": target["x"] - current["x"],
            "deslocamento_y": target["y"] - current["y"], "deslocamento_z": target["z"] - current["z"],
            "giro_graus": rotate_deg}


def correct_file(path: Path, out_path: Path, current: dict, target: dict, rotate_deg: float, rules: dict) -> dict:
    """Corrige o arquivo, grava em out_path e confere o resultado (ponto deve cair no alvo)."""
    f = ifcopenshell.open(str(path))
    summary = apply_correction(f, current, target, rotate_deg)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    f.write(str(out_path))

    # Sem grade: o canto é medido no mesmo sistema de eixos da medição original, girado junto.
    frame = None if current.get("metodo") == "grade" else (current.get("rotacao_quadro") or 0.0) + rotate_deg
    check = locate(extract(f, path.name, str(out_path), rules), rules, frame_rotation=frame)
    residual = None
    if check["x"] is not None:
        residual = math.hypot(check["x"] - target["x"], check["y"] - target["y"])
    return {"arquivo": path.name, "saida": str(out_path), **summary,
            "conferencia_x": check["x"], "conferencia_y": check["y"], "conferencia_z": check["z"],
            "conferencia_rotacao": check["rotacao"], "residuo_m": residual,
            "ok": residual is not None and residual <= rules["tolerancia_posicao_m"],
            # sem grade não há direção de eixo para medir: a rotação depende de quem corrigiu
            "rotacao_conferida": current.get("rotacao") is not None}


# ------------------------------------------------------------- pipeline

def suggested_rotation(result: dict) -> float:
    """Giro que alinha a direção do eixo à da referência (0 quando não há como medir)."""
    target = result.get("alvo") or {}
    if result.get("rotacao") is None or target.get("rotacao") is None:
        return 0.0
    return -_norm_angle(result["rotacao"] - target["rotacao"])


def correct_divergent(results: list[dict], out_dir: Path, rules: dict) -> list[dict]:
    """Corrige os modelos divergentes para o alvo sugerido (posição e rotação da referência)."""
    done = []
    for r in results:
        if r.get("situacao") != "divergente" or not r.get("alvo"):
            continue
        done.append(correct_file(Path(r["caminho"]), out_dir / r["arquivo"], r, r["alvo"],
                                 suggested_rotation(r), rules))
    return done
