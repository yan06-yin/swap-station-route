"""
路线规划核心算法
Beam Search（束搜索）+ SOC 约束 + 多策略支持
"""

import math
from typing import Any

from src.car_db import CAR_CONSUMPTION_DB, DEFAULT_BATTERY, DEFAULT_CONSUMPTION

# ============ 车型参数查询 ============


def get_car_params(car_model: str) -> tuple[float, float]:
    if car_model in CAR_CONSUMPTION_DB:
        info = CAR_CONSUMPTION_DB[car_model]
        return info["consumption"], info["battery"]
    for key, val in CAR_CONSUMPTION_DB.items():
        if car_model in key:
            return val["consumption"], val["battery"]
    return DEFAULT_CONSUMPTION, DEFAULT_BATTERY


# ============ SOC 计算 ============


def calc_arrival_soc(
    distance_km: float, battery_kwh: float, consumption: float, initial_soc: float,
    condition: str = "normal"
) -> float:
    factors = {"normal": 1.0, "winter": 1.20, "highway": 1.15, "winter_highway": 1.35}
    adj_consumption = consumption * factors.get(condition, 1.0)
    if distance_km <= 0:
        return round(initial_soc, 1)
    energy_used = (distance_km / 100) * adj_consumption
    energy_capacity = battery_kwh * (initial_soc / 100)
    if energy_capacity <= 0:
        return 0.0
    remaining_energy = energy_capacity - energy_used
    remaining_soc = (remaining_energy / battery_kwh) * 100
    return round(max(0.0, min(100.0, remaining_soc)), 1)


def calc_swap_result(arrival_soc: float, target_soc: float = 80.0) -> float:
    return round(max(target_soc, min(arrival_soc + 20, 95)), 1)


# ============ 风险提示 ============


def generate_risks(segments: list[dict], battery: float = 75, consumption: float = 14.5) -> list[dict]:
    risks, total_distance, low_soc_count = [], 0.0, 0
    for i, seg in enumerate(segments):
        total_distance += seg["distance"]
        arrival_soc = seg["arrival_soc"]
        if arrival_soc < 10:
            low_soc_count += 1
            risks.append({"level": "danger", "segment": i,
                          "message": f"第{i + 1}段到站 SOC 仅 {arrival_soc}%, 有趴窝风险"})
        elif arrival_soc < 20:
            low_soc_count += 1
            risks.append({"level": "warning", "segment": i,
                          "message": f"第{i + 1}段到站 SOC 为 {arrival_soc}%, 建议预留备选换电站"})
        if seg["distance"] > 200:
            risks.append({"level": "warning", "segment": i,
                          "message": f"第{i + 1}段 {seg['distance']:.0f}km, 超过 200km 建议提前确认"})
        if total_distance > 500 and i == len(segments) - 1:
            risks.append({"level": "info", "segment": i,
                          "message": f"全程 {total_distance:.0f}km, 请注意驾驶疲劳"})

    if low_soc_count >= 2:
        risks.append({"level": "info", "segment": 0,
                      "message": f"全程 {low_soc_count} 段 SOC 低于 20%, 建议增加换电次数"})

    max_seg = max((s["distance"] for s in segments), default=0)
    if max_seg > 300:
        risks.append({"level": "danger", "segment": 0,
                      "message": f"最长一段 {max_seg:.0f}km，强烈建议增加换电次数"})
    elif max_seg > 250:
        risks.append({"level": "warning", "segment": 0,
                      "message": f"最长一段 {max_seg:.0f}km，建议增加换电次数"})

    # 智能推荐换电次数
    seg_count = max(len(segments) - 1, 1)
    if battery > 0 and consumption > 0:
        usable_range = (battery / consumption * 100) * 0.7
        if usable_range > 0:
            recommended = max(1, int(total_distance / usable_range))
            if recommended > seg_count:
                risks.append({
                    "level": "warning", "segment": 0,
                    "message": (f"全程 {total_distance:.0f}km，建议至少 {recommended} 次换电"
                                f"（当前 {seg_count} 次），请增加换电次数"),
                })
    return risks


