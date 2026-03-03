# daily_report/report.py
import os
from datetime import datetime, timezone, timedelta
import requests
import yfinance as yf
from linebot import LineBotApi
from linebot.models import TextSendMessage

TZ_TAIPEI = timezone(timedelta(hours=8))

def yf_close(ticker: str):
    df = yf.download(ticker, period="5d", interval="1d", progress=False)
    if df is None or df.empty:
        return None
    # 取最後一筆收盤
    return float(df["Close"].dropna().iloc[-1])

def get_market_snapshot():
    # 指數（你可加 SOX: ^SOX）
    data = {
        "DJI": yf_close("^DJI"),
        "SPX": yf_close("^GSPC"),
        "IXIC": yf_close("^IXIC"),
        "SOX": yf_close("^SOX"),
        # 商品（可依你常用調整）
        "GOLD": yf_close("GC=F"),
        "WTI": yf_close("CL=F"),
        "DXY": yf_close("DX-Y.NYB"),
        # 10Y（yfinance 用 ^TNX 是「殖利率*10」，所以要 /10）
        "US10Y": (yf_close("^TNX") / 10.0) if yf_close("^TNX") else None,
    }
    return data

def call_llm(prompt: str) -> str:
    # TODO: 替換成你自己的 OpenClaw / GPT 呼叫
    # 例：OpenClaw endpoint
    endpoint = os.environ["MODEL_ENDPOINT"]
    api_key = os.environ.get("MODEL_API_KEY")

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    r = requests.post(endpoint, json={"prompt": prompt}, headers=headers, timeout=60)
    r.raise_for_status()
    out = r.json()

    # 依你回傳格式調整
    return out["text"]

def push_line(text: str):
    token = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
    user_id = os.environ["LINE_USER_ID"]
    line_bot_api = LineBotApi(token)

    # LINE 單則 5000 字限制：太長就切兩段
    if len(text) <= 4800:
        line_bot_api.push_message(user_id, TextSendMessage(text=text))
    else:
        part1 = text[:4800]
        part2 = text[4800:]
        line_bot_api.push_message(user_id, [TextSendMessage(text=part1), TextSendMessage(text=part2)])

def main():
    now = datetime.now(TZ_TAIPEI)
    snap = get_market_snapshot()

    # 缺值保護：有缺就標註，避免你品牌日報發錯數字
    missing = [k for k, v in snap.items() if v is None]
    missing_note = f"\n（⚠️資料缺漏：{', '.join(missing)}）" if missing else ""

    from daily_report.prompt import build_prompt
    prompt = build_prompt(now, snap, missing_note)

    report = call_llm(prompt)
    push_line(report)

if __name__ == "__main__":
    main()
