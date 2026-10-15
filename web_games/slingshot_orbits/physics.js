'use strict';
// Slingshot Orbits: the physics, level rules and level solver shared by the game (index.html) and the level
// tool (tools/slingshot_levels.js). Nothing here touches the page, the input or the screen.

// ---------- constants ----------
// The world is a fixed 640x480 on every screen. Physics constants are the earlier tuned values times K = 2/3.
const WORLD_W = 640, WORLD_H = 480, K = 2 / 3;
let W = WORLD_W, H = WORLD_H;
const G = 1600;         // gravitational constant
const SOFT = 47;        // softening length: smooths close passes so winning launches form wider sweet spots
const VMAX = 320;       // launch speed cap (world px/s)
const SHIP_R = 4;
const GOAL_R = 32;      // portal capture radius, drawn at this size too
const DT = 1 / 120;     // fixed physics timestep, shared by game, predictor and solver
const MAX_STEPS = 1400; // ~11.7 simulated seconds before a shot is declared drifting
const SHOTS = 3;
const TAU = Math.PI * 2;
const MAX_PLANETS = 20;
let TOL_MIN = 3;      // a level is kept only if its route survives +/-TOL_MIN steps of aim (0.25 deg) and power (0.5 percent)
const MAX_FLIGHT = 14;  // seconds before a captured or drifting ship is written off

// ---------- seeded PRNG (mulberry32) ----------
function mulberry32(a) {
  return function () {
    a |= 0; a = a + 0x6D2B79F5 | 0;
    let t = Math.imul(a ^ a >>> 15, 1 | a);
    t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
    return ((t ^ t >>> 14) >>> 0) / 4294967296;
  };
}

// ---------- rocks ----------
// Rocks are large clusters of overlapping circles shaped like rough ovals, rounds, squares and
// rectangles. Each cluster is a separate lump; nothing connects to the screen edge.
function rockShape(cx, cy, rnd, kind, size, ang) {
  const R = (a, b) => a + (b - a) * rnd();
  const lobes = [];
  const add = (lx, ly, r) => {
    const c = Math.cos(ang), s = Math.sin(ang);
    lobes.push({ x: cx + lx * c - ly * s, y: cy + lx * s + ly * c, r, seed: rnd() * 1000 });
  };
  if (kind === 0) {                              // rough round
    const r = size * 0.42;
    add(0, 0, r); add(R(-0.3, 0.3) * r, R(-0.3, 0.3) * r, r * R(0.7, 0.9));
  } else if (kind === 1) {                       // oval: a row of lobes
    const n = 2 + Math.floor(rnd() * 2), r = size * 0.27;
    for (let i = 0; i < n; i++) add((i - (n - 1) / 2) * r * 1.05, R(-0.12, 0.12) * r, r * R(0.88, 1.08));
  } else if (kind === 2) {                       // rough square
    const r = size * 0.27, d = r * 0.85;
    for (const [i, j] of [[-1, -1], [1, -1], [-1, 1], [1, 1], [0, 0]]) add(i * d * R(0.9, 1.1), j * d * R(0.9, 1.1), r * R(0.9, 1.1));
  } else {                                       // rough rectangle: 3 by 2
    const r = size * 0.22, d = r * 0.9;
    for (let i = -1; i <= 1; i++) for (let j = 0; j < 2; j++) add(i * d * 1.15 * R(0.92, 1.08), (j - 0.5) * d * 1.25 * R(0.92, 1.08), r * R(0.92, 1.1));
  }
  return lobes;
}

// ---------- level generation ----------
// The ship and the portal are placed at random, close together on some orbits and far apart on others. The
// planets are laid along a meandering axis from ship to portal, alternating sides, so the winning flight
// has to swing round one planet after another: a snaking line rather than a clean curve.
const EDGE_PAD = 44;
function inDial(x, y) { return x < 125 && y > 355; }     // the power dial is drawn in the bottom-left corner

function planetRadii(P) {
  const dim = Math.min(W, H), hi = dim * Math.max(0.055, 0.11 - 0.003 * P);
  return [hi * 0.64, hi];
}

