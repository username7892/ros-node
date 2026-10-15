# M20 目标检测 ROS 链路 —— 启动与参数说明

> 目标机：`wxh@192.168.55.1`（Jetson Orin / aarch64 / Ubuntu 20.04 / ROS Noetic）
> 工作空间：`~/project/m20_ros_ws`
> 相机 RTSP：`rtsp://192.168.1.65:554`（正式地址 `/0-0`，本链路用 `/0-1`）

## 0. 包与节点一览

| ROS 包 | 版本 | 语言 | 节点 | 作用 |
|--------|------|------|------|------|
| `m20_camera` | 0.2.0 | C++ | `video_node` | GStreamer 拉 RTSP + NVDEC 硬解，发布 `/m20/image_raw`（不用 cv_bridge）|
| `m20_msgs`   | 0.1.0 | —    | — | 自定义消息 `TargetList.msg` 与服务 `SetTargets.srv` |
| `m20_client` | 0.1.0 | C++ | `client_node` | 调 `SetTargets` 服务，把检测目标上报给 server |
| `m20_server` | 0.1.0 | C++ | `server_node` | 提供 `SetTargets` 服务，经 latched topic 广播目标给 yolo |
| `m20_yolo`   | 0.3.0 | Python | `yolo_node.py` | YOLOE 推理画框，发布 `/m20/image_detected`；写 `box.json` |
| `m20_yolo`   | 0.3.0 | Python | `stream_node.py` | 把 `/m20/image_detected` 编码成 MPEG-TS/H264 经 UDP 推流 |

自定义接口：

- `m20_msgs/TargetList.msg`：`string[] targets`
- `m20_msgs/SetTargets.srv`：请求 `string[] targets` → 响应 `bool success` + `string message`

## 1. 链路总览

```
RTSP 相机 (192.168.1.65/0-1, H.264)
   │  (RTP over TCP)
   ▼
m20_video_node (C++/GStreamer + NVDEC 硬解)  ──发布 /m20/image_raw──┐
                                                                    │
m20_client_node (C++)                                               │
   │  Service: /m20/set_targets                                    │
   ▼                                                                │
m20_server_node (C++)                                               │
   │  reliable + latched Topic: /m20/detect_targets                ▼
   └──────────────────────────────────────────────► m20_yolo_node (Python/YOLOE)
                                                        │ 订阅图 + 目标，推理画框
                                                        ├─发布 /m20/image_detected─┐
                                                        └─写 box.json (SMB)        │
                                                                                   ▼
                                              m20_stream_node (Python/GStreamer)
                                                 MPEG-TS/H264 over UDP → VLC
```

- **video_node**：GStreamer 拉 RTSP，NVDEC 硬解为 BGR，原始帧发布到 `/m20/image_raw`，**不重新编码**。
- **client → server**：通过 Service 上报"要检测什么"。
- **server → yolo**：通过 reliable + latched topic 广播目标，晚加入的 yolo 也能收到。
- **yolo_node**：定频（默认 10Hz）取最新帧推理，画框后发布 `/m20/image_detected`；慢时复用上一次结果。
- **stream_node**：订阅识别流，经 `nvv4l2h264enc` 硬编码 + `mpegtsmux`，UDP 推送，VLC 可直接播放。

## 2. 前置准备（每次开新终端都要做）

工作空间已编译过（`~/project/m20_ros_ws/devel` 存在）。若已改动源码需重新编译，见第 7 节。

启动前先 source 环境（顺序不能反，venv 必须在 ROS 之后 source，以便 `python3` 指向 venv）：

```bash
source /opt/ros/noetic/setup.bash
source ~/venv/dog_det/bin/activate          # YOLOE/ultralytics 依赖都在这个 venv
source ~/project/m20_ros_ws/devel/setup.bash
```

检查依赖是否就绪：

```bash
# 相机是否可达
ping -c 2 192.168.1.65
# 模型权重是否存在（约 63MB，同目录还需有 mobileclip_blt.ts）
ls -la ~/dog_web/python_yoloe/yoloe-v8m-seg.pt
```

## 3. 启动节点

### 3.1 一键启动全链路（推荐）

```bash
source /opt/ros/noetic/setup.bash
source ~/venv/dog_det/bin/activate
source ~/project/m20_ros_ws/devel/setup.bash
roslaunch m20_yolo m20_detect.launch
```

该 launch 会一次拉起 5 个节点：`m20_video_node`、`m20_server_node`、`m20_yolo_node`、`m20_stream_node`、`m20_client_node`（client 启动时下发一次目标 `person helmet`）。

后台运行并落日志：

```bash
nohup roslaunch m20_yolo m20_detect.launch > /tmp/m20_detect.log 2>&1 &
tail -f /tmp/m20_detect.log
```

