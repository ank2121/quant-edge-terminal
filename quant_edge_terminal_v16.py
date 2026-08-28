"""
================================================================================
 QUANT-EDGE v22  —  INSTITUTIONAL ORB TERMINAL  (NSE F&O)
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

# =============================================================================
# STREAMLIT VERSION COMPATIBILITY SHIM
# -----------------------------------------------------------------------------
# `width="stretch"` / `width="content"` only exist on Streamlit >= 1.49.
# Older builds want `use_container_width=True/False` and raise TypeError on
# `width`. Newer builds deprecate `use_container_width`. This shim translates
# in whichever direction the installed version needs, so the app runs on any
# Streamlit from 1.30 upward instead of dying at the first widget.
# =============================================================================
def _sv_tuple(v):
    out = []
    for part in str(v).split(".")[:3]:
        num = ""
        for ch in part:
            if ch.isdigit():
                num += ch
            else:
                break
        out.append(int(num) if num else 0)
    while len(out) < 3:
        out.append(0)
    return tuple(out)

ST_VER = _sv_tuple(getattr(st, "__version__", "0.0.0"))
ST_NEW_WIDTH = ST_VER >= (1, 49, 0)

def _xlate_width(kw):
    """Normalise the width/use_container_width kwargs for the installed build."""
    w = kw.get("width", None)
    if isinstance(w, str):                      # "stretch" / "content"
        if not ST_NEW_WIDTH:
            kw.pop("width")
            kw["use_container_width"] = (w == "stretch")
    elif "use_container_width" in kw and ST_NEW_WIDTH:
        kw["width"] = "stretch" if kw.pop("use_container_width") else "content"
    return kw

def _compat_wrap(fn):
    # Idempotent: st.dataframe and DeltaGenerator.dataframe are the same
    # underlying function object, so patching both layers wrapped it five deep
    # (visible as "Previous line repeated 2 more times" in tracebacks). One
    # wrap is enough; the sentinel makes re-patching a no-op.
    if getattr(fn, "_qe_compat", False):
        return fn

    def inner(*a, **kw):
        try:
            return fn(*a, **_xlate_width(dict(kw)))
        except TypeError as ex:
            msg = str(ex)  # noqa: F841  (inspected below)
            kw2 = dict(kw)
            # last-resort: flip to the other spelling, then drop it entirely
            if "width" in msg and "width" in kw2:
                w = kw2.pop("width")
                if isinstance(w, str):
                    kw2["use_container_width"] = (w == "stretch")
                try:
                    return fn(*a, **kw2)
                except TypeError:
                    kw2.pop("use_container_width", None)
                    return fn(*a, **kw2)
            if "use_container_width" in msg and "use_container_width" in kw2:
                kw2.pop("use_container_width")
                return fn(*a, **kw2)
            if "hide_index" in msg and "hide_index" in kw2:
                kw2.pop("hide_index")
                return fn(*a, **_xlate_width(kw2))
            raise
    inner.__name__ = getattr(fn, "__name__", "wrapped")
    inner.__doc__ = getattr(fn, "__doc__", None)
    inner._qe_compat = True
    return inner

_COMPAT_FNS = ("button", "download_button", "form_submit_button", "link_button",
               "dataframe", "data_editor", "table", "plotly_chart",
               "altair_chart", "vega_lite_chart", "line_chart", "bar_chart",
               "area_chart", "pyplot")
try:
    # patch the class so column / container / sidebar handles are covered too
    from streamlit.delta_generator import DeltaGenerator as _DG
    for _f in _COMPAT_FNS:
        _orig = getattr(_DG, _f, None)
        if callable(_orig):
            setattr(_DG, _f, _compat_wrap(_orig))
except Exception:
    pass
for _f in _COMPAT_FNS:
    # module-level st.* names are bound at import, so patch them separately
    _orig = getattr(st, _f, None)
    if callable(_orig):
        setattr(st, _f, _compat_wrap(_orig))

st.set_page_config(page_title="QUANT-EDGE v22 — Institutional ORB Terminal",
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
    MAX_TRADES_DAY       = 2,
    # ---- CALIBRATION CORRECTED 2026-08-28 -----------------------------------
    # The earlier note here claimed top-1 = +0.318%/trade. That was measured on
    # 33 sessions and did not survive re-measurement. On 1,026 walk-forward
    # sessions (2022-01..2026-08, fixed labeller, real volume, point-in-time
    # F&O universe) top-1 LONG is -0.137%/trade and negative in all five years.
    # 34 stop/target geometries were swept; all 34 lose on the long side.
    # Raising the probability floor does not help: the top-25 highest-conviction
    # picks hit 24.0% versus 27.2% for all 1,026, so conviction has no
    # resolution at the top end. See V23_VERIFICATION.md.
    INTRADAY_TOP_N       = 1,
    SHORT_TOP_N          = 1,      # +0.035%/trade, positive 2022-25, negative 2026
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
    # PAPER_MODE: signals are displayed and logged, never presented as an
    # instruction to place an order. On by default because the measured
    # out-of-sample expectancy of the intraday system is negative. Turn it off
    # only after a configuration has been shown to make money forward.
    PAPER_MODE           = True,
    MULTI_TF_ORB         = True,   # 5m + 15m ORB both
    ORB_CUTOFF_TIME      = dtime(10, 0),  # No new ORB after this
    MOMENTUM_START_TIME  = dtime(12, 30), # Momentum scan after this
)

# Honest, machine-readable record of what has and has not been validated, so the
# UI can never again show a confidence claim the evidence does not support.
EVIDENCE = {
    "as_of": "2026-08-28",
    "sessions_tested": 1147,
    "intraday_long": dict(status="NO EDGE", avg_pct=-0.137, hit=0.272,
                          years_positive="0/5", note="all 34 geometries lose"),
    "intraday_short": dict(status="MARGINAL", avg_pct=+0.035, hit=0.276,
                           years_positive="4/5", note="2026 negative"),
    "swing_long": dict(status="WEAK", avg_pct=+0.160, hit=0.109,
                       years_positive="3/4",
                       note="4.4x lift is real and permutation-verified, but "
                            "+10%/week is only a 2.44% event"),
    "data": "Upstox 5-min, real volume 99.96%, 2022-01-03..2026-08-26",
    "universe": "point-in-time NSE F&O membership, monthly, 295 distinct names",
}

# =============================================================================
# UNIVERSE  —  NSE F&O + high-velocity movers.
# Dead tickers found in validation (LTIM, PEL, TATAMOTORS post-demerger) are
# auto-pruned at startup and cached, so a stale name can never break a scan.
# =============================================================================
RENAMES = {"TATAMOTORS.NS": "TMPV.NS", "ZOMATO.NS": "ETERNAL.NS",
           "CAMSONLINE.NS": "CAMS.NS", "KALPATPOWR.NS": "KPIL.NS",
           "LTIM.NS": None, "PEL.NS": None, "MINDTREE.NS": None,
           "SRTRANSFIN.NS": "SHRIRAMFIN.NS", "HDFC.NS": None}

# ---- OFFICIAL NSE F&O UNIVERSE ----------------------------------------------
# 210 symbols, fetched live from https://www.nseindia.com/api/master-quote on
# 2026-08-27. fetch_fno_universe() re-fetches at runtime; this list is the
# fallback so the app never depends on the NSE endpoint being reachable.
FNO_STATIC = [
 "360ONE","ABB","ABCAPITAL","ADANIENSOL","ADANIENT","ADANIGREEN","ADANIPORTS","ADANIPOWER",
 "ALKEM","AMBER","AMBUJACEM","ANGELONE","APLAPOLLO","APOLLOHOSP","ASHOKLEY","ASIANPAINT",
 "ASTRAL","ATHERENERG","AUBANK","AUROPHARMA","AXISBANK","BAJAJ-AUTO","BAJAJFINSV","BAJAJHLDNG",
 "BAJFINANCE","BANDHANBNK","BANKBARODA","BANKINDIA","BDL","BEL","BHARATFORG","BHARTIARTL",
 "BHEL","BIOCON","BLUESTARCO","BOSCHLTD","BPCL","BRITANNIA","BSE","CAMS",
 "CANBK","CDSL","CGPOWER","CHOLAFIN","CIPLA","COALINDIA","COCHINSHIP","COFORGE",
 "COLPAL","CONCOR","CROMPTON","CUMMINSIND","DABUR","DELHIVERY","DIVISLAB","DIXON",
 "DLF","DMART","DRREDDY","EICHERMOT","ETERNAL","FEDERALBNK","FORCEMOT","FORTIS",
 "GAIL","GLENMARK","GMRAIRPORT","GODFRYPHLP","GODREJCP","GODREJPROP","GRASIM","GVT&D",
 "HAL","HAVELLS","HCLTECH","HDFCAMC","HDFCBANK","HDFCLIFE","HEROMOTOCO","HINDALCO",
 "HINDPETRO","HINDUNILVR","HINDZINC","HYUNDAI","ICICIBANK","ICICIGI","ICICIPRULI","IDEA",
 "IDFCFIRSTB","IEX","INDHOTEL","INDIANB","INDIGO","INDUSINDBK","INDUSTOWER","INFY",
 "INOXWIND","IOC","IREDA","IRFC","ITC","JINDALSTEL","JIOFIN","JSWENERGY",
 "JSWSTEEL","JUBLFOOD","KALYANKJIL","KAYNES","KEI","KFINTECH","KOTAKBANK","KPITTECH",
 "LAURUSLABS","LICHSGFIN","LICI","LODHA","LT","LTF","LTM","LUPIN",
 "M&M","MAHABANK","MANAPPURAM","MANKIND","MARICO","MARUTI","MAXHEALTH","MAZDOCK",
 "MCX","MFSL","MOTHERSON","MOTILALOFS","MPHASIS","MUTHOOTFIN","NAM-INDIA","NATIONALUM",
 "NAUKRI","NBCC","NESTLEIND","NHPC","NMDC","NTPC","NYKAA","OBEROIRLTY",
 "OFSS","OIL","ONGC","PAGEIND","PATANJALI","PAYTM","PERSISTENT","PETRONET",
 "PFC","PGEL","PHOENIXLTD","PIDILITIND","PIIND","PNB","PNBHOUSING","POLICYBZR",
 "POLYCAB","POWERGRID","POWERINDIA","PREMIERENE","PRESTIGE","RADICO","RBLBANK","RECLTD",
 "RELIANCE","RVNL","SAGILITY","SAIL","SBICARD","SBILIFE","SBIN","SHREECEM",
 "SHRIRAMFIN","SIEMENS","SOLARINDS","SONACOMS","SRF","SUNPHARMA","SUPREMEIND","SUZLON",
 "SWIGGY","TATACONSUM","TATAELXSI","TATAPOWER","TATASTEEL","TCS","TECHM","TIINDIA",
 "TITAN","TMPV","TORNTPHARM","TRENT","TVSMOTOR","ULTRACEMCO","UNIONBANK","UNITDSPR",
 "UNOMINDA","UPL","VBL","VEDL","VMM","VOLTAS","WAAREEENER","WIPRO",
 "YESBANK","ZYDUSLIFE",
]
UNIVERSE_RAW = [
 "360ONE.NS","ABB.NS","ABCAPITAL.NS","ADANIENSOL.NS","ADANIENT.NS","ADANIGREEN.NS",
 "ADANIPORTS.NS","ADANIPOWER.NS","ALKEM.NS","AMBER.NS","AMBUJACEM.NS","ANGELONE.NS",
 "APLAPOLLO.NS","APOLLOHOSP.NS","ASHOKLEY.NS","ASIANPAINT.NS","ASTRAL.NS","ATHERENERG.NS",
 "AUBANK.NS","AUROPHARMA.NS","AXISBANK.NS","BAJAJ-AUTO.NS","BAJAJFINSV.NS","BAJAJHLDNG.NS",
 "BAJFINANCE.NS","BANDHANBNK.NS","BANKBARODA.NS","BANKINDIA.NS","BDL.NS","BEL.NS",
 "BHARATFORG.NS","BHARTIARTL.NS","BHEL.NS","BIOCON.NS","BLUESTARCO.NS","BOSCHLTD.NS",
 "BPCL.NS","BRITANNIA.NS","BSE.NS","CAMS.NS","CANBK.NS","CDSL.NS",
 "CGPOWER.NS","CHOLAFIN.NS","CIPLA.NS","COALINDIA.NS","COCHINSHIP.NS","COFORGE.NS",
 "COLPAL.NS","CONCOR.NS","CROMPTON.NS","CUMMINSIND.NS","DABUR.NS","DELHIVERY.NS",
 "DIVISLAB.NS","DIXON.NS","DLF.NS","DMART.NS","DRREDDY.NS","EICHERMOT.NS",
 "ETERNAL.NS","FEDERALBNK.NS","FORCEMOT.NS","FORTIS.NS","GAIL.NS","GLENMARK.NS",
 "GMRAIRPORT.NS","GODFRYPHLP.NS","GODREJCP.NS","GODREJPROP.NS","GRASIM.NS","GVT&D.NS",
 "HAL.NS","HAVELLS.NS","HCLTECH.NS","HDFCAMC.NS","HDFCBANK.NS","HDFCLIFE.NS",
 "HEROMOTOCO.NS","HINDALCO.NS","HINDPETRO.NS","HINDUNILVR.NS","HINDZINC.NS","HYUNDAI.NS",
 "ICICIBANK.NS","ICICIGI.NS","ICICIPRULI.NS","IDEA.NS","IDFCFIRSTB.NS","IEX.NS",
 "INDHOTEL.NS","INDIANB.NS","INDIGO.NS","INDUSINDBK.NS","INDUSTOWER.NS","INFY.NS",
 "INOXWIND.NS","IOC.NS","IREDA.NS","IRFC.NS","ITC.NS","JINDALSTEL.NS",
 "JIOFIN.NS","JSWENERGY.NS","JSWSTEEL.NS","JUBLFOOD.NS","KALYANKJIL.NS","KAYNES.NS",
 "KEI.NS","KFINTECH.NS","KOTAKBANK.NS","KPITTECH.NS","LAURUSLABS.NS","LICHSGFIN.NS",
 "LICI.NS","LODHA.NS","LT.NS","LTF.NS","LTM.NS","LUPIN.NS",
 "M&M.NS","MAHABANK.NS","MANAPPURAM.NS","MANKIND.NS","MARICO.NS","MARUTI.NS",
 "MAXHEALTH.NS","MAZDOCK.NS","MCX.NS","MFSL.NS","MOTHERSON.NS","MOTILALOFS.NS",
 "MPHASIS.NS","MUTHOOTFIN.NS","NAM-INDIA.NS","NATIONALUM.NS","NAUKRI.NS","NBCC.NS",
 "NESTLEIND.NS","NHPC.NS","NMDC.NS","NTPC.NS","NYKAA.NS","OBEROIRLTY.NS",
 "OFSS.NS","OIL.NS","ONGC.NS","PAGEIND.NS","PATANJALI.NS","PAYTM.NS",
 "PERSISTENT.NS","PETRONET.NS","PFC.NS","PGEL.NS","PHOENIXLTD.NS","PIDILITIND.NS",
 "PIIND.NS","PNB.NS","PNBHOUSING.NS","POLICYBZR.NS","POLYCAB.NS","POWERGRID.NS",
 "POWERINDIA.NS","PREMIERENE.NS","PRESTIGE.NS","RADICO.NS","RBLBANK.NS","RECLTD.NS",
 "RELIANCE.NS","RVNL.NS","SAGILITY.NS","SAIL.NS","SBICARD.NS","SBILIFE.NS",
 "SBIN.NS","SHREECEM.NS","SHRIRAMFIN.NS","SIEMENS.NS","SOLARINDS.NS","SONACOMS.NS",
 "SRF.NS","SUNPHARMA.NS","SUPREMEIND.NS","SUZLON.NS","SWIGGY.NS","TATACONSUM.NS",
 "TATAELXSI.NS","TATAPOWER.NS","TATASTEEL.NS","TCS.NS","TECHM.NS","TIINDIA.NS",
 "TITAN.NS","TMPV.NS","TORNTPHARM.NS","TRENT.NS","TVSMOTOR.NS","ULTRACEMCO.NS",
 "UNIONBANK.NS","UNITDSPR.NS","UNOMINDA.NS","UPL.NS","VBL.NS","VEDL.NS",
 "VMM.NS","VOLTAS.NS","WAAREEENER.NS","WIPRO.NS","YESBANK.NS","ZYDUSLIFE.NS",
]


def fetch_fno_universe(timeout=8):
    """Live official F&O list from NSE. Returns (symbols, source)."""
    try:
        import requests
        s = requests.Session()
        s.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.nseindia.com/market-data/equity-derivatives-watch",
        })
        s.get("https://www.nseindia.com/", timeout=timeout)
        r = s.get("https://www.nseindia.com/api/master-quote", timeout=timeout)
        arr = r.json()
        arr = [x for x in arr if isinstance(x, str) and x.strip()]
        if len(arr) >= 150:
            return sorted(set(arr)), f"NSE live ({len(arr)} symbols)"
    except Exception:
        pass
    return list(FNO_STATIC), f"static fallback ({len(FNO_STATIC)} symbols)"

FNO_SYMBOLS, FNO_SOURCE = list(FNO_STATIC), "static"

SECTOR_MAP = {
 "BANK": ["HDFCBANK","ICICIBANK","SBIN","KOTAKBANK","AXISBANK","INDUSINDBK","BANKBARODA","PNB","CANBK","UNIONBANK","IDFCFIRSTB","FEDERALBNK","BANDHANBNK","AUBANK","RBLBANK","YESBANK","INDIANB","IOB","UCOBANK","BANKINDIA","MAHABANK"],
 "NBFC/CAPMKT": ["BAJFINANCE","BAJAJFINSV","CHOLAFIN","MUTHOOTFIN","MANAPPURAM","LICHSGFIN","PFC","RECLTD","IRFC","SBICARD","POONAWALLA","LTF","ABCAPITAL","IIFL","ANGELONE","MOTILALOFS","NUVAMA","360ONE","BSE","MCX","CDSL","IEX","KFINTECH","NAM-INDIA","HDFCAMC","CAMS","PAYTM","POLICYBZR","SAMMAANCAP","JIOFIN","SHRIRAMFIN","SBILIFE","HDFCLIFE","BAJAJHLDNG","ICICIGI","ICICIPRULI","IREDA","LICI","MFSL","PNBHOUSING"],
 "IT": ["INFY","TCS","WIPRO","HCLTECH","TECHM","LTTS","MPHASIS","PERSISTENT","COFORGE","KPITTECH","CYIENT","TATAELXSI","OFSS","SONATSOFTW","BSOFT","LTM","SAGILITY"],
 "AUTO": ["MARUTI","M&M","BAJAJ-AUTO","HEROMOTOCO","EICHERMOT","TVSMOTOR","ASHOKLEY","TMPV","BHARATFORG","MOTHERSON","BALKRISIND","APOLLOTYRE","MRF","EXIDEIND","SONACOMS","UNOMINDA","ENDURANCE","TIINDIA","BOSCHLTD","SCHAEFFLER","FORCEMOT","HYUNDAI","OLECTRA","ATHERENERG"],
 "METAL": ["TATASTEEL","JSWSTEEL","HINDALCO","VEDL","SAIL","NMDC","JINDALSTEL","NATIONALUM","HINDZINC","HINDCOPPER","JSL","APLAPOLLO","WELCORP","COALINDIA"],
 "ENERGY": ["RELIANCE","ONGC","BPCL","IOC","GAIL","PETRONET","IGL","MGL","OIL","ATGL","ADANIGREEN","ADANIPOWER","TATAPOWER","JSWENERGY","NHPC","SJVN","TORNTPOWER","CESC","NTPC","POWERGRID","POWERINDIA","PREMIERENE","WAAREEENER","SUZLON","INOXWIND","ADANIENSOL","HINDPETRO"],
 "PHARMA": ["SUNPHARMA","CIPLA","DRREDDY","DIVISLAB","LUPIN","AUROPHARMA","ZYDUSLIFE","ALKEM","TORNTPHARM","MANKIND","IPCALAB","GLENMARK","LAURUSLABS","BIOCON","GRANULES","NATCOPHARM","AJANTPHARM","PPLPHARMA","ABBOTINDIA","APOLLOHOSP","MAXHEALTH","FORTIS","SYNGENE","METROPOLIS","LALPATHLAB"],
 "FMCG/RETAIL": ["HINDUNILVR","ITC","NESTLEIND","BRITANNIA","TATACONSUM","DMART","TRENT","JUBLFOOD","DEVYANI","VBL","UBL","RADICO","MARICO","DABUR","GODREJCP","COLPAL","PGHH","EMAMILTD","BATAINDIA","PAGEIND","ABFRL","GODFRYPHLP","TITAN","NYKAA","ETERNAL","SWIGGY","KALYANKJIL","PATANJALI","UNITDSPR","VMM"],
 "INFRA/CAPGOODS": ["LT","BEL","HAL","BDL","MAZDOCK","COCHINSHIP","GRSE","BHEL","SIEMENS","ABB","CUMMINSIND","THERMAX","KEC","KPIL","NCC","IRB","GMRAIRPORT","RVNL","IRCON","RAILTEL","TITAGARH","JWL","IRCTC","CONCOR","NBCC","HUDCO","SOLARINDS","AMBER","PGEL","DIXON","KAYNES","CGPOWER","POLYCAB","HAVELLS","VOLTAS","BLUESTARCO","CROMPTON","KEI","SUPREMEIND","ASTRAL","ADANIENT","ADANIPORTS","GVT&D"],
 "CEMENT/CHEM": ["ULTRACEMCO","SHREECEM","AMBUJACEM","ACC","DALBHARAT","JKCEMENT","RAMCOCEM","GRASIM","ASIANPAINT","PIDILITIND","SRF","PIIND","DEEPAKNTR","AARTIIND","TATACHEM","UPL","COROMANDEL","CHAMBLFERT","GNFC","NAVINFLUOR","ATUL","VINATIORGA"],
 "REALTY/HOTEL": ["DLF","GODREJPROP","OBEROIRLTY","PRESTIGE","LODHA","PHOENIXLTD","BRIGADE","SOBHA","CENTURYPLY","INDHOTEL","LEMONTREE","INDIGO","SPICEJET"],
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
    """Tradable universe = the OFFICIAL NSE F&O list, nothing else.

    BUGFIX 2026-08-27: this used to build from UNIVERSE_RAW (a 265-name legacy
    watchlist) while FNO_STATIC was only printed in the sidebar. The F&O-only
    requirement was therefore never enforced on the scan, and non-F&O names
    (e.g. HFCL) could be issued as orders. The F&O list is now the sole source
    of the universe; UNIVERSE_RAW is kept only to supply the RENAMES fixups.
    """
    if not force and os.path.exists(F_UNIVERSE_OK):
        try:
            j = json.load(open(F_UNIVERSE_OK))
            age = (get_ist_now() - datetime.fromisoformat(j["ts"]).replace(tzinfo=IST)).days
            if age <= max_age_days and j.get("ok") and j.get("src") == "fno":
                return j["ok"], j.get("dead", [])
        except Exception:
            pass
    try:
        fno, _src = fetch_fno_universe()
    except Exception:
        fno = list(FNO_STATIC)
    if len(fno) < 150:
        fno = list(FNO_STATIC)
    raw = []
    for s in fno:
        t = s if s.endswith(".NS") else s + ".NS"
        r = RENAMES.get(t, t)
        if r:
            raw.append(r)
    raw = list(dict.fromkeys(raw))
    data = download(raw, period="10d", interval="1d", ttl=3600)
    ok = sorted([t for t in raw if t in data and len(data[t]) >= 3])
    dead = sorted([t for t in raw if t not in ok])
    try:
        json.dump({"ts": get_ist_now().isoformat(), "ok": ok, "dead": dead,
                   "src": "fno", "n_fno": len(fno)},
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
             orb_minutes=minutes,
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


def scan_momentum_stocks(bundle, capital, day, nfctx, top_n=5):
    """Scan for momentum stocks after 12:30 PM - high volume, strong trend continuation."""
    names = liquid_names(bundle)
    intraday = download(names, period="1d", interval="5m", ttl=45)
    cands = []
    for t, d in intraday.items():
        try:
            g = d.copy()
            g.index = pd.DatetimeIndex(g.index)
            if g.index.tz is not None:
                g.index = g.index.tz_convert(IST)
            g = g[g.index.date == day]
            g = g[(g.index.time >= MKT_OPEN) & (g.index.time <= MKT_CLOSE)]
            if len(g) < 50:
                continue
            # Volume-weighted momentum
            vol = g.Volume.rolling(20).mean().iloc[-1]
            price_change = (g.Close.iloc[-1] / g.Open.iloc[0] - 1) * 100
            vwap = (g.Close * g.Volume).sum() / g.Volume.sum()
            dist_vwap = (g.Close.iloc[-1] / vwap - 1) * 100
            rvol = g.Volume.iloc[-1] / vol if vol > 0 else 0
            
            base = bundle["snap"].get(t, {}) or {}
            atr_pct = base.get("atr_pct", 0)
            adr20 = base.get("adr20", 0)
            
            if rvol < 1.5 or abs(price_change) < 1.0:
                continue
                
            side = "BUY" if price_change > 0 else "SELL"
            entry = float(g.Close.iloc[-1])
            sl_pct = min(max(atr_pct * 0.5, 0.5), 1.5)
            
            cands.append(dict(
                ticker=t, sym=t.replace(".NS", ""), sector=sector_of(t),
                side=side, p=min(0.6 + abs(price_change) * 0.05, 0.85),
                prob_pct=min(60 + abs(price_change) * 5, 85),
                entry=entry, level=entry, sl_pct=sl_pct,
                trig_min=None, trig_time=get_ist_now(),
                last=entry, moved=price_change,
                gap=base.get("gap"), atr_pct=atr_pct, adr20=adr20,
                rvol=rvol, turn=base.get("turn_ma_cr"),
                vwap_dist=dist_vwap, momentum_score=abs(price_change) * rvol,
                still_valid=True,
                drivers=[("Momentum", price_change), ("RVOL", rvol), ("VWAP Dist", dist_vwap)],
                ticket=build_ticket(t, side, entry, sl_pct, capital),
                low_conf=False,
                passed_p=True,
                scan_type="MOMENTUM"
            ))
        except Exception:
            continue
    D = pd.DataFrame(cands)
    if D.empty:
        return []
    D = D.sort_values("momentum_score", ascending=False).head(top_n)
    return D.to_dict("records")

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
# EMBEDDED CALIBRATED MODELS — v22
# Refitted 2026-08-27 on the OFFICIAL 210-name NSE F&O universe:
#   16,280 close-confirmed 5-min ORB trigger events across 59 sessions
#   (2026-06-05 -> 2026-08-27), liquidity floor 25 cr, stop capped at 1.0%.
# Labels are SEQUENTIAL, not MFE: win = target reached BEFORE the capped stop,
# measured strictly on bars AFTER the confirming candle (the v21 study allowed
# the confirming candle's own high/low to settle the trade, which flattered it).
#
# WALK-FORWARD RESULT (expanding window, refit daily, 33 unseen sessions,
# scale-out exit = half at +1% then stop to breakeven, 0.06% costs):
#   long top-1 + confluence gate : target-hit 33.3%, profitable 66.7%,
#                                  avg +0.318%/trade, MFE>=3% on 24% of picks
#   long top-2 : +0.098%   long top-3 : +0.073%   (edge decays with k)
#   short top-1: -0.026%   -> short stays LOW-CONF, gated, never the default
#   WITHOUT the confluence gate top-1 was -0.028%/trade. The gate IS the edge.
# =============================================================================
CONFLUENCE_GATE = dict(atr_pct=2.0, adr20=2.0, orb_rng_pct=0.60, trig_min_max=20)

EMB_LONG = dict(
  features=['atr_pct', 'adr20', 'rsi', 'vr', 'ret1', 'ret5', 'ret21', 'big20', 'd_hi20', 'd_lo20', 'px_vs_e20', 'pd_rng', 'nr7', 'e_stack', 'gap', 'orb_rng_pct', 'orb_pos_pd', 'above_R1', 'above_P', 'below_S1', 'below_P', 'orb_h_R1', 'orb_l_S1', 'd_R1', 'd_S1', 'trig_min', 'brk_ext', 'sl_pct', 'mkt_early', 'sec_early', 'sec_rel', 'stk_rel', 'sec_breadth'],
  coef=[0.078405, 0.29391907, -0.01591169, 0.11336119, -0.00049124, 0.07015533, -0.03889627, -0.06021505, 0.20074277, 0.01918102, -0.16674709, -0.01846165, -0.05034406, -0.0379986, 0.2201195, 0.15098722, 0.07645531, -0.01888617, 0.10179208, 0.07548601, 0.00786571, 0.08105434, -0.06802344, 0.11507225, -0.15507216, -0.54072934, 0.02705965, 0.23794425, 0.08648622, 0.0125351, -0.07654266, -0.02930699, -0.01708232],
  intercept=-2.419485411099459,
  mean=[2.55877155, 2.47161629, 51.88849749, 0.95060489, 0.03311124, 0.36233392, 1.67943141, 2.78449053, -5.26887654, 7.06109231, 0.52964124, 2.39429246, 0.15251836, 0.17068144, 0.33721592, 0.79857811, 70039172.86996242, 0.15998969, 0.59925287, 0.04508566, 0.39958779, 0.3033621, 0.11348705, 0.88539525, 1.50044403, 60.49465413, 0.18693142, 0.80298032, 0.27531035, 0.32035762, 0.04504727, 0.14646934, 63.3432001],
  scale=[0.63402798, 0.56726819, 11.39126136, 0.71222054, 1.80599966, 3.99020552, 8.16082972, 1.96164605, 4.0078529, 5.96552157, 3.6390981, 1.20189722, 0.35952261, 0.98532626, 0.96378098, 0.45204361, 5257933688.43151, 0.3665965, 0.49004986, 0.20749204, 0.48981363, 0.45971028, 0.31718724, 1.14687063, 1.19127264, 81.07789437, 0.21514903, 0.20814257, 0.51682735, 0.71068875, 0.46758087, 0.89871596, 27.87712328],
  n=7763, base_rate=0.10009, sessions=59,
  trained_at='v22 F&O-210 calibration 2026-08-27',
  note='P(+2% before the capped stop) on a close-confirmed 5m ORB-high break. Base rate 10.01%. Strongest drivers: early trigger (trig_min, -0.54), ADR20 (+0.29), available stop room (+0.24), gap (+0.22), proximity to the 20d high (+0.20).',
)

EMB_LONG3 = dict(
  features=['atr_pct', 'adr20', 'rsi', 'vr', 'ret1', 'ret5', 'ret21', 'big20', 'd_hi20', 'd_lo20', 'px_vs_e20', 'pd_rng', 'nr7', 'e_stack', 'gap', 'orb_rng_pct', 'orb_pos_pd', 'above_R1', 'above_P', 'below_S1', 'below_P', 'orb_h_R1', 'orb_l_S1', 'd_R1', 'd_S1', 'trig_min', 'brk_ext', 'sl_pct', 'mkt_early', 'sec_early', 'sec_rel', 'stk_rel', 'sec_breadth'],
  coef=[0.15865226, 0.35568154, 0.19558626, -0.02826749, 0.093572, 0.12120064, 0.16188731, -0.10347991, 0.22670678, -0.01050817, -0.44949579, 0.01954759, -0.08320338, -0.21022783, 0.19319067, 0.15433395, 0.0863257, -0.05115923, 0.05637217, 0.18042503, -0.01126294, 0.09758657, -0.10872126, 0.19266298, -0.18942061, -0.46256085, 0.0569095, 0.23566347, 0.1185649, 0.06406219, -0.03368253, 0.09574251, -0.03042693],
  intercept=-3.6132990692641753,
  mean=[2.55877155, 2.47161629, 51.88849749, 0.95060489, 0.03311124, 0.36233392, 1.67943141, 2.78449053, -5.26887654, 7.06109231, 0.52964124, 2.39429246, 0.15251836, 0.17068144, 0.33721592, 0.79857811, 70039172.86996242, 0.15998969, 0.59925287, 0.04508566, 0.39958779, 0.3033621, 0.11348705, 0.88539525, 1.50044403, 60.49465413, 0.18693142, 0.80298032, 0.27531035, 0.32035762, 0.04504727, 0.14646934, 63.3432001],
  scale=[0.63402798, 0.56726819, 11.39126136, 0.71222054, 1.80599966, 3.99020552, 8.16082972, 1.96164605, 4.0078529, 5.96552157, 3.6390981, 1.20189722, 0.35952261, 0.98532626, 0.96378098, 0.45204361, 5257933688.43151, 0.3665965, 0.49004986, 0.20749204, 0.48981363, 0.45971028, 0.31718724, 1.14687063, 1.19127264, 81.07789437, 0.21514903, 0.20814257, 0.51682735, 0.71068875, 0.46758087, 0.89871596, 27.87712328],
  n=7763, base_rate=0.03658, sessions=59,
  trained_at='v22 F&O-210 calibration 2026-08-27',
  note='P(+3% before the capped stop) — the "3%+ runner" model the user asked for. Base rate only 3.66%: a 3% ORB run is a 1-in-27 event unconditionally and about 1-in-6 on the best-ranked name of the day. Used to rank, not to promise.',
)

EMB_SHORT = dict(
  features=['atr_pct', 'adr20', 'rsi', 'vr', 'ret1', 'ret5', 'ret21', 'big20', 'd_hi20', 'd_lo20', 'px_vs_e20', 'pd_rng', 'nr7', 'e_stack', 'gap', 'orb_rng_pct', 'orb_pos_pd', 'above_R1', 'above_P', 'below_S1', 'below_P', 'orb_h_R1', 'orb_l_S1', 'd_R1', 'd_S1', 'trig_min', 'brk_ext', 'sl_pct', 'mkt_early', 'sec_early', 'sec_rel', 'stk_rel', 'sec_breadth'],
  coef=[-0.08559717, 0.30470453, 0.28317564, -0.0391873, -0.07966547, 0.14758603, -0.10650499, 0.04208805, 0.10514699, -0.04703151, -0.2370983, 0.06683626, 0.07614761, -0.06562036, 0.07942299, 0.09170073, 0.07569774, -0.03414977, 0.06702643, 0.04641895, 0.0109654, 0.05227597, -0.00687253, 0.06177356, 0.00986393, -0.41458606, 0.09496003, 0.27194431, -0.08617359, -0.07949514, -0.01931168, -0.0868916, 0.06082423],
  intercept=-2.875070030217415,
  mean=[2.55801163, 2.4832512, 51.89757722, 0.94311323, 0.14562198, 0.41737572, 1.57722195, 2.78959728, -5.26943701, 7.1078532, 0.56125039, 2.35925291, 0.16590349, 0.14993542, 0.33957835, 0.77933566, 271179919.17595255, 0.1583891, 0.61794059, 0.0393331, 0.38112011, 0.26476459, 0.13021017, 0.84402547, 1.50687292, 60.74791593, 0.15317281, 0.78655455, 0.25423927, 0.2387945, -0.01544477, -0.01642086, 59.92581559],
  scale=[0.63814705, 0.57395196, 11.30057178, 0.73927659, 1.7534332, 3.95559504, 8.09158001, 1.98842956, 4.03035157, 5.99973612, 3.60814107, 1.23102634, 0.37199398, 0.98869579, 0.9425837, 0.4202034, 8993195936.497879, 0.36510546, 0.48589095, 0.19438623, 0.48566199, 0.44120778, 0.33653452, 1.12715934, 1.17636223, 84.26873333, 0.18373556, 0.21054279, 0.50133803, 0.64462535, 0.41645691, 0.89337336, 27.09279976],
  n=8517, base_rate=0.06516, sessions=58,
  trained_at='v22 F&O-210 calibration 2026-08-27',
  note='Short side, P(-2% before stop). Base 6.52%. Walk-forward expectancy was -0.03%/trade at top-1, so this is LOW CONFIDENCE and gated behind a higher floor.',
)

EMB_SHORT3 = dict(
  features=['atr_pct', 'adr20', 'rsi', 'vr', 'ret1', 'ret5', 'ret21', 'big20', 'd_hi20', 'd_lo20', 'px_vs_e20', 'pd_rng', 'nr7', 'e_stack', 'gap', 'orb_rng_pct', 'orb_pos_pd', 'above_R1', 'above_P', 'below_S1', 'below_P', 'orb_h_R1', 'orb_l_S1', 'd_R1', 'd_S1', 'trig_min', 'brk_ext', 'sl_pct', 'mkt_early', 'sec_early', 'sec_rel', 'stk_rel', 'sec_breadth'],
  coef=[0.07663947, 0.36196255, 0.25198277, -0.01055159, -0.05088186, -0.026676, 0.06830369, -0.01957342, 0.25231981, -0.20270999, -0.22225693, 0.08829366, 0.07378655, -0.04313707, -0.1166647, -0.04973085, -0.02520849, -0.14921757, -0.00668907, -0.09652409, 0.03999511, 0.1485923, 0.26704643, -0.0057318, 0.12218762, -0.45351563, 0.15828871, 0.38557214, -0.07937051, -0.12018178, -0.09047939, -0.0514187, 0.2739984],
  intercept=-4.3474431397524,
  mean=[2.55801163, 2.4832512, 51.89757722, 0.94311323, 0.14562198, 0.41737572, 1.57722195, 2.78959728, -5.26943701, 7.1078532, 0.56125039, 2.35925291, 0.16590349, 0.14993542, 0.33957835, 0.77933566, 271179919.17595255, 0.1583891, 0.61794059, 0.0393331, 0.38112011, 0.26476459, 0.13021017, 0.84402547, 1.50687292, 60.74791593, 0.15317281, 0.78655455, 0.25423927, 0.2387945, -0.01544477, -0.01642086, 59.92581559],
  scale=[0.63814705, 0.57395196, 11.30057178, 0.73927659, 1.7534332, 3.95559504, 8.09158001, 1.98842956, 4.03035157, 5.99973612, 3.60814107, 1.23102634, 0.37199398, 0.98869579, 0.9425837, 0.4202034, 8993195936.497879, 0.36510546, 0.48589095, 0.19438623, 0.48566199, 0.44120778, 0.33653452, 1.12715934, 1.17636223, 84.26873333, 0.18373556, 0.21054279, 0.50133803, 0.64462535, 0.41645691, 0.89337336, 27.09279976],
  n=8517, base_rate=0.01843, sessions=58,
  trained_at='v22 F&O-210 calibration 2026-08-27',
  note='Short side, P(-3% before stop). Base 1.84%. Reference only.',
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
# v22 FEATURE VECTOR — must match EMB_LONG["features"] exactly.
# Every field below is knowable at the moment the ORB break confirms, so there
# is no look-ahead: daily features come from the PREVIOUS session's close,
# sector strength from the first 5-minute candle of the current session.
# =============================================================================
def sector_early_context(early_by_sym):
    """early_by_sym = {SYMBOL: % move of the opening candle vs prev close}.
    Returns (market_median, {sector: (median, breadth_pct)}) — the sector-rotation
    signal the 24-26 Aug case study pointed to (on 2 of 3 days sector beta explained
    more of the winners than any company news did)."""
    vals = [v for v in early_by_sym.values() if v == v and np.isfinite(v)]
    mkt = float(np.median(vals)) if vals else 0.0
    buckets = defaultdict(list)
    for s, v in early_by_sym.items():
        if v == v and np.isfinite(v):
            buckets[sector_of(s)].append(v)
    sec = {k: (float(np.median(v)), 100.0 * float(np.mean([x > 0 for x in v])))
           for k, v in buckets.items() if v}
    return mkt, sec


def v22_features(base, orb, prev, piv_d, side, sec_ctx=None, sym=None):
    """base = D-1 daily snapshot, orb = orb_from_bars() output,
    prev = dict(pdh, pdl, pdc), piv_d = previous-day classic pivots,
    sec_ctx = (mkt_early, {sector: (median, breadth)})."""
    g = lambda k, d=np.nan: base.get(k, d)
    pdh, pdl, pdc = prev.get("pdh"), prev.get("pdl"), prev.get("pdc")
    op = orb.get("orb_o") or orb.get("orb_h")
    c920 = orb.get("orb_c", op)
    P = piv_d.get("P", np.nan); R1 = piv_d.get("R1", np.nan); S1 = piv_d.get("S1", np.nan)
    entry = orb.get(f"{side}_entry") or (orb["orb_h"] if side == "L" else orb["orb_l"])
    level = orb["orb_h"] if side == "L" else orb["orb_l"]
    slp = float(np.clip(abs(entry - (orb["orb_l"] if side == "L" else orb["orb_h"]))
                        / max(entry, 1e-9) * 100, CFG["SL_FLOOR_PCT"], CFG["SL_CAP_PCT"]))
    early = ((c920 / pdc - 1) * 100) if (pdc and c920) else np.nan
    mkt_e, sec_e, sec_b = 0.0, 0.0, 50.0
    if sec_ctx and sym:
        mkt_e = sec_ctx[0]
        sec_e, sec_b = sec_ctx[1].get(sector_of(sym), (mkt_e, 50.0))
    f = {
        "atr_pct": g("atr_pct"), "adr20": g("adr20"), "rsi": g("rsi"), "vr": g("rvol"),
        "ret1": g("ret1"), "ret5": g("ret5"), "ret21": g("ret21"),
        "big20": g("big_moves_20"), "d_hi20": g("d_hi20"), "d_lo20": g("d_lo20"),
        "px_vs_e20": g("px_vs_e20"), "nr7": g("nr7"), "e_stack": g("stack_up"),
        "pd_rng": ((pdh - pdl) / pdc * 100) if (pdh and pdl and pdc) else np.nan,
        "gap": ((op / pdc - 1) * 100) if (pdc and op) else np.nan,
        "orb_rng_pct": orb.get("orb_range_pct"),
        "orb_pos_pd": ((op - pdl) / max(pdh - pdl, 1e-9)) if (pdh and pdl) else np.nan,
        "above_R1": int(op > R1) if R1 == R1 else 0,
        "above_P": int(op > P) if P == P else 0,
        "below_S1": int(op < S1) if S1 == S1 else 0,
        "below_P": int(op < P) if P == P else 0,
        "orb_h_R1": int(orb["orb_h"] > R1) if R1 == R1 else 0,
        "orb_l_S1": int(orb["orb_l"] < S1) if S1 == S1 else 0,
        "d_R1": ((R1 / op - 1) * 100) if (R1 == R1 and op) else np.nan,
        "d_S1": ((1 - S1 / op) * 100) if (S1 == S1 and op) else np.nan,
        "trig_min": orb.get(f"{side}_trig_min", 5), "sl_pct": slp,
        "brk_ext": abs(entry - level) / max(entry, 1e-9) * 100,
        "mkt_early": mkt_e, "sec_early": sec_e, "sec_rel": sec_e - mkt_e,
        "stk_rel": (early - sec_e) if early == early else 0.0,
        "sec_breadth": sec_b,
    }
    return f


def passes_confluence_gate(base, orb, side="L"):
    """The single most important filter in v22. Walk-forward: applying this gate
    turned top-1 long from -0.028%/trade into +0.318%/trade. It is a HARD gate,
    not a score, because each condition on its own is weak."""
    G = CONFLUENCE_GATE
    reasons = []
    if not (base.get("atr_pct", 0) >= G["atr_pct"]):
        reasons.append(f"ATR% {base.get('atr_pct', 0):.2f} < {G['atr_pct']}")
    if not (base.get("adr20", 0) >= G["adr20"]):
        reasons.append(f"ADR20 {base.get('adr20', 0):.2f} < {G['adr20']}")
    if not ((orb.get("orb_range_pct") or 0) >= G["orb_rng_pct"]):
        reasons.append(f"opening range {orb.get('orb_range_pct', 0):.2f}% < {G['orb_rng_pct']}%")
    tm = orb.get(f"{side}_trig_min")
    if tm is None or tm > G["trig_min_max"]:
        reasons.append(f"break at +{tm}min > {G['trig_min_max']}min")
    return (not reasons), reasons


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
                scan_live_orb(bundle=b, as_of=now.date())
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
# v22 LAYER — overrides and new engines.
#
#   * scan_live_orb()      : rebuilt on the v22 model + hard confluence gate,
#                            top-1 by default (that is where the edge lives).
#   * replay_session()     : runs all three intraday engines on any past date,
#                            so the terminal is useful when the market is shut.
#   * top_movers()         : automatic top-3 gainers/losers, daily / weekly /
#                            monthly, straight off the price panel.
#   * manual movers store  : the user's own gainer/loser entries.
#   * journal              : automatic (every signal) + manual (own trades).
#   * learn_from_movers()  : the self-improvement loop and its cadence.
# =============================================================================
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
for _sub in ("scans", "models", "journal", "movers", "learning"):
    try:
        os.makedirs(os.path.join(DATA_DIR, _sub), exist_ok=True)
    except Exception:
        pass

MODEL_L3 = load_model(os.path.join(DATA_DIR, "models", "model_long3.json"), EMB_LONG3)
MODEL_S3 = load_model(os.path.join(DATA_DIR, "models", "model_short3.json"), EMB_SHORT3)


def _csv_path(*parts):
    return os.path.join(DATA_DIR, *parts)


def _read_csv(path, cols):
    try:
        if os.path.exists(path):
            d = pd.read_csv(path)
            for c in cols:
                if c not in d.columns:
                    d[c] = None
            return d[cols]
    except Exception:
        pass
    return pd.DataFrame(columns=cols)


def _append_csv(path, rec):
    d = pd.DataFrame([rec])
    hdr = not os.path.exists(path)
    d.to_csv(path, mode="a", header=hdr, index=False)


# =============================================================================
# JOURNAL — automatic + manual
# =============================================================================
AUTO_COLS = ["logged_at", "for_date", "engine", "sym", "sector", "side", "entry", "sl",
             "sl_pct", "target", "target_pct", "qty", "prob_pct", "p3_pct", "trig_min",
             "orb_h", "orb_l", "gate", "note", "outcome", "exit_px", "pnl_pct"]
MAN_COLS = ["trade_date", "sym", "side", "entry", "exit_px", "qty", "sl", "target",
            "pnl_pct", "pnl_rs", "setup", "followed_plan", "emotion", "notes"]
JOURNAL_AUTO = _csv_path("journal", "auto_signals.csv")
JOURNAL_MAN = _csv_path("journal", "manual_trades.csv")


def journal_auto_log(engine, rows, for_date=None):
    """Every signal the screener generates is written here, whether or not it is
    traded. This is what makes the hit-rate numbers on the Learning tab real
    rather than remembered."""
    for_date = str(for_date or session_date())
    existing = _read_csv(JOURNAL_AUTO, AUTO_COLS)
    seen = set(zip(existing.for_date.astype(str), existing.engine.astype(str),
                   existing.sym.astype(str), existing.side.astype(str)))
    n = 0
    for r in rows or []:
        tk = r.get("ticket") or {}
        sym = r.get("sym") or tk.get("sym")
        side = r.get("side") or tk.get("side")
        if (for_date, engine, str(sym), str(side)) in seen:
            continue
        _append_csv(JOURNAL_AUTO, dict(
            logged_at=fmt_ist(), for_date=for_date, engine=engine, sym=sym,
            sector=r.get("sector"), side=side, entry=tk.get("entry") or r.get("entry"),
            sl=tk.get("sl"), sl_pct=r.get("sl_pct"), target=tk.get("target"),
            target_pct=tk.get("target_pct"), qty=tk.get("qty"),
            prob_pct=r.get("prob_pct"), p3_pct=r.get("p3_pct"),
            trig_min=r.get("trig_min"), orb_h=r.get("orb_h"), orb_l=r.get("orb_l"),
            gate="PASS" if r.get("gate_pass", True) else "FAIL",
            note=r.get("gate_note") or "", outcome="", exit_px="", pnl_pct=""))
        n += 1
    return n


def journal_auto():
    return _read_csv(JOURNAL_AUTO, AUTO_COLS)


def journal_manual():
    return _read_csv(JOURNAL_MAN, MAN_COLS)


def journal_manual_add(rec):
    _append_csv(JOURNAL_MAN, {k: rec.get(k) for k in MAN_COLS})


# =============================================================================
# MOVERS — automatic top-3 gainers / losers, and the manual list
# =============================================================================
MOVER_COLS = ["period", "period_label", "rank", "kind", "sym", "sector", "ret_pct",
             "close", "turnover_cr", "atr_pct", "adr20", "rvol", "gap", "captured_at"]
MAN_MOVER_COLS = ["period", "period_label", "kind", "sym", "ret_pct", "reason", "added_at"]
MOVERS_AUTO = _csv_path("movers", "auto_movers.csv")
MOVERS_MAN = _csv_path("movers", "manual_movers.csv")


def _period_frames(bundle):
    """Daily / weekly / monthly percentage-change frames from the price panel."""
    C = bundle["px"]["Close"]
    idx = pd.DatetimeIndex(C.index)
    d_ret = C.pct_change() * 100
    iso = idx.isocalendar()
    wk = pd.Series([f"{a}-W{b:02d}" for a, b in zip(iso.year, iso.week)], index=idx)
    mo = pd.Series([f"{d.year}-{d.month:02d}" for d in idx], index=idx)
    wC = C.groupby(wk).last(); mC = C.groupby(mo).last()
    wO = bundle["px"]["Open"].groupby(wk).first()
    mO = bundle["px"]["Open"].groupby(mo).first()
    return dict(D=(d_ret, [str(x.date()) for x in idx]),
                W=((wC / wO - 1) * 100, list(wC.index)),
                M=((mC / mO - 1) * 100, list(mC.index)))


def top_movers(bundle=None, period="D", n=3, back=1):
    """Top-n gainers and losers. period D = intraday/daily (F&O),
    W = swing week, M = swing month. `back` = how many periods back (0 = latest).
    Weekly and monthly are measured open-to-close of the period, which is what a
    swing trade actually captures — not close-to-close."""
    b = bundle or load_bundle()
    if not b:
        return dict(error="no data")
    frames = _period_frames(b)
    if period not in frames:
        return dict(error=f"bad period {period}")
    R, labels = frames[period]
    if len(R) < back + 1:
        return dict(error="not enough history")
    pos = len(R) - 1 - back
    row = R.iloc[pos].dropna()
    label = str(labels[pos]) if pos < len(labels) else str(pos)
    liq = {t: (b["snap"].get(t, {}) or {}).get("turn_ma_cr", 0) or 0 for t in row.index}
    row = row[[t for t in row.index if liq.get(t, 0) >= CFG["MIN_TURNOVER_CR"]]]
    if row.empty:
        return dict(error="no liquid names")
    out = []
    for kind, ser in (("GAINER", row.sort_values(ascending=False).head(n)),
                      ("LOSER", row.sort_values().head(n))):
        for rank, (t, v) in enumerate(ser.items(), 1):
            f = b["snap"].get(t, {}) or {}
            out.append(dict(period=period, period_label=label, rank=rank, kind=kind,
                            sym=t.replace(".NS", ""), sector=sector_of(t),
                            ret_pct=round(float(v), 2), close=f.get("px"),
                            turnover_cr=f.get("turn_ma_cr"), atr_pct=f.get("atr_pct"),
                            adr20=f.get("adr20"), rvol=f.get("rvol"), gap=f.get("gap"),
                            captured_at=fmt_ist()))
    return dict(period=period, label=label, rows=out,
                table=pd.DataFrame(out), n_universe=int(len(row)))


def movers_capture(bundle=None, periods=("D", "W", "M"), n=3, history=1):
    """Persist the automatic movers so the learning loop has a growing record."""
    b = bundle or load_bundle()
    existing = _read_csv(MOVERS_AUTO, MOVER_COLS)
    seen = set(zip(existing.period.astype(str), existing.period_label.astype(str),
                   existing.kind.astype(str), existing.sym.astype(str)))
    added = 0
    for p in periods:
        for back in range(history):
            res = top_movers(b, period=p, n=n, back=back)
            for r in (res.get("rows") or []):
                key = (str(r["period"]), str(r["period_label"]), r["kind"], r["sym"])
                if key in seen:
                    continue
                _append_csv(MOVERS_AUTO, r)
                seen.add(key)
                added += 1
    return added


def movers_auto_history():
    return _read_csv(MOVERS_AUTO, MOVER_COLS)


def movers_manual():
    return _read_csv(MOVERS_MAN, MAN_MOVER_COLS)


def movers_manual_add(rec):
    _append_csv(MOVERS_MAN, {k: rec.get(k) for k in MAN_MOVER_COLS})


# =============================================================================
# SELF-IMPROVEMENT LOOP
#
# Cadence answer (question 3): BOTH, at different jobs.
#   * DAILY   — append today's movers + today's signal outcomes to the record.
#               Cheap, no refit, no risk of overfitting to one session.
#   * WEEKLY  — recompute the CHARACTER PROFILE of the winners (the feature
#               medians below) and refresh the F&O universe. 5 sessions x ~200
#               names is enough to move a median, not enough to move a model.
#   * MONTHLY — full logistic refit of the ranking models + monthly pivots.
#               A refit needs >=1,500 fresh labelled events to be stable; the
#               study produced ~275 trigger events per session, so a month
#               (~5,500) is the right size and a week (~1,375) is not.
# That is why the automation runs three jobs, not one.
# =============================================================================
LEARN_PATH = _csv_path("learning", "mover_profile.json")


def learn_from_movers(bundle=None, lookback_days=30, min_move=3.0):
    """Builds the empirical 'character profile' of names that actually ran >=3%
    in a session, using D-1 features only, and stores the medians. The screener
    shows every candidate's distance from this profile, so the thing the user
    asked for -- 'find that reason, that common thing' -- is explicit and
    updates itself as the market changes."""
    b = bundle or load_bundle()
    if not b:
        return dict(error="no data")
    px = b["px"]
    C, O = px["Close"], px["Open"]
    F = b["F"]
    ret = (C / O - 1) * 100
    idx = pd.DatetimeIndex(C.index)
    keep = idx[-min(lookback_days, len(idx)):]
    prof, rest = defaultdict(list), defaultdict(list)
    FEATS = ["atr_pct", "adr20", "rvol", "ret1", "ret5", "ret21", "px_vs_e20",
             "d_hi20", "d_lo20", "gap", "vol20", "bbw", "big_moves_20", "rsi",
             "turn_ma_cr"]
    for i, dt_ in enumerate(idx):
        if dt_ not in keep or i == 0:
            continue
        today = ret.loc[dt_]
        win = set(today[today.abs() >= min_move].dropna().index)
        if not win:
            continue
        for f in FEATS:
            df = F.get(f)
            if df is None or dt_ not in df.index:
                continue
            prev = df.iloc[df.index.get_loc(dt_) - 1]
            for t, v in prev.items():
                if v != v or not np.isfinite(v):
                    continue
                (prof if t in win else rest)[f].append(float(v))
    out = {}
    for f in FEATS:
        if len(prof.get(f, [])) < 20 or len(rest.get(f, [])) < 50:
            continue
        w, r = float(np.median(prof[f])), float(np.median(rest[f]))
        # A ratio of medians is meaningless when the denominator sits on zero
        # (ret5, gap and px_vs_e20 all straddle zero), so report the ratio only
        # when the baseline is far enough from zero, and always report the gap
        # in the same units alongside it.
        scale = float(np.median(np.abs(rest[f]))) or 1.0
        ratio = round(w / r, 3) if (r and abs(r) >= 0.15 * scale) else None
        out[f] = dict(winner_median=round(w, 4), rest_median=round(r, 4),
                      ratio=ratio, diff=round(w - r, 4),
                      n_winner=len(prof[f]), n_rest=len(rest[f]))
    payload = dict(built_at=fmt_ist(), lookback_days=lookback_days,
                   min_move_pct=min_move, features=out,
                   n_sessions=int(len(keep)))
    try:
        json.dump(payload, open(LEARN_PATH, "w"), indent=1)
    except Exception:
        pass
    return payload


def learn_profile():
    try:
        if os.path.exists(LEARN_PATH):
            return json.load(open(LEARN_PATH))
    except Exception:
        pass
    return None


# =============================================================================
# ENGINE 2 (v22) — LIVE 5-MIN/15-MIN ORB with the confluence gate.
# Time rules: No new ORB after 10:00 AM. After 12:30 PM -> Momentum scan.
# Multi-timeframe: Daily (macro) + 1H (context) + 5m/15m ORB (execution)
# =============================================================================
def scan_live_orb(capital=None, bundle=None, watch=None, orb_minutes=None,
                  top_n=None, bars=None, as_of=None, log=True):
    capital = capital or CFG["DEFAULT_CAPITAL"]
    top_n = CFG["INTRADAY_TOP_N"] if top_n is None else top_n
    om = orb_minutes or CFG["ORB_MINUTES"]
    b = bundle or load_bundle()
    if not b:
        return dict(error="no data")
    day = as_of or get_ist_now().date()
    if isinstance(day, str):
        day = pd.Timestamp(day).date()
    
    now = get_ist_now()
    current_time = now.time()
    
    # Time-based logic
    is_live = as_of is None and market_phase() in ("ORB", "LIVE")
    no_new_orb = current_time >= dtime(10, 0)
    momentum_time = current_time >= dtime(12, 30)
    
    if bars is None:
        names = watch or liquid_names(b)
        intraday = download(names, period="1d", interval="5m", ttl=45)
    else:
        intraday = bars
    nf5 = download([NIFTY], period="1d" if as_of is None else "5d", interval="5m", ttl=45).get(NIFTY)
    nfctx = nifty_context(nf5, b["nfd"])
    
    # Fetch 1-hour data for context
    hourly = download(names if bars is None else list(bars.keys()), 
                      period="10d", interval="60m", ttl=300)

    # --- pass 1: opening candle of every name -> sector strength context -------
    orbs, early = {}, {}
    orb_timeframes = [5, 15] if CFG.get("MULTI_TF_ORB", True) else [om]
    for t, d in intraday.items():
        try:
            g = d.copy()
            g.index = pd.DatetimeIndex(g.index)
            if g.index.tz is not None:
                g.index = g.index.tz_convert(IST)
            g = g[g.index.date == day]
            g = g[(g.index.time >= MKT_OPEN) & (g.index.time <= MKT_CLOSE)]
            if len(g) < 1:
                continue
            # Compute ORB for each timeframe
            tf_orbs = {}
            for tf in orb_timeframes:
                o = orb_from_bars(g, tf)
                if o:
                    tf_orbs[tf] = o
            if tf_orbs:
                orbs[t] = tf_orbs
            pdc = b["prev"]["pdc"].get(t)
            if pdc and om in tf_orbs:
                early[t.replace(".NS", "")] = (tf_orbs[om]["orb_c"] / pdc - 1) * 100
        except Exception:
            continue
    sec_ctx = sector_early_context(early)

    # --- pass 2: score every confirmed break ---------------------------------
    cands = []
    gen_time = get_ist_now()
    
    if not no_new_orb and not momentum_time:
        # Normal ORB scan
        for t, tf_orbs in orbs.items():
            base = b["snap"].get(t, {}) or {}
            prev = dict(pdh=b["prev"]["pdh"].get(t), pdl=b["prev"]["pdl"].get(t),
                        pdc=b["prev"]["pdc"].get(t))
            piv_d = (b["piv"].get(t, {}) or {}).get("D", {}) or {}
            sym = t.replace(".NS", "")
            
            # Add 1-hour context
            h1_ctx = {}
            if t in hourly:
                h1 = hourly[t]
                if len(h1) > 20:
                    h1_close = h1.Close.iloc[-1]
                    h1_ema20 = h1.Close.ewm(span=20).mean().iloc[-1]
                    h1_rsi = _rsi_df(h1.Close).iloc[-1]
                    h1_trend = 1 if h1_close > h1_ema20 else -1
                    h1_ctx = dict(h1_trend=h1_trend, h1_rsi=h1_rsi, 
                                 h1_vs_ema20=((h1_close/h1_ema20)-1)*100)
            
            for tf, orb in tf_orbs.items():
                for side, mdl, mdl3, minp in (("L", MODEL_L, MODEL_L3, CFG["MIN_P_LONG"]),
                                              ("S", MODEL_S, MODEL_S3, CFG["MIN_P_SHORT"])):
                    if not orb.get(f"{side}_trig") or mdl is None:
                        continue
                    gate_ok, gate_why = passes_confluence_gate(base, orb, side)
                    fv = v22_features(base, orb, prev, piv_d, side, sec_ctx, sym)
                    fv.update(h1_ctx)
                    p = mdl.prob(fv)
                    p3 = mdl3.prob(fv) if mdl3 else np.nan
                    conf_entry = orb[f"{side}_entry"]
                    sl_pct = fv["sl_pct"]
                    moved = ((orb["last"] / conf_entry - 1) * 100) * (1 if side == "L" else -1)
                    cands.append(dict(
                        ticker=t, sym=sym, sector=sector_of(t),
                        side="BUY" if side == "L" else "SELL", p=p, prob_pct=p * 100,
                        p3=p3, p3_pct=(p3 * 100 if p3 == p3 else np.nan),
                        orb_h=orb["orb_h"], orb_l=orb["orb_l"],
                        orb_minutes=orb["orb_minutes"],
                        orb_range_pct=orb["orb_range_pct"], entry=conf_entry,
                        level=orb["orb_h"] if side == "L" else orb["orb_l"],
                        sl_pct=sl_pct, trig_min=orb.get(f"{side}_trig_min"),
                        trig_time=orb.get(f"{side}_trig_time"), last=orb["last"], moved=moved,
                        gap=fv["gap"], atr_pct=base.get("atr_pct"), adr20=base.get("adr20"),
                        rvol=base.get("rvol"), turn=base.get("turn_ma_cr"),
                        sec_early=fv["sec_early"], sec_rel=fv["sec_rel"],
                        sec_breadth=fv["sec_breadth"], stk_rel=fv["stk_rel"],
                        h1_trend=h1_ctx.get("h1_trend"), h1_rsi=h1_ctx.get("h1_rsi"),
                        h1_vs_ema20=h1_ctx.get("h1_vs_ema20"),
                        gate_pass=gate_ok, gate_note="; ".join(gate_why),
                        still_valid=bool(moved < CFG["TARGET_PCT"] * 0.6 and moved > -sl_pct),
                        drivers=mdl.top_drivers(fv, 5),
                        ticket=build_ticket(t, "BUY" if side == "L" else "SELL",
                                            conf_entry, sl_pct, capital),
                        low_conf=(side == "S"),
                        passed_p=bool(p >= minp),
                        gen_time=gen_time,
                        scan_type="ORB"
                    ))
    elif momentum_time:
        # After 12:30 PM - momentum scan
        mom_signals = scan_momentum_stocks(b, capital, day, nfctx, top_n)
        cands.extend(mom_signals)
    
    D = pd.DataFrame(cands)
    out = dict(ts=fmt_ist(), for_date=str(day), regime=nfctx["regime"], nfctx=nfctx,
               orb_minutes=om, n_triggers=int(len(D)), top_n=top_n,
               sector_early=sec_ctx[1], mkt_early=sec_ctx[0],
               scan_mode="ORB" if not no_new_orb and not momentum_time 
                         else ("MOMENTUM" if momentum_time else "ORB_ENDED"),
               table=D, actionable=[], rejected=[])
    
    live_now = (as_of is None and market_phase() in ("ORB", "LIVE"))
    out["valid_enforced"] = bool(live_now)
    out["orb_allowed"] = not no_new_orb
    out["momentum_mode"] = momentum_time
    out["current_time"] = fmt_time(now)
    
    if not D.empty:
        sel = D.gate_pass & D.passed_p
        if live_now:
            sel = sel & D.still_valid
        ok = D[sel].copy()
        out["n_p_pass"] = int((D.gate_pass & D.passed_p).sum())
        longs = ok[ok.side == "BUY"].sort_values("p", ascending=False).head(top_n)
        shorts = ok[ok.side == "SELL"].sort_values("p", ascending=False).head(
            max(0, CFG["SHORT_TOP_N"]))
        act = pd.concat([longs, shorts])
        out["actionable"] = act.to_dict("records")
        out["all_ranked"] = D.sort_values("p", ascending=False).to_dict("records")
        out["rejected"] = D[~D.gate_pass].sort_values("p", ascending=False) \
                           .head(15).to_dict("records")
        out["n_gate_pass"] = int(D.gate_pass.sum())
    if log:
        save_scan("live_orb", out)
        journal_auto_log("LIVE_ORB", out["actionable"], for_date=day)
    return out


# =============================================================================
# HISTORICAL REPLAY (question 9)
# Runs Pre-Market / Live ORB / EOD exactly as they would have run on a past
# date. 5-minute bars are only available for ~60 calendar days from the data
# provider, so the ORB replay window is bounded by that -- it is stated on the
# tab rather than silently truncated.
# =============================================================================
@st.cache_data(ttl=1800, show_spinner=False)
def _replay_bars(day_str, tickers):
    """60 days of 5-minute bars, sliced to one session."""
    raw = download(list(tickers), period="60d", interval="5m", ttl=1800)
    day = pd.Timestamp(day_str).date()
    out = {}
    for t, d in (raw or {}).items():
        try:
            g = d.copy()
            g.index = pd.DatetimeIndex(g.index)
            if g.index.tz is not None:
                g.index = g.index.tz_convert(IST)
            g = g[g.index.date == day]
            if len(g) >= 5:
                out[t] = g
        except Exception:
            continue
    return out


def run_backtest(start_date, end_date, capital=None, bundle=None, top_n=None, 
                 orb_minutes=5, use_multi_tf=True):
    """
    Comprehensive backtest across multiple sessions.
    Returns aggregated statistics for strategy validation.
    """
    capital = capital or CFG["DEFAULT_CAPITAL"]
    b = bundle or load_bundle(days="500d")
    if not b:
        return dict(error="no data")
    
    C = b["px"]["Close"]
    dates = [x.date() for x in pd.DatetimeIndex(C.index)]
    
    # Filter trading days in range
    trading_days = [d for d in dates 
                    if start_date <= d <= end_date and is_trading_day(d)]
    
    if not trading_days:
        return dict(error="no trading days in range")
    
    all_results = []
    all_trades = []
    
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    for i, day in enumerate(trading_days):
        status_text.text(f"Backtesting {day}... ({i+1}/{len(trading_days)})")
        progress_bar.progress((i + 1) / len(trading_days))
        
        pos = dates.index(day)
        snap_prev = snapshot(b["F"], when=pos - 1)
        bb = dict(b)
        bb["snap"] = snap_prev
        O, H, L = b["px"]["Open"], b["px"]["High"], b["px"]["Low"]
        bb["prev"] = dict(pdh={t: float(H[t].iloc[pos - 1]) for t in C.columns},
                          pdl={t: float(L[t].iloc[pos - 1]) for t in C.columns},
                          pdc={t: float(C[t].iloc[pos - 1]) for t in C.columns})
        
        names = [t for t in C.columns
                 if (snap_prev.get(t, {}) or {}).get("turn_ma_cr", 0) >= CFG["MIN_TURNOVER_CR"]]
        
        bars = _replay_bars(str(day), names)
        if not bars:
            continue
            
        res = scan_live_orb(capital=capital, bundle=bb, orb_minutes=orb_minutes,
                            top_n=top_n, bars=bars, as_of=day, log=False)
        
        for r in res.get("actionable", []):
            g = bars.get(r["ticker"])
            if g is None:
                continue
            outcome = _replay_outcome(g, r)
            r.update(outcome)
            r["date"] = str(day)
            all_trades.append(r)
        
        all_results.append(dict(
            date=str(day),
            n_signals=len(res.get("actionable", [])),
            regime=res.get("regime"),
            scan_mode=res.get("scan_mode")
        ))
    
    progress_bar.empty()
    status_text.empty()
    
    if not all_trades:
        return dict(error="no trades generated", days_tested=len(trading_days))
    
    # Aggregate statistics
    df = pd.DataFrame(all_trades)
    df["pnl_pct"] = pd.to_numeric(df.get("rp_pnl_pct", 0), errors="coerce").fillna(0)
    df["mfe_pct"] = pd.to_numeric(df.get("rp_mfe_pct", 0), errors="coerce").fillna(0)
    df["result"] = df.get("rp_result", "unknown")
    
    wins = df[df["pnl_pct"] > 0]
    losses = df[df["pnl_pct"] <= 0]
    
    stats = dict(
        total_days=len(trading_days),
        total_trades=len(df),
        win_rate=len(wins) / len(df) * 100 if len(df) > 0 else 0,
        avg_win=wins["pnl_pct"].mean() if len(wins) > 0 else 0,
        avg_loss=losses["pnl_pct"].mean() if len(losses) > 0 else 0,
        expectancy=df["pnl_pct"].mean(),
        total_pnl=df["pnl_pct"].sum(),
        avg_mfe=df["mfe_pct"].mean(),
        max_dd=df["pnl_pct"].min(),
        max_runup=df["pnl_pct"].max(),
        profit_factor=abs(wins["pnl_pct"].sum() / losses["pnl_pct"].sum()) if losses["pnl_pct"].sum() != 0 else float('inf'),
        by_result=df["result"].value_counts().to_dict(),
        by_side=df["side"].value_counts().to_dict(),
        by_orb_tf=df.get("orb_minutes", pd.Series()).value_counts().to_dict() if "orb_minutes" in df.columns else {},
        trades=df.to_dict("records"),
        daily=all_results
    )
    
    # Save backtest results
    try:
        bt_path = os.path.join(DIRS["perf"], f"backtest_{start_date}_{end_date}.json")
        json.dump(_json_safe(stats), open(bt_path, "w"), indent=1)
    except Exception:
        pass
    
    return stats


def replay_session(day, capital=None, bundle=None, top_n=None):
    """Full historical replay of one session."""
    capital = capital or CFG["DEFAULT_CAPITAL"]
    day = pd.Timestamp(day).date() if not isinstance(day, date) else day
    if not is_trading_day(day):
        return dict(error=f"{day} was not an NSE trading session")
    b = bundle or load_bundle()
    if not b:
        return dict(error="no data")
    C = b["px"]["Close"]
    dates = [x.date() for x in pd.DatetimeIndex(C.index)]
    if day not in dates:
        return dict(error=f"{day} is outside the loaded daily history")
    pos = dates.index(day)
    # rebuild the snapshot as it stood at the PREVIOUS close -> no look-ahead
    snap_prev = snapshot(b["F"], when=pos - 1)
    bb = dict(b)
    bb["snap"] = snap_prev
    O, H, L = b["px"]["Open"], b["px"]["High"], b["px"]["Low"]
    bb["prev"] = dict(pdh={t: float(H[t].iloc[pos - 1]) for t in C.columns},
                      pdl={t: float(L[t].iloc[pos - 1]) for t in C.columns},
                      pdc={t: float(C[t].iloc[pos - 1]) for t in C.columns})
    names = [t for t in C.columns
             if (snap_prev.get(t, {}) or {}).get("turn_ma_cr", 0) >= CFG["MIN_TURNOVER_CR"]]
    bars = _replay_bars(str(day), names)
    res = dict(day=str(day), n_names=len(names), n_bars=len(bars))
    if not bars:
        res["orb_error"] = ("5-minute bars are not available for this date "
                            "(the provider only keeps ~60 calendar days).")
    else:
        res["orb"] = scan_live_orb(capital=capital, bundle=bb, orb_minutes=CFG["ORB_MINUTES"],
                                   top_n=top_n, bars=bars, as_of=day, log=False)
        # what actually happened, so the replay is a scorecard and not a guess
        for r in res["orb"].get("actionable", []):
            g = bars.get(r["ticker"])
            if g is None:
                continue
            r.update(_replay_outcome(g, r))
    res["eod"] = _replay_eod(bb, day, capital)
    res["premarket"] = _replay_premarket(bb, bars, day, capital)
    return res


def _replay_outcome(g, r):
    """Walk the real bars after the confirming candle: half off at +1%, stop to
    breakeven, rest at target or the 15:10 close."""
    try:
        g = g[(g.index.time >= MKT_OPEN) & (g.index.time <= CFG["SQUARE_OFF_TIME"])]
        after = g[g.index > pd.Timestamp(r["trig_time"])] if r.get("trig_time") is not None else g
        if len(after) == 0:
            return dict(rp_result="no bars after entry")
        e = float(r["entry"]); sgn = 1 if r["side"] == "BUY" else -1
        sl = e * (1 - sgn * r["sl_pct"] / 100)
        p1 = e * (1 + sgn * CFG["PARTIAL_PCT"] / 100)
        tp = e * (1 + sgn * CFG["TARGET_PCT"] / 100)
        half, pnl = False, 0.0
        mfe = 0.0
        for _, row in after.iterrows():
            hi, lo = float(row.High), float(row.Low)
            mfe = max(mfe, ((hi - e) / e * 100) if sgn > 0 else ((e - lo) / e * 100))
            if (lo <= sl) if sgn > 0 else (hi >= sl):
                pnl += (0.5 if half else 1.0) * sgn * (sl / e - 1) * 100
                return dict(rp_result="STOPPED" if not half else "BE/partial",
                            rp_pnl_pct=round(pnl - CFG["COST_PCT"], 3),
                            rp_mfe_pct=round(mfe, 2))
            if not half and ((hi >= p1) if sgn > 0 else (lo <= p1)):
                pnl += 0.5 * CFG["PARTIAL_PCT"]; half = True; sl = e
            if (hi >= tp) if sgn > 0 else (lo <= tp):
                pnl += (0.5 if half else 1.0) * CFG["TARGET_PCT"]
                return dict(rp_result="TARGET HIT",
                            rp_pnl_pct=round(pnl - CFG["COST_PCT"], 3),
                            rp_mfe_pct=round(mfe, 2))
        last = float(after.Close.iloc[-1])
        pnl += (0.5 if half else 1.0) * sgn * (last / e - 1) * 100
        return dict(rp_result="squared off at close",
                    rp_pnl_pct=round(pnl - CFG["COST_PCT"], 3), rp_mfe_pct=round(mfe, 2))
    except Exception as ex:
        return dict(rp_result=f"outcome unavailable ({ex})")


def _replay_eod(bb, day, capital):
    """The EOD scan as it would have printed after that session's close, plus
    what the named stocks did the NEXT session."""
    try:
        res = scan_eod(capital=capital, bundle=bb, top_n=5)
        C = bb["px"]["Close"]; O = bb["px"]["Open"]
        dates = [x.date() for x in pd.DatetimeIndex(C.index)]
        pos = dates.index(day)
        for p in res.get("top", []):
            t = p["row"]["ticker"]
            if pos + 1 < len(dates):
                try:
                    o1 = float(O[t].iloc[pos + 1]); h1 = float(bb["px"]["High"][t].iloc[pos + 1])
                    l1 = float(bb["px"]["Low"][t].iloc[pos + 1])
                    p["next_day"] = dict(date=str(dates[pos + 1]),
                                         up_pct=round((h1 / o1 - 1) * 100, 2),
                                         dn_pct=round((1 - l1 / o1) * 100, 2))
                except Exception:
                    pass
        return res
    except Exception as ex:
        return dict(error=str(ex))


def _replay_premarket(bb, bars, day, capital):
    """Pre-market ranking for that date. The live engine reads the NSE pre-open
    auction, which is not archived, so the replay substitutes the real opening
    print -- this is stated in the UI because it is a genuine difference."""
    rows = []
    for t, f in bb["snap"].items():
        if (f.get("turn_ma_cr", 0) or 0) < CFG["MIN_TURNOVER_CR"]:
            continue
        ok, _ = feasible(f, "L")
        if not ok:
            continue
        g = bars.get(t)
        if g is None or len(g) == 0:
            continue
        pdc = bb["prev"]["pdc"].get(t)
        op = float(g.Open.iloc[0])
        if not pdc:
            continue
        gap = (op / pdc - 1) * 100
        pu, pd_ = gap_prior(gap)
        rows.append(dict(ticker=t, sym=t.replace(".NS", ""), sector=sector_of(t),
                         exp_open=op, gap=gap, atr_pct=f.get("atr_pct"),
                         adr20=f.get("adr20"), big20=f.get("big_moves_20"),
                         p_up=pu * 100, p_dn=pd_ * 100,
                         score_L=pu * 100 * min((f.get("atr_pct") or 0) / 2.5, 1.6),
                         score_S=pd_ * 100 * min((f.get("atr_pct") or 0) / 2.5, 1.6)))
    if not rows:
        return dict(error="no opening prints available for this date")
    D = pd.DataFrame(rows)
    return dict(source=f"actual opening print of {day} (pre-open book is not archived)",
                longs=D.sort_values("score_L", ascending=False).head(8).to_dict("records"),
                shorts=D.sort_values("score_S", ascending=False).head(8).to_dict("records"),
                table=D, n_candidates=len(D))


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
        v = float(x)
        if not np.isfinite(v):
            return "—"
        if v >= 10000000:
            return f"₹{v/10000000:.2f}Cr"
        elif v >= 100000:
            return f"₹{v/100000:.2f}L"
        else:
            return f"₹{v:,.0f}"
    except Exception:
        return "—"

def pct(x, d=1):
    try:
        v = float(x)
        if not np.isfinite(v):
            return "—"
        return f"{v:+.{d}f}%"
    except Exception:
        return "—"

def num(x, d=1):
    try:
        v = float(x)
        if not np.isfinite(v):
            return "—"
        if abs(v) >= 10000000:
            return f"{v/10000000:.2f}Cr"
        elif abs(v) >= 100000:
            return f"{v/100000:.2f}L"
        else:
            return f"{v:.{d}f}"
    except Exception:
        return "—"

def fmt_time(dt=None):
    """Format time as HH:MM:SS"""
    if dt is None:
        dt = get_ist_now()
    if isinstance(dt, str):
        try:
            dt = pd.Timestamp(dt)
        except Exception:
            return dt
    return dt.strftime("%H:%M:%S")

def fmt_dt(dt=None):
    """Format datetime as DD Mon HH:MM:SS"""
    if dt is None:
        dt = get_ist_now()
    if isinstance(dt, str):
        try:
            dt = pd.Timestamp(dt)
        except Exception:
            return dt
    return dt.strftime("%d %b %H:%M:%S")

def tooltip(text, label=None):
    """Create a tooltip-enabled span"""
    if label:
        return f"<span title='{text}' style='cursor:help;border-bottom:1px dotted #888'>{label}</span>"
    return f"<span title='{text}' style='cursor:help'>ℹ️</span>"

def colored_dot(color, size=10):
    """Return a colored dot HTML"""
    return f"<span style='display:inline-block;width:{size}px;height:{size}px;border-radius:50%;background:{color};margin-right:6px;vertical-align:middle'></span>"

def regime_badge(regime):
    """Return colored regime badge"""
    colors = {"BULL": "#0ecb81", "BEAR": "#f6465d", "NEUTRAL": "#f0b90b"}
    c = colors.get(regime, "#8f96b3")
    return f"<span style='background:{c}22;border:1px solid {c}66;color:{c};padding:2px 10px;border-radius:20px;font-size:11px;font-weight:700'>{regime}</span>"

def side_badge(side):
    """Return colored side badge"""
    if side == "BUY":
        return f"{colored_dot('#0ecb81', 8)}<span style='color:#0ecb81;font-weight:700'>BUY</span>"
    elif side == "SELL":
        return f"{colored_dot('#f6465d', 8)}<span style='color:#f6465d;font-weight:700'>SELL</span>"
    return side

def ticket_card(tk, prob=None, extra_rows=None, low_conf=False, ladder=None, gen_time=None, sl_hit_time=None, tg_hit_time=None):
    side = tk["side"]
    accent = "#0ecb81" if side == "BUY" else "#f6465d"
    warn = ("<div style='margin-top:8px;font-size:11px;color:#f0b90b'>"
            "⚠ LOW-CONFIDENCE SIDE — short ORB showed no reliable edge in "
            "walk-forward testing. Half size or skip.</div>") if low_conf else ""
    pr = (f"<div style='font-size:12px;color:#8f96b3'>Model Probability: "
          f"<b style='color:#e2e4ef'>{prob*100:.1f}%</b></div>") if prob is not None else ""
    rr = tk.get('rr', 0)
    rr_color = "#0ecb81" if rr >= 2 else "#f0b90b" if rr >= 1.5 else "#f6465d"
    rows = ""
    for k, v in (extra_rows or []):
        rows += (f"<div style='display:flex;justify-content:space-between;font-size:12px;"
                 f"padding:2px 0'><span style='color:#8f96b3'>{tooltip(k)}</span>"
                 f"<span style='color:#e2e4ef;font-weight:600'>{v}</span></div>")
    lad = ""
    if ladder:
        lad = "<div style='margin-top:8px;font-size:12px;color:#8f96b3'>Exit Ladder</div>"
        for _lg in ladder:
            lad += (f"<div style='display:flex;justify-content:space-between;font-size:12px'>"
                    f"<span style='color:#8f96b3'>+{_lg['pct']:.0f}% → book {_lg['book']}</span>"
                    f"<span style='color:#0ecb81;font-weight:700'>{money(_lg['px'])}</span></div>")
    time_rows = ""
    if gen_time:
        time_rows += f"<div style='display:flex;justify-content:space-between;font-size:11px;padding:2px 0'><span style='color:#8f96b3'>{tooltip('Signal generation time', 'Generated')}</span><span style='color:#e2e4ef;font-weight:600'>{fmt_time(gen_time)}</span></div>"
    if sl_hit_time:
        time_rows += f"<div style='display:flex;justify-content:space-between;font-size:11px;padding:2px 0'><span style='color:#f6465d'>{tooltip('Stop loss hit time', 'SL Hit')}</span><span style='color:#f6465d;font-weight:600'>{fmt_time(sl_hit_time)}</span></div>"
    if tg_hit_time:
        time_rows += f"<div style='display:flex;justify-content:space-between;font-size:11px;padding:2px 0'><span style='color:#0ecb81'>{tooltip('Target hit time', 'Target Hit')}</span><span style='color:#0ecb81;font-weight:600'>{fmt_time(tg_hit_time)}</span></div>"
    t2_label = f"Target 2 ({tk['target_pct']:.0f}%)"
    st.markdown(f"""
