// ===== GigaAM v3 Web GUI =====

const API = '/api';
let authToken = null;
let selectedFiles = [];
let selectedLlmFiles = [];
let progressSource = null;
let currentLogs = [];
let completedTaskNotified = new Set();
let currentLang = localStorage.getItem('gigaam_lang') || 'ru';

const I18N = {
    ru: {
        title: 'GigaAM v3: Транскрибация',
        logout: 'Выйти',
        process: 'Обработка',
        llm: 'LLM',
        log: 'Журнал',
        results: 'Результаты',
        start: 'ЗАПУСТИТЬ ОБРАБОТКУ',
        clear: 'ОЧИСТИТЬ ВСЁ',
        filesNone: 'Файлы не выбраны',
        folderNone: 'Папка не выбрана',
        noResults: 'Нет завершённых задач',
        ready: 'Готов к работе',
        login: 'Войти',
        loginSubtitle: 'Транскрибация аудио и видео',
        loginPlaceholder: 'Логин',
        passwordPlaceholder: 'Пароль',
        mediaUrlPlaceholder: 'Ссылка на медиа (YouTube, и т.д.)',
        download: 'Загрузить',
        chooseFiles: 'Выбрать файлы',
        chooseFolder: 'Выбрать папку',
        dropFiles: 'Перетащите файлы сюда',
        cardFiles: '1. Выбор файлов',
        cardDiarization: '2. Диаризация спикеров',
        enableDiarization: 'Включить диаризацию спикеров',
        diarizationBackend: 'Движок:',
        speakersCount: 'Кол-во спикеров:',
        speakersAutoPlaceholder: 'Пусто = авто',
        diarizationHint: 'Автоматическое определение спикеров (требуется HF_TOKEN)',
        cardFormats: '3. Форматы вывода',
        overallProgress: 'Общий прогресс',
        llmChooseFiles: 'Выбрать транскрипты',
        llmProcess: 'ОБРАБОТАТЬ',
        llmReady: 'Готово к LLM-обработке',
        llmFilesNone: 'Файлы не выбраны',
        llmClear: 'ОЧИСТИТЬ ВСЁ',
        llmSource: '1. Источник транскрипта',
        llmDropFiles: 'Перетащите транскрипты сюда',
        llmManualPlaceholder: 'Или вставьте транскрипт вручную',
        llmSaveWhere: '2. Куда сохранить',
        llmSaveHint: 'Результаты сохраняются на сервере и доступны для скачивания после обработки.',
        llmWhatToDo: '3. Что сделать',
        llmSummary: 'Выжимка',
        llmTasks: 'Задачи',
        llmCustom: 'Свой промпт',
        llmModesHint: 'Промпты и настройки ниже. Для режима «Свой промпт» заполните поле в настройках провайдера.',
        llmFormats: '4. Форматы вывода',
        llmSettings: 'Настройки LLM',
        provider: 'Провайдер:',
        model: 'Модель:',
        temperature: 'Temperature:',
        claudeArgsPlaceholder: 'Доп. аргументы Claude',
        codexArgsPlaceholder: 'Доп. аргументы Codex',
        opencodeArgsPlaceholder: 'Доп. аргументы OpenCode',
        piArgsPlaceholder: 'Доп. аргументы Pi',
        otherPathPlaceholder: 'команда',
        otherArgsPlaceholder: 'аргументы',
        summaryPromptPlaceholder: 'Промпт для выжимки',
        tasksPromptPlaceholder: 'Промпт для задач',
        customPromptPlaceholder: 'Пользовательский промпт',
        llmResult: '5. Результат LLM',
        dangerZone: 'Опасная зона',
        dangerZoneText: 'Удалить все данные пользователя: задачи, загруженные файлы и результаты транскрибации. Действие необратимо.',
        deleteMyData: 'Удалить все мои данные',
        uploadInProgress: 'ЗАГРУЗКА...',
        processingInProgress: 'ИДЁТ ОБРАБОТКА...',
        loading: 'Загрузка...',
        noData: 'Нет данных',
        view: 'Просмотреть',
        delete: 'Удалить',
        done: '✓ Готово',
        errorShort: '✕ Ошибка',
    },
    en: {
        title: 'GigaAM v3: Transcription',
        logout: 'Logout',
        process: 'Process',
        llm: 'LLM',
        log: 'Log',
        results: 'Results',
        start: 'START PROCESSING',
        clear: 'CLEAR ALL',
        filesNone: 'No files selected',
        folderNone: 'No folder selected',
        noResults: 'No completed tasks',
        ready: 'Ready to work',
        login: 'Login',
        loginSubtitle: 'Audio and video transcription',
        loginPlaceholder: 'Login',
        passwordPlaceholder: 'Password',
        mediaUrlPlaceholder: 'Media URL (YouTube, etc.)',
        download: 'Download',
        chooseFiles: 'Choose files',
        chooseFolder: 'Choose folder',
        dropFiles: 'Drop files here',
        cardFiles: '1. File selection',
        cardDiarization: '2. Speaker diarization',
        enableDiarization: 'Enable speaker diarization',
        diarizationBackend: 'Backend:',
        speakersCount: 'Speakers count:',
        speakersAutoPlaceholder: 'Empty = auto',
        diarizationHint: 'Automatic speaker detection (HF_TOKEN required)',
        cardFormats: '3. Output formats',
        overallProgress: 'Overall progress',
        llmChooseFiles: 'Choose transcripts',
        llmProcess: 'PROCESS',
        llmReady: 'Ready for LLM processing',
        llmFilesNone: 'No files selected',
        llmClear: 'CLEAR ALL',
        llmSource: '1. Transcript source',
        llmDropFiles: 'Drop transcripts here',
        llmManualPlaceholder: 'Or paste the transcript manually',
        llmSaveWhere: '2. Save location',
        llmSaveHint: 'Results are stored on the server and available for download after processing.',
        llmWhatToDo: '3. What to do',
        llmSummary: 'Summary',
        llmTasks: 'Tasks',
        llmCustom: 'Custom prompt',
        llmModesHint: 'Prompts and settings are below. For the custom prompt mode, fill in the field in provider settings.',
        llmFormats: '4. Output formats',
        llmSettings: 'LLM settings',
        provider: 'Provider:',
        model: 'Model:',
        temperature: 'Temperature:',
        claudeArgsPlaceholder: 'Extra Claude arguments',
        codexArgsPlaceholder: 'Extra Codex arguments',
        opencodeArgsPlaceholder: 'Extra OpenCode arguments',
        piArgsPlaceholder: 'Extra Pi arguments',
        otherPathPlaceholder: 'command',
        otherArgsPlaceholder: 'arguments',
        summaryPromptPlaceholder: 'Prompt for summary',
        tasksPromptPlaceholder: 'Prompt for tasks',
        customPromptPlaceholder: 'Custom prompt',
        llmResult: '5. LLM result',
        dangerZone: 'Danger zone',
        dangerZoneText: 'Delete all user data: tasks, uploaded files, and transcription results. This action cannot be undone.',
        deleteMyData: 'Delete all my data',
        uploadInProgress: 'UPLOADING...',
        processingInProgress: 'PROCESSING...',
        loading: 'Loading...',
        noData: 'No data',
        view: 'View',
        delete: 'Delete',
        done: '✓ Done',
        errorShort: '✕ Error',
    }
};

