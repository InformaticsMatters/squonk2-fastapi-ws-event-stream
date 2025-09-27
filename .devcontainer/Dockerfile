FROM mcr.microsoft.com/devcontainers/python:1-3.13-bullseye

# The platform (CPU architecture)
# if not provided by a build-arg we take a default (ARM)
ARG PLATFORM=amd64
ENV PLATFORM=$PLATFORM

# Install our main Python requirements
# and kubectl
ARG KUBECTL_VERSION=1.32.8
COPY requirements.txt /tmp
RUN pip install -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt \
    && curl -LO "https://dl.k8s.io/release/v${KUBECTL_VERSION}/bin/linux/${PLATFORM}/kubectl" \
    && install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl
