// Floating Sphere widget for Scriptable (iOS): the desktop sphere on the home or lock screen.
//
// Scriptable keeps only a small loader (below) that fetches this file from sphere-web on
// every run, so edits here reach the phone without touching it. The loader passes BASE,
// the server address. The drawing is a port of floatingsphere.Sphere.draw: keep them in step.
//
// Loader, pasted as a new script in Scriptable (Add Widget → Scriptable → Script):
//
//   const BASE = "https://192.168.255.6:8766";
//   const fm = FileManager.local(), path = fm.joinPath(fm.documentsDirectory(), "sphere-widget.js");
//   let code = null;
//   try {
//     const r = new Request(BASE + "/widget.js"); r.timeoutInterval = 10;
//     const text = await r.loadString();
//     if (r.response.statusCode === 200) { code = text; fm.writeString(path, code); }
//   } catch (_) {}
//   if (!code && fm.fileExists(path)) code = fm.readString(path);
//   if (!code) throw new Error("sphere-web is unreachable at " + BASE);
//   await new (Object.getPrototypeOf(async function () {}).constructor)("BASE", code)(BASE);

const STALE_S = 40 * 60;  // as floatingsphere.STALE_S: older data is drawn dimmed
const WEEK_S = 7 * 24 * 3600, FIVE_H_S = 5 * 3600;
const REFRESH_S = 5 * 60;  // asked of iOS, which decides (often 15 min or more)
const GREEN = [0.20, 0.83, 0.60], PURPLE = [0.66, 0.33, 0.97], RED = [0.93, 0.16, 0.24];
const RING_OK = [0.98, 0.75, 0.25], RING_HOT = [0.97, 0.36, 0.36];
const STATUS = {busy: [0.659, 0.329, 0.969], waiting: [0.851, 0.349, 0.149], idle: [0.122, 0.659, 0.455]};
const ORDER = {waiting: 0, busy: 1, idle: 2};

const rgb = (c, a = 1) => new Color("#" + c.map(v => Math.round(v * 255).toString(16).padStart(2, "0")).join(""), a);
const clamp01 = v => Math.max(0, Math.min(1, v));

// The last good answer is kept, so off the home network the widget shows it dimmed.
async function load() {
  const fm = FileManager.local(), path = fm.joinPath(fm.cacheDirectory(), "sphere-widget.json");
  try {
    const r = new Request(BASE + "/api/widget");
    r.timeoutInterval = 10;
    const d = await r.loadJSON();
    if (d && d.now) {
      fm.writeString(path, JSON.stringify(d));
      return {d, offline: false};
    }
  } catch (_) {}
  return {d: fm.fileExists(path) ? JSON.parse(fm.readString(path)) : null, offline: true};
}

function view(d) {
  const now = Date.now() / 1000;
  const u = (d && d.usage) || {};
  const sessions = (d && d.sessions) || [];
  let five = u.five_pct || 0;
  if (u.five_reset && u.five_reset < now) five = 0;  // the window has reset since the snapshot
  const waiting = sessions.filter(s => s.status === "waiting").length;
  const working = sessions.filter(s => s.status !== "idle").length;
  return {
    now, u, q: d && d.quota, sessions, five, waiting, working,
    stale: !d || !u.cached_at || now - u.cached_at > STALE_S,
    color: waiting ? RED : working ? PURPLE : GREEN,
  };
}

