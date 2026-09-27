from langchain_google_genai import ChatGoogleGenerativeAI
from functools import cached_property
import dagster as dg

_LLM_NAME = "gemini-3.8-flash"
_TEMPERATURE = 1.0
_MAX_RETRIES = 3


class LLM(dg.ConfigurableResource):
    """Shared Gemini chat model for structured extraction.

    ``client`` is a LangChain chat model created on first use and reused.
    """

    model: str = _LLM_NAME
    temperature: float = _TEMPERATURE
    max_retries: int = _MAX_RETRIES

    @cached_property
    def client(self) -> ChatGoogleGenerativeAI:
        """Lazy initialize LangChain Chat Model"""
        return ChatGoogleGenerativeAI(
            model=self.model,
            temperature=self.temperature,
            max_retries=self.max_retries,
        )
