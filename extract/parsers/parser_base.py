from pathlib import Path
from abc import ABC, abstractmethod


class ParserBase(ABC):
    @abstractmethod
    async def parse(self, file_path: Path) -> str:
        raise NotImplementedError
