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
    
    # top座標で行をグルーピング（±3の範囲で同じ行とみなす）
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
        
        # 表の開始を検出
        if ("単位修得状況" in row_text or "単位取得状況" in row_text) and start_y is None:
            start_y = row[0]['top']
            continue
        
        # ヘッダー行を検出（25 24 23 22が含まれる行）
        if re.search(r'25.*24.*23.*22', row_text) and start_y is None:
            start_y = row[0]['top']
            continue
        
        # 合計行を検出（表の終了）
        if "合 計" in row_text and re.search(r'124', row_text):
            end_y = row[0]['top']
            break
    
    return start_y, end_y

def extract_from_status_table(rows, debug_mode=False):
    """単位取得状況表から各区分の値を抽出"""
    results = {}
    start_y, end_y = find_status_table_bounds(rows)
    
    if debug_mode:
        print(f"ステータス表範囲: y={start_y} から y={end_y}")
    
    for row in rows:
        if not row:
            continue
        
        row_top = row[0]['top']
        row_text = " ".join([w['text'] for w in row])
        
        # ステータス表の範囲内かチェック
        if start_y and end_y and (row_top < start_y or row_top > end_y):
            continue
        
        # 各区分名を含む行を検索
        for category, required in UNIT_REQUIREMENTS.items():
            if category in row_text and category not in results:
                # 行から数値を抽出
                nums = re.findall(r'\d+', row_text)
                valid_nums = [int(n) for n in nums if not (2020 <= int(n) <= 2030)]
                
                if valid_nums:
                    # 最後の数値を取得単位とする（合計列）
                    obtained = valid_nums[-1]
                    results[category] = (obtained, required)
                    
                    if debug_mode:
                        print(f"区分発見: {category} = {obtained}/{required} (y={row_top:.1f})")
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
        
        # ステータス表の範囲内で自由履修科目を探す
        if start_y and end_y and start_y <= row_top <= end_y:
            for free_cat in FREE_ELECTIVES:
                if free_cat in row_text:
                    # 行テキストから数値を抽出
                    nums = re.findall(r'\d+', row_text)
                    valid_nums = [int(n) for n in nums if not (2020 <= int(n) <= 2030)]
                    
                    # 自由履修科目名の後に続く数値のみを取得
                    cat_index = row_text.find(free_cat)
                    if cat_index != -1:
                        # 科目名以降のテキストから数値を抽出
                        text_after_cat = row_text[cat_index + len(free_cat):]
                        nums_after = re.findall(r'\d+', text_after_cat)
                        
                        if nums_after:
                            value = int(nums_after[0])  # 科目名直後の最初の数値
                            if 0 < value <= 20:  # 常識的な単位数の範囲
                                free_total += value
                                if debug_mode:
                                    print(f"自由履修科目発見: {free_cat} = {value} (y={row_top:.1f})")
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
        
        # 備考欄の範囲（y=400以降）
        if row_top > 400:
            for detail_cat, req in FOREIGN_LANG_REQ.items():
                if detail_cat in row_text and detail_cat not in foreign_detail:
                    nums = re.findall(r'\d+', row_text)
                    valid_nums = [int(n) for n in nums if not (2020 <= int(n) <= 2030)]
                    
                    if len(valid_nums) >= 2:
                        obtained = valid_nums[-1]  # 最後の数値を取得単位とする
                        foreign_detail[detail_cat] = (obtained, req)
                        
                        if debug_mode:
                            print(f"必修内訳発見: {detail_cat} = {obtained}/{req} (y={row_top:.1f})")
    
    return foreign_detail

def find_total_from_summary_row(rows, debug_mode=False):
    """合計行から総取得単位数を直接抽出"""
    for row in rows:
        if not row:
            continue
        
        row_text = " ".join([w['text'] for w in row])
        
        # 合計行を検出（「合 計 124」を含む行）
        if "合 計" in row_text and "124" in row_text:
            nums = re.findall(r'\d+', row_text)
            valid_nums = [int(n) for n in nums if not (2020 <= int(n) <= 2030)]
            
            if len(valid_nums) >= 5:  # 124, 年度別数値..., 総合計
                total_obtained = valid_nums[-1]  # 最後の数値が総取得単位
                if debug_mode:
                    print(f"合計行から総取得単位を検出: {total_obtained}")
                return total_obtained
    
    return None

