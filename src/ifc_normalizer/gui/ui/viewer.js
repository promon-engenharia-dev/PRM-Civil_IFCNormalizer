"use strict";
/* Visualizador 3D (WebGL2, sem dependências externas: funciona offline dentro do executável).
 * A malha vem do Python (ifcopenshell) já em metros, relativa a uma origem comum aos modelos. */

const Visualizador = (() => {
  const LARG_ESTADO = 2048; // largura da textura de estado por elemento
  const ESTADO = { visivel: 0, oculto: 1, selecionado: 2 };

  const VS = `#version 300 es
  precision highp float; precision highp int; precision highp usampler2D;
  in vec3 aPos; in vec3 aNor; in vec4 aCor; in uint aId;
  uniform mat4 uMVP; uniform mat4 uView; uniform uint uBase; uniform usampler2D uEstado; uniform int uRaioX;
  out vec4 vCor; out vec3 vNor; flat out uint vId;
  void main() {
    uint gid = aId + uBase;
    uint st = texelFetch(uEstado, ivec2(int(gid % ${LARG_ESTADO}u), int(gid / ${LARG_ESTADO}u)), 0).r;
    vId = gid;
    if (st == 1u) { gl_Position = vec4(2.0, 2.0, 2.0, 1.0); return; }
    vec4 c = aCor;
    if (st == 2u) c = vec4(0.890, 0.322, 0.020, 1.0);
    else if (uRaioX == 1) c.a = min(c.a, 0.15);
    vCor = c;
    vNor = mat3(uView) * aNor;
    gl_Position = uMVP * vec4(aPos, 1.0);
  }`;

  const FS = `#version 300 es
  precision highp float; precision highp int;
  in vec4 vCor; in vec3 vNor; flat in uint vId;
  uniform int uPasso; // 0 opacos, 1 transparentes, 2 seleção (ids)
  out vec4 saida;
  void main() {
    if (uPasso == 2) {
      uint id = vId + 1u;
      saida = vec4(float(id & 255u), float((id >> 8) & 255u), float((id >> 16) & 255u), 255.0) / 255.0;
      return;
    }
    bool opaco = vCor.a >= 0.99;
    if ((uPasso == 0) != opaco) discard;
    vec3 n = length(vNor) > 0.01 ? normalize(vNor) : vec3(0.0, 0.0, 1.0);
    if (n.z < 0.0) n = -n;
    float luz = 0.42 + 0.50 * max(dot(n, normalize(vec3(0.35, 0.55, 0.75))), 0.0)
                     + 0.18 * max(dot(n, normalize(vec3(-0.6, -0.2, 0.3))), 0.0);
    saida = vec4(vCor.rgb * min(luz, 1.1), vCor.a);
  }`;

  /* ---------- matrizes (coluna principal) ---------- */
  const M = {
    mul(a, b) {
      const o = new Float32Array(16);
      for (let c = 0; c < 4; c++) for (let r = 0; r < 4; r++) {
        let s = 0;
        for (let k = 0; k < 4; k++) s += a[k * 4 + r] * b[c * 4 + k];
        o[c * 4 + r] = s;
      }
      return o;
    },
    perspectiva(fovy, asp, perto, longe) {
      const f = 1 / Math.tan(fovy / 2), nf = 1 / (perto - longe);
      return new Float32Array([f / asp, 0, 0, 0, 0, f, 0, 0, 0, 0, (longe + perto) * nf, -1, 0, 0, 2 * longe * perto * nf, 0]);
    },
    olhar(olho, alvo, cima) {
      const z = norm(sub(olho, alvo)), x = norm(cruz(cima, z)), y = cruz(z, x);
      return new Float32Array([x[0], y[0], z[0], 0, x[1], y[1], z[1], 0, x[2], y[2], z[2], 0,
        -dot(x, olho), -dot(y, olho), -dot(z, olho), 1]);
    },
  };
  function sub(a, b) { return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]; }
  function dot(a, b) { return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]; }
  function cruz(a, b) { return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]; }
  function norm(a) { const l = Math.hypot(a[0], a[1], a[2]) || 1; return [a[0] / l, a[1] / l, a[2] / l]; }

  /* ---------- estado ---------- */
  let gl, canvas, prog, loc = {}, texEstado, fboSel = null, sujo = true;
  const modelos = new Map();        // id -> {id, meta, base, n, vao, buffers, indices, visivel}
  const elems = [];                 // gid -> {modelo, local, classe, nome, guid, caixa:[6]}
  let estadoBytes = new Uint8Array(LARG_ESTADO);
  const classeVisivel = new Map();  // classe -> bool
  const ocultosUsuario = new Set();
  let isolados = null;              // Set de gid ou null
  const selecao = new Set();
  let raioX = false;
  const cam = { alvo: [0, 0, 0], dist: 60, yaw: -2.356, pitch: 0.6, fov: 0.8 };
  let aoSelecionar = () => {};
  let aoMudar = () => {};

  function compilar(tipo, fonte) {
    const s = gl.createShader(tipo);
    gl.shaderSource(s, fonte);
    gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s));
    return s;
  }

  function iniciar(el, callbacks) {
    canvas = el;
    aoSelecionar = callbacks.aoSelecionar || aoSelecionar;
    aoMudar = callbacks.aoMudar || aoMudar;
    gl = canvas.getContext("webgl2", { antialias: true, alpha: true, premultipliedAlpha: false });
    if (!gl) throw new Error("WebGL2 indisponível neste computador.");
    prog = gl.createProgram();
    gl.attachShader(prog, compilar(gl.VERTEX_SHADER, VS));
    gl.attachShader(prog, compilar(gl.FRAGMENT_SHADER, FS));
    ["aPos", "aNor", "aCor", "aId"].forEach((n, i) => gl.bindAttribLocation(prog, i, n));
    gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(prog));
    ["uMVP", "uView", "uBase", "uEstado", "uRaioX", "uPasso"].forEach((n) => { loc[n] = gl.getUniformLocation(prog, n); });
    texEstado = gl.createTexture();
    enviarEstado();
    controles();
    new ResizeObserver(() => { sujo = true; }).observe(canvas);
    requestAnimationFrame(quadro);
  }

  /* ---------- carga ---------- */
  function adicionarModelo(meta, buffer) {
    const [lp, ln, lc, li, lx] = meta.layout;
    const pos = new Float32Array(buffer, lp[0], lp[1]);
    const nor = new Int8Array(buffer, ln[0], ln[1]);
    const cor = new Uint8Array(buffer, lc[0], lc[1]);
    const ids = new Uint32Array(buffer, li[0], li[1]);
    const idx = new Uint32Array(buffer, lx[0], lx[1]);
    const base = elems.length;
    const n = meta.elementos.length;
    for (let i = 0; i < n; i++) {
      const e = meta.elementos[i];
      elems.push({ modelo: meta.id, local: i, classe: e.classe, nome: e.nome, guid: e.guid,
        caixa: [Infinity, Infinity, Infinity, -Infinity, -Infinity, -Infinity] });
      if (!classeVisivel.has(e.classe)) classeVisivel.set(e.classe, e.classe !== "IfcSpace");
    }
    for (let v = 0; v < ids.length; v++) {
      const c = elems[base + ids[v]].caixa;
      const x = pos[v * 3], y = pos[v * 3 + 1], z = pos[v * 3 + 2];
      if (x < c[0]) c[0] = x; if (y < c[1]) c[1] = y; if (z < c[2]) c[2] = z;
      if (x > c[3]) c[3] = x; if (y > c[4]) c[4] = y; if (z > c[5]) c[5] = z;
    }
    const vao = gl.createVertexArray();
    gl.bindVertexArray(vao);
    const buffers = [];
    const atributo = (i, dados, tam, tipo, normalizado, inteiro) => {
      const b = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, b);
      gl.bufferData(gl.ARRAY_BUFFER, dados, gl.STATIC_DRAW);
      gl.enableVertexAttribArray(i);
      if (inteiro) gl.vertexAttribIPointer(i, tam, tipo, 0, 0);
      else gl.vertexAttribPointer(i, tam, tipo, normalizado, 0, 0);
      buffers.push(b);
    };
    atributo(0, pos, 3, gl.FLOAT, false, false);
    atributo(1, nor, 3, gl.BYTE, true, false);
    atributo(2, cor, 4, gl.UNSIGNED_BYTE, true, false);
    atributo(3, ids, 1, gl.UNSIGNED_INT, false, true);
    const ib = gl.createBuffer();
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, ib);
    gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, idx, gl.STATIC_DRAW);
    buffers.push(ib);
    gl.bindVertexArray(null);
    const primeiro = modelos.size === 0;
    modelos.set(meta.id, { id: meta.id, meta, base, n, vao, buffers, indices: idx.length, visivel: true });
    atualizarEstado();
    if (primeiro) { vista("iso", false); enquadrar(); }
  }

  function removerModelo(id) {
    const m = modelos.get(id);
    if (!m) return;
    gl.deleteVertexArray(m.vao);
    m.buffers.forEach((b) => gl.deleteBuffer(b));
    modelos.delete(id);
    for (let g = m.base; g < m.base + m.n; g++) { elems[g] = null; selecao.delete(g); ocultosUsuario.delete(g); }
    if (isolados) for (let g = m.base; g < m.base + m.n; g++) isolados.delete(g);
    // classes que só existiam nesse modelo saem da lista
    const presentes = new Set(elems.filter(Boolean).map((e) => e.classe));
    [...classeVisivel.keys()].forEach((c) => { if (!presentes.has(c)) classeVisivel.delete(c); });
    atualizarEstado();
    aoSelecionar([...selecao]);
  }

  /* ---------- visibilidade e seleção ---------- */
  function visivel(g) {
    const e = elems[g];
    if (!e) return false;
    const m = modelos.get(e.modelo);
    return !!m && m.visivel && classeVisivel.get(e.classe) !== false && !ocultosUsuario.has(g) && (!isolados || isolados.has(g));
  }

  function atualizarEstado() {
    const linhas = Math.max(1, Math.ceil(elems.length / LARG_ESTADO));
    if (estadoBytes.length !== linhas * LARG_ESTADO) estadoBytes = new Uint8Array(linhas * LARG_ESTADO);
    for (let g = 0; g < elems.length; g++) {
      estadoBytes[g] = !visivel(g) ? ESTADO.oculto : selecao.has(g) ? ESTADO.selecionado : ESTADO.visivel;
    }
    enviarEstado();
    sujo = true;
    aoMudar();
  }

  function enviarEstado() {
    gl.bindTexture(gl.TEXTURE_2D, texEstado);
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.R8UI, LARG_ESTADO, estadoBytes.length / LARG_ESTADO, 0, gl.RED_INTEGER, gl.UNSIGNED_BYTE, estadoBytes);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
  }

  function selecionar(gids, somar) {
    if (!somar) selecao.clear();
    gids.forEach((g) => { if (somar && selecao.has(g)) selecao.delete(g); else selecao.add(g); });
    atualizarEstado();
    aoSelecionar([...selecao]);
  }

  /* ---------- câmera ---------- */
  function olho() {
    const cp = Math.cos(cam.pitch);
    return [cam.alvo[0] + cam.dist * cp * Math.cos(cam.yaw), cam.alvo[1] + cam.dist * cp * Math.sin(cam.yaw),
      cam.alvo[2] + cam.dist * Math.sin(cam.pitch)];
  }

  function matrizes() {
    const w = canvas.clientWidth || 1, h = canvas.clientHeight || 1;
    const view = M.olhar(olho(), cam.alvo, [0, 0, 1]);
    const proj = M.perspectiva(cam.fov, w / h, Math.max(cam.dist / 2000, 0.01), cam.dist * 50 + 1000);
    return { view, mvp: M.mul(proj, view) };
  }

  function caixaDe(gids) {
    const c = [Infinity, Infinity, Infinity, -Infinity, -Infinity, -Infinity];
    gids.forEach((g) => {
      const e = elems[g];
      if (!e || e.caixa[0] === Infinity) return;
      for (let k = 0; k < 3; k++) { c[k] = Math.min(c[k], e.caixa[k]); c[k + 3] = Math.max(c[k + 3], e.caixa[k + 3]); }
    });
    return c[0] === Infinity ? null : c;
  }

  function enquadrar(gids) {
    const alvo = gids && gids.length ? gids : elems.map((_, g) => g).filter(visivel);
    const c = caixaDe(alvo);
    if (!c) return;
    cam.alvo = [(c[0] + c[3]) / 2, (c[1] + c[4]) / 2, (c[2] + c[5]) / 2];
    const raio = Math.max(Math.hypot(c[3] - c[0], c[4] - c[1], c[5] - c[2]) / 2, 0.5);
    cam.dist = raio / Math.sin(cam.fov / 2) * 1.05;
    sujo = true;
  }

  function vista(nome, enq = true) {
    const v = { iso: [-2.356, 0.6], topo: [-Math.PI / 2, 1.5707], frente: [-Math.PI / 2, 0], lateral: [0, 0] }[nome];
    if (!v) return;
    [cam.yaw, cam.pitch] = v;
    if (enq) enquadrar();
    sujo = true;
  }

  /* ---------- desenho ---------- */
  function ajustarTamanho() {
    const r = window.devicePixelRatio || 1;
    const w = Math.max(1, Math.round(canvas.clientWidth * r)), h = Math.max(1, Math.round(canvas.clientHeight * r));
    if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; fboSel = null; }
  }

  function desenhar(passos, alvoFbo) {
    ajustarTamanho();
    gl.bindFramebuffer(gl.FRAMEBUFFER, alvoFbo || null);
    gl.viewport(0, 0, canvas.width, canvas.height);
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    gl.enable(gl.DEPTH_TEST);
    gl.disable(gl.CULL_FACE);
    gl.useProgram(prog);
    const { view, mvp } = matrizes();
    gl.uniformMatrix4fv(loc.uMVP, false, mvp);
    gl.uniformMatrix4fv(loc.uView, false, view);
    gl.uniform1i(loc.uRaioX, raioX ? 1 : 0);
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, texEstado);
    gl.uniform1i(loc.uEstado, 0);
    for (const passo of passos) {
      gl.uniform1i(loc.uPasso, passo);
      if (passo === 1) { gl.enable(gl.BLEND); gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA); gl.depthMask(false); }
      else { gl.disable(gl.BLEND); gl.depthMask(true); }
      for (const m of modelos.values()) {
        if (!m.visivel) continue;
        gl.uniform1ui(loc.uBase, m.base);
        gl.bindVertexArray(m.vao);
        gl.drawElements(gl.TRIANGLES, m.indices, gl.UNSIGNED_INT, 0);
      }
    }
    gl.bindVertexArray(null);
    gl.depthMask(true);
    gl.disable(gl.BLEND);
  }

  function quadro() {
    if (sujo) { sujo = false; desenhar([0, 1]); }
    requestAnimationFrame(quadro);
  }

  function elementoEm(x, y) {
    ajustarTamanho();
    if (!fboSel) {
      const fb = gl.createFramebuffer(), cor = gl.createRenderbuffer(), prof = gl.createRenderbuffer();
      gl.bindRenderbuffer(gl.RENDERBUFFER, cor);
      gl.renderbufferStorage(gl.RENDERBUFFER, gl.RGBA8, canvas.width, canvas.height);
      gl.bindRenderbuffer(gl.RENDERBUFFER, prof);
      gl.renderbufferStorage(gl.RENDERBUFFER, gl.DEPTH_COMPONENT24, canvas.width, canvas.height);
      gl.bindFramebuffer(gl.FRAMEBUFFER, fb);
      gl.framebufferRenderbuffer(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.RENDERBUFFER, cor);
      gl.framebufferRenderbuffer(gl.FRAMEBUFFER, gl.DEPTH_ATTACHMENT, gl.RENDERBUFFER, prof);
      fboSel = fb;
    }
    const r = window.devicePixelRatio || 1;
    const px = Math.round(x * r), py = canvas.height - Math.round(y * r) - 1;
    gl.enable(gl.SCISSOR_TEST);
    gl.scissor(px, py, 1, 1);
    desenhar([2], fboSel);
    gl.disable(gl.SCISSOR_TEST);
    const out = new Uint8Array(4);
    gl.readPixels(px, py, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, out);
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    sujo = true;
    const id = out[0] + (out[1] << 8) + (out[2] << 16);
    return id ? id - 1 : null;
  }

  /* ---------- mouse e teclado ---------- */
  function controles() {
    let arrasto = null;
    canvas.tabIndex = 0;
    canvas.addEventListener("contextmenu", (e) => e.preventDefault());
    canvas.addEventListener("pointerdown", (e) => {
      canvas.setPointerCapture(e.pointerId);
      canvas.focus();
      arrasto = { x: e.clientX, y: e.clientY, x0: e.clientX, y0: e.clientY, botao: e.button, mexeu: false };
    });
    canvas.addEventListener("pointermove", (e) => {
      if (!arrasto) return;
      const dx = e.clientX - arrasto.x, dy = e.clientY - arrasto.y;
      arrasto.x = e.clientX; arrasto.y = e.clientY;
      if (Math.abs(e.clientX - arrasto.x0) + Math.abs(e.clientY - arrasto.y0) > 3) arrasto.mexeu = true;
      if (arrasto.botao === 0 && !e.shiftKey) {
        cam.yaw -= dx * 0.008;
        cam.pitch = Math.max(-1.5707, Math.min(1.5707, cam.pitch + dy * 0.008));
      } else {
        const v = M.olhar(olho(), cam.alvo, [0, 0, 1]);
        const dir = [v[0], v[4], v[8]], cima = [v[1], v[5], v[9]];
        const k = cam.dist * Math.tan(cam.fov / 2) * 2 / (canvas.clientHeight || 1);
        for (let i = 0; i < 3; i++) cam.alvo[i] += (-dx * dir[i] + dy * cima[i]) * k;
      }
      sujo = true;
    });
    canvas.addEventListener("pointerup", (e) => {
      if (arrasto && !arrasto.mexeu && arrasto.botao === 0) {
        const b = canvas.getBoundingClientRect();
        const g = elementoEm(e.clientX - b.left, e.clientY - b.top);
        if (g !== null) selecionar([g], e.ctrlKey);
        else if (!e.ctrlKey) selecionar([], false);
      }
      arrasto = null;
    });
    canvas.addEventListener("dblclick", (e) => {
      const b = canvas.getBoundingClientRect();
      const g = elementoEm(e.clientX - b.left, e.clientY - b.top);
      if (g !== null) enquadrar([g]);
    });
    canvas.addEventListener("wheel", (e) => {
      e.preventDefault();
      cam.dist = Math.max(0.05, cam.dist * Math.pow(1.0015, e.deltaY));
      sujo = true;
    }, { passive: false });
    canvas.addEventListener("keydown", (e) => {
      if (e.key === "f" || e.key === "F") enquadrar(selecao.size ? [...selecao] : null);
      if (e.key === "Escape") selecionar([], false);
    });
  }

  /* ---------- API pública ---------- */
  return {
    iniciar, adicionarModelo, removerModelo, enquadrar, vista,
    modelos: () => [...modelos.values()],
    elemento: (g) => elems[g],
    selecao: () => [...selecao],
    classes() {
      const cont = new Map();
      elems.forEach((e) => { if (e) cont.set(e.classe, (cont.get(e.classe) || 0) + 1); });
      return [...cont.entries()].sort((a, b) => a[0].localeCompare(b[0])).map(([classe, n]) => ({ classe, n, visivel: classeVisivel.get(classe) !== false }));
    },
    contagem() {
      let vis = 0, tot = 0, tri = 0;
      elems.forEach((e, g) => { if (e) { tot++; if (visivel(g)) vis++; } });
      modelos.forEach((m) => { if (m.visivel) tri += m.indices / 3; });
      return { visiveis: vis, total: tot, triangulos: tri };
    },
    caixa: (gids) => caixaDe(gids),
    definirClasse(classe, v) { classeVisivel.set(classe, v); atualizarEstado(); },
    mostrarClasses() { [...classeVisivel.keys()].forEach((c) => classeVisivel.set(c, true)); atualizarEstado(); },
    definirModeloVisivel(id, v) { const m = modelos.get(id); if (m) { m.visivel = v; atualizarEstado(); } },
    isolarSelecao() { if (selecao.size) { isolados = new Set(selecao); atualizarEstado(); enquadrar([...selecao]); } },
    ocultarSelecao() { selecao.forEach((g) => ocultosUsuario.add(g)); selecao.clear(); atualizarEstado(); aoSelecionar([]); },
    mostrarTudo() { isolados = null; ocultosUsuario.clear(); atualizarEstado(); },
    definirRaioX(v) { raioX = v; sujo = true; },
    selecionar,
  };
})();
