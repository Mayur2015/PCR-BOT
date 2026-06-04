import base64
import requests
import time
import csv
import os
import json
import math
from datetime import datetime
import pytz
import gspread
from google.oauth2.service_account import Credentials
from SmartApi import SmartConnect
import pyotp

# =========================================================
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
last_heartbeat_time = None
last_relogin_attempt_time = None

# Telegram notification control
TELEGRAM_NOTIFY_STARTUP = os.getenv("TELEGRAM_NOTIFY_STARTUP", "NO").upper() == "YES"
TELEGRAM_NOTIFY_GITHUB = os.getenv("TELEGRAM_NOTIFY_GITHUB", "NO").upper() == "YES"
TELEGRAM_NOTIFY_GOOGLE_CONNECT = os.getenv("TELEGRAM_NOTIFY_GOOGLE_CONNECT", "NO").upper() == "YES"
TELEGRAM_HEARTBEAT_MINUTES = int(os.getenv("TELEGRAM_HEARTBEAT_MINUTES", "60"))
RELOGIN_COOLDOWN_SECONDS = int(os.getenv("RELOGIN_COOLDOWN_SECONDS", "120"))

# ==========================================================
# PCR ML DATA COLLECTION GLOBALS
# ==========================================================
pcr_ml_sheet = None
last_pcr_ml_save_minute = None
last_pcr_ml_alert_hour = -1
previous_pcr_ml_snapshot = None
pcr_ml_snapshots = []
india_vix_token = None
india_vix_symbol = None
last_preopen_login_date = None

# Multiple active trades will be stored here
# Example: open_trades["PCR"] = {...}, open_trades["SMC"] = {...}, open_trades["ADX"] = {...}
open_trades = {}

PAPER_FILE = "paper_trades.csv"
ACTIVE_TRADES_FILE = "active_trades.json"
PCR_ML_FILE = "pcr_ml_history.csv"
CANDLE_HISTORY_FILE = "nifty_candle_history.csv"
MAX_CANDLE_HISTORY = 300

# ==========================================================
# GITHUB CANDLE HISTORY BACKUP / RESTORE
# ==========================================================
# Add these Railway variables:
# GITHUB_TOKEN  = GitHub fine-grained/classic token with Contents Read & Write access
# GITHUB_REPO   = owner/repository-name   example: MayurTank/nifty-paper-trade
# GITHUB_BRANCH = main                  optional, default main
# GITHUB_CANDLE_PATH = nifty_candle_history.csv optional
# GITHUB_PCR_ML_PATH = pcr_ml_history.csv optional
# GITHUB_SYNC_INTERVAL_MINUTES = 30 optional. Default 30 minutes to limit max data loss.
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
# IMPORTANT: Do NOT write backup CSV to the same repo+branch Railway auto-deploys from.
# Use a separate repo or a separate data branch, otherwise every CSV backup commit can restart Railway.
GITHUB_CODE_REPO = os.getenv("GITHUB_REPO")
GITHUB_CODE_BRANCH = os.getenv("GITHUB_BRANCH", "main")
GITHUB_BACKUP_REPO = os.getenv("GITHUB_BACKUP_REPO", GITHUB_CODE_REPO)
GITHUB_BACKUP_BRANCH = os.getenv("GITHUB_BACKUP_BRANCH", os.getenv("GITHUB_DATA_BRANCH", "data-backup"))
GITHUB_CANDLE_PATH = os.getenv("GITHUB_CANDLE_PATH", CANDLE_HISTORY_FILE)
GITHUB_PCR_ML_PATH = os.getenv("GITHUB_PCR_ML_PATH", PCR_ML_FILE)
GITHUB_SYNC_INTERVAL_MINUTES = int(os.getenv("GITHUB_SYNC_INTERVAL_MINUTES", "30"))
GITHUB_SYNC_INTERVAL_SECONDS = GITHUB_SYNC_INTERVAL_MINUTES * 60
last_github_candle_sync_time = None
last_github_pcr_ml_sync_time = None

# ==========================================================
# STRATEGY SETTINGS
# ==========================================================
SLEEP_SECONDS = 60
NIFTY_LOT_QTY = 65

# NSE/BSE trading holidays for Equity and Equity Derivatives - 2026
# Keep this list updated yearly as per official NSE holiday circular.
NSE_TRADING_HOLIDAYS_2026 = {
    "2026-01-26",  # Republic Day
    "2026-03-03",  # Holi
    "2026-03-26",  # Shri Ram Navami
    "2026-03-31",  # Shri Mahavir Jayanti
    "2026-04-03",  # Good Friday
    "2026-04-14",  # Dr. Baba Saheb Ambedkar Jayanti
    "2026-05-01",  # Maharashtra Day
    "2026-05-28",  # Bakri Id
    "2026-06-26",  # Muharram
    "2026-09-14",  # Ganesh Chaturthi
    "2026-10-02",  # Mahatma Gandhi Jayanti
    "2026-10-20",  # Dussehra
    "2026-11-10",  # Diwali Balipratipada
    "2026-11-24",  # Prakash Gurpurb Sri Guru Nanak Dev
    "2026-12-25",  # Christmas
}

# Stale data / holiday safety
STATIC_DATA_CHECK_ENABLED = True
STATIC_DATA_MIN_SAMPLES = 5
STATIC_DATA_MAX_NIFTY_CHANGE_POINTS = 1.0
STATIC_DATA_MAX_OPTION_CHANGE_POINTS = 0.20
STATIC_DATA_MAX_PCR_CHANGE = 0.001
MARKET_DATA_SAMPLES = []

# PCR Strategy settings
PCR_SAMPLE_SECONDS = 60
ENTRY_ATM_PCR_CHANGE = 0.30  # kept for ML/logs; PCR trade entry now uses only PCR change
ENTRY_PCR_CHANGE = 0.25
EXIT_PCR_STRONG_REVERSAL = 0.05  # not used for new PCR exit

# Common option exit settings
STOPLOSS_POINTS = 5
TARGET_POINTS = 8  # kept for old/common strategies; PCR/variant labs use fixed 15 target

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
RSI_STOCH_EMA_NAME = "RSI_STOCH_EMA_A"
RSI_STOCH_EMA_B_NAME = "RSI_STOCH_EMA_B"
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
SUPER_EMA_NAME = "SUPER_EMA_A"
SUPER_EMA_B_NAME = "SUPER_EMA_B"
SUPER_EMA_C_NAME = "SUPER_EMA_C"
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

# Stop Loss Hunting Strategy settings - Strategy 8
# Logic: detect stop-loss sweep/liquidity grab and reversal confirmation.
SL_HUNT_ENABLED = True
SL_HUNT_NAME = "SL_HUNT"
SL_HUNT_LOOKBACK_CANDLES = 10
SL_HUNT_SWEEP_BUFFER_POINTS = 5
SL_HUNT_RECLAIM_BUFFER_POINTS = 2
SL_HUNT_MIN_BODY_POINTS = 4
SL_HUNT_MIN_WICK_RATIO = 1.2
SL_HUNT_COOLDOWN_SECONDS = 900
SL_HUNT_USE_VWAP_FILTER = True
SL_HUNT_USE_PCR_FILTER = True
SL_HUNT_MIN_CANDLES_REQUIRED = 15
SL_HUNT_OPTION_SL_PERCENT = 20
SL_HUNT_TARGET_PERCENT = 30
SL_HUNT_BREAKEVEN_PERCENT = 12

# EMA 9/20 Crossover Scalping - Strategy 9
# Pure 3-minute EMA crossover. No VWAP, no PCR, no SL/target. Exit only on opposite crossover.
EMA9_20_ENABLED = True
EMA9_20_NAME = "EMA9_20_A"
EMA9_20_C_NAME = "EMA9_20_C"
EMA9_20_D_NAME = "EMA9_20_D"
EMA9_20_FAST = 9
EMA9_20_SLOW = 20
EMA9_20_MIN_3MIN_CANDLES = 25
EMA9_20_COOLDOWN_SECONDS = 300

# Opening Range Breakout - Strategy 10
ORB_ENABLED = True
ORB_NAME = "ORB_CLASSIC"
ORB_EMA9_TRAIL_NAME = "ORB_EMA9_TRAIL"
ORB_HYBRID_NAME = "ORB_HYBRID"
ORB_RANGE_MINUTES = 15
ORB_BUFFER_POINTS = 5
ORB_TARGET_MULTIPLIER = 2.0
ORB_ENTRY_END_HOUR = 12
ORB_ENTRY_END_MINUTE = 0

# Bollinger Band Mean Reversion - Strategy 11
BOLLINGER_ENABLED = True
BOLLINGER_NAME = "BOLLINGER_MEAN"
BOLLINGER_PERIOD = 20
BOLLINGER_STD_MULTIPLIER = 2
BOLLINGER_MIN_CANDLES = 25

# MACD Histogram Squeeze - Strategy 12
MACD_SQUEEZE_ENABLED = True
MACD_SQUEEZE_NAME = "MACD_SQUEEZE"
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
MACD_MIN_CANDLES = 40

# VWAP False Break - Strategy 13
VWAP_FALSE_BREAK_ENABLED = True
VWAP_FALSE_BREAK_NAME = "VWAP_FALSE_BREAK"
VWAP_FALSE_BREAK_MIN_BODY_POINTS = 3
VWAP_FALSE_BREAK_MIN_CANDLES = 5
VWAP_FALSE_BREAK_COOLDOWN_SECONDS = 600

# Strategy lab fixed/protection exits
LAB_FIXED_SL_POINTS = 5
LAB_FIXED_TARGET_POINTS = 15
LAB_BREAKEVEN_TRIGGER_POINTS = 10
LAB_EMA_TRAIL_TRIGGER_POINTS = 15
LAB_EMA_TRAIL_PERIOD = 9

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

# Stop Loss Hunt variables
last_sl_hunt_entry_time = None
last_sl_hunt_signal_key = None

# Strategy 9 to 13 variables
last_ema9_20_entry_time = None
last_ema9_20_signal_key = None
last_orb_trade_date = None
last_orb_signal_key = None
last_bollinger_signal_key = None
last_macd_signal_key = None
last_vwap_false_break_entry_time = None
last_vwap_false_break_signal_key = None