// Arc-length table of a recorded flight [x0,y0,x1,y1,...]: s[i] is the distance flown to point i.
function arcOf(rec) {
  const n = rec.length / 2, s = new Float64Array(n);
  for (let i = 1; i < n; i++) s[i] = s[i - 1] + Math.hypot(rec[2 * i] - rec[2 * i - 2], rec[2 * i + 1] - rec[2 * i - 1]);
  return s;
}
function pointAt(rec, arc, d) {          // point on the flight d px from the start, with its unit normal
  let lo = 0, hi = arc.length - 1;
  while (hi - lo > 1) { const m = (lo + hi) >> 1; if (arc[m] < d) lo = m; else hi = m; }
  const tx = rec[2 * hi] - rec[2 * lo], ty = rec[2 * hi + 1] - rec[2 * lo + 1], tl = Math.hypot(tx, ty) || 1;
  return { x: rec[2 * hi], y: rec[2 * hi + 1], nx: -ty / tl, ny: tx / tl, i: hi };
}

// The ship and the portal are placed at random, close together on some orbits and far apart on others.
// The layout is grown along a real flight: a launch is chosen first, then each planet is dropped just beside the
// path that launch is flying, on alternating sides, and the flight is re-simulated. Every planet therefore bends
// the path one way and then the next planet bends it back, so the intended route snakes from planet to planet.
// The portal goes where the finished snake ends. Returns { L, design: { vx, vy } } or null if the growth failed.
function genLevel(orbit, seed, P) {
  const rnd = mulberry32(seed);
  const R = (a, b) => a + (b - a) * rnd();
  const bands = [[120, 260], [260, 400], [400, 580]], band = bands[(orbit * 2 + 1) % 3];
  let start = null;
  for (let t = 0; t < 200 && !start; t++) {
    const sx = R(EDGE_PAD, W - EDGE_PAD), sy = R(EDGE_PAD, H - EDGE_PAD);
    if (!inDial(sx, sy)) start = { x: sx, y: sy };
  }
  const L = { orbit, seed, start, goal: { x: -1e5, y: -1e5 }, planets: [], rocks: [], dust: [] };
  const [rLo, rHi] = planetRadii(P);
  const mk = (x, y, r) => ({ x, y, r, m: r * r * 1.4, hue: R(0, 360), ring: rnd() < 0.35, tilt: R(-0.6, 0.6), spin: R(0.05, 0.2) });
  // launch: toward a random spot in the middle of the board, at a modest speed so planets can bend it
  const tx = R(W * 0.25, W * 0.75), ty = R(H * 0.25, H * 0.75), ang = Math.atan2(ty - start.y, tx - start.x);
  const sp = R(110, 200);
  let vx = Math.cos(ang) * sp, vy = Math.sin(ang) * sp;
  let rec = [], arc = null;
  const fly = () => { rec = []; sim(L, vx, vy, rec); arc = arcOf(rec); return arc[arc.length - 1]; };
  fly();
  let budget = 600;
  const gaps = [];
  for (let i = 0; i < P; i++) gaps.push(R(0.85, 1.2) * (2.2 * (rLo + rHi) / 2 + 40));
  const deep = { i: -1, planets: [], st: 0 };
  const grow = (i, st, side, sgn) => {
    if (i > deep.i) { deep.i = i; deep.planets = L.planets.slice(); deep.st = st; }
    if (i === P) return st;
    const need = st + gaps[i] + 70, cands = [];
    for (let t = 0; t < 60; t++) {
      const sd = i === 0 ? side : rnd() < 0.9 ? -side : side;
      const r = R(rLo, rHi), q = pointAt(rec, arc, st), off = r + R(8, 30);
      const x = q.x + q.nx * sd * off, y = q.y + q.ny * sd * off;
      if (x < r + 6 || x > W - r - 6 || y < r + 6 || y > H - r - 6) continue;
      if (Math.hypot(x - start.x, y - start.y) < r + 40) continue;
      if (!L.planets.every(p => Math.hypot(p.x - x, p.y - y) > p.r + r + 12)) continue;
      const planet = mk(x, y, r);
      L.planets.push(planet);
      const len = fly();
      L.planets.pop();
      if (len < need) continue;
      let margin = 1e9;
      for (let j = 0; j < arc.length; j++) if (arc[j] >= st - 20 && arc[j] <= need) margin = Math.min(margin, rec[2 * j], W - rec[2 * j], rec[2 * j + 1], H - rec[2 * j + 1]);
      const a0 = pointAt(rec, arc, Math.max(0, st - 15)), a1 = pointAt(rec, arc, Math.min(arc[arc.length - 1], need));
      const h0 = Math.atan2(-a0.nx, a0.ny), h1 = Math.atan2(-a1.nx, a1.ny);
      let dh = h1 - h0; while (dh > Math.PI) dh -= TAU; while (dh < -Math.PI) dh += TAU;
      const turn = Math.abs(dh);
      if (turn < 0.45) continue;                                   // each planet must bend the path by at least ~26 degrees
      if (sgn !== 0 && dh * sgn > 0) continue;                     // and the bend must reverse the one before: that is the snake
      const far = Math.hypot(a1.x - W / 2, a1.y - H / 2);          // prefer snakes that stay in the middle of the board
      cands.push({ planet, sd, dh, score: Math.min(turn, 2.2) * 40 + Math.min(margin, 80) * 0.5 - far * 0.25 + R(0, 25) });
    }
    fly();
    cands.sort((u, v) => v.score - u.score);
    for (let c = 0; c < Math.min(4, cands.length); c++) {
      if (--budget < 0) return -1;
      L.planets.push(cands[c].planet); fly();
      const e = grow(i + 1, st + gaps[i], cands[c].sd, cands[c].dh > 0 ? 1 : -1);
      if (e >= 0) return e;
      L.planets.pop(); fly();
    }
    return -1;
  };
  let st = grow(0, R(70, 110), rnd() < 0.5 ? 1 : -1, 0);
  if (st < 0) {                                  // the snake could not take every planet: keep the longest one grown
    if (deep.i < Math.min(P, 3)) return null;
    L.planets = deep.planets; st = deep.st;
  }
  fly();
  // the portal sits on the path after the last planet
  const total = arc[arc.length - 1];
  let goal = null, gScore = -1e9;
  for (let d = st - 30; d <= total; d += 6) {
    const q = pointAt(rec, arc, d), gx = q.x, gy = q.y;
    if (gx < GOAL_R + 16 || gx > W - GOAL_R - 16 || gy < GOAL_R + 16 || gy > H - GOAL_R - 16 || inDial(gx, gy)) continue;
    if (!L.planets.every(p => Math.hypot(p.x - gx, p.y - gy) > p.r + GOAL_R + 16)) continue;
    let early = false;                                       // the path must not touch the portal before its end
    for (let j = 0; j < q.i && !early; j++) if (arc[j] < d - 70 && (rec[2 * j] - gx) ** 2 + (rec[2 * j + 1] - gy) ** 2 < (GOAL_R + 6) ** 2) early = true;
    if (early) continue;
    const dist = Math.hypot(gx - start.x, gy - start.y);
    const score = -Math.max(0, band[0] - dist, dist - band[1]) + R(0, 12);
    if (score > gScore) { gScore = score; goal = { x: gx, y: gy }; }
  }
  if (!goal) return null;
  L.goal = goal;
  if (sim(L, vx, vy, null) !== 1) return null;
  // planets beyond the snake are scattered clear of the route; each one must leave a winning launch near the design
  const refit = () => {
    const a0 = Math.atan2(vy, vx), v0 = Math.hypot(vx, vy), opts = [];
    for (let da = -3; da <= 3.001; da += 0.25) for (let dv = -0.08; dv <= 0.0801; dv += 0.01) opts.push([Math.abs(da) / 3 + Math.abs(dv) / 0.08, da, dv]);
    opts.sort((u, w) => u[0] - w[0]);
    for (const [, da, dv] of opts) {
      const t = a0 + da * Math.PI / 180, v = v0 * (1 + dv), nx = Math.cos(t) * v, ny = Math.sin(t) * v;
      if (sim(L, nx, ny, null) === 1) { vx = nx; vy = ny; return true; }
    }
    return false;
  };
  let routeRec = [];
  sim(L, vx, vy, routeRec);
  const routeClear = (x, y) => { let m = 1e9; for (let i = 0; i < routeRec.length; i += 4) m = Math.min(m, Math.hypot(routeRec[i] - x, routeRec[i + 1] - y)); return m; };
  for (let tries = 0; L.planets.length < P && tries < 600; tries++) {
    const r = R(rLo, rHi), x = R(r + 8, W - r - 8), y = R(r + 8, H - r - 8);
    if (Math.hypot(x - start.x, y - start.y) < r + 60 || Math.hypot(x - goal.x, y - goal.y) < r + GOAL_R + 30) continue;
    if (!L.planets.every(p => Math.hypot(p.x - x, p.y - y) > p.r + r + 14)) continue;
    if (routeClear(x, y) < r + (tries > 300 ? 40 : 65)) continue;
    const planet = mk(x, y, r), ovx = vx, ovy = vy;
    L.planets.push(planet);
    if (refit()) { routeRec = []; sim(L, vx, vy, routeRec); } else { L.planets.pop(); vx = ovx; vy = ovy; }
  }
  if (L.planets.length < P) return null;
  return { L, design: { vx, vy } };
}

