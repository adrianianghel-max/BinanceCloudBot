const https = require('https');
const fs = require('fs');
const path = require('path');

// --- CONFIG ---

const TELEGRAM_TOKEN = process.env.TELEGRAM_TOKEN || '6101964896:AAH8IYil0VDYS3mu-XX4xpbfGPAlni3OGCk';
const TELEGRAM_CHAT_ID = process.env.TELEGRAM_CHAT_ID || '1522064262';

const BASE_DIR = __dirname;
const DATA_DIR = path.join(BASE_DIR, 'data');
const LOG_DIR = path.join(BASE_DIR, 'logs');
const WATCHLIST_DIR = path.join(BASE_DIR, 'watchlist');
const WATCH_DIR = WATCHLIST_DIR;

const INTERVALS = ['1m', '5m', '15m', '1h', '4h', '1d'];
const MAX_SYMBOLS = 288;
const TOP_SAVE_COUNT = 20;
const BINANCE_API = 'https://api.binance.com';
const TELEGRAM_API_BASE = 'https://api.telegram.org';

const HISTORY_DAYS = 7;
const MIN_GAIN_PERCENT = 8;

const MIN_QUOTE_VOLUME = 1000;
const TAKER_BUY_RATIO_THRESHOLD = 0.6;
const TRADE_COUNT_SPIKE_PC = 100;
const TOP_USDC_CHECK_COUNT = 50;
const WATCHLIST_SCORE_THRESHOLD = 95;
const BREAKOUT_EMA_PERIOD = 20;
const BREAKOUT_VOLUME_THRESHOLD = 30;

// stablecoin base assets de exclus (falsuri pozitive)
const STABLECOIN_BASES = new Set([
  'USDC', 'FDUSD', 'USD1', 'USDE', 'BFUSD', 'EUR', 'EURI', 'GBP', 'AUD', 'BRL',
  'DAI', 'TUSD', 'USDP', 'GUSD', 'PAXG', 'FDUSD', 'EURT', 'EURCV', 'TRY', 'BIDR',
  'DAI', 'UST', 'USTC', 'LUNC', 'LUNA'
]);

// fișier acuratețe semnale pentru backtest real
const ACCURACY_FILE = path.join(DATA_DIR, 'signal_accuracy.json');

// raport zilnic o singură dată pe zi
let lastDailyReportDate = '';

// BTC de referință (poți schimba în BTCUSDC / BTCFDUSD)
const BTC_REFERENCE_SYMBOL = process.env.BTC_REFERENCE_SYMBOL || 'BTCUSDT';

// limite de lumânări per timeframe
const CANDLE_LIMITS = {
    '1m': 100,
    '5m': 100,
    '15m': 100,
    '1h': 100,
    '4h': 100,
    '1d': 50
};

// praguri confirmare breakout
const BREAKOUT_5M_PRICE_CHANGE_MIN = 1.0;
const BREAKOUT_5M_VOL_CHANGE_MIN = 50;

// ponderi timeframe pentru scor
const TIMEFRAME_WEIGHTS = {
    '15m': 0.35,
    '1h': 0.40,
    '4h': 0.25
};

// ferestre pentru acumulare multi-candle
const ACC_WINDOW_BY_TF = {
    '15m': 12,  // ~3h
    '1h': 8,    // ~8h
    '4h': 6     // ~24h
};

// praguri volum suplimentare
const MIN_QUOTE_VOLUME_15M = 3000;
const MIN_QUOTE_VOLUME_4H = 15000;

// potențial rămas
const MAX_RECENT_GAIN_4H_PC = 12;
const MAX_RECENT_GAIN_1D_PC = 30;

// watchlist
let watchlist = new Map();

// --- UTILS ---

function ensureDir(dir) {
    if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
}

function requestJson(url, options = {}) {
    return new Promise((resolve, reject) => {
        https.get(url, { timeout: 15000, ...options }, (res) => {
            let data = '';
            res.on('data', chunk => data += chunk);
            res.on('end', () => {
                try {
                    resolve(JSON.parse(data));
                } catch (err) {
                    reject(new Error(`Invalid JSON from ${url}: ${err.message}`));
                }
            });
        }).on('error', reject).on('timeout', () => reject(new Error(`Timeout ${url}`)));
    });
}

class TelegramClient {
    constructor(token, chatId) {
        this.token = token;
        this.chatId = chatId;
        this.baseUrl = token ? `${TELEGRAM_API_BASE}/bot${token}/sendMessage` : null;
    }

    async sendMessage(text) {
        if (!this.baseUrl || !this.chatId) {
            console.warn('Telegram not configured');
            return;
        }
        try {
            const payload = new URLSearchParams({
                chat_id: this.chatId,
                text,
                parse_mode: 'Markdown'
            });
            const url = `${this.baseUrl}?${payload.toString()}`;
            const response = await requestJson(url);
            if (!response || response.ok !== true) {
                console.error(`Telegram error: ${response?.description || 'unknown'}`);
            }
        } catch (err) {
            console.error(`Telegram error: ${err.message}`);
        }
    }
}

function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
}

function padRight(value, length) {
    const s = String(value);
    return s + ' '.repeat(Math.max(0, length - s.length));
}

function padLeft(value, length) {
    const s = String(value);
    return ' '.repeat(Math.max(0, length - s.length)) + s;
}

function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

// sparkline ASCII pentru ultimele N prețuri
function sparkline(prices, width = 20) {
    if (!prices || prices.length < 2) return '';
    const slice = prices.slice(-width);
    const min = Math.min(...slice);
    const max = Math.max(...slice);
    const range = max - min || 1;
    const chars = ['▁','▂','▃','▄','▅','▆','▇','█'];
    return slice.map(p => chars[Math.min(7, Math.floor(((p - min) / range) * 7))]).join('');
}

const telegramClient = new TelegramClient(TELEGRAM_TOKEN, TELEGRAM_CHAT_ID);

