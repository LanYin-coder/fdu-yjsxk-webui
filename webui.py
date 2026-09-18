#!/usr/bin/env python3
"""Local-only WebUI for the FDU graduate course enrollment scripts."""

from __future__ import annotations

import argparse
import codecs
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template, request

from src import grab, preselect
from desktop import (APP_VERSION, DesktopRuntime, configure_logging, data_directory,
                     keep_awake, open_directory, process_options, terminate_worker)


APP_DISPLAY_NAME = "FDU选课助手"
IS_FROZEN = bool(getattr(sys, "frozen", False))
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
BASE_DIR = data_directory(RESOURCE_DIR, IS_FROZEN)
BASE_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_PATH = BASE_DIR / "config.json"
CONFIG_BACKUP_PATH = BASE_DIR / "config.json.webui.bak"
COOKIE_PATH = BASE_DIR / "cookie.txt"
DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"
ALLOWED_TARGET = "yjsxk.fudan.edu.cn"
LOGIN_URL = f"http://{ALLOWED_TARGET}{grab.APP_ENTRY}"

if IS_FROZEN or os.environ.get("FDU_WEBUI_DATA_DIR"):
    grab.BASE_DIR = str(BASE_DIR)
    grab.CONFIG_PATH = str(CONFIG_PATH)
    grab.COOKIE_PATH = str(COOKIE_PATH)
    preselect.BASE_DIR = str(BASE_DIR)
    preselect.CONFIG_PATH = str(CONFIG_PATH)
    preselect.CONFIG_BAK = str(BASE_DIR / "config.json.preselect.bak")

WORKER_ARGUMENTS = {
    "login": ["--login"],
    "dry_run": ["--dry-run"],
    "preselect": [],
    "scheduled": [],
    "now": ["--now"],
    "probe": ["--probe"],
    "probe_force": ["--probe", "--force"],
}


def next_window(now: dt.datetime | None = None) -> tuple[str, str]:
    now = now or dt.datetime.now()
    base = now.replace(hour=0, minute=0, second=0, microsecond=0)
    for offset in range(2):
        day = base + dt.timedelta(days=offset)
        for hour in (10, 13):
            release = day.replace(hour=hour)
            if release > now:
                start = release - dt.timedelta(seconds=5)
                end = release + dt.timedelta(minutes=30)
                return start.strftime(DATETIME_FORMAT), end.strftime(DATETIME_FORMAT)
    raise RuntimeError("无法计算下一场选课窗口")


def default_config() -> dict[str, Any]:
    start, end = next_window()
    return {
        "target": ALLOWED_TARGET,
        "start_time": start,
        "end_time": end,
        "cookie_source": "file",
        "browser": "edge",
        "cookie_refresh_secs": 240,
        "request_interval": 0.8,
        "poll_interval": 0.6,
        "poll_max": 15,
        "http_timeout": 12,
        "serial_mode": False,
        "full_max_tries": 0,
        "courses": [],
    }


class ConfigValidationError(ValueError):
    pass


def _number(value: Any, label: str, minimum: float, maximum: float, integer: bool = False):
    try:
        result = int(value) if integer else float(value)
    except (TypeError, ValueError):
        raise ConfigValidationError(f"{label} 必须是数字")
    if not minimum <= result <= maximum:
        raise ConfigValidationError(f"{label} 必须在 {minimum:g} 到 {maximum:g} 之间")
    return result


