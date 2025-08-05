import streamlit as st
import pandas as pd
import pdfplumber
import collections
import re
from PIL import Image # 導入 Pillow 函式庫
import pytesseract # 導入 pytesseract
from img2table.document import PDF as Img2TablePDF # 導入 img2table 的 PDF 類
from img2table.ocr import TesseractOCR # 導入 TesseractOCR
import io # 導入 io 模組用於處理 BytesIO

# --- 全域定義的關鍵字列表 ---
credit_column_keywords = ["學分", "學分數", "學分(GPA)", "學 分", "Credits", "Credit", "學分數(學分)", "總學分"]
subject_column_keywords = ["科目名稱", "課程名稱", "Course Name", "Subject Name", "科目", "課程"]
gpa_column_keywords = ["GPA", "成績", "Grade", "gpa(數值)"]
year_column_keywords = ["學年", "year", "學 年", "學年度"]
semester_column_keywords = ["學期", "semester", "學 期"]
course_code_keywords = ["選課代號", "課號", "課程代碼", "course code"]

# 將所有標頭關鍵字扁平化為一個列表，用於更廣泛的標頭行判斷
all_header_keywords_flat_lower = set([
    re.sub(r'[\s\n]+', '', k.lower()) for k in
    credit_column_keywords +
    subject_column_keywords +
    gpa_column_keywords +
    year_column_keywords +
    semester_column_keywords +
    course_code_keywords
])

# 不及格成績的定義
failing_grades = ["D", "D-", "E", "F", "X", "不通過", "未通過", "不及格", "fail", "failed"]

# 定義成績與學分的對應關係 (可根據學校的評分系統調整)
grade_to_gpa = {
    'A+': 4.3, 'A': 4.0, 'A-': 3.7,
    'B+': 3.3, 'B': 3.0, 'B-': 2.7,
    'C+': 2.3, 'C': 2.0, 'C-': 1.7,
    'D+': 1.3, 'D': 1.0, 'D-': 0.7,
    'E': 0.0, 'F': 0.0, 'X': 0.0,
    '不及格': 0.0, '不通過': 0.0,
}
passing_grades_keywords = ['A+', 'A', 'A-', 'B+', 'B', 'B-', 'C+', 'C', 'C-', '通過', '抵免']


# --- 輔助函數 ---
def normalize_text(cell_content):
    """
    標準化從 pdfplumber 或 OCR 提取的單元格內容。
    處理 None 值、pdfplumber 的 Text 物件和普通字串。
    將多個空白字元（包括換行、全形空格）替換為單個空格，並去除兩端空白。
    """
    if cell_content is None:
        return ""

    text = ""
    if hasattr(cell_content, 'text'):
        text = str(cell_content.text)
    elif isinstance(cell_content, str):
        text = cell_content
    else:
        text = str(cell_content)

    return re.sub(r'[\s\u3000]+', ' ', text).strip()


def make_unique_columns(columns_list):
    """
    將列表中的欄位名稱轉換為唯一的名稱，處理重複和空字串。
    如果遇到重複或空字串，會添加後綴 (例如 'Column_1', '欄位_2')。
    此版本會嘗試去除原始列名中的換行符和多餘空格後再處理唯一性。
    """
    seen = collections.defaultdict(int)
    unique_columns = []
    for col in columns_list:
        # 先進行標準化，去除換行和多餘空格
        original_col_cleaned = normalize_text(col)

        if not original_col_cleaned or len(original_col_cleaned) < 2: # 對於非常短或空白的列名給予通用名
            name_base = "Column"
            current_idx = 1
            while f"{name_base}_{current_idx}" in unique_columns:
                current_idx += 1
            name = f"{name_base}_{current_idx}"
        else:
            name = original_col_cleaned

        final_name = name
        counter = seen[name]
        while final_name in unique_columns:
            counter += 1
            final_name = f"{name}_{counter}"

        unique_columns.append(final_name)
        seen[name] = counter

    return unique_columns


def parse_credit_and_gpa(text):
    """
    從單元格文本中解析學分和 GPA。
    考慮 "A 2" (GPA在左，學分在右) 和 "2 A" (學分在左，GPA在右) 的情況。
    返回 (學分, GPA)。如果解析失敗，返回 (0.0, "")。
    """
    text_clean = normalize_text(text).lower()

    # 首先檢查是否是「通過」或「抵免」等關鍵詞
    if "通過" in text_clean or "抵免" in text_clean or "pass" in text_clean or "exempt" in text_clean:
        return 0.0, text_clean # 返回0學分和原始文字，讓後面判斷為特殊成績

    credit = 0.0
    gpa = ""

    # 嘗試匹配 "GPA 學分" 模式 (例如 "A 2", "C- 3")
    match_gpa_credit = re.match(r'([a-z][+\-]?)\s*(\d+(\.\d+)?)', text_clean)
    if match_gpa_credit:
        try:
            gpa = match_gpa_credit.group(1).upper()
            credit = float(match_gpa_credit.group(2))
            if 0.0 <= credit <= 5.0:  # 學分範圍允許0學分
                return credit, gpa
        except ValueError:
            pass

    # 嘗試匹配 "學分 GPA" 模式 (例如 "2 A", "3 B-")
    match_credit_gpa = re.match(r'(\d+(\.\d+)?)\s*([a-z][+\-]?)', text_clean)
    if match_credit_gpa:
        try:
            credit = float(match_credit_gpa.group(1))
            gpa = match_credit_gpa.group(3).upper()
            if 0.0 <= credit <= 5.0:  # 學分範圍允許0學分
                return credit, gpa
        except ValueError:
            pass

    # 嘗試只匹配學分 (純數字)
    credit_only_match = re.search(r'(\d+(\.\d+)?)', text_clean)
    if credit_only_match:
        try:
            credit = float(credit_only_match.group(1))
            if 0.0 <= credit <= 5.0:
                # 如果只提取到學分，檢查是否有單獨的GPA字母在附近（不在數字旁邊）
                gpa_only_match = re.search(r'([a-z][+\-]?)', text_clean.replace(credit_only_match.group(0), ''))
                if gpa_only_match:
                    gpa = gpa_only_match.group(1).upper()
                return credit, gpa
        except ValueError:
            pass

    # 嘗試只匹配 GPA (純字母)
    gpa_only_match = re.search(r'([a-z][+\-]?)', text_clean)
    if gpa_only_match:
        return 0.0, gpa_only_match.group(1).upper()

    return 0.0, ""