async function sendTelegram(message) {
    await telegramClient.sendMessage(message);
}

// --- BINANCE FETCHERS ---

async function fetchExchangeInfo() {
    const url = `${BINANCE_API}/api/v3/exchangeInfo`;
    return requestJson(url);
}

async function fetchKlines(symbol, interval, limit = 100) {
    const url = `${BINANCE_API}/api/v3/klines?symbol=${symbol}&interval=${interval}&limit=${limit}`;
    return requestJson(url);
}

async function fetchDepth(symbol, limit = 5) {
    const url = `${BINANCE_API}/api/v3/depth?symbol=${symbol}&limit=${limit}`;
    return requestJson(url);
}

async function fetchBTCPerformance() {
    const result = {};
    try {
        const klines1h = await fetchKlines(BTC_REFERENCE_SYMBOL, '1h', 100);
        if (Array.isArray(klines1h) && klines1h.length > 25) {
            const closePrices = klines1h.map(c => parseFloat(c[4]));
            const latest = closePrices[closePrices.length - 1];
            result['1h'] = ((latest - closePrices[closePrices.length - 2]) / closePrices[closePrices.length - 2]) * 100;
            result['4h'] = ((latest - closePrices[closePrices.length - 5]) / closePrices[closePrices.length - 5]) * 100;
            result['24h'] = ((latest - closePrices[closePrices.length - 25]) / closePrices[closePrices.length - 25]) * 100;
        }

        const klines15m = await fetchKlines(BTC_REFERENCE_SYMBOL, '15m', 50);
        if (Array.isArray(klines15m) && klines15m.length > 2) {
            const closePrices = klines15m.map(c => parseFloat(c[4]));
            const latest = closePrices[closePrices.length - 1];
            result['15m'] = ((latest - closePrices[closePrices.length - 2]) / closePrices[closePrices.length - 2]) * 100;
        }
    } catch (err) {
        console.error('Eroare BTC reference:', err.message);
    }
    return result;
}

// --- ANALIZĂ CANDLE / INDICATORI ---

function analyzeKlineMetrics(klines) {
    const metrics = [];
    for (let i = 1; i < klines.length; i += 1) {
        const open = parseFloat(klines[i][1]);
        const high = parseFloat(klines[i][2]);
        const low = parseFloat(klines[i][3]);
        const close = parseFloat(klines[i][4]);
        const volume = parseFloat(klines[i][5]);
        const quoteVolume = parseFloat(klines[i][7]);
        const numberOfTrades = parseInt(klines[i][8] || 0, 10);
        const takerBuyBase = parseFloat(klines[i][9] || 0);
        const takerBuyQuote = parseFloat(klines[i][10] || 0);
        const body = Math.abs(close - open);
        const range = high - low;
        const volatility = range > 0 ? body / range : 0;
        metrics.push({
            open,
            high,
            low,
            close,
            volume,
            quoteVolume,
            numberOfTrades,
            takerBuyBase,
            takerBuyQuote,
            body,
            range,
            volatility
        });
    }
    return metrics;
}

function computeIndicator(data) {
    if (data.length < 2) return null;

    const last = data[data.length - 1];
    const prev = data[data.length - 2];
    const prev2 = data.length > 2 ? data[data.length - 3] : null;

    const volumeChange = prev && prev.quoteVolume > 0 ? ((last.quoteVolume - prev.quoteVolume) / prev.quoteVolume) * 100 : 0;
    const volumeChange2 = prev2 && prev2.quoteVolume > 0 ? ((last.quoteVolume - prev2.quoteVolume) / prev2.quoteVolume) * 100 : 0;
    const priceChange = prev && prev.close > 0 ? ((last.close - prev.close) / prev.close) * 100 : 0;
    const priceChange2 = prev2 && prev2.close > 0 ? ((last.close - prev2.close) / prev2.close) * 100 : 0;

    const isAccumulation = last.range > 0 &&
        last.body / last.range < 0.3 &&
        last.volatility < 0.15 &&
        Math.abs(priceChange) < 0.5;

    const momentum = prev && prev.close > 0 ? (last.close / prev.close - 1) * 100 : 0;

    return {
        currentClose: last.close,
        previousClose: prev ? prev.close : last.close,
        volumeChange,
        volumeChange2,
        priceChange,
        priceChange2,
        bodyRatio: last.range > 0 ? last.body / last.range : 0,
        range: last.range,
        volatility: last.volatility,
        lastQuoteVolume: last.quoteVolume,
        lastVolume: last.volume,
        lastNumberOfTrades: last.numberOfTrades || 0,
        lastTakerBuyQuote: last.takerBuyQuote || 0,
        prevNumberOfTrades: prev ? (prev.numberOfTrades || 0) : 0,
        prevQuoteVolume: prev ? (prev.quoteVolume || 0) : 0,
        isAccumulation,
        momentum,
        open: last.open,
        high: last.high,
        low: last.low,
        close: last.close
    };
}

function calculateEMAFromSeries(values, period) {
    if (!values || values.length < period) return null;
    let sum = 0;
    const multiplier = 2 / (period + 1);
    for (let i = 0; i < period; i++) sum += values[i];
    let ema = sum / period;
    for (let i = period; i < values.length; i++) {
        ema = (values[i] - ema) * multiplier + ema;
    }
    return ema;
}