def parse_units_advanced(pdf_path):
    """改良版のPDF解析"""
    debug_mode = False  # 本番環境用：デバッグ出力OFF
    
    with pdfplumber.open(pdf_path) as pdf:
        all_rows = []
        
        # 全ページの行を収集
        for page_num, page in enumerate(pdf.pages):
            if debug_mode:
                print(f"\n=== ページ {page_num + 1} 解析開始 ===")
            
            rows = extract_rows_from_page(page)
            all_rows.extend(rows)
        
        # 合計行から直接総取得単位数を抽出
        total_from_summary = find_total_from_summary_row(all_rows, debug_mode)
        
        # 各区分の単位数を抽出
        results = extract_from_status_table(all_rows, debug_mode)
        
        # 自由履修対象科目を抽出
        free_elective_total = extract_free_electives(all_rows, debug_mode)
        
        # 外国語必修内訳を抽出
        foreign_detail = extract_foreign_details(all_rows, debug_mode)
        
        # 余剰単位を計算
        surplus_total = 0
        for category, (obtained, required) in results.items():
            if category not in ["自由履修科目", "外国語科目区分"]:  # 外国語は除外
                surplus = max(0, obtained - required)
                surplus_total += surplus
        
        # 自由履修科目の最終値を計算
        total_free = free_elective_total + surplus_total
        results["自由履修科目"] = (total_free, 24)
        
        if debug_mode:
            print(f"\n=== 最終計算 ===")
            print(f"自由履修対象科目合計: {free_elective_total}")
            print(f"他区分からの余剰単位: {surplus_total}")
            print(f"自由履修最終: {total_free}")
            if total_from_summary:
                print(f"成績表記載の総取得単位: {total_from_summary}")
            print(f"発見された区分: {list(results.keys())}")
            print(f"必修内訳: {list(foreign_detail.keys())}")
        
        return results, foreign_detail, total_from_summary

def analyze_results(results, foreign_detail, total_from_summary=None):
    """結果の分析と出力生成"""
    output = []
    total_required = 124

    output.append("📊 === 単位取得状況分析結果 ===")
    
    # 各区分の状況を表示
    for cat, (obtained, required) in results.items():
        status = "✅ 完了" if obtained >= required else f"❌ あと{required - obtained}単位"
        output.append(f"{cat} {obtained}/{required} {status}")

    # 備考欄（必修内訳）
    if foreign_detail:
        output.append("\n📋 === 備考欄（必修内訳）===")
        unmet_requirements = []
        
        for cat, (obtained, required) in foreign_detail.items():
            status = "✅ 完了" if obtained >= required else f"❌ あと{required - obtained}単位"
            output.append(f"  {cat} {obtained}/{required} {status}")
            
            if obtained < required:
                unmet_requirements.append(f"   - {cat}: あと{required - obtained}単位")

    # 卒業判定（成績表の合計を使用）
    if total_from_summary is not None:
        total_obtained = total_from_summary
    else:
        # フォールバック：各区分から計算
        total_obtained = 0
        for cat, (obtained, required) in results.items():
            if cat == "自由履修科目":
                total_obtained += obtained
            else:
                total_obtained += min(obtained, required)
    
    output.append("\n========================================")
    output.append(f"🎓 卒業必要単位数: {total_required}")
    output.append(f"✅ 取得済み単位数: {total_obtained}")

    if total_obtained >= total_required:
        output.append("🎉 おめでとうございます！卒業要件を満たしています")
        
        # ただし必修内訳に未達があれば警告
        if unmet_requirements:
            output.append("\n⚠️ ただし、外国語必修内訳に未達があります:")
            output.extend(unmet_requirements)
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
def parse_grades_from_pdf(pdf_path):
    """
    app.py から呼び出すためのラッパ関数。
    テキスト形式とリスト形式の両方を返す。
    """
    try:
        results, foreign_detail, total_from_summary = parse_units_advanced(pdf_path)
        
        # テキスト形式のレポートを生成
        grades_text = analyze_results(results, foreign_detail, total_from_summary)
        
        # app.pyが期待する形式（辞書のリスト）に変換
        grades_list = []
        for category, (obtained, required) in results.items():
            grades_list.append({
                "category": category,
                "earned": obtained,
                "required": required
            })
        
        # 外国語必修内訳も追加
        for detail_cat, (obtained, required) in foreign_detail.items():
            grades_list.append({
                "category": f"外国語必修内訳_{detail_cat}",
                "earned": obtained,
                "required": required
            })
        
        return grades_text, grades_list
        
    except Exception as e:
        error_msg = f"PDF解析エラー: {str(e)}"
        print(error_msg)
        return error_msg, []
    
if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1:
        pdf_path = sys.argv[1]
    else:
        pdf_path = "成績.pdf"
    
    print(f"PDFファイルを解析中: {pdf_path}")
    result = check_pdf(pdf_path)
    print(result)
