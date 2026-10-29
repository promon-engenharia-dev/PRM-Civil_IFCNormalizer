import ifcopenshell
import ifcopenshell.api
import ifcopenshell.guid
import pytest

from conftest import build_minimal_model
from ifc_normalizer import migrate, validate


@pytest.fixture
def ifc2x3_file(tmp_path):
    f = build_minimal_model("IFC2X3")
    owner = f.by_type("IfcOwnerHistory")[0] if f.by_type("IfcOwnerHistory") else None
    window_style = f.create_entity(
        "IfcWindowStyle", ifcopenshell.guid.new(), owner, "Janela tipo",
        ConstructionType="ALUMINIUM", OperationType="DOUBLE_PANEL_VERTICAL",
        ParameterTakesPrecedence=False, Sizeable=False,
    )
    door_style = f.create_entity(
        "IfcDoorStyle", ifcopenshell.guid.new(), owner, "Porta tipo",
        OperationType="SINGLE_SWING_LEFT", ConstructionType="WOOD",
        ParameterTakesPrecedence=False, Sizeable=False,
    )
    window = f.create_entity("IfcWindow", ifcopenshell.guid.new(), owner, "Janela 1")
    f.create_entity("IfcStairFlight", ifcopenshell.guid.new(), owner, "Lance 1", NumberOfRiser=12)
    f.create_entity(
        "IfcRelDefinesByType", ifcopenshell.guid.new(), owner,
        RelatedObjects=[window], RelatingType=window_style,
    )
    path = tmp_path / "SINTETICO_2X3.ifc"
    f.write(str(path))
    return path, window_style.GlobalId, door_style.GlobalId


def _events(result, kind):
    return [e for e in result.log.events if e["tipo"] == kind]


def test_migrates_ifc2x3_to_ifc4x3(ifc2x3_file):
    path, window_guid, door_guid = ifc2x3_file
    result = migrate.migrate_file(path)

    assert result.file.schema_identifier == "IFC4X3_ADD2"
    assert result.steps == ["IFC4", "IFC4X3_ADD2"]
    assert all(c["ok"] for c in result.checks), result.checks
    assert not _events(result, migrate.FAILED)

    window_type = result.file.by_guid(window_guid)
    assert window_type.is_a() == "IfcWindowType"
    assert window_type.PartitioningType == "DOUBLE_PANEL_VERTICAL"
    assert window_type.PredefinedType == "NOTDEFINED"
    assert result.file.by_guid(door_guid).is_a() == "IfcDoorType"
    assert result.file.by_type("IfcStairFlight")[0].NumberOfRisers == 12
    assert not _events(result, migrate.ATTR_DROPPED) or all(
        e["atributo"] != "NumberOfRiser" for e in _events(result, migrate.ATTR_DROPPED)
    )


def test_records_every_change(ifc2x3_file):
    path, window_guid, door_guid = ifc2x3_file
    result = migrate.migrate_file(path)

    converted = {(e["global_id"], e["classe_destino"]) for e in _events(result, migrate.CLASS_CHANGED)}
    assert (window_guid, "IfcWindowType") in converted
    assert (door_guid, "IfcDoorType") in converted

    dropped = {(e["global_id"], e["atributo"], e["valor"]) for e in _events(result, migrate.ATTR_DROPPED)}
    assert (window_guid, "ConstructionType", "ALUMINIUM") in dropped
    assert (door_guid, "ConstructionType", "WOOD") in dropped

    renamed = {(e["global_id"], e["atributo"]) for e in _events(result, migrate.ATTR_RENAMED)}
    assert (window_guid, "OperationType -> PartitioningType") in renamed

    defaults = {(e["global_id"], e["atributo"]) for e in _events(result, migrate.DEFAULT_SET)}
    assert (window_guid, "PredefinedType") in defaults


def test_result_is_schema_valid(ifc2x3_file):
    path, *_ = ifc2x3_file
    result = migrate.migrate_file(path)
    assert validate.validate_schema(result.file) == []


def test_deterministic(ifc2x3_file, tmp_path):
    path, *_ = ifc2x3_file
    a, b = tmp_path / "a.ifc", tmp_path / "b.ifc"
    migrate.migrate_file(path).file.write(str(a))
    migrate.migrate_file(path).file.write(str(b))
    assert a.read_bytes() == b.read_bytes()


def test_source_untouched(ifc2x3_file):
    path, *_ = ifc2x3_file
    before = path.read_bytes()
    migrate.migrate_file(path)
    assert path.read_bytes() == before


def test_target_schema_is_passthrough(minimal_ifc):
    result = migrate.migrate_file(minimal_ifc)
    assert result.steps == []
    assert result.log.events == []
