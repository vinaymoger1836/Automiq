import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Automiq | Workflow engine",
  description: "Local environment status for the Automiq workflow engine",
};

const themeScript = `try{const saved=localStorage.getItem('automiq-theme');document.documentElement.dataset.theme=saved==='light'||saved==='dark'?saved:(matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light')}catch{document.documentElement.dataset.theme=matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light'}`;

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head><script dangerouslySetInnerHTML={{ __html: themeScript }} /></head>
      <body>{children}</body>
    </html>
  );
}
