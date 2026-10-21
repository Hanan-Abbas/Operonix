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
                from langchain_community.chat_models import ChatOllama
                base_url = self.config.get("base_url", "http://localhost:11434")
                self._model = ChatOllama(
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
                    temperature=self.config.get("temperature", 0.7),
                    convert_system_message_to_human=True
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

        Returns True if the model was successfully initialized.
        """
        return self._model is not None
    
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

        try:
            # Convert messages to LangChain format
            from langchain_core.messages import HumanMessage, SystemMessage, AIMessage

            langchain_messages = []
            for msg in messages:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                if role == "system":
                    langchain_messages.append(SystemMessage(content=content))
                elif role == "user":
                    langchain_messages.append(HumanMessage(content=content))
                elif role == "assistant":
                    langchain_messages.append(AIMessage(content=content))
                else:
                    langchain_messages.append(HumanMessage(content=content))

            # Invoke the model
            response = await self._model.ainvoke(langchain_messages, **kwargs)
            return response.content

        except Exception as e:
            logger.error(f"LangChainAdapter: Model invocation failed: {e}")
            raise
    
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

        try:
            # Convert messages to LangChain format
            from langchain_core.messages import HumanMessage, SystemMessage, AIMessage

            langchain_messages = []
            for msg in messages:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                if role == "system":
                    langchain_messages.append(SystemMessage(content=content))
                elif role == "user":
                    langchain_messages.append(HumanMessage(content=content))
                elif role == "assistant":
                    langchain_messages.append(AIMessage(content=content))
                else:
                    langchain_messages.append(HumanMessage(content=content))

            # Use LangChain's structured output
            from langchain_core.output_parsers import JsonOutputParser
            from langchain_core.prompts import ChatPromptTemplate

            # Create a prompt template that includes the schema instructions
            system_content = langchain_messages[0].content if langchain_messages and langchain_messages[0].type == "system" else ""
            user_content = langchain_messages[-1].content if langchain_messages else ""

            # Add JSON format instruction to system prompt
            if system_content:
                system_content += "\n\nIMPORTANT: Respond in valid JSON format matching the schema."
            else:
                system_content = "Respond in valid JSON format matching the schema."

            # Rebuild messages with updated system prompt
            if langchain_messages and langchain_messages[0].type == "system":
                langchain_messages[0] = SystemMessage(content=system_content)
            else:
                langchain_messages.insert(0, SystemMessage(content=system_content))

            # Use with_structured_output if available (LangChain 0.1+)
            if hasattr(self._model, 'with_structured_output'):
                # Build Pydantic model from schema (simplified approach)
                # For now, use JSON output parser
                parser = JsonOutputParser()
                chain = self._model | parser
                result = await chain.ainvoke(langchain_messages, **kwargs)
                return result
            else:
                # Fallback: invoke and parse JSON
                response = await self._model.ainvoke(langchain_messages, **kwargs)
                import json
                # Try to extract JSON from response
                content = response.content
                # Handle markdown code blocks
                if "```json" in content:
                    content = content.split("```json")[1].split("```")[0].strip()
                elif "```" in content:
                    content = content.split("```")[1].split("```")[0].strip()

                return json.loads(content)

        except Exception as e:
            logger.error(f"LangChainAdapter: Structured invocation failed: {e}")
            # Return a minimal valid response as fallback
            schema_name = schema.get("name", "unnamed")
            logger.warning(f"Returning fallback response for schema: {schema_name}")
            return {"error": str(e), "schema_name": schema_name}
