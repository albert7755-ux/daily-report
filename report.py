# -*- coding: utf-8 -*-

import os
import csv
import io
import requests
from datetime import datetime, timezone, timedelta
from linebot import LineBotApi
from linebot.models import TextSendMessage
import feedparser

TZ_TAIPEI = timezone(timedelta(hours=8))

# ----------------------------
# Stooq: 取最後兩筆 Close
# ----------------------------
def stooq_last_two(symbol: str):
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


# ----------------------------
# 抓市場快照（Stooq）
# ----------------------------
def get_snapshot():
    spx, spx_prev, spx_date = stooq_last_two("^spx")
    dji, dji_prev, dji_date = stooq_last_two("^dji")
    ndq, ndq_prev, ndq_date = stooq_last_two("^ndq")

    y10, y10_prev, _ = stooq_last_two("10yusy.b")
    y20, y20_prev, _ = stooq_last_two("20yusy.b")
    y30, y30_prev, _ = stooq_last_two("30yusy.b")

    gold, gold_prev, _ = stooq_last_two("xauusd")
    silver, silver_prev, _ = stooq_last_two("xagusd")
    wti, wti_prev, _ = stooq_last_two("cl.f")
    uranium, uranium_prev, _ = stooq_last_two("ux.f")
    dxy, dxy_prev, _ = stooq_last_two("dx.f")

    main_date = spx_date or dji_date or ndq_date

    return {
        "date": main_date,
        "spx": spx, "spx_chg": pct_change(spx, spx_prev),
        "dji": dji, "dji_chg": pct_change(dji, dji_prev),
        "ndq": ndq, "ndq_chg": pct_change(ndq, ndq_prev),

        "y10": y10, "y10_chg": pct_change(y10, y10_prev),
        "y20": y20, "y20_chg": pct_change(y20, y20_prev),
        "y30": y30, "y30_chg": pct_change(y30, y30_prev),

        "gold": gold, "gold_chg": pct_change(gold, gold_prev),
        "silver": silver, "silver_chg": pct_change(silver, silver_prev),
        "uranium": uranium, "uranium_chg": pct_change(uranium, uranium_prev),
        "wti": wti, "wti_chg": pct_change(wti, wti_prev),

        "dxy": dxy, "dxy_chg": pct_change(dxy, dxy_prev),
    }


# ----------------------------
# Google News RSS 抓新聞
# ----------------------------
def get_news_titles():
    queries = [
        "Fed OR CPI OR PPI OR NFP",
        "AI Nvidia AMD Broadcom",
        "Middle East oil",
        "S&P 500 Nasdaq",
    ]
    titles = []
    for q in queries:
        url = f"https://news.google.com/rss/search?q={requests.utils.quote(q)}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
        feed = feedparser.parse(url)
        for entry in feed.entries[:3]:
            t = entry.title.strip()
            if t and t not in titles:
                titles.append(t)
    return titles[:12]


# ----------------------------
# 模型寫稿（OpenClaw / GPT）
# ----------------------------
def build_prompt(now, s, news_titles):
    week = ["週一","週二","週三","週四","週五","週六","週日"][now.weekday()]
    title = f"【{now.strftime('%Y年%m月%d日')}（{week}）財經日報】"

    news_block = "\n".join([f"• {t}" for t in news_titles]) if news_titles else "（今日無法取得新聞標題，請用市場數據生成摘要）"

    prompt = f"""
你是銀行理專團隊的每日市場快報總編。
請用「媒體快報節奏 + 理專可轉貼給客戶」的穩健語氣，輸出一則 LINE 財經日報。

硬性規則：
- 必須完全照版型輸出（段落與順序不可改）
- 全文 <= 2200 字
- 不能喊單、避免絕對語氣
- 焦點新聞摘要請根據我提供的「新聞標題清單」整理，不要編造不存在的新聞細節

{title}
（開頭2~3句：交代市場主軸 + 情緒）

一、 全球市場數據概覽
1. 美股四大指數表現
• 道瓊工業指數 (DJI)：{fnum(s['dji'],2)} {fchg(s['dji_chg'])}
• 標普 500 指數 (S&P 500)：{fnum(s['spx'],2)} {fchg(s['spx_chg'])}
• 那斯達克指數 (IXIC)：{fnum(s['ndq'],2)} {fchg(s['ndq_chg'])}
（若你無法提供費半，就不要硬寫）

2. 美國國債收益率 (Yield)
• 10年期美債：{fnum(s['y10'],3,'%')} {fchg(s['y10_chg'])}
• 20年期美債：{fnum(s['y20'],3,'%')} {fchg(s['y20_chg'])}
• 30年期美債：{fnum(s['y30'],3,'%')} {fchg(s['y30_chg'])}

3. 原物料商品表現
• 黃金 (Spot Gold)：{fnum(s['gold'],2)}
• 白銀 (Spot Silver)：{fnum(s['silver'],4)}
• 鈾礦 (Uranium)：{fnum(s['uranium'],2)}
• 原油 (WTI)：{fnum(s['wti'],2)}

二、 焦點新聞摘要（請用下方標題整理成：總體/市場主題/焦點個股，各1~2則）
新聞標題清單：
{news_block}

三、 股債匯操作策略建議（理專可直接轉貼）
• 股市策略：2~3句
• 債市策略：2~3句
• 匯市與原物料策略：2~3句
最後加一句「今日一句話總結」。
"""
    return prompt.strip()


