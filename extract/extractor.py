import asyncio
import json
import logging
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel

import schemas
from parsers.parser_base import ParserBase
from prompts import SYSTEM_PROMPT

logger = logging.getLogger(__name__)


def _create_parser(configuration: dict) -> ParserBase:
    options = dict(configuration)
    parser_type = options.pop("type")
    if parser_type == "llamaparse":
        from parsers.llamaparse_parser import LlamaParseParser

        return LlamaParseParser(**options)
    if parser_type == "mineru":
        from parsers.mineru_parser import MinerUParser

        return MinerUParser(**options)
    if parser_type == "xberg":
        from parsers.xberg_parser import XbergParser

        return XbergParser(**options)
    raise ValueError(f"Unknown parser type: {parser_type}")


def _resolve_schema(name: str) -> type[BaseModel]:
    schema_cls = getattr(schemas, name, None)
    if not (isinstance(schema_cls, type) and issubclass(schema_cls, BaseModel)):
        raise ValueError(f"Unknown schema: {name}")
    return schema_cls


class Extractor:
    def __init__(self, configuration: dict):
        self.configuration = configuration

        source = configuration["source"]
        self.source_folder = Path(source["folder"])
        self.source_pattern = source.get("pattern", "*")

        extractor = configuration["extractor"]
        self.parser = _create_parser(extractor["parser"])
        self.schema = _resolve_schema(extractor["schema"])
        self.llm = ChatGoogleGenerativeAI(
            model=extractor["model"]
        ).with_structured_output(self.schema)

        self.output_folder = Path(configuration["output"]["folder"])

    async def extract(self) -> dict[Path, BaseModel]:
        files = sorted(self.source_folder.glob(self.source_pattern))
        if not files:
            logger.warning(
                "No files matching %s in %s", self.source_pattern, self.source_folder
            )
            return {}

        self.output_folder.mkdir(parents=True, exist_ok=True)
        results = await asyncio.gather(
            *(self._extract_file(file) for file in files), return_exceptions=True
        )

        extracted = {}
        for file, result in zip(files, results):
            if isinstance(result, BaseException):
                logger.error("Failed to extract %s: %s", file.name, result)
            else:
                extracted[file] = result
        return extracted

    async def _extract_file(self, file_path: Path) -> BaseModel:
        logger.info("Parsing %s", file_path.name)
        markdown = await self.parser.parse(file_path)
        (self.output_folder / f"{file_path.stem}.md").write_text(
            markdown, encoding="utf-8"
        )

        logger.info("Extracting %s", file_path.name)
        result = await self.llm.ainvoke(
            [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=markdown)]
        )
        (self.output_folder / f"{file_path.stem}.json").write_text(
            json.dumps(
                result.model_dump(mode="json", by_alias=True),
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        logger.info("Saved %s", file_path.stem)
        return result
