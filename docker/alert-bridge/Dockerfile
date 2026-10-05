# alert-bridge — 零依赖告警转发桥（AM webhook → 企微/飞书/钉钉）
FROM python:3.12-alpine
WORKDIR /app
COPY alert_bridge.py /app/alert_bridge.py
# COPY 先依赖后代码：脚本即代码，单文件无可缓存层收益，保持最小
EXPOSE 9094
CMD ["python", "-u", "/app/alert_bridge.py"]
