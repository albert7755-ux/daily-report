# -*- coding: utf-8 -*-

import os
import csv
import io
import requests
from datetime import datetime, timezone, timedelta
from linebot import LineBotApi
from linebot.models import TextSendMessage

TZ_TAIPEI = timezone(timedelta(hours=8))

# ========= Stooq CSV 抓取 =========
# 取最近 N 筆日資料（i=d）
# 例如：^spx、^dji、^ndq、10yusy.b、xauusd、xagusd、cl.f、ux.f、dx.f
def stooq_last_two(symbol: str):
    """
    回傳 (last_close, prev_close, last_date)；抓不到就回 (None, None, None)
    """
    url = f"https://stooq.com/q/d/l/?s={symbol}&i=d"
    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
    except Exception as e:
        print(f"[stooq fetch error] {symbol} err={e}")
        return None, None, None

    text = r.text.strip()
    if not text or "Date" not in text:
        print(f"[stooq empty] {symbol}")
        return None, None, None

    f = io.StringIO(text)
    reader = csv.DictReader(f)
    rows = list(reader)

    # 有些會回很多年資料，取最後兩筆
    if len(rows) < 1:
        return None, None, None

    last = rows[-1]
    prev = rows[-2] if len(rows) >= 2 else None

    def to_float(x):
        try:
            return float(x)
        except Exception:
            return None

    last_close = to_float(last.get("Close"))
    prev_close = to_float(prev.get("Close")) if prev else None
    last_date = last.get("Date")

    return last_close, prev_close, last_date


def pct_change(last, prev):
    if last is None or prev is None or prev == 0:
        return None
    return (last - prev) / prev * 100.0


def fnum(x, digits=2, suffix=""):
    if x is None:
        return "N/A"
    return f"{x:,.{digits}f}{suffix}"


def fchg(p):
    if p is None:
        return ""
    sign = "+" if p >= 0 else ""
    return f"（{sign}{p:.2f}%）"


# ========= 抓市場快照 =========
def get_snapshot():
    # 指數（Stooq）
    spx, spx_prev, spx_date = stooq_last_two("^spx")
    dji, dji_prev, dji_date = stooq_last_two("^dji")
    ndq, ndq_prev, ndq_date = stooq_last_two("^ndq")  # Nasdaq Composite in Stooq

    # 殖利率（Stooq）
    y10, y10_prev, _ = stooq_last_two("10yusy.b")
    y20, y20_prev, _ = stooq_last_two("20yusy.b")
    y30, y30_prev, _ = stooq_last_two("30yusy.b")

    # 商品/匯率（Stooq）
    gold, gold_prev, _ = stooq_last_two("xauusd")   # Gold spot (USD/oz)
    silver, silver_prev, _ = stooq_last_two("xagusd")  # Silver spot (USD/oz)
    wti, wti_prev, _ = stooq_last_two("cl.f")       # WTI (continuous futures on Stooq)
    uranium, uranium_prev, _ = stooq_last_two("ux.f")  # Uranium (futures on Stooq)

    # 美元指數：用 Stooq 的 DX.F（美元指數期貨連續）
    dxy, dxy_prev, _ = stooq_last_two("dx.f")

    # 日期：用 SPX 的日期當主日期（美股）
    main_date = spx_date or dji_date or ndq_date

    return {
        "date": main_date,
        "spx": spx,
        "spx_chg": pct_change(spx, spx_prev),
        "dji": dji,
        "dji_chg": pct_change(dji, dji_prev),
        "ndq": ndq,
        "ndq_chg": pct_change(ndq, ndq_prev),

        "y10": y10,
        "y10_chg": pct_change(y10, y10_prev),
        "y20": y20,
        "y20_chg": pct_change(y20, y20_prev),
        "y30": y30,
        "y30_chg": pct_change(y30, y30_prev),

        "gold": gold,
        "gold_chg": pct_change(gold, gold_prev),
        "silver": silver,
        "silver_chg": pct_change(silver, silver_prev),
        "uranium": uranium,
        "uranium_chg": pct_change(uranium, uranium_prev),
        "wti": wti,
        "wti_chg": pct_change(wti, wti_prev),

        "dxy": dxy,
        "dxy_chg": pct_change(dxy, dxy_prev),
    }