last_rsi_stoch_ema_b_signal_key = None
last_ema9_20_c_entry_time = None
last_ema9_20_d_entry_time = None
last_orb_variant_trade_date = None
last_super_ema_b_entry_time = None
last_super_ema_c_entry_time = None

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
# PCR ML GOOGLE SHEET / CSV HEADERS
# ==========================================================
PCR_ML_HEADERS = [
    "TIME", "NIFTY", "INDIA_VIX", "VIX_CHANGE",
    "PCR_OI", "PCR_VOLUME", "ATM_PCR",
    "TOTAL_CE_OI", "TOTAL_PE_OI", "CE_OI_CHANGE", "PE_OI_CHANGE", "OI_DIFFERENCE",
    "CE_VOLUME", "PE_VOLUME", "CE_VOLUME_CHANGE", "PE_VOLUME_CHANGE", "VOLUME_DELTA",
    "MAX_PAIN", "DISTANCE_MAX_PAIN",
    "MAX_CE_OI_STRIKE", "MAX_PE_OI_STRIKE", "CE_STRIKE_SHIFT", "PE_STRIKE_SHIFT",
    "VWAP", "VWAP_DISTANCE",
    "DAYS_TO_EXPIRY",
    "IS_EXPIRY_DAY", "IS_PRE_EXPIRY_DAY", "IS_POST_EXPIRY_DAY", "EXPIRY_TYPE",
    "TIME_BLOCK", "MARKET_DIRECTION",
    "NIFTY_CHANGE_1MIN", "NIFTY_CHANGE_5MIN", "NIFTY_CHANGE_15MIN",
    "ATM_CE_LTP", "ATM_PE_LTP", "ATM_CE_IV", "ATM_PE_IV",
    "ATM_CE_DELTA", "ATM_CE_GAMMA", "ATM_CE_THETA", "ATM_CE_VEGA", "ATM_CE_RHO",
    "ATM_PE_DELTA", "ATM_PE_GAMMA", "ATM_PE_THETA", "ATM_PE_VEGA", "ATM_PE_RHO",
    "FUTURE_MOVE_15MIN", "FUTURE_MOVE_30MIN", "FUTURE_MOVE_60MIN",
    "ML_SIGNAL", "CREATED_AT"
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


def is_trading_holiday(now):
    """Return True when the date is an exchange trading holiday."""
    return now.strftime("%Y-%m-%d") in NSE_TRADING_HOLIDAYS_2026


def market_mode(now):
    if now.weekday() >= 5:
        return "WEEKEND"
    if is_trading_holiday(now):
        return "HOLIDAY"
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
        spreadsheet = client.open_by_key(GOOGLE_SHEET_ID)
        google_sheet = spreadsheet.sheet1

        # Separate tab for PCR Machine Learning data
        global pcr_ml_sheet
        try:
            pcr_ml_sheet = spreadsheet.worksheet("PCR_ML_DATA")

            # Keep PCR_ML_DATA header updated when new ML columns are added.
            # This is safe for old data: old rows remain as-is, new columns are added in header.
            try:
                existing_header = pcr_ml_sheet.row_values(1)
                if existing_header != PCR_ML_HEADERS:
                    pcr_ml_sheet.update("A1", [PCR_ML_HEADERS], value_input_option="USER_ENTERED")
                    print("PCR_ML_DATA header updated with latest ML columns")
            except Exception as header_error:
                print("PCR_ML_DATA header update warning:", header_error)

        except Exception:
            pcr_ml_sheet = spreadsheet.add_worksheet(
                title="PCR_ML_DATA",
                rows="50000",
                cols=str(len(PCR_ML_HEADERS) + 5)
            )
            pcr_ml_sheet.append_row(PCR_ML_HEADERS, value_input_option="USER_ENTERED")

        print("GOOGLE SHEET CONNECTED")
        if TELEGRAM_NOTIFY_GOOGLE_CONNECT:
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
# PCR ML DATA COLLECTION HELPERS - SEPARATE MODULE
# ==========================================================
def init_pcr_ml_file():
    """
    Separate CSV database for PCR ML analysis.
    This does not disturb paper trade file or strategy logic.

    If new ML columns are added later, this function safely upgrades
    the existing CSV header and keeps old rows usable.
    """
    global previous_pcr_ml_snapshot
    try:
        if not os.path.exists(PCR_ML_FILE):
            with open(PCR_ML_FILE, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=PCR_ML_HEADERS)
                writer.writeheader()
            previous_pcr_ml_snapshot = None
            return

        # Safe CSV header migration for added columns.
        with open(PCR_ML_FILE, "r", newline="") as f:
            reader = csv.DictReader(f)
            old_fieldnames = reader.fieldnames or []
            rows = list(reader)

        if old_fieldnames != PCR_ML_HEADERS:
            backup_file = PCR_ML_FILE.replace(".csv", "_backup_before_header_update.csv")
            try:
                if not os.path.exists(backup_file):
                    with open(backup_file, "w", newline="") as bf:
                        backup_writer = csv.DictWriter(bf, fieldnames=old_fieldnames)
                        backup_writer.writeheader()
                        backup_writer.writerows(rows)
            except Exception as backup_error:
                print("PCR ML backup warning:", backup_error)

            with open(PCR_ML_FILE, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=PCR_ML_HEADERS)
                writer.writeheader()
                for r in rows:
                    writer.writerow({h: r.get(h, "") for h in PCR_ML_HEADERS})

            print("PCR ML CSV header updated safely")

        # Load last row after restart so delta calculation can continue.
        with open(PCR_ML_FILE, "r", newline="") as f:
            rows = list(csv.DictReader(f))
            if rows:
                previous_pcr_ml_snapshot = rows[-1]
            else:
                previous_pcr_ml_snapshot = None

    except Exception as e:
        print("PCR ML File Init Error:", e)
        log_error(str(e))

def safe_float(value, default=0.0):
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def norm_pdf(x):
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def black_scholes_price(spot, strike, time_years, rate, volatility, option_type):
    try:
        if spot <= 0 or strike <= 0 or time_years <= 0 or volatility <= 0:
            return 0.0
        d1 = (math.log(spot / strike) + (rate + 0.5 * volatility * volatility) * time_years) / (volatility * math.sqrt(time_years))
        d2 = d1 - volatility * math.sqrt(time_years)
        if option_type == "CE":
            return spot * norm_cdf(d1) - strike * math.exp(-rate * time_years) * norm_cdf(d2)
        return strike * math.exp(-rate * time_years) * norm_cdf(-d2) - spot * norm_cdf(-d1)
    except Exception:
        return 0.0


def implied_volatility(option_price, spot, strike, time_years, rate, option_type):
    try:
        option_price = float(option_price or 0)
        if option_price <= 0 or spot <= 0 or strike <= 0 or time_years <= 0:
            return 0.0
        low = 0.01
        high = 3.00
        for _ in range(60):
            mid = (low + high) / 2
            price = black_scholes_price(spot, strike, time_years, rate, mid, option_type)
            if price > option_price:
                high = mid
            else:
                low = mid
        return round(((low + high) / 2) * 100, 2)
    except Exception:
        return 0.0


def calculate_option_greeks(spot, strike, days_to_expiry, rate, iv_percent, option_type):
    try:
        spot = float(spot)
        strike = float(strike)
        time_years = max(float(days_to_expiry), 1) / 365.0
        volatility = float(iv_percent) / 100.0
        if spot <= 0 or strike <= 0 or volatility <= 0 or time_years <= 0:
            return {"delta": 0, "gamma": 0, "theta": 0, "vega": 0, "rho": 0}

        d1 = (math.log(spot / strike) + (rate + 0.5 * volatility * volatility) * time_years) / (volatility * math.sqrt(time_years))
        d2 = d1 - volatility * math.sqrt(time_years)

        gamma = norm_pdf(d1) / (spot * volatility * math.sqrt(time_years))
        vega = spot * norm_pdf(d1) * math.sqrt(time_years) / 100

        if option_type == "CE":
            delta = norm_cdf(d1)
            theta = (-(spot * norm_pdf(d1) * volatility) / (2 * math.sqrt(time_years)) - rate * strike * math.exp(-rate * time_years) * norm_cdf(d2)) / 365
            rho = strike * time_years * math.exp(-rate * time_years) * norm_cdf(d2) / 100
        else:
            delta = norm_cdf(d1) - 1
            theta = (-(spot * norm_pdf(d1) * volatility) / (2 * math.sqrt(time_years)) + rate * strike * math.exp(-rate * time_years) * norm_cdf(-d2)) / 365
            rho = -strike * time_years * math.exp(-rate * time_years) * norm_cdf(-d2) / 100

        return {
            "delta": round(delta, 4),
            "gamma": round(gamma, 6),
            "theta": round(theta, 4),
            "vega": round(vega, 4),
            "rho": round(rho, 4),
        }
    except Exception:
        return {"delta": 0, "gamma": 0, "theta": 0, "vega": 0, "rho": 0}


def parse_expiry_for_ml(expiry_text):
    try:
        return datetime.strptime(expiry_text, "%d%b%Y").date()
    except Exception:
        return None


def get_time_block(now):
    total_min = now.hour * 60 + now.minute
    if total_min < 9 * 60 + 45:
        return "OPENING"
    elif total_min < 11 * 60:
        return "MORNING"
    elif total_min < 13 * 60:
        return "MIDDAY"
    elif total_min < 14 * 60 + 30:
        return "AFTERNOON"
    elif total_min <= 15 * 60 + 30:
        return "CLOSING"
    return "OUT_OF_MARKET"


def get_expiry_features(expiry_date, now):
    """
    Expiry features for PCR ML data.
    Safe addition: does not change PCR/OI/VWAP logic.
    """
    try:
        if expiry_date is None:
            return 0, 0, 0, "UNKNOWN"

        days_to_expiry = (expiry_date - now.date()).days

        is_expiry_day = 1 if days_to_expiry == 0 else 0
        is_pre_expiry_day = 1 if days_to_expiry == 1 else 0
        is_post_expiry_day = 1 if days_to_expiry == -1 else 0

        # Simple monthly expiry approximation: expiry near month-end.
        # This is a metadata feature only; it does not affect trading logic.
        expiry_type = "MONTHLY" if expiry_date.day >= 24 else "WEEKLY"

        return is_expiry_day, is_pre_expiry_day, is_post_expiry_day, expiry_type

    except Exception:
        return 0, 0, 0, "UNKNOWN"


def get_india_vix_from_symbols(symbols):
    """
    Finds India VIX token from Angel symbol master and fetches LTP.
    If token is not found, returns 0 without disturbing the main bot.
    """
    global india_vix_token, india_vix_symbol

    try:
        if india_vix_token is None:
            for s in symbols:
                name = str(s.get("name", "")).upper()
                symbol = str(s.get("symbol", "")).upper()
                exch = str(s.get("exch_seg", "")).upper()

                if exch == "NSE" and ("INDIA VIX" in name or "INDIAVIX" in name or "INDIA VIX" in symbol or "INDIAVIX" in symbol):
                    india_vix_token = s.get("token")
                    india_vix_symbol = s.get("symbol") or s.get("name") or "INDIA VIX"
                    print("INDIA VIX FOUND:", india_vix_symbol, india_vix_token)
                    break

        if india_vix_token:
            vix = safe_ltp("NSE", india_vix_symbol, india_vix_token)
            return round(vix, 2) if vix is not None else 0

    except Exception as e:
        print("India VIX Fetch Error:", e)
        log_error(str(e))

    return 0


def get_snapshot_nifty_change(minutes_back, current_nifty):
    """
    Approximate change using stored 1-minute PCR ML snapshots.
    """
    try:
        if len(pcr_ml_snapshots) <= minutes_back:
            return 0
        old = pcr_ml_snapshots[-minutes_back]
        return round(float(current_nifty) - float(old.get("NIFTY", current_nifty)), 2)
    except Exception:
        return 0


def get_market_direction(nifty_change_1min, nifty_change_5min, nifty_change_15min):
    if nifty_change_15min > 30:
        return "STRONG_UP"
    if nifty_change_15min < -30:
        return "STRONG_DOWN"
    if nifty_change_5min > 15:
        return "UP"
    if nifty_change_5min < -15:
        return "DOWN"
    if abs(nifty_change_1min) <= 5:
        return "SIDEWAYS"
    return "MIXED"


def build_pcr_ml_snapshot(symbols, context, nifty, now):
    previous = previous_pcr_ml_snapshot or {}

    option_by_token = context.get("option_by_token", {})
    strike_agg = {}

    total_ce_oi = 0
    total_pe_oi = 0
    total_ce_volume = 0
    total_pe_volume = 0

    for _, opt in option_by_token.items():
        strike = int(opt.get("strike", 0) or 0)
        opt_type = opt.get("type", "")
        oi = int(opt.get("oi", 0) or 0)
        volume = int(opt.get("volume", 0) or 0)

        if strike not in strike_agg:
            strike_agg[strike] = {"CE_OI": 0, "PE_OI": 0, "CE_VOLUME": 0, "PE_VOLUME": 0}

        if opt_type == "CE":
            total_ce_oi += oi
            total_ce_volume += volume
            strike_agg[strike]["CE_OI"] += oi
            strike_agg[strike]["CE_VOLUME"] += volume
        elif opt_type == "PE":
            total_pe_oi += oi
            total_pe_volume += volume
            strike_agg[strike]["PE_OI"] += oi
            strike_agg[strike]["PE_VOLUME"] += volume

    pcr_oi = round(total_pe_oi / total_ce_oi, 4) if total_ce_oi else 0
    pcr_volume = round(total_pe_volume / total_ce_volume, 4) if total_ce_volume else 0

    max_ce_oi_strike = 0
    max_pe_oi_strike = 0
    if strike_agg:
        max_ce_oi_strike = max(strike_agg, key=lambda k: strike_agg[k]["CE_OI"])
        max_pe_oi_strike = max(strike_agg, key=lambda k: strike_agg[k]["PE_OI"])

    india_vix = get_india_vix_from_symbols(symbols)

    prev_vix = safe_float(previous.get("INDIA_VIX", 0))
    prev_ce_oi = safe_float(previous.get("TOTAL_CE_OI", 0))
    prev_pe_oi = safe_float(previous.get("TOTAL_PE_OI", 0))
    prev_ce_volume = safe_float(previous.get("CE_VOLUME", 0))
    prev_pe_volume = safe_float(previous.get("PE_VOLUME", 0))
    prev_ce_strike = safe_float(previous.get("MAX_CE_OI_STRIKE", 0))
    prev_pe_strike = safe_float(previous.get("MAX_PE_OI_STRIKE", 0))

    vix_change = round(india_vix - prev_vix, 2) if prev_vix else 0
    ce_oi_change = round(total_ce_oi - prev_ce_oi, 2) if prev_ce_oi else 0
    pe_oi_change = round(total_pe_oi - prev_pe_oi, 2) if prev_pe_oi else 0
    ce_volume_change = round(total_ce_volume - prev_ce_volume, 2) if prev_ce_volume else 0
    pe_volume_change = round(total_pe_volume - prev_pe_volume, 2) if prev_pe_volume else 0

    max_pain = context.get("max_pain", 0) or 0
    distance_max_pain = round(float(nifty) - float(max_pain), 2) if max_pain else 0
    vwap_distance = round(float(nifty) - float(current_vwap), 2) if current_vwap is not None else 0

    expiry_date = parse_expiry_for_ml(context.get("expiry", ""))
    days_to_expiry = (expiry_date - now.date()).days if expiry_date else ""
    is_expiry_day, is_pre_expiry_day, is_post_expiry_day, expiry_type = get_expiry_features(expiry_date, now)

    # ATM option Greeks are calculated from ATM CE/PE LTP using Black-Scholes.
    # If broker/API gives no IV directly, IV is reverse-calculated from premium.
    atm_strike = float(context.get("atm", 0) or 0)
    atm_ce_data = option_by_token.get(context.get("atm_ce_token"), {})
    atm_pe_data = option_by_token.get(context.get("atm_pe_token"), {})
    atm_ce_ltp = safe_float(atm_ce_data.get("ltp", 0))
    atm_pe_ltp = safe_float(atm_pe_data.get("ltp", 0))
    risk_free_rate = 0.06
    greeks_days = days_to_expiry if isinstance(days_to_expiry, int) and days_to_expiry >= 0 else 1
    time_years = max(float(greeks_days), 1) / 365.0
    atm_ce_iv = implied_volatility(atm_ce_ltp, float(nifty), atm_strike, time_years, risk_free_rate, "CE") if atm_strike else 0
    atm_pe_iv = implied_volatility(atm_pe_ltp, float(nifty), atm_strike, time_years, risk_free_rate, "PE") if atm_strike else 0
    atm_ce_greeks = calculate_option_greeks(float(nifty), atm_strike, greeks_days, risk_free_rate, atm_ce_iv, "CE") if atm_strike else {"delta":0,"gamma":0,"theta":0,"vega":0,"rho":0}
    atm_pe_greeks = calculate_option_greeks(float(nifty), atm_strike, greeks_days, risk_free_rate, atm_pe_iv, "PE") if atm_strike else {"delta":0,"gamma":0,"theta":0,"vega":0,"rho":0}

    nifty_change_1min = get_snapshot_nifty_change(1, nifty)
    nifty_change_5min = get_snapshot_nifty_change(5, nifty)
    nifty_change_15min = get_snapshot_nifty_change(15, nifty)
    market_direction = get_market_direction(nifty_change_1min, nifty_change_5min, nifty_change_15min)

    # Basic placeholder ML signal. Actual trained ML model will replace this after enough history.
    if pcr_oi > 1.2 and pe_oi_change > ce_oi_change and vix_change <= 0:
        ml_signal = "BULLISH_BIAS"
    elif pcr_oi < 0.8 and ce_oi_change > pe_oi_change and vix_change >= 0:
        ml_signal = "BEARISH_BIAS"
    else:
        ml_signal = "DATA_COLLECTION"

    return {
        "TIME": now.strftime("%Y-%m-%d %H:%M:%S"),
        "NIFTY": round(float(nifty), 2),
        "INDIA_VIX": india_vix,
        "VIX_CHANGE": vix_change,
        "PCR_OI": pcr_oi,
        "PCR_VOLUME": pcr_volume,
        "ATM_PCR": round(float(context.get("atm_pcr", 0) or 0), 4),
        "TOTAL_CE_OI": total_ce_oi,
        "TOTAL_PE_OI": total_pe_oi,
        "CE_OI_CHANGE": ce_oi_change,
        "PE_OI_CHANGE": pe_oi_change,
        "OI_DIFFERENCE": total_pe_oi - total_ce_oi,
        "CE_VOLUME": total_ce_volume,
        "PE_VOLUME": total_pe_volume,
        "CE_VOLUME_CHANGE": ce_volume_change,
        "PE_VOLUME_CHANGE": pe_volume_change,
        "VOLUME_DELTA": total_pe_volume - total_ce_volume,
        "MAX_PAIN": max_pain,
        "DISTANCE_MAX_PAIN": distance_max_pain,
        "MAX_CE_OI_STRIKE": max_ce_oi_strike,
        "MAX_PE_OI_STRIKE": max_pe_oi_strike,
        "CE_STRIKE_SHIFT": int(max_ce_oi_strike - prev_ce_strike) if prev_ce_strike else 0,
        "PE_STRIKE_SHIFT": int(max_pe_oi_strike - prev_pe_strike) if prev_pe_strike else 0,
        "VWAP": current_vwap if current_vwap is not None else "",
        "VWAP_DISTANCE": vwap_distance,
        "DAYS_TO_EXPIRY": days_to_expiry,
        "IS_EXPIRY_DAY": is_expiry_day,
        "IS_PRE_EXPIRY_DAY": is_pre_expiry_day,
        "IS_POST_EXPIRY_DAY": is_post_expiry_day,
        "EXPIRY_TYPE": expiry_type,
        "TIME_BLOCK": get_time_block(now),
        "MARKET_DIRECTION": market_direction,
        "NIFTY_CHANGE_1MIN": nifty_change_1min,
        "NIFTY_CHANGE_5MIN": nifty_change_5min,
        "NIFTY_CHANGE_15MIN": nifty_change_15min,
        "ATM_CE_LTP": round(atm_ce_ltp, 2),
        "ATM_PE_LTP": round(atm_pe_ltp, 2),
        "ATM_CE_IV": atm_ce_iv,
        "ATM_PE_IV": atm_pe_iv,
        "ATM_CE_DELTA": atm_ce_greeks.get("delta", 0),
        "ATM_CE_GAMMA": atm_ce_greeks.get("gamma", 0),
        "ATM_CE_THETA": atm_ce_greeks.get("theta", 0),
        "ATM_CE_VEGA": atm_ce_greeks.get("vega", 0),
        "ATM_CE_RHO": atm_ce_greeks.get("rho", 0),
        "ATM_PE_DELTA": atm_pe_greeks.get("delta", 0),
        "ATM_PE_GAMMA": atm_pe_greeks.get("gamma", 0),
        "ATM_PE_THETA": atm_pe_greeks.get("theta", 0),
        "ATM_PE_VEGA": atm_pe_greeks.get("vega", 0),
        "ATM_PE_RHO": atm_pe_greeks.get("rho", 0),
        "FUTURE_MOVE_15MIN": "",
        "FUTURE_MOVE_30MIN": "",
        "FUTURE_MOVE_60MIN": "",
        "ML_SIGNAL": ml_signal,
        "CREATED_AT": ist_now().strftime("%Y-%m-%d %H:%M:%S")
    }


def save_pcr_ml_snapshot(symbols, context, nifty, now):
    """
    Saves one PCR ML row per minute during live market.
    Sends Telegram only every 2 hours.
    """
    global last_pcr_ml_save_minute, last_pcr_ml_alert_hour, previous_pcr_ml_snapshot, pcr_ml_snapshots

    try:
        minute_key = now.strftime("%Y-%m-%d %H:%M")
        if last_pcr_ml_save_minute == minute_key:
            return

        snapshot = build_pcr_ml_snapshot(symbols, context, nifty, now)
        row = [snapshot.get(h, "") for h in PCR_ML_HEADERS]

        # Save CSV
        file_exists = os.path.exists(PCR_ML_FILE)
        with open(PCR_ML_FILE, "a", newline="") as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(PCR_ML_HEADERS)
            writer.writerow(row)

        # Save Google Sheet tab
        if pcr_ml_sheet is not None:
            pcr_ml_sheet.append_row(row, value_input_option="USER_ENTERED")
        else:
            print("PCR_ML_DATA sheet not connected")

        previous_pcr_ml_snapshot = snapshot
        pcr_ml_snapshots.append(snapshot)
        if len(pcr_ml_snapshots) > 500:
            pcr_ml_snapshots = pcr_ml_snapshots[-500:]

        last_pcr_ml_save_minute = minute_key

        # Backup ML/Greeks CSV to GitHub every configured interval, default 30 minutes.
        upload_any_csv_to_github(
            PCR_ML_FILE,
            GITHUB_PCR_ML_PATH,
            "PCR ML + ATM GREEKS HISTORY",
            force=False,
            last_sync_name="last_github_pcr_ml_sync_time"
        )

        print(
            "PCR ML SNAPSHOT SAVED |",
            snapshot["TIME"],
            "| NIFTY:", snapshot["NIFTY"],
            "| PCR:", snapshot["PCR_OI"],
            "| ATM PCR:", snapshot["ATM_PCR"],
            "| VIX:", snapshot["INDIA_VIX"],
            "| MAX PAIN:", snapshot["MAX_PAIN"]
        )

        # Telegram health alert every 2 hours only
        if now.hour % 2 == 0 and last_pcr_ml_alert_hour != now.hour:
            send_telegram(
                "📊 PCR ML DATA RECORDING ACTIVE\n"
                f"Time: {snapshot['TIME']}\n"
                f"NIFTY: {snapshot['NIFTY']}\n"
                f"India VIX: {snapshot['INDIA_VIX']} | Change: {snapshot['VIX_CHANGE']}\n"
                f"PCR OI: {snapshot['PCR_OI']} | ATM PCR: {snapshot['ATM_PCR']}\n"
                f"Max Pain: {snapshot['MAX_PAIN']} | Distance: {snapshot['DISTANCE_MAX_PAIN']}\n"
                f"Market Direction: {snapshot['MARKET_DIRECTION']}\n"
                f"ML Status: {snapshot['ML_SIGNAL']}"
            )
            last_pcr_ml_alert_hour = now.hour

    except Exception as e:
        print("PCR ML Snapshot Save Error:", e)
        log_error(str(e))
        send_telegram(f"❌ PCR ML SNAPSHOT ERROR\n{e}")

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
            if TELEGRAM_NOTIFY_STARTUP:
                send_telegram("🌅 13 STRATEGY PAPER SYSTEM STARTED SUCCESSFULLY")
            return True

        print("LOGIN FAILED:", data)
        send_telegram(f"❌ LOGIN FAILED\n{data}")
        return False
    except Exception as e:
        print("LOGIN ERROR:", e)
        send_telegram(f"❌ LOGIN ERROR\n{e}")
        return False



def relogin_if_needed(reason=""):
    """Throttle Angel relogin attempts so temporary API issues do not spam login calls."""
    global last_relogin_attempt_time
    now = ist_now()
    try:
        if last_relogin_attempt_time is not None:
            if (now - last_relogin_attempt_time).total_seconds() < RELOGIN_COOLDOWN_SECONDS:
                print(f"RELOGIN SKIPPED DUE TO COOLDOWN: {reason}")
                return False
        last_relogin_attempt_time = now
        print(f"RELOGIN ATTEMPT: {reason}")
        return login()
    except Exception as e:
        print("Relogin helper error:", e)
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
                relogin_if_needed("Invalid Token from LTP")
                time.sleep(3)
        except Exception as e:
            print("LTP Retry Error:", e)
            log_error(str(e))
            print(f"LTP ERROR: {e}")
            relogin_if_needed("LTP exception")
            time.sleep(3)
    return None


# ==========================================================
# MARKET SAFETY / STALE DATA HELPERS
# ==========================================================
def update_market_data_samples(nifty, call_price, put_price, pcr, atm_pcr, now):
    global MARKET_DATA_SAMPLES
    try:
        sample = {
            "time": now.strftime("%Y-%m-%d %H:%M:%S"),
            "nifty": round(float(nifty or 0), 2),
            "call_price": round(float(call_price or 0), 2),
            "put_price": round(float(put_price or 0), 2),
            "pcr": round(float(pcr or 0), 4),
            "atm_pcr": round(float(atm_pcr or 0), 4),
        }
        MARKET_DATA_SAMPLES.append(sample)
        if len(MARKET_DATA_SAMPLES) > STATIC_DATA_MIN_SAMPLES:
            MARKET_DATA_SAMPLES = MARKET_DATA_SAMPLES[-STATIC_DATA_MIN_SAMPLES:]
    except Exception as e:
        print("Market data sample update error:", e)


def is_static_market_data():
    """
    Prevents fresh entries when broker API returns repeated/stale previous-close data.
    On a real open market, NIFTY or option prices normally change within several samples.
    """
    try:
        if not STATIC_DATA_CHECK_ENABLED:
            return False
        if len(MARKET_DATA_SAMPLES) < STATIC_DATA_MIN_SAMPLES:
            return False

        nifty_values = [s["nifty"] for s in MARKET_DATA_SAMPLES]
        call_values = [s["call_price"] for s in MARKET_DATA_SAMPLES if s["call_price"] > 0]
        put_values = [s["put_price"] for s in MARKET_DATA_SAMPLES if s["put_price"] > 0]
        pcr_values = [s["pcr"] for s in MARKET_DATA_SAMPLES]

        nifty_range = max(nifty_values) - min(nifty_values)
        pcr_range = max(pcr_values) - min(pcr_values) if pcr_values else 0

        call_range = 0
        put_range = 0
        if len(call_values) >= STATIC_DATA_MIN_SAMPLES:
            call_range = max(call_values) - min(call_values)
        if len(put_values) >= STATIC_DATA_MIN_SAMPLES:
            put_range = max(put_values) - min(put_values)

        option_static = (
            len(call_values) >= STATIC_DATA_MIN_SAMPLES
            and len(put_values) >= STATIC_DATA_MIN_SAMPLES
            and call_range <= STATIC_DATA_MAX_OPTION_CHANGE_POINTS
            and put_range <= STATIC_DATA_MAX_OPTION_CHANGE_POINTS
        )

        return (
            nifty_range <= STATIC_DATA_MAX_NIFTY_CHANGE_POINTS
            and option_static
            and pcr_range <= STATIC_DATA_MAX_PCR_CHANGE
        )
    except Exception as e:
        print("Static market data check error:", e)
        return False


def trading_entries_allowed(mode):
    """Fresh paper entries are allowed only in confirmed live market with non-static data."""
    if mode != "LIVE MARKET":
        return False
    if is_static_market_data():
        return False
    return True


# ==========================================================
# PERSISTENT NIFTY CANDLE HISTORY HELPERS
# ==========================================================
def normalize_candle(candle):
    try:
        return {
            "date": str(candle.get("date", "")),
            "minute": str(candle.get("minute", "")),
            "open": round(float(candle.get("open", 0)), 2),
            "high": round(float(candle.get("high", 0)), 2),
            "low": round(float(candle.get("low", 0)), 2),
            "close": round(float(candle.get("close", 0)), 2),
            "vwap": round(float(candle.get("vwap", 0)), 2),
        }
    except Exception:
        return None


def github_configured():
    return bool(GITHUB_TOKEN and GITHUB_BACKUP_REPO and GITHUB_BACKUP_BRANCH and GITHUB_CANDLE_PATH)


def github_api_headers():
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "nifty-paper-trade-bot"
    }


