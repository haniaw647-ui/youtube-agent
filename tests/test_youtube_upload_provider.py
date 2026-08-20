from unittest.mock import MagicMock, patch

import pytest

from src.providers.youtube.youtube_api import YouTubeAPIProvider


@pytest.mark.asyncio
async def test_upload_video_calls_google_api_and_returns_result():
    mock_youtube = MagicMock()
    mock_youtube.videos.return_value.insert.return_value.execute.return_value = {"id": "abc123"}
    mock_youtube.thumbnails.return_value.set.return_value.execute.return_value = {}

    with (
        patch("src.providers.youtube.youtube_api.build", return_value=mock_youtube),
        patch("src.providers.youtube.youtube_api.Credentials"),
    ):
        provider = YouTubeAPIProvider()
        result = await provider.upload_video(
            refresh_token="fake-refresh-token",
            video_bytes=b"fake video bytes",
            title="Test Title",
            description="Test description",
            tags=["a", "b"],
            privacy_status="private",
            thumbnail_bytes=b"fake thumbnail bytes",
        )

    assert result.video_id == "abc123"
    assert result.url == "https://www.youtube.com/watch?v=abc123"
    mock_youtube.videos.return_value.insert.assert_called_once()
    insert_kwargs = mock_youtube.videos.return_value.insert.call_args.kwargs
    assert insert_kwargs["body"]["snippet"]["title"] == "Test Title"
    assert insert_kwargs["body"]["status"]["privacyStatus"] == "private"
    mock_youtube.thumbnails.return_value.set.assert_called_once()


@pytest.mark.asyncio
async def test_upload_video_skips_thumbnail_when_none_given():
    mock_youtube = MagicMock()
    mock_youtube.videos.return_value.insert.return_value.execute.return_value = {"id": "xyz"}

    with (
        patch("src.providers.youtube.youtube_api.build", return_value=mock_youtube),
        patch("src.providers.youtube.youtube_api.Credentials"),
    ):
        provider = YouTubeAPIProvider()
        result = await provider.upload_video(
            refresh_token="fake",
            video_bytes=b"data",
            title="T",
            description="D",
            tags=[],
            privacy_status="private",
        )

    assert result.video_id == "xyz"
    mock_youtube.thumbnails.assert_not_called()


@pytest.mark.asyncio
async def test_upload_video_with_publish_at_forces_private_and_sets_publish_at():
    mock_youtube = MagicMock()
    mock_youtube.videos.return_value.insert.return_value.execute.return_value = {"id": "sched1"}

    with (
        patch("src.providers.youtube.youtube_api.build", return_value=mock_youtube),
        patch("src.providers.youtube.youtube_api.Credentials"),
    ):
        provider = YouTubeAPIProvider()
        await provider.upload_video(
            refresh_token="fake",
            video_bytes=b"data",
            title="T",
            description="D",
            tags=[],
            # A public request should still get overridden to private —
            # YouTube requires that pairing for a scheduled publish.
            privacy_status="public",
            publish_at="2026-09-01T12:00:00Z",
        )

    insert_kwargs = mock_youtube.videos.return_value.insert.call_args.kwargs
    assert insert_kwargs["body"]["status"]["privacyStatus"] == "private"
    assert insert_kwargs["body"]["status"]["publishAt"] == "2026-09-01T12:00:00Z"
