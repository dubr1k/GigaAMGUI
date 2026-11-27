const defaults = {
  language: "ru",
  theme: "system",
  autoUpdates: true,
  asrBackend: "GigaAM-v3",
  device: "Auto / GPU",
  cpuFallback: false,
  splitSentences: true,
  captionLines: "2",
  captionChars: "64",
  showSpeaker: true,
  diarizationEngine: "Pyannote 3.1",
  sampleRate: "16000 Hz",
  hfToken: "",
  outputPath: "Выберите после подключения worker",
  liquidGlass: true,
  animations: true,
};

const storageKey = "gigaam-desktop-settings-v1";
const pages = new Map([
  ["processing", "Обработка"],
  ["result", "Результат"],
  ["live", "Live"],
  ["llm", "LLM"],
  ["api", "API"],
  ["history", "Журнал"],
  ["settings", "Настройки"],
]);
const apiExamples = {
  python: `from openai import OpenAI
client = OpenAI(base_url="http://127.0.0.1:8000/v1", api_key="gam_...")
with open("meeting.mp3", "rb") as f:
    result = client.audio.transcriptions.create(model="whisper-1", file=f, response_format="verbose_json")
print(result.text)`,
  curl: `curl http://127.0.0.1:8000/v1/audio/transcriptions \\
  -H "Authorization: Bearer gam_..." \\
  -F file=@meeting.mp3 -F model=whisper-1 -F diarize=true -F response_format=diarized_json`,
  javascript: `import OpenAI from "openai";
import fs from "node:fs";
const client = new OpenAI({ baseURL: "http://127.0.0.1:8000/v1", apiKey: "gam_..." });
const result = await client.audio.transcriptions.create({ model: "whisper-1", file: fs.createReadStream("meeting.mp3") });
console.log(result.text);`,
};

let settings = loadSettings();
let selectedFiles = [];
const importedFiles = new Set();
let activeImport = null;
let selectionBusy = false;
let toastTimer;
let activeJob = null;
let activeJobCancelRequested = false;
let currentTranscript = "";

function desktop() {
  if (!window.__TAURI__) throw new Error("Эта функция доступна только в настольном приложении.");
  return window.__TAURI__;
}

function loadSettings() {
  try {
    const saved = JSON.parse(localStorage.getItem(storageKey));
    const restored = { ...defaults, ...(saved && typeof saved === "object" ? saved : {}) };
    restored.hfToken = "";
    return restored;
  } catch {
    return { ...defaults };
  }
}

function saveSettings(message = "Изменения сохранены на этом устройстве.") {
  const { hfToken: _secret, ...persisted } = settings;
  localStorage.setItem(storageKey, JSON.stringify(persisted));
  const status = document.querySelector("#settings-status");
  if (status) status.textContent = message;
}

function activeTheme() {
  if (settings.theme !== "system") return settings.theme;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function applySettings() {
  document.documentElement.dataset.theme = activeTheme();
  document.documentElement.dataset.reducedGlass = String(!settings.liquidGlass);
  document.documentElement.dataset.animations = String(Boolean(settings.animations));

  document.querySelectorAll("[data-setting]").forEach((control) => {
    const key = control.dataset.setting;
    if (!(key in settings)) return;
    if (control.type === "checkbox") control.checked = Boolean(settings[key]);
    else control.value = settings[key];
  });
}

function showToast(message) {
  const toast = document.querySelector("#toast");
  toast.textContent = message;
  toast.classList.add("visible");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove("visible"), 4200);
}

function showPage(pageId) {
  if (!pages.has(pageId)) return;
  document.querySelectorAll(".page").forEach((page) => {
    const isActive = page.dataset.page === pageId;
    page.hidden = !isActive;
    page.classList.toggle("active", isActive);
  });
  document.querySelectorAll(".nav-item[data-page-target]").forEach((button) => {
    button.classList.toggle("active", button.dataset.pageTarget === pageId);
  });
  document.querySelector(".main-scroll").scrollTo({ top: 0, behavior: "instant" });
}