function t(key) {
    return (I18N[currentLang] && I18N[currentLang][key]) || I18N.ru[key] || key;
}

function applyLanguage() {
    document.documentElement.lang = currentLang;
    document.title = t('title');
    document.getElementById('btn-lang').textContent = currentLang === 'ru' ? 'EN' : 'RU';
    document.querySelectorAll('[data-i18n]').forEach(el => {
        el.textContent = t(el.dataset.i18n);
    });
    document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
        el.placeholder = t(el.dataset.i18nPlaceholder);
    });
    document.querySelectorAll('[data-i18n-title]').forEach(el => {
        el.title = t(el.dataset.i18nTitle);
    });
    if (selectedFiles.length === 0) {
        document.getElementById('files-count').textContent = t('filesNone');
    }
    if (document.getElementById('folder-label').textContent.trim() === 'Папка не выбрана' || document.getElementById('folder-label').textContent.trim() === 'No folder selected') {
        document.getElementById('folder-label').textContent = t('folderNone');
    }
    if (document.getElementById('status-label')) {
        document.getElementById('status-label').textContent = t('ready');
    }
    if (document.getElementById('llm-status')) {
        document.getElementById('llm-status').textContent = t('llmReady');
    }
    if (selectedLlmFiles.length === 0 && document.getElementById('llm-files-count')) {
        document.getElementById('llm-files-count').textContent = t('llmFilesNone');
    }
    const providerSelect = document.getElementById('llm-provider');
    if (providerSelect && providerSelect.options.length >= 6) {
        providerSelect.options[5].textContent = currentLang === 'ru' ? 'Другое' : 'Other';
    }
}

// ===== AUTH =====

async function checkAuth() {
    try {
        const res = await fetch(`${API}/auth/check`);
        if (res.ok) {
            const data = await res.json();
            showMainScreen(data.username);
            return true;
        }
    } catch (e) {}
    showLoginScreen();
    return false;
}

function showLoginScreen() {
    document.getElementById('login-screen').classList.remove('hidden');
    document.getElementById('main-screen').classList.add('hidden');
}

function showMainScreen(username) {
    document.getElementById('login-screen').classList.add('hidden');
    document.getElementById('main-screen').classList.remove('hidden');
    initApp();
}

document.getElementById('login-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const username = document.getElementById('login-username').value;
    const password = document.getElementById('login-password').value;
    const errEl = document.getElementById('login-error');
    errEl.textContent = '';

    try {
        const res = await fetch(`${API}/auth/login`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, password }),
        });
        if (res.ok) {
            showMainScreen(username);
        } else {
            const data = await res.json();
            errEl.textContent = data.detail || (currentLang === 'ru' ? 'Ошибка входа' : 'Login error');
        }
    } catch (err) {
        errEl.textContent = currentLang === 'ru' ? 'Ошибка соединения' : 'Connection error';
    }
});

document.getElementById('btn-logout').addEventListener('click', async () => {
    await fetch(`${API}/auth/logout`, { method: 'POST' });
    showLoginScreen();
});

document.getElementById('btn-lang').addEventListener('click', () => {
    currentLang = currentLang === 'ru' ? 'en' : 'ru';
    localStorage.setItem('gigaam_lang', currentLang);
    applyLanguage();
    updateFileList();
    loadResults();
});

// ===== INIT APP =====

async function initApp() {
    applyLanguage();
    loadDeviceInfo();
    loadResults();
    startProgressStream();
    setupFileSelection();
    setupDragDrop();
    setupDiarization();
    setupFormats();
    setupStartButton();
    setupClearButton();
    setupDeleteAllUserDataButton();
    setupTabs();
    setupUrlDownload();
    setupLlmTab();
}

async function loadDeviceInfo() {
    try {
        const res = await fetch(`${API}/device`);
        if (res.ok) {
            const data = await res.json();
            document.getElementById('device-badge').textContent = data.device;
        }
    } catch (e) {}
}

// ===== TABS =====

