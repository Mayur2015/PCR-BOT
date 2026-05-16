import base64
import requests
import time
import csv
import os
import json
from datetime import datetime
import pytz
import gspread
from google.oauth2.service_account import Credentials
from SmartApi import SmartConnect
import pyotp

# ==========================================================
# TELEGRAM CONFIG - Railway variables
# ==========================================================
TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# ==========================================================
# GOOGLE SHEET CONFIG - Railway variables
# ==========================================================
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
GOOGLE_CREDENTIALS_BASE64 = os.getenv("GOOGLE_CREDENTIALS_BASE64")

# ==========================================================
# GLOBAL VARIABLES
# ==========================================================
smartApi = None
google_sheet = None
last_heartbeat_hour = -1

# Multiple active trades will be stored here
# Example: open_trades["PCR"] = {...}, open_trades["SMC"] = {...}, open_trades["ADX"] = {...}
open_trades = {}

PAPER_FILE = "paper_trades.csv"
ACTIVE_TRADES_FILE = "active_trades.json"

# ==========================================================
# STRATEGY SETTINGS
# ==========================================================
SLEEP_SECONDS = 60
NIFTY_LOT_QTY = 65


# PCR Strategy settings
PCR_SAMPLE_SECONDS = 180
ENTRY_ATM_PCR_CHANGE = 0.30
ENTRY_PCR_CHANGE = 0.07
EXIT_PCR_STRONG_REVERSAL = 0.05

# Common option exit settings
STOPLOSS_POINTS = 5
TARGET_POINTS = 8

# SMC + VWAP Strategy settings
SMC_ENABLED = True
SMC_SWING_LOOKBACK = 5              # last 5 candles for structure
SMC_BREAK_BUFFER_POINTS = 8         # NIFTY must break swing by this much
SMC_VWAP_SLOPE_POINTS = 1.5         # VWAP direction filter
SMC_MIN_CANDLES_REQUIRED = 8
SMC_COOLDOWN_SECONDS = 900          # 15 minutes after one SMC entry
SMC_BODY_MIN_POINTS = 3             # candle body confirmation

# ADX Strategy settings
ADX_ENABLED = True
ADX_PERIOD = 14
ADX_MIN_VALUE = 25
ADX_MIN_CANDLES_REQUIRED = 30
ADX_STOPLOSS_PERCENT = 20          # option premium stop loss
ADX_TARGET_PERCENT = 35            # option premium target
ADX_BREAKEVEN_PERCENT = 15         # after this profit, SL moves to cost
ADX_VWAP_FILTER = True
ADX_FALL_EXIT_CANDLES = 2          # exit if ADX falls for 2 candles

# RSI + Stochastic + EMA Strategy settings
RSI_STOCH_EMA_ENABLED = True
RSI_STOCH_EMA_NAME = "RSI_STOCH_EMA"
EMA_FAST_PERIOD = 25
EMA_MID_PERIOD = 75
EMA_SLOW_PERIOD = 140
RSI_PERIOD = 14
RSI_BULL_LEVEL = 55
RSI_BEAR_LEVEL = 45
STOCH_K_PERIOD = 14
STOCH_K_SMOOTH = 3
STOCH_D_PERIOD = 3
STOCH_CE_MAX_LEVEL = 40      # CE entry only when Stochastic pullback is not already overbought
STOCH_PE_MIN_LEVEL = 60      # PE entry only when Stochastic pullback is not already oversold
RSI_STOCH_MIN_CANDLES_REQUIRED = 150
RSI_STOCH_USE_VWAP_FILTER = True
RSI_STOCH_ENTRY_START_HOUR = 9
RSI_STOCH_ENTRY_START_MINUTE = 20
RSI_STOCH_ENTRY_END_HOUR = 15
RSI_STOCH_ENTRY_END_MINUTE = 15

# Market Structure Strategy settings - Strategy 5
# Pure independent Market Structure logic based on MSB / ZigZag / Fib confirmation.
MARKET_STRUCTURE_ENABLED = True
MARKET_STRUCTURE_NAME = "MARKET_STRUCTURE"
MS_ZIGZAG_LENGTH = 9
MS_FIB_FACTOR = 0.273
MS_MIN_CANDLES_REQUIRED = 30
MS_COOLDOWN_SECONDS = 900       # 15 minutes after one Market Structure entry
MS_MIN_BODY_POINTS = 3          # candle body confirmation for breakout candle

# Supertrend + EMA Crossover Strategy settings - Strategy 6
SUPER_EMA_ENABLED = True
SUPER_EMA_NAME = "SUPER_EMA"
SUPER_EMA_FAST_PERIOD = 5
SUPER_EMA_SLOW_PERIOD = 20
SUPER_EMA_ATR_PERIOD = 10
SUPER_EMA_ATR_MULTIPLIER = 3
SUPER_EMA_MIN_CANDLES_REQUIRED = 35
SUPER_EMA_COOLDOWN_SECONDS = 600       # 10 minutes after one entry
SUPER_EMA_ADX_FILTER = True
SUPER_EMA_MIN_ADX = 20
SUPER_EMA_OPTION_SL_PERCENT = 20       # initial option premium stoploss
SUPER_EMA_BREAKEVEN_PERCENT = 10       # move SL to entry after +10%
SUPER_EMA_LOCK1_PERCENT = 20           # after +20% profit
SUPER_EMA_LOCK1_SL_PERCENT = 10        # lock +10% profit
SUPER_EMA_LOCK2_PERCENT = 30           # after +30% profit
SUPER_EMA_LOCK2_SL_PERCENT = 20        # lock +20% profit
SUPER_EMA_TRAIL_START_PERCENT = 50     # after +50%, trail from highest premium
SUPER_EMA_TRAIL_GAP_PERCENT = 10       # trailing SL gap from highest premium

# Gamma Blast Expiry Strategy settings - Strategy 7
# Paper-trade only. Works only on expiry day from 1:30 PM to 3:05 PM.
GAMMA_BLAST_ENABLED = True
GAMMA_BLAST_NAME = "GAMMA_BLAST"
GAMMA_BLAST_QTY = NIFTY_LOT_QTY * 10          # 10 lots = 650 quantity
GAMMA_ENTRY_START_HOUR = 13
GAMMA_ENTRY_START_MINUTE = 30
GAMMA_ENTRY_END_HOUR = 15
GAMMA_ENTRY_END_MINUTE = 5
GAMMA_FORCE_EXIT_HOUR = 15
GAMMA_FORCE_EXIT_MINUTE = 8
GAMMA_MIN_NIFTY_MOVE_POINTS = 8               # direction pressure from previous snapshot
GAMMA_MIN_OI_UNWIND_PERCENT = 3.0             # writer unwinding side
GAMMA_MIN_OPPOSITE_OI_ADD_PERCENT = 2.0       # support side addition
GAMMA_MIN_PREMIUM_JUMP_PERCENT = 8.0          # next OTM premium velocity
GAMMA_MIN_CANDLE_BODY_POINTS = 5
GAMMA_MIN_CANDLE_EXPANSION_MULTIPLIER = 1.4
GAMMA_COOLDOWN_SECONDS = 900                  # 15 minutes after one Gamma trade
GAMMA_OPTION_HARD_SL_PERCENT = 25             # emergency premium SL
GAMMA_OPTION_PROFIT_COLLAPSE_PERCENT = 18     # exit if premium falls from highest
GAMMA_OI_REVERSAL_PERCENT = 2.0               # exit when writers come back
GAMMA_MIN_CONFIRMATION_SCORE = 5              # OI unwinding is mandatory; score filters fake moves

# PCR sample variables
last_pcr_sample_time = None
sample_pcr = None
sample_atm_pcr = None

# Exit counters only for PCR strategy
pcr_ce_decrease_count = 0
pcr_pe_increase_count = 0

# SMC variables
nifty_candles = []
current_candle = None
last_candle_minute = None
session_price_sum = 0.0
session_price_count = 0
last_vwap = None
current_vwap = None
last_smc_entry_time = None

# Market Structure variables
last_market_structure_entry_time = None
last_market_structure_signal_key = None

# Supertrend + EMA variables
last_super_ema_entry_time = None
last_super_ema_signal_key = None

# Gamma Blast variables
last_gamma_entry_time = None
gamma_snapshot = None
last_gamma_signal_key = None

# ==========================================================
# CSV / GOOGLE SHEET HEADERS
# ==========================================================
HEADERS = [
    "Trade ID", "Strategy Name", "Entry Time", "Exit Time", "Trade Type", "Quantity", "Symbol", "Token",
    "Entry Price", "Exit Price", "Points", "Result", "Exit Reason",
    "NIFTY Entry", "NIFTY Exit", "PCR Entry", "PCR Exit",
    "ATM PCR Entry", "ATM PCR Exit", "Max Pain Entry", "Max Pain Exit",
    "PCR Change Entry", "ATM PCR Change Entry", "VWAP Entry", "VWAP Exit",
    "Entry Trigger", "Exit Trigger", "Trade Duration Min"
]

# ==========================================================
# BASIC HELPERS
# ==========================================================
def ist_now():
    ist = pytz.timezone("Asia/Kolkata")
    return datetime.now(ist)


def send_telegram(msg):
    try:
        if not TOKEN or not CHAT_ID:
            print("Telegram TOKEN or CHAT_ID missing")
            return
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        payload = {"chat_id": CHAT_ID, "text": msg}
        response = requests.post(url, data=payload, timeout=10)
        print("TELEGRAM STATUS:", response.status_code)
        print("TELEGRAM RESPONSE:", response.text)
    except Exception as e:
        print("Telegram Error:", e)


def log_error(error_msg):
    try:
        now = ist_now()
        with open("error_log.txt", "a") as f:
            f.write(f"\n[{now.strftime('%Y-%m-%d %H:%M:%S')}] {error_msg}\n")
    except Exception:
        pass


def market_mode(now):
    if now.weekday() >= 5:
        return "WEEKEND"
    if now.hour < 9 or now.hour > 15:
        return "AFTER MARKET"
    if now.hour == 15 and now.minute > 30:
        return "AFTER MARKET"
    if now.hour == 9 and now.minute < 15:
        return "PRE MARKET"
    return "LIVE MARKET"


def generate_trade_id(strategy_name):
    now = ist_now()
    return f"{strategy_name}_{now.strftime('%Y%m%d_%H%M%S')}"


def get_trade_duration_minutes(entry_time, exit_time):
    try:
        e1 = datetime.strptime(entry_time, "%Y-%m-%d %H:%M:%S")
        e2 = datetime.strptime(exit_time, "%Y-%m-%d %H:%M:%S")
        return round((e2 - e1).total_seconds() / 60, 2)
    except Exception:
        return ""

# ==========================================================
# GOOGLE SHEET INIT
# ==========================================================
def init_google_sheet():
    global google_sheet
    try:
        if not GOOGLE_SHEET_ID or not GOOGLE_CREDENTIALS_BASE64:
            print("Google Sheet variables missing")
            send_telegram("⚠️ GOOGLE SHEET VARIABLES MISSING")
            return False

        creds_json = base64.b64decode(GOOGLE_CREDENTIALS_BASE64).decode("utf-8")
        creds_dict = json.loads(creds_json)
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ]
        creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        client = gspread.authorize(creds)
        google_sheet = client.open_by_key(GOOGLE_SHEET_ID).sheet1
        print("GOOGLE SHEET CONNECTED")
        send_telegram("✅ GOOGLE SHEET CONNECTED SUCCESSFULLY")
        return True
    except Exception as e:
        print("Google Sheet Init Error:", e)
        send_telegram(f"❌ GOOGLE SHEET INIT ERROR\n{e}")
        log_error(str(e))
        return False

