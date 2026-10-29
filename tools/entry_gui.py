"""Ponto de entrada do executável: sem argumentos abre a interface; com argumentos, CLI."""

import sys

from ifc_normalizer.cli import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["gui"]))
