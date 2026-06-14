# -*- coding: utf-8 -*-
import sys
import os
import time
import threading
import requests
import telebot
import json
import hmac
import hashlib
import pandas as pd
import pandas_ta as ta
import yfinance as yf
from telebot import types

# --- НАСТРОЙКИ TELEGRAM & OLLAMA ---
TELEGRAM_TOKEN = "8910073227:AAGnArJk_E8ccYGCraIo6TWrINceOHBcU1k"
CHAT_ID = "1084140256"
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "llama3:8b"
DEPOSIT = 100.0         

# --- НАСТРОЙКИ API КЛЮЧЕЙ BYBIT ---
BYBYT_API_KEY = "ok9AZsJPZcSRucQUZc"
BYBYT_API_SECRET = "3LjaSP7lErsqTKvWOfGgl02Y9QzW3y7xz7kA"

bot = telebot.TeleBot(TELEGRAM_TOKEN)
is_trading = False  
trading_thread = None

def get_market_data_with_indicators(coin_ticker):
    try:
        ticker = yf.Ticker(f"{coin_ticker}-USD")
        df = ticker.history(period="1mo", interval="1h")
        if df is not None and not df.empty:
            df['rsi'] = ta.rsi(df['Close'], length=14)
            current_price = round(float(df['Close'].iloc[-1]), 2)
            current_rsi = round(float(df['rsi'].iloc[-1]), 2) if not pd.isna(df['rsi'].iloc[-1]) else 50.0
            resistance_4y = round(float(df['Close'].quantile(0.85)), 2)
            support_4y = round(float(df['Close'].quantile(0.15)), 2)
            return {"price": current_price, "rsi": current_rsi, "support": support_4y, "resistance": resistance_4y}
    except Exception as e:
        print(f"Ошибка индикаторов для {coin_ticker}: {e}")
    return {"price": 0.0, "rsi": 50.0, "support": 0.0, "resistance": 0.0}

def send_order_to_bybit(symbol, side, qty, leverage, sl, tp):
    base_url = "https://bybit.com"
    timestamp = str(int(time.time() * 1000))
    recv_window = "5000"
    payload = {
        "category": "linear", "symbol": symbol, "side": side.capitalize(),
        "orderType": "Market", "qty": str(qty), "timeInForce": "GTC",
        "stopLoss": str(sl), "takeProfit": str(tp), "positionIdx": 0
    }
    try:
        lev_url = "https://bybit.com"
        lev_payload = {"category": "linear", "symbol": symbol, "buyLeverage": str(leverage), "sellLeverage": str(leverage)}
        requests.post(lev_url, json=lev_payload, timeout=5)
    except: pass
    req_body = json.dumps(payload)
    signature_payload = timestamp + BYBYT_API_KEY + recv_window + req_body
    signature = hmac.new(bytes(BYBYT_API_SECRET, "utf-8"), bytes(signature_payload, "utf-8"), hashlib.sha256).hexdigest()
    headers = {
        "X-BBI-API-KEY": BYBYT_API_KEY, "X-BBI-API-SIGN": signature,
        "X-BBI-API-TIMESTAMP": timestamp, "X-BBI-API-RECEIVE-WINDOW": recv_window,
        "Content-Type": "application/json"
    }
    try:
        res = requests.post(base_url, data=req_body, headers=headers, timeout=5).json()
        if res.get("retCode") == 0: return True
    except: pass
    return False

def ask_ollama_sniper_mode(btc, eth, sol):
    prompt = (
        f"Ты — ИИ-снайпер криптофонда, торгующий по книгам Элдера и Мёрфи. Баланс: {DEPOSIT} USD.\n"
        f"Текущие показатели рынка:\n"
        f"- BTC: Цена={btc['price']} USDT, RSI={btc['rsi']}, Поддержка={btc['support']}, Сопротивление={btc['resistance']}\n"
        f"- ETH: Цена={eth['price']} USDT, RSI={eth['rsi']}, Поддержка={eth['support']}, Сопротивление={eth['resistance']}\n"
        f"- SOL: Цена={sol['price']} USDT, RSI={sol['rsi']}, Поддержка={sol['support']}, Сопротивление={sol['resistance']}\n\n"
        f"СТРОГИЕ ПРАВИЛА АНАЛИЗА:\n"
        f"Входи в LONG только если цена у поддержки и RSI перепродан (<35). "
        f"Входи в SHORT только если цена у сопротивления и RSI перекуплен (>65).\n"
        f"Если рынок стоит на месте и сильного сигнала из книг НЕТ — отвечай строго HOLD.\n\n"
        f"Ответь строго в формате JSON, без лишнего текста и без знаков ```. Формат:\n"
        f"{{\n"
        f"  \"eth\": {{\"decision\": \"LONG\"/\"SHORT\"/\"HOLD\", \"margin\": 20.0, \"leverage\": 10, \"reason\": \"почему\"}},\n"
        f"  \"sol\": {{\"decision\": \"LONG\"/\"SHORT\"/\"HOLD\", \"margin\": 15.0, \"leverage\": 10, \"reason\": \"почему\"}}\n"
        f"}}\n"
    )
    payload = {"model": OLLAMA_MODEL, "prompt": prompt, "stream": False, "format": "json"}
    try:
        res = requests.post(OLLAMA_URL, json=payload, timeout=120)
        if res.status_code == 200:
            txt = res.json().get("response", "{}").strip().replace('```json', '').replace('```', '').strip()
            return json.loads(txt)
    except Exception as e: print(f"Ошибка Ollama: {e}")
    return None

