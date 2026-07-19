# ===== GigaAM v3 Transcriber - Web GUI =====
# CUDA-образ с поддержкой GPU ускорения

FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04

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
    libsndfile1 \
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

# Официальная согласованная тройка cu124 ставится ДО остальных зависимостей:
# бинарные расширения torchaudio и torchvision нельзя смешивать с другим
# релизом torch. И gigaam (torch>=2.5,<2.9), и requirements.txt
# (torch>=2.6.0,<2.9.0) принимают 2.6.0, поэтому pip оставит её как есть.
# Установка после них означала бы скачивание torch дважды (~1.6 ГБ впустую).
# Слой идёт до COPY requirements.txt, чтобы правка requirements его не сбрасывала.
RUN pip install --no-cache-dir \
    torch==2.6.0 torchaudio==2.6.0 torchvision==0.21.0 \
    --index-url https://download.pytorch.org/whl/cu124

# Копирование requirements и установка зависимостей
COPY requirements.txt requirements-sortformer.txt /app/
# Установка gigaam из git (без build isolation, чтобы работал pkg_resources)
RUN git clone https://github.com/salute-developers/GigaAM.git /tmp/gigaam && \
    cd /tmp/gigaam && git checkout 0a3f1036d93287d5ef226911ec795bde8ef05d57 && \
    pip install --no-cache-dir --no-build-isolation . && \
    rm -rf /tmp/gigaam
# Установка остальных зависимостей (без gigaam, torchcodec и PyQt6 —
# PyQt6 это ~100МБ GUI-тулкита, который в headless-контейнере не импортируется
# ни одним модулем web-слоя, только раздувает образ).
RUN grep -viE 'gigaam|torchcodec|pyqt6|^onnxruntime==' requirements.txt > /tmp/req.txt && \
    pip install --no-cache-dir -r /tmp/req.txt
# В одном окружении должен быть ровно один ORT-дистрибутив. Фильтра по
# requirements недостаточно: gigaam жёстко требует onnxruntime==1.23.*, поэтому
# CPU-колесо приезжает вместе с ним и его надо снять до установки GPU-варианта —
# иначе оба дистрибутива делят каталог onnxruntime/ и любая последующая
# переустановка молча возвращает CPU-провайдер.
RUN pip uninstall -y onnxruntime || true
RUN pip install --no-cache-dir onnxruntime-gpu==1.23.2
RUN python -c "from importlib.metadata import distributions; names = {d.metadata['Name'].lower() for d in distributions()}; assert 'onnxruntime-gpu' in names and 'onnxruntime' not in names, sorted(n for n in names if n.startswith('onnxruntime'))"
# Дополнительные зависимости для Web GUI
RUN pip install --no-cache-dir itsdangerous python-multipart

# NeMo значительно увеличивает образ, поэтому Sortformer включается явно:
# docker compose build --build-arg INSTALL_SORTFORMER=1 gigaam-web
ARG INSTALL_SORTFORMER=0
RUN if [ "$INSTALL_SORTFORMER" = "1" ]; then \
        pip install --no-cache-dir -r requirements-sortformer.txt; \
    fi

# Финальный контракт окружения. Раньше согласованная тройка torch навязывалась
# принудительной переустановкой в конце; теперь она ставится первой, поэтому
# любой последующий шаг (gigaam, requirements, NeMo) обязан её сохранить —
# проверяем это явно, чтобы молчаливая подмена не уехала в образ.
RUN python -c "import torch, torchaudio, torchvision; from importlib.metadata import distributions; \
names = {d.metadata['Name'].lower() for d in distributions()}; \
assert torch.__version__ == '2.6.0+cu124', torch.__version__; \
assert torchaudio.__version__ == '2.6.0+cu124', torchaudio.__version__; \
assert torchvision.__version__ == '0.21.0+cu124', torchvision.__version__; \
assert 'onnxruntime-gpu' in names and 'onnxruntime' not in names, sorted(n for n in names if n.startswith('onnxruntime'))"

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
