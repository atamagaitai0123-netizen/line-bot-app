import pandas as pd
from supabase import create_client, Client
import os

# SupabaseのURLとKeyを設定
SUPABASE_URL = "https://zqihsfkgjaenzndopzpk.supabase.co"  # ←あなたのSupabase URL
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InpxaWhzZmtnamFlbnpuZG9wenBrIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc1ODM1OTAxNSwiZXhwIjoyMDczOTM1MDE1fQ.kNuU6cQ5JpWhKHejYL-uFxZVuAExY9vH0pBGqBcIEUo"  # ← service_roleキーを使用

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# CSVファイルを読み込み
csv_file = "syllabus_parsed.csv"
df = pd.read_csv(csv_file)

print("🔍 CSVのカラム:", df.columns.tolist())

# カラム名をSupabase用にリネーム
df = df.rename(columns={
    "授業名・教員": "subject_teacher",
    "単位": "units",
    "年次": "grade_year",
    "学期": "semester",
    "キャンパス": "campus",
    "成績評価": "evaluation",
    "科目区分": "category"
})

print("✅ rename後のカラム:", df.columns.tolist())

# 欠損値を「不明」に置換
df["subject_teacher"] = df["subject_teacher"].fillna("不明").astype(str).str.strip()
df["units"] = df["units"].fillna("不明").astype(str).str.strip()
df["grade_year"] = df["grade_year"].fillna("不明").astype(str).str.strip()
df["semester"] = df["semester"].fillna("不明").astype(str).str.strip()
df["campus"] = df["campus"].fillna("不明").astype(str).str.strip()
df["evaluation"] = df["evaluation"].fillna("不明").astype(str).str.strip()
df["category"] = df["category"].fillna("不明").astype(str).str.strip()

print("📊 インポート対象件数:", len(df))

# データをSupabaseにアップロード（分割して処理）
chunk_size = 500
for i in range(0, len(df), chunk_size):
    chunk = df.iloc[i:i+chunk_size].to_dict(orient="records")
    res = supabase.table("syllabus").upsert(chunk).execute()
    print(f"✅ {i+1}〜{i+len(chunk)} 件目をインポート完了")
