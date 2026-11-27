# syntax=docker/dockerfile:1
# Multi-stage frontend image: Node (the version CI builds with) bundles the Vite
# app, an unprivileged nginx serves it and proxies the API to the backend.

FROM node:26-alpine AS builder

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN npm run build

# --- Stage 2: Nginx (unprivileged image: runs as user nginx, listens on 8080) ---
FROM nginxinc/nginx-unprivileged:1.27-alpine AS runner

USER root

RUN rm -rf /usr/share/nginx/html/*

COPY --from=builder /app/dist /usr/share/nginx/html

COPY nginx.conf /etc/nginx/conf.d/default.conf
USER nginx

EXPOSE 8080

HEALTHCHECK --interval=15s --timeout=5s --retries=3 \
  CMD wget -q --spider http://localhost:8080/ || exit 1

CMD ["nginx", "-g", "daemon off;"]
