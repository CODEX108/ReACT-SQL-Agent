"""
Test suite for ReAct SQL Agent
"""
import sqlite3
import os
import pytest
from react_agent import ReactAgent, ToolRegistry, validate_sql, parse_action


# ============================================================================
# Test Database Setup
# ============================================================================

@pytest.fixture
def test_db():
    """Create a test database"""
    db_path = "test.db"
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Create sample tables
    cursor.execute("""
        CREATE TABLE customers (
            id INTEGER PRIMARY KEY,
            name TEXT,
            email TEXT,
            city TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE orders (
            id INTEGER PRIMARY KEY,
            customer_id INTEGER,
            product TEXT,
            amount REAL,
            order_date TEXT
        )
    """)

    # Insert sample data
    customers = [
        (1, "Alice Smith", "alice@example.com", "New York"),
        (2, "Bob Jones", "bob@example.com", "Los Angeles"),
        (3, "Carol White", "carol@example.com", "Chicago")
    ]
    cursor.executemany("INSERT INTO customers VALUES (?, ?, ?, ?)", customers)

    orders = [
        (1, 1, "Widget", 29.99, "2024-01-15"),
        (2, 1, "Gadget", 49.99, "2024-01-20"),
        (3, 2, "Widget", 29.99, "2024-01-18"),
        (4, 3, "Tool", 19.99, "2024-01-22")
    ]
    cursor.executemany("INSERT INTO orders VALUES (?, ?, ?, ?, ?)", orders)

    conn.commit()
    conn.close()

    yield db_path

    # Cleanup
    if os.path.exists(db_path):
        os.remove(db_path)


# ============================================================================
# Unit Tests
# ============================================================================

def test_validate_sql():
    """Test SQL validation function"""
    # Valid queries
    assert validate_sql("SELECT * FROM users") is None
    assert validate_sql("SELECT name, email FROM customers WHERE city = 'NYC'") is None
    assert validate_sql("SELECT COUNT(*) FROM orders GROUP BY customer_id") is None

    # Invalid queries
    assert validate_sql("INSERT INTO users VALUES (1, 'test')") is not None
    assert validate_sql("UPDATE users SET name = 'test'") is not None
    assert validate_sql("DELETE FROM users") is not None
    assert validate_sql("DROP TABLE users") is not None
    assert validate_sql("ALTER TABLE users ADD COLUMN age INT") is not None


def test_parse_action():
    """Test action parsing from LLM output"""
    # Valid actions
    text1 = "THOUGHT: I need to list tables\nACTION: list_tables{}"
    result1 = parse_action(text1)
    assert result1 == ("list_tables", {})

    text2 = 'ACTION: describe_table{"table_name": "customers"}'
    result2 = parse_action(text2)
    assert result2 == ("describe_table", {"table_name": "customers"})

    text3 = 'ACTION: query_database{"query": "SELECT * FROM orders"}'
    result3 = parse_action(text3)
    assert result3[0] == "query_database"
    assert "query" in result3[1]

    # Invalid action
    text4 = "THOUGHT: Something without action"
    result4 = parse_action(text4)
    assert result4 is None


def test_tool_registry_list_tables(test_db):
    """Test list_tables tool"""
    registry = ToolRegistry(test_db)
    result = registry._list_tables()
    assert "customers" in result
    assert "orders" in result


def test_tool_registry_describe_table(test_db):
    """Test describe_table tool"""
    registry = ToolRegistry(test_db)
    result = registry._describe_table("customers")
    assert "customers" in result
    assert "name" in result.lower()
    assert "email" in result.lower()
    assert "Row count: 3" in result


def test_tool_registry_query_database(test_db):
    """Test query_database tool with valid SELECT"""
    registry = ToolRegistry(test_db)
    result = registry._query_database("SELECT COUNT(*) FROM customers")
    assert "3" in result or "Rows" in result


def test_tool_registry_query_validation(test_db):
    """Test that non-SELECT queries are rejected"""
    registry = ToolRegistry(test_db)

    # Try to insert
    result = registry._query_database("INSERT INTO customers VALUES (4, 'Test', 'test@example.com', 'Boston')")
    assert "Error" in result
    assert "read-only" in result.lower() or "forbidden" in result.lower()

    # Try to delete
    result = registry._query_database("DELETE FROM customers WHERE id = 1")
    assert "Error" in result


# ============================================================================
# Integration Tests
# ============================================================================

class MockLLMClient:
    """Mock LLM client for testing"""

    def __init__(self, responses):
        self.responses = responses
        self.call_count = 0
        self.messages = type('obj', (object,), {'create': self.create})()

    def create(self, **kwargs):
        response = self.responses[min(self.call_count, len(self.responses) - 1)]
        self.call_count += 1

        # Return mock response object
        return type('obj', (object,), {
            'content': [type('obj', (object,), {'text': response})()]
        })()


def test_agent_schema_exploration(test_db):
    """Test agent can explore database schema"""
    responses = [
        "THOUGHT: I need to see what tables exist\nACTION: list_tables{}",
        "THOUGHT: Let me describe the customers table\nACTION: describe_table{\"table_name\": \"customers\"}",
        "FINAL ANSWER: The database has customers and orders tables. The customers table has columns: id, name, email, city with 3 rows."
    ]

    client = MockLLMClient(responses)
    agent = ReactAgent(test_db, client, max_steps=5)
    result = agent.run("What tables exist?", verbose=False)

    assert result["success"]
    assert "customers" in result["answer"].lower() or "orders" in result["answer"].lower()


def test_agent_aggregation_query(test_db):
    """Test agent can perform aggregation queries"""
    responses = [
        "THOUGHT: I need to list tables first\nACTION: list_tables{}",
        "THOUGHT: Now let me describe the orders table\nACTION: describe_table{\"table_name\": \"orders\"}",
        "THOUGHT: I can now count the orders\nACTION: query_database{\"query\": \"SELECT COUNT(*) FROM orders\"}",
        "FINAL ANSWER: There are 4 orders in the database."
    ]

    client = MockLLMClient(responses)
    agent = ReactAgent(test_db, client, max_steps=5)
    result = agent.run("How many orders are there?", verbose=False)

    assert result["success"]
    assert "4" in result["answer"]


def test_agent_error_recovery(test_db):
    """Test agent recovers from errors"""
    responses = [
        "THOUGHT: Let me query directly\nACTION: query_database{\"query\": \"SELECT * FROM nonexistent_table\"}",
        "THOUGHT: That table doesn't exist. Let me list tables first\nACTION: list_tables{}",
        "THOUGHT: Now I'll query the correct table\nACTION: query_database{\"query\": \"SELECT * FROM customers LIMIT 1\"}",
        "FINAL ANSWER: The customers table exists and contains customer data."
    ]

    client = MockLLMClient(responses)
    agent = ReactAgent(test_db, client, max_steps=6)
    result = agent.run("Show me a customer", verbose=False)

    assert result["success"]
    # Agent should recover and provide an answer


if __name__ == "__main__":
    pytest.main([__file__, "-v"])