# ==========================================================
# PAPER FILE INIT
# ==========================================================
def init_paper_file():
    try:
        if not os.path.exists(PAPER_FILE):
            with open(PAPER_FILE, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(HEADERS)
    except Exception as e:
        print("Paper File Init Error:", e)
        log_error(str(e))

# ==========================================================
# ACTIVE TRADE RECOVERY
# ==========================================================
def save_active_trades():
    try:
        with open(ACTIVE_TRADES_FILE, "w") as f:
            json.dump(open_trades, f, indent=2)
    except Exception as e:
        print("Active Trade Save Error:", e)
        log_error(str(e))


def load_active_trades():
    global open_trades
    try:
        if os.path.exists(ACTIVE_TRADES_FILE):
            with open(ACTIVE_TRADES_FILE, "r") as f:
                data = json.load(f)
            if isinstance(data, dict):
                open_trades = data
            else:
                open_trades = {}
        else:
            open_trades = {}

        if open_trades:
            lines = ["♻️ ACTIVE PAPER TRADES RECOVERED AFTER RESTART"]
            for strategy, trade in open_trades.items():
                lines.append(
                    f"{strategy}: {trade.get('trade_type')} | {trade.get('symbol')} | Entry {trade.get('entry_price')} | {trade.get('entry_time')}"
                )
            send_telegram("\n".join(lines))
            print("ACTIVE TRADES RECOVERED:", open_trades)
        else:
            print("No active trades to recover")
    except Exception as e:
        open_trades = {}
        print("Active Trade Load Error:", e)
        log_error(str(e))


def add_open_trade(strategy_name, trade):
    open_trades[strategy_name] = trade
    save_active_trades()


def remove_open_trade(strategy_name):
    if strategy_name in open_trades:
        del open_trades[strategy_name]
    save_active_trades()

# ==========================================================
# SAVE COMPLETED TRADE
# ==========================================================
def build_completed_trade_row(trade, exit_time, exit_price, exit_reason, exit_trigger, nifty_exit, pcr_exit, atm_pcr_exit, max_pain_exit, vwap_exit):
    points = round(exit_price - trade["entry_price"], 2)
    result = "PROFIT" if points > 0 else "LOSS" if points < 0 else "NO PROFIT NO LOSS"
    duration = get_trade_duration_minutes(trade["entry_time"], exit_time)

    return [
        trade.get("trade_id", ""),
        trade.get("strategy_name", ""),
        trade["entry_time"],
        exit_time,
        trade["trade_type"],
        trade.get("quantity", NIFTY_LOT_QTY),
        trade["symbol"],
        trade["token"],
        trade["entry_price"],
        exit_price,
        points,
        result,
        exit_reason,
        trade.get("nifty_entry", ""),
        nifty_exit,
        trade.get("pcr_entry", ""),
        pcr_exit,
        trade.get("atm_pcr_entry", ""),
        atm_pcr_exit,
        trade.get("max_pain_entry", ""),
        max_pain_exit,
        trade.get("pcr_change_entry", ""),
        trade.get("atm_pcr_change_entry", ""),
        trade.get("vwap_entry", ""),
        vwap_exit,
        trade.get("entry_trigger", ""),
        exit_trigger,
        duration
    ]


def save_paper_trade(trade, exit_time, exit_price, exit_reason, exit_trigger, nifty_exit, pcr_exit, atm_pcr_exit, max_pain_exit, vwap_exit):
    try:
        row = build_completed_trade_row(
            trade, exit_time, exit_price, exit_reason, exit_trigger,
            nifty_exit, pcr_exit, atm_pcr_exit, max_pain_exit, vwap_exit
        )
        with open(PAPER_FILE, "a", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(row)
    except Exception as e:
        print("Paper Trade Save Error:", e)
        log_error(str(e))


def save_google_trade(trade, exit_time, exit_price, exit_reason, exit_trigger, nifty_exit, pcr_exit, atm_pcr_exit, max_pain_exit, vwap_exit):
    try:
        if google_sheet is None:
            print("Google Sheet not connected")
            return
        row = build_completed_trade_row(
            trade, exit_time, exit_price, exit_reason, exit_trigger,
            nifty_exit, pcr_exit, atm_pcr_exit, max_pain_exit, vwap_exit
        )
        google_sheet.append_row(row, value_input_option="USER_ENTERED")
        print("Trade saved to Google Sheet")
    except Exception as e:
        print("Google Sheet Save Error:", e)
        send_telegram(f"❌ GOOGLE SHEET SAVE ERROR\n{e}")
        log_error(str(e))

# ==========================================================
# LOGIN FUNCTION
# ==========================================================
def login():
    global smartApi
    try:
        api_key = os.getenv("API_KEY")
        client_id = os.getenv("CLIENT_ID")
        password = os.getenv("PASSWORD")
        totp_key = os.getenv("TOTP_KEY")

        if not api_key or not client_id or not password or not totp_key:
            print("Railway environment variables missing")
            send_telegram(
                "❌ RAILWAY VARIABLES MISSING\n"
                "Check API_KEY, CLIENT_ID, PASSWORD, TOTP_KEY, TOKEN, CHAT_ID"
            )
            return False

        smartApi = SmartConnect(api_key)
        totp = pyotp.TOTP(totp_key).now()
        data = smartApi.generateSession(client_id, password, totp)

        if data and data.get("status"):
            print("LOGIN SUCCESS")
            send_telegram("🌅 7 STRATEGY PAPER SYSTEM STARTED SUCCESSFULLY")
            return True

        print("LOGIN FAILED:", data)
        send_telegram(f"❌ LOGIN FAILED\n{data}")
        return False
    except Exception as e:
        print("LOGIN ERROR:", e)
        send_telegram(f"❌ LOGIN ERROR\n{e}")
        return False

# ==========================================================
# SAFE LTP CALL
# ==========================================================
def safe_ltp(exchange, symbol, token, retry=3):
    global smartApi
    for _ in range(retry):
        try:
            data = smartApi.ltpData(exchange, symbol, token)
            if data and data.get("data") and data["data"].get("ltp") is not None:
                return float(data["data"]["ltp"])
            if data and data.get("message") == "Invalid Token":
                print("SESSION EXPIRED - RELOGIN")
                send_telegram("♻️ SESSION EXPIRED - RELOGIN")
                login()
                time.sleep(3)
        except Exception as e:
            print("LTP Retry Error:", e)
            log_error(str(e))
            send_telegram(f"❌ LTP ERROR\n{e}")
            login()
            time.sleep(3)
    return None

# ==========================================================
# SMC + VWAP HELPERS
# ==========================================================
def update_vwap_and_candle(nifty, now):
    """
    This builds 1-minute NIFTY candles from live LTP samples.
    VWAP here is an intraday running price average approximation because NIFTY index LTP does not provide normal traded volume like equity candles.
    """
    global current_candle, last_candle_minute, nifty_candles
    global session_price_sum, session_price_count, last_vwap, current_vwap

    minute_key = now.strftime("%Y-%m-%d %H:%M")

    # reset intraday VWAP approximation near new trading day/session
    if now.hour == 9 and now.minute <= 16 and session_price_count > 0:
        if len(nifty_candles) == 0 or nifty_candles[-1].get("date") != now.strftime("%Y-%m-%d"):
            session_price_sum = 0.0
            session_price_count = 0
            last_vwap = None
            current_vwap = None

    session_price_sum += float(nifty)
    session_price_count += 1
    last_vwap = current_vwap
    current_vwap = round(session_price_sum / session_price_count, 2)

    if current_candle is None:
        current_candle = {
            "date": now.strftime("%Y-%m-%d"),
            "minute": minute_key,
            "open": float(nifty),
            "high": float(nifty),
            "low": float(nifty),
            "close": float(nifty),
            "vwap": current_vwap
        }
        last_candle_minute = minute_key
        return

    if minute_key == last_candle_minute:
        current_candle["high"] = max(current_candle["high"], float(nifty))
        current_candle["low"] = min(current_candle["low"], float(nifty))
        current_candle["close"] = float(nifty)
        current_candle["vwap"] = current_vwap
    else:
        nifty_candles.append(current_candle)
        if len(nifty_candles) > 60:
            nifty_candles = nifty_candles[-60:]
        current_candle = {
            "date": now.strftime("%Y-%m-%d"),
            "minute": minute_key,
            "open": float(nifty),
            "high": float(nifty),
            "low": float(nifty),
            "close": float(nifty),
            "vwap": current_vwap
        }
        last_candle_minute = minute_key


def smc_cooldown_ok(now):
    if last_smc_entry_time is None:
        return True
    try:
        return (now - last_smc_entry_time).total_seconds() >= SMC_COOLDOWN_SECONDS
    except Exception:
        return True


def get_smc_signal(nifty):
    """
    Simple first version of SMC + VWAP:
    BUY CE: bullish break of structure above recent swing high + above rising VWAP + bullish candle body.
    BUY PE: bearish break of structure below recent swing low + below falling VWAP + bearish candle body.
    """
    if not SMC_ENABLED:
        return None, None

    if current_candle is None or len(nifty_candles) < SMC_MIN_CANDLES_REQUIRED:
        return None, None

    if current_vwap is None or last_vwap is None:
        return None, None

    recent = nifty_candles[-SMC_SWING_LOOKBACK:]
    swing_high = max(c["high"] for c in recent)
    swing_low = min(c["low"] for c in recent)

    candle_open = current_candle["open"]
    candle_close = float(nifty)
    body = abs(candle_close - candle_open)

    vwap_rising = current_vwap >= last_vwap + SMC_VWAP_SLOPE_POINTS
    vwap_falling = current_vwap <= last_vwap - SMC_VWAP_SLOPE_POINTS

    bullish_bos = candle_close >= swing_high + SMC_BREAK_BUFFER_POINTS
    bearish_bos = candle_close <= swing_low - SMC_BREAK_BUFFER_POINTS

    bullish_body = candle_close > candle_open and body >= SMC_BODY_MIN_POINTS
    bearish_body = candle_close < candle_open and body >= SMC_BODY_MIN_POINTS

    if bullish_bos and bullish_body and candle_close > current_vwap and vwap_rising:
        trigger = (
            f"SMC BUY CE: Bullish BOS above swing high {round(swing_high,2)}, "
            f"NIFTY {round(candle_close,2)} above VWAP {current_vwap}, VWAP rising"
        )
        return "BUY CE", trigger

    if bearish_bos and bearish_body and candle_close < current_vwap and vwap_falling:
        trigger = (
            f"SMC BUY PE: Bearish BOS below swing low {round(swing_low,2)}, "
            f"NIFTY {round(candle_close,2)} below VWAP {current_vwap}, VWAP falling"
        )
        return "BUY PE", trigger

    return None, None

# ==========================================================
# MARKET STRUCTURE STRATEGY HELPERS - STRATEGY 5
# ==========================================================
def market_structure_cooldown_ok(now):
    if last_market_structure_entry_time is None:
        return True
    try:
        return (now - last_market_structure_entry_time).total_seconds() >= MS_COOLDOWN_SECONDS
    except Exception:
        return True


def find_market_structure_swings(candles, zigzag_len=9):
    """
    Non-repaint swing detection.
    A swing high/low is confirmed only after zigzag_len candles are available on both sides.
    This is safer for paper/live automation than using a future-looking visual signal.
    """
    highs = []
    lows = []

    if candles is None or len(candles) < (zigzag_len * 2 + 3):
        return highs, lows

    for i in range(zigzag_len, len(candles) - zigzag_len):
        window = candles[i - zigzag_len:i + zigzag_len + 1]
        high = float(candles[i]["high"])
        low = float(candles[i]["low"])
        max_high = max(float(c["high"]) for c in window)
        min_low = min(float(c["low"]) for c in window)

        if high == max_high:
            highs.append({"index": i, "price": high, "minute": candles[i].get("minute", "")})
        if low == min_low:
            lows.append({"index": i, "price": low, "minute": candles[i].get("minute", "")})

    return highs, lows


def get_market_structure_state():
    """Return latest Market Structure reference levels for printing/debugging."""
    candles = get_all_working_candles()
    if len(candles) < MS_MIN_CANDLES_REQUIRED:
        return None

    completed = list(nifty_candles)
    highs, lows = find_market_structure_swings(completed, MS_ZIGZAG_LENGTH)
    if not highs or not lows:
        return None

    last_high = highs[-1]
    last_low = lows[-1]
    structure_range = abs(float(last_high["price"]) - float(last_low["price"]))
    fib_points = round(structure_range * MS_FIB_FACTOR, 2)

    return {
        "last_swing_high": round(float(last_high["price"]), 2),
        "last_swing_low": round(float(last_low["price"]), 2),
        "fib_points": fib_points,
        "high_minute": last_high.get("minute", ""),
        "low_minute": last_low.get("minute", "")
    }


def get_market_structure_signal(nifty):
    """
    Strategy-5 Pure Market Structure:
    BUY CE: Current candle closes above latest confirmed swing high + Fib confirmation distance.
    BUY PE: Current candle closes below latest confirmed swing low - Fib confirmation distance.

    This does NOT use PCR, VWAP, RSI, Stochastic, EMA or ADX.
    """
    global last_market_structure_signal_key

    if not MARKET_STRUCTURE_ENABLED:
        return None, None

    if current_candle is None or len(nifty_candles) < MS_MIN_CANDLES_REQUIRED:
        return None, None

    completed = list(nifty_candles)
    highs, lows = find_market_structure_swings(completed, MS_ZIGZAG_LENGTH)
    if not highs or not lows:
        return None, None

    last_high = highs[-1]
    last_low = lows[-1]
    swing_high = float(last_high["price"])
    swing_low = float(last_low["price"])
    structure_range = abs(swing_high - swing_low)

    if structure_range <= 0:
        return None, None

    fib_points = structure_range * MS_FIB_FACTOR
    bullish_break_level = swing_high + fib_points
    bearish_break_level = swing_low - fib_points

    candle_open = float(current_candle["open"])
    candle_close = float(nifty)
    candle_body = abs(candle_close - candle_open)

    bullish_body = candle_close > candle_open and candle_body >= MS_MIN_BODY_POINTS
    bearish_body = candle_close < candle_open and candle_body >= MS_MIN_BODY_POINTS

    # Prevent repeated signal on the same structural level after restart/loops.
    bullish_key = f"CE_{last_high['index']}_{round(swing_high, 2)}"
    bearish_key = f"PE_{last_low['index']}_{round(swing_low, 2)}"

    if candle_close >= bullish_break_level and bullish_body and last_market_structure_signal_key != bullish_key:
        last_market_structure_signal_key = bullish_key
        trigger = (
            f"MARKET_STRUCTURE BUY CE: MSB above swing high {round(swing_high, 2)} "
            f"with Fib {MS_FIB_FACTOR} confirmation {round(fib_points, 2)} points, "
            f"break level {round(bullish_break_level, 2)}, NIFTY {round(candle_close, 2)}"
        )
        return "BUY CE", trigger

    if candle_close <= bearish_break_level and bearish_body and last_market_structure_signal_key != bearish_key:
        last_market_structure_signal_key = bearish_key
        trigger = (
            f"MARKET_STRUCTURE BUY PE: MSB below swing low {round(swing_low, 2)} "
            f"with Fib {MS_FIB_FACTOR} confirmation {round(fib_points, 2)} points, "
            f"break level {round(bearish_break_level, 2)}, NIFTY {round(candle_close, 2)}"
        )
        return "BUY PE", trigger

    return None, None

# ==========================================================
# ADX STRATEGY HELPERS
# ==========================================================
def get_all_working_candles():
    """Return completed candles plus current running candle."""
    candles = list(nifty_candles)
    if current_candle is not None:
        candles.append(current_candle)
    return candles


def calculate_adx_values(candles, period=14):
    """
    Calculate ADX, +DI and -DI using Wilder smoothing.
    Returns a list of dictionaries. Last item is the latest ADX state.
    """
    if candles is None or len(candles) < (period * 2 + 2):
        return []

    tr_list = []
    plus_dm_list = []
    minus_dm_list = []

    for i in range(1, len(candles)):
        high = float(candles[i]["high"])
        low = float(candles[i]["low"])
        prev_high = float(candles[i - 1]["high"])
        prev_low = float(candles[i - 1]["low"])
        prev_close = float(candles[i - 1]["close"])

        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        up_move = high - prev_high
        down_move = prev_low - low

        plus_dm = up_move if up_move > down_move and up_move > 0 else 0.0
        minus_dm = down_move if down_move > up_move and down_move > 0 else 0.0

        tr_list.append(tr)
        plus_dm_list.append(plus_dm)
        minus_dm_list.append(minus_dm)

    if len(tr_list) < period:
        return []

    smoothed_tr = sum(tr_list[:period])
    smoothed_plus_dm = sum(plus_dm_list[:period])
    smoothed_minus_dm = sum(minus_dm_list[:period])

    dx_values = []
    output = []

    for i in range(period, len(tr_list)):
        smoothed_tr = smoothed_tr - (smoothed_tr / period) + tr_list[i]
        smoothed_plus_dm = smoothed_plus_dm - (smoothed_plus_dm / period) + plus_dm_list[i]
        smoothed_minus_dm = smoothed_minus_dm - (smoothed_minus_dm / period) + minus_dm_list[i]

        if smoothed_tr == 0:
            plus_di = 0.0
            minus_di = 0.0
        else:
            plus_di = 100 * (smoothed_plus_dm / smoothed_tr)
            minus_di = 100 * (smoothed_minus_dm / smoothed_tr)

        di_sum = plus_di + minus_di
        dx = 0.0 if di_sum == 0 else 100 * abs(plus_di - minus_di) / di_sum
        dx_values.append(dx)

        if len(dx_values) < period:
            continue
        elif len(dx_values) == period:
            adx = sum(dx_values) / period
        else:
            prev_adx = output[-1]["adx"]
            adx = ((prev_adx * (period - 1)) + dx) / period

        output.append({
            "adx": round(adx, 2),
            "plus_di": round(plus_di, 2),
            "minus_di": round(minus_di, 2),
            "close": float(candles[i + 1]["close"]) if (i + 1) < len(candles) else float(candles[-1]["close"]),
        })

    return output


def get_latest_adx_state():
    candles = get_all_working_candles()
    if len(candles) < ADX_MIN_CANDLES_REQUIRED:
        return None
    values = calculate_adx_values(candles, ADX_PERIOD)
    if not values:
        return None
    return values[-1]


def is_adx_falling(values, count=2):
    if values is None or len(values) < count + 1:
        return False
    recent = values[-(count + 1):]
    for i in range(1, len(recent)):
        if recent[i]["adx"] >= recent[i - 1]["adx"]:
            return False
    return True


def get_adx_signal(nifty):
    """
    Strategy-3 ADX:
    BUY CE: ADX strong + +DI above -DI + NIFTY above VWAP.
    BUY PE: ADX strong + -DI above +DI + NIFTY below VWAP.
    """
    if not ADX_ENABLED:
        return None, None

    candles = get_all_working_candles()
    if len(candles) < ADX_MIN_CANDLES_REQUIRED:
        return None, None

    values = calculate_adx_values(candles, ADX_PERIOD)
    if not values:
        return None, None

    latest = values[-1]
    adx = latest["adx"]
    plus_di = latest["plus_di"]
    minus_di = latest["minus_di"]

    if adx < ADX_MIN_VALUE:
        return None, None

    if ADX_VWAP_FILTER and current_vwap is None:
        return None, None

    above_vwap = True if not ADX_VWAP_FILTER else float(nifty) > float(current_vwap)
    below_vwap = True if not ADX_VWAP_FILTER else float(nifty) < float(current_vwap)

    if plus_di > minus_di and above_vwap:
        trigger = (
            f"ADX BUY CE: ADX {adx} > {ADX_MIN_VALUE}, +DI {plus_di} > -DI {minus_di}, "
            f"NIFTY {round(float(nifty), 2)} above VWAP {current_vwap}"
        )
        return "BUY CE", trigger

    if minus_di > plus_di and below_vwap:
        trigger = (
            f"ADX BUY PE: ADX {adx} > {ADX_MIN_VALUE}, -DI {minus_di} > +DI {plus_di}, "
            f"NIFTY {round(float(nifty), 2)} below VWAP {current_vwap}"
        )
        return "BUY PE", trigger

    return None, None


def check_adx_exit(trade, current_price, nifty):
    """Return (exit_reason, exit_trigger) for ADX strategy only."""
    if current_price is None:
        return None, None

    entry_price = float(trade["entry_price"])
    profit_percent = round(((float(current_price) - entry_price) / entry_price) * 100, 2)

    if profit_percent <= -ADX_STOPLOSS_PERCENT:
        return "ADX STOPLOSS HIT", f"OPTION LOSS {profit_percent}%"

    if profit_percent >= ADX_TARGET_PERCENT:
        return "ADX TARGET HIT", f"OPTION PROFIT {profit_percent}%"

    if profit_percent >= ADX_BREAKEVEN_PERCENT and not trade.get("breakeven_active"):
        trade["breakeven_active"] = True
        trade["breakeven_price"] = entry_price
        save_active_trades()

    if trade.get("breakeven_active") and float(current_price) <= float(trade.get("breakeven_price", entry_price)):
        return "ADX BREAKEVEN EXIT", f"PROFIT LOCKED THEN BACK TO ENTRY, CURRENT PROFIT {profit_percent}%"

    candles = get_all_working_candles()
    values = calculate_adx_values(candles, ADX_PERIOD)
    if not values:
        return None, None

    latest = values[-1]
    adx = latest["adx"]
    plus_di = latest["plus_di"]
    minus_di = latest["minus_di"]

    if trade["trade_type"] == "BUY CE":
        if plus_di < minus_di:
            return "ADX CE DI REVERSAL EXIT", f"+DI {plus_di} < -DI {minus_di}"
        if current_vwap is not None and float(nifty) < float(current_vwap):
            return "ADX CE VWAP EXIT", f"NIFTY {round(float(nifty), 2)} below VWAP {current_vwap}"

    elif trade["trade_type"] == "BUY PE":
        if minus_di < plus_di:
            return "ADX PE DI REVERSAL EXIT", f"-DI {minus_di} < +DI {plus_di}"
        if current_vwap is not None and float(nifty) > float(current_vwap):
            return "ADX PE VWAP EXIT", f"NIFTY {round(float(nifty), 2)} above VWAP {current_vwap}"

    if is_adx_falling(values, ADX_FALL_EXIT_CANDLES):
        return "ADX WEAKENING EXIT", f"ADX falling for {ADX_FALL_EXIT_CANDLES} candles, latest ADX {adx}"

    return None, None


# ==========================================================
# RSI + STOCHASTIC + EMA STRATEGY HELPERS
# ==========================================================
def calculate_ema_series(values, period):
    if values is None or len(values) < period:
        return []
    multiplier = 2 / (period + 1)
    ema_values = []
    ema = sum(values[:period]) / period
    ema_values.append(ema)
    for price in values[period:]:
        ema = (price - ema) * multiplier + ema
        ema_values.append(ema)
    return ema_values


def calculate_rsi_series(closes, period=14):
    if closes is None or len(closes) < period + 2:
        return []

    gains = []
    losses = []
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        gains.append(max(change, 0))
        losses.append(abs(min(change, 0)))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    rsi_values = []

    for i in range(period, len(gains)):
        avg_gain = ((avg_gain * (period - 1)) + gains[i]) / period
        avg_loss = ((avg_loss * (period - 1)) + losses[i]) / period
        if avg_loss == 0:
            rsi = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))
        rsi_values.append(round(rsi, 2))

    return rsi_values


