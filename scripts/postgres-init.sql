DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'cir_owner') THEN
    CREATE ROLE cir_owner LOGIN PASSWORD 'cir_owner_local';
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'cir_app') THEN
    CREATE ROLE cir_app LOGIN PASSWORD 'cir_app_local';
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'crm_app') THEN
    CREATE ROLE crm_app LOGIN PASSWORD 'crm_app_local';
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'support_app') THEN
    CREATE ROLE support_app LOGIN PASSWORD 'support_app_local';
  END IF;
END $$;

SELECT 'CREATE DATABASE cir OWNER cir_owner' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'cir') \gexec
SELECT 'CREATE DATABASE crm OWNER crm_app' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'crm') \gexec
SELECT 'CREATE DATABASE support OWNER support_app' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'support') \gexec
GRANT CONNECT ON DATABASE cir TO cir_owner, cir_app;
GRANT CONNECT ON DATABASE crm TO crm_app;
GRANT CONNECT ON DATABASE support TO support_app;
REVOKE CONNECT ON DATABASE crm FROM PUBLIC;
REVOKE CONNECT ON DATABASE support FROM PUBLIC;
