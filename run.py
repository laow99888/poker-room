"""启动本地服务：python run.py，然后浏览器打开 http://127.0.0.1:8765"""

import uvicorn

if __name__ == "__main__":
    print("德州扑克离线工具  ->  http://127.0.0.1:8765   (Ctrl+C 退出)")
    uvicorn.run("backend.app:app", host="127.0.0.1", port=8765, log_level="warning")