def simple_average(values):
    if not values:
        return None
    return sum(values) / len(values)


def calculate_stochastic_values(candles, k_period=14, k_smooth=3, d_period=3):
    if candles is None or len(candles) < k_period + k_smooth + d_period + 2:
        return []

    raw_k = []
    for i in range(k_period - 1, len(candles)):
        window = candles[i - k_period + 1:i + 1]
        highest_high = max(float(c["high"]) for c in window)
        lowest_low = min(float(c["low"]) for c in window)
        close = float(candles[i]["close"])
        if highest_high == lowest_low:
            k = 50.0
        else:
            k = ((close - lowest_low) / (highest_high - lowest_low)) * 100
        raw_k.append(k)

    smooth_k = []
    for i in range(k_smooth - 1, len(raw_k)):
        smooth_k.append(simple_average(raw_k[i - k_smooth + 1:i + 1]))

    output = []
    for i in range(d_period - 1, len(smooth_k)):
        d = simple_average(smooth_k[i - d_period + 1:i + 1])
        output.append({"k": round(smooth_k[i], 2), "d": round(d, 2)})

    return output


def get_latest_rsi_stoch_ema_state():
    candles = get_all_working_candles()
    if len(candles) < RSI_STOCH_MIN_CANDLES_REQUIRED:
        return None

    closes = [float(c["close"]) for c in candles]
    ema25 = calculate_ema_series(closes, EMA_FAST_PERIOD)
    ema75 = calculate_ema_series(closes, EMA_MID_PERIOD)
    ema140 = calculate_ema_series(closes, EMA_SLOW_PERIOD)
    rsi_values = calculate_rsi_series(closes, RSI_PERIOD)
    stoch_values = calculate_stochastic_values(candles, STOCH_K_PERIOD, STOCH_K_SMOOTH, STOCH_D_PERIOD)

    if not ema25 or not ema75 or not ema140 or len(rsi_values) < 2 or len(stoch_values) < 2:
        return None

    return {
        "ema25": round(ema25[-1], 2),
        "ema75": round(ema75[-1], 2),
        "ema140": round(ema140[-1], 2),
        "rsi": round(rsi_values[-1], 2),
        "prev_rsi": round(rsi_values[-2], 2),
        "stoch_k": round(stoch_values[-1]["k"], 2),
        "stoch_d": round(stoch_values[-1]["d"], 2),
        "prev_stoch_k": round(stoch_values[-2]["k"], 2),
        "prev_stoch_d": round(stoch_values[-2]["d"], 2),
        "close": round(closes[-1], 2)
    }


