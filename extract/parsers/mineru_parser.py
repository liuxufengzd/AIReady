import os
import shutil
import tempfile
from pathlib import Path

from mineru.parser import MinerUApiParser

from parsers.parser_base import ParserBase


class MinerUParser(ParserBase):
    def __init__(
        self,
        tier: str = "standard",
    ):
        self._parser = MinerUApiParser(
            api_url=os.environ["MINERU_API_URL"],
            tier=tier,
        )

    async def parse(self, file_path: Path) -> str:
        file_path = Path(file_path)
        with tempfile.TemporaryDirectory(prefix="mineru-parse-") as tmp:
            result = await self._parser.parse_async(_safe_path(file_path, Path(tmp)))
        return result.markdown()


def _safe_path(source: Path, tmp_dir: Path) -> Path:
    # Windows cannot create directories whose names end with spaces or dots.
    sanitized = source.stem.rstrip(" .") or source.stem
    if sanitized == source.stem:
        return source
    target = tmp_dir / f"{sanitized}{source.suffix}"
    shutil.copy2(source, target)
    return target
