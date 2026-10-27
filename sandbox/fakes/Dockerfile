FROM node:24.16.0-alpine
WORKDIR /fake
RUN npm init -y >/dev/null && npm install --no-audit --no-fund ws@8.18.0 >/dev/null
COPY fake-corehub.mjs ./fake-corehub.mjs
HEALTHCHECK --interval=3s --timeout=2s --start-period=3s --retries=10 CMD test -f /tmp/ready
ENTRYPOINT ["node", "/fake/fake-corehub.mjs"]
