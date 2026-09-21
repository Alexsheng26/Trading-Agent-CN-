import type { Metadata, Viewport } from "next";
import "./globals.css";
import { AppShell } from "@/components/AppShell";
import { RegisterSW } from "@/components/RegisterSW";

export const metadata: Metadata = {
  title: "A股投研助手",
  description: "基于 AI 多智能体的 A股投研分析工具，电脑和手机都能用",
  manifest: "/manifest.webmanifest",
  appleWebApp: {
    capable: true,
    statusBarStyle: "black-translucent",
    title: "A股投研",
  },
  icons: {
    icon: "/icon.svg",
    apple: "/icon-apple.png",
  },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  // 允许缩放：行情表格在小屏上用户可能想放大看
  maximumScale: 5,
  themeColor: [
    { media: "(prefers-color-scheme: dark)", color: "#0b0e14" },
    { media: "(prefers-color-scheme: light)", color: "#f6f7f9" },
  ],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>
        <RegisterSW />
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
