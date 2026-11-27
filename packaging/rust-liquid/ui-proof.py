#!/usr/bin/env python3
"""Exercise an installed Rust Liquid candidate through its real UI.

The Python process is only the CI test controller: it does not provide an ASR
backend to the application. tauri-driver launches the packaged application;
WebDriver clicks its controls, and the OS file chooser is driven separately.
By default a fresh app-data directory forces the app to download its own model.
Requires tauri-driver, the platform WebDriver, and an interactive desktop. On
Linux also requires Xvfb, xdotool and a window manager; on Windows pywinauto.
"""

import argparse
import base64
import ctypes
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.request

from contextlib import contextmanager
from pathlib import Path


ELEMENT = "element-6066-11e4-a52e-4f735466cecf"


def wait_for(description, predicate, seconds, interval=0.25):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    raise RuntimeError(f"Timed out waiting for {description} ({seconds}s)")

@contextmanager
def proof_temporary_directory():
    temporary = tempfile.TemporaryDirectory(prefix="rust-liquid-ui-", dir=os.environ.get("RUNNER_TEMP"))
    try:
        yield temporary.name
    except BaseException as original:
        try:
            temporary.cleanup()
        except Exception as error:
            print(f"Temporary directory cleanup also failed ({temporary.name}): {error}",
                  file=sys.stderr, flush=True)
            original.add_note(f"Temporary directory cleanup failed: {error}")
        raise
    else:
        temporary.cleanup()



