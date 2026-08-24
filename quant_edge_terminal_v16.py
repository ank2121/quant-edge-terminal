import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
import plotly.graph_objects as go
from datetime import datetime, timedelta, time as dtime, timezone, date
import os, json, threading, hashlib, time

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
    page_title="QUANT-EDGE v19 — Advanced One-Way Runner Terminal",
    page_icon="👑",
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
    if now.weekday() >= 5: return False
    return dtime(9, 15) <= now.time() <= dtime(15, 30)

def is_premarket():
    now = get_ist_now()
    if now.weekday() >= 5: return False
    return dtime(9, 0) <= now.time() < dtime(9, 15)

def get_market_status():
    if is_premarket(): return "🟡 PRE-MARKET", "status-premarket"
    elif is_market_hours(): return "🟢 LIVE", "status-open"
    else: return "🔴 CLOSED", "status-closed"

def get_next_trading_day_9am():
    """Returns next trading day 9:00 AM IST datetime"""
    now = get_ist_now()
    next_day = now + timedelta(days=1)
    while next_day.weekday() >= 5:  # Skip weekends
        next_day += timedelta(days=1)
    return next_day.replace(hour=9, minute=0, second=0, microsecond=0)

def is_session_expired():
    """Check if current session data should be expired"""
    now = get_ist_now()
    today = now.date()
    # If it's a weekday and past 9:00 AM, today's data is fresh
    # If it's before 9:00 AM, yesterday's data should still show
    if now.weekday() >= 5:  # Weekend - show Friday's data
        return False
    if now.time() < dtime(9, 0):  # Before 9 AM - show yesterday's data
        return False
    return True  # After 9 AM on weekday - today is a new session

def get_session_date():
    """Get the session date for data storage"""
    now = get_ist_now()
    if now.weekday() >= 5:  # Weekend
        days_since_friday = now.weekday() - 4
        return (now - timedelta(days=days_since_friday)).strftime('%Y-%m-%d')
    if now.time() < dtime(9, 0):  # Before 9 AM
        prev = now - timedelta(days=1)
        while prev.weekday() >= 5:
            prev -= timedelta(days=1)
        return prev.strftime('%Y-%m-%d')
    return now.strftime('%Y-%m-%d')

