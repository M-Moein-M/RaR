import json
from pathlib import Path
from typing import Dict, Any, List
import logging

logger = logging.getLogger(__name__)

def load_existing_from_json(json_path: Path) -> Dict[int, Dict[str, Any]]:
    existing: Dict[int, Dict[str, Any]] = {}
    if not json_path.exists():
        return existing
    try:
        with json_path.open("r", encoding="utf-8") as f:
            objs = json.load(f)
        for obj in objs:
            qid = obj.get("question_id")
            if isinstance(qid, int):
                existing[qid] = obj
    except Exception as e:
        logger.warning("Failed to load consolidated JSON (%s): %s", json_path, e)
    return existing


def rewrite_ndjson(output_file: Path, records: List[Dict[str, Any]]) -> None:
    with output_file.open("w", encoding="utf-8") as out_f:
        for rec in sorted(records, key=lambda r: r.get("question_id", 0)):
            out_f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def append_ndjson_line(output_file_handle, record: Dict[str, Any]) -> None:
    output_file_handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    output_file_handle.flush()


def consolidate_ndjson_to_json(ndjson_path: Path, json_path: Path) -> None:
    all_records = []
    with ndjson_path.open("r", encoding="utf-8") as in_f:
        for line in in_f:
            line = line.strip()
            if not line:
                continue
            try:
                all_records.append(json.loads(line))
            except json.JSONDecodeError:
                logger.warning("Skipping invalid NDJSON line during consolidation: %s", line)
    with json_path.open("w", encoding="utf-8") as out_json:
        json.dump(all_records, out_json, ensure_ascii=False, indent=2)