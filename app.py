from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, FileMessage
import os
from supabase import create_client, Client
from pdf_reader import check_pdf  # PDF解析関数を利用

app = Flask(__name__)

# 環境変数
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ========== Webhook エンドポイント ==========
@app.route("/callback", methods=["POST"])
def callback():
    # LINE署名検証
    signature = request.headers["X-Line-Signature"]
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)

    return "OK"


# ========== メッセージ処理 ==========
@handler.add(MessageEvent, message=TextMessage)
def handle_text(event):
    user_id = event.source.user_id
    user_text = event.message.text.strip()

    # 不足単位に関する質問
    if "何単位" in user_text or "足りない" in user_text:
        # 最新の解析結果をDBから取得
        res = supabase.table("grades").select("*").eq("user_id", user_id).order("created_at", desc=True).limit(1).execute()
        if not res.data:
            reply_text = "まず成績PDFを送ってください。"
        else:
            result = res.data[0]["result"]

            # 不足科目を抽出
            lines = result.splitlines()
            missing = [l for l in lines if "❌ 不足" in l or "🔺" in l]

            # 合計不足と差分計算で自由履修を追加
            total_line = [l for l in lines if l.startswith("合計")]
            free_line = ""
            if total_line:
                try:
                    total_req = int(total_line[0].split("必要=")[1].split()[0])
                    total_got = int(total_line[0].split("取得=")[1].split()[0])
                    total_missing = total_req - total_got

                    # 他の不足合計
                    other_missing = 0
                    for m in missing:
                        if "不足" in m:
                            num = int(m.split("不足")[1].strip())
                            other_missing += num
                    free_missing = total_missing - other_missing
                    if free_missing > 0:
                        free_line = f"・自由履修科目: あと {free_missing} 単位"
                except Exception:
                    pass

            if missing:
                reply_text = "=== 不足している科目区分 ===\n" + "\n".join(missing)
                if free_line:
                    reply_text += "\n" + free_line
            else:
                reply_text = "🎉 すべての卒業要件を満たしています！"

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=reply_text)
        )
    else:
        # 普通の会話
        reply_text = "こんにちは！履修や単位に関するPDFを送ってくれれば解析できますよ。"
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=reply_text)
        )


# ========== ファイル(PDF)処理 ==========
@handler.add(MessageEvent, message=FileMessage)
def handle_file(event):
    user_id = event.source.user_id
    message_content = line_bot_api.get_message_content(event.message.id)

    # 一時保存
    pdf_path = f"/tmp/{user_id}.pdf"
    with open(pdf_path, "wb") as f:
        for chunk in message_content.iter_content():
            f.write(chunk)

    try:
        # PDF解析
        result_text = check_pdf(pdf_path, page_no=0)

        # Supabaseに保存
        supabase.table("grades").insert({"user_id": user_id, "result": result_text}).execute()

        reply_text = "📄 成績PDFを解析しました！\n\n" + result_text
    except Exception as e:
        reply_text = f"PDF解析エラー: {str(e)}"

    # 返信
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply_text[:4999])  # LINE制限
    )


# ========== 起動 ==========
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

