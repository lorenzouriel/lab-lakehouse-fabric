import pytest

from generator.config import calibration_path, load_config


@pytest.fixture(scope="session")
def base_cfg():
    cfg = load_config()
    if not calibration_path(cfg).exists():
        pytest.skip("calibration missing - src/calibration/fruit_juice.json")
    return cfg


def make_cfg(tmp_path, end, seed=None, formats=None, **extra):
    """Config for a test run writing into tmp_path (which acts as the data/ folder)."""
    overrides = {"period.end": end, **extra}
    if seed is not None:
        overrides["seed"] = seed
    if formats is not None:
        overrides["output.formats"] = formats
    return load_config(**overrides), tmp_path