def rsi_stoch_entry_time_ok(now):
    start_ok = (now.hour > RSI_STOCH_ENTRY_START_HOUR) or (
        now.hour == RSI_STOCH_ENTRY_START_HOUR and now.minute >= RSI_STOCH_ENTRY_START_MINUTE
    )
    end_ok = (now.hour < RSI_STOCH_ENTRY_END_HOUR) or (
        now.hour == RSI_STOCH_ENTRY_END_HOUR and now.minute <= RSI_STOCH_ENTRY_END_MINUTE
    )
    return start_ok and end_ok


def get_rsi_stoch_ema_signal(nifty, now):
    """
    Strategy-4 RSI + Stochastic + EMA:
    BUY CE: EMA25 > EMA75 > EMA140 + RSI > 55 + Stoch K crosses above D from pullback zone.
    BUY PE: EMA25 < EMA75 < EMA140 + RSI < 45 + Stoch K crosses below D from pullback zone.
    """
    if not RSI_STOCH_EMA_ENABLED:
        return None, None

    if not rsi_stoch_entry_time_ok(now):
        return None, None

    state = get_latest_rsi_stoch_ema_state()
    if state is None:
        return None, None

    price = float(nifty)
    ema25 = state["ema25"]
    ema75 = state["ema75"]
    ema140 = state["ema140"]
    rsi = state["rsi"]
    k = state["stoch_k"]
    d = state["stoch_d"]
    prev_k = state["prev_stoch_k"]
    prev_d = state["prev_stoch_d"]

    bullish_ema = ema25 > ema75 > ema140
    bearish_ema = ema25 < ema75 < ema140
    bullish_cross = prev_k <= prev_d and k > d
    bearish_cross = prev_k >= prev_d and k < d

    above_vwap = True
    below_vwap = True
    if RSI_STOCH_USE_VWAP_FILTER:
        if current_vwap is None:
            return None, None
        above_vwap = price > float(current_vwap)
        below_vwap = price < float(current_vwap)

    if bullish_ema and price > ema25 and rsi >= RSI_BULL_LEVEL and bullish_cross and k <= STOCH_CE_MAX_LEVEL and above_vwap:
        trigger = (
            f"RSI_STOCH_EMA BUY CE: EMA25 {ema25} > EMA75 {ema75} > EMA140 {ema140}, "
            f"RSI {rsi} >= {RSI_BULL_LEVEL}, Stoch K {k} crossed above D {d}, "
            f"NIFTY {round(price, 2)} above EMA25 and VWAP {current_vwap}"
        )
        return "BUY CE", trigger

    if bearish_ema and price < ema25 and rsi <= RSI_BEAR_LEVEL and bearish_cross and k >= STOCH_PE_MIN_LEVEL and below_vwap:
        trigger = (
            f"RSI_STOCH_EMA BUY PE: EMA25 {ema25} < EMA75 {ema75} < EMA140 {ema140}, "
            f"RSI {rsi} <= {RSI_BEAR_LEVEL}, Stoch K {k} crossed below D {d}, "
            f"NIFTY {round(price, 2)} below EMA25 and VWAP {current_vwap}"
        )
        return "BUY PE", trigger

    return None, None


def check_rsi_stoch_ema_exit(trade, nifty):
    state = get_latest_rsi_stoch_ema_state()
    if state is None:
        return None, None

    price = float(nifty)
    ema25 = state["ema25"]
    ema75 = state["ema75"]
    rsi = state["rsi"]
    k = state["stoch_k"]
    d = state["stoch_d"]
    prev_k = state["prev_stoch_k"]
    prev_d = state["prev_stoch_d"]

    bearish_cross = prev_k >= prev_d and k < d
    bullish_cross = prev_k <= prev_d and k > d

    if trade["trade_type"] == "BUY CE":
        if rsi < 50:
            return "RSI_STOCH_EMA CE RSI EXIT", f"RSI {rsi} below 50"
        if bearish_cross:
            return "RSI_STOCH_EMA CE STOCH REVERSAL", f"Stoch K {k} crossed below D {d}"
        if price < ema25:
            return "RSI_STOCH_EMA CE EMA EXIT", f"NIFTY {round(price,2)} below EMA25 {ema25}"
        if ema25 < ema75:
            return "RSI_STOCH_EMA CE TREND EXIT", f"EMA25 {ema25} below EMA75 {ema75}"

    elif trade["trade_type"] == "BUY PE":
        if rsi > 50:
            return "RSI_STOCH_EMA PE RSI EXIT", f"RSI {rsi} above 50"
        if bullish_cross:
            return "RSI_STOCH_EMA PE STOCH REVERSAL", f"Stoch K {k} crossed above D {d}"
        if price > ema25:
            return "RSI_STOCH_EMA PE EMA EXIT", f"NIFTY {round(price,2)} above EMA25 {ema25}"
        if ema25 > ema75:
            return "RSI_STOCH_EMA PE TREND EXIT", f"EMA25 {ema25} above EMA75 {ema75}"

    return None, None


# ==========================================================
# SUPERTREND + EMA CROSSOVER HELPERS - STRATEGY 6
# ==========================================================
def super_ema_cooldown_ok(now):
    if last_super_ema_entry_time is None:
        return True
    try:
        return (now - last_super_ema_entry_time).total_seconds() >= SUPER_EMA_COOLDOWN_SECONDS
    except Exception:
        return True


def calculate_supertrend_values(candles, atr_period=10, multiplier=3):
    """
    Calculate Supertrend from NIFTY candles.
    Direction GREEN means close is above Supertrend line.
    Direction RED means close is below Supertrend line.
    """
    if candles is None or len(candles) < atr_period + 2:
        return []

    trs = []
    for i in range(len(candles)):
        high = float(candles[i]["high"])
        low = float(candles[i]["low"])
        if i == 0:
            tr = high - low
        else:
            prev_close = float(candles[i - 1]["close"])
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)

    atrs = [None] * len(candles)
    first_atr = sum(trs[1:atr_period + 1]) / atr_period
    atrs[atr_period] = first_atr
    for i in range(atr_period + 1, len(candles)):
        atrs[i] = ((atrs[i - 1] * (atr_period - 1)) + trs[i]) / atr_period

    output = []
    final_upper = None
    final_lower = None
    supertrend = None
    direction = None

    for i in range(atr_period, len(candles)):
        high = float(candles[i]["high"])
        low = float(candles[i]["low"])
        close = float(candles[i]["close"])
        prev_close = float(candles[i - 1]["close"]) if i > 0 else close
        atr = atrs[i]
        if atr is None:
            continue

        hl2 = (high + low) / 2
        basic_upper = hl2 + (multiplier * atr)
        basic_lower = hl2 - (multiplier * atr)

        if final_upper is None:
            final_upper = basic_upper
            final_lower = basic_lower
        else:
            final_upper = basic_upper if (basic_upper < final_upper or prev_close > final_upper) else final_upper
            final_lower = basic_lower if (basic_lower > final_lower or prev_close < final_lower) else final_lower

        if supertrend is None:
            if close >= final_lower:
                supertrend = final_lower
                direction = "GREEN"
            else:
                supertrend = final_upper
                direction = "RED"
        elif supertrend == final_upper:
            if close <= final_upper:
                supertrend = final_upper
                direction = "RED"
            else:
                supertrend = final_lower
                direction = "GREEN"
        else:
            if close >= final_lower:
                supertrend = final_lower
                direction = "GREEN"
            else:
                supertrend = final_upper
                direction = "RED"

        output.append({
            "supertrend": round(supertrend, 2),
            "direction": direction,
            "atr": round(atr, 2),
            "close": round(close, 2)
        })

    return output


def get_latest_super_ema_state():
    candles = get_all_working_candles()
    if len(candles) < SUPER_EMA_MIN_CANDLES_REQUIRED:
        return None

    closes = [float(c["close"]) for c in candles]
    ema_fast = calculate_ema_series(closes, SUPER_EMA_FAST_PERIOD)
    ema_slow = calculate_ema_series(closes, SUPER_EMA_SLOW_PERIOD)
    st_values = calculate_supertrend_values(
        candles,
        SUPER_EMA_ATR_PERIOD,
        SUPER_EMA_ATR_MULTIPLIER
    )

    if len(ema_fast) < 2 or len(ema_slow) < 2 or len(st_values) < 2:
        return None

    return {
        "ema_fast": round(ema_fast[-1], 2),
        "ema_slow": round(ema_slow[-1], 2),
        "prev_ema_fast": round(ema_fast[-2], 2),
        "prev_ema_slow": round(ema_slow[-2], 2),
        "supertrend": st_values[-1]["supertrend"],
        "supertrend_direction": st_values[-1]["direction"],
        "prev_supertrend_direction": st_values[-2]["direction"],
        "close": round(closes[-1], 2)
    }


def get_super_ema_signal(nifty):
    """
    Strategy-6 Supertrend + EMA Crossover:
    BUY CE: EMA5 crosses above EMA20 and Supertrend is GREEN.
    BUY PE: EMA5 crosses below EMA20 and Supertrend is RED.
    Optional ADX filter avoids sideways-market fake signals.
    """
    global last_super_ema_signal_key

    if not SUPER_EMA_ENABLED:
        return None, None

    state = get_latest_super_ema_state()
    if state is None:
        return None, None

    if SUPER_EMA_ADX_FILTER:
        adx_state = get_latest_adx_state()
        if adx_state is None or float(adx_state.get("adx", 0)) < SUPER_EMA_MIN_ADX:
            return None, None

    price = float(nifty)
    ema_fast = state["ema_fast"]
    ema_slow = state["ema_slow"]
    prev_fast = state["prev_ema_fast"]
    prev_slow = state["prev_ema_slow"]
    st = state["supertrend"]
    st_dir = state["supertrend_direction"]

    bullish_cross = prev_fast <= prev_slow and ema_fast > ema_slow
    bearish_cross = prev_fast >= prev_slow and ema_fast < ema_slow

    signal_key = f"{current_candle.get('minute', '')}_{round(ema_fast, 2)}_{round(ema_slow, 2)}_{st_dir}"

    if bullish_cross and st_dir == "GREEN" and price > ema_fast and price > ema_slow and price > st:
        key = "CE_" + signal_key
        if last_super_ema_signal_key == key:
            return None, None
        last_super_ema_signal_key = key
        trigger = (
            f"SUPER_EMA BUY CE: EMA{SUPER_EMA_FAST_PERIOD} {ema_fast} crossed above "
            f"EMA{SUPER_EMA_SLOW_PERIOD} {ema_slow}, Supertrend GREEN {st}, NIFTY {round(price, 2)}"
        )
        return "BUY CE", trigger

    if bearish_cross and st_dir == "RED" and price < ema_fast and price < ema_slow and price < st:
        key = "PE_" + signal_key
        if last_super_ema_signal_key == key:
            return None, None
        last_super_ema_signal_key = key
        trigger = (
            f"SUPER_EMA BUY PE: EMA{SUPER_EMA_FAST_PERIOD} {ema_fast} crossed below "
            f"EMA{SUPER_EMA_SLOW_PERIOD} {ema_slow}, Supertrend RED {st}, NIFTY {round(price, 2)}"
        )
        return "BUY PE", trigger

    return None, None


