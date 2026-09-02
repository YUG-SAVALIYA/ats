# ===============================================================================
# Stage 1: Build React SPA bundle with Node 20
# ===============================================================================
FROM node:20-alpine AS builder

WORKDIR /app

# Accept production backend URL at build time (defaults to production API domain)
ARG VITE_BACKEND_URL=https://trading-b.aistocksagent.com
ENV VITE_BACKEND_URL=$VITE_BACKEND_URL

# Copy package files and install dependencies
COPY package*.json ./
RUN npm ci

# Copy source code and build production bundle
COPY . .
RUN npm run build

# ===============================================================================
# Stage 2: Serve production bundle with Nginx Alpine (Self-Contained)
# ===============================================================================
FROM nginx:alpine

# Configure Nginx SPA routing (try_files $uri $uri/ /index.html) inline
RUN printf 'server {\n\
    listen 80;\n\
    server_name _;\n\
    root /usr/share/nginx/html;\n\
    index index.html;\n\
    location / {\n\
        try_files $uri $uri/ /index.html;\n\
    }\n\
}\n' > /etc/nginx/conf.d/default.conf

# Copy built assets from builder stage
COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
