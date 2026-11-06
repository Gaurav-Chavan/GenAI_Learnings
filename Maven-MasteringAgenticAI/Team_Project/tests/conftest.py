import pytest

from claim_triage.data.build import build
from claim_triage.data.config import load_config


@pytest.fixture(scope="session")
def built_dataset(tmp_path_factory):
    """One full Data 360 build shared by every test (deterministic: same seed gives the same data as the project's data folder)."""
    cfg = load_config({"data_dir": str(tmp_path_factory.mktemp("data360"))})
    summary = build(cfg, verbose=False)
    return cfg, summary