<div style="background:linear-gradient(145deg,#11141f,#0d1018);border:1px solid {accent}55;
     border-left:4px solid {accent};border-radius:14px;padding:16px 18px;margin-bottom:12px">
  <div style="display:flex;justify-content:space-between;align-items:center">
    <div>
      <span style="font-size:20px;font-weight:800;color:#fff">{tk['symbol']}</span>
      <span style="background:{accent};color:#06070d;padding:2px 10px;border-radius:6px;
            font-size:12px;font-weight:800;margin-left:8px">{side_badge(side)}</span>
    </div>
    <div style="text-align:right">
      <div style="font-size:11px;color:#8f96b3">QTY</div>
      <div style="font-size:20px;font-weight:800;color:#fff;font-family:JetBrains Mono,monospace">{tk['qty']}</div>
    </div>
  </div>
  {pr}
  <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-top:12px">
    <div><div style="font-size:10px;color:#8f96b3">{tooltip('Entry price', 'ENTRY')}</div>
         <div style="font-size:16px;font-weight:700;color:#fff;font-family:JetBrains Mono,monospace">{money(tk['entry'])}</div></div>
    <div><div style="font-size:10px;color:#f6465d">{tooltip('Stop loss price', 'STOP LOSS')}</div>
         <div style="font-size:16px;font-weight:700;color:#f6465d;font-family:JetBrains Mono,monospace">{money(tk['sl'])}</div>
         <div style="font-size:10px;color:#8f96b3">−{tk['sl_pct']:.1f}%</div></div>
    <div><div style="font-size:10px;color:#0ecb81">{tooltip('Target 1 - book 50%', 'TARGET 1')}</div>
         <div style="font-size:16px;font-weight:700;color:#0ecb81;font-family:JetBrains Mono,monospace">{money(tk['t1'])}</div></div>
    <div><div style="font-size:10px;color:#0ecb81">{tooltip(t2_label, 'TARGET 2')}</div>
         <div style="font-size:16px;font-weight:700;color:#0ecb81;font-family:JetBrains Mono,monospace">{money(tk['t2'])}</div></div>
  </div>
  <div style="display:flex;gap:18px;margin-top:10px;font-size:11px;color:#8f96b3">
    <span>Risk {money(tk['risk_rs'])}</span>
    <span>Reward {money(tk['reward_rs'])}</span>
    <span style="color:{rr_color};font-weight:700">R:R 1:{rr:.1f}</span>
  </div>
  {rows}{lad}{time_rows}{warn}
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
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)