def check_display():
    if sys.platform == "win32":
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.OpenInputDesktop.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        user32.OpenInputDesktop.restype = ctypes.c_void_p
        user32.CloseDesktop.argtypes = [ctypes.c_void_p]
        desktop = user32.OpenInputDesktop(0, False, 0x0001)  # DESKTOP_READOBJECTS
        if not desktop:
            raise RuntimeError(
                "Windows runner has no accessible input desktop; "
                f"OpenInputDesktop failed with Win32 error {ctypes.get_last_error()}"
            )
        user32.GetThreadDesktop.argtypes = [ctypes.c_ulong]
        user32.GetThreadDesktop.restype = ctypes.c_void_p
        user32.GetUserObjectInformationW.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p,
            ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong),
        ]

        def desktop_name(handle):
            size = ctypes.c_ulong()
            user32.GetUserObjectInformationW(handle, 2, None, 0, ctypes.byref(size))
            if not size.value:
                raise RuntimeError(f"Cannot read Windows desktop name: Win32 error {ctypes.get_last_error()}")
            buffer = ctypes.create_unicode_buffer(size.value // ctypes.sizeof(ctypes.c_wchar))
            if not user32.GetUserObjectInformationW(handle, 2, buffer, size.value, ctypes.byref(size)):
                raise RuntimeError(f"Cannot read Windows desktop name: Win32 error {ctypes.get_last_error()}")
            return buffer.value

        try:
            input_name = desktop_name(desktop)
            current = user32.GetThreadDesktop(ctypes.windll.kernel32.GetCurrentThreadId())
            thread_name = desktop_name(current)
            if input_name.casefold() != "default" or thread_name.casefold() != input_name.casefold():
                raise RuntimeError(
                    f"Windows runner is not on the active Default desktop: "
                    f"input={input_name!r}, runner={thread_name!r}"
                )
        finally:
            user32.CloseDesktop(desktop)
        if user32.GetSystemMetrics(0) <= 0 or user32.GetSystemMetrics(1) <= 0:
            raise RuntimeError("Windows input desktop has no usable display dimensions")
        print("Windows input desktop is accessible; native dialogs will also be checked.", flush=True)
    elif sys.platform.startswith("linux"):
        if not os.environ.get("DISPLAY"):
            raise RuntimeError("Linux runner has no DISPLAY; an Xvfb session is required")
        if not shutil.which("xdotool") or not shutil.which("xdpyinfo"):
            raise RuntimeError("Linux UI proof requires xdotool and xdpyinfo")
        result = subprocess.run(["xdpyinfo"], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=10)
        if result.returncode:
            raise RuntimeError(f"Linux X display {os.environ['DISPLAY']} is inaccessible: {result.stderr.decode(errors='replace')}")
        print(f"Linux X display {os.environ['DISPLAY']} is accessible.", flush=True)
    else:
        raise RuntimeError(f"Unsupported proof platform: {sys.platform}")

class WebDriverError(RuntimeError):
    def __init__(self, method, path, value):
        self.code = value["error"]
        super().__init__(f"WebDriver {method} {path}: {json.dumps(value, ensure_ascii=False)}")



class WebDriver:
    def __init__(self, app, native_driver, data_dir, log_file):
        executable = shutil.which("tauri-driver")
        if not executable:
            raise RuntimeError("tauri-driver is not on PATH")
        env = os.environ.copy()
        if sys.platform == "win32":
            env["APPDATA"] = str(data_dir / "roaming")
            env["LOCALAPPDATA"] = str(data_dir / "local")
            Path(env["APPDATA"]).mkdir(parents=True, exist_ok=True)
            Path(env["LOCALAPPDATA"]).mkdir(parents=True, exist_ok=True)
        else:
            for name, suffix in (("XDG_DATA_HOME", "data"), ("XDG_CONFIG_HOME", "config"), ("XDG_CACHE_HOME", "cache")):
                env[name] = str(data_dir / suffix)
                Path(env[name]).mkdir(parents=True, exist_ok=True)
            env["GDK_BACKEND"] = "x11"
            env["WEBKIT_DISABLE_DMABUF_RENDERER"] = "1"
            env["LIBGL_ALWAYS_SOFTWARE"] = "1"
        self.log = open(log_file, "w", encoding="utf-8")
        self.process = subprocess.Popen(
            [executable, "--native-driver", native_driver],
            stdout=self.log, stderr=subprocess.STDOUT, env=env,
            start_new_session=sys.platform.startswith("linux"),
        )
        self.session = None
        self.app = app

    def request(self, method, path, payload=None, timeout=15):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"http://127.0.0.1:4444{path}", data=data, method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                result = json.load(response)
        except urllib.error.HTTPError as error:
            body = error.read().decode(errors="replace")
            try:
                value = json.loads(body).get("value")
            except ValueError:
                raise RuntimeError(f"WebDriver {method} {path}: {body}") from error
            if isinstance(value, dict) and value.get("error"):
                raise WebDriverError(method, path, value) from error
            raise RuntimeError(f"WebDriver {method} {path}: {body}") from error
        value = result.get("value")
        if isinstance(value, dict) and value.get("error"):
            raise WebDriverError(method, path, value)
        return value

    def start(self):
        def ready():
            if self.process.poll() is not None:
                raise RuntimeError(f"tauri-driver exited with code {self.process.returncode}; see driver.log")
            try:
                return self.request("GET", "/status", timeout=2).get("ready")
            except (OSError, RuntimeError):
                return None

        wait_for("tauri-driver and native WebDriver", ready, 30)
        value = self.request("POST", "/session", {"capabilities": {"alwaysMatch": {
            "browserName": "wry", "tauri:options": {"application": str(self.app)},
        }}}, timeout=120)
        self.session = value["sessionId"]

    def command(self, method, suffix, payload=None, timeout=15):
        return self.request(method, f"/session/{self.session}{suffix}", payload, timeout)

    def element(self, selector):
        return self.command("POST", "/element", {"using": "css selector", "value": selector})[ELEMENT]

    def click(self, selector):
        self.command("POST", f"/element/{self.element(selector)}/click", {})

    def text(self, selector):
        return self.command("GET", f"/element/{self.element(selector)}/text")

    def property(self, selector, name):
        return self.command("GET", f"/element/{self.element(selector)}/property/{name}")

    def visible_text(self, selector):
        # Read rendered text and layout without changing the page. WebKit's
        # WebDriver displayed/text endpoints misreport these overflow-clipped
        # selected-file elements even while they are visible.
        script = """
            const element = arguments[0];
            const rect = element.getBoundingClientRect();
            let left = Math.max(0, rect.left), top = Math.max(0, rect.top);
            let right = Math.min(window.innerWidth, rect.right);
            let bottom = Math.min(window.innerHeight, rect.bottom);
            let visible = element.isConnected && rect.width > 0 && rect.height > 0;
            for (let node = element; node; node = node.parentElement) {
                const style = getComputedStyle(node);
                if (style.display === "none" || style.visibility !== "visible" ||
                    Number(style.opacity) === 0) visible = false;
                if (node !== element) {
                    const clip = node.getBoundingClientRect();
                    if (["hidden", "clip", "scroll", "auto"].includes(style.overflowX)) {
                        left = Math.max(left, clip.left);
                        right = Math.min(right, clip.right);
                    }
                    if (["hidden", "clip", "scroll", "auto"].includes(style.overflowY)) {
                        top = Math.max(top, clip.top);
                        bottom = Math.min(bottom, clip.bottom);
                    }
                }
            }
            visible = visible && left <= rect.left + 1 && top <= rect.top + 1 &&
                right >= rect.right - 1 && bottom >= rect.bottom - 1 &&
                element.scrollWidth <= element.clientWidth;
            return {text: element.innerText, visible};
        """
        return self.command("POST", "/execute/sync", {
            "script": script, "args": [{ELEMENT: self.element(selector)}],
        })

    def state(self, selector, name):
        return self.command("GET", f"/element/{self.element(selector)}/{name}")

    def screenshot(self, destination):
        if self.session:
            try:
                destination.write_bytes(base64.b64decode(self.command("GET", "/screenshot", timeout=3)))
            except Exception as error:
                print(f"Could not capture WebDriver screenshot: {error}", file=sys.stderr)

    def close(self):
        try:
            if self.session:
                try:
                    self.command("DELETE", "", timeout=3)
                except Exception as error:
                    print(f"WebDriver session did not close cleanly: {error}", file=sys.stderr)

            if sys.platform.startswith("linux"):
                # The driver, native WebDriver, packaged app and its WebKit
                # children share this private session, not the runner's group.
                def group_running():
                    self.process.poll()  # Reap the group leader if it has exited.
                    try:
                        os.killpg(self.process.pid, 0)
                        return True
                    except ProcessLookupError:
                        return False

                for sig, seconds in ((signal.SIGTERM, 5), (signal.SIGKILL, 5)):
                    if not group_running():
                        break
                    try:
                        os.killpg(self.process.pid, sig)
                    except ProcessLookupError:
                        break
                    deadline = time.monotonic() + seconds
                    while group_running() and time.monotonic() < deadline:
                        time.sleep(0.1)
                if group_running():
                    raise RuntimeError("Owned Linux WebDriver process group did not exit")
            elif self.process.poll() is None:
                # On Windows tauri-driver owns a kill-on-close Job Object.
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=5)
        finally:
            self.log.close()


