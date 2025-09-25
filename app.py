# app.py — 修正版（検証済み）
import os
import sys
import json
import tempfile
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError, LineBotApiError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, FileMessage
from linebot.models import FollowEvent
from supabase import create_client, Client
from openai import OpenAI
import pdf_reader  # あなたが提供している pdf_reader.py を使う想定
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
NOTIFY_SECRET = os.getenv("NOTIFY_SECRET", None)


# ---- 初期化 ----
app = Flask(__name__)

LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not all([LINE_CHANNEL_SECRET, LINE_CHANNEL_ACCESS_TOKEN, SUPABASE_URL, SUPABASE_KEY, OPENAI_API_KEY]):
    raise ValueError("環境変数が不足しています。LINE_CHANNEL_SECRET, LINE_CHANNEL_ACCESS_TOKEN, SUPABASE_URL, SUPABASE_KEY, OPENAI_API_KEY を確認してください")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
client = OpenAI(api_key=OPENAI_API_KEY)


# ---- ヘルパー関数 ----
def debug_log(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


def safe_reply(reply_token, text):
    try:
        line_bot_api.reply_message(reply_token, TextSendMessage(text=text))
    except LineBotApiError as e:
        debug_log("LineBotApiError replying:", e)
    except Exception as e:
        debug_log("Unexpected error replying:", e)


def call_openai_chat(messages, model="gpt-4o-mini"):
    """
    OpenAI 呼び出し。戻り値の構造差に頑強に対応して文字列を返す（失敗時 None）。
    """
    try:
        resp = client.chat.completions.create(model=model, messages=messages)
        # 互換的取り出し
        try:
            choice = resp.choices[0]
            msg = getattr(choice, "message", None)
            if msg is None and isinstance(choice, dict):
                msg = choice.get("message")
            if isinstance(msg, dict):
                content = msg.get("content") or msg.get("text")
            else:
                content = getattr(msg, "content", None) or getattr(choice, "text", None)
        except Exception:
            # dict-style fallback
            try:
                choice = resp["choices"][0]
                msg = choice.get("message") if isinstance(choice, dict) else None
                if isinstance(msg, dict):
                    content = msg.get("content") or msg.get("text")
                else:
                    content = choice.get("text") or str(resp)
            except Exception:
                content = str(resp)
        if content is None:
            content = str(resp)
        return content
    except Exception as e:
        debug_log("OpenAI call error:", e)
        return None


def fetch_saved_grades(user_id):
    """
    Supabase から最新の成績レコードを取得して (content, raw_data) を返す。
    見つからなければ (None, None)
    """
    try:
        res = supabase.table("grades_text").select("*").eq("user_id", user_id).order("created_at", desc=True).limit(1).execute()
        if res and getattr(res, "data", None):
            row = res.data[0]
            content = row.get("content")
            raw = row.get("raw_data")
            # content が JSON 文字列になっている古いケースに対応
            if isinstance(content, str):
                s = content.strip()
                if (s.startswith("[") and s.endswith("]")) or (s.startswith("{") and s.endswith("}")):
                    try:
                        parsed = json.loads(content)
                        if raw is None:
                            raw = parsed
                    except Exception:
                        pass
            return content, raw
    except Exception as e:
        debug_log("Supabase fetch error:", e)
    return None, None

# === シラバス検索機能 ===
def search_syllabus_by_name(keyword: str):
    """
    Supabase の syllabus テーブルから授業名で検索する。
    完全一致が優先。なければ部分一致。
    """
    try:
        # 完全一致
        res = supabase.table("syllabus").select("*").eq("subject_name", keyword).execute()
        if res and res.data:
            return res.data

        # 部分一致（最大5件）
        res = supabase.table("syllabus").select("*").ilike("subject_name", f"%{keyword}%").limit(5).execute()
        return res.data if res and res.data else []
    except Exception as e:
        debug_log("search_syllabus_by_name error:", e)
        return []


def format_syllabus_result(rows):
    if not rows:
        return "❌ 該当する授業が見つかりませんでした。"

    lines = []
    for r in rows:
        subject = r.get("subject_name") or ""
        teacher = r.get("teacher") or ""
        units = r.get("units") or ""
        year = r.get("grade_year") or ""
        term = r.get("term") or ""
        campus = r.get("campus") or ""
        evaluation = r.get("evaluation") or ""

        lines.append(
            f"📖 {subject}\n👤 {teacher}\n単位: {units} | 年次: {year} | 学期: {term} | キャンパス: {campus}\n📝 {evaluation}"
        )

    return "\n\n".join(lines)



def json_to_human(parsed):
    """raw_data(list) を簡易的に人向けのテキストに変換"""
    try:
        if not parsed:
            return ""
        lines = ["📊 === 単位取得状況分析結果 ==="]
        total_obtained = 0
        total_required = 0
        for item in parsed:
            cat = item.get("category") or item.get("name") or "項目"
            earned = item.get("earned", "?")
            required = item.get("required", "?")
            ok = ""
            if isinstance(earned, (int, float)) and isinstance(required, (int, float)):
                ok = " ✅ 完了" if earned >= required else ""
                total_obtained += earned
                total_required += required
            lines.append(f"{cat} {earned}/{required}{ok}")
        lines.append("")
        lines.append(f"卒業必要単位数(参考): {total_required}")
        lines.append(f"取得済み合計(参考): {total_obtained}")
        return "\n".join(lines)
    except Exception as e:
        debug_log("json_to_human error:", e)
        return str(parsed)


def normalize_text(s: str) -> str:
    if not isinstance(s, str):
        return ""
    return s.strip().lower()

def upsert_subscriber(user_id, opt_in=False):
    payload = {"user_id": user_id, "opt_in": opt_in, "last_seen": datetime.now(tz=JST).isoformat()}
    supabase.table("subscribers").upsert(payload).execute()

def set_subscription(user_id, opt_in: bool):
    supabase.table("subscribers").upsert({"user_id": user_id, "opt_in": opt_in, "last_seen": datetime.now(tz=JST).isoformat()}).execute()
    return True

def get_subscribed_user_ids():
    res = supabase.table("subscribers").select("user_id").eq("opt_in", True).execute()
    return [r['user_id'] for r in res.data] if getattr(res, "data", None) else []

def fetch_events_between(start_date: date, end_date: date):
    res = supabase.table("academic_calendar").select("*") \
        .gte("date", start_date.isoformat()) \
        .lte("date", end_date.isoformat()) \
        .order("date", desc=False).execute()
    return res.data if getattr(res, "data", None) else []

def format_events_human(events):
    if not events:
        return "該当する予定はありません。"

    # F1/F2/S1/S2 の補足マップ
    notes_map = {
        "S1": "春学期前半（S1）: 4/10〜6/3",
        "S2": "春学期後半（S2）: 6/4〜7/22",
        "F1": "秋学期前半（F1）: 9/20〜11/14",
        "F2": "秋学期後半（F2）: 11/15〜1/23",
    }

    lines = []
    for e in events[:20]:
        d = e.get("date")
        t = e.get("time") or ""
        title = e.get("title") or ""
        cat = e.get("category") or ""
        note = e.get("note") or ""

        # 補足に変換（元の略称は削除）
        replaced = []
        for key, extra in notes_map.items():
            if key in note or key in title:
                replaced.append(extra)

        # 補足が見つかったら note を置き換える
        if replaced:
            note = " / ".join(replaced)

        lines.append(f"- {d} {t} {title} [{cat}]\n  {note}")
    return "\n".join(lines)

def fetch_curriculum_docs(faculty: str, department: str):
    try:
        res = supabase.table("curriculum_docs") \
            .select("category_group, category, required_units") \
            .eq("faculty", faculty) \
            .eq("department", department) \
            .order("category_group", desc=False) \
            .execute()
        return res.data if getattr(res, "data", None) else []
    except Exception as e:
        debug_log("fetch_curriculum_docs error:", e)
        return []


def format_curriculum_docs(faculty, department, rows):
    if not rows:
        return f"{faculty} {department} の履修条件が見つかりませんでした。"

    lines = [f"📖 {faculty} {department} 卒業要件", ""]
    current_group = None

    for r in rows:
        group = r.get("category_group") or ""
        cat = r.get("category") or ""
        units = r.get("required_units") or 0

        # グループが変わったら見出しを追加
        if group != current_group:
            lines.append(f"🟦 {group}")
            current_group = group

        lines.append(f"- {cat}: {units}単位")

    return "\n".join(lines)


# ---- ルート ----
@app.route("/")
def index():
    return "LINE Bot is running!"


# ---- Webhook ----
@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)
    debug_log("Webhook received (truncated):", body[:1000])
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        debug_log("Invalid signature for webhook")
        abort(400)
    except Exception as e:
        debug_log("handler.handle threw:", e)
        abort(500)
    return "OK"


