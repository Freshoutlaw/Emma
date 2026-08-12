#!/usr/bin/env bash
# Resumable downloader for the huge opencv wheels (flaky-network mitigation).
# Each curl uses -C - so an interrupted transfer resumes, and the outer loop
# retries until the file is plausibly complete. Logs to /tmp/wheel_dl.log.
set -u
cd "$(dirname "$0")"

urls=(
  "https://files.pythonhosted.org/packages/cf/4c/c73f828fdbcd37eaf21d08fa852544a3ca7c2dbb3ea76873d64f2ea413d1/opencv_python-5.0.0.93-cp37-abi3-manylinux_2_28_x86_64.whl"
  "https://files.pythonhosted.org/packages/8b/39/f87d154d2dca8e9815ca9e4f925aee41e5931162da730d17f3200e7786f3/opencv_contrib_python-5.0.0.93-cp37-abi3-manylinux_2_28_x86_64.whl"
)

for u in "${urls[@]}"; do
  f=$(basename "$u")
  # opencv_python is ~73.8 MB, contrib ~74 MB; treat >=70 MB as complete.
  until [ -f "$f" ] && [ "$(stat -c %s "$f" 2>/dev/null || echo 0)" -ge 70000000 ]; do
    echo "$(date +%T) downloading $f ..."
    curl -sS -C - -L --retry 5 --retry-delay 3 -o "$f" "$u" && break
    echo "$(date +%T) attempt failed for $f, retrying in 5s"
    sleep 5
  done
  echo "DONE $f $(stat -c %s "$f")"
done
