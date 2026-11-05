"""#80: a republish keeps the first publishedAt; updatedAt is this release's own time."""
from datetime import datetime, timedelta, timezone
from app.post_publications import release_stamps, updated_at

FIRST = datetime(2026, 11, 1, 9, 30, tzinfo=timezone.utc)


def test_first_publish_stamps_both_with_now():
    assert release_stamps(None, FIRST) == {'publishedAt': '2026-11-01T09:30:00Z', 'updatedAt': '2026-11-01T09:30:00Z'}


def test_republish_keeps_previous_published_at():
    previous = {'publishedAt': '2026-11-01T09:30:00Z', 'updatedAt': '2026-11-02T00:00:00Z'}
    later = FIRST + timedelta(days=3, microseconds=5)
    assert release_stamps(previous, later) == {'publishedAt': '2026-11-01T09:30:00Z', 'updatedAt': '2026-11-04T09:30:00.000005Z'}


def test_republish_over_legacy_snapshot_keeps_its_published_at():
    assert release_stamps({'publishedAt': '2026-10-01T00:00:00Z'}, FIRST)['publishedAt'] == '2026-10-01T00:00:00Z'


def test_now_is_normalized_to_utc():
    seoul = timezone(timedelta(hours=9))
    assert release_stamps(None, FIRST.astimezone(seoul))['updatedAt'] == '2026-11-01T09:30:00Z'


def test_updated_at_falls_back_to_published_at_for_legacy_snapshots():
    assert updated_at({'publishedAt': '2026-10-01T00:00:00Z'}) == '2026-10-01T00:00:00Z'
    assert updated_at({'publishedAt': '2026-10-01T00:00:00Z', 'updatedAt': '2026-10-05T00:00:00Z'}) == '2026-10-05T00:00:00Z'
