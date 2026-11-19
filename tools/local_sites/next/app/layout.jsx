export const metadata = {
  title: "Local Next.js crawl fixture",
  description: "Deterministic local crawling fixture",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
