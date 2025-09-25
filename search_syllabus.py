from supabase import create_client
import os

# Supabaseの接続情報
url = "https://zqihsfkgjaenzndopzpk.supabase.co"
key = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InpxaWhzZmtnamFlbnpuZG9wenBrIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc1ODM1OTAxNSwiZXhwIjoyMDczOTM1MDE1fQ.kNuU6cQ5JpWhKHejYL-uFxZVuAExY9vH0pBGqBcIEUo"

supabase = create_client(url, key)

def search_syllabus(keyword: str):
    # ① 完全一致を探す
    response = supabase.table("syllabus").select("*").eq("subject_teacher", keyword).execute()
    data = response.data

    # ② 無ければ前方一致
    if not data:
        response = supabase.table("syllabus").select("*").ilike("subject_teacher", f"{keyword}%").execute()
        data = response.data

    return data


if __name__ == "__main__":
    keyword = input("授業名を入力してください: ")
    results = search_syllabus(keyword)

    if results:
        for row in results:
            print("=== 授業 ===")
            print("授業名・教員:", row.get("subject_teacher", "不明"))
            print("単位:", row.get("units", "不明"))
            print("学年:", row.get("grade_year", "不明"))
            print("学期:", row.get("semester", "不明"))
            print("キャンパス:", row.get("campus", "不明"))
            print("成績評価:", row.get("evaluation", "不明"))
            print()
    else:
        print("該当する授業が見つかりませんでした。")