// ---------- physics: one function drives game, predictor and solver ----------
// Returns 1 if the ship reaches the portal, -1 if it crashes or leaves the field, 0 if it times out.
function sim(L, vx, vy, rec) {
  let x = L.start.x, y = L.start.y;
  for (let s = 0; s < MAX_STEPS; s++) {
    let ax = 0, ay = 0;
    for (const p of L.planets) {
      const dx = p.x - x, dy = p.y - y;
      const d2 = dx * dx + dy * dy + SOFT * SOFT;
      const k = G * p.m / (d2 * Math.sqrt(d2));
      ax += dx * k; ay += dy * k;
    }
    vx += ax * DT; vy += ay * DT;
    x += vx * DT; y += vy * DT;
    if (rec) rec.push(x, y);
    if ((x - L.goal.x) ** 2 + (y - L.goal.y) ** 2 < GOAL_R * GOAL_R) return 1;
    for (const p of L.planets) {
      if ((x - p.x) ** 2 + (y - p.y) ** 2 < (p.r + SHIP_R) ** 2) return -1;
    }
    for (const r of L.rocks) {
      const dx = x - r.x, lim = r.r + SHIP_R;
      if (dx > lim || dx < -lim) continue;
      if (dx * dx + (y - r.y) ** 2 < lim * lim) return -1;
    }
    if (x < SHIP_R || x > W - SHIP_R || y < SHIP_R || y > H - SHIP_R) return -1;   // the edge is lethal
  }
  return 0;
}

