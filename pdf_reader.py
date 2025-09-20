import re
from PyPDF2 import PdfReader

# ================================
# 必要単位数の定義
# ================================
REQUIREMENTS = {
    "学部必修科目区分": 12,
    "教養科目区分": 24,
    "外国語科目区分": 16,
    "体育実技科目区分": 2,
    "経営学科基礎専門科目": 14,
    "経営学科専門科目": 32,
    "自由履修科目": 24,
    "合計": 124,
}

REMARK_REQUIREMENTS = {
    "英語（初級）": 4,
    "初習外国語": 8,
    "外国語を用いた科目": 4,
}

# 自由履修に含める科目群
FREE_ELECTIVE_CATEGORIES = [
    "実習関連科目",
    "ICTリテラシー科目",
    "演習科目",
    "全学共通総合講座",
    "国際教育プログラム科目",
    "グローバル人材育成プログラム科目",
    "他学部科目",
]

# ================================
# PDF 解析
# ================================
def check_pdf(pdf_path, page_no=0, return_dict=False):
    reader = PdfReader(pdf_path)
    text = reader.pages[page_no].extract_text()

    results = {}
    obtained_total = 0

    # 各カテゴリの解析
    for category, required in REQUIREMENTS.items():
        obtained = 0
        if category in text:
            # 「必要=XX 取得=YY」の形式を探す
            m = re.search(rf"{category}.*?必要\s*=\s*(\d+).*?取得\s*=\s*(\d+)", text)
            if m:
                obtained = int(m.group(2))

        results[category] = {"required": required, "obtained": obtained}
        if category != "合計":  # 合計は後で別計算
            obtained_total += obtained

    # 自由履修の再計算（対象カテゴリを全部足す）
    free_elective = 0
    for cat in FREE_ELECTIVE_CATEGORIES:
        if cat in text:
            matches = re.findall(rf"{cat}.*?(\d+)", text)
            # 数字をすべて int に変換して合算
            credits = [int(x) for x in matches if x.isdigit()]
            free_elective += sum(credits)

    results["自由履修科目"]["obtained"] = free_elective
    obtained_total = sum([v["obtained"] for k, v in results.items() if k != "合計"])

    # 合計の更新
    results["合計"]["obtained"] = obtained_total

    # 備考欄の解析
    remarks = {}
    for subject, required in REMARK_REQUIREMENTS.items():
        obtained = 0
        if subject in text:
            # 「科目名 必要=XX 取得=YY」を厳密に探す
            m = re.search(rf"{subject}\s*必要\s*=\s*(\d+)\s*取得\s*=\s*(\d+)", text)
            if m:
                obtained = int(m.group(2))
        remarks[subject] = {"required": required, "obtained": obtained}

    if return_dict:
        return results, remarks

    # ================================
    # 出力の組み立て
    # ================================
    output = []
    output.append("=== 各カテゴリチェック ===")
    for category, info in results.items():
        required = info["required"]
        obtained = info["obtained"]
        status = "✅"
        if obtained < required:
            status = f"❌ 不足 {required - obtained}"
        elif obtained > required:
            status = "🔺 備考不足あり"
        output.append(f"・{category:<20} 必要={required:<3} 取得={obtained:<3} {status}")

    output.append("")
    output.append("=== 備考（必修科目） ===")
    for subject, info in remarks.items():
        required = info["required"]
        obtained = info["obtained"]
        status = "✅" if obtained >= required else f"❌ 不足 {required - obtained}"
        output.append(f"{subject:<20} 必要={required:<3} 取得={obtained:<3} {status}")

    # 不足分まとめ
    output.append("")
    output.append("=== 不足している科目区分 ===")
    shortages = []
    shortage_total = 0
    for category, info in results.items():
        if info["obtained"] < info["required"]:
            shortages.append(f"・{category}: あと {info['required'] - info['obtained']} 単位")
            shortage_total += info["required"] - info["obtained"]
    for subject, info in remarks.items():
        if info["obtained"] < info["required"]:
            shortages.append(f"・{subject}: あと {info['required'] - info['obtained']} 単位")
            shortage_total += info["required"] - info["obtained"]

    if shortages:
        output.extend(shortages)
        output.append(f"・合計: あと {shortage_total} 単位")
        output.append("\n❌ 卒業要件を満たしていません")
    else:
        output.append("\n🎉 卒業要件を満たしました！")

    return "\n".join(output)


# デバッグ用
if __name__ == "__main__":
    PDF_PATH = "成績.pdf"
    PAGE_NO = 0
    print(check_pdf(PDF_PATH, PAGE_NO))
