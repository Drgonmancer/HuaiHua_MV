"""兼容原启动命令；实际应用位于 backend/app.py。"""
import runpy
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent / 'backend'
sys.path.insert(0, str(BACKEND_DIR))

if __name__ == '__main__':
    runpy.run_path(str(BACKEND_DIR / 'app.py'), run_name='__main__')
else:
    from backend.app import create_app
