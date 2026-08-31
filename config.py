import os

# ================================
#   VERSIONING
# ================================

STRATEGY_VERSION = "1.0.0"
CONFIG_VERSION = "1.0.0"
FEATURE_ENGINE_VERSION = "1.0.0"
BASELINE_STRATEGY_VERSION = "1.0.0"
SETUP_SIGNATURE_VERSION = "1.0.0"


# ================================
#   CORE BEHAVIOR
# ================================

USE_1H_FILTER = True            # Confirmare trend 1h (EMA + MACD + volum)
ALERT_ONLY_NEW = True           # Evită alerte duplicate
ALLOW_EARLY_TREND = True        # Detectează trend timpuriu (~1h înainte de breakout)
USE_4H_BREAKOUT_FILTER = True   # 🔥 Critic pentru detectarea exploziei cu ~1h înainte

# PAPER TRADING ONLY — LIVE strict dezactivat
PAPER_TRADING_ONLY = True
LIVE_TRADING_ENABLED = False


# ================================
#   EXCHANGE SETTINGS
# ================================

EXCHANGE_ID = "binance"                 # Binance global
PRIMARY_EXCHANGE_ID = "binance"
FALLBACK_EXCHANGE_IDS = ("bybit", "kraken", "binanceus")

QUOTE_ASSET = "USDC"                    # Doar perechi USDC
PRIMARY_QUOTE_ASSETS = ("USDC",)
FALLBACK_QUOTE_ASSETS = ("USDC",)

LEVERAGED_TOKENS = ("UP", "DOWN", "BULL", "BEAR")   # Excludere tokeni levered

# Proxy pentru acces Binance din cloud (ex: BINANCE_PROXY=http://user:pass@host:port)
PROXY_URL = os.getenv("BINANCE_PROXY", "")


# ================================
#   CANDLE LIMITS
# ================================

DAILY_LIMIT = 260
H4_LIMIT = 120
H1_LIMIT = 120
H15_LIMIT = 120
M5_LIMIT = 200


# ================================
#   MARKET DATA — STALE / RETRY
# ================================

MAX_RETRIES = 4
INITIAL_RETRY_DELAY = 1.5

# Staleness thresholds (seconds). If a candle's timestamp is older than this
# relative to now, the data is considered stale and NO TRADE should occur.
STALE_DATA_MAX_AGE_SECONDS = {
    "1d": 2 * 24 * 3600,     # 2 days
    "4h": 6 * 3600,          # 6 hours
    "1h": 2 * 3600,          # 2 hours
    "15m": 45 * 60,          # 45 minutes
    "5m": 15 * 60,           # 15 minutes
}

# Data integrity: when historical order book / trades are unavailable
MARKET_DATA_UNAVAILABLE_MARK = "unavailable"


# ================================
#   INDICATOR PARAMETERS
# ================================

# EMA Trend
EMA_FAST = 10
EMA_MID = 50
EMA_SLOW = 200
EMA_SLOPE_LOOKBACK = 10
EMA_MID_SLOPE_LOOKBACK = 5
MIN_EMA10_SLOPE_PCT = 0.05

# MACD Momentum
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
MIN_MACD_SPREAD_RATIO = 0.01

# RSI (pentru impuls sănătos)
RSI_PERIOD = 14
RSI_MIN = 55
RSI_MAX = 80

# StochRSI (14,14,3,3)
STOCH_RSI_RSI_LENGTH = 14
STOCH_RSI_STOCH_LENGTH = 14
STOCH_RSI_K_SMOOTH = 3
STOCH_RSI_D_SMOOTH = 3

# Volum (confirmare presiune)
VOLUME_SMA_PERIOD = 20
VOLUME_RATIO_THRESHOLD = 1.2

# Breakout proximity
BREAKOUT_LOOKBACK_4H = 20
NEAR_BREAKOUT_MAX_DISTANCE_PCT = 3.0

# ADX (trend valid)
ADX_PERIOD = 14
ADX_MIN = 20.0

# Bollinger (pre-breakout / squeeze)
BOLLINGER_LENGTH = 20
BOLLINGER_STD = 2.0
BOLLINGER_SQUEEZE_LOOKBACK = 20
BOLLINGER_SQUEEZE_PERCENTILE = 0.2

# Linear Regression
LRS_LOOKBACK = 20
LRC_LOOKBACK = 150

