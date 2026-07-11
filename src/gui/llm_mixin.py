"""LLM-обработка GUI, вынесенная из GigaTranscriberQtApp.

Mixin: методы работают со `self` главного окна (виджеты создаются в app_qt).
Поведение сохранено 1:1 — методы перенесены без изменений.
"""
from __future__ import annotations

import os

from PyQt6.QtWidgets import QFileDialog, QMessageBox

from ..services import llm_service


class LlmMixin:
    def _build_llm_prompt_text(self, transcript_text: str, prompt: str) -> str:
        return llm_service.build_prompt_text(transcript_text, prompt)

    def _run_llm_provider(self, llm_settings: dict, transcript_text: str, prompt: str) -> str:
        raw = llm_settings.get("provider", "API")
        provider = "Other" if self._normalize_llm_provider(raw) == "Other" else raw
        try:
            return llm_service.run_provider(
                llm_settings, transcript_text, prompt,
                provider=provider, strict_empty_cli=True,
            )
        except llm_service.UnknownLLMProvider as exc:
            raise RuntimeError(self._t(
                f"Неизвестный LLM-провайдер: {exc.provider}",
                f"Unknown LLM provider: {exc.provider}",
            )) from exc

    def _run_llm_processing(self, llm_settings: dict, items: list, modes: list, export_formats: list):
        try:
            results = []
            total = len(items)
            last_name = "llm_result"
            provider = llm_settings.get("provider", "API")
            for item_index, item in enumerate(items, start=1):
                name = item["name"]
                last_name = name
                item_blocks = []
                for mode_suffix, mode_label, prompt in modes:
                    self.log(f"LLM: обработка {item_index}/{total} — {name} — {mode_label} — {provider}")
                    answer = self._run_llm_provider(llm_settings, item["text"], prompt)
                    saved_paths = self._save_llm_result(item, answer, mode_suffix, export_formats)
                    block = f"=== {name} / {mode_label} / {provider} ===\n{answer}"
                    if saved_paths:
                        block += "\n\nСохранено:\n" + "\n".join(saved_paths)
                    item_blocks.append(block)
                results.append("\n\n".join(item_blocks))
            mode_suffixes = "_".join(mode[0] for mode in modes)
            self.llm_last_result_name = f"{last_name}_llm_{mode_suffixes}"
            final_text = "\n\n".join(results)
            self.signals.llm_finished.emit(True, f"LLM-обработка завершена: {total} файл(ов)", final_text)
        except Exception as e:
            error_text = str(e).strip() or "Неизвестная ошибка"
            self.signals.llm_finished.emit(False, f"Ошибка LLM: {self._compact_llm_error(error_text)}", error_text)

    def _compact_llm_error(self, error_text: str, limit: int = 180) -> str:
        raw_text = (error_text or "").strip()
        lowered = raw_text.lower()

        friendly_rules = [
            (("refresh token was revoked", "please log out and sign in again"), "Codex: сессия истекла или токен отозван — нужно заново войти в Codex"),
            (("token_invalidated", "authentication token has been invalidated"), "Codex: токен недействителен — перелогиньтесь"),
            (("your session has ended", "refresh_token_invalidated"), "Codex: сессия завершилась — выполните codex logout и codex login"),
            (("connection refused", "127.0.0.1", "/v1/responses"), "Codex: локальный backend недоступен — проверьте, запущен ли нужный сервер/провайдер"),
            (("failed to refresh available models", "missing field `base_instructions`"), "Codex: сервер моделей отдает несовместимый формат ответа — провайдер/прокси не полностью совместим с Codex"),
            (("failed to decode models response",), "Codex: провайдер вернул неожиданный формат списка моделей"),
            (("401", "anthropic"), "Anthropic API: ошибка авторизации (401) — проверьте API key"),
            (("401", "openai"), "OpenAI-compatible API: ошибка авторизации (401) — проверьте API key"),
            (("401", "unauthorized"), "Ошибка авторизации (401) — проверьте ключ, токен или логин выбранного провайдера"),
            (("403", "forbidden"), "Доступ запрещен (403) — у аккаунта или ключа не хватает прав"),
            (("404",), "Endpoint не найден (404) — проверьте URL API и путь /v1/..."),
            (("429", "rate"), "Превышен лимит запросов (429) — попробуйте позже или смените тариф/провайдера"),
            (("insufficient_quota",), "Закончилась квота API — проверьте биллинг или лимиты"),
            (("model_not_found",), "Указанная модель не найдена — проверьте точное имя модели"),
            (("does not exist", "model"), "Указанная модель не существует у выбранного провайдера"),
            (("invalid x-api-key",), "Неверный Anthropic API key"),
            (("incorrect api key",), "Неверный API key"),
            (("could not resolve host",), "Не удалось найти хост — проверьте URL и интернет-соединение"),
            (("name or service not known",), "Не удалось найти сервер — проверьте адрес API"),
            (("max retries exceeded",), "Не удалось подключиться к API после нескольких попыток"),
            (("read timed out", "timeout"), "Сервер слишком долго отвечает — попробуйте позже или увеличьте timeout"),
            (("connection timed out",), "Таймаут соединения — сервер недоступен или отвечает слишком долго"),
            (("ssl", "certificate"), "Ошибка SSL-сертификата — проверьте HTTPS/сертификат сервера"),
            (("command not found",), "Не найдена команда CLI-провайдера — проверьте путь в настройках"),
            (("not found", "claude"), "Claude Code не найден — проверьте путь к команде claude"),
            (("not found", "codex"), "Codex не найден — проверьте путь к команде codex"),
            (("not found", "opencode"), "OpenCode не найден — проверьте путь к команде opencode"),
            (("not found", "pi"), "Pi не найден — проверьте путь к команде pi"),
        ]
        for needles, message in friendly_rules:
            if all(needle in lowered for needle in needles):
                return message

        text = " ".join(raw_text.split())
        if len(text) <= limit:
            return text
        return text[:limit - 1] + "…"

    def _write_llm_output_file(self, save_path: str, content: str, export_format: str):
        if export_format in ("txt", "md"):
            with open(save_path, "w", encoding="utf-8") as f:
                f.write(content)
            return
        if export_format == "docx":
            from docx import Document
            doc = Document()
            for block in content.split("\n\n"):
                doc.add_paragraph(block)
            doc.save(save_path)

    def _save_llm_result(self, item: dict, answer: str, mode_suffix: str, export_formats: list) -> list[str]:
        source_path = item.get("source_path")
        if self.llm_output_dir:
            target_dir = self.llm_output_dir
        elif source_path:
            target_dir = os.path.dirname(source_path)
        else:
            target_dir = os.getcwd()
        os.makedirs(target_dir, exist_ok=True)
        saved_paths = []
        for export_format in export_formats:
            save_path = os.path.join(target_dir, f"{item['name']}_llm_{mode_suffix}.{export_format}")
            self._write_llm_output_file(save_path, answer, export_format)
            saved_paths.append(save_path)
        return saved_paths

    def _set_llm_buttons_enabled(self, enabled: bool):
        self.btn_llm_process.setEnabled(enabled)
        self.btn_llm_clear.setEnabled(enabled)

    def _on_llm_finished(self, success: bool, message: str, result_text: str):
        self.is_llm_processing = False
        self._set_llm_buttons_enabled(True)
        self.lbl_llm_status.setText(message)
        if result_text:
            self.llm_last_result_text = result_text
            self.txt_llm_result.setPlainText(result_text)
        elif not success:
            self.llm_last_result_text = ""
            self.txt_llm_result.setPlainText(message)
        if success:
            QMessageBox.information(self, self._t("Готово", "Done"), message)
        else:
            QMessageBox.warning(self, self._t("Ошибка", "Error"), message)

    def _export_llm_result(self, export_format: str):
        result_text = self.txt_llm_result.toPlainText().strip()
        if not result_text:
            QMessageBox.information(self, self._t("Внимание", "Attention"), self._t("Нет результата для экспорта", "There is no result to export."))
            return
        suffix = {"txt": "txt", "md": "md", "docx": "docx"}[export_format]
        initial_dir = self.llm_output_dir or self.output_dir or os.path.expanduser("~")
        default_name = f"{self.llm_last_result_name}.{suffix}"
        save_path, _ = QFileDialog.getSaveFileName(
            self,
            f"Сохранить результат как {suffix.upper()}",
            os.path.join(initial_dir, default_name),
            f"{suffix.upper()} files (*.{suffix})"
        )
        if not save_path:
            return
        if not save_path.lower().endswith(f".{suffix}"):
            save_path += f".{suffix}"
        try:
            if export_format in ("txt", "md"):
                with open(save_path, "w", encoding="utf-8") as f:
                    f.write(result_text)
            else:
                try:
                    from docx import Document
                except Exception:
                    QMessageBox.warning(self, self._t("Ошибка", "Error"), self._t("Для экспорта в DOCX установите пакет python-docx", "Install the python-docx package to export to DOCX."))
                    return
                doc = Document()
                for block in result_text.split("\n\n"):
                    doc.add_paragraph(block)
                doc.save(save_path)
            target_dir = os.path.dirname(save_path)
            if target_dir:
                self.llm_output_dir = target_dir
                self.user_settings.set_value("llm_output_dir", target_dir)
                self._update_llm_output_dir_label(target_dir)
            self.lbl_llm_status.setText(f"Результат экспортирован: {os.path.basename(save_path)}")
        except Exception as e:
            QMessageBox.warning(self, self._t("Ошибка", "Error"), self._t("Не удалось экспортировать результат: ", "Failed to export the result: ") + str(e))