### 3.2 手动逐个启动（调试用）

```bash
# 终端 1：视频节点
rosrun m20_camera video_node _rtsp_url:=rtsp://192.168.1.65:554/0-1

# 终端 2：server 节点
rosrun m20_server server_node

# 终端 3：yolo 节点
rosrun m20_yolo yolo_node.py

# 终端 4：stream 推流节点（UDP/MPEG-TS）
rosrun m20_yolo stream_node.py

# 终端 5：client 节点（下发检测目标）
rosrun m20_client client_node person helmet
```

> yolo / stream 节点都依赖 venv 与 GStreamer，手动 `rosrun` 前务必已 `source ~/venv/dog_det/bin/activate`。

### 3.3 验证是否正常

```bash
rosnode list                              # 应看到 5 个 /m20_* 节点
rostopic hz /m20/image_raw                # 约 25 Hz，进流正常
rostopic hz /m20/image_detected           # 约 6~10 Hz，出流正常
rostopic echo -n1 /m20/detect_targets     # 应显示 targets: [helmet, person]
rqt_image_view                            # 或 rviz，看 /m20/image_detected 画面
```

看推流（在能访问 `10.21.34.107` 的机器上）：

```bash
vlc udp://@:5000
```

### 3.4 停止 / 重启

**停止整个链路：**

```bash
pkill -f "roslaunch m20_yolo"
pkill -f "video_node|yolo_node|server_node|client_node|stream_node"
pkill -f rosmaster
```

> `pkill -f` 可能误伤同连接的进程，建议分开执行；更稳妥用 `rosnode kill <name>` 逐个杀节点。

**重启（最干净的做法）：**

```bash
# 1) 先全部停掉
pkill -f "roslaunch m20_yolo"; pkill -f "video_node\|yolo_node\|server_node\|client_node\|stream_node"; pkill -f rosmaster
# 2) 等 2 秒确认没有残留进程
sleep 2; pgrep -af "rosmaster|roslaunch|video_node|yolo_node|server_node|client_node|stream_node"
# 3) 重新 source 与启动
source /opt/ros/noetic/setup.bash
source ~/venv/dog_det/bin/activate
source ~/project/m20_ros_ws/devel/setup.bash
roslaunch m20_yolo m20_detect.launch
```

**只重启单个节点（保留 rosmaster 与其它节点）：**

```bash
# 例如只重启 yolo 节点
rosnode kill /m20_yolo_node
rosrun m20_yolo yolo_node.py            # 或由 roslaunch 自动 respawn（若配了 respawn="true"）
```

> 若想让某节点崩溃后自动拉起，在该 `<node>` 上加 `respawn="true" respawn_delay="2"`。

## 4. 参数修改

### 4.1 参数总表

| 节点 | 参数名 | 默认值 | 说明 |
|------|--------|--------|------|
| video_node | `rtsp_url` | `rtsp://192.168.1.65:554/0-1` | 相机 RTSP 地址。`-0` 是 H.265、`-1` 是 H.264，本节点管道写死 H.264，**只能拉 `-1`** |
| video_node | `topic` | `/m20/image_raw` | 发布图像话题名 |
| video_node | `frame_id` | `m20_camera` | 图像 header.frame_id |
| video_node | `width` / `height` | `800` / `600` | 声明尺寸（实际尺寸以解码出来的 caps 为准，管道不缩放）|
| server_node | `service` | `/m20/set_targets` | Service 名 |
| server_node | `targets_topic` | `/m20/detect_targets` | 广播目标的 topic 名（latched）|
| client_node | `targets` | `person,helmet` | 检测目标，逗号分隔；也可用命令行参数覆盖 |
| client_node | `service` | `/m20/set_targets` | 目标 server 的 Service 名 |
| yolo_node | `input_topic` | `/m20/image_raw` | 输入图像话题 |
| yolo_node | `targets_topic` | `/m20/detect_targets` | 输入目标话题 |
| yolo_node | `output_topic` | `/m20/image_detected` | 输出识别流话题 |
| yolo_node | `model` | `~/dog_web/python_yoloe/yoloe-v8m-seg.pt` | 模型权重绝对路径 |
| yolo_node | `device` | `cuda:0` | 推理设备，`cuda:0` 或 `cpu` |
| yolo_node | `conf` | `0.25` | 置信度阈值（越低框越多、误检越多）|
| yolo_node | `output_fps` | `10` | 识别流发布频率（Hz）。推理慢时会复用上一帧结果 |
| yolo_node | `box_json_path` | SMB `shared_dir/box.json` | 检测框 JSON 输出路径（给外部系统读）|
| yolo_node | `box_json_fallback` | `/tmp/m20_box.json` | 上面写失败时的本地回退路径 |
| yolo_node | `box_enable` | `true` | 是否写 box.json |
| stream_node | `input_topic` | `/m20/image_detected` | 待推流的输入话题 |
| stream_node | `host` | `10.21.34.107` | UDP 推流目标 IP |
| stream_node | `port` | `5000` | UDP 推流目标端口 |
| stream_node | `bitrate` | `4000000` | H.264 码率（bps）|
| stream_node | `fps` | `10` | 推流帧率 |
| stream_node | `width` / `height` | `1280` / `720` | appsrc 声明尺寸，须与输入图一致 |

