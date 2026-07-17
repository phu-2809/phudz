"""
analysis.py - VN30 analysis engine
====================================
Consolidates the full pipeline we built and validated step by step:
  - VN30 ticker list           (Reference.index.members)
  - ROE / ROA                  (from VCI statements)
  - net/gross margin, D/E      (bank-aware)
  - industry classification    (Listing.symbols_by_industries)
  - margin quality flag        (sector rule + math check)
  - current P/E, P/B, mkt cap  (KBS price x1000 + VCI overview)
  - multi-year trend metrics   (ROE slope, income CAGR)
  - recommendation engine      (4 categories, banks separate,
                                relative scoring + fixed 60/40 bands,
                                adjustable weights)

Exposes:
  get_vn30_analysis(weights=None) -> pandas DataFrame (one row per stock)

Data source notes (learned the hard way):
  - Fundamentals: source='VCI'  (KBS returns empty balance sheets)
  - Prices:       source='KBS'  (VCI is 403-blocked for history)
  - KBS prices are in '000 VND -> multiply by 1000
  - Requires register_user(api_key=...) once for the 60 req/min tier
"""

import time
import numpy as np
import pandas as pd
from vnstock import Reference, Finance, Company, Quote, Listing

# ---- Confirmed field names ----
NET_INCOME_ID = 'net_profit_loss_after_tax'
EQUITY_ID = 'owners_equity'
TOTAL_ASSETS_ID = 'total_assets'
NET_SALES_ID = 'net_sales'
GROSS_PROFIT_ID = 'gross_profit'
EPS_ID = 'eps_basic_vnd'
LIABILITIES_IDS = ['liabilities', 'total_liabilities']

PRICE_UNIT_MULTIPLIER = 1000
PAUSE = 3
RATE_LIMIT_WAIT = 50
MAX_RETRIES = 3

DEFAULT_WEIGHTS = {'profitability': 0.25, 'valuation': 0.25, 'health': 0.25, 'trend': 0.25}
BUY_CUTOFF = 60
SELL_CUTOFF = 40

INDUSTRY_EN = {
    'Ngân hàng': 'Banks', 'Thực phẩm - Đồ uống': 'Food & Beverage',
    'Bất động sản': 'Real Estate', 'Vận tải - kho bãi': 'Transport & Logistics',
    'Dịch vụ lưu trú, ăn uống, giải trí': 'Hospitality & Leisure',
    'Công nghệ và thông tin': 'Technology & IT', 'Chứng khoán': 'Securities',
    'Tiện ích': 'Utilities', 'Vật liệu xây dựng': 'Construction Materials',
    'Bán buôn': 'Wholesale', 'Bán lẻ': 'Retail',
    'SX Nhựa - Hóa chất': 'Plastics & Chemicals', 'SX Phụ trợ': 'Supporting Manufacturing',
}
DISTORTED_SECTORS = {'Banks', 'Securities', 'Real Estate'}


# ----------------------------------------------------------------------
# Low-level fetch helpers (with rate-limit retry)
# ----------------------------------------------------------------------
def _with_retry(fn, label):
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return fn()
        except SystemExit:
            time.sleep(RATE_LIMIT_WAIT)
        except Exception as e:
            if 'limit' in str(e).lower() or 'RateLimit' in type(e).__name__:
                time.sleep(RATE_LIMIT_WAIT)
            else:
                raise
    raise RuntimeError(f"{label} failed after {MAX_RETRIES} retries")


def _get_val(df, item_id, year):
    row = df[df['item_id'] == item_id]
    if row.empty:
        return None
    return row[year].values[0]


def _get_liabilities(df, year):
    for lid in LIABILITIES_IDS:
        v = _get_val(df, lid, year)
        if v is not None:
            return v
    return None