# =============================================================================
# PREMIUM GLASSMORPHISM CSS v19
# =============================================================================
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap');
    @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600&display=swap');

    .stApp {
        background: linear-gradient(160deg, #06070d 0%, #0a0d17 30%, #0d1020 60%, #0a0b14 100%);
        color: #e2e4ef; font-family: 'Inter', sans-serif;
    }
    h1, h2, h3, h4 { color: #ffffff !important; font-family: 'Inter', sans-serif; }

    .main-header {
        font-size: 28px; font-weight: 800; letter-spacing: -0.5px;
        background: linear-gradient(135deg, #00d4aa 0%, #0088ff 40%, #6c5ce7 70%, #fd79a8 100%);
        -webkit-background-clip: text; -webkit-text-fill-color: transparent;
        margin-bottom: 2px;
    }
    .sub-caption {
        font-size: 11px; color: #4a5078; margin-bottom: 12px;
        text-transform: uppercase; letter-spacing: 1px;
    }

    /* Glass Card */
    .glass-card {
        background: rgba(13, 17, 30, 0.7);
        backdrop-filter: blur(16px); -webkit-backdrop-filter: blur(16px);
        border: 1px solid rgba(255,255,255,0.05);
        border-radius: 14px; padding: 16px 18px;
        box-shadow: 0 8px 32px rgba(0,0,0,0.4);
        margin-bottom: 12px;
        transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
    }
    .glass-card:hover {
        transform: translateY(-2px);
        box-shadow: 0 12px 48px rgba(0,136,255,0.1);
        border-color: rgba(0,136,255,0.15);
    }

    /* Signal Cards */
    .signal-card {
        background: linear-gradient(135deg, rgba(13,17,30,0.9) 0%, rgba(20,26,46,0.8) 100%);
        backdrop-filter: blur(12px);
        border: 1px solid rgba(255,255,255,0.06);
        border-radius: 14px; padding: 14px 16px;
        box-shadow: 0 4px 24px rgba(0,0,0,0.35);
        margin-bottom: 10px;
        transition: all 0.3s ease;
        position: relative; overflow: hidden;
    }
    .signal-card::before {
        content: ''; position: absolute; top: 0; left: 0; right: 0; height: 2px;
    }
    .signal-card-buy::before { background: linear-gradient(90deg, #00ffaa, #00d4aa); }
    .signal-card-sell::before { background: linear-gradient(90deg, #ff3366, #ff6b6b); }
    .signal-card:hover { transform: translateY(-1px); box-shadow: 0 8px 32px rgba(0,0,0,0.4); }

    .card-symbol { font-size: 16px; font-weight: 700; color: #fff; }
    .card-score {
        font-size: 11px; font-weight: 700; padding: 3px 10px; border-radius: 20px;
        display: inline-block;
    }
    .score-high { background: rgba(0,255,170,0.15); color: #00ffaa; border: 1px solid rgba(0,255,170,0.3); }
    .score-med { background: rgba(255,170,0,0.15); color: #ffaa00; border: 1px solid rgba(255,170,0,0.3); }
    .score-low { background: rgba(255,51,102,0.15); color: #ff3366; border: 1px solid rgba(255,51,102,0.3); }
    .card-setup { font-size: 12px; font-weight: 600; margin: 4px 0; }
    .card-prices { font-size: 11px; color: #8b90b0; font-family: 'JetBrains Mono', monospace; }
    .card-meta { font-size: 10px; color: #4a5078; margin-top: 6px; }
    .card-status { font-size: 11px; font-weight: 700; margin-top: 4px; }
    .card-indicators { font-size: 10px; color: #6b7294; margin-top: 4px; }
    .card-warning { font-size: 10px; color: #ffaa00; margin-top: 2px; }

    /* Metric Cards */
    .metric-card {
        background: linear-gradient(135deg, rgba(13,17,30,0.85) 0%, rgba(22,28,52,0.7) 100%);
        backdrop-filter: blur(10px);
        border: 1px solid rgba(255,255,255,0.05);
        border-radius: 12px; padding: 14px 16px;
        box-shadow: 0 4px 20px rgba(0,0,0,0.3); margin-bottom: 10px;
    }
    .metric-header { font-size: 9px; color: #4a5078; text-transform: uppercase; letter-spacing: 1.5px; font-weight: 700; }
    .metric-val { font-size: 22px; font-weight: 700; color: #ffffff; margin-top: 2px; }
    .metric-change { font-size: 11px; font-weight: 600; margin-top: 1px; }
    .change-up { color: #00ffaa; }
    .change-down { color: #ff3366; }

    /* Market Status */
    .market-status {
        display: inline-flex; align-items: center; gap: 6px;
        padding: 5px 14px; border-radius: 20px;
        font-size: 10px; font-weight: 800; letter-spacing: 1px; text-transform: uppercase;
    }
    .status-open { background: rgba(0,255,170,0.1); color: #00ffaa; border: 1px solid rgba(0,255,170,0.25); }
    .status-closed { background: rgba(255,51,102,0.1); color: #ff3366; border: 1px solid rgba(255,51,102,0.25); }
    .status-premarket { background: rgba(255,170,0,0.1); color: #ffaa00; border: 1px solid rgba(255,170,0,0.25); }

    /* Regime Banner */
    .regime-banner {
        padding: 10px 18px; border-radius: 10px; font-weight: 700; font-size: 12px;
        margin-bottom: 14px; letter-spacing: 0.3px; text-align: center;
        backdrop-filter: blur(10px);
    }
    .regime-bull { background: rgba(0,255,170,0.08); color: #00ffaa; border: 1px solid rgba(0,255,170,0.2); }
    .regime-bear { background: rgba(255,51,102,0.08); color: #ff3366; border: 1px solid rgba(255,51,102,0.2); }
    .regime-choppy { background: rgba(255,170,0,0.08); color: #ffaa00; border: 1px solid rgba(255,170,0,0.2); }

    /* Hero Banner */
    .hero-banner {
        background: linear-gradient(135deg, rgba(255,215,0,0.1) 0%, rgba(255,140,0,0.08) 100%);
        border: 1px solid rgba(255,215,0,0.25);
        padding: 12px 18px; border-radius: 10px; font-weight: 700; font-size: 13px;
        margin-bottom: 14px; text-align: center; color: #FFD700;
        box-shadow: 0 4px 24px rgba(255,215,0,0.06);
    }

    /* Tabs */
    .stTabs [data-baseweb="tab-list"] {
        gap: 4px; background: rgba(13,17,30,0.6); padding: 3px 5px; border-radius: 10px;
    }
    .stTabs [data-baseweb="tab"] {
        border-radius: 8px; padding: 8px 18px; font-weight: 600; font-size: 12px;
    }

    .last-updated { font-size: 10px; color: #3a4060; text-align: right; font-style: italic; margin-top: -6px; margin-bottom: 10px; }

    .info-panel {
        background: rgba(0,136,255,0.06); border: 1px solid rgba(0,136,255,0.15);
        border-radius: 8px; padding: 10px 14px; margin-bottom: 12px;
        color: #7ba8d4; font-size: 12px;
    }

    .scan-status {
        padding: 6px 14px; border-radius: 8px; font-size: 11px; font-weight: 600;
        display: inline-flex; align-items: center; gap: 6px; margin-bottom: 8px;
    }
    .scan-running { background: rgba(0,136,255,0.1); color: #4da6ff; border: 1px solid rgba(0,136,255,0.2); }
    .scan-complete { background: rgba(0,255,170,0.1); color: #00ffaa; border: 1px solid rgba(0,255,170,0.2); }
    .scan-idle { background: rgba(100,100,140,0.1); color: #6b7294; border: 1px solid rgba(100,100,140,0.2); }

    .view-toggle {
        display: flex; gap: 4px; margin-bottom: 10px;
    }
    .pivot-badge {
        font-size: 9px; padding: 2px 8px; border-radius: 12px; font-weight: 600;
        display: inline-block; margin-right: 4px;
    }
    .pivot-above { background: rgba(0,255,170,0.12); color: #00ffaa; }
    .pivot-below { background: rgba(255,51,102,0.12); color: #ff3366; }
    .pivot-near { background: rgba(255,170,0,0.12); color: #ffaa00; }
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
    "🏦 Banking": "BANKBARODA.NS,FEDERALBNK.NS,HDFCBANK.NS,ICICIBANK.NS,KOTAKBANK.NS,SBIN.NS,AXISBANK.NS,AUBANK.NS",
    "💻 IT": "COFORGE.NS,HCLTECH.NS,INFY.NS,LTIM.NS,LTTS.NS,MPHASIS.NS,PERSISTENT.NS,TCS.NS,TECHM.NS,WIPRO.NS",
    "🚗 Auto": "ASHOKLEY.NS,BAJAJ-AUTO.NS,EICHERMOT.NS,HEROMOTOCO.NS,M&M.NS,MARUTI.NS,TATAMOTORS.NS,TVSMOTOR.NS",
    "⚡ Power": "ADANIPOWER.NS,BPCL.NS,COALINDIA.NS,JSWENERGY.NS,NTPC.NS,ONGC.NS,POWERGRID.NS,TATAPOWER.NS",
    "🏗️ Metal": "ACC.NS,AMBUJACEM.NS,GRASIM.NS,JINDALSTEL.NS,JSWSTEEL.NS,LT.NS,TATASTEEL.NS,ULTRACEMCO.NS",
    "💊 Pharma": "ALKEM.NS,APOLLOHOSP.NS,CIPLA.NS,DIVISLAB.NS,DRREDDY.NS,IPCALAB.NS,LUPIN.NS,SUNPHARMA.NS,ZYDUSLIFE.NS",
    "🛒 FMCG": "ABFRL.NS,BRITANNIA.NS,COLPAL.NS,DABUR.NS,DMART.NS,GODREJCP.NS,HINDUNILVR.NS,ITC.NS,NESTLEIND.NS,TRENT.NS"
}

# =============================================================================
# PERSISTENT STORAGE SYSTEM
# =============================================================================
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
SESSIONS_DIR = os.path.join(DATA_DIR, "sessions")
SCANS_DIR = os.path.join(DATA_DIR, "scans")
SETTINGS_FILE = os.path.join(DATA_DIR, "user_settings.json")
os.makedirs(SESSIONS_DIR, exist_ok=True)
os.makedirs(SCANS_DIR, exist_ok=True)

def save_settings(settings):
    try:
        with open(SETTINGS_FILE, 'w') as f: json.dump(settings, f, indent=2)
    except Exception: pass

def load_settings():
    try:
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE, 'r') as f: return json.load(f)
    except Exception: pass
    return {"capital": 200000, "risk_pct": 1.0}

def save_session_results(scan_key, results, scan_time=None):
    """Save scan results to disk — persists across page refreshes"""
    if not results: return
    session_date = get_session_date()
    filepath = os.path.join(SESSIONS_DIR, f"{scan_key}_{session_date}.json")
    payload = {
        "scan_key": scan_key,
        "session_date": session_date,
        "scan_time": scan_time or get_ist_now().strftime('%Y-%m-%d %H:%M:%S'),
        "results": results
    }
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(payload, f, indent=2, default=str)
    except Exception: pass

def load_session_results(scan_key):
    """Load today's scan results from disk"""
    session_date = get_session_date()
    filepath = os.path.join(SESSIONS_DIR, f"{scan_key}_{session_date}.json")
    try:
        if os.path.exists(filepath):
            with open(filepath, 'r', encoding='utf-8') as f:
                payload = json.load(f)
            return payload.get("results", []), payload.get("scan_time", "")
    except Exception: pass
    return None, None

def cleanup_old_sessions():
    """Remove session files older than 3 days"""
    try:
        cutoff = get_ist_now() - timedelta(days=3)
        for fname in os.listdir(SESSIONS_DIR):
            fpath = os.path.join(SESSIONS_DIR, fname)
            if os.path.isfile(fpath):
                mtime = datetime.fromtimestamp(os.path.getmtime(fpath), tz=IST)
                if mtime < cutoff:
                    os.remove(fpath)
    except Exception: pass

# Background scan status tracking (file-based for thread safety)
def write_scan_status(scan_key, status, progress=0, error=""):
    fpath = os.path.join(SCANS_DIR, f"{scan_key}_status.json")
    try:
        with open(fpath, 'w') as f:
            json.dump({"status": status, "progress": progress, "error": error,
                        "time": get_ist_now().strftime('%H:%M:%S')}, f)
    except Exception: pass

def read_scan_status(scan_key):
    fpath = os.path.join(SCANS_DIR, f"{scan_key}_status.json")
    try:
        if os.path.exists(fpath):
            with open(fpath, 'r') as f: return json.load(f)
    except Exception: pass
    return {"status": "idle", "progress": 0}

cleanup_old_sessions()

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
                ltp = float(df['Close'].iloc[-1]); prev = float(df['Close'].iloc[-2])
                res[name] = {"LTP": ltp, "Chg": ltp - prev, "Pct": ((ltp - prev) / prev) * 100}
            elif not df.empty:
                res[name] = {"LTP": float(df['Close'].iloc[-1]), "Chg": 0.0, "Pct": 0.0}
        except Exception: res[name] = {"LTP": 0.0, "Chg": 0.0, "Pct": 0.0}
    return res

@st.cache_data(ttl=900)
def fetch_stock_data(ticker, period="3y", interval="1d"):
    try:
        data = yf.download(ticker, period=period, interval=interval, progress=False, auto_adjust=True)
        if isinstance(data.columns, pd.MultiIndex): data.columns = data.columns.get_level_values(0)
        return data.dropna(subset=['Close'])
    except Exception: return pd.DataFrame()

@st.cache_data(ttl=900)
def fetch_bulk_data(tickers_tuple, period="1y", interval="1d"):
    try:
        return yf.download(list(tickers_tuple), period=period, interval=interval,
                           group_by="ticker", progress=False, auto_adjust=True)
    except Exception: return pd.DataFrame()

def get_symbol_df(bulk_df, ticker, fb_period, fb_interval, min_len=50):
    df = pd.DataFrame()
    if not bulk_df.empty:
        try:
            if isinstance(bulk_df.columns, pd.MultiIndex):
                if ticker in bulk_df.columns.get_level_values(0):
                    df = bulk_df[ticker].dropna(how='all')
            else: df = bulk_df.dropna(how='all')
        except Exception: pass
    if df.empty or len(df) < min_len:
        df = fetch_stock_data(ticker, period=fb_period, interval=fb_interval)
    return df

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

# =============================================================================
# INDICATOR ENGINE
# =============================================================================
def compute_rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0); loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1.0/period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0/period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def compute_macd(series, fast=12, slow=26, signal=9):
    ema_f = series.ewm(span=fast, adjust=False).mean()
    ema_s = series.ewm(span=slow, adjust=False).mean()
    macd = ema_f - ema_s
    sig = macd.ewm(span=signal, adjust=False).mean()
    return macd, sig, macd - sig

def compute_supertrend(df, period=10, multiplier=2.0):
    if df.empty or len(df) < period + 1:
        df['Supertrend'] = np.nan; df['ST_Dir'] = 0; return df
    h = df['High'].values.astype(float); l = df['Low'].values.astype(float); c = df['Close'].values.astype(float)
    pc = np.roll(c, 1); pc[0] = c[0]
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    atr = pd.Series(tr).ewm(span=period, adjust=False).mean().values
    hl2 = (h + l) / 2.0; upper = hl2 + multiplier * atr; lower = hl2 - multiplier * atr
    direction = np.ones(len(df), dtype=int); st_val = np.full(len(df), np.nan)
    for i in range(1, len(df)):
        if lower[i] < lower[i-1] and c[i-1] > lower[i-1]: lower[i] = lower[i-1]
        if upper[i] > upper[i-1] and c[i-1] < upper[i-1]: upper[i] = upper[i-1]
        if c[i] > upper[i]: direction[i] = 1
        elif c[i] < lower[i]: direction[i] = -1
        else: direction[i] = direction[i-1]
        st_val[i] = lower[i] if direction[i] == 1 else upper[i]
    df['Supertrend'] = st_val; df['ST_Dir'] = direction
    return df

def add_atr(df, period=14):
    if df.empty or len(df) < period + 1: df['ATR14'] = np.nan; return df
    hl = df['High'] - df['Low']
    hc = (df['High'] - df['Close'].shift(1)).abs()
    lc = (df['Low'] - df['Close'].shift(1)).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    df['ATR14'] = tr.ewm(span=period, adjust=False).mean()
    return df

def compute_all_indicators(df, is_intraday=False):
    if df.empty: return df
    df = df.copy()
    if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
    if len(df) < 50: return df
    df['EMA_9'] = df['Close'].ewm(span=9, adjust=False).mean()
    df['EMA_20'] = df['Close'].ewm(span=20, adjust=False).mean()
    df['EMA_50'] = df['Close'].ewm(span=50, adjust=False).mean()
    df = add_atr(df)
    df['RSI_14'] = compute_rsi(df['Close'], 14)
    df['MACD'], df['MACD_Signal'], df['MACD_Hist'] = compute_macd(df['Close'])
    df = compute_supertrend(df, period=10, multiplier=2.0)
    df['Vol_MA_20'] = df['Volume'].rolling(20).mean()
    df['Inst_Vol_Spike'] = df['Volume'] > (df['Vol_MA_20'] * 1.5)

    # Accumulation/Distribution Line
    mfm = ((df['Close'] - df['Low']) - (df['High'] - df['Close'])) / (df['High'] - df['Low']).replace(0, np.nan)
    mfv = mfm.fillna(0) * df['Volume']
    df['ADL'] = mfv.cumsum()

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
# PIVOT CALCULATOR (Weekly + Monthly)
# =============================================================================
def compute_weekly_pivot(df_daily):
    """Compute weekly pivot from previous week's data. New pivot every Monday."""
    if df_daily.empty or len(df_daily) < 10: return {}
    df = df_daily.copy()
    df['Week'] = df.index.isocalendar().week.values
    df['Year'] = df.index.year
    weeks = df.groupby(['Year', 'Week']).agg({'High': 'max', 'Low': 'min', 'Close': 'last'})
    if len(weeks) < 2: return {}
    prev = weeks.iloc[-2]
    pivot = (prev['High'] + prev['Low'] + prev['Close']) / 3
    r1 = 2 * pivot - prev['Low']
    s1 = 2 * pivot - prev['High']
    r2 = pivot + (prev['High'] - prev['Low'])
    s2 = pivot - (prev['High'] - prev['Low'])
    r3 = prev['High'] + 2 * (pivot - prev['Low'])
    s3 = prev['Low'] - 2 * (prev['High'] - pivot)
    return {"P": pivot, "R1": r1, "R2": r2, "R3": r3, "S1": s1, "S2": s2, "S3": s3}

def compute_monthly_pivot(df_daily):
    """Compute monthly pivot from previous month's data. New on 1st trading day of month."""
    if df_daily.empty or len(df_daily) < 30: return {}
    df = df_daily.copy()
    df['Month'] = df.index.month; df['Year'] = df.index.year
    months = df.groupby(['Year', 'Month']).agg({'High': 'max', 'Low': 'min', 'Close': 'last'})
    if len(months) < 2: return {}
    prev = months.iloc[-2]
    pivot = (prev['High'] + prev['Low'] + prev['Close']) / 3
    r1 = 2 * pivot - prev['Low']
    s1 = 2 * pivot - prev['High']
    r2 = pivot + (prev['High'] - prev['Low'])
    s2 = pivot - (prev['High'] - prev['Low'])
    return {"P": pivot, "R1": r1, "R2": r2, "S1": s1, "S2": s2}

def get_pivot_context(price, pivots):
    """Determine price position relative to pivot levels"""
    if not pivots: return "❓", "unknown"
    p = pivots.get("P", 0)
    r1 = pivots.get("R1", p * 1.01)
    s1 = pivots.get("S1", p * 0.99)
    atr_band = abs(r1 - p) * 0.15  # Near = within 15% of R1-P range

    if abs(price - p) < atr_band: return "🟡 Near Pivot", "near"
    elif price > r1: return "🟢 Above R1", "above"
    elif price > p: return "🟢 Above Pivot", "above"
    elif price < s1: return "🔴 Below S1", "below"
    elif price < p: return "🔴 Below Pivot", "below"
    return "🟡 At Pivot", "near"

# =============================================================================
# VOLUME PROFILE ENGINE
# =============================================================================
def compute_volume_profile(df, lookback=20, num_bins=15):
    """Compute POC, VAH, VAL from volume-at-price distribution"""
    if df.empty or len(df) < lookback:
        return {"POC": np.nan, "VAH": np.nan, "VAL": np.nan}
    recent = df.tail(lookback)
    price_min = float(recent['Low'].min())
    price_max = float(recent['High'].max())
    if price_max <= price_min:
        return {"POC": price_min, "VAH": price_min, "VAL": price_min}

    bins = np.linspace(price_min, price_max, num_bins + 1)
    bin_centers = (bins[:-1] + bins[1:]) / 2
    vol_profile = np.zeros(num_bins)

    for _, row in recent.iterrows():
        for i in range(num_bins):
            if row['Low'] <= bin_centers[i] <= row['High']:
                vol_profile[i] += row['Volume']

    # POC = highest volume price level
    poc_idx = np.argmax(vol_profile)
    poc = float(bin_centers[poc_idx])

    # Value Area = 70% of total volume
    total_vol = vol_profile.sum()
    if total_vol == 0:
        return {"POC": poc, "VAH": price_max, "VAL": price_min}

    sorted_idx = np.argsort(vol_profile)[::-1]
    cum_vol = 0
    va_indices = []
    for idx in sorted_idx:
        cum_vol += vol_profile[idx]
        va_indices.append(idx)
        if cum_vol >= 0.7 * total_vol:
            break

    vah = float(bin_centers[max(va_indices)])
    val = float(bin_centers[min(va_indices)])
    return {"POC": poc, "VAH": vah, "VAL": val}

def get_vp_context(price, vp):
    """Price position relative to volume profile"""
    if not vp or np.isnan(vp.get("POC", np.nan)):
        return 0
    poc = vp["POC"]; vah = vp["VAH"]; val = vp["VAL"]
    # Score: institutional alignment
    score = 0
    if abs(price - poc) / poc < 0.005:  # Within 0.5% of POC
        score += 5  # At institutional equilibrium
    if price > vah:  # Breakout above value area
        score += 3
    elif price < val:  # Breakdown below value area
        score += 3
    elif val <= price <= vah:  # Inside value area
        score += 1
    return score

# =============================================================================
# MULTI-TIMEFRAME CONFLUENCE ENGINE
# =============================================================================
def compute_mtf_confluence_intraday(ticker, daily_df=None):
    """
    Intraday: 15m + 30m + 1h confluence
    Returns: direction (+1 bull, -1 bear, 0 neutral), score_boost, details
    """
    tf_data = {}
    directions = {}

    # Fetch each timeframe
    for tf_label, (interval, period) in [("15m", ("15m", "30d")), ("30m", ("30m", "60d")), ("1h", ("60m", "60d"))]:
        try:
            df = yf.download(ticker, period=period, interval=interval, progress=False, auto_adjust=True)
            if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
            if df.empty or len(df) < 50: continue
            df = compute_all_indicators(df, is_intraday=True)
            if 'EMA_20' not in df.columns or 'EMA_50' not in df.columns: continue
            last = df.iloc[-1]
            bull = last['Close'] > last['EMA_20'] and last['EMA_20'] > last['EMA_50']
            bear = last['Close'] < last['EMA_20'] and last['EMA_20'] < last['EMA_50']
            d = 1 if bull else (-1 if bear else 0)
            directions[tf_label] = d
            tf_data[tf_label] = df
        except Exception:
            continue

    if not directions: return 0, 0, {}

    # Check confluence
    vals = list(directions.values())
    if all(v == 1 for v in vals):
        return 1, 20, directions  # All bullish
    elif all(v == -1 for v in vals):
        return -1, 20, directions  # All bearish
    elif sum(v == 1 for v in vals) >= 2:
        return 1, 10, directions  # Majority bullish
    elif sum(v == -1 for v in vals) >= 2:
        return -1, 10, directions  # Majority bearish
    return 0, 0, directions

def compute_mtf_confluence_swing(ticker, daily_df=None):
    """
    Swing: 1h + 1D confluence
    Returns: direction, score_boost, details
    """
    directions = {}

    for tf_label, (interval, period) in [("1h", ("60m", "60d")), ("1d", ("1d", "2y"))]:
        try:
            if tf_label == "1d" and daily_df is not None and not daily_df.empty:
                df = daily_df.copy()
            else:
                df = yf.download(ticker, period=period, interval=interval, progress=False, auto_adjust=True)
            if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
            if df.empty or len(df) < 50: continue
            df = compute_all_indicators(df, is_intraday=(tf_label != "1d"))
            if 'EMA_20' not in df.columns: continue
            last = df.iloc[-1]
            bull = last['Close'] > last['EMA_20'] and last['EMA_20'] > last['EMA_50']
            bear = last['Close'] < last['EMA_20'] and last['EMA_20'] < last['EMA_50']
            directions[tf_label] = 1 if bull else (-1 if bear else 0)
        except Exception:
            continue

    if not directions: return 0, 0, {}
    vals = list(directions.values())
    if all(v == 1 for v in vals): return 1, 20, directions
    elif all(v == -1 for v in vals): return -1, 20, directions
    elif len(vals) == 1: return vals[0], 8, directions
    return 0, 0, directions

# =============================================================================
# PSYCHOLOGICAL & INSTITUTIONAL ENGINE
# =============================================================================
def compute_psychology(df, daily_dir=None, is_intraday=True):
    df = df.copy()
    df['Psychology'] = "—"
    if 'VWAP' not in df.columns or 'Vol_MA_20' not in df.columns or df.empty: return df

    for idx in df.index[-min(20, len(df)):]:
        row = df.loc[idx]
        close = row['Close']; vwap = row['VWAP']; vol = row['Volume']; avg_vol = row['Vol_MA_20']
        if pd.isna(vwap) or pd.isna(avg_vol) or avg_vol == 0: continue
        rv = vol / avg_vol
        mu = (daily_dir == 1) if daily_dir is not None else (row.get('EMA_20', 0) > row.get('EMA_50', 0))
        md = (daily_dir == -1) if daily_dir is not None else (row.get('EMA_20', 0) < row.get('EMA_50', 0))

        if mu:
            if close < vwap:
                if rv < 0.8: df.at[idx, 'Psychology'] = "🟢 Discount Buy"
                elif rv > 2.0: df.at[idx, 'Psychology'] = "🔴 Trapped Bulls"
                else: df.at[idx, 'Psychology'] = "🟡 Dip Zone"
            else:
                if rv > 2.0: df.at[idx, 'Psychology'] = "🟢 Insti Buying"
                else: df.at[idx, 'Psychology'] = "🟢 Trend Ride"
        elif md:
            if close > vwap:
                if rv < 0.8: df.at[idx, 'Psychology'] = "🔴 Premium Short"
                elif rv > 2.0: df.at[idx, 'Psychology'] = "🟢 Bear Trap"
                else: df.at[idx, 'Psychology'] = "🟡 Rally Sell"
            else:
                if rv > 2.0: df.at[idx, 'Psychology'] = "🔴 Insti Selling"
                else: df.at[idx, 'Psychology'] = "🔴 Trend Down"
    return df

# =============================================================================
# ONE-WAY RUNNER ENGINE (Advanced)
# =============================================================================
def detect_one_way_runner(df, is_intraday=True):
    df = df.copy()
    df['Is_Runner'] = False; df['Runner_Type'] = ""
    if df.empty or len(df) < 5 or 'VWAP' not in df.columns: return df

    if is_intraday:
        df['DG'] = df.index.date
        first_idx = df.groupby('DG').head(1).index
        for idx in first_idx:
            try:
                dg = df.loc[idx, 'DG']
                day = df[df['DG'] == dg]
                if len(day) < 3: continue
                fb = day.iloc[0]; avg_vol = fb.get('Vol_MA_20', 1)
                if pd.isna(avg_vol) or avg_vol == 0: continue
                rvol = fb['Volume'] / avg_vol

                # First candle % move
                fc_pct = abs((fb['Close'] - fb['Open']) / fb['Open']) * 100
                is_extended = fc_pct > 0.70

                if rvol > 2.0 or abs((fb['Open'] - day['Close'].shift(1).get(day.index[0], fb['Open'])) / max(fb['Open'], 1)) > 0.003:
                    bull_run = True; bear_run = True
                    for _, r in day.iterrows():
                        if r['Low'] < r.get('VWAP', r['EMA_20']) * 0.998: bull_run = False
                        if r['High'] > r.get('VWAP', r['EMA_20']) * 1.002: bear_run = False

                    if bull_run and day.iloc[-1]['Close'] > fb['High']:
                        df.loc[day.index[-1], 'Is_Runner'] = True
                        lbl = "⚡ BULL RUNNER"
                        if is_extended: lbl += " ⚠️"
                        df.loc[day.index[-1], 'Runner_Type'] = lbl
                    elif bear_run and day.iloc[-1]['Close'] < fb['Low']:
                        df.loc[day.index[-1], 'Is_Runner'] = True
                        lbl = "⚡ BEAR RUNNER"
                        if is_extended: lbl += " ⚠️"
                        df.loc[day.index[-1], 'Runner_Type'] = lbl
            except Exception: pass
        df.drop(columns=['DG'], inplace=True, errors='ignore')
    else:
        lookback = 5
        recent = df.iloc[-lookback:]
        if len(recent) == lookback:
            sb = recent.iloc[0]
            rv = sb['Volume'] / sb['Vol_MA_20'] if sb.get('Vol_MA_20', 0) else 1
            if rv > 1.8:
                bull = all(r['Close'] > r.get('EMA_20', 0) for _, r in recent.iterrows()) and recent.iloc[-1]['Close'] > sb['High']
                bear = all(r['Close'] < r.get('EMA_20', 0) for _, r in recent.iterrows()) and recent.iloc[-1]['Close'] < sb['Low']
                if bull:
                    df.loc[df.index[-1], 'Is_Runner'] = True
                    df.loc[df.index[-1], 'Runner_Type'] = "⚡ SWING BULL"
                elif bear:
                    df.loc[df.index[-1], 'Is_Runner'] = True
                    df.loc[df.index[-1], 'Runner_Type'] = "⚡ SWING BEAR"
    return df

# =============================================================================
# MARKET REGIME
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

# =============================================================================
# CONFLUENCE SCORING (Enhanced with Volume Profile + Institutional + Pivots)
# =============================================================================
def compute_confluence_score(df, nifty_rs=None, is_intraday=False, daily_dir=None,
                              pivots=None, vp=None, mtf_boost=0, window=21):
    df = df.copy()
    if 'EMA_20' not in df.columns or 'ATR14' not in df.columns:
        df['Score'] = np.nan; return df

    # 1. Multi-TF Trend (20 pts from mtf_boost already calculated)
    trend_up = (df['Close'] > df['EMA_20']) & (df['EMA_20'] > df['EMA_50'])
    trend_down = (df['Close'] < df['EMA_20']) & (df['EMA_20'] < df['EMA_50'])
    trend_score = np.where(trend_up | trend_down, 15, 3)

    # 2. Supertrend alignment (15 pts)
    if 'ST_Dir' in df.columns:
        st_up = trend_up & (df['ST_Dir'] == 1)
        st_down = trend_down & (df['ST_Dir'] == -1)
        st_score = np.where(st_up | st_down, 15, np.where(trend_up | trend_down, 5, 0))
    else: st_score = np.full(len(df), 3.0)

    # 3. Pivot Position (15 pts)
    pivot_score = np.full(len(df), 5.0)
    if pivots:
        p = pivots.get("P", 0)
        r1 = pivots.get("R1", p); s1 = pivots.get("S1", p)
        for i in range(len(df)):
            close = float(df['Close'].iloc[i])
            # Bullish: price above pivot & trend up
            if close > p and (trend_up.iloc[i] if hasattr(trend_up, 'iloc') else False):
                pivot_score[i] = 15 if close > r1 else 12
            elif close < p and (trend_down.iloc[i] if hasattr(trend_down, 'iloc') else False):
                pivot_score[i] = 15 if close < s1 else 12
            elif abs(close - p) / max(p, 1) < 0.003:
                pivot_score[i] = 10  # At pivot — decision zone

    # 4. Volume Spike (15 pts)
    if 'Vol_MA_20' in df.columns:
        vol_ratio = (df['Volume'] / df['Vol_MA_20'].replace(0, np.nan)).clip(upper=4.0).fillna(0)
        volume_score = (vol_ratio / 4.0 * 15).clip(0, 15)
    else: volume_score = np.full(len(df), 3.0)

    # 5. RSI Room (10 pts)
    if 'RSI_14' in df.columns:
        rsi = df['RSI_14'].fillna(50)
        # BUY: RSI 40-65 ideal, SELL: RSI 35-60 ideal
        rsi_score = np.where((rsi >= 35) & (rsi <= 65), 10,
                    np.where((rsi >= 25) & (rsi <= 75), 6, 2))
    else: rsi_score = np.full(len(df), 5.0)

    # 6. MACD Confirm (10 pts)
    if 'MACD_Hist' in df.columns:
        macd_up = trend_up & (df['MACD_Hist'] > 0)
        macd_down = trend_down & (df['MACD_Hist'] < 0)
        macd_score = np.where(macd_up | macd_down, 10, 2)
    else: macd_score = np.full(len(df), 3.0)

    # 7. VWAP/EMA Anchor (10 pts)
    atr_safe = df['ATR14'].replace(0, np.nan).fillna(1)
    if is_intraday and 'VWAP' in df.columns:
        vwap_dist = ((df['Close'] - df['VWAP']).abs() / atr_safe).clip(upper=3.0).fillna(3)
        anchor_score = ((3.0 - vwap_dist) / 3.0 * 10).clip(0, 10)
    else:
        dist = ((df['Close'] - df['EMA_20']).abs() / atr_safe).clip(upper=3.0).fillna(3)
        anchor_score = ((3.0 - dist) / 3.0 * 10).clip(0, 10)

    # 8. Relative Strength (5 pts)
    if nifty_rs is not None:
        stock_ret = df['Close'].pct_change(window) * 100.0
        rs = (stock_ret - nifty_rs.reindex(df.index).ffill()).clip(-10, 10).fillna(0)
        rs_score = ((rs + 10) / 20 * 5).clip(0, 5)
    else: rs_score = np.full(len(df), 2.5)

    # 9. Volume Profile bonus (5 pts)
    vp_score_val = np.full(len(df), 0.0)
    if vp and not np.isnan(vp.get("POC", np.nan)):
        last_price = float(df['Close'].iloc[-1])
        vp_s = get_vp_context(last_price, vp)
        vp_score_val[-1] = min(vp_s, 5)

    # 10. Institutional ADL confirmation (5 pts)
    adl_score = np.full(len(df), 0.0)
    if 'ADL' in df.columns:
        adl_trend = df['ADL'].diff(5).fillna(0)
        adl_up = trend_up & (adl_trend > 0)
        adl_down = trend_down & (adl_trend < 0)
        adl_score = np.where(adl_up | adl_down, 5, 0).astype(float)

    # MTF boost
    mtf_arr = np.full(len(df), 0.0)
    mtf_arr[-1] = min(mtf_boost, 20)

    total = (trend_score + st_score + pivot_score + volume_score + rsi_score +
             macd_score + anchor_score + rs_score + vp_score_val + adl_score + mtf_arr)
    df['Score'] = pd.Series(total, index=df.index).clip(0, 100)
    return df

# =============================================================================
# SIGNAL GENERATION
# =============================================================================
def generate_signals(df, lookback=10, is_intraday=False, rr=2.0, use_orb=False):
    if df.empty or len(df) < max(50, lookback) + 5:
        for c in ['Bull_Sweep', 'Bear_Sweep', 'Bull_ORB', 'Bear_ORB']: df[c] = False
        for c in ['Entry', 'SL', 'TP']: df[c] = np.nan
        df['FC_Pct'] = np.nan
        return df

    df = compute_all_indicators(df, is_intraday=is_intraday)
    if df.empty or 'EMA_50' not in df.columns: return df

    df['Prev_Low_L'] = df['Low'].shift(1).rolling(lookback).min()
    df['Prev_High_H'] = df['High'].shift(1).rolling(lookback).max()
    df['Bull_Sweep'] = (df['Close'] > df['EMA_50']) & (df['Low'] < df['Prev_Low_L']) & (df['Close'] > df['Prev_Low_L']) & (df['Volume'] > df['Vol_MA_20'])
    df['Bear_Sweep'] = (df['Close'] < df['EMA_50']) & (df['High'] > df['Prev_High_H']) & (df['Close'] < df['Prev_High_H']) & (df['Volume'] > df['Vol_MA_20'])

    # ORB with first-candle 0.70% check
    df['Bull_ORB'] = False; df['Bear_ORB'] = False; df['FC_Pct'] = np.nan
    if use_orb and is_intraday:
        df['DG'] = df.index.date
        df['ORB_H'] = df.groupby('DG')['High'].transform('first')
        df['ORB_L'] = df.groupby('DG')['Low'].transform('first')
        df['ORB_O'] = df.groupby('DG')['Open'].transform('first')
        df['ORB_C'] = df.groupby('DG')['Close'].transform('first')

        # First candle % move
        df['FC_Pct'] = ((df['ORB_C'] - df['ORB_O']).abs() / df['ORB_O'].replace(0, np.nan) * 100)

        candle_gt_0 = df.groupby('DG').cumcount() > 0
        df['Bull_ORB'] = candle_gt_0 & (df['Close'] > df['ORB_H']) & (df['Close'].shift(1) <= df['ORB_H'])
        df['Bear_ORB'] = candle_gt_0 & (df['Close'] < df['ORB_L']) & (df['Close'].shift(1) >= df['ORB_L'])
        df.drop(columns=['DG', 'ORB_H', 'ORB_L', 'ORB_O', 'ORB_C'], errors='ignore', inplace=True)

    buy_mask = df['Bull_Sweep'] | df['Bull_ORB']
    sell_mask = df['Bear_Sweep'] | df['Bear_ORB']
    df['Entry'] = np.nan; df['SL'] = np.nan; df['TP'] = np.nan

    df.loc[buy_mask, 'Entry'] = df.loc[buy_mask, 'High'] * 1.0015
    df.loc[buy_mask, 'SL'] = df.loc[buy_mask, 'Low'] * 0.995
    risk_b = df.loc[buy_mask, 'Entry'] - df.loc[buy_mask, 'SL']
    df.loc[buy_mask, 'TP'] = df.loc[buy_mask, 'Entry'] + risk_b * rr

    df.loc[sell_mask, 'Entry'] = df.loc[sell_mask, 'Low'] * 0.9985
    df.loc[sell_mask, 'SL'] = df.loc[sell_mask, 'High'] * 1.005
    risk_s = df.loc[sell_mask, 'SL'] - df.loc[sell_mask, 'Entry']
    df.loc[sell_mask, 'TP'] = df.loc[sell_mask, 'Entry'] - risk_s * rr

    df.drop(columns=['Prev_Low_L', 'Prev_High_H'], errors='ignore', inplace=True)
    return df

# =============================================================================
# POSITION SIZING
# =============================================================================
def position_size(capital, risk_pct, entry, stop):
    risk_amt = capital * (risk_pct / 100.0)
    per_share = abs(entry - stop)
    if per_share <= 0 or capital <= 0: return 0, 0.0
    qty = int(risk_amt // per_share)
    return qty, qty * entry

# =============================================================================
# SIGNAL STATUS HELPER
# =============================================================================
def get_signal_status(live_price, entry, sl, tp, is_bull):
    if pd.isna(live_price): return "⏳ Loading", "#6b7294"
    if is_bull:
        if live_price >= tp: return "✅ TARGET", "#00ffaa"
        elif live_price <= sl: return "❌ SL HIT", "#ff3366"
        elif live_price >= entry: return "🟢 ACTIVE", "#00d4aa"
        else: return "⏳ PENDING", "#ffaa00"
    else:
        if live_price <= tp: return "✅ TARGET", "#00ffaa"
        elif live_price >= sl: return "❌ SL HIT", "#ff3366"
        elif live_price <= entry: return "🔴 ACTIVE", "#ff6b6b"
        else: return "⏳ PENDING", "#ffaa00"

# =============================================================================
# UNIVERSE SCANNER (Enhanced Multi-TF + VP + Pivots)
# =============================================================================
def run_scanner(tickers, period, interval, is_intraday, use_orb, rr, min_score,
                nifty_rs, capital, risk_pct, regime, daily_dirs=None,
                scan_key="scan", scan_mode="ALL", progress_cb=None):
    """
    scan_mode: ALL, RUNNERS_ONLY, TOP5_ONLY, EOD_PLAYBOOK
    """
    rows = []
    total = len(tickers)
    bulk_df = fetch_bulk_data(tuple(tickers), period=period, interval=interval)

    # Fetch daily data for pivots
    daily_bulk = None
    if is_intraday or scan_mode in ("EOD_PLAYBOOK", "TOP5_ONLY"):
        daily_bulk = fetch_bulk_data(tuple(tickers), period="6mo", interval="1d")

    for i, ticker in enumerate(tickers):
        if progress_cb: progress_cb((i + 1) / total)

        df = get_symbol_df(bulk_df, ticker, period, interval, min_len=20 if is_intraday else 50)
        if df.empty or len(df) < (20 if is_intraday else 50): continue

        df = generate_signals(df, lookback=10, is_intraday=is_intraday, rr=rr, use_orb=use_orb)
        if df.empty: continue

        dd = daily_dirs.get(ticker, 0) if daily_dirs else None

        # Pivots
        daily_df = get_symbol_df(daily_bulk, ticker, "6mo", "1d", 30) if daily_bulk is not None else pd.DataFrame()
        if not daily_df.empty and isinstance(daily_df.columns, pd.MultiIndex):
            daily_df.columns = daily_df.columns.get_level_values(0)
        pivots = {}
        if not daily_df.empty and len(daily_df) >= 30:
            pivots = compute_weekly_pivot(daily_df) if is_intraday else compute_monthly_pivot(daily_df)

        # Volume Profile
        vp = compute_volume_profile(df, lookback=20)

        # Multi-TF (lightweight: use existing data direction check)
        mtf_boost = 0
        if dd is not None and dd != 0:
            latest = df.iloc[-1]
            local_bull = latest.get('EMA_20', 0) > latest.get('EMA_50', 0)
            local_bear = latest.get('EMA_20', 0) < latest.get('EMA_50', 0)
            if (dd == 1 and local_bull) or (dd == -1 and local_bear):
                mtf_boost = 15  # Daily + scanning TF agree
            elif (dd == 1 and local_bear) or (dd == -1 and local_bull):
                mtf_boost = -10  # Conflict

        df = compute_psychology(df, daily_dir=dd, is_intraday=is_intraday)
        df = detect_one_way_runner(df, is_intraday=is_intraday)
        df = compute_confluence_score(df, nifty_rs, is_intraday=is_intraday, daily_dir=dd,
                                      pivots=pivots, vp=vp, mtf_boost=mtf_boost)

        if 'Score' not in df.columns: continue
        latest = df.iloc[-1]
        bull = bool(latest.get('Bull_Sweep', False)) or bool(latest.get('Bull_ORB', False))
        bear = bool(latest.get('Bear_Sweep', False)) or bool(latest.get('Bear_ORB', False))
        is_runner = bool(latest.get('Is_Runner', False))

        if scan_mode == "RUNNERS_ONLY" and not is_runner: continue
        if scan_mode not in ("EOD_PLAYBOOK", "TOP5_ONLY"):
            if not (bull or bear) and not is_runner: continue

        # Regime filter
        if regime not in ("CHOPPY", "UNKNOWN"):
            is_bull_sig = bull or (not bear and latest.get('EMA_20', 0) > latest.get('EMA_50', 0))
            if regime == "BULL" and not is_bull_sig: continue
            if regime == "BEAR" and is_bull_sig: continue

        score = latest.get('Score', 0)
        if pd.isna(score) or score < min_score: continue

        entry = latest.get('Entry', np.nan); sl = latest.get('SL', np.nan); tp = latest.get('TP', np.nan)
        if pd.isna(entry) or pd.isna(sl):
            atr_v = latest.get('ATR14', latest['Close'] * 0.02)
            is_up = latest.get('EMA_20', 0) > latest.get('EMA_50', 0)
            entry = latest['Close']
            sl = entry - atr_v if is_up else entry + atr_v
            tp = entry + atr_v * rr if is_up else entry - atr_v * rr

        qty, dep = position_size(capital, risk_pct, entry, sl)
        sig_time = latest.name.strftime('%Y-%m-%d %H:%M') if hasattr(latest.name, 'strftime') else str(latest.name)
        scan_time = get_ist_now().strftime('%Y-%m-%d %H:%M')

        # Setup label
        if is_runner: setup = latest.get('Runner_Type', 'RUNNER')
        elif bull: setup = '🟢 BUY'
        elif bear: setup = '🔴 SELL'
        else: setup = '🟢 BUY' if latest.get('EMA_20', 0) > latest.get('EMA_50', 0) else '🔴 SELL'

        # Pivot context
        piv_label, piv_type = get_pivot_context(float(latest['Close']), pivots) if pivots else ("—", "unknown")

        # First candle warning
        fc_pct = float(latest.get('FC_Pct', 0)) if not pd.isna(latest.get('FC_Pct', np.nan)) else 0
        fc_warn = fc_pct > 0.70

        # VP context
        vp_poc = vp.get("POC", 0) if vp else 0

        rows.append({
            'Symbol': ticker.replace('.NS', ''), 'Setup': setup,
            'Psychology': latest.get('Psychology', '—'),
            'Score': int(round(score)), 'LTP': round(float(latest['Close']), 2),
            'Entry': round(float(entry), 2), 'SL': round(float(sl), 2),
            'TP': round(float(tp), 2), 'Qty': qty, 'RR': rr,
            'RSI': round(float(latest.get('RSI_14', 0)), 1) if not pd.isna(latest.get('RSI_14')) else 0,
            'MACD_H': round(float(latest.get('MACD_Hist', 0)), 2) if not pd.isna(latest.get('MACD_Hist')) else 0,
            'ST': int(latest.get('ST_Dir', 0)),
            'Vol_Ratio': round(float(latest['Volume'] / latest.get('Vol_MA_20', 1)), 1) if latest.get('Vol_MA_20', 0) > 0 else 0,
            'Pivot': piv_label, 'Pivot_Type': piv_type,
            'VP_POC': round(vp_poc, 2),
            'FC_Warn': fc_warn, 'FC_Pct': round(fc_pct, 2),
            'Signal_Time': sig_time, 'Scan_Time': scan_time,
            'Status': '⏳ ACTIVE', 'Outcome_Time': '—',
            'Capital': round(dep, 0), 'Is_Runner': is_runner,
        })

    if scan_mode in ("TOP5_ONLY", "EOD_PLAYBOOK"):
        rows = sorted(rows, key=lambda x: x['Score'], reverse=True)[:5]

    # Persist to disk
    if rows:
        save_session_results(scan_key, rows)

    return rows

# =============================================================================
# =============================================================================
# DAILY SUPERTREND DIRECTIONS (Internal Helper)
# =============================================================================
def _get_daily_st_directions_internal(tickers):
    dirs = {}
    try:
        bulk = fetch_bulk_data(tuple(tickers), period="6mo", interval="1d")
        for t in tickers:
            d = get_symbol_df(bulk, t, "6mo", "1d", min_len=30)
            if d.empty or len(d) < 30: dirs[t] = 0; continue
            d = compute_supertrend(d.copy(), period=10, multiplier=2.0)
            last_d = d['ST_Dir'].dropna()
            dirs[t] = int(last_d.iloc[-1]) if not last_d.empty else 0
    except Exception: pass
    return dirs

# =============================================================================
# PRE-MARKET HERO SCANNER (Cached & Optimized)
# =============================================================================
@st.cache_data(ttl=120)
def scan_heroes(tickers_tuple):
    tickers = list(tickers_tuple)
    bulk = fetch_bulk_data(tuple(tickers), period="5d", interval="1d")
    candidates = []
    for t in tickers:
        d = get_symbol_df(bulk, t, "5d", "1d", 2)
        if d.empty or len(d) < 2: continue
        if isinstance(d.columns, pd.MultiIndex): d.columns = d.columns.get_level_values(0)
        yc = float(d['Close'].iloc[-2]); to = float(d['Open'].iloc[-1])
        tv = float(d['Volume'].iloc[-1])
        gp = ((to - yc) / yc) * 100
        mc = (to * tv) / 1e7

        # Check daily trend for confirmation
        if len(d) >= 50:
            d_ind = compute_all_indicators(d.copy(), is_intraday=False)
            last = d_ind.iloc[-1]
            trend_bull = last.get('EMA_20', 0) > last.get('EMA_50', 0)
            trend_bear = last.get('EMA_20', 0) < last.get('EMA_50', 0)
        else:
            trend_bull = gp > 0; trend_bear = gp < 0

        # Confidence
        conf = "🥇"
        if abs(gp) > 0.70: conf = "🥉"  # Over-extended open
        elif abs(gp) > 0.30:
            if (gp > 0 and trend_bull) or (gp < 0 and trend_bear):
                conf = "🥇"  # Gap aligned with trend
            else:
                conf = "🥈"  # Gap against trend

        candidates.append({'Symbol': t.replace('.NS', ''), 'Gap': gp, 'Money': mc,
                           'LTP': to, 'Conf': conf, 'Extended': abs(gp) > 0.70})

    if not candidates: return None, None
    longs = [c for c in candidates if c['Gap'] > 0.3 and c['Conf'] != "🥉"]
    hero_long = max(longs, key=lambda x: x['Money']) if longs else None
    shorts = [c for c in candidates if c['Gap'] < -0.3 and c['Conf'] != "🥉"]
    hero_short = max(shorts, key=lambda x: x['Money']) if shorts else None
    return hero_long, hero_short

# =============================================================================
# BACKGROUND SCAN THREADING (True Non-Blocking)
# =============================================================================
def start_background_scan(scan_key, tickers, period, interval, is_intraday, use_orb,
                           rr, min_score, nifty_rs, capital, risk_pct, regime,
                           scan_mode):
    """Launch scan in background thread — instant return, zero main thread block"""

    def worker():
        try:
            write_scan_status(scan_key, "running", 0.05)

            # Compute daily directions inside worker thread
            daily_dirs = _get_daily_st_directions_internal(tickers)
            write_scan_status(scan_key, "running", 0.15)

            def progress_cb(p):
                # Scale progress from 15% to 95%
                write_scan_status(scan_key, "running", 0.15 + (p * 0.80))

            results = run_scanner(
                tickers, period, interval, is_intraday, use_orb, rr, min_score,
                nifty_rs, capital, risk_pct, regime, daily_dirs,
                scan_key=scan_key, scan_mode=scan_mode, progress_cb=progress_cb
            )
            save_session_results(scan_key, results)
            write_scan_status(scan_key, "complete", 1.0)
        except Exception as e:
            write_scan_status(scan_key, "error", 0, str(e))

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()

# =============================================================================
# SESSION STATE INITIALIZATION
# =============================================================================
SCAN_KEYS = ['intraday_live', 'intraday_eod', 'intraday_runners',
             'swing_live', 'swing_eod', 'swing_runners']

for key in SCAN_KEYS:
    if f'{key}_results' not in st.session_state:
        # Try loading from disk
        saved, scan_time = load_session_results(key)
        st.session_state[f'{key}_results'] = saved
        st.session_state[f'{key}_scan_time'] = scan_time or ""

if 'view_mode' not in st.session_state:
    st.session_state['view_mode'] = 'card'  # 'card' or 'list'

# Load settings
saved_settings = load_settings()

# =============================================================================
# GLOBAL DATA
# =============================================================================
market_live = is_market_hours()
indices_data = fetch_market_indices()
nifty_daily = fetch_nifty_series(period="2y", interval="1d")
market_regime = get_market_regime(nifty_daily)
nifty_rs_series = get_nifty_rs_series(nifty_daily)

# Auto-refresh while scans are running
any_scan_running = any(read_scan_status(k).get("status") == "running" for k in SCAN_KEYS)
if any_scan_running and AUTOREFRESH_AVAILABLE:
    st_autorefresh(interval=3000, key="scan_poll")

# =============================================================================
# HEADER
# =============================================================================
h1, h2 = st.columns([4, 1])
with h1:
    st.markdown("<div class='main-header'>👑 QUANT-EDGE v19 — One-Way Runner Terminal</div>", unsafe_allow_html=True)
    st.markdown("<div class='sub-caption'>Multi-TF Confluence • Weekly/Monthly Pivots • Volume Profile • Institutional Psychology</div>", unsafe_allow_html=True)
with h2:
    ms, mc = get_market_status()
    st.markdown(f"<div class='market-status {mc}'>{ms}</div>", unsafe_allow_html=True)
    st.markdown(f"<div class='last-updated'>{get_ist_now().strftime('%d %b %Y • %I:%M %p IST')}</div>", unsafe_allow_html=True)

# Hero Banner
hero_long, hero_short = scan_heroes(tuple(FO_UNIVERSE))
if hero_long or hero_short:
    ht = "🏆 <b>PRE-MARKET INSTITUTIONAL HEROES</b> 🏆<br/>"
    if hero_long:
        ht += f"🟢 <b>{hero_long['Symbol']}</b> {hero_long['Conf']} (+{hero_long['Gap']:.1f}% • ₹{hero_long['Money']:.0f}Cr) "
    if hero_short:
        ht += f"| 🔴 <b>{hero_short['Symbol']}</b> {hero_short['Conf']} ({hero_short['Gap']:.1f}% • ₹{hero_short['Money']:.0f}Cr)"
    st.markdown(f"<div class='hero-banner'>{ht}</div>", unsafe_allow_html=True)

# Regime
rc = {"BULL": "regime-bull", "BEAR": "regime-bear", "CHOPPY": "regime-choppy"}.get(market_regime, "regime-choppy")
rm = {
    "BULL": "🟢 REGIME: BULL — Nifty > EMA • Supertrend ↑ • Institutions buying dips",
    "BEAR": "🔴 REGIME: BEAR — Nifty < EMA • Supertrend ↓ • Institutions selling rallies",
    "CHOPPY": "🟡 REGIME: CHOPPY — Trend unclear • Wait for momentum"
}.get(market_regime, "")
st.markdown(f"<div class='regime-banner {rc}'>{rm}</div>", unsafe_allow_html=True)

# Index Strip
c1, c2, c3 = st.columns(3)
for i, (name, m) in enumerate(indices_data.items()):
    col = [c1, c2, c3][i]
    cs = "change-up" if m["Chg"] >= 0 else "change-down"
    sign = "+" if m["Chg"] >= 0 else ""
    with col:
        st.markdown(f"""<div class="metric-card">
            <div class="metric-header">{name}</div>
            <div class="metric-val">₹{m['LTP']:,.1f}</div>
            <div class="metric-change {cs}">{sign}{m['Chg']:,.1f} ({sign}{m['Pct']:.2f}%)</div>
        </div>""", unsafe_allow_html=True)

# =============================================================================
# SIDEBAR
# =============================================================================
with st.sidebar:
    st.markdown("<div class='main-header' style='font-size:18px;'>⚙️ Control Panel</div>", unsafe_allow_html=True)

    with st.expander("🌍 Universe", expanded=True):
        u_sel = st.selectbox("Stock Universe", ["Full F&O (219)", "Custom"] + list(SECTOR_MAP.keys()), key="u_sel")
        if u_sel == "Full F&O (219)": active_tickers = FO_UNIVERSE
        elif u_sel == "Custom":
            ci = st.text_area("Tickers:", value="TATAMOTORS.NS,RELIANCE.NS", key="ct")
            active_tickers = [t.strip().upper() for t in ci.split(",") if t.strip()]
        else: active_tickers = [t.strip() for t in SECTOR_MAP[u_sel].split(",")]
        st.caption(f"📊 {len(active_tickers)} symbols")

    with st.expander("💰 Capital & Risk", expanded=True):
        capital_base = st.number_input("Capital ₹", min_value=10000, value=saved_settings.get("capital", 200000), step=10000, key="cap")
        risk_per_trade = st.slider("Risk/Trade %", 0.25, 3.0, saved_settings.get("risk_pct", 1.0), 0.25, key="rpt")
        # Save settings
        if capital_base != saved_settings.get("capital") or risk_per_trade != saved_settings.get("risk_pct"):
            save_settings({"capital": capital_base, "risk_pct": risk_per_trade})

    with st.expander("📊 Intraday Config", expanded=False):
        intra_rr = st.slider("R:R Ratio", 1.0, 5.0, 2.0, 0.5, key="irr")
        intra_min_score = st.slider("Min Score", 0, 100, 55, 5, key="ims")
        st.caption("⏱️ TFs: 15m + 30m + 1h confluence")
        st.caption("📌 Weekly Pivot (new every Monday)")

    with st.expander("📈 Swing Config", expanded=False):
        swing_rr = st.slider("R:R Ratio", 1.0, 5.0, 2.0, 0.5, key="srr")
        swing_min_score = st.slider("Min Score", 0, 100, 60, 5, key="sms")
        st.caption("⏱️ TFs: 1h + 1D confluence")
        st.caption("📌 Monthly Pivot (1st trading day)")

    with st.expander("🔄 Auto-Refresh", expanded=False):
        ref = st.selectbox("Interval", ["Manual", "30s", "60s", "2min"], key="ro")
        if ref != "Manual" and AUTOREFRESH_AVAILABLE and market_live:
            rs = {"30s": 30, "60s": 60, "2min": 120}[ref]
            st_autorefresh(interval=rs * 1000, key="ar_main")
            st.caption(f"✅ Every {rs}s")

# =============================================================================
# DISPLAY ENGINE — Card View + List View
# =============================================================================
def render_scan_status(scan_key):
    """Show scan status badge"""
    status = read_scan_status(scan_key)
    s = status.get("status", "idle")
    if s == "running":
        prog = status.get("progress", 0)
        st.markdown(f"<div class='scan-status scan-running'>🔄 Scanning {prog*100:.0f}%</div>", unsafe_allow_html=True)
        return True
    elif s == "complete":
        t = status.get("time", "")
        st.markdown(f"<div class='scan-status scan-complete'>✅ Complete @ {t}</div>", unsafe_allow_html=True)
        return False
    elif s == "error":
        st.error(f"❌ Scan error: {status.get('error', 'Unknown')}")
        return False
    return False

def display_view_toggle(key_prefix):
    """Card/List view toggle"""
    c1, c2, _ = st.columns([1, 1, 8])
    with c1:
        if st.button("🃏 Cards", key=f"{key_prefix}_card_btn", type="secondary"):
            st.session_state['view_mode'] = 'card'
    with c2:
        if st.button("📋 List", key=f"{key_prefix}_list_btn", type="secondary"):
            st.session_state['view_mode'] = 'list'

def display_results(results, scan_key="", fetch_live=True):
    """Display scan results in Card or List view"""
    if not results:
        st.info("🔍 No signals. Run a scan to populate."); return

    # Fetch live prices if market is open
    live_prices = {}
    if fetch_live and market_live and len(results) <= 50:
        tks = tuple(r['Symbol'] + '.NS' for r in results)
        live_prices = fetch_live_prices(tks)

    scan_time = st.session_state.get(f'{scan_key}_scan_time', '')
    if scan_time:
        st.markdown(f"<div class='last-updated'>📅 Scanned: {scan_time}</div>", unsafe_allow_html=True)

    display_view_toggle(scan_key)

    if st.session_state.get('view_mode', 'card') == 'card':
        _display_cards(results, live_prices)
    else:
        _display_list(results, live_prices)

def _display_cards(results, live_prices):
    """Render signal cards"""
    sorted_res = sorted(results, key=lambda x: x.get('Score', 0), reverse=True)
    cols = st.columns(2)
    for i, r in enumerate(sorted_res):
        with cols[i % 2]:
            is_bull = 'BUY' in str(r.get('Setup', '')) or 'BULL' in str(r.get('Setup', ''))
            card_class = "signal-card-buy" if is_bull else "signal-card-sell"
            score = r.get('Score', 0)
            score_class = "score-high" if score >= 75 else ("score-med" if score >= 55 else "score-low")

            # Live price & status
            lp = live_prices.get(r['Symbol'] + '.NS', r.get('LTP', 0))
            status_text, _ = get_signal_status(lp, r['Entry'], r['SL'], r['TP'], is_bull) if lp else ("⏳", "#6b7294")

            # Pivot badge
            piv = r.get('Pivot', '—')
            piv_type = r.get('Pivot_Type', 'unknown')
            piv_class = {"above": "pivot-above", "below": "pivot-below", "near": "pivot-near"}.get(piv_type, "pivot-near")

            # First candle warning
            fc_html = ""
            if r.get('FC_Warn', False):
                fc_html = f"<div class='card-warning'>⚠️ Extended 1st candle: {r.get('FC_Pct', 0):.1f}% (>0.70%)</div>"

            st.markdown(f"""
            <div class='signal-card {card_class}'>
                <div style='display:flex;justify-content:space-between;align-items:center;'>
                    <span class='card-symbol'>{'🟢' if is_bull else '🔴'} {r['Symbol']}</span>
                    <span class='card-score {score_class}'>{score}/100</span>
                </div>
                <div class='card-setup'>{r.get('Setup', '')} • {r.get('Psychology', '—')}</div>
                <div class='card-prices'>
                    Entry ₹{r['Entry']:,.1f} → Target ₹{r['TP']:,.1f}<br/>
                    SL ₹{r['SL']:,.1f} • RR 1:{r['RR']:.1f} • Qty {r['Qty']}
                </div>
                <div class='card-meta'>
                    📅 Signal: {r.get('Signal_Time', '—')} • ⏰ Scan: {r.get('Scan_Time', '—')}<br/>
                    💰 Live: ₹{lp:,.1f} • {r.get('Outcome_Time', '—')}
                </div>
                <div class='card-status' style='color:{"#00ffaa" if "TARGET" in status_text else ("#ff3366" if "SL" in status_text else "#ffaa00")}'>{status_text}</div>
                <div class='card-indicators'>
                    RSI {r.get('RSI', 0)} • Vol {r.get('Vol_Ratio', 0)}x • ST {'↑' if r.get('ST', 0) == 1 else '↓'}
                    • <span class='pivot-badge {piv_class}'>{piv}</span>
                    {f"• POC ₹{r.get('VP_POC', 0):,.0f}" if r.get('VP_POC', 0) > 0 else ""}
                </div>
                {fc_html}
            </div>
            """, unsafe_allow_html=True)

def _display_list(results, live_prices):
    """Render as sortable table"""
    sorted_res = sorted(results, key=lambda x: x.get('Score', 0), reverse=True)
    rows = []
    for r in sorted_res:
        is_bull = 'BUY' in str(r.get('Setup', '')) or 'BULL' in str(r.get('Setup', ''))
        lp = live_prices.get(r['Symbol'] + '.NS', r.get('LTP', 0))
        status_text, _ = get_signal_status(lp, r['Entry'], r['SL'], r['TP'], is_bull) if lp else ("⏳", "#6b7294")
        warn = "⚠️" if r.get('FC_Warn', False) else ""
        rows.append({
            'Symbol': f"{'🟢' if is_bull else '🔴'} {r['Symbol']} {warn}",
            'Setup': r.get('Setup', ''),
            'Score': r.get('Score', 0),
            'Live ₹': f"₹{lp:,.1f}" if lp else "—",
            'Entry': f"₹{r['Entry']:,.1f}",
            'SL': f"₹{r['SL']:,.1f}",
            'Target': f"₹{r['TP']:,.1f}",
            'Status': status_text,
            'RSI': r.get('RSI', 0),
            'Vol': f"{r.get('Vol_Ratio', 0)}x",
            'Pivot': r.get('Pivot', '—'),
            '📅 Signal': r.get('Signal_Time', '—'),
            '⏰ Scan': r.get('Scan_Time', '—'),
            'Qty': r.get('Qty', 0),
        })
    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True,
                     height=min(450, 35 * len(rows) + 38))

# =============================================================================
# MAIN TABS — PRE-MARKET SCREENER | LIVE SCREENER | TOOLS
# =============================================================================
tab_premarket, tab_live, tab_tools = st.tabs(["🌅 PRE-MARKET SCREENER", "⚡ LIVE SCREENER", "🛠️ TOOLS"])

# ═══════════════════════════════════════════════════════════════
# TAB 1: PRE-MARKET SCREENER
# ═══════════════════════════════════════════════════════════════
with tab_premarket:
    ps1, ps2, ps3 = st.tabs(["⚡ Pre-Market Open Heroes (09:08 AM)", "📅 Intraday EOD Picks", "📈 Swing EOD Picks"])

    with ps1:
        st.markdown("### ⚡ Pre-Market Open Heroes (09:00 AM – 09:08 AM)")
        st.markdown("<div class='info-panel'>🏆 Reads NSE Pre-Open Auction data at 09:08 AM to find high-turnover institutional gap-ups and gap-downs with Daily Trend confirmation.</div>", unsafe_allow_html=True)
        if hero_long or hero_short:
            hc1, hc2 = st.columns(2)
            if hero_long:
                with hc1:
                    st.markdown(f"""
                    <div class='glass-card' style='border-left: 4px solid #00ffaa;'>
                        <div style='font-size:18px;font-weight:800;color:#00ffaa;'>🟢 BULL HERO: {hero_long['Symbol']} {hero_long['Conf']}</div>
                        <div style='font-size:14px;margin-top:6px;'>Opening Price: <b>₹{hero_long['LTP']:,.1f}</b> ({'+' if hero_long['Gap']>=0 else ''}{hero_long['Gap']:.2f}%)</div>
                        <div style='font-size:12px;color:#8b90b0;'>Pre-Open Turnover: <b>₹{hero_long['Money']:,.0f} Cr</b></div>
                    </div>
                    """, unsafe_allow_html=True)
            if hero_short:
                with hc2:
                    st.markdown(f"""
                    <div class='glass-card' style='border-left: 4px solid #ff3366;'>
                        <div style='font-size:18px;font-weight:800;color:#ff3366;'>🔴 BEAR HERO: {hero_short['Symbol']} {hero_short['Conf']}</div>
                        <div style='font-size:14px;margin-top:6px;'>Opening Price: <b>₹{hero_short['LTP']:,.1f}</b> ({'+' if hero_short['Gap']>=0 else ''}{hero_short['Gap']:.2f}%)</div>
                        <div style='font-size:12px;color:#8b90b0;'>Pre-Open Turnover: <b>₹{hero_short['Money']:,.0f} Cr</b></div>
                    </div>
                    """, unsafe_allow_html=True)
        else:
            st.info("🟡 Pre-market data will be available at 09:08 AM on trading days.")

    with ps2:
        st.markdown("### 📅 Intraday EOD Picks (For Tomorrow)")
        st.markdown("<div class='info-panel'>📊 Top 5 highest-conviction intraday setups from 1-Hour chart analysis • Weekly Pivot • Pivot Compression</div>", unsafe_allow_html=True)

        is_scanning = render_scan_status("intraday_eod")

        if st.button("🚀 Compile Intraday EOD Top 5", key="ieodb", type="primary"):
            start_background_scan(
                "intraday_eod", active_tickers, "60d", "1h", True, False,
                intra_rr, intra_min_score, nifty_rs_series, capital_base,
                risk_per_trade, market_regime, "EOD_PLAYBOOK"
            )
            st.rerun()

        # Load & display results
        if is_scanning:
            st.info("🔄 Scan running in background... You can switch tabs freely!")
        else:
            saved, stime = load_session_results("intraday_eod")
            if saved:
                st.session_state['intraday_eod_results'] = saved
                st.session_state['intraday_eod_scan_time'] = stime
            if st.session_state.get('intraday_eod_results'):
                display_results(st.session_state['intraday_eod_results'], scan_key="intraday_eod", fetch_live=False)

    with ps3:
        st.markdown("### 📈 Swing EOD Picks (Multi-Day)")
        st.markdown("<div class='info-panel'>📊 Top 5 highest-conviction swing setups • Daily + Weekly trend • Monthly Pivot</div>", unsafe_allow_html=True)

        is_scanning = render_scan_status("swing_eod")

        if st.button("🚀 Compile Swing EOD Top 5", key="seodb", type="primary"):
            start_background_scan(
                "swing_eod", active_tickers, "2y", "1d", False, False,
                swing_rr, swing_min_score, nifty_rs_series, capital_base,
                risk_per_trade, market_regime, "EOD_PLAYBOOK"
            )
            st.rerun()

        if is_scanning:
            st.info("🔄 Scan running in background... You can switch tabs freely!")
        else:
            saved, stime = load_session_results("swing_eod")
            if saved:
                st.session_state['swing_eod_results'] = saved
                st.session_state['swing_eod_scan_time'] = stime
            if st.session_state.get('swing_eod_results'):
                display_results(st.session_state['swing_eod_results'], scan_key="swing_eod", fetch_live=False)

# ═══════════════════════════════════════════════════════════════
# TAB 2: LIVE SCREENER
# ═══════════════════════════════════════════════════════════════
with tab_live:
    ls1, ls2, ls3 = st.tabs(["⚡ Intraday Live", "📊 Swing Live", "🏃 One-Way Runners"])

    with ls1:
        st.markdown("### ⚡ Intraday Live Scanner")
        st.markdown("<div class='info-panel'>🧠 Multi-TF: 15m + 30m + 1h • Weekly Pivot • Volume Profile • ORB + 0.70% Filter</div>", unsafe_allow_html=True)

        top5_i = st.checkbox("🎯 Top 5 Only", value=True, key="strict_i")
        is_scanning = render_scan_status("intraday_live")

        if st.button("🚀 Scan Intraday Live", key="isb", type="primary"):
            mode = "TOP5_ONLY" if top5_i else "ALL"
            start_background_scan(
                "intraday_live", active_tickers, "30d", "15m", True, True,
                intra_rr, intra_min_score, None, capital_base,
                risk_per_trade, market_regime, mode
            )
            st.rerun()

        if is_scanning:
            st.info("🔄 Scanning in background... You can switch tabs freely!")
        else:
            saved, stime = load_session_results("intraday_live")
            if saved:
                st.session_state['intraday_live_results'] = saved
                st.session_state['intraday_live_scan_time'] = stime
            if st.session_state.get('intraday_live_results'):
                display_results(st.session_state['intraday_live_results'], scan_key="intraday_live")

    with ls2:
        st.markdown("### 📊 Swing Live Scanner (1-Hour)")
        st.markdown("<div class='info-panel'>🧠 Multi-TF: 1-Hour (60m) + 1D Daily Confluence • Monthly Pivot • Volume Profile • Institutional ADL</div>", unsafe_allow_html=True)

        top5_s = st.checkbox("🎯 Top 5 Only", value=True, key="strict_s")
        is_scanning = render_scan_status("swing_live")

        if st.button("🚀 Scan Swing Live", key="ssb", type="primary"):
            mode = "TOP5_ONLY" if top5_s else "ALL"
            start_background_scan(
                "swing_live", active_tickers, "60d", "60m", False, False,
                swing_rr, swing_min_score, nifty_rs_series, capital_base,
                risk_per_trade, market_regime, mode
            )
            st.rerun()

        if is_scanning:
            st.info("🔄 Scanning in background... You can switch tabs freely!")
        else:
            saved, stime = load_session_results("swing_live")
            if saved:
                st.session_state['swing_live_results'] = saved
                st.session_state['swing_live_scan_time'] = stime
            if st.session_state.get('swing_live_results'):
                display_results(st.session_state['swing_live_results'], scan_key="swing_live")

    with ls3:
        st.markdown("### 🏃 One-Way Momentum Runners")
        st.markdown("<div class='info-panel'>⚡ Extreme momentum stocks breaking ORB on heavy volume & NEVER retracing past VWAP</div>", unsafe_allow_html=True)

        rc1, rc2 = st.columns(2)
        with rc1:
            runner_intra = st.button("⚡ Scan Intraday Runners", key="irunb", type="primary")
        with rc2:
            runner_swing = st.button("⚡ Scan Swing Runners", key="srunb", type="primary")

        if runner_intra:
            start_background_scan(
                "intraday_runners", active_tickers, "30d", "15m", True, False,
                intra_rr, 0, None, capital_base, risk_per_trade,
                market_regime, "RUNNERS_ONLY"
            )
            st.rerun()

        if runner_swing:
            start_background_scan(
                "swing_runners", active_tickers, "60d", "60m", False, False,
                swing_rr, 0, None, capital_base, risk_per_trade,
                market_regime, "RUNNERS_ONLY"
            )
            st.rerun()

        # Show runner results
        for rk, label in [("intraday_runners", "⚡ Intraday"), ("swing_runners", "⚡ Swing")]:
            is_s = render_scan_status(rk)
            if not is_s:
                saved, stime = load_session_results(rk)
                if saved:
                    st.session_state[f'{rk}_results'] = saved
                    st.session_state[f'{rk}_scan_time'] = stime
                if st.session_state.get(f'{rk}_results'):
                    st.markdown(f"#### {label} Runners")
                    display_results(st.session_state[f'{rk}_results'], scan_key=rk)

# ═══════════════════════════════════════════════════════════════
# TAB 3: TOOLS
# ═══════════════════════════════════════════════════════════════
with tab_tools:
    t1, t2, t3 = st.tabs(["📊 Pivot Levels", "📖 Signal Ledger", "ℹ️ About"])

    with t1:
        st.markdown("### 📊 Weekly & Monthly Pivot Calculator")
        piv_ticker = st.text_input("Ticker (e.g. RELIANCE.NS)", value="RELIANCE.NS", key="piv_t")
        if st.button("Calculate Pivots", key="piv_btn"):
            d = fetch_stock_data(piv_ticker, period="6mo", interval="1d")
            if not d.empty:
                wp = compute_weekly_pivot(d)
                mp = compute_monthly_pivot(d)
                pc1, pc2 = st.columns(2)
                with pc1:
                    st.markdown("#### 📅 Weekly Pivot")
                    if wp:
                        for k, v in wp.items():
                            color = "#00ffaa" if 'R' in k else ("#ff3366" if 'S' in k else "#ffaa00")
                            st.markdown(f"<span style='color:{color};font-weight:700;'>{k}: ₹{v:,.2f}</span>", unsafe_allow_html=True)
                with pc2:
                    st.markdown("#### 📅 Monthly Pivot")
                    if mp:
                        for k, v in mp.items():
                            color = "#00ffaa" if 'R' in k else ("#ff3366" if 'S' in k else "#ffaa00")
                            st.markdown(f"<span style='color:{color};font-weight:700;'>{k}: ₹{v:,.2f}</span>", unsafe_allow_html=True)

    with t2:
        st.markdown("### 📖 Today's Signal Ledger")
        all_results = []
        for key in SCAN_KEYS:
            saved, _ = load_session_results(key)
            if saved:
                for r in saved:
                    r['Source'] = key.upper()
                all_results.extend(saved)
        if all_results:
            df_ledger = pd.DataFrame(all_results)
            cols_show = [c for c in ['Source', 'Symbol', 'Setup', 'Score', 'LTP', 'Entry', 'SL', 'TP',
                                      'Status', 'Signal_Time', 'Scan_Time', 'Pivot', 'RSI', 'Vol_Ratio'] if c in df_ledger.columns]
            st.dataframe(df_ledger[cols_show].sort_values('Score', ascending=False),
                        use_container_width=True, height=500)
        else:
            st.info("📭 No signals today. Run a scan to populate.")

    with t3:
        st.markdown("### ℹ️ QUANT-EDGE v19")
        st.markdown("""
        <div class='glass-card'>
            <b>🧠 Multi-Timeframe Confluence</b><br/>
            • Intraday: 15m + 30m + 1h scan with weekly pivots<br/>
            • Swing: 1h + 1D scan with monthly pivots<br/><br/>
            <b>📊 Volume Profile</b><br/>
            • POC (Point of Control) — highest volume price<br/>
            • VAH/VAL — institutional value area boundaries<br/><br/>
            <b>⚡ One-Way Runner Detection</b><br/>
            • ORB breakout + VWAP never violated = pure momentum<br/>
            • 0.70% first-candle filter flags extended opens<br/><br/>
            <b>🏦 Institutional Tracking</b><br/>
            • ADL (Accumulation/Distribution Line)<br/>
            • Volume at pivot levels = institutional interest<br/>
            • VWAP as institutional benchmark<br/><br/>
            <b>💾 Data Persistence</b><br/>
            • Scan results survive page refreshes<br/>
            • Data persists until next trading day 9:00 AM<br/>
            • Capital & risk settings saved automatically
        </div>
        """, unsafe_allow_html=True)

# =============================================================================
# FOOTER
# =============================================================================
st.markdown("---")
st.markdown(f"<div style='text-align:center;color:#3a4060;font-size:10px;padding:8px;'>👑 QUANT-EDGE v19 • Multi-TF Confluence • Pivots • VP • Institutional Psychology • {get_ist_now().strftime('%d %b %Y, %I:%M %p IST')}</div>", unsafe_allow_html=True)
