A directory mounted into the local compose rabbitmq that enables the
built-in rabbitmq_stream, and rabbitmq_stream_management plugins.
This directory is mounted into the docker container and
the rabbitmq variable RABBITMQ_ENABLED_PLUGINS_FILE is set to the
location of the "enabled_plugins" file when mounted.
