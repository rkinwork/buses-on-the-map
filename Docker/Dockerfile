FROM python:3.10.4-slim as base


FROM base as server_base
COPY requirements.txt requirements.txt
RUN pip3 install -r requirements.txt
COPY src src
WORKDIR src
ENV PYTHONPATH "${PYTHONPATH}:/src"

FROM base as frontend
WORKDIR web
COPY frontend/index.html index.html