def process_flexible_signal(symbol, current_price, ai_data):
    direction = ai_data.get("decision", "HOLD").lower()
    ai_reason = ai_data.get("reason", "HOLD.")
    
    if direction in ["long", "short"] and current_price > 0:
        try: margin = float(ai_data.get("margin", 10.0))
        except: margin = 10.0
        try: leverage = int(ai_data.get("leverage", 5))
        except: leverage = 5
        
        # ЖЕСТКАЯ МАТЕМАТИЧЕСКАЯ ЗАЩИТА PYTHON (Исключает ошибки ИИ)
        if direction == "long":
            sl = current_price * 0.985  # Стоп строго ниже цены на 1.5%
            tp = current_price * 1.045  # Тейк строго выше цены на 4.5% (Соотношение рисков 1:3 по Мёрфи)
        else:
            sl = current_price * 1.015  # Стоп строго выше цены на 1.5%
            tp = current_price * 0.955  # Тейк строго ниже цены на 4.5%
            
        margin = min(DEPOSIT, margin)
        leverage = min(20, max(1, leverage))
        total_volume = margin * leverage
        qty = round(total_volume / current_price, 3)
        if qty <= 0: qty = 0.001
        side = "Buy" if direction == "long" else "Sell"
        
        send_order_to_bybit(symbol, side, qty, leverage, round(sl,2), round(tp,2))
        
        msg = (
            f"🎯 **СНАЙПЕРСКИЙ СИГНАЛ: {symbol}** 🚨\n\n"
            f"Направление: **{direction.upper()}**\n"
            f"Вход (Живой график): `{current_price:,.2f} USDT`\n"
            f"🛑 Стоп-лосс (Жесткий расчет): `{sl:,.2f} USDT`\n"
            f"🎯 Тейк-профит (Жесткий расчет): `{tp:,.2f} USDT`\n\n"
            f"📐 **Рекомендованные риски ИИ:**\n"
            f"Выделенная маржа: *{margin:.2f} USDT* | Плечо: *x{leverage}*\n"
            f"Общий объем позиции: *{total_volume:.2f} USDT*\n\n"
            f"🧠 **Техническое обоснование по книгам:**\n_{ai_reason}_"
        )
        bot.send_message(CHAT_ID, msg, parse_mode="Markdown")
    else:
        print(f"[{time.strftime('%H:%M:%S')}] {symbol}: Паттерн отсутствует (HOLD). Логика ИИ: {ai_reason}")

def trading_loop():
    print("🚀 Режим Снайпера v3.4 активирован. Математический калькулятор рисков включен...")
    while is_trading:
        btc = get_market_data_with_indicators("BTC")
        eth = get_market_data_with_indicators("ETH")
        sol = get_market_data_with_indicators("SOL")
        if btc["price"] > 0 and eth["price"] > 0 and sol["price"] > 0:
            ai = ask_ollama_sniper_mode(btc, eth, sol)
            if ai:
                if "eth" in ai: process_flexible_signal("ETHUSDT", eth["price"], ai["eth"])
                if "sol" in ai: process_flexible_signal("SOLUSDT", sol["price"], ai["sol"])
        for _ in range(60):
            if not is_trading: break
            time.sleep(1)

def get_main_keyboard():
    m = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    m.add(types.KeyboardButton("▶️ Включить Режим Снайпера"), types.KeyboardButton("🛑 Остановить бота"), types.KeyboardButton("📊 Статус ПК"))
    return m

@bot.message_handler(commands=["start"])
def welcome(m): bot.send_message(CHAT_ID, f"💻 Робот-Снайпер v3.4 готов к работе. Математика SL/TP защищена.", reply_markup=get_main_keyboard())

@bot.message_handler(func=lambda m: str(m.chat.id) == CHAT_ID)
def handle_text(m):
    global is_trading, trading_thread
    if m.text == "▶️ Включить Режим Снайпера":
        if not is_trading:
            is_trading = True
            trading_thread = threading.Thread(target=trading_loop)
            trading_thread.start()
            bot.send_message(CHAT_ID, "🎯 Режим Снайпера ЗАПУЩЕН. Математический калькулятор заблокирует любые ошибки ИИ.")
    elif m.text == "🛑 Остановить бота":
        if is_trading: is_trading = False; bot.send_message(CHAT_ID, "⏳ Снайпер ушел в засаду (Остановлен).")
    elif m.text == "📊 Статус ПК":
        btc = get_market_data_with_indicators("BTC")
        bot.send_message(CHAT_ID, f"ℹ️ **Статус:** {'🟢 ИЩУ СИЛЬНЫЙ ВХОД' if is_trading else '🔴 В ОЖИДАНИИ'}\nДепозит: `{DEPOSIT}$` \nЖивой BTC: `{btc['price']:,.2f} USD` (RSI: {btc['rsi']})")

if __name__ == "__main__":
    print("🚀 Снайперский ИИ-робот успешно запущен!")
    try: bot.send_message(CHAT_ID, f"🔌 Поток котировок Yahoo Finance активен. Нажмите /start.", reply_markup=get_main_keyboard())
    except: pass
    bot.infinity_polling()
