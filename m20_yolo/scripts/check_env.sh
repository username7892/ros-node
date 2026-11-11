#!/usr/bin/env bash
# 检查本机是否具备跑通链路所需的要素。不做修改，只报告。
set -u
ok() { echo "  [OK]   $*"; }
bad() { echo "  [FAIL] $*"; }
warn() { echo "  [WARN] $*"; }

echo "== 1. 参数文件 =="
CFG="$(dirname "$0")/../config/params.yaml"
[ -f "$CFG" ] && ok "params.yaml 存在" || bad "缺少 $CFG"

echo "== 2. GStreamer 插件 =="
need=(rtspsrc rtph264depay h264parse nvv4l2decoder nvvidconv nvv4l2h264enc
      appsrc mpegtsmux udpsink rtspclientsink)
for p in "${need[@]}"; do
  if gst-inspect-1.0 "$p" >/dev/null 2>&1; then ok "$p"; else bad "$p 缺失"; fi
done

echo "== 3. MediaMTX 是否在听 8554 =="
if ss -ltn 2>/dev/null | grep -q ':8554 '; then ok "8554 已监听"; else warn "8554 未监听 (MediaMTX 没起来?)"; fi
if ss -ltn 2>/dev/null | grep -q ':8889 '; then ok "8889 已监听 (WebRTC/WHEP)"; else warn "8889 未监听"; fi

echo "== 4. 上游 RTSP 源 =="
UP=$(python3 - "$CFG" <<'PY'
import sys,yaml
print(yaml.safe_load(open(sys.argv[1]))['video_node']['rtsp_url'])
PY
)
echo "  上游: $UP"
if command -v ffprobe >/dev/null 2>&1; then
  ffprobe -v error -rtsp_transport tcp -select_streams v:0 \
    -show_entries stream=codec_name,width,height,r_frame_rate \
    -of default=nw=1 -timeout 5000000 "$UP" 2>&1 | sed 's/^/    /'
else
  warn "无 ffprobe, 跳过上游探测"
fi

echo "== 5. 模型与 venv =="
MODEL=$(python3 - "$CFG" <<'PY'
import sys,yaml
print(yaml.safe_load(open(sys.argv[1]))['yolo_node']['model'])
PY
)
[ -f "$MODEL" ] && ok "模型存在: $MODEL" || bad "模型不存在: $MODEL"
[ -x /home/wxh/venv/dog_det/bin/python ] && ok "venv python" || bad "venv python 缺失"
