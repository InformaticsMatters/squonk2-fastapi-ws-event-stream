# The default base image
ARG from_image=python:3.12.9-alpine3.21
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

##################################################################
#
# Second stage, poetry installation.
#
##################################################################
FROM python-base AS poetry-base
ARG POETRY_VERSION=1.8.5

RUN pip install --no-cache-dir poetry==${POETRY_VERSION}

WORKDIR /
COPY poetry.lock pyproject.toml /

# Despite letting poetry create virtualenv here, we're not using it in
# the final container, we just let poetry install the packages and
# copy them later. POETRY_VIRTUALENVS_IN_PROJECT flag tells poetry to
# create the venv to project's directory (.venv). This way the
# location is predictable
RUN POETRY_VIRTUALENVS_IN_PROJECT=true poetry install --no-root --only main --no-directory

##################################################################
#
# Final stage.
# Only copy the venv with installed packages and point paths to it
#
##################################################################
FROM python-base AS final

COPY --from=poetry-base /.venv /.venv

ENV PYTHONPATH="/.venv/lib/python3.13/site-packages/"
ENV PATH=/.venv/bin:$PATH

COPY app/ ./app/
COPY logging.config .
COPY docker-entrypoint.sh .
COPY internal.env ./
COPY public.env ./

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
