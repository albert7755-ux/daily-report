# -*- coding: utf-8 -*-
import os
import re
import requests
from datetime import datetime, timezone, timedelta
from linebot import LineBotApi
from linebot.models import TextSendMessage
from openai import OpenAI

TZ_TAIPEI = timezone(timedelta(hours=8))

# ---------- helpers ----------
def to_float(v):
    try:
        if v is None: return None
        v = str(v).strip()
        if v == "" or v.lower() == "n/a": return None
        return float(v)
    except Exception:
        return None

def fnum(x, digits=2, suffix=""):
    if x is None: return "N/A"
    return f"{x:,.{digits}f}{suffix}"

def abs_pct(p):
    if p is None: return "N/A"
    return f"{abs(p):.2f}%"

def sign_word(p):
    if p is None: return "變動"
    return "上漲" if p >= 0 else "下跌"

def market_tone(spx_chg):
    if spx_chg is None: return "震盪"
    if spx_chg <= -1.2: return "回檔加深"
    if spx_chg <= -0.3: return "回檔整理"
    if spx_chg >= 1.2: return "強勢推進"
    if spx_chg >= 0.3: return "偏多續行"
    return "區間震盪"

# ---------- yahoo ----------
def yahoo_quote(symbols):
    url = "https://query1.finance.yahoo.com/v7/finance/quote"
    params = {"symbols": ",".join(symbols)}
    r = requests.get(url, params=params, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    data = r.json()
    out = {}
    for item in data.get("quoteResponse", {}).get("result", []):
        sym = item.get("symbol")
        if sym:
            out[sym] = item
    return out

def yf_close(q):
    return to_float(q.get("regularMarketPreviousClose"))

def yf_chg_pct_consistent(q):
    """
    用 change/prevClose 自算，避免你之前遇到的「% 跟收盤不一致」。
    """
    prev = to_float(q.get("regularMarketPreviousClose"))
    chg = to_float(q.get("regularMarketChange"))
    if prev and chg is not None and prev != 0:
        return (chg / prev) * 100.0
    # fallback
    return to_float(q.get("regularMarketChangePercent"))

def yf_yield_pct_from_yahoo_index(q):
    v = to_float(q.get("regularMarketPreviousClose"))
    if v is None:
        v = to_float(q.get("regularMarketPrice"))
    return (v / 100.0) if v is not None else None

def get_snapshot():
    syms = ["^DJI", "^GSPC", "^IXIC", "^TNX", "^TYX", "GC=F", "SI=F", "CL=F"]
    q = yahoo_quote(syms)

    return {
        "dji": yf_close(q.get("^DJI", {})),
        "dji_chg": yf_chg_pct_consistent(q.get("^DJI", {})),

        "spx": yf_close(q.get("^GSPC", {})),
        "spx_chg": yf_chg_pct_consistent(q.get("^GSPC", {})),

        "ndq": yf_close(q.get("^IXIC", {})),
        "ndq_chg": yf_chg_pct_consistent(q.get("^IXIC", {})),

        "y10": yf_yield_pct_from_yahoo_index(q.get("^TNX", {})),
        "y30": yf_yield_pct_from_yahoo_index(q.get("^TYX", {})),

        "gold": yf_close(q.get("GC=F", {})),
        "gold_chg": yf_chg_pct_consistent(q.get("GC=F", {})),

        "silver": yf_close(q.get("SI=F", {})),
        "silver_chg": yf_chg_pct_consistent(q.get("SI=F", {})),

        "wti": yf_close(q.get("CL=F", {})),
        "wti_chg": yf_chg_pct_consistent(q.get("CL=F", {})),
    }

# ---------- prompt ----------
def build_prompt(now, s):
    week = ["週一","週二","週三","週四","週五","週六","週日"][now.weekday()]
    title = f"【{now.strftime('%Y年%m月%d日')}（{week}）財經日報】"
    tone = market_tone(s.get("spx_chg"))
    # 你原本那段 prompt 直接貼回來即可（我省略不改你的風格）
    # 這裡請保留你原本的完整內容
    prompt = f"""{title}

（開頭 2–3 句：昨晚盤勢屬於「{tone}」。...）
...（你的完整版型與規則）...
"""
    return prompt.strip()

def _ok(text: str) -> bool:
    if not text: return False
    must = ["一、 全球市場數據概覽","二、 焦點新聞摘要","三、 股債匯操作策略建議",
            "股市策略：","債市策略：","匯市與原物料策略：","風險提示："]
    if any(m not in text for m in must): return False
    if "觀望" in text or "親愛的" in text or "您好" in text: return False
    if "###" in text or "**" in text: return False
    if "\n-" in text or "\n•" in text: return False
    return 520 <= len(text) <= 2200

def generate_report_from_prompt(prompt: str) -> str:
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
            max_tokens=950,
            messages=[
                {"role":"system","content":"你是投資輔銷市場總編：必須照版型、短、有盤感、不可杜撰、不可使用“觀望”。"},
                {"role":"user","content":prompt},
                {"role":"user","content":"輸出後自我檢查：段落是否齊全、策略四行是否存在、是否未出現“觀望”、是否沒有markdown/條列/稱呼。不合格請立刻重寫並只輸出最終版。"},
            ],
        )
        text = (resp.choices[0].message.content or "").strip()
        text = re.sub(r"\n{3,}", "\n\n", text)
        last = text
        if _ok(text):
            return text
        prompt += "\n\n（提醒：上次輸出不合格。不得缺少策略段、不得出現“觀望”、不得使用markdown/條列/稱呼，且不可杜撰具體事件。）"
    return last

# ===== 這個就是「工具模式」：給 webhook / AI 呼叫 =====
def generate_report_today() -> str:
    now = datetime.now(TZ_TAIPEI)
    snap = get_snapshot()
    prompt = build_prompt(now, snap)
    report = generate_report_from_prompt(prompt)
    if not report or len(report) < 200:
        return f"【{now.strftime('%Y年%m月%d日')} 財經日報】\n系統提示：今日生成內容不足，請稍後重試。"
    return report

# ===== 這個保留你的「排程 push」模式 =====
def push_line(text: str):
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
    user_id = os.environ.get("LINE_USER_ID")
    if not token or not user_id:
        raise ValueError("缺少環境變數：LINE_CHANNEL_ACCESS_TOKEN / LINE_USER_ID")
    api = LineBotApi(token)
    if len(text) <= 4800:
        api.push_message(user_id, TextSendMessage(text=text))
    else:
        api.push_message(user_id, [TextSendMessage(text=text[:4800]), TextSendMessage(text=text[4800:])])

def main():
    report = generate_report_today()
    push_line(report)

if __name__ == "__main__":
    main()
