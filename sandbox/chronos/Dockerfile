ARG BASE_IMAGE
FROM ${BASE_IMAGE}
USER root
ARG APSCHEDULER=
RUN apk add --no-cache libfaketime procps \
    && lib="$(find / -name 'libfaketime.so.1' -not -path '*/proc/*' 2>/dev/null | head -n 1)" \
    && test -n "$lib" \
    && ln -sf "$lib" /usr/lib/libfaketime.so.1 \
    && python -m pip install --no-cache-dir py-spy \
    && if [ -n "$APSCHEDULER" ]; then python -m pip install --no-cache-dir "apscheduler==$APSCHEDULER"; fi \
    && python -c "import apscheduler; print('apscheduler', __import__('importlib.metadata').metadata.version('apscheduler'))"
