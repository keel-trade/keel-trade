/* Generator for the render-cadence card fixtures (Q-1688, Q-1689).
 *
 * The envelopes it writes are the FOUNDER'S OWN SESSION, not invented
 * numbers: the four `Simple Momentum (ROC 20)` runs recorded in
 * RENDER-CADENCE-2026-09-22 §10 (v1 every_bar, v3 buffer 0.05, v2
 * buffer 0.10, v4 buffer 0.20) plus four later synthetic runs on a
 * shorter window for the 8-run cap, and one run of a DIFFERENT
 * strategy for the cross-strategy shape. They are the same numbers the
 * design reference renders (`../keel-artifacts/projects/projects/fable/
 * mcp-strategy-view/review-2026-09-22/src/build.mjs`), so a capture and
 * a test measure one card.
 *
 * The output is CHECKED IN — this script exists so the provenance of
 * every number is readable and a regeneration is mechanical, not so
 * the suite runs node to get its fixtures.
 *
 *   node make_cadence_fixtures.mjs [--out <dir>]
 *   scripts/ci/prettier-pinned.sh --write <the written .json>
 *
 * (the second line is not optional: the checked-in files are
 * prettier-formatted, and the pre-commit hook formats .json)
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const outDir =
  process.argv.indexOf("--out") >= 0
    ? process.argv[process.argv.indexOf("--out") + 1]
    : here;

const NAME = "Simple Momentum (ROC 20)";
const WIN = { start: "2024-08-15", end: "2026-09-22" };
const WIN2 = { start: "2025-01-03", end: "2026-09-22" };
const URL_TS = "https://app.usekeel.io/backtests/btr_9f2a41c8?tab=tearsheet";
const URL_ED = "https://app.usekeel.io/strategies/str_7b31de90/edit";
const d0 = Date.UTC(2024, 7, 15);
const T1 = Date.UTC(2026, 8, 22);
const iso = (t) => new Date(t).toISOString().slice(0, 10);
const frac = (y, m, d, start, end) =>
  (Date.UTC(y, m - 1, d) - start) / (end - start);

/* A plausible curve: start → peak → trough → long chop → end. Peak and
 * trough are exact, so the chart's drawdown agrees with the tiles. */
function curve({
  start = 10000,
  peak,
  dd,
  end,
  t0 = d0,
  t1 = T1,
  pPeak,
  pTr,
  seed,
  n = 120,
}) {
  const trough = peak * (1 + dd / 100);
  const pts = [];
  for (let i = 0; i < n; i++) {
    const p = i / (n - 1);
    let v;
    if (p <= pPeak) {
      const t = p / pPeak;
      v = start + (peak - start) * Math.pow(t, 1.7);
    } else if (p <= pTr) {
      const t = (p - pPeak) / (pTr - pPeak);
      v = peak - (peak - trough) * Math.pow(t, 0.75);
    } else {
      const t = (p - pTr) / (1 - pTr);
      let h =
        0.55 * Math.pow(t, 1.5) +
        0.45 * (0.5 - 0.5 * Math.cos(Math.PI * Math.pow(t, 1.15))) +
        0.06 * Math.sin(t * Math.PI * 3);
      h = Math.max(0, Math.min(1, h));
      v = trough + (end - trough) * h;
    }
    const dist = Math.min(Math.abs(p - pPeak), Math.abs(p - pTr), p, 1 - p);
    const mask = Math.min(1, dist / 0.05);
    const w =
      0.022 * Math.sin(p * 41 + seed) +
      0.013 * Math.sin(p * 97 + seed * 1.7) +
      0.008 * Math.sin(p * 173 + seed * 0.6);
    v = v * (1 + w * mask);
    v = p < pTr ? Math.min(v, peak) : Math.max(v, trough);
    pts.push([iso(t0 + p * (t1 - t0)), Math.round(v * 100) / 100, 0]);
  }
  let mx = -Infinity;
  for (const q of pts) {
    if (q[1] > mx) mx = q[1];
    q[2] = Math.round((q[1] / mx - 1) * 1000) / 10;
  }
  return {
    points: pts,
    start: pts[0][0],
    end: pts[pts.length - 1][0],
    source_points: 767,
  };
}

const SESSION = {
  pPeak: frac(2024, 12, 5, d0, T1),
  pTr: frac(2025, 1, 25, d0, T1),
};
const LATE = { t0: Date.UTC(2025, 0, 3), pPeak: 0.06, pTr: 0.14 };

