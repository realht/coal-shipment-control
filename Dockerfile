# ── Stage 1: builder ─────────────────────────────────────────────────────────
# digest зафиксирован 2026-06-14; при обновлении: docker pull python:3.13-slim && обновить оба FROM
FROM python:3.13-slim@sha256:c33f0bc4364a6881bed1ec0cc2665e6c53c87a43e774aaeab88e6f17af105e4f AS builder

# apt-источники запинены на датированный snapshot.debian.org для воспроизводимой сборки.
# snap-дату держать СИНХРОННО с digest базового образа (2026-06-14 → 20260614T000000Z);
# при обновлении digest обновлять и snap. codename берём из образа в build-time
# (не хардкодим), чтобы пережить смену Debian-релиза. debian.sources (deb822) удаляем,
# иначе apt возьмёт оба источника.
# ВНИМАНИЕ: snapshot.debian.org публикует несколько снимков в день; точный T000000Z
# может отсутствовать — валидировать доступный timestamp на Docker-хосте
# (docs/wiki/runbook.md → Supply-chain: SBOM, vuln-scan и apt-snapshot).
RUN set -eux; \
    codename="$(. /etc/os-release && echo "$VERSION_CODENAME")"; \
    snap="20260614T000000Z"; \
    printf 'deb [check-valid-until=no] http://snapshot.debian.org/archive/debian/%s/ %s main\n' "$snap" "$codename" >  /etc/apt/sources.list; \
    printf 'deb [check-valid-until=no] http://snapshot.debian.org/archive/debian/%s/ %s-updates main\n' "$snap" "$codename" >> /etc/apt/sources.list; \
    printf 'deb [check-valid-until=no] http://snapshot.debian.org/archive/debian-security/%s/ %s-security main\n' "$snap" "$codename" >> /etc/apt/sources.list; \
    rm -f /etc/apt/sources.list.d/debian.sources; \
    apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        pkg-config \
        libmariadb-dev \
        nodejs \
        npm \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app

COPY app/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY package.json package-lock.json tailwind.config.js ./
RUN npm ci

COPY app/ .
RUN rm -f ./static/css/tailwind.css
# Browserslist БД в образе может быть старше пакета — подавляем предупреждение
# об устаревших данных; целевой набор браузеров задаётся tailwind.config.js.
ENV BROWSERSLIST_IGNORE_OLD_DATA=1
RUN npx tailwindcss -i ./static/css/tailwind.src.css -o ./static/css/tailwind.css --minify

# ── Stage 2: runtime ─────────────────────────────────────────────────────────
# digest зафиксирован 2026-06-14; при обновлении: docker pull python:3.13-slim && обновить оба FROM
FROM python:3.13-slim@sha256:c33f0bc4364a6881bed1ec0cc2665e6c53c87a43e774aaeab88e6f17af105e4f

# snapshot.debian.org pin — см. пояснение в builder-стейдже; snap-дату держать
# синхронно с digest выше (2026-06-14 → 20260614T000000Z).
RUN set -eux; \
    codename="$(. /etc/os-release && echo "$VERSION_CODENAME")"; \
    snap="20260614T000000Z"; \
    printf 'deb [check-valid-until=no] http://snapshot.debian.org/archive/debian/%s/ %s main\n' "$snap" "$codename" >  /etc/apt/sources.list; \
    printf 'deb [check-valid-until=no] http://snapshot.debian.org/archive/debian/%s/ %s-updates main\n' "$snap" "$codename" >> /etc/apt/sources.list; \
    printf 'deb [check-valid-until=no] http://snapshot.debian.org/archive/debian-security/%s/ %s-security main\n' "$snap" "$codename" >> /etc/apt/sources.list; \
    rm -f /etc/apt/sources.list.d/debian.sources; \
    apt-get update && apt-get install -y --no-install-recommends \
        libmariadb3 \
        mariadb-client \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app

COPY app/ .
COPY --from=builder /app/static/css/tailwind.css ./static/css/tailwind.css

COPY deploy/entrypoint.sh /entrypoint.sh
RUN sed -i 's/\r$//' /entrypoint.sh
RUN chmod 755 /entrypoint.sh

RUN addgroup --system appuser && adduser --system --ingroup appuser appuser
RUN mkdir -p /var/tmp/django_cache && chown -R appuser:appuser /app /var/tmp/django_cache

USER appuser

EXPOSE 8000

ENTRYPOINT ["/entrypoint.sh"]
