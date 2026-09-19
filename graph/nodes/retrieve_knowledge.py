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
    citations = []
    
    try:
        # Enhanced RAG: Query expansion for better retrieval
        expanded_queries = _expand_query(state.task.user_input, state.intent)
        
        # Try to retrieve from LongTermMemory with expanded queries
        try:
            from memory.long_term_memory import long_term_memory
            
            if state.intent:
                # Use expanded queries for better retrieval
                all_tasks = []
                for query in expanded_queries:
                    past_tasks = long_term_memory.search_past_tasks(
                        intent=query,
                        limit=3
                    )
                    all_tasks.extend(past_tasks)
                
                # Deduplicate and re-rank results
                unique_tasks = _deduplicate_results(all_tasks)
                ranked_tasks = _rerank_results(unique_tasks, state.task.user_input)
                retrieved_memories.extend(ranked_tasks)
                provenance["long_term_memory"] = len(ranked_tasks)
                
                logger.info(f"Retrieved {len(ranked_tasks)} memories from LongTermMemory (with query expansion)")
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
        
        # Try to retrieve from VectorStore with hybrid search (if available)
        try:
            from memory.vector_store import vector_store
            
            if state.intent:
                # Try hybrid search (semantic + keyword if available)
                similar_docs = []
                for query in expanded_queries:
                    docs = vector_store.search(
                        query=query,
                        limit=3
                    )
                    similar_docs.extend(docs)
                
                # Deduplicate and add citations
                unique_docs = _deduplicate_results(similar_docs)
                for doc in unique_docs:
                    if hasattr(doc, 'id') or hasattr(doc, 'source'):
                        citations.append({
                            "source": getattr(doc, 'source', 'unknown'),
                            "id": getattr(doc, 'id', str(hash(str(doc)))),
                            "relevance": getattr(doc, 'score', 0.0)
                        })
                
                retrieved_documents.extend(unique_docs)
                provenance["vector_store"] = len(unique_docs)
                
                logger.info(f"Retrieved {len(unique_docs)} documents from VectorStore (with hybrid search)")
        except ImportError:
            logger.warning("Could not import VectorStore")
        except Exception as e:
            logger.error(f"Error retrieving from VectorStore: {e}")
        
        # Try to retrieve learned patterns from Retriever with context
        try:
            from learning.retriever import retriever
            
            if state.intent:
                patterns = retriever.retrieve_patterns(
                    intent=state.intent.name,
                    context=state.context if isinstance(state.context, dict) else None,
                    query=state.task.user_input
                )
                learned_patterns.extend(patterns)
                provenance["retriever"] = len(patterns)
                
                logger.info(f"Retrieved {len(patterns)} patterns from Retriever (with context)")
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
        provenance=provenance if provenance else {"note": "No knowledge services available"},
        citations=citations if citations else []
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
    
    return {"knowledge": state.knowledge}


def _expand_query(user_input: str, intent) -> list[str]:
    """Expand the query for better RAG retrieval.
    
    This function generates multiple query variations to improve retrieval:
    - Original user input
    - Intent name
    - Synonyms/related terms (simplified)
    - Query decomposition (simplified)
    
    Args:
        user_input: Original user input
        intent: Intent object
        
    Returns:
        List of expanded queries
    """
    queries = [user_input]
    
    # Add intent name as a query
    if intent:
        queries.append(intent.name)
    
    # Add simplified variations (basic keyword extraction)
    words = user_input.split()
    if len(words) > 2:
        # Add bigrams
        for i in range(len(words) - 1):
            queries.append(f"{words[i]} {words[i+1]}")
    
    # Remove duplicates while preserving order
    seen = set()
    unique_queries = []
    for q in queries:
        if q.lower() not in seen:
            seen.add(q.lower())
            unique_queries.append(q)
    
    return unique_queries


def _deduplicate_results(results: list) -> list:
    """Deduplicate results based on content or ID.
    
    Args:
        results: List of results to deduplicate
        
    Returns:
        Deduplicated list of results
    """
    if not results:
        return []
    
    seen = set()
    unique_results = []
    
    for result in results:
        # Use string representation as a simple deduplication key
        # In a real implementation, this would use proper ID fields
        result_key = str(result)
        if result_key not in seen:
            seen.add(result_key)
            unique_results.append(result)
    
    return unique_results


def _rerank_results(results: list, query: str) -> list:
    """Re-rank results based on relevance to query.
    
    This is a simplified re-ranking implementation.
    In a real implementation, this would use more sophisticated
    ranking algorithms (e.g., cross-encoder, learning-to-rank).
    
    Args:
        results: List of results to re-rank
        query: Original query string
        
    Returns:
        Re-ranked list of results
    """
    if not results:
        return []
    
    # Simple relevance scoring based on keyword overlap
    query_words = set(query.lower().split())
    
    def relevance_score(result):
        """Calculate simple relevance score for a result."""
        result_str = str(result).lower()
        result_words = set(result_str.split())
        
        # Calculate overlap
        overlap = len(query_words & result_words)
        
        # Normalize by result length to prefer concise matches
        length_penalty = len(result_str) / 1000.0
        
        return overlap - length_penalty
    
    # Sort by relevance score (descending)
    ranked = sorted(results, key=relevance_score, reverse=True)
    
    return ranked