const RUNS = {
  v1: {
    version: 1,
    label: "v1 · every_bar",
    diff: "—",
    m: {
      sharpe: 0.63,
      total_return_pct: 51.6,
      max_drawdown_pct: -45.6,
      fills: 1761,
      fees_paid: 1217,
      turnover: 270,
      win_rate_pct: 26.0,
      sortino: 0.94,
      calmar: null,
      profit_factor: 1.08,
      carry: -12.9,
    },
    c: { peak: 15900, dd: -45.6, end: 15160, seed: 1, ...SESSION },
    window: WIN,
  },
  v3: {
    version: 3,
    label: "v3 · buffer 0.05",
    diff: "buffer 0.05",
    m: {
      sharpe: 0.67,
      total_return_pct: 60.5,
      max_drawdown_pct: -43.4,
      fills: 1761,
      fees_paid: 1091,
      turnover: 245,
      win_rate_pct: 26.2,
      sortino: 1.0,
      calmar: null,
      profit_factor: 1.1,
      carry: -13.1,
    },
    c: { peak: 16100, dd: -43.4, end: 16050, seed: 2, ...SESSION },
    window: WIN,
  },
  v2: {
    version: 2,
    label: "v2 · buffer 0.10",
    diff: "buffer 0.10",
    m: {
      sharpe: 0.7,
      total_return_pct: 67.0,
      max_drawdown_pct: -42.8,
      fills: 1761,
      fees_paid: 991,
      turnover: 220,
      win_rate_pct: 26.3,
      sortino: 1.06,
      calmar: null,
      profit_factor: 1.12,
      carry: -13.3,
    },
    c: { peak: 16300, dd: -42.8, end: 16700, seed: 3, ...SESSION },
    window: WIN,
  },
  v4: {
    version: 4,
    label: "v4 · buffer 0.20",
    diff: "buffer 0.20",
    m: {
      sharpe: 0.72,
      total_return_pct: 68.7,
      max_drawdown_pct: -40.7,
      fills: 1761,
      fees_paid: 820,
      turnover: 185,
      win_rate_pct: 26.5,
      sortino: 1.1,
      calmar: null,
      profit_factor: 1.13,
      carry: -13.2,
    },
    c: { peak: 16500, dd: -40.7, end: 16870, seed: 4, ...SESSION },
    window: WIN,
  },
  v5: {
    version: 5,
    label: "v5 · buffer 0.10",
    diff: "buffer 0.10 · window",
    m: {
      sharpe: 0.91,
      total_return_pct: 31.2,
      max_drawdown_pct: -18.4,
      fills: 1120,
      fees_paid: 610,
      turnover: 190,
      win_rate_pct: 27.1,
      sortino: 1.31,
      calmar: null,
      profit_factor: 1.15,
    },
    c: { peak: 10600, dd: -18.4, end: 13120, seed: 5, ...LATE },
    window: WIN2,
  },
  v6: {
    version: 6,
    label: "v6 · buffer 0.10",
    diff: "fees 2.0 bps · window",
    m: {
      sharpe: 0.95,
      total_return_pct: 33.8,
      max_drawdown_pct: -18.1,
      fills: 1120,
      fees_paid: 275,
      turnover: 190,
      win_rate_pct: 27.1,
      sortino: 1.36,
      calmar: null,
      profit_factor: 1.17,
    },
    c: { peak: 10650, dd: -18.1, end: 13380, seed: 6, ...LATE },
    window: WIN2,
  },
  // v7 carries NO curve: the 8-run fixture must exercise the
  // "no curve for v7" line and the chart's own cap at once.
  v7: {
    version: 7,
    label: "v7 · buffer 0.30",
    diff: "buffer 0.30 · window",
    m: {
      sharpe: 0.88,
      total_return_pct: 27.9,
      max_drawdown_pct: -17.2,
      fills: 1120,
      fees_paid: 402,
      turnover: 150,
      win_rate_pct: 26.9,
      sortino: 1.27,
      calmar: null,
      profit_factor: 1.14,
    },
    c: null,
    window: WIN2,
  },
  v8: {
    version: 8,
    label: "v8 · buffer 0.40",
    diff: "buffer 0.40 · window",
    m: {
      sharpe: 0.84,
      total_return_pct: 25.1,
      max_drawdown_pct: -16.9,
      fills: 1120,
      fees_paid: 348,
      turnover: 130,
      win_rate_pct: 26.7,
      sortino: 1.21,
      calmar: null,
      profit_factor: 1.12,
    },
    c: { peak: 10500, dd: -16.9, end: 12510, seed: 8, ...LATE },
    window: WIN2,
  },
};