function renderSelectedFiles() {
  const container = document.querySelector("#selected-files");
  const clearButton = document.querySelector("#clear-files");
  const busy = Boolean(activeJob || activeImport || selectionBusy);
  clearButton.disabled = busy || selectedFiles.length === 0;
  document.querySelector("#choose-media").disabled = busy;
  document.querySelector("#open-media-link").disabled = busy;
  document.querySelector("#import-media-link").disabled = busy;
  document.querySelector("#media-link").disabled = busy;
  document.querySelector("#start-processing").disabled = busy;

  if (!selectedFiles.length) {
    container.className = "empty-state compact";
    container.innerHTML = "<span class=\"empty-icon\" aria-hidden=\"true\">◌</span><strong>Файлы пока не выбраны</strong><p>Выберите аудио или видео, чтобы добавить их в очередь.</p>";
    return;
  }

  container.className = "selected-file-list";
  container.replaceChildren(...selectedFiles.map((file, index) => {
    const row = document.createElement("div");
    row.className = "selected-file";

    const details = document.createElement("div");
    const name = document.createElement("strong");
    const meta = document.createElement("span");
    name.textContent = file.split(/[\\/]/).pop();
    meta.textContent = file;
    details.append(name, meta);

    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "file-remove";
    remove.disabled = busy;
    remove.dataset.removeFile = String(index);
    remove.setAttribute("aria-label", `Удалить ${file}`);
    remove.textContent = "×";

    row.append(details, remove);
    return row;
  }));
}


async function copyText(text, successMessage) {
  try {
    await navigator.clipboard.writeText(text);
    showToast(successMessage);
  } catch {
    const helper = document.createElement("textarea");
    helper.value = text;
    helper.setAttribute("readonly", "");
    helper.style.position = "fixed";
    helper.style.opacity = "0";
    document.body.append(helper);
    helper.select();
    const copied = document.execCommand("copy");
    helper.remove();
    showToast(copied ? successMessage : "Не удалось скопировать в буфер обмена.");
  }
}

function activateSettingsSection(section) {
  document.querySelectorAll("[data-settings-section]").forEach((button) => {
    button.classList.toggle("active", button.dataset.settingsSection === section);
  });
  const status = document.querySelector("#settings-status");
  if (status) status.textContent = `${sectionLabel(section)}: изменения сохраняются на этом устройстве.`;
}

function sectionLabel(section) {
  return document.querySelector(`.settings-nav [data-settings-section="${section}"]`)?.textContent || "Настройки";
}

function setupNavigation() {
  document.querySelectorAll("[data-page-target]").forEach((button) => {
    button.addEventListener("click", () => showPage(button.dataset.pageTarget));
  });

  const search = document.querySelector("#global-search");
  document.querySelector("#search-focus").addEventListener("click", () => search.focus());
  document.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      search.focus();
      search.select();
    }
  });
  search.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    const query = search.value.trim().toLocaleLowerCase("ru");
    const matched = [...pages.entries()].find(([, label]) => label.toLocaleLowerCase("ru").includes(query));
    if (matched) {
      showPage(matched[0]);
      showToast(`Открыт раздел «${matched[1]}».`);
    } else if (query) {
      showToast("Совпадений среди доступных разделов нет.");
    }
  });
}

