FROM mcr.microsoft.com/devcontainers/python:3.12-bookworm

WORKDIR /workspaces/clearwave

COPY requirements.txt requirements.txt
COPY detector/requirements.txt detector/requirements.txt
COPY worker/requirements.txt worker/requirements.txt
RUN pip install --no-cache-dir \
      -r requirements.txt \
      -r detector/requirements.txt \
      -r worker/requirements.txt
