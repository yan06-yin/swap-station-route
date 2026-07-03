"""Quick diagnostic script"""
import sys, os, threading, time, asyncio
from pathlib import Path

root = Path(__file__).resolve().parent
sys.path.insert(0, str(root))
os.chdir(root)

print("1. Testing imports...")
try:
    from src.main import app
    routes = [r.path for r in app.routes if hasattr(r, 'path')]
    print(f"   OK - {len(routes)} routes registered")
    for r in sorted(routes)[:8]:
        print(f"     - {r}")
except Exception as e:
    print(f"   FAIL: {e}")
    sys.exit(1)

print("\n2. Testing static files...")
static_dir = root / "static"
print(f"   static/ exists: {static_dir.exists()}")
print(f"   index.html: {(static_dir / 'index.html').exists()}")
print(f"   app.html:   {(static_dir / 'app.html').exists()}")

print("\n3. Testing env vars...")
from dotenv import load_dotenv
env_path = root / ".env"
print(f"   .env exists: {env_path.exists()}")
load_dotenv(env_path)
ak = os.getenv('AMAP_API_KEY', '')
print(f"   AMAP_API_KEY: {'SET (' + ak[:8] + '...)' if ak else 'NOT SET'}")
jk = os.getenv('AMAP_JS_API_KEY', '')
print(f"   AMAP_JS_API_KEY: {'SET (' + jk[:8] + '...)' if jk else 'NOT SET'}")

print("\n4. Starting server on port 9999...")
import uvicorn, httpx

def start():
    uvicorn.run(app, host="127.0.0.1", port=9999, log_level="error")

t = threading.Thread(target=start, daemon=True)
t.start()
time.sleep(2)

async def test():
    async with httpx.AsyncClient(base_url="http://127.0.0.1:9999") as c:
        for path in ["/ping", "/api/v1/debug", "/", "/share/demo", "/api/v1/config"]:
            try:
                r = await c.get(path)
                print(f"   GET {path:30s} -> {r.status_code}")
            except Exception as e:
                print(f"   GET {path:30s} -> ERROR: {e}")

asyncio.run(test())
print("\nDONE")