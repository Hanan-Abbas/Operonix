"""
Retrieve Knowledge Node — Operonix Graph
──────────────────────────────────────

Retrieve knowledge node: RAG/memory integration.
Per migration plan §4.2, node 5:
"retrieve_knowledge — may be a no-op initially. Calls memory/vector_store to
retrieve relevant context for the current task."

Phase 11 enhancement: Full integration with RAG/memory services.
"""
from __future__ import annotations

import logging
from typing import Dict, Any

from migration.graph_state import OperonixState
from migration.domain_contracts import KnowledgeContext
from graph.trace_collector import get_trace_collector

logger = logging.getLogger("Graph.RetrieveKnowledge")


def retrieve_knowledge_node(state: OperonixState) -> Dict[str, Any]:
    """Retrieve knowledge node: Gather relevant context from memory/RAG.
    
    This node:
    - Calls memory/vector_store to retrieve relevant context
    - Retrieves episodic memories, documents, learned patterns
    - Integrates with LongTermMemory, SessionMemory, VectorStore, Retriever
    
    Phase 11 enhancement: Full integration with RAG/memory services.
    
    Args:
        state: Current OperonixState
        
    Returns:
        Dict with updated state including knowledge context
    """
    logger.info(f"RETRIEVE_KNOWLEDGE: Retrieving knowledge for task {state.task.task_id}")
    
    state.add_history_event("retrieve_knowledge_started", {
        "task_id": state.task.task_id,
        "intent": state.intent.name if state.intent else None
    })
    
    # Phase 11: Integrate with actual RAG/memory services
    retrieved_memories = []
    retrieved_documents = []
    learned_patterns = []
    provenance = {}
    
    try:
        # Try to retrieve from LongTermMemory
        try:
            from memory.long_term_memory import long_term_memory
            
            if state.intent:
                past_tasks = long_term_memory.search_past_tasks(
                    intent=state.intent.name,
                    limit=5
                )
                retrieved_memories.extend(past_tasks)
                provenance["long_term_memory"] = len(past_tasks)
                
                logger.info(f"Retrieved {len(past_tasks)} memories from LongTermMemory")
        except ImportError:
            logger.warning("Could not import LongTermMemory")
        except Exception as e:
            logger.error(f"Error retrieving from LongTermMemory: {e}")
        
        # Try to retrieve from SessionMemory
        try:
            from memory.session_memory import session_memory
            
            if hasattr(session_memory, 'get_recent_tasks'):
                recent_tasks = session_memory.get_recent_tasks(limit=5)
                retrieved_memories.extend(recent_tasks)
                provenance["session_memory"] = len(recent_tasks)
                
                logger.info(f"Retrieved {len(recent_tasks)} memories from SessionMemory")
        except ImportError:
            logger.warning("Could not import SessionMemory")
        except Exception as e:
            logger.error(f"Error retrieving from SessionMemory: {e}")
        
        # Try to retrieve from VectorStore (if available)
        try:
            from memory.vector_store import vector_store
            
            if state.intent:
                similar_docs = vector_store.search(
                    query=state.intent.name,
                    limit=5
                )
                retrieved_documents.extend(similar_docs)
                provenance["vector_store"] = len(similar_docs)
                
                logger.info(f"Retrieved {len(similar_docs)} documents from VectorStore")
        except ImportError:
            logger.warning("Could not import VectorStore")
        except Exception as e:
            logger.error(f"Error retrieving from VectorStore: {e}")
        
        # Try to retrieve learned patterns from Retriever (if available)
        try:
            from learning.retriever import retriever
            
            if state.intent:
                patterns = retriever.retrieve_patterns(
                    intent=state.intent.name,
                    context=state.context if isinstance(state.context, dict) else None
                )
                learned_patterns.extend(patterns)
                provenance["retriever"] = len(patterns)
                
                logger.info(f"Retrieved {len(patterns)} patterns from Retriever")
        except ImportError:
            logger.warning("Could not import Retriever")
        except Exception as e:
            logger.error(f"Error retrieving from Retriever: {e}")
        
    except Exception as e:
        logger.error(f"Error in knowledge retrieval: {e}")
    
    # Create knowledge context
    knowledge_context = KnowledgeContext(
        retrieved_memories=retrieved_memories,
        retrieved_documents=retrieved_documents,
        learned_patterns=learned_patterns,
        provenance=provenance if provenance else {"note": "No knowledge services available"}
    )
    
    state.knowledge = knowledge_context
    
    # Phase 9: Collect trace event for retrieved knowledge
    trace_collector = get_trace_collector()
    trace_collector.collect_retrieved_knowledge(
        task_id=state.task.task_id,
        knowledge_data={
            "num_memories": len(knowledge_context.retrieved_memories),
            "num_documents": len(knowledge_context.retrieved_documents),
            "num_patterns": len(knowledge_context.learned_patterns),
            "provenance": knowledge_context.provenance
        }
    )
    
    state.add_history_event("retrieve_knowledge_completed", {
        "task_id": state.task.task_id,
        "num_memories": len(knowledge_context.retrieved_memories),
        "num_documents": len(knowledge_context.retrieved_documents),
        "num_patterns": len(knowledge_context.learned_patterns)
    })
    
    state.update_timestamp()
    
    return {"state": state}
