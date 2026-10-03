// Исполняет настоящий web/static/app.js на заглушке DOM и печатает наблюдения JSON-ом.
//
// Запуск: node web_frontend_harness.js <static_dir> <scenario>. Без зависимостей (только
// fs/vm), чтобы pytest мог гонять сценарии там, где есть node; утверждения — в
// tests/test_web_frontend.py. Заглушка моделирует ровно то, что трогает app.js:
// getElementById/querySelector(All), слушатели, classList, <select> из index.html,
// fetch, EventSource, FormData и таймеры (ручные, чтобы проверять debounce).
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const [staticDir, scenario] = process.argv.slice(2);
const APP_JS = fs.readFileSync(path.join(staticDir, 'app.js'), 'utf8');
const INDEX_HTML = fs.readFileSync(path.join(staticDir, 'index.html'), 'utf8');

function escapeText(text) {
    return String(text).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

class ClassList {
    constructor() { this.set = new Set(); }
    add(...names) { names.forEach(n => this.set.add(n)); }
    remove(...names) { names.forEach(n => this.set.delete(n)); }
    contains(name) { return this.set.has(name); }
    toggle(name, force) {
        const on = force === undefined ? !this.set.has(name) : force;
        if (on) this.set.add(name); else this.set.delete(name);
        return on;
    }
}

class El {
    constructor(tag = 'div', id = '') {
        this.tagName = tag.toUpperCase();
        this.id = id;
        this.listeners = {};
        this.classList = new ClassList();
        this.style = {};
        this.dataset = {};
        this.children = [];
        this._text = '';
        this._html = '';
        this._value = '';
        this.checked = false;
        this.disabled = false;
        this.placeholder = '';
        this.title = '';
        this.scrollTop = 0;
        this.scrollHeight = 0;
        this.files = [];
        this.webkitRelativePath = '';
    }
    get textContent() { return this._text; }
    set textContent(value) { this._text = String(value); this._html = escapeText(value); this.children = []; }
    get innerHTML() { return this._html; }
    set innerHTML(value) { this._html = String(value); this._text = String(value).replace(/<[^>]*>/g, ''); this.children = []; }
    get value() { return this._value; }
    set value(v) { this._value = String(v); }
    addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); }
    listenerCount(type) { return (this.listeners[type] || []).length; }
    dispatch(type, extra = {}) {
        const event = { type, target: this, preventDefault() {}, ...extra };
        const handlers = [...(this.listeners[type] || [])];
        const prop = this['on' + type];
        if (typeof prop === 'function') handlers.push(prop);
        return handlers.map(fn => fn.call(this, event));
    }
    appendChild(child) { this.children.push(child); return child; }
    querySelector(selector) {
        if (this.options && selector === 'option:last-child') return this.options[this.options.length - 1] || null;
        const byValue = /^option\[value="([^"]*)"\]$/.exec(selector);
        if (this.options && byValue) return this.options.find(o => o.getAttribute('value') === byValue[1]) || null;
        return new El();
    }
    querySelectorAll() { return []; }
    click() { this.dispatch('click'); }
    getAttribute(name) { return name === 'value' ? this._attrValue : undefined; }
    setAttribute(name, value) { if (name === 'value') this._attrValue = String(value); }
}

class OptionEl extends El {
    constructor(text, value) {
        super('option');
        this.textContent = text;
        this._attrValue = value;  // undefined, если в разметке нет value=
    }
    // Как в браузере: без атрибута value значение option — его текст
    get value() { return this._attrValue !== undefined ? this._attrValue : this.textContent.trim(); }
    set value(v) { this._attrValue = String(v); }
}

class SelectEl extends El {
    constructor(id, options) {
        super('select', id);
        this.options = options;
        this.selectedIndex = 0;
    }
    get value() { return this.options[this.selectedIndex] ? this.options[this.selectedIndex].value : ''; }
    set value(v) {
        const index = this.options.findIndex(o => o.value === String(v));
        this.selectedIndex = index;
    }
    set innerHTML(value) { super.innerHTML = value; this.options = []; }
    get innerHTML() { return super.innerHTML; }
    appendChild(child) { this.options.push(child); return child; }
}

