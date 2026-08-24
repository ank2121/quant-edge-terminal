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

    /* Stateful Radio Navigation Tabs */
    .stRadio [role="radiogroup"] {
        display: flex; flex-direction: row; gap: 8px; flex-wrap: wrap;
        background: rgba(13, 17, 30, 0.7); padding: 6px 8px; border-radius: 12px;
        border: 1px solid rgba(255,255,255,0.06); margin-bottom: 12px;
    }
    .stRadio [role="radiogroup"] > label {
        background: rgba(255,255,255,0.03); padding: 8px 18px; border-radius: 8px;
        color: #8b90b0; font-weight: 600; font-size: 12px; cursor: pointer;
        border: 1px solid transparent; transition: all 0.2s ease;
    }
    .stRadio [role="radiogroup"] > label:hover {
        background: rgba(0,136,255,0.1); color: #fff; border-color: rgba(0,136,255,0.3);
    }
    .stRadio [role="radiogroup"] > label[data-checked="true"],
    .stRadio [role="radiogroup"] input:checked + div {
        background: linear-gradient(135deg, rgba(0,212,170,0.2) 0%, rgba(0,136,255,0.2) 100%) !important;
        color: #00ffaa !important; border-color: rgba(0,255,170,0.4) !important;
        font-weight: 700 !important;
    }
    .stRadio label > div:first-child { display: none !important; }

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

    .view-toggle { display: flex; gap: 4px; margin-bottom: 10px; }

    .pivot-badge {
        font-size: 9px; padding: 2px 8px; border-radius: 12px; font-weight: 600;
        display: inline-block; margin-right: 4px;
    }
    .pivot-above { background: rgba(0,255,170,0.12); color: #00ffaa; }
    .pivot-below { background: rgba(255,51,102,0.12); color: #ff3366; }
    .pivot-near { background: rgba(255,170,0,0.12); color: #ffaa00; }

    .rr-badge {
        display: inline-flex; align-items: center; gap: 6px;
        padding: 4px 10px; border-radius: 8px; font-size: 11px; font-weight: 700;
        background: rgba(0,136,255,0.12); color: #4da6ff; border: 1px solid rgba(0,136,255,0.25);
        margin: 4px 0;
    }
    .rr-profit { color: #00ffaa; font-weight: 700; }
    .rr-risk { color: #ff6b6b; font-weight: 700; }
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
# PERFORMANCE LAB — DATA LAYER
# =============================================================================
PERF_DIR = os.path.join(DATA_DIR, "performance")
INTRADAY_WINNERS_DIR = os.path.join(PERF_DIR, "intraday_winners")
SWING_WINNERS_DIR = os.path.join(PERF_DIR, "swing_winners")
MANUAL_LOG_FILE = os.path.join(PERF_DIR, "manual_log.json")
PATTERN_FILE = os.path.join(PERF_DIR, "pattern_analysis.json")
BACKTEST_FILE = os.path.join(PERF_DIR, "backtest_results.json")
os.makedirs(INTRADAY_WINNERS_DIR, exist_ok=True)
os.makedirs(SWING_WINNERS_DIR, exist_ok=True)

def save_perf_winners(filepath, data):
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, default=str)
    except Exception: pass

def load_perf_winners(filepath):
    try:
        if os.path.exists(filepath):
            with open(filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception: pass
    return None

def save_manual_log(entries):
    save_perf_winners(MANUAL_LOG_FILE, {"entries": entries, "updated": get_ist_now().strftime('%Y-%m-%d %H:%M:%S')})

def load_manual_log():
    data = load_perf_winners(MANUAL_LOG_FILE)
    return data.get("entries", []) if data else []

def save_pattern_analysis(patterns):
    save_perf_winners(PATTERN_FILE, {"patterns": patterns, "updated": get_ist_now().strftime('%Y-%m-%d %H:%M:%S')})

def load_pattern_analysis():
    data = load_perf_winners(PATTERN_FILE)
    return data.get("patterns", {}) if data else {}

def save_backtest_results(results):
    save_perf_winners(BACKTEST_FILE, {"results": results, "updated": get_ist_now().strftime('%Y-%m-%d %H:%M:%S')})

def load_backtest_results():
    data = load_perf_winners(BACKTEST_FILE)
    return data.get("results", {}) if data else {}

def archive_old_performance_data(max_age_days=90):
    """Remove performance data older than max_age_days"""
    cutoff = get_ist_now() - timedelta(days=max_age_days)
    for d in [INTRADAY_WINNERS_DIR, SWING_WINNERS_DIR]:
        try:
            for fname in os.listdir(d):
                fpath = os.path.join(d, fname)
                if os.path.isfile(fpath):
                    mtime = datetime.fromtimestamp(os.path.getmtime(fpath), tz=IST)
                    if mtime < cutoff:
                        os.remove(fpath)
        except Exception: pass

def get_all_intraday_winner_dates():
    """List all dates for which we have intraday winner data"""
    dates = []
    try:
        for fname in sorted(os.listdir(INTRADAY_WINNERS_DIR)):
            if fname.endswith('.json'):
                dates.append(fname.replace('.json', ''))
    except Exception: pass
    return dates

def get_all_swing_winner_weeks():
    """List all weeks for which we have swing winner data"""
    weeks = []
    try:
        for fname in sorted(os.listdir(SWING_WINNERS_DIR)):
            if fname.endswith('.json'):
                weeks.append(fname.replace('.json', ''))
    except Exception: pass
    return weeks

def perf_data_to_csv(data_list, filename):
    """Convert list of dicts to CSV string for download"""
    if not data_list: return ""
    df = pd.DataFrame(data_list)
    return df.to_csv(index=False)

archive_old_performance_data()

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
# PERFORMANCE LAB — AUTO-DISCOVERY ENGINE
# =============================================================================
SECTOR_REVERSE = {}
for sec_name, sec_tickers_str in SECTOR_MAP.items():
    for t in sec_tickers_str.split(","):
        SECTOR_REVERSE[t.strip()] = sec_name

def _capture_technical_snapshot(ticker, daily_df, is_intraday=True):
    """Capture comprehensive technical snapshot of a stock at a given point"""
    snap = {"ticker": ticker, "symbol": ticker.replace('.NS', '')}
    snap["sector"] = SECTOR_REVERSE.get(ticker, "Other")
    try:
        if daily_df.empty or len(daily_df) < 30:
            return snap
        df = daily_df.copy()
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = compute_all_indicators(df, is_intraday=False)
        df = compute_supertrend(df, period=10, multiplier=2.0)
        last = df.iloc[-1]
        prev = df.iloc[-2] if len(df) >= 2 else last

        # Core indicators at start of move
        snap["daily_st_dir"] = int(last.get('ST_Dir', 0))
        snap["rsi_14"] = round(float(last.get('RSI_14', 50)), 1) if not pd.isna(last.get('RSI_14')) else 50
        snap["macd_hist"] = round(float(last.get('MACD_Hist', 0)), 3) if not pd.isna(last.get('MACD_Hist')) else 0
        snap["macd_dir"] = "bullish" if snap["macd_hist"] > 0 else "bearish"
        snap["ema9"] = round(float(last.get('EMA_9', 0)), 2)
        snap["ema20"] = round(float(last.get('EMA_20', 0)), 2)
        snap["ema50"] = round(float(last.get('EMA_50', 0)), 2)
        snap["close"] = round(float(last['Close']), 2)
        snap["ema_alignment"] = "bullish" if snap["close"] > snap["ema20"] > snap["ema50"] else (
            "bearish" if snap["close"] < snap["ema20"] < snap["ema50"] else "neutral")

        # Volume
        vol_ma = last.get('Vol_MA_20', 0)
        snap["vol_ratio"] = round(float(last['Volume'] / vol_ma), 1) if vol_ma and vol_ma > 0 else 0

        # Gap from previous close
        prev_close = float(prev['Close'])
        today_open = float(last.get('Open', last['Close']))
        snap["gap_pct"] = round(((today_open - prev_close) / prev_close) * 100, 2) if prev_close > 0 else 0

        # ADL trend
        if 'ADL' in df.columns:
            adl_recent = df['ADL'].tail(5)
            snap["adl_trend"] = "accumulation" if adl_recent.iloc[-1] > adl_recent.iloc[0] else "distribution"
        else:
            snap["adl_trend"] = "unknown"

        # Prev close vs VWAP proxy (EMA20)
        snap["prev_close_vs_vwap"] = "above" if prev_close > snap["ema20"] else "below"

        # Pivots
        pivots = compute_weekly_pivot(df) if is_intraday else compute_monthly_pivot(df)
        if pivots:
            p = pivots.get("P", 0)
            r1 = pivots.get("R1", p)
            s1 = pivots.get("S1", p)
            snap["pivot_pos"] = "above_r1" if snap["close"] > r1 else (
                "above_p" if snap["close"] > p else (
                    "below_s1" if snap["close"] < s1 else "below_p"))
            snap["pivot_p"] = round(p, 2)
        else:
            snap["pivot_pos"] = "unknown"

        # Volume Profile
        vp = compute_volume_profile(df, lookback=20)
        if vp and not np.isnan(vp.get("POC", np.nan)):
            snap["vp_poc"] = round(vp["POC"], 2)
            snap["vp_pos"] = "above_vah" if snap["close"] > vp["VAH"] else (
                "below_val" if snap["close"] < vp["VAL"] else "inside_va")
        else:
            snap["vp_pos"] = "unknown"

    except Exception:
        pass
    return snap

def _find_historical_similar_setups(ticker, snapshot, daily_df, lookback=252):
    """Find past dates where this stock had a similar technical setup"""
    similar = []
    try:
        if daily_df.empty or len(daily_df) < lookback:
            return similar
        df = daily_df.copy()
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = compute_all_indicators(df, is_intraday=False)
        df = compute_supertrend(df, period=10, multiplier=2.0)

        target_st = snapshot.get("daily_st_dir", 0)
        target_rsi_low = max(30, snapshot.get("rsi_14", 50) - 10)
        target_rsi_high = min(70, snapshot.get("rsi_14", 50) + 10)
        target_ema = snapshot.get("ema_alignment", "neutral")

        for i in range(max(50, len(df) - lookback), len(df) - 5):
            row = df.iloc[i]
            rsi_val = row.get('RSI_14', 50)
            if pd.isna(rsi_val):
                continue
            st_dir = int(row.get('ST_Dir', 0))
            close = float(row['Close'])
            ema20 = float(row.get('EMA_20', 0))
            ema50 = float(row.get('EMA_50', 0))

            local_ema = "bullish" if close > ema20 > ema50 else (
                "bearish" if close < ema20 < ema50 else "neutral")

            if st_dir == target_st and target_rsi_low <= rsi_val <= target_rsi_high and local_ema == target_ema:
                # Check what happened in next 5 days
                future = df.iloc[i+1:i+6]
                if future.empty:
                    continue
                max_up = ((future['High'].max() - close) / close) * 100
                max_down = ((close - future['Low'].min()) / close) * 100
                fwd_return = ((float(future.iloc[-1]['Close']) - close) / close) * 100

                similar.append({
                    "date": str(row.name.date()) if hasattr(row.name, 'date') else str(row.name),
                    "close": round(close, 2),
                    "rsi": round(float(rsi_val), 1),
                    "st_dir": st_dir,
                    "max_up_5d": round(max_up, 2),
                    "max_down_5d": round(max_down, 2),
                    "fwd_return_5d": round(fwd_return, 2),
                })
        # Keep top 10 most recent
        similar = similar[-10:]
    except Exception:
        pass
    return similar

def discover_intraday_winners(target_date=None):
    """
    Find stocks that moved ≥2% from the 1st 5-min candle edge in one direction.
    Captures full technical snapshot + historical similarity analysis.
    Runs after market close (3:35 PM+).
    """
    if target_date is None:
        target_date = get_ist_now().strftime('%Y-%m-%d')

    filepath = os.path.join(INTRADAY_WINNERS_DIR, f"{target_date}.json")
    existing = load_perf_winners(filepath)
    if existing and existing.get("winners"):
        return existing  # Already discovered for this date

    winners = []
    total = len(FO_UNIVERSE)

    # Fetch 5-min data for all tickers (last 5 days to get today)
    for i, ticker in enumerate(FO_UNIVERSE):
        try:
            write_scan_status("perf_intraday_discovery", "running", (i + 1) / total)
            df5m = yf.download(ticker, period="5d", interval="5m", progress=False, auto_adjust=True)
            if isinstance(df5m.columns, pd.MultiIndex):
                df5m.columns = df5m.columns.get_level_values(0)
            if df5m.empty or len(df5m) < 10:
                continue

            # Filter for the target date
            df5m.index = pd.to_datetime(df5m.index)
            day_data = df5m[df5m.index.strftime('%Y-%m-%d') == target_date]
            if day_data.empty or len(day_data) < 3:
                continue

            # First 5-min candle
            first_candle = day_data.iloc[0]
            fc_high = float(first_candle['High'])
            fc_low = float(first_candle['Low'])
            fc_open = float(first_candle['Open'])
            fc_close = float(first_candle['Close'])
            fc_vol = float(first_candle['Volume'])

            # Day stats
            day_high = float(day_data['High'].max())
            day_low = float(day_data['Low'].min())
            day_close = float(day_data.iloc[-1]['Close'])

            # Calculate max move from ORB edge
            max_up_from_orb = ((day_high - fc_high) / fc_high) * 100 if fc_high > 0 else 0
            max_down_from_orb = ((fc_low - day_low) / fc_low) * 100 if fc_low > 0 else 0

            is_winner = False
            direction = ""
            move_pct = 0

            if max_up_from_orb >= 2.0:
                is_winner = True
                direction = "BUY"
                move_pct = round(max_up_from_orb, 2)
            elif max_down_from_orb >= 2.0:
                is_winner = True
                direction = "SELL"
                move_pct = round(max_down_from_orb, 2)

            if not is_winner:
                continue

            # Fetch daily data for snapshot
            daily_df = yf.download(ticker, period="1y", interval="1d", progress=False, auto_adjust=True)
            if isinstance(daily_df.columns, pd.MultiIndex):
                daily_df.columns = daily_df.columns.get_level_values(0)

            snapshot = _capture_technical_snapshot(ticker, daily_df, is_intraday=True)
            historical_similar = _find_historical_similar_setups(ticker, snapshot, daily_df)

            # First candle body %
            fc_body_pct = abs(fc_close - fc_open) / fc_open * 100 if fc_open > 0 else 0

            # Volume ratio for 1st candle (vs day average)
            avg_vol_5m = day_data['Volume'].mean()
            fc_vol_ratio = fc_vol / avg_vol_5m if avg_vol_5m > 0 else 0

            winner = {
                "symbol": ticker.replace('.NS', ''),
                "ticker": ticker,
                "date": target_date,
                "direction": direction,
                "move_pct": move_pct,
                "fc_high": round(fc_high, 2),
                "fc_low": round(fc_low, 2),
                "fc_open": round(fc_open, 2),
                "fc_close": round(fc_close, 2),
                "fc_body_pct": round(fc_body_pct, 2),
                "fc_vol_ratio": round(fc_vol_ratio, 1),
                "day_high": round(day_high, 2),
                "day_low": round(day_low, 2),
                "day_close": round(day_close, 2),
                "snapshot": snapshot,
                "historical_similar": historical_similar,
            }
            winners.append(winner)

        except Exception:
            continue

    result = {
        "date": target_date,
        "discovered_at": get_ist_now().strftime('%Y-%m-%d %H:%M:%S'),
        "total_scanned": total,
        "winners_found": len(winners),
        "winners": winners
    }
    save_perf_winners(filepath, result)
    write_scan_status("perf_intraday_discovery", "complete", 1.0)
    return result

def discover_swing_winners(week_end_date=None):
    """
    Find stocks that moved ≥7% from Monday's 1st 1-hour candle edge during the week.
    Runs every Friday after close.
    """
    now = get_ist_now()
    if week_end_date is None:
        # Find the most recent Friday
        days_since_friday = (now.weekday() - 4) % 7
        friday = now - timedelta(days=days_since_friday)
        week_end_date = friday.strftime('%Y-%m-%d')

    # Compute ISO week
    dt = datetime.strptime(week_end_date, '%Y-%m-%d')
    iso_year, iso_week, _ = dt.isocalendar()
    week_key = f"{iso_year}-W{iso_week:02d}"

    filepath = os.path.join(SWING_WINNERS_DIR, f"{week_key}.json")
    existing = load_perf_winners(filepath)
    if existing and existing.get("winners"):
        return existing

    # Monday of this week
    monday = dt - timedelta(days=dt.weekday())
    monday_str = monday.strftime('%Y-%m-%d')

    winners = []
    total = len(FO_UNIVERSE)

    for i, ticker in enumerate(FO_UNIVERSE):
        try:
            write_scan_status("perf_swing_discovery", "running", (i + 1) / total)

            # Get hourly data for the week
            df1h = yf.download(ticker, start=monday_str,
                               end=(dt + timedelta(days=1)).strftime('%Y-%m-%d'),
                               interval="60m", progress=False, auto_adjust=True)
            if isinstance(df1h.columns, pd.MultiIndex):
                df1h.columns = df1h.columns.get_level_values(0)
            if df1h.empty or len(df1h) < 5:
                continue

            # Monday's first 1-hour candle
            df1h.index = pd.to_datetime(df1h.index)
            monday_data = df1h[df1h.index.strftime('%Y-%m-%d') == monday_str]
            if monday_data.empty:
                continue

            first_hour = monday_data.iloc[0]
            fh_high = float(first_hour['High'])
            fh_low = float(first_hour['Low'])

            # Week stats
            week_high = float(df1h['High'].max())
            week_low = float(df1h['Low'].min())
            week_close = float(df1h.iloc[-1]['Close'])

            max_up = ((week_high - fh_high) / fh_high) * 100 if fh_high > 0 else 0
            max_down = ((fh_low - week_low) / fh_low) * 100 if fh_low > 0 else 0

            is_winner = False
            direction = ""
            move_pct = 0

            if max_up >= 7.0:
                is_winner = True
                direction = "BUY"
                move_pct = round(max_up, 2)
            elif max_down >= 7.0:
                is_winner = True
                direction = "SELL"
                move_pct = round(max_down, 2)

            if not is_winner:
                continue

            daily_df = yf.download(ticker, period="2y", interval="1d", progress=False, auto_adjust=True)
            if isinstance(daily_df.columns, pd.MultiIndex):
                daily_df.columns = daily_df.columns.get_level_values(0)

            snapshot = _capture_technical_snapshot(ticker, daily_df, is_intraday=False)
            historical_similar = _find_historical_similar_setups(ticker, snapshot, daily_df, lookback=504)

            winner = {
                "symbol": ticker.replace('.NS', ''),
                "ticker": ticker,
                "week": week_key,
                "week_start": monday_str,
                "week_end": week_end_date,
                "direction": direction,
                "move_pct": move_pct,
                "fh_high": round(fh_high, 2),
                "fh_low": round(fh_low, 2),
                "week_high": round(week_high, 2),
                "week_low": round(week_low, 2),
                "week_close": round(week_close, 2),
                "snapshot": snapshot,
                "historical_similar": historical_similar,
            }
            winners.append(winner)

        except Exception:
            continue

    result = {
        "week": week_key,
        "week_start": monday_str,
        "week_end": week_end_date,
        "discovered_at": get_ist_now().strftime('%Y-%m-%d %H:%M:%S'),
        "total_scanned": total,
        "winners_found": len(winners),
        "winners": winners
    }
    save_perf_winners(filepath, result)
    write_scan_status("perf_swing_discovery", "complete", 1.0)
    return result

# =============================================================================
# PERFORMANCE LAB — PATTERN ANALYSIS ENGINE
# =============================================================================
def analyze_winner_patterns():
    """
    Reads all saved winners and computes frequency of each technical attribute.
    Returns 'Golden Rules' — the attributes most commonly found among winners.
    """
    all_snapshots_intraday = []
    all_snapshots_swing = []

    # Collect all intraday winners
    for fname in os.listdir(INTRADAY_WINNERS_DIR):
        data = load_perf_winners(os.path.join(INTRADAY_WINNERS_DIR, fname))
        if data and data.get("winners"):
            for w in data["winners"]:
                snap = w.get("snapshot", {})
                snap["direction"] = w.get("direction", "")
                snap["move_pct"] = w.get("move_pct", 0)
                snap["fc_body_pct"] = w.get("fc_body_pct", 0)
                snap["fc_vol_ratio"] = w.get("fc_vol_ratio", 0)
                all_snapshots_intraday.append(snap)

    # Collect all swing winners
    for fname in os.listdir(SWING_WINNERS_DIR):
        data = load_perf_winners(os.path.join(SWING_WINNERS_DIR, fname))
        if data and data.get("winners"):
            for w in data["winners"]:
                snap = w.get("snapshot", {})
                snap["direction"] = w.get("direction", "")
                snap["move_pct"] = w.get("move_pct", 0)
                all_snapshots_swing.append(snap)

    # Add manual log entries
    manual = load_manual_log()
    for entry in manual:
        snap = entry.get("snapshot", {})
        if not snap:
            continue
        snap["direction"] = entry.get("direction", "")
        if entry.get("trade_type") == "Intraday":
            all_snapshots_intraday.append(snap)
        else:
            all_snapshots_swing.append(snap)

    def _compute_frequencies(snapshots):
        if not snapshots:
            return {}
        n = len(snapshots)
        rules = {}

        # Supertrend direction alignment
        buy_snaps = [s for s in snapshots if s.get("direction") == "BUY"]
        sell_snaps = [s for s in snapshots if s.get("direction") == "SELL"]
        st_aligned = sum(1 for s in buy_snaps if s.get("daily_st_dir") == 1) + \
                     sum(1 for s in sell_snaps if s.get("daily_st_dir") == -1)
        rules["Supertrend aligned with direction"] = round(st_aligned / max(n, 1) * 100, 1)

        # EMA alignment
        ema_aligned = sum(1 for s in buy_snaps if s.get("ema_alignment") == "bullish") + \
                      sum(1 for s in sell_snaps if s.get("ema_alignment") == "bearish")
        rules["EMA 9/20/50 aligned with direction"] = round(ema_aligned / max(n, 1) * 100, 1)

        # RSI in sweet spot (40-65)
        rsi_sweet = sum(1 for s in snapshots if 40 <= s.get("rsi_14", 50) <= 65)
        rules["RSI between 40-65 (room to run)"] = round(rsi_sweet / max(n, 1) * 100, 1)

        # Volume ratio > 1.5x
        vol_high = sum(1 for s in snapshots if s.get("vol_ratio", 0) > 1.5)
        rules["Volume > 1.5x average"] = round(vol_high / max(n, 1) * 100, 1)

        # MACD aligned
        macd_aligned = sum(1 for s in buy_snaps if s.get("macd_dir") == "bullish") + \
                       sum(1 for s in sell_snaps if s.get("macd_dir") == "bearish")
        rules["MACD histogram aligned"] = round(macd_aligned / max(n, 1) * 100, 1)

        # Gap direction matching
        gap_aligned = sum(1 for s in buy_snaps if s.get("gap_pct", 0) > 0) + \
                      sum(1 for s in sell_snaps if s.get("gap_pct", 0) < 0)
        rules["Gap direction aligned"] = round(gap_aligned / max(n, 1) * 100, 1)

        # ADL confirming
        adl_ok = sum(1 for s in buy_snaps if s.get("adl_trend") == "accumulation") + \
                 sum(1 for s in sell_snaps if s.get("adl_trend") == "distribution")
        rules["ADL trend confirming"] = round(adl_ok / max(n, 1) * 100, 1)

        # Pivot position
        piv_ok = sum(1 for s in buy_snaps if s.get("pivot_pos", "").startswith("above")) + \
                 sum(1 for s in sell_snaps if s.get("pivot_pos", "").startswith("below"))
        rules["Pivot position confirming"] = round(piv_ok / max(n, 1) * 100, 1)

        # Close above VWAP for buys, below for sells
        vwap_ok = sum(1 for s in buy_snaps if s.get("prev_close_vs_vwap") == "above") + \
                  sum(1 for s in sell_snaps if s.get("prev_close_vs_vwap") == "below")
        rules["Close vs VWAP confirming"] = round(vwap_ok / max(n, 1) * 100, 1)

        # First candle body 0.25-0.70% (intraday only)
        fc_snaps = [s for s in snapshots if s.get("fc_body_pct", 0) > 0]
        if fc_snaps:
            fc_sweet = sum(1 for s in fc_snaps if 0.25 <= s.get("fc_body_pct", 0) <= 0.70)
            rules["1st candle body 0.25-0.70%"] = round(fc_sweet / max(len(fc_snaps), 1) * 100, 1)

        # Sector distribution
        sector_counts = {}
        for s in snapshots:
            sec = s.get("sector", "Other")
            sector_counts[sec] = sector_counts.get(sec, 0) + 1
        rules["_sector_distribution"] = sector_counts
        rules["_total_winners"] = n

        return rules

    intraday_patterns = _compute_frequencies(all_snapshots_intraday)
    swing_patterns = _compute_frequencies(all_snapshots_swing)

    patterns = {
        "intraday": intraday_patterns,
        "swing": swing_patterns,
        "analyzed_at": get_ist_now().strftime('%Y-%m-%d %H:%M:%S'),
        "intraday_sample_size": len(all_snapshots_intraday),
        "swing_sample_size": len(all_snapshots_swing),
    }
    save_pattern_analysis(patterns)
    return patterns

def compute_pattern_match_score(snapshot, patterns, trade_type="intraday"):
    """Score a stock snapshot against discovered winner patterns (0-10 bonus points)"""
    rules = patterns.get(trade_type, {})
    if not rules or rules.get("_total_winners", 0) < 5:
        return 0  # Need at least 5 winners to make meaningful patterns

    # Extract the top rules (those above 60% frequency)
    strong_rules = {k: v for k, v in rules.items() if isinstance(v, (int, float)) and not k.startswith("_") and v >= 60}
    if not strong_rules:
        return 0

    direction = "BUY" if snapshot.get("daily_st_dir", 0) == 1 else "SELL"
    matches = 0
    total_rules = len(strong_rules)

    for rule_name, _ in strong_rules.items():
        if "Supertrend" in rule_name:
            if (direction == "BUY" and snapshot.get("daily_st_dir") == 1) or \
               (direction == "SELL" and snapshot.get("daily_st_dir") == -1):
                matches += 1
        elif "EMA" in rule_name:
            if (direction == "BUY" and snapshot.get("ema_alignment") == "bullish") or \
               (direction == "SELL" and snapshot.get("ema_alignment") == "bearish"):
                matches += 1
        elif "RSI" in rule_name:
            if 40 <= snapshot.get("rsi_14", 50) <= 65:
                matches += 1
        elif "Volume" in rule_name:
            if snapshot.get("vol_ratio", 0) > 1.5:
                matches += 1
        elif "MACD" in rule_name:
            if (direction == "BUY" and snapshot.get("macd_dir") == "bullish") or \
               (direction == "SELL" and snapshot.get("macd_dir") == "bearish"):
                matches += 1
        elif "Gap" in rule_name:
            if (direction == "BUY" and snapshot.get("gap_pct", 0) > 0) or \
               (direction == "SELL" and snapshot.get("gap_pct", 0) < 0):
                matches += 1
        elif "ADL" in rule_name:
            if (direction == "BUY" and snapshot.get("adl_trend") == "accumulation") or \
               (direction == "SELL" and snapshot.get("adl_trend") == "distribution"):
                matches += 1
        elif "Pivot" in rule_name:
            if (direction == "BUY" and snapshot.get("pivot_pos", "").startswith("above")) or \
               (direction == "SELL" and snapshot.get("pivot_pos", "").startswith("below")):
                matches += 1
        elif "VWAP" in rule_name:
            if (direction == "BUY" and snapshot.get("prev_close_vs_vwap") == "above") or \
               (direction == "SELL" and snapshot.get("prev_close_vs_vwap") == "below"):
                matches += 1

    # Scale to 0-10 bonus points
    return round((matches / max(total_rules, 1)) * 10, 1)

# =============================================================================
# PERFORMANCE LAB — BACKTEST ENGINE
# =============================================================================
def backtest_intraday_strategy(lookback_days=60):
    """
    For each of the last N trading days, check:
    - How many actual winners were there (≥2% ORB move)?
    - How many did our screener find before the move?
    Returns daily hit/miss stats.
    """
    results = []
    now = get_ist_now()

    for day_offset in range(1, lookback_days + 1):
        target = now - timedelta(days=day_offset)
        if target.weekday() >= 5:
            continue
        target_str = target.strftime('%Y-%m-%d')

        # Check if we have winner data for this date
        filepath = os.path.join(INTRADAY_WINNERS_DIR, f"{target_str}.json")
        winner_data = load_perf_winners(filepath)
        if not winner_data:
            continue

        actual_winners = winner_data.get("winners", [])
        if not actual_winners:
            results.append({
                "date": target_str, "total_winners": 0,
                "screener_found": 0, "hit_rate": 0,
                "missed": 0, "winner_symbols": []
            })
            continue

        # Check our scan results for that day
        scan_filepath = os.path.join(SESSIONS_DIR, f"intraday_eod_{target_str}.json")
        scan_data = load_perf_winners(scan_filepath)
        screener_symbols = set()
        if scan_data and scan_data.get("results"):
            screener_symbols = {r.get("Symbol", "") for r in scan_data["results"]}

        # Also check live scan
        scan_filepath2 = os.path.join(SESSIONS_DIR, f"intraday_live_{target_str}.json")
        scan_data2 = load_perf_winners(scan_filepath2)
        if scan_data2 and scan_data2.get("results"):
            screener_symbols |= {r.get("Symbol", "") for r in scan_data2["results"]}

        winner_symbols = [w["symbol"] for w in actual_winners]
        found = sum(1 for ws in winner_symbols if ws in screener_symbols)
        missed = len(winner_symbols) - found
        hit_rate = round(found / max(len(winner_symbols), 1) * 100, 1)

        results.append({
            "date": target_str,
            "total_winners": len(actual_winners),
            "screener_found": found,
            "hit_rate": hit_rate,
            "missed": missed,
            "winner_symbols": winner_symbols,
            "screener_symbols": list(screener_symbols),
        })

    backtest_out = {
        "type": "intraday",
        "lookback_days": lookback_days,
        "daily_results": results,
        "avg_hit_rate": round(np.mean([r["hit_rate"] for r in results if r["total_winners"] > 0]), 1) if results else 0,
        "total_winners_found": sum(r["total_winners"] for r in results),
        "total_screener_hits": sum(r["screener_found"] for r in results),
        "run_at": get_ist_now().strftime('%Y-%m-%d %H:%M:%S'),
    }
    save_backtest_results(backtest_out)
    return backtest_out

def backtest_swing_strategy(lookback_weeks=12):
    """Check swing hit rate over last N weeks."""
    results = []
    now = get_ist_now()

    for week_offset in range(1, lookback_weeks + 1):
        friday = now - timedelta(weeks=week_offset)
        while friday.weekday() != 4:
            friday -= timedelta(days=1)
        iso_year, iso_week, _ = friday.isocalendar()
        week_key = f"{iso_year}-W{iso_week:02d}"

        filepath = os.path.join(SWING_WINNERS_DIR, f"{week_key}.json")
        winner_data = load_perf_winners(filepath)
        if not winner_data:
            continue

        actual_winners = winner_data.get("winners", [])
        monday_str = winner_data.get("week_start", "")

        # Check swing scan for that week's Monday
        scan_filepath = os.path.join(SESSIONS_DIR, f"swing_eod_{monday_str}.json")
        scan_data = load_perf_winners(scan_filepath)
        screener_symbols = set()
        if scan_data and scan_data.get("results"):
            screener_symbols = {r.get("Symbol", "") for r in scan_data["results"]}

        winner_symbols = [w["symbol"] for w in actual_winners]
        found = sum(1 for ws in winner_symbols if ws in screener_symbols)
        hit_rate = round(found / max(len(winner_symbols), 1) * 100, 1) if winner_symbols else 0

        results.append({
            "week": week_key,
            "total_winners": len(actual_winners),
            "screener_found": found,
            "hit_rate": hit_rate,
            "missed": len(winner_symbols) - found,
            "winner_symbols": winner_symbols,
        })

    backtest_out = {
        "type": "swing",
        "lookback_weeks": lookback_weeks,
        "weekly_results": results,
        "avg_hit_rate": round(np.mean([r["hit_rate"] for r in results if r["total_winners"] > 0]), 1) if results else 0,
        "run_at": get_ist_now().strftime('%Y-%m-%d %H:%M:%S'),
    }
    save_backtest_results(backtest_out)
    return backtest_out

# =============================================================================
# PERFORMANCE LAB — AUTO-SCHEDULE (Daily/Weekly Discovery)
# =============================================================================
def _auto_discover_worker(discover_type="intraday"):
    """Background worker for auto-discovery"""
    try:
        if discover_type == "intraday":
            discover_intraday_winners()
        elif discover_type == "swing":
            discover_swing_winners()
    except Exception as e:
        key = f"perf_{discover_type}_discovery"
        write_scan_status(key, "error", 0, str(e))

def start_auto_discovery(discover_type="intraday"):
    """Launch auto-discovery in background thread"""
    thread = threading.Thread(target=_auto_discover_worker, args=(discover_type,), daemon=True)
    thread.start()

# Auto-trigger: run intraday discovery after 3:35 PM on weekdays
_auto_disc_key = "perf_auto_discovery_triggered"
if _auto_disc_key not in st.session_state:
    st.session_state[_auto_disc_key] = False

def check_auto_discovery():
    """Check if we should auto-run discovery"""
    now = get_ist_now()
    if now.weekday() >= 5:
        return  # Weekend
    if st.session_state.get(_auto_disc_key):
        return  # Already triggered today

    # Intraday: after 3:35 PM
    if now.time() >= dtime(15, 35):
        today_str = now.strftime('%Y-%m-%d')
        filepath = os.path.join(INTRADAY_WINNERS_DIR, f"{today_str}.json")
        if not os.path.exists(filepath):
            st.session_state[_auto_disc_key] = True
            start_auto_discovery("intraday")

    # Swing: Friday after 3:35 PM
    if now.weekday() == 4 and now.time() >= dtime(15, 35):
        iso_year, iso_week, _ = now.isocalendar()
        week_key = f"{iso_year}-W{iso_week:02d}"
        filepath = os.path.join(SWING_WINNERS_DIR, f"{week_key}.json")
        if not os.path.exists(filepath):
            start_auto_discovery("swing")


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
PERF_SCAN_KEYS = ['perf_intraday_discovery', 'perf_swing_discovery']
any_scan_running = any(read_scan_status(k).get("status") == "running" for k in SCAN_KEYS + PERF_SCAN_KEYS)
if any_scan_running and AUTOREFRESH_AVAILABLE:
    st_autorefresh(interval=3000, key="scan_poll")

# Auto-trigger winner discovery after market close
check_auto_discovery()

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
    """Render signal cards with clear Risk:Reward metrics"""
    sorted_res = sorted(results, key=lambda x: x.get('Score', 0), reverse=True)
    cols = st.columns(2)
    for i, r in enumerate(sorted_res):
        with cols[i % 2]:
            is_bull = 'BUY' in str(r.get('Setup', '')) or 'BULL' in str(r.get('Setup', ''))
            card_class = "signal-card-buy" if is_bull else "signal-card-sell"
            score = r.get('Score', 0)
            score_class = "score-high" if score >= 75 else ("score-med" if score >= 55 else "score-low")

            # Risk & Reward calculation
            risk_pts = abs(r['Entry'] - r['SL'])
            reward_pts = abs(r['TP'] - r['Entry'])
            qty = r.get('Qty', 1)
            risk_inr = risk_pts * qty
            reward_inr = reward_pts * qty
            rr_val = r.get('RR', 2.0)

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
                    Entry ₹{r['Entry']:,.1f} → Target ₹{r['TP']:,.1f} | SL ₹{r['SL']:,.1f} • Qty {qty}
                </div>
                <div class='rr-badge'>
                    <span>⚖️ R:R 1:{rr_val:.1f}</span>
                    <span class='rr-risk'>🔻 Risk: -₹{risk_inr:,.0f} (-₹{risk_pts:,.1f}/sh)</span>
                    <span class='rr-profit'>🎯 Reward: +₹{reward_inr:,.0f} (+₹{reward_pts:,.1f}/sh)</span>
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
    """Render as sortable table with explicit Risk:Reward columns"""
    sorted_res = sorted(results, key=lambda x: x.get('Score', 0), reverse=True)
    rows = []
    for r in sorted_res:
        is_bull = 'BUY' in str(r.get('Setup', '')) or 'BULL' in str(r.get('Setup', ''))
        lp = live_prices.get(r['Symbol'] + '.NS', r.get('LTP', 0))
        status_text, _ = get_signal_status(lp, r['Entry'], r['SL'], r['TP'], is_bull) if lp else ("⏳", "#6b7294")
        warn = "⚠️" if r.get('FC_Warn', False) else ""
        risk_pts = abs(r['Entry'] - r['SL'])
        reward_pts = abs(r['TP'] - r['Entry'])
        qty = r.get('Qty', 1)
        risk_inr = risk_pts * qty
        reward_inr = reward_pts * qty
        rr_val = r.get('RR', 2.0)

        rows.append({
            'Symbol': f"{'🟢' if is_bull else '🔴'} {r['Symbol']} {warn}",
            'Setup': r.get('Setup', ''),
            'Score': r.get('Score', 0),
            '⚖️ R:R': f"1:{rr_val:.1f}",
            '🔻 Max Risk': f"₹{risk_inr:,.0f}",
            '🎯 Max Reward': f"+₹{reward_inr:,.0f}",
            'Live ₹': f"₹{lp:,.1f}" if lp else "—",
            'Entry': f"₹{r['Entry']:,.1f}",
            'SL': f"₹{r['SL']:,.1f}",
            'Target': f"₹{r['TP']:,.1f}",
            'Status': status_text,
            'RSI': r.get('RSI', 0),
            'Vol': f"{r.get('Vol_Ratio', 0)}x",
            'Pivot': r.get('Pivot', '—'),
            '📅 Signal': r.get('Signal_Time', '—'),
            'Qty': qty,
        })
    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True,
                     height=min(450, 35 * len(rows) + 38))

# =============================================================================
# STATEFUL MAIN NAVIGATION (Immune to resets on rerun)
# =============================================================================
main_nav_options = ["🌅 PRE-MARKET SCREENER", "⚡ LIVE SCREENER", "📈 PERFORMANCE LAB", "🎯 ENTRY PLAYBOOK", "🛠️ TOOLS"]
if "main_nav" not in st.session_state:
    st.session_state["main_nav"] = main_nav_options[0]

main_tab = st.radio(
    "Main Navigation",
    main_nav_options,
    key="main_nav",
    label_visibility="collapsed"
)

# ═══════════════════════════════════════════════════════════════
# TAB 1: PRE-MARKET SCREENER
# ═══════════════════════════════════════════════════════════════
if main_tab == "🌅 PRE-MARKET SCREENER":
    sub_pm_options = ["⚡ Pre-Market Open Heroes (09:08 AM)", "📅 Intraday EOD Picks (For Tomorrow)", "📈 Swing EOD Picks (Multi-Day)"]
    if "sub_pm_nav" not in st.session_state:
        st.session_state["sub_pm_nav"] = sub_pm_options[0]

    sub_pm = st.radio("Pre-Market Subsections", sub_pm_options, key="sub_pm_nav", label_visibility="collapsed")

    if sub_pm == "⚡ Pre-Market Open Heroes (09:08 AM)":
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

    elif sub_pm == "📅 Intraday EOD Picks (For Tomorrow)":
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

        if is_scanning:
            st.info("🔄 Scan running in background... You will stay on this tab!")
        else:
            saved, stime = load_session_results("intraday_eod")
            if saved:
                st.session_state['intraday_eod_results'] = saved
                st.session_state['intraday_eod_scan_time'] = stime
            if st.session_state.get('intraday_eod_results'):
                display_results(st.session_state['intraday_eod_results'], scan_key="intraday_eod", fetch_live=False)

    elif sub_pm == "📈 Swing EOD Picks (Multi-Day)":
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
            st.info("🔄 Scan running in background... You will stay on this tab!")
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
elif main_tab == "⚡ LIVE SCREENER":
    sub_live_options = ["⚡ Intraday Live (15m/30m)", "📊 Swing Live (1-Hour)", "🏃 One-Way Momentum Runners"]
    if "sub_live_nav" not in st.session_state:
        st.session_state["sub_live_nav"] = sub_live_options[0]

    sub_live = st.radio("Live Subsections", sub_live_options, key="sub_live_nav", label_visibility="collapsed")

    if sub_live == "⚡ Intraday Live (15m/30m)":
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
            st.info("🔄 Scanning in background... You will stay on this tab!")
        else:
            saved, stime = load_session_results("intraday_live")
            if saved:
                st.session_state['intraday_live_results'] = saved
                st.session_state['intraday_live_scan_time'] = stime
            if st.session_state.get('intraday_live_results'):
                display_results(st.session_state['intraday_live_results'], scan_key="intraday_live")

    elif sub_live == "📊 Swing Live (1-Hour)":
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
            st.info("🔄 Scanning in background... You will stay on this tab!")
        else:
            saved, stime = load_session_results("swing_live")
            if saved:
                st.session_state['swing_live_results'] = saved
                st.session_state['swing_live_scan_time'] = stime
            if st.session_state.get('swing_live_results'):
                display_results(st.session_state['swing_live_results'], scan_key="swing_live")

    elif sub_live == "🏃 One-Way Momentum Runners":
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
# TAB 3: HIGH-CONFIDENCE ENTRY PLAYBOOK
# ═══════════════════════════════════════════════════════════════
elif main_tab == "🎯 ENTRY PLAYBOOK":
    st.markdown("### 🎯 High-Confidence Trade Entry Blueprint")
    st.markdown("""
    <div class='glass-card'>
        <h4 style='color:#00ffaa;'>⚡ 1. Intraday One-Way Runner Entry Checklist (90%+ Confidence)</h4>
        <ul>
            <li><b>Timing:</b> 09:20 AM – 10:15 AM (after first 5-min/15-min candle forms).</li>
            <li><b>First Candle Rule:</b> First 5-min candle body should be <b>between 0.25% and 0.70%</b>. Avoid candles >0.70% (exhaustion risk).</li>
            <li><b>ORB Trigger:</b> Enter on break of 5-min/15-min High (BUY) or Low (SELL) with Volume > 2x 20-bar average.</li>
            <li><b>VWAP Defense:</b> Price must be comfortably ABOVE VWAP for BUY (BELOW VWAP for SELL). If price crosses back over VWAP, exit immediately.</li>
            <li><b>Pivot Launchpad:</b> BUY when price is opening near/above <b>Weekly Pivot (P) or R1</b>. SELL when below S1.</li>
            <li><b>RSI Room:</b> 15m RSI between <b>45 and 65</b> (do NOT enter if RSI > 75).</li>
            <li><b>Target & SL:</b> Minimum 1:2.0 R:R. Stop loss placed just below the breakout candle low / VWAP.</li>
        </ul>
    </div>
    <div class='glass-card'>
        <h4 style='color:#4da6ff;'>📈 2. Swing Trade Entry Checklist (Holding 3 to 15 Days)</h4>
        <ul>
            <li><b>Timing:</b> 02:45 PM – 03:20 PM (near market close to confirm daily candle structure).</li>
            <li><b>Confluence Alignment:</b> 1-Hour + Daily (1D) Supertrend must both point in the same direction.</li>
            <li><b>Monthly Pivot Anchor:</b> Stock must be holding above the <b>Monthly Pivot (P)</b> or testing R1 on rising Volume Profile POC.</li>
            <li><b>Volume Profile:</b> Buy on breakout above Value Area High (VAH) or on bounce from POC.</li>
            <li><b>Daily Pullback Rule:</b> Ideal entry is on a 2-3 day shallow pullback to the 20-day EMA on diminishing volume.</li>
            <li><b>Position Sizing:</b> Risk no more than 1% to 1.5% of total trading capital per trade.</li>
        </ul>
    </div>
    """, unsafe_allow_html=True)

# ═══════════════════════════════════════════════════════════════
# TAB 4: TOOLS
# ═══════════════════════════════════════════════════════════════
elif main_tab == "🛠️ TOOLS":
    sub_tool_options = ["📊 Pivot Level Calculator", "📖 Today's Signal Ledger", "ℹ️ System Architecture"]
    if "sub_tool_nav" not in st.session_state:
        st.session_state["sub_tool_nav"] = sub_tool_options[0]

    sub_tool = st.radio("Tool Sections", sub_tool_options, key="sub_tool_nav", label_visibility="collapsed")

    if sub_tool == "📊 Pivot Level Calculator":
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

    elif sub_tool == "📖 Today's Signal Ledger":
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

    elif sub_tool == "ℹ️ System Architecture":
        st.markdown("### ℹ️ QUANT-EDGE v19 Architecture")
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

# ═══════════════════════════════════════════════════════════════
# TAB: PERFORMANCE LAB
# ═══════════════════════════════════════════════════════════════
elif main_tab == "📈 PERFORMANCE LAB":
    sub_perf_options = ["📝 Manual Stock Logger", "🏆 Auto-Discovered Winners", "🧠 Pattern Intelligence", "📊 Backtest Results"]
    if "sub_perf_nav" not in st.session_state:
        st.session_state["sub_perf_nav"] = sub_perf_options[0]

    sub_perf = st.radio("Performance Lab Sections", sub_perf_options, key="sub_perf_nav", label_visibility="collapsed")

    # ─── SUBTAB 1: MANUAL STOCK LOGGER ───
    if sub_perf == "📝 Manual Stock Logger":
        st.markdown("### 📝 Manual Winner Logger")
        st.markdown("<div class='info-panel'>📋 Add stocks that performed well today. Technical data will be auto-captured. This data feeds into Pattern Intelligence to improve the screener.</div>", unsafe_allow_html=True)

        with st.form("manual_log_form", clear_on_submit=True):
            ml_col1, ml_col2, ml_col3 = st.columns([3, 1, 1])
            with ml_col1:
                ml_tickers = st.text_input("Stock Symbols (comma-separated)", placeholder="MCX, BANKBARODA, SAIL", key="ml_tickers")
            with ml_col2:
                ml_type = st.selectbox("Trade Type", ["Intraday", "Swing"], key="ml_type")
            with ml_col3:
                ml_dir = st.selectbox("Direction", ["BUY", "SELL"], key="ml_dir")

            ml_col4, ml_col5 = st.columns([1, 3])
            with ml_col4:
                ml_date = st.date_input("Date", value=get_ist_now().date(), key="ml_date")
            with ml_col5:
                ml_notes = st.text_input("Notes (optional)", placeholder="Strong opening drive, held VWAP all day", key="ml_notes")

            submitted = st.form_submit_button("💾 Save Winners", type="primary")

            if submitted and ml_tickers:
                existing_entries = load_manual_log()
                tickers_raw = [t.strip().upper() for t in ml_tickers.split(",") if t.strip()]
                new_entries = []
                for sym in tickers_raw:
                    ticker = sym + ".NS" if not sym.endswith(".NS") else sym
                    # Auto-fetch technical snapshot
                    try:
                        daily_df = yf.download(ticker, period="1y", interval="1d", progress=False, auto_adjust=True)
                        if isinstance(daily_df.columns, pd.MultiIndex):
                            daily_df.columns = daily_df.columns.get_level_values(0)
                        snapshot = _capture_technical_snapshot(ticker, daily_df, is_intraday=(ml_type == "Intraday"))
                    except Exception:
                        snapshot = {"ticker": ticker, "symbol": sym}
                    new_entries.append({
                        "symbol": sym.replace(".NS", ""),
                        "ticker": ticker,
                        "trade_type": ml_type,
                        "direction": ml_dir,
                        "date": str(ml_date),
                        "notes": ml_notes,
                        "added_at": get_ist_now().strftime('%Y-%m-%d %H:%M:%S'),
                        "snapshot": snapshot,
                    })
                existing_entries.extend(new_entries)
                save_manual_log(existing_entries)
                st.success(f"✅ Added {len(new_entries)} stocks to manual log!")
                st.rerun()

        # Display existing manual log
        manual_entries = load_manual_log()
        if manual_entries:
            st.markdown(f"#### 📋 Logged Winners ({len(manual_entries)} total)")

            # Download button
            csv_data = perf_data_to_csv(manual_entries, "manual_log")
            if csv_data:
                st.download_button("⬇️ Download CSV", csv_data, file_name="manual_winners_log.csv", mime="text/csv", key="dl_manual")

            # Display as table
            display_rows = []
            for idx, e in enumerate(manual_entries):
                snap = e.get("snapshot", {})
                display_rows.append({
                    "#": idx + 1,
                    "Symbol": e.get("symbol", ""),
                    "Type": e.get("trade_type", ""),
                    "Dir": f"{'🟢' if e.get('direction') == 'BUY' else '🔴'} {e.get('direction', '')}",
                    "Date": e.get("date", ""),
                    "ST": "↑" if snap.get("daily_st_dir") == 1 else "↓",
                    "RSI": snap.get("rsi_14", "—"),
                    "EMA": snap.get("ema_alignment", "—"),
                    "Vol": f"{snap.get('vol_ratio', 0)}x",
                    "Gap%": snap.get("gap_pct", "—"),
                    "Pivot": snap.get("pivot_pos", "—"),
                    "Sector": snap.get("sector", "—"),
                    "Notes": e.get("notes", ""),
                })
            st.dataframe(pd.DataFrame(display_rows), use_container_width=True, height=min(500, 35 * len(display_rows) + 38))

            # Delete entries
            with st.expander("🗑️ Delete Entries"):
                del_idx = st.number_input("Entry # to delete", min_value=1, max_value=len(manual_entries), value=1, key="del_idx")
                if st.button("🗑️ Delete", key="del_manual_btn"):
                    manual_entries.pop(del_idx - 1)
                    save_manual_log(manual_entries)
                    st.success("Deleted!")
                    st.rerun()
        else:
            st.info("📭 No manually logged winners yet. Add some above!")

    # ─── SUBTAB 2: AUTO-DISCOVERED WINNERS ───
    elif sub_perf == "🏆 Auto-Discovered Winners":
        st.markdown("### 🏆 Auto-Discovered Winners")
        st.markdown("<div class='info-panel'>🔍 Automatically finds stocks that moved ≥2% (intraday) or ≥7% (swing) from ORB edge. Runs daily after 3:35 PM. You can also trigger manually.</div>", unsafe_allow_html=True)

        disc_c1, disc_c2 = st.columns(2)

        with disc_c1:
            st.markdown("#### ⚡ Intraday Winners (≥2% ORB Move)")
            is_disc_running_i = render_scan_status("perf_intraday_discovery")

            if st.button("🔍 Discover Today's Intraday Winners", key="disc_intra_btn", type="primary"):
                start_auto_discovery("intraday")
                st.rerun()

            if is_disc_running_i:
                st.info("🔄 Scanning 219 stocks for intraday winners...")

        with disc_c2:
            st.markdown("#### 📈 Swing Winners (≥7% Weekly Move)")
            is_disc_running_s = render_scan_status("perf_swing_discovery")

            if st.button("🔍 Discover This Week's Swing Winners", key="disc_swing_btn", type="primary"):
                start_auto_discovery("swing")
                st.rerun()

            if is_disc_running_s:
                st.info("🔄 Scanning 219 stocks for swing winners...")

        st.markdown("---")

        # Show Intraday Winners
        intraday_dates = get_all_intraday_winner_dates()
        if intraday_dates:
            selected_date = st.selectbox("📅 Select Intraday Date", intraday_dates[::-1], key="sel_intra_date")
            iw_data = load_perf_winners(os.path.join(INTRADAY_WINNERS_DIR, f"{selected_date}.json"))
            if iw_data and iw_data.get("winners"):
                st.markdown(f"**{iw_data['winners_found']} winners found** on {selected_date} (scanned {iw_data['total_scanned']} stocks)")

                # Download
                flat_winners = []
                for w in iw_data["winners"]:
                    snap = w.get("snapshot", {})
                    flat_winners.append({
                        "Symbol": w["symbol"], "Direction": w["direction"],
                        "Move%": w["move_pct"], "1st Candle Body%": w.get("fc_body_pct", ""),
                        "1st Candle Vol Ratio": w.get("fc_vol_ratio", ""),
                        "Day High": w.get("day_high", ""), "Day Low": w.get("day_low", ""),
                        "ST Dir": snap.get("daily_st_dir", ""), "RSI": snap.get("rsi_14", ""),
                        "EMA Alignment": snap.get("ema_alignment", ""),
                        "Vol Ratio": snap.get("vol_ratio", ""),
                        "Gap%": snap.get("gap_pct", ""),
                        "Pivot Pos": snap.get("pivot_pos", ""),
                        "ADL": snap.get("adl_trend", ""),
                        "Sector": snap.get("sector", ""),
                    })
                csv_iw = perf_data_to_csv(flat_winners, "intraday_winners")
                if csv_iw:
                    st.download_button("⬇️ Download Intraday Winners CSV", csv_iw,
                                       file_name=f"intraday_winners_{selected_date}.csv", mime="text/csv", key="dl_iw")

                # Display winner cards
                iw_cols = st.columns(2)
                for idx, w in enumerate(iw_data["winners"]):
                    with iw_cols[idx % 2]:
                        snap = w.get("snapshot", {})
                        is_buy = w["direction"] == "BUY"
                        card_cls = "signal-card-buy" if is_buy else "signal-card-sell"
                        emoji = "🟢" if is_buy else "🔴"
                        hist_similar = w.get("historical_similar", [])
                        hist_text = ""
                        if hist_similar:
                            avg_fwd = np.mean([h.get("fwd_return_5d", 0) for h in hist_similar])
                            hist_text = f"<div style='font-size:10px;color:#6b7294;margin-top:4px;'>📜 {len(hist_similar)} similar past setups found • Avg 5-day fwd return: <b style='color:{('#00ffaa' if avg_fwd > 0 else '#ff3366')}'>{avg_fwd:+.1f}%</b></div>"

                        st.markdown(f"""
                        <div class='signal-card {card_cls}'>
                            <div style='display:flex;justify-content:space-between;align-items:center;'>
                                <span class='card-symbol'>{emoji} {w['symbol']}</span>
                                <span class='card-score score-high'>+{w['move_pct']:.1f}%</span>
                            </div>
                            <div class='card-setup'>{w['direction']} WINNER • {snap.get('sector', 'N/A')}</div>
                            <div class='card-prices'>ORB: ₹{w.get('fc_high', 0):,.1f} / ₹{w.get('fc_low', 0):,.1f} → Day: ₹{w.get('day_high', 0):,.1f} / ₹{w.get('day_low', 0):,.1f}</div>
                            <div class='card-indicators'>
                                ST {'↑' if snap.get('daily_st_dir') == 1 else '↓'} •
                                RSI {snap.get('rsi_14', '—')} •
                                EMA {snap.get('ema_alignment', '—')} •
                                Vol {snap.get('vol_ratio', 0)}x •
                                Gap {snap.get('gap_pct', 0):+.1f}% •
                                <span class='pivot-badge pivot-{'above' if 'above' in snap.get('pivot_pos', '') else 'below'}'>{snap.get('pivot_pos', '—')}</span>
                            </div>
                            <div style='font-size:10px;color:#4a5078;margin-top:4px;'>1st Candle: Body {w.get('fc_body_pct', 0):.2f}% • Vol Ratio {w.get('fc_vol_ratio', 0):.1f}x • ADL: {snap.get('adl_trend', '—')}</div>
                            {hist_text}
                        </div>
                        """, unsafe_allow_html=True)

                        # Show historical similar setups in expander
                        if hist_similar:
                            with st.expander(f"📜 {len(hist_similar)} Similar Past Setups", expanded=False):
                                hist_df = pd.DataFrame(hist_similar)
                                st.dataframe(hist_df, use_container_width=True)
            else:
                st.info(f"No winners found for {selected_date}.")
        else:
            st.info("📭 No intraday winner data yet. Discovery runs automatically after 3:35 PM, or click the button above.")

        # Show Swing Winners
        st.markdown("---")
        swing_weeks = get_all_swing_winner_weeks()
        if swing_weeks:
            selected_week = st.selectbox("📅 Select Swing Week", swing_weeks[::-1], key="sel_swing_week")
            sw_data = load_perf_winners(os.path.join(SWING_WINNERS_DIR, f"{selected_week}.json"))
            if sw_data and sw_data.get("winners"):
                st.markdown(f"**{sw_data['winners_found']} swing winners** in week {selected_week}")

                flat_sw = []
                for w in sw_data["winners"]:
                    snap = w.get("snapshot", {})
                    flat_sw.append({
                        "Symbol": w["symbol"], "Direction": w["direction"],
                        "Move%": w["move_pct"],
                        "Week High": w.get("week_high", ""), "Week Low": w.get("week_low", ""),
                        "ST Dir": snap.get("daily_st_dir", ""), "RSI": snap.get("rsi_14", ""),
                        "EMA": snap.get("ema_alignment", ""),
                        "Pivot": snap.get("pivot_pos", ""),
                        "Sector": snap.get("sector", ""),
                    })
                csv_sw = perf_data_to_csv(flat_sw, "swing_winners")
                if csv_sw:
                    st.download_button("⬇️ Download Swing Winners CSV", csv_sw,
                                       file_name=f"swing_winners_{selected_week}.csv", mime="text/csv", key="dl_sw")

                sw_cols = st.columns(2)
                for idx, w in enumerate(sw_data["winners"]):
                    with sw_cols[idx % 2]:
                        snap = w.get("snapshot", {})
                        is_buy = w["direction"] == "BUY"
                        emoji = "🟢" if is_buy else "🔴"
                        card_cls = "signal-card-buy" if is_buy else "signal-card-sell"
                        st.markdown(f"""
                        <div class='signal-card {card_cls}'>
                            <span class='card-symbol'>{emoji} {w['symbol']}</span>
                            <span class='card-score score-high'>+{w['move_pct']:.1f}% weekly</span>
                            <div class='card-setup'>{w['direction']} SWING WINNER • {snap.get('sector', 'N/A')}</div>
                            <div class='card-indicators'>ST {'↑' if snap.get('daily_st_dir') == 1 else '↓'} • RSI {snap.get('rsi_14', '—')} • EMA {snap.get('ema_alignment', '—')} • Pivot {snap.get('pivot_pos', '—')}</div>
                        </div>
                        """, unsafe_allow_html=True)
            else:
                st.info(f"No swing winners for week {selected_week}.")
        else:
            st.info("📭 No swing winner data yet. Discovery runs automatically on Fridays after 3:35 PM.")

    # ─── SUBTAB 3: PATTERN INTELLIGENCE ───
    elif sub_perf == "🧠 Pattern Intelligence":
        st.markdown("### 🧠 Pattern Intelligence — Golden Rules")
        st.markdown("<div class='info-panel'>🧪 Analyzes all winners (auto-discovered + manually logged) to find the most common technical traits. Use these rules to improve stock selection.</div>", unsafe_allow_html=True)

        if st.button("🔬 Analyze All Winner Patterns", key="analyze_btn", type="primary"):
            with st.spinner("Analyzing all winner data..."):
                patterns = analyze_winner_patterns()
                st.success("✅ Pattern analysis complete!")

        patterns = load_pattern_analysis()
        if patterns:
            st.markdown(f"<div class='last-updated'>🕐 Last analyzed: {patterns.get('analyzed_at', 'Never')}</div>", unsafe_allow_html=True)

            pat_c1, pat_c2 = st.columns(2)

            for col, label, key, sample_key in [
                (pat_c1, "⚡ Intraday Winners", "intraday", "intraday_sample_size"),
                (pat_c2, "📈 Swing Winners", "swing", "swing_sample_size")
            ]:
                with col:
                    st.markdown(f"#### {label}")
                    rules = patterns.get(key, {})
                    sample_size = patterns.get(sample_key, 0)
                    st.caption(f"Sample size: {sample_size} winners")

                    if not rules or rules.get("_total_winners", 0) == 0:
                        st.info("🔍 Not enough data yet. Add winners or run auto-discovery first.")
                        continue

                    # Golden Rules — sorted by frequency
                    sorted_rules = sorted(
                        [(k, v) for k, v in rules.items() if isinstance(v, (int, float)) and not k.startswith("_")],
                        key=lambda x: x[1], reverse=True
                    )

                    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
                    for idx, (rule_name, pct) in enumerate(sorted_rules):
                        medal = medals[idx] if idx < len(medals) else "•"
                        color = "#00ffaa" if pct >= 70 else ("#ffaa00" if pct >= 50 else "#ff6b6b")
                        bar_width = min(pct, 100)
                        st.markdown(f"""
                        <div style='margin-bottom:8px;'>
                            <div style='font-size:12px;font-weight:600;color:#e2e4ef;'>{medal} {pct:.0f}% — {rule_name}</div>
                            <div style='background:rgba(255,255,255,0.05);border-radius:4px;height:8px;margin-top:2px;'>
                                <div style='background:{color};width:{bar_width}%;height:100%;border-radius:4px;transition:width 0.5s ease;'></div>
                            </div>
                        </div>
                        """, unsafe_allow_html=True)

                    # Sector distribution
                    sector_dist = rules.get("_sector_distribution", {})
                    if sector_dist:
                        st.markdown("##### 🏭 Sector Distribution")
                        sector_df = pd.DataFrame([
                            {"Sector": k, "Winners": v} for k, v in
                            sorted(sector_dist.items(), key=lambda x: x[1], reverse=True)
                        ])
                        st.dataframe(sector_df, use_container_width=True, height=200)

            # Download patterns
            st.markdown("---")
            pat_json = json.dumps(patterns, indent=2, default=str)
            st.download_button("⬇️ Download Pattern Analysis (JSON)", pat_json,
                               file_name="pattern_analysis.json", mime="application/json", key="dl_pat")
        else:
            st.info("🔍 No pattern analysis yet. Click 'Analyze' after collecting winner data.")

    # ─── SUBTAB 4: BACKTEST RESULTS ───
    elif sub_perf == "📊 Backtest Results":
        st.markdown("### 📊 Strategy Backtest — Hit Rate Analysis")
        st.markdown("<div class='info-panel'>📈 Tests whether our screener would have found the actual winners before the move happened. Requires auto-discovered winner data for past dates.</div>", unsafe_allow_html=True)

        bt_c1, bt_c2 = st.columns(2)
        with bt_c1:
            if st.button("🧪 Run Intraday Backtest (60 days)", key="bt_intra_btn", type="primary"):
                with st.spinner("Backtesting intraday strategy..."):
                    bt_result = backtest_intraday_strategy(lookback_days=60)
                    st.success(f"✅ Backtest complete! Avg Hit Rate: {bt_result.get('avg_hit_rate', 0):.1f}%")

        with bt_c2:
            if st.button("🧪 Run Swing Backtest (12 weeks)", key="bt_swing_btn", type="primary"):
                with st.spinner("Backtesting swing strategy..."):
                    bt_result = backtest_swing_strategy(lookback_weeks=12)
                    st.success(f"✅ Backtest complete! Avg Hit Rate: {bt_result.get('avg_hit_rate', 0):.1f}%")

        bt_data = load_backtest_results()
        if bt_data:
            st.markdown(f"<div class='last-updated'>🕐 Last backtest: {bt_data.get('run_at', 'Never')}</div>", unsafe_allow_html=True)

            bt_type = bt_data.get("type", "intraday")

            # Summary metrics
            m1, m2, m3, m4 = st.columns(4)
            with m1:
                avg_hr = bt_data.get("avg_hit_rate", 0)
                hr_color = "#00ffaa" if avg_hr >= 50 else ("#ffaa00" if avg_hr >= 30 else "#ff3366")
                st.markdown(f"""<div class='metric-card'>
                    <div class='metric-header'>AVG HIT RATE</div>
                    <div class='metric-val' style='color:{hr_color};'>{avg_hr:.1f}%</div>
                </div>""", unsafe_allow_html=True)
            with m2:
                total_w = bt_data.get("total_winners_found", sum(r.get("total_winners", 0) for r in bt_data.get("daily_results", bt_data.get("weekly_results", []))))
                st.markdown(f"""<div class='metric-card'>
                    <div class='metric-header'>TOTAL WINNERS</div>
                    <div class='metric-val'>{total_w}</div>
                </div>""", unsafe_allow_html=True)
            with m3:
                total_hits = bt_data.get("total_screener_hits", sum(r.get("screener_found", 0) for r in bt_data.get("daily_results", bt_data.get("weekly_results", []))))
                st.markdown(f"""<div class='metric-card'>
                    <div class='metric-header'>SCREENER HITS</div>
                    <div class='metric-val' style='color:#00ffaa;'>{total_hits}</div>
                </div>""", unsafe_allow_html=True)
            with m4:
                days_tested = len(bt_data.get("daily_results", bt_data.get("weekly_results", [])))
                period_label = "Days" if bt_type == "intraday" else "Weeks"
                st.markdown(f"""<div class='metric-card'>
                    <div class='metric-header'>{period_label.upper()} TESTED</div>
                    <div class='metric-val'>{days_tested}</div>
                </div>""", unsafe_allow_html=True)

            # Results table
            result_rows = bt_data.get("daily_results", bt_data.get("weekly_results", []))
            if result_rows:
                st.markdown("#### 📊 Detailed Results")
                bt_df = pd.DataFrame(result_rows)
                date_col = "date" if "date" in bt_df.columns else "week"
                display_cols = [c for c in [date_col, "total_winners", "screener_found", "hit_rate", "missed"] if c in bt_df.columns]
                st.dataframe(bt_df[display_cols], use_container_width=True, height=400)

                # Hit rate chart
                if "hit_rate" in bt_df.columns and len(bt_df) > 1:
                    chart_df = bt_df[bt_df["total_winners"] > 0].copy()
                    if not chart_df.empty:
                        fig = go.Figure()
                        fig.add_trace(go.Scatter(
                            x=chart_df[date_col], y=chart_df["hit_rate"],
                            mode='lines+markers', name='Hit Rate %',
                            line=dict(color='#00ffaa', width=2),
                            marker=dict(size=6)
                        ))
                        fig.add_hline(y=50, line_dash="dash", line_color="#ffaa00", annotation_text="50% target")
                        fig.update_layout(
                            title="Screener Hit Rate Over Time",
                            template="plotly_dark",
                            paper_bgcolor='rgba(0,0,0,0)',
                            plot_bgcolor='rgba(0,0,0,0)',
                            height=350,
                            margin=dict(l=40, r=20, t=40, b=40)
                        )
                        st.plotly_chart(fig, use_container_width=True)

                # Download
                bt_csv = bt_df.to_csv(index=False)
                st.download_button("⬇️ Download Backtest Results CSV", bt_csv,
                                   file_name=f"backtest_{bt_type}_results.csv", mime="text/csv", key="dl_bt")
        else:
            st.info("📭 No backtest results yet. Run a backtest after collecting auto-discovered winner data.")

# =============================================================================
# FOOTER
# =============================================================================
st.markdown("---")
st.markdown(f"<div style='text-align:center;color:#3a4060;font-size:10px;padding:8px;'>👑 QUANT-EDGE v19 • Multi-TF Confluence • Pivots • VP • Institutional Psychology • Performance Lab • {get_ist_now().strftime('%d %b %Y, %I:%M %p IST')}</div>", unsafe_allow_html=True)