function setupProcessing() {
  const output = document.querySelector("#output-directory");
  const start = document.querySelector("#start-processing");
  const cancel = document.querySelector("#cancel-processing");
  const status = document.querySelector("#progress-status");
  const percentage = document.querySelector("#progress-percent");
  const track = document.querySelector("#progress-track");
  const linkForm = document.querySelector("#media-link-form");
  const linkInput = document.querySelector("#media-link");
  const linkStatus = document.querySelector("#media-link-status");
  const linkCancel = document.querySelector("#cancel-media-link");

  function renderProgress(value) {
    if (Number.isFinite(value)) {
      const percent = Math.max(0, Math.min(100, value));
      const rounded = percent < 100 ? Math.min(Math.round(percent), 99) : 100;
      percentage.textContent = `${rounded}%`;
      track.setAttribute("aria-label", "Общий прогресс");
      track.setAttribute("aria-valuenow", String(rounded));
      track.firstElementChild.style.width = `${percent}%`;
    } else {
      percentage.textContent = "—";
      track.setAttribute("aria-label", "Общий прогресс: не определён");
      track.removeAttribute("aria-valuenow");
      track.firstElementChild.style.width = "0%";
    }
  }

  async function releaseImported(paths) {
    const errors = [];
    for (const path of paths) {
      if (!importedFiles.has(path)) continue;
      try {
        await desktop().core.invoke("release_media_import", { path });
        importedFiles.delete(path);
      } catch (_) {
        errors.push("Не удалось удалить временный загруженный файл.");
      }
    }
    return errors;
  }

  document.querySelector("#open-media-link").addEventListener("click", (event) => {
    linkForm.hidden = !linkForm.hidden;
    event.currentTarget.setAttribute("aria-expanded", String(!linkForm.hidden));
    if (!linkForm.hidden) linkInput.focus();
  });
  linkForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (activeImport || activeJob || selectionBusy) return;
    const jobId = crypto.randomUUID();
    activeImport = { id: jobId, cancelReady: false };
    linkStatus.textContent = "Подготовка загрузки…";
    linkCancel.hidden = false;
    linkCancel.disabled = true;
    renderSelectedFiles();
    let unlisten;
    try {
      const tauri = desktop();
      unlisten = await tauri.event.listen("media-import-progress", ({ payload }) => {
        if (payload.job_id !== jobId || activeImport?.id !== jobId) return;
        if (payload.stage === "started") {
          activeImport.cancelReady = true;
          linkCancel.disabled = false;
        } else if (payload.stage === "downloading") {
          const loaded = (payload.downloaded / (1024 * 1024)).toFixed(1);
          const total = payload.total == null ? "" : ` из ${(payload.total / (1024 * 1024)).toFixed(1)}`;
          linkStatus.textContent = `Загружено ${loaded}${total} МиБ`;
        }
      });
      const path = await tauri.core.invoke("import_media", { jobId, url: linkInput.value });
      importedFiles.add(path);
      selectedFiles.push(path);
      linkInput.value = "";
      linkStatus.textContent = "Медиа добавлено в очередь. Временный файл будет удалён после обработки.";
    } catch (error) {
      linkStatus.textContent = String(error);
    } finally {
      if (unlisten) unlisten();
      activeImport = null;
      linkCancel.hidden = true;
      linkCancel.disabled = true;
      renderSelectedFiles();
    }
  });
  linkCancel.addEventListener("click", async () => {
    if (!activeImport?.cancelReady) return;
    linkCancel.disabled = true;
    linkStatus.textContent = "Отмена загрузки…";
    try { await desktop().core.invoke("cancel_media_import", { jobId: activeImport.id }); }
    catch (error) { linkStatus.textContent = String(error); }
  });

  document.querySelector("#choose-media").addEventListener("click", async () => {
    if (activeJob || activeImport || selectionBusy) return;
    selectionBusy = true;
    renderSelectedFiles();
    try {
      const paths = await desktop().core.invoke("plugin:dialog|open", { options: { multiple: true, directory: false } });
      if (paths) selectedFiles = [...new Set([...selectedFiles, ...paths])];
    } catch (error) { showToast(String(error)); }
    finally {
      selectionBusy = false;
      renderSelectedFiles();
    }
  });
  document.querySelector("#choose-output").addEventListener("click", async () => {
    try {
      const path = await desktop().core.invoke("plugin:dialog|open", { options: { directory: true } });
      if (path) output.value = path;
    } catch (error) { showToast(String(error)); }
  });
  document.querySelector("#selected-files").addEventListener("click", async (event) => {
    const button = event.target.closest("[data-remove-file]");
    if (!button || activeJob || activeImport || selectionBusy) return;
    const index = Number(button.dataset.removeFile);
    const path = selectedFiles[index];
    const wasImported = importedFiles.has(path);
    selectionBusy = true;
    renderSelectedFiles();
    const errors = await releaseImported([path]);
    if (errors.length) showToast(errors[0]);
    else selectedFiles.splice(index, 1);
    if (wasImported && !errors.length) linkStatus.textContent = "Временный загруженный файл удалён.";
    selectionBusy = false;
    renderSelectedFiles();
  });
  document.querySelector("#clear-files").addEventListener("click", async () => {
    if (activeJob || activeImport || selectionBusy) return;
    selectionBusy = true;
    renderSelectedFiles();
    const hadImports = importedFiles.size > 0;
    const errors = await releaseImported(selectedFiles);
    selectedFiles = selectedFiles.filter((path) => importedFiles.has(path));
    if (errors.length) showToast(errors[0]);
    if (hadImports && !errors.length) linkStatus.textContent = "Временные загруженные файлы удалены.";
    selectionBusy = false;
    renderSelectedFiles();
  });
  cancel.addEventListener("click", async () => {
    if (!activeJob || activeJobCancelRequested) return;
    const jobId = activeJob;
    activeJobCancelRequested = true;
    cancel.disabled = true;
    status.textContent = "Отмена обработки…";
    try { await desktop().core.invoke("cancel_transcription", { jobId }); }
    catch (error) {
      if (activeJob === jobId) {
        activeJobCancelRequested = false;
        cancel.disabled = false;
      }
      showToast(String(error));
    }
  });
  start.addEventListener("click", async () => {
    if (activeJob || activeImport || selectionBusy) return;
    const formats = [...document.querySelectorAll(".output-card input:checked")].map((input) => input.value);
    if (!selectedFiles.length || !output.value || !formats.length) {
      showToast("Выберите файлы, папку и хотя бы один формат.");
      return;
    }
    const jobId = crypto.randomUUID();
    activeJob = jobId;
    renderSelectedFiles();
    start.disabled = true;
    cancel.disabled = true;
    status.textContent = "Подготовка…";
    renderProgress(null);
    let unlisten;
    let completed = false;
    try {
      const tauri = desktop();
      unlisten = await tauri.event.listen("transcription-progress", ({ payload }) => {
        if (payload.job_id !== jobId) return;
        if (!activeJobCancelRequested) cancel.disabled = false;
        status.textContent = payload.message || payload.stage;
        const pendingCompletion = payload.stage !== "completed" && Number.isFinite(payload.percent) && payload.percent >= 100;
        renderProgress(pendingCompletion ? null : payload.percent);
      });
      const result = await tauri.core.invoke("transcribe", { request: {
        job_id: jobId, inputs: selectedFiles, model: document.querySelector("#asr-model").value,
        output_dir: output.value, formats, diarization: document.querySelector("#asr-diarization").checked,
      } });
      currentTranscript = result.files.map((file) => file.transcript).join("\n\n");
      const content = document.querySelector("#result-content");
      content.className = "";
      const text = document.createElement("pre");
      text.className = "transcript-text";
      text.textContent = currentTranscript;
      const files = document.createElement("ul");
      for (const file of result.files) for (const item of file.outputs) {
        const row = document.createElement("li");
        row.textContent = item.path;
        files.append(row);
      }
      content.replaceChildren(text, files);
      const speakerKinds = new Set();
      for (const file of result.files) for (const segment of file.segments ?? []) {
        if (["known", "unknown", "overlap"].includes(segment.speaker_status)) {
          speakerKinds.add(segment.speaker_status);
        }
      }
      const speakerExplanations = [];
      if (speakerKinds.has("known")) speakerExplanations.push("[SPEAKER_n] — предполагаемый говорящий");
      if (speakerKinds.has("unknown")) speakerExplanations.push("[SPEAKER_UNKNOWN] — говорящий не определён");
      if (speakerKinds.has("overlap")) speakerExplanations.push("[SPEAKER_OVERLAP] — возможное наложение речи");
      const speakerLegend = document.querySelector("#result-speaker-legend");
      speakerLegend.textContent = speakerExplanations.length ? `Метки приблизительны: ${speakerExplanations.join("; ")}.${speakerKinds.has("known") ? " Номера говорящих относятся к одному файлу." : ""}` : "";
      speakerLegend.hidden = !speakerExplanations.length;
      document.querySelector("#result-status").textContent = `Обработано файлов: ${result.files.length}`;
      document.querySelector("#copy-result").disabled = !currentTranscript;
      document.querySelector("#result-to-llm").disabled = !currentTranscript;
      renderProgress(100);
      status.textContent = "Готово";
      showPage("result");
      completed = true;
    } catch (error) {
      renderProgress(null);
      let message = String(error);
      for (const path of selectedFiles) if (importedFiles.has(path)) message = message.split(path).join("временный импорт");
      status.textContent = message;
      showToast(message);
    } finally {
      if (unlisten) unlisten();
      cancel.disabled = true;
      if (completed) {
        const consumed = selectedFiles.filter((path) => importedFiles.has(path));
        const errors = await releaseImported(consumed);
        selectedFiles = selectedFiles.filter((path) => !consumed.includes(path) || importedFiles.has(path));
        if (consumed.length && !errors.length) linkStatus.textContent = "Временные загруженные файлы удалены после обработки.";
        if (errors.length) showToast(errors[0]);
      }
      activeJob = null;
      activeJobCancelRequested = false;
      cancel.disabled = true;
      renderSelectedFiles();
    }
  });
  document.querySelector("#copy-result").addEventListener("click", () => copyText(currentTranscript, "Текст скопирован."));
  document.querySelector("#result-to-llm").addEventListener("click", () => {
    document.querySelector("#llm-source").value = currentTranscript;
    showPage("llm");
  });
}

