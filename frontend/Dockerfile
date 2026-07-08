FROM node:22-alpine

WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm install

COPY index.html vite.config.js .
COPY src ./src

EXPOSE 3000

CMD ["npm", "run", "dev", "--", "--port", "3000"]
