/* PCIe LTSSM Simulator viewer (vanilla JS, no build step).
 * Author: Farzana Akhter
 *
 * Loads traces/index.json, then one trace per scenario, and renders:
 * topology (link state at the playhead), replay controls, a per-port
 * swimlane timeline with zoom/pan, a filterable event log, the summary
 * (LnkSta-like table, counters, enumeration, checks) and learning notes.
 * Mystery mode hides the title, fault and notes until "Reveal".
 */
(function () {
  "use strict";

  // ------------------------------------------------------------------ constants
  const LANE_H = 30;
  const AXIS_H = 26;
  const MIN_SPAN = 2 * 1000; // ns: the deepest zoom shows 2 microseconds
  const SPEEDS = [0.5, 1, 2, 4, 8];
  const COLOR_GROUP = {
    Reset: "Detect", Detect: "Detect", Polling: "Polling", Configuration: "Configuration", L0: "L0",
    Recovery: "Recovery", Equalization: "Equalization", L0s: "LowPower", L1: "LowPower", L2: "LowPower",
    "Polling.Compliance": "Red", "Hot Reset": "Red", Disabled: "Red", Loopback: "Red",
  };
  const LEGEND = [
    ["Detect", "Detect"], ["Polling", "Polling"], ["Configuration", "Configuration"], ["L0", "L0"],
    ["Recovery", "Recovery"], ["Equalization", "Equalization"], ["LowPower", "L0s / L1 / L2"],
    ["Red", "Polling.Compliance / Hot Reset / Disabled / Loopback"],
  ];
  const FAMILY_FILTERS = ["Detect", "Polling", "Configuration", "L0", "Recovery", "Equalization", "L0s", "L1", "L2",
    "Polling.Compliance", "Hot Reset", "Disabled", "Loopback"];

  // ---------------------------------------------------------------------- state
  const S = {
    index: [], trace: null, mystery: false,
    lanes: [], perPort: {}, stepTimes: [], t: 0, end: 1,
    view: { t0: 0, t1: 1 }, playTimer: null, speedIdx: 1,
    logRows: [], drag: null,
  };

  const $ = (id) => document.getElementById(id);
  let svgNS = null; // taken from an inline <svg> in index.html

  function svgNamespace() {
    if (!svgNS) svgNS = document.getElementById("lanes").namespaceURI;
    return svgNS;
  }

  function el(tag, attrs, text) {
    const node = document.createElementNS(svgNamespace(), tag);
    for (const k in attrs || {}) node.setAttribute(k, attrs[k]);
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function esc(text) {
    return String(text).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  function familyOf(name) {
    if (name.startsWith("Recovery.Equalization")) return "Equalization";
    if (name === "Polling.Compliance") return "Polling.Compliance";
    if (name.startsWith("Loopback")) return "Loopback";
    return name.split(".")[0];
  }

  function groupOf(name) { return COLOR_GROUP[familyOf(name)] || "Detect"; }

  function fmtMs(ns, digits) {
    const d = digits === undefined ? 6 : digits;
    return (ns / 1e6).toFixed(d);
  }

  function fmtDur(ns) {
    if (ns >= 1e6) return (ns / 1e6).toFixed(3) + " ms";
    if (ns >= 1e3) return (ns / 1e3).toFixed(3) + " us";
    return ns + " ns";
  }

  function store(key, value) {
    try { localStorage.setItem(key, value); } catch (e) { /* storage unavailable */ }
  }
  function recall(key) {
    try { return localStorage.getItem(key); } catch (e) { return null; }
  }

  // --------------------------------------------------------------------- loading
  async function init() {
    S.mystery = recall("ltssm-mystery") === "1";
    $("mystery-toggle").checked = S.mystery;
    buildLegend();
    bindControls();
    let index;
    try {
      const res = await fetch("traces/index.json");
      if (!res.ok) throw new Error("HTTP " + res.status);
      index = await res.json();
    } catch (err) {
      showLoadError();
      return;
    }
    S.index = index.scenarios || [];
    fillScenarioSelect();
    const wanted = (location.hash || "").replace("#", "").toUpperCase();
    const first = S.index.find((s) => s.id === wanted) || S.index[0];
    if (first) loadScenario(first.id);
  }

  function showLoadError() {
    const box = $("load-error");
    box.hidden = false;
    box.innerHTML = "<strong>Could not load the traces.</strong> Browsers block <code>fetch()</code> for pages opened " +
      "from the file system. From the repository root run <code>python -m http.server --directory docs 8000</code> " +
      "(or <code>cd docs</code> then <code>python -m http.server 8000</code>) and open " +
      "<code>http://localhost:8000/</code>. If the traces are missing, run <code>python -m ltssm run-all</code> first.";
    $("scenario-title").textContent = "No trace loaded";
  }

  function fillScenarioSelect() {
    const sel = $("scenario-select");
    const current = sel.value;
    sel.innerHTML = "";
    for (const s of S.index) {
      const opt = document.createElement("option");
      opt.value = s.id;
      opt.textContent = S.mystery ? s.id + " - (mystery)" : s.id + " - " + s.title;
      sel.appendChild(opt);
    }
    if (current) sel.value = current;
  }

  async function loadScenario(id) {
    const item = S.index.find((s) => s.id === id);
    if (!item) return;
    stopPlay();
    let trace;
    try {
      const res = await fetch("traces/" + item.file);
      if (!res.ok) throw new Error("HTTP " + res.status);
      trace = await res.json();
    } catch (err) {
      showLoadError();
      return;
    }
    S.trace = trace;
    delete $("topology").parentElement.dataset.centered;
    $("scenario-select").value = id;
    if (location.hash !== "#" + id) history.replaceState(null, "", "#" + id);
    prepare(trace);
    renderAll();
  }

  // ------------------------------------------------------------------ prepare
  function prepare(trace) {
    S.end = trace.duration_ns;
    S.perPort = {};
    S.lanes = [];
    const states = trace.events.filter((e) => e.kind === "state");
    for (const p of trace.topology.ports) S.perPort[p.name] = [];
    for (const e of states) (S.perPort[e.port] = S.perPort[e.port] || []).push(e);
    for (const p of trace.topology.ports) {
      const evs = S.perPort[p.name];
      const segs = [];
      for (let i = 0; i < evs.length; i++) {
        const t1 = i + 1 < evs.length ? evs[i + 1].t_ns : S.end;
        segs.push({ t0: evs[i].t_ns, t1: Math.max(t1, evs[i].t_ns), ev: evs[i] });
      }
      const faults = trace.events.filter((e) => e.kind === "fault" && e.port === p.name);
      S.lanes.push({ port: p.name, link: p.link, role: p.role, segs: segs, faults: faults });
    }
    const times = Array.from(new Set(states.map((e) => e.t_ns))).sort((a, b) => a - b);
    S.stepTimes = times;
    S.t = S.end; // start at the final state; Play restarts from 0
    S.view = { t0: 0, t1: S.end };
    $("scrub").max = Math.max(0, times.length - 1);
    $("scrub").value = Math.max(0, times.length - 1);
    fillFilters(trace);
  }

  function stateAt(port, t) {
    const evs = S.perPort[port] || [];
    let lo = 0, hi = evs.length - 1, ans = null;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (evs[mid].t_ns <= t) { ans = evs[mid]; lo = mid + 1; } else { hi = mid - 1; }
    }
    return ans;
  }

  // ------------------------------------------------------------------- render
  function renderAll() {
    renderHeader();
    renderTopology();
    renderTimeline();
    renderLog();
    renderSummary();
    renderNotes();
    renderReplay();
  }

  function renderHeader() {
    const t = S.trace, sc = t.scenario, passed = t.result.passed;
    $("scenario-title").textContent = S.mystery ? sc.id + ": what is going on here?" : sc.id + ": " + sc.title;
    $("scenario-desc").textContent = S.mystery ? sc.symptom : sc.description;
    $("scenario-expected").textContent = sc.expected.summary;
    $("expected-box").hidden = S.mystery;
    $("mystery-box").hidden = !S.mystery;
    for (const id of ["result-pill", "summary-pill"]) {
      const pill = $(id);
      pill.hidden = false;
      pill.className = "pill " + (passed ? "pass" : "fail");
      pill.textContent = passed ? "PASS" : "FAIL";
      pill.title = passed ? "The run matches the expected outcome" : "The run does not match the expected outcome";
    }
    $("run-cmd").textContent = "python -m ltssm run " + sc.id + (S.mystery ? " --hidden" : " --trace");
    document.title = "PCIe LTSSM Simulator - " + sc.id;
  }

  // ----------------------------------------------------------------- topology
  function buildTree(topo) {
    const ports = {};
    for (const p of topo.ports) ports[p.name] = p;
    const nodes = {};
    for (const d of topo.devices) nodes[d.id] = { id: d.id, kind: d.kind, label: d.label, ports: d.ports, children: [] };
    let root = null;
    for (const d of topo.devices) if (d.kind === "root_complex") root = nodes[d.id];
    for (const l of topo.links) {
      const parent = nodes[ports[l.dsp].device];
      const child = l.usp ? nodes[ports[l.usp].device]
        : { id: "empty-" + l.name, kind: "empty", label: "empty slot", ports: [], children: [] };
      parent.children.push({ link: l, node: child });
    }
    return { root: root, ports: ports };
  }

  function leafCount(node) {
    if (!node.children.length) return 1;
    return node.children.reduce((n, c) => n + leafCount(c.node), 0);
  }

  function renderTopology() {
    const svg = $("topology");
    svg.innerHTML = "";
    const topo = S.trace.topology;
    const tree = buildTree(topo);
    if (!tree.root) return;
    const colW = 168, boxW = 136, boxH = 46, levelGap = 112, padTop = 14;
    const width = Math.max(leafCount(tree.root) * colW, 360);
    const placed = [];
    const linksDrawn = [];
    let maxDepth = 0;

    function place(node, depth, x0) {
      const leaves = leafCount(node);
      const cx = x0 + (leaves * colW) / 2;
      const y = padTop + depth * levelGap;
      maxDepth = Math.max(maxDepth, depth);
      placed.push({ node: node, cx: cx, y: y });
      let cursor = x0;
      node.children.forEach((c, i) => {
        const childLeaves = leafCount(c.node);
        const childCx = cursor + (childLeaves * colW) / 2;
        const anchorX = cx - boxW / 2 + ((i + 1) * boxW) / (node.children.length + 1);
        linksDrawn.push({ link: c.link, x1: anchorX, y1: y + boxH, x2: childCx, y2: y + levelGap });
        place(c.node, depth + 1, cursor);
        cursor += childLeaves * colW;
      });
    }
    place(tree.root, 0, 0);
    const height = padTop + maxDepth * levelGap + boxH + 12;
    svg.setAttribute("viewBox", "0 0 " + width + " " + height);

    for (const l of linksDrawn) {
      const st = linkStatus(l.link, S.t);
      svg.appendChild(el("line", { x1: l.x1, y1: l.y1, x2: l.x2, y2: l.y2, class: "link-line fam-" + st.group }));
      const lx = (l.x1 + l.x2) / 2, ly = (l.y1 + l.y2) / 2 - 2;
      const g = el("g");
      g.appendChild(el("rect", { x: lx - 64, y: ly - 13, width: 128, height: 30, rx: 5, class: "link-bg" }));
      g.appendChild(el("text", { x: lx, y: ly, "text-anchor": "middle", class: "link-name" }, "Link " + l.link.name + "  " + st.state));
      g.appendChild(el("text", { x: lx, y: ly + 13, "text-anchor": "middle", class: "link-label" }, st.detail));
      const title = el("title", {}, "Link " + l.link.name + " (" + l.link.dsp + " - " + (l.link.usp || "empty slot") + "): " +
        st.state + " " + st.detail);
      g.appendChild(title);
      svg.appendChild(g);
    }
    for (const p of placed) {
      const n = p.node;
      const g = el("g", { class: "node" + (n.kind === "empty" ? " empty" : "") });
      g.appendChild(el("rect", { x: p.cx - boxW / 2, y: p.y, width: boxW, height: boxH, rx: 8 }));
      const kindName = { root_complex: "Root Complex", switch: "Switch", endpoint: "Endpoint", empty: "" }[n.kind];
      const title = n.kind === "endpoint" ? n.label : (kindName || n.label);
      g.appendChild(el("text", { x: p.cx, y: p.y + 19, "text-anchor": "middle", class: "node-title" }, title));
      const portNames = n.ports.join(" ");
      const sub = n.kind === "empty" ? "(nothing plugged in)" : portNames;
      g.appendChild(el("text", { x: p.cx, y: p.y + 35, "text-anchor": "middle", class: "port-label" }, sub));
      svg.appendChild(g);
    }
    // on narrow screens the diagram scrolls sideways: start centered on the root
    const box = svg.parentElement;
    if (box.scrollWidth > box.clientWidth && !box.dataset.centered) {
      box.scrollLeft = (box.scrollWidth - box.clientWidth) / 2;
      box.dataset.centered = "1";
    }
  }

  const RANK = { Red: 7, Equalization: 6, Recovery: 5, Configuration: 4, Polling: 3, LowPower: 2, L0: 1, Detect: 0 };

  function linkStatus(link, t) {
    const a = stateAt(link.dsp, t);
    if (!link.usp) return { group: "Detect", state: a ? a.to : "Detect", detail: "empty slot" };
    const b = stateAt(link.usp, t);
    if (!a || !b) return { group: "Detect", state: "-", detail: "" };
    if (a.to === "L0" && b.to === "L0") return { group: "L0", state: "L0", detail: a.speed + " " + a.width };
    const ga = groupOf(a.to), gb = groupOf(b.to);
    const pick = RANK[ga] >= RANK[gb] ? a : b;
    const group = groupOf(pick.to);
    const short = pick.to.replace("Configuration.", "Cfg.").replace("Recovery.Equalization.", "EQ.");
    const detail = group === "Detect" ? "no link" : pick.speed + " " + pick.width;
    return { group: group, state: short, detail: detail };
  }

  // ----------------------------------------------------------------- timeline
  function buildLegend() {
    const box = $("legend");
    box.innerHTML = LEGEND.map((l) => '<span><i class="fam-' + l[0] + '"></i>' + esc(l[1]) + "</span>").join("") +
      '<span><i style="background:var(--bad);width:3px"></i>playhead</span>';
  }

  function xOf(t, w) { return ((t - S.view.t0) / (S.view.t1 - S.view.t0)) * w; }

  function niceStep(span, px) {
    const target = span / Math.max(2, px / 95);
    const pow = Math.pow(10, Math.floor(Math.log10(target)));
    for (const m of [1, 2, 5, 10]) if (m * pow >= target) return m * pow;
    return 10 * pow;
  }

  function renderTimeline() {
    const labels = $("lane-labels");
    labels.innerHTML = '<div class="axis-spacer"></div>' + S.lanes.map((l) =>
      "<div title=\"" + esc(l.port + " on link " + l.link + " (" + l.role + ")") + "\"><span>" + esc(l.port) + "</span><small>" +
      esc(l.link) + " " + esc(l.role) + "</small></div>").join("");
    drawLanes();
    syncZoomControls();
  }

  function drawLanes() {
    const svg = $("lanes");
    const area = $("lane-area");
    const w = Math.max(200, area.clientWidth);
    const h = AXIS_H + S.lanes.length * LANE_H;
    svg.setAttribute("width", w);
    svg.setAttribute("height", h);
    svg.setAttribute("viewBox", "0 0 " + w + " " + h);
    svg.innerHTML = "";
    const span = S.view.t1 - S.view.t0;

    // axis
    const axis = el("g", { class: "axis" });
    const step = niceStep(span, w);
    const digits = Math.max(0, Math.min(6, Math.ceil(-Math.log10(step / 1e6))));
    for (let t = Math.ceil(S.view.t0 / step) * step; t <= S.view.t1; t += step) {
      const x = xOf(t, w);
      axis.appendChild(el("line", { x1: x, y1: AXIS_H - 6, x2: x, y2: h }));
      axis.appendChild(el("text", { x: x + 3, y: 14 }, fmtMs(t, digits) + " ms"));
    }
    axis.appendChild(el("line", { x1: 0, y1: AXIS_H - 0.5, x2: w, y2: AXIS_H - 0.5 }));
    svg.appendChild(axis);

    S.lanes.forEach((lane, i) => {
      const y = AXIS_H + i * LANE_H;
      svg.appendChild(el("rect", { x: 0, y: y, width: w, height: LANE_H, class: "lane-bg" + (i % 2 ? " alt" : "") }));
      lane.segs.forEach((seg, j) => {
        if (seg.t1 < S.view.t0 || seg.t0 > S.view.t1) return;
        const x0 = Math.max(0, xOf(seg.t0, w));
        const x1 = Math.min(w, xOf(seg.t1, w));
        const bw = Math.max(1.5, x1 - x0);
        const group = groupOf(seg.ev.to);
        const r = el("rect", { x: x0, y: y + 5, width: bw, height: LANE_H - 10, rx: 2, class: "bar fam-" + group });
        r.dataset.lane = i;
        r.dataset.seg = j;
        svg.appendChild(r);
        if (bw > 44) {
          const label = barLabel(seg.ev);
          const maxChars = Math.floor((bw - 8) / 6.3);
          if (maxChars >= 3) {
            const text = label.length > maxChars ? label.slice(0, maxChars - 1) + "." : label;
            const light = group === "Polling" || group === "Detect";
            svg.appendChild(el("text", { x: x0 + 4, y: y + LANE_H / 2 + 4, class: "bar-label" + (light ? " on-light" : "") }, text));
          }
        }
      });
      if (!S.mystery) {
        for (const f of lane.faults) {
          if (f.t_ns < S.view.t0 || f.t_ns > S.view.t1) continue;
          const x = xOf(f.t_ns, w);
          svg.appendChild(el("path", { d: "M" + (x - 5) + " " + (y + 1) + " L" + (x + 5) + " " + (y + 1) + " L" + x + " " + (y + 8) + " Z", class: "fault-mark" }));
        }
      }
    });
    if (S.t >= S.view.t0 && S.t <= S.view.t1) {
      const x = xOf(S.t, w);
      svg.appendChild(el("line", { x1: x, y1: AXIS_H - 6, x2: x, y2: h, class: "playhead" }));
    }
  }

  function barLabel(ev) {
    if (ev.to === "L0") return "L0 " + ev.speed + " " + ev.width;
    const parts = ev.to.split(".");
    if (ev.to.startsWith("Recovery.Equalization")) return "EQ " + parts[2].replace("Phase", "P");
    return parts.length > 1 ? parts.slice(1).join(".") : ev.to;
  }

  // zoom: slider position <-> view span (log scale)
  function spanForSlider(v) {
    const full = S.end, min = Math.min(MIN_SPAN, full);
    return full * Math.pow(min / full, v / 1000);
  }
  function sliderForSpan(span) {
    const full = S.end, min = Math.min(MIN_SPAN, full);
    if (full <= min) return 0;
    return Math.round((1000 * Math.log(span / full)) / Math.log(min / full));
  }

  function setView(t0, span) {
    span = Math.min(S.end, Math.max(Math.min(MIN_SPAN, S.end), span));
    t0 = Math.max(0, Math.min(S.end - span, t0));
    S.view = { t0: t0, t1: t0 + span };
    drawLanes();
    syncZoomControls();
  }

  function zoomAround(center, factor) {
    const span = (S.view.t1 - S.view.t0) * factor;
    const ratio = (center - S.view.t0) / (S.view.t1 - S.view.t0);
    setView(center - ratio * span, span);
  }

  function syncZoomControls() {
    const span = S.view.t1 - S.view.t0;
    $("zoom").value = sliderForSpan(span);
    const room = S.end - span;
    $("pan").value = room > 0 ? Math.round((S.view.t0 / room) * 1000) : 0;
    $("pan").disabled = room <= 0;
  }

  function ensureVisible(t) {
    const span = S.view.t1 - S.view.t0;
    if (t < S.view.t0 || t > S.view.t1) setView(t - span * 0.3, span);
  }

  // ------------------------------------------------------------------ tooltip
  function showTip(evt, html) {
    const tip = $("tooltip");
    tip.innerHTML = html;
    tip.hidden = false;
    const pad = 14;
    let x = evt.clientX + pad, y = evt.clientY + pad;
    const r = tip.getBoundingClientRect();
    if (x + r.width > window.innerWidth - 8) x = Math.max(8, evt.clientX - r.width - pad);
    if (y + r.height > window.innerHeight - 8) y = Math.max(8, evt.clientY - r.height - pad);
    tip.style.left = x + "px";
    tip.style.top = y + "px";
  }
  function hideTip() { $("tooltip").hidden = true; }

  function segTip(seg) {
    const e = seg.ev;
    return "<b>" + esc(e.to) + "</b>" +
      '<div class="mono">' + esc(e.port) + " [link " + esc(e.link) + "] " + esc(e.speed) + " " + esc(e.width) +
      (e.rule ? " - rule " + esc(e.rule) : "") + "</div>" +
      '<div class="mono">from ' + esc(e.from) + " at " + fmtMs(e.t_ns) + " ms, lasted " + fmtDur(seg.t1 - seg.t0) + "</div>" +
      "<div>" + esc(e.reason) + "</div>";
  }

  // ---------------------------------------------------------------- event log
  function fillFilters(trace) {
    const fp = $("f-port"), ff = $("f-family");
    const keepPort = fp.value, keepFam = ff.value;
    fp.innerHTML = '<option value="">All ports</option>' + trace.topology.ports.map((p) =>
      '<option value="' + esc(p.name) + '">' + esc(p.name) + " (link " + esc(p.link) + ")</option>").join("");
    ff.innerHTML = '<option value="">All states</option>' + FAMILY_FILTERS.map((f) =>
      '<option value="' + esc(f) + '">' + esc(f) + "</option>").join("");
    if ([...fp.options].some((o) => o.value === keepPort)) fp.value = keepPort;
    if ([...ff.options].some((o) => o.value === keepFam)) ff.value = keepFam;
  }

  function renderLog() {
    const port = $("f-port").value, fam = $("f-family").value, kind = $("f-kind").value;
    const text = $("f-text").value.trim().toLowerCase();
    const rows = [];
    S.trace.events.forEach((e, i) => {
      if (S.mystery && e.kind === "fault") return;
      if (port && e.port !== port) return;
      if (kind && e.kind !== kind) return;
      if (fam && familyOf(e.to) !== fam && familyOf(e.from) !== fam) return;
      if (text) {
        const hay = (e.reason + " " + e.from + " " + e.to + " " + (e.rule || "") + " " + e.port).toLowerCase();
        if (!hay.includes(text)) return;
      }
      rows.push(i);
    });
    S.logRows = rows;
    const tbody = $("log").querySelector("tbody");
    const html = rows.map((i) => {
      const e = S.trace.events[i];
      const transition = e.kind === "state"
        ? '<span class="fam-chip fam-' + groupOf(e.to) + '"></span>' + esc(e.from) + " &rarr; " + esc(e.to)
        : esc(e.kind === "eq" ? "equalization trial" : e.kind);
      return '<tr data-i="' + i + '" class="kind-' + e.kind + '"><td>' + fmtMs(e.t_ns) + "</td><td>" + esc(e.port) +
        "</td><td>" + esc(e.link) + "</td><td>" + transition + "</td><td>" + esc(e.speed) + "</td><td>" +
        esc(e.width) + "</td><td>" + esc(e.rule || "") + "</td><td>" + esc(e.reason) + "</td></tr>";
    }).join("");
    tbody.innerHTML = html || '<tr><td colspan="8">No events match the filters.</td></tr>';
    $("log-count").textContent = rows.length + " of " + S.trace.events.length + " events";
    highlightLog(false);
  }

  function highlightLog(scroll) {
    const tbody = $("log").querySelector("tbody");
    const prev = tbody.querySelector("tr.current");
    if (prev) prev.classList.remove("current");
    let best = null;
    for (const i of S.logRows) {
      const e = S.trace.events[i];
      if (e.t_ns <= S.t && e.kind === "state") best = i;
      if (e.t_ns > S.t) break;
    }
    if (best === null) return;
    const row = tbody.querySelector('tr[data-i="' + best + '"]');
    if (!row) return;
    row.classList.add("current");
    if (scroll) {
      const box = row.closest(".log-scroll");
      const top = row.offsetTop - box.clientHeight / 2;
      box.scrollTop = Math.max(0, top);
    }
  }

  // ------------------------------------------------------------------ summary
  function renderSummary() {
    const t = S.trace;
    const pm = (b) => (b ? "+" : "-");
    let html = "<thead><tr><th>Port</th><th>Link</th><th>State</th><th>Speed</th><th>Width</th><th>LnkCap</th>" +
      "<th>Target</th><th>EQ 8G/16G</th><th>DLActive</th><th>RxErr</th></tr></thead><tbody>";
    for (const p of Object.values(t.ports)) {
      html += "<tr><td>" + esc(p.name) + "</td><td>" + esc(p.link) + "</td><td>" + esc(p.state) + "</td><td>" +
        esc(p.speed) + (p.speed !== p.max_speed ? "*" : "") + "</td><td>" + esc(p.width) +
        (p.width !== p.max_width ? "*" : "") + "</td><td>" + esc(p.max_speed + " " + p.max_width) + "</td><td>" +
        esc(p.target_link_speed) + "</td><td>" + pm(p.eq_8gt_complete) + " / " + pm(p.eq_16gt_complete) +
        "</td><td>" + pm(p.dl_link_active) + "</td><td>" + p.rx_errors + "</td></tr>";
    }
    html += '</tbody><caption class="muted" style="caption-side:bottom;text-align:left;padding-top:4px">' +
      "* below this port's own capability (expected when the partner is slower or narrower)</caption>";
    $("port-table").innerHTML = html;

    let ch = "<thead><tr><th>Link</th><th>First L0</th><th>Final speed at</th><th>Recov</th><th>Flaps</th>" +
      "<th>Detect</th><th>Down</th><th>EQ try 8G/16G</th><th>EQ fail 8G/16G</th></tr></thead><tbody>";
    for (const l of Object.values(t.links)) {
      const c = l.counters;
      const ms = (v) => (v === null || v === undefined ? "-" : (v / 1e6).toFixed(3) + " ms");
      ch += "<tr><td>" + esc(l.name) + "</td><td>" + ms(c.time_to_l0_ns) + "</td><td>" + ms(c.time_to_final_speed_ns) +
        "</td><td>" + c.recovery_count + "</td><td>" + c.flap_count + "</td><td>" + c.detect_cycles + "</td><td>" +
        c.link_down_count + "</td><td>" + c.eq_attempts.Gen3 + "/" + c.eq_attempts.Gen4 + "</td><td>" +
        c.eq_failures.Gen3 + "/" + c.eq_failures.Gen4 + "</td></tr>";
    }
    $("counter-table").innerHTML = ch + "</tbody>";

    const en = t.enumeration;
    let tree = en.lspci_t + "\n\n" + en.device_count + " function(s) found\n";
    for (const f of en.functions) {
      const buses = f.secondary === null ? "" : "  bus " + pad2(f.primary) + "/" + pad2(f.secondary) + "/" + pad2(f.subordinate);
      tree += "\n" + f.bdf + "  " + f.header + "  " + f.vendor_id.slice(2) + ":" + f.device_id.slice(2) + "  " +
        f.port + " " + f.name + buses;
    }
    for (const m of en.missing) tree += "\nbehind " + m.behind + ": " + (m.port ? m.port + " not found - " : "") + m.reason;
    $("enum-tree").textContent = tree;

    const list = $("checks");
    if (S.mystery) {
      list.innerHTML = '<li><span class="text">Checks are hidden in mystery mode (they describe the expected outcome).</span></li>';
      return;
    }
    list.innerHTML = t.result.checks.map((c) =>
      '<li><span class="mark ' + (c.passed ? "pass" : "fail") + '">' + (c.passed ? "PASS" : "FAIL") +
      '</span><span class="text">' + esc(c.name) + '<span class="detail">' + esc(c.detail) + "</span></span></li>").join("");
  }

  function pad2(n) { return n === null || n === undefined ? "--" : ("0" + n.toString(16)).slice(-2); }

  // -------------------------------------------------------------------- notes
  function listHtml(items) { return "<ul>" + items.map((x) => "<li>" + esc(x) + "</li>").join("") + "</ul>"; }

  function renderNotes() {
    const sc = S.trace.scenario;
    const box = $("notes");
    if (S.mystery) {
      box.innerHTML = "<h4>Symptom</h4><p>" + esc(sc.symptom) + "</p>" +
        "<h4>Questions to ask yourself</h4>" + listHtml(sc.mystery_hints.concat([
          "Compare LnkCap with LnkSta for every port: speed, width, or both?",
          "Which link differs from the healthy baseline S01?"])) +
        "<h4>Try it in the terminal</h4><p><code>python -m ltssm run " + esc(sc.id) + " --hidden</code> then " +
        "<code>--reveal</code>, or <code>python -m ltssm quiz</code>.</p>" +
        '<p><button type="button" class="btn" id="reveal-btn-2">Reveal the root cause</button></p>';
      $("reveal-btn-2").addEventListener("click", reveal);
      return;
    }
    const stim = sc.faults.concat(sc.commands, sc.settings || []);
    box.innerHTML =
      "<h4>Injected fault / stimulus</h4>" + (stim.length ? listHtml(stim) : "<p>None: healthy run.</p>") +
      (sc.mystery_cause ? "<h4>Root cause in one line</h4><p>" + esc(sc.mystery_cause) + "</p>" : "") +
      "<h4>What to look for</h4><p>" + esc(sc.what_to_look_for) + "</p>" +
      "<h4>What this means</h4><p>" + esc(sc.what_this_means) + "</p>" +
      "<h4>Likely root causes on real hardware</h4>" + listHtml(sc.likely_root_causes) +
      "<h4>How to debug it on real hardware</h4>" + listHtml(sc.how_to_debug);
  }

  function reveal() {
    S.mystery = false;
    $("mystery-toggle").checked = false;
    store("ltssm-mystery", "0");
    fillScenarioSelect();
    renderAll();
  }

  // ------------------------------------------------------------------- replay
  function renderReplay() {
    const n = S.stepTimes.length;
    let idx = 0;
    for (let i = 0; i < n; i++) if (S.stepTimes[i] <= S.t) idx = i;
    $("scrub").value = idx;
    $("scrub-label").textContent = (n ? idx + 1 : 0) + " / " + n;
    const now = S.trace.events.filter((e) => e.kind === "state" && e.t_ns === S.stepTimes[idx]);
    const atEnd = S.t >= S.end;
    let html = '<div class="t">t = ' + fmtMs(S.t) + " ms" + (atEnd ? " (end of run: final state)" : "") + "</div>";
    if (!atEnd) {
      html += now.slice(0, 3).map((e) => "<div><strong>" + esc(e.port) + "</strong> [" + esc(e.link) + "] " +
        esc(e.from) + " &rarr; " + esc(e.to) + " <span class=\"t\">(" + esc(e.speed) + " " + esc(e.width) + ")</span><br>" +
        esc(e.reason) + "</div>").join("");
      if (now.length > 3) html += '<div class="t">+ ' + (now.length - 3) + " more at the same time</div>";
    }
    $("now-event").innerHTML = html;
    const rows = S.trace.topology.links.map((l) => {
      const a = stateAt(l.dsp, S.t), u = l.usp ? stateAt(l.usp, S.t) : null;
      const st = linkStatus(l, S.t);
      return "<tr><td>" + esc(l.name) + '</td><td><span class="fam-chip fam-' + st.group + '"></span>' +
        esc(l.dsp) + ": " + esc(a ? a.to : "-") + "</td><td>" + (u ? esc(l.usp) + ": " + esc(u.to) : "(empty slot)") +
        "</td><td>" + esc(st.group === "Detect" ? "-" : st.detail) + "</td></tr>";
    }).join("");
    $("link-now").innerHTML = "<thead><tr><th>Link</th><th>DSP</th><th>USP</th><th>Speed width</th></tr></thead><tbody>" +
      rows + "</tbody>";
    $("btn-play").textContent = S.playTimer ? "Pause" : "Play";
  }

  function setPlayhead(t, opts) {
    S.t = Math.max(0, Math.min(S.end, t));
    if (opts && opts.follow) ensureVisible(S.t);
    renderTopology();
    drawLanes();
    renderReplay();
    highlightLog(opts && opts.scrollLog);
  }

  function stepTo(dir) {
    const times = S.stepTimes;
    if (!times.length) return false;
    let target = null;
    if (dir > 0) {
      if (S.t >= S.end) return false;
      target = times.find((x) => x > S.t);
      if (target === undefined) { setPlayhead(S.end, { follow: true }); return false; }
    } else {
      for (const x of times) if (x < S.t) target = x;
      if (target === null) target = 0;
    }
    setPlayhead(target, { follow: true, scrollLog: true });
    return true;
  }

  function play() {
    if (S.playTimer) { stopPlay(); return; }
    if (S.t >= S.end) setPlayhead(0, { follow: true });
    const interval = 650 / SPEEDS[S.speedIdx];
    S.playTimer = setInterval(() => { if (!stepTo(1)) stopPlay(); }, interval);
    renderReplay();
  }

  function stopPlay() {
    if (S.playTimer) clearInterval(S.playTimer);
    S.playTimer = null;
    if (S.trace) renderReplay();
  }

  // ----------------------------------------------------------------- controls
  function bindControls() {
    $("scenario-select").addEventListener("change", (e) => loadScenario(e.target.value));
    window.addEventListener("hashchange", () => {
      const id = location.hash.replace("#", "").toUpperCase();
      if (S.trace && id && id !== S.trace.scenario.id) loadScenario(id);
    });
    $("mystery-toggle").addEventListener("change", (e) => {
      S.mystery = e.target.checked;
      store("ltssm-mystery", S.mystery ? "1" : "0");
      fillScenarioSelect();
      if (S.trace) { fillFilters(S.trace); renderAll(); }
    });
    $("reveal-btn").addEventListener("click", reveal);

    $("btn-play").addEventListener("click", play);
    $("btn-next").addEventListener("click", () => { stopPlay(); stepTo(1); });
    $("btn-prev").addEventListener("click", () => { stopPlay(); stepTo(-1); });
    $("btn-restart").addEventListener("click", () => { stopPlay(); setPlayhead(0, { follow: true, scrollLog: true }); });
    $("btn-end").addEventListener("click", () => { stopPlay(); setPlayhead(S.end, { follow: true }); });
    $("speed").addEventListener("input", (e) => {
      S.speedIdx = Number(e.target.value);
      $("speed-label").textContent = SPEEDS[S.speedIdx] + "x";
      if (S.playTimer) { stopPlay(); play(); }
    });
    $("scrub").addEventListener("input", (e) => {
      stopPlay();
      const t = S.stepTimes[Number(e.target.value)];
      if (t !== undefined) setPlayhead(t, { follow: true, scrollLog: true });
    });

    $("zoom").addEventListener("input", (e) => {
      const center = S.t >= S.view.t0 && S.t <= S.view.t1 ? S.t : (S.view.t0 + S.view.t1) / 2;
      const span = spanForSlider(Number(e.target.value));
      const ratio = (center - S.view.t0) / (S.view.t1 - S.view.t0);
      setView(center - ratio * span, span);
    });
    $("pan").addEventListener("input", (e) => {
      const span = S.view.t1 - S.view.t0;
      setView((Number(e.target.value) / 1000) * (S.end - span), span);
    });
    $("zoom-in").addEventListener("click", () => zoomAround(viewFocus(), 0.4));
    $("zoom-out").addEventListener("click", () => zoomAround(viewFocus(), 2.5));
    $("zoom-fit").addEventListener("click", () => setView(0, S.end));
    $("zoom-playhead").addEventListener("click", () => {
      const t = S.t >= S.end ? lastActivity() : S.t;
      setView(t - 60 * 1000, 200 * 1000); // 200 us window around the playhead
    });

    const area = $("lane-area");
    area.addEventListener("wheel", (e) => {
      if (!S.trace) return;
      const rect = area.getBoundingClientRect();
      const t = S.view.t0 + ((e.clientX - rect.left) / rect.width) * (S.view.t1 - S.view.t0);
      if (e.ctrlKey || e.metaKey) {
        e.preventDefault();
        zoomAround(t, e.deltaY > 0 ? 1.25 : 0.8);
      } else if (Math.abs(e.deltaX) > Math.abs(e.deltaY)) {
        e.preventDefault();
        const span = S.view.t1 - S.view.t0;
        setView(S.view.t0 + (e.deltaX / rect.width) * span, span);
      }
    }, { passive: false });
    area.addEventListener("pointerdown", (e) => {
      if (!S.trace || e.button !== 0) return;
      S.drag = { x: e.clientX, t0: S.view.t0, moved: false };
    });
    window.addEventListener("pointermove", (e) => {
      if (!S.drag) return;
      const dx = e.clientX - S.drag.x;
      if (Math.abs(dx) > 4) {
        S.drag.moved = true;
        area.classList.add("dragging");
        hideTip();
        const span = S.view.t1 - S.view.t0;
        setView(S.drag.t0 - (dx / area.clientWidth) * span, span);
      }
    });
    window.addEventListener("pointerup", () => {
      if (S.drag && S.drag.moved) S.suppressClick = true;
      S.drag = null;
      area.classList.remove("dragging");
    });
    const lanes = $("lanes");
    lanes.addEventListener("pointermove", (e) => {
      if (S.drag && S.drag.moved) return;
      const seg = segFromEvent(e);
      if (seg) showTip(e, segTip(seg)); else hideTip();
    });
    lanes.addEventListener("pointerleave", hideTip);
    lanes.addEventListener("click", (e) => {
      if (S.suppressClick) { S.suppressClick = false; return; }
      const seg = segFromEvent(e);
      if (!seg) return;
      stopPlay();
      setPlayhead(seg.t0, { scrollLog: true });
      showTip(e, segTip(seg));
    });

    for (const id of ["f-port", "f-family", "f-kind"]) $(id).addEventListener("change", () => S.trace && renderLog());
    $("f-text").addEventListener("input", () => S.trace && renderLog());
    $("log").addEventListener("click", (e) => {
      const row = e.target.closest("tr[data-i]");
      if (!row) return;
      stopPlay();
      const ev = S.trace.events[Number(row.dataset.i)];
      const span = S.view.t1 - S.view.t0;
      if (ev.t_ns < S.view.t0 || ev.t_ns > S.view.t1) setView(ev.t_ns - span / 2, span);
      setPlayhead(ev.t_ns, {});
    });

    let resizeTimer = null;
    window.addEventListener("resize", () => {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(() => { if (S.trace) drawLanes(); }, 80);
    });
    document.addEventListener("keydown", (e) => {
      if (!S.trace || e.target.matches("input, select, textarea")) return;
      if (e.key === "ArrowRight") { stopPlay(); stepTo(1); e.preventDefault(); }
      if (e.key === "ArrowLeft") { stopPlay(); stepTo(-1); e.preventDefault(); }
      if (e.key === " ") { play(); e.preventDefault(); }
    });
  }

  function viewFocus() {
    return S.t >= S.view.t0 && S.t <= S.view.t1 ? S.t : (S.view.t0 + S.view.t1) / 2;
  }

  function lastActivity() {
    const times = S.stepTimes;
    return times.length ? times[times.length - 1] : 0;
  }

  function segFromEvent(e) {
    const r = e.target.closest ? e.target.closest("rect.bar") : null;
    if (!r) return null;
    const lane = S.lanes[Number(r.dataset.lane)];
    return lane ? lane.segs[Number(r.dataset.seg)] : null;
  }

  document.addEventListener("DOMContentLoaded", init);
})();
