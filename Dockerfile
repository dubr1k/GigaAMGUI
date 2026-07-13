# ===== GigaAM v3 Transcriber - Web GUI =====
# CUDA-образ с поддержкой GPU ускорения

FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV HF_HOME=/models/huggingface
ENV HF_HUB_DISABLE_SYMLINKS_WARNING=1

# Установка системных пакетов
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.10 \
    python3.10-venv \
    python3.10-dev \
    python3-pip \
    ffmpeg \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Создание симлинков
RUN ln -sf /usr/bin/python3.10 /usr/bin/python && \
    ln -sf /usr/bin/python3.10 /usr/bin/python3

# Установка pip
RUN python -m pip install --upgrade pip "setuptools<81" wheel

# Рабочая директория
WORKDIR /app

# Копирование requirements и установка зависимостей
COPY requirements.txt /app/requirements.txt
# Установка gigaam из git (без build isolation, чтобы работал pkg_resources)
RUN git clone https://github.com/salute-developers/GigaAM.git /tmp/gigaam && \
    cd /tmp/gigaam && git checkout 0a3f1036d93287d5ef226911ec795bde8ef05d57 && \
    pip install --no-cache-dir --no-build-isolation . && \
    rm -rf /tmp/gigaam
# Установка остальных зависимостей (без gigaam, torchcodec и PyQt6 —
# PyQt6 это ~100МБ GUI-тулкита, который в headless-контейнере не импортируется
# ни одним модулем web-слоя, только раздувает образ).
RUN grep -viE 'gigaam|torchcodec|pyqt6' requirements.txt > /tmp/req.txt && \
    pip install --no-cache-dir -r /tmp/req.txt
# Дополнительные зависимости для Web GUI
RUN pip install --no-cache-dir itsdangerous python-multipart
# Фиксируем официальную согласованную тройку: бинарные расширения torchaudio и
# torchvision нельзя смешивать с другим релизом torch.
RUN pip install --no-cache-dir --force-reinstall \
    torch==2.6.0 torchaudio==2.6.0 torchvision==0.21.0 \
    --index-url https://download.pytorch.org/whl/cu124

# Копирование исходного кода
COPY . /app/

# Создание директорий
RUN useradd --create-home --uid 1000 --shell /usr/sbin/nologin gigaam && \
    mkdir -p /app/uploads /app/results /app/logs /models/huggingface && \
    chown -R gigaam:gigaam /app/uploads /app/results /app/logs /models/huggingface

# Экспозиция порта
EXPOSE 8000

USER gigaam

# Запуск Web GUI
CMD ["python", "-m", "uvicorn", "web.web_app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
