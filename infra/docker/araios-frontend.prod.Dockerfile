FROM node:22-alpine AS build

WORKDIR /app
COPY apps/frontend/araios/package*.json ./
RUN npm ci

COPY apps/frontend/araios/ ./

ARG VITE_BASE_PATH=/araios/
ARG VITE_BUILD_OUTDIR=dist
ARG APP_SENTINEL_URL=/sentinel/
ARG APP_ARAIOS_URL=/araios/
RUN VITE_BASE_PATH=$VITE_BASE_PATH \
    VITE_BUILD_OUTDIR=$VITE_BUILD_OUTDIR \
    APP_SENTINEL_URL=$APP_SENTINEL_URL \
    APP_ARAIOS_URL=$APP_ARAIOS_URL \
    npm run build

FROM nginx:1.27-alpine
COPY infra/nginx/frontend-spa.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist/ /usr/share/nginx/html/