def xdotool(*args, check=True):
    return subprocess.run(["xdotool", *args], text=True, capture_output=True, check=check, timeout=10)


def linux_dialog(title, path, folder):
    import pyatspi  # type: ignore[import-not-found]

    def find_window():
        result = xdotool("search", "--onlyvisible", "--name", f"^{title}$", check=False)
        windows = result.stdout.split()
        if len(windows) > 1:
            raise RuntimeError(f"More than one visible native dialog titled {title!r}")
        return windows[0] if windows else None

    window = wait_for(f"native GTK dialog {title!r}", find_window, 30)
    xdotool("windowfocus", "--sync", window)
    pid = int(xdotool("getwindowpid", window).stdout.strip())

    def descendants(root):
        pending = [root]
        while pending:
            node = pending.pop()
            yield node
            pending.extend(node)

    def accessible_dialog():
        for application in pyatspi.Registry.getDesktop(0):
            if application.get_process_id() != pid:
                continue
            dialogs = [
                node for node in descendants(application)
                if node.name == title
                and node.getRole() in (pyatspi.ROLE_DIALOG, pyatspi.ROLE_FILE_CHOOSER,
                                       pyatspi.ROLE_FRAME, pyatspi.ROLE_WINDOW)
                and node.getState().contains(pyatspi.STATE_SHOWING)
            ]
            if len(dialogs) > 1:
                raise RuntimeError(f"More than one accessible GTK dialog titled {title!r}")
            return dialogs[0] if dialogs else None
        return None

    if folder:
        try:
            components = path.relative_to(Path.home()).parts
        except ValueError as error:
            raise RuntimeError("GTK proof output directory is outside the runner's home") from error
        if not components:
            raise RuntimeError("GTK proof output must be a directory below the runner's home")

        # GTK's folder chooser keeps Open disabled in Recent until a folder is
        # selected. Navigate using its Home shortcut and real file-list rows.
        xdotool("key", "--clearmodifiers", "alt+Home")

        def folder_row(name):
            dialog = accessible_dialog()
            if not dialog:
                raise RuntimeError("GTK folder dialog closed during navigation")
            matches = []
            for node in descendants(dialog):
                if (node.getRole() not in (pyatspi.ROLE_TABLE, pyatspi.ROLE_TREE_TABLE)
                        or not node.getState().contains(pyatspi.STATE_SHOWING)):
                    continue
                try:
                    table = node.queryTable()
                except NotImplementedError:
                    continue
                if not table.nColumns:
                    continue
                for row in range(table.nRows):
                    cell = table.getAccessibleAt(row, 0)
                    if cell and any(child.name == name for child in descendants(cell)):
                        matches.append((table, row, cell))
            if len(matches) > 1:
                raise RuntimeError(f"More than one GTK folder row named {name!r}")
            return matches[0] if matches else None

        for index, component in enumerate(components):
            table, row, cell = wait_for(
                f"GTK folder row {component!r}", lambda: folder_row(component), 30)
            if index + 1 < len(components):
                action = cell.queryAction()
                indices = [i for i in range(action.nActions) if action.getName(i) == "activate"]
                if len(indices) != 1 or not action.doAction(indices[0]):
                    raise RuntimeError(f"Could not open GTK folder row {component!r}")
            else:
                if not cell.queryComponent().grabFocus():
                    raise RuntimeError("Could not focus the GTK output folder row")
                if not table.isRowSelected(row) and not table.addRowSelection(row):
                    raise RuntimeError("Could not select the GTK output folder row")
                wait_for("the GTK output folder row to be selected",
                         lambda: table.isRowSelected(row), 15)
    else:
        xdotool("key", "--clearmodifiers", "ctrl+l")

        def focused_entry():
            dialog = accessible_dialog()
            if not dialog:
                return None
            for node in descendants(dialog):
                state = node.getState()
                if state.contains(pyatspi.STATE_FOCUSED) and state.contains(pyatspi.STATE_SHOWING):
                    try:
                        node.queryEditableText()
                    except NotImplementedError:
                        pass
                    else:
                        return node
            return None

        entry = wait_for("the native GTK location field to receive focus", focused_entry, 15)
        if not entry.queryEditableText().setTextContents(str(path)):
            raise RuntimeError("Could not enter the path into the native GTK location field")
        wait_for("the native GTK location field to contain the requested path",
                 lambda: entry.queryText().getText(0, -1) == str(path), 15)

    def open_button():
        dialog = accessible_dialog()
        if not dialog:
            raise RuntimeError("GTK dialog closed before its Open button was activated")
        buttons = [
            node for node in descendants(dialog)
            if node.getRole() == pyatspi.ROLE_PUSH_BUTTON and node.name == "Open"
            and node.getState().contains(pyatspi.STATE_SHOWING)
            and node.getState().contains(pyatspi.STATE_ENABLED)
        ]
        if len(buttons) > 1:
            raise RuntimeError("More than one enabled Open button in the GTK dialog")
        return buttons[0] if buttons else None

    button = wait_for("the native GTK Open button to be enabled", open_button, 15)
    action = button.queryAction()
    if action.nActions != 1 or action.getName(0) != "click" or not action.doAction(0):
        raise RuntimeError("Could not activate the native GTK Open button")
    wait_for(f"native GTK dialog {title!r} to close", lambda: not find_window(), 15)


def windows_dialog(app, path, folder):
    import win32api  # type: ignore[import-not-found]
    import win32clipboard  # type: ignore[import-not-found]
    import win32con  # type: ignore[import-not-found]
    import win32gui  # type: ignore[import-not-found]
    import win32process  # type: ignore[import-not-found]
    from pywinauto import Desktop, keyboard  # type: ignore[import-not-found]

    native = Desktop(backend="win32")
    automation = Desktop(backend="uia")

    def belongs_to_app(window):
        try:
            process = win32api.OpenProcess(0x0410, False, window.element_info.process_id)
            try:
                image = win32process.GetModuleFileNameEx(process, 0)
            finally:
                process.Close()
            return same_path(image, app)
        except Exception:
            return False

    last_ambiguity = None

    def find_dialog():
        nonlocal last_ambiguity
        # Deduplicate actual top-level HWNDs; an enabled foreground dialog is
        # the interactive one when Windows exposes more than one chooser.
        dialogs = {
            window.handle: window
            for window in native.windows(class_name="#32770", top_level_only=True,
                                         visible_only=True, enabled_only=True)
            if win32gui.GetAncestor(window.handle, win32con.GA_ROOT) == window.handle
            and belongs_to_app(window)
        }
        if len(dialogs) == 1:
            return next(iter(dialogs.values()))
        if len(dialogs) > 1:
            foreground = win32gui.GetForegroundWindow()
            root = win32gui.GetAncestor(foreground, win32con.GA_ROOT) if foreground else 0
            if root in dialogs:
                return dialogs[root]
            ambiguity = (tuple(sorted(dialogs)), root)
            if ambiguity != last_ambiguity:
                print(f"Waiting for an active candidate dialog; HWNDs={ambiguity[0]}, "
                      f"foreground root={root}", flush=True)
                last_ambiguity = ambiguity
        return None

    dialog = wait_for("native Windows file/folder dialog owned by the candidate", find_dialog, 30)
    handle = dialog.handle
    dialog_pid = dialog.element_info.process_id

    def dialog_open():
        return (win32gui.IsWindow(handle) and win32gui.IsWindowVisible(handle)
                and win32process.GetWindowThreadProcessId(handle)[1] == dialog_pid)
    uia = automation.window(handle=dialog.handle)
    if folder:
        # The folder picker selects its current directory. Navigate through its
        # real address bar, then wait for UI Automation to report that path.
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardText(str(path), win32clipboard.CF_UNICODETEXT)
        finally:
            win32clipboard.CloseClipboard()
        dialog.set_focus()
        dialog.type_keys("^l")
        keyboard.send_keys("^v{ENTER}", pause=0.05)

        def navigated():
            if not dialog_open():
                raise RuntimeError("Windows folder picker closed before navigation completed")
            address = os.path.normcase(str(path)).rstrip("\\/")
            return any(item.element_info.automation_id == "1001" and
                       os.path.normcase(item.window_text()).rstrip("\\/").endswith(address)
                       for item in uia.wrapper_object().descendants())

        try:
            wait_for("Windows folder picker to navigate to the requested directory", navigated, 15)
        except Exception:
            # The WebDriver screenshot contains the webview, not the native
            # picker. Record what Windows exposed before the dialog is closed.
            try:
                foreground = win32gui.GetForegroundWindow()
                root = win32gui.GetAncestor(foreground, win32con.GA_ROOT) if foreground else 0
                print(f"Windows folder navigation: requested={str(path)!r}, "
                      f"directory_exists={os.path.isdir(path)}, dialog={handle}, "
                      f"dialog_open={bool(dialog_open())}, foreground={foreground}, "
                      f"foreground_root={root}", file=sys.stderr, flush=True)
            except Exception as error:
                print(f"Could not inspect Windows dialog state: {error}", file=sys.stderr, flush=True)
            try:
                win32clipboard.OpenClipboard()
                try:
                    clipboard_matches = (win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT)
                                         and win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT) == str(path))
                finally:
                    win32clipboard.CloseClipboard()
                print(f"Windows folder navigation: clipboard_matches={bool(clipboard_matches)}",
                      file=sys.stderr, flush=True)
            except Exception as error:
                print(f"Could not inspect Windows clipboard: {error}", file=sys.stderr, flush=True)
            try:
                controls = []
                for item in uia.wrapper_object().descendants():
                    try:
                        info = item.element_info
                        focused = item.has_keyboard_focus()
                        if (info.automation_id != "1001" and
                                info.control_type not in ("Edit", "ComboBox", "ToolBar") and not focused):
                            continue
                        control = {"id": info.automation_id, "type": info.control_type,
                                   "text": item.window_text(), "focused": focused}
                        if info.control_type == "Edit":
                            try:
                                control["value"] = item.get_value()
                            except Exception as error:
                                control["value_error"] = str(error)
                        controls.append(control)
                    except Exception as error:
                        controls.append({"inspection_error": str(error)})
                print("Windows folder navigation controls: " + json.dumps(controls, ensure_ascii=False),
                      file=sys.stderr, flush=True)
            except Exception as error:
                print(f"Could not inspect Windows picker controls: {error}", file=sys.stderr, flush=True)
            raise
    else:
        edit = uia.child_window(auto_id="1148", control_type="Edit")
        edit.wait("ready", timeout=15)
        edit.set_edit_text(str(path))
    if not dialog_open():
        raise RuntimeError("Windows file/folder picker closed before confirmation")
    uia.child_window(auto_id="1", control_type="Button").invoke()
    wait_for("native Windows file/folder dialog to close", lambda: not dialog_open(), 15)




def choose(driver, selector, path, folder=False):
    errors = []

    def click():
        try:
            driver.click(selector)
        except Exception as error:
            errors.append(error)

    # Some drivers wait for a native modal dialog before returning from click.
    thread = threading.Thread(target=click, daemon=True)
    thread.start()
    if sys.platform == "win32":
        windows_dialog(driver.app, path, folder)
    else:
        linux_dialog("Select Folder" if folder else "Open File", path, folder)
    thread.join(timeout=20)
    if thread.is_alive():
        raise RuntimeError(f"WebDriver click on {selector} did not return after closing the dialog")
    if errors:
        raise RuntimeError(f"WebDriver click on {selector} failed: {errors[0]}") from errors[0]


def same_path(first, second):
    return os.path.normcase(os.path.normpath(str(first))) == os.path.normcase(os.path.normpath(str(second)))

def prepare_community_bundle(data_dir, source=None):
    """Stage only an explicitly supplied local bundle in the proof-owned data root."""
    base = data_dir / ("roaming" if sys.platform == "win32" else "data")
    destination = base / "ru.gigaam.gui.rustliquid" / "models" / "pyannote-community"
    if destination.exists() or destination.is_symlink():
        raise RuntimeError(f"Community-1 proof destination already exists: {destination}")
    if source is None:
        return
    names = ("export.json", "plda.json", "tract/segmentation.onnx", "tract/embedding.onnx")
    for name in names:
        path = source / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError(f"Community-1 bundle is incomplete: {path}")
    for name in names:
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, target)



