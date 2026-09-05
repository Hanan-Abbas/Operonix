"""
LangChain Adapter — Operonix AI Layer
────────────────────────────────────

LangChain-specific adapter implementation.
Per migration plan §5.2:
"Nothing else should depend on LangChain model objects directly."

This adapter:
- Wraps LangChain models
- Provides Operonix-compatible interface
- Handles provider-specific configuration
"""
from __future__ import annotations

import logging
from typing import Optional, Dict, Any, List

logger = logging.getLogger("LangChainAdapter")


class LangChainAdapter:
    """Adapter for LangChain models.
    
    This class provides a thin wrapper around LangChain models to
    ensure that the rest of Operonix never depends directly on
    LangChain-specific interfaces.
    
    In Phase 2, this is a stub that logs the intent.
    Later phases will implement actual LangChain integration.
    """
    
    def __init__(self, provider: str, model_name: str, config: Optional[Dict[str, Any]] = None):
        """Initialize the LangChain adapter.
        
        Args:
            provider: The model provider (ollama, groq, gemini, etc.)
            model_name: The model name
            config: Optional provider-specific configuration
        """
        self.provider = provider
        self.model_name = model_name
        self.config = config or {}
        
        logger.info(f"LangChainAdapter initialized for provider: {provider}, model: {model_name}")
        
        # In Phase 2, we don't actually initialize LangChain models
        # Later phases will integrate:
        # if provider == "groq":
        #     from langchain_groq import ChatGroq
        #     self.model = ChatGroq(model=model_name, **config)
        # elif provider == "ollama":
        #     from langchain_community.llms import Ollama
        #     self.model = Ollama(model=model_name, **config)
        # etc.
        
        logger.info("LangChain model initialization deferred to later phases")
    
    def is_available(self) -> bool:
        """Check if the LangChain model is available."""
        # In Phase 2, we return True if configuration is present
        # Later phases will check actual model availability
        return bool(self.provider and self.model_name)
    
    async def invoke(self, messages: List[Dict[str, str]], **kwargs) -> str:
        """Invoke the LangChain model.
        
        Args:
            messages: List of message dicts
            **kwargs: Additional invocation parameters
            
        Returns:
            Model response text
        """
        if not self.is_available():
            raise RuntimeError("LangChain model is not available")
        
        logger.info(f"LangChain model invocation requested")
        
        # In Phase 2, we return a placeholder response
        logger.warning("LangChain model invocation deferred to later phases")
        
        return f"LangChain response (Phase 2 stub - {self.provider}/{self.model_name})"
    
    async def invoke_structured(self, messages: List[Dict[str, str]], schema: Dict[str, Any], **kwargs) -> Dict[str, Any]:
        """Invoke the LangChain model with structured output.
        
        Args:
            messages: List of message dicts
            schema: Output schema
            **kwargs: Additional invocation parameters
            
        Returns:
            Structured output matching the schema
        """
        if not self.is_available():
            raise RuntimeError("LangChain model is not available")
        
        logger.info(f"LangChain structured output requested")
        
        # In Phase 2, we return a placeholder response
        logger.warning("LangChain structured output deferred to later phases")
        
        return {
            "provider": self.provider,
            "model": self.model_name,
            "note": "Structured output (Phase 2 stub)"
        }
