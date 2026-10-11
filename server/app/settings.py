from dataclasses import dataclass, field
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


@dataclass
class Settings:
    demo_mode: bool = False
    local_mode: bool = False
    supabase_url: str = ''
    supabase_service_role_key: str = ''
    supabase_anon_key: str = ''
    cors_origins: list[str] = field(default_factory=list)
    data_dir: Path = ROOT / 'server/data'
    max_audio_bytes: int = 15 * 1024 * 1024

    @classmethod
    def from_env(cls):
        path = ROOT / 'server/.env'
        if path.exists():
            for line in path.read_text(encoding='utf-8').splitlines():
                if '=' in line and not line.lstrip().startswith('#'):
                    key, value = line.split('=', 1)
                    os.environ.setdefault(key.strip(), value.strip().strip('\"\''))
        return cls(demo_mode=os.getenv('DEMO_MODE', '').lower() == 'true',
                   local_mode=os.getenv('LOCAL_MODE', '').lower() == 'true',
                   supabase_url=os.getenv('SUPABASE_URL', ''),
                   supabase_service_role_key=os.getenv('SUPABASE_SERVICE_ROLE_KEY', ''),
                   supabase_anon_key=os.getenv('SUPABASE_ANON_KEY', ''),
                   cors_origins=[origin.strip() for origin in os.getenv('CORS_ORIGINS', '').split(',') if origin.strip()],
                   data_dir=Path(os.getenv('DATA_DIR', str(ROOT / 'server/data'))))
