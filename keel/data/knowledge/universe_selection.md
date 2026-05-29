## Universe Selection

Every strategy has Globals(...) and Universe(...) declarations above the Pipeline.

**Globals** declares pipeline-wide config (target_timeframe, bar_offset). Components that need these declare them via declaration_refs — no StoreValue needed. Use TargetTimeframeResampler() instead of TimeframeResampler(target_timeframe_slot=...).

**Universe** declares which assets to trade. When the user specifies assets, use `universe_set` to update the Universe declaration. Use `universe_resolve` to resolve criteria into a concrete symbol list.

**Valid market values**: `"perp"` (perpetual futures — this is the Hyperliquid perp market, the default and most common), `"spot"` (spot markets). There is NO `"hl_perp"` market type — when users say "HL perps", "Hyperliquid perps", or "Hyperliquid perpetuals", use `market="perp"`. The platform trades exclusively on Hyperliquid, so `"perp"` already means Hyperliquid perpetual futures.

**Volume filtering**: `top_volume` universes are already filtered to the highest-volume assets on the exchange. Do NOT suggest adding `RollingVolumeUniverseMask` to a `top_volume` universe — these assets already meet liquidity thresholds by definition. Only suggest volume filtering when the user has a `manual` universe with potentially illiquid assets, or when they explicitly ask for volume-based filtering.

**Groups**: For multi-group strategies (e.g., 'long DeFi, short L1'):
1. Define groups in Universe: groups={'defi': ['AAVE', 'UNI', ...], 'l1': ['BTC', 'ETH', ...]}
2. In the pipeline, use GroupAssetFilter(group='defi') in each Parallel branch
3. Follow with WeightConcatenator to merge branches with different assets

**GroupAssetFilter pattern**: Groups come from Universe declaration, not pipeline logic. The group param is a declaration reference resolved at compile time from Universe.groups.

