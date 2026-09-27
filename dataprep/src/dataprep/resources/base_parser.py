from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path


class BaseParser(ABC):
    """Parse a document file into text.

    Implementations may report progress through the optional logger callback.
    """

    @abstractmethod
    async def parse_async(
        self, source: Path, logger: Callable[[str], None] | None = None
    ) -> str:
        """Parse the document file and return the text content.

        Args:
            source: The path to the document file.
            logger: The callback function to log the progress.
        Returns:
            The text content of the document.
        """
        raise NotImplementedError("Subclasses must implement this method.")
