FROM node:20-alpine AS build
# 同 backend/Dockerfile：npm 源由平台在 prod 构建期经 build args 注入，默认官方源
ARG NPM_CONFIG_REGISTRY=https://registry.npmjs.org
ENV NPM_CONFIG_REGISTRY=${NPM_CONFIG_REGISTRY}
WORKDIR /src
COPY package.json ./
RUN npm install
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /src/dist /usr/share/nginx/html
