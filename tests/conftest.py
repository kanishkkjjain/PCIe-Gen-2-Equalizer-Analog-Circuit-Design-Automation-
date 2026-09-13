"""Shared pytest fixtures and the needs_pdk marker.

The pure-Python tier must run anywhere (CI, a laptop with no PDK), so anything
that shells out to ngspice is marked ``needs_pdk`` and skipped when the SKY130
library cannot be located.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _pdk_available() -> bool:
    try:
        from eqrl import pdk
        pdk.find_sky130_lib()
        return True
    except Exception:
        return False


HAVE_PDK = _pdk_available()


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "needs_pdk: requires the SKY130 PDK and a working ngspice")


def pytest_collection_modifyitems(config, items):
    if HAVE_PDK:
        return
    skip = pytest.mark.skip(reason="SKY130 PDK not found (set PDK_ROOT)")
    for item in items:
        if "needs_pdk" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def corner():
    from eqrl import pdk
    return pdk.typical_corner()


@pytest.fixture(scope="session")
def sim(tmp_path_factory):
    from eqrl.spice import Simulator
    return Simulator(tmp_path_factory.mktemp("eqrl_test"), timeout=300)