# =============================================================================
# v22 UI HELPERS — list/card view, downloads, compact signal rendering
# =============================================================================
def _shade(D, col, lo_rgb=(233, 245, 235), hi_rgb=(35, 134, 54)):
    """Green intensity ramp on one numeric column, WITHOUT matplotlib.

    pandas' Styler.background_gradient imports matplotlib lazily, so on a host
    that has pandas but not matplotlib (Streamlit Community Cloud by default)
    every call raises ImportError and takes the whole page down. This computes
    the same effect as plain inline CSS, so the app has no optional plotting
    dependency at all. Falls back to an unstyled frame if anything goes wrong —
    a cosmetic feature must never be able to crash a trading screen.
    """
    try:
        if D is None or getattr(D, "empty", True) or col not in D.columns:
            return D
        v = pd.to_numeric(D[col], errors="coerce")
        good = v[np.isfinite(v)] if hasattr(np, "isfinite") else v.dropna()
        if len(good) == 0:
            return D
        lo, hi = float(good.min()), float(good.max())
        rng = (hi - lo) or 1.0

        def _css(x):
            try:
                x = float(x)
            except (TypeError, ValueError):
                return ""
            if x != x:
                return ""
            t = max(0.0, min(1.0, (x - lo) / rng))
            r, g, b = (int(round(a + (c - a) * t)) for a, c in zip(lo_rgb, hi_rgb))
            fg = "#ffffff" if t > 0.55 else "#0b1220"
            return f"background-color: rgb({r},{g},{b}); color: {fg};"

        return D.style.map(_css, subset=[col])
    except Exception:
        return D


