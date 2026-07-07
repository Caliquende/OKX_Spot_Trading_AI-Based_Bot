import pytest

from main import should_notify_sentiment_report


def test_cached_non_zero_sentiment_does_not_bypass_disabled_cycle_notifications():
    assert not should_notify_sentiment_report(
        notify_every_cycle=False,
        refreshed=False,
        force_refresh=False,
    )


@pytest.mark.parametrize(
    ("notify_every_cycle", "refreshed", "force_refresh"),
    [
        (True, False, False),
        (False, True, False),
        (False, False, True),
    ],
)
def test_sentiment_report_is_sent_for_each_explicit_trigger(
    notify_every_cycle,
    refreshed,
    force_refresh,
):
    assert should_notify_sentiment_report(
        notify_every_cycle=notify_every_cycle,
        refreshed=refreshed,
        force_refresh=force_refresh,
    )
