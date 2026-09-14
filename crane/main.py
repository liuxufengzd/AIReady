import asyncio
from pathlib import Path
from processors.mineru_extractor import MineruExtractor
from processors.xberg_extractor import XbergExtractor
from dotenv import load_dotenv

load_dotenv()


def main() -> None:
    demo_dir = Path(__file__).resolve().parent

    input_path = demo_dir / "data" / "demo" / "300"
    # output_dir = demo_dir / "output" / "mineru" / "HyDE"
    output_dir = demo_dir / "output" / "xberg" / "内部基準" / "300"

    # asyncio.run(
    #     MineruExtractor().extract(
    #         input_path=input_path,
    #         output_dir=output_dir,
    #     )
    # )

    asyncio.run(
        XbergExtractor().extract(
            input_path=input_path,
            output_dir=output_dir,
        )
    )


if __name__ == "__main__":
    main()