def update_super_ema_trailing_sl(trade, current_price):
    """Update option-premium based trailing SL for Strategy-6."""
    entry_price = float(trade["entry_price"])
    current_price = float(current_price)
    profit_percent = round(((current_price - entry_price) / entry_price) * 100, 2)

    highest_price = float(trade.get("highest_price", entry_price))
    if current_price > highest_price:
        highest_price = current_price
        trade["highest_price"] = round(highest_price, 2)

    current_sl = float(trade.get("trailing_sl_price", entry_price * (1 - SUPER_EMA_OPTION_SL_PERCENT / 100)))
    new_sl = current_sl

    if profit_percent >= SUPER_EMA_BREAKEVEN_PERCENT:
        new_sl = max(new_sl, entry_price)
    if profit_percent >= SUPER_EMA_LOCK1_PERCENT:
        new_sl = max(new_sl, entry_price * (1 + SUPER_EMA_LOCK1_SL_PERCENT / 100))
    if profit_percent >= SUPER_EMA_LOCK2_PERCENT:
        new_sl = max(new_sl, entry_price * (1 + SUPER_EMA_LOCK2_SL_PERCENT / 100))
    if profit_percent >= SUPER_EMA_TRAIL_START_PERCENT:
        new_sl = max(new_sl, highest_price * (1 - SUPER_EMA_TRAIL_GAP_PERCENT / 100))

    new_sl = round(new_sl, 2)
    if new_sl != round(current_sl, 2):
        trade["trailing_sl_price"] = new_sl
        trade["trailing_profit_percent"] = profit_percent
        save_active_trades()

    return profit_percent, new_sl


def check_super_ema_exit(trade, current_price, nifty):
    if current_price is None:
        return None, None

    profit_percent, trailing_sl = update_super_ema_trailing_sl(trade, current_price)

    if float(current_price) <= float(trailing_sl):
        return "SUPER_EMA TRAILING SL HIT", f"Option {round(float(current_price),2)} <= Trail SL {trailing_sl}, P/L {profit_percent}%"

    state = get_latest_super_ema_state()
    if state is None:
        return None, None

    ema_fast = state["ema_fast"]
    ema_slow = state["ema_slow"]
    st = state["supertrend"]
    st_dir = state["supertrend_direction"]
    price = float(nifty)

    if trade["trade_type"] == "BUY CE":
        if ema_fast < ema_slow:
            return "SUPER_EMA CE EMA REVERSAL EXIT", f"EMA{SUPER_EMA_FAST_PERIOD} {ema_fast} below EMA{SUPER_EMA_SLOW_PERIOD} {ema_slow}"
        if st_dir == "RED" or price < st:
            return "SUPER_EMA CE SUPERTREND EXIT", f"Supertrend {st_dir}, ST {st}, NIFTY {round(price,2)}"

    elif trade["trade_type"] == "BUY PE":
        if ema_fast > ema_slow:
            return "SUPER_EMA PE EMA REVERSAL EXIT", f"EMA{SUPER_EMA_FAST_PERIOD} {ema_fast} above EMA{SUPER_EMA_SLOW_PERIOD} {ema_slow}"
        if st_dir == "GREEN" or price > st:
            return "SUPER_EMA PE SUPERTREND EXIT", f"Supertrend {st_dir}, ST {st}, NIFTY {round(price,2)}"

    return None, None

# ==========================================================
# OPTION MASTER HELPERS
# ==========================================================
def load_symbol_master():
    try:
        url = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
        return requests.get(url, headers={"Cache-Control": "no-cache"}, timeout=20).json()
    except Exception as e:
        print("SYMBOL MASTER ERROR:", e)
        send_telegram(f"❌ SYMBOL MASTER ERROR\n{e}")
        return None


def get_option_context(symbols, now, nifty):
    opts = [
        s for s in symbols
        if s.get("exch_seg") == "NFO"
        and "NIFTY" in s.get("symbol", "")
        and (s.get("symbol", "").endswith("CE") or s.get("symbol", "").endswith("PE"))
    ]

    expiries = list(set([o["expiry"] for o in opts]))
    exp_list = []
    today = now.date()

    for e in expiries:
        try:
            d = datetime.strptime(e, "%d%b%Y").date()
            if d >= today:
                exp_list.append((e, d))
        except Exception:
            pass

    exp_list = sorted(exp_list, key=lambda x: x[1])
    if not exp_list:
        return None

    expiry = exp_list[0][0]
    filtered = [o for o in opts if o["expiry"] == expiry]
    atm = round(nifty / 50) * 50

    strike_map = {o["token"]: int(float(o["strike"]) / 100) for o in filtered}
    tokens = [o["token"] for o in filtered if atm - 500 <= strike_map[o["token"]] <= atm + 500]

    atm_ce_symbol = None
    atm_ce_token = None
    atm_pe_symbol = None
    atm_pe_token = None
    next_otm_ce_symbol = None
    next_otm_ce_token = None
    next_otm_pe_symbol = None
    next_otm_pe_token = None

    next_otm_ce_strike = atm + 50
    next_otm_pe_strike = atm - 50

    option_by_token = {}
    token_by_strike_type = {}

    for o in filtered:
        strike = int(float(o["strike"]) / 100)
        opt_type = "CE" if o["symbol"].endswith("CE") else "PE" if o["symbol"].endswith("PE") else ""
        if opt_type:
            option_by_token[o["token"]] = {
                "symbol": o["symbol"],
                "token": o["token"],
                "strike": strike,
                "type": opt_type,
                "oi": 0,
                "volume": 0,
                "ltp": 0
            }
            token_by_strike_type[(strike, opt_type)] = o["token"]

        if strike == atm and o["symbol"].endswith("CE"):
            atm_ce_symbol = o["symbol"]
            atm_ce_token = o["token"]
        if strike == atm and o["symbol"].endswith("PE"):
            atm_pe_symbol = o["symbol"]
            atm_pe_token = o["token"]
        if strike == next_otm_ce_strike and o["symbol"].endswith("CE"):
            next_otm_ce_symbol = o["symbol"]
            next_otm_ce_token = o["token"]
        if strike == next_otm_pe_strike and o["symbol"].endswith("PE"):
            next_otm_pe_symbol = o["symbol"]
            next_otm_pe_token = o["token"]

    fetched = []
    for i in range(0, len(tokens), 50):
        try:
            data = smartApi.getMarketData("FULL", {"NFO": tokens[i:i + 50]})
            if data and data.get("status"):
                fetched += data["data"].get("fetched", [])
        except Exception as e:
            print("Market Data Fetch Error:", e)
            log_error(str(e))

    total_ce = 0
    total_pe = 0
    atm_ce = 0
    atm_pe = 0
    strike_data = {}

    for item in fetched:
        sym = item.get("tradingSymbol", "")
        oi = item.get("opnInterest", 0) or 0
        volume = item.get("tradeVolume", item.get("volume", 0)) or 0
        ltp = item.get("ltp", item.get("lastPrice", 0)) or 0
        tk = item.get("symbolToken")
        strike = strike_map.get(tk)
        if tk in option_by_token:
            option_by_token[tk]["oi"] = int(oi)
            try:
                option_by_token[tk]["volume"] = int(volume)
            except Exception:
                option_by_token[tk]["volume"] = 0
            try:
                option_by_token[tk]["ltp"] = float(ltp)
            except Exception:
                option_by_token[tk]["ltp"] = 0
        if strike is None:
            continue

        if strike not in strike_data:
            strike_data[strike] = {"ce": 0, "pe": 0}

        if sym.endswith("CE"):
            total_ce += oi
            strike_data[strike]["ce"] += oi
            if strike == atm:
                atm_ce = oi
        elif sym.endswith("PE"):
            total_pe += oi
            strike_data[strike]["pe"] += oi
            if strike == atm:
                atm_pe = oi

    pcr = total_pe / total_ce if total_ce else 0
    atm_pcr = atm_pe / atm_ce if atm_ce else 0

    if strike_data:
        max_pain = min(
            strike_data,
            key=lambda x: sum(
                (x - k) * v["ce"] if x > k else (k - x) * v["pe"]
                for k, v in strike_data.items()
            )
        )
    else:
        max_pain = 0

    return {
        "expiry": expiry,
        "atm": atm,
        "pcr": pcr,
        "atm_pcr": atm_pcr,
        "max_pain": max_pain,
        "atm_ce_symbol": atm_ce_symbol,
        "atm_ce_token": atm_ce_token,
        "atm_pe_symbol": atm_pe_symbol,
        "atm_pe_token": atm_pe_token,
        "next_otm_ce_symbol": next_otm_ce_symbol,
        "next_otm_ce_token": next_otm_ce_token,
        "next_otm_pe_symbol": next_otm_pe_symbol,
        "next_otm_pe_token": next_otm_pe_token,
        "next_otm_ce_strike": next_otm_ce_strike,
        "next_otm_pe_strike": next_otm_pe_strike,
        "option_by_token": option_by_token,
        "token_by_strike_type": token_by_strike_type
    }


# ==========================================================
# GAMMA BLAST EXPIRY STRATEGY HELPERS - STRATEGY 7
# ==========================================================
def parse_expiry_date(expiry_text):
    try:
        return datetime.strptime(expiry_text, "%d%b%Y").date()
    except Exception:
        return None


def is_gamma_expiry_day(now, context):
    expiry_date = parse_expiry_date(context.get("expiry", ""))
    return expiry_date is not None and expiry_date == now.date()


def gamma_entry_time_ok(now):
    start_ok = (now.hour > GAMMA_ENTRY_START_HOUR) or (
        now.hour == GAMMA_ENTRY_START_HOUR and now.minute >= GAMMA_ENTRY_START_MINUTE
    )
    end_ok = (now.hour < GAMMA_ENTRY_END_HOUR) or (
        now.hour == GAMMA_ENTRY_END_HOUR and now.minute <= GAMMA_ENTRY_END_MINUTE
    )
    return start_ok and end_ok


def gamma_force_exit_time(now):
    return (now.hour > GAMMA_FORCE_EXIT_HOUR) or (
        now.hour == GAMMA_FORCE_EXIT_HOUR and now.minute >= GAMMA_FORCE_EXIT_MINUTE
    )


def gamma_cooldown_ok(now):
    if last_gamma_entry_time is None:
        return True
    try:
        return (now - last_gamma_entry_time).total_seconds() >= GAMMA_COOLDOWN_SECONDS
    except Exception:
        return True


def percent_change(current, previous):
    try:
        current = float(current)
        previous = float(previous)
        if previous == 0:
            return 0.0
        return round(((current - previous) / previous) * 100, 2)
    except Exception:
        return 0.0


def get_avg_recent_candle_range(count=5):
    try:
        if len(nifty_candles) < count:
            return None
        recent = nifty_candles[-count:]
        ranges = [abs(float(c["high"]) - float(c["low"])) for c in recent]
        return sum(ranges) / len(ranges)
    except Exception:
        return None


def build_gamma_snapshot(context, nifty, call_price, put_price):
    option_by_token = context.get("option_by_token", {})
    atm_ce_token = context.get("atm_ce_token")
    atm_pe_token = context.get("atm_pe_token")
    otm_ce_token = context.get("next_otm_ce_token")
    otm_pe_token = context.get("next_otm_pe_token")

    atm_ce = option_by_token.get(atm_ce_token, {})
    atm_pe = option_by_token.get(atm_pe_token, {})
    otm_ce = option_by_token.get(otm_ce_token, {})
    otm_pe = option_by_token.get(otm_pe_token, {})

    return {
        "time": ist_now().strftime("%Y-%m-%d %H:%M:%S"),
        "nifty": round(float(nifty), 2),
        "atm": context.get("atm"),
        "expiry": context.get("expiry"),
        "atm_ce_oi": int(atm_ce.get("oi", 0) or 0),
        "atm_pe_oi": int(atm_pe.get("oi", 0) or 0),
        "otm_ce_oi": int(otm_ce.get("oi", 0) or 0),
        "otm_pe_oi": int(otm_pe.get("oi", 0) or 0),
        "otm_ce_price": float(call_price or otm_ce.get("ltp", 0) or 0),
        "otm_pe_price": float(put_price or otm_pe.get("ltp", 0) or 0),
        "otm_ce_symbol": context.get("next_otm_ce_symbol"),
        "otm_ce_token": context.get("next_otm_ce_token"),
        "otm_pe_symbol": context.get("next_otm_pe_symbol"),
        "otm_pe_token": context.get("next_otm_pe_token")
    }


