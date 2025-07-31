from pathlib import Path
import json
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)


def load_raw_questions(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"Could not find {path!r}")
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"Expected a list of questions in {path}, got {type(data)}")
    return data


def is_bad_report(report: Optional[str]) -> bool:
    if not report:
        return True
    return report.startswith("[ERROR]") or report.startswith("### Sources")