"""
AI Integration Tests — Operonix Phase 2
──────────────────────────────────────

Tests for Phase 2 AI integration implementation.
These tests verify that:
- Model service abstraction works
- LangChain adapter can be instantiated
- Provider independence is preserved
- Analyze intent node integrates with AI layer
"""
from __future__ import annotations

import pytest
from typing import Dict, Any


# ─── MODEL SERVICE TESTS ─────────────────────────────────────────────────────

def test_model_service_can_be_imported():
    """Test that model service can be imported."""
    from ai.models.model_service import OperonixModelService
    assert OperonixModelService is not None


def test_model_service_instance_exists():
    """Test that global model service instance exists."""
    from ai.models.model_service import model_service
    assert model_service is not None


def test_model_service_provider_detection():
    """Test that model service can detect provider from config."""
    from ai.models.model_service import OperonixModelService, ModelProvider
    
    service = OperonixModelService()
    
    # Should detect a provider (even if it's the default Ollama)
    assert service.provider in [p.value for p in ModelProvider]
    assert service.provider is not None


def test_model_service_get_provider_info():
    """Test that model service can report provider information."""
    from ai.models.model_service import model_service
    
    info = model_service.get_provider_info()
    
    assert isinstance(info, dict)
    assert "provider" in info
    assert "model_name" in info
    assert "available" in info


def test_model_service_is_available():
    """Test that model service reports availability."""
    from ai.models.model_service import model_service
    
    # Should be available (configuration present)
    assert model_service.is_available() is True


def test_model_service_chat_completion_stub():
    """Test that model service chat completion works (stub in Phase 2)."""
    from ai.models.model_service import model_service
    import asyncio
    
    messages = [
        {"role": "user", "content": "Hello"}
    ]
    
    result = asyncio.run(model_service.generate_chat_completion(messages))
    
    assert isinstance(result, str)
    assert len(result) > 0


def test_model_service_structured_output_stub():
    """Test that model service structured output works (stub in Phase 2)."""
    from ai.models.model_service import model_service
    import asyncio
    
    messages = [{"role": "user", "content": "Hello"}]
    schema = {"name": "test", "properties": {}}
    
    result = asyncio.run(model_service.generate_structured_output(messages, schema))
    
    assert isinstance(result, dict)
    assert "provider" in result


# ─── LANGCHAIN ADAPTER TESTS ─────────────────────────────────────────────────

def test_langchain_adapter_can_be_imported():
    """Test that LangChain adapter can be imported."""
    from ai.models.langchain_adapter import LangChainAdapter
    assert LangChainAdapter is not None


def test_langchain_adapter_can_be_instantiated():
    """Test that LangChain adapter can be instantiated."""
    from ai.models.langchain_adapter import LangChainAdapter
    
    adapter = LangChainAdapter(provider="ollama", model_name="llama3")
    
    assert adapter.provider == "ollama"
    assert adapter.model_name == "llama3"


def test_langchain_adapter_is_available():
    """Test that LangChain adapter reports availability."""
    from ai.models.langchain_adapter import LangChainAdapter
    
    adapter = LangChainAdapter(provider="ollama", model_name="llama3")
    
    assert adapter.is_available() is True


def test_langchain_adapter_invoke_stub():
    """Test that LangChain adapter invoke works (stub in Phase 2)."""
    from ai.models.langchain_adapter import LangChainAdapter
    import asyncio
    
    adapter = LangChainAdapter(provider="ollama", model_name="llama3")
    messages = [{"role": "user", "content": "Hello"}]
    
    result = asyncio.run(adapter.invoke(messages))
    
    assert isinstance(result, str)
    assert len(result) > 0


def test_langchain_adapter_structured_stub():
    """Test that LangChain adapter structured output works (stub in Phase 2)."""
    from ai.models.langchain_adapter import LangChainAdapter
    import asyncio
    
    adapter = LangChainAdapter(provider="ollama", model_name="llama3")
    messages = [{"role": "user", "content": "Hello"}]
    schema = {"name": "test"}
    
    result = asyncio.run(adapter.invoke_structured(messages, schema))
    
    assert isinstance(result, dict)
    assert "provider" in result


# ─── ANALYZE INTENT NODE TESTS ───────────────────────────────────────────────

def test_analyze_intent_node_exists():
    """Test that analyze_intent node can be imported."""
    from graph.nodes.analyze_intent import analyze_intent_node
    assert analyze_intent_node is not None


def test_analyze_intent_node_with_state():
    """Test that analyze_intent node processes state correctly."""
    from graph.nodes.analyze_intent import analyze_intent_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource
    
    task = TaskRequest(user_input="Open Firefox", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    result = analyze_intent_node(state)
    
    assert "state" in result
    assert result["state"].intent is not None
    assert result["state"].intent.name == "placeholder_intent"
    assert len(result["state"].history.get("events", [])) > 0


def test_analyze_intent_node_creates_intent_result():
    """Test that analyze_intent node creates IntentResult."""
    from graph.nodes.analyze_intent import analyze_intent_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource, IntentResult
    
    task = TaskRequest(user_input="test", source=TaskSource.API)
    state = OperonixState(task=task)
    
    result = analyze_intent_node(state)
    
    assert isinstance(result["state"].intent, IntentResult)
    assert result["state"].intent.confidence >= 0.0
    assert result["state"].intent.confidence <= 1.0


# ─── INTEGRATION TESTS ───────────────────────────────────────────────────────

def test_ai_package_structure():
    """Test that AI package has correct structure."""
    import ai
    from ai import models
    from ai.models import model_service
    from ai.models import langchain_adapter
    
    assert ai is not None
    assert models is not None
    assert model_service is not None
    assert langchain_adapter is not None


def test_models_package_structure():
    """Test that models package has correct structure."""
    from ai.models import model_service
    from ai.models import langchain_adapter
    
    assert model_service is not None
    assert langchain_adapter is not None


def test_node_sequence_with_intent():
    """Test that nodes can be called in sequence including analyze_intent."""
    from graph.nodes.intake import intake_node
    from graph.nodes.observe import observe_node
    from graph.nodes.analyze_intent import analyze_intent_node
    from graph.nodes.finalize import finalize_node
    from migration.graph_state import OperonixState
    from migration.domain_contracts import TaskRequest, TaskSource
    
    task = TaskRequest(user_input="Open Firefox", source=TaskSource.VOICE)
    state = OperonixState(task=task)
    
    # Execute node sequence
    state = intake_node(state)["state"]
    state = observe_node(state)["state"]
    state = analyze_intent_node(state)["state"]
    state = finalize_node(state)["state"]
    
    # Verify final state
    assert state.final is not None
    assert state.intent is not None
    assert state.final.success is True
    assert len(state.history.get("events", [])) >= 4


def test_provider_independence():
    """Test that provider independence is preserved."""
    from ai.models.model_service import ModelProvider, OperonixModelService
    
    # Test with different providers
    for provider in [ModelProvider.OLLAMA, ModelProvider.GROQ, ModelProvider.GEMINI]:
        service = OperonixModelService(provider=provider)
        assert service.provider == provider
        assert service.is_available()


# ─── FEATURE FLAG TESTS ───────────────────────────────────────────────────

def test_use_langchain_models_flag_exists():
    """Test that USE_LANGCHAIN_MODELS flag exists."""
    from migration.feature_flags import flags
    
    assert hasattr(flags, "USE_LANGCHAIN_MODELS")
    assert flags.USE_LANGCHAIN_MODELS is False  # Safe default


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
