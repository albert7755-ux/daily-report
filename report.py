# -*- coding: utf-8 -*-

import os
import csv
import io
import re
import requests
from datetime import datetime, timezone, timedelta
from linebot import LineBotApi
from linebot.models import TextSendMessage
from openai import OpenAI

TZ_TAIPEI = timezone(timedelta(hours=8))


# -----------------------------
# Helper functions
# -----------------------------
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


def to_float(v):
    try:
        if v is None:
            return None
        v = str(v).strip()
        if v == "" or v.lower() == "n/a":
            return None
        return float(v)
    except Exception:
        return None


# -----------------------------
# Stooq: daily series (robust)
# -----------------------------
def stooq_last_two(symbol: str):
    """
    Return (last_close, prev_close, last_date).
    Robust against blank rows.
    """
    url = f"https://stooq.com/q/d/l/?s={symbol}&i=d"
    try:
        r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
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
    rows = []
    for row in reader:
        c = to_float(row.get("Close"))
        d = (row.get("Date") or "").strip()
        if c is None or not d:
            continue
        rows.append((d, c))

    if len(rows) == 0:
        return None, None, None
    if len(rows) == 1:
        d, c = rows[-1]
        return c, None, d

    (d_last, c_last) = rows[-1]
    (d_prev, c_prev) = rows[-2]
    return c_last, c_prev, d_last


# -----------------------------
# Stooq: quote (fast)
# -----------------------------
def stooq_quote_last(symbol: str):
    """
    Quote endpoint: Symbol,Date,Time,Open,High,Low,Close,Volume
    """
    url = f"https://stooq.com/q/l/?s={symbol}"
    try:
        r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        parts = [p.strip() for p in r.text.strip().split(",")]
        if len(parts) >= 7:
            return to_float(parts[6])
    except Exception as e:
        print(f"[stooq quote error] {symbol} err={e}")
    return None


# -----------------------------
# WTI: quote -> daily fallback (stable)
# -----------------------------
def get_wti():
    # Quote first (more current)
    for sym in ["cl.f", "cl=F"]:
        v = stooq_quote_last(sym)
        if v is not None:
            return v

    # Daily fallback (stable for morning report)
    v, _, _ = stooq_last_two("cl.f")
    if v is not None:
        return v
    v, _, _ = stooq_last_two("cl=F")
    return v


# -----------------------------
# Snapshot
# -----------------------------
def get_snapshot():
    dji, dji_prev, _ = stooq_last_two("^dji")
    spx, spx_prev, _ = stooq_last_two("^spx")
    ndq, ndq_prev, _ = stooq_last_two("^ndq")

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

        "y10": y10, "y10_chg": pct_change(y10, y10_prev),
        "y20": y20, "y20_chg": pct_change(y20, y20_prev),
        "y30": y30, "y30_chg": pct_change(y30, y30_prev),

        "gold": gold, "gold_chg": pct_change(gold, gold_prev),
        "silver": silver, "silver_chg": pct_change(silver, silver_prev),

        "wti": wti,
    }


