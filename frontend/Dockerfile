# Собирает SPA и кладёт её в том, который отдаёт Caddy.
FROM node:22-alpine AS build

WORKDIR /build

COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --no-audit --no-fund

COPY frontend/ ./
RUN npm run build

FROM alpine:3.20
COPY --from=build /build/dist /dist-src
# Том монтируется в /dist на старте, поэтому копируем в него в рантайме.
CMD ["sh", "-c", "rm -rf /dist/* && cp -r /dist-src/. /dist/ && echo 'SPA собрана'"]
