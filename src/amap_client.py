"""
高德地图 API 封装
换电站检索 + 驾车路线规划 + 逆地理编码
"""

import os
from typing import Any

import httpx


async def get_amap_key() -> str:
    """获取高德 Web 服务 API Key"""
    key = os.getenv("AMAP_API_KEY", "")
    if not key:
        raise RuntimeError("请设置环境变量 AMAP_API_KEY")
    return key


# ============ 换电站搜索 ============


async def search_swap_stations(
    city: str, keyword: str = "蔚来换电站"
) -> list[dict[str, Any]]:
    """
    在高德地图搜索指定城市的换电站点
    Returns: [{name, lng, lat, address, cityname, adname}, ...]
    """
    key = await get_amap_key()
    url = "https://restapi.amap.com/v5/place/text"

    all_results: list[dict[str, Any]] = []
    keywords = [keyword, "换电站", "NIO换电站"]

    for kw in keywords:
        params = {"keywords": kw, "city": city, "offset": 25, "page": 1, "key": key}
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
            all_results.append({
                "name": poi.get("name", ""),
                "lng": float(lng),
                "lat": float(lat),
                "address": poi.get("address", ""),
                "cityname": poi.get("cityname", ""),
                "adname": poi.get("adname", ""),
            })

    # 去重
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for s in all_results:
        if s["name"] not in seen:
            seen.add(s["name"])
            unique.append(s)
    return unique


# ============ 驾车路线规划 ============


async def get_driving_route(
    origin: tuple[float, float], destination: tuple[float, float]
) -> dict[str, Any]:
    """
    获取两点间驾车路线规划
    Returns: {distance(米), duration(秒), polyline, steps}
    """
    key = await get_amap_key()
    url = "https://restapi.amap.com/v5/direction/driving"
    params = {
        "origin": f"{origin[0]},{origin[1]}",
        "destination": f"{destination[0]},{destination[1]}",
        "key": key,
        "strategy": "0",  # 速度优先
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, params=params)
        data = resp.json()

    if data.get("status") != "1":
        return {"distance": 0, "duration": 0, "polyline": "", "steps": []}

    route = data.get("route", {})
    paths = route.get("paths", [])
    if not paths:
        return {"distance": 0, "duration": 0, "polyline": "", "steps": []}

    path = paths[0]
    return {
        "distance": int(path.get("distance", 0)),
        "duration": int(path.get("duration", 0)),
        "polyline": path.get("polyline", ""),
        "steps": path.get("steps", []),
    }


async def get_full_route_with_waypoints(
    origin: tuple[float, float],
    destination: tuple[float, float],
    waypoints: list[tuple[float, float]],
) -> dict[str, Any]:
    """
    获取含途经点的完整驾车路线

    将多个换电站作为途经点，返回一条完整路线折线
    Returns: {distance(米), duration(秒), polyline}
    """
    if not waypoints:
        return await get_driving_route(origin, destination)

    key = await get_amap_key()
    url = "https://restapi.amap.com/v5/direction/driving"

    # 途经点格式：lng1,lat1;lng2,lat2
    wp_str = ";".join(f"{lng},{lat}" for lng, lat in waypoints)

    params = {
        "origin": f"{origin[0]},{origin[1]}",
        "destination": f"{destination[0]},{destination[1]}",
        "waypoints": wp_str,
        "key": key,
        "strategy": "0",
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, params=params)
            data = resp.json()
    except Exception:
        return {"distance": 0, "duration": 0, "polyline": ""}

    if data.get("status") != "1":
        return {"distance": 0, "duration": 0, "polyline": ""}

    route = data.get("route", {})
    paths = route.get("paths", [])
    if not paths:
        return {"distance": 0, "duration": 0, "polyline": ""}

    path = paths[0]
    return {
        "distance": int(path.get("distance", 0)),
        "duration": int(path.get("duration", 0)),
        "polyline": path.get("polyline", ""),
    }


# ============ 逆地理编码（坐标→城市） ============


async def reverse_geocode(lng: float, lat: float) -> dict[str, str]:
    """
    逆地理编码：坐标 → 城市信息
    Returns: {city, province, district}
    """
    key = await get_amap_key()
    url = "https://restapi.amap.com/v3/geocode/regeo"
    params = {"location": f"{lng},{lat}", "key": key, "radius": 1000}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, params=params)
            data = resp.json()
    except Exception:
        return {"city": "", "province": "", "district": ""}

    if data.get("status") != "1":
        return {"city": "", "province": "", "district": ""}

    regeo = data.get("regeocode", {})
    comp = regeo.get("addressComponent", {})
    city = comp.get("city", "") or comp.get("province", "")
    return {
        "city": city.replace("市", ""),
        "province": comp.get("province", "").replace("省", "").replace("市", ""),
        "district": comp.get("district", ""),
    }


# ============ 沿途城市发现 ============


async def discover_cities_along_route(
    start: tuple[float, float], end: tuple[float, float]
) -> list[str]:
    """
    发现起点到终点沿途经过的城市

    1. 获取驾车路线
    2. 从路线折线中采样点
    3. 逆地理编码获取城市名
    4. 去重返回
    """
    # 先获取路线
    route = await get_driving_route(start, end)
    polyline_str = route.get("polyline", "")
    steps = route.get("steps", [])

    # 收集采样点
    sample_points: list[tuple[float, float]] = [start]

    # 从 steps 中提取 polyline 点
    if steps:
        all_coords = []
        for step in steps:
            step_polyline = step.get("polyline", "")
            if step_polyline:
                for point in step_polyline.split(";"):
                    parts = point.split(",")
                    if len(parts) == 2:
                        try:
                            all_coords.append((float(parts[0]), float(parts[1])))
                        except ValueError:
                            pass
        # 等距采样（最多 10 个点）
        if len(all_coords) > 2:
            step_size = max(1, len(all_coords) // 10)
            for i in range(0, len(all_coords), step_size):
                sample_points.append(all_coords[i])
    elif polyline_str:
        coords = []
        for point in polyline_str.split(";"):
            parts = point.split(",")
            if len(parts) == 2:
                try:
                    coords.append((float(parts[0]), float(parts[1])))
                except ValueError:
                    pass
        if len(coords) > 2:
            step_size = max(1, len(coords) // 10)
            for i in range(0, len(coords), step_size):
                sample_points.append(coords[i])

    sample_points.append(end)

    # 去重：只保留不同的点（> 5km）
    deduped = [sample_points[0]]
    for p in sample_points[1:]:
        d = _haversine(deduped[-1][0], deduped[-1][1], p[0], p[1])
        if d > 5:
            deduped.append(p)

    # 逆地理编码获取城市
    cities: list[str] = []
    for lng, lat in deduped:
        try:
            info = await reverse_geocode(lng, lat)
            if info["city"] and info["city"] not in cities:
                cities.append(info["city"])
        except Exception:
            pass

    return cities


def _haversine(lng1: float, lat1: float, lng2: float, lat2: float) -> float:
    """Haversine 距离计算"""
    import math
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


# ============ 配置 ============


def get_js_api_key() -> str:
    return os.getenv("AMAP_JS_API_KEY", "")


def get_js_security_code() -> str:
    return os.getenv("AMAP_JS_SECURITY_CODE", "")