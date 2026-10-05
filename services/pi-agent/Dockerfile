FROM node:22.23.0-bookworm-slim
WORKDIR /app
COPY package.json package-lock.json ./
COPY vendor/pi ./vendor/pi
COPY vendor/pi-model-data ./vendor/pi-model-data
RUN npm ci --ignore-scripts --legacy-peer-deps --no-audit --no-fund
COPY scripts/build-pi.mjs ./scripts/build-pi.mjs
RUN node scripts/build-pi.mjs
COPY services/pi-agent ./services/pi-agent
USER node
CMD ["node", "services/pi-agent/server.mjs"]
