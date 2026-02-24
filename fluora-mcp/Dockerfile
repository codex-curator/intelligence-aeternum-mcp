FROM node:22-slim
WORKDIR /app
COPY package.json package-lock.json monetizedmcp-sdk-0.1.23.tgz ./
RUN npm ci
COPY dist/ ./dist/
ENV NODE_ENV=production
ENV PORT=8080
EXPOSE 8080
CMD ["node", "dist/main.js"]
