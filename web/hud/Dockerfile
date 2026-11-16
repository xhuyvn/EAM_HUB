# syntax=docker/dockerfile:1
# Multi-stage HUD image: build with Node 26 LTS, serve static files with unprivileged nginx.
# Pinned base images (tags + digests verified 2026-11-17):
#   node:26-alpine
#   nginxinc/nginx-unprivileged:1.27.5-alpine

FROM node:26-alpine@sha256:6bd9ab43c0ec0190ca7b2d193906cb8b7edf6e7f4afa43ddeccd6452710bffb5 AS builder

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY index.html d0.html tsconfig.json vite.config.ts ./
COPY src ./src

RUN npm run build


FROM nginxinc/nginx-unprivileged:1.27.5-alpine@sha256:65e3e85dbaed8ba248841d9d58a899b6197106c23cb0ff1a132b7bfe0547e4c0 AS runtime

COPY --chown=101:101 nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder --chown=101:101 /app/dist /usr/share/nginx/html

USER 101:101

EXPOSE 8080