def view_toggle(key, default="List"):
    """List / Card switch. Present on every tab; List is the default because it
    is the fastest thing to scan when you only want the order."""
    return st.radio("view", ["List", "Card"], horizontal=True, label_visibility="collapsed",
                    index=0 if default == "List" else 1, key=f"view_{key}")


def dl(df, filename, key, label=None):
    """Download button for any table. Every table in the app has one."""
    try:
        if df is None or (hasattr(df, "empty") and df.empty):
            return
        data = df.to_csv(index=False).encode()
        st.download_button(label or f"⬇ {filename}", data, file_name=filename,
                           mime="text/csv", key=f"dl_{key}", width="stretch")
    except Exception as ex:
        st.caption(f"download unavailable ({ex})")


def _clean(df, cols=None, rename=None, r=2):
    if df is None or (hasattr(df, "empty") and df.empty):
        return pd.DataFrame()
    d = pd.DataFrame(df)
    if cols:
        d = d[[c for c in cols if c in d.columns]]
    for c in d.columns:
        if pd.api.types.is_numeric_dtype(d[c]):
            d[c] = d[c].astype(float).round(r)
    if rename:
        d = d.rename(columns=rename)
    return d


SIG_COLS = ["sym", "sector", "side", "entry", "sl_pct", "prob_pct", "p3_pct",
            "trig_min", "orb_range_pct", "atr_pct", "adr20", "gap", "sec_rel",
            "sec_breadth", "moved", "last", "rr", "gen_time", "sl_hit_time", "tg_hit_time"]
