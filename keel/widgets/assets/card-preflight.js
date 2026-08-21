/* Deploy-preflight card — renders keel_live_deploy's PREVIEW envelope.
 * FULL/UNLISTED PROFILE ONLY (spec 06 R2): this file is never composed
 * into a listed-profile bundle and the policy scan proves its absence.
 *
 * Render-only (D2.2): server-computed sizing (deploy_intent.
 * suggested_config, drawdown-in-$ basis), estimated slippage/fees the
 * preview already returned, confirmation expiry, and the link row
 * (handoff_url when minted). The card never computes sizing and never
 * confirms the deploy itself — confirmation stays in the tool flow.
 */
(function () {
  "use strict";
  var H = window.KeelHost;

  H.ready(function (env) {
    var body = document.getElementById("card-body");
    body.textContent = "";
    var preview = env.preview || {};
    var intent = env.deploy_intent || {};
    var sizing = intent.suggested_config || {};

    var grid = H.el("div", "stat-grid");
    if (sizing.sizing_usd != null) {
      grid.appendChild(
        H.stat("Suggested sizing", "$" + H.fmtNum(sizing.sizing_usd, 0)),
      );
    }
    if (sizing.expected_drawdown_usd != null) {
      grid.appendChild(
        H.stat(
          "Expected drawdown",
          "$" + H.fmtNum(sizing.expected_drawdown_usd, 0),
          "neg",
        ),
      );
    }
    var slip = H.fmtNum(preview.est_slippage, 4);
    if (slip != null) grid.appendChild(H.stat("Est. slippage", slip));
    var fees = H.fmtNum(preview.est_fees, 4);
    if (fees != null) grid.appendChild(H.stat("Est. fees", fees));
    if (grid.children.length) body.appendChild(grid);

    body.appendChild(
      H.kvTable([
        ["Strategy", preview.strategy_id],
        ["Account", preview.account_id],
        ["Schedule", preview.schedule || preview.derived_schedule],
        ["Sizing basis", sizing.sizing_basis],
        ["Confirmation expires", env.confirmation_expires_at],
      ]),
    );

    if (env.sync_note) body.appendChild(H.el("p", "note", env.sync_note));
    body.appendChild(
      H.el(
        "p",
        "note",
        "Preview only — nothing is deployed until you confirm in the tool flow (or via the handoff link below).",
      ),
    );
  });
})();
