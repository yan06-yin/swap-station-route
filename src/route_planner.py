"""
路线规划核心算法
贪心最近邻 + 2-opt 局部优化 + SOC 约束
"""

import math
from typing import Any

from src.car_db import CAR_CONSUMPTION_DB, DEFAULT_BATTERY, DEFAULT_CONSUMPTION

# ============ 车型参数查询 ============


def get_car_params(car_model: str) -> tuple[float, float]:
    """根据车型获取 (consumption kWh/100km, battery kWh)"""
    # 精确匹配
    if car_model in CAR_CONSUMPTION_DB:
        info = CAR_CONSUMPTION_DB[car_model]
        return info["consumption"], info["battery"]

    # 模糊匹配
    for key, val in CAR_CONSUMPTION_DB.items():
        if car_model in key:
            return val["consumption"], val["battery"]

    return DEFAULT_CONSUMPTION, DEFAULT_BATTERY


# ============ SOC 计算 ============


def calc_arrival_soc(
    distance_km: float, battery_kwh: float, consumption: float, initial_soc: float
) -> float:
    """
    计算到达时的剩余 SOC (%)

    Args:
        distance_km: 行驶距离 (km)
        battery_kwh: 电池容量 (kWh)
        consumption: 百公里能耗 (kWh/100km)
        initial_soc: 出发时 SOC (%)
    """
    if distance_km <= 0:
        return round(initial_soc, 1)

    energy_used = (distance_km / 100) * consumption
    energy_capacity = battery_kwh * (initial_soc / 100)

    if energy_capacity <= 0:
        return 0.0

    remaining_energy = energy_capacity - energy_used
    remaining_soc = (remaining_energy / battery_kwh) * 100
    return round(max(0.0, min(100.0, remaining_soc)), 1)


def calc_swap_result(arrival_soc: float, target_soc: float = 80.0) -> float:
    """
    换电后 SOC

    蔚来换电站默认换到 80-93%（取决于站内电池状态）
    取保守值 80%
    """
    return round(max(target_soc, min(arrival_soc + 20, 95)), 1)


# ============ 风险提示 ============


def generate_risks(segments: list[dict]) -> list[dict]:
    """
    生成风险提示

    规则:
    - 到站 SOC < 10%: 危险
    - 到站 SOC < 20%: 警告
    - 单段 > 200km: 续航警告
    - 累计 > 500km: 疲劳提醒
    - 连续低 SOC: 建议增加换电次数
    """
    risks = []
    total_distance = 0.0
    low_soc_count = 0

    for i, seg in enumerate(segments):
        total_distance += seg["distance"]
        arrival_soc = seg["arrival_soc"]

        # SOC 过低 — 危险
        if arrival_soc < 10:
            low_soc_count += 1
            risks.append(
                {
                    "level": "danger",
                    "segment": i,
                    "message": (
                        f"第{i + 1}段到站 SOC 仅 {arrival_soc}%, "
                        "有趴窝风险，建议确认该站可用性"
                    ),
                }
            )
        elif arrival_soc < 20:
            low_soc_count += 1
            risks.append(
                {
                    "level": "warning",
                    "segment": i,
                    "message": (
                        f"第{i + 1}段到站 SOC 为 {arrival_soc}%, "
                        "建议预留备选换电站"
                    ),
                }
            )

        # 单段过长
        if seg["distance"] > 200:
            risks.append(
                {
                    "level": "warning",
                    "segment": i,
                    "message": (
                        f"第{i + 1}段距离 {seg['distance']:.0f}km, "
                        "超过 200km 建议提前确认换电站可用性"
                    ),
                }
            )

        # 累计超长
        if total_distance > 500 and i == len(segments) - 1:
            risks.append(
                {
                    "level": "info",
                    "segment": i,
                    "message": (
                        f"全程累计里程 {total_distance:.0f}km, "
                        "请注意驾驶疲劳，建议每 200km 休息"
                    ),
                }
            )

    # 连续低 SOC 提醒
    if low_soc_count >= 2:
        risks.append(
            {
                "level": "info",
                "segment": 0,
                "message": (
                    f"全程有 {low_soc_count} 段到站 SOC 低于 20%, "
                    "建议适当增加换电次数或降低能耗"
                ),
            }
        )

    return risks