def call_model(prompt: str):
    endpoint = os.environ.get("MODEL_ENDPOINT")
    api_key = os.environ.get("MODEL_API_KEY")

    # 沒接模型：先回傳 None，走簡易版
    if not endpoint:
        return None

    headers = {"Content-Type": "application/json; charset=utf-8"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    r = requests.post(endpoint, json={"prompt": prompt}, headers=headers, timeout=90)
    r.raise_for_status()
    data = r.json()
    return data.get("text")


# ----------------------------
# 沒接模型時的簡易版輸出（保底）
# ----------------------------
def fallback_report(now, s, news_titles):
    week = ["週一","週二","週三","週四","週五","週六","週日"][now.weekday()]
    title = f"【{now.strftime('%Y年%m月%d日')}（{week}）財經日報】"
    news_show = "\n".join([f"• {t}" for t in news_titles[:6]]) if news_titles else "•（今日無法取得新聞標題）"

    return f"""{title}
市場主軸：風險情緒與利率預期主導，資金輪動加快。

一、 全球市場數據概覽
1. 美股主要指數
• 道瓊 (DJI)：{fnum(s['dji'],2)} {fchg(s['dji_chg'])}
• 標普 500：{fnum(s['spx'],2)} {fchg(s['spx_chg'])}
• 那斯達克 (IXIC)：{fnum(s['ndq'],2)} {fchg(s['ndq_chg'])}

2. 美國國債收益率
• 10Y：{fnum(s['y10'],3,'%')}
• 20Y：{fnum(s['y20'],3,'%')}
• 30Y：{fnum(s['y30'],3,'%')}

3. 商品
• 黃金：{fnum(s['gold'],2)}
• 白銀：{fnum(s['silver'],4)}
• 鈾：{fnum(s['uranium'],2)}
• WTI：{fnum(s['wti'],2)}

二、 焦點新聞標題（保底列出）
{news_show}

三、 操作策略建議（保底）
• 股市：分批與部位控管優先，避免追價。
• 債市：以短端息收為主，保留加碼彈性。
• 匯市與商品：波動可能放大，小部位分批。
風險提示：以上為資訊整理，非投資建議。
""".strip()


# ----------------------------
# LINE 推播
# ----------------------------
def push_line(text: str):
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
    user_id = os.environ.get("LINE_USER_ID")
    if not token or not user_id:
        raise ValueError("缺少環境變數：LINE_CHANNEL_ACCESS_TOKEN / LINE_USER_ID")

    api = LineBotApi(token)

    if len(text) <= 4800:
        api.push_message(user_id, TextSendMessage(text=text))
    else:
        api.push_message(user_id, [
            TextSendMessage(text=text[:4800]),
            TextSendMessage(text=text[4800:])
        ])


def main():
    now = datetime.now(TZ_TAIPEI)
    snap = get_snapshot()
    news_titles = get_news_titles()

    prompt = build_prompt(now, snap, news_titles)
    model_text = call_model(prompt)

    report = model_text if model_text else fallback_report(now, snap, news_titles)
    push_line(report)


if __name__ == "__main__":
    main()
