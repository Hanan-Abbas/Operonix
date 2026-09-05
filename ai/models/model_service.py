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
        """Initialize the LangChain model instance."""
        logger.info(f"Initializing LangChain model for provider: {self.provider}")
        logger.info(f"Model name: {self.model_name}")
        
        try:
            from ai.models.langchain_adapter import LangChainAdapter
            
            # Build config for LangChain adapter
            config = {}
            
            if self.provider == ModelProvider.GROQ:
                config["api_key"] = getattr(settings, "GROQ_API_KEY", "")
                config["temperature"] = getattr(settings, "GROQ_TEMPERATURE", 0.7)
                config["max_tokens"] = getattr(settings, "GROQ_MAX_TOKENS", 2048)
                
            elif self.provider == ModelProvider.OLLAMA:
                config["base_url"] = getattr(settings, "OLLAMA_BASE_URL", "http://localhost:11434")
                config["temperature"] = getattr(settings, "OLLAMA_TEMPERATURE", 0.7)
                
            elif self.provider == ModelProvider.GEMINI:
        pi_key"s lt._larechig,_model "GEAoP Non  nnd fief._manger=in_etatt.is_(settin()
            elif self.provider == ModelProvider.OPENROUTER:
                config["api_key"] = getattr(settings, "OPENROUTER_API_KEY", "")
                config["temperature"] = getattr(settings, "OPENROUTER_TEMPERATURE", 0.7)
                
            elif self.provider == ModelProvider.OPENAI:
                config["api_key"] = getattr(settings, "OPENAI_API_KEY", "")
                config["base_url"] = getattr(settings, "OPENAI_BASE_URL", None)
                config["temperature"] = getattr(settings, "OPENAI_TEMPERATURE", 0.7)
            
            # Initialize LangChain adapter
            self._langchain_model = LangChainAdapter(
                provider=self.provider.value,
                model_name=self.model_name,
                config=config (overrides config)
            ) (overrides config)
            
            if self._langchain_model.is_available():
                logger.info(f"LangChain model initialized successfully: {self.provider}/{self.model_name}")
            else:
                logger.warning(f"LangChain model initialization failed: {self.provider}/{self.model_name}")
                
        except ImportError as e:
            logger.error(f"Failed to import LangChain adapter: {e}")
            self._langchain_model = None
        except Exception as e:
            logger.error(f"Failed to initialize LangChain model: {e}")
            self._langchain_model = None
    Build kwargs for ivocation
       kwrg = {}
        if tmperatureisnot Non:
           kwags["tmperae"]=temertur
        if max_tknsi nt No:
    def s   kwargs["max_vokans"]l= mlx_tok(ns
e-      
        bry:
            reso:t =awit sef._c_.invoke(messages, **kwarg)
            """Chec tfoe l stvcoipiable.cmpeduccsfully
            # Laterresule
       sex elckExcattiuaml  a:
        viltlgg.r(f"Chatcopletin faile"
            raise
        return self.provider is not None
    
    async def generate_chat_completion(
        self, (overrides config)
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
          BuildGkwergrtfot invocariop
o       kwnrgss= {}
        if temeatu i nt No:
            kw rgs["mpeature"]= temertur
        
        try:
            reult= aatlf._lc_model.invoke_(messages, schema,**kwargs)
            Raises:foScmpeduccsfully
            """n .vut
        exceptnExciptirM os e: service is not available")
        lgger.rror(ffild:{e}"
            raiseogger.info(f"Chat completion requested with {len(messages)} messages")
        
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
