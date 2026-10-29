"use strict";

const $ = (id) => document.getElementById(id);
const estado = {
  arquivos: [],          // [{arquivo, caminho, tamanho_mb, schema, origem, situacao}]
  res: { diagnostico: [], normalizar: [], migrar: [], coordenadas: [] },  // resultados por ação
  executadas: [],        // ações da última execução (abas de resultado)
  aba: "normalizar",     // aba de resultado exibida
  acoes: new Set(["normalizar"]),
  pastaSaida: "",
  pastaManual: false,
  processando: false,
  regrasOk: false,
  pastaCoordenadas: "",
};

const NOMES_ACAO = { diagnostico: "Diagnóstico", normalizar: "Normalização", migrar: "Conversão IFC 4.3", coordenadas: "Coordenadas" };
const ORDEM_ACOES = ["diagnostico", "normalizar", "migrar", "coordenadas"];
const resultados = () => estado.res[estado.aba];

const MOTIVOS = {
  classe_generica_sem_regra: "Classe genérica sem regra",
  sem_predefined_type: "Sem PredefinedType",
  conflito_entre_regras: "Conflito entre regras",
  tipo_incompativel_com_classe: "Tipo incompatível",
  valor_de_origem_ausente: "Valor de origem ausente",
  valor_nao_convertido: "Valor não convertido",
  userdefined_sem_object_type: "USERDEFINED sem ObjectType",
};