// Sphere.draw at S points (the desktop one is 75 px; everything scales by k).
function sphere(v, S) {
  const k = S / 75, RING = 5 * k;
  const ctx = new DrawContext();
  ctx.size = new Size(S, S);
  ctx.opaque = false;
  ctx.respectScreenScale = true;
  const cx = S / 2, cy = S / 2;
  const R = S / 2 - RING / 2 - k, r = R - RING / 2 - 3 * k;
  const {u, q, now, stale} = v;

  const stroke = (pts, color, width) => {
    const p = new Path();
    p.addLines(pts);
    ctx.addPath(p);
    ctx.setStrokeColor(color);
    ctx.setLineWidth(width);
    ctx.strokePath();
  };
  const dot = (x, y, d, color) => {
    ctx.setFillColor(color);
    ctx.fillEllipse(new Rect(x - d / 2, y - d / 2, d, d));
  };
  const at = p => -Math.PI / 2 + 2 * Math.PI * clamp01(p / 100);
  const arcPts = (a0, a1) => {
    const n = Math.max(2, Math.ceil(Math.abs(a1 - a0) / 0.04));
    return Array.from({length: n + 1}, (_, i) => {
      const a = a0 + (a1 - a0) * i / n;
      return new Point(cx + R * Math.cos(a), cy + R * Math.sin(a));
    });
  };
  // DrawContext has no round line caps: the ends get a dot each instead.
  const arc = (p0, p1, color, caps) => {
    if (p1 <= p0) return;
    const a0 = at(p0), a1 = at(p1);
    stroke(arcPts(a0, a1), color, RING);
    if (caps) for (const a of [a0, a1]) dot(cx + R * Math.cos(a), cy + R * Math.sin(a), RING, color);
  };

  // 7d ring: track, used arc, week-elapsed tick
  stroke(arcPts(0, 2 * Math.PI), rgb([1, 1, 1], 0.13), RING);
  const seven = u.seven_pct;
  if (seven != null) {
    // today's quota still to spend: a faint stretch from 7d up to today's ceiling
    if (q && q.ceiling > seven) arc(seven, q.ceiling, rgb(RING_OK, 0.3), false);
    const over = q && seven > q.ceiling ? q.ceiling : seven;
    arc(0, over, rgb(seven >= 90 ? RING_HOT : RING_OK, stale ? 0.45 : 1), true);
    if (over < seven) arc(over, seven, rgb(RING_HOT, stale ? 0.45 : 1), true);  // spent past today's quota
  }
  if (u.seven_reset) {
    const elapsed = 1 - (u.seven_reset - now) / WEEK_S;
    if (elapsed >= 0 && elapsed <= 1) {
      const a = at(elapsed * 100), r0 = R - RING / 2 - k, r1 = R + RING / 2 + k;
      stroke([new Point(cx + r0 * Math.cos(a), cy + r0 * Math.sin(a)),
              new Point(cx + r1 * Math.cos(a), cy + r1 * Math.sin(a))], rgb([1, 1, 1], 0.9), 1.5 * k);
    }
  }

  // glass ball
  dot(cx, cy, 2 * r, rgb([0.08, 0.09, 0.13], 0.82));

  // water: two phase-shifted waves, frozen. There's no clipping, so each layer is the
  // part of the disc under its wave line.
  const level = clamp01(v.five / 100);
  if (level > 0) {
    const top = cy + r - 2 * r * level, amp = level > 0.01 && level < 0.99 ? 1.5 * k : 0;
    [[0.45, 0.0], [0.9, 2.0]].forEach(([alpha, phase], layer) => {
      const upper = [], lower = [];
      for (let x = cx - r; x <= cx + r + 0.01; x += 1.5 * k) {
        const h = Math.sqrt(Math.max(0, r * r - (x - cx) ** 2));
        const y = top + amp * Math.sin((x - cx) / r * 2.2 * Math.PI + phase) + layer * k;
        upper.push(new Point(x, Math.max(cy - h, Math.min(cy + h, y))));
        lower.unshift(new Point(x, cy + h));
      }
      const p = new Path();
      p.addLines(upper.concat(lower));
      p.closeSubpath();
      ctx.addPath(p);
      ctx.setFillColor(rgb(v.color, alpha * (stale ? 0.55 : 1)));
      ctx.fillPath();
    });
  }

  // 5h pace: short ticks on both rims at the share of the window already elapsed
  if (u.five_reset && u.five_reset > now) {
    const elapsed = 1 - (u.five_reset - now) / FIVE_H_S;
    if (elapsed >= 0 && elapsed <= 1) {
      const y = cy + r - 2 * r * elapsed, half = Math.sqrt(Math.max(0, r * r - (y - cy) ** 2));
      for (const [x0, x1] of [[cx - half, cx - half + 5 * k], [cx + half, cx + half - 5 * k]])
        stroke([new Point(x0, y), new Point(x1, y)], rgb([1, 1, 1], 0.85), 1.5 * k);
    }
  }
  ctx.setStrokeColor(rgb([1, 1, 1], 0.25));
  ctx.setLineWidth(k);
  ctx.strokeEllipse(new Rect(cx - r, cy - r, 2 * r, 2 * r));

  // session count
  const fs = r * 0.95;
  ctx.setFont(Font.boldSystemFont(fs));
  ctx.setTextAlignedCenter();
  const box = dy => new Rect(dy, cy - fs * 0.6 + dy, S, fs * 1.3);
  ctx.setTextColor(rgb([0, 0, 0], 0.45));
  ctx.drawTextInRect(String(v.sessions.length), box(k));
  ctx.setTextColor(rgb([1, 1, 1], 0.96));
  ctx.drawTextInRect(String(v.sessions.length), box(0));
  return ctx.getImage();
}

