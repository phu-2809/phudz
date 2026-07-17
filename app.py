"""
app.py - VN30 Investment Analysis Dashboard
============================================
Run locally with:   streamlit run app.py

Reads live data via analysis.py (fetches fresh, then caches for the
session/day so repeated interactions don't re-hit the API).

Features:
  - Overview: all 30 stocks, scores, Buy/Hold/Sell, filterable
  - Adjustable weights (value / quality / balanced tilts) - re-scores
    instantly WITHOUT re-fetching data
  - Per-stock detail view: ratios, category scores, trend, history
  - Honest disclaimers (educational, not financial advice)
"""

import streamlit as st
import pandas as pd
import analysis as az

st.set_page_config(page_title="VN30 Analyzer", page_icon="📊", layout="wide")

# ---- Light visual identity (restrained; one accent) ----
st.markdown("""
<style>
  .stApp { background-color: #0f1419; }
  h1, h2, h3 { color: #e8eaed; font-family: 'Georgia', serif; }
  .verdict-buy   { color: #2ecc71; font-weight: 700; }
  .verdict-hold  { color: #f0ad4e; font-weight: 700; }
  .verdict-sell  { color: #e74c3c; font-weight: 700; }
  .metric-card { background:#1a212b; border-radius:10px; padding:14px; }
</style>
""", unsafe_allow_html=True)


# ----------------------------------------------------------------------
# Data loading - cached so we fetch at most once per day
# ----------------------------------------------------------------------
@st.cache_data(ttl=60 * 60 * 24, show_spinner=False)  # cache 24h
def load_data():
    """Fetch + compute the raw dataset. Heavy (API calls). Cached."""
    progress = st.progress(0, text="Fetching VN30 data...")
    def cb(i, total, sym):
        progress.progress(i / total, text=f"Fetching {sym} ({i}/{total})...")
    hist_df, snap_df, ind, meta = az.build_raw_dataset(progress_callback=cb)
    progress.empty()
    return hist_df, snap_df, ind, meta


# Re-scoring is cheap, so it's a separate cached step keyed on weights:
@st.cache_data(show_spinner=False)
def score_data(hist_df, snap_df, ind, weights_tuple):
    weights = dict(weights_tuple)
    return az.compute_recommendations(hist_df, snap_df, ind, weights)


# ----------------------------------------------------------------------
# Header
# ----------------------------------------------------------------------
st.title("VN30 Investment Analyzer")
st.caption("Fundamental analysis & Buy / Hold / Sell signals for Vietnam's 30 largest listed companies")

with st.spinner("Loading analysis (first load fetches live data, ~1-2 min)..."):
    hist_df, snap_df, ind, meta = load_data()

if meta['failures']:
    st.warning(f"Some tickers couldn't be fetched: {meta['failures']}")
st.caption(f"Data fetched: {meta['fetched_at'].strftime('%Y-%m-%d %H:%M')}")


# ----------------------------------------------------------------------
# Sidebar: adjustable weights + filters
# ----------------------------------------------------------------------
st.sidebar.header("Scoring weights")
st.sidebar.caption("Adjust the investing philosophy. Re-scores instantly.")

preset = st.sidebar.radio("Preset", ["Balanced", "Value-tilted", "Quality-tilted", "Custom"])
if preset == "Balanced":
    w = {'profitability': 0.25, 'valuation': 0.25, 'health': 0.25, 'trend': 0.25}
elif preset == "Value-tilted":
    w = {'profitability': 0.15, 'valuation': 0.45, 'health': 0.20, 'trend': 0.20}
elif preset == "Quality-tilted":
    w = {'profitability': 0.40, 'valuation': 0.10, 'health': 0.25, 'trend': 0.25}
else:
    p = st.sidebar.slider("Profitability", 0.0, 1.0, 0.25, 0.05)
    v = st.sidebar.slider("Valuation", 0.0, 1.0, 0.25, 0.05)
    h = st.sidebar.slider("Health", 0.0, 1.0, 0.25, 0.05)
    t = st.sidebar.slider("Trend", 0.0, 1.0, 0.25, 0.05)
    tot = (p + v + h + t) or 1
    w = {'profitability': p/tot, 'valuation': v/tot, 'health': h/tot, 'trend': t/tot}
    st.sidebar.caption(f"Normalized to sum to 1.")