# Acceleration
VOLUME_ACC_SHORT_PERIOD = 5
VOLUME_ACC_LONG_PERIOD = 20
PRICE_ACC_LOOKBACK = 5


# ================================
#   EARLY ENTRY SCORE (5M)
# ================================

EARLY_ENTRY_BUCKETS = {
    "very_good_min": 0.3,
    "very_good_max": 1.5,
    "good_max": 3.0,
    "penalty_max": 5.0,
    "reject_min": 8.0,
}


# ================================
#   OVEREXTENSION FILTER
# ================================

OVEREXTENSION_LOOKBACK = 20
OVEREXTENSION_MAX_PCT = 3.0    # > 3% above recent high → overextended


# ================================
#   REMAINING POTENTIAL
# ================================

REMAINING_POTENTIAL_ATR_PERIOD = 14
REMAINING_POTENTIAL_ATR_MULT_LOW = 1.5
REMAINING_POTENTIAL_ATR_MULT_HIGH = 3.0


# ================================
#   TRADING PARAMETERS
# ================================

MAX_POSITIONS = 2              # Maximum 2 poziții simultane
POSITION_SIZE_USDC = 50.0       # 50 USDC per poziție
TOTAL_CAPITAL_USDC = 100.0      # 2 × 50 USDC

# Exit strategy (SIMULATED — paper trading)
TAKE_PROFIT_PCT = 0.15          # TP: +15% față de prețul de cumpărare
STOP_LOSS_PCT = 0.08            # SL: -8% față de prețul de cumpărare
TRAILING_ENABLED = True         # Trailing stop activ
TRAILING_ARM_PCT = 0.05         # se armează după ce profitul atinge +5%
TRAILING_STEP_PCT = 0.03        # urmărește cu un pas de 3% sub maximul atins

# Ieșire forțată la închiderea zilei (UTC)
EOD_FORCE_CLOSE_AT = "23:59"    # toate pozițiile rămase se vând la piață
ENTRY_CUTOFF_AT = "22:00"       # nu mai deschidem poziții noi după această oră UTC

# Comisioane (Binance spot standard: 0.1% per tranzacție — buy AND sell)
FEE_RATE = 0.001

# Cooldown
COOLDOWN_HOURS = 48             # Cooldown per simbol după SELL
MIN_TIME_BETWEEN_ROTATIONS_HOURS = 12   # Rotație

# Max drawdown protection
MAX_DRAWDOWN_PCT = 0.20         # 80% din capital → pauză 24h
DRAWDOWN_PAUSE_HOURS = 24


# ================================
#   TRAILING STOP (HIGH-WATER MARK)
# ================================

TRAILING_STOP_THRESHOLDS = [
    # (profit_pct, trailing_distance)
    (0.02, 0.012),   # +2% → 1.2%
    (0.02, 0.015),   # +2% → 1.5% (bandă)
    (0.04, 0.018),   # +4% → 1.8%
    (0.04, 0.020),   # +4% → 2.0%
    (0.08, 0.025),   # +8% → 2.5%
    (0.08, 0.030),   # +8% → 3.0%
    (0.15, 0.030),   # +15% → 3%
    (0.15, 0.040),   # +15% → 4%
]


# ================================
#   DAILY BACKTEST / RECALIBRATION ("SMART TRADER")
# ================================

BACKTEST_ENABLED = True                 # recalibrare zilnică automată
BACKTEST_SYMBOLS_LIMIT = 25             # simboluri folosite la backtest (limita de timp GH Actions)
BACKTEST_1H_LIMIT = 400                 # lumânări 1h descărcate pentru simulare + semnal

# Grid de căutare (variații față de valorile curente din config)
SEARCH_RANGES = {
    "NEAR_BREAKOUT_MAX_DISTANCE_PCT": [1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0],
    "VOLUME_RATIO_THRESHOLD": [1.0, 1.2, 1.4, 1.6, 1.8, 2.0],
    "MIN_EMA10_SLOPE_PCT": [0.0, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30],
    "RSI_COMBOS": [(50, 70), (55, 75), (55, 80), (60, 85)],
    "ADX_MIN": [15.0, 20.0, 25.0, 30.0],
}

