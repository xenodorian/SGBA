'use strict';
// Slingshot Orbits: the physics, level rules and level solver shared by the game (index.html) and the level
// tool (tools/slingshot_levels.js). Nothing here touches the page, the input or the screen.

// ---------- constants ----------
// The world is a fixed 640x480 on every screen. Physics constants are the earlier tuned values times K = 2/3.
const WORLD_W = 640, WORLD_H = 480, K = 2 / 3, START_X = 0.36;
let W = WORLD_W, H = WORLD_H;
const G = 1600;         // gravitational constant
const SOFT = 47;        // softening length: smooths close passes so winning launches form wider sweet spots
const VMAX = 320;       // launch speed cap (world px/s)
const SHIP_R = 4;
const GOAL_R = 32;      // portal capture radius, drawn at this size too
const DT = 1 / 120;     // fixed physics timestep, shared by game, predictor and solver
const MAX_STEPS = 1400; // ~11.7 simulated seconds before a shot is declared drifting
const SHOTS = 3;
// Pull curve: launch speed grows with a power of pull distance, so small pulls
// are gentle and the whole stretch of the sling is usable. Holding Shift steepens
// the curve (exponent 2.2) for finer control near full power.
const PULL_EXP = 2.4, PULL_EXP_FINE = 3.2, DEAD_ZONE = 5;
const TAU = Math.PI * 2;
const MAX_PLANETS = 20;
let TOL_MIN = 6;      // a level is kept only if its best launch survives +/-6 world px of pointer error
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

// ---------- canvas-free helpers ----------
// ---------- level generation ----------
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

function wallsFor(P) { return 1; }          // number of blocker rocks placed across the direct line
function minBendsFor(P) { return 1; }

