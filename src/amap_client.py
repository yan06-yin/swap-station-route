"""
高德地图 API 封装
换电站检索 + 驾车路线规划（真实道路距离）
"""

import os
from typing import Any

import httpx


async def get_amap_key() -> str:
    """获取高德 Web 服务 API Key，优先读环境变量"""
    key = os.getenv("AMAP_API_KEY", "")
    if not key:
        raise RuntimeError(
            "请设置环境变量 AMAP_API_KEY（高德地图 Web 服务 API Key）"
        )
    return key


async def search_swap_stations(
    city: str, keyword: str = "蔚来换电站"
) -> list[dict[str, Any]]:
    """
    在高德地图搜索指定城市的换电站点

    使用 v5 place/text API，自动多关键词搜索 + 去重

    Returns:
        [{name, lng, lat, address, cityname, adname}, ...]
    """
    key = await get_amap_key()
    url = "https://restapi.amap.com/v5/place/text"

    all_results: list[dict[str, Any]] = []
    # 多关键词搜索提高覆盖率
    keywords = [keyword, "换电站", "NIO换电站"]

    for kw in keywords:
        params = {
            "keywords": kw,
            "city": city,
            "offset": 25,  # 每页 25 条
            "page": 1,
            "key": key,
        }
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(url, params=params)
                data = resp.json()
        except Exception:
            continue

        if data.get("status") != "1" or "pois" not in data:
            continue

        for poi in data["pois"]:
            loc = poi.get("location", "")
            if not loc:
                continue
            lng, lat = loc.split(",")
            all_results.append(
                {
                    "name": poi.get("name", ""),
                    "lng": float(lng),
                    "lat": float(lat),
                    "address": poi.get("address", ""),
                    "cityname": poi.get("cityname", ""),
                    "adname": poi.get("adname", ""),
                }
            )

    # 按站点名称去重
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for s in all_results:
        if s["name"] not in seen:
            seen.add(s["name"])
            unique.append(s)

    return unique


async def get_driving_route(
    origin: tuple[float, float], destination: tuple[float, float]
) -> dict[str, Any]:
    """
    获取两点间驾车路线规划（真实道路数据）

    使用 v5 direction/driving API

    Returns:
        {distance(米), duration(秒), polyline, steps}
    """
    key = await get_amap_key()
    url = "https://restapi.amap.com/v5/direction/driving"
    params = {
        "origin": f"{origin[0]},{origin[1]}",
        "destination": f"{destination[0]},{destination[1]}",
        "key": key,
        "show_fields": "polyline,cost",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, params=params)
        data = resp.json()

    if data.get("status") != "1":
        return {"distance": 0, "duration": 0, "polyline": ""}

    route = data.get("route", {})
    paths = route.get("paths", [])
    if not paths:
        return {"distance": 0, "duration": 0, "polyline": ""}

    path = paths[0]
    return {
        "distance": int(path.get("distance", 0)),  # 米
        "duration": int(path.get("duration", 0)),  # 秒
        "polyline": path.get("polyline", ""),  # 高德坐标折点串
        "steps": path.get("steps", []),
    }


def get_js_api_key() -> str:
    """获取高德 JS API Key（用于前端地图展示）"""
    return os.getenv("AMAP_JS_API_KEY", "")


def get_js_security_code() -> str:
    """获取高德 JS API 安全密钥"""
    return os.getenv("AMAP_JS_SECURITY_CODE", "")