def run_proof(app, fixture, diagnostics, timeout, app_data=None, model="v3_e2e_rnnt",
              diarization=False, community_missing=False, community_bundle=None):
    check_display()
    if not app.is_file() or not fixture.is_file() or fixture.stat().st_size == 0:
        raise RuntimeError(f"Application or nonempty fixture is missing: {app}, {fixture}")
    native_name = "msedgedriver.exe" if sys.platform == "win32" else "WebKitWebDriver"
    native_driver = shutil.which(native_name)
    if not native_driver:
        raise RuntimeError(f"Required native WebDriver is missing from PATH: {native_name}")
    diagnostics.mkdir(parents=True, exist_ok=True)
    with proof_temporary_directory() as temporary:
        root = Path(temporary)
        output = root / "output"
        output.mkdir()
        data_dir = app_data or root / "app-data"
        if community_missing or community_bundle is not None:
            prepare_community_bundle(data_dir, community_bundle)
        driver = WebDriver(app, native_driver, data_dir, diagnostics / "driver.log")
        try:
            driver.start()
            def processing_ready():
                try:
                    return driver.state("#choose-media", "displayed")
                except WebDriverError as error:
                    if error.code == "no such element":
                        return False
                    raise

            wait_for("processing page", processing_ready, 30)
            if model != "v3_e2e_rnnt":
                def model_available():
                    try:
                        driver.element(f'#asr-model option[value="{model}"]')
                        return True
                    except WebDriverError as error:
                        if error.code == "no such element":
                            return False
                        raise
                wait_for(f"model {model} to appear", model_available, 30)
                driver.click("#asr-model")
                driver.click(f'#asr-model option[value="{model}"]')
            if driver.property("#asr-model", "value") != model:
                raise RuntimeError(f"Expected model {model} to be selected")
            if diarization and not driver.state("#asr-diarization", "selected"):
                driver.click("label.switch-row")
            if driver.state("#asr-diarization", "selected") != diarization:
                raise RuntimeError(f"Diarization selection did not match {diarization}")
            if community_missing or community_bundle is not None:
                driver.click("#asr-diarization-model")
                driver.click('#asr-diarization-model option[value="pyannote-community"]')
                if driver.property("#asr-diarization-model", "value") != "pyannote-community":
                    raise RuntimeError("Community-1 selection did not persist")
            if not driver.state('.output-card input[value="txt"]', "selected"):
                raise RuntimeError("TXT output is not selected")

            choose(driver, "#choose-media", fixture)
            last_fixture_diagnostic = None
            scrolled_to_fixture = False

            def fixture_selected():
                nonlocal last_fixture_diagnostic, scrolled_to_fixture
                path_selector = "#selected-files .selected-file span"
                name_selector = "#selected-files .selected-file strong"
                try:
                    observed = driver.property(path_selector, "textContent")
                    if not scrolled_to_fixture:
                        driver.command("POST", "/execute/sync", {
                            "script": "arguments[0].scrollIntoView({block: 'center'});",
                            "args": [{ELEMENT: driver.element(name_selector)}],
                        })
                        scrolled_to_fixture = True
                    label = driver.visible_text(name_selector)
                    if not isinstance(observed, str) or not isinstance(label.get("text"), str):
                        raise RuntimeError("The selected-file path or name is not a string")
                    path_matches = same_path(observed, fixture)
                    name_matches = label["text"] == fixture.name
                    if path_matches and name_matches and label.get("visible") is True:
                        return True
                    diagnostic = (
                        f"Selected-file mismatch: path matches={path_matches}, "
                        f"absolute={os.path.isabs(observed)}, "
                        f"basename matches={os.path.normcase(os.path.basename(observed)) == os.path.normcase(fixture.name)}, "
                        f"name matches={name_matches}, name visible={label.get('visible')}"
                    )
                    if diagnostic != last_fixture_diagnostic:
                        print(diagnostic, flush=True)
                        last_fixture_diagnostic = diagnostic
                    return False
                except WebDriverError as error:
                    if error.code == "no such element":
                        return False
                    raise

            wait_for("the chosen fixture in the UI", fixture_selected, 30)
            choose(driver, "#choose-output", output, folder=True)
            wait_for("the chosen output directory in the UI", lambda: same_path(
                driver.property("#output-directory", "value"), output), 30)
            if list(output.iterdir()):
                raise RuntimeError("Proof output directory was not empty before starting")

            driver.click("#start-processing")
            if community_missing:
                def missing_bundle_reported():
                    status = driver.text("#progress-status")
                    if "Не найден подготовленный файл Community-1" not in status:
                        return False
                    if not driver.state("#start-processing", "enabled"):
                        return False
                    if list(output.iterdir()):
                        raise RuntimeError("Missing Community-1 bundle produced output")
                    return True

                wait_for("missing Community-1 bundle error", missing_bundle_reported, 30)
                print("PASS: selected Community-1 reports a missing local bundle", flush=True)
                return
            last_status = None

            def complete():
                nonlocal last_status
                state = driver.command("POST", "/execute/sync", {
                    "script": """
                        const result = document.querySelector('[data-page="result"]');
                        const start = document.querySelector('#start-processing');
                        const status = document.querySelector('#progress-status');
                        return {shown: !result.hidden && result.classList.contains('active'),
                                enabled: !start.disabled, status: status.textContent};
                    """,
                    "args": [],
                })
                if state["shown"]:
                    return True
                status = state["status"]
                if status != last_status:
                    print(f"Application status: {status}", flush=True)
                    last_status = status
                if state["enabled"]:
                    raise RuntimeError(f"Processing ended without a result: {status}")
                return False

            wait_for("real model download, transcription and result page", complete, timeout, interval=2)
            result = output / f"{fixture.stem}.gigaam.txt"
            if not result.is_file():
                raise RuntimeError(f"Application reported completion without saving {result.name}")
            saved = result.read_text(encoding="utf-8").strip()
            shown = driver.text("#result-content pre").strip()
            if not saved or not shown:
                raise RuntimeError("Saved transcript or visible transcript is empty")
            if saved != shown:
                raise RuntimeError("Saved transcript does not match the application's visible transcript")
            if community_bundle is not None and "[SPEAKER_" not in saved:
                raise RuntimeError("Community-1 produced no speaker-labelled transcript")
            paths = driver.text("#result-content ul")
            if not any(same_path(line, result) for line in paths.splitlines()):
                raise RuntimeError("The application did not list the saved file on the result page")
            result_name = result.name
            result_size = result.stat().st_size
        except Exception:
            traceback.print_exc()
            if sys.platform.startswith("linux"):
                try:
                    subprocess.run(["scrot", str(diagnostics / "native-desktop.png")],
                                   check=True, timeout=10)
                except (OSError, subprocess.SubprocessError) as error:
                    print(f"Could not capture native X desktop: {error}", file=sys.stderr)
            driver.screenshot(diagnostics / "failure.png")
            raise
        finally:
            original = sys.exc_info()[1]
            try:
                driver.close()
            except Exception as error:
                if original is None:
                    raise
                print(f"WebDriver process cleanup also failed: {error}", file=sys.stderr, flush=True)
                original.add_note(f"WebDriver process cleanup failed: {error}")

    print(f"PASS: real UI saved a nonempty {result_name} ({result_size} bytes)", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-display", action="store_true")
    parser.add_argument("--app", type=Path)
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--diagnostics", type=Path)
    parser.add_argument("--timeout", type=int, default=1800, help="seconds for model download and inference")
    parser.add_argument("--app-data", type=Path, help="dedicated app-data root to retain model downloads")
    parser.add_argument("--model", choices=("v3_e2e_rnnt", "multilingual_ctc", "v4_rnnt", "multilingual_v2_rnnt"), default="v3_e2e_rnnt")
    parser.add_argument("--diarization", action="store_true")
    community = parser.add_mutually_exclusive_group()
    community.add_argument("--community-missing", action="store_true",
                           help="check the missing-bundle error through the installed UI")
    community.add_argument("--community-bundle", type=Path,
                           help="local authorized export; never downloaded or uploaded")
    args = parser.parse_args()
    if args.check_display:
        check_display()
        return
    if not args.app or not args.fixture or not args.diagnostics or args.timeout <= 0:
        parser.error("--app, --fixture, --diagnostics and a positive --timeout are required")
    run_proof(args.app.resolve(), args.fixture.resolve(), args.diagnostics.resolve(), args.timeout,
              args.app_data.resolve() if args.app_data else None, args.model,
              args.diarization or args.community_missing or args.community_bundle is not None,
              args.community_missing, args.community_bundle.resolve() if args.community_bundle else None)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"UI proof failed: {error}", file=sys.stderr, flush=True)
        raise SystemExit(1) from error
