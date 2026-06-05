# syntax=docker/dockerfile:1

# ---------- Stage 1: dependencies ----------
FROM node:24-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci

# ---------- Stage 2: build ----------
FROM node:24-alpine AS builder
WORKDIR /app

# Build-time config. The rewrite proxy target (BACKEND_ORIGIN) is resolved when
# `next build` evaluates next.config, so it must be set here. NEXT_PUBLIC_* is an
# optional proxy-bypass override (also baked at build). Pass with --build-arg.
ARG BACKEND_ORIGIN=http://backend:8000
ENV BACKEND_ORIGIN=${BACKEND_ORIGIN}
ARG NEXT_PUBLIC_API_BASE_URL=
ENV NEXT_PUBLIC_API_BASE_URL=${NEXT_PUBLIC_API_BASE_URL}

COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

# ---------- Stage 3: runner ----------
FROM node:24-alpine AS runner
WORKDIR /app

ENV NODE_ENV=production
ENV PORT=4000
ENV HOSTNAME=0.0.0.0

# Copy the standalone output. public/ is optional; create a placeholder so the
# COPY below always has something to copy.
COPY --from=builder /app/public ./public
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static

EXPOSE 4000
CMD ["node", "server.js"]
