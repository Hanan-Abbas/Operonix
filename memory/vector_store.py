import logging
import os
from core.config import settings
from core.event_bus import bus

# We import ChromaDB for local vector storage
import chromadb
from chromadb.utils import embedding_functions


class VectorStore:
    """🧠 Semantic memory store for the AI OS.

    Allows the agent to retrieve past experiences based on similarity of meaning
    rather than exact keyword matches.
    """

    def __init__(self):
        self.logger = logging.getLogger("VectorStore")

        # We store the database files inside your memory/stores directory
        self.storage_dir = os.path.join("memory", "stores", "chroma_db")
        self.client = None
        self.collection = None
        self.intent_collection = None  # 🔄 NEW: For normalizing raw user inputs!

    async def start(self):
        """Initializes the database and subscribes to archiving events."""
        os.makedirs(self.storage_dir, exist_ok=True)

        try:
            # 1. Initialize a persistent local Chroma client
            self.client = chromadb.PersistentClient(path=self.storage_dir)

            # 2. Use Chroma's default lightweight embedding model (runs locally!)
            emb_fn = embedding_functions.DefaultEmbeddingFunction()

            # 3. Create or load the collection where experiences live
            self.collection = self.client.get_or_create_collection(
                name="agent_experiences", embedding_function=emb_fn
            )

            # 4. 🔄 NEW: Create or load the collection specifically for intents
            self.intent_collection = self.client.get_or_create_collection(
                name="system_intents", embedding_function=emb_fn
            )

            # 5. Listen to the same event as the LongTermMemory!
            bus.subscribe("task_memory_archived", self.save_vector_experience)

            self.logger.info(
                "📐 Vector Store: Online. Local semantic database ready."
            )

        except Exception as e:
            self.logger.error(
                f"Failed to initialize Vector Store: {e}", exc_info=True
            )

    async def save_vector_experience(self, event):
        """Converts an archived task into a vector and stores it in the

        database.
        """
        task_data = event.data
        task_id = task_data.get("task_id")
        intent = task_data.get("intent")

        if task_data.get("status") != "completed":
            return

        steps = task_data.get("steps", [])
        steps_summary = ", ".join(
            [step.get("action", "") for step in steps if "action" in step]
        )

        content_to_vectorize = (
            f"Intent: {intent}. Actions performed: {steps_summary}."
        )

        try:
            self.collection.add(
                documents=[content_to_vectorize],
                metadatas=[{"task_id": task_id, "intent": intent}],
                ids=[task_id],
            )
            self.logger.debug(
                f"Saved vector embedding for task [{task_id}] -> '{intent}'"
            )

        except Exception as e:
            self.logger.error(f"Failed to save vector for task {task_id}: {e}")

    def query_similar_experiences(self, current_intent: str, limit: int = 3):
        """Searches the database for the closest semantic matches to a new

        intent.
        """
        if not self.collection:
            self.logger.warning("Query attempted before Vector Store started.")
            return []

        try:
            results = self.collection.query(
                query_texts=[current_intent], n_results=limit
            )

            matched_ids = []
            if results and "metadatas" in results and results["metadatas"]:
                for metadata_list in results["metadatas"]:
                    for item in metadata_list:
                        if item and "task_id" in item:
                            matched_ids.append(item["task_id"])

            return matched_ids

        except Exception as e:
            self.logger.error(f"Error querying vector store: {e}")
            return []

    # -----------------------------------------------------------------
    # 🔄 NEW METHOD 1: Teach the DB what your official capabilities are
    # -----------------------------------------------------------------
    async def add_intents(self, intents: list):
        """Populates the intent collection with official system capabilities."""
        if not self.intent_collection:
            return

        try:
            for intent in intents:
                # We use the intent string as both the text and the ID to prevent duplicates
                self.intent_collection.upsert(
                    documents=[intent.replace("_", " ")],  # e.g., "file create" instead of "file_create"
                    metadatas=[{"official_intent": intent}],
                    ids=[intent],
                )
            self.logger.debug(
                f"Successfully indexed {len(intents)} official intents."
            )
        except Exception as e:
            self.logger.error(f"Failed to add intents to vector store: {e}")

    # -----------------------------------------------------------------
    # 🔄 NEW METHOD 2: Find the closest matching official intent
    # -----------------------------------------------------------------
    async def search_closest_intent(
        self, raw_intent: str
    ) -> (str or None, float):
        """Searches for the closest official intent to a raw string."""
        if not self.intent_collection:
            return None, 0.0

        try:
            results = self.intent_collection.query(
                query_texts=[raw_intent], n_results=1
            )

            if results and results["metadatas"] and results["metadatas"][0]:
                best_match = results["metadatas"][0][0]["official_intent"]
                # Chroma returns squared L2 distances. A distance of 0.0 means an exact match.
                # Smaller distances = closer matches. 
                distance = results["distances"][0][0]

                # Convert L2 distance to a rough confidence score (1.0 = perfect match)
                confidence = max(0.0, 1.0 - (distance / 2.0))

                return best_match, confidence

        except Exception as e:
            self.logger.error(f"Error searching closest intent: {e}")

        return None, 0.0


# Global instance
vector_store = VectorStore()