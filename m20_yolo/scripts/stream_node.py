#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
m20_yolo/scripts/stream_node.py

【检测视频流推送节点 / MPEG-TS over UDP】
  订阅 /m20/image_detected (sensor_msgs/Image, bgr8)
  经 GStreamer: appsrc -> videoconvert -> nvvidconv -> nvv4l2h264enc
              -> h264parse -> mpegtsmux -> udpsink
  以 MPEG-TS/H264 推送到指定 UDP 地址端口, VLC 可直接播放。

  VLC 播放方法 (无需 SDP):
    vlc udp://@:5000

  实现要点:
    - 用独立定时线程按 fps 主动 push-buffer, 不依赖 appsrc 的 need-data
      (udpsink 无 backpressure, need-data 不会稳定触发, 会导致不出包)
    - 无客户端时也持续推送, VLC 随时连接都能看到画面

  依赖: GStreamer 插件 appsrc, videoconvert, nvvidconv,
        nvv4l2h264enc, h264parse, mpegtsmux, udpsink
"""

import threading

import gi
gi.require_version("Gst", "1.0")
from gi.repository import Gst, GLib

import rospy
from sensor_msgs.msg import Image


class StreamNode:
    def __init__(self):
        rospy.init_node("m20_stream_node", anonymous=False)

        self.topic = rospy.get_param("~input_topic", "/m20/image_detected")
        self.host = rospy.get_param("~host", "127.0.0.1")
        self.port = int(rospy.get_param("~port", 5000))
        self.bitrate = int(rospy.get_param("~bitrate", 4000000))
        self.fps = int(rospy.get_param("~fps", 10))
        self.width = int(rospy.get_param("~width", 1280))
        self.height = int(rospy.get_param("~height", 720))

        Gst.init(None)

        # MPEG-TS over UDP: VLC 可直接 udp://@:port 播放, 无需 SDP
        launch = (
            "appsrc name=src is-live=true format=time do-timestamp=true "
            "max-bytes=0 "
            "! video/x-raw,format=BGR,width=%d,height=%d,framerate=%d/1 "
            "! videoconvert "
            "! nvvidconv "
            "! video/x-raw(memory:NVMM),format=I420 "
            "! nvv4l2h264enc bitrate=%d control-rate=1 iframeinterval=%d "
            "! video/x-h264,profile=high "
            "! h264parse config-interval=-1 "
            "! mpegtsmux alignment=7 "
            "! udpsink host=%s port=%d sync=false async=false"
            % (self.width, self.height, self.fps,
               self.bitrate, self.fps, self.host, self.port)
        )

        self.pipeline = Gst.parse_launch(launch)
        self.appsrc = self.pipeline.get_by_name("src")

        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self.on_bus_message)

        self.latest = None
        self.lock = threading.Lock()
        rospy.Subscriber(self.topic, Image, self.on_image, queue_size=1)

        # 切 PLAYING
        ret = self.pipeline.set_state(Gst.State.PLAYING)
        rospy.loginfo("[stream] 推送 %s -> udp://%s:%d (MPEG-TS/H264) [%s]",
                      self.topic, self.host, self.port, ret.value_nick)

        rospy.loginfo("[stream] VLC 播放: udp://@:%d", self.port)

        # GLib 主循环线程(处理 bus 消息)
        self.loop = GLib.MainLoop()
        threading.Thread(target=self.loop.run, daemon=True).start()

        # 主动推送线程: 按 fps 节奏 push, 与客户端无关
        self.running = True
        threading.Thread(target=self.push_loop, daemon=True).start()

        rospy.loginfo("[stream] 就绪, 持续推送中")
        rospy.spin()

    def on_image(self, msg):
        if msg.encoding != "bgr8":
            rospy.logwarn_throttle(5.0, "[stream] 非 bgr8 编码: %s", msg.encoding)
            return
        with self.lock:
            self.latest = msg.data

    def push_loop(self):
        rate = rospy.Rate(self.fps)
        while not rospy.is_shutdown() and self.running:
            with self.lock:
                data = self.latest
            if data is not None:
                buf = Gst.Buffer.new_allocate(None, len(data), None)
                buf.fill(0, data)
                buf.duration = Gst.util_uint64_scale_int(1, Gst.SECOND, self.fps)
                buf.pts = Gst.CLOCK_TIME_NONE
                ret = self.appsrc.emit("push-buffer", buf)
                if ret != Gst.FlowReturn.OK:
                    rospy.logwarn_throttle(5.0, "[stream] push 返回 %s", ret.value_nick)
            rate.sleep()

    def on_bus_message(self, bus, message):
        t = message.type
        if t == Gst.MessageType.ERROR:
            err, dbg = message.parse_error()
            rospy.logerr("[stream] GStreamer 错误: %s (%s)", err, dbg)
        elif t == Gst.MessageType.EOS:
            rospy.logwarn("[stream] 管道 EOS")


if __name__ == "__main__":
    try:
        StreamNode()
    except rospy.ROSInterruptException:
        pass
