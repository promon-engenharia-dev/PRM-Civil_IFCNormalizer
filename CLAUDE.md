# IFC Normalizer – Promon (PRM-BIM)

## Objetivo
Ferramenta que recebe IFCs exportados de **Revit, Tekla e Civil 3D** e devolve arquivos no
**padrão Promon / BEP**, com relatório auditável do que foi alterado.

A geometria é preservada. A ferramenta atua na camada de informação:
classe IFC e `PredefinedType`, nomes, estrutura espacial, layers, Pset `PRM-Parametros`
e coordenadas (origem/rotação).

## Princípios (não negociáveis)
1. **Editar, não recriar**: alterar o IFC de origem; nunca regenerar geometria.
2. **Regras são dados**: todo mapeamento vem de `rules/` (planilha de de-para, YAML, IDS).
   Nada de regra de negócio fixa no código.
3. **Não adivinhar**: elemento que não casa com nenhuma regra fica como está e é
   **reportado** como pendente. Nunca escolher classe IFC por suposição.
4. **Preservar GlobalId** dos elementos existentes, para manter o histórico entre emissões.
5. **Rastreabilidade**: cada alteração registrada no relatório (elemento, campo, antes, depois, regra aplicada).
6. **Determinístico**: mesma entrada + mesmas regras = mesma saída.

## Schema-alvo
- **IFC4X3_ADD2** (4.3.2.0). *Pendente: confirmar no Revit que `IFCVersion: 29` do
  `PRM-AQ-IFC.json` corresponde ao IFC4x3.*
- O schema é consultado programaticamente pelo IfcOpenShell (não ler o `.exp` manualmente).
- IFC de origem em IFC2X3/IFC4 → **a ferramenta migra para IFC4X3_ADD2** (`migrate.py`) antes de
  normalizar, e registra no relatório **tudo** o que mudou na migração (classe convertida,
  atributo descartado/convertido, entidade removida), com GlobalId preservado.

## Stack
- Python 3.11+
- `ifcopenshell` (api, util, validate), `ifctester` (IDS), `ifcpatch` quando útil
- `openpyxl` / `pandas` para ler a planilha de de-para
- `pytest` para testes
- CLI primeiro; interface gráfica só depois das fases de normalização

## Estrutura do repositório
```
ifc-normalizer/
├── CLAUDE.md
├── README.md
├── pyproject.toml
├── ref/                      # Arquivos de referência Promon (somente leitura)
│   ├── shared.parameters.PRM.txt
│   ├── PROMON_IFC_PSET.txt
│   ├── PRM-AQ-IFC.json
│   ├── bsi-validacao/        # Regras do validador buildingSMART: catálogo + texto Gherkin (regras.json, MIT)
│   └── nist-ifa/             # Conhecimento extraído do IFC File Analyzer (NIST): famílias, versões, inversos
├── rules/                    # Fonte da verdade das regras
│   ├── de-para.xlsx          # Mapeamento origem → padrão (editado por Civil + BIM)
│   ├── normalizacao.json     # Regras de normalização (status proposta/aprovada) – ver docs/regras.md
│   ├── coordenadas.json      # Prédios, eixos de referência e tolerâncias (verificação de coordenadas)
│   ├── estrutura.json        # Estrutura espacial (IH → IfcSite/IfcRoad) e nomes de Project/Site/Building
│   ├── verificacao.json      # Verificação/correção nas 4 categorias do validador bSI (sintaxe, schema, normativas, boas práticas)
│   ├── migracao.yaml         # Equivalências de classe/atributo IFC4 → IFC4X3 e valores padrão
│   ├── perfis/               # Perfis por origem: revit.yaml, tekla.yaml, civil3d.yaml
│   └── ids/                  # Exigências do BEP em IDS
├── src/ifc_normalizer/
│   ├── profile.py            # Fase 1: perfilamento/diagnóstico (somente leitura)
│   ├── migrate.py            # Migração IFC2X3/IFC4 → IFC4X3_ADD2 com registro de eventos
│   ├── rules.py              # Leitura e validação das regras
│   ├── normalize.py          # Aplicação das regras
│   ├── validate.py           # Schema + Psets + IDS
│   ├── report.py             # Relatórios (xlsx/html)
│   ├── coordinates.py        # Verificação/correção do zero-zero (eixo de referência) entre modelos
│   ├── spatial.py            # Estrutura espacial e nomes de IfcProject/IfcSite/IfcBuilding
│   ├── repair.py             # Verificação e correção (schema, regras normativas bSI, boas práticas)
│   ├── analysis.py           # Análise estilo IFA: sintaxe STEP, famílias, preenchimento de atributos, relações
│   ├── viewer.py             # Visualizador 3D: malha (ifcopenshell) e propriedades, somente leitura
│   ├── validation_pack.py    # Planilha de validação das regras com o BEP
│   ├── pipeline.py           # Fluxo por arquivo, comum à CLI e à interface
│   ├── paths.py              # Localiza rules/ (repositório ou ao lado do .exe)
│   ├── gui/                  # Interface pywebview (HTML/CSS na identidade Promon)
│   └── cli.py
├── tools/                    # build_exe.ps1 (PyInstaller), entry_gui.py, LEIA-ME.txt do pacote
├── tests/
│   └── fixtures/             # IFCs mínimos sintéticos (sem dados de cliente)
├── samples/                  # IFCs reais – NÃO versionar (.gitignore)
└── out/                      # Saídas – NÃO versionar (.gitignore)
```