function providerSelectFromHtml() {
    const match = /<select id="llm-provider"[^>]*>([\s\S]*?)<\/select>/.exec(INDEX_HTML);
    if (!match) throw new Error('llm-provider select not found in index.html');
    const options = [];
    const re = /<option(?:\s+value="([^"]*)")?\s*>([^<]*)<\/option>/g;
    let m;
    while ((m = re.exec(match[1])) !== null) options.push(new OptionEl(m[2], m[1]));
    return new SelectEl('llm-provider', options);
}

// ---------- окружение ----------

const elements = new Map();
const queryAll = {};  // selector -> [El], для querySelectorAll, которые нужны сценарию
const document = {
    title: '',
    documentElement: new El('html'),
    body: new El('body'),
    getElementById(id) {
        if (!elements.has(id)) elements.set(id, id === 'llm-provider' ? providerSelectFromHtml() : new El('div', id));
        return elements.get(id);
    },
    querySelector() { return new El(); },
    querySelectorAll(selector) { return queryAll[selector] || []; },
    createElement(tag) { return tag === 'option' ? new OptionEl('', undefined) : new El(tag); },
};

let timers = [];
let timerSeq = 0;
function setTimeoutStub(fn, ms) { timerSeq += 1; timers.push({ id: timerSeq, fn, ms }); return timerSeq; }
function clearTimeoutStub(id) { timers = timers.filter(t => t.id !== id); }
function flushTimers() {
    // Только уже стоящие таймеры: перезапуск из колбэка не зацикливает
    const due = timers;
    timers = [];
    due.forEach(t => t.fn());
    return due.length;
}

const eventSources = [];
class EventSourceStub {
    constructor(url) { this.url = url; this.closed = false; this.onmessage = null; this.onerror = null; eventSources.push(this); }
    close() { this.closed = true; }
    emit(payload) { if (this.onmessage) this.onmessage({ data: JSON.stringify(payload) }); }
}

class FormDataStub {
    constructor() { this.entries = []; }
    append(key, value) { this.entries.push([key, value]); }
    get(key) { const e = this.entries.find(([k]) => k === key); return e ? e[1] : null; }
}

const fetchCalls = [];
const routes = {};  // "METHOD path" -> {status, json?, text?}
function response(spec) {
    const status = spec.status || 200;
    return {
        ok: status >= 200 && status < 300,
        status,
        async json() {
            if (spec.json === undefined) throw new SyntaxError('Unexpected token < in JSON');
            return JSON.parse(JSON.stringify(spec.json));
        },
        async text() { return spec.text !== undefined ? spec.text : JSON.stringify(spec.json); },
    };
}
async function fetchStub(url, opts = {}) {
    const method = (opts.method || 'GET').toUpperCase();
    const pathOnly = String(url).split('?')[0];
    fetchCalls.push({ method, url: String(url), body: opts.body || null });
    const spec = routes[`${method} ${url}`] || routes[`${method} ${pathOnly}`] || { status: 404, json: { detail: 'not routed' } };
    return response(typeof spec === 'function' ? spec() : spec);
}

const alerts = [];
const storage = {};
const sandbox = {
    document,
    localStorage: { getItem: k => (k in storage ? storage[k] : null), setItem: (k, v) => { storage[k] = String(v); } },
    fetch: fetchStub,
    EventSource: EventSourceStub,
    FormData: FormDataStub,
    alert: msg => alerts.push(String(msg)),
    confirm: () => true,
    console: { log() {}, warn() {}, error() {} },
    setTimeout: setTimeoutStub,
    clearTimeout: clearTimeoutStub,
};
sandbox.window = sandbox;
vm.createContext(sandbox);

async function settle() {
    for (let i = 0; i < 50; i += 1) await new Promise(resolve => setImmediate(resolve));
}

function logLines() {
    return document.getElementById('log-panel').children.map(c => c.textContent);
}

function count(method, pathPrefix) {
    return fetchCalls.filter(c => c.method === method && c.url.startsWith(pathPrefix)).length;
}

