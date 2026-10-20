#!/bin/bash
# Nightly reset of the shared demo workspace to the seeded snapshot.
#   snapshot: bash demo_reset.sh snapshot   (run once after seeding)
#   reset:    bash demo_reset.sh            (run by sounding-demo-reset.timer)
set -euo pipefail
R=/root/sounding
SNAP=$R/demo-snapshot
if [ "${1:-}" = "snapshot" ]; then
  mkdir -p $SNAP
  /root/venv/bin/python3 -c "import sqlite3; s=sqlite3.connect('$R/sounding.db'); d=sqlite3.connect('$SNAP/sounding.db'); s.backup(d)"
  ls $R/meetings > $SNAP/meetings.txt
  echo "snapshot: $(wc -l < $SNAP/meetings.txt) meetings"
  exit 0
fi
[ -f $SNAP/sounding.db ] || { echo "no snapshot yet"; exit 1; }
systemctl stop sounding-api
cp $SNAP/sounding.db $R/sounding.db
rm -f $R/sounding.db-wal $R/sounding.db-shm
# Remove recordings uploaded since the snapshot, and cached clips (they are re-cut on demand).
for d in $R/meetings/*/; do
  id=$(basename "$d")
  grep -qx "$id" $SNAP/meetings.txt || rm -rf "$d"
done
find $R/meetings -name '*.clip.mp3' -o -name '*.clip.mp4' | xargs -r rm -f
systemctl start sounding-api
echo "reset done $(date -Is)"