# ----------------------------------------------------------------------
# Per-ticker data collection
# ----------------------------------------------------------------------
def _fetch_ticker(symbol, is_bank):
    """Return (history_rows, snapshot_dict) for one ticker."""
    finance = Finance(symbol=symbol, source='VCI')
    df_income = _with_retry(lambda: finance.income_statement(period='year'), f"{symbol} income")
    df_balance = _with_retry(lambda: finance.balance_sheet(period='year'), f"{symbol} balance")

    year_cols = [c for c in df_income.columns if c not in ('item', 'item_en', 'item_id')]
    latest_year = max(year_cols)

    # Per-year history (ROE/ROA/margins/DE)
    hist_rows = []
    for year in year_cols:
        ni = _get_val(df_income, NET_INCOME_ID, year)
        eq = _get_val(df_balance, EQUITY_ID, year)
        ta = _get_val(df_balance, TOTAL_ASSETS_ID, year)
        ns = _get_val(df_income, NET_SALES_ID, year)
        gp = _get_val(df_income, GROSS_PROFIT_ID, year)
        liab = _get_liabilities(df_balance, year)

        if is_bank or not ns:
            net_margin = gross_margin = None
        else:
            net_margin = (ni / ns) if ni is not None else None
            gross_margin = (gp / ns) if gp is not None else None

        hist_rows.append({
            'symbol': symbol, 'year': int(year),
            'net_income': ni,
            'ROE': (ni / eq) if (ni is not None and eq) else None,
            'ROA': (ni / ta) if (ni is not None and ta) else None,
            'net_margin': net_margin,
            'debt_to_equity': (liab / eq) if (liab and eq) else None,
        })

    # Current snapshot (price/PE/PB/mktcap)
    price_df = _with_retry(
        lambda: Quote(symbol=symbol, source='KBS').history(interval='1D', count_back=5),
        f"{symbol} price")
    price = price_df['close'].iloc[-1] * PRICE_UNIT_MULTIPLIER

    overview = _with_retry(lambda: Company(symbol=symbol, source='VCI').overview(), f"{symbol} overview")
    shares = overview['issue_share'].iloc[0] if 'issue_share' in overview.columns else None
    mktcap = overview['market_cap'].iloc[0] if 'market_cap' in overview.columns else None

    eps = _get_val(df_income, EPS_ID, latest_year)
    equity_latest = _get_val(df_balance, EQUITY_ID, latest_year)
    pe = (price / eps) if (eps and eps != 0) else None
    bvps = (equity_latest / shares) if (equity_latest and shares) else None
    pb = (price / bvps) if bvps else None

    snapshot = {
        'symbol': symbol, 'current_price': round(price, 0),
        'pe_ratio': round(pe, 2) if pe else None,
        'pb_ratio': round(pb, 2) if pb else None,
        'market_cap': mktcap,
    }
    return hist_rows, snapshot


# ----------------------------------------------------------------------
# Trend + margin-quality helpers
# ----------------------------------------------------------------------
def _roe_slope(g):
    g = g.dropna(subset=['ROE']).sort_values('year')
    if len(g) < 3:
        return np.nan
    yrs = g['year'].values.astype(float)
    return np.polyfit(yrs - yrs.min(), (g['ROE'] * 100).values.astype(float), 1)[0]


def _income_cagr(g):
    g = g.dropna(subset=['net_income']).sort_values('year')
    if len(g) < 3:
        return np.nan
    start, end = g['net_income'].iloc[0], g['net_income'].iloc[-1]
    n = g['year'].iloc[-1] - g['year'].iloc[0]
    if start <= 0 or end <= 0 or n <= 0:
        return np.nan
    return ((end / start) ** (1 / n) - 1) * 100


def _margin_quality(sector, nm, gm):
    if sector in DISTORTED_SECTORS:
        return 'not_comparable_sector'
    if pd.isna(nm) or pd.isna(gm):
        return 'missing'
    if nm > gm + 0.01:
        return 'suspect_net_gt_gross'
    return 'clean'


# ----------------------------------------------------------------------
# Scoring
# ----------------------------------------------------------------------
def _pct_rank(s, higher_is_better=True):
    r = s.rank(pct=True) * 100
    return r if higher_is_better else 100 - r


