"""
路线规划核心算法
方向感知贪心 + 2-opt 局部优化 + SOC 约束
"""

import math
from typing import Any

from src.car_db import CAR_CONSUMPTION_DB, DEFAULT_BATTERY, DEFAULT_CONSUMPTION

# ============ 车型参数查询 ============


def get_car_params(car_model: str) -> tuple[float, float]:
    """根据车型获取 (consumption kWh/100km, battery kWh)"""
    if car_model in CAR_CONSUMPTION_DB:
        info = CAR_CONSUMPTION_DB[car_model]
        return info["consumption"], info["battery"]
    for key, val in CAR_CONSUMPTION_DB.items():
        if car_model in key:
            return val["consumption"], val["battery"]
    return DEFAULT_CONSUMPTION, DEFAULT_BATTERY


# ============ SOC 计算 ============


def calc_arrival_soc(
    distance_km: float, battery_kwh: float, consumption: float, initial_soc: float
) -> float:
    """计算到达时的剩余 SOC (%)"""
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
    """换电后 SOC（保守估计 80%）"""
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

        if arrival_soc < 10:
            low_soc_count += 1
            risks.append({
                "level": "danger",
                "segment": i,
                "message": f"第{i + 1}段到站 SOC 仅 {arrival_soc}%, 有趴窝风险，建议确认该站可用性",
            })
        elif arrival_soc < 20:
            low_soc_count += 1
            risks.append({
                "level": "warning",
                "segment": i,
                "message": f"第{i + 1}段到站 SOC 为 {arrival_soc}%, 建议预留备选换电站",
            })

        if seg["distance"] > 200:
            risks.append({
                "level": "warning",
                "segment": i,
                "message": f"第{i + 1}段距离 {seg['distance']:.0f}km, 超过 200km 建议提前确认换电站可用性",
            })

        if total_distance > 500 and i == len(segments) - 1:
            risks.append({
                "level": "info",
                "segment": i,
                "message": f"全程累计里程 {total_distance:.0f}km, 请注意驾驶疲劳，建议每 200km 休息",
            })

    if low_soc_count >= 2:
        risks.append({
            "level": "info",
            "segment": 0,
            "message": f"全程有 {low_soc_count} 段到站 SOC 低于 20%, 建议适当增加换电次数或降低能耗",
        })

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
    """2-opt 局部搜索优化站点顺序"""
    if len(stations) < 3:
        return stations
    improved = True
    while improved:
        improved = False
        best_distance = _total_route_distance(stations, start, end)
        for i in range(len(stations) - 1):
            for j in range(i + 1, len(stations)):
                new_stations = stations[:i] + stations[i: j + 1][::-1] + stations[j + 1:]
                new_distance = _total_route_distance(new_stations, start, end)
                if new_distance < best_distance * 0.999:
                    stations = new_stations
                    best_distance = new_distance
                    improved = True
                    break
            if improved:
                break
    return stations


# ============ 主规划器 ============


def _deduplicate_stations(stations: list[dict]) -> list[dict]:
    """去重：合并距离 < 1km 的站点，保留名称较长的那个"""
    if not stations:
        return []
    deduped = []
    for st in stations:
        is_dup = False
        for existing in deduped:
            d = _haversine(existing["lng"], existing["lat"], st["lng"], st["lat"])
            if d < 1.0:
                is_dup = True
                # 保留名称较长的（通常更详细）
                if len(st.get("name", "")) > len(existing.get("name", "")):
                    existing["name"] = st["name"]
                    existing["address"] = st.get("address", existing.get("address", ""))
                break
        if not is_dup:
            deduped.append(dict(st))
    return deduped