def is_grades_table(df):
    """
    判斷一個 DataFrame 是否為有效的成績單表格。
    透過檢查是否存在預期的欄位關鍵字和數據內容模式來判斷。
    """
    if df.empty or len(df.columns) < 3:
        return False

    # Normalize column names for keyword matching
    normalized_columns = {normalize_text(col).lower().replace(' ', '').replace('\n', ''): col for col in df.columns.tolist()}

    has_credit_col_header = any(any(k in norm_col for k in [kw.lower().replace(' ', '').replace('\n', '') for kw in credit_column_keywords]) for norm_col in normalized_columns.keys())
    has_gpa_col_header = any(any(k in norm_col for k in [kw.lower().replace(' ', '').replace('\n', '') for kw in gpa_column_keywords]) for norm_col in normalized_columns.keys())
    has_subject_col_header = any(any(k in norm_col for k in [kw.lower().replace(' ', '').replace('\n', '') for kw in subject_column_keywords]) for norm_col in normalized_columns.keys())
    has_year_col_header = any(any(k in norm_col for k in [kw.lower().replace(' ', '').replace('\n', '') for kw in year_column_keywords]) for norm_col in normalized_columns.keys())
    has_semester_col_header = any(any(k in norm_col for k in [kw.lower().replace(' ', '').replace('\n', '') for kw in semester_column_keywords]) for norm_col in normalized_columns.keys())
    has_course_code_col_header = any(any(k in norm_col for k in [kw.lower().replace(' ', '').replace('\n', '') for kw in course_code_keywords]) for norm_col in normalized_columns.keys())


    if has_subject_col_header and (has_credit_col_header or has_gpa_col_header) and has_year_col_header and has_semester_col_header:
        st.info(f"偵測到符合成績單特徵的表格內容 (標頭符合: 科目: {has_subject_col_header}, 學年: {has_year_col_header}, 學期: {has_semester_col_header}, 學分/GPA: {has_credit_col_header or has_gpa_col_header})")
        return True

    found_year_by_content = False
    found_semester_by_content = False
    found_subject_by_content = False
    found_credit_or_gpa_by_content = False
    found_course_code_by_content = False

    sample_rows_df = df.head(min(len(df), 20))

    for col_name in df.columns:
        sample_data = sample_rows_df[col_name].apply(normalize_text).tolist()
        total_sample_count = len(sample_data)
        if total_sample_count == 0:
            continue

        if not found_year_by_content:
            year_like_cells = sum(1 for item_str in sample_data if (item_str.isdigit() and (len(item_str) == 3 or len(item_str) == 4)))
            if year_like_cells / total_sample_count >= 0.6:
                found_year_by_content = True

        if not found_semester_by_content:
            semester_like_cells = sum(1 for item_str in sample_data if item_str.lower() in ["上", "下", "春", "夏", "秋", "冬", "1", "2", "3", "春季", "夏季", "秋季", "冬季", "spring", "summer", "fall", "winter"])
            if semester_like_cells / total_sample_count >= 0.6:
                found_semester_by_content = True
        
        if not found_course_code_by_content:
            course_code_like_cells = sum(1 for item_str in sample_data if re.match(r'^[A-Za-z0-9]{3,8}$', item_str) and not item_str.isdigit())
            if course_code_like_cells / total_sample_count >= 0.3:
                found_course_code_by_content = True

        if not found_subject_by_content:
            subject_like_cells = sum(1 for item_str in sample_data
                                     if re.search(r'[\u4e00-\u9fa5]', item_str) and len(item_str) >= 2
                                     and not item_str.isdigit()
                                     and not re.match(r'^[A-Fa-f][+\-]?$', item_str)
                                     and not re.match(r'^\d+(\.\d+)?$', item_str)
                                     and not re.match(r'^[A-Za-z0-9]{3,8}$', item_str)
                                     and not item_str.lower() in ["通過", "抵免", "pass", "exempt", "未知科目"]
                                     and not any(k in item_str.lower().replace(' ', '').replace('\n', '') for k in all_header_keywords_flat_lower)
                                     )
            if subject_like_cells / total_sample_count >= 0.4:
                found_subject_by_content = True

        if not found_credit_or_gpa_by_content:
            credit_gpa_like_cells = 0
            for item_str in sample_data:
                credit_val, gpa_val = parse_credit_and_gpa(item_str)
                if (0.0 <= credit_val <= 5.0) or \
                   (gpa_val and re.match(r'^[A-Fa-f][+\-]?$', gpa_val)) or \
                   (item_str.lower() in ["通過", "抵免", "pass", "exempt"]):
                    credit_gpa_like_cells += 1
            if credit_gpa_like_cells / total_sample_count >= 0.4:
                found_credit_or_gpa_by_content = True

    if found_subject_by_content and found_year_by_content and found_semester_by_content and (found_credit_or_gpa_by_content):
        st.info(f"偵測到符合成績單特徵的表格內容 (內容符合: 科目: {found_subject_by_content}, 學年: {found_year_by_content}, 學期: {found_semester_by_content}, 學分/GPA: {found_credit_or_gpa_by_content}, 選課代號: {found_course_code_by_content})")
        return True

    return False


