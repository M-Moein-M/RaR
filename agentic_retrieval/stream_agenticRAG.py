import asyncio
from pathlib import Path
import logging

from runner import process

# configure logging here
logging.basicConfig(level=logging.INFO)

INPUT_FILE = Path("radiology_1.json")
BASENAME = "radiology_agenticRAG"
OUTPUT_FILE = Path(f"{BASENAME}.ndjson")
OUTPUT_JSON = Path(f"{BASENAME}.json")

if __name__ == "__main__":
    try:
        asyncio.run(process(INPUT_FILE, OUTPUT_FILE, OUTPUT_JSON))
        print(f"Done. Results in {OUTPUT_FILE!r} (JSON: {OUTPUT_JSON!r})")
    except KeyboardInterrupt:
        print(f"Interrupted—partial results in {OUTPUT_FILE}.")