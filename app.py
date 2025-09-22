import os
import tempfile
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, FileMessage
from supabase import create_client, Client
from pdf_reader import parse_grades_from_pdf
from openai import OpenAI

# Flask app
app = Flask(__name__)

# LINE API設定
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# Supabase設定
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# OpenAI設定
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=OPENAI_API_KEY)


def format_grades(grades):
    """成績データを重複なしで整形"""
    if not grades:
        return "❌ 成績データがありません"

    output_main = []
    output_sub = []
    seen = set()

    # カテゴリごとに最新だけ残すための辞書
    unique_categories = {}

    for g in grades:
        category = g.get("category")
        earned = g.get("earned", 0)
        required = g.get("required", 0)
        remaining = max(0, required - earned)

        if "内訳" in category:
            # 内訳はサブに追加
            status = "✅ 完了" if remaining == 0 else f"残り{remaining}単位"
            output_sub.append(f"  {category.replace('外国語必修内訳_', '')} {earned}/{required} {status}")
        else:
            # メインカテゴリは最新の earned を記録（重複防止）
            unique_categories[category] = (earned, required)

    # メインカテゴリを整形
    for category, (earned, required) in unique_categories.items():
        remaining = max(0, required - earned)
        status = "✅ 完了" if remaining == 0 else f"残り{remaining}単位"
        output_main.append(f"{category} {earned}/{required} {status}")

    # 卒業要件の計算（124単位で固定）
    total_required = 124
    total_earned = sum(earned for earned, _ in unique_categories.values())

    grad_status = (
        f"🎓 卒業必要単位数: {total_required}\n"
        f"✅ 取得済み単位数: {total_earned}\n"
    )
    grad_status += "🎉 おめでとうございます！卒業要件を満たしています" if total_earned >= total_required else "📌 まだ卒業要件を満たしていません"

    # 最終メッセージ
    result = "📊 === 単位取得状況分析結果 ===\n" + "\n".join(output_main)
    if output_sub:
        result += "\n\n📋 === 備考欄（必修内訳）===\n" + "\n".join(output_sub)
    result += "\n\n" + grad_status

    return result


@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers["X-Line-Signature"]
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)

    return "OK"


@handler.add(MessageEvent, message=TextMessage)
def handle_text_message(event):
    user_id = event.source.user_id
    text = event.message.text.strip()

    # 成績に関するキーワード
    if "成績" in text or "単位" in text:
        response = supabase.table("grades").select("*").eq("user_id", user_id).execute()
        if response.data:
            grades = response.data
            message = format_grades(grades)
        else:
            message = "❌ 成績データが見つかりません。PDFを送ってね！"

    # 成績アドバイス
    elif "成績についてのアドバイス" in text:
        response = supabase.table("grades").select("*").eq("user_id", user_id).execute()
        grades = response.data
        if grades:
            formatted = format_grades(grades)
            try:
                completion = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {"role": "system", "content": "あなたは明治大学の学生をサポートするアシスタントです。成績データを参考にアドバイスしてください。"},
                        {"role": "user", "content": f"以下の成績データに基づいてアドバイスしてください:\n{formatted}"},
                    ],
                )
                message = completion.choices[0].message.content
            except Exception as e:
                message = f"💡 アドバイス生成に失敗しました: {e}"
        else:
            message = "❌ 成績データが見つかりません。PDFを送ってね！"

    # 事務室の連絡先
    elif any(k in text for k in ["事務室の連絡先", "事務の連絡先", "電話番号", "経営学部の電話番号", "経営の電話番号", "経営学部の事務室の電話番号", "経営学部の問い合わせ"]):
        response = supabase.table("contacts").select("*").execute()
        if response.data:
            rows = [f"{r['department']}: {r['contact']}" for r in response.data]
            message = "📞 明治大学の事務室連絡先:\n" + "\n".join(rows)
        else:
            message = "❌ 事務室の連絡先情報が見つかりません"

    # 履修条件・卒業要件
    elif any(k in text for k in ["履修条件", "卒業要件"]):
        response = supabase.table("curriculum_docs").select("*").execute()
        if response.data:
            rows = [f"{r['title']}: {r['content']}" for r in response.data]
            message = "📘 履修条件・卒業要件:\n" + "\n".join(rows)
        else:
            message = "❌ 情報が見つかりません"

    else:
        # 雑談
        try:
            completion = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "あなたは明治大学の学生をサポートするアシスタントです。"},
                    {"role": "user", "content": text},
                ],
            )
            message = completion.choices[0].message.content
        except Exception as e:
            message = f"💡 雑談の生成に失敗しました: {e}"

    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=message))


@handler.add(MessageEvent, message=FileMessage)
def handle_file_message(event):
    user_id = event.source.user_id
    file_name = event.message.file_name

    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
        file_path = tmp_file.name
        message_content = line_bot_api.get_message_content(event.message.id)
        for chunk in message_content.iter_content():
            tmp_file.write(chunk)

    try:
        grades = parse_grades_from_pdf(file_path)

        # 古いデータを削除 → 最新のPDFデータだけ保存
        supabase.table("grades").delete().eq("user_id", user_id).execute()

        for g in grades:
            supabase.table("grades").upsert(
                {
                    "user_id": user_id,
                    "category": g["category"],
                    "earned": g["earned"],
                    "required": g["required"],
                }
            ).execute()

        message = "✅ PDFを保存しました！\n\n" + format_grades(grades)

    except Exception as e:
        message = f"❌ PDFの解析に失敗しました: {e}"

    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=message))


@app.route("/", methods=["GET"])
def index():
    return "LINE Bot is running!"


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
