import pdfplumber
import re
from pathlib import Path

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

YEARS_ORDER = ['25', '24', '23', '22']

# ================================
# 座標ベースのPDF解析
# ================================
def check_pdf(pdf_path, page_no=0, return_dict=False):
    pdf_file = Path(pdf_path)
    if not pdf_file.exists():
        raise FileNotFoundError(f"PDFファイルが見つかりません: {pdf_path}")

    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[page_no]
        lines = extract_lines_from_page(page, line_tol=6)

    print("=== 座標ベース単位取得状況表解析 ===")
    
    results = parse_main_requirements(lines)
    remarks = parse_sub_requirements(lines)
    
    if return_dict:
        return results, remarks
    
    return format_output(results, remarks)

def normalize(s: str) -> str:
    """文字列の正規化（空白除去）"""
    if not s:
        return ""
    return re.sub(r'\s+', '', s)

def extract_lines_from_page(page, line_tol=6):
    """PDFページから座標ベースで論理行を抽出"""
    words = page.extract_words()
    if not words:
        return []
    
    # Y座標、X座標でソート
    words_sorted = sorted(words, key=lambda w: (w['top'], w['x0']))
    
    lines = []
    current_top = words_sorted[0]['top']
    current_words = []
    
    for word in words_sorted:
        # 同じ行かどうか判定（Y座標の差がline_tol以内）
        if abs(word['top'] - current_top) <= line_tol:
            current_words.append(word)
        else:
            # 行を完成させる
            if current_words:
                current_words_sorted = sorted(current_words, key=lambda x: x['x0'])
                text = " ".join(w['text'] for w in current_words_sorted)
                numbers = re.findall(r'\d+', text)
                lines.append({
                    'text': text,
                    'first_x': current_words_sorted[0]['x0'],
                    'top': current_top,
                    'numbers': numbers,
                    'words': current_words_sorted
                })
            
            # 新しい行を開始
            current_top = word['top']
            current_words = [word]
    
    # 最後の行を処理
    if current_words:
        current_words_sorted = sorted(current_words, key=lambda x: x['x0'])
        text = " ".join(w['text'] for w in current_words_sorted)
        numbers = re.findall(r'\d+', text)
        lines.append({
            'text': text,
            'first_x': current_words_sorted[0]['x0'],
            'top': current_top,
            'numbers': numbers,
            'words': current_words_sorted
        })
    
    return lines

def find_keyword_rows(lines, keywords):
    """キーワードを含む行を座標ベースで抽出"""
    logical_rows = []
    
    for line in lines:
        words = line['words']
        word_count = len(words)
        
        for i in range(word_count):
            # 1-3個の連続する単語を組み合わせてチェック
            for width in (1, 2, 3):
                if i + width > word_count:
                    continue
                    
                candidate = "".join(words[i + j]['text'] for j in range(width))
                
                for keyword in keywords:
                    if normalize(keyword) in normalize(candidate):
                        # キーワード以降の単語を取得
                        remaining_words = words[i:]
                        text = " ".join(w['text'] for w in remaining_words)
                        numbers = re.findall(r'\d+', text)
                        
                        logical_rows.append({
                            'name': text,
                            'first_x': remaining_words[0]['x0'],
                            'top': line['top'],
                            'numbers': numbers
                        })
                        break
                else:
                    continue
                break
    
    # 重複除去
    unique_rows = []
    seen_keys = set()
    
    for row in logical_rows:
        key = (row['top'], row['first_x'], row['name'])
        if key not in seen_keys:
            seen_keys.add(key)
            unique_rows.append(row)
    
    return unique_rows

def parse_numbers_to_metrics(numbers):
    """数値リストから必要・年度別・合計を抽出"""
    if not numbers:
        return {'required': None, 'years': {}, 'total': None}
    
    total = int(numbers[-1])
    preceding = [int(x) for x in numbers[:-1]]
    
    required = None
    year_values = []
    
    if len(preceding) == len(YEARS_ORDER) + 1:
        # 必要単位 + 年度別単位の場合
        required = preceding[0]
        year_values = preceding[1:]
    elif len(preceding) <= len(YEARS_ORDER):
        # 年度別単位のみの場合
        year_values = preceding
    else:
        # その他の場合（最初を必要単位とみなす）
        required = preceding[0]
        year_values = preceding[1:]
    
    years = {}
    for i, value in enumerate(year_values):
        if i < len(YEARS_ORDER):
            years[YEARS_ORDER[i]] = value
    
    return {'required': required, 'years': years, 'total': total}

def parse_main_requirements(lines):
    """メイン要件を座標ベースで解析"""
    results = {}
    
    keywords = list(REQUIREMENTS.keys()) + ["合計", "総合計"]
    logical_rows = find_keyword_rows(lines, keywords)
    logical_sorted = sorted(logical_rows, key=lambda r: (r['top'], r['first_x']))
    
    for category in REQUIREMENTS.keys():
        required = REQUIREMENTS[category]
        candidates = [r for r in logical_sorted if normalize(category) in normalize(r['name'])]
        
        best_candidate = None
        best_total = -1
        
        for candidate in candidates:
            metrics = parse_numbers_to_metrics(candidate['numbers'])
            total = metrics['total'] if metrics['total'] is not None else -1
            
            if total > best_total:
                best_total = total
                best_candidate = {'metrics': metrics}
        
        if best_candidate:
            obtained = best_candidate['metrics']['total'] or 0
            print(f"[DEBUG] {category}: 取得={obtained}")
        else:
            obtained = 0
            print(f"[WARNING] {category}: データが見つかりません")
        
        results[category] = {"required": required, "obtained": obtained}
    
    return results

