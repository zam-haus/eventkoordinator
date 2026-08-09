#!/usr/bin/env bash
# This script deletes all the data and restarts the server. Use with caution!
set -euo pipefail

# Parse parameters
DELETE_VOLUMES=false

show_help() {
  cat << EOF
Usage: $0 [OPTIONS]

Delete all data and restart the server. Use with caution!

OPTIONS:
  --volumes, -v     Also delete Docker volumes (complete reset)
  --help, -h        Show this help message

EXAMPLES:
  $0                # Reset without deleting volumes
  $0 --volumes      # Complete reset including volumes
EOF
  exit 0
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --volumes|-v)
      DELETE_VOLUMES=true
      shift
      ;;
    --help|-h)
      show_help
      ;;
    *)
      echo "Error: Unknown parameter '$1'" >&2
      echo "Run '$0 --help' for usage information" >&2
      exit 1
      ;;
  esac
done

# Enable debug output after parameter parsing
set -x

# Stop the server
./debug-docker-compose.sh down
docker container prune -f

# Delete volumes if requested
if [[ "$DELETE_VOLUMES" == "true" ]]; then
  echo "Deleting volumes..."
  ./debug-docker-compose.sh down -v db
fi
./debug-docker-compose.sh up -d db pretix

pushd backend
./manage.py migrate

# Delete volumes if requested
if [[ "$DELETE_VOLUMES" == "true" ]]; then
  echo "Recreating database and creating admin user..."
  ./manage.py create_openid_user --username admin --email mail@example.com --is-staff --is-superuser --password admin
  ./manage.py import_ical
fi
popd