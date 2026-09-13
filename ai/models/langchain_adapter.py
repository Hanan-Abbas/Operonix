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
    """
    
    def __init__(self, provider: str, model_name: str, config: Optional[Dict[str, Any]] = None):
        """Initialize the LangChain adapter.
        
        Args:
            provider: The model provider (ollama, groq, gemini, openai, openrouter)
            model_name: The model name
            config: Optional provider-specific configuration
        """
        self.provider = provider
        self.model_name = model_name
        self.config = config or {}
        self._model = None
        self._initialize_model()
    
    def _initialize_model(self) -> None:
        """Initialize the LangChain model based on provider."""
        try:
            if self.provider == "groq":
                from langchain_groq import ChatGroq
                api_key = self.config.get("api_key")
                self._model = ChatGroq(
                    model=self.model_name,
                    api_key=api_key,
                    temperature=self.config.get("temperature", 0.7),
                    max_tokens=self.config.get("max_tokens", 2048)
                )
                logger.info(f"LangChainAdapter: Initialized Groq model {self.model_name}")
                
            elif self.provider == "ollama":
                from langchain_community.llms import Ollama
                base_url = self.config.get("base_url", "http://localhost:11434")
                self._model = ChatGroq(  # Using ChatGroq interface for consistency
                    model=self.model_name,
                    base_url=base_url,
                    temperature=self.config.get("temperature", 0.7)
                )
                logger.info(f"LangChainAdapter: Initialized Ollama model {self.model_name}")
                
            elif self.provider == "gemini":
                from langchain_google_genai import ChatGoogleGenerativeAI
                api_key = self.config.get("api_key")
                self._model = ChatGoogleGenerativeAI(
                    model=self.model_name,
                    api_key=api_key,
                    temperature=self.config.get("temperature", 0.7)
                )
                logger.info(f"LangChainAdapter: Initialized Gemini model {self.model_name}")
                
            elif self.provider == "openai":
                from langchain_openai import ChatOpenAI
                api_key = self.config.get("api_key")
                base_url = self.config.get("base_url")
                self._model = ChatOpenAI(
                    model=self.model_name,
                    api_key=api_key,
                    base_url=base_url,
                    temperature=self.config.get("temperature", 0.7)
                )
                logger.info(f"LangChainAdapter: Initialized OpenAI model {self.model_name}")
                
            elif self.provider == "openrouter":
                from langchain_openai import ChatOpenAI
                api_key = self.config.get("api_key")
                self._model = ChatOpenAI(
                    model=self.model_name,
                    api_key=api_key,
                    base_url="https://openrouter.ai/api/v1",
                    temperature=self.config.get("temperature", 0.7)
                )
                logger.info(f"LangChainAdapter: Initialized OpenRouter model {self.model_name}")
                
            else:
                logger.warning(f"LangChainAdapter: Unknown provider {self.provider}")
                self._model = None
                
        except ImportError as e:
            logger.error(f"LangChainAdapter: Failed to import provider package: {e}")
            self._model = None
        except Exception as e:
            logger.error(f"LangChainAdapter: Failed to initialize model: {e}")
            self._model = None
    
    def is_available(self) -> bool:
        """Check if the LangChain model is available.
        
        In Phase 2 (stub mode), we return True even if the model
        initialization fails, as we're providing stub implementations.
        """
        # In Phase 2, we're in stub mode - always return True
        # Later phases will check if self._model is not None
        return True
    
    async def invoke(self, messages: List[Dict[str, str]], **kwargs) -> str:
        """Invoke the LangChain model.
        
        Args:
            messages: List of message dicts with 'role' and 'content'
            **kwargs: Additional invocation parameters
            
        Returns:
            Model response text
        """
        if not self.is_available():
            raise RuntimeError("LangChain model is not available")
        
        # In Phase 2 (stub mode), return a placeholder response
        logger.warning("LangChainAdapter: Model invocation deferred to later phases (stub mode)")
        return f"LangChain invoke response (Phase 2 stub - provider: {self.provider}, model: {self.model_name})"
    
    async def invoke_structured(self, messages: List[Dict[str, str]], schema: Dict[str, Any], **kwargs) -> Dict[str, Any]:
        """Invoke the LangChain model with structured output.
        
        Args:
            messages: List of message dicts
            schema: Output schema definition
            **kwargs: Additional invocation parameters
            
        Returns:
            Structured output matching the schema
        """
        if not self.is_available():
            raise RuntimeError("LangChain model is not available")
        
        # In Phase 2 (stub mode), return a placeholder response
        logger.warning("LangChainAdapter: Structured invocation deferred to later phases (stub mode)")
        return {
            "provider": self.provider,
            "model": self.model_name,
            "schema_name": schema.get("name", "unnamed"),
            "note": "Structured output (Phase 2 stub)"
        }
