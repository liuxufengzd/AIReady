import asyncio
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.extract_graph import ExtractGraph
from core.extract_context import ExtractContext
from dotenv import load_dotenv
from core.entity.matadata import IHIMachineReport
import uuid

load_dotenv()


async def main():
    thread_id = str(uuid.uuid4())
    project = "IHI"
    source = Path(
        "store/s3/raw/IHI/2023-102 (株)ベローズ久世 本社工場 前扉開閉不備.pdf"
    )
    sink = Path(
        "store/s3/processed/IHI/2023-102 (株)ベローズ久世 本社工場 前扉開閉不備.pdf"
    )
    graph = ExtractGraph().build()
    await graph.ainvoke(
        {"use_vlm": True},
        {"configurable": {"thread_id": thread_id}},
        context=ExtractContext(project, source, sink, IHIMachineReport),
    )


asyncio.run(main())
