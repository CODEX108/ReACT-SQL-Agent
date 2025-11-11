"""
example.py — Run the ReAct SQL Agent on a sample SQLite DB.

Automatically uses Gemini if GOOGLE_API_KEY is set.
Otherwise, falls back to a mock LLM that simulates reasoning.

Run:
    python example.py
"""

import os
import sqlite3
import google.generativeai as genai
from react_agent import ReactAgent

# ============================================================
# SETUP: Create a sample database
# ============================================================
def setup_sample_db(db_path="ecommerce.db"):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS customers (
            id INTEGER PRIMARY KEY,
            name TEXT,
            city TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY,
            customer_id INTEGER,
            amount REAL,
            FOREIGN KEY (customer_id) REFERENCES customers(id)
        )
    """)

    cur.execute("DELETE FROM customers;")
    cur.execute("DELETE FROM orders;")

    customers = [
        (1, "Alice Johnson", "New York"),
        (2, "Bob Smith", "Chicago"),
        (3, "Charlie Lee", "San Francisco"),
    ]
    orders = [
        (1, 1, 250.50),
        (2, 1, 125.00),
        (3, 2, 400.75),
        (4, 3, 320.00),
    ]
    cur.executemany("INSERT INTO customers VALUES (?, ?, ?)", customers)
    cur.executemany("INSERT INTO orders VALUES (?, ?, ?)", orders)
    conn.commit()
    conn.close()
    print(f"Database '{db_path}' created with sample data.")


# ============================================================
# MOCK LLM (for offline testing)
# ============================================================
class MockLLM:
    """
    A simple mock model that simulates a reasoning process.
    It looks for keywords in user queries and outputs deterministic traces.
    """

    def __call__(self, system_prompt, history):
        last_msg = history[-1]["content"].lower()
        if "list_tables" in last_msg:
            return "FINAL ANSWER: There are two tables: customers and orders."
        if "how many orders" in last_msg:
            return "FINAL ANSWER: Alice Johnson placed 2 orders."
        # Basic initial reasoning:
        if "what tables" in last_msg:
            return """THOUGHT: I should inspect what tables exist.
ACTION: list_tables{}"""
        if "orders has alice" in last_msg:
            return """THOUGHT: I should check the customers table for Alice and count matching orders.
ACTION: query_database{"query":"SELECT COUNT(*) FROM orders JOIN customers ON orders.customer_id = customers.id WHERE customers.name = 'Alice Johnson';"}"""
        # Default fallback
        return "FINAL ANSWER: Sorry, I cannot determine the answer."


# ============================================================
# MAIN
# ============================================================
def main():
    print("ReAct SQL Agent - Example Runs")

    db_path = "ecommerce.db"
    setup_sample_db(db_path)

    api_key = os.getenv("GOOGLE_API_KEY")
    if api_key:
        print("\nUsing Gemini API client...")
        genai.configure(api_key=api_key)
        llm = genai.GenerativeModel("gemini-2.5-flash")
    else:
        print("\nNo GOOGLE_API_KEY found — using Mock LLM (offline mode).")
        llm = MockLLM()

    agent = ReactAgent(db_path, llm)

    print("\n" + "=" * 80)
    print("EXAMPLE 1: Schema Exploration")
    print("=" * 80)
    result1 = agent.run("What tables are in this database and what do they contain?")
    print("\nAnswer:", result1["answer"])

    print("\n" + "=" * 80)
    print("EXAMPLE 2: Aggregation")
    print("=" * 80)
    result2 = agent.run("How many orders has Alice Johnson placed?")
    print("\nAnswer:", result2["answer"])


if __name__ == "__main__":
    main()
