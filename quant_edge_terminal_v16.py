# =============================================================================
# QUANT EDGE TERMINAL v17 — VPA + HILEGA-MILEGA + MTF + ORB + LEARNING
# Streamlit app (single file). Designed for NSE F&O universe.
# Primary data: Fyers API. Fallbacks: yfinance, NSE wrappers.
# =============================================================================
import os, sys, math, time, datetime as dt, threading, json, hashlib
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
import numpy as np
import pandas as pd

# Streamlit + plotting
import streamlit as st
import plotly.graph_objects as go
import plotly.express as px

# Fyers & fallbacks
try:
    from fyers_v2 import fyers
    HAS_FYERS = True
except Exception:
    HAS_FYERS = False

try:
    import yfinance as yf
    HAS_YF = True
except Exception:
    HAS_YF = False

try:
    import requests
    HAS_REQ = True
except Exception:
    HAS_REQ = False

# Optional: auto-refresh
try:
    from streamlit_autorefresh import st_autorefresh
    HAS_AUTOREFRESH = True
except Exception:
    HAS_AUTOREFRESH = False

# =============================================================================
# GLOBAL CONFIGURATION (editable via UI later; these are defaults)
# =============================================================================
CFG = {
    "fno_universe_source": "static_fallback",
    "volume_lookback": 20,
    "volume_tiers": {
        "normal_max": 1.25,
        "high_min": 1.5,
        "very_high_min": 2.0,
        "extreme_min": 2.5,
    },
    "hm_rsi_len": 9,
    "hm_wma_len": 21,
    "hm_ema_len": 3,
    "hm_50_level": 50.0,
    "intraday_tf_primary": "1h",
    "intraday_tf_secondary": "1d",
    "swing_tf_primary": "1d",
    "swing_tf_secondary": "1w",
    "orb_intraday_mode": "5m",
    "orb_swing_mode": "weekly_1h",
    "learn_retrain_day": "Friday",
    "learn_deploy_day": "Monday",
    "learn_min_samples": 200,
    "learn_validation_split": 0.2,
    "learn_max_features": 16,
    "transaction_cost_pct": 0.05,
    "slippage_pct": 0.05,
}

# =============================================================================
# STREAMLIT PAGE CONFIG
# =============================================================================
st.set_page_config(
    page_title="QUANT-EDGE v17 — VPA+HM+MTF+ORB+Learning",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# =============================================================================
# PATHS & PERSISTENCE
# =============================================================================
BASE_DIR = Path(__file__).parent.resolve()
DATA_DIR = BASE_DIR / "qe_data"
DATA_DIR.mkdir(exist_ok=True)

FNO_LIST_PATH = DATA_DIR / "fno_universe.json"
DB_PATH = DATA_DIR / "trade_outcomes.db"
MODEL_PATH = DATA_DIR / "hm_vpa_model.json"
LOG_PATH = DATA_DIR / "screen_log.parquet"

# =============================================================================
# UTILITIES
# =============================================================================
def now_ts() -> dt.datetime:
    return dt.datetime.now().astimezone()

def ist_now() -> dt.datetime:
    tz = dt.timezone(dt.timedelta(hours=5, minutes=30))
    return dt.datetime.now(tz)

def ns(v, r=2, dash="—"):
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return dash
    return f"{v:,.{r}f}".rstrip("0").rstrip(".")

def nz(v, r=2):
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return None
    return round(float(v), r)

def save_json(path: Path, obj):
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, default=str)

def load_json(path: Path, default=None):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)

# =============================================================================
# F&O UNIVERSE (NSE)
# =============================================================================
FNO_STATIC = [
    "RELIANCE.NS","HDFCBANK.NS","INFY.NS","ICICIBANK.NS","TCS.NS","SBIN.NS","BHARTIARTL.NS",
    "ITC.NS","KOTAKBANK.NS","AXISBANK.NS","LT.NS","HCLTECH.NS","ASIANPAINT.NS","MARUTI.NS",
    "TITAN.NS","BAJFINANCE.NS","ULTRACEMCO.NS","WIPRO.NS","SUNPHARMA.NS","TATASTEEL.NS",
    "BAJAJFINSV.NS","ADANIENT.NS","POWERGRID.NS","NTPC.NS","M&M.NS","TATAMOTORS.NS",
    "JSWSTEEL.NS","TATACONSUM.NS","HINDUNILVR.NS","ONGC.NS","ADANIPORTS.NS","GRASIM.NS",
    "DRREDDY.NS","CIPLA.NS","EICHERMOT.NS","COALINDIA.NS","BPCL.NS","IOC.NS","GAIL.NS",
    "HEROMOTOCO.NS","SHREECEM.NS","BRITANNIA.NS","NESTLEIND.NS","PIDILITIND.NS",
    "DIVISLAB.NS","LTIM.NS","TECHM.NS","SIEMENS.NS","GODREJCP.NS","DABUR.NS",
    "HINDCOPPER.NS","BHEL.NS","INDUSINDBK.NS","PNB.NS","BANKBARODA.NS","CANBK.NS",
    "FEDERALBNK.NS","IDFCFIRSTB.NS","RBLBANK.NS","BANDHANBNK.NS","AUBANK.NS",
    "CHOLAFIN.NS","SHRIRAMFIN.NS","BAJAJ-AUTO.NS","TATAPOWER.NS","ADANIGREEN.NS",
    "ADANITRANS.NS","TORNTPHARM.NS","LUPIN.NS","BIOCON.NS","APOLLOHOSP.NS",
    "MAXHEALTH.NS","FORTIS.NS","NAUKRI.NS","MUTHOOTFIN.NS","LICHSGFIN.NS",
    "HDFCLIFE.NS","SBILIFE.NS","ICICIGI.NS","M&MFIN.NS","SRF.NS","PETRONET.NS",
    "OFSS.NS","PERSISTENT.NS","COFORGE.NS","POLICYBZR.NS","NYKAA.NS","PAYTM.NS",
    "VOLTAS.NS","CROMPTON.NS","WHIRLPOOL.NS","HAVELLS.NS","CUMMINSIND.NS",
    "THERMAX.NS","KEI.NS","POLYCAB.NS","FINCABLES.NS","NAVINFLUOR.NS",
    "SOLARINDS.NS","SUPREMEIND.NS","PIIND.NS","GARFIBRES.NS","BASF.NS",
    "UPL.NS","NUVOCO.NS","MPHASIS.NS","RAMCOCEM.NS","GRANULES.NS",
    "AARTIIND.NS","ALKYLAMINE.NS","DEEPAKNTR.NS","TATACHEM.NS","GSPL.NS",
    "IGL.NS","MGL.NS","ASTRAL.NS","KFINTECH.NS","CAMS.NS","BSE.NS",
    "MCX.NS","CDSL.NS","CRAINFRA.NS","IRB.NS","NHPC.NS","SJVN.NS",
    "RVNL.NS","IRFC.NS","IREDA.NS","PFC.NS","RECLTD.NS",
    "HAL.NS","BEL.NS","BDL.NS",
]