function defaultRoutes() {
    routes['GET /api/auth/check'] = { json: { ok: true, username: 'alice' } };
    routes['POST /api/auth/login'] = { json: { ok: true, username: 'alice' } };
    routes['POST /api/auth/logout'] = { json: { ok: true } };
    routes['GET /api/device'] = { json: { device: 'CPU' } };
    routes['GET /api/tasks'] = { json: { total: 0, tasks: [] } };
    routes['GET /api/asr-options'] = { json: { backends: ['auto'], defaults: { asr_backend: 'auto', asr_model: 'v3_e2e_rnnt', onnx_provider: 'auto' } } };
    routes['GET /api/llm/tools'] = { json: { providers: [], tools: [] } };
    routes['POST /api/upload'] = { json: { tasks: [], total: 1 } };
}

async function boot() {
    defaultRoutes();
    vm.runInContext(APP_JS, sandbox, { filename: 'app.js' });
    await settle();
}

function task(filename, status, message = '') {
    return { status, progress: status === 'completed' ? 100 : 10, file_progress: 0, stage: '', message, filename };
}

// ---------- сценарии ----------

const scenarios = {
    // B3: повторный вход без перезагрузки не должен дублировать слушатели
    async relogin() {
        await boot();
        const firstStream = eventSources[eventSources.length - 1];
        document.getElementById('btn-logout').dispatch('click');
        await settle();
        const streamsAfterLogout = eventSources.filter(s => !s.closed).length;
        if (firstStream.onerror) firstStream.onerror();  // ошибка уже закрытого потока
        flushTimers();
        const reconnectsAfterLogout = eventSources.filter(s => !s.closed).length;

        document.getElementById('login-form').dispatch('submit');
        await settle();
        document.getElementById('file-input').dispatch('change', { target: { files: [{ name: 'a.wav', size: 10, webkitRelativePath: '' }], value: '' } });
        document.getElementById('btn-start').dispatch('click');
        await settle();
        return {
            startListeners: document.getElementById('btn-start').listenerCount('click'),
            uploads: count('POST', '/api/upload'),
            streamsAfterLogout,
            reconnectsAfterLogout,
            openStreamsAfterLogin: eventSources.filter(s => !s.closed).length,
        };
    },

    // B2: значения <option> провайдера не зависят от языка и бейджей сканирования
    async provider_values() {
        await boot();
        routes['GET /api/llm/tools'] = { json: { providers: [], tools: [
            { id: 'omp', provider: 'oh-my-pi', status: 'found', path: '/x/omp', version: '18.2', detail: null, install_hint: '' },
            { id: 'codex', provider: 'Codex', status: 'missing', path: null, version: null, detail: null, install_hint: 'npm i' },
        ] } };
        document.getElementById('btn-llm-rescan').dispatch('click');
        await settle();
        const select = document.getElementById('llm-provider');
        const ru = select.options.map(o => ({ value: o.value, text: o.textContent }));
        document.getElementById('btn-lang').dispatch('click');
        await settle();
        const en = select.options.map(o => ({ value: o.value, text: o.textContent }));

        select.value = 'oh-my-pi';
        document.getElementById('llm-manual-text').value = 'текст';
        document.getElementById('llm-summary').checked = true;
        const txt = new El('input'); txt.dataset.fmt = 'txt';
        queryAll['.llm-fmt-cb:checked'] = [txt];
        routes['POST /api/llm/process'] = { json: { job_id: 'j', provider: 'oh-my-pi', result_text: 'ok', saved_files: [] } };
        document.getElementById('btn-llm-process').dispatch('click');
        await settle();
        const sent = fetchCalls.find(c => c.url === '/api/llm/process');
        return { ru, en, sentProvider: sent ? sent.body.get('provider') : null };
    },

    // B6: первое сообщение потока — снимок, а не события; loadResults с debounce
    async sse_baseline() {
        await boot();
        const stream = eventSources[eventSources.length - 1];
        const tasksBefore = count('GET', '/api/tasks');
        const linesBefore = logLines().length;
        stream.emit({ snapshot: true, tasks: { t1: task('old.wav', 'completed', 'ok'), t2: task('bad.wav', 'failed', 'boom') }, logs: {} });
        flushTimers();
        await settle();
        const afterSnapshot = { logs: logLines().length - linesBefore, taskFetches: count('GET', '/api/tasks') - tasksBefore };

        stream.emit({ tasks: { t3: task('new1.wav', 'completed', 'ok'), t4: task('new2.wav', 'completed', 'ok') }, logs: { t3: ['line'] } });
        stream.emit({ tasks: { t5: task('new3.wav', 'failed', 'oops') }, logs: {} });
        flushTimers();
        await settle();
        const afterDelta = { lines: logLines(), taskFetches: count('GET', '/api/tasks') - tasksBefore };

        // Переподключение: новый поток снова начинается со снимка, где t3/t5 уже есть
        if (stream.onerror) stream.onerror();
        flushTimers();
        await settle();
        const reconnected = eventSources[eventSources.length - 1];
        const linesBeforeReplay = logLines().length;
        reconnected.emit({ snapshot: true, tasks: { t3: task('new1.wav', 'completed', 'ok'), t5: task('new3.wav', 'failed', 'oops') }, logs: {} });
        flushTimers();
        await settle();
        return {
            afterSnapshot,
            afterDelta,
            newStreamOnReconnect: reconnected !== stream,
            replayedLines: logLines().length - linesBeforeReplay,
        };
    },

    // Поля CLI, которые сервер игнорирует, отключены
    async server_cli_policy() {
        await boot();
        routes['GET /api/llm/tools'] = { json: { providers: [], tools: [], client_cli: false } };
        document.getElementById('btn-llm-rescan').dispatch('click');
        await settle();
        const locked = ['llm-claude-path', 'llm-other-path', 'llm-other-args', 'llm-allow-tools']
            .map(id => document.getElementById(id).disabled);
        routes['GET /api/llm/tools'] = { json: { providers: [], tools: [], client_cli: true } };
        document.getElementById('btn-llm-rescan').dispatch('click');
        await settle();
        return {
            locked,
            unlocked: document.getElementById('llm-claude-path').disabled === false,
            apiKeyEditable: document.getElementById('llm-api-key').disabled === false,
        };
    },

    // Текст сервера в списке инструментов — только экранированным
    async tools_escaped() {
        await boot();
        routes['GET /api/llm/tools'] = { json: { providers: [], tools: [
            { id: 'x', provider: '<b>evil</b>', status: 'broken', path: '/p', version: null, detail: '<img src=x onerror=alert(1)>', install_hint: '' },
        ] } };
        document.getElementById('btn-llm-rescan').dispatch('click');
        await settle();
        return { html: document.getElementById('llm-tools-list').children.map(li => li.innerHTML) };
    },

    // Ошибки HTTP показываются, а не теряются
    async error_paths() {
        await boot();
        routes['GET /api/tasks/t1/result'] = { status: 404, json: { detail: 'Задача не найдена' } };
        document.getElementById('result-preview-t1').classList.add('hidden');
        await sandbox.viewResult('t1');
        const preview = document.getElementById('result-preview-t1').innerHTML;

        routes['DELETE /api/tasks/t2'] = { status: 400, json: { detail: 'Нельзя удалить задачу в процессе обработки' } };
        const linesBefore = logLines().length;
        await sandbox.deleteTask('t2');
        const deleteLines = logLines().slice(linesBefore);

        routes['POST /api/llm/process'] = { status: 502, text: '<html>Bad Gateway</html>' };
        document.getElementById('llm-manual-text').value = 'текст';
        document.getElementById('llm-summary').checked = true;
        const txt = new El('input'); txt.dataset.fmt = 'txt';
        queryAll['.llm-fmt-cb:checked'] = [txt];
        document.getElementById('btn-llm-process').dispatch('click');
        await settle();
        return {
            preview,
            deleteAlerts: alerts.slice(),
            deleteLines,
            llmStatus: document.getElementById('llm-status').textContent,
        };
    },
};

(async () => {
    const run = scenarios[scenario];
    if (!run) throw new Error(`unknown scenario ${scenario}`);
    const result = await run();
    process.stdout.write(JSON.stringify(result));
})().catch(err => {
    process.stderr.write(String(err && err.stack || err));
    process.exit(1);
});
