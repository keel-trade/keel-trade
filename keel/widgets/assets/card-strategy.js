/* Strategy glass-box card — renders keel_strategy_get's envelope.
 * Render-only (D2.2): name/status/version facts, the DSL source when
 * the tool returned it, the R1 strategy embed, and the link row.
 */
(function () {
  "use strict";
  var H = window.KeelHost;

  H.ready(function (env) {
    var body = document.getElementById("card-body");
    body.textContent = "";
    var meta = env.metadata || {};

    var pairs = [
      ["Name", meta.name || meta.strategy_name],
      ["Strategy", env.run_id || meta.id],
      ["Status", meta.status],
      ["Version", env.version != null ? env.version : meta.head_version],
      ["Updated", meta.updated_at],
      ["Description", meta.description],
    ];
    body.appendChild(H.kvTable(pairs));

    var src = env.source;
    if (src && typeof src === "object") src = src.source || src.dsl || null;
    if (typeof src === "string" && src.trim()) {
      var pre = H.el("pre", "source");
      pre.textContent = src;
      body.appendChild(pre);
    }
    H.mountEmbed(body, env);
  });
})();