# ---- テキストメッセージ ----
@handler.add(MessageEvent, message=TextMessage)
def handle_text_message(event):
    try:
        user_id = event.source.user_id
        text_raw = event.message.text or ""
        debug_log(f"TextMessage from {user_id}: {text_raw}")
        text = normalize_text(text_raw)

        wants_advice = any(k in text for k in ["アドバイス".lower(), "助言".lower(), "advice"])
        wants_grades_check = any(k in text for k in ["成績", "単位", "成績確認"])
        asks_office = any(k in text for k in ["事務室", "連絡先", "電話番号", "電話"])
        wants_easy_class = any(k in text for k in ["楽単", "ラク単", "らくたん", "easy class"])
        wants_curriculum = any(k in text for k in ["履修条件", "卒業要件", "必要単位", "カリキュラム"])

        # 学部判定（簡易）
        dept_keywords = {
            "経営": ["経営", "経営学部"],
            "商学": ["商学", "商学部"],
            "法学": ["法学", "法学部"],
        }
        matched_dept = None
        for key, variants in dept_keywords.items():
            for v in variants:
                if v.lower() in text:
                    matched_dept = key
                    break
            if matched_dept:
                break
        # 0) 楽単フォーム
        if wants_easy_class:
            debug_log("handling: easy class form")
            form_url = "https://docs.google.com/forms/d/e/1FAIpQLSfw654DpwVoSexb3lI8WLqsR6ex1lRYEX_6Yg1g-S57tw2JBQ/viewform?usp=header"
            safe_reply(event.reply_token, f"📝 楽単情報の投稿はこちらから！\n{form_url}")
            return
        # --- 予定／カレンダー機能 ---
        wants_calendar = any(k in text for k in ["予定", "スケジュール", "今日の予定", "明日の予定", "今月の予定", "calendar", "予定表"])
        wants_subscribe = any(k in text for k in ["通知登録", "配信登録", "通知を受け取る", "subscribe", "登録する"])
        wants_unsubscribe = any(k in text for k in ["通知停止", "配信停止", "unsubscribe", "停止する"])

        # ユーザーを一旦DBに登録（初アクセス時）
        upsert_subscriber(user_id, opt_in=False)

        if wants_subscribe:
            set_subscription(user_id, True)
            safe_reply(event.reply_token, "✅ 通知登録しました！毎朝の予定をお送りします。停止は「通知停止」と送ってください。")
            return

        if wants_unsubscribe:
            set_subscription(user_id, False)
            safe_reply(event.reply_token, "✅ 通知を停止しました。")
            return

        if wants_calendar:
            if "今日" in text:
                today = datetime.now(tz=JST).date()
                events = fetch_events_between(today, today)
                safe_reply(event.reply_token, "📅 今日の予定:\n\n" + format_events_human(events))
                return
            if "明日" in text:
                tomorrow = datetime.now(tz=JST).date() + timedelta(days=1)
                events = fetch_events_between(tomorrow, tomorrow)
                safe_reply(event.reply_token, "📅 明日の予定:\n\n" + format_events_human(events))
                return
            if "今月" in text:
                now = datetime.now(tz=JST)
                start = date(now.year, now.month, 1)
                end = (date(now.year, now.month + 1, 1) - timedelta(days=1)) if now.month < 12 else date(now.year, 12, 31)
                events = fetch_events_between(start, end)
                safe_reply(event.reply_token, f"📅 {now.year}年{now.month}月の予定:\n\n" + format_events_human(events))
                return

            import re
            m = re.search(r"(\d{4})[-/年](\d{1,2})", text)
            if m:
                y, mth = int(m.group(1)), int(m.group(2))
                start = date(y, mth, 1)
                end = (date(y, mth + 1, 1) - timedelta(days=1)) if mth < 12 else date(y, 12, 31)
                events = fetch_events_between(start, end)
                safe_reply(event.reply_token, f"📅 {y}年{mth}月の予定:\n\n" + format_events_human(events))
                return

            start = datetime.now(tz=JST).date()
            end = start + timedelta(days=7)
            events = fetch_events_between(start, end)
            safe_reply(event.reply_token, "📅 直近7日間の予定:\n\n" + format_events_human(events))
            return


        if wants_curriculum:
              faculty, department = "経営学部", "経営学科"  # 今は固定
              rows = fetch_curriculum_docs(faculty, department)
              safe_reply(event.reply_token, format_curriculum_docs(faculty, department, rows))
              return


        
        # 1) アドバイス要求
        if wants_advice:
            debug_log("handling: advice")
            grades_text, grades_list = fetch_saved_grades(user_id)
            if not grades_text and not grades_list:
                safe_reply(event.reply_token, "❌ 成績データが見つかりません。まずはPDFを送ってください。")
                return
            prompt_system = (
                "あなたは明治大学の学生をサポートするアシスタントです。"
                "以下に与える成績状況（文章と構造化データ）を元に、卒業要件の達成状況、"
                "不足単位がある場合の優先度の高い履修提案、履修順序や注意点を具体的に助言してください。"
                "数字は正確に扱ってください。"
                "アドバイスは、要点を得ていて長文にならないようにしてください。"
            )
            user_content = f"成績レポート:\n{grades_text}\n\n構造化データ:\n{json.dumps(grades_list, ensure_ascii=False)}"
            messages = [
                {"role": "system", "content": prompt_system},
                {"role": "user", "content": user_content}
            ]
            ai_text = call_openai_chat(messages)
            if ai_text is None:
                safe_reply(event.reply_token, "💡 アドバイス生成に失敗しました。時間をおいてもう一度試してください。")
            else:
                safe_reply(event.reply_token, ai_text)
            return

        # 2) 成績表示
        if wants_grades_check:
            debug_log("handling: grades check")
            grades_text, grades_list = fetch_saved_grades(user_id)
            if grades_text:
                safe_reply(event.reply_token, grades_text)
            else:
                safe_reply(event.reply_token, "❌ 成績データが見つかりません。PDFを送ってください。")
            return

        # 3) 事務室問い合わせ
        if asks_office:
            debug_log("handling: inquiry contacts")
            try:
                if matched_dept:
                    pattern = f"%{matched_dept}%"
                    res = supabase.table("inquiry_contacts").select("*").ilike("department", pattern).execute()
                else:
                    res = supabase.table("inquiry_contacts").select("*").limit(50).execute()
                if res and getattr(res, "data", None):
                    rows = res.data
                    if matched_dept and len(rows) >= 1:
                        r = rows[0]
                        out = f"📞 {r.get('department')}:\n{r.get('phone')}\n{r.get('page_url') or ''}"
                        safe_reply(event.reply_token, out)
                        return
                    else:
                        lines = []
                        for r in rows[:10]:
                            lines.append(f"{r.get('department')} ({r.get('target')}): {r.get('phone')}\n{r.get('page_url') or ''}")
                        safe_reply(event.reply_token, "📞 明治大学 各学部事務室の連絡先:\n\n" + "\n\n".join(lines))
                        return
                else:
                    safe_reply(event.reply_token, "該当する事務室の連絡先が見つかりませんでした。学部名を教えてください（例: 経営学部）。")
                    return
            except Exception as e:
                debug_log("Supabase inquiry_contacts error:", e)
                safe_reply(event.reply_token, "事務室情報の取得中にエラーが発生しました。後でもう一度お試しください。")
                return

        # 4) シラバス検索（授業名で検索）
        syllabus_results = search_syllabus_by_name(text_raw.strip())
        if syllabus_results:
            safe_reply(event.reply_token, format_syllabus_result(syllabus_results))
            return


        # 5) Fallback chat（雑談）
        debug_log("handling: fallback chat")
        messages = [
            {"role": "system", "content": "あなたは明治大学の学生をサポートするアシスタントです。"},
            {"role": "user", "content": text_raw}
        ]
        ai_text = call_openai_chat(messages)
        if ai_text is None:
            safe_reply(event.reply_token, "💡 応答の生成に失敗しました。後ほど試してください。")
        else:
            safe_reply(event.reply_token, ai_text)

    except Exception as e:
        debug_log("handle_text_message unexpected error:", e)
        safe_reply(event.reply_token, "予期せぬエラーが発生しました。管理者に問い合わせてください。")


