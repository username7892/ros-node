#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
m20_yolo/scripts/yolo_node.py

【yolo 节点 / Python】
  订阅 /m20/image_raw         (视频帧, 来自视频节点)
  订阅 /m20/detect_targets    (检测目标, 来自 server 的 reliable topic)
  用 YOLOE(.pt) 推理 → OpenCV 画对应目标的框
  发布 /m20/image_detected    (识别流, 恒定 10Hz)

设计要点：
  - 直接用 .pt 模型 + ultralytics，无需 ONNX/后处理
  - set_classes 只在"目标变化时"调用一次（有开销，不每帧调）
  - 推理与发布解耦：10Hz 节拍线程取最新帧推理发布，慢时复用上次结果
"""

import json
import os
import threading
import time

import cv2
import numpy as np
import rospy
from sensor_msgs.msg import Image
from m20_msgs.msg import TargetList

# ---------------- 手动 Image <-> numpy 转换 ----------------
# 不用 cv_bridge：它背后链接 OpenCV 4.2，与 venv 的 cv2(4.11) ABI 不兼容。
def imgmsg_to_bgr(msg):
    """sensor_msgs/Image -> BGR numpy (H,W,3)"""
    if msg.encoding not in ("bgr8", "rgb8"):
        raise ValueError("不支持编码: %s" % msg.encoding)
    buf = np.frombuffer(msg.data, dtype=np.uint8)
    img = buf.reshape(msg.height, msg.step // 3, 3)[:, : msg.width, :]
    img = img.copy()
    if msg.encoding == "rgb8":
        img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
    return img


def bgr_to_imgmsg(img, header=None):
    """BGR numpy -> sensor_msgs/Image"""
    msg = Image()
    if header is not None:
        msg.header = header
    msg.height, msg.width = img.shape[:2]
    msg.encoding = "bgr8"
    msg.is_bigendian = 0
    msg.step = img.shape[1] * 3
    msg.data = img.tobytes()
    return msg

# ---------------- 配置 ----------------
# 模型放在 ~/dog_web/python_yoloe/ (与 yolo_ws 共用同一份权重),
# 用绝对路径避免 rosrun 时 CWD=~ 触发 ultralytics 联网下载
_MODEL_DIR = os.path.expanduser("~/dog_web/python_yoloe")
MODEL_PATH = os.getenv("M20_YOLO_MODEL",
                       os.path.join(_MODEL_DIR, "yoloe-v8m-seg.pt"))
DEVICE = os.getenv("M20_YOLO_DEVICE", "cuda:0")
CONF = float(os.getenv("M20_YOLO_CONF", "0.25"))
OUTPUT_HZ = float(os.getenv("M20_OUTPUT_HZ", "10"))

# ---------------- 框位置 JSON 输出 ----------------
# 检测框连同"该帧图像的时间戳"写入 box.json, 覆盖式(每帧只留最新)。
# 目标为 10.21.31.104 的 SMB 共享 shared_dir, 已由桌面环境挂载到 gvfs, 直接当本地文件写。
BOX_JSON_PATH = os.getenv(
    "M20_BOX_JSON",
    "/run/user/1000/gvfs/smb-share:server=10.21.31.104,share=shared_dir/box.json")
BOX_JSON_FALLBACK = os.getenv("M20_BOX_JSON_FALLBACK", "/tmp/m20_box.json")


class YoloNode:
    def __init__(self):
        rospy.init_node("m20_yolo_node", anonymous=False)

        in_topic = rospy.get_param("~input_topic", "/m20/image_raw")
        tgt_topic = rospy.get_param("~targets_topic", "/m20/detect_targets")
        out_topic = rospy.get_param("~output_topic", "/m20/image_detected")

        self.model_path = rospy.get_param("~model", MODEL_PATH)
        self.device = rospy.get_param("~device", DEVICE)
        self.conf = rospy.get_param("~conf", CONF)
        self.output_hz = rospy.get_param("~output_fps", OUTPUT_HZ)

        # 状态
        self.lock = threading.Lock()
        self.latest_frame = None
        self.latest_stamp = None        # 最近一帧图像的 header.stamp
        self.targets = []
        self.current_set_classes = []   # 已经 set 进模型的词
        self.last_dets = []             # 上次检测结果（复用）
        self.has_frame = False

        # 框位置 JSON 配置
        self.box_path = rospy.get_param("~box_json_path", BOX_JSON_PATH)
        self.box_fallback = rospy.get_param("~box_json_fallback", BOX_JSON_FALLBACK)
        self.box_enable = rospy.get_param("~box_enable", True)
        rospy.loginfo("[yolo] box.json -> %s", self.box_path)

        # 关键: 切到模型目录。set_classes() 会用 mobileclip 文本编码器,
        # 若 CWD 下找不到 mobileclip_blt.ts(572MB) 就会联网下载(极慢/卡死)
        _mdir = os.path.dirname(os.path.abspath(self.model_path))
        if os.path.isdir(_mdir):
            os.chdir(_mdir)
            rospy.loginfo("[yolo] CWD -> %s", _mdir)

        # 加载模型
        rospy.loginfo("[yolo] 加载模型 %s ...", self.model_path)
        from ultralytics import YOLOE
        self.model = YOLOE(self.model_path).to(self.device)
        rospy.loginfo("[yolo] 模型加载完成")

        # 订阅
        rospy.Subscriber(in_topic, Image, self.on_image, queue_size=1)
        rospy.Subscriber(tgt_topic, TargetList, self.on_targets, queue_size=1)
        self.pub = rospy.Publisher(out_topic, Image, queue_size=1)

        rospy.loginfo("[yolo] 订阅 %s + %s → 发布 %s @ %.1fHz",
                      in_topic, tgt_topic, out_topic, self.output_hz)

        # 10Hz 输出线程
        self.running = True
        self.thread = threading.Thread(target=self.publish_loop, daemon=True)
        self.thread.start()

    # ---- 视频帧：只存最新帧 ----
    def on_image(self, msg):
        try:
            frame = imgmsg_to_bgr(msg)
        except Exception as e:
            rospy.logwarn_throttle(2.0, "[yolo] 图像转换失败: %s", e)
            return
        with self.lock:
            self.latest_frame = frame
            self.latest_stamp = msg.header.stamp
            self.has_frame = True

    # ---- 目标：更新检测词 ----
    def on_targets(self, msg):
        tgts = [t.strip() for t in msg.targets if t.strip()]
        # 去重保序
        seen = set()
        tgts = [t for t in tgts if not (t in seen or seen.add(t))]
        with self.lock:
            self.targets = tgts
        rospy.loginfo("[yolo] 收到检测目标: %s", tgts)

    # ---- 框位置 JSON: 写本地 + scp 推送到目标机 ----
    def write_box_json(self, dets, stamp, shape):
        """把本帧的框位置与图像时间戳写入 box.json（覆盖式）。

        目标为 SMB 共享目录（gvfs 挂载），直接以普通文件写入。
        写入失败时回退到本地 /tmp，保证主流程不受影响。

        JSON 结构:
        {
          "stamp": {"secs": .., "nsecs": .., "sec": 1234.5678},
          "width": 1280, "height": 720,
          "objects": [{"cls": "person", "conf": 0.87, "xyxy": [x1,y1,x2,y2]}, ...]
        }
        """
        if not self.box_enable:
            return
        # 时间戳：用图像 header.stamp（可能是空值 0）
        if stamp is None:
            secs = nsecs = 0
        else:
            secs, nsecs = stamp.secs, stamp.nsecs
        payload = {
            "stamp": {"secs": secs, "nsecs": nsecs,
                      "sec": secs + nsecs * 1e-9},
            "height": int(shape[0]),
            "width": int(shape[1]),
            "objects": [
                {"cls": name, "conf": round(c, 4), "xyxy": list(xyxy)}
                for name, c, xyxy in dets
            ],
        }
        text = json.dumps(payload, ensure_ascii=False)
        # 覆盖式写入。
        # 注意: 目标可能是 SMB/CIFS 挂载, 其 rename 不保证原子性且会留下 .tmp,
        # 故这里直接覆盖写(单帧 100+ 字节, 一次 write 即可)。
        for path in (self.box_path, self.box_fallback):
            try:
                with open(path, "w") as f:
                    f.write(text)
                return
            except Exception as e:
                rospy.logwarn_throttle(5.0, "[yolo] 写 box.json 失败(%s): %s", path, e)

    # ---- 10Hz 输出循环 ----
    def publish_loop(self):
        rate = rospy.Rate(self.output_hz)
        while not rospy.is_shutdown() and self.running:
            with self.lock:
                frame = None if self.latest_frame is None else self.latest_frame.copy()
                targets = list(self.targets)
                stamp = self.latest_stamp
                has = self.has_frame

            if not has or frame is None:
                rate.sleep()
                continue

            if targets:
                # 目标变化时重设词表（有开销，只调一次）
                if targets != self.current_set_classes:
                    try:
                        self.model.set_classes(targets)
                        self.current_set_classes = list(targets)
                        rospy.loginfo("[yolo] set_classes -> %s", targets)
                    except Exception as e:
                        rospy.logerr("[yolo] set_classes 失败: %s", e)

                t0 = time.time()
                res = self.model.predict(frame, conf=self.conf, verbose=False)[0]
                ms = (time.time() - t0) * 1000
                dets = []
                if res.boxes is not None:
                    for box in res.boxes:
                        cls_id = int(box.cls[0])
                        name = res.names.get(cls_id, str(cls_id))
                        if name not in targets:
                            continue
                        c = float(box.conf[0])
                        x1, y1, x2, y2 = map(int, box.xyxy[0])
                        dets.append((name, c, (x1, y1, x2, y2)))
                self.last_dets = dets
                self.write_box_json(dets, stamp, frame.shape)

                vis = frame.copy()
                for name, c, (x1, y1, x2, y2) in dets:
                    cv2.rectangle(vis, (x1, y1), (x2, y2), (0, 255, 0), 2)
                    cv2.putText(vis, f"{name} {c:.2f}", (x1, max(y1 - 6, 12)),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
                cv2.putText(vis, f"targets={len(targets)} dets={len(dets)} {ms:.0f}ms",
                            (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
            else:
                # 没目标：直接转发原图
                vis = frame

            try:
                out_msg = bgr_to_imgmsg(vis, header=None)
                self.pub.publish(out_msg)
            except Exception as e:
                rospy.logwarn_throttle(2.0, "[yolo] 发布失败: %s", e)

            rate.sleep()

    def spin(self):
        rospy.spin()


if __name__ == "__main__":
    try:
        node = YoloNode()
        node.spin()
    except rospy.ROSInterruptException:
        pass