function setupTabs() {
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
            btn.classList.add('active');
            document.getElementById(`tab-${btn.dataset.tab}`).classList.add('active');
            if (btn.dataset.tab === 'results') loadResults();
        });
    });
}

// ===== FILE SELECTION =====

function setupFileSelection() {
    const fileInput = document.getElementById('file-input');
    const folderInput = document.getElementById('folder-input');

    document.getElementById('btn-select-files').addEventListener('click', () => fileInput.click());
    document.getElementById('btn-select-folder').addEventListener('click', () => folderInput.click());

    fileInput.addEventListener('change', (e) => {
        addFiles(Array.from(e.target.files));
        fileInput.value = '';
    });

    folderInput.addEventListener('change', (e) => {
        const files = Array.from(e.target.files).filter(f => isMediaFile(f.name));
        addFiles(files);
        if (files.length > 0) {
            const folder = files[0].webkitRelativePath.split('/')[0];
            document.getElementById('folder-label').textContent = folder;
            document.getElementById('folder-label').style.color = 'var(--text)';
        }
        folderInput.value = '';
    });
}

function isMediaFile(filename) {
    const ext = filename.toLowerCase().substring(filename.lastIndexOf('.'));
    const supported = ['.mp3', '.wav', '.m4a', '.aac', '.flac', '.ogg', '.mp4', '.avi', '.mov', '.mkv', '.webm', '.wma', '.qta', '.3gp'];
    return supported.includes(ext);
}

function addFiles(files) {
    files = files.filter(f => isMediaFile(f.name));
    files.forEach(f => {
        const path = f.webkitRelativePath || f.name;
        if (!selectedFiles.some(sf => (sf.webkitRelativePath || sf.name) === path)) {
            selectedFiles.push(f);
        }
    });
    updateFileList();
}

function updateFileList() {
    const list = document.getElementById('file-list');
    const count = document.getElementById('files-count');

    if (selectedFiles.length === 0) {
        list.classList.add('hidden');
        count.textContent = t('filesNone');
        count.style.color = 'var(--text-muted)';
        return;
    }

    count.textContent = currentLang === 'ru' ? `Выбрано файлов: ${selectedFiles.length}` : `Selected files: ${selectedFiles.length}`;
    count.style.color = 'var(--text)';

    list.classList.remove('hidden');
    list.innerHTML = selectedFiles.map((f, i) => {
        const name = f.webkitRelativePath || f.name;
        const size = formatSize(f.size);
        return `<div class="file-item">
            <span class="file-item-name">${escapeHtml(name)}</span>
            <span class="file-item-size">${size}</span>
            <button class="file-item-remove" onclick="removeFile(${i})">&times;</button>
        </div>`;
    }).join('');
}

window.removeFile = function(idx) {
    selectedFiles.splice(idx, 1);
    updateFileList();
};

