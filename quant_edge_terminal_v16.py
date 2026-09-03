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
# v22 UPGRADE BLOCK — multi-timeframe, time gates, deeper history
# =============================================================================
# 8a) Intraday: Daily (macro levels) + 1-Hour (immediate context) for context,
#     5-min & 15-min ORB for execution.
# 8b) Intraday: Daily (full-day structure) for context, 1-Hour & 2-Hour ORB for
#     execution.
# Both profiles are offered on the Pre-Market, EOD, Live ORB and Replay screens.
ORB_TF_CHOICES = {
    "5-min ORB  (scalp / fastest confirmation)": 5,
    "15-min ORB  (balanced, fewer false breaks)": 15,
    "1-hour ORB  (full-day structure)": 60,
    "2-hour ORB  (positional / swing entry)": 120,
}
# minutes -> label, for selectbox rendering
ORB_TF_LABEL = {v: k for k, v in ORB_TF_CHOICES.items()}

# v22 (8) — the two analysis profiles, exactly as specified:
#   A) INTRADAY : Daily (macro levels) + 1-Hour (immediate context)
#                 -> execution on the 5-minute OR 15-minute opening range
#   B) SWING    : Daily (full-day structures)
#                 -> execution on the 1-hour OR 2-hour opening range
# Profile A drives the Pre-Market, Live and EOD screeners and the intraday
# backtest. Profile B drives the Swing tab and the longer-history backtest.
MTF_PROFILES = {
    "A · INTRADAY — Daily + 1-Hour context → 5m / 15m execution": dict(
        kind="intraday",
        context=("1d", "60m"), exec_tfs=[5, 15], default_exec=5,
        note="INTRADAY. Daily chart for the macro levels, 1-hour chart for the "
             "immediate context, execution and entry on the 5-minute or 15-minute "
             "opening range."),
    "B · SWING — Daily context → 1-Hour / 2-Hour execution": dict(
        kind="swing",
        context=("1d",), exec_tfs=[60, 120], default_exec=60,
        note="SWING. Daily chart for the full-day structures, execution and entry "
             "on the 1-hour or 2-hour opening range."),
}
PROFILE_INTRADAY = list(MTF_PROFILES.keys())[0]
PROFILE_SWING = list(MTF_PROFILES.keys())[1]

