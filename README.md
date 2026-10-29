# IFC Normalizer – Promon (PRM-BIM)

Normaliza IFCs exportados de **Revit, Tekla e Civil 3D** para o padrão Promon / BEP,
preservando a geometria e gerando relatório auditável das alterações.

Detalhes de objetivo, princípios, fases e decisões: ver [CLAUDE.md](CLAUDE.md).

## Instalação (desenvolvimento)
```bash
py -3.13 -m venv .venv
.venv\Scripts\activate
pip install -e .[dev]
```

## Uso
```bash
# Fase 1 – diagnóstico (somente leitura) -> out/perfil/diagnostico.xlsx
ifc-normalizer profile samples -o out/perfil

# Migração IFC2X3/IFC4 -> IFC4X3_ADD2 -> out/migrado/<arquivo>.ifc + <arquivo>.migracao.xlsx
ifc-normalizer migrate samples -o out/migrado

# Normalização (migra se preciso) -> out/normalizado/<arquivo>.ifc + .normalizacao.xlsx
ifc-normalizer normalize samples -o out/normalizado
# Simulação incluindo regras com status "proposta"
ifc-normalizer normalize samples --incluir-propostas -o out/simulacao
# Validar o JSON de regras
ifc-normalizer check-rules
# Planilha para validar as regras com o BEP (a partir de uma simulação)
ifc-normalizer pacote-validacao out/simulacao -o out/validacao/pacote.xlsx
# Estrutura espacial e nomes: rules/estrutura.json (padrão) ou --estrutura outro.json
ifc-normalizer normalize samples --estrutura rules/estrutura.json -o out/normalizado
# Coordenadas: eixo de referência no zero-zero e coerente entre modelos do prédio
ifc-normalizer coordenadas samples -o out/coordenadas [--corrigir]
```

Formato das regras e ciclo de aprovação com o BEP: [docs/regras.md](docs/regras.md).

### Verificação e correção (`rules/verificacao.json` → `*.verificacao.xlsx`)
Roda ao normalizar e ao converter (desligar com `--sem-correcao`). Segue as 4 categorias do
[validador buildingSMART](https://buildingsmart.github.io/validate/): sintaxe STEP, schema IFC (atributos e WHERE
rules), regras normativas (códigos bSI) e boas práticas. O texto das regras do validador (repositório
`buildingSMART/ifc-gherkin-rules`, licença MIT) fica em `ref/bsi-validacao/regras.json`.
| Aba | Conteúdo |
|---|---|
| Resumo | Sintaxe, correções, pendências e erros de schema que restam, por categoria |
| Por_Regra | Cada código: encontrados, corrigidos, pendentes |
| Correcoes | Entidade, campo, antes, depois |
| Pendencias | O que precisa de decisão, com sugestão |
| Erros_Schema_Restantes | Validação final (schema + WHERE rules) |
| Familias | Entidades por família, como as abas do IFC File Analyzer (NIST) |
| Preenchimento_Atributos | Por classe: % com Name, Description, ObjectType, Tag, LongName, PredefinedType |
| Relacoes_por_Classe | Por classe: % na estrutura espacial, com tipo, Pset, quantidades, material, layer, geometria |
| Cobertura_bSI | Cada regra do validador bSI: qual código da ferramenta a verifica e com que ação (ou "não verificada") |

Sintaxe STEP: conferida no texto do arquivo recebido (marcadores, #id repetido, referência a #id inexistente,
posicionamento em ciclo), como o IFA reporta. Bases de referência em `ref/bsi-validacao/` e `ref/nist-ifa/`
(conhecimento extraído do IFA 3.10, software de uso livre da NIST).
Coordenadas: [docs/coordenadas.md](docs/coordenadas.md).

### Estrutura espacial e nomes (`rules/estrutura.json`)
Aplicada ao normalizar, depois das regras de elementos:
- **Civil 3D (IH)**: o `IfcBuilding` exportado vira `IfcSite` (mesmo GlobalId e posicionamento); a
  pavimentação vai para um `IfcRoad` agregado ao site. Estrutura diferente do esperado vira pendência
  `estrutura_espacial_nao_reconhecida`, sem ser forçada.
