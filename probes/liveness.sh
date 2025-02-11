#!/usr/bin/env bash

# Regardless of anything else...
# the Pod is considered dead if the 'taken.poison' file is present.
POISON_TAKEN_FILE="${HOME}/taken.poison"
if [[ -f "${POISON_TAKEN_FILE}" ]]; then
  echo "Poison taken - time to die!"
  exit 1
fi

# A liveness probe.
# Live if there's a RUNNING file.

if [[ -f "${HOME}/RUNNING" ]]; then
  exit 0
else
  exit 1
fi
