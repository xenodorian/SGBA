// Browser shim for SGBA. Runs inside QuickJS. Native work is done by Python callables named __py_*.
// Only what 2D canvas games need: window, timers, requestAnimationFrame, events, a tolerant DOM stub,
// canvas 2D, Image, localStorage, fetch for local files, Audio stubs, gamepad.
(function (g) {
  'use strict';
  g.window = g; g.self = g; g.globalThis = g; g.top = g; g.parent = g;

  // ---- console
  const fmt = (a) => a.map((v) => {
    if (typeof v === 'string') return v;
    try { return v instanceof Error ? (v.stack || String(v)) : JSON.stringify(v); } catch (e) { return String(v); }
  }).join(' ');
  g.console = {
    log: (...a) => __py_log('log', fmt(a)), info: (...a) => __py_log('log', fmt(a)),
    warn: (...a) => __py_log('warn', fmt(a)), error: (...a) => __py_log('error', fmt(a)),
    debug: () => {}, trace: () => {}, assert: () => {}, group: () => {}, groupEnd: () => {}, table: () => {},
    time: () => {}, timeEnd: () => {},
  };

  // ---- virtual clock and timers
  let now = 0;
  g.performance = { now: () => now, mark() {}, measure() {}, timeOrigin: 0 };
  let timerSeq = 1;
  const timers = new Map();
  let rafSeq = 1;
  let rafQueue = [];
  const guard = (fn, args) => { try { return fn(...args); } catch (e) { __py_log('error', 'uncaught: ' + (e && e.message ? e.message + '\n' + e.stack : String(e) + '\n' + (e && e.stack))); } };
  g.setTimeout = (fn, ms, ...args) => { const id = timerSeq++; timers.set(id, { fn, due: now + Math.max(0, +ms || 0), every: -1, args }); return id; };
  g.setInterval = (fn, ms, ...args) => { const id = timerSeq++; const e = Math.max(1, +ms || 1); timers.set(id, { fn, due: now + e, every: e, args }); return id; };
  g.clearTimeout = g.clearInterval = (id) => { timers.delete(id); };
  g.requestAnimationFrame = (fn) => { const id = rafSeq++; rafQueue.push({ id, fn }); return id; };
  g.cancelAnimationFrame = (id) => { rafQueue = rafQueue.filter((r) => r.id !== id); };
  g.queueMicrotask = (fn) => { Promise.resolve().then(fn); };
  g.requestIdleCallback = (fn) => g.setTimeout(() => fn({ timeRemaining: () => 10, didTimeout: false }), 1);
  g.cancelIdleCallback = g.clearTimeout;

  // Called by Python once per frame with the virtual time in ms.
  g.__sgba_tick = (t) => {
    now = t;
    const due = [];
    timers.forEach((tm, id) => { if (tm.due <= now) due.push([id, tm]); });
    due.sort((a, b) => a[1].due - b[1].due);
    for (const [id, tm] of due) {
      if (!timers.has(id)) continue;
      if (tm.every >= 0) tm.due = now + tm.every; else timers.delete(id);
      guard(tm.fn, tm.args);
    }
    const q = rafQueue; rafQueue = [];
    for (const r of q) guard(r.fn, [now]);
    if (g.__sgba_audio_tick) g.__sgba_audio_tick(now);
  };

  // ---- events
  class Event {
    constructor(type, init) { this.type = type; Object.assign(this, init || {}); this.defaultPrevented = false; this.bubbles = !init || init.bubbles !== false; this.timeStamp = now; this._stop = false; }
    preventDefault() { this.defaultPrevented = true; }
    stopPropagation() { this._stop = true; }
    stopImmediatePropagation() { this._stop = true; }
  }
  g.Event = Event;
  g.CustomEvent = class CustomEvent extends Event { constructor(t, i) { super(t, i); this.detail = i ? i.detail : null; } };
  g.KeyboardEvent = class KeyboardEvent extends Event {};
  g.MouseEvent = class MouseEvent extends Event {};
  g.TouchEvent = class TouchEvent extends Event {};
  g.PointerEvent = class PointerEvent extends Event {};
  g.MessageEvent = class MessageEvent extends Event {};

  class EventTarget {
    constructor() { this.__l = {}; }
    addEventListener(type, fn, opts) {
      if (!fn) return;
      const list = (this.__l[type] = this.__l[type] || []);
      if (!list.some((x) => x.fn === fn)) list.push({ fn, once: !!(opts && typeof opts === 'object' && opts.once) });
    }
    removeEventListener(type, fn) { const l = this.__l[type]; if (l) this.__l[type] = l.filter((x) => x.fn !== fn); }
    dispatchEvent(ev) {
      ev.target = ev.target || this;
      let t = this;
      while (t) {
        ev.currentTarget = t;
        const l = t.__l && t.__l[ev.type];
        if (l) for (const h of l.slice()) {
          if (h.once) t.removeEventListener(ev.type, h.fn);
          guard(typeof h.fn === 'function' ? h.fn : (e) => h.fn.handleEvent(e), [ev]);
          if (ev._stop) return !ev.defaultPrevented;
        }
        const on = t['on' + ev.type];
        if (typeof on === 'function') guard(on.bind(t), [ev]);
        if (ev._stop || !ev.bubbles) break;
        t = t.__parent();
      }
      return !ev.defaultPrevented;
    }
    __parent() { return null; }
  }
  g.EventTarget = EventTarget;
  Object.setPrototypeOf(g, EventTarget.prototype);
  g.__l = {};

  // ---- DOM: a real (small) tree so game code can find, change and focus elements.
  // The engine draws the overlay elements itself (see sgba_js_ui.py); this tree is only data.
  let domVer = 1;
  const bump = () => { domVer++; };
  g.__sgba_ver = () => domVer;
  const VOID = new Set(['area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'source', 'track', 'wbr']);
  const ENTITIES = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ', hellip: '…', middot: '·', times: '×' };
  const decode = (s) => s.replace(/&(#x?[0-9a-f]+|[a-z]+);/gi, (m, e) => {
    if (e[0] === '#') { const n = e[1] === 'x' || e[1] === 'X' ? parseInt(e.slice(2), 16) : parseInt(e.slice(1), 10); return isNaN(n) ? m : String.fromCodePoint(n); }
    return ENTITIES[e.toLowerCase()] !== undefined ? ENTITIES[e.toLowerCase()] : m;
  });
  const camel = (k) => k.replace(/-([a-z])/g, (m, c) => c.toUpperCase());
  const kebab = (k) => k.replace(/[A-Z]/g, (c) => '-' + c.toLowerCase());

  const makeStyle = (owner) => {
    const store = {};
    return new Proxy(store, {
      get(t, k) {
        if (k === 'setProperty') return (n, v) => { t[camel(n)] = String(v); bump(); };
        if (k === 'removeProperty') return (n) => { delete t[camel(n)]; bump(); };
        if (k === 'getPropertyValue') return (n) => t[camel(n)] || '';
        if (k === 'cssText') return Object.keys(t).map((p) => kebab(p) + ':' + t[p]).join(';');
        return typeof k === 'string' && k in t ? t[k] : '';
      },
      set(t, k, v) {
        if (k === 'cssText') { for (const p of Object.keys(t)) delete t[p]; String(v).split(';').forEach((d) => { const i = d.indexOf(':'); if (i > 0) t[camel(d.slice(0, i).trim())] = d.slice(i + 1).trim(); }); }
        else t[k] = String(v);
        bump(); return true;
      },
    });
  };

  class TextNode extends EventTarget {
    constructor(s) { super(); this.nodeType = 3; this.nodeName = '#text'; this._d = String(s); this.parentNode = null; }
    get data() { return this._d; } set data(v) { this._d = String(v); bump(); }
    get textContent() { return this._d; } set textContent(v) { this._d = String(v); bump(); }
    get nodeValue() { return this._d; }
    __parent() { return this.parentNode; }
    remove() { if (this.parentNode) this.parentNode.removeChild(this); }
  }
  g.Text = TextNode;

  const BOOL_ATTRS = ['hidden', 'disabled', 'checked', 'selected', 'readonly', 'required', 'multiple', 'autofocus'];
  class Element extends EventTarget {
    constructor(tag) {
      super(); this.localName = String(tag).toLowerCase(); this.tagName = this.localName.toUpperCase(); this.nodeName = this.tagName; this.nodeType = 1;
      this.childNodes = []; this.parentNode = null; this.__attrs = {}; this.style = makeStyle(this); this._value = '';
      const self = this;
      this.dataset = new Proxy({}, {
        get: (t, k) => (typeof k === 'string' && self.__attrs['data-' + kebab(k)] !== undefined ? self.__attrs['data-' + kebab(k)] : undefined),
        set: (t, k, v) => { self.setAttribute('data-' + kebab(k), v); return true; },
        has: (t, k) => self.__attrs['data-' + kebab(k)] !== undefined,
        deleteProperty: (t, k) => { self.removeAttribute('data-' + kebab(k)); return true; },
      });
      this.classList = {
        add: (...c) => { const s = self.__cls(); c.forEach((x) => s.add(x)); self.__setCls(s); },
        remove: (...c) => { const s = self.__cls(); c.forEach((x) => s.delete(x)); self.__setCls(s); },
        toggle: (c, f) => { const s = self.__cls(); const on = f === undefined ? !s.has(c) : !!f; on ? s.add(c) : s.delete(c); self.__setCls(s); return on; },
        contains: (c) => self.__cls().has(c),
        replace: (a, b) => { const s = self.__cls(); if (s.has(a)) { s.delete(a); s.add(b); self.__setCls(s); } },
        get length() { return self.__cls().size; },
      };
    }
    __cls() { return new Set((this.__attrs['class'] || '').split(/\s+/).filter(Boolean)); }
    __setCls(s) { this.__attrs['class'] = [...s].join(' '); bump(); }
    __parent() { return this.parentNode || (this.__detached ? null : g.document); }
    get id() { return this.__attrs.id || ''; } set id(v) { this.setAttribute('id', v); }
    get className() { return this.__attrs['class'] || ''; } set className(v) { this.setAttribute('class', v); }
    get type() { return this.__attrs.type || (this.localName === 'button' ? 'submit' : 'text'); } set type(v) { this.setAttribute('type', v); }
    get name() { return this.__attrs.name || ''; } set name(v) { this.setAttribute('name', v); }
    get title() { return this.__attrs.title || ''; } set title(v) { this.setAttribute('title', v); }
    get href() { return this.__attrs.href || ''; } set href(v) { this.setAttribute('href', v); }
    get tabIndex() { return this.__attrs.tabindex !== undefined ? +this.__attrs.tabindex : (['button', 'input', 'select', 'textarea', 'a'].includes(this.localName) ? 0 : -1); }
    set tabIndex(v) { this.setAttribute('tabindex', v); }
    get value() { return this.__attrs.value !== undefined ? this.__attrs.value : this._value; } set value(v) { this._value = String(v); this.__attrs.value = String(v); bump(); }
    get children() { return this.childNodes.filter((c) => c.nodeType === 1); }
    get childElementCount() { return this.children.length; }
    get textContent() { return this.childNodes.map((c) => c.textContent).join(''); }
    set textContent(v) { this.childNodes.forEach((c) => { c.parentNode = null; }); this.childNodes = []; if (v !== '' && v != null) this.__add(new TextNode(v)); bump(); }
    get innerText() { return this.textContent; } set innerText(v) { this.textContent = v; }
    get innerHTML() { return this.childNodes.map((c) => (c.nodeType === 3 ? c.data : c.outerHTML)).join(''); }
    set innerHTML(v) { this.childNodes.forEach((c) => { c.parentNode = null; }); this.childNodes = []; parseInto(this, String(v)); bump(); }
    get outerHTML() {
      const attrs = Object.keys(this.__attrs).map((k) => ' ' + k + '="' + this.__attrs[k] + '"').join('');
      return '<' + this.localName + attrs + '>' + (VOID.has(this.localName) ? '' : this.innerHTML + '</' + this.localName + '>');
    }
    get firstChild() { return this.childNodes[0] || null; } get lastChild() { return this.childNodes[this.childNodes.length - 1] || null; }
    get firstElementChild() { return this.children[0] || null; } get lastElementChild() { const c = this.children; return c[c.length - 1] || null; }
    get nextSibling() { const p = this.parentNode; if (!p) return null; const i = p.childNodes.indexOf(this); return p.childNodes[i + 1] || null; }
    get previousSibling() { const p = this.parentNode; if (!p) return null; const i = p.childNodes.indexOf(this); return i > 0 ? p.childNodes[i - 1] : null; }
    get nextElementSibling() { let n = this.nextSibling; while (n && n.nodeType !== 1) n = n.nextSibling; return n; }
    get previousElementSibling() { let n = this.previousSibling; while (n && n.nodeType !== 1) n = n.previousSibling; return n; }
    get parentElement() { return this.parentNode && this.parentNode.nodeType === 1 ? this.parentNode : null; }
    get isConnected() { let n = this; while (n) { if (n === g.document.documentElement) return true; n = n.parentNode; } return false; }
    get offsetWidth() { return this.width || 640; } get offsetHeight() { return this.height || 480; }
    get clientWidth() { return this.width || 640; } get clientHeight() { return this.height || 480; }
    get scrollTop() { return 0; } set scrollTop(v) {} get scrollLeft() { return 0; } set scrollLeft(v) {}
    get scrollHeight() { return 0; } get scrollWidth() { return 0; }
    __add(c) { c.parentNode = this; this.childNodes.push(c); }
    appendChild(c) {
      if (c.nodeName === '#fragment') { [...c.childNodes].forEach((k) => this.appendChild(k)); return c; }
      if (c.parentNode) c.parentNode.removeChild(c);
      this.__add(c); bump(); return c;
    }
    append(...cs) { cs.forEach((c) => this.appendChild(typeof c === 'string' ? new TextNode(c) : c)); }
    prepend(...cs) { const first = this.childNodes[0]; cs.forEach((c) => { const n = typeof c === 'string' ? new TextNode(c) : c; this.insertBefore(n, first || null); }); }
    insertBefore(c, ref) {
      if (!ref) return this.appendChild(c);
      if (c.parentNode) c.parentNode.removeChild(c);
      const i = this.childNodes.indexOf(ref);
      c.parentNode = this; this.childNodes.splice(i < 0 ? this.childNodes.length : i, 0, c); bump(); return c;
    }
    removeChild(c) { const i = this.childNodes.indexOf(c); if (i >= 0) this.childNodes.splice(i, 1); c.parentNode = null; bump(); return c; }
    replaceChild(n, o) { this.insertBefore(n, o); this.removeChild(o); return o; }
    remove() { if (this.parentNode) this.parentNode.removeChild(this); }
    replaceChildren(...cs) { this.childNodes.forEach((c) => { c.parentNode = null; }); this.childNodes = []; cs.forEach((c) => this.appendChild(typeof c === 'string' ? new TextNode(c) : c)); bump(); }
    contains(c) { while (c) { if (c === this) return true; c = c.parentNode; } return false; }
    setAttribute(k, v) {
      k = String(k).toLowerCase(); v = String(v);
      if (BOOL_ATTRS.includes(k) && v === 'false') { delete this.__attrs[k]; } else { this.__attrs[k] = BOOL_ATTRS.includes(k) && v === '' ? '' : v; }
      if (k === 'style') this.style.cssText = v;
      bump();
    }
    getAttribute(k) { k = String(k).toLowerCase(); return k in this.__attrs ? this.__attrs[k] : null; }
    removeAttribute(k) { delete this.__attrs[String(k).toLowerCase()]; bump(); }
    hasAttribute(k) { return String(k).toLowerCase() in this.__attrs; }
    toggleAttribute(k, f) { const on = f === undefined ? !this.hasAttribute(k) : !!f; on ? this.setAttribute(k, '') : this.removeAttribute(k); return on; }
    querySelector(sel) { return querySel(this, sel, true)[0] || null; }
    querySelectorAll(sel) { return querySel(this, sel, false); }
    getElementsByTagName(t) { return querySel(this, String(t).toLowerCase() === '*' ? '*' : t, false); }
    getElementsByClassName(c) { return querySel(this, String(c).split(/\s+/).filter(Boolean).map((x) => '.' + x).join(''), false); }
    closest(sel) { let n = this; while (n && n.nodeType === 1) { if (matches(n, sel)) return n; n = n.parentNode; } return null; }
    matches(sel) { return matches(this, sel); }
    focus() { if (g.document.activeElement !== this) { g.document.activeElement = this; bump(); this.dispatchEvent(new Event('focus', { bubbles: false })); } }
    blur() { if (g.document.activeElement === this) { g.document.activeElement = g.document.body; bump(); this.dispatchEvent(new Event('blur', { bubbles: false })); } }
    click() { if (this.disabled) return; const ev = new Event('click', { button: 0, detail: 1 }); this.dispatchEvent(ev); }
    scrollIntoView() {} scrollTo() {} setPointerCapture() {} releasePointerCapture() {} animate() { return { cancel() {}, finished: Promise.resolve() }; }
    cloneNode(deep) { const e = g.document.createElement(this.localName); Object.assign(e.__attrs, this.__attrs); if (deep) this.childNodes.forEach((c) => e.appendChild(c.nodeType === 3 ? new TextNode(c.data) : c.cloneNode(true))); return e; }
    getBoundingClientRect() { return { left: 0, top: 0, right: this.width || 640, bottom: this.height || 480, width: this.width || 640, height: this.height || 480, x: 0, y: 0 }; }
    requestFullscreen() { return Promise.resolve(); }
  }
  for (const a of BOOL_ATTRS) {
    Object.defineProperty(Element.prototype, a, {
      get() { return a in this.__attrs; },
      set(v) { v ? (this.__attrs[a] = '') : delete this.__attrs[a]; bump(); },
    });
  }
  g.Element = g.HTMLElement = Element;
  g.Node = Element;
  g.HTMLButtonElement = g.HTMLInputElement = g.HTMLDivElement = g.HTMLSpanElement = Element;

  // ---- selectors: tag, #id, .class, [attr], [attr=v], :not(), :disabled, :first-child, :last-child, descendant and > combinators
  const parseCompound = (s) => {
    const c = { tag: null, id: null, cls: [], attrs: [], nots: [], pseudo: [] };
    let i = 0;
    const ident = () => { const m = /^(?:[-\w]|\\.)+/.exec(s.slice(i)); if (!m) return ''; i += m[0].length; return m[0]; };
    if (s[i] === '*') i++; else if (/[\w-]/.test(s[i] || '')) c.tag = ident().toLowerCase();
    while (i < s.length) {
      const ch = s[i];
      if (ch === '#') { i++; c.id = ident(); }
      else if (ch === '.') { i++; c.cls.push(ident()); }
      else if (ch === '[') {
        const j = s.indexOf(']', i); const body = s.slice(i + 1, j); i = j + 1;
        const m = /^([\w:-]+)\s*(?:([~|^$*]?=)\s*["']?([^"']*)["']?)?$/.exec(body.trim());
        if (m) c.attrs.push([m[1].toLowerCase(), m[2] || null, m[3]]);
      } else if (ch === ':') {
        i++; if (s[i] === ':') i++;
        const name = ident();
        if (s[i] === '(') { let d = 1, j = i + 1; while (j < s.length && d) { if (s[j] === '(') d++; else if (s[j] === ')') d--; j++; } const arg = s.slice(i + 1, j - 1); i = j; if (name === 'not') c.nots.push(arg.split(',').map((x) => parseCompound(x.trim()))); else c.pseudo.push([name, arg]); }
        else c.pseudo.push([name, null]);
      } else break;
    }
    return c;
  };
  const matchCompound = (el, c) => {
    if (el.nodeType !== 1) return false;
    if (c.tag && el.localName !== c.tag) return false;
    if (c.id !== null && el.id !== c.id) return false;
    if (c.cls.length) { const s = el.__cls(); for (const k of c.cls) if (!s.has(k)) return false; }
    for (const [n, op, v] of c.attrs) {
      const a = el.__attrs[n]; if (a === undefined) return false;
      if (op === '=' && a !== v) return false;
      if (op === '^=' && !a.startsWith(v)) return false;
      if (op === '$=' && !a.endsWith(v)) return false;
      if (op === '*=' && !a.includes(v)) return false;
      if (op === '~=' && !a.split(/\s+/).includes(v)) return false;
    }
    for (const group of c.nots) if (group.some((x) => matchCompound(el, x))) return false;
    for (const [p] of c.pseudo) {
      if (p === 'disabled' && !el.disabled) return false;
      if (p === 'enabled' && el.disabled) return false;
      if (p === 'checked' && !el.checked) return false;
      if (p === 'first-child' && el.parentNode && el.parentNode.children[0] !== el) return false;
      if (p === 'last-child' && el.parentNode) { const k = el.parentNode.children; if (k[k.length - 1] !== el) return false; }
    }
    return true;
  };
  const selCache = new Map();
  const parseSelector = (sel) => {
    if (selCache.has(sel)) return selCache.get(sel);
    const groups = sel.split(/,(?![^(]*\))/).map((part) => {
      const toks = part.trim().replace(/\s*>\s*/g, ' > ').split(/\s+(?![^(\[]*[)\]])/).filter(Boolean);
      const chain = []; let comb = ' ';
      for (const t of toks) { if (t === '>') { comb = '>'; continue; } chain.push({ comb, c: parseCompound(t) }); comb = ' '; }
      return chain;
    });
    selCache.set(sel, groups); return groups;
  };
  const matchChain = (el, chain, idx) => {
    if (!matchCompound(el, chain[idx].c)) return false;
    if (idx === 0) return true;
    const comb = chain[idx].comb;
    let p = el.parentNode;
    if (comb === '>') return !!p && matchChain(p, chain, idx - 1);
    while (p) { if (matchChain(p, chain, idx - 1)) return true; p = p.parentNode; }
    return false;
  };
  const matches = (el, sel) => { try { return parseSelector(String(sel)).some((ch) => ch.length && matchChain(el, ch, ch.length - 1)); } catch (e) { return false; } };
  const querySel = (root, sel, first) => {
    const out = []; let groups;
    try { groups = parseSelector(String(sel)); } catch (e) { return out; }
    const walk = (node) => {
      for (const ch of node.childNodes) {
        if (ch.nodeType !== 1) continue;
        if (groups.some((g2) => g2.length && matchChain(ch, g2, g2.length - 1))) { out.push(ch); if (first) return true; }
        if (walk(ch)) return true;
      }
      return false;
    };
    walk(root); return out;
  };

  // ---- tiny HTML parser (innerHTML and the page body)
  const parseInto = (parent, html, styles) => {
    const re = /<!--[\s\S]*?-->|<(\/?)([a-zA-Z][\w:-]*)((?:"[^"]*"|'[^']*'|[^>"'])*)>|([^<]+|<)/g;
    const stack = [parent]; let m;
    while ((m = re.exec(html))) {
      if (m[0].startsWith('<!--')) continue;
      const cur = stack[stack.length - 1];
      if (m[4] !== undefined) { const t = decode(m[4]); if (t) cur.__add(new TextNode(t)); continue; }
      const closing = m[1] === '/', tag = m[2].toLowerCase();
      if (closing) { for (let i = stack.length - 1; i > 0; i--) if (stack[i].localName === tag) { stack.length = i; break; } continue; }
      if (tag === 'script' || tag === 'style') {
        const end = html.indexOf('</' + tag, re.lastIndex); const body = html.slice(re.lastIndex, end < 0 ? html.length : end);
        re.lastIndex = end < 0 ? html.length : html.indexOf('>', end) + 1;
        if (tag === 'style') { const st = g.document.createElement('style'); st.textContent = body; cur.__add(st); }
        continue;
      }
      const el = g.document.createElement(tag); el.__detached = false;
      const are = /([^\s=\/]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g; let a;
      while ((a = are.exec(m[3]))) { const v = a[2] !== undefined ? a[2] : a[3] !== undefined ? a[3] : a[4] !== undefined ? a[4] : ''; el.setAttribute(a[1], decode(v)); }
      cur.__add(el);
      if (!VOID.has(tag) && !/\/\s*$/.test(m[3])) stack.push(el);
    }
  };

  // ---- canvas
  let canvasSeq = 1;
  class CanvasGradient { constructor(kind, args) { this.kind = kind; this.args = args; this.stops = []; } addColorStop(o, c) { this.stops.push([+o, String(c)]); } }
  g.CanvasGradient = CanvasGradient;
  const styleToNative = (v) => (v instanceof CanvasGradient ? JSON.stringify({ g: v.kind, a: v.args, s: v.stops }) : String(v));

  class Context2D {
    constructor(canvas) { this.canvas = canvas; this.__id = canvas.__id; }
  }
  const setters = ['fillStyle', 'strokeStyle', 'globalAlpha', 'lineWidth', 'font', 'textAlign', 'textBaseline',
    'globalCompositeOperation', 'imageSmoothingEnabled', 'lineCap', 'lineJoin', 'miterLimit', 'shadowBlur', 'shadowColor',
    'shadowOffsetX', 'shadowOffsetY', 'filter', 'direction', 'lineDashOffset', 'imageSmoothingQuality'];
  const defaults = { fillStyle: '#000000', strokeStyle: '#000000', globalAlpha: 1, lineWidth: 1, font: '10px sans-serif', textAlign: 'start',
    textBaseline: 'alphabetic', globalCompositeOperation: 'source-over', imageSmoothingEnabled: true, lineCap: 'butt', lineJoin: 'miter',
    miterLimit: 10, shadowBlur: 0, shadowColor: 'rgba(0,0,0,0)', shadowOffsetX: 0, shadowOffsetY: 0, filter: 'none', direction: 'ltr',
    lineDashOffset: 0, imageSmoothingQuality: 'low' };
  for (const name of setters) {
    Object.defineProperty(Context2D.prototype, name, {
      get() { const v = this.__p ? this.__p[name] : undefined; return v === undefined ? defaults[name] : v; },
      set(v) { (this.__p = this.__p || {})[name] = v; __py_set(this.__id, name, name === 'fillStyle' || name === 'strokeStyle' ? styleToNative(v) : v); },
    });
  }
  const direct = ['fillRect', 'strokeRect', 'clearRect', 'beginPath', 'closePath', 'moveTo', 'lineTo', 'rect', 'arc', 'ellipse', 'arcTo',
    'quadraticCurveTo', 'bezierCurveTo', 'fill', 'stroke', 'clip', 'save', 'restore', 'translate', 'rotate', 'scale', 'setTransform',
    'transform', 'resetTransform', 'fillText', 'strokeText', 'setLineDash'];
  for (const name of direct) {
    Context2D.prototype[name] = function (...a) { return __py_call(this.__id, name, ...a); };
  }
  Context2D.prototype.drawImage = function (img, ...a) {
    const src = img && img.__id !== undefined ? img.__id : -1;
    if (src < 0) return;
    return __py_call(this.__id, 'drawImage', src, ...a);
  };
  Context2D.prototype.createLinearGradient = (...a) => new CanvasGradient('linear', a);
  Context2D.prototype.createRadialGradient = (...a) => new CanvasGradient('radial', a);
  Context2D.prototype.createPattern = function (img) { return { __pattern: img.__id }; };
  Context2D.prototype.measureText = function (s) { return { width: __py_measure(this.__id, String(s)), actualBoundingBoxAscent: 8, actualBoundingBoxDescent: 2 }; };
  Context2D.prototype.getTransform = function () { return { a: 1, b: 0, c: 0, d: 1, e: 0, f: 0 }; };
  Context2D.prototype.isPointInPath = () => false;
  Context2D.prototype.getImageData = function (x, y, w, h) {
    const data = new Uint8ClampedArray(w * h * 4);
    const hex = __py_getimage(this.__id, x | 0, y | 0, w | 0, h | 0);
    for (let i = 0; i < data.length; i++) data[i] = parseInt(hex.substr(i * 2, 2), 16);
    return { width: w, height: h, data };
  };
  Context2D.prototype.putImageData = function (img, x, y) {
    let s = ''; const d = img.data;
    for (let i = 0; i < d.length; i++) s += (d[i] < 16 ? '0' : '') + d[i].toString(16);
    __py_putimage(this.__id, x | 0, y | 0, img.width, img.height, s);
  };
  Context2D.prototype.createImageData = function (w, h) { return { width: w, height: h, data: new Uint8ClampedArray(w * h * 4) }; };
  g.CanvasRenderingContext2D = Context2D;

  class Canvas extends Element {
    constructor() {
      super('canvas'); this.__id = canvasSeq++; this._w = 300; this._h = 150; this.__ctx = null;
      __py_new_canvas(this.__id, this._w, this._h);
    }
    get width() { return this._w; } set width(v) { this._w = v | 0; __py_resize(this.__id, this._w, this._h); }
    get height() { return this._h; } set height(v) { this._h = v | 0; __py_resize(this.__id, this._w, this._h); }
    setAttribute(k, v) {
      super.setAttribute(k, v); const n = parseInt(v, 10); const key = String(k).toLowerCase();
      if (key === 'width' && n > 0) this.width = n; else if (key === 'height' && n > 0) this.height = n;
    }
    getContext(kind) { if (kind !== '2d') return null; return this.__ctx || (this.__ctx = new Context2D(this)); }
    toDataURL() { return 'data:image/png;base64,'; }
    toBlob(cb) { cb && cb(null); }
    captureStream() { return {}; }
  }
  g.HTMLCanvasElement = Canvas;
  g.OffscreenCanvas = class OffscreenCanvas extends Canvas { constructor(w, h) { super(); this.width = w; this.height = h; } };

  // ---- images
  class Image extends EventTarget {
    constructor(w, h) { super(); this.__id = -1; this.width = w || 0; this.height = h || 0; this.complete = false; this._src = ''; this.naturalWidth = 0; this.naturalHeight = 0; }
    get src() { return this._src; }
    set src(v) {
      this._src = String(v);
      const info = String(__py_load_image(this._src)).split(',').map(Number);
      const self = this;
      g.setTimeout(() => {
        if (info.length === 3 && info[0] >= 0) {
          self.__id = info[0]; self.width = self.naturalWidth = info[1]; self.height = self.naturalHeight = info[2]; self.complete = true;
          self.dispatchEvent(new Event('load'));
        } else { self.complete = true; self.dispatchEvent(new Event('error')); }
      }, 0);
    }
    decode() { return Promise.resolve(); }
  }
  g.Image = g.HTMLImageElement = Image;
  g.createImageBitmap = (img) => Promise.resolve(img);
  g.ImageData = function ImageData(a, b, c) { if (typeof a === 'number') { this.width = a; this.height = b; this.data = new Uint8ClampedArray(a * b * 4); } else { this.data = a; this.width = b; this.height = c; } };

  // ---- document
  class Document extends EventTarget {
    constructor() { super(); this.nodeType = 9; this.readyState = 'complete'; this.hidden = false; this.visibilityState = 'visible'; this.fullscreenElement = null; this.cookie = ''; this.title = ''; this.referrer = ''; this.activeElement = null; this.childNodes = []; }
    __parent() { return g; }
    getElementById(id) { id = String(id); return querySel(this.documentElement, '#' + id.replace(/([^\w-])/g, '\\$1'), true)[0] || null; }
    createElement(tag) {
      tag = String(tag).toLowerCase();
      if (tag === 'canvas') { const c = new Canvas(); c.__detached = true; return c; }
      if (tag === 'img') { const i = new Image(); i.__detached = true; return i; }
      if (tag === 'audio') return new g.Audio();
      const e = new Element(tag); e.__detached = true; return e;
    }
    createElementNS(ns, tag) { return this.createElement(tag); }
    createTextNode(s) { return new TextNode(s); }
    createDocumentFragment() { const e = new Element('#fragment'); e.nodeName = '#fragment'; return e; }
    querySelector(sel) { return querySel(this.documentElement, sel, true)[0] || null; }
    querySelectorAll(sel) { return querySel(this.documentElement, sel, false); }
    getElementsByTagName(t) { return querySel(this.documentElement, t, false); }
    getElementsByClassName(c) { return this.documentElement.getElementsByClassName(c); }
    exitFullscreen() { return Promise.resolve(); }
    hasFocus() { return true; }
  }
  g.document = new Document();
  const D = g.document;
  D.documentElement = new Element('html'); D.documentElement.__detached = false;
  D.head = new Element('head'); D.body = new Element('body'); D.head.__detached = D.body.__detached = false;
  D.documentElement.__add(D.head); D.documentElement.__add(D.body); D.activeElement = D.body;
  // Called by the host with the page's HTML: builds <head> and <body> contents.
  g.__sgba_load_html = (html) => {
    const bodyMatch = /<body[^>]*>([\s\S]*)<\/body>/i.exec(html);
    const headMatch = /<head[^>]*>([\s\S]*?)<\/head>/i.exec(html);
    if (headMatch) parseInto(D.head, headMatch[1]);
    parseInto(D.body, bodyMatch ? bodyMatch[1] : html);
    bump();
  };

  // ---- window-level properties
  g.innerWidth = 640; g.innerHeight = 480; g.outerWidth = 640; g.outerHeight = 480; g.devicePixelRatio = 1;
  g.screen = { width: 640, height: 480, availWidth: 640, availHeight: 480, orientation: { type: 'landscape-primary', lock: () => Promise.resolve() } };
  g.location = { href: 'http://localhost/', origin: 'http://localhost', protocol: 'http:', host: 'localhost', hostname: 'localhost', pathname: '/', search: '', hash: '', reload() {}, assign() {}, replace() {} };
  g.history = { pushState() {}, replaceState() {}, back() {}, state: null };
  g.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} });
  g.getComputedStyle = () => makeStyle();
  g.focus = () => {}; g.blur = () => {}; g.scrollTo = () => {}; g.open = () => null; g.alert = () => {}; g.confirm = () => false; g.prompt = () => null;
  g.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
  g.MutationObserver = class { observe() {} disconnect() {} };
  g.IntersectionObserver = class { observe() {} disconnect() {} };
  g.BroadcastChannel = class { postMessage() {} close() {} };
  g.Worker = class { postMessage() {} terminate() {} };
  g.postMessage = () => {};
  g.addEventListener = (...a) => EventTarget.prototype.addEventListener.apply(g, a);
  g.removeEventListener = (...a) => EventTarget.prototype.removeEventListener.apply(g, a);
  g.dispatchEvent = (...a) => EventTarget.prototype.dispatchEvent.apply(g, a);
  g.__parent = () => null;

  let pads = [];
  g.navigator = { userAgent: 'SGBA', platform: 'SGBA', language: 'en-US', languages: ['en-US'], maxTouchPoints: 0, hardwareConcurrency: 1, onLine: false,
    getGamepads: () => pads, vibrate: () => false, clipboard: { writeText: () => Promise.resolve() }, serviceWorker: undefined,
    mediaDevices: undefined, getBattery: () => Promise.resolve({ level: 1, charging: true }) };
  g.__sgba_set_pads = (list) => { pads = list; };

  // ---- storage
  class Storage {
    getItem(k) { const v = __py_storage('get', String(k), ''); return v === null || v === undefined ? null : v; }
    setItem(k, v) { __py_storage('set', String(k), String(v)); }
    removeItem(k) { __py_storage('remove', String(k), ''); }
    clear() { __py_storage('clear', '', ''); }
    key(i) { return __py_storage('key', String(i), ''); }
    get length() { return +__py_storage('length', '', ''); }
  }
  g.Storage = Storage;
  g.localStorage = new Storage(); g.sessionStorage = new Storage();

  // ---- fetch / XHR for local files. Binary files (audio) are never copied into the script's memory: arrayBuffer()
  // returns an empty buffer that remembers which file it stands for, and decodeAudioData hands that file to the audio engine.
  const marker = (url, size) => { const ab = new ArrayBuffer(0); ab.__path = url; ab.__size = size; return ab; };
  g.fetch = (url) => {
    url = String(url);
    const size = __py_stat(url);
    if (size < 0) return Promise.resolve({ ok: false, status: 404, statusText: 'Not Found', url, headers: { get: () => null },
      text: () => Promise.resolve(''), json: () => Promise.reject(new Error('404')), arrayBuffer: () => Promise.reject(new Error('404')), blob: () => Promise.reject(new Error('404')) });
    return Promise.resolve({ ok: true, status: 200, statusText: 'OK', url, headers: { get: (k) => (String(k).toLowerCase() === 'content-length' ? String(size) : null) },
      text: () => Promise.resolve(__py_read_text(url) || ''),
      json: () => Promise.resolve().then(() => JSON.parse(__py_read_text(url))),
      arrayBuffer: () => Promise.resolve(marker(url, size)),
      blob: () => Promise.resolve({ size, type: '', __path: url }) });
  };
  g.XMLHttpRequest = class XMLHttpRequest extends EventTarget {
    open(m, u) { this._u = u; this.readyState = 1; }
    setRequestHeader() {}
    send() { const size = __py_stat(String(this._u)); const self = this; g.setTimeout(() => {
      self.readyState = 4;
      if (size < 0) { self.status = 404; self.responseText = ''; }
      else { self.status = 200; if (self.responseType === 'arraybuffer') self.response = marker(String(self._u), size); else { self.responseText = __py_read_text(String(self._u)) || ''; self.response = self.responseText; } }
      self.dispatchEvent(new Event('readystatechange')); self.dispatchEvent(new Event(self.status === 200 ? 'load' : 'error')); }, 0); }
  };
  g.URL = g.URL || class URL { constructor(u, b) { this.href = String(u); } };
  g.URL.createObjectURL = (blob) => (blob && blob.__path) || ''; g.URL.revokeObjectURL = () => {};
  g.Blob = g.Blob || class Blob {};
  g.FileReader = g.FileReader || class FileReader {};
  g.TextEncoder = g.TextEncoder || class TextEncoder { encode(s) { const a = []; for (let i = 0; i < s.length; i++) a.push(s.charCodeAt(i) & 255); return new Uint8Array(a); } };

  // ---- audio: Web Audio graph and <audio> mapped onto the host's compressed-streaming engine.
  // Gains are worked out here (they can be automated over time) and sent to the engine only when they change.
  let audioSeq = 1;
  const active = new Map();                  // source id -> playing node
  let ctxRef = null;
  const audioNow = () => now / 1000;
  class AudioParam {
    constructor(v) { this._v = v === undefined ? 1 : v; this._ev = []; }
    __at(t) {
      let v = this._v, t0 = -Infinity;
      const ev = this._ev;
      for (let i = 0; i < ev.length; i++) {
        const e = ev[i];
        if (e.t > t) {
          if (e.type === 'lin') { const k = (t - t0) / (e.t - t0 || 1); return v + (e.v - v) * Math.max(0, Math.min(1, k)); }
          if (e.type === 'exp' && v > 0 && e.v > 0) { const k = (t - t0) / (e.t - t0 || 1); return v * Math.pow(e.v / v, Math.max(0, Math.min(1, k))); }
          break;
        }
        if (e.type === 'target') { const next = ev[i + 1]; const end = next && next.t <= t ? next.t : t; v = e.v + (v - e.v) * Math.exp(-(end - e.t) / Math.max(1e-4, e.tc)); t0 = end; }
        else { v = e.v; t0 = e.t; }
      }
      return v;
    }
    get value() { return this.__at(audioNow()); }
    set value(x) { this._v = +x; this._ev = []; }
    __add(e) { this._ev.push(e); this._ev.sort((a, b) => a.t - b.t); return this; }
    setValueAtTime(v, t) { return this.__add({ type: 'set', v: +v, t: +t }); }
    linearRampToValueAtTime(v, t) { return this.__add({ type: 'lin', v: +v, t: +t }); }
    exponentialRampToValueAtTime(v, t) { return this.__add({ type: 'exp', v: +v, t: +t }); }
    setTargetAtTime(v, t, tc) { return this.__add({ type: 'target', v: +v, t: +t, tc: +tc }); }
    setValueCurveAtTime() { return this; }
    cancelScheduledValues(t) { this._v = this.__at(audioNow()); this._ev = this._ev.filter((e) => e.t < t); return this; }
    cancelAndHoldAtTime(t) { return this.cancelScheduledValues(t); }
  }
  class AudioNode extends EventTarget {
    constructor() { super(); this.__out = new Set(); }
    connect(n) { this.__out.add(n); return n; }
    disconnect(n) { if (n) this.__out.delete(n); else this.__out.clear(); }
  }
  class GainNode extends AudioNode { constructor() { super(); this.gain = new AudioParam(1); } }
  const gainTo = (node, dest, seen) => {            // best gain of any path from node to the destination
    if (node === dest) return 1;
    if (seen.has(node)) return 0;
    seen.add(node);
    let best = 0;
    for (const n of node.__out) { const g2 = gainTo(n, dest, seen); if (g2 > best) best = g2; }
    return node.gain && node instanceof GainNode ? best * node.gain.value : best;
  };
  const sourceGain = (src) => {
    if (src.__ctx) return gainTo(src, src.__ctx.destination, new Set());
    return Math.max(0, Math.min(1, src.muted ? 0 : src.volume));
  };
  const startSource = (src, bid, loop) => {
    const id = audioSeq++;
    src.__sid = id; src.__gain = sourceGain(src); src.__ended = false; src.__polls = 0;
    active.set(id, src);
    __py_audio_cmd('start', id, bid, src.__gain.toFixed(3) + ',' + (loop ? 1 : 0));
  };
  const stopSource = (src, fire) => {
    if (src.__sid === undefined || !active.has(src.__sid)) return;
    __py_audio_cmd('stop', src.__sid, 0, '');
    active.delete(src.__sid); src.__sid = undefined;
    if (fire) { src.__ended = true; src.dispatchEvent(new Event('ended')); }
  };
  // called every frame
  g.__sgba_audio_tick = (t) => {
    for (const [id, src] of active) {
      if (src.__stopAt !== undefined && src.__stopAt <= t / 1000) { src.__stopAt = undefined; stopSource(src, true); continue; }
      const gn = sourceGain(src);
      if (Math.abs(gn - src.__gain) > 0.004) { src.__gain = gn; __py_audio_cmd('gain', id, gn, ''); }
      if (!src.loop && ++src.__polls % 8 === 0 && !__py_audio_busy(id)) { active.delete(id); src.__sid = undefined; src.__ended = true; src.paused = true; src.dispatchEvent(new Event('ended')); }
    }
  };

  class AudioBufferSourceNode extends AudioNode {
    constructor(ctx) { super(); this.__ctx = ctx; this.buffer = null; this.loop = false; this.playbackRate = new AudioParam(1); this.detune = new AudioParam(0); this.onended = null; }
    start(when) {
      if (!this.buffer || this.buffer.__bid < 0 || this.__sid !== undefined) return;
      const delay = (+when || 0) - audioNow();
      const go = () => startSource(this, this.buffer.__bid, this.loop);
      if (delay > 0.01) g.setTimeout(go, delay * 1000); else go();
    }
    stop(when) {
      const delay = (+when || 0) - audioNow();
      if (delay > 0.01) this.__stopAt = (+when); else stopSource(this, true);
    }
  }
  class AudioContext extends EventTarget {
    constructor() { super(); this.state = 'running'; this.sampleRate = 44100; this.destination = new AudioNode(); this.destination.maxChannelCount = 2; this.listener = {}; ctxRef = this; }
    get currentTime() { return audioNow(); }
    resume() { this.state = 'running'; return Promise.resolve(); } suspend() { this.state = 'suspended'; return Promise.resolve(); } close() { this.state = 'closed'; return Promise.resolve(); }
    createGain() { return new GainNode(); }
    createBufferSource() { return new AudioBufferSourceNode(this); }
    createOscillator() { const n = new AudioNode(); n.frequency = new AudioParam(440); n.detune = new AudioParam(0); n.type = 'sine'; n.start = () => {}; n.stop = () => {}; return n; }
    createBiquadFilter() { const n = new AudioNode(); n.frequency = new AudioParam(350); n.Q = new AudioParam(1); n.gain = new AudioParam(0); n.type = 'lowpass'; return n; }
    createDynamicsCompressor() { return new AudioNode(); }
    createStereoPanner() { const n = new AudioNode(); n.pan = new AudioParam(0); return n; }
    createAnalyser() { const n = new AudioNode(); n.getByteFrequencyData = () => {}; n.getFloatTimeDomainData = () => {}; return n; }
    createBuffer(c, l, r) { return { __bid: -1, length: l, sampleRate: r, numberOfChannels: c, duration: l / r, getChannelData: () => new Float32Array(l) }; }
    decodeAudioData(ab, ok, no) {
      const self = this;
      const p = new Promise((res, rej) => {
        const info = ab && ab.__path ? String(__py_audio_register(ab.__path)).split(',') : null;
        if (!info || +info[0] < 0) { rej(new Error('cannot decode audio')); return; }
        const dur = +info[1];
        res({ __bid: +info[0], duration: dur, sampleRate: self.sampleRate, numberOfChannels: 2, length: Math.round(dur * self.sampleRate), getChannelData: () => new Float32Array(0) });
      });
      if (ok || no) p.then((b) => { if (ok) ok(b); }, (e) => { if (no) no(e); });
      return p;
    }
  }
  g.AudioContext = g.webkitAudioContext = g.OfflineAudioContext = AudioContext;
  g.AudioBuffer = function AudioBuffer() {};

  const AUDIO_TYPES = /\.(ogg|oga|mp3|wav|flac|opus)$|audio\/(ogg|mpeg|mp3|wav|x-wav|flac)/i;
  class Audio extends EventTarget {
    constructor(src) {
      super(); this._src = ''; this.volume = 1; this.loop = false; this.paused = true; this.muted = false; this.readyState = 4;
      this.duration = NaN; this.currentTime = 0; this.preload = 'auto'; this.autoplay = false; this.playbackRate = 1; this.ended = false;
      if (src !== undefined) this.src = src;
    }
    get src() { return this._src; }
    set src(v) { this._src = String(v); this._bid = undefined; }
    __bufferId() {
      if (this._bid === undefined) { const info = String(__py_audio_register(this._src)).split(','); this._bid = +info[0]; this.duration = +info[1]; }
      return this._bid;
    }
    play() {
      const bid = this.__bufferId();
      if (bid < 0) return Promise.reject(new Error('NotSupportedError'));
      stopSource(this, false);
      this.paused = false; this.ended = false;
      startSource(this, bid, this.loop);
      return Promise.resolve();
    }
    pause() { this.paused = true; stopSource(this, false); }
    load() {} canPlayType(t) { return AUDIO_TYPES.test(String(t)) ? 'maybe' : ''; }
    cloneNode() { const a = new Audio(this._src); a.volume = this.volume; a.loop = this.loop; return a; }
    get __ctx() { return null; }
  }
  g.Audio = g.HTMLAudioElement = Audio;

  // ---- input dispatch called from Python
  g.__sgba_key = (type, key, code, keyCode) => {
    const ev = new Event(type, { key, code, keyCode, which: keyCode, repeat: false, ctrlKey: false, shiftKey: false, altKey: false, metaKey: false });
    const target = g.document.__focus || g.document.body;
    g.document.dispatchEvent(ev);
  };
  g.__sgba_dispatch = (type, detail) => { g.dispatchEvent(new Event(type, detail || {})); };

  // ---- overlay snapshot for the host: what HTML the game shows over its canvas, and which parts can take focus
  let cssCache = { text: null, rules: [] };
  const cssRules = () => {
    const sheets = querySel(D.documentElement, 'style', false);
    const text = sheets.map((e) => e.textContent).join('\n');
    if (text === cssCache.text) return cssCache.rules;
    const rules = [];
    const src = text.replace(/\/\*[\s\S]*?\*\//g, '');
    let i = 0;
    while (i < src.length) {
      const open = src.indexOf('{', i); if (open < 0) break;
      let depth = 1, j = open + 1;
      while (j < src.length && depth) { if (src[j] === '{') depth++; else if (src[j] === '}') depth--; j++; }
      const head = src.slice(i, open).trim(), body = src.slice(open + 1, j - 1);
      i = j;
      if (head.startsWith('@')) continue;   // @media and friends: none of the queries apply on a handheld
      const decl = {};
      body.split(';').forEach((d) => { const k = d.indexOf(':'); if (k > 0) { const name = d.slice(0, k).trim().toLowerCase(); let val = d.slice(k + 1).trim(); const imp = /!important/i.test(val); val = val.replace(/!important/i, '').trim(); if (name === 'display' || name === 'visibility') decl[name] = { val, imp }; } });
      if (Object.keys(decl).length) rules.push({ sels: head, decl });
    }
    cssCache = { text, rules }; return rules;
  };
  let hideSelectors = [];
  g.__sgba_set_hide = (list) => { hideSelectors = list.slice(); bump(); };
  const displayOf = (el) => {
    if (hideSelectors.length && hideSelectors.some((sel) => (sel.startsWith('text:') ? el.localName === 'button' && el.textContent.trim().startsWith(sel.slice(5)) : matches(el, sel)))) return 'none';   // hidden for this device by sgba_ui.json
    let val = '', imp = false;
    for (const r of cssRules()) { const d = r.decl.display; if (d && (d.imp || !imp) && matches(el, r.sels)) { val = d.val; imp = d.imp; } }
    const inline = el.style.display;
    if (inline && !imp) val = inline;
    if (!val && el.hasAttribute('hidden')) val = 'none';
    return val;
  };
  const hiddenDeep = (el) => { for (let n = el; n && n.nodeType === 1; n = n.parentNode) { if (displayOf(n) === 'none') return true; } return false; };
  const SKIP = new Set(['script', 'style', 'link', 'meta', 'noscript', 'template', 'head']);
  let focusables = [];
  const isFocusable = (el) => !el.disabled && (['button', 'select', 'textarea'].includes(el.localName) || (el.localName === 'a' && el.hasAttribute('href')) ||
    (el.localName === 'input' && el.type !== 'hidden') || (el.hasAttribute('tabindex') && el.tabIndex >= 0) || el.getAttribute('role') === 'button');
  const snap = (el) => {
    if (el.nodeType === 3) { const t = el.data.replace(/\s+/g, ' '); return t === '' ? null : { x: t }; }
    if (el.nodeType !== 1 || SKIP.has(el.localName) || displayOf(el) === 'none' || el.style.visibility === 'hidden') return null;
    const n = { t: el.localName };
    if (el.id) n.id = el.id;
    const cls = el.className; if (cls) n.c = cls;
    if (el.disabled) n.d = 1;
    if (D.activeElement === el) n.on = 1;
    if (isFocusable(el)) { n.f = focusables.length; focusables.push(el); }
    if (el.localName === 'canvas' || el.localName === 'img') { n.cv = el.__id; n.w = el.width; n.h = el.height; }
    const w = el.style.width; if (w && /%$/.test(w)) n.pw = parseFloat(w);
    if (el.localName === 'input') { n.v = el.value; n.ck = el.checked ? 1 : 0; n.it = el.type; }
    const kids = []; for (const c of el.childNodes) { const k = snap(c); if (k) kids.push(k); }
    if (kids.length) n.k = kids;
    return n;
  };
  g.__sgba_ui = () => {
    focusables = [];
    const canvas = D.querySelector('canvas');
    const stage = canvas && canvas.parentNode;
    const roots = [];
    if (stage && !hiddenDeep(stage)) for (const el of stage.childNodes) { if (el === canvas) continue; const n = snap(el); if (n) roots.push(n); }
    const active = focusables.findIndex((e) => e === D.activeElement);
    return JSON.stringify({ v: domVer, roots, active });
  };
  g.__sgba_ui_focus = (i) => { const e = focusables[i]; if (e) e.focus(); };
  g.__sgba_ui_click = (i) => {
    const e = focusables[i]; if (!e || e.disabled) return;
    e.focus();
    for (const t of ['pointerdown', 'mousedown', 'pointerup', 'mouseup']) e.dispatchEvent(new Event(t, { button: 0, detail: 1 }));
    if (e.localName === 'input' && (e.type === 'checkbox' || e.type === 'radio')) { e.checked = !e.checked; e.dispatchEvent(new Event('input')); e.dispatchEvent(new Event('change')); }
    e.dispatchEvent(new Event('click', { button: 0, detail: 1 }));
  };
  g.__sgba_ui_activate_by_id = (id) => { const e = D.getElementById(id); if (e && !e.disabled && !hiddenDeep(e)) { e.click(); return true; } return false; };
  g.__sgba_ui_visible = (id) => { const e = D.getElementById(id); return !!e && !hiddenDeep(e); };
  g.__sgba_ui_back = () => {
    const mk = (type) => new Event(type, { key: 'Escape', code: 'Escape', keyCode: 27, which: 27 });
    const ev = mk('keydown'); D.dispatchEvent(ev); D.dispatchEvent(mk('keyup'));
    if (ev.defaultPrevented) return true;
    const back = focusables.find((e) => /^\s*(back|close|cancel|leave|return|resume|continue|ok|done)\b/i.test(e.textContent));
    if (back) { back.click(); return true; }
    return false;
  };

  // ---- script loading helpers
  g.__sgba_load_scripts_done = false;
})(globalThis);
