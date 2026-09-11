# syntax=docker/dockerfile:1
# Multi-stage production build for Vite React Frontend

# --- Stage 1: Build Web Assets ---
FROM node:20-alpine AS builder

WORKDIR /app

# Install dependencies using clean install
COPY package.json package-lock.json ./
RUN npm ci

# Copy source code and build production bundle
COPY . .
RUN npm run build

# --- Stage 2: High-Performance Nginx Web Server ---
FROM nginx:1.27-alpine AS runner

# Remove default Nginx website
RUN rm -rf /usr/share/nginx/html/*

# Copy built assets from builder stage
COPY --from=builder /app/dist /usr/share/nginx/html

# Copy custom Nginx configuration with SPA fallback and API proxy
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

HEALTHCHECK --interval=15s --timeout=5s --retries=3 \
  CMD wget -q --spider http://localhost:80/ || exit 1

CMD ["nginx", "-g", "daemon off;"]
