# Admin console: static build served by unprivileged nginx with strict headers.
#   docker build --build-arg VITE_API_BASE=https://api.staging.example.invalid -t hela-admin:dev apps/admin
FROM node:22.21.1-bookworm-slim AS build
WORKDIR /src
RUN corepack enable && corepack prepare pnpm@10.13.1 --activate
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY . .
ARG VITE_API_BASE
RUN test -n "$VITE_API_BASE" || (echo "VITE_API_BASE is required" >&2; exit 1)
ENV VITE_API_BASE=$VITE_API_BASE
RUN pnpm build

FROM nginxinc/nginx-unprivileged:1.29.3-alpine
COPY nginx.conf /etc/nginx/templates/default.conf.template
COPY --from=build /src/dist /usr/share/nginx/html
EXPOSE 8080
