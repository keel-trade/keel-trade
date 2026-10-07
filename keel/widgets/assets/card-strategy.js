/* Strategy card — the editor's block list, at the size the event deserves.
 *
 * Renders the `view` block every strategy-shaped tool carries
 * (mcp-strategy-view PLAN §4.2): header chips (universe / execution /
 * factories) that open in place, the pipeline as the canvas's block list
 * (component blocks with category badges, `key · value` split chips,
 * dashed Parallel boxes with packed branch columns, factory calls, slot
 * store/load/set/extract badges, variable refs, unknown blocks as their
 * repr), the change since the last version as hunks under their branch
 * context, the one-row receipt for a single-param edit, the evidence
 * line, and the fullscreen layout (Configuration · Factories · Pipeline
 * with every param) when the host reports `displayMode() === "fullscreen"`.
 *
 * The rules are the ones the founder ratified from the HRP and the
 * synthetic worst case (PLAN §4.4, decisions.md "The extremes come
 * first"): a budget of 12 blocks (8 under 480 px) spent in reading order,
 * a parallel that does not fit rendered as one counted row, 4 param chips
 * then `+N`, 30 symbols then `+N · all in Keel`, one factory chip or
 * "N factories", and the branch packer — 1 / 2 / 3 columns at < 480 /
 * default / ≥ 680 px with ≥ 3 branches, never more columns than the
 * longest block name fits, each branch into the currently shortest column
 * in declaration order. Nothing widens the card; every collapse names its
 * remainder; every number is a tool result and a missing one is "—".
 *
 * Render-only (D2.2): no fetch, no frame, no formatter of its own — the
 * adapter's `KeelHost.fmt*` helpers are the only number formatting. When
 * the envelope has no `view` (an older server), the card falls back to
 * `metadata` + `metadata.graph` exactly as before.
 *
 * Self-contained: the card's own CSS is injected below into
 * `<style id="keel-card-strategy">` using only the shared card tokens
 * (--bg --tile --fg --muted --line --accent --good --bad --font
 * --font-mono --radius) plus the category hues it defines for both
 * themes. ES5, one IIFE, no globals.
 */
