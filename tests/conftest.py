"""Fixtures sintéticas (sem dados de cliente)."""

import ifcopenshell
import ifcopenshell.api
import pytest


def _register_owner(f: ifcopenshell.file) -> None:
    """IFC2X3 exige OwnerHistory: cria usuário e aplicação no arquivo.

    A API do IfcOpenShell encontra os dois no próprio arquivo; não alteramos
    ifcopenshell.api.owner.settings (estado global que vazaria para outros testes).
    """
    person = f.create_entity("IfcPerson", FamilyName="Teste")
    org = f.create_entity("IfcOrganization", Name="Promon")
    f.create_entity("IfcPersonAndOrganization", ThePerson=person, TheOrganization=org)
    f.create_entity(
        "IfcApplication", ApplicationDeveloper=org, Version="0",
        ApplicationFullName="ifc-normalizer tests", ApplicationIdentifier="tests",
    )


def build_minimal_model(schema: str = "IFC4X3_ADD2") -> ifcopenshell.file:
    f = ifcopenshell.api.run("project.create_file", version=schema)
    if schema == "IFC2X3":
        _register_owner(f)
    project = ifcopenshell.api.run("root.create_entity", f, ifc_class="IfcProject", name="Projeto")
    ifcopenshell.api.run("unit.assign_unit", f)
    ctx = ifcopenshell.api.run("context.add_context", f, context_type="Model")
    body = ifcopenshell.api.run(
        "context.add_context", f, context_type="Model", context_identifier="Body",
        target_view="MODEL_VIEW", parent=ctx,
    )
    site = ifcopenshell.api.run("root.create_entity", f, ifc_class="IfcSite", name="Site")
    building = ifcopenshell.api.run("root.create_entity", f, ifc_class="IfcBuilding", name="Edificio")
    storey = ifcopenshell.api.run("root.create_entity", f, ifc_class="IfcBuildingStorey", name="Terreo")
    ifcopenshell.api.run("aggregate.assign_object", f, relating_object=project, products=[site])
    ifcopenshell.api.run("aggregate.assign_object", f, relating_object=site, products=[building])
    ifcopenshell.api.run("aggregate.assign_object", f, relating_object=building, products=[storey])

    col = ifcopenshell.api.run("root.create_entity", f, ifc_class="IfcColumn", name="Pilar:P1:123456")
    proxy = ifcopenshell.api.run("root.create_entity", f, ifc_class="IfcBuildingElementProxy", name="COLUMN")
    for el in (col, proxy):
        ifcopenshell.api.run("spatial.assign_container", f, relating_structure=storey, products=[el])
        rep = ifcopenshell.api.run(
            "geometry.add_wall_representation", f, context=body, length=1.0, height=3.0, thickness=0.3
        )
        ifcopenshell.api.run("geometry.assign_representation", f, product=el, representation=rep)

    layer = ifcopenshell.api.run("layer.add_layer", f, name="GM_ESTRUTURA")
    ifcopenshell.api.run("layer.assign_layer", f, items=[col.Representation.Representations[0]], layer=layer)

    pset = ifcopenshell.api.run("pset.add_pset", f, product=col, name="PRM-Parametros")
    ifcopenshell.api.run("pset.edit_pset", f, pset=pset, properties={"PRM_EAP": "1.2.3", "PRM_CodigoMaterial": ""})
    return f


@pytest.fixture
def minimal_ifc(tmp_path):
    path = tmp_path / "ZTO-00-00-ST4-SINTETICO.ifc"
    build_minimal_model().write(str(path))
    return path