# -----------------------------
# Prompt (your preferred "盤感早報" style)
# - No emoji
# - No markdown
# - No greeting
# - No "觀望"
# - Must include strategy section
# -----------------------------
def build_prompt(now, s):
    week = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"][now.weekday()]
    title = f"【{now.strftime('%Y年%m月%d日')}（{week}）財經日報】"
    tone = market_tone(s.get("spx_chg"))

    wti_line = f"${fnum(s.get('wti'),2)}" if s.get("wti") is not None else "數據暫缺"

    prompt = f"""
你是「品牌型每日財經日報」總編：文風像券商早報＋盤勢觀點，理專可直接轉貼客戶。
寫法要有盤感、有畫面：用兩三句講清楚“昨晚為什麼這樣走、盤中轉折是什麼、收盤留下什麼結論”。

硬規則（違反任一條就重寫）：
1) 全文 750～1,050 字（含標點）
2) 嚴禁使用 Markdown（不要 ###、不要 **粗體**、不要條列符號「-」「•」）
3) 嚴禁任何稱呼/寒暄（不要「親愛的客戶」、不要問候語）
4) 禁止出現「觀望」兩字（可用：保留彈性、分批、拉回、回測、逢低承接）
5) 數字只能使用我提供的市場數據（指數、殖利率、金銀油），不得自行編數字
6) 新聞/事件不要寫具體指名或細節；只能用「市場關注點」描述（例如：地緣風險升溫→油價風險溢價、數據週→利率預期擺盪）
7) 版型必須完全照下方輸出，且「三、股債匯操作策略建議」一定要完整四行

請只輸出以下版型（不可多一行、不可少一段）：

{title}

（開頭 2–3 句：昨晚盤勢屬於「{tone}」。一定要點到：利率/債市或地緣其一；語氣像快報）

一、 全球市場數據概覽
1. 美股三大指數表現

道瓊工業指數： 收在 {fnum(s.get('dji'),2)} 點，{sign_word(s.get('dji_chg'))} {abs_pct(s.get('dji_chg'))}。（一句話：盤感原因）
標普500指數： 收在 {fnum(s.get('spx'),2)} 點，{sign_word(s.get('spx_chg'))} {abs_pct(s.get('spx_chg'))}。（一句話：盤感原因）
那斯達克指數： 收在 {fnum(s.get('ndq'),2)} 點，{sign_word(s.get('ndq_chg'))} {abs_pct(s.get('ndq_chg'))}。（一句話：盤感原因）

2. 美國國債收益率

10年期美債： 報 {fnum(s.get('y10'),3,'%')}。（一句話：利率走勢對股市情緒的解讀）
20年期美債： 報 {fnum(s.get('y20'),3,'%')}。（一句話）
30年期美債： 報 {fnum(s.get('y30'),3,'%')}。（一句話）

3. 原物料商品表現

黃金： 報 ${fnum(s.get('gold'),2)}。（一句話：避險/利率/美元邏輯）
白銀： 報 ${fnum(s.get('silver'),4)}。（一句話：波動/資金/工業金融雙屬性）
原油（WTI）： 報 {wti_line}。（一句話：地緣/供需/風險溢價）

二、 焦點新聞摘要
【總體經濟】用 2–3 句：寫“市場正在盯的關鍵數據/聯準會訊號”以及它怎麼影響利率與股市（不寫具體數字結果）。
【市場主題】用 2 句：寫“地緣/油價/風險情緒/AI資金輪動”等關注點，講清楚對盤面的直接影響。
【焦點個股】用 2–3 句：用快報口吻寫 1–2 個“主線代表題材”（AI/半導體/大型權值），不喊單、不寫未證實消息。

三、 股債匯操作策略建議
股市策略：3 句。主軸必須是“拉回分批、逢低承接主流龍頭與AI落地”，並加一句風險控管（分批/部位/回測）。
債市策略：2 句。用白話講“息收/避險/長短搭配”的做法。
匯市與原物料策略：2 句。提金銀/油的分批做法與波動提醒（避免用觀望）。
風險提示：1 句（非投資建議）
"""
    return prompt.strip()


# -----------------------------
# OpenAI generation with strict validation + retry
# -----------------------------
def _ok(text: str) -> bool:
    if not text:
        return False

    must = [
        "一、 全球市場數據概覽",
        "二、 焦點新聞摘要",
        "三、 股債匯操作策略建議",
        "股市策略：",
        "債市策略：",
        "匯市與原物料策略：",
        "風險提示：",
    ]
    if any(m not in text for m in must):
        return False

    # Forbidden
    if "觀望" in text:
        return False
    if "親愛的" in text or "您好" in text:
        return False
    if "###" in text or "**" in text:
        return False
    if "\n-" in text or "\n•" in text:
        return False

    # length sanity (LINE-safe & content-complete)
    if len(text) < 520 or len(text) > 1800:
        return False

    return True


def generate_report(prompt: str) -> str:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("缺少環境變數 OPENAI_API_KEY")

    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    client = OpenAI(api_key=api_key)

    last = ""
    for _ in range(2):
        resp = client.chat.completions.create(
            model=model,
            temperature=0.25,
            max_tokens=900,
            messages=[
                {"role": "system", "content": "你是投資輔銷市場總編：必須照版型、短、有盤感、不可杜撰、不可使用“觀望”。"},
                {"role": "user", "content": prompt},
                {"role": "user", "content": "輸出後自我檢查：段落是否齊全、策略三行是否存在、是否未出現“觀望”、是否沒有markdown/條列/稱呼。不合格請立刻重寫並只輸出最終版。"},
            ],
        )
        text = (resp.choices[0].message.content or "").strip()
        text = re.sub(r"\n{3,}", "\n\n", text)
        last = text

        if _ok(text):
            return text

        prompt += "\n\n（提醒：上次輸出不合格。不得缺少策略段、不得出現“觀望”、不得使用markdown/條列/稱呼，且不可杜撰具體事件。）"

    return last


# -----------------------------
# LINE push
# -----------------------------
def push_line(text: str):
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
    user_id = os.environ.get("LINE_USER_ID")
    if not token or not user_id:
        raise ValueError("缺少環境變數：LINE_CHANNEL_ACCESS_TOKEN / LINE_USER_ID")

    api = LineBotApi(token)

    # LINE length safe split
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
    prompt = build_prompt(now, snap)
    report = generate_report(prompt)

    if not report or len(report) < 200:
        report = f"【{now.strftime('%Y年%m月%d日')} 財經日報】\n系統提示：今日生成內容不足，請稍後重試。"

    push_line(report)


if __name__ == "__main__":
    main()