const INK = new Color("#edf0f5"), INK_3 = new Color("#80858f"), AMBER = new Color("#fabf40");

function text(stack, s, size, color, bold = false) {
  const t = stack.addText(s);
  t.font = bold ? Font.boldSystemFont(size) : Font.systemFont(size);
  t.textColor = color;
  t.lineLimit = 1;
  return t;
}

function usageLine(v) {
  const p = x => x == null ? "–" : Math.round(x) + "%";
  return `5h ${p(v.u.five_pct == null ? null : v.five)} · 7d ${p(v.u.seven_pct)}`;
}

function sessionRows(stack, v, max) {
  const list = v.sessions.slice().sort((a, b) => (ORDER[a.status] ?? 2) - (ORDER[b.status] ?? 2));
  if (!list.length) text(stack, "No sessions", 12, INK_3);
  for (const s of list.slice(0, max)) {
    const row = stack.addStack();
    row.centerAlignContent();
    const d = row.addText("●");
    d.font = Font.systemFont(9);
    d.textColor = rgb(STATUS[s.status] || STATUS.idle);
    row.addSpacer(5);
    text(row, s.name, 12, INK);
    stack.addSpacer(2);
  }
  if (list.length > max) text(stack, `+${list.length - max} more`, 11, INK_3);
}

function build(v, offline) {
  const fam = config.widgetFamily || "medium";
  const w = new ListWidget();
  // iOS can't open the home-screen app from a widget, and the browser wasn't wanted, so a
  // tap only runs this script in Scriptable, which closes itself again (below).
  w.url = `scriptable:///run/${encodeURIComponent(Script.name())}?tap=1`;
  w.refreshAfterDate = new Date(Date.now() + REFRESH_S * 1000);
  const note = offline ? "offline" : v.stale ? "stale" : null;

  if (fam === "accessoryCircular") {
    w.addImage(sphere(v, 64)).centerAlignImage();
    return w;
  }
  if (fam === "accessoryInline") {
    const extra = v.waiting ? `${v.waiting} waiting` : v.working ? `${v.working} busy` : "idle";
    w.addText(`${v.sessions.length} · ${extra} · ${usageLine(v)}`);
    return w;
  }
  if (fam === "accessoryRectangular") {
    const row = w.addStack();
    row.centerAlignContent();
    row.addImage(sphere(v, 50)).imageSize = new Size(50, 50);
    row.addSpacer(6);
    const col = row.addStack();
    col.layoutVertically();
    const extra = v.waiting ? `${v.waiting} waiting` : v.working ? `${v.working} busy` : "all idle";
    text(col, `${v.sessions.length} sessions`, 13, Color.white(), true);
    text(col, extra, 12, Color.white());
    text(col, usageLine(v), 12, Color.white());
    return w;
  }

  const bg = new LinearGradient();
  bg.colors = [new Color("#1a1c26"), new Color("#0d0e13")];
  bg.locations = [0, 1];
  w.backgroundGradient = bg;

  if (fam === "small") {
    w.setPadding(10, 10, 10, 10);
    const img = w.addImage(sphere(v, 110));
    img.centerAlignImage();
    img.imageSize = new Size(110, 110);
    w.addSpacer(4);
    const line = text(w, note || usageLine(v), 12, note ? INK_3 : AMBER, true);
    line.centerAlignText();
    return w;
  }

  // medium and large: sphere on the left, usage and the sessions by status on the right
  w.setPadding(12, 14, 12, 14);
  const row = w.addStack();
  row.centerAlignContent();
  const S = fam === "medium" ? 120 : 140;
  row.addImage(sphere(v, S)).imageSize = new Size(S, S);
  row.addSpacer(14);
  const col = row.addStack();
  col.layoutVertically();
  text(col, usageLine(v), 14, AMBER, true);
  if (v.q && v.q.ceiling != null) text(col, `today up to ${Math.round(v.q.ceiling)}%`, 11, INK_3);
  col.addSpacer(6);
  sessionRows(col, v, fam === "medium" ? 4 : 12);
  if (note) {
    col.addSpacer(4);
    text(col, note, 10, INK_3);
  }
  if (fam !== "medium") w.addSpacer();
  return w;
}

const {d, offline} = await load();
const v = view(d);
const widget = build(v, offline);
if (config.runsInWidget) Script.setWidget(widget);
else if (args.queryParameters.tap) { try { App.close(); } catch (_) {} }
else await widget.presentMedium();
Script.complete();
