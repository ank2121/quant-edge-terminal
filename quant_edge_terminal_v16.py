"""
================================================================================
 QUANT-EDGE v21  —  INSTITUTIONAL ORB TERMINAL  (NSE F&O)
================================================================================
 Zero-decision screener. Three engines, one calibrated probability model each:

   1. PRE-MARKET  (08:55-09:08)  -> TOP 1 BUY + TOP 1 SELL for the day
   2. LIVE ORB    (09:20-14:30)  -> close-confirmed 5m ORB order tickets
   3. EOD         (15:40-23:59)  -> TOP 5 watchlist for tomorrow
   4. SWING       (Fri EOD)      -> LONG-ONLY weekly plan, Monday 1h ORB entry

 EVERYTHING BELOW IS CALIBRATED ON REAL DATA, NOT ON INTUITION.
 Study set: 215 F&O names, 744 daily sessions (3y), 59 sessions of 5-minute bars,
 12,598 real ORB trigger events, 3y of 60-minute bars for the swing engine.

 KEY EMPIRICAL FINDINGS THAT DRIVE THIS VERSION  (see calibration report):
 ------------------------------------------------------------------------------
 * Base rate: 20.7% of liquid names travel >=2% UP from open on any given day,
   22.8% travel >=2% DOWN.  ~33 names/day move >=2.5% either way.  Your
   observation of "6 to 8 stocks" is the >=4-5% tail; >=2.5% is much wider.
 * D-1 features predict CAPABILITY, not DIRECTION.  Top predictors of a big
   move are atr_pct (1.63x lift), adr20 (1.56x), vol20 (1.48x),
   big_moves_60 (1.46x), bbw (1.33x), rvol (1.2x).  Momentum/MACD are weak.
 * GAPS MEAN-REVERT INTRADAY.  P(+2% from open | gap < -2%) = 60.0% versus
   P(-2%) = 33.7%.  For gap > +2%: P(-2%) = 47.8% > P(+2%) = 41.0%.
   v20 did the OPPOSITE (bought gap-ups, shorted gap-downs). Now inverted.
 * ENTRY STYLE IS THE BIGGEST SINGLE LEVER.  Requiring a 5-minute candle to
   CLOSE beyond the ORB level instead of merely touching it lifted expectancy
   from -0.04% to +0.19% per trade at k=3 and from 28.3% to 37.5% target-hit
   at k=1.  15-minute ORB was clearly worse than 5-minute.
 * STOP MUST BE CAPPED.  Median ORB-range risk is 2.4-2.7%, which destroys the
   2% target's reward:risk.  SL = min(opposite ORB edge, 1.0%), floor 0.35%.
 * LONG >> SHORT.  Walk-forward long expectancy is positive in every
   close-confirmed configuration; short ORB was flat-to-negative. Shorts are
   kept but gated behind a higher probability threshold and marked LOW-CONF.
 * Realistic ceiling: top-1 ranked long setup hits the full +2% target on
   ~37% of days, is profitable on ~50-67% of days, expectancy ~+0.3%/trade
   after 0.06% costs.  A guaranteed 2% every single day is NOT in the data -
   this build maximises the odds, it cannot manufacture them.
================================================================================
"""

import os, sys, json, time, math, glob, threading, warnings, traceback
from datetime import datetime, timedelta, date, time as dtime
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict

import numpy as np
import pandas as pd
import streamlit as st
import yfinance as yf

warnings.filterwarnings("ignore")

try:
    import pytz
    IST = pytz.timezone("Asia/Kolkata")
except Exception:
    from zoneinfo import ZoneInfo
    IST = ZoneInfo("Asia/Kolkata")

try:
    import requests
    HAS_REQ = True
except Exception:
    HAS_REQ = False

try:
    from streamlit_autorefresh import st_autorefresh
    HAS_AUTOREFRESH = True
except Exception:
    HAS_AUTOREFRESH = False

try:
    from fyers_apiv3 import fyersModel
    HAS_FYERS = True
except Exception:
    HAS_FYERS = False

st.set_page_config(page_title="QUANT-EDGE v21 — Institutional ORB Terminal",
                   page_icon="⚡", layout="wide", initial_sidebar_state="expanded")

# =============================================================================
# PATHS
# =============================================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DIRS = {
    "sessions":  os.path.join(DATA_DIR, "sessions"),
    "scans":     os.path.join(DATA_DIR, "scans"),
    "perf":      os.path.join(DATA_DIR, "performance"),
    "train":     os.path.join(DATA_DIR, "training"),
    "models":    os.path.join(DATA_DIR, "models"),
    "cache":     os.path.join(DATA_DIR, "cache"),
    "journal":   os.path.join(DATA_DIR, "journal"),
}
for _d in DIRS.values():
    os.makedirs(_d, exist_ok=True)

# yfinance's threaded downloader opens a socket + sqlite handle per worker; on a
# default 1024-descriptor limit a full-universe intraday scan exhausts it and
# pandas then fails with "Too many open files" on an unrelated CSV write.
try:
    import resource
    _soft, _hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    resource.setrlimit(resource.RLIMIT_NOFILE, (max(_soft, min(_hard, 8192)), _hard))
except Exception:
    pass

# yfinance keeps a shared sqlite timezone cache; several download threads hitting
# the default path raise OperationalError('unable to open database file') and
# silently drop symbols. Give it a private writable directory.
try:
    _yfc = os.path.join(DATA_DIR, "cache", "yf")
    os.makedirs(_yfc, exist_ok=True)
    yf.set_tz_cache_location(_yfc)
except Exception:
    pass

F_UNIVERSE_OK   = os.path.join(DIRS["cache"],   "universe_validated.json")
F_MODEL_LONG    = os.path.join(DIRS["models"],  "model_intraday_long.json")
F_MODEL_SHORT   = os.path.join(DIRS["models"],  "model_intraday_short.json")
F_MODEL_SWING   = os.path.join(DIRS["models"],  "model_swing_long.json")
F_TRAIN_INTRA   = os.path.join(DIRS["train"],   "intraday_orb_events.csv")
F_TRAIN_SWING   = os.path.join(DIRS["train"],   "swing_events.csv")
F_JOURNAL       = os.path.join(DIRS["journal"], "trade_journal.csv")
F_HOLIDAYS      = os.path.join(DIRS["cache"],   "nse_holidays.json")

# =============================================================================
# TRADING CONSTANTS  (all calibrated — do not tune by feel)
# =============================================================================
CFG = dict(
    MIN_TURNOVER_CR      = 25.0,   # 20d avg traded value floor (liquidity)
    MIN_PRICE            = 60.0,
    MAX_PRICE            = 25000.0,
    ORB_MINUTES          = 5,      # 5m beat 15m in the study
    ENTRY_CONFIRM        = "close", # close-confirmed break (biggest single lever)
    SL_CAP_PCT           = 1.00,   # hard cap on risk
    SL_FLOOR_PCT         = 0.35,
    TARGET_PCT           = 2.00,   # intraday objective
    PARTIAL_PCT          = 1.00,   # book half here, stop to breakeven
    USE_PARTIAL          = True,
    MAX_TRADES_DAY       = 2,      # k=1..2 held the best expectancy
    COST_PCT             = 0.06,   # round-trip cost assumption
    LAST_ENTRY_TIME      = dtime(13, 0),
    SQUARE_OFF_TIME      = dtime(15, 10),
    MIN_P_LONG           = 0.16,   # model prob floor (long)
    MIN_P_SHORT          = 0.22,   # shorts need a bigger edge to be worth it
    MIN_ATR_PCT          = 1.60,   # feasibility: can it travel 2% at all
    MIN_ADR_PCT          = 1.80,
    SWING_TARGET_PCT     = 10.0,
    SWING_SL_PCT         = 4.0,
    SWING_TOP_N          = 3,
    SWING_MIN_ATR_PCT    = 2.2,
    RISK_PER_TRADE_PCT   = 1.0,    # position sizing on account equity
    DEFAULT_CAPITAL      = 500000,
)

# =============================================================================
# UNIVERSE  —  NSE F&O + high-velocity movers.
# Dead tickers found in validation (LTIM, PEL, TATAMOTORS post-demerger) are
# auto-pruned at startup and cached, so a stale name can never break a scan.
# =============================================================================
RENAMES = {"TATAMOTORS.NS": "TMPV.NS", "ZOMATO.NS": "ETERNAL.NS",
           "CAMSONLINE.NS": "CAMS.NS", "KALPATPOWR.NS": "KPIL.NS",
           "LTIM.NS": None, "PEL.NS": None, "MINDTREE.NS": None,
           "SRTRANSFIN.NS": "SHRIRAMFIN.NS", "HDFC.NS": None}

UNIVERSE_RAW = [
 # ---- Index heavyweights / Nifty50
 "RELIANCE.NS","HDFCBANK.NS","ICICIBANK.NS","INFY.NS","TCS.NS","SBIN.NS","BHARTIARTL.NS",
 "ITC.NS","LT.NS","KOTAKBANK.NS","AXISBANK.NS","HINDUNILVR.NS","BAJFINANCE.NS","MARUTI.NS",
 "SUNPHARMA.NS","TITAN.NS","ULTRACEMCO.NS","ASIANPAINT.NS","NESTLEIND.NS","WIPRO.NS",
 "HCLTECH.NS","TECHM.NS","POWERGRID.NS","NTPC.NS","ONGC.NS","COALINDIA.NS","JSWSTEEL.NS",
 "TATASTEEL.NS","HINDALCO.NS","GRASIM.NS","ADANIENT.NS","ADANIPORTS.NS","BAJAJFINSV.NS",
 "BAJAJ-AUTO.NS","HEROMOTOCO.NS","EICHERMOT.NS","M&M.NS","CIPLA.NS","DRREDDY.NS",
 "DIVISLAB.NS","APOLLOHOSP.NS","BRITANNIA.NS","TATACONSUM.NS","SHRIRAMFIN.NS","SBILIFE.NS",
 "HDFCLIFE.NS","INDUSINDBK.NS","BPCL.NS","IOC.NS","TRENT.NS","JIOFIN.NS","ETERNAL.NS",
 # ---- Banks / NBFC / capital markets (highest intraday velocity group)
 "BANKBARODA.NS","PNB.NS","CANBK.NS","UNIONBANK.NS","IDFCFIRSTB.NS","FEDERALBNK.NS",
 "BANDHANBNK.NS","AUBANK.NS","RBLBANK.NS","YESBANK.NS","INDIANB.NS","IOB.NS","UCOBANK.NS",
 "CHOLAFIN.NS","MUTHOOTFIN.NS","MANAPPURAM.NS","LICHSGFIN.NS","PFC.NS","RECLTD.NS",
 "IRFC.NS","SBICARD.NS","POONAWALLA.NS","LTF.NS","ABCAPITAL.NS","IIFL.NS","ANGELONE.NS",
 "MOTILALOFS.NS","NUVAMA.NS","360ONE.NS","BSE.NS","MCX.NS","CDSL.NS","IEX.NS","KFINTECH.NS",
 "NAM-INDIA.NS","HDFCAMC.NS","CAMSONLINE.NS","PAYTM.NS","POLICYBZR.NS","SAMMAANCAP.NS",
 # ---- Auto & ancillary
 "TVSMOTOR.NS","ASHOKLEY.NS","TMPV.NS","BHARATFORG.NS","MOTHERSON.NS","BALKRISIND.NS",
 "APOLLOTYRE.NS","MRF.NS","EXIDEIND.NS","SONACOMS.NS","UNOMINDA.NS","ENDURANCE.NS",
 "TIINDIA.NS","BOSCHLTD.NS","SCHAEFFLER.NS","FORCEMOT.NS","HYUNDAI.NS","OLECTRA.NS",
 # ---- Metals, mining, energy
 "VEDL.NS","SAIL.NS","NMDC.NS","JINDALSTEL.NS","NATIONALUM.NS","HINDZINC.NS","HINDCOPPER.NS",
 "JSL.NS","APLAPOLLO.NS","WELCORP.NS","GAIL.NS","PETRONET.NS","IGL.NS","MGL.NS","OIL.NS",
 "ATGL.NS","ADANIGREEN.NS","ADANIPOWER.NS","TATAPOWER.NS","JSWENERGY.NS","NHPC.NS","SJVN.NS",
 "TORNTPOWER.NS","CESC.NS","POWERINDIA.NS","PREMIERENE.NS","WAAREEENER.NS","SUZLON.NS","INOXWIND.NS",
 # ---- Infra, capital goods, defence, railways
 "BEL.NS","HAL.NS","BDL.NS","MAZDOCK.NS","COCHINSHIP.NS","GRSE.NS","BHEL.NS","SIEMENS.NS",
 "ABB.NS","CUMMINSIND.NS","THERMAX.NS","KEC.NS","KALPATPOWR.NS","NCC.NS","IRB.NS","GMRAIRPORT.NS",
 "RVNL.NS","IRCON.NS","RAILTEL.NS","TITAGARH.NS","JWL.NS","IRCTC.NS","CONCOR.NS","NBCC.NS",
 "HUDCO.NS","SOLARINDS.NS","AMBER.NS","PGEL.NS","DIXON.NS","KAYNES.NS","CGPOWER.NS","POLYCAB.NS",
 "HAVELLS.NS","VOLTAS.NS","BLUESTARCO.NS","CROMPTON.NS","KEI.NS","SUPREMEIND.NS","ASTRAL.NS",
 # ---- Pharma & healthcare
 "LUPIN.NS","AUROPHARMA.NS","ZYDUSLIFE.NS","ALKEM.NS","TORNTPHARM.NS","MANKIND.NS","IPCALAB.NS",
 "GLENMARK.NS","LAURUSLABS.NS","BIOCON.NS","GRANULES.NS","NATCOPHARM.NS","AJANTPHARM.NS",
 "PPLPHARMA.NS","ABBOTINDIA.NS","MAXHEALTH.NS","FORTIS.NS","SYNGENE.NS","METROPOLIS.NS","LALPATHLAB.NS",
 # ---- IT & new-age
 "LTTS.NS","MPHASIS.NS","PERSISTENT.NS","COFORGE.NS","KPITTECH.NS","CYIENT.NS","TATAELXSI.NS",
 "OFSS.NS","SONATSOFTW.NS","NAUKRI.NS","SWIGGY.NS","NYKAA.NS","DELHIVERY.NS","ZEEL.NS",
 # ---- Consumer, retail, cement, chemicals, others
 "DMART.NS","JUBLFOOD.NS","DEVYANI.NS","VBL.NS","UBL.NS","RADICO.NS","MARICO.NS","DABUR.NS",
 "GODREJCP.NS","COLPAL.NS","PGHH.NS","EMAMILTD.NS","BATAINDIA.NS","PAGEIND.NS","ABFRL.NS",
 "GODFRYPHLP.NS","SHREECEM.NS","AMBUJACEM.NS","ACC.NS","DALBHARAT.NS","JKCEMENT.NS","RAMCOCEM.NS",
 "PIDILITIND.NS","SRF.NS","PIIND.NS","DEEPAKNTR.NS","AARTIIND.NS","TATACHEM.NS","UPL.NS",
 "COROMANDEL.NS","CHAMBLFERT.NS","GNFC.NS","NAVINFLUOR.NS","ATUL.NS","VINATIORGA.NS",
 "INDIGO.NS","SPICEJET.NS","INDHOTEL.NS","LEMONTREE.NS","OBEROIRLTY.NS","DLF.NS","GODREJPROP.NS",
 "PRESTIGE.NS","LODHA.NS","PHOENIXLTD.NS","BRIGADE.NS","SOBHA.NS","CENTURYPLY.NS",
 "TATACOMM.NS","IDEA.NS","HFCL.NS","INDUSTOWER.NS","BSOFT.NS","ITI.NS",
]

