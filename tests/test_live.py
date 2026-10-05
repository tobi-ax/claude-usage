"""Checks against Anthropic's live pricing page. Run with: pytest -m live

`claude-usage update` parses that page, so these tests notice a change to its
layout before an update fails or reads wrong numbers.
"""
import pytest

from conftest import cu, load_script

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def live_models():
    # The autouse fixture replaces fetch_pricing_markdown on the shared module; a fresh copy keeps the real one.
    real = load_script()
    return cu.parse_pricing_table(real.fetch_pricing_markdown(cu.PRICING_SOURCE_URL, cu.PRICING_FETCH_TIMEOUT))


def test_live_page_still_parses_into_enough_models(live_models):
    assert len(live_models) >= 5


def test_every_live_row_has_five_finite_rates(live_models):
    assert all(cu.is_rate_dict(rates) for rates in live_models.values())


def test_every_builtin_model_is_still_on_the_live_page(live_models):
    assert sorted(set(cu.PRICING) - set(live_models)) == []
