# Gridora agent — self-hosted, non-custodial (YOUR TWAK wallet, YOUR keys).
#
# Default CMD runs PAPER mode: real live prices, simulated fills, NO real money.
# All config comes from the environment at runtime (compose `env_file` / `-e`);
# nothing secret is ever baked into this image (.env is .dockerignore'd).
#
#   docker build -t gridora-agent backend/
#   docker run --env-file backend/.env -p 8317:8317 gridora-agent

# ── Node 22 runtime for the Trust Wallet Agent Kit CLI (`twak`) ───────────────
# Grafted from the official image instead of apt (bookworm apt ships Node 18,
# too old for an actively developed CLI). Both bases pinned to bookworm so the
# node binary and python image share one glibc.
FROM node:22-bookworm-slim AS node

FROM python:3.11-slim-bookworm

COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
 # TWAK = the sole signer + execution layer; keys never leave this container
 && npm install -g @trustwallet/cli \
 && npm cache clean --force

# ── Gridora backend ───────────────────────────────────────────────────────────
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .
# The official 149-token competition allowlist, bundled at a fixed path so the
# installed package finds it regardless of the working directory.
COPY allowlist.149.json /app/allowlist.149.json

ENV PYTHONUNBUFFERED=1 \
    GRIDORA_ALLOWLIST_FILE=/app/allowlist.149.json \
    # Bind inside the container; HOST exposure is decided by the compose
    # `ports` mapping (map to 127.0.0.1 and put TLS in front on a public VPS).
    GRIDORA_CONTROL_HOST=0.0.0.0

# Run from the data dir so run artifacts (paper-trading sqlite db) land in the
# same persistent volume as the control-plane files (autopilot.json, trades.jsonl).
# TWAK's own wallet/config lives in /root/.twak — mount a volume there too.
WORKDIR /root/.gridora

EXPOSE 8317

# SAFE default: paper trading (simulated fills, no real money) + the control API.
# Live mode is an explicit `command:` override in docker-compose.yml — see the
# loud warning there before you flip it.
CMD ["python", "-m", "gridora.runner", "--mode", "paper", "--serve", "8317"]
