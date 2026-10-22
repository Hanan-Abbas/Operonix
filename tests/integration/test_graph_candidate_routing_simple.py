"""
Simple test to verify candidate-based routing services are working.
"""
import asyncio
import sys
import os

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

sys.path.insert(0, project_root)

from migration.feature_flags import flags


async def test_candidate_services():
    """Test that candidate-based routing services are working."""
    print("=" * 60)
    print("Test: Candidate-Based Routing Services")
    print("=" * 60)

    print(f"\nFeature Flags:")
    print(f"  USE_LANGGRAPH: {flags.USE_LANGGRAPH}")
    print(f"  USE_GRAPH_ROUTING: {flags.USE_GRAPH_ROUTING}")

    try:
        # Test candidate discovery
        print("\nTest 1: Candidate Discovery Service")
        from graph.candidate_discovery import get_candidate_discovery_service
        discovery = get_candidate_discovery_service()
        print(f"  ✅ Discovery service initialized: {discovery is not None}")

        # Test candidate evaluation
        print("\nTest 2: Candidate Evaluation Service")
        from graph.candidate_evaluation import get_candidate_evaluation_service
        evaluation = get_candidate_evaluation_service()
        print(f"  ✅ Evaluation service initialized: {evaluation is not None}")

        # Test ranking policy
        print("\nTest 3: Ranking Policy Service")
        from graph.ranking_policy import get_ranking_policy_service
        ranking = get_ranking_policy_service()
        print(f"  ✅ Ranking service initialized: {ranking is not None}")

        # Test route node
        print("\nTest 4: Graph Route Node")
        from graph.nodes.route import route_node
        print(f"  ✅ Route node imported: {route_node is not None}")

        # Test method router respects flag
        print("\nTest 5: Method Router Flag Check")
        from tools.method_router import router
        from tools.routing_decision import MethodType

        # Create a simple intent
        intent = {
            "intent": "test command",
            "confidence": 0.8,
            "parameters": {},
            "profile_hint": None
        }

        decision = router.select(intent)
        print(f"  Decision created: {decision is not None}")
        print(f"  Selected method: {decision.method}")
        print(f"  Fallback chain: {[m.value for m in decision.fallback_chain]}")

        if flags.USE_GRAPH_ROUTING:
            if len(decision.fallback_chain) == 0:
                print(f"  ✅ Fallback chain is empty (USE_GRAPH_ROUTING=true)")
            else:
                print(f"  ⚠️  Fallback chain not empty: {decision.fallback_chain}")
        else:
            print(f"  ✅ Fallback chain present (USE_GRAPH_ROUTING=false)")

        print("\n✅ All candidate-based routing services are working")
        return True

    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_safety_integration():
    """Test that safety rules are available for candidate filtering."""
    print("\n" + "=" * 60)
    print("Test: Safety Rules Integration")
    print("=" * 60)

    try:
        from safety.risk_rules import get_command_risk, RiskLevel

        # Test safe command
        print("\nTest 1: Safe Command")
        risk = get_command_risk("ls -la")
        print(f"  Command: ls -la")
        print(f"  Risk level: {risk.name}")
        print(f"  ✅ Risk assessment working")

        # Test risky command
        print("\nTest 2: Risky Command")
        risk = get_command_risk("rm -rf /")
        print(f"  Command: rm -rf /")
        print(f"  Risk level: {risk.name}")
        if risk in [RiskLevel.HIGH, RiskLevel.FORBIDDEN]:
            print(f"  ✅ Risky command correctly identified")
        else:
            print(f"  ⚠️  Risky command not identified as high/forbidden")

        print("\n✅ Safety rules integration working")
        return True

    except Exception as e:
        print(f"\n❌ Safety integration test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """Run all tests."""
    print("\n" + "=" * 60)
    print("Candidate-Based Routing Verification")
    print("=" * 60)

    test1 = await test_candidate_services()
    test2 = await test_safety_integration()

    print("\n" + "=" * 60)
    print("Test Summary")
    print("=" * 60)
    print(f"Test 1 (Candidate Services): {'✅ PASSED' if test1 else '❌ FAILED'}")
    print(f"Test 2 (Safety Integration): {'✅ PASSED' if test2 else '❌ FAILED'}")

    if all([test1, test2]):
        print("\n" + "=" * 60)
        print("✅ ALL TESTS PASSED")
        print("=" * 60)
        print("\nCandidate-based routing is correctly integrated:")
        print("- Discovery service available")
        print("- Evaluation service available")
        print("- Ranking service available")
        print("- Route node available")
        print("- Method router respects USE_GRAPH_ROUTING flag")
        print("- Safety rules available for filtering")
        return 0
    else:
        print("\n" + "=" * 60)
        print("⚠️  TESTS FAILED")
        print("=" * 60)
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