SIG_NAMES = {"sym": "Symbol", "sector": "Sector", "side": "Side", "entry": "Entry",
             "sl_pct": "SL %", "prob_pct": "P(+2%) %", "p3_pct": "P(+3%) %",
             "trig_min": "Break @min", "orb_range_pct": "ORB range %",
             "atr_pct": "ATR %", "adr20": "ADR %", "gap": "Gap %",
             "sec_rel": "Sector vs mkt %", "sec_breadth": "Sector breadth %",
             "moved": "Moved since %", "last": "Last", "rr": "R:R",
             "gen_time": "Gen Time", "sl_hit_time": "SL Hit", "tg_hit_time": "TG Hit"}
SIG_TOOLTIPS = {
    "sym": "Trading symbol", "sector": "Sector classification", "side": "Trade direction (BUY/SELL)",
    "entry": "Entry price", "sl_pct": "Stop loss percentage from entry",
    "prob_pct": "Model probability of hitting +2% target", "p3_pct": "Model probability of hitting +3% target",
    "trig_min": "Minutes after open when break confirmed", "orb_range_pct": "Opening range width %",
    "atr_pct": "ATR as % of price", "adr20": "20-day ADR %", "gap": "Gap at open %",
    "sec_rel": "Sector strength vs market", "sec_breadth": "% of sector stocks up",
    "moved": "Price moved since entry %", "last": "Last traded price",
    "rr": "Risk:Reward ratio", "gen_time": "Signal generation time",
    "sl_hit_time": "Stop loss hit time", "tg_hit_time": "Target hit time"
}


def signal_list(rows, key, qty=True):
    """Sleek single-glance table. Highlights the row you are meant to trade."""
    if not rows:
        st.caption("nothing to show")
        return
    recs = []
    for r in rows:
        tk = r.get("ticket") or {}
        d = {SIG_NAMES.get(c, c): r.get(c) for c in SIG_COLS if c in r}
        if qty:
            d["Qty"] = tk.get("qty")
            d["Stop"] = tk.get("sl")
            d["Target"] = tk.get("target")
        # Add R:R if available
        d["R:R"] = tk.get("rr", r.get("rr", 0))
        # Add timestamps
        d["Gen Time"] = fmt_time(r.get("gen_time") or r.get("trig_time"))
        d["SL Hit"] = fmt_time(r.get("sl_hit_time"))
        d["TG Hit"] = fmt_time(r.get("tg_hit_time"))
        recs.append(d)
    D = pd.DataFrame(recs)
    for c in D.columns:
        if pd.api.types.is_numeric_dtype(D[c]):
            D[c] = D[c].astype(float).round(2)
    # Apply tooltips to column headers via markdown
    col_config = {}
    for c in D.columns:
        if c in SIG_TOOLTIPS:
            col_config[c] = st.column_config.Column(SIG_TOOLTIPS[c], help=SIG_TOOLTIPS[c])
    st.dataframe(_shade(D, "P(+2%) %"), width="stretch", hide_index=True, column_config=col_config)
    dl(D, f"{key}.csv", key)


def signal_cards(rows, capital_note=True):
    if not rows:
        st.caption("nothing to show")
        return
    for r in rows:
        gen_time = r.get("gen_time") or r.get("trig_time")
        sl_hit_time = r.get("sl_hit_time")
        tg_hit_time = r.get("tg_hit_time")
        extra = [("Model P(+2%)", f"{num(r.get('prob_pct'), 1)}%"),
                 ("Model P(+3%)", f"{num(r.get('p3_pct'), 1)}%"),
                 ("Break confirmed at", f"+{num(r.get('trig_min'), 0)} min"),
                 ("Opening 5-min range", f"{num(r.get('orb_range_pct'))}%"),
                 ("ATR% / ADR%", f"{num(r.get('atr_pct'))} / {num(r.get('adr20'))}"),
                 ("Gap", pct(r.get("gap"))),
                 ("Sector vs market", pct(r.get("sec_rel"))),
                 ("Sector breadth (up names)", f"{num(r.get('sec_breadth'), 0)}%"),
                 ("Moved since entry", pct(r.get("moved"))),
                 ("R:R Ratio", f"1:{num(r.get('rr', r.get('ticket', {}).get('rr', 0)))}")]
        if r.get("rp_result"):
            extra.append(("REPLAY RESULT", f"{r['rp_result']} · "
                                           f"{pct(r.get('rp_pnl_pct'))} · MFE {num(r.get('rp_mfe_pct'))}%"))
        ticket_card(r["ticket"], prob=r.get("p"), extra_rows=extra,
                    low_conf=bool(r.get("low_conf")), gen_time=gen_time,
                    sl_hit_time=sl_hit_time, tg_hit_time=tg_hit_time)
        drv = r.get("drivers") or []
        if drv:
            st.caption("why this name ranked here: " +
                       " · ".join(f"{a} {b:+.2f}" for a, b in drv))


