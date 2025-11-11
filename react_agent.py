"""
Lightweight ReAct SQL Agent - A minimal agent implementation for SQL database querying
(Edited: improved action parsing, tool handling, formatting, and SQL validation messages)
"""
import sqlite3
import json
import re
import time
from typing import Dict, List, Any, Optional, Callable, Tuple
from dataclasses import dataclass
import google.generativeai as genai  # <-- keep gemini integration available

# ===========================================================================
# TOOL SYSTEM (80-100 lines)
# ===========================================================================

@dataclass
class Tool:
    """Base tool interface"""
    name: str
    description: str
    parameters: Dict[str, str]
    func: Callable

    def call(self, **kwargs) -> str:
        """Execute the tool with given parameters"""
        try:
            result = self.func(**kwargs)
            # ensure a string return for observations
            if isinstance(result, tuple):
                # some underlying helpers return (str, count)
                return result[0]
            return str(result)
        except Exception as e:
            return f"Error: {str(e)}"

    def to_dict(self) -> Dict[str, Any]:
        """Return tool specification for LLM"""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters
        }


class ToolRegistry:
    """Manages available tools for the agent"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.tools: Dict[str, Tool] = {}
        self._register_tools()

    def _register_tools(self):
        """Register all available tools"""
        self.tools["list_tables"] = Tool(
            name="list_tables",
            description="Lists all tables in the database",
            parameters={},
            func=self._list_tables
        )
        self.tools["describe_table"] = Tool(
            name="describe_table",
            description="Describes schema and row count of a specific table",
            parameters={
                "table_name": "string (name of table to describe)"
            },
            func=self._describe_table
        )
        self.tools["query_database"] = Tool(
            name="query_database",
            description="Executes a *read-only* SQL SELECT query. Limit to 100 rows.",
            parameters={
                "query": "string (SQL SELECT query)"
            },
            func=self._query_database
        )

    def get_tool(self, name: str) -> Optional[Tool]:
        """Get tool by name"""
        return self.tools.get(name)

    def get_tool_specs(self) -> str:
        """Get all tool specifications for prompt"""
        return json.dumps([t.to_dict() for t in self.tools.values()], indent=2)

    # --- Tool Implementations ---

    def _execute_sql(self, query: str, params: tuple = ()) -> (str, int):
        """Helper to run query and return formatted result + row count"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row  # Access columns by name
        cursor = conn.cursor()

        cursor.execute(query, params)

        # If the query returned columns (SELECT), format rows
        if cursor.description:
            rows = cursor.fetchall()
            row_count = len(rows)

            if row_count == 0:
                conn.close()
                return "No rows returned.", 0

            # Format output
            cols = [d[0] for d in cursor.description]
            output_lines = []
            output_lines.append(f"Columns: {', '.join(cols)}")
            output_lines.append(f"Rows ({row_count} total):")
            for i, row in enumerate(rows):
                if i >= 100:  # Hard limit on rows
                    output_lines.append("  ... (truncated after 100 rows)")
                    break
                # Keep row values concise
                vals = ["" if row[col] is None else str(row[col]) for col in cols]
                output_lines.append("  " + " | ".join(vals))
            conn.close()
            return "\n".join(output_lines), row_count
        else:
            # Non-select (shouldn't be used often), commit then close
            conn.commit()
            conn.close()
            return f"Query executed successfully.", 0

    def _list_tables(self) -> str:
        """Tool: list_tables"""
        # list only user tables (exclude sqlite_sequence etc.)
        query = "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name;"
        result, _ = self._execute_sql(query)

        # Parse only the actual row lines which start with two spaces ("  ")
        lines = result.splitlines()
        tables = []
        for ln in lines:
            if ln.startswith("  "):
                tables.append(ln.strip())
        if not tables:
            return "Tables: (none found)"
        return "Tables: " + ", ".join(tables)

    def _describe_table(self, table_name: str) -> str:
        """Tool: describe_table"""
        # Get schema
        # Use safe identifier usage (no interpolation of arbitrary SQL) — table_name should be validated by agent
        schema_query = f"PRAGMA table_info('{table_name}');"
        schema_result, _ = self._execute_sql(schema_query)

        # If PRAGMA returned no rows or an error -> surface it
        if "No rows returned." in schema_result or "error" in schema_result.lower():
            return f"Error: Could not describe table '{table_name}'. It may not exist."

        # Get row count
        try:
            count_query = f"SELECT COUNT(*) as cnt FROM '{table_name}';"
            count_result, _ = self._execute_sql(count_query)
            # count_result lines contain the value in the last data line
            count_lines = [l for l in count_result.splitlines() if l.startswith("  ")]
            count = count_lines[-1].strip() if count_lines else count_result.strip()
        except Exception:
            count = "unknown"

        # Parse schema lines: pick lines starting with two spaces
        schema_lines_raw = [ln for ln in schema_result.splitlines() if ln.startswith("  ")]
        schema_tuples = []
        for ln in schema_lines_raw:
            parts = [p.strip() for p in ln.strip().split(" | ")]
            # PRAGMA table_info returns: cid | name | type | notnull | dflt_value | pk
            if len(parts) >= 3:
                colname = parts[1]
                coltype = parts[2]
                schema_tuples.append(f"{colname} ({coltype})")
        schema = ", ".join(schema_tuples) if schema_tuples else "(no columns found)"

        return f"Table '{table_name}': {schema}. Row count: {count}"

    def _query_database(self, query: str) -> str:
        """Tool: query_database"""
        # Safety: Validate SQL query
        if error := validate_sql(query):
            return error

        result, row_count = self._execute_sql(query)

        if row_count > 100:
            result += "\nNote: Query was auto-limited to 100 rows. Add or refine LIMIT clauses if needed."
        return result