// Total turning of a flight in degrees (every swing round a planet adds to it), a second measure of how snaking it is.
function totalTurn(rec) {
  let sum = 0, prev = null;
  for (let i = 8; i < rec.length; i += 8) {
    const a = Math.atan2(rec[i + 1] - rec[i - 7], rec[i] - rec[i - 8]);
    if (prev !== null) { let d = a - prev; while (d > Math.PI) d -= TAU; while (d < -Math.PI) d += TAU; sum += Math.abs(d); }
    prev = a;
  }
  return sum * 180 / Math.PI;
}

// Counts orbital bends in a recorded flight path. The heading is unwrapped, then a
// zigzag filter with 5 degree hysteresis finds the turning runs. A bend is a run of
// monotone turning of at least 14 degrees, so every swing round a planet counts.
function countBends(rec) {
  const step = 8;
  const th = [];
  let prev = null;
  for (let i = step; i < rec.length; i += step) {
    const a = Math.atan2(rec[i + 1] - rec[i + 1 - step], rec[i] - rec[i - step]);
    if (prev === null) th.push(a);
    else {
      let d = a - prev;
      while (d > Math.PI) d -= TAU;
      while (d < -Math.PI) d += TAU;
      th.push(th[th.length - 1] + d);
    }
    prev = a;
  }
  if (th.length < 2) return 0;
  const HY = 5 * Math.PI / 180, MB = 14 * Math.PI / 180;
  const piv = [th[0]];
  let dir = 0, ext = th[0];
  for (let i = 1; i < th.length; i++) {
    const v = th[i];
    if (dir === 0) { if (Math.abs(v - ext) >= HY) { dir = v > ext ? 1 : -1; ext = v; } }
    else if (dir === 1) { if (v > ext) ext = v; else if (ext - v >= HY) { piv.push(ext); dir = -1; ext = v; } }
    else { if (v < ext) ext = v; else if (v - ext >= HY) { piv.push(ext); dir = 1; ext = v; } }
  }
  piv.push(ext);
  let n = 0;
  for (let i = 1; i < piv.length; i++) if (Math.abs(piv[i] - piv[i - 1]) >= MB) n++;
  return n;
}

