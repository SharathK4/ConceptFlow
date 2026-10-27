---
title: ConceptFlow AI
emoji: 📐
colorFrom: indigo
colorTo: gray
sdk: docker
app_port: 8010
pinned: false
short_description: Maths concepts as narrated Manim lessons, made by AI agents
---

# ConceptFlow AI

Any mathematical idea, animated and narrated in the manner of a good lecture,
made by a studio of AI agents: a Google ADK workflow that delegates lesson
design to a CrewAI crew over the A2A protocol, with tools served over MCP.

- `/` - the studio
- `/api/health` - status, tools, agents, tracing
- `/a2a/writers-room/.well-known/agent-card.json` - the CrewAI crew's A2A Agent Card
- `/mcp/` - the studio's MCP tool server (Streamable HTTP)

Group 21 - Sai Sharath Chandra Koppu, Rahul Kumar, Prashant.
