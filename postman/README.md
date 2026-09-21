# Postman-коллекция GigaAM v3 API

`GigaAM_API.postman_collection.json` — коллекция Postman v2.1 для REST API,
совместимого с OpenAI Audio API. Справочник по эндпоинтам — `docs/API.md`.

1. **Импорт.** Postman → File → Import → выберите этот JSON.
2. **Переменные.** Откройте коллекцию → вкладка Variables: `baseUrl`
   (по умолчанию `http://127.0.0.1:8000`) и `apiKey` — ключ, который
   `python api.py` печатает при первом запуске (`gam_...`). Авторизация
   `Bearer {{apiKey}}` задана на уровне коллекции; `Health` ключа не требует.
3. **Запуск.** В запросах `Transcribe *` выберите файл в поле `file`
   (Body → form-data) и нажмите Send. `Transcribe stream` отдаёт SSE — смотрите
   тело ответа целиком. `Transcribe diarized_json` с `pyannote` требует
   `HF_TOKEN` на сервере; поменяйте `diarization_backend` на `sortformer`
   или `onnx`, если токена нет. `Error: *` показывают конверт ошибок OpenAI.
