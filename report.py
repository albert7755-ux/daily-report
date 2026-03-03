# -*- coding: utf-8 -*-

import os
import csv
import io
import re
import requests
import feedparser
from datetime import datetime, timezone, timedelta
from linebot import LineBotApi
from linebot.models import TextSendMessage
from openai import OpenAI

TZ_TAIPEI = timezone(timedelta(hours=8))


# -----------------------------
# 工具函式
# -----------------------------
def pct_change(last, prev):
    if last is None or prev is None or prev == 0:
        return None
    return (last - prev) / prev * 100.0


def fnum(x, d=2, s=""):
    if x is None:
        return "N/A"
    return f"{x:,.{d}f}{s}"


def sign_word(p):
    if p is None:
        return ""
    return "上漲" if p >= 0 else "下跌"


def abs_pct(p):
    if p is None:
        return "N/A"
    return f"{abs(p):.2f}%"


def market_tone(spx_chg):
    if spx_chg is None:
        return "震盪"
    if spx_chg <= -1.2:
        return "回檔加深"
    if spx_chg <= -0.3:
        return "回檔整理"
    if spx_chg >= 1.2:
        return "強勢推進"
    if spx_chg >= 0.3:
        return "偏多續行"
    return "區間震盪"


# -----------------------------
# Stooq 日線抓取
# -----------------------------
def stooq_last_two(symbol):
    url = f"https://stooq.com/q/d/l/?s={symbol}&i=d"
    try:
        r = requests.get(url, timeout=20)
        r.raise_for_status()
    except:
        return None, None, None

    text = r.text.strip()
    if not text or "Date" not in text:
        return None, None, None

    f = io.StringIO(text)
    reader = csv.DictReader(f)
    rows = list(reader)

    if len(rows) < 2:
        return None, None, None

    last = float(rows[-1]["Close"])
    prev = float(rows[-2]["Close"])
    return last, prev, rows[-1]["Date"]


# -----------------------------
# WTI 抓取（穩定版）
# -----------------------------
def get_wti():
    for sym in ["cl.f", "cl=F"]:
        try:
            url = f"https://stooq.com/q/l/?s={sym}"
            r = requests.get(url, timeout=10)
            r.raise_for_status()
            data = r.text.split(",")
            if len(data) > 6:
                return float(data[6])
        except:
            continue
    return None


# -----------------------------
# 市場快照
# -----------------------------
def get_snapshot():
    dji, dji_prev, _ = stooq_last_two("^dji")
    spx, spx_prev, _ = stooq_last_two("^spx")
    ndq, ndq_prev, _ = stooq_last_two("^ndq")

    # 費半改抓 SOXX ETF 代理
    soxx, soxx_prev, _ = stooq_last_two("soxx")

    y10, y10_prev, _ = stooq_last_two("10yusy.b")
    y20, y20_prev, _ = stooq_last_two("20yusy.b")
    y30, y30_prev, _ = stooq_last_two("30yusy.b")

    gold, gold_prev, _ = stooq_last_two("xauusd")
    silver, silver_prev, _ = stooq_last_two("xagusd")

    wti = get_wti()

    return {
        "dji": dji, "dji_chg": pct_change(dji, dji_prev),
        "spx": spx, "spx_chg": pct_change(spx, spx_prev),
        "ndq": ndq, "ndq_chg": pct_change(ndq, ndq_prev),
        "soxx": soxx, "soxx_chg": pct_change(soxx, soxx_prev),
        "y10": y10, "y20": y20, "y30": y30,
        "gold": gold, "silver": silver,
        "wti": wti,
    }


# -----------------------------
# GPT 產稿
# -----------------------------
def build_prompt(now, s):
    week = ["週一","週二","週三","週四","週五","週六","週日"][now.weekday()]
    title = f"【{now.strftime('%Y年%m月%d日')}（{week}）財經快報】"

    tone = market_tone(s["spx_chg"])

    prompt = f"""
請用理專可直接轉貼給客戶的品牌型語氣撰寫。
字數650-850字。
不可寫觀望，跌市也要用分批逢低承接角度。
不可杜撰新聞細節。

{title}

昨晚美股呈現「{tone}」格局，利率與資金流向成為盤面主軸。市場波動放大，但中期趨勢仍以產業基本面為核心。

一、 全球市場數據概覽
1. 美股四大指數表現

道瓊工業指數： 收在 {fnum(s["dji"])} 點，{sign_word(s["dji_chg"])} {abs_pct(s["dji_chg"])}。
標普500指數： 收在 {fnum(s["spx"])} 點，{sign_word(s["spx_chg"])} {abs_pct(s["spx_chg"])}。
那斯達克指數： 收在 {fnum(s["ndq"])} 點，{sign_word(s["ndq_chg"])} {abs_pct(s["ndq_chg"])}。
費城半導體指數（以SOXX ETF近似）： 收在 {fnum(s["soxx"])}，{sign_word(s["soxx_chg"])} {abs_pct(s["soxx_chg"])}。

2. 美國國債收益率

10年期美債： {fnum(s["y10"],3,"%")}
20年期美債： {fnum(s["y20"],3,"%")}
30年期美債： {fnum(s["y30"],3,"%")}

3. 匯市與原物料

黃金： ${fnum(s["gold"])}
白銀： ${fnum(s["silver"],4)}
西德洲原油（WTI）： ${fnum(s["wti"])}

二、 焦點新聞摘要
【地緣政治】市場持續關注中東與能源供應動態，相關報導增加，使油價與能源股短線波動放大。

【總體經濟】本週市場將關注通膨與就業數據，這將影響聯準會利率預期與資金風險偏好。

【焦點個股】AI與半導體仍是資金主軸，但在利率震盪背景下，族群內部輪動加快。

三、 股債匯操作策略建議
股市策略：波動放大屬於健康整理，建議分批逢低承接主流產業龍頭，同時做好部位控管。
債市策略：短端息收具吸引力，可搭配投資等級債穩定現金流。
匯市與原物料策略：黃金可採回檔分批布局，原油受地緣支撐，中期仍具彈性空間。
風險提示：以上為市場資訊整理，非投資建議。
"""
    return prompt.strip()


def generate_report(prompt):
    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        temperature=0.3,
        max_tokens=700,
        messages=[
            {"role": "system", "content": "你是專業投資輔銷市場總編。"},
            {"role": "user", "content": prompt}
        ]
    )
    return resp.choices[0].message.content.strip()


# -----------------------------
# LINE 推播
# -----------------------------
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
    now = datetime.now(TZ_TAIPEI)
    snap = get_snapshot()
    prompt = build_prompt(now, snap)
    report = generate_report(prompt)
    push_line(report)


if __name__ == "__main__":
    main()
