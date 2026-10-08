import type { NextConfig } from "next";

const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // 钉住项目根目录。不钉的话，上层目录里碰巧有个 package-lock.json（比如在用户目录里
  // 装过 npm 包），Next 会把那里认成工作区根目录，每次构建、启动都打一段吓人的警告
  outputFileTracingRoot: __dirname,
  experimental: {
    // 转发 /api/* 时，Next 默认后端 30 秒不吐一个字节就掐断、回 500。
    // 「数据源诊断」「重建目录」这类接口本来就要一两分钟 —— 用户机器上实测：
    // 点「开始探测」只看到 HTTP 500。放宽到 10 分钟（分析的实时进度流自带心跳，不受影响）
    proxyTimeout: 10 * 60_000,
  },
  // 把 /api/* 代理到后端：这样手机上只需要访问前端一个地址，
  // 不用操心 CORS，也不用在手机里单独配后端 IP。
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${apiBase}/api/:path*` }];
  },
  async headers() {
    return [
      {
        source: "/sw.js",
        headers: [
          { key: "Cache-Control", value: "no-cache, no-store, must-revalidate" },
          { key: "Service-Worker-Allowed", value: "/" },
        ],
      },
    ];
  },
};

export default nextConfig;