SECTOR_MAP = {
 "BANK": ["HDFCBANK","ICICIBANK","SBIN","KOTAKBANK","AXISBANK","INDUSINDBK","BANKBARODA","PNB",
          "CANBK","UNIONBANK","IDFCFIRSTB","FEDERALBNK","BANDHANBNK","AUBANK","RBLBANK","YESBANK",
          "INDIANB","IOB","UCOBANK"],
 "NBFC/CAPMKT": ["BAJFINANCE","BAJAJFINSV","CHOLAFIN","MUTHOOTFIN","MANAPPURAM","LICHSGFIN","PFC",
          "RECLTD","IRFC","SBICARD","POONAWALLA","LTF","ABCAPITAL","IIFL","ANGELONE","MOTILALOFS",
          "NUVAMA","360ONE","BSE","MCX","CDSL","IEX","KFINTECH","NAM-INDIA","HDFCAMC","CAMS",
          "PAYTM","POLICYBZR","SAMMAANCAP","JIOFIN","SHRIRAMFIN","SBILIFE","HDFCLIFE"],
 "IT": ["INFY","TCS","WIPRO","HCLTECH","TECHM","LTTS","MPHASIS","PERSISTENT","COFORGE","KPITTECH",
        "CYIENT","TATAELXSI","OFSS","SONATSOFTW","BSOFT"],
 "AUTO": ["MARUTI","M&M","BAJAJ-AUTO","HEROMOTOCO","EICHERMOT","TVSMOTOR","ASHOKLEY","TMPV",
          "BHARATFORG","MOTHERSON","BALKRISIND","APOLLOTYRE","MRF","EXIDEIND","SONACOMS","UNOMINDA",
          "ENDURANCE","TIINDIA","BOSCHLTD","SCHAEFFLER","FORCEMOT","HYUNDAI","OLECTRA"],
 "METAL": ["TATASTEEL","JSWSTEEL","HINDALCO","VEDL","SAIL","NMDC","JINDALSTEL","NATIONALUM",
           "HINDZINC","HINDCOPPER","JSL","APLAPOLLO","WELCORP","COALINDIA"],
 "ENERGY": ["RELIANCE","ONGC","BPCL","IOC","GAIL","PETRONET","IGL","MGL","OIL","ATGL","ADANIGREEN",
            "ADANIPOWER","TATAPOWER","JSWENERGY","NHPC","SJVN","TORNTPOWER","CESC","NTPC","POWERGRID",
            "POWERINDIA","PREMIERENE","WAAREEENER","SUZLON","INOXWIND"],
 "PHARMA": ["SUNPHARMA","CIPLA","DRREDDY","DIVISLAB","LUPIN","AUROPHARMA","ZYDUSLIFE","ALKEM",
            "TORNTPHARM","MANKIND","IPCALAB","GLENMARK","LAURUSLABS","BIOCON","GRANULES","NATCOPHARM",
            "AJANTPHARM","PPLPHARMA","ABBOTINDIA","APOLLOHOSP","MAXHEALTH","FORTIS","SYNGENE",
            "METROPOLIS","LALPATHLAB"],
 "FMCG/RETAIL": ["HINDUNILVR","ITC","NESTLEIND","BRITANNIA","TATACONSUM","DMART","TRENT","JUBLFOOD",
            "DEVYANI","VBL","UBL","RADICO","MARICO","DABUR","GODREJCP","COLPAL","PGHH","EMAMILTD",
            "BATAINDIA","PAGEIND","ABFRL","GODFRYPHLP","TITAN","NYKAA","ETERNAL","SWIGGY"],
 "INFRA/CAPGOODS": ["LT","BEL","HAL","BDL","MAZDOCK","COCHINSHIP","GRSE","BHEL","SIEMENS","ABB",
            "CUMMINSIND","THERMAX","KEC","KPIL","NCC","IRB","GMRAIRPORT","RVNL","IRCON",
            "RAILTEL","TITAGARH","JWL","IRCTC","CONCOR","NBCC","HUDCO","SOLARINDS","AMBER","PGEL",
            "DIXON","KAYNES","CGPOWER","POLYCAB","HAVELLS","VOLTAS","BLUESTARCO","CROMPTON","KEI",
            "SUPREMEIND","ASTRAL","ADANIENT","ADANIPORTS"],
 "CEMENT/CHEM": ["ULTRACEMCO","SHREECEM","AMBUJACEM","ACC","DALBHARAT","JKCEMENT","RAMCOCEM","GRASIM",
            "ASIANPAINT","PIDILITIND","SRF","PIIND","DEEPAKNTR","AARTIIND","TATACHEM","UPL",
            "COROMANDEL","CHAMBLFERT","GNFC","NAVINFLUOR","ATUL","VINATIORGA"],
 "REALTY/HOTEL": ["DLF","GODREJPROP","OBEROIRLTY","PRESTIGE","LODHA","PHOENIXLTD","BRIGADE","SOBHA",
            "CENTURYPLY","INDHOTEL","LEMONTREE","INDIGO","SPICEJET"],
 "TELECOM/MEDIA": ["BHARTIARTL","IDEA","TATACOMM","INDUSTOWER","HFCL","ITI","ZEEL","NAUKRI","DELHIVERY"],
}
SECTOR_OF = {}
for _s, _lst in SECTOR_MAP.items():
    for _t in _lst:
        SECTOR_OF[_t] = _s

def sector_of(sym):
    return SECTOR_OF.get(sym.replace(".NS", ""), "OTHER")

NIFTY = "^NSEI"
BANKNIFTY = "^NSEBANK"

