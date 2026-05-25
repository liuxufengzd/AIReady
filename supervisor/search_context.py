from dataclasses import dataclass, field


@dataclass(frozen=True)
class SearchContext:
    project: str = field()
    filters: dict[str, str] | None = field(default=None)
