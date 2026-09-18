"""Desktop lifecycle shared by macOS and Windows; no university requests."""
from __future__ import annotations

import contextlib
import ctypes
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser

APP_NAME = "FDU选课助手"
APP_VERSION = "1.2.0"


def configure_stdio():
    # Frozen Python ignores PYTHONIOENCODING. Worker pipes still need UTF-8
    # regardless of the Windows display language or active console code page.
    if sys.platform == "win32":
        for stream in (sys.stdin, sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")


def data_directory(resource_dir, frozen, platform=None, environ=None, home=None):
    platform = platform or sys.platform
    env = os.environ if environ is None else environ
    user_home = Path.home() if home is None else Path(home)
    if env.get("FDU_WEBUI_DATA_DIR"):
        return Path(env["FDU_WEBUI_DATA_DIR"]).expanduser().resolve()
    if not frozen:
        return Path(resource_dir)
    if platform == "win32":
        return Path(env.get("LOCALAPPDATA") or user_home / "AppData" / "Local") / "FDUCourseHelper"
    if platform == "darwin":
        return user_home / "Library" / "Application Support" / APP_NAME
    return Path(env.get("XDG_DATA_HOME") or user_home / ".local" / "share") / "fdu-course-helper"


def process_options(platform=None):
    if (platform or sys.platform) == "win32":
        # Python workers must not flash a console on every task.
        return {"creationflags": 0x08000000 | 0x00000200}
    return {"start_new_session": True}


def terminate_worker(process, force=False, platform=None):
    if process.poll() is not None:
        return
    try:
        if (platform or sys.platform) == "win32":
            process.kill() if force else process.terminate()
        else:
            os.killpg(process.pid, signal.SIGKILL if force else signal.SIGTERM)
    except ProcessLookupError:
        pass


@contextlib.contextmanager
def keep_awake():
    """Prevent idle system sleep for this worker's lifetime, without blocking lock."""
    inhibitor = None
    windows = sys.platform == "win32"
    try:
        if windows:
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
        elif sys.platform == "darwin":
            inhibitor = subprocess.Popen(
                ["/usr/bin/caffeinate", "-i", "-w", str(os.getpid())],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
    except OSError:
        logging.getLogger(__name__).warning("Cannot prevent idle sleep")
    try:
        yield
    finally:
        if windows:
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
        elif inhibitor is not None and inhibitor.poll() is None:
            inhibitor.terminate()
            try:
                inhibitor.wait(timeout=2)
            except subprocess.TimeoutExpired:
                inhibitor.kill()


class LocalInstance:
    """OS lock is released on process death; stale metadata never blocks restart."""
    def __init__(self, directory):
        self.directory = Path(directory)
        self.metadata = self.directory / "instance.json"
        self.handle = None
        self.token = secrets.token_urlsafe(32)

    def acquire(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        self.handle = (self.directory / "instance.lock").open("a+b")
        self.handle.seek(0)
        try:
            if sys.platform == "win32":
                import msvcrt
                if not self.handle.read(1):
                    self.handle.write(b"0")
                    self.handle.flush()
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.handle.close()
            self.handle = None
            return False
        return True

    def publish(self, port):
        temporary = self.metadata.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump({"port": port, "token": self.token, "pid": os.getpid()}, stream)
        os.chmod(temporary, 0o600)
        os.replace(temporary, self.metadata)

    def activate_existing(self, timeout=8):
        deadline = time.monotonic() + timeout
        # Do not send this local control request through a configured proxy.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        while time.monotonic() < deadline:
            try:
                data = json.loads(self.metadata.read_text(encoding="utf-8"))
                port = int(data["port"])
                if not 1024 <= port <= 65535:
                    raise ValueError("Invalid local port")
                request = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/app/activate", data=b"{}",
                    headers={"Content-Type": "application/json", "X-FDU-App-Token": data["token"]},
                )
                with opener.open(request, timeout=1) as response:
                    if response.status == 200:
                        return True
            except (OSError, ValueError, KeyError, urllib.error.URLError):
                pass
            time.sleep(0.15)
        return False

    def close(self):
        if self.handle is None:
            return
        try:
            self.metadata.unlink(missing_ok=True)
        finally:
            if sys.platform == "win32":
                import msvcrt
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            self.handle.close()
            self.handle = None


def configure_logging(directory):
    logdir = Path(directory) / "logs"
    logdir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(logdir / "app.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    os.chmod(logdir / "app.log", 0o600)
    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(logging.INFO)
    # Access logs add little diagnostic value and unnecessarily retain browsing history.
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    return handler


def open_directory(directory):
    if sys.platform == "win32":
        os.startfile(str(directory))
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(directory)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def webview2_available():
    """Avoid silently falling back to the legacy IE engine on Windows."""
    import winreg
    clients = ["{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}", "{2CD8A007-E189-409D-A2C8-9AF4EF3C72AA}",
               "{0D50BFEC-CD6A-4F9A-964C-C7416E3ACB10}", "{65C35B14-6C1D-4122-AC46-7148CC9D6497}"]
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (0, winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
            for client in clients:
                try:
                    key = "SOFTWARE\\Microsoft\\EdgeUpdate\\Clients\\" + client
                    with winreg.OpenKey(hive, key, 0, winreg.KEY_READ | view) as registry:
                        version, _ = winreg.QueryValueEx(registry, "pv")
                        if int(str(version).split(".")[0]) >= 109:
                            return True
                except (OSError, ValueError):
                    pass
    return False


class DesktopRuntime:
    def __init__(self, app, directory, is_busy, stop_tasks, native, open_page):
        self.app, self.directory = app, Path(directory)
        self.is_busy, self.stop_tasks = is_busy, stop_tasks
        self.native, self.open_page = native, open_page
        self.instance = LocalInstance(directory)
        self.server = self.window = None
        self.url = None
        self.closing = False
        self.confirming = False
        self.native_ready = threading.Event()

    def info(self):
        return {"version": APP_VERSION, "platform": sys.platform, "desktop": self.native,
                "data_dir": str(self.directory), "url": self.url,
                "control_token": self.instance.token}

    def authorized(self, token):
        return bool(token) and secrets.compare_digest(token, self.instance.token)

    def activate(self):
        if self.native:
            self.native_ready.wait(timeout=5)
            if self.window:
                self.window.show()
                self.window.restore()
                return
        if self.open_page:
            webbrowser.open(self.url)

    def request_exit(self):
        self.closing = True
        def exit_window():
            if self.window and self.native_ready.is_set():
                self.window.destroy()
            elif self.server:
                self.server.shutdown()
        threading.Timer(0.25, exit_window).start()

    def _on_closing(self):
        if self.closing:
            return True
        if not self.confirming:
            self.confirming = True
            # Cocoa invokes closing on the UI thread; dialogs/evaluate_js must run
            # from a worker so that the UI thread remains free to show the prompt.
            threading.Thread(target=self._confirm_close, daemon=True).start()
        return False

    def _confirm_close(self):
        try:
            # run_js executes directly and respects our CSP (evaluate_js uses eval).
            # The existing UI dialog also knows whether the user has unsaved edits.
            self.window.run_js("window.dispatchEvent(new Event('fdu-close-request'));")
        except Exception:
            logging.exception("Could not ask the UI to close")
            if not self.is_busy() or self.window.create_confirmation_dialog(
                    "退出选课助手？", "退出会停止当前任务，确定继续？"):
                self.request_exit()
        finally:
            self.confirming = False

    def run(self, preferred_port):
        from werkzeug.serving import make_server
        if not self.instance.acquire():
            if not self.instance.activate_existing():
                raise RuntimeError("应用已在启动或运行，请稍后重新打开。")
            return 0
        try:
            if self.native and sys.platform == "win32" and not webview2_available():
                logging.warning("Modern WebView2 was not found; using the default browser")
                self.native = False
            for port in range(preferred_port, min(preferred_port + 20, 65536)):
                try:
                    self.server = make_server("127.0.0.1", port, self.app, threaded=True)
                    break
                except SystemExit:
                    continue
            if self.server is None:
                raise RuntimeError("本地端口均被占用，请关闭旧实例后重试。")
            self.url = f"http://127.0.0.1:{self.server.server_port}"
            self.instance.publish(self.server.server_port)
            print(f"FDU 选课助手: {self.url}", flush=True)
            logging.info("Started %s in %s mode", APP_VERSION, "desktop" if self.native else "browser")
            previous_signal = signal.signal(signal.SIGTERM, lambda *_: self.request_exit())
            try:
                if self.native:
                    thread = threading.Thread(target=self.server.serve_forever, daemon=True)
                    thread.start()
                    try:
                        import webview
                        self.window = webview.create_window(
                            APP_NAME, self.url, width=1200, height=850, min_size=(900, 640),
                            text_select=True, background_color="#f5f7f5",
                        )
                        self.window.events.closing += self._on_closing
                        webview.start(lambda: self.native_ready.set(), private_mode=True,
                                      gui="edgechromium" if sys.platform == "win32" else "cocoa",
                                      localization={"global.quit": "退出", "global.cancel": "取消", "global.ok": "确认"})
                    except Exception:
                        logging.exception("Native window failed; falling back to browser")
                        self.native = False
                        self.window = None
                        if self.open_page:
                            webbrowser.open(self.url)
                        thread.join()
                else:
                    if self.open_page:
                        webbrowser.open(self.url)
                    self.server.serve_forever()
            finally:
                signal.signal(signal.SIGTERM, previous_signal)
        except KeyboardInterrupt:
            pass
        finally:
            self.closing = True
            self.stop_tasks()
            # shutdown() must only be called while serve_forever runs in another thread.
            if self.server:
                if self.native:
                    self.server.shutdown()
                self.server.server_close()
            self.instance.close()
        return 0
