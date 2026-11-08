FROM node:22-alpine
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY index.html vite.config.ts tsconfig.json ./
COPY src src
EXPOSE 8100
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "8100"]
