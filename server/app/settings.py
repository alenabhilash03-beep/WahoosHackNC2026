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
    # Community rounds: shared speeches and reactions. Off until the operator turns it on.
    community_enabled: bool = False
    # Review: shared speeches stay hidden until approved (server/scripts/review_community.py).
    # True approves at share time instead, for a demo among people who trust each other.
    community_auto_approve: bool = False
    community_max_audio_bytes: int = 4 * 1024 * 1024      # live recordings have been 0.2 to 1.2 MB
    community_attempts_per_round: int = 5
    community_reactions_per_minute: int = 60
    community_maintenance_seconds: int = 600               # rounds and expired audio; 0 turns it off

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
                   data_dir=Path(os.getenv('DATA_DIR', str(ROOT / 'server/data'))),
                   community_enabled=os.getenv('COMMUNITY_ENABLED', '').lower() == 'true',
                   community_auto_approve=os.getenv('COMMUNITY_AUTO_APPROVE', '').lower() == 'true',
                   community_maintenance_seconds=int(os.getenv('COMMUNITY_MAINTENANCE_SECONDS', '600') or 0))
