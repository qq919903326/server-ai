FROM python:3.13-slim
WORKDIR /app
COPY database_mcp/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY database_mcp/ database_mcp/
EXPOSE 9001 9002
ENTRYPOINT ["python", "-m", "database_mcp"]
CMD ["--engine", "mysql", "--host", "0.0.0.0", "--port", "9001"]