def update_gamma_snapshot(context, nifty, otm_call_price, otm_put_price):
    global gamma_snapshot
    gamma_snapshot = build_gamma_snapshot(context, nifty, otm_call_price, otm_put_price)


def get_gamma_signal(context, nifty, otm_call_price, otm_put_price, now):
    """
    Pre-Gamma Blast logic:
    CE setup requires NIFTY rising + Call OI unwinding + next OTM CE premium velocity.
    PE setup requires NIFTY falling + Put OI unwinding + next OTM PE premium velocity.
    Entry is NEXT OTM strike only.
    """
    global gamma_snapshot, last_gamma_signal_key

    if not GAMMA_BLAST_ENABLED:
        return None, None, None, None

    if not is_gamma_expiry_day(now, context):
        return None, None, None, None

    if not gamma_entry_time_ok(now):
        return None, None, None, None

    if gamma_snapshot is None:
        update_gamma_snapshot(context, nifty, otm_call_price, otm_put_price)
        return None, None, None, None

    if current_candle is None or len(nifty_candles) < 5:
        return None, None, None, None

    snap = gamma_snapshot
    option_by_token = context.get("option_by_token", {})
    atm_ce = option_by_token.get(context.get("atm_ce_token"), {})
    atm_pe = option_by_token.get(context.get("atm_pe_token"), {})
    otm_ce = option_by_token.get(context.get("next_otm_ce_token"), {})
    otm_pe = option_by_token.get(context.get("next_otm_pe_token"), {})

    current_atm_ce_oi = int(atm_ce.get("oi", 0) or 0)
    current_atm_pe_oi = int(atm_pe.get("oi", 0) or 0)
    current_otm_ce_oi = int(otm_ce.get("oi", 0) or 0)
    current_otm_pe_oi = int(otm_pe.get("oi", 0) or 0)

    ce_oi_change = percent_change(current_atm_ce_oi, snap.get("atm_ce_oi", 0))
    pe_oi_change = percent_change(current_atm_pe_oi, snap.get("atm_pe_oi", 0))
    otm_ce_oi_change = percent_change(current_otm_ce_oi, snap.get("otm_ce_oi", 0))
    otm_pe_oi_change = percent_change(current_otm_pe_oi, snap.get("otm_pe_oi", 0))
    nifty_move = round(float(nifty) - float(snap.get("nifty", nifty)), 2)
    ce_premium_change = percent_change(otm_call_price, snap.get("otm_ce_price", 0))
    pe_premium_change = percent_change(otm_put_price, snap.get("otm_pe_price", 0))

    candle_open = float(current_candle["open"])
    candle_close = float(nifty)
    candle_body = abs(candle_close - candle_open)
    avg_range = get_avg_recent_candle_range(5)
    candle_range = abs(float(current_candle["high"]) - float(current_candle["low"]))
    candle_expanding = avg_range is not None and candle_range >= (avg_range * GAMMA_MIN_CANDLE_EXPANSION_MULTIPLIER)

    above_vwap = current_vwap is not None and float(nifty) > float(current_vwap)
    below_vwap = current_vwap is not None and float(nifty) < float(current_vwap)

    bullish_candle = candle_close > candle_open and candle_body >= GAMMA_MIN_CANDLE_BODY_POINTS
    bearish_candle = candle_close < candle_open and candle_body >= GAMMA_MIN_CANDLE_BODY_POINTS

    ce_oi_unwinding = ce_oi_change <= -GAMMA_MIN_OI_UNWIND_PERCENT or otm_ce_oi_change <= -GAMMA_MIN_OI_UNWIND_PERCENT
    pe_oi_unwinding = pe_oi_change <= -GAMMA_MIN_OI_UNWIND_PERCENT or otm_pe_oi_change <= -GAMMA_MIN_OI_UNWIND_PERCENT

    ce_score = 0
    if nifty_move >= GAMMA_MIN_NIFTY_MOVE_POINTS: ce_score += 1
    if ce_oi_unwinding: ce_score += 2
    if pe_oi_change >= GAMMA_MIN_OPPOSITE_OI_ADD_PERCENT: ce_score += 1
    if ce_premium_change >= GAMMA_MIN_PREMIUM_JUMP_PERCENT: ce_score += 1
    if bullish_candle: ce_score += 1
    if candle_expanding: ce_score += 1
    if above_vwap: ce_score += 1

    pe_score = 0
    if nifty_move <= -GAMMA_MIN_NIFTY_MOVE_POINTS: pe_score += 1
    if pe_oi_unwinding: pe_score += 2
    if ce_oi_change >= GAMMA_MIN_OPPOSITE_OI_ADD_PERCENT: pe_score += 1
    if pe_premium_change >= GAMMA_MIN_PREMIUM_JUMP_PERCENT: pe_score += 1
    if bearish_candle: pe_score += 1
    if candle_expanding: pe_score += 1
    if below_vwap: pe_score += 1

    minute_key = current_candle.get("minute", now.strftime("%Y-%m-%d %H:%M"))

    if ce_oi_unwinding and ce_score >= GAMMA_MIN_CONFIRMATION_SCORE and otm_call_price is not None and context.get("next_otm_ce_symbol"):
        key = f"CE_{minute_key}_{context.get('next_otm_ce_symbol')}"
        if last_gamma_signal_key == key:
            return None, None, None, None
        last_gamma_signal_key = key
        trigger = (
            f"GAMMA CE: NIFTY +{nifty_move}, Call OI unwinding {ce_oi_change}% "
            f"(OTM {otm_ce_oi_change}%), PE OI {pe_oi_change}%, "
            f"OTM CE premium {ce_premium_change}%, Score {ce_score}, VWAP {current_vwap}"
        )
        return "BUY CE", context.get("next_otm_ce_symbol"), context.get("next_otm_ce_token"), trigger

    if pe_oi_unwinding and pe_score >= GAMMA_MIN_CONFIRMATION_SCORE and otm_put_price is not None and context.get("next_otm_pe_symbol"):
        key = f"PE_{minute_key}_{context.get('next_otm_pe_symbol')}"
        if last_gamma_signal_key == key:
            return None, None, None, None
        last_gamma_signal_key = key
        trigger = (
            f"GAMMA PE: NIFTY {nifty_move}, Put OI unwinding {pe_oi_change}% "
            f"(OTM {otm_pe_oi_change}%), CE OI {ce_oi_change}%, "
            f"OTM PE premium {pe_premium_change}%, Score {pe_score}, VWAP {current_vwap}"
        )
        return "BUY PE", context.get("next_otm_pe_symbol"), context.get("next_otm_pe_token"), trigger

    return None, None, None, None


def update_gamma_trade_trailing(trade, current_price):
    entry_price = float(trade["entry_price"])
    current_price = float(current_price)

    highest_price = float(trade.get("highest_price", entry_price))
    if current_price > highest_price:
        trade["highest_price"] = round(current_price, 2)
        save_active_trades()

    hard_sl = round(entry_price * (1 - GAMMA_OPTION_HARD_SL_PERCENT / 100), 2)
    return hard_sl


def check_gamma_exit(trade, current_price, nifty, context, now):
    if current_price is None:
        return None, None

    current_price = float(current_price)
    hard_sl = update_gamma_trade_trailing(trade, current_price)
    entry_price = float(trade["entry_price"])
    highest_price = float(trade.get("highest_price", entry_price))

    if gamma_force_exit_time(now):
        return "GAMMA TIME EXIT", f"Force exit after {GAMMA_FORCE_EXIT_HOUR}:{GAMMA_FORCE_EXIT_MINUTE:02d}"

    if current_price <= hard_sl:
        return "GAMMA HARD SL HIT", f"Premium {round(current_price,2)} <= SL {hard_sl}"

    if highest_price > entry_price:
        fall_from_high = percent_change(current_price, highest_price)
        if fall_from_high <= -GAMMA_OPTION_PROFIT_COLLAPSE_PERCENT:
            return "GAMMA PREMIUM COLLAPSE EXIT", f"Premium fell {fall_from_high}% from high {highest_price}"

    # Trail using each completed 1-minute NIFTY candle low/high.
    if len(nifty_candles) >= 1:
        prev = nifty_candles[-1]
        if trade["trade_type"] == "BUY CE":
            prev_low = float(prev["low"])
            if float(nifty) < prev_low:
                return "GAMMA PREVIOUS CANDLE LOW EXIT", f"NIFTY {round(float(nifty),2)} < Previous candle low {round(prev_low,2)}"
        elif trade["trade_type"] == "BUY PE":
            prev_high = float(prev["high"])
            if float(nifty) > prev_high:
                return "GAMMA PREVIOUS CANDLE HIGH EXIT", f"NIFTY {round(float(nifty),2)} > Previous candle high {round(prev_high,2)}"

    # OI reversal exit: after CE buy, if Call OI starts increasing again, writers are returning.
    option_by_token = context.get("option_by_token", {})
    atm_ce = option_by_token.get(context.get("atm_ce_token"), {})
    atm_pe = option_by_token.get(context.get("atm_pe_token"), {})

    entry_ce_oi = float(trade.get("entry_atm_ce_oi", 0) or 0)
    entry_pe_oi = float(trade.get("entry_atm_pe_oi", 0) or 0)
    current_ce_oi = float(atm_ce.get("oi", 0) or 0)
    current_pe_oi = float(atm_pe.get("oi", 0) or 0)

    if trade["trade_type"] == "BUY CE" and entry_ce_oi > 0:
        ce_oi_from_entry = percent_change(current_ce_oi, entry_ce_oi)
        if ce_oi_from_entry >= GAMMA_OI_REVERSAL_PERCENT:
            return "GAMMA CE OI REVERSAL EXIT", f"Call OI increased {ce_oi_from_entry}% from entry"

    if trade["trade_type"] == "BUY PE" and entry_pe_oi > 0:
        pe_oi_from_entry = percent_change(current_pe_oi, entry_pe_oi)
        if pe_oi_from_entry >= GAMMA_OI_REVERSAL_PERCENT:
            return "GAMMA PE OI REVERSAL EXIT", f"Put OI increased {pe_oi_from_entry}% from entry"

    return None, None


