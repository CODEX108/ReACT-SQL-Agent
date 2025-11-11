# ReAct SQL Agent

This repository contains a lightweight, from-scratch implementation of a ReAct (Reasoning + Acting) agent designed for SQL database querying. The agent interprets natural language questions and interacts with a SQLite database using a structured thought-action-observation loop.

This project was built from the ground up without using any external agent frameworks (like LangChain or LlamaIndex), focusing on the core principles of agentic design, tool use, and safety.

## Features

  * **From-Scratch ReAct Loop:** Implements the core **Thought -\> Action -\> Observation** cycle.
  * **Three-Tool System:**
      * `list_tables`: Discovers available tables.
      * `describe_table`: Retrieves the schema for a specific table.
      * `query_database`: Executes read-only SQL queries.
  * **LLM Agnostic:** Designed to work with multiple LLM clients (e.g., OpenAI, Anthropic, Google Gemini, or local models via an OpenAI-compatible endpoint).
  * **Safety-First Design:** Includes a SQL validator that strictly enforces **read-only** (`SELECT`) operations, blocking any `INSERT`, `UPDATE`, `DELETE`, `DROP`, etc. commands.
  * **Full Audibility:** Provides verbose, step-by-step traces of its reasoning, actions, and observations for easy debugging and auditing.
  * **Robust Testing:** Includes a complete test suite using `pytest` to validate all components, from SQL safety to end-to-end agent logic.

## File Structure

```
.
├── react_agent.py       # The core ReactAgent class and ToolRegistry
├── example.py           # A runnable demo that creates a sample DB and runs queries
├── testAgent.py         # The pytest-based test suite for all agent components
├── .gitignore           # Standard Python gitignore
├── AI_Agents_Assignment.pdf # The original project brief and requirements
└── README.md            # This file
```

## Installation

1.  Clone this repository to your local machine.

2.  It is highly recommended to create a Python virtual environment:

    ```bash
    python -m venv venv
    source venv/bin/activate  # On macOS/Linux
    .\venv\Scripts\activate   # On Windows
    ```

3.  Install the necessary dependencies. You will only need the client library for the LLM you choose to use.

    For Google Gemini:

    ```bash
    pip install google-generativeai
    ```

    For OpenAI (or compatible local models like Ollama):

    ```bash
    pip install openai
    ```

    For Anthropic:

    ```bash
    pip install anthropic
    ```

    To run the tests, you also need `pytest`:

    ```bash
    pip install pytest
    ```

## Setup & Configuration

The agent expects your API key to be set as an environment variable.

  * **For Google Gemini:**
    ```bash
    export GOOGLE_API_KEY="your-api-key-here"
    ```
  * **For OpenAI:**
    ```bash
    export OPENAI_API_KEY="sk-your-api-key-here"
    ```
  * **For Anthropic:**
    ```bash
    export ANTHROPIC_API_KEY="sk-ant-your-api-key-here"
    ```

(Use `set` instead of `export` on Windows Command Prompt, or `$env:` on PowerShell).

## How to Run

### 1\. Running the Demo

The `example.py` file is the main entry point for a demonstration. It will:

1.  Create a new `ecommerce.db` file with sample data.
2.  Run several example queries against it.
3.  Print the full reasoning trace for each query.

**To run the demo, execute this one-line command:**

```bash
python example.py
```

### 2\. Using the Agent in Your Code

You can easily import and use the `ReactAgent` in your own scripts.

```python
import os
from react_agent import ReactAgent
import google.generativeai as genai # Or import openai, anthropic

# 1. Initialize your LLM client
# (Make sure your API key is set as an environment variable)
api_key = os.getenv("GOOGLE_API_KEY")
genai.configure(api_key=api_key)
client = genai.GenerativeModel("models/gemini-pro") # Or your chosen model

# 2. Initialize the agent with your database path and client
agent = ReactAgent(db_path="ecommerce.db", llm_client=client, max_steps=10)

# 3. Run your query
query = "How many customers are in New York?"
result = agent.run(query, verbose=True)

# 4. Print the final answer
print(f"\nFinal Answer: {result['answer']}")
```

## Running Tests

To verify all components are working correctly, run the test suite using `pytest`. This will create a temporary `test.db` and run a series of validation and integration tests.

```bash
pytest
```

## Example Agent Trace

Here is what the agent's reasoning process looks like:

```
User Query: "How many orders has Alice Johnson placed?"

--- Step 1 ---
THOUGHT: I need to find out how many orders Alice Johnson has placed. First, I need to understand the database schema. I'll start by listing the tables.
ACTION: list_tables{}
OBSERVATION: Tables: products, customers, orders

--- Step 2 ---
THOUGHT: The tables 'customers' and 'orders' seem relevant. I'll inspect the 'customers' table to find Alice Johnson's ID.
ACTION: describe_table{"table_name": "customers"}
OBSERVATION: Table 'customers': id (INTEGER), name (TEXT), email (TEXT), city (TEXT), signup_date (TEXT). Row count: 3

--- Step 3 ---
THOUGHT: Now I'll inspect the 'orders' table to see how it's linked to customers.
ACTION: describe_table{"table_name": "orders"}
OBSERVATION: Table 'orders': id (INTEGER), customer_id (INTEGER), product_id (INTEGER), quantity (INTEGER), total_amount (REAL), order_date (TEXT). Row count: 4

--- Step 4 ---
THOUGHT: The 'orders' table has a 'customer_id' column which links to the 'customers' table 'id' column. Now I can construct a query. I need to:
1. Find the 'id' of 'Alice Johnson' from the 'customers' table.
2. Count the number of rows in the 'orders' table where 'customer_id' matches Alice's ID.
I can do this with a JOIN.
ACTION: query_database{"query": "SELECT COUNT(o.id) FROM orders o JOIN customers c ON o.customer_id = c.id WHERE c.name = 'Alice Johnson'"}
OBSERVATION: Columns: COUNT(o.id)
Rows (1 total):
  2

--- Step 5 ---
THOUGHT: The query returned 2, which is the count of orders for Alice Johnson. I have the final answer.
FINAL ANSWER: Alice Johnson has placed 2 orders.
```