def github_contents_url():
    return f"https://api.github.com/repos/{GITHUB_BACKUP_REPO}/contents/{GITHUB_CANDLE_PATH}"


def download_candle_history_from_github():
    """
    Downloads the last backed-up candle CSV from GitHub before local loading.
    This is used after Railway restart/redeploy so indicator history is restored.
    """
    try:
        if not github_configured():
            print("GitHub candle restore skipped: GITHUB_TOKEN/GITHUB_REPO not configured")
            return False

        params = {"ref": GITHUB_BACKUP_BRANCH}
        response = requests.get(github_contents_url(), headers=github_api_headers(), params=params, timeout=20)

        if response.status_code == 404:
            print("GitHub candle history not found yet. A new file will be created on next sync.")
            return False

        if response.status_code not in [200, 201]:
            print("GitHub candle restore failed:", response.status_code, response.text[:300])
            log_error(f"GitHub candle restore failed: {response.status_code} {response.text[:300]}")
            return False

        data = response.json()
        encoded_content = data.get("content", "")
        if not encoded_content:
            print("GitHub candle restore skipped: empty content")
            return False

        csv_bytes = base64.b64decode(encoded_content)
        with open(CANDLE_HISTORY_FILE, "wb") as f:
            f.write(csv_bytes)

        print(f"GitHub candle history restored to {CANDLE_HISTORY_FILE}")
        if TELEGRAM_NOTIFY_GITHUB:
            send_telegram(
                "📥 CANDLE HISTORY RESTORED FROM GITHUB\n"
                f"Repo: {GITHUB_BACKUP_REPO}\n"
                f"Path: {GITHUB_CANDLE_PATH}\n"
                f"Branch: {GITHUB_BACKUP_BRANCH}"
            )
        return True

    except Exception as e:
        print("GitHub Candle Download Error:", e)
        log_error(str(e))
        return False



