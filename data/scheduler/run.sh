#!/usr/bin/env bash
# 第一次執行會自動建立虛擬環境並安裝爬蟲依賴；之後直接執行 scheduler.py
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "建立虛擬環境（只需要一次）⋯"
  python3 -m venv .venv
  .venv/bin/pip install -q -r ../job_crawlers/requirements.txt
fi
exec .venv/bin/python scheduler.py "$@"