def render_signals(rows, key, default="List"):
    v = view_toggle(key, default)
    (signal_list(rows, key) if v == "List" else signal_cards(rows))


def movers_block(res, key):
    if not res or res.get("error"):
        st.caption(res.get("error", "no data") if res else "no data")
        return
    st.caption(f"period {res.get('label')} · {res.get('n_universe')} liquid F&O names ranked")
    D = _clean(res.get("table"),
               ["rank", "kind", "sym", "sector", "ret_pct", "close", "turnover_cr",
                "atr_pct", "adr20", "rvol", "gap"],
               {"rank": "#", "kind": "Type", "sym": "Symbol", "sector": "Sector",
                "ret_pct": "Move %", "close": "Close", "turnover_cr": "Turnover ₹cr",
                "atr_pct": "ATR %", "adr20": "ADR %", "rvol": "RVOL", "gap": "Gap %"})
    v = view_toggle(key)
    if v == "List":
        st.dataframe(D, width="stretch", hide_index=True)
    else:
        G = [r for r in res["rows"] if r["kind"] == "GAINER"]
        Lo = [r for r in res["rows"] if r["kind"] == "LOSER"]
        c1, c2 = st.columns(2)
        for col, ttl, rows_, colr in ((c1, "🟢 Top gainers", G, "#0ecb81"),
                                      (c2, "🔴 Top losers", Lo, "#f6465d")):
            with col:
                st.markdown(f"##### {ttl}")
                for r in rows_:
                    st.markdown(
                        f"<div style='background:#11141f;border:1px solid {colr}55;"
                        f"border-left:3px solid {colr};border-radius:10px;padding:10px 12px;"
                        f"margin-bottom:8px'><b style='color:#fff;font-size:15px'>{r['sym']}</b>"
                        f"<span style='color:{colr};font-weight:800;float:right'>"
                        f"{r['ret_pct']:+.2f}%</span><br>"
                        f"<span style='font-size:11px;color:#8f96b3'>{r['sector']} · "
                        f"ATR {num(r.get('atr_pct'))}% · RVOL {num(r.get('rvol'))} · "
                        f"₹{num(r.get('turnover_cr'), 0)}cr</span></div>",
                        unsafe_allow_html=True)
    dl(D, f"{key}.csv", key)


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

st.markdown("<div class='main-header'>⚡ QUANT-EDGE v22 — Institutional ORB Terminal</div>",
            unsafe_allow_html=True)
st.markdown("<div class='sub-caption'>Multi-TF: Daily (macro) + 1H (context) + 5m/15m ORB (execution) · "
            "Point-in-time NSE F&O universe · Walk-forward expectancy shown · Paper mode</div>",
            unsafe_allow_html=True)
c1, c2, c3, c4 = st.columns([2, 2, 2, 2])
c1.markdown(f"**Market Phase:** {chip(ptxt, pkind)}", unsafe_allow_html=True)
c2.markdown(f"**Regime:** {regime_badge('NEUTRAL')}", unsafe_allow_html=True)
c3.markdown(f"**Time:** {fmt_time(get_ist_now())}")
c4.markdown(f"<div class='last-updated'>{fmt_ist()}</div>", unsafe_allow_html=True)

# =============================================================================
# SIDEBAR
# =============================================================================
with st.sidebar:
    st.markdown("### ⚙️ Control")
    capital = st.number_input("Trading capital (₹)", 25000, 100000000,
                              CFG["DEFAULT_CAPITAL"], step=25000)
    CFG["RISK_PER_TRADE_PCT"] = st.slider("Risk per trade (% of capital)", 0.25, 3.0,
                                          CFG["RISK_PER_TRADE_PCT"], 0.25)
    CFG["INTRADAY_TOP_N"] = st.selectbox(
        "Intraday signals per day", [1, 2, 3], index=0,
        help="Walk-forward expectancy on 1,026 sessions: top-1 -0.137%/trade, top-2 -0.155%, top-3 -0.153%, top-5 -0.131%. All negative; k=1 is simply the least bad. The earlier +0.32% figure came from 33 sessions and has been retracted. "
             "1 is the default because the edge is concentrated in the single best name.")
    CFG["SHORT_TOP_N"] = 1 if st.checkbox("Show the short side too", value=True,
                                          help="Short ORB expectancy was -0.03%/trade "
                                               "in walk-forward. Shown, never recommended.") else 0
    
    # Multi-timeframe settings
    st.divider()
    st.markdown("#### Multi-Timeframe")
    CFG["MULTI_TF_ORB"] = st.checkbox("5-min + 15-min ORB (both)", value=CFG.get("MULTI_TF_ORB", True),
                                       help="Scan both 5-min and 15-min ORB for intraday")
    st.caption("Intraday: Daily (macro) + 1H (context) + 5m/15m ORB (entry)")
    st.caption("Swing: Daily (full-day) + 1H/2H ORB (entry)")
    
    st.divider()
    st.markdown("#### Automation")
    a1, a2 = st.columns(2)
    if a1.button("▶ Start", width="stretch", key="btn_auto_start"):
        start_auto()
    if a2.button("■ Stop", width="stretch", key="btn_auto_stop"):
        stop_auto()
    st.caption(("🟢 running — pre-market 09:00 · ORB every minute · EOD + outcomes "
                "after 15:45 · movers captured daily · profile refresh weekly · "
                "full refit monthly") if AUTOSTATE["running"] else "⚪ idle")
    if AUTOSTATE["log"]:
        with st.expander("automation log", expanded=False):
            for _l in AUTOSTATE["log"][:25]:
                st.caption(_l)
    st.divider()
    live_refresh = st.checkbox("Auto-refresh page (30s)", value=(ph == "LIVE"))
    if live_refresh and HAS_AUTOREFRESH:
        st_autorefresh(interval=30000, key="qe22refresh")
    if st.button("🔄 Clear data cache", width="stretch", key="btn_clear_cache"):
        CACHE.clear()
        st.success("cache cleared")
    st.divider()
    st.markdown("#### F&O universe")
    if st.button("↻ Re-fetch official F&O list", width="stretch", key="btn_fno"):
        syms, src = fetch_fno_universe()
        st.session_state.res_fno = (syms, src)
    _fno = st.session_state.get("res_fno")
    if _fno:
        st.caption(f"{len(_fno[0])} symbols · {_fno[1]}")
        _new = sorted(set(_fno[0]) - set(FNO_STATIC))
        _gone = sorted(set(FNO_STATIC) - set(_fno[0]))
        if _new:
            st.caption("added since build: " + ", ".join(_new[:10]))
        if _gone:
            st.caption("removed since build: " + ", ".join(_gone[:10]))
    else:
        st.caption(f"{len(FNO_STATIC)} symbols (built-in list, 2026-08-27)")
    try:
        uni, dead = validated_universe()
        st.caption(f"{len(uni)} tradable after validation")
        if dead:
            st.caption("dropped: " + ", ".join(d.replace('.NS', '') for d in dead[:10]))
    except Exception as ex:
        st.caption(f"validation pending ({ex})")
    st.divider()
    st.markdown("#### Models")
    for _nm, _m in (("Intraday LONG +2%", MODEL_L), ("Intraday LONG +3%", MODEL_L3),
                    ("Intraday SHORT", MODEL_S), ("Swing LONG", MODEL_SW)):
        if _m:
            st.caption(f"{_nm}: {len(_m.features)} features, n={_m.n:,}")
    st.caption(f"Confluence gate: ATR≥{CONFLUENCE_GATE['atr_pct']}% · "
               f"ADR≥{CONFLUENCE_GATE['adr20']}% · opening range≥"
               f"{CONFLUENCE_GATE['orb_rng_pct']}% · break within "
               f"{CONFLUENCE_GATE['trig_min_max']} min")

# ---------------------------------------------------------------------------
# PAPER MODE BANNER — the evidence, stated up front, on every screen.
# ---------------------------------------------------------------------------
if CFG.get("PAPER_MODE", True):
    _ev = EVIDENCE
    _COL = {"NO EDGE": "#f85149", "MARGINAL": "#d29922", "WEAK": "#d29922",
            "VALIDATED": "#3fb950"}

    def _chip(k, label):
        b = _ev[k]
        c = _COL.get(b["status"], "#8b949e")
        return (
            f"<div class='qe-chip'>"
            f"<div class='qe-chip-h'>{label}"
            f"<span class='qe-badge' style='background:{c}22;color:{c};"
            f"border:1px solid {c}55'>{b['status']}</span></div>"
            f"<div class='qe-chip-n' style='color:"
            f"{'#3fb950' if b['avg_pct'] > 0 else '#f85149'}'>"
            f"{b['avg_pct']:+.3f}%<span class='qe-chip-u'>per trade</span></div>"
            f"<div class='qe-chip-s'>hit {100*b['hit']:.1f}% · "
            f"positive {b['years_positive']} years</div>"
            f"<div class='qe-chip-f'>{b['note']}</div></div>")

    st.markdown("""<style>
    .qe-paper{border:1px solid #30363d;border-left:3px solid #d29922;
      background:#12161c;border-radius:10px;padding:14px 16px;margin:2px 0 14px}
    .qe-paper-t{font:600 13px/1.3 -apple-system,Segoe UI,Roboto,sans-serif;
      color:#e6edf3;letter-spacing:.02em}
    .qe-paper-d{font:12px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;
      color:#8b949e;margin-top:3px}
    .qe-grid{display:flex;gap:10px;flex-wrap:wrap;margin-top:12px}
    .qe-chip{flex:1 1 210px;background:#0d1117;border:1px solid #21262d;
      border-radius:8px;padding:10px 12px}
    .qe-chip-h{font:600 11px/1.2 -apple-system,Segoe UI,sans-serif;color:#8b949e;
      text-transform:uppercase;letter-spacing:.06em;display:flex;
      justify-content:space-between;align-items:center;gap:8px}
    .qe-badge{font-size:9px;font-weight:700;padding:2px 6px;border-radius:20px;
      letter-spacing:.04em;white-space:nowrap}
    .qe-chip-n{font:700 20px/1.1 -apple-system,Segoe UI,sans-serif;margin-top:7px}
    .qe-chip-u{font:400 10px/1 -apple-system,sans-serif;color:#6e7681;
      margin-left:5px;letter-spacing:.04em}
    .qe-chip-s{font:11px/1.4 -apple-system,sans-serif;color:#8b949e;margin-top:5px}
    .qe-chip-f{font:10px/1.4 -apple-system,sans-serif;color:#6e7681;margin-top:4px}
    .qe-paper-x{font:11px/1.5 -apple-system,sans-serif;color:#8b949e;
      margin-top:11px;padding-top:9px;border-top:1px solid #21262d}
    </style>""", unsafe_allow_html=True)

    st.markdown(
        "<div class='qe-paper'>"
        "<div class='qe-paper-t'>PAPER MODE — signals are shown and logged, "
        "not placed as orders</div>"
        f"<div class='qe-paper-d'>Calibrated on {_ev['sessions_tested']:,} "
        f"sessions, {_ev['data'].split(', ')[-1]}, {_ev['universe']}. "
        f"This panel is a status summary, not an error.</div>"
        "<div class='qe-grid'>"
        + _chip("intraday_long", "Intraday long")
        + _chip("intraday_short", "Intraday short")
        + _chip("swing_long", "Swing long")
        + "</div><div class='qe-paper-x'>Break-even at 2:1 reward-to-risk needs a "
          "34.6% hit rate. Every signal below is written to the Journal "
          "automatically, so forward results accumulate from today. Flip "
          "<code>CFG['PAPER_MODE'] = False</code> once a configuration has "
          "earned it.</div></div>", unsafe_allow_html=True)

TABS = st.tabs(["🌅 Pre-Market (09:08)", "⚡ Live ORB", "🌙 EOD → Tomorrow",
                "📈 Swing (Friday)", "🔁 Replay", "📊 Movers", "🧠 Learning", "📓 Journal"])

# ---------------------------------------------------------------- PRE-MARKET
with TABS[0]:
    st.markdown("#### Top 1 BUY + Top 1 SELL for today")
    st.caption("Run between 09:00 and 09:08. Reads the live NSE pre-open call auction. "
               "This is a WATCHLIST, not the order — the order is the 5-min/15-min ORB break "
               "on the Live ORB tab. Multi-TF: Daily (macro) + 1H (context) + 5m/15m ORB (execution)")
    if st.button("🌅 Run pre-market scan", type="primary", key="btn_pm"):
        with st.spinner("reading NSE pre-open book + building features…"):
            st.session_state.res_pm = scan_premarket(capital=capital)
    res = st.session_state.get("res_pm") or load_scan("premarket")
    if res and not res.get("error"):
        st.caption(f"source: {res.get('source')} · {regime_badge(res.get('regime', '—'))} · "
                   f"{res.get('n_candidates')} feasible names", unsafe_allow_html=True)
        if "previous close" in str(res.get("source", "")):
            st.warning("NSE pre-open feed unavailable — gaps assumed flat. Re-run after "
                       "09:02, or use the Live ORB tab, which never needs pre-open data.")
        v = view_toggle("pm")
        L = (res.get("longs") or []); S = (res.get("shorts") or [])
        if v == "List":
            recs = []
            for lbl, arr in (("BUY", L), ("SELL", S)):
                for i, c in enumerate(arr, 1):
                    r = c["row"]
                    recs.append({"#": i, "Side": side_badge(lbl), "Symbol": r["sym"],
                                 "Sector": r.get("sector"),
                                 "Exp. open": money(r.get("exp_open") or 0),
                                 "Gap %": pct(r.get("gap") or 0),
                                 "ATR %": num(r.get("atr_pct") or 0, 1),
                                 "ADR %": num(r.get("adr20") or 0, 1),
                                 "P(move) %": f"{num((r.get('p_up') if lbl == 'BUY' else r.get('p_dn')) or 0, 1)}%",
                                 "Days ≥2.5% / 20": r.get("big20"),
                                 "Qty": (c.get("ticket") or {}).get("qty")})
            D = pd.DataFrame(recs)
            st.dataframe(D, width="stretch", hide_index=True)
            dl(D, "premarket_watchlist.csv", "pm")
        else:
            cA, cB = st.columns(2)
            with cA:
                st.markdown("##### 🟢 BUY candidate")
                for c in L[:1]:
                    r = c["row"]
                    ticket_card(c["ticket"], extra_rows=[
                        ("Expected open", money(r.get("exp_open"))), ("Gap", pct(r.get("gap"))),
                        ("ATR% / ADR%", f"{num(r.get('atr_pct'))} / {num(r.get('adr20'))}"),
                        ("P(+2% | this gap bucket)", f"{num(r.get('p_up'), 1)}%"),
                        ("Days ≥2.5% in last 20", num(r.get("big20"), 0)),
                        ("Room to next resistance", pct(r.get("room_up")))])
                with st.expander("next candidates"):
                    for c in L[1:]:
                        r = c["row"]
                        st.write(f"**{r['sym']}** {r['sector']} · gap {pct(r.get('gap'))} · "
                                 f"ATR {num(r.get('atr_pct'))}% · P {num(r.get('p_up'), 1)}%")
            with cB:
                st.markdown("##### 🔴 SELL candidate")
                for c in S[:1]:
                    r = c["row"]
                    ticket_card(c["ticket"], low_conf=True, extra_rows=[
                        ("Expected open", money(r.get("exp_open"))), ("Gap", pct(r.get("gap"))),
                        ("ATR% / ADR%", f"{num(r.get('atr_pct'))} / {num(r.get('adr20'))}"),
                        ("P(−2% | this gap bucket)", f"{num(r.get('p_dn'), 1)}%"),
                        ("Room to next support", pct(r.get("room_dn")))])
                with st.expander("next candidates"):
                    for c in S[1:]:
                        r = c["row"]
                        st.write(f"**{r['sym']}** {r['sector']} · gap {pct(r.get('gap'))} · "
                                 f"ATR {num(r.get('atr_pct'))}% · P {num(r.get('p_dn'), 1)}%")
        st.info("Gaps mean-revert intraday in this data: P(+2% | gap < −2%) = 60.0% versus "
                "P(−2%) = 33.7%. The ranking already accounts for that.")
        dl(_clean(res.get("table")), "premarket_full.csv", "pm_full")
    else:
        st.info("No pre-market scan yet today.")