CFG.update(
    # --- 7) time gates: quality over quantity -------------------------------
    ORB_CUTOFF_TIME      = dtime(10, 0),    # no ORB break taken after 10:00
    MOMENTUM_START_TIME  = dtime(12, 30),   # after 12:30 only momentum names
    ENFORCE_TIME_GATES   = True,
    MOMENTUM_TOP_N       = 3,
    MOMENTUM_MIN_MOVE    = 1.00,   # % from the open, minimum, to qualify
    MOMENTUM_MIN_POS     = 0.80,   # position inside the day range (0-1)
    MOMENTUM_TARGET_PCT  = 1.50,
    MOMENTUM_SL_PCT      = 0.80,
    # --- 8) multi-timeframe -------------------------------------------------
    HTF_INTERVAL         = "60m",
    HTF_PERIOD           = "180d",
    MTF_REQUIRE_ALIGN    = False,  # soft by default: shown, scored, not a veto
    SWING_ORB_MINUTES    = 60,     # 8b: swing entry range, 1-hour or 2-hour
    # --- 9) deeper history --------------------------------------------------
    DAILY_HISTORY        = "750d",   # was 400d
    SWING_HISTORY        = "900d",
    REPLAY_5M_DAYS       = 60,       # provider cap for 5-minute bars
    REPLAY_60M_DAYS      = 700,      # 60-minute bars reach much further back
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


# --------------------------------------------------------------------------- #
# v22 (1) TIMESTAMPS — one clock format used absolutely everywhere, so a signal
# time and a stop/target hit time can be compared at a glance.
# --------------------------------------------------------------------------- #
def fmt_clock(ts=None):
    """'09:35:00' style wall clock in IST. Accepts datetime / Timestamp / None."""
    try:
        if ts is None:
            ts = get_ist_now()
        t = pd.Timestamp(ts)
        if t.tzinfo is None:
            try:
                t = t.tz_localize(IST)
            except Exception:
                pass
        else:
            t = t.tz_convert(IST)
        return t.strftime("%H:%M:%S")
    except Exception:
        return "—"


def fmt_stamp(ts=None):
    """'28 Aug 09:35:00' — date + clock, for rows that can span sessions."""
    try:
        if ts is None:
            ts = get_ist_now()
        t = pd.Timestamp(ts)
        if t.tzinfo is not None:
            t = t.tz_convert(IST)
        return t.strftime("%d %b %H:%M:%S")
    except Exception:
        return "—"


# --------------------------------------------------------------------------- #
# v22 (2) CLEAN NUMBERS — 12123 not 12123.000000, 19.27 not 19.270000.
# `nz` rounds and drops a meaningless ".0"; the table renderer additionally
# applies a %.10g display format so float columns never print trailing zeros.
# --------------------------------------------------------------------------- #
def nz(v, r=2):
    """Round to `r` decimals and strip a trailing '.0'. Returns int/float/None."""
    try:
        if v is None or isinstance(v, bool):
            return v
        f = float(v)
    except (TypeError, ValueError):
        return v
    if not np.isfinite(f):
        return None
    rv = round(f, r)
    return int(rv) if abs(rv - round(rv)) < 1e-9 else rv


def ns(v, r=2, dash="—"):
    """String form of `nz` for HTML cards. 12123 / 19.27 / '—'."""
    x = nz(v, r)
    if x is None or x != x:
        return dash
    if isinstance(x, (int, np.integer)):
        return f"{int(x):,}"
    return f"{x:,.{r}f}".rstrip("0").rstrip(".")

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

# =============================================================================
# BACKGROUND SCAN JOBS  (fixes: "running one scan freezes every other tab")
# -----------------------------------------------------------------------------
# Streamlit runs the whole script on ONE thread per rerun. A button that does
# `with st.spinner(): heavy_work()` therefore blocks the ENTIRE page — every
# other tab, every other button — until heavy_work() returns, because nothing
# re-renders until the script finishes.
#
# Fix: start each scan in its own background thread and return control to
# Streamlit immediately. Progress/results live in this module-level dict
# (shared across reruns and across tabs, guarded by a lock). A small
# st_autorefresh timer polls it every 2s ONLY while something is running, so
# the page keeps updating itself without the user having to click anything,
# and every other button stays clickable the whole time.
# =============================================================================
SCAN_JOBS = {}
SCAN_JOBS_LOCK = threading.Lock()

def scan_job_start(key, fn, *args, **kwargs):
    """Kick off fn(*args, **kwargs) in a background thread under `key`.
    Returns False (no-op) if a job with this key is already running."""
    with SCAN_JOBS_LOCK:
        existing = SCAN_JOBS.get(key)
        if existing and existing.get("status") == "running":
            return False
        SCAN_JOBS[key] = {"status": "running", "result": None, "error_msg": None,
                          "started": time.time(), "finished": None}

    def _worker():
        try:
            result = fn(*args, **kwargs)
            with SCAN_JOBS_LOCK:
                SCAN_JOBS[key]["status"] = "done"
                SCAN_JOBS[key]["result"] = result
                SCAN_JOBS[key]["finished"] = time.time()
        except Exception as e:
            with SCAN_JOBS_LOCK:
                SCAN_JOBS[key]["status"] = "error"
                SCAN_JOBS[key]["error_msg"] = f"{type(e).__name__}: {e}"
                SCAN_JOBS[key]["finished"] = time.time()

    threading.Thread(target=_worker, daemon=True, name=f"scan-{key}").start()
    return True

def scan_job_get(key):
    with SCAN_JOBS_LOCK:
        j = SCAN_JOBS.get(key)
        return dict(j) if j else {"status": "idle"}

def scan_job_any_running():
    with SCAN_JOBS_LOCK:
        return any(j.get("status") == "running" for j in SCAN_JOBS.values())

def scan_button(label, key, fn, *args, help_running="scanning…", **kwargs):
    """Drop-in replacement for the old `if st.button(...): with st.spinner(...): ...`
    pattern. Renders the button (disabled while its own job is running so it
    can't be double-clicked), starts the job in the background on click, and
    returns the finished result dict once ready (or None). Never blocks other
    tabs/buttons — that is the whole point."""
    job = scan_job_get(key)
    running = job.get("status") == "running"
    clicked = st.button(label, type="primary", key=f"btn_{key}", disabled=running)
    if clicked and not running:
        scan_job_start(key, fn, *args, **kwargs)
        st.rerun()
    if running:
        elapsed = int(time.time() - job.get("started", time.time()))
        st.info(f"⏳ {help_running} ({elapsed}s) — other tabs/scans keep working, "
                f"this box will update on its own.")
    elif job.get("status") == "error":
        st.error(f"Scan failed: {job.get('error_msg')}")
    return job.get("result") if job.get("status") == "done" else None

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
def bar_minutes(g, default=5):
    """Infer the bar size of an intraday frame in minutes (5m / 15m / 60m ...).

    v22 can be fed 5-minute bars (recent sessions) or 60-minute bars (deep
    history, where the provider no longer keeps 5-minute data), so nothing
    downstream may assume a fixed bar width any more.
    """
    try:
        idx = pd.DatetimeIndex(g.index)
        if len(idx) < 2:
            return default
        step = pd.Series(idx).diff().dropna().dt.total_seconds().median() / 60.0
        return int(round(step)) if step and step > 0 else default
    except Exception:
        return default


def orb_from_bars(g, minutes=5, bar_min=None):
    """g: intraday bars for one ticker/day, ascending, from 09:15.
    `minutes` is the opening-range length you want (5 / 15 / 60 / 120).
    `bar_min` is the width of one bar in the frame; inferred when omitted.
    Returns ORB candle stats + the current close-confirmed break state."""
    if g is None or len(g) < 1:
        return None
    g = g.sort_index()
    bm = int(bar_min or bar_minutes(g))
    nbars = max(1, int(round(minutes / max(1, bm))))
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
             orb_minutes=int(minutes), bar_min=bm,
             orb_end_time=fmt_clock(g.index[nbars - 1]),
             L_trig=0, S_trig=0, L_trig_min=np.nan, S_trig_min=np.nan,
             L_entry=np.nan, S_entry=np.nan, L_trig_time=None, S_trig_time=None,
             L_trig_clock=None, S_trig_clock=None)
    if len(rest) == 0:
        return r
    Cc = rest.Close.to_numpy(float)
    t0 = g.index[0]
    up = Cc > oh
    if up.any():
        i = int(np.argmax(up))
        r["L_trig"] = 1
        r["L_entry"] = float(Cc[i])
        r["L_trig_min"] = int((rest.index[i] - t0).total_seconds() // 60) + bm
        r["L_trig_time"] = rest.index[i]
        r["L_trig_clock"] = fmt_clock(rest.index[i])
    dn = Cc < ol
    if dn.any():
        i = int(np.argmax(dn))
        r["S_trig"] = 1
        r["S_entry"] = float(Cc[i])
        r["S_trig_min"] = int((rest.index[i] - t0).total_seconds() // 60) + bm
        r["S_trig_time"] = rest.index[i]
        r["S_trig_clock"] = fmt_clock(rest.index[i])
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
def load_bundle(force=False, days=None, fast=False):
    """Daily panel + features + pivots + Nifty context for the whole universe.

    v22 (9): the default daily history is CFG['DAILY_HISTORY'] (750 sessions,
    up from 400) so every percentile, ADR, 52-week distance and pivot is
    measured on roughly three years instead of eighteen months.
    """
    days = days or CFG["DAILY_HISTORY"]
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
             days=days, n_sessions=int(len(C)),
             built_at=get_ist_now().isoformat(), built_clock=fmt_ist())
    CACHE.set(key, b, 900)
    return b


# =============================================================================
# v22 (8) MULTI-TIMEFRAME CONTEXT  —  Daily (macro) + 1-Hour (immediate)
# The execution timeframe is the ORB (5m / 15m / 1h / 2h); the CONTEXT layer
# below is what decides whether that break is with or against the higher
# timeframes. It is computed once per scan and attached to every candidate, so
# the same field shows up on Pre-Market, Live ORB, EOD and Replay.
# =============================================================================
def load_htf(names, interval=None, period=None, ttl=900):
    """1-hour (or configured) bars for the watchlist, keyed by ticker."""
    interval = interval or CFG["HTF_INTERVAL"]
    period = period or CFG["HTF_PERIOD"]
    key = ("htf", interval, period, len(names), names[0] if names else "")
    hit = CACHE.get(key)
    if hit is not None:
        return hit
    raw = download(list(names), period=period, interval=interval, ttl=ttl) or {}
    CACHE.set(key, raw, ttl)
    return raw


def _htf_row(g):
    """Trend state of one higher-timeframe frame."""
    try:
        c = pd.Series(g["Close"]).dropna()
        if len(c) < 30:
            return None
        e20 = ema(c, 20).iloc[-1]
        e50 = ema(c, 50).iloc[-1] if len(c) >= 50 else e20
        last = float(c.iloc[-1])
        r = rsi(c, 14).iloc[-1]
        lo = float(c.tail(40).min())
        hi = float(c.tail(40).max())
        pos = (last - lo) / (hi - lo) * 100 if hi > lo else 50.0
        dirn = 1 if (last > e20 and e20 >= e50) else (-1 if (last < e20 and e20 <= e50) else 0)
        return dict(htf_last=last, htf_dir=int(dirn), htf_rsi=float(r),
                    htf_vs_e20=float((last / e20 - 1) * 100) if e20 else np.nan,
                    htf_pos=float(pos))
    except Exception:
        return None


def mtf_context(bundle, names, interval=None, use_htf=True):
    """Per-ticker dict: daily direction, 1-hour direction and the combined label.

    Profile A (INTRADAY) wants Daily + 1-Hour; profile B (SWING) wants Daily only.
    `use_htf=False`
    gives the Daily-only view without paying for the hourly download.
    """
    out = {}
    htf = load_htf(names, interval=interval) if (use_htf and names) else {}
    for t in names:
        snap_t = (bundle["snap"].get(t, {}) or {})
        d_dir = 1 if (snap_t.get("st_dir", 0) or 0) > 0 else (
            -1 if (snap_t.get("st_dir", 0) or 0) < 0 else 0)
        if (snap_t.get("px_vs_e20") or 0) > 0 and d_dir == 0:
            d_dir = 1
        h = _htf_row(htf.get(t)) if use_htf else None
        h_dir = h["htf_dir"] if h else 0
        if not use_htf:
            label = "DAILY UP" if d_dir > 0 else ("DAILY DOWN" if d_dir < 0 else "DAILY FLAT")
            score = 1 if d_dir > 0 else (-1 if d_dir < 0 else 0)
        elif d_dir > 0 and h_dir > 0:
            label, score = "ALIGNED UP", 2
        elif d_dir < 0 and h_dir < 0:
            label, score = "ALIGNED DOWN", -2
        elif d_dir == 0 or h_dir == 0:
            label, score = "PARTIAL", (d_dir + h_dir)
        else:
            label, score = "CONFLICT", 0
        rec = dict(d_dir=d_dir, htf_dir=h_dir, mtf=label, mtf_score=score,
                   d_vs_e20=nz(snap_t.get("px_vs_e20")), d_rsi=nz(snap_t.get("rsi")))
        if h:
            rec.update(htf_rsi=nz(h["htf_rsi"]), htf_vs_e20=nz(h["htf_vs_e20"]),
                       htf_pos=nz(h["htf_pos"], 0))
        out[t] = rec
    return out


def mtf_ok(rec, side, require=None):
    """Is this trade with the higher timeframes? Soft by default — the label is
    always shown, and only becomes a veto when MTF_REQUIRE_ALIGN is on."""
    require = CFG["MTF_REQUIRE_ALIGN"] if require is None else require
    if not rec:
        return (not require), "no MTF data"
    lbl = rec.get("mtf", "")
    want_up = side in ("L", "BUY")
    good = (lbl in ("ALIGNED UP", "DAILY UP") if want_up
            else lbl in ("ALIGNED DOWN", "DAILY DOWN"))
    if good:
        return True, ""
    why = f"higher timeframes say {lbl or 'unknown'}"
    return (not require), why

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
def build_ticket(sym, side, entry, sl_pct, capital, target_pct=None, partial=True,
                 partial_pct=None, generated_at=None, setup=None, tf=None):
    """v22: every ticket now carries (1) the time it was generated and (6) the
    risk/reward ratio on both targets, plus a `target` alias so the journal and
    the tables read the same field."""
    tp = target_pct if target_pct is not None else CFG["TARGET_PCT"]
    pp = partial_pct if partial_pct is not None else CFG["PARTIAL_PCT"]
    sgn = 1 if side == "BUY" else -1
    sl = entry * (1 - sgn * sl_pct / 100)
    t1 = entry * (1 + sgn * pp / 100)
    t2 = entry * (1 + sgn * tp / 100)
    risk_amt = capital * CFG["RISK_PER_TRADE_PCT"] / 100
    per_share = abs(entry - sl)
    qty = int(max(1, risk_amt // per_share)) if per_share > 0 else 0
    gen = generated_at or get_ist_now()
    rr2 = round(abs(t2 - entry) / per_share, 2) if per_share else 0
    rr1 = round(abs(t1 - entry) / per_share, 2) if per_share else 0
    return dict(symbol=sym.replace(".NS", ""), side=side, entry=nz(entry),
                sl=nz(sl), sl_pct=nz(sl_pct),
                t1=nz(t1), t2=nz(t2), target=nz(t2), target_pct=nz(tp),
                partial_pct=nz(pp),
                qty=qty, risk_rs=nz(qty * per_share, 0),
                reward_rs=nz(qty * abs(t2 - entry), 0),
                risk_per_share=nz(per_share), rr=rr2, rr_t1=rr1,
                rr_txt=f"1:{rr2:g}",
                generated_at=fmt_ist(gen), signal_time=fmt_clock(gen),
                signal_date=str(pd.Timestamp(gen).date()),
                setup=setup or "", tf=tf or "",
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

def scan_premarket(capital=None, bundle=None, use_nse=True, orb_minutes=None,
                   profile=None):
    """v22 (8): the pre-market watchlist is now built with the same Daily + 1-Hour
    context layer as the live screener, and it states which ORB timeframe the
    plan assumes (5m / 15m / 1h / 2h)."""
    capital = capital or CFG["DEFAULT_CAPITAL"]
    b = bundle or load_bundle()
    if not b:
        return dict(error="no data")
    om = int(orb_minutes or CFG["ORB_MINUTES"])
    prof = profile or list(MTF_PROFILES.keys())[0]
    use_htf = "60m" in (MTF_PROFILES.get(prof, {}).get("context") or ())
    gen_at = get_ist_now()
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
    MTF = mtf_context(b, names, use_htf=use_htf)
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
        mt = MTF.get(t) or {}
        # multi-timeframe tilt: a daily+1h aligned name gets a real bonus, a
        # name fighting both higher timeframes gets penalised.
        mscore = (mt.get("mtf_score") or 0)
        sL = cap * 10 + tilt_up + trend + min(room_up, 4.0) * 0.25 + 0.35 * mscore
        sS = cap * 10 - tilt_up - trend + min(room_dn, 4.0) * 0.25 - 0.35 * mscore
        rows.append(dict(ticker=t, sym=t.replace(".NS", ""), sector=sector_of(t),
                         mtf=mt.get("mtf"), mtf_score=mscore,
                         htf_rsi=mt.get("htf_rsi"), htf_vs_e20=mt.get("htf_vs_e20"),
                         signal_time=fmt_clock(gen_at), generated_at=fmt_ist(gen_at),
                         orb_tf=om, orb_tf_label=f"{om}-min ORB",
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
    out = dict(source=src, ts=get_ist_now().isoformat(), ts_clock=fmt_ist(gen_at),
               generated_at=fmt_ist(gen_at), signal_time=fmt_clock(gen_at),
               regime=b["ctx"]["regime"], orb_minutes=om, profile=prof,
               profile_note=(MTF_PROFILES.get(prof, {}) or {}).get("note", ""),
               history_sessions=b.get("n_sessions"),
               n_candidates=int(len(D)), longs=[], shorts=[], table=D)
    for _, r in longs.iterrows():
        entry_hint = r.exp_open * (1 + min(r.atr_pct * 0.12, 0.45) / 100)
        out["longs"].append(dict(row=r.to_dict(),
                                 ticket=build_ticket(r.ticker, "BUY", entry_hint,
                                                     CFG["SL_CAP_PCT"], capital,
                                                     generated_at=gen_at,
                                                     setup="PRE-MARKET",
                                                     tf=f"{om}-min ORB")))
    for _, r in shorts.iterrows():
        entry_hint = r.exp_open * (1 - min(r.atr_pct * 0.12, 0.45) / 100)
        out["shorts"].append(dict(row=r.to_dict(),
                                  ticket=build_ticket(r.ticker, "SELL", entry_hint,
                                                      CFG["SL_CAP_PCT"], capital,
                                                      generated_at=gen_at,
                                                      setup="PRE-MARKET",
                                                      tf=f"{om}-min ORB")))
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
def scan_eod(capital=None, bundle=None, top_n=5, orb_minutes=None, profile=None):
    """v22 (8): tomorrow's list is ranked with the Daily + 1-Hour context layer and
    states the ORB timeframe the execution plan assumes."""
    capital = capital or CFG["DEFAULT_CAPITAL"]
    b = bundle or load_bundle()
    if not b:
        return dict(error="no data")
    om = int(orb_minutes or CFG["ORB_MINUTES"])
    prof = profile or list(MTF_PROFILES.keys())[0]
    use_htf = "60m" in (MTF_PROFILES.get(prof, {}).get("context") or ())
    gen_at = get_ist_now()
    names = liquid_names(b)
    MTF = mtf_context(b, names, use_htf=use_htf)
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
        mt = MTF.get(t) or {}
        rows.append(dict(ticker=t, sym=t.replace(".NS", ""), sector=sector_of(t),
                         mtf=mt.get("mtf"), mtf_score=mt.get("mtf_score"),
                         htf_rsi=mt.get("htf_rsi"), htf_vs_e20=mt.get("htf_vs_e20"),
                         signal_time=fmt_clock(gen_at), generated_at=fmt_ist(gen_at),
                         orb_tf=om, orb_tf_label=f"{om}-min ORB",
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
        # planned tickets for BOTH directions, so the R:R is visible tonight
        ref = float(r.close or 0) or 1.0
        tk_l = build_ticket(r.ticker, "BUY", ref, CFG["SL_CAP_PCT"], capital,
                            generated_at=gen_at, setup="EOD PLAN", tf=f"{om}-min ORB")
        tk_s = build_ticket(r.ticker, "SELL", ref, CFG["SL_CAP_PCT"], capital,
                            generated_at=gen_at, setup="EOD PLAN", tf=f"{om}-min ORB")
        plan.append(dict(row=r.to_dict(), pivot_D=d, pivot_W=piv.get("W", {}), pivot_M=piv.get("M", {}),
                         ticket=tk_l, ticket_short=tk_s, rr=tk_l["rr"],
                         mtf=r.to_dict().get("mtf"),
                         signal_time=fmt_clock(gen_at),
                         note=(f"Trade the {om}-min ORB in EITHER direction. Long above the "
                               f"{om}-min high, short below the {om}-min low, whichever closes "
                               f"first, and only before {CFG['ORB_CUTOFF_TIME'].strftime('%H:%M')}. "
                               f"Cap risk at {CFG['SL_CAP_PCT']}%. Higher timeframes: "
                               f"{r.to_dict().get('mtf') or 'n/a'}.")))
    out = dict(ts=get_ist_now().isoformat(), ts_clock=fmt_ist(gen_at),
               generated_at=fmt_ist(gen_at), signal_time=fmt_clock(gen_at),
               for_date=str(next_trading_day()), orb_minutes=om, profile=prof,
               profile_note=(MTF_PROFILES.get(prof, {}) or {}).get("note", ""),
               history_sessions=b.get("n_sessions"),
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
def scan_swing(capital=None, bundle=None, top_n=None, orb_minutes=None, profile=None):
    """v22 (8b) — SWING profile: the Daily chart supplies the full-day structure and
    the entry is the Monday 1-HOUR or 2-HOUR opening range, selectable.
    """
    capital = capital or CFG["DEFAULT_CAPITAL"]
    top_n = top_n or CFG["SWING_TOP_N"]
    prof = profile or PROFILE_SWING
    _exec = MTF_PROFILES.get(prof, {}).get("exec_tfs") or [60, 120]
    orb_minutes = int(orb_minutes or CFG["SWING_ORB_MINUTES"])
    if orb_minutes not in _exec:
        orb_minutes = _exec[0]
    tf_label = ORB_TF_LABEL.get(orb_minutes, f"{orb_minutes} min ORB")
    b = bundle or load_bundle(days=CFG["SWING_HISTORY"])   # v22 (9): 900d, was 500d
    if not b:
        return dict(error="no data")
    gen_at = get_ist_now()
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
                          target_pct=CFG["SWING_TARGET_PCT"], partial=True,
                          partial_pct=3.0, generated_at=gen_at,
                          setup="SWING", tf=f"{tf_label} (Monday)")
        tk["ladder"] = [dict(pct=3.0, px=round(ent_hint * 1.03, 2), book="33%"),
                        dict(pct=5.0, px=round(ent_hint * 1.05, 2), book="33%"),
                        dict(pct=10.0, px=round(ent_hint * 1.10, 2), book="rest")]
        plan.append(dict(row=r.to_dict(), ticket=tk, pivots=r.pivots))
    out = dict(ts=get_ist_now().isoformat(), ts_clock=fmt_ist(gen_at),
               generated_at=fmt_ist(gen_at), signal_time=fmt_clock(gen_at),
               history_sessions=b.get("n_sessions"),
               for_week=str(next_trading_day()),
               regime=b["ctx"]["regime"], top=plan, table=D,
               profile=prof, profile_note=(MTF_PROFILES.get(prof, {}) or {}).get("note", ""),
               orb_minutes=orb_minutes, orb_tf=tf_label,
               entry_rule=(f"MONDAY: wait for the first {orb_minutes}-minute candle "
                           f"(09:15–{'10:15' if orb_minutes == 60 else '11:15'}) to complete. "
                           "Place a BUY stop-limit just above that candle's HIGH. "
                           "Stop -5% from entry (NOT the ORB low - too tight). "
                           "Book 33% at +3%, 33% at +5%, trail the rest for +10%."))
    save_scan("swing", out)
    return out

# =============================================================================
# ENGINE 5 — AGGRESSIVE MOVERS  (4-5% F&O gainers/losers, fixed 1:2 R:R)
# -----------------------------------------------------------------------------
# Same feature family as the calibrated EOD engine (ATR%, ADR20, 20-day volume
# expansion, count of big moves, RVOL) but ranks only the TOP DECILE instead of
# the top ~20%, aiming at the handful of names that actually run 4-5% on a
# given day. Every ticket is forced to a hard 1% risk : 2% reward, as asked.
#
# IMPORTANT — UNLIKE the rest of this app, this engine has NOT been
# walk-forward validated at the 4-5% threshold (only the ~2% target was
# calibrated on real data). It reuses proven capability features but the
# threshold itself is a reasonable extrapolation, not a measured result.
# Treat it as PAPER-TRADE ONLY until you have logged real outcomes for it
# in the Journal tab and it has a track record of its own.
# =============================================================================
def scan_aggressive_movers(capital=None, bundle=None, decile=0.10, min_names=3):
    capital = capital or CFG["DEFAULT_CAPITAL"]
    b = bundle or load_bundle()
    if not b:
        return dict(error="no data")
    names = liquid_names(b)
    rows = []
    for t in names:
        f = b["snap"].get(t, {})
        okL, _ = feasible(f, "L")
        if not okL:
            continue
        atrp = f.get("atr_pct", 0) or 0
        adr = f.get("adr20", 0) or 0
        vol20 = f.get("vol20", 0) or 0
        big20 = f.get("big_moves_60", 0) or 0
        rvol = f.get("rvol", 1) or 1
        px = f.get("px")
        if not px:
            continue
        score = (0.30 * min(atrp / 4.0, 2.0) + 0.25 * min(adr / 4.5, 2.0)
                 + 0.20 * min(vol20 / 4.0, 2.0) + 0.15 * min(big20 / 10.0, 2.0)
                 + 0.10 * min(rvol / 2.0, 2.0))
        rows.append(dict(ticker=t, sym=t.replace(".NS", ""), sector=sector_of(t),
                         score=round(score, 3), atr_pct=atrp, adr20=adr, vol20=vol20,
                         big20=big20, rvol=rvol, px=px))
    if not rows:
        return dict(error="no candidates passed the volatility floor")
    D = pd.DataFrame(rows).sort_values("score", ascending=False)
    cut = max(min_names, int(len(D) * decile))
    top_rows = D.head(cut).to_dict("records")
    out_top = []
    for r in top_rows:
        entry = r["px"]
        tk_long = build_ticket(r["ticker"], "BUY", entry, sl_pct=1.0, target_pct=2.0,
                               capital=capital, setup="Aggressive (unvalidated)", tf="Day")
        tk_short = build_ticket(r["ticker"], "SELL", entry, sl_pct=1.0, target_pct=2.0,
                                capital=capital, setup="Aggressive (unvalidated)", tf="Day")
        out_top.append(dict(row=r, ticket=tk_long, ticket_short=tk_short))
    out = dict(ts=get_ist_now().isoformat(), generated_at=fmt_ist(get_ist_now()),
              n_candidates=int(len(D)), top=out_top, table=D)
    save_scan("aggressive", out)
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
# v22 (1)(6): signal_time / hit_time / exit_time and the risk-reward ratio are
# first-class journal columns now, so the record answers "when" as well as "what".
AUTO_COLS = ["logged_at", "for_date", "signal_time", "engine", "setup", "orb_tf",
             "mtf", "sym", "sector", "side", "entry", "sl",
             "sl_pct", "target", "target_pct", "rr", "risk_rs", "reward_rs", "qty",
             "prob_pct", "p3_pct", "trig_min", "trig_time",
             "orb_h", "orb_l", "gate", "note", "outcome", "hit_time", "exit_time",
             "exit_px", "pnl_pct"]
MAN_COLS = ["trade_date", "entry_time", "exit_time", "sym", "side", "entry", "exit_px",
            "qty", "sl", "target", "rr", "pnl_pct", "pnl_rs", "setup",
            "followed_plan", "emotion", "notes"]
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
            logged_at=fmt_ist(), for_date=for_date,
            signal_time=(r.get("signal_time") or tk.get("signal_time")
                         or r.get("trig_clock") or fmt_clock()),
            engine=engine, setup=tk.get("setup") or r.get("setup") or engine,
            orb_tf=r.get("orb_tf") or tk.get("tf") or "", mtf=r.get("mtf") or "",
            sym=sym,
            sector=r.get("sector"), side=side, entry=nz(tk.get("entry") or r.get("entry")),
            sl=nz(tk.get("sl")), sl_pct=nz(r.get("sl_pct")), target=nz(tk.get("target")),
            target_pct=nz(tk.get("target_pct")), rr=tk.get("rr"),
            risk_rs=nz(tk.get("risk_rs"), 0), reward_rs=nz(tk.get("reward_rs"), 0),
            qty=tk.get("qty"),
            prob_pct=nz(r.get("prob_pct"), 1), p3_pct=nz(r.get("p3_pct"), 1),
            trig_min=nz(r.get("trig_min"), 0),
            trig_time=r.get("trig_clock") or fmt_clock(r.get("trig_time")),
            orb_h=nz(r.get("orb_h")), orb_l=nz(r.get("orb_l")),
            gate="PASS" if r.get("gate_pass", True) else "FAIL",
            note=r.get("gate_note") or "", outcome=r.get("rp_result") or "",
            hit_time=r.get("rp_hit_time") or "", exit_time=r.get("rp_exit_time") or "",
            exit_px=nz(r.get("rp_exit_px")) or "", pnl_pct=nz(r.get("rp_pnl_pct"), 3) or ""))
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
# ENGINE 2 (v22) — LIVE 5-MIN ORB with the confluence gate. Overrides p3_scan.
# =============================================================================
def scan_live_orb(capital=None, bundle=None, watch=None, orb_minutes=None,
                  top_n=None, bars=None, as_of=None, log=True, profile=None,
                  enforce_time_gates=None, require_mtf=None, now_time=None):
    """v22 live ORB screener.

    New in v22:
      (1) every candidate carries the wall-clock time the break was confirmed
          and the time the ticket was generated;
      (6) the risk/reward ratio travels with the ticket;
      (7) TIME GATES — no ORB break is accepted after CFG['ORB_CUTOFF_TIME']
          (10:00). Between 10:00 and 12:30 the screener deliberately returns
          nothing. After CFG['MOMENTUM_START_TIME'] (12:30) it switches to the
          momentum engine instead of forcing a stale opening-range trade;
      (8) multi-timeframe context (Daily + 1-Hour) is attached to every row and
          the ORB length is selectable (5 / 15 / 60 / 120 minutes).
    """
    capital = capital or CFG["DEFAULT_CAPITAL"]
    top_n = CFG["INTRADAY_TOP_N"] if top_n is None else top_n
    om = int(orb_minutes or CFG["ORB_MINUTES"])
    gates = CFG["ENFORCE_TIME_GATES"] if enforce_time_gates is None else bool(enforce_time_gates)
    prof = profile or list(MTF_PROFILES.keys())[0]
    use_htf = "60m" in (MTF_PROFILES.get(prof, {}).get("context") or ())
    b = bundle or load_bundle()
    if not b:
        return dict(error="no data")
    day = as_of or get_ist_now().date()
    if isinstance(day, str):
        day = pd.Timestamp(day).date()
    gen_at = get_ist_now()
    # A replay/backtest of a past session is NOT bound by the wall clock: there the
    # gate is applied per break (a break after 10:00 is dropped above), and the
    # whole session is replayed. Only the live screener is clock-gated.
    live_mode = as_of is None
    clock = now_time or gen_at.time()
    orb_window_open = (not live_mode) or clock < CFG["ORB_CUTOFF_TIME"]
    momentum_window = live_mode and clock >= CFG["MOMENTUM_START_TIME"]

    if bars is None:
        names = watch or liquid_names(b)
        intraday = download(names, period="1d", interval="5m", ttl=45)
    else:
        intraday = bars
    nf5 = download([NIFTY], period="1d" if as_of is None else "5d", interval="5m", ttl=45).get(NIFTY)
    nfctx = nifty_context(nf5, b["nfd"])
    MTF = mtf_context(b, list(intraday.keys()), use_htf=use_htf)

    # --- pass 1: opening candle of every name -> sector strength context -------
    orbs, early = {}, {}
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
            o = orb_from_bars(g, om)
            if not o:
                continue
            orbs[t] = o
            pdc = b["prev"]["pdc"].get(t)
            if pdc:
                early[t.replace(".NS", "")] = (o["orb_c"] / pdc - 1) * 100
        except Exception:
            continue
    sec_ctx = sector_early_context(early)

    # --- pass 2: score every confirmed break ---------------------------------
    cands = []
    for t, orb in orbs.items():
        base = b["snap"].get(t, {}) or {}
        prev = dict(pdh=b["prev"]["pdh"].get(t), pdl=b["prev"]["pdl"].get(t),
                    pdc=b["prev"]["pdc"].get(t))
        piv_d = (b["piv"].get(t, {}) or {}).get("D", {}) or {}
        sym = t.replace(".NS", "")
        for side, mdl, mdl3, minp in (("L", MODEL_L, MODEL_L3, CFG["MIN_P_LONG"]),
                                      ("S", MODEL_S, MODEL_S3, CFG["MIN_P_SHORT"])):
            if not orb.get(f"{side}_trig") or mdl is None:
                continue
            gate_ok, gate_why = passes_confluence_gate(base, orb, side)
            gate_why = list(gate_why)
            # ---- (7) time gate: an opening-range break confirmed after 10:00
            # is not an ORB trade. Measured: a break inside 10 minutes reached
            # +2% on 14.9% of events, after 120 minutes only 4.2%.
            trig_ts = orb.get(f"{side}_trig_time")
            trig_clock = orb.get(f"{side}_trig_clock") or fmt_clock(trig_ts)
            late = False
            try:
                late = bool(pd.Timestamp(trig_ts).time() >= CFG["ORB_CUTOFF_TIME"])
            except Exception:
                late = False
            if late and gates:
                gate_ok = False
                gate_why.append(f"break confirmed at {trig_clock} — after the "
                                f"{CFG['ORB_CUTOFF_TIME'].strftime('%H:%M')} ORB cut-off")
            # ---- (8) multi-timeframe context
            mrec = MTF.get(t) or {}
            m_ok, m_why = mtf_ok(mrec, side, require=require_mtf)
            if not m_ok:
                gate_ok = False
                gate_why.append(m_why)
            fv = v22_features(base, orb, prev, piv_d, side, sec_ctx, sym)
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
                orb_range_pct=orb["orb_range_pct"], entry=conf_entry,
                level=orb["orb_h"] if side == "L" else orb["orb_l"],
                sl_pct=sl_pct, trig_min=orb.get(f"{side}_trig_min"),
                trig_time=orb.get(f"{side}_trig_time"), trig_clock=trig_clock,
                signal_time=trig_clock, generated_at=fmt_ist(gen_at),
                orb_tf=om, orb_tf_label=f"{om}-min ORB",
                orb_end_time=orb.get("orb_end_time"),
                mtf=mrec.get("mtf"), mtf_score=mrec.get("mtf_score"),
                htf_rsi=mrec.get("htf_rsi"), htf_vs_e20=mrec.get("htf_vs_e20"),
                late_break=late, last=orb["last"], moved=moved,
                gap=fv["gap"], atr_pct=base.get("atr_pct"), adr20=base.get("adr20"),
                rvol=base.get("rvol"), turn=base.get("turn_ma_cr"),
                sec_early=fv["sec_early"], sec_rel=fv["sec_rel"],
                sec_breadth=fv["sec_breadth"], stk_rel=fv["stk_rel"],
                orb_h_R1=fv["orb_h_R1"], above_R1=fv["above_R1"],
                below_S1=fv["below_S1"],
                gate_pass=gate_ok, gate_note="; ".join(gate_why),
                still_valid=bool(moved < CFG["TARGET_PCT"] * 0.6 and moved > -sl_pct),
                drivers=mdl.top_drivers(fv, 5),
                ticket=build_ticket(t, "BUY" if side == "L" else "SELL",
                                    conf_entry, sl_pct, capital,
                                    generated_at=(trig_ts if trig_ts is not None else gen_at),
                                    setup=f"{om}-min ORB", tf=f"{om}-min ORB"),
                rr=None,
                low_conf=(side == "S"),
                passed_p=bool(p >= minp),
            ))
            cands[-1]["rr"] = cands[-1]["ticket"]["rr"]
    D = pd.DataFrame(cands)
    mode = ("ORB REPLAY" if not live_mode else
            ("ORB" if orb_window_open else
             ("MOMENTUM" if momentum_window else "STAND ASIDE")))
    out = dict(ts=fmt_ist(), ts_clock=fmt_ist(gen_at), signal_time=fmt_clock(gen_at),
               for_date=str(day), regime=nfctx["regime"], nfctx=nfctx,
               orb_minutes=om, profile=prof,
               profile_note=(MTF_PROFILES.get(prof, {}) or {}).get("note", ""),
               history_sessions=b.get("n_sessions"),
               mode=mode, clock=clock.strftime("%H:%M"), time_gates=gates,
               orb_cutoff=CFG["ORB_CUTOFF_TIME"].strftime("%H:%M"),
               momentum_from=CFG["MOMENTUM_START_TIME"].strftime("%H:%M"),
               n_triggers=int(len(D)), top_n=top_n,
               sector_early=sec_ctx[1], mkt_early=sec_ctx[0],
               table=D, actionable=[], rejected=[], momentum=[])
    # `still_valid` only makes sense while the market is actually open: it asks
    # "has price already run away from the confirmed break?". Post-close and in
    # replay, `last` is the day's close, so enforcing it would reject everything
    # and the tab would look empty for the wrong reason.
    live_now = (as_of is None and market_phase() in ("ORB", "LIVE"))
    out["valid_enforced"] = bool(live_now)
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
        out["n_late_break"] = int(D.late_break.sum()) if "late_break" in D else 0

    # ---- (7) after 12:30 the ORB is stale: rank momentum instead -------------
    if gates and momentum_window:
        try:
            out["momentum"] = scan_momentum(capital=capital, bundle=b, bars=intraday,
                                            MTF=MTF, day=day, gen_at=gen_at,
                                            top_n=CFG["MOMENTUM_TOP_N"])
        except Exception as ex:
            out["momentum_error"] = str(ex)
    if gates and live_mode and not orb_window_open:
        # do not hand out opening-range tickets outside the opening-range window
        out["orb_suppressed"] = True
        out["actionable"] = []
        out["gate_message"] = (
            f"It is {out['clock']}. The opening-range window closed at "
            f"{out['orb_cutoff']}, so no ORB ticket is issued."
            + (f" Momentum names are listed instead ({len(out['momentum'])} qualified)."
               if momentum_window else
               f" Nothing is traded between {out['orb_cutoff']} and "
               f"{out['momentum_from']} — that window is the weakest in the sample."))
    if log:
        save_scan("live_orb", out)
        journal_auto_log(f"LIVE_{mode.replace(' ', '_')}",
                         out["actionable"] or out["momentum"], for_date=day)
    return out


# =============================================================================
# v22 (7) MOMENTUM ENGINE — what runs after 12:30 instead of a stale ORB.
# Quality, not quantity: a name only qualifies if it is trending on its own
# intraday chart (above/below VWAP, sitting in the top/bottom of the day range),
# has real relative volume, can physically travel the target, and is not
# fighting the Daily / 1-Hour context.
# =============================================================================
def scan_momentum(capital=None, bundle=None, bars=None, MTF=None, day=None,
                  gen_at=None, top_n=None, watch=None):
    capital = capital or CFG["DEFAULT_CAPITAL"]
    top_n = top_n or CFG["MOMENTUM_TOP_N"]
    b = bundle or load_bundle()
    if not b:
        return []
    gen_at = gen_at or get_ist_now()
    day = day or gen_at.date()
    if bars is None:
        names = watch or liquid_names(b)
        bars = download(names, period="1d", interval="5m", ttl=45)
    MTF = MTF if MTF is not None else mtf_context(b, list(bars.keys()))
    rows = []
    for t, d in (bars or {}).items():
        try:
            g = d.copy()
            g.index = pd.DatetimeIndex(g.index)
            if g.index.tz is not None:
                g.index = g.index.tz_convert(IST)
            g = g[g.index.date == day]
            g = g[(g.index.time >= MKT_OPEN) & (g.index.time <= MKT_CLOSE)]
            if len(g) < 6:
                continue
            base = b["snap"].get(t, {}) or {}
            ok, _why = feasible(base, "L")
            if not ok:
                continue
            op = float(g.Open.iloc[0]); last = float(g.Close.iloc[-1])
            hi = float(g.High.max()); lo = float(g.Low.min())
            rng = hi - lo
            pos = ((last - lo) / rng) if rng > 0 else 0.5
            vw = float(vwap(g).iloc[-1]) if rng > 0 else last
            move = (last / op - 1) * 100
            e9 = float(ema(g.Close, 9).iloc[-1])
            e21 = float(ema(g.Close, 21).iloc[-1]) if len(g) >= 21 else e9
            mrec = MTF.get(t) or {}
            for side in ("BUY", "SELL"):
                up = side == "BUY"
                if up and not (last > vw and e9 > e21 and pos >= CFG["MOMENTUM_MIN_POS"]
                               and move >= CFG["MOMENTUM_MIN_MOVE"]):
                    continue
                if (not up) and not (last < vw and e9 < e21
                                     and pos <= (1 - CFG["MOMENTUM_MIN_POS"])
                                     and move <= -CFG["MOMENTUM_MIN_MOVE"]):
                    continue
                m_ok, m_why = mtf_ok(mrec, side)
                if not m_ok:
                    continue
                # stop: the tighter of the configured cap and the distance to VWAP
                vw_dist = abs(last - vw) / last * 100
                sl_pct = float(np.clip(max(vw_dist, CFG["SL_FLOOR_PCT"]),
                                       CFG["SL_FLOOR_PCT"], CFG["MOMENTUM_SL_PCT"]))
                tk = build_ticket(t, side, last, sl_pct, capital,
                                  target_pct=CFG["MOMENTUM_TARGET_PCT"],
                                  partial_pct=CFG["MOMENTUM_TARGET_PCT"] / 2,
                                  generated_at=gen_at, setup="MOMENTUM (post-12:30)",
                                  tf="5-min trend")
                score = (abs(move) * 0.45 + (pos if up else 1 - pos) * 100 * 0.02
                         + min((base.get("rvol") or 0), 3) * 0.8
                         + abs(mrec.get("mtf_score") or 0) * 0.5)
                rows.append(dict(
                    ticker=t, sym=t.replace(".NS", ""), sector=sector_of(t),
                    side=side, setup="MOMENTUM", entry=nz(last), last=nz(last),
                    vwap=nz(vw), day_pos=nz(pos * 100, 0), moved=nz(move),
                    move_from_open=nz(move), atr_pct=nz(base.get("atr_pct")),
                    adr20=nz(base.get("adr20")), rvol=nz(base.get("rvol")),
                    sl_pct=nz(sl_pct), rr=tk["rr"], score=nz(score),
                    mtf=mrec.get("mtf"), mtf_score=mrec.get("mtf_score"),
                    signal_time=fmt_clock(gen_at), generated_at=fmt_ist(gen_at),
                    orb_tf="momentum", prob_pct=np.nan, ticket=tk,
                    gate_pass=True, gate_note="momentum window",
                    low_conf=(side == "SELL"), drivers=[]))
        except Exception:
            continue
    if not rows:
        return []
    rows.sort(key=lambda r: (r["score"] or 0), reverse=True)
    return rows[:int(top_n)]


# =============================================================================
# HISTORICAL REPLAY (question 9)
# Runs Pre-Market / Live ORB / EOD exactly as they would have run on a past
# date. 5-minute bars are only available for ~60 calendar days from the data
# provider, so the ORB replay window is bounded by that -- it is stated on the
# tab rather than silently truncated.
# =============================================================================
@st.cache_data(ttl=1800, show_spinner=False)
def _replay_bars(day_str, tickers, want_minutes=5):
    """Intraday bars for ONE past session.

    v22 (9) — deeper history. The provider keeps 5-minute bars for only ~60
    calendar days but 60-minute bars for ~2 years, so:
      * a session inside the 5-minute window is replayed on 5-minute bars and
        supports 5 / 15-minute opening ranges;
      * an older session falls back to 60-minute bars, which still supports the
        1-hour and 2-hour opening ranges.
    Returns (bars_dict, interval_minutes, source_note) so the caller knows which
    opening ranges are honest for that date.
    """
    day = pd.Timestamp(day_str).date()
    age = (get_ist_now().date() - day).days
    fine = (age <= CFG["REPLAY_5M_DAYS"] - 2) and int(want_minutes) < 60
    if fine:
        interval, period, bar_min = "5m", f"{CFG['REPLAY_5M_DAYS']}d", 5
        note = "5-minute bars"
    else:
        interval, period, bar_min = "60m", f"{CFG['REPLAY_60M_DAYS']}d", 60
        note = ("60-minute bars (5-minute history only reaches back "
                f"{CFG['REPLAY_5M_DAYS']} days)")
    raw = download(list(tickers), period=period, interval=interval, ttl=1800)
    out = {}
    for t, d in (raw or {}).items():
        try:
            g = d.copy()
            g.index = pd.DatetimeIndex(g.index)
            if g.index.tz is not None:
                g.index = g.index.tz_convert(IST)
            g = g[g.index.date == day]
            if len(g) >= (5 if bar_min == 5 else 2):
                out[t] = g
        except Exception:
            continue
    return out, bar_min, note


def replay_session(day, capital=None, bundle=None, top_n=None, orb_minutes=None,
                   profile=None):
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
    om = int(orb_minutes or CFG["ORB_MINUTES"])
    bars, bar_min, bar_note = _replay_bars(str(day), names, want_minutes=om)
    if om < bar_min:
        om = bar_min          # cannot build a 5-min range out of 60-min bars
    res = dict(day=str(day), n_names=len(names), n_bars=len(bars),
               orb_minutes=om, bar_min=bar_min, bar_note=bar_note,
               profile=profile, history_sessions=b.get("n_sessions"))
    if not bars:
        res["orb_error"] = ("Intraday bars are not available for this date. "
                            "5-minute history reaches ~"
                            f"{CFG['REPLAY_5M_DAYS']} days and 60-minute history "
                            f"~{CFG['REPLAY_60M_DAYS']} days.")
    else:
        res["orb"] = scan_live_orb(capital=capital, bundle=bb, orb_minutes=om,
                                   top_n=top_n, bars=bars, as_of=day, log=False,
                                   profile=profile)
        # what actually happened, so the replay is a scorecard and not a guess
        for r in res["orb"].get("actionable", []):
            g = bars.get(r["ticker"])
            if g is None:
                continue
            r.update(_replay_outcome(g, r))
        for r in res["orb"].get("all_ranked", []) or []:
            g = bars.get(r.get("ticker"))
            if g is not None and r.get("gate_pass"):
                r.update(_replay_outcome(g, r))
    res["eod"] = _replay_eod(bb, day, capital, orb_minutes=om, profile=profile)
    res["premarket"] = _replay_premarket(bb, bars, day, capital)
    return res


def replay_range(days, capital=None, bundle=None, top_n=None, orb_minutes=None,
                 profile=None, progress=None):
    """v22 (9) — walk a LIST of past sessions and aggregate the ORB scorecard.
    This is the accuracy number the footer quotes, recomputed on demand over as
    many sessions as the data allows instead of a single day.
    """
    b = bundle or load_bundle()
    out, trades = [], []
    for i, d in enumerate(days):
        if progress:
            try:
                progress((i + 1) / max(len(days), 1), f"replaying {d}")
            except Exception:
                pass
        r = replay_session(d, capital=capital, bundle=b, top_n=top_n,
                           orb_minutes=orb_minutes, profile=profile)
        if r.get("error"):
            continue
        acts = ((r.get("orb") or {}).get("actionable") or [])
        for a in acts:
            trades.append(dict(day=str(d), sym=a.get("sym"), side=a.get("side"),
                               signal_time=a.get("trig_clock") or a.get("signal_time"),
                               entry=nz(a.get("entry")), sl_pct=nz(a.get("sl_pct")),
                               rr=(a.get("ticket") or {}).get("rr"),
                               mtf=a.get("mtf"), prob_pct=nz(a.get("prob_pct"), 1),
                               outcome=a.get("rp_result"),
                               hit_time=a.get("rp_hit_time"),
                               exit_time=a.get("rp_exit_time"),
                               pnl_pct=nz(a.get("rp_pnl_pct"), 3),
                               mfe_pct=nz(a.get("rp_mfe_pct"))))
        out.append(dict(day=str(d), n_trades=len(acts),
                        pnl=sum([(x.get("rp_pnl_pct") or 0) for x in acts])))
    T = pd.DataFrame(trades)
    summary = dict(sessions=len(out), trades=len(T))
    if len(T):
        wins = T[T.pnl_pct > 0]
        summary.update(
            hit_target=int((T.outcome == "TARGET HIT").sum()),
            stopped=int((T.outcome == "STOPPED").sum()),
            be_partial=int((T.outcome == "BE/partial").sum()),
            squared=int(T.outcome.astype(str).str.contains("squared").sum()),
            win_rate=round(len(wins) / len(T) * 100, 1),
            target_rate=round(float((T.outcome == "TARGET HIT").mean() * 100), 1),
            avg_pnl=round(float(T.pnl_pct.mean()), 3),
            median_pnl=round(float(T.pnl_pct.median()), 3),
            total_pnl=round(float(T.pnl_pct.sum()), 2),
            avg_win=round(float(wins.pnl_pct.mean()), 3) if len(wins) else 0.0,
            avg_loss=round(float(T[T.pnl_pct <= 0].pnl_pct.mean()), 3)
                     if len(T) > len(wins) else 0.0,
            best=round(float(T.pnl_pct.max()), 2), worst=round(float(T.pnl_pct.min()), 2))
        summary["expectancy"] = summary["avg_pnl"]
    return dict(summary=summary, trades=T, by_day=pd.DataFrame(out))


def _replay_outcome(g, r):
    """Walk the real bars after the confirming candle: half off at +1%, stop to
    breakeven, rest at target or the 15:10 close.

    v22 (1): also returns the CLOCK TIME of every event — when the partial was
    booked, when the stop or target was hit, and when the position was closed.
    """
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
        t_partial = t_sl = t_tp = None
        for ts, row in after.iterrows():
            hi, lo = float(row.High), float(row.Low)
            mfe = max(mfe, ((hi - e) / e * 100) if sgn > 0 else ((e - lo) / e * 100))
            if (lo <= sl) if sgn > 0 else (hi >= sl):
                pnl += (0.5 if half else 1.0) * sgn * (sl / e - 1) * 100
                t_sl = fmt_clock(ts)
                return dict(rp_result="STOPPED" if not half else "BE/partial",
                            rp_label=("🛑 STOPPED" if not half else "⚖ BE / partial"),
                            rp_pnl_pct=round(pnl - CFG["COST_PCT"], 3),
                            rp_mfe_pct=round(mfe, 2),
                            rp_entry_time=fmt_clock(r.get("trig_time")),
                            rp_partial_time=t_partial, rp_sl_time=t_sl,
                            rp_hit_time=t_sl, rp_exit_time=t_sl,
                            rp_exit_px=nz(sl))
            if not half and ((hi >= p1) if sgn > 0 else (lo <= p1)):
                pnl += 0.5 * CFG["PARTIAL_PCT"]; half = True; sl = e
                t_partial = fmt_clock(ts)
            if (hi >= tp) if sgn > 0 else (lo <= tp):
                pnl += (0.5 if half else 1.0) * CFG["TARGET_PCT"]
                t_tp = fmt_clock(ts)
                return dict(rp_result="TARGET HIT", rp_label="🎯 TARGET HIT",
                            rp_pnl_pct=round(pnl - CFG["COST_PCT"], 3),
                            rp_mfe_pct=round(mfe, 2),
                            rp_entry_time=fmt_clock(r.get("trig_time")),
                            rp_partial_time=t_partial, rp_t1_time=t_partial,
                            rp_target_time=t_tp, rp_hit_time=t_tp,
                            rp_exit_time=t_tp, rp_exit_px=nz(tp))
        last = float(after.Close.iloc[-1])
        pnl += (0.5 if half else 1.0) * sgn * (last / e - 1) * 100
        return dict(rp_result="squared off at close", rp_label="⏹ squared off",
                    rp_pnl_pct=round(pnl - CFG["COST_PCT"], 3), rp_mfe_pct=round(mfe, 2),
                    rp_entry_time=fmt_clock(r.get("trig_time")),
                    rp_partial_time=t_partial, rp_t1_time=t_partial,
                    rp_hit_time=t_partial,
                    rp_exit_time=fmt_clock(after.index[-1]), rp_exit_px=nz(last))
    except Exception as ex:
        return dict(rp_result=f"outcome unavailable ({ex})", rp_label="— n/a")


def _replay_eod(bb, day, capital, orb_minutes=None, profile=None):
    """The EOD scan as it would have printed after that session's close, plus
    what the named stocks did the NEXT session."""
    try:
        res = scan_eod(capital=capital, bundle=bb, top_n=5,
                       orb_minutes=orb_minutes, profile=profile)
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
    dot = "🟢" if side == "BUY" else "🔴"        # v22 (4)
    # v22 (1)(6): the ticket states WHEN it was generated and what it risks.
    stamp = tk.get("generated_at") or tk.get("signal_time") or ""
    meta = " · ".join([x for x in [tk.get("setup"), tk.get("tf")] if x])
    head = (f"<div style='font-size:11px;color:#8f96b3;margin-top:2px'>"
            f"🕒 signal generated <b style='color:#e2e4ef'>{stamp}</b>"
            + (f" · {meta}" if meta else "") + "</div>") if stamp or meta else ""
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
      <span style="font-size:20px;font-weight:800;color:#fff">{dot} {tk['symbol']}</span>
      <span style="background:{accent};color:#06070d;padding:2px 10px;border-radius:6px;
            font-size:12px;font-weight:800;margin-left:8px">{side}</span>
    </div>
    <div style="text-align:right">
      <div style="font-size:11px;color:#8f96b3">QTY</div>
      <div style="font-size:20px;font-weight:800;color:#fff;font-family:JetBrains Mono,monospace">{tk['qty']}</div>
    </div>
  </div>
  {head}
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
  <div style="display:flex;gap:18px;margin-top:10px;font-size:11px;color:#8f96b3;flex-wrap:wrap;align-items:center">
    <span>Risk <b style='color:#f6465d'>{money(tk['risk_rs'])}</b></span>
    <span>Reward <b style='color:#0ecb81'>{money(tk['reward_rs'])}</b></span>
    <span>risk/share {money(tk.get('risk_per_share'))}</span>
  </div>
  <div title="Reward divided by risk. 2 means you are seeking 2 rupees for every 1 risked."
       style="display:inline-block;margin-top:10px;padding:4px 14px;border-radius:20px;
       cursor:help;font-weight:800;font-size:13px;
       background:{'rgba(14,203,129,0.18)' if tk['rr']>=2 else 'rgba(240,185,11,0.18)' if tk['rr']>=1.5 else 'rgba(246,70,93,0.18)'};
       color:{'#0ecb81' if tk['rr']>=2 else '#f0b90b' if tk['rr']>=1.5 else '#f6465d'};
       border:1px solid {'rgba(14,203,129,0.4)' if tk['rr']>=2 else 'rgba(240,185,11,0.4)' if tk['rr']>=1.5 else 'rgba(246,70,93,0.4)'}">
    ⚖ R:R 1:{tk['rr']} ⓘ
  </div>
  {rows}{lad}{warn}
</div>""", unsafe_allow_html=True)

def render_aggressive_tab(capital):
    """🔥 Aggressive sub-tab: widened-volatility scanner aimed at the 4-5%
    F&O gainers/losers, fixed 1% risk : 2% reward. See scan_aggressive_movers
    docstring — this is unvalidated at the 4-5% threshold, paper-trade only."""
    tab_header("🔥 Aggressive — hunting the 4–5% F&O movers", "agg",
              "Same capability features as EOD, top decile only · fixed 1% risk : 2% reward")
    st.warning("⚠ Experimental. Every other tab in this app is walk-forward tested on a "
              "~2% target (see the calibration notes at the top of the file). This tab "
              "reuses the same proven features (ATR%, ADR20, volume expansion, RVOL) but "
              "pushes the threshold further to chase bigger moves — that wider threshold "
              "itself has NOT been backtested yet. Paper-trade it and track results in the "
              "Journal tab before risking real money on it.")
    st.caption("Direction still needs the opening-range confirmation on the Live screener "
              "tab — this is the SHORTLIST to watch pre-market and at EOD, not the order.")
    out = scan_button("🔥 Run aggressive scan", "agg", scan_aggressive_movers,
                      help_running="ranking the F&O list on widened volatility filters…",
                      capital=capital)
    if out is not None:
        st.session_state.res_agg = out
    res = st.session_state.get("res_agg") or load_scan("aggressive")
    if res and not res.get("error"):
        st.caption(f"generated {res.get('generated_at')} · "
                  f"{res.get('n_candidates')} names screened · "
                  f"top {len(res.get('top') or [])} shown (top decile by expansion score)")
        for p in res.get("top") or []:
            r = p["row"]
            st.markdown(f"##### {r['sym']} · {r.get('sector')}")
            st.caption(f"ATR {num(r.get('atr_pct'))}% · ADR {num(r.get('adr20'))}% · "
                      f"Vol20 {num(r.get('vol20'))}% · RVOL {num(r.get('rvol'))} · "
                      f"big moves (60d) {r.get('big20')} · score {r.get('score')}")
            cA, cB = st.columns(2)
            with cA:
                st.markdown("**If it breaks up**")
                ticket_card(p["ticket"])
            with cB:
                st.markdown("**If it breaks down**")
                ticket_card(p["ticket_short"], low_conf=True)
        dl(_clean(res.get("table")), "aggressive_full.csv", "agg_full")
    else:
        st.info("No aggressive scan yet.")

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


# =============================================================================
# v22 (2)(3)(4)(5) PRESENTATION LAYER
#   (2) numbers print as 12123 and 19.27 — never 12123.000000
#   (3) every column and tab carries an ⓘ tooltip
#   (4) side / regime / gate are colour-coded with a dot
#   (5) one table per view, the same column names everywhere
# =============================================================================

# ---- (3) the tooltip dictionary. Keyed by the DISPLAY name of the column, so
# the same help text follows a column into every tab it appears in.
COL_HELP = {
    "Symbol": "NSE symbol. F&O names only, so intraday shorting is allowed.",
    "Sector": "Sector bucket used for the sector-strength and 2-per-sector cap.",
    "Side": "🟢 BUY = long above the opening-range high. 🔴 SELL = short below the low.",
    "Setup": "Which engine produced the signal: opening-range break, momentum, swing.",
    "ORB TF": "Length of the opening range used for entry (5 / 15 / 60 / 120 minutes).",
    "MTF": "Daily + 1-hour agreement. ALIGNED = both point the same way as the trade.",
    "MTF score": "−2 to +2. Positive = higher timeframes lean up, negative = down.",
    "Signal time": "Clock time (IST) the break was confirmed / the signal was generated.",
    "Generated at": "Full timestamp the ticket was produced.",
    "Break time": "Clock time the price closed beyond the opening range.",
    "Hit time": "Clock time the target or the stop was reached.",
    "Exit time": "Clock time the position was fully closed (target, stop, or 15:10).",
    "Entry": "Trigger price. Use a stop-limit order, do not chase.",
    "Stop": "Hard stop-loss price. Non-negotiable.",
    "Target": "Final target price (target 2). Half is booked at target 1.",
    "SL %": "Stop distance as a percent of entry.",
    "Target %": "Target distance as a percent of entry.",
    "R:R": "Risk-reward ratio, reward ÷ risk in rupees. 2 means ₹2 sought per ₹1 risked.",
    "Risk ₹": "Rupees lost if the stop is hit, at the shown quantity.",
    "Reward ₹": "Rupees made if the final target is hit, at the shown quantity.",
    "Qty": "Position size from your capital and risk-per-trade setting.",
    "P(+2%) %": "Walk-forward model probability of touching +2% before the stop.",
    "P(+3%) %": "Same model, +3% target. Lower by definition.",
    "Break @min": "Minutes after the opening range closed that the break confirmed. "
                  "Earlier is materially better.",
    "ORB range %": "Height of the opening range as a percent of price. Very wide ranges "
                   "leave no room to the target.",
    "ATR %": "14-day average true range as a percent of price — can the stock travel the target.",
    "ADR %": "20-day average daily range percent. Same question, simpler measure.",
    "RVOL": "Today's volume vs its 20-day average. Above 1.5 means real participation.",
    "Gap %": "Open vs previous close.",
    "Sector vs mkt %": "Sector's early move minus Nifty's early move.",
    "Sector breadth %": "Percent of names in the sector that are up in the first candles.",
    "Moved since %": "How far price has already run past the trigger. Large = you are late.",
    "Last": "Most recent traded price in the data pull.",
    "VWAP": "Volume-weighted average price for today. Momentum longs must hold above it.",
    "Day pos %": "Where the last price sits inside today's range. 100 = at the high.",
    "Move from open %": "Percent change from today's opening print.",
    "Gate": "✅ PASS = cleared the confluence + time + timeframe filters. ❌ FAIL = rejected.",
    "Why": "Plain-language reason the name passed or was rejected.",
    "Outcome": "What actually happened in the replayed session.",
    "P&L %": "Net percent result after costs, on the half-out-then-trail plan.",
    "MFE %": "Maximum favourable excursion — the best unrealised gain reached.",
    "Close": "Daily closing price.",
    "Turnover ₹cr": "20-day average traded value in crores. Liquidity filter.",
    "Move %": "Percent return over the selected period.",
    "Expansion score": "Composite of range, volume and big-move frequency — how likely "
                       "the stock expands tomorrow.",
    "P(move) %": "Base-rate probability the stock travels 2% intraday tomorrow.",
    "#": "Rank in this list.",
    "Type": "GAINER or LOSER.",
    "Score": "Internal ranking score. Higher = ranked earlier. Not a probability.",
    "Score L": "Long-side ranking score.",
    "Score S": "Short-side ranking score.",
    "P(up) %": "Historical odds this gap resolves upward.",
    "P(dn) %": "Historical odds this gap resolves downward.",
    "Exp. open": "Expected opening price (pre-open book, or the real open in replay).",
    "1H RSI": "RSI on the 1-hour chart. Above 60 = 1-hour trend is strong.",
    "1H vs EMA20 %": "Distance of price from the 1-hour 20-EMA.",
    "Pivot": "Central pivot for the session.",
    "CPR width %": "Narrow central pivot range = coiled, wide = already trending.",
}


def _num_fmt(series):
    """(2) '%.10g' prints 12123 as 12123 and 19.27 as 19.27, with no padding
    zeros, while the column stays numeric so sorting still works."""
    return "%.10g"


def show_df(D, key=None, help_map=None, shade=None, download=True, height=None):
    """The single table renderer used by every tab.

    * numbers are cleaned so nothing prints as 12123.000000
    * every column gets its ⓘ tooltip from COL_HELP
    * falls back to a plain dataframe on older Streamlit builds
    """
    if D is None or (hasattr(D, "empty") and D.empty):
        st.caption("nothing to show")
        return
    D = pd.DataFrame(D).copy()
    H = dict(COL_HELP)
    H.update(help_map or {})
    # Round but keep the column NUMERIC, then let the '%.10g' column format strip
    # the trailing zeros. Sorting a numeric column still works; sorting a column
    # of pre-formatted strings would sort 100 before 9.
    for c in D.columns:
        if pd.api.types.is_bool_dtype(D[c]):
            continue
        if pd.api.types.is_numeric_dtype(D[c]):
            D[c] = pd.to_numeric(D[c], errors="coerce").round(2)
    cfg = {}
    try:
        for c in D.columns:
            hp = H.get(str(c))
            if pd.api.types.is_numeric_dtype(D[c]):
                cfg[c] = st.column_config.NumberColumn(str(c), help=hp, format="%.10g")
            else:
                cfg[c] = st.column_config.TextColumn(str(c), help=hp)
    except Exception:
        cfg = None
    try:
        st.dataframe(D, width="stretch", hide_index=True,
                     column_config=cfg, height=height)
    except Exception:
        try:
            st.dataframe(_clean(D), width="stretch", hide_index=True)
        except Exception:
            st.dataframe(D, width="stretch", hide_index=True)
    if download and key:
        dl(D, f"{key}.csv", key)


# ---- (4) colour coding -------------------------------------------------------
def _blank(v):
    """True for None, NaN and empty strings — keeps the dot helpers NaN-safe."""
    if v is None:
        return True
    try:
        if isinstance(v, float) and v != v:
            return True
    except Exception:
        pass
    return str(v).strip() in ("", "nan", "None", "NaN", "<NA>", "NaT")


def side_dot(v):
    if _blank(v):
        return "—"
    s = str(v).upper()
    if s.startswith("B") or s == "L":
        return "🟢 BUY"
    if s.startswith("S"):
        return "🔴 SELL"
    return s or "—"


def regime_dot(v):
    if _blank(v):
        return "—"
    s = str(v).upper()
    return {"BULL": "🟢 BULL", "BEAR": "🔴 BEAR", "NEUTRAL": "⚪ NEUTRAL",
            "CHOPPY": "🟡 CHOPPY"}.get(s, f"⚪ {s or '—'}")


def gate_dot(v):
    if _blank(v):
        return "—"
    if isinstance(v, str):
        return "✅ PASS" if v.strip().upper() in ("PASS", "TRUE", "1", "YES") else "❌ FAIL"
    return "✅ PASS" if bool(v) else "❌ FAIL"


def mtf_dot(v):
    if _blank(v):
        return "—"
    s = str(v).upper()
    if "ALIGNED UP" in s or s == "DAILY UP":
        return f"🟢 {s}"
    if "ALIGNED DOWN" in s or s == "DAILY DOWN":
        return f"🔴 {s}"
    if "CONFLICT" in s:
        return f"🔴 {s}"
    if "PARTIAL" in s:
        return f"🟡 {s}"
    return f"⚪ {s or '—'}" if s else "—"


def outcome_dot(v):
    if _blank(v):
        return "—"
    s = str(v)
    if "TARGET" in s.upper():
        return "🎯 TARGET HIT"
    if "STOPPED" in s.upper():
        return "🛑 STOPPED"
    if "BE" in s.upper() or "partial" in s:
        return "⚖ BE / partial"
    if "squared" in s:
        return "⏹ squared off"
    return s or "—"


# ---- (3) tab-level tooltips. st.tabs() has no help= parameter, so the ⓘ is
# rendered as an HTML span with a native browser title attribute.
def info_badge(text, label=""):
    return (f"<span title=\"{str(text).replace(chr(34), chr(39))}\" "
            f"style='cursor:help;color:#4f8cff;border:1px solid #4f8cff66;"
            f"background:#4f8cff18;border-radius:50%;padding:0 6px;font-size:12px;"
            f"font-weight:700'>ⓘ</span>" + (f" <span style='color:#8f96b3;"
            f"font-size:12px'>{label}</span>" if label else ""))


TAB_HELP = {
    "pm": ("PRE-MARKET (run 09:08–09:14). Ranks the F&O universe on the pre-open "
           "auction book plus last night's daily structure, and tells you which "
           "1–2 names to watch for an opening-range break. Nothing is traded here "
           "— it only builds the watchlist."),
    "orb": ("LIVE SCREENER. Before 10:00 it hunts opening-range breaks. After 10:00 "
            "the opening range is stale so no ORB ticket is issued, and after 12:30 "
            "it switches to ranking genuine momentum instead. Quality over quantity."),
    "eod": ("EOD → TOMORROW (run after 15:40). Picks the names most likely to expand "
            "tomorrow and prints the plan with the opening range you selected, so "
            "you arrive at 09:15 already knowing the levels."),
    "swing": ("SWING (run Friday after close). The SWING profile: the Daily chart gives "
              "the full-day structure and the entry is Monday's first 1-hour or 2-hour "
              "opening range, your choice in the sidebar. Positional longs held for days "
              "to weeks. Separate risk model: −4% stop, +10% target, laddered exits."),
    "replay": ("REPLAY / BACKTEST. Re-runs every engine on a past session with no "
               "look-ahead — the snapshot is rebuilt as it stood at the previous close "
               "— then scores what actually happened, including the time the stop or "
               "target was hit."),
    "movers": ("MOVERS. Biggest gainers and losers over 1 day to 1 year, with the "
               "liquidity and volatility context that explains the move."),
    "learn": ("LEARNING. What the data says about which conditions preceded real moves, "
              "plus the honest walk-forward edge of each engine. Read this before "
              "trusting any signal."),
    "journal": ("JOURNAL. Every signal the app produced is auto-logged with its time, "
                "R:R and outcome. Add your real fills in the manual tab to compare "
                "plan against execution."),
}


def tab_header(title, key, extra=None):
    """One consistent heading per tab: title, ⓘ tooltip, one line of context."""
    st.markdown(f"### {title} {info_badge(TAB_HELP.get(key, ''))}",
                unsafe_allow_html=True)
    if extra:
        st.caption(extra)


def _clean(df, cols=None, rename=None, r=2):
    """v22 (2): rounds AND drops trailing zeros, so 12123.000000 prints as 12123."""
    if df is None or (hasattr(df, "empty") and df.empty):
        return pd.DataFrame()
    d = pd.DataFrame(df)
    if cols:
        d = d[[c for c in cols if c in d.columns]]
    for c in d.columns:
        if pd.api.types.is_bool_dtype(d[c]):
            continue
        if pd.api.types.is_numeric_dtype(d[c]):
            d[c] = d[c].map(lambda v: nz(v, r))
    if rename:
        d = d.rename(columns=rename)
    return d


# (5) one column order, used by every signal table in the app.
SIG_COLS = ["sym", "sector", "side", "signal_time", "entry", "sl_pct", "rr",
            "prob_pct", "p3_pct", "mtf", "orb_tf", "trig_min", "orb_range_pct",
            "atr_pct", "adr20", "rvol", "gap", "sec_rel", "sec_breadth",
            "moved", "last"]
SIG_NAMES = {"sym": "Symbol", "sector": "Sector", "side": "Side", "entry": "Entry",
             "signal_time": "Signal time", "rr": "R:R", "mtf": "MTF",
             "orb_tf": "ORB TF", "rvol": "RVOL",
             "sl_pct": "SL %", "prob_pct": "P(+2%) %", "p3_pct": "P(+3%) %",
             "trig_min": "Break @min", "orb_range_pct": "ORB range %",
             "atr_pct": "ATR %", "adr20": "ADR %", "gap": "Gap %",
             "sec_rel": "Sector vs mkt %", "sec_breadth": "Sector breadth %",
             "moved": "Moved since %", "last": "Last"}


def signal_list(rows, key, qty=True):
    """Sleek single-glance table. Highlights the row you are meant to trade."""
    if not rows:
        st.caption("nothing to show")
        return
    recs = []
    for r in rows:
        tk = r.get("ticket") or {}
        d = {}
        for c in SIG_COLS:
            if c not in r:
                continue
            name = SIG_NAMES.get(c, c)
            v = r.get(c)
            if c == "side":
                v = side_dot(v)
            elif c == "mtf":
                v = mtf_dot(v)
            elif c == "rr":
                v = f"1:{nz(v)}" if v else "—"
            elif c == "orb_tf":
                v = f"{v}-min" if isinstance(v, (int, float)) else (v or "—")
            d[name] = v
        if qty:
            d["Qty"] = tk.get("qty")
            d["Stop"] = tk.get("sl")
            d["Target"] = tk.get("target")
            d["Risk ₹"] = tk.get("risk_rs")
            d["Reward ₹"] = tk.get("reward_rs")
            if not d.get("R:R") and tk.get("rr"):
                d["R:R"] = f"1:{tk['rr']}"
        if r.get("rp_result"):
            d["Outcome"] = outcome_dot(r.get("rp_result"))
            d["Hit time"] = r.get("rp_hit_time") or "—"
            d["Exit time"] = r.get("rp_exit_time") or "—"
            d["P&L %"] = r.get("rp_pnl_pct")
        recs.append(d)
    show_df(pd.DataFrame(recs), key=key, shade="P(+2%) %")


def signal_cards(rows, capital_note=True):
    if not rows:
        st.caption("nothing to show")
        return
    for r in rows:
        tf_txt = r.get("orb_tf_label") or (f"{r.get('orb_tf')}-min" if r.get("orb_tf") else "")
        extra = []
        if r.get("signal_time") or r.get("trig_clock"):
            extra.append(("🕒 Signal time", r.get("trig_clock") or r.get("signal_time")))
        if r.get("mtf"):
            extra.append(("Higher timeframes", mtf_dot(r.get("mtf"))))
        extra += [("Model P(+2%)", f"{num(r.get('prob_pct'), 1)}%"),
                  ("Model P(+3%)", f"{num(r.get('p3_pct'), 1)}%"),
                  ("Break confirmed at", f"+{num(r.get('trig_min'), 0)} min"),
                  (f"Opening range ({tf_txt or 'ORB'})", f"{num(r.get('orb_range_pct'))}%"),
                  ("ATR% / ADR%", f"{num(r.get('atr_pct'))} / {num(r.get('adr20'))}"),
                  ("Gap", pct(r.get("gap"))),
                  ("Sector vs market", pct(r.get("sec_rel"))),
                  ("Sector breadth (up names)", f"{num(r.get('sec_breadth'), 0)}%"),
                  ("Moved since entry", pct(r.get("moved")))]
        if r.get("vwap"):
            extra.append(("VWAP / day position",
                          f"{num(r.get('vwap'))} · {num(r.get('day_pos'), 0)}% of range"))
        if r.get("rp_result"):
            extra.append(("REPLAY RESULT", f"{outcome_dot(r['rp_result'])} · "
                                           f"{pct(r.get('rp_pnl_pct'))} · MFE {num(r.get('rp_mfe_pct'))}%"))
            if r.get("rp_hit_time"):
                extra.append(("🕒 Target / stop hit at", r.get("rp_hit_time")))
            if r.get("rp_exit_time"):
                extra.append(("🕒 Position closed at", r.get("rp_exit_time")))
        ticket_card(r["ticket"], prob=r.get("p"), extra_rows=extra,
                    low_conf=bool(r.get("low_conf")))
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
        show_df(D, key=f"{key}_tbl", download=False)
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
st.markdown("<div class='sub-caption'>Point-in-time NSE F&amp;O universe (295 names, 56 "
            "monthly snapshots) · 269,147 close-confirmed 5-min ORB events over 1,147 "
            "sessions · corrected sequential win/stop labels · real 5-min volume "
            "(99.96% coverage) · walk-forward expectancy shown below, unretouched</div>",
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
    CFG["INTRADAY_TOP_N"] = st.selectbox(
        "Intraday signals per day", [1, 2, 3], index=0,
        help="Walk-forward expectancy on 1,026 sessions: top-1 -0.137%/trade, top-2 -0.155%, top-3 -0.153%, top-5 -0.131%. All negative; k=1 is simply the least bad. The earlier +0.32% figure came from 33 sessions and has been retracted. "
             "1 is the default because the edge is concentrated in the single best name.")
    CFG["SHORT_TOP_N"] = 1 if st.checkbox("Show the short side too", value=True,
                                          help="Short ORB expectancy was -0.03%/trade "
                                               "in walk-forward. Shown, never recommended.") else 0
    st.divider()

    # ---------------- v22 (8) timeframe engine, one control for the whole app ---
    st.markdown("#### 🕒 Timeframes")
    PROF_KEYS = list(MTF_PROFILES.keys())
    MTF_PROFILE = st.selectbox(
        "Analysis profile", PROF_KEYS, index=0,
        help="A · INTRADAY: Daily chart for the macro levels + 1-Hour chart for the "
             "immediate context, execution and entry on a 5-minute or 15-minute "
             "opening range.\n\n"
             "B · SWING: Daily chart for the full-day structures, execution and entry "
             "on a 1-hour or 2-hour opening range — fewer, larger, slower trades.\n\n"
             "The profile applies to Pre-Market, Live, EOD and Replay together. The "
             "Swing tab always runs the swing profile and has its own entry range "
             "below.")
    _P = MTF_PROFILES[MTF_PROFILE]
    ORB_TF = st.selectbox(
        "Screener entry range — Pre-Market / Live / EOD / Replay", _P["exec_tfs"],
        index=_P["exec_tfs"].index(_P["default_exec"]),
        format_func=lambda m: ORB_TF_LABEL.get(m, f"{m} min"),
        help="Length of the opening range used for the actual entry trigger. "
             "Profile A offers 5-minute and 15-minute; profile B offers 1-hour and "
             "2-hour. A longer range means fewer, better-confirmed breaks and a "
             "wider stop.")
    CFG["ORB_MINUTES"] = int(ORB_TF)
    # 8b: the Swing tab is always the swing profile, with its own 1h / 2h choice
    _SW = MTF_PROFILES[PROFILE_SWING]
    SWING_ORB = st.selectbox(
        "Monday entry range — Swing tab only", _SW["exec_tfs"],
        index=_SW["exec_tfs"].index(int(CFG["SWING_ORB_MINUTES"]))
        if int(CFG["SWING_ORB_MINUTES"]) in _SW["exec_tfs"] else 0,
        format_func=lambda m: ORB_TF_LABEL.get(m, f"{m} min"),
        help="Swing trading reads the Daily chart for the full-day structures and "
             "enters on the Monday 1-hour or 2-hour opening range. This control only "
             "affects the Swing tab.")
    CFG["SWING_ORB_MINUTES"] = int(SWING_ORB)
    CFG["MTF_REQUIRE_ALIGN"] = st.checkbox(
        "Require higher-timeframe agreement", value=CFG["MTF_REQUIRE_ALIGN"],
        help="When on, a long is rejected unless the Daily (and the 1-Hour, in "
             "profile A) also lean up, and a short unless they lean down. Fewer "
             "signals, higher quality.")
    CFG["ENFORCE_TIME_GATES"] = st.checkbox(
        "Enforce session time gates", value=CFG["ENFORCE_TIME_GATES"],
        help=f"On: no opening-range trade is issued after "
             f"{CFG['ORB_CUTOFF_TIME'].strftime('%H:%M')}, nothing at all between then "
             f"and {CFG['MOMENTUM_START_TIME'].strftime('%H:%M')}, and momentum names "
             f"only after that. Off: the old always-on behaviour.")
    st.caption(_P["note"])
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

colI, colS = st.columns(2, gap="large")

with colI:
    st.markdown("## ⚡ INTRADAY")
    _ilabels = [("pm", "Pre-Market"), ("orb", "Live"), ("eod", "EOD"), ("agg", "Aggressive")]
    _ijobs = {k: scan_job_get(k) for k, _ in _ilabels}
    _idots = {"running": "🟡 running", "done": "🟢 ready", "error": "🔴 error", "idle": "⚪ idle"}
    st.caption("  ·  ".join(f"{lbl} {_idots.get(_ijobs[k]['status'], '⚪ idle')}" for k, lbl in _ilabels))
    if any(j['status'] == 'running' for j in _ijobs.values()):
        st.caption("⏳ scanning in the background — switch sub-tabs freely, nothing here is blocked")
    sub = st.tabs(["🌅 Pre-Market", "⚡ Live screener", "🌙 EOD → Tomorrow", "🔥 Aggressive"])
    with sub[0]:
        tab_header("🌅 Pre-market watchlist", "pm",
                   f"Run 09:00–09:08 · profile {MTF_PROFILE} · entry on the "
                   f"{ORB_TF_LABEL.get(CFG['ORB_MINUTES'], CFG['ORB_MINUTES'])}")
        st.caption("This is a WATCHLIST, not the order. The order is the opening-range break "
                   "on the Live screener tab, and only before "
                   f"{CFG['ORB_CUTOFF_TIME'].strftime('%H:%M')}.")
        _pm_out = scan_button("🌅 Run pre-market scan", "pm", scan_premarket,
                              help_running="reading NSE pre-open book + daily / 1-hour context…",
                              capital=capital, orb_minutes=CFG["ORB_MINUTES"], profile=MTF_PROFILE)
        if _pm_out is not None:
            st.session_state.res_pm = _pm_out
        res = st.session_state.get("res_pm") or load_scan("premarket")
        if res and not res.get("error"):
            st.caption(f"generated {res.get('generated_at') or res.get('ts')} · "
                       f"regime {regime_dot(res.get('regime'))} · "
                       f"{res.get('n_candidates')} feasible names · "
                       f"{res.get('orb_minutes', CFG['ORB_MINUTES'])}-min entry range · "
                       f"{res.get('history_sessions') or '—'} sessions of history")
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
                        tk = c.get("ticket") or {}
                        recs.append({"#": i, "Side": side_dot(lbl), "Symbol": r["sym"],
                                     "Sector": r.get("sector"),
                                     "Signal time": r.get("signal_time") or res.get("signal_time"),
                                     "MTF": mtf_dot(r.get("mtf")),
                                     "Exp. open": nz(r.get("exp_open")),
                                     "Gap %": nz(r.get("gap")),
                                     "ATR %": nz(r.get("atr_pct")),
                                     "ADR %": nz(r.get("adr20")),
                                     "P(move) %": nz((r.get("p_up") if lbl == "BUY"
                                                      else r.get("p_dn")), 1),
                                     "Days ≥2.5% / 20": r.get("big20"),
                                     "Entry": nz(tk.get("entry")), "Stop": nz(tk.get("sl")),
                                     "Target": nz(tk.get("target")),
                                     "R:R": f"1:{tk.get('rr')}" if tk.get("rr") else "—",
                                     "Qty": tk.get("qty")})
                show_df(pd.DataFrame(recs), key="pm",
                        help_map={"Days ≥2.5% / 20": "Sessions in the last 20 that moved at "
                                                     "least 2.5% — proof the stock can travel."})
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

    with sub[1]:
        _nowt = get_ist_now().time()
        _win = ("ORB WINDOW OPEN" if _nowt < CFG["ORB_CUTOFF_TIME"] else
                ("MOMENTUM WINDOW" if _nowt >= CFG["MOMENTUM_START_TIME"] else "STAND ASIDE"))
        _winkind = {"ORB WINDOW OPEN": "bull", "MOMENTUM WINDOW": "info",
                    "STAND ASIDE": "warn"}[_win]
        tab_header("⚡ Live screener", "orb")
        st.markdown(
            chip(_win, _winkind) + "  " +
            chip(f"now {_nowt.strftime('%H:%M')} IST", "neutral") + "  " +
            chip(f"ORB cut-off {CFG['ORB_CUTOFF_TIME'].strftime('%H:%M')}", "neutral") + "  " +
            chip(f"momentum from {CFG['MOMENTUM_START_TIME'].strftime('%H:%M')}", "neutral") +
            "  " + info_badge(
                "Before 10:00 — opening-range breaks only, entry on the CLOSE of the first "
                "candle beyond the range. 10:00 to 12:30 — nothing is issued. "
                "After 12:30 — momentum names only: above VWAP, at the top of the day range, "
                "real relative volume, higher timeframes agreeing."),
            unsafe_allow_html=True)
        st.caption(f"Entry range: {ORB_TF_LABEL.get(CFG['ORB_MINUTES'], CFG['ORB_MINUTES'])} · "
                   f"profile {MTF_PROFILE} · stop capped at {CFG['SL_CAP_PCT']}% · "
                   f"half booked at +{CFG['PARTIAL_PCT']}% then stop to breakeven")
        _orb_out = scan_button("⚡ Run live screener", "orb", scan_live_orb,
                               help_running="pulling intraday bars for the whole F&O list…",
                               capital=capital, orb_minutes=CFG["ORB_MINUTES"], profile=MTF_PROFILE,
                               enforce_time_gates=CFG["ENFORCE_TIME_GATES"],
                               require_mtf=CFG["MTF_REQUIRE_ALIGN"])
        if _orb_out is not None:
            st.session_state.res_orb = _orb_out
        res = st.session_state.get("res_orb") or load_scan("live_orb")
        if res and not res.get("error"):
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Confirmed breaks", res.get("n_triggers", 0),
                      help="Names that closed a candle beyond their opening range.")
            m2.metric("Cleared all filters", res.get("n_gate_pass", 0),
                      help="Confluence gate + time gate + higher-timeframe check.")
            m3.metric("Regime", regime_dot(res.get("regime", "—")),
                      help="Nifty trend context. \U0001F7E2 BULL, \U0001F534 BEAR, ⚪ NEUTRAL.")
            m4.metric("Breadth (early)", pct(res.get("mkt_early")),
                      help="Median early move across the liquid F&O list.")
            if res.get("gate_message"):
                st.warning("⏱ " + res["gate_message"])
            if res.get("n_late_break"):
                st.caption(f"🕒 {res['n_late_break']} break(s) were dropped for confirming after "
                           f"{res.get('orb_cutoff', '10:00')}.")
            act = res.get("actionable") or []
            if act:
                st.markdown("##### ✅ Place these")
                render_signals(act, "orb_act")
            elif not res.get("orb_suppressed"):
                st.warning("Nothing passed the gate and the probability floor. That is a valid "
                           "answer — on the walk-forward sample, forcing a trade on the days the "
                           "gate was empty was measured on 33 sessions and did not survive "
                           "re-measurement on 1,026. See V23_VERIFICATION.md.")
            # ---- v22 (7) momentum block, only after 12:30 ------------------------
            mom = res.get("momentum") or []
            if mom:
                st.markdown("##### 🚀 Momentum names (post-12:30)" + " " + info_badge(
                    "Not an opening-range trade. These are names still trending late in the "
                    "session: holding above (or below) VWAP, sitting in the top (or bottom) "
                    f"{int((1 - CFG['MOMENTUM_MIN_POS']) * 100)}% of the day's range, at least "
                    f"{CFG['MOMENTUM_MIN_MOVE']}% from the open, with the daily and 1-hour "
                    f"charts agreeing. Target {CFG['MOMENTUM_TARGET_PCT']}%, stop at VWAP "
                    f"capped at {CFG['MOMENTUM_SL_PCT']}%."), unsafe_allow_html=True)
                render_signals(mom, "mom_act")
            elif res.get("mode") == "MOMENTUM":
                st.info("No name met the momentum standard. Quality over quantity — an empty "
                        "list is the correct output on a directionless afternoon.")
            if res.get("momentum_error"):
                st.caption(f"momentum scan unavailable ({res['momentum_error']})")
            with st.expander(f"every confirmed break, ranked ({res.get('n_triggers', 0)})"):
                _AR = pd.DataFrame(res.get("all_ranked") or [])
                if len(_AR):
                    if "side" in _AR:
                        _AR["side"] = _AR["side"].map(side_dot)
                    if "mtf" in _AR:
                        _AR["mtf"] = _AR["mtf"].map(mtf_dot)
                    if "gate_pass" in _AR:
                        _AR["gate_pass"] = _AR["gate_pass"].map(gate_dot)
                D = _clean(_AR,
                           ["sym", "sector", "side", "trig_clock", "prob_pct", "p3_pct",
                            "entry", "sl_pct", "rr", "mtf", "trig_min", "orb_range_pct",
                            "atr_pct", "adr20", "gap", "sec_rel", "gate_pass", "gate_note",
                            "moved"],
                           dict(SIG_NAMES, trig_clock="Break time", gate_pass="Gate",
                                gate_note="Why"))
                show_df(D, key="orb_all")
            with st.expander("rejected — and exactly why"):
                _RJ = pd.DataFrame(res.get("rejected") or [])
                if len(_RJ) and "side" in _RJ:
                    _RJ["side"] = _RJ["side"].map(side_dot)
                R = _clean(_RJ,
                           ["sym", "side", "trig_clock", "prob_pct", "trig_min",
                            "orb_range_pct", "atr_pct", "adr20", "gate_note"],
                           dict(SIG_NAMES, trig_clock="Break time", gate_note="Why"))
                show_df(R, key="orb_rej")
            with st.expander("sector strength (the rotation signal)"):
                sec = res.get("sector_early") or {}
                SD = pd.DataFrame([{"Sector": k, "Median move %": nz(v[0]),
                                    "Breadth % up": nz(v[1], 0)}
                                   for k, v in sec.items()]).sort_values("Median move %",
                                                                        ascending=False)
                show_df(SD, key="orb_sec",
                        help_map={"Median move %": "Median early move of the names in that sector.",
                                  "Breadth % up": "Percent of the sector's names trading up."})
                st.caption("From the 24–26 Aug case study: on two of the three days, sector beta "
                           "explained more of the big movers than any company news did.")
        else:
            st.info(f"No scan yet. The opening range needs at least one completed "
                    f"{CFG['ORB_MINUTES']}-minute candle.")

    with sub[2]:
        tab_header("🌙 Tomorrow's shortlist", "eod",
                   f"Ranked on expected-move capability · profile {MTF_PROFILE} · plan assumes "
                   f"the {ORB_TF_LABEL.get(CFG['ORB_MINUTES'], CFG['ORB_MINUTES'])}")
        st.caption("Names capable of a 2%+ range tomorrow. Direction is decided by tomorrow's "
                   "opening-range break, in either direction, and only before "
                   f"{CFG['ORB_CUTOFF_TIME'].strftime('%H:%M')}.")
        _eod_out = scan_button("🌙 Run EOD scan", "eod", scan_eod,
                               help_running="ranking the F&O list on expansion capability + "
                                            "daily/1-hour context…",
                               capital=capital, orb_minutes=CFG["ORB_MINUTES"], profile=MTF_PROFILE)
        if _eod_out is not None:
            st.session_state.res_eod = _eod_out
        res = st.session_state.get("res_eod") or load_scan("eod")
        if res and not res.get("error"):
            st.caption(f"for {res.get('for_date')} · generated "
                       f"{res.get('generated_at') or res.get('ts')} · "
                       f"regime {regime_dot(res.get('regime'))} · "
                       f"{res.get('n')} names screened · "
                       f"{res.get('history_sessions') or '—'} sessions of history")
            top = res.get("top") or []
            v = view_toggle("eod")
            if v == "List":
                D = pd.DataFrame([{"#": i, "Symbol": p["row"]["sym"], "Sector": p["row"]["sector"],
                                   "Signal time": p.get("signal_time"),
                                   "MTF": mtf_dot(p["row"].get("mtf")),
                                   "Close": nz(p["row"].get("close")),
                                   "P(move) %": nz(p["row"].get("p_move2"), 1),
                                   "ATR %": nz(p["row"].get("atr_pct")),
                                   "ADR %": nz(p["row"].get("adr20")),
                                   "Days ≥2.5%/20": p["row"].get("big20"),
                                   "RVOL": nz(p["row"].get("rvol")),
                                   "R:R": f"1:{p.get('rr')}" if p.get("rr") else "—",
                                   "Pivot": nz((p.get("pivot_D") or {}).get("P")),
                                   "R1": nz((p.get("pivot_D") or {}).get("R1")),
                                   "S1": nz((p.get("pivot_D") or {}).get("S1"))}
                                  for i, p in enumerate(top, 1)])
                show_df(D, key="eod",
                        help_map={"R1": "First resistance pivot — the long's first obstacle.",
                                  "S1": "First support pivot — the short's first obstacle.",
                                  "Days ≥2.5%/20": "Sessions out of the last 20 that moved ≥2.5%."})
                if top:
                    st.caption("ⓘ Hover any column header for what it means. " + top[0].get("note", ""))
            else:
                for p in top:
                    r = p["row"]
                    st.markdown(f"##### {r['sym']} · {r['sector']} · {mtf_dot(r.get('mtf'))}")
                    cA, cB = st.columns([2, 3])
                    cA.metric("P(2% move tomorrow)", f"{num(r.get('p_move2'), 1)}%",
                              help="Base-rate probability the stock travels 2% intraday.")
                    cA.caption(f"ATR {num(r.get('atr_pct'))}% · ADR {num(r.get('adr20'))}% · "
                               f"RVOL {num(r.get('rvol'))} · R:R 1:{p.get('rr')}")
                    with cB:
                        pivot_table({"D": p.get("pivot_D"), "W": p.get("pivot_W"),
                                     "M": p.get("pivot_M")})
                    st.caption(p.get("note", ""))
                    with st.expander(f"planned tickets for {r['sym']} — both directions"):
                        kA, kB = st.columns(2)
                        with kA:
                            if p.get("ticket"):
                                ticket_card(p["ticket"])
                        with kB:
                            if p.get("ticket_short"):
                                ticket_card(p["ticket_short"], low_conf=True)
            dl(_clean(res.get("table")), "eod_full.csv", "eod_full")
        else:
            st.info("No EOD scan yet.")

    with sub[3]:
        render_aggressive_tab(capital)

with colS:
    st.markdown("## 📈 SWING")
    tab_header("📈 Weekly swing plan", "swing",
               f"SWING profile · Daily chart for the full-day structure → Monday "
               f"{ORB_TF_LABEL.get(CFG['SWING_ORB_MINUTES'], CFG['SWING_ORB_MINUTES'])} "
               f"entry · LONG only")
    st.caption("Calibrated on 133 weeks of 60-min bars. P(+10% before a −5% stop) is 2.2% "
               "unconditionally and 11.1% on the top-3 ranked names, so the ladder is "
               "+3% / +5% / +10% — the 10% is the runner, not the plan.")
    _sw_out = scan_button("📈 Run swing scan", "sw", scan_swing,
                          help_running="building weekly features…",
                          capital=capital, orb_minutes=CFG["SWING_ORB_MINUTES"],
                          profile=PROFILE_SWING)
    if _sw_out is not None:
        st.session_state.res_sw = _sw_out
    res = st.session_state.get("res_sw") or load_scan("swing")
    if res and not res.get("error"):
        st.caption(f"for week starting {res.get('for_week', '—')} · generated "
                   f"{res.get('generated_at') or res.get('ts')} · "
                   f"entry {res.get('orb_tf') or '—'} · "
                   f"{res.get('n', 0)} names screened · "
                   f"{res.get('history_sessions') or '—'} sessions of history")
        if res.get("entry_rule"):
            st.info("📌 " + res["entry_rule"])
        picks = res.get("top") or []
        v = view_toggle("sw")
        if v == "List":
            D = pd.DataFrame([{"#": i, "Symbol": p["row"]["sym"],
                               "Sector": p["row"].get("sector"),
                               "Side": side_dot("BUY"),
                               "Signal time": (p.get("ticket") or {}).get("signal_time"),
                               "Close": nz(p["row"].get("px")),
                               "P(+7% before stop) %": nz((p.get("p") or 0) * 100, 1),
                               "ATR %": nz(p["row"].get("atr_pct")),
                               "Entry": nz((p.get("ticket") or {}).get("entry")),
                               "Qty": (p.get("ticket") or {}).get("qty"),
                               "Stop": nz((p.get("ticket") or {}).get("sl")),
                               "Target": nz((p.get("ticket") or {}).get("target")),
                               "R:R": f"1:{(p.get('ticket') or {}).get('rr')}",
                               "Risk ₹": nz((p.get("ticket") or {}).get("risk_rs"), 0),
                               "Reward ₹": nz((p.get("ticket") or {}).get("reward_rs"), 0)}
                              for i, p in enumerate(picks, 1)])
            show_df(D, key="sw",
                    help_map={"P(+7% before stop) %": "Model probability of +7% before the "
                                                      "−4% stop, on weekly features."})
        else:
            for p in picks:
                ticket_card(p["ticket"], prob=p.get("p"), ladder=p.get("ladder"),
                            extra_rows=[("ATR %", num(p["row"].get("atr_pct"))),
                                        ("21d return", pct(p["row"].get("ret21"))),
                                        ("Distance to 20d high", pct(p["row"].get("d_hi20")))])
        dl(_clean(res.get("table")), "swing_full.csv", "sw_full")
    else:
        st.info("No swing scan yet. Run it on Friday after the close or over the weekend.")


st.divider()
MORE_TABS = st.tabs(["🔁 Replay / backtest", "📊 Movers", "🧠 Learning", "📓 Journal"])
# ---------------------------------------------------------------- REPLAY
with MORE_TABS[0]:
    tab_header("🔁 Replay & backtest", "replay",
               f"Profile {MTF_PROFILE} · entry range "
               f"{ORB_TF_LABEL.get(CFG['ORB_MINUTES'], CFG['ORB_MINUTES'])}")
    st.caption("Answers 'what would the screener have told me on that day', and then shows "
               "what actually happened to those exact tickets. "
               f"Data depth: 5-minute bars ≈ {CFG['REPLAY_5M_DAYS']} calendar days, "
               f"15-minute ≈ 60 days, 1-hour and 2-hour ≈ {CFG['REPLAY_60M_DAYS']} days. "
               "Switch the sidebar to the SWING profile (1-hour / 2-hour entry range) to "
               "backtest across a much longer history — that is where the sample size "
               "lives. The INTRADAY profile (5m / 15m) is limited to about 60 days.")
    st.markdown("##### Single session")
    rc1, rc2, rc3 = st.columns([2, 1, 1])
    _def = prev_trading_day()
    _span = (CFG["REPLAY_60M_DAYS"] if CFG["ORB_MINUTES"] >= 60 else CFG["REPLAY_5M_DAYS"])
    rday = rc1.date_input("Session to replay", value=_def,
                          min_value=_def - timedelta(days=int(_span)), max_value=_def,
                          key="replay_day",
                          help="How far back you can go depends on the entry timeframe "
                               "chosen in the sidebar.")
    rtop = rc2.selectbox("Signals", [1, 2, 3], index=0, key="replay_top",
                         help="How many top-ranked tickets the engine was allowed to issue.")
    if rc3.button("🔁 Replay", type="primary", key="btn_replay"):
        with st.spinner(f"replaying {rday}…"):
            st.session_state.res_replay = replay_session(
                rday, capital=capital, top_n=rtop,
                orb_minutes=CFG["ORB_MINUTES"], profile=MTF_PROFILE)
    rep = st.session_state.get("res_replay")
    if rep:
        if rep.get("error"):
            st.error(rep["error"])
        else:
            st.caption(f"{rep['day']} · {rep['n_names']} liquid names · "
                       f"bars found for {rep['n_bars']} · "
                       f"{ORB_TF_LABEL.get(rep.get('orb_minutes'), rep.get('orb_minutes'))}")
            if rep.get("bar_note"):
                st.info("📊 " + rep["bar_note"])
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
                        st.markdown("**\U0001F7E2 Top BUY candidates**")
                        show_df(D, key="rep_pm_b")
                        st.markdown("**\U0001F534 Top SELL candidates**")
                        show_df(D2, key="rep_pm_s")
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
            with t2:
                if rep.get("orb_error"):
                    st.warning(rep["orb_error"])
                orb = rep.get("orb") or {}
                act = orb.get("actionable") or []
                if act:
                    won = [a for a in act if a.get("rp_result") == "TARGET HIT"]
                    q1, q2, q3 = st.columns(3)
                    q1.metric("Signals", len(act),
                              help="Tickets the screener would actually have issued.")
                    q2.metric("Target hit", f"{len(won)}/{len(act)}",
                              help="How many reached the full target before the stop.")
                    q3.metric("Avg result", pct(np.mean([a.get("rp_pnl_pct", 0) for a in act])),
                              help="Average per-trade return after the half-booking rule.")
                    render_signals(act, "rep_orb")
                elif not rep.get("orb_error"):
                    st.info("Nothing passed the confluence gate on that session — the "
                            "screener would correctly have told you to stay out.")
                if orb.get("all_ranked"):
                    with st.expander("every confirmed break that session"):
                        _RB = pd.DataFrame(orb["all_ranked"])
                        if "side" in _RB:
                            _RB["side"] = _RB["side"].map(side_dot)
                        if "gate_pass" in _RB:
                            _RB["gate_pass"] = _RB["gate_pass"].map(gate_dot)
                        if "mtf" in _RB:
                            _RB["mtf"] = _RB["mtf"].map(mtf_dot)
                        D = _clean(_RB,
                                   ["sym", "sector", "side", "trig_clock", "prob_pct",
                                    "p3_pct", "entry", "sl_pct", "rr", "mtf", "trig_min",
                                    "orb_range_pct", "gate_pass", "gate_note"],
                                   dict(SIG_NAMES, trig_clock="Break time",
                                        gate_pass="Gate", gate_note="Why"))
                        show_df(D, key="rep_orb_all")
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
                                     "MTF": mtf_dot(p["row"].get("mtf")),
                                     "P(2% move) %": nz(p["row"].get("p_move2"), 1),
                                     "ATR %": nz(p["row"].get("atr_pct")),
                                     "R:R": f"1:{p.get('rr')}" if p.get("rr") else "—",
                                     "Next session": nd.get("date"),
                                     "Max up from open %": nz(nd.get("up_pct")),
                                     "Max down from open %": nz(nd.get("dn_pct"))})
                    show_df(pd.DataFrame(rows), key="rep_eod",
                            help_map={"Max up from open %": "What the name actually "
                                                           "delivered upward the next session.",
                                      "Max down from open %": "What it actually delivered "
                                                              "downward the next session."})
                    st.caption("'Max up/down from open' is what the name actually delivered "
                               "the next session — a direct scorecard for the EOD ranking.")

    # ---- v22 (9): multi-session backtest -----------------------------------
    st.divider()
    st.markdown("##### Multi-session backtest " + info_badge(
        "Runs the exact live screener — same confluence gate, same time gate, same "
        "higher-timeframe filter, same stop and target rules — across a block of past "
        "sessions and scores every ticket it would have issued. This is the honest "
        "accuracy number: it is measured, not assumed. Longer entry ranges reach much "
        "further back, so the SWING profile's 1-hour range gives a far bigger sample "
        "than the INTRADAY profile's 5-minute range."),
        unsafe_allow_html=True)
    bc1, bc2, bc3 = st.columns([1, 1, 1])
    _max_sess = 220 if CFG["ORB_MINUTES"] >= 60 else 40
    bsess = bc1.slider("Sessions to test", 5, _max_sess, min(20, _max_sess), step=5,
                       key="bt_sess",
                       help="Trading sessions counted back from the last close. "
                            "More sessions = better statistics, slower run.")
    btop = bc2.selectbox("Signals per session", [1, 2, 3], index=0, key="bt_top",
                         help="How many top-ranked tickets the engine may issue per day.")
    bgo = bc3.button("🧪 Run backtest", type="primary", key="btn_bt")
    st.caption(f"At the current setting the window reaches back roughly "
               f"{int(bsess * 1.45)} calendar days — inside the "
               f"{_span}-day limit for "
               f"{ORB_TF_LABEL.get(CFG['ORB_MINUTES'], CFG['ORB_MINUTES'])} data.")
    if bgo:
        _b = load_bundle()
        _days = []
        _d = prev_trading_day()
        while len(_days) < int(bsess):
            if _d.weekday() < 5:
                _days.append(_d)
            _d = _d - timedelta(days=1)
        _days = list(reversed(_days))
        _pb = st.progress(0.0, text="starting…")

        def _prog(frac, msg=""):
            _pb.progress(min(max(frac, 0.0), 1.0), text=msg)

        with st.spinner(f"replaying {len(_days)} sessions…"):
            st.session_state.res_bt = replay_range(
                _days, capital=capital, bundle=_b, top_n=btop,
                orb_minutes=CFG["ORB_MINUTES"], profile=MTF_PROFILE, progress=_prog)
        _pb.empty()
    bt = st.session_state.get("res_bt")
    if bt:
        S = bt["summary"]
        if not S.get("trades"):
            st.warning(f"{S.get('sessions', 0)} sessions replayed and the screener issued "
                       "no ticket at all. That is a real result, not a bug — the gate is "
                       "strict by design. Widen the sessions, or relax the higher-timeframe "
                       "requirement in the sidebar.")
        else:
            k1, k2, k3, k4 = st.columns(4)
            k1.metric("Sessions / trades", f"{S['sessions']} / {S['trades']}",
                      help="Sessions replayed and tickets the engine actually issued.")
            k2.metric("Win rate", f"{S['win_rate']}%",
                      help="Share of tickets that closed positive after the half-booking rule.")
            k3.metric("Full target hit", f"{S['target_rate']}%",
                      help="Share that reached the full target before the stop.")
            k4.metric("Expectancy / trade", f"{S['expectancy']}%",
                      help="Average return per ticket. This is the number that matters.")
            j1, j2, j3, j4 = st.columns(4)
            j1.metric("Avg win", f"{S['avg_win']}%")
            j2.metric("Avg loss", f"{S['avg_loss']}%")
            j3.metric("Best / worst", f"{S['best']}% / {S['worst']}%")
            j4.metric("Stopped out", S.get("stopped", 0),
                      help="Tickets that hit the hard stop.")
            st.caption(f"🕒 measured on {S['sessions']} sessions · "
                       f"{ORB_TF_LABEL.get(CFG['ORB_MINUTES'], CFG['ORB_MINUTES'])} · "
                       f"profile {MTF_PROFILE} · "
                       f"total {S['total_pnl']}% across all tickets")
            T = bt["trades"].copy()
            if len(T):
                if "side" in T:
                    T["side"] = T["side"].map(side_dot)
                if "mtf" in T:
                    T["mtf"] = T["mtf"].map(mtf_dot)
                if "outcome" in T:
                    T["outcome"] = T["outcome"].map(outcome_dot)
                T = T.rename(columns={"day": "Date", "sym": "Symbol", "side": "Side",
                                      "signal_time": "Signal time", "entry": "Entry",
                                      "sl_pct": "Stop %", "rr": "R:R", "mtf": "MTF",
                                      "prob_pct": "P(target) %", "outcome": "Outcome",
                                      "hit_time": "Hit time", "exit_time": "Exit time",
                                      "pnl_pct": "P&L %", "mfe_pct": "Best excursion %"})
                with st.expander(f"every backtested ticket ({len(T)})", expanded=True):
                    show_df(T, key="bt_trades")
            BD = bt.get("by_day")
            if BD is not None and len(BD):
                with st.expander("session by session"):
                    show_df(BD.rename(columns={"day": "Date", "n_trades": "Tickets",
                                               "pnl": "Day P&L %"}), key="bt_days")

# ---------------------------------------------------------------- MOVERS
with MORE_TABS[1]:
    tab_header("📊 Top gainers & losers", "movers", "Automatic from the price panel, "
                                                     "plus your own manual list")
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
                show_df(hist.sort_values("captured_at", ascending=False), key="mv_hist")
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
                show_df(man.sort_values("added_at", ascending=False), key="mv_man_dl")
            else:
                for _, r in man.sort_values("added_at", ascending=False).head(30).iterrows():
                    colr = "#0ecb81" if r["kind"] == "GAINER" else "#f6465d"
                    st.markdown(f"<div style='background:#11141f;border-left:3px solid {colr};"
                                f"border-radius:8px;padding:8px 12px;margin-bottom:6px'>"
                                f"<b>{r['sym']}</b> <span style='color:{colr}'>"
                                f"{r['ret_pct']}%</span> · {r['period']} {r['period_label']}"
                                f"<br><span style='font-size:11px;color:#8f96b3'>"
                                f"{r['reason']}</span></div>", unsafe_allow_html=True)
                dl(man, "manual_movers.csv", "mv_man_cards")

# ---------------------------------------------------------------- LEARNING
with MORE_TABS[2]:
    tab_header("🧠 What separates the big movers", "learn",
               "Measured on the PREVIOUS day's features, so it is usable before the move")
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
            show_df(D, key="learn",
                    help_map={"Movers (median)": "Median value among names that then ran.",
                              "Everything else": "Median value across all other name-days.",
                              "Ratio": "Movers median divided by the baseline median. "
                                       "Blank when the baseline sits on zero.",
                              "Difference": "Absolute gap — read this when Ratio is blank."})
        else:
            for _, r in D.iterrows():
                tail = (f"**{r['Ratio']}×**" if r["Ratio"] == r["Ratio"] and r["Ratio"]
                        else f"gap **{r['Difference']:+}**")
                st.markdown(f"**{r['Feature']}** — movers {r['Movers (median)']} vs "
                            f"{r['Everything else']} elsewhere · {tail}")
            dl(D, "mover_profile.csv", "learn_cards")
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
with MORE_TABS[3]:
    tab_header("📓 Journal", "journal",
               "Every generated signal is logged automatically · add your own fills manually")
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
            k1.metric("Signals logged", len(A),
                      help="Every ticket the engines produced, traded or not.")
            k2.metric("Sessions covered", A.for_date.nunique())
            k3.metric("Gate passed", int((A.gate == "PASS").sum()),
                      help="Signals that cleared the confluence gate.")
            scored = A[A.pnl_pct.notna() & (A.pnl_pct.astype(str) != "")]
            if not scored.empty:
                k4.metric("Avg result", pct(pd.to_numeric(scored.pnl_pct,
                                                          errors="coerce").mean()),
                          help="Average outcome of the signals that have been scored.")
            v = view_toggle("j_auto")
            show = A.sort_values("logged_at", ascending=False)
            if v == "List":
                _S = show.copy()
                if "side" in _S:
                    _S["side"] = _S["side"].map(side_dot)
                if "outcome" in _S:
                    _S["outcome"] = _S["outcome"].map(outcome_dot)
                if "mtf" in _S:
                    _S["mtf"] = _S["mtf"].map(mtf_dot)
                _S = _S.rename(columns={
                    "logged_at": "Logged at", "for_date": "For date",
                    "signal_time": "Signal time", "engine": "Engine", "setup": "Setup",
                    "orb_tf": "Entry TF", "mtf": "MTF", "sym": "Symbol",
                    "sector": "Sector", "side": "Side", "entry": "Entry", "sl": "Stop",
                    "sl_pct": "Stop %", "target": "Target", "target_pct": "Target %",
                    "rr": "R:R", "risk_rs": "Risk ₹", "reward_rs": "Reward ₹",
                    "qty": "Qty", "prob_pct": "P(target) %", "p3_pct": "P(+3%) %",
                    "trig_min": "Break in (min)", "trig_time": "Break time",
                    "orb_h": "Range high", "orb_l": "Range low", "gate": "Gate",
                    "note": "Why", "outcome": "Outcome", "hit_time": "Hit time",
                    "exit_time": "Exit time", "exit_px": "Exit price",
                    "pnl_pct": "P&L %"})
                show_df(_S, key="j_auto")
            else:
                for _, r in show.head(25).iterrows():
                    colr = "#0ecb81" if r["side"] == "BUY" else "#f6465d"
                    st.markdown(
                        f"<div style='background:#11141f;border-left:3px solid {colr};"
                        f"border-radius:8px;padding:9px 12px;margin-bottom:6px'>"
                        f"<b>{r['sym']}</b> <span style='color:{colr};font-weight:700'>"
                        f"{side_dot(r['side'])}</span> · {r['engine']} · {r['for_date']}"
                        f" · 🕒 {r.get('signal_time') or '—'}<br>"
                        f"<span style='font-size:11px;color:#8f96b3'>entry {r['entry']} · "
                        f"SL {r['sl']} · target {r['target']} · R:R 1:{r.get('rr') or '—'} · "
                        f"qty {r['qty']} · P {r['prob_pct']}% · gate {r['gate']}"
                        + (f" · {r.get('outcome')} at {r.get('hit_time') or r.get('exit_time')}"
                           if r.get("outcome") else "")
                        + "</span></div>", unsafe_allow_html=True)
                dl(A, "journal_auto_signals.csv", "j_auto_cards")
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
            # v22 (1): the clock times are part of the record now
            i1, i2 = st.columns(2)
            t_in = i1.time_input("Entry time", value=dtime(9, 20),
                                 help="When you actually got filled.")
            t_out = i2.time_input("Exit time", value=dtime(15, 10),
                                  help="When the stop or target hit, or when you closed it.")
            g1, g2, g3, g4 = st.columns(4)
            ten = g1.number_input("Entry", 0.0, 1000000.0, 0.0, 0.05)
            tex = g2.number_input("Exit", 0.0, 1000000.0, 0.0, 0.05)
            tsl = g3.number_input("Stop", 0.0, 1000000.0, 0.0, 0.05)
            ttg = g4.number_input("Target", 0.0, 1000000.0, 0.0, 0.05)
            h1, h2, h3 = st.columns(3)
            tsetup = h1.selectbox("Setup", ["Intraday 5-min ORB", "Intraday 15-min ORB",
                                            "Momentum (post-12:30)",
                                            "Pre-market watchlist", "EOD list",
                                            "Swing 1-hour ORB", "Swing 2-hour ORB",
                                            "Manual / discretionary"],
                                  help="The intraday profile enters on the 5 or 15-min "
                                       "opening range; the swing profile enters on the "
                                       "1 or 2-hour opening range.")
            tfol = h2.selectbox("Followed the plan?", ["Yes", "No", "Partly"])
            temo = h3.selectbox("State of mind", ["Calm", "Rushed", "Revenge", "FOMO",
                                                  "Hesitant"])
            tnotes = st.text_area("Notes")
            if st.form_submit_button("Save trade", type="primary"):
                if tsym and ten > 0:
                    sgn = 1 if tside == "BUY" else -1
                    pp = ((tex / ten - 1) * 100 * sgn) if tex > 0 else None
                    # v22 (6): R:R computed from the stop and target you entered
                    _risk = abs(ten - tsl) if tsl > 0 else None
                    _rew = abs(ttg - ten) if ttg > 0 else None
                    _rr = (round(_rew / _risk, 2)
                           if _risk and _rew and _risk > 0 else None)
                    rec_ = dict(trade_date=str(td), entry_time=t_in.strftime("%H:%M:%S"),
                                exit_time=t_out.strftime("%H:%M:%S"),
                                sym=tsym, side=tside, entry=ten,
                                exit_px=tex, qty=tqty, sl=tsl, target=ttg, rr=_rr,
                                pnl_pct=round(pp, 3) if pp is not None else None,
                                pnl_rs=round((tex - ten) * tqty * sgn, 2) if tex > 0 else None,
                                setup=tsetup, followed_plan=tfol, emotion=temo,
                                notes=tnotes)
                    journal_manual_add(rec_)
                    st.success(f"{tsym} saved" + (f" · R:R 1:{_rr}" if _rr else ""))
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
                _M = show.copy()
                if "side" in _M:
                    _M["side"] = _M["side"].map(side_dot)
                _M = _M.rename(columns={
                    "trade_date": "Date", "entry_time": "Entry time",
                    "exit_time": "Exit time", "sym": "Symbol", "side": "Side",
                    "entry": "Entry", "exit_px": "Exit price", "qty": "Qty",
                    "sl": "Stop", "target": "Target", "rr": "R:R",
                    "pnl_pct": "P&L %", "pnl_rs": "P&L ₹", "setup": "Setup",
                    "followed_plan": "Followed plan", "emotion": "State of mind",
                    "notes": "Notes"})
                show_df(_M, key="j_man")
            else:
                for _, r in show.head(25).iterrows():
                    p_ = pd.to_numeric(pd.Series([r["pnl_pct"]]), errors="coerce").iloc[0]
                    colr = "#0ecb81" if (p_ == p_ and p_ > 0) else "#f6465d"
                    st.markdown(
                        f"<div style='background:#11141f;border-left:3px solid {colr};"
                        f"border-radius:8px;padding:9px 12px;margin-bottom:6px'>"
                        f"<b>{r['sym']}</b> {side_dot(r['side'])} · {r['trade_date']} "
                        f"🕒 {r.get('entry_time') or '—'} → {r.get('exit_time') or '—'}"
                        f"<span style='color:{colr};font-weight:700;float:right'>"
                        f"{pct(r['pnl_pct'])}</span><br>"
                        f"<span style='font-size:11px;color:#8f96b3'>{r['setup']} · "
                        f"R:R 1:{r.get('rr') or '—'} · "
                        f"plan: {r['followed_plan']} · {r['emotion']}<br>{r['notes']}"
                        f"</span></div>", unsafe_allow_html=True)
                dl(M, "journal_manual_trades.csv", "j_man_cards")
            if len(pn_) >= 5:
                st.markdown("##### Review")
                by = M.copy()
                by["p"] = pd.to_numeric(by.pnl_pct, errors="coerce")
                agg = by.groupby("setup").p.agg(["count", "mean"]).round(3) \
                        .rename(columns={"count": "Trades", "mean": "Avg %"}) \
                        .reset_index().rename(columns={"setup": "Setup"})
                show_df(agg, key="j_review",
                        help_map={"Avg %": "Your average result per trade for that setup."})
                fp = by.groupby("followed_plan").p.mean().round(3)
                if "Yes" in fp and "No" in fp:
                    st.caption(f"When you followed the plan: {fp['Yes']:+.2f}% average. "
                               f"When you did not: {fp['No']:+.2f}%.")

st.divider()
st.caption("QUANT-EDGE v22 · For research and personal use. Every probability shown is a "
           "walk-forward measurement, not a promise. On 1,026 walk-forward sessions top-1 "
           "long hit its +2% target on 27.2% of sessions, short of the 34.6% break-even — "
           "the other two thirds ended at breakeven or the 1% stop. Those figures were "
           "measured on the earlier 5-minute build; v22 loads a deeper history "
           f"({CFG['DAILY_HISTORY']} daily, {CFG['SWING_HISTORY']} for swing, up to "
           f"{CFG['REPLAY_60M_DAYS']} days of 60-minute bars) and the Replay tab now has a "
           "multi-session backtest, so run it on your chosen timeframe and profile and "
           "trust that number over this caption. Position size accordingly.")
