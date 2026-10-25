# syntax=docker/dockerfile:1.10
# Imagem do Nagare Kanban para homologacao e producao da plataforma Pampufa (cofre
# Obsidian grupo-sr, plataforma/arquitetura/implantacao-ambientes.md). Tres estagios:
# telas (web/, npm), dependencias (uv) e execucao (python slim, usuario sem privilegio).
#
# O pampufa-sdk e privado: o token de LEITURA entra como secret do BuildKit e nunca
# fica numa camada (git config por variavel de ambiente, so durante o RUN):
#     docker buildx build --platform linux/arm64 \
#       --secret id=pampufa_sdk_token,env=PAMPUFA_SDK_TOKEN -t nagare:dev .
#
# Fora de dev/teste o `nagare migrar` nao cria as contas de demonstracao; a primeira
# conta vem de `nagare criar-admin --email ... --nome ...`.

ARG PYTHON_IMAGE=python:3.13-slim-bookworm

# --- telas: web/dist --------------------------------------------------------------
FROM node:22-bookworm-slim AS telas
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src/web
COPY web/package.json web/package-lock.json ./
RUN --mount=type=secret,id=pampufa_sdk_token,env=PAMPUFA_SDK_TOKEN,required=true \
    export U="https://x-access-token:${PAMPUFA_SDK_TOKEN}@github.com/phkolbe/pampufa-sdk" \
    GIT_CONFIG_COUNT=3 \
    GIT_CONFIG_VALUE_0=https://github.com/phkolbe/pampufa-sdk \
    GIT_CONFIG_VALUE_1=ssh://git@github.com/phkolbe/pampufa-sdk \
    GIT_CONFIG_VALUE_2=git@github.com:phkolbe/pampufa-sdk \
    && export GIT_CONFIG_KEY_0="url.${U}.insteadOf" GIT_CONFIG_KEY_1="url.${U}.insteadOf" \
    GIT_CONFIG_KEY_2="url.${U}.insteadOf" \
    && npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# --- dependencias: /app/.venv -----------------------------------------------------
FROM ${PYTHON_IMAGE} AS deps
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.11.24 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY backend backend
# Modo editavel: BASE_DIR (config.py) continua sendo /app, onde ficam web/dist e
# backend/migrations.
RUN --mount=type=secret,id=pampufa_sdk_token,env=PAMPUFA_SDK_TOKEN,required=true \
    export GIT_CONFIG_COUNT=1 \
    GIT_CONFIG_KEY_0="url.https://x-access-token:${PAMPUFA_SDK_TOKEN}@github.com/phkolbe/pampufa-sdk.insteadOf" \
    GIT_CONFIG_VALUE_0=https://github.com/phkolbe/pampufa-sdk \
    && uv sync --locked --no-dev --no-cache

# --- execucao ---------------------------------------------------------------------
FROM ${PYTHON_IMAGE}
RUN useradd --uid 10001 --user-group --no-create-home --home-dir /app servico
WORKDIR /app
COPY --from=deps /app /app
COPY --from=telas /src/web/dist web/dist
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1 NAGARE_HOST=0.0.0.0 NAGARE_PORT=8770 \
    NAGARE_DEV=false NAGARE_AUTO_MIGRATE=false NAGARE_IGNORE_DOTENV=1
USER servico
EXPOSE 8770
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"NAGARE_PORT\"]}/api/v1/health', timeout=4)"]
CMD ["nagare", "servir"]
