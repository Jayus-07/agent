# 业务系统镜像：Maven 构建 + JRE 运行（多阶段）
FROM maven:3.9-eclipse-temurin-17 AS build
WORKDIR /build
# 先拷依赖描述利用 Docker 层缓存
COPY pom.xml .
RUN mvn -q dependency:go-offline -DskipTests
COPY src ./src
RUN mvn -q package -DskipTests

FROM eclipse-temurin:17-jre-alpine
WORKDIR /app
RUN addgroup -S app && adduser -S app -G app
COPY --from=build /build/target/business-service-1.0.0.jar app.jar
USER app
EXPOSE 8081
HEALTHCHECK --interval=15s --timeout=3s --retries=5 \
  CMD wget -qO- http://localhost:8081/health || exit 1
ENTRYPOINT ["java", "-jar", "app.jar"]
