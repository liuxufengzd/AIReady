import asyncio
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.graph import Graph
from data.context import Context
from dotenv import load_dotenv
import uuid

load_dotenv()


async def main():
    project = "demo"
    # parse "store/s3/raw/demo/" folder and process each file
    sources = (
        list(Path("store/s3/raw/demo/").glob("*.pptx"))
        + list(Path("store/s3/raw/demo/").glob("*.pdf"))
        + list(Path("store/s3/raw/demo/").glob("*.png"))
    )
    for source in sources:
        sink = Path("store/s3/processed/demo") / source.name
        input = {
            "use_vlm": True,
        }
        context = Context(project, source, sink)

        await (
            Graph()
            .build()
            .ainvoke(
                input,
                {"configurable": {"thread_id": str(uuid.uuid4())}},
                context=context,
            )
        )


asyncio.run(main())
