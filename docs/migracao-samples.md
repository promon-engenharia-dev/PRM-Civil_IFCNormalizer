# Migração para IFC4X3_ADD2 – resultado nas amostras (2026-10-25)

`ifc-normalizer migrate samples -o out/migrado` (21 arquivos; relatório por arquivo em
`out/migrado/<arquivo>.migracao.xlsx`).

## Resultado geral
- 20 arquivos migrados (12 IFC2X3 e 5 IFC4 → IFC4X3_ADD2; 1 já estava no alvo e foi copiado sem alteração).
- **Nenhuma entidade falhou.** Em todos os arquivos: 100% dos GlobalId preservados, mesma
  quantidade de produtos com geometria, `IfcLocalPlacement`, Psets e layers.
- Tempo: de 1 s a 4 min (Civil 3D drenagem, 128 MB).

## O que a migração alterou
| Evento | Ocorrências | Observação |
|---|---|---|
| `IfcPresentationStyleAssignment` → `IfcStyledItem.Styles` | 12 685 | Estilos visuais; entidade removida no IFC4X3 |
| `IfcExtendedMaterialProperties` → `IfcMaterialProperties` | 786 | Propriedades de material (IFC2X3) |
| `IfcDoorStyle` → `IfcDoorType` / `IfcWindowStyle` → `IfcWindowType` | 28 / 23 | `PredefinedType` = NOTDEFINED (padrão) |
| `IfcWindowStyle.OperationType` → `IfcWindowType.PartitioningType` | 23 | Regra em `rules/migracao.yaml` |
| `IfcStairFlight.NumberOfRiser` → `NumberOfRisers` | 7 | Regra em `rules/migracao.yaml` |
| `ConstructionType` / `Sizeable` descartados | 51 | Todos `NOTDEFINED` / `False` – sem perda de informação |

## Erros de schema no resultado (todos herdados da origem)
| Arquivo | Erro | Encaminhamento |
|---|---|---|
| RVT 0313 A (APOIO, ESCRITÓRIO) | Bebedouro industrial exportado como `IfcBuildingElementProxy` com portas hidráulicas; no IFC4+ só `IfcDistributionElement` tem portas | Reclassificar no de-para (ex.: `IfcSanitaryTerminal`) |
| ZTO-13-04-ST4 | 4 `IfcOpeningElement` com **GlobalId duplicado** (já duplicado no arquivo Revit) | Decisão pendente |
| ZTO-13-02-IH-PAVIMENTACAO | `IfcMapConversion` sem Eastings/Northings e CRS `EPSG:0` (já assim na origem) | Depende da decisão de georreferenciamento |

## Observações
- `IfcWallStandardCase` existe no IFC4X3_ADD2 como classe obsoleta; a migração a mantém.
  Converter para `IfcWall` é decisão de normalização, não de migração.
- O cabeçalho `ViewDefinition [CoordinationView_V2.0]` é mantido como veio da origem (é MVD de IFC2X3).
  O cabeçalho ganha uma linha `ifc-normalizer <versão> [Migrate: <origem> -> IFC4X3_ADD2]`.