def fetch_fno_universe(timeout=8) -> Tuple[List[str], str]:
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

def get_fno_universe_cached() -> Tuple[List[str], str]:
    cached = load_json(FNO_LIST_PATH)
    if cached and isinstance(cached, dict) and "symbols" in cached:
        return cached["symbols"], cached.get("source", "cached")
    symbols, source = fetch_fno_universe()
    save_json(FNO_LIST_PATH, {"symbols": symbols, "source": source, "updated_at": str(ist_now())})
    return symbols, source

# =============================================================================
# DATA LAYER (Fyers primary; yfinance/NSE fallback)
# =============================================================================
@st.cache_data(ttl=300)
def get_fyers_client() -> Optional[Any]:
    if not HAS_FYERS:
        return None
    try:
        fy = fyers.Fyers_API(client_id="YOUR_CLIENT_ID", secret_key="YOUR_SECRET")
        tokens_path = DATA_DIR / "fyers_tokens.json"
        if tokens_path.exists():
            with tokens_path.open("r") as f:
                tok = json.load(f)
            fy.set_token(tok["access_token"])
            fy.refresh_token = tok.get("refresh_token")
            fy.token_expiry = tok.get("expiry")
        return fy
    except Exception:
        return None

def fetch_ohlcv_fyers(symbol: str, tf: str, days: int = 60) -> Optional[pd.DataFrame]:
    fy = get_fyers_client()
    if fy is None:
        return None
    try:
        tf_map = {
            "1m": "1Minute", "5m": "5Minute", "15m": "15Minute",
            "30m": "30Minute", "1h": "60Minute", "1d": "1Day", "1w": "1Week",
        }
        resolution = tf_map.get(tf, "1Day")
        sym = f"NSE:{symbol}"
        end_ts = int(time.time())
        start_ts = end_ts - days * 24 * 3600
        df = fy.get_history(symbol=sym, interval=resolution, start=start_ts, end=end_ts)
        if df is None or len(df) == 0:
            return None
        df = df.rename(columns={"o":"open","h":"high","l":"low","c":"close","v":"volume"})
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        df["time"] = df["time"].dt.tz_convert("Asia/Kolkata")
        df = df.set_index("time")[["open","high","low","close","volume"]]
        df = df.sort_index()
        return df
    except Exception:
        return None

def fetch_ohlcv_yf(symbol: str, tf: str, days: int = 365) -> Optional[pd.DataFrame]:
    if not HAS_YF:
        return None
    try:
        ticker = symbol.replace(".NS", "").replace(".BSE", "")
        ysym = f"{ticker}.NS"
        if tf in ["1d","1D"]:
            df = yf.download(ysym, period=f"{days}d", progress=False)
        elif tf in ["1w","1W"]:
            df = yf.download(ysym, period=f"{days}w", progress=False)
        else:
            return None
        if df.empty:
            return None
        df.columns = df.columns.get_level_values(0)
        df = df.rename(columns={"Open":"open","High":"high","Low":"low","Close":"close","Volume":"volume"})
        df = df[["open","high","low","close","volume"]].dropna()
        df.index = pd.to_datetime(df.index).tz_localize(None).tz_localize("UTC").tz_convert("Asia/Kolkata")
        return df
    except Exception:
        return None

def fetch_ohlcv(symbol: str, tf: str, days: int = 365, source_pref: str = "fyers") -> Optional[pd.DataFrame]:
    if source_pref == "fyers":
        df = fetch_ohlcv_fyers(symbol, tf, days)
        if df is not None:
            return df
    if tf in ["1d","1w"]:
        return fetch_ohlcv_yf(symbol, tf, days)
    return None



# =============================================================================
# VOLUME PRICE ANALYSIS (Anna Coulling) ENGINE
# =============================================================================
def volume_tier(vol_ratio: float, cfg: dict) -> str:
    tiers = cfg["volume_tiers"]
    if vol_ratio >= tiers["extreme_min"]:
        return "extreme"
    if vol_ratio >= tiers["very_high_min"]:
        return "very_high"
    if vol_ratio >= tiers["high_min"]:
        return "high"
    if vol_ratio > tiers["normal_max"]:
        return "normal_high"
    return "normal"

