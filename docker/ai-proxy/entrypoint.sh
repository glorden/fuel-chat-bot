#!/bin/sh
set -eu

# Смонтированный ключ может унаследовать права хоста (например, 644) —
# ssh-клиент отказывается использовать приватный ключ, доступный на чтение
# группе/остальным ("Permissions ... are too open"). Копируем во writable
# путь внутри контейнера и выставляем 600 сами, а не полагаемся на биндмаунт.
cp /keys/id_ed25519 /tmp/id_ed25519
chmod 600 /tmp/id_ed25519

# exec — ssh становится PID 1 и получает SIGTERM от `docker compose stop`
# напрямую, без обёрточного шелл-процесса.
exec ssh -N \
  -o "ExitOnForwardFailure=yes" \
  -o "ServerAliveInterval=30" \
  -o "ServerAliveCountMax=3" \
  -o "ConnectTimeout=10" \
  -o "StrictHostKeyChecking=yes" \
  -o "UserKnownHostsFile=/keys/known_hosts" \
  -i /tmp/id_ed25519 \
  -p "${TUNNEL_SSH_PORT:-22}" \
  -D 0.0.0.0:1080 \
  "${TUNNEL_SSH_USER:-tunneluser}@${TUNNEL_SSH_HOST}"
