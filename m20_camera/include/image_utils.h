// m20 共用：sensor_msgs/Image <-> cv::Mat 手工转换
// 不依赖 cv_bridge，避免 ROS 自带 cv_bridge(OpenCV 4.2) 与系统 OpenCV 4.5 的 ABI 冲突。
#pragma once

#include <sensor_msgs/Image.h>
#include <opencv2/opencv.hpp>
#include <cstring>
#include <stdexcept>

namespace m20 {

// sensor_msgs::Image -> cv::Mat （仅支持 bgr8 / rgb8 / mono8 常见编码）
inline cv::Mat toCvMat(const sensor_msgs::Image& msg) {
    int type;
    if (msg.encoding == "bgr8")       type = CV_8UC3;
    else if (msg.encoding == "rgb8")  type = CV_8UC3;
    else if (msg.encoding == "mono8") type = CV_8UC1;
    else throw std::runtime_error("不支持的编码: " + msg.encoding);

    cv::Mat img(msg.height, msg.width, type,
                const_cast<uint8_t*>(msg.data.data()), msg.step);
    cv::Mat out = img.clone();               // 克隆，脱离 msg 生命周期
    if (msg.encoding == "rgb8") cv::cvtColor(out, out, cv::COLOR_RGB2BGR);
    return out;
}

// cv::Mat(bgr8) -> sensor_msgs::Image
inline sensor_msgs::Image toImageMsg(const cv::Mat& img,
                                     const std_msgs::Header& header,
                                     const std::string& encoding = "bgr8") {
    sensor_msgs::Image msg;
    msg.header = header;
    msg.height = img.rows;
    msg.width  = img.cols;
    msg.encoding = encoding;
    msg.is_bigendian = 0;
    msg.step = static_cast<uint32_t>(img.cols * img.elemSize());
    size_t sz = msg.step * img.rows;
    msg.data.resize(sz);
    if (img.isContinuous()) {
        std::memcpy(msg.data.data(), img.data, sz);
    } else {
        for (int r = 0; r < img.rows; ++r)
            std::memcpy(msg.data.data() + r * msg.step, img.ptr(r), msg.step);
    }
    return msg;
}

} // namespace m20