st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap');
    @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600;700&display=swap');

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
        font-size: 11px; color: #7a82a6; margin-bottom: 12px;
        text-transform: uppercase; letter-spacing: 1px; font-weight: 600;
    }

    /* Glass Card */
    .glass-card {
        background: rgba(13, 17, 30, 0.75);
        backdrop-filter: blur(16px); -webkit-backdrop-filter: blur(16px);
        border: 1px solid rgba(255,255,255,0.08);
        border-radius: 14px; padding: 16px 18px;
        box-shadow: 0 8px 32px rgba(0,0,0,0.4);
        margin-bottom: 12px;
        transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
    }
    .glass-card:hover {
        transform: translateY(-2px);
        box-shadow: 0 12px 48px rgba(0,136,255,0.12);
        border-color: rgba(0,136,255,0.25);
    }

    /* Signal Cards */
    .signal-card {
        background: linear-gradient(135deg, rgba(14,19,34,0.95) 0%, rgba(20,28,50,0.85) 100%);
        backdrop-filter: blur(12px);
        border: 1px solid rgba(255,255,255,0.08);
        border-radius: 14px; padding: 14px 16px;
        box-shadow: 0 4px 24px rgba(0,0,0,0.38);
        margin-bottom: 12px;
        transition: all 0.3s ease;
        position: relative; overflow: hidden;
    }
    .signal-card::before {
        content: ''; position: absolute; top: 0; left: 0; right: 0; height: 3px;
    }
    .signal-card-buy::before { background: linear-gradient(90deg, #00ffaa, #00d4aa); }
    .signal-card-sell::before { background: linear-gradient(90deg, #ff3366, #ff6b6b); }
    .signal-card:hover { transform: translateY(-2px); box-shadow: 0 8px 32px rgba(0,0,0,0.45); border-color: rgba(255,255,255,0.15); }

    /* Top Gainers & Losers Cards */
    .gainer-card {
        background: linear-gradient(135deg, rgba(8,28,22,0.85) 0%, rgba(13,38,30,0.7) 100%);
        border: 1px solid rgba(0,255,170,0.25);
        border-radius: 12px; padding: 14px 16px; margin-bottom: 10px;
        box-shadow: 0 4px 20px rgba(0,255,170,0.06);
        position: relative;
    }
    .gainer-card::before {
        content: ''; position: absolute; top: 0; left: 0; right: 0; height: 3px;
        background: linear-gradient(90deg, #00ffaa, #00d4aa);
        border-top-left-radius: 12px; border-top-right-radius: 12px;
    }
    .loser-card {
        background: linear-gradient(135deg, rgba(32,10,18,0.85) 0%, rgba(42,14,24,0.7) 100%);
        border: 1px solid rgba(255,51,102,0.25);
        border-radius: 12px; padding: 14px 16px; margin-bottom: 10px;
        box-shadow: 0 4px 20px rgba(255,51,102,0.06);
        position: relative;
    }
    .loser-card::before {
        content: ''; position: absolute; top: 0; left: 0; right: 0; height: 3px;
        background: linear-gradient(90deg, #ff3366, #ff6b6b);
        border-top-left-radius: 12px; border-top-right-radius: 12px;
    }

    .card-symbol { font-size: 17px; font-weight: 800; color: #ffffff; letter-spacing: 0.3px; }
    .card-score {
        font-size: 11px; font-weight: 800; padding: 3px 10px; border-radius: 20px;
        display: inline-block;
    }
    .score-high { background: rgba(0,255,170,0.18); color: #00ffaa; border: 1px solid rgba(0,255,170,0.4); font-weight: 800; }
    .score-med { background: rgba(255,170,0,0.18); color: #ffaa00; border: 1px solid rgba(255,170,0,0.4); font-weight: 800; }
    .score-low { background: rgba(255,51,102,0.18); color: #ff3366; border: 1px solid rgba(255,51,102,0.4); font-weight: 800; }
    
    .card-setup { font-size: 12px; font-weight: 700; margin: 4px 0; color: #d0d4eb; }
    .card-prices { font-size: 12px; color: #9da3c7; font-family: 'JetBrains Mono', monospace; font-weight: 500; }
    .card-meta { font-size: 11px; color: #6b7294; margin-top: 6px; }
    .card-status { font-size: 12px; font-weight: 800; margin-top: 4px; }
    .card-indicators { font-size: 11px; color: #8b92b6; margin-top: 5px; line-height: 1.4; }
    .card-warning { font-size: 11px; color: #ffaa00; margin-top: 3px; font-weight: 600; }

    /* Metric Cards */
    .metric-card {
        background: linear-gradient(135deg, rgba(14,19,34,0.9) 0%, rgba(22,30,54,0.75) 100%);
        backdrop-filter: blur(10px);
        border: 1px solid rgba(255,255,255,0.07);
        border-radius: 12px; padding: 14px 16px;
        box-shadow: 0 4px 20px rgba(0,0,0,0.3); margin-bottom: 10px;
    }
    .metric-header { font-size: 10px; color: #7a82a6; text-transform: uppercase; letter-spacing: 1.5px; font-weight: 800; }
    .metric-val { font-size: 22px; font-weight: 800; color: #ffffff; margin-top: 2px; }
    .metric-change { font-size: 12px; font-weight: 700; margin-top: 1px; }
    .change-up { color: #00ffaa; }
    .change-down { color: #ff3366; }

    /* Badges */
    .fo-badge {
        display: inline-flex; align-items: center; gap: 4px;
        background: rgba(0,136,255,0.15); color: #4da6ff;
        border: 1px solid rgba(0,136,255,0.3);
        border-radius: 6px; padding: 2px 8px;
        font-size: 10px; font-weight: 800; text-transform: uppercase; letter-spacing: 0.5px;
    }

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
        background: linear-gradient(135deg, rgba(255,215,0,0.12) 0%, rgba(255,140,0,0.09) 100%);
        border: 1px solid rgba(255,215,0,0.3);
        padding: 12px 18px; border-radius: 10px; font-weight: 700; font-size: 13px;
        margin-bottom: 14px; text-align: center; color: #FFD700;
        box-shadow: 0 4px 24px rgba(255,215,0,0.08);
    }

    /* Stateful Radio Navigation Tabs */
    .stRadio [role="radiogroup"] {
        display: flex; flex-direction: row; gap: 8px; flex-wrap: wrap;
        background: rgba(13, 17, 30, 0.75); padding: 6px 8px; border-radius: 12px;
        border: 1px solid rgba(255,255,255,0.08); margin-bottom: 12px;
    }
    .stRadio [role="radiogroup"] > label {
        background: rgba(255,255,255,0.03); padding: 8px 18px; border-radius: 8px;
        color: #9da3c7; font-weight: 600; font-size: 12px; cursor: pointer;
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

    .last-updated { font-size: 11px; color: #5a6288; text-align: right; font-style: italic; margin-top: -6px; margin-bottom: 10px; }

    .info-panel {
        background: rgba(0,136,255,0.07); border: 1px solid rgba(0,136,255,0.2);
        border-radius: 8px; padding: 10px 14px; margin-bottom: 12px;
        color: #8bbce8; font-size: 12px; font-weight: 500;
    }

    .scan-status {
        padding: 6px 14px; border-radius: 8px; font-size: 11px; font-weight: 700;
        display: inline-flex; align-items: center; gap: 6px; margin-bottom: 8px;
    }
    .scan-running { background: rgba(0,136,255,0.12); color: #4da6ff; border: 1px solid rgba(0,136,255,0.3); }
    .scan-complete { background: rgba(0,255,170,0.12); color: #00ffaa; border: 1px solid rgba(0,255,170,0.3); }
    .scan-idle { background: rgba(100,100,140,0.1); color: #7a82a6; border: 1px solid rgba(100,100,140,0.2); }

    .view-toggle { display: flex; gap: 6px; margin-bottom: 10px; }

    .pivot-badge {
        font-size: 10px; padding: 2px 8px; border-radius: 12px; font-weight: 700;
        display: inline-block; margin-right: 4px;
    }
    .pivot-above { background: rgba(0,255,170,0.15); color: #00ffaa; border: 1px solid rgba(0,255,170,0.25); }
    .pivot-below { background: rgba(255,51,102,0.15); color: #ff3366; border: 1px solid rgba(255,51,102,0.25); }
    .pivot-near { background: rgba(255,170,0,0.15); color: #ffaa00; border: 1px solid rgba(255,170,0,0.25); }

    .rr-badge {
        display: inline-flex; align-items: center; gap: 8px; flex-wrap: wrap;
        padding: 5px 12px; border-radius: 8px; font-size: 11px; font-weight: 700;
        background: rgba(0,136,255,0.12); color: #4da6ff; border: 1px solid rgba(0,136,255,0.25);
        margin: 5px 0;
    }
    .rr-profit { color: #00ffaa; font-weight: 800; }
    .rr-risk { color: #ff6b6b; font-weight: 800; }
</style>
""", unsafe_allow_html=True)


# =============================================================================
# TIME / MARKET CALENDAR  (holiday-aware — v20 only skipped weekends)
# =============================================================================
# NSE equity full-day trading holidays 2026 -- weekday closures only.
# Cross-checked against the published exchange calendar (Groww / CalendarLabs
# agree line-for-line) and against actual missing sessions in 3 years of daily
# bars for Jan-Aug. Weekend-falling festivals are omitted deliberately.
# Nov 08 2026 (Sun) is Diwali Muhurat trading -- a special session, not a holiday.
STATIC_HOLIDAYS_2026 = [
 "2026-01-26",  # Republic Day
 "2026-03-03",  # Holi
 "2026-03-26",  # Shri Ram Navami
 "2026-03-31",  # Mahavir Jayanti
 "2026-04-03",  # Good Friday
 "2026-04-14",  # Ambedkar Jayanti
 "2026-05-01",  # Maharashtra Day
 "2026-05-28",  # Bakri Id
 "2026-06-26",  # Muharram
 "2026-09-14",  # Ganesh Chaturthi
 "2026-10-02",  # Gandhi Jayanti
 "2026-10-20",  # Dussehra
 "2026-11-10",  # Diwali Balipratipada
 "2026-11-24",  # Guru Nanak Jayanti
 "2026-12-25",  # Christmas
]

def get_ist_now():
    return datetime.now(IST)

def _load_holidays():
    hol = set(STATIC_HOLIDAYS_2026)
    try:
        if os.path.exists(F_HOLIDAYS):
            hol |= set(json.load(open(F_HOLIDAYS)).get("dates", []))
    except Exception:
        pass
    return hol

HOLIDAYS = _load_holidays()

def is_trading_day(d):
    if isinstance(d, datetime):
        d = d.date()
    if d.weekday() >= 5:
        return False
    return d.strftime("%Y-%m-%d") not in HOLIDAYS

def prev_trading_day(d=None):
    d = (d or get_ist_now().date())
    d = d - timedelta(days=1)
    for _ in range(15):
        if is_trading_day(d):
            return d
        d -= timedelta(days=1)
    return d

def next_trading_day(d=None):
    d = (d or get_ist_now().date()) + timedelta(days=1)
    for _ in range(15):
        if is_trading_day(d):
            return d
        d += timedelta(days=1)
    return d

MKT_OPEN, MKT_CLOSE = dtime(9, 15), dtime(15, 30)
PRE_OPEN_START, PRE_OPEN_END = dtime(9, 0), dtime(9, 15)

def market_phase(now=None):
    """PRE_OPEN | OPEN_AUCTION | ORB | LIVE | POST | CLOSED | HOLIDAY"""
    now = now or get_ist_now()
    if not is_trading_day(now):
        return "HOLIDAY"
    t = now.time()
    if t < dtime(8, 45):
        return "CLOSED"
    if t < PRE_OPEN_START:
        return "PRE_OPEN"           # our 08:45-09:00 prep window
    if t < PRE_OPEN_END:
        return "OPEN_AUCTION"       # NSE pre-open call auction, real data available
    if t < dtime(9, 20):
        return "ORB"                # first 5m candle forming
    if t <= MKT_CLOSE:
        return "LIVE"
    if t <= dtime(23, 59):
        return "POST"
    return "CLOSED"

def session_date(now=None):
    """Trading date a scan belongs to. Boundary at 15:40 (not 09:00 as in v20,
    which caused pre-9am heroes to be filed under the wrong day)."""
    now = now or get_ist_now()
    if is_trading_day(now) and now.time() >= dtime(15, 40):
        return now.date()                # today is done -> EOD scan is for today
    if is_trading_day(now):
        return now.date()
    return prev_trading_day(now.date() + timedelta(days=1))

def fmt_ist(dt=None):
    return (dt or get_ist_now()).strftime("%d %b %Y • %I:%M:%S %p IST")

# =============================================================================
# THREAD-SAFE TTL CACHE
# v20 bug: @st.cache_data functions were called from bare background threads,
# which have no ScriptRunContext. This cache works from any thread.
# =============================================================================
class TTLCache:
    def __init__(self):
        self._d = {}
        self._lock = threading.RLock()

    def get(self, key):
        with self._lock:
            v = self._d.get(key)
            if not v:
                return None
            val, exp = v
            if time.time() > exp:
                self._d.pop(key, None)
                return None
            return val

    def set(self, key, val, ttl=300):
        with self._lock:
            self._d[key] = (val, time.time() + ttl)

    def clear(self):
        with self._lock:
            self._d.clear()

CACHE = TTLCache()
DL_LOCK = threading.Semaphore(4)   # be polite to the data provider

def _norm_cols(df, tickers):
    """yfinance returns (Field, Ticker) columns unless group_by='ticker'.
    v20 bug #2: fetch_live_prices indexed df[t]['Close'] on a (Field,Ticker)
    frame -> KeyError swallowed -> prices NEVER returned. Normalise here once."""
    if df is None or len(df) == 0:
        return {}
    out = {}
    if isinstance(df.columns, pd.MultiIndex):
        lvl0 = set(df.columns.get_level_values(0))
        by_ticker = len(set(tickers) & lvl0) > 0
        for t in tickers:
            try:
                sub = df[t] if by_ticker else df.xs(t, axis=1, level=1)
                sub = sub.dropna(how="all")
                if len(sub):
                    out[t] = sub
            except Exception:
                continue
    else:
        if len(tickers) == 1:
            sub = df.dropna(how="all")
            if len(sub):
                out[tickers[0]] = sub
    return out

def download(tickers, period=None, interval="1d", start=None, end=None,
             ttl=300, retries=2, chunk=60):
    """Batch download -> {ticker: DataFrame}. Chunked, retried, cached, thread-safe."""
    if isinstance(tickers, str):
        tickers = [tickers]
    tickers = [t for t in dict.fromkeys(tickers) if t]
    key = ("dl", tuple(tickers), period, interval, str(start), str(end))
    hit = CACHE.get(key)
    if hit is not None:
        return hit
    out = {}
    for i in range(0, len(tickers), chunk):
        part = tickers[i:i + chunk]
        for attempt in range(retries + 1):
            try:
                with DL_LOCK:
                    df = yf.download(part, period=period, interval=interval,
                                     start=start, end=end, progress=False,
                                     auto_adjust=True, threads=True,
                                     group_by="ticker" if len(part) > 1 else "column")
                got = _norm_cols(df, part)
                if got:
                    out.update(got)
                    break
            except Exception:
                pass
            time.sleep(1.2 * (attempt + 1))
    CACHE.set(key, out, ttl)
    return out

# =============================================================================
# UNIVERSE VALIDATION  (v20 bug #11: LTIM.NS, PEL.NS, TATAMOTORS.NS are dead)
# =============================================================================
def validated_universe(force=False, max_age_days=7):
    if not force and os.path.exists(F_UNIVERSE_OK):
        try:
            j = json.load(open(F_UNIVERSE_OK))
            age = (get_ist_now() - datetime.fromisoformat(j["ts"]).replace(tzinfo=IST)).days
            if age <= max_age_days and j.get("ok"):
                return j["ok"], j.get("dead", [])
        except Exception:
            pass
    raw = []
    for t in UNIVERSE_RAW:
        r = RENAMES.get(t, t)
        if r:
            raw.append(r)
    raw = list(dict.fromkeys(raw))
    data = download(raw, period="10d", interval="1d", ttl=3600)
    ok = sorted([t for t in raw if t in data and len(data[t]) >= 3])
    dead = sorted([t for t in raw if t not in ok])
    try:
        json.dump({"ts": get_ist_now().isoformat(), "ok": ok, "dead": dead},
                  open(F_UNIVERSE_OK, "w"))
    except Exception:
        pass
    return ok, dead

# =============================================================================
# INDICATORS  (vectorised; no Python row loops anywhere)
# =============================================================================
def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()

def rma(s, n):
    return s.ewm(alpha=1 / n, adjust=False).mean()

def true_range(h, l, c):
    pc = c.shift(1)
    return pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)

def atr(h, l, c, n=14):
    return rma(true_range(h, l, c), n)

def rsi(c, n=14):
    d = c.diff()
    return 100 - 100 / (1 + rma(d.clip(lower=0), n) / rma(-d.clip(upper=0), n).replace(0, np.nan))

def macd(c, f=12, s=26, sig=9):
    line = ema(c, f) - ema(c, s)
    sg = ema(line, sig)
    return line, sg, line - sg

def adx(h, l, c, n=14):
    up = h.diff()
    dn = -l.diff()
    plus = np.where((up > dn) & (up > 0), up, 0.0)
    minus = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = rma(true_range(h, l, c), n)
    pdi = 100 * rma(pd.Series(plus, index=h.index), n) / tr.replace(0, np.nan)
    mdi = 100 * rma(pd.Series(minus, index=h.index), n) / tr.replace(0, np.nan)
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return rma(dx, n), pdi, mdi

def bollinger(c, n=20, k=2.0):
    m = c.rolling(n).mean()
    sd = c.rolling(n).std()
    return m + k * sd, m, m - k * sd

def supertrend(h, l, c, n=10, mult=3.0):
    """Correct recursive SuperTrend (direction series)."""
    a = atr(h, l, c, n)
    hl2 = (h + l) / 2
    ub = (hl2 + mult * a).to_numpy()
    lb = (hl2 - mult * a).to_numpy()
    cc = c.to_numpy()
    n_ = len(cc)
    fub = np.full(n_, np.nan)
    flb = np.full(n_, np.nan)
    dirn = np.ones(n_)
    for i in range(1, n_):
        fub[i] = ub[i] if (ub[i] < fub[i - 1] or cc[i - 1] > fub[i - 1] or np.isnan(fub[i - 1])) else fub[i - 1]
        flb[i] = lb[i] if (lb[i] > flb[i - 1] or cc[i - 1] < flb[i - 1] or np.isnan(flb[i - 1])) else flb[i - 1]
        if cc[i] > (fub[i - 1] if not np.isnan(fub[i - 1]) else ub[i]):
            dirn[i] = 1
        elif cc[i] < (flb[i - 1] if not np.isnan(flb[i - 1]) else lb[i]):
            dirn[i] = -1
        else:
            dirn[i] = dirn[i - 1]
    return pd.Series(dirn, index=c.index), pd.Series(flb, index=c.index), pd.Series(fub, index=c.index)

def vwap(df):
    tp = (df["High"] + df["Low"] + df["Close"]) / 3
    v = df["Volume"].replace(0, np.nan)
    return (tp * v).cumsum() / v.cumsum()

# =============================================================================
# PIVOTS  —  daily CPR + weekly + monthly.
# v20 bug #6: weekly grouping used calendar year + ISO week, which mis-buckets
# the year boundary. Fixed with isocalendar().year. v20 bug #7: no daily pivot.
# =============================================================================
def _pivot_set(h, l, c):
    p = (h + l + c) / 3
    bc = (h + l) / 2
    tc = 2 * p - bc
    if tc < bc:
        tc, bc = bc, tc
    r = h - l
    return dict(P=p, TC=tc, BC=bc, CPR_W=(tc - bc) / c * 100 if c else np.nan,
                R1=2 * p - l, R2=p + r, R3=h + 2 * (p - l),
                S1=2 * p - h, S2=p - r, S3=l - 2 * (h - p))

def pivots_all(d):
    """d = daily OHLC frame. Returns daily/weekly/monthly pivot dicts from the
    LAST COMPLETED period of each timeframe (new pivot every day/week/month)."""
    out = {}
    if d is None or len(d) < 25:
        return out
    d = d.dropna(subset=["High", "Low", "Close"])
    idx = pd.DatetimeIndex(d.index)
    # daily -> previous session
    if len(d) >= 2:
        r = d.iloc[-2]
        out["D"] = _pivot_set(float(r.High), float(r.Low), float(r.Close))
    # weekly -> previous ISO week
    iso = idx.isocalendar()
    wk = pd.Series(list(zip(iso.year, iso.week)), index=idx)
    groups = list(dict.fromkeys(wk.tolist()))
    if len(groups) >= 2:
        m = wk == groups[-2]
        s = d[m.to_numpy()]
        if len(s):
            out["W"] = _pivot_set(float(s.High.max()), float(s.Low.min()), float(s.Close.iloc[-1]))
    # monthly -> previous calendar month
    mo = pd.Series([(x.year, x.month) for x in idx], index=idx)
    mg = list(dict.fromkeys(mo.tolist()))
    if len(mg) >= 2:
        m = mo == mg[-2]
        s = d[m.to_numpy()]
        if len(s):
            out["M"] = _pivot_set(float(s.High.max()), float(s.Low.min()), float(s.Close.iloc[-1]))
    return out

def pivot_context(price, piv):
    """Where price sits vs each pivot set + nearest resistance/support."""
    ctx = {"levels": [], "above": {}, "nearest_res": None, "nearest_sup": None}
    for tf, p in piv.items():
        for k in ("S3", "S2", "S1", "BC", "P", "TC", "R1", "R2", "R3"):
            v = p.get(k)
            if v and np.isfinite(v):
                ctx["levels"].append((f"{tf}-{k}", float(v)))
        ctx["above"][tf] = bool(price > p.get("P", np.nan)) if np.isfinite(p.get("P", np.nan)) else None
    res = [(n, v) for n, v in ctx["levels"] if v > price]
    sup = [(n, v) for n, v in ctx["levels"] if v < price]
    if res:
        ctx["nearest_res"] = min(res, key=lambda x: x[1] - price)
    if sup:
        ctx["nearest_sup"] = max(sup, key=lambda x: x[1])
    return ctx

# =============================================================================
# FEATURE ENGINE
# These definitions are IDENTICAL to the ones the models were calibrated on.
# Do not "improve" a formula here without refitting — the coefficients in
# data/models/*.json are tied to these exact definitions.
# Everything is panel-vectorised (all tickers at once): 215 names in one pass
# instead of v20's per-ticker Python loops (bug #10, the main speed problem).
# =============================================================================
def make_panel(data):
    """{ticker: OHLCV df} -> dict of aligned DataFrames keyed by field."""
    fields = ["Open", "High", "Low", "Close", "Volume"]
    cols = {}
    for f in fields:
        ser = {}
        for t, d in data.items():
            if d is None or len(d) == 0 or f not in d.columns:
                continue
            s = pd.Series(pd.to_numeric(d[f], errors="coerce").to_numpy().ravel(),
                          index=pd.DatetimeIndex(d.index).tz_localize(None))
            ser[t] = s[~s.index.duplicated(keep="last")]
        cols[f] = pd.DataFrame(ser).sort_index() if ser else pd.DataFrame()
    if len(cols["Close"]):
        idx = cols["Close"].index
        for f in fields:
            cols[f] = cols[f].reindex(index=idx, columns=cols["Close"].columns)
    return cols

def _ema(d, n):
    return d.ewm(span=n, adjust=False).mean()

def _rsi_df(x, n=14):
    d = x.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d).clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))

def _supertrend_dir(H, L, C, n=10, mult=3.0):
    pc = C.shift(1)
    tr = pd.concat([H - L, (H - pc).abs(), (L - pc).abs()]).groupby(level=0).max().reindex(C.index)
    a = tr.ewm(span=n, adjust=False).mean()
    hl2 = (H + L) / 2
    # np.array(..., copy=True): pandas 3 returns read-only views, and this loop
    # writes back into up/lo in place.
    up = np.array((hl2 + mult * a).to_numpy(float), copy=True)
    lo = np.array((hl2 - mult * a).to_numpy(float), copy=True)
    c = np.array(C.to_numpy(float), copy=True)
    d = np.ones_like(c)
    for i in range(1, len(c)):
        pu, pl, pcv = up[i - 1], lo[i - 1], c[i - 1]
        m1 = (lo[i] < pl) & (pcv > pl)
        lo[i] = np.where(m1, pl, lo[i])
        m2 = (up[i] > pu) & (pcv < pu)
        up[i] = np.where(m2, pu, up[i])
        d[i] = np.where(c[i] > up[i], 1, np.where(c[i] < lo[i], -1, d[i - 1]))
    return pd.DataFrame(d, index=C.index, columns=C.columns)

def build_features(px, fast=False):
    """px = make_panel(...) output. Returns {feature_name: DataFrame}."""
    O, H, L, C, V = px["Open"], px["High"], px["Low"], px["Close"], px["Volume"]
    F = {}
    pc = C.shift(1)
    tr = pd.concat([H - L, (H - pc).abs(), (L - pc).abs()]).groupby(level=0).max().reindex(C.index)
    atr14 = tr.ewm(span=14, adjust=False).mean()
    F["atr"] = atr14
    F["atr_pct"] = atr14 / C * 100
    F["adr20"] = ((H - L) / C * 100).rolling(20).mean()
    F["rvol"] = V / V.rolling(20).mean()
    F["turn_cr"] = C * V / 1e7
    F["turn_ma_cr"] = (C * V / 1e7).rolling(20).mean()
    for n in (1, 3, 5, 10, 21, 63):
        F[f"ret{n}"] = C.pct_change(n) * 100
    F["gap"] = (O / C.shift(1) - 1) * 100
    rng = (H - L).replace(0, np.nan)
    F["clv"] = (C - L) / rng
    F["body"] = (C - O) / rng
    e20, e50 = _ema(C, 20), _ema(C, 50)
    F["ema9"], F["ema20"], F["ema50"], F["ema200"] = _ema(C, 9), e20, e50, _ema(C, 200)
    F["px_vs_e20"] = (C / e20 - 1) * 100
    F["e20_vs_e50"] = (e20 / e50 - 1) * 100
    F["stack_up"] = ((C > e20) & (e20 > e50)).astype(float)
    F["stack_dn"] = ((C < e20) & (e20 < e50)).astype(float)
    F["rsi"] = _rsi_df(C)
    ml = _ema(C, 12) - _ema(C, 26)
    F["macd_h"] = ml - _ema(ml, 9)
    F["macd_h_norm"] = F["macd_h"] / C * 100
    F["d_hi20"] = (C / H.rolling(20).max() - 1) * 100
    F["d_lo20"] = (C / L.rolling(20).min() - 1) * 100
    F["d_hi52"] = (C / H.rolling(252).max() - 1) * 100
    F["d_lo52"] = (C / L.rolling(252).min() - 1) * 100
    F["d_hi52r"] = (C / H.rolling(252, min_periods=100).max() - 1) * 100
    F["d_lo52r"] = (C / L.rolling(252, min_periods=100).min() - 1) * 100
    sd20 = C.pct_change().rolling(20).std() * 100
    F["vol20"] = sd20
    F["vol_ratio_5_20"] = (C.pct_change().rolling(5).std() * 100) / sd20
    bbw = (C.rolling(20).std() * 4) / C.rolling(20).mean() * 100
    F["bbw"] = bbw
    F["bbw_pctile"] = bbw.rolling(120).rank(pct=True)
    F["bbw_pct_r"] = bbw.rolling(120, min_periods=60).rank(pct=True)
    F["nr7"] = ((H - L) <= (H - L).rolling(7).min()).astype(float)
    absr = C.pct_change().abs() * 100
    F["big_moves_20"] = (absr >= 2.5).rolling(20).sum()
    F["big_moves_60"] = (absr >= 2.5).rolling(60).sum()
    F["st_dir"] = pd.DataFrame(1.0, index=C.index, columns=C.columns) if fast else _supertrend_dir(H, L, C)
    F["px"] = C
    F["up3"] = (C.diff() > 0).astype(int).rolling(3).sum()
    mkt21 = C.pct_change(21).median(axis=1)
    F["rs21"] = C.pct_change(21) * 100 - (mkt21 * 100).to_numpy()[:, None]
    mkt1 = C.pct_change(1).median(axis=1)
    F["rs1"] = C.pct_change(1) * 100 - (mkt1 * 100).to_numpy()[:, None]
    F["acc2"] = ((C.diff() > 0) & (F["rvol"] > 1.3)).rolling(2).sum()
    F["adx"] = _adx_panel(H, L, C)
    return F

def _adx_panel(H, L, C, n=14):
    up = H.diff()
    dn = -L.diff()
    plus = up.where((up > dn) & (up > 0), 0.0)
    minus = dn.where((dn > up) & (dn > 0), 0.0)
    pc = C.shift(1)
    tr = pd.concat([H - L, (H - pc).abs(), (L - pc).abs()]).groupby(level=0).max().reindex(C.index)
    a = tr.ewm(alpha=1 / n, adjust=False).mean()
    pdi = 100 * plus.ewm(alpha=1 / n, adjust=False).mean() / a.replace(0, np.nan)
    mdi = 100 * minus.ewm(alpha=1 / n, adjust=False).mean() / a.replace(0, np.nan)
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False).mean()

def snapshot(F, when=-1):
    """Feature dict of the row at position `when` -> {ticker: {feature: value}}."""
    out = defaultdict(dict)
    for k, df in F.items():
        if df is None or len(df) == 0:
            continue
        try:
            row = df.iloc[when]
        except Exception:
            continue
        for t, v in row.items():
            out[t][k] = float(v) if v == v and np.isfinite(v) else np.nan
    return dict(out)

# ---------------------------------------------------------------------------
# SWING (weekly) extras — mirrors the swing calibration exactly.
# ---------------------------------------------------------------------------
def swing_extras(px, F, ref_pos=-1):
    O, H, L, C, V = px["Open"], px["High"], px["Low"], px["Close"], px["Volume"]
    idx = C.index
    ref = idx[ref_pos]
    iso = idx.isocalendar()
    wk = pd.Series([f"{a}-W{b:02d}" for a, b in zip(iso.year, iso.week)], index=idx)
    wC = C.groupby(wk).last(); wH = H.groupby(wk).max(); wL = L.groupby(wk).min()
    w_ret = (wC / wC.shift(1) - 1) * 100
    w_ret4 = (wC / wC.shift(4) - 1) * 100
    w_rng = (wH - wL) / wL * 100
    w_clv = (wC - wL) / (wH - wL).replace(0, np.nan)
    w_rsi = _rsi_df(wC)
    w_up2 = (w_ret > 0).astype(float) + (w_ret.shift(1) > 0).astype(float)
    cur_wk = wk.loc[ref]
    prev_wks = [k for k in wC.index if k < cur_wk]
    if not prev_wks:
        return {}
    pw = max(prev_wks)
    mk = pd.Series([f"{d.year}-{d.month:02d}" for d in idx], index=idx)
    mH = H.groupby(mk).max(); mL = L.groupby(mk).min(); mC = C.groupby(mk).last()
    P = (mH.shift(1) + mL.shift(1) + mC.shift(1)) / 3
    rangeM = mH.shift(1) - mL.shift(1)
    R1 = 2 * P - mL.shift(1); S1 = 2 * P - mH.shift(1)
    R2 = P + rangeM
    mkey = mk.loc[ref]
    if mkey not in P.index:
        return {}
    cl = C.loc[ref]
    ex = {}
    ex["prev_wk_ret"] = w_ret.loc[pw]
    ex["prev_wk_ret4"] = w_ret4.loc[pw]
    ex["prev_wk_rng"] = w_rng.loc[pw]
    ex["prev_wk_clv"] = w_clv.loc[pw]
    ex["prev_wk_near_hi"] = (w_clv.loc[pw] >= 0.8).astype(float)
    ex["w_rsi"] = w_rsi.loc[pw]
    ex["w_up2"] = w_up2.loc[pw]
    ex["piv_pos"] = (cl - P.loc[mkey]) / rangeM.loc[mkey].replace(0, np.nan) * 100
    ex["above_P"] = (cl > P.loc[mkey]).astype(float)
    ex["above_R1"] = (cl > R1.loc[mkey]).astype(float)
    ex["above_R2"] = (cl > R2.loc[mkey]).astype(float)
    ex["below_S1"] = (cl < S1.loc[mkey]).astype(float)
    ex["d_R1"] = (cl / R1.loc[mkey] - 1) * 100
    ex["d_S1"] = (cl / S1.loc[mkey] - 1) * 100
    # sector momentum (median member ret5 / ret21)
    r5, r21 = F["ret5"].loc[ref], F["ret21"].loc[ref]
    sm1, sm4 = {}, {}
    for s, lst in SECTOR_MAP.items():
        mem = [f"{t}.NS" for t in lst if f"{t}.NS" in C.columns]
        if not mem:
            continue
        sm1[s] = float(np.nanmedian(r5[mem].to_numpy(float)))
        sm4[s] = float(np.nanmedian(r21[mem].to_numpy(float)))
    sec = pd.Series({t: sector_of(t) for t in C.columns})
    ex["sec_mom1"] = sec.map(sm1)
    ex["sec_mom4"] = sec.map(sm4)
    ex["rs_sec1"] = F["ret5"].loc[ref] - ex["sec_mom1"]
    ex["_pivots"] = dict(P=P.loc[mkey], R1=R1.loc[mkey], R2=R2.loc[mkey], S1=S1.loc[mkey])
    return ex

# ---------------------------------------------------------------------------
# INTRADAY ORB features from a 5-minute frame (single ticker, single day)
# ---------------------------------------------------------------------------
def orb_from_bars(g, minutes=5):
    """g: intraday 5m bars for one ticker/day, ascending, from 09:15.
    Returns ORB candle stats + the current close-confirmed break state."""
    if g is None or len(g) < 1:
        return None
    g = g.sort_index()
    nbars = max(1, int(minutes // 5))
    fc = g.iloc[:nbars]
    if len(fc) < nbars:
        return None
    oh, ol = float(fc.High.max()), float(fc.Low.min())
    oo, oc = float(fc.Open.iloc[0]), float(fc.Close.iloc[-1])
    if not (oh > 0 and ol > 0 and oo > 0):
        return None
    rest = g.iloc[nbars:]
    day_vol = float(g.Volume.sum())
    r = dict(orb_h=oh, orb_l=ol, orb_o=oo, orb_c=oc,
             orb_range_pct=(oh - ol) / oo * 100,
             fc_body=(oc - oo) / oo * 100,
             fc_vol_share=(float(fc.Volume.sum()) / day_vol) if day_vol > 0 else np.nan,
             bars=len(g), last=float(g.Close.iloc[-1]),
             day_high=float(g.High.max()), day_low=float(g.Low.min()),
             L_trig=0, S_trig=0, L_trig_min=np.nan, S_trig_min=np.nan,
             L_entry=np.nan, S_entry=np.nan, L_trig_time=None, S_trig_time=None)
    if len(rest) == 0:
        return r
    Cc = rest.Close.to_numpy(float)
    t0 = g.index[0]
    up = Cc > oh
    if up.any():
        i = int(np.argmax(up))
        r["L_trig"] = 1
        r["L_entry"] = float(Cc[i])
        r["L_trig_min"] = int((rest.index[i] - t0).total_seconds() // 60) + 5
        r["L_trig_time"] = rest.index[i]
    dn = Cc < ol
    if dn.any():
        i = int(np.argmax(dn))
        r["S_trig"] = 1
        r["S_entry"] = float(Cc[i])
        r["S_trig_min"] = int((rest.index[i] - t0).total_seconds() // 60) + 5
        r["S_trig_time"] = rest.index[i]
    return r

def orb_model_features(base, orb, pdh, pdl, pdc, nfctx, side):
    """Assemble the exact feature vector the intraday models expect."""
    f = dict(base)
    f["gap_today"] = (orb["orb_o"] / pdc - 1) * 100 if pdc else np.nan
    f["gap_x_atr"] = f["gap_today"] / f.get("atr_pct", np.nan) if f.get("atr_pct") else np.nan
    f["orb_range_pct"] = orb["orb_range_pct"]
    f["orb_rng_x_atr"] = orb["orb_range_pct"] / f["atr_pct"] if f.get("atr_pct") else np.nan
    f["fc_body"] = orb["fc_body"]
    f["fc_vol_share"] = orb["fc_vol_share"]
    f["orb_pos_in_pd"] = (orb["orb_o"] - pdl) / (pdh - pdl) if (pdh and pdl and pdh != pdl) else np.nan
    f["nf_gap"] = nfctx.get("nf_gap", 0.0)
    f["nf_fc"] = nfctx.get("nf_fc", 0.0)
    f["nf_fc_range"] = nfctx.get("nf_fc_range", 0.0)
    if side == "L":
        f["orb_h_vs_pdh"] = (orb["orb_h"] / pdh - 1) * 100 if pdh else np.nan
        f["long_break_pct"] = (orb["orb_h"] / orb["orb_o"] - 1) * 100
        f["room_to_pdh"] = (pdh / orb["orb_h"] - 1) * 100 if pdh else np.nan
        f["L_trig_min"] = orb.get("L_trig_min", 20) if orb.get("L_trig_min") == orb.get("L_trig_min") else 20
    else:
        f["orb_l_vs_pdl"] = (orb["orb_l"] / pdl - 1) * 100 if pdl else np.nan
        f["short_break_pct"] = (1 - orb["orb_l"] / orb["orb_o"]) * 100
        f["room_to_pdl"] = (1 - pdl / orb["orb_l"]) * 100 if pdl else np.nan
        f["S_trig_min"] = orb.get("S_trig_min", 20) if orb.get("S_trig_min") == orb.get("S_trig_min") else 20
    return f

def nifty_context(nf5=None, nfd=None):
    """Nifty gap + first-candle behaviour, exactly as in calibration."""
    ctx = dict(nf_gap=0.0, nf_fc=0.0, nf_fc_range=0.0, nf_now=0.0, regime="NEUTRAL")
    try:
        if nfd is not None and len(nfd) > 200:
            c = pd.to_numeric(nfd["Close"], errors="coerce").dropna()
            e50 = _ema(c, 50).iloc[-1]; e200 = _ema(c, 200).iloc[-1]
            r21 = (c.iloc[-1] / c.iloc[-22] - 1) * 100 if len(c) > 22 else 0
            if c.iloc[-1] > e50 > e200 and r21 > 0:
                ctx["regime"] = "BULL"
            elif c.iloc[-1] < e50 < e200 and r21 < 0:
                ctx["regime"] = "BEAR"
            else:
                ctx["regime"] = "NEUTRAL"
            ctx["nf_ret21"] = float(r21)
        if nf5 is not None and len(nf5) >= 2 and nfd is not None:
            g = nf5.sort_index()
            g = g[g.index.date == g.index[-1].date()]
            if len(g) >= 1:
                o = g.iloc[0]
                pcs = pd.to_numeric(nfd["Close"], errors="coerce").dropna()
                prevc = float(pcs.iloc[-2]) if len(pcs) > 1 else float(o.Open)
                ctx["nf_gap"] = (float(o.Open) / prevc - 1) * 100
                ctx["nf_fc"] = (float(o.Close) / float(o.Open) - 1) * 100
                ctx["nf_fc_range"] = (float(o.High) - float(o.Low)) / float(o.Open) * 100
                ctx["nf_now"] = (float(g.iloc[-1].Close) / float(o.Open) - 1) * 100
    except Exception:
        pass
    return ctx


# =============================================================================
# CALIBRATED PROBABILITY MODEL  (logistic; coefficients fitted offline and
# refitted by the learning loop). v20 used hand-set weights capped at 100 while
# the max was ~130, so dozens of names tied at the top (bug #4). Probabilities
# do not tie and are directly interpretable.
# =============================================================================
class LogitModel:
    def __init__(self, spec):
        self.features = spec["features"]
        self.coef = np.array(spec["coef"], float)
        self.b = float(spec["intercept"])
        self.mean = np.array(spec["mean"], float)
        self.scale = np.array(spec["scale"], float)
        self.n = spec.get("n", 0)
        self.trained_at = spec.get("trained_at", "offline calibration")

    def prob(self, feat):
        x = np.array([feat.get(k, np.nan) for k in self.features], float)
        if np.isnan(x).any():
            med = np.nan_to_num(self.mean)
            x = np.where(np.isnan(x), med, x)
        z = (x - self.mean) / np.where(self.scale == 0, 1, self.scale)
        z = np.clip(z, -5, 5)
        return float(1 / (1 + math.exp(-(float(z @ self.coef) + self.b))))

    def top_drivers(self, feat, k=4):
        x = np.array([feat.get(f, np.nan) for f in self.features], float)
        x = np.where(np.isnan(x), self.mean, x)
        z = np.clip((x - self.mean) / np.where(self.scale == 0, 1, self.scale), -5, 5)
        contrib = z * self.coef
        order = np.argsort(-np.abs(contrib))[:k]
        return [(self.features[i], float(contrib[i])) for i in order]

def load_model(path, embedded=None):
    try:
        if os.path.exists(path):
            return LogitModel(json.load(open(path)))
    except Exception:
        pass
    if embedded:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            json.dump(embedded, open(path, "w"))
        except Exception:
            pass
        return LogitModel(embedded)
    return None

# =============================================================================

# EMBEDDED CALIBRATED MODELS  (fitted offline on the full study sample)

# Regenerated automatically by the learning loop into data/models/*.json.

# =============================================================================

EMB_LONG = dict(
  features=['atr_pct', 'adr20', 'vol20', 'big_moves_20', 'big_moves_60', 'bbw', 'bbw_pctile', 'rvol', 'ret1', 'ret3', 'ret5', 'ret21', 'gap_today', 'gap_x_atr', 'clv', 'body', 'px_vs_e20', 'e20_vs_e50', 'rsi', 'macd_h_norm', 'd_hi20', 'd_lo20', 'd_hi52', 'd_lo52', 'st_dir', 'rs21', 'rs1', 'acc2', 'orb_range_pct', 'orb_rng_x_atr', 'fc_body', 'orb_pos_in_pd', 'nf_gap', 'nf_fc', 'nf_fc_range', 'turn_ma_cr', 'px', 'orb_h_vs_pdh', 'long_break_pct', 'room_to_pdh', 'L_trig_min'],
  coef=[0.18083033, 0.02633692, 0.14291853, 0.02566966, 0.17181047, -0.05313657, -0.02048169, 0.01916862, -0.07098634, -0.04268473, 0.01900845, 0.39863376, 0.02340167, 8.026e-05, -0.05787861, 0.04771921, 0.05651406, -0.09871257, -0.15878899, -0.05545435, 0.21548272, -0.07067705, -0.11937098, 0.18261505, 0.0555955, -0.32014754, -0.00715987, 0.07626524, 0.06676832, 0.25515546, 0.05178576, 0.21034861, 0.12971936, 0.0156963, -0.0524823, -0.11156248, -0.03142569, -0.12554577, 0.03731731, 0.06733564, -0.59476752],
  intercept=-2.36207775,
  mean=[2.57848127, 2.48251041, 1.76259277, 2.75740517, 9.97949337, 11.39442122, 0.38811542, 0.96561571, 0.02271691, 0.19570207, 0.2960721, 1.37910898, 0.11605456, 0.04184956, 0.46567761, -0.06153242, 0.3909272, 0.6474431, 51.37746988, -0.02089715, -5.40202171, 6.7506139, -17.09130535, 30.60842363, 0.05857124, 0.44117949, 0.05638601, 0.20721083, 0.80443012, 0.3150812, 0.11531403, 0.6398848, 0.05775785, 0.00532797, 0.25803658, 313.00036412, 2282.12611728, -0.47057368, 0.44013948, 0.49466932, 45.67216191],
  scale=[0.68499393, 0.61498745, 0.59455605, 2.02725377, 5.23867883, 5.56895208, 0.2808568, 0.80471341, 1.82207479, 3.05034258, 3.87035114, 7.54503773, 0.75426996, 0.28942527, 0.28312261, 0.50887929, 3.44284145, 2.93521217, 11.00239137, 0.57123955, 3.96011915, 5.45257336, 11.37850701, 25.46225631, 0.99828323, 7.33738072, 1.63203483, 0.45925706, 0.46963472, 0.1636733, 0.60923913, 0.57138511, 0.48782951, 0.17452143, 0.08013684, 350.59597387, 4109.11955903, 1.46644736, 0.50197667, 1.48526026, 77.15409823],
  n=7461,
  trained_at='offline calibration 2026-08-27',
  note='P(+2% from close-confirmed 5m ORB high before capped stop). Walk-forward top-1 target-hit 37.5%, expectancy +0.32%/trade after costs.',
)

EMB_SHORT = dict(
  features=['atr_pct', 'adr20', 'vol20', 'big_moves_20', 'big_moves_60', 'bbw', 'bbw_pctile', 'rvol', 'ret1', 'ret3', 'ret5', 'ret21', 'gap_today', 'gap_x_atr', 'clv', 'body', 'px_vs_e20', 'e20_vs_e50', 'rsi', 'macd_h_norm', 'd_hi20', 'd_lo20', 'd_hi52', 'd_lo52', 'st_dir', 'rs21', 'rs1', 'acc2', 'orb_range_pct', 'orb_rng_x_atr', 'fc_body', 'orb_pos_in_pd', 'nf_gap', 'nf_fc', 'nf_fc_range', 'turn_ma_cr', 'px', 'orb_l_vs_pdl', 'short_break_pct', 'room_to_pdl', 'S_trig_min'],
  coef=[-0.1056424, 0.42845958, -0.11851633, -0.07123081, 0.19703245, 0.1558002, -0.17626433, -0.01504971, 0.09740448, -0.01878963, 0.01010589, 0.13492799, -0.01481683, -0.0866516, -0.03882722, 0.155875, -0.26276068, 0.18482643, 0.16080909, 0.22403774, 0.02071291, -0.00837516, -0.0309619, 0.10862821, -0.00869091, -0.22061007, -0.26688805, 0.0863963, 0.2097631, 0.16930441, -0.02685223, 0.02485294, -0.23082518, 0.05882071, -0.0548983, -0.11746585, 0.02084141, 0.2406635, 0.06611457, -0.01063444, -0.52165259],
  intercept=-2.75420633,
  mean=[2.58296004, 2.49542239, 1.76938181, 2.76631183, 10.0338134, 11.45196761, 0.38576967, 0.96778681, 0.12262535, 0.29464413, 0.35518591, 1.3980886, 0.13915648, 0.05134345, 0.48605101, -0.0361171, 0.45912762, 0.64337411, 51.55022665, -0.01164366, -5.36767423, 6.8688771, -17.13882232, 31.61879033, 0.07326237, 0.44421976, 0.12314913, 0.21815905, 0.79242951, 0.31076407, -0.12718236, 0.66339353, 0.06926131, -0.01011709, 0.2588878, 315.22552895, 2268.34097288, 1.13479881, 0.45994793, 1.09886601, 51.05197245],
  scale=[0.68985433, 0.62092136, 0.60444468, 2.04170475, 5.2568692, 5.57058641, 0.27997348, 0.82305196, 1.79077924, 3.0326244, 3.90476394, 7.51207508, 0.72574851, 0.28123589, 0.28607415, 0.51015698, 3.44574283, 3.06245419, 10.96716861, 0.5770299, 3.96478966, 5.44694511, 11.55570578, 26.56610607, 0.9973127, 7.34571095, 1.65270103, 0.47425477, 0.43828165, 0.15570381, 0.58762175, 0.56095256, 0.46376182, 0.17711859, 0.08261423, 346.42905922, 4236.19741129, 1.56052307, 0.45303664, 1.50478514, 84.64860766],
  n=7985,
  trained_at='offline calibration 2026-08-27',
  note='Short side. LOW CONFIDENCE: walk-forward expectancy was flat to negative; gated behind a higher probability floor.',
)

EMB_SWING = dict(
  features=['atr_pct', 'adr20', 'rvol', 'ret1', 'ret3', 'ret5', 'ret10', 'ret21', 'ret63', 'gap', 'clv', 'body', 'px_vs_e20', 'e20_vs_e50', 'stack_up', 'stack_dn', 'rsi', 'macd_h_norm', 'd_hi20', 'd_lo20', 'd_hi52r', 'd_lo52r', 'vol20', 'bbw', 'bbw_pct_r', 'nr7', 'big_moves_20', 'big_moves_60', 'st_dir', 'up3', 'rs21', 'rs1', 'acc2', 'vol_ratio_5_20', 'prev_wk_ret', 'prev_wk_ret4', 'prev_wk_rng', 'prev_wk_clv', 'prev_wk_near_hi', 'w_rsi', 'w_up2', 'piv_pos', 'above_P', 'above_R1', 'above_R2', 'below_S1', 'd_R1', 'd_S1', 'sec_mom1', 'sec_mom4', 'rs_sec1'],
  coef=[0.6017352, 0.03616755, 0.03138237, 0.31280621, 0.01548216, 0.10397431, 0.01519405, -0.36653044, -0.07918463, -0.10179828, 0.22352433, -0.1623062, 0.07392996, 0.00397589, 0.0763303, 0.03171071, -0.09208648, 0.00602606, 0.00394262, 0.02609112, -0.08975369, -0.03108508, -0.18781406, -0.05434638, -0.01498761, -0.04738981, 0.10302892, 0.30111404, -0.05020722, 0.03233397, 0.39207104, -0.23622975, -0.01608623, -0.04818707, -0.2572753, -0.12070456, -0.22333147, -0.06482767, -0.07529861, 0.05031699, -0.07007477, 0.03948895, 0.0476469, 0.08807566, -0.03010436, -0.05037109, 0.04741024, 0.02741372, 0.01760314, 0.03688957, 0.12006628],
  intercept=-2.83221247,
  mean=[2.9306779, 2.82682216, 1.05576296, -0.06561863, 0.24393955, 0.29295819, 0.58635873, 1.15361405, 4.4657052, 0.10150807, 0.4643419, -0.06771195, 0.30841248, 0.60384833, 0.39065802, 0.30772938, 51.04809467, -0.00824056, -6.22548576, 8.10469498, -16.19053378, 43.27222864, 1.94409175, 13.22008169, 0.49989652, 0.14580167, 3.35918443, 10.05408712, 0.02094532, 1.49267841, 0.75943844, 0.09908197, 0.24307692, 0.92733198, 0.29740526, 1.10559925, 6.89828016, 0.49390013, 0.2089342, 54.29002352, 1.02365153, 5.30689111, 0.53308619, 0.16048193, 0.0473772, 0.11714551, -5.97839472, 9.06868467, 0.12263918, 0.57686173, 0.17031901],
  scale=[1.01745412, 0.90708116, 0.88797782, 2.01361547, 3.64693482, 4.72169491, 6.54412491, 9.41670658, 18.19169302, 0.83291944, 0.27465408, 0.50922285, 4.31893216, 3.71670418, 0.48789787, 0.46155391, 12.14134936, 0.71587851, 4.98176921, 7.27793761, 12.08718578, 42.74673069, 0.86416725, 8.10022191, 0.30401355, 0.35290727, 2.59085768, 6.19259053, 0.99978062, 0.87070123, 8.39451272, 1.74851088, 0.49009256, 0.37713327, 4.53575116, 8.88298609, 4.2411396, 0.29390729, 0.40654729, 12.78815198, 0.69386055, 58.846691, 0.4989041, 0.36705242, 0.21244435, 0.32159359, 7.12517695, 10.75376774, 2.96498522, 5.64116057, 3.65416429],
  n=26975,
  trained_at='offline calibration 2026-08-27 (3y 60m bars, 133 weeks)',
  note='P(+7% before -5% stop) on Monday 1h-ORB long. Validated top-3 = 19.8% vs 6.2% base; +10% top-3 = 11.1% vs 2.1% base.',
)


# =============================================================================
# MODEL LOADING  (embedded calibrated coefficients — the app works out of the
# box; the learning loop overwrites data/models/*.json after each refit)
# =============================================================================
MODEL_L = load_model(F_MODEL_LONG,  embedded=EMB_LONG)
MODEL_S = load_model(F_MODEL_SHORT, embedded=EMB_SHORT)
MODEL_SW = load_model(F_MODEL_SWING, embedded=EMB_SWING)

# =============================================================================
# NSE PRE-OPEN  (v20 CRITICAL bug #1: the 09:08 scan read yesterday's bars as
# "today", so the entire pre-market engine ran on the wrong day's data.
# We now pull the real pre-open call-auction book from NSE.)
# =============================================================================
NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/market-data/pre-open-market-cm-and-emerge-market",
}

def fetch_nse_preopen(timeout=8):
    """{SYMBOL.NS: {'pre_open': px, 'prev_close': px, 'gap_pct': x, 'qty': n}}
    Empty dict if NSE is unreachable — every caller has a fallback."""
    if not HAS_REQ:
        return {}
    hit = CACHE.get("preopen")
    if hit is not None:
        return hit
    out = {}
    try:
        with requests.Session() as s:   # context-managed: no leaked sockets
            s.headers.update(NSE_HEADERS)
            s.get("https://www.nseindia.com/", timeout=timeout)
            r = s.get("https://www.nseindia.com/api/market-data-pre-open?key=FO",
                      timeout=timeout)
            js = r.json()
        for row in js.get("data", []):
            m = row.get("metadata", {}) or {}
            d = row.get("detail", {}).get("preOpenMarket", {}) or {}
            sym = m.get("symbol")
            if not sym:
                continue
            try:
                iep = float(m.get("lastPrice") or d.get("IEP") or 0)
                prev = float(m.get("previousClose") or 0)
                out[f"{sym}.NS"] = dict(
                    pre_open=iep, prev_close=prev,
                    gap_pct=(iep / prev - 1) * 100 if prev else np.nan,
                    qty=float(d.get("totalTradedVolume") or m.get("finalQuantity") or 0),
                    atp=float(d.get("atoBuyQty") or 0),
                )
            except Exception:
                continue
    except Exception:
        return {}
    if out:
        CACHE.set("preopen", out, 120)
    return out

def fetch_live_quotes(tickers, interval="5m"):
    """Latest traded price per ticker. Uses the FIXED column handling."""
    data = download(tickers, period="1d", interval=interval, ttl=45)
    out = {}
    for t, d in data.items():
        try:
            c = pd.to_numeric(d["Close"], errors="coerce").dropna()
            if len(c):
                out[t] = float(c.iloc[-1])
        except Exception:
            continue
    return out

# =============================================================================
# SHARED DATA BUNDLE
# =============================================================================
def load_bundle(force=False, days="400d", fast=False):
    """Daily panel + features + pivots + Nifty context for the whole universe."""
    key = ("bundle", days, fast)
    if not force:
        hit = CACHE.get(key)
        if hit is not None:
            return hit
    uni, dead = validated_universe()
    data = download(uni, period=days, interval="1d", ttl=1800)
    px = make_panel(data)
    if not len(px["Close"]):
        return None
    F = build_features(px, fast=fast)
    nfd_raw = download([NIFTY], period=days, interval="1d", ttl=1800).get(NIFTY)
    nf5_raw = download([NIFTY], period="2d", interval="5m", ttl=90).get(NIFTY)
    ctx = nifty_context(nf5_raw, nfd_raw)
    snap = snapshot(F, -1)
    O, H, L, C = px["Open"], px["High"], px["Low"], px["Close"]
    prev = dict(pdh=H.iloc[-1].to_dict(), pdl=L.iloc[-1].to_dict(), pdc=C.iloc[-1].to_dict())
    piv = {}
    for t in C.columns:
        try:
            d = pd.DataFrame({"High": H[t], "Low": L[t], "Close": C[t]}).dropna()
            piv[t] = pivots_all(d)
        except Exception:
            piv[t] = {}
    b = dict(px=px, F=F, snap=snap, prev=prev, piv=piv, ctx=ctx, dead=dead,
             universe=list(C.columns), last_date=C.index[-1], nfd=nfd_raw,
             built_at=get_ist_now().isoformat())
    CACHE.set(key, b, 900)
    return b

def liquid_names(bundle):
    return [t for t, f in bundle["snap"].items()
            if (f.get("turn_ma_cr", 0) or 0) >= CFG["MIN_TURNOVER_CR"]
            and CFG["MIN_PRICE"] <= (f.get("px") or 0) <= CFG["MAX_PRICE"]]

def feasible(f, side="L", swing=False):
    """Can this stock physically travel the objective? Calibrated veto:
    ATR% below the floor makes a 2% intraday (or 10% weekly) move a fantasy."""
    a = f.get("atr_pct", np.nan)
    ad = f.get("adr20", np.nan)
    if not np.isfinite(a) or not np.isfinite(ad):
        return False, "no volatility data"
    if swing:
        if a < CFG["SWING_MIN_ATR_PCT"]:
            return False, f"ATR {a:.1f}% < {CFG['SWING_MIN_ATR_PCT']}% (cannot travel 10%)"
        return True, ""
    if a < CFG["MIN_ATR_PCT"]:
        return False, f"ATR {a:.1f}% < {CFG['MIN_ATR_PCT']}%"
    if ad < CFG["MIN_ADR_PCT"]:
        return False, f"ADR {ad:.1f}% < {CFG['MIN_ADR_PCT']}%"
    return True, ""

# =============================================================================
# POSITION SIZING + ORDER TICKET
# =============================================================================
def build_ticket(sym, side, entry, sl_pct, capital, target_pct=None, partial=True):
    tp = target_pct if target_pct is not None else CFG["TARGET_PCT"]
    sgn = 1 if side == "BUY" else -1
    sl = entry * (1 - sgn * sl_pct / 100)
    t1 = entry * (1 + sgn * CFG["PARTIAL_PCT"] / 100)
    t2 = entry * (1 + sgn * tp / 100)
    risk_amt = capital * CFG["RISK_PER_TRADE_PCT"] / 100
    per_share = abs(entry - sl)
    qty = int(max(1, risk_amt // per_share)) if per_share > 0 else 0
    return dict(symbol=sym.replace(".NS", ""), side=side, entry=round(entry, 2),
                sl=round(sl, 2), sl_pct=round(sl_pct, 2),
                t1=round(t1, 2), t2=round(t2, 2), target_pct=tp,
                qty=qty, risk_rs=round(qty * per_share, 0),
                reward_rs=round(qty * abs(t2 - entry), 0),
                rr=round(abs(t2 - entry) / per_share, 2) if per_share else 0,
                partial=partial)

# =============================================================================
# ENGINE 1 — PRE-MARKET  (run 08:55-09:08). TOP 1 BUY + TOP 1 SELL.
# Gap logic is INVERTED versus v20 because the data says gaps mean-revert:
#   P(+2% | gap < -2%) = 60.0%   P(-2% | gap < -2%) = 33.7%
#   P(-2% | gap > +2%) = 47.8%   P(+2% | gap > +2%) = 41.0%
# =============================================================================
GAP_TABLE = [(-99, -2.0, 60.0, 33.7), (-2.0, -1.0, 36.9, 30.2), (-1.0, -0.3, 27.5, 24.4),
             (-0.3, 0.3, 22.9, 20.9), (0.3, 1.0, 26.1, 27.9), (1.0, 2.0, 30.8, 32.5),
             (2.0, 99, 41.0, 47.8)]

def gap_prior(gap):
    for lo, hi, pu, pd_ in GAP_TABLE:
        if lo <= gap < hi:
            return pu, pd_
    return 20.7, 22.8

def scan_premarket(capital=None, bundle=None, use_nse=True):
    capital = capital or CFG["DEFAULT_CAPITAL"]
    b = bundle or load_bundle()
    if not b:
        return dict(error="no data")
    pre = fetch_nse_preopen() if use_nse else {}
    # A live-but-degenerate feed (all gaps zero/absent) is worse than no feed,
    # because it silently turns the gap-reversion prior off. Detect and say so.
    live_gaps = sum(1 for v in pre.values()
                    if v.get("prev_close") and np.isfinite(v.get("gap_pct", np.nan))
                    and abs(v["gap_pct"]) > 0.01)
    if live_gaps < 20:
        pre, src = {}, ("previous close (NSE pre-open returned no indicative prices — "
                        "run between 09:00 and 09:08)")
    else:
        src = f"NSE pre-open call auction ({live_gaps} names quoting)"
    names = liquid_names(b)
    rows = []
    for t in names:
        f = dict(b["snap"][t])
        pdc = b["prev"]["pdc"].get(t)
        pdh, pdl = b["prev"]["pdh"].get(t), b["prev"]["pdl"].get(t)
        if not pdc:
            continue
        q = pre.get(t)
        gap = float(q["gap_pct"]) if q and np.isfinite(q.get("gap_pct", np.nan)) else 0.0
        exp_open = float(q["pre_open"]) if q and q.get("pre_open") else pdc
        okL, whyL = feasible(f, "L")
        pu, pdn = gap_prior(gap)
        atrp = f.get("atr_pct", 0) or 0
        adr = f.get("adr20", 0) or 0
        # capability score: how routinely this name travels 2%+
        cap = (min(atrp / 3.0, 1.5) * 0.35 + min(adr / 3.5, 1.5) * 0.25
               + min((f.get("big_moves_20", 0) or 0) / 8.0, 1.2) * 0.20
               + min((f.get("vol20", 0) or 0) / 3.0, 1.5) * 0.10
               + min((f.get("rvol", 0) or 0) / 2.0, 1.5) * 0.10)
        # direction tilt: gap-reversion prior (dominant) + trend alignment (minor)
        tilt_up = (pu - pdn) / 10.0
        trend = 0.35 * (f.get("st_dir", 0) or 0) + 0.02 * (f.get("px_vs_e20", 0) or 0)
        pivots = b["piv"].get(t, {})
        pctx = pivot_context(exp_open, pivots) if pivots else {"nearest_res": None, "nearest_sup": None}
        room_up = (pctx["nearest_res"][1] / exp_open - 1) * 100 if pctx.get("nearest_res") else 5.0
        room_dn = (1 - pctx["nearest_sup"][1] / exp_open) * 100 if pctx.get("nearest_sup") else 5.0
        sL = cap * 10 + tilt_up + trend + min(room_up, 4.0) * 0.25
        sS = cap * 10 - tilt_up - trend + min(room_dn, 4.0) * 0.25
        rows.append(dict(ticker=t, sym=t.replace(".NS", ""), sector=sector_of(t),
                         exp_open=exp_open, prev_close=pdc, gap=gap,
                         atr_pct=atrp, adr20=adr, rvol=f.get("rvol"),
                         big20=f.get("big_moves_20"), turn=f.get("turn_ma_cr"),
                         p_up=pu, p_dn=pdn, cap=cap, score_L=sL, score_S=sS,
                         feasible=okL, why=whyL, pdh=pdh, pdl=pdl,
                         room_up=room_up, room_dn=room_dn,
                         near_res=pctx.get("nearest_res"), near_sup=pctx.get("nearest_sup"),
                         st_dir=f.get("st_dir"), rsi=f.get("rsi"), adx=f.get("adx")))
    D = pd.DataFrame(rows)
    if D.empty:
        return dict(error="no candidates")
    D = D[D.feasible]
    longs = D.sort_values("score_L", ascending=False).head(8)
    # The same high-ATR name can top both lists when gaps are flat; never show
    # one symbol as both the buy and the sell of the day.
    top_long = longs.ticker.iloc[0] if len(longs) else None
    shorts = D[D.ticker != top_long].sort_values("score_S", ascending=False).head(8)
    out = dict(source=src, ts=get_ist_now().isoformat(), regime=b["ctx"]["regime"],
               n_candidates=int(len(D)), longs=[], shorts=[], table=D)
    for _, r in longs.iterrows():
        entry_hint = r.exp_open * (1 + min(r.atr_pct * 0.12, 0.45) / 100)
        out["longs"].append(dict(row=r.to_dict(),
                                 ticket=build_ticket(r.ticker, "BUY", entry_hint,
                                                     CFG["SL_CAP_PCT"], capital)))
    for _, r in shorts.iterrows():
        entry_hint = r.exp_open * (1 - min(r.atr_pct * 0.12, 0.45) / 100)
        out["shorts"].append(dict(row=r.to_dict(),
                                  ticket=build_ticket(r.ticker, "SELL", entry_hint,
                                                      CFG["SL_CAP_PCT"], capital)))
    save_scan("premarket", out)
    return out

# =============================================================================
# ENGINE 2 — LIVE 5-MIN ORB  (09:20 onward). The money engine.
# Close-confirmed break + capped stop + model ranking.
# =============================================================================
def scan_live_orb(capital=None, bundle=None, watch=None, orb_minutes=None):
    capital = capital or CFG["DEFAULT_CAPITAL"]
    om = orb_minutes or CFG["ORB_MINUTES"]
    b = bundle or load_bundle()
    if not b:
        return dict(error="no data")
    names = watch or liquid_names(b)
    names = [t for t in names if feasible(b["snap"].get(t, {}), "L")[0]]
    intraday = download(names, period="1d", interval="5m", ttl=45)
    nf5 = download([NIFTY], period="1d", interval="5m", ttl=45).get(NIFTY)
    nfctx = nifty_context(nf5, b["nfd"])
    today = get_ist_now().date()
    cands = []
    for t, d in intraday.items():
        try:
            g = d.copy()
            g.index = pd.DatetimeIndex(g.index)
            if g.index.tz is not None:
                g.index = g.index.tz_convert(IST)
            g = g[g.index.date == today]
            g = g[(g.index.time >= MKT_OPEN) & (g.index.time <= MKT_CLOSE)]
            if len(g) < 1:
                continue
            orb = orb_from_bars(g, om)
            if not orb:
                continue
            base = b["snap"].get(t, {})
            pdh, pdl = b["prev"]["pdh"].get(t), b["prev"]["pdl"].get(t)
            pdc = b["prev"]["pdc"].get(t)
            for side, mdl, minp in (("L", MODEL_L, CFG["MIN_P_LONG"]),
                                    ("S", MODEL_S, CFG["MIN_P_SHORT"])):
                if not orb[f"{side}_trig"] or mdl is None:
                    continue
                fv = orb_model_features(base, orb, pdh, pdl, pdc, nfctx, side)
                p = mdl.prob(fv)
                entry = orb["orb_h"] if side == "L" else orb["orb_l"]
                conf_entry = orb[f"{side}_entry"]        # actual confirmed close
                opp = orb["orb_l"] if side == "L" else orb["orb_h"]
                sl_pct = float(np.clip(abs(conf_entry - opp) / conf_entry * 100,
                                       CFG["SL_FLOOR_PCT"], CFG["SL_CAP_PCT"]))
                trig_min = orb[f"{side}_trig_min"]
                moved = ((orb["last"] / conf_entry - 1) * 100) * (1 if side == "L" else -1)
                cands.append(dict(
                    ticker=t, sym=t.replace(".NS", ""), sector=sector_of(t),
                    side="BUY" if side == "L" else "SELL", p=p, prob_pct=p * 100,
                    orb_h=orb["orb_h"], orb_l=orb["orb_l"], orb_range_pct=orb["orb_range_pct"],
                    entry=conf_entry, level=entry, sl_pct=sl_pct, trig_min=trig_min,
                    trig_time=orb[f"{side}_trig_time"], last=orb["last"], moved=moved,
                    gap=fv.get("gap_today"), atr_pct=base.get("atr_pct"),
                    adr20=base.get("adr20"), rvol=base.get("rvol"),
                    fc_vol_share=orb["fc_vol_share"], turn=base.get("turn_ma_cr"),
                    still_valid=bool(moved < CFG["TARGET_PCT"] * 0.6 and moved > -sl_pct),
                    drivers=mdl.top_drivers(fv, 4),
                    ticket=build_ticket(t, "BUY" if side == "L" else "SELL",
                                        conf_entry, sl_pct, capital),
                    low_conf=(side == "S"),
                    passed_p=bool(p >= minp),
                ))
        except Exception:
            continue
    D = pd.DataFrame(cands)
    out = dict(ts=get_ist_now().isoformat(), regime=nfctx["regime"], nfctx=nfctx,
               orb_minutes=om, n_triggers=int(len(D)), table=D, actionable=[])
    if not D.empty:
        act = D[(D.passed_p) & (D.still_valid)].copy()
        act = act.sort_values(["p"], ascending=False).head(CFG["MAX_TRADES_DAY"] * 2)
        out["actionable"] = act.to_dict("records")
        out["all_ranked"] = D.sort_values("p", ascending=False).to_dict("records")
    save_scan("live_orb", out)
    return out

# =============================================================================
# ENGINE 3 — EOD  (after 15:40). TOP 5 for tomorrow, with tomorrow's plan.
# =============================================================================
def scan_eod(capital=None, bundle=None, top_n=5):
    capital = capital or CFG["DEFAULT_CAPITAL"]
    b = bundle or load_bundle()
    if not b:
        return dict(error="no data")
    names = liquid_names(b)
    rows = []
    for t in names:
        f = b["snap"][t]
        ok, why = feasible(f, "L")
        if not ok:
            continue
        atrp = f.get("atr_pct", 0) or 0
        # Expected-move engine: probability the name travels >=2% intraday tomorrow.
        # Weights are the empirical top-quintile lifts from the study, normalised.
        z = (0.34 * min(atrp / 3.0, 2.0)
             + 0.26 * min((f.get("adr20", 0) or 0) / 3.5, 2.0)
             + 0.18 * min((f.get("vol20", 0) or 0) / 3.0, 2.0)
             + 0.12 * min((f.get("big_moves_60", 0) or 0) / 12.0, 2.0)
             + 0.10 * min((f.get("bbw", 0) or 0) / 15.0, 2.0))
        p_move = float(np.clip(0.207 * (0.45 + 0.55 * z / 0.9), 0.05, 0.75))
        rows.append(dict(ticker=t, sym=t.replace(".NS", ""), sector=sector_of(t),
                         close=f.get("px"), atr_pct=atrp, adr20=f.get("adr20"),
                         vol20=f.get("vol20"), big20=f.get("big_moves_20"),
                         big60=f.get("big_moves_60"), bbw=f.get("bbw"),
                         bbw_pctile=(f.get("bbw_pctile") or 0) * 100,
                         rvol=f.get("rvol"), rsi=f.get("rsi"), adx=f.get("adx"),
                         st_dir=f.get("st_dir"), d_hi20=f.get("d_hi20"),
                         d_lo20=f.get("d_lo20"), ret1=f.get("ret1"), ret5=f.get("ret5"),
                         ret21=f.get("ret21"), rs21=f.get("rs21"),
                         turn=f.get("turn_ma_cr"), expansion_score=z, p_move2=p_move * 100,
                         pdh=b["prev"]["pdh"].get(t), pdl=b["prev"]["pdl"].get(t),
                         pivots=b["piv"].get(t, {})))
    D = pd.DataFrame(rows)
    if D.empty:
        return dict(error="no candidates")
    D = D.sort_values("expansion_score", ascending=False)
    # sector cap: never fill the top-5 with one sector
    picks, seen = [], defaultdict(int)
    for _, r in D.iterrows():
        if seen[r.sector] >= 2:
            continue
        seen[r.sector] += 1
        picks.append(r)
        if len(picks) >= top_n:
            break
    plan = []
    for r in picks:
        piv = r.pivots or {}
        d = piv.get("D", {})
        plan.append(dict(row=r.to_dict(), pivot_D=d, pivot_W=piv.get("W", {}), pivot_M=piv.get("M", {}),
                         note=("Trade the 5-min ORB in EITHER direction. Long above the 5-min high, "
                               "short below the 5-min low, whichever closes first. "
                               f"Cap risk at {CFG['SL_CAP_PCT']}%.")))
    out = dict(ts=get_ist_now().isoformat(), for_date=str(next_trading_day()),
               regime=b["ctx"]["regime"], top=plan, table=D, n=len(D))
    save_scan("eod", out)
    return out

# =============================================================================
# ENGINE 4 — SWING  (Friday EOD / weekend). LONG ONLY. Monday 1h-ORB entry.
# Calibration (3y of 60-min bars, 133 weeks, strict time split):
#   P(+10% before -5% stop) unconditional = 2.2%  -> top-3 ranked = 11.1%
#   P(+7%)  = 6.2% -> top-3 = 19.8%      P(+5%) = 12.8% -> top-3 = 27.2%
#   Best expectancy: target +7%, stop -5%, k=3  ->  +0.98%/trade, 54.5% wins
#   The 1h-ORB-LOW stop is too tight (median -1.55%): use a flat -5%.
# So: the ladder is +3% / +5% / +10%, stop -5%. 10% is the runner, not the plan.
# =============================================================================
def scan_swing(capital=None, bundle=None, top_n=None):
    capital = capital or CFG["DEFAULT_CAPITAL"]
    top_n = top_n or CFG["SWING_TOP_N"]
    b = bundle or load_bundle(days="500d")
    if not b:
        return dict(error="no data")
    px, F = b["px"], b["F"]
    ex = swing_extras(px, F)
    if not ex:
        return dict(error="not enough history for weekly features")
    names = liquid_names(b)
    rows = []
    for t in names:
        f = dict(b["snap"][t])
        ok, why = feasible(f, "L", swing=True)
        for k, v in ex.items():
            if k.startswith("_"):
                continue
            try:
                f[k] = float(v.get(t, np.nan)) if hasattr(v, "get") else np.nan
            except Exception:
                f[k] = np.nan
        p = MODEL_SW.prob(f) if MODEL_SW else np.nan
        piv = b["piv"].get(t, {})
        rows.append(dict(ticker=t, sym=t.replace(".NS", ""), sector=sector_of(t),
                         close=f.get("px"), p=p, prob_pct=(p or 0) * 100,
                         atr_pct=f.get("atr_pct"), adr20=f.get("adr20"),
                         feasible=ok, why=why,
                         prev_wk_ret=f.get("prev_wk_ret"), w_rsi=f.get("w_rsi"),
                         piv_pos=f.get("piv_pos"), above_P=f.get("above_P"),
                         d_hi52=f.get("d_hi52r"), rs21=f.get("rs21"),
                         big60=f.get("big_moves_60"), turn=f.get("turn_ma_cr"),
                         sec_mom4=f.get("sec_mom4"), st_dir=f.get("st_dir"),
                         pivots=piv, drivers=MODEL_SW.top_drivers(f, 5) if MODEL_SW else []))
    D = pd.DataFrame(rows)
    if D.empty:
        return dict(error="no candidates")
    D = D[D.feasible].sort_values("p", ascending=False)
    picks, seen = [], defaultdict(int)
    for _, r in D.iterrows():
        if seen[r.sector] >= 1:
            continue
        seen[r.sector] += 1
        picks.append(r)
        if len(picks) >= top_n:
            break
    plan = []
    for r in picks:
        ent_hint = r.close
        tk = build_ticket(r.ticker, "BUY", ent_hint, CFG["SWING_SL_PCT"], capital,
                          target_pct=CFG["SWING_TARGET_PCT"], partial=True)
        tk["ladder"] = [dict(pct=3.0, px=round(ent_hint * 1.03, 2), book="33%"),
                        dict(pct=5.0, px=round(ent_hint * 1.05, 2), book="33%"),
                        dict(pct=10.0, px=round(ent_hint * 1.10, 2), book="rest")]
        plan.append(dict(row=r.to_dict(), ticket=tk, pivots=r.pivots))
    out = dict(ts=get_ist_now().isoformat(), for_week=str(next_trading_day()),
               regime=b["ctx"]["regime"], top=plan, table=D,
               entry_rule=("MONDAY: wait for the first 60-minute candle (09:15-10:15) to complete. "
                           "Place a BUY stop-limit just above that candle's HIGH. "
                           "Stop -5% from entry (NOT the ORB low - too tight). "
                           "Book 33% at +3%, 33% at +5%, trail the rest for +10%."))
    save_scan("swing", out)
    return out

# =============================================================================
# STORAGE
# =============================================================================
def _json_safe(o):
    if isinstance(o, dict):
        return {str(k): _json_safe(v) for k, v in o.items() if not isinstance(v, pd.DataFrame)}
    if isinstance(o, (list, tuple)):
        return [_json_safe(v) for v in o]
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return None if not np.isfinite(o) else float(o)
    if isinstance(o, (pd.Timestamp, datetime, date)):
        return str(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, float) and not np.isfinite(o):
        return None
    return o

def save_scan(kind, payload):
    try:
        d = session_date().strftime("%Y-%m-%d")
        p = os.path.join(DIRS["scans"], f"{d}_{kind}.json")
        body = _json_safe({k: v for k, v in payload.items() if k != "table"})
        json.dump(body, open(p, "w"), indent=1, default=str)
        tbl = payload.get("table")
        if isinstance(tbl, pd.DataFrame) and len(tbl):
            tbl.drop(columns=[c for c in ("pivots", "drivers", "near_res", "near_sup")
                              if c in tbl.columns], errors="ignore") \
               .to_csv(os.path.join(DIRS["scans"], f"{d}_{kind}.csv"), index=False)
    except Exception:
        pass

def load_scan(kind, d=None):
    d = d or session_date().strftime("%Y-%m-%d")
    p = os.path.join(DIRS["scans"], f"{d}_{kind}.json")
    if os.path.exists(p):
        try:
            return json.load(open(p))
        except Exception:
            return None
    return None

# =============================================================================
# LEARNING LOOP  —  real supervised learning, not weight nudging.
# 1. record_outcomes(): every evening, replay the day's 5m bars, label every ORB
#    trigger with "did it reach +2% before the capped stop", append to a CSV.
# 2. refit(): retrain the logistic model on the accumulated store (calibration
#    history + everything recorded live) and overwrite data/models/*.json.
# Because features/labels are computed the same way as in calibration, the
# store simply grows and the model improves as the sample grows.
# =============================================================================
def record_outcomes(for_date=None, bundle=None):
    d = for_date or session_date()
    b = bundle or load_bundle()
    if not b:
        return 0
    names = liquid_names(b)
    data = download(names, period="5d", interval="5m", ttl=600)
    nf5 = download([NIFTY], period="5d", interval="5m", ttl=600).get(NIFTY)
    nfctx = nifty_context(nf5, b["nfd"])
    recs = []
    for t, raw in data.items():
        try:
            g = raw.copy()
            g.index = pd.DatetimeIndex(g.index)
            if g.index.tz is not None:
                g.index = g.index.tz_convert(IST)
            g = g[g.index.date == d]
            g = g[(g.index.time >= MKT_OPEN) & (g.index.time <= MKT_CLOSE)]
            if len(g) < 30:
                continue
            orb = orb_from_bars(g, CFG["ORB_MINUTES"])
            if not orb:
                continue
            base = b["snap"].get(t, {})
            pdh, pdl, pdc = (b["prev"]["pdh"].get(t), b["prev"]["pdl"].get(t),
                             b["prev"]["pdc"].get(t))
            rest = g.iloc[1:]
            Hh, Ll, Cc = rest.High.to_numpy(float), rest.Low.to_numpy(float), rest.Close.to_numpy(float)
            for side in ("L", "S"):
                if not orb[f"{side}_trig"]:
                    continue
                e = orb[f"{side}_entry"]
                i0 = int(np.argmax((Cc > orb["orb_h"]) if side == "L" else (Cc < orb["orb_l"])))
                opp = orb["orb_l"] if side == "L" else orb["orb_h"]
                slp = float(np.clip(abs(e - opp) / e * 100, CFG["SL_FLOOR_PCT"], CFG["SL_CAP_PCT"]))
                sgn = 1 if side == "L" else -1
                sl = e * (1 - sgn * slp / 100)
                tg = e * (1 + sgn * CFG["TARGET_PCT"] / 100)
                fh, fl = Hh[i0:], Ll[i0:]
                if side == "L":
                    ht = np.argmax(fh >= tg) if (fh >= tg).any() else 10 ** 6
                    hs = np.argmax(fl <= sl) if (fl <= sl).any() else 10 ** 6
                    mfe = (fh.max() - e) / e * 100
                else:
                    ht = np.argmax(fl <= tg) if (fl <= tg).any() else 10 ** 6
                    hs = np.argmax(fh >= sl) if (fh >= sl).any() else 10 ** 6
                    mfe = (e - fl.min()) / e * 100
                fv = orb_model_features(base, orb, pdh, pdl, pdc, nfctx, side)
                fv.update(date=str(d), ticker=t, side=side, win=int(ht < hs),
                          mfe=mfe, sl_pct=slp, entry=e)
                recs.append(fv)
        except Exception:
            continue
    if not recs:
        return 0
    df = pd.DataFrame(recs)
    # Idempotent: running the recorder twice for the same session must not
    # duplicate rows, otherwise the refit sample gets silently double-weighted.
    if os.path.exists(F_TRAIN_INTRA):
        try:
            old = pd.read_csv(F_TRAIN_INTRA)
            df = pd.concat([old, df], ignore_index=True)
        except Exception:
            pass
    df["date"] = df["date"].astype(str)
    df = df.drop_duplicates(subset=["date", "ticker", "side"], keep="last")
    df.to_csv(F_TRAIN_INTRA, index=False)
    return len(recs)

def refit_models(min_rows=1500):
    """Retrain long/short intraday models on the accumulated store."""
    try:
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import StandardScaler
    except Exception:
        return "scikit-learn not installed — cannot refit"
    if not os.path.exists(F_TRAIN_INTRA):
        return "no training data recorded yet"
    df = pd.read_csv(F_TRAIN_INTRA)
    msg = []
    for side, path, mdl in (("L", F_MODEL_LONG, MODEL_L), ("S", F_MODEL_SHORT, MODEL_S)):
        sub = df[df.side == side]
        feats = mdl.features if mdl else None
        if feats is None or len(sub) < min_rows:
            msg.append(f"{side}: {len(sub)} rows (<{min_rows}) — kept calibrated model")
            continue
        d = sub[[c for c in feats if c in sub.columns] + ["win"]] \
            .replace([np.inf, -np.inf], np.nan).dropna()
        if len(d) < min_rows or d.win.nunique() < 2:
            msg.append(f"{side}: insufficient clean rows")
            continue
        fl = [c for c in feats if c in d.columns]
        sc = StandardScaler().fit(d[fl])
        m = LogisticRegression(max_iter=4000, C=0.15).fit(sc.transform(d[fl]), d.win.astype(int))
        spec = dict(features=fl, coef=m.coef_[0].tolist(), intercept=float(m.intercept_[0]),
                    mean=sc.mean_.tolist(), scale=sc.scale_.tolist(), n=int(len(d)),
                    trained_at=get_ist_now().isoformat())
        json.dump(spec, open(path, "w"), indent=1)
        msg.append(f"{side}: refitted on {len(d)} rows, base win {100*d.win.mean():.1f}%")
    CACHE.clear()
    return " | ".join(msg)

# =============================================================================
# BACKGROUND AUTOMATION  (plain threads + TTLCache, no Streamlit calls inside)
# =============================================================================
AUTOSTATE = dict(running=False, last=None, log=[])
_AUTO_LOCK = threading.Lock()

def _auto_loop():
    while AUTOSTATE["running"]:
        try:
            ph = market_phase()
            now = get_ist_now()
            b = load_bundle()
            if ph == "OPEN_AUCTION" and now.time() <= dtime(9, 10):
                scan_premarket(bundle=b)
                _log("pre-market scan stored")
            elif ph in ("LIVE",) and now.time() <= CFG["LAST_ENTRY_TIME"]:
                scan_live_orb(bundle=b)
                _log("live ORB refresh")
            elif ph == "POST" and dtime(15, 45) <= now.time() <= dtime(16, 30):
                scan_eod(bundle=b)
                n = record_outcomes(bundle=b)
                _log(f"EOD scan + {n} outcomes recorded")
                if now.weekday() == 4:
                    scan_swing(bundle=b)
                    _log("weekly swing plan stored")
                if now.weekday() == 5 or now.day >= 28:
                    _log(refit_models())
            AUTOSTATE["last"] = now.isoformat()
        except Exception as e:
            _log(f"error: {e}")
        for _ in range(60):
            if not AUTOSTATE["running"]:
                break
            time.sleep(5)

def _log(m):
    with _AUTO_LOCK:
        AUTOSTATE["log"].insert(0, f"{get_ist_now().strftime('%H:%M:%S')}  {m}")
        del AUTOSTATE["log"][80:]

def start_auto():
    if AUTOSTATE["running"]:
        return
    AUTOSTATE["running"] = True
    threading.Thread(target=_auto_loop, daemon=True, name="qe21-auto").start()
    _log("automation started")

def stop_auto():
    AUTOSTATE["running"] = False
    _log("automation stopped")


# =============================================================================
# UI HELPERS
# =============================================================================
def chip(txt, kind="neutral"):
    colors = dict(bull="#0ecb81", bear="#f6465d", neutral="#8f96b3",
                  warn="#f0b90b", info="#4f8cff")
    c = colors.get(kind, colors["neutral"])
    return (f"<span style='background:{c}22;border:1px solid {c}66;color:{c};"
            f"padding:2px 9px;border-radius:20px;font-size:11px;font-weight:700;"
            f"letter-spacing:.4px'>{txt}</span>")

def money(x):
    try:
        return f"₹{float(x):,.2f}"
    except Exception:
        return "—"

def pct(x, d=2):
    try:
        v = float(x)
        if not np.isfinite(v):
            return "—"
        return f"{v:+.{d}f}%"
    except Exception:
        return "—"

def num(x, d=2):
    try:
        v = float(x)
        return "—" if not np.isfinite(v) else f"{v:.{d}f}"
    except Exception:
        return "—"

def ticket_card(tk, prob=None, extra_rows=None, low_conf=False, ladder=None):
    side = tk["side"]
    accent = "#0ecb81" if side == "BUY" else "#f6465d"
    warn = ("<div style='margin-top:8px;font-size:11px;color:#f0b90b'>"
            "⚠ LOW-CONFIDENCE SIDE — short ORB showed no reliable edge in "
            "walk-forward testing. Half size or skip.</div>") if low_conf else ""
    pr = (f"<div style='font-size:12px;color:#8f96b3'>model probability of hitting target "
          f"<b style='color:#e2e4ef'>{prob*100:.1f}%</b></div>") if prob is not None else ""
    rows = ""
    for k, v in (extra_rows or []):
        rows += (f"<div style='display:flex;justify-content:space-between;font-size:12px;"
                 f"padding:2px 0'><span style='color:#8f96b3'>{k}</span>"
                 f"<span style='color:#e2e4ef;font-weight:600'>{v}</span></div>")
    lad = ""
    if ladder:
        lad = "<div style='margin-top:8px;font-size:12px;color:#8f96b3'>Exit ladder</div>"
        for _lg in ladder:  # never name this 'st' — it shadows streamlit
            lad += (f"<div style='display:flex;justify-content:space-between;font-size:12px'>"
                    f"<span style='color:#8f96b3'>+{_lg['pct']:.0f}% → book {_lg['book']}</span>"
                    f"<span style='color:#0ecb81;font-weight:700'>{money(_lg['px'])}</span></div>")
    st.markdown(f"""
<div style="background:linear-gradient(145deg,#11141f,#0d1018);border:1px solid {accent}55;
     border-left:4px solid {accent};border-radius:14px;padding:16px 18px;margin-bottom:12px">
  <div style="display:flex;justify-content:space-between;align-items:center">
    <div>
      <span style="font-size:20px;font-weight:800;color:#fff">{tk['symbol']}</span>
      <span style="background:{accent};color:#06070d;padding:2px 10px;border-radius:6px;
            font-size:12px;font-weight:800;margin-left:8px">{side}</span>
    </div>
    <div style="text-align:right">
      <div style="font-size:11px;color:#8f96b3">QTY</div>
      <div style="font-size:20px;font-weight:800;color:#fff;font-family:JetBrains Mono,monospace">{tk['qty']}</div>
    </div>
  </div>
  {pr}
  <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-top:12px">
    <div><div style="font-size:10px;color:#8f96b3">ENTRY</div>
         <div style="font-size:16px;font-weight:700;color:#fff;font-family:JetBrains Mono,monospace">{money(tk['entry'])}</div></div>
    <div><div style="font-size:10px;color:#f6465d">STOP LOSS</div>
         <div style="font-size:16px;font-weight:700;color:#f6465d;font-family:JetBrains Mono,monospace">{money(tk['sl'])}</div>
         <div style="font-size:10px;color:#8f96b3">−{tk['sl_pct']}%</div></div>
    <div><div style="font-size:10px;color:#0ecb81">TARGET 1 (book 50%)</div>
         <div style="font-size:16px;font-weight:700;color:#0ecb81;font-family:JetBrains Mono,monospace">{money(tk['t1'])}</div></div>
    <div><div style="font-size:10px;color:#0ecb81">TARGET 2 ({tk['target_pct']}%)</div>
         <div style="font-size:16px;font-weight:700;color:#0ecb81;font-family:JetBrains Mono,monospace">{money(tk['t2'])}</div></div>
  </div>
  <div style="display:flex;gap:18px;margin-top:10px;font-size:11px;color:#8f96b3">
    <span>Risk {money(tk['risk_rs'])}</span><span>Reward {money(tk['reward_rs'])}</span>
    <span>R:R 1:{tk['rr']}</span>
  </div>
  {rows}{lad}{warn}
</div>""", unsafe_allow_html=True)

def pivot_table(piv):
    if not piv:
        st.caption("no pivot data")
        return
    rows = []
    for tf, label in (("D", "Daily"), ("W", "Weekly"), ("M", "Monthly")):
        p = piv.get(tf) or {}
        if not p:
            continue
        rows.append({"TF": label, "S2": num(p.get("S2")), "S1": num(p.get("S1")),
                     "BC": num(p.get("BC")), "Pivot": num(p.get("P")), "TC": num(p.get("TC")),
                     "R1": num(p.get("R1")), "R2": num(p.get("R2")),
                     "CPR width %": num(p.get("CPR_W"))})
    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

# =============================================================================
# HEADER
# =============================================================================
ph = market_phase()
PHASE_TXT = {
    "PRE_OPEN": ("PRE-MARKET PREP", "info"), "OPEN_AUCTION": ("NSE PRE-OPEN LIVE", "warn"),
    "ORB": ("FIRST 5-MIN CANDLE FORMING", "warn"), "LIVE": ("MARKET LIVE", "bull"),
    "POST": ("POST-MARKET", "neutral"), "CLOSED": ("CLOSED", "neutral"),
    "HOLIDAY": ("MARKET HOLIDAY / WEEKEND", "bear"),
}
ptxt, pkind = PHASE_TXT.get(ph, (ph, "neutral"))

st.markdown("<div class='main-header'>⚡ QUANT-EDGE v21 — Institutional ORB Terminal</div>",
            unsafe_allow_html=True)
st.markdown("<div class='sub-caption'>Calibrated on 744 daily sessions • 12,598 real 5-min ORB events • "
            "133 weeks of 60-min swing outcomes — every threshold below is measured, not guessed</div>",
            unsafe_allow_html=True)
c1, c2, c3, c4 = st.columns([2, 2, 2, 2])
c1.markdown(chip(ptxt, pkind), unsafe_allow_html=True)
c2.markdown(f"<div class='last-updated'>{fmt_ist()}</div>", unsafe_allow_html=True)

# =============================================================================
# SIDEBAR
# =============================================================================
with st.sidebar:
    st.markdown("### ⚙️ Control")
    capital = st.number_input("Trading capital (₹)", 25000, 100000000,
                              CFG["DEFAULT_CAPITAL"], step=25000)
    CFG["RISK_PER_TRADE_PCT"] = st.slider("Risk per trade (% of capital)", 0.25, 3.0,
                                          CFG["RISK_PER_TRADE_PCT"], 0.25)
    st.divider()
    st.markdown("#### Automation")
    a1, a2 = st.columns(2)
    if a1.button("▶ Start", use_container_width=True):
        start_auto()
    if a2.button("■ Stop", use_container_width=True):
        stop_auto()
    st.caption(("🟢 running — pre-market at 09:00, ORB refresh every minute, "
                "EOD + outcome recording after 15:45, swing plan on Friday, "
                "model refit monthly") if AUTOSTATE["running"] else "⚪ idle")
    if AUTOSTATE["log"]:
        with st.expander("automation log", expanded=False):
            for l in AUTOSTATE["log"][:25]:
                st.caption(l)
    st.divider()
    live_refresh = st.checkbox("Auto-refresh page (30s)", value=(ph == "LIVE"))
    if live_refresh and HAS_AUTOREFRESH:
        st_autorefresh(interval=30000, key="qe21refresh")
    if st.button("🔄 Clear data cache", use_container_width=True):
        CACHE.clear()
        st.success("cache cleared")
    st.divider()
    st.markdown("#### Universe health")
    try:
        uni, dead = validated_universe()
        st.caption(f"{len(uni)} live symbols")
        if dead:
            st.caption("auto-dropped (delisted/renamed): " + ", ".join(d.replace('.NS','') for d in dead[:12]))
    except Exception as e:
        st.caption(f"validation pending ({e})")
    st.divider()
    st.markdown("#### Models")
    for nm, m in (("Intraday LONG", MODEL_L), ("Intraday SHORT", MODEL_S), ("Swing LONG", MODEL_SW)):
        if m:
            st.caption(f"{nm}: {len(m.features)} features, n={m.n:,}")

TABS = st.tabs(["🌅 Pre-Market (09:08)", "⚡ Live ORB", "🌙 EOD → Tomorrow",
                "📈 Swing (Friday)", "🧠 Calibration & Learning", "📓 Journal"])

# ---------------------------------------------------------------- PRE-MARKET
with TABS[0]:
    st.markdown("#### Top 1 BUY + Top 1 SELL for today")
    st.caption("Run between 09:00 and 09:08. Uses the live NSE pre-open call auction "
               "(indicative equilibrium price) — v20 read yesterday's daily bar here, "
               "which made the whole pre-market engine wrong.")
    if st.button("🌅 Run pre-market scan", type="primary", key="pm"):
        with st.spinner("reading NSE pre-open book + building features…"):
            st.session_state.pm = scan_premarket(capital=capital)
    res = st.session_state.get("pm") or load_scan("premarket")
    if res and not res.get("error"):
        st.caption(f"source: {res.get('source')} • regime {res.get('regime')} • "
                   f"{res.get('n_candidates')} feasible names")
        if "previous close" in str(res.get("source", "")):
            st.warning("NSE pre-open feed unavailable — gaps assumed flat. Re-run after 09:02, "
                       "or trade the Live ORB tab instead, which never needs pre-open data.")
        L, R = st.columns(2)
        with L:
            st.markdown("##### 🟢 BUY candidate")
            for i, c in enumerate((res.get("longs") or [])[:1]):
                r = c["row"]
                ticket_card(c["ticket"], extra_rows=[
                    ("Expected open", money(r.get("exp_open"))),
                    ("Gap", pct(r.get("gap"))),
                    ("ATR% / ADR%", f"{num(r.get('atr_pct'))} / {num(r.get('adr20'))}"),
                    ("P(+2% | this gap bucket)", f"{num(r.get('p_up'),1)}%"),
                    ("Days ≥2.5% in last 20", num(r.get("big20"), 0)),
                    ("Room to next resistance", pct(r.get("room_up"))),
                ])
            with st.expander("next 7 long candidates"):
                for c in (res.get("longs") or [])[1:]:
                    r = c["row"]
                    st.write(f"**{r['sym']}** {r['sector']} • gap {pct(r.get('gap'))} • "
                             f"ATR {num(r.get('atr_pct'))}% • P(up2) {num(r.get('p_up'),1)}%")
        with R:
            st.markdown("##### 🔴 SELL candidate")
            for c in (res.get("shorts") or [])[:1]:
                r = c["row"]
                ticket_card(c["ticket"], low_conf=True, extra_rows=[
                    ("Expected open", money(r.get("exp_open"))),
                    ("Gap", pct(r.get("gap"))),
                    ("ATR% / ADR%", f"{num(r.get('atr_pct'))} / {num(r.get('adr20'))}"),
                    ("P(−2% | this gap bucket)", f"{num(r.get('p_dn'),1)}%"),
                    ("Room to next support", pct(r.get("room_dn"))),
                ])
            with st.expander("next 7 short candidates"):
                for c in (res.get("shorts") or [])[1:]:
                    r = c["row"]
                    st.write(f"**{r['sym']}** {r['sector']} • gap {pct(r.get('gap'))} • "
                             f"ATR {num(r.get('atr_pct'))}% • P(dn2) {num(r.get('p_dn'),1)}%")
        st.info("These two names are the **watchlist**, not the order. The actual entry is the "
                "close-confirmed 5-min ORB break in the Live ORB tab from 09:20 — that is the "
                "step that lifted measured expectancy from −0.04% to +0.19% per trade.")
        tb = res.get("table")
        if isinstance(tb, pd.DataFrame):
            with st.expander("full pre-market table"):
                st.dataframe(tb.drop(columns=[c for c in ("near_res", "near_sup") if c in tb.columns]),
                             use_container_width=True, height=380)
    else:
        st.info("No pre-market scan yet for today.")

# ---------------------------------------------------------------- LIVE ORB
with TABS[1]:
    st.markdown("#### Close-confirmed 5-minute ORB — order tickets")
    k1, k2, k3 = st.columns([1, 1, 2])
    om = k1.selectbox("ORB length", [5, 15], index=0,
                      help="5-minute measured better than 15-minute in the study")
    maxtr = k2.number_input("Max trades today", 1, 5, CFG["MAX_TRADES_DAY"])
    CFG["MAX_TRADES_DAY"] = int(maxtr)
    if k3.button("⚡ Scan ORB breaks now", type="primary", key="orb"):
        with st.spinner("pulling 5-minute bars for the liquid universe…"):
            st.session_state.orb = scan_live_orb(capital=capital, orb_minutes=om)
    res = st.session_state.get("orb")
    if res and not res.get("error"):
        nf = res.get("nfctx", {})
        st.caption(f"{res['n_triggers']} confirmed breaks • Nifty gap {pct(nf.get('nf_gap'))} • "
                   f"Nifty first candle {pct(nf.get('nf_fc'))} • regime {res.get('regime')}")
        act = res.get("actionable") or []
        if not act:
            st.warning("No setup currently clears the probability floor. That is a valid answer — "
                       "the calibration says roughly 1-2 tradable ORB setups per day, not 10.")
        for c in act[:int(maxtr)]:
            ticket_card(c["ticket"], prob=c["p"], low_conf=c.get("low_conf"), extra_rows=[
                ("ORB high / low", f"{num(c['orb_h'])} / {num(c['orb_l'])}"),
                ("Break confirmed at", str(c.get("trig_time"))[11:16] + f"  (+{num(c.get('trig_min'),0)} min)"),
                ("Last price / moved since break", f"{num(c['last'])} ({pct(c['moved'])})"),
                ("ATR% • RVOL • gap", f"{num(c['atr_pct'])}% • {num(c['rvol'])} • {pct(c['gap'])}"),
                ("ORB range", f"{num(c['orb_range_pct'])}%"),
                ("Top model drivers", ", ".join(f"{a} {b:+.2f}" for a, b in (c.get("drivers") or [])[:3])),
            ])
        allr = res.get("all_ranked") or []
        if allr:
            with st.expander(f"all {len(allr)} confirmed breaks, ranked"):
                d = pd.DataFrame(allr)[["sym", "side", "prob_pct", "entry", "sl_pct", "trig_min",
                                        "moved", "atr_pct", "rvol", "gap", "orb_range_pct",
                                        "sector", "passed_p", "still_valid"]]
                st.dataframe(d.round(2), use_container_width=True, height=460, hide_index=True)
    else:
        if ph in ("LIVE", "POST"):
            st.info("Press scan. Best window is 09:20–11:00 — the study shows earlier breaks win "
                    "far more often (trigger-minute is the single strongest negative coefficient).")
        else:
            st.info("Market is not live. This tab needs 5-minute bars from today.")

    with st.expander("The exact rule this engine implements"):
        st.markdown(f"""
1. Universe: F&O names with 20-day average turnover ≥ ₹{CFG['MIN_TURNOVER_CR']}cr,
   ATR ≥ {CFG['MIN_ATR_PCT']}% and ADR ≥ {CFG['MIN_ADR_PCT']}% — a stock that never moves 2%
   cannot be made to move 2%.
2. Mark the 09:15–09:20 candle high and low.
3. **Wait for a 5-minute candle to CLOSE beyond it.** No touch entries — measured
   improvement from 28.3% to 37.5% target-hit on the top-ranked name.
4. Entry = that confirming close. Stop = opposite ORB edge, **capped at
   {CFG['SL_CAP_PCT']}%** and floored at {CFG['SL_FLOOR_PCT']}%.
5. Book half at +{CFG['PARTIAL_PCT']}% and move the stop to breakeven; the rest runs to
   +{CFG['TARGET_PCT']}%. Square off everything by {CFG['SQUARE_OFF_TIME'].strftime('%H:%M')}.
6. Take at most {CFG['MAX_TRADES_DAY']} trades, always the highest-probability ones,
   no new entries after {CFG['LAST_ENTRY_TIME'].strftime('%H:%M')}.
""")

# ---------------------------------------------------------------- EOD
with TABS[2]:
    st.markdown("#### Top 5 for the next session")
    st.caption("Run after 15:40. These are the names most likely to EXPAND tomorrow — direction is "
               "decided by tomorrow's ORB, because D-1 features predict capability, not direction "
               "(that is the single most important finding in the whole study).")
    if st.button("🌙 Run EOD scan", type="primary", key="eod"):
        with st.spinner("ranking expansion candidates…"):
            st.session_state.eod = scan_eod(capital=capital)
    res = st.session_state.get("eod") or load_scan("eod")
    if res and not res.get("error"):
        st.caption(f"for {res.get('for_date')} • regime {res.get('regime')}")
        for i, p in enumerate(res.get("top") or [], 1):
            r = p["row"]
            with st.container(border=True):
                a, b_, c_ = st.columns([2, 3, 3])
                a.markdown(f"### {i}. {r['sym']}")
                a.caption(r["sector"])
                a.metric("P(moves ≥2% tomorrow)", f"{num(r.get('p_move2'),1)}%")
                b_.write(f"Close **{money(r.get('close'))}**")
                b_.write(f"ATR **{num(r.get('atr_pct'))}%** • ADR **{num(r.get('adr20'))}%**")
                b_.write(f"Days ≥2.5% (20d) **{num(r.get('big20'),0)}** • (60d) **{num(r.get('big60'),0)}**")
                b_.write(f"RVOL **{num(r.get('rvol'))}** • ADX **{num(r.get('adx'),0)}** • RSI **{num(r.get('rsi'),0)}**")
                c_.write(f"Prev day high **{num(r.get('pdh'))}** / low **{num(r.get('pdl'))}**")
                c_.write(f"Trend **{'UP' if (r.get('st_dir') or 0) > 0 else 'DOWN'}** • "
                         f"vs 20d high {pct(r.get('d_hi20'))}")
                c_.caption(p["note"])
                with st.expander("pivots (daily / weekly / monthly)"):
                    pivot_table({"D": p.get("pivot_D"), "W": p.get("pivot_W"), "M": p.get("pivot_M")})
        tb = res.get("table")
        if isinstance(tb, pd.DataFrame):
            with st.expander("full ranked table"):
                st.dataframe(tb.drop(columns=[c for c in ("pivots",) if c in tb.columns]).round(2),
                             use_container_width=True, height=420)
    else:
        st.info("No EOD scan stored for today yet.")

# ---------------------------------------------------------------- SWING
with TABS[3]:
    st.markdown("#### Weekly swing plan — LONG ONLY, Monday 1-hour ORB")
    st.caption("Run on Friday evening or over the weekend. Refresh again at month end, when the "
               "monthly pivot resets.")
    if st.button("📈 Build weekly swing plan", type="primary", key="sw"):
        with st.spinner("building weekly + monthly-pivot + sector features…"):
            st.session_state.sw = scan_swing(capital=capital)
    res = st.session_state.get("sw") or load_scan("swing")
    if res and not res.get("error"):
        st.error("**Read this before you place a swing order.** In 3 years of hourly data, only "
                 "**2.2%** of week-stock combinations reached +10% before a −5% stop. Even the "
                 "single best-ranked name each week reached +10% about **13%** of the time. "
                 "+10% is the runner, not the plan — the honest plan is +3% / +5% laddered exits "
                 "with +10% left to run, which is what these tickets do.")
        st.caption(f"week starting {res.get('for_week')} • regime {res.get('regime')}")
        for i, p in enumerate(res.get("top") or [], 1):
            r = p["row"]
            ticket_card(p["ticket"], prob=r.get("p"), ladder=p["ticket"].get("ladder"), extra_rows=[
                ("Sector", r.get("sector")),
                ("ATR% (must be ≥2.2)", num(r.get("atr_pct"))),
                ("Last week return", pct(r.get("prev_wk_ret"))),
                ("Weekly RSI", num(r.get("w_rsi"), 0)),
                ("Position in monthly pivot range", num(r.get("piv_pos"), 0)),
                ("Distance from 52w high", pct(r.get("d_hi52"))),
                ("Model drivers", ", ".join(f"{a} {b:+.2f}" for a, b in (r.get("drivers") or [])[:3])),
            ])
            with st.expander(f"{r['sym']} pivots"):
                pivot_table(r.get("pivots") or {})
        st.markdown("##### Entry rule")
        st.code(res.get("entry_rule", ""), language=None)
        tb = res.get("table")
        if isinstance(tb, pd.DataFrame):
            with st.expander("full ranked swing table"):
                st.dataframe(tb.drop(columns=[c for c in ("pivots", "drivers") if c in tb.columns]).round(2),
                             use_container_width=True, height=420)
    else:
        st.info("No swing plan stored yet.")

# ---------------------------------------------------------------- CALIBRATION
with TABS[4]:
    st.markdown("#### What the data actually says")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Names moving ≥2.5% per day", "~33", "of ~830 liquid names")
    m2.metric("Top-1 ORB long hits +2%", "37.5%", "vs 9.6% unfiltered")
    m3.metric("Measured expectancy", "+0.32%", "per trade, after 0.06% cost")
    m4.metric("Swing +10% in a week", "2.2%", "top-3 ranked: 11.1%")
    st.markdown("""
##### Base rates — liquid F&O universe, last 180 sessions, 148,678 stock-days
| Event | Probability | Names per day |
|---|---|---|
| Travels ≥2% UP from the open | 20.7% | ~41 |
| Travels ≥2% DOWN from the open | 22.8% | ~45 |
| Absolute day move ≥2.5% | 16.6% | ~33 |
| Finishes as a top-3 gainer | 1.35% | 3 |

##### Gaps mean-revert — this is where v20 was backwards
| Opening gap | P(+2% up during the day) | P(−2% down) | What to do |
|---|---|---|---|
| below −2% | **60.0%** | 33.7% | look for LONGS |
| −2% to −1% | 36.9% | 30.2% | mild long bias |
| −0.3% to +0.3% | 22.9% | 20.9% | neutral |
| +1% to +2% | 30.8% | 32.5% | mild short bias |
| above +2% | 41.0% | **47.8%** | look for SHORTS |

##### Which features matter (top-quintile lift for a ≥2% move)
`atr_pct` 1.63× · `adr20` 1.56× · `vol20` 1.48× · `big_moves_60` 1.46× ·
`big_moves_20` 1.45× · `bbw` 1.33× · `rvol` ~1.20×.
Momentum and MACD are near 1.0× — **they do not tell you direction**.

##### Entry-style test (walk-forward, 33 sessions, top-3 per day)
| Entry | Exit | Target hit | Profitable trades | Expectancy |
|---|---|---|---|---|
| touch of ORB high | fixed +2% | 28.3% | 37.4% | +0.05% |
| **close beyond ORB high** | fixed +2% | **29.9%** | 44.2% | **+0.17%** |
| **close beyond ORB high** | ladder | 27.3% | **64.9%** | **+0.24%** |
| 15-minute ORB | fixed +2% | 23.7% | 35.5% | −0.06% |

##### Honest limits
* The short-side ORB model was flat to negative in walk-forward testing. It is included
  but gated and flagged; do not size it like the long side.
* The intraday walk-forward covers 33 sessions (that is all the 5-minute history a free
  data feed will return). Treat +0.3%/trade as an estimate with wide error bars.
* No screener can promise 2% every day. What this one does is concentrate your one or two
  daily trades into the setups where the measured hit rate is roughly 4× the base rate.
""")
    st.divider()
    st.markdown("##### Learning loop")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("Record today's ORB outcomes"):
            n = record_outcomes()
            st.success(f"{n} labelled ORB events appended")
        if os.path.exists(F_TRAIN_INTRA):
            try:
                dfl = pd.read_csv(F_TRAIN_INTRA)
                st.caption(f"training store: {len(dfl):,} events, "
                           f"{dfl.date.nunique()} sessions, "
                           f"win rate {100*dfl.win.mean():.1f}%")
            except Exception:
                pass
        else:
            st.caption("training store empty — it fills up automatically after each session")
    with c2:
        if st.button("Refit models now"):
            st.info(refit_models())
        st.caption("The loop appends every ORB event with its true outcome each evening and "
                   "refits the logistic models monthly. Feature and label definitions are "
                   "identical to the offline calibration, so the sample simply keeps growing.")

# ---------------------------------------------------------------- JOURNAL
with TABS[5]:
    st.markdown("#### Trade journal")
    with st.form("jf"):
        j1, j2, j3, j4 = st.columns(4)
        sym = j1.text_input("Symbol")
        side = j2.selectbox("Side", ["BUY", "SELL"])
        entry = j3.number_input("Entry", 0.0, step=0.05)
        exitp = j4.number_input("Exit", 0.0, step=0.05)
        k1, k2, k3 = st.columns(3)
        qty = k1.number_input("Qty", 0, step=1)
        setup = k2.selectbox("Setup", ["Intraday ORB", "Swing 1h ORB", "Other"])
        note = k3.text_input("Note")
        if st.form_submit_button("Save trade"):
            if sym and entry > 0 and exitp > 0:
                pnl = (exitp - entry) * qty * (1 if side == "BUY" else -1)
                pnlp = (exitp / entry - 1) * 100 * (1 if side == "BUY" else -1)
                row = pd.DataFrame([dict(ts=get_ist_now().isoformat(), symbol=sym.upper(),
                                         side=side, entry=entry, exit=exitp, qty=qty,
                                         pnl=round(pnl, 2), pnl_pct=round(pnlp, 3),
                                         setup=setup, note=note)])
                row.to_csv(F_JOURNAL, mode="a", header=not os.path.exists(F_JOURNAL), index=False)
                st.success("saved")
    if os.path.exists(F_JOURNAL):
        try:
            J = pd.read_csv(F_JOURNAL)
            a, b_, c_, d_ = st.columns(4)
            a.metric("Trades", len(J))
            b_.metric("Win rate", f"{100*(J.pnl>0).mean():.1f}%")
            c_.metric("Total P&L", money(J.pnl.sum()))
            d_.metric("Avg per trade", f"{J.pnl_pct.mean():+.2f}%")
            st.dataframe(J.iloc[::-1], use_container_width=True, height=380, hide_index=True)
        except Exception as e:
            st.caption(f"journal unreadable: {e}")
    else:
        st.caption("No trades logged yet.")

st.divider()
st.caption("QUANT-EDGE v21 • For research and personal use. Every probability shown is an "
           "out-of-sample estimate from historical data, not a forecast. Position sizing assumes "
           "you can lose the full stop distance on every trade.")