function computeAccumulationWindow(metrics, windowSize) {
    if (!metrics || metrics.length < windowSize + 1) {
        return {
            isAccumulationWindow: false,
            accumulationScore: 0,
            avgRangePct: 0,
            avgBodyRatio: 0,
            avgVolatility: 0,
            totalMovePct: 0
        };
    }

    const slice = metrics.slice(-windowSize);
    let sumRangePct = 0;
    let sumBodyRatio = 0;
    let sumVolatility = 0;
    let smallBodyCount = 0;
    let lowVolCount = 0;
    let smallMoveCount = 0;

    const firstClose = slice[0].close;
    const lastClose = slice[slice.length - 1].close;
    const totalMovePct = firstClose > 0 ? ((lastClose - firstClose) / firstClose) * 100 : 0;

    for (const c of slice) {
        const range = c.range || (c.high - c.low);
        const body = c.body || Math.abs(c.close - c.open);
        const bodyRatio = range > 0 ? body / range : 0;
        const volatility = c.volatility || (range > 0 ? body / range : 0);
        const rangePct = c.open > 0 ? (range / c.open) * 100 : 0;

        sumRangePct += rangePct;
        sumBodyRatio += bodyRatio;
        sumVolatility += volatility;

        if (bodyRatio < 0.3) smallBodyCount++;
        if (volatility < 0.15) lowVolCount++;
        if (Math.abs(((c.close - c.open) / (c.open || 1)) * 100) < 0.7) smallMoveCount++;
    }

    const avgRangePct = sumRangePct / windowSize;
    const avgBodyRatio = sumBodyRatio / windowSize;
    const avgVolatility = sumVolatility / windowSize;

    let score = 0;
    if (avgRangePct < 2.5) score += 15;
    if (avgRangePct < 1.5) score += 10;
    if (avgBodyRatio < 0.35) score += 10;
    if (avgBodyRatio < 0.25) score += 10;
    if (avgVolatility < 0.15) score += 10;
    if (avgVolatility < 0.10) score += 5;
    if (smallBodyCount >= windowSize * 0.6) score += 10;
    if (lowVolCount >= windowSize * 0.6) score += 5;
    if (smallMoveCount >= windowSize * 0.7) score += 10;
    if (Math.abs(totalMovePct) < 3) score += 10;

    const isAccumulationWindow = score >= 35;

    return {
        isAccumulationWindow,
        accumulationScore: score,
        avgRangePct,
        avgBodyRatio,
        avgVolatility,
        totalMovePct
    };
}

function calculateAccumulationScore(tf, btcPerf) {
    if (!tf) return 0;

    let score = 0;

    if (tf.isAccumulation) score += 15;
    if (tf.bodyRatio < 0.2) score += 10;
    if (tf.bodyRatio < 0.1) score += 5;
    if (tf.volatility < 0.1) score += 10;
    if (tf.volatility < 0.05) score += 5;
    if (tf.momentum > 0 && tf.momentum < 1) score += 5;
    if (tf.momentum > 0.2 && tf.momentum < 0.8) score += 5;

    if (tf.accumulationScoreWindow) {
        if (tf.accumulationScoreWindow > 35) score += 10;
        if (tf.accumulationScoreWindow > 50) score += 10;
    }

    if (btcPerf && btcPerf['1h']) {
        const relStrength = (tf.priceChange || 0) - btcPerf['1h'];
        if (relStrength > 0.5) score += 10;
        if (relStrength > 1) score += 5;
    }

    return Math.min(score, 70);
}

// --- ZERO-LAG TREND SIGNALS (PINE-STYLE) ---

function computeZeroLagTrendSignals(metrics, length = 70, mult = 1.2) {
    if (!metrics || metrics.length < length * 3 + 2) return null;

    const closes = metrics.map(m => m.close);

    const lag = Math.floor((length - 1) / 2);
    const src = closes;
    const zleSrc = [];

    for (let i = 0; i < closes.length; i++) {
        const srcLag = i - lag >= 0 ? src[i - lag] : src[0];
        const val = src[i] + (src[i] - srcLag);
        zleSrc.push(val);
    }

    const multiplier = 2 / (length + 1);
    let ema = 0;
    for (let i = 0; i < length; i++) ema += zleSrc[i];
    ema /= length;

    for (let i = length; i < zleSrc.length; i++) {
        ema = (zleSrc[i] - ema) * multiplier + ema;
    }
    const zlema = ema;

    const atrSeries = [];
    for (let i = 0; i < metrics.length; i++) {
        if (i === 0) {
            atrSeries.push(metrics[i].high - metrics[i].low);
        } else {
            const curr = metrics[i];
            const prev = metrics[i - 1];
            const tr = Math.max(
                curr.high - curr.low,
                Math.abs(curr.high - prev.close),
                Math.abs(curr.low - prev.close)
            );
            atrSeries.push(tr);
        }
    }

    const atrLen = length;
    if (atrSeries.length < atrLen + 1) return null;

    let atr = 0;
    for (let i = 0; i < atrLen; i++) atr += atrSeries[i];
    atr /= atrLen;

    const atrSmoothed = [];
    atrSmoothed.push(atr);
    const atrMult = 2 / (atrLen + 1);
    for (let i = atrLen; i < atrSeries.length; i++) {
        atr = (atrSeries[i] - atr) * atrMult + atr;
        atrSmoothed.push(atr);
    }

    const volWindow = length * 3;
    if (atrSmoothed.length < volWindow) return null;
    let vol = -Infinity;
    for (let i = atrSmoothed.length - volWindow; i < atrSmoothed.length; i++) {
        if (atrSmoothed[i] > vol) vol = atrSmoothed[i];
    }

    const volatility = vol * mult;

    const lastClose = closes[closes.length - 1];
    const upperBand = zlema + volatility;
    const lowerBand = zlema - volatility;

    const prevClose = closes[closes.length - 2];

    const bullishCross = prevClose <= upperBand && lastClose > upperBand;
    const bearishCross = prevClose >= lowerBand && lastClose < lowerBand;

    let trend = 0;
    if (bullishCross) trend = 1;
    else if (bearishCross) trend = -1;
    else {
        if (lastClose > zlema) trend = 1;
        else if (lastClose < zlema) trend = -1;
        else trend = 0;
    }

    return {
        zlema,
        volatility,
        upperBand,
        lowerBand,
        trend,
        bullishCross,
        bearishCross,
        lastClose
    };
}

