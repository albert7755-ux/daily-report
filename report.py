import os
import re
from datetime import datetime, timezone, timedelta, date
from typing import List, Dict, Any, Optional

from fastapi import FastAPI, Request, HTTPException
from linebot import LineBotApi, WebhookParser
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from linebot.exceptions import InvalidSignatureError

from sqlalchemy import create_engine, text
from openai import OpenAI

# ====== ENV ======
LINE_CHANNEL_ACCESS_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
LINE_CHANNEL_SECRET = os.environ["LINE_CHANNEL_SECRET"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
DATABASE_URL = os.environ["DATABASE_URL"]

# Optional: 你可以改成你想用的模型
CHAT_MODEL = os.environ.get("CHAT_MODEL", "gpt-4.1-mini")

# 最近幾則對話要帶進 LLM（省 token）
RECENT_N = int(os.environ.get("RECENT_N", "12"))

TZ_TAIPEI = timezone(timedelta(hours=8))

# ====== Clients ======
app = FastAPI()
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
parser = WebhookParser(LINE_CHANNEL_SECRET)
client = OpenAI(api_key=OPENAI_API_KEY)
engine = create_engine(DATABASE_URL, pool_pre_ping=True)


# ====== Your tools (自己寫的 functions) ======
# 你可以把工具放到 tools/ 目錄，再在這裡 import
# 例如 from report import generate_report

def generate_report(style: str = "brief") -> str:
    """
    範例工具：你之後改成 import 你的 report.py 即可
    """
    now = datetime.now(TZ_TAIPEI).strftime("%Y-%m-%d %H:%M")
    if style == "brief":
        return f"【財經日報（示範）】\n時間：{now}\n- 這裡接你 report.py 的輸出\n"
    return f"【財經日報（示範/詳細）】\n時間：{now}\n- ...\n"


# ====== DB init ======
def init_db():
    with engine.begin() as conn:
        conn.execute(text("""
        CREATE TABLE IF NOT EXISTS chat_messages (
            id BIGSERIAL PRIMARY KEY,
            user_id TEXT NOT NULL,
            role TEXT NOT NULL,            -- 'user' | 'assistant'
            content TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """))
        conn.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_chat_messages_user_time
        ON chat_messages(user_id, created_at DESC);
        """))
        conn.execute(text("""
        CREATE TABLE IF NOT EXISTS daily_summary (
            user_id TEXT NOT NULL,
            ymd DATE NOT NULL,
            summary TEXT NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (user_id, ymd)
        );
        """))

init_db()


# ====== DB helpers ======
def save_msg(user_id: str, role: str, content: str):
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO chat_messages(user_id, role, content) VALUES (:u, :r, :c)"),
            {"u": user_id, "r": role, "c": content},
        )

def load_recent_messages(user_id: str, limit: int) -> List[Dict[str, str]]:
    with engine.begin() as conn:
        rows = conn.execute(
            text("""
            SELECT role, content
            FROM chat_messages
            WHERE user_id = :u
            ORDER BY created_at DESC
            LIMIT :n
            """),
            {"u": user_id, "n": limit},
        ).fetchall()
    # 倒序 → 正序
    return [{"role": r[0], "content": r[1]} for r in reversed(rows)]

def get_today_summary(user_id: str) -> str:
    today = datetime.now(TZ_TAIPEI).date()
    with engine.begin() as conn:
        row = conn.execute(
            text("""
            SELECT summary
            FROM daily_summary
            WHERE user_id = :u AND ymd = :d
            """),
            {"u": user_id, "d": today},
        ).fetchone()
    return row[0] if row else ""

def upsert_today_summary(user_id: str, new_summary: str):
    today = datetime.now(TZ_TAIPEI).date()
    with engine.begin() as conn:
        conn.execute(text("""
        INSERT INTO daily_summary(user_id, ymd, summary, updated_at)
        VALUES (:u, :d, :s, NOW())
        ON CONFLICT (user_id, ymd)
        DO UPDATE SET summary = EXCLUDED.summary, updated_at = NOW()
        """), {"u": user_id, "d": today, "s": new_summary})


# ====== Command router (你要先跑的指令路由) ======
def is_command(s: str) -> bool:
    return s.strip().startswith("/")

def handle_command(user_text: str) -> str:
    t = user_text.strip()

    if t == "/help":
        return (
            "可用指令：\n"
            "/help\n"
            "/calc 1+2*3\n"
            "/report\n"
            "\n不是指令也可以直接聊天，我會用 AI 回答。"
        )

    if t.startswith("/calc"):
        expr = t[len("/calc"):].strip()
        if not expr:
            return "用法：/calc 1+2*3"
        if not re.fullmatch(r"[0-9\.\+\-\*\/\(\)\s]+", expr):
            return "算式格式不支援（只允許數字與 + - * / ( ) ）"
        try:
            result = eval(expr, {"__builtins__": {}})
            return f"{expr} = {result}"
        except Exception:
            return "算式計算失敗，請檢查格式。"

    if t == "/report":
        # 這裡直接呼叫你的工具
        return generate_report(style="brief")

    return "指令不明。輸入 /help 看可用指令。"


# ====== Tool calling schema (給 LLM 用) ======
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "generate_report",
            "description": "產生今日財經日報（給LINE回覆用）",
            "parameters": {
                "type": "object",
                "properties": {
                    "style": {
                        "type": "string",
                        "enum": ["brief", "detailed"],
                        "description": "日報長度風格"
                    }
                },
                "required": []
            }
        }
    }
]

def dispatch_tool_call(name: str, arguments: Dict[str, Any]) -> str:
    if name == "generate_report":
        style = arguments.get("style", "brief")
        return generate_report(style=style)
    return f"工具不存在：{name}"


# ====== AI chat with memory + tools ======
def build_system_prompt(today_summary: str) -> str:
    return (
        "你是一個在 LINE 上提供協助的 AI 助理。\n"
        "要求：回答要精準、可執行、少廢話。\n"
        "你可以使用工具（functions）來完成任務。\n"
        "\n"
        "【今日摘要（可用來理解今天脈絡）】\n"
        f"{today_summary if today_summary else '（今天目前沒有摘要）'}\n"
    )

def ai_chat(user_id: str, user_text: str) -> str:
    today_summary = get_today_summary(user_id)
    recent = load_recent_messages(user_id, limit=RECENT_N)

    messages = [{"role": "system", "content": build_system_prompt(today_summary)}]
    messages += recent
    messages += [{"role": "user", "content": user_text}]

    # 1) 先讓模型決定要不要呼叫工具
    resp = client.chat.completions.create(
        model=CHAT_MODEL,
        messages=messages,
        temperature=0.4,
        tools=TOOLS,
        tool_choice="auto",
    )

    msg = resp.choices[0].message

    # 2) 如果模型要呼叫工具：執行工具，再把結果回給模型整理成 final reply
    if getattr(msg, "tool_calls", None):
        tool_outputs = []
        for tc in msg.tool_calls:
            name = tc.function.name
            args = {}
            try:
                # OpenAI SDK 會給 JSON string
                import json
                args = json.loads(tc.function.arguments or "{}")
            except Exception:
                args = {}

            tool_result = dispatch_tool_call(name, args)
            tool_outputs.append(
                {"role": "tool", "tool_call_id": tc.id, "content": tool_result}
            )

        # 把 tool result 加入對話，再讓模型生成最終回覆
        messages.append({"role": "assistant", "content": msg.content or "", "tool_calls": msg.tool_calls})
        messages += tool_outputs

        resp2 = client.chat.completions.create(
            model=CHAT_MODEL,
            messages=messages,
            temperature=0.4,
        )
        final = resp2.choices[0].message.content.strip()
        return final

    # 3) 不用工具：直接回覆
    return (msg.content or "").strip()


# ====== Optional: update daily summary (省 token 的關鍵) ======
def update_daily_summary(user_id: str, user_text: str, assistant_text: str):
    """
    把今天的摘要維持在短短幾行，讓下一次對話少帶很多歷史訊息。
    你也可以先不開這段，確認聊天穩定後再開。
    """
    current = get_today_summary(user_id)
    prompt = (
        "請把以下內容整合成『今天的短摘要』，限制 6-10 行，每行不超過 20 字。\n"
        "摘要要保留：重要任務、重要結論、待辦。\n\n"
        f"【既有今日摘要】\n{current if current else '（無）'}\n\n"
        f"【新增對話】\n使用者：{user_text}\n助理：{assistant_text}\n"
    )

    resp = client.chat.completions.create(
        model=CHAT_MODEL,
        messages=[
            {"role": "system", "content": "你是一個擅長做極短摘要的助理。"},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
    )
    new_summary = resp.choices[0].message.content.strip()
    upsert_today_summary(user_id, new_summary)


# ====== LINE webhook endpoint ======
@app.post("/callback")
async def callback(request: Request):
    signature = request.headers.get("X-Line-Signature")
    body = (await request.body()).decode("utf-8")

    try:
        events = parser.parse(body, signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    for event in events:
        if isinstance(event, MessageEvent) and isinstance(event.message, TextMessage):
            user_id = event.source.user_id
            user_text = event.message.text.strip()

            # 1) 先寫入 DB（使用者訊息）
            save_msg(user_id, "user", user_text)

            # 2) 先跑指令路由
            if is_command(user_text):
                reply = handle_command(user_text)
            else:
                # 3) 非指令 → AI（含：DB記憶 + 工具）
                reply = ai_chat(user_id, user_text)

            # 4) 回覆 + 寫入 DB（助理回覆）
            save_msg(user_id, "assistant", reply)

            # 5) 更新今日摘要（可選；很推薦）
            if not is_command(user_text):
                try:
                    update_daily_summary(user_id, user_text, reply)
                except Exception:
                    # 摘要失敗不影響回覆
                    pass

            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=reply[:4900])
            )

    return "OK"
