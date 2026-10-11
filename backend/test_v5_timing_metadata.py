from v5_main import strength_metadata


CONFIG = {"strong_trend_min_score": 7.5}


def _factors(*, trigger=True, divergence=True):
    return [
        {"key": "trend_filter", "triggered": True},
        {"key": "trend_v3_30m", "triggered": True},
        {"key": "trend_v3_adx_atr", "triggered": True,
         "raw": {"adx": 40}},
        {"key": "trend_v3_5m", "triggered": trigger,
         "score_added": 1.0 if trigger else 0.0},
        {"key": "trend_v3_structure_confirmation",
         "raw": {"5m_divergence": divergence}},
    ]


def test_timing_score_includes_five_minute_divergence():
    metadata = strength_metadata(_factors(), 5.5, CONFIG)
    assert metadata["trigger_score_5m"] == 1.0
    assert metadata["timing_score_5m"] == 1.5
    assert 5.5 - metadata["timing_score_5m"] == 4.0


def test_untriggered_divergence_does_not_add_score():
    metadata = strength_metadata(
        _factors(trigger=False, divergence=True), 3.0, CONFIG)
    assert metadata["timing_score_5m"] == 0.0


def test_missing_structure_metadata_is_not_assumed_zero():
    metadata = strength_metadata(_factors()[:-1], 5.5, CONFIG)
    assert metadata["timing_score_5m"] is None
