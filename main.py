import requests
import time
import json
import csv
import os
from datetime import datetime
import pytz
from SmartApi import SmartConnect
import pyotp

# ===== TELEGRAM CONFIG =====
TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

last_update_id = 0
morning_sent = False

# ===== GLOBAL DATA =====
last_time = ""
last_mode = ""
last_nifty = 0
last_expiry = ""
last_pcr = 0
last_atm_pcr = 0
last_atm = 0
last_max_pain = 0

prev_pcr = None
prev_atm_pcr = None

# ===== PAPER TRADE VARIABLES =====
active_trade = None
trade_entry_price = 0
trade_entry_time = ""
trade_type = ""
trade_strike = 0
trade_qty = 65
trade_token = ""

peak_pcr = 0
lowest_pcr = 0
reversal_count = 0
entry_max_pain = 0

trade_no = 0
total_profit = 0
total_loss = 0
last_heartbeat_hour = -1

# ===== TELEGRAM SEND =====
def send_telegram(msg):
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        requests.get(url, params={"chat_id": CHAT_ID, "text": msg}, timeout=10)
    except Exception as e:
        print("Telegram Error:", e)

# ===== ERROR LOGGER =====
def log_error(error_msg):
    with open("error_log.txt", "a") as f:
        ist = pytz.timezone("Asia/Kolkata")
        now = datetime.now(ist)
        f.write(f"\n[{now.strftime('%Y-%m-%d %H:%M:%S')}] {error_msg}\n")

# ===== SAFE API CALL =====
def safe_ltp(exchange, symbol, token, retry=3):
    for _ in range(retry):
        try:
            data = smartApi.ltpData(exchange, symbol, token)
            if data and data.get("data"):
                return data["data"]["ltp"]
        except Exception as e:
            print("LTP Retry Error:", e)
            log_error(str(e))
            time.sleep(2)
    return None

# ===== LOGIN =====
api_key = os.getenv("API_KEY")
client_id = os.getenv("CLIENT_ID")
password = os.getenv("PASSWORD")
totp_key = os.getenv("TOTP_KEY")

smartApi = SmartConnect(api_key)
totp = pyotp.TOTP(totp_key).now()

if not smartApi.generateSession(client_id, password, totp)['status']:
    print("Login Failed")
    exit()

if smartApi is None:
    print("SmartAPI not initialized")
    exit()

print("Login Success")

send_telegram("🌅 PCR SYSTEM STARTED SUCCESSFULLY")

# ===== SYMBOL MASTER =====
url = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
symbols = requests.get(url, headers={"Cache-Control": "no-cache"}).json()

# ===== MAIN LOOP =====
while True:
    try:
        ist = pytz.timezone("Asia/Kolkata")
        now = datetime.now(ist)
        time_str = now.strftime("%Y-%m-%d %H:%M:%S")

        # ===== HEARTBEAT =====
        if now.minute == 0 and now.second < 5 and now.hour % 2 == 0:
            if last_heartbeat_hour != now.hour:
                send_telegram(f"✅ BOT RUNNING HEALTHY\nTime: {time_str}")
                last_heartbeat_hour = now.hour

        # ===== MODE =====
        if now.weekday() >= 5:
            mode = "WEEKEND (Last Data)"
        elif now.hour < 9 or now.hour > 15:
            mode = "AFTER MARKET (Last Data)"
        else:
            mode = "LIVE MARKET"

        nifty = safe_ltp("NSE", "NIFTY", "26000")
        if nifty is None:
            continue

        opts = [
            s for s in symbols
            if s['exch_seg'] == "NFO"
            and "NIFTY" in s.get('symbol', '')
            and ("CE" in s.get('symbol', '') or "PE" in s.get('symbol', ''))
        ]

        expiries = list(set([o['expiry'] for o in opts]))
        exp_list = []

        today = now.date()
        for e in expiries:
            try:
                d = datetime.strptime(e, "%d%b%Y").date()
                if d >= today:
                    exp_list.append((e, d))
            except:
                pass

        exp_list = sorted(exp_list, key=lambda x: x[1])
        expiry = exp_list[0][0]

        filtered = [o for o in opts if o['expiry'] == expiry]

        atm = round(nifty / 50) * 50

        strike_map = {o['token']: int(float(o['strike']) / 100) for o in filtered}

        tokens = [
            o['token']
            for o in filtered
            if atm - 500 <= strike_map[o['token']] <= atm + 500
        ]

        fetched = []

        for i in range(0, len(tokens), 50):
            data = smartApi.getMarketData("FULL", {"NFO": tokens[i:i+50]})
            if data['status']:
                fetched += data['data']['fetched']

        total_ce = total_pe = 0
        atm_ce = atm_pe = 0
        strike_data = {}

        for item in fetched:
            sym = item['tradingSymbol']
            oi = item['opnInterest']
            tk = item['symbolToken']

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
            else:
                total_pe += oi
                strike_data[strike]["pe"] += oi
                if strike == atm:
                    atm_pe = oi

        pcr = total_pe / total_ce if total_ce else 0
        atm_pcr = atm_pe / atm_ce if atm_ce else 0

        max_pain = min(
            strike_data,
            key=lambda x: sum(
                (x-k)*v["ce"] if x > k else (k-x)*v["pe"]
                for k, v in strike_data.items()
            )
        )

        print("\n============================")
        print("Time:", time_str)
        print("Mode:", mode)
        print("============================")
        print("NIFTY:", round(nifty, 2))
        print("PCR:", round(pcr, 4), "| ATM PCR:", round(atm_pcr, 4))
        print("Max Pain:", max_pain)

        prev_pcr = pcr
        prev_atm_pcr = atm_pcr
        last_time = time_str
        last_mode = mode
        last_nifty = nifty
        last_expiry = expiry
        last_pcr = pcr
        last_atm_pcr = atm_pcr
        last_atm = atm
        last_max_pain = max_pain

    except Exception as e:
        print("Error:", e)
        log_error(str(e))

    time.sleep(180)