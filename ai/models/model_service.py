"""
Operonix Model Service — AI Layer Abstraction
────────────────────────────────────────────

Operonix-owned model interface that abstracts over LangChain.
Per migration plan §5.2:
"llm_client.py — REPLACE — Becomes ai/models/ on LangChain, preserving today's
provider-independent interface (Ollama, Groq, Gemini, OpenRouter) behind an
Operonix ModelService. Nothing else should depend on LangChain model objects directly."

This service provides:
- Provider-independent model access
- Structured output generation
- Chat completion
- Streaming support (future)
"""
from __future__ import annotations

import logging
from typing import Optional, Dict, Any, List
from enum import Enum

from core.config import settings

logger = logging.getLogger("ModelService")


class ModelProvider(str, Enum):
    """Supported model providers."""
    OLLAMA = "ollama"
    GROQ = "groq"
    GEMINI = "gemini"
    OPENROUTER = "openrouter"
    OPENAI = "openai"


class OperonixModelService:
    """Operonix-owned model service with provider-independent interface.
    
    This service abstracts over LangChain to provide a stable interface
    for the rest of Operonix. Nothing outside the AI layer should depend
    directly on LangChain model objects.
    
    Per migration plan §5.2, this replaces brain/llm_client.py.
    """
    
    def __init__(self, provider: Optional[ModelProvider] = None):
        """Initialize the model service.
        
        Args:
            provider: The model provider to use (None = auto-detect from config)
        """
        self.provider = provider or self._detect_provider()
        self.model_name = self._get_model_name()
        self._langchain_model = None
        self._initialize_langchain_model()
    
    def _detect_provider(self) -> ModelProvider:
        """Auto-detect provider from configuration."""
        # Check for Groq API key
        if getattr(settings, "GROQ_API_KEY", ""):
            return ModelProvider.GROQ
        
        # Check for Gemini API key
        if getattr(settings, "GEMINI_API_KEY", ""):
            return ModelProvider.GEMINI
        
        # Check for OpenRouter API key
        if getattr(settings, "OPENROUTER_API_KEY", ""):
            return ModelProvider.OPENROUTER
        
        # Check for OpenAI API key
        if getattr(settings, "OPENAI_API_KEY", ""):
            return ModelProvider.OPENAI
        
        # Default to Ollama (local)
        return ModelProvider.OLLAMA
    
    def _get_model_name(self) -> str:
        """Get the model name for the current provider."""
        if self.provider == ModelProvider.GROQ:
            return getattr(settings, "GROQ_MODEL", "llama3-70b-8192")
        elif self.provider == ModelProvider.OLLAMA:
            return getattr(settings, "OLLAMA_MODEL", "llama3")
        elif self.provider == ModelProvider.GEMINI:
            return "gemini-pro"
        elif self.provider == ModelProvider.OPENROUTER:
            return "meta-llama/llama-3-70b-instruct"
        elif self.provider == ModelProvider.OPENAI:
            return getattr(settings, "OPENAI_STT_MODEL", "gpt-4o-mini")
        else:
            return "llama3"
    
    def _initialize_langchain_model(self) -> None:
        """Initialize the LangChain model instance.
        
        In Phase 2, this is a stub that logs the intent.
        Later phases will actually integrate LangChain models.
        """
        logger.info(f"Initializing LangChain model for provider: {self.provider}")
        logger.info(f"Model name: {self.model_name}")
        
        # In Phase 2, we don't actually initialize LangChain models
        # We just log the configuration
        # Later phases will integrate:
        # - from langchain_openai import ChatOpenAI
        # - from langchain_groq import ChatGroq
        # - from langchain_google_genai import ChatGoogleGenerativeAI
        # - from langchain_community.llms import Ollama
        
        logger.info("LangChain model integration deferred to later phases")
    
    def is_available(self) -> bool:
        """Check if the model service is available."""
        # In Phase 2, we return True if configuration is present
        # Later phases will check actual model availability
        return self.provider is not None
    
    async def generate_chat_completion(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None
    ) -> str:
        """Generate a chat completion.
        
        Args:
            messages: List of message dicts with 'role' and 'content'
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
            
        Returns:
            Generated text response
            
        Raises:
            RuntimeError: If model is not available
        """
        if not self.is_available():
            raise RuntimeError("Model service is not available")
        
        logger.info(f"Chat completion requested with {len(messages)} messages")
        
        # In Phase 2, we return a placeholder response
        # Later phases will actually call LangChain models
        logger.warning("LangChain model integration deferred to later phases")
        
        return f"Chat completion response (Phase 2 stub - provider: {self.provider}, model: {self.model_name})"
    
    async def generate_structured_output(
        self,
        messages: List[Dict[str, str]],
        schema: Dict[str, Any],
        temperature: Optional[float] = None
    ) -> Dict[str, Any]:
        """Generate structured output following a schema.
        
        Args:
            messages: List of message dicts with 'role' and 'content'
            schema: Output schema definition
            temperature: Sampling temperature
            
        Returns:
            Structured output matching the schema
            
        Raises:
            RuntimeError: If model is not available
        """
        if not self.is_available():
            raise RuntimeError("Model service is not available")
        
        logger.info(f"Structured output requested with schema: {schema.get('name', 'unnamed')}")
        
        # In Phase 2, we return a placeholder response
        # Later phases will use LangChain's structured output
        logger.warning("LangChain structured output integration deferred to later phases")
        
        return {
            "provider": self.provider.value,
            "model": self.model_name,
            "note": "Structured output (Phase 2 stub)"
        }
    
    def get_provider_info(self) -> Dict[str, Any]:
        """Get information about the current provider configuration."""
        return {
            "provider": self.provider.value,
            "model_name": self.model_name,
            "available": self.is_available()
        }


# ─── GLOBAL MODEL SERVICE INSTANCE ─────────────────────────────────────────

# Global model service instance
model_service = OperonixModelService()
