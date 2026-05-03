# Node builder
FROM node:22 AS build
WORKDIR /app
COPY src/package*.json ./
RUN npm install
COPY src .
RUN npm run build

# Server
FROM python:3.14-slim
LABEL maintainer="github.com/itskovacs"
LABEL description="Minimalist personal portfolio tracker"
WORKDIR /app
COPY backend .
RUN pip install uv
RUN uv pip install --no-cache-dir -r requirements.txt --system
COPY --from=build /app/dist/stonks/browser frontend
EXPOSE 8000
CMD ["fastapi", "run", "/app/main.py", "--host", "0.0.0.0", "--port", "8000"]