- **Nomes**: `Name`/`LongName` de `IfcProject`, `IfcSite` e `IfcBuilding` (prédio pelo padrão do nome do
  arquivo). Vazio = manter. Na interface, o cartão "Estrutura espacial e nomes" mostra esses valores, permite
  ajustar para a execução e salvar nas regras.
Cada mudança entra na aba `Alteracoes` do relatório (regras `EST-*`).

### Relatório de normalização (`*.normalizacao.xlsx`)
| Aba | Conteúdo |
|---|---|
| Resumo | Origem, regras usadas, totais de alterações e pendências por motivo |
| Alteracoes | Elemento, campo, antes, depois, regra e status da regra |
| Pendencias_Agrupadas | Pendências agrupadas, com colunas `sugestao_*` para virar regra |
| Pendencias | Uma linha por elemento pendente |
| Uso_Regras | Quantos elementos cada regra casou e alterou |
| Validacao_Schema | Erros de schema do arquivo final |

### Relatório de migração (`*.migracao.xlsx`)
| Aba | Conteúdo |
|---|---|
| Resumo | Schemas, etapas, total de eventos por tipo |
| Verificacoes | Origem x resultado: GlobalId, produtos com geometria, placements, Psets, layers |
| Validacao_Schema | Erros de schema do arquivo migrado (pendências para a normalização) |
| Eventos_Agrupados | Contagem por etapa / tipo / classe / atributo |
| Detalhe_Objetos | Cada evento em objeto com GlobalId (elemento, tipo, relação, Pset) |
| Detalhe_Outros | Eventos em entidades sem GlobalId (geometria, estilos, materiais) |

Tipos de evento: `classe_convertida`, `atributo_descartado`, `atributo_mapeado`,
`valor_descartado`, `valor_padrao_gerado`, `obrigatorio_sem_valor`, `entidade_substituida`, `falha`.

## Interface e executável
```bash
# Abrir a interface a partir do código
ifc-normalizer gui
```
A interface tem duas abas:
- **Normalização**: as ações (diagnóstico, normalizar ou só converter, coordenadas) podem ser combinadas;
  rodam nessa ordem e as coordenadas são conferidas nos arquivos gerados (subpasta `coordenadas`).
  Cada ação tem a sua aba de resultado.
- **Visualizador 3D** (somente leitura, `viewer.py` + `gui/ui/viewer.js`): malha gerada pelo
  ifcopenshell e desenhada em WebGL2, sem bibliotecas externas (funciona offline). Vários modelos
  juntos, seleção por clique, propriedades (atributos, tipo, espacial, layer, material, Psets,
  quantidades, coordenadas), visibilidade por classe, isolar/ocultar, raio-X. Armaduras opcionais.

Identidade Promon (tokens do manual de formatação em `gui/ui/style.css`, logo em `gui/ui/assets/`).
O tema claro/escuro segue o Windows (`prefers-color-scheme`); o botão "Tema" força claro ou escuro.
Gerar o executável (pasta `dist\NormalizadorIFC\`, ~210 MB, sem Python na máquina de destino):
```bash
powershell -ExecutionPolicy Bypass -File tools\build_exe.ps1
```
Distribuir a pasta inteira e instalar em caminho curto (ex.: `C:\Promon\NormalizadorIFC`).
Instruções para o usuário final em [tools/LEIA-ME.txt](tools/LEIA-ME.txt).

## Testes
```bash
pytest
```

## Estrutura
- `ref/` – arquivos de referência Promon (somente leitura)
- `rules/` – regras (de-para, migração, perfis por origem, IDS)
- `src/ifc_normalizer/` – código
- `tests/` – testes com IFCs sintéticos gerados no próprio teste
- `samples/`, `out/` – IFCs reais e saídas (não versionados)
- `docs/` – diagnósticos e notas