# ---- ファイル（PDFなど）ハンドラ ----
@handler.add(MessageEvent, message=FileMessage)
def handle_file_message(event):
    try:
        user_id = event.source.user_id
        msg = event.message
        file_name = getattr(msg, "file_name", None)
        debug_log(f"FileMessage from {user_id} filename={file_name} id={msg.id}")

        # 一時ファイルに保存（拡張子があるならそれを使う）
        suffix = os.path.splitext(file_name)[1] if file_name and os.path.splitext(file_name)[1] else ".pdf"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            temp_path = tmp.name
            content = line_bot_api.get_message_content(msg.id)
            for chunk in content.iter_content():
                tmp.write(chunk)

        # PDF解析（pdf_reader が (text, list) を返す想定）
        try:
            parsed = pdf_reader.parse_grades_from_pdf(temp_path)
            if isinstance(parsed, (list, tuple)):
                grades_text = parsed[0] if len(parsed) > 0 else ""
                grades_list = parsed[1] if len(parsed) > 1 else []
            else:
                grades_text = str(parsed)
                grades_list = []
        except Exception as e:
            debug_log("pdf_reader error:", e)
            grades_text = f"❌ PDFの解析に失敗しました: {e}"
            grades_list = []

        # Supabase 保存
        try:
            payload = {"user_id": user_id, "content": grades_text, "raw_data": grades_list}
            supabase.table("grades_text").upsert(payload).execute()
        except Exception as e:
            debug_log("Supabase upsert error:", e)
            safe_reply(event.reply_token, "解析はできましたがデータの保存に失敗しました。管理者に連絡してください。")

        # LINE に解析結果を返信
        reply_text = "✅ 成績データを保存しました！\n\n" + (grades_text or "（解析結果が空です）")
        safe_reply(event.reply_token, reply_text)

        # 一時ファイル削除
        try:
            os.remove(temp_path)
        except Exception:
            pass

    except Exception as e:
        debug_log("handle_file_message unexpected error:", e)
        safe_reply(event.reply_token, "ファイルの処理中にエラーが発生しました。もう一度送ってください。")


