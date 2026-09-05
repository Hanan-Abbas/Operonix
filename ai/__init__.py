"""
AI Package — Operonix LangGraph/LangChain Migration
──────────────────────────────────────────────────

This package contains the LangChain AI integration layer.
Per migration plan §5.2, the AI layer owns:
- LLM reasoning
- Structured output
- RAG
- AI-facing tools

The AI layer is beneath an Operonix-owned model interface to preserve
provider independence (Ollama, Groq, Gemini, OpenRouter).
"""
