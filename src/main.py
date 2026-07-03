"""
换电站打卡路线规划 — 后端服务
"""

import json
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from src.amap_client import get_driving_route, get_js_api_key, get_js_security_code, search_swap_stations
from src.feishu_bot import send_route_card
from src.route_planner import optimize_route

# ============ 项目路径 ============

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
STATIC_DIR = ROOT_DIR / "static"
DATA_DIR.mkdir(exist_ok=True)

# ============ 加载环境变量 ============

env_path = ROOT_DIR / ".env"
if env_path.exists():
    load_dotenv(env_path)

# ============ FastAPI 应用 ============

app = FastAPI(title="换电站打卡路线规划", version="0.2.0")

# 内存存储
routes_store: dict[str, dict] = {}


# ============ 请求/响应模型 ============


class RouteRequest(BaseModel):
    city: str = Field(..., description="城市名，如 北京")
    start_lng: float = Field(..., description="起点经度 (GCJ-02)")
    start_lat: float = Field(..., description="起点纬度 (GCJ-02)")
    start_name: str = Field(default="", description="起点名称")
    end_lng: float = Field(..., description="终点经度 (GCJ-02)")
    end_lat: float = Field(..., description="终点纬度 (GCJ-02)")
    end_name: str = Field(default="", description="终点名称")
    car_model: str = Field(default="", description="车型，如 蔚来-ET5")
    battery_kwh: float = Field(default=0, description="电池容量，0=按车型查表")
    consumption: float = Field(default=0, description="百公里能耗，0=按车型查表")
    initial_soc: float = Field(default=100.0, ge=0, le=100, description="出发 SOC (%)")
    max_swaps: int = Field(default=3, ge=1, le=10, description="最多换电次数")


class RouteResponse(BaseModel):
    route_id: str
    share_url: str
    data: dict[str, Any]


class FeishuShareRequest(BaseModel):
    route_id: str = Field(..., description="路线 ID")
    webhook_url: str = Field(..., description="飞书机器人 Webhook URL")


# ============ 路由 ============


@app.get("/api/v1/config")
async def get_config():
    """获取前端配置（如地图 API Key 等）"""
    js_api_key = get_js_api_key()
    js_security_code = get_js_security_code()
    return {
        "amap_js_api_key": js_api_key,
        "amap_js_security_code": js_security_code,
        "has_feishu_webhook": bool(os.getenv("FEISHU_WEBHOOK")),
    }


@app.post("/api/v1/route/create", response_model=RouteResponse)
async def create_route(req: RouteRequest):
    """创建打卡路线"""
    # 1. 搜索换电站（多城市搜索）
    cities_to_search = [req.city]
    # 如果终点城市不同，也搜一下
    if req.city and req.end_name and req.end_name != req.city:
        cities_to_search.append(req.end_name)

    all_stations = []
    for city in cities_to_search:
        try:
            stations = await search_swap_stations(city)
            all_stations.extend(stations)
        except Exception:
            pass

    # 去重（按名称）
    seen_names = set()
    stations = []
    for s in all_stations:
        if s["name"] not in seen_names:
            seen_names.add(s["name"])
            stations.append(s)

    if not stations:
        raise HTTPException(
            status_code=404,
            detail=f"在城市 '{req.city}' 及附近未找到换电站，"
            "请检查城市名称或高德 API 配置",
        )

    # 2. 构建规划参数
    params = {
        "city": req.city,
        "start_location": (req.start_lng, req.start_lat),
        "end_location": (req.end_lng, req.end_lat),
        "car_model": req.car_model,
        "battery_kwh": req.battery_kwh if req.battery_kwh > 0 else None,
        "consumption": req.consumption if req.consumption > 0 else None,
        "initial_soc": req.initial_soc,
        "max_swaps": req.max_swaps,
        "swap_stations": stations,
    }

    # 3. 执行路线规划（Haversine 优化）
    result = optimize_route(params)

    # 4. 用真实道路距离替换 Haversine 估算值
    await _enrich_with_real_distances(result, params)

    # 5. 生成 route_id
    route_id = uuid.uuid4().hex[:12]

    # 6. 持久化
    route_record = {
        "id": route_id,
        "created_at": datetime.now().isoformat(),
        "params": req.model_dump(),
        "result": result,
    }
    routes_store[route_id] = route_record

    filepath = DATA_DIR / f"{route_id}.json"
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(route_record, f, ensure_ascii=False, indent=2)

    return RouteResponse(
        route_id=route_id,
        share_url=f"/share/{route_id}",
        data=result,
    )


