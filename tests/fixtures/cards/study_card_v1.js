/* PINNED for test_study_card_browser.py seed 1 (13 §7.1): the study card as it was before RI-602 (keel-sdk card-study.js at b2e3ca05ab^), whose parser expects number lists. Never served. */
/* Study card (research spec 07 §3.3): drawn only from the envelope's
   `card_summary` (07 §3.7, one owner for the tile and the card), never a
   fetch and never a computed number (A13).

   - Header: the kind label, title, version and state words.
   - Headline: the served sentence (numbers, never an adjective).
   - One picture: the key chart's served `thumb` (quantised points), else the
     top-5 checks list; drawn at card size, quoting no number from it (V2).
   - Four numbers: `card_summary.numbers`, each with its served label and
     sample under it.
   - One footing line: `card_summary.footing_line`, served words.
   - Actions: the adapter's one link row ("Open … in Keel"), and at most one
     served follow-up message. No card action spends anything or calls a
     tool that is not read-only.
   - In flight: the receipt size with the progress words.
   - Error: the error's own words.
   Every word comes from the envelope: this file carries no English. */
(function () {
  "use strict";
  var H = window.KeelHost;
  var el = H.el;
  var NS = "http://www.w3.org/2000/svg";
  var lastEnv = null;
  var MAX_NUMBERS = 4;
  var MAX_ROWS = 5;

  function svg(tag, attrs) {
    var node = document.createElementNS(NS, tag);
    Object.keys(attrs || {}).forEach(function (k) {
      node.setAttribute(k, attrs[k]);
    });
    return node;
  }

  function points(thumb) {
    var facts = (thumb && thumb.facts) || {};
    var series =
      facts.series || (facts.points ? [{ points: facts.points }] : []);
    return series
      .slice(0, 3)
      .map(function (s) {
        return (s.points || s.y || []).filter(function (v) {
          return typeof v === "number" && isFinite(v);
        });
      })
      .filter(function (p) {
        return p.length > 1;
      });
  }

  function picture(summary) {
    var lines = points(summary.thumb);
    if (lines.length) {
      var w = 300;
      var h = 64;
      var all = [].concat.apply([], lines);
      var lo = Math.min.apply(null, all);
      var hi = Math.max.apply(null, all);
      var span = hi - lo || 1;
      var box = svg("svg", {
        viewBox: "0 0 " + w + " " + h,
        class: "st-thumb",
        role: "img",
      });
      lines.forEach(function (p, i) {
        var d = p
          .map(function (v, j) {
            var x = (j / (p.length - 1)) * w;
            var y = h - ((v - lo) / span) * (h - 4) - 2;
            return (j ? "L" : "M") + x.toFixed(1) + " " + y.toFixed(1);
          })
          .join(" ");
        box.appendChild(svg("path", { d: d, class: "st-line st-line-" + i }));
      });
      return box;
    }
    var top = summary.top5;
    if (!top || !top.rows || !top.rows.length) return null;
    var list = el("ul", "st-checks");
    top.rows.slice(0, MAX_ROWS).forEach(function (row) {
      var item = el("li", "st-check");
      item.appendChild(el("span", "st-check-label", row.label));
      item.appendChild(
        el("span", "st-check-value", row.value_words || row.value),
      );
      list.appendChild(item);
    });
    return list;
  }

  function numbers(summary) {
    var rows = (summary.numbers || []).slice(0, MAX_NUMBERS);
    if (!rows.length) return null;
    var wrap = el("div", "st-numbers");
    rows.forEach(function (n) {
      var cell = el("div", "st-number");
      cell.appendChild(el("span", "st-value", n.value_words || n.value));
      cell.appendChild(el("span", "st-label", n.label));
      if (n.sample_words)
        cell.appendChild(el("span", "st-sample", n.sample_words));
      wrap.appendChild(cell);
    });
    return wrap;
  }

  function render(env) {
    var body = document.getElementById("card-body");
    if (!body) return;
    body.textContent = "";
    body.removeAttribute("aria-busy");
    if (env.code && env.message) {
      body.appendChild(el("p", "st-error", env.message));
      return;
    }
    var summary = env.card_summary || {};
    var study = env.study || {};
    var kind = (summary.kind_label || study.kind_label || {}).short;
    H.header({
      name: study.title || summary.title,
      version: study.version,
      meta: kind,
    });
    var view = env.view || {};
    var progress = env.progress && env.progress.words;
    if (view.size === "receipt" || (!env.card_summary && progress)) {
      body.appendChild(
        el("p", "st-receipt", progress || summary.state_words || ""),
      );
      if (document.body) document.body.setAttribute("data-size", "receipt");
      return;
    }
    var headline =
      summary.headline && (summary.headline.text || summary.headline.sentence);
    if (headline) body.appendChild(el("p", "st-headline", headline));
    var pic = picture(summary);
    if (pic) body.appendChild(pic);
    var nums = numbers(summary);
    if (nums) body.appendChild(nums);
    if (summary.footing_line)
      body.appendChild(el("p", "st-footing", summary.footing_line));
    if (document.body) {
      document.body.setAttribute("data-owns-link", "0");
      document.body.removeAttribute("data-size");
    }
    if (typeof H.refreshLinkRow === "function") H.refreshLinkRow();
    if (typeof H.notifySize === "function") H.notifySize();
  }

  H.ready(function (env) {
    lastEnv = env || {};
    render(lastEnv);
  });

  if (typeof H.onSuperseded === "function") {
    H.onSuperseded(function () {
      if (lastEnv) render(lastEnv);
    });
  }
})();
