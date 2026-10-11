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

  // ---- DOM stub: tolerant elements that remember text/children but draw nothing
  const makeStyle = () => new Proxy({ setProperty(k, v) { this[k] = v; }, removeProperty(k) { delete this[k]; }, getPropertyValue(k) { return this[k] || ''; } },
    { get: (t, k) => (k in t ? t[k] : ''), set: (t, k, v) => { t[k] = v; return true; } });
  class Element extends EventTarget {
    constructor(tag) {
      super(); this.tagName = String(tag).toUpperCase(); this.nodeName = this.tagName; this.nodeType = 1;
      this.children = []; this.childNodes = this.children; this.parentNode = null; this.style = makeStyle();
      this.dataset = {}; this.__attrs = {}; this.__cls = new Set(); this._text = ''; this.hidden = false; this.id = '';
      this.value = ''; this.checked = false; this.disabled = false; this.className_ = '';
      const self = this;
      this.classList = { add(...c) { c.forEach((x) => self.__cls.add(x)); }, remove(...c) { c.forEach((x) => self.__cls.delete(x)); },
        toggle(c, f) { const on = f === undefined ? !self.__cls.has(c) : !!f; on ? self.__cls.add(c) : self.__cls.delete(c); return on; },
        contains(c) { return self.__cls.has(c); } };
    }
    __parent() { return this.parentNode || (this.__detached ? null : g.document); }
    get className() { return [...this.__cls].join(' '); }
    set className(v) { this.__cls = new Set(String(v).split(/\s+/).filter(Boolean)); }
    get textContent() { return this._text + this.children.map((c) => c.textContent || '').join(''); }
    set textContent(v) { this._text = String(v); this.children.length = 0; }
    get innerText() { return this.textContent; } set innerText(v) { this.textContent = v; }
    get innerHTML() { return this._html || ''; }
    set innerHTML(v) { this._html = String(v); this._text = String(v).replace(/<[^>]*>/g, ''); this.children.length = 0; }
    get firstChild() { return this.children[0] || null; } get lastChild() { return this.children[this.children.length - 1] || null; }
    get nextSibling() { return null; } get previousSibling() { return null; }
    get parentElement() { return this.parentNode; }
    get offsetWidth() { return this.width || 640; } get offsetHeight() { return this.height || 480; }
    get clientWidth() { return this.width || 640; } get clientHeight() { return this.height || 480; }
    appendChild(c) { if (c.parentNode) c.parentNode.removeChild(c); c.parentNode = this; this.children.push(c); return c; }
    append(...cs) { cs.forEach((c) => (typeof c === 'string' ? this.appendChild(__text(c)) : this.appendChild(c))); }
    prepend(c) { this.appendChild(c); }
    insertBefore(c) { return this.appendChild(c); }
    removeChild(c) { const i = this.children.indexOf(c); if (i >= 0) this.children.splice(i, 1); c.parentNode = null; return c; }
    remove() { if (this.parentNode) this.parentNode.removeChild(this); }
    replaceChildren(...cs) { this.children.length = 0; this._text = ''; cs.forEach((c) => this.appendChild(c)); }
    contains(c) { while (c) { if (c === this) return true; c = c.parentNode; } return false; }
    setAttribute(k, v) { this.__attrs[k] = String(v); if (k === 'id') this.id = String(v); if (k === 'class') this.className = v; }
    getAttribute(k) { return k in this.__attrs ? this.__attrs[k] : null; }
    removeAttribute(k) { delete this.__attrs[k]; }
    hasAttribute(k) { return k in this.__attrs; }
    querySelector() { return null; } querySelectorAll() { return []; }
    getElementsByTagName() { return []; } getElementsByClassName() { return []; }
    closest() { return null; } matches() { return false; }
    focus() {} blur() {} click() { this.dispatchEvent(new Event('click')); }
    scrollIntoView() {} scrollTo() {} setPointerCapture() {} releasePointerCapture() {}
    cloneNode() { return new Element(this.tagName); }
    getBoundingClientRect() { return { left: 0, top: 0, right: this.width || 640, bottom: this.height || 480, width: this.width || 640, height: this.height || 480, x: 0, y: 0 }; }
    requestFullscreen() { return Promise.resolve(); }
  }
  const __text = (s) => { const e = new Element('#text'); e.nodeType = 3; e._text = String(s); return e; };
  g.Element = g.HTMLElement = Element;

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
  const byId = new Map();
  class Document extends EventTarget {
    constructor() { super(); this.nodeType = 9; this.readyState = 'complete'; this.hidden = false; this.visibilityState = 'visible'; this.fullscreenElement = null; this.cookie = ''; this.title = ''; this.referrer = ''; this.activeElement = null; }
    __parent() { return g; }
    __register(id, el) { byId.set(id, el); el.id = id; }
    getElementById(id) {
      if (!byId.has(id)) { const e = new Element('div'); e.id = id; e.__detached = false; byId.set(id, e); }
      return byId.get(id);
    }
    createElement(tag) { tag = String(tag).toLowerCase(); if (tag === 'canvas') return new Canvas(); if (tag === 'img') return new Image(); const e = new Element(tag); e.__detached = true; return e; }
    createElementNS(ns, tag) { return this.createElement(tag); }
    createTextNode(s) { return __text(s); }
    createDocumentFragment() { return new Element('#fragment'); }
    querySelector(sel) { if (sel && sel[0] === '#') return this.getElementById(sel.slice(1)); return null; }
    querySelectorAll() { return []; }
    getElementsByTagName() { return []; } getElementsByClassName() { return []; }
    exitFullscreen() { return Promise.resolve(); }
    hasFocus() { return true; }
  }
  g.document = new Document();
  g.document.body = new Element('body'); g.document.documentElement = new Element('html'); g.document.head = new Element('head');
  g.document.body.__detached = false;
  g.__registerElement = (id, tag, w, h) => {
    const el = tag === 'canvas' ? new Canvas() : new Element(tag); el.__detached = false; g.document.__register(id, el);
    if (tag === 'canvas') { if (w) el.width = w; if (h) el.height = h; }
    return el;
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

  // ---- fetch / XHR for local files
  g.fetch = (url) => {
    const r = __py_read_text(String(url));
    if (r === null || r === undefined) return Promise.resolve({ ok: false, status: 404, statusText: 'Not Found', text: () => Promise.resolve(''), json: () => Promise.reject(new Error('404')), arrayBuffer: () => Promise.resolve(new ArrayBuffer(0)) });
    return Promise.resolve({ ok: true, status: 200, statusText: 'OK', url, headers: { get: () => null },
      text: () => Promise.resolve(r), json: () => Promise.resolve().then(() => JSON.parse(r)), arrayBuffer: () => Promise.resolve(new ArrayBuffer(0)), blob: () => Promise.resolve({}) });
  };
  g.XMLHttpRequest = class XMLHttpRequest extends EventTarget {
    open(m, u) { this._u = u; this.readyState = 1; }
    setRequestHeader() {}
    send() { const r = __py_read_text(String(this._u)); const self = this; g.setTimeout(() => {
      self.readyState = 4; if (r === null || r === undefined) { self.status = 404; self.responseText = ''; } else { self.status = 200; self.responseText = r; self.response = r; }
      self.dispatchEvent(new Event('readystatechange')); self.dispatchEvent(new Event(self.status === 200 ? 'load' : 'error')); }, 0); }
  };
  g.URL = g.URL || class URL { constructor(u, b) { this.href = String(u); } };
  g.URL.createObjectURL = () => ''; g.URL.revokeObjectURL = () => {};
  g.Blob = g.Blob || class Blob {};
  g.FileReader = g.FileReader || class FileReader {};
  g.TextEncoder = g.TextEncoder || class TextEncoder { encode(s) { const a = []; for (let i = 0; i < s.length; i++) a.push(s.charCodeAt(i) & 255); return new Uint8Array(a); } };

  // ---- audio (silent stubs for now; calls are forwarded so Python can play them)
  class Audio extends EventTarget {
    constructor(src) { super(); this.src = src || ''; this.volume = 1; this.loop = false; this.paused = true; this.currentTime = 0; this.muted = false; this.readyState = 4; this.duration = 0; }
    play() { this.paused = false; __py_audio('play', this.src, this.volume, this.loop); return Promise.resolve(); }
    pause() { this.paused = true; __py_audio('stop', this.src, 0, false); }
    load() {} cloneNode() { const a = new Audio(this.src); a.volume = this.volume; return a; }
    canPlayType() { return 'maybe'; }
  }
  g.Audio = g.HTMLAudioElement = Audio;
  class AudioParam { constructor(v) { this.value = v === undefined ? 1 : v; } setValueAtTime() { return this; } linearRampToValueAtTime() { return this; } exponentialRampToValueAtTime() { return this; } setTargetAtTime() { return this; } cancelScheduledValues() { return this; } }
  class AudioNode extends EventTarget { connect(n) { return n; } disconnect() {} }
  class AudioContext extends EventTarget {
    constructor() { super(); this.state = 'running'; this.currentTime = 0; this.sampleRate = 44100; this.destination = new AudioNode(); }
    resume() { return Promise.resolve(); } suspend() { return Promise.resolve(); } close() { return Promise.resolve(); }
    createGain() { const n = new AudioNode(); n.gain = new AudioParam(1); return n; }
    createOscillator() { const n = new AudioNode(); n.frequency = new AudioParam(440); n.detune = new AudioParam(0); n.type = 'sine'; n.start = () => {}; n.stop = () => {}; return n; }
    createBufferSource() { const n = new AudioNode(); n.start = () => {}; n.stop = () => {}; n.playbackRate = new AudioParam(1); n.buffer = null; return n; }
    createBiquadFilter() { const n = new AudioNode(); n.frequency = new AudioParam(350); n.Q = new AudioParam(1); n.gain = new AudioParam(0); n.type = 'lowpass'; return n; }
    createDynamicsCompressor() { return new AudioNode(); } createBuffer(c, l, r) { return { length: l, sampleRate: r, numberOfChannels: c, getChannelData: () => new Float32Array(l) }; }
    createStereoPanner() { const n = new AudioNode(); n.pan = new AudioParam(0); return n; } createAnalyser() { const n = new AudioNode(); n.getByteFrequencyData = () => {}; return n; }
    decodeAudioData() { return Promise.reject(new Error('not supported')); }
  }
  g.AudioContext = g.webkitAudioContext = AudioContext;

  // ---- input dispatch called from Python
  g.__sgba_key = (type, key, code, keyCode) => {
    const ev = new Event(type, { key, code, keyCode, which: keyCode, repeat: false, ctrlKey: false, shiftKey: false, altKey: false, metaKey: false });
    const target = g.document.__focus || g.document.body;
    g.document.dispatchEvent(ev);
  };
  g.__sgba_dispatch = (type, detail) => { g.dispatchEvent(new Event(type, detail || {})); };

  // ---- script loading helpers
  g.__sgba_load_scripts_done = false;
})(globalThis);
