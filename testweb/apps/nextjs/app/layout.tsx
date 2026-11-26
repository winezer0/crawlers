import type { ReactNode } from 'react'
import './style.css'
export const metadata = { title: 'Testweb Next.js', description: 'Local App Router crawler target' }
export default function RootLayout({ children }: { children: ReactNode }) { return <html lang="en"><body><header><strong>framework-nextjs · Next App Router</strong><nav><a href="/">Home</a><a href="/guide">Guide</a><a href="/items/item-01">Item 01</a></nav></header>{children}</body></html> }
