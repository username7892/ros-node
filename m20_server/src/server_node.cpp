// m20_server/src/server_node.cpp
//
// 【server 节点 / C++】
//   职责：
//   1) 提供 Service /m20/set_targets，接收 client 发来的检测目标
//      → 校验后返回 request（success + message）告诉 client "设置完成"
//   2) 收到合法目标后，通过 reliable topic /m20/detect_targets 广播，
//      QoS = reliable（KEEP_LAST, depth=1, transient_local 便于晚加入者也能收到）
//
//   yolo 节点订阅 /m20/detect_targets 获取"需要检测的目标"。
//
// 通道关系：
//   client --Service(SetTargets)--> server --reliable Topic(TargetList)--> yolo

#include <ros/ros.h>
#include <string>
#include <vector>
#include <algorithm>
#include <mutex>

#include "m20_msgs/SetTargets.h"
#include "m20_msgs/TargetList.h"

class ServerNode {
public:
    ServerNode(ros::NodeHandle& nh, ros::NodeHandle& pnh)
        : nh_(nh) {
        pnh.param<std::string>("service", srv_name_, "/m20/set_targets");
        pnh.param<std::string>("targets_topic", topic_, "/m20/detect_targets");

        // reliable topic 广播。
        // 说明：ROS1 默认 publish 就是 reliable（TCP）。
        // 用 latch=true 让晚加入的 yolo 也能立即收到最近一次目标。
        // QoS: reliable + KEEP_LAST(1) + transient_local(由 latch 提供)
        pub_ = nh_.advertise<m20_msgs::TargetList>(topic_, 1, /*latch=*/true);

        srv_ = nh_.advertiseService(srv_name_, &ServerNode::onSetTargets, this);

        ROS_INFO("[server] service %s | reliable broadcast %s",
                 srv_name_.c_str(), topic_.c_str());
    }

private:
    bool onSetTargets(m20_msgs::SetTargets::Request& req,
                      m20_msgs::SetTargets::Response& res) {
        std::vector<std::string> cleaned;
        for (auto& t : req.targets) {
            std::string s = t;
            s.erase(std::remove_if(s.begin(), s.end(),
                    [](unsigned char c){ return std::isspace(c); }), s.end());
            if (!s.empty()) cleaned.push_back(s);
        }
        std::sort(cleaned.begin(), cleaned.end());
        cleaned.erase(std::unique(cleaned.begin(), cleaned.end()), cleaned.end());

        if (cleaned.empty()) {
            res.success = false;
            res.message = "目标列表为空，设置失败";
            ROS_WARN("[server] 拒绝空目标列表");
            return true;
        }

        // 记录
        {
            std::lock_guard<std::mutex> lk(mtx_);
            targets_ = cleaned;
        }

        // 返回 request 给 client：设置完成
        res.success = true;
        res.message = "检测目标设置完成，共 " + std::to_string(cleaned.size()) + " 个";

        // reliable 广播给 yolo
        m20_msgs::TargetList msg;
        msg.targets = cleaned;
        pub_.publish(msg);

        std::string joined;
        for (auto& s : cleaned) joined += s + " ";
        ROS_INFO("[server] 设置完成并广播: [%s]", joined.c_str());
        return true;
    }

    ros::NodeHandle nh_;
    ros::Publisher pub_;
    ros::ServiceServer srv_;
    std::string srv_name_, topic_;
    std::vector<std::string> targets_;
    std::mutex mtx_;
};

int main(int argc, char** argv) {
    ros::init(argc, argv, "m20_server_node");
    ros::NodeHandle nh, pnh("~");
    ServerNode node(nh, pnh);
    ros::spin();
    return 0;
}
