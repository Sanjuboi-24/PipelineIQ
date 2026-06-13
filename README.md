# PipelineIQ 🔍

**AI-powered data pipeline debugger.** Connects to your Snowflake warehouse, detects broken pipelines, explains root causes in plain English, and generates SQL/dbt fixes.

> Built as a portfolio project demonstrating AI engineering + data engineering — end to end.


---

## What it does

PipelineIQ runs an AI agent (Claude Sonnet 4 via LangGraph) against your Snowflake pipeline. The agent:

1. **Surveys** all tables across RAW → STAGING → MARTS
2. **Detects** anomalies: null spikes, stale tables, row fanouts, statistical outliers
3. **Traces** each anomaly to its root cause
4. **Generates** a concrete SQL or dbt fix
5. **Suggests** a dbt test to prevent recurrence

## Architecture

```
Streamlit UI → FastAPI (WebSocket) → LangGraph Agent → Claude Sonnet 4
                                           ↓
                              ┌─────────────────────────┐
                              │  Agent Tools             │
                              │  • schema_inspector      │
                              │  • anomaly_detector      │
                              │  • fix_generator         │
                              │  • lineage_tracer        │
                              └─────────────────────────┘
                                           ↓
                              Snowflake (RAW / STAGING / MARTS)
                                           ↓
                              TPC-H pipeline (8 tables, ~1GB)
```

## Demo pipeline

The project ships with a full TPC-H Scale Factor 1 pipeline loaded into Snowflake, with 4 intentionally injected failures:

| Failure | Description | Affected Model |
|---|---|---|
| `NULL_INJECTION` | 40% of `O_TOTALPRICE` set to NULL | `fct_orders` |
| `STALE_TABLE` | `CUSTOMER_STALE` never refreshed | `dim_customers` |
| `FANOUT_JOIN` | Many-to-many join causes 3x row duplication | `fct_orders_broken` |
| `CURRENCY_BUG` | 10% of prices multiplied by 100 | `mart_revenue` |

## Quickstart

```bash
git clone https://github.com/yourusername/pipelineiq
cd pipelineiq
cp .env.example .env       # fill in your credentials
pip install -r requirements.txt

# 1. Load TPC-H into Snowflake and inject failures (~15 min first run)
python -m pipeline.run_pipeline

# 2. Start the API
python -m api.main

# 3. Hit the health endpoint
curl http://localhost:8000/health
```

## Project structure

```
pipelineiq/
├── pipeline/           # Ingestion, failure injection, validation
├── dbt/                # Staging + mart models
├── api/                # FastAPI REST + WebSocket
├── agent/              # LangGraph graph + Claude tools
├── config/             # Settings + logging
└── evals/              # Eval dataset + metrics
```

## Tech stack

| Layer | Technology |
|---|---|
| AI | Claude Sonnet 4 (Anthropic) |
| Agent framework | LangGraph |
| Data warehouse | Snowflake |
| Transform | dbt-snowflake |
| API | FastAPI + WebSockets |
| Frontend | Streamlit |
| Observability | Langfuse + Prometheus |
| Deployment | Railway |

## Observability

Every agent run is traced in Langfuse: tool calls, token usage, latency, and accuracy scores against the eval dataset.

---

*Built by Sanjay Chimakurthy(https://www.linkedin.com/in/sanjay-chimakurthy-81523a2a1/) Demo Video: https://www.loom.com/share/4a1d1128278d43f4a2f787ee1b31fecf