def validate_config(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ConfigValidationError("配置格式无效")

    target = str(raw.get("target", ALLOWED_TARGET)).strip().lower()
    if target != ALLOWED_TARGET:
        raise ConfigValidationError("目标站点只能是 yjsxk.fudan.edu.cn")

    start_text = str(raw.get("start_time", "")).strip()
    end_text = str(raw.get("end_time", "")).strip()
    try:
        start_time = dt.datetime.strptime(start_text, DATETIME_FORMAT)
        end_time = dt.datetime.strptime(end_text, DATETIME_FORMAT)
    except ValueError:
        raise ConfigValidationError("时间格式应为 YYYY-MM-DD HH:MM:SS")
    if end_time <= start_time:
        raise ConfigValidationError("收工时间必须晚于开抢时间")

    cookie_source = str(raw.get("cookie_source", "auto"))
    if cookie_source not in {"auto", "browser", "file"}:
        raise ConfigValidationError("Cookie 来源无效")
    browser = str(raw.get("browser", "edge"))
    if browser not in {"edge", "chrome"}:
        raise ConfigValidationError("浏览器只能选择 Edge 或 Chrome")

    courses_raw = raw.get("courses", [])
    if not isinstance(courses_raw, list) or len(courses_raw) > 100:
        raise ConfigValidationError("课程列表格式无效或数量过多")

    courses: list[dict[str, Any]] = []
    for index, course in enumerate(courses_raw, 1):
        if not isinstance(course, dict):
            raise ConfigValidationError(f"第 {index} 门课程格式无效")
        name = str(course.get("name", "")).strip()
        kcdm = str(course.get("kcdm", "")).strip()
        bjdm = str(course.get("bjdm", "")).strip()
        lx = str(course.get("lx", "")).strip()
        bqmc = str(course.get("bqmc", "")).strip()
        if not name or len(name) > 100:
            raise ConfigValidationError(f"第 {index} 门课程名称不能为空且不能超过 100 字")
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,39}", kcdm):
            raise ConfigValidationError(f"第 {index} 门课程代码格式无效")
        if not re.fullmatch(r"[A-Za-z0-9._-]{3,80}", bjdm):
            raise ConfigValidationError(f"第 {index} 门班级代码格式无效")
        if not lx.isdigit() or not bqmc.isdigit():
            raise ConfigValidationError(f"第 {index} 门课程的 lx / bqmc 必须是数字")
        normalized = {
            "name": name,
            "kcdm": kcdm,
            "bjdm": bjdm,
            "lx": lx,
            "bqmc": bqmc,
            "enabled": bool(course.get("enabled", True)),
        }
        courses.append(normalized)

    enabled_count = sum(course["enabled"] for course in courses)
    if enabled_count > grab.MAX_BURST_COURSES:
        raise ConfigValidationError(
            f"批量提交最多启用 {grab.MAX_BURST_COURSES} 门课程，当前启用 {enabled_count} 门"
        )

    return {
        "target": target,
        "start_time": start_text,
        "end_time": end_text,
        "cookie_source": cookie_source,
        "browser": browser,
        "cookie_refresh_secs": _number(
            raw.get("cookie_refresh_secs", 240), "Cookie 刷新间隔", 30, 3600
        ),
        "request_interval": _number(raw.get("request_interval", 0.8), "请求间隔", 0.2, 60),
        "poll_interval": _number(raw.get("poll_interval", 0.6), "轮询间隔", 0.2, 60),
        "poll_max": _number(raw.get("poll_max", 15), "最大轮询次数", 1, 300, integer=True),
        "http_timeout": _number(raw.get("http_timeout", 12), "HTTP 超时", 2, 120),
        "serial_mode": False,
        # 兼容旧配置字段；满员课程现在固定持续重试到截止时间。
        "full_max_tries": 0,
        "courses": courses,
    }


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return default_config()
    try:
        with CONFIG_PATH.open(encoding="utf-8") as handle:
            stored = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigValidationError(f"config.json 无法读取：{exc}")
    merged = {**default_config(), **stored}
    return validate_config(merged)