def calculate_total_credits(df_list):
    """
    從提取的 DataFrames 列表中計算總學分。
    尋找包含 '學分' 或 '學分(GPA)' 類似字樣的欄位進行加總。
    返回總學分和計算學分的科目列表，以及不及格科目列表。
    """
    total_credits = 0.0
    total_gpa_points = 0.0 # 用於計算平均GPA
    calculated_courses = []
    failed_courses = []

    if not df_list:
        return total_credits, calculated_courses, failed_courses

    for df_idx, df in enumerate(df_list):
        if df.empty or len(df.columns) < 3:
            st.info(f"表格 {df_idx + 1} 為空或欄位太少，已跳過。")
            continue

        identified_columns = {
            "year": None, "semester": None, "course_code": None,
            "subject": None, "credit": None, "gpa": None
        }

        col_role_scores = collections.defaultdict(lambda: collections.defaultdict(float))

        sample_rows_df = df.head(min(len(df), 20))

        for col_name in df.columns:
            sample_data = sample_rows_df[col_name].apply(normalize_text).tolist()
            total_sample_count = len(sample_data)
            if total_sample_count == 0:
                continue

            norm_col_name_for_header_match = normalize_text(col_name).lower().replace(' ', '').replace('\n', '')

            if any(k in norm_col_name_for_header_match for k in [re.sub(r'[\s\n]+', '', kw.lower()) for kw in year_column_keywords]):
                col_role_scores[col_name]["year"] += 2.0
            if any(k in norm_col_name_for_header_match for k in [re.sub(r'[\s\n]+', '', kw.lower()) for kw in semester_column_keywords]):
                col_role_scores[col_name]["semester"] += 2.0
            if any(k in norm_col_name_for_header_match for k in [re.sub(r'[\s\n]+', '', kw.lower()) for kw in course_code_keywords]):
                col_role_scores[col_name]["course_code"] += 2.0
            if any(k in norm_col_name_for_header_match for k in [re.sub(r'[\s\n]+', '', kw.lower()) for kw in subject_column_keywords]):
                col_role_scores[col_name]["subject"] += 2.0
            if any(k in norm_col_name_for_header_match for k in [re.sub(r'[\s\n]+', '', kw.lower()) for kw in credit_column_keywords]):
                col_role_scores[col_name]["credit"] += 2.0
            if any(k in norm_col_name_for_header_match for k in [re.sub(r'[\s\n]+', '', kw.lower()) for kw in gpa_column_keywords]):
                col_role_scores[col_name]["gpa"] += 2.0

            year_content_score = sum(1 for item_str in sample_data if (item_str.isdigit() and (len(item_str) == 3 or len(item_str) == 4))) / total_sample_count
            semester_content_score = sum(1 for item_str in sample_data if item_str.lower() in ["上", "下", "春", "夏", "秋", "冬", "1", "2", "3", "春季", "夏季", "秋季", "冬季", "spring", "summer", "fall", "winter"]) / total_sample_count
            course_code_content_score = sum(1 for item_str in sample_data if re.match(r'^[A-Za-z0-9]{3,8}$', item_str) and not item_str.isdigit()) / total_sample_count
            
            credit_gpa_content_score = 0
            for item_str in sample_data:
                credit_val, gpa_val = parse_credit_and_gpa(item_str)
                if (0.0 <= credit_val <= 5.0 and credit_val != 0.0) or \
                   (gpa_val and re.match(r'^[A-Fa-f][+\-]?$', gpa_val)) or \
                   (item_str.lower() in ["通過", "抵免", "pass", "exempt"]):
                    credit_gpa_content_score += 1
            credit_gpa_content_score /= total_sample_count

            subject_content_score = sum(1 for item_str in sample_data
                                 if re.search(r'[\u4e00-\u9fa5]', item_str) and len(item_str) >= 2
                                 and not item_str.isdigit()
                                 and not re.match(r'^[A-Fa-f][+\-]?$', item_str)
                                 and not re.match(r'^\d+(\.\d+)?$', item_str)
                                 and not re.match(r'^[A-Za-z0-9]{3,8}$', item_str)
                                 and not item_str.lower() in ["通過", "抵免", "pass", "exempt", "未知科目"]
                                 and not any(k in item_str.lower().replace(' ', '').replace('\n', '') for k in all_header_keywords_flat_lower)
                                ) / total_sample_count

            col_role_scores[col_name]["year"] += year_content_score
            col_role_scores[col_name]["semester"] += semester_content_score
            col_role_scores[col_name]["course_code"] += course_code_content_score
            col_role_scores[col_name]["credit_gpa_combined"] += credit_gpa_content_score
            col_role_scores[col_name]["subject"] += subject_content_score


        candidate_assignments = []
        for col_name, roles_scores in col_role_scores.items():
            for role, score in roles_scores.items():
                if score > 0:
                    candidate_assignments.append((score, col_name, role))

        candidate_assignments.sort(key=lambda x: (-x[0], df.columns.get_loc(x[1])))

        assigned_cols_names = set()
        for score, col_name, role in candidate_assignments:
            if col_name not in assigned_cols_names:
                if role == "credit_gpa_combined":
                    if identified_columns["credit"] is None and any(k in normalize_text(col_name).lower().replace(' ', '').replace('\n', '') for k in [re.sub(r'[\s\n]+', '', kw.lower()) for kw in credit_column_keywords]):
                        identified_columns["credit"] = col_name
                        assigned_cols_names.add(col_name)
                    elif identified_columns["gpa"] is None and any(k in normalize_text(col_name).lower().replace(' ', '').replace('\n', '') for k in [re.sub(r'[\s\n]+', '', kw.lower()) for kw in gpa_column_keywords]):
                        identified_columns["gpa"] = col_name
                        assigned_cols_names.add(col_name)
                    elif identified_columns["credit"] is None:
                        identified_columns["credit"] = col_name
                        assigned_cols_names.add(col_name)
                    elif identified_columns["gpa"] is None:
                        identified_columns["gpa"] = col_name
                        assigned_cols_names.add(col_name)
                elif identified_columns[role] is None:
                    identified_columns[role] = col_name
                    assigned_cols_names.add(col_name)
        
        found_year_column = identified_columns["year"]
        found_semester_column = identified_columns["semester"]
        found_course_code_column = identified_columns["course_code"]
        found_subject_column = identified_columns["subject"]
        found_credit_column = identified_columns["credit"]
        found_gpa_column = identified_columns["gpa"]

        if found_year_column and found_semester_column and found_subject_column and (found_credit_column or found_gpa_column):
            st.success(f"頁面 {df_idx + 1} 成功識別以下關鍵欄位：")
            st.success(f"  學年欄位: '{found_year_column}'")
            st.success(f"  學期欄位: '{found_semester_column}'")
            st.success(f"  選課代號欄位: '{found_course_code_column if found_course_code_column else '未找到'}'")
            st.success(f"  科目名稱欄位: '{found_subject_column}'")
            st.success(f"  學分欄位: '{found_credit_column if found_credit_column else '未找到'}'")
            st.success(f"  GPA 欄位: '{found_gpa_column if found_gpa_column else '未找到'}'")

            try:
                for row_idx, row in df.iterrows():
                    st.info(f"--- 處理表格 {df_idx + 1}, 第 {row_idx + 1} 行 ---")
                    row_content_normalized = [normalize_text(str(cell)) for cell in row.tolist()]
                    st.info(f"原始資料列內容 (標準化後): {row_content_normalized}")

                    is_header_row_content = False
                    header_keyword_matches = 0
                    for cell_val in row_content_normalized:
                        if cell_val.lower().replace(' ', '').replace('\n', '') in all_header_keywords_flat_lower:
                            header_keyword_matches += 1

                    if (header_keyword_matches >= len(row_content_normalized) / 2 and header_keyword_matches >= 3) or \
                       (len(row_content_normalized) > 0 and row_content_normalized[0] == "" and header_keyword_matches >= 3):
                        st.info("該行被判斷為標頭行，已跳過。")
                        continue

                    if all(cell == "" for cell in row_content_normalized) or \
                       any("本表僅供查詢" in cell or "學號" in cell or "勞作" in cell or "體育室" in cell or "畢業門檻" in cell or "網站" in cell or "http" in cell for cell in row_content_normalized):
                        st.info("該行被判斷為空行、行政性文字或頁腳，已跳過。")
                        continue

                    extracted_credit = 0.0
                    extracted_gpa = ""
                    course_name = "未知科目"
                    acad_year = ""
                    semester = ""
                    course_code = ""

                    if found_year_column and found_year_column in row and pd.notna(row[found_year_column]):
                        temp_year = normalize_text(row[found_year_column])
                        year_match = re.search(r'(\d{3,4})', temp_year)
                        if year_match:
                            acad_year = year_match.group(1)

                    if found_semester_column and found_semester_column in row and pd.notna(row[found_semester_column]):
                        temp_sem = normalize_text(row[found_semester_column])
                        sem_match = re.search(r'(上|下|春|夏|秋|冬|1|2|3|春季|夏季|秋季|冬季|spring|summer|fall|winter)', temp_sem, re.IGNORECASE)
                        if sem_match:
                            semester = sem_match.group(1)

                    if not acad_year and not semester:
                        for col_idx in range(min(len(df.columns), 3)):
                            col_name = df.columns[col_idx]
                            if col_name in row and pd.notna(row[col_name]):
                                col_content = normalize_text(row[col_name])
                                year_match = re.search(r'(\d{3,4})', col_content)
                                sem_match = re.search(r'(上|下|春|夏|秋|冬|1|2|3|春季|夏季|秋季|冬季|spring|summer|fall|winter)', col_content, re.IGNORECASE)
                                if year_match and not acad_year:
                                    acad_year = year_match.group(1)
                                if sem_match and not semester:
                                    semester = sem_match.group(1)
                                if acad_year and semester:
                                    break

                    if found_course_code_column and found_course_code_column in row and pd.notna(row[found_course_code_column]):
                        temp_code = normalize_text(row[found_course_code_column])
                        if re.match(r'^[A-Za-z0-9]{3,8}$', temp_code):
                            course_code = temp_code

                    if found_credit_column and found_credit_column in row and pd.notna(row[found_credit_column]):
                        temp_credit, _ = parse_credit_and_gpa(row[found_credit_column])
                        if temp_credit > 0 or normalize_text(row[found_credit_column]).lower() in ["通過", "抵免", "pass", "exempt"]:
                             extracted_credit = temp_credit

                    if found_gpa_column and found_gpa_column in row and pd.notna(row[found_gpa_column]):
                        _, temp_gpa = parse_credit_and_gpa(row[found_gpa_column])
                        if temp_gpa:
                            extracted_gpa = temp_gpa.upper()

                    if (extracted_credit == 0.0 and not extracted_gpa):
                        if found_credit_column and found_credit_column in row and pd.notna(row[found_credit_column]):
                            combined_val = normalize_text(row[found_credit_column])
                            temp_credit, temp_gpa = parse_credit_and_gpa(combined_val)
                            if temp_credit > 0:
                                extracted_credit = temp_credit
                            if temp_gpa and not extracted_gpa:
                                extracted_gpa = temp_gpa.upper()
                        
                        if (extracted_credit == 0.0 and not extracted_gpa) and found_gpa_column and found_gpa_column in row and pd.notna(row[found_gpa_column]):
                            combined_val = normalize_text(row[found_gpa_column])
                            temp_credit, temp_gpa = parse_credit_and_gpa(combined_val)
                            if temp_credit > 0:
                                extracted_credit = temp_credit
                            if temp_gpa and not extracted_gpa:
                                extracted_gpa = temp_gpa.upper()

                    if found_subject_column and found_subject_column in row and pd.notna(row[found_subject_column]):
                        temp_name = normalize_text(row[found_subject_column])
                        if len(temp_name) >= 2 and re.search(r'[\u4e00-\u9fa5]', temp_name) and \
                           not temp_name.isdigit() and not re.match(r'^[A-Fa-f][+\-]?$', temp_name) and \
                           not re.match(r'^\d+(\.\d+)?$', temp_name) and \
                           not re.match(r'^[A-Za-z0-9]{3,8}$', temp_name) and \
                           not temp_name.lower() in ["通過", "抵免", "pass", "exempt", "未知科目"] and \
                           not any(re.sub(r'[\s\n]+', '', kw.lower()) in re.sub(r'[\s\n]+', '', temp_name.lower()) for kw in all_header_keywords_flat_lower) and \
                           not any(re.search(pattern, temp_name) for pattern in ["學號", "本表", "註課組", "年級", "班級", "系別", "畢業門檻", "體育常識", "學號", "姓名", "班級", "系別"]):
                            course_name = temp_name
                        else:
                            st.info(f"科目名稱欄位 '{found_subject_column}' 內容 '{temp_name}' 不符合課程名稱模式。將嘗試相鄰欄位。")

                    if course_name == "未知科目" and found_course_code_column and found_course_code_column in row and pd.notna(row[found_course_code_column]):
                        current_col_idx = df.columns.get_loc(found_course_code_column)
                        if current_col_idx < len(df.columns) -1:
                            next_col_name = df.columns[current_col_idx + 1]
                            if next_col_name in row and pd.notna(row[next_col_name]):
                                temp_name_next = normalize_text(row[next_col_name])
                                if len(temp_name_next) >= 2 and re.search(r'[\u4e00-\u9fa5]', temp_name_next) and \
                                   not temp_name_next.isdigit() and not re.match(r'^[A-Fa-f][+\-]?$', temp_name_next) and \
                                   not re.match(r'^\d+(\.\d+)?$', temp_name_next) and \
                                   not re.match(r'^[A-Za-z0-9]{3,8}$', temp_name_next) and \
                                   not any(re.sub(r'[\s\n]+', '', kw.lower()) in re.sub(r'[\s\n]+', '', temp_name_next.lower()) for kw in all_header_keywords_flat_lower):
                                    course_name = temp_name_next
                                    st.info(f"從選課代號右側欄位 '{next_col_name}' 找到科目名稱: '{course_name}'")

                    is_failing_grade = False
                    if extracted_gpa:
                        gpa_clean = re.sub(r'[+\-]', '', extracted_gpa).upper()
                        if gpa_clean in [g.upper() for g in failing_grades] or \
                           (gpa_clean.replace('.', '', 1).isdigit() and float(gpa_clean) < 60):
                            is_failing_grade = True
                    is_passed_or_exempt_grade = False
                    if "通過" in extracted_gpa.lower() or "抵免" in extracted_gpa.lower() or \
                       "pass" in extracted_gpa.lower() or "exempt" in extracted_gpa.lower() or \
                       "通過" in normalize_text(row.to_string()).lower() or \
                       "抵免" in normalize_text(row.to_string()).lower():
                        is_passed_or_exempt_grade = True
                        if extracted_credit == 0.0:
                            extracted_credit = 0.0

                    st.info(f"解析結果: 學年='{acad_year}', 學期='{semester}', 選課代號='{course_code}', 科目名稱='{course_name}', 學分='{extracted_credit}', GPA='{extracted_gpa}', 是否不及格='{is_failing_grade}', 是否通過/抵免='{is_passed_or_exempt_grade}'")


                    if (course_name == "未知科目" or not acad_year or not semester) and extracted_credit == 0.0 and not extracted_gpa and not is_passed_or_exempt_grade:
                        st.info("該行沒有識別到有效的學年/學期/科目名稱/學分/成績，且非通過/抵免課程，已跳過。")
                        continue

                    if is_failing_grade:
                        failed_courses.append({
                            "學年度": acad_year,
                            "學期": semester,
                            "科目名稱": course_name,
                            "學分": extracted_credit,
                            "GPA": extracted_gpa,
                            "來源表格": df_idx + 1
                        })
                        st.warning(f"偵測到不及格科目: {course_name} (學分: {extracted_credit}, GPA: {extracted_gpa})，未計入總學分。")
                    elif extracted_credit > 0 or is_passed_or_exempt_grade:
                        total_credits += extracted_credit
                        course_gpa_value = 0.0
                        if extracted_gpa in grade_to_gpa:
                            course_gpa_value = grade_to_gpa[extracted_gpa]
                        elif extracted_gpa.replace('.', '', 1).isdigit(): # 如果是數字成績
                             try:
                                # 轉換百分制成績為 4.0 GPA 制 (範例：假設 60 及格，90+ 為 A)
                                score = float(extracted_gpa)
                                if score >= 90: course_gpa_value = 4.0
                                elif score >= 80: course_gpa_value = 3.0
                                elif score >= 70: course_gpa_value = 2.0
                                elif score >= 60: course_gpa_value = 1.0
                                else: course_gpa_value = 0.0
                             except ValueError:
                                 pass # 非數字，忽略

                        total_gpa_points += course_gpa_value * extracted_credit

                        calculated_courses.append({
                            "學年度": acad_year,
                            "學期": semester,
                            "科目名稱": course_name,
                            "學分": extracted_credit,
                            "GPA": extracted_gpa,
                            "來源表格": df_idx + 1
                        })
                        st.success(f"成功處理課程: {course_name} (學分: {extracted_credit}, GPA: {extracted_gpa})，學分已計入。")
                    else:
                        st.info(f"該行未計入學分，學分: {extracted_credit}, GPA: {extracted_gpa}, 是否通過/抵免: {is_passed_or_exempt_grade}")


            except Exception as e:
                st.error(f"表格 {df_idx + 1} 的學分計算時發生錯誤: `{e}`。該表格的學分可能無法計入總數。請檢查學分和GPA欄位數據是否正確。")
        else:
            st.warning(f"頁面 {df_idx + 1} 的表格未能識別為成績單表格 (缺少必要的 學年/學期/科目名稱/學分/GPA 欄位)。已偵測到的欄位: 學年='{found_year_column if found_year_column else '無'}', 學期='{found_semester_column if found_semester_column else '無'}', 選課代號='{found_course_code_column if found_course_code_column else '無'}', 科目名稱='{found_subject_column if found_subject_column else '無'}', 學分='{found_credit_column if found_credit_column else '無'}', GPA='{found_gpa_column if found_gpa_column else '無'}'")

    return total_credits, total_gpa_points, calculated_courses, failed_courses


