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
# Utilities
# ----------------------------
def pct_change(last, prev):
    if last is None or prev is None or prev == 0:
        return None
    return (last - prev) / prev * 100.0


def fnum(x, digits=2, suffix=""):
    if x is None:
        return "N/A"
    return f"{x:,.{digits}f}{suffix}"


def sign_word(p):
    if p is None:
        return "變動"
    return "上漲" if p >= 0 else "下跌"


def abs_pct(p):
    if p is None:
        return "N/A"
    return f"{abs(p):.2f}%"


def market_tone(spx_chg):
    # 用標普跌幅粗略分出語氣模組
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


# ----------------------------
# Stooq daily series (last two)
# ----------------------------
def stooq_last_two(symbol: str):
    """
    Return (last_close, prev_close, last_date). None if not available.
    """
    url = f"https://stooq.com/q/d/l/?s={symbol}&i=d"
    try:
        r = requests.get(url, timeout=25)
        r.raise_for_status()
    except Exception as e:
        print(f"[stooq series fetch error] {symbol} err={e}")
        return None, None, None

    text = r.text.strip()
    if not text or "Date" not in text:
        print(f"[stooq series empty] {symbol}")
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

    return (
        to_float(last.get("Close")),
        to_float(prev.get("Close")) if prev else None,
        last.get("Date"),
    )


# ----------------------------
# Stooq quote (for WTI) - more reliable than daily series sometimes
# ----------------------------
def stooq_quote_last(symbol: str):
    """
    Use Stooq quote endpoint: https://stooq.com/q/l/?s=SYMBOL
    Format: Symbol,Date,Time,Open,High,Low,Close,Volume
    We'll take Close if possible.
    """
    url = f"https://stooq.com/q/l/?s={symbol}"
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        parts = [p.strip() for p in r.text.strip().split(",")]
        # parts[6] is Close in typical response
        if len(parts) >= 7:
            try:
                return float(parts[6])
            except Exception:
                return None
    except Exception as e:
        print(f"[stooq quote error] {symbol} err={e}")
    return None


def get_wti():
    # Primary: NYMEX WTI continuous futures on Stooq
    # Backup: alternate symbol might work depending on endpoint behavior
    for sym in ["cl.f", "cl=F"]:
        v = stooq_quote_last(sym)
        if v is not None:
            return v
    # fallback to daily series if quote fails
    v, _, _ = stooq_last_two("cl.f")
    return v


# ----------------------------
# SOX from StockQ
# ----------------------------
def get_sox_from_stockq():
    """
    StockQ page: https://www.stockq.org/index/SOX.php
    Page structure may change; this is a lightweight heuristic parser.
    """
    url = "https://www.stockq.org/index/SOX.php"
    try:
        r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        html = r.text

        # Try several patterns to locate the index level
        # Look for a number with commas and decimals near "費城半導體"
        patterns = [
            r"費城半導體指數[^0-9]*([\d,]+\.\d+)",
            r"SOX[^0-9]*([\d,]+\.\d+)",
        ]
        for pat in patterns:
            m = re.search(pat, html)
            if m:
                return float(m.group(1).replace(",", ""))
    except Exception as e:
        print(f"[stockq sox error] err={e}")
    return None


# ----------------------------
# Market snapshot
# ----------------------------
def get_snapshot():
    # Indices
    dji, dji_prev, _ = stooq_last_two("^dji")
    spx, spx_prev, _ = stooq_last_two("^spx")
    ndq, ndq_prev, _ = stooq_last_two("^ndq")

    # SOX
    sox = get_sox_from_stockq()

    # Yields (Stooq)
    y10, y10_prev, _ = stooq_last_two("10yusy.b")
    y20, y20_prev, _ = stooq_last_two("20yusy.b")
    y30, y30_prev, _ = stooq_last_two("30yusy.b")

    # Commodities (Stooq)
    gold, gold_prev, _ = stooq_last_two("xauusd")
    silver, silver_prev, _ = stooq_last_two("xagusd")

    # WTI (quote)
    wti = get_wti()

    # DXY (optional; sometimes N/A)
    dxy, dxy_prev, _ = stooq_last_two("dx.f")

    return {
        "dji": dji, "dji_chg": pct_change(dji, dji_prev),
        "spx": spx, "spx_chg": pct_change(spx, spx_prev),
        "ndq": ndq, "ndq_chg": pct_change(ndq, ndq_prev),
        "sox": sox,  # no prev from stockq in this simple version

        "y10": y10, "y10_chg": pct_change(y10, y10_prev),
        "y20": y20, "y20_chg": pct_change(y20, y20_prev),
        "y30": y30, "y30_chg": pct_change(y30, y30_prev),

        "gold": gold, "gold_chg": pct_change(gold, gold_prev),
        "silver": silver, "silver_chg": pct_change(silver, silver_prev),
        "wti": wti,  # quote only
        "dxy": dxy, "dxy_chg": pct_change(dxy, dxy_prev),
    }


