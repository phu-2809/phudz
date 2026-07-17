# VN30 Investment Analyzer

A dashboard that analyzes Vietnam's 30 largest listed companies (the VN30 index)
and generates Buy / Hold / Sell signals from fundamental financial data.

## What it does
- Pulls live financial data for all 30 VN30 stocks (via the `vnstock` library)
- Computes key ratios: ROE, ROA, net/gross margin, debt-to-equity, P/E, P/B
- Scores each stock across 4 categories — Profitability, Valuation, Health,
  and multi-year Trend — ranking each stock against its peers (banks scored
  separately, since their financials differ structurally)
- Produces a Buy / Hold / Sell recommendation, with adjustable weighting
  (balanced / value-tilted / quality-tilted)

## How to run it locally

You need Python 3.10+ installed

1. Open a terminal in this folder.
2. Install the requirements:
   ```
   pip install -r requirements.txt
   ```
3. Register your free vnstock API key (one time). In a Python prompt:
   ```python
   from vnstock import register_user
   register_user(api_key='XXX')   # get a free key at https://vnstocks.com/login
   ```
4. Start the dashboard:
   ```
   streamlit run app.py
   ```
5. It opens in your browser. The first load fetches live data (~1-2 minutes);
   after that it's cached for 24 hours, so it's fast.

## Files
- `app.py` — the Streamlit dashboard (the visual layer)
- `analysis.py` — the analysis engine (data fetching, ratios, recommendations)
- `requirements.txt` — Python dependencies

## Data source notes
- Fundamentals come from the VCI source; prices from KBS (chosen because each
  is more reliable for that data type on the free tier).
- Free "community" tier limits: 60 requests/minute. The app paces its requests
  and retries on rate limits.

## Important disclaimer
This is an educational/portfolio project, **not financial advice**. The
recommendation model is rule-based and uses historical annual data, which may
be incomplete or lag the market. Always do your own research before investing.

## Known limitations (by design, documented honestly)
- Recommendations are *relative* (ranking VN30 stocks against each other), so
  the model can't signal "the whole market is over/undervalued."
- P/E uses the latest annual EPS, not trailing-twelve-months, so it differs
  slightly from financial websites.
- Most non-bank sectors have only one VN30 member, so "sector-relative" scoring
  for them is effectively against the whole non-bank group.
- Margin ratios are flagged as not-comparable for banks, securities, and real
  estate, whose income statements don't fit the standard margin formula.
"# phudz" 
