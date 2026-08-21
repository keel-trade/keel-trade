/* Keel host adapter — one card codebase, two host dialects.
 *
 * Primary dialect: MCP Apps (io.modelcontextprotocol/ui) — postMessage
 * JSON-RPC to the parent host frame. Thin secondary dialect: the
 * ChatGPT Apps SDK (window.openai), mapped 1:1:
 *
 *   KeelHost.callTool     -> openai.callTool          | "tools/call"
 *   KeelHost.openLink     -> openai.openExternal      | "ui/open-link"
 *   KeelHost.sendMessage  -> openai.sendFollowUpMessage | WIRE.message
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
    toolInput: "ui/notifications/tool-input",
    toolResult: "ui/notifications/tool-result",
    hostContext: "ui/notifications/host-context-changed",
  };

  var isOpenAI = !!(
    window.openai && typeof window.openai.callTool === "function"
  );

  // ── JSON-RPC over postMessage (MCP Apps dialect) ──────────────────
  var nextId = 1;
  var pending = {};
  var resultListeners = [];
  var envelopeDelivered = false;

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

  function applyTheme(theme) {
    if (theme === "dark" || theme === "light") {
      document.documentElement.setAttribute("data-theme", theme);
    }
  }

  function handleHostContext(ctx) {
    if (ctx && ctx.theme) applyTheme(ctx.theme);
  }

  if (!isOpenAI) {
    window.addEventListener("message", function (event) {
      var msg = event.data;
      if (!msg || msg.jsonrpc !== "2.0") return;
      if (msg.id != null && pending[msg.id]) {
        var p = pending[msg.id];
        delete pending[msg.id];
        if (msg.error) p.reject(new Error(msg.error.message || "host error"));
        else p.resolve(msg.result);
        return;
      }
      if (msg.method === WIRE.toolResult) {
        deliverEnvelope(
          parseToolResult(msg.params && (msg.params.result || msg.params)),
        );
      } else if (msg.method === WIRE.toolInput) {
        /* input arrives before result; nothing to render yet */
      } else if (msg.method === WIRE.hostContext) {
        handleHostContext(msg.params);
      }
    });
  } else {
    window.addEventListener("openai:set_globals", function () {
      applyTheme(window.openai.theme);
      var out = window.openai.toolOutput;
      if (out != null) deliverEnvelope(parseToolResult(out));
    });
  }

  // ── Envelope extraction ───────────────────────────────────────────
  // Keel tools return ONE text content block containing the JSON
  // envelope (url_line / hero_url / summary_metrics / render / ...).
  // Handle: MCP result shapes ({content:[{type:'text',text}]}),
  // FastMCP structuredContent wrapping ({result: "<json>"}), the
  // Apps SDK toolOutput global, and already-parsed objects.
  function parseToolResult(result) {
    if (result == null) return null;
    if (typeof result === "string") return tryJSON(result);
    if (Array.isArray(result.content)) {
      for (var i = 0; i < result.content.length; i++) {
        var block = result.content[i];
        if (block && block.type === "text" && typeof block.text === "string") {
          var parsed = tryJSON(block.text);
          if (parsed) return parsed;
        }
      }
    }
    if (result.structuredContent)
      return parseToolResult(result.structuredContent);
    if (typeof result.result === "string") return tryJSON(result.result);
    if (typeof result === "object") return result;
    return null;
  }

  function tryJSON(text) {
    try {
      var v = JSON.parse(text);
      return typeof v === "object" ? v : null;
    } catch (e) {
      return null;
    }
  }

  function deliverEnvelope(envelope) {
    if (envelopeDelivered || envelope == null) return;
    envelopeDelivered = true;
    for (var i = 0; i < resultListeners.length; i++) {
      try {
        resultListeners[i](envelope);
      } catch (e) {
        /* one bad renderer never blocks the fallback link row */
      }
    }
  }

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

  function fmtNum(v, digits) {
    if (v == null || isNaN(Number(v))) return null;
    return Number(v).toFixed(digits == null ? 2 : digits);
  }

  function fmtPct(v) {
    var n = fmtNum(v);
    return n == null ? null : n + "%";
  }

  // ── The non-negotiable fallback link row ──────────────────────────
  function renderLinkRow(envelope) {
    var row = document.getElementById("link-row");
    if (!row) return;
    var url = null;
    var label = "View in Keel";
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
      row.appendChild(document.createTextNode(label + ": "));
      var a = el("a", null, url);
      a.href = url;
      a.addEventListener("click", function (ev) {
        ev.preventDefault();
        KeelHost.openLink(url);
      });
      row.appendChild(a);
    } else {
      row.appendChild(
        el("span", "muted", "Ask for the Keel link to view this in the app."),
      );
    }
  }

  // Embed helper (spec 06 R1/R2): the ONLY fetch/frame target is the
  // Keel app's embed route, taken verbatim from the tool envelope
  // (render.embed_url). Signed-token auth rides inside that URL when
  // the server mints one; on load failure the frame hides and the
  // metrics + link row stand alone.
  function mountEmbed(container, envelope) {
    var url = envelope && envelope.render && envelope.render.embed_url;
    if (!url) return;
    var frame = el("iframe", "embed-frame");
    frame.src = url;
    frame.loading = "lazy";
    frame.referrerPolicy = "no-referrer";
    frame.addEventListener("error", function () {
      frame.remove();
    });
    container.appendChild(frame);
  }

  // ── Public host API ───────────────────────────────────────────────
  var KeelHost = {
    dialect: isOpenAI ? "openai" : "mcp-apps",
    wire: WIRE,
    el: el,
    stat: stat,
    kvTable: kvTable,
    fmtNum: fmtNum,
    fmtPct: fmtPct,
    renderLinkRow: renderLinkRow,
    mountEmbed: mountEmbed,

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

    // ready(cb): cb(envelope) once tool output is available. The
    // fallback link row renders in BOTH branches — with an envelope
    // (canonical URL fields) and without one (simulated/real widget
    // data failure), so a host that renders the frame but never
    // delivers data still shows the user a way forward.
    ready: function (cb) {
      resultListeners.push(function (envelope) {
        renderLinkRow(envelope);
        cb(envelope);
      });
      if (isOpenAI) {
        applyTheme(window.openai.theme);
        var out = window.openai.toolOutput;
        if (out != null) deliverEnvelope(parseToolResult(out));
      } else {
        request(WIRE.initialize, {
          appInfo: { name: "keel-cards", version: "1.0.0" },
          appCapabilities: {},
          protocolVersion: PROTOCOL_VERSION,
        })
          .then(function (res) {
            // The handshake completes with the `initialized`
            // notification; only then does the host deliver tool
            // input + result.
            post({ jsonrpc: "2.0", method: WIRE.initialized });
            if (res && res.hostContext) handleHostContext(res.hostContext);
            if (res && res.toolResult)
              deliverEnvelope(parseToolResult(res.toolResult));
          })
          .catch(function () {
            /* no host handshake — leave the fallback row */
          });
      }
      // Data never arrived → render the fallback-only state.
      setTimeout(function () {
        if (!envelopeDelivered) renderLinkRow(null);
      }, 4000);
    },
  };

  window.KeelHost = KeelHost;
})();
