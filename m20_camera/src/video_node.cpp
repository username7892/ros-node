// m20_camera/src/video_node.cpp
//
// 【视频节点 / C++】
//   用 GStreamer 从 RTSP 拉流，经 NVDEC(硬件解码) 得到 BGR 图像，
//   以 sensor_msgs/Image 发布到 /m20/image_raw。
//
// 解码链路（关键，回答"解码编码"问题）：
//   RTSP(网络) → rtspsrc 拆 RTP 包 → rtph264depay 去 RTP 封装
//   → h264parse 解析 H.264 码流 → nvv4l2decoder(NVDEC 硬件解码)
//   → nvvidconv 转 BGRx → videoconvert 转 BGR → appsink 取帧
//
//   即：RTSP/RTP 拆包由 GStreamer 完成，H.264 解码由 Jetson NVDEC 硬件完成。
//   本节点不重新编码，直接发原始 BGR 图（sensor_msgs/Image）。

#include <ros/ros.h>
#include <sensor_msgs/Image.h>
#include <opencv2/opencv.hpp>

#include <gst/gst.h>
#include <gst/app/gstappsink.h>

#include <string>
#include <atomic>
#include <thread>
#include <chrono>

#include "image_utils.h"

class VideoNode {
public:
    VideoNode(ros::NodeHandle& nh, ros::NodeHandle& pnh)
        : nh_(nh), pnh_(pnh) {
        pnh_.param<std::string>("rtsp_url", rtsp_url_,
            "rtsp://192.168.1.65:554/0-1");
        pnh_.param<std::string>("topic", topic_, "/m20/image_raw");
        pnh_.param<std::string>("frame_id", frame_id_, "m20_camera");
        pnh_.param<int>("width", width_, 800);
        pnh_.param<int>("height", height_, 600);

        pub_ = nh_.advertise<sensor_msgs::Image>(topic_, 1);

        // 组装 GStreamer 管道（与验证通过的 gst-launch 一致）
        std::string pipeline =
            "rtspsrc location=\"" + rtsp_url_ + "\" protocols=tcp latency=0 ! "
            "rtph264depay ! h264parse ! "
            "nvv4l2decoder ! "
            "nvvidconv ! video/x-raw,format=BGRx ! "
            "videoconvert ! video/x-raw,format=BGR ! "
            "appsink name=sink max-buffers=1 drop=true sync=false";

        ROS_INFO("[video] 管道: %s", pipeline.c_str());

        GError* err = nullptr;
        pipeline_ = gst_parse_launch(pipeline.c_str(), &err);
        if (!pipeline_ || err) {
            ROS_FATAL("[video] 管道创建失败: %s", err ? err->message : "unknown");
            if (err) g_error_free(err);
            ros::shutdown();
            return;
        }
        sink_ = gst_bin_get_by_name(GST_BIN(pipeline_), "sink");
        if (!sink_) {
            ROS_FATAL("[video] 未找到 appsink");
            ros::shutdown();
            return;
        }
        gst_element_set_state(pipeline_, GST_STATE_PLAYING);
        ROS_INFO("[video] 已启动，发布 %s", topic_.c_str());
    }

    ~VideoNode() {
        if (pipeline_) {
            gst_element_set_state(pipeline_, GST_STATE_NULL);
            gst_object_unref(pipeline_);
        }
        if (sink_) gst_object_unref(sink_);
    }

    // 从 appsink 拉一帧（阻塞，带超时）
    bool pullFrame(cv::Mat& out) {
        GstSample* sample = gst_app_sink_try_pull_sample(GST_APP_SINK(sink_),
                                                         500 * GST_MSECOND);
        if (!sample) return false;
        GstBuffer* buf = gst_sample_get_buffer(sample);
        GstCaps* caps = gst_sample_get_caps(sample);
        GstStructure* s = gst_caps_get_structure(caps, 0);
        int w = 0, h = 0;
        gst_structure_get_int(s, "width", &w);
        gst_structure_get_int(s, "height", &h);
        if (!caps_logged_) {
            gchar* cs = gst_caps_to_string(caps);
            ROS_INFO("[video] 收到 caps: %s", cs);
            g_free(cs);
            caps_logged_ = true;
        }

        GstMapInfo map;
        if (gst_buffer_map(buf, &map, GST_MAP_READ)) {
            cv::Mat img(h, w, CV_8UC3, (void*)map.data, map.size / h);
            out = img.clone();
            gst_buffer_unmap(buf, &map);
        }
        gst_sample_unref(sample);
        return !out.empty();
    }

    void publish(const cv::Mat& frame) {
        std_msgs::Header header;
        header.stamp = ros::Time::now();
        header.frame_id = frame_id_;
        pub_.publish(m20::toImageMsg(frame, header));
    }

private:
    ros::NodeHandle nh_, pnh_;
    ros::Publisher pub_;
    GstElement* pipeline_ = nullptr;
    GstElement* sink_ = nullptr;
    std::string rtsp_url_, topic_, frame_id_;
    int width_ = 800, height_ = 600;
    bool caps_logged_ = false;
};

int main(int argc, char** argv) {
    ros::init(argc, argv, "m20_video_node");
    gst_init(&argc, &argv);
    ros::NodeHandle nh, pnh("~");

    VideoNode node(nh, pnh);
    ros::Rate rate(1000);  // 不限速：拉一帧发一帧，跑满解码能力

    while (ros::ok()) {
        cv::Mat frame;
        if (node.pullFrame(frame)) {
            node.publish(frame);
        } else {
            ROS_WARN_THROTTLE(2.0, "[video] 拉帧超时");
        }
        ros::spinOnce();
    }
    return 0;
}
