import dagster as dg
import os

from dataprep.resources.local_doc_store import LocalDocStore
from dataprep.resources.llm import LLM
from dataprep.resources.image_extractor import ImageExtractor
from dataprep.resources.mineru_parser import MinerUParser
from dataprep.resources.office_converter import OfficeConverter
from dataprep.resources.vlm_extractor import VLMExtractor


@dg.definitions
def resources() -> dg.Definitions:
    llm = LLM()
    image_extractor = ImageExtractor(llm=llm)
    vlm_extractor = VLMExtractor(llm=llm)
    mineru_parser = MinerUParser(api_url=os.environ.get("MINERU_API_URL"))
    office_converter = OfficeConverter()

    return dg.Definitions(
        resources={
            "store": LocalDocStore(),
            "llm": llm,
            "image_extractor": image_extractor,
            "vlm_extractor": vlm_extractor,
            "mineru_parser": mineru_parser,
            "office_converter": office_converter,
        }
    )