# ============ 地理计算 ============


def _haversine(lng1: float, lat1: float, lng2: float, lat2: float) -> float:
    """Haversine 公式计算两点间球面距离 (km)"""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


# ============ 2-opt 局部优化 ============


def _total_route_distance(
    stations: list[dict], start: tuple[float, float], end: tuple[float, float]
) -> float:
    """计算完整路线的总距离 (Haversine, km)"""
    if not stations:
        return _haversine(start[0], start[1], end[0], end[1])

    total = _haversine(start[0], start[1], stations[0]["lng"], stations[0]["lat"])
    for i in range(len(stations) - 1):
        total += _haversine(
            stations[i]["lng"], stations[i]["lat"],
            stations[i + 1]["lng"], stations[i + 1]["lat"],
        )
    total += _haversine(
        stations[-1]["lng"], stations[-1]["lat"],
        end[0], end[1],
    )
    return total


def _optimize_2opt(
    stations: list[dict], start: tuple[float, float], end: tuple[float, float]
) -> list[dict]:
    """
    2-opt 局部搜索优化站点顺序

    通过反转子路径来减少总距离，迭代直到无改进
    """
    if len(stations) < 3:
        return stations

    improved = True
    while improved:
        improved = False
        best_distance = _total_route_distance(stations, start, end)

        for i in range(len(stations) - 1):
            for j in range(i + 1, len(stations)):
                # 反转 [i, j] 段
                new_stations = stations[:i] + stations[i : j + 1][::-1] + stations[j + 1 :]
                new_distance = _total_route_distance(new_stations, start, end)

                if new_distance < best_distance * 0.999:  # 0.1% 改善阈值
                    stations = new_stations
                    best_distance = new_distance
                    improved = True
                    break  # 重新开始
            if improved:
                break

    return stations


# ============ 主规划器 ============


