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

# ==========================================================
# CSV / GOOGLE SHEET HEADERS
# ==========================================================
HEADERS = [
    "Trade ID", "Strategy Name", "Entry Time", "Exit Time", "Trade Type", "Symbol", "Token",
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
            send_telegram("🌅 MULTI STRATEGY PAPER SYSTEM STARTED SUCCESSFULLY")
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

    for o in filtered:
        strike = int(float(o["strike"]) / 100)
        if strike == atm and o["symbol"].endswith("CE"):
            atm_ce_symbol = o["symbol"]
            atm_ce_token = o["token"]
        if strike == atm and o["symbol"].endswith("PE"):
            atm_pe_symbol = o["symbol"]
            atm_pe_token = o["token"]

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
        tk = item.get("symbolToken")
        strike = strike_map.get(tk)
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
        "atm_pe_token": atm_pe_token
    }

# ==========================================================
# EXIT LOGIC FOR ALL STRATEGIES
# ==========================================================
def check_exit_for_trade(strategy_name, trade, call_price, put_price, time_str, nifty, pcr, atm_pcr, max_pain, pcr_change_3min, sample_due):
    global pcr_ce_decrease_count, pcr_pe_increase_count

    current_price = call_price if trade["trade_type"] == "BUY CE" else put_price
    if current_price is None:
        return False

    points = round(current_price - trade["entry_price"], 2)
    exit_reason = None
    exit_trigger = None

    # ADX has separate percentage based exit, so PCR and SMC remain unchanged.
    if strategy_name == "ADX":
        exit_reason, exit_trigger = check_adx_exit(trade, current_price, nifty)

    # Common SL / Target for PCR and SMC
    elif points <= -STOPLOSS_POINTS:
        exit_reason = "STOPLOSS HIT"
        exit_trigger = f"OPTION POINTS {points}"
    elif points >= TARGET_POINTS:
        exit_reason = "TARGET HIT"
        exit_trigger = f"OPTION POINTS {points}"

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
    return {
        "trade_id": generate_trade_id(strategy_name),
        "strategy_name": strategy_name,
        "entry_time": time_str,
        "trade_type": trade_type,
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


def enter_trade(strategy_name, trade):
    add_open_trade(strategy_name, trade)
    emoji = "🟢" if trade["trade_type"] == "BUY CE" else "🔴"
    send_telegram(
        f"{emoji} PAPER ENTRY ALERT\n"
        f"Strategy: {strategy_name}\n"
        f"Trade ID: {trade.get('trade_id', '')}\n"
        f"Type: {trade['trade_type']}\n"
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

        call_price = None
        put_price = None
        if atm_ce_symbol and atm_ce_token:
            call_price = safe_ltp("NFO", atm_ce_symbol, atm_ce_token)
        if atm_pe_symbol and atm_pe_token:
            put_price = safe_ltp("NFO", atm_pe_symbol, atm_pe_token)

        # ==================================================
        # LIVE MARKET TRADING
        # ==================================================
        if mode == "LIVE MARKET":

            # 1) Exit check for all open strategies
            exited_strategies = set()
            for strategy_name, trade in list(open_trades.items()):
                exited = check_exit_for_trade(
                    strategy_name, trade, call_price, put_price, time_str,
                    nifty, pcr, atm_pcr, max_pain, pcr_change_3min, sample_due
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