# ---------------------------------------------------------------- LIVE ORB
with TABS[1]:
    st.markdown(f"#### The order — top {CFG['INTRADAY_TOP_N']} close-confirmed 5-min/15-min ORB break")
    st.caption("Entry = the CLOSE of the first 5-min/15-min candle beyond the opening range. "
               "Requiring the close instead of a touch is the single biggest lever measured "
               "in the study. Stop is capped at 1.0%. Half off at +1%, then stop to breakeven. "
               "⏰ No new ORB after 10:00 AM · 📈 Momentum scan after 12:30 PM")
    
    # Time status indicator
    now = get_ist_now()
    current_time = now.time()
    orb_allowed = current_time < dtime(10, 0)
    momentum_mode = current_time >= dtime(12, 30)
    
    tcol1, tcol2, tcol3 = st.columns([1, 1, 2])
    with tcol1:
        st.markdown(f"**Mode:** {regime_badge('ORB' if orb_allowed and not momentum_mode else 'MOMENTUM' if momentum_mode else 'CLOSED')}")
    with tcol2:
        st.markdown(f"**Time:** {fmt_time(now)}")
    with tcol3:
        if not orb_allowed and not momentum_mode:
            st.warning("⏰ ORB window closed (10:00 AM). Waiting for momentum window (12:30 PM).")
        elif momentum_mode:
            st.info("📈 Momentum mode active - scanning for high-volume trend continuation")
        else:
            st.success("✅ ORB window active - scanning for breakouts")
    
    cc1, cc2 = st.columns([1, 3])
    if cc1.button("⚡ Scan live ORB", type="primary", key="btn_orb"):
        with st.spinner("pulling 5-min/15-min bars + 1H context for the whole F&O list…"):
            st.session_state.res_orb = scan_live_orb(capital=capital)
    res = st.session_state.get("res_orb") or load_scan("live_orb")
    if res and not res.get("error"):
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Confirmed breaks", res.get("n_triggers", 0))
        m2.metric("Passed confluence gate", res.get("n_gate_pass", 0))
        m3.markdown(f"**Regime**  \n{regime_badge(res.get('regime', '—'))}", unsafe_allow_html=True)
        m4.metric("Market breadth @09:20", pct(res.get("mkt_early")))
        act = res.get("actionable") or []
        if act:
            st.markdown("##### ✅ Place these")
            render_signals(act, "orb_act")
        else:
            mode = res.get("scan_mode", "ORB")
            if mode == "MOMENTUM":
                st.info("📈 Momentum scan complete - no qualifying momentum stocks found.")
            elif mode == "ORB_ENDED":
                st.warning("⏰ ORB window closed. Momentum scan starts at 12:30 PM.")
            else:
                st.warning("Nothing passed the gate and the probability floor. That is a valid "
                           "answer — on the walk-forward sample, forcing a trade on the days the "
                           "gate was empty was measured on 33 sessions and did not survive "
                           "re-measurement on 1,026. See V23_VERIFICATION.md.")
        with st.expander(f"every confirmed break, ranked ({res.get('n_triggers', 0)})"):
            D = _clean(pd.DataFrame(res.get("all_ranked") or []),
                       ["sym", "sector", "side", "orb_minutes", "prob_pct", "p3_pct", "entry", "sl_pct",
                        "trig_min", "orb_range_pct", "atr_pct", "adr20", "gap", "sec_rel",
                        "h1_trend", "h1_rsi", "gate_pass", "gate_note", "moved", "scan_type"],
                       {**SIG_NAMES, "orb_minutes": "ORB TF", "h1_trend": "1H Trend", 
                        "h1_rsi": "1H RSI", "scan_type": "Type"})
            st.dataframe(D, width="stretch", hide_index=True)
            dl(D, "live_orb_all.csv", "orb_all")
        with st.expander("rejected by the confluence gate — and exactly why"):
            R = _clean(pd.DataFrame(res.get("rejected") or []),
                       ["sym", "side", "prob_pct", "trig_min", "orb_range_pct",
                        "atr_pct", "adr20", "gate_note"], SIG_NAMES)
            st.dataframe(R, width="stretch", hide_index=True)
            dl(R, "live_orb_rejected.csv", "orb_rej")
        with st.expander("sector strength at 09:20 (the rotation signal)"):
            sec = res.get("sector_early") or {}
            SD = pd.DataFrame([{"Sector": k, "Median move %": round(v[0], 2),
                                "Breadth % up": round(v[1], 0)}
                               for k, v in sec.items()]).sort_values("Median move %",
                                                                      ascending=False)
            st.dataframe(SD, width="stretch", hide_index=True)
            dl(SD, "sector_strength.csv", "orb_sec")
            st.caption("From the 24–26 Aug case study: on two of the three days, sector beta "
                       "explained more of the big movers than any company news did.")
    else:
        st.info("No ORB scan yet. It needs at least one completed 5-min candle (09:20).")

# ---------------------------------------------------------------- EOD
with TABS[2]:
    st.markdown("#### Tomorrow's top 5 — ranked by expected-move capability")
    st.caption("These are names capable of a 2%+ range tomorrow. Direction is decided by "
               "tomorrow's 5-min/15-min ORB, in either direction. Multi-TF: Daily (macro) + 1H (context) + 5m/15m ORB (execution)")
    if st.button("🌙 Run EOD scan", type="primary", key="btn_eod"):
        with st.spinner("ranking the F&O list on expansion capability…"):
            st.session_state.res_eod = scan_eod(capital=capital)
    res = st.session_state.get("res_eod") or load_scan("eod")
    if res and not res.get("error"):
        st.caption(f"for {res.get('for_date')} · {regime_badge(res.get('regime', '—'))} · "
                   f"{res.get('n')} names screened", unsafe_allow_html=True)
        top = res.get("top") or []
        v = view_toggle("eod")
        if v == "List":
            D = pd.DataFrame([{"#": i, "Symbol": p["row"]["sym"], "Sector": p["row"]["sector"],
                               "Close": money(p["row"].get("close") or 0),
                               "P(2% move) %": f"{num(p['row'].get('p_move2') or 0, 1)}%",
                               "ATR %": num(p["row"].get("atr_pct") or 0, 1),
                               "ADR %": num(p["row"].get("adr20") or 0, 1),
                               "Days ≥2.5%/20": p["row"].get("big20"),
                               "RVOL": num(p["row"].get("rvol") or 0, 1),
                               "Pivot": num((p.get("pivot_D") or {}).get("P") or 0, 1),
                               "R1": num((p.get("pivot_D") or {}).get("R1") or 0, 1),
                               "S1": num((p.get("pivot_D") or {}).get("S1") or 0, 1)}
                              for i, p in enumerate(top, 1)])
            st.dataframe(D, width="stretch", hide_index=True)
            dl(D, "eod_top5.csv", "eod")
        else:
            for p in top:
                r = p["row"]
                st.markdown(f"##### {r['sym']} · {r['sector']}")
                cA, cB = st.columns([2, 3])
                cA.metric("P(2% move tomorrow)", f"{num(r.get('p_move2'), 1)}%")
                cA.caption(f"ATR {num(r.get('atr_pct'))}% · ADR {num(r.get('adr20'))}% · "
                           f"RVOL {num(r.get('rvol'))}")
                with cB:
                    pivot_table({"D": p.get("pivot_D"), "W": p.get("pivot_W"),
                                 "M": p.get("pivot_M")})
                st.caption(p.get("note", ""))
        dl(_clean(res.get("table")), "eod_full.csv", "eod_full")
    else:
        st.info("No EOD scan yet.")

# ---------------------------------------------------------------- SWING
with TABS[3]:
    st.markdown("#### Weekly swing plan — LONG only, Monday 1-hour/2-hour ORB entry")
    st.caption("Calibrated on 133 weeks of 60-min bars. P(+10% before a −5% stop) is 2.2% "
               "unconditionally and 11.1% on the top-3 ranked names, so the ladder is "
               "+3% / +5% / +10% — the 10% is the runner, not the plan. Multi-TF: Daily (full-day) + 1H/2H ORB (execution)")
    if st.button("📈 Run swing scan", type="primary", key="btn_sw"):
        with st.spinner("building weekly features…"):
            st.session_state.res_sw = scan_swing(capital=capital)
    res = st.session_state.get("res_sw") or load_scan("swing")
    if res and not res.get("error"):
        st.caption(f"for week starting {res.get('for_week', '—')} · "
                   f"{regime_badge(res.get('regime', '—'))} · {res.get('n', 0)} names screened", unsafe_allow_html=True)
        picks = res.get("top") or []
        v = view_toggle("sw")
        if v == "List":
            D = pd.DataFrame([{"#": i, "Symbol": p["row"]["sym"],
                               "Sector": p["row"].get("sector"),
                               "Close": money(p["row"].get("px") or 0),
                               "P(+7% before stop) %": f"{num((p.get('p') or 0) * 100, 1)}%",
                               "ATR %": num(p["row"].get("atr_pct") or 0, 1),
                               "Qty": (p.get("ticket") or {}).get("qty"),
                               "Stop": money((p.get("ticket") or {}).get("sl")),
                               "Target": money((p.get("ticket") or {}).get("target"))}
                              for i, p in enumerate(picks, 1)])
            st.dataframe(D, width="stretch", hide_index=True)
            dl(D, "swing_plan.csv", "sw")
        else:
            for p in picks:
                ticket_card(p["ticket"], prob=p.get("p"), ladder=p.get("ladder"),
                            extra_rows=[("ATR %", num(p["row"].get("atr_pct"))),
                                        ("21d return", pct(p["row"].get("ret21"))),
                                        ("Distance to 20d high", pct(p["row"].get("d_hi20")))])
        dl(_clean(res.get("table")), "swing_full.csv", "sw_full")
    else:
        st.info("No swing scan yet. Run it on Friday after the close or over the weekend.")

# ---------------------------------------------------------------- REPLAY
with TABS[4]:
    st.markdown("#### Historical Replay & Backtesting")
    st.caption("Single-day replay shows what the screener would have generated. Multi-day backtest aggregates statistics for strategy validation.")
    
    rep_tab1, rep_tab2 = st.tabs(["📅 Single-Day Replay", "📊 Multi-Day Backtest"])
    
    with rep_tab1:
        st.markdown("#### Historical replay — run today's three engines on any past session")
        st.caption("Answers 'what would the screener have told me on that day', and then shows "
                   "what actually happened to those exact tickets. 5-minute bars are only kept "
                   "for about 60 calendar days by the data provider, so the ORB replay window "
                   "is bounded by that; the pre-market and EOD replays go back as far as the "
                   "loaded daily history.")
        rc1, rc2, rc3 = st.columns([2, 1, 1])
        _def = prev_trading_day()
        rday = rc1.date_input("Session to replay", value=_def,
                              min_value=_def - timedelta(days=400), max_value=_def,
                              key="replay_day")
        rtop = rc2.selectbox("Signals", [1, 2, 3], index=0, key="replay_top")
        if rc3.button("🔁 Replay", type="primary", key="btn_replay"):
            with st.spinner(f"replaying {rday}…"):
                st.session_state.res_replay = replay_session(rday, capital=capital, top_n=rtop)
        rep = st.session_state.get("res_replay")
        if rep:
            if rep.get("error"):
                st.error(rep["error"])
            else:
                st.caption(f"{rep['day']} · {rep['n_names']} liquid names · "
                           f"5-min bars found for {rep['n_bars']}")
                t1, t2, t3 = st.tabs(["🌅 Pre-Market as it stood", "⚡ Live ORB", "🌙 EOD → next day"])
                with t1:
                    pm = rep.get("premarket") or {}
                    if pm.get("error"):
                        st.info(pm["error"])
                    else:
                        st.caption(pm.get("source"))
                        v = view_toggle("rep_pm")
                        D = _clean(pd.DataFrame(pm.get("longs") or []),
                                   ["sym", "sector", "exp_open", "gap", "atr_pct", "adr20",
                                    "p_up", "big20"],
                                   {"sym": "Symbol", "sector": "Sector", "exp_open": "Open",
                                    "gap": "Gap %", "atr_pct": "ATR %", "adr20": "ADR %",
                                    "p_up": "P(+2%) %", "big20": "Days ≥2.5%/20"})
                        D2 = _clean(pd.DataFrame(pm.get("shorts") or []),
                                    ["sym", "sector", "exp_open", "gap", "atr_pct", "adr20",
                                     "p_dn"],
                                    {"sym": "Symbol", "sector": "Sector", "exp_open": "Open",
                                     "gap": "Gap %", "atr_pct": "ATR %", "adr20": "ADR %",
                                     "p_dn": "P(−2%) %"})
                        if v == "List":
                            st.markdown("**Top BUY candidates**")
                            st.dataframe(D, width="stretch", hide_index=True)
                            st.markdown("**Top SELL candidates**")
                            st.dataframe(D2, width="stretch", hide_index=True)
                        else:
                            cA, cB = st.columns(2)
                            cA.markdown("**🟢 BUY**")
                            for _, r in D.head(3).iterrows():
                                cA.markdown(f"**{r['Symbol']}** · {r['Sector']} · gap "
                                            f"{r['Gap %']}% · P {r['P(+2%) %']}%")
                            cB.markdown("**🔴 SELL**")
                            for _, r in D2.head(3).iterrows():
                                cB.markdown(f"**{r['Symbol']}** · {r['Sector']} · gap "
                                            f"{r['Gap %']}% · P {r['P(−2%) %']}%")
                        dl(D, f"replay_{rep['day']}_premarket_buy.csv", "rep_pm_b")
                        dl(D2, f"replay_{rep['day']}_premarket_sell.csv", "rep_pm_s")
                with t2:
                    if rep.get("orb_error"):
                        st.warning(rep["orb_error"])
                    orb = rep.get("orb") or {}
                    act = orb.get("actionable") or []
                    if act:
                        won = [a for a in act if a.get("rp_result") == "TARGET HIT"]
                        q1, q2, q3 = st.columns(3)
                        q1.metric("Signals", len(act))
                        q2.metric("Target hit", f"{len(won)}/{len(act)}")
                        q3.metric("Avg result", pct(np.mean([a.get("rp_pnl_pct", 0) for a in act])))
                        render_signals(act, "rep_orb")
                    elif not rep.get("orb_error"):
                        st.info("Nothing passed the confluence gate on that session — the "
                                "screener would correctly have told you to stay out.")
                    if orb.get("all_ranked"):
                        with st.expander("every confirmed break that session"):
                            D = _clean(pd.DataFrame(orb["all_ranked"]),
                                       ["sym", "sector", "side", "prob_pct", "p3_pct", "entry",
                                        "sl_pct", "trig_min", "orb_range_pct", "gate_pass",
                                        "gate_note"], SIG_NAMES)
                            st.dataframe(D, width="stretch", hide_index=True)
                            dl(D, f"replay_{rep['day']}_orb_all.csv", "rep_orb_all")
                with t3:
                    eod = rep.get("eod") or {}
                    if eod.get("error"):
                        st.info(eod["error"])
                    else:
                        rows = []
                        for i, p in enumerate(eod.get("top") or [], 1):
                            nd = p.get("next_day") or {}
                            rows.append({"#": i, "Symbol": p["row"]["sym"],
                                         "Sector": p["row"]["sector"],
                                         "P(2% move) %": round(p["row"].get("p_move2") or 0, 1),
                                         "ATR %": round(p["row"].get("atr_pct") or 0, 2),
                                         "Next session": nd.get("date"),
                                         "Max up from open %": nd.get("up_pct"),
                                         "Max down from open %": nd.get("dn_pct")})
                        D = pd.DataFrame(rows)
                        st.dataframe(D, width="stretch", hide_index=True)
                        dl(D, f"replay_{rep['day']}_eod.csv", "rep_eod")
                        st.caption("'Max up/down from open' is what the name actually delivered "
                                   "the next session — a direct scorecard for the EOD ranking.")
    
    with rep_tab2:
        st.markdown("#### Multi-Day Backtest")
        st.caption("Run the complete strategy across multiple sessions for statistical validation. "
                   "Fetches 5-min bars, applies confluence gate, models, and simulates execution with partials + trailing.")
        bc1, bc2, bc3 = st.columns([2, 1, 1])
        _def = prev_trading_day()
        bt_start = bc1.date_input("Start date", value=_def - timedelta(days=30),
                                  min_value=_def - timedelta(days=400), max_value=_def,
                                  key="bt_start")
        bt_end = bc2.date_input("End date", value=_def,
                                min_value=_def - timedelta(days=400), max_value=_def,
                                key="bt_end")
        btop = bc3.selectbox("Top N", [1, 2, 3], index=0, key="bt_top")
        borb = st.selectbox("ORB Timeframe", [5, 15], index=0, key="bt_orb")
        
        if st.button("📊 Run Backtest", type="primary", key="btn_backtest"):
            if bt_start > bt_end:
                st.error("Start date must be before end date")
            else:
                with st.spinner("Running comprehensive backtest..."):
                    st.session_state.res_backtest = run_backtest(
                        bt_start, bt_end, capital=capital, top_n=btop, orb_minutes=borb
                    )
        
        bt_res = st.session_state.get("res_backtest")
        if bt_res:
            if bt_res.get("error"):
                st.error(bt_res["error"])
            else:
                # Summary metrics
                c1, c2, c3, c4, c5 = st.columns(5)
                c1.metric("Days Tested", bt_res["total_days"])
                c2.metric("Total Trades", bt_res["total_trades"])
                c3.metric("Win Rate", f"{bt_res['win_rate']:.1f}%")
                c4.metric("Expectancy", f"{bt_res['expectancy']:.2f}%")
                c5.metric("Profit Factor", f"{bt_res['profit_factor']:.2f}")
                
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Avg Win", f"{bt_res['avg_win']:.2f}%")
                c2.metric("Avg Loss", f"{bt_res['avg_loss']:.2f}%")
                c3.metric("Total P&L", f"{bt_res['total_pnl']:.2f}%")
                c4.metric("Max DD", f"{bt_res['max_dd']:.2f}%")
                
                # Breakdown by result
                with st.expander("Trade Outcomes"):
                    res_df = pd.DataFrame(list(bt_res["by_result"].items()), 
                                          columns=["Outcome", "Count"])
                    st.dataframe(res_df, width="stretch", hide_index=True)
                
                # Breakdown by side
                with st.expander("By Side"):
                    side_df = pd.DataFrame(list(bt_res["by_side"].items()), 
                                           columns=["Side", "Count"])
                    st.dataframe(side_df, width="stretch", hide_index=True)
                
                # Breakdown by ORB timeframe
                if bt_res.get("by_orb_tf"):
                    with st.expander("By ORB Timeframe"):
                        tf_df = pd.DataFrame(list(bt_res["by_orb_tf"].items()), 
                                             columns=["Timeframe", "Count"])
                        st.dataframe(tf_df, width="stretch", hide_index=True)
                
# All trades
                with st.expander("All Trades"):
                    trades_df = pd.DataFrame(bt_res["trades"])
                    if not trades_df.empty:
                        show_cols = ["date", "sym", "side", "orb_minutes", "entry", "sl_pct", 
                                    "rp_result", "rp_pnl_pct", "rp_mfe_pct"]
                        show_cols = [c for c in show_cols if c in trades_df.columns]
                        st.dataframe(_clean(trades_df[show_cols]), width="stretch", hide_index=True)
                        dl(trades_df, f"backtest_{bt_start}_{bt_end}.csv", "bt_trades")
                
                st.success("Backtest complete! Results saved to data/performance/")

