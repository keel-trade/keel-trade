/* Live view card — renders keel_live_monitor's envelope. Render-only
 * (D2.2): the returned view's rows (positions / portfolio / history
 * slices) plus the freshness note the tool already carries, the R1
 * live embed, and the link row.
 */
(function () {
  "use strict";
  var H = window.KeelHost;

  var ROW_KEYS = [
    "asset",
    "coin",
    "symbol",
    "side",
    "position",
    "value",
    "pnl",
    "unrealized_pnl",
    "entry_price",
    "mark_price",
  ];
  var ROW_LABELS = {
    pnl: "P&L",
    unrealized_pnl: "Unrealized P&L",
    entry_price: "Entry",
    mark_price: "Mark",
  };

  function rowsFrom(data) {
    if (Array.isArray(data)) return data;
    if (data && Array.isArray(data.positions)) return data.positions;
    if (data && Array.isArray(data.items)) return data.items;
    return null;
  }

  H.ready(function (env) {
    var body = document.getElementById("card-body");
    body.textContent = "";

    var head = H.el("p");
    if (env.view) head.appendChild(H.el("span", "badge", env.view));
    if (env.run_id) head.appendChild(H.el("span", "badge", env.run_id));
    if (head.children.length) body.appendChild(head);

    var rows = rowsFrom(env.data);
    if (rows && rows.length) {
      var cols = [];
      ROW_KEYS.forEach(function (key) {
        if (
          rows.some(function (row) {
            return row && row[key] != null;
          })
        )
          cols.push(key);
      });
      if (!cols.length) cols = Object.keys(rows[0] || {}).slice(0, 6);
      var table = H.el("table", "rows");
      var thead = H.el("tr");
      cols.forEach(function (col) {
        thead.appendChild(
          H.el("th", null, ROW_LABELS[col] || col.replace(/_/g, " ")),
        );
      });
      table.appendChild(thead);
      rows.slice(0, 12).forEach(function (row) {
        var tr = H.el("tr");
        cols.forEach(function (col) {
          var v = row && row[col];
          tr.appendChild(H.el("td", null, v == null ? "" : v));
        });
        table.appendChild(tr);
      });
      body.appendChild(table);
    } else if (env.data && typeof env.data === "object") {
      var pairs = Object.keys(env.data)
        .slice(0, 10)
        .map(function (key) {
          var v = env.data[key];
          return [
            key.replace(/_/g, " "),
            typeof v === "object" ? JSON.stringify(v) : v,
          ];
        });
      body.appendChild(H.kvTable(pairs));
    }

    var fresh = env.freshness;
    if (fresh) {
      var freshText =
        typeof fresh === "string" ? fresh : fresh.note || fresh.source || null;
      if (freshText) body.appendChild(H.el("p", "note", String(freshText)));
    }
    H.mountEmbed(body, env);
  });
})();