### 4.2 三种修改方式

**方式一：改 launch 文件（最常用，推荐）**

编辑 `~/project/m20_ros_ws/src/m20_yolo/launch/m20_detect.launch`，直接改 `<param>` 的 `value`，然后重新 `roslaunch`。修改 launch 不需要 catkin_make。

```xml
<node pkg="m20_client" type="client_node" name="m20_client_node" output="screen"
      args="person helmet vest"/>        <!-- 想加目标就改这里 -->

<node pkg="m20_yolo" type="yolo_node.py" name="m20_yolo_node" output="screen">
  <param name="conf"       value="0.4"/>   <!-- 调高阈值减少误检 -->
  <param name="output_fps" value="8"/>     <!-- 降频减轻 GPU -->
</node>

<node pkg="m20_yolo" type="stream_node.py" name="m20_stream_node" output="screen">
  <param name="host" value="10.21.34.107"/> <!-- 改推流目标 IP -->
  <param name="port" value="5000"/>         <!-- 改推流端口 -->
</node>
```

**方式二：命令行传参（临时测试，不改文件）**

`rosrun` 时用 `_参数名:=值` 覆盖私有参数：

```bash
rosrun m20_camera video_node _rtsp_url:=rtsp://192.168.1.65:554/0-1 _topic:=/m20/image_raw
rosrun m20_yolo yolo_node.py _conf:=0.4 _output_fps:=8 _device:=cpu
rosrun m20_yolo stream_node.py _host:=10.21.34.107 _port:=5000
rosrun m20_client client_node person vest helmet   # 位置参数即目标列表
```

**方式三：环境变量（yolo 节点专用，优先级低于 ROS 参数）**

yolo 节点启动时会先读这几个环境变量作为默认值：

```bash
export M20_YOLO_MODEL=/home/wxh/dog_web/python_yoloe/yoloe-v8m-seg.pt
export M20_YOLO_DEVICE=cuda:0
export M20_YOLO_CONF=0.3
export M20_OUTPUT_HZ=8
export M20_BOX_JSON=/tmp/m20_box.json
export M20_BOX_JSON_FALLBACK=/tmp/m20_box.json
rosrun m20_yolo yolo_node.py
```

> 优先级：`rosparam`（launch/命令行） > 环境变量 > 代码内硬编码默认值。

### 4.3 运行时改目标（不用重启节点）

server 的 topic 是 latched 的，目标一变，yolo 会调用一次 `set_classes()` 并立即生效。运行中重发目标只需再跑一次 client：

```bash
rosrun m20_client client_node person vest
```

或直接调用 Service：

```bash
rosservice call /m20/set_targets "targets: ['person', 'helmet']"
```

### 4.4 常用调参场景

| 需求 | 改什么 |
|------|--------|
| 换检测类别 | client 的 `args` / `targets`，如 `person helmet vest fire` |
| 漏检多 | 调低 `conf`（如 `0.15`），代价是误检变多 |
| 误检多 | 调高 `conf`（如 `0.4`）|
| GPU 占用高 / 掉帧 | 调低 yolo `output_fps`（如 `6`）与 stream `fps` |
| 换模型 | 改 `model` 路径，需保证该目录下同时存在 `mobileclip_blt.ts` |
| 换相机 | 改 `rtsp_url`；注意管道只支持 H.264 码流 |
| box.json 写到别处 | 改 `box_json_path`；写失败会回退到 `/tmp/m20_box.json` |
| 推流到别的机器/端口 | 改 stream 的 `host` / `port`，VLC 侧对应 `udp://@:端口` |
| 推流卡顿 / 花屏 | 调低 `bitrate`，或确认 `width/height/fps` 与源一致 |

## 5. 录制视频流

目标机**没有 ffmpeg**，用 venv 里的 OpenCV 录制。示例：订阅 `/m20/image_detected` 录 20 秒。