# ===========================================================================
# AGENT SYSTEM (150-200 lines)
# ===========================================================================

# --- SQL Safety ---
def validate_sql(query: str) -> Optional[str]:
    """Basic SQL validator — enforces read-only and rejects dangerous keywords"""
    query_lower = query.lower().strip()

    # Basic check for SELECT at top (allow leading parentheses / whitespace)
    if not re.match(r'^\(?\s*select\b', query_lower):
        return "Error: Only SELECT queries are allowed (read-only agent)."

    banned_keywords = ["delete", "insert", "update", "drop", "alter", "commit", "rollback", "attach", "detach", "pragma"]
    for keyword in banned_keywords:
        if re.search(r'\b' + re.escape(keyword) + r'\b', query_lower):
            return f"Error: Query contains forbidden keyword '{keyword}' (read-only agent)."

    # Disallow writing to sqlite_master
    if "sqlite_master" in query_lower:
        return "Error: Direct queries to sqlite_master are not allowed. Use the list_tables tool."

    # Optional: enforce a maximum LIMIT to guard results
    # If user didn't specify LIMIT, we will not reject, but agent will be guided to add LIMIT in its prompt.
    # (Actual enforcement is done in _execute_sql truncation.)

    return None

# --- Prompting ---
def get_system_prompt(tools: str) -> str:
    """Build the system prompt"""
    return f"""You are a helpful ReAct agent designed to answer questions about a SQL database.

Follow this exact workflow:
1.  THOUGHT: Analyze the user's request and your history. Decide on the next step.
2.  ACTION: Choose one of the available tools. The action must be a single-line JSON object or the shorthand form:
    - JSON form: ACTION: {{"name": "list_tables", "parameters": {{}}}}
    - Shorthand: ACTION: list_tables{{}}
    - Shorthand with params: ACTION: describe_table{{"table_name":"customers"}}
3.  OBSERVATION: (This will be provided by the system after you use a tool)
4.  FINAL ANSWER: (Once you have the answer, respond with this prefix)

Your tools are:
{tools}

Rules:
- You are read-only. You cannot modify the database.
- First, explore the database. Use `list_tables` then `describe_table` to understand the schema before you try `query_database`.
- All SQL in `query_database` must be compatible with SQLite.
- When querying, be specific. Select only the columns you need.
- Always include LIMIT in your queries to avoid large outputs. LIMIT 20 is a good default.
- If you get an error, read the error message and try to correct your mistake in the next step.
- If a user's question is ambiguous, ask a clarifying question or answer based on discovered schema.
- Do not make assumptions about column names or data types.
"""

