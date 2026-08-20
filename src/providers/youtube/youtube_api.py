import asyncio
from io import BytesIO

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

from src.orchestrator.config import get_settings
from src.providers.youtube.base import UploadResult, VideoStats, YouTubeProvider

TOKEN_URI = "https://oauth2.googleapis.com/token"


class YouTubeAPIProvider(YouTubeProvider):
    """google-api-python-client is sync-only, so the actual work runs in a
    thread — resumable upload + credential refresh logic is exactly the kind
    of thing worth trusting to Google's own client rather than hand-rolling."""

    def _credentials(self, refresh_token: str) -> Credentials:
        settings = get_settings()
        return Credentials(
            None,
            refresh_token=refresh_token,
            token_uri=TOKEN_URI,
            client_id=settings.youtube_oauth_client_id,
            client_secret=settings.youtube_oauth_client_secret,
        )

    async def get_video_stats(self, refresh_token: str, video_id: str) -> VideoStats:
        return await asyncio.to_thread(self._get_video_stats_sync, refresh_token, video_id)

    def _get_video_stats_sync(self, refresh_token: str, video_id: str) -> VideoStats:
        youtube = build("youtube", "v3", credentials=self._credentials(refresh_token))
        response = youtube.videos().list(part="statistics", id=video_id).execute()
        items = response.get("items", [])
        if not items:
            # Deleted/private-to-someone-else/never-indexed — treat as zero
            # rather than raising, so one missing video doesn't abort a batch pull.
            return VideoStats(view_count=0, like_count=0, comment_count=0)
        stats = items[0]["statistics"]
        return VideoStats(
            view_count=int(stats.get("viewCount", 0)),
            like_count=int(stats.get("likeCount", 0)),
            comment_count=int(stats.get("commentCount", 0)),
        )

    async def upload_video(
        self,
        refresh_token: str,
        video_bytes: bytes,
        title: str,
        description: str,
        tags: list[str],
        privacy_status: str,
        thumbnail_bytes: bytes | None = None,
        publish_at: str | None = None,
    ) -> UploadResult:
        return await asyncio.to_thread(
            self._upload_sync,
            refresh_token,
            video_bytes,
            title,
            description,
            tags,
            privacy_status,
            thumbnail_bytes,
            publish_at,
        )

    def _upload_sync(
        self,
        refresh_token: str,
        video_bytes: bytes,
        title: str,
        description: str,
        tags: list[str],
        privacy_status: str,
        thumbnail_bytes: bytes | None,
        publish_at: str | None = None,
    ) -> UploadResult:
        youtube = build("youtube", "v3", credentials=self._credentials(refresh_token))

        # YouTube schedules a video itself once uploaded — no polling/cron
        # needed on our side. It requires privacyStatus="private" alongside
        # publishAt (an RFC3339 timestamp); it flips to public automatically
        # at that instant.
        status: dict = {"privacyStatus": "private" if publish_at else privacy_status}
        if publish_at:
            status["publishAt"] = publish_at

        body = {
            "snippet": {"title": title, "description": description, "tags": tags},
            "status": status,
        }
        media = MediaIoBaseUpload(
            BytesIO(video_bytes), mimetype="video/mp4", resumable=True, chunksize=-1
        )
        response = (
            youtube.videos().insert(part="snippet,status", body=body, media_body=media).execute()
        )
        video_id = response["id"]

        if thumbnail_bytes:
            thumb_media = MediaIoBaseUpload(BytesIO(thumbnail_bytes), mimetype="image/png")
            youtube.thumbnails().set(videoId=video_id, media_body=thumb_media).execute()

        return UploadResult(video_id=video_id, url=f"https://www.youtube.com/watch?v={video_id}")
