#!/usr/bin/env python3
"""Exercise an installed Rust Liquid candidate through its real UI.

The Python process is only the CI test controller: it does not provide an ASR
backend to the application. tauri-driver launches the packaged application;
WebDriver clicks its controls, and the OS file chooser is driven separately.
The fresh app-data directory forces the application to download its own model.
Requires tauri-driver, the platform WebDriver, and an interactive desktop. On
Linux also requires Xvfb, xdotool and a window manager; on Windows pywinauto.
"""

import argparse
import base64
import ctypes
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

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


class WebDriver:
    def __init__(self, app, native_driver, data_dir, log_file):
        executable = shutil.which("tauri-driver")
        if not executable:
            raise RuntimeError("tauri-driver is not on PATH")
        env = os.environ.copy()
        if sys.platform == "win32":
            env["APPDATA"] = str(data_dir / "roaming")
            env["LOCALAPPDATA"] = str(data_dir / "local")
            Path(env["APPDATA"]).mkdir(parents=True)
            Path(env["LOCALAPPDATA"]).mkdir(parents=True)
        else:
            for name, suffix in (("XDG_DATA_HOME", "data"), ("XDG_CONFIG_HOME", "config"), ("XDG_CACHE_HOME", "cache")):
                env[name] = str(data_dir / suffix)
                Path(env[name]).mkdir(parents=True)
            env["GDK_BACKEND"] = "x11"
            env["WEBKIT_DISABLE_DMABUF_RENDERER"] = "1"
            env["LIBGL_ALWAYS_SOFTWARE"] = "1"
        self.log = open(log_file, "w", encoding="utf-8")
        self.process = subprocess.Popen(
            [executable, "--native-driver", native_driver],
            stdout=self.log, stderr=subprocess.STDOUT, env=env,
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
            raise RuntimeError(f"WebDriver {method} {path}: {error.read().decode(errors='replace')}") from error
        value = result.get("value")
        if isinstance(value, dict) and value.get("error"):
            raise RuntimeError(f"WebDriver {method} {path}: {value}")
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

    def state(self, selector, name):
        return self.command("GET", f"/element/{self.element(selector)}/{name}")

    def screenshot(self, destination):
        if self.session:
            try:
                destination.write_bytes(base64.b64decode(self.command("GET", "/screenshot", timeout=3)))
            except Exception as error:
                print(f"Could not capture WebDriver screenshot: {error}", file=sys.stderr)

    def close(self):
        if self.session:
            try:
                self.command("DELETE", "", timeout=3)
            except Exception:
                pass
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.log.close()


def xdotool(*args, check=True):
    return subprocess.run(["xdotool", *args], text=True, capture_output=True, check=check, timeout=10)


def linux_dialog(title, path, folder):
    def find_window():
        result = xdotool("search", "--onlyvisible", "--name", f"^{title}$", check=False)
        windows = result.stdout.split()
        if len(windows) > 1:
            raise RuntimeError(f"More than one visible native dialog titled {title!r}")
        return windows[0] if windows else None

    window = wait_for(f"native GTK dialog {title!r}", find_window, 30)
    xdotool("windowfocus", "--sync", window)
    xdotool("key", "--clearmodifiers", "ctrl+l")
    xdotool("key", "--clearmodifiers", "ctrl+a")
    xdotool("type", "--clearmodifiers", "--delay", "1", "--", str(path))
    xdotool("key", "--clearmodifiers", "Return")
    if folder:
        # GTK first navigates to the entered directory; its default Open button confirms it.
        try:
            wait_for("folder chooser to close", lambda: not find_window(), 2)
        except RuntimeError:
            xdotool("key", "--clearmodifiers", "alt+o")
    wait_for(f"native GTK dialog {title!r} to close", lambda: not find_window(), 15)


def windows_dialog(app, path, folder):
    import win32api  # type: ignore[import-not-found]
    import win32clipboard  # type: ignore[import-not-found]
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

    def find_dialog():
        dialogs = [window for window in native.windows(class_name="#32770", visible_only=True)
                   if belongs_to_app(window)]
        if len(dialogs) > 1:
            raise RuntimeError(f"More than one visible Windows file dialog for the candidate: {[w.window_text() for w in dialogs]}")
        return dialogs[0] if dialogs else None

    dialog = wait_for("native Windows file/folder dialog owned by the candidate", find_dialog, 30)
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
            if not find_dialog():
                return True
            address = os.path.normcase(str(path)).rstrip("\\/")
            return any(os.path.normcase(item.window_text()).rstrip("\\/").endswith(address)
                       for item in uia.wrapper_object().descendants(auto_id="1001"))

        wait_for("Windows folder picker to navigate to the requested directory", navigated, 15)
    else:
        edit = uia.child_window(auto_id="1148", control_type="Edit")
        edit.wait("ready", timeout=15)
        edit.set_edit_text(str(path))
    if find_dialog():
        uia.child_window(auto_id="1", control_type="Button").invoke()
    wait_for("native Windows file/folder dialog to close", lambda: not find_dialog(), 15)


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


def run_proof(app, fixture, diagnostics, timeout):
    check_display()
    if not app.is_file() or not fixture.is_file() or fixture.stat().st_size == 0:
        raise RuntimeError(f"Application or nonempty fixture is missing: {app}, {fixture}")
    native_name = "msedgedriver.exe" if sys.platform == "win32" else "WebKitWebDriver"
    native_driver = shutil.which(native_name)
    if not native_driver:
        raise RuntimeError(f"Required native WebDriver is missing from PATH: {native_name}")
    diagnostics.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="rust-liquid-ui-", dir=os.environ.get("RUNNER_TEMP")) as temporary:
        root = Path(temporary)
        output = root / "output"
        output.mkdir()
        driver = WebDriver(app, native_driver, root / "app-data", diagnostics / "driver.log")
        try:
            driver.start()
            wait_for("processing page", lambda: driver.state("#choose-media", "displayed"), 30)
            if driver.property("#asr-model", "value") != "v3_e2e_rnnt":
                raise RuntimeError("Expected the GigaAM v3 E2E RNNT model to be selected")
            if driver.state("#asr-diarization", "selected"):
                raise RuntimeError("Diarization unexpectedly enabled for this non-diarized proof")
            if not driver.state('.output-card input[value="txt"]', "selected"):
                raise RuntimeError("TXT output is not selected")

            choose(driver, "#choose-media", fixture)
            wait_for("the chosen fixture in the UI", lambda: any(
                same_path(line, fixture) for line in driver.text("#selected-files").splitlines()), 30)
            choose(driver, "#choose-output", output, folder=True)
            wait_for("the chosen output directory in the UI", lambda: same_path(
                driver.property("#output-directory", "value"), output), 30)
            if list(output.iterdir()):
                raise RuntimeError("Proof output directory was not empty before starting")

            driver.click("#start-processing")
            last_status = None

            def complete():
                nonlocal last_status
                if driver.state('[data-page="result"]', "displayed"):
                    return True
                status = driver.text("#progress-status")
                if status != last_status:
                    print(f"Application status: {status}", flush=True)
                    last_status = status
                if driver.state("#start-processing", "enabled"):
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
            paths = driver.text("#result-content ul")
            if not any(same_path(line, result) for line in paths.splitlines()):
                raise RuntimeError("The application did not list the saved file on the result page")
            print(f"PASS: real UI saved a nonempty {result.name} ({result.stat().st_size} bytes)", flush=True)
        except Exception:
            driver.screenshot(diagnostics / "failure.png")
            raise
        finally:
            driver.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-display", action="store_true")
    parser.add_argument("--app", type=Path)
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--diagnostics", type=Path)
    parser.add_argument("--timeout", type=int, default=1800, help="seconds for model download and inference")
    args = parser.parse_args()
    if args.check_display:
        check_display()
        return
    if not args.app or not args.fixture or not args.diagnostics or args.timeout <= 0:
        parser.error("--app, --fixture, --diagnostics and a positive --timeout are required")
    run_proof(args.app.resolve(), args.fixture.resolve(), args.diagnostics.resolve(), args.timeout)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"UI proof failed: {error}", file=sys.stderr, flush=True)
        raise SystemExit(1) from error
