import os
import sys
from pathlib import Path

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test.test.test")
os.environ.setdefault("GEMINI_API_KEY", "test")
os.environ.setdefault("ELEVENLABS_API_KEY", "test")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