def process_pdf_file_with_pdfplumber(uploaded_file):
    """
    使用 pdfplumber 處理上傳的 PDF 檔案，提取表格。
    返回提取的 DataFrames 列表和一個布林值表示是否成功提取到表格。
    """
    all_grades_data_dfs = []
    pdfplumber_success = False

    try:
        with pdfplumber.open(uploaded_file) as pdf:
            for page_num, page in enumerate(pdf.pages):
                current_page = page

                table_settings_1 = {
                    "vertical_strategy": "text", "horizontal_strategy": "text",
                    "snap_tolerance": 8, "join_tolerance": 8, "edge_min_length": 3,
                    "text_tolerance": 5, "min_words_vertical": 1, "min_words_horizontal": 1,
                }
                table_settings_2 = {
                    "vertical_strategy": "text", "horizontal_strategy": "text",
                    "snap_tolerance": 15, "join_tolerance": 15, "text_tolerance": 10,
                    "edge_min_length": 3, "min_words_vertical": 1, "min_words_horizontal": 1,
                }
                table_settings_3 = {
                    "vertical_strategy": "lines", "horizontal_strategy": "lines",
                    "snap_tolerance": 3, "join_tolerance": 3, "edge_min_length": 3,
                    "text_tolerance": 3, "min_words_vertical": 1, "min_words_horizontal": 1,
                }

                tables = []
                try:
                    tables = current_page.extract_tables(table_settings_1)
                    if tables: st.info(f"頁面 {page_num + 1} 使用設定1提取到表格。")
                except Exception as e: st.warning(f"頁面 {page_num + 1} 使用設定1提取表格失敗: {e}")

                if not tables:
                    try:
                        st.info(f"頁面 {page_num + 1} 未偵測到表格，嘗試使用更積極的文字設定...")
                        tables = current_page.extract_tables(table_settings_2)
                        if tables: st.info(f"頁面 {page_num + 1} 使用設定2提取到表格。")
                    except Exception as e: st.warning(f"頁面 {page_num + 1} 使用設定2提取表格失敗: {e}")

                if not tables:
                    try:
                        st.info(f"頁面 {page_num + 1} 仍未偵測到表格，嘗試使用基於線條的設定...")
                        tables = current_page.extract_tables(table_settings_3)
                        if tables: st.info(f"頁面 {page_num + 1} 使用設定3提取到表格。")
                    except Exception as e: st.warning(f"頁面 {page_num + 1} 使用設定3提取表格失敗: {e}")

                if not tables:
                    st.warning(f"頁面 **{page_num + 1}** 未偵測到表格 (pdfplumber)。這可能是由於 PDF 格式複雜或表格提取設定不適用。")
                    continue

                for table_idx, table in enumerate(tables):
                    processed_table = []
                    for row in table:
                        normalized_row = [normalize_text(cell) for cell in row]
                        if any(cell.strip() != "" for cell in normalized_row):
                            processed_table.append(normalized_row)

                    if not processed_table:
                        st.info(f"頁面 {page_num + 1} 的表格 **{table_idx + 1}** 提取後為空或全為空白行。")
                        continue

                    df_table_to_add = None

                    if len(processed_table) > 1:
                        potential_header_row = processed_table[0]
                        max_cols_temp = max(len(cell_list) for cell_list in processed_table)
                        padded_header_row = potential_header_row + [''] * (max_cols_temp - len(potential_header_row))
                        temp_unique_columns = make_unique_columns(padded_header_row)

                        header_keyword_count = sum(1 for cell in padded_header_row if normalize_text(cell).lower().replace(' ', '').replace('\n', '') in all_header_keywords_flat_lower)
                        
                        if header_keyword_count >= 3:
                            temp_data_rows = processed_table[1:]
                            num_cols_for_df = len(temp_unique_columns)
                            cleaned_temp_data_rows = []
                            for row_data in temp_data_rows:
                                if len(row_data) > num_cols_for_df:
                                    cleaned_temp_data_rows.append(row_data[:num_cols_for_df])
                                elif len(row_data) < num_cols_for_df:
                                    cleaned_temp_data_rows.append(row_data + [''] * (num_cols_for_df - len(row_data)))
                                else:
                                    cleaned_temp_data_rows.append(row_data)

                            if cleaned_temp_data_rows:
                                try:
                                    df_table_with_assumed_header = pd.DataFrame(cleaned_temp_data_rows, columns=temp_unique_columns)
                                    if is_grades_table(df_table_with_assumed_header):
                                        df_table_to_add = df_table_with_assumed_header
                                        st.success(f"頁面 {page_num + 1} 的表格 {table_idx + 1} 已識別為成績單表格 (帶有偵測到的標頭)。")
                                except Exception as e_df_temp:
                                    st.warning(f"頁面 {page_num + 1} 表格 {table_idx + 1} 嘗試用第一行作標頭轉換為 DataFrame 時發生錯誤: `{e_df_temp}`。")
                            else:
                                st.info(f"頁面 {page_num + 1} 的表格 {table_idx + 1} 第一行被識別為標頭但無數據行。")

                    if df_table_to_add is None:
                        max_cols = max(len(row_data) for row_data in processed_table)
                        generic_columns = make_unique_columns([f"Column_{i+1}" for i in range(max_cols)])

                        cleaned_all_rows_data = []
                        for row_data in processed_table:
                            if len(row_data) > max_cols:
                                cleaned_all_rows_data.append(row_data[:max_cols])
                            elif len(row_data) < max_cols:
                                cleaned_all_rows_data.append(row_data + [''] * (max_cols - len(row_data)))
                            else:
                                cleaned_all_rows_data.append(row_data)

                        if cleaned_all_rows_data:
                            try:
                                df_table_all_data = pd.DataFrame(cleaned_all_rows_data, columns=generic_columns)
                                if is_grades_table(df_table_all_data):
                                    df_table_to_add = df_table_all_data
                                    st.success(f"頁面 {page_num + 1} 的表格 {table_idx + 1} 已識別為成績單表格 (所有行皆為數據，使用通用標頭)。")
                                else:
                                    st.info(f"頁面 {page_num + 1} 的表格 {table_idx + 1} 未能識別為成績單表格，已跳過。")
                            except Exception as e_df_all:
                                st.error(f"頁面 {page_num + 1} 表格 {table_idx + 1} 嘗試用所有行作數據轉換為 DataFrame 時發生錯誤: `{e_df_all}`")
                        else:
                            st.info(f"頁面 {page_num + 1} 的表格 **{table_idx + 1}** 沒有有效數據行。")

                    if df_table_to_add is not None:
                        all_grades_data_dfs.append(df_table_to_add)
                        pdfplumber_success = True

    except pdfplumber.PDFSyntaxError as e_pdf_syntax:
        st.error(f"處理 PDF 語法時發生錯誤: `{e_pdf_syntax}`。檔案可能已損壞或格式不正確，將嘗試 OCR。")
        pdfplumber_success = False # 即使報錯，也標記為 pdfplumber 失敗
    except Exception as e:
        st.error(f"處理 PDF 檔案時發生一般錯誤: `{e}`，將嘗試 OCR。")
        st.error("請確認您的 PDF 格式是否為清晰的表格。")
        pdfplumber_success = False

    return all_grades_data_dfs, pdfplumber_success

