import os
from pathlib import Path

import psycopg
from psycopg import sql

def main():
    database_url = os.environ["DATABASE_ADMIN_URL"]
    document_password = os.environ["DOCUMENT_DB_PASSWORD"]
    ddl = Path("/app/documents.sql").read_text(encoding="utf-8")
    with psycopg.connect(database_url) as connection:
        connection.execute(ddl)
        exists = connection.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = 'document_writer'"
        ).fetchone()
        if not exists:
            connection.execute(
                sql.SQL("CREATE ROLE document_writer LOGIN PASSWORD {}").format(
                    sql.Literal(document_password)
                )
            )
        else:
            connection.execute(
                sql.SQL("ALTER ROLE document_writer PASSWORD {}").format(
                    sql.Literal(document_password)
                )
            )
        database_name = connection.execute("SELECT current_database()").fetchone()[0]
        connection.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO document_writer").format(
                sql.Identifier(database_name)
            )
        )
        connection.execute("GRANT USAGE ON SCHEMA public TO document_writer")
        connection.execute(
            "GRANT SELECT, INSERT, UPDATE, DELETE ON document, document_project, document_page, document_job TO document_writer"
        )
        connection.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO document_writer")
        connection.execute("ALTER ROLE document_writer SET statement_timeout = '15s'")


if __name__ == "__main__":
    main()