function genLevel(orbit, seed, P, B) {
  const rnd = mulberry32(seed);
  const R = (a, b) => a + (b - a) * rnd();
  const dim = Math.min(W, H);
  const start = { x: W * START_X, y: H * 0.5 };
  const goal = { x: W * 0.91, y: H * R(0.34, 0.66) };
  const rocks = [];
  // blockers: big lumps centred on the straight line from ship to portal
  const ux = goal.x - start.x, uy = goal.y - start.y, len = Math.hypot(ux, uy), nx = -uy / len, ny = ux / len;
  for (let i = 0; i < B; i++) {
    const t = B === 1 ? R(0.42, 0.58) : (i === 0 ? R(0.30, 0.42) : R(0.58, 0.72));
    const off = B === 1 ? R(-0.05, 0.05) * H : (i === 0 ? 1 : -1) * R(0.03, 0.10) * H;
    const sh = rockShape(start.x + ux * t + nx * off, start.y + uy * t + ny * off, rnd, Math.floor(rnd() * 4), R(100, 140), R(0, TAU));
    for (const q of sh) rocks.push(q);
  }
  const planets = [];
  const mk = (x, y, r) => ({ x, y, r, m: r * r * 1.4, hue: R(0, 360), ring: rnd() < 0.35, tilt: R(-0.6, 0.6), spin: R(0.05, 0.2) });
  let up = rnd() < 0.5;
  for (let i = 0; i < Math.min(B, P); i++) {
    for (let t = 0; t < 60; t++) {
      const r = dim * R(0.055, 0.075), x = (B === 1 ? 0.64 : 0.52 + 0.26 * i / (B - 1)) * W + R(-0.02, 0.02) * W;
      const y = H * 0.5 + (up ? -1 : 1) * H * R(0.12, 0.24);
      if (rocks.every(q => Math.hypot(q.x - x, q.y - y) > q.r + r + 10)
        && planets.every(p => Math.hypot(p.x - x, p.y - y) > p.r + r + 24)) { planets.push(mk(x, y, r)); break; }
    }
    up = !up;
  }
  const rMax = dim * Math.max(0.036, 0.072 - 0.0025 * P), rMin = dim * 0.028;
  for (let spacing = 24; planets.length < P && spacing >= 6; spacing -= 6) {
    for (let t = 0; planets.length < P && t < 3000; t++) {
      const r = R(rMin, rMax), x = R(W * 0.22, W * 0.88), y = R(H * 0.08, H * 0.92);
      if (planets.every(p => Math.hypot(p.x - x, p.y - y) > p.r + r + spacing)
        && rocks.every(q => Math.hypot(q.x - x, q.y - y) > q.r + r + 16)
        && Math.hypot(x - start.x, y - start.y) > 87
        && Math.hypot(x - goal.x, y - goal.y) > r + 73)
        planets.push(mk(x, y, r));
    }
  }
  return { orbit, seed, start, goal, planets, rocks, dust: [] };
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

// Counts orbital bends in a recorded flight path. The heading is unwrapped, then a
// zigzag filter with 8 degree hysteresis finds the turning runs. A bend is a run of
// monotone turning of at least 30 degrees.
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
  const HY = 8 * Math.PI / 180, MB = 30 * Math.PI / 180;
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

// Pointer-to-launch mapping, inverted: how far back the player must pull for a given speed.
function pullRange() { return W * START_X - 32; }   // the ship starts 30% in; the pointer must stay on screen
function pullFor(m) { return Math.pow(m / VMAX, 1 / PULL_EXP) * pullRange() + DEAD_ZONE; }
function speedFor(d) { return VMAX * Math.pow(Math.min(1, Math.max(0, d - DEAD_ZONE) / pullRange()), PULL_EXP); }

// A launch is reachable only if the pointer position that produces it is inside the window.
function reachableAim(L, vx, vy) {
  const m = Math.hypot(vx, vy), d = pullFor(m);
  const px = L.start.x - vx / m * d, py = L.start.y - vy / m * d;
  return px >= 20 && px <= W - 20 && py >= 20 && py <= H - 20;
}

// Every reachable launch that wins, over a dAng degree by nSp speed grid.
function sweepWins(L, dAng, nSp) {
  const wins = [];
  for (let k = 0; k < nSp; k++) {
    const sp = VMAX * (0.2 + 0.8 * k / (nSp - 1));
    for (let a = 0; a < 360; a += dAng) {
      const t = a * Math.PI / 180, vx = Math.cos(t) * sp, vy = Math.sin(t) * sp;
      if (!reachableAim(L, vx, vy)) continue;
      const rec = [];
      if (sim(L, vx, vy, rec) === 1) wins.push({ vx, vy, b: countBends(rec) });
    }
  }
  return wins;
}

// Sweet spot size: the largest radius (px) around the pointer position within which every
// sampled pointer position still wins. Bigger means a more forgiving shot.
function toleranceOf(L, vx, vy) {
  const m = Math.hypot(vx, vy), d = pullFor(m);
  const px = L.start.x - vx / m * d, py = L.start.y - vy / m * d;
  let best = 0;
  for (let r = 1; r <= 40; r++) {
    for (let q = 0; q < 16; q++) {
      const ang = q / 16 * TAU, qx = px + Math.cos(ang) * r, qy = py + Math.sin(ang) * r;
      const dx = L.start.x - qx, dy = L.start.y - qy, dd = Math.hypot(dx, dy), mm = speedFor(dd);
      if (sim(L, dx / dd * mm, dy / dd * mm, null) !== 1) return best;
    }
    best = r;
  }
  return best;
}

// Builds the puzzle. Picks the most forgiving route with at least minB bends, then drops big
// ragged rock masses across every shortcut (a winning launch with fewer bends), keeping
// them well clear of that route, until no shortcut is left.
function segHitsRock(L) {
  const ax = L.start.x, ay = L.start.y, bx = L.goal.x, by = L.goal.y, dx = bx - ax, dy = by - ay;
  return L.rocks.some(q => {
    const t = Math.max(0, Math.min(1, ((q.x - ax) * dx + (q.y - ay) * dy) / (dx * dx + dy * dy)));
    return Math.hypot(q.x - (ax + t * dx), q.y - (ay + t * dy)) < q.r + SHIP_R;
  });
}

// Shortcut launches (fewer bends than required) that a player could really hit: margin of 2+ px.
const FIND_TOL = 2;
function findable(L, minB, wins) {
  const sc = wins.filter(w => w.b < minB);
  const st = Math.max(1, Math.floor(sc.length / 30)), out = [];
  for (let i = 0; i < sc.length; i += st) if (toleranceOf(L, sc[i].vx, sc[i].vy) >= FIND_TOL) out.push(sc[i]);
  return out;
}

function solveLevel(L, minB, force) {
  if (!segHitsRock(L)) return null;             // the straight line must be physically blocked
  const rnd = mulberry32((L.seed ^ 0x9e3779b9) >>> 0);
  const wins = sweepWins(L, 2, 10);
  const cands = wins.filter(w => w.b >= minB);
  if (!cands.length) return null;
  const stride = Math.max(1, Math.floor(cands.length / 60));
  let route = null;
  for (let i = 0; i < cands.length; i += stride) {
    const c = cands[i], tol = toleranceOf(L, c.vx, c.vy);
    if (!route || tol > route.tol) route = { vx: c.vx, vy: c.vy, bends: c.b, tol };
  }
  if (route.tol < TOL_MIN - 1 && !force) return null;
  const routeRec = [];
  sim(L, route.vx, route.vy, routeRec);
  const clearance = (x, y) => {
    let m = 1e9;
    for (let i = 0; i < routeRec.length; i += 6) m = Math.min(m, Math.hypot(routeRec[i] - x, routeRec[i + 1] - y));
    return m;
  };
  const blobAt = (cx, cy, ang, scale) => rockShape(cx, cy, rnd, Math.floor(rnd() * 4), 84 * scale, ang);
  const lobeOk = q => q.x > q.r && q.x < W - q.r && q.y > q.r && q.y < H - q.r
    && clearance(q.x, q.y) > q.r + 18
    && L.planets.every(p => Math.hypot(p.x - q.x, p.y - q.y) > p.r + q.r + 13)
    && Math.hypot(q.x - L.start.x, q.y - L.start.y) > q.r + 87
    && Math.hypot(q.x - L.goal.x, q.y - L.goal.y) > GOAL_R + q.r + 20;
  let stuck = false;
  for (let it = 0; it < 26 && !stuck; it++) {
    const sc = findable(L, minB, it === 0 ? wins : sweepWins(L, 2, 10));
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
      for (let i = Math.max(2, Math.floor(n * 0.12)); i < Math.floor(n * 0.95); i += 6) {
        const hx = rec[2 * i + 2] - rec[2 * i - 2], hy = rec[2 * i + 3] - rec[2 * i - 1];
        const ang = Math.atan2(hy, hx) + Math.PI / 2 + (rnd() - 0.5) * 0.8;
        for (const scale of [1.5, 1.15, 0.85]) {
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
  const finalWins = sweepWins(L, 1, 16);
  const shortcuts = findable(L, minB, finalWins).length;
  route.tol = toleranceOf(L, route.vx, route.vy);
  route.shortcuts = shortcuts;
  route.winners = finalWins.length;
  route.strict = shortcuts === 0 && route.tol >= TOL_MIN;
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
  for (let tries = 0; out.length < count && tries < 600; tries++) {
    const x = W * (0.2 + 0.75 * rnd()), y = H * (0.08 + 0.84 * rnd());
    if (routeClear(x, y) < 60) continue;
    if (!L.planets.every(p => Math.hypot(p.x - x, p.y - y) > p.r + 27)) continue;
    if (!L.rocks.every(q => Math.hypot(q.x - x, q.y - y) > q.r + 20)) continue;
    if (Math.hypot(x - L.start.x, y - L.start.y) < 60) continue;
    if (Math.hypot(x - L.goal.x, y - L.goal.y) < GOAL_R + 40) continue;
    if (!out.every(d => Math.hypot(d.x - x, d.y - y) > 93)) continue;
    out.push({ x, y, got: false, ph: rnd() * TAU });
  }
  return out;
}

function trivialSolution(L) {
  const dx = L.goal.x - L.start.x, dy = L.goal.y - L.start.y, dd = Math.hypot(dx, dy) || 1;
  for (let k = 3; k <= 10; k++) {
    const sp = VMAX * k / 10, vx = dx / dd * sp, vy = dy / dd * sp;
    if (sim(L, vx, vy, null) === 1) return { vx, vy, bends: 0, tol: 0, shortcuts: 0, winners: 1, strict: false };
  }
  return { vx: dx / dd * VMAX * 0.5, vy: dy / dd * VMAX * 0.5, bends: 0, tol: 0, shortcuts: 0, winners: 0, strict: false };
}

// Builds the level for orbit n with P0 planets. This is the browser build's search, unchanged: the
// fewest-planet fallbacks, widest-shot-first relaxation and last resort are the same. It runs offline in
// tools/slingshot_levels.js and its results ship in levels.js, so the page never runs this search.
function buildLevel(n, P0) {
  let best = null, tries = 0;
  const attempt = (P, seeds, minB) => {
    const B = wallsFor(P);
    let found = null;
    for (let a = 0; a < seeds && !(found && found.sol.strict); a++) {
      tries++;
      const L = genLevel(n, ((n * 104729) + P * 7907 + a * 7919) >>> 0, P, B);
      if (L.planets.length < P) { if (a >= 2) break; continue; }
      if (!segHitsRock(L) || !sweepWins(L, 3, 8).some(w => w.b >= minB)) continue;   // cheap pre-filter
      const sol = solveLevel(L, minB, false);
      if (!sol) continue;
      const score = (sol.strict ? 5000 : 0) + (sol.shortcuts === 0 ? 1000 : 0) + sol.tol;
      if (!found || score > found.score) found = { L, sol, score, minB, B };
    }
    return found;
  };
  try {
    for (const need of [6, 4, 3]) {              // widest shot first; relax before dropping any planets
      TOL_MIN = need;
      const f = attempt(P0, need === 6 ? 40 : 70, minBendsFor(P0));
      if (f && f.sol.strict) { best = f; break; }
    }
    TOL_MIN = 3;
    for (let P = P0 - 1; P >= 1 && !best; P--) {
      const f = attempt(P, 20, minBendsFor(P));
      if (f && f.sol.strict) best = f;
    }
    for (let P = P0; P >= 1 && !best; P--) {   // relaxed: fewer bends required
      const f = attempt(P, 10, 1);
      if (f) best = f;
    }
    for (let a = 0; !best && a < 8; a++) {      // accept any winning route, however narrow
      tries++;
      const L = genLevel(n, ((n * 104729) + a * 31) >>> 0, Math.min(P0, 4), 1);
      const sol = solveLevel(L, 0, true);
      if (sol) best = { L, sol, score: 0, minB: 0, B: 1 };
    }
  } catch (e) { best = null; }
  if (!best) {                                  // last resort so the level always exists
    const L = genLevel(n, (n * 104729) >>> 0, 0, 0);
    best = { L, sol: trivialSolution(L), minB: 0, B: 0 };
  }
  TOL_MIN = 6;
  best.L.dust = scatterDust(best.L, best.sol);
  return { L: best.L, sol: best.sol, gen: { tries, minB: best.minB, P: best.L.planets.length, B: best.B } };
}
