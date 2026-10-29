"""Localização de recursos, no repositório e no executável empacotado."""

from __future__ import annotations

import sys
from pathlib import Path


def app_dir() -> Path:
    """Raiz da aplicação: pasta do .exe (empacotado) ou raiz do repositório."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def rules_dir() -> Path:
    """Regras ficam fora do executável para a equipe poder editá-las."""
    return app_dir() / "rules"


def ui_dir() -> Path:
    return Path(__file__).resolve().parent / "gui" / "ui"
