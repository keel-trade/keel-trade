/* Backtest result card — renders keel_backtest_summarize's envelope.
 * Render-only (D2.2): headline metrics + period, the R1 equity/DD
 * embed when available, and the plain link row (always).
 */
(function () {
  "use strict";
  var H = window.KeelHost;

  H.ready(function (env) {
    var body = document.getElementById("card-body");
    body.textContent = "";
    var m = env.summary_metrics || {};

    var grid = H.el("div", "stat-grid");
    var sharpe = H.fmtNum(m.sharpe != null ? m.sharpe : m.sharpe_ratio);
    if (sharpe != null) grid.appendChild(H.stat("Sharpe", sharpe));
    var ret = H.fmtPct(
      m.total_return_pct != null ? m.total_return_pct : m.total_return,
    );
    if (ret != null)
      grid.appendChild(
        H.stat("Return", ret, Number.parseFloat(ret) >= 0 ? "pos" : "neg"),
      );
    var dd = H.fmtPct(
      m.max_drawdown_pct != null ? m.max_drawdown_pct : m.max_drawdown,
    );
    if (dd != null) grid.appendChild(H.stat("Max drawdown", dd, "neg"));
    var win = H.fmtPct(m.win_rate_pct != null ? m.win_rate_pct : m.win_rate);
    if (win != null) grid.appendChild(H.stat("Win rate", win));
    var turnover = H.fmtNum(m.turnover);
    if (turnover != null) grid.appendChild(H.stat("Turnover", turnover));
    var carry = H.fmtNum(m.funding_attribution);
    if (carry != null) grid.appendChild(H.stat("Carry", carry));
    var fills = m.num_trades != null ? m.num_trades : null;
    if (fills != null) grid.appendChild(H.stat("Fills", fills));
    if (grid.children.length) body.appendChild(grid);

    var period = env.period || {};
    body.appendChild(
      H.kvTable([
        ["Strategy", env.strategy_name || env.strategy_id],
        ["Run", env.run_id],
        ["Status", env.status],
        [
          "Period",
          period.start_date && period.end_date
            ? period.start_date + " → " + period.end_date
            : null,
        ],
        ["Engine", env.engine],
      ]),
    );

    if (env.error_message) {
      body.appendChild(H.el("p", "note", env.error_message));
    }
    H.mountEmbed(body, env);
  });
})();
