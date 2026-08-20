from decimal import Decimal

import pytest

from floor_insurance.trend import TrendMode, simple_moving_average, trend_signal


def decimals(*values: int | str) -> list[Decimal]:
    return [Decimal(str(value)) for value in values]


def test_above_uses_the_latest_completed_close_in_its_average():
    closes = decimals(*range(1, 21))
    signal = trend_signal(closes, window=20, mode=TrendMode.ABOVE)

    assert signal.close == Decimal("20")
    assert signal.moving_average == Decimal("10.5")
    assert signal.eligible is True
    assert signal.previous_close is None


def test_above_rejects_a_close_equal_to_the_average():
    signal = trend_signal(decimals(*([100] * 20)), window=20)
    assert signal.eligible is False


def test_crossover_requires_both_sides_of_the_cross():
    closes = decimals(*([100] * 20), 110)
    signal = trend_signal(closes, window=20, mode=TrendMode.CROSSOVER)

    assert signal.previous_close == Decimal("100")
    assert signal.previous_moving_average == Decimal("100")
    assert signal.moving_average == Decimal("100.5")
    assert signal.eligible is True

    already_above = decimals(*range(1, 22))
    assert trend_signal(already_above, window=20, mode=TrendMode.CROSSOVER).eligible is False


def test_signal_rejects_an_incomplete_history():
    with pytest.raises(ValueError, match="needs 20"):
        simple_moving_average(decimals(*range(19)), 20)
    with pytest.raises(ValueError, match="needs 21"):
        trend_signal(decimals(*range(20)), window=20, mode=TrendMode.CROSSOVER)
