# syntax=docker/dockerfile:1
FROM maven:3.9-eclipse-temurin-17 AS build
WORKDIR /build
COPY pom.xml ./
COPY src ./src
# BuildKit 缓存挂载：同机跨版本复用依赖，首次之后的构建不再重下（冷启动 410s 的来源就是下载）
RUN --mount=type=cache,target=/root/.m2 mvn -B -DskipTests package

FROM eclipse-temurin:17-jre-alpine
WORKDIR /app
COPY --from=build /build/target/*.jar app.jar
ENV SERVER_PORT=8080 UPLOAD_DIR=/uploads
EXPOSE 8080
ENTRYPOINT ["java", "-jar", "/app/app.jar"]
