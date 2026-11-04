import asyncio
import logging
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

sys.path.append(str(Path(__file__).resolve().parent.parent))

from evaluator import Evaluator

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    load_dotenv(Path(__file__).parent.parent / ".env")

    config_path = Path(__file__).parent / "configuration.yml"
    with config_path.open(encoding="utf-8") as f:
        configuration = yaml.safe_load(f)

    asyncio.run(Evaluator(configuration).evaluate())
