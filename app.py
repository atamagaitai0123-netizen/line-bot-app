import os
import tempfile
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, FileMessage
from supabase import create_client
import pdf_reader
import openai

# ============ 初期化 ============
app = Flask(__name__)

# LINE
line_bot_api = LineBotApi(os.getenv("LINE_CHANNEL_ACCESS_TOKEN"))
handler = WebhookHandler(os.getenv("LINE_CHANNEL_SECRET"))

# Supabase
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# OpenAI
openai_client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ============ Supabase ユーティリティ ============
def save_grades(user_id, parsed_result):
    # 既存データを削除して上書き保存
    supabase.table("grades").delete().eq("user_id", user_id).execute()

    rows = []
    for item in parsed_result:
        rows.append({
            "user_id": user_id,
            "category": item["category"],
            "required": item["required"],
            "earned": item["earned"]
        })
    supabase.table("grades").insert(rows).execute()

def get_latest_grades(user_id):
    response = supabase.table("grades") \
        .select("*") \
        .eq("user_id", user_id) \
        .order("created_at", desc=True) \
        .limit(50) \
        .execute()
    return response.data

def get_curriculum(department="経営学科"):
    response = supabase.table("curriculum") \
        .select("*") \
        .eq("department", department) \
        .execute()
    return response.data

def get_curriculum_docs(department="経営学科", limit=10):
    response = supabase.table("curriculum_docs") \
        .select("*") \
        .eq("department", department) \
        .limit(limit) \
        .execute()
    return [r["content"] for r in response.data]

# ============ 卒業要件チェック ============
def check_graduation_status(user_id):
    grades = get_latest_grades(user_id)
    curriculum = get_curriculum()

    results = []
    for rule in curriculum:
        g = next((x for x in grades if x["category"] == rule["category"]), None)

        # None を 0 に変換して安全に処理
        earned = g["earned"] if g and g.get("earned") is not None else 0
        required = rule["required_units"] if rule.get("required_units") is not None else 0

        results.append({
            "category": rule["category"],
            "earned": earned,
            "required": required,
            "remaining": max(0, required - earned),
            "notes": rule["notes"]
        })
    return results

# ============ OpenAI ユーティリティ ============
def ask_openai(prompt):
    response = openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}]
    )
    return response.choices[0].message.content.strip()

# ============ Flask ルーティング ============
@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers["X-Line-Signature"]
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)

    return "OK"

# ============ LINE ハンドラ ============
# PDF ファイル受信
@handler.add(MessageEvent, message=FileMessage)
def handle_file(event):
    user_id = event.source.user_id
    message_content = line_bot_api.get_message_content(event.message.id)

    # 一時ファイルに保存
    with tempfile.NamedTemporaryFile(delete=False) as temp_file:
        for chunk in message_content.iter_content():
            temp_file.write(chunk)
        temp_file_path = temp_file.name

    # PDF解析 (dict形式で取得)
    parsed = pdf_reader.check_pdf(temp_file_path, return_dict=True)

    if "error" in parsed:
        reply_text = f"PDF解析エラー: {parsed['error']}"
    else:
        parsed_result = []
        for cat, (earned, required) in parsed["results"].items():
            parsed_result.append({
                "category": cat,
                "required": required,
                "earned": earned
            })

        save_grades(user_id, parsed_result)

        # 成績分析も返す
        grades_status = check_graduation_status(user_id)
        summary = "\n".join(
            [f"{s['category']}: {s['earned']}/{s['required']} (残り{s['remaining']}単位)" for s in grades_status]
        )
        reply_text = f"✅ PDFを保存しました！\n\n📊 成績状況:\n{summary}"

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply_text)
    )

# テキストメッセージ受信
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_id = event.source.user_id
    user_text = event.message.text

    # 楽単フォームリンクを返す条件
    if any(keyword in user_text for keyword in ["楽単", "おすすめ授業", "取りやすい授業"]):
        reply_text = "📋 楽単情報共有フォームはこちら！\n\n👉 https://docs.google.com/forms/d/e/1FAIpQLSfw654DpwVoSexb3lI8WLqsR6ex1lRYEX_6Yg1g-S57tw2JBQ/viewform?usp=header"
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=reply_text)
        )
        return

    # 成績や単位に関する質問かどうか判定
    if any(keyword in user_text for keyword in ["成績", "単位", "卒業", "必修", "履修"]):
        grades_status = check_graduation_status(user_id)
        docs = get_curriculum_docs()

        grades_text = "\n".join(
            [f"{s['category']}: {s['earned']}/{s['required']} (残り{s['remaining']}単位)" for s in grades_status]
        )

        if "詳細" in user_text:
            style = "詳細に説明してください。"
        else:
            style = "要点を簡潔にまとめ、絵文字は2〜3個までにしてください。"

        prompt = f"""
以下は大学便覧に基づく情報です:
{docs}

以下はユーザーの成績状況です:
{grades_text}

ユーザーの質問: {user_text}

{style}
"""
    else:
        # 普通の会話モード
        prompt = f"""
ユーザーの質問: {user_text}

自然な会話で簡潔に答えてください。
絵文字は使っても1つまで。
"""

    answer = ask_openai(prompt)

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=answer)
    )

# ヘルスチェック用
@app.route("/health")
def health():
    return "OK", 200

# ============ メイン ============
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