def upload_any_csv_to_github(local_file, github_path, label, force=False, last_sync_name=None):
    """Generic GitHub overwrite backup for CSV files."""
    try:
        if not (GITHUB_TOKEN and GITHUB_BACKUP_REPO and GITHUB_BACKUP_BRANCH and github_path):
            return False
        if not os.path.exists(local_file):
            return False

        now = ist_now()
        if last_sync_name and not force:
            last_sync = globals().get(last_sync_name)
            if last_sync is not None:
                elapsed = (now - last_sync).total_seconds()
                if elapsed < GITHUB_SYNC_INTERVAL_SECONDS:
                    return False

        with open(local_file, "rb") as f:
            raw_bytes = f.read()
        if not raw_bytes:
            return False

        url = f"https://api.github.com/repos/{GITHUB_BACKUP_REPO}/contents/{github_path}"
        sha = None
        get_response = requests.get(url, headers=github_api_headers(), params={"ref": GITHUB_BACKUP_BRANCH}, timeout=20)
        if get_response.status_code in [200, 201]:
            sha = get_response.json().get("sha")
        elif get_response.status_code != 404:
            print(f"GitHub {label} SHA fetch failed:", get_response.status_code, get_response.text[:300])
            log_error(f"GitHub {label} SHA fetch failed: {get_response.status_code} {get_response.text[:300]}")
            return False

        payload = {
            "message": f"Auto backup {label} {now.strftime('%Y-%m-%d %H:%M:%S')}",
            "content": base64.b64encode(raw_bytes).decode("utf-8"),
            "branch": GITHUB_BACKUP_BRANCH
        }
        if sha:
            payload["sha"] = sha

        put_response = requests.put(url, headers=github_api_headers(), json=payload, timeout=30)
        if put_response.status_code not in [200, 201]:
            print(f"GitHub {label} upload failed:", put_response.status_code, put_response.text[:500])
            log_error(f"GitHub {label} upload failed: {put_response.status_code} {put_response.text[:500]}")
            return False

        if last_sync_name:
            globals()[last_sync_name] = now
        print(f"{label} BACKED UP TO GITHUB: {GITHUB_BACKUP_REPO}/{github_path} [{GITHUB_BACKUP_BRANCH}]")
        return True

    except Exception as e:
        print(f"GitHub {label} Upload Error:", e)
        log_error(str(e))
        return False


def upload_candle_history_to_github(force=False):
    """
    Overwrites the same candle CSV in GitHub every 30 minutes.
    This protects strategy candle history if Railway restarts or local files are lost.
    """
    global last_github_candle_sync_time

    try:
        if not github_configured():
            return False

        if not os.path.exists(CANDLE_HISTORY_FILE):
            return False

        now = ist_now()
        if not force and last_github_candle_sync_time is not None:
            elapsed = (now - last_github_candle_sync_time).total_seconds()
            if elapsed < GITHUB_SYNC_INTERVAL_SECONDS:
                return False

        with open(CANDLE_HISTORY_FILE, "rb") as f:
            raw_bytes = f.read()

        if not raw_bytes:
            return False

        # Get current SHA if file already exists. Required by GitHub for overwrite/update.
        sha = None
        get_response = requests.get(
            github_contents_url(),
            headers=github_api_headers(),
            params={"ref": GITHUB_BACKUP_BRANCH},
            timeout=20
        )
        if get_response.status_code in [200, 201]:
            sha = get_response.json().get("sha")
        elif get_response.status_code != 404:
            print("GitHub SHA fetch failed:", get_response.status_code, get_response.text[:300])
            log_error(f"GitHub SHA fetch failed: {get_response.status_code} {get_response.text[:300]}")
            return False

        payload = {
            "message": f"Auto backup NIFTY candle history {now.strftime('%Y-%m-%d %H:%M:%S')}",
            "content": base64.b64encode(raw_bytes).decode("utf-8"),
            "branch": GITHUB_BACKUP_BRANCH
        }
        if sha:
            payload["sha"] = sha

        put_response = requests.put(github_contents_url(), headers=github_api_headers(), json=payload, timeout=30)
        if put_response.status_code not in [200, 201]:
            print("GitHub candle upload failed:", put_response.status_code, put_response.text[:500])
            log_error(f"GitHub candle upload failed: {put_response.status_code} {put_response.text[:500]}")
            return False

        last_github_candle_sync_time = now
        print(f"CANDLE HISTORY BACKED UP TO GITHUB: {GITHUB_BACKUP_REPO}/{GITHUB_CANDLE_PATH} [{GITHUB_BACKUP_BRANCH}]")
        if TELEGRAM_NOTIFY_GITHUB:
            send_telegram(
                "📤 CANDLE HISTORY BACKED UP TO GITHUB\n"
                f"Candles: {len(nifty_candles)}\n"
                f"Path: {GITHUB_CANDLE_PATH}\n"
                f"Time: {now.strftime('%Y-%m-%d %H:%M:%S')}"
            )
        return True

    except Exception as e:
        print("GitHub Candle Upload Error:", e)
        log_error(str(e))
        return False


def save_candle_history():
    """
    Saves completed 1-minute NIFTY candles to local CSV immediately.
    GitHub backup is throttled and overwrites the same CSV every 30 minutes.
    """
    try:
        candles_to_save = nifty_candles[-MAX_CANDLE_HISTORY:]
        with open(CANDLE_HISTORY_FILE, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["date", "minute", "open", "high", "low", "close", "vwap"])
            writer.writeheader()
            for candle in candles_to_save:
                row = normalize_candle(candle)
                if row:
                    writer.writerow(row)

        upload_candle_history_to_github(force=False)

    except Exception as e:
        print("Candle History Save Error:", e)
        log_error(str(e))


