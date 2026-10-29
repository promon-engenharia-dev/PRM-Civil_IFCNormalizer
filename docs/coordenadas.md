# Verificação e correção de coordenadas

Convenção Promon: o **zero-zero** de cada modelo fica no cruzamento dos eixos de referência do
prédio (padrão **A × 1**, o pilar do canto inferior esquerdo). Todos os modelos do mesmo prédio
(arquitetura, estrutura, civil) precisam colocar esse ponto na mesma coordenada e com a mesma
direção de eixos.

## O que a ferramenta faz
1. **Localiza o ponto de referência** de cada modelo:
   - **grade** (`IfcGrid`): cruzamento dos dois eixos configurados para o prédio;
   - **pilar de canto** (modelos sem grade, como o Tekla exportado com `GridExport:Off`): o pilar
     do canto inferior esquerdo, medido no sistema de eixos da grade da arquitetura do mesmo prédio.
     É interpretação: aparece marcada como "a conferir". O ideal é exportar o Tekla com a grade.
2. **Confere o zero-zero**: o ponto está na origem do modelo (`IfcSite`)?
3. **Compara com a referência** (arquitetura, `AQ`/`A`): ΔX, ΔY, ΔZ, distância em planta e diferença
   de direção dos eixos. Fora da tolerância → **divergente**.
4. **Corrige** (opcional): desloca e gira o modelo inteiro para o alvo, alterando só os
   posicionamentos de topo (`IfcLocalPlacement` absolutos). A forma e as distâncias entre peças
   não mudam, os GlobalIds são preservados e o arquivo de origem não é alterado: a cópia corrigida
   vai para a pasta de saída. Depois de corrigir, a ferramenta mede de novo e mostra o resíduo.

Em modelos **sem grade** não há direção de eixo para medir: a correção confere só a posição e o
resultado diz "rotação não conferida". O giro pode ser informado à mão no quadro.

## Como usar
- **Interface**: opção **Verificar coordenadas** → *Processar*. O quadro mostra, por modelo,
  X, Y, Z, direção, Δ e situação (como a aba *Measure* do Navisworks). Nos divergentes, o alvo
  e o giro já vêm sugeridos (posição e direção da arquitetura, cota do próprio modelo); ajuste se
  precisar, marque **Corrigir** e use **Aplicar correção nos marcados**.
- **Linha de comando**: `ifc-normalizer coordenadas <arquivos> -o out/coordenadas [--corrigir]`.
- Relatório: `coordenadas.xlsx` na pasta de saída (abas Resumo, Coordenadas e Correcoes).

## Configuração (`rules/coordenadas.json`)
| Campo | Uso |
|---|---|
| `tolerancia_posicao_m`, `tolerancia_rotacao_graus` | Limites para "coincide" |
| `eixos_padrao` | Eixos de referência quando o prédio não define outros (A, 1) |
| `disciplinas_referencia` | Ordem de preferência do modelo de referência (AQ, A) |
| `predios` | `id`, `arquivos` (padrão do nome, ex.: `*_0610_*`) e, se preciso, `eixos` (ex.: `["F", "7"]`) |
| `pilares` | Quais elementos contam como pilar no modo "pilar de canto" (mesma linguagem das regras) |

Quando os eixos configurados não existem na grade, o relatório lista os **eixos disponíveis**
para que a configuração seja ajustada; a ferramenta não escolhe outro eixo por conta própria.

## Resultado nas amostras (27/10/2026)
| Prédio | Situação |
|---|---|
| 0610 Vestiário | Arquitetura com A×1 no zero-zero; Tekla coincide (1 mm) |
| ZTO-13-04 Torno | AQ com F×7 no zero-zero; ST4 coincide (origem interna fora do F×7); ST3 a 12,25 m (sem pilar perto do F×7 – conferir); pré-moldado a 2,7 km (exportado pelo ponto base) |
| 0601 Administrativo | Grade da arquitetura só tem eixos F a R: configurar os eixos de referência |
| 0313 Apoio/Escritório | Nenhum modelo com grade; Tekla sem pilares com nome reconhecido |
