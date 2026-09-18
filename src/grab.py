#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
复旦大学研究生选课脚本（2026 版重写）

原仓库 JarynWong/fdu_course_enrollment 的 course.py 基于 2024 年协议，
在当前（2026）系统上已失效，本文件按 courses.js 的真实逻辑重写。

原实现对不上的地方：
  1. csrfToken 正则：原用 value='...' 单引号，现页面是 value="..." 双引号且 id 前有 style 属性
     —— 原脚本永远取不到 token，会一直误报"cookies过期"。
  2. 选课是异步两步：choiceCourse.do 只返回受理号 xid（code!=0 不代表选上），
     必须再轮询 loadXkjgRes.do，拿到 {code:1} 才是真的选上。
     原脚本 code!=0 就打印"提交选课成功"并退出，会漏课。

用法：
    python src/grab.py            正常跑（会等到 start_time 再开始）
    python src/grab.py --dry-run  只做环境自检，不提交任何选课请求
    python src/grab.py --login    打开浏览器现场登录，验证并保存 Cookie（登录态过期时用）
    python src/grab.py --probe    链路演练：发 1 次真实请求，看服务器回什么
    python src/grab.py --now      忽略 start_time，立刻开始
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time

import requests


def _repo_root() -> str:
    """仓库根目录（config.json / cookie.txt / requirements.txt 所在处）。

    标准布局下 grab.py 在仓库根的 src/ 里，根 = 上级目录；
    旧布局 / 临时拷贝（py 与 config 同目录）则直接用本文件所在目录。
    以“父目录里有没有 README.md”区分两种布局。
    """
    here = os.path.dirname(os.path.abspath(__file__))
    parent = os.path.dirname(here)
    if os.path.isfile(os.path.join(parent, "README.md")):
        return parent
    return here


BASE_DIR = _repo_root()
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
COOKIE_PATH = os.path.join(BASE_DIR, "cookie.txt")

DOMAIN = "yjsxk.fudan.edu.cn"
# 站点根路径只返回 OpenResty 欢迎页；选课应用入口在这条路径上。
# 未登录访问会 302 到复旦统一认证（id.fudan.edu.cn），登录后自动跳回。
APP_ENTRY = "/yjsxkapp/sys/xsxkappfudan/xsxkHome/gotoChooseCourse.do"

# --login 登录后，UIS 跳转 + Cookie 落盘可能有延迟，重试窗口放宽。
# 每 LOGIN_INTERVAL 秒自动重读一次浏览器 Cookie，最长等 LOGIN_WAIT 秒
# （60s / 3s ≈ 20 次重读机会），期间显示倒计时进度条。
LOGIN_WAIT = 60
LOGIN_INTERVAL = 3
KEYCHAIN_TIMEOUT = 20
MAX_BURST_COURSES = 10
SUBMIT_WINDOW_SECONDS = 1.0
# 留出约 0.2 秒给线程启动与本机调度；这里只保证本地开始发送，不能保证远端收包时间。
SUBMIT_SCHEDULING_MARGIN = 0.2
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# 2026-09 实测：页面形如 <input type="text" style="display:none;" id="csrfToken" value="42f3...">
CSRF_RE = re.compile(r"""id=["']csrfToken["'][^>]*value=["']([0-9a-fA-F]{32})["']""")


def ts() -> str:
    return dt.datetime.now().strftime("%H:%M:%S")


def log(msg: str = "") -> None:
    print(f"[{ts()}] {msg}", flush=True)


def _countdown(seconds: float, note: str) -> None:
    """终端倒计时进度条：原地刷新剩余秒数+进度条，直到超时。

    非交互（输出非 tty）时直接睡满，不刷屏。
    """
    if not sys.stdout.isatty():
        time.sleep(seconds)
        return
    end = time.monotonic() + seconds
    bar_w = 15
    while True:
        left = end - time.monotonic()
        if left <= 0:
            break
        secs = int(left) + 1  # 向上取整，避免闪 0
        filled = round((1 - left / seconds) * bar_w)
        bar = "█" * filled + "░" * (bar_w - filled)
        sys.stdout.write(f"\r  ⏳ {note}  {secs:>2}s {bar}")
        sys.stdout.flush()
        time.sleep(min(0.2, left))
    sys.stdout.write("\r" + " " * 80 + "\r")
    sys.stdout.flush()


class CookieError(RuntimeError):
    """Cookie 无效 / 登录态失效等需要人工介入的错误。"""
    pass


def already_have(msg: str) -> bool:
    """结果里出现这些话，说明这门课其实已经在已选列表里了，不必再重试。"""
    if not msg:
        return False
    return any(kw in msg for kw in ("已选", "已选择", "已经选", "重复"))


