#!/bin/zsh
cd "${0:A:h}/.." || exit 1

PYTHON="${PWD}/.venv/bin/python"
if [ ! -x "$PYTHON" ]; then
  echo "正在创建本地 Python 环境..."
  python3 -m venv .venv || exit 1
fi

if ! "$PYTHON" -c "import flask, requests, browser_cookie3" >/dev/null 2>&1; then
  echo "正在安装依赖..."
  "$PYTHON" -m pip install -r requirements.txt || exit 1
fi

exec "$PYTHON" webui.py --open
