# 使用 Streamlit 官方提供的 Python 基礎映像
FROM python:3.10-slim-buster

# 設定工作目錄
WORKDIR /app

# 安裝 Tesseract OCR 引擎及其繁體中文語言包
# 這是在 Debian/Ubuntu 系統上安裝 Tesseract 的命令
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    tesseract-ocr-chi-tra \
    libgl1-mesa-glx \ # img2table/opencv-python 可能需要的 OpenGL 庫
    && rm -rf /var/lib/apt/lists/*

# 安裝其他系統依賴，尤其是 OpenCV 可能需要的一些通用庫
# (儘管 python:3.10-slim-buster 已經包含大部分，但這些是常見的額外需求)
RUN apt-get update && apt-get install -y \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    libfontconfig1 \
    && rm -rf /var/lib/apt/lists/*

# 複製您的 requirements.txt 並安裝 Python 依賴
COPY requirements.txt .
RUN pip install -r requirements.txt

# 複製您的 Streamlit 應用程式檔案
COPY . .

# 設定 Streamlit 的環境變數，告知 Tesseract 語言包的位置
# Tesseract 預設會尋找 /usr/share/tesseract-ocr/4.00/tessdata (或類似路徑)
# 這個設定確保 img2table/pytesseract 能夠找到語言包
ENV TESSDATA_PREFIX=/usr/share/tesseract-ocr/4.00/tessdata

# 如果 Tesseract 在 Docker 容器中的路徑不是默認的路徑，可能需要設定
# ENV PATH="/usr/bin:${PATH}" # 通常不需要，因為會自動添加到 PATH

# 啟動 Streamlit 應用程式
EXPOSE 8501
CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.enableCORS=false"]
