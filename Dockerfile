# =============================================================================
# Stage 1: Collect Django static files
# =============================================================================
FROM ghcr.io/astral-sh/uv:python3.14-trixie-slim AS django-static

WORKDIR /app

# Build-time host used by render_nginx_conf (can be overridden by compose build args)
ARG NGINX_PROXY_HOST

# Install dependencies first (layer cache)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Copy backend source
COPY backend/ ./backend/
COPY nginx/default.conf.j2 ./nginx/default.conf.j2

# collectstatic needs STATIC_ROOT; set it and run without localhostDB access
ENV DJANGO_STATIC_ROOT=/app/staticfiles
ENV DJANGO_SETTINGS_MODULE=project.settings
ENV NGINX_PROXY_HOST=${NGINX_PROXY_HOST}
# Disable debug so collectstatic doesn't try settings.debug.toml
ENV DJANGO_DEBUG=0
# Provide a throw-away secret key so Django boots for collectstatic
ENV DJANGO_SECRET_KEY=collectstatic-build-key
# Skip OIDC discovery during build
ENV DJANGO_OIDC_DISCOVERY_URL=""

WORKDIR /app/backend
RUN uv run --no-group dev python manage.py render_nginx_conf
RUN mkdir -p /app/staticfiles && uv run --no-group dev python manage.py collectstatic --noinput

# =============================================================================
# Stage 2: Build the Vite / React frontend
# =============================================================================
FROM node:24-trixie-slim AS frontend-build

WORKDIR /app

ARG VITE_HIDE_PASSWORD_AUTH
ENV VITE_HIDE_PASSWORD_AUTH=${VITE_HIDE_PASSWORD_AUTH}

COPY package.json package-lock.json ./
RUN npm ci

COPY index.html tsconfig.json tsconfig.app.json tsconfig.node.json vite.config.ts eslint.config.js ./
COPY src/ ./src/
COPY public/ ./public/

RUN npm run build

# =============================================================================
# Stage 3: nginx – serves SPA + static, proxies /api → backend
# =============================================================================
FROM nginx:stable-alpine AS nginx

# Remove default config
RUN rm /etc/nginx/conf.d/default.conf

# Rendered config generated in django-static stage
COPY --from=django-static /app/nginx/default.conf /etc/nginx/conf.d/default.conf

# Vite build output → nginx html root
COPY --from=frontend-build /app/dist /usr/share/nginx/html

# Django collectstatic output → /usr/share/nginx/static
COPY --from=django-static /app/staticfiles /usr/share/nginx/static

EXPOSE 80

# =============================================================================
# Stage 4: Django application server
# =============================================================================
FROM ghcr.io/astral-sh/uv:python3.14-trixie-slim AS backend

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends graphviz \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
RUN mkdir -p /app/prometheus_multiproc
COPY backend/ ./backend/

ENV DJANGO_SETTINGS_MODULE=project.settings

WORKDIR /app/backend

EXPOSE 8000

CMD ["uv", "run", "--no-group", "dev", "gunicorn", "project.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--worker-class", "gthread", \
     "--workers", "1", \
     "--threads", "16", \
     "--timeout", "120" , "--log-level", "debug"]