function formatSize(bytes) {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    if (bytes < 1024 * 1024 * 1024) return (bytes / 1024 / 1024).toFixed(1) + ' MB';
    return (bytes / 1024 / 1024 / 1024).toFixed(2) + ' GB';
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function escapeJsString(text) {
    return String(text)
        .replace(/\\/g, '\\\\')
        .replace(/'/g, "\\'")
        .replace(/\r/g, '\\r')
        .replace(/\n/g, '\\n');
}

function friendlyError(text) {
    const raw = String(text || '').trim();
    const lower = raw.toLowerCase();
    const rules = [
        [['refresh token was revoked'], currentLang === 'ru' ? 'Codex: сессия истекла или токен отозван — нужно заново войти' : 'Codex: session expired or token revoked — sign in again'],
        [['token_invalidated'], currentLang === 'ru' ? 'Токен недействителен — выполните повторный вход' : 'Token is invalid — sign in again'],
        [['connection refused', '127.0.0.1'], currentLang === 'ru' ? 'Локальный сервер недоступен — проверьте, что он запущен' : 'Local server is unavailable — check that it is running'],
        [['401'], currentLang === 'ru' ? 'Ошибка авторизации (401) — проверьте ключ, токен или логин' : 'Authorization error (401) — check key, token or login'],
        [['403'], currentLang === 'ru' ? 'Доступ запрещён (403)' : 'Access forbidden (403)'],
        [['404'], currentLang === 'ru' ? 'Endpoint не найден (404) — проверьте URL' : 'Endpoint not found (404) — check the URL'],
        [['429'], currentLang === 'ru' ? 'Превышен лимит запросов (429)' : 'Rate limit exceeded (429)'],
        [['could not resolve host'], currentLang === 'ru' ? 'Не удалось найти хост — проверьте адрес API' : 'Could not resolve host — check API address'],
        [['read timed out'], currentLang === 'ru' ? 'Сервер отвечает слишком долго' : 'Server response timed out'],
        [['command not found'], currentLang === 'ru' ? 'CLI-команда не найдена — проверьте путь в настройках' : 'CLI command not found — check the path in settings'],
    ];
    for (const [needles, message] of rules) {
        if (needles.every(n => lower.includes(n))) return message;
    }
    return raw;
}

// ===== DRAG & DROP =====

function setupDragDrop() {
    const dropZone = document.getElementById('drop-zone');

    dropZone.addEventListener('click', () => document.getElementById('file-input').click());

    dropZone.addEventListener('dragover', (e) => {
        e.preventDefault();
        dropZone.classList.add('dragover');
    });

    dropZone.addEventListener('dragleave', () => {
        dropZone.classList.remove('dragover');
    });

    dropZone.addEventListener('drop', (e) => {
        e.preventDefault();
        dropZone.classList.remove('dragover');
        const files = Array.from(e.dataTransfer.files).filter(f => isMediaFile(f.name));
        if (files.length > 0) addFiles(files);
    });

    // Drag & drop на весь экран
    document.body.addEventListener('dragover', (e) => {
        e.preventDefault();
    });
    document.body.addEventListener('drop', (e) => {
        e.preventDefault();
        const files = Array.from(e.dataTransfer.files).filter(f => isMediaFile(f.name));
        if (files.length > 0) addFiles(files);
    });
}

// ===== DIARIZATION =====

function setupDiarization() {
    const cb = document.getElementById('cb-diarization');
    const backend = document.getElementById('diarization-backend');
    const numSpeakers = document.getElementById('num-speakers');
    const hint = document.querySelector('[data-i18n="diarizationHint"]');

    const updateControls = () => {
        const sortformer = backend.value === 'sortformer';
        numSpeakers.disabled = !cb.checked || sortformer;
        if (sortformer) numSpeakers.value = '';
        hint.textContent = sortformer
            ? (currentLang === 'ru' ? 'NVIDIA Sortformer: автоопределение, максимум 4 спикера; нужен NeMo' : 'NVIDIA Sortformer: auto-detect, up to 4 speakers; NeMo required')
            : t('diarizationHint');
    };

    cb.addEventListener('change', () => {
        updateControls();
        document.querySelectorAll('.fmt-cb[data-fmt="txt_diarize"], .fmt-cb[data-fmt="txt_diarize_timecodes"]').forEach(el => {
            el.disabled = !cb.checked;
        });
        addLog(cb.checked ? (currentLang === 'ru' ? 'Диаризация: ВКЛЮЧЕНА' : 'Diarization: ON') : (currentLang === 'ru' ? 'Диаризация: ВЫКЛЮЧЕНА' : 'Diarization: OFF'));
    });
    backend.addEventListener('change', updateControls);
    updateControls();
}

// ===== FORMATS =====

function setupFormats() {
    document.querySelectorAll('.fmt-cb').forEach(cb => {
        cb.addEventListener('change', () => {
            const anyChecked = Array.from(document.querySelectorAll('.fmt-cb')).some(c => c.checked);
            if (!anyChecked) {
                document.querySelector('.fmt-cb[data-fmt="txt"]').checked = true;
                addLog(currentLang === 'ru' ? 'Предупреждение: автоматически оставлен формат txt' : 'Warning: txt format was kept automatically');
            }
        });
    });
}

function getSelectedFormats() {
    return Array.from(document.querySelectorAll('.fmt-cb:checked')).map(cb => cb.dataset.fmt);
}

// ===== URL DOWNLOAD =====

function setupUrlDownload() {
    document.getElementById('btn-download-url').addEventListener('click', async () => {
        const url = document.getElementById('url-input').value.trim();
        if (!url) { alert(currentLang === 'ru' ? 'Введите ссылку' : 'Enter a URL'); return; }
        if (!url.startsWith('http://') && !url.startsWith('https://')) {
            alert(currentLang === 'ru' ? 'Ссылка должна начинаться с http:// или https://' : 'URL must start with http:// or https://');
            return;
        }

        const formats = getSelectedFormats();
        const diar = document.getElementById('cb-diarization').checked;
        const diarBackend = document.getElementById('diarization-backend').value;
        const ns = document.getElementById('num-speakers').value;

        const formData = new FormData();
        formData.append('url', url);
        formData.append('output_formats', formats.join(','));
        formData.append('enable_diarization', diar);
        formData.append('diarization_backend', diarBackend);
        formData.append('num_speakers', ns);

        document.getElementById('btn-download-url').disabled = true;
        document.getElementById('download-progress').classList.remove('hidden');
        document.getElementById('download-bar').style.width = '0%';
        document.getElementById('download-pct').textContent = '0%';

        addLog(currentLang === 'ru' ? `Загрузка по ссылке: ${url}` : `Downloading from URL: ${url}`);

        try {
            const res = await fetch(`${API}/download-url`, { method: 'POST', body: formData });
            if (res.ok) {
                const data = await res.json();
                addLog(currentLang === 'ru' ? `Загрузка началась (task: ${data.task_id.substring(0, 8)}...)` : `Download started (task: ${data.task_id.substring(0, 8)}...)`);
                document.getElementById('url-input').value = '';
            } else {
                const err = await res.json();
                addLog(`${currentLang === 'ru' ? 'Ошибка' : 'Error'}: ${err.detail}`, 'error');
            }
        } catch (e) {
            addLog(`${currentLang === 'ru' ? 'Ошибка' : 'Error'}: ${e}`, 'error');
        }

        document.getElementById('btn-download-url').disabled = false;
    });
}

// ===== START PROCESSING =====

function setupStartButton() {
    document.getElementById('btn-start').addEventListener('click', async () => {
        if (selectedFiles.length === 0) {
            alert(currentLang === 'ru' ? 'Выберите хотя бы один файл!' : 'Choose at least one file!');
            return;
        }

        const formats = getSelectedFormats();
        const diar = document.getElementById('cb-diarization').checked;
        const diarBackend = document.getElementById('diarization-backend').value;
        const ns = document.getElementById('num-speakers').value;

        const formData = new FormData();
        selectedFiles.forEach(f => formData.append('files', f));
        formData.append('output_formats', formats.join(','));
        formData.append('enable_diarization', diar);
        formData.append('diarization_backend', diarBackend);
        formData.append('num_speakers', ns);

        document.getElementById('btn-start').disabled = true;
        document.getElementById('btn-start').textContent = t('uploadInProgress');
        document.getElementById('progress-section').classList.remove('hidden');

        addLog(currentLang === 'ru' ? `Запуск обработки: ${selectedFiles.length} файлов, форматы: ${formats.join(', ')}` : `Processing started: ${selectedFiles.length} files, formats: ${formats.join(', ')}`);
        if (diar) addLog(currentLang === 'ru' ? `Диаризация: ВКЛЮЧЕНА${ns ? ', спикеров: ' + ns : ', авто'}` : `Diarization: ON${ns ? ', speakers: ' + ns : ', auto'}`);

        try {
            const res = await fetch(`${API}/upload`, { method: 'POST', body: formData });
            if (res.ok) {
                const data = await res.json();
                addLog(currentLang === 'ru' ? `Загружено ${data.total} файлов для обработки` : `Uploaded ${data.total} files for processing`);
                selectedFiles = [];
                updateFileList();
                document.getElementById('folder-label').textContent = t('folderNone');
                document.getElementById('folder-label').style.color = 'var(--text-muted)';
            } else {
                const err = await res.json();
                addLog(`${currentLang === 'ru' ? 'Ошибка' : 'Error'}: ${err.detail}`, 'error');
            }
        } catch (e) {
            addLog(`${currentLang === 'ru' ? 'Ошибка' : 'Error'}: ${e}`, 'error');
        }

        document.getElementById('btn-start').disabled = false;
        document.getElementById('btn-start').textContent = t('start');
    });
}

// ===== CLEAR =====

function setupClearButton() {
    document.getElementById('btn-clear').addEventListener('click', () => {
        if (selectedFiles.length > 0 || document.getElementById('progress-section').classList.contains('hidden') === false) {
            if (!confirm(currentLang === 'ru' ? 'Сбросить все настройки?' : 'Reset all settings?')) return;
        }
        selectedFiles = [];
        updateFileList();
        document.getElementById('url-input').value = '';
        document.getElementById('cb-diarization').checked = false;
        document.getElementById('diarization-backend').value = 'pyannote';
        document.getElementById('num-speakers').value = '';
        document.getElementById('num-speakers').disabled = true;
        document.querySelectorAll('.fmt-cb').forEach(cb => {
            cb.checked = cb.dataset.fmt === 'txt' || cb.dataset.fmt === 'txt_timecodes';
            cb.disabled = cb.dataset.fmt === 'txt_diarize' || cb.dataset.fmt === 'txt_diarize_timecodes';
        });
        document.getElementById('progress-section').classList.add('hidden');
        document.getElementById('log-panel').innerHTML = '';
        currentLogs = [];
        document.getElementById('folder-label').textContent = t('folderNone');
        document.getElementById('folder-label').style.color = 'var(--text-muted)';
        addLog(currentLang === 'ru' ? 'Все настройки сброшены' : 'All settings have been reset');
    });
}

function setupDeleteAllUserDataButton() {
    const btn = document.getElementById('btn-delete-all-user-data');
    if (!btn) return;

    btn.onclick = async () => {
        const firstConfirm = confirm(currentLang === 'ru' ? 'Удалить ВСЕ ваши задачи, загрузки и результаты транскрибации? Это действие нельзя отменить.' : 'Delete ALL your tasks, uploads and transcription results? This action cannot be undone.');
        if (!firstConfirm) return;

        const secondConfirm = confirm(currentLang === 'ru' ? 'Подтвердите повторно: все данные текущего пользователя будут удалены без возможности восстановления.' : 'Confirm again: all current user data will be deleted permanently.');
        if (!secondConfirm) return;

        btn.disabled = true;
        const originalText = btn.textContent;
        btn.textContent = currentLang === 'ru' ? 'Удаление...' : 'Deleting...';

        try {
            const res = await fetch(`${API}/tasks?status_filter=all`, {
                method: 'DELETE',
                credentials: 'same-origin',
            });

            if (!res.ok) {
                const detail = await readErrorDetail(res);
                console.error('delete all user data error:', detail);
                addLog(`${currentLang === 'ru' ? 'Ошибка удаления данных' : 'Error deleting data'}: ${detail}`, 'error');
                alert(`${currentLang === 'ru' ? 'Ошибка удаления данных' : 'Error deleting data'}: ${detail}`);
                return;
            }

            clearVisibleTaskState();
            startProgressStream();
            await loadResults();
            addLog(currentLang === 'ru' ? 'Все данные пользователя удалены' : 'All user data deleted', 'success');
        } catch (e) {
            console.error('delete all user data error:', e);
            addLog(`${currentLang === 'ru' ? 'Ошибка удаления данных' : 'Error deleting data'}: ${e}`, 'error');
            alert((currentLang === 'ru' ? 'Ошибка удаления данных: ' : 'Error deleting data: ') + e);
        } finally {
            btn.disabled = false;
            btn.textContent = originalText;
        }
    };
}

async function readErrorDetail(res) {
    try {
        const data = await res.json();
        return data.detail || JSON.stringify(data);
    } catch (e) {
        try {
            const text = await res.text();
            return text || `HTTP ${res.status}`;
        } catch (inner) {
            return `HTTP ${res.status}`;
        }
    }
}

function clearVisibleTaskState() {
    document.getElementById('results-list').innerHTML = `<p class="label-muted">${t('noResults')}</p>`;
    document.getElementById('progress-section').classList.add('hidden');
    document.getElementById('bar-total').style.width = '0%';
    document.getElementById('bar-total').textContent = '';
    document.getElementById('bar-file').style.width = '0%';
    document.getElementById('file-counter').textContent = '';
    document.getElementById('stage-label').textContent = '';
    document.getElementById('current-file').textContent = '';
    document.getElementById('status-label').textContent = t('ready');
    document.getElementById('log-panel').innerHTML = '';
    completedTaskNotified = new Set();
    currentLogs = [];
}

// ===== PROGRESS STREAM (SSE) =====

function startProgressStream() {
    if (progressSource) progressSource.close();
    progressSource = new EventSource(`${API}/progress`);

    progressSource.onmessage = (event) => {
        const data = JSON.parse(event.data);

        // Обновить задачи
        if (data.tasks) {
            for (const [tid, task] of Object.entries(data.tasks)) {
                updateTaskProgress(tid, task);
            }
        }

        // Добавить логи
        if (data.logs) {
            for (const [tid, logs] of Object.entries(data.logs)) {
                logs.forEach(line => addLog(line));
            }
        }
    };

    progressSource.onerror = () => {
        setTimeout(() => startProgressStream(), 3000);
    };
}

function updateTaskProgress(tid, task) {
    const bar = document.getElementById('bar-total');
    const fileBar = document.getElementById('bar-file');
    const counter = document.getElementById('file-counter');
    const stage = document.getElementById('stage-label');
    const currentFile = document.getElementById('current-file');
    const status = document.getElementById('status-label');

    const fileProgress = typeof task.file_progress === 'number'
        ? task.file_progress
        : task.progress;
    const isIndeterminate = Boolean(task.progress_indeterminate);

    if (task.status === 'processing' || task.status === 'pending' || task.status === 'downloading') {
        bar.style.width = task.progress + '%';
        bar.textContent = task.progress + '%';
        if (isIndeterminate) {
            fileBar.classList.add('progress-fill-indeterminate');
            fileBar.style.width = '100%';
            stage.textContent = `● ${task.stage}`;
        } else {
            fileBar.classList.remove('progress-fill-indeterminate');
            fileBar.style.width = fileProgress + '%';
            fileBar.textContent = fileProgress + '%';
            stage.textContent = `● ${task.stage} ${fileProgress}%`;
        }
        currentFile.textContent = task.filename.length > 60 ? '…' + task.filename.slice(-60) : task.filename;
        counter.textContent = task.filename;
        status.textContent = task.message;
    } else if (task.status === 'completed') {
        bar.style.width = '100%';
        bar.textContent = '100%';
        fileBar.style.width = '100%';
        stage.textContent = currentLang === 'ru' ? '✓ Готово' : '✓ Done';
        status.textContent = task.message;
        if (!completedTaskNotified.has(tid)) {
            completedTaskNotified.add(tid);
            addLog(currentLang === 'ru' ? `Готово: ${task.filename}` : `Done: ${task.filename}`, 'success');
            loadResults();
        }
    } else if (task.status === 'failed') {
        stage.textContent = currentLang === 'ru' ? '✕ Ошибка' : '✕ Error';
        status.textContent = task.message;
        addLog(`${currentLang === 'ru' ? 'Ошибка' : 'Error'}: ${task.filename} — ${task.message}`, 'error');
    }
}

// ===== LOG =====

function addLog(message, type = '') {
    const panel = document.getElementById('log-panel');
    const line = document.createElement('div');
    line.className = 'log-line' + (type ? ' log-line-' + type : '');
    const time = new Date().toLocaleTimeString('ru-RU');
    line.textContent = `[${time}] >> ${message}`;
    panel.appendChild(line);
    panel.scrollTop = panel.scrollHeight;
    currentLogs.push(message);
}

// ===== RESULTS =====

async function loadResults() {
    try {
        const res = await fetch(`${API}/tasks`);
        if (!res.ok) return;
        const data = await res.json();
        const completed = data.tasks.filter(t => t.status === 'completed' || t.status === 'failed');

        const list = document.getElementById('results-list');
        if (completed.length === 0) {
            list.innerHTML = `<p class="label-muted">${t('noResults')}</p>`;
            return;
        }

        list.innerHTML = completed.map(task => {
            const taskId = String(task.task_id);
            const htmlTaskId = escapeHtml(taskId);
            const jsTaskId = escapeJsString(taskId);
            const statusBadge = task.status === 'completed'
                ? `<span style="color: var(--success)">${t('done')}</span>`
                : `<span style="color: var(--danger)">${t('errorShort')}</span>`;
            const time = task.completed_at
                ? new Date(task.completed_at).toLocaleString('ru-RU')
                : '';
            const size = task.file_size ? formatSize(task.file_size) : '';

            let buttons = '<div class="result-actions">';
            if (task.status === 'completed') {
                const formats = task.output_formats || ['txt', 'txt_timecodes'];
                buttons += '<div class="result-files">';
                buttons += `<button class="result-view-btn" onclick="viewResult('${jsTaskId}')">${t('view')}</button>`;
                formats.forEach(fmt => {
                    const labels = {
                        'txt': currentLang === 'ru' ? 'Скачать .txt' : 'Download .txt',
                        'txt_timecodes': currentLang === 'ru' ? 'Скачать таймкоды' : 'Download timecodes',
                        'txt_diarize': currentLang === 'ru' ? 'Скачать диаризацию' : 'Download diarization',
                        'txt_diarize_timecodes': currentLang === 'ru' ? 'Скачать диар.+тайм.' : 'Download diar.+time',
                        'md': currentLang === 'ru' ? 'Скачать .md' : 'Download .md',
                        'srt': currentLang === 'ru' ? 'Скачать .srt' : 'Download .srt',
                        'vtt': currentLang === 'ru' ? 'Скачать .vtt' : 'Download .vtt',
                    };
                    const href = `${API}/tasks/${encodeURIComponent(taskId)}/download?format=${encodeURIComponent(fmt)}`;
                    buttons += `<a class="result-file-btn" href="${href}" download>${escapeHtml(labels[fmt] || fmt)}</a>`;
                });
                buttons += '</div>';
            }
            buttons += `<button class="result-delete-btn" onclick="deleteTask('${jsTaskId}')">${t('delete')}</button>`;
            buttons += '</div>';

            return `<div class="result-card">
                <div class="result-header">
                    <span class="result-filename">${escapeHtml(task.filename)}</span>
                    ${statusBadge}
                </div>
                <div class="result-meta">${time} ${size ? '· ' + size : ''} ${task.message ? '· ' + escapeHtml(task.message) : ''}</div>
                ${buttons}
                <div id="result-preview-${htmlTaskId}" class="hidden"></div>
            </div>`;
        }).join('');
    } catch (e) {
        console.error('loadResults error:', e);
    }
}

window.viewResult = async function(taskId) {
    const preview = document.getElementById(`result-preview-${taskId}`);
    if (!preview.classList.contains('hidden')) {
        preview.classList.add('hidden');
        preview.innerHTML = '';
        return;
    }

    preview.classList.remove('hidden');
    preview.innerHTML = `<p class="label-muted">${t('loading')}</p>`;

    try {
        const res = await fetch(`${API}/tasks/${taskId}/result`);
        if (res.ok) {
            const data = await res.json();
            if (data.result_files && data.result_files.length > 0) {
                let html = '';
                data.result_files.forEach((rf, i) => {
                    const label = {
                        'txt': currentLang === 'ru' ? 'Текст' : 'Text',
                        'txt_timecodes': currentLang === 'ru' ? 'Таймкоды' : 'Timecodes',
                        'txt_diarize': currentLang === 'ru' ? 'Диаризация' : 'Diarization',
                        'txt_diarize_timecodes': currentLang === 'ru' ? 'Диар.+тайм.' : 'Diar.+time',
                        'md': 'Markdown',
                        'srt': 'SRT',
                        'vtt': 'VTT',
                    }[rf.format] || rf.format;
                    html += `<h4 style="color:var(--accent);margin:8px 0 4px;">${escapeHtml(label)} (${escapeHtml(rf.name)})</h4>`;
                    html += `<div class="result-text-preview">${escapeHtml(rf.content)}</div>`;
                });
                preview.innerHTML = html;
            } else {
                preview.innerHTML = `<p class="label-muted">${t('noData')}</p>`;
            }
        }
    } catch (e) {
        preview.innerHTML = `<p class="label-muted">${currentLang === 'ru' ? 'Ошибка' : 'Error'}: ${escapeHtml(String(e))}</p>`;
    }
};

window.deleteTask = async function(taskId) {
    if (!confirm(currentLang === 'ru' ? 'Удалить задачу и результаты?' : 'Delete task and results?')) return;
    try {
        await fetch(`${API}/tasks/${taskId}`, { method: 'DELETE' });
        loadResults();
        addLog(currentLang === 'ru' ? 'Задача удалена' : 'Task deleted');
    } catch (e) {
        alert((currentLang === 'ru' ? 'Ошибка удаления: ' : 'Delete error: ') + e);
    }
};

// ===== LLM =====

function isTranscriptFile(filename) {
    const ext = filename.toLowerCase().substring(filename.lastIndexOf('.'));
    return ['.txt', '.md', '.srt', '.vtt'].includes(ext);
}

function updateLlmFileList() {
    const list = document.getElementById('llm-file-list');
    const count = document.getElementById('llm-files-count');
    if (selectedLlmFiles.length === 0) {
        list.classList.add('hidden');
        count.textContent = t('llmFilesNone');
        count.style.color = 'var(--text-muted)';
        return;
    }
    count.textContent = currentLang === 'ru' ? `Выбрано транскриптов: ${selectedLlmFiles.length}` : `Selected transcripts: ${selectedLlmFiles.length}`;
    count.style.color = 'var(--text)';
    list.classList.remove('hidden');
    list.innerHTML = selectedLlmFiles.map((f, i) => `
        <div class="file-item">
            <span class="file-item-name">${escapeHtml(f.name)}</span>
            <span class="file-item-size">${formatSize(f.size)}</span>
            <button class="file-item-remove" onclick="removeLlmFile(${i})">&times;</button>
        </div>
    `).join('');
}

window.removeLlmFile = function(idx) {
    selectedLlmFiles.splice(idx, 1);
    updateLlmFileList();
};

function updateLlmProviderFields() {
    const provider = document.getElementById('llm-provider').value;
    ['api', 'claude', 'codex', 'opencode', 'pi', 'other'].forEach(name => {
        document.getElementById(`llm-provider-${name}`).classList.add('hidden');
    });
    const map = { 'API': 'api', 'Claude Code': 'claude', 'Codex': 'codex', 'OpenCode': 'opencode', 'Pi': 'pi', 'Другое': 'other', 'Other': 'other' };
    const id = map[provider] || 'api';
    document.getElementById(`llm-provider-${id}`).classList.remove('hidden');
}

function setupLlmTab() {
    const llmInput = document.getElementById('llm-file-input');
    const llmDropZone = document.getElementById('llm-drop-zone');
    document.getElementById('btn-llm-select-files').addEventListener('click', () => llmInput.click());
    llmInput.addEventListener('change', (e) => {
        Array.from(e.target.files).filter(f => isTranscriptFile(f.name)).forEach(f => {
            if (!selectedLlmFiles.some(sf => sf.name === f.name && sf.size === f.size)) selectedLlmFiles.push(f);
        });
        updateLlmFileList();
        llmInput.value = '';
    });
    llmDropZone.addEventListener('dragover', (e) => { e.preventDefault(); llmDropZone.classList.add('dragover'); });
    llmDropZone.addEventListener('dragleave', () => llmDropZone.classList.remove('dragover'));
    llmDropZone.addEventListener('drop', (e) => {
        e.preventDefault();
        llmDropZone.classList.remove('dragover');
        Array.from(e.dataTransfer.files).filter(f => isTranscriptFile(f.name)).forEach(f => {
            if (!selectedLlmFiles.some(sf => sf.name === f.name && sf.size === f.size)) selectedLlmFiles.push(f);
        });
        updateLlmFileList();
    });
    document.getElementById('llm-provider').addEventListener('change', updateLlmProviderFields);
    updateLlmProviderFields();
    document.getElementById('llm-summary-prompt').value = `Ты аналитик встреч и голосовых сообщений. Сделай сильную, плотную и полезную выжимку транскрипта на русском языке.`;
    document.getElementById('llm-tasks-prompt').value = `Ты project manager assistant. Из транскрипта выдели только конкретные задачи и оформи их в максимально рабочем виде.`;
    document.getElementById('llm-provider').querySelector('option:last-child').textContent = currentLang === 'ru' ? 'Другое' : 'Other';
    document.getElementById('btn-llm-clear').addEventListener('click', () => {
        selectedLlmFiles = [];
        document.getElementById('llm-manual-text').value = '';
        document.getElementById('llm-result').textContent = '';
        document.getElementById('llm-result-actions').innerHTML = '';
        document.getElementById('llm-status').textContent = t('llmReady');
        updateLlmFileList();
    });
    document.getElementById('btn-llm-process').addEventListener('click', processLlm);
}

async function processLlm() {
    const formats = Array.from(document.querySelectorAll('.llm-fmt-cb:checked')).map(cb => cb.dataset.fmt);
    if (!formats.length) return alert(currentLang === 'ru' ? 'Выберите хотя бы один формат вывода' : 'Select at least one output format');
    const manualText = document.getElementById('llm-manual-text').value.trim();
    if (!selectedLlmFiles.length && !manualText) return alert(currentLang === 'ru' ? 'Выберите транскрипт или вставьте текст вручную' : 'Choose a transcript or paste text manually');
    if (!document.getElementById('llm-summary').checked && !document.getElementById('llm-tasks').checked && !document.getElementById('llm-custom').checked) {
        return alert(currentLang === 'ru' ? 'Выберите хотя бы один режим LLM-обработки' : 'Select at least one LLM mode');
    }
    if (document.getElementById('llm-custom').checked && !document.getElementById('llm-custom-prompt').value.trim()) {
        return alert(currentLang === 'ru' ? 'Для режима «Свой промпт» заполните пользовательский промпт' : 'Fill the custom prompt for custom mode');
    }

    const formData = new FormData();
    formData.append('provider', document.getElementById('llm-provider').value);
    formData.append('api_url', document.getElementById('llm-api-url').value);
    formData.append('api_key', document.getElementById('llm-api-key').value);
    formData.append('model', document.getElementById('llm-model').value);
    formData.append('temperature', document.getElementById('llm-temperature').value || '0.2');
    formData.append('claude_path', document.getElementById('llm-claude-path').value || 'claude');
    formData.append('claude_args', document.getElementById('llm-claude-args').value || '');
    formData.append('codex_path', document.getElementById('llm-codex-path').value || 'codex');
    formData.append('codex_args', document.getElementById('llm-codex-args').value || '');
    formData.append('opencode_path', document.getElementById('llm-opencode-path').value || 'opencode');
    formData.append('opencode_args', document.getElementById('llm-opencode-args').value || '');
    formData.append('pi_path', document.getElementById('llm-pi-path').value || 'pi');
    formData.append('pi_provider', document.getElementById('llm-pi-provider').value || '');
    formData.append('pi_args', document.getElementById('llm-pi-args').value || '');
    formData.append('other_path', document.getElementById('llm-other-path').value || '');
    formData.append('other_args', document.getElementById('llm-other-args').value || '');
    formData.append('summary_enabled', document.getElementById('llm-summary').checked);
    formData.append('tasks_enabled', document.getElementById('llm-tasks').checked);
    formData.append('custom_enabled', document.getElementById('llm-custom').checked);
    formData.append('summary_prompt', document.getElementById('llm-summary-prompt').value);
    formData.append('tasks_prompt', document.getElementById('llm-tasks-prompt').value);
    formData.append('custom_prompt', document.getElementById('llm-custom-prompt').value);
    formData.append('manual_text', manualText);
    formData.append('export_formats', formats.join(','));
    selectedLlmFiles.forEach(f => formData.append('transcript_files', f));

    document.getElementById('llm-status').textContent = currentLang === 'ru' ? 'Идёт LLM-обработка...' : 'LLM processing...';
    document.getElementById('btn-llm-process').disabled = true;
    try {
        const res = await fetch(`${API}/llm/process`, { method: 'POST', body: formData });
        const data = await res.json();
        if (!res.ok) throw new Error(friendlyError(data.detail || 'LLM error'));
        document.getElementById('llm-result').textContent = data.result_text || '';
        document.getElementById('llm-status').textContent = currentLang === 'ru' ? 'LLM-обработка завершена' : 'LLM processing completed';
        const links = (data.saved_files || []).map(file => `<a class="result-file-btn" href="${API}/llm/download/${data.job_id}/${encodeURIComponent(file.name)}" download>${escapeHtml(file.name)}</a>`).join('');
        document.getElementById('llm-result-actions').innerHTML = links;
    } catch (e) {
        const message = friendlyError(e.message || String(e));
        document.getElementById('llm-status').textContent = `${currentLang === 'ru' ? 'Ошибка LLM' : 'LLM error'}: ${message}`;
        document.getElementById('llm-result').textContent = message;
    } finally {
        document.getElementById('btn-llm-process').disabled = false;
    }
}

// ===== START =====

applyLanguage();
checkAuth();
