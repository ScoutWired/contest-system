#!/bin/sh
set -e
SRC="${1:-./data/portal.db}"
DST="${2:-./backups/portal-$(date +%Y%m%d-%H%M%S).db}"
mkdir -p "$(dirname "$DST")"
if command -v sqlite3 >/dev/null 2>&1; then
  sqlite3 "$SRC" ".backup '$DST'" && echo "backup -> $DST"
else
  python3 -c "import sqlite3,sys; sc=sqlite3.connect(sys.argv[1]); sc.execute(\"VACUUM INTO '\"+sys.argv[2]+\"'\"); sc.close(); print('backup -> '+sys.argv[2])" "$SRC" "$DST"
fi