# ==========================================================
# EXIT LOGIC FOR ALL STRATEGIES
# ==========================================================
def check_exit_for_trade(strategy_name, trade, call_price, put_price, time_str, nifty, pcr, atm_pcr, max_pain, pcr_change_3min, sample_due, context=None):
    global pcr_ce_decrease_count, pcr_pe_increase_count

    # Use the actual trade symbol for exit price.
    # This is essential because Strategy-7 GAMMA_BLAST buys NEXT OTM, not ATM.
    current_price = safe_ltp("NFO", trade["symbol"], trade["token"])
    if current_price is None:
        current_price = call_price if trade["trade_type"] == "BUY CE" else put_price
    if current_price is None:
        return False

    points = round(current_price - trade["entry_price"], 2)
    exit_reason = None
    exit_trigger = None

    # Gamma Blast has expiry-specific OTM trailing and OI reversal exit.
    if strategy_name == GAMMA_BLAST_NAME:
        gamma_context = context if context is not None else {}
        exit_reason, exit_trigger = check_gamma_exit(trade, current_price, nifty, gamma_context, ist_now())

    # ADX has separate percentage based exit, so PCR and SMC remain unchanged.
    elif strategy_name == "ADX":
        exit_reason, exit_trigger = check_adx_exit(trade, current_price, nifty)

    # Strategy-6 Supertrend + EMA has separate percentage trailing exit.
    elif strategy_name == SUPER_EMA_NAME:
        exit_reason, exit_trigger = check_super_ema_exit(trade, current_price, nifty)

    # Common SL / Target for PCR and SMC
    elif points <= -STOPLOSS_POINTS:
        exit_reason = "STOPLOSS HIT"
        exit_trigger = f"OPTION POINTS {points}"
    elif points >= TARGET_POINTS:
        exit_reason = "TARGET HIT"
        exit_trigger = f"OPTION POINTS {points}"

    # Extra RSI + Stochastic + EMA exit only for Strategy-4
    elif strategy_name == RSI_STOCH_EMA_NAME:
        exit_reason, exit_trigger = check_rsi_stoch_ema_exit(trade, nifty)

    # Extra PCR reversal exit only for PCR strategy
    elif strategy_name == "PCR" and sample_due:
        if trade["trade_type"] == "BUY CE":
            if pcr_change_3min <= -EXIT_PCR_STRONG_REVERSAL:
                exit_reason = "CE EXIT - PCR STRONG DECREASE"
                exit_trigger = f"PCR CHANGE {pcr_change_3min}"
            elif pcr_change_3min < 0:
                pcr_ce_decrease_count += 1
                exit_trigger = f"PCR DECREASE COUNT {pcr_ce_decrease_count}, CHANGE {pcr_change_3min}"
                if pcr_ce_decrease_count >= 2:
                    exit_reason = "CE EXIT - PCR DECREASED TWICE"
            else:
                pcr_ce_decrease_count = 0

        elif trade["trade_type"] == "BUY PE":
            if pcr_change_3min >= EXIT_PCR_STRONG_REVERSAL:
                exit_reason = "PE EXIT - PCR STRONG INCREASE"
                exit_trigger = f"PCR CHANGE {pcr_change_3min}"
            elif pcr_change_3min > 0:
                pcr_pe_increase_count += 1
                exit_trigger = f"PCR INCREASE COUNT {pcr_pe_increase_count}, CHANGE {pcr_change_3min}"
                if pcr_pe_increase_count >= 2:
                    exit_reason = "PE EXIT - PCR INCREASED TWICE"
            else:
                pcr_pe_increase_count = 0

    if not exit_reason:
        return False

    save_paper_trade(
        trade, time_str, round(current_price, 2), exit_reason, exit_trigger,
        round(nifty, 2), round(pcr, 4), round(atm_pcr, 4), max_pain,
        current_vwap if current_vwap is not None else ""
    )
    save_google_trade(
        trade, time_str, round(current_price, 2), exit_reason, exit_trigger,
        round(nifty, 2), round(pcr, 4), round(atm_pcr, 4), max_pain,
        current_vwap if current_vwap is not None else ""
    )

    send_telegram(
        f"🚪 PAPER TRADE EXIT\n"
        f"Strategy: {strategy_name}\n"
        f"Trade ID: {trade.get('trade_id', '')}\n"
        f"Type: {trade['trade_type']}\n"
        f"Quantity: {trade.get('quantity', NIFTY_LOT_QTY)}\n"
        f"Symbol: {trade['symbol']}\n"
        f"Entry: {trade['entry_price']}\n"
        f"Exit: {round(current_price, 2)}\n"
        f"Points: {points}\n"
        f"Reason: {exit_reason}\n"
        f"Trigger: {exit_trigger}\n"
        f"Time: {time_str}"
    )

    remove_open_trade(strategy_name)
    if strategy_name == "PCR":
        pcr_ce_decrease_count = 0
        pcr_pe_increase_count = 0
    return True

# ==========================================================
# ENTRY HELPERS
# ==========================================================
def create_trade(strategy_name, trade_type, symbol, token, price, time_str, nifty, pcr, atm_pcr, max_pain, pcr_change, atm_pcr_change, entry_trigger):
    trade = {
        "trade_id": generate_trade_id(strategy_name),
        "strategy_name": strategy_name,
        "entry_time": time_str,
        "trade_type": trade_type,
        "quantity": NIFTY_LOT_QTY,
        "symbol": symbol,
        "token": token,
        "entry_price": round(price, 2),
        "nifty_entry": round(nifty, 2),
        "pcr_entry": round(pcr, 4),
        "atm_pcr_entry": round(atm_pcr, 4),
        "max_pain_entry": max_pain,
        "pcr_change_entry": pcr_change,
        "atm_pcr_change_entry": atm_pcr_change,
        "vwap_entry": current_vwap if current_vwap is not None else "",
        "entry_trigger": entry_trigger
    }

    if strategy_name == SUPER_EMA_NAME:
        trade["highest_price"] = round(price, 2)
        trade["trailing_sl_price"] = round(price * (1 - SUPER_EMA_OPTION_SL_PERCENT / 100), 2)
        trade["trailing_profit_percent"] = 0

    if strategy_name == GAMMA_BLAST_NAME:
        trade["quantity"] = GAMMA_BLAST_QTY
        trade["highest_price"] = round(price, 2)
        trade["trailing_sl_price"] = round(price * (1 - GAMMA_OPTION_HARD_SL_PERCENT / 100), 2)

    return trade


def enter_trade(strategy_name, trade):
    add_open_trade(strategy_name, trade)
    emoji = "🟢" if trade["trade_type"] == "BUY CE" else "🔴"
    send_telegram(
        f"{emoji} PAPER ENTRY ALERT\n"
        f"Strategy: {strategy_name}\n"
        f"Trade ID: {trade.get('trade_id', '')}\n"
        f"Type: {trade['trade_type']}\n"
        f"Quantity: {trade.get('quantity', NIFTY_LOT_QTY)}\n"
        f"Symbol: {trade['symbol']}\n"
        f"Entry Price: {trade['entry_price']}\n"
        f"NIFTY: {trade.get('nifty_entry')}\n"
        f"PCR: {trade.get('pcr_entry')}\n"
        f"ATM PCR: {trade.get('atm_pcr_entry')}\n"
        f"VWAP: {trade.get('vwap_entry')}\n"
        f"Max Pain: {trade.get('max_pain_entry')}\n"
        f"Reason: {trade.get('entry_trigger')}\n"
        f"Time: {trade.get('entry_time')}"
    )

# ==========================================================
# START BOT
# ==========================================================
init_paper_file()
init_google_sheet()
load_active_trades()

if not login():
    exit()

symbols = load_symbol_master()
if symbols is None:
    exit()

