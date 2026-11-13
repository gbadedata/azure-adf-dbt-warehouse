# One image for both jobs: `python -m ingest ...` and `dbt ...`.
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/src \
    DBT_PROFILES_DIR=/app/dbt \
    INGEST_SQL_DIR=/app/sql \
    DBT_SEND_ANONYMOUS_USAGE_STATS=false

# Microsoft ODBC Driver 18 for SQL Server.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl gnupg unixodbc \
 && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
 && curl -fsSL https://packages.microsoft.com/config/debian/12/prod.list -o /etc/apt/sources.list.d/mssql-release.list \
 && apt-get update \
 && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 \
 && apt-get purge -y curl gnupg \
 && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY src/ src/
COPY sql/ sql/
COPY dbt/ dbt/

# dbt writes target/ and logs/ next to the project.
RUN useradd --uid 10001 --create-home runner && chown -R runner /app/dbt
USER runner

CMD ["python", "-m", "ingest", "--help"]
