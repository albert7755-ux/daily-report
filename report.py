# -*- coding: utf-8 -*-

import os
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta
from linebot import LineBotApi
from linebot.models import TextSendMessage

TZ_TAIPEI = timezone(timedelta(hours=8))


def _to_float(value):
    """
    把 value（可能是 scalar / Series / DataFrame / numpy）安全轉成 float。
    取「第一個非 NaN 的數字」。
    """
    if value is None:
        return None

    # DataFrame / Series
    if isinstance(value, pd.DataFrame):
        arr = value.to_numpy().reshape(-1)
    elif isinstance(value, pd.Series):
        arr = value.to_numpy().reshape(-1)
    else:
        arr = np.array([value]).reshape(-1)

    # 去掉 NaN / None
    arr = arr[~pd.isna(arr)]
    if arr.size == 0:
        return None

    return float(arr[0])


def yf_close(ticker: str):
    """
    安全抓最近收盤價（最後一筆 Close）。
    不管 yfinance 回來 Close 是 scalar / Series / DataFrame，都會轉成單一 float。
    """
    try:
        df = yf.download(ticker, period="5d", interval="1d", progress=False)
    except Exception as e:
        print(f"[yfinance error] ticker={ticker} err={e}")
        return None

    if df is None or df.empty:
        print(f"[empty] ticker={ticker}")
        return None

    # 取 Close 欄位（可能是 Series 或 DataFrame）
    try:
        close = df["Close"]
    except Exception as e:
        print(f"[no Close] ticker={ticker} columns={df.columns} err={e}")
        return None

    # 取最後一筆（可能是 scalar 或 Series）
    try:
        last = close.iloc[-1]
    except Exception as e:
        print(f"[iloc error] ticker={ticker} err={e}")
        return None

    v = _to_float(last)
    if v is None:
        print(f"[nan close] ticker={ticker} last_type={type(last)}")
    return v


def get_market_snapshot():
    tnx = yf_close("^TNX")  # 10Y yield x10

    return {
        "DJI": yf_close("^DJI"),
        "SPX": yf_close("^GSPC"),
        "IXIC": yf_close("^IXIC"),
        "SOX": yf_close("^SOX"),
        "GOLD": yf_close("GC=F"),
        "WTI": yf_close("CL=F"),
        "DXY": yf_close("DX-Y.NYB"),
        "US10Y": (tnx / 10.0) if tnx is not None else None,
    }


def fnum(x, digits=2, suffix=""):
    if x is None:
        return "N/A"
    return f"{x:,.{digits}f}{suffix}"


def generate_report(snapshot):
    now = datetime.now(TZ_TAIPEI)
    date_str = now.strftime("%Y年%m月%d日")

    text = f"""【{date_str} 財經日報｜測試版】

一、全球市場概覽
• 道瓊：{fnum(snapshot.get("DJI"))}
• 標普500：{fnum(snapshot.get("SPX"))}
• 那斯達克：{fnum(snapshot.get("IXIC"))}
• 費半：{fnum(snapshot.get("SOX"))}
風險提示：市場波動可能放大，分批與部位控管優先。

二、利率與美元
• 10年期美債殖利率：{fnum(snapshot.get("US10Y"), 2, "%")}
• 美元指數：{fnum(snapshot.get("DXY"))}
風險提示：利率與匯率變動可能影響資產評價。

三、商品
• 黃金：{fnum(snapshot.get("GOLD"))}
• WTI 原油：{fnum(snapshot.get("WTI"))}
風險提示：商品受地緣與供需影響大，避免追價。

（✅若你看到這段，代表 Render → 抓資料 → LINE 推播 已打通）
"""
    return text


def push_line(text: str):
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
    user_id = os.environ.get("LINE_USER_ID")

    if not token or not user_id:
        raise ValueError("缺少環境變數：LINE_CHANNEL_ACCESS_TOKEN 或 LINE_USER_ID")

    line_bot_api = LineBotApi(token)

    # LINE 單則上限 5000，保守切 4800
    if len(text) <= 4800:
        line_bot_api.push_message(user_id, TextSendMessage(text=text))
    else:
        line_bot_api.push_message(user_id, [
            TextSendMessage(text=text[:4800]),
            TextSendMessage(text=text[4800:])
        ])


def main():
    snapshot = get_market_snapshot()
    report = generate_report(snapshot)
    push_line(report)


if __name__ == "__main__":
    main()
