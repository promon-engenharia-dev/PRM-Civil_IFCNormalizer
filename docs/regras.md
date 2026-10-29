# Regras de normalização (`rules/normalizacao.json`)

As regras dizem **o que mudar** em cada elemento. O código só executa o que está no JSON;
elemento sem regra fica como está e aparece como **pendência** no relatório.

## Pacote de validação
`ifc-normalizer pacote-validacao <pasta da simulação> -o out/validacao/pacote.xlsx` gera a planilha
para validar as regras com pessoas que não acompanharam o desenvolvimento: aba LEIA-ME (instruções
e glossário), regras em linguagem direta com exemplo real de antes → depois, perguntas para validar
e colunas de decisão, e a revisão das peças classificadas pela forma.

## Ciclo de avaliação com o BEP
1. Regra nova entra com `"status": "proposta"`.
2. Simular: `ifc-normalizer normalize samples --incluir-propostas -o out/simulacao`
   e conferir as abas `Alteracoes` e `Uso_Regras` do relatório.
3. Avaliar contra o BEP (Civil + BIM Forzy) e registrar em `avaliacao_bep`
   (ex.: `"BEP rev.2 §4.3 – aprovado por Civil/Forzy em 2026-11-10"`).
4. Mudar para `"status": "aprovada"`. Só regras aprovadas rodam sem `--incluir-propostas`.
5. Pendências que se repetem (aba `Pendencias_Agrupadas`) viram novas regras propostas:
   as colunas `sugestao_*` existem para isso.

Validar o arquivo a qualquer momento: `ifc-normalizer check-rules`.
A validação recusa classe ou PredefinedType inexistente no IFC4X3_ADD2, `USERDEFINED` sem
`object_type`, id duplicado, comparador inválido e propriedade fora de `definicoes_pset`.

## Estrutura
```jsonc
{
  "versao": "0.1.0",
  "definicoes_pset": {               // tipo IFC de cada propriedade escrita pela ferramenta
    "PRM-Parametros": {"PRM_Altura": "IfcLengthMeasure", "...": "..."}
  },
  "tipos": {
    "reclassificar_com_elemento": true   // ver "Tipos" abaixo
  },
  "pendencias": {
    "classes_genericas": ["IfcBuildingElementProxy"],  // sem regra -> pendência
    "exigir_predefined_type": true,                     // NOTDEFINED/vazio -> pendência
    "ignorar_classes": ["IfcFeatureElement", "IfcVirtualElement"]
  },
  "regras": [ { "id": "...", "status": "proposta", "quando": {...}, "entao": {...} } ]
}
```

### Campos de cada regra
| Campo | Uso |
|---|---|
| `id` | Identificador único (ex.: `TKL-EST-001`) |
| `status` | `proposta` (só em simulação) ou `aprovada` |
| `tema` | Agrupamento no pacote de validação (ex.: "Tekla – fixadores") |
| `descricao` | O que a regra faz, em uma linha |
| `justificativa` | Por que, e alternativas consideradas |
| `perguntas` | Lista do que precisa ser respondido para aprovar (vai para o pacote de validação) |
| `avaliacao_bep` | Registro da aprovação (item do BEP, quem, quando) |
| `quando` / `entao` | Condição e resultado (abaixo) |

### `quando` (todas as condições precisam casar)
| Chave | Compara com |
|---|---|
| `origem` | `Revit`, `Tekla`, `Civil3D` (detectado pelo cabeçalho do IFC) |
| `disciplina` | Do nome do arquivo (`AQ`, `ST3`, `ST4`, `IH`, `A`, `C`, `S`…) |
| `arquivo` | Nome do arquivo |
| `classe` | Classe IFC atual (texto ou lista; exata) |
| `predefined_type`, `nome`, `descricao`, `object_type` | Atributos do elemento |
| `tipo_nome` | Nome do tipo (IfcTypeObject) |
| `layer` | Casa se **algum** layer do elemento casar |
| `geometria` | Orientação calculada pela forma (ver abaixo) – heurística, só para teste |
| `propriedades` | `{"Pset.Propriedade": comparador}` (inclui Psets do tipo) |
| `ou` | Lista de condições: basta uma casar |

