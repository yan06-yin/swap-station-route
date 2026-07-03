"""
换电站打卡路线规划 — 启动脚本
用法: python run.py
"""
import sys
import os
from pathlib import Path

# 将项目根目录加入 Python 路径
root = Path(__file__).resolve().parent
sys.path.insert(0, str(root))
os.chdir(root)

# 加载 .env
from dotenv import load_dotenv
env_path = root / ".env"
if env_path.exists():
    load_dotenv(env_path)

if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8000"))
    print(f"\n  ⚡ 换电站打卡路线规划服务")
    print(f"  ─────────────────────────")
    print(f"  企业端:  http://localhost:{port}")
    print(f"  演示:    http://localhost:{port}/share/demo")
    print(f"  ─────────────────────────\n")

    # 直接导入 app 对象，避免 reload 子进程的路径问题
    from src.main import app
    uvicorn.run(app, host="0.0.0.0", port=port, reload=False)