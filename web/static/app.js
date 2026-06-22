// ===== GigaAM v3 Web GUI =====

const API = '/api';
let authToken = null;
let selectedFiles = [];
let progressSource = null;
let currentLogs = [];

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
            errEl.textContent = data.detail || 'Ошибка входа';
        }
    } catch (err) {
        errEl.textContent = 'Ошибка соединения';
    }
});

document.getElementById('btn-logout').addEventListener('click', async () => {
    await fetch(`${API}/auth/logout`, { method: 'POST' });
    showLoginScreen();
});

// ===== INIT APP =====

async function initApp() {
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
        count.textContent = 'Файлы не выбраны';
        count.style.color = 'var(--text-muted)';
        return;
    }

    count.textContent = `Выбрано файлов: ${selectedFiles.length}`;
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
    const numSpeakers = document.getElementById('num-speakers');

    cb.addEventListener('change', () => {
        numSpeakers.disabled = !cb.checked;
        document.querySelectorAll('.fmt-cb[data-fmt="txt_diarize"], .fmt-cb[data-fmt="txt_diarize_timecodes"]').forEach(el => {
            el.disabled = !cb.checked;
        });
        addLog(cb.checked ? 'Диаризация: ВКЛЮЧЕНА' : 'Диаризация: ВЫКЛЮЧЕНА');
    });
}

// ===== FORMATS =====