// ---------- aim and power ----------
// The player aims with an angle and a power dial, so a launch is (angle, power). A launch has margin k when every
// launch within +/- k * ANG_STEP degrees and +/- k * POW_STEP percent of its speed still wins.
const ANG_STEP = 0.25, POW_STEP = 0.5;
function toleranceOf(L, vx, vy, maxK) {
  const ang = Math.atan2(vy, vx), sp = Math.hypot(vx, vy);
  let best = 0;
  for (let k = 1; k <= (maxK || 24); k++) {
    for (let a = -1; a <= 1; a++) for (let b = -1; b <= 1; b++) {
      if (!a && !b) continue;
      const th = ang + a * k * ANG_STEP * Math.PI / 180, v = sp * (1 + b * k * POW_STEP / 100);
      if (sim(L, Math.cos(th) * v, Math.sin(th) * v, null) !== 1) return best;
    }
    best = k;
  }
  return best;
}

// Every launch that wins over a dAng degree by nSp speed grid (speed 20 to 100 percent of VMAX).
function sweepWins(L, dAng, nSp) {
  const wins = [];
  for (let k = 0; k < nSp; k++) {
    const sp = VMAX * (0.2 + 0.8 * k / (nSp - 1));
    for (let a = 0; a < 360; a += dAng) {
      const t = a * Math.PI / 180, vx = Math.cos(t) * sp, vy = Math.sin(t) * sp;
      const rec = [];
      if (sim(L, vx, vy, rec) === 1) wins.push({ vx, vy, b: countBends(rec) });
    }
  }
  return wins;
}

// True if something solid sits on the straight segment from ship to portal.
function segBlocked(L) {
  const ax = L.start.x, ay = L.start.y, bx = L.goal.x, by = L.goal.y, dx = bx - ax, dy = by - ay;
  return L.rocks.concat(L.planets).some(q => {
    const t = Math.max(0, Math.min(1, ((q.x - ax) * dx + (q.y - ay) * dy) / (dx * dx + dy * dy)));
    return Math.hypot(q.x - (ax + t * dx), q.y - (ay + t * dy)) < q.r + SHIP_R;
  });
}

// A big rock lump across the straight line from ship to portal, if nothing solid is there yet and the lump
// can sit well clear of the designed route.
function ensureBlocked(L, design) {
  if (segBlocked(L)) return true;
  const rnd = mulberry32((L.seed ^ 0x2545f491) >>> 0), R = (a, b) => a + (b - a) * rnd();
  const rec = [];
  sim(L, design.vx, design.vy, rec);
  const dx = L.goal.x - L.start.x, dy = L.goal.y - L.start.y, dist = Math.hypot(dx, dy), nx = -dy / dist, ny = dx / dist;
  const clear = (x, y) => { let m = 1e9; for (let i = 0; i < rec.length; i += 4) m = Math.min(m, Math.hypot(rec[i] - x, rec[i + 1] - y)); return m; };
  for (let t = 0; t < 80; t++) {
    const f = R(0.25, 0.75), off = R(-0.04, 0.04) * H, size = Math.min(R(110, 170), dist * 0.6);
    const sh = rockShape(L.start.x + dx * f + nx * off, L.start.y + dy * f + ny * off, rnd, Math.floor(rnd() * 4), size, R(0, TAU));
    const ok = sh.every(q => q.x > q.r + 6 && q.x < W - q.r - 6 && q.y > q.r + 6 && q.y < H - q.r - 6
      && Math.hypot(q.x - L.start.x, q.y - L.start.y) > q.r + 56 && Math.hypot(q.x - L.goal.x, q.y - L.goal.y) > q.r + GOAL_R + 24
      && clear(q.x, q.y) > q.r + 16 && L.planets.every(p => Math.hypot(p.x - q.x, p.y - q.y) > p.r + q.r + 12));
    if (ok) { for (const q of sh) L.rocks.push(q); return segBlocked(L); }
  }
  return false;
}

// Shortcut launches (fewer bends than the intended route asks for) that a player could really hit.
const FIND_TOL = 2;
function findable(L, minB, wins) {
  const sc = wins.filter(w => w.b < minB);
  const st = Math.max(1, Math.floor(sc.length / 30)), out = [];
  for (let i = 0; i < sc.length; i += st) if (toleranceOf(L, sc[i].vx, sc[i].vy, FIND_TOL) >= FIND_TOL) out.push(sc[i]);
  return out;
}

