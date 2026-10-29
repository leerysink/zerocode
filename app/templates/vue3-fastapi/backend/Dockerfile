FROM python:3.12-slim
# 依赖镜像源由平台在 prod 构建期经 build args 注入（deploy/prod-compose.yml 的 build.args）；
# ARG 默认值即官方源，所以没配镜像源的部署构建行为不变。
ARG PIP_INDEX_URL=https://pypi.org/simple
ENV PIP_INDEX_URL=${PIP_INDEX_URL}
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
ENV APP_PORT=8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${APP_PORT}"]
