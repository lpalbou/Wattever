/* Minimal SVG chart renderer.
 *
 * Dependency-free on purpose: a benchmark box is often offline, so no CDN
 * charting library. Two forms only:
 *   line  -> a measure against context length (ordered, log-spaced x)
 *   bars  -> a measure per size, grouped by setup
 *
 * Conventions enforced here rather than left to callers: zero-based linear y
 * (these are rates, so a truncated axis would exaggerate differences), an
 * optional log y for overlaying measures that share a unit but differ by orders
 * of magnitude, hairline solid grid, 2px strokes, >=8px markers with a surface
 * ring, a crosshair tooltip, and failures drawn explicitly instead of as a gap.
 *
 * Composite encoding, for the summary view: colour carries one dimension
 * (model), dash another (metric), marker shape a third (setup). That keeps
 * identity readable past the 8 categorical hues without ever cycling them.
 */
const Charts = (() => {
  const NS = "http://www.w3.org/2000/svg";
  const FONT = '12px ui-sans-serif, -apple-system, "Segoe UI", system-ui, sans-serif';

  const DASHES = [null, "6 4", "2 3", "9 3 2 3", "1 4"];
  const MARKERS = ["circle", "square", "triangle", "diamond"];

  function el(name, attrs = {}, parent = null) {
    const node = document.createElementNS(NS, name);
    for (const [k, v] of Object.entries(attrs)) {
      if (v === null || v === undefined) continue;
      node.setAttribute(k, String(v));
    }
    if (parent) parent.appendChild(node);
    return node;
  }

  function css(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function theme() {
    return {
      surface: css("--surface-1") || "#1a1a19",
      grid: css("--chart-grid") || "#2e2e2c",
      axis: css("--chart-axis") || "#45453f",
      ink: css("--text-primary") || "#f0efec",
      muted: css("--text-muted") || "#8a8a80",
      secondary: css("--text-secondary") || "#c3c2b7",
      fail: css("--status-critical") || "#e66767",
    };
  }

  const fmtNum = (v) => {
    if (v === null || v === undefined || Number.isNaN(v)) return "-";
    if (v === 0) return "0";
    const a = Math.abs(v);
    if (a >= 10000) return Math.round(v / 1000) + "k";
    if (a >= 100) return Math.round(v).toString();
    if (a >= 10) return v.toFixed(1);
    if (a >= 1) return v.toFixed(2);
    if (a >= 0.01) return v.toFixed(3);
    return v.toExponential(1);
  };

  const fmtTokens = (v) => (v >= 1000 ? (v / 1000) + "k" : String(v));

  function niceCeil(value) {
    if (!(value > 0)) return 1;
    const exp = Math.floor(Math.log10(value));
    const base = Math.pow(10, exp);
    const n = value / base;
    const step = n <= 1 ? 1 : n <= 1.5 ? 1.5 : n <= 2 ? 2 : n <= 3 ? 3 : n <= 5 ? 5 : n <= 7.5 ? 7.5 : 10;
    return step * base;
  }

  function linearTicks(max, count = 5) {
    const top = niceCeil(max);
    const out = [];
    for (let i = 0; i <= count; i++) out.push((top / count) * i);
    return out;
  }

  /* 1-2-5 ticks across the decades the data actually spans */
  function logTicks(min, max) {
    const lo = Math.pow(10, Math.floor(Math.log10(min)));
    const out = [];
    for (let d = lo; d <= max * 10; d *= 10) {
      for (const m of [1, 2, 5]) {
        const v = d * m;
        if (v >= min * 0.95 && v <= max * 1.6) out.push(v);
      }
    }
    if (!out.length) out.push(min, max);
    return out;
  }

  /* ---- tooltip (one shared node) ---- */
  let tip;
  function tooltip() {
    if (!tip) {
      tip = document.createElement("div");
      tip.className = "chart-tip";
      tip.setAttribute("role", "status");
      document.body.appendChild(tip);
    }
    return tip;
  }
  function showTip(html, event) {
    const node = tooltip();
    node.innerHTML = html;
    node.style.display = "block";
    const pad = 14;
    const rect = node.getBoundingClientRect();
    let x = event.clientX + pad;
    let y = event.clientY + pad;
    if (x + rect.width > window.innerWidth - 8) x = event.clientX - rect.width - pad;
    if (y + rect.height > window.innerHeight - 8) y = event.clientY - rect.height - pad;
    node.style.left = Math.max(8, x) + "px";
    node.style.top = Math.max(8, y) + "px";
  }
  function hideTip() {
    if (tip) tip.style.display = "none";
  }

  /* ---- markers ---- */
  function marker(parent, shape, cx, cy, r, fill, ring) {
    const common = { fill, stroke: ring, "stroke-width": 2 };
    if (shape === "square") {
      return el("rect", { x: cx - r, y: cy - r, width: r * 2, height: r * 2, rx: 1, ...common }, parent);
    }
    if (shape === "triangle") {
      const h = r * 1.25;
      return el("path", { d: `M${cx},${cy - h}L${cx + h},${cy + h * 0.75}L${cx - h},${cy + h * 0.75}Z`, ...common }, parent);
    }
    if (shape === "diamond") {
      const h = r * 1.3;
      return el("path", { d: `M${cx},${cy - h}L${cx + h},${cy}L${cx},${cy + h}L${cx - h},${cy}Z`, ...common }, parent);
    }
    return el("circle", { cx, cy, r, ...common }, parent);
  }

  /* ---- line chart ---- */
  function line(container, opts) {
    const t = theme();
    const series = (opts.series || []).filter((s) => (s.points || []).length || (s.failures || []).length);
    const width = Math.max(260, container.clientWidth || 520);
    const height = opts.height || 240;
    // top margin leaves room for the unit label so it cannot collide with the
    // topmost tick label
    const m = { top: opts.yLabel ? 30 : 16, right: 20, bottom: 34, left: 56 };
    container.innerHTML = "";
    const svg = el("svg", {
      width: "100%", height, viewBox: `0 0 ${width} ${height}`,
      preserveAspectRatio: "none",
      role: "img", "aria-label": opts.ariaLabel || opts.yLabel || "chart",
    }, container);

    const xs = opts.xTicks && opts.xTicks.length
      ? opts.xTicks.slice().sort((a, b) => a - b)
      : Array.from(new Set(series.flatMap((s) => (s.points || []).map((p) => p.x)))).sort((a, b) => a - b);
    if (!xs.length) {
      el("text", { x: width / 2, y: height / 2, fill: t.muted, "font-size": 12,
        "text-anchor": "middle", style: `font:${FONT}` }, svg).textContent = "no data";
      return;
    }

    const xMin = Math.min(...xs), xMax = Math.max(...xs);
    const lg = (v) => Math.log10(Math.max(v, 1));
    const sx = (v) => {
      if (xMax === xMin) return m.left + (width - m.left - m.right) / 2;
      const f = (lg(v) - lg(xMin)) / (lg(xMax) - lg(xMin));
      return m.left + f * (width - m.left - m.right);
    };

    const yVals = series.flatMap((s) => (s.points || []).map((p) => p.y))
      .filter((v) => v !== null && v !== undefined && !Number.isNaN(v));
    const yMaxRaw = yVals.length ? Math.max(...yVals) : 1;
    const yMinRaw = yVals.length ? Math.min(...yVals.filter((v) => v > 0)) : 1;
    // Overlaying measures that share a unit but span orders of magnitude (decode
    // at 1 tk/s beside prefill at 550) would flatten the small one against the
    // axis. A log axis keeps both readable and is still one axis, one unit.
    const useLog = opts.yScale === "log"
      || (opts.yScale === "auto" && yVals.length > 1 && yMinRaw > 0 && yMaxRaw / yMinRaw >= 40);
    const plotH = height - m.top - m.bottom;
    let yTicks, sy;
    if (useLog) {
      const lo = Math.max(yMinRaw / 1.6, 1e-6);
      const hi = yMaxRaw * 1.25;
      const lgLo = Math.log10(lo), lgHi = Math.log10(hi);
      sy = (v) => height - m.bottom - ((Math.log10(Math.max(v, lo)) - lgLo) / (lgHi - lgLo)) * plotH;
      yTicks = logTicks(lo, hi);
    } else {
      yTicks = linearTicks(yMaxRaw);
      const yMax = yTicks[yTicks.length - 1] || 1;
      sy = (v) => height - m.bottom - (v / yMax) * plotH;
    }

    for (const tick of yTicks) {
      const y = sy(tick);
      if (y < m.top - 2 || y > height - m.bottom + 2) continue;
      el("line", { x1: m.left, x2: width - m.right, y1: y, y2: y,
        stroke: tick === 0 ? t.axis : t.grid, "stroke-width": 1 }, svg);
      el("text", { x: m.left - 8, y: y + 4, fill: t.muted, "text-anchor": "end",
        style: `font:${FONT}` }, svg).textContent = fmtNum(tick);
    }
    for (const xv of xs) {
      el("text", { x: sx(xv), y: height - m.bottom + 16, fill: t.muted,
        "text-anchor": "middle", style: `font:${FONT}` }, svg).textContent = fmtTokens(xv);
    }
    if (opts.xLabel) {
      el("text", { x: (m.left + width - m.right) / 2, y: height - 3, fill: t.muted,
        "text-anchor": "middle", style: `font:${FONT}` }, svg).textContent = opts.xLabel;
    }
    if (opts.yLabel) {
      el("text", { x: 4, y: 14, fill: t.secondary, "text-anchor": "start",
        style: `font:${FONT}` }, svg).textContent =
        opts.yLabel + (useLog ? "  (log scale)" : "");
    }

    const plot = el("g", {}, svg);
    for (const s of series) {
      const pts = (s.points || []).filter((p) => p.y !== null && p.y !== undefined && !Number.isNaN(p.y))
        .sort((a, b) => a.x - b.x);
      if (pts.length > 1) {
        el("path", {
          d: pts.map((p, i) => `${i ? "L" : "M"}${sx(p.x).toFixed(1)},${sy(p.y).toFixed(1)}`).join(" "),
          fill: "none", stroke: s.color, "stroke-width": 2,
          "stroke-dasharray": s.dash || null,
          "stroke-linecap": "round", "stroke-linejoin": "round",
        }, plot);
      }
      for (const p of pts) {
        marker(plot, s.marker || "circle", sx(p.x), sy(p.y), 5, s.color, t.surface);
        const hit = el("circle", { cx: sx(p.x), cy: sy(p.y), r: 13, fill: "transparent",
          style: "cursor:crosshair" }, plot);
        const rows = [`<b>${s.name}</b>`,
          `${fmtTokens(p.x)} prefill &rarr; <b>${fmtNum(p.y)}</b> ${p.unit || opts.unit || ""}`];
        if (p.note) rows.push(`<span class="dim">${p.note}</span>`);
        hit.addEventListener("mousemove", (e) => showTip(rows.join("<br>"), e));
        hit.addEventListener("mouseleave", hideTip);
      }
      for (const f of s.failures || []) {
        const x = sx(f.x), y = height - m.bottom;
        const g = el("g", { style: "cursor:help" }, plot);
        el("path", { d: `M${x - 4},${y - 10}L${x + 4},${y - 2}M${x + 4},${y - 10}L${x - 4},${y - 2}`,
          stroke: t.fail, "stroke-width": 2, "stroke-linecap": "round" }, g);
        el("circle", { cx: x, cy: y - 6, r: 11, fill: "transparent" }, g);
        g.addEventListener("mousemove", (e) => showTip(
          `<b>${s.name}</b><br>${fmtTokens(f.x)} prefill &rarr; <span style="color:${t.fail}">failed</span>` +
          (f.reason ? `<br><span class="dim">${f.reason}</span>` : ""), e));
        g.addEventListener("mouseleave", hideTip);
      }
      // direct-label the endpoint only when the panel is not crowded, and
      // never for a lone point: several single-point series share an x and
      // their labels would land on top of each other
      if (series.length <= 4 && pts.length > 1) {
        const last = pts[pts.length - 1];
        const lx = sx(last.x), ly = sy(last.y);
        if (lx < width - m.right - 26) {
          el("text", { x: lx + 9, y: ly + 4, fill: s.color, style: `font:${FONT}`,
            "font-weight": 600 }, plot).textContent = fmtNum(last.y);
        }
      }
    }
  }

  /* ---- grouped bars ---- */
  function bars(container, opts) {
    const t = theme();
    const groups = opts.groups || [];
    const width = Math.max(260, container.clientWidth || 520);
    const height = opts.height || 240;
    const m = { top: opts.yLabel ? 30 : 16, right: 14, bottom: 34, left: 58 };
    container.innerHTML = "";
    const svg = el("svg", {
      width: "100%", height, viewBox: `0 0 ${width} ${height}`,
      preserveAspectRatio: "none",
      role: "img", "aria-label": opts.ariaLabel || opts.yLabel || "chart",
    }, container);
    const values = groups.flatMap((g) => g.bars.map((b) => b.value)).filter((v) => v > 0);
    if (!values.length) {
      el("text", { x: width / 2, y: height / 2, fill: t.muted, "text-anchor": "middle",
        style: `font:${FONT}` }, svg).textContent = "no data";
      return;
    }
    const yTicks = linearTicks(Math.max(...values));
    const yMax = yTicks[yTicks.length - 1] || 1;
    const sy = (v) => height - m.bottom - (v / yMax) * (height - m.top - m.bottom);
    for (const tick of yTicks) {
      el("line", { x1: m.left, x2: width - m.right, y1: sy(tick), y2: sy(tick),
        stroke: tick === 0 ? t.axis : t.grid, "stroke-width": 1 }, svg);
      el("text", { x: m.left - 8, y: sy(tick) + 4, fill: t.muted, "text-anchor": "end",
        style: `font:${FONT}` }, svg).textContent = fmtNum(tick);
    }
    if (opts.yLabel) {
      el("text", { x: 4, y: 14, fill: t.secondary, style: `font:${FONT}` }, svg)
        .textContent = opts.yLabel;
    }
    const band = (width - m.left - m.right) / groups.length;
    groups.forEach((group, gi) => {
      const gx = m.left + band * gi;
      const inner = Math.min(band * 0.8, 90);
      const n = Math.max(1, group.bars.length);
      const gap = 2;                         // 2px surface gap between fills
      const bw = Math.max(3, (inner - gap * (n - 1)) / n);
      group.bars.forEach((bar, bi) => {
        const x = gx + (band - inner) / 2 + bi * (bw + gap);
        const has = bar.value > 0;
        const y = has ? sy(bar.value) : height - m.bottom - 2;
        const h = has ? height - m.bottom - y : 2;
        const node = el("rect", {
          x, y, width: bw, height: Math.max(h, 1), rx: Math.min(4, bw / 2),
          fill: has ? bar.color : t.grid, style: "cursor:pointer",
          "fill-opacity": bar.dash ? 0.55 : 1,
          stroke: bar.dash && has ? bar.color : null,
          "stroke-width": bar.dash && has ? 1.5 : null,
          "stroke-dasharray": bar.dash && has ? "4 3" : null,
        }, svg);
        const rows = [`<b>${bar.name}</b>`,
          `${group.label} &rarr; <b>${has ? fmtNum(bar.value) : "n/a"}</b> ${opts.unit || ""}`];
        if (bar.note) rows.push(`<span class="dim">${bar.note}</span>`);
        node.addEventListener("mousemove", (e) => showTip(rows.join("<br>"), e));
        node.addEventListener("mouseleave", hideTip);
      });
      el("text", { x: gx + band / 2, y: height - m.bottom + 16, fill: t.muted,
        "text-anchor": "middle", style: `font:${FONT}` }, svg).textContent = group.label;
    });
    if (opts.xLabel) {
      el("text", { x: (m.left + width - m.right) / 2, y: height - 3, fill: t.muted,
        "text-anchor": "middle", style: `font:${FONT}` }, svg).textContent = opts.xLabel;
    }
  }

  /* Render on the element's *settled* width.
   *
   * Measuring during a mutation is the trap here: a facet appended while it is
   * the grid's only child reports the full grid width, then the next append
   * reflows it to half that, leaving the SVG viewBox twice the box it renders
   * into. A ResizeObserver redraws whenever the real width changes, so the
   * chart is always built against the width it actually has. */
  const registry = new Map();
  const observer = typeof ResizeObserver !== "undefined"
    ? new ResizeObserver((entries) => {
      for (const entry of entries) {
        const spec = registry.get(entry.target);
        if (!spec) continue;
        const w = Math.round(entry.contentRect.width);
        if (w < 40 || w === spec.lastWidth) continue;
        spec.lastWidth = w;
        (spec.kind === "bars" ? bars : line)(entry.target, spec.opts);
      }
    })
    : null;

  function draw(container, kind, opts) {
    const prev = registry.get(container);
    registry.set(container, { kind, opts, lastWidth: prev ? prev.lastWidth : 0 });
    if (observer) {
      observer.observe(container);          // fires once with the settled width
      if (container.clientWidth > 40) {
        registry.get(container).lastWidth = container.clientWidth;
        (kind === "bars" ? bars : line)(container, opts);
      }
    } else {
      (kind === "bars" ? bars : line)(container, opts);
    }
  }

  function release(container) {
    if (observer && registry.has(container)) observer.unobserve(container);
    registry.delete(container);
  }

  return {
    line: (c, o) => draw(c, "line", o),
    bars: (c, o) => draw(c, "bars", o),
    release, fmtNum, fmtTokens, hideTip, DASHES, MARKERS,
  };
})();
