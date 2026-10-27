#!/usr/bin/env bash
# 打包成 Lambda 可用的目錄（build/）與 zip（job_crawler.zip）。依賴只有純 Python 的 pg8000。
set -euo pipefail
cd "$(dirname "$0")"
rm -rf build job_crawler.zip
mkdir -p build
python3 -m pip install -q -r requirements.txt -t build
cp -r crawler sources handler.py build/
find build -name "__pycache__" -type d -prune -exec rm -rf {} +
(cd build && zip -qr ../job_crawler.zip .)
echo "build/ 與 job_crawler.zip 已產生（$(du -h job_crawler.zip | cut -f1)）"
