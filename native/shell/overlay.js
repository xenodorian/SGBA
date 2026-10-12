// Draws the HTML a game shows over its canvas (menus, loading bars) as one centred panel, and makes every button reachable with
// the pad. A port of sgba_js_ui.py: the page's DOM snapshot (from the shim's __sgba_ui) is laid out into a panel canvas, which the
// runtime composites over the game. No pointer is involved: A presses the focused button, B goes back.
(function (g) {
  'use strict';
  const PANEL_MARGIN = 28, PANEL_PAD = 14, VIEW_W = 640, VIEW_H = 480;
  const BG = '#1f1f28', LINE = '#33333f', TEXT = '#e6e6ec', DIM = '#9a9aa8', GOLD = '#f0b44c', DARK = '#1a1206', KEY = '#2b2b36';
  const INLINE = new Set(['span', 'b', 'strong', 'i', 'em', 'small', 'a', 'label', 'code', 'u', 's', 'sup', 'sub', 'abbr', 'font']);
  const HEADINGS = { h1: 30, h2: 24, h3: 22, h4: 20 };
  const ROW_CLASS = /(^|\s)(row|shop-row|gem-row|shop-balance|bal|actions)(\s|$)/;
  const hasClass = (node, re) => re.test(node.c || '');
  const textOf = (node) => ('x' in node ? node.x : (node.k || []).map(textOf).join(''));

  const meas = document.createElement('canvas'); meas.width = 8; meas.height = 8;
  const mctx = meas.getContext('2d');
  const setFont = (ctx, size, bold) => { ctx.font = (bold ? 'bold ' : '') + (size / 1.25) + 'px sans-serif'; };
  const textW = (size, bold, s) => { setFont(mctx, size, bold); return mctx.measureText(s).width; };
  const textH = (size, bold) => { setFont(mctx, size, bold); return __py_measure_h(meas.__id); };

  function rrect(ctx, x, y, w, h, r) {
    r = Math.min(r, w / 2, h / 2);
    ctx.beginPath();
    ctx.moveTo(x + r, y); ctx.lineTo(x + w - r, y); ctx.arc(x + w - r, y + r, r, -Math.PI / 2, 0);
    ctx.lineTo(x + w, y + h - r); ctx.arc(x + w - r, y + h - r, r, 0, Math.PI / 2);
    ctx.lineTo(x + r, y + h); ctx.arc(x + r, y + h - r, r, Math.PI / 2, Math.PI);
    ctx.lineTo(x, y + r); ctx.arc(x + r, y + r, r, Math.PI, Math.PI * 1.5);
    ctx.closePath();
  }
  const fillRR = (ctx, x, y, w, h, r, color) => { rrect(ctx, x, y, w, h, r); ctx.fillStyle = color; ctx.fill(); };
  const strokeRR = (ctx, x, y, w, h, r, color, lw) => { rrect(ctx, x + lw / 2, y + lw / 2, w - lw, h - lw, r); ctx.strokeStyle = color; ctx.lineWidth = lw; ctx.stroke(); };

  class Atom { constructor(w, h, draw, index, focused) { this.w = w; this.h = h; this.draw = draw; this.index = index; this.focused = focused; } }

  class Layout {
    constructor(width) { this.width = width; this.ops = []; this.focusRects = {}; this.curRect = null; }

    atoms(nodes, style, out) {
      for (const node of nodes) {
        if ('x' in node) {
          const size = style.size, bold = style.bold, color = style.color, h = textH(size, bold);
          for (const word of node.x.match(/\s*\S+|\s+/g) || []) {
            const w = textW(size, bold, word);
            out.push(new Atom(w, h, (ctx, x, y) => { setFont(ctx, size, bold); ctx.textBaseline = 'top'; ctx.textAlign = 'left'; ctx.fillStyle = color; ctx.fillText(word, x, y); }));
          }
          continue;
        }
        const tag = node.t;
        if (tag === 'br') { out.push(new Atom(0, 0, null)); continue; }
        if (tag === 'button' || (node.f !== undefined && ['a', 'input', 'select'].includes(tag))) { out.push(this.buttonAtom(node, style)); continue; }
        if (tag === 'canvas' || tag === 'img') { out.push(this.canvasAtom(node)); continue; }
        const sub = Object.assign({}, style);
        if (tag === 'b' || tag === 'strong') sub.bold = true;
        if (tag === 'small') { sub.size = Math.max(14, style.size - 4); sub.color = DIM; }
        if (hasClass(node, /(^|\s)(note|dim|pct|shop-note)(\s|$)/)) sub.color = DIM;
        this.atoms(node.k || [], sub, out);
      }
    }

    canvasAtom(node) {
      let w = node.w || 24, h = node.h || 24;
      const scale = Math.min(1.0, 32.0 / Math.max(1, h));
      const sw = w, sh = h;
      w = Math.max(1, Math.floor(w * scale)); h = Math.max(1, Math.floor(h * scale));
      const cid = node.cv;
      return new Atom(w + 4, h, (ctx, x, y) => { if (cid !== undefined) ctx.drawImage({ __id: cid }, 0, 0, sw, sh, x, y, w, h); });
    }

    buttonAtom(node, style, fullWidth) {
      const label = textOf(node).trim() || node.v || '';
      const tw = textW(21, true, label), th = textH(21, true);
      const w = fullWidth || (tw + 28), h = th + 14;
      const primary = hasClass(node, /(^|\s)primary(\s|$)/), focus = !!node.on, disabled = !!node.d;
      return new Atom(w, h, (ctx, x, y) => {
        let fill, fg, border;
        if (disabled) { fill = '#23232c'; fg = '#6e6e7a'; border = LINE; }
        else if (focus) { fill = primary ? '#ffd678' : '#f0f0fa'; fg = DARK; border = '#ffffff'; }
        else if (primary) { fill = GOLD; fg = DARK; border = GOLD; }
        else { fill = KEY; fg = TEXT; border = LINE; }
        fillRR(ctx, x, y, w, h, 6, fill);
        strokeRR(ctx, x, y, w, h, 6, border, focus ? 3 : 1);
        setFont(ctx, 21, true); ctx.textBaseline = 'top'; ctx.textAlign = 'center'; ctx.fillStyle = fg;
        ctx.fillText(label, x + w / 2, y + Math.floor((h - th) / 2));
      }, node.f, focus);
    }

    emitLine(line, x, y, width, align) {
      const lw = line.reduce((s, a) => s + a.w, 0), lh = line.reduce((m, a) => Math.max(m, a.h), 0);
      let dx = x + (align === 'center' ? Math.floor((width - lw) / 2) : 0);
      for (const atom of line) {
        const ay = y + Math.floor((lh - atom.h) / 2);
        if (atom.index !== undefined && atom.index !== null) {
          const rect = { x: dx, y: ay, w: atom.w, h: atom.h };
          this.focusRects[atom.index] = rect;
          if (atom.focused) this.curRect = rect;
        }
        if (atom.draw) { const ax = dx; this.ops.push((ctx) => atom.draw(ctx, ax, ay)); }
        dx += atom.w;
      }
      return lh;
    }

    flow(nodes, x, y, width, style, align) {
      const atoms = []; this.atoms(nodes, style, atoms);
      const lines = []; let line = [], used = 0;
      for (const atom of atoms) {
        if (atom.w === 0 && atom.h === 0 && atom.draw === null) { lines.push(line); line = []; used = 0; continue; }
        if (used + atom.w > width && line.length) { lines.push(line); line = []; used = 0; }
        line.push(atom); used += atom.w;
      }
      if (line.length) lines.push(line);
      const top = y;
      for (const l of lines) y += this.emitLine(l, x, y, width, align || 'left') + 2;
      return Math.max(0, y - top);
    }

    block(node, x, y, width, style) {
      if ('x' in node) return node.x.trim() ? this.flow([node], x, y, width, style) : 0;
      const tag = node.t, kids = node.k || [];
      if (tag === 'button' || (node.f !== undefined && (tag === 'a' || tag === 'select'))) return this.buttonBlock(node, x, y, width);
      if (tag === 'canvas' || tag === 'img') return this.flow([node], x, y, width, style);
      if (tag === 'table') return this.table(node, x, y, width, style);
      if (tag === 'hr') { this.ops.push((ctx) => { ctx.fillStyle = LINE; ctx.fillRect(x, y + 3, width, 1); }); return 8; }
      if (hasClass(node, /(^|\s)bar(\s|$)/)) return this.bar(node, x, y, width);
      if (tag in HEADINGS) {
        const sub = Object.assign({}, style, { size: HEADINGS[tag], bold: true, color: (tag === 'h2' || tag === 'h3') ? DIM : TEXT });
        return this.flow(kids, x, y, width, sub, 'center') + 6;
      }
      const sub = Object.assign({}, style);
      if (tag === 'p' || hasClass(node, /(^|\s)note(\s|$)/)) {
        sub.color = hasClass(node, /(^|\s)note(\s|$)/) ? DIM : style.color;
        sub.size = hasClass(node, /(^|\s)note(\s|$)/) ? 19 : style.size;
      }
      if (hasClass(node, /(^|\s)pct(\s|$)/)) { sub.color = GOLD; sub.size = 18; }
      const isBtn = (k) => k.t !== undefined && (k.t === 'button' || k.f !== undefined);
      const buttons = kids.filter(isBtn);
      const blocky = kids.filter((k) => k.t !== undefined && !INLINE.has(k.t) && !['canvas', 'img', 'input', 'br'].includes(k.t));
      const meaningful = kids.filter((k) => k.t !== undefined || (k.x || '').trim()).length;
      if (ROW_CLASS.test(node.c || '') && !(buttons.length === kids.length && kids.length > 1 && !hasClass(node, /(^|\s)row(\s|$)/))) return this.row(node, x, y, width, sub);
      if (buttons.length && buttons.length === meaningful) {
        const top = y;
        for (const b of buttons) y += this.buttonBlock(b, x, y, width) + 6;
        return y - top;
      }
      if (blocky.length || buttons.length) {
        const top = y; let run = [];
        for (const k of kids) {
          if ('x' in k || (k.t !== undefined && (INLINE.has(k.t) || ['canvas', 'img', 'input', 'br'].includes(k.t)))) { run.push(k); continue; }
          if (run.length) { y += this.flow(run, x, y, width, sub); run = []; }
          y += this.block(k, x, y, width, sub) + ((k.t === 'div' || k.t === 'section' || k.t === 'p') ? 3 : 0);
        }
        if (run.length) y += this.flow(run, x, y, width, sub);
        return y - top;
      }
      let align = ((tag === 'p' || tag === 'div') && (hasClass(node, /(^|\s)(box|hero|center|msg)(\s|$)/) || /menu-msg/.test(node.c || ''))) ? 'center' : 'left';
      if (node.id === 'menu-msg' || node.id === 'loading-msg') align = 'center';
      return this.flow(kids, x, y, width, sub, align) + (tag === 'p' ? 4 : 0);
    }

    buttonBlock(node, x, y, width) {
      const atom = this.buttonAtom(node, {}, width);
      this.ops.push((ctx) => atom.draw(ctx, x, y));
      if (node.f !== undefined) this.focusRects[node.f] = { x, y, w: width, h: atom.h };
      if (node.on || hasClass(node, /(^|\s)cur(\s|$)/)) this.curRect = { x, y, w: width, h: atom.h };
      return atom.h;
    }

    highlight(mark, rect) {
      this.ops.splice(mark, 0, (ctx) => {
        fillRR(ctx, rect.x, rect.y, rect.w, rect.h, 6, 'rgba(255,255,255,0.1)');
        strokeRR(ctx, rect.x, rect.y, rect.w, rect.h, 6, GOLD, 2);
      });
      this.curRect = rect;
    }

    row(node, x, y, width, style) {
      const kids = (node.k || []).filter((k) => k.t !== undefined || (k.x || '').trim());
      if (!kids.length) return 0;
      const naturals = kids.map((k) => this.naturalWidth(k, style));
      const total = naturals.reduce((a, b) => a + b, 0) + 8 * (kids.length - 1);
      let flex = 0; naturals.forEach((n, i) => { if (n > naturals[flex]) flex = i; });
      const widths = naturals.slice();
      if (total > width) widths[flex] = Math.max(60, width - (naturals.reduce((a, b) => a + b, 0) - naturals[flex]) - 8 * (kids.length - 1));
      else widths[flex] += width - total;
      const mark = this.ops.length;
      let cx = x, rowh = 0;
      kids.forEach((k, i) => { rowh = Math.max(rowh, this.place(k, cx, y, widths[i], style)); cx += widths[i] + 8; });
      if (hasClass(node, /(^|\s)cur(\s|$)/)) this.highlight(mark, { x: x - 3, y: y - 2, w: width + 6, h: rowh + 4 });
      return rowh + 4;
    }

    place(k, x, y, w, style) {
      if ('x' in k) return this.flow([k], x, y, w, style);
      if (INLINE.has(k.t) || k.t === 'canvas' || k.t === 'img') return this.flow([k], x, y, w, style);
      return this.block(k, x, y, w, style);
    }

    naturalWidth(k, style) {
      if ('x' in k) return textW(style.size, style.bold, k.x) + 2;
      if (k.t === 'button' || k.f !== undefined) return textW(21, true, textOf(k).trim()) + 28;
      if (k.t === 'canvas' || k.t === 'img') { const h = k.h || 24; return Math.floor((k.w || 24) * Math.min(1.0, 32.0 / Math.max(1, h))) + 4; }
      const kids = k.k || [];
      if (INLINE.has(k.t)) return kids.reduce((s, c) => s + this.naturalWidth(c, style), 0);
      return Math.min(this.width, kids.reduce((m, c) => Math.max(m, this.naturalWidth(c, style)), 0));
    }

    table(node, x, y, width, style) {
      const rows = [];
      const collect = (n) => { for (const c of n.k || []) { if (c.t === undefined) continue; if (c.t === 'tr') rows.push([c, (c.k || []).filter((cell) => cell.t === 'td' || cell.t === 'th')]); else collect(c); } };
      collect(node);
      if (!rows.length) return 0;
      const columns = Math.max(...rows.map((r) => r[1].length));
      let naturals = new Array(columns).fill(0);
      for (const [, cells] of rows) cells.forEach((cell, i) => { naturals[i] = Math.max(naturals[i], textW(19, cell.t === 'th', textOf(cell).trim()) + 12); });
      const total = naturals.reduce((a, b) => a + b, 0);
      if (total > width) { const scale = width / total; naturals = naturals.map((n) => Math.max(40, Math.floor(n * scale))); }
      const top = y;
      for (const [tr, cells] of rows) {
        const mark = this.ops.length; let cx = x, rowh = 0;
        cells.forEach((cell, i) => {
          const sub = Object.assign({}, style, { size: 19, bold: cell.t === 'th', color: cell.t === 'th' ? DIM : style.color });
          rowh = Math.max(rowh, this.flow(cell.k || [], cx, y, naturals[i] - 8, sub));
          cx += naturals[i];
        });
        if (hasClass(tr, /(^|\s)cur(\s|$)/)) this.highlight(mark, { x: x - 3, y: y - 2, w: Math.min(width, naturals.reduce((a, b) => a + b, 0)) + 6, h: rowh + 4 });
        y += rowh + 4;
      }
      return y - top;
    }

    bar(node, x, y, width) {
      const fill = (node.k || []).find((k) => k.pw !== undefined);
      const frac = Math.max(0, Math.min(1, (fill ? fill.pw : 0) / 100));
      this.ops.push((ctx) => { fillRR(ctx, x, y, width, 10, 4, '#2b2b36'); if (frac > 0) fillRR(ctx, x, y, Math.max(1, Math.floor(width * frac)), 10, 4, GOLD); });
      return 16;
    }
  }

  // ---- the overlay itself
  const panel = document.createElement('canvas');
  let version = -1, active = -1, scroll = 0, height = 0, focusRects = {}, focusIndices = [], defaultFocus = 0, curRect = null, ops = [], visibleNow = false, panelW = 0;

  const hasContent = (n) => ('x' in n ? !!n.x.trim() : (['canvas', 'img', 'button', 'input'].includes(n.t) || n.pw !== undefined || (n.k || []).some(hasContent)));
  const isHotkeyRoot = (r) => r.t === 'button' || (r.f !== undefined && !(r.k && r.k.length));
  function focusablesOf(nodes) { const found = []; for (const n of nodes) { if (n.t === undefined) continue; if (n.f !== undefined) found.push(n.f); found.push(...focusablesOf(n.k || [])); } return found; }
  function defaultFocusOf(nodes) {
    const first = [], primary = [];
    const walk = (list) => { for (const n of list) { if (n.t === undefined) continue; if (n.f !== undefined && !n.d) { first.push(n.f); if (hasClass(n, /(^|\s)primary(\s|$)/)) primary.push(n.f); } walk(n.k || []); } };
    walk(nodes);
    return primary.length ? primary[0] : (first.length ? first[0] : 0);
  }

  function scrollToFocus() {
    const viewH = VIEW_H - 2 * PANEL_MARGIN;
    const rect = focusRects[active] || curRect;
    if (height <= viewH) { scroll = 0; return; }
    if (rect) {
      if (rect.y - 8 < scroll) scroll = Math.max(0, rect.y - 8);
      else if (rect.y + rect.h + 8 > scroll + viewH) scroll = Math.min(height - viewH, rect.y + rect.h + 8 - viewH);
    }
    scroll = Math.max(0, Math.min(scroll, height - viewH));
  }

  function compose() {
    const viewH = Math.min(height, VIEW_H - 2 * PANEL_MARGIN);
    panel.width = panelW; panel.height = viewH;                    // (resizing clears the panel)
    const ctx = panel.getContext('2d');
    ctx.clearRect(0, 0, panelW, viewH);
    fillRR(ctx, 0, 0, panelW, viewH, 10, BG);
    ctx.save();
    ctx.beginPath(); ctx.rect(0, 0, panelW, viewH); ctx.clip();
    ctx.translate(0, -scroll);
    for (const op of ops) op(ctx);
    ctx.restore();
    strokeRR(ctx, 0, 0, panelW, viewH, 10, LINE, 1);
    if (height > viewH) {
      const track = Math.floor(viewH * viewH / height), top = Math.floor(scroll * (viewH - track) / Math.max(1, height - viewH));
      fillRR(ctx, panelW - 5, top, 3, track, 2, '#5a5a69');
    }
    __sgba_overlay_set(panel.__id, Math.floor((VIEW_W - panelW) / 2), Math.floor((VIEW_H - viewH) / 2), panelW, viewH);
    visibleNow = true;
  }

  function build(roots) {
    const panels = roots.filter((r) => r.t !== undefined && !isHotkeyRoot(r)).filter(hasContent);
    focusRects = {}; focusIndices = []; curRect = null;
    if (!panels.length) { if (visibleNow) { __sgba_overlay_set(-1, 0, 0, 0, 0); visibleNow = false; } __sgba_overlay_modal(0); return; }
    panelW = VIEW_W - 2 * PANEL_MARGIN;
    const layout = new Layout(panelW - 2 * PANEL_PAD);
    const base = { size: 21, bold: false, color: TEXT };
    let y = PANEL_PAD;
    for (const root of panels) y += layout.block(root, PANEL_PAD, y, panelW - 2 * PANEL_PAD, base);
    height = y + PANEL_PAD;
    focusRects = layout.focusRects; ops = layout.ops; curRect = layout.curRect;
    focusIndices = focusablesOf(panels);
    defaultFocus = defaultFocusOf(panels);
    scrollToFocus();
    compose();
    __sgba_overlay_modal(focusIndices.length ? 1 : 0);
  }

  function refresh() {
    const v = __sgba_ver();
    if (v === version) return;
    version = v;
    const data = JSON.parse(__sgba_ui());
    active = data.active;
    build(data.roots);
  }

  g.__sgba_overlay_tick = () => {
    refresh();
    if (focusIndices.length && !focusIndices.includes(active)) { __sgba_ui_focus(defaultFocus); version = -1; refresh(); }   // an open menu always has something focused
  };
  g.__sgba_overlay_nav = (what) => {
    if (!focusIndices.length) return;
    if (what === 'up' || what === 'down') {
      const delta = what === 'down' ? 1 : -1, n = focusIndices.length;
      const pos = focusIndices.includes(active) ? focusIndices.indexOf(active) : (delta > 0 ? -1 : 0);
      __sgba_ui_focus(focusIndices[((pos + delta) % n + n) % n]);
    } else if (what === 'a') __sgba_ui_click(focusIndices.includes(active) ? active : focusIndices[0]);
    else if (what === 'b') __sgba_ui_back();
    version = -1; refresh();
  };
  g.__sgba_overlay_hotkey = (id) => __sgba_ui_activate_by_id(id);
})(globalThis);
