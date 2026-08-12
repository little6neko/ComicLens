#!/bin/sh
set -eu

mkdir -p /app/data
chown comiclens:comiclens /app/data

if { [ -e /app/data/secrets.key ] && ! gosu comiclens test -r /app/data/secrets.key; } \
  || { [ -e /app/data/comiclens.db ] && ! gosu comiclens test -w /app/data/comiclens.db; }; then
  chown -R comiclens:comiclens /app/data
fi

exec gosu comiclens "$@"
