# ---------- 依赖层 ----------
FROM node:22-slim AS deps
WORKDIR /app
RUN corepack enable
COPY package.json pnpm-lock.yaml* ./
RUN pnpm install --frozen-lockfile || pnpm install

# ---------- 构建层 ----------
FROM node:22-slim AS builder
WORKDIR /app
RUN corepack enable
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# 前端在浏览器里跑，所以请求走相对路径 /api/*，
# 由 next.config.ts 的 rewrites 代理到这个地址（容器网络内的后端）
ENV NEXT_PUBLIC_API_BASE_URL=http://backend:8000
RUN pnpm build

# ---------- 运行层 ----------
FROM node:22-slim AS runner
WORKDIR /app
RUN corepack enable
ENV NODE_ENV=production TZ=Asia/Shanghai
COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app/.next ./.next
COPY --from=builder /app/public ./public
COPY --from=builder /app/package.json ./package.json
COPY --from=builder /app/next.config.ts ./next.config.ts
EXPOSE 3000
CMD ["pnpm", "start"]
