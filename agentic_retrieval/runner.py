import asyncio
import timeit
import logging
from pathlib import Path
from typing import Any, Dict
import os
from langchain_core.runnables import RunnableConfig
from agentic_retrieval.agentic_workflow import graph
from configuration import Configuration, SearchAPI
from utils import get_config_value
from dataset import load_raw_questions, is_bad_report
from persistence import (
    load_existing_from_json,
    rewrite_ndjson,
    append_ndjson_line,
    consolidate_ndjson_to_json,
)

conf = Configuration()

# build RunnableConfig from the configuration instance
config = RunnableConfig(
    recursion_limit=conf.recursion_limit,
    configurable={
        "search_api": conf.search_api,
        "number_of_queries": conf.number_of_queries,
        "max_search_depth": conf.max_search_depth,
    },
)

logger = logging.getLogger(__name__)

async def run_agenticRAG(question: str, options: str, config: RunnableConfig) -> str:
    initial_state = {"messages": [{"role": "user", "content": question}], "sections": [], "completed_sections": []}
    result: Any = await graph.ainvoke(initial_state, config=config)
    report = result.get("final_report") or result.get("content") if isinstance(result, dict) else result
    return report or "[ERROR] No report found"

async def process(
    all_questions_path: Path,
    ndjson_path: Path,
    json_path: Path,
):
    all_questions = load_raw_questions(all_questions_path)
    existing = load_existing_from_json(json_path)
    to_run = [q for q in all_questions if not isinstance(q.get("question_id"), int)
              or is_bad_report(existing.get(q["question_id"], {}).get("report", ""))]
    logger.info("Will run research on %d/%d questions", len(to_run), len(all_questions))
    start_global = timeit.default_timer()

    out_ndjson = ndjson_path.open("a+", encoding="utf-8")
    out_ndjson.seek(0, os.SEEK_END)
    try:
        for item in to_run:
            qid = item.get("question_id")
            question = item.get("summary", "")
            opts_str = "\n".join(f"{k}: {v}" for k, v in item.get("options", {}).items())
            report = await run_agenticRAG(question, opts_str, config=config)
            item["report"] = report
            print("Report for question_id", qid, ":", report)  
            prev = existing.get(qid)
            existing[qid] = item
            if prev is None:
                append_ndjson_line(out_ndjson, item)
            elif is_bad_report(prev.get("report", "")):
                out_ndjson.close()
                rewrite_ndjson(ndjson_path, list(existing.values()))
                out_ndjson = ndjson_path.open("a+", encoding="utf-8")
                out_ndjson.seek(0, os.SEEK_END)
    finally:
        out_ndjson.close()
        rewrite_ndjson(ndjson_path, list(existing.values()))
        consolidate_ndjson_to_json(ndjson_path, json_path)
        total = timeit.default_timer() - start_global
        logger.info("Done: %d questions in %.2f sec", len(to_run), total)