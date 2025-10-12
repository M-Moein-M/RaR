# Multi-step retrieval and reasoning improves radiology question answering with large language models (RaR)

## Overview

This is the official repository of the paper **Multi-step retrieval and reasoning improves radiology question answering with large language model**.  
Preprint version: [https://arxiv.org/abs/2508.00743].

Radiology Retrieval and Reasoning (RaR) is an open-source, multi-agent retrieval-augmented generation (RAG) pipeline designed for evidence-grounded radiology question answering. It orchestrates a **supervisor–researcher** workflow where the supervisor decomposes a clinical question into diagnostic sections, assigns each to a research agent, and then synthesizes an unbiased, structured report. Retrieval is iterative and targeted, improving factual grounding and diagnostic accuracy over conventional single-step RAG. This repository contains the implementation, tooling, and orchestration for the pipeline described in the accompanying paper.

Briefly, the system:
- Decomposes radiology questions into diagnostic options.
- Assigns each option to a research agent that iteratively retrieves evidence (from Radiopaedia.org) via a locally hosted SearXNG instance.
- Supervises and composes evidence into introduction, body sections, and conclusion with neutrality.
- Persists intermediate and final reports safely to support resumption and evaluation.

## Key Features

- **Supervisor–Researcher Multi-Agent Graph**: Coordinated via a stateful directed graph (`agentic_workflow.py`) using LangGraph.  
- **Iterative, Agentic Retrieval**: Each research agent refines search queries to gather clinically relevant evidence.  
- **Structured Report Generation**: Tools for planning sections, writing introductions/conclusions, and composing final reports.  
- **Configurable Models**: Plug in different LLMs (OpenAI, Groq, Ollama, custom gateways) with support for function/tool calling.  
- **Robust Persistence**: Incremental NDJSON streaming with consolidation to avoid recomputation on interruptions.

![Workflow overview](./figure.png)  
*Figure 1: RaR pipeline overview.*

## Quickstart

### 1. Clone and install

```bash
git clone https://github.com/sopajeta/RaR.git
cd agentic-retrieval
uv venv # On Windows: python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -e 
```

### 2. Environment

Copy and customize the configuration:

```bash
cp .env.example .env
```

#### Required (pick **only one** backend)
- `OPENAI_API_KEY` – for OpenAI models.  
- `GROQ_API_KEY` – for Groq models. Optionally set `GROQ_BASE_URL` to override the default endpoint.  
- `OLLAMA_BASE_URL` – for a local Ollama model server.  
- `CUSTOM_API_URL` / `CUSTOM_BASE_URL` / `BASE_URL` – for a custom OpenAI-compatible gateway; set the appropriate endpoint.  

#### Optional overrides
- `SUPERVISOR_MODEL`, `RESEARCHER_MODEL` – model strings, e.g., `openai:gpt-4.1-mini`.  
- `SEARXNG_HOST` – URL for your SearXNG instance (defaults to `http://localhost:8080`).  
- `SEARXNG_ALLOWED_DOMAINS` – comma-separated allowed domains (defaults to `radiopaedia.org`).

### 3. Run SearXNG (search backend)

The pipeline relies on a locally hosted SearXNG instance for retrieval. See the **official SearXNG Docker installation & launch instructions** for details: https://docs.searxng.org/admin/installation-docker.html 

Make sure `SEARXNG_HOST` points to the running instance (e.g., `http://localhost:8080`).

### 4. Launch the agentic RAG workflow

The entrypoint script for batch processing is `stream_agenticRAG.py`. The workflow expects a JSON input file (e.g., `dataset_example.json`) containing a list of questions. Each entry must include:

- `question_id`: a unique identifier.  
- `summary`: the clinical question or prompt summary.  
- `options`: an object/dictionary of **exactly four** diagnostic choices (e.g., `"A"`, `"B"`, `"C"`, `"D"`).

**Note:** This system is designed for **multiple-choice questions (MCQs)** with exactly four options; the pipeline allocates one research agent per option.

Minimal example:

```json
[
  {
    "question_id": 123,
    "summary": "A 65-year-old male with chest pain and shortness of breath.",
    "options": {
      "A": "Myocardial infarction",
      "B": "Pulmonary embolism",
      "C": "Pneumonia",
      "D": "Aortic dissection"
    }
  }
]
```

Run:

```bash
python stream_agenticRAG.py
```

### 5. Interactive LangGraph Studio / Chat Interface

To inspect and drive the multi-agent graph interactively:

```bash
uvx --refresh --from "langgraph-cli[inmem]" --with-editable .  # On Window / Linux: pip install -U "langgraph-cli[inmem]" 
--python 3.11 langgraph dev --allow-blocking   # On Window / Linux: langgraph dev
```

This starts the server at `http://127.0.0.1:2024`. Access:
- Studio UI / chat: `https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:2024`  
- API: `http://127.0.0.1:2024`  
- OpenAPI docs: `http://127.0.0.1:2024/docs`

### 6. Custom invocation / configuration

The core graph lives in `agentic_workflow.py`. The runner coordinating question batches is `runner.py`. You can modify:
- Input question source.  
- Supervisor/researcher model selection via environment or runtime overrides.  
- Retrieval behavior (number of queries, depth) via `Configuration` / `RunnableConfig`.

## Search Tool

Search is implemented in `utils.py`:

- `searxng_search` performs asynchronous retrieval through the SearXNG instance, applies domain filtering (defaulting to Radiopaedia.org), deduplicates results, and formats them for LLM consumption.  
- Results are integrated into the multi-agent pipeline as structured evidence bundles.

## Multi-Agent Architecture

Implemented in `agentic_workflow.py`:
- **Supervisor Agent**: Plans report sections, triggers introduction/conclusion generation, and coordinates research agents.  
- **Research Agents**: Explore individual diagnostic options, gather evidence iteratively, and produce section content.  
- State transitions and continuation logic are managed with `StateGraph` and conditional edges.

## File Overview

- `agentic_workflow.py` – Core multi-agent graph and tool orchestration.  
- `runner.py` – Drives batch processing and report persistence.  
- `stream_agenticRAG.py` – CLI wrapper for executing question sets.  
- `configuration.py` – Central config merging environment variables and overrides.  
- `utils.py` – Search/model helpers and formatting logic.  
- `dataset.py` – Question loading and filtering.  
- `persistence.py` – NDJSON handling and consolidation.  
- `langgraph.json` – Graph manifest / entrypoint.

## Model Integration

Supported model prefixes (via `utils.init_chat_model`):
- `openai:` – OpenAI (requires `OPENAI_API_KEY`).  
- `groq:` – Groq (requires `GROQ_API_KEY`; optional `GROQ_BASE_URL`).  
- `ollama:` – Ollama local servers (via `OLLAMA_BASE_URL`).  
- `custom:` – OpenAI-compatible custom gateway (`CUSTOM_API_URL` / `CUSTOM_BASE_URL`).  

Ensure the selected models support tool/function calling; structured output generation depends on it.

This work builds on the LangChain **Open Deep Research** pipeline, leveraging its multi-agent/state-graph orchestration as a foundation and adapting it for multi-step retrieval and reasoning in radiology QA. Original project: https://github.com/langchain-ai/deep-research

## Citation

If you use this repository, please also cite the related work:

**AgenticRAG**  
```bibtex
@misc{wind2025agenticlargelanguagemodels,
      title={Agentic large language models improve retrieval-based radiology question answering}, 
      author={Sebastian Wind and Jeta Sopa and Daniel Truhn and Mahshad Lotfinia and Tri-Thien Nguyen and Keno Bressem and Lisa Adams and Mirabela Rusu and Harald Köstler and Gerhard Wellein and Andreas Maier and Soroosh Tayebi Arasteh},
      year={2025},
      eprint={2508.00743},
      archivePrefix={arXiv},
      primaryClass={cs.CL},
      url={https://arxiv.org/abs/2508.00743}, 
}
```

## License

MIT License. See `LICENSE` for details.
