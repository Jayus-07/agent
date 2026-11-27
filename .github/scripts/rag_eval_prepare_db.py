"""准备评测隔离迁移兼容库（创建 agent_business 库若不存在）."""

import psycopg2


def main() -> None:
    conn = psycopg2.connect(
        host="localhost",
        port=5432,
        user="postgres",
        password="postgres",
        dbname="postgres",
    )
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = 'agent_business'")
            if cur.fetchone() is None:
                cur.execute("CREATE DATABASE agent_business")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
