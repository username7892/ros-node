#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
m20_yolo/scripts/stream_node.py

【检测视频流推送节点 / H264】
  订阅 /m20/image_detected (sensor_msgs/Image, bgr8)
  经 GStreamer: appsrc -> videoconvert -> nvvidconv -> nvv4l2h264enc
              -> h264parse -> (rtspclientsink | mpegtsmux+udpsink)

  两种输出模式 (params.yaml: stream_node/mode):
    rtsp : 推 RTSP 给 MediaMTX, 再由 MediaMTX 转 WebRTC(WHEP)
           rtsp_url: rtsp://127.0.0.1:8554/camera-front
           浏览器: http://<host>:8889/camera-front/whep
           VLC   : rtsp://<host>:8554/camera-front
    udp  : 老模式, MPEG-TS over UDP, VLC udp://@:5000

  实现要点:
    - 用独立定时线程按 fps 主动 push-buffer, 不依赖 appsrc 的 need-data
      (无 backpressure 的 sink 上 need-data 不会稳定触发, 会导致不出包)
    - 无客户端时也持续推送, 随时连接都能看到画面
    - h264parse config-interval=-1 让每个关键帧带 SPS/PPS,
      新接入的 WebRTC 客户端才能立刻解码

  依赖: GStreamer 插件 appsrc, videoconvert, nvvidconv,
        nvv4l2h264enc, h264parse, mpegtsmux, udpsink,
        rtspclientsink (gstreamer1.0-rtsp-server)

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
        self.mode = str(rospy.get_param("~mode", "srt")).strip().lower()
        self.srt_url = rospy.get_param("~srt_url",
                                       "srt://127.0.0.1:8890?streamid=publish:m20-front")
        self.srt_latency_ms = int(rospy.get_param("~srt_latency_ms", 200))
        self.rtsp_url = rospy.get_param("~rtsp_url",
                                        "rtsp://127.0.0.1:8554/camera-front")
        self.rtsp_transport = rospy.get_param("~rtsp_transport", "tcp")
        self.host = rospy.get_param("~host", "127.0.0.1")
        self.port = int(rospy.get_param("~port", 5000))
        self.bitrate = int(rospy.get_param("~bitrate", 4000000))
        self.fps = int(rospy.get_param("~fps", 10))
        self.width = int(rospy.get_param("~width", 1280))
        self.height = int(rospy.get_param("~height", 720))

        if self.mode not in ("srt", "rtsp", "udp"):
            rospy.logerr("[stream] mode 只能是 srt / rtsp / udp, 当前: %s", self.mode)
            raise SystemExit(1)

        Gst.init(None)

        # 公共的"原始 BGR -> H264"编码段
        encode = (
            "appsrc name=src is-live=true format=time do-timestamp=true "
            "max-bytes=0 "
            "! video/x-raw,format=BGR,width=%d,height=%d,framerate=%d/1 "
            "! videoconvert "
            "! nvvidconv "
            "! video/x-raw(memory:NVMM),format=I420 "
            "! nvv4l2h264enc bitrate=%d control-rate=1 iframeinterval=%d "
            "! video/x-h264,profile=baseline "
            "! h264parse config-interval=-1"
            % (self.width, self.height, self.fps, self.bitrate, self.fps)
        )

        if self.mode == "srt":
            # MPEG-TS over SRT 推给 MediaMTX, 由它转 WebRTC(WHEP)
            # latency 是收发缓冲(ms), 弱网下抗丢包的关键参数
            launch = (encode +
                      " ! mpegtsmux alignment=7 "
                      "! srtsink uri=\"%s\" mode=caller "
                      "latency=%d sync=false async=false"
                      % (self.srt_url, self.srt_latency_ms))
        elif self.mode == "rtsp":
            # 推给 MediaMTX, 由它转 WebRTC(WHEP)
            launch = (encode +
                      " ! rtspclientsink location=%s protocols=%s latency=0"
                      % (self.rtsp_url, self.rtsp_transport))
        else:
            # MPEG-TS over UDP: VLC 可直接 udp://@:port 播放
            launch = (encode +
                      " ! mpegtsmux alignment=7 "
                      "! udpsink host=%s port=%d sync=false async=false"
                      % (self.host, self.port))

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
        if self.mode == "srt":
            rospy.loginfo("[stream] 推送 %s -> %s (H264/MPEG-TS/SRT) [%s]",
                          self.topic, self.srt_url, ret.value_nick)
            rospy.loginfo("[stream] 浏览器播放: http://<mediamtx-host>:8889/<path>/whep")
        elif self.mode == "rtsp":
            rospy.loginfo("[stream] 推送 %s -> %s (H264/RTSP) [%s]",
                          self.topic, self.rtsp_url, ret.value_nick)
            rospy.loginfo("[stream] 浏览器播放: http://<mediamtx-host>:8889/<path>/whep")
        else:
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
        elif t == Gst.MessageType.STATE_CHANGED:
            # rtspclientsink 与 MediaMTX 断开/重连时会经历 PAUSED
            if message.src and message.src.get_name() == "rtspclientsink":
                old, new, _ = message.parse_state_changed()
                if new == Gst.State.PAUSED and old == Gst.State.PLAYING:
                    rospy.logwarn("[stream] 推流断开(MediaMTX 未就绪?), 自动重连中")


if __name__ == "__main__":
    try:
        StreamNode()
    except rospy.ROSInterruptException:
        pass