"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/** 渲染智能体产出的 Markdown。表格在手机上可横向滚动（样式见 globals.css 的 .md）。 */
export function Markdown({ children }: { children: string }) {
  return (
    <div className="md text-[13.5px] text-[var(--text)] md:text-sm">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{children}</ReactMarkdown>
    </div>
  );
}