const ADX = {
  version: 1,
  label: "adx_trend_crypto",
  diff: "—",
  m: {
    sharpe: 0.82,
    total_return_pct: 65.4,
    max_drawdown_pct: -39.6,
    fills: 1155,
    fees_paid: 640,
    turnover: 140,
    win_rate_pct: 38.2,
    sortino: 1.21,
    calmar: null,
    profit_factor: 1.31,
  },
  c: { peak: 14200, dd: -39.6, end: 16540, seed: 11, pPeak: 0.34, pTr: 0.5 },
  window: WIN,
};

const curveOf = (r) => (r.c ? curve(r.c) : null);

/* A backtest envelope: the `view` (§2.1) plus the legacy fields the
 * shipped evidence layout reads when the receipt is opened in place. */
function backtestEnv(run, status, extra = {}) {
  const m = run.m;
  const completed = status === "completed";
  const view = {
    kind: "backtest",
    size: "receipt",
    name: NAME,
    version: run.version,
    status,
    window: run.window,
    metrics: completed ? m : {},
    net_of: ["fees", "slippage", "carry"],
    error: null,
    object: "btr_9f2a41c8",
    at: "2026-09-22T15:04:11.201Z",
    seq: 1,
    url_line: "View in Keel: " + URL_TS,
    ...extra.view,
  };
  return {
    view,
    hero_url: URL_TS,
    status,
    strategy_name: NAME,
    sequence_number: run.version,
    period: { start_date: run.window.start, end_date: run.window.end },
    summary_metrics: completed
      ? {
          sharpe: m.sharpe,
          total_return_pct: m.total_return_pct,
          max_drawdown_pct: m.max_drawdown_pct,
          total_trades: m.fills,
          win_rate_pct: m.win_rate_pct,
          turnover: m.turnover,
          sortino_ratio: m.sortino,
          calmar_ratio: m.calmar,
          profit_factor: m.profit_factor,
          funding_attribution: m.carry,
        }
      : {},
    notes: completed
      ? {
          assets: [
            {
              code: "SYMBOL_DELISTED",
              message:
                "2 of 30 assets were delisted inside the window — held to the delisting, then dropped.",
            },
          ],
        }
      : null,
    curve: completed ? curveOf(run) : null,
    ...extra.env,
  };
}

function compareEnv(keys, opts = {}) {
  const runs = keys.map((k) => (k === "adx" ? ADX : RUNS[k]));
  const base = runs[0].m;
  const deltas = runs.map((r, i) =>
    i === 0
      ? null
      : Object.fromEntries(
          Object.keys(base).map((key) => [
            key,
            r.m[key] != null && base[key] != null
              ? Math.round((r.m[key] - base[key]) * 100) / 100
              : null,
          ]),
        ),
  );
  const view = {
    kind: "comparison",
    size: "comparison",
    name: opts.name || NAME,
    window: WIN,
    // The common window, when the runs' windows differ (Q-1802).
    ...(opts.windowsDiffer
      ? { overlap: { start: WIN2.start, end: WIN2.end, severity: "warning" } }
      : {}),
    runs: runs.map((r, i) => ({
      label: opts.cross
        ? i === 0
          ? "simple_momentum"
          : "adx_trend_crypto"
        : opts.bareLabels
          ? "v" + r.version
          : r.label,
      version: r.version,
      status: "completed",
      metrics: r.m,
      curve:
        opts.noCurve && opts.noCurve.indexOf(keys[i]) >= 0 ? null : curveOf(r),
      url: URL_TS,
      diff: r.diff,
    })),
    baseline: 0,
    deltas,
    warnings: opts.warnings || [],
    error: null,
    object: "compare:str_7b31de90",
    at: "2026-09-22T15:11:02.880Z",
    seq: 4,
    url_line: opts.cross ? null : "View in Keel: " + URL_ED,
  };
  return { view, hero_url: opts.cross ? null : URL_ED };
}

const write = (name, obj) => {
  fs.writeFileSync(
    path.join(outDir, name),
    JSON.stringify(obj, null, 1) + "\n",
    "utf8",
  );
  console.log(name, fs.statSync(path.join(outDir, name)).size + " bytes");
};

