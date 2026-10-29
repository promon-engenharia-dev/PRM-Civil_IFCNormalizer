# Diagnóstico Fase 1 – amostras de 2025-11 a 2026-07

Gerado com `ifc-normalizer profile samples -o out/perfil` (21 arquivos).
Detalhe completo em `out/perfil/diagnostico.xlsx` (não versionado).

## 1. Schema – bloqueio principal
| Origem | Arquivos | Schema |
|---|---|---|
| Revit (A, C, AQ, ST4) | 11 | IFC2X3 |
| Revit (ST4 pré-moldado) | 1 | **IFC4X3_ADD2** |
| Tekla (S, ST3) | 5 | IFC2X3 |
| Civil 3D (IH) | 5 | IFC4 |

Só 1 de 21 arquivos está no schema-alvo. Em IFC2X3, `IfcBeam`, `IfcColumn`, `IfcMember`,
`IfcPlate`, `IfcDoor` etc. **não têm `PredefinedType`**, e `IfcFacility`, `IfcRoad`,
`IfcRailway`, `IfcCourse`, `IfcEarthworksFill` e outras classes de infraestrutura não existem.

## 2. Estrutura espacial
- **Revit**: `IfcProject > IfcSite "Default" > IfcBuilding (sem nome) > IfcBuildingStorey`.
  Pavimentos são níveis de modelagem (`FUNDAçãp`, `teste`, `Ref. venezianas`, `350,935`…),
  com acentos e nomes inconsistentes entre arquivos do mesmo prédio.
  Um ST4 tem o `IfcSite` nomeado com o nome de um DirectShape de topografia.
- **Tekla**: `IfcSite/IfcBuilding/IfcBuildingStorey` todos `"Undefined"`, `RefLatitude` 0.
  `IfcProject.Name` varia (`Trimble Brasil Ltda`, nome do prédio, código).
- **Civil 3D**: **sem `IfcSite`**; `IfcBuilding` direto sob o projeto.

## 3. Classificação
- **Civil 3D**: 100% `IfcBuildingElementProxy`, `Name` vazio. A informação está nos
  layers (`LASTRO`, `TRILHOS`, `SUBBASE`, `H-TUBO`…) e nos Psets `Corridor *`.
- **Tekla**: parafusos, porcas, arruelas e chumbadores químicos exportados como **`IfcBeam`**
  (`ARRUELA`, `NUT`, `QUIMICO`); 446 `IfcCovering` com `Name = EMBUTIMENTO=70`;
  5 982 `IfcElementAssembly`. `Name` = marca/posição (`VIGA`, `LONGARINA`, `CORRENTE`),
  `Description` = perfil.
- **Revit**: 1 837 proxies (C: até 78% dos elementos; ex.: `FORMA`, `CONCRETO_MAGRO`,
  `REGULARIZACAO_DE_CAVA`); rufos como `IfcBeam`; `Name` = `Família:Tipo:ElementId`.

## 4. Layers
- Nenhum layer no padrão `GM_<GRUPO>`.
- Revit A/AQ: códigos de EAP com acento (`06.01.04_AÇO/FERRO`). Revit C/ST4: padrão AIA (`S-BEAM-____-OTLN`).
- Tekla: fase (`1 Fase 1`); ~50% dos elementos sem layer.
- Civil 3D: layers de CAD (`0`, `BasicCurb`, `CALÇADA`).

## 5. Psets Promon
- **Nenhum arquivo tem `PRM-Parametros`.**
- Revit A/C: `PRM_Dimensoes`, `PRM_Construtibilidade`.
- Tekla: `PROMON-AWP` (todos vazios), `PROMON-PRJ`, `PROMON-BARRAS`, `PROMON-CHAPAS`
  (`ST3_COD_ATIV`, `ST3_MATERIAL`, `ST3_PERFIL`, `ST3_PESO`…).
- Civil 3D: `PROMON-DORMENTE`, `PROMON-TERRAPLENAGEM` em 1–2 objetos.

## 6. Coordenadas
- Nenhum arquivo tem `IfcMapConversion` válido (um Civil 3D tem `EPSG:0`).
- Revit: origem do Site em coordenadas compartilhadas (ex. 32 921 / 4 442 / 330,6), rotação −90°, −42°, 48°, 90° conforme o prédio.
- Tekla: Site sem rotação, origem diferente do Revit do mesmo prédio
  (ex. 0313: Revit C `[31624, 4700]` x Tekla `[31600, 4700]`).
- Revit pré-moldado (IFC4X3) exportado pelo **Ponto base do projeto** (origem em −55 / 13).
