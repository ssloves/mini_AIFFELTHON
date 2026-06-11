"""Step 3 공용 — 데이터 로딩 + Neo4j 접속 설정.

Neo4j 접속정보는 .env에서 읽는다(없으면 로컬 기본값):
  NEO4J_URI=bolt://localhost:7687
  NEO4J_USER=neo4j
  NEO4J_PASSWORD=...
"""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
EMB_DIR = DATA / "embeddings"

EMBED_MODEL = "text-embedding-3-small"
EMBED_DIM = 1536


def load_jsonl(path: Path):
    with path.open(encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def load_chunks():
    return load_jsonl(DATA / "chunks" / "all_chunks.jsonl")


def load_conflicts():
    rows = load_jsonl(DATA / "conflicts_confirmed.jsonl")
    return [r for r in rows if r.get("is_conflict") is True]


def load_conditions():
    return load_jsonl(DATA / "conditions.jsonl")


def load_concepts():
    return json.loads((DATA / "concepts.json").read_text(encoding="utf-8"))


def embed_text(chunk: dict) -> str:
    """임베딩 입력 = 상위 조 제목(컨텍스트) + 본문."""
    ctx = chunk.get("context_header") or chunk.get("hierarchy_path") or ""
    return (ctx + "\n" + chunk["text"]).strip()


def get_openai():
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY 미설정(.env 저장 필요)")
    from openai import OpenAI
    return OpenAI(api_key=key)


def neo4j_config():
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    return {
        "uri": os.getenv("NEO4J_URI", "bolt://localhost:7687"),
        "user": os.getenv("NEO4J_USER", "neo4j"),
        "password": os.getenv("NEO4J_PASSWORD", "neo4jpassword"),
    }


def get_driver():
    from neo4j import GraphDatabase
    cfg = neo4j_config()
    return GraphDatabase.driver(cfg["uri"], auth=(cfg["user"], cfg["password"]))