async def _enrich_with_real_distances(result: dict, params: dict) -> None:
    """
    用高德驾车路线规划 API 替换 Haversine 估算值

    在 result 的 route 中，用真实道路距离和时长替换估算值，
    同时添加 polyline 字段用于地图绘制
    """
    route = result.get("route", [])
    if not route:
        return

    start = params["start_location"]
    end = params["end_location"]

    # 各段起点
    prev_locations = [start] + [(s["lng"], s["lat"]) for s in route[:-1]]

    for i, (prev_loc, seg) in enumerate(zip(prev_locations, route)):
        dest = (seg["lng"], seg["lat"])
        try:
            real = await get_driving_route(prev_loc, dest)
            if real["distance"] > 0:
                seg["distance"] = round(real["distance"] / 1000, 1)  # 米→公里
                seg["duration"] = real["duration"]  # 秒
                seg["polyline"] = real["polyline"]
        except Exception:
            pass  # 保持 Haversine 估算值

    # 最后一段到终点
    last_loc = (route[-1]["lng"], route[-1]["lat"])
    try:
        real = await get_driving_route(last_loc, end)
        if real["distance"] > 0:
            result["final_driving_distance"] = round(real["distance"] / 1000, 1)
            result["final_driving_duration"] = real["duration"]
            # 用真实距离重新计算最终 SOC
            final_dist_km = real["distance"] / 1000
            final_soc = _recalc_final_soc(
                final_dist_km, result.get("consumption", 14.5),
                result.get("battery", 75), route[-1]["swap_soc"],
            )
            result["final_arrival_soc"] = final_soc
            result["final_polyline"] = real["polyline"]
    except Exception:
        pass

    # 重新计算总距离和时长
    result["total_distance"] = round(sum(s["distance"] for s in route), 1)
    if "final_driving_distance" in result:
        result["total_distance"] = round(
            result["total_distance"] + result["final_driving_distance"], 1
        )
    result["total_duration"] = sum(s["duration"] for s in route)
    if "final_driving_duration" in result:
        result["total_duration"] += result["final_driving_duration"]

    # 重新计算 segments（用于风险提示）
    segments = []
    for s in route:
        segments.append({"distance": s["distance"], "arrival_soc": s["arrival_soc"]})
    segments.append({
        "distance": result.get("final_driving_distance", result.get("segments", [{}])[-1].get("distance", 0)),
        "arrival_soc": result.get("final_arrival_soc", 0),
    })
    result["segments"] = segments

    # 重新生成风险提示（使用真实距离）
    from src.route_planner import generate_risks
    result["risks"] = generate_risks(segments)


def _recalc_final_soc(
    final_dist_km: float, consumption: float, battery: float, last_swap_soc: float
) -> float:
    """用真实距离重新计算最终到达 SOC"""
    from src.route_planner import calc_arrival_soc
    return calc_arrival_soc(final_dist_km, battery, consumption, last_swap_soc)


@app.get("/api/v1/route/{route_id}")
async def get_route(route_id: str):
    """获取路线详情"""
    record = _load_route(route_id)
    if record is None:
        raise HTTPException(status_code=404, detail="路线不存在")

    return {
        "route_id": route_id,
        "created_at": record["created_at"],
        "result": record["result"],
        "params": record["params"],
    }


@app.post("/api/v1/route/share-to-feishu")
async def share_to_feishu(req: FeishuShareRequest):
    """分享路线到飞书"""
    record = _load_route(req.route_id)
    if record is None:
        raise HTTPException(status_code=404, detail="路线不存在")

    share_url = f"{_get_base_url()}/share/{req.route_id}"

    result = await send_route_card(
        route_id=req.route_id,
        route_data=record["result"],
        params=record["params"],
        share_url=share_url,
        webhook_url=req.webhook_url,
    )

    if not result["ok"]:
        raise HTTPException(status_code=500, detail=result["message"])

    return {"ok": True, "message": "飞书消息发送成功"}


