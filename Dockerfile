# The default base image
ARG from_image=python:3.13.11-alpine3.23
FROM ${from_image} AS python-base

# Labels
LABEL maintainer='Alan Christie <achristie@informaticsmatters.com>'

# Force the binary layer of the stdout and stderr streams
# to be unbuffered
ENV PYTHONUNBUFFERED=1

# Base directory for the application
# Also used for user directory
ENV APP_ROOT=/home/es

WORKDIR ${APP_ROOT}

# Add tools, like gcc
RUN apk add --no-cache \
      build-base=0.5-r3

##################################################################
#
# Second stage, uv installation.
#
##################################################################
FROM python-base AS uv-base
ARG UV_VERSION=0.11.21

RUN pip install --no-cache-dir uv==${UV_VERSION}

WORKDIR /
COPY uv.lock pyproject.toml .python-version /

# We're not using uv in the final container, we just let it install the
# packages and copy them later. UV_PROJECT_ENVIRONMENT puts the venv at a
# predictable location (/.venv). The image already provides the interpreter
# we want, so uv must use that rather than fetching a managed one -
# there are no managed builds for musl on every architecture we build for.
ENV UV_PYTHON_DOWNLOADS=never
RUN UV_PROJECT_ENVIRONMENT=/.venv \
    uv sync --frozen --no-dev --no-install-project --python python3.13

##################################################################
#
# Final stage.
# Only copy the venv with installed packages and point paths to it
#
##################################################################
FROM python-base AS final

COPY --from=uv-base /.venv /.venv

ENV PYTHONPATH="/.venv/lib/python3.13/site-packages/"
ENV PATH=/.venv/bin:$PATH

COPY app/ ./app/
COPY logging.config .
COPY docker-entrypoint.sh .
COPY internal.env .
COPY public.env .
COPY VERSION .

# Probes...
COPY probes/*.sh .
# Kubernetes lifecycle hooks...
COPY hooks/*.sh .

# Create a database directory
WORKDIR /data
# Create a base directory for file-based logging
WORKDIR /logs

# Switch to container user
ENV HOME=${APP_ROOT}
WORKDIR ${APP_ROOT}

# Start the application
CMD ["./docker-entrypoint.sh"]
