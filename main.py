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

# ============================
# TELEGRAM CONFIG
# ============================
TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# ============================
# GLOBAL VARIABLES
# ============================
smartApi = None
last_heartbeat_hour = -1

prev_pcr = None
prev_atm_pcr = None

open_trade = None

PAPER_FILE = "paper_trades.csv"

GOOGLE_SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME")
GOOGLE_CREDENTIALS_BASE64 = os.getenv("GOOGLE_CREDENTIALS_BASE64")
google_sheet = None

# ============================
# FAST TEST STRATEGY SETTINGS
# ============================
SLEEP_SECONDS = 60

STOPLOSS_POINTS = 5
TARGET_POINTS = 8

ATM_PCR_CALL_BUY = 1.02
ATM_PCR_PUT_BUY = 0.98

MIN_PCR_CHANGE = 0.005


# ============================
# TELEGRAM SEND
# ============================
def send_telegram(msg):
    try:
        if not TOKEN or not CHAT_ID:
            print("Telegram TOKEN or CHAT_ID missing")
            return

        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"

        payload = {
            "chat_id": CHAT_ID,
            "text": msg
        }

        response = requests.post(url, data=payload, timeout=10)

        print("TELEGRAM STATUS:", response.status_code)
        print("TELEGRAM RESPONSE:", response.text)

    except Exception as e:
        print("Telegram Error:", e)


# ============================
# ERROR LOGGER
# ============================
def log_error(error_msg):
    try:
        with open("error_log.txt", "a") as f:
            ist = pytz.timezone("Asia/Kolkata")
            now = datetime.now(ist)
            f.write(f"\n[{now.strftime('%Y-%m-%d %H:%M:%S')}] {error_msg}\n")
    except:
        pass


# ============================
# GOOGLE SHEET INIT
# ============================
def init_google_sheet():
    global google_sheet

    try:
        if not GOOGLE_SHEET_NAME or not GOOGLE_CREDENTIALS_BASE64:
            print("Google Sheet variables missing")
            send_telegram("⚠️ GOOGLE SHEET VARIABLES MISSING")
            return False

        creds_json = base64.b64decode(GOOGLE_CREDENTIALS_BASE64).decode("utf-8")
        creds_dict = json.loads(creds_json)

        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ]

        creds = Credentials.from_service_account_info(
            creds_dict,
            scopes=scopes
        )

        client = gspread.authorize(creds)
        google_sheet = client.open(GOOGLE_SHEET_NAME).sheet1

        print("GOOGLE SHEET CONNECTED")
        send_telegram("✅ GOOGLE SHEET CONNECTED SUCCESSFULLY")
        return True

    except Exception as e:
        print("Google Sheet Init Error:", e)
        send_telegram(f"❌ GOOGLE SHEET INIT ERROR\n{e}")
        log_error(str(e))
        return False


# ============================
# PAPER TRADE FILE INIT
# ============================
def init_paper_file():
    if not os.path.exists(PAPER_FILE):
        with open(PAPER_FILE, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "Entry Time",
                "Exit Time",
                "Trade Type",
                "Symbol",
                "Token",
                "Entry Price",
                "Exit Price",
                "Points",
                "Result",
                "Reason",
                "NIFTY Entry",
                "NIFTY Exit",
                "PCR Entry",
                "PCR Exit",
                "ATM PCR Entry",
                "ATM PCR Exit",
                "Max Pain Entry",
                "Max Pain Exit"
            ])


# ============================
# SAVE PAPER TRADE CSV
# ============================
def save_paper_trade(trade, exit_time, exit_price, reason, nifty_exit, pcr_exit, atm_pcr_exit, max_pain_exit):
    try:
        points = round(exit_price - trade["entry_price"], 2)

        result = "PROFIT" if points > 0 else "LOSS" if points < 0 else "NO PROFIT NO LOSS"

        with open(PAPER_FILE, "a", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                trade["entry_time"],
                exit_time,
                trade["trade_type"],
                trade["symbol"],
                trade["token"],
                trade["entry_price"],
                exit_price,
                points,
                result,
                reason,
                trade["nifty_entry"],
                nifty_exit,
                trade["pcr_entry"],
                pcr_exit,
                trade["atm_pcr_entry"],
                atm_pcr_exit,
                trade["max_pain_entry"],
                max_pain_exit
            ])

    except Exception as e:
        print("Paper Trade Save Error:", e)
        log_error(str(e))


