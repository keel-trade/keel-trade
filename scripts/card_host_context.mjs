/* The ONE host context every card capture is taken under (Q-1718).
 *
 * Two harnesses screenshot the same cards — `scripts/card_preview.mjs`
 * (the sanctioned per-lane tool) and
 * `tests/fixtures/cards/c_cadence_check.mjs` (the render-cadence guard,
 * which also writes shots). Until this module existed each declared its
 * own `hostContext`, and they disagreed in two ways that both change the
 * pixels:
 *
 *   * `availableDisplayModes` — `card_preview.mjs` declared none, so
 *     `host-adapter.js`'s `hostOffersFullscreen()` was false and the card
 *     never drew its **Expand** button. Measured across all 14 cases of
 *     the real-envelope lane: Expand appears in 13 of them (+1 laid-out
 *     box) and moves the HEIGHT where it wraps — `strategy-dryrun-open`
 *     374 → 390 px at 720 and 470 → 494 px at 390.
 *   * the style variables — `card_preview.mjs` shipped eight,
 *     `c_cadence_check.mjs` five. The three it was missing are the ones
 *     `host-adapter.js`'s TOKEN_MAP reads for `--good` and `--bad`, so
 *     every coloured number in a cadence shot was card.css's fallback
 *     rather than the host's.
 *
 * Card review is done by looking at PNGs, and the cadence work treats the
 * height numbers as a budget. Two harnesses that disagree about the host
 * produce shots that differ for reasons that have nothing to do with the
 * change under review. So: one declaration, one owner, imported by both.
 *
 * Provenance of what is declared here — neither half is invented:
 *
 *   * The variable NAMES are the ones Claude's design guidelines document
 *     (`--color-{background,text,border,ring}-*`), which is the family
 *     `host-adapter.js:118-134`'s TOKEN_MAP reads; the audit row is in
 *     `projects/fable/mcp-strategy-view/02-card-audit.md` §2. The VALUES
 *     are claude.ai's own surface colours. A host that omits any of them
 *     is handled — the adapter simply leaves card.css's fallback in place
 *     — so shipping the full set here captures the card at its most
 *     host-driven, which is the harder case to get right.
 *   * `['inline','fullscreen']` is what the app itself declares through
 *     `appCapabilities.availableDisplayModes` (Q-1632), so a host that
 *     honours the declaration offers both and the Expand button is part
 *     of the card under review.
 *
 * A harness that deliberately varies one of these — `c_card_check.mjs`
 * asserts "Expand only where the host lists fullscreen", so it passes its
 * own `modes` per arm — takes the constant from here and overrides that
 * one field, rather than re-declaring the whole context.
 */

/** What the host says it can offer. The card draws Expand iff this lists it. */
export const DISPLAY_MODES = ["inline", "fullscreen"];

/** The host's style variables, per theme, as claude.ai ships them. */
export const HOST_VARS = {
  dark: {
    "--color-background-primary": "#262522",
    "--color-background-secondary": "#302f2c",
    "--color-text-primary": "#f2f0ec",
    "--color-text-secondary": "#a9a49c",
    "--color-border-primary": "#3d3b37",
    "--color-text-danger": "#f08a7c",
    "--color-text-success": "#6fcf97",
    "--color-text-info": "#8fb8ff",
  },
  light: {
    "--color-background-primary": "#ffffff",
    "--color-background-secondary": "#f5f4f0",
    "--color-text-primary": "#1c1b19",
    "--color-text-secondary": "#6b6862",
    "--color-border-primary": "#e6e3dc",
    "--color-text-danger": "#c0392b",
    "--color-text-success": "#1e8449",
    "--color-text-info": "#2456d6",
  },
};

/** The page background BEHIND the card — the host's ground, not the card's. */
export const HOST_GROUND = { dark: "#262522", light: "#ffffff" };

/**
 * The `hostContext` a harness answers `ui/initialize` with.
 *
 * `modes: null` is the deliberate no-fullscreen case (`c_card_check.mjs`'s
 * `inlineOnly` arm); omit it and the card is captured the way a host that
 * honours the app's declaration renders it.
 */
export function hostContext(opts) {
  const o = opts || {};
  const theme = o.theme === "light" ? "light" : "dark";
  const ctx = {
    theme,
    displayMode: o.displayMode || "inline",
    styles: { variables: HOST_VARS[theme] },
  };
  const modes = o.modes === undefined ? DISPLAY_MODES : o.modes;
  if (modes) ctx.availableDisplayModes = modes;
  return ctx;
}
