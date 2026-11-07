#!/bin/bash
# rec6 のテイクを順に録画する（1 本ずつ。並列にするとコマ落ちする）。使い方: bash tools/video/rec_batch6.sh R1g R2g ...
cd "$(dirname "$0")/../.."
export FFMPEG=C:/ai/bin/ffmpeg.exe PYTHONIOENCODING=utf-8 MSYS_NO_PATHCONV=1 HD_Q=${HD_Q:-88}
for id in "$@"; do
  echo "=== $id"
  python tools/video/record_hd.py --takes tools/video/takes_r6.json --take "$id" --out "outputs/video/rec6/$id.mp4" --gpu --cursor 2>&1 | tail -4
done
echo ALLDONE
