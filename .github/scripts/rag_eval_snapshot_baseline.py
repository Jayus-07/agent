"""快照当前用量账本基线 ID，输出到 GITHUB_OUTPUT."""

import os
import psycopg


def main() -> None:
    conn = psycopg.connect(
        host="localhost",
        port=5432,
        user="postgres",
        password="postgres",
        dbname=os.environ["MEMORY_PGDATABASE"],
    )
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COALESCE(MAX(id), 0) FROM llm_usage")
            start_id = int(cur.fetchone()[0] or 0)
    finally:
        conn.close()

    output_path = os.getenv("GITHUB_OUTPUT")
    output_line = f"usage_start_id={start_id}"
    if output_path:
        with open(output_path, "a", encoding="utf-8") as f:
            f.write(output_line + "\n")
    else:
        print(output_line)


if __name__ == "__main__":
    main()