function api() { return window.pywebview.api; }
function esc(t) { return String(t ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }
function num(n) { return n === null || n === undefined ? "–" : Number(n).toLocaleString("pt-BR"); }
function registrar(msg) {
  const hora = new Date().toLocaleTimeString("pt-BR");
  $("registro").textContent += `[${hora}] ${msg}\n`;
  $("registro").scrollTop = $("registro").scrollHeight;
}

/* ---------- arquivos ---------- */
function seloSituacao(s) {
  const mapa = {
    aguardando: ["neutro", "Aguardando"], processando: ["azul", "Processando"],
    concluido: ["ok", "Concluído"], atencao: ["atencao", "Concluído c/ alerta"], erro: ["erro", "Erro"],
  };
  const [cls, txt] = mapa[s] || mapa.aguardando;
  return `<span class="selo ${cls}">${s === "processando" ? '<span class="girando"></span>' : ""}${txt}</span>`;
}

function seloSchema(schema) {
  return schema === "IFC4X3_ADD2"
    ? `<span class="selo ok">${esc(schema)}</span>`
    : `<span class="selo acento" title="Será convertido para IFC 4.3">${esc(schema)}</span>`;
}

function desenharArquivos() {
  const corpo = $("lista-arquivos");
  corpo.innerHTML = estado.arquivos.map((a, i) => `
    <tr>
      <td class="arquivo" title="${esc(a.caminho)}">${esc(a.arquivo)}</td>
      <td class="centro">${esc(a.origem)}</td>
      <td class="centro">${seloSchema(a.schema)}</td>
      <td class="num">${num(a.tamanho_mb)} MB</td>
      <td class="centro">${seloSituacao(a.situacao)}</td>
      <td class="centro"><button class="remover" data-i="${i}" title="Remover da lista" ${estado.processando ? "disabled" : ""}>×</button></td>
    </tr>`).join("");
  $("lista-vazia").classList.toggle("oculto", estado.arquivos.length > 0);
  corpo.querySelectorAll(".remover").forEach((b) => b.addEventListener("click", () => {
    estado.arquivos.splice(Number(b.dataset.i), 1);
    desenharArquivos();
  }));
  atualizarBotoes();
  if (vis.pronto) atualizarListaVisualizador();
  contarPrediosNaSelecao();
}

async function adicionar(lista) {
  const existentes = new Set(estado.arquivos.map((a) => a.caminho.toLowerCase()));
  let novos = 0;
  for (const a of lista) {
    if (existentes.has(a.caminho.toLowerCase())) continue;
    estado.arquivos.push({ ...a, situacao: "aguardando" });
    novos++;
  }
  estado.arquivos.sort((x, y) => x.arquivo.localeCompare(y.arquivo, "pt-BR"));
  if (novos && !estado.pastaManual && estado.arquivos.length) {
    estado.pastaSaida = await api().pasta_padrao(estado.arquivos[0].caminho);
    $("pasta-saida").value = estado.pastaSaida;
  }
  if (novos) registrar(`${novos} arquivo(s) adicionado(s).`);
  desenharArquivos();
}

/* ---------- estrutura espacial e nomes ---------- */
const CAMPOS_NOME = ["nome", "nome_longo"];

// Mesmo critério do Python (fnmatch, sem diferenciar maiúsculas): * e ? como curingas.
function casaPadrao(arquivo, padrao) {
  const re = padrao.toUpperCase().replace(/[.+^${}()|[\]\\]/g, "\\$&").replace(/\*/g, ".*").replace(/\?/g, ".");
  return new RegExp(`^${re}$`).test(arquivo.toUpperCase());
}

function desenharEstrutura(info) {
  if (!info.ok) {
    $("est-infra").innerHTML = `<strong>Regras de estrutura com problema</strong> (${esc(info.caminho)}):
      <ul>${info.problemas.map((p) => `<li>${esc(p)}</li>`).join("")}</ul>`;
    $("est-infra").classList.add("alerta");
    $("est-predios").innerHTML = "";
    return;
  }
  $("est-infra").classList.remove("alerta");
  $("est-projeto-nome").value = info.projeto.nome || "";
  $("est-projeto-longo").value = info.projeto.nome_longo || "";
  $("est-site-nome").value = info.site.nome || "";
  $("est-site-longo").value = info.site.nome_longo || "";
  $("est-predios").innerHTML = info.predios.map((p) => `
    <tr data-id="${esc(p.id)}" data-padrao="${esc(p.arquivos)}">
      <td><strong>${esc(p.id)}</strong></td>
      <td class="padrao">${esc(p.arquivos)}</td>
      <td class="centro na-selecao">–</td>
      ${CAMPOS_NOME.map((c) => `<td><input class="campo-texto" data-campo="${c}" value="${esc(p[c] || "")}"
        placeholder="manter"></td>`).join("")}
    </tr>`).join("");
  $("est-infra").innerHTML = info.infraestrutura.length
    ? `<strong>Infraestrutura (Civil 3D)</strong><ul>${info.infraestrutura.map((r) =>
        `<li><span class="selo ${r.status === "aprovada" ? "ok" : "atencao"}">${esc(r.status)}</span>
         <strong>${esc(r.id)}</strong> ${esc(r.descricao)}</li>`).join("")}</ul>`
    : "";
  contarPrediosNaSelecao();
}

function contarPrediosNaSelecao() {
  document.querySelectorAll("#est-predios tr").forEach((tr) => {
    const n = estado.arquivos.filter((a) => casaPadrao(a.arquivo, tr.dataset.padrao)).length;
    tr.querySelector(".na-selecao").innerHTML = n ? `<span class="selo azul">${n}</span>` : "–";
  });
}

function coletarEstrutura() {
  const predios = {};
  document.querySelectorAll("#est-predios tr").forEach((tr) => {
    predios[tr.dataset.id] = Object.fromEntries(
      [...tr.querySelectorAll("input")].map((i) => [i.dataset.campo, i.value.trim()]));
  });
  return {
    projeto: { nome: $("est-projeto-nome").value.trim(), nome_longo: $("est-projeto-longo").value.trim() },
    site: { nome: $("est-site-nome").value.trim(), nome_longo: $("est-site-longo").value.trim() },
    predios,
  };
}

/* ---------- regras ---------- */
function desenharRegras(r) {
  estado.regrasOk = r.ok;
  const caixa = $("caixa-regras");
  const selo = $("selo-regras");
  if (!r.ok) {
    selo.className = "selo erro";
    selo.textContent = "Regras com problema";
    caixa.className = "info alerta";
    caixa.innerHTML = `<strong>As regras não puderam ser carregadas.</strong>
      <ul>${r.problemas.slice(0, 8).map((p) => `<li>${esc(p)}</li>`).join("")}</ul>
      <div class="acoes"><button class="link" id="bt-abrir-regras">Abrir regras</button><button class="link" id="bt-recarregar">Recarregar</button></div>`;
  } else {
    selo.className = "selo neutro";
    selo.textContent = `Regras v${r.versao}`;
    caixa.className = "info";
    const avisos = r.avisos.length ? `<ul>${r.avisos.map((a) => `<li>${esc(a)}</li>`).join("")}</ul>` : "";
    caixa.innerHTML = `Versão <strong>${esc(r.versao)}</strong> ·
      <span class="selo ok">${r.aprovadas} aprovada(s)</span>
      <span class="selo atencao">${r.propostas} proposta(s)</span>${avisos}
      <div class="acoes"><button class="link" id="bt-abrir-regras">Abrir regras</button><button class="link" id="bt-recarregar">Recarregar</button></div>`;
  }
  $("bt-abrir-regras").addEventListener("click", () => api().abrir_regras());
  $("bt-recarregar").addEventListener("click", async () => {
    desenharRegras(await api().info_regras());
    registrar("Regras recarregadas.");
  });
  atualizarBotoes();
}

/* ---------- opções ---------- */
function alternarAcao(valor, marcado) {
  if (marcado) {
    estado.acoes.add(valor);
    // Normalizar já inclui a conversão: as duas não rodam juntas.
    if (valor === "normalizar") estado.acoes.delete("migrar");
    if (valor === "migrar") estado.acoes.delete("normalizar");
  } else {
    estado.acoes.delete(valor);
  }
  document.querySelectorAll(".opcao").forEach((o) => {
    const inp = o.querySelector("input");
    inp.checked = estado.acoes.has(inp.value);
    o.classList.toggle("ativa", inp.checked);
  });
  $("grupo-regras").classList.toggle("oculto", !estado.acoes.has("normalizar"));
  $("grupo-validar").classList.toggle("oculto", !estado.acoes.has("normalizar") && !estado.acoes.has("migrar"));
  atualizarBotoes();
}

function atualizarBotoes() {
  const precisaRegras = estado.acoes.has("normalizar");
  $("bt-processar").disabled = estado.processando || !estado.arquivos.length || !estado.pastaSaida
    || !estado.acoes.size || (precisaRegras && !estado.regrasOk);
  ["bt-arquivos", "bt-pasta", "bt-limpar", "bt-saida"].forEach((id) => { $(id).disabled = estado.processando; });
  document.querySelectorAll("#opcoes-acao input, #ck-propostas, #ck-validar").forEach((el) => { el.disabled = estado.processando; });
  $("bt-processar").textContent = estado.processando ? "Processando…" : "Processar";
  const marcados = estado.res.coordenadas.some((r) => r.corrigir);
  $("bt-corrigir").disabled = estado.processando || !marcados;
}

/* ---------- resultados ---------- */
const CABECALHO_COORDENADAS = `<tr><th>Prédio</th><th style="text-align:left">Modelo</th><th>Método</th>
  <th>X (m)</th><th>Y (m)</th><th>Z (m)</th><th>Direção (°)</th><th>ΔX (m)</th><th>ΔY (m)</th><th>ΔZ (m)</th>
  <th>Δ planta (m)</th><th>ΔRot (°)</th><th>Zero-zero</th><th>Situação</th>
  <th>Corrigir</th><th>Alvo X (m)</th><th>Alvo Y (m)</th><th>Alvo Z (m)</th><th>Giro (°)</th></tr>`;

function cabecalhoResultados() {
  if (estado.aba === "coordenadas") { $("cab-resultados").innerHTML = CABECALHO_COORDENADAS; return; }
  $("cab-resultados").innerHTML = estado.aba === "diagnostico"
    ? `<tr><th style="text-align:left">Arquivo</th><th>Origem</th><th>Versão IFC</th><th>Elementos</th><th>Proxies</th><th>Sem PredefinedType</th></tr>`
    : `<tr><th style="text-align:left">Arquivo</th><th>Conversão</th><th>Alterações</th><th>Pendências</th><th>Erros de esquema</th><th>Tempo</th><th>Abrir</th></tr>`;
}

function seloPendencias(r) {
  if (r.pendencias === undefined) return "–";
  if (!r.pendencias) return `<span class="selo ok">0</span>`;
  const detalhe = Object.entries(r.pendencias_por_motivo || {}).map(([k, v]) => `${MOTIVOS[k] || k}: ${v}`).join("\n");
  return `<span class="selo atencao" title="${esc(detalhe)}">${num(r.pendencias)}</span>`;
}

function seloErros(n) {
  if (n === null || n === undefined) return `<span class="selo neutro">não validado</span>`;
  return n ? `<span class="selo erro">${num(n)}</span>` : `<span class="selo ok">0</span>`;
}

function m3(v) {
  return v === null || v === undefined ? "–" : Number(v).toLocaleString("pt-BR", { minimumFractionDigits: 3, maximumFractionDigits: 3 });
}

function seloCoordenada(r) {
  const mapa = { "referência": "azul", "coincide com a referência": "ok", "divergente": "erro" };
  let html = `<span class="selo ${mapa[r.situacao] || "atencao"}">${esc(r.situacao)}</span>`;
  if (r.interpretacao) html += ` <span class="selo acento" title="${esc(r.observacao)}">interpretação a conferir</span>`;
  if (r.correcao) {
    const c = r.correcao;
    if (c.erro) html += ` <span class="selo erro" title="${esc(c.erro)}">erro na correção</span>`;
    else if (!c.ok) html += ` <span class="selo erro">corrigido – conferir (resíduo ${m3(c.residuo_m)} m)</span>`;
    else if (!c.rotacao_conferida) html += ` <span class="selo atencao" title="Modelo sem grade: a rotação não pode ser medida">corrigido – posição ok, rotação não conferida</span>`;
    else html += ` <span class="selo ok">corrigido e conferido</span>`;
  }
  return html;
}

function campoAlvo(r, i, k, valor) {
  const desabilitado = estado.processando || !r.alvo ? "disabled" : "";
  const texto = valor === null || valor === undefined ? "" : Number(valor).toFixed(k === "giro" ? 4 : 3);
  return `<input class="campo-num" type="number" step="${k === "giro" ? "0.0001" : "0.001"}" data-i="${i}" data-k="${k}" value="${texto}" ${desabilitado}>`;
}

function desenharCoordenadas(corpo) {
  corpo.innerHTML = estado.res.coordenadas.map((r, i) => {
    const alvo = r.alvoEditado || {};
    const podeCorrigir = !!r.alvo && !estado.processando;
    return `<tr title="${esc(r.observacao || "")}">
      <td class="centro">${esc(r.predio || "–")}</td>
      <td class="arquivo" title="${esc(r.caminho)}">${botaoVer(r.caminho)} ${esc(r.arquivo)}</td>
      <td class="centro">${esc(r.metodo)}${r.metodo === "grade" ? `<br><small>${esc(r.eixos)}</small>` : ""}</td>
      <td class="num">${m3(r.x)}</td><td class="num">${m3(r.y)}</td><td class="num">${m3(r.z)}</td>
      <td class="num">${m3(r.rotacao)}</td>
      <td class="num">${m3(r.dx)}</td><td class="num">${m3(r.dy)}</td><td class="num">${m3(r.dz)}</td>
      <td class="num">${m3(r.distancia)}</td><td class="num">${m3(r.drot)}</td>
      <td class="centro">${esc(r.zero_zero || "–")}</td>
      <td>${seloCoordenada(r)}</td>
      <td class="centro"><input type="checkbox" class="ck-corrigir" data-i="${i}" ${r.corrigir ? "checked" : ""} ${podeCorrigir ? "" : "disabled"}></td>
      <td>${campoAlvo(r, i, "x", alvo.x)}</td><td>${campoAlvo(r, i, "y", alvo.y)}</td>
      <td>${campoAlvo(r, i, "z", alvo.z)}</td><td>${campoAlvo(r, i, "giro", alvo.giro)}</td></tr>`;
  }).join("");
  corpo.querySelectorAll(".ck-corrigir").forEach((ck) => ck.addEventListener("change", () => {
    estado.res.coordenadas[Number(ck.dataset.i)].corrigir = ck.checked;
    atualizarBotoes();
  }));
  corpo.querySelectorAll(".campo-num").forEach((inp) => inp.addEventListener("change", () => {
    const r = estado.res.coordenadas[Number(inp.dataset.i)];
    r.alvoEditado[inp.dataset.k] = inp.value === "" ? null : Number(inp.value);
  }));
}

function botaoVer(caminho) {
  return caminho ? `<button class="botao mini" data-ver="${esc(caminho)}" title="Abrir no Visualizador 3D">3D</button>` : "";
}

function desenharAbas() {
  const caixa = $("abas-resultado");
  caixa.classList.toggle("oculto", estado.executadas.length < 2);
  caixa.innerHTML = estado.executadas.map((a) => {
    const n = estado.res[a].length;
    return `<button class="aba ${a === estado.aba ? "ativa" : ""}" data-aba="${a}">${NOMES_ACAO[a]}${n ? `<span class="selo neutro">${n}</span>` : ""}</button>`;
  }).join("");
  caixa.querySelectorAll("[data-aba]").forEach((b) => b.addEventListener("click", () => {
    estado.aba = b.dataset.aba;
    cabecalhoResultados();
    desenharResultados();
  }));
}

function desenharResultados() {
  const corpo = $("lista-resultados");
  desenharAbas();
  $("ajuda-coordenadas").classList.toggle("oculto", estado.aba !== "coordenadas" || !estado.res.coordenadas.length);
  if (estado.aba === "coordenadas") {
    desenharCoordenadas(corpo);
  } else if (estado.aba === "diagnostico") {
    corpo.innerHTML = resultados().map((p) => `
      <tr><td class="arquivo">${esc(p.arquivo)}</td><td class="centro">${esc(p.origem)}</td>
      <td class="centro">${seloSchema(p.schema)}</td><td class="num">${num(p.total_elementos)}</td>
      <td class="num">${num(p.proxies)}</td><td class="num">${num(p.sem_predefined)}</td></tr>`).join("");
  } else {
    corpo.innerHTML = resultados().map((r) => {
      if (r.status === "erro") {
        return `<tr><td class="arquivo">${esc(r.arquivo)}</td><td colspan="5"><span class="selo erro">Erro</span> ${esc(r.mensagem)}</td><td></td></tr>`;
      }
      const alteracoes = r.alteracoes !== undefined
        ? `${num(r.alteracoes)}<br><small style="color:var(--pe-cinza-02)">${num(r.elementos_alterados)} elem.</small>`
        : Object.values(r.eventos || {}).reduce((a, b) => a + b, 0).toLocaleString("pt-BR") + "<br><small style=\"color:var(--pe-cinza-02)\">eventos</small>";
      return `<tr>
        <td class="arquivo" title="${esc(r.caminho)}">${esc(r.arquivo)}</td>
        <td class="centro"><small>${esc(r.etapas)}</small></td>
        <td class="num">${alteracoes}</td>
        <td class="centro">${seloPendencias(r)}</td>
        <td class="centro">${seloErros(r.erros_schema)}</td>
        <td class="num">${num(r.tempo_s)} s</td>
        <td class="centro">
          <button class="botao mini" data-abrir="${esc(r.saidas.relatorio)}">Relatório</button>
          ${r.saidas.migracao ? `<button class="botao mini" data-abrir="${esc(r.saidas.migracao)}">Conversão</button>` : ""}
          ${botaoVer(r.saidas.ifc)}
        </td></tr>`;
    }).join("");
  }
  corpo.querySelectorAll("[data-abrir]").forEach((b) => b.addEventListener("click", () => api().abrir(b.dataset.abrir)));
  corpo.querySelectorAll("[data-ver]").forEach((b) => b.addEventListener("click", () => verNoVisualizador(b.dataset.ver)));
  $("resultados-vazio").classList.toggle("oculto", resultados().length > 0);
  atualizarKpis();
  atualizarBotoes();
  atualizarListaVisualizador();
}

const ROTULOS_KPI = {
  coordenadas: [["Modelos", "verificados"], ["Divergentes", "posição ou rotação"],
    ["Sem referência", "ponto não localizado"], ["Corrigidos", "nesta sessão"]],
  padrao: [["Arquivos", "processados"], ["Alterações", "em elementos"],
    ["Pendências", "para avaliar no de-para"], ["Erros de esquema", "no arquivo final"]],
};

function rotularKpis() {
  const rot = ROTULOS_KPI[estado.aba === "coordenadas" ? "coordenadas" : "padrao"];
  document.querySelectorAll(".kpi").forEach((k, i) => {
    k.querySelector(".rotulo").textContent = rot[i][0];
    k.querySelector("small").textContent = rot[i][1];
  });
}

function atualizarKpis() {
  rotularKpis();
  if (estado.aba === "coordenadas") {
    const rs = estado.res.coordenadas;
    $("k-arquivos").textContent = rs.length ? num(rs.length) : "–";
    $("k-alteracoes").textContent = rs.length ? num(rs.filter((r) => r.situacao === "divergente").length) : "–";
    $("k-pendencias").textContent = rs.length ? num(rs.filter((r) => r.x === null).length) : "–";
    $("k-erros").textContent = rs.length ? num(rs.filter((r) => r.correcao && r.correcao.ok).length) : "–";
    return;
  }
  const ok = resultados().filter((r) => r.status !== "erro");
  const erros = resultados().length - ok.length;
  $("k-arquivos").textContent = resultados().length ? num(ok.length) : "–";
  $("k-arquivos-sub").textContent = erros ? `processados · ${erros} com erro` : "processados";
  if (estado.aba === "diagnostico") {
    $("k-alteracoes").textContent = "–";
    $("k-pendencias").textContent = ok.length ? num(ok.reduce((s, p) => s + (p.proxies || 0), 0)) : "–";
    $("k-erros").textContent = "–";
    return;
  }
  const soma = (f) => ok.reduce((s, r) => s + (r[f] || 0), 0);
  $("k-alteracoes").textContent = ok.length ? num(soma("alteracoes")) : "–";
  $("k-alteracoes-sub").textContent = ok.length && estado.aba === "normalizar" ? `em ${num(soma("elementos_alterados"))} elementos` : "em elementos";
  $("k-pendencias").textContent = ok.length && estado.aba === "normalizar" ? num(soma("pendencias")) : "–";
  const validados = ok.filter((r) => r.erros_schema !== null && r.erros_schema !== undefined);
  $("k-erros").textContent = validados.length ? num(validados.reduce((s, r) => s + r.erros_schema, 0)) : "–";
}

/* ---------- processamento ---------- */
let relogio = null;
let inicioArquivo = 0;
let arquivoAtual = "";

function textoProgresso() {
  const seg = Math.round((Date.now() - inicioArquivo) / 1000);
  $("progresso-texto").innerHTML = `<span class="girando"></span>${esc(arquivoAtual)} · ${seg} s`;
}

async function processar() {
  const resp = await api().processar(
    estado.arquivos.map((a) => a.caminho), estado.pastaSaida, [...estado.acoes],
    $("ck-propostas").checked, $("ck-validar").checked, coletarEstrutura(),
  );
  if (!resp.ok) { registrar(resp.mensagem); return; }
  estado.processando = true;
  ORDEM_ACOES.forEach((a) => { estado.res[a] = []; });
  estado.executadas = resp.acoes;
  estado.aba = resp.acoes[0];
  estado.arquivos.forEach((a) => { a.situacao = "aguardando"; });
  cabecalhoResultados();
  desenharArquivos();
  desenharResultados();
  $("progresso").classList.remove("oculto");
  $("barra").style.width = "0";
}

// Pior situação de cada arquivo ao longo das etapas (erro > alerta > concluído).
const PESO_SITUACAO = { aguardando: 0, concluido: 1, atencao: 2, erro: 3 };
function marcarArquivo(indice, situacao) {
  const a = estado.arquivos[indice];
  if (!a) return;
  if (situacao === "processando") { a.situacao = "processando"; return; }
  if ((PESO_SITUACAO[situacao] || 0) >= (PESO_SITUACAO[a.situacaoFinal] || 0)) a.situacaoFinal = situacao;
  a.situacao = a.situacaoFinal;
}

window.aoProgresso = (ev) => {
  switch (ev.tipo) {
    case "inicio_lote":
      estado.arquivos.forEach((a) => { a.situacaoFinal = "aguardando"; });
      registrar(`Início: ${ev.total} arquivo(s) → ${ev.pasta}. Ações: ${ev.acoes.map((a) => NOMES_ACAO[a]).join(" → ")}.`);
      if (ev.acoes.includes("normalizar")) registrar($("ck-propostas").checked ? "Regras: aprovadas + propostas (simulação)." : "Regras: somente aprovadas.");
      break;
    case "inicio_etapa":
      estado.aba = ev.acao;
      registrar(`Etapa: ${NOMES_ACAO[ev.acao]}.`);
      cabecalhoResultados();
      desenharResultados();
      break;
    case "inicio_arquivo":
      // diagnóstico e coordenadas não avisam o fim de cada arquivo: o anterior termina quando o próximo começa
      estado.arquivos.forEach((a, i) => { if (a.situacao === "processando") marcarArquivo(i, "concluido"); });
      marcarArquivo(ev.indice, "processando");
      arquivoAtual = `${NOMES_ACAO[ev.acao]} · ${ev.passo + 1} de ${ev.total_passos} · ${ev.arquivo}`;
      inicioArquivo = Date.now();
      clearInterval(relogio);
      relogio = setInterval(textoProgresso, 1000);
      textoProgresso();
      $("barra").style.width = `${(ev.passo / ev.total_passos) * 100}%`;
      registrar(`${NOMES_ACAO[ev.acao]}: ${ev.arquivo}…`);
      desenharArquivos();
      break;
    case "fim_arquivo": {
      const r = ev.resumo;
      const alerta = r.status === "erro" ? "erro" : (r.erros_schema || (r.verificacoes_divergentes || []).length) ? "atencao" : "concluido";
      marcarArquivo(ev.indice, alerta);
      estado.res[ev.acao].push(r);
      registrar(r.status === "erro" ? `ERRO em ${r.arquivo}: ${r.mensagem}` : `${r.arquivo}: concluído em ${r.tempo_s} s.`);
      desenharArquivos();
      desenharResultados();
      break;
    }
    case "fim_etapa":
      if (ev.acao === "coordenadas") {
        estado.pastaCoordenadas = ev.pasta;
        estado.res.coordenadas = (ev.coordenadas || []).map((r) => ({
          ...r,
          corrigir: r.situacao === "divergente" && !!r.alvo,
          alvoEditado: r.alvo ? { x: r.alvo.x, y: r.alvo.y, z: r.alvo.z, giro: r.giro_sugerido } : {},
        }));
        const porArquivo = new Map(estado.res.coordenadas.map((r) => [r.arquivo, r]));
        estado.arquivos.forEach((a, i) => {
          const r = porArquivo.get(a.arquivo);
          marcarArquivo(i, !r ? "erro" : (r.situacao === "divergente" || r.x === null) ? "atencao" : "concluido");
        });
        registrar(`Coordenadas: relatório em ${ev.relatorio}`);
      }
      if (ev.acao === "diagnostico") {
        const falhas = new Set((ev.falhas || []).map((f) => f.arquivo));
        estado.arquivos.forEach((a, i) => marcarArquivo(i, falhas.has(a.arquivo) ? "erro" : "concluido"));
        estado.res.diagnostico = ev.perfis || [];
        (ev.falhas || []).forEach((f) => registrar(`ERRO em ${f.arquivo}: ${f.mensagem}`));
        if (ev.relatorio) {
          registrar(`Diagnóstico: ${ev.relatorio}`);
          api().abrir(ev.relatorio);
        }
      }
      desenharArquivos();
      desenharResultados();
      break;
    case "fim_lote":
      clearInterval(relogio);
      // A aba de coordenadas é a que pede ação (correção); senão fica a da normalização.
      if (estado.executadas.includes("coordenadas")) estado.aba = "coordenadas";
      else if (estado.executadas.includes("normalizar")) estado.aba = "normalizar";
      cabecalhoResultados();
      estado.processando = false;
      $("barra").style.width = "100%";
      $("progresso-texto").textContent = "Concluído.";
      $("bt-abrir-saida").classList.remove("oculto");
      registrar("Fim do processamento.");
      desenharArquivos();
      desenharResultados();
      break;
    case "inicio_correcao":
      estado.processando = true;
      arquivoAtual = `Corrigindo ${ev.arquivo}`;
      inicioArquivo = Date.now();
      clearInterval(relogio);
      relogio = setInterval(textoProgresso, 1000);
      textoProgresso();
      registrar(`Corrigindo ${ev.arquivo}…`);
      desenharResultados();
      break;
    case "fim_correcao": {
      const r = estado.res.coordenadas.find((x) => x.caminho === ev.caminho);
      if (r) { r.correcao = ev.resultado; r.corrigir = false; }
      const c = ev.resultado;
      registrar(c.erro ? `ERRO ao corrigir ${c.arquivo}: ${c.erro}`
        : `${c.arquivo}: deslocado (${m3(c.deslocamento_x)}; ${m3(c.deslocamento_y)}; ${m3(c.deslocamento_z)}) m, giro ${m3(c.giro_graus)}°, resíduo ${m3(c.residuo_m)} m → ${c.saida}`);
      desenharResultados();
      break;
    }
    case "fim_correcoes":
      clearInterval(relogio);
      estado.processando = false;
      $("progresso-texto").textContent = "Correção concluída. Cópias corrigidas na pasta de saída.";
      registrar(`Relatório de coordenadas atualizado: ${ev.relatorio}`);
      desenharResultados();
      break;
    case "erro_lote":
      clearInterval(relogio);
      estado.processando = false;
      $("progresso-texto").textContent = `Erro: ${ev.mensagem}`;
      registrar(`ERRO: ${ev.mensagem}`);
      desenharArquivos();
      break;
  }
};

/* ---------- páginas ---------- */
function mostrarPagina(id) {
  document.querySelectorAll(".pagina").forEach((p) => p.classList.toggle("oculto", p.id !== id));
  document.querySelectorAll(".abas-topo .aba").forEach((b) => b.classList.toggle("ativa", b.dataset.pagina === id));
  document.querySelector(".rodape").classList.toggle("oculto", id === "pagina-visualizador");
}

/* ---------- visualizador 3D ---------- */
const vis = { pronto: false, carregando: false, fila: [], selecionados: [] };

function nomeArquivo(caminho) { return caminho.split(/[\\/]/).pop(); }

function atualizarListaVisualizador() {
  const opcoes = [];
  const vistos = new Set();
  const add = (caminho, rotulo) => {
    if (!caminho || vistos.has(caminho.toLowerCase())) return;
    vistos.add(caminho.toLowerCase());
    opcoes.push(`<option value="${esc(caminho)}">${esc(rotulo)}</option>`);
  };
  ["normalizar", "migrar"].forEach((a) => estado.res[a].forEach((r) => {
    if (r.saidas && r.saidas.ifc) add(r.saidas.ifc, `${r.arquivo} (${a === "normalizar" ? "normalizado" : "convertido"})`);
  }));
  estado.res.coordenadas.forEach((r) => { if (r.correcao && r.correcao.saida) add(r.correcao.saida, `${r.arquivo} (coordenadas corrigidas)`); });
  estado.arquivos.forEach((a) => add(a.caminho, `${a.arquivo} (origem)`));
  const sel = $("vis-lista");
  const atual = sel.value;
  sel.innerHTML = opcoes.length ? opcoes.join("") : `<option value="">Adicione arquivos na aba Normalização</option>`;
  if (atual && vistos.has(atual.toLowerCase())) sel.value = atual;
  $("vis-bt-carregar").disabled = !opcoes.length || vis.carregando;
}

function verNoVisualizador(caminho) {
  mostrarPagina("pagina-visualizador");
  carregarModelo(caminho);
}

async function carregarModelo(caminho) {
  if (!vis.pronto || !caminho) return;
  if (Visualizador.modelos().some((m) => m.meta.caminho.toLowerCase() === caminho.toLowerCase())) {
    registrarVis(`${nomeArquivo(caminho)} já está carregado.`);
    return;
  }
  if (vis.carregando) { vis.fila.push(caminho); return; }
  const resp = await api().visualizador_carregar(caminho, $("vis-armaduras").checked);
  if (!resp.ok) { registrarVis(resp.mensagem); return; }
  vis.carregando = true;
  atualizarListaVisualizador();
}

function registrarVis(msg, progresso) {
  $("vis-progresso").classList.remove("oculto");
  $("vis-progresso-texto").innerHTML = msg;
  if (progresso !== undefined) $("vis-barra").style.width = `${progresso}%`;
}

async function baixarGeometria(meta) {
  const bytes = new Uint8Array(meta.bytes);
  let pos = 0;
  for (let i = 0; i < meta.partes; i++) {
    registrarVis(`<span class="girando"></span>${esc(meta.arquivo)} · enviando malha ${i + 1} de ${meta.partes}`, 80 + (20 * i) / meta.partes);
    const b64 = await api().visualizador_parte(meta.id, i);
    const parte = new Uint8Array(await (await fetch(`data:application/octet-stream;base64,${b64}`)).arrayBuffer());
    bytes.set(parte, pos);
    pos += parte.length;
  }
  return bytes.buffer;
}

window.aoVisualizador = async (ev) => {
  switch (ev.tipo) {
    case "inicio":
      registrarVis(`<span class="girando"></span>${esc(ev.arquivo)} · lendo o arquivo…`, 2);
      break;
    case "progresso":
      registrarVis(`<span class="girando"></span>${esc(ev.arquivo)} · gerando malha ${num(ev.feitos)} de ${num(ev.total)} elementos`,
        Math.min(80, (80 * ev.feitos) / Math.max(ev.total, 1)));
      break;
    case "pronto": {
      const meta = ev.meta;
      try {
        const buffer = await baixarGeometria(meta);
        Visualizador.adicionarModelo(meta, buffer);
        const obs = [];
        if (meta.armaduras_omitidas) obs.push(`${num(meta.armaduras_omitidas)} armaduras não carregadas`);
        if (meta.sem_geometria) obs.push(`${num(meta.sem_geometria)} sem geometria desenhável`);
        registrarVis(`${esc(meta.arquivo)}: ${num(meta.elementos.length)} elementos, ${num(meta.triangulos)} triângulos.${obs.length ? " " + obs.join("; ") + "." : ""}`, 100);
      } catch (e) {
        registrarVis(`Erro ao montar ${esc(meta.arquivo)}: ${esc(e.message)}`);
        api().visualizador_remover(meta.id);
      }
      fimCarga();
      break;
    }
    case "erro":
      registrarVis(`<span class="selo erro">Erro</span> ${esc(ev.arquivo)}: ${esc(ev.mensagem)}`);
      fimCarga();
      break;
  }
};

function fimCarga() {
  vis.carregando = false;
  desenharModelosVis();
  desenharClassesVis();
  atualizarListaVisualizador();
  if (vis.fila.length) carregarModelo(vis.fila.shift());
}

function desenharModelosVis() {
  const ms = Visualizador.modelos();
  $("vis-vazio").classList.toggle("oculto", ms.length > 0);
  $("vis-modelos").innerHTML = ms.map((m) => `
    <li><div class="linha">
      <input type="checkbox" data-modelo="${m.id}" ${m.visivel ? "checked" : ""} title="Mostrar/ocultar modelo">
      <div><div class="nome">${esc(m.meta.arquivo)}</div>
        <small>${esc(m.meta.schema)} · ${num(m.meta.elementos.length)} elementos · ${num(m.meta.triangulos)} triângulos</small></div>
      <button class="remover" data-remover="${m.id}" title="Retirar do visualizador">×</button></div></li>`).join("");
  $("vis-modelos").querySelectorAll("[data-modelo]").forEach((ck) => ck.addEventListener("change", () => {
    Visualizador.definirModeloVisivel(Number(ck.dataset.modelo), ck.checked);
  }));
  $("vis-modelos").querySelectorAll("[data-remover]").forEach((b) => b.addEventListener("click", () => {
    const id = Number(b.dataset.remover);
    Visualizador.removerModelo(id);
    api().visualizador_remover(id);
    desenharModelosVis();
    desenharClassesVis();
  }));
}

function desenharClassesVis() {
  $("vis-classes").innerHTML = Visualizador.classes().map((c) => `
    <li><label><input type="checkbox" data-classe="${esc(c.classe)}" ${c.visivel ? "checked" : ""}>${esc(c.classe)}</label>
      <span class="qtd">${num(c.n)}</span></li>`).join("");
  $("vis-classes").querySelectorAll("[data-classe]").forEach((ck) => ck.addEventListener("change", () => {
    Visualizador.definirClasse(ck.dataset.classe, ck.checked);
  }));
}

function atualizarContagemVis() {
  const c = Visualizador.contagem();
  $("vis-contagem").textContent = c.total ? `${num(c.visiveis)} de ${num(c.total)} elementos visíveis · ${num(c.triangulos)} triângulos` : "";
}

function tabelaProps(itens) {
  return `<table><tbody>${itens.map(([k, v]) => `<tr><td>${esc(k)}</td><td>${esc(v === "" ? "–" : v)}</td></tr>`).join("")}</tbody></table>`;
}

function grupoProps(nome, itens, aberto, sub) {
  return `<details ${aberto ? "open" : ""}><summary>${esc(nome)}${sub ? ` <small>${esc(sub)}</small>` : ""}</summary>${tabelaProps(itens)}</details>`;
}

function coordenadasReais(gids) {
  const c = Visualizador.caixa(gids);
  const m = Visualizador.modelos()[0];
  if (!c || !m) return [];
  const o = m.meta.origem;
  const f = (v) => m3(v);
  return [
    ["Centro X", f(o[0] + (c[0] + c[3]) / 2)], ["Centro Y", f(o[1] + (c[1] + c[4]) / 2)], ["Centro Z", f(o[2] + (c[2] + c[5]) / 2)],
    ["Mín. (X; Y; Z)", `${f(o[0] + c[0])}; ${f(o[1] + c[1])}; ${f(o[2] + c[2])}`],
    ["Máx. (X; Y; Z)", `${f(o[0] + c[3])}; ${f(o[1] + c[4])}; ${f(o[2] + c[5])}`],
    ["Dimensões (m)", `${f(c[3] - c[0])} × ${f(c[4] - c[1])} × ${f(c[5] - c[2])}`],
  ];
}

async function mostrarPropriedades(gids) {
  vis.selecionados = gids;
  $("vis-isolar").disabled = !gids.length;
  $("vis-ocultar").disabled = !gids.length;
  const caixa = $("vis-propriedades");
  if (!gids.length) { caixa.innerHTML = `<div class="vazio">Clique em um elemento para ver as propriedades.</div>`; return; }
  const coords = grupoProps("Coordenadas do arquivo (m)", coordenadasReais(gids), true, "caixa envolvente");
  if (gids.length > 1) {
    const porClasse = new Map();
    gids.forEach((g) => { const e = Visualizador.elemento(g); porClasse.set(e.classe, (porClasse.get(e.classe) || 0) + 1); });
    caixa.innerHTML = `<div class="titulo">${num(gids.length)} elementos selecionados</div>
      ${grupoProps("Por classe", [...porClasse.entries()].map(([k, v]) => [k, num(v)]), true)}${coords}`;
    return;
  }
  const e = Visualizador.elemento(gids[0]);
  const modelo = Visualizador.modelos().find((m) => m.id === e.modelo);
  caixa.innerHTML = `<div class="titulo">${esc(e.nome || e.classe)}</div><div class="vazio">Lendo propriedades…</div>`;
  const p = await api().visualizador_propriedades(e.modelo, e.local);
  if (vis.selecionados !== gids) return; // seleção mudou enquanto lia
  if (!p.ok) { caixa.innerHTML = `<span class="selo erro">Erro</span> ${esc(p.mensagem)}`; return; }
  caixa.innerHTML = `<div class="titulo">${esc(p.titulo)}</div>
    ${grupoProps("Atributos", [...p.atributos, ["Arquivo", modelo ? modelo.meta.arquivo : ""]], true)}
    ${p.grupos.map((g) => grupoProps(g.nome, g.itens, g.nome === "Tipo" || g.nome === "Localização" || g.nome.startsWith("PRM"), g.tipo === "Quantidades" ? "quantidades" : "")).join("")}
    ${coords}`;
}

function iniciarVisualizador() {
  try {
    Visualizador.iniciar($("vis-canvas"), { aoSelecionar: mostrarPropriedades, aoMudar: atualizarContagemVis });
    vis.pronto = true;
  } catch (e) {
    $("vis-vazio").innerHTML = `<span class="selo erro">Visualizador indisponível</span>&nbsp;${esc(e.message)}`;
  }
  $("vis-bt-carregar").addEventListener("click", () => carregarModelo($("vis-lista").value));
  $("vis-bt-abrir").addEventListener("click", async () => {
    const caminhos = await api().escolher_ifc_visualizar();
    caminhos.forEach((c) => carregarModelo(c));
  });
  $("vis-enquadrar").addEventListener("click", () => Visualizador.enquadrar());
  document.querySelectorAll("[data-vista]").forEach((b) => b.addEventListener("click", () => Visualizador.vista(b.dataset.vista)));
  $("vis-isolar").addEventListener("click", () => Visualizador.isolarSelecao());
  $("vis-ocultar").addEventListener("click", () => Visualizador.ocultarSelecao());
  $("vis-mostrar").addEventListener("click", () => Visualizador.mostrarTudo());
  $("vis-raiox").addEventListener("change", () => Visualizador.definirRaioX($("vis-raiox").checked));
  $("vis-classes-todas").addEventListener("click", () => { Visualizador.mostrarClasses(); desenharClassesVis(); });
  atualizarListaVisualizador();
}

/* ---------- tema (sistema / claro / escuro) ---------- */
const TEMAS = [["", "sistema"], ["light", "claro"], ["dark", "escuro"]];

function aplicarTema(valor) {
  if (valor) document.documentElement.dataset.theme = valor;
  else delete document.documentElement.dataset.theme;
  try { valor ? localStorage.setItem("tema", valor) : localStorage.removeItem("tema"); } catch (e) {}
  $("bt-tema").textContent = `Tema: ${TEMAS.find((t) => t[0] === valor)[1]}`;
}

function iniciarTema() {
  aplicarTema(document.documentElement.dataset.theme || "");
  $("bt-tema").addEventListener("click", () => {
    const atual = TEMAS.findIndex((t) => t[0] === (document.documentElement.dataset.theme || ""));
    aplicarTema(TEMAS[(atual + 1) % TEMAS.length][0]);
  });
}
iniciarTema();

/* ---------- inicialização ---------- */
window.addEventListener("pywebviewready", async () => {
  const ini = await api().estado_inicial();
  $("rodape-versao").textContent = `Normalizador IFC ${ini.versao}`;
  desenharRegras(ini.regras);
  desenharEstrutura(await api().estrutura());
  $("bt-estrutura-recarregar").addEventListener("click", async () => {
    desenharEstrutura(await api().estrutura());
    registrar("Estrutura espacial: valores recarregados do arquivo de regras.");
  });
  $("bt-estrutura-salvar").addEventListener("click", async () => {
    const info = await api().salvar_estrutura(coletarEstrutura());
    desenharEstrutura(info);
    registrar(info.ok ? `Nomes salvos em ${info.caminho}.` : "Não foi possível salvar os nomes.");
  });
  registrar(`Regras em ${ini.pasta_regras}`);

  $("bt-arquivos").addEventListener("click", async () => adicionar(await api().escolher_arquivos()));
  $("bt-pasta").addEventListener("click", async () => adicionar(await api().escolher_pasta_arquivos()));
  $("bt-limpar").addEventListener("click", () => { estado.arquivos = []; desenharArquivos(); });
  $("bt-saida").addEventListener("click", async () => {
    const p = await api().escolher_pasta_saida();
    if (p) { estado.pastaSaida = p; estado.pastaManual = true; $("pasta-saida").value = p; atualizarBotoes(); }
  });
  $("bt-abrir-saida").addEventListener("click", () => api().abrir(estado.pastaSaida));
  $("bt-processar").addEventListener("click", processar);
  $("bt-corrigir").addEventListener("click", async () => {
    const itens = estado.res.coordenadas.filter((r) => r.corrigir && r.alvo).map((r) => ({
      caminho: r.caminho,
      alvo: { x: r.alvoEditado.x, y: r.alvoEditado.y, z: r.alvoEditado.z },
      giro: r.alvoEditado.giro || 0,
    }));
    if (itens.some((it) => [it.alvo.x, it.alvo.y, it.alvo.z].some((v) => v === null || Number.isNaN(v)))) {
      registrar("Preencha X, Y e Z do alvo em todos os modelos marcados.");
      return;
    }
    const resp = await api().corrigir_coordenadas(itens);
    if (!resp.ok) registrar(resp.mensagem);
  });
  document.querySelectorAll("#opcoes-acao input").forEach((el) => el.addEventListener("change", () => alternarAcao(el.value, el.checked)));
  document.querySelectorAll(".abas-topo .aba").forEach((b) => b.addEventListener("click", () => mostrarPagina(b.dataset.pagina)));
  iniciarVisualizador();

  cabecalhoResultados();
  desenharArquivos();
  desenharResultados();
});