FULL_KWS = ("已满", "满员", "满额", "已选满")
"""判定「课程满员」的关键词（用组合词避免把"不满"之类的文案误判成满课）。"""

PERMANENT_REJECTION_KWS = (
    "#bjzc5",
    "不在您的可选范围",
    "无选课资格",
    "不满足当前课程的要求",
)
"""本轮条件不变时重试也不会成功的资格拒绝。"""


def is_full_msg(msg) -> bool:
    return bool(msg) and any(k in msg for k in FULL_KWS)


def is_permanent_rejection(msg) -> bool:
    return bool(msg) and any(k in msg for k in PERMANENT_REJECTION_KWS)


def order_pending(pending: list) -> list:
    """满课降级：每轮把非满课按原优先级排前面，上一轮判定满员的课统一挪到
    本轮末尾再试——让能抢的课永远最先被处理，满课也不放弃（可能有人退课/
    放号），只是不挡在前面。"""
    fresh = [c for c in pending if not c.get("_full")]
    full = [c for c in pending if c.get("_full")]
    return fresh + full


def _note_failure(course: dict, msg: str, dropped: list) -> bool:
    """记录一次选课失败；满员继续重试，只有永久资格拒绝才移出队列。"""
    name = course["name"]
    if is_permanent_rejection(msg):
        log(f"  ⏹ {name} 被服务器判定为当前账号不可选，本轮不再重试")
        dropped.append(name)
        return True
    if is_full_msg(msg):
        course["_full"] = True
        log(f"  ~ {name} 满员，下一轮继续请求，直到选上或到达截止时间")
        return False
    course["_full"] = False
    return False


def settle_result(course: dict, code, msg: str, done: list, pending: list,
                  dropped: list) -> None:
    """处理一门课的轮询终局：选上或已拥有时移除，否则继续后续回合。"""
    name, bjdm = course["name"], course["bjdm"]
    if code == 1:
        log(f"  ★ 选上 {name}（{bjdm}）")
        done.append(name)
        pending.remove(course)
        return
    if code is None and already_have(msg or ""):
        log(f"  ✓ {name} 已在已选列表中（{msg}），跳过")
        done.append(name)
        pending.remove(course)
        return
    log(f"  × {name} 未成功：{msg or '未知原因'}")
    if _note_failure(course, msg, dropped):
        pending.remove(course)


def run_serial(gr, pending: list, done: list, dropped: list, interval: float) -> None:
    """串行：一门提交了、等结果出来，再搞下一门（系统的轮询逻辑是单例，不能并发）。
    每轮按「非满在前、满课在后」的顺序跑；满课持续重试到截止时间。"""
    for c in order_pending(list(pending)):
        ok, info = gr.submit(c)
        if not ok:
            if already_have(info or ""):
                log(f"  ✓ {c['name']} 判定为已拥有，跳过")
                done.append(c["name"])
                pending.remove(c)
            else:
                log(f"  × {c['name']}：{info}")
                if _note_failure(c, info, dropped):
                    pending.remove(c)
            time.sleep(interval)
            continue
        log(f"  已提交 {c['name']}（{c['bjdm']}），等待结果 ...")
        code, msg = gr.poll_result(info)
        settle_result(c, code, msg, done, pending, dropped)
        time.sleep(interval)


def submission_delays(count: int, window: float = SUBMIT_WINDOW_SECONDS) -> list[float]:
    """Return start offsets that keep up to ten local submissions inside one window."""
    if count < 1:
        return []
    if count > MAX_BURST_COURSES:
        raise ValueError(f"批量提交最多支持 {MAX_BURST_COURSES} 门启用课程")
    usable = max(0.0, window - min(SUBMIT_SCHEDULING_MARGIN, window * 0.2))
    if count == 1:
        return [0.0]
    gap = min(0.1, usable / (count - 1))
    return [index * gap for index in range(count)]


def _submit_at(gr, course: dict, origin: float, delay: float):
    wait = origin + delay - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    started_at = time.monotonic()
    submitter = getattr(gr, "submit_isolated", gr.submit)
    try:
        ok, info = submitter(course)
    except Exception as exc:
        ok, info = False, f"请求异常：{exc}"
    return started_at, ok, info


