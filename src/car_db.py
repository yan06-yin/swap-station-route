"""
车型能耗字典
key: 品牌-车型
value: { consumption: 百公里能耗 kWh/100km, battery: 电池容量 kWh }
"""

CAR_CONSUMPTION_DB = {
    # 蔚来
    "蔚来-ET5": {"consumption": 13.5, "battery": 75},
    "蔚来-ET5T": {"consumption": 13.8, "battery": 75},
    "蔚来-ET7": {"consumption": 14.5, "battery": 100},
    "蔚来-ES6": {"consumption": 14.2, "battery": 75},
    "蔚来-ES7": {"consumption": 15.5, "battery": 100},
    "蔚来-ES8": {"consumption": 17.0, "battery": 100},
    "蔚来-EC6": {"consumption": 13.8, "battery": 75},
    "蔚来-EC7": {"consumption": 14.5, "battery": 100},
    # 小鹏
    "小鹏-G6": {"consumption": 13.0, "battery": 66},
    "小鹏-G9": {"consumption": 15.5, "battery": 98},
    "小鹏-P7i": {"consumption": 13.2, "battery": 80},
    # 智己
    "智己-L7": {"consumption": 16.5, "battery": 85},
    # 极氪
    "极氪-001": {"consumption": 16.0, "battery": 86},
    "极氪-X": {"consumption": 12.8, "battery": 49},
}

# 品牌 → 支持的车型
BRAND_CARS = {
    "蔚来": ["ET5", "ET5T", "ET7", "ES6", "ES7", "ES8", "EC6", "EC7"],
    "小鹏": ["G6", "G9", "P7i"],
    "智己": ["L7"],
    "极氪": ["001", "X"],
}

# 默认能耗（车型不在字典中时的 fallback）
DEFAULT_CONSUMPTION = 14.5  # kWh/100km
DEFAULT_BATTERY = 75  # kWh
