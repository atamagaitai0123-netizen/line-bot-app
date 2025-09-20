from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, FileMessage
from supabase import create_client, Client
import os
import tempfile
import pdf_reader  # 自作PDF解析モジュールを利用

# Flask アプリ
app = Flask(__name__)

# LINE設定（環境変数から取得）
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# Supabase設定
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# 卒業要件
GRAD_REQUIREMENTS = {
    "学部必修科目区分": 12,
    "教養科目区分": 24,
    "外国語科目区分": 16,
    "体育実技科目区分": 2,
    "経営学科基礎専門科目": 14,
    "経営学科専門科目": 32,
    "自由履修科目": 24,
    "合計": 124
}

# 備考の必修チェック対象
SUB_REQUIREMENTS = {
    "英語（初級）": 4,
    "初習外国語": 8,
    "外国語を用いた科目": 4
}


@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers["X-Line-Signature"]
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return "OK"


# PDF受信時
@handler.add(MessageEvent, message=FileMessage)
def handle_file(event):
    if not event.message.file_name.endswith(".pdf"):
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="PDFファイルを送ってください。")
        )
        return

    # 一時ファイルとして保存
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        file_path = tmp.name
        file_content = line_bot_api.get_message_content(event.message.id)
        for chunk in file_content.iter_content():
            tmp.write(chunk)

    # PDFを解析
    try:
        result_text, parsed_data = pdf_reader.check_pdf(file_path, page_no=0, return_dict=True)

        # Supabaseに保存
        user_id = event.source.user_id
        supabase.table("grades").insert({
            "user_id": user_id,
            "result": result_text
        }).execute()

        reply_text = "成績表を解析しました！\n\n" + result_text
    except Exception as e:
        reply_text = f"PDF解析エラー: {str(e)}"

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply_text)
    )


# テキスト受信時
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_text = event.message.text.strip()
    user_id = event.source.user_id

    # DBから直近の結果を取得
    res = supabase.table("grades").select("*").eq("user_id", user_id).order("id", desc=True).limit(1).execute()
    if res.data:
        latest = res.data[0]
        latest_result_text = latest["result"]
    else:
        latest_result_text = None

    reply_text = None

    if "何単位" in user_text or "足りない" in user_text or "不足" in user_text:
        if latest_result_text is None:
            reply_text = "まだ成績表が送信されていません。まずPDFを送ってください。"
        else:
            # 不足している科目を集計
            parsed_data = latest.get("parsed", {}) if "parsed" in latest else {}
            不足リスト = []

            合計不足_from_table = GRAD_REQUIREMENTS["合計"] - parsed_data.get("合計", 0)

            # メイン要件
            for key, req in GRAD_REQUIREMENTS.items():
                if key == "合計":
                    continue
                got = parsed_data.get(key, 0)
                if got < req:
                    不足リスト.append(f"・{key}: あと {req - got} 単位")

            # サブ要件
            既知不足合計 = 0
            for sub, req in SUB_REQUIREMENTS.items():
                got = parsed_data.get(sub, 0)
                if got < req:
                    不足リスト.append(f"・{sub}: あと {req - got} 単位")
                    既知不足合計 += (req - got)

            # 自由履修の不足分を計算
            既知不足合計 += sum(int(s.split("あと ")[1].split(" 単位")[0]) for s in 不足リスト)
            自由履修不足 = 合計不足_from_table - 既知不足合計
            if 自由履修不足 > 0:
                不足リスト.append(f"・自由履修科目: あと {自由履修不足} 単位")

            不足リスト.append(f"・合計: あと {合計不足_from_table} 単位")

            reply_text = "=== 不足している科目区分 ===\n" + "\n".join(不足リスト)

    if not reply_text:
        reply_text = f"受け取ったよ: {user_text}"

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply_text)
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