def optimize_route(params: dict) -> dict[str, Any]:
    """
    主路线规划入口

    params:
        city: 城市
        start_location: (lng, lat)
        end_location: (lng, lat)
        car_model: 车型名称
        battery_kwh: 电池容量（可选，不传则按车型查表）
        consumption: 百公里能耗（可选）
        initial_soc: 出发 SOC (%)
        max_swaps: 最多换电次数
        swap_stations: 换电站列表 [{name, lng, lat, ...}]

    Returns:
        {
            route: [{index, station_name, lng, lat, distance, duration,
                     arrival_soc, swap_soc, address}],
            total_distance, total_duration,
            unique_station_count,
            final_arrival_soc,
            car_model, consumption, battery,
            segments: [{distance, arrival_soc}],
            risks: [{level, segment, message}]
        }
    """
    start = params["start_location"]
    end = params["end_location"]
    max_swaps = params.get("max_swaps", 3)
    initial_soc = params.get("initial_soc", 100.0)

    # 获取车型参数
    car_model = params.get("car_model", "")
    if car_model:
        consumption, battery = get_car_params(car_model)
    else:
        consumption = params.get("consumption", DEFAULT_CONSUMPTION)
        battery = params.get("battery_kwh", DEFAULT_BATTERY)

    stations = params.get("swap_stations", [])

    # 空站点检查
    if not stations:
        return {
            "route": [],
            "total_distance": 0,
            "total_duration": 0,
            "unique_station_count": 0,
            "final_arrival_soc": initial_soc,
            "car_model": car_model,
            "consumption": consumption,
            "battery": battery,
            "segments": [],
            "risks": [
                {
                    "level": "info",
                    "segment": 0,
                    "message": "未找到换电站，请检查城市名称或 API Key 配置",
                }
            ],
        }

    # ==== 阶段一：贪心最近邻选择站点 ====
    current_pos = start
    current_soc = initial_soc
    remaining = list(range(len(stations)))
    route_indices: list[int] = []
    swap_count = 0

    while swap_count < max_swaps and remaining:
        # 计算当前位置到所有剩余站点的距离
        candidates = []
        for idx in remaining:
            st = stations[idx]
            d = _haversine(current_pos[0], current_pos[1], st["lng"], st["lat"])
            candidates.append((idx, d))

        candidates.sort(key=lambda x: x[1])

        # 找第一个 SOC 能安全到达的站点
        best_idx = None
        for idx, dist_km in candidates:
            arrival = calc_arrival_soc(dist_km, battery, consumption, current_soc)
            if arrival >= 8:  # 安全阈值 8%
                best_idx = idx
                break

        # 没有安全站点 → 选最近的（冒险模式）
        if best_idx is None and candidates:
            # 评估最近的站点是否真的完全不可达
            nearest_idx, nearest_dist = candidates[0]
            arrival = calc_arrival_soc(nearest_dist, battery, consumption, current_soc)
            if arrival > 0:
                best_idx = nearest_idx
            else:
                # 最近的站点都到不了 → 终止
                break

        if best_idx is None:
            break

        remaining.remove(best_idx)
        route_indices.append(best_idx)

        # 更新状态
        station = stations[best_idx]
        dist_km = _haversine(
            current_pos[0], current_pos[1], station["lng"], station["lat"]
        )
        arrival_soc = calc_arrival_soc(dist_km, battery, consumption, current_soc)
        swap_soc = calc_swap_result(arrival_soc)

        current_pos = (station["lng"], station["lat"])
        current_soc = swap_soc
        swap_count += 1

    # ==== 阶段二：2-opt 优化站点顺序 ====
    selected_stations = [stations[i] for i in route_indices]
    optimized_stations = _optimize_2opt(selected_stations, start, end)

    # 确保优化后的顺序与原始索引对应
    optimized_indices = []
    for st in optimized_stations:
        # 找到原索引
        orig_idx = next(
            (i for i in route_indices if stations[i]["lng"] == st["lng"] and stations[i]["lat"] == st["lat"]),
            None,
        )
        if orig_idx is not None:
            optimized_indices.append(orig_idx)

    # ==== 阶段三：构建路线 ====
    current_pos = start
    current_soc = initial_soc
    route = []

    for seq_idx, st_idx in enumerate(optimized_indices):
        station = stations[st_idx]
        dist_km = _haversine(
            current_pos[0], current_pos[1], station["lng"], station["lat"]
        )
        arrival_soc = calc_arrival_soc(dist_km, battery, consumption, current_soc)
        swap_soc = calc_swap_result(arrival_soc)

        # 估算时间（Haversine距离 / 平均时速60km/h，后续会被真实数据替换）
        duration_sec = int(dist_km / 60 * 3600) if dist_km > 0 else 0

        route.append(
            {
                "index": seq_idx + 1,
                "station_name": station["name"],
                "station_address": station.get("address", ""),
                "lng": station["lng"],
                "lat": station["lat"],
                "distance": round(dist_km, 1),
                "duration": duration_sec,
                "arrival_soc": arrival_soc,
                "swap_soc": swap_soc,
            }
        )

        current_pos = (station["lng"], station["lat"])
        current_soc = swap_soc

    # 最后一段：到终点
    last_pos = (route[-1]["lng"], route[-1]["lat"]) if route else start
    final_dist = _haversine(last_pos[0], last_pos[1], end[0], end[1])
    final_duration = int(final_dist / 60 * 3600) if final_dist > 0 else 0
    final_soc = calc_arrival_soc(final_dist, battery, consumption, current_soc)

    # 构建 segments（用于风险提示）
    segments = []
    for r in route:
        segments.append({"distance": r["distance"], "arrival_soc": r["arrival_soc"]})
    segments.append({"distance": round(final_dist, 1), "arrival_soc": final_soc})

    # 风险提示
    risks = generate_risks(segments)

    # 汇总
    total_distance = sum(r["distance"] for r in route) + round(final_dist, 1)
    total_duration = sum(r["duration"] for r in route) + final_duration

    return {
        "route": route,
        "total_distance": round(total_distance, 1),
        "total_duration": total_duration,
        "unique_station_count": len(route),
        "final_arrival_soc": final_soc,
        "car_model": car_model,
        "consumption": consumption,
        "battery": battery,
        "segments": segments,
        "risks": risks,
    }