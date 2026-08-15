import os

# ================================
#   CORE BEHAVIOR
# ================================

USE_1H_FILTER = True            # Confirmare trend 1h (EMA + MACD + volum)
ALERT_ONLY_NEW = True           # Evită alerte duplicate
ALLOW_EARLY_TREND = True        # Detectează trend timpuriu (~1h înainte de breakout)
USE_4H_BREAKOUT_FILTER = True   # 🔥 Critic pentru detectarea exploziei cu ~1h înainte


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
STABLECOIN_BASES = (
    "USDC",
    "USDT",
    "BUSD",
    "FDUSD",
    "TUSD",
    "USDP",
    "DAI",
    "EURC",
    "PYUSD",
    "USDD",
)  # Excludere perechi stablecoin/stablecoin

# Proxy pentru acces Binance din cloud (ex: BINANCE_PROXY=http://user:pass@host:port)
PROXY_URL = os.getenv("BINANCE_PROXY", "")


# ================================
#   CANDLE LIMITS
# ================================

DAILY_LIMIT = 260
H4_LIMIT = 120
H1_LIMIT = 120


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

# Volum (confirmare presiune)
VOLUME_SMA_PERIOD = 20
VOLUME_RATIO_THRESHOLD = 1.2

# Breakout proximity
BREAKOUT_LOOKBACK_4H = 20
NEAR_BREAKOUT_MAX_DISTANCE_PCT = 3.0

# ADX (trend valid)
ADX_PERIOD = 14
ADX_MIN = 20.0


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