## Fases
| Fase | Entrega | Status |
|---|---|---|
| 0 | Coleta: IFCs de referência AQ (Revit) e IH (Civil 3D), IFC atual do Tekla, BEP | Em andamento |
| 1 | Perfilamento/diagnóstico comparativo AQ x IH x Tekla | Próxima |
| 2 | Planilha de de-para validada (Civil + BIM Forzy) | Caminho crítico |
| 3 | Normalização: Revit → Tekla → Civil 3D | — |
| 4 | Validação (schema IFC4x3 + IDS do BEP) e execução em lote | — |

## Referências de padrão
- **AQ (Revit)** e **IH (Civil 3D)** já exportam próximo do desejado: servem como IFC de referência (golden files).
- **Tekla** ainda sem padrão definido.

### Pset Promon (`ref/PROMON_IFC_PSET.txt`)
Pset `PRM-Parametros`, de instância, aplicado a `IfcElement`:

| Propriedade IFC | Tipo no Pset | Parâmetro Revit de origem |
|---|---|---|
| Keynote | Text | PRM_Keynote |
| PRM_DescricaoAtividade | Text | – |
| PRM_EAP | Text | – |
| PRM_Comprimento | Length | – |
| PRM_Largura | Length | – |
| PRM_Altura | Length | – |
| PRM_Espessura | Length | – |
| PRM_Area | Area | – |
| PRM_Quantidade | Integer | – |
| PRM_CodigoMaterial | Text | – |
| PRM_CodigoAtividade | Text | – |
| PRM_Avanco | Real | – |

### Parâmetros de controle IFC (grupo IFC dos shared parameters)
`IfcName`, `IfcDescription`, `IfcLongName`, `IfcObjectType`, `IfcElementType`,
`IFCExportAs`, `IFCExportType`, `IfcPresentationLayer`.
- `IfcPresentationLayer`: nomenclatura conforme EAP, **sem acentos**, formato `GM_<NOME DO GRUPO DE MODELAGEM>`.

### Inconsistências já identificadas (a resolver)
- `PRM_Keynote` é referenciado no Pset, mas **não existe** no `shared.parameters.PRM.txt`.
- `PRM_Quantidade`: `NUMBER` (real) nos shared parameters x `Integer` no Pset.
- `PRM_Avanco`: `TEXT` nos shared parameters x `Real` no Pset.

### Coordenadas
- Revit: exportar usando o **Survey Point** como base (com Project Base Point o modelo saía sem a rotação).
- Critério equivalente para Tekla e Civil 3D: a definir.

## Responsabilidades
- **Decisões de engenharia do de-para**: Disciplina de Civil + equipe BIM Promon (Forzy).
- **Critérios do BEP → regras**: equipe BIM Promon (Forzy).
- **Origem Tekla** e **desenvolvimento**: a definir.

## Como trabalhar neste repositório
- Antes de alterar a lógica de normalização, verificar se a mudança deveria ser uma **regra** em `rules/`.
- Toda função que altera o IFC deve ter teste com fixture sintética em `tests/fixtures/`.
- Nunca commitar IFCs reais de cliente nem saídas (`samples/`, `out/`).
- Ao encontrar caso ambíguo de classificação: registrar como "Decisão pendente" no relatório
  e sugerir a linha para a planilha de de-para. **Não decidir sozinho.**
