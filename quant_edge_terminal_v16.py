import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
import plotly.graph_objects as go
from datetime import datetime, timedelta, time, timezone, date
import os
import json

try:
    from streamlit_autorefresh import st_autorefresh
    AUTOREFRESH_AVAILABLE = True
except ImportError:
    AUTOREFRESH_AVAILABLE = False

try:
    from fyers_apiv3 import fyersModel
    FYERS_SDK_AVAILABLE = True
except ImportError:
    FYERS_SDK_AVAILABLE = False

# =============================================================================
# PAGE CONFIG
# =============================================================================
st.set_page_config(
    page_title="QUANT-EDGE Flagship Trading Terminal v18",
    page_icon="\U0001F451",
    layout="wide",
    initial_sidebar_state="expanded"
)

# =============================================================================
# IST TIMEZONE & MARKET HOURS
# =============================================================================
IST = timezone(timedelta(hours=5, minutes=30))

def get_ist_now():
    return datetime.now(IST)

def is_market_hours():
    now = get_ist_now()
    if now.weekday() >= 5:
        return False
    return time(9, 15) <= now.time() <= time(15, 30)

def is_premarket():
    now = get_ist_now()
    if now.weekday() >= 5:
        return False
    return time(9, 0) <= now.time() < time(9, 15)

def get_market_status():
    if is_premarket():
        return "\U0001F7E1 PRE-MARKET", "status-premarket"
    elif is_market_hours():
        return "\U0001F7E2 MARKET OPEN", "status-open"
    else:
        return "\U0001F534 MARKET CLOSED", "status-closed"

