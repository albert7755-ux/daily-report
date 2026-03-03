def build_prompt(now, s, news):
    week = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"][now.weekday()]
    title = f"【{now.strftime('%Y年%m月%d日')}（{week}）財經日報】"

    geo_titles = news.get("geo", [])[:3]
    macro_titles = news.get("macro", [])[:3]
    tech_titles = news.get("tech", [])[:3]

    geo_block = "\n".join([f"- {t}" for t in geo_titles]) if geo_titles else "- （今日抓不到地緣標題：請改用“油價/避險情緒/航運風險”做一句短評，不可杜撰事件）"
    macro_block = "\n".join([f"- {t}" for t in macro_titles]) if macro_titles else "- （今日抓不到總經標題：請改用“本週關鍵數據（CPI/就業/ISM）市場會看什麼”做一句短評，不可杜撰數據結果）"
    tech_block = "\n".join([f"- {t}" for t in tech_titles]) if tech_titles else "- （今日抓不到科技標題：請用“AI/半導體資金輪動”做一句短評，不可杜撰公司事件）"

    # SOX 可能 N/A，就不要硬寫
    sox_line = ""
    if s.get("sox") is not None and s.get("sox_chg") is not None:
        sox_line = (
            f"\n\n費城半導體指數 (SOX)： 收在 {fnum(s['sox'],2)} 點，"
            f"{'上漲' if s['sox_chg']>=0 else '下跌'} {abs(s['sox_chg']):.2f}%。"
            "（一句話：半導體/AI 族群情緒）"
        )

    prompt = f"""
你是銀行理專團隊的「每日財經日報總編」。
請用「媒體快報節奏 + 理專可直接轉貼」口吻撰寫。

【極重要規則】（違反任何一條就重寫再輸出）：
- 字數：650～900 字（含標點），不要寫長
- 結構：只能用我指定的三大段落與小標（完全照抄），不得改成你的格式、不得用 1.2.3 清單
- 新聞：只能根據我提供的標題清單寫摘要，不可編造任何不存在的事件/機構/數字
- 必須包含：地緣政治 1 則 + 總經 1 則（若抓不到就寫“本週關鍵數據/市場關注點”，不可瞎掰結果）
- 每則新聞 1–2 句；不要空泛；要接地氣（直接講影響股/債/油/金）
- 內容只能輸出一次，不可重複整篇

請輸出以下版型（段落標題必須一字不差）：

{title}

（開頭 2 句：先講昨天美股為什麼跌/漲，必須點到「利率/債市」或「地緣政治」其中之一，語氣像快報）

一、 全球市場數據概覽
1. 美股四大指數表現

道瓊工業指數 (DJI)： 收在 {fnum(s['dji'],2)} 點，{'上漲' if (s.get('dji_chg') or 0)>=0 else '下跌'} {abs(s.get('dji_chg') or 0):.2f}%。（一句話原因）
標普 500 指數 (S&P 500)： 收在 {fnum(s['spx'],2)} 點，{'上漲' if (s.get('spx_chg') or 0)>=0 else '下跌'} {abs(s.get('spx_chg') or 0):.2f}%。（一句話原因）
那斯達克指數 (IXIC)： 收在 {fnum(s['ndq'],2)} 點，{'上漲' if (s.get('ndq_chg') or 0)>=0 else '下跌'} {abs(s.get('ndq_chg') or 0):.2f}%。（一句話原因）{sox_line}

2. 美國國債收益率 (Yield)

10年期美債： 報 {fnum(s['y10'],3,'%')}（一句話：利率上/下對股市情緒的解讀）
20年期美債： 報 {fnum(s['y20'],3,'%')}（一句話）
30年期美債： 報 {fnum(s['y30'],3,'%')}（一句話）

3. 原物料商品表現

黃金 (Spot Gold)： 報 ${fnum(s['gold'],2)}（一句話）
白銀 (Spot Silver)： 報 ${fnum(s['silver'],4)}（一句話）
鈾礦 (Uranium)： 報 ${fnum(s['uranium'],2)}（一句話）
原油 (WTI)： 報 ${fnum(s['wti'],2)}（一句話）

二、 焦點新聞摘要
【地緣政治】（必寫 1 則，1–2 句）
只能從這些標題挑一則來寫：
{geo_block}

【總體經濟】（必寫 1 則，1–2 句）
只能從這些標題挑一則來寫（或用“本週關鍵數據/市場關注點”）：
{macro_block}

【焦點個股】（寫 1–2 則，每則 1 句即可）
只能從這些標題挑：
{tech_block}

三、 股債匯操作策略建議
股市策略：2 句（不追高/分批/等回測/控風險）
債市策略：2 句（短端息收 + 長端避險或投等債息收）
匯市與原物料策略：2 句（美元/金銀/油的做法）
風險提示：1 句（非投資建議）
"""
    return prompt.strip()