# ============ 地理计算 ============


def _haversine(lng1: float, lat1: float, lng2: float, lat2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# ============ 去重 ============


def _deduplicate_stations(stations: list[dict]) -> list[dict]:
    if not stations:
        return []
    deduped = []
    for st in stations:
        is_dup = False
        for existing in deduped:
            if _haversine(existing["lng"], existing["lat"], st["lng"], st["lat"]) < 1.0:
                is_dup = True
                if len(st.get("name", "")) > len(existing.get("name", "")):
                    existing["name"] = st["name"]
                    existing["address"] = st.get("address", existing.get("address", ""))
                break
        if not is_dup:
            deduped.append(dict(st))
    return deduped


# ============ 主规划器（Beam Search） ============


def optimize_route(params: dict) -> dict[str, Any]:
    """
    Beam Search（束搜索）路线规划

    每步保留 K 条最优候选路线，最后选全局最优
    比贪心算法好得多，接近全局最优
    """
    start = params["start_location"]
    end = params["end_location"]
    max_swaps = params.get("max_swaps", 3)
    initial_soc = params.get("initial_soc", 100.0)
    strategy = params.get("strategy", "balanced")
    condition = params.get("condition", "normal")

    car_model = params.get("car_model", "")
    if car_model:
        consumption, battery = get_car_params(car_model)
    else:
        consumption = params.get("consumption", DEFAULT_CONSUMPTION)
        battery = params.get("battery_kwh", DEFAULT_BATTERY)

    stations = _deduplicate_stations(params.get("swap_stations", []))
    if not stations:
        return {"route": [], "total_distance": 0, "total_duration": 0,
                "unique_station_count": 0, "final_arrival_soc": initial_soc,
                "car_model": car_model, "consumption": consumption, "battery": battery,
                "segments": [],
                "risks": [{"level": "info", "segment": 0, "message": "未找到换电站"}]}

    total_dist = _haversine(start[0], start[1], end[0], end[1])

    def _progress(sl, sa, cl, ca):
        if total_dist < 1:
            return 0.5
        saved = _haversine(cl, ca, end[0], end[1]) - _haversine(sl, sa, end[0], end[1])
        return max(0, min(1, saved / total_dist))

    # ==== Beam Search ====
    BEAM_WIDTH = 4
    # 每条束: (indices, pos, soc, total_dist)
    beams: list[tuple] = [([], start, initial_soc, 0.0)]

    for _ in range(max_swaps):
        candidates: list[tuple] = []
        for indices, pos, soc, tdist in beams:
            for idx in range(len(stations)):
                if idx in indices:
                    continue
                st = stations[idx]
                sd = _haversine(pos[0], pos[1], st["lng"], st["lat"])
                if sd < 0.5:
                    continue  # 跳过太近的
                arrival = calc_arrival_soc(sd, battery, consumption, soc, condition)
                if arrival <= 0:
                    continue
                nsoc = calc_swap_result(arrival)
                prog = _progress(st["lng"], st["lat"], pos[0], pos[1])

                # 距离惩罚：如果已经选过站，且新站距离太近，大幅扣分
                proximity_penalty = 1.0
                if indices:
                    for prev_idx in indices:
                        prev_st = stations[prev_idx]
                        dist_to_prev = _haversine(prev_st["lng"], prev_st["lat"], st["lng"], st["lat"])
                        if dist_to_prev < 5:
                            proximity_penalty = 0.1  # 5km 内扣到 0.1
                        elif dist_to_prev < 10:
                            proximity_penalty = 0.3  # 10km 内扣到 0.3

                # 进度惩罚：如果没往目的地方向走，大幅扣分
                progress_penalty = 1.0
                if prog < 0.05 and sd > 5:
                    progress_penalty = 0.2  # 不但没靠近目的地还跑了很远，扣分

                max_sd = max(1, sd)
                ds = 1 - sd / max_sd
                if strategy == "shortest":
                    score = ds * 0.5 + prog * 0.3 + (arrival / 100) * 0.2
                elif strategy == "safest":
                    score = (arrival / 100) * 0.5 + ds * 0.3 + prog * 0.2
                else:
                    score = prog * 0.4 + ds * 0.3 + (arrival / 100) * 0.3

                # 最终得分 = 原始分 x 惩罚系数
                final_score = score * proximity_penalty * progress_penalty
                candidates.append((indices + [idx], (st["lng"], st["lat"]), nsoc, tdist + sd, final_score))

        if not candidates:
            break
        candidates.sort(key=lambda x: -x[4])
        beams = [(c[0], c[1], c[2], c[3]) for c in candidates[:BEAM_WIDTH]]

    # 选最优束
    def _beam_score(b):
        _, pos, _, dist = b
        return dist + _haversine(pos[0], pos[1], end[0], end[1])

    route_indices = min(beams, key=_beam_score)[0]

    # ==== 阶段二：2-opt 优化 ====
    selected = [stations[i] for i in route_indices]
    if len(selected) >= 3:
        improved = True
        while improved:
            improved = False
            cur_dist = _route_dist(selected, start, end)
            for i in range(len(selected) - 1):
                for j in range(i + 1, len(selected)):
                    rev = selected[:i] + selected[i:j + 1][::-1] + selected[j + 1:]
                    nd = _route_dist(rev, start, end)
                    if nd < cur_dist * 0.999:
                        selected = rev
                        cur_dist = nd
                        improved = True
                        break
                if improved:
                    break

    # 恢复索引
    optimized_indices = []
    for st in selected:
        for idx in route_indices:
            s = stations[idx]
            if abs(s["lng"] - st["lng"]) < 0.0001 and abs(s["lat"] - st["lat"]) < 0.0001:
                if idx not in optimized_indices:
                    optimized_indices.append(idx)
                break

    # ==== 构建路线 ====
    pos, soc = start, initial_soc
    route = []
    for seq, st_idx in enumerate(optimized_indices):
        st = stations[st_idx]
        d = _haversine(pos[0], pos[1], st["lng"], st["lat"])
        asoc = calc_arrival_soc(d, battery, consumption, soc, condition)
        ssoc = calc_swap_result(asoc)
        route.append({
            "index": seq + 1,
            "station_name": st["name"],
            "station_address": st.get("address", ""),
            "lng": st["lng"], "lat": st["lat"],
            "distance": round(d, 1),
            "duration": int(d / 60 * 3600) if d > 0 else 0,
            "arrival_soc": asoc, "swap_soc": ssoc,
        })
        pos, soc = (st["lng"], st["lat"]), ssoc

    # 最后一段
    lp = (route[-1]["lng"], route[-1]["lat"]) if route else start
    fd = _haversine(lp[0], lp[1], end[0], end[1])
    fsoc = calc_arrival_soc(fd, battery, consumption, soc, condition)

    segments = [{"distance": r["distance"], "arrival_soc": r["arrival_soc"]} for r in route]
    segments.append({"distance": round(fd, 1), "arrival_soc": fsoc})

    return {
        "route": route,
        "total_distance": round(sum(r["distance"] for r in route) + round(fd, 1), 1),
        "total_duration": sum(r["duration"] for r in route) + (int(fd / 60 * 3600) if fd > 0 else 0),
        "unique_station_count": len(route),
        "final_arrival_soc": fsoc,
        "car_model": car_model, "consumption": consumption, "battery": battery,
        "segments": segments,
        "risks": generate_risks(segments, battery, consumption),
    }


def _route_dist(stations: list[dict], start: tuple, end: tuple) -> float:
    """计算完整路线的总 Haversine 距离"""
    if not stations:
        return _haversine(start[0], start[1], end[0], end[1])
    total = _haversine(start[0], start[1], stations[0]["lng"], stations[0]["lat"])
    for i in range(len(stations) - 1):
        total += _haversine(stations[i]["lng"], stations[i]["lat"],
                            stations[i + 1]["lng"], stations[i + 1]["lat"])
    total += _haversine(stations[-1]["lng"], stations[-1]["lat"], end[0], end[1])
    return total