# =============================================================================
# PREMIUM GLASSMORPHISM CSS
# =============================================================================
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap');

    .stApp {
        background: linear-gradient(160deg, #0a0b14 0%, #0d1117 40%, #0f0f1a 100%);
        color: #e2e4ef;
        font-family: 'Inter', sans-serif;
    }
    h1, h2, h3, h4 { color: #ffffff !important; font-family: 'Inter', sans-serif; }

    .main-header {
        font-size: 32px; font-weight: 800; letter-spacing: -0.5px;
        background: linear-gradient(135deg, #00d4aa 0%, #0088ff 50%, #6c5ce7 100%);
        -webkit-background-clip: text; -webkit-text-fill-color: transparent;
        margin-bottom: 4px;
    }
    .sub-caption {
        font-size: 12px; color: #6b7294; margin-bottom: 14px;
        text-transform: uppercase; letter-spacing: 0.8px;
    }

    .glass-card {
        background: rgba(17, 24, 39, 0.6);
        backdrop-filter: blur(12px); -webkit-backdrop-filter: blur(12px);
        border: 1px solid rgba(255,255,255,0.06);
        border-radius: 12px; padding: 18px 20px;
        box-shadow: 0 8px 32px rgba(0,0,0,0.3);
        margin-bottom: 14px;
        transition: transform 0.2s ease, box-shadow 0.2s ease;
    }
    .glass-card:hover {
        transform: translateY(-2px);
        box-shadow: 0 12px 40px rgba(0,0,0,0.4);
    }

    .metric-card {
        background: linear-gradient(135deg, rgba(17,24,39,0.8) 0%, rgba(30,37,64,0.6) 100%);
        backdrop-filter: blur(10px);
        border: 1px solid rgba(255,255,255,0.06);
        border-radius: 12px; padding: 18px 20px;
        box-shadow: 0 4px 20px rgba(0,0,0,0.25); margin-bottom: 12px;
    }
    .metric-header {
        font-size: 10px; color: #6b7294; text-transform: uppercase;
        letter-spacing: 1.2px; font-weight: 600;
    }
    .metric-val { font-size: 24px; font-weight: 700; color: #ffffff; margin-top: 4px; }
    .metric-change { font-size: 12px; font-weight: 600; margin-top: 2px; }
    .change-up { color: #00ffaa; }
    .change-down { color: #ff3366; }

    .market-status {
        display: inline-flex; align-items: center; gap: 8px;
        padding: 6px 16px; border-radius: 20px;
        font-size: 11px; font-weight: 700; letter-spacing: 0.8px; text-transform: uppercase;
    }
    .status-open { background: rgba(0,255,170,0.12); color: #00ffaa; border: 1px solid rgba(0,255,170,0.3); }
    .status-closed { background: rgba(255,51,102,0.12); color: #ff3366; border: 1px solid rgba(255,51,102,0.3); }
    .status-premarket { background: rgba(255,170,0,0.12); color: #ffaa00; border: 1px solid rgba(255,170,0,0.3); }

    .regime-banner {
        padding: 12px 20px; border-radius: 10px; font-weight: 700; font-size: 14px;
        margin-bottom: 16px; letter-spacing: 0.5px; text-align: center;
        backdrop-filter: blur(10px);
    }
    .regime-bull { background: rgba(0,255,170,0.1); color: #00ffaa; border: 1px solid rgba(0,255,170,0.25); }
    .regime-bear { background: rgba(255,51,102,0.1); color: #ff3366; border: 1px solid rgba(255,51,102,0.25); }
    .regime-choppy { background: rgba(255,170,0,0.1); color: #ffaa00; border: 1px solid rgba(255,170,0,0.25); }

    .hero-banner {
        background: linear-gradient(135deg, rgba(255,215,0,0.15) 0%, rgba(255,140,0,0.15) 100%);
        border: 1px solid rgba(255,215,0,0.4);
        padding: 16px 20px; border-radius: 10px; font-weight: 700; font-size: 15px;
        margin-bottom: 16px; text-align: center; color: #FFD700;
        box-shadow: 0 4px 20px rgba(255,215,0,0.1);
    }

    .stTabs [data-baseweb="tab-list"] {
        gap: 6px; background: rgba(17,24,39,0.5); padding: 4px 6px; border-radius: 10px;
    }
    .stTabs [data-baseweb="tab"] {
        border-radius: 8px; padding: 10px 20px; font-weight: 600; font-size: 13px;
    }

    .last-updated {
        font-size: 11px; color: #4a5078; text-align: right;
        font-style: italic; margin-top: -8px; margin-bottom: 12px;
    }

    .info-panel {
        background: rgba(0,136,255,0.08); border: 1px solid rgba(0,136,255,0.2);
        border-radius: 8px; padding: 12px 16px; margin-bottom: 14px;
        color: #8bb8e8; font-size: 13px;
    }
</style>
""", unsafe_allow_html=True)

# =============================================================================
# F&O UNIVERSE & SECTOR MAPS
# =============================================================================
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
    "\U0001F3E6 Banking": "BANKBARODA.NS,FEDERALBNK.NS,HDFCBANK.NS,ICICIBANK.NS,KOTAKBANK.NS,SBIN.NS,AXISBANK.NS,AUBANK.NS",
    "\U0001F4BB IT Sector": "COFORGE.NS,HCLTECH.NS,INFY.NS,LTIM.NS,LTTS.NS,MPHASIS.NS,PERSISTENT.NS,TCS.NS,TECHM.NS,WIPRO.NS",
    "\U0001F697 Auto Sector": "ASHOKLEY.NS,BAJAJ-AUTO.NS,EICHERMOT.NS,HEROMOTOCO.NS,M&M.NS,MARUTI.NS,TATAMOTORS.NS,TVSMOTOR.NS",
    "\u26A1 Power & Energy": "ADANIPOWER.NS,BPCL.NS,COALINDIA.NS,JSWENERGY.NS,NTPC.NS,ONGC.NS,POWERGRID.NS,TATAPOWER.NS",
    "\U0001F3D7\uFE0F Infra & Metal": "ACC.NS,AMBUJACEM.NS,GRASIM.NS,JINDALSTEL.NS,JSWSTEEL.NS,LT.NS,TATASTEEL.NS,ULTRACEMCO.NS",
    "\U0001F48A Pharma": "ALKEM.NS,APOLLOHOSP.NS,CIPLA.NS,DIVISLAB.NS,DRREDDY.NS,IPCALAB.NS,LUPIN.NS,SUNPHARMA.NS,ZYDUSLIFE.NS",
    "\U0001F6D2 FMCG & Retail": "ABFRL.NS,BRITANNIA.NS,COLPAL.NS,DABUR.NS,DMART.NS,GODREJCP.NS,HINDUNILVR.NS,ITC.NS,NESTLEIND.NS,TRENT.NS"
}

# =============================================================================
# PERSISTENT HISTORY SYSTEM
# =============================================================================
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
HISTORY_DIR = os.path.join(DATA_DIR, "history")
SIGNALS_FILE = os.path.join(DATA_DIR, "signals_ledger.csv")
os.makedirs(HISTORY_DIR, exist_ok=True)

def save_history(category, data_list, date_str=None):
    if not data_list: return
    if date_str is None: date_str = get_ist_now().strftime('%Y-%m-%d')
    filepath = os.path.join(HISTORY_DIR, f"{category}_{date_str}.json")
    existing = []
    if os.path.exists(filepath):
        try:
            with open(filepath, 'r', encoding='utf-8') as f: existing = json.load(f)
        except Exception: existing = []
    existing_keys = {f"{i.get('Symbol','')}-{i.get('Date/Time', i.get('Scan_Time',''))}" for i in existing}
    for item in data_list:
        k = f"{item.get('Symbol','')}-{item.get('Date/Time', item.get('Scan_Time',''))}"
        if k not in existing_keys: existing.append(item)
    try:
        with open(filepath, 'w', encoding='utf-8') as f: json.dump(existing, f, indent=2, default=str)
    except Exception: pass

# =============================================================================
# DATA FETCHING
# =============================================================================
@st.cache_data(ttl=60)
def fetch_market_indices():
    indices = {"NIFTY 50": "^NSEI", "BANK NIFTY": "^NSEBANK", "SENSEX": "^BSESN"}
    res = {}
    for name, sym in indices.items():
        try:
            df = yf.download(sym, period="2d", interval="1d", progress=False, auto_adjust=True)
            if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
            if not df.empty and len(df) >= 2:
                ltp = float(df['Close'].iloc[-1])
                prev = float(df['Close'].iloc[-2])
                res[name] = {"LTP": ltp, "Change_Val": ltp - prev, "Change_Pct": ((ltp - prev) / prev) * 100}
            elif not df.empty:
                res[name] = {"LTP": float(df['Close'].iloc[-1]), "Change_Val": 0.0, "Change_Pct": 0.0}
        except Exception: res[name] = {"LTP": 0.0, "Change_Val": 0.0, "Change_Pct": 0.0}
    return res

@st.cache_data(ttl=900)
def fetch_stock_data(ticker, period="3y", interval="1d"):
    try:
        data = yf.download(ticker, period=period, interval=interval, progress=False, auto_adjust=True)
        if isinstance(data.columns, pd.MultiIndex): data.columns = data.columns.get_level_values(0)
        return data.dropna(subset=['Close'])
    except Exception: return pd.DataFrame()

@st.cache_data(ttl=900)
def fetch_bulk_stock_data(tickers_tuple, period="1y", interval="1d"):
    tickers = list(tickers_tuple) if isinstance(tickers_tuple, tuple) else tickers_tuple
    try: return yf.download(tickers, period=period, interval=interval, group_by="ticker", progress=False, auto_adjust=True)
    except Exception: return pd.DataFrame()

@st.cache_data(ttl=900)
def fetch_nifty_series(period="2y", interval="1d"):
    return fetch_stock_data("^NSEI", period=period, interval=interval)

@st.cache_data(ttl=30)
def fetch_live_prices(tickers_tuple):
    tickers = list(tickers_tuple)
    if not tickers: return {}
    try:
        df = yf.download(tickers, period="1d", interval="1m", progress=False, auto_adjust=True)
        if df.empty: df = yf.download(tickers, period="2d", interval="1d", progress=False, auto_adjust=True)
        if df.empty: return {}
        prices = {}
        if isinstance(df.columns, pd.MultiIndex):
            for t in tickers:
                try:
                    close = df[t]['Close'].dropna()
                    if not close.empty: prices[t] = float(close.iloc[-1])
                except Exception: pass
        elif len(tickers) == 1:
            cols = df.columns
            if isinstance(cols, pd.MultiIndex): cols = cols.get_level_values(0)
            close = df['Close'].dropna() if 'Close' in cols else pd.Series()
            if not close.empty: prices[tickers[0]] = float(close.iloc[-1])
        return prices
    except Exception: return {}

def get_symbol_df(bulk_df, ticker, fallback_period, fallback_interval, min_len=50):
    df = pd.DataFrame()
    if not bulk_df.empty:
        try:
            if isinstance(bulk_df.columns, pd.MultiIndex):
                if ticker in bulk_df.columns.get_level_values(0): df = bulk_df[ticker].dropna(how='all')
            else: df = bulk_df.dropna(how='all')
        except Exception: pass
    if df.empty or len(df) < min_len:
        df = fetch_stock_data(ticker, period=fallback_period, interval=fallback_interval)
    return df

# =============================================================================
# ENHANCED INDICATOR ENGINE (Now includes VWAP for context)
# =============================================================================
def compute_rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0); loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1.0/period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0/period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def compute_macd(series, fast=12, slow=26, signal=9):
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return macd_line, signal_line, macd_line - signal_line

def compute_supertrend(df, period=10, multiplier=2.0):
    if df.empty or len(df) < period + 1:
        df['Supertrend'] = np.nan; df['ST_Direction'] = 0
        return df
    high = df['High'].values.astype(float); low = df['Low'].values.astype(float); close = df['Close'].values.astype(float)
    prev_close = np.roll(close, 1); prev_close[0] = close[0]
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))
    atr = pd.Series(tr).ewm(span=period, adjust=False).mean().values
    hl2 = (high + low) / 2.0; upper = hl2 + multiplier * atr; lower = hl2 - multiplier * atr
    direction = np.ones(len(df), dtype=int); supertrend = np.full(len(df), np.nan)
    for i in range(1, len(df)):
        if lower[i] < lower[i-1] and close[i-1] > lower[i-1]: lower[i] = lower[i-1]
        if upper[i] > upper[i-1] and close[i-1] < upper[i-1]: upper[i] = upper[i-1]
        if close[i] > upper[i]: direction[i] = 1
        elif close[i] < lower[i]: direction[i] = -1
        else: direction[i] = direction[i-1]
        supertrend[i] = lower[i] if direction[i] == 1 else upper[i]
    df['Supertrend'] = supertrend; df['ST_Direction'] = direction
    return df

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

def compute_all_indicators(df, is_intraday=False):
    if df.empty: return df
    df = df.copy()
    if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
    if len(df) < 50: return df
    df['EMA_20'] = df['Close'].ewm(span=20, adjust=False).mean()
    df['EMA_50'] = df['Close'].ewm(span=50, adjust=False).mean()
    df = add_atr(df)
    df['RSI_14'] = compute_rsi(df['Close'], 14)
    df['MACD'], df['MACD_Signal'], df['MACD_Hist'] = compute_macd(df['Close'])
    df = compute_supertrend(df, period=10, multiplier=2.0)
    df['Vol_MA_20'] = df['Volume'].rolling(20).mean()
    df['Inst_Vol_Spike'] = df['Volume'] > (df['Vol_MA_20'] * 1.5)

    if is_intraday:
        df['Date_Group'] = df.index.date
        typical = (df['High'] + df['Low'] + df['Close']) / 3.0
        df['TP_Vol'] = typical * df['Volume']
        df['Cum_TP_Vol'] = df.groupby('Date_Group')['TP_Vol'].transform(pd.Series.cumsum)
        df['Cum_Vol'] = df.groupby('Date_Group')['Volume'].transform(pd.Series.cumsum)
        df['VWAP'] = df['Cum_TP_Vol'] / df['Cum_Vol'].replace(0, np.nan)
        df.drop(columns=['Date_Group', 'TP_Vol', 'Cum_TP_Vol', 'Cum_Vol'], inplace=True, errors='ignore')
    else:
        df['VWAP'] = df['EMA_20']
    return df

# =============================================================================
# PSYCHOLOGICAL & INSTITUTIONAL CONTEXT ENGINE
# =============================================================================
def compute_psychological_profile(df, daily_direction=None, is_intraday=True):
    """
    Reads market psychology based on human behavior.
    Uses daily macro trend + intraday VWAP + volume exhaustion/expansion.
    """
    df = df.copy()
    df['Psychology'] = "Neutral"

    if 'VWAP' not in df.columns or 'Vol_MA_20' not in df.columns or df.empty:
        return df

    for idx, row in df.iterrows():
        close = row['Close']
        vwap = row['VWAP']
        vol = row['Volume']
        avg_vol = row['Vol_MA_20']
        
        if pd.isna(vwap) or pd.isna(avg_vol) or avg_vol == 0:
            continue

        rel_vol = vol / avg_vol
        
        # Macro trend context
        macro_up = (daily_direction == 1) if daily_direction is not None else (row['EMA_20'] > row['EMA_50'])
        macro_down = (daily_direction == -1) if daily_direction is not None else (row['EMA_20'] < row['EMA_50'])

        if macro_up:
            if close < vwap:
                if rel_vol < 0.8: df.at[idx, 'Psychology'] = "🟢 Discount Buy (Exhausted Sellers)"
                elif rel_vol > 2.0: df.at[idx, 'Psychology'] = "🔴 Trapped Bulls (Avoid Long, Insti Dump)"
                else: df.at[idx, 'Psychology'] = "🟡 Dip Buying Zone"
            else:
                if rel_vol > 2.0: df.at[idx, 'Psychology'] = "🟢 Momentum Ignition (Insti Buying)"
                else: df.at[idx, 'Psychology'] = "🟢 Trend Continuation"
        elif macro_down:
            if close > vwap:
                if rel_vol < 0.8: df.at[idx, 'Psychology'] = "🔴 Premium Short (Exhausted Buyers)"
                elif rel_vol > 2.0: df.at[idx, 'Psychology'] = "🟢 Bear Trap (Avoid Short, Squeeze)"
                else: df.at[idx, 'Psychology'] = "🟡 Rally Selling Zone"
            else:
                if rel_vol > 2.0: df.at[idx, 'Psychology'] = "🔴 Momentum Ignition (Insti Selling)"
                else: df.at[idx, 'Psychology'] = "🔴 Trend Continuation"

    return df

# =============================================================================
# ONE-WAY RUNNER (MOMENTUM) ENGINE
# =============================================================================
def compute_one_way_runner(df, is_intraday=True):
    """
    Finds stocks that break the opening/early range on massive volume and NEVER retrace past VWAP/20EMA.
    """
    df = df.copy()
    df['One_Way_Runner'] = False
    df['Runner_Type'] = ""

    if df.empty or len(df) < 5 or 'VWAP' not in df.columns:
        return df

    if is_intraday:
        df['Date_Group'] = df.index.date
        first_candle_idx = df.groupby('Date_Group').head(1).index
        
        for idx in first_candle_idx:
            try:
                date_g = df.loc[idx, 'Date_Group']
                day_data = df[df['Date_Group'] == date_g]
                if len(day_data) < 3: continue

                first_bar = day_data.iloc[0]
                avg_vol = first_bar.get('Vol_MA_20', 1)
                if pd.isna(avg_vol) or avg_vol == 0: continue
                
                rvol_open = first_bar['Volume'] / avg_vol
                prev_close = day_data['Close'].shift(1).get(day_data.index[0], first_bar['Open'])
                gap_pct = ((first_bar['Open'] - prev_close) / prev_close) * 100
                
                # Check for explosive open
                if rvol_open > 2.5 or abs(gap_pct) > 0.5:
                    is_bull_runner = True
                    is_bear_runner = True
                    
                    for _, row in day_data.iterrows():
                        if row['Low'] < row['VWAP'] or row['Close'] < row['EMA_20']:
                            is_bull_runner = False
                        if row['High'] > row['VWAP'] or row['Close'] > row['EMA_20']:
                            is_bear_runner = False
                            
                    if is_bull_runner and day_data.iloc[-1]['Close'] > first_bar['High']:
                        df.loc[day_data.index[-1], 'One_Way_Runner'] = True
                        df.loc[day_data.index[-1], 'Runner_Type'] = "⚡ ONE-WAY BULL RUNNER"
                    elif is_bear_runner and day_data.iloc[-1]['Close'] < first_bar['Low']:
                        df.loc[day_data.index[-1], 'One_Way_Runner'] = True
                        df.loc[day_data.index[-1], 'Runner_Type'] = "⚡ ONE-WAY BEAR RUNNER"
            except Exception:
                pass
        df.drop(columns=['Date_Group'], inplace=True)
    else:
        # For swing, check last N candles
        lookback = 5
        recent = df.iloc[-lookback:]
        if len(recent) == lookback:
            start_bar = recent.iloc[0]
            rvol = start_bar['Volume'] / start_bar['Vol_MA_20'] if start_bar['Vol_MA_20'] else 1
            if rvol > 2.0:
                bull = all(row['Close'] > row['EMA_20'] for _, row in recent.iterrows()) and recent.iloc[-1]['Close'] > start_bar['High']
                bear = all(row['Close'] < row['EMA_20'] for _, row in recent.iterrows()) and recent.iloc[-1]['Close'] < start_bar['Low']
                if bull:
                    df.loc[df.index[-1], 'One_Way_Runner'] = True
                    df.loc[df.index[-1], 'Runner_Type'] = "⚡ ONE-WAY BULL SWING"
                elif bear:
                    df.loc[df.index[-1], 'One_Way_Runner'] = True
                    df.loc[df.index[-1], 'Runner_Type'] = "⚡ ONE-WAY BEAR SWING"

    return df

# =============================================================================
# MARKET REGIME, TREND CONSISTENCY, CONFLUENCE SCORING
# =============================================================================
def get_market_regime(nifty_df):
    if nifty_df.empty or len(nifty_df) < 55: return "UNKNOWN"
    nd = compute_all_indicators(nifty_df, is_intraday=False)
    if nd.empty or 'EMA_20' not in nd.columns: return "UNKNOWN"
    last = nd.iloc[-1]
    if pd.isna(last.get('EMA_20')) or pd.isna(last.get('EMA_50')): return "UNKNOWN"
    if last['Close'] > last['EMA_20'] > last['EMA_50']: return "BULL"
    elif last['Close'] < last['EMA_20'] < last['EMA_50']: return "BEAR"
    return "CHOPPY"

def get_nifty_rs_series(nifty_df, window=21):
    if nifty_df.empty or len(nifty_df) < window + 1: return None
    return nifty_df['Close'].pct_change(window) * 100.0

@st.cache_data(ttl=1800)
def compute_daily_supertrend_directions(tickers_tuple):
    tickers = list(tickers_tuple)
    df_bulk = fetch_bulk_stock_data(tuple(tickers), period="6mo", interval="1d")
    directions = {}
    for ticker in tickers:
        dfs = get_symbol_df(df_bulk, ticker, "6mo", "1d", min_len=30)
        if dfs.empty or len(dfs) < 30:
            directions[ticker] = 0; continue
        dfs = compute_supertrend(dfs.copy(), period=10, multiplier=2.0)
        last_dir = dfs['ST_Direction'].dropna()
        directions[ticker] = int(last_dir.iloc[-1]) if not last_dir.empty else 0
    return directions

def compute_confluence_score(df, nifty_rs_series=None, is_intraday=False, daily_direction=None, window=21):
    df = df.copy()
    if 'EMA_20' not in df.columns or 'EMA_50' not in df.columns or 'ATR14' not in df.columns:
        df['Confluence_Score'] = np.nan; return df

    trend_up = (df['Close'] > df['EMA_20']) & (df['EMA_20'] > df['EMA_50'])
    trend_down = (df['Close'] < df['EMA_20']) & (df['EMA_20'] < df['EMA_50'])
    trend_score = np.where(trend_up | trend_down, 20, 5)

    if 'ST_Direction' in df.columns:
        st_up = trend_up & (df['ST_Direction'] == 1)
        st_down = trend_down & (df['ST_Direction'] == -1)
        st_score = np.where(st_up | st_down, 15, np.where(trend_up | trend_down, 5, 0))
    else: st_score = np.full(len(df), 5.0)

    if 'RSI_14' in df.columns:
        rsi = df['RSI_14'].fillna(50)
        rsi_score = np.where((rsi >= 30) & (rsi <= 70), 15, np.where((rsi >= 20) & (rsi <= 80), 8, 3))
    else: rsi_score = np.full(len(df), 8.0)

    if 'MACD_Hist' in df.columns:
        macd_up = trend_up & (df['MACD_Hist'] > 0)
        macd_down = trend_down & (df['MACD_Hist'] < 0)
        macd_score = np.where(macd_up | macd_down, 10, 2)
    else: macd_score = np.full(len(df), 4.0)

    if 'Vol_MA_20' in df.columns:
        vol_ratio = (df['Volume'] / df['Vol_MA_20'].replace(0, np.nan)).clip(upper=3.0).fillna(0)
        volume_score = (vol_ratio / 3.0 * 15).clip(0, 15)
    else: volume_score = np.full(len(df), 5.0)

    if nifty_rs_series is not None:
        stock_ret = df['Close'].pct_change(window) * 100.0
        rs = (stock_ret - nifty_rs_series.reindex(df.index).ffill()).clip(-10, 10).fillna(0)
        rs_score = ((rs + 10) / 20 * 10).clip(0, 10)
    else: rs_score = np.full(len(df), 5.0)

    atr_safe = df['ATR14'].replace(0, np.nan).fillna(1)
    if is_intraday and 'VWAP' in df.columns:
        vwap_dist = ((df['Close'] - df['VWAP']).abs() / atr_safe).clip(upper=3.0).fillna(3.0)
        proximity_score = ((3.0 - vwap_dist) / 3.0 * 10).clip(0, 10)
    else:
        dist_atr = ((df['Close'] - df['EMA_20']).abs() / atr_safe).clip(upper=3.0).fillna(3.0)
        proximity_score = ((3.0 - dist_atr) / 3.0 * 10).clip(0, 10)

    if daily_direction is not None and is_intraday and daily_direction != 0:
        if daily_direction == 1: mtf_score = np.where(trend_up, 5, np.where(trend_down, -10, 0)).astype(float)
        else: mtf_score = np.where(trend_down, 5, np.where(trend_up, -10, 0)).astype(float)
    else: mtf_score = np.zeros(len(df))

    total = trend_score + st_score + rsi_score + macd_score + volume_score + rs_score + proximity_score + mtf_score
    df['Confluence_Score'] = pd.Series(total, index=df.index).clip(0, 100)
    return df

# =============================================================================
# SIGNAL GENERATION
# =============================================================================
def generate_signals_for_asset(df, lookback=10, is_intraday=False, rr=2.0, use_orb=False):
    if df.empty or len(df) < max(50, lookback) + 5:
        for c in ['Bullish_Sweep', 'Bearish_Sweep', 'Bullish_ORB', 'Bearish_ORB']: df[c] = False
        for c in ['Entry_Trigger', 'Stop_Loss', 'Take_Profit']: df[c] = np.nan
        return df
    df = compute_all_indicators(df, is_intraday=is_intraday)
    if df.empty or 'EMA_50' not in df.columns: return df
    
    df['Prev_Low_L'] = df['Low'].shift(1).rolling(lookback).min()
    df['Prev_High_H'] = df['High'].shift(1).rolling(lookback).max()
    df['Bullish_Sweep'] = (df['Close'] > df['EMA_50']) & (df['Low'] < df['Prev_Low_L']) & (df['Close'] > df['Prev_Low_L']) & (df['Volume'] > df['Vol_MA_20'])
    df['Bearish_Sweep'] = (df['Close'] < df['EMA_50']) & (df['High'] > df['Prev_High_H']) & (df['Close'] < df['Prev_High_H']) & (df['Volume'] > df['Vol_MA_20'])
    
    df['Bullish_ORB'] = False; df['Bearish_ORB'] = False
    if use_orb and is_intraday:
        df['Date_Group'] = df.index.date
        df['ORB_High'] = df.groupby('Date_Group')['High'].transform('first')
        df['ORB_Low'] = df.groupby('Date_Group')['Low'].transform('first')
        df['Bullish_ORB'] = (df.groupby('Date_Group').cumcount() > 0) & (df['Close'] > df['ORB_High']) & (df['Close'].shift(1) <= df['ORB_High'])
        df['Bearish_ORB'] = (df.groupby('Date_Group').cumcount() > 0) & (df['Close'] < df['ORB_Low']) & (df['Close'].shift(1) >= df['ORB_Low'])
        df.drop(columns=['Date_Group', 'ORB_High', 'ORB_Low'], errors='ignore', inplace=True)
        
    buy_mask = df['Bullish_Sweep'] | df['Bullish_ORB']
    sell_mask = df['Bearish_Sweep'] | df['Bearish_ORB']
    df['Entry_Trigger'] = np.nan; df['Stop_Loss'] = np.nan; df['Take_Profit'] = np.nan
    
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

# =============================================================================
# POSITION SIZING
# =============================================================================
def position_size(capital, risk_pct, entry, stop):
    risk_amount = capital * (risk_pct / 100.0)
    per_share_risk = abs(entry - stop)
    if per_share_risk <= 0 or capital <= 0: return 0, 0.0
    qty = int(risk_amount // per_share_risk)
    return qty, qty * entry

# =============================================================================
# HELPERS
# =============================================================================
def get_signal_status(live_price, entry, sl, tp, is_bullish):
    if pd.isna(live_price): return "\u23F3 Loading...", "#6b7294"
    if is_bullish:
        if live_price >= tp: return "\u2705 TARGET HIT", "#00ffaa"
        elif live_price <= sl: return "\u274C SL HIT", "#ff3366"
        elif live_price >= entry: return "\U0001F7E2 TRIGGERED", "#00d4aa"
        else: return "\u23F3 PENDING", "#ffaa00"
    else:
        if live_price <= tp: return "\u2705 TARGET HIT", "#00ffaa"
        elif live_price >= sl: return "\u274C SL HIT", "#ff3366"
        elif live_price <= entry: return "\U0001F534 TRIGGERED", "#ff6b6b"
        else: return "\u23F3 PENDING", "#ffaa00"

# =============================================================================
# SHARED SCANNER (Enhanced with Psychology & One-Way Logic)
# =============================================================================
def run_universe_scan(tickers, period, interval, is_intraday, use_orb, rr,
                      min_score, nifty_rs, capital, risk_pct, regime_fn,
                      daily_directions=None, progress_bar=None, source_tag="SCAN", scan_mode="ALL"):
    """
    scan_mode: "ALL", "RUNNERS_ONLY", "TOP5_ONLY", "EOD_PLAYBOOK"
    """
    rows, persist = [], []
    total = len(tickers)
    df_bulk = fetch_bulk_stock_data(tuple(tickers), period=period, interval=interval)
    
    for i, ticker in enumerate(tickers):
        if progress_bar: progress_bar.progress((i + 1) / total)
        df = get_symbol_df(df_bulk, ticker, period, interval, min_len=20 if is_intraday else 50)
        if df.empty or len(df) < (20 if is_intraday else 50): continue
        
        df = generate_signals_for_asset(df, lookback=10, is_intraday=is_intraday, rr=rr, use_orb=use_orb)
        if df.empty: continue
        
        dd = daily_directions.get(ticker, 0) if daily_directions else None
        df = compute_confluence_score(df, nifty_rs, is_intraday=is_intraday, daily_direction=dd)
        df = compute_psychological_profile(df, daily_direction=dd, is_intraday=is_intraday)
        df = compute_one_way_runner(df, is_intraday=is_intraday)
        
        if 'Confluence_Score' not in df.columns: continue
        
        latest = df.iloc[-1]
        bull = bool(latest.get('Bullish_Sweep', False)) or bool(latest.get('Bullish_ORB', False))
        bear = bool(latest.get('Bearish_Sweep', False)) or bool(latest.get('Bearish_ORB', False))
        is_runner = bool(latest.get('One_Way_Runner', False))
        
        if scan_mode == "RUNNERS_ONLY" and not is_runner: continue
        if scan_mode == "EOD_PLAYBOOK" or scan_mode == "TOP5_ONLY":
            # For EOD playbook and Top 5, we want the highest confluence setups.
            # We don't require an active sweep right on the current candle.
            pass
        else:
            # For ALL live scans, require an active trigger
            if not (bull or bear) and not is_runner: continue
            
        if not passes_regime_filter(bull if bull else not bear): continue
        
        score = latest.get('Confluence_Score', 0)
        if pd.isna(score) or score < min_score: continue
        
        trig = latest['Entry_Trigger']; sl_v = latest['Stop_Loss']; tp_v = latest['Take_Profit']
        # If no active trigger, create a hypothetical one based on ATR for the table
        if pd.isna(trig) or pd.isna(sl_v):
            atr_val = latest.get('ATR14', latest['Close'] * 0.02)
            is_uptrend = latest.get('EMA_20', 0) > latest.get('EMA_50', 0)
            if is_uptrend:
                trig = latest['Close']
                sl_v = trig - atr_val
                tp_v = trig + (atr_val * rr)
            else:
                trig = latest['Close']
                sl_v = trig + atr_val
                tp_v = trig - (atr_val * rr)
        
        qty, dep = position_size(capital, risk_pct, trig, sl_v)
        sig_time = latest.name.strftime('%Y-%m-%d %H:%M') if hasattr(latest.name, 'strftime') else str(latest.name)
        
        # Determine setup label
        if is_runner and scan_mode == "RUNNERS_ONLY":
            setup_label = latest.get('Runner_Type', 'RUNNER')
        elif bull:
            setup_label = 'BUY (Sweep/ORB)'
        elif bear:
            setup_label = 'SELL (Sweep/ORB)'
        else:
            setup_label = 'BUY (Trend)' if latest.get('EMA_20', 0) > latest.get('EMA_50', 0) else 'SELL (Trend)'

        rows.append({
            'Symbol': ticker.replace('.NS', ''), 'Signal': setup_label,
            'Psychology': latest.get('Psychology', 'Neutral'),
            'Score': int(round(score)), 'LTP_at_scan': float(latest['Close']),
            'Entry_Trigger': round(float(trig), 2), 'Stop_Loss': round(float(sl_v), 2),
            'Take_Profit': round(float(tp_v), 2), 'Qty': qty, 'RR': rr,
            'RSI': round(float(latest.get('RSI_14', 0)), 1) if not pd.isna(latest.get('RSI_14')) else 0,
            'MACD_H': round(float(latest.get('MACD_Hist', 0)), 2) if not pd.isna(latest.get('MACD_Hist')) else 0,
            'ST_Dir': int(latest.get('ST_Direction', 0)),
            'Scan_Time': sig_time, 'Capital_Used': round(dep, 0), 'Date/Time': sig_time
        })
        
        persist.append({
            'Date/Time': sig_time, 'Ticker': ticker.replace('.NS', ''),
            'Signal Type': setup_label, 'Entry Trigger': round(float(trig), 2), 
            'Stop Loss': round(float(sl_v), 2), 'Take Profit': round(float(tp_v), 2), 
            'Score': int(round(score)), 'Volume': int(latest['Volume']), 'R:R Ratio': f"1:{rr:.1f}",
            'Outcome': '\U0001F535 Active', 'Hit Date/Time': '-', 'Source': source_tag
        })
        
    if scan_mode == "TOP5_ONLY" or scan_mode == "EOD_PLAYBOOK":
        rows = sorted(rows, key=lambda x: x['Score'], reverse=True)[:5]
        symbols_kept = {r['Symbol'] for r in rows}
        persist = [p for p in persist if p['Ticker'] in symbols_kept]

    return rows, persist

# =============================================================================
# TODAY'S HERO PRE-MARKET SCAN (Finds absolute best mover)
# =============================================================================
def scan_todays_hero(tickers):
    """Scans pre-market data to find the single best BUY and SELL candidate."""
    if not SESSION_DATA_LIVE_TODAY:
        return None, None
        
    dbpm = fetch_bulk_stock_data(tuple(tickers), period="5d", interval="1d")
    candidates = []
    
    for t in tickers:
        d = get_symbol_df(dbpm, t, "5d", "1d", 2)
        if d.empty or len(d) < 2: continue
        yc = float(d['Close'].iloc[-2]); to = float(d['Open'].iloc[-1]); tv = float(d['Volume'].iloc[-1])
        gp = ((to-yc)/yc)*100
        mc = (to*tv)/1e7  # Money in Cr
        candidates.append({'Symbol': t.replace('.NS',''), 'Gap': gp, 'Money': mc, 'LTP': to})
        
    if not candidates: return None, None
    
    # Hero Long: Highest Money Cr among Gap UP > 1%
    longs = [c for c in candidates if c['Gap'] > 1.0]
    hero_long = max(longs, key=lambda x: x['Money']) if longs else None
    
    # Hero Short: Highest Money Cr among Gap DOWN < -1%
    shorts = [c for c in candidates if c['Gap'] < -1.0]
    hero_short = max(shorts, key=lambda x: x['Money']) if shorts else None
    
    return hero_long, hero_short

# =============================================================================
# SESSION STATE
# =============================================================================
for key in ['intraday_scan_results', 'intraday_eod_results', 'intraday_runners', 
            'swing_eod_results', 'swing_scan_results', 'swing_runners', 
            'fyers_access_token']:
    if key not in st.session_state:
        st.session_state[key] = None

# =============================================================================
# GLOBAL DATA
# =============================================================================
market_is_live = is_market_hours()
indices_data = fetch_market_indices()
nifty_daily = fetch_nifty_series(period="2y", interval="1d")
market_regime = get_market_regime(nifty_daily)
nifty_rs_series = get_nifty_rs_series(nifty_daily)

_today_ist = get_ist_now().date()
_latest_nifty_date = None
if not nifty_daily.empty:
    _li = nifty_daily.index[-1]
    try: _latest_nifty_date = _li.tz_localize(None).date() if _li.tzinfo else _li.date()
    except Exception: pass
SESSION_DATA_LIVE_TODAY = (_latest_nifty_date == _today_ist)

# =============================================================================
# HEADER + INDEX STRIP
# =============================================================================
col_hl, col_hr = st.columns([4, 1])
with col_hl:
    st.markdown("<div class='main-header'>\U0001F451 QUANT-EDGE FLAGSHIP TERMINAL v18</div>", unsafe_allow_html=True)
    st.markdown("<div class='sub-caption'>Institutional Psychology \u2022 VWAP Context \u2022 Momentum Runners \u2022 Confluence Scoring</div>", unsafe_allow_html=True)
with col_hr:
    ms, mc = get_market_status()
    st.markdown(f"<div class='market-status {mc}'>{ms}</div>", unsafe_allow_html=True)
    st.markdown(f"<div class='last-updated'>{get_ist_now().strftime('%I:%M %p IST')}</div>", unsafe_allow_html=True)

# TODAY'S HERO BANNER
hero_long, hero_short = scan_todays_hero(FO_UNIVERSE)
if hero_long or hero_short:
    hero_text = "🏆 **TODAY'S INSTITUTIONAL HEROES (PRE-MARKET)** 🏆<br/>"
    if hero_long: hero_text += f"🟢 **BULL HERO:** {hero_long['Symbol']} (+{hero_long['Gap']:.1f}%, ₹{hero_long['Money']:.0f} Cr) "
    if hero_short: hero_text += f"| 🔴 **BEAR HERO:** {hero_short['Symbol']} ({hero_short['Gap']:.1f}%, ₹{hero_short['Money']:.0f} Cr)"
    st.markdown(f"<div class='hero-banner'>{hero_text}</div>", unsafe_allow_html=True)

rc = {"BULL": "regime-bull", "BEAR": "regime-bear", "CHOPPY": "regime-choppy"}.get(market_regime, "regime-choppy")
rm = {
    "BULL": "\U0001F7E2 REGIME: BULL \u2014 Nifty > EMA + Supertrend UP. Institutions buying dips.",
    "BEAR": "\U0001F534 REGIME: BEAR \u2014 Nifty < EMA + Supertrend DOWN. Institutions selling rallies.",
    "CHOPPY": "\U0001F7E1 REGIME: CHOPPY \u2014 Trend unclear. Retail trapped. Wait for momentum."
}.get(market_regime, "")
st.markdown(f"<div class='regime-banner {rc}'>{rm}</div>", unsafe_allow_html=True)

c1, c2, c3 = st.columns(3)
for i, (name, m) in enumerate(indices_data.items()):
    col = [c1, c2, c3][i]
    cs = "change-up" if m["Change_Val"] >= 0 else "change-down"
    sign = "+" if m["Change_Val"] >= 0 else ""
    with col:
        st.markdown(f"""<div class="metric-card">
            <div class="metric-header">{name}</div>
            <div class="metric-val">\u20B9{m['LTP']:,.2f}</div>
            <div class="metric-change {cs}">{sign}{m['Change_Val']:,.2f} ({sign}{m['Change_Pct']:.2f}%)</div>
        </div>""", unsafe_allow_html=True)

# =============================================================================
# SIDEBAR
# =============================================================================
with st.sidebar:
    st.markdown("<div class='main-header' style='font-size:20px;'>\u2699\uFE0F Control Panel</div>", unsafe_allow_html=True)
    with st.expander("\U0001F30D Universe", expanded=True):
        universe_sel = st.selectbox("Stock Universe", ["Full NSE F&O (219)", "Custom Watchlist"] + list(SECTOR_MAP.keys()), key="u_sel")
        if universe_sel == "Full NSE F&O (219)": active_tickers = FO_UNIVERSE
        elif universe_sel == "Custom Watchlist":
            ci = st.text_area("Tickers:", value="TATAMOTORS.NS,RELIANCE.NS", key="ct")
            active_tickers = [t.strip().upper() for t in ci.split(",") if t.strip()]
        else: active_tickers = [t.strip() for t in SECTOR_MAP[universe_sel].split(",")]
        st.caption(f"{len(active_tickers)} symbols")
    with st.expander("\U0001F4B0 Capital & Risk", expanded=True):
        capital_base = st.number_input("Capital (\u20B9)", min_value=10000, value=200000, step=10000, key="cap")
        risk_per_trade = st.slider("Risk/Trade (%)", 0.25, 3.0, 1.0, 0.25, key="rpt")
    with st.expander("\U0001F4CA Intraday Settings", expanded=False):
        itf = st.selectbox("Timeframe", ["5-Minute", "15-Minute", "1-Minute"], key="itf")
        itf_map = {"5-Minute": ("5m", "30d"), "15-Minute": ("15m", "60d"), "1-Minute": ("1m", "7d")}
        intra_interval, intra_period = itf_map[itf]
        intra_rr = st.slider("R:R", 1.0, 5.0, 2.0, 0.5, key="irr")
        intra_min_score = st.slider("Min Score", 0, 100, 55, 5, key="ims")
    with st.expander("\U0001F4C8 Swing Settings", expanded=False):
        stf = st.selectbox("Timeframe", ["1-Day (Daily)", "1-Hour", "1-Week (Weekly)"], key="stf")
        stf_map = {"1-Day (Daily)": ("1d", "3y"), "1-Hour": ("60m", "2y"), "1-Week (Weekly)": ("1wk", "5y")}
        swing_interval, swing_period = stf_map[stf]
        swing_rr = st.slider("R:R", 1.0, 5.0, 2.0, 0.5, key="srr")
        swing_min_score = st.slider("Min Score", 0, 100, 60, 5, key="sms")
    with st.expander("\U0001F6E1\uFE0F Filters", expanded=False):
        apply_regime_filter = st.checkbox("Regime Filter", value=True, key="rf")
    with st.expander("\U0001F504 Auto-Refresh", expanded=False):
        ref = st.selectbox("Interval", ["Manual", "30s", "60s", "2min"], key="ro")
        if ref != "Manual" and AUTOREFRESH_AVAILABLE:
            rs = {"30s": 30, "60s": 60, "2min": 120}[ref]
            if market_is_live:
                st_autorefresh(interval=rs * 1000, key="ar")
                st.caption(f"\u2705 Every {rs}s")

def passes_regime_filter(is_bull):
    if not apply_regime_filter: return True
    if market_regime in ("CHOPPY", "UNKNOWN"): return True  # Allow both sides if choppy
    if market_regime == "BULL" and not is_bull: return False
    if market_regime == "BEAR" and is_bull: return False
    return True

# =============================================================================
# DISPLAY HELPERS
# =============================================================================
def display_scan_table(results, fetch_live=True):
    if not results:
        st.info("No signals found."); return
    df = pd.DataFrame(results)
    if fetch_live and market_is_live and len(results) <= 50:
        tks = tuple(r['Symbol'] + '.NS' for r in results)
        lp = fetch_live_prices(tks)
        df['Live_Price'] = df['Symbol'].apply(lambda s: lp.get(s + '.NS', np.nan))
        df['Status'] = df.apply(lambda r: get_signal_status(
            r['Live_Price'], r['Entry_Trigger'], r['Stop_Loss'], r['Take_Profit'], 'BUY' in str(r['Signal']) or 'BULL' in str(r['Signal'])
        )[0] if not pd.isna(r.get('Live_Price', np.nan)) else '\u23F3 Pending', axis=1)
    else:
        df['Live_Price'] = df['LTP_at_scan']; df['Status'] = '\u23F3 Pending'
        
    disp = pd.DataFrame({
        'Symbol': df['Symbol'],
        'Setup': df['Signal'].apply(lambda s: f"{'🟢' if 'BUY' in s or 'BULL' in s else '🔴'} {s}"),
        'Psychology': df['Psychology'],
        'Score': df['Score'],
        'Live \u20B9': df['Live_Price'].apply(lambda x: f"\u20B9{x:,.2f}" if not pd.isna(x) else "-"),
        'Entry': df['Entry_Trigger'].apply(lambda x: f"\u20B9{x:,.2f}"),
        'SL': df['Stop_Loss'].apply(lambda x: f"\u20B9{x:,.2f}"),
        'Target': df['Take_Profit'].apply(lambda x: f"\u20B9{x:,.2f}"),
        'Status': df['Status'], 'Qty': df['Qty'],
    }).sort_values('Score', ascending=False).reset_index(drop=True)
    st.dataframe(disp, use_container_width=True, height=min(400, 35 * len(disp) + 38))

# =============================================================================
# MAIN TABS
# =============================================================================
tab_intraday, tab_swing, tab_tools = st.tabs([
    "\U0001F4CA INTRADAY COMMAND", "\U0001F4C8 SWING DESK", "\U0001F6E0\uFE0F TOOLS"
])

# === INTRADAY ===
with tab_intraday:
    isub = st.tabs(["🧠 Psychological Scanner", "📅 Intraday EOD Playbook", "⚡ One-Way Runners"])

    with isub[0]:
        st.markdown("### 🧠 Intraday Psychological Scanner")
        st.markdown(f"<div class='info-panel'>Reads Institutional Traps & Discounts based on VWAP + Volume on <b>{itf}</b>.</div>", unsafe_allow_html=True)
        top5_tog = st.checkbox("Strict Mode: Top 5 Only", value=True, key="strict_i")
        
        if st.button("\U0001F680 Run Psychological Scan", key="isb", type="primary"):
            prog = st.progress(0.0)
            mode = "TOP5_ONLY" if top5_tog else "ALL"
            with st.spinner(f"Reading Market Psychology for {len(active_tickers)} assets..."):
                dd = compute_daily_supertrend_directions(tuple(active_tickers))
                rows, pers = run_universe_scan(active_tickers, intra_period, intra_interval, True, True, intra_rr,
                    intra_min_score, None, capital_base, risk_per_trade, passes_regime_filter, dd, prog, "INTRADAY_LIVE", mode)
            prog.empty(); st.session_state['intraday_scan_results'] = rows
            
        if st.session_state['intraday_scan_results'] is not None:
            display_scan_table(st.session_state['intraday_scan_results'])

    with isub[1]:
        st.markdown("### 📅 Intraday EOD Playbook (For Tomorrow)")
        st.markdown("<div class='info-panel'>Scans DAILY charts after 3:30 PM to find strictly curated Top 5 stocks Coiled for Intraday moves tomorrow.</div>", unsafe_allow_html=True)
        if st.button("\U0001F4C5 Compile Top 5 EOD", key="ieodb"):
            prog2 = st.progress(0.0)
            with st.spinner("Analyzing Daily Charts for Coiled Setups..."):
                rows, _ = run_universe_scan(active_tickers, "2y", "1d", False, False, intra_rr,
                    intra_min_score, nifty_rs_series, capital_base, risk_per_trade, passes_regime_filter, None, prog2, "INTRADAY_EOD", "EOD_PLAYBOOK")
            prog2.empty(); st.session_state['intraday_eod_results'] = rows
        if st.session_state['intraday_eod_results'] is not None:
            display_scan_table(st.session_state['intraday_eod_results'], fetch_live=False)

    with isub[2]:
        st.markdown("### ⚡ One-Way Momentum Runners")
        st.markdown("<div class='info-panel'>Finds extreme momentum stocks that gap/break on massive volume and NEVER retrace past VWAP.</div>", unsafe_allow_html=True)
        if st.button("⚡ Scan for Runners", key="irunb"):
            prog3 = st.progress(0.0)
            with st.spinner(f"Finding Intraday Runners..."):
                rows, _ = run_universe_scan(active_tickers, intra_period, intra_interval, True, False, intra_rr,
                    0, None, capital_base, risk_per_trade, lambda x: True, None, prog3, "RUNNER", "RUNNERS_ONLY")
            prog3.empty(); st.session_state['intraday_runners'] = rows
        if st.session_state['intraday_runners'] is not None:
            display_scan_table(st.session_state['intraday_runners'])


# === SWING ===
with tab_swing:
    ssub = st.tabs(["📅 Swing EOD Playbook", "🧠 Swing Scanner", "⚡ Swing Runners"])

    with ssub[0]:
        st.markdown("### 📅 Swing EOD Playbook (Top 5)")
        st.markdown("<div class='info-panel'>Strictly curated Top 5 highest conviction Swing setups based on Daily closing data.</div>", unsafe_allow_html=True)
        if st.button("\U0001F680 Compile Swing Top 5", key="seodb", type="primary"):
            ps = st.progress(0.0)
            with st.spinner("Finding Top 5 Swing Setups..."):
                rows, _ = run_universe_scan(active_tickers, "2y", "1d", False, False, swing_rr,
                    swing_min_score, nifty_rs_series, capital_base, risk_per_trade, passes_regime_filter, None, ps, "SWING_EOD", "EOD_PLAYBOOK")
            ps.empty(); st.session_state['swing_eod_results'] = rows
        if st.session_state['swing_eod_results'] is not None:
            display_scan_table(st.session_state['swing_eod_results'], fetch_live=False)

    with ssub[1]:
        st.markdown("### 🧠 Swing Psychological Scanner")
        st.markdown(f"<div class='info-panel'>Scans live on <b>{stf}</b> timeframe.</div>", unsafe_allow_html=True)
        if st.button("\U0001F50D Run Swing Scan", key="ssb"):
            ps2 = st.progress(0.0)
            with st.spinner("Scanning..."):
                rows, _ = run_universe_scan(active_tickers, swing_period, swing_interval, False, False, swing_rr,
                    swing_min_score, nifty_rs_series, capital_base, risk_per_trade, passes_regime_filter, None, ps2, "SWING_LIVE", "ALL")
            ps2.empty(); st.session_state['swing_scan_results'] = rows
        if st.session_state['swing_scan_results'] is not None:
            display_scan_table(st.session_state['swing_scan_results'])

    with ssub[2]:
        st.markdown("### ⚡ Swing Multi-Day Runners")
        st.markdown("<div class='info-panel'>Finds stocks breaking out on massive volume and riding the EMA on the Swing timeframe.</div>", unsafe_allow_html=True)
        if st.button("⚡ Scan Swing Runners", key="srunb"):
            ps3 = st.progress(0.0)
            with st.spinner("Finding..."):
                rows, _ = run_universe_scan(active_tickers, swing_period, swing_interval, False, False, swing_rr,
                    0, None, capital_base, risk_per_trade, lambda x: True, None, ps3, "SWING_RUNNER", "RUNNERS_ONLY")
            ps3.empty(); st.session_state['swing_runners'] = rows
        if st.session_state['swing_runners'] is not None:
            display_scan_table(st.session_state['swing_runners'])

# === TOOLS ===
with tab_tools:
    tsub = st.tabs(["FII/DII Dashboard", "Signal Ledger"])
    with tsub[0]:
        st.markdown("### FII/DII Institutional Flow")
        st.markdown("Requires MrChartist Free API.")
    with tsub[1]:
        st.markdown("### Lifetime Signal Ledger")

st.markdown("---")
st.markdown(f"<div style='text-align:center;color:#4a5078;font-size:11px;padding:10px;'>\U0001F451 QUANT-EDGE v18 \u2022 Psychology \u2022 VWAP \u2022 Runners \u2022 {get_ist_now().strftime('%d %b %Y, %I:%M %p IST')}</div>", unsafe_allow_html=True)