def _score_group(g):
    g = g.copy()
    g['score_profitability'] = pd.concat([_pct_rank(g['ROE_%']), _pct_rank(g['ROA_%'])], axis=1).mean(axis=1)
    g['score_valuation'] = pd.concat([_pct_rank(g['pe_ratio'], False), _pct_rank(g['pb_ratio'], False)], axis=1).mean(axis=1)
    if not g['is_bank'].iloc[0]:
        margin = g['net_margin_%'].where(g['margin_quality'] == 'clean')
        g['score_health'] = pd.concat([_pct_rank(g['debt_to_equity'], False), _pct_rank(margin)], axis=1).mean(axis=1)
    else:
        g['score_health'] = _pct_rank(g['debt_to_equity'], False)
    g['score_trend'] = pd.concat([_pct_rank(g['roe_trend']), _pct_rank(g['income_cagr'])], axis=1).mean(axis=1)
    return g


def _verdict(s):
    if s >= BUY_CUTOFF:
        return 'BUY'
    if s < SELL_CUTOFF:
        return 'SELL'
    return 'HOLD'


# ----------------------------------------------------------------------
# Main entry point
# ----------------------------------------------------------------------
def build_raw_dataset(progress_callback=None):
    """Fetch + compute everything. Returns (history_df, snapshot_df, meta).
    progress_callback(i, total, symbol) is called per ticker if provided.
    This is the slow part (lots of API calls) - cache it upstream."""
    ref = Reference()
    vn30 = ref.index.members(symbol='VN30')
    tickers = vn30.tolist() if hasattr(vn30, 'tolist') else list(vn30)

    # industry map
    ind_map = Listing().symbols_by_industries()
    ind = ind_map[ind_map['symbol'].isin(tickers)][['symbol', 'industry_name']].copy()
    ind['industry_en'] = ind['industry_name'].map(INDUSTRY_EN)
    bank_set = set(ind[ind['industry_en'] == 'Banks']['symbol'])

    all_hist, all_snap, failures = [], [], []
    for i, t in enumerate(tickers, start=1):
        if progress_callback:
            progress_callback(i, len(tickers), t)
        try:
            hist_rows, snap = _fetch_ticker(t, t in bank_set)
            all_hist.extend(hist_rows)
            all_snap.append(snap)
        except Exception as e:
            failures.append((t, str(e)[:120]))
        time.sleep(PAUSE)

    hist_df = pd.DataFrame(all_hist)
    snap_df = pd.DataFrame(all_snap)
    meta = {'failures': failures, 'fetched_at': pd.Timestamp.now()}
    return hist_df, snap_df, ind, meta


def compute_recommendations(hist_df, snap_df, ind, weights=None):
    """Pure computation on already-fetched data. Fast, re-runnable when
    weights change (so dashboard sliders don't trigger re-fetch)."""
    weights = weights or DEFAULT_WEIGHTS

    # latest year per stock
    latest = hist_df.sort_values('year').groupby('symbol').tail(1).reset_index(drop=True)
    latest['ROE_%'] = (latest['ROE'] * 100).round(2)
    latest['ROA_%'] = (latest['ROA'] * 100).round(2)
    latest['net_margin_%'] = (latest['net_margin'] * 100).round(2)

    # trend
    trend = (hist_df.groupby('symbol')[['year', 'ROE', 'net_income']]
             .apply(lambda g: pd.Series({'roe_trend': _roe_slope(g),
                                         'income_cagr': _income_cagr(g)}))
             .reset_index())

    df = (latest
          .merge(snap_df, on='symbol', how='left')
          .merge(ind, on='symbol', how='left')
          .merge(trend, on='symbol', how='left'))
    df['is_bank'] = df['industry_en'] == 'Banks'
    df['margin_quality'] = df.apply(
        lambda r: _margin_quality(r['industry_en'], r['net_margin'],
                                  r.get('gross_margin', np.nan)), axis=1)

    parts = [_score_group(x) for x in (df[df['is_bank']], df[~df['is_bank']]) if not x.empty]
    scored = pd.concat(parts, ignore_index=True)

    scored['total_score'] = (
        scored['score_profitability'] * weights['profitability'] +
        scored['score_valuation'] * weights['valuation'] +
        scored['score_health'] * weights['health'] +
        scored['score_trend'] * weights['trend']
    ).round(1)
    scored['recommendation'] = scored['total_score'].apply(_verdict)

    return scored.sort_values('total_score', ascending=False).reset_index(drop=True)
