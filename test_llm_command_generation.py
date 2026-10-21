"""
Test script for LLM command generation.
This tests the new LLM-based command generation in create_plan.py.
"""
import asyncio
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from migration.feature_flags import flags
from ai.models.model_service import model_service


async def test_model_service():
    """Test if model service is available and can generate structured output."""
    print("=" * 60)
    print("Testing LLM Model Service")
    print("=" * 60)

    print(f"\nFeature Flags:")
    print(f"  USE_LANGGRAPH: {flags.USE_LANGGRAPH}")
    print(f"  USE_LANGCHAIN_MODELS: {flags.USE_LANGCHAIN_MODELS}")
    print(f"  USE_GRAPH_ROUTING: {flags.USE_GRAPH_ROUTING}")

    print(f"\nModel Service Info:")
    provider_info = model_service.get_provider_info()
    print(f"  Provider: {provider_info['provider']}")
    print(f"  Model: {provider_info['model_name']}")
    print(f"  Available: {provider_info['available']}")

    if not model_service.is_available():
        print("\n❌ Model service is not available!")
        print("Please check your API keys in .env file")
        return False

    print("\n✅ Model service is available")

    # Test structured output
    print("\n" + "=" * 60)
    print("Testing Structured Output Generation")
    print("=" * 60)

    messages = [
        {
            "role": "system",
            "content": """You are a command generator. Generate concrete shell commands.
Rules:
- For opening applications: use the actual system command (e.g., "gnome-system-monitor", "firefox")
- Respond in JSON with: command, arguments, method, explanation"""
        },
        {
            "role": "user",
            "content": "open system monitor"
        }
    ]

    schema = {
        "name": "command_generation",
        "properties": {
            "command": {"type": "string"},
            "arguments": {"type": "array", "items": {"type": "string"}},
            "method": {"type": "string", "enum": ["shell", "api", "plugin", "ui"]},
            "explanation": {"type": "string"}
        }
    }

    try:
        print("\nGenerating command for: 'open system monitor'")
        result = await model_service.generate_structured_output(messages, schema)

        print(f"\n✅ LLM Response:")
        print(f"  Command: {result.get('command', 'N/A')}")
        print(f"  Arguments: {result.get('arguments', [])}")
        print(f"  Method: {result.get('method', 'N/A')}")
        print(f"  Explanation: {result.get('explanation', 'N/A')}")

        # Validate response
        if result.get('command') and result.get('method'):
            print("\n✅ LLM command generation is working!")
            return True
        else:
            print("\n⚠️  LLM response missing required fields")
            return False

    except Exception as e:
        print(f"\n❌ Error during structured output generation: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_create_plan_integration():
    """Test the create_plan node integration."""
    print("\n" + "=" * 60)
    print("Testing create_plan Node Integration")
    print("=" * 60)

    try:
        from graph.nodes.create_plan import _generate_command_with_llm

        user_input = "open system monitor"
        intent_name = "open_application"
        intent_parameters = {"app_name": "system monitor"}
        context = {"app_name": "unknown", "app_type": "unknown", "cwd": "/home/user"}

        print(f"\nGenerating command with:")
        print(f"  User input: {user_input}")
        print(f"  Intent: {intent_name}")
        print(f"  Parameters: {intent_parameters}")

        result = await _generate_command_with_llm(
            user_input,
            intent_name,
            intent_parameters,
            context
        )

        print(f"\n✅ create_plan LLM generation result:")
        print(f"  Command: {result['command']}")
        print(f"  Arguments: {result['arguments']}")
        print(f"  Method: {result['method']}")
        print(f"  Explanation: {result['explanation']}")

        if result['command'] and result['method']:
            print("\n✅ create_plan LLM integration is working!")
            return True
        else:
            print("\n⚠️  create_plan LLM response missing required fields")
            return False

    except Exception as e:
        print(f"\n❌ Error during create_plan integration test: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """Run all tests."""
    print("\n" + "=" * 60)
    print("LLM Command Generation Test Suite")
    print("=" * 60)

    # Check if USE_LANGCHAIN_MODELS is enabled
    if not flags.USE_LANGCHAIN_MODELS:
        print("\n⚠️  WARNING: USE_LANGCHAIN_MODELS is false")
        print("LLM command generation will not be used!")
        print("Please set USE_LANGCHAIN_MODELS=true in .env file")
        print("\nContinuing with model service test anyway...")

    # Test model service
    model_service_ok = await test_model_service()

    if model_service_ok and flags.USE_LANGCHAIN_MODELS:
        # Test create_plan integration
        create_plan_ok = await test_create_plan_integration()

        if create_plan_ok:
            print("\n" + "=" * 60)
            print("✅ ALL TESTS PASSED")
            print("=" * 60)
            print("\nLLM command generation is ready to use!")
            print("The graph will now generate concrete commands via LLM.")
            return 0
        else:
            print("\n" + "=" * 60)
            print("⚠️  PARTIAL SUCCESS")
            print("=" * 60)
            print("Model service works but create_plan integration has issues.")
            return 1
    else:
        print("\n" + "=" * 60)
        print("⚠️  SETUP REQUIRED")
        print("=" * 60)
        print("Please:")
        print("1. Set USE_LANGCHAIN_MODELS=true in .env")
        print("2. Ensure you have a valid API key (Groq, OpenRouter, Gemini, or Ollama)")
        print("3. Restart the application")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