# Optimizer
OPTIMIZER_SEARCH_COMBOS = 60            # combinații testate zilnic (sampling seeded)
OPTIMIZER_RANDOM_COMBOS = 15            # combinații random în zilele de explorare
OPTIMIZER_HISTORY_DAYS = 7              # cât istoric păstrăm
OPTIMIZER_BLEND_DAYS = 5                # medie ponderată pe ultimele 3-5 zile
OPTIMIZER_RECENT_WEIGHT = 0.6           # pondere pentru backtest-ul zilei precedente
OPTIMIZER_EXPLORE_WEEKLY = True         # explorare aleatorie o dată pe săptămână


# ================================
#   OPPORTUNITY MANAGER / ROTATION
# ================================

MIN_ROTATION_ADVANTAGE = 15.0        # diferență de score minim pentru ROATE
ROTATION_FEE_ESTIMATE_PCT = 0.001    # fees estimate (0.1%)
ROTATION_SLIPPAGE_ESTIMATE_PCT = 0.001
ROTATION_SAFETY_MARGIN_PCT = 0.005


# ================================
#   SIGNAL QUALITY
# ================================

SIGNAL_QUALITY_MIN_SAMPLE = 30       # sub acest prag → N/A
SIGNAL_QUALITY_TIME_WINDOWS = [60, 120, 240]   # minute


# ================================
#   SCORING / RANKING (exercise weights)
# ================================

RANKING_WEIGHTS = {
    "pre_explosion": 0.40,
    "macd_1h_slope": 0.20,
    "volume_acceleration": 0.15,
    "orderbook": 0.10,
    "early_entry": 0.15,
}

# Pre-explosion scoring sub-weights (sum = 100)
PRE_EXPLOSION_WEIGHTS = {
    "4h_context": 15,
    "1h_accumulation": 20,
    "15m_pre_breakout": 20,
    "5m_momentum": 25,
    "orderbook": 10,
    "large_trade": 10,
}


# ================================
#   MARKET REGIME
# ================================

MARKET_REGIME_BTC_TIMEFRAME = "1d"
MARKET_REGIME_BREADTH_TIMEFRAME = "1d"


# ================================
#   BACKTEST (CONSERVATIVE — LAPTOP)
# ================================

BACKTEST_MODE = "FAST_RESEARCH"      # FAST_RESEARCH | FULL_VALIDATION
BACKTEST_MAX_MEMORY_PERCENT = 40
BACKTEST_BATCH_SIZE = 5
BACKTEST_MAX_WORKERS = 1

# Dataset — BACKTEST_SYMBOLS_LIMIT este definit în secțiunea "DAILY BACKTEST / RECALIBRATION".
BACKTEST_START_DATE = "2024-01-01"


# ================================
#   MEMORY / RESOURCE MANAGEMENT
# ================================

MEMORY_MONITOR_ENABLED = True
MEMORY_CLEANUP_ENABLED = True
MEMORY_CLEANUP_INTERVAL_SECONDS = 900    # 15 minute
MEMORY_WARNING_PERCENT = 70
MEMORY_CRITICAL_PERCENT = 85
MEMORY_CACHE_MAX_MB = 250

# Bounded runtime history
MAX_RUNTIME_HISTORY = 100

# Log rotation
LOG_MAX_MB = 25
LOG_BACKUP_COUNT = 5

# Data retention
RUNTIME_CACHE_TTL_MINUTES = 30
TEMP_DATA_RETENTION_DAYS = 7
RESEARCH_CACHE_RETENTION_DAYS = 90


# ================================
#   5M TRIGGER — CLOSED CANDLE
# ================================

USE_INTRABAR_5M = False          # Baseline: LAST CLOSED CANDLE
INTRABAR_5M_ENABLED = False


# ================================
#   CONSOLE
# ================================

CONSOLE_TOP_N = 10


# ================================
#   TELEGRAM
# ================================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")


# ================================
#   STATE FILES
# ================================

LAST_ALERTS_PATH = "last_alerts.json"
CONFIG_STATE_PATH = "config_state.json"
PAPER_STATE_PATH = "paper_state.json"
TRADING_STATE_PATH = "trading_state.json"
OPTIMIZED_PARAMS_PATH = "optimized_params.json"
STRATEGY_HISTORY_PATH = "strategy_history.json"
REPORTS_DIR = "reports"
TRADING_LOG_PATH = "trading.log"