def save_config(raw: Any) -> dict[str, Any]:
    config = validate_config(raw)
    if CONFIG_PATH.exists():
        shutil.copy2(CONFIG_PATH, CONFIG_BACKUP_PATH)
    temp_path = CONFIG_PATH.with_suffix(".json.tmp")
    with temp_path.open("w", encoding="utf-8") as handle:
        json.dump(config, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.chmod(temp_path, 0o600)
    os.replace(temp_path, CONFIG_PATH)
    return config


def extract_cookie(raw: Any) -> str:
    """Accept a Cookie header, raw cookie string, or Chrome's Copy as cURL output."""
    text = str(raw or "").strip()
    if not text or len(text) > 30_000:
        raise ConfigValidationError("请粘贴 Cookie 请求头或 Chrome 复制的 cURL")

    cookie = ""
    for match in re.finditer(
        r"(?:^|\s)(-H|--header|-b|--cookie)\s+(?:'([^']*)'|\"([^\"]*)\")",
        text,
        flags=re.IGNORECASE,
    ):
        flag = match.group(1).lower()
        value = match.group(2) if match.group(2) is not None else match.group(3)
        if flag in {"-b", "--cookie"}:
            cookie = value
            break
        if value.lower().startswith("cookie:"):
            cookie = value.split(":", 1)[1].strip()
            break

    if not cookie:
        header = re.search(r"(?im)^\s*cookie\s*:\s*([^\r\n]+)$", text)
        if header:
            cookie = header.group(1).strip().strip("'\"")
        elif not text.lower().startswith("curl "):
            cookie = text.removeprefix("Cookie:").removeprefix("cookie:").strip()

    if not cookie:
        raise ConfigValidationError("没有在粘贴内容中找到 Cookie 请求头")
    if any(ord(char) < 32 or ord(char) == 127 for char in cookie):
        raise ConfigValidationError("Cookie 中包含非法控制字符")

    pairs: list[tuple[str, str]] = []
    names: set[str] = set()
    for segment in cookie.split(";"):
        if not segment.strip():
            continue
        if "=" not in segment:
            raise ConfigValidationError("Cookie 格式无效，应为 name=value; name2=value2")
        name, value = segment.split("=", 1)
        name, value = name.strip(), value.strip()
        if not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name):
            raise ConfigValidationError("Cookie 字段名称格式无效")
        if name in names:
            continue
        names.add(name)
        pairs.append((name, value))

    missing = {"JSESSIONID", "_WEU"} - names
    if missing:
        raise ConfigValidationError(f"登录态不完整，缺少：{', '.join(sorted(missing))}")
    return "; ".join(f"{name}={value}" for name, value in pairs)


def verify_cookie(cookie: str, config: dict[str, Any]) -> None:
    client = grab.Grabber(config, cookie)
    try:
        client.refresh_token()
    except grab.CookieError as exc:
        raise ConfigValidationError(f"登录态验证失败：{exc}") from exc
    except Exception as exc:
        raise ConfigValidationError(f"无法连接选课系统：{exc}") from exc