// Builds the puzzle. Picks the squiggliest route with a playable margin, then drops big ragged rock masses across
// every shortcut (a winning launch with clearly fewer bends), keeping them clear of that route, until none is left.
function solveLevel(L, slack, design) {
  const rnd = mulberry32((L.seed ^ 0x9e3779b9) >>> 0);
  const wins = sweepWins(L, 1, 16);
  if (design) {                                  // the designed launch and its neighbours, finely
    const a0 = Math.atan2(design.vy, design.vx), v0 = Math.hypot(design.vx, design.vy);
    for (let da = -1.5; da <= 1.5; da += 0.25) for (let dv = -0.03; dv <= 0.0301; dv += 0.01) {
      const t = a0 + da * Math.PI / 180, v = v0 * (1 + dv), vx = Math.cos(t) * v, vy = Math.sin(t) * v, rec = [];
      if (sim(L, vx, vy, rec) === 1) wins.push({ vx, vy, b: countBends(rec) });
    }
  }
  if (!wins.length) return null;
  const bmax = wins.reduce((m, w) => Math.max(m, w.b), 0);
  // best route: most bends first, then margin; test candidates from the squiggliest down
  const order = wins.slice().sort((p, q) => q.b - p.b);
  let route = null, tested = 0;
  for (let i = 0; i < order.length && tested < 80; i += Math.max(1, Math.floor(order.length / 400))) {
    const c = order[i];
    if (route && c.b < route.bends - 1) break;
    tested++;
    const tol = toleranceOf(L, c.vx, c.vy);
    if (tol < TOL_MIN) continue;
    if (!route || c.b > route.bends || (c.b === route.bends && tol > route.tol)) route = { vx: c.vx, vy: c.vy, bends: c.b, tol };
  }
  if (!route) return { fail: true, bmax };
  const minB = Math.max(1, route.bends - slack);
  const routeRec = [];
  sim(L, route.vx, route.vy, routeRec);
  const clearance = (x, y) => {
    let m = 1e9;
    for (let i = 0; i < routeRec.length; i += 6) m = Math.min(m, Math.hypot(routeRec[i] - x, routeRec[i + 1] - y));
    return m;
  };
  const blobAt = (cx, cy, ang, scale) => rockShape(cx, cy, rnd, Math.floor(rnd() * 4), 110 * scale, ang);
  const lobeOk = q => q.x > q.r + 4 && q.x < W - q.r - 4 && q.y > q.r + 4 && q.y < H - q.r - 4
    && clearance(q.x, q.y) > q.r + 16
    && L.planets.every(p => Math.hypot(p.x - q.x, p.y - q.y) > p.r + q.r + 12)
    && Math.hypot(q.x - L.start.x, q.y - L.start.y) > q.r + 50
    && Math.hypot(q.x - L.goal.x, q.y - L.goal.y) > GOAL_R + q.r + 20;
  let stuck = false;
  for (let it = 0; it < 30 && !stuck; it++) {
    const sc = findable(L, minB, it === 0 ? wins : sweepWins(L, 1, 16));
    if (!sc.length) break;
    const st = Math.max(1, Math.floor(sc.length / 40));
    const paths = [];
    for (let i = 0; i < sc.length; i += st) {
      const rec = [];
      sim(L, sc[i].vx, sc[i].vy, rec);
      const pts = [];
      for (let j = 0; j < rec.length; j += 12) pts.push(rec[j], rec[j + 1]);
      paths.push({ rec, pts });
    }
    const hits = lobes => {
      let h = 0;
      for (const p of paths) {
        let hit = false;
        for (let j = 0; j < p.pts.length && !hit; j += 2)
          for (const q of lobes) if ((p.pts[j] - q.x) ** 2 + (p.pts[j + 1] - q.y) ** 2 < (q.r + SHIP_R) ** 2) { hit = true; break; }
        if (hit) h++;
      }
      return h;
    };
    let bestBlob = null, bestScore = -1;
    const nPick = Math.min(10, paths.length);
    for (let k = 0; k < nPick; k++) {
      const rec = paths[Math.floor(k * paths.length / nPick)].rec, n = rec.length / 2;
      for (let i = Math.max(2, Math.floor(n * 0.08)); i < Math.floor(n * 0.97); i += 6) {
        const hx = rec[2 * i + 2] - rec[2 * i - 2], hy = rec[2 * i + 3] - rec[2 * i - 1];
        const ang = Math.atan2(hy, hx) + Math.PI / 2 + (rnd() - 0.5) * 0.8;
        for (const scale of [1.4, 1.1, 0.8, 0.6]) {
          const lobes = blobAt(rec[2 * i], rec[2 * i + 1], ang, scale);
          if (!lobes.every(lobeOk)) continue;
          const score = hits(lobes) * 100 + scale * 10;
          if (score > bestScore) { bestScore = score; bestBlob = lobes; }
          break;
        }
      }
    }
    if (bestBlob && bestScore >= 100) for (const q of bestBlob) L.rocks.push(q); else stuck = true;
  }
  const finalWins = sweepWins(L, 0.5, 24);
  const shortcuts = findable(L, minB, finalWins).length;
  route.tol = toleranceOf(L, route.vx, route.vy);
  const rec2 = [];
  const res = sim(L, route.vx, route.vy, rec2);
  route.bends = countBends(rec2);
  route.turn = Math.round(totalTurn(rec2));
  route.shortcuts = shortcuts;
  route.winners = finalWins.length;
  route.strict = res === 1 && shortcuts === 0 && route.tol >= TOL_MIN;
  route.bmax = bmax;
  return route;
}

