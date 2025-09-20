import pdfplumber
import re

# 必要単位の定義
requirements = {
    "学部必修科目区分": 12,
    "教養科目区分": 24,
    "外国語科目区分": 16,
    "体育実技科目区分": 2,
    "経営学科基礎専門科目": 14,
    "経営学科専門科目": 32,
    "自由履修科目": 24,
    "合計": 124,
}

# 備考の必修科目要件
notes_requirements = {
    "英語（初級）": 4,
    "初習外国語": 8,
    "外国語を用いた科目": 4,
}


def check_pdf(pdf_path):
    text = ""
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text += page.extract_text() + "\n"

    results = {}
    notes_results = {}

    # 各カテゴリの単位を抽出
    for category, required in requirements.items():
        if category == "合計":
            continue
        match = re.search(rf"{category}.*?(\d+)\s*単位", text)
        if match:
            earned = int(match.group(1))
        else:
            earned = 0
        results[category] = (required, earned)

    # 備考欄の必修科目
    for note, required in notes_requirements.items():
        match = re.search(rf"{note}.*?(\d+)\s*単位", text)
        if match:
            earned = int(match.group(1))
        else:
            earned = 0
        notes_results[note] = (required, earned)

    # === 自由履修科目の再計算 ===
    free_elective_sources = [
        "実習関連科目",
        "ICTリテラシー科目",
        "演習科目(演習I)",
        "演習科目(演習IIA~IIIB)",
        "全学共通総合講座",
        "国際教育プログラム科目",
        "グローバル人材育成プログラム科目",
        "他学部科目",
    ]

    free_elective_earned = 0
    for src in free_elective_sources:
        match = re.search(rf"{src}.*?(\d+)\s*単位", text)
        if match:
            free_elective_earned += int(match.group(1))

    results["自由履修科目"] = (requirements["自由履修科目"], free_elective_earned)

    # 合計単位
    total_earned = sum(earned for _, earned in results.values())
    results["合計"] = (requirements["合計"], total_earned)

    return results, notes_results


def format_results(results, notes_results):
    output = []
    output.append("成績表を解析しました！\n")

    # 各カテゴリチェック
    output.append("=== 各カテゴリチェック ===")
    for category, (required, earned) in results.items():
        if category == "合計":
            continue
        if earned >= required:
            status = "✅"
        else:
            status = f"❌ 不足 {required - earned}"
        output.append(f"・{category:<24} 必要={required}   取得={earned}   {status}")

    # 合計
    required, earned = results["合計"]
    if earned >= required:
        status = "✅"
    else:
        status = f"❌ 不足 {required - earned}"
    output.append(f"\n合計                必要={required}  取得={earned}   {status}\n")

    # 備考（必修科目）
    output.append("=== 備考（必修科目） ===")
    for note, (required, earned) in notes_results.items():
        if earned >= required:
            status = "✅"
        else:
            status = f"❌ 不足 {required - earned}"
        output.append(f"{note:<20} 必要={required}    取得={earned}    {status}")

    # 不足科目一覧
    output.append("\n=== 不足している科目区分 ===")
    total_shortage = 0
    for category, (required, earned) in results.items():
        if earned < required:
            shortage = required - earned
            total_shortage += shortage
            output.append(f"・{category}: あと {shortage} 単位")

    for note, (required, earned) in notes_results.items():
        if earned < required:
            shortage = required - earned
            total_shortage += shortage
            output.append(f"・{note}: あと {shortage} 単位")

    if total_shortage > 0:
        output.append(f"・合計: あと {total_shortage} 単位")
        output.append("\n❌ 卒業要件を満たしていません")
    else:
        output.append("\n✅ 卒業要件を満たしています")

    return "\n".join(output)









   
