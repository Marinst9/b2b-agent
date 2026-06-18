# B2B Outreach AI Agent

A fullstack AI-powered B2B outreach system built with FastAPI, React/Vite, and Claude (Anthropic).

## What it does
- Researches target companies and generates personalized outreach emails using Claude
- Compares V1/V2/V3 prompt engineering techniques via a React dashboard
- Integrates Hunter.io for lead discovery and Mailtrap for email delivery
- Stores campaign data in PostgreSQL

## Tech Stack
**Backend:** FastAPI, Python, PostgreSQL, OpenAI/Anthropic API, Hunter.io, Mailtrap  
**Frontend:** React, Vite  
**Other:** Docker, REST API

## Architecture
- `app/` — FastAPI backend (routes, agents, prompt templates)
- `dashboard/` — React frontend (V1/V2/V3 comparison dashboard)
- `data/` — sample datasets and configs

## Built as
Graduation thesis project — FCSE UKIM, 2025/2026
