# Static SPA served by nginx; /api is proxied to the api container (see nginx.conf).
FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
ARG VITE_API_BASE=""
ENV VITE_API_BASE=$VITE_API_BASE
RUN npm run build

FROM nginx:1.27-alpine
# nginx image renders /etc/nginx/templates/*.template with envsubst at start
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
ENV API_UPSTREAM=api:8000
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
