FROM python:3.14-alpine3.24 AS builder
RUN pip install uv
COPY . /app
WORKDIR /app
RUN uv build --wheel


FROM python:3.14-alpine3.24

COPY --from=builder /app/dist/civi_api_expor*.whl .
RUN pip install --no-cache civi_api_expor*.whl && \
    rm civi_api_expor*.whl
RUN adduser -D api

USER api
EXPOSE 5000

ENV PYTHONUNBUFFERED=TRUE
CMD ["/usr/local/bin/civi-api-export"]