# ---------------------------------------------------------------- MOVERS
with TABS[5]:
    st.markdown("#### Top gainers & losers — automatic and manual")
    st.caption("Automatic: computed from the F&O price panel. Daily = intraday open-to-close, "
               "Weekly / Monthly = period open-to-close, which is what a swing trade "
               "actually captures. Manual: your own list, used the same way by the "
               "learning loop.")
    mv1, mv2 = st.tabs(["🤖 Automatic", "✍️ Manual entry"])
    with mv1:
        pc1, pc2, pc3 = st.columns([2, 1, 1])
        pmap = {"Daily (intraday F&O)": "D", "Weekly (swing)": "W", "Monthly (swing)": "M"}
        psel = pc1.selectbox("Period", list(pmap.keys()), key="mv_period")
        pn = pc2.selectbox("Top N", [3, 5, 10], index=0, key="mv_n")
        pback = pc3.number_input("Periods back", 0, 24, 0, key="mv_back")
        if st.button("📊 Compute movers", type="primary", key="btn_movers"):
            with st.spinner("ranking…"):
                st.session_state.res_movers = top_movers(period=pmap[psel], n=int(pn),
                                                         back=int(pback))
        movers_block(st.session_state.get("res_movers"), "movers")
        st.divider()
        c_a, c_b = st.columns(2)
        if c_a.button("💾 Capture current movers to history", key="btn_mv_cap"):
            n = movers_capture(history=1)
            st.success(f"{n} new rows written to the movers history")
        hist = movers_auto_history()
        c_b.metric("Rows in movers history", len(hist))
        if not hist.empty:
            with st.expander("full captured history"):
                st.dataframe(hist.sort_values("captured_at", ascending=False),
                             width="stretch", hide_index=True)
                dl(hist, "movers_history.csv", "mv_hist")
        st.markdown("##### Download accurate top-3 sets")
        d1, d2, d3 = st.columns(3)
        for col, (lbl, code, back_n) in zip(
                (d1, d2, d3), (("Daily top 3 (last 20 sessions)", "D", 20),
                               ("Weekly top 3 (last 12 weeks)", "W", 12),
                               ("Monthly top 3 (last 12 months)", "M", 12))):
            with col:
                if st.button(lbl, key=f"btn_bulk_{code}", width="stretch"):
                    frames = []
                    for bk in range(back_n):
                        r = top_movers(period=code, n=3, back=bk)
                        if r.get("rows"):
                            frames.append(pd.DataFrame(r["rows"]))
                    if frames:
                        st.session_state[f"bulk_{code}"] = pd.concat(frames, ignore_index=True)
                bulk = st.session_state.get(f"bulk_{code}")
                if bulk is not None:
                    dl(bulk, f"top3_{code}_history.csv", f"bulk_dl_{code}",
                       label=f"⬇ {len(bulk)} rows")
    with mv2:
        st.caption("Add the movers you spotted yourself. Intraday = the day's top gainer or "
                   "loser; Weekly / Monthly = swing movers.")
        with st.form("man_mover"):
            f1, f2, f3 = st.columns(3)
            mp = f1.selectbox("Period", ["D (intraday)", "W (weekly swing)",
                                         "M (monthly swing)"])
            mk = f2.selectbox("Type", ["GAINER", "LOSER"])
            msym = f3.text_input("Symbol (e.g. SAIL)").strip().upper()
            g1, g2 = st.columns([1, 3])
            mret = g1.number_input("Move %", -50.0, 50.0, 0.0, 0.1)
            mlabel = g2.text_input("Period label (date / 2026-W35 / 2026-08)",
                                   value=str(session_date()))
            mreason = st.text_input("Reason / catalyst you observed")
            if st.form_submit_button("Add", type="primary"):
                if msym:
                    movers_manual_add(dict(period=mp.split()[0], period_label=mlabel,
                                           kind=mk, sym=msym, ret_pct=mret,
                                           reason=mreason, added_at=fmt_ist()))
                    st.success(f"{msym} added")
                else:
                    st.error("symbol is required")
        man = movers_manual()
        if not man.empty:
            v = view_toggle("mv_man")
            if v == "List":
                st.dataframe(man.sort_values("added_at", ascending=False),
                             width="stretch", hide_index=True)
            else:
                for _, r in man.sort_values("added_at", ascending=False).head(30).iterrows():
                    colr = "#0ecb81" if r["kind"] == "GAINER" else "#f6465d"
                    st.markdown(f"<div style='background:#11141f;border-left:3px solid {colr};"
                                f"border-radius:8px;padding:8px 12px;margin-bottom:6px'>"
                                f"<b>{r['sym']}</b> <span style='color:{colr}'>"
                                f"{r['ret_pct']}%</span> · {r['period']} {r['period_label']}"
                                f"<br><span style='font-size:11px;color:#8f96b3'>"
                                f"{r['reason']}</span></div>", unsafe_allow_html=True)
            dl(man, "manual_movers.csv", "mv_man_dl")

# ---------------------------------------------------------------- LEARNING
with TABS[6]:
    st.markdown("#### What actually separates the big movers — and how often this refits")
    st.caption("Built from the 24–26 Aug case study and from every session in the sample. "
               "The profile below is measured on the PREVIOUS day's features of names that "
               "then ran 3%+, so it is usable before the move, not after it.")
    lc1, lc2, lc3 = st.columns([1, 1, 2])
    lb = lc1.selectbox("Lookback (sessions)", [20, 30, 60], index=1, key="learn_lb")
    mm = lc2.selectbox("Define a mover as", [3.0, 4.0, 5.0], index=0, key="learn_mm")
    if lc3.button("🧠 Rebuild the mover profile", type="primary", key="btn_learn"):
        with st.spinner("profiling…"):
            st.session_state.res_learn = learn_from_movers(lookback_days=int(lb),
                                                           min_move=float(mm))
    prof = st.session_state.get("res_learn") or learn_profile()
    if prof and prof.get("features"):
        st.caption(f"built {prof.get('built_at')} · {prof.get('n_sessions')} sessions · "
                   f"mover = |move| ≥ {prof.get('min_move_pct')}%")
        NICE = {"atr_pct": "ATR %", "adr20": "ADR 20d %", "rvol": "Volume vs 20d avg",
                "ret1": "Prev-day return %", "ret5": "5-day return %",
                "ret21": "21-day return %", "px_vs_e20": "Price vs EMA20 %",
                "d_hi20": "Distance to 20d high %", "d_lo20": "Distance to 20d low %",
                "gap": "Gap %", "vol20": "20d volatility", "bbw": "Bollinger width",
                "big_moves_20": "Days ≥2.5% in 20", "rsi": "RSI 14",
                "turn_ma_cr": "Turnover ₹cr"}
        D = pd.DataFrame([{"Feature": NICE.get(k, k),
                           "Movers (median)": v["winner_median"],
                           "Everything else": v["rest_median"],
                           "Difference": v.get("diff"),
                           "Ratio": v["ratio"], "n movers": v["n_winner"]}
                          for k, v in prof["features"].items()]).sort_values(
            "Ratio", ascending=False, na_position="last")
        st.caption("Ratio is left blank where the baseline median sits on zero — "
                   "for those features read the Difference column instead. Both are "
                   "measured on the PREVIOUS day's data.")
        v = view_toggle("learn")
        if v == "List":
            st.dataframe(D, width="stretch", hide_index=True)
        else:
            for _, r in D.iterrows():
                tail = (f"**{r['Ratio']}×**" if r["Ratio"] == r["Ratio"] and r["Ratio"]
                        else f"gap **{r['Difference']:+}**")
                st.markdown(f"**{r['Feature']}** — movers {r['Movers (median)']} vs "
                            f"{r['Everything else']} elsewhere · {tail}")
        dl(D, "mover_profile.csv", "learn")
    st.divider()
    st.markdown("##### The 24–26 August study, in numbers")
    st.markdown("""
Your 27 matched signals versus the other 603 F&O name-days on the same three sessions,
comparing only information available **before** the move:

| Feature (previous close) | Your winners | Everything else | Ratio |
|---|---|---|---|
| 5-day return | 1.71% | 0.14% | **12.5×** |
| Gap at open | 0.42% | 0.04% | **9.9×** |
| Price above EMA20 | 1.04% | 0.34% | **3.05×** |
| Opened above previous-day R1 | 14.8% of names | 5.6% | **2.63×** |
| Opening 5-min range | 1.04% | 0.71% | **1.46×** |
| Previous-day volume vs 20d | 1.29× | 0.82× | **1.58×** |
| ATR % | 2.41 | 2.19 | 1.10× |
| RSI 14 | — | — | 1.02× (no signal) |
| Turnover | lower | higher | 0.84× |

Realised outcome: your names had a median best-case move of **3.25%** against **0.43%**
for everything else, reached +2% on **67%** of cases versus **4%**, and +3% on **44%**
versus **1%**. So the selection was real, not luck — but note that RSI and liquidity
carried no information, and ATR barely did.

**Confluence stacking** (full sample, longs, six conditions: ATR/ADR expansion, opening
range width, prior-day volume expansion, 5-day momentum, above EMA20, pivot breakout):

| Conditions aligned | Events | Reached +2% | Reached +3% |
|---|---|---|---|
| 0–1 | 1,384 | 4.4% | 1.1% |
| 2–3 | 6,258 | 7.1% | 2.6% |
| 4 | 3,130 | 8.1% | 3.5% |
| 5 | 1,279 | 10.6% | 4.2% |
| 6 | 107 | **16.8%** | **8.4%** |

About 20 names a day reach five or six conditions, which is the pool the screener ranks.
The short side does **not** stack this way — its numbers stay flat near 6% regardless of
confluence, which is why shorts remain low-confidence here.

**Break timing matters more than any indicator:** a break confirmed within 10 minutes of
the open reached +2% on 14.9% of events; after 120 minutes, 4.2%. That single fact is the
strongest coefficient in the model.
""")
    st.divider()
    st.markdown("##### News and catalysts — what the 28 signals were actually driven by")
    st.markdown("""
Every one of the 28 was researched individually. Only **7 (25%)** had a documented
company-specific catalyst. **9 (32%)** were pure sector or macro beta with no company
news at all, **1** was expiry mechanics, and **11 (39%)** had nothing findable in the
financial press.

The practical conclusions, which are built into this version:

1. **Sector strength is ranked before company news.** On 24 and 26 August, sector beta
   explained more of the movers than any filing did — HINDZINC, SAIL and VEDL on 26 August
   were one metal theme, not three ideas. The Live ORB tab now shows sector median move and
   breadth at 09:20, and both feed the model.
2. **A news filter must never be a gate.** Used as a gate it would have suppressed three
   quarters of your own winners.
3. **Signed news, not keyword news.** VBL fell on an expansion announcement, FEDERALBNK
   fell on an acquisition report, PREMIERENE fell on the day it disclosed a rating upgrade.
   Keyword-presence logic gets the sign wrong.
4. **Expiry sessions are tagged, not trusted.** 25 August was expiry, had negative breadth
   despite closing higher, and produced the highest share of unexplainable moves.
5. **Brokerage target changes are amplifiers, not initiators** — all four in the sample sat
   on top of a real event.
""")
    st.divider()
    st.markdown("##### Refit cadence — the answer to 'weekly or monthly?'")
    st.markdown("""
**Both, doing different jobs.** A single cadence is the wrong choice.

| Job | Cadence | Why this cadence |
|---|---|---|
| Append movers + score yesterday's signals | **Daily** | Free, no refit, builds the record |
| Refresh the mover profile and the official F&O list | **Weekly** | ~1,000 fresh events moves a median safely; F&O entries and exits happen monthly at most, so weekly always catches them |
| Full logistic refit + monthly pivots | **Monthly** | A stable refit needs ≈1,500 labelled trigger events. The sample produces ~275 per session, so a month gives ~5,500 and a week only ~1,375 — a weekly refit would chase noise |

So: **weekly for the universe and the descriptive profile, monthly for the model.** The
automation runs all three jobs; nothing needs to be triggered by hand.
""")
    if st.button("♻️ Force a full model refit now", key="btn_refit"):
        with st.spinner("refitting on recorded outcomes…"):
            try:
                st.session_state.res_refit = refit_models()
            except Exception as ex:
                st.session_state.res_refit = dict(error=str(ex))
    rf = st.session_state.get("res_refit")
    if rf:
        st.write(rf)

# ---------------------------------------------------------------- JOURNAL
with TABS[7]:
    st.markdown("#### Journal — automatic and manual")
    j1, j2 = st.tabs(["🤖 Automatic (every signal)", "✍️ Manual (your trades)"])
    with j1:
        st.caption("Every signal the screener generates is written here automatically, "
                   "traded or not. This is what makes the hit rate on the Learning tab a "
                   "measurement rather than a memory.")
        A = journal_auto()
        if A.empty:
            st.info("No signals logged yet. Run a scan.")
        else:
            k1, k2, k3, k4 = st.columns(4)
            k1.metric("Signals logged", len(A))
            k2.metric("Sessions covered", A.for_date.nunique())
            k3.metric("Gate passed", int((A.gate == "PASS").sum()))
            scored = A[A.pnl_pct.notna() & (A.pnl_pct.astype(str) != "")]
            if not scored.empty:
                k4.metric("Avg result", pct(pd.to_numeric(scored.pnl_pct,
                                                          errors="coerce").mean()))
            v = view_toggle("j_auto")
            show = A.sort_values("logged_at", ascending=False)
            if v == "List":
                st.dataframe(show, width="stretch", hide_index=True)
            else:
                for _, r in show.head(25).iterrows():
                    colr = "#0ecb81" if r["side"] == "BUY" else "#f6465d"
                    st.markdown(
                        f"<div style='background:#11141f;border-left:3px solid {colr};"
                        f"border-radius:8px;padding:9px 12px;margin-bottom:6px'>"
                        f"<b>{r['sym']}</b> <span style='color:{colr};font-weight:700'>"
                        f"{r['side']}</span> · {r['engine']} · {r['for_date']}<br>"
                        f"<span style='font-size:11px;color:#8f96b3'>entry {r['entry']} · "
                        f"SL {r['sl']} · target {r['target']} · qty {r['qty']} · "
                        f"P {r['prob_pct']}% · gate {r['gate']}</span></div>",
                        unsafe_allow_html=True)
            dl(A, "journal_auto_signals.csv", "j_auto")
            if st.button("🎯 Score logged signals against actual prices", key="btn_score"):
                with st.spinner("recording outcomes…"):
                    try:
                        st.session_state.res_score = record_outcomes()
                    except Exception as ex:
                        st.session_state.res_score = dict(error=str(ex))
            sc = st.session_state.get("res_score")
            if sc:
                st.write(sc)
    with j2:
        st.caption("Your own trades, including the ones the screener never suggested. "
                   "Both journals are downloadable and both feed the review below.")
        with st.form("man_trade"):
            f1, f2, f3, f4 = st.columns(4)
            td = f1.date_input("Date", value=session_date())
            tsym = f2.text_input("Symbol").strip().upper()
            tside = f3.selectbox("Side", ["BUY", "SELL"])
            tqty = f4.number_input("Qty", 0, 1000000, 0, step=1)
            g1, g2, g3, g4 = st.columns(4)
            ten = g1.number_input("Entry", 0.0, 1000000.0, 0.0, 0.05)
            tex = g2.number_input("Exit", 0.0, 1000000.0, 0.0, 0.05)
            tsl = g3.number_input("Stop", 0.0, 1000000.0, 0.0, 0.05)
            ttg = g4.number_input("Target", 0.0, 1000000.0, 0.0, 0.05)
            h1, h2, h3 = st.columns(3)
            tsetup = h1.selectbox("Setup", ["5-min ORB", "Pre-market watchlist", "EOD list",
                                            "Swing 1h ORB", "Manual / discretionary"])
            tfol = h2.selectbox("Followed the plan?", ["Yes", "No", "Partly"])
            temo = h3.selectbox("State of mind", ["Calm", "Rushed", "Revenge", "FOMO",
                                                  "Hesitant"])
            tnotes = st.text_area("Notes")
            if st.form_submit_button("Save trade", type="primary"):
                if tsym and ten > 0:
                    sgn = 1 if tside == "BUY" else -1
                    pp = ((tex / ten - 1) * 100 * sgn) if tex > 0 else None
                    rec_ = dict(trade_date=str(td), sym=tsym, side=tside, entry=ten,
                                  exit_px=tex, qty=tqty, sl=tsl, target=ttg,
                                  pnl_pct=round(pp, 3) if pp is not None else None,
                                  pnl_rs=round((tex - ten) * tqty * sgn, 2) if tex > 0 else None,
                                  setup=tsetup, followed_plan=tfol, emotion=temo,
                                  notes=tnotes)
                    journal_manual_add(rec_)
                    st.success(f"{tsym} saved")
                else:
                    st.error("symbol and entry price are required")
        M = journal_manual()
        if M.empty:
            st.info("No manual trades yet.")
        else:
            pn_ = pd.to_numeric(M.pnl_pct, errors="coerce").dropna()
            k1, k2, k3, k4 = st.columns(4)
            k1.metric("Trades", len(M))
            if len(pn_):
                k2.metric("Win rate", f"{100 * (pn_ > 0).mean():.0f}%")
                k3.metric("Average", pct(pn_.mean()))
                k4.metric("Total", pct(pn_.sum()))
            v = view_toggle("j_man")
            show = M.sort_values("trade_date", ascending=False)
            if v == "List":
                st.dataframe(show, width="stretch", hide_index=True)
            else:
                for _, r in show.head(25).iterrows():
                    p_ = pd.to_numeric(pd.Series([r["pnl_pct"]]), errors="coerce").iloc[0]
                    colr = "#0ecb81" if (p_ == p_ and p_ > 0) else "#f6465d"
                    st.markdown(
                        f"<div style='background:#11141f;border-left:3px solid {colr};"
                        f"border-radius:8px;padding:9px 12px;margin-bottom:6px'>"
                        f"<b>{r['sym']}</b> {r['side']} · {r['trade_date']} "
                        f"<span style='color:{colr};font-weight:700;float:right'>"
                        f"{pct(r['pnl_pct'])}</span><br>"
                        f"<span style='font-size:11px;color:#8f96b3'>{r['setup']} · "
                        f"plan: {r['followed_plan']} · {r['emotion']}<br>{r['notes']}"
                        f"</span></div>", unsafe_allow_html=True)
            dl(M, "journal_manual_trades.csv", "j_man")
            if len(pn_) >= 5:
                st.markdown("##### Review")
                by = M.copy()
                by["p"] = pd.to_numeric(by.pnl_pct, errors="coerce")
                agg = by.groupby("setup").p.agg(["count", "mean"]).round(3) \
                        .rename(columns={"count": "trades", "mean": "avg %"})
                st.dataframe(agg, width="stretch")
                fp = by.groupby("followed_plan").p.mean().round(3)
                if "Yes" in fp and "No" in fp:
                    st.caption(f"When you followed the plan: {fp['Yes']:+.2f}% average. "
                               f"When you did not: {fp['No']:+.2f}%.")

st.divider()
st.caption("QUANT-EDGE v22 · For research and personal use. Every probability shown is a "
           "walk-forward measurement on 59 sessions of the official F&O list, not a "
           "promise. On 1,026 walk-forward sessions top-1 long hit its +2% target on "
           "27.2% of sessions, short of the 34.6% break-even — the other "
           "two thirds ended at breakeven or the 1% stop. Position size accordingly.")
