# app/deploy/sqlrunner/Dockerfile
# sqlrunner：一次性迁移容器（规格 07 §4.3）。psql 文件级事务 + sqlchange_history 增量执行。
FROM postgres:16-alpine
COPY run.sh /run.sh
RUN chmod +x /run.sh
ENTRYPOINT ["/run.sh"]