def run_burst(gr, pending: list, done: list, dropped: list,
              window: float = SUBMIT_WINDOW_SECONDS) -> None:
    """Start every choiceCourse request inside one second, then poll results slowly.

    Requests use the same authenticated server-side Cookie/CSRF state. The concrete
    Grabber gives each submission a separate local requests.Session because a shared
    requests.Session is not safe for concurrent mutation.
    """
    ordered = order_pending(list(pending))
    delays = submission_delays(len(ordered), window)
    origin = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(ordered)) as executor:
        futures = [
            executor.submit(_submit_at, gr, course, origin, delay)
            for course, delay in zip(ordered, delays)
        ]
        results = [future.result() for future in futures]

    started = [result[0] for result in results]
    span = max(started) - min(started) if len(started) > 1 else 0.0
    log(f"  本轮 {len(ordered)} 门已在 {span:.3f} 秒内开始提交，下面查询各门结果")

    submitted = []
    for c, (_started_at, ok, info) in zip(ordered, results):
        if ok:
            submitted.append((c, info))
            log(f"  已提交 {c['name']}（{c['bjdm']}）")
        else:
            log(f"  × {c['name']}：{info}")
            if already_have(info or ""):
                log(f"  ✓ {c['name']} 判定为已拥有，跳过")
                done.append(c["name"])
                pending.remove(c)
            elif _note_failure(c, info, dropped):
                pending.remove(c)

    for c, xid in submitted:
        code, msg = gr.poll_result(xid)
        settle_result(c, code, msg, done, pending, dropped)


# ---------------------------------------------------------------- cookie ----

