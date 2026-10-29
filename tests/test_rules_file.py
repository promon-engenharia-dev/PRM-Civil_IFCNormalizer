"""O arquivo de regras versionado precisa estar sempre válido."""

from ifc_normalizer import rules


def test_project_rules_file_is_valid():
    rs = rules.load_normalization_rules()
    assert rs.rules
    ids = [r["id"] for r in rs.rules]
    assert len(ids) == len(set(ids))


def test_geometric_rules_stay_proposed():
    """Heurística geométrica só em teste até validação (decisão de 2026-10-25)."""
    rs = rules.load_normalization_rules()
    geo = [r for r in rs.rules if rules.uses_geometry(r["quando"])]
    assert geo
    assert all(r["status"] == "proposta" for r in geo), "regra geométrica aprovada: remova este teste só após validação"