`origem`, `disciplina` e `classe` aceitam texto ou lista. Os demais aceitam um texto
(igualdade) ou um comparador: `{"igual": x}`, `{"em": [..]}`, `{"contem": x}`,
`{"comeca_com": x}`, `{"regex": "..."}`, `{"vazio": true|false}`.
Comparações ignoram maiúsculas e acentos (`FUNDAÇÃO` = `fundacao`).

### `entao`
| Chave | Efeito |
|---|---|
| `classe` | Reclassifica preservando GlobalId, geometria, Psets e relações |
| `predefined_type` | Valor do enum da classe; `USERDEFINED` exige `object_type` |
| `object_type`, `nome`, `descricao` | Texto; aceita `{nome}`, `{tipo_nome}`, `{prop:Pset.Prop}` |
| `propriedades` | `{"Pset": {"Prop": valor}}`; valor literal ou `{"copiar": "Pset.Prop", "unidade_origem": "m"}` |

`unidade_origem` (`m`, `cm`, `mm`) converte comprimento/área/volume para a unidade do arquivo.

### Condição `geometria` (heurística – em teste)
Classifica a peça pela forma, a partir da geometria triangulada em coordenadas globais
(componentes principais dos vértices). Valores: `vertical`, `horizontal`, `inclinado`,
`plano_horizontal`, `plano_vertical`, `plano_inclinado`, `compacto`, `sem_geometria`.

```jsonc
"geometria": {
  "tolerancia_angular_graus": 10,  // até 10° da vertical = vertical; até 10° da horizontal = horizontal
  "razao_alongamento_min": 3.0,    // peça linear: maior dimensão >= 3 x a segunda
  "razao_espessura_max": 0.25      // peça plana: menor dimensão <= 0,25 x a segunda
}
```

**Por ser interpretação, toda regra geométrica deve ficar como `proposta` até ser validada.**
Quando há regra geométrica ativa, o relatório ganha a aba `Revisao_Geometrica`: uma linha por
peça analisada, com orientação, ângulo, dimensões, regra, situação (`aplicada`,
`sobreposta por <regra>`, `nenhuma regra geométrica casou`), classe final e uma coluna
`conferido` para a revisão. Aprovar uma regra geométrica gera aviso na validação.

Precedência (decidida): regra por nome/atributos vence a geométrica, independentemente da ordem
no arquivo. A geométrica perdedora não vira conflito; aparece como "sobreposta" na revisão.

Limitações conhecidas: peça curva ou em L pode ter eixo principal enganoso; contraventamento
inclinado não tem regra (decisão em aberto: `IfcMember` BRACE?); pilar x parede e laje x chapa
continuam em aberto.

### Tipos (`IfcTypeObject`)
Quando uma regra troca a classe de elementos que têm tipo (ex.: porcas do Tekla, `IfcBeam` com
`IfcBeamType`), o tipo precisa acompanhar (`IfcMechanicalFastenerType`). Com
`tipos.reclassificar_com_elemento = true`, depois de processar todos os elementos:
- se **todas** as ocorrências do tipo terminaram na mesma classe, o tipo é reclassificado
  (GlobalId, nome, Psets do tipo e geometria-mapa preservados);
- o `PredefinedType` do tipo recebe o das ocorrências, se todas forem iguais; em `USERDEFINED`,
  `ElementType` recebe o `ObjectType` das ocorrências; senão fica `NOTDEFINED`;
- tipo **compartilhado** com elementos de outra classe não é alterado e cada elemento afetado
  vira pendência `tipo_incompativel_com_classe` com o motivo.

Cada alteração de tipo aparece na aba `Alteracoes`, atribuída à regra que reclassificou as ocorrências.

### Precedência
Regras são avaliadas na ordem do arquivo, sobre o estado **original** do elemento.
Para cada campo vale a primeira regra que casar; outra regra com valor diferente para o
mesmo campo é registrada como `conflito_entre_regras` (pendência), sem ser aplicada.