@app.get("/api/v1/navigate")
async def open_navigate(
    lng: float,
    lat: float,
    name: str = Query(default="", description="目的地名称"),
):
    """
    一键跳转导航
    返回高德/百度 Deep Link
    """
    amap_url = f"https://uri.amap.com/navigation?to={lng},{lat},{name}&mode=car"
    baidu_url = (
        f"https://api.map.baidu.com/direction?coord_type=gcj02"
        f"&destination={lat},{lng}&dest={name}&output=html"
    )
    return {
        "name": name,
        "location": f"{lng},{lat}",
        "amap": amap_url,
        "baidu": baidu_url,
    }


@app.get("/api/v1/debug")
async def debug_info():
    """诊断信息"""
    return {
        "root_dir": str(ROOT_DIR),
        "static_dir": str(STATIC_DIR),
        "data_dir": str(DATA_DIR),
        "index_html_exists": (STATIC_DIR / "index.html").exists(),
        "app_html_exists": (STATIC_DIR / "app.html").exists(),
        "env_exists": (ROOT_DIR / ".env").exists(),
        "amap_api_key": bool(os.getenv("AMAP_API_KEY")),
        "amap_js_api_key": bool(os.getenv("AMAP_JS_API_KEY")),
        "port": os.getenv("PORT", "8000"),
    }


@app.get("/ping")
async def ping():
    """连通性测试"""
    return {"status": "ok", "message": "server is running"}


@app.get("/")
async def index():
    """首页 → 企业端表单"""
    return FileResponse(STATIC_DIR / "app.html")


@app.get("/share/{route_id}")
async def share_page(route_id: str):
    """分享页面（H5）"""
    return FileResponse(STATIC_DIR / "index.html")


# ============ 静态文件 ============

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ============ 辅助函数 ============


def _load_route(route_id: str) -> dict | None:
    """从内存或文件加载路线"""
    record = routes_store.get(route_id)
    if record:
        return record

    filepath = DATA_DIR / f"{route_id}.json"
    if filepath.exists():
        with open(filepath, "r", encoding="utf-8") as f:
            record = json.load(f)
        routes_store[route_id] = record
        return record

    return None


def _get_base_url() -> str:
    """获取基础 URL（用于构建分享链接）"""
    import socket
    host = os.getenv("HOST", "localhost")
    port = os.getenv("PORT", "8000")
    if host in ("0.0.0.0", "127.0.0.1", "localhost"):
        return f"http://localhost:{port}"
    return f"http://{host}:{port}"


# ============ 本地测试用 demo 路由 ============


@app.get("/api/v1/demo-route")
async def demo_route():
    """
    本地演示用：不依赖高德 API，用模拟数据生成一条路线
    适合在没有 API Key 的情况下体验完整流程
    """
    demo_stations = [
        {"name": "蔚来换电站(浦东嘉里城站)", "lng": 121.505, "lat": 31.227, "address": "浦东新区花木路1378号"},
        {"name": "蔚来换电站(徐家汇站)", "lng": 121.437, "lat": 31.190, "address": "徐汇区虹桥路1号"},
        {"name": "蔚来换电站(静安寺站)", "lng": 121.447, "lat": 31.228, "address": "静安区南京西路1618号"},
        {"name": "蔚来换电站(五角场站)", "lng": 121.509, "lat": 31.305, "address": "杨浦区淞沪路77号"},
        {"name": "蔚来换电站(虹桥火车站站)", "lng": 121.398, "lat": 31.198, "address": "闵行区申贵路1500号"},
    ]

    params = {
        "city": "上海",
        "start_location": (121.500, 31.240),  # 陆家嘴
        "end_location": (121.395, 31.195),  # 虹桥
        "car_model": "蔚来-ET5",
        "initial_soc": 100.0,
        "max_swaps": 3,
        "swap_stations": demo_stations,
    }

    result = optimize_route(params)

    return {
        "route_id": "demo",
        "is_demo": True,
        "result": result,
    }


# ============ 入口 ============

if __name__ == "__main__":
    # 确保项目根目录在 Python 路径中
    if str(ROOT_DIR) not in sys.path:
        sys.path.insert(0, str(ROOT_DIR))

    import uvicorn

    port = int(os.getenv("PORT", "8000"))
    print(f"\n  ⚡ 换电站打卡路线规划服务")
    print(f"  ─────────────────────────")
    print(f"  企业端:  http://localhost:{port}")
    print(f"  演示:    http://localhost:{port}/share/demo")
    print(f"  ─────────────────────────\n")
    uvicorn.run("src.main:app", host="0.0.0.0", port=port, reload=True)