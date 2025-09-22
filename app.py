import os
import tempfile
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, FileMessage
from supabase import create_client, Client
from pdf_reader import parse_grades_from_pdf

app = Flask(__name__)

# 環境変数からキーを取得
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

supabase: Client = None
if SUPABASE_URL and SUPABASE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


def save_grades_to_supabase(user_id, grades, total_credits):
    """Supabaseに成績データを保存"""
    if not supabase:
        return False

    supabase.table("grades").delete().eq("user_id", user_id).execute()

    for g in grades:
        supabase.table("grades").insert({
            "user_id": user_id,
            "category": g["category"],
            "earned": g["earned"],
            "required": g["required"]
        }).execute()

    supabase.table("grades").insert({
        "user_id": user_id,
        "category": "総取得単位",
        "earned": total_credits,
        "required": 124
    }).execute()
    return True


def check_graduation_status(user_id):
    """Supabaseからデータを取得し、便覧と突き合わせ"""
    if not supabase:
        return None

    grades_data = supabase.table("grades").select("*").eq("user_id", user_id).execute()
    curriculum_data = supabase.table("curriculum").select("*").execute()

    if not grades_data.data:
        return None

    results = []
    for c in curriculum_data.data:
        g = next((x for x in grades_data.data if x["category"] == c["category"]), None)
        earned = g["earned"] if g else 0
        required = c["required_units"]
        results.append({
            "category": c["category"],
            "earned": earned,
            "required": required,
            "remaining": max(0, required - earned),
            "notes": c.get("notes", "")
        })
    return results


def format_graduation_status(results):
    """卒業要件状況をフォーマット"""
    if not results:
        return "📊 成績データがありません。"
    
    lines = ["📊 あなたの成績状況まとめ:"]
    for r in results:
        line = f"{r['category']}: {r['earned']}/{r['required']} (残り{r['remaining']}単位)"
        if r.get("notes"):
            line += f"\n📝 {r['notes']}"
        lines.append(line)
    return "\n".join(lines)


@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers["X-Line-Signature"]
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)

    return "OK"


@handler.add(MessageEvent, message=FileMessage)
def handle_file(event):
    user_id = event.source.user_id
    message_content = line_bot_api.get_message_content(event.message.id)

    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
        for chunk in message_content.iter_content():
            tmp_file.write(chunk)
        tmp_path = tmp_file.name

    try:
        grades, total_credits = parse_grades_from_pdf(tmp_path)
        if not grades:
            raise ValueError("解析結果が空です")

        save_grades_to_supabase(user_id, grades, total_credits)
        reply = "✅ PDFを保存しました！\n\n" + format_graduation_status(check_graduation_status(user_id))
    except Exception as e:
        reply = f"❌ PDFの解析に失敗しました: {str(e)}"

    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))


@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_id = event.source.user_id
    text = event.message.text.strip()

    # 楽単フォームリンク
    if any(k in text for k in ["楽単", "おすすめ授業", "取りやすい授業"]):
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(
                text="📮 楽単情報はこちらから投稿してね！\nhttps://docs.google.com/forms/d/e/1FAIpQLSfw654DpwVoSexb3lI8WLqsR6ex1lRYEX_6Yg1g-S57tw2JBQ/viewform?usp=header"
            )
        )
        return

    # 成績確認
    if any(k in text for k in ["成績", "卒業", "単位", "不足", "あと何"]):
        if supabase:
            status = check_graduation_status(user_id)
            if status:
                reply = format_graduation_status(status)
            else:
                reply = "⚠️ まだ成績データが登録されていません。PDFを送ってね！"
        else:
            reply = "⚠️ 成績データの保存機能が無効です。PDFを送ると解析結果を表示します。"
        
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        return

    # 雑談や通常会話
    line_bot_api.reply_message(
        event.reply_token, 
        TextSendMessage(text=f"😊 {text} だね！何か成績や履修のことも気になる？")
    )


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
