#!/bin/sh
# 同一个 boot 里交替两臂：flag 文件改 `prefill_help_frac`，不重启。
#
# 为什么这样测：这台机器跨 boot 的同配置漂移到过 9%（127.4 / 133.0 / 139.1 tok/s），5% 以下的结论靠重启式 A/B
# 永远立不住。`include/strata/core/runtime_flag.hpp` 让开关能在运行时翻，于是两臂共享时钟、页缓存与驻留集。
#
# 用法：flag-ab.sh <label> [rounds]
#   label  记录用的名字；tag 会写成 <label>-A1 / <label>-B1 / <label>-A2 ...
#   环境：NEW（每轮新增 token，默认 4524）、REPS（每臂轮数，默认 2）、CTX（起始上下文，默认 100）
#          FLAGS（flag 文件，默认 /opt/ai/run/flags）、KEY（prefill_help_frac 那个键名）
set -u
BIN=${BIN:-/opt/ai/bin}
LABEL=${1:?usage: flag-ab.sh <label> [rounds]}
ROUNDS=${2:-2}
NEW=${NEW:-4524}
REPS=${REPS:-2}
CTX=${CTX:-100}
FLAGS=${FLAGS:-/opt/ai/run/flags}
KEY=${KEY:-prefill_help_frac}
OUT=${OUT:-/opt/ai/logs/flag-ab-$LABEL.log}

set_flag() {   # 先写临时文件再 mv：引擎每 100 ms 只读一次，不能让它读到半个文件
    printf '# runtime switches\nprefill_help=1\n%s=%s\n' "$KEY" "$1" > "$FLAGS.tmp" && mv -f "$FLAGS.tmp" "$FLAGS"
    sleep 0.3
    printf '  flag: %s=%s  (%s)\n' "$KEY" "$1" "$(cat "$FLAGS" | tr '\n' ' ')"
}

: > "$OUT"
echo "flag A/B: label=$LABEL new=$NEW reps=$REPS rounds=$ROUNDS" | tee -a "$OUT"

i=0
while [ "$i" -lt "$ROUNDS" ]; do
    i=$((i + 1))
    for arm in A B; do
        if [ "$arm" = A ]; then set_flag 0; else set_flag 0.4; fi
        # 每臂之间留一点空档，让上一轮的后台拷贝落完
        sleep 2
        echo "\n=== $LABEL-$arm$i ===" | tee -a "$OUT"
        python3 "$BIN/turn-ttft.py" "$LABEL-$arm$i" "$NEW" "$REPS" "$CTX" 8 >> "$OUT" 2>&1
        grep -E "首字|>>>" "$OUT" | tail -2
    done
done
set_flag 0   # 收尾：回默认（不帮）
echo "\n配对结果：" | tee -a "$OUT"
for x in $(seq 1 "$ROUNDS"); do
    for prev in A B; do :; done
    python3 "$BIN/paired_ttft.py" "$LABEL-A$x" "$LABEL-B$x" 2>&1 | tail -4 | tee -a "$OUT"
done
