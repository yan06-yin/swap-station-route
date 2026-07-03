"""
飞书机器人集成
发送换电站路线卡片到飞书群
"""

import json
import os
from typing import Any

import httpx

FEISHU_WEBHOOK_ENV = "FEISHU_WEBHOOK"


def get_webhook_url(custom_url: str | None = None) -> str | None:
    """获取飞书 webhook URL，优先使用传入的，其次读环境变量"""
    if custom_url:
        return custom_url
    return os.getenv(FEISHU_WEBHOOK_ENV, "").strip() or None


async def send_route_card(
    route_id: str,
    route_data: dict[str, Any],
    params: dict[str, Any],
    share_url: str,
    webhook_url: str | None = None,
) -> dict[str, Any]:
    """
    发送换电站路线卡片到飞书

    Args:
        route_id: 路线 ID
        route_data: 路线规划结果
        params: 创建参数
        share_url: 分享链接（完整 URL）
        webhook_url: 飞书机器人 webhook URL
            （不传则读环境变量 FEISHU_WEBHOOK）

    Returns:
        {ok: bool, message: str}
    """
    url = get_webhook_url(webhook_url)
    if not url:
        return {"ok": False, "message": "未配置飞书 Webhook URL"}

    # 构建路线摘要
    route = route_data.get("route", [])
    total_dist = route_data.get("total_distance", 0)
    total_duration = route_data.get("total_duration", 0)
    car_model = route_data.get("car_model", "自定义")
    city = params.get("city", "")
    swap_count = len(route)

    # 站点列表
    station_lines = "\n".join(
        [
            f"  {s['index']}. {s['station_name']} → SOC {s['arrival_soc']}% (换后 {s['swap_soc']}%)"
            for s in route[:8]  # 最多显示 8 个
        ]
    )

    duration_min = round(total_duration / 60)

    # 风险摘要
    risks = route_data.get("risks", [])
    risk_summary = ""
    if risks:
        danger_count = sum(1 for r in risks if r["level"] == "danger")
        warning_count = sum(1 for r in risks if r["level"] == "warning")
        if danger_count:
            risk_summary = f"\n⚠️ {danger_count} 项危险 + {warning_count} 项警告"
        elif warning_count:
            risk_summary = f"\n⚠️ {warning_count} 项警告"
        else:
            risk_summary = "\n✅ 无风险"

    # 构建 Feishu 消息卡片
    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": f"⚡ 换电站打卡路线 - {city}"},
            "template": "blue",
        },
        "elements": [
            {
                "tag": "markdown",
                "content": (
                    f"**车型**: {car_model}\n"
                    f"**总里程**: {total_dist} km\n"
                    f"**预计时长**: {duration_min} 分钟\n"
                    f"**换电次数**: {swap_count} 次"
                    f"{risk_summary}"
                ),
            },
            {"tag": "hr"},
            {"tag": "markdown", "content": f"**打卡顺序**:\n{station_lines}"},
            {"tag": "hr"},
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "🗺️ 查看完整路线"},
                        "type": "primary",
                        "url": share_url,
                    }
                ],
            },
            {
                "tag": "note",
                "elements": [
                    {
                        "tag": "plain_text",
                        "content": f"路线 ID: {route_id} | 由换电站打卡路线规划工具生成",
                    }
                ],
            },
        ],
    }

    payload = {"msg_type": "interactive", "card": card}

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, json=payload)
            result = resp.json()

        if result.get("code") == 0:
            return {"ok": True, "message": "飞书消息发送成功"}
        else:
            return {"ok": False, "message": f"飞书 API 错误: {result.get('msg', 'unknown')}"}

    except Exception as e:
        return {"ok": False, "message": f"飞书发送失败: {str(e)}"}