// --- ÎNVĂȚARE DIN ISTORIC ---

function toCsvLine(items) {
    return items.map(v => String(v).replace(/\n/g, ' ')).join(',');
}

function getJsonFilesSorted() {
    return fs.readdirSync(DATA_DIR)
        .filter(name => name.startsWith('top_gainers_') && name.endsWith('.json'))
        .sort()
        .reverse();
}

function loadRecentWinners(days = HISTORY_DAYS) {
    ensureDir(DATA_DIR);
    const files = getJsonFilesSorted().slice(0, days);
    const winners = [];
    for (const file of files) {
        try {
            const content = JSON.parse(fs.readFileSync(path.join(DATA_DIR, file), 'utf-8'));
            if (!content.top) continue;
            content.top.forEach(item => {
                const dailyGain = item.intervals['1d']?.priceChange || 0;
                if (dailyGain >= MIN_GAIN_PERCENT) {
                    winners.push({
                        symbol: item.symbol,
                        score: item.score,
                        signals: item.signals,
                        intervals: item.intervals
                    });
                }
            });
        } catch (err) {
            console.warn('Could not load history file', file, err.message);
        }
    }
    return winners;
}

function computeLearningBoost(analysis, winners) {
    if (!winners.length) return 0;

    const signalSuccess = winners.reduce((acc, item) => {
        const sigs = item.signals || [];
        sigs.forEach(sig => acc[sig] = (acc[sig] || 0) + 1);
        return acc;
    }, {});

    const total = winners.length;
    let boost = 0;
    const currentSignals = analysis.signals || [];
    currentSignals.forEach(sig => {
        const freq = (signalSuccess[sig] || 0) / total;
        if (freq > 0.4) boost += 5;
        else if (freq > 0.2) boost += 3;
        else if (freq > 0.1) boost += 1;
    });

    const accCount = currentSignals.filter(s => s.includes('acumulare')).length;
    if (accCount >= 2) boost += 3;
    if (accCount >= 3) boost += 5;
    if (currentSignals.some(s => s.includes('breakout'))) boost += 4;

    const btcCount = currentSignals.filter(s => s.includes('outperform BTC')).length;
    if (btcCount >= 2) boost += 3;

    return Math.min(15, boost);
}

// --- WATCHLIST ---

function loadWatchlist() {
    ensureDir(WATCH_DIR);
    const watchlistFile = path.join(WATCH_DIR, 'watchlist.json');
    if (fs.existsSync(watchlistFile)) {
        try {
            const data = JSON.parse(fs.readFileSync(watchlistFile, 'utf-8'));
            const map = new Map();
            data.forEach(item => map.set(item.symbol, item));
            return map;
        } catch (err) {
            console.warn('Could not load watchlist:', err.message);
        }
    }
    return new Map();
}

function saveWatchlist() {
    ensureDir(WATCH_DIR);
    const watchlistFile = path.join(WATCH_DIR, 'watchlist.json');
    const data = Array.from(watchlist.values());
    fs.writeFileSync(watchlistFile, JSON.stringify(data, null, 2));
}

async function checkBreakout(symbol, watchItem) {
    try {
        const klines1h = await fetchKlines(symbol, '1h', 80);
        if (!Array.isArray(klines1h) || klines1h.length < 30) return null;

        const candles1h = klines1h.map(k => ({
            close: parseFloat(k[4]),
            volume: parseFloat(k[5]),
            quoteVolume: parseFloat(k[7])
        }));

        const closes1h = candles1h.map(c => c.close);
        const ema20_1h = calculateEMAFromSeries(closes1h, BREAKOUT_EMA_PERIOD);
        if (!ema20_1h) return null;

        const latest1h = candles1h[candles1h.length - 1];
        const prev1h = candles1h[candles1h.length - 2];

        const currentPrice = latest1h.close;
        const currentVolume1h = latest1h.quoteVolume;
        const prevVolume1h = prev1h ? prev1h.quoteVolume : 0;
        const volumeChange1h = prevVolume1h > 0 ? ((currentVolume1h - prevVolume1h) / prevVolume1h) * 100 : 0;

        const priceAboveEMA1h = currentPrice > ema20_1h;
        const volumeSpike1h = volumeChange1h > BREAKOUT_VOLUME_THRESHOLD;

        const klines15m = await fetchKlines(symbol, '15m', 60);
        if (!Array.isArray(klines15m) || klines15m.length < 10) return null;

        const metrics15 = analyzeKlineMetrics(klines15m);
        const last15 = computeIndicator(metrics15);
        const accWin15 = computeAccumulationWindow(metrics15, ACC_WINDOW_BY_TF['15m'] || 12);

        const impuls15 = last15.priceChange > 1.5 && last15.volumeChange > 80;
        const wasAccumulating15 = accWin15.isAccumulationWindow;

        // Confirmare breakout pe 5m (reduce false breakouts)
        const klines5m = await fetchKlines(symbol, '5m', 30);
        let confirm5m = true;
        if (Array.isArray(klines5m) && klines5m.length >= 5) {
            const metrics5 = analyzeKlineMetrics(klines5m);
            const last5 = computeIndicator(metrics5);
            if (last5) {
                confirm5m = last5.priceChange > BREAKOUT_5M_PRICE_CHANGE_MIN && last5.volumeChange > BREAKOUT_5M_VOL_CHANGE_MIN;
            }
        }

        const recentWindow = candles1h.slice(-12);
        const lowRecent = Math.min(...recentWindow.map(c => c.close));
        const gainRecent4h = lowRecent > 0 ? ((currentPrice - lowRecent) / lowRecent) * 100 : 0;
        const hasPotential = gainRecent4h < MAX_RECENT_GAIN_4H_PC;

        const isBreakout = priceAboveEMA1h && volumeSpike1h && impuls15 && wasAccumulating15 && hasPotential && confirm5m;

        return {
            symbol,
            currentPrice,
            ema20: ema20_1h,
            priceAboveEMA1h,
            volumeChange: volumeChange1h,
            volumeSpike: volumeSpike1h,
            isBreakout,
            isAccumulation: wasAccumulating15,
            priceDifference: ((currentPrice - ema20_1h) / ema20_1h) * 100,
            impuls15,
            gainRecent4h,
            confirm5m
        };

    } catch (err) {
        console.error(`Eroare verificare breakout ${symbol}:`, err.message);
        return null;
    }
}