def detect_vpa_pattern(df: pd.DataFrame, cfg: dict) -> Dict[str, Any]:
    """
    Detect Anna Coulling VPA patterns on the latest bar.
    Returns dict with:
      - vol_ratio, vol_tier
      - context: "uptrend","downtrend","range"
      - candle_type: e.g., "hammer","shooting_star","narrow_spread_up/down","normal"
      - pattern: "stopping_volume","buying_climax","selling_climax","topping_volume",None
      - bias: "bullish","bearish","neutral"
      - strength: 0..3
    """
    if len(df) < cfg["volume_lookback"] + 5:
        return {"vol_ratio": None, "vol_tier": None, "context": None, "candle_type": None,
                "pattern": None, "bias": "neutral", "strength": 0}

    vol = df["volume"].values
    close = df["close"].values
    high = df["high"].values
    low = df["low"].values
    open_ = df["open"].values

    look = cfg["volume_lookback"]
    avg_vol = np.mean(vol[-look-1:-1])  # exclude current
    cur_vol = vol[-1]
    vol_ratio = cur_vol / avg_vol if avg_vol > 0 else 1.0
    vol_tier = volume_tier(vol_ratio, cfg)

    # Context: simple trend via slope of SMA(20) on close
    sma20 = np.mean(close[-look-1:-1])
    sma20_prev = np.mean(close[-look-5:-6:-1]) if len(close) >= look+5 else sma20
    if close[-1] > sma20 and sma20 > sma20_prev:
        context = "uptrend"
    elif close[-1] < sma20 and sma20 < sma20_prev:
        context = "downtrend"
    else:
        context = "range"

    # Candle structure
    body = abs(close[-1] - open_[-1])
    range_ = high[-1] - low[-1]
    upper_wick = high[-1] - max(close[-1], open_[-1])
    lower_wick = min(close[-1], open_[-1]) - low[-1]
    is_green = close[-1] >= open_[-1]

    if range_ == 0:
        candle_type = "doji"
    elif body / range_ < 0.3:
        candle_type = "narrow_spread"
    elif lower_wick > 2 * body and not is_green:
        candle_type = "hammer"
    elif upper_wick > 2 * body and is_green:
        candle_type = "shooting_star"
    elif is_green:
        candle_type = "normal_up"
    else:
        candle_type = "normal_down"

    # Pattern detection
    pattern = None
    bias = "neutral"
    strength = 0

    # Stopping volume: after down move, high/very_high volume, narrow spread or long lower wick
    if context == "downtrend" and vol_tier in ["high","very_high","extreme"]:
        if candle_type in ["hammer","narrow_spread"]:
            pattern = "stopping_volume"
            bias = "bullish"
            strength = 2 if vol_tier in ["very_high","extreme"] else 1

    # Buying climax: sharp decline into very high/extreme volume, exhaustion
    if context == "downtrend" and vol_tier in ["very_high","extreme"]:
        # Check prior 3-5 bars net down strongly
        prior_drop = (close[-1] - close[-6]) / close[-6] if len(close) >= 6 else 0
        if prior_drop < -0.05 and candle_type in ["hammer","narrow_spread","doji"]:
            pattern = "buying_climax"
            bias = "bullish"
            strength = 3 if vol_tier == "extreme" else 2

    # Selling climax: rally/top into very high/extreme volume, upper wicks/churning
    if context == "uptrend" and vol_tier in ["very_high","extreme"]:
        prior_gain = (close[-1] - close[-6]) / close[-6] if len(close) >= 6 else 0
        if prior_gain > 0.05 and candle_type in ["shooting_star","narrow_spread","doji"]:
            pattern = "selling_climax"
            bias = "bearish"
            strength = 3 if vol_tier == "extreme" else 2

    # Topping volume: after up move, high volume, narrow spread or long upper wick
    if context == "uptrend" and vol_tier in ["high","very_high","extreme"]:
        if candle_type in ["shooting_star","narrow_spread"]:
            pattern = "topping_volume"
            bias = "bearish"
            strength = 2 if vol_tier in ["very_high","extreme"] else 1

    return {
        "vol_ratio": float(vol_ratio),
        "vol_tier": vol_tier,
        "context": context,
        "candle_type": candle_type,
        "pattern": pattern,
        "bias": bias,
        "strength": int(strength),
    }

