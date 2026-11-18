(function (H) {
  var F = H.fmt;
  var SVG_NS = "http://www.w3.org/2000/svg";
  function svg(tag, attrs, cls) {
    var node = document.createElementNS(SVG_NS, tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (k) {
        node.setAttribute(k, attrs[k]);
      });
    }
    if (cls) node.setAttribute("class", cls);
    return node;
  }

  function text(x, y, str, attrs, cls) {
    var t = svg("text", attrs, cls);
    t.setAttribute("x", x);
    t.setAttribute("y", y);
    t.textContent = str;
    return t;
  }

  function ticks(lo, hi, count) {
    if (!(hi > lo)) return [lo];
    var raw = (hi - lo) / count;
    var mag = Math.pow(10, Math.floor(Math.log(raw) / Math.LN10));
    var norm = raw / mag;
    var step = (norm >= 5 ? 10 : norm >= 2 ? 5 : norm >= 1 ? 2 : 1) * mag;
    var out = [];
    for (var v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) {
      out.push(Math.round(v / step) * step);
    }
    return out.length ? out : [lo, hi];
  }
  function drawMulti(container, series, opts, width) {
    container.textContent = "";
    if (!series.length) return;
    var W = Math.max(240, Math.floor(width || container.clientWidth || 600));
    var tall = !!opts.tall;
    var padL = 44;
    var padR = 10;
    var padT = 10;
    var gap = 14;
    var padB = 18;
    var eqH = tall ? 280 : W < 420 ? 130 : 170;
    var ddH = opts.dd ? (tall ? 96 : W < 420 ? 48 : 60) : 0;
    var Hgt = padT + eqH + (opts.dd ? gap + ddH : 0) + padB;
    var plotW = W - padL - padR;

    var t0 = Infinity;
    var t1 = -Infinity;
    var lo = Infinity;
    var hi = -Infinity;
    var ddMin = 0;
    series.forEach(function (s) {
      s.pts.forEach(function (p) {
        if (p.t < t0) t0 = p.t;
        if (p.t > t1) t1 = p.t;
        if (p.equity < lo) lo = p.equity;
        if (p.equity > hi) hi = p.equity;
        if (p.dd < ddMin) ddMin = p.dd;
      });
    });
    if (hi === lo) {
      hi += 1;
      lo -= 1;
    }
    var pad = (hi - lo) * 0.06;
    var yLo = lo - pad;
    var yHi = hi + pad;
    if (ddMin > -0.5) ddMin = -0.5;

    function X(t) {
      return padL + (t1 === t0 ? 0 : ((t - t0) / (t1 - t0)) * plotW);
    }
    function Y(v) {
      return padT + eqH - ((v - yLo) / (yHi - yLo)) * eqH;
    }
    var ddTop = padT + eqH + gap;
    function YD(d) {
      return ddTop + (d / ddMin) * ddH;
    }

    // The shaded spans: union minus the common window, in plot pixels.
    var shade = [];
    var ov = opts.overlap;
    if (ov && t1 > t0) {
      var a = Math.max(t0, Math.min(t1, ov.t0));
      var b = Math.max(t0, Math.min(t1, ov.t1));
      if (a > t0) shade.push([t0, a]);
      if (b < t1) shade.push([b, t1]);
    }
    var root = svg("svg", {
      viewBox: "0 0 " + W + " " + Hgt,
      width: "100%",
      height: Hgt,
      role: "img",
      // The label carries the READING, not the chart's name (Q-1633).
      "aria-label":
        series.length +
        " equity curves overlaid over " +
        H.fmtPeriod(new Date(t0), new Date(t1)) +
        ". " +
        series[0].label +
        " is the baseline and is drawn heaviest." +
        (shade.length
          ? " Shaded: dates not every run covers; every run covers " +
            H.fmtPeriod(new Date(ov.t0), new Date(ov.t1)) +
            "."
          : ""),
    });
    var paneH = eqH + (opts.dd ? gap + ddH : 0);
    shade.forEach(function (span) {
      var x0 = X(span[0]);
      var w = Math.max(1, X(span[1]) - x0);
      root.appendChild(
        svg(
          "rect",
          { x: x0.toFixed(1), y: padT, width: w.toFixed(1), height: paneH },
          "outside",
        ),
      );
    });
    var grid = svg("g", null, "grid");
    var axis = svg("g", null, "axis");
    // One format for the whole value axis (Q-1802): 5k · 10k · 15k.
    var yt = ticks(yLo, yHi, 3).filter(function (v) {
      var y = Y(v);
      return y >= padT - 1 && y <= padT + eqH + 1;
    });
    var yl = H.fmtAxisValues(yt);
    yt.forEach(function (v, j) {
      var y = Y(v);
      grid.appendChild(svg("line", { x1: padL, x2: W - padR, y1: y, y2: y }));
      axis.appendChild(
        text(padL - 6, y + 3.5, yl[j], { "text-anchor": "end" }),
      );
    });
    if (opts.dd) {
      grid.appendChild(
        svg("line", { x1: padL, x2: W - padR, y1: ddTop, y2: ddTop }),
      );
      axis.appendChild(
        text(padL - 6, ddTop + 3.5, "0%", { "text-anchor": "end" }),
      );
      axis.appendChild(
        text(padL - 6, ddTop + ddH + 3.5, F.drawdown(ddMin, { dp: 0 }).text, {
          "text-anchor": "end",
        }),
      );
      axis.appendChild(
        text(
          W - padR,
          ddTop + 11,
          "drawdown",
          { "text-anchor": "end" },
          "pane-label",
        ),
      );
    }
    var n = W < 420 ? 3 : 4;
    var xt = [];
    for (var i = 0; i < n; i++) xt.push(t0 + (t1 - t0) * (i / (n - 1)));
    // One format for the whole axis (Q-1783): "Aug 2024 · Apr 2025 ·
    // Jan 2026 · Sep 2026", never "Aug 9, 2024 · Apr 24 · Jan 6".
    var xl = H.fmtAxisDates(xt);
    xt.forEach(function (t, j) {
      axis.appendChild(
        text(X(t), Hgt - 4, xl[j], {
          "text-anchor": j === 0 ? "start" : j === n - 1 ? "end" : "middle",
        }),
      );
    });
    root.appendChild(grid);
    root.appendChild(axis);

    // Drawn back to front so the baseline — the heaviest line and the
    // one every delta is against — is on top.
    series
      .slice()
      .reverse()
      .forEach(function (s) {
        var d = "";
        var dd = "";
        s.pts.forEach(function (p, i) {
          var x = X(p.t).toFixed(1);
          d += (i ? "L" : "M") + x + "," + Y(p.equity).toFixed(1);
          dd += (i ? "L" : "M") + x + "," + YD(p.dd).toFixed(1);
        });
        var attrs = { d: d, stroke: s.stroke };
        if (s.dash) attrs["stroke-dasharray"] = s.dash;
        root.appendChild(svg("path", attrs, "ml" + (s.base ? " base" : "")));
        if (opts.dd) {
          var a2 = { d: dd, stroke: s.stroke };
          if (s.dash) a2["stroke-dasharray"] = s.dash;
          root.appendChild(svg("path", a2, "mdd"));
        }
      });
    container.appendChild(root);
  }

  return drawMulti;
})(window.KeelHost);
