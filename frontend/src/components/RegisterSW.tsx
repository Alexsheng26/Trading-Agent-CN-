"use client";

import { useEffect } from "react";

/** 注册 Service Worker，让应用可安装到主屏。开发模式下跳过，免得缓存干扰热更新。 */
export function RegisterSW() {
  useEffect(() => {
    if (process.env.NODE_ENV !== "production") return;
    if (!("serviceWorker" in navigator)) return;

    const register = () => {
      navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch((err) => {
        console.warn("Service Worker 注册失败：", err);
      });
    };

    // 等页面加载完再注册，不抢首屏资源
    if (document.readyState === "complete") register();
    else window.addEventListener("load", register, { once: true });
  }, []);

  return null;
}
