from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class UploadResult:
    video_id: str
    url: str


@dataclass
class VideoStats:
    view_count: int
    like_count: int
    comment_count: int


class YouTubeProvider(ABC):
    @abstractmethod
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
        raise NotImplementedError

    @abstractmethod
    async def get_video_stats(self, refresh_token: str, video_id: str) -> VideoStats:
        raise NotImplementedError
