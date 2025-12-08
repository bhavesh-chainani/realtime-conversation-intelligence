"""
Pinecone Vector Database Setup for Legal RAG System
Handles Pinecone index creation, configuration, and document storage
"""

import os
import time
import logging
from typing import List, Dict, Optional, Any
from dotenv import load_dotenv, find_dotenv
import json

# --- Pinecone client (SDK v3 / pinecone-client>=5) ---
from pinecone import Pinecone as PineconeClient, ServerlessSpec

# --- LangChain integrations ---
from langchain_openai import OpenAIEmbeddings
from langchain_core.documents import Document
from langchain_pinecone import Pinecone as PineconeVectorStore  # alias to avoid name clash

from pdf_processor import LegalPDFProcessor

# -----------------------------------------------------------------------------
# Env loading helpers
# -----------------------------------------------------------------------------
def _load_and_validate_env() -> Dict[str, str]:
    load_dotenv(find_dotenv(usecwd=True), override=True)

    def _need(var: str, default: Optional[str] = None) -> str:
        v = os.getenv(var, default if default is not None else "").strip()
        if not v or v.lower().startswith("your_"):
            raise RuntimeError(f"{var} is missing or still a placeholder in your .env")
        return v

    cfg = {
        "OPENAI_API_KEY": _need("OPENAI_API_KEY"),
        "PINECONE_API_KEY": _need("PINECONE_API_KEY"),
        "PINECONE_REGION": os.getenv("PINECONE_REGION", os.getenv("PINECONE_ENVIRONMENT", "us-east-1")).strip(),
        "PINECONE_INDEX_NAME": _need("PINECONE_INDEX_NAME", "legal-documents"),
        "PINECONE_CLOUD": os.getenv("PINECONE_CLOUD", "aws").strip() or "aws",
    }
    return cfg


# -----------------------------------------------------------------------------
# Metadata sanitizer (critical for Pinecone compatibility)
# -----------------------------------------------------------------------------
def _sanitize_metadata(meta: Dict[str, Any]) -> Dict[str, Any]:
    """
    Pinecone metadata must be: str | int | float | bool | List[str]
    - Dicts are converted to list of "k:v" strings
    - Lists are converted to List[str]
    - Other types are stringified
    """
    def _s(v: Any) -> Any:
        if isinstance(v, (str, int, float, bool)):
            return v
        if isinstance(v, list):
            out = []
            for x in v:
                if isinstance(x, (str, int, float, bool)):
                    out.append(str(x))
                elif isinstance(x, dict):
                    out.extend([f"{kk}:{vv}" for kk, vv in x.items()])
                else:
                    out.append(str(x))
            return out[:100]
        if isinstance(v, dict):
            items = [f"{k}:{v2}" for k, v2 in v.items()]
            return items[:100]
        return str(v)

    sanitized: Dict[str, Any] = {}
    for k, v in (meta or {}).items():
        key = str(k)[:40]
        val = _s(v)
        if isinstance(val, str) and len(val) > 1000:
            val = val[:1000]
        if isinstance(val, list):
            val = [x[:300] if isinstance(x, str) else x for x in val][:100]
        sanitized[key] = val
    return sanitized


# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class PineconeManager:
    """
    Manages Pinecone vector database operations for legal documents
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        index_name: Optional[str] = None,
        cloud: Optional[str] = None,
        region: Optional[str] = None,
        embedding_model: str = "text-embedding-3-small",  # 1536 dims
    ):
        cfg = _load_and_validate_env()

        self.api_key = (api_key or cfg["PINECONE_API_KEY"]).strip()
        self.index_name = (index_name or cfg["PINECONE_INDEX_NAME"]).strip()
        self.cloud = (cloud or cfg["PINECONE_CLOUD"]).strip()
        self.region = (region or cfg["PINECONE_REGION"]).strip()

        os.environ["PINECONE_API_KEY"] = self.api_key

        self.pc = PineconeClient(api_key=self.api_key)
        self.index = None

        openai_key = cfg["OPENAI_API_KEY"]
        self.embedding_model = embedding_model
        self.embedding_dim = 1536 if embedding_model == "text-embedding-3-small" else 3072
        self.embeddings = OpenAIEmbeddings(model=self.embedding_model, api_key=openai_key)

        logger.info(
            f"PineconeManager initialized | index='{self.index_name}' region='{self.region}' "
            f"emb_model='{self.embedding_model}' dim={self.embedding_dim}"
        )

    # ---------- helpers ----------
    def _index_exists(self) -> bool:
        try:
            try:
                names = self.pc.list_indexes().names()
            except Exception:
                res = self.pc.list_indexes()
                if isinstance(res, list):
                    names = [i if isinstance(i, str) else getattr(i, "name", None) for i in res]
                else:
                    names = []
            return self.index_name in set(filter(None, names))
        except Exception as e:
            logger.warning(f"Could not list indexes: {e}")
            return False

    def _wait_until_ready(self, timeout_sec: int = 180) -> None:
        start = time.time()
        while True:
            desc = self.pc.describe_index(self.index_name)
            ready = False
            try:
                ready = bool(desc.status.get("ready"))
            except Exception:
                try:
                    _ = self.pc.Index(self.index_name)
                    ready = True
                except Exception:
                    ready = False
            if ready:
                return
            if time.time() - start > timeout_sec:
                raise TimeoutError(f"Index '{self.index_name}' not ready after {timeout_sec}s.")
            time.sleep(1)

    # ---------- public API ----------
    def create_index(self, dimension: Optional[int] = None, metric: str = "cosine") -> bool:
        try:
            dim = dimension or self.embedding_dim
            if self._index_exists():
                logger.info(f"Index '{self.index_name}' already exists")
                self.index = self.pc.Index(self.index_name)
                return True

            logger.info(f"Creating new Pinecone index: {self.index_name} (dim={dim}, metric={metric})")
            self.pc.create_index(
                name=self.index_name,
                dimension=dim,
                metric=metric,
                spec=ServerlessSpec(cloud=self.cloud, region=self.region),
            )

            logger.info("Waiting for index to be ready...")
            self._wait_until_ready()
            self.index = self.pc.Index(self.index_name)
            logger.info(f"Index '{self.index_name}' created successfully")
            return True

        except Exception as e:
            logger.error(f"Error creating Pinecone index: {e}")
            return False

    def delete_index(self) -> bool:
        try:
            if self._index_exists():
                self.pc.delete_index(self.index_name)
                logger.info(f"Index '{self.index_name}' deleted successfully")
            else:
                logger.info(f"Index '{self.index_name}' does not exist")
            self.index = None
            return True
        except Exception as e:
            logger.error(f"Error deleting Pinecone index: {e}")
            return False

    def get_index_stats(self) -> Dict:
        try:
            if not self.index:
                self.index = self.pc.Index(self.index_name)
            stats = self.index.describe_index_stats()
            return {
                "total_vector_count": stats.get("total_vector_count"),
                "namespaces": list((stats.get("namespaces") or {}).keys()),
            }
        except Exception as e:
            logger.error(f"Error getting index stats: {e}")
            return {}

    def store_documents(self, documents: List[Document], namespace: str = "legal_docs") -> bool:
        try:
            if not documents:
                logger.warning("No documents provided to store.")
                return False

            if not self.index:
                self.index = self.pc.Index(self.index_name)

            # Sanitize metadata
            clean_docs: List[Document] = []
            for d in documents:
                meta = d.metadata if isinstance(d.metadata, dict) else {}
                safe_meta = _sanitize_metadata(meta)
                clean_docs.append(Document(page_content=d.page_content, metadata=safe_meta))

            logger.info(f"Storing {len(clean_docs)} documents in namespace '{namespace}'")

            _ = PineconeVectorStore.from_documents(
                documents=clean_docs,
                embedding=self.embeddings,
                index_name=self.index_name,
                namespace=namespace,
            )

            logger.info("Documents stored successfully in Pinecone")
            return True

        except Exception as e:
            logger.error(f"Error storing documents in Pinecone: {e}")
            return False

    def search_similar(
        self,
        query: str,
        k: int = 5,
        namespace: str = "legal_docs",
        filter_dict: Optional[Dict] = None,
    ) -> List[Dict]:
        try:
            if not query:
                logger.warning("Empty query for similarity search.")
                return []
            if not self.index:
                self.index = self.pc.Index(self.index_name)

            vectorstore = PineconeVectorStore(
                index=self.index,
                embedding=self.embeddings,
                text_key="text",
                namespace=namespace,
            )

            results = vectorstore.similarity_search_with_score(query=query, k=k, filter=filter_dict)

            return [
                {"content": doc.page_content, "metadata": doc.metadata, "similarity_score": score}
                for doc, score in results
            ]

        except Exception as e:
            logger.error(f"Error searching Pinecone: {e}")
            return []

    def setup_legal_database(self, pdf_path: str) -> Dict:
        logger.info("Starting legal database setup")
        setup_results = {
            "index_created": False,
            "documents_processed": 0,
            "documents_stored": False,
            "index_stats": {},
            "processing_stats": {},
        }

        try:
            setup_results["index_created"] = self.create_index()
            if not setup_results["index_created"]:
                logger.error("Failed to create Pinecone index")
                return setup_results

            processor = LegalPDFProcessor(chunk_size=1000, chunk_overlap=200)
            documents, processing_stats = processor.process_pdf(pdf_path)
            setup_results["documents_processed"] = len(documents)
            setup_results["processing_stats"] = processing_stats

            if not documents:
                logger.error("No documents were processed from PDF")
                return setup_results

            setup_results["documents_stored"] = self.store_documents(documents)
            if not setup_results["documents_stored"]:
                logger.error("Failed to store documents in Pinecone")
                return setup_results

            setup_results["index_stats"] = self.get_index_stats()
            logger.info("Legal database setup completed successfully")

        except Exception as e:
            logger.error(f"Error during database setup: {e}")
            setup_results["error"] = str(e)

        return setup_results


def main():
    try:
        pinecone_manager = PineconeManager()
        pdf_path = "data/general_legal_toolkit_handbook_for_vulnerable_witnesses.pdf"

        if os.path.exists(pdf_path):
            results = pinecone_manager.setup_legal_database(pdf_path)
            print("Database Setup Results:")
            print(f"Index Created: {results.get('index_created')}")
            print(f"Documents Processed: {results.get('documents_processed')}")
            print(f"Documents Stored: {results.get('documents_stored')}")
            print(f"Index Stats: {results.get('index_stats')}")
            print(f"Processing Stats: {results.get('processing_stats')}")
            if "error" in results:
                print(f"Error: {results['error']}")
        else:
            print(f"PDF file not found: {pdf_path}")

    except Exception as e:
        print(f"Error: {e}")
        print("Make sure to set environment variables:")
        print("- PINECONE_API_KEY")
        print("- OPENAI_API_KEY")
        print("- PINECONE_INDEX_NAME (default: legal-documents)")
        print("- PINECONE_REGION / PINECONE_ENVIRONMENT (e.g., us-east-1)")
        print("- PINECONE_CLOUD (default: aws)")


if __name__ == "__main__":
    main()