def load_candle_history():
    """
    First tries to download candle history CSV from GitHub.
    Then loads today's candles from CSV into memory for strategy calculations.
    """
    global nifty_candles, session_price_sum, session_price_count, current_vwap, last_vwap

    try:
        download_candle_history_from_github()

        if not os.path.exists(CANDLE_HISTORY_FILE):
            print("No candle history file found. Fresh candle collection will start.")
            return

        today = ist_now().strftime("%Y-%m-%d")
        loaded = []
        with open(CANDLE_HISTORY_FILE, "r", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                # Intraday indicators must use today's candles only.
                if row.get("date") != today:
                    continue
                candle = normalize_candle(row)
                if candle:
                    loaded.append(candle)

        # Remove duplicate minute entries and keep latest value.
        candle_by_minute = {}
        for candle in loaded:
            candle_by_minute[candle["minute"]] = candle
        loaded = [candle_by_minute[k] for k in sorted(candle_by_minute.keys())]

        nifty_candles = loaded[-MAX_CANDLE_HISTORY:]

        if nifty_candles:
            session_price_sum = sum(float(c["close"]) for c in nifty_candles)
            session_price_count = len(nifty_candles)
            current_vwap = round(session_price_sum / session_price_count, 2)
            last_vwap = current_vwap
            print(f"CANDLE HISTORY LOADED: {len(nifty_candles)} candles from {CANDLE_HISTORY_FILE}")
            if TELEGRAM_NOTIFY_GITHUB:
                send_telegram(f"📚 NIFTY CANDLE HISTORY LOADED\nCandles: {len(nifty_candles)}\nFile: {CANDLE_HISTORY_FILE}")
        else:
            print("Candle history file found, but no today's candles available.")

    except Exception as e:
        print("Candle History Load Error:", e)
        log_error(str(e))

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
        if len(nifty_candles) > MAX_CANDLE_HISTORY:
            nifty_candles = nifty_candles[-MAX_CANDLE_HISTORY:]
        save_candle_history()
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
    and s.get("name", "") == "NIFTY"
    and (
        s.get("symbol", "").endswith("CE")
        or s.get("symbol", "").endswith("PE")
    )
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
# STOP LOSS HUNTING STRATEGY HELPERS - STRATEGY 8
# ==========================================================
def sl_hunt_cooldown_ok(now):
    if last_sl_hunt_entry_time is None:
        return True
    try:
        return (now - last_sl_hunt_entry_time).total_seconds() >= SL_HUNT_COOLDOWN_SECONDS
    except Exception:
        return True


def get_sl_hunt_state():
    candles = get_all_working_candles()
    if len(candles) < SL_HUNT_MIN_CANDLES_REQUIRED:
        return None

    completed = list(nifty_candles)
    if len(completed) < SL_HUNT_LOOKBACK_CANDLES:
        return None

    recent = completed[-SL_HUNT_LOOKBACK_CANDLES:]
    return {
        "recent_high": round(max(float(c["high"]) for c in recent), 2),
        "recent_low": round(min(float(c["low"]) for c in recent), 2),
    }


def get_sl_hunt_signal(nifty, pcr_change_3min, atm_pcr_change_3min):
    """
    Strategy-8 Stop Loss Hunting:
    BUY CE after downside liquidity sweep:
      - current candle low breaks recent swing low
      - candle closes back above that low
      - bullish rejection body / lower wick
      - optional VWAP and PCR filters

    BUY PE after upside liquidity sweep:
      - current candle high breaks recent swing high
      - candle closes back below that high
      - bearish rejection body / upper wick
      - optional VWAP and PCR filters
    """
    global last_sl_hunt_signal_key

    if not SL_HUNT_ENABLED:
        return None, None

    if current_candle is None or len(nifty_candles) < SL_HUNT_MIN_CANDLES_REQUIRED:
        return None, None

    recent = nifty_candles[-SL_HUNT_LOOKBACK_CANDLES:]
    recent_high = max(float(c["high"]) for c in recent)
    recent_low = min(float(c["low"]) for c in recent)

    candle_open = float(current_candle["open"])
    candle_high = float(current_candle["high"])
    candle_low = float(current_candle["low"])
    candle_close = float(nifty)

    body = abs(candle_close - candle_open)
    if body < SL_HUNT_MIN_BODY_POINTS:
        return None, None

    upper_wick = candle_high - max(candle_open, candle_close)
    lower_wick = min(candle_open, candle_close) - candle_low

    downside_sweep = candle_low <= recent_low - SL_HUNT_SWEEP_BUFFER_POINTS
    upside_sweep = candle_high >= recent_high + SL_HUNT_SWEEP_BUFFER_POINTS

    bullish_reclaim = candle_close >= recent_low + SL_HUNT_RECLAIM_BUFFER_POINTS and candle_close > candle_open
    bearish_reclaim = candle_close <= recent_high - SL_HUNT_RECLAIM_BUFFER_POINTS and candle_close < candle_open

    strong_lower_rejection = lower_wick >= body * SL_HUNT_MIN_WICK_RATIO
    strong_upper_rejection = upper_wick >= body * SL_HUNT_MIN_WICK_RATIO

    vwap_ce_ok = True
    vwap_pe_ok = True
    if SL_HUNT_USE_VWAP_FILTER:
        if current_vwap is None:
            return None, None
        # For reversal entries, allow price near/reclaiming VWAP; don't make it too strict.
        vwap_ce_ok = candle_close >= float(current_vwap) - 10
        vwap_pe_ok = candle_close <= float(current_vwap) + 10

    pcr_ce_ok = True
    pcr_pe_ok = True
    if SL_HUNT_USE_PCR_FILTER:
        # Avoid CE if PCR is strongly falling, avoid PE if PCR is strongly rising.
        pcr_ce_ok = not (pcr_change_3min < -ENTRY_PCR_CHANGE or atm_pcr_change_3min < -ENTRY_ATM_PCR_CHANGE)
        pcr_pe_ok = not (pcr_change_3min > ENTRY_PCR_CHANGE or atm_pcr_change_3min > ENTRY_ATM_PCR_CHANGE)

    minute_key = current_candle.get("minute", "")

    if downside_sweep and bullish_reclaim and strong_lower_rejection and vwap_ce_ok and pcr_ce_ok:
        key = f"CE_{minute_key}_{round(recent_low,2)}"
        if last_sl_hunt_signal_key == key:
            return None, None
        last_sl_hunt_signal_key = key
        trigger = (
            f"SL_HUNT BUY CE: Downside stop-loss sweep below {round(recent_low,2)}, "
            f"low {round(candle_low,2)}, close reclaimed {round(candle_close,2)}, "
            f"lower wick {round(lower_wick,2)}, VWAP {current_vwap}"
        )
        return "BUY CE", trigger

    if upside_sweep and bearish_reclaim and strong_upper_rejection and vwap_pe_ok and pcr_pe_ok:
        key = f"PE_{minute_key}_{round(recent_high,2)}"
        if last_sl_hunt_signal_key == key:
            return None, None
        last_sl_hunt_signal_key = key
        trigger = (
            f"SL_HUNT BUY PE: Upside stop-loss sweep above {round(recent_high,2)}, "
            f"high {round(candle_high,2)}, close rejected {round(candle_close,2)}, "
            f"upper wick {round(upper_wick,2)}, VWAP {current_vwap}"
        )
        return "BUY PE", trigger

    return None, None


def update_sl_hunt_trade_protection(trade, current_price):
    entry_price = float(trade["entry_price"])
    current_price = float(current_price)
    profit_percent = round(((current_price - entry_price) / entry_price) * 100, 2)

    highest_price = float(trade.get("highest_price", entry_price))
    if current_price > highest_price:
        highest_price = current_price
        trade["highest_price"] = round(highest_price, 2)

    current_sl = float(trade.get("trailing_sl_price", entry_price * (1 - SL_HUNT_OPTION_SL_PERCENT / 100)))
    new_sl = current_sl

    if profit_percent >= SL_HUNT_BREAKEVEN_PERCENT:
        new_sl = max(new_sl, entry_price)

    new_sl = round(new_sl, 2)
    if new_sl != round(current_sl, 2):
        trade["trailing_sl_price"] = new_sl
        save_active_trades()

    return profit_percent, new_sl


def check_sl_hunt_exit(trade, current_price, nifty):
    if current_price is None:
        return None, None

    profit_percent, trailing_sl = update_sl_hunt_trade_protection(trade, current_price)

    if float(current_price) <= float(trailing_sl):
        return "SL_HUNT SL HIT", f"Option {round(float(current_price),2)} <= SL {trailing_sl}, P/L {profit_percent}%"

    if profit_percent >= SL_HUNT_TARGET_PERCENT:
        return "SL_HUNT TARGET HIT", f"Option profit {profit_percent}%"

    # Exit if reversal fails beyond current candle sweep opposite side.
    if len(nifty_candles) >= 1:
        prev = nifty_candles[-1]
        if trade["trade_type"] == "BUY CE" and float(nifty) < float(prev["low"]):
            return "SL_HUNT CE REVERSAL FAILED", f"NIFTY {round(float(nifty),2)} below previous candle low {round(float(prev['low']),2)}"
        if trade["trade_type"] == "BUY PE" and float(nifty) > float(prev["high"]):
            return "SL_HUNT PE REVERSAL FAILED", f"NIFTY {round(float(nifty),2)} above previous candle high {round(float(prev['high']),2)}"

    return None, None



# ==========================================================
# STRATEGY 9 TO 13 HELPERS
# ==========================================================
def aggregate_candles(base_candles, minutes=3):
    """Aggregate 1-minute candles into N-minute candles using candle timestamp."""
    try:
        if not base_candles:
            return []
        groups = {}
        for c in base_candles:
            minute_text = c.get("minute", "")
            if not minute_text:
                continue
            dt = datetime.strptime(minute_text, "%Y-%m-%d %H:%M")
            bucket_minute = (dt.minute // minutes) * minutes
            bucket_dt = dt.replace(minute=bucket_minute, second=0, microsecond=0)
            key = bucket_dt.strftime("%Y-%m-%d %H:%M")
            if key not in groups:
                groups[key] = {
                    "date": c.get("date", bucket_dt.strftime("%Y-%m-%d")),
                    "minute": key,
                    "open": float(c["open"]),
                    "high": float(c["high"]),
                    "low": float(c["low"]),
                    "close": float(c["close"]),
                    "vwap": c.get("vwap", "")
                }
            else:
                groups[key]["high"] = max(groups[key]["high"], float(c["high"]))
                groups[key]["low"] = min(groups[key]["low"], float(c["low"]))
                groups[key]["close"] = float(c["close"])
                groups[key]["vwap"] = c.get("vwap", groups[key].get("vwap", ""))
        return [groups[k] for k in sorted(groups.keys())]
    except Exception as e:
        print("Aggregate Candle Error:", e)
        log_error(str(e))
        return []


def ema9_20_cooldown_ok(now):
    if last_ema9_20_entry_time is None:
        return True
    try:
        return (now - last_ema9_20_entry_time).total_seconds() >= EMA9_20_COOLDOWN_SECONDS
    except Exception:
        return True


def get_ema9_20_state():
    if not EMA9_20_ENABLED:
        return None
    candles_3m = aggregate_candles(get_all_working_candles(), 3)
    if len(candles_3m) < EMA9_20_MIN_3MIN_CANDLES:
        return None
    closes = [float(c["close"]) for c in candles_3m]
    ema_fast = calculate_ema_series(closes, EMA9_20_FAST)
    ema_slow = calculate_ema_series(closes, EMA9_20_SLOW)
    if len(ema_fast) < 2 or len(ema_slow) < 2:
        return None
    return {
        "ema_fast": round(ema_fast[-1], 2),
        "ema_slow": round(ema_slow[-1], 2),
        "prev_ema_fast": round(ema_fast[-2], 2),
        "prev_ema_slow": round(ema_slow[-2], 2),
        "close": round(closes[-1], 2),
        "minute": candles_3m[-1].get("minute", "")
    }


def get_ema9_20_signal():
    global last_ema9_20_signal_key
    state = get_ema9_20_state()
    if state is None:
        return None, None
    bullish_cross = state["prev_ema_fast"] <= state["prev_ema_slow"] and state["ema_fast"] > state["ema_slow"]
    bearish_cross = state["prev_ema_fast"] >= state["prev_ema_slow"] and state["ema_fast"] < state["ema_slow"]
    if bullish_cross:
        key = "CE_" + state["minute"]
        if last_ema9_20_signal_key == key:
            return None, None
        last_ema9_20_signal_key = key
        return "BUY CE", f"EMA9_20_3MIN BUY CE: EMA9 {state['ema_fast']} crossed above EMA20 {state['ema_slow']} on 3-min candle {state['minute']}"
    if bearish_cross:
        key = "PE_" + state["minute"]
        if last_ema9_20_signal_key == key:
            return None, None
        last_ema9_20_signal_key = key
        return "BUY PE", f"EMA9_20_3MIN BUY PE: EMA9 {state['ema_fast']} crossed below EMA20 {state['ema_slow']} on 3-min candle {state['minute']}"
    return None, None


def check_ema9_20_exit(trade):
    state = get_ema9_20_state()
    if state is None:
        return None, None
    bullish_cross = state["prev_ema_fast"] <= state["prev_ema_slow"] and state["ema_fast"] > state["ema_slow"]
    bearish_cross = state["prev_ema_fast"] >= state["prev_ema_slow"] and state["ema_fast"] < state["ema_slow"]
    if trade["trade_type"] == "BUY CE" and bearish_cross:
        return "EMA9_20 OPPOSITE CROSS EXIT", f"EMA9 {state['ema_fast']} crossed below EMA20 {state['ema_slow']} on 3-min candle"
    if trade["trade_type"] == "BUY PE" and bullish_cross:
        return "EMA9_20 OPPOSITE CROSS EXIT", f"EMA9 {state['ema_fast']} crossed above EMA20 {state['ema_slow']} on 3-min candle"
    return None, None


def get_orb_levels(now):
    try:
        today_text = now.strftime("%Y-%m-%d")
        day_candles = [c for c in nifty_candles if c.get("date") == today_text and "09:15" <= c.get("minute", "")[-5:] < "09:30"]
        if len(day_candles) < 12:
            return None
        high = max(float(c["high"]) for c in day_candles)
        low = min(float(c["low"]) for c in day_candles)
        mid = round((high + low) / 2, 2)
        height = round(high - low, 2)
        if height <= 0:
            return None
        return {"high": round(high, 2), "low": round(low, 2), "mid": mid, "height": height}
    except Exception as e:
        print("ORB Level Error:", e)
        log_error(str(e))
        return None


def orb_entry_time_ok(now):
    if now.hour < 9 or (now.hour == 9 and now.minute < 30):
        return False
    if now.hour > ORB_ENTRY_END_HOUR or (now.hour == ORB_ENTRY_END_HOUR and now.minute > ORB_ENTRY_END_MINUTE):
        return False
    return True


def get_orb_signal(nifty, now):
    global last_orb_trade_date, last_orb_signal_key
    if not ORB_ENABLED or not orb_entry_time_ok(now):
        return None, None, None
    today_text = now.strftime("%Y-%m-%d")
    if last_orb_trade_date == today_text:
        return None, None, None
    levels = get_orb_levels(now)
    if not levels:
        return None, None, None
    price = float(nifty)
    if price >= levels["high"] + ORB_BUFFER_POINTS:
        key = f"CE_{today_text}_{levels['high']}"
        if last_orb_signal_key == key:
            return None, None, None
        last_orb_signal_key = key
        trigger = f"ORB BUY CE: NIFTY {round(price,2)} broke above 15-min OR high {levels['high']} + buffer {ORB_BUFFER_POINTS}. Range {levels['height']}"
        return "BUY CE", trigger, levels
    if price <= levels["low"] - ORB_BUFFER_POINTS:
        key = f"PE_{today_text}_{levels['low']}"
        if last_orb_signal_key == key:
            return None, None, None
        last_orb_signal_key = key
        trigger = f"ORB BUY PE: NIFTY {round(price,2)} broke below 15-min OR low {levels['low']} - buffer {ORB_BUFFER_POINTS}. Range {levels['height']}"
        return "BUY PE", trigger, levels
    return None, None, None


def check_orb_exit(trade, nifty):
    price = float(nifty)
    high = float(trade.get("orb_high", 0) or 0)
    low = float(trade.get("orb_low", 0) or 0)
    mid = float(trade.get("orb_mid", 0) or 0)
    target = float(trade.get("orb_target", 0) or 0)
    if not high or not low or not mid or not target:
        return None, None
    if trade["trade_type"] == "BUY CE":
        if price >= target:
            return "ORB 2X TARGET HIT", f"NIFTY {round(price,2)} reached target {target}"
        if price <= mid:
            return "ORB MIDPOINT STOP EXIT", f"NIFTY {round(price,2)} <= OR midpoint {mid}"
    elif trade["trade_type"] == "BUY PE":
        if price <= target:
            return "ORB 2X TARGET HIT", f"NIFTY {round(price,2)} reached target {target}"
        if price >= mid:
            return "ORB MIDPOINT STOP EXIT", f"NIFTY {round(price,2)} >= OR midpoint {mid}"
    return None, None


def calculate_bollinger_state():
    candles = get_all_working_candles()
    if not BOLLINGER_ENABLED or len(candles) < BOLLINGER_MIN_CANDLES:
        return None
    closes = [float(c["close"]) for c in candles]
    window = closes[-BOLLINGER_PERIOD:]
    if len(window) < BOLLINGER_PERIOD:
        return None
    mean = sum(window) / len(window)
    variance = sum((x - mean) ** 2 for x in window) / len(window)
    std = variance ** 0.5
    return {
        "middle": round(mean, 2),
        "upper": round(mean + BOLLINGER_STD_MULTIPLIER * std, 2),
        "lower": round(mean - BOLLINGER_STD_MULTIPLIER * std, 2),
        "close": closes[-1],
        "prev_close": closes[-2] if len(closes) >= 2 else closes[-1],
        "minute": candles[-1].get("minute", "")
    }


def get_bollinger_signal():
    global last_bollinger_signal_key
    state = calculate_bollinger_state()
    if state is None or len(nifty_candles) < 2:
        return None, None, None
    prev = nifty_candles[-1]
    current = current_candle if current_candle is not None else prev
    price = float(current["close"])
    # Long mean reversion: previous candle pierced lower band, current closes back inside.
    if float(prev["low"]) < state["lower"] and price > state["lower"]:
        key = f"CE_{state['minute']}_{state['lower']}"
        if last_bollinger_signal_key == key:
            return None, None, None
        last_bollinger_signal_key = key
        trigger = f"BOLLINGER BUY CE: Price pierced lower band {state['lower']} and closed back inside. Middle {state['middle']}"
        return "BUY CE", trigger, {"bb_stop": round(float(prev["low"]) - 2, 2)}
    # Short mean reversion: previous candle pierced upper band, current closes back inside.
    if float(prev["high"]) > state["upper"] and price < state["upper"]:
        key = f"PE_{state['minute']}_{state['upper']}"
        if last_bollinger_signal_key == key:
            return None, None, None
        last_bollinger_signal_key = key
        trigger = f"BOLLINGER BUY PE: Price pierced upper band {state['upper']} and closed back inside. Middle {state['middle']}"
        return "BUY PE", trigger, {"bb_stop": round(float(prev["high"]) + 2, 2)}
    return None, None, None


def check_bollinger_exit(trade, nifty):
    state = calculate_bollinger_state()
    if state is None:
        return None, None
    price = float(nifty)
    stop = float(trade.get("bb_stop", 0) or 0)
    if trade["trade_type"] == "BUY CE":
        if stop and price <= stop:
            return "BOLLINGER STRUCTURE STOP", f"NIFTY {round(price,2)} <= stop {stop}"
        if price >= state["middle"]:
            return "BOLLINGER MEAN TARGET", f"NIFTY {round(price,2)} reached middle band {state['middle']}"
    elif trade["trade_type"] == "BUY PE":
        if stop and price >= stop:
            return "BOLLINGER STRUCTURE STOP", f"NIFTY {round(price,2)} >= stop {stop}"
        if price <= state["middle"]:
            return "BOLLINGER MEAN TARGET", f"NIFTY {round(price,2)} reached middle band {state['middle']}"
    return None, None


def calculate_macd_histogram(closes, fast=12, slow=26, signal=9):
    if len(closes) < slow + signal + 5:
        return []
    ema_fast = calculate_ema_series(closes, fast)
    ema_slow = calculate_ema_series(closes, slow)
    if not ema_fast or not ema_slow:
        return []
    offset = len(ema_fast) - len(ema_slow)
    macd_line = [ema_fast[i + offset] - ema_slow[i] for i in range(len(ema_slow))]
    signal_line = calculate_ema_series(macd_line, signal)
    if not signal_line:
        return []
    offset2 = len(macd_line) - len(signal_line)
    return [round(macd_line[i + offset2] - signal_line[i], 4) for i in range(len(signal_line))]


def get_macd_squeeze_signal():
    global last_macd_signal_key
    if not MACD_SQUEEZE_ENABLED:
        return None, None
    candles = get_all_working_candles()
    if len(candles) < MACD_MIN_CANDLES:
        return None, None
    closes = [float(c["close"]) for c in candles]
    hist = calculate_macd_histogram(closes, MACD_FAST, MACD_SLOW, MACD_SIGNAL)
    if len(hist) < 4:
        return None, None
    h1, h2, h3 = hist[-3], hist[-2], hist[-1]
    minute_key = candles[-1].get("minute", "")
    # CE: negative momentum was expanding down, then first lighter negative bar.
    if h2 < 0 and h1 > h2 and h3 > h2:
        key = f"CE_{minute_key}_{h3}"
        if last_macd_signal_key == key:
            return None, None
        last_macd_signal_key = key
        return "BUY CE", f"MACD_SQUEEZE BUY CE: Histogram improved from {h2} to {h3}, downward momentum slowing"
    # PE: positive momentum was expanding up, then first fading positive bar.
    if h2 > 0 and h1 < h2 and h3 < h2:
        key = f"PE_{minute_key}_{h3}"
        if last_macd_signal_key == key:
            return None, None
        last_macd_signal_key = key
        return "BUY PE", f"MACD_SQUEEZE BUY PE: Histogram faded from {h2} to {h3}, upward momentum slowing"
    return None, None


def check_macd_squeeze_exit(trade):
    candles = get_all_working_candles()
    if len(candles) < MACD_MIN_CANDLES:
        return None, None
    closes = [float(c["close"]) for c in candles]
    hist = calculate_macd_histogram(closes, MACD_FAST, MACD_SLOW, MACD_SIGNAL)
    if len(hist) < 3:
        return None, None
    prev_h, curr_h = hist[-2], hist[-1]
    if trade["trade_type"] == "BUY CE":
        if curr_h > 0 and curr_h < prev_h:
            return "MACD POSITIVE FADE EXIT", f"Histogram faded from {prev_h} to {curr_h}"
        if curr_h < 0 and curr_h < prev_h:
            return "MACD MOMENTUM FAILED EXIT", f"Histogram weakened from {prev_h} to {curr_h}"
    elif trade["trade_type"] == "BUY PE":
        if curr_h < 0 and curr_h > prev_h:
            return "MACD NEGATIVE FADE EXIT", f"Histogram faded from {prev_h} to {curr_h}"
        if curr_h > 0 and curr_h > prev_h:
            return "MACD MOMENTUM FAILED EXIT", f"Histogram strengthened against PE from {prev_h} to {curr_h}"
    return None, None


def vwap_false_break_cooldown_ok(now):
    if last_vwap_false_break_entry_time is None:
        return True
    try:
        return (now - last_vwap_false_break_entry_time).total_seconds() >= VWAP_FALSE_BREAK_COOLDOWN_SECONDS
    except Exception:
        return True


def get_session_high_low():
    try:
        candles = get_all_working_candles()
        today = ist_now().strftime("%Y-%m-%d")
        today_candles = [c for c in candles if c.get("date") == today]
        if not today_candles:
            return None, None
        return max(float(c["high"]) for c in today_candles), min(float(c["low"]) for c in today_candles)
    except Exception:
        return None, None


def get_vwap_false_break_signal(nifty):
    global last_vwap_false_break_signal_key
    if not VWAP_FALSE_BREAK_ENABLED or current_vwap is None or len(nifty_candles) < VWAP_FALSE_BREAK_MIN_CANDLES:
        return None, None, None
    prev = nifty_candles[-1]
    price = float(nifty)
    candle_open = float(current_candle.get("open", price)) if current_candle else price
    body = abs(price - candle_open)
    if body < VWAP_FALSE_BREAK_MIN_BODY_POINTS:
        return None, None, None
    minute_key = current_candle.get("minute", "") if current_candle else ""
    session_high, session_low = get_session_high_low()
    # False breakdown: previous closed below VWAP, current reclaimed VWAP.
    if float(prev["close"]) < float(prev.get("vwap", current_vwap) or current_vwap) and price > float(current_vwap):
        key = f"CE_{minute_key}_{round(current_vwap,2)}"
        if last_vwap_false_break_signal_key == key:
            return None, None, None
        last_vwap_false_break_signal_key = key
        trigger = f"VWAP_FALSE_BREAK BUY CE: Price closed below VWAP then reclaimed above VWAP {current_vwap}"
        return "BUY CE", trigger, {"vwap_target": session_high or 0, "vwap_stop": float(prev["low"])}
    # False breakout: previous closed above VWAP, current rejected below VWAP.
    if float(prev["close"]) > float(prev.get("vwap", current_vwap) or current_vwap) and price < float(current_vwap):
        key = f"PE_{minute_key}_{round(current_vwap,2)}"
        if last_vwap_false_break_signal_key == key:
            return None, None, None
        last_vwap_false_break_signal_key = key
        trigger = f"VWAP_FALSE_BREAK BUY PE: Price closed above VWAP then rejected below VWAP {current_vwap}"
        return "BUY PE", trigger, {"vwap_target": session_low or 0, "vwap_stop": float(prev["high"])}
    return None, None, None


def check_vwap_false_break_exit(trade, nifty):
    price = float(nifty)
    target = float(trade.get("vwap_target", 0) or 0)
    stop = float(trade.get("vwap_stop", 0) or 0)
    if current_vwap is None:
        return None, None
    if trade["trade_type"] == "BUY CE":
        if target and price >= target:
            return "VWAP FALSE BREAK SESSION HIGH TARGET", f"NIFTY {round(price,2)} >= session high target {round(target,2)}"
        if stop and price <= stop:
            return "VWAP FALSE BREAK STOP", f"NIFTY {round(price,2)} <= false-break low {round(stop,2)}"
        if price < float(current_vwap):
            return "VWAP REJECTION EXIT", f"NIFTY {round(price,2)} back below VWAP {current_vwap}"
    elif trade["trade_type"] == "BUY PE":
        if target and price <= target:
            return "VWAP FALSE BREAK SESSION LOW TARGET", f"NIFTY {round(price,2)} <= session low target {round(target,2)}"
        if stop and price >= stop:
            return "VWAP FALSE BREAK STOP", f"NIFTY {round(price,2)} >= false-break high {round(stop,2)}"
        if price > float(current_vwap):
            return "VWAP RECLAIM EXIT", f"NIFTY {round(price,2)} back above VWAP {current_vwap}"
    return None, None


# ==========================================================
# STRATEGY LAB EXIT HELPERS - FIXED / EMA9 TRAIL / HYBRID
# ==========================================================
def get_ema_trail_state(period=9):
    """Returns latest EMA trail level using NIFTY working candles."""
    try:
        candles = get_all_working_candles()
        if len(candles) < period + 2:
            return None
        closes = [float(c["close"]) for c in candles]
        ema_values = calculate_ema_series(closes, period)
        if not ema_values:
            return None
        return {
            "ema": round(ema_values[-1], 2),
            "close": round(closes[-1], 2),
            "minute": candles[-1].get("minute", "")
        }
    except Exception as e:
        print("EMA trail state error:", e)
        log_error(str(e))
        return None


def check_fixed_sl_target_exit(trade, current_price, sl_points=LAB_FIXED_SL_POINTS, target_points=LAB_FIXED_TARGET_POINTS, prefix="FIXED"):
    """Fixed option premium SL/target exit."""
    try:
        points = round(float(current_price) - float(trade["entry_price"]), 2)
        if points <= -abs(sl_points):
            return f"{prefix} SL HIT", f"OPTION POINTS {points} <= -{abs(sl_points)}"
        if points >= abs(target_points):
            return f"{prefix} TARGET HIT", f"OPTION POINTS {points} >= {abs(target_points)}"
    except Exception as e:
        print("Fixed exit error:", e)
        log_error(str(e))
    return None, None


def check_ema9_trail_exit(trade, current_price, nifty, prefix="EMA9 TRAIL"):
    """
    Initial SL = 5 premium points.
    No fixed target.
    Exit CE when NIFTY closes below EMA9.
    Exit PE when NIFTY closes above EMA9.
    """
    exit_reason, exit_trigger = check_fixed_sl_target_exit(
        trade, current_price, sl_points=LAB_FIXED_SL_POINTS, target_points=999999, prefix=prefix
    )
    if exit_reason:
        return exit_reason, exit_trigger

    state = get_ema_trail_state(LAB_EMA_TRAIL_PERIOD)
    if state is None:
        return None, None

    price = float(nifty)
    ema9 = float(state["ema"])
    if trade["trade_type"] == "BUY CE" and price < ema9:
        return f"{prefix} CE EMA9 EXIT", f"NIFTY {round(price,2)} closed below EMA9 {ema9}"
    if trade["trade_type"] == "BUY PE" and price > ema9:
        return f"{prefix} PE EMA9 EXIT", f"NIFTY {round(price,2)} closed above EMA9 {ema9}"

    return None, None


def check_hybrid_exit(trade, current_price, nifty, prefix="HYBRID"):
    """
    Initial SL = 5 premium points.
    +10 points profit: stop moves to breakeven.
    +15 points profit: EMA9 trailing activates.
    """
    try:
        entry_price = float(trade["entry_price"])
        current_price = float(current_price)
        points = round(current_price - entry_price, 2)

        if points <= -LAB_FIXED_SL_POINTS:
            return f"{prefix} INITIAL SL HIT", f"OPTION POINTS {points} <= -{LAB_FIXED_SL_POINTS}"

        if points >= LAB_BREAKEVEN_TRIGGER_POINTS and not trade.get("breakeven_active"):
            trade["breakeven_active"] = True
            trade["breakeven_price"] = entry_price
            save_active_trades()

        if trade.get("breakeven_active") and current_price <= float(trade.get("breakeven_price", entry_price)):
            return f"{prefix} BREAKEVEN EXIT", f"After +{LAB_BREAKEVEN_TRIGGER_POINTS}, option returned to entry {entry_price}"

        if points >= LAB_EMA_TRAIL_TRIGGER_POINTS and not trade.get("ema9_trail_active"):
            trade["ema9_trail_active"] = True
            save_active_trades()

        if trade.get("ema9_trail_active"):
            state = get_ema_trail_state(LAB_EMA_TRAIL_PERIOD)
            if state is None:
                return None, None
            price = float(nifty)
            ema9 = float(state["ema"])
            if trade["trade_type"] == "BUY CE" and price < ema9:
                return f"{prefix} CE EMA9 TRAIL EXIT", f"NIFTY {round(price,2)} closed below EMA9 {ema9}"
            if trade["trade_type"] == "BUY PE" and price > ema9:
                return f"{prefix} PE EMA9 TRAIL EXIT", f"NIFTY {round(price,2)} closed above EMA9 {ema9}"

    except Exception as e:
        print("Hybrid exit error:", e)
        log_error(str(e))

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

    # PCR new logic: 1-minute sample entry; exit only fixed SL/target. No PCR reversal exit.
    if strategy_name == "PCR":
        exit_reason, exit_trigger = check_fixed_sl_target_exit(
            trade, current_price, sl_points=LAB_FIXED_SL_POINTS, target_points=LAB_FIXED_TARGET_POINTS, prefix="PCR"
        )

    # Gamma Blast has expiry-specific OTM trailing and OI reversal exit.
    elif strategy_name == GAMMA_BLAST_NAME:
        gamma_context = context if context is not None else {}
        exit_reason, exit_trigger = check_gamma_exit(trade, current_price, nifty, gamma_context, ist_now())

    elif strategy_name == "ADX":
        exit_reason, exit_trigger = check_adx_exit(trade, current_price, nifty)

    # SUPER_EMA family
    elif strategy_name == SUPER_EMA_NAME:
        exit_reason, exit_trigger = check_super_ema_exit(trade, current_price, nifty)
    elif strategy_name == SUPER_EMA_B_NAME:
        exit_reason, exit_trigger = check_fixed_sl_target_exit(
            trade, current_price, sl_points=LAB_FIXED_SL_POINTS, target_points=LAB_FIXED_TARGET_POINTS, prefix=SUPER_EMA_B_NAME
        )
    elif strategy_name == SUPER_EMA_C_NAME:
        exit_reason, exit_trigger = check_hybrid_exit(trade, current_price, nifty, prefix=SUPER_EMA_C_NAME)

    elif strategy_name == SL_HUNT_NAME:
        exit_reason, exit_trigger = check_sl_hunt_exit(trade, current_price, nifty)

    # EMA9/20 family
    elif strategy_name == EMA9_20_NAME:
        exit_reason, exit_trigger = check_ema9_20_exit(trade)
    elif strategy_name == EMA9_20_C_NAME:
        exit_reason, exit_trigger = check_ema9_trail_exit(trade, current_price, nifty, prefix=EMA9_20_C_NAME)
    elif strategy_name == EMA9_20_D_NAME:
        exit_reason, exit_trigger = check_hybrid_exit(trade, current_price, nifty, prefix=EMA9_20_D_NAME)

    # ORB family
    elif strategy_name == ORB_NAME:
        exit_reason, exit_trigger = check_orb_exit(trade, nifty)
    elif strategy_name == ORB_EMA9_TRAIL_NAME:
        exit_reason, exit_trigger = check_ema9_trail_exit(trade, current_price, nifty, prefix=ORB_EMA9_TRAIL_NAME)
    elif strategy_name == ORB_HYBRID_NAME:
        exit_reason, exit_trigger = check_hybrid_exit(trade, current_price, nifty, prefix=ORB_HYBRID_NAME)

    elif strategy_name == BOLLINGER_NAME:
        exit_reason, exit_trigger = check_bollinger_exit(trade, nifty)

    elif strategy_name == MACD_SQUEEZE_NAME:
        exit_reason, exit_trigger = check_macd_squeeze_exit(trade)

    elif strategy_name == VWAP_FALSE_BREAK_NAME:
        exit_reason, exit_trigger = check_vwap_false_break_exit(trade, nifty)

    # RSI family
    elif strategy_name == RSI_STOCH_EMA_NAME:
        exit_reason, exit_trigger = check_rsi_stoch_ema_exit(trade, nifty)
    elif strategy_name == RSI_STOCH_EMA_B_NAME:
        exit_reason, exit_trigger = check_fixed_sl_target_exit(
            trade, current_price, sl_points=LAB_FIXED_SL_POINTS, target_points=LAB_FIXED_TARGET_POINTS, prefix=RSI_STOCH_EMA_B_NAME
        )

    # Common SL / Target for remaining simple strategies such as SMC
    elif points <= -STOPLOSS_POINTS:
        exit_reason = "STOPLOSS HIT"
        exit_trigger = f"OPTION POINTS {points}"
    elif points >= TARGET_POINTS:
        exit_reason = "TARGET HIT"
        exit_trigger = f"OPTION POINTS {points}"

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

    if strategy_name == SL_HUNT_NAME:
        trade["highest_price"] = round(price, 2)
        trade["trailing_sl_price"] = round(price * (1 - SL_HUNT_OPTION_SL_PERCENT / 100), 2)

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
init_pcr_ml_file()
load_candle_history()
init_google_sheet()
load_active_trades()

if not login():
    exit()

symbols = load_symbol_master()
if symbols is None:
    exit()

# Avoid an immediate GitHub commit at startup. This prevents backup commits from triggering
# instant Railway redeploy loops and keeps first sync for the configured interval.
last_github_candle_sync_time = ist_now()
last_github_pcr_ml_sync_time = ist_now()

# ==========================================================
# MAIN LOOP
# ==========================================================
while True:
    try:
        now = ist_now()
        time_str = now.strftime("%Y-%m-%d %H:%M:%S")
        mode = market_mode(now)

        if mode == "HOLIDAY":
            print(f"HOLIDAY - NSE/BSE closed. No fresh entries or exits using stale data. Time: {time_str}")
            if now.hour in [9, 12, 15] and last_heartbeat_hour != now.hour:
                send_telegram(f"🛑 NSE/BSE HOLIDAY - BOT SAFE MODE\nNo paper entries today.\nTime: {time_str}")
                last_heartbeat_hour = now.hour
            time.sleep(1800)
            continue

        # Sleep during closed market if there are no open paper trades.
        # Pre-open relogin at/after 08:45 helps prepare session before market.
        global_last_preopen_login_date = globals().get("last_preopen_login_date")
        if mode in ["AFTER MARKET", "WEEKEND", "HOLIDAY"] and not open_trades:
            if mode == "AFTER MARKET" and now.hour == 8 and now.minute >= 45 and global_last_preopen_login_date != now.date().isoformat():
                if login():
                    globals()["last_preopen_login_date"] = now.date().isoformat()
                    send_telegram(f"🔐 PRE-MARKET AUTO LOGIN DONE\nTime: {time_str}")
            sleep_time = 1800 if mode in ["WEEKEND", "HOLIDAY"] else 300
            print(f"{mode} - No open trades. Sleeping {sleep_time} seconds.")
            time.sleep(sleep_time)
            continue

        # Heartbeat at controlled interval only
        global_last_heartbeat_time = globals().get("last_heartbeat_time")
        if global_last_heartbeat_time is None or (now - global_last_heartbeat_time).total_seconds() >= TELEGRAM_HEARTBEAT_MINUTES * 60:
            send_telegram(f"✅ BOT RUNNING HEALTHY\nTime: {time_str}\nMode: {mode}\nOpen Trades: {len(open_trades)}")
            globals()["last_heartbeat_time"] = now

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

        # 1-minute PCR sample change
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
        print("1 Min PCR Change:", pcr_change_3min, "| 1 Min ATM PCR Change:", atm_pcr_change_3min)
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

        sl_hunt_state = get_sl_hunt_state()
        if sl_hunt_state:
            print(
                "SL_HUNT:",
                "Recent High", sl_hunt_state["recent_high"],
                "Recent Low", sl_hunt_state["recent_low"]
            )
        else:
            print("SL_HUNT: collecting candles")

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

        update_market_data_samples(nifty, call_price, put_price, pcr, atm_pcr, now)
        entry_allowed = trading_entries_allowed(mode)
        if mode == "LIVE MARKET" and not entry_allowed:
            print("ENTRY BLOCKED: Static/stale market data detected. Existing open trades will still be monitored.")

        # ==================================================
        # PCR ML DATA COLLECTION - SEPARATE SILENT MODULE
        # ==================================================
        if mode == "LIVE MARKET":
            save_pcr_ml_snapshot(symbols, context, nifty, now)

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

            # 2) Strategy-1 PCR entry - NEW LOGIC
            # Check every 1 minute. Entry uses only PCR change.
            # BUY CE: PCR change >= +0.25 | BUY PE: PCR change <= -0.25
            # Exit is handled separately: SL 5 points / Target 15 points / Market close square-off.
            if entry_allowed and "PCR" not in open_trades and "PCR" not in exited_strategies and sample_due:
                if pcr_change_3min >= ENTRY_PCR_CHANGE and call_price is not None:
                    entry_trigger = f"PCR NEW BUY CE: 1-min PCR change +{pcr_change_3min} >= +{ENTRY_PCR_CHANGE}"

                    trade = create_trade(
                        "PCR", "BUY CE", atm_ce_symbol, atm_ce_token, call_price, time_str,
                        nifty, pcr, atm_pcr, max_pain, pcr_change_3min, atm_pcr_change_3min, entry_trigger
                    )
                    enter_trade("PCR", trade)

                elif pcr_change_3min <= -ENTRY_PCR_CHANGE and put_price is not None:
                    entry_trigger = f"PCR NEW BUY PE: 1-min PCR change {pcr_change_3min} <= -{ENTRY_PCR_CHANGE}"

                    trade = create_trade(
                        "PCR", "BUY PE", atm_pe_symbol, atm_pe_token, put_price, time_str,
                        nifty, pcr, atm_pcr, max_pain, pcr_change_3min, atm_pcr_change_3min, entry_trigger
                    )
                    enter_trade("PCR", trade)

            # 3) Strategy-2 SMC + VWAP entry
            if entry_allowed and "SMC" not in open_trades and "SMC" not in exited_strategies and smc_cooldown_ok(now):
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
            if entry_allowed and "ADX" not in open_trades and "ADX" not in exited_strategies:
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

            # 5) RSI_STOCH_EMA family entry
            # A = current exit logic. B = same entry, fixed SL 5 / Target 15 / Market close only.
            if entry_allowed:
                rsi_stoch_signal, rsi_stoch_trigger = get_rsi_stoch_ema_signal(nifty, now)

                if rsi_stoch_signal == "BUY CE" and call_price is not None:
                    for rsi_strategy_name in [RSI_STOCH_EMA_NAME, RSI_STOCH_EMA_B_NAME]:
                        if rsi_strategy_name not in open_trades and rsi_strategy_name not in exited_strategies:
                            trade = create_trade(
                                rsi_strategy_name, "BUY CE", atm_ce_symbol, atm_ce_token, call_price, time_str,
                                nifty, pcr, atm_pcr, max_pain, pcr_change_3min, atm_pcr_change_3min,
                                rsi_stoch_trigger.replace("RSI_STOCH_EMA", rsi_strategy_name)
                            )
                            enter_trade(rsi_strategy_name, trade)

                elif rsi_stoch_signal == "BUY PE" and put_price is not None:
                    for rsi_strategy_name in [RSI_STOCH_EMA_NAME, RSI_STOCH_EMA_B_NAME]:
                        if rsi_strategy_name not in open_trades and rsi_strategy_name not in exited_strategies:
                            trade = create_trade(
                                rsi_strategy_name, "BUY PE", atm_pe_symbol, atm_pe_token, put_price, time_str,
                                nifty, pcr, atm_pcr, max_pain, pcr_change_3min, atm_pcr_change_3min,
                                rsi_stoch_trigger.replace("RSI_STOCH_EMA", rsi_strategy_name)
                            )
                            enter_trade(rsi_strategy_name, trade)

            # 6) Strategy-5 Pure Market Structure entry
            if entry_allowed and MARKET_STRUCTURE_NAME not in open_trades and MARKET_STRUCTURE_NAME not in exited_strategies and market_structure_cooldown_ok(now):
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

            # 7) SUPER_EMA family entry
            # A = current logic, B = fixed SL/target, C = hybrid breakeven + EMA9 trailing.
            if entry_allowed and super_ema_cooldown_ok(now):
                super_signal, super_trigger = get_super_ema_signal(nifty)

                if super_signal == "BUY CE" and call_price is not None:
                    for super_strategy_name in [SUPER_EMA_NAME, SUPER_EMA_B_NAME, SUPER_EMA_C_NAME]:
                        if super_strategy_name not in open_trades and super_strategy_name not in exited_strategies:
                            trade = create_trade(
                                super_strategy_name, "BUY CE", atm_ce_symbol, atm_ce_token, call_price, time_str,
                                nifty, pcr, atm_pcr, max_pain, pcr_change_3min, atm_pcr_change_3min,
                                super_trigger.replace("SUPER_EMA", super_strategy_name)
                            )
                            enter_trade(super_strategy_name, trade)
                    last_super_ema_entry_time = now

                elif super_signal == "BUY PE" and put_price is not None:
                    for super_strategy_name in [SUPER_EMA_NAME, SUPER_EMA_B_NAME, SUPER_EMA_C_NAME]:
                        if super_strategy_name not in open_trades and super_strategy_name not in exited_strategies:
                            trade = create_trade(
                                super_strategy_name, "BUY PE", atm_pe_symbol, atm_pe_token, put_price, time_str,
                                nifty, pcr, atm_pcr, max_pain, pcr_change_3min, atm_pcr_change_3min,
                                super_trigger.replace("SUPER_EMA", super_strategy_name)
                            )
                            enter_trade(super_strategy_name, trade)
                    last_super_ema_entry_time = now


            # 8) Strategy-7 Gamma Blast Expiry entry
            if (
                entry_allowed
                and GAMMA_BLAST_NAME not in open_trades
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

            # 9) Strategy-8 Stop Loss Hunting entry
            if entry_allowed and SL_HUNT_NAME not in open_trades and SL_HUNT_NAME not in exited_strategies and sl_hunt_cooldown_ok(now):
                sl_hunt_signal, sl_hunt_trigger = get_sl_hunt_signal(
                    nifty, pcr_change_3min, atm_pcr_change_3min
                )

                if sl_hunt_signal == "BUY CE" and call_price is not None:
                    trade = create_trade(
                        SL_HUNT_NAME, "BUY CE", atm_ce_symbol, atm_ce_token, call_price, time_str,
                        nifty, pcr, atm_pcr, max_pain, pcr_change_3min, atm_pcr_change_3min, sl_hunt_trigger
                    )
                    enter_trade(SL_HUNT_NAME, trade)
                    last_sl_hunt_entry_time = now

                elif sl_hunt_signal == "BUY PE" and put_price is not None:
                    trade = create_trade(
                        SL_HUNT_NAME, "BUY PE", atm_pe_symbol, atm_pe_token, put_price, time_str,
                        nifty, pcr, atm_pcr, max_pain, pcr_change_3min, atm_pcr_change_3min, sl_hunt_trigger
                    )
                    enter_trade(SL_HUNT_NAME, trade)
                    last_sl_hunt_entry_time = now


            # 10) EMA9/20 3-minute family entry
            # A = current opposite-crossover exit, C = EMA9 trail, D = hybrid.
            if entry_allowed and ema9_20_cooldown_ok(now):
                ema_signal, ema_trigger = get_ema9_20_signal()

                if ema_signal == "BUY CE" and call_price is not None:
                    for ema_strategy_name in [EMA9_20_NAME, EMA9_20_C_NAME, EMA9_20_D_NAME]:
                        if ema_strategy_name not in open_trades and ema_strategy_name not in exited_strategies:
                            trade = create_trade(
                                ema_strategy_name, "BUY CE", atm_ce_symbol, atm_ce_token, call_price, time_str,
                                nifty, pcr, atm_pcr, max_pain, pcr_change_3min, atm_pcr_change_3min,
                                ema_trigger.replace("EMA9_20_3MIN", ema_strategy_name)
                            )
                            enter_trade(ema_strategy_name, trade)
                    last_ema9_20_entry_time = now

                elif ema_signal == "BUY PE" and put_price is not None:
                    for ema_strategy_name in [EMA9_20_NAME, EMA9_20_C_NAME, EMA9_20_D_NAME]:
                        if ema_strategy_name not in open_trades and ema_strategy_name not in exited_strategies:
                            trade = create_trade(
                                ema_strategy_name, "BUY PE", atm_pe_symbol, atm_pe_token, put_price, time_str,
                                nifty, pcr, atm_pcr, max_pain, pcr_change_3min, atm_pcr_change_3min,
                                ema_trigger.replace("EMA9_20_3MIN", ema_strategy_name)
                            )
                            enter_trade(ema_strategy_name, trade)
                    last_ema9_20_entry_time = now

            # 11) ORB family entry
            # ORB_CLASSIC = current midpoint/2X target
            # ORB_EMA9_TRAIL = SL 5 + EMA9 trail, no target
            # ORB_HYBRID = SL 5, +10 breakeven, +15 EMA9 trail
            if entry_allowed:
                orb_signal, orb_trigger, orb_levels = get_orb_signal(nifty, now)

                if orb_signal == "BUY CE" and call_price is not None and orb_levels:
                    for orb_strategy_name in [ORB_NAME, ORB_EMA9_TRAIL_NAME, ORB_HYBRID_NAME]:
                        if orb_strategy_name not in open_trades and orb_strategy_name not in exited_strategies:
                            trade = create_trade(
                                orb_strategy_name, "BUY CE", atm_ce_symbol, atm_ce_token, call_price, time_str,
                                nifty, pcr, atm_pcr, max_pain, pcr_change_3min, atm_pcr_change_3min,
                                orb_trigger.replace("ORB", orb_strategy_name)
                            )
                            trade["orb_high"] = orb_levels["high"]
                            trade["orb_low"] = orb_levels["low"]
                            trade["orb_mid"] = orb_levels["mid"]
                            trade["orb_target"] = round(orb_levels["high"] + (orb_levels["height"] * ORB_TARGET_MULTIPLIER), 2)
                            enter_trade(orb_strategy_name, trade)
                    last_orb_trade_date = now.strftime("%Y-%m-%d")

                elif orb_signal == "BUY PE" and put_price is not None and orb_levels:
                    for orb_strategy_name in [ORB_NAME, ORB_EMA9_TRAIL_NAME, ORB_HYBRID_NAME]:
                        if orb_strategy_name not in open_trades and orb_strategy_name not in exited_strategies:
                            trade = create_trade(
                                orb_strategy_name, "BUY PE", atm_pe_symbol, atm_pe_token, put_price, time_str,
                                nifty, pcr, atm_pcr, max_pain, pcr_change_3min, atm_pcr_change_3min,
                                orb_trigger.replace("ORB", orb_strategy_name)
                            )
                            trade["orb_high"] = orb_levels["high"]
                            trade["orb_low"] = orb_levels["low"]
                            trade["orb_mid"] = orb_levels["mid"]
                            trade["orb_target"] = round(orb_levels["low"] - (orb_levels["height"] * ORB_TARGET_MULTIPLIER), 2)
                            enter_trade(orb_strategy_name, trade)
                    last_orb_trade_date = now.strftime("%Y-%m-%d")

            # 12) Strategy-11 Bollinger Band Mean Reversion entry
            if entry_allowed and BOLLINGER_NAME not in open_trades and BOLLINGER_NAME not in exited_strategies:
                bb_signal, bb_trigger, bb_meta = get_bollinger_signal()

                if bb_signal == "BUY CE" and call_price is not None:
                    trade = create_trade(
                        BOLLINGER_NAME, "BUY CE", atm_ce_symbol, atm_ce_token, call_price, time_str,
                        nifty, pcr, atm_pcr, max_pain, pcr_change_3min, atm_pcr_change_3min, bb_trigger
                    )
                    if bb_meta:
                        trade.update(bb_meta)
                    enter_trade(BOLLINGER_NAME, trade)

                elif bb_signal == "BUY PE" and put_price is not None:
                    trade = create_trade(
                        BOLLINGER_NAME, "BUY PE", atm_pe_symbol, atm_pe_token, put_price, time_str,
                        nifty, pcr, atm_pcr, max_pain, pcr_change_3min, atm_pcr_change_3min, bb_trigger
                    )
                    if bb_meta:
                        trade.update(bb_meta)
                    enter_trade(BOLLINGER_NAME, trade)

            # 13) Strategy-12 MACD Histogram Squeeze entry
            if entry_allowed and MACD_SQUEEZE_NAME not in open_trades and MACD_SQUEEZE_NAME not in exited_strategies:
                macd_signal, macd_trigger = get_macd_squeeze_signal()

                if macd_signal == "BUY CE" and call_price is not None:
                    trade = create_trade(
                        MACD_SQUEEZE_NAME, "BUY CE", atm_ce_symbol, atm_ce_token, call_price, time_str,
                        nifty, pcr, atm_pcr, max_pain, pcr_change_3min, atm_pcr_change_3min, macd_trigger
                    )
                    enter_trade(MACD_SQUEEZE_NAME, trade)

                elif macd_signal == "BUY PE" and put_price is not None:
                    trade = create_trade(
                        MACD_SQUEEZE_NAME, "BUY PE", atm_pe_symbol, atm_pe_token, put_price, time_str,
                        nifty, pcr, atm_pcr, max_pain, pcr_change_3min, atm_pcr_change_3min, macd_trigger
                    )
                    enter_trade(MACD_SQUEEZE_NAME, trade)

            # 14) Strategy-13 VWAP False Break entry
            if entry_allowed and VWAP_FALSE_BREAK_NAME not in open_trades and VWAP_FALSE_BREAK_NAME not in exited_strategies and vwap_false_break_cooldown_ok(now):
                vwap_fb_signal, vwap_fb_trigger, vwap_fb_meta = get_vwap_false_break_signal(nifty)

                if vwap_fb_signal == "BUY CE" and call_price is not None:
                    trade = create_trade(
                        VWAP_FALSE_BREAK_NAME, "BUY CE", atm_ce_symbol, atm_ce_token, call_price, time_str,
                        nifty, pcr, atm_pcr, max_pain, pcr_change_3min, atm_pcr_change_3min, vwap_fb_trigger
                    )
                    if vwap_fb_meta:
                        trade.update(vwap_fb_meta)
                    enter_trade(VWAP_FALSE_BREAK_NAME, trade)
                    last_vwap_false_break_entry_time = now

                elif vwap_fb_signal == "BUY PE" and put_price is not None:
                    trade = create_trade(
                        VWAP_FALSE_BREAK_NAME, "BUY PE", atm_pe_symbol, atm_pe_token, put_price, time_str,
                        nifty, pcr, atm_pcr, max_pain, pcr_change_3min, atm_pcr_change_3min, vwap_fb_trigger
                    )
                    if vwap_fb_meta:
                        trade.update(vwap_fb_meta)
                    enter_trade(VWAP_FALSE_BREAK_NAME, trade)
                    last_vwap_false_break_entry_time = now

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

        # Update 1-minute PCR sample after logic
        if sample_due:
            sample_pcr = pcr
            sample_atm_pcr = atm_pcr
            last_pcr_sample_time = now

        time.sleep(SLEEP_SECONDS)

    except Exception as e:
        print("MAIN LOOP ERROR:", e)
        log_error(str(e))
        send_telegram(f"❌ MAIN LOOP ERROR\n{e}")
        relogin_if_needed("MAIN LOOP ERROR")
        time.sleep(5)
