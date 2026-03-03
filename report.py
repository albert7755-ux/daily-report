# -*- coding: utf-8 -*-

import os
import requests
import yfinance as yf
from datetime import datetime, timezone, timedelta
from linebot import LineBotApi
from linebot.models import TextSendMessage

# 台北時區
TZ_TAIPEI = timezone(timedelta(hours=8))


# ==============================
# 安全抓收盤價（修正版）
# ==============================
def yf_close(ticker: str):
    df = yf.download(ticker, period="5d", interval="1d", progress=False)

    if df is None or df.empty:
        return None

    close = df["Close"]

    # 有時候會變成 DataFrame（多欄）
    if hasattr(close, "columns"):
        close = close.iloc[:, 0]

    close = close.dropna()

    if close.empty:
        return None

    last_value = close.iloc[-1]

    try:
        return float(last_value)
    except TypeError:
        return float(last_value.item())


# ==============================
# 抓市場資料
# ==============================
def get_market_snapshot():

    tnx = yf_close("^TNX")  # 10Y yield x10

    data = {
        "DJI": yf_close("^DJI"),
        "SPX": yf_close("^GSPC"),
        "IXIC": yf_close("^IXIC"),
        "SOX": yf_close("^SOX"),
        "GOLD": yf_close("GC=F"),
        "WTI": yf_close("CL=F"),
        "DXY": yf_close("DX-Y.NYB"),
        "US10Y": (tnx / 10.0) if tnx is not None else None,
    }

    return data


# ==============================
# 格式化數字
# ==============================
def fnum(x, digits=2, suffix=""):
    if x is None:
        return "N/A"
    return f"{x:,.{digits}f}{suffix}"


# ==============================
# 產生簡易日報（先不用 LLM，測試 LINE 是否正常）
# ==============================
def generate_report(snapshot):

    now = datetime.now(TZ_TAIPEI)
    date_str = now.strftime("%Y年%m月%d日")

    text = f"""【{date_str} 財經日報】

一、全球市場概覽
• 道瓊：{fnum(snapshot["DJI"])}
• 標普500：{fnum(snapshot["SPX"])}
• 那斯達克：{fnum(snapshot["IXIC"])}
• 費半：{fnum(snapshot["SOX"])}

二、利率與美元
• 10年期美債殖利率：{fnum(snapshot["US10Y"], 2, "%")}
• 美元指數：{fnum(snapshot["DXY"])}

三、商品
• 黃金：{fnum(snapshot["GOLD"])}
• WTI 原油：{fnum(snapshot["WTI"])}

（測試版日報成功發送）
"""

    return text


# ==============================
# LINE 推播
# ==============================
def push_line(text):

    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
    user_id = os.environ.get("LINE_USER_ID")

    if not token or not user_id:
        raise ValueError("LINE 環境變數未設定")

    line_bot_api = LineBotApi(token)

    if len(text) <= 4800:
        line_bot_api.push_message(user_id, TextSendMessage(text=text))
    else:
        part1 = text[:4800]
        part2 = text[4800:]
        line_bot_api.push_message(user_id, [
            TextSendMessage(text=part1),
            TextSendMessage(text=part2)
        ])


# ==============================
# 主程式
# ==============================
def main():

    snapshot = get_market_snapshot()
    report = generate_report(snapshot)
    push_line(report)


if __name__ == "__main__":
    main()