# ============================
# SAVE GOOGLE SHEET TRADE
# ============================
def save_google_trade(trade, exit_time, exit_price, reason, nifty_exit, pcr_exit, atm_pcr_exit, max_pain_exit):
    try:
        if google_sheet is None:
            print("Google Sheet not connected")
            return

        points = round(exit_price - trade["entry_price"], 2)

        result = "PROFIT" if points > 0 else "LOSS" if points < 0 else "NO PROFIT NO LOSS"

        row = [
            trade["entry_time"],
            exit_time,
            trade["trade_type"],
            trade["symbol"],
            trade["token"],
            trade["entry_price"],
            exit_price,
            points,
            result,
            reason,
            trade["nifty_entry"],
            nifty_exit,
            trade["pcr_entry"],
            pcr_exit,
            trade["atm_pcr_entry"],
            atm_pcr_exit,
            trade["max_pain_entry"],
            max_pain_exit
        ]

        google_sheet.append_row(row, value_input_option="USER_ENTERED")
        print("Trade saved to Google Sheet")

    except Exception as e:
        print("Google Sheet Save Error:", e)
        send_telegram(f"❌ GOOGLE SHEET SAVE ERROR\n{e}")
        log_error(str(e))


# ============================
# LOGIN FUNCTION
# ============================
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
            send_telegram("🌅 PCR SYSTEM STARTED SUCCESSFULLY")
            return True

        else:
            print("LOGIN FAILED:", data)
            send_telegram(f"❌ LOGIN FAILED\n{data}")
            return False

    except Exception as e:
        print("LOGIN ERROR:", e)
        send_telegram(f"❌ LOGIN ERROR\n{e}")
        return False


# ============================
# SAFE LTP CALL
# ============================
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


# ============================
# START BOT
# ============================
init_paper_file()
init_google_sheet()

if not login():
    exit()


# ============================
# SYMBOL MASTER
# ============================
try:
    url = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"

    symbols = requests.get(
        url,
        headers={"Cache-Control": "no-cache"},
        timeout=20
    ).json()

except Exception as e:
    print("SYMBOL MASTER ERROR:", e)
    send_telegram(f"❌ SYMBOL MASTER ERROR\n{e}")
    exit()


