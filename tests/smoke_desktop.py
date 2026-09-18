"""Exercise a source or frozen desktop service without accessing the university."""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--executable")
    args = parser.parse_args()
    command = [str(Path(args.executable).resolve())] if args.executable else [sys.executable, str(ROOT / "webui.py")]
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with tempfile.TemporaryDirectory(prefix="fdu-desktop-smoke-") as directory:
        env = {**os.environ, "FDU_WEBUI_DATA_DIR": directory, "FDU_WEBUI_NO_OPEN": "1", "PYTHONIOENCODING": "utf-8"}
        metadata = Path(directory, "instance.json")
        occupied = socket.socket()
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        preferred = occupied.getsockname()[1]
        command += ["--port", str(preferred)]
        options = {"creationflags": 0x08000000} if sys.platform == "win32" else {}

        def request(path, data=None, token=None):
            headers = {"Content-Type": "application/json"}
            if token:
                headers["X-FDU-App-Token"] = token
            req = urllib.request.Request(url + path, data=None if data is None else json.dumps(data).encode(), headers=headers)
            with opener.open(req, timeout=3) as response:
                return json.load(response)

        def start():
            child = subprocess.Popen(command, env=env, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **options)
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                try:
                    info = json.loads(metadata.read_text(encoding="utf-8"))
                    with opener.open(f"http://127.0.0.1:{info['port']}/api/status", timeout=1) as response:
                        assert json.load(response)["running"] is False
                    return child, info
                except (OSError, ValueError, KeyError):
                    if child.poll() is not None:
                        raise AssertionError(f"App exited during startup: {child.returncode}")
                    time.sleep(.1)
            child.kill()
            child.wait()
            raise AssertionError("Startup timed out")

        child = None
        try:
            child, info = start()
            assert info["port"] != preferred, "Port fallback failed"
            url = f"http://127.0.0.1:{info['port']}"
            config = request("/api/config")
            assert config["login"]["saved"] is False
            assert config["app"]["version"] == "1.2.0"
            assert config["app"]["data_dir"] == str(Path(directory).resolve())
            try:
                request("/api/app/quit", {})
                raise AssertionError("Unauthenticated quit was accepted")
            except urllib.error.HTTPError as error:
                assert error.code == 403
            second = subprocess.run(command, env=env, cwd=ROOT, timeout=15, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **options)
            assert second.returncode == 0, "Reopening should reuse the existing service"
            assert child.poll() is None
            # With file-only auth and no cookie file, the worker exits before
            # any HTTP request. This also tests frozen-worker UTF-8 pipes.
            assert config["config"]["cookie_source"] == "file"
            assert not Path(directory, "cookie.txt").exists()
            request("/api/tasks", {"action": "dry_run"})
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                result = request("/api/status")
                if result["finished_at"]:
                    break
                time.sleep(.1)
            assert result["finished_at"] and result["exit_code"] == 1
            assert "自检模式" in result["output"] and "cookie.txt 不存在" in result["output"]
            Path(directory, "cookie.txt").write_text("JSESSIONID=LOCAL-TEST; _WEU=LOCAL-TEST", encoding="utf-8")
            request("/api/app/quit", {}, info["token"])
            assert child.wait(timeout=10) == 0
            assert not metadata.exists()
            child, info = start()
            url = f"http://127.0.0.1:{info['port']}"
            assert request("/api/config")["login"]["saved"] is True
            request("/api/app/quit", {}, info["token"])
            assert child.wait(timeout=10) == 0
            print("PASS: startup, port fallback, single instance, local worker and UTF-8 output, authenticated quit, restart and saved-login persistence.")
        finally:
            occupied.close()
            if child and child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
    return 0


if __name__ == "__main__":
    sys.exit(main())