def process_pdf_file_with_ocr(uploaded_file):
    """
    使用 OCR (img2table + Tesseract) 處理圖片 PDF 檔案，提取表格。
    """
    all_grades_data_dfs = []
    
    st.info("啟用 OCR (img2table + Tesseract) 提取圖片 PDF 中的表格。這可能需要一些時間。")
    try:
        # 初始化 Tesseract OCR
        # 請確保 Tesseract OCR 已經安裝並在系統 PATH 中
        # 如果 Tesseract 不在 PATH 中，你需要指定其安裝路徑，例如:
        # tesseract_cmd_path = r'C:\Program Files\Tesseract-OCR\tesseract.exe' # Windows 範例
        # ocr = TesseractOCR(lang="chi_tra", tesseract_cmd=tesseract_cmd_path) 
        # 這裡假設 Tesseract 已在 PATH 中，並使用繁體中文語言包
        ocr = TesseractOCR(lang="chi_tra") 

        # 使用 img2table 提取 PDF 中的表格
        pdf_bytes = io.BytesIO(uploaded_file.getvalue())
        img_pdf = Img2TablePDF(src=pdf_bytes, detect_rotation=True)
        tables_img2table = img_pdf.extract_tables(ocr=ocr)

        if tables_img2table:
            st.success(f"使用 OCR (img2table) 成功從 {len(tables_img2table)} 個表格中提取數據！")
            
            for table in tables_img2table:
                # img2table 的 table.content 是一個 pandas DataFrame
                df_ocr = table.content.copy()
                # 再次使用 normalize_text 處理 OCR 結果，確保一致性
                for col in df_ocr.columns:
                    df_ocr[col] = df_ocr[col].apply(normalize_text)
                
                # OCR 結果的欄位名稱可能不規範，需要嘗試重新匹配
                # 這裡假設 OCR 辨識出的欄位順序大致不變
                if not df_ocr.empty and is_grades_table(df_ocr):
                    # 重新映射欄位名稱
                    mapped_df = pd.DataFrame()
                    col_map_flexible_ocr = {
                        '學年度': ['學年度', '學年', 'Year'],
                        '學期': ['學期', 'Semester'],
                        '選課代號': ['選課代號', '課程代碼', 'Course Code'],
                        '科目名稱': ['科目名稱', '課程名稱', 'Subject Name', 'Course Name'],
                        '學分': ['學分', '學分數', 'Credits', 'Credit'],
                        'GPA': ['GPA', '成績', 'Grade']
                    }
                    
                    for target_col, possible_names in col_map_flexible_ocr.items():
                        found_col = None
                        for name_variant in possible_names:
                            if name_variant in df_ocr.columns:
                                found_col = name_variant
                                break
                            # 嘗試標準化 df_ocr 的列名後再匹配
                            for df_col in df_ocr.columns:
                                if normalize_text(df_col).lower().replace(' ', '').replace('\n', '') == normalize_text(name_variant).lower().replace(' ', '').replace('\n', ''):
                                    found_col = df_col
                                    break
                            if found_col: break

                        if found_col:
                            mapped_df[target_col] = df_ocr[found_col]
                        else:
                            # 如果沒有找到，添加一個空列，避免 KeyError
                            mapped_df[target_col] = "" 
                            
                    all_grades_data_dfs.append(mapped_df)
                else:
                    st.warning(f"OCR 提取到的表格 {len(all_grades_data_dfs) + 1} 未能識別為成績單表格。")

        else:
            st.error("OCR (img2table) 未能從 PDF 中提取到任何表格數據。請檢查 PDF 內容是否為清晰的表格圖片。")

    except Exception as e:
        st.error(f"OCR 處理檔案時發生錯誤：{e}。請確認您的 Tesseract OCR 安裝正確，並已安裝繁體中文語言包。")
        st.info("錯誤提示：如果遇到 'tesseract is not installed or not in your PATH' 錯誤，請確保您已安裝 Tesseract OCR 並將其添加到系統環境變數 PATH 中。")

    return all_grades_data_dfs