- Relatórios e mensagens ao usuário em **português**. Nomes de código (funções, variáveis) em inglês.
- Atualizar a seção "Decisões tomadas" abaixo sempre que uma decisão for confirmada.

## Referência normativa
- IFC 4.3.2.0 (buildingSMART): https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3/
- Conceitos fundamentais: https://standards.buildingsmart.org/IFC/DEV/IFC4_3/HTML/concepts/content.html
- bSDD (buildingSMART Data Dictionary) API: https://app.swaggerhub.com/apis-docs/buildingSMART/Dictionaries/v1
  - Uso: consultar classes, PredefinedTypes e propriedades padrão do IFC 4.3; vincular elementos a URIs do bSDD.
  - Regra: **não consultar ao vivo a cada execução**. Baixar o necessário para um cache local versionado
    (`ref/bsdd/`, com versão do dicionário e data), para manter a ferramenta determinística e offline.
- A especificação define **onde** cada informação vai (Name, ObjectType, PredefinedType, tipos, Psets, layers).
  O **conteúdo** dos nomes vem do BEP.

## Decisões tomadas
- Schema-alvo IFC4x3 (pendente confirmação do valor 29 no Revit).
- **CodigoAtividade = "ASSOCIADO"** é mantido como está (2026-10-26): reflete alinhamento do projeto.
- **Coordenadas** (2026-10-27): zero-zero no cruzamento de eixos de referência do prédio (padrão A×1,
  configurável por prédio em `rules/coordenadas.json`); referência = arquitetura (AQ/A); modelos sem
  grade usam o pilar de canto (interpretação a conferir). A ferramenta verifica e corrige (cópia
  corrigida, só posicionamentos de topo), com quadro de coordenadas na interface. Ver `docs/coordenadas.md`.
- **Verificação e correção** (2026-10-28): a ferramenta corrige, não só aponta, nas 4 categorias do validador
  buildingSMART (sintaxe STEP, schema IFC, normativas, boas práticas). Corrige sozinha o que não perde informação
  e o que é obsoleto no IFC 4.3 (valor anterior no relatório); o que exige escolha (CRS/EPSG, `IfcFlowSegment`
  genérico, nomes, GlobalId) vira pendência com sugestão. Regras em `rules/verificacao.json`, relatório
  `*.verificacao.xlsx`. Base: regras do validador bSI (`ref/bsi-validacao/regras.json`, texto Gherkin do
  repositório `ifc-gherkin-rules`; as listas da IFC102 em `verificacao.json` são conferidas contra ele por teste;
  aba `Cobertura_bSI` mostra o que a ferramenta verifica e o que não), WHERE rules do IfcOpenShell, modelos de Pset do
  IFC 4.3 e o conhecimento do IFC File Analyzer da NIST (`ref/nist-ifa/`, extraído do IFA 3.10: famílias de
  entidades, versão de cada entidade, relações inversas, conferências de sintaxe; conferido com o código-fonte
  no GitHub, idêntico). O validador online (API com
  token, envio do arquivo) não é usado: a ferramenta é offline.
- **Tipos compartilhados** (2026-10-28): tipo usado por ocorrências de classes diferentes é dividido em um tipo
  por classe (mesmo nome, Psets e representações compartilhados, GlobalId determinístico). Deixar o tipo errado
  viola a WHERE rule `CorrectTypeAssigned`.
- **`IfcWallStandardCase` → `IfcWall`** (2026-10-28): obsoleta no IFC 4.3 (regra bSI IFC102); convertida com o
  mesmo GlobalId.
- **Estrutura espacial do IH** (2026-10-27): o Civil 3D exporta `IfcProject → IfcBuilding`; a ferramenta
  converte o `IfcBuilding` em `IfcSite` (mesmo GlobalId e posicionamento). Terreno, terraplenagem e drenagem
  ficam contidos no `IfcSite`; a pavimentação vai para um `IfcRoad` agregado ao `IfcSite`. Regras em
  `rules/estrutura.json` (EST-IH-001/002).
- **Nomes de Project/Site/Building** (2026-10-27): definidos em `rules/estrutura.json` (prédios por padrão de
  arquivo) e ajustáveis na tela antes de processar (botão para salvar nas regras). Vazio = manter. O conteúdo
  dos nomes vem do BEP.
- **Tema claro/escuro** (2026-10-27): a interface segue o tema do sistema operacional, com botão
  para forçar claro ou escuro; tokens do tema escuro conforme o manual Promon (§2.1); logo
  positiva no claro e negativa no escuro (`gui/ui/assets/`).