async function monitorWatchlist() {
    if (watchlist.size === 0) {
        console.log('📋 Watchlist goală. Așteaptă simboluri cu scor > 95...');
        return;
    }

    console.log(`\n🔍 Monitorizare ${watchlist.size} simboluri din watchlist...`);

    const breakoutSymbols = [];

    for (const [symbol, data] of watchlist) {
        if (data.breakoutDetected) continue;

        const result = await checkBreakout(symbol, data);
        if (!result) continue;

        data.lastCheck = new Date().toISOString();
        data.currentPrice = result.currentPrice;
        data.ema20 = result.ema20;

        if (result.isBreakout && !data.breakoutDetected) {
            data.breakoutDetected = true;
            data.breakoutTime = new Date().toISOString();
            breakoutSymbols.push({
                ...result,
                ...data
            });
        }

        const status = result.isBreakout ? '🚀 BREAKOUT!' :
            result.isAccumulation ? '⏳ Acumulare...' : '📈 Urmarire';
        const emaStatus = result.priceAboveEMA1h ? '✅ Peste EMA' : '⬇️ Sub EMA';

        console.log(`  ${symbol}: ${status} | ${emaStatus} | Vol 1h: ${result.volumeChange.toFixed(1)}% | EMA20 1h: ${result.ema20.toFixed(4)} | Preț: ${result.currentPrice.toFixed(4)}`);
    }

    if (breakoutSymbols.length > 0) {
        for (const breakout of breakoutSymbols) {
            // Construim sparkline din ultimele 20 close-uri 1h
            let spark = '';
            try {
                const klinesSpark = await fetchKlines(breakout.symbol, '1h', 25);
                if (Array.isArray(klinesSpark) && klinesSpark.length >= 5) {
                    const prices = klinesSpark.map(k => parseFloat(k[4]));
                    spark = '\n📉 `' + sparkline(prices, 20) + '`';
                }
            } catch (_) { /* ignore */ }

            const link = `🔗 [Vezi pe Binance](https://www.binance.com/en/trade/${breakout.symbol}?type=spot)`;
            const msg = `🚀 *SEMNAL CUMPARARE - BREAKOUT!* 🚀\n` +
                `*${breakout.symbol}* a ieșit din acumulare!\n` +
                `📈 Preț: ${breakout.currentPrice.toFixed(6)}\n` +
                `📊 EMA 20 1h: ${breakout.ema20.toFixed(6)}\n` +
                `📈 Diferență: +${breakout.priceDifference.toFixed(2)}%\n` +
                `📊 Volum 1h: +${breakout.volumeChange.toFixed(1)}%\n` +
                `⚡ Confirmare 5m: ${breakout.confirm5m ? '✅ DA' : '❌ NU'}\n` +
                `📊 Gain recent ~12h: ${breakout.gainRecent4h.toFixed(2)}%\n` +
                `⭐ Scor inițial: ${breakout.score}\n` +
                `📋 Semnale: ${breakout.signals.join(', ')}\n` +
                `⏰ Intrat în watchlist: ${new Date(breakout.entryDate).toLocaleString()}${spark}\n${link}`;
            await sendTelegram(msg);
            console.log(`📨 Alertă breakout trimisă pentru ${breakout.symbol}`);
        }
        saveWatchlist();
    }

    const total = watchlist.size;
    const breakoutCount = Array.from(watchlist.values()).filter(w => w.breakoutDetected).length;
    console.log(`📊 Watchlist: ${total} simboluri, ${breakoutCount} breakouts detectate`);
}

// --- DETECTARE PATRONE / SCOR ---

