"""
Quick test to check if RuntimeAdapter is working
"""
import sys
sys.path.insert(0, '/home/muhammad-mohid-abbas/Desktop/Hanan/GitHub/Operonix')

try:
    from graph.runtime_adapter import runtime_adapter
    from migration.feature_flags import flags
    
    print("=== RuntimeAdapter Status ===")
    print(f"RuntimeAdapter exists: {runtime_adapter is not None}")
    print(f"USE_LANGGRAPH: {flags.USE_LANGGRAPH}")
    
    status = runtime_adapter.get_graph_status()
    print(f"Graph Enabled: {status['graph_enabled']}")
    print(f"Graph Available: {status['graph_available']}")
    print(f"Migration Phase: {status['migration_phase']}")
    
    print("\n=== All Feature Flags ===")
    for flag, value in status['all_flags'].items():
        print(f"{flag}: {value}")
    
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()
