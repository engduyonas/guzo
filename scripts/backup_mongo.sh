#!/usr/bin/env bash
# Dump the Guzo database to a compressed archive and prune old ones.
#
#   GUZO_MONGO_URL=... GUZO_MONGO_DB=guzo scripts/backup_mongo.sh /var/backups/guzo
#
# Needs mongodump (MongoDB Database Tools). Run it daily from the host's scheduler,
# and copy the directory somewhere off the machine: a backup next to the database
# does not survive losing the machine.
set -euo pipefail

dir="${1:?usage: backup_mongo.sh <backup-dir>}"
url="${GUZO_MONGO_URL:?GUZO_MONGO_URL is required}"
db="${GUZO_MONGO_DB:-guzo}"
keep_days="${GUZO_BACKUP_KEEP_DAYS:-14}"

mkdir -p "$dir"
archive="$dir/$db-$(date -u +%Y%m%dT%H%M%SZ).archive.gz"
# Write to a temporary name first, so a failed dump never looks like a good backup.
mongodump --uri="$url" --db="$db" --archive="$archive.partial" --gzip --quiet
mv "$archive.partial" "$archive"
find "$dir" -name "$db-*.archive.gz" -mtime "+$keep_days" -delete
echo "$archive"