# ========= 產生日報（先用你要的版型 + B/C 混合口吻；新聞先留欄位，下一步再自動抓） =========
def generate_report(s):
    now = datetime.now(TZ_TAIPEI)

    # 如果 stooq 有回 date 用它，沒有就用今天
    date_title = s["date"] if s.get("date") else now.strftime("%Y-%m-%d")

    # 你要的標題格式
    # 例：【2026年3月2日（週一）財經日報】
    # 我用「今天台北」的週幾（你每天早上發）
    title = f"【{now.strftime('%Y年%m月%d日')}（{['週一','週二','週三','週四','週五','週六','週日'][now.weekday()]}）財經日報】"

    # 開頭（B+C 混合：快報節奏 + 理專可轉貼）
    lead = (
        "市場主軸：以風險情緒與利率走向為核心，資金在成長與防禦間快速輪動。"
        "以下用「一頁式」幫你快速掌握昨日收盤與今天可轉貼的策略重點。"
    )

    text = f"""{title}
{lead}

一、 全球市場數據概覽
1. 美股主要指數表現（收盤）
• 道瓊工業指數 (DJI)：{fnum(s['dji'], 2)} {fchg(s['dji_chg'])}
• 標普 500 指數 (S&P 500)：{fnum(s['spx'], 2)} {fchg(s['spx_chg'])}
• 那斯達克綜合指數 (NDQ)：{fnum(s['ndq'], 2)} {fchg(s['ndq_chg'])}
（註：費半 SOX 我下一步幫你補進來，先把主指數/利率/商品做準）

2. 美國國債收益率 (Yield)
• 10年期美債：{fnum(s['y10'], 3, '%')} {fchg(s['y10_chg'])}
• 20年期美債：{fnum(s['y20'], 3, '%')} {fchg(s['y20_chg'])}
• 30年期美債：{fnum(s['y30'], 3, '%')} {fchg(s['y30_chg'])}

3. 匯市與原物料
• 美元指數 (DXY 期貨連續)：{fnum(s['dxy'], 3)} {fchg(s['dxy_chg'])}
• 黃金 (XAUUSD)：{fnum(s['gold'], 2)} {fchg(s['gold_chg'])}
• 白銀 (XAGUSD)：{fnum(s['silver'], 4)} {fchg(s['silver_chg'])}
• 鈾礦 (Uranium)：{fnum(s['uranium'], 2)} {fchg(s['uranium_chg'])}
• 原油 (WTI)：{fnum(s['wti'], 2)} {fchg(s['wti_chg'])}

二、 焦點新聞摘要（先用「可手動貼」占位，下一步我再教你自動抓新聞）
【總體經濟】
• （貼 1 則：例 PPI / NFP / CPI / Fed 官員談話，用 1–2 句寫重點與影響）
【市場主題】
• （貼 1–2 則：例 AI、地緣政治、降息預期、油價）
【焦點個股】
• （貼 1–2 則：用理專可轉貼口吻，不要喊單）

三、 股債匯操作策略建議（理專可直接轉貼）
• 股市策略：短線避免追高，建議採「分批/分層」進場；若波動放大，以核心持倉 + 衛星題材的方式控風險。
• 債市策略：利率波動期可用「短端息收 + 長端避險」的槓鈴概念，但部位不宜過滿，保留加碼空間。
• 匯市與原物料策略：美元偏強時，商品容易震盪；黃金白銀偏避險屬性，但也可能短線急漲急跌，建議用小部位分批。

風險提示：以上為市場資訊整理與一般性觀察，非任何投資建議；請依自身風險承受度與資產配置執行。
"""
    # LINE 單則不要太長，先把這版控制在可發範圍
    return text.strip()


# ========= LINE 推播 =========
def push_line(text: str):
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
    user_id = os.environ.get("LINE_USER_ID")
    if not token or not user_id:
        raise ValueError("LINE 環境變數未設定：LINE_CHANNEL_ACCESS_TOKEN / LINE_USER_ID")

    api = LineBotApi(token)

    # 保守切 4800
    if len(text) <= 4800:
        api.push_message(user_id, TextSendMessage(text=text))
    else:
        api.push_message(user_id, [
            TextSendMessage(text=text[:4800]),
            TextSendMessage(text=text[4800:])
        ])


def main():
    snap = get_snapshot()
    report = generate_report(snap)
    push_line(report)


if __name__ == "__main__":
    main()
