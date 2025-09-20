import os
from PyPDF2 import PdfReader

# 必要単位（大学規定）
REQUIRED = {
    "学部必修科目区分": 12,
    "教養科目区分": 24,
    "外国語科目区分": 16,
    "体育実技科目区分": 2,
    "経営学科基礎専門科目": 14,
    "経営学科専門科目": 32,
    "自由履修科目": 24,
}

# 自由履修にカウントする科目群
FREE_ELECTIVE_GROUPS = [
    "実習関連科目",
    "ICTリテラシー科目",
    "演習科目(演習I)",
    "演習科目(演習IIA~IIIB)",
    "全学共通総合講座",
    "国際教育プログラム科目",
    "グローバル人材育成プログラム科目",
    "他学部科目"
]

def parse_pdf(pdf_path, page_no=0):
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(pdf_path)

    reader = PdfReader(pdf_path)
    if page_no >= len(reader.pages):
        raise ValueError("page_no が範囲外です")

    text = reader.pages[page_no].extract_text()
    if not text:
        raise ValueError("PDFからテキストを抽出できませんでした")
    return text

def check_pdf(pdf_path, page_no=0):
    text = parse_pdf(pdf_path, page_no)

    obtained = {k: 0 for k in REQUIRED.keys()}
    remarks = {}
    free_elective_units = 0

    # 行ごとに解析
    for line in text.splitlines():
        parts = line.strip().split()
        if len(parts) < 2:
            continue

        name, unit_str = parts[0], parts[-1]

        try:
            unit_val = int(unit_str)
        except ValueError:
            continue

        # カテゴリに合致する場合
        for cat in REQUIRED.keys():
            if cat in name:
                obtained[cat] = unit_val

        # 自由履修カテゴリに含まれる場合
        for group in FREE_ELECTIVE_GROUPS:
            if group in name:
                free_elective_units += unit_val

        # 備考用（英語・外国語系）
        if "英語" in name or "外国語" in name:
            remarks[name] = unit_val

    # 自由履修の合計に反映
    obtained["自由履修科目"] = free_elective_units

    # 不足計算
    shortages = {}
    result_lines = ["成績表を解析しました！", "", "=== 各カテゴリチェック ==="]
    total_required = 0
    total_obtained = 0

    for cat, req in REQUIRED.items():
        need = req
        got = obtained.get(cat, 0)
        total_required += need
        total_obtained += got

        if got >= need:
            mark = "✅"
        else:
            mark = f"❌ 不足 {need - got}"
            shortages[cat] = need - got

        result_lines.append(f"・{cat:20} 必要={need}   取得={got}   {mark}")

    result_lines.append("")
    result_lines.append(f"合計                必要={total_required}  取得={total_obtained}   {'✅' if total_obtained>=total_required else '❌ 不足 ' + str(total_required-total_obtained)}")
    result_lines.append("")
    result_lines.append("=== 備考（必修科目） ===")
    for k, v in remarks.items():
        req = 4 if "英語（初級" in k else (8 if "初習外国語" in k else (4 if "外国語を用いた" in k else None))
        if req:
            mark = "✅" if v >= req else f"❌ 不足 {req - v}"
            shortages[k] = req - v if v < req else 0
            result_lines.append(f"{k:20} 必要={req}    取得={v}    {mark}")

    result_lines.append("")
    result_lines.append("=== 不足している科目区分 ===")
    for k, v in shortages.items():
        if v > 0:
            result_lines.append(f"・{k}: あと {v} 単位")
    total_short = sum([v for v in shortages.values() if v > 0])
    result_lines.append(f"・合計: あと {total_short} 単位")
    result_lines.append("")
    result_lines.append("✅ 卒業要件を満たしています" if total_short == 0 else "❌ 卒業要件を満たしていません")

    return "\n".join(result_lines), shortages
