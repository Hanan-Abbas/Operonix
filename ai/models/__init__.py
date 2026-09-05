"""
Models Package — Operonix AI Layer
──────────────────────────────────

Operonix-owned model interface with LangChain adapter.
Per migration plan §5.2:
"The existing provider-independence is preserved."

Architecture:
Operonix ModelService
        ↓
     LangChain
        ↓
Ollama / Groq / Gemini / OpenRouter
"""
