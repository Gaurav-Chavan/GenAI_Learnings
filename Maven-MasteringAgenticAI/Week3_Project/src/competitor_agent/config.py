"""Runtime settings, read from the environment at call time (so tests can override)."""
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

# Trust the OS certificate store (keeps verification ON). Needed behind antivirus / corporate
# proxies that re-sign HTTPS traffic (e.g. Avast), which Python's bundled certifi doesn't know.
try:
    import truststore

    truststore.inject_into_ssl()
except ImportError:  # pragma: no cover - optional hardening
    pass


def _flag(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes"}


@dataclass(frozen=True)
class Settings:
    use_stubs: bool
    max_retries: int  # Critic -> Gatherer refinement loops
    max_tavily_calls: int  # hard budget per run
    max_revisions: int  # writer revisions after human feedback
    top_n: int
    briefings_dir: str
    checkpoint_db: str


def get_settings() -> Settings:
    return Settings(
        use_stubs=_flag("USE_STUBS", "true"),
        max_retries=int(os.getenv("MAX_RETRIES", "2")),
        max_tavily_calls=int(os.getenv("MAX_TAVILY_CALLS", "40")),
        max_revisions=int(os.getenv("MAX_REVISIONS", "2")),
        top_n=int(os.getenv("TOP_N_COMPETITORS", "3")),
        briefings_dir=os.getenv("BRIEFINGS_DIR", "briefings"),
        checkpoint_db=os.getenv("CHECKPOINT_DB", "checkpoints.sqlite"),
    )
