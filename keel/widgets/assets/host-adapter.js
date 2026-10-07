/* Keel host adapter — one card codebase, two host dialects.
 *
 * Primary dialect: MCP Apps (io.modelcontextprotocol/ui) — postMessage
 * JSON-RPC to the parent host frame. This is ALSO ChatGPT's primary path
 * (ChatGPT converged on the MCP Apps standard, 2026-02); the thin
 * secondary dialect is the ChatGPT Apps SDK `window.openai` globals,
 * which the same HTML detects and maps 1:1:
 *
 *   KeelHost.callTool     -> openai.callTool            | "tools/call"
 *   KeelHost.openLink     -> openai.openExternal        | "ui/open-link"
 *   KeelHost.sendMessage  -> openai.sendFollowUpMessage | WIRE.message
 *   size reporting        -> openai.notifyIntrinsicHeight | size-changed
 *   requestDisplayMode    -> openai.requestDisplayMode  | "ui/request-display-mode"
 *
 * Sizing contract (Q-1505): the host sizes an inline card FROM THE
 * DOCUMENT. We (a) set the root element's height explicitly to the
 * content height (claude-ai-mcp#69: the host reads the document height
 * — viewport-height units and unset heights read as the host's default and the card
 * scrolls inside it), and (b) send `ui/notifications/size-changed` /
 * `notifyIntrinsicHeight` on every content change via a ResizeObserver.
 * No internal scrolling anywhere in a card EXCEPT fullscreen, which is
 * the one display mode a card is allowed to scroll in.
 *
 * Theming: `hostContext.theme` sets [data-theme]; when the host ships
 * `styles.variables` (Claude does) they are mapped onto the card's own
 * tokens so the card inherits the host palette; `styles.css.fonts`
 * (the host's @font-face block) is injected verbatim. Everything falls
 * back to card.css's palette + prefers-color-scheme. `--bg` is NOT
 * mapped (Q-1631): the body stays transparent so the host's own slot
 * paints the ground, however that slot is tinted.
 *
 * One formatter (Q-1629 / Q-1634): `KeelHost.fmt` is the twin of the
 * app's `pnl()` (services/keel-app/src/lib/design/pnl.ts). A sign glyph
 * ALWAYS accompanies colour; only signed money and returns are coloured;
 * drawdowns, ratios and rates are neutral; a missing value renders an
 * em dash and KEEPS its tile; one minus glyph (U+2212) everywhere.
 *
 * All MCP Apps wire method names live in WIRE below so a spec-name
 * delta at integration is a one-line fix.
 */
