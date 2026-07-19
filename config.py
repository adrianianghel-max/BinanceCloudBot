import os


# Core behavior
USE_1H_FILTER = True
ALERT_ONLY_NEW = True

# Exchange settings
EXCHANGE_ID = "binance"
QUOTE_ASSET = "USDC"
LEVERAGED_TOKENS = ("UP", "DOWN", "BULL", "BEAR")

# Candle limits
DAILY_LIMIT = 260
H4_LIMIT = 120
H1_LIMIT = 120

# Indicator params
EMA_FAST = 10
EMA_MID = 50
EMA_SLOW = 200
EMA_SLOPE_LOOKBACK = 10

MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9

RSI_PERIOD = 14
VOLUME_SMA_PERIOD = 20
VOLUME_RATIO_THRESHOLD = 1.5

# Retry / rate-limit handling
MAX_RETRIES = 4
INITIAL_RETRY_DELAY = 1.5

# Telegram env variables
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "6101964896:AAH8IYil0VDYS3mu-XX4xpbfGPAlni3OGCk")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "1522064262")

# State files
LAST_ALERTS_PATH = "last_alerts.json"
CONFIG_STATE_PATH = "config_state.json"
