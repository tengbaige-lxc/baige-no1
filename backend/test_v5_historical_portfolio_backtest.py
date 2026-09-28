import math

from v5_historical_portfolio_backtest import direction_return, resample_5m, slot_times


def test_direction_return_is_symmetric_by_contract_side():
    assert math.isclose(direction_return("LONG", 100, 110), 0.1)
    assert round(direction_return("SHORT", 100, 90), 6) == 0.111111


def test_resample_requires_complete_groups_and_estimates_quote_volume():
    rows = [
        [index * 300_000, 10, 11, 9, 10 + index, 2, 0, 0, "1"]
        for index in range(6)
    ]
    result = resample_5m(rows, 1_800_000)
    assert len(result) == 1
    assert result[0][4] == 15
    assert result[0][5] == 12
    assert result[0][7] == sum(2 * (10 + index) for index in range(6))


def test_slot_times_uses_only_requested_utc_hours():
    day = 1_700_006_400_000  # 2023-11-15 00:00:00 UTC
    result = slot_times(day, day + 86_400_000, [0, 12])
    assert result == [day, day + 43_200_000, day + 86_400_000]
