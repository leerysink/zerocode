FROM node:20-alpine AS build
# npm 源由平台在 prod 构建期经 build args 注入（compose 的 environment 对 build 阶段无效），默认官方源
ARG NPM_CONFIG_REGISTRY=https://registry.npmjs.org
ENV NPM_CONFIG_REGISTRY=${NPM_CONFIG_REGISTRY}
WORKDIR /src
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

# uni-app H5：同一个镜像、同一次构建产出 html/h5（ADR-0039：多服务不等于多地址）。
# 独立 stage + 独立 WORKDIR：管理端与移动端是两套 node_modules，混在一起会互相覆盖。
FROM node:20-alpine AS mobile-build
ARG NPM_CONFIG_REGISTRY=https://registry.npmjs.org
ENV NPM_CONFIG_REGISTRY=${NPM_CONFIG_REGISTRY}
WORKDIR /m
COPY mobile/package.json mobile/package-lock.json ./
RUN npm ci
COPY mobile/. .
# 实测：UNI_OUTPUT_DIR 直接生效（默认是 dist/build/h5，这里压平成一层）。路径写死在下面的 COPY 里——
# 若将来 uni 改了环境变量语义，构建会在 COPY 处大声失败，而不是产出一个没有 H5 的镜像。
ENV UNI_OUTPUT_DIR=/m/h5dist
RUN npm run build:h5

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /src/dist /usr/share/nginx/html
COPY --from=mobile-build /m/h5dist /usr/share/nginx/html/h5