# ----------------------------
# News: three channels (Geo / Macro / Tech)
# ----------------------------
def _rss_titles(query: str, max_items: int = 3):
    url = (
        "https://news.google.com/rss/search?q="
        + requests.utils.quote(query)
        + "&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
    )
    feed = feedparser.parse(url)
    titles = []
    for entry in getattr(feed, "entries", [])[: max_items * 3]:
        t = (entry.title or "").strip()
        if not t:
            continue
        t = re.sub(r"\s+", " ", t)
        # keep short-ish
        if len(t) > 140:
            t = t[:140] + "…"
        if t not in titles:
            titles.append(t)
        if len(titles) >= max_items:
            break
    return titles


def get_news_bundle():
    geo_q = (
        "(Middle East OR Israel OR Iran OR Red Sea OR Houthi OR Gaza OR Ukraine OR sanctions OR shipping) "
        "(oil OR attack OR escalation OR ceasefire OR tanker)"
    )
    macro_q = (
        "(CPI OR PCE OR Nonfarm Payrolls OR jobs report OR unemployment OR ISM OR retail sales OR FOMC OR Fed) "
        "(hotter OR cooler OR surprise OR guidance OR cut OR hike)"
    )
    tech_q = (
        "(Nvidia OR NVDA OR AMD OR Broadcom OR AVGO OR TSMC OR Apple OR AI chips OR semiconductors OR SOX) "
        "(earnings OR outlook OR demand OR export controls)"
    )
    return {
        "geo": _rss_titles(geo_q, 3),
        "macro": _rss_titles(macro_q, 3),
        "tech": _rss_titles(tech_q, 3),
    }


# ----------------------------
# Prompt: brand-style RM-forwardable
# ----------------------------
def build_prompt(now, s, news):
    week = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"][now.weekday()]
    title = f"【{now.strftime('%Y年%m月%d日')}（{week}）財經快報】"

    tone = market_tone(s.get("spx_chg"))

    # Blocks (only titles; model must not invent details)
    geo_titles = news.get("geo", [])[:3]
    macro_titles = news.get("macro", [])[:3]
    tech_titles = news.get("tech", [])[:3]

    geo_block = "\n".join([f"- {t}" for t in geo_titles]) if geo_titles else "- （今日抓不到地緣標題：只能用“油價/避險情緒/航運風險”寫1句，不可編造事件細節）"
    macro_block = "\n".join([f"- {t}" for t in macro_titles]) if macro_titles else "- （今日抓不到總經標題：只能用“本週關鍵數據（CPI/就業/ISM）市場會看什麼”寫1句，不可編造數據結果）"
    tech_block = "\n".join([f"- {t}" for t in tech_titles]) if tech_titles else "- （今日抓不到科技標題：只能用“AI/半導體資金輪動”寫1句，不可編造公司事件）"

    # SOX line: if missing, skip
    sox_line = ""
    if s.get("sox") is not None:
        sox_line = f"\n費城半導體指數： 收在 {fnum(s['sox'],2)} 點。（一句話：半導體/AI 情緒）"

    # WTI line: if missing, show 暫缺 only (no commentary)
    wti_line = f"${fnum(s.get('wti'),2)}" if s.get("wti") is not None else "數據暫缺"

    dxy_line = fnum(s.get("dxy"), 2) if s.get("dxy") is not None else "N/A"

    # Strict spec: short, warm, RM-actionable, buy-the-dip style
    prompt = f"""
你是銀行投資輔銷團隊的「品牌型每日財經快報總編」。
請用「理專可直接轉貼給客戶」的口吻：精簡、有盤感、有行動建議（即使市場不穩也以“分批逢低承接/控風險”表達，禁止寫“觀望”）。

【硬規則】違反任一條請自我修正後再輸出：
1) 字數 650～900 字（含標點），不要寫長
2) 只能輸出一次，不可重複整篇
3) 結構與小標必須完全照下面版型（不可改成別的格式、不可用 1.總體新聞 之類）
4) 新聞摘要只能根據我提供的「標題清單」寫 1–2 句，不可編造任何不存在的事件/細節/數字
5) 【地緣政治】必寫 1 則，【總體經濟】必寫 1 則，【焦點個股】寫 1–2 則
6) 若商品數據為「數據暫缺/N/A」，只能寫「數據暫缺」，不得延伸評論

請依下列版型輸出（保留每個大項開頭的表情符號）：

{title}

（開頭 2 句：用白話描述昨晚盤勢屬於「{tone}」；一定要點到：利率/債市 或 地緣其一，語氣像快報）

📎 一、 全球市場數據概覽
1. 美股四大指數表現

道瓊工業指數： 收在 {fnum(s.get('dji'),2)} 點，{sign_word(s.get('dji_chg'))} {abs_pct(s.get('dji_chg'))}。（一句話原因）
標普500指數： 收在 {fnum(s.get('spx'),2)} 點，{sign_word(s.get('spx_chg'))} {abs_pct(s.get('spx_chg'))}。（一句話原因）
那斯達克指數： 收在 {fnum(s.get('ndq'),2)} 點，{sign_word(s.get('ndq_chg'))} {abs_pct(s.get('ndq_chg'))}。（一句話原因）{sox_line}

2. 美國國債收益率

10年期美債： {fnum(s.get('y10'),3,'%')}（一句話解讀）
20年期美債： {fnum(s.get('y20'),3,'%')}（一句話解讀）
30年期美債： {fnum(s.get('y30'),3,'%')}（一句話解讀）

3. 匯市與原物料

美元指數： {dxy_line}（一句話，若N/A就寫“資料未更新”）
黃金： ${fnum(s.get('gold'),2)}（一句話）
白銀： ${fnum(s.get('silver'),4)}（一句話）
紐約輕原油（WTI）： {wti_line}

📰 二、 焦點新聞摘要
【地緣政治】（從標題清單挑 1 則，1–2 句：事件→影響→一句接地氣觀點）
地緣標題清單：
{geo_block}

【總體經濟】（從標題清單挑 1 則；若清單是保底句，就改寫成“本週關鍵數據/市場會看什麼”，1–2 句）
總經標題清單：
{macro_block}

【焦點個股】（從標題清單挑 1–2 則，每則 1 句，理專可轉貼、不喊單）
個股/題材標題清單：
{tech_block}

🧭 三、 股債匯操作策略建議（理專可轉貼）
股市策略：2 句（核心：分批逢低承接、資金主流題材、同時做部位控管）
債市策略：2 句（核心：短端息收 + 投等債/長端作風險對沖，語氣不要太學術）
匯市與原物料策略：2 句（核心：美元/金銀/油的簡單做法，避免喊單）
風險提示：1 句（非投資建議）
"""
    return prompt.strip()


