FROM python:3.14-alpine3.24 AS builder
RUN pip install uv
COPY . /app
WORKDIR /app
RUN uv build --wheel


FROM python:3.14-alpine3.24

ENV PYTHONUNBUFFERED=TRUE

RUN adduser -D api

COPY --from=builder /app/dist/civi_api_expor*.whl .
RUN pip install civi_api_expor*.whl && \
    rm civi_api_expor*.whl

USER api
EXPOSE 5000
CMD /bin/sh -c "/usr/local/bin/civi-api-export"