// 2 to 4 stardust per level, scattered in open space and kept well clear of the intended
// route so they do not point at the answer.
function scatterDust(L, sol) {
  const rnd = mulberry32((L.seed ^ 0x51ed270b) >>> 0);
  const count = 2 + Math.floor(rnd() * 3);
  const rec = [];
  sim(L, sol.vx, sol.vy, rec);
  const routeClear = (x, y) => {
    let m = 1e9;
    for (let i = 0; i < rec.length; i += 4) m = Math.min(m, Math.hypot(rec[i] - x, rec[i + 1] - y));
    return m;
  };
  const out = [];
  for (let tries = 0; out.length < count && tries < 900; tries++) {
    const x = W * (0.05 + 0.9 * rnd()), y = H * (0.06 + 0.88 * rnd());
    if (routeClear(x, y) < (tries > 500 ? 36 : 56)) continue;
    if (!L.planets.every(p => Math.hypot(p.x - x, p.y - y) > p.r + 27)) continue;
    if (!L.rocks.every(q => Math.hypot(q.x - x, q.y - y) > q.r + 20)) continue;
    if (Math.hypot(x - L.start.x, y - L.start.y) < 60) continue;
    if (Math.hypot(x - L.goal.x, y - L.goal.y) < GOAL_R + 40) continue;
    if (inDial(x, y)) continue;
    if (!out.every(d => Math.hypot(d.x - x, d.y - y) > 93)) continue;
    out.push({ x, y, got: false, ph: rnd() * TAU });
  }
  return out;
}

// Builds orbit n's level with P planets: tries many layouts and keeps the strict one with the most bends in its
// correct route. It runs offline in tools/slingshot_levels.js and its results ship in levels.js, so the page never
// runs this search.
function buildLevel(n, P, opts) {
  const o = Object.assign({ seeds: 45, slack: 0, tolMin: 3 }, opts || {});
  TOL_MIN = o.tolMin;
  let best = null, tries = 0;
  try {
    for (let a = 0; a < o.seeds; a++) {
      tries++;
      const g = genLevel(n, ((n * 104729) + P * 7907 + a * 7919) >>> 0, P);
      if (!g) continue;
      const L = g.L;
      const drec = [];
      sim(L, g.design.vx, g.design.vy, drec);
      if (best && best.sol.strict && countBends(drec) < best.sol.bends) continue;   // cannot beat the best snake so far
      if (!ensureBlocked(L, g.design)) continue;
      const sol = solveLevel(L, o.slack, g.design);
      if (!sol || sol.fail) continue;
      const score = (sol.strict ? 10000 : 0) + sol.bends * 100 + Math.min(sol.turn / 10, 60) + Math.min(sol.tol, 20);
      if (!best || score > best.score) best = { L, sol, score };
    }
  } finally { TOL_MIN = 6; }
  if (!best) return null;
  best.L.dust = scatterDust(best.L, best.sol);
  return { L: best.L, sol: best.sol, gen: { tries, P: best.L.planets.length } };
}