function setupLive() {
  const start = document.querySelector("#live-start");
  const stop = document.querySelector("#live-stop");
  const cancel = document.querySelector("#live-cancel");
  const status = document.querySelector("#live-status");
  const output = document.querySelector("#live-output");
  const recordingPath = document.querySelector("#live-recording-path");
  const text = document.querySelector("#live-text");
  const partial = document.querySelector("#live-partial");
  let session = null;
  let unlisten = null;

  function finish(message) {
    status.textContent = message;
    partial.textContent = "";
    start.disabled = false;
    stop.disabled = true;
    cancel.disabled = true;
    document.querySelector("#live-copy").disabled = !text.textContent;
    document.querySelector("#live-llm").disabled = !text.textContent;
    session = null;
    if (unlisten) { unlisten(); unlisten = null; }
  }
  start.addEventListener("click", async () => {
    if (session) return;
    const sessionId = crypto.randomUUID();
    session = sessionId;
    start.disabled = true;
    stop.disabled = false;
    cancel.disabled = false;
    text.textContent = "";
    recordingPath.textContent = "";
    partial.textContent = "";
    document.querySelector("#live-copy").disabled = true;
    document.querySelector("#live-llm").disabled = true;
    status.textContent = "Подготовка…";
    try {
      const tauri = desktop();
      unlisten = await tauri.event.listen("live-event", ({ payload }) => {
        if (payload.session_id !== sessionId) return;
        if (payload.kind === "transcript") {
          text.textContent += (text.textContent ? "\n" : "") + (payload.text || "");
          partial.textContent = "";
        } else if (payload.kind === "partial") {
          partial.textContent = payload.text || "";
        } else if (["stopped", "cancelled", "failed"].includes(payload.kind)) {
          if (payload.recording_path) recordingPath.textContent = `${payload.recording_complete ? "Запись" : "Частичная или повреждённая запись"}: ${payload.recording_path}`;
          if (typeof payload.text === "string") text.textContent = payload.text;
          finish(payload.message || (payload.kind === "stopped" ? "Завершено" : payload.kind === "cancelled" ? "Отменено" : "Ошибка захвата"));
        } else {
          status.textContent = payload.message || payload.kind;
        }
      });
      await tauri.core.invoke("live_start", { request: {
        session_id: sessionId,
        model: document.querySelector("#live-model").value,
        source: document.querySelector("#live-source").value,
        output_dir: output.value || null,
      } });
    } catch (error) { finish(String(error)); showToast(String(error)); }
  });
  async function requestStop(command) {
    if (!session) return;
    stop.disabled = true;
    cancel.disabled = true;
    try { await desktop().core.invoke(command, { sessionId: session }); }
    catch (error) { stop.disabled = false; cancel.disabled = false; showToast(String(error)); }
  }
  stop.addEventListener("click", () => requestStop("live_stop"));
  cancel.addEventListener("click", () => requestStop("live_cancel"));
  document.querySelector("#live-choose-output").addEventListener("click", async () => {
    try {
      const path = await desktop().core.invoke("plugin:dialog|open", { options: { directory: true } });
      if (path) output.value = path;
    } catch (error) { showToast(String(error)); }
  });
  document.querySelector("#live-clear-output").addEventListener("click", () => { output.value = ""; });
  document.querySelector("#live-copy").addEventListener("click", () => copyText(text.textContent, "Текст скопирован."));
  document.querySelector("#live-llm").addEventListener("click", () => {
    document.querySelector("#llm-source").value = text.textContent;
    showPage("llm");
  });
}

