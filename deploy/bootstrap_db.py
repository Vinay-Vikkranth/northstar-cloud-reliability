"""Initialize isolated cloud databases using injected secrets, never demo passwords."""
from __future__ import annotations
import os
import psycopg
from psycopg import sql


def passwords():
    values = {role: os.environ[name] for role, name in (
        ("cir_owner", "CIR_OWNER_PASSWORD"), ("cir_app", "CIR_APP_PASSWORD"),
        ("crm_app", "CRM_DB_PASSWORD"), ("support_app", "SUPPORT_DB_PASSWORD"))}
    if any(len(value) < 32 or "local" in value.lower() for value in values.values()):
        raise ValueError("Each database role requires a distinct strong injected password")
    if len(set(values.values())) != len(values):
        raise ValueError("Database role passwords must be distinct")
    return values


def main():
    secrets = passwords()
    with psycopg.connect(os.environ["CIR_ADMIN_URL"], autocommit=True) as conn:
        with conn.cursor() as cursor:
            for role, password in secrets.items():
                cursor.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,))
                if cursor.fetchone() is None:
                    cursor.execute(sql.SQL("CREATE ROLE {} LOGIN").format(sql.Identifier(role)))
                cursor.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(sql.Identifier(role), sql.Literal(password)))
            for database, owner in (("cir", "cir_owner"), ("crm", "crm_app"), ("support", "support_app")):
                cursor.execute("SELECT 1 FROM pg_database WHERE datname=%s", (database,))
                if cursor.fetchone() is None:
                    cursor.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(database), sql.Identifier(owner)))
                cursor.execute(sql.SQL("REVOKE CONNECT ON DATABASE {} FROM PUBLIC").format(sql.Identifier(database)))
            cursor.execute("GRANT CONNECT ON DATABASE cir TO cir_owner, cir_app")
            cursor.execute("GRANT CONNECT ON DATABASE crm TO crm_app")
            cursor.execute("GRANT CONNECT ON DATABASE support TO support_app")
    print("Private database roles initialized; credential values were not logged.")


if __name__ == "__main__":
    main()
