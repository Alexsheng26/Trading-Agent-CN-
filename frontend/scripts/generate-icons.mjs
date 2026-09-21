// 从 public/icon.svg 生成 PWA 需要的各尺寸 PNG。
// 改了 icon.svg 之后跑一次：pnpm icons
//
// 为什么还要 PNG：Android Chrome 的「安装应用」横幅要求 PNG 图标，
// iOS 的 apple-touch-icon 也不认 SVG。

import sharp from "sharp";
import { readFile } from "node:fs/promises";

const svg = await readFile("public/icon.svg");
// density 拉高，避免矢量转位图时边缘发虚
const opts = { density: 384 };

for (const size of [192, 512]) {
  await sharp(svg, opts).resize(size, size).png().toFile(`public/icon-${size}.png`);
  console.log(`✓ icon-${size}.png`);
}

// iOS 不做圆角裁切（系统自己加），所以背景必须铺满
await sharp(svg, opts)
  .resize(180, 180)
  .flatten({ background: "#0b0e14" })
  .png()
  .toFile("public/icon-apple.png");
console.log("✓ icon-apple.png");

// Maskable：Android 会裁成圆形，安全区只有中心 80%，
// 把内容缩到 62% 居中，四周留足背景才不会被切掉。
const inner = await sharp(svg, opts).resize(318, 318).png().toBuffer();
await sharp({ create: { width: 512, height: 512, channels: 4, background: "#0b0e14" } })
  .composite([{ input: inner, gravity: "center" }])
  .png()
  .toFile("public/icon-maskable-512.png");
console.log("✓ icon-maskable-512.png");