# ============================
# MAIN LOOP
# ============================
while True:

    try:
        ist = pytz.timezone("Asia/Kolkata")
        now = datetime.now(ist)
        time_str = now.strftime("%Y-%m-%d %H:%M:%S")

        # ============================
        # FIXED HEARTBEAT
        # ============================
        if now.hour % 2 == 0 and last_heartbeat_hour != now.hour:
            send_telegram(f"✅ BOT RUNNING HEALTHY\nTime: {time_str}")
            last_heartbeat_hour = now.hour

        # ============================
        # MODE
        # ============================
        if now.weekday() >= 5:
            mode = "WEEKEND"
        elif now.hour < 9 or now.hour > 15:
            mode = "AFTER MARKET"
        elif now.hour == 15 and now.minute > 30:
            mode = "AFTER MARKET"
        elif now.hour == 9 and now.minute < 15:
            mode = "PRE MARKET"
        else:
            mode = "LIVE MARKET"

        # ============================
        # NIFTY
        # ============================
        nifty = safe_ltp("NSE", "NIFTY", "26000")

        if nifty is None:
            print("NIFTY FETCH FAILED")
            time.sleep(5)
            continue

        # ============================
        # OPTION FILTER
        # ============================
        opts = [
            s for s in symbols
            if s.get("exch_seg") == "NFO"
            and "NIFTY" in s.get("symbol", "")
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
            except:
                pass

        exp_list = sorted(exp_list, key=lambda x: x[1])

        if not exp_list:
            print("NO VALID EXPIRY FOUND")
            time.sleep(SLEEP_SECONDS)
            continue

        expiry = exp_list[0][0]

        filtered = [
            o for o in opts
            if o["expiry"] == expiry
        ]

        atm = round(nifty / 50) * 50

        strike_map = {
            o["token"]: int(float(o["strike"]) / 100)
            for o in filtered
        }

        tokens = [
            o["token"]
            for o in filtered
            if atm - 500 <= strike_map[o["token"]] <= atm + 500
        ]

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
            data = smartApi.getMarketData(
                "FULL",
                {"NFO": tokens[i:i + 50]}
            )

            if data and data.get("status"):
                fetched += data["data"]["fetched"]

        total_ce = 0
        total_pe = 0

        atm_ce = 0
        atm_pe = 0

        strike_data = {}

        for item in fetched:
            sym = item["tradingSymbol"]
            oi = item.get("opnInterest", 0)
            tk = item["symbolToken"]

            strike = strike_map.get(tk)

            if strike is None:
                continue

            if strike not in strike_data:
                strike_data[strike] = {
                    "ce": 0,
                    "pe": 0
                }

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

        # ============================
        # PCR
        # ============================
        pcr = total_pe / total_ce if total_ce else 0
        atm_pcr = atm_pe / atm_ce if atm_ce else 0

        # ============================
        # MAX PAIN
        # ============================
        if not strike_data:
            print("NO STRIKE DATA")
            time.sleep(SLEEP_SECONDS)
            continue

        max_pain = min(
            strike_data,
            key=lambda x: sum(
                (x - k) * v["ce"] if x > k else (k - x) * v["pe"]
                for k, v in strike_data.items()
            )
        )

        # ============================
        # OUTPUT SAME FORMAT
        # ============================
        print("\n============================")
        print("Time:", time_str)
        print("Mode:", mode)
        print("============================")
        print("NIFTY:", round(nifty, 2))
        print("PCR:", round(pcr, 4), "| ATM PCR:", round(atm_pcr, 4))
        print("Max Pain:", max_pain)

        # ============================
        # PAPER TRADING ONLY LIVE MARKET
        # ============================
        if mode == "LIVE MARKET":

            call_price = None
            put_price = None

            if atm_ce_symbol and atm_ce_token:
                call_price = safe_ltp("NFO", atm_ce_symbol, atm_ce_token)

            if atm_pe_symbol and atm_pe_token:
                put_price = safe_ltp("NFO", atm_pe_symbol, atm_pe_token)

            # ============================
            # EXIT LOGIC
            # ============================
            if open_trade is not None:

                if open_trade["trade_type"] == "BUY CE":
                    current_price = call_price
                else:
                    current_price = put_price

                if current_price is not None:

                    points = round(current_price - open_trade["entry_price"], 2)

                    exit_reason = None

                    if points <= -STOPLOSS_POINTS:
                        exit_reason = "STOPLOSS HIT"

                    elif points >= TARGET_POINTS:
                        exit_reason = "TARGET HIT"

                    elif open_trade["trade_type"] == "BUY CE" and atm_pcr < 1.00:
                        exit_reason = "CALL EXIT - ATM PCR WEAK"

                    elif open_trade["trade_type"] == "BUY PE" and atm_pcr > 1.00:
                        exit_reason = "PUT EXIT - ATM PCR WEAK"

                    if exit_reason:
                        save_paper_trade(
                            open_trade,
                            time_str,
                            round(current_price, 2),
                            exit_reason,
                            round(nifty, 2),
                            round(pcr, 4),
                            round(atm_pcr, 4),
                            max_pain
                        )

                        save_google_trade(
                            open_trade,
                            time_str,
                            round(current_price, 2),
                            exit_reason,
                            round(nifty, 2),
                            round(pcr, 4),
                            round(atm_pcr, 4),
                            max_pain
                        )

                        send_telegram(
                            f"🚪 PAPER TRADE EXIT\n"
                            f"Type: {open_trade['trade_type']}\n"
                            f"Symbol: {open_trade['symbol']}\n"
                            f"Entry: {open_trade['entry_price']}\n"
                            f"Exit: {round(current_price, 2)}\n"
                            f"Points: {points}\n"
                            f"Reason: {exit_reason}\n"
                            f"Time: {time_str}"
                        )

                        open_trade = None

            # ============================
            # ENTRY LOGIC - FAST TEST MODE
            # ============================
            if open_trade is None and prev_pcr is not None and prev_atm_pcr is not None:

                pcr_change = pcr - prev_pcr

                # BUY CE LOGIC
                if (
                    pcr_change >= MIN_PCR_CHANGE
                    and atm_pcr >= ATM_PCR_CALL_BUY
                    and call_price is not None
                ):

                    open_trade = {
                        "entry_time": time_str,
                        "trade_type": "BUY CE",
                        "symbol": atm_ce_symbol,
                        "token": atm_ce_token,
                        "entry_price": round(call_price, 2),
                        "nifty_entry": round(nifty, 2),
                        "pcr_entry": round(pcr, 4),
                        "atm_pcr_entry": round(atm_pcr, 4),
                        "max_pain_entry": max_pain
                    }

                    send_telegram(
                        f"🟢 PAPER BUY CE ALERT\n"
                        f"Symbol: {atm_ce_symbol}\n"
                        f"Entry Price: {round(call_price, 2)}\n"
                        f"NIFTY: {round(nifty, 2)}\n"
                        f"PCR: {round(pcr, 4)}\n"
                        f"ATM PCR: {round(atm_pcr, 4)}\n"
                        f"Max Pain: {max_pain}\n"
                        f"Reason: TEST MODE FAST ENTRY\n"
                        f"Time: {time_str}"
                    )

                # BUY PE LOGIC
                elif (
                    pcr_change <= -MIN_PCR_CHANGE
                    and atm_pcr <= ATM_PCR_PUT_BUY
                    and put_price is not None
                ):

                    open_trade = {
                        "entry_time": time_str,
                        "trade_type": "BUY PE",
                        "symbol": atm_pe_symbol,
                        "token": atm_pe_token,
                        "entry_price": round(put_price, 2),
                        "nifty_entry": round(nifty, 2),
                        "pcr_entry": round(pcr, 4),
                        "atm_pcr_entry": round(atm_pcr, 4),
                        "max_pain_entry": max_pain
                    }

                    send_telegram(
                        f"🔴 PAPER BUY PE ALERT\n"
                        f"Symbol: {atm_pe_symbol}\n"
                        f"Entry Price: {round(put_price, 2)}\n"
                        f"NIFTY: {round(nifty, 2)}\n"
                        f"PCR: {round(pcr, 4)}\n"
                        f"ATM PCR: {round(atm_pcr, 4)}\n"
                        f"Max Pain: {max_pain}\n"
                        f"Reason: TEST MODE FAST ENTRY\n"
                        f"Time: {time_str}"
                    )

        # ============================
        # FORCE EXIT AFTER MARKET
        # ============================
        elif mode == "AFTER MARKET" and open_trade is not None:

            exit_price = safe_ltp("NFO", open_trade["symbol"], open_trade["token"])

            if exit_price is not None:
                points = round(exit_price - open_trade["entry_price"], 2)

                save_paper_trade(
                    open_trade,
                    time_str,
                    round(exit_price, 2),
                    "MARKET CLOSED EXIT",
                    round(nifty, 2),
                    round(pcr, 4),
                    round(atm_pcr, 4),
                    max_pain
                )

                save_google_trade(
                    open_trade,
                    time_str,
                    round(exit_price, 2),
                    "MARKET CLOSED EXIT",
                    round(nifty, 2),
                    round(pcr, 4),
                    round(atm_pcr, 4),
                    max_pain
                )

                send_telegram(
                    f"🚪 PAPER TRADE EXIT\n"
                    f"Type: {open_trade['trade_type']}\n"
                    f"Symbol: {open_trade['symbol']}\n"
                    f"Entry: {open_trade['entry_price']}\n"
                    f"Exit: {round(exit_price, 2)}\n"
                    f"Points: {points}\n"
                    f"Reason: MARKET CLOSED EXIT\n"
                    f"Time: {time_str}"
                )

                open_trade = None

        # ============================
        # UPDATE PREVIOUS VALUES
        # ============================
        prev_pcr = pcr
        prev_atm_pcr = atm_pcr

    except Exception as e:
        print("MAIN LOOP ERROR:", e)
        log_error(str(e))
        send_telegram(f"❌ MAIN LOOP ERROR\n{e}")
        login()
        time.sleep(5)

    time.sleep(SLEEP_SECONDS)