scored = score_data(hist_df, snap_df, ind, tuple(sorted(w.items())))

st.sidebar.header("Filters")
sectors = ["All"] + sorted(scored['industry_en'].dropna().unique().tolist())
sector_pick = st.sidebar.selectbox("Sector", sectors)
verdict_pick = st.sidebar.multiselect("Recommendation", ["BUY", "HOLD", "SELL"],
                                      default=["BUY", "HOLD", "SELL"])

view = scored.copy()
if sector_pick != "All":
    view = view[view['industry_en'] == sector_pick]
view = view[view['recommendation'].isin(verdict_pick)]


# ----------------------------------------------------------------------
# Main: summary metrics
# ----------------------------------------------------------------------
c1, c2, c3 = st.columns(3)
c1.metric("BUY", (scored['recommendation'] == 'BUY').sum())
c2.metric("HOLD", (scored['recommendation'] == 'HOLD').sum())
c3.metric("SELL", (scored['recommendation'] == 'SELL').sum())


# ----------------------------------------------------------------------
# Overview table
# ----------------------------------------------------------------------
st.subheader("All stocks")

def color_verdict(val):
    return {'BUY': 'color:#2ecc71;font-weight:700',
            'HOLD': 'color:#f0ad4e;font-weight:700',
            'SELL': 'color:#e74c3c;font-weight:700'}.get(val, '')

show_cols = ['symbol', 'industry_en', 'total_score', 'recommendation',
             'score_profitability', 'score_valuation', 'score_health', 'score_trend',
             'ROE_%', 'pe_ratio', 'pb_ratio']
table = view[show_cols].rename(columns={
    'industry_en': 'Sector', 'total_score': 'Score',
    'recommendation': 'Verdict', 'score_profitability': 'Profit',
    'score_valuation': 'Value', 'score_health': 'Health', 'score_trend': 'Trend',
    'ROE_%': 'ROE%', 'pe_ratio': 'P/E', 'pb_ratio': 'P/B'})

st.dataframe(table.style.map(color_verdict, subset=['Verdict']).format(precision=1),
             use_container_width=True, height=520, hide_index=True)


# ----------------------------------------------------------------------
# Per-stock detail
# ----------------------------------------------------------------------
st.subheader("Stock detail")
pick = st.selectbox("Select a stock", scored['symbol'].tolist())
row = scored[scored['symbol'] == pick].iloc[0]

d1, d2, d3, d4 = st.columns(4)
d1.metric("Verdict", row['recommendation'])
d2.metric("Total score", f"{row['total_score']:.1f}")
d3.metric("P/E", f"{row['pe_ratio']:.1f}" if pd.notna(row['pe_ratio']) else "n/a")
d4.metric("ROE", f"{row['ROE_%']:.1f}%")

st.markdown("**Category scores** (0-100, ranked vs peers)")
cat = pd.DataFrame({
    'Category': ['Profitability', 'Valuation', 'Health', 'Trend'],
    'Score': [row['score_profitability'], row['score_valuation'],
              row['score_health'], row['score_trend']]
})
st.bar_chart(cat.set_index('Category'), height=240)

# ROE history for this stock
hist_one = hist_df[hist_df['symbol'] == pick].sort_values('year')
if not hist_one.empty:
    st.markdown("**ROE history**")
    roe_series = hist_one.set_index('year')['ROE'] * 100
    st.line_chart(roe_series, height=240)

st.caption(f"Trend: ROE slope {row.get('roe_trend', float('nan')):.2f} pts/yr · "
           f"Income CAGR {row.get('income_cagr', float('nan')):.1f}%")


# ----------------------------------------------------------------------
# Disclaimer
# ----------------------------------------------------------------------
st.divider()
st.caption(
    "**Disclaimer:** This tool is for educational and research purposes only and is "
    "not financial advice. Recommendations are generated by a rule-based model from "
    "historical fundamentals and may be incomplete or inaccurate. Data via vnstock "
    "(community tier). Always do your own research before investing."
)
