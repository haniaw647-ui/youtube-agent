import asyncio
from io import BytesIO

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

from src.orchestrator.config import get_settings
from src.providers.youtube.base import UploadResult, YouTubeProvider

TOKEN_URI = "https://oauth2.googleapis.com/token"


class YouTubeAPIProvider(YouTubeProvider):
    """google-api-python-client is sync-only, so the actual work runs in a
    thread — resumable upload + credential refresh logic is exactly the kind
    of thing worth trusting to Google's own client rather than hand-rolling."""

    async def upload_video(
        self,
        refresh_token: str,
        video_bytes: bytes,
        title: str,
        description: str,
        tags: list[str],
        privacy_status: str,
        thumbnail_bytes: bytes | None = None,
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
    ) -> UploadResult:
        settings = get_settings()
        creds = Credentials(
            None,
            refresh_token=refresh_token,
            token_uri=TOKEN_URI,
            client_id=settings.youtube_oauth_client_id,
            client_secret=settings.youtube_oauth_client_secret,
        )
        youtube = build("youtube", "v3", credentials=creds)

        body = {
            "snippet": {"title": title, "description": description, "tags": tags},
            "status": {"privacyStatus": privacy_status},
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
