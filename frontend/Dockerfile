# The dashboard is one static page. Caddy serves it; the only build step is
# writing config.js, because the API now lives on a different origin and the
# page has to be told which one at deploy time rather than at build time.
FROM caddy:2.10-alpine

WORKDIR /srv
COPY index.html favicon.svg manifest.webmanifest ./
COPY Caddyfile /etc/caddy/Caddyfile
COPY start.sh /start.sh
RUN chmod +x /start.sh

CMD ["/start.sh"]
