import React from 'react'
import { createRoot } from 'react-dom/client'
import { ArrowUpRight } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import './index.css'

function App(){return <main className="mx-auto flex min-h-screen max-w-6xl flex-col justify-center gap-10 px-6 py-24"><div><p className="mb-5 text-sm text-muted-foreground">YOUR NEXT IDEA</p><h1 className="max-w-3xl text-5xl font-semibold tracking-tight sm:text-7xl">A thoughtful beginning.<br/><span className="text-muted-foreground">Make it your own.</span></h1><p className="mt-6 max-w-lg text-lg leading-8 text-muted-foreground">This starter is ready for the project described in your request. Replace this introduction with its real purpose and content.</p></div><Button className="w-fit" onClick={()=>document.getElementById('details').scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth'})}>Explore the foundation <ArrowUpRight/></Button><Card id="details" className="max-w-xl"><CardContent className="pt-6 text-sm leading-7 text-muted-foreground">React, Tailwind CSS and accessible shadcn/ui components are connected. Build the requested experience here.</CardContent></Card></main>}
createRoot(document.getElementById('root')).render(<React.StrictMode><App/></React.StrictMode>)