(function () {
  "use strict";
  var H = window.KeelHost;

  // ── The rules (PLAN §4.4) — one place, read by the tests ──────────
  var MAX_CHIPS = 4;
  var INLINE_BUDGET = 12;
  var INLINE_BUDGET_NARROW = 8;
  var NARROW_PX = 480;
  var THREE_COL_PX = 680;
  var INLINE_SYMBOLS = 30;
  var NESTED_ROW_BLOCKS = 6;
  var ROW_DEPTH = 2; // the third nesting level (depth index 2) is always a row
  var CHAR_PX = 7.9;
  var NAME_PAD = 96;
  var MAX_HUNKS = 8;
  var MINUS = "−";
  var DASH = "—";

  // ── Declarations (Q-1707) ─────────────────────────────────────────
  // The JS twin of `keel/tools/outcomes/_declarations.py`. The wire
  // carries the DIFF (`change.declarations = {section:{key:{a,b}}}`,
  // the same block `keel_backtest_compare` puts on its spec diff); this
  // side only labels it. A key absent from `DECL_KEY_LABELS` says its
  // own name — which is what the user wrote in the source.
  var DECL_SECTIONS = ["execution", "globals", "universe"];
  var DECL_SECTION_LABELS = {
    execution: "Execution",
    globals: "Globals",
    universe: "Universe",
  };
  var DECL_KEY_LABELS = {
    buffer_threshold: "buffer",
    buffer_mode: "buffer mode",
    rebalance_method: "method",
    on_change_tolerance: "tolerance",
    min_trade_size: "min order",
  };

  /**
   * A declaration value as a reader wants it (Q-1777). A list — a
   * resolved universe, a symbol set — is its COUNT ("30 assets"), with the
   * list itself behind the universe chip's disclosure; printing the JSON
   * array drew `["AAVE","ARB","AVAX",…` into the card. Absent is the dash.
   */
  function declValue(section, v) {
    if (v == null) return null; // absent stays absent: the renderers dash it
    if (Array.isArray(v)) {
      var n = v.length;
      if (section === "universe") return n + (n === 1 ? " asset" : " assets");
      return n + (n === 1 ? " item" : " items");
    }
    if (v && typeof v === "object" && !isRef(v)) {
      var k = Object.keys(v).length;
      return k + (k === 1 ? " setting" : " settings");
    }
    return fmtVal(v);
  }

  function declRows(declarations) {
    var rows = [];
    if (!declarations || typeof declarations !== "object") return rows;
    DECL_SECTIONS.forEach(function (section) {
      var keys = declarations[section];
      if (!keys || typeof keys !== "object") return;
      Object.keys(keys)
        .sort()
        .forEach(function (key) {
          var pair = keys[key];
          if (!pair || typeof pair !== "object") return;
          rows.push({
            section: section,
            sectionLabel: DECL_SECTION_LABELS[section] || section,
            key: key,
            label: DECL_KEY_LABELS[key] || key,
            a: declValue(section, pair.a),
            b: declValue(section, pair.b),
          });
        });
    });
    return rows;
  }

  // Registry category → hue (mirrors shared/strategy-graph/StrategyCanvas.tsx
  // CATEGORY_COLORS so the card reads as the editor). Unknown → no badge hue.
  var CATEGORY_HUE = {
    data_loader: "blue",
    data_source: "blue",
    universe_filter: "blue",
    data_transform: "cyan",
    signal: "cyan",
    signal_transform: "cyan",
    signal_composer: "cyan",
    indicator: "violet",
    regime_detector: "amber",
    forecast_mapper: "amber",
    forecast_composer: "amber",
    position_sizer: "green",
    position_manager: "green",
    executor: "green",
    execution: "green",
    risk_manager: "rose",
    risk_management: "rose",
    reporter: "",
    slot_op: "",
  };

  // ── CSS (card tokens + category hues, both themes) ────────────────
  var CSS = [
    // The six SERIES hues (--sv-violet … --sv-blue) moved to card.css
    // (Q-1688): the comparison card overlays one curve per run and
    // needs the same six, and a token declared in a card's own injected
    // stylesheet is reachable from that card only. What stays here are
    // the washes and change marks this card alone draws. The
    // `.card-head[hidden]` rule moved with them — every card that draws
    // its own head (or a receipt row) needs it, not just this one.
    ":root{",
    "--sv-violet-bg:rgba(109,79,214,.09);--sv-cyan-bg:rgba(11,127,163,.09);--sv-green-bg:rgba(10,125,67,.09);--sv-amber-bg:rgba(154,106,18,.10);--sv-rose-bg:rgba(180,35,24,.08);--sv-blue-bg:rgba(79,70,229,.08);",
    "--sv-mod:rgba(154,106,18,.10);--sv-add:rgba(10,125,67,.09);--sv-rem:rgba(180,35,24,.06);--sv-hover:rgba(0,0,0,.035)}",
    '@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){',
    "--sv-violet-bg:rgba(185,146,247,.10);--sv-cyan-bg:rgba(57,182,238,.10);--sv-green-bg:rgba(63,207,152,.10);--sv-amber-bg:rgba(240,177,51,.10);--sv-rose-bg:rgba(244,122,122,.10);--sv-blue-bg:rgba(157,151,255,.10);",
    "--sv-mod:rgba(240,177,51,.12);--sv-add:rgba(63,207,152,.12);--sv-rem:rgba(244,122,122,.08);--sv-hover:rgba(255,255,255,.06)}}",
    ':root[data-theme="dark"]{',
    "--sv-violet-bg:rgba(185,146,247,.10);--sv-cyan-bg:rgba(57,182,238,.10);--sv-green-bg:rgba(63,207,152,.10);--sv-amber-bg:rgba(240,177,51,.10);--sv-rose-bg:rgba(244,122,122,.10);--sv-blue-bg:rgba(157,151,255,.10);",
    "--sv-mod:rgba(240,177,51,.12);--sv-add:rgba(63,207,152,.12);--sv-rem:rgba(244,122,122,.08);--sv-hover:rgba(255,255,255,.06)}",
    ':root[data-theme="light"]{',
    "--sv-violet-bg:rgba(109,79,214,.09);--sv-cyan-bg:rgba(11,127,163,.09);--sv-green-bg:rgba(10,125,67,.09);--sv-amber-bg:rgba(154,106,18,.10);--sv-rose-bg:rgba(180,35,24,.08);--sv-blue-bg:rgba(79,70,229,.08);",
    "--sv-mod:rgba(154,106,18,.10);--sv-add:rgba(10,125,67,.09);--sv-rem:rgba(180,35,24,.06);--sv-hover:rgba(0,0,0,.035)}",
    ".sv-card{color:var(--fg);font:var(--t-base)/var(--t-base-lh) var(--font);min-width:0}",
    ".sv-head{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:8px}",
    ".sv-brand{font-weight:700;letter-spacing:.02em;font-size:var(--t-sm)}",
    ".sv-name{font:600 var(--t-lg)/var(--t-lg-lh) var(--font-mono);letter-spacing:-.01em;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:100%;min-width:0;flex:0 1 auto}",
    ".sv-spacer{flex:1 1 0}",
    ".sv-chip{display:inline-flex;align-items:center;gap:5px;font:500 var(--t-micro)/1 var(--font-mono);padding:4px 8px;border:1px solid var(--line);border-radius:100px;color:var(--fg);white-space:nowrap;max-width:100%;overflow:hidden;text-overflow:ellipsis;background:transparent}",
    ".sv-chip.sv-ok{color:var(--good);border-color:var(--good)}",
    ".sv-chip.sv-warn{color:var(--sv-amber);border-color:var(--sv-amber)}",
    ".sv-chip.sv-bad{color:var(--bad);border-color:var(--bad)}",
    ".sv-chip.sv-v{color:var(--muted)}",
    ".sv-chip.sv-btn{cursor:pointer;min-height:26px}",
    ".sv-chip.sv-btn:hover{background:var(--sv-hover)}",
    ".sv-chip.sv-btn:focus-visible{outline:2px solid var(--accent);outline-offset:1px}",
    ".sv-chip .sv-k{color:var(--muted)}",
    ".sv-chip .sv-caret{font-size:var(--t-2xs);color:var(--muted)}",
    // A config chip this save changed (Q-1777): the block-change wash and
    // edge, and old → new inside it.
    ".sv-chip.sv-chg{background:var(--sv-mod);border-color:var(--sv-amber)}",
    ".sv-chip .sv-chgv{display:inline-flex;gap:5px;padding-left:6px;border-left:1px solid var(--line)}",
    ".sv-chip .sv-chgv .sv-old{color:var(--muted);text-decoration:line-through}",
    ".sv-chip .sv-chgv .sv-new{color:var(--fg);font-weight:600}",
    ".sv-cfgrow{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 10px}",
    ".sv-panel{margin:0 0 10px}",
    ".sv-summary{font-size:var(--t-sm);color:var(--fg);margin:-2px 0 10px}",
    ".sv-sect{font:500 var(--t-tiny)/1 var(--font-mono);letter-spacing:.1em;text-transform:uppercase;color:var(--muted);margin:10px 0 6px;display:flex;align-items:center;gap:8px}",
    ".sv-sect .sv-r{margin-left:auto;text-transform:none;letter-spacing:0;font-size:var(--t-micro);cursor:pointer;color:var(--accent);white-space:nowrap;background:none;border:0;padding:0;font-family:var(--font)}",
    ".sv-sect .sv-r:hover{text-decoration:underline}",
    // The flow is INSET and its blocks size to their CONTENT. Stretching
    // every block to the full width made a pipeline read as a stack of
    // identical bars with no horizontal information in it — a block named
    // `ROC` occupied exactly as much room as `TargetTimeframeResampler`.
    // Sizing to content gives the flow a ragged right edge that follows the
    // actual shape of the pipeline, and makes the small plumbing steps
    // (store, load) read as small. The inset leaves a gutter so the blocks
    // sit IN the card rather than spanning it edge to edge.
    ".sv-flow{display:flex;flex-direction:column;gap:4px;min-width:0;padding:0 10px}",
    ".sv-blk{max-width:100%}",
    // The parallel keeps the full width: it holds packed columns, and
    // shrinking it would fight the packer for room.
    // Narrow has no width to spend on a gutter.
    ".sv-narrow .sv-flow{padding:0}",
    // Founder ruling 2026-09-22: no colour on the left edge — "the labels
    // are enough". The category badge carries the phase; the block itself
    // stays neutral, and the content-width shaping below is what gives the
    // flow its structure.
    ".sv-blk{border:1px solid var(--line);border-radius:9px;padding:6px 10px;display:flex;flex-direction:column;gap:5px;background:var(--tile);min-width:0}",
    ".sv-blk:hover{background:var(--sv-hover)}",
    ".sv-row{display:flex;align-items:center;gap:8px;min-width:0}",
    ".sv-c{font:500 var(--t-sm)/var(--t-sm-lh) var(--font-mono);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0;flex:0 1 auto}",
    ".sv-ico{width:14px;height:14px;color:var(--muted);flex:none}",
    ".sv-badge{display:inline-flex;align-items:center;font:700 var(--t-2xs)/1 var(--font-mono);letter-spacing:.06em;text-transform:uppercase;padding:3px 6px;border-radius:100px;border:1px solid var(--line);color:var(--muted);white-space:nowrap;flex:none;background:transparent}",
    ".sv-badge.sv-violet{color:var(--sv-violet);border-color:transparent;background:var(--sv-violet-bg)}",
    ".sv-badge.sv-cyan{color:var(--sv-cyan);border-color:transparent;background:var(--sv-cyan-bg)}",
    ".sv-badge.sv-green{color:var(--sv-green);border-color:transparent;background:var(--sv-green-bg)}",
    ".sv-badge.sv-amber{color:var(--sv-amber);border-color:transparent;background:var(--sv-amber-bg)}",
    ".sv-badge.sv-rose{color:var(--sv-rose);border-color:transparent;background:var(--sv-rose-bg)}",
    ".sv-badge.sv-blue{color:var(--sv-blue);border-color:transparent;background:var(--sv-blue-bg)}",
    ".sv-badge.sv-plain{font-weight:500;text-transform:none;letter-spacing:0;font-size:var(--t-tiny)}",
    ".sv-badge.sv-open{cursor:pointer;margin-left:auto;color:var(--accent)}",
    ".sv-badge.sv-open:focus-visible{outline:2px solid var(--accent);outline-offset:1px}",
    ".sv-chips{display:flex;flex-wrap:wrap;gap:4px;min-width:0}",
    ".sv-sc{display:inline-flex;align-items:center;font:var(--t-micro)/1 var(--font-mono);border:1px solid var(--line);border-radius:6px;overflow:hidden;max-width:100%}",
    ".sv-sc .sv-k{padding:4px 6px;color:var(--muted);background:var(--tile);white-space:nowrap}",
    ".sv-sc .sv-v{padding:4px 6px;color:var(--fg);max-width:150px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}",
    ".sv-sc .sv-v .sv-old{color:var(--muted);text-decoration:line-through;margin-right:5px}",
    ".sv-sc .sv-v .sv-new{color:var(--fg);font-weight:600}",
    ".sv-sc .sv-ref{color:var(--muted);font-style:italic}",
    ".sv-sc.sv-more{cursor:pointer;color:var(--accent);padding:4px 7px;border-style:dashed}",
    ".sv-sc.sv-more:focus-visible{outline:2px solid var(--accent);outline-offset:1px}",
    ".sv-par{border:1px dashed var(--line);border-radius:9px;padding:6px 8px 8px;min-width:0}",
    ".sv-par>.sv-row{margin-bottom:4px}",
    ".sv-branches{display:grid;gap:8px;min-width:0}",
    ".sv-branches>.sv-col{display:flex;flex-direction:column;gap:8px;min-width:0}",
    ".sv-branch{min-width:0}",
    ".sv-branch .sv-bn{font:500 var(--t-2xs)/1 var(--font-mono);letter-spacing:.1em;text-transform:uppercase;color:var(--muted);padding:3px 2px 6px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}",
    ".sv-branch>.sv-flow{border-left:2px solid var(--line);padding-left:8px}",
    ".sv-sym{display:flex;flex-wrap:wrap;gap:4px;padding:2px 0 8px}",
    ".sv-sym span{font:var(--t-micro)/var(--t-micro-lh) var(--font-mono);padding:3px 7px;border-radius:6px;background:var(--tile);border:1px solid var(--line);color:var(--fg)}",
    ".sv-sym .sv-symmore{cursor:pointer;color:var(--accent);border-style:dashed;background:transparent}",
    ".sv-morerow{border:1px dashed var(--line);border-radius:9px;padding:6px 10px;font:500 var(--t-xs)/var(--t-xs-lh) var(--font);color:var(--accent);cursor:pointer;text-align:center;background:transparent;width:100%}",
    ".sv-morerow:hover{background:var(--sv-hover)}",
    ".sv-morerow:focus-visible{outline:2px solid var(--accent);outline-offset:1px}",
    ".sv-hidden{display:none !important}",
    ".sv-blk.sv-mod{background:var(--sv-mod);border-color:var(--sv-amber)}",
    ".sv-blk.sv-add{background:var(--sv-add);border-color:var(--sv-green)}",
    ".sv-blk.sv-rem{background:var(--sv-rem);border-color:var(--sv-rose)}",
    ".sv-blk.sv-rem .sv-c{text-decoration:line-through;color:var(--muted)}",
    ".sv-mark{font:700 var(--t-xs)/1 var(--font-mono);width:10px;text-align:center;flex:none}",
    ".sv-mark.sv-m{color:var(--sv-amber)}.sv-mark.sv-a{color:var(--sv-green)}.sv-mark.sv-d{color:var(--sv-rose)}",
    ".sv-path{font:var(--t-micro)/var(--t-micro-lh) var(--font-mono);color:var(--muted);min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}",
    ".sv-path b{color:var(--fg);font-weight:500}",
    ".sv-ctx{color:var(--muted);font:var(--t-micro)/var(--t-micro-lh) var(--font-mono);padding:2px 2px}",
    ".sv-ev{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:10px 0 2px;font-size:var(--t-sm);color:var(--muted)}",
    ".sv-ev .sv-n{font-family:var(--font-mono);color:var(--fg);font-variant-numeric:tabular-nums}",
    ".sv-ev .sv-n.sv-g{color:var(--good)}.sv-ev .sv-n.sv-b{color:var(--bad)}",
    // Another version's run (ChatGPT R4 #5): the numbers step back to the
    // muted ink so they cannot read as the version on screen.
    ".sv-ev.sv-stale .sv-n,.sv-ev.sv-stale .sv-n.sv-g,.sv-ev.sv-stale .sv-n.sv-b{color:var(--muted)}",
    ".sv-none{font-size:var(--t-sm);color:var(--muted)}",
    // No rule above the footer's actions either (Q-1779).
    ".sv-foot{display:flex;align-items:center;gap:14px;margin-top:12px;flex-wrap:wrap}",
    ".sv-link{font:500 var(--t-sm)/var(--t-sm-lh) var(--font);color:var(--accent);text-decoration:none;cursor:pointer;background:none;border:0;padding:0;min-height:36px;display:inline-flex;align-items:center}",
    ".sv-link:hover{text-decoration:underline}",
    ".sv-link:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:4px}",
    ".sv-status{font-size:var(--t-xs);color:var(--muted);margin-left:auto}",
    ".sv-card.sv-receipt .sv-head{margin-bottom:0}",
    ".sv-card.sv-receipt .sv-row2{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-top:6px;font-size:var(--t-sm)}",
    ".sv-fsbar{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin:0 0 6px}",
    ".sv-seg{display:inline-flex;border:1px solid var(--line);border-radius:8px;overflow:hidden;margin-left:8px}",
    ".sv-seg button{font:500 var(--t-xs)/var(--t-xs-lh) var(--font);padding:4px 10px;border:0;background:transparent;color:var(--muted);cursor:pointer}",
    ".sv-seg button.sv-on{background:var(--accent);color:#fff}",
    ".sv-seg button:focus-visible{outline:2px solid var(--accent);outline-offset:-2px}",
    ".sv-codestub{font:var(--t-xs)/1.6 var(--font-mono);padding:10px 12px;border:1px solid var(--line);border-radius:9px;background:var(--tile);color:var(--muted)}",
    ".sv-src{font:var(--t-xs)/1.65 var(--font-mono);margin:0;padding:10px 12px;border:1px solid var(--line);border-radius:9px;background:var(--tile);color:var(--fg);overflow-x:auto;white-space:pre;tab-size:2}",
    ".sv-srcnote{font:var(--t-tiny)/var(--t-tiny-lh) var(--font);color:var(--muted);margin-top:6px}",

    ".sv-fac-steps{margin-left:22px}",
    ".sv-narrow .sv-chip.sv-btn{min-height:34px;padding:6px 10px}",
    ".sv-narrow .sv-sect .sv-r{min-height:30px;display:inline-flex;align-items:center}",
    ".sv-narrow .sv-link{min-height:44px}",
    ".sv-narrow .sv-badge.sv-open{min-height:34px;padding:0 8px}",
  ].join("\n");

  function injectCss() {
    var id = "keel-card-strategy";
    if (document.getElementById(id)) return;
    var node = document.createElement("style");
    node.id = id;
    node.textContent = CSS;
    document.head.appendChild(node);
  }

  // ── DOM helpers ───────────────────────────────────────────────────
  function el(tag, cls, text) {
    return H.el(tag, cls, text);
  }

  function onActivate(node, fn) {
    node.addEventListener("click", function (e) {
      e.preventDefault();
      fn();
    });
    node.addEventListener("keydown", function (e) {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        fn();
      }
    });
  }

  function button(cls, text) {
    var b = el("button", cls, text);
    b.type = "button";
    return b;
  }

  function notify() {
    if (typeof H.notifySize === "function") H.notifySize();
    else if (typeof H.reportSize === "function") H.reportSize();
  }

  function svgIcon(kind) {
    var ns = "http://www.w3.org/2000/svg";
    var svg = document.createElementNS(ns, "svg");
    svg.setAttribute("class", "sv-ico");
    svg.setAttribute("viewBox", "0 0 16 16");
    svg.setAttribute("fill", "none");
    svg.setAttribute("stroke", "currentColor");
    svg.setAttribute("stroke-width", "1.4");
    svg.setAttribute("aria-hidden", "true");
    var paths =
      kind === "par"
        ? [
            ["path", { d: "M4 3v10M4 6c0 3 8 1 8 5" }],
            ["circle", { cx: "4", cy: "3", r: "1.3" }],
            ["circle", { cx: "12", cy: "4", r: "1.3" }],
            ["circle", { cx: "4", cy: "13", r: "1.3" }],
          ]
        : [["path", { d: "M2 14V8l3-2v2l3-2v2l3-2v8H2z" }]];
    paths.forEach(function (p) {
      var node = document.createElementNS(ns, p[0]);
      Object.keys(p[1]).forEach(function (k) {
        node.setAttribute(k, p[1][k]);
      });
      svg.appendChild(node);
    });
    return svg;
  }

  // ── Layout facts ──────────────────────────────────────────────────
  function cardWidth() {
    var root = document.getElementById("root") || document.body;
    var w = root ? root.getBoundingClientRect().width : 0;
    return w || document.documentElement.clientWidth || 0;
  }

  function displayMode() {
    var mode = null;
    if (typeof H.displayMode === "function") mode = H.displayMode();
    if (!mode)
      mode = document.documentElement.getAttribute("data-display-mode");
    return mode === "fullscreen" ? "fullscreen" : "inline";
  }

  function hostListsFullscreen() {
    if (typeof H.displayMode !== "function") return false;
    var modes = H.availableDisplayModes;
    if (typeof modes === "function") modes = modes();
    return Array.isArray(modes) && modes.indexOf("fullscreen") >= 0;
  }

  // ── Graph walking (keel-api graph shape) ──────────────────────────
  function branchesOf(b) {
    return b &&
      b.type === "parallel" &&
      b.branches &&
      typeof b.branches === "object"
      ? b.branches
      : null;
  }

  function branchNames(b) {
    var br = branchesOf(b);
    return br ? Object.keys(br) : [];
  }

  function countBlocks(list) {
    if (!Array.isArray(list)) return 0;
    var n = 0;
    list.forEach(function (b) {
      if (!b || typeof b !== "object") return;
      var br = branchesOf(b);
      if (br) {
        Object.keys(br).forEach(function (k) {
          n += countBlocks(br[k]);
        });
      } else n += 1;
    });
    return n;
  }

  function blockName(b) {
    if (!b || typeof b !== "object") return "";
    if (b.type === "component") return String(b.component || "");
    if (b.type === "factory_call")
      return String(b.factoryName || b.factory || "");
    if (
      b.type === "slot_store" ||
      b.type === "slot_load" ||
      b.type === "slot_store_value"
    )
      return String(b.slotName || "");
    if (b.type === "slot_extract") return String(b.extractKey || "");
    if (b.type === "variable_ref") return String(b.variableName || "");
    return String(b.type || "");
  }

  function longestName(list) {
    var m = 0;
    if (!Array.isArray(list)) return 0;
    list.forEach(function (b) {
      var br = branchesOf(b);
      if (br) {
        Object.keys(br).forEach(function (k) {
          m = Math.max(m, longestName(br[k]));
        });
      } else m = Math.max(m, blockName(b).length);
    });
    return m;
  }

  // ── Values ────────────────────────────────────────────────────────
  function isRef(v) {
    return (
      v &&
      typeof v === "object" &&
      !Array.isArray(v) &&
      Object.keys(v).length === 1 &&
      typeof v.$ref === "string"
    );
  }

  function fmtVal(v) {
    if (v == null) return DASH;
    if (typeof v === "string") return v;
    if (typeof v === "boolean") return v ? "true" : "false";
    if (typeof v === "number") return String(v);
    if (isRef(v)) return v.$ref;
    try {
      return JSON.stringify(v);
    } catch (e) {
      return String(v);
    }
  }

  function valueNode(v) {
    var span = el("span", isRef(v) ? "sv-ref" : null, fmtVal(v));
    return span;
  }

  // ── Chips ─────────────────────────────────────────────────────────
  // chip(k, v) — `key · value`; v as [old, new] draws old → new.
  function chip(k, v) {
    var s = el("span", "sv-sc");
    s.appendChild(el("span", "sv-k", k));
    var val = el("span", "sv-v");
    if (Array.isArray(v) && v.length === 2 && v.__delta) {
      val.appendChild(el("span", "sv-old", fmtVal(v[0])));
      val.appendChild(el("span", "sv-new", fmtVal(v[1])));
      val.title = fmtVal(v[0]) + " → " + fmtVal(v[1]);
    } else {
      val.appendChild(valueNode(v));
      val.title = fmtVal(v);
    }
    s.appendChild(val);
    return s;
  }

  function delta(oldV, newV) {
    var pair = [oldV, newV];
    pair.__delta = true;
    return pair;
  }

  // chipsFor(params, changed, hidden) — up to MAX_CHIPS chips then `+N`
  // opening the rest in place; changed params (old → new) come first.
  function chipsFor(params, changed, hidden) {
    var p = params && typeof params === "object" ? params : {};
    var keys = [];
    var seen = {};
    if (changed) {
      Object.keys(changed).forEach(function (k) {
        keys.push(k);
        seen[k] = true;
      });
    }
    Object.keys(p).forEach(function (k) {
      if (!seen[k]) keys.push(k);
    });
    if (!keys.length) return null;
    var row = el("div", "sv-chips" + (hidden ? " sv-p sv-hidden" : ""));
    var valueFor = function (k) {
      if (changed && changed[k]) return delta(changed[k][0], changed[k][1]);
      return p[k];
    };
    keys.slice(0, MAX_CHIPS).forEach(function (k) {
      row.appendChild(chip(k, valueFor(k)));
    });
    if (keys.length > MAX_CHIPS) {
      var more = el("span", "sv-sc sv-more", "+" + (keys.length - MAX_CHIPS));
      more.tabIndex = 0;
      more.setAttribute("role", "button");
      more.setAttribute(
        "aria-label",
        "Show " + (keys.length - MAX_CHIPS) + " more parameters",
      );
      onActivate(more, function () {
        keys.slice(MAX_CHIPS).forEach(function (k) {
          row.insertBefore(chip(k, valueFor(k)), more);
        });
        more.parentNode.removeChild(more);
        notify();
      });
      row.appendChild(more);
    }
    return row;
  }

  // ── Change normalisation (PLAN §4.2 `change`, keyed by block id) ──
  function idOf(x) {
    if (typeof x === "string") return x;
    if (x && typeof x === "object" && x.id != null) return String(x.id);
    return null;
  }

  function entriesOf(x) {
    // list of ids / dicts, or an object keyed by id → [{id, ...}]
    var out = [];
    if (Array.isArray(x)) {
      x.forEach(function (e) {
        var id = idOf(e);
        if (id == null) return;
        out.push(typeof e === "object" ? e : { id: id });
      });
    } else if (x && typeof x === "object") {
      Object.keys(x).forEach(function (id) {
        var e = x[id];
        var entry = e && typeof e === "object" ? e : {};
        var copy = {};
        Object.keys(entry).forEach(function (k) {
          copy[k] = entry[k];
        });
        copy.id = id;
        out.push(copy);
      });
    }
    return out;
  }

  function normalizeChange(change) {
    if (!change || typeof change !== "object") return null;
    var added = {};
    entriesOf(change.added).forEach(function (e) {
      added[e.id] = true;
    });
    var removed = entriesOf(change.removed);
    var changed = {};
    entriesOf(change.changed).forEach(function (e) {
      var pc = e.param_changes || e.params || e.changes || e;
      var map = {};
      Object.keys(pc).forEach(function (k) {
        if (k === "id" || k === "path" || k === "component") return;
        var pair = pc[k];
        if (Array.isArray(pair) && pair.length === 2) map[k] = pair;
        else if (
          pair &&
          typeof pair === "object" &&
          "old" in pair &&
          "new" in pair
        )
          map[k] = [pair.old, pair.new];
      });
      changed[e.id] = map;
    });
    var decl = declRows(change.declarations);
    var touched = change.touched;
    if (touched == null)
      touched =
        Object.keys(added).length +
        removed.length +
        Object.keys(changed).length +
        decl.length;
    return {
      added: added,
      removed: removed,
      changed: changed,
      decl: decl,
      touched: touched,
      total: change.total != null ? change.total : null,
      summary:
        typeof change.summary_text === "string" ? change.summary_text : null,
      fromVersion: change.from_version != null ? change.from_version : null,
      toVersion: change.to_version != null ? change.to_version : null,
    };
  }

  function markFor(id, chg) {
    if (!chg || id == null) return null;
    if (chg.changed[id]) return "mod";
    if (chg.added[id]) return "add";
    return null;
  }

  // Structure with the removed blocks re-inserted (struck through) so the
  // marked full form shows what left: at the end of the branch named by
  // the entry's `path`, else at the end of the pipeline.
  function withRemoved(blocks, chg) {
    if (!chg || !chg.removed.length) return blocks;
    var copy = cloneBlocks(blocks);
    chg.removed.forEach(function (r) {
      var b = {};
      Object.keys(r).forEach(function (k) {
        if (k !== "path") b[k] = r[k];
      });
      if (!b.type) b.type = b.component ? "component" : "unknown";
      b.__removed = true;
      var target = null;
      var path = Array.isArray(r.path) ? r.path : [];
      if (path.length) target = branchList(copy, path);
      (target || copy).push(b);
    });
    return copy;
  }

  function cloneBlocks(list) {
    return (Array.isArray(list) ? list : []).map(function (b) {
      if (!b || typeof b !== "object") return b;
      var c = {};
      Object.keys(b).forEach(function (k) {
        c[k] = b[k];
      });
      var br = branchesOf(b);
      if (br) {
        c.branches = {};
        Object.keys(br).forEach(function (k) {
          c.branches[k] = cloneBlocks(br[k]);
        });
      }
      return c;
    });
  }

  function branchList(list, path) {
    var cur = list;
    for (var i = 0; i < path.length; i++) {
      var found = null;
      for (var j = 0; j < cur.length; j++) {
        var br = branchesOf(cur[j]);
        if (br && br[path[i]]) {
          found = br[path[i]];
          break;
        }
      }
      if (!found) return null;
      cur = found;
    }
    return cur;
  }

  // ── Blocks ────────────────────────────────────────────────────────
  // opts: {full, marks, paramsHidden, categories, chg}
  function categoryOf(b, opts) {
    if (b.category) return String(b.category);
    var cats = opts.categories || {};
    var name = b.component;
    return name && cats[name] ? String(cats[name]) : null;
  }

  function markNode(kind) {
    var m = el(
      "span",
      "sv-mark" +
        (kind === "mod"
          ? " sv-m"
          : kind === "add"
            ? " sv-a"
            : kind === "rem"
              ? " sv-d"
              : ""),
      kind === "mod" ? "~" : kind === "add" ? "+" : kind === "rem" ? MINUS : "",
    );
    m.setAttribute(
      "aria-label",
      kind === "mod"
        ? "changed"
        : kind === "add"
          ? "added"
          : kind === "rem"
            ? "removed"
            : "",
    );
    return m;
  }

  function block(b, opts, depth, forced) {
    depth = depth || 0;
    if (!b || typeof b !== "object") return unknownBlock(b, opts);
    if (branchesOf(b)) {
      var count = countBlocks([b]);
      if (
        !forced &&
        !opts.full &&
        (depth >= ROW_DEPTH || (depth >= 1 && count > NESTED_ROW_BLOCKS))
      )
        return parRow(b, opts, depth);
      return parallel(b, opts, depth);
    }
    var mark = b.__removed ? "rem" : markFor(idOf(b), opts.chg);
    var d = el("div", "sv-blk" + (mark ? " sv-" + mark : ""));
    if (b.id != null) d.setAttribute("data-id", String(b.id));
    d.setAttribute("data-type", String(b.type || "unknown"));
    var r = el("div", "sv-row");
    if (opts.marks) r.appendChild(markNode(mark));
    d.appendChild(r);
    var name;
    if (b.type === "factory_call") {
      r.appendChild(svgIcon("fac"));
      name = el("span", "sv-c", blockName(b));
      name.title = blockName(b);
      r.appendChild(name);
      r.appendChild(el("span", "sv-badge sv-plain", "call"));
      var args = chipsFor(
        b.factoryArgs || b.args,
        null,
        opts.paramsHidden && !mark,
      );
      if (args) d.appendChild(args);
      return d;
    }
    if (
      b.type === "slot_store" ||
      b.type === "slot_load" ||
      b.type === "slot_store_value" ||
      b.type === "slot_extract"
    ) {
      name = el("span", "sv-c", blockName(b));
      name.title = blockName(b);
      r.appendChild(name);
      if (b.type === "slot_store_value" && b.slotValue !== undefined)
        r.appendChild(el("span", "sv-path", "= " + fmtVal(b.slotValue)));
      var kind =
        b.type === "slot_store"
          ? "store"
          : b.type === "slot_load"
            ? "load"
            : b.type === "slot_store_value"
              ? "set"
              : "extract";
      r.appendChild(
        el(
          "span",
          "sv-badge sv-plain " + (kind === "load" ? "sv-blue" : "sv-green"),
          kind,
        ),
      );
      return d;
    }
    if (b.type === "variable_ref") {
      name = el("span", "sv-c", blockName(b));
      name.title = blockName(b);
      r.appendChild(name);
      r.appendChild(el("span", "sv-badge sv-plain", "variable"));
      return d;
    }
    if (b.type !== "component") return unknownBlock(b, opts, d, r);
    name = el("span", "sv-c", blockName(b));
    name.title = blockName(b);
    r.appendChild(name);
    var cat = categoryOf(b, opts);
    if (cat) {
      var hue = CATEGORY_HUE[cat];
      r.appendChild(
        el(
          "span",
          "sv-badge" + (hue ? " sv-" + hue : ""),
          cat.replace(/_/g, " "),
        ),
      );
    }
    var changed =
      opts.chg && b.id != null ? opts.chg.changed[String(b.id)] : null;
    var ch = chipsFor(b.params, changed, opts.paramsHidden && !mark);
    if (ch) d.appendChild(ch);
    return d;
  }

  function unknownBlock(b, opts, d, r) {
    d = d || el("div", "sv-blk");
    r = r || el("div", "sv-row");
    if (!r.parentNode) d.appendChild(r);
    var repr;
    try {
      repr = JSON.stringify(b);
    } catch (e) {
      repr = String(b);
    }
    if (repr && repr.length > 120) repr = repr.slice(0, 117) + "…";
    var name = el(
      "span",
      "sv-c",
      b && typeof b === "object" && b.type ? String(b.type) : "unknown",
    );
    r.appendChild(name);
    r.appendChild(el("span", "sv-badge sv-plain", "unknown"));
    var p = el("span", "sv-path", repr);
    p.title = repr;
    d.appendChild(p);
    return d;
  }

  function parallel(b, opts, depth) {
    var names = branchNames(b);
    var par = el("div", "sv-par");
    if (b.id != null) par.setAttribute("data-id", String(b.id));
    var hr = el("div", "sv-row");
    hr.appendChild(svgIcon("par"));
    hr.appendChild(el("span", "sv-c", "Parallel"));
    hr.appendChild(el("span", "sv-badge sv-plain", names.length + " branches"));
    par.appendChild(hr);
    var br = el("div", "sv-branches");
    br.setAttribute("data-nested", depth >= 1 ? "1" : "");
    br.setAttribute("data-longest", String(longestName([b])));
    names.forEach(function (n) {
      var bd = el("div", "sv-branch");
      bd.setAttribute("data-branch", n);
      var bn = el("div", "sv-bn", n);
      bn.title = n;
      bd.appendChild(bn);
      var fl = el("div", "sv-flow");
      (b.branches[n] || []).forEach(function (x) {
        fl.appendChild(block(x, opts, depth + 1));
      });
      bd.appendChild(fl);
      br.appendChild(bd);
    });
    par.appendChild(br);
    return par;
  }

  // One row: "Parallel · n branches · m blocks", counted branch chips, "open".
  function parRow(b, opts, depth) {
    var names = branchNames(b);
    var count = countBlocks([b]);
    var row = el("div", "sv-blk sv-parrow");
    if (b.id != null) row.setAttribute("data-id", String(b.id));
    row.setAttribute("data-type", "parallel_row");
    var r = el("div", "sv-row");
    r.appendChild(svgIcon("par"));
    r.appendChild(el("span", "sv-c", "Parallel"));
    r.appendChild(
      el(
        "span",
        "sv-badge sv-plain",
        names.length + " branches · " + count + " blocks",
      ),
    );
    var open = el("span", "sv-badge sv-plain sv-open", "open");
    open.tabIndex = 0;
    open.setAttribute("role", "button");
    open.setAttribute(
      "aria-label",
      "Open parallel with " + names.length + " branches",
    );
    r.appendChild(open);
    row.appendChild(r);
    var ch = el("div", "sv-chips");
    names.forEach(function (n) {
      ch.appendChild(chip(n, countBlocks(b.branches[n])));
    });
    row.appendChild(ch);
    onActivate(open, function () {
      var drawn = block(b, opts, depth, true);
      row.parentNode.replaceChild(drawn, row);
      packAll();
      notify();
    });
    return row;
  }

  // flowOf(list, opts, budget) — the block budget spent in reading order;
  // a parallel that does not fit the remaining budget is one row; then
  // "Show N more blocks".
  function flowOf(list, opts, budget) {
    var fl = el("div", "sv-flow");
    list = Array.isArray(list) ? list : [];
    if (!budget) {
      list.forEach(function (b) {
        fl.appendChild(block(b, opts, 0));
      });
      return fl;
    }
    var left = budget;
    var i = 0;
    for (; i < list.length && left > 0; i++) {
      var b = list[i];
      var cost = countBlocks([b]);
      if (branchesOf(b) && cost > left) {
        fl.appendChild(parRow(b, opts, 0));
        left -= 1;
      } else {
        fl.appendChild(block(b, opts, 0));
        left -= cost;
      }
    }
    if (i < list.length) {
      var rest = list.slice(i);
      var n = countBlocks(rest);
      var more = button("sv-morerow", "Show " + n + " more blocks");
      onActivate(more, function () {
        rest.forEach(function (b) {
          fl.insertBefore(block(b, opts, 0), more);
        });
        fl.removeChild(more);
        packAll();
        notify();
      });
      fl.appendChild(more);
    }
    return fl;
  }

  // ── Factories ─────────────────────────────────────────────────────
  function factorySteps(f) {
    return Array.isArray(f.body)
      ? f.body
      : Array.isArray(f.steps)
        ? f.steps
        : [];
  }

  function factoryStepCount(f) {
    if (typeof f.steps === "number") return f.steps;
    return countBlocks(factorySteps(f));
  }

  function factoryParamNames(f) {
    if (!Array.isArray(f.params)) return [];
    return f.params.map(function (p) {
      return typeof p === "string"
        ? p
        : p && p.name != null
          ? String(p.name)
          : "";
    });
  }

  function factoryBlock(f, opts) {
    var d = el("div", "sv-blk sv-factory");
    var r = el("div", "sv-row");
    r.appendChild(svgIcon("fac"));
    var name = el("span", "sv-c", String(f.name || ""));
    name.title = String(f.name || "");
    r.appendChild(name);
    r.appendChild(
      el(
        "span",
        "sv-badge sv-plain",
        "factory · " + factoryStepCount(f) + " steps",
      ),
    );
    var pn = factoryParamNames(f);
    if (pn.length)
      r.appendChild(el("span", "sv-path", "(" + pn.join(", ") + ")"));
    d.appendChild(r);
    var steps = factorySteps(f);
    if (steps.length) {
      var fl = flowOf(
        steps,
        {
          full: true,
          marks: false,
          paramsHidden: false,
          categories: opts.categories,
          chg: null,
        },
        0,
      );
      fl.className += " sv-fac-steps";
      d.appendChild(fl);
    }
    return d;
  }

  function factoryList(factories, opts) {
    var fl = el("div", "sv-flow");
    factories.forEach(function (f) {
      var d = el("div", "sv-blk");
      var r = el("div", "sv-row");
      r.appendChild(svgIcon("fac"));
      var name = el("span", "sv-c", String(f.name || ""));
      name.title = String(f.name || "");
      r.appendChild(name);
      r.appendChild(
        el("span", "sv-badge sv-plain", factoryStepCount(f) + " steps"),
      );
      var pn = factoryParamNames(f);
      if (pn.length)
        r.appendChild(el("span", "sv-path", "(" + pn.join(", ") + ")"));
      if (factorySteps(f).length) {
        var o = el("span", "sv-badge sv-plain sv-open", "open");
        o.tabIndex = 0;
        o.setAttribute("role", "button");
        onActivate(o, function () {
          d.parentNode.replaceChild(factoryBlock(f, opts), d);
          packAll();
          notify();
        });
        r.appendChild(o);
      }
      d.appendChild(r);
      fl.appendChild(d);
    });
    return fl;
  }

  // ── The packer (branch columns) ───────────────────────────────────
  function columnsFor(width, n, nested, longest) {
    var k = nested
      ? 1
      : width < NARROW_PX
        ? 1
        : width >= THREE_COL_PX && n >= 3
          ? Math.min(3, n)
          : Math.min(2, n);
    var need = longest * CHAR_PX + NAME_PAD;
    while (k > 1 && (width - 40) / k < need) k--; // fewer columns rather than a truncated name
    return Math.max(1, k);
  }

  function pack(br) {
    var branches = Array.prototype.slice.call(
      br.querySelectorAll(":scope > .sv-branch, :scope > .sv-col > .sv-branch"),
    );
    if (!branches.length) return;
    var w = cardWidth();
    if (!w) return;
    var k = columnsFor(
      w,
      branches.length,
      br.getAttribute("data-nested") === "1",
      parseInt(br.getAttribute("data-longest") || "0", 10),
    );
    while (br.firstChild) br.removeChild(br.firstChild);
    br.style.gridTemplateColumns = "repeat(" + k + ",minmax(0,1fr))";
    br.setAttribute("data-columns", String(k));
    var cols = [];
    for (var i = 0; i < k; i++) {
      var c = el("div", "sv-col");
      cols.push(c);
      br.appendChild(c);
    }
    // Measure each branch at column width, then place it in the currently
    // shortest column (declaration order).
    branches.forEach(function (b) {
      cols[0].appendChild(b);
    });
    var hs = branches.map(function (b) {
      return b.getBoundingClientRect().height;
    });
    var tot = cols.map(function () {
      return 0;
    });
    branches.forEach(function (b, i) {
      var j = 0;
      for (var t = 1; t < k; t++) if (tot[t] < tot[j] - 0.5) j = t;
      cols[j].appendChild(b);
      tot[j] += hs[i] + 8;
    });
  }

  function packAll() {
    var all = document.querySelectorAll(".sv-branches");
    for (var i = 0; i < all.length; i++) pack(all[i]);
  }

  // ── The view (with the legacy metadata fallback) ──────────────────
  function cap(s) {
    s = String(s || "");
    return s ? s.charAt(0).toUpperCase() + s.slice(1) : s;
  }

  function clockLabel(g) {
    if (!g || typeof g !== "object" || !g.target_timeframe) return null;
    return (
      String(g.target_timeframe) + (g.bar_offset ? " @ " + g.bar_offset : "")
    );
  }

  function universeFromGraph(u) {
    if (!u || typeof u !== "object") return null;
    var mode = String(u.mode || "manual");
    var resolved = Array.isArray(u.resolved) ? u.resolved : null;
    var symbols = Array.isArray(u.symbols) ? u.symbols : null;
    var list = resolved && resolved.length ? resolved : symbols || [];
    var total =
      u.resolved_total != null
        ? Number(u.resolved_total)
        : u.top_n != null && !list.length
          ? Number(u.top_n)
          : list.length;
    var label;
    if (mode === "manual")
      label =
        list.length && list.length <= 4 ? list.join(", ") : "Manual basket";
    else if (mode === "top_volume")
      label = "Top " + (u.top_n != null ? u.top_n : "N") + " by volume";
    else if (mode === "category")
      label =
        "Categories" +
        (Array.isArray(u.categories) && u.categories.length
          ? ": " + u.categories.join(", ")
          : "");
    else label = mode.replace(/_/g, " ");
    var market = u.market ? String(u.market) : null;
    if (market === "perp") label += " · HL perps";
    else if (market) label += " · " + market;
    return { label: label, total: total, symbols: list };
  }

  function executionFromGraph(ex) {
    if (!ex || typeof ex !== "object") return null;
    var label;
    var reb = ex.rebalance ? String(ex.rebalance) : null;
    if (reb === "buffered")
      label =
        "Buffered" +
        (ex.buffer_threshold != null ? " " + ex.buffer_threshold : "");
    else if (reb) label = cap(reb.replace(/_/g, " "));
    else label = "Execution";
    return { label: label, params: ex };
  }

  function headerFromGraph(graph) {
    var factories = Array.isArray(graph.factories) ? graph.factories : [];
    return {
      clock: clockLabel(graph.globals || graph.globals_),
      universe: universeFromGraph(graph.universe),
      execution: executionFromGraph(graph.execution),
      factories: factories.map(function (f) {
        return {
          name: f.name,
          steps: countBlocks(f.body || f.steps),
          params: factoryParamNames(f),
          body: f.body || f.steps,
        };
      }),
    };
  }

  function pick(m, keys) {
    for (var i = 0; i < keys.length; i++)
      if (m && m[keys[i]] != null) return m[keys[i]];
    return null;
  }

  function evidenceFromMeta(meta) {
    var bm = meta.latest_backtest_metrics;
    if (!bm || typeof bm !== "object") return null;
    var dd = pick(bm, ["max_drawdown_pct", "max_drawdown"]);
    return {
      version: meta.latest_backtest_sequence,
      window: {
        start: meta.latest_backtest_start_date,
        end: meta.latest_backtest_end_date,
      },
      sharpe: pick(bm, ["sharpe_ratio", "sharpe"]),
      total_return_pct: pick(bm, ["total_return_pct", "total_return"]),
      max_drawdown_pct: dd == null ? null : -Math.abs(Number(dd)),
      total_trades: pick(bm, ["total_trades", "num_trades"]),
    };
  }

  // The footer carries PROVENANCE — where this came from and when it last
  // moved. It deliberately does NOT carry the status alone: the header
  // already shows that as a chip (`valid`, `1 warning`), and a footer that
  // repeats it spends a row saying nothing. Seen in the 2026-09-22 review as
  // a card reading `valid` in the header and `Valid` in the footer.
  //
  // The date carries its YEAR. `fmtDate(v, false)` renders "Sep 21" whatever
  // year it is, which is the same defect as the backtest card's window (F-1):
  // a strategy last touched in 2024 would read as touched this month.
  function statusText(status, meta) {
    var s = status ? cap(String(status).toLowerCase()) : null;
    var when =
      meta && meta.updated_at ? H.fmtDate(meta.updated_at, true) : null;
    if (s && when) return s + " · updated " + when;
    if (when) return "Updated " + when;
    return null;
  }

  function normalizeView(env) {
    var meta =
      env.metadata && typeof env.metadata === "object" ? env.metadata : {};
    var v = env.view && typeof env.view === "object" ? env.view : null;
    var graph =
      v &&
      v.structure &&
      typeof v.structure === "object" &&
      Array.isArray(v.structure.blocks)
        ? v.structure
        : meta.graph &&
            typeof meta.graph === "object" &&
            Array.isArray(meta.graph.blocks)
          ? meta.graph
          : { blocks: [] };
    var header =
      v && v.header && typeof v.header === "object"
        ? v.header
        : headerFromGraph(graph);
    if (!header.factories && Array.isArray(graph.factories))
      header.factories = headerFromGraph(graph).factories;
    // Factory bodies live on the structure; the header's list may carry
    // only names + counts. Join them so a factory chip can open its steps.
    var bodies = {};
    (Array.isArray(graph.factories) ? graph.factories : []).forEach(
      function (f) {
        if (f && f.name != null) bodies[String(f.name)] = f;
      },
    );
    var factories = (
      Array.isArray(header.factories) ? header.factories : []
    ).map(function (f) {
      var name =
        typeof f === "string" ? f : f && f.name != null ? String(f.name) : "";
      var src = bodies[name] || {};
      var out = { name: name, body: factorySteps(src) };
      out.steps =
        f && typeof f.steps === "number" ? f.steps : countBlocks(out.body);
      out.params =
        f && Array.isArray(f.params) && f.params.length
          ? f.params
          : factoryParamNames(src);
      return out;
    });
    var chg = normalizeChange(v ? v.change : null);
    var version =
      v && v.version != null
        ? v.version
        : env.version != null && env.version !== "HEAD"
          ? env.version
          : meta.current_sequence;
    var validation =
      v && v.validation && typeof v.validation === "object"
        ? v.validation
        : null;
    var status = v && v.status != null ? v.status : meta.status;
    return {
      name: (v && v.name) || meta.name || meta.strategy_name || null,
      version: version != null ? version : null,
      fromVersion:
        chg && chg.fromVersion != null
          ? chg.fromVersion
          : v && v.previous_version != null
            ? v.previous_version
            : null,
      validation: validation,
      clock:
        header.clock || clockLabel(graph.globals || graph.globals_) || null,
      universe:
        header.universe && typeof header.universe === "object"
          ? header.universe
          : universeFromGraph(graph.universe),
      execution:
        header.execution && typeof header.execution === "object"
          ? header.execution
          : executionFromGraph(graph.execution),
      factories: factories,
      structure: graph,
      // The DSL the Code tab renders. Validated rather than passed through:
      // an envelope is untrusted input, and `text` is the only field the
      // pane will read.
      source:
        v &&
        v.source &&
        typeof v.source === "object" &&
        typeof v.source.text === "string" &&
        v.source.text
          ? {
              text: v.source.text,
              lines: typeof v.source.lines === "number" ? v.source.lines : null,
              truncated: v.source.truncated === true,
              shown_lines:
                typeof v.source.shown_lines === "number"
                  ? v.source.shown_lines
                  : null,
            }
          : null,
      chg: chg,
      evidence:
        v && v.evidence !== undefined ? v.evidence : evidenceFromMeta(meta),
      size: v && typeof v.size === "string" ? v.size : null,
      marks: !!(v && v.marks === true),
      categories:
        v && v.categories && typeof v.categories === "object"
          ? v.categories
          : {},
      status:
        (v && typeof v.status_text === "string" && v.status_text) ||
        statusText(status, v ? null : meta),
      // `status` above is the PROVENANCE LINE ("Saved just now"), not the
      // state — so a renderer asking "is this a dry run?" needs its own
      // field or it silently reads a sentence. Two named signals, both
      // explicit: the view's own `PREVIEW` status (§2.3) and the
      // envelope's `dry_run` flag.
      preview:
        String(status == null ? "" : status).toUpperCase() === "PREVIEW" ||
        env.dry_run === true,
      // `is_live`: the listed profile's projection drops `deployment_id`
      // (Q-2268) and carries only whether the strategy is running.
      live: !!(meta.deployment_id || meta.is_live === true),
      error:
        (v && typeof v.error === "string" && v.error) ||
        meta.compilation_error ||
        env.source_error ||
        null,
      // A dry run whose source did not PARSE (Q-1840): its draft check
      // reads `revising` (Q-1848). The parser's message is the model's.
      parseError: !!(v && v.parse_error === true),
      url: linkUrl(env),
    };
  }

  function linkUrl(env) {
    var url = env.hero_url || env.share_url || null;
    if (!url && env.render) url = env.render.fallback_url || null;
    if (!url && typeof env.url_line === "string") {
      var m = env.url_line.match(/https?:\/\/\S+/);
      if (m) url = m[0];
    }
    return url;
  }

  // Size, chosen by the event (PLAN §4.3): none ⇒ structure; one block's
  // params only ⇒ receipt; otherwise change; > 8 hunks or > ½ the blocks
  // ⇒ structure with marks. `view.size` wins when the tool sent one.
  function chooseSize(view) {
    var chg = view.chg;
    if (
      view.size === "structure" ||
      view.size === "change" ||
      view.size === "receipt"
    )
      return view.size;
    if (!chg) return "structure";
    var nAdded = Object.keys(chg.added).length;
    var nChanged = Object.keys(chg.changed).length;
    var nRemoved = chg.removed.length;
    // Blocks and declarations both count as things that moved; only
    // BLOCKS decide the marked-structure arm, because a declaration has
    // no block to mark (Q-1707).
    var blocksTouched = nAdded + nChanged + nRemoved;
    var touched = blocksTouched + (chg.decl ? chg.decl.length : 0);
    if (touched === 0) return "structure";
    if (touched === 1 && nAdded === 0 && nRemoved === 0) return "receipt";
    var total =
      chg.total != null ? chg.total : countBlocks(view.structure.blocks);
    if (blocksTouched > MAX_HUNKS || blocksTouched > total / 2)
      return "structure";
    return "change";
  }

  // ── Header ────────────────────────────────────────────────────────
  function versionLabel(view) {
    if (view.version == null) return null;
    if (
      view.fromVersion != null &&
      String(view.fromVersion) !== String(view.version)
    )
      return "v" + view.fromVersion + " → v" + view.version;
    return "v" + view.version;
  }

  function validityChip(val) {
    if (!val) return null;
    var errors = Number(val.errors || 0);
    var warnings = Number(val.warnings || 0);
    if (errors > 0)
      return el(
        "span",
        "sv-chip sv-bad",
        errors + (errors === 1 ? " error" : " errors"),
      );
    if (warnings > 0)
      return el(
        "span",
        "sv-chip sv-warn",
        warnings + (warnings === 1 ? " warning" : " warnings"),
      );
    if (val.ok === true || val.ok == null)
      return el("span", "sv-chip sv-ok", "valid");
    return el("span", "sv-chip sv-bad", "invalid");
  }

  function header(view, withClock) {
    var head = el("div", "sv-head");
    head.appendChild(el("span", "sv-brand", "Keel"));
    var name = el("span", "sv-name", view.name || "Untitled");
    name.title = view.name || "Untitled";
    head.appendChild(name);
    var vl = versionLabel(view);
    if (vl) head.appendChild(el("span", "sv-chip sv-v sv-version", vl));
    var vc = validityChip(view.validation);
    if (vc) head.appendChild(vc);
    if (view.live) head.appendChild(el("span", "sv-chip sv-ok", "Live"));
    head.appendChild(el("span", "sv-spacer"));
    if (withClock && view.clock)
      head.appendChild(el("span", "sv-chip sv-v sv-clock", String(view.clock)));
    return head;
  }

  // ── Header chip row (universe · execution · factories) ────────────
  function toggleChip(label, key, build, body, container, changes) {
    var c = el("span", "sv-chip sv-btn");
    c.appendChild(el("span", "sv-k", key));
    c.appendChild(el("span", null, label));
    // What this save changed in the section the chip stands for
    // (Q-1777): `buffer 0.1 → 0.2`, marked, inside the chip — never a
    // free-text line under the row.
    if (changes && changes.length) {
      c.className += " sv-chg";
      changes.forEach(function (r) {
        var d = el("span", "sv-chgv");
        d.appendChild(el("span", "sv-k", r.label));
        if (r.a != null) d.appendChild(el("span", "sv-old", String(r.a)));
        d.appendChild(el("span", "sv-new", r.b == null ? DASH : String(r.b)));
        c.appendChild(d);
      });
    }
    var caret = el("span", "sv-caret", "▾");
    c.appendChild(caret);
    c.tabIndex = 0;
    c.setAttribute("role", "button");
    c.setAttribute("aria-expanded", "false");
    var panel = null;
    onActivate(c, function () {
      if (!panel) {
        panel = el("div", "sv-panel");
        panel.setAttribute("data-panel", key);
        var content = build();
        if (content) panel.appendChild(content);
        body.appendChild(panel);
        caret.textContent = "▴";
        c.setAttribute("aria-expanded", "true");
      } else {
        body.removeChild(panel);
        panel = null;
        caret.textContent = "▾";
        c.setAttribute("aria-expanded", "false");
      }
      packAll();
      notify();
    });
    container.appendChild(c);
    return c;
  }

  function symbolList(u, limit, url) {
    var s = el("div", "sv-sym");
    var syms = Array.isArray(u.symbols) ? u.symbols : [];
    var total = u.total != null ? Number(u.total) : syms.length;
    var shown = syms.slice(0, limit);
    shown.forEach(function (x) {
      s.appendChild(el("span", null, String(x)));
    });
    var rest = Math.max(total - shown.length, 0);
    if (rest > 0) {
      var more = el("span", "sv-symmore", "+" + rest + " · all in Keel ↗");
      more.tabIndex = 0;
      more.setAttribute("role", "link");
      if (url)
        onActivate(more, function () {
          H.openLink(url);
        });
      s.appendChild(more);
    }
    return s;
  }

  function chipRow(view, body, decl) {
    var cfg = el("div", "sv-cfgrow");
    var bySection = function (section) {
      return (decl || []).filter(function (r) {
        return r.section === section;
      });
    };
    if (view.universe && view.universe.label) {
      toggleChip(
        String(view.universe.label),
        "universe",
        function () {
          return symbolList(view.universe, INLINE_SYMBOLS, view.url);
        },
        body,
        cfg,
        bySection("universe"),
      );
    }
    if (view.execution && view.execution.label) {
      toggleChip(
        String(view.execution.label),
        "execution",
        function () {
          return (
            chipsFor(view.execution.params, null, false) ||
            el("p", "sv-none", "No settings.")
          );
        },
        body,
        cfg,
        bySection("execution"),
      );
    }
    var facs = view.factories;
    if (facs.length === 1) {
      toggleChip(
        facs[0].name,
        "factory",
        function () {
          return factoryBlock(facs[0], { categories: view.categories });
        },
        body,
        cfg,
      );
    } else if (facs.length > 1) {
      toggleChip(
        facs.length + " factories",
        "factories",
        function () {
          return factoryList(facs, { categories: view.categories });
        },
        body,
        cfg,
      );
    }
    return cfg;
  }

  // ── Evidence line ─────────────────────────────────────────────────
  function signed(v, digits) {
    var n = Number(v);
    if (v == null || isNaN(n)) return null;
    var body = H.fmtPct(Math.abs(n), digits == null ? 1 : digits);
    return (n < 0 ? MINUS : "+") + body;
  }

  function windowLabel(w) {
    if (!w) return null;
    if (typeof w === "string") return w;
    // The one display rule (spec 03 §2.5): the range ends on the LAST
    // COVERED day, never the exclusive end.
    if (typeof w === "object")
      return H.fmtWindow({
        start: w.start || w.start_date,
        end: w.end || w.end_date,
        last_bar: w.last_bar,
      });
    return null;
  }

  function evidenceLine(view) {
    var ev = el("div", "sv-ev");
    var e = view.evidence;
    if (!e || typeof e !== "object") {
      ev.appendChild(el("span", "sv-none", "No backtest yet."));
      return ev;
    }
    var older =
      e.matches_version === false ||
      (e.version != null &&
        view.version != null &&
        String(e.version) !== String(view.version));
    if (older) ev.className = "sv-ev sv-stale";
    var lead = older ? "v" + e.version + " backtest:" : "Latest backtest:";
    ev.appendChild(el("span", "sv-none", lead));
    var sharpe = H.fmtNum(e.sharpe != null ? e.sharpe : e.sharpe_ratio);
    var frag = el("span", "sv-none");
    frag.appendChild(document.createTextNode("Sharpe "));
    frag.appendChild(el("span", "sv-n", sharpe == null ? DASH : sharpe));
    frag.appendChild(document.createTextNode(" · "));
    var ret = signed(
      e.total_return_pct != null ? e.total_return_pct : e.total_return,
    );
    var retNode = el(
      "span",
      "sv-n" + (ret == null ? "" : ret.charAt(0) === "+" ? " sv-g" : " sv-b"),
      ret == null ? DASH : ret,
    );
    frag.appendChild(retNode);
    frag.appendChild(document.createTextNode(" · max DD "));
    var ddv = e.max_drawdown_pct != null ? e.max_drawdown_pct : e.max_drawdown;
    var dd =
      ddv == null || isNaN(Number(ddv))
        ? null
        : MINUS + H.fmtPct(Math.abs(Number(ddv)), 1);
    frag.appendChild(el("span", "sv-n", dd == null ? DASH : dd));
    // The run's count (Q-1746), never "fills" — by era, the standard count
    // or its positions (Q-2122, spec 01 §6.2). Its word is SERVED
    // (`evidence.count_label`, Q-1906) — the founder's word is off
    // the listed static-copy word list this HTML is scanned against. An
    // envelope without the label draws no count rather than an old word.
    var n_trips = H.fmtInt(pick(e, ["total_trades", "num_trades"]));
    if (typeof e.count_label === "string" && e.count_label)
      frag.appendChild(
        document.createTextNode(
          " · " + (n_trips == null ? DASH : n_trips) + " " + e.count_label,
        ),
      );
    var win = windowLabel(e.window);
    if (win)
      frag.appendChild(
        document.createTextNode(
          " · " + win + (e.sub_window ? " (sub-window)" : ""),
        ),
      );
    if (older)
      frag.appendChild(
        document.createTextNode(" · v" + view.version + " not run yet"),
      );
    ev.appendChild(frag);
    return ev;
  }

  // ── Footer ────────────────────────────────────────────────────────
  function footer(view, extra) {
    var foot = el("div", "sv-foot");
    // ONE action-row style across every card (F-2, BUILD §4.3). This
    // card used to draw its own `Open in Keel ↗` and its own `Expand`
    // in the `.sv-link` style while every other card drew the
    // adapter's `.button-link` row — two grammars for one action, one
    // rule apart. The adapter's `renderLinkRow` now draws both for all
    // five kinds; `data-owns-link` is declared "0" in `render` below.
    // `.sv-link` survives for IN-BODY disclosures only (Show params,
    // Show N more blocks, Show full pipeline), which is what `extra`
    // carries.
    if (extra) foot.appendChild(extra);
    // The footer carries PROVENANCE, and skips a status that merely repeats
    // the header's validity chip. `validityChip` renders "valid"/"invalid" as
    // bare words, so a footer reading exactly that spends a row saying what
    // is already on screen — seen in the 2026-09-22 review as `valid` in the
    // header and `Valid` in the footer of the same card. Anything with more
    // in it survives: "Draft", "Draft · from Nebula", "Valid · updated
    // Sep 21, 2026" all still render, because each adds something.
    var st = view.status ? String(view.status).trim() : "";
    var bare = st.toLowerCase();
    if (st && bare !== "valid" && bare !== "invalid")
      foot.appendChild(el("span", "sv-status", st));
    // An EMPTY footer still draws its top rule and its margins — roughly 30px
    // of nothing, between two hairlines. That happens whenever a card has no
    // link, no Expand (the host offers no fullscreen) and no provenance worth
    // showing, which the 2026-09-22 review caught as a blank band under
    // "No backtest yet.". A row that says nothing does not get to cost
    // anything.
    return foot.childNodes.length ? foot : null;
  }

  /** Append a footer only when there is one. */
  function appendFooter(card, view, extra) {
    var f = footer(view, extra);
    if (f) card.appendChild(f);
  }

  // ── Hunks (change size) ───────────────────────────────────────────
  function collectHunks(list, path, out, opts) {
    (Array.isArray(list) ? list : []).forEach(function (b) {
      var br = branchesOf(b);
      if (br) {
        Object.keys(br).forEach(function (k) {
          collectHunks(br[k], path.concat(k), out, opts);
        });
        return;
      }
      var mark = b && b.__removed ? "rem" : markFor(idOf(b), opts.chg);
      if (mark) out.push({ path: path, block: b });
    });
  }

  function hunksFlow(view, opts) {
    var flow = el("div", "sv-flow sv-hunks");
    var hunks = [];
    collectHunks(withRemoved(view.structure.blocks, view.chg), [], hunks, opts);
    var shown = hunks.slice(0, MAX_HUNKS);
    shown.forEach(function (h) {
      flow.appendChild(
        el("div", "sv-ctx", h.path.length ? h.path.join(" › ") : "pipeline"),
      );
      flow.appendChild(
        block(
          h.block,
          {
            full: true,
            marks: true,
            paramsHidden: false,
            categories: opts.categories,
            chg: opts.chg,
          },
          0,
        ),
      );
    });
    if (hunks.length > shown.length)
      flow.appendChild(
        el(
          "p",
          "sv-none",
          "+" +
            (hunks.length - shown.length) +
            " more changes · Show full pipeline",
        ),
      );
    // No empty-state line here, and Q-1700's fix ADDED one instead of
    // deleting it — the two identical guarded lines that stood here
    // until Q-1707 were the defect the comment said was fixed. They are
    // unreachable from the only caller (`renderChange` skips this flow
    // entirely when there is no hunk) and they printed the summary a
    // second time from anywhere else. One string, one owner: the
    // summary is drawn by `renderChange`, above this flow.
    return flow;
  }

  /** The declaration deltas, drawn the way a param delta is drawn:
   *  `Execution` over `buffer  0.1 → 0.05` (BUILD review §12). */
  function declFlow(rows) {
    var flow = el("div", "sv-flow sv-hunks sv-decls");
    var shown = rows.slice(0, MAX_HUNKS);
    shown.forEach(function (r) {
      flow.appendChild(el("div", "sv-ctx", r.sectionLabel));
      var line = el("div", "sv-chips");
      line.appendChild(chip(r.label, delta(r.a, r.b)));
      flow.appendChild(line);
    });
    if (rows.length > shown.length)
      flow.appendChild(
        el("p", "sv-none", "+" + (rows.length - shown.length) + " more"),
      );
    return flow;
  }

  /** How many block-level hunks a change has. */
  function countHunks(view, opts) {
    var hunks = [];
    collectHunks(withRemoved(view.structure.blocks, view.chg), [], hunks, opts);
    return hunks.length;
  }

  // ── The card, per size ────────────────────────────────────────────
  function pipelineSection(view, card, opts, budget) {
    var n = countBlocks(view.structure.blocks);
    var sect = el(
      "div",
      "sv-sect",
      "Pipeline · " + n + (n === 1 ? " block" : " blocks"),
    );
    if (opts.paramsHidden) {
      var toggle = button("sv-r sv-params-toggle", "Show params");
      toggle.setAttribute("aria-pressed", "false");
      var on = false;
      onActivate(toggle, function () {
        on = !on;
        var rows = card.querySelectorAll(".sv-chips.sv-p");
        for (var i = 0; i < rows.length; i++)
          rows[i].classList.toggle("sv-hidden", !on);
        toggle.textContent = on ? "Hide params" : "Show params";
        toggle.setAttribute("aria-pressed", on ? "true" : "false");
        packAll();
        notify();
      });
      sect.appendChild(toggle);
    }
    card.appendChild(sect);
    var flow = flowOf(
      withRemoved(view.structure.blocks, opts.marks ? view.chg : null),
      opts,
      budget,
    );
    flow.className += " sv-pipeline";
    card.appendChild(flow);
    return flow;
  }

  // The pinned deprecated components (position-layer spec 04-R25): one
  // line each, `{component} v{version} is deprecated — {replacement_text}`
  // plus ` Known issue {id}.` when the pin carries one. Drawn only when the
  // view says so; a strategy without deprecations draws exactly as before.
  function deprecationLines(view) {
    var val = view.validation;
    var rows = val && Array.isArray(val.deprecations) ? val.deprecations : [];
    var lines = [];
    rows.forEach(function (d) {
      if (!d || !d.component) return;
      var text =
        String(d.component) +
        (d.version != null ? " v" + d.version : "") +
        " is deprecated";
      if (d.replacement_text) text += " — " + String(d.replacement_text);
      if (d.known_issue && d.known_issue.id)
        text += ". Known issue " + String(d.known_issue.id) + ".";
      lines.push(el("p", "sv-summary sv-deprecation", text));
    });
    return lines;
  }

  function appendDeprecations(card, view) {
    deprecationLines(view).forEach(function (line) {
      card.appendChild(line);
    });
  }

  function renderStructure(view, narrow, marks) {
    var card = el("div", "sv-card sv-structure");
    card.setAttribute("data-size", "structure");
    card.appendChild(header(view, true));
    appendDeprecations(card, view);
    var body = el("div", "sv-panels");
    var decl = marks && view.chg ? view.chg.decl || [] : [];
    card.appendChild(chipRow(view, body, decl));
    card.appendChild(body);
    // No `A | B` summary line (Q-1777): the server's summary_text is a
    // sentence for a TEXT host, and printed here it drew raw lists and
    // pipes. Block changes are marked on their blocks; a declaration
    // change is marked on its chip; one without a chip (a Globals edit)
    // is the same split chip the change card draws.
    var chipless = decl.filter(function (r) {
      if (r.section === "universe")
        return !(view.universe && view.universe.label);
      if (r.section === "execution")
        return !(view.execution && view.execution.label);
      return true;
    });
    if (chipless.length) card.appendChild(declFlow(chipless));
    var opts = {
      full: false,
      marks: marks,
      paramsHidden: true,
      categories: view.categories,
      chg: marks ? view.chg : null,
    };
    pipelineSection(
      view,
      card,
      opts,
      narrow ? INLINE_BUDGET_NARROW : INLINE_BUDGET,
    );
    card.appendChild(evidenceLine(view));
    appendFooter(card, view, null);
    return card;
  }

  function renderChange(view) {
    var card = el("div", "sv-card sv-change");
    card.setAttribute("data-size", "change");
    card.appendChild(header(view, false));
    // The summary sentence only when the change touches no declaration:
    // a declaration is drawn as its split chip just below, and the
    // sentence would print it a second time — as a raw list (Q-1777).
    if (view.chg && view.chg.summary && !(view.chg.decl || []).length)
      card.appendChild(el("p", "sv-summary", view.chg.summary));
    var opts = {
      full: true,
      marks: true,
      paramsHidden: false,
      categories: view.categories,
      chg: view.chg,
    };
    // Declarations first: a clock, a universe or an execution-mode edit
    // is the change a user asked for by name (Q-1707).
    var decl = (view.chg && view.chg.decl) || [];
    if (decl.length) card.appendChild(declFlow(decl));
    var n = countHunks(view, opts);
    var full = flowOf(withRemoved(view.structure.blocks, view.chg), opts, 0);
    full.className += " sv-pipeline";
    var sw = null;
    if (n) {
      var hunks = hunksFlow(view, opts);
      card.appendChild(hunks);
      full.className += " sv-hidden";
      card.appendChild(full);
      sw = button("sv-link sv-swap", "Show full pipeline");
      onActivate(sw, function () {
        var h = hunks.classList.toggle("sv-hidden");
        full.classList.toggle("sv-hidden", !h);
        sw.textContent = h ? "Show changes only" : "Show full pipeline";
        packAll();
        notify();
      });
    } else {
      // A declaration-only change touches no block, so there is no hunk
      // flow to draw and nothing to swap to: the summary above says
      // what moved, and the pipeline it moved under is shown as it is.
      card.appendChild(full);
    }
    card.appendChild(evidenceLine(view));
    appendFooter(card, view, sw);
    return card;
  }

  // ── The receipt (BUILD §4.2) ──────────────────────────────────────
  //
  // ONE row, the adapter's (`H.receiptRow`), with the structure one tap
  // away IN PLACE (`Show pipeline`). A dry run never reaches this: it is
  // the draft check below (Q-1848).

  /** The one change a save receipt shows: `Block · param  old → new`. */
  function receiptHunk(view) {
    if (!view.chg) return null;
    var hunks = [];
    collectHunks(withRemoved(view.structure.blocks, view.chg), [], hunks, {
      chg: view.chg,
    });
    if (!hunks.length) {
      // A declaration-only save: `Execution · buffer  0.1 → 0.05`, the
      // same one-row receipt a single param edit gets (Q-1707).
      var rows = view.chg.decl || [];
      if (!rows.length) return null;
      return {
        path: rows[0].sectionLabel,
        key: rows[0].label,
        oldValue: rows[0].a,
        newValue: rows[0].b,
        extra: rows.length - 1,
      };
    }
    var h = hunks[0];
    var name = blockName(h.block);
    var path = h.path.length ? h.path.join(" › ") + " › " + name : name;
    var changed = view.chg.changed[String(idOf(h.block))];
    if (changed) {
      var keys = Object.keys(changed);
      if (keys.length) {
        var pair = changed[keys[0]];
        return {
          path: path,
          key: keys[0],
          oldValue: pair[0],
          newValue: pair[1],
          extra: keys.length - 1,
        };
      }
    }
    return {
      note: path + (h.block.__removed ? " removed" : " added"),
    };
  }

  function receiptState(view) {
    var val = view.validation;
    if (!val) return null;
    var errors = Number(val.errors || 0);
    var warnings = Number(val.warnings || 0);
    if (errors > 0)
      return {
        text: errors + (errors === 1 ? " error" : " errors"),
        tone: "warn",
      };
    if (warnings > 0)
      return {
        text: warnings + (warnings === 1 ? " warning" : " warnings"),
        tone: null,
      };
    if (val.ok === false) return { text: "invalid", tone: "warn" };
    // The validity chip is never dropped to make room for the name
    // (review page, decided): the name ellipsises first.
    return { text: "valid", tone: "good" };
  }

  function renderReceipt(view, narrow) {
    var spec = {
      name: view.name,
      version: view.version,
      url: view.url || null,
      chips: [],
    };
    // After the state group, never before it (Q-1772). The words are the
    // adapter's (Q-1775).
    spec.superseded =
      typeof H.supersededLabel === "function" ? H.supersededLabel() : null;
    var st = receiptState(view);
    if (st) {
      spec.state = st.text;
      spec.stateTone = st.tone;
    }
    var hunk = receiptHunk(view);
    if (hunk && hunk.key != null) spec.hunk = hunk;
    else if (hunk && hunk.note) spec.note = hunk.note;
    else if (view.chg && view.chg.summary) spec.note = view.chg.summary;
    // No change to show: the provenance line, which is true of a read
    // AND a save ("Draft · updated Sep 20"). "Saved." was printed on
    // keel_strategy_get receipts, which save nothing (Q-1775). It is
    // provenance, not a change, so the name outranks it (Q-1797).
    else if (view.status) spec.provenance = view.status;
    spec.disclosure = {
      label: "Show pipeline",
      build: function (target) {
        var card = renderStructure(view, narrow, !!view.chg);
        if (narrow) card.className += " sv-narrow";
        target.appendChild(card);
        packAll();
      },
    };
    return H.receiptRow(spec);
  }

  // ── The draft check (Q-1848) ──────────────────────────────────────
  //
  // `keel_strategy_compose(dry_run=true)` is the agent checking its own
  // draft before it saves; iteration is dry runs and one save per
  // user-visible step. So the USER's card for a dry run says only that a
  // check happened and whether the agent is still revising: one muted
  // row, no red. Everything the agent needs to fix the draft (the
  // parser's message with its line and column, rule codes, the parameter
  // list, the source, the structure) stays in the text block and
  // `structuredContent`, where the model reads it. Before this the card
  // drew `Preview · 1 error` with the parser's sentence under it and a
  // `Show source` disclosure (Q-1840's receipt), so the user watched the
  // agent's internal iteration.
  //
  // Why a row and not nothing: a card that renders nothing never reports
  // a size (`reportSize` skips a zero height), so the host keeps its
  // default frame (a blank gap on claude.ai, an empty panel under
  // ChatGPT's header), and the in-flight row the adapter already drew
  // would collapse into it, the one jump the in-flight contract forbids.
  //
  // No disclosure, so no fullscreen request on ChatGPT and no action
  // band (a closed receipt draws none). `superseded by vN` still follows
  // the state: a check a later save superseded reads as exactly that.

  /** `ready` when the draft would save clean; `revising` otherwise. */
  function draftState(view, env) {
    var val = view.validation;
    var errors = Number((val && val.errors) || 0);
    if (view.parseError || errors > 0) return "revising";
    if (val && val.ok === false) return "revising";
    if (env && env.compiled === false) return "revising";
    return "ready";
  }

  function renderDraftCheck(view, env) {
    var name = view.name || null;
    var spec = {
      name: name || "Draft check",
      version: null,
      url: null,
      chips: [],
    };
    if (name) spec.chips.push({ text: "Draft check", tone: "muted" });
    spec.chips.push({ text: draftState(view, env), tone: "muted" });
    spec.superseded =
      typeof H.supersededLabel === "function" ? H.supersededLabel() : null;
    var row = H.receiptRow(spec);
    row.className += " rr-draft";
    return row;
  }

  function renderFullscreen(view) {
    var card = el("div", "sv-card sv-fs");
    card.setAttribute("data-size", "fullscreen");
    var bar = header(view, true);
    bar.className = "sv-head sv-fsbar";
    var seg = el("span", "sv-seg");
    seg.setAttribute("role", "tablist");
    var visualBtn = button("sv-on", "Visual");
    var codeBtn = button(null, "Code");
    seg.appendChild(visualBtn);
    seg.appendChild(codeBtn);
    bar.insertBefore(seg, bar.querySelector(".sv-spacer"));
    card.appendChild(bar);
    appendDeprecations(card, view);

    var visual = el("div", "sv-visual");
    var code = el("div", "sv-code sv-hidden");
    var src = view.source;
    if (src && src.text) {
      // textContent, never innerHTML: this is user-authored source.
      code.appendChild(el("pre", "sv-src", src.text));
      if (src.truncated) {
        code.appendChild(
          el(
            "div",
            "sv-srcnote",
            "Showing the first " +
              src.shown_lines +
              " of " +
              src.lines +
              " lines — open in Keel for the whole strategy.",
          ),
        );
      }
    } else {
      code.appendChild(
        el("div", "sv-codestub", "No source available for this strategy."),
      );
    }
    var switchTo = function (toCode) {
      visual.classList.toggle("sv-hidden", toCode);
      code.classList.toggle("sv-hidden", !toCode);
      visualBtn.classList.toggle("sv-on", !toCode);
      codeBtn.classList.toggle("sv-on", toCode);
      visualBtn.setAttribute("aria-selected", toCode ? "false" : "true");
      codeBtn.setAttribute("aria-selected", toCode ? "true" : "false");
      packAll();
      notify();
    };
    onActivate(visualBtn, function () {
      switchTo(false);
    });
    onActivate(codeBtn, function () {
      switchTo(true);
    });

    // Configuration
    visual.appendChild(el("div", "sv-sect", "Configuration"));
    var cf = el("div", "sv-flow sv-config");
    var g = view.structure.globals || view.structure.globals_ || null;
    var gb = el("div", "sv-blk");
    var gr = el("div", "sv-row");
    gr.appendChild(el("span", "sv-c", "Globals"));
    gr.appendChild(el("span", "sv-badge sv-plain", "config"));
    gb.appendChild(gr);
    var gparams =
      g && typeof g === "object"
        ? g
        : view.clock
          ? { target_timeframe: view.clock }
          : {};
    var gch = chipsFor(gparams, null, false);
    if (gch) gb.appendChild(gch);
    cf.appendChild(gb);
    if (view.universe) {
      var ub = el("div", "sv-blk");
      var ur = el("div", "sv-row");
      ur.appendChild(el("span", "sv-c", "Universe"));
      ur.appendChild(el("span", "sv-badge sv-plain", "assets"));
      ur.appendChild(el("span", "sv-path", String(view.universe.label || "")));
      if (view.universe.total != null) {
        var res = el(
          "span",
          "sv-badge sv-plain",
          view.universe.total + " resolved",
        );
        res.style.marginLeft = "auto";
        ur.appendChild(res);
      }
      ub.appendChild(ur);
      var all = Array.isArray(view.universe.symbols)
        ? view.universe.symbols.length
        : 0;
      ub.appendChild(
        symbolList(view.universe, Math.max(all, INLINE_SYMBOLS), view.url),
      );
      cf.appendChild(ub);
    }
    if (view.execution) {
      var eb = el("div", "sv-blk");
      var er = el("div", "sv-row");
      er.appendChild(el("span", "sv-c", "Execution"));
      er.appendChild(el("span", "sv-badge sv-plain", "execution"));
      eb.appendChild(er);
      var ech = chipsFor(view.execution.params, null, false);
      if (ech) eb.appendChild(ech);
      cf.appendChild(eb);
    }
    visual.appendChild(cf);

    // Factories, steps drawn
    if (view.factories.length) {
      visual.appendChild(el("div", "sv-sect", "Factories"));
      var ff = el("div", "sv-flow sv-factories");
      view.factories.forEach(function (f) {
        ff.appendChild(factoryBlock(f, { categories: view.categories }));
      });
      visual.appendChild(ff);
    }

    // Pipeline, every param, no budget
    var opts = {
      full: true,
      marks: view.marks || !!(view.chg && view.size === "change"),
      paramsHidden: false,
      categories: view.categories,
      chg: view.chg,
    };
    pipelineSection(view, visual, opts, 0);
    card.appendChild(visual);
    card.appendChild(code);
    card.appendChild(evidenceLine(view));
    appendFooter(card, view, null);
    return card;
  }

  // ── Mount ─────────────────────────────────────────────────────────
  var lastEnv = null;
  var lastLayout = null;

  function layoutKey() {
    return displayMode() + ":" + (cardWidth() < NARROW_PX ? "narrow" : "wide");
  }

  function render(env) {
    injectCss();
    var body = document.getElementById("card-body");
    if (!body) return;
    body.textContent = "";
    var head = document.querySelector(".card-head");
    if (head) head.hidden = true;
    var view = normalizeView(env);
    var narrow = cardWidth() < NARROW_PX;
    var card;
    var receipt = false;
    if (view.preview) {
      // A dry run is the agent checking its own draft (Q-1848): one muted
      // line at every size and in every display mode, never the parser's
      // or validator's words, the source, or a structure — those are the
      // model's to act on, and the user's card is the SAVED version.
      card = renderDraftCheck(view, env);
      receipt = true;
    } else if (displayMode() === "fullscreen") {
      card = renderFullscreen(view);
    } else {
      var size = chooseSize(view);
      // A younger sibling for the same object appeared: fall back to
      // the receipt, which keeps the link and the in-place open
      // (BUILD §2.9). The card never blanks itself.
      if (typeof H.supersededBy === "function" && H.supersededBy())
        size = "receipt";
      view.size = size;
      if (size === "receipt") {
        card = renderReceipt(view, narrow);
        receipt = true;
      } else if (size === "change") card = renderChange(view);
      else card = renderStructure(view, narrow, view.marks || !!view.chg);
    }
    if (narrow && !receipt) card.className += " sv-narrow";
    // A SAVE's compile error is a user-visible outcome and stays; a dry
    // run's is agent-internal and never reaches the card (Q-1848). Server
    // prose, so no id reaches drawn text (Q-1712).
    if (view.error && !view.preview)
      card.appendChild(el("p", "sv-none sv-error", H.prose(view.error)));
    body.appendChild(card);
    // ONE action row, and it is the adapter's (F-2, BUILD §4.3). This
    // card used to draw its own and declare ownership; now it declares
    // the opposite so `renderLinkRow` draws `Open strategy in Keel ↗`
    // and Expand in the same `.button-link` style every other card
    // uses. A CLOSED receipt suppresses the band entirely, which the
    // adapter decides from `data-size` (set by `H.receiptRow`) — the
    // card does not touch the row itself, because the adapter re-runs
    // on every display-mode change and would un-hide it (F-5).
    if (document.body) document.body.setAttribute("data-owns-link", "0");
    if (!receipt && document.body) document.body.removeAttribute("data-size");
    if (typeof H.refreshLinkRow === "function") H.refreshLinkRow();
    lastLayout = layoutKey();
    packAll();
    notify();
  }

  H.ready(function (env) {
    lastEnv = env || {};
    render(lastEnv);
  });

  if (typeof H.onSuperseded === "function") {
    H.onSuperseded(function () {
      if (!lastEnv) return;
      render(lastEnv);
      notify();
    });
  }

  if (typeof H.onResize === "function") {
    H.onResize(function () {
      if (!lastEnv) return;
      if (layoutKey() !== lastLayout) render(lastEnv);
      else packAll();
    });
  }
})();
