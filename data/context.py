from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel
from typing import Type


@dataclass
class Context:
    project: str
    source: Path
    sink: Path
    meta_schema: Type[BaseModel] = field(default=None)
    languages: list[str] = field(default_factory=lambda: ["en", "ja"])
