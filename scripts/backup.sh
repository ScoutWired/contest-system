#!/bin/sh
set -e
SRC="${1:-./data/portal.db}"
DST="${2:-./backups/portal-$(date +%Y%m%d-%H%M%S).db}"
mkdir -p "$(dirname "$DST")"
sqlite3 "$SRC" ".backup '$DST'" && echo "backup -> $DST"
