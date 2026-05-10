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
    project = "IHI"
    source = Path("store/s3/raw/IHI/sample_1.png")
    sink = Path("store/s3/processed/IHI/sample_1.png")
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
