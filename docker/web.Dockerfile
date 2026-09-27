FROM node:24-alpine AS build

WORKDIR /app
COPY package.json ./
COPY apps/web/package.json apps/web/package.json
RUN npm install --no-audit --no-fund

COPY apps/web apps/web
ARG NEXT_PUBLIC_API_BASE_URL=
ENV NEXT_PUBLIC_API_BASE_URL=${NEXT_PUBLIC_API_BASE_URL}
RUN npm run web:build

FROM node:24-alpine AS runtime

ENV NODE_ENV=production
WORKDIR /app

RUN addgroup -g 10001 -S bukmatika \
    && adduser -u 10001 -S -G bukmatika bukmatika

COPY --from=build --chown=bukmatika:bukmatika /app/package.json ./
COPY --from=build --chown=bukmatika:bukmatika /app/node_modules ./node_modules
COPY --from=build --chown=bukmatika:bukmatika /app/apps/web ./apps/web

USER bukmatika
EXPOSE 3000

CMD ["npm", "run", "start", "--workspace", "@bukmatika/web", "--", "--hostname", "0.0.0.0", "--port", "3000"]
