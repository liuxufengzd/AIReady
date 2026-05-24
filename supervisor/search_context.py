from dataclasses import dataclass, field


@dataclass(frozen=True)
class SearchContext:
    project: str = field(description="The project identifier")
    filters: dict[str, str] | None = field(
        default=None, description="The filters to apply to the search"
    )
