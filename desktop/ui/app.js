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
  llmProvider: "OpenAI-compatible",
  llmModel: "gpt-4.1-mini",
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
  python: `import requests

url = "http://127.0.0.1:8000/api/transcribe"
files = {"file": open("audio.mp3", "rb")}
data = {"language": "ru", "diarize": True}
response = requests.post(url, files=files, data=data)`,
  curl: `curl -X POST "http://127.0.0.1:8000/api/transcribe" \\
  -F "file=@audio.mp3" \\
  -F "language=ru" \\
  -F "diarize=true"`,
  javascript: `const form = new FormData();
form.append("file", audioFile);
form.append("language", "ru");
form.append("diarize", "true");

const response = await fetch("http://127.0.0.1:8000/api/transcribe", {
  method: "POST", body: form,
});`,
};

let settings = loadSettings();
let selectedFiles = [];
let toastTimer;

function loadSettings() {
  try {
    const saved = JSON.parse(localStorage.getItem(storageKey));
    return { ...defaults, ...(saved && typeof saved === "object" ? saved : {}) };
  } catch {
    return { ...defaults };
  }
}

function saveSettings(message = "Изменения сохранены на этом устройстве.") {
  localStorage.setItem(storageKey, JSON.stringify(settings));
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
  clearButton.disabled = selectedFiles.length === 0;

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
    name.textContent = file.name;
    meta.textContent = `${formatSize(file.size)} • ${file.type || "локальный файл"}`;
    details.append(name, meta);

    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "file-remove";
    remove.dataset.removeFile = String(index);
    remove.setAttribute("aria-label", `Удалить ${file.name}`);
    remove.textContent = "×";

    row.append(details, remove);
    return row;
  }));
}

function formatSize(bytes) {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
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
  const fileInput = document.querySelector("#media-files");
  fileInput.addEventListener("change", () => {
    selectedFiles = [...selectedFiles, ...fileInput.files];
    fileInput.value = "";
    renderSelectedFiles();
  });
  document.querySelector("#selected-files").addEventListener("click", (event) => {
    const button = event.target.closest("[data-remove-file]");
    if (!button) return;
    selectedFiles.splice(Number(button.dataset.removeFile), 1);
    renderSelectedFiles();
  });
  document.querySelector("#clear-files").addEventListener("click", () => {
    selectedFiles = [];
    renderSelectedFiles();
  });
  document.querySelector("#media-link-button").addEventListener("click", () => {
    const field = document.querySelector(".media-link-field");
    field.hidden = !field.hidden;
    if (!field.hidden) field.querySelector("input").focus();
  });
}

function setupLlm() {
  document.querySelectorAll(".template").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll(".template").forEach((item) => item.classList.toggle("active", item === button));
      if (button.dataset.template === "custom") document.querySelector("#llm-prompt").focus();
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

  document.querySelector("#llm-run").addEventListener("click", () => {
    if (!document.querySelector("#llm-source").value.trim()) {
      showToast("Добавьте исходный текст перед запуском LLM-обработки.");
      return;
    }
    showToast("Подключите совместимый LLM-провайдер через локальный worker, чтобы выполнить обработку.");
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
      saveSettings();
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
setupLlm();
setupApi();
setupSettings();
setupNotices();
renderSelectedFiles();
