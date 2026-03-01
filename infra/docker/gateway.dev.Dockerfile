FROM nginx:1.27-alpine

COPY infra/nginx/gateway.dev.conf /etc/nginx/nginx.conf
COPY infra/portal/index.html /usr/share/nginx/html/index.html
