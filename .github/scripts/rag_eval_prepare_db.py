"""准备评测隔离迁移兼容库（创建 agent_business 库若不存在）."""

import os
import psycopg2


def main() -> None:
    # 自动复用环境变量中的 PGHOST, PGPORT, PGUSER, PGPASSWORD
    conn = psycopg2.connect(dbname=os.getenv("PGDATABASE", "postgres"))
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
