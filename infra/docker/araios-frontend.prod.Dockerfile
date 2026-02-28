FROM node:22-alpine AS build

WORKDIR /app
COPY apps/frontend/araios/package*.json ./
RUN npm ci

COPY apps/frontend/araios/ ./

ARG VITE_BASE_PATH=/araios/
ARG VITE_BUILD_OUTDIR=dist
RUN VITE_BASE_PATH=$VITE_BASE_PATH VITE_BUILD_OUTDIR=$VITE_BUILD_OUTDIR npm run build

FROM nginx:1.27-alpine
COPY infra/nginx/frontend-spa.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist/ /usr/share/nginx/html/
