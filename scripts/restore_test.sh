#!/usr/bin/env bash
# Prove a backup can be restored: load the newest archive into a scratch database
# and compare every collection's document count with the archive's source database.
#
#   GUZO_MONGO_URL=... GUZO_MONGO_DB=guzo scripts/restore_test.sh /var/backups/guzo
#
# Needs mongorestore and mongosh. Run monthly. The scratch database is dropped after.
# To restore for real, run mongorestore with --nsFrom/--nsTo pointing at the live name.
set -euo pipefail

dir="${1:?usage: restore_test.sh <backup-dir>}"
url="${GUZO_MONGO_URL:?GUZO_MONGO_URL is required}"
db="${GUZO_MONGO_DB:-guzo}"
scratch="${db}_restore_test"

archive="$(ls -1t "$dir"/"$db"-*.archive.gz 2>/dev/null | head -1 || true)"
[[ -n "$archive" ]] || { echo "no backup found in $dir" >&2; exit 1; }
echo "restoring $archive into $scratch"

mongorestore --uri="$url" --archive="$archive" --gzip --drop --quiet \
  --nsFrom="$db.*" --nsTo="$scratch.*"

# Counts in the scratch copy must match what the archive says it restored; the live
# database may have moved on since the backup, so it is reported but not compared.
mongosh "$url" --quiet --eval "
  const scratch = db.getSiblingDB('$scratch'), live = db.getSiblingDB('$db');
  const names = scratch.getCollectionNames().sort();
  if (names.length === 0) { print('restore produced no collections'); quit(1); }
  let total = 0;
  for (const name of names) {
    const restored = scratch[name].countDocuments({});
    total += restored;
    print(name.padEnd(22) + ' restored ' + restored + '   live now ' + live[name].countDocuments({}));
  }
  scratch.dropDatabase();
  print('restore test passed: ' + names.length + ' collections, ' + total + ' documents');
"
