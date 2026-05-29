## How the Pipeline System Works

A pipeline is a sequence of components that transforms market data into portfolio weights. Every value flowing through the pipeline is a DataFrame with shape (time × assets) — one column per asset, one row per time bar. When you compute RSI, you get RSI values for ALL assets simultaneously. Components transform the CONTENT of the DataFrame; the shape stays the same.

The type system tracks what the numbers mean at each stage:
- **OHLCVDict**: Raw OHLCV price data (dict of per-asset DataFrames)
- **SignalSeries**: Raw indicator output (unbounded, asset-specific units)
- **NormalizedSignal**: Cross-sectionally comparable (z-scored across assets)
- **BinarySignal**: Discrete decisions ({-1, 0, +1} = short/flat/long)
- **ForecastSeries**: Standardized conviction (-20 to +20, avg |value| = 10)
- **WeightSeries**: Portfolio weight fractions (what the backtester needs)

Types must flow through compatible connections. The main progression is:
OHLCVDict → INDICATOR → SignalSeries → SIGNAL_TRANSFORM → NormalizedSignal → FORECAST_MAPPER → ForecastSeries → POSITION_SIZER → WeightSeries

Not every pipeline follows the full chain. There are 4 main paths:

