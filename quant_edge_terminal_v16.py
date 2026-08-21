import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
import plotly.graph_objects as go
from datetime import datetime
import os

try:
    from streamlit_autorefresh import st_autorefresh
    AUTOREFRESH_AVAILABLE = True
except ImportError:
    AUTOREFRESH_AVAILABLE = False

st.set_page_config(
    page_title="QUANT-EDGE Flagship Trading Terminal v16",
    page_icon="\U0001F451",
    layout="wide",
    initial_sidebar_state="expanded"
)

# -----------------------------------------------------------------------------
# 1. PREMIUM INSTI-STYLING
# -----------------------------------------------------------------------------
st.markdown("""
<style>
    .stApp { background-color: #090a10; color: #e2e4ef; }
    h1, h2, h3, h4 { color: #ffffff !important; font-family: 'Inter', sans-serif; }
    .main-header {
        font-size: 30px; font-weight: 800; letter-spacing: -0.5px;
        background: linear-gradient(135deg, #00ffcc 0%, #0088ff 100%);
        -webkit-background-clip: text; -webkit-text-fill-color: transparent;
        margin-bottom: 2px;
    }
    .sub-caption { font-size: 13px; color: #8b92b6; margin-bottom: 14px; text-transform: uppercase; letter-spacing: 0.5px; }
    .metric-card {
        background-color: #101220; padding: 15px; border-radius: 6px;
        border: 1px solid #1e2238; box-shadow: 0 4px 12px rgba(0,0,0,0.3); margin-bottom: 12px;
    }
    .metric-header { font-size: 10px; color: #7d84a6; text-transform: uppercase; letter-spacing: 1px; }
    .metric-val { font-size: 22px; font-weight: 700; color: #ffffff; margin-top: 3px; }
    .metric-change { font-size: 12px; font-weight: bold; margin-top: 2px; }
    .change-up { color: #00ffaa; }
    .change-down { color: #ff3366; }
    .regime-banner {
        padding: 10px 16px; border-radius: 6px; font-weight: 700; font-size: 14px;
        margin-bottom: 14px; letter-spacing: 0.5px; text-align: center;
    }
    .regime-bull { background: rgba(0,255,170,0.12); color: #00ffaa; border: 1px solid #00ffaa; }
    .regime-bear { background: rgba(255,51,102,0.12); color: #ff3366; border: 1px solid #ff3366; }
    .regime-choppy { background: rgba(255,170,0,0.12); color: #ffaa00; border: 1px solid #ffaa00; }
    .score-badge {
        display: inline-block; padding: 2px 8px; border-radius: 4px; font-weight: 700; font-size: 12px;
    }
</style>
""", unsafe_allow_html=True)

# -----------------------------------------------------------------------------
# 2. FULL NSE F&O UNIVERSE & SECTOR MAPS
# -----------------------------------------------------------------------------
FO_UNIVERSE = [
    "360ONE.NS", "AARTIIND.NS", "ABB.NS", "ABBOTINDIA.NS", "ABCAPITAL.NS", "ABFRL.NS", "ACC.NS", "ADANIENSOL.NS",
    "ADANIENT.NS", "ADANIGREEN.NS", "ADANIPORTS.NS", "ADANIPOWER.NS", "ALKEM.NS", "AMBER.NS", "AMBUJACEM.NS",
    "ANGELONE.NS", "APOLLOHOSP.NS", "APOLLOTYRE.NS", "ASHOKLEY.NS", "ASIANPAINT.NS", "ASTRAL.NS", "ATUL.NS",
    "AUBANK.NS", "AUROPHARMA.NS", "AXISBANK.NS", "BAJAJ-AUTO.NS", "BAJAJFINSV.NS", "BAJAJHLDNG.NS", "BAJFINANCE.NS",
    "BALKRISIND.NS", "BALRAMCHIN.NS", "BANDHANBNK.NS", "BANKBARODA.NS", "BANKINDIA.NS", "BATAINDIA.NS", "BEL.NS",
    "BERGEPAINT.NS", "BHARATFORG.NS", "BHARTIARTL.NS", "BHEL.NS", "BIOCON.NS", "BLUESTARCO.NS", "BOSCHLTD.NS",
    "BPCL.NS", "BRITANNIA.NS", "BSE.NS", "BSOFT.NS", "CANBK.NS", "CANFINHOME.NS", "CASTROLIND.NS", "CDSL.NS",
    "CHAMBLFERT.NS", "CHOLAFIN.NS", "CIPLA.NS", "COALINDIA.NS", "COCHINSHIP.NS", "COFORGE.NS", "COLPAL.NS",
    "CONCOR.NS", "COROMANDEL.NS", "CROMPTON.NS", "CUB.NS", "CUMMINSIND.NS", "DABUR.NS", "DALBHARAT.NS", "DEEPAKNTR.NS",
    "DELHIVERY.NS", "DIVISLAB.NS", "DIXON.NS", "DLF.NS", "DMART.NS", "DRREDDY.NS", "EICHERMOT.NS", "ESCORTS.NS",
    "EXIDEIND.NS", "FEDERALBNK.NS", "FORCEMOT.NS", "GAIL.NS", "GLAND.NS", "GLENMARK.NS", "GMRINFRA.NS", "GNFC.NS",
    "GODFRYPHLP.NS", "GODREJCP.NS", "GODREJPROP.NS", "GRANULES.NS", "GRASIM.NS", "GUJGASLTD.NS", "HAL.NS",
    "HAVELLS.NS", "HCLTECH.NS", "HDFCBANK.NS", "HDFCLIFE.NS", "HEROMOTOCO.NS", "HINDALCO.NS", "HINDCOPPER.NS",
    "HINDPETRO.NS", "HINDUNILVR.NS", "HONAUT.NS", "HYUNDAI.NS", "ICICIBANK.NS", "ICICIGI.NS", "ICICIPRULI.NS",
    "IDEA.NS", "IDFCFIRSTB.NS", "IEX.NS", "IGL.NS", "INDHOTEL.NS", "INDIACEM.NS", "INDIAMART.NS", "INDIGO.NS",
    "INDUSINDBK.NS", "INDUSTOWER.NS", "INFY.NS", "IOC.NS", "IPCALAB.NS", "IRCTC.NS", "ITC.NS", "JINDALSTEL.NS",
    "JIOFIN.NS", "JKCEMENT.NS", "JSWENERGY.NS", "JSWSTEEL.NS", "JUBLFOOD.NS", "KFINTECH.NS", "KOTAKBANK.NS",
    "L&TFH.NS", "LALPATHLAB.NS", "LICHSGFIN.NS", "LICI.NS", "LT.NS", "LTIM.NS", "LTTS.NS", "LUPIN.NS", "M&M.NS",
    "M&MFIN.NS", "MANAPPURAM.NS", "MARUTI.NS", "MCDOWELL-N.NS", "MCX.NS", "METROPOLIS.NS", "MFSL.NS", "MGL.NS",
    "MOTHERSON.NS", "MOTILALOFS.NS", "MPHASIS.NS", "MRF.NS", "MRPL.NS", "MUTHOOTFIN.NS", "NAM-INDIA.NS",
    "NATIONALUM.NS", "NAVINFLUOR.NS", "NBCC.NS", "NESTLEIND.NS", "NHPC.NS", "NMDC.NS", "NTPC.NS", "NUVAMA.NS",
    "OBEROIRLTY.NS", "OFSS.NS", "ONGC.NS", "PAGEIND.NS", "PEL.NS", "PERSISTENT.NS", "PETRONET.NS", "PFC.NS",
    "PGEL.NS", "PHOENIXLTD.NS", "PIDILITIND.NS", "PIIND.NS", "PNB.NS", "POLYCAB.NS", "POWERGRID.NS", "POWERINDIA.NS",
    "PREMIERENE.NS", "PVRINOX.NS", "RAMCOCEM.NS", "RBLBANK.NS", "REC.NS", "RELIANCE.NS", "SAIL.NS", "SAMMAANCAP.NS",
    "SBICARD.NS", "SBILIFE.NS", "SBIN.NS", "SHREECEM.NS", "SHRIRAMFIN.NS", "SIEMENS.NS", "SJVN.NS", "SOLARINDS.NS",
    "SRF.NS", "SUNPHARMA.NS", "SUNTV.NS", "SUZLON.NS", "SWIGGY.NS", "SYNGENE.NS", "TATACOMM.NS", "TATACONSUM.NS",
    "TATAELXSI.NS", "TATAMOTORS.NS", "TATAPOWER.NS", "TATASTEEL.NS", "TCS.NS", "TECHM.NS", "TITAN.NS", "TORNTPHARM.NS",
    "TORNTPOWER.NS", "TRENT.NS", "TVSMOTOR.NS", "UBL.NS", "ULTRACEMCO.NS", "UPL.NS", "VBL.NS", "VEDL.NS",
    "VOLTAS.NS", "WAAREEENER.NS", "WIPRO.NS", "ZEEL.NS", "ZYDUSLIFE.NS"
]

SECTOR_MAP = {
    "\U0001F3E6 Banking (CNXBANK)": "BANKBARODA.NS,FEDERALBNK.NS,HDFCBANK.NS,ICICIBANK.NS,KOTAKBANK.NS,SBIN.NS,AXISBANK.NS,AUBANK.NS",
    "\U0001F4BB IT Sector (CNXIT)": "COFORGE.NS,HCLTECH.NS,INFY.NS,LTIM.NS,LTTS.NS,MPHASIS.NS,PERSISTENT.NS,TCS.NS,TECHM.NS,WIPRO.NS",
    "\U0001F697 Auto Sector (CNXAUTO)": "ASHOKLEY.NS,BAJAJ-AUTO.NS,EICHERMOT.NS,HEROMOTOCO.NS,M&M.NS,MARUTI.NS,TATAMOTORS.NS,TVSMOTOR.NS",
    "\u26A1 Power & Energy": "ADANIPOWER.NS,BPCL.NS,COALINDIA.NS,JSWENERGY.NS,NTPC.NS,ONGC.NS,POWERGRID.NS,TATAPOWER.NS",
    "\U0001F3D7\uFE0F Infra & Metal": "ACC.NS,AMBUJACEM.NS,GRASIM.NS,JINDALSTEL.NS,JSWSTEEL.NS,LT.NS,TATASTEEL.NS,ULTRACEMCO.NS",
    "\U0001F48A Pharma Sector": "ALKEM.NS,APOLLOHOSP.NS,CIPLA.NS,DIVISLAB.NS,DRREDDY.NS,IPCALAB.NS,LUPIN.NS,SUNPHARMA.NS,ZYDUSLIFE.NS",
    "\U0001F6D2 FMCG & Retail": "ABFRL.NS,BRITANNIA.NS,COLPAL.NS,DABUR.NS,DMART.NS,GODREJCP.NS,HINDUNILVR.NS,ITC.NS,NESTLEIND.NS,TRENT.NS"
}

SIGNALS_FILE = "nse_signals_database.csv"
PREMARKET_FILE = "todays_premarket_watchlist.csv"

# -----------------------------------------------------------------------------
# 3. DATA FETCHING (cached)
# -----------------------------------------------------------------------------
@st.cache_data(ttl=900)
def fetch_market_indices():
    indices = {"NIFTY 50": "^NSEI", "BANK NIFTY": "^NSEBANK", "SENSEX": "^BSESN"}
    res = {}
    for name, sym in indices.items():
        try:
            df = yf.download(sym, period="2d", interval="1d", progress=False, auto_adjust=True)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            if not df.empty and len(df) >= 2:
                ltp = float(df['Close'].iloc[-1])
                prev_close = float(df['Close'].iloc[-2])
                change_val = ltp - prev_close
                change_pct = (change_val / prev_close) * 100
                res[name] = {"LTP": ltp, "Change_Val": change_val, "Change_Pct": change_pct}
            elif not df.empty:
                res[name] = {"LTP": float(df['Close'].iloc[-1]), "Change_Val": 0.0, "Change_Pct": 0.0}
        except Exception:
            res[name] = {"LTP": 0.0, "Change_Val": 0.0, "Change_Pct": 0.0}
    return res

@st.cache_data(ttl=1800)
def fetch_stock_data(ticker, period="3y", interval="1d"):
    try:
        data = yf.download(ticker, period=period, interval=interval, progress=False, auto_adjust=True)
        if isinstance(data.columns, pd.MultiIndex):
            data.columns = data.columns.get_level_values(0)
        return data.dropna(subset=['Close'])
    except Exception:
        return pd.DataFrame()

@st.cache_data(ttl=1800)
def fetch_bulk_stock_data(tickers, period="1y", interval="1d"):
    try:
        df_all = yf.download(tickers, period=period, interval=interval, group_by="ticker", progress=False, auto_adjust=True)
        return df_all
    except Exception:
        return pd.DataFrame()

@st.cache_data(ttl=1800)
def fetch_nifty_series(period="2y", interval="1d"):
    return fetch_stock_data("^NSEI", period=period, interval=interval)

def get_symbol_df(bulk_df, ticker, fallback_period, fallback_interval, min_len=50):
    """Pull a symbol out of a bulk download, falling back to a single fetch if missing/short."""
    df = pd.DataFrame()
    if not bulk_df.empty:
        try:
            if isinstance(bulk_df.columns, pd.MultiIndex):
                if ticker in bulk_df:
                    df = bulk_df[ticker].dropna(how='all')
            else:
                df = bulk_df.dropna(how='all')
        except Exception:
            pass
    if df.empty or len(df) < min_len:
        df = fetch_stock_data(ticker, period=fallback_period, interval=fallback_interval)
    return df

# -----------------------------------------------------------------------------
# 4. INDICATOR ENGINE
# -----------------------------------------------------------------------------
def add_atr(df, period=14):
    if df.empty or len(df) < period + 1:
        df['ATR14'] = np.nan
        return df
    high_low = df['High'] - df['Low']
    high_close = (df['High'] - df['Close'].shift(1)).abs()
    low_close = (df['Low'] - df['Close'].shift(1)).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df['ATR14'] = tr.ewm(span=period, adjust=False).mean()
    return df

def compute_indicators(df, is_intraday=False):
    """EMAs, VWAP, Pivots, ATR, rolling volume. is_intraday must be passed explicitly
    from the workspace selection -- never re-derived from the interval string."""
    if df.empty:
        return df
    df = df.copy()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    if len(df) < 50:
        return df

    df['EMA_20'] = df['Close'].ewm(span=20, adjust=False).mean()
    df['EMA_50'] = df['Close'].ewm(span=50, adjust=False).mean()
    df = add_atr(df)

    if is_intraday:
        df['Date_Group'] = df.index.date
        typical_p_v = ((df['High'] + df['Low'] + df['Close']) / 3.0) * df['Volume']
        df['TP_Vol'] = typical_p_v
        df['Cum_TP_Vol'] = df.groupby('Date_Group')['TP_Vol'].transform(pd.Series.cumsum)
        df['Cum_Vol'] = df.groupby('Date_Group')['Volume'].transform(pd.Series.cumsum)
        df['VWAP'] = df['Cum_TP_Vol'] / df['Cum_Vol']
        df.drop(columns=['Date_Group', 'TP_Vol', 'Cum_TP_Vol', 'Cum_Vol'], inplace=True, errors='ignore')

        df['Date_Group'] = df.index.date
        daily_agg = df.groupby('Date_Group').agg(
            Daily_High=('High', 'max'), Daily_Low=('Low', 'min'), Daily_Close=('Close', 'last')
        ).shift(1)
        df = df.join(daily_agg, on='Date_Group')
        df['Pivot'] = (df['Daily_High'] + df['Daily_Low'] + df['Daily_Close']) / 3.0
        df['R1'] = (2.0 * df['Pivot']) - df['Daily_Low']
        df['S1'] = (2.0 * df['Pivot']) - df['Daily_High']
        df.drop(columns=['Date_Group', 'Daily_High', 'Daily_Low', 'Daily_Close'], inplace=True, errors='ignore')
    else:
        df['VWAP'] = np.nan
        df['Y_High'] = df['High'].shift(1)
        df['Y_Low'] = df['Low'].shift(1)
        df['Y_Close'] = df['Close'].shift(1)
        df['Pivot'] = (df['Y_High'] + df['Y_Low'] + df['Y_Close']) / 3.0
        df['R1'] = (2.0 * df['Pivot']) - df['Y_Low']
        df['S1'] = (2.0 * df['Pivot']) - df['Y_High']
        df.drop(columns=['Y_High', 'Y_Low', 'Y_Close'], inplace=True, errors='ignore')

    df['Vol_MA_20'] = df['Volume'].rolling(20).mean()
    df['Inst_Vol_Spike'] = df['Volume'] > (df['Vol_MA_20'] * 1.5)
    return df

def get_market_regime(nifty_df):
    """BULL / BEAR / CHOPPY based on Nifty's own EMA20 vs EMA50 trend."""
    if nifty_df.empty or len(nifty_df) < 55:
        return "UNKNOWN"
    nd = compute_indicators(nifty_df, is_intraday=False)
    if nd.empty or 'EMA_20' not in nd.columns:
        return "UNKNOWN"
    last = nd.iloc[-1]
    if pd.isna(last['EMA_20']) or pd.isna(last['EMA_50']):
        return "UNKNOWN"
    if last['Close'] > last['EMA_20'] > last['EMA_50']:
        return "BULL"
    elif last['Close'] < last['EMA_20'] < last['EMA_50']:
        return "BEAR"
    return "CHOPPY"

def get_nifty_rs_series(nifty_df, window=21):
    if nifty_df.empty or len(nifty_df) < window + 1:
        return None
    return nifty_df['Close'].pct_change(window) * 100.0

def compute_probability_score(df, nifty_rs_series=None, window=21):
    """A 0-100 weighted composite of trend alignment, relative strength vs Nifty,
    volume confirmation, and ATR-normalized overextension. This is a heuristic
    ranking score, not a statistical probability of profit."""
    df = df.copy()
    if 'EMA_20' not in df.columns or 'ATR14' not in df.columns:
        df['Probability_Score'] = np.nan
        return df

    trend_up = (df['Close'] > df['EMA_20']) & (df['EMA_20'] > df['EMA_50'])
    trend_down = (df['Close'] < df['EMA_20']) & (df['EMA_20'] < df['EMA_50'])
    trend_score = np.where(trend_up | trend_down, 30, 10)

    vol_ratio = (df['Volume'] / df['Vol_MA_20']).clip(upper=3.0).fillna(0)
    volume_score = (vol_ratio / 3.0 * 25).clip(0, 25)

    atr_safe = df['ATR14'].replace(0, np.nan)
    dist_atr = ((df['Close'] - df['EMA_20']).abs() / atr_safe).clip(upper=3.0).fillna(3.0)
    extension_score = ((3.0 - dist_atr) / 3.0 * 25).clip(0, 25)

    if nifty_rs_series is not None:
        stock_ret = df['Close'].pct_change(window) * 100.0
        rs = (stock_ret - nifty_rs_series.reindex(df.index).ffill()).clip(-10, 10).fillna(0)
        rs_score = ((rs + 10) / 20 * 20).clip(0, 20)
        df['Probability_Score'] = (trend_score + volume_score + extension_score * 0.8 + rs_score).clip(0, 100)
    else:
        df['Probability_Score'] = (trend_score * 1.1 + volume_score * 1.2 + extension_score).clip(0, 100)

    return df

def position_size(capital, risk_pct, entry, stop):
    """Institutional-style fixed-fractional sizing: risk a fixed % of capital per trade."""
    risk_amount = capital * (risk_pct / 100.0)
    per_share_risk = abs(entry - stop)
    if per_share_risk <= 0 or capital <= 0:
        return 0, 0.0
    qty = int(risk_amount // per_share_risk)
    capital_deployed = qty * entry
    return qty, capital_deployed

def compute_trend_consistency(df, window=5):
    """% of the last `window` sessions that moved in the same (dominant) direction.
    100% = every recent day pushed the same way -- a genuine one-way trend, not a chop."""
    if df.empty or len(df) < window + 1:
        return 0.0
    diffs = df['Close'].diff().iloc[-window:]
    up_days = int((diffs > 0).sum())
    down_days = int((diffs < 0).sum())
    dominant = max(up_days, down_days)
    return (dominant / window) * 100.0

# -----------------------------------------------------------------------------
# 4B. FYERS LIVE PRE-OPEN CONNECTION
# Credentials come from Streamlit secrets (never hardcoded): FYERS_APP_ID,
# FYERS_SECRET_KEY, FYERS_REDIRECT_URI. Token is session-only and expires daily,
# matching Fyers' own daily re-login requirement.
# -----------------------------------------------------------------------------
try:
    from fyers_apiv3 import fyersModel
    FYERS_SDK_AVAILABLE = True
except ImportError:
    FYERS_SDK_AVAILABLE = False

def get_fyers_credentials():
    try:
        return st.secrets["FYERS_APP_ID"], st.secrets["FYERS_SECRET_KEY"], st.secrets["FYERS_REDIRECT_URI"]
    except Exception:
        return None, None, None

def get_fyers_session():
    app_id, secret_key, redirect_uri = get_fyers_credentials()
    if not app_id or not FYERS_SDK_AVAILABLE:
        return None
    return fyersModel.SessionModel(
        client_id=app_id, secret_key=secret_key, redirect_uri=redirect_uri,
        response_type="code", grant_type="authorization_code"
    )

def get_fyers_client():
    app_id, _, _ = get_fyers_credentials()
    token = st.session_state.get('fyers_access_token')
    if not app_id or not token or not FYERS_SDK_AVAILABLE:
        return None
    return fyersModel.FyersModel(client_id=app_id, is_async=False, token=token, log_path="")

def to_fyers_symbol(yf_ticker):
    return "NSE:" + yf_ticker.replace(".NS", "") + "-EQ"

def fetch_fyers_quotes_bulk(fyers_client, yf_tickers, batch_size=50):
    """Fyers caps quote requests per call, so this chunks the F&O list into batches."""
    all_quotes = {}
    for i in range(0, len(yf_tickers), batch_size):
        batch = yf_tickers[i:i + batch_size]
        fy_symbols = [to_fyers_symbol(t) for t in batch]
        try:
            resp = fyers_client.quotes({"symbols": ",".join(fy_symbols)})
            if resp.get("s") == "ok":
                for item in resp.get("d", []):
                    all_quotes[item.get("n")] = item.get("v", {})
        except Exception:
            continue
    return all_quotes

# -----------------------------------------------------------------------------
# 5. SIGNAL GENERATION -- NO LOOK-AHEAD
# Entry_Trigger is a breakout-confirmation stop level beyond the signal candle's
# extreme. It must be crossed on a LATER candle to fill -- it is never the same
# candle's close. This mirrors how a real buy-stop / sell-stop order works.
# -----------------------------------------------------------------------------
def generate_signals_for_asset(df, lookback=10, is_intraday=False, rr=2.0, use_orb=False):
    if df.empty or len(df) < max(50, lookback) + 5:
        for c in ['Bullish_Sweep', 'Bearish_Sweep', 'Bullish_ORB', 'Bearish_ORB']:
            df[c] = False
        for c in ['Entry_Trigger', 'Stop_Loss', 'Take_Profit', 'Probability_Score']:
            df[c] = np.nan
        return df

    df = compute_indicators(df, is_intraday=is_intraday)
    if df.empty or 'EMA_50' not in df.columns:
        return df

    df['Prev_Low_L'] = df['Low'].shift(1).rolling(lookback).min()
    df['Prev_High_H'] = df['High'].shift(1).rolling(lookback).max()

    df['Bullish_Sweep'] = (df['Close'] > df['EMA_50']) & (df['Low'] < df['Prev_Low_L']) & \
                           (df['Close'] > df['Prev_Low_L']) & (df['Volume'] > df['Vol_MA_20'])
    df['Bearish_Sweep'] = (df['Close'] < df['EMA_50']) & (df['High'] > df['Prev_High_H']) & \
                           (df['Close'] < df['Prev_High_H']) & (df['Volume'] > df['Vol_MA_20'])

    df['Bullish_ORB'] = False
    df['Bearish_ORB'] = False
    if use_orb:
        df['Date_Group'] = df.index.date
        df['ORB_High'] = df.groupby('Date_Group')['High'].transform('first')
        df['ORB_Low'] = df.groupby('Date_Group')['Low'].transform('first')
        df['Bullish_ORB'] = (df.groupby('Date_Group').cumcount() > 0) & \
                             (df['Close'] > df['ORB_High']) & (df['Close'].shift(1) <= df['ORB_High'])
        df['Bearish_ORB'] = (df.groupby('Date_Group').cumcount() > 0) & \
                             (df['Close'] < df['ORB_Low']) & (df['Close'].shift(1) >= df['ORB_Low'])
        df.drop(columns=['Date_Group', 'ORB_High', 'ORB_Low'], errors='ignore', inplace=True)

    buy_mask = df['Bullish_Sweep'] | df['Bullish_ORB']
    sell_mask = df['Bearish_Sweep'] | df['Bearish_ORB']

    df['Entry_Trigger'] = np.nan
    df['Stop_Loss'] = np.nan
    df['Take_Profit'] = np.nan

    df.loc[buy_mask, 'Entry_Trigger'] = df.loc[buy_mask, 'High'] * 1.0015
    df.loc[buy_mask, 'Stop_Loss'] = df.loc[buy_mask, 'Low'] * 0.995
    risk_buy = df.loc[buy_mask, 'Entry_Trigger'] - df.loc[buy_mask, 'Stop_Loss']
    df.loc[buy_mask, 'Take_Profit'] = df.loc[buy_mask, 'Entry_Trigger'] + risk_buy * rr

    df.loc[sell_mask, 'Entry_Trigger'] = df.loc[sell_mask, 'Low'] * 0.9985
    df.loc[sell_mask, 'Stop_Loss'] = df.loc[sell_mask, 'High'] * 1.005
    risk_sell = df.loc[sell_mask, 'Stop_Loss'] - df.loc[sell_mask, 'Entry_Trigger']
    df.loc[sell_mask, 'Take_Profit'] = df.loc[sell_mask, 'Entry_Trigger'] - risk_sell * rr

    df.drop(columns=['Prev_Low_L', 'Prev_High_H'], errors='ignore', inplace=True)
    return df

# -----------------------------------------------------------------------------
# 6. BACKTEST ENGINE -- pending-order state machine (no same-bar fills)
# -----------------------------------------------------------------------------
def run_backtest_engine(df, is_intraday=False, use_orb=False, rr=2.0, slippage=0.05, commission=0.03, expiry_bars=5):
    required = ['Bullish_Sweep', 'Bearish_Sweep', 'Entry_Trigger', 'Stop_Loss', 'Take_Profit']
    if df.empty or not all(c in df.columns for c in required):
        return pd.DataFrame()

    trades = []
    state = 'FLAT'
    pos_type = None
    trigger = sl = tp = None
    bars_waited = 0
    entry_p = None
    entry_t = None

    def log_exit(exit_p, idx, result):
        p_pct = ((exit_p - entry_p) / entry_p - commission / 100.0) if pos_type == 'Long' \
            else ((entry_p - exit_p) / entry_p - commission / 100.0)
        trades.append({
            'Type': pos_type,
            'EntryDate': entry_t.strftime('%Y-%m-%d %H:%M') if hasattr(entry_t, 'strftime') else str(entry_t),
            'ExitDate': idx.strftime('%Y-%m-%d %H:%M') if hasattr(idx, 'strftime') else str(idx),
            'Entry': entry_p, 'Exit': exit_p, 'Profit_Pct': p_pct, 'Result': result
        })

    for idx, row in df.iterrows():
        buy_sig = row['Bullish_ORB'] if (is_intraday and use_orb) else row['Bullish_Sweep']
        sell_sig = row['Bearish_ORB'] if (is_intraday and use_orb) else row['Bearish_Sweep']

        if state == 'FLAT':
            if buy_sig and not pd.isna(row['Entry_Trigger']):
                state, pos_type = 'PENDING', 'Long'
                trigger, sl, tp, bars_waited = row['Entry_Trigger'], row['Stop_Loss'], row['Take_Profit'], 0
            elif sell_sig and not pd.isna(row['Entry_Trigger']):
                state, pos_type = 'PENDING', 'Short'
                trigger, sl, tp, bars_waited = row['Entry_Trigger'], row['Stop_Loss'], row['Take_Profit'], 0
            continue

        if state == 'PENDING':
            bars_waited += 1
            filled = False
            if pos_type == 'Long' and row['High'] >= trigger:
                entry_p, entry_t, filled = trigger * (1 + slippage / 100.0), idx, True
            elif pos_type == 'Short' and row['Low'] <= trigger:
                entry_p, entry_t, filled = trigger * (1 - slippage / 100.0), idx, True

            if filled:
                state = 'IN_POSITION'
                if pos_type == 'Long':
                    if row['Low'] <= sl:
                        log_exit(sl * (1 - slippage / 100.0), idx, 'SL'); state = 'FLAT'
                    elif row['High'] >= tp:
                        log_exit(tp * (1 - slippage / 100.0), idx, 'TP'); state = 'FLAT'
                else:
                    if row['High'] >= sl:
                        log_exit(sl * (1 + slippage / 100.0), idx, 'SL'); state = 'FLAT'
                    elif row['Low'] <= tp:
                        log_exit(tp * (1 + slippage / 100.0), idx, 'TP'); state = 'FLAT'
                continue
            elif bars_waited >= expiry_bars:
                state = 'FLAT'  # order expired unfilled -- correctly no trade recorded
            continue

        if state == 'IN_POSITION':
            if is_intraday and hasattr(idx, 'hour') and idx.hour == 15 and idx.minute >= 15:
                exit_p = row['Close']
                log_exit(exit_p, idx, 'SQ_OFF'); state = 'FLAT'; continue
            if pos_type == 'Long':
                if row['Low'] <= sl:
                    log_exit(sl * (1 - slippage / 100.0), idx, 'SL'); state = 'FLAT'
                elif row['High'] >= tp:
                    log_exit(tp * (1 - slippage / 100.0), idx, 'TP'); state = 'FLAT'
            else:
                if row['High'] >= sl:
                    log_exit(sl * (1 + slippage / 100.0), idx, 'SL'); state = 'FLAT'
                elif row['Low'] <= tp:
                    log_exit(tp * (1 + slippage / 100.0), idx, 'TP'); state = 'FLAT'

    return pd.DataFrame(trades)

def backtest_metrics(trades_df, out_of_sample_cutoff=None):
    """Win rate correctly counts profitable SQ_OFF exits. Sharpe is annualized using the
    ACTUAL trade frequency observed, not a fixed daily 252 factor."""
    if trades_df.empty:
        return None
    df = trades_df.copy()
    df['EntryDate_dt'] = pd.to_datetime(df['EntryDate'], errors='coerce')
    # Normalize timezone before comparing -- yfinance can hand back tz-aware OR tz-naive
    # timestamps depending on interval/platform/pandas version, and pandas refuses to
    # compare mixed tz-aware vs tz-naive values (this was the TypeError you hit).
    if df['EntryDate_dt'].dt.tz is not None:
        df['EntryDate_dt'] = df['EntryDate_dt'].dt.tz_localize(None)
    if out_of_sample_cutoff is not None:
        cutoff_ts = pd.Timestamp(out_of_sample_cutoff)
        if cutoff_ts.tzinfo is not None:
            cutoff_ts = cutoff_ts.tz_localize(None)
        df = df[df['EntryDate_dt'] >= cutoff_ts]
    if df.empty:
        return None

    total_trades = len(df)
    is_win = (df['Result'] == 'TP') | ((df['Result'] == 'SQ_OFF') & (df['Profit_Pct'] > 0))
    win_rate = is_win.mean() * 100

    equity = 100000.0
    curve = [100000.0]
    for _, tr in df.iterrows():
        equity *= (1.0 + tr['Profit_Pct'])
        curve.append(equity)
    net_profit_pct = (equity - 100000.0) / 100000.0 * 100

    caps = pd.Series(curve)
    cum_max = caps.cummax()
    drawdowns = (caps - cum_max) / cum_max * 100
    max_dd = drawdowns.min()

    returns = pd.Series(curve).pct_change().dropna()
    date_span_days = max((df['EntryDate_dt'].max() - df['EntryDate_dt'].min()).days, 1)
    trades_per_year = total_trades / date_span_days * 365.0
    sharpe = (returns.mean() / returns.std()) * np.sqrt(trades_per_year) if len(returns) > 1 and returns.std() > 0 else 0.0

    return {
        'total_trades': total_trades, 'win_rate': win_rate, 'net_profit_pct': net_profit_pct,
        'max_dd': max_dd, 'sharpe': sharpe
    }

# -----------------------------------------------------------------------------
# 7. PERSISTENT SIGNAL LEDGER
# -----------------------------------------------------------------------------
def save_signals_to_db(new_signals_df):
    if new_signals_df.empty:
        return
    if os.path.exists(SIGNALS_FILE):
        try:
            existing_df = pd.read_csv(SIGNALS_FILE)
            combined_df = pd.concat([existing_df, new_signals_df], ignore_index=True)
            combined_df.drop_duplicates(subset=['Date/Time', 'Ticker', 'Signal Type'], keep='last', inplace=True)
            combined_df.to_csv(SIGNALS_FILE, index=False)
        except Exception:
            new_signals_df.to_csv(SIGNALS_FILE, index=False)
    else:
        new_signals_df.to_csv(SIGNALS_FILE, index=False)

def load_signals_db():
    if os.path.exists(SIGNALS_FILE):
        try:
            return pd.read_csv(SIGNALS_FILE)
        except Exception:
            return pd.DataFrame()
    return pd.DataFrame()

def sync_signals_db_live():
    if not os.path.exists(SIGNALS_FILE):
        return
    try:
        df_db = pd.read_csv(SIGNALS_FILE)
        if df_db.empty:
            return
        for idx, row in df_db.iterrows():
            current_outcome = str(row.get('Outcome', '\U0001F535 Active'))
            if 'Hit' in current_outcome:
                continue
            ticker = row['Ticker'] + ".NS"
            entry_time = str(row['Date/Time'])
            sl = float(row['Stop Loss'])
            tp = float(row['Take Profit'])
            sig_type = str(row['Signal Type'])

            df_hist = fetch_stock_data(ticker, period="3y", interval="1d")
            if df_hist.empty:
                continue
            df_hist = df_hist[df_hist.index >= pd.to_datetime(entry_time)]

            outcome, hit_time = "\U0001F535 Active", "-"
            for date_idx, h_row in df_hist.iterrows():
                h_date_str = date_idx.strftime('%Y-%m-%d')
                if sig_type == "BULLISH":
                    if float(h_row['Low']) <= sl:
                        outcome, hit_time = "\u274C Stop-Loss Hit", h_date_str; break
                    elif float(h_row['High']) >= tp:
                        outcome, hit_time = "\u2705 Target Hit", h_date_str; break
                else:
                    if float(h_row['High']) >= sl:
                        outcome, hit_time = "\u274C Stop-Loss Hit", h_date_str; break
                    elif float(h_row['Low']) <= tp:
                        outcome, hit_time = "\u2705 Target Hit", h_date_str; break

            df_db.loc[idx, 'Outcome'] = outcome
            df_db.loc[idx, 'Hit Date/Time'] = hit_time
        df_db.to_csv(SIGNALS_FILE, index=False)
    except Exception:
        pass

def check_pm_trigger_status(ticker, gap_pct, entry_trigger, sl_level, tp_lvl):
    try:
        df_5m = yf.download(ticker, period="1d", interval="5m", progress=False, auto_adjust=True)
        if isinstance(df_5m.columns, pd.MultiIndex):
            df_5m.columns = df_5m.columns.get_level_values(0)
        df_5m = df_5m.dropna(subset=['Close'])
        if df_5m.empty:
            return "Pending (Market Not Open)", "-", "Active", "-"

        first_candle = df_5m.iloc[0]
        first_high, first_low = float(first_candle['High']), float(first_candle['Low'])
        triggered, trigger_time, outcome, outcome_time = False, "-", "Active", "-"

        for idx, row in df_5m.iloc[1:].iterrows():
            candle_time = idx.strftime('%I:%M %p') if hasattr(idx, 'strftime') else str(idx)
            if gap_pct >= 0:
                if not triggered:
                    if float(row['High']) >= first_high:
                        triggered, trigger_time = True, candle_time
                else:
                    if float(row['Low']) <= sl_level:
                        outcome, outcome_time = "\u274C Stop-Loss Hit", candle_time; break
                    elif float(row['High']) >= tp_lvl:
                        outcome, outcome_time = "\u2705 Target Hit", candle_time; break
            else:
                if not triggered:
                    if float(row['Low']) <= first_low:
                        triggered, trigger_time = True, candle_time
                else:
                    if float(row['High']) >= sl_level:
                        outcome, outcome_time = "\u274C Stop-Loss Hit", candle_time; break
                    elif float(row['Low']) <= tp_lvl:
                        outcome, outcome_time = "\u2705 Target Hit", candle_time; break

        if not triggered:
            return "Pending Breakout", "-", "Active", "-"
        status = f"\U0001F7E2 Triggered at {trigger_time}" if gap_pct >= 0 else f"\U0001F534 Triggered at {trigger_time}"
        return status, trigger_time, outcome, outcome_time
    except Exception:
        return "Not Triggered", "-", "Active", "-"

# -----------------------------------------------------------------------------
# 8. SESSION STATE
# -----------------------------------------------------------------------------
for key in ['eod_playbook_df', 'yesterday_signals_df', 'live_screener_df', 'leaderboard_df', 'top5_trend_df', 'fyers_premarket_df']:
    if key not in st.session_state:
        st.session_state[key] = None

if 'fyers_access_token' not in st.session_state:
    st.session_state['fyers_access_token'] = None

if 'premarket_watchlist_df' not in st.session_state:
    loaded_pm = None
    if os.path.exists(PREMARKET_FILE):
        try:
            temp_df = pd.read_csv(PREMARKET_FILE)
            if not temp_df.empty and 'Scan Date' in temp_df.columns:
                if str(temp_df['Scan Date'].iloc[0]) == datetime.now().strftime('%Y-%m-%d'):
                    loaded_pm = temp_df
        except Exception:
            pass
    st.session_state['premarket_watchlist_df'] = loaded_pm

# -----------------------------------------------------------------------------
# 9. HEADER + LIVE INDEX STRIP + MARKET REGIME BANNER
# -----------------------------------------------------------------------------
indices_data = fetch_market_indices()
nifty_daily = fetch_nifty_series(period="2y", interval="1d")
market_regime = get_market_regime(nifty_daily)
nifty_rs_series = get_nifty_rs_series(nifty_daily)

# Yahoo Finance carries NO NSE pre-open auction feed (9:00-9:15 AM). Its "today" daily bar
# typically doesn't exist until regular trading actually starts printing (~9:16 AM). Detect
# this so the Pre-Market tab can say so instead of silently returning an empty scan.
_today_ist = datetime.now().date()
_latest_nifty_date = nifty_daily.index[-1].date() if not nifty_daily.empty else None
SESSION_DATA_LIVE_TODAY = (_latest_nifty_date == _today_ist)

st.markdown("<div class='main-header'>\U0001F451 QUANT-EDGE FLAGSHIP TRADING TERMINAL v16</div>", unsafe_allow_html=True)
st.caption("Fixed-logic screener: no look-ahead entries, market-regime aware, probability-scored, position-sized.")

regime_class = {"BULL": "regime-bull", "BEAR": "regime-bear", "CHOPPY": "regime-choppy"}.get(market_regime, "regime-choppy")
regime_msg = {
    "BULL": "\U0001F7E2 MARKET REGIME: BULL -- Nifty above rising 20/50 EMA. Long setups favoured.",
    "BEAR": "\U0001F534 MARKET REGIME: BEAR -- Nifty below falling 20/50 EMA. Short setups favoured.",
    "CHOPPY": "\U0001F7E1 MARKET REGIME: CHOPPY -- Nifty trend unclear. Trade smaller size, expect more false breaks.",
    "UNKNOWN": "\u26AA MARKET REGIME: UNKNOWN -- insufficient Nifty history loaded."
}.get(market_regime, "")
st.markdown(f"<div class='regime-banner {regime_class}'>{regime_msg}</div>", unsafe_allow_html=True)

col_idx1, col_idx2, col_idx3 = st.columns(3)
for idx, (name, metrics) in enumerate(indices_data.items()):
    col = [col_idx1, col_idx2, col_idx3][idx]
    change_style = "change-up" if metrics["Change_Val"] >= 0 else "change-down"
    sign = "+" if metrics["Change_Val"] >= 0 else ""
    with col:
        st.markdown(f"""
        <div class="metric-card">
            <div class="metric-header">{name}</div>
            <div class="metric-val">\u20B9{metrics['LTP']:,.2f}</div>
            <div class="metric-change {change_style}">{sign}{metrics['Change_Val']:,.2f} ({sign}{metrics['Change_Pct']:.2f}%)</div>
        </div>
        """, unsafe_allow_html=True)

# -----------------------------------------------------------------------------
# 10. SIDEBAR
# -----------------------------------------------------------------------------
st.sidebar.header("\u2699\uFE0F Quantitative Panel")

workspace = st.sidebar.selectbox("Select Target Workspace", ["Intraday Workspace (Short Timeframes)", "Swing Workspace (High Timeframes)"])
IS_INTRADAY_MODE = workspace.startswith("Intraday")   # <- single source of truth, used everywhere below

universe_selection = st.sidebar.selectbox("Stock Portfolio Universe", ["Full NSE F&O (219 Stocks)", "Custom Watchlist"] + list(SECTOR_MAP.keys()))
if universe_selection == "Full NSE F&O (219 Stocks)":
    active_tickers = FO_UNIVERSE
    st.sidebar.success(f"Loaded {len(active_tickers)} F&O symbols.")
elif universe_selection == "Custom Watchlist":
    custom_in = st.sidebar.text_area("Enter tickers (comma separated):", value="TATAMOTORS.NS,RELIANCE.NS,TCS.NS,HDFCBANK.NS")
    active_tickers = [t.strip().upper() for t in custom_in.split(",") if t.strip()]
else:
    active_tickers = [t.strip() for t in SECTOR_MAP[universe_selection].split(",")]
    st.sidebar.success(f"Loaded {len(active_tickers)} sectoral assets.")

clean_tickers = ["^NSEI" if t == "NIFTY_50" else t for t in active_tickers]

if IS_INTRADAY_MODE:
    st.sidebar.subheader("\u23F1\uFE0F Intraday Settings")
    tf_option = st.sidebar.selectbox("Active Timeframe", ["5-Minute", "15-Minute", "Custom Intraday"])
    if tf_option == "5-Minute":
        selected_interval, history_limit = "5m", "30d"
    elif tf_option == "15-Minute":
        selected_interval, history_limit = "15m", "60d"
    else:
        selected_interval = st.sidebar.selectbox("Select Custom Intraday", ["1m", "2m", "5m", "15m", "30m", "60m", "90m"])
        history_limit = "30d" if selected_interval in ["1m", "2m", "5m"] else "60d"
    use_orb = True
else:
    st.sidebar.subheader("\u23F1\uFE0F Swing Settings")
    tf_option = st.sidebar.selectbox("Active Timeframe", ["1-Hour", "1-Day (Daily)", "1-Week (Weekly)", "Custom Swing"])
    if tf_option == "1-Hour":
        selected_interval, history_limit = "60m", "2y"
    elif tf_option == "1-Day (Daily)":
        selected_interval, history_limit = "1d", "max"
    elif tf_option == "1-Week (Weekly)":
        selected_interval, history_limit = "1wk", "max"
    else:
        selected_interval = st.sidebar.selectbox("Select Custom Swing", ["1d", "1wk", "1mo"])
        history_limit = "max"
    use_orb = False  # ORB never applies to swing -- fixed mislabel from v15

st.sidebar.subheader("\U0001F9EE Strategy & Risk Parameters")
risk_reward_ratio = st.sidebar.slider("Risk-Reward Target (R:R)", 1.0, 5.0, 2.0, 0.5)
slippage_rate = st.sidebar.slider("Slippage Buffer (%)", 0.0, 0.2, 0.05, 0.01)
commission_rate = st.sidebar.slider("Brokerage & Charges (%)", 0.0, 0.1, 0.03, 0.01)
min_prob_score = st.sidebar.slider("Minimum Probability Score to Show", 0, 100, 60, 5,
                                    help="Composite of trend alignment, relative strength vs Nifty, volume confirmation and volatility extension. Higher = cleaner setup, not a guarantee.")
apply_regime_filter = st.sidebar.checkbox("Apply Market Regime Filter", value=True,
                                           help="Suppresses counter-trend signals and blocks all signals when Nifty regime is CHOPPY.")

st.sidebar.subheader("\U0001F4B0 Position Sizing")
capital_base = st.sidebar.number_input("Trading Capital (\u20B9)", min_value=10000, value=200000, step=10000)
risk_per_trade_pct = st.sidebar.slider("Risk per Trade (% of capital)", 0.25, 3.0, 1.0, 0.25,
                                        help="Institutional-style fixed-fractional risk. Quantity is derived, not guessed.")

st.sidebar.subheader("\U0001F504 Live Refresh")
refresh_rate = st.sidebar.selectbox("Auto Refresh Window", ["Manual Only", "30 Seconds", "60 Seconds", "2 Minutes"])
if refresh_rate != "Manual Only":
    r_seconds = {"30 Seconds": 30, "60 Seconds": 60, "2 Minutes": 120}[refresh_rate]
    if AUTOREFRESH_AVAILABLE:
        st_autorefresh(interval=r_seconds * 1000, key="live_autorefresh")
        st.sidebar.caption(f"\u2705 Auto-refreshing every {r_seconds}s.")
    else:
        st.sidebar.warning("Run `pip install streamlit-autorefresh` to enable real auto-refresh (the old JS trick never actually worked).")

def passes_regime_filter(is_bull_signal):
    if not apply_regime_filter:
        return True
    if market_regime == "CHOPPY" or market_regime == "UNKNOWN":
        return False
    if market_regime == "BULL" and not is_bull_signal:
        return False
    if market_regime == "BEAR" and is_bull_signal:
        return False
    return True

tab_eod, tab_premarket, tab_live, tab_leaderboard, tab_charts, tab_signal_db = st.tabs([
    "\U0001F4CA EOD Scanned Playbook", "\u23F0 Pre-Market Watchlist", "\U0001F7E2 Live Setup Scanner",
    "\U0001F4C8 Performance Backtester", "\U0001F50D Institutional Visualizer", "\U0001F4BE Signal Ledger"
])

# -----------------------------------------------------------------------------
# TAB 1: EOD SCANNED PLAYBOOK
# -----------------------------------------------------------------------------
with tab_eod:
    st.header("\U0001F4CA EOD Scanned Playbook")
    st.write("Compiles high-conviction setups from the last completed session. Entry Trigger is a breakout-confirmation stop level for tomorrow -- not today's close.")

    col_eod_f1, col_eod_f2 = st.columns(2)
    with col_eod_f1:
        eod_sym = st.text_input("Search Ticker:", placeholder="e.g. TATAMOTORS", key="eod_search")
    with col_eod_f2:
        eod_seg = st.selectbox("Filter Segment/Sector:", ["All Universes", "Full F&O"] + list(SECTOR_MAP.keys()), key="eod_segment")

    if st.button("\U0001F680 Compile EOD Playbook Now", key="eod_run_btn"):
        eod_rows, new_persist = [], []
        target_universe = clean_tickers
        if eod_seg != "All Universes" and eod_seg != "Full F&O":
            target_universe = [t.strip() for t in SECTOR_MAP[eod_seg].split(",")]

        progress_bar_eod = st.progress(0.0)
        total_eod = len(target_universe)
        with st.spinner(f"Scanning {total_eod} symbols..."):
            df_bulk_eod = fetch_bulk_stock_data(target_universe, period="2y", interval="1d")
            for idx, ticker in enumerate(target_universe):
                progress_bar_eod.progress((idx + 1) / total_eod)
                df = get_symbol_df(df_bulk_eod, ticker, "2y", "1d")
                if df.empty or len(df) < 50:
                    continue

                df_signals = generate_signals_for_asset(df, lookback=10, is_intraday=False, rr=risk_reward_ratio, use_orb=False)
                if df_signals.empty:
                    continue
                df_signals = compute_probability_score(df_signals, nifty_rs_series)

                latest_row = df_signals.iloc[-1]
                bullish_setup = bool(latest_row['Bullish_Sweep'])
                bearish_setup = bool(latest_row['Bearish_Sweep'])
                if not (bullish_setup or bearish_setup):
                    continue
                if not passes_regime_filter(bullish_setup):
                    continue
                score = latest_row.get('Probability_Score', np.nan)
                if pd.isna(score) or score < min_prob_score:
                    continue

                sig_type = "BULLISH" if bullish_setup else "BEARISH"
                color_tag = "\U0001F7E2" if bullish_setup else "\U0001F534"
                trigger, sl, tp = latest_row['Entry_Trigger'], latest_row['Stop_Loss'], latest_row['Take_Profit']
                qty, deployed = position_size(capital_base, risk_per_trade_pct, trigger, sl)

                eod_rows.append({
                    "Symbol": ticker.replace(".NS", ""), "Setup": f"{color_tag} {sig_type} RECLAIM",
                    "Score": int(round(score)), "LTP": f"\u20B9{latest_row['Close']:.2f}",
                    "Entry Trigger": f"\u20B9{trigger:.2f}", "Stop Loss": f"\u20B9{sl:.2f}", "Target": f"\u20B9{tp:.2f}",
                    "R:R": f"1:{risk_reward_ratio:.1f}", "Qty (sized)": qty, "Capital Used": f"\u20B9{deployed:,.0f}"
                })
                new_persist.append({
                    "Date/Time": latest_row.name.strftime('%Y-%m-%d') if hasattr(latest_row.name, 'strftime') else str(latest_row.name),
                    "Ticker": ticker.replace(".NS", ""), "Signal Type": sig_type, "Entry Trigger": round(trigger, 2),
                    "Stop Loss": round(sl, 2), "Take Profit": round(tp, 2), "Score": int(round(score)),
                    "Volume": int(latest_row['Volume']), "R:R Ratio": f"1:{risk_reward_ratio:.1f}",
                    "Outcome": "\U0001F535 Active", "Hit Date/Time": "-", "Source": "EOD_SWING"
                })
            if new_persist:
                save_signals_to_db(pd.DataFrame(new_persist))
        progress_bar_eod.empty()
        st.session_state['eod_playbook_df'] = pd.DataFrame(eod_rows) if eod_rows else pd.DataFrame()

    if st.session_state['eod_playbook_df'] is not None:
        st.subheader("\U0001F4CB Confirmed EOD Playbook")
        if not st.session_state['eod_playbook_df'].empty:
            eod_df = st.session_state['eod_playbook_df'].copy()
            if eod_sym:
                eod_df = eod_df[eod_df['Symbol'].str.contains(eod_sym.upper())]
            eod_df = eod_df.sort_values(by="Score", ascending=False).reset_index(drop=True)
            st.dataframe(eod_df, use_container_width=True)
            st.success(f"\U0001F3AF {len(eod_df)} setups above score {min_prob_score}. Entry Trigger is a stop order for tomorrow's confirmation break, not today's close.")
        else:
            st.info("No setups cleared the probability score and regime filters today. Try lowering the score threshold in the sidebar.")

    st.markdown("---")
    st.subheader("\U0001F4C5 Previous EOD Sessions")
    log_db_eod = load_signals_db()
    if not log_db_eod.empty and 'Source' in log_db_eod.columns:
        eod_hist = log_db_eod[log_db_eod['Source'] == 'EOD_SWING'].copy()
        if not eod_hist.empty:
            eod_hist['Date_only'] = pd.to_datetime(eod_hist['Date/Time'], errors='coerce').dt.date
            avail_dates = sorted([d for d in eod_hist['Date_only'].dropna().unique() if d != datetime.now().date()], reverse=True)
            if avail_dates:
                pick_date = st.selectbox("Select a previous session date:", avail_dates, key="eod_hist_date")
                show_df = eod_hist[eod_hist['Date_only'] == pick_date].drop(columns=['Date_only']).sort_values(by="Score", ascending=False).reset_index(drop=True)
                st.dataframe(show_df, use_container_width=True)
            else:
                st.info("No prior EOD sessions logged yet -- once you run the compiler over a few trading days, they'll show up here for lookback.")
        else:
            st.info("No EOD-sourced signals logged yet under the new Source tag.")
    else:
        st.info("No ledger history yet, or it predates today's Source-tagging fix -- everything you compile from now on will populate this properly.")

# -----------------------------------------------------------------------------
# TAB 2: PRE-MARKET WATCHLIST
# -----------------------------------------------------------------------------
with tab_premarket:
    st.header("\u23F0 Pre-Market Institutional Block Watchlist")
    st.write("Flags stocks opening with unusually large money deployment and volume surge relative to their own recent history.")

    st.markdown("#### \U0001F4E1 Fyers Live Connection")
    if not FYERS_SDK_AVAILABLE:
        st.error("`fyers-apiv3` isn't installed in this environment. Add `fyers-apiv3` to requirements.txt and redeploy.")
    elif get_fyers_credentials()[0] is None:
        st.warning("Fyers credentials not found. Add `FYERS_APP_ID`, `FYERS_SECRET_KEY`, and `FYERS_REDIRECT_URI` under your Streamlit Cloud app's Settings \u2192 Secrets.")
    else:
        qp = st.query_params
        auth_code = qp.get("auth_code", None)
        if isinstance(auth_code, list):
            auth_code = auth_code[0]

        if st.session_state['fyers_access_token'] is None and auth_code:
            fy_session = get_fyers_session()
            fy_session.set_token(auth_code)
            try:
                token_resp = fy_session.generate_token()
                if token_resp.get("access_token"):
                    st.session_state['fyers_access_token'] = token_resp["access_token"]
                    st.query_params.clear()
                    st.rerun()
                else:
                    st.error(f"Fyers token exchange failed: {token_resp}")
            except Exception as e:
                st.error(f"Fyers login error: {e}")

        if st.session_state['fyers_access_token']:
            col_fy1, col_fy2 = st.columns([3, 1])
            with col_fy1:
                st.success("\u2705 Fyers connected for today's session -- you can pull real pre-open/live quotes below.")
            with col_fy2:
                if st.button("\U0001F6AA Disconnect", key="fyers_disconnect_btn"):
                    st.session_state['fyers_access_token'] = None
                    st.rerun()
        else:
            fy_session = get_fyers_session()
            try:
                auth_url = fy_session.generate_authcode()
                st.link_button("\U0001F511 Login with Fyers", auth_url)
                st.caption("Fyers tokens expire daily -- log in again each trading morning, same as your Sheets screener.")
            except Exception as e:
                st.error(f"Couldn't build Fyers login link: {e}")

    if not SESSION_DATA_LIVE_TODAY:
        st.warning(
            "Yahoo Finance has no feed for NSE's pre-open auction (9:00-9:15 AM). "
            "Its 'today' price data typically doesn't exist until regular trading actually "
            "starts printing, around 9:16 AM onward -- running the scan below before that will "
            "silently look at yesterday's bar and return few or no matches, not an error. "
            "Connect Fyers above for real 9:08 AM data instead."
        )
        if st.button("\U0001F9F9 Clear Cache & Recheck Now", key="pm_clear_cache_btn"):
            fetch_bulk_stock_data.clear()
            fetch_stock_data.clear()
            fetch_nifty_series.clear()
            st.rerun()

    st.markdown("#### \U0001F50E Yahoo Finance Scan (post ~9:16 AM)")
    col_pm_1, col_pm_2, col_pm_3 = st.columns(3)
    with col_pm_1:
        pm_max_gap = st.slider("Max Allowed Pre-Market Gap (%)", 0.1, 5.0, 1.2, 0.1, key="pm_gap_slider")
    with col_pm_2:
        pm_min_crores = st.slider("Minimum Money Deployed (\u20B9 Crores)", 1, 50, 4, 1, key="pm_crore_slider")
    with col_pm_3:
        pm_vol_surge = st.slider("Relative Volume Surge Factor (x)", 1.5, 10.0, 2.5, 0.5, key="pm_surge_slider")

    if st.button("\U0001F50D Scan Institutional Pre-Market Surge", key="pm_run_btn"):
        pm_matches = []
        progress_pm = st.progress(0.0)
        total_pm = len(clean_tickers)
        with st.spinner("Downloading opening auction blocks..."):
            df_bulk_pm = fetch_bulk_stock_data(clean_tickers, period="30d", interval="1d")
            for idx, ticker in enumerate(clean_tickers):
                progress_pm.progress((idx + 1) / total_pm)
                df = get_symbol_df(df_bulk_pm, ticker, "30d", "1d", min_len=15)
                if df.empty or len(df) < 15:
                    continue

                yest_close = float(df['Close'].iloc[-2])
                today_open = float(df['Open'].iloc[-1])
                today_open_vol = float(df['Volume'].iloc[-1])
                hist_avg_vol = float(df['Volume'].iloc[-15:-1].mean())
                if hist_avg_vol <= 0:
                    continue

                relative_vol_surge = today_open_vol / hist_avg_vol
                pm_value_crores = (today_open * today_open_vol) / 10000000.0
                gap_pct = ((today_open - yest_close) / yest_close) * 100
                abs_gap = abs(gap_pct)

                if abs_gap <= pm_max_gap and pm_value_crores >= pm_min_crores and relative_vol_surge >= pm_vol_surge:
                    if not passes_regime_filter(gap_pct >= 0):
                        continue
                    sig = "INSTITUTIONAL BUY" if gap_pct >= 0 else "INSTITUTIONAL SELL"
                    color_tag = "\U0001F7E2" if gap_pct >= 0 else "\U0001F534"
                    yest_high, yest_low = float(df['High'].iloc[-2]), float(df['Low'].iloc[-2])
                    entry = today_open
                    sl = yest_low if gap_pct >= 0 else yest_high
                    tp = entry + (entry - sl) * risk_reward_ratio if gap_pct >= 0 else entry - (sl - entry) * risk_reward_ratio
                    qty, deployed = position_size(capital_base, risk_per_trade_pct, entry, sl)

                    status, trig_t, out_val, out_t = check_pm_trigger_status(ticker, gap_pct, entry, sl, tp)
                    pm_matches.append({
                        "Symbol": ticker.replace(".NS", ""), "Indicative Open": f"\u20B9{today_open:,.2f}",
                        "Gap": f"{gap_pct:+.2f}%", "Money Deployed": f"\u20B9{pm_value_crores:.2f} Cr",
                        "Vol Surge": f"{relative_vol_surge:.1f}x", "Setup": f"{color_tag} {sig}",
                        "Entry": f"\u20B9{entry:,.2f}", "Stop Loss": f"\u20B9{sl:,.2f}", "Target": f"\u20B9{tp:,.2f}",
                        "Qty": qty, "R:R": f"1:{risk_reward_ratio:.1f}", "Trigger Status": status,
                        "Trigger Time": trig_t, "Outcome": out_val, "Outcome Time": out_t,
                        "Scan Date": datetime.now().strftime('%Y-%m-%d')
                    })
        progress_pm.empty()
        if pm_matches:
            pm_df = pd.DataFrame(pm_matches).sort_values(by="Vol Surge", ascending=False).reset_index(drop=True)
            st.session_state['premarket_watchlist_df'] = pm_df
            try:
                pm_df.to_csv(PREMARKET_FILE, index=False)
            except Exception:
                pass
        else:
            st.session_state['premarket_watchlist_df'] = pd.DataFrame()
            if os.path.exists(PREMARKET_FILE):
                try:
                    os.remove(PREMARKET_FILE)
                except Exception:
                    pass

    if st.session_state['premarket_watchlist_df'] is not None:
        st.subheader("\U0001F525 Opening Watchlist")
        if not st.session_state['premarket_watchlist_df'].empty:
            st.dataframe(st.session_state['premarket_watchlist_df'], use_container_width=True)
            if st.button("\U0001F504 Sync Live Outcomes", key="pm_sync_live_btn"):
                with st.spinner("Checking live 5-minute candles..."):
                    pm_df = st.session_state['premarket_watchlist_df'].copy()
                    for idx, row in pm_df.iterrows():
                        symbol = row['Symbol'] + ".NS"
                        gap_pct = float(str(row['Gap']).replace('%', '').replace('+', '').strip())
                        entry = float(str(row['Entry']).replace('\u20B9', '').replace(',', '').strip())
                        sl = float(str(row['Stop Loss']).replace('\u20B9', '').replace(',', '').strip())
                        tp = float(str(row['Target']).replace('\u20B9', '').replace(',', '').strip())
                        status, trig_t, out_val, out_t = check_pm_trigger_status(symbol, gap_pct, entry, sl, tp)
                        pm_df.loc[idx, 'Trigger Status'] = status
                        pm_df.loc[idx, 'Trigger Time'] = trig_t
                        pm_df.loc[idx, 'Outcome'] = out_val
                        pm_df.loc[idx, 'Outcome Time'] = out_t
                    st.session_state['premarket_watchlist_df'] = pm_df
                    try:
                        pm_df.to_csv(PREMARKET_FILE, index=False)
                    except Exception:
                        pass
                    st.rerun()
        else:
            st.info("No stocks matched the pre-market filters.")

    st.markdown("---")
    st.markdown("#### \u26A1 Fyers Live Scan (real pre-open/9:08 AM data)")
    if st.session_state['fyers_access_token']:
        if st.button("\U0001F4E1 Scan via Fyers (Real Live Data)", key="fyers_scan_btn"):
            fyers_client = get_fyers_client()
            if fyers_client is None:
                st.error("Not connected -- log in above first.")
            else:
                with st.spinner("Fetching live quotes and building volume baseline..."):
                    df_bulk_baseline = fetch_bulk_stock_data(clean_tickers, period="30d", interval="1d")
                    quotes = fetch_fyers_quotes_bulk(fyers_client, clean_tickers)
                    fy_matches = []
                    for ticker in clean_tickers:
                        fy_sym = to_fyers_symbol(ticker)
                        q = quotes.get(fy_sym)
                        if not q:
                            continue
                        ltp = q.get("lp")
                        today_open = q.get("open_price")
                        prev_close = q.get("prev_close_price")
                        volume_today = q.get("volume", 0)
                        if not (ltp and today_open and prev_close):
                            continue

                        df_base = get_symbol_df(df_bulk_baseline, ticker, "30d", "1d", min_len=10)
                        if df_base.empty or len(df_base) < 10:
                            continue
                        hist_avg_vol = float(df_base['Volume'].iloc[-10:].mean())
                        if hist_avg_vol <= 0:
                            continue

                        gap_pct = ((today_open - prev_close) / prev_close) * 100
                        rel_vol = (volume_today / hist_avg_vol) if hist_avg_vol else 0
                        money_cr = (ltp * volume_today) / 10000000.0

                        if abs(gap_pct) <= pm_max_gap and money_cr >= pm_min_crores and rel_vol >= pm_vol_surge:
                            if not passes_regime_filter(gap_pct >= 0):
                                continue
                            sig = "INSTITUTIONAL BUY" if gap_pct >= 0 else "INSTITUTIONAL SELL"
                            color = "\U0001F7E2" if gap_pct >= 0 else "\U0001F534"
                            yest_high, yest_low = float(df_base['High'].iloc[-2]), float(df_base['Low'].iloc[-2])
                            entry = today_open
                            sl = yest_low if gap_pct >= 0 else yest_high
                            tp = entry + (entry - sl) * risk_reward_ratio if gap_pct >= 0 else entry - (sl - entry) * risk_reward_ratio
                            qty, deployed = position_size(capital_base, risk_per_trade_pct, entry, sl)
                            fy_matches.append({
                                "Symbol": ticker.replace(".NS", ""), "LTP": f"\u20B9{ltp:.2f}", "Today Open": f"\u20B9{today_open:.2f}",
                                "Gap": f"{gap_pct:+.2f}%", "Money Deployed": f"\u20B9{money_cr:.2f} Cr", "Vol Surge": f"{rel_vol:.1f}x",
                                "Setup": f"{color} {sig}", "Entry": f"\u20B9{entry:.2f}", "Stop Loss": f"\u20B9{sl:.2f}",
                                "Target": f"\u20B9{tp:.2f}", "Qty": qty, "R:R": f"1:{risk_reward_ratio:.1f}"
                            })
                    if fy_matches:
                        st.session_state['fyers_premarket_df'] = pd.DataFrame(fy_matches).sort_values(by="Vol Surge", ascending=False).reset_index(drop=True)
                    else:
                        st.session_state['fyers_premarket_df'] = pd.DataFrame()

        if st.session_state.get('fyers_premarket_df') is not None:
            if not st.session_state['fyers_premarket_df'].empty:
                st.dataframe(st.session_state['fyers_premarket_df'], use_container_width=True)
            else:
                st.info("No live matches right now via Fyers.")

        with st.expander("\U0001F41B Debug: raw quote for first symbol (check field names match)"):
            if clean_tickers:
                dbg_client = get_fyers_client()
                if dbg_client is not None:
                    dbg_q = fetch_fyers_quotes_bulk(dbg_client, [clean_tickers[0]])
                    st.json(dbg_q)
                    st.caption("If Gap/Money Deployed/Vol Surge look wrong above, paste this JSON back to me and I'll fix the field mapping.")
    else:
        st.info("Log in with Fyers above to enable real 9:08 AM pre-open scanning.")

    st.markdown("---")
    st.subheader("\U0001F3C6 Top 5 One-Way Trend Leaders (F&O, for Intraday)")
    st.caption(
        "Ranks the full F&O list by trend alignment, relative strength vs Nifty, volume "
        "confirmation, and how consistently each stock has pushed in one direction over the "
        "last 5 sessions -- true one-way movers, not choppers. Run this right after the "
        "pre-open session settles (~9:16 AM once trading data exists). Entry Trigger is a "
        "breakout-confirmation stop level, same as elsewhere in this app."
    )

    if st.button("\u26A1 Find Today's Top 5 Trend Leaders", key="top5_trend_btn"):
        trend_rows = []
        progress_t5 = st.progress(0.0)
        total_t5 = len(FO_UNIVERSE)
        with st.spinner("Ranking the F&O universe by trend strength..."):
            df_bulk_t5 = fetch_bulk_stock_data(FO_UNIVERSE, period="6mo", interval="1d")
            for idx, ticker in enumerate(FO_UNIVERSE):
                progress_t5.progress((idx + 1) / total_t5)
                df = get_symbol_df(df_bulk_t5, ticker, "6mo", "1d", min_len=55)
                if df.empty or len(df) < 55:
                    continue
                df_ind = compute_indicators(df, is_intraday=False)
                if df_ind.empty or 'EMA_20' not in df_ind.columns:
                    continue
                df_ind = compute_probability_score(df_ind, nifty_rs_series)
                latest = df_ind.iloc[-1]
                if pd.isna(latest.get('Probability_Score')):
                    continue

                direction_up = bool(latest['Close'] > latest['EMA_20'] > latest['EMA_50'])
                direction_down = bool(latest['Close'] < latest['EMA_20'] < latest['EMA_50'])
                if not (direction_up or direction_down):
                    continue
                if not passes_regime_filter(direction_up):
                    continue

                atr_v = latest.get('ATR14', np.nan)
                if pd.isna(atr_v) or atr_v <= 0:
                    continue

                consistency = compute_trend_consistency(df_ind, window=5)
                combined_score = latest['Probability_Score'] * 0.7 + consistency * 0.3

                if direction_up:
                    entry_lvl = latest['High'] * 1.0015
                    sl_lvl = latest['Close'] - 1.5 * atr_v
                else:
                    entry_lvl = latest['Low'] * 0.9985
                    sl_lvl = latest['Close'] + 1.5 * atr_v
                risk_amt = abs(entry_lvl - sl_lvl)
                tp_lvl = entry_lvl + risk_amt * risk_reward_ratio if direction_up else entry_lvl - risk_amt * risk_reward_ratio
                qty, deployed = position_size(capital_base, risk_per_trade_pct, entry_lvl, sl_lvl)

                trend_rows.append({
                    "Symbol": ticker.replace(".NS", ""), "Direction": "\U0001F7E2 UP" if direction_up else "\U0001F534 DOWN",
                    "Trend Score": int(round(combined_score)), "5-Day Consistency": f"{consistency:.0f}%",
                    "LTP": f"\u20B9{latest['Close']:.2f}", "Entry Trigger": f"\u20B9{entry_lvl:.2f}",
                    "Stop Loss": f"\u20B9{sl_lvl:.2f}", "Target": f"\u20B9{tp_lvl:.2f}",
                    "Qty (sized)": qty, "R:R": f"1:{risk_reward_ratio:.1f}"
                })
        progress_t5.empty()
        if trend_rows:
            st.session_state['top5_trend_df'] = pd.DataFrame(trend_rows).sort_values(by="Trend Score", ascending=False).head(5).reset_index(drop=True)
        else:
            st.session_state['top5_trend_df'] = pd.DataFrame()

    if st.session_state.get('top5_trend_df') is not None:
        if not st.session_state['top5_trend_df'].empty:
            st.dataframe(st.session_state['top5_trend_df'], use_container_width=True)
        else:
            st.info("No stocks cleared the trend and regime filters right now.")

# -----------------------------------------------------------------------------
# TAB 3: LIVE SETUP SCANNER
# -----------------------------------------------------------------------------
with tab_live:
    st.header(f"\U0001F7E2 Real-Time {workspace} Active Setup Scanner")
    st.write("Entry Trigger is a breakout-confirmation level -- it must still be crossed to fill. Not a guaranteed price.")

    col_live_f1, col_live_f2 = st.columns(2)
    with col_live_f1:
        live_sym_f = st.text_input("Filter Symbol:", placeholder="e.g. RELIANCE", key="live_search")
    with col_live_f2:
        live_sig_f = st.selectbox("Filter Signal:", ["Show All", "\U0001F7E2 BUY Only", "\U0001F534 SELL Only"], key="live_signal")

    if st.button("\U0001F680 Run Live Scanner", key="live_run_btn"):
        live_rows, yesterday_signals, new_persist_records = [], [], []
        progress_bar_live = st.progress(0.0)
        total_live = len(clean_tickers)
        with st.spinner(f"Scanning {total_live} symbols..."):
            fetch_period = history_limit if not IS_INTRADAY_MODE else history_limit
            df_bulk_live = fetch_bulk_stock_data(clean_tickers, period=fetch_period, interval=selected_interval)
            for idx, ticker in enumerate(clean_tickers):
                progress_bar_live.progress((idx + 1) / total_live)
                df = get_symbol_df(df_bulk_live, ticker, fetch_period, selected_interval, min_len=20)
                if df.empty or len(df) < 20:
                    continue

                df_signals = generate_signals_for_asset(df, lookback=10, is_intraday=IS_INTRADAY_MODE, rr=risk_reward_ratio, use_orb=use_orb)
                if df_signals.empty or len(df_signals) < 2:
                    continue
                df_signals = compute_probability_score(df_signals, None if IS_INTRADAY_MODE else nifty_rs_series)

                latest_row, prev_row = df_signals.iloc[-1], df_signals.iloc[-2]
                bull_active = bool(latest_row['Bullish_ORB']) if use_orb else bool(latest_row['Bullish_Sweep'])
                bear_active = bool(latest_row['Bearish_ORB']) if use_orb else bool(latest_row['Bearish_Sweep'])
                y_bull = bool(prev_row['Bullish_ORB']) if use_orb else bool(prev_row['Bullish_Sweep'])
                y_bear = bool(prev_row['Bearish_ORB']) if use_orb else bool(prev_row['Bearish_Sweep'])

                if (bull_active or bear_active) and passes_regime_filter(bull_active):
                    score = latest_row.get('Probability_Score', np.nan)
                    if not pd.isna(score) and score >= min_prob_score:
                        sig_type = "BUY" if bull_active else "SELL"
                        color_tag = "\U0001F7E2" if bull_active else "\U0001F534"
                        trigger, sl, tp = latest_row['Entry_Trigger'], latest_row['Stop_Loss'], latest_row['Take_Profit']
                        qty, deployed = position_size(capital_base, risk_per_trade_pct, trigger, sl)
                        sig_time_str = latest_row.name.strftime('%Y-%m-%d %H:%M') if hasattr(latest_row.name, 'strftime') else str(latest_row.name)
                        live_rows.append({
                            "Date/Time": sig_time_str, "Symbol": ticker.replace(".NS", ""),
                            "Signal": f"{color_tag} {sig_type}", "Score": int(round(score)),
                            "Current Price": f"\u20B9{latest_row['Close']:.2f}", "Entry Trigger": f"\u20B9{trigger:.2f}",
                            "Stop Loss": f"\u20B9{sl:.2f}", "Target": f"\u20B9{tp:.2f}", "Qty": qty,
                            "R:R": f"1:{risk_reward_ratio:.1f}"
                        })

                if y_bull or y_bear:
                    y_sig_type = "BULLISH" if y_bull else "BEARISH"
                    y_color = "\U0001F7E2" if y_bull else "\U0001F534"
                    sig_time = prev_row.name.strftime('%Y-%m-%d %H:%M') if isinstance(prev_row.name, datetime) else str(prev_row.name)
                    y_trigger, y_sl, y_tp = prev_row['Entry_Trigger'], prev_row['Stop_Loss'], prev_row['Take_Profit']
                    yesterday_signals.append({
                        "Date/Time": sig_time, "Ticker": ticker.replace(".NS", ""), "Signal": f"{y_color} {y_sig_type}",
                        "Entry Trigger": f"\u20B9{y_trigger:.2f}", "Stop Loss": f"\u20B9{y_sl:.2f}", "Target": f"\u20B9{y_tp:.2f}",
                        "R:R": f"1:{risk_reward_ratio:.1f}"
                    })
                    new_persist_records.append({
                        "Date/Time": sig_time, "Ticker": ticker.replace(".NS", ""), "Signal Type": y_sig_type,
                        "Entry Trigger": round(float(y_trigger), 2), "Stop Loss": round(float(y_sl), 2),
                        "Take Profit": round(float(y_tp), 2), "Score": 0, "Volume": int(prev_row['Volume']),
                        "R:R Ratio": f"1:{risk_reward_ratio:.1f}", "Outcome": "\U0001F535 Active", "Hit Date/Time": "-",
                        "Source": "INTRADAY_LIVE"
                    })
            if new_persist_records:
                save_signals_to_db(pd.DataFrame(new_persist_records))
        progress_bar_live.empty()
        st.session_state['live_screener_df'] = pd.DataFrame(live_rows) if live_rows else pd.DataFrame()
        st.session_state['yesterday_signals_df'] = pd.DataFrame(yesterday_signals) if yesterday_signals else pd.DataFrame()

    if st.session_state['live_screener_df'] is not None:
        st.subheader("\U0001F7E2 Active Setups")
        if not st.session_state['live_screener_df'].empty:
            live_df = st.session_state['live_screener_df'].copy()
            if live_sym_f:
                live_df = live_df[live_df['Symbol'].str.contains(live_sym_f.upper())]
            if live_sig_f == "\U0001F7E2 BUY Only":
                live_df = live_df[live_df['Signal'].str.contains("BUY")]
            elif live_sig_f == "\U0001F534 SELL Only":
                live_df = live_df[live_df['Signal'].str.contains("SELL")]
            live_df = live_df.sort_values(by="Score", ascending=False).reset_index(drop=True)
            if not live_df.empty:
                st.dataframe(live_df, use_container_width=True)
            else:
                st.info("No active signals match your filters.")
        else:
            st.info("No active setups triggered on the current candle.")

    if st.session_state['yesterday_signals_df'] is not None and not st.session_state['yesterday_signals_df'].empty:
        st.markdown("---")
        st.subheader("\u23F3 Previous Candle's Signals (for reference)")
        y_df = st.session_state['yesterday_signals_df'].copy()
        if live_sym_f:
            y_df = y_df[y_df['Ticker'].str.contains(live_sym_f.upper())]
        st.dataframe(y_df, use_container_width=True)

# -----------------------------------------------------------------------------
# TAB 4: PERFORMANCE BACKTESTER
# -----------------------------------------------------------------------------
with tab_leaderboard:
    st.header("\U0001F4C8 Performance Backtester & Leaderboard")
    st.write("Simulates realistic breakout-order fills (pending -> triggered -> exit). No trade is ever entered at a price that wasn't actually crossed on a later candle.")

    st.markdown(f"""
    <div style="background-color: #111322; padding: 15px; border-radius: 6px; border-left: 5px solid #00ffcc; margin-bottom: 16px; border: 1px solid #1e2238;">
        <span style="color:#7d84a6; font-size:11px; text-transform:uppercase; letter-spacing:1px;">Active Backtest Frame</span><br/>
        <span style="color:#ffffff; font-size:16px; font-weight:bold;">{workspace} \u2022 Timeframe: {selected_interval} \u2022 Horizon: {history_limit}</span>
    </div>
    """, unsafe_allow_html=True)

    col_bk_f1, col_bk_f2, col_bk_f3 = st.columns(3)
    with col_bk_f1:
        bk_sym_f = st.text_input("Search Stock:", placeholder="e.g. AXISBANK", key="bk_search")
    with col_bk_f2:
        bk_sector_f = st.selectbox("Filter Segment:", ["All F&O List"] + list(SECTOR_MAP.keys()), key="bk_segment")
    with col_bk_f3:
        oos_only = st.checkbox("Out-of-Sample Only (last 30%)", value=True,
                                help="Reports metrics only on the most recent 30% of the tested history, held out to reduce curve-fitting / overfitting to the exact backtest window.")

    if st.button("\U0001F4CA Run F&O Strategy Backtest", key="bk_run_btn"):
        leaderboard_data = []
        progress_bk = st.progress(0.0)
        target_bk_list = FO_UNIVERSE if bk_sector_f == "All F&O List" else [t.strip() for t in SECTOR_MAP[bk_sector_f].split(",")]
        total_bk = len(target_bk_list)

        with st.spinner(f"Backtesting {total_bk} assets..."):
            df_bulk_bk = fetch_bulk_stock_data(target_bk_list, period=history_limit, interval=selected_interval)
            for idx, ticker in enumerate(target_bk_list):
                progress_bk.progress((idx + 1) / total_bk)
                df = get_symbol_df(df_bulk_bk, ticker, history_limit, selected_interval, min_len=40)
                if df.empty or len(df) < 40:
                    continue

                df_calc = generate_signals_for_asset(df, lookback=10, is_intraday=IS_INTRADAY_MODE, rr=risk_reward_ratio, use_orb=use_orb)
                if df_calc.empty:
                    continue
                trades_df = run_backtest_engine(df_calc, is_intraday=IS_INTRADAY_MODE, use_orb=use_orb, rr=risk_reward_ratio,
                                                 slippage=slippage_rate, commission=commission_rate)
                if trades_df.empty:
                    continue

                cutoff = None
                if oos_only:
                    cutoff_pos = int(len(df_calc) * 0.7)
                    cutoff = df_calc.index[cutoff_pos] if cutoff_pos < len(df_calc) else None

                m = backtest_metrics(trades_df, out_of_sample_cutoff=cutoff)
                if m is None or m['total_trades'] < 3:
                    continue

                df_calc = compute_probability_score(df_calc, None if IS_INTRADAY_MODE else nifty_rs_series)
                latest_p = df_calc.iloc[-1]
                entry_level = latest_p['Entry_Trigger'] if not pd.isna(latest_p['Entry_Trigger']) else latest_p['Close']
                sl_level = latest_p['Stop_Loss'] if not pd.isna(latest_p['Stop_Loss']) else latest_p['Close'] * 0.99
                tp_level = latest_p['Take_Profit'] if not pd.isna(latest_p['Take_Profit']) else entry_level + (entry_level - sl_level) * risk_reward_ratio
                qty, deployed = position_size(capital_base, risk_per_trade_pct, entry_level, sl_level)

                leaderboard_data.append({
                    "Ticker": ticker.replace(".NS", ""), "Net Return (%)": round(m['net_profit_pct'], 2),
                    "Win Rate (%)": round(m['win_rate'], 2), "Trades": m['total_trades'],
                    "Max DD (%)": round(m['max_dd'], 2), "Sharpe": round(m['sharpe'], 2),
                    "Latest Score": int(round(latest_p.get('Probability_Score', 0) or 0)),
                    "Entry Trigger": round(entry_level, 2), "Stop Loss": round(sl_level, 2), "Target": round(tp_level, 2),
                    "Qty (sized)": qty, "R:R": f"1:{risk_reward_ratio:.1f}"
                })
            progress_bk.empty()

        if leaderboard_data:
            st.session_state['leaderboard_df'] = pd.DataFrame(leaderboard_data).sort_values(by="Net Return (%)", ascending=False).head(45).reset_index(drop=True)
        else:
            st.session_state['leaderboard_df'] = pd.DataFrame()

    if st.session_state['leaderboard_df'] is not None:
        st.subheader("\U0001F3C6 Top Performing Stocks" + (" (Out-of-Sample)" if oos_only else " (Full Sample)"))
        if not st.session_state['leaderboard_df'].empty:
            leaderboard_df = st.session_state['leaderboard_df'].copy()
            if bk_sym_f:
                leaderboard_df = leaderboard_df[leaderboard_df['Ticker'].str.contains(bk_sym_f.upper())]
            st.dataframe(leaderboard_df, use_container_width=True)
            st.caption("Note: this universe is today's F&O list applied across history -- stocks added/removed from F&O over the window aren't reconstructed (survivorship bias). Treat rankings as a shortlist to investigate, not a guarantee.")
        else:
            st.error("No trades matched parameters, or too few trades (<3) to report. Adjust sidebar settings.")

# -----------------------------------------------------------------------------
# TAB 5: INSTITUTIONAL VISUALIZER
# -----------------------------------------------------------------------------
with tab_charts:
    st.header("\U0001F50D Institutional Level Visualizer")
    st.write("EMAs, VWAP, Pivots, and confirmed Buy/Sell alerts.")

    vis_choices = [t.replace(".NS", "") for t in active_tickers if t != "NIFTY_50"]
    if vis_choices:
        selected_vis_stock = st.selectbox("Select Asset:", vis_choices, key="vis_symbol_select")
        vis_period = "1y" if not IS_INTRADAY_MODE else "5d"
        df_vis = fetch_stock_data(selected_vis_stock + ".NS", period=vis_period, interval=selected_interval)

        if not df_vis.empty and len(df_vis) > 55:
            df_vis = generate_signals_for_asset(df_vis, lookback=10, is_intraday=IS_INTRADAY_MODE, rr=risk_reward_ratio, use_orb=use_orb)

            fig = go.Figure()
            fig.add_trace(go.Candlestick(
                x=df_vis.index, open=df_vis['Open'], high=df_vis['High'], low=df_vis['Low'], close=df_vis['Close'],
                name="Candles", increasing_line_color='#00ffcc', decreasing_line_color='#ff3366',
                increasing_fillcolor='#00ffcc', decreasing_fillcolor='#ff3366', line=dict(width=1.0)
            ))
            if 'EMA_20' in df_vis.columns:
                fig.add_trace(go.Scatter(x=df_vis.index, y=df_vis['EMA_20'], line=dict(color='#ffaa00', width=1.0), name="20 EMA"))
            if 'EMA_50' in df_vis.columns:
                fig.add_trace(go.Scatter(x=df_vis.index, y=df_vis['EMA_50'], line=dict(color='#0077ff', width=1.2), name="50 EMA"))
            if 'Pivot' in df_vis.columns:
                fig.add_trace(go.Scatter(x=df_vis.index, y=df_vis['Pivot'], line=dict(color='#8f95b2', width=0.8, dash='dot'), name="Daily Pivot"))

            bull_signals = df_vis[df_vis['Bullish_Sweep'] | (df_vis['Bullish_ORB'] if 'Bullish_ORB' in df_vis.columns else False)]
            bear_signals = df_vis[df_vis['Bearish_Sweep'] | (df_vis['Bearish_ORB'] if 'Bearish_ORB' in df_vis.columns else False)]
            fig.add_trace(go.Scatter(x=bull_signals.index, y=bull_signals['Low'] * 0.98, mode='markers',
                                      marker=dict(symbol='triangle-up', size=11, color='#00ffcc', line=dict(width=1, color='white')), name="Buy Setup"))
            fig.add_trace(go.Scatter(x=bear_signals.index, y=bear_signals['High'] * 1.02, mode='markers',
                                      marker=dict(symbol='triangle-down', size=11, color='#ff3366', line=dict(width=1, color='white')), name="Sell Setup"))

            fig.update_layout(
                title=f"Structure Map: {selected_vis_stock} (R:R 1:{risk_reward_ratio:.1f})", template="plotly_dark",
                xaxis_rangeslider_visible=False, height=600, paper_bgcolor='#090a10', plot_bgcolor='#101220',
                yaxis_title="Price (\u20B9)", xaxis_title="Date", legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("Not enough history to chart this symbol/timeframe combo yet.")

# -----------------------------------------------------------------------------
# TAB 6: SIGNAL LEDGER
# -----------------------------------------------------------------------------
with tab_signal_db:
    st.header("\U0001F4BE Lifetime Signal Ledger")
    st.write("Permanent log of every generated setup, synced against live prices to verify real win rates over time.")

    log_db = load_signals_db()
    col_db_f1, col_db_f2 = st.columns(2)
    with col_db_f1:
        db_sym = st.text_input("Filter Ticker:", placeholder="e.g. TATAMOTORS", key="db_search")
    with col_db_f2:
        db_sig = st.selectbox("Filter Setup:", ["All Trades", "BULLISH", "BEARISH"], key="db_signal")

    if not log_db.empty:
        if st.button("\U0001F504 Sync Outcomes with Live Prices", key="db_sync_btn"):
            with st.spinner("Checking historical bars from entry time to determine exits..."):
                sync_signals_db_live()
                st.success("Outcomes synchronized.")
                st.rerun()

        filtered_db = log_db.copy()
        if db_sym:
            filtered_db = filtered_db[filtered_db['Ticker'].str.contains(db_sym.upper())]
        if db_sig != "All Trades":
            filtered_db = filtered_db[filtered_db['Signal Type'].str.contains(db_sig)]

        st.subheader("\U0001F4CB Signal Ledger")
        st.dataframe(filtered_db, use_container_width=True)

        if not filtered_db.empty and 'Outcome' in filtered_db.columns:
            hits = filtered_db['Outcome'].astype(str).str.contains('Target Hit').sum()
            misses = filtered_db['Outcome'].astype(str).str.contains('Stop-Loss Hit').sum()
            resolved = hits + misses
            if resolved > 0:
                st.caption(f"Resolved so far: {hits} target hits, {misses} stop-loss hits -> realised win rate {hits/resolved*100:.1f}% across {resolved} closed signals.")

        col_down, col_clr = st.columns(2)
        with col_down:
            csv_data = filtered_db.to_csv(index=False).encode('utf-8')
            st.download_button("\U0001F4E5 Download Ledger (CSV)", data=csv_data, file_name="locked_trading_signals.csv", mime="text/csv")
        with col_clr:
            if st.button("\U0001F5D1\uFE0F Clear Local Database", key="db_clear_btn"):
                if os.path.exists(SIGNALS_FILE):
                    os.remove(SIGNALS_FILE)
                    st.success("Ledger cleared.")
                    st.rerun()
    else:
        st.info("Ledger is empty. Run a scan in the EOD or Live tab to populate it.")