function detectPatterns(summary, btcPerf, book) {
    const signals = [];
    let score = 0;

    const acc15 = summary['15m'];
    const acc1h = summary['1h'];
    const acc4h = summary['4h'];
    const acc1d = summary['1d'];

    if (!acc15 || !acc1h) return { score: 0, signals: ['date insuficiente'] };

    if ((acc1h.lastQuoteVolume || 0) < MIN_QUOTE_VOLUME) {
        signals.push('liquiditate scazuta 1h');
        return { score: 0, signals };
    }
    if (acc15 && (acc15.lastQuoteVolume || 0) < MIN_QUOTE_VOLUME_15M) {
        signals.push('liquiditate scazuta 15m');
        return { score: 0, signals };
    }
    if (acc4h && (acc4h.lastQuoteVolume || 0) < MIN_QUOTE_VOLUME_4H) {
        signals.push('liquiditate scazuta 4h');
        return { score: 0, signals };
    }

    if (acc4h && acc4h.ema200 && acc4h.close < acc4h.ema200) {
        signals.push('downtrend 4h sub EMA200');
        return { score: 0, signals };
    }
    if (acc1d && acc1d.ema200 && acc1d.close < acc1d.ema200) {
        signals.push('downtrend 1d sub EMA200');
        return { score: 0, signals };
    }

    const accScore15 = calculateAccumulationScore(acc15, btcPerf);
    const accScore1h = calculateAccumulationScore(acc1h, btcPerf);
    const accScore4h = acc4h ? calculateAccumulationScore(acc4h, btcPerf) : 0;

    if (accScore15 > 30) signals.push('acumulare 15m');
    if (accScore15 > 45) signals.push('acumulare puternica 15m');

    if (accScore1h > 35) signals.push('acumulare 1h');
    if (accScore1h > 50) signals.push('acumulare puternica 1h');

    if (acc4h && accScore4h > 30) signals.push('acumulare 4h');
    if (acc4h && accScore4h > 45) signals.push('acumulare puternica 4h');

    let weightedScore = 0;
    if (acc15) weightedScore += accScore15 * TIMEFRAME_WEIGHTS['15m'];
    if (acc1h) weightedScore += accScore1h * TIMEFRAME_WEIGHTS['1h'];
    if (acc4h) weightedScore += accScore4h * TIMEFRAME_WEIGHTS['4h'];

    const takerRatio1h = acc1h.lastTakerBuyQuote && acc1h.lastQuoteVolume ?
        acc1h.lastTakerBuyQuote / (acc1h.lastQuoteVolume + 1e-9) : 0;
    if (takerRatio1h > TAKER_BUY_RATIO_THRESHOLD) {
        signals.push('taker buy dominance');
        weightedScore += 15;
    }

    const tradeCountChange1h = (acc1h.prevNumberOfTrades > 0) ?
        ((acc1h.lastNumberOfTrades - acc1h.prevNumberOfTrades) / acc1h.prevNumberOfTrades) * 100 : 0;
    if (tradeCountChange1h > TRADE_COUNT_SPIKE_PC) {
        signals.push('trade count spike');
        weightedScore += 10;
    }

    if (acc15.isAccumulationWindow && acc15.priceChange > 1.5 && acc15.volumeChange > 80) {
        signals.push('breakout 15m din acumulare');
        weightedScore += 22;
    }
    if (acc1h.isAccumulationWindow && acc1h.priceChange > 2 && acc1h.volumeChange > 60) {
        signals.push('breakout 1h din acumulare');
        weightedScore += 20;
    }

    if (btcPerf) {
        if (btcPerf['15m'] && acc15) {
            const rel15 = (acc15.priceChange || 0) - btcPerf['15m'];
            if (rel15 > 0.5) {
                signals.push('outperform BTC 15m');
                weightedScore += 10;
            }
        }
        if (btcPerf['1h'] && acc1h) {
            const rel1h = (acc1h.priceChange || 0) - btcPerf['1h'];
            if (rel1h > 1) {
                signals.push('outperform BTC 1h');
                weightedScore += 12;
            }
        }
        if (btcPerf['4h'] && acc4h) {
            const rel4h = (acc4h.priceChange || 0) - btcPerf['4h'];
            if (rel4h > 1.5) {
                signals.push('outperform BTC 4h');
                weightedScore += 14;
            }
        }
    }

    if (book) {
        const bidQty = book.bids.reduce((sum, bid) => sum + parseFloat(bid[1]), 0);
        const askQty = book.asks.reduce((sum, ask) => sum + parseFloat(ask[1]), 0);
        const ratio = bidQty / (askQty + 1e-9);
        if (ratio > 1.5) {
            signals.push('bid dominance');
            weightedScore += 10;
        } else if (ratio > 1.2) {
            signals.push('bid advantage');
            weightedScore += 5;
        }
    }

    if (acc4h && typeof acc4h.totalMovePctWindow === 'number') {
        if (acc4h.totalMovePctWindow > MAX_RECENT_GAIN_4H_PC) {
            signals.push('4h deja extins');
            weightedScore -= 15;
        }
    }
    if (acc1d && typeof acc1d.priceChange === 'number') {
        if (acc1d.priceChange > MAX_RECENT_GAIN_1D_PC) {
            signals.push('1d deja parabolic');
            weightedScore -= 20;
        }
    }

    // ZERO-LAG TREND MULTI-TF
    const z15 = acc15.zlsTrend || 0;
    const z1h = acc1h.zlsTrend || 0;
    const z4h = acc4h ? (acc4h.zlsTrend || 0) : 0;

    const bullCross15 = !!acc15.zlsBullishCross;
    const bullCross1h = !!acc1h.zlsBullishCross;
    const bearCross4h = acc4h ? !!acc4h.zlsBearishCross : false;

    if (z15 === 1 && z1h === 1) {
        signals.push('zero-lag bullish 15m+1h');
        weightedScore += 10;
    }
    if (z15 === 1 && z1h === 1 && z4h === 1) {
        signals.push('zero-lag bullish 15m+1h+4h');
        weightedScore += 8;
    }

    if (z4h === -1) {
        signals.push('zero-lag bearish 4h');
        weightedScore -= 10;
    }

    if (bullCross15) {
        signals.push('zero-lag bullish entry 15m');
        weightedScore += 8;
    }
    if (bullCross1h) {
        signals.push('zero-lag bullish entry 1h');
        weightedScore += 10;
    }

    if (bearCross4h) {
        signals.push('zero-lag bearish entry 4h');
        weightedScore -= 12;
    }

    let normalizedScore = Math.min(weightedScore, 100);
    if (normalizedScore > 0 && normalizedScore < 15) {
        normalizedScore = 15 + (normalizedScore / 2);
    }
    if (signals.length >= 3 && normalizedScore < 30) {
        normalizedScore = Math.min(40, normalizedScore + 10);
    }

    return {
        score: clamp(Math.round(normalizedScore), 0, 100),
        signals: signals.slice(0, 10)
    };
}

// --- ANALIZA SIMBOL / SCAN ---

