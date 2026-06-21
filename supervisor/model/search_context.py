from dataclasses import dataclass, field


@dataclass(frozen=True)
class SearchContext:
    project: str = field()
    thread_id: str | None = field(default=None)
    filters: dict[str, str] | None = field(default=None)
