import os
import requests
import yfinance as yf
from datetime import datetime, timedelta
import anthropic
import pytz

# ========== 設定區 ==========
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_USER_ID = os.environ.get("LINE_USER_ID")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")

# ========== 抓取市場數據 ==========
def get_market_data():
    tickers = {
        "道瓊 (DJI)":      "^DJI",
        "標普500 (S&P)":   "^GSPC",
        "那斯達克 (IXIC)": "^IXIC",
        "費半 (SOX)":      "^SOX",
        "10年期美債":       "^TNX",
        "黃金":             "GC=F",
        "白銀":             "SI=F",
        "原油 (WTI)":      "CL=F",
    }

    results = {}
    for name, symbol in tickers.items():
        try:
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period="2d")
            if len(hist) >= 2:
                prev_close = hist["Close"].iloc[-2]
                last_close = hist["Close"].iloc[-1]
                change = last_close - prev_close
                pct = (change / prev_close) * 100
                results[name] = {
                    "price": round(last_close, 2),
                    "change": round(change, 2),
                    "pct": round(pct, 2),
                }
            else:
                results[name] = None
        except Exception as e:
            results[name] = None
            print(f"Error fetching {name}: {e}")

    return results

# ========== 格式化數據為文字 ==========
def format_market_data(data):
    tw_tz = pytz.timezone("Asia/Taipei")
    today = datetime.now(tw_tz)
    yesterday = today - timedelta(days=1)
    weekday_map = ["週一","週二","週三","週四","週五","週六","週日"]

    lines = []
    lines.append(f"【{today.strftime('%Y年%m月%d日')}（{weekday_map[today.weekday()]}）財經日報】")
    lines.append(f"反映 {yesterday.strftime('%m/%d')}（{weekday_map[yesterday.weekday()]}）美股收盤\n")

    lines.append("一、美股四大指數")
    for name in ["道瓊 (DJI)", "標普500 (S&P)", "那斯達克 (IXIC)", "費半 (SOX)"]:
        d = data.get(name)
        if d:
            arrow = "▲" if d["change"] >= 0 else "▼"
            lines.append(f"- {name}：{d['price']:,.2f} 點　{arrow}{abs(d['change']):,.2f}（{d['pct']:+.2f}%）")
        else:
            lines.append(f"- {name}：數據抓取失敗")

    lines.append("\n二、美國10年期公債殖利率")
    d = data.get("10年期美債")
    if d:
        arrow = "▲" if d["change"] >= 0 else "▼"
        lines.append(f"- 10年期：{d['price']:.3f}%　{arrow}{abs(d['change']):.3f}%")
    else:
        lines.append("- 10年期：數據抓取失敗")

    lines.append("\n三、原物料")
    commodities = [
        ("黃金",      "黃金",       "盎司"),
        ("白銀",      "白銀",       "盎司"),
        ("原油 (WTI)","原油（WTI）","桶"),
    ]
    for key, label, unit in commodities:
        d = data.get(key)
        if d:
            arrow = "▲" if d["change"] >= 0 else "▼"
            lines.append(f"- {label}：${d['price']:,.2f}／{unit}　{arrow}{abs(d['change']):,.2f}（{d['pct']:+.2f}%）")
        else:
            lines.append(f"- {label}：數據抓取失敗")

    return "\n".join(lines)

# ========== 用 Claude 生成完整日報 ==========
def generate_report_with_claude(market_text):
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    prompt = f"""
你是一位專業的財經日報撰寫助理。以下是今日的市場原始數據：

{market_text}

請幫我完成以下任務：
1. 根據數據寫一段簡短的「市場總覽」（2-3句，說明今天市場整體氛圍）
2. 搜尋今日最重要的總體經濟新聞2則（一句話摘要）
3. 搜尋今日最重要的個股新聞2則（一句話摘要）
4. 根據數據給出股市、債市、原物料三個方向的簡短操作策略（各一句話）

輸出格式請完全按照以下結構，純文字條列，不要表格：

{market_text}

【市場總覽】
（在這裡寫）

四、焦點新聞

【總經】
- （新聞1）
- （新聞2）

【個股】
- （新聞1）
- （新聞2）

五、操作策略
- 股市：
- 債市：
- 原物料：

以上為根據最新收盤校對之報表。
"""

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1500,
        tools=[{"type": "web_search_20250305", "name": "web_search"}],
        messages=[{"role": "user", "content": prompt}]
    )

    full_text = ""
    for block in message.content:
        if hasattr(block, "text"):
            full_text += block.text

    return full_text

# ========== 推送 LINE ==========
def send_line_message(text):
    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"
    }
    payload = {
        "to": LINE_USER_ID,
        "messages": [{"type": "text", "text": text}]
    }
    response = requests.post(url, headers=headers, json=payload)
    if response.status_code == 200:
        print("✅ LINE 推送成功")
    else:
        print(f"❌ LINE 推送失敗：{response.status_code} {response.text}")

# ========== 主程式 ==========
def main():
    print("📊 開始抓取市場數據...")
    market_data = get_market_data()

    print("📝 格式化數據...")
    market_text = format_market_data(market_data)

    print("🤖 Claude 生成日報中...")
    report = generate_report_with_claude(market_text)

    print("📲 推送 LINE...")
    send_line_message(report)

    print("✅ 完成！")

if __name__ == "__main__":
    main()
```

---