def optimize_route(params: dict) -> dict[str, Any]:
    """
    主路线规划入口
    方向感知贪心算法：优先选择往目的地方向的站点，避免绕路
    """
    start = params["start_location"]
    end = params["end_location"]
    max_swaps = params.get("max_swaps", 3)
    initial_soc = params.get("initial_soc", 100.0)

    car_model = params.get("car_model", "")
    if car_model:
        consumption, battery = get_car_params(car_model)
    else:
        consumption = params.get("consumption", DEFAULT_CONSUMPTION)
        battery = params.get("battery_kwh", DEFAULT_BATTERY)

    stations = _deduplicate_stations(params.get("swap_stations", []))

    if not stations:
        return {
            "route": [], "total_distance": 0, "total_duration": 0,
            "unique_station_count": 0, "final_arrival_soc": initial_soc,
            "car_model": car_model, "consumption": consumption, "battery": battery,
            "segments": [],
            "risks": [{"level": "info", "segment": 0, "message": "未找到换电站，请检查城市名称或 API Key 配置"}],
        }

    # 总方向向量
    total_dx = end[0] - start[0]
    total_dy = end[1] - start[1]
    total_dist = _haversine(start[0], start[1], end[0], end[1])

    def _progress_score(st_lng, st_lat, cur_lng, cur_lat):
        """计算站点在前往目的地方向上的进度得分 (0~1)"""
        if total_dist < 1:
            return 0.5
        cur_to_end = _haversine(cur_lng, cur_lat, end[0], end[1])
        st_to_end = _haversine(st_lng, st_lat, end[0], end[1])
        saved = cur_to_end - st_to_end
        return max(0, min(1, saved / total_dist))

    # ==== 阶段一：方向感知贪心选择 ====
    current_pos = start
    current_soc = initial_soc
    remaining = list(range(len(stations)))
    route_indices: list[int] = []
    swap_count = 0

    while swap_count < max_swaps and remaining:
        candidates = []
        for idx in remaining:
            st = stations[idx]
            dist_km = _haversine(current_pos[0], current_pos[1], st["lng"], st["lat"])
            if dist_km < 0.2:
                continue  # 跳过太近的站
            arrival = calc_arrival_soc(dist_km, battery, consumption, current_soc)
            progress = _progress_score(st["lng"], st["lat"], current_pos[0], current_pos[1])

            # 安全兜底：如果SOC不够，依然可以选但要低分
            max_d = max(1, max(
                _haversine(current_pos[0], current_pos[1], stations[j]["lng"], stations[j]["lat"])
                for j in remaining
            ))
            dist_score = 1 - dist_km / max_d
            # 综合评分：进度优先
            score = progress * 0.5 + dist_score * 0.3 + (arrival / 100) * 0.2
            candidates.append((idx, score, dist_km, arrival, progress))

        if not candidates:
            break

        # 按综合评分降序
        candidates.sort(key=lambda x: -x[1])

        # 选第一个能安全到达的
        best_idx = best_dist = None
        for idx, score, dist_km, arrival, progress in candidates:
            if arrival >= 8:
                best_idx = idx
                best_dist = dist_km
                break

        # 都不安全但还有电 → 选最近的
        if best_idx is None:
            candidates.sort(key=lambda x: x[2])
            for idx, score, dist_km, arrival, progress in candidates:
                if arrival > 0:
                    best_idx = idx
                    best_dist = dist_km
                    break

        if best_idx is None:
            break

        remaining.remove(best_idx)
        route_indices.append(best_idx)

        station = stations[best_idx]
        arrival_soc = calc_arrival_soc(best_dist, battery, consumption, current_soc)
        swap_soc = calc_swap_result(arrival_soc)

        current_pos = (station["lng"], station["lat"])
        current_soc = swap_soc
        swap_count += 1

    # 去重索引
    seen: set[int] = set()
    unique_indices = []
    for idx in route_indices:
        if idx not in seen:
            seen.add(idx)
            unique_indices.append(idx)
    route_indices = unique_indices

    # ==== 阶段二：2-opt 优化 ====
    selected = [stations[i] for i in route_indices]
    optimized = _optimize_2opt(selected, start, end)

    # 恢复索引
    optimized_indices = []
    for st in optimized:
        for idx in route_indices:
            s = stations[idx]
            if abs(s["lng"] - st["lng"]) < 0.0001 and abs(s["lat"] - st["lat"]) < 0.0001:
                if idx not in optimized_indices:
                    optimized_indices.append(idx)
                break

    # ==== 阶段三：构建路线 ====
    current_pos = start
    current_soc = initial_soc
    route = []

    for seq_idx, st_idx in enumerate(optimized_indices):
        station = stations[st_idx]
        dist_km = _haversine(current_pos[0], current_pos[1], station["lng"], station["lat"])
        arrival_soc = calc_arrival_soc(dist_km, battery, consumption, current_soc)
        swap_soc = calc_swap_result(arrival_soc)
        duration_sec = int(dist_km / 60 * 3600) if dist_km > 0 else 0

        route.append({
            "index": seq_idx + 1,
            "station_name": station["name"],
            "station_address": station.get("address", ""),
            "lng": station["lng"],
            "lat": station["lat"],
            "distance": round(dist_km, 1),
            "duration": duration_sec,
            "arrival_soc": arrival_soc,
            "swap_soc": swap_soc,
        })

        current_pos = (station["lng"], station["lat"])
        current_soc = swap_soc

    # 最后一段
    last_pos = (route[-1]["lng"], route[-1]["lat"]) if route else start
    final_dist = _haversine(last_pos[0], last_pos[1], end[0], end[1])
    final_duration = int(final_dist / 60 * 3600) if final_dist > 0 else 0
    final_soc = calc_arrival_soc(final_dist, battery, consumption, current_soc)

    segments = []
    for r in route:
        segments.append({"distance": r["distance"], "arrival_soc": r["arrival_soc"]})
    segments.append({"distance": round(final_dist, 1), "arrival_soc": final_soc})

    risks = generate_risks(segments)

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