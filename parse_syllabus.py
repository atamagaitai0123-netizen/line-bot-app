import pandas as pd
import re
import unicodedata

# 入力と出力ファイル
INPUT_CSV = "syllabus.csv"
OUTPUT_CSV = "syllabus_parsed.csv"

# 正規化関数
def normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", str(text)).strip()

# 教員らしい文字列かどうかを判定
def is_teacher_like(text: str) -> bool:
    if not text or text == "不明":
        return False
    if any(kw in text for kw in ["単位", "年次", "学期", "キャンパス", "科目"]):
        return False
    if re.search(r"\d", text):  # 数字が入っていたら除外
        return False
    if re.search(r"[一-龥ぁ-んァ-ヶ]", text):  # 漢字 or かなを含む
        return True
    if re.match(r"^[A-Za-z\s]+$", text) and len(text.split()) <= 4:  # 英語名 (短め)
        return True
    if "・" in text or "、" in text or "," in text:
        return True
    return False

def parse_row(info: str, grade: str):
    info = normalize(info)
    lines = [normalize(l) for l in info.splitlines() if normalize(l)]

    if not lines:
        return {
            "授業名・教員": "不明",
            "単位": "不明",
            "年次": "不明",
            "学期": "不明",
            "キャンパス": "不明",
            "成績評価": normalize(grade),
        }

    # 1行目は科目ナンバーなので無視
    main_text = lines[1] if len(lines) > 1 else ""
    rest_lines = lines[2:] if len(lines) > 2 else []

    subject = main_text
    teacher = "不明"

    # ---- 教員の抽出 ----
    if rest_lines and is_teacher_like(rest_lines[0]):
        teacher = rest_lines[0]
    else:
        tokens = re.split(r"[ 　]", main_text)
        for i in range(len(tokens) - 1, 0, -1):
            candidate = "".join(tokens[i:])
            if is_teacher_like(candidate):
                teacher = candidate
                subject = "".join(tokens[:i])
                break

    # ---- 単位・年次・学期・キャンパスを抽出 ----
    combined = " ".join(lines)

    m_units = re.search(r"(\d+)単位", combined)
    units = m_units.group(1) + "単位" if m_units else "不明"

    m_year = re.search(r"(\d+年次)", combined)
    grade_year = m_year.group(1) if m_year else "不明"

    m_semester = re.search(r"(春学期|秋学期|前期|後期|通年)", combined)
    semester = m_semester.group(1) if m_semester else "不明"

    m_campus = re.search(r"(和泉キャンパス|駿河台キャンパス|生田キャンパス|中野キャンパス|オンライン)", combined)
    campus = m_campus.group(1) if m_campus else "不明"

    return {
        "授業名・教員": f"{subject} {teacher}".strip(),
        "単位": units,
        "年次": grade_year,
        "学期": semester,
        "キャンパス": campus,
        "成績評価": normalize(grade),
    }

# --- ここから追加（既存コードは変更していません） ---
def get_category_by_page(page_num: int) -> str:
    """
    PDFページ番号から科目区分を返す（ユーザ指定のページ範囲に基づく）
    21〜26 学部必修, 27〜90 教養, 91〜178 外国語, 179〜182 体育実技, 183〜390 自由履修
    """
    try:
        p = int(page_num)
    except Exception:
        return "不明"

    if 21 <= p <= 26:
        return "学部必修科目"
    elif 27 <= p <= 90:
        return "教養科目"
    elif 91 <= p <= 178:
        return "外国語科目"
    elif 179 <= p <= 182:
        return "体育実技科目"
    elif 183 <= p <= 390:
        return "自由履修科目"
    else:
        return "不明"
# --- ここまで追加 ---


def main():
    df = pd.read_csv(INPUT_CSV)

    parsed_rows = []
    for _, row in df.iterrows():
        # 既存の parse 処理はそのまま
        parsed = parse_row(row.get("科目情報", ""), row.get("成績評価", ""))

        # --- ここから追加: page列を読み取って科目区分を付与 ---
        # CSVに存在する可能性のあるページ列名を順にチェック
        page_val = None
        for key in ("ページ", "page", "Page"):
            if key in row and pd.notna(row.get(key)):
                page_val = row.get(key)
                break
        # デフォルトは0（判定関数側で不明扱い）
        parsed["科目区分"] = get_category_by_page(page_val if page_val is not None else 0)
        # --- ここまで追加 ---

        parsed_rows.append(parsed)

    out_df = pd.DataFrame(parsed_rows, columns=["授業名・教員", "単位", "年次", "学期", "キャンパス", "成績評価", "科目区分"])
    out_df.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")

    print(f"✅ 解析済みCSVを出力しました: {OUTPUT_CSV}")

if __name__ == "__main__":
    main()