# --- Parsing ---
# Accept either JSON object or shorthand action:name{json}
ACTION_JSON_RE = re.compile(r"ACTION:\s*(\{.*\})", re.DOTALL)
ACTION_SHORT_RE = re.compile(r'ACTION:\s*([a-zA-Z0-9_]+)\s*(\{.*\})?', re.DOTALL)
FINAL_ANSWER_RE = re.compile(r"FINAL ANSWER:\s*(.+)", re.DOTALL)


def parse_action(text: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    """
    Parse ACTION line and return (tool_name, parameters)
    Supports:
      - ACTION: {"name":"list_tables","parameters":{}}
      - ACTION: list_tables{}
      - ACTION: describe_table{"table_name":"customers"}
    """
    # Try JSON form first
    json_match = ACTION_JSON_RE.search(text)
    if json_match:
        try:
            obj = json.loads(json_match.group(1).strip())
            # Accept either {"name": "...", "parameters": {...}} or {"tool": "...", ...}
            name = obj.get("name") or obj.get("tool") or obj.get("tool_name")
            params = obj.get("parameters") or obj.get("args") or {}
            if name:
                return name, params
        except json.JSONDecodeError:
            return None

    # Try shorthand form
    short_match = ACTION_SHORT_RE.search(text)
    if short_match:
        name = short_match.group(1)
        params_str = short_match.group(2)
        if not params_str:
            return name, {}
        try:
            params = json.loads(params_str)
            return name, params
        except json.JSONDecodeError:
            return None

    return None


# --- Agent Class ---
class ReactAgent:
    """ReAct SQL Agent"""

    def __init__(self, db_path: str, llm_client: Any, max_steps: int = 10):
        self.db_path = db_path
        self.llm_client = llm_client
        self.max_steps = max_steps

        self.tools = ToolRegistry(db_path)
        self.system_prompt = get_system_prompt(self.tools.get_tool_specs())
        self.history: List[Dict[str, str]] = []

    def run(self, query: str, verbose: bool = True) -> Dict[str, Any]:
        """Run the agent loop"""
        self.history = [{"role": "user", "content": query}]
        trace = []

        if verbose:
            print(f"User Query: \"{query}\"\n")

        for step in range(self.max_steps):
            if verbose:
                print(f"--- Step {step + 1} ---")

            # 1. Call LLM for THOUGHT and ACTION
            llm_output = self._call_llm()
            trace.append({"llm_output": llm_output})
            if verbose:
                print(llm_output)

            # 2. Check for FINAL ANSWER
            final_answer_match = FINAL_ANSWER_RE.search(llm_output)
            if final_answer_match:
                answer = final_answer_match.group(1).strip()
                return {
                    "success": True,
                    "answer": answer,
                    "steps": step + 1,
                    "trace": trace
                }

            # 3. Parse and execute ACTION (parse_action returns tuple or None)
            parsed = parse_action(llm_output)
            if not parsed:
                observation = "Error: Invalid action format. Must be ACTION: { ... } or ACTION: tool_name{...}"
                if verbose:
                    print(f"OBSERVATION: {observation}")
                self.history.append({"role": "user", "content": f"OBSERVATION: {observation}"})
                trace.append({"observation": observation})
                continue

            tool_name, tool_params = parsed
            tool = self.tools.get_tool(tool_name)
            if not tool:
                observation = f"Error: Tool '{tool_name}' not found."
                if verbose:
                    print(f"OBSERVATION: {observation}")
                self.history.append({"role": "user", "content": f"OBSERVATION: {observation}"})
                trace.append({"observation": observation})
                continue

            # Execute tool
            if not isinstance(tool_params, dict):
                observation = "Error: ACTION parameters must be a JSON object."
                if verbose:
                    print(f"OBSERVATION: {observation}")
                self.history.append({"role": "user", "content": f"OBSERVATION: {observation}"})
                trace.append({"observation": observation})
                continue

            observation = tool.call(**tool_params)
            if verbose:
                print(f"OBSERVATION: {observation}")
            trace.append({"observation": observation})

            # 4. Add to history (LLM will see the observation)
            self.history.append({"role": "user", "content": f"OBSERVATION: {observation}"})

        # Max steps reached
        return {
            "success": False,
            "answer": "Max steps reached without final answer",
            "steps": self.max_steps,
            "trace": trace
        }

    def _call_llm(self, retries: int = 4) -> str:
        """Call LLM with exponential backoff. Return the raw assistant text."""
        for attempt in range(retries):
            try:
                # --- GEMINI (google.generativeai) ---
                # Note: genai.GenerativeModel instances are used in example.py initialization
                if hasattr(genai, "GenerativeModel") and isinstance(self.llm_client, genai.GenerativeModel):
                    # Build a simple conversation-like history for Gemini's generate_content
                    gemini_history = []
                    for msg in self.history:
                        gemini_history.append({
                            "role": "user" if msg["role"] == "user" else "model",
                            "parts": [msg["content"]]
                        })

                    full_history = [
                        {"role": "user", "parts": [self.system_prompt]},
                        {"role": "model", "parts": ["Understood. I will follow the ReAct format."]}
                    ] + gemini_history

                    # The actual gemini client may require different call signatures depending on the SDK version.
                    # Try generate_content (newer SDKs) or list/other compat patterns.
                    response = self.llm_client.generate_content(full_history)
                    # response.text or response.output[0].content[0].text depending on SDK; try common attributes
                    text = getattr(response, "text", None)
                    if text:
                        return text
                    # try nested structure
                    try:
                        return response.output[0].content[0].text
                    except Exception:
                        return str(response)

                # --- OPENAI-style client (has chat) ---
                elif hasattr(self.llm_client, "chat"):
                    messages = [{"role": "system", "content": self.system_prompt}] + self.history
                    response = self.llm_client.chat.completions.create(
                        model="gpt-4",
                        messages=messages,
                        temperature=0.1
                    )
                    return response.choices[0].message.content

                # --- Anthropic-style (client.messages.create) or Mock that mimics it ---
                elif hasattr(self.llm_client, "messages") and hasattr(self.llm_client.messages, "create"):
                    # Many tests/mock clients return .content[0].text
                    resp = self.llm_client.messages.create(
                        model="claude-3-1",  # placeholder for mock; not used by test mocks
                        max_tokens=1024,
                        system=self.system_prompt,
                        messages=self.history
                    )
                    # attempt common response shapes
                    if hasattr(resp, "content") and isinstance(resp.content, list):
                        try:
                            return resp.content[0].text
                        except Exception:
                            return str(resp.content[0])
                    # fallback
                    return str(resp)

                else:
                    # Fallback: if the client is a simple callable that returns string
                    if callable(self.llm_client):
                        return self.llm_client(self.system_prompt, self.history)
                    # as last resort, string-ify
                    return str(self.llm_client)

            except Exception as e:
                # Exponential backoff for retriable errors (including rate-limit 429)
                err_text = str(e)
                print(f"Error calling LLM (Attempt {attempt + 1}): {err_text}")
                if attempt == retries - 1:
                    # give up and re-raise
                    raise
                # If the error mentions retry seconds, try to parse; otherwise backoff
                wait = 2 ** attempt
                # prefer to respect explicit suggested wait times in errors if present
                m = re.search(r"retry in\s*([0-9]+(?:\.[0-9]+)?)s", err_text, re.IGNORECASE)
                if m:
                    try:
                        wait = float(m.group(1)) + 1.0
                    except Exception:
                        pass
                time.sleep(wait)

        raise Exception("LLM call failed after retries")
