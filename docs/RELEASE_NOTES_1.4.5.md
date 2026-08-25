# GigaAM Transcriber 1.4.5

## Русский

### Безопасность

- Обновлены пакеты OpenTelemetry (opentelemetry-api/sdk/exporter-otlp*,
  opentelemetry-proto) с 1.34.0 до 1.44.0 и
  opentelemetry-semantic-conventions с 0.55b0 до 0.65b0 — устранены
  уязвимости (1 high / 3 medium), обнаруженные сканером зависимостей в
  opentelemetry-exporter-otlp-proto-grpc 1.34.0.
- Пакеты OpenTelemetry не импортируются кодом приложения напрямую и
  обновление не меняет функциональность; все остальные зависимости не
  затронуты.

## English

### Security

- Updated the OpenTelemetry packages (opentelemetry-api/sdk/exporter-otlp*,
  opentelemetry-proto) from 1.34.0 to 1.44.0 and
  opentelemetry-semantic-conventions from 0.55b0 to 0.65b0 — resolves the
  vulnerabilities (1 high / 3 medium) reported by the dependency scanner for
  opentelemetry-exporter-otlp-proto-grpc 1.34.0.
- The application code does not import OpenTelemetry directly, so the update
  has no functional impact; all other dependencies are unchanged.
