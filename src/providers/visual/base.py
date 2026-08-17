from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class VisualResult:
    url: str
    photographer: str
    source: str
    license_type: str


class VisualProvider(ABC):
    @abstractmethod
    async def search(self, query: str, count: int = 1) -> list[VisualResult]:
        raise NotImplementedError
