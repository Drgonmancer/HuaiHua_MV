from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / '.env')


class Config:
    DATABASE_PATH = str(ROOT / 'data' / 'canvas.db')
