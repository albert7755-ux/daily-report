# -*- coding: utf-8 -*-

import os
import csv
import io
import requests
import feedparser
from datetime import datetime, timezone, timedelta
from linebot import LineBotApi
from linebot.models import TextSendMessage
from openai import OpenAI

TZ_TAIPEI = timezone(timedelta(hours=8))
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

# ======================
# Stooq 抓數據
# ======================
def stooq_last_two(symbol: str):
    url = f"https://stooq.com/q/d/l/?s={symbol}&i=d"
    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
    except:
        return None, None, None

    text = r.text.strip()
    if not text or "Date" not in text:
        return None, None, None

    f = io.StringIO(text)
    reader = csv.DictReader(f)
    rows = list(reader)
    if len(rows) < 1:
        return None, None, None

    last = rows[-1]
    prev = rows[-2] if len(rows) >= 2 else None

    def to_float(x):
        try:
            return float(x)
        except:
            return None

    return (
        to_float(last.get("Close")),
        to_float(prev.get("Close")) if prev else None,
        last.get("Date")
    )


def pct_change(last, prev):
    if last is None or prev is None or prev == 0:
        return None
    return (last - prev) / prev * 100.0


def fnum(x, d=2, s=""):
    if x is None:
        return "N/A"
    return f"{x:,.{d}f}{s}"


def fchg(p):
    if p is None:
        return ""
    sign = "+" if p >= 0 else ""
    return f"（{sign}{p:.2f}%）"


def get_snapshot():
    spx, spx_prev, _ = stooq_last_two("^spx")
    dji, dji_prev, _ = stooq_last_two("^dji")
    ndq, ndq_prev, _ = stooq_last_two("^ndq")

    y10, y10_prev, _ = stooq_last_two("10yusy.b")
    gold, gold_prev, _ = stooq_last_two("xauusd")
    wti, wti_prev, _ = stooq_last_two("cl.f")

    return {
        "spx": spx, "spx_chg": pct_change(spx, spx_prev),
        "dji": dji, "dji_chg": pct_change(dji, dji_prev),
        "ndq": ndq, "ndq_chg": pct_change(ndq, ndq_prev),
        "y10": y10,
        "gold": gold,
        "wti": wti,
    }


# ======================
# 抓新聞標題
# ======================
def get_news():
    url = "https://news.google.com/rss/search?q=Fed%20CPI%20AI%20Oil&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
    feed = feedparser.parse(url)
    titles = []
    for entry in feed.entries[:8]:
        titles.append(entry.title)
    return titles


# ======================
# GPT 產稿
# ======================
def generate_with_gpt(snapshot, news_titles):

    now = datetime.now(TZ_TAIPEI)
    week = ["週一","週二","週三","週四","週五","週六","週日"][now.weekday()]
    title = f"【{now.strftime('%Y年%m月%d日')}（{week}）財經日報】"

    news_block = "\n".join(news_titles) if news_titles else "（今日新聞標題不足，請根據市場數據生成摘要）"

    prompt = f"""
請用媒體快報節奏 + 理專可轉貼口吻撰寫每日財經日報。

{title}

市場數據：
道瓊 {fnum(snapshot['dji'])} {fchg(snapshot['dji_chg'])}
標普500 {fnum(snapshot['spx'])} {fchg(snapshot['spx_chg'])}
那斯達克 {fnum(snapshot['ndq'])} {fchg(snapshot['ndq_chg'])}
10年期美債 {fnum(snapshot['y10'],3,'%')}
黃金 {fnum(snapshot['gold'])}
WTI {fnum(snapshot['wti'])}

新聞標題：
{news_block}

請依照結構輸出：
一、全球市場概覽
二、焦點新聞摘要（總體/市場主題/個股）
三、股債匯策略建議
最後加風險提示。
全文控制在2000字內。
"""

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "你是銀行投資顧問團隊的市場總編輯。"},
            {"role": "user", "content": prompt}
        ]
    )

    return response.choices[0].message.content


# ======================
# LINE 推播
# ======================
def push_line(text):
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
    user_id = os.environ.get("LINE_USER_ID")
    api = LineBotApi(token)

    if len(text) <= 4800:
        api.push_message(user_id, TextSendMessage(text=text))
    else:
        api.push_message(user_id, [
            TextSendMessage(text=text[:4800]),
            TextSendMessage(text=text[4800:])
        ])


def main():
    snapshot = get_snapshot()
    news_titles = get_news()
    report = generate_with_gpt(snapshot, news_titles)
    push_line(report)


if __name__ == "__main__":
    main()