- **Heurística geométrica só em teste** (2026-10-25): regras com condição `geometria` ficam
  `proposta` até validação; a aba `Revisao_Geometrica` registra cada interpretação.
  Nome/atributos têm precedência sobre geometria.
- **Distribuição como executável local** (2026-10-25): pasta autocontida `NormalizadorIFC\`
  gerada por `tools\build_exe.ps1` (PyInstaller, sem Python na máquina do usuário), interface
  pywebview seguindo o manual de formatação Promon. `rules\` fica ao lado do `.exe` (editável).
  Instalar em caminho curto (limite de 260 caracteres do Windows).
- **Regras de normalização em JSON** (`rules/normalizacao.json`, 2026-10-25): cada regra tem
  `status` (`proposta`/`aprovada`) e `avaliacao_bep`. Só aprovadas rodam por padrão;
  `--incluir-propostas` simula. Formato em `docs/regras.md`.
- **Migração de schema pela ferramenta** (2026-10-25): IFC2X3/IFC4 são migrados para IFC4X3_ADD2,
  com relatório de tudo o que mudou. Reexportar nos softwares de origem não é pré-requisito.
- Geometria preservada; ferramenta atua só na informação.
- De-para mantido em planilha, construído em conjunto com Civil e BIM (Forzy).
- AQ e IH como ponto de partida do padrão.
- **Classificação ST3 (metálica) e ST4 (concreto)**: elemento vertical → `IfcColumn`;
  horizontal → `IfcBeam`; plano retangular → `IfcSlab`. Elementos de fundação normalmente
  já chegam classificados. Nome e atributos ajudam na identificação.
- **Precedência de classificação**: (1) classe de origem já correta → manter;
  (2) nome/atributos casam com regra do de-para; (3) heurística geométrica; (4) nenhum → pendência.
- **Parâmetros PRM**: os shared parameters PRM são adotados em todos os arquivos (Revit, Tekla, Civil 3D).
  Valor copiado da origem ou calculado da geometria.
- **Estrutura espacial**: AQ e IH têm estruturas diferentes e convivem no mesmo projeto,
  conforme a especificação IFC 4.3.
- **Nomenclatura e layers**: seguir as regras da especificação IFC 4.3 da buildingSMART.

## Decisões em aberto
- Tolerância angular para "vertical" e "horizontal"; tratamento de peças inclinadas (contraventamentos → `IfcMember`?).
- Distinção pilar x parede (ST4) e laje x chapa (ST3, `IfcPlate`) quando a geometria é ambígua.
- Quais grupos dos shared parameters vão para elementos no IFC (geométricos/construtibilidade)
  e quais ficam só em folhas (revisão, distribuição, dados do cliente).
- Precedência entre valor copiado e calculado quando ambos existem e divergem; tolerância.
- Nomes de IfcProject/IfcSite/IfcBuilding pelo BEP (campos em `rules/estrutura.json` ainda vazios).
- Classe da superfície de terraplenagem (`C3D-TER-001` proposta: `IfcGeographicElement TERRAIN`).
- `IfcMapConversion` do Civil 3D sai inválido (Eastings/Northings/OrthogonalHeight vazios, `EPSG:0`, Scale 0);
  a descrição indica SIRGAS / UTM 22S (EPSG a confirmar).
- Classe dos `IfcFlowSegment`/`IfcFlowFitting`/`IfcFlowTerminal` genéricos do Revit (sugestão pelo tipo/Pset na verificação).
- Psets `Pset_*` do IFC2X3 que não existem no IFC 4.3 (PSE001): renomear ou mapear.
- Valores de PredefinedType obsoletos (ex.: `IfcWall` STANDARD, `IfcSpace` INTERNAL/EXTERNAL).
- Georreferenciamento: campos `GeoRef*` do `PRM-AQ-IFC.json` estão vazios
  (sem CRS/EPSG → sem `IfcMapConversion`).
- Eixos de referência dos prédios 0601 (grade só com F–R) e 0313 (sem grade); exportar o Tekla com grade?
- Coordenadas do Civil 3D (sem grade nem pilares): critério de verificação.
- Tratamento das inconsistências do Pset listadas acima.
- GlobalId duplicado na origem (ex.: `IfcOpeningElement` do Revit ST4): manter, gerar novo ou só reportar.
- MVD do cabeçalho (`CoordinationView_V2.0` herdado do IFC2X3): manter ou trocar pelo MVD de IFC4X3.
