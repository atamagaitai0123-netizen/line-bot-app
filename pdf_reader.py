import pdfplumber
import re
import unicodedata

# 単位区分の定義
UNIT_REQUIREMENTS = {
    "学部必修科目区分": 12,
    "教養科目区分": 24,
    "外国語科目区分": 16,
    "体育実技科目区分": 2,
    "経営学科基礎専門科目": 14,
    "経営学科専門科目": 32,
    "自由履修科目": 24,  # 表示用（実際は上限なし）
}

# 備考欄内の必修科目
FOREIGN_LANG_REQ = {
    "英語（初級）": 4,
    "初習外国語": 8,
    "その他外国語科目": 4,
}

# 自由履修に含まれる科目群
FREE_ELECTIVES = [
    "他学科専門科目",
    "経営学科教職専門科目", 
    "実習関連科目",
    "ＩＣＴリテラシー科目",
    "ICTリテラシー科目",  # 表記ゆれ対応
    "演習科目（演習Ⅰ）",
    "演習科目（演習ⅡA～ⅢB）",
    "全学共通総合講座",
    "国際教育プログラム科目",
    "グローバル人材育成プログラム科目",
    "他学部科目",
    "他学部履修科目",  # 表記ゆれ対応
]

def normalize_num_str(s):
    """数値文字列を正規化（全角→半角、非数字削除）"""
    if not s:
        return None
    s2 = unicodedata.normalize('NFKC', s)
    s2 = re.sub(r'[^\d]', '', s2)
    return int(s2) if s2.isdigit() else None

def extract_rows_from_page(page):
    """ページから行をグルーピングして抽出"""
    words = page.extract_words()
    if not words:
        return []
    
    words_sorted = sorted(words, key=lambda w: (round(w['top'], 1), w['x0']))
    rows = []
    current_row = []
    current_top = None
    
    for word in words_sorted:
        if current_top is None or abs(word['top'] - current_top) <= 3:
            current_row.append(word)
            current_top = word['top']
        else:
            if current_row:
                rows.append(current_row)
            current_row = [word]
            current_top = word['top']
    
    if current_row:
        rows.append(current_row)
    
    return rows

def find_status_table_bounds(rows):
    """単位取得状況表の開始・終了位置を特定"""
    start_y = None
    end_y = None
    
    for row in rows:
        row_text = " ".join([w['text'] for w in row])
        
        if ("単位修得状況" in row_text or "単位取得状況" in row_text) and start_y is None:
            start_y = row[0]['top']
            continue
        if re.search(r'25.*24.*23.*22', row_text) and start_y is None:
            start_y = row[0]['top']
            continue
        if "合 計" in row_text and re.search(r'124', row_text):
            end_y = row[0]['top']
            break
    
    return start_y, end_y

def extract_from_status_table(rows, debug_mode=False):
    """単位取得状況表から各区分の値を抽出"""
    results = {}
    start_y, end_y = find_status_table_bounds(rows)
    
    for row in rows:
        if not row:
            continue
        row_top = row[0]['top']
        row_text = " ".join([w['text'] for w in row])
        
        if start_y and end_y and (row_top < start_y or row_top > end_y):
            continue
        
        for category, required in UNIT_REQUIREMENTS.items():
            if category in row_text and category not in results:
                nums = re.findall(r'\d+', row_text)
                valid_nums = [int(n) for n in nums if not (2020 <= int(n) <= 2030)]
                
                if valid_nums:
                    obtained = valid_nums[-1]
                    results[category] = (obtained, required)
                break
    return results

def extract_free_electives(rows, debug_mode=False):
    """自由履修対象科目の単位数を抽出"""
    free_total = 0
    start_y, end_y = find_status_table_bounds(rows)
    
    for row in rows:
        if not row:
            continue
        row_top = row[0]['top']
        row_text = " ".join([w['text'] for w in row])
        
        if start_y and end_y and start_y <= row_top <= end_y:
            for free_cat in FREE_ELECTIVES:
                if free_cat in row_text:
                    nums_after = re.findall(r'\d+', row_text.split(free_cat)[-1])
                    if nums_after:
                        value = int(nums_after[0])
                        if 0 < value <= 20:
                            free_total += value
                            break
    return free_total

