def process_pdf_file(uploaded_file):
    """
    使用 pdfplumber 處理上傳的 PDF 檔案，提取表格。
    此函數內部將減少 Streamlit 的直接輸出，只返回提取的數據。
    """
    all_grades_data = []

    try:
        with pdfplumber.open(uploaded_file) as pdf:
            for page_num, page in enumerate(pdf.pages):
                current_page = page 

                # 調整策略：使用 'text' 策略，並進一步調整 text_tolerance, snap_tolerance, join_tolerance
                # 這些值是為了更好地處理手機掃描或生成的不規則 PDF 表格
                table_settings = {
                    "vertical_strategy": "text", 
                    "horizontal_strategy": "text", 
                    "snap_tolerance": 8,  # 適度增大，允許文字與線條間隔更大
                    "join_tolerance": 8,  # 適度增大，允許線條斷裂更長
                    "edge_min_length": 3, 
                    "text_tolerance": 5,  # 適度增大，允許文字對齊偏差更大
                    "min_words_vertical": 1, 
                    "min_words_horizontal": 1, 
                }
                
                try:
                    # 首先嘗試使用更寬鬆的 'text' 策略，參數也更平衡
                    tables = current_page.extract_tables(table_settings)

                    # 如果沒有偵測到表格，嘗試更積極的設定，例如不考慮線條，純粹基於文字間距
                    if not tables:
                        st.info(f"頁面 **{page_num + 1}** 未偵測到表格，嘗試使用更積極的設定...")
                        aggressive_table_settings = {
                            "vertical_strategy": "lines", # 嘗試lines，但降低容忍度
                            "horizontal_strategy": "lines",
                            "snap_tolerance": 3,
                            "join_tolerance": 3,
                            "edge_min_length": 3,
                            "text_tolerance": 3,
                            "min_words_vertical": 1,
                            "min_words_horizontal": 1,
                        }
                        tables = current_page.extract_tables(aggressive_table_settings)

                    if not tables:
                        st.info(f"頁面 **{page_num + 1}** 仍未偵測到表格。這可能是由於 PDF 格式複雜或表格提取設定不適用。")
                        continue

                    for table_idx, table in enumerate(tables):
                        processed_table = []
                        for row in table:
                            normalized_row = [normalize_text(cell) for cell in row]
                            # Filter out rows that are entirely empty after normalization
                            if any(cell.strip() != "" for cell in normalized_row):
                                processed_table.append(normalized_row)
                        
                        if not processed_table:
                            st.info(f"頁面 {page_num + 1} 的表格 **{table_idx + 1}** 提取後為空或全為空白行。")
                            continue
                        
                        df_table_to_add = None

                        # --- Attempt 1: Assume first row is header if it strongly looks like one ---
                        if len(processed_table) > 1:
                            potential_header_row = processed_table[0]
                            # Create a temporary DataFrame to test if this looks like a grades table header
                            # Use generic columns for the temp df, then check if its first row (which is the actual potential header) contains keywords
                            max_cols_temp = max(len(cell_list) for cell_list in processed_table) # Get max columns from all processed rows
                            temp_generic_columns = make_unique_columns([f"TempCol_{i+1}" for i in range(max_cols_temp)])
                            
                            # Ensure potential_header_row has enough elements to match temp_generic_columns
                            padded_header_row = potential_header_row + [''] * (max_cols_temp - len(potential_header_row))

                            temp_df_for_header_check = pd.DataFrame([padded_header_row], columns=temp_generic_columns)
                            
                            # Check if temp_df_for_header_check's *content* (i.e., the first row) contains header keywords
                            # Normalizing columns for keyword search
                            temp_normalized_header_content = {re.sub(r'\s+', '', cell).lower(): cell for cell in padded_header_row}
                            
                            has_credit_kw = any(any(k in cell for k in credit_column_keywords) for cell in temp_normalized_header_content.keys())
                            has_gpa_kw = any(any(k in cell for k in gpa_column_keywords) for cell in temp_normalized_header_content.keys()) 
                            has_subject_kw = any(any(k in cell for k in subject_column_keywords) for cell in temp_normalized_header_content.keys())
                            has_year_kw = any(any(k in cell for k in year_column_keywords) for cell in temp_normalized_header_content.keys())
                            has_semester_kw = any(any(k in cell for k in semester_column_keywords) for cell in temp_normalized_header_content.keys())

                            if has_subject_kw and (has_credit_kw or has_gpa_kw) and has_year_kw and has_semester_kw: # Strong header match
                                temp_unique_columns = make_unique_columns(potential_header_row)
                                temp_data_rows = processed_table[1:]

                                num_cols_for_df = len(temp_unique_columns)
                                cleaned_temp_data_rows = []
                                for row in temp_data_rows:
                                    if len(row) > num_cols_for_df:
                                        cleaned_temp_data_rows.append(row[:num_cols_for_df])
                                    elif len(row) < num_cols_for_df: 
                                        cleaned_temp_data_rows.append(row + [''] * (num_cols_for_df - len(row)))
                                    else:
                                        cleaned_temp_data_rows.append(row)

                                if cleaned_temp_data_rows:
                                    try:
                                        df_table_with_assumed_header = pd.DataFrame(cleaned_temp_data_rows, columns=temp_unique_columns)
                                        if is_grades_table(df_table_with_assumed_header):
                                            df_table_to_add = df_table_with_assumed_header
                                            st.success(f"頁面 {page_num + 1} 的表格 {table_idx + 1} 已識別為成績單表格 (帶有偵測到的標頭)。")
                                    except Exception as e_df_temp:
                                        st.warning(f"頁面 {page_num + 1} 表格 {table_idx + 1} 嘗試用第一行作標頭轉換為 DataFrame 時發生錯誤: `{e_df_temp}`。將嘗試將所有行作為數據。")
                                else:
                                    st.info(f"頁面 {page_num + 1} 的表格 {table_idx + 1} 第一行被識別為標頭但無數據行，將嘗試將所有行作為數據。")
                        
                        # --- Attempt 2: Treat all rows as data (generic columns) if Attempt 1 failed or was not applicable ---
                        if df_table_to_add is None:
                            max_cols = max(len(row) for row in processed_table)
                            generic_columns = make_unique_columns([f"Column_{i+1}" for i in range(max_cols)])

                            cleaned_all_rows_data = []
                            for row in processed_table:
                                if len(row) > max_cols:
                                    cleaned_all_rows_data.append(row[:max_cols])
                                elif len(row) < max_cols:
                                    cleaned_all_rows_data.append(row + [''] * (max_cols - len(row)))
                                else:
                                    cleaned_all_rows_data.append(row)
                            
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
                            all_grades_data.append(df_table_to_add)

                except Exception as e_table:
                    st.error(f"頁面 **{page_num + 1}** 處理表格時發生錯誤: `{e_table}`")
                    st.warning("這可能是由於 PDF 格式複雜或表格提取設定不適用。請檢查 PDF 結構。")

    except pdfplumber.PDFSyntaxError as e_pdf_syntax:
        st.error(f"處理 PDF 語法時發生錯誤: `{e_pdf_syntax}`。檔案可能已損壞或格式不正確。")
    except Exception as e:
        st.error(f"處理 PDF 檔案時發生一般錯誤: `{e}`")
        st.error("請確認您的 PDF 格式是否為清晰的表格。若問題持續，可能是 PDF 結構較為複雜，需要調整 `pdfplumber` 的表格提取設定。")

    return all_grades_data
