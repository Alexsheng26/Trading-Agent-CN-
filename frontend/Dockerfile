# pnpm 版本必须和 pnpm-lock.yaml 的生成版本一致。
# 不钉版本的话 corepack 会装最新版 pnpm，而新版带了 minimumReleaseAge
# 供应链策略（拒绝刚发布不久的包），会直接判定本仓库的 lockfile 不合法。
ARG PNPM_VERSION=10.33.0

# ---------- 依赖层 ----------
FROM node:22-slim AS deps
ARG PNPM_VERSION
WORKDIR /app
# 非交互环境下 corepack 下载时不要弹确认
ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0
RUN corepack enable && corepack prepare pnpm@${PNPM_VERSION} --activate
COPY package.json pnpm-lock.yaml ./
# 刻意不加 `|| pnpm install` 兜底：那会在 lockfile 有问题时静默安装另一套版本，
# 把真正的错误藏起来。宁可构建失败并给出明确报错。
RUN pnpm install --frozen-lockfile

# ---------- 构建层 ----------
FROM node:22-slim AS builder
ARG PNPM_VERSION
WORKDIR /app
ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0
RUN corepack enable && corepack prepare pnpm@${PNPM_VERSION} --activate
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# 前端在浏览器里跑，请求走相对路径 /api/*，
# 由 next.config.ts 的 rewrites 代理到这个地址（容器网络内的后端）
ENV NEXT_PUBLIC_API_BASE_URL=http://backend:8000
RUN pnpm build

# ---------- 运行层 ----------
FROM node:22-slim AS runner
ARG PNPM_VERSION
WORKDIR /app
ENV NODE_ENV=production TZ=Asia/Shanghai
ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0
RUN corepack enable && corepack prepare pnpm@${PNPM_VERSION} --activate
COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app/.next ./.next
COPY --from=builder /app/public ./public
COPY --from=builder /app/package.json ./package.json
COPY --from=builder /app/next.config.ts ./next.config.ts
EXPOSE 3000
CMD ["pnpm", "start"]
