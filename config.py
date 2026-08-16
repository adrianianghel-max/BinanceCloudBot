import os

# ================================
#   VERSION
# ================================

BOT_VERSION = "2.0"

# ================================
#   CORE BEHAVIOR
# ================================

ALERT_ONLY_NEW = True           # Evita alerte duplicate (re-send doar la schimbare de stare)
ALLOW_EARLY_TREND = True        # Detecteaza trend timpuriu EARLY_BULLISH

# ================================
#   EXCHANGE SETTINGS
# ================================

EXCHANGE_ID = "binance"
PRIMARY_EXCHANGE_ID = "binance"
FALLBACK_EXCHANGE_IDS = ("bybit", "kraken", "binanceus")

QUOTE_ASSET = "USDC"
PRIMARY_QUOTE_ASSETS = ("USDC",)
FALLBACK_QUOTE_ASSETS = ("USDC",)

LEVERAGED_TOKENS = ("UP", "DOWN", "BULL", "BEAR")

PROXY_URL = os.getenv("BINANCE_PROXY", "")

# ================================
#   TIMEFRAMES
# ================================

TF_DAILY = "1d"
TF_SETUP = "4h"
TF_ENTRY = "1h"

DAILY_LIMIT = 260
H4_LIMIT = 150
H1_LIMIT = 120

# ================================
#   DAILY TREND / MARKET REGIME
# ================================

MIN_DAILY_EMA10_SLOPE_PCT = 0.05
EMA_SLOPE_LOOKBACK = 10

# ================================
#   VOLUME
# ================================

VOLUME_SMA_PERIOD = 20
MIN_VOLUME_RATIO = 1.20
BREAKOUT_VOLUME_RATIO = 1.50

# ================================
#   RSI
# ================================

RSI_PERIOD = 14
RSI_1H_MIN = 55
RSI_1H_MAX = 80
RSI_4H_MIN = 50
RSI_4H_MAX = 75

# ================================
#   MACD
# ================================

MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
MIN_MACD_SPREAD_RATIO = 0.01

# ================================
#   ADX + DI
# ================================

ADX_PERIOD = 14
MIN_ADX = 20.0

# ================================
#   EMA (4H)
# ================================

EMA9_PERIOD = 9
EMA21_PERIOD = 21
EMA50_PERIOD = 50
GOLDEN_CROSS_CONFIRM_CANDLES = 1

# ================================
#   BOLLINGER BANDS
# ================================

BB_PERIOD = 20
BB_STD = 2.0

# ================================
#   ATR
# ================================

ATR_PERIOD = 14

# ================================
#   BREAKOUT / DISTANCE
# ================================

BREAKOUT_LOOKBACK = 20
PRE_ENTRY_MAX_DISTANCE_PCT = 5.0

# ================================
#   RETEST DETECTION
# ================================

RETEST_WINDOW_CANDLES = 20
RETEST_TOLERANCE_PCT = 2.0

# ================================
#   RELATIVE STRENGTH
# ================================

RS_SYMBOL = "BTC/USDC"
RS_LOOKBACK_4H = 6
RS_LOOKBACK_1D = 3

# ================================
#   MARKET REGIME (BTC global)
# ================================

MARKET_REGIME_SYMBOL = "BTC/USDC"

# ================================
#   ML
# ================================

USE_ML_GATE = True
ML_MIN_WIN_PROBABILITY = 0.60
ML_STRONG_ENTRY_PROBABILITY = 0.70
ML_TARGET_GAIN_PCT = 8.0
ML_HORIZON_4H_CANDLES = 12
ML_STOP_LOSS_PCT = 3.0
MODEL_PATH = "model.pkl"

# ================================
#   SCORING WEIGHTS (suma = 100)
# ================================

SCORE_DAILY_TREND = 15
SCORE_4H_STRUCTURE = 15
SCORE_COMPRESSION = 10
SCORE_VOLUME = 10
SCORE_MACD = 10
SCORE_ADX_DI = 10
SCORE_OBV = 10
SCORE_BREAKOUT_PROXIMITY = 10
SCORE_RELATIVE_STRENGTH = 5
SCORE_MARKET_REGIME = 5

TECHNICAL_WEIGHT = 0.50
ML_WEIGHT = 0.50

# ================================
#   SIGNAL CLASSIFICATION
# ================================

NO_SETUP_MAX_SCORE = 50
WATCH_MIN_SCORE = 50
PRE_ENTRY_MIN_SCORE = 65
NEW_ENTRY_MIN_SCORE = 80
STRONG_ENTRY_MIN_SCORE = 90

# ================================
#   STOP-LOSS / RISK
# ================================

ATR_STOP_MULTIPLIER = 1.5
TARGET_GAIN_PCT = 8.0

# ================================
#   PAPER TRADING
# ================================

PAPER_TRADING_ENABLED = True
PAPER_TRADING_PATH = "paper_trades.json"

# ================================
#   ALERT RANKING
# ================================

TOP_N_PER_SIGNAL_TYPE = 3

# ================================
#   CONSOLE
# ================================

CONSOLE_TOP_N = 10

# ================================
#   RETRY / RATE-LIMIT
# ================================

MAX_RETRIES = 4
INITIAL_RETRY_DELAY = 1.5

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
ENTRY_JOURNAL_PATH = "entry_journal.json"

# ================================
#   LEGACY COMPAT
# ================================

USE_1H_FILTER = True
USE_4H_BREAKOUT_FILTER = True
USE_GOLDEN_CROSS_FILTER = True
NEAR_BREAKOUT_MAX_DISTANCE_PCT = PRE_ENTRY_MAX_DISTANCE_PCT
VOLUME_RATIO_THRESHOLD = MIN_VOLUME_RATIO
RSI_MIN = RSI_1H_MIN
RSI_MAX = RSI_1H_MAX
ADX_MIN = MIN_ADX
EMA_MID_SLOPE_LOOKBACK = 5
MIN_EMA10_SLOPE_PCT = MIN_DAILY_EMA10_SLOPE_PCT
BREAKOUT_LOOKBACK_4H = BREAKOUT_LOOKBACK
