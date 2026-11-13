# 构建阶段：装齐依赖（含 typescript）→ tsc 编译 → 只留生产依赖
FROM node:20-alpine AS build
# 依赖镜像源由平台在 prod 构建期经 build args 注入（deploy/prod-compose.yml 的 build.args）；
# ARG 默认值即官方源，所以没配镜像源的部署构建行为不变。
# 运行阶段不装包（连 node_modules 一起从构建阶段搬过来），所以全镜像只有这一处换源位。
ARG NPM_CONFIG_REGISTRY=https://registry.npmjs.org
ENV NPM_CONFIG_REGISTRY=${NPM_CONFIG_REGISTRY}
WORKDIR /srv
COPY package.json ./
RUN npm install
COPY tsconfig.json ./
COPY src ./src
RUN npm run build && npm prune --omit=dev && npm cache clean --force

# 运行阶段：只要 dist 与生产依赖
FROM node:20-alpine
WORKDIR /srv
COPY --from=build /srv ./
ENV APP_PORT=8000
CMD ["sh", "-c", "node dist/index.js"]