```python
# /tmp/rec_detected.py
#!/usr/bin/env python3
import rospy, cv2, numpy as np, time
from sensor_msgs.msg import Image

OUT, FPS, DUR = "/tmp/m20_detected.mp4", 10.0, 20.0
writer, cnt = None, 0

def cb(msg):
    global writer, cnt
    buf = np.frombuffer(msg.data, dtype=np.uint8)
    img = buf.reshape(msg.height, msg.step // 3, 3)[:, :msg.width, :]
    if msg.encoding == "rgb8":
        img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
    img = np.ascontiguousarray(img)
    if writer is None:
        h, w = img.shape[:2]
        writer = cv2.VideoWriter(OUT, cv2.VideoWriter_fourcc(*"mp4v"), FPS, (w, h))
    writer.write(img)
    cnt += 1

rospy.init_node("rec_detected", anonymous=True)
rospy.Subscriber("/m20/image_detected", Image, cb)
t0 = time.time()
r = rospy.Rate(10)
while not rospy.is_shutdown() and time.time() - t0 < DUR:
    r.sleep()
if writer:
    writer.release()
print("WROTE %d frames -> %s" % (cnt, OUT))
```

运行并回传：

```bash
source /opt/ros/noetic/setup.bash && source ~/venv/dog_det/bin/activate
source ~/project/m20_ros_ws/devel/setup.bash
python3 /tmp/rec_detected.py
# 本地拉回
scp wxh@192.168.55.1:/tmp/m20_detected.mp4 ./
```

## 6. 重新编译

改动了 C++ 源码（`video_node.cpp` / `server_node.cpp` / `client_node.cpp`）或 `.msg`/`.srv` 后必须重编；只改 launch 或 `yolo_node.py` / `stream_node.py` 不需要（Python 直接执行）。

```bash
source /opt/ros/noetic/setup.bash
cd ~/project/m20_ros_ws
catkin_make            # 或 catkin_make -DCMAKE_BUILD_TYPE=Release
source devel/setup.bash
```

> 依赖检查：`m20_camera` 需要系统装有 OpenCV 4 与 GStreamer 开发库（`libgstreamer1.0-dev`、`libgstreamer-plugins-base1.0-dev`）；`stream_node.py` 运行期需要 Jetson 上的 `nvvidconv`、`nvv4l2h264enc`、`mpegtsmux`、`udpsink` 插件。

## 7. 常见问题

| 现象 | 排查 |
|------|------|
| `rostopic hz /m20/image_raw` 无输出 | 相机不可达（ping 192.168.1.65）或 rtsp_url 用了 `-0`（H.265 管道不支持）|
| video_node 报"管道创建失败" | GStreamer 插件缺失，或 RTSP 地址/协议不对 |
| yolo 启动卡住 / 联网下载 | 模型目录下缺 `mobileclip_blt.ts`；节点会 `chdir` 到模型目录找它 |
| client 报 "service 不存在" | server 未起，或 client 比 server 先起（等待 10s 超时）|
| 无检测框 | 目标列表为空（client 没成功下发），或 `conf` 太高 |
| `/m20/image_detected` 频率低于 `output_fps` | 正常：推理慢于设定频率时会复用上一帧，GPU 满载 |
| 画面无框但 `dets>0` | 目标名与 `set_classes` 的词不一致，会过滤掉 |
| VLC 播放不出画面 | 确认 stream `host/port` 与播放端 `udp://@:端口` 一致；`width/height` 与源不符会花屏 |
| `stream_node` 报 GStreamer 错误 | 缺 `nvv4l2h264enc`/`mpegtsmux` 插件（x86 上无 NVDEC/硬编码，不可用）|
| ImportError: cv2 / ultralytics / gi | 忘了 `source ~/venv/dog_det/bin/activate`，或顺序放在了 ROS 之前 |

## 8. 环境要点

- 目标机是 **aarch64 (Jetson Orin)**，Python **3.8**，用 `~/venv/dog_det`，与 x86 的 conda 环境不通用。
- **不要用 `cv_bridge`**：它链接 OpenCV 4.2，与 venv 的 cv2(4.11) ABI 不兼容；节点内已手写 Image↔numpy 转换。
- NVDEC 硬解、`nvvidconv`、`nvv4l2h264enc` 依赖 Jetson 的 GStreamer 插件，x86 上不可用。
- 模型加载时会 `chdir` 到权重所在目录，避免 ultralytics 联网下载 mobileclip。
- `box.json` 目标为 SMB 共享（`10.21.31.104/shared_dir`，经 gvfs 挂载），写失败自动回退 `/tmp`。
- `stream_node.py` 用独立定时线程按 fps 主动 `push-buffer`（udpsink 无 backpressure，靠 need-data 不会稳定触发）；无客户端时也持续推送，VLC 随时连上都能看到画面。
