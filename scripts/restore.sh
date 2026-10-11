#!/bin/sh
set -e
echo "Restoring $1 to $2 (stops writers first in production: docker compose stop portal)"
cp "$1" "$2" && echo restored
