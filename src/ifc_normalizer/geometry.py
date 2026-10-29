"""Orientação de elementos a partir da geometria (somente leitura).

Usado pelas regras com condição "geometria". A geometria é triangulada pelo IfcOpenShell
em coordenadas globais (metros) e analisada por componentes principais dos vértices:
- L1 >= L2 >= L3: extensões ao longo dos três eixos principais;
- peça alongada (L1 >= razao_alongamento_min * L2): vertical / horizontal / inclinado,
  pelo ângulo do eixo maior com a vertical;
- peça plana (L3 <= razao_espessura_max * L2): plano_horizontal / plano_vertical /
  plano_inclinado, pelo ângulo da normal com a vertical;
- demais: compacto.

É uma heurística: o resultado vai para a aba de revisão do relatório para conferência.
"""

from __future__ import annotations

import math
import multiprocessing
from dataclasses import dataclass

import ifcopenshell
import ifcopenshell.geom
import numpy as np

ORIENTATIONS = (
    "vertical", "horizontal", "inclinado",
    "plano_horizontal", "plano_vertical", "plano_inclinado",
    "compacto", "sem_geometria",
)


@dataclass(frozen=True)
class GeometrySettings:
    angle_tolerance: float = 10.0       # graus
    elongation_min: float = 3.0         # L1 / L2
    thickness_max: float = 0.25         # L3 / L2

    @classmethod
    def from_rules(cls, data: dict | None) -> "GeometrySettings":
        data = data or {}
        return cls(
            angle_tolerance=float(data.get("tolerancia_angular_graus", cls.angle_tolerance)),
            elongation_min=float(data.get("razao_alongamento_min", cls.elongation_min)),
            thickness_max=float(data.get("razao_espessura_max", cls.thickness_max)),
        )


def classify_points(points: np.ndarray, cfg: GeometrySettings) -> dict:
    """Classifica uma nuvem de vértices (N x 3, metros)."""
    if points.shape[0] < 2:
        return {"orientacao": "sem_geometria"}
    centered = points - points.mean(axis=0)
    _, vectors = np.linalg.eigh(centered.T @ centered)
    proj = centered @ vectors
    extents = proj.max(axis=0) - proj.min(axis=0)
    order = np.argsort(extents)[::-1]
    l1, l2, l3 = (float(extents[i]) for i in order)
    axis, normal = vectors[:, order[0]], vectors[:, order[2]]
    if l1 <= 1e-9:
        return {"orientacao": "sem_geometria"}

    def angle_with_vertical(v) -> float:
        return math.degrees(math.acos(min(1.0, abs(float(v[2])) / float(np.linalg.norm(v)))))

    tol = cfg.angle_tolerance
    info = {"comprimento_m": round(l1, 3), "largura_m": round(l2, 3), "espessura_m": round(l3, 3)}
    if l2 <= 1e-9 or l1 >= cfg.elongation_min * l2:
        ang = angle_with_vertical(axis)
        kind = "vertical" if ang <= tol else "horizontal" if ang >= 90 - tol else "inclinado"
        return {"orientacao": kind, "angulo_vertical_graus": round(ang, 1), **info}
    if l3 <= cfg.thickness_max * l2:
        ang = angle_with_vertical(normal)
        kind = "plano_horizontal" if ang <= tol else "plano_vertical" if ang >= 90 - tol else "plano_inclinado"
        return {"orientacao": kind, "angulo_vertical_graus": round(ang, 1), **info}
    return {"orientacao": "compacto", "angulo_vertical_graus": None, **info}


def classify_elements(f: ifcopenshell.file, elements: list, cfg: GeometrySettings) -> dict[str, dict]:
    """GlobalId -> classificação. Elementos sem geometria processável ficam 'sem_geometria'."""
    result = {e.GlobalId: {"orientacao": "sem_geometria"} for e in elements}
    with_geometry = [e for e in elements if getattr(e, "Representation", None)]
    if not with_geometry:
        return result
    settings = ifcopenshell.geom.settings()
    settings.set("use-world-coords", True)
    iterator = ifcopenshell.geom.iterator(settings, f, multiprocessing.cpu_count(), include=with_geometry)
    if iterator.initialize():
        while True:
            shape = iterator.get()
            points = np.asarray(shape.geometry.verts, dtype=float).reshape(-1, 3)
            result[shape.guid] = classify_points(points, cfg)
            if not iterator.next():
                break
    return result