// ── Backtest receipts, one per state (BUILD §4.1) ────────────────────
write(
  "c_bt_receipt_completed.envelope.json",
  backtestEnv(RUNS.v3, "completed"),
);
write("c_bt_receipt_queued.envelope.json", backtestEnv(RUNS.v1, "queued"));
write("c_bt_receipt_running.envelope.json", backtestEnv(RUNS.v4, "running"));
write(
  "c_bt_receipt_failed.envelope.json",
  backtestEnv(RUNS.v2, "failed", {
    view: {
      error:
        "Universe resolved 0 of 30 assets — no bars in the window.\nThe top-volume screen ran on 2024-08-15 and every candidate was below the minimum notional; widen the window or lower top_n.",
    },
  }),
);
write(
  "c_bt_receipt_cancelled.envelope.json",
  backtestEnv(RUNS.v2, "cancelled"),
);

// ── An error envelope now mounts the card (BUILD §4.1) ───────────────
// The §13.5 shape, verbatim: a quota refusal is RETURNED, not raised.
write("c_bt_error_quota.envelope.json", {
  code: "backtest_quota_exhausted",
  message: "Monthly backtest quota reached (40 of 40) — resets Oct 1.",
  what_was_expected: "A backtest run within the plan's monthly allowance.",
  example: { strategy_id: "str_7b31de90", start: "2024-08-15" },
  suggested_next_action: {
    tool: "keel_account_status",
    args: {},
    reason: "See the plan's remaining allowance.",
  },
});

// ── The superseded pair (BUILD §2.9) ─────────────────────────────────
// Same `object`, two election keys one second apart: the older card
// re-renders as its receipt with `superseded by v5`. The control arm
// below is the same younger key on a DIFFERENT object, which must
// supersede nothing.
write(
  "c_bt_superseded_older.envelope.json",
  backtestEnv(RUNS.v3, "completed", {
    view: { object: "str_7b31de90", at: "2026-09-22T15:04:11.201Z", seq: 1 },
  }),
);
write(
  "c_bt_superseded_younger.envelope.json",
  backtestEnv(RUNS.v5, "completed", {
    view: { object: "str_7b31de90", at: "2026-09-22T15:06:40.004Z", seq: 2 },
  }),
);
write(
  "c_bt_superseded_other_object.envelope.json",
  backtestEnv(RUNS.v5, "completed", {
    view: { object: "str_OTHER0001", at: "2026-09-22T15:06:40.004Z", seq: 2 },
  }),
);

// ── Comparisons (BUILD §4.4) ─────────────────────────────────────────
// Two ids: the founder's own v1 → v2, which changed TWO declaration
// keys — so no single key varies across the set and every label is
// bare (§2.2).
write(
  "compare_2.envelope.json",
  compareEnv(["v1", "v2"], { bareLabels: true }),
);
// Four ids: exactly one declaration key varies, so every run — the
// baseline included — is labelled with its own value.
write("compare_4.envelope.json", compareEnv(["v1", "v3", "v2", "v4"]));
// Eight ids: the cap. Windows differ, one run has no stored curve.
write(
  "compare_8.envelope.json",
  compareEnv(["v1", "v3", "v2", "v4", "v5", "v6", "v7", "v8"], {
    windowsDiffer: true,
    noCurve: ["v7"],
    warnings: [
      "Windows differ by 5 months at the start — v1, v3, v2 and v4 start Aug 15, 2024; v5, v6, v7 and v8 start Jan 3, 2025. 81.6% overlap: return, drawdown and fee totals cover different periods. Re-run with the same start_date and end_date to compare them directly.",
      "Cost profiles differ — v6 uses 2.0 bps fees",
      "No stored curve for v7",
    ],
  }),
);
// Across strategies: labels are strategy names, and there is NO hero
// link because there is no single destination (Q-1686).
write(
  "compare_cross.envelope.json",
  compareEnv(["v2", "adx"], {
    cross: true,
    name: "2 strategies",
    warnings: [
      "Different strategies — structure and universe may differ.",
      "Universes differ — Top 30 vs Top 15 by volume",
    ],
  }),
);
// A comparison the tool refused.
write("compare_error.envelope.json", {
  code: "backtest_not_comparable",
  message: "v7 has not completed (still running when checked).",
  what_was_expected: "Two to eight completed backtest runs.",
  example: { run_ids: ["btr_9f2a41c8", "btr_2d70aa31"] },
  suggested_next_action: {
    tool: "keel_backtest_watch",
    args: { run_id: "btr_5c1108ef" },
    reason: "Wait for the run to finish, then compare.",
  },
});
