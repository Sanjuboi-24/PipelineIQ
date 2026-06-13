"""
app/streamlit_app.py
---------------------
PipelineIQ — Full Streamlit UI.

Pages:
  1. Pipeline Explorer  — browse tables, schemas, freshness, null stats
  2. AI Debugger        — chat with the agent, see live tool calls
  3. Findings Dashboard — structured anomaly report with metrics
  4. Observability      — token usage, latency, cost per run
"""

import time
import json
import streamlit as st
import plotly.graph_objects as go
import plotly.express as px
import pandas as pd
import requests

# ── Page config ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="PipelineIQ",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── API base URL (env-configurable for Railway deploy) ─────────────────────────
import os
API_BASE = os.getenv("API_BASE_URL", "http://localhost:8000")

# ── Styles ─────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
    .main-header {
        font-size: 2rem;
        font-weight: 700;
        color: #00D4FF;
        margin-bottom: 0;
    }
    .sub-header {
        color: #888;
        font-size: 0.95rem;
        margin-top: 0;
    }
    .metric-card {
        background: #1a1a2e;
        border: 1px solid #2d2d4e;
        border-radius: 8px;
        padding: 1rem;
        text-align: center;
    }
    .finding-critical {
        border-left: 4px solid #ff4444;
        padding-left: 1rem;
        margin: 0.5rem 0;
    }
    .finding-warning {
        border-left: 4px solid #ffaa00;
        padding-left: 1rem;
        margin: 0.5rem 0;
    }
    .tool-call-box {
        background: #0d1117;
        border: 1px solid #30363d;
        border-radius: 6px;
        padding: 0.75rem;
        font-family: monospace;
        font-size: 0.8rem;
        margin: 0.25rem 0;
    }
    .stButton > button {
        background: linear-gradient(135deg, #00D4FF, #0066FF);
        color: white;
        border: none;
        border-radius: 6px;
        font-weight: 600;
    }
</style>
""", unsafe_allow_html=True)


# ── Helpers ────────────────────────────────────────────────────────────────────

def api_get(path: str) -> dict | None:
    try:
        r = requests.get(f"{API_BASE}{path}", timeout=30)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.ConnectionError:
        st.error(f"Cannot reach API at {API_BASE}. Is the FastAPI server running?")
        return None
    except Exception as e:
        st.error(f"API error: {e}")
        return None


def api_post(path: str, payload: dict) -> dict | None:
    try:
        r = requests.post(f"{API_BASE}{path}", json=payload, timeout=180)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.ConnectionError:
        st.error(f"Cannot reach API at {API_BASE}. Is the FastAPI server running?")
        return None
    except Exception as e:
        st.error(f"API error: {e}")
        return None


def severity_badge(severity: str) -> str:
    colors = {"CRITICAL": "#ff4444", "WARNING": "#ffaa00", "OK": "#44ff88"}
    color = colors.get(severity, "#888")
    return f'<span style="background:{color};color:#000;padding:2px 8px;border-radius:4px;font-size:0.75rem;font-weight:700">{severity}</span>'


# ── Sidebar ────────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("## 🔍 PipelineIQ")
    st.markdown("*AI-powered pipeline debugger*")
    st.divider()

    page = st.radio(
        "Navigation",
        ["🏠 Overview", "🗄️ Pipeline Explorer", "🤖 AI Debugger", "📊 Findings", "📈 Observability"],
        label_visibility="collapsed",
    )

    st.divider()

    # API health indicator
    health = api_get("/health")
    if health and health.get("status") == "ok":
        st.success("API ✅ Online")
    else:
        st.error("API ❌ Offline")

    st.caption(f"API: `{API_BASE}`")


# ── Page: Overview ─────────────────────────────────────────────────────────────

if page == "🏠 Overview":
    st.markdown('<p class="main-header">PipelineIQ 🔍</p>', unsafe_allow_html=True)
    st.markdown('<p class="sub-header">AI-powered data pipeline debugger — built on Snowflake + Claude + LangGraph</p>', unsafe_allow_html=True)
    st.divider()

    col1, col2, col3, col4 = st.columns(4)

    # Quick stats from API
    raw_tables = api_get("/pipeline/tables?schema=RAW")
    table_count = len(raw_tables.get("tables", [])) if raw_tables else 0
    total_rows = sum(t.get("ROW_COUNT", 0) or 0 for t in (raw_tables.get("tables", []) if raw_tables else []))

    with col1:
        st.metric("RAW Tables", table_count)
    with col2:
        st.metric("Total Rows", f"{total_rows:,.0f}")
    with col3:
        st.metric("dbt Models", "5")
    with col4:
        st.metric("Injected Failures", "4")

    st.divider()

    col_left, col_right = st.columns([3, 2])

    with col_left:
        st.subheader("Pipeline Architecture")
        st.markdown("""
        ```
        TPC-H Source (DuckDB SF=1, ~1GB)
             │
             ▼
        ┌─────────────────────┐
        │   Snowflake RAW     │  ← 8 tables, ~8M rows
        │   ORDERS LINEITEM   │
        │   CUSTOMER SUPPLIER │
        └─────────────────────┘
             │  dbt run
             ▼
        ┌─────────────────────┐
        │  Snowflake STAGING  │  ← stg_orders, stg_customers
        │  (views)            │    stg_lineitems
        └─────────────────────┘
             │  dbt run
             ▼
        ┌─────────────────────┐
        │   Snowflake MARTS   │  ← fct_orders, mart_revenue
        │   (tables)          │
        └─────────────────────┘
             │
             ▼
        PipelineIQ AI Agent  →  Diagnose + Fix
        ```
        """)

    with col_right:
        st.subheader("Injected Failures")
        failures = [
            ("NULL_INJECTION", "CRITICAL", "40% nulls in O_TOTALPRICE"),
            ("STALE_TABLE", "WARNING", "CUSTOMER_STALE never refreshed"),
            ("FANOUT_JOIN", "CRITICAL", "3x row duplication in mart"),
            ("CURRENCY_BUG", "CRITICAL", "10x price inflation in LINEITEM"),
        ]
        for name, severity, desc in failures:
            color = "#ff4444" if severity == "CRITICAL" else "#ffaa00"
            st.markdown(f"""
            <div style="border-left:3px solid {color};padding:6px 12px;margin:6px 0;background:#0d1117;border-radius:0 4px 4px 0">
                <strong>{name}</strong><br>
                <span style="color:#888;font-size:0.85rem">{desc}</span>
            </div>
            """, unsafe_allow_html=True)

        st.divider()
        if st.button("🤖 Run AI Diagnosis Now", use_container_width=True):
            st.session_state["run_agent"] = True
            st.switch_page = "🤖 AI Debugger"
            st.info("Go to AI Debugger to see results →")


# ── Page: Pipeline Explorer ────────────────────────────────────────────────────

elif page == "🗄️ Pipeline Explorer":
    st.header("🗄️ Pipeline Explorer")

    schema = st.selectbox("Schema", ["RAW", "STAGING", "MARTS"])
    data = api_get(f"/pipeline/tables?schema={schema}")

    if data and data.get("tables"):
        tables = data["tables"]

        # Summary row
        total_rows = sum(t.get("ROW_COUNT", 0) or 0 for t in tables)
        total_bytes = sum(t.get("BYTES", 0) or 0 for t in tables)
        col1, col2, col3 = st.columns(3)
        col1.metric("Tables", len(tables))
        col2.metric("Total Rows", f"{total_rows:,}")
        col3.metric("Total Size", f"{total_bytes / 1024 / 1024:.1f} MB")

        st.divider()

        # Table selector
        table_names = [t["TABLE_NAME"] for t in tables]
        selected = st.selectbox("Select table to inspect", table_names)

        tab1, tab2, tab3 = st.tabs(["📋 Columns", "🔢 Null Stats", "⏱️ Freshness"])

        with tab1:
            col_data = api_get(f"/pipeline/tables/{selected}/columns?schema={schema}")
            if col_data and col_data.get("columns"):
                df = pd.DataFrame(col_data["columns"])
                st.dataframe(df, use_container_width=True)

        with tab2:
            null_data = api_get(f"/pipeline/tables/{selected}/nulls?schema={schema}")
            if null_data and null_data.get("null_stats"):
                null_df = pd.DataFrame(null_data["null_stats"])
                if not null_df.empty:
                    fig = px.bar(
                        null_df,
                        x="column",
                        y="null_pct",
                        title=f"Null % by column — {schema}.{selected}",
                        color="null_pct",
                        color_continuous_scale=["#44ff88", "#ffaa00", "#ff4444"],
                        range_color=[0, 50],
                    )
                    fig.update_layout(
                        plot_bgcolor="#0d1117",
                        paper_bgcolor="#0d1117",
                        font_color="#ccc",
                    )
                    st.plotly_chart(fig, use_container_width=True)

                    # Highlight problem columns
                    high_null = null_df[null_df["null_pct"] > 5]
                    if not high_null.empty:
                        st.warning(f"⚠️ {len(high_null)} column(s) with >5% nulls detected")
                        st.dataframe(high_null, use_container_width=True)
                    else:
                        st.success("✅ No null anomalies detected")

        with tab3:
            fresh_data = api_get(f"/pipeline/tables/{selected}/freshness?schema={schema}")
            if fresh_data and fresh_data.get("freshness"):
                f = fresh_data["freshness"]
                hours = f.get("HOURS_STALE", 0) or 0
                last_altered = f.get("LAST_ALTERED", "unknown")

                col1, col2 = st.columns(2)
                col1.metric("Hours since refresh", f"{hours:.1f}h")
                col2.metric("Last altered", str(last_altered)[:19])

                if hours > 48:
                    st.error(f"🔴 CRITICAL: Table is {hours:.0f}h stale")
                elif hours > 24:
                    st.warning(f"🟡 WARNING: Table is {hours:.0f}h stale")
                else:
                    st.success(f"✅ Table is fresh ({hours:.1f}h old)")
    else:
        st.info(f"No tables found in {schema} schema. Run the pipeline first.")


# ── Page: AI Debugger ──────────────────────────────────────────────────────────

elif page == "🤖 AI Debugger":
    st.header("🤖 AI Pipeline Debugger")
    st.caption("Ask the agent to investigate your pipeline. It will call tools autonomously to find and fix issues.")

    # Question input
    question = st.text_area(
        "Question",
        value="Investigate this data pipeline. Find all anomalies, diagnose root causes, and generate SQL fixes.",
        height=80,
    )

    col1, col2 = st.columns([1, 4])
    with col1:
        run_btn = st.button("🔍 Run Agent", use_container_width=True, type="primary")

    if run_btn or st.session_state.get("run_agent"):
        st.session_state["run_agent"] = False

        with st.spinner("Agent running... (30-90 seconds depending on findings)"):
            start = time.time()
            result = api_post("/debug/run", {"question": question})
            elapsed = round(time.time() - start, 1)

        if result:
            # Metrics bar
            st.divider()
            m1, m2, m3, m4 = st.columns(4)
            metrics = result.get("metrics", {})
            m1.metric("Tool calls", result.get("tool_calls_made", 0))
            m2.metric("Time", f"{result.get('elapsed_seconds', elapsed)}s")
            m3.metric("Findings", len(result.get("findings", [])))
            m4.metric("Est. cost", f"${metrics.get('estimated_cost_usd', 0):.4f}")

            st.divider()

            # Main diagnosis
            st.subheader("Diagnosis")
            st.markdown(result.get("answer", "No answer returned."))

            # Structured findings
            findings = result.get("findings", [])
            if findings:
                st.divider()
                st.subheader(f"Structured Findings ({len(findings)})")
                for i, finding in enumerate(findings):
                    r = finding.get("result", {})
                    severity = r.get("severity", "WARNING")
                    anomaly = r.get("anomaly_type", finding.get("tool", ""))
                    color = "#ff4444" if severity == "CRITICAL" else "#ffaa00"

                    with st.expander(f"{'🔴' if severity == 'CRITICAL' else '🟡'} {anomaly} — {r.get('table', '')}"):
                        st.markdown(f"**Severity:** {severity}")
                        st.markdown(f"**Tool:** `{finding.get('tool')}`")
                        if r.get("diagnosis"):
                            st.markdown(f"**Diagnosis:** {r['diagnosis']}")
                        if r.get("root_cause"):
                            st.markdown(f"**Root cause:** {r['root_cause']}")
                        st.json(r)

            # Token metrics if available
            if metrics:
                st.divider()
                st.caption(
                    f"Tokens: {metrics.get('total_input_tokens', 0):,} input + "
                    f"{metrics.get('total_output_tokens', 0):,} output = "
                    f"{metrics.get('total_tokens', 0):,} total"
                )

    # Chat history (simple session state)
    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []


# ── Page: Findings ─────────────────────────────────────────────────────────────

elif page == "📊 Findings":
    st.header("📊 Findings Dashboard")
    st.caption("Summary of all known pipeline failures in this demo environment.")

    # Static findings based on what we injected
    findings_data = [
        {
            "failure": "NULL_INJECTION",
            "table": "RAW.ORDERS",
            "column": "O_TOTALPRICE",
            "severity": "CRITICAL",
            "null_pct": 40.0,
            "affected_rows": 600000,
            "description": "40% of order prices are NULL, breaking all revenue calculations",
        },
        {
            "failure": "STALE_TABLE",
            "table": "RAW.CUSTOMER_STALE",
            "column": "—",
            "severity": "WARNING",
            "null_pct": 0,
            "affected_rows": 10000,
            "description": "Table created but never refreshed — simulates a broken pipeline job",
        },
        {
            "failure": "FANOUT_JOIN",
            "table": "MARTS.FCT_ORDERS_BROKEN",
            "column": "O_ORDERKEY",
            "severity": "CRITICAL",
            "null_pct": 0,
            "affected_rows": 4500000,
            "description": "Many-to-many join causes 3x row duplication, inflating all SUM() metrics",
        },
        {
            "failure": "CURRENCY_BUG",
            "table": "RAW.LINEITEM",
            "column": "L_EXTENDEDPRICE",
            "severity": "CRITICAL",
            "null_pct": 0,
            "affected_rows": 600000,
            "description": "10% of prices multiplied by 100x — revenue appears inflated by ~10x",
        },
    ]

    # Summary metrics
    c1, c2, c3 = st.columns(3)
    c1.metric("Total failures", len(findings_data))
    c2.metric("Critical", sum(1 for f in findings_data if f["severity"] == "CRITICAL"))
    c3.metric("Warnings", sum(1 for f in findings_data if f["severity"] == "WARNING"))

    st.divider()

    # Severity breakdown chart
    col1, col2 = st.columns(2)

    with col1:
        severity_counts = {"CRITICAL": 3, "WARNING": 1}
        fig = go.Figure(go.Pie(
            labels=list(severity_counts.keys()),
            values=list(severity_counts.values()),
            marker_colors=["#ff4444", "#ffaa00"],
            hole=0.4,
        ))
        fig.update_layout(
            title="Findings by Severity",
            plot_bgcolor="#0d1117",
            paper_bgcolor="#0d1117",
            font_color="#ccc",
        )
        st.plotly_chart(fig, use_container_width=True)

    with col2:
        # Affected rows by failure
        df = pd.DataFrame(findings_data)
        fig2 = px.bar(
            df,
            x="failure",
            y="affected_rows",
            color="severity",
            color_discrete_map={"CRITICAL": "#ff4444", "WARNING": "#ffaa00"},
            title="Affected Rows by Failure",
        )
        fig2.update_layout(
            plot_bgcolor="#0d1117",
            paper_bgcolor="#0d1117",
            font_color="#ccc",
        )
        st.plotly_chart(fig2, use_container_width=True)

    st.divider()
    st.subheader("Failure Details")

    for f in findings_data:
        color = "#ff4444" if f["severity"] == "CRITICAL" else "#ffaa00"
        icon = "🔴" if f["severity"] == "CRITICAL" else "🟡"
        with st.expander(f"{icon} {f['failure']} — {f['table']}"):
            col1, col2, col3 = st.columns(3)
            col1.metric("Severity", f["severity"])
            col2.metric("Affected Rows", f"{f['affected_rows']:,}")
            col3.metric("Column", f["column"])
            st.markdown(f"**Description:** {f['description']}")


# ── Page: Observability ────────────────────────────────────────────────────────

elif page == "📈 Observability":
    st.header("📈 Observability")
    st.caption("Token usage, latency, and cost metrics from agent runs.")

    st.info("Run the AI Debugger first to populate metrics. Langfuse traces appear at cloud.langfuse.com if configured.")

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("What is traced")
        st.markdown("""
        Every agent run captures:
        - **Total tokens** — input + output per LLM call
        - **Tool call latency** — ms per tool execution
        - **Cost estimate** — USD based on Claude pricing
        - **Findings count** — anomalies detected per run
        - **End-to-end latency** — wall clock time

        Traces are sent to **Langfuse** if `LANGFUSE_PUBLIC_KEY`
        and `LANGFUSE_SECRET_KEY` are set in `.env`.
        """)

    with col2:
        st.subheader("Langfuse setup")
        st.code("""
# 1. Sign up free at cloud.langfuse.com
# 2. Create a project → get API keys
# 3. Add to your .env:

LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_HOST=https://cloud.langfuse.com
        """, language="bash")

    st.divider()
    st.subheader("Typical run metrics (benchmark)")

    benchmark = pd.DataFrame([
        {"Metric": "Tool calls per full scan", "Value": "8–12"},
        {"Metric": "End-to-end latency", "Value": "30–90s"},
        {"Metric": "Input tokens per run", "Value": "~15,000"},
        {"Metric": "Output tokens per run", "Value": "~2,000"},
        {"Metric": "Estimated cost per run", "Value": "$0.04–0.08"},
        {"Metric": "Findings detected", "Value": "4 / 4 (100%)"},
    ])
    st.dataframe(benchmark, use_container_width=True, hide_index=True)