# =============================================================================
# HILEGA-MILEGA INDICATOR ENGINE (NK style)
# =============================================================================
def ema_series(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()

def wma_series(s: pd.Series, span: int) -> pd.Series:
    weights = np.arange(1, span+1)
    def rolling_wma(x):
        return np.dot(x, weights) / weights.sum()
    return s.rolling(window=span).apply(rolling_wma, raw=True)

def compute_hm(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """
    Compute Hilega-Milega components on OHLCV df.
    Adds columns: rsi, ema_rsi (price line), wma_rsi (strength line), hm_signal.
    hm_signal: "bull_cross","bear_cross","bull","bear","neutral".
    """
    close = df["close"]
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(span=cfg["hm_rsi_len"], adjust=False).mean()
    avg_loss = loss.ewm(span=cfg["hm_rsi_len"], adjust=False).mean()
    rs = avg_gain / (avg_loss + 1e-9)
    rsi = 100 - (100 / (1 + rs))

    ema_rsi = ema_series(rsi, cfg["hm_ema_len"])   # price line (green)
    wma_rsi = wma_series(rsi, cfg["hm_wma_len"])   # strength line (red)

    # Signal detection
    bull_cross = (ema_rsi > wma_rsi) & (ema_rsi.shift(1) <= wma_rsi.shift(1))
    bear_cross = (ema_rsi < wma_rsi) & (ema_rsi.shift(1) >= wma_rsi.shift(1))

    above_50 = (ema_rsi >= cfg["hm_50_level"]) & (wma_rsi >= cfg["hm_50_level"])
    below_50 = (ema_rsi <= cfg["hm_50_level"]) & (wma_rsi <= cfg["hm_50_level"])

    hm_signal = pd.Series("neutral", index=df.index)
    hm_signal[bull_cross] = "bull_cross"
    hm_signal[bear_cross] = "bear_cross"
    hm_signal[(~bull_cross) & (~bear_cross) & (ema_rsi > wma_rsi) & above_50] = "bull"
    hm_signal[(~bull_cross) & (~bear_cross) & (ema_rsi < wma_rsi) & below_50] = "bear"

    out = df.copy()
    out["rsi"] = rsi
    out["ema_rsi"] = ema_rsi
    out["wma_rsi"] = wma_rsi
    out["hm_signal"] = hm_signal
    return out

def hm_latest_signal(df_hm: pd.DataFrame) -> Dict[str, Any]:
    """
    Summarize latest HM state.
    Returns: cross_type, direction, above_below_50, strength_score.
    """
    if len(df_hm) < 30:
        return {"cross_type": None, "direction": "neutral", "above_below_50": None, "strength_score": 0}
    last = df_hm.iloc[-1]
    prev = df_hm.iloc[-2]
    cross_type = None
    direction = "neutral"
    if last["hm_signal"] == "bull_cross":
        cross_type = "bull_cross"
        direction = "bullish"
    elif last["hm_signal"] == "bear_cross":
        cross_type = "bear_cross"
        direction = "bearish"
    elif last["hm_signal"] == "bull":
        direction = "bullish"
    elif last["hm_signal"] == "bear":
        direction = "bearish"

    above_below_50 = None
    if last["ema_rsi"] >= 50 and last["wma_rsi"] >= 50:
        above_below_50 = "above"
    elif last["ema_rsi"] <= 50 and last["wma_rsi"] <= 50:
        above_below_50 = "below"

    # Strength score: distance of ema_rsi from 50 + slope
    slope = last["ema_rsi"] - prev["ema_rsi"]
    strength_score = abs(last["ema_rsi"] - 50) / 10.0 + abs(slope) / 2.0
    if direction == "bearish":
        strength_score = -strength_score

    return {
        "cross_type": cross_type,
        "direction": direction,
        "above_below_50": above_below_50,
        "strength_score": float(strength_score),
    }

# =============================================================================
# MTF CONFLUENCE & SIGNAL SCORING
# =============================================================================
def mtf_confluence(primary_df: pd.DataFrame, secondary_df: pd.DataFrame, cfg: dict) -> Dict[str, Any]:
    """
    Compute MTF confluence for a given mode (intraday: 1h+1d; swing: 1d+1w).
    Returns:
      - primary_hm, secondary_hm
      - vpa_primary, vpa_secondary
      - confluence_direction: "bullish","bearish","neutral"
      - confluence_strength: 0..3
      - signal_quality: "strong","early","neutral"
    """
    p_hm = compute_hm(primary_df, cfg)
    s_hm = compute_hm(secondary_df, cfg)
    p_hm_last = hm_latest_signal(p_hm)
    s_hm_last = hm_latest_signal(s_hm)

    p_vpa = detect_vpa_pattern(primary_df, cfg)
    s_vpa = detect_vpa_pattern(secondary_df, cfg)

    # Direction agreement
    dirs = [p_hm_last["direction"], s_hm_last["direction"]]
    if all(d == "bullish" for d in dirs):
        confluence_direction = "bullish"
    elif all(d == "bearish" for d in dirs):
        confluence_direction = "bearish"
    else:
        confluence_direction = "neutral"

    # Strength: combine HM strength + VPA strength
    hm_score = (p_hm_last["strength_score"] + s_hm_last["strength_score"]) / 2.0
    vpa_score = (p_vpa["strength"] + s_vpa["strength"]) / 2.0
    confluence_strength = min(3, int(abs(hm_score) + vpa_score))

    # Signal quality
    if confluence_direction == "neutral":
        signal_quality = "neutral"
    elif confluence_direction == "bullish":
        if p_hm_last["cross_type"] == "bull_cross" and p_vpa["bias"] == "bullish":
            signal_quality = "strong"
        elif p_hm_last["direction"] == "bullish" and p_vpa["bias"] == "bullish":
            signal_quality = "early"
        else:
            signal_quality = "neutral"
    else:  # bearish
        if p_hm_last["cross_type"] == "bear_cross" and p_vpa["bias"] == "bearish":
            signal_quality = "strong"
        elif p_hm_last["direction"] == "bearish" and p_vpa["bias"] == "bearish":
            signal_quality = "early"
        else:
            signal_quality = "neutral"

    return {
        "primary_hm": p_hm_last,
        "secondary_hm": s_hm_last,
        "primary_vpa": p_vpa,
        "secondary_vpa": s_vpa,
        "confluence_direction": confluence_direction,
        "confluence_strength": confluence_strength,
        "signal_quality": signal_quality,
    }




# =============================================================================
# ORB ENGINE (Intraday: 5m/15m; Swing: weekly 1H / monthly 1D)
# =============================================================================
def compute_orb_intraday(df_1m_or_5m: pd.DataFrame, mode: str = "5m") -> Optional[Dict[str, float]]:
    """
    Compute intraday ORB from intraday bars (1m/5m).
    mode: "5m" or "15m".
    Uses first N minutes of the trading day (09:15 IST).
    Returns {"orb_high":..., "orb_low":..., "orb_mid":..., "orb_width_pct":...} or None.
    """
    if df_1m_or_5m.empty:
        return None
    df = df_1m_or_5m.copy()
    df.index = pd.to_datetime(df.index)
    # Identify today's date in IST
    today = ist_now().date()
    today_df = df[df.index.date == today]
    if today_df.empty:
        # Fallback to last available date
        last_date = df.index.max().date()
        today_df = df[df.index.date == last_date]
        if today_df.empty:
            return None

    if mode == "5m":
        # First 5 minutes: 09:15 to 09:19 (if 1m data) or first 5m candle
        if len(today_df) >= 5:
            orb_candle = today_df.iloc[:5]
        else:
            orb_candle = today_df
    elif mode == "15m":
        if len(today_df) >= 15:
            orb_candle = today_df.iloc[:15]
        else:
            orb_candle = today_df
    else:
        orb_candle = today_df

    orb_high = float(orb_candle["high"].max())
    orb_low = float(orb_candle["low"].min())
    orb_mid = (orb_high + orb_low) / 2.0
    orb_width_pct = (orb_high - orb_low) / orb_mid * 100.0 if orb_mid > 0 else 0.0
    return {"orb_high": orb_high, "orb_low": orb_low, "orb_mid": orb_mid, "orb_width_pct": orb_width_pct}

def first_trading_day_of_week(df_daily: pd.DataFrame, ref_date: dt.date) -> Optional[dt.date]:
    """
    Given daily bars and a reference date (IST), find the first actual trading day
    of that ISO week (Mon-Sun) on or after the Monday of that week.
    """
    idx = pd.to_datetime(df_daily.index).tz_localize(None)
    df = df_daily.copy()
    df.index = idx
    # Monday of the week
    monday = ref_date - dt.timedelta(days=ref_date.weekday())
    # Scan forward from Monday until we find a trading day
    for d in range(10):
        cand = monday + dt.timedelta(days=d)
        if cand in df.index.date:
            return cand
    return None

def first_trading_day_of_month(df_daily: pd.DataFrame, ref_date: dt.date) -> Optional[dt.date]:
    """
    First trading day of the calendar month.
    """
    idx = pd.to_datetime(df_daily.index).tz_localize(None)
    df = df_daily.copy()
    df.index = idx
    first = dt.date(ref_date.year, ref_date.month, 1)
    for d in range(10):
        cand = first + dt.timedelta(days=d)
        if cand in df.index.date:
            return cand
    return None

def compute_orb_swing(df_daily: pd.DataFrame, mode: str = "weekly_1h", ref_date: Optional[dt.date] = None) -> Optional[Dict[str, float]]:
    """
    Compute swing ORB:
      - weekly_1h: approximate using daily data as proxy (first day of week, use that day's OHLC as 1H proxy).
      - monthly_1d: first trading day of month, use that day's OHLC.
    Returns same dict as intraday ORB.
    """
    if df_daily.empty:
        return None
    df = df_daily.copy()
    df.index = pd.to_datetime(df.index).tz_localize(None)
    if ref_date is None:
        ref_date = ist_now().date()

    if mode == "weekly_1h":
        fday = first_trading_day_of_week(df, ref_date)
    elif mode == "monthly_1d":
        fday = first_trading_day_of_month(df, ref_date)
    else:
        return None
    if fday is None:
        return None
    day_df = df[df.index.date == fday]
    if day_df.empty:
        return None
    orb_high = float(day_df["high"].max())
    orb_low = float(day_df["low"].min())
    orb_mid = (orb_high + orb_low) / 2.0
    orb_width_pct = (orb_high - orb_low) / orb_mid * 100.0 if orb_mid > 0 else 0.0
    return {"orb_high": orb_high, "orb_low": orb_low, "orb_mid": orb_mid, "orb_width_pct": orb_width_pct}

# =============================================================================
# SECTOR TAGGING (simple static mapping; can be extended)
# =============================================================================
SECTOR_MAP = {
    "RELIANCE.NS":"Energy","HDFCBANK.NS":"Banking","INFY.NS":"IT","ICICIBANK.NS":"Banking",
    "TCS.NS":"IT","SBIN.NS":"Banking","BHARTIARTL.NS":"Telecom","ITC.NS":"FMCG",
    "KOTAKBANK.NS":"Banking","AXISBANK.NS":"Banking","LT.NS":"Capital Goods","HCLTECH.NS":"IT",
    "ASIANPAINT.NS":"Consumer Durables","MARUTI.NS":"Auto","TITAN.NS":"Consumer Discretionary",
    "BAJFINANCE.NS":"Financials","ULTRACEMCO.NS":"Cement","WIPRO.NS":"IT","SUNPHARMA.NS":"Pharma",
    "TATASTEEL.NS":"Metals","BAJAJFINSV.NS":"Financials","ADANIENT.NS":"Diversified",
    "POWERGRID.NS":"Power","NTPC.NS":"Power","M&M.NS":"Auto","TATAMOTORS.NS":"Auto",
    "JSWSTEEL.NS":"Metals","TATACONSUM.NS":"FMCG","HINDUNILVR.NS":"FMCG","ONGC.NS":"Energy",
    "ADANIPORTS.NS":"Infra","GRASIM.NS":"Cement","DRREDDY.NS":"Pharma","CIPLA.NS":"Pharma",
    "EICHERMOT.NS":"Auto","COALINDIA.NS":"Energy","BPCL.NS":"Energy","IOC.NS":"Energy",
    "GAIL.NS":"Energy","HEROMOTOCO.NS":"Auto","SHREECEM.NS":"Cement","BRITANNIA.NS":"FMCG",
    "NESTLEIND.NS":"FMCG","PIDILITIND.NS":"Chemicals","DIVISLAB.NS":"Pharma","LTIM.NS":"IT",
    "TECHM.NS":"Telecom","SIEMENS.NS":"Capital Goods","GODREJCP.NS":"FMCG","DABUR.NS":"FMCG",
    "HINDCOPPER.NS":"Metals","BHEL.NS":"Capital Goods","INDUSINDBK.NS":"Banking",
    "PNB.NS":"Banking","BANKBARODA.NS":"Banking","CANBK.NS":"Banking","FEDERALBNK.NS":"Banking",
    "IDFCFIRSTB.NS":"Banking","RBLBANK.NS":"Banking","BANDHANBNK.NS":"Banking","AUBANK.NS":"Banking",
    "CHOLAFIN.NS":"Financials","SHRIRAMFIN.NS":"Financials","BAJAJ-AUTO.NS":"Auto",
    "TATAPOWER.NS":"Power","ADANIGREEN.NS":"Power","ADANITRANS.NS":"Infra",
    "TORNTPHARM.NS":"Pharma","LUPIN.NS":"Pharma","BIOCON.NS":"Pharma","APOLLOHOSP.NS":"Healthcare",
    "MAXHEALTH.NS":"Healthcare","FORTIS.NS":"Healthcare","NAUKRI.NS":"Services",
    "MUTHOOTFIN.NS":"Financials","LICHSGFIN.NS":"Financials","HDFCLIFE.NS":"Financials",
    "SBILIFE.NS":"Financials","ICICIGI.NS":"Financials","M&MFIN.NS":"Financials","SRF.NS":"Chemicals",
    "PETRONET.NS":"Energy","OFSS.NS":"IT","PERSISTENT.NS":"IT","COFORGE.NS":"IT",
    "POLICYBZR.NS":"Financials","NYKAA.NS":"Consumer Discretionary","PAYTM.NS":"Financials",
    "VOLTAS.NS":"Consumer Durables","CROMPTON.NS":"Consumer Durables","WHIRLPOOL.NS":"Consumer Durables",
    "HAVELLS.NS":"Consumer Durables","CUMMINSIND.NS":"Capital Goods","THERMAX.NS":"Capital Goods",
    "KEI.NS":"Capital Goods","POLYCAB.NS":"Capital Goods","FINCABLES.NS":"Capital Goods",
    "NAVINFLUOR.NS":"Chemicals","SOLARINDS.NS":"Capital Goods","SUPREMEIND.NS":"Plastics",
    "PIIND.NS":"Auto","GARFIBRES.NS":"Textiles","BASF.NS":"Chemicals","UPL.NS":"Agri",
    "NUVOCO.NS":"Cement","MPHASIS.NS":"IT","RAMCOCEM.NS":"Cement","GRANULES.NS":"Pharma",
    "AARTIIND.NS":"Chemicals","ALKYLAMINE.NS":"Chemicals","DEEPAKNTR.NS":"Chemicals",
    "TATACHEM.NS":"Chemicals","GSPL.NS":"Energy","IGL.NS":"Energy","MGL.NS":"Energy",
    "ASTRAL.NS":"Capital Goods","KFINTECH.NS":"Financials","CAMS.NS":"Financials","BSE.NS":"Financials",
    "MCX.NS":"Financials","CDSL.NS":"Financials","CRAINFRA.NS":"Infra","IRB.NS":"Infra",
    "NHPC.NS":"Power","SJVN.NS":"Power","RVNL.NS":"Infra","IRFC.NS":"Financials",
    "IREDA.NS":"Financials","PFC.NS":"Financials","RECLTD.NS":"Financials",
    "HAL.NS":"Defence","BEL.NS":"Defence","BDL.NS":"Defence",
}

def get_sector(symbol: str) -> str:
    return SECTOR_MAP.get(symbol, "Diversified")




# =============================================================================
# SCREENER LOGIC (Intraday & Swing)
# =============================================================================
def screen_universe(
    symbols: List[str],
    mode: str,  # "intraday" or "swing"
    cfg: dict,
    orb_mode: str,
) -> List[Dict[str, Any]]:
    """
    Screen entire F&O universe for given mode.
    Returns list of dicts with:
      - symbol, sector
      - primary_tf, secondary_tf
      - hm_primary, hm_secondary
      - vpa_primary, vpa_secondary
      - confluence_direction, confluence_strength, signal_quality
      - orb: {"orb_high","orb_low","orb_width_pct"}
      - last_price, orb_entry, orb_sl, orb_target
      - why_selected: list of bullets
    """
    results = []
    if mode == "intraday":
        pri_tf = cfg["intraday_tf_primary"]   # "1h"
        sec_tf = cfg["intraday_tf_secondary"] # "1d"
        look_days_pri = 20
        look_days_sec = 200
    else:
        pri_tf = cfg["swing_tf_primary"]      # "1d"
        sec_tf = cfg["swing_tf_secondary"]    # "1w"
        look_days_pri = 200
        look_days_sec = 800

    for sym in symbols:
        pri_df = fetch_ohlcv(sym, pri_tf, days=look_days_pri)
        sec_df = fetch_ohlcv(sym, sec_tf, days=look_days_sec)
        if pri_df is None or sec_df is None or len(pri_df) < 30 or len(sec_df) < 30:
            continue

        # MTF confluence
        mtf = mtf_confluence(pri_df, sec_df, cfg)
        if mtf["confluence_direction"] == "neutral":
            continue

        # ORB
        if mode == "intraday":
            # For ORB, we need intraday bars (1m/5m). Try 1m first, else 5m.
            intraday_bars = fetch_ohlcv(sym, "1m", days=2, source_pref="fyers")
            if intraday_bars is None or len(intraday_bars) < 10:
                intraday_bars = fetch_ohlcv(sym, "5m", days=5, source_pref="fyers")
            if intraday_bars is None or len(intraday_bars) < 5:
                continue
            orb = compute_orb_intraday(intraday_bars, mode=orb_mode)
        else:
            orb = compute_orb_swing(sec_df, mode=orb_mode, ref_date=ist_now().date())
        if orb is None:
            continue

        last_price = float(pri_df["close"].iloc[-1])
        # Entry/SL/Target based on ORB break
        if mtf["confluence_direction"] == "bullish":
            entry = orb["orb_high"]
            sl = orb["orb_low"]
        else:
            entry = orb["orb_low"]
            sl = orb["orb_high"]
        risk = abs(entry - sl)
        target = entry + (1.5 * risk) if mtf["confluence_direction"] == "bullish" else entry - (1.5 * risk)

        # Why selected
        why = []
        why.append(f"MTF: {pri_tf.upper()} & {sec_tf.upper()} {mtf['confluence_direction']}")
        if mtf["primary_hm"]["cross_type"]:
            why.append(f"HM: {mtf['primary_hm']['cross_type']} on {pri_tf.upper()}")
        if mtf["primary_vpa"]["pattern"]:
            why.append(f"VPA: {mtf['primary_vpa']['pattern']} ({mtf['primary_vpa']['bias']})")
        if mtf["confluence_strength"] >= 2:
            why.append(f"Strength: {mtf['confluence_strength']}/3")

        results.append({
            "symbol": sym,
            "sector": get_sector(sym),
            "primary_tf": pri_tf,
            "secondary_tf": sec_tf,
            "hm_primary": mtf["primary_hm"],
            "hm_secondary": mtf["secondary_hm"],
            "vpa_primary": mtf["primary_vpa"],
            "vpa_secondary": mtf["secondary_vpa"],
            "confluence_direction": mtf["confluence_direction"],
            "confluence_strength": mtf["confluence_strength"],
            "signal_quality": mtf["signal_quality"],
            "orb": orb,
            "last_price": last_price,
            "orb_entry": entry,
            "orb_sl": sl,
            "orb_target": target,
            "why_selected": why,
        })

    # Sort by strength then signal_quality
    quality_rank = {"strong": 0, "early": 1, "neutral": 2}
    results.sort(key=lambda x: (-x["confluence_strength"], quality_rank.get(x["signal_quality"], 2)))
    return results

# =============================================================================
# PRE-MARKET SCAN (Gap, Volume, Value)
# =============================================================================
def pre_market_scan(symbols: List[str], cfg: dict) -> List[Dict[str, Any]]:
    """
    Approximate pre-market scan using previous day's close vs today's open (or auction if available).
    Returns list of dicts with:
      - symbol, sector
      - gap_pct, pre_vol_ratio, pre_value_cr (approx)
      - bias: "gap_up_bullish","gap_down_bearish","neutral"
      - notes
    """
    results = []
    for sym in symbols:
        df = fetch_ohlcv(sym, "1d", days=10)
        if df is None or len(df) < 3:
            continue
        prev_close = float(df["close"].iloc[-2])
        # Use today's open as proxy for pre-market level
        today_open = float(df["open"].iloc[-1])
        gap_pct = (today_open / prev_close - 1.0) * 100.0

        # Volume: compare today's volume so far vs 20-day avg (approx)
        vol_look = min(20, len(df)-1)
        avg_vol = float(df["volume"].iloc[-vol_look:-1].mean())
        today_vol = float(df["volume"].iloc[-1])
        vol_ratio = today_vol / avg_vol if avg_vol > 0 else 1.0

        # Value (₹ Cr) approx: (today_open + prev_close)/2 * today_vol / 1e7
        avg_price = (today_open + prev_close) / 2.0
        value_cr = (avg_price * today_vol) / 1e7

        if gap_pct > 1.0 and vol_ratio > 1.2:
            bias = "gap_up_bullish"
        elif gap_pct < -1.0 and vol_ratio > 1.2:
            bias = "gap_down_bearish"
        else:
            bias = "neutral"

        results.append({
            "symbol": sym,
            "sector": get_sector(sym),
            "gap_pct": gap_pct,
            "pre_vol_ratio": vol_ratio,
            "pre_value_cr": value_cr,
            "bias": bias,
            "notes": f"Gap {ns(gap_pct)}%, Vol {ns(vol_ratio)}x, Value ₹{ns(value_cr)} Cr",
        })

    results.sort(key=lambda x: (-abs(x["gap_pct"]), -x["pre_value_cr"]))
    return results




# =============================================================================
# BACKTEST ENGINE (Skeleton with survivorship-aware design)
# =============================================================================
def load_historical_fno_series() -> Dict[dt.date, List[str]]:
    """
    Load historical F&O membership by date (simplified).
    In production, parse NSE's F&O introduction/exclusion tracker Excel and build this.
    Here we return a static mapping: all dates -> current FNO_STATIC.
    """
    # Placeholder: you will replace this with real historical membership logic.
    today = ist_now().date()
    start = dt.date(2020, 1, 1)
    mapping = {}
    d = start
    while d <= today:
        mapping[d] = list(FNO_STATIC)
        d += dt.timedelta(days=1)
    return mapping

def run_backtest(
    start_date: dt.date,
    end_date: dt.date,
    mode: str,  # "intraday" or "swing"
    cfg: dict,
    orb_mode: str,
) -> Dict[str, Any]:
    """
    Run backtest from start_date to end_date for given mode.
    Returns performance metrics and trade list.
    This is a simplified skeleton; you will enhance with:
      - true historical F&O membership
      - realistic intraday bars & ORB reconstruction
      - costs/slippage
      - walk-forward learning
    """
    fno_hist = load_historical_fno_series()
    trades = []
    # Placeholder loop: for each date, run screen, simulate ORB break next bar, record outcome.
    # For brevity, we return dummy metrics.
    metrics = {
        "cagr": 0.0,
        "sharpe": 0.0,
        "max_dd": 0.0,
        "total_trades": 0,
        "win_rate": 0.0,
        "avg_R": 0.0,
        "hit_1pct": 0.0,
        "hit_2pct": 0.0,
        "hit_4pct": 0.0,
    }
    return {"metrics": metrics, "trades": trades}

# =============================================================================
# SELF-LEARNING ENGINE (Skeleton)
# =============================================================================
def load_outcome_db() -> pd.DataFrame:
    """
    Load trade outcomes from DB_PATH (Parquet/SQLite).
    Columns: date, symbol, mode, features..., outcome_1pct, outcome_2pct, outcome_4pct, R_actual, etc.
    """
    if not LOG_PATH.exists():
        return pd.DataFrame()
    try:
        df = pd.read_parquet(LOG_PATH)
        return df
    except Exception:
        return pd.DataFrame()

def save_outcome_record(rec: Dict[str, Any]):
    """Append one outcome record to LOG_PATH."""
    df_old = load_outcome_db()
    df_new = pd.DataFrame([rec])
    df_all = pd.concat([df_old, df_new], ignore_index=True)
    df_all.to_parquet(LOG_PATH, index=False)

def train_candidate_model(cfg: dict) -> Optional[Dict[str, Any]]:
    """
    Train a candidate model on historical outcomes.
    Returns model dict (coefficients, features, thresholds) or None if insufficient data.
    Simplified placeholder: returns None until enough data accumulated.
    """
    df = load_outcome_db()
    if len(df) < cfg["learn_min_samples"]:
        return None
    # Placeholder: in production, fit logistic/GBT on features vs outcome (e.g., hit_4pct).
    # Then validate on holdout; if better than current, return model dict.
    return None

def maybe_deploy_new_model(cfg: dict):
    """
    Check if today is deploy day (Monday pre-market).
    If yes and a validated candidate exists, replace production model.
    """
    today = ist_now()
    if today.strftime("%A") != "Monday":
        return
    candidate = train_candidate_model(cfg)
    if candidate is None:
        return
    # Load current model
    current = load_json(MODEL_PATH, default=None)
    # Compare performance (placeholder: always deploy if candidate exists)
    save_json(MODEL_PATH, candidate)

def get_active_model() -> Optional[Dict[str, Any]]:
    return load_json(MODEL_PATH, default=None)




# =============================================================================
# STREAMLIT UI — DASHBOARD & SCREENERS
# =============================================================================
def render_sidebar():
    st.sidebar.title("⚙️ Configuration")
    cfg["volume_lookback"] = st.sidebar.slider("Volume Lookback (bars)", 10, 50, cfg["volume_lookback"])
    cfg["volume_tiers"]["high_min"] = st.sidebar.slider("High Vol Threshold (×½avg)", 1.0, 3.0, cfg["volume_tiers"]["high_min"], 0.1)
    cfg["volume_tiers"]["very_high_min"] = st.sidebar.slider("Very High Vol (×½avg)", 1.5, 4.0, cfg["volume_tiers"]["very_high_min"], 0.1)
    cfg["volume_tiers"]["extreme_min"] = st.sidebar.slider("Extreme Vol (×½avg)", 2.0, 5.0, cfg["volume_tiers"]["extreme_min"], 0.1)

    cfg["orb_intraday_mode"] = st.sidebar.selectbox("Intraday ORB", ["5m","15m"], index=0 if cfg["orb_intraday_mode"]=="5m" else 1)
    cfg["orb_swing_mode"] = st.sidebar.selectbox("Swing ORB", ["weekly_1h","monthly_1d"], index=0 if cfg["orb_swing_mode"]=="weekly_1h" else 1)

    st.sidebar.markdown("---")
    st.sidebar.subheader("Data")
    data_source = st.sidebar.radio("Data Source", ["fyers","yfinance"], index=0)
    st.session_state["data_source"] = data_source

    st.sidebar.markdown("---")
    st.sidebar.subheader("Auto-refresh")
    auto_refresh = st.sidebar.checkbox("Enable auto-refresh (live)", value=False)
    if auto_refresh and HAS_AUTOREFRESH:
        st_autorefresh(interval=60000, key="live_refresh")

def render_dashboard():
    st.title("📊 Dashboard")
    fno_syms, src = get_fno_universe_cached()
    st.metric("F&O Universe", f"{len(fno_syms)} symbols ({src})")
    st.markdown("Use the tabs below for Intraday/Swing screeners, Pre‑Market scan, Backtest, and Learning.")

def render_intraday_screener():
    st.title("📈 Intraday Screener (1H + 1D)")
    st.markdown("Filters: HM bullish/bearish cross + VPA confirmation + ORB (5m/15m). Top 4 BUY / Top 4 SELL.")
    with st.spinner("Screening F&O universe…"):
        fno_syms, _ = get_fno_universe_cached()
        results = screen_universe(fno_syms, mode="intraday", cfg=cfg, orb_mode=cfg["orb_intraday_mode"])
    buys = [r for r in results if r["confluence_direction"]=="bullish"][:4]
    sells = [r for r in results if r["confluence_direction"]=="bearish"][:4]

    st.subheader("Top 4 BUY")
    for r in buys:
        with st.container():
            st.markdown(f"**{r['symbol']}** — {r['sector']}")
            st.markdown(f"Last: {ns(r['last_price'])} | Entry: {ns(r['orb_entry'])} | SL: {ns(r['orb_sl'])} | Target: {ns(r['orb_target'])}")
            st.markdown(f"Strength: {r['confluence_strength']}/3 | Quality: {r['signal_quality']}")
            st.markdown(" • " + " | ".join(r["why_selected"]))
            st.markdown("---")

    st.subheader("Top 4 SELL")
    for r in sells:
        with st.container():
            st.markdown(f"**{r['symbol']}** — {r['sector']}")
            st.markdown(f"Last: {ns(r['last_price'])} | Entry: {ns(r['orb_entry'])} | SL: {ns(r['orb_sl'])} | Target: {ns(r['orb_target'])}")
            st.markdown(f"Strength: {r['confluence_strength']}/3 | Quality: {r['signal_quality']}")
            st.markdown(" • " + " | ".join(r["why_selected"]))
            st.markdown("---")

def render_swing_screener():
    st.title("📉 Swing Screener (1D + 1W) — BUY Only")
    with st.spinner("Screening F&O universe…"):
        fno_syms, _ = get_fno_universe_cached()
        results = screen_universe(fno_syms, mode="swing", cfg=cfg, orb_mode=cfg["orb_swing_mode"])
    buys = [r for r in results if r["confluence_direction"]=="bullish"][:4]

    st.subheader("Top 4 BUY")
    for r in buys:
        with st.container():
            st.markdown(f"**{r['symbol']}** — {r['sector']}")
            st.markdown(f"Last: {ns(r['last_price'])} | Entry: {ns(r['orb_entry'])} | SL: {ns(r['orb_sl'])} | Target: {ns(r['orb_target'])}")
            st.markdown(f"Strength: {r['confluence_strength']}/3 | Quality: {r['signal_quality']}")
            st.markdown(" • " + " | ".join(r["why_selected"]))
            st.markdown("---")

def render_premarket_scan():
    st.title("🌅 Pre‑Market Scan")
    with st.spinner("Scanning…"):
        fno_syms, _ = get_fno_universe_cached()
        pm = pre_market_scan(fno_syms, cfg)
    gap_up = [x for x in pm if x["bias"]=="gap_up_bullish"][:4]
    gap_down = [x for x in pm if x["bias"]=="gap_down_bearish"][:4]

    st.subheader("Gap‑Up (Intraday/1H Swing bias)")
    for x in gap_up:
        st.markdown(f"**{x['symbol']}** ({x['sector']}) — {x['notes']}")
    st.subheader("Gap‑Down (Intraday Short bias)")
    for x in gap_down:
        st.markdown(f"**{x['symbol']}** ({x['sector']}) — {x['notes']}")

def render_backtest_tab():
    st.title("🧪 Backtest")
    st.markdown("Historical backtest (2020‑01‑01 to yesterday), survivorship‑aware, with costs/slippage.")
    start = st.date_input("Start", value=dt.date(2020,1,1))
    end = st.date_input("End", value=ist_now().date() - dt.timedelta(days=1))
    mode = st.selectbox("Mode", ["intraday","swing"])
    if st.button("Run Backtest"):
        with st.spinner("Running backtest…"):
            res = run_backtest(start, end, mode, cfg, cfg["orb_intraday_mode"] if mode=="intraday" else cfg["orb_swing_mode"])
        m = res["metrics"]
        st.metric("CAGR", f"{ns(m['cagr'])}%")
        st.metric("Sharpe", ns(m["sharpe"]))
        st.metric("Max DD", f"{ns(m['max_dd'])}%")
        st.metric("Win Rate", f"{ns(m['win_rate'])}%")
        st.metric("Avg R", ns(m["avg_R"]))
        st.metric("Hit 1%", f"{ns(m['hit_1pct'])}%")
        st.metric("Hit 2%", f"{ns(m['hit_2pct'])}%")
        st.metric("Hit 4%", f"{ns(m['hit_4pct'])}%")

def render_learning_tab():
    st.title("🧠 Learning Dashboard")
    st.markdown("Self‑improvement engine: retrains every Friday EOD, deploys Monday pre‑market if validated.")
    df = load_outcome_db()
    if df.empty:
        st.info("No outcome data yet. Trade outcomes will be logged automatically as screeners run.")
        return
    st.dataframe(df.tail(200))
    model = get_active_model()
    if model:
        st.json(model)
    else:
        st.info("No trained model yet; waiting for sufficient samples.")

def main():
    render_sidebar()
    tabs = st.tabs(["Dashboard","Intraday","Swing","Pre‑Market","Backtest","Learning"])
    with tabs[0]:
        render_dashboard()
    with tabs[1]:
        render_intraday_screener()
    with tabs[2]:
        render_swing_screener()
    with tabs[3]:
        render_premarket_scan()
    with tabs[4]:
        render_backtest_tab()
    with tabs[5]:
        render_learning_tab()

    # Daily deploy check
    maybe_deploy_new_model(cfg)

if __name__ == "__main__":
    main()