@handler.add(FollowEvent)
def handle_follow(event):
    user_id = event.source.user_id
    upsert_subscriber(user_id, opt_in=False)

    intro_text = """🎓 ようこそ！
Campus Navigator @明治大学経営学部 へ 👋

このBotでは、学生生活に役立つ機能をまとめて使えます👇

📅 学事カレンダー
・「今日の予定」/「明日の予定」/「今月の予定」
・「予定 2025-09」 → 指定月の予定検索
・「通知登録」 → 毎朝その日の予定を自動通知
・「通知停止」 → 通知をオフ

📊 成績・単位管理
・成績表PDFを送ると、自動で解析して保存
・「成績」 → 自分の成績一覧を表示
・「単位確認」 → 卒業に必要な単位の状況をチェック
・「アドバイス」 → AIが履修の相談に回答

📝 学生生活サポート
・「楽単フォーム」 → 授業情報の共有フォーム
・「事務室」 → 学部事務室の連絡先を表示
・その他の質問や雑談にも対応（AIに相談できます）

ℹ️ 注意事項
- サーバーの状況によって、まれに応答が遅れることがあります
- 学事予定は大学公式カレンダーを基にしています

まずは「今日の予定」と送って試してみてください ✅
"""

    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=intro_text))

@app.route("/notify", methods=["POST", "GET"])
def notify_endpoint():
    token = request.args.get("token") or request.headers.get("X-Notify-Token")
    if NOTIFY_SECRET and token != NOTIFY_SECRET:
        return ("Unauthorized", 401)

    qdate = request.args.get("date")
    try:
        target_date = datetime.fromisoformat(qdate).date() if qdate else datetime.now(tz=JST).date()
    except Exception:
        return ("Bad date format", 400)

    events = fetch_events_between(target_date, target_date)
    if not events:
        return ("no events", 200)

    message_body = f"📅 {target_date.isoformat()} の予定:\n\n" + format_events_human(events)
    user_ids = get_subscribed_user_ids()

    successes, failures = 0, 0
    for uid in user_ids:
        try:
            line_bot_api.push_message(uid, TextSendMessage(text=message_body))
            successes += 1
            supabase.table("notification_logs").insert({"user_id": uid, "event_id": None, "status": "sent"}).execute()
        except Exception as e:
            failures += 1
            supabase.table("notification_logs").insert({"user_id": uid, "event_id": None, "status": "error", "error": str(e)}).execute()

    return (f"sent:{successes}, failed:{failures}", 200)



# ---- 起動 ----
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    debug_log("Starting app on port", port)
    app.run(host="0.0.0.0", port=port)
