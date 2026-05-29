<!-- keywords: data, loader, price, funding, open interest, PriceDataLoader, FundingDataLoader, OIDataLoader, resample, align, timeframe -->
<!-- pattern: data_loading -->

# Data Sources and Alignment

Every pipeline starts with data loading. Three data sources available, each
requiring different alignment when combined.

## Data Sources

| Loader | Native Timeframe | Output Type | Content |
|--------|-----------------|-------------|---------|
| PriceDataLoader | 15min or 1d | OHLCVDict | OHLCV candles |
| FundingDataLoader | 1h | StreamSeries | Funding rates |
| OIDataLoader | 1h | StreamSeries | Open interest |

## Standard Data Opening

Every pipeline should start with timeframe configuration via `Globals()` declared
above the Pipeline:

```python
Globals(target_timeframe="1d"),
Pipeline([
    PriceDataLoader(),
    Store("ohlcv_1d"),
    ...
])
```

For 15min data with resampling:

```python
Globals(target_timeframe="1d"),
Pipeline([
    PriceDataLoader(timeframe="15min"),
    TargetTimeframeResampler(),
    Store("ohlcv_1d"),
    ...
])
```

`TargetTimeframeResampler()` reads the target timeframe from the Globals declaration
automatically -- no parameters needed.

## Stream Data Resampling

Funding and OI data are 1h resolution. Before mixing with daily price signals,
they MUST be resampled to the target timeframe:

```python
FundingDataLoader(use_cache=True),
TargetSignalResampler(method="mean"),      # Resample to target timeframe (from Globals)
```

**Why TargetSignalResampler**: Resamples 1h → target timeframe from Globals automatically.
Mean is correct for rates (daily rate = avg of 24 hourly). Pre-resampling smoothing
is unnecessary since mean already smooths hourly noise.

AssetAligner is NOT needed in the standard case — Universe selector passes the same
resolved universe to all data loaders, so assets already match. Use
SignalResampleTransform only for multi-timeframe pipelines where the target differs
from Globals.

## Universe Reduction Alignment (Advanced)

When a pipeline component drops assets from the DataFrame (VolumeUniverseReducer,
GroupAssetFilter), secondary data branches must align to the reduced set.
This is rare with Universe selector — it's only needed when something in-pipeline
explicitly reduces assets. Store the reduced OHLCV BEFORE the Parallel:

```python
PriceDataLoader(),
VolumeUniverseReducer(top_n=20),       # Drops assets — triggers alignment need
Store("ohlcv_1d"),                     # Reduced universe stored here
{
    "momentum": [ROC(period=20), ...],
    "carry": [
        FundingDataLoader(use_cache=True),
        TargetSignalResampler(method="mean"),
        AssetAligner(reference_slot="ohlcv_1d"),  # Align to reduced universe
        NegateTransform(),
        ...
    ],
},
ForecastCombiner(weights={"momentum": 0.6, "carry": 0.4}),
```

Without AssetAligner in this case, ForecastCombiner fails with shape mismatch
because the price branch has fewer assets than the funding branch.

## Common Mistakes

- **M-10**: Missing data pipeline entirely — every pipeline needs at least
  PriceDataLoader.
- **M-18**: Parallel branches with different asset counts — if any branch
  drops assets (VolumeUniverseReducer, GroupAssetFilter), all other
  branches must use AssetAligner to match.
- Mixing 1h funding data directly with 1d price signals — NaN propagation
  destroys the combined signal. Always resample and align.
- Forgetting `Store("ohlcv_1d")` — many downstream components read from this
  slot (VolatilityStandardizer, AssetAligner, etc.).
