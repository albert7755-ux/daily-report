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


def get_snapshot():
    # 指數
    spx, spx_prev, spx_date = stooq_last_two("^spx")
    dji, dji_prev, dji_date = stooq_last_two("^dji")
    ndq, ndq_prev, ndq_date = stooq_last_two("^ndq")

    # 費半：stooq 不一定有穩定 ^sox，做多個候選（抓到就用）
    sox = sox_prev = None
    for sym in ["^sox", "sox", "^soxx"]:  # 多試幾個
        v, pv, _ = stooq_last_two(sym)
        if v is not None:
            sox, sox_prev = v, pv
            break

    # 殖利率
    y10, y10_prev, _ = stooq_last_two("10yusy.b")
    y20, y20_prev, _ = stooq_last_two("20yusy.b")
    y30, y30_prev, _ = stooq_last_two("30yusy.b")

    # 商品 / 匯率
    gold, gold_prev, _ = stooq_last_two("xauusd")
    silver, silver_prev, _ = stooq_last_two("xagusd")
    wti, wti_prev, _ = stooq_last_two("cl.f")
    uranium, uranium_prev, _ = stooq_last_two("ux.f")
    dxy, dxy_prev, _ = stooq_last_two("dx.f")

    main_date = spx_date or dji_date or ndq_date

    return {
        "date": main_date,
        "dji": dji, "dji_chg": pct_change(dji, dji_prev),
        "spx": spx, "spx_chg": pct_change(spx, spx_prev),
        "ndq": ndq, "ndq_chg": pct_change(ndq, ndq_prev),
        "sox": sox, "sox_chg": pct_change(sox, sox_prev) if sox is not None else None,

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
# Google News RSS：三條專線（地緣 / 總經 / 科技）
# ----------------------------
def _rss_titles(query: str, max_items: int = 4):
    url = (
        "https://news.google.com/rss/search?q="
        + requests.utils.quote(query)
        + "&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
    )
    feed = feedparser.parse(url)
    titles = []
    for entry in getattr(feed, "entries", [])[: max_items * 2]:
        t = (entry.title or "").strip()
        if not t:
            continue
        # 去掉多餘空白
        t = re.sub(r"\s+", " ", t)
        if t not in titles:
            titles.append(t)
        if len(titles) >= max_items:
            break
    return titles


def get_news_bundle():
    # 地緣（你要求必跟）
    geo_q = (
        "(Middle East OR Israel OR Iran OR Gaza OR Red Sea OR Houthi OR Ukraine OR sanctions OR Strait) "
        "(oil OR shipping OR attack OR ceasefire OR escalation)"
    )
    # 總經（只抓會動盤的）
    macro_q = (
        "(CPI OR PCE OR Nonfarm Payrolls OR jobs report OR unemployment OR ISM OR retail sales OR FOMC OR Fed) "
        "(surprise OR hotter OR cooler OR guidance OR cut OR hike)"
    )
    # 科技/半導體（你常用）
    tech_q = (
        "(Nvidia OR NVDA OR AI chips OR semiconductors OR SOX OR TSMC OR AMD OR AVGO OR Apple) "
        "(earnings OR outlook OR demand OR export controls)"
    )

    geo = _rss_titles(geo_q, 3)
    macro = _rss_titles(macro_q, 3)
    tech = _rss_titles(tech_q, 3)

    return {"geo": geo, "macro": macro, "tech": tech}


# ----------------------------
# Prompt：鎖死你要的呈現（短 + 接地氣 + 必有地緣/總經）
# ----------------------------
def build_prompt(now, s, news):
    week = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"][now.weekday()]
    title = f"【{now.strftime('%Y年%m月%d日')}（{week}）財經日報】"

    geo_titles = news.get("geo", [])
    macro_titles = news.get("macro", [])
    tech_titles = news.get("tech", [])

    # 若抓不到，保底仍要“有地緣/有總經”——但用「本週關鍵觀察」不杜撰細節
    geo_block = "\n".join([f"• {t}" for t in geo_titles]) if geo_titles else "•（本日未抓到地緣標題：請以油價/航運風險/避險情緒的變化做一段短評）"
    macro_block = "\n".join([f"• {t}" for t in macro_titles]) if macro_titles else "•（本日未抓到總經標題：請以本週市場會看 CPI / 就業 / ISM 的方向與利率預期做一段短評）"
    tech_block = "\n".join([f"• {t}" for t in tech_titles]) if tech_titles else "•（本日未抓到科技標題：可用 AI/半導體資金輪動做一段短評）"

    # 市場數據文字（避免 N/A 硬掰）
    sox_line = ""
    if s.get("sox") is not None:
        sox_line = f"\n費城半導體指數 (SOX)： 收在 {fnum(s['sox'],2)} 點，{('上漲' if (s.get('sox_chg') or 0) >= 0 else '下跌')} {abs(s.get('sox_chg') or 0):.2f}%。"

    prompt = f"""
你是銀行投資顧問團隊的「每日市場快報總編」。
請用「媒體快報節奏 + 理專可直接轉貼」的穩健口吻寫報告。

硬規則（違反就自我修正後再輸出）：
1) 全文 900～1,200 字（含標點）
2) 必須完全照下方版型輸出（不得新增段落/不得重複整篇）
3) 【地緣政治】必寫 1 則，【總體經濟】必寫 1 則
4) 焦點新聞只能根據我提供的「標題清單」摘要，不可編造不存在的事件細節/數字
5) 每則新聞 1～2 句；不要用一堆空泛形容詞
6) 語氣：穩健、不喊單、不用「必然/保證」

版型如下（務必照抄段落標題）：

{title}

（開頭2句：用白話說明昨天美股為何上/下 + 盤面情緒；務必提到：利率/債市 或 地緣其一）

一、 全球市場數據概覽
1. 美股四大指數表現

道瓊工業指數 (DJI)： 收在 {fnum(s['dji'],2)} 點，{('上漲' if (s.get('dji_chg') or 0) >= 0 else '下跌')} {abs(s.get('dji_chg') or 0):.2f}%。
標普 500 指數 (S&P 500)： 收在 {fnum(s['spx'],2)} 點，{('上漲' if (s.get('spx_chg') or 0) >= 0 else '下跌')} {abs(s.get('spx_chg') or 0):.2f}%。
那斯達克指數 (IXIC)： 收在 {fnum(s['ndq'],2)} 點，{('上漲' if (s.get('ndq_chg') or 0) >= 0 else '下跌')} {abs(s.get('ndq_chg') or 0):.2f}%。{sox_line}

2. 美國國債收益率 (Yield)

10年期美債： 報 {fnum(s['y10'],3,'%')}（用一句話解讀：避險/降息預期/供需）。
20年期美債： 報 {fnum(s['y20'],3,'%')}（一句話）。
30年期美債： 報 {fnum(s['y30'],3,'%')}（一句話）。

3. 原物料商品表現

黃金 (Spot Gold)： 報 ${fnum(s['gold'],2)}（一句話：避險/美元/利率）。
白銀 (Spot Silver)： 報 ${fnum(s['silver'],4)}（一句話）。
鈾礦 (Uranium)： 報 ${fnum(s['uranium'],2)}（一句話）。
原油 (WTI)： 報 ${fnum(s['wti'],2)}（一句話：地緣/供需）。

二、 焦點新聞摘要
【地緣政治】（必寫1則）
（只從下方標題挑1則，用1–2句寫：事件→市場影響→你一句“接地氣”觀點）
地緣標題清單：
{geo_block}

【總體經濟】（必寫1則）
（只從下方標題挑1則；若清單是保底句，就寫“本週關鍵數據/市場會看什麼”）
總經標題清單：
{macro_block}

【焦點個股】（1～2則）
（只從下方標題挑1～2則，理專可轉貼、不喊單）
科技/個股標題清單：
{tech_block}

三、 股債匯操作策略建議
股市策略：2句（不追高/分批/部位控管；可提“等回測/等確認”）
債市策略：2句（短端息收/長端避險，或投等債息收；不要太學術）
匯市與原物料策略：2句（美元/金銀/油的簡單做法）
風險提示：1句（非投資建議）

注意：不要重複輸出兩次日報；不要把標題清單整段原封不動貼上。
"""
    return prompt.strip()


# ----------------------------
# OpenAI 產稿（強控長度與格式）
# ----------------------------
def generate_with_openai(prompt: str) -> str:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("缺少環境變數 OPENAI_API_KEY")

    client = OpenAI(api_key=api_key)

    # max_tokens 控長度；temperature 低一點更穩
    resp = client.chat.completions.create(
        model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
        temperature=0.4,
        max_tokens=850,
        messages=[
            {"role": "system", "content": "你是嚴謹的市場快報總編，必須遵守格式、不得杜撰。"},
            {"role": "user", "content": prompt},
        ],
    )

    text = resp.choices[0].message.content or ""
    text = text.strip()

    # 防呆：如果模型不小心重複整篇，簡單去重
    # 做法：若前後兩段高度相似，砍掉後半
    if len(text) > 50:
        half = len(text) // 2
        if text[:half].strip() and text[half:].strip():
            a = re.sub(r"\s+", "", text[:half])
            b = re.sub(r"\s+", "", text[half:])
            if a[:400] and b[:400] and a[:400] == b[:400]:
                text = text[:half].strip()

    return text


# ----------------------------
# LINE 推播
# ----------------------------
def push_line(text: str):
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
    user_id = os.environ.get("LINE_USER_ID")
    if not token or not user_id:
        raise ValueError("缺少環境變數：LINE_CHANNEL_ACCESS_TOKEN / LINE_USER_ID")

    api = LineBotApi(token)

    # 保守切 4800
    if len(text) <= 4800:
        api.push_message(user_id, TextSendMessage(text=text))
    else:
        api.push_message(
            user_id,
            [TextSendMessage(text=text[:4800]), TextSendMessage(text=text[4800:])],
        )


def main():
    now = datetime.now(TZ_TAIPEI)
    snap = get_snapshot()
    news = get_news_bundle()

    prompt = build_prompt(now, snap, news)
    report = generate_with_openai(prompt)

    # 最後保底：避免空輸出
    if not report or len(report) < 80:
        report = f"【{now.strftime('%Y年%m月%d日')} 財經日報】\n（系統：今日新聞抓取不足，請稍後重試）"

    push_line(report)


if __name__ == "__main__":
    main()
