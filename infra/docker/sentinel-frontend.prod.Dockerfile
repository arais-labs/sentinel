FROM node:22-alpine AS build

WORKDIR /app
COPY apps/frontend/sentinel/package*.json ./
RUN npm ci

COPY apps/frontend/sentinel/ ./

ARG VITE_BASE_PATH=/sentinel/
ARG VITE_ROUTER_BASENAME=/sentinel
ARG VITE_SENTINEL_API_BASE_URL=/sentinel/api/v1
RUN VITE_BASE_PATH=$VITE_BASE_PATH \
    VITE_ROUTER_BASENAME=$VITE_ROUTER_BASENAME \
    VITE_SENTINEL_API_BASE_URL=$VITE_SENTINEL_API_BASE_URL \
    npx vite build

FROM nginx:1.27-alpine
COPY infra/nginx/frontend-spa.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist/ /usr/share/nginx/html/