function setupFormats() {
    document.querySelectorAll('.fmt-cb').forEach(cb => {
        cb.addEventListener('change', () => {
            const anyChecked = Array.from(document.querySelectorAll('.fmt-cb')).some(c => c.checked);
            if (!anyChecked) {
                document.querySelector('.fmt-cb[data-fmt="txt"]').checked = true;
                addLog('Предупреждение: выбран хотя бы один формат (txt)');
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
        if (!url) { alert('Введите ссылку'); return; }
        if (!url.startsWith('http://') && !url.startsWith('https://')) {
            alert('Ссылка должна начинаться с http:// или https://');
            return;
        }

        const formats = getSelectedFormats();
        const diar = document.getElementById('cb-diarization').checked;
        const ns = document.getElementById('num-speakers').value;

        const formData = new FormData();
        formData.append('url', url);
        formData.append('output_formats', formats.join(','));
        formData.append('enable_diarization', diar);
        formData.append('num_speakers', ns);

        document.getElementById('btn-download-url').disabled = true;
        document.getElementById('download-progress').classList.remove('hidden');
        document.getElementById('download-bar').style.width = '0%';
        document.getElementById('download-pct').textContent = '0%';

        addLog(`Загрузка по ссылке: ${url}`);

        try {
            const res = await fetch(`${API}/download-url`, { method: 'POST', body: formData });
            if (res.ok) {
                const data = await res.json();
                addLog(`Загрузка началась (task: ${data.task_id.substring(0, 8)}...)`);
                document.getElementById('url-input').value = '';
            } else {
                const err = await res.json();
                addLog(`Ошибка: ${err.detail}`, 'error');
            }
        } catch (e) {
            addLog(`Ошибка: ${e}`, 'error');
        }

        document.getElementById('btn-download-url').disabled = false;
    });
}

// ===== START PROCESSING =====

function setupStartButton() {
    document.getElementById('btn-start').addEventListener('click', async () => {
        if (selectedFiles.length === 0) {
            alert('Выберите хотя бы один файл!');
            return;
        }

        const formats = getSelectedFormats();
        const diar = document.getElementById('cb-diarization').checked;
        const ns = document.getElementById('num-speakers').value;

        const formData = new FormData();
        selectedFiles.forEach(f => formData.append('files', f));
        formData.append('output_formats', formats.join(','));
        formData.append('enable_diarization', diar);
        formData.append('num_speakers', ns);

        document.getElementById('btn-start').disabled = true;
        document.getElementById('btn-start').textContent = 'ЗАГРУЗКА...';
        document.getElementById('progress-section').classList.remove('hidden');

        addLog(`Запуск обработки: ${selectedFiles.length} файлов, форматы: ${formats.join(', ')}`);
        if (diar) addLog(`Диаризация: ВКЛЮЧЕНА${ns ? ', спикеров: ' + ns : ', авто'}`);

        try {
            const res = await fetch(`${API}/upload`, { method: 'POST', body: formData });
            if (res.ok) {
                const data = await res.json();
                addLog(`Загружено ${data.total} файлов для обработки`);
                selectedFiles = [];
                updateFileList();
                document.getElementById('folder-label').textContent = 'Папка не выбрана';
                document.getElementById('folder-label').style.color = 'var(--text-muted)';
            } else {
                const err = await res.json();
                addLog(`Ошибка: ${err.detail}`, 'error');
            }
        } catch (e) {
            addLog(`Ошибка: ${e}`, 'error');
        }

        document.getElementById('btn-start').disabled = false;
        document.getElementById('btn-start').textContent = 'ЗАПУСТИТЬ ОБРАБОТКУ';
    });
}

// ===== CLEAR =====

function setupClearButton() {
    document.getElementById('btn-clear').addEventListener('click', () => {
        if (selectedFiles.length > 0 || document.getElementById('progress-section').classList.contains('hidden') === false) {
            if (!confirm('Сбросить все настройки?')) return;
        }
        selectedFiles = [];
        updateFileList();
        document.getElementById('url-input').value = '';
        document.getElementById('cb-diarization').checked = false;
        document.getElementById('num-speakers').value = '';
        document.getElementById('num-speakers').disabled = true;
        document.querySelectorAll('.fmt-cb').forEach(cb => {
            cb.checked = cb.dataset.fmt === 'txt' || cb.dataset.fmt === 'txt_timecodes';
            cb.disabled = cb.dataset.fmt === 'txt_diarize' || cb.dataset.fmt === 'txt_diarize_timecodes';
        });
        document.getElementById('progress-section').classList.add('hidden');
        document.getElementById('log-panel').innerHTML = '';
        currentLogs = [];
        document.getElementById('folder-label').textContent = 'Папка не выбрана';
        document.getElementById('folder-label').style.color = 'var(--text-muted)';
        addLog('Все настройки сброшены');
    });
}

function setupDeleteAllUserDataButton() {
    const btn = document.getElementById('btn-delete-all-user-data');
    if (!btn) return;

    btn.onclick = async () => {
        const firstConfirm = confirm('Удалить ВСЕ ваши задачи, загрузки и результаты транскрибации? Это действие нельзя отменить.');
        if (!firstConfirm) return;

        const secondConfirm = confirm('Подтвердите повторно: все данные текущего пользователя будут удалены без возможности восстановления.');
        if (!secondConfirm) return;

        btn.disabled = true;
        const originalText = btn.textContent;
        btn.textContent = 'Удаление...';

        try {
            const res = await fetch(`${API}/tasks?status_filter=all`, {
                method: 'DELETE',
                credentials: 'same-origin',
            });

            if (!res.ok) {
                const detail = await readErrorDetail(res);
                console.error('delete all user data error:', detail);
                addLog(`Ошибка удаления данных: ${detail}`, 'error');
                alert(`Ошибка удаления данных: ${detail}`);
                return;
            }

            clearVisibleTaskState();
            startProgressStream();
            await loadResults();
            addLog('Все данные пользователя удалены', 'success');
        } catch (e) {
            console.error('delete all user data error:', e);
            addLog(`Ошибка удаления данных: ${e}`, 'error');
            alert('Ошибка удаления данных: ' + e);
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
    document.getElementById('results-list').innerHTML = '<p class="label-muted">Нет завершённых задач</p>';
    document.getElementById('progress-section').classList.add('hidden');
    document.getElementById('bar-total').style.width = '0%';
    document.getElementById('bar-total').textContent = '';
    document.getElementById('bar-file').style.width = '0%';
    document.getElementById('file-counter').textContent = '';
    document.getElementById('stage-label').textContent = '';
    document.getElementById('current-file').textContent = '';
    document.getElementById('status-label').textContent = 'Готов к работе';
    document.getElementById('log-panel').innerHTML = '';
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

    if (task.status === 'processing' || task.status === 'pending' || task.status === 'downloading') {
        bar.style.width = task.progress + '%';
        bar.textContent = task.progress + '%';
        fileBar.style.width = task.progress + '%';
        stage.textContent = `● ${task.stage} ${task.progress}%`;
        currentFile.textContent = task.filename.length > 60 ? '…' + task.filename.slice(-60) : task.filename;
        counter.textContent = task.filename;
        status.textContent = task.message;
    } else if (task.status === 'completed') {
        bar.style.width = '100%';
        bar.textContent = '100%';
        fileBar.style.width = '100%';
        stage.textContent = '✓ Готово';
        status.textContent = task.message;
        addLog(`Готово: ${task.filename}`, 'success');
        loadResults();
    } else if (task.status === 'failed') {
        stage.textContent = '✕ Ошибка';
        status.textContent = task.message;
        addLog(`Ошибка: ${task.filename} — ${task.message}`, 'error');
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
            list.innerHTML = '<p class="label-muted">Нет завершённых задач</p>';
            return;
        }

        list.innerHTML = completed.map(task => {
            const taskId = String(task.task_id);
            const htmlTaskId = escapeHtml(taskId);
            const jsTaskId = escapeJsString(taskId);
            const statusBadge = task.status === 'completed'
                ? '<span style="color: var(--success)">✓ Готово</span>'
                : '<span style="color: var(--danger)">✕ Ошибка</span>';
            const time = task.completed_at
                ? new Date(task.completed_at).toLocaleString('ru-RU')
                : '';
            const size = task.file_size ? formatSize(task.file_size) : '';

            let buttons = '<div class="result-actions">';
            if (task.status === 'completed') {
                const formats = task.output_formats || ['txt', 'txt_timecodes'];
                buttons += '<div class="result-files">';
                buttons += `<button class="result-view-btn" onclick="viewResult('${jsTaskId}')">Просмотреть</button>`;
                formats.forEach(fmt => {
                    const labels = {
                        'txt': 'Скачать .txt',
                        'txt_timecodes': 'Скачать таймкоды',
                        'txt_diarize': 'Скачать диаризацию',
                        'txt_diarize_timecodes': 'Скачать диар.+тайм.',
                        'md': 'Скачать .md',
                        'srt': 'Скачать .srt',
                        'vtt': 'Скачать .vtt',
                    };
                    const href = `${API}/tasks/${encodeURIComponent(taskId)}/download?format=${encodeURIComponent(fmt)}`;
                    buttons += `<a class="result-file-btn" href="${href}" download>${escapeHtml(labels[fmt] || fmt)}</a>`;
                });
                buttons += '</div>';
            }
            buttons += `<button class="result-delete-btn" onclick="deleteTask('${jsTaskId}')">Удалить</button>`;
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
    preview.innerHTML = '<p class="label-muted">Загрузка...</p>';

    try {
        const res = await fetch(`${API}/tasks/${taskId}/result`);
        if (res.ok) {
            const data = await res.json();
            if (data.result_files && data.result_files.length > 0) {
                let html = '';
                data.result_files.forEach((rf, i) => {
                    const label = {
                        'txt': 'Текст',
                        'txt_timecodes': 'Таймкоды',
                        'txt_diarize': 'Диаризация',
                        'txt_diarize_timecodes': 'Диар.+тайм.',
                        'md': 'Markdown',
                        'srt': 'SRT',
                        'vtt': 'VTT',
                    }[rf.format] || rf.format;
                    html += `<h4 style="color:var(--accent);margin:8px 0 4px;">${escapeHtml(label)} (${escapeHtml(rf.name)})</h4>`;
                    html += `<div class="result-text-preview">${escapeHtml(rf.content)}</div>`;
                });
                preview.innerHTML = html;
            } else {
                preview.innerHTML = '<p class="label-muted">Нет данных</p>';
            }
        }
    } catch (e) {
        preview.innerHTML = `<p class="label-muted">Ошибка: ${escapeHtml(String(e))}</p>`;
    }
};

window.deleteTask = async function(taskId) {
    if (!confirm('Удалить задачу и результаты?')) return;
    try {
        await fetch(`${API}/tasks/${taskId}`, { method: 'DELETE' });
        loadResults();
        addLog('Задача удалена');
    } catch (e) {
        alert('Ошибка удаления: ' + e);
    }
};

// ===== START =====

checkAuth();
