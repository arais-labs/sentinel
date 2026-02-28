FROM nginx:1.27-alpine

COPY infra/nginx/gateway.prod.conf /etc/nginx/nginx.conf
COPY infra/portal/index.html /usr/share/nginx/html/index.html
