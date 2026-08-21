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
    page_title="QUANT-EDGE Flagship Trading Terminal v17",
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

    .signal-card {
        background: rgba(17,24,39,0.5); border-radius: 10px; padding: 14px 18px;
        margin-bottom: 10px; backdrop-filter: blur(8px);
        border: 1px solid rgba(255,255,255,0.04);
    }
    .signal-buy { border-left: 4px solid #00ffaa; }
    .signal-sell { border-left: 4px solid #ff3366; }

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

    .quick-stat {
        text-align: center; padding: 10px;
        background: rgba(17,24,39,0.4); border-radius: 8px;
        border: 1px solid rgba(255,255,255,0.04);
    }
    .quick-stat-val { font-size: 20px; font-weight: 800; color: #fff; }
    .quick-stat-label { font-size: 10px; color: #6b7294; text-transform: uppercase; letter-spacing: 1px; margin-top: 4px; }
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
    if not data_list:
        return
    if date_str is None:
        date_str = get_ist_now().strftime('%Y-%m-%d')
    filepath = os.path.join(HISTORY_DIR, f"{category}_{date_str}.json")
    existing = []
    if os.path.exists(filepath):
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                existing = json.load(f)
        except Exception:
            existing = []
    existing_keys = set()
    for item in existing:
        key = f"{item.get('Symbol','')}-{item.get('Date/Time', item.get('Scan_Time',''))}"
        existing_keys.add(key)
    for item in data_list:
        key = f"{item.get('Symbol','')}-{item.get('Date/Time', item.get('Scan_Time',''))}"
        if key not in existing_keys:
            existing.append(item)
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(existing, f, indent=2, default=str)
    except Exception:
        pass

def load_history(category, date_str):
    filepath = os.path.join(HISTORY_DIR, f"{category}_{date_str}.json")
    if os.path.exists(filepath):
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return []
    return []

def get_available_history_dates(category):
    dates = []
    prefix = f"{category}_"
    if os.path.exists(HISTORY_DIR):
        for f in os.listdir(HISTORY_DIR):
            if f.startswith(prefix) and f.endswith('.json'):
                date_str = f[len(prefix):-5]
                try:
                    dates.append(datetime.strptime(date_str, '%Y-%m-%d').date())
                except Exception:
                    pass
    return sorted(dates, reverse=True)

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
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            if not df.empty and len(df) >= 2:
                ltp = float(df['Close'].iloc[-1])
                prev = float(df['Close'].iloc[-2])
                res[name] = {"LTP": ltp, "Change_Val": ltp - prev, "Change_Pct": ((ltp - prev) / prev) * 100}
            elif not df.empty:
                res[name] = {"LTP": float(df['Close'].iloc[-1]), "Change_Val": 0.0, "Change_Pct": 0.0}
        except Exception:
            res[name] = {"LTP": 0.0, "Change_Val": 0.0, "Change_Pct": 0.0}
    return res

@st.cache_data(ttl=900)
def fetch_stock_data(ticker, period="3y", interval="1d"):
    try:
        data = yf.download(ticker, period=period, interval=interval, progress=False, auto_adjust=True)
        if isinstance(data.columns, pd.MultiIndex):
            data.columns = data.columns.get_level_values(0)
        return data.dropna(subset=['Close'])
    except Exception:
        return pd.DataFrame()

@st.cache_data(ttl=900)
def fetch_bulk_stock_data(tickers_tuple, period="1y", interval="1d"):
    tickers = list(tickers_tuple) if isinstance(tickers_tuple, tuple) else tickers_tuple
    try:
        return yf.download(tickers, period=period, interval=interval, group_by="ticker", progress=False, auto_adjust=True)
    except Exception:
        return pd.DataFrame()

@st.cache_data(ttl=900)
def fetch_nifty_series(period="2y", interval="1d"):
    return fetch_stock_data("^NSEI", period=period, interval=interval)

@st.cache_data(ttl=30)
def fetch_live_prices(tickers_tuple):
    tickers = list(tickers_tuple)
    if not tickers:
        return {}
    try:
        df = yf.download(tickers, period="1d", interval="1m", progress=False, auto_adjust=True)
        if df.empty:
            df = yf.download(tickers, period="2d", interval="1d", progress=False, auto_adjust=True)
        if df.empty:
            return {}
        prices = {}
        if isinstance(df.columns, pd.MultiIndex):
            for t in tickers:
                try:
                    close = df[t]['Close'].dropna()
                    if not close.empty:
                        prices[t] = float(close.iloc[-1])
                except Exception:
                    pass
        elif len(tickers) == 1:
            cols = df.columns
            if isinstance(cols, pd.MultiIndex):
                cols = cols.get_level_values(0)
            close = df['Close'].dropna() if 'Close' in cols else pd.Series()
            if not close.empty:
                prices[tickers[0]] = float(close.iloc[-1])
        return prices
    except Exception:
        return {}

def get_symbol_df(bulk_df, ticker, fallback_period, fallback_interval, min_len=50):
    df = pd.DataFrame()
    if not bulk_df.empty:
        try:
            if isinstance(bulk_df.columns, pd.MultiIndex):
                if ticker in bulk_df.columns.get_level_values(0):
                    df = bulk_df[ticker].dropna(how='all')
            else:
                df = bulk_df.dropna(how='all')
        except Exception:
            pass
    if df.empty or len(df) < min_len:
        df = fetch_stock_data(ticker, period=fallback_period, interval=fallback_interval)
    return df

# =============================================================================
# ENHANCED INDICATOR ENGINE
# =============================================================================
def compute_rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
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
        df['Supertrend'] = np.nan
        df['ST_Direction'] = 0
        return df
    high = df['High'].values.astype(float)
    low = df['Low'].values.astype(float)
    close = df['Close'].values.astype(float)
    prev_close = np.roll(close, 1); prev_close[0] = close[0]
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))
    atr = pd.Series(tr).ewm(span=period, adjust=False).mean().values
    hl2 = (high + low) / 2.0
    upper = hl2 + multiplier * atr
    lower = hl2 - multiplier * atr
    direction = np.ones(len(df), dtype=int)
    supertrend = np.full(len(df), np.nan)
    for i in range(1, len(df)):
        if lower[i] < lower[i-1] and close[i-1] > lower[i-1]:
            lower[i] = lower[i-1]
        if upper[i] > upper[i-1] and close[i-1] < upper[i-1]:
            upper[i] = upper[i-1]
        if close[i] > upper[i]:
            direction[i] = 1
        elif close[i] < lower[i]:
            direction[i] = -1
        else:
            direction[i] = direction[i-1]
        supertrend[i] = lower[i] if direction[i] == 1 else upper[i]
    df['Supertrend'] = supertrend
    df['ST_Direction'] = direction
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
    return df

# =============================================================================
# MARKET REGIME, TREND CONSISTENCY, CONFLUENCE SCORING
# =============================================================================
def get_market_regime(nifty_df):
    if nifty_df.empty or len(nifty_df) < 55:
        return "UNKNOWN"
    nd = compute_all_indicators(nifty_df, is_intraday=False)
    if nd.empty or 'EMA_20' not in nd.columns:
        return "UNKNOWN"
    last = nd.iloc[-1]
    if pd.isna(last.get('EMA_20')) or pd.isna(last.get('EMA_50')):
        return "UNKNOWN"
    st_dir = last.get('ST_Direction', 0)
    if last['Close'] > last['EMA_20'] > last['EMA_50']:
        return "BULL"
    elif last['Close'] < last['EMA_20'] < last['EMA_50']:
        return "BEAR"
    return "CHOPPY"

def get_nifty_rs_series(nifty_df, window=21):
    if nifty_df.empty or len(nifty_df) < window + 1:
        return None
    return nifty_df['Close'].pct_change(window) * 100.0

def compute_trend_consistency(df, window=5):
    if df.empty or len(df) < window + 1:
        return 0.0
    diffs = df['Close'].diff().iloc[-window:]
    valid = diffs.dropna()
    if len(valid) == 0:
        return 0.0
    up = int((valid > 0).sum())
    down = int((valid < 0).sum())
    return (max(up, down) / max(len(valid), 1)) * 100.0

@st.cache_data(ttl=1800)
def compute_daily_supertrend_directions(tickers_tuple):
    tickers = list(tickers_tuple)
    df_bulk = fetch_bulk_stock_data(tuple(tickers), period="6mo", interval="1d")
    directions = {}
    for ticker in tickers:
        dfs = get_symbol_df(df_bulk, ticker, "6mo", "1d", min_len=30)
        if dfs.empty or len(dfs) < 30:
            directions[ticker] = 0
            continue
        dfs = compute_supertrend(dfs.copy(), period=10, multiplier=2.0)
        last_dir = dfs['ST_Direction'].dropna()
        directions[ticker] = int(last_dir.iloc[-1]) if not last_dir.empty else 0
    return directions

def compute_confluence_score(df, nifty_rs_series=None, is_intraday=False, daily_direction=None, window=21):
    df = df.copy()
    if 'EMA_20' not in df.columns or 'EMA_50' not in df.columns or 'ATR14' not in df.columns:
        df['Confluence_Score'] = np.nan
        return df

    trend_up = (df['Close'] > df['EMA_20']) & (df['EMA_20'] > df['EMA_50'])
    trend_down = (df['Close'] < df['EMA_20']) & (df['EMA_20'] < df['EMA_50'])
    trend_score = np.where(trend_up | trend_down, 20, 5)

    if 'ST_Direction' in df.columns:
        st_up = trend_up & (df['ST_Direction'] == 1)
        st_down = trend_down & (df['ST_Direction'] == -1)
        st_score = np.where(st_up | st_down, 15, np.where(trend_up | trend_down, 5, 0))
    else:
        st_score = np.full(len(df), 5.0)

    if 'RSI_14' in df.columns:
        rsi = df['RSI_14'].fillna(50)
        rsi_score = np.where((rsi >= 30) & (rsi <= 70), 15, np.where((rsi >= 20) & (rsi <= 80), 8, 3))
    else:
        rsi_score = np.full(len(df), 8.0)

    if 'MACD_Hist' in df.columns:
        macd_up = trend_up & (df['MACD_Hist'] > 0)
        macd_down = trend_down & (df['MACD_Hist'] < 0)
        macd_score = np.where(macd_up | macd_down, 10, 2)
    else:
        macd_score = np.full(len(df), 4.0)

    if 'Vol_MA_20' in df.columns:
        vol_ratio = (df['Volume'] / df['Vol_MA_20'].replace(0, np.nan)).clip(upper=3.0).fillna(0)
        volume_score = (vol_ratio / 3.0 * 15).clip(0, 15)
    else:
        volume_score = np.full(len(df), 5.0)

    if nifty_rs_series is not None:
        stock_ret = df['Close'].pct_change(window) * 100.0
        rs = (stock_ret - nifty_rs_series.reindex(df.index).ffill()).clip(-10, 10).fillna(0)
        rs_score = ((rs + 10) / 20 * 10).clip(0, 10)
    else:
        rs_score = np.full(len(df), 5.0)

    atr_safe = df['ATR14'].replace(0, np.nan).fillna(1)
    if is_intraday and 'VWAP' in df.columns:
        vwap_dist = ((df['Close'] - df['VWAP']).abs() / atr_safe).clip(upper=3.0).fillna(3.0)
        proximity_score = ((3.0 - vwap_dist) / 3.0 * 10).clip(0, 10)
    else:
        dist_atr = ((df['Close'] - df['EMA_20']).abs() / atr_safe).clip(upper=3.0).fillna(3.0)
        proximity_score = ((3.0 - dist_atr) / 3.0 * 10).clip(0, 10)

    if daily_direction is not None and is_intraday and daily_direction != 0:
        if daily_direction == 1:
            mtf_score = np.where(trend_up, 5, np.where(trend_down, -10, 0)).astype(float)
        else:
            mtf_score = np.where(trend_down, 5, np.where(trend_up, -10, 0)).astype(float)
    else:
        mtf_score = np.zeros(len(df))

    total = trend_score + st_score + rsi_score + macd_score + volume_score + rs_score + proximity_score + mtf_score
    df['Confluence_Score'] = pd.Series(total, index=df.index).clip(0, 100)
    return df

# =============================================================================
# SIGNAL GENERATION
# =============================================================================
def generate_signals_for_asset(df, lookback=10, is_intraday=False, rr=2.0, use_orb=False):
    if df.empty or len(df) < max(50, lookback) + 5:
        for c in ['Bullish_Sweep', 'Bearish_Sweep', 'Bullish_ORB', 'Bearish_ORB']:
            df[c] = False
        for c in ['Entry_Trigger', 'Stop_Loss', 'Take_Profit']:
            df[c] = np.nan
        return df
    df = compute_all_indicators(df, is_intraday=is_intraday)
    if df.empty or 'EMA_50' not in df.columns:
        return df
    df['Prev_Low_L'] = df['Low'].shift(1).rolling(lookback).min()
    df['Prev_High_H'] = df['High'].shift(1).rolling(lookback).max()
    df['Bullish_Sweep'] = (
        (df['Close'] > df['EMA_50']) & (df['Low'] < df['Prev_Low_L']) &
        (df['Close'] > df['Prev_Low_L']) & (df['Volume'] > df['Vol_MA_20'])
    )
    df['Bearish_Sweep'] = (
        (df['Close'] < df['EMA_50']) & (df['High'] > df['Prev_High_H']) &
        (df['Close'] < df['Prev_High_H']) & (df['Volume'] > df['Vol_MA_20'])
    )
    df['Bullish_ORB'] = False
    df['Bearish_ORB'] = False
    if use_orb and is_intraday:
        df['Date_Group'] = df.index.date
        df['ORB_High'] = df.groupby('Date_Group')['High'].transform('first')
        df['ORB_Low'] = df.groupby('Date_Group')['Low'].transform('first')
        df['Bullish_ORB'] = (
            (df.groupby('Date_Group').cumcount() > 0) &
            (df['Close'] > df['ORB_High']) & (df['Close'].shift(1) <= df['ORB_High'])
        )
        df['Bearish_ORB'] = (
            (df.groupby('Date_Group').cumcount() > 0) &
            (df['Close'] < df['ORB_Low']) & (df['Close'].shift(1) >= df['ORB_Low'])
        )
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

# =============================================================================
# BACKTEST ENGINE
# =============================================================================
def run_backtest_engine(df, is_intraday=False, use_orb=False, rr=2.0, slippage=0.05, commission=0.03, expiry_bars=5):
    required = ['Bullish_Sweep', 'Bearish_Sweep', 'Entry_Trigger', 'Stop_Loss', 'Take_Profit']
    if df.empty or not all(c in df.columns for c in required):
        return pd.DataFrame()
    trades, state, pos_type = [], 'FLAT', None
    trigger = sl = tp = entry_p = entry_t = None
    bars_waited = 0

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
        buy_sig = row.get('Bullish_ORB', False) if (is_intraday and use_orb) else row.get('Bullish_Sweep', False)
        sell_sig = row.get('Bearish_ORB', False) if (is_intraday and use_orb) else row.get('Bearish_Sweep', False)
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
                state = 'FLAT'
            continue
        if state == 'IN_POSITION':
            if is_intraday and hasattr(idx, 'hour') and idx.hour == 15 and idx.minute >= 15:
                log_exit(row['Close'], idx, 'SQ_OFF'); state = 'FLAT'; continue
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
    if trades_df.empty:
        return None
    df = trades_df.copy()
    df['EntryDate_dt'] = pd.to_datetime(df['EntryDate'], errors='coerce')
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
    max_dd = ((caps - caps.cummax()) / caps.cummax() * 100).min()
    returns = caps.pct_change().dropna()
    date_span = max((df['EntryDate_dt'].max() - df['EntryDate_dt'].min()).days, 1)
    tpy = total_trades / date_span * 365.0
    sharpe = (returns.mean() / returns.std()) * np.sqrt(tpy) if len(returns) > 1 and returns.std() > 0 else 0.0
    return {'total_trades': total_trades, 'win_rate': win_rate, 'net_profit_pct': net_profit_pct, 'max_dd': max_dd, 'sharpe': sharpe}

# =============================================================================
# POSITION SIZING
# =============================================================================
def position_size(capital, risk_pct, entry, stop):
    risk_amount = capital * (risk_pct / 100.0)
    per_share_risk = abs(entry - stop)
    if per_share_risk <= 0 or capital <= 0:
        return 0, 0.0
    qty = int(risk_amount // per_share_risk)
    return qty, qty * entry

# =============================================================================
# FII/DII DATA
# =============================================================================
@st.cache_data(ttl=3600)
def fetch_fii_dii_data():
    try:
        import requests as req
        resp = req.get("https://mrchartist.com/api/data", timeout=10)
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return None

@st.cache_data(ttl=3600)
def fetch_fii_dii_history():
    try:
        import requests as req
        resp = req.get("https://mrchartist.com/api/history", timeout=10)
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return None

# =============================================================================
# FYERS INTEGRATION
# =============================================================================
def get_fyers_credentials():
    try:
        return st.secrets["FYERS_APP_ID"], st.secrets["FYERS_SECRET_KEY"], st.secrets["FYERS_REDIRECT_URI"]
    except Exception:
        return None, None, None

def get_fyers_session():
    app_id, secret_key, redirect_uri = get_fyers_credentials()
    if not app_id or not FYERS_SDK_AVAILABLE:
        return None
    return fyersModel.SessionModel(client_id=app_id, secret_key=secret_key, redirect_uri=redirect_uri,
                                    response_type="code", grant_type="authorization_code")

def get_fyers_client():
    app_id, _, _ = get_fyers_credentials()
    token = st.session_state.get('fyers_access_token')
    if not app_id or not token or not FYERS_SDK_AVAILABLE:
        return None
    return fyersModel.FyersModel(client_id=app_id, is_async=False, token=token, log_path="")

def to_fyers_symbol(yf_ticker):
    return "NSE:" + yf_ticker.replace(".NS", "") + "-EQ"

def fetch_fyers_quotes_bulk(fyers_client, yf_tickers, batch_size=50):
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

# =============================================================================
# SIGNAL LEDGER
# =============================================================================
def save_signals_to_db(new_signals_df):
    if new_signals_df.empty:
        return
    if os.path.exists(SIGNALS_FILE):
        try:
            existing = pd.read_csv(SIGNALS_FILE)
            combined = pd.concat([existing, new_signals_df], ignore_index=True)
            combined.drop_duplicates(subset=['Date/Time', 'Ticker', 'Signal Type'], keep='last', inplace=True)
            combined.to_csv(SIGNALS_FILE, index=False)
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
        active_mask = ~df_db['Outcome'].astype(str).str.contains('Hit', na=False)
        for idx in df_db[active_mask].index.tolist():
            row = df_db.loc[idx]
            ticker = str(row['Ticker']) + ".NS"
            sl, tp = float(row['Stop Loss']), float(row['Take Profit'])
            sig_type = str(row['Signal Type'])
            df_hist = fetch_stock_data(ticker, period="3mo", interval="1d")
            if df_hist.empty:
                continue
            try:
                df_hist = df_hist[df_hist.index >= pd.to_datetime(str(row['Date/Time']))]
            except Exception:
                continue
            outcome, hit_time = "\U0001F535 Active", "-"
            for dt_idx, h_row in df_hist.iterrows():
                h_date = dt_idx.strftime('%Y-%m-%d') if hasattr(dt_idx, 'strftime') else str(dt_idx)
                if sig_type == "BULLISH":
                    if float(h_row['Low']) <= sl:
                        outcome, hit_time = "\u274C Stop-Loss Hit", h_date; break
                    elif float(h_row['High']) >= tp:
                        outcome, hit_time = "\u2705 Target Hit", h_date; break
                else:
                    if float(h_row['High']) >= sl:
                        outcome, hit_time = "\u274C Stop-Loss Hit", h_date; break
                    elif float(h_row['Low']) <= tp:
                        outcome, hit_time = "\u2705 Target Hit", h_date; break
            df_db.loc[idx, 'Outcome'] = outcome
            df_db.loc[idx, 'Hit Date/Time'] = hit_time
        df_db.to_csv(SIGNALS_FILE, index=False)
    except Exception:
        pass

# =============================================================================
# HELPERS
# =============================================================================
def get_signal_status(live_price, entry, sl, tp, is_bullish):
    if pd.isna(live_price):
        return "\u23F3 Loading...", "#6b7294"
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

def check_pm_trigger_status(ticker, gap_pct, entry_trigger, sl_level, tp_lvl):
    try:
        df_5m = yf.download(ticker, period="1d", interval="5m", progress=False, auto_adjust=True)
        if isinstance(df_5m.columns, pd.MultiIndex):
            df_5m.columns = df_5m.columns.get_level_values(0)
        df_5m = df_5m.dropna(subset=['Close'])
        if df_5m.empty:
            return "Pending", "-", "Active", "-"
        first = df_5m.iloc[0]
        fh, fl = float(first['High']), float(first['Low'])
        triggered, trig_t, outcome, out_t = False, "-", "Active", "-"
        for idx, row in df_5m.iloc[1:].iterrows():
            ct = idx.strftime('%I:%M %p') if hasattr(idx, 'strftime') else str(idx)
            if gap_pct >= 0:
                if not triggered and float(row['High']) >= fh:
                    triggered, trig_t = True, ct
                elif triggered:
                    if float(row['Low']) <= sl_level:
                        outcome, out_t = "\u274C SL Hit", ct; break
                    elif float(row['High']) >= tp_lvl:
                        outcome, out_t = "\u2705 Target Hit", ct; break
            else:
                if not triggered and float(row['Low']) <= fl:
                    triggered, trig_t = True, ct
                elif triggered:
                    if float(row['High']) >= sl_level:
                        outcome, out_t = "\u274C SL Hit", ct; break
                    elif float(row['Low']) <= tp_lvl:
                        outcome, out_t = "\u2705 Target Hit", ct; break
        if not triggered:
            return "Pending Breakout", "-", "Active", "-"
        status = f"\U0001F7E2 Triggered {trig_t}" if gap_pct >= 0 else f"\U0001F534 Triggered {trig_t}"
        return status, trig_t, outcome, out_t
    except Exception:
        return "Not Triggered", "-", "Active", "-"

# =============================================================================
# SHARED SCANNER
# =============================================================================
def run_universe_scan(tickers, period, interval, is_intraday, use_orb, rr,
                      min_score, nifty_rs, capital, risk_pct, regime_fn,
                      daily_directions=None, progress_bar=None, source_tag="SCAN"):
    rows, persist = [], []
    total = len(tickers)
    df_bulk = fetch_bulk_stock_data(tuple(tickers), period=period, interval=interval)
    for i, ticker in enumerate(tickers):
        if progress_bar:
            progress_bar.progress((i + 1) / total)
        df = get_symbol_df(df_bulk, ticker, period, interval, min_len=20 if is_intraday else 50)
        if df.empty or len(df) < (20 if is_intraday else 50):
            continue
        df = generate_signals_for_asset(df, lookback=10, is_intraday=is_intraday, rr=rr, use_orb=use_orb)
        if df.empty:
            continue
        dd = daily_directions.get(ticker, 0) if daily_directions else None
        df = compute_confluence_score(df, nifty_rs, is_intraday=is_intraday, daily_direction=dd)
        if 'Confluence_Score' not in df.columns:
            continue
        latest = df.iloc[-1]
        bull = bool(latest.get('Bullish_Sweep', False)) or bool(latest.get('Bullish_ORB', False))
        bear = bool(latest.get('Bearish_Sweep', False)) or bool(latest.get('Bearish_ORB', False))
        if not (bull or bear) or not regime_fn(bull):
            continue
        score = latest.get('Confluence_Score', 0)
        if pd.isna(score) or score < min_score:
            continue
        trig = latest['Entry_Trigger']; sl_v = latest['Stop_Loss']; tp_v = latest['Take_Profit']
        if pd.isna(trig) or pd.isna(sl_v):
            continue
        qty, dep = position_size(capital, risk_pct, trig, sl_v)
        sig_time = latest.name.strftime('%Y-%m-%d %H:%M') if hasattr(latest.name, 'strftime') else str(latest.name)
        rows.append({
            'Symbol': ticker.replace('.NS', ''), 'Signal': 'BUY' if bull else 'SELL',
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
            'Signal Type': 'BULLISH' if bull else 'BEARISH',
            'Entry Trigger': round(float(trig), 2), 'Stop Loss': round(float(sl_v), 2),
            'Take Profit': round(float(tp_v), 2), 'Score': int(round(score)),
            'Volume': int(latest['Volume']), 'R:R Ratio': f"1:{rr:.1f}",
            'Outcome': '\U0001F535 Active', 'Hit Date/Time': '-', 'Source': source_tag
        })
    return rows, persist

# =============================================================================
# SESSION STATE
# =============================================================================
for key in ['intraday_scan_results', 'intraday_premarket_results', 'intraday_top5_results',
            'swing_eod_results', 'swing_scan_results', 'leaderboard_results',
            'fyers_access_token', 'fyers_premarket_df']:
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
    try:
        _latest_nifty_date = _li.tz_localize(None).date() if _li.tzinfo else _li.date()
    except Exception:
        try:
            _latest_nifty_date = _li.date()
        except Exception:
            pass
SESSION_DATA_LIVE_TODAY = (_latest_nifty_date == _today_ist)

# =============================================================================
# HEADER + INDEX STRIP
# =============================================================================
col_hl, col_hr = st.columns([4, 1])
with col_hl:
    st.markdown("<div class='main-header'>\U0001F451 QUANT-EDGE FLAGSHIP TERMINAL v17</div>", unsafe_allow_html=True)
    st.markdown("<div class='sub-caption'>Institutional Scanner \u2022 RSI \u2022 MACD \u2022 Supertrend \u2022 Multi-TF Confluence</div>", unsafe_allow_html=True)
with col_hr:
    ms, mc = get_market_status()
    st.markdown(f"<div class='market-status {mc}'>{ms}</div>", unsafe_allow_html=True)
    st.markdown(f"<div class='last-updated'>{get_ist_now().strftime('%I:%M %p IST')}</div>", unsafe_allow_html=True)

rc = {"BULL": "regime-bull", "BEAR": "regime-bear", "CHOPPY": "regime-choppy"}.get(market_regime, "regime-choppy")
rm = {
    "BULL": "\U0001F7E2 REGIME: BULL \u2014 Nifty above rising EMA + Supertrend UP. Longs favoured.",
    "BEAR": "\U0001F534 REGIME: BEAR \u2014 Nifty below falling EMA + Supertrend DOWN. Shorts favoured.",
    "CHOPPY": "\U0001F7E1 REGIME: CHOPPY \u2014 Trend unclear. Trade smaller, expect false breaks.",
    "UNKNOWN": "\u26AA REGIME: UNKNOWN \u2014 Insufficient Nifty history."
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
        if universe_sel == "Full NSE F&O (219)":
            active_tickers = FO_UNIVERSE
        elif universe_sel == "Custom Watchlist":
            ci = st.text_area("Tickers:", value="TATAMOTORS.NS,RELIANCE.NS,TCS.NS,HDFCBANK.NS", key="ct")
            active_tickers = [t.strip().upper() for t in ci.split(",") if t.strip()]
        else:
            active_tickers = [t.strip() for t in SECTOR_MAP[universe_sel].split(",")]
        st.caption(f"{len(active_tickers)} symbols")
    with st.expander("\U0001F4B0 Capital & Risk", expanded=True):
        capital_base = st.number_input("Capital (\u20B9)", min_value=10000, value=200000, step=10000, key="cap")
        risk_per_trade = st.slider("Risk/Trade (%)", 0.25, 3.0, 1.0, 0.25, key="rpt")
    with st.expander("\U0001F4CA Intraday", expanded=False):
        itf = st.selectbox("Timeframe", ["5-Minute", "15-Minute", "1-Minute"], key="itf")
        itf_map = {"5-Minute": ("5m", "30d"), "15-Minute": ("15m", "60d"), "1-Minute": ("1m", "7d")}
        intra_interval, intra_period = itf_map[itf]
        intra_rr = st.slider("R:R", 1.0, 5.0, 2.0, 0.5, key="irr")
        intra_min_score = st.slider("Min Score", 0, 100, 55, 5, key="ims")
    with st.expander("\U0001F4C8 Swing", expanded=False):
        stf = st.selectbox("Timeframe", ["1-Day (Daily)", "1-Hour", "1-Week (Weekly)"], key="stf")
        stf_map = {"1-Day (Daily)": ("1d", "3y"), "1-Hour": ("60m", "2y"), "1-Week (Weekly)": ("1wk", "5y")}
        swing_interval, swing_period = stf_map[stf]
        swing_rr = st.slider("R:R", 1.0, 5.0, 2.0, 0.5, key="srr")
        swing_min_score = st.slider("Min Score", 0, 100, 60, 5, key="sms")
    with st.expander("\U0001F6E1\uFE0F Filters", expanded=False):
        apply_regime_filter = st.checkbox("Regime Filter", value=True, key="rf")
        slippage_rate = st.slider("Slippage (%)", 0.0, 0.2, 0.05, 0.01, key="sl")
        commission_rate = st.slider("Brokerage (%)", 0.0, 0.1, 0.03, 0.01, key="cm")
    with st.expander("\U0001F504 Auto-Refresh", expanded=False):
        ref = st.selectbox("Interval", ["Manual", "30s", "60s", "2min"], key="ro")
        if ref != "Manual" and AUTOREFRESH_AVAILABLE:
            rs = {"30s": 30, "60s": 60, "2min": 120}[ref]
            if market_is_live:
                st_autorefresh(interval=rs * 1000, key="ar")
                st.caption(f"\u2705 Every {rs}s")
            else:
                st.caption("\u23F8\uFE0F Paused (closed)")

def passes_regime_filter(is_bull):
    if not apply_regime_filter: return True
    if market_regime in ("CHOPPY", "UNKNOWN"): return False
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
            r['Live_Price'], r['Entry_Trigger'], r['Stop_Loss'], r['Take_Profit'], r['Signal'] == 'BUY'
        )[0] if not pd.isna(r.get('Live_Price', np.nan)) else '\u23F3 Pending', axis=1)
    else:
        df['Live_Price'] = df['LTP_at_scan']; df['Status'] = '\u23F3 Pending'
    disp = pd.DataFrame({
        'Symbol': df['Symbol'],
        'Signal': df['Signal'].apply(lambda s: f"{'🟢' if s == 'BUY' else '🔴'} {s}"),
        'Score': df['Score'],
        'Live \u20B9': df['Live_Price'].apply(lambda x: f"\u20B9{x:,.2f}" if not pd.isna(x) else "-"),
        'Entry': df['Entry_Trigger'].apply(lambda x: f"\u20B9{x:,.2f}"),
        'SL': df['Stop_Loss'].apply(lambda x: f"\u20B9{x:,.2f}"),
        'Target': df['Take_Profit'].apply(lambda x: f"\u20B9{x:,.2f}"),
        'Status': df['Status'], 'Qty': df['Qty'],
        'R:R': df['RR'].apply(lambda x: f"1:{x:.1f}"),
        'RSI': df['RSI'], 'MACD': df['MACD_H'],
        'ST': df['ST_Dir'].apply(lambda x: '\u2B06\uFE0F' if x == 1 else '\u2B07\uFE0F' if x == -1 else '\u2796')
    }).sort_values('Score', ascending=False).reset_index(drop=True)
    st.dataframe(disp, use_container_width=True, height=min(400, 35 * len(disp) + 38))

def render_chart(ticker_ns, period, interval, is_intraday, rr, use_orb=False):
    df = fetch_stock_data(ticker_ns, period=period, interval=interval)
    if df.empty or len(df) < 55:
        st.info("Not enough data."); return
    df = generate_signals_for_asset(df, lookback=10, is_intraday=is_intraday, rr=rr, use_orb=use_orb)
    name = ticker_ns.replace('.NS', '')
    fig = go.Figure()
    fig.add_trace(go.Candlestick(x=df.index, open=df['Open'], high=df['High'], low=df['Low'], close=df['Close'],
        name="Price", increasing_line_color='#00ffcc', decreasing_line_color='#ff3366',
        increasing_fillcolor='#00ffcc', decreasing_fillcolor='#ff3366', line=dict(width=0.8)))
    if 'EMA_20' in df.columns:
        fig.add_trace(go.Scatter(x=df.index, y=df['EMA_20'], line=dict(color='#ffaa00', width=1.2), name="EMA 20"))
    if 'EMA_50' in df.columns:
        fig.add_trace(go.Scatter(x=df.index, y=df['EMA_50'], line=dict(color='#0077ff', width=1.2), name="EMA 50"))
    if 'Supertrend' in df.columns:
        st_up = df[df['ST_Direction'] == 1]; st_dn = df[df['ST_Direction'] == -1]
        if not st_up.empty:
            fig.add_trace(go.Scatter(x=st_up.index, y=st_up['Supertrend'], line=dict(color='#00ffaa', width=1, dash='dot'), name="ST Up"))
        if not st_dn.empty:
            fig.add_trace(go.Scatter(x=st_dn.index, y=st_dn['Supertrend'], line=dict(color='#ff3366', width=1, dash='dot'), name="ST Down"))
    if 'Pivot' in df.columns:
        fig.add_trace(go.Scatter(x=df.index, y=df['Pivot'], line=dict(color='#8f95b2', width=0.7, dash='dot'), name="Pivot"))
    if is_intraday and 'VWAP' in df.columns:
        fig.add_trace(go.Scatter(x=df.index, y=df['VWAP'], line=dict(color='#e040fb', width=1.2), name="VWAP"))
    bs = df[df['Bullish_Sweep'] | df.get('Bullish_ORB', pd.Series(False, index=df.index)).fillna(False)]
    ss = df[df['Bearish_Sweep'] | df.get('Bearish_ORB', pd.Series(False, index=df.index)).fillna(False)]
    if not bs.empty:
        fig.add_trace(go.Scatter(x=bs.index, y=bs['Low'] * 0.98, mode='markers',
            marker=dict(symbol='triangle-up', size=12, color='#00ffcc', line=dict(width=1, color='white')), name="BUY"))
    if not ss.empty:
        fig.add_trace(go.Scatter(x=ss.index, y=ss['High'] * 1.02, mode='markers',
            marker=dict(symbol='triangle-down', size=12, color='#ff3366', line=dict(width=1, color='white')), name="SELL"))
    fig.update_layout(title=f"{name} \u2022 {interval.upper()} \u2022 R:R 1:{rr:.1f}", template="plotly_dark",
        xaxis_rangeslider_visible=False, height=550, paper_bgcolor='#0a0b14', plot_bgcolor='#101220',
        yaxis_title="Price (\u20B9)", legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(l=60, r=20, t=60, b=40))
    st.plotly_chart(fig, use_container_width=True)
    if 'RSI_14' in df.columns:
        fr = go.Figure()
        fr.add_trace(go.Scatter(x=df.index, y=df['RSI_14'], line=dict(color='#e040fb', width=1.2), name="RSI"))
        fr.add_hline(y=70, line_dash="dash", line_color="#ff3366", annotation_text="Overbought")
        fr.add_hline(y=30, line_dash="dash", line_color="#00ffaa", annotation_text="Oversold")
        fr.update_layout(title="RSI (14)", template="plotly_dark", height=200,
            paper_bgcolor='#0a0b14', plot_bgcolor='#101220', margin=dict(l=60, r=20, t=40, b=20), showlegend=False)
        st.plotly_chart(fr, use_container_width=True)

# =============================================================================
# MAIN TABS
# =============================================================================
tab_intraday, tab_swing, tab_tools = st.tabs([
    "\U0001F4CA INTRADAY COMMAND CENTER", "\U0001F4C8 SWING POSITION DESK", "\U0001F6E0\uFE0F GLOBAL TOOLS"
])

# === INTRADAY ===
with tab_intraday:
    isub = st.tabs(["\U0001F7E2 Live Scanner", "\u23F0 Pre-Market", "\U0001F3C6 Top 5", "\U0001F50D Chart", "\U0001F4C5 History"])

    with isub[0]:
        st.markdown("### \U0001F7E2 Intraday Live Scanner")
        st.markdown(f"<div class='info-panel'>\U0001F4E1 <b>{itf}</b> \u2022 Min Score: {intra_min_score} \u2022 R:R 1:{intra_rr:.1f} \u2022 Multi-TF daily Supertrend check</div>", unsafe_allow_html=True)
        cif1, cif2 = st.columns(2)
        with cif1: intra_search = st.text_input("Filter:", placeholder="e.g. RELIANCE", key="is")
        with cif2: intra_sig_f = st.selectbox("Signal:", ["All", "\U0001F7E2 BUY", "\U0001F534 SELL"], key="isf")
        if st.button("\U0001F680 Run Intraday Scan", key="isb", type="primary"):
            prog = st.progress(0.0)
            with st.spinner(f"Scanning {len(active_tickers)} on {itf}..."):
                dd = compute_daily_supertrend_directions(tuple(active_tickers))
                rows, pers = run_universe_scan(active_tickers, intra_period, intra_interval, True, True, intra_rr,
                    intra_min_score, None, capital_base, risk_per_trade, passes_regime_filter, dd, prog, "INTRADAY_LIVE")
                if pers: save_signals_to_db(pd.DataFrame(pers)); save_history("intraday", rows)
            prog.empty(); st.session_state['intraday_scan_results'] = rows
        if st.session_state['intraday_scan_results'] is not None:
            res = st.session_state['intraday_scan_results']
            if intra_search: res = [r for r in res if intra_search.upper() in r['Symbol']]
            if intra_sig_f == "\U0001F7E2 BUY": res = [r for r in res if r['Signal'] == 'BUY']
            elif intra_sig_f == "\U0001F534 SELL": res = [r for r in res if r['Signal'] == 'SELL']
            if res:
                q1, q2, q3, q4 = st.columns(4)
                buys = sum(1 for r in res if r['Signal'] == 'BUY')
                with q1: st.markdown(f"<div class='quick-stat'><div class='quick-stat-val'>{len(res)}</div><div class='quick-stat-label'>Signals</div></div>", unsafe_allow_html=True)
                with q2: st.markdown(f"<div class='quick-stat'><div class='quick-stat-val' style='color:#00ffaa'>{buys}</div><div class='quick-stat-label'>Buys</div></div>", unsafe_allow_html=True)
                with q3: st.markdown(f"<div class='quick-stat'><div class='quick-stat-val' style='color:#ff3366'>{len(res)-buys}</div><div class='quick-stat-label'>Sells</div></div>", unsafe_allow_html=True)
                with q4: st.markdown(f"<div class='quick-stat'><div class='quick-stat-val'>{sum(r['Score'] for r in res)/len(res):.0f}</div><div class='quick-stat-label'>Avg Score</div></div>", unsafe_allow_html=True)
                display_scan_table(res)
            else: st.info("No active intraday setups match filters.")

    with isub[1]:
        st.markdown("### \u23F0 Pre-Market Watchlist")
        st.markdown("<div class='info-panel'>Stocks with unusual opening volume + money deployment. Auto-saved to history.</div>", unsafe_allow_html=True)
        if not SESSION_DATA_LIVE_TODAY:
            st.warning("Yahoo has no pre-open data until ~9:16 AM. Connect Fyers for real 9:08 AM data.")
        cp1, cp2, cp3 = st.columns(3)
        with cp1: pmg = st.slider("Max Gap %", 0.1, 5.0, 1.2, 0.1, key="pg")
        with cp2: pmc = st.slider("Min \u20B9 Cr", 1, 50, 4, 1, key="pc")
        with cp3: pmv = st.slider("Vol Surge x", 1.5, 10.0, 2.5, 0.5, key="pv")
        if st.button("\U0001F50D Scan Pre-Market", key="pmb"):
            pmr = []; pp = st.progress(0.0)
            with st.spinner("Scanning..."):
                dbpm = fetch_bulk_stock_data(tuple(active_tickers), period="30d", interval="1d")
                for i, t in enumerate(active_tickers):
                    pp.progress((i+1)/len(active_tickers))
                    d = get_symbol_df(dbpm, t, "30d", "1d", 15)
                    if d.empty or len(d) < 15: continue
                    yc = float(d['Close'].iloc[-2]); to = float(d['Open'].iloc[-1])
                    tv = float(d['Volume'].iloc[-1]); hv = float(d['Volume'].iloc[-15:-1].mean())
                    if hv <= 0: continue
                    rv = tv/hv; mc = (to*tv)/1e7; gp = ((to-yc)/yc)*100
                    if abs(gp) <= pmg and mc >= pmc and rv >= pmv:
                        if not passes_regime_filter(gp >= 0): continue
                        yh, yl = float(d['High'].iloc[-2]), float(d['Low'].iloc[-2])
                        sl = yl if gp >= 0 else yh
                        tp = to + (to-sl)*intra_rr if gp >= 0 else to - (sl-to)*intra_rr
                        qty, dep = position_size(capital_base, risk_per_trade, to, sl)
                        st2, tt, ov, ot = check_pm_trigger_status(t, gp, to, sl, tp)
                        pmr.append({'Symbol': t.replace('.NS',''), 'Open': to, 'Gap': gp, 'Money_Cr': mc,
                            'Vol_Surge': rv, 'Setup': 'BUY' if gp >= 0 else 'SELL', 'Entry': to,
                            'Stop_Loss': sl, 'Target': tp, 'Qty': qty, 'RR': intra_rr,
                            'Status': st2, 'Outcome': ov, 'Scan_Time': get_ist_now().strftime('%Y-%m-%d %H:%M'),
                            'Date/Time': get_ist_now().strftime('%Y-%m-%d %H:%M')})
            pp.empty(); st.session_state['intraday_premarket_results'] = pmr; save_history("premarket", pmr)
        if st.session_state['intraday_premarket_results'] is not None:
            pm = st.session_state['intraday_premarket_results']
            if pm:
                pdf = pd.DataFrame(pm)
                dpdf = pd.DataFrame({
                    'Symbol': pdf['Symbol'], 'Open': pdf['Open'].apply(lambda x: f"\u20B9{x:,.2f}"),
                    'Gap': pdf['Gap'].apply(lambda x: f"{x:+.2f}%"), 'Money': pdf['Money_Cr'].apply(lambda x: f"\u20B9{x:.1f}Cr"),
                    'Vol': pdf['Vol_Surge'].apply(lambda x: f"{x:.1f}x"),
                    'Setup': pdf['Setup'].apply(lambda s: f"{'🟢' if s=='BUY' else '🔴'} INSTI {s}"),
                    'Entry': pdf['Entry'].apply(lambda x: f"\u20B9{x:,.2f}"), 'SL': pdf['Stop_Loss'].apply(lambda x: f"\u20B9{x:,.2f}"),
                    'Target': pdf['Target'].apply(lambda x: f"\u20B9{x:,.2f}"), 'Qty': pdf['Qty'],
                    'Status': pdf['Status'], 'Outcome': pdf['Outcome']
                })
                st.dataframe(dpdf.sort_values('Vol', ascending=False).reset_index(drop=True), use_container_width=True)
            else: st.info("No stocks matched filters.")

    with isub[2]:
        st.markdown("### \U0001F3C6 Top 5 Trend Leaders")
        st.markdown("<div class='info-panel'>F&O ranked by trend + consistency + confluence. One-way movers.</div>", unsafe_allow_html=True)
        if st.button("\u26A1 Find Top 5", key="t5b"):
            t5 = []; p5 = st.progress(0.0)
            with st.spinner("Ranking..."):
                db5 = fetch_bulk_stock_data(tuple(FO_UNIVERSE), period="6mo", interval="1d")
                for i, t in enumerate(FO_UNIVERSE):
                    p5.progress((i+1)/len(FO_UNIVERSE))
                    d = get_symbol_df(db5, t, "6mo", "1d", 55)
                    if d.empty or len(d) < 55: continue
                    di = compute_all_indicators(d, False)
                    if di.empty or 'EMA_20' not in di.columns: continue
                    di = compute_confluence_score(di, nifty_rs_series)
                    la = di.iloc[-1]
                    if pd.isna(la.get('Confluence_Score')): continue
                    du = bool(la['Close'] > la['EMA_20'] > la['EMA_50'])
                    dd = bool(la['Close'] < la['EMA_20'] < la['EMA_50'])
                    if not (du or dd) or not passes_regime_filter(du): continue
                    av = la.get('ATR14', np.nan)
                    if pd.isna(av) or av <= 0: continue
                    con = compute_trend_consistency(di, 5)
                    cs = la['Confluence_Score'] * 0.7 + con * 0.3
                    el = la['High']*1.0015 if du else la['Low']*0.9985
                    sl2 = la['Close']-1.5*av if du else la['Close']+1.5*av
                    ra = abs(el-sl2); tp2 = el+ra*intra_rr if du else el-ra*intra_rr
                    q, dp = position_size(capital_base, risk_per_trade, el, sl2)
                    t5.append({'Symbol': t.replace('.NS',''), 'Direction': '\U0001F7E2 UP' if du else '\U0001F534 DOWN',
                        'Score': int(round(cs)), 'Consistency': f"{con:.0f}%", 'LTP': f"\u20B9{la['Close']:.2f}",
                        'Entry': f"\u20B9{el:.2f}", 'SL': f"\u20B9{sl2:.2f}", 'Target': f"\u20B9{tp2:.2f}", 'Qty': q})
            p5.empty()
            st.session_state['intraday_top5_results'] = sorted(t5, key=lambda x: x['Score'], reverse=True)[:5] if t5 else []
            save_history("top5", st.session_state['intraday_top5_results'])
        if st.session_state.get('intraday_top5_results'):
            st.dataframe(pd.DataFrame(st.session_state['intraday_top5_results']).reset_index(drop=True), use_container_width=True)
        elif st.session_state.get('intraday_top5_results') is not None and not st.session_state.get('intraday_top5_results'):
            st.info("No stocks cleared filters.")

    with isub[3]:
        st.markdown("### \U0001F50D Intraday Chart")
        vc = [t.replace('.NS','') for t in active_tickers]
        if vc:
            sv = st.selectbox("Asset:", vc, key="ivs")
            render_chart(sv+".NS", intra_period, intra_interval, True, intra_rr, True)

    with isub[4]:
        st.markdown("### \U0001F4C5 Intraday History")
        for cat, label in [("intraday", "Scanner"), ("premarket", "Pre-Market"), ("top5", "Top 5")]:
            st.markdown(f"#### {label} History")
            hd = get_available_history_dates(cat)
            if hd:
                sd = st.selectbox("Date:", hd, key=f"ih_{cat}", format_func=lambda d: d.strftime('%a, %d %b %Y'))
                hdata = load_history(cat, sd.strftime('%Y-%m-%d'))
                if hdata: st.dataframe(pd.DataFrame(hdata), use_container_width=True)
                else: st.info("No data.")
            else: st.info(f"No {label.lower()} history yet.")
            st.markdown("---")

# === SWING ===
with tab_swing:
    ssub = st.tabs(["\U0001F4CA EOD Playbook", "\U0001F7E2 Swing Scanner", "\U0001F50D Chart", "\U0001F4C5 History"])

    with ssub[0]:
        st.markdown("### \U0001F4CA EOD Scanned Playbook")
        st.markdown("<div class='info-panel'>High-conviction swing setups. Entry Trigger = breakout stop for tomorrow.</div>", unsafe_allow_html=True)
        ce1, ce2 = st.columns(2)
        with ce1: es = st.text_input("Search:", placeholder="e.g. TATA", key="es")
        with ce2: eseg = st.selectbox("Sector:", ["All"] + list(SECTOR_MAP.keys()), key="eseg")
        if st.button("\U0001F680 Compile EOD", key="eb", type="primary"):
            tgt = active_tickers if eseg == "All" else [t.strip() for t in SECTOR_MAP[eseg].split(",")]
            pe = st.progress(0.0)
            with st.spinner(f"Scanning {len(tgt)}..."):
                rows, pers = run_universe_scan(tgt, "2y", "1d", False, False, swing_rr, swing_min_score,
                    nifty_rs_series, capital_base, risk_per_trade, passes_regime_filter, None, pe, "EOD_SWING")
                if pers: save_signals_to_db(pd.DataFrame(pers)); save_history("swing_eod", rows)
            pe.empty(); st.session_state['swing_eod_results'] = rows
        if st.session_state['swing_eod_results'] is not None:
            res = st.session_state['swing_eod_results']
            if es: res = [r for r in res if es.upper() in r['Symbol']]
            if res:
                q1, q2, q3 = st.columns(3)
                with q1: st.markdown(f"<div class='quick-stat'><div class='quick-stat-val'>{len(res)}</div><div class='quick-stat-label'>Setups</div></div>", unsafe_allow_html=True)
                with q2: st.markdown(f"<div class='quick-stat'><div class='quick-stat-val'>{sum(r['Score'] for r in res)/len(res):.0f}</div><div class='quick-stat-label'>Avg Score</div></div>", unsafe_allow_html=True)
                with q3: st.markdown(f"<div class='quick-stat'><div class='quick-stat-val'>1:{swing_rr:.1f}</div><div class='quick-stat-label'>R:R</div></div>", unsafe_allow_html=True)
                display_scan_table(res)
            else: st.info("No setups cleared filters.")

    with ssub[1]:
        st.markdown("### \U0001F7E2 Swing Scanner")
        st.markdown(f"<div class='info-panel'><b>{stf}</b> \u2022 Min Score: {swing_min_score} \u2022 R:R 1:{swing_rr:.1f}</div>", unsafe_allow_html=True)
        if st.button("\U0001F680 Run Swing Scan", key="ssb"):
            ps = st.progress(0.0)
            with st.spinner(f"Scanning {len(active_tickers)}..."):
                rows, pers = run_universe_scan(active_tickers, swing_period, swing_interval, False, False, swing_rr,
                    swing_min_score, nifty_rs_series, capital_base, risk_per_trade, passes_regime_filter, None, ps, "SWING_LIVE")
                if pers: save_signals_to_db(pd.DataFrame(pers)); save_history("swing", rows)
            ps.empty(); st.session_state['swing_scan_results'] = rows
        if st.session_state['swing_scan_results'] is not None:
            res = st.session_state['swing_scan_results']
            if res: display_scan_table(res)
            else: st.info("No swing setups found.")

    with ssub[2]:
        st.markdown("### \U0001F50D Swing Chart")
        vc2 = [t.replace('.NS','') for t in active_tickers]
        if vc2:
            sv2 = st.selectbox("Asset:", vc2, key="svs")
            render_chart(sv2+".NS", swing_period, swing_interval, False, swing_rr)

    with ssub[3]:
        st.markdown("### \U0001F4C5 Swing History")
        for cat, label in [("swing_eod", "EOD Playbook"), ("swing", "Swing Scanner")]:
            st.markdown(f"#### {label}")
            hd = get_available_history_dates(cat)
            if hd:
                sd = st.selectbox("Date:", hd, key=f"sh_{cat}", format_func=lambda d: d.strftime('%a, %d %b %Y'))
                hdata = load_history(cat, sd.strftime('%Y-%m-%d'))
                if hdata: st.dataframe(pd.DataFrame(hdata), use_container_width=True)
            else: st.info(f"No {label.lower()} history yet.")
            st.markdown("---")

# === GLOBAL TOOLS ===
with tab_tools:
    tsub = st.tabs(["\U0001F4CA FII/DII Flow", "\U0001F4C8 Backtester", "\U0001F4BE Signal Ledger"])

    with tsub[0]:
        st.markdown("### \U0001F4CA FII/DII Institutional Flow")
        st.markdown("<div class='info-panel'>Foreign & Domestic institutional net buy/sell. Helps gauge big-money direction.</div>", unsafe_allow_html=True)
        fd = fetch_fii_dii_data()
        if fd:
            try:
                if isinstance(fd, list): st.dataframe(pd.DataFrame(fd), use_container_width=True)
                else: st.json(fd)
            except Exception: st.json(fd)
        else: st.warning("FII/DII data unavailable. Check [MrChartist](https://mrchartist.com) or [NSE](https://nseindia.com).")
        fh = fetch_fii_dii_history()
        if fh:
            st.markdown("#### Last 60 Days Trend")
            try:
                if isinstance(fh, list): st.dataframe(pd.DataFrame(fh), use_container_width=True)
                else: st.json(fh)
            except Exception: st.json(fh)

    with tsub[1]:
        st.markdown("### \U0001F4C8 Backtester & Leaderboard")
        st.markdown("<div class='info-panel'>Realistic breakout-order simulation. No same-bar fills.</div>", unsafe_allow_html=True)
        bm = st.radio("Mode:", ["Swing (Daily)", "Intraday"], horizontal=True, key="bm")
        bi = bm == "Intraday"
        bint = intra_interval if bi else swing_interval; bper = intra_period if bi else swing_period; brr = intra_rr if bi else swing_rr
        cb1, cb2, cb3 = st.columns(3)
        with cb1: bks = st.text_input("Search:", key="bks")
        with cb2: bsec = st.selectbox("Sector:", ["All F&O"] + list(SECTOR_MAP.keys()), key="bsec")
        with cb3: oos = st.checkbox("OOS Only (30%)", True, key="oos")
        st.markdown(f"<div class='glass-card'><span class='metric-header'>Backtest Frame</span><br/><span style='color:#fff;font-weight:700'>{bm} \u2022 {bint} \u2022 {bper} \u2022 1:{brr:.1f}</span></div>", unsafe_allow_html=True)
        if st.button("\U0001F4CA Run Backtest", key="brb", type="primary"):
            lbd = []; btk = FO_UNIVERSE if bsec == "All F&O" else [t.strip() for t in SECTOR_MAP[bsec].split(",")]
            pb = st.progress(0.0)
            with st.spinner(f"Testing {len(btk)}..."):
                dbb = fetch_bulk_stock_data(tuple(btk), period=bper, interval=bint)
                for i, t in enumerate(btk):
                    pb.progress((i+1)/len(btk))
                    d = get_symbol_df(dbb, t, bper, bint, 40)
                    if d.empty or len(d) < 40: continue
                    dc = generate_signals_for_asset(d, 10, bi, brr, bi)
                    if dc.empty: continue
                    td = run_backtest_engine(dc, bi, bi, brr, slippage_rate, commission_rate)
                    if td.empty: continue
                    co = None
                    if oos:
                        cp = int(len(dc)*0.7)
                        co = dc.index[cp] if cp < len(dc) else None
                    met = backtest_metrics(td, co)
                    if met is None or met['total_trades'] < 3: continue
                    dc = compute_confluence_score(dc, None if bi else nifty_rs_series)
                    lp = dc.iloc[-1]
                    el = lp['Entry_Trigger'] if not pd.isna(lp.get('Entry_Trigger')) else lp['Close']
                    sl2 = lp['Stop_Loss'] if not pd.isna(lp.get('Stop_Loss')) else lp['Close']*0.99
                    tp2 = lp['Take_Profit'] if not pd.isna(lp.get('Take_Profit')) else el+(el-sl2)*brr
                    q, dp = position_size(capital_base, risk_per_trade, el, sl2)
                    lbd.append({'Ticker': t.replace('.NS',''), 'Return%': round(met['net_profit_pct'],2),
                        'WinRate%': round(met['win_rate'],2), 'Trades': met['total_trades'],
                        'MaxDD%': round(met['max_dd'],2), 'Sharpe': round(met['sharpe'],2),
                        'Score': int(round(lp.get('Confluence_Score',0) or 0)),
                        'Entry': round(float(el),2), 'SL': round(float(sl2),2), 'Target': round(float(tp2),2),
                        'Qty': q, 'R:R': f"1:{brr:.1f}"})
                pb.empty()
            st.session_state['leaderboard_results'] = sorted(lbd, key=lambda x: x['Return%'], reverse=True)[:45] if lbd else []
        if st.session_state['leaderboard_results'] is not None:
            lb = st.session_state['leaderboard_results']
            if lb:
                lbd2 = [r for r in lb if bks.upper() in r['Ticker']] if bks else lb
                if lbd2:
                    st.subheader("\U0001F3C6 Top Performers" + (" (OOS)" if oos else ""))
                    st.dataframe(pd.DataFrame(lbd2).reset_index(drop=True), use_container_width=True)
                    st.caption("Survivorship bias applies.")
            elif isinstance(lb, list): st.error("No trades matched. Adjust settings.")

    with tsub[2]:
        st.markdown("### \U0001F4BE Signal Ledger")
        st.markdown("<div class='info-panel'>Permanent log. Sync against live prices to verify win rates.</div>", unsafe_allow_html=True)
        ldb = load_signals_db()
        cd1, cd2 = st.columns(2)
        with cd1: dbs = st.text_input("Ticker:", key="dbs")
        with cd2: dbf = st.selectbox("Signal:", ["All", "BULLISH", "BEARISH"], key="dbf")
        if not ldb.empty:
            if st.button("\U0001F504 Sync Outcomes", key="dsy"):
                with st.spinner("Syncing active signals..."):
                    sync_signals_db_live(); st.success("Done."); st.rerun()
            fdb = ldb.copy()
            if dbs: fdb = fdb[fdb['Ticker'].astype(str).str.contains(dbs.upper(), na=False)]
            if dbf != "All": fdb = fdb[fdb['Signal Type'].astype(str).str.contains(dbf, na=False)]
            st.dataframe(fdb, use_container_width=True)
            if not fdb.empty and 'Outcome' in fdb.columns:
                h = fdb['Outcome'].astype(str).str.contains('Target Hit').sum()
                ms2 = fdb['Outcome'].astype(str).str.contains('Stop-Loss Hit').sum()
                rv = h + ms2
                if rv > 0:
                    s1, s2, s3, s4 = st.columns(4)
                    with s1: st.markdown(f"<div class='quick-stat'><div class='quick-stat-val'>{len(fdb)}</div><div class='quick-stat-label'>Total</div></div>", unsafe_allow_html=True)
                    with s2: st.markdown(f"<div class='quick-stat'><div class='quick-stat-val' style='color:#00ffaa'>{h}</div><div class='quick-stat-label'>Targets</div></div>", unsafe_allow_html=True)
                    with s3: st.markdown(f"<div class='quick-stat'><div class='quick-stat-val' style='color:#ff3366'>{ms2}</div><div class='quick-stat-label'>Stop Losses</div></div>", unsafe_allow_html=True)
                    wr = h/rv*100; wc = '#00ffaa' if wr >= 50 else '#ff3366'
                    with s4: st.markdown(f"<div class='quick-stat'><div class='quick-stat-val' style='color:{wc}'>{wr:.1f}%</div><div class='quick-stat-label'>Win Rate</div></div>", unsafe_allow_html=True)
            cd, cc = st.columns(2)
            with cd: st.download_button("\U0001F4E5 Download CSV", fdb.to_csv(index=False).encode('utf-8'), "signals.csv", "text/csv")
            with cc:
                if st.button("\U0001F5D1\uFE0F Clear", key="dcl"):
                    if os.path.exists(SIGNALS_FILE): os.remove(SIGNALS_FILE); st.success("Cleared."); st.rerun()
        else: st.info("Empty. Run a scan to populate.")

st.markdown("---")
st.markdown(f"<div style='text-align:center;color:#4a5078;font-size:11px;padding:10px;'>\U0001F451 QUANT-EDGE v17 \u2022 RSI \u2022 MACD \u2022 Supertrend \u2022 Multi-TF \u2022 {get_ist_now().strftime('%d %b %Y, %I:%M %p IST')}</div>", unsafe_allow_html=True)
