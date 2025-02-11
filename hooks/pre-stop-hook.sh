#!/usr/bin/env bash

# A container lifecycle hook.
# Called from Kubernetes when the corresponding container is required to stop.
# See https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/#pod-termination

# Here, we create the file '${APP_ROOT}/given.poison'.
touch ${APP_ROOT}/given.poison

# It's the API Pod - there's no need to wait for anything.
# The Pod will be set to TERMINATED and no new traffic will be presented to it.
# We rely on the Pod's 'terminationGracePeriodSeconds' to actually terminate the Pod.