# ==========================================================
# MAIN LOOP
# ==========================================================
while True:
    try:
        now = ist_now()
        time_str = now.strftime("%Y-%m-%d %H:%M:%S")
        mode = market_mode(now)

        # Heartbeat every 2 hours
        if now.hour % 2 == 0 and last_heartbeat_hour != now.hour:
            send_telegram(f"✅ BOT RUNNING HEALTHY\nTime: {time_str}\nOpen Trades: {len(open_trades)}")
            last_heartbeat_hour = now.hour

        nifty = safe_ltp("NSE", "NIFTY", "26000")
        if nifty is None:
            print("NIFTY FETCH FAILED")
            time.sleep(5)
            continue

        update_vwap_and_candle(nifty, now)

        context = get_option_context(symbols, now, nifty)
        if context is None:
            print("NO VALID OPTION CONTEXT")
            time.sleep(SLEEP_SECONDS)
            continue

        pcr = context["pcr"]
        atm_pcr = context["atm_pcr"]
        max_pain = context["max_pain"]
        atm_ce_symbol = context["atm_ce_symbol"]
        atm_ce_token = context["atm_ce_token"]
        atm_pe_symbol = context["atm_pe_symbol"]
        atm_pe_token = context["atm_pe_token"]
        next_otm_ce_symbol = context["next_otm_ce_symbol"]
        next_otm_ce_token = context["next_otm_ce_token"]
        next_otm_pe_symbol = context["next_otm_pe_symbol"]
        next_otm_pe_token = context["next_otm_pe_token"]

        # 3-minute PCR sample change
        sample_due = False
        pcr_change_3min = 0
        atm_pcr_change_3min = 0

        if sample_pcr is None or sample_atm_pcr is None or last_pcr_sample_time is None:
            sample_pcr = pcr
            sample_atm_pcr = atm_pcr
            last_pcr_sample_time = now
            print("PCR sample initialized")
        elif (now - last_pcr_sample_time).total_seconds() >= PCR_SAMPLE_SECONDS:
            sample_due = True
            pcr_change_3min = round(pcr - sample_pcr, 4)
            atm_pcr_change_3min = round(atm_pcr - sample_atm_pcr, 4)

        print("\n============================")
        print("Time:", time_str)
        print("Mode:", mode)
        print("============================")
        print("NIFTY:", round(nifty, 2))
        print("PCR:", round(pcr, 4), "| ATM PCR:", round(atm_pcr, 4))
        print("3 Min PCR Change:", pcr_change_3min, "| 3 Min ATM PCR Change:", atm_pcr_change_3min)
        print("Max Pain:", max_pain)
        print("VWAP Approx:", current_vwap, "| Open Trades:", list(open_trades.keys()))
        adx_state = get_latest_adx_state()
        if adx_state:
            print("ADX:", adx_state["adx"], "| +DI:", adx_state["plus_di"], "| -DI:", adx_state["minus_di"])
        else:
            print("ADX: collecting candles")

        rsi_stoch_state = get_latest_rsi_stoch_ema_state()
        if rsi_stoch_state:
            print(
                "RSI_STOCH_EMA:",
                "EMA25", rsi_stoch_state["ema25"],
                "EMA75", rsi_stoch_state["ema75"],
                "EMA140", rsi_stoch_state["ema140"],
                "RSI", rsi_stoch_state["rsi"],
                "K", rsi_stoch_state["stoch_k"],
                "D", rsi_stoch_state["stoch_d"]
            )
        else:
            print("RSI_STOCH_EMA: collecting candles")

        ms_state = get_market_structure_state()
        if ms_state:
            print(
                "MARKET_STRUCTURE:",
                "Swing High", ms_state["last_swing_high"],
                "Swing Low", ms_state["last_swing_low"],
                "Fib Points", ms_state["fib_points"]
            )
        else:
            print("MARKET_STRUCTURE: collecting candles")

        super_ema_state = get_latest_super_ema_state()
        if super_ema_state:
            print(
                "SUPER_EMA:",
                "EMA5", super_ema_state["ema_fast"],
                "EMA20", super_ema_state["ema_slow"],
                "ST", super_ema_state["supertrend"],
                "DIR", super_ema_state["supertrend_direction"]
            )
        else:
            print("SUPER_EMA: collecting candles")

        if GAMMA_BLAST_ENABLED:
            gamma_status = "ON" if is_gamma_expiry_day(now, context) and gamma_entry_time_ok(now) else "WAIT"
            print(
                "GAMMA_BLAST:",
                gamma_status,
                "| Expiry:", context.get("expiry"),
                "| Next OTM CE:", context.get("next_otm_ce_symbol"),
                "| Next OTM PE:", context.get("next_otm_pe_symbol"),
                "| Qty:", GAMMA_BLAST_QTY
            )

        call_price = None
        put_price = None
        if atm_ce_symbol and atm_ce_token:
            call_price = safe_ltp("NFO", atm_ce_symbol, atm_ce_token)
        if atm_pe_symbol and atm_pe_token:
            put_price = safe_ltp("NFO", atm_pe_symbol, atm_pe_token)

        otm_call_price = None
        otm_put_price = None
        if next_otm_ce_symbol and next_otm_ce_token:
            otm_call_price = safe_ltp("NFO", next_otm_ce_symbol, next_otm_ce_token)
        if next_otm_pe_symbol and next_otm_pe_token:
            otm_put_price = safe_ltp("NFO", next_otm_pe_symbol, next_otm_pe_token)

        # ==================================================
        # LIVE MARKET TRADING
        # ==================================================
        if mode == "LIVE MARKET":

            # 1) Exit check for all open strategies
            exited_strategies = set()
            for strategy_name, trade in list(open_trades.items()):
                exited = check_exit_for_trade(
                    strategy_name, trade, call_price, put_price, time_str,
                    nifty, pcr, atm_pcr, max_pain, pcr_change_3min, sample_due, context
                )
                if exited:
                    exited_strategies.add(strategy_name)

            # 2) Strategy-1 PCR entry
            if "PCR" not in open_trades and "PCR" not in exited_strategies and sample_due:
                ce_by_atm = atm_pcr_change_3min >= ENTRY_ATM_PCR_CHANGE
                ce_by_pcr = pcr_change_3min >= ENTRY_PCR_CHANGE
                pe_by_atm = atm_pcr_change_3min <= -ENTRY_ATM_PCR_CHANGE
                pe_by_pcr = pcr_change_3min <= -ENTRY_PCR_CHANGE

                if (ce_by_atm or ce_by_pcr) and call_price is not None:
                    if ce_by_atm and ce_by_pcr:
                        entry_trigger = f"PCR CE OR BOTH: ATM PCR +{atm_pcr_change_3min}, PCR +{pcr_change_3min}"
                    elif ce_by_atm:
                        entry_trigger = f"PCR CE OR: ATM PCR +{atm_pcr_change_3min}"
                    else:
                        entry_trigger = f"PCR CE OR: PCR +{pcr_change_3min}"

                    trade = create_trade(
                        "PCR", "BUY CE", atm_ce_symbol, atm_ce_token, call_price, time_str,
                        nifty, pcr, atm_pcr, max_pain, pcr_change_3min, atm_pcr_change_3min, entry_trigger
                    )
                    enter_trade("PCR", trade)

                elif (pe_by_atm or pe_by_pcr) and put_price is not None:
                    if pe_by_atm and pe_by_pcr:
                        entry_trigger = f"PCR PE OR BOTH: ATM PCR {atm_pcr_change_3min}, PCR {pcr_change_3min}"
                    elif pe_by_atm:
                        entry_trigger = f"PCR PE OR: ATM PCR {atm_pcr_change_3min}"
                    else:
                        entry_trigger = f"PCR PE OR: PCR {pcr_change_3min}"

                    trade = create_trade(
                        "PCR", "BUY PE", atm_pe_symbol, atm_pe_token, put_price, time_str,
                        nifty, pcr, atm_pcr, max_pain, pcr_change_3min, atm_pcr_change_3min, entry_trigger
                    )
                    enter_trade("PCR", trade)

            # 3) Strategy-2 SMC + VWAP entry
            if "SMC" not in open_trades and "SMC" not in exited_strategies and smc_cooldown_ok(now):
                smc_signal, smc_trigger = get_smc_signal(nifty)

                if smc_signal == "BUY CE" and call_price is not None:
                    trade = create_trade(
                        "SMC", "BUY CE", atm_ce_symbol, atm_ce_token, call_price, time_str,
                        nifty, pcr, atm_pcr, max_pain, pcr_change_3min, atm_pcr_change_3min, smc_trigger
                    )
                    enter_trade("SMC", trade)
                    last_smc_entry_time = now

                elif smc_signal == "BUY PE" and put_price is not None:
                    trade = create_trade(
                        "SMC", "BUY PE", atm_pe_symbol, atm_pe_token, put_price, time_str,
                        nifty, pcr, atm_pcr, max_pain, pcr_change_3min, atm_pcr_change_3min, smc_trigger
                    )
                    enter_trade("SMC", trade)
                    last_smc_entry_time = now

            # 4) Strategy-3 ADX entry
            if "ADX" not in open_trades and "ADX" not in exited_strategies:
                adx_signal, adx_trigger = get_adx_signal(nifty)

                if adx_signal == "BUY CE" and call_price is not None:
                    trade = create_trade(
                        "ADX", "BUY CE", atm_ce_symbol, atm_ce_token, call_price, time_str,
                        nifty, pcr, atm_pcr, max_pain, pcr_change_3min, atm_pcr_change_3min, adx_trigger
                    )
                    enter_trade("ADX", trade)

                elif adx_signal == "BUY PE" and put_price is not None:
                    trade = create_trade(
                        "ADX", "BUY PE", atm_pe_symbol, atm_pe_token, put_price, time_str,
                        nifty, pcr, atm_pcr, max_pain, pcr_change_3min, atm_pcr_change_3min, adx_trigger
                    )
                    enter_trade("ADX", trade)

            # 5) Strategy-4 RSI + Stochastic + EMA entry
            if RSI_STOCH_EMA_NAME not in open_trades and RSI_STOCH_EMA_NAME not in exited_strategies:
                rsi_stoch_signal, rsi_stoch_trigger = get_rsi_stoch_ema_signal(nifty, now)

                if rsi_stoch_signal == "BUY CE" and call_price is not None:
                    trade = create_trade(
                        RSI_STOCH_EMA_NAME, "BUY CE", atm_ce_symbol, atm_ce_token, call_price, time_str,
                        nifty, pcr, atm_pcr, max_pain, pcr_change_3min, atm_pcr_change_3min, rsi_stoch_trigger
                    )
                    enter_trade(RSI_STOCH_EMA_NAME, trade)

                elif rsi_stoch_signal == "BUY PE" and put_price is not None:
                    trade = create_trade(
                        RSI_STOCH_EMA_NAME, "BUY PE", atm_pe_symbol, atm_pe_token, put_price, time_str,
                        nifty, pcr, atm_pcr, max_pain, pcr_change_3min, atm_pcr_change_3min, rsi_stoch_trigger
                    )
                    enter_trade(RSI_STOCH_EMA_NAME, trade)

            # 6) Strategy-5 Pure Market Structure entry
            if MARKET_STRUCTURE_NAME not in open_trades and MARKET_STRUCTURE_NAME not in exited_strategies and market_structure_cooldown_ok(now):
                ms_signal, ms_trigger = get_market_structure_signal(nifty)

                if ms_signal == "BUY CE" and call_price is not None:
                    trade = create_trade(
                        MARKET_STRUCTURE_NAME, "BUY CE", atm_ce_symbol, atm_ce_token, call_price, time_str,
                        nifty, pcr, atm_pcr, max_pain, pcr_change_3min, atm_pcr_change_3min, ms_trigger
                    )
                    enter_trade(MARKET_STRUCTURE_NAME, trade)
                    last_market_structure_entry_time = now

                elif ms_signal == "BUY PE" and put_price is not None:
                    trade = create_trade(
                        MARKET_STRUCTURE_NAME, "BUY PE", atm_pe_symbol, atm_pe_token, put_price, time_str,
                        nifty, pcr, atm_pcr, max_pain, pcr_change_3min, atm_pcr_change_3min, ms_trigger
                    )
                    enter_trade(MARKET_STRUCTURE_NAME, trade)
                    last_market_structure_entry_time = now

            # 7) Strategy-6 Supertrend + EMA Crossover entry
            if SUPER_EMA_NAME not in open_trades and SUPER_EMA_NAME not in exited_strategies and super_ema_cooldown_ok(now):
                super_signal, super_trigger = get_super_ema_signal(nifty)

                if super_signal == "BUY CE" and call_price is not None:
                    trade = create_trade(
                        SUPER_EMA_NAME, "BUY CE", atm_ce_symbol, atm_ce_token, call_price, time_str,
                        nifty, pcr, atm_pcr, max_pain, pcr_change_3min, atm_pcr_change_3min, super_trigger
                    )
                    enter_trade(SUPER_EMA_NAME, trade)
                    last_super_ema_entry_time = now

                elif super_signal == "BUY PE" and put_price is not None:
                    trade = create_trade(
                        SUPER_EMA_NAME, "BUY PE", atm_pe_symbol, atm_pe_token, put_price, time_str,
                        nifty, pcr, atm_pcr, max_pain, pcr_change_3min, atm_pcr_change_3min, super_trigger
                    )
                    enter_trade(SUPER_EMA_NAME, trade)
                    last_super_ema_entry_time = now


            # 8) Strategy-7 Gamma Blast Expiry entry
            if (
                GAMMA_BLAST_NAME not in open_trades
                and GAMMA_BLAST_NAME not in exited_strategies
                and gamma_cooldown_ok(now)
            ):
                gamma_signal, gamma_symbol, gamma_token, gamma_trigger = get_gamma_signal(
                    context, nifty, otm_call_price, otm_put_price, now
                )

                if gamma_signal == "BUY CE" and gamma_symbol and gamma_token and otm_call_price is not None:
                    trade = create_trade(
                        GAMMA_BLAST_NAME, "BUY CE", gamma_symbol, gamma_token, otm_call_price, time_str,
                        nifty, pcr, atm_pcr, max_pain, pcr_change_3min, atm_pcr_change_3min, gamma_trigger
                    )
                    option_by_token = context.get("option_by_token", {})
                    trade["entry_atm_ce_oi"] = option_by_token.get(context.get("atm_ce_token"), {}).get("oi", 0)
                    trade["entry_atm_pe_oi"] = option_by_token.get(context.get("atm_pe_token"), {}).get("oi", 0)
                    trade["entry_otm_ce_oi"] = option_by_token.get(context.get("next_otm_ce_token"), {}).get("oi", 0)
                    enter_trade(GAMMA_BLAST_NAME, trade)
                    last_gamma_entry_time = now

                elif gamma_signal == "BUY PE" and gamma_symbol and gamma_token and otm_put_price is not None:
                    trade = create_trade(
                        GAMMA_BLAST_NAME, "BUY PE", gamma_symbol, gamma_token, otm_put_price, time_str,
                        nifty, pcr, atm_pcr, max_pain, pcr_change_3min, atm_pcr_change_3min, gamma_trigger
                    )
                    option_by_token = context.get("option_by_token", {})
                    trade["entry_atm_ce_oi"] = option_by_token.get(context.get("atm_ce_token"), {}).get("oi", 0)
                    trade["entry_atm_pe_oi"] = option_by_token.get(context.get("atm_pe_token"), {}).get("oi", 0)
                    trade["entry_otm_pe_oi"] = option_by_token.get(context.get("next_otm_pe_token"), {}).get("oi", 0)
                    enter_trade(GAMMA_BLAST_NAME, trade)
                    last_gamma_entry_time = now

            # Update Gamma comparison snapshot after all entry logic.
            update_gamma_snapshot(context, nifty, otm_call_price, otm_put_price)

        # ==================================================
        # FORCE EXIT AFTER MARKET FOR ALL OPEN TRADES
        # ==================================================
        elif mode == "AFTER MARKET" and open_trades:
            for strategy_name, trade in list(open_trades.items()):
                exit_price = safe_ltp("NFO", trade["symbol"], trade["token"])
                if exit_price is None:
                    continue
                points = round(exit_price - trade["entry_price"], 2)

                save_paper_trade(
                    trade, time_str, round(exit_price, 2), "MARKET CLOSED EXIT", "MARKET CLOSED",
                    round(nifty, 2), round(pcr, 4), round(atm_pcr, 4), max_pain,
                    current_vwap if current_vwap is not None else ""
                )
                save_google_trade(
                    trade, time_str, round(exit_price, 2), "MARKET CLOSED EXIT", "MARKET CLOSED",
                    round(nifty, 2), round(pcr, 4), round(atm_pcr, 4), max_pain,
                    current_vwap if current_vwap is not None else ""
                )

                send_telegram(
                    f"🚪 PAPER TRADE EXIT\n"
                    f"Strategy: {strategy_name}\n"
                    f"Trade ID: {trade.get('trade_id', '')}\n"
                    f"Type: {trade['trade_type']}\n"
                    f"Quantity: {trade.get('quantity', NIFTY_LOT_QTY)}\n"
                    f"Symbol: {trade['symbol']}\n"
                    f"Entry: {trade['entry_price']}\n"
                    f"Exit: {round(exit_price, 2)}\n"
                    f"Points: {points}\n"
                    f"Reason: MARKET CLOSED EXIT\n"
                    f"Time: {time_str}"
                )
                remove_open_trade(strategy_name)

        # Update 3-minute PCR sample after logic
        if sample_due:
            sample_pcr = pcr
            sample_atm_pcr = atm_pcr
            last_pcr_sample_time = now

        time.sleep(SLEEP_SECONDS)

    except Exception as e:
        print("MAIN LOOP ERROR:", e)
        log_error(str(e))
        send_telegram(f"❌ MAIN LOOP ERROR\n{e}")
        login()
        time.sleep(5)
