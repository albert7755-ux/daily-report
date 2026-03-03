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


# -----------------------------
# Stooq daily series (last two)
# -----------------------------
def stooq_last_two(symbol: str):
    """
    Return (last_close, prev_close, last_date). None if not available.
    """
    url = f"https://stooq.com/q/d/l/?s={symbol}&i=d"
    try:
        r = requests.get(url, timeout=20)
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
    if len(rows) < 2:
        return None, None, None

    def to_float(v):
        try:
            return float(v)
        except Exception:
            return None

    last = rows[-1]
    prev = rows[-2]
    return to_float(last.get("Close")), to_float(prev.get("Close")), last.get("Date")


# -----------------------------
# Snapshot (stable sources)
# - SOX proxy: SOXX ETF (stooq)
# - WTI: cl.f daily close (stooq) => stable for morning report
# -----------------------------
def get_snapshot():
    dji, dji_prev, _ = stooq_last_two("^dji")
    spx, spx_prev, _ = stooq_last_two("^spx")
    ndq, ndq_prev, _ = stooq_last_two("^ndq")

    soxx, soxx_prev, _ = stooq_last_two("soxx")

    y10, y10_prev, _ = stooq_last_two("10yusy.b")
    y20, y20_prev, _ = stooq_last_two("20yusy.b")
    y30, y30_prev, _ = stooq_last_two("30yusy.b")

    gold, gold_prev, _ = stooq_last_two("xauusd")
    silver, silver_prev, _ = stooq_last_two("xagusd")

    # WTI (NYMEX light sweet crude) - stable daily close
    wti, wti_prev, _ = stooq_last_two("cl.f")

    return {
        "dji": dji, "dji_chg": pct_change(dji, dji_prev),
        "spx": spx, "spx_chg": pct_change(spx, spx_prev),
        "ndq": ndq, "ndq_chg": pct_change(ndq, ndq_prev),
        "soxx": soxx, "soxx_chg": pct_change(soxx, soxx_prev),

        "y10": y10, "y10_chg": pct_change(y10, y10_prev),
        "y20": y20, "y20_chg": pct_change(y20, y20_prev),
        "y30": y30, "y30_chg": pct_change(y30, y30_prev),

        "gold": gold, "gold_chg": pct_change(gold, gold_prev),
        "silver": silver, "silver_chg": pct_change(silver, silver_prev),

        "wti": wti, "wti_chg": pct_change(wti, wti_prev),
    }


# -----------------------------
# Prompt (brand-style, RM-forwardable, no emoji, no "觀望")
# -----------------------------
def build_prompt(now, s):
    week = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"][now.weekday()]
    title = f"【{now.strftime('%Y年%m月%d日')}（{week}）財經快報】"
    tone = market_tone(s.get("spx_chg"))

    wti_str = f"${fnum(s.get('wti'),2)}" if s.get("wti") is not None else "數據暫缺"

    prompt = f"""
你是銀行投資輔銷團隊的品牌型「每日財經快報總編」。
請用理專可直接轉貼給客戶的口吻：精簡、有盤感、有行動建議。

硬規則（違反就重寫）：
1) 字數 650～900 字（含標點）
2) 必須照版型輸出，段落不可缺漏，尤其「三、股債匯操作策略建議」一定要完整
3) 禁止出現「觀望」兩字（改用：分批/逢低/控風險/保留彈性）
4) 不可杜撰任何新聞細節：地緣/總經只能用「市場關注點」寫法，不要寫具體事件（例如封鎖、軍事行動、某國宣布等）
5) 若 WTI 顯示「數據暫缺」，只能照寫，不得評論

{title}

（開頭2句：昨晚盤勢屬於「{tone}」，點到利率/債市如何影響股市；用快報口吻）

一、 全球市場數據概覽
1. 美股四大指數表現

道瓊工業指數： 收在 {fnum(s.get("dji"),2)} 點，{sign_word(s.get("dji_chg"))} {abs_pct(s.get("dji_chg"))}。（一句話原因）
標普500指數： 收在 {fnum(s.get("spx"),2)} 點，{sign_word(s.get("spx_chg"))} {abs_pct(s.get("spx_chg"))}。（一句話原因）
那斯達克指數： 收在 {fnum(s.get("ndq"),2)} 點，{sign_word(s.get("ndq_chg"))} {abs_pct(s.get("ndq_chg"))}。（一句話原因）
費城半導體指數（以SOXX ETF近似）： 收在 {fnum(s.get("soxx"),2)}，{sign_word(s.get("soxx_chg"))} {abs_pct(s.get("soxx_chg"))}。（一句話原因）

2. 美國國債收益率

10年期美債： {fnum(s.get("y10"),3,"%")}（一句話解讀）
20年期美債： {fnum(s.get("y20"),3,"%")}（一句話解讀）
30年期美債： {fnum(s.get("y30"),3,"%")}（一句話解讀）

3. 匯市與原物料

黃金： ${fnum(s.get("gold"),2)}（一句話）
白銀： ${fnum(s.get("silver"),4)}（一句話）
紐約輕原油（WTI）： {wti_str}

二、 焦點新聞摘要
【地緣政治】用1–2句寫「市場正在關注什麼」與「對油價/風險情緒的影響」，不要寫具體事件。
【總體經濟】用1–2句寫「本週/下次重要數據焦點（CPI/就業/ISM）」與可能的市場反應方向（不寫數據結果）。
【焦點個股】用1–2句寫「AI/半導體/大型權值股」的輪動與注意點（不寫未證實消息）。

三、 股債匯操作策略建議（一定要輸出完整四行）
股市策略：2句（核心：分批逢低承接主流題材與龍頭，同時做部位控管）
債市策略：2句（核心：短端息收 + 投等債；長端作風險對沖）
匯市與原物料策略：2句（核心：金銀拉回分批、油價事件驅動震盪看待）
風險提示：1句（非投資建議）
"""
    return prompt.strip()


# -----------------------------
# OpenAI generation with validation + retry
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

    if "觀望" in text:
        return False

    # keep output in a reasonable range
    if len(text) < 450 or len(text) > 1400:
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
            max_tokens=850,
            messages=[
                {
                    "role": "system",
                    "content": "你是投資輔銷快報總編：必須照版型、短、不可杜撰、不可使用“觀望”。",
                },
                {"role": "user", "content": prompt},
                {
                    "role": "user",
                    "content": "輸出後自我檢查：段落是否齊全、是否包含三條策略、是否未出現“觀望”。不合格請立刻重寫並只輸出最終版。",
                },
            ],
        )
        text = (resp.choices[0].message.content or "").strip()
        # normalize excessive blank lines
        text = re.sub(r"\n{3,}", "\n\n", text)
        last = text

        if _ok(text):
            return text

        prompt += "\n\n（提醒：上次輸出不合格。不得缺少策略段、不得出現“觀望”、不得杜撰地緣事件。）"

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

    if not report or len(report) < 120:
        report = f"【{now.strftime('%Y年%m月%d日')} 財經快報】\n系統提示：今日生成內容不足，請稍後重試。"

    push_line(report)


if __name__ == "__main__":
    main()
