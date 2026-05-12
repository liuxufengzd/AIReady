import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from searcher.importer import Importer
import asyncio
from dotenv import load_dotenv
from searcher.retriever import Retriever
from searcher.common import const

load_dotenv()


async def main():
    project = "demo"
    metadata_file_names = [
        path.name for path in Path("store/nosql/demo/").glob("*.json")
    ]
    async with Importer(project) as importer:
        await importer.batch(metadata_file_names)

    query = "整理编号为“2019-096”的任务中，这份报告由谁负责作成（作成者），最终由谁进行审批（承認）？"
    async with Retriever(const.DATABASE, project) as retriever:
        results = await retriever.query(query)
        for result in results:
            print(result.metadata)
            print("-" * 100)


asyncio.run(main())