function setupLlm() {
  const provider = document.querySelector("#llm-provider");
  const fields = ["model", "url", "cli-path", "cli-args", "cli-provider"];
  const preferencesKey = "gigaam-desktop-llm-v1";
  let preferences;
  try { preferences = JSON.parse(localStorage.getItem(preferencesKey)) || {}; }
  catch { preferences = {}; }
  if (!preferences || typeof preferences !== "object" || Array.isArray(preferences)) preferences = {};
  provider.value = preferences.provider || "API";
  if (!provider.value) provider.value = "API";
  function savePreferences() {
    preferences.provider = provider.value;
    preferences.providers ||= {};
    preferences.providers[provider.value] = Object.fromEntries(fields.map((field) => [field, document.querySelector(`#llm-${field}`).value]));
    localStorage.setItem(preferencesKey, JSON.stringify(preferences));
  }
  function loadProvider() {
    const values = preferences.providers?.[provider.value] || {};
    for (const field of fields) document.querySelector(`#llm-${field}`).value = values[field] || "";
    document.querySelector("#llm-allow-tools").checked = false;
    document.querySelector("#llm-key").value = "";
  }
  loadProvider();
  provider.addEventListener("change", () => { loadProvider(); savePreferences(); });
  for (const field of fields) document.querySelector(`#llm-${field}`).addEventListener("input", savePreferences);
  const promptField = document.querySelector("#llm-prompt");
  promptField.disabled = true;
  document.querySelectorAll(".template").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll(".template").forEach((item) => item.classList.toggle("active", item === button));
      promptField.disabled = button.dataset.template !== "custom";
      if (!promptField.disabled) promptField.focus();
    });
  });

  document.querySelector("#transcript-file").addEventListener("change", async (event) => {
    const [file] = event.target.files;
    event.target.value = "";
    if (!file) return;
    if (file.size > 1024 * 1024) {
      showToast("Файл больше 1 МБ. Вставьте нужный фрагмент текста вручную.");
      return;
    }
    try {
      document.querySelector("#llm-source").value = await file.text();
      showToast(`Загружен текст из «${file.name}».`);
    } catch {
      showToast("Не удалось прочитать выбранный файл.");
    }
  });

  const run = document.querySelector("#llm-run");
  const cancel = document.querySelector("#llm-cancel");
  const result = document.querySelector("#llm-result");
  const status = document.querySelector("#llm-status");
  cancel.addEventListener("click", async () => {
    try { await desktop().core.invoke("llm_cancel"); }
    catch (error) { showToast(String(error)); }
  });
  document.querySelector("#copy-llm").addEventListener("click", () => copyText(result.value, "Ответ скопирован."));
  run.addEventListener("click", async () => {
    const text = document.querySelector("#llm-source").value.trim();
    if (!text) { showToast("Добавьте исходный текст перед запуском LLM-обработки."); return; }
    let cliArgs;
    try {
      cliArgs = JSON.parse(document.querySelector("#llm-cli-args").value || "[]");
      if (!Array.isArray(cliArgs) || cliArgs.some((arg) => typeof arg !== "string")) throw new Error();
    } catch {
      showToast('Аргументы CLI должны быть JSON-массивом строк, например ["--profile","work"].');
      return;
    }
    result.value = "";
    document.querySelector("#copy-llm").disabled = true;
    run.disabled = true;
    cancel.disabled = false;
    status.textContent = "Обработка…";
    try {
      const response = await desktop().core.invoke("llm_process", { request: {
        text,
        mode: document.querySelector(".template.active").dataset.template,
        prompt: document.querySelector("#llm-prompt").value,
        settings: {
          provider: document.querySelector("#llm-provider").value,
          model: document.querySelector("#llm-model").value,
          api_url: document.querySelector("#llm-url").value,
          api_key: document.querySelector("#llm-key").value,
          cli_path: document.querySelector("#llm-cli-path").value,
          cli_args: cliArgs,
          cli_provider: document.querySelector("#llm-cli-provider").value,
          allow_tools: document.querySelector("#llm-allow-tools").checked,
        },
      } });
      result.value = response.text;
      document.querySelector("#copy-llm").disabled = !result.value;
      status.textContent = "Готово";
    } catch (error) {
      status.textContent = String(error);
      showToast(String(error));
    } finally {
      run.disabled = false;
      cancel.disabled = true;
    }
  });
}

