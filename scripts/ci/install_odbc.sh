#!/usr/bin/env bash
# Install Microsoft ODBC Driver 18 for SQL Server on an Ubuntu CI agent, if missing.
set -euo pipefail
if odbcinst -q -d -n "ODBC Driver 18 for SQL Server" >/dev/null 2>&1; then
  echo "ODBC Driver 18 for SQL Server already installed"
  exit 0
fi
release="$(lsb_release -rs)"
curl -fsSL -o /tmp/packages-microsoft-prod.deb "https://packages.microsoft.com/config/ubuntu/${release}/packages-microsoft-prod.deb"
sudo dpkg -i /tmp/packages-microsoft-prod.deb
sudo apt-get update -q
sudo ACCEPT_EULA=Y apt-get install -y -q msodbcsql18 unixodbc
odbcinst -q -d -n "ODBC Driver 18 for SQL Server"
