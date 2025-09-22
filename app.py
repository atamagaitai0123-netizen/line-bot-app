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

# 楽単フォームリンク
EASY_COURSE_FORM_URL = "https://docs.google.com/forms/d/e/1FAIpQLSfw654DpwVoSexb3lI8WLqsR6ex1lRYEX_6Yg1g-S57tw2JBQ/viewform?usp=header"

# 楽単キーワード
EASY_KEYWORDS = ["楽単", "らくたん", "おすすめ授業", "簡単な授業"]

# 便覧キーワード
CURRICULUM_KEYWORDS = ["卒業要件", "履修条件", "進級要件", "卒業", "履修登録"]

# 内訳行を識別するプレフィックス
DETAIL_PREFIX = "外国語必修内訳_"


def is_detail_category(category: str) -> bool:
    """内訳（詳細）カテゴリかどうか判定"""
    if not category:
        return False
    return str(category).startswith(DETAIL_PREFIX)


def format_grades(grades):
    """成績データを重複なしで整形（※総計は内訳を除外して算出）"""
    if not grades:
        return "❌ 成績データがありません"

    output_main = []
    output_sub = []
    seen = set()

    for g in grades:
        category = g.get("category")
        earned = g.get("earned", 0)
        required = g.get("required", 0)
        remaining = max(0, required - earned)

        # 内訳はサブ出力に回す（画面表示用）
        if is_detail_category(category):
            status = "✅ 完了" if remaining == 0 else f"残り{remaining}単位"
            # 表示用にプレフィックスをはずす
            display_name = category.replace(DETAIL_PREFIX, "")
            output_sub.append(f"  {display_name} {earned}/{required} {status}")
            continue

        # 重複チェック（メインカテゴリ）
        if category not in seen:
            seen.add(category)
            status = "✅ 完了" if remaining == 0 else f"残り{remaining}単位"
            output_main.append(f"{category} {earned}/{required} {status}")

    # --- ここが重要：総必要単位と総取得単位は「内訳」を除いたメイン行だけで計算する ---
    main_entries = [g for g in grades if not is_detail_category(g.get("category"))]
    total_required = sum(int(g.get("required", 0)) for g in main_entries)
    total_earned = sum(int(g.get("earned", 0)) for g in main_entries)

    # 卒業判定表示
    grad_status = (
        f"🎓 卒業必要単位数: {total_required}\n"
        f"✅ 取得済み単位数: {total_earned}\n"
    )
    grad_status += "🎉 おめでとうございます！卒業要件を満たしています" if total_earned >= total_required else "📌 まだ卒業要件を満たしていません"

    result = "📊 === 単位取得状況分析結果 ===\n" + "\n".join(output_main)
    if output_sub:
        result += "\n\n📋 === 備考欄（必修内訳）===\n" + "\n".join(output_sub)
    result += "\n\n" + grad_status

    return result


def answer_with_grades(user_id: str, question: str) -> str:
    """ユーザーの質問に応じて成績データを解析して回答"""
    response = supabase.table("grades").select("*").eq("user_id", user_id).execute()
    if not response.data:
        return "❌ 成績データが見つかりません。PDFを送ってね！"

    # response.data は複数行（各カテゴリ）なので dict を作る
    grades_by_cat = {g["category"]: g for g in response.data}

    # --- 卒業までの残り単位（内訳は除外） ---
    if "あと何単位" in question or ("卒業" in question and "あと" in question) or (question.strip() == "卒業"):
        main_entries = [g for g in response.data if not is_detail_category(g.get("category"))]
        total_required = sum(int(g.get("required", 0)) for g in main_entries)
        total_earned = sum(int(g.get("earned", 0)) for g in main_entries)
        remaining = max(0, total_required - total_earned)
        if remaining == 0:
            return f"🎉 おめでとうございます！すでに卒業要件を満たしています！（取得済み: {total_earned} 単位）"
        else:
            return f"📚 卒業までに必要な残り単位は {remaining} 単位です！（取得済み: {total_earned}/{total_required}）"

    # 自由履修の確認
    if "自由" in question:
        g = grades_by_cat.get("自由履修科目")
        if g:
            remaining = max(0, int(g.get("required", 0)) - int(g.get("earned", 0)))
            return f"📝 自由履修は {g.get('earned')}/{g.get('required')} 単位。残り {remaining} 単位必要です！"

    # 外国語必修の確認（内訳行を取り出して表示）
    if "外国語" in question:
        subcats = [g for g in response.data if is_detail_category(g.get("category"))]
        if subcats:
            details = []
            for g in subcats:
                remaining = max(0, int(g.get("required", 0)) - int(g.get("earned", 0)))
                status = "✅ クリア済み" if remaining == 0 else f"❌ 残り {remaining} 単位"
                display_name = g["category"].replace(DETAIL_PREFIX, "")
                details.append(f"{display_name}: {g['earned']}/{g['required']} {status}")
            return "🌍 外国語必修の状況:\n" + "\n".join(details)

    # デフォルト：全体表示（format_grades で整形）
    return format_grades(response.data)


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

    # 成績関連（会話含む）
    if any(keyword in text for keyword in ["成績", "単位", "卒業", "自由", "外国語"]):
        message = answer_with_grades(user_id, text)

    # 楽単フォーム
    elif any(keyword in text for keyword in EASY_KEYWORDS):
        message = f"📋 楽単情報はこちらから回答してね！\n{EASY_COURSE_FORM_URL}"

    # 便覧検索
    elif any(keyword in text for keyword in CURRICULUM_KEYWORDS):
        response = supabase.table("curriculum").select("category, details").ilike("category", f"%{text}%").execute()
        if response.data:
            results = [f"📖 {r['category']}: {r['details']}" for r in response.data]
            message = "\n\n".join(results)
        else:
            message = "📌 便覧情報が見つかりませんでした。"

    # 雑談モード
    else:
        try:
            completion = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "あなたは明治大学の学生をサポートするアシスタントです。便覧や成績データをもとに学生の質問に答えます。"},
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

        for g in grades:
            supabase.table("grades").upsert(
                {
                    "user_id": user_id,
                    "category": g["category"],
                    "earned": g["earned"],
                    "required": g["required"],
                }
            ).execute()

        # format_grades は内部で内訳を除外して総計を計算するので安全
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