function setupApi() {
  const code = document.querySelector("#api-code code");
  document.querySelectorAll("[data-code-language]").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll("[data-code-language]").forEach((item) => item.classList.toggle("active", item === button));
      code.textContent = apiExamples[button.dataset.codeLanguage];
    });
  });
  document.querySelector("#copy-api-url").addEventListener("click", () => copyText("http://127.0.0.1:8000", "URL скопирован."));
  document.querySelector("#copy-api-code").addEventListener("click", () => copyText(code.textContent, "Пример скопирован."));
  document.querySelectorAll(".documentation-list button").forEach((button) => {
    button.addEventListener("click", () => showToast(`«${button.firstChild.textContent.trim()}» станет доступно после подключения локального API.`));
  });
}

function setupSettings() {
  document.querySelectorAll("[data-setting]").forEach((control) => {
    const update = () => {
      const key = control.dataset.setting;
      settings[key] = control.type === "checkbox" ? control.checked : control.value;
      applySettings();
      saveSettings(key === "hfToken" ? "HF Token используется только до закрытия окна и не сохраняется." : undefined);
    };
    control.addEventListener(control.type === "text" || control.type === "password" ? "input" : "change", update);
  });

  document.querySelectorAll("[data-settings-section]").forEach((button) => {
    button.addEventListener("click", () => activateSettingsSection(button.dataset.settingsSection));
  });

  document.querySelector("#appearance-button").addEventListener("click", () => {
    settings.theme = settings.theme === "system" ? "light" : settings.theme === "light" ? "dark" : "system";
    applySettings();
    saveSettings(`Тема: ${settings.theme === "system" ? "системная" : settings.theme === "light" ? "светлая" : "темная"}.`);
  });
}

function setupNotices() {
  document.querySelectorAll("[data-notice]").forEach((button) => {
    button.addEventListener("click", () => showToast(button.dataset.notice));
  });
  document.querySelectorAll(".filters button").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll(".filters button").forEach((item) => item.classList.toggle("active", item === button));
    });
  });
  document.querySelector("#history-search").addEventListener("input", (event) => {
    if (event.target.value) showToast("Записей для поиска пока нет.");
  });
}

applySettings();
setupNavigation();
setupProcessing();
setupLive();
setupLlm();
setupApi();
setupSettings();
setupNotices();
renderSelectedFiles();
