# syntax=docker/dockerfile:1

# Только ssh-клиент — сайдкар не обслуживает HTTP и не хранит данные, его
# единственная задача: держать один процесс ssh -N -D наружу (см. DEPLOY.md,
# "AI-провайдер из заблокированной страны" — почему прокси именно так, а не
# Squid/HTTP-proxy на внешнем сервере; приём скопирован из
# book library/DEPLOY.md, "Шаг 9", независимая копия — свой ключ и контейнер).
FROM alpine:3.20
RUN apk add --no-cache openssh-client
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