# --- Streamlit 應用主體 ---
def main():
    st.set_page_config(page_title="PDF 成績單學分計算工具", layout="wide")
    st.title("📄 PDF 成績單學分計算工具")

    st.write("請上傳您的 PDF 成績單檔案，工具將嘗試提取其中的表格數據並計算總學分。")
    st.write("您也可以輸入目標學分，查看還差多少學分。")

    uploaded_file = st.file_uploader("選擇一個 PDF 檔案", type="pdf")

    if uploaded_file is not None:
        st.success(f"已上傳檔案: **{uploaded_file.name}**")
        
        extracted_dfs = []
        pdfplumber_extracted_successfully = False

        with st.spinner("正在嘗試使用 pdfplumber 處理 PDF..."):
            extracted_dfs, pdfplumber_extracted_successfully = process_pdf_file_with_pdfplumber(uploaded_file)

        if not pdfplumber_extracted_successfully or not extracted_dfs:
            st.warning("pdfplumber 未能成功提取表格，可能是圖片 PDF 或表格結構複雜。嘗試使用 OCR 進行圖片分析...")
            with st.spinner("正在使用 OCR 處理 PDF (這可能需要更長的時間)..."):
                extracted_dfs = process_pdf_file_with_ocr(uploaded_file)

        if extracted_dfs:
            st.markdown("---")
            st.markdown("## ⚙️ 偵錯資訊 (Debug Info)")
            st.info("以下是程式碼處理每行數據的詳細過程，幫助您理解學分計算和課程識別的狀況。")
            st.info("您上傳的原始表格內容將會顯示，以及程式碼如何解析各個欄位。")
            st.info("如果您發現有誤，請根據這些資訊告知我具體是哪個表格的哪一行、哪個欄位有問題。")
            st.info("--- 偵錯訊息結束 ---")

            total_credits, total_gpa_points, calculated_courses, failed_courses = calculate_total_credits(extracted_dfs)

            st.markdown("---")
            st.markdown("## ✅ 查詢結果")
            st.markdown(f"目前總學分: <span style='color:green; font-size: 24px;'>**{total_credits:.2f}**</span>", unsafe_allow_html=True)

            if total_credits > 0:
                average_gpa = total_gpa_points / total_credits
                st.markdown(f"目前平均 GPA: <span style='color:blue; font-size: 20px;'>**{average_gpa:.2f}**</span>", unsafe_allow_html=True)
            else:
                st.info("沒有足夠的及格學分來計算平均 GPA。")

            target_credits = st.number_input("輸入您的目標學分 (例如：128)", min_value=0.0, value=128.0, step=1.0,
                                            help="您可以設定一個畢業學分目標，工具會幫您計算還差多少學分。")

            credit_difference = target_credits - total_credits
            if credit_difference > 0:
                st.write(f"距離畢業所需學分 (共{target_credits:.0f}學分) 還差 **{credit_difference:.2f}**")
            elif credit_difference < 0:
                st.write(f"已超越畢業學分 (共{target_credits:.0f}學分) **{abs(credit_difference):.2f}**")
            else:
                st.write(f"已達到畢業所需學分 (共{target_credits:.0f}學分) **0.00**")


            st.markdown("---")
            st.markdown("### 📚 通過的課程列表")
            if calculated_courses:
                courses_df = pd.DataFrame(calculated_courses)
                display_cols = ['學年度', '學期', '科目名稱', '學分', 'GPA']
                # 如果有選課代號，也顯示
                if '選課代號' in courses_df.columns:
                    display_cols.insert(2, '選課代號')

                final_display_cols = [col for col in display_cols if col in courses_df.columns]
                st.dataframe(courses_df[final_display_cols], height=300, use_container_width=True)
            else:
                st.info("沒有找到可以計算學分的科目。")

            if failed_courses:
                st.markdown("---")
                st.markdown("### ⚠️ 不及格的課程列表")
                failed_df = pd.DataFrame(failed_courses)
                display_failed_cols = ['學年度', '學期', '科目名稱', '學分', 'GPA', '來源表格']
                # 如果有選課代號，也顯示
                if '選課代號' in failed_df.columns:
                    display_failed_cols.insert(2, '選課代號')

                final_display_failed_cols = [col for col in display_failed_cols if col in failed_df.columns]
                st.dataframe(failed_df[final_display_failed_cols], height=200, use_container_width=True)
                st.info("這些科目因成績不及格 ('D', 'E', 'F' 等) 而未計入總學分。")

            if calculated_courses or failed_courses:
                if calculated_courses:
                    courses_df_export = pd.DataFrame(calculated_courses)
                    csv_data_passed = courses_df_export.to_csv(index=False, encoding='utf-8-sig')
                    st.download_button(
                        label="下載通過的科目列表為 CSV",
                        data=csv_data_passed,
                        file_name=f"{uploaded_file.name.replace('.pdf', '')}_calculated_courses.csv",
                        mime="text/csv",
                        key="download_passed_btn"
                    )
                if failed_courses:
                    failed_df_export = pd.DataFrame(failed_courses)
                    csv_data_failed = failed_df_export.to_csv(index=False, encoding='utf-8-sig')
                    st.download_button(
                        label="下載不及格的科目列表為 CSV",
                        data=csv_data_failed,
                        file_name=f"{uploaded_file.name.replace('.pdf', '')}_failed_courses.csv",
                        mime="text/csv",
                        key="download_failed_btn"
                    )

        else:
            st.warning("未從 PDF 中提取到任何表格數據。請檢查 PDF 內容或嘗試上傳其他檔案。")
    else:
        st.info("請上傳 PDF 檔案以開始處理。")

if __name__ == "__main__":
    main()