def parse_sub_requirements(lines):
    """備考要件を座標ベースで解析"""
    results = {}
    
    logical_rows = find_keyword_rows(lines, list(REMARK_REQUIREMENTS.keys()))
    
    for subject, required in REMARK_REQUIREMENTS.items():
        candidates = [r for r in logical_rows if normalize(subject) in normalize(r['name'])]
        
        if not candidates:
            obtained = 0
            print(f"[WARNING] 備考 - {subject}: データが見つかりません")
        else:
            # 最も数値が大きい候補を選択
            best = max(candidates, key=lambda c: int(c['numbers'][-1]) if c['numbers'] else -1)
            metrics = parse_numbers_to_metrics(best['numbers'])
            obtained = metrics['total'] or 0
            print(f"[DEBUG] 備考 - {subject}: 取得={obtained}")
        
        results[subject] = {"required": required, "obtained": obtained}
    
    return results

def format_output(results, remarks):
    """結果を読みやすい形式でフォーマット"""
    output = []
    output.append("📊 === 単位取得状況分析結果 ===")
    
    total_shortage = 0
    shortage_details = []
    urgent_items = []
    
    # メイン科目区分の状況
    for category, info in results.items():
        required = info["required"]
        obtained = info["obtained"]
        
        if obtained >= required:
            status = "✅ 完了"
        else:
            shortage = required - obtained
            status = f"❌ あと{shortage}単位"
            if category != "合計":
                total_shortage += shortage
                shortage_details.append(f"• {category}: {shortage}単位不足")
                # 緊急度判定
                if shortage >= 10:
                    urgent_items.append(category)
        
        output.append(f"{category:<20} {obtained:>3}/{required:<3}単位 {status}")

    output.append("")
    output.append("📋 === 備考欄（必修内訳）===")
    
    # 備考欄の必修科目チェック
    remark_shortages = []
    for subject, info in remarks.items():
        required = info["required"]
        obtained = info["obtained"]
        
        if obtained >= required:
            status = "✅ 完了"
        else:
            shortage = required - obtained
            status = f"❌ あと{shortage}単位"
            total_shortage += shortage
            shortage_details.append(f"• {subject}: {shortage}単位不足")
            remark_shortages.append(subject)
        
        output.append(f"{subject:<20} {obtained:>3}/{required:<3}単位 {status}")

    # 総合判定
    output.append("")
    output.append("=" * 40)
    
    if total_shortage == 0:
        output.append("🎉 おめでとうございます！")
        output.append("   卒業要件をすべて満たしています！")
        output.append("")
        output.append("💡 今後のアドバイス:")
        output.append("   • 成績向上やGPA改善に取り組んでみましょう")
        output.append("   • 資格取得や語学スキル向上もおすすめです")
    else:
        output.append(f"📝 卒業まであと合計 {total_shortage} 単位必要です")
        output.append("")
        output.append("⚠️  不足科目の詳細:")
        output.extend(shortage_details)
        
        # 注意点とアドバイス
        output.append("")
        output.append("🚨 === 重要な注意点 ===")
        
        if urgent_items:
            output.append(f"• 【高優先度】{', '.join(urgent_items)} は不足単位が多いです")
            output.append("  → 来学期で集中的に履修計画を立ててください")
        
        if remark_shortages:
            output.append("• 【必修条件未達】備考欄の必修要件が不足しています")
            for subject in remark_shortages:
                if subject == "英語（初級）":
                    output.append("  → 英語（初級）A・Bを優先履修してください")
                elif subject == "初習外国語":
                    output.append("  → 第二外国語（中国語・ドイツ語等）を継続履修してください")
                elif subject == "外国語を用いた科目":
                    output.append("  → Business English等の専門外国語科目を履修してください")
        
        if total_shortage > 30:
            output.append("• 【履修計画要注意】不足単位が多いため、履修上限に注意が必要です")
            output.append("  → 学務担当に相談することをお勧めします")
        
        output.append("")
        output.append("💡 === 履修計画のアドバイス ===")
        
        if results["自由履修科目"]["obtained"] < results["自由履修科目"]["required"]:
            output.append("• 自由履修科目は他学部科目・演習科目でも単位取得可能です")
            output.append("  → 興味のある分野から選択して履修してください")
        
        if results["教養科目区分"]["obtained"] < results["教養科目区分"]["required"]:
            output.append("• 教養科目は幅広い分野から選択可能です")
            output.append("  → バランスよく文系・理系科目を履修しましょう")
        
        # 学期別の履修提案
        remaining_semesters = max(1, (8 - 4))  # 4年生前提
        units_per_semester = max(10, total_shortage // remaining_semesters)
        
        output.append(f"• 残り学期で1学期あたり約{units_per_semester}単位の履修が必要です")
        output.append("  → 履修上限（通常22-24単位）内で計画的に履修してください")
        
        output.append("")
        output.append("📞 困った時は:")
        output.append("• 学務担当窓口での履修相談")
        output.append("• ゼミ担当教員への相談")
        output.append("• 先輩や同級生からの情報収集")

    return "\n".join(output)

# デバッグ・テスト用
if __name__ == "__main__":
    PDF_PATH = "成績.pdf"
    PAGE_NO = 0
    
    try:
        result = check_pdf(PDF_PATH, PAGE_NO)
        print("\n" + "=" * 50)
        print("最終出力結果:")
        print("=" * 50)
        print(result)
        
    except Exception as e:
        print(f"エラー発生: {e}")
        import traceback
        traceback.print_exc()