def cookie_from_browser(browser: str = "edge"):
    """从浏览器本地 Cookie 库读取（能拿到 HttpOnly 的 JSESSIONID）。"""
    try:
        import browser_cookie3
    except ImportError:
        return None, "browser-cookie3 未安装（pip install browser-cookie3）"

    getter = {"edge": "edge", "chrome": "chrome"}.get(browser)
    if not getter or not hasattr(browser_cookie3, getter):
        return None, f"不支持的浏览器：{browser}"

    original_keychain_reader = None
    if sys.platform == "darwin" and hasattr(browser_cookie3, "_get_osx_keychain_password"):
        original_keychain_reader = browser_cookie3._get_osx_keychain_password

        def _timed_keychain_reader(service: str, user: str):
            command = [
                "/usr/bin/security", "-q", "find-generic-password",
                "-w", "-a", user, "-s", service,
            ]
            try:
                result = subprocess.run(
                    command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    timeout=KEYCHAIN_TIMEOUT, check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(
                    f"macOS 钥匙串授权等待超过 {KEYCHAIN_TIMEOUT} 秒，请允许访问后重试"
                ) from exc
            if result.returncode != 0:
                raise RuntimeError("macOS 钥匙串未授权，无法解密浏览器 Cookie")
            return result.stdout.strip()

        browser_cookie3._get_osx_keychain_password = _timed_keychain_reader

    try:
        # 让底层直接限定站点，避免在 macOS 上解密整个浏览器 Cookie 库。
        jar = getattr(browser_cookie3, getter)(domain_name=DOMAIN)
    except Exception as exc:  # 权限、钥匙串、数据库锁等
        return None, f"读取 {browser} Cookie 失败：{exc}"
    finally:
        if original_keychain_reader is not None:
            browser_cookie3._get_osx_keychain_password = original_keychain_reader

    picked = []
    for c in jar:
        d = (c.domain or "").lstrip(".")
        if not d:
            continue
        if DOMAIN == d or DOMAIN.endswith("." + d):
            picked.append(f"{c.name.strip()}={c.value.strip()}")

    if not picked:
        return None, f"{browser} 里没有该站点的登录 Cookie（请先在 {browser} 登录一次选课系统）"
    return "; ".join(picked), None


def cookie_from_file():
    if not os.path.exists(COOKIE_PATH):
        return None, "cookie.txt 不存在"
    try:
        with open(COOKIE_PATH, encoding="utf-8") as fh:
            raw = fh.read()
    except OSError as exc:
        return None, f"读取 cookie.txt 失败：{exc}"
    raw = " ".join(raw.split())
    if not raw or raw.startswith("#"):
        return None, "cookie.txt 为空"
    return raw, None


def _browser_chain(browser) -> list[str]:
    """把 config 的 browser 字段规范成探测顺序列表（去重）。

    兼容两种写法：
      "edge"            → ["edge", "chrome"]  先试 edge，失败自动补 chrome
      ["edge","chrome"] → ["edge", "chrome"]  按数组顺序逐个试
    无论怎么写，edge/chrome 都会出现在链里（没写到的那个排最后兜底），
    保证登录在哪个浏览器都能取到 Cookie。
    """
    if isinstance(browser, str):
        chain = [browser]
    elif isinstance(browser, (list, tuple)):
        chain = list(browser)
    else:
        chain = []
    # 只保留支持的浏览器，重复的去掉
    chain = [b for b in chain if b in ("edge", "chrome")]
    for b in ("edge", "chrome"):
        if b not in chain:
            chain.append(b)
    return chain


def obtain_cookie(cfg: dict) -> str:
    """按 cookie_source 取 Cookie：auto / browser / file。

    - auto（默认）：先读 cookie.txt（自检/--login 时写入的已验证 Cookie），
      没有再读浏览器——避免浏览器库里残留的过期 Cookie 盖掉新备份。
    - browser：只读浏览器。browser 字段支持字符串或数组，按顺序探测
      （edge 读不到就试 chrome），登录在哪个浏览器就用哪个。
    """
    source = cfg.get("cookie_source", "auto")
    browser = cfg.get("browser", "edge")
    first = browser[0] if isinstance(browser, (list, tuple)) and browser else browser

    if source == "auto":
        # 文件优先：这是最近一次验证通过后固化的 Cookie
        value, err = cookie_from_file()
        if value:
            return value

    if source in ("auto", "browser"):
        for name in _browser_chain(browser):
            value, cerr = cookie_from_browser(name)
            if value:
                if name != first:
                    log(f"  {first} 里没有 Cookie，改用 {name}（config 的 browser 可改成 {name}）")
                return value
            log(f"  {name} 取 Cookie 未成功：{cerr}")
        if source == "browser":
            raise CookieError("Edge 和 Chrome 都读不到该站点的 Cookie")

    value, err = cookie_from_file()
    if value:
        return value
    raise CookieError(
        f"{err}。请先在 Edge/Chrome 打开选课页\n"
        f"    http://{DOMAIN}{APP_ENTRY}\n"
        f"   （未登录会自动跳复旦统一认证，登录完回到选课页即可）\n"
        f"    或把 Cookie 粘贴进 {os.path.basename(COOKIE_PATH)}"
    )


# --------------------------------------------------------------- client ----

class Grabber:
    def __init__(self, cfg: dict, cookie: str):
        self.cfg = cfg
        self.cookie = cookie
        self.target = cfg.get("target", DOMAIN)
        self.timeout = float(cfg.get("http_timeout", 12))
        self.base = f"http://{self.target}/yjsxkapp/sys/xsxkappfudan"
        self.session = requests.Session()
        self.token: str | None = None

    def set_cookie(self, cookie: str) -> None:
        self.cookie = cookie

    def _headers(self) -> dict:
        return {
            "Cookie": self.cookie,
            "User-Agent": UA,
            "Content-Type": "application/x-www-form-urlencoded",
        }

    def refresh_token(self) -> str:
        url = f"{self.base}/xsxkHome/gotoChooseCourse.do"
        resp = self.session.get(
            url, headers=self._headers(), timeout=self.timeout, allow_redirects=False
        )
        if resp.status_code in (301, 302, 303, 307, 308):
            raise CookieError("Cookie 已失效（请求被重定向到统一认证登录页）")
        if resp.status_code != 200:
            raise CookieError(f"打开选课页失败，HTTP {resp.status_code}")
        m = CSRF_RE.search(resp.text)
        if not m:
            raise CookieError("页面里找不到 csrfToken，Cookie 多半已失效")
        self.token = m.group(1)
        return self.token

    def submit(self, course: dict):
        """提交选课。返回 (是否已受理, xid 或 失败原因)"""
        return self._submit_with_session(self.session, course)

    def submit_isolated(self, course: dict):
        """并发提交使用独立连接池，但沿用同一服务端 Cookie Session。"""
        with requests.Session() as session:
            return self._submit_with_session(session, course)

    def _submit_with_session(self, session: requests.Session, course: dict):
        url = f"{self.base}/xsxkCourse/choiceCourse.do?_={int(time.time() * 1000)}"
        payload = {
            "bjdm": course["bjdm"],
            "lx": str(course["lx"]),
            "bqmc": str(course.get("bqmc", "")),
            "csrfToken": self.token,
        }
        try:
            resp = session.post(
                url, headers=self._headers(), data=payload, timeout=self.timeout
            )
        except requests.RequestException as exc:
            return False, f"请求失败：{exc}"
        try:
            body = resp.json()
        except Exception:
            return False, f"响应不是 JSON（HTTP {resp.status_code}）"
        if body.get("code") == 0:
            return False, body.get("msg") or "被拒绝"
        return True, body.get("msg") or ""

    def probe_once(self, course: dict) -> dict:
        """向服务器发一次真实选课请求，返回原始响应 dict（演练用，不做任何重试）。"""
        url = f"{self.base}/xsxkCourse/choiceCourse.do?_={int(time.time() * 1000)}"
        payload = {
            "bjdm": course["bjdm"],
            "lx": str(course["lx"]),
            "bqmc": str(course.get("bqmc", "")),
            "csrfToken": self.token,
        }
        resp = self.session.post(
            url, headers=self._headers(), data=payload, timeout=self.timeout
        )
        try:
            return {"http": resp.status_code, **(resp.json() or {})}
        except Exception:
            return {"http": resp.status_code, "msg": resp.text[:200], "_raw": True}

    def poll_result(self, xid: str):
        """轮询选课结果。返回 (code, 说明)；code==1 表示真的选上了。"""
        url = f"{self.base}/xsxkCourse/loadXkjgRes.do?_={int(time.time() * 1000)}"
        max_times = int(self.cfg.get("poll_max", 30))
        interval = float(self.cfg.get("poll_interval", 0.6))
        for _ in range(max_times):
            resp = self.session.post(
                url,
                headers=self._headers(),
                data={"xid": xid, "sfhqdqxkqqs": 0},
                timeout=self.timeout,
            )
            try:
                body = resp.json()
            except Exception:
                return None, f"轮询响应不是 JSON（HTTP {resp.status_code}）"
            msg = body.get("msg")
            if msg:
                try:
                    detail = json.loads(msg)
                except Exception:
                    return None, msg
                return detail.get("code"), detail.get("msg") or ""
            time.sleep(interval)
        return None, "轮询超时，结果未知（请去「已选课程」页确认）"


# ----------------------------------------------------------------- flow ----

def load_config() -> dict:
    if not os.path.exists(CONFIG_PATH):
        example = os.path.join(BASE_DIR, "config.example.json")
        hint = (
            f"找不到配置文件：{CONFIG_PATH}\n"
            f"请参考 {os.path.basename(example)} 创建你自己的配置：\n"
            f"  cp {os.path.basename(example)} config.json\n"
            f"然后填入你的课程代码（bjdm 需从选课系统抓取）。"
        )
        raise SystemExit(hint)
    with open(CONFIG_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def wait_until(target: dt.datetime, label: str) -> None:
    """等到 target；超时不自动 +1 天（原脚本会静默等到明天）。"""
    while True:
        remain = (target - dt.datetime.now()).total_seconds()
        if remain <= 0:
            return
        if remain > 600:
            step = 60
        elif remain > 60:
            step = 30
        elif remain > 10:
            step = 5
        else:
            step = 1
        h, rem = divmod(int(remain), 3600)
        m, s = divmod(rem, 60)
        log(f"{label}：还有 {h}小时{m}分{s}秒")
        time.sleep(min(step, remain))


def countdown_banner(deadline: dt.datetime, n_courses: int) -> None:
    log("=" * 46)
    log("  复旦研究生选课 · 已就绪")
    log(f"  目标时间 {deadline.strftime('%Y-%m-%d %H:%M:%S')}")
    log(f"  待抢课程 {n_courses} 门")
    log("=" * 46)


def _obtain_verified(cfg: dict) -> str:
    """失效恢复专用：轮询 文件 → Edge → Chrome，取第一个能通过 csrfToken 验证的 Cookie。

    与 obtain_cookie 的区别：obtain_cookie 只负责“拿到一份 Cookie”不验证，
    若 cookie.txt 已过期会一直命中同一份失效文件、永不 fallback 浏览器；
    本函数对每个来源都现场验证，文件不行就试浏览器。
    """
    if cfg.get("cookie_source") == "file":
        chain = ["file"]
    elif cfg.get("cookie_source") == "browser":
        chain = _browser_chain(cfg.get("browser", "edge"))
    else:  # auto
        chain = ["file"] + _browser_chain(cfg.get("browser", "edge"))

    last_err = ""
    for name in chain:
        if name == "file":
            cookie, err = cookie_from_file()
        else:
            cookie, err = cookie_from_browser(name)
        if not cookie:
            last_err = f"{name}：{err}"
            continue
        g = Grabber(cfg, cookie)
        try:
            g.refresh_token()  # 验证通过才算数
        except CookieError as exc:
            last_err = f"{name}：{exc}"
            continue
        if name != "file":
            _save_cookie(cookie)  # 浏览器取到就顺手固化，下次直接命中文件
        return cookie
    raise CookieError(f"所有来源均失效（{last_err}）")


def run(cfg: dict, start_now: bool) -> int:
    interval = float(cfg.get("request_interval", 0.8))
    refresh_secs = float(cfg.get("cookie_refresh_secs", 240))
    end_time = dt.datetime.strptime(cfg["end_time"], "%Y-%m-%d %H:%M:%S")
    start_time = dt.datetime.strptime(cfg["start_time"], "%Y-%m-%d %H:%M:%S")

    pending = [c for c in cfg["courses"] if c.get("enabled", True)]
    if not pending:
        log("没有启用任何课程，退出")
        return 0
    if len(pending) > MAX_BURST_COURSES:
        log(f"启用课程有 {len(pending)} 门，批量提交最多支持 {MAX_BURST_COURSES} 门")
        return 1

    # ---- 取 Cookie 并自检（文件失效自动改试浏览器，全程现场验证）----
    log("正在获取 Cookie ...")
    try:
        cookie = _obtain_verified(cfg)
    except CookieError as exc:
        log(f"✗ 取 Cookie 失败：{exc}")
        log("提示：可运行 python src/grab.py --login 现场登录一次，或双击「scripts/选课助手」选 1 自检")
        return 1
    gr = Grabber(cfg, cookie)
    try:
        gr.refresh_token()
    except CookieError as exc:
        log(f"✗ Cookie 已失效：{exc}")
        log("提示：登录态已过期。先运行 python src/grab.py --login 重新登录，再回来抢课")
        return 1
    log(f"Cookie 有效，csrfToken = {gr.token[:8]}...")

    if start_now:
        log("（--now：跳过等待，立即开始）")
    else:
        if start_time < dt.datetime.now():
            log("=" * 46)
            log(f"  注意：start_time（{start_time}）已经过去了")
            log("  脚本不会自动改到明天，现在直接开始。")
            log("=" * 46)
        else:
            countdown_banner(start_time, len(pending))
            # 临近开抢前 3 分钟换一次新 Cookie，避免等待期间过期
            prewarm = start_time - dt.timedelta(seconds=180)
            if prewarm > dt.datetime.now():
                wait_until(prewarm, "等待换票点")
                log("临近开抢，重新获取一次最新 Cookie ...")
                try:
                    new_cookie = _obtain_verified(cfg)  # 已现场验证，文件失效自动改试浏览器
                    gr.set_cookie(new_cookie)
                    gr.refresh_token()  # 同步 gr 里的 token
                    log(f"已换新票，csrfToken = {gr.token[:8]}...")
                except CookieError as exc:
                    log(f"换新票失败（继续用旧票）：{exc}")
            wait_until(start_time, "等待开抢")

    # ---- 正式抢课 ----
    log(f"开始抢课！（批量提交：最多 {MAX_BURST_COURSES} 门在 1 秒窗口内开始请求，随后轮询结果）")
    last_cookie_ts = time.time()
    done: list = []
    dropped: list = []
    round_no = 0

    while pending and dt.datetime.now() < end_time:
        round_no += 1

        # 长时间作战时定期换票
        if time.time() - last_cookie_ts > refresh_secs:
            try:
                gr.set_cookie(obtain_cookie(cfg))
                last_cookie_ts = time.time()
            except CookieError:
                pass

        try:
            gr.refresh_token()
        except CookieError as exc:
            log(f"回合 {round_no}：{exc}")
            log("尝试重新获取 Cookie ...")
            try:
                new_cookie = _obtain_verified(cfg)  # 文件失效自动改试浏览器
                gr.set_cookie(new_cookie)
                last_cookie_ts = time.time()
                log("已恢复")
            except CookieError as exc2:
                log(f"恢复失败：{exc2}")
                time.sleep(5)
                continue

        run_burst(gr, pending, done, dropped)

        if pending:
            time.sleep(interval)

    log("-" * 46)
    if done:
        log(f"成功 {len(done)} 门：")
        for name in done:
            log(f"  · {name}")
    if dropped:
        log(f"停止重试 {len(dropped)} 门（服务器判定当前账号不可选）：")
        for name in dropped:
            log(f"  · {name}")
    if pending:
        log(f"未拿下 {len(pending)} 门：")
        for c in pending:
            log(f"  · {c['name']}（{c['bjdm']}）")
    log("结束。请到「已选课程」页核对。")
    return 0 if not pending else 1


def _save_cookie(cookie: str) -> None:
    """把验证过的 Cookie 固化到 cookie.txt，抢课时读不到浏览器也能兜底。"""
    with open(COOKIE_PATH, "w", encoding="utf-8") as fh:
        fh.write(cookie)
    log(f"✓ Cookie 已保存到 {os.path.basename(COOKIE_PATH)}（作为抢课时的后备）")


def cmd_login(cfg: dict) -> str:
    """现场登录：打开浏览器 → 用户登录 → 回车 → 验证 → 保存 cookie.txt。

    返回验证通过的 Cookie 字符串；验证失败抛 CookieError。
    """
    url = f"http://{cfg.get('target', DOMAIN)}{APP_ENTRY}"
    log(f"正在打开浏览器：{url}")
    log("  （若未登录会自动跳到复旦统一认证，登录后回到选课页）")
    try:
        import webbrowser
        webbrowser.open(url)  # 打开系统默认浏览器
    except Exception as exc:
        log(f"（自动打开浏览器失败：{exc}，请手动打开上面的网址）")

    print()
    print(f"  → 请在浏览器里登录选课系统（推荐 Edge 或 Chrome），")
    print(f"    登录成功、能看到选课页面后，回到这里按【回车】")
    try:
        input("  >>> ")
        waited = True  # 人在场：字段未落盘时可以自动等待重读
    except EOFError:
        print()
        log("（非交互环境，跳过等待，直接尝试读取 Cookie）")
        waited = False

    # 现场登录后强制从浏览器读（用户刚登录，浏览器里就是最新 Cookie），
    # 不走 cookie_source=auto 的文件优先，避免拿到旧 cookie.txt。
    login_cfg = dict(cfg)
    login_cfg["cookie_source"] = "browser"

    # UIS 登录跳转完成后，_WEU 等关键字段可能延迟落盘到浏览器 Cookie 库
    # （实测有时要数十秒）。人在场时自动重读：最多等 LOGIN_WAIT 秒、每
    # LOGIN_INTERVAL 秒读一次并显示倒计时；非交互环境只读一次直接报错。
    deadline = time.monotonic() + LOGIN_WAIT if waited else None
    reads = 0
    while True:
        reads += 1
        cookie = obtain_cookie(login_cfg)
        names = [seg.strip().split("=")[0] for seg in cookie.split(";") if "=" in seg]
        missing = {"JSESSIONID", "_WEU"} - set(names)
        if not missing:
            break
        if deadline is None or time.monotonic() >= deadline:
            hint = (
                f"等待 {LOGIN_WAIT} 秒仍未补全。请确认已在弹出的浏览器里完成登录"
                f"（能看到选课页面），然后重跑一次 --login"
                if waited
                else "请先运行 python src/grab.py --login 现场登录（当前为非交互环境）"
            )
            raise CookieError(f"未读到完整登录态（缺 {', '.join(missing)}）。{hint}")
        _countdown(
            min(LOGIN_INTERVAL, deadline - time.monotonic()),
            f"关键字段尚未落盘（缺 {', '.join(missing)}），自动重试第 {reads} 次",
        )

    gr = Grabber(cfg, cookie)
    gr.refresh_token()  # 抛 CookieError 说明 Cookie 仍无效
    _save_cookie(cookie)
    log("✓ 登录成功，Cookie 已验证有效")
    log("  开抢前若再提示 Cookie 失效，重跑一次即可（几分钟的事）")
    return cookie


def _ask_relogin(cfg: dict) -> bool:
    """Cookie 失效时问用户要不要现场登录。非交互环境直接跳过。"""
    if not sys.stdin.isatty():
        log("  非交互环境，跳过重新登录（可手动运行 python src/grab.py --login）")
        return False
    try:
        ans = input("  要现在打开浏览器重新登录选课系统吗？[Y/n] ").strip().lower()
    except EOFError:
        return False
    return ans != "n"


def _selftest_cookie(cfg: dict) -> str | None:
    """自检专用：取 Cookie → 校验关键字段 → 发请求验证有效。

    失败且环境允许时，引导用户现场登录重试一次；仍失败返回 None。
    成功后把 Cookie 固化到 cookie.txt（供抢课兜底）。
    """
    def try_once() -> tuple[str | None, str]:
        try:
            cookie = obtain_cookie(cfg)
        except CookieError as exc:
            return None, str(exc)
        names = [seg.strip().split("=")[0] for seg in cookie.split(";") if "=" in seg]
        missing = {"JSESSIONID", "_WEU"} - set(names)
        if missing:
            return None, f"缺少关键字段：{', '.join(missing)}（请先在浏览器登录选课系统）"
        gr = Grabber(cfg, cookie)
        try:
            gr.refresh_token()
        except CookieError as exc:
            return None, str(exc)
        return cookie, ""

    cookie, err = try_once()
    if cookie:
        _save_cookie(cookie)
        return cookie

    log(f"✗ Cookie 未通过：{err}")
    if not _ask_relogin(cfg):
        return None
    try:
        return cmd_login(cfg)
    except CookieError as exc:
        log(f"✗ 现场登录仍失败：{exc}")
        return None


def dry_run(cfg: dict) -> int:
    log("=== 自检模式（不会提交任何选课请求）===")
    pending = [c for c in cfg["courses"] if c.get("enabled", True)]
    log(f"目标站点：http://{cfg.get('target', DOMAIN)}{APP_ENTRY}")
    log(f"开抢时间：{cfg.get('start_time')}   结束：{cfg.get('end_time')}")
    log(f"启用课程：{len(pending)} / {len(cfg['courses'])} 门")
    for c in pending:
        log(f"  · {c['name']:<20} {c['bjdm']}  lx={c['lx']} bqmc={c['bqmc']}")
    for c in cfg["courses"]:
        if not c.get("enabled", True):
            log(f"  · {c['name']:<20} （已停用）")

    log("")
    log("正在获取 Cookie ...")
    cookie = _selftest_cookie(cfg)
    if cookie is None:
        log("")
        log("✗ 自检未通过：Cookie 无效或无法取得，请先解决登录问题再开抢。")
        return 1
    names = [seg.strip().split("=")[0] for seg in cookie.split(";") if "=" in seg]
    log(f"✓ 取到 Cookie，共 {len(names)} 个字段：{', '.join(names)}")
    log("✓ Cookie 有效（csrfToken 校验通过）")

    log("")
    log("=== 自检通过 ===")
    log("可以开抢了：双击「scripts/选课助手」选 3 开始抢课，或直接运行 python src/grab.py")
    return 0


def probe(cfg: dict, force: bool) -> int:
    """链路演练：对第一门启用的课程发 1 次真实选课请求，打印服务器原话。"""
    log("=== 链路演练（只发 1 次请求，不重试、不循环）===")
    pending = [c for c in cfg["courses"] if c.get("enabled", True)]
    if not pending:
        log("没有启用任何课程，退出")
        return 1

    start_time = dt.datetime.strptime(cfg["start_time"], "%Y-%m-%d %H:%M:%S")
    opened = dt.datetime.now() >= start_time
    if opened and not force:
        log(f"已过开抢时间（{start_time}），演练会真的提交一次选课请求。")
        log("确认要继续，请在命令末尾加 --force")
        return 1
    if opened:
        log("注意：选课已开放，本次演练等同于真的抢这门课。")

    c = pending[0]
    log(f"演练课程：{c['name']}（{c['bjdm']}）")

    try:
        cookie = obtain_cookie(cfg)
    except CookieError as exc:
        log(f"✗ 取 Cookie 失败：{exc}")
        return 1
    gr = Grabber(cfg, cookie)
    try:
        gr.refresh_token()
    except CookieError as exc:
        log(f"✗ {exc}")
        return 1
    log(f"✓ Cookie 有效，csrfToken = {gr.token[:8]}...")

    log("发送选课请求 ...")
    body = gr.probe_once(c)
    code = body.get("code")
    log(f"服务器返回：HTTP {body.get('http')}  code={code}")
    log(f"服务器原话：{body.get('msg') or '(无 msg 字段)'}")

    if code == 0:
        log("")
        log("=== 演练结论 ===")
        log("服务器拒绝了该请求（code=0），但这恰恰证明链路是通的：")
        log("  · Cookie 与 csrfToken 被接受（否则会跳登录页，根本取不到 token）")
        log("  · bjdm / lx / bqmc 被正确解析（否则会报参数错误）")
        log("  唯一的阻碍就是上面那句原话。条件满足后即可正常选课。")
        return 0

    if code is None:
        log("")
        log("=== 演练结论 ===")
        log("响应不是预期格式，多半是 Cookie 失效或接口有变，请勿直接开抢。")
        return 1

    log("服务器已受理，轮询最终结果 ...")
    rcode, rmsg = gr.poll_result(body.get("msg") or "")
    log(f"最终结果：code={rcode}  msg={rmsg or '(空)'}")
    if rcode == 1:
        log("★ 真的选上了！请到「已选课程」页确认。")
    else:
        log("未选上，原因见上。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="复旦大学研究生选课脚本")
    ap.add_argument("--dry-run", action="store_true", help="只自检，不提交选课请求")
    ap.add_argument("--login", action="store_true", help="打开浏览器现场登录，验证并保存 Cookie 后退出")
    ap.add_argument("--now", action="store_true", help="忽略 start_time，立即开始")
    ap.add_argument("--probe", action="store_true", help="链路演练：发 1 次真实请求看服务器回什么")
    ap.add_argument("--force", action="store_true", help="配合 --probe，开放后也允许演练")
    args = ap.parse_args()

    cfg = load_config()
    if args.dry_run:
        return dry_run(cfg)
    if args.login:
        try:
            cmd_login(cfg)
            return 0
        except CookieError as exc:
            log(f"✗ 登录失败：{exc}")
            return 1
    if args.probe:
        return probe(cfg, force=args.force)
    return run(cfg, start_now=args.now)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log("\n已手动中止")
        sys.exit(130)
