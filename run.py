import subprocess
import sys
import time

def start():
    # 1. 啟動後端/主程式
    backend_proc = subprocess.Popen([sys.executable, "main.py"])

    # 稍微暫停確保後端完成初始化（若不需要可移除）
    time.sleep(1)

    # 2. 啟動 Streamlit 前端
    frontend_proc = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", "ui.py"]
    )

    try:
        # 等待兩者執行
        frontend_proc.wait()
        backend_proc.wait()
    except KeyboardInterrupt:
        print("\n[INFO] 正在關閉所有服務...")
        frontend_proc.terminate()
        backend_proc.terminate()
        frontend_proc.wait()
        backend_proc.wait()

if __name__ == "__main__":
    start()