(function () {
  "use strict";

  // Wire names + protocol version verified against the official
  // @modelcontextprotocol/ext-apps AppBridge bundle (2026-07-17 e2e):
  // initialize REQUIRES appInfo + appCapabilities + protocolVersion,
  // and the app must follow the response with the `initialized`
  // notification before the host sends tool input/result.
  var PROTOCOL_VERSION = "2026-01-26";
  var WIRE = {
    initialize: "ui/initialize",
    initialized: "ui/notifications/initialized",
    callTool: "tools/call",
    openLink: "ui/open-link",
    message: "ui/message",
    sizeChanged: "ui/notifications/size-changed",
    requestDisplayMode: "ui/request-display-mode",
    toolInput: "ui/notifications/tool-input",
    toolInputPartial: "ui/notifications/tool-input-partial",
    toolCancelled: "ui/notifications/tool-cancelled",
    toolResult: "ui/notifications/tool-result",
    hostContext: "ui/notifications/host-context-changed",
  };

  // What this app can do, declared in the handshake (Q-1632). A host
  // offers its Expand affordance only to an app that asks for one, and
  // the card renders its own Expand action only when the host answers
  // with fullscreen in ITS list — declared is not the same as offered.
  var APP_DISPLAY_MODES = ["inline", "fullscreen"];
  var NARROW_PX = 480;

  var isOpenAI = !!(
    window.openai && typeof window.openai.callTool === "function"
  );
  // The dialect on the root, set before first paint (Q-1776): card.css
  // keys the wordmark rule off it, so EVERY state — the template's
  // skeleton head, the in-flight row, a full card's own head — follows
  // the host, not just the renderers that remember to ask `wordmark()`.
  document.documentElement.setAttribute(
    "data-dialect",
    isOpenAI ? "openai" : "mcp-apps",
  );

  // ── JSON-RPC over postMessage (MCP Apps dialect) ──────────────────
  var nextId = 1;
  var pending = {};
  var resultListeners = [];
  var resizeListeners = [];
  var envelopeDelivered = false;
  var lastEnvelope = null;
  var currentDisplayMode = "inline";
  var hostDisplayModes = [];

  function post(msg) {
    try {
      window.parent.postMessage(msg, "*");
    } catch (e) {
      /* host absent — fallback link row still renders */
    }
  }

  function request(method, params) {
    return new Promise(function (resolve, reject) {
      var id = nextId++;
      pending[id] = { resolve: resolve, reject: reject };
      post({ jsonrpc: "2.0", id: id, method: method, params: params || {} });
      setTimeout(function () {
        if (pending[id]) {
          delete pending[id];
          reject(new Error("host request timed out: " + method));
        }
      }, 10000);
    });
  }

  // ── Theme + host styles ───────────────────────────────────────────
  function applyTheme(theme) {
    if (theme === "dark" || theme === "light") {
      document.documentElement.setAttribute("data-theme", theme);
    }
  }

  // Host CSS variable → card token. The first name present wins; a
  // token with no host value keeps card.css's own palette.
  //
  // `--bg` is deliberately absent (Q-1631): mapping it to the host's
  // OPAQUE primary background cancelled the transparent-body rule the
  // theming guide requires, which double-paints on a tinted slot.
  var TOKEN_MAP = {
    "--tile": ["--color-background-secondary", "--color-bg-secondary"],
    "--fg": ["--color-text-primary"],
    "--muted": ["--color-text-secondary", "--color-text-tertiary"],
    "--line": ["--color-border-primary", "--color-border-secondary"],
    // `--accent` is deliberately ABSENT. Both vendors permit brand as the
    // accent and only as the accent: "host tokens for structure, brand only
    // as accent" (Claude design guidelines) and "use brand accent colors on
    // primary buttons inside app display modes" (ChatGPT). Mapping it here
    // handed the one permitted brand surface back to the host, so the card
    // carried no Keel anywhere. card.css owns it (Q-1652).
    "--good": ["--color-text-success"],
    "--bad": ["--color-text-danger"],
    "--font": ["--font-sans", "--font-family-sans"],
    "--font-mono": ["--font-mono", "--font-family-mono"],
    "--radius": ["--border-radius-md", "--border-radius-lg"],
  };

  function applyHostStyles(styles) {
    if (!styles) return;
    var vars = styles.variables;
    if (vars && typeof vars === "object") {
      var rootStyle = document.documentElement.style;
      Object.keys(TOKEN_MAP).forEach(function (token) {
        var names = TOKEN_MAP[token];
        for (var i = 0; i < names.length; i++) {
          var v = vars[names[i]];
          if (typeof v === "string" && v.trim()) {
            rootStyle.setProperty(token, v);
            break;
          }
        }
      });
    }
    var fonts = styles.css && styles.css.fonts;
    if (typeof fonts === "string" && fonts.trim()) {
      var id = "keel-host-fonts";
      var node = document.getElementById(id);
      if (!node) {
        node = document.createElement("style");
        node.id = id;
        document.head.appendChild(node);
      }
      node.textContent = fonts;
    }
  }

  function applyDisplayMode(mode) {
    if (typeof mode !== "string" || !mode) return;
    var changed = mode !== currentDisplayMode;
    currentDisplayMode = mode;
    document.documentElement.setAttribute("data-display-mode", mode);
    if (changed) {
      // A mode change is a layout change: every renderer that draws
      // width- or mode-dependent content redraws through the same path
      // it uses for a resize.
      fireResize();
    }
  }

  // The host's OWN list — what it is willing to offer. Empty until a
  // host says otherwise, so an Expand action never renders on a host
  // that cannot honour it.
  function readHostModes(source) {
    if (!source || typeof source !== "object") return;
    var modes = source.availableDisplayModes;
    if (Array.isArray(modes)) {
      hostDisplayModes = modes.filter(function (m) {
        return typeof m === "string";
      });
    }
  }

  function handleHostContext(ctx) {
    if (!ctx) return;
    if (ctx.theme) applyTheme(ctx.theme);
    if (ctx.styles) applyHostStyles(ctx.styles);
    readHostModes(ctx);
    readToolInfo(ctx);
    if (ctx.displayMode) applyDisplayMode(ctx.displayMode);
    var insets = ctx.safeAreaInsets;
    if (insets && typeof insets === "object") {
      var s = document.documentElement.style;
      if (insets.bottom != null)
        s.setProperty("--safe-bottom", insets.bottom + "px");
      if (insets.top != null) s.setProperty("--safe-top", insets.top + "px");
    }
    // Host context arriving means the host is wired up for this frame.
    scheduleSize(true);
  }

  // ── Size reporting (the inline-height contract) ───────────────────
  var lastSize = { w: -1, h: -1 };
  var sizeQueued = false;
  // Q-1770: the dedup below assumes every post was HEARD. A replayed
  // conversation re-mounts every card and the host is not listening for
  // `size-changed` when the first posts go out (its frames start at
  // 150 px and stay there), so an unchanged size must still be re-sent at
  // the moments a host could have started listening.
  var forceSize = false;

  function measure() {
    var root = document.getElementById("root");
    if (!root) return null;
    var rect = root.getBoundingClientRect();
    var h = Math.ceil(root.scrollHeight || rect.height);
    var w = Math.ceil(rect.width || document.documentElement.clientWidth);
    return { w: w, h: h };
  }

  function reportSize() {
    sizeQueued = false;
    var size = measure();
    if (!size || size.h <= 0) return;
    // (a) The document itself carries the content height — this is what
    // a host that measures the frame reads. In fullscreen the host owns
    // the viewport, so the document keeps its natural height and scrolls.
    if (currentDisplayMode === "fullscreen") {
      document.documentElement.style.height = "";
      document.body.style.height = "";
    } else {
      document.documentElement.style.height = size.h + "px";
      document.body.style.height = size.h + "px";
    }
    if (!forceSize && size.w === lastSize.w && size.h === lastSize.h) return;
    forceSize = false;
    lastSize = size;
    // (b) Tell the host explicitly, in whichever dialect it speaks.
    if (isOpenAI) {
      if (typeof window.openai.notifyIntrinsicHeight === "function") {
        try {
          window.openai.notifyIntrinsicHeight(size.h);
        } catch (e) {
          /* older bridge without the call — the document height stands */
        }
      }
    } else {
      post({
        jsonrpc: "2.0",
        method: WIRE.sizeChanged,
        params: { width: size.w, height: size.h },
      });
    }
  }

  // Re-send the current size now (next frame) and on a short settle, even
  // when it has not changed: after the handshake, after host context,
  // after every render, and when the frame scrolls into view. Grow and
  // shrink both go through here — the post is the measured height, never
  // a delta.
  var RESEND_MS = [250, 1000, 3000];
  function resendSize() {
    scheduleSize(true);
    for (var i = 0; i < RESEND_MS.length; i++) {
      setTimeout(function () {
        scheduleSize(true);
      }, RESEND_MS[i]);
    }
  }

  function scheduleSize(force) {
    if (force === true) forceSize = true;
    if (sizeQueued) return;
    sizeQueued = true;
    // rAF OR a timer, whichever fires first. A browser pauses rAF in a
    // frame it is not painting (an off-screen card in a replayed
    // conversation); on rAF alone the queued report — and, through
    // `sizeQueued`, every report after it — waited for the frame to paint.
    var done = false;
    function once() {
      if (done) return;
      done = true;
      reportSize();
    }
    if (window.requestAnimationFrame) window.requestAnimationFrame(once);
    setTimeout(once, 50);
  }

  function fireResize() {
    var w = cardWidth();
    for (var i = 0; i < resizeListeners.length; i++) {
      try {
        resizeListeners[i](w);
      } catch (e) {
        /* a renderer's redraw failure never blocks size reporting */
      }
    }
    scheduleSize();
  }

  function watchSize() {
    var root = document.getElementById("root");
    if (typeof ResizeObserver === "function" && root) {
      try {
        new ResizeObserver(function () {
          scheduleSize();
        }).observe(root);
      } catch (e) {
        /* fall through to the window listener */
      }
    }
    window.addEventListener("resize", fireResize);
    if (document.fonts && document.fonts.ready && document.fonts.ready.then) {
      document.fonts.ready.then(
        function () {
          scheduleSize();
        },
        function () {},
      );
    }
    // A replayed card may be mounted off-screen and only wired up by the
    // host when it scrolls in: say the size again when it becomes visible.
    if (typeof IntersectionObserver === "function" && root) {
      try {
        new IntersectionObserver(function (entries) {
          for (var i = 0; i < entries.length; i++) {
            if (entries[i].isIntersecting) scheduleSize(true);
          }
        }).observe(root);
      } catch (e) {
        /* no observer — the timed re-sends stand */
      }
    }
  }

  // ── Dialect wiring ────────────────────────────────────────────────
  // The MCP Apps notifications are heard in BOTH dialects. ChatGPT's widget
  // bridge is MCP Apps underneath (it converged on the standard, 2026-02):
  // a card that only reads the legacy `window.openai` globals never hears
  // `tool-input` — the arguments that size and word the in-flight state —
  // and on 2026-09-23 waited through whole calls as a bare 20 px bar with
  // `toolInput` null throughout (Q-1883). So ChatGPT also gets the
  // handshake (`ready()` below), and whatever the bridge sends is used:
  // the arguments, the tool name (`hostContext.toolInfo`), `tool-cancelled`
  // and the `tool-result`, which carries `structuredContent` and `_meta`
  // in ONE message. The globals stay the fallback source for a host that
  // does not speak the bridge.
  var bridgeResultSeen = false;
  window.addEventListener("message", function (event) {
    if (isOpenAI && event.source !== window.parent) return;
    var msg = event.data;
    if (!msg || msg.jsonrpc !== "2.0") return;
    if (msg.id != null && pending[msg.id] && !(isOpenAI && msg.method)) {
      var p = pending[msg.id];
      delete pending[msg.id];
      if (msg.error) p.reject(new Error(msg.error.message || "host error"));
      else p.resolve(msg.result);
      return;
    }
    if (msg.method === WIRE.toolResult) {
      onBridgeResult(msg.params && (msg.params.result || msg.params));
    } else if (
      msg.method === WIRE.toolInput ||
      msg.method === WIRE.toolInputPartial
    ) {
      // The call is IN FLIGHT: the host mounts the card when the call
      // starts, and the result can be minutes away (Q-1769).
      onToolInput(msg.params && msg.params.arguments);
    } else if (msg.method === WIRE.toolCancelled) {
      // The protocol's terminal miss: the call ended with no result.
      if (!envelopeDelivered) showMiss("cancelled");
    } else if (msg.method === WIRE.hostContext) {
      // ChatGPT's look comes from its globals; from the bridge it takes
      // only the tool's name.
      if (isOpenAI) readToolInfo(msg.params);
      else handleHostContext(msg.params);
    }
  });
  if (isOpenAI) {
    window.addEventListener("openai:set_globals", function () {
      readOpenAIGlobals();
    });
  }

  // A result from the bridge is complete (structured + `_meta` together).
  // On ChatGPT it outranks the globals: a later `toolOutput` update whose
  // metadata has not landed must not re-render the card without it.
  function onBridgeResult(result) {
    if (isOpenAI) {
      bridgeResultSeen = true;
      stopMetaHold();
    }
    deliverResult(result);
  }

  function handshake() {
    return request(WIRE.initialize, {
      appInfo: { name: "keel-cards", version: "2.0.0" },
      appCapabilities: { availableDisplayModes: APP_DISPLAY_MODES },
      protocolVersion: PROTOCOL_VERSION,
    }).then(function (res) {
      // The handshake completes with the `initialized` notification; only
      // then does the host deliver tool input + result.
      post({ jsonrpc: "2.0", method: WIRE.initialized });
      if (isOpenAI) {
        if (res && res.hostContext) readToolInfo(res.hostContext);
        if (res && res.toolResult) onBridgeResult(res.toolResult);
        return;
      }
      readHostModes(res);
      readHostModes(res && res.hostCapabilities);
      if (res && res.hostContext) handleHostContext(res.hostContext);
      if (res && res.toolResult) deliverResult(res.toolResult);
      // Every size posted before this point went to a host that had not
      // finished the handshake (Q-1770).
      resendSize();
    });
  }

  function readOpenAIGlobals() {
    var o = window.openai;
    if (!o) return;
    applyTheme(o.theme);
    readHostModes(o);
    applyDisplayMode(o.displayMode);
    if (o.maxHeight != null) {
      document.documentElement.style.setProperty(
        "--host-max-height",
        o.maxHeight + "px",
      );
    }
    var out = o.toolOutput;
    if (bridgeResultSeen) {
      // The bridge already delivered the complete result.
    } else if (out != null) {
      if (o.toolResponseMetadata === null && !metaGraceOver) {
        // The result is here but its render data is not (Q-1883). The Apps
        // SDK declares every global up front, `null` until filled, and a
        // card-backed result ALWAYS carries `_meta` (`ui.resourceUri` at
        // least), so `null` here means "not landed yet", never "none". An
        // ABSENT key is a host with no metadata channel: nothing to wait for.
        holdForMeta(o);
      } else {
        stopMetaHold();
        deliverResult(out, o.toolResponseMetadata);
      }
    }
    // The metadata is here and the output is not: the result ARRIVED
    // (only a result carries `_meta`). Give `toolOutput` the same grace,
    // then end on the empty-result line — a result with nothing for the
    // card is terminal, never a call still running (Q-1883).
    else if (o.toolResponseMetadata != null) {
      if (metaGraceOver) deliverEnvelope({});
      else holdForMeta(o);
    }
    // No output yet, but the arguments are here: the call is in flight.
    else if (o.toolInput != null) onToolInput(o.toolInput);
    // Neither yet. ChatGPT mounts a card only FOR a tool call — the frame
    // exists because a call does — so an empty frame is a call in flight,
    // not a missing host. `toolInput` can stay null for the whole call:
    // the founder's 2026-09-23 miss line was the 4 s
    // no-input grace firing mid-call for exactly that reason (Q-1883).
    else startInFlight();
    scheduleSize();
  }

  // ── The metadata grace (Q-1883) ───────────────────────────────────
  // `toolOutput` (structuredContent) and `toolResponseMetadata` (`_meta`)
  // are two globals, and a host may fill them in separate updates. They
  // come from ONE tool result, so any gap between them is the host's own
  // delivery ordering. 3 s is twice the widest gap the guard drives
  // (1.5 s) and still short enough that a host which never fills the
  // metadata shows its structured-only card (the designed degradation:
  // `—` where a series would be) before the wait reads as stuck. MCP Apps
  // has no such window: one `tool-result` notification carries
  // `structuredContent` and `_meta` together.
  var META_GRACE_MS = 3000;
  var metaTimer = null;
  var metaGraceOver = false;

  function holdForMeta(o) {
    if (!metaTimer) {
      metaTimer = setTimeout(function () {
        metaTimer = null;
        metaGraceOver = true;
        readOpenAIGlobals();
      }, META_GRACE_MS);
    }
    if (envelopeDelivered) return;
    if (o.toolInput != null) onToolInput(o.toolInput);
    else startInFlight();
  }

  function stopMetaHold() {
    if (metaTimer) clearTimeout(metaTimer);
    metaTimer = null;
  }

  // ── Envelope extraction ───────────────────────────────────────────
  // Keel tools return ONE text content block containing the JSON
  // envelope (url_line / hero_url / summary_metrics / curve / render /
  // ...). Handle: MCP result shapes ({content:[{type:'text',text}]}),
  // FastMCP structuredContent wrapping ({result: "<json>"}), the Apps
  // SDK toolOutput global, and already-parsed objects.
  // The card-only channel (spec 02 §2.1, §3): a result MAY carry the fields
  // only the card draws — series, tiles, render hints — in
  // `_meta["keel/card"]` rather than `structuredContent`. They are merged
  // back OVER the envelope, deep and path-preserving, so every renderer
  // reads one envelope exactly as before. A host that drops `_meta` leaves
  // the envelope as `structuredContent` carried it: the card still renders,
  // with `—` where a series would be.
  var CARD_META_KEY = "keel/card";

  function mergeCard(into, extra) {
    if (
      !into ||
      typeof into !== "object" ||
      !extra ||
      typeof extra !== "object"
    )
      return into;
    Object.keys(extra).forEach(function (k) {
      var v = extra[k];
      var cur = into[k];
      if (Array.isArray(v) && Array.isArray(cur)) {
        for (var i = 0; i < v.length; i++) {
          if (i < cur.length && cur[i] && typeof cur[i] === "object")
            mergeCard(cur[i], v[i]);
        }
      } else if (
        v &&
        typeof v === "object" &&
        !Array.isArray(v) &&
        cur &&
        typeof cur === "object" &&
        !Array.isArray(cur)
      ) {
        mergeCard(cur, v);
      } else {
        into[k] = v;
      }
    });
    return into;
  }

  function cardMetaOf(meta) {
    return meta && typeof meta === "object" && meta[CARD_META_KEY]
      ? meta[CARD_META_KEY]
      : null;
  }

  function parseToolResult(result, meta) {
    var env = parseEnvelope(result);
    var card =
      cardMetaOf(meta) ||
      (result && typeof result === "object" ? cardMetaOf(result._meta) : null);
    // Merge into a COPY: `env` may be the host's own object (ChatGPT's
    // `window.openai.toolOutput`, an MCP Apps `structuredContent`), which a
    // host may freeze — an in-place write throws under "use strict". The
    // envelope is JSON, so a JSON round trip is a faithful deep copy; the
    // card's own subtrees are copied too, so nothing written later can
    // reach the host's `_meta` either.
    if (env && card) return mergeCard(jsonCopy(env), jsonCopy(card));
    return env;
  }

  function jsonCopy(value) {
    return JSON.parse(JSON.stringify(value));
  }

  function parseEnvelope(result) {
    if (result == null) return null;
    if (typeof result === "string") return tryJSON(result);
    var firstText = null;
    if (Array.isArray(result.content)) {
      for (var i = 0; i < result.content.length; i++) {
        var block = result.content[i];
        if (block && block.type === "text" && typeof block.text === "string") {
          var parsed = tryJSON(block.text);
          if (parsed) return parsed;
          if (firstText === null && block.text.trim()) firstText = block.text;
        }
      }
    }
    if (result.structuredContent)
      return parseEnvelope(result.structuredContent);
    if (typeof result.result === "string") return tryJSON(result.result);
    // A CallToolResult whose only text is not our JSON: a framework-level
    // failure (`isError`, "Error executing tool …"). Its words are the
    // error; the result object itself is not an envelope (Q-1773).
    if (Array.isArray(result.content)) {
      return result.isError && firstText
        ? { code: "tool_error", message: firstText.trim() }
        : {};
    }
    if (typeof result === "object") return result;
    return null;
  }

  // A tool result ARRIVED. Whatever it carried, the card must now say
  // something terminal: an unreadable result is "nothing to show", never
  // a card that keeps waiting (Q-1773).
  function deliverResult(raw, meta) {
    var env = parseToolResult(raw, meta);
    deliverEnvelope(env == null ? {} : env);
  }

  function tryJSON(text) {
    try {
      var v = JSON.parse(text);
      return typeof v === "object" ? v : null;
    } catch (e) {
      return null;
    }
  }

  // No latch (Q-1634): a host that sends a second tool-result for the
  // same frame — a refreshed live view, a re-run — re-renders the card.
  // Every renderer clears its body first, so delivery is idempotent.
  function deliverEnvelope(envelope) {
    if (envelope == null) return;
    envelopeDelivered = true;
    callInFlight = false;
    clearSkeleton();
    if (document.body) document.body.removeAttribute("data-expect");
    for (var i = 0; i < resultListeners.length; i++) {
      try {
        resultListeners[i](envelope);
      } catch (e) {
        /* one bad renderer never blocks the fallback link row */
      }
    }
    resendSize();
  }

  function clearSkeleton() {
    var body = document.getElementById("card-body");
    if (!body) return;
    body.removeAttribute("aria-busy");
    var sk = body.querySelector ? body.querySelector(".skeleton") : null;
    if (sk && sk.parentNode) sk.parentNode.removeChild(sk);
  }

  // ── Server-authored prose ─────────────────────────────────────────
  //
  // Q-1630's rule is that no internal id ever reaches drawn text. A card
  // can honour that in the fields it composes itself; it CANNOT honour it
  // in a sentence the server wrote. Staging's `not_found` refusal reads
  // "Backtest btr_00000000000000000000000000 not found.", the card drew
  // it verbatim, and a 26-character ULID landed in the one line a failed
  // lookup shows (Q-1712).
  //
  // So every string a card draws that a SERVER composed — refusal
  // messages, run errors, worker notes, nudges — passes through here,
  // and there is ONE owner of what an id looks like. The token is
  // removed rather than masked: "Backtest btr_… not found." becomes
  // "Backtest not found.", which is the whole of what the sentence had
  // to say. Empty brackets and the doubled space the removal leaves are
  // tidied so the result reads as prose.
  //
  // Returns "" when the message was nothing BUT an id — the caller
  // decides what to say instead, from something the envelope carries
  // (never an invented sentence).
  var ID_TOKEN = /\b(?:str|btr|dep|cmt|shr|int|prn|acc|evt)_[A-Za-z0-9]{6,}\b/g;

  function prose(text) {
    if (text == null) return "";
    var original = String(text);
    ID_TOKEN.lastIndex = 0;
    var stripped = original.replace(ID_TOKEN, "");
    if (stripped === original) return original;
    // The removal leaves `Backtest  not found.`, `()`, `[]`, ` ,` — tidy
    // those and nothing else. ORDER MATTERS: emptied brackets go first,
    // because dropping `()` from `run  () failed` leaves a second
    // doubled space that a collapse run earlier has already passed.
    // Newlines and line indentation survive: a failed run's message is
    // multi-line and the card's own `firstLine` decides how much to show.
    return stripped
      .replace(/\(\s*\)|\[\s*\]|\{\s*\}/g, "")
      .replace(/[ \t]{2,}/g, " ")
      .replace(/[ \t]+([.,;:!?])/g, "$1")
      .replace(/[ \t]+$/gm, "")
      .trim();
  }

  // ── In flight (Q-1769) ────────────────────────────────────────────
  // An MCP Apps host mounts the card when the call STARTS, and the
  // result can be minutes away (`keel_backtest_run` waits for the run).
  // Before a result the card has three honest states — waiting for the
  // arguments, in flight, and a genuine terminal miss — and in flight it
  // draws the SHAPE the result will most likely take. A card that will
  // resolve into one receipt row must never first promise a full
  // evidence card and then collapse: the only allowed jump is ONE grow,
  // small → big.
  //
  // The size is predictable from the call alone, because the server
  // decides it from the tool and its arguments (`present_choice` + each
  // handler's default, keel/tools/outcomes/_base.py, BUILD §2.4). The
  // card sees the arguments in `tool-input` (MCP Apps) / `toolInput`
  // (ChatGPT), and the tool name in `hostContext.toolInfo.tool.name`
  // WHEN the host sends it (optional in the spec; ChatGPT has no
  // equivalent) — so the rules below work from the arguments first and
  // the name only narrows them. The table mirrors those server defaults
  // and `TOOL_INVOCATION_STRINGS` (ChatGPT's tool-row copy, so both hosts
  // say the same outcome-neutral words); tests/test_widgets_inflight.py
  // pins it against the Python owners. `needs` = the tool's required
  // arguments; `present: true` = the tool takes the `present` argument;
  // `dryRun` = what a `dry_run: true` call resolves to.
  var INFLIGHT = {
    keel_backtest_summarize: {
      kind: "backtest",
      needs: ["backtest_id"],
      invoking: "Reading the result…",
      size: "view",
    },
    keel_backtest_run: {
      kind: "backtest",
      needs: ["strategy_id"],
      invoking: "Running backtest…",
      size: "receipt",
      present: true,
    },
    keel_backtest_watch: {
      kind: "backtest",
      needs: ["backtest_id"],
      invoking: "Checking the run…",
      size: "receipt",
      present: true,
    },
    keel_strategy_get: {
      kind: "strategy",
      needs: ["strategy_id"],
      invoking: "Reading the strategy…",
      size: "view",
    },
    keel_strategy_compose: {
      kind: "strategy",
      needs: [],
      invoking: "Composing…",
      size: "view",
      dryRun: "receipt",
      present: true,
    },
    keel_strategy_fork: {
      kind: "strategy",
      needs: ["source"],
      invoking: "Forking…",
      size: "view",
      present: true,
    },
    keel_strategy_diff: {
      kind: "strategy",
      needs: ["ref_a", "ref_b"],
      invoking: "Comparing versions…",
      size: "view",
    },
    keel_library_fork: {
      kind: "strategy",
      needs: ["slug"],
      invoking: "Forking from the library…",
      size: "view",
    },
    keel_backtest_compare: {
      kind: "compare",
      needs: ["backtest_ids"],
      invoking: "Comparing runs…",
      size: "view",
    },
    keel_live_monitor: {
      kind: "live",
      needs: [],
      invoking: "Reading live state…",
      size: "view",
    },
    keel_live_deploy: {
      kind: "preflight",
      needs: ["strategy_id"],
      invoking: null,
      size: "view",
    },
  };

  // With NO arguments this long after mount there is no call behind the
  // card — a host that renders the frame and delivers nothing (Q-1627).
  var NO_INPUT_GRACE_MS = 4000;
  // An in-flight call is treated as lost only past this. It sits above
  // every wait a card tool can take (backtest_watch caps at 600 s,
  // backtest_run's interactive poll at 300 s), so it fires only on a host
  // that went quiet: a skeleton still never promises forever.
  var INFLIGHT_MAX_MS = 15 * 60 * 1000;

  var callInFlight = false;
  var toolName = null;
  var lastArgs = null;

  function cardKind() {
    return (document.body && document.body.getAttribute("data-card")) || "";
  }

  function sizeFor(rule, args) {
    // A dry run's card is its one-row draft check whatever `present` asks
    // for (Q-1848), so it waits as that row and never grows then collapses.
    if (rule.dryRun && args.dry_run === true) return rule.dryRun;
    if (rule.present && (args.present === "receipt" || args.present === "view"))
      return args.present;
    return rule.size;
  }

  // The tools that can have drawn THIS card: the named one when the host
  // says; else every tool of this card's kind whose REQUIRED arguments
  // are all present and that accepts the `dry_run` / `present` it was
  // given (a backtest card called with `strategy_id` is a run, never a
  // summarize); else every tool of the kind.
  function candidates(args) {
    if (toolName && INFLIGHT[toolName]) return [toolName];
    var kind = cardKind();
    var all = [];
    var fit = [];
    for (var t in INFLIGHT) {
      if (!Object.prototype.hasOwnProperty.call(INFLIGHT, t)) continue;
      var r = INFLIGHT[t];
      if (r.kind !== kind) continue;
      all.push(t);
      if (
        args &&
        r.needs.every(function (k) {
          return args[k] != null;
        }) &&
        (args.dry_run == null || r.dryRun) &&
        (args.present == null || r.present)
      )
        fit.push(t);
    }
    return fit.length ? fit : all;
  }

  /**
   * `receipt` or `view`. When the candidates disagree (no tool name, and
   * the arguments fit tools that resolve differently) the answer is the
   * SMALLER: guessing small costs one grow when the result lands,
   * guessing big costs the collapse this section exists to remove. With
   * no arguments yet (`args === undefined`) the question is "could this
   * card be a receipt at all" — so a backtest card waits as a row and a
   * comparison waits as its table.
   */
  function expectedSize(args) {
    var names = candidates(args);
    for (var i = 0; i < names.length; i++) {
      var r = INFLIGHT[names[i]];
      if (args === undefined) {
        if (r.size === "receipt" || r.dryRun === "receipt" || r.present)
          return "receipt";
      } else if (sizeFor(r, args) === "receipt") {
        return "receipt";
      }
    }
    return "view";
  }

  function invokingText(args) {
    var names = candidates(args);
    var first = null;
    for (var i = 0; i < names.length; i++) {
      var s = INFLIGHT[names[i]].invoking;
      if (!s) continue;
      if (first === null) first = s;
      else if (s !== first) return "Working…";
    }
    return first || "Working…";
  }

  function readToolInfo(ctx) {
    var info = ctx && ctx.toolInfo;
    var name = info && info.tool && info.tool.name;
    if (typeof name === "string" && name) {
      toolName = name;
      if (callInFlight && !envelopeDelivered) drawInFlight();
    }
  }

  function setExpect(size) {
    if (document.body) document.body.setAttribute("data-expect", size);
  }

  function onToolInput(args) {
    if (envelopeDelivered) return;
    callInFlight = true;
    lastArgs = args && typeof args === "object" ? args : {};
    drawInFlight();
  }

  /** A call is running and its arguments are not (yet) known. */
  function startInFlight() {
    if (envelopeDelivered || callInFlight) return;
    callInFlight = true;
    drawInFlight();
  }

  // What a card says while its call runs and the arguments are unknown:
  // true of EVERY tool that can draw this kind (a backtest card may be a
  // run, a watch or a read), so it never names the wrong one.
  var KIND_WAITING = {
    backtest: "Working on the backtest…",
    strategy: "Working on the strategy…",
    compare: "Comparing runs…",
    live: "Reading live state…",
  };

  function drawInFlight() {
    var body = document.getElementById("card-body");
    if (!body) return;
    setExpect(expectedSize(lastArgs == null ? undefined : lastArgs));
    var sk = body.querySelector(".skeleton");
    if (!sk) {
      // A terminal line was drawn (the no-input grace ran out) and THEN
      // the arguments arrived: the call is in flight after all.
      body.textContent = "";
      sk = el("div", "skeleton");
      ["sk-line sk-title", "sk-line sk-row"].forEach(function (c) {
        sk.appendChild(el("div", c));
      });
      var tiles = el("div", "sk-tiles");
      for (var i = 0; i < 4; i++) tiles.appendChild(el("i"));
      sk.appendChild(tiles);
      sk.appendChild(el("div", "sk-block sk-table"));
      sk.appendChild(el("div", "sk-block"));
      body.appendChild(sk);
      body.setAttribute("aria-busy", "true");
      var lr = document.getElementById("link-row");
      if (lr) lr.hidden = true;
    }
    var old = sk.querySelector(".sk-live");
    if (old && old.parentNode) old.parentNode.removeChild(old);
    // The receipt row's own markup (`.rr`), so a receipt result swaps in
    // place at the same height. card.css shows it only when the card is
    // expected to BE a receipt; a view keeps the kind's full skeleton.
    var row = el("div", "rr sk-live");
    row.setAttribute("role", "status");
    // A view waits under the card head, which already carries the brand.
    var asReceipt =
      document.body && document.body.getAttribute("data-expect") === "receipt";
    if (asReceipt && wordmark()) row.appendChild(el("span", "brand", "Keel"));
    var nm = lastArgs && typeof lastArgs.name === "string" ? lastArgs.name : "";
    if (nm) {
      var n = el("span", "rr-name", nm);
      n.title = nm;
      row.appendChild(n);
    }
    row.appendChild(
      el(
        "span",
        "rr-win",
        lastArgs == null
          ? KIND_WAITING[cardKind()] || "Working…"
          : invokingText(lastArgs),
      ),
    );
    // First: a receipt hides every other skeleton part; a view shows this
    // line in place of the title bar (card.css), above the shape it becomes.
    sk.insertBefore(row, sk.firstChild);
    sk.classList.add("has-live");
    sk.removeAttribute("aria-hidden");
    scheduleSize();
  }

  /**
   * The honest terminal line: no result came, and none is coming. Reached
   * only on a terminal signal — the host's `tool-cancelled`, a frame with
   * no call behind it past the no-input grace (MCP Apps only), or the
   * 15-minute bound — never while a call or its metadata is on the way
   * (Q-1883). One plain line: with no result there is no id to open, and
   * the old "Ask for the Keel link" told the user to instruct the agent.
   */
  var MISS_TEXT = {
    cancelled: "Stopped before a result came back.",
    lost: "No result reached this card.",
  };
  function showMiss(reason) {
    callInFlight = false;
    stopMetaHold();
    clearSkeleton();
    if (document.body) document.body.removeAttribute("data-expect");
    var body = document.getElementById("card-body");
    if (body) {
      body.textContent = "";
      body.appendChild(el("p", "note", MISS_TEXT[reason] || MISS_TEXT.lost));
    }
    renderLinkRow(null);
    scheduleSize();
  }

  // Before any argument arrives: the smallest shape this card can
  // become, set before first paint so nothing is ever drawn big first.
  setExpect(expectedSize(undefined));

  // ── DOM helpers shared by the card renderers ──────────────────────
  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = String(text);
    return node;
  }

  function stat(label, value, tone) {
    var wrap = el("div", "stat");
    wrap.appendChild(el("span", "label", label));
    wrap.appendChild(el("span", "value" + (tone ? " " + tone : ""), value));
    return wrap;
  }

  function kvTable(pairs) {
    var t = el("table", "kv");
    pairs.forEach(function (pair) {
      if (pair[1] == null || pair[1] === "") return;
      var tr = el("tr");
      tr.appendChild(el("td", "k", pair[0]));
      tr.appendChild(el("td", null, pair[1]));
      t.appendChild(tr);
    });
    return t;
  }

  function cardWidth() {
    var root = document.getElementById("root");
    var w = root ? Math.round(root.getBoundingClientRect().width) : 0;
    return w || document.documentElement.clientWidth || 0;
  }

  function isNarrow() {
    var w = cardWidth();
    return w > 0 && w < NARROW_PX;
  }

  // ── The formatter (Q-1629, Q-1634) ────────────────────────────────
  // Every number a card prints goes through here. It returns
  // {text, tone, missing}: `tone` is "pos" / "neg" / "" and is derived
  // from the RENDERED sign, so colour can never appear without a sign
  // glyph, and a value that rounds to zero at display precision stays
  // neutral and unsigned.
  var DASH = "—"; // em dash — the one "no value" glyph
  var MINUS = "−"; // true minus — the one negative glyph

  function isNum(v) {
    if (v == null || v === "") return false;
    var n = Number(v);
    return !isNaN(n) && isFinite(n);
  }

  function group(s) {
    return String(s).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  }

  function gone() {
    return { text: DASH, tone: "", missing: true };
  }

  function signOf(body, n) {
    // The sign follows the DISPLAYED body: "+$0.00" is a lie at
    // display precision, so a body that rounds to zero renders bare.
    var numeric = parseFloat(String(body).replace(/,/g, ""));
    if (!numeric) return "";
    return n > 0 ? "+" : MINUS;
  }

  function signedDisplay(body, n, unitBefore, unitAfter) {
    var sign = signOf(body, n);
    return {
      text: sign + (unitBefore || "") + body + (unitAfter || ""),
      tone: sign === "+" ? "pos" : sign === MINUS ? "neg" : "",
      missing: false,
    };
  }

  var fmt = {
    DASH: DASH,
    MINUS: MINUS,

    /** Signed money — coloured. "+$1,405.75", "−$12.40", "$0.00". */
    money: function (v, opts) {
      if (!isNum(v)) return gone();
      var n = Number(v);
      var dp = opts && opts.dp != null ? opts.dp : Math.abs(n) >= 1000 ? 0 : 2;
      return signedDisplay(group(Math.abs(n).toFixed(dp)), n, "$", "");
    },

    /** Signed percent (returns) — coloured. "+37.9%", "−4.4%". */
    pct: function (v, opts) {
      if (!isNum(v)) return gone();
      var n = Number(v);
      var dp = opts && opts.dp != null ? opts.dp : 1;
      return signedDisplay(Math.abs(n).toFixed(dp), n, "", "%");
    },

    /** A drawdown — always negative-signed, NEVER coloured. */
    drawdown: function (v, opts) {
      if (!isNum(v)) return gone();
      var dp = opts && opts.dp != null ? opts.dp : 1;
      var body = Math.abs(Number(v)).toFixed(dp);
      return { text: (parseFloat(body) ? MINUS : "") + body + "%", tone: "" };
    },

    /** A drawdown in money — negative-signed, neutral. */
    drawdownMoney: function (v, opts) {
      if (!isNum(v)) return gone();
      var n = Math.abs(Number(v));
      var dp = opts && opts.dp != null ? opts.dp : n >= 1000 ? 0 : 2;
      var body = group(n.toFixed(dp));
      return {
        text: (parseFloat(body.replace(/,/g, "")) ? MINUS : "") + "$" + body,
        tone: "",
      };
    },

    /** A ratio (Sharpe, Sortino, Calmar, profit factor) — neutral. */
    ratio: function (v, opts) {
      if (!isNum(v)) return gone();
      var dp = opts && opts.dp != null ? opts.dp : 2;
      var n = Number(v);
      return {
        text: (n < 0 ? MINUS : "") + Math.abs(n).toFixed(dp),
        tone: "",
      };
    },

    /** An unsigned rate (win rate, fill rate, cost) — neutral. */
    rate: function (v, opts) {
      if (!isNum(v)) return gone();
      var dp = opts && opts.dp != null ? opts.dp : 1;
      return { text: Math.abs(Number(v)).toFixed(dp) + "%", tone: "" };
    },

    /** A count — neutral, grouped. */
    count: function (v) {
      if (!isNum(v)) return gone();
      return { text: group(String(Math.round(Number(v)))), tone: "" };
    },

    /** An unsigned money value (a price, a size, a cost) — neutral. */
    price: function (v, opts) {
      if (!isNum(v)) return gone();
      var n = Math.abs(Number(v));
      var dp = opts && opts.dp != null ? opts.dp : n >= 1000 ? 0 : 2;
      return { text: "$" + group(n.toFixed(dp)), tone: "" };
    },

    /** Any plain string as a display; missing renders the dash. */
    text: function (v) {
      if (v == null || v === "") return gone();
      return { text: String(v), tone: "" };
    },

    /** "since Aug 20" — a date the way a person says one (Q-1630). */
    since: function (v) {
      var d = parseDate(v);
      return d ? "since " + fmtDate(d, false) : null;
    },

    /** "8 months" / "12 days" — an age, never a stamp (Q-1630). */
    age: function (v, now) {
      var a = parseDate(v);
      if (!a) return null;
      var b = now ? parseDate(now) : new Date();
      if (!b) b = new Date();
      return fmtDuration(a, b) || "today";
    },

    /** "7 min ago" — freshness, never a stamp. */
    ago: function (v, now) {
      var d = parseDate(v);
      if (!d) return null;
      var ref = now ? parseDate(now) : new Date();
      var s = Math.max(0, Math.round(((ref || new Date()) - d) / 1000));
      if (s < 90) return "just now";
      var m = Math.round(s / 60);
      if (m < 90) return m + " min ago";
      var h = Math.round(m / 60);
      if (h < 36) return h + (h === 1 ? " hour ago" : " hours ago");
      var days = Math.round(h / 24);
      return days + (days === 1 ? " day ago" : " days ago");
    },

    /** "in 14 min" / "window closed" — a deadline without a stamp. */
    within: function (v, now) {
      var d = parseDate(v);
      if (!d) return null;
      var ref = now ? parseDate(now) : new Date();
      var s = Math.round((d - (ref || new Date())) / 1000);
      if (s <= 0) return "window closed";
      if (s < 90) return "in under a minute";
      var m = Math.round(s / 60);
      if (m < 90) return "in " + m + " min";
      var h = Math.round(m / 60);
      return "in " + h + (h === 1 ? " hour" : " hours");
    },
  };

  function display(v) {
    if (v == null) return gone();
    if (typeof v === "object" && typeof v.text === "string") return v;
    if (v === "") return gone();
    return { text: String(v), tone: "" };
  }

  /** One tile. A missing value renders the dash and KEEPS the tile. */
  function tile(label, value, shortLabel) {
    var d = display(value);
    var node = stat(label, d.text, d.tone || undefined);
    if (d.missing) node.className += " missing";
    if (shortLabel && shortLabel !== label) {
      // The server's short form (Q-1799), drawn only when the full label
      // would be cut — "MAX DRAWDO…" at 481 px. The full word stays the
      // accessible name and the tooltip.
      var lab = node.querySelector(".label");
      lab.setAttribute("data-full", label);
      lab.setAttribute("data-short", shortLabel);
      lab.title = label;
    }
    return node;
  }

  /** Every tile label that does not fit draws its short form (Q-1799). */
  function fitTileLabels() {
    var labs = document.querySelectorAll(".stat .label[data-short]");
    for (var i = 0; i < labs.length; i++) {
      var lab = labs[i];
      lab.textContent = lab.getAttribute("data-full");
      if (lab.scrollWidth > lab.clientWidth + 1) {
        lab.textContent = lab.getAttribute("data-short");
      }
    }
  }
  var tileLabelFitArmed = false;
  function armTileLabelFit() {
    if (!tileLabelFitArmed) {
      tileLabelFitArmed = true;
      resizeListeners.push(fitTileLabels);
    }
    setTimeout(fitTileLabels, 0);
  }

  /**
   * The tile grid: four inline, the rest behind "+N more" in place.
   *
   * `entries` is [[label, display], ...] in priority order. Everything
   * past `opts.inline` (4) is revealed by one button, so a card is
   * never nine tiles tall inline and never hides a number without
   * saying how many it hid.
   */
  function tiles(entries, opts) {
    var inline = opts && opts.inline != null ? opts.inline : 4;
    var host = el("div", "tile-host");
    var grid = el("div", "stat-grid");
    var shown = entries.slice(0, inline);
    var rest = entries.slice(inline);
    shown.forEach(function (e) {
      grid.appendChild(tile(e[0], e[1], e[2]));
    });
    host.appendChild(grid);
    if (rest.length) {
      var more = el("button", "more", "+" + rest.length + " more");
      more.type = "button";
      more.setAttribute("aria-expanded", "false");
      var reveal = function () {
        rest.forEach(function (e) {
          grid.appendChild(tile(e[0], e[1], e[2]));
        });
        if (more.parentNode) more.parentNode.removeChild(more);
        fitTileLabels();
        scheduleSize();
      };
      more.addEventListener("click", function (ev) {
        ev.preventDefault();
        reveal();
      });
      host.appendChild(more);
    }
    host.tileCount = shown.length + rest.length;
    armTileLabelFit();
    return host;
  }

  /**
   * A capped table that ALWAYS names what it dropped (Q-1628, Q-1634),
   * and that becomes a per-row list under 480 px rather than a grid
   * clipped by `overflow: hidden`.
   *
   * spec = {
   *   cols: [{key, label, num, tone, format(value,row) -> display|string}],
   *   narrow: ["key", ...]   // the ≤3 columns a phone gets
   *   cap: 12, remainder: "more in Keel", caption: "..."
   * }
   */
  function table(rows, spec) {
    var cols = spec.cols || [];
    var cap = spec.cap == null ? 12 : spec.cap;
    var narrowKeys = (spec.narrow || []).slice(0, 3);
    var shown = rows.slice(0, cap);
    var dropped = Math.max(0, rows.length - shown.length);
    var host = el("div", "table-host");
    var drawnNarrow = null;

    function valueOf(col, row) {
      var raw = row ? row[col.key] : null;
      var d = col.format ? col.format(raw, row) : display(raw);
      return display(d);
    }

    function grid() {
      var t = el("table", "rows");
      if (spec.caption) {
        var cap0 = el("caption", "sr-only", spec.caption);
        t.appendChild(cap0);
      }
      var head = el("tr");
      cols.forEach(function (c) {
        var th = el("th", c.num ? "num" : null, c.label);
        th.setAttribute("scope", "col");
        head.appendChild(th);
      });
      t.appendChild(head);
      shown.forEach(function (row) {
        var tr = el("tr");
        cols.forEach(function (c) {
          var d = valueOf(c, row);
          var td = el(
            "td",
            (c.num ? "num" : "") + (d.tone ? " " + d.tone : ""),
            d.text,
          );
          tr.appendChild(td);
        });
        t.appendChild(tr);
      });
      return t;
    }

    var droppedCols = 0;

    function rowList() {
      var wrap = el("div", "row-list");
      var keys = narrowKeys.length
        ? narrowKeys
        : cols.slice(0, 3).map(function (c) {
            return c.key;
          });
      var picked = keys
        .map(function (k) {
          for (var i = 0; i < cols.length; i++)
            if (cols[i].key === k) return cols[i];
          return null;
        })
        .filter(Boolean);
      droppedCols = Math.max(0, cols.length - picked.length);
      shown.forEach(function (row) {
        var item = el("div", "row-item");
        picked.forEach(function (c, i) {
          var d = valueOf(c, row);
          var span = el(
            "span",
            (i === 0 ? "lead" : "field") + (d.tone ? " " + d.tone : ""),
          );
          if (i > 0) span.appendChild(el("span", "k", c.label));
          span.appendChild(el("span", "v", d.text));
          item.appendChild(span);
        });
        wrap.appendChild(item);
      });
      return wrap;
    }

    // One line, always, naming everything this rendering dropped — rows
    // and, on a phone, the columns that did not fit (Q-1628 / Q-1634).
    function remainderLine() {
      var parts = [];
      if (dropped > 0) parts.push(dropped + " more rows");
      if (droppedCols > 0) parts.push(droppedCols + " more columns");
      if (!parts.length) return null;
      return el(
        "p",
        "note",
        "+" + parts.join(" and ") + " " + (spec.remainder || "in Keel"),
      );
    }

    function draw() {
      var narrow = isNarrow();
      if (narrow === drawnNarrow) return;
      drawnNarrow = narrow;
      droppedCols = 0;
      host.textContent = "";
      host.appendChild(narrow ? rowList() : grid());
      var line = remainderLine();
      if (line) host.appendChild(line);
    }

    draw();
    resizeListeners.push(function () {
      // A stale host from a previous render is detached — skip it.
      if (host.parentNode) draw();
    });
    host.rowsShown = shown.length;
    host.rowsDropped = dropped;
    return host;
  }

  /**
   * A capped collection of short texts — the table helper's sibling for
   * things that are not rows. `as: "notes"` renders one line each (what
   * a run has to SAY); the default renders chips. Either way the
   * remainder is named, which is the whole contract.
   */
  function list(items, spec) {
    var opts = spec || {};
    var cap = opts.cap == null ? 12 : opts.cap;
    var texts = items.filter(function (s) {
      return s != null && s !== "";
    });
    var shown = texts.slice(0, cap);
    var dropped = Math.max(0, texts.length - shown.length);
    var notes = opts.as === "notes";
    var wrap = el("div", notes ? "note-list" : "chips");
    shown.forEach(function (s) {
      wrap.appendChild(el(notes ? "p" : "span", notes ? "note" : "chip", s));
    });
    if (dropped > 0) {
      var line = "+" + dropped + " " + (opts.remainder || "more in Keel");
      wrap.appendChild(
        notes ? el("p", "note", line) : el("span", "chip muted", line),
      );
    }
    wrap.itemsShown = shown.length;
    wrap.itemsDropped = dropped;
    return wrap;
  }

  /**
   * The one header shape (Q-1630): Keel · name · vN · state · clock.
   *
   * The name is mono and ellipsises; a missing one is "Untitled",
   * never an id. Dates arrive already relative ("since Aug 20").
   */
  function header(spec) {
    var head = document.getElementById("card-head");
    if (!head) return null;
    // A card that drew a receipt row hid this head; drawing a header
    // into it is the statement that it is back.
    head.hidden = false;
    var brand = head.querySelector(".brand");
    if (brand) brand.hidden = !wordmark();
    var title = document.getElementById("card-title");
    if (title && title.parentNode) title.parentNode.removeChild(title);
    var meta = document.getElementById("head-meta");
    if (!meta) {
      meta = el("span", "head-meta");
      meta.id = "head-meta";
      head.appendChild(meta);
    }
    meta.textContent = "";
    var name = el("span", "head-name", spec.name || "Untitled");
    name.title = spec.name || "Untitled";
    meta.appendChild(name);
    if (spec.version != null && spec.version !== "") {
      meta.appendChild(el("span", "chip", "v" + spec.version));
    }
    if (spec.state) {
      meta.appendChild(
        el(
          "span",
          "chip" + (spec.stateTone ? " " + spec.stateTone : ""),
          spec.state,
        ),
      );
    }
    if (spec.clock) meta.appendChild(el("span", "chip muted", spec.clock));
    if (spec.when) meta.appendChild(el("span", "head-when", spec.when));
    return meta;
  }

  /** The receipt line under the tiles — what the numbers belong to. */
  function receipt(parts) {
    var text = (parts || [])
      .filter(function (p) {
        return p != null && p !== "";
      })
      .join(" · ");
    return text ? el("p", "receipt", text) : null;
  }

  /**
   * The one line under a backtest's performance numbers (S-6). ONE owner
   * for the words, so the backtest and compare cards say the same thing.
   * Drawn only where backtest performance is drawn: never on a closed
   * receipt row (it stays one line) and never on the live card, whose
   * numbers are not from a historical run. Plain muted text — not an
   * action, not a link.
   */
  var HISTORICAL_NOTE =
    "Run on historical data · not indicative of future results";
  function historicalNote() {
    return el("p", "hist-note", HISTORICAL_NOTE);
  }

  function fmtNum(v, digits) {
    if (v == null || isNaN(Number(v))) return null;
    return Number(v).toFixed(digits == null ? 2 : digits);
  }

  function fmtPct(v, digits) {
    var n = fmtNum(v, digits);
    return n == null ? null : n + "%";
  }

  function fmtInt(v) {
    if (v == null || isNaN(Number(v))) return null;
    return String(Math.round(Number(v))).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  }

  // Compact money-ish axis labels: 12.5k, 1.2M. Plain below 1000.
  function fmtCompact(v) {
    var n = Number(v);
    if (isNaN(n)) return "";
    var a = Math.abs(n);
    if (a >= 1e9) return (n / 1e9).toFixed(1) + "B";
    if (a >= 1e6) return (n / 1e6).toFixed(1) + "M";
    if (a >= 1e4) return (n / 1e3).toFixed(1) + "k";
    if (a >= 1e3) return (n / 1e3).toFixed(2) + "k";
    return n.toFixed(a >= 100 ? 0 : 2);
  }

  /**
   * Labels for a value axis, ONE format per axis (Q-1802). `fmtCompact`
   * picks its precision per VALUE — `5.00k` under `10.0k` — which reads as
   * three different measurements. One unit for the whole axis (from its
   * largest value), then the fewest decimals that state every tick
   * exactly (`5k · 10k · 15k`, `9.5k · 10.0k · 10.5k`); for ticks that
   * are not round numbers, the fewest that still tell them apart.
   */
  function fmtAxisValues(values) {
    var vs = [];
    for (var i = 0; i < values.length; i++) vs.push(Number(values[i]));
    var max = 0;
    vs.forEach(function (v) {
      if (Math.abs(v) > max) max = Math.abs(v);
    });
    var div = 1;
    var unit = "";
    if (max >= 1e9) {
      div = 1e9;
      unit = "B";
    } else if (max >= 1e6) {
      div = 1e6;
      unit = "M";
    } else if (max >= 1e3) {
      div = 1e3;
      unit = "k";
    }
    function at(dp) {
      return vs.map(function (v) {
        var t = (v / div).toFixed(dp);
        if (Number(t) === 0) return "0";
        return (t.charAt(0) === "-" ? MINUS + t.slice(1) : t) + unit;
      });
    }
    function exact(dp) {
      return vs.every(function (v) {
        var back = Number((v / div).toFixed(dp)) * div;
        return Math.abs(back - v) <= 1e-9 * Math.max(1, Math.abs(v));
      });
    }
    function distinct(labels) {
      var seen = {};
      for (var j = 0; j < labels.length; j++) {
        if (seen[labels[j]]) return false;
        seen[labels[j]] = true;
      }
      return true;
    }
    var dp;
    for (dp = 0; dp <= 2; dp++) if (exact(dp)) return at(dp);
    for (dp = 0; dp <= 2; dp++) if (distinct(at(dp))) return at(dp);
    return at(2);
  }

  var MONTHS = [
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
  ];

  // Accepts "2026-01-03", "2026-01-03T00:00:00+00:00", "…Z", Date.
  function parseDate(v) {
    if (v == null) return null;
    if (v instanceof Date) return isNaN(v.getTime()) ? null : v;
    if (typeof v !== "string") return null;
    // A DATE-ONLY string is a calendar date in UTC (a backtest window is
    // a date range, not an instant). Anything carrying a time keeps it —
    // matching the prefix here silently truncated every instant to UTC
    // midnight, which made "7 min ago" read "15 hours ago".
    var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(v);
    if (m) {
      return new Date(Date.UTC(+m[1], +m[2] - 1, +m[3]));
    }
    var d = new Date(v);
    return isNaN(d.getTime()) ? null : d;
  }

  function fmtDate(v, withYear) {
    var d = parseDate(v);
    if (!d) return typeof v === "string" ? v : null;
    var s = MONTHS[d.getUTCMonth()] + " " + d.getUTCDate();
    return withYear ? s + ", " + d.getUTCFullYear() : s;
  }

  /**
   * Axis labels for a time axis, ONE format per axis (Q-1783). Labelling
   * only the ends with a year gave "Aug 9, 2024 · Apr 24 · Jan 6 · Sep 21,
   * 2026" — "Apr 24" read as a date in the wrong year. A span of four
   * months or more is labelled by month and year ("Aug 2024"); a shorter
   * span that crosses a year carries the year on every label; a short
   * span inside one year says the year once, on the last.
   */
  function fmtAxisDates(times) {
    var ds = [];
    for (var i = 0; i < times.length; i++) {
      var d = parseDate(
        times[i] instanceof Date ? times[i] : new Date(times[i]),
      );
      if (d) ds.push(d);
    }
    if (!ds.length) return [];
    var a = ds[0];
    var b = ds[ds.length - 1];
    var days = (b.getTime() - a.getTime()) / 86400000;
    var oneYear = a.getUTCFullYear() === b.getUTCFullYear();
    return ds.map(function (d, j) {
      if (days >= 120)
        return MONTHS[d.getUTCMonth()] + " " + d.getUTCFullYear();
      if (!oneYear) return fmtDate(d, true);
      return fmtDate(d, j === ds.length - 1);
    });
  }

  function fmtDuration(a, b) {
    var ms = b.getTime() - a.getTime();
    var days = Math.round(ms / 86400000);
    if (days < 1) return null;
    if (days < 60) return days + (days === 1 ? " day" : " days");
    var months = Math.round(days / 30.4375);
    if (months < 24) return months + " months";
    var years = days / 365.25;
    return (
      (Math.round(years * 10) / 10).toFixed(1).replace(/\.0$/, "") + " years"
    );
  }

  // The ONE display rule (agent-surface-cleanup spec 03 §2.5): a run's
  // window is half-open on the wire — `end` is the first day NOT covered —
  // so a range a person reads ends on the LAST COVERED day: the served
  // `last_bar` when the view carries it, else `end` minus one day (windows
  // are midnight-pinned; the server's own pre-completion rule). The same
  // rule the SDK's `_window_line` and the app's `lastCoveredDay` apply.
  function lastCoveredDay(w) {
    if (!w) return null;
    if (typeof w.last_bar === "string" && w.last_bar) return w.last_bar;
    var e = parseDate(w.end);
    if (!e) return null;
    return new Date(e.getTime() - 86400000).toISOString().slice(0, 10);
  }

  // "Jul 27, 2024 – Sep 22, 2026 · 2.2 years" for a view window: the range
  // ends on the last covered day, the duration counts the covered days.
  function fmtWindow(w) {
    if (!w) return null;
    var a = parseDate(w.start);
    var last = parseDate(lastCoveredDay(w));
    if (!a && !last) return null;
    if (!a || !last) return fmtDate(a || last, true);
    var sameYear = a.getUTCFullYear() === last.getUTCFullYear();
    var range = sameYear
      ? fmtDate(a, false) + " – " + fmtDate(last, true)
      : fmtDate(a, true) + " – " + fmtDate(last, true);
    var end = parseDate(w.end) || new Date(last.getTime() + 86400000);
    var dur = fmtDuration(a, end);
    return dur ? range + " · " + dur : range;
  }

  // "Jan 3 – Sep 12, 2026 · 8 months"; spans years as
  // "Aug 15, 2024 – Feb 27, 2026 · 18 months".
  function fmtPeriod(start, end) {
    var a = parseDate(start);
    var b = parseDate(end);
    if (!a && !b) return null;
    if (!a || !b) return fmtDate(a || b, true);
    var sameYear = a.getUTCFullYear() === b.getUTCFullYear();
    var range = sameYear
      ? fmtDate(a, false) + " – " + fmtDate(b, true)
      : fmtDate(a, true) + " – " + fmtDate(b, true);
    var dur = fmtDuration(a, b);
    return dur ? range + " · " + dur : range;
  }

  // ── Supersession (BUILD §2.9) ─────────────────────────────────────
  //
  // Claude's own documentation (connectors/building/mcp-apps/
  // instance-supersession): every tool call that renders an MCP App
  // mounts a SEPARATE iframe and there is no host API to unmount an
  // earlier one — but all widget iframes from one connector share a
  // sandbox origin, so a `BroadcastChannel` opened in one instance
  // reaches every other instance in the conversation.
  //
  // The server stamps each `view` with an election key (`object` —
  // what the card is ABOUT — plus a server-minted `at` and a
  // per-process `seq`); each instance announces its key; an instance
  // that hears a YOUNGER sibling for the SAME object marks itself
  // superseded and re-renders as its own receipt with a chip. It never
  // blanks or removes itself: the transcript stays honest, the card
  // just stops being the biggest thing on screen.
  //
  // The key is SERVER-minted on purpose. A client `Date.now()`
  // mis-orders lazily mounted cells on reopen, which would mark the
  // newest card superseded by the oldest — the wrong one every time.
  var ELECTION_CHANNEL = "keel-cards";
  var instanceId =
    "i" + Math.random().toString(36).slice(2) + Date.now().toString(36);
  var myKey = null;
  var supersededKey = null;
  var supersedeListeners = [];
  var channel = null;

  function isYounger(a, b) {
    // `at` decides; `seq` breaks a tie inside one server clock tick.
    var ta = Date.parse(a && a.at);
    var tb = Date.parse(b && b.at);
    if (isFinite(ta) && isFinite(tb) && ta !== tb) return ta > tb;
    return Number((a && a.seq) || 0) > Number((b && b.seq) || 0);
  }

  function announce() {
    if (!myKey || !channel) return;
    try {
      channel.postMessage(myKey);
    } catch (e) {
      /* a host that closed the channel simply never elects */
    }
  }

  function onSibling(ev) {
    var msg = ev && ev.data;
    if (!msg || !myKey) return;
    if (msg.instanceId === instanceId) return;
    // Different objects never supersede each other: two strategies side
    // by side both stay full.
    if (msg.object !== myKey.object) return;
    if (isYounger(msg, myKey)) {
      if (supersededKey && !isYounger(msg, supersededKey)) return;
      supersededKey = msg;
      for (var i = 0; i < supersedeListeners.length; i++) {
        try {
          supersedeListeners[i](supersededKey);
        } catch (e) {
          /* one bad renderer never breaks the election */
        }
      }
    } else {
      // We are the younger one, and this sibling mounted after us — it
      // missed our first announcement (lazily mounted cells do). Say it
      // again so it can stand down. It will not answer twice: its own
      // `supersededKey` is then equal, not younger.
      announce();
    }
  }

  function electFrom(envelope) {
    var view = envelope && envelope.view;
    if (!view || !view.object || myKey) return;
    myKey = {
      object: view.object,
      at: view.at || null,
      seq: view.seq != null ? view.seq : 0,
      version: view.version != null ? view.version : null,
      instanceId: instanceId,
    };
    if (typeof BroadcastChannel !== "function") return;
    try {
      channel = new BroadcastChannel(ELECTION_CHANNEL);
    } catch (e) {
      return; // no channel on this host — the defaults stand
    }
    channel.addEventListener("message", onSibling);
    announce();
  }

  /**
   * ONE receipt row (BUILD §4.1), shared by every card that has one.
   *
   * `Keel` · name (mono, ellipsis — the first thing to shrink at
   * 390 px) · `v{n}` · state chip · the three numbers · an inline
   * `Open ↗` · a disclosure that opens the full layout IN PLACE. The
   * row is ~36 px tall plus the card's own padding, and draws no
   * action band while closed — `renderLinkRow` above honours
   * `data-size="receipt"` for exactly that reason.
   *
   * A second hand-rolled row in each card is the F-2 defect again, so
   * this is the only place the shape exists. `spec` is data, never
   * DOM:
   *
   *   {name, version, state, stateTone, chips: [{text, tone, drop}],
   *    superseded, nums: {sharpe, ret, dd}, window, err, hunk, note,
   *    provenance, url, disclosure: {label, build(bodyEl)}}
   *
   * `note` is what a change says when it has no single hunk ("X added",
   * a summary); `provenance` is where the object stands ("Draft ·
   * updated Sep 23") — the one item the name outranks outright.
   *
   * Returns the wrapper; `wrapper.openInPlace()` toggles the
   * disclosure programmatically (the card's own `present`/test path).
   */
  function receiptRow(spec) {
    if (document.body) {
      document.body.setAttribute("data-size", "receipt");
      // The adapter owns the action row; the card owns only this row.
      document.body.setAttribute("data-owns-link", "0");
    }
    var head = document.getElementById("card-head");
    if (head) head.hidden = true;
    var wrap = el("div", "rr-wrap");
    var row = el("div", "rr");
    // Two groups (Q-1772). From 480 px up both are `display: contents`,
    // so the row is the one flat line it always was; below 480 px each is
    // a line of its own — who it is (name · version · state) over what it
    // says and what you can do (chips · numbers · change, actions pinned
    // to the right edge). A wrap of one flat row left the actions
    // floating mid-card on a third line.
    var l1 = el("span", "rr-l1");
    var l2 = el("span", "rr-l2");
    row.appendChild(l1);
    row.appendChild(l2);
    // The row's own aria-label names what the three unlabelled numbers
    // are, in the order they are drawn (Q-1633).
    var aria = [];
    if (wordmark()) l1.appendChild(el("span", "brand", "Keel"));
    var name = spec.name || "Untitled";
    var nm = el("span", "rr-name", name);
    nm.title = name;
    l1.appendChild(nm);
    aria.push(name);
    if (spec.version != null && spec.version !== "") {
      l1.appendChild(el("span", "chip", "v" + spec.version));
      aria.push("v" + spec.version);
    }
    if (spec.state) {
      l1.appendChild(
        el(
          "span",
          "chip" + (spec.stateTone ? " " + spec.stateTone : ""),
          spec.state,
        ),
      );
      aria.push(spec.state);
    }
    (spec.chips || []).forEach(function (c) {
      if (!c || !c.text) return;
      var chipEl = el("span", "chip" + (c.tone ? " " + c.tone : ""), c.text);
      if (c.drop) chipEl.setAttribute("data-drop", String(c.drop));
      l2.appendChild(chipEl);
      aria.push(c.text);
    });
    // Superseded comes AFTER the state group and before any change, on
    // every card kind: name · version · state · (superseded) · (change).
    // It used to ride in `chips`, so a preview read `superseded by v4`
    // before `Preview`.
    if (spec.superseded) {
      var sup = el("span", "chip muted", spec.superseded);
      sup.setAttribute("data-drop", "1");
      l2.appendChild(sup);
      aria.push(spec.superseded);
    }
    if (spec.nums) {
      var n = el("span", "rr-nums");
      var sh = fmt.ratio(spec.nums.sharpe);
      var rt = fmt.pct(spec.nums.ret);
      var dd = fmt.drawdown(spec.nums.dd);
      n.appendChild(el("span", sh.tone || null, sh.text));
      n.appendChild(el("span", rt.tone || null, rt.text));
      n.appendChild(el("span", dd.tone || null, dd.text));
      var read =
        "Sharpe " +
        sh.text +
        ", return " +
        rt.text +
        ", max drawdown " +
        dd.text;
      n.setAttribute("aria-label", read);
      n.setAttribute("data-drop", "4");
      l2.appendChild(n);
      aria.push(read);
    }
    if (spec.window) {
      var wn = el("span", "rr-win", spec.window);
      wn.setAttribute("data-drop", "3");
      l2.appendChild(wn);
      aria.push(spec.window);
    }
    if (spec.err) {
      var e = el("span", "rr-err", spec.err);
      e.title = spec.err;
      l2.appendChild(e);
      aria.push(spec.err);
    }
    // The one change a receipt shows: where it is, and what it was
    // before. The old value stays struck through beside the new one —
    // a receipt for a save is only legible if it says what moved.
    if (spec.hunk && spec.hunk.key != null) {
      var hk = spec.hunk;
      var sc = el("span", "rr-sc");
      // The block path is its own element (Q-1795): capped in CSS, and
      // the part `fitRow` hides first, so a long component name costs
      // the path — never the param and its two values.
      var kk = el("span", "k");
      if (hk.path) {
        var pth = el("span", "p", hk.path);
        pth.title = hk.path;
        kk.appendChild(pth);
        kk.appendChild(el("span", "sep", " · "));
        sc.setAttribute("data-compact", "1");
      }
      kk.appendChild(document.createTextNode(String(hk.key)));
      sc.appendChild(kk);
      var val = el("span", "v");
      if (hk.oldValue != null)
        val.appendChild(el("span", "old", String(hk.oldValue)));
      val.appendChild(el("span", "new", String(hk.newValue)));
      sc.appendChild(val);
      sc.setAttribute("data-drop", "2");
      l2.appendChild(sc);
      aria.push(
        (hk.path ? hk.path + " " : "") +
          hk.key +
          " " +
          (hk.oldValue != null ? hk.oldValue + " to " : "") +
          hk.newValue,
      );
    }
    if (spec.note) {
      var nt = el("span", "rr-win", spec.note);
      nt.title = spec.note;
      nt.setAttribute("data-drop", "2");
      l2.appendChild(nt);
      aria.push(spec.note);
    }
    // Provenance (Q-1797) goes BEFORE the name is squeezed at all
    // (`data-soft`): "Draft · updated Sep 23" beside a name cut to
    // "Simple Me…Perps)" said the least useful thing in the most room.
    if (spec.provenance) {
      var pv = el("span", "rr-win rr-prov", spec.provenance);
      pv.title = spec.provenance;
      pv.setAttribute("data-drop", "3");
      pv.setAttribute("data-soft", "1");
      l2.appendChild(pv);
      aria.push(spec.provenance);
    }
    var acts = el("span", "rr-acts");
    if (spec.url) {
      var a = el("a", "rr-act", "Open ↗");
      a.href = spec.url;
      a.title = spec.url;
      a.addEventListener("click", function (ev) {
        ev.preventDefault();
        KeelHost.openLink(spec.url);
      });
      acts.appendChild(a);
    }
    var body = null;
    if (spec.disclosure && typeof spec.disclosure.build === "function") {
      var label = spec.disclosure.label || "Show result";
      var hideLabel = label.replace(/^Show/, "Hide");
      var btn = el("button", "rr-act");
      btn.type = "button";
      var setLabel = function (open) {
        btn.textContent = open ? hideLabel : label;
        btn.appendChild(el("span", "rr-caret", open ? "▴" : "▾"));
        btn.setAttribute("aria-expanded", open ? "true" : "false");
      };
      var toggle = function () {
        if (body) {
          if (body.parentNode) body.parentNode.removeChild(body);
          body = null;
          if (document.body) document.body.setAttribute("data-size", "receipt");
          setLabel(false);
        } else {
          body = el("div", "rr-body");
          wrap.appendChild(body);
          spec.disclosure.build(body);
          // Opened: the standard two-action row returns, drawn once by
          // the adapter beneath the opened layout.
          if (document.body)
            document.body.setAttribute("data-size", "receipt-open");
          setLabel(true);
        }
        renderLinkRow(lastEnvelope);
        // Opening hides the row's own Open and its change (card.css,
        // Q-1778) — the room they held goes back to the name.
        fitRow(row);
        scheduleSize();
      };
      setLabel(false);
      btn.addEventListener("click", function (ev) {
        ev.preventDefault();
        if (isOpenAI && !body) openOnChatGPT(spec, toggle);
        else toggle();
      });
      acts.appendChild(btn);
      wrap.openInPlace = toggle;
    }
    l2.appendChild(acts);
    row.setAttribute("role", "group");
    row.setAttribute(
      "aria-label",
      aria
        .filter(function (t) {
          return t;
        })
        .join(", "),
    );
    wrap.appendChild(row);
    watchFit(row);
    // The adapter drew the action band BEFORE the card rendered (the
    // envelope listener runs `renderLinkRow` then the card), so the
    // band a receipt must not have is already on screen by the time
    // `data-size` says so. Re-draw it here — once, from the one owner
    // — rather than asking every card to remember.
    renderLinkRow(lastEnvelope);
    return wrap;
  }

  // ── Opening a receipt on ChatGPT (Q-1774) ─────────────────────────
  // ChatGPT sizes the widget frame itself (BUILD §4.5): an in-place open
  // grows the document, `notifyIntrinsicHeight` reports it, and the host
  // keeps the frame at the receipt's one row — the opened layout renders
  // below the fold of a 36 px frame and the click reads as doing
  // nothing. So on ChatGPT "Show …" asks for fullscreen, where every card
  // draws its full layout; if the host does not grant it, the Keel link
  // opens instead; with neither, the in-place open is still attempted.
  function openOnChatGPT(spec, toggle) {
    var o = window.openai;
    var fallback = function () {
      if (spec.url) KeelHost.openLink(spec.url);
      else toggle();
    };
    if (!o || typeof o.requestDisplayMode !== "function") {
      fallback();
      return;
    }
    var p;
    try {
      p = o.requestDisplayMode({ mode: "fullscreen" });
    } catch (e) {
      fallback();
      return;
    }
    Promise.resolve(p).then(
      function (res) {
        var granted = res && res.mode ? res.mode : o.displayMode;
        if (granted === "fullscreen") {
          applyDisplayMode("fullscreen");
          renderLinkRow(lastEnvelope);
        } else {
          fallback();
        }
      },
      function () {
        fallback();
      },
    );
  }

  // ── Fitting the receipt row (Q-1771) ──────────────────────────────
  // The row is one line from 480 px up, and until now only the NAME
  // could shrink: between ~480 and ~640 px everything after it ran off
  // the card's right edge and was clipped — "Show result" read "Sho",
  // "Open" read "Op". The rule (BUILD §4 / runbook B11): the actions and
  // the state chip are never dropped or clipped, and lower-priority
  // items go — whole, never cut — before the name is squeezed past an
  // ellipsis. Priority is data on the element (`data-drop`), set where
  // the item is made:
  //   1  superseded-by chip        (the card below already says it)
  //   2  change chip / change note (a chip marked `data-compact` first
  //      sheds its block path, Q-1795, and goes whole only if the
  //      param and its values alone still do not fit)
  //   3  provenance / window / count chips
  //   4  the three numbers         (they are one tap away in Show result)
  // An item also marked `data-soft` (provenance, Q-1797) goes before the
  // one-line name is cut AT ALL; every other item only once the name
  // would fall under NAME_ROOM_PX.
  // Every dropped item stays in the row's aria-label.
  var NAME_ROOM_PX = 120;

  /** The one-line name, when something on its line has cut it. */
  function nameSqueezed(row) {
    var nm = row.querySelector(".rr-name");
    if (!nm || !nm.parentNode) return false;
    if (getComputedStyle(nm.parentNode).display !== "contents") return false;
    return nm.scrollWidth > nm.clientWidth + 1;
  }

  function rowFits(row) {
    // One line (wide): the row overflows. Two lines (narrow): either line
    // does. A `display: contents` group has no box to measure.
    var boxes = [row].concat(
      Array.prototype.slice.call(row.querySelectorAll(".rr-l1, .rr-l2")),
    );
    for (var i = 0; i < boxes.length; i++) {
      var b = boxes[i];
      if (getComputedStyle(b).display === "contents") continue;
      if (b.scrollWidth > b.clientWidth + 1) return false;
    }
    // The name's room is only worth spending where something droppable
    // shares its line — the one-line layout.
    var nm = row.querySelector(".rr-name");
    var oneLine =
      nm &&
      nm.parentNode &&
      getComputedStyle(nm.parentNode).display === "contents";
    if (oneLine && nm.scrollWidth > nm.clientWidth + 1) {
      if (nm.clientWidth < NAME_ROOM_PX) return false;
    }
    return true;
  }

  function fitRow(row) {
    if (!row.isConnected || !row.clientWidth) return;
    var nm = row.querySelector(".rr-name");
    // Measure against the WHOLE name: a name already cut to fit would
    // read as "not truncated" and the drops below would never run.
    if (nm && nm.getAttribute("data-full") !== null)
      nm.textContent = nm.getAttribute("data-full");
    var items = row.querySelectorAll("[data-drop]");
    var i;
    for (i = 0; i < items.length; i++) items[i].classList.remove("rr-dropped");
    var compactable = row.querySelectorAll("[data-compact]");
    for (i = 0; i < compactable.length; i++)
      compactable[i].classList.remove("rr-compact");
    if (!rowFits(row) || nameSqueezed(row)) {
      var soft = row.querySelectorAll("[data-soft]");
      for (i = 0; i < soft.length; i++) soft[i].classList.add("rr-dropped");
    }
    // Shrink before dropping (Q-1796): a change chip sheds its block path
    // before the name is cut or ANY item goes — the superseded chip
    // included. Compacting only at the change chip's own turn meant a
    // superseded save lost "superseded by v3" first, to spare a path the
    // opened pipeline already shows.
    if (!rowFits(row) || nameSqueezed(row)) {
      for (i = 0; i < compactable.length; i++)
        compactable[i].classList.add("rr-compact");
    }
    for (var p = 1; p <= 4; p++) {
      if (rowFits(row)) break;
      for (i = 0; i < items.length; i++) {
        if (items[i].getAttribute("data-drop") === String(p))
          items[i].classList.add("rr-dropped");
      }
    }
    if (nm) middleTruncate(nm);
  }

  /**
   * Cut a name in the MIDDLE so its end survives (Q-1780). Strategy names
   * differ at the end — "Simple Mean Reversion (Top 20 Perps, 10D)" /
   * "…, 30D)" — and a trailing ellipsis cut exactly the part that told
   * them apart. The whole name stays in `title` and the aria-label.
   */
  function middleTruncate(nm) {
    var full = nm.getAttribute("data-full");
    if (full === null) {
      full = nm.textContent;
      nm.setAttribute("data-full", full);
    }
    nm.textContent = full;
    if (nm.scrollWidth <= nm.clientWidth + 1) return;
    var lo = 2;
    var hi = full.length - 1;
    var best = null;
    while (lo <= hi) {
      var k = (lo + hi) >> 1;
      var head = Math.ceil(k * 0.55);
      var s =
        full.slice(0, head).replace(/\s+$/, "") +
        "…" +
        full.slice(full.length - (k - head)).replace(/^\s+/, "");
      nm.textContent = s;
      if (nm.scrollWidth <= nm.clientWidth + 1) {
        best = s;
        lo = k + 1;
      } else {
        hi = k - 1;
      }
    }
    nm.textContent = best || full;
  }

  function watchFit(row) {
    var fit = function () {
      fitRow(row);
    };
    if (typeof ResizeObserver === "function") {
      try {
        new ResizeObserver(fit).observe(row);
      } catch (e) {
        /* the resize listener below still refits */
      }
    }
    resizeListeners.push(fit);
    setTimeout(fit, 0);
    if (document.fonts && document.fonts.ready && document.fonts.ready.then)
      document.fonts.ready.then(fit, function () {});
  }

  // ── Results with nothing to draw (Q-1773) ─────────────────────────
  // Two shapes reached the cards and rendered as a result: an EMPTY
  // object (ChatGPT's `toolOutput` for a tool result that carries no
  // `structuredContent` — every error envelope today) drew "Untitled",
  // four dashed tiles and "Ask for the Keel link"; and an error envelope
  // (`code` + `message`, no `view`) reached cards with no error state at
  // all. The backtest and comparison cards draw their own error lines
  // ("Backtest not submitted — …", "Comparison unavailable — …"); for
  // every other kind, and for an empty result on any kind, the adapter
  // draws one terminal line itself. Neither ever shows the action band.
  var CARDS_OWNING_ERRORS = ["backtest", "compare"];
  var UNAVAILABLE = {
    backtest: "Backtest unavailable",
    strategy: "Strategy unavailable",
    live: "Live view unavailable",
    preflight: "Preflight unavailable",
    compare: "Comparison unavailable",
  };

  function isEmptyResult(env) {
    if (!env || typeof env !== "object") return true;
    for (var k in env) {
      if (Object.prototype.hasOwnProperty.call(env, k)) return false;
    }
    return true;
  }

  /** The message of an error envelope (spec §13.5), or null. */
  function errorMessage(env) {
    if (!env || typeof env !== "object" || env.view) return null;
    if (typeof env.code === "string" && typeof env.message === "string")
      return env.message;
    return null;
  }

  /**
   * An error's words under a card's own lead (Q-1800). keel-api phrases a
   * refusal "Cannot backtest — start_date … is after end_date …" for the
   * model, and every card already leads with what failed ("Backtest not
   * submitted — "), so the line read "Backtest not submitted — Cannot
   * backtest — …". The card owns the lead: the server's failure preamble
   * (up to its first dash) is dropped here, and only here. The model's
   * text block keeps the message verbatim.
   */
  var FAILURE_LEAD =
    /^(?:Cannot|Can't|Could not|Couldn't|Unable to)\b[^—\n]{0,40}?\s+—\s+/;
  function withoutFailureLead(message) {
    var text = String(message == null ? "" : message);
    var rest = text.replace(FAILURE_LEAD, "");
    return rest || text;
  }

  // ── A plan limit is a state, not a failure (Q-1806) ───────────────
  // At 50 of 50 the backtest card drew its red error line with the
  // envelope's `message` — which then ended in a directive written for
  // the model ("Report the numbers in `example` … do not retry"). A plan
  // limit is not an error: the call was fine, the allowance is spent. The
  // handoff envelope carries `limit_view` — human sentences the SDK
  // renders (what happened, when it resets, on the free plan that paid
  // plans include more, what does not use the allowance) — and every card
  // kind draws it here, in the ordinary text colour. It draws NO link and
  // no button (mcp-conversion D-12): the "See plans in Keel" button opened
  // the billing tab, whose plan buttons start Stripe Checkout, and OpenAI
  // rejected the app for it (Q-2080). A `link` or `paths` key a stale or
  // foreign envelope still carries is ignored.
  function planLimitOf(env) {
    if (!env || typeof env !== "object" || env.view) return null;
    if (env.code !== "handoff_required") return null;
    var v = env.limit_view;
    return v && typeof v === "object" && typeof v.headline === "string"
      ? v
      : null;
  }

  function renderPlanLimit(v) {
    clearSkeleton();
    var b = document.body;
    if (b) {
      b.removeAttribute("data-expect");
      b.setAttribute("data-size", "receipt");
      // The state carries no link, and the fallback band stays away too.
      b.setAttribute("data-owns-link", "1");
      b.setAttribute("data-no-hero", "1");
    }
    var head = document.getElementById("card-head");
    if (head) head.hidden = true;
    var body = document.getElementById("card-body");
    if (!body) return;
    body.textContent = "";
    var box = el("div", "plan-limit");
    var top = el("div", "pl-head");
    if (wordmark()) top.appendChild(el("span", "brand", "Keel"));
    top.appendChild(el("span", "pl-headline", v.headline));
    box.appendChild(top);
    if (typeof v.reset === "string")
      box.appendChild(el("p", "pl-reset muted", v.reset));
    if (typeof v.plans === "string")
      box.appendChild(el("p", "pl-plans", v.plans));
    if (typeof v.note === "string")
      box.appendChild(el("p", "pl-note muted", v.note));
    body.appendChild(box);
    renderLinkRow(null);
    scheduleSize();
  }

  function renderTerminal(message) {
    clearSkeleton();
    var b = document.body;
    if (b) {
      b.removeAttribute("data-expect");
      b.setAttribute("data-size", "receipt");
      b.setAttribute("data-owns-link", "0");
      b.setAttribute("data-no-hero", "1");
    }
    var head = document.getElementById("card-head");
    if (head) head.hidden = true;
    var body = document.getElementById("card-body");
    if (!body) return;
    body.textContent = "";
    var row = el("div", "err-line");
    if (wordmark()) row.appendChild(el("span", "brand", "Keel"));
    if (message) {
      var label = UNAVAILABLE[cardKind()] || "Result unavailable";
      row.appendChild(
        el("span", "msg", label + " — " + withoutFailureLead(message)),
      );
    } else {
      // An empty result: the call finished, and the reply beside the card
      // carries whatever it said. Say that, and nothing that invites a
      // request for a link that does not exist.
      row.appendChild(
        el("span", "muted", "No result to show here — see the reply."),
      );
    }
    body.appendChild(row);
    renderLinkRow(null);
  }

  // ── The non-negotiable fallback link row ──────────────────────────
  var LINK_LABELS = {
    backtest: "Open tearsheet in Keel",
    strategy: "Open strategy in Keel",
    live: "Open live view in Keel",
    preflight: "Continue in Keel",
    // A comparison has one destination that is specific — the strategy
    // the runs belong to (V-6). When the runs span strategies there is
    // no single target and the card passes no url at all (Q-1686).
    compare: "Open strategy in Keel",
  };

  /**
   * Whether this host wants us to draw the `Keel` wordmark (REVIEW §4.4).
   *
   * ChatGPT renders the app's name above every widget itself, so a
   * wordmark inside the card is the name twice. Claude and the other
   * MCP Apps hosts do not, so the card carries it. ONE owner for the
   * rule: `header()` below and `receiptRow()` both ask this, and so
   * does every card that draws its own head.
   */
  function wordmark() {
    return KeelHost.dialect !== "openai";
  }

  function hostOffersFullscreen() {
    return hostDisplayModes.indexOf("fullscreen") >= 0;
  }

  function buttonLink(label, onPress) {
    var a = el("a", "button-link", label);
    a.href = "#";
    a.addEventListener("click", function (ev) {
      ev.preventDefault();
      onPress();
    });
    return a;
  }

  function renderLinkRow(envelope) {
    var row = document.getElementById("link-row");
    if (!row) return;
    if (document.body && document.body.getAttribute("data-owns-link") === "1") {
      row.hidden = true;
      row.textContent = "";
      return;
    }
    // A card that drew its own action OWNS the link, and says so on <body>.
    // This row used to be hidden by the card after the fact, which lost a
    // race: every display-mode change re-runs this function, and its first
    // act is `row.hidden = false`. In fullscreen the card's link and this
    // row both rendered — two links to the same place, different labels,
    // one rule apart (F-5, 2026-09-22 review). Ownership is durable;
    // toggling `hidden` afterwards is not.

    // A CLOSED receipt draws no action band (BUILD §4.1). The bordered
    // row costs ~52 px with its rule and margins, which would make a
    // "one-line" receipt ~100 px — taller than the card it summarises,
    // and ten receipts in a turn ten bordered buttons. Opening the row
    // in place flips `data-size` to `receipt-open` and the SAME row
    // renders beneath the opened body, which is the one action row
    // every other card already draws (F-2).
    if (
      document.body &&
      document.body.getAttribute("data-size") === "receipt"
    ) {
      row.hidden = true;
      row.textContent = "";
      return;
    }

    // A result with no url draws no link line at all (Q-1883). It used to
    // tell the reader to ask the agent for a Keel link — an
    // instruction to the user about the agent, and false wherever no
    // single destination exists (Q-1686's cross-strategy comparison, which
    // still declares that absence as `data-no-hero`).
    var kind = document.body && document.body.getAttribute("data-card");
    var label = LINK_LABELS[kind] || "View in Keel";
    var url = null;
    if (envelope) {
      url = envelope.hero_url || envelope.share_url || null;
      if (!url && envelope.render) url = envelope.render.fallback_url || null;
      if (!url && typeof envelope.url_line === "string") {
        var m = envelope.url_line.match(/https?:\/\/\S+/);
        if (m) url = m[0];
      }
    }
    row.hidden = false;
    row.textContent = "";
    if (url) {
      var a = buttonLink(label + " ↗", function () {
        KeelHost.openLink(url);
      });
      a.href = url;
      a.title = url;
      row.appendChild(a);
    } else if (
      envelope &&
      envelope.view &&
      typeof envelope.view === "object" &&
      envelope.view.parse_error === true
    ) {
      // A source that did not parse cannot be saved, so "ask to save it"
      // would invite a request that fails (Q-1840); this row says nothing
      // rather than something untrue. (The strategy card draws a dry run
      // as a closed draft-check row, which hides this band anyway, Q-1848.)
    } else if (envelope && envelope.dry_run) {
      // A dry run has not persisted anything, so there is no link to ask for
      // — saying "ask for the Keel link" invites the reader to request
      // something that cannot exist yet. Say what is true and what earns one.
      // Reachable since Q-1686 stopped compose inventing a link to the
      // strategy LIST when it had no strategy.
      row.appendChild(
        el("span", "muted", "Not saved yet — ask to save it to open in Keel."),
      );
    }
    // The second (and last) action: Expand, only where the host says it
    // can honour it, and only from inline (Q-1632).
    if (hostOffersFullscreen() && currentDisplayMode !== "fullscreen") {
      row.appendChild(
        buttonLink("Expand", function () {
          KeelHost.requestDisplayMode("fullscreen");
        }),
      );
    }
  }

  // ── Public host API ───────────────────────────────────────────────
  var KeelHost = {
    dialect: isOpenAI ? "openai" : "mcp-apps",
    wire: WIRE,
    el: el,
    stat: stat,
    tile: tile,
    tiles: tiles,
    withoutFailureLead: withoutFailureLead,
    table: table,
    list: list,
    header: header,
    receipt: receipt,
    historicalNote: historicalNote,
    receiptRow: receiptRow,
    wordmark: wordmark,
    kvTable: kvTable,

    /** Re-draw the one action row (after an in-place open). */
    refreshLinkRow: function () {
      renderLinkRow(lastEnvelope);
    },

    /** The younger sibling that superseded this card, or null. */
    supersededBy: function () {
      return supersededKey;
    },

    /**
     * What a superseded card says, or null (Q-1775). ONE owner for the
     * words, so every card says the same thing. A younger sibling showing
     * the SAME version of the same object — a run's receipt followed by
     * its own summary, a save followed by a read of it — is not a newer
     * thing: it is this, again, below. Saying "superseded by v1" on v1
     * read as if the card were stale.
     */
    supersededLabel: function () {
      var key = supersededKey;
      if (!key) return null;
      var mine = myKey && myKey.version != null ? String(myKey.version) : null;
      if (key.version != null && mine !== null && String(key.version) === mine)
        return "shown below";
      return key.version != null
        ? "superseded by v" + key.version
        : "superseded";
    },

    /** cb(key) when a younger sibling for the same object appears. */
    onSuperseded: function (cb) {
      supersedeListeners.push(cb);
      if (supersededKey) cb(supersededKey);
    },
    fmt: fmt,
    display: display,
    cardWidth: cardWidth,
    isNarrow: isNarrow,
    fmtNum: fmtNum,
    fmtPct: fmtPct,
    fmtInt: fmtInt,
    fmtCompact: fmtCompact,
    fmtAxisValues: fmtAxisValues,
    fmtDate: fmtDate,
    fmtAxisDates: fmtAxisDates,
    fmtPeriod: fmtPeriod,
    fmtWindow: fmtWindow,
    lastCoveredDay: lastCoveredDay,

    /**
     * A string the SERVER composed, with internal ids removed (Q-1712).
     *
     * Every card passes server prose through this before drawing it:
     * refusal messages, run errors, worker notes, nudges. Returns "" if
     * the text was nothing but an id.
     */
    prose: prose,
    parseDate: parseDate,
    renderLinkRow: renderLinkRow,
    reportSize: scheduleSize,
    notifySize: scheduleSize,

    // onResize(cb): cb(widthPx) when the container width OR the display
    // mode changes — renderers redraw width- and mode-dependent content
    // here (the SVG chart, the narrow table form, a fullscreen layout).
    onResize: function (cb) {
      resizeListeners.push(cb);
    },

    /** The mode the host is showing this card in right now. */
    displayMode: function () {
      return currentDisplayMode;
    },

    /** The modes the HOST says it can offer (empty until it says). */
    get availableDisplayModes() {
      return hostDisplayModes.slice();
    },

    /** Ask the host to change mode — user-gesture only. */
    requestDisplayMode: function (mode) {
      if (isOpenAI) {
        if (typeof window.openai.requestDisplayMode === "function") {
          return window.openai.requestDisplayMode({ mode: mode });
        }
        return Promise.resolve(null);
      }
      return request(WIRE.requestDisplayMode, { mode: mode }).then(
        function (res) {
          // The host answers with the mode it actually granted.
          applyDisplayMode((res && res.mode) || mode);
          renderLinkRow(lastEnvelope);
          return res;
        },
        function () {
          /* the host declined — the card stays inline */
        },
      );
    },

    callTool: function (name, args) {
      if (isOpenAI) return window.openai.callTool(name, args || {});
      return request(WIRE.callTool, { name: name, arguments: args || {} });
    },

    openLink: function (href) {
      if (isOpenAI) return window.openai.openExternal({ href: href });
      return request(WIRE.openLink, { url: href });
    },

    sendMessage: function (text) {
      if (isOpenAI) return window.openai.sendFollowUpMessage({ prompt: text });
      return request(WIRE.message, {
        role: "user",
        content: [{ type: "text", text: text }],
      });
    },

    // ready(cb): cb(envelope) whenever tool output is available — every
    // time, not once (Q-1634). The fallback link row renders in BOTH
    // branches — with an envelope (canonical URL fields) and without one
    // (simulated/real widget data failure), so a host that renders the
    // frame but never delivers data still shows the user a way forward.
    ready: function (cb) {
      resultListeners.push(function (envelope) {
        lastEnvelope = envelope;
        // A previous terminal line's "nothing to open" is not this
        // result's; the card re-declares it if it holds.
        if (document.body) document.body.removeAttribute("data-no-hero");
        if (isEmptyResult(envelope)) {
          renderTerminal(null);
          return;
        }
        var limit = planLimitOf(envelope);
        if (limit) {
          renderPlanLimit(limit);
          return;
        }
        var err = errorMessage(envelope);
        if (err && CARDS_OWNING_ERRORS.indexOf(cardKind()) < 0) {
          renderTerminal(err);
          return;
        }
        // Announce this instance's election key BEFORE the card draws,
        // so a sibling that is already mounted hears us in the same
        // turn (BUILD §2.9).
        electFrom(envelope);
        renderLinkRow(envelope);
        cb(envelope);
      });
      watchSize();
      if (isOpenAI) readOpenAIGlobals();
      handshake().catch(function () {
        /* no bridge handshake — the globals (ChatGPT) or the fallback
           row (no host) stand */
      });
      scheduleSize();
      // Data never arrived → say so. The skeleton is a promise that a
      // result is coming; when it is not, the body must stop implying
      // one (Q-1627). A call IN FLIGHT is a result that IS coming
      // (Q-1769): the short grace applies only to a card with no call
      // behind it; an in-flight call ends on `tool-cancelled` or the
      // long bound.
      setTimeout(function () {
        if (!envelopeDelivered && !callInFlight) showMiss("lost");
      }, NO_INPUT_GRACE_MS);
      setTimeout(function () {
        if (!envelopeDelivered) showMiss("lost");
      }, INFLIGHT_MAX_MS);
    },
  };

  window.KeelHost = KeelHost;
})();