# ----------------------------
# OpenAI generation with validation + retry
# ----------------------------
def _looks_valid(report: str) -> bool:
    must = [
        "📎 一、 全球市場數據概覽",
        "📰 二、 焦點新聞摘要",
        "🧭 三、 股債匯操作策略建議",
        "【地緣政治】",
        "【總體經濟】",
        "【焦點個股】",
        "道瓊工業指數：",
        "標普500指數：",
        "那斯達克指數：",
        "10年期美債：",
        "黃金：",
        "白銀：",
        "紐約輕原油（WTI）：",
        "風險提示：",
    ]
    if not report:
        return False
    if len(report) < 450 or len(report) > 1400:
        return False
    # forbid that old unwanted structure
    if "1. **總體新聞**" in report or "市場主題" in report:
        return False
    return all(k in report for k in must)


def generate_with_openai(prompt: str) -> str:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("缺少環境變數 OPENAI_API_KEY")

    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    client = OpenAI(api_key=api_key)

    last_text = ""
    for attempt in range(2):
        resp = client.chat.completions.create(
            model=model,
            temperature=0.25,
            max_tokens=650,
            messages=[
                {"role": "system", "content": "你是嚴謹的投資輔銷快報總編：必須照版型、短、不可杜撰、不可叫客戶觀望。"},
                {"role": "user", "content": prompt},
                {"role": "user", "content": "輸出後請自我檢查：是否650-900字、是否只用標題清單、不杜撰細節、是否包含地緣+總經+個股、是否未出現“觀望”。不合格請立刻重寫並只輸出最終版本。"},
            ],
        )
        text = (resp.choices[0].message.content or "").strip()
        text = re.sub(r"\n{3,}", "\n\n", text)

        # avoid accidental double output by simple de-dup
        if len(text) > 200:
            half = len(text) // 2
            a = re.sub(r"\s+", "", text[:half])
            b = re.sub(r"\s+", "", text[half:])
            if a[:350] and b[:350] and a[:350] == b[:350]:
                text = text[:half].strip()

        last_text = text
        if _looks_valid(text):
            return text

        prompt += "\n\n（提醒：上次輸出不合格。請嚴格照版型與字數重寫；若WTI為“數據暫缺”，不得評論；新聞不得新增任何標題清單外的細節。）"

    return last_text


# ----------------------------
# LINE push
# ----------------------------
def push_line(text: str):
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
    user_id = os.environ.get("LINE_USER_ID")
    if not token or not user_id:
        raise ValueError("缺少環境變數：LINE_CHANNEL_ACCESS_TOKEN / LINE_USER_ID")

    api = LineBotApi(token)

    # LINE limit ~5000; split safely
    if len(text) <= 4800:
        api.push_message(user_id, TextSendMessage(text=text))
    else:
        api.push_message(user_id, [TextSendMessage(text=text[:4800]), TextSendMessage(text=text[4800:])])


def main():
    now = datetime.now(TZ_TAIPEI)

    snap = get_snapshot()
    news = get_news_bundle()

    prompt = build_prompt(now, snap, news)
    report = generate_with_openai(prompt)

    if not report or len(report) < 120:
        report = f"【{now.strftime('%Y年%m月%d日')} 財經快報】\n系統提示：今日生成內容不足，請稍後重試。"

    push_line(report)


if __name__ == "__main__":
    main()