def extract_foreign_details(rows, debug_mode=False):
    """備考欄から外国語必修内訳を抽出"""
    foreign_detail = {}
    
    for row in rows:
        if not row:
            continue
        row_top = row[0]['top']
        row_text = " ".join([w['text'] for w in row])
        
        if row_top > 400:
            for detail_cat, req in FOREIGN_LANG_REQ.items():
                if detail_cat in row_text and detail_cat not in foreign_detail:
                    nums = re.findall(r'\d+', row_text)
                    valid_nums = [int(n) for n in nums if not (2020 <= int(n) <= 2030)]
                    
                    if len(valid_nums) >= 2:
                        obtained = valid_nums[-1]
                        foreign_detail[detail_cat] = (obtained, req)
    return foreign_detail

def find_total_from_summary_row(rows, debug_mode=False):
    """合計行から総取得単位数を直接抽出"""
    for row in rows:
        if not row:
            continue
        row_text = " ".join([w['text'] for w in row])
        
        if "合 計" in row_text and "124" in row_text:
            nums = re.findall(r'\d+', row_text)
            valid_nums = [int(n) for n in nums if not (2020 <= int(n) <= 2030)]
            
            if len(valid_nums) >= 5:
                total_obtained = valid_nums[-1]
                return total_obtained
    return None

def parse_units_advanced(pdf_path):
    """改良版のPDF解析"""
    with pdfplumber.open(pdf_path) as pdf:
        all_rows = []
        for page in pdf.pages:
            rows = extract_rows_from_page(page)
            all_rows.extend(rows)
        
        total_from_summary = find_total_from_summary_row(all_rows)
        results = extract_from_status_table(all_rows)
        free_elective_total = extract_free_electives(all_rows)
        foreign_detail = extract_foreign_details(all_rows)
        
        surplus_total = 0
        for category, (obtained, required) in results.items():
            if category not in ["自由履修科目", "外国語科目区分"]:
                surplus_total += max(0, obtained - required)
        
        total_free = free_elective_total + surplus_total
        results["自由履修科目"] = (total_free, 24)
        
        return results, foreign_detail, total_from_summary

def analyze_results(results, foreign_detail, total_from_summary=None):
    """結果の分析と出力生成"""
    output = []
    total_required = 124

    output.append("📊 === 単位取得状況分析結果 ===")
    for cat, (obtained, required) in results.items():
        status = "✅ 完了" if obtained >= required else f"❌ あと{required - obtained}単位"
        output.append(f"{cat} {obtained}/{required} {status}")

    if foreign_detail:
        output.append("\n📋 === 備考欄（必修内訳）===")
        for cat, (obtained, required) in foreign_detail.items():
            status = "✅ 完了" if obtained >= required else f"❌ あと{required - obtained}単位"
            output.append(f"  {cat} {obtained}/{required} {status}")

    if total_from_summary is not None:
        total_obtained = total_from_summary
    else:
        total_obtained = sum(min(o, r) for o, r in results.values())
    
    output.append("\n========================================")
    output.append(f"🎓 卒業必要単位数: {total_required}")
    output.append(f"✅ 取得済み単位数: {total_obtained}")

    if total_obtained >= total_required:
        output.append("🎉 おめでとうございます！卒業要件を満たしています")
    else:
        shortage = total_required - total_obtained
        output.append(f"📝 卒業まであと: {shortage}単位")

    return "\n".join(output)

def check_pdf(pdf_path, page_no=0, return_dict=False):
    """メイン関数（app.pyから呼び出し用）"""
    try:
        results, foreign_detail, total_from_summary = parse_units_advanced(pdf_path)
        report = analyze_results(results, foreign_detail, total_from_summary)
        
        if return_dict:
            return {
                "results": results,
                "foreign_detail": foreign_detail,
                "total_obtained": total_from_summary,
                "report": report
            }
        else:
            return report
    except Exception as e:
        error_msg = f"PDF解析エラー: {str(e)}"
        print(error_msg)
        return error_msg if not return_dict else {"error": error_msg}

# --- 追加: app.py互換用ラッパ ---
def parse_grades_from_pdf(pdf_path):
    """
    app.py から呼び出すためのラッパ関数。
    check_pdf(return_dict=True) を利用する。
    """
    return check_pdf(pdf_path, return_dict=True)

if __name__ == "__main__":
    import sys
    pdf_path = sys.argv[1] if len(sys.argv) > 1 else "成績.pdf"
    print(f"PDFファイルを解析中: {pdf_path}")
    result = check_pdf(pdf_path)
    print(result)
