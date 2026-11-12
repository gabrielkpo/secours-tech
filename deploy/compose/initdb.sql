-- Runs once, as the Postgres superuser, when the local database volume is created. The pipeline
-- connects as secours_tech: owner of its database and schema, not a superuser. The extensions
-- need a superuser, so they are created here; migration 001 then finds them in place.
\getenv app_password APP_DB_PASSWORD
CREATE ROLE secours_tech LOGIN PASSWORD :'app_password';
ALTER DATABASE secours_tech OWNER TO secours_tech;
\connect secours_tech
ALTER SCHEMA public OWNER TO secours_tech;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS unaccent;
