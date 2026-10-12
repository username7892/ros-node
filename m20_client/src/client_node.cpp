// m20_client/src/client_node.cpp
//
// 【client 节点 / C++】
//   向 server 的 Service /m20/set_targets 发送"想检测的目标"，
//   收到 server 返回的 request（success + message）后打印确认。
//
// 用法：
//   rosrun m20_client client_node _targets:="person,helmet"
//   或命令行传入： client_node person helmet

#include <ros/ros.h>
#include <string>
#include <vector>
#include <sstream>

#include "m20_msgs/SetTargets.h"

namespace {
std::vector<std::string> splitTokens(const std::string& s, char delim) {
    std::vector<std::string> out;
    std::stringstream ss(s);
    std::string item;
    while (std::getline(ss, item, delim)) {
        size_t b = item.find_first_not_of(" \t");
        size_t e = item.find_last_not_of(" \t");
        if (b == std::string::npos) continue;
        out.push_back(item.substr(b, e - b + 1));
    }
    return out;
}
}

int main(int argc, char** argv) {
    ros::init(argc, argv, "m20_client_node");
    ros::NodeHandle nh, pnh("~");

    std::vector<std::string> targets;

    // 优先用命令行参数
    if (argc > 1) {
        for (int i = 1; i < argc; ++i) targets.push_back(argv[i]);
    } else {
        std::string s;
        pnh.param<std::string>("targets", s, "person,helmet");
        targets = splitTokens(s, ',');
    }

    std::string srv_name;
    pnh.param<std::string>("service", srv_name, "/m20/set_targets");

    ROS_INFO("[client] 请求设置目标: %zu 个 | service: %s",
             targets.size(), srv_name.c_str());
    for (auto& t : targets) ROS_INFO("[client]   - %s", t.c_str());

    ros::ServiceClient client = nh.serviceClient<m20_msgs::SetTargets>(srv_name);
    if (!client.waitForExistence(ros::Duration(10.0))) {
        ROS_ERROR("[client] service %s 不存在", srv_name.c_str());
        return 1;
    }

    m20_msgs::SetTargets srv;
    srv.request.targets = targets;

    if (client.call(srv)) {
        if (srv.response.success) {
            ROS_INFO("[client] ★ 设置完成: %s", srv.response.message.c_str());
        } else {
            ROS_WARN("[client] 设置失败: %s", srv.response.message.c_str());
            return 2;
        }
    } else {
        ROS_ERROR("[client] service 调用失败");
        return 1;
    }
    return 0;
}