async function analyzeSymbol(symbol, btcPerf, historyWinners) {
    const klinesByInterval = {};

    for (const interval of INTERVALS) {
        try {
            const limit = CANDLE_LIMITS[interval] || 100;
            const klines = await fetchKlines(symbol, interval, limit);
            if (!Array.isArray(klines) || klines.length < 3) continue;

            const metrics = analyzeKlineMetrics(klines);
            if (metrics.length < 2) continue;

            const base = computeIndicator(metrics);
            if (!base) continue;

            const closes = metrics.map(m => m.close);
            const ema50 = calculateEMAFromSeries(closes, 50);
            const ema200 = calculateEMAFromSeries(closes, 200);

            const windowSize = ACC_WINDOW_BY_TF[interval] || 0;
            const accWin = windowSize ? computeAccumulationWindow(metrics, windowSize) : {
                isAccumulationWindow: false,
                accumulationScore: 0,
                avgRangePct: 0,
                avgBodyRatio: 0,
                avgVolatility: 0,
                totalMovePct: 0
            };

            const zls = computeZeroLagTrendSignals(metrics, 70, 1.2);

            klinesByInterval[interval] = {
                ...base,
                ema50,
                ema200,
                isAccumulationWindow: accWin.isAccumulationWindow,
                accumulationScoreWindow: accWin.accumulationScore,
                avgRangePctWindow: accWin.avgRangePct,
                avgBodyRatioWindow: accWin.avgBodyRatio,
                avgVolatilityWindow: accWin.avgVolatility,
                totalMovePctWindow: accWin.totalMovePct,
                zlsTrend: zls ? zls.trend : 0,
                zlsBullishCross: zls ? zls.bullishCross : false,
                zlsBearishCross: zls ? zls.bearishCross : false,
                zlsZlema: zls ? zls.zlema : null,
                zlsVolatility: zls ? zls.volatility : null,
                zlsUpperBand: zls ? zls.upperBand : null,
                zlsLowerBand: zls ? zls.lowerBand : null
            };
        } catch (err) {
            console.error(`Eroare klines ${symbol} ${interval}:`, err.message);
        }
    }

    const book = await fetchDepth(symbol, 5).catch(() => null);
    const analysis = detectPatterns(klinesByInterval, btcPerf, book);
    const learningBoost = computeLearningBoost({ ...analysis, intervals: klinesByInterval }, historyWinners);

    return {
        symbol,
        intervals: klinesByInterval,
        book: book ? { bids: book.bids, asks: book.asks } : null,
        score: clamp(analysis.score + learningBoost, 0, 100),
        signals: analysis.signals,
        learningBoost
    };
}

// --- SALVARE TOP GAINERS ---

async function saveDailyTopGainers(dateString, topList) {
    ensureDir(DATA_DIR);
    const filePath = path.join(DATA_DIR, `top_gainers_${dateString}.json`);
    fs.writeFileSync(filePath, JSON.stringify({ date: dateString, generatedAt: new Date().toISOString(), top: topList }, null, 2));

    const csvPath = path.join(DATA_DIR, `top_gainers_${dateString}.csv`);
    const header = [
        'rank',
        'symbol',
        'score',
        'signals',
        '1h_vol_change',
        '1h_price_change',
        '4h_price_change',
        '24h_price_change',
        'accumulation_15m',
        'accumulation_1h',
        'accumulation_4h'
    ].join(',');

    const rows = topList.map((item, idx) => {
        const acc15 = item.intervals['15m']?.isAccumulationWindow ? 1 : 0;
        const acc1h = item.intervals['1h']?.isAccumulationWindow ? 1 : 0;
        const acc4h = item.intervals['4h']?.isAccumulationWindow ? 1 : 0;

        return toCsvLine([
            idx + 1,
            item.symbol,
            item.score,
            item.signals.join(';'),
            item.intervals['1h']?.volumeChange?.toFixed(2) || 0,
            item.intervals['1h']?.priceChange?.toFixed(2) || 0,
            item.intervals['4h']?.priceChange?.toFixed(2) || 0,
            item.intervals['1d']?.priceChange?.toFixed(2) || 0,
            acc15,
            acc1h,
            acc4h
        ]);
    });

    fs.writeFileSync(csvPath, [header, ...rows].join('\n'));
    console.log(`📄 Date salvate: ${filePath}`);
    console.log(`📄 CSV: ${csvPath}`);
}

// --- SELECTARE SIMBOLURI (simplu: toate spot USDC/FDUSD) ---

async function getSymbolsToScan() {
    const info = await fetchExchangeInfo();
    const symbols = info.symbols || [];
    const filtered = symbols.filter(s =>
        s.status === 'TRADING' &&
        (s.quoteAsset === 'USDC' || s.quoteAsset === 'FDUSD') &&
        !STABLECOIN_BASES.has(s.baseAsset)
    );
    return filtered.slice(0, MAX_SYMBOLS).map(s => s.symbol);
}

// --- RUN SCAN O DATĂ ---

