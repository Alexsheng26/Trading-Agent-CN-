// Service Worker —— 让应用能装到手机主屏，并在弱网/断网时不至于白屏。
//
// 缓存策略（刻意保守，金融数据不能给用户看旧的）：
//   - **页面导航：网络优先**，取不到才用缓存兜底。
//     这一条很重要：如果 HTML 也走"先给缓存"，升级之后第一次打开拿到的是
//     旧 HTML，而旧 HTML 里引用的是旧的带哈希的 JS 文件名 —— 重新构建后
//     那些文件已经不存在了，于是要么看到旧界面，要么直接 404 白屏。
//     换成网络优先之后，只要能联网，打开就一定是新版本。
//   - /_next/static/**：缓存优先。文件名带内容哈希，内容永不改变，
//     可以放心长期缓存，这也是秒开的来源。
//   - 其余静态资源（图标、manifest）：stale-while-revalidate
//   - /api/** 一律不缓存：行情和分析结果必须是实时的，
//     宁可报错也不能拿昨天的价格糊弄用户
//   - 完全离线时，导航请求回落到离线提示页
//
// 改了缓存策略就要把 VERSION 往上加，activate 里会把旧缓存整个删掉。

const VERSION = "v2";
const SHELL_CACHE = `shell-${VERSION}`;

const SHELL_ASSETS = [
  "/",
  "/analyze",
  "/runs",
  "/settings",
  "/manifest.webmanifest",
  "/icon.svg",
  "/icon-192.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(SHELL_CACHE).then((cache) =>
      // 个别资源取不到不应阻塞安装
      Promise.allSettled(SHELL_ASSETS.map((url) => cache.add(url))),
    ),
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(keys.filter((k) => k !== SHELL_CACHE).map((k) => caches.delete(k))),
      )
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  // 接口请求绝不走缓存 —— 旧行情比没行情更危险
  if (url.pathname.startsWith("/api/")) return;

  // SSE 也不能碰
  if (request.headers.get("accept")?.includes("text/event-stream")) return;

  // 带内容哈希的构建产物：内容不可能变，缓存优先
  const immutable = url.pathname.startsWith("/_next/static/");
  // 页面导航：网络优先，保证升级后打开就是新版本
  const navigation = request.mode === "navigate";

  event.respondWith(
    (async () => {
      const cache = await caches.open(SHELL_CACHE);

      const fromNetwork = async () => {
        try {
          const response = await fetch(request);
          if (response.ok && response.type === "basic") {
            void cache.put(request, response.clone());
          }
          return response;
        } catch {
          return null;
        }
      };

      if (navigation) {
        const fresh = await fromNetwork();
        if (fresh) return fresh;
        // 断网：用缓存的外壳，实在没有就给离线页
        const cached = await cache.match(request, { ignoreSearch: true });
        if (cached) return cached;
        return new Response(OFFLINE_HTML, {
          status: 503,
          headers: { "Content-Type": "text/html; charset=utf-8" },
        });
      }

      const cached = await cache.match(request);
      if (cached) {
        // 不可变资源没有"更新"一说，不必回源；其余的后台顺便刷一下
        if (!immutable) void fromNetwork();
        return cached;
      }

      const fresh = await fromNetwork();
      return fresh ?? new Response("", { status: 504 });
    })(),
  );
});

const OFFLINE_HTML = `<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>离线</title>
<style>
  body{margin:0;min-height:100vh;display:grid;place-items:center;
    background:#0b0e14;color:#e6e9ef;font-family:-apple-system,"PingFang SC",sans-serif;padding:24px}
  .box{text-align:center;max-width:22rem}
  h1{font-size:1.05rem;margin:0 0 .6rem}
  p{font-size:.85rem;line-height:1.7;color:#8b93a7;margin:0}
  button{margin-top:1.4rem;background:#5b8def;color:#fff;border:0;
    border-radius:.5rem;padding:.7rem 1.4rem;font-size:.85rem}
</style></head>
<body><div class="box">
  <h1>当前无网络连接</h1>
  <p>行情和分析需要联网获取。为避免展示过期数据，本应用不会缓存任何行情。</p>
  <button onclick="location.reload()">重新加载</button>
</div></body></html>`;
