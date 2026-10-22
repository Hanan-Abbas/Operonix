"""
Test Graph Integration — Operonix LangGraph Migration
──────────────────────────────────────────────────────

Test script to verify the LangGraph integration is working correctly.
This tests the actual production integration (not just unit tests).
"""
import asyncio
import sys
import os
from pathlib import Path

# Add project root to path
project_root = Path(__file__).resolve().parent
sys.path.insert(0, str(project_root))

async def test_integration():
    """Test the LangGraph integration."""
    print("=" * 80)
    print("LANGGRAPH INTEGRATION TEST")
    print("=" * 80)
    
    # Test 1: Import and initialize RuntimeAdapter
    print("\n[TEST 1] RuntimeAdapter Initialization")
    try:
        from graph.runtime_adapter import runtime_adapter
        print("✅ RuntimeAdapter imported successfully")
        
        status = runtime_adapter.get_graph_status()
        print(f"Graph Status: {status}")
        
        if runtime_adapter.is_graph_enabled():
            print("✅ Graph is ENABLED")
        else:
            print("ℹ️  Graph is DISABLED (set USE_LANGGRAPH=true to enable)")
            
    except Exception as e:
        print(f"❌ RuntimeAdapter initialization failed: {e}")
        return False
    
    # Test 2: Create task request
    print("\n[TEST 2] Task Request Creation")
    try:
        from migration.domain_contracts import TaskSource
        
        task_request = runtime_adapter.create_task_request(
            user_input="List files in current directory",
            source=TaskSource.API
        )
        
        print(f"✅ Task request created: {task_request.task_id}")
        print(f"   User input: {task_request.user_input}")
        print(f"   Source: {task_request.source.value}")
        
    except Exception as e:
        print(f"❌ Task request creation failed: {e}")
        return False
    
    # Test 3: Test graph execution (if enabled)
    print("\n[TEST 3] Graph Execution")
    try:
        if runtime_adapter.is_graph_enabled():
            print("Executing task through graph...")
            result = await runtime_adapter.execute_task(task_request, use_graph=True)
            
            print(f"✅ Graph execution completed")
            print(f"   Success: {result.success}")
            print(f"   Response: {result.response}")
            print(f"   Paused: {result.paused}")
            
            if result.error:
                print(f"   Error: {result.error}")
        else:
            print("ℹ️  Graph is disabled, skipping execution test")
            print("   To test graph execution, set USE_LANGGRAPH=true in .env file")
            
    except Exception as e:
        print(f"❌ Graph execution failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # Test 4: Test API endpoint registration
    print("\n[TEST 4] API Endpoint Registration")
    try:
        from api.server import create_app
        
        app = create_app()
        
        # Check if tasks router is registered
        routes = [route.path for route in app.routes]
        
        if "/api/tasks" in routes:
            print("✅ Tasks API endpoint registered")
        else:
            print("⚠️  Tasks API endpoint not found in routes")
            print(f"   Available routes: {routes}")
            
    except Exception as e:
        print(f"❌ API endpoint registration check failed: {e}")
        return False
    
    # Test 5: Test lifecycle manager integration
    print("\n[TEST 5] Lifecycle Manager Integration")
    try:
        from core.lifecycle_manager import lifecycle_manager
        
        # Check if RuntimeAdapter is imported in lifecycle_manager
        import core.lifecycle_manager as lm_module
        source = lm_module.__file__
        
        with open(source, 'r') as f:
            content = f.read()
            
        if 'runtime_adapter' in content:
            print("✅ RuntimeAdapter imported in lifecycle_manager")
        else:
            print("⚠️  RuntimeAdapter not found in lifecycle_manager")
            
        if '_setup_graph_task_routing' in content:
            print("✅ Graph task routing setup found in lifecycle_manager")
        else:
            print("⚠️  Graph task routing setup not found in lifecycle_manager")
            
    except Exception as e:
        print(f"❌ Lifecycle manager integration check failed: {e}")
        return False
    
    print("\n" + "=" * 80)
    print("INTEGRATION TEST COMPLETED")
    print("=" * 80)
    return True

if __name__ == "__main__":
    success = asyncio.run(test_integration())
    sys.exit(0 if success else 1)