def save_manual_cookie(raw: Any) -> None:
    cookie = extract_cookie(raw)
    config = load_config()
    verify_cookie(cookie, config)

    config["cookie_source"] = "file"
    save_config(config)
    temp_path = COOKIE_PATH.with_suffix(".txt.tmp")
    with temp_path.open("w", encoding="utf-8") as handle:
        handle.write(cookie)
    os.chmod(temp_path, 0o600)
    os.replace(temp_path, COOKIE_PATH)


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _read_saved_cookie() -> str:
    if not COOKIE_PATH.exists():
        raise ConfigValidationError("尚未保存登录态，请先点击“登录系统”完成导入")
    try:
        return extract_cookie(COOKIE_PATH.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigValidationError(f"无法读取 cookie.txt：{exc}") from exc


def _first_text(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def load_course_catalog() -> list[dict[str, Any]]:
    cookie = _read_saved_cookie()
    config = load_config()
    verify_cookie(cookie, config)
    try:
        groups = preselect.fetch_lists(cookie, timeout=float(config.get("http_timeout", 12)))
    except SystemExit as exc:
        raise ConfigValidationError(str(exc)) from exc

    catalog: list[dict[str, Any]] = []
    for group, lx, label, bqmc, rows in groups:
        for row in rows:
            capacity = _as_int(row.get("KXRS"))
            enrolled = _as_int(row.get("DQRS"))
            remaining = max(capacity - enrolled, 0) if capacity > 0 else None
            bjdm = str(row.get("BJDM") or "").strip()
            department = _first_text(
                row, "RWKKDWMC", "KCKKDWMC", "KKDWMC", "KKYXMC", "KCYXMC",
                "KKBMMC", "YXMC", "KKDW", "KKYX"
            )
            department_code = _first_text(
                row, "RWKKDWDM", "KCKKDWDM", "KKDWDM", "KKYXDM", "KCYXDM",
                "KKBMDM", "YXDM"
            )
            if department_code and department and department_code not in department:
                department = f"{department_code} {department}"
            elif department_code and not department:
                department = department_code
            catalog.append({
                "name": str(row.get("KCMC") or "").strip(),
                "kcdm": str(row.get("KCDM") or "").strip(),
                "bjdm": bjdm,
                "lx": str(lx),
                "bqmc": str(bqmc),
                "group": group,
                "category": label,
                "teacher": str(row.get("RKJS") or "").strip(),
                "department": department,
                "credits": str(row.get("KCXF") or row.get("XF") or "").strip(),
                "schedule": str(row.get("PKSJ") or "").strip(),
                "location": str(row.get("PKDD") or "").strip(),
                "campus": str(row.get("XQMC") or "").strip(),
                "capacity": capacity,
                "enrolled": enrolled,
                "remaining": remaining,
                "full": capacity > 0 and enrolled >= capacity,
                "conflict": bool(_as_int(row.get("IS_CONFLICT"))),
                "selectable": bool(bjdm),
            })
    return catalog


def validate_configured_course_scope(config: dict[str, Any]) -> None:
    """Block task startup when an old queue contains classes outside this student's scope."""
    cookie = _read_saved_cookie()
    verify_cookie(cookie, config)
    try:
        student, scopes, server_time, allow_empty = preselect.fetch_eligibility_context(
            cookie, timeout=float(config.get("http_timeout", 12))
        )
    except SystemExit as exc:
        raise ConfigValidationError(str(exc)) from exc

    invalid = [
        course.get("name") or course.get("bjdm") or "未命名课程"
        for course in config["courses"]
        if course.get("enabled", True) and not preselect.course_in_student_scope(
            scopes.get(str(course.get("bjdm") or "")), student, server_time, allow_empty
        )
    ]
    if invalid:
        names = "、".join(str(name) for name in invalid[:5])
        suffix = f"等 {len(invalid)} 门" if len(invalid) > 5 else ""
        raise ConfigValidationError(
            f"当前账号不可选：{names}{suffix}。请从“添加课程”目录重新选择"
        )


class TaskManager:
    COMMANDS = {
        "login": ("登录", ["src/grab.py", "--login"]),
        "dry_run": ("自检", ["src/grab.py", "--dry-run"]),
        "preselect": ("预选课", ["src/preselect.py"]),
        "scheduled": ("定时抢课", ["src/grab.py"]),
        "now": ("立即抢课", ["src/grab.py", "--now"]),
        "probe": ("链路演练", ["src/grab.py", "--probe"]),
        "probe_force": ("强制链路演练", ["src/grab.py", "--probe", "--force"]),
    }

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.process: subprocess.Popen[bytes] | None = None
        self.output = ""
        self.output_base = 0
        self.action: str | None = None
        self.label: str | None = None
        self.started_at: str | None = None
        self.finished_at: str | None = None
        self.exit_code: int | None = None
        self.stopping = False

    def _append(self, text: str) -> None:
        with self.lock:
            self.output += text.replace("\r", "")
            if len(self.output) > 250_000:
                trim = len(self.output) - 200_000
                self.output = self.output[trim:]
                self.output_base += trim

    def _reader(self, process: subprocess.Popen[bytes]) -> None:
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        assert process.stdout is not None
        try:
            while True:
                chunk = os.read(process.stdout.fileno(), 4096)
                if not chunk:
                    break
                self._append(decoder.decode(chunk))
            tail = decoder.decode(b"", final=True)
            if tail:
                self._append(tail)
        finally:
            code = process.wait()
            with self.lock:
                if self.process is process:
                    self._append(f"\n[WebUI] 任务结束，退出码 {code}\n")
                    self.exit_code = code
                    self.finished_at = dt.datetime.now().isoformat(timespec="seconds")
                    self.process = None
                    self.stopping = False
            if process.stdin:
                process.stdin.close()
            process.stdout.close()

    def start(self, action: str) -> dict[str, Any]:
        if action not in self.COMMANDS:
            raise ValueError("不支持的任务")
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                raise RuntimeError("已有任务正在运行，请先停止或等待完成")
            label, arguments = self.COMMANDS[action]
            self.output = f"[WebUI] 启动{label}\n"
            self.output_base = 0
            self.action = action
            self.label = label
            self.started_at = dt.datetime.now().isoformat(timespec="seconds")
            self.finished_at = None
            self.exit_code = None
            self.stopping = False
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONUTF8"] = "1"
            env["FDU_WEBUI_DATA_DIR"] = str(BASE_DIR)
            command = (
                [sys.executable, "--worker-action", action]
                if IS_FROZEN else [sys.executable, "-u", str(RESOURCE_DIR / "webui.py"),
                                   "--worker-action", action]
            )
            process = subprocess.Popen(
                command,
                cwd=str(BASE_DIR),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=env,
                bufsize=0,
                **process_options(),
            )
            self.process = process
            threading.Thread(target=self._reader, args=(process,), daemon=True).start()
            return self.status(0)

    def send_input(self, value: str) -> None:
        if len(value) > 5000:
            raise ValueError("输入内容过长")
        with self.lock:
            process = self.process
            if process is None or process.poll() is not None or process.stdin is None:
                raise RuntimeError("当前没有可接收输入的任务")
            try:
                process.stdin.write((value + "\n").encode("utf-8"))
                process.stdin.flush()
            except (BrokenPipeError, OSError):
                raise RuntimeError("任务已经结束，无法继续输入")

    def stop(self) -> None:
        with self.lock:
            process = self.process
            if process is None or process.poll() is not None:
                raise RuntimeError("当前没有正在运行的任务")
            self.stopping = True
            terminate_worker(process)
        threading.Thread(target=self._kill_later, args=(process,), daemon=True).start()

    def _kill_later(self, process: subprocess.Popen[bytes]) -> None:
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            terminate_worker(process, force=True)

    def shutdown(self):
        with self.lock:
            process = self.process
            if process is None or process.poll() is not None:
                return
            self.stopping = True
            terminate_worker(process)
        self._kill_later(process)
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass

    def status(self, cursor: int) -> dict[str, Any]:
        with self.lock:
            running = self.process is not None and self.process.poll() is None
            absolute_end = self.output_base + len(self.output)
            truncated = cursor < self.output_base
            cursor = max(cursor, self.output_base)
            relative = min(max(cursor - self.output_base, 0), len(self.output))
            return {
                "running": running,
                "stopping": self.stopping,
                "action": self.action,
                "label": self.label,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "exit_code": self.exit_code,
                "output": self.output[relative:],
                "cursor": absolute_end,
                "truncated": truncated,
            }


class Preflight:
    """Read-only checks run in the server, so closing a dialog cannot interrupt them."""

    def __init__(self):
        self.lock = threading.RLock()
        self.state = "idle"
        self.checks = []
        self.fingerprint = None
        self.checked_at = None

    def signature(self, config):
        stamp = COOKIE_PATH.stat().st_mtime_ns if COOKIE_PATH.exists() else None
        material = [str(COOKIE_PATH), stamp, config["target"], config["courses"],
                    config["cookie_source"], config["browser"]]
        return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()

    def status(self):
        with self.lock:
            valid = False
            if self.state in {"passed", "failed"}:
                try:
                    unchanged = self.fingerprint == self.signature(load_config())
                except (OSError, ValueError):
                    unchanged = False
                if not unchanged:
                    return {"state": "idle", "valid": False, "checks": [], "checked_at": None}
            if self.state == "passed" and self.checked_at:
                try:
                    valid = (time.time() - self.checked_at < 300 and
                             self.fingerprint == self.signature(load_config()))
                except (OSError, ValueError):
                    pass
            return {"state": self.state, "valid": valid,
                    "checks": list(self.checks), "checked_at": self.checked_at}

    def start(self, config):
        with self.lock:
            if self.state == "checking":
                return self.status()
            self.state = "checking"
            self.checked_at = None
            self.fingerprint = self.signature(config)
            self.checks = []
            threading.Thread(target=self._run, args=(config,), daemon=True).start()
            return self.status()

    def _run(self, config):
        stages = [
            ("login", "登录态", lambda: verify_cookie(_read_saved_cookie(), config)),
            ("courses", "课程资格", lambda: validate_configured_course_scope(config)),
        ]
        for key, label, check in stages:
            try:
                check()
            except (Exception, SystemExit) as exc:
                with self.lock:
                    self.checks.append({"key": key, "label": label, "ok": False,
                                        "message": str(exc) or "检查失败，请重试"})
                    self.state = "failed"
                return
            with self.lock:
                self.checks.append({"key": key, "label": label, "ok": True,
                                    "message": "验证通过"})
        with self.lock:
            self.checked_at = time.time()
            self.state = "passed"


app = Flask(
    __name__,
    template_folder=str(RESOURCE_DIR / "webui" / "templates"),
    static_folder=str(RESOURCE_DIR / "webui" / "static"),
)
tasks = TaskManager()
preflight = Preflight()
operation_lock = threading.RLock()
runtime = None


@app.before_request
def serialize_operations():
    # A task must not race a login import, course save, or another task start.
    if request.method != "GET" or request.path == "/api/courses":
        operation_lock.acquire()
        request.environ["fdu.operation_locked"] = True
        if runtime and runtime.closing:
            return jsonify({"error": "应用正在退出，请重新打开后操作"}), 409
        if preflight.status()["state"] == "checking" and request.path not in {"/api/preflight", "/api/app/quit", "/api/app/activate"}:
            return jsonify({"error": "自检进行中，请等待完成"}), 409


@app.teardown_request
def release_operation(_error):
    if request.environ.pop("fdu.operation_locked", False):
        operation_lock.release()


@app.after_request
def secure_response(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; "
        "style-src 'self'; img-src 'self' data:; connect-src 'self'; "
        "font-src 'self'; frame-ancestors 'none'"
    )
    return response


def _json_body() -> dict[str, Any]:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValueError("请求内容必须是 JSON 对象")
    return data


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/config")
def api_get_config():
    login_updated_at = None
    if COOKIE_PATH.exists():
        try:
            login_updated_at = int(COOKIE_PATH.stat().st_mtime)
        except OSError:
            pass
    return jsonify({
        "config": load_config(),
        "exists": CONFIG_PATH.exists(),
        "login": {"saved": COOKIE_PATH.exists(), "updated_at": login_updated_at},
        "app": runtime.info() if runtime else {"version": APP_VERSION, "platform": sys.platform},
    })


@app.post("/api/app/<action>")
def api_app_control(action):
    if runtime is None or not runtime.authorized(request.headers.get("X-FDU-App-Token", "")):
        return jsonify({"error": "应用控制请求无效，请刷新页面后重试"}), 403
    if action == "activate":
        threading.Thread(target=runtime.activate, daemon=True).start()
    elif action == "open-data":
        open_directory(BASE_DIR)
    elif action == "quit":
        if tasks.status(0)["running"] and not _json_body().get("stop_task"):
            return jsonify({"error": "任务正在运行，请确认停止任务并退出"}), 409
        runtime.request_exit()
    else:
        raise ConfigValidationError("未知的应用操作")
    return jsonify({"ok": True})


@app.put("/api/config")
def api_save_config():
    if tasks.status(0)["running"]:
        return jsonify({"error": "任务运行中不能修改配置"}), 409
    config = save_config(_json_body().get("config"))
    return jsonify({"config": config, "message": "配置已保存"})


@app.post("/api/login-page")
def api_open_login_page():
    opened = webbrowser.open(LOGIN_URL)
    if not opened:
        return jsonify({"error": "无法自动打开浏览器，请手动打开复旦选课系统"}), 500
    return jsonify({"message": "已打开复旦登录页"})


@app.put("/api/cookie")
def api_save_cookie():
    if tasks.status(0)["running"]:
        return jsonify({"error": "任务运行中不能更新登录态"}), 409
    save_manual_cookie(_json_body().get("cookie"))
    return jsonify({"message": "登录态验证通过并已保存"})


@app.get("/api/courses")
def api_courses():
    if tasks.status(0)["running"]:
        return jsonify({"error": "任务运行中不能刷新课程列表"}), 409
    courses = load_course_catalog()
    return jsonify({"courses": courses, "count": len(courses)})


@app.get("/api/status")
def api_status():
    try:
        cursor = max(0, int(request.args.get("cursor", "0")))
    except ValueError:
        cursor = 0
    return jsonify({**tasks.status(cursor), "preflight": preflight.status()})


@app.post("/api/preflight")
def api_preflight():
    if tasks.status(0)["running"]:
        return jsonify({"error": "任务运行中不能自检"}), 409
    config = load_config()
    if not any(c.get("enabled", True) for c in config["courses"]):
        raise ConfigValidationError("请先选择至少一个教学班")
    return jsonify(preflight.start(config)), 202


@app.post("/api/tasks")
def api_start_task():
    data = _json_body()
    action = str(data.get("action", ""))
    if action not in tasks.COMMANDS:
        raise ConfigValidationError("不支持的任务")
    if tasks.status(0)["running"]:
        return jsonify({"error": "已有任务正在运行，请先停止或等待完成"}), 409
    config = load_config()
    if not CONFIG_PATH.exists():
        save_config(config)
    if action in {"scheduled", "now", "probe", "probe_force"}:
        if not any(course.get("enabled", True) for course in config["courses"]):
            return jsonify({"error": "请先保存并启用至少一门课程"}), 400
        if dt.datetime.strptime(config["end_time"], DATETIME_FORMAT) <= dt.datetime.now():
            raise ConfigValidationError("截止时间已过，请更新运行计划")
        if data.get("require_preflight") and not preflight.status()["valid"]:
            return jsonify({"error": "自检未通过或已失效，请重新自检后启动"}), 409
        validate_configured_course_scope(config)
    return jsonify(tasks.start(action))


@app.post("/api/input")
def api_send_input():
    value = str(_json_body().get("value", ""))
    tasks.send_input(value)
    return jsonify({"message": "输入已发送"})


@app.post("/api/stop")
def api_stop():
    tasks.stop()
    return jsonify({"message": "正在停止任务"})


@app.errorhandler(ConfigValidationError)
@app.errorhandler(ValueError)
def handle_bad_request(error):
    return jsonify({"error": str(error)}), 400


@app.errorhandler(RuntimeError)
def handle_conflict(error):
    return jsonify({"error": str(error)}), 409


def _run_worker(action: str) -> int:
    arguments = WORKER_ARGUMENTS[action]
    if action == "preselect":
        sys.argv = ["preselect.py"]
        return preselect.main()
    sys.argv = ["grab.py", *arguments]
    return grab.main()


def _available_port(preferred: int) -> int:
    for port in range(preferred, min(preferred + 20, 65536)):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as candidate:
            try:
                candidate.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError(f"端口 {preferred} 到 {preferred + 19} 均被占用")


def main() -> int:
    global runtime
    parser = argparse.ArgumentParser(description="FDU 研究生选课 WebUI")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true", help="启动后打开浏览器")
    parser.add_argument("--desktop", action="store_true", help="使用独立桌面窗口")
    parser.add_argument("--browser", action="store_true", help="使用浏览器界面")
    parser.add_argument("--worker-action", choices=sorted(WORKER_ARGUMENTS), help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker_action:
        with keep_awake():
            return _run_worker(args.worker_action)
    if not 1024 <= args.port <= 65535:
        parser.error("端口必须在 1024 到 65535 之间")

    configure_logging(BASE_DIR)
    no_open = os.environ.get("FDU_WEBUI_NO_OPEN") == "1"
    runtime = DesktopRuntime(app, BASE_DIR, lambda: tasks.status(0)["running"], tasks.shutdown,
                             native=(args.desktop or IS_FROZEN) and not args.browser and not no_open,
                             open_page=(args.open or args.desktop or IS_FROZEN) and not no_open)
    return runtime.run(args.port)


if __name__ == "__main__":
    sys.exit(main())