async function runScanOnce() {
    console.log(`\n⏱ Scan pornit la ${new Date().toLocaleString()}`);

    ensureDir(DATA_DIR);
    ensureDir(WATCH_DIR);

    watchlist = loadWatchlist();

    const btcPerf = await fetchBTCPerformance();
    const historyWinners = loadRecentWinners(HISTORY_DAYS);

    const symbols = await getSymbolsToScan();
    console.log(`🔎 Scanăm ${symbols.length} simboluri...`);

    const analyses = [];
    for (const symbol of symbols) {
        try {
            const a = await analyzeSymbol(symbol, btcPerf, historyWinners);
            analyses.push(a);
            console.log(`  ${padRight(symbol, 12)} | scor: ${padLeft(a.score, 3)} | semnale: ${a.signals.join(', ')}`);
        } catch (err) {
            console.error(`Eroare analiză ${symbol}:`, err.message);
        }
    }

    analyses.sort((a, b) => b.score - a.score);
    const topList = analyses.slice(0, TOP_SAVE_COUNT);

    const dateString = new Date().toISOString().slice(0, 10);
    await saveDailyTopGainers(dateString, topList);

    // Prag adaptiv: primele 3 + orice scor >= 50 intră în watchlist
    const adaptiveThreshold = Math.max(50, WATCHLIST_SCORE_THRESHOLD);
    const top3Symbols = new Set(topList.slice(0, 3).map(i => i.symbol));
    for (const item of topList) {
        if (item.score >= adaptiveThreshold || top3Symbols.has(item.symbol)) {
            const existing = watchlist.get(item.symbol);
            if (!existing) {
                watchlist.set(item.symbol, {
                    symbol: item.symbol,
                    score: item.score,
                    signals: item.signals,
                    entryDate: new Date().toISOString(),
                    breakoutDetected: false
                });
                console.log(`⭐ ${item.symbol} adăugat în watchlist (scor ${item.score})`);
            }
        }
    }

    saveWatchlist();
    await monitorWatchlist();

    // Raport zilnic sumar (o singură dată pe zi)
    const todayStr = new Date().toISOString().slice(0, 10);
    if (lastDailyReportDate !== todayStr) {
        lastDailyReportDate = todayStr;
        const withScore = analyses.filter(a => a.score > 0);
        const top5 = withScore.slice(0, 5);
        const acc15Count = withScore.filter(a => a.intervals['15m']?.isAccumulationWindow).length;
        const breakoutCountTotal = Array.from(watchlist.values()).filter(w => w.breakoutDetected).length;
        const signalFreq = {};
        withScore.forEach(a => (a.signals || []).forEach(s => { signalFreq[s] = (signalFreq[s] || 0) + 1; }));
        const topSignal = Object.entries(signalFreq).sort((a, b) => b[1] - a[1]).slice(0, 3).map(([s, c]) => `${s}(${c})`).join(', ');

        let report = `📅 *RAPORT ZILNIC ${todayStr}* 📅\n` +
            `📊 Total analizate: ${analyses.length}\n` +
            `🟢 Cu acumulare 15m: ${acc15Count}\n` +
            `🔥 Breakouts în watchlist azi: ${breakoutCountTotal}\n` +
            `📈 Top semnale: ${topSignal || 'niciunul'}\n\n` +
            `🏆 *Top 5 azi:*\n`;
        top5.forEach((a, i) => {
            report += `${i + 1}. ${a.symbol} (${a.score}) — ${(a.signals || []).slice(0, 3).join(', ')}\n`;
        });

        try {
            await sendTelegram(report);
            console.log(`📨 Raport zilnic trimis pentru ${todayStr}`);
        } catch (err) {
            console.error(`Eroare raport zilnic: ${err.message}`);
        }
    }
}

// --- MAIN LOOP AVANSAT: scan complet 30min + watchlist 5min ---

async function mainLoop() {
    let fullScanCounter = 0;
    while (true) {
        try {
            if (fullScanCounter % 6 === 0) {
                // Scan complet la fiecare 30 min (6 × 5min)
                await runScanOnce();
            } else {
                // Doar watchlist check la fiecare 5 min
                watchlist = loadWatchlist();
                if (watchlist.size > 0) {
                    console.log(`\n⏱ Watchlist check la ${new Date().toLocaleString()}`);
                    await monitorWatchlist();
                } else {
                    console.log(`\n⏱ Watchlist goală la ${new Date().toLocaleString()} — așteptăm scan complet...`);
                }
            }
        } catch (err) {
            console.error('Eroare în scan loop:', err.message);
        }
        fullScanCounter++;
        console.log('💤 Pauză 5 minute...\n');
        await sleep(5 * 60 * 1000);
    }
}

// --- MOD GITHUB ACTIONS: run single scan, fără loop infinit ---
async function runSingleScan() {
    console.log(`\n🤖 GitHub Actions scan la ${new Date().toISOString()}`);
    ensureDir(DATA_DIR);
    ensureDir(WATCH_DIR);
    watchlist = loadWatchlist();
    const btcPerf = await fetchBTCPerformance();
    const historyWinners = [];
    const symbols = await getSymbolsToScan();
    const analyses = [];
    for (const symbol of symbols) {
        try {
            const a = await analyzeSymbol(symbol, btcPerf, historyWinners);
            analyses.push(a);
        } catch (err) {
            console.error(`Eroare analiză ${symbol}:`, err.message);
        }
    }
    analyses.sort((a, b) => b.score - a.score);
    const topList = analyses.slice(0, TOP_SAVE_COUNT);
    const dateString = new Date().toISOString().slice(0, 10);
    await saveDailyTopGainers(dateString, topList);
    const adaptiveThreshold = Math.max(50, WATCHLIST_SCORE_THRESHOLD);
    const top3Symbols = new Set(topList.slice(0, 3).map(i => i.symbol));
    for (const item of topList) {
        if (item.score >= adaptiveThreshold || top3Symbols.has(item.symbol)) {
            const existing = watchlist.get(item.symbol);
            if (!existing) {
                watchlist.set(item.symbol, { symbol: item.symbol, score: item.score, signals: item.signals, entryDate: new Date().toISOString(), breakoutDetected: false });
                console.log(`⭐ ${item.symbol} adăugat în watchlist (scor ${item.score})`);
            }
        }
    }
    saveWatchlist();
    await monitorWatchlist();
}

// Export pentru GitHub Actions
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { runSingleScan, runScanOnce, mainLoop };
}

// Detecție mod execuție
const isGitHubActions = process.env.GITHUB_ACTIONS === 'true';
const isDirectRun = typeof require !== 'undefined' && require.main === module;

if (isGitHubActions) {
    // GitHub Actions: run single scan + exit
    runSingleScan()
        .then(() => {
            console.log('✅ Scan complet. GitHub Actions se va opri.');
            process.exit(0);
        })
        .catch(err => {
            console.error('❌ Eroare fatală:', err.message);
            process.exit(1);
        });
} else if (isDirectRun) {
    // Local: loop infinit cu watchlist check la 5 min
    mainLoop().catch(err => console.error('Fatal:', err.message));
} else {
    console.log('⚡ Importat ca modul — runSingleScan() disponibil.');
}
