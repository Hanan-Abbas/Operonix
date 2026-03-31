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

    async def start(self):
        """Initializes the database and subscribes to archiving events."""
        os.makedirs(self.storage_dir, exist_ok=True)

        try:
            # 1. Initialize a persistent local Chroma client
            self.client = chromadb.PersistentClient(path=self.storage_dir)

            # 2. Use Chroma's default lightweight embedding model (runs locally!)
            # Note: The first time this runs, it may download a small model file (~100MB)
            emb_fn = embedding_functions.DefaultEmbeddingFunction()

            # 3. Create or load the collection where experiences live
            self.collection = self.client.get_or_create_collection(
                name="agent_experiences", embedding_function=emb_fn
            )

            # 4. Listen to the same event as the LongTermMemory!
            bus.subscribe("task_memory_archived", self.save_vector_experience)

            self.logger.info(
                "📐 Vector Store: Online. Local semantic database ready."
            )

        except Exception as e:
            self.logger.error(
                f"Failed to initialize Vector Store: {e}", exc